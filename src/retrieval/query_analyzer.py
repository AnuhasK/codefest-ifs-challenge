import re
import json
import logging
from typing import List, Optional
from pydantic import BaseModel, Field

from src.providers.llm_provider import LLMProvider

logger = logging.getLogger(__name__)

STOP_WORDS = {
    "what", "is", "the", "in", "of", "to", "a", "an", "and", "or", "for", "on", "at",
    "by", "with", "from", "as", "about", "into", "through", "during", "before", "after",
    "which", "who", "whom", "whose", "where", "when", "why", "how", "did", "does", "do",
    "according", "per", "official", "recorded", "known", "called", "named"
}

MULTI_HOP_PATTERNS = [
    re.compile(r"\bwhich\s+(?:war|accord|event|faction|citadel|keep|treaty)\b", re.IGNORECASE),
    re.compile(r"\bwon\s+by\s+the\s+faction\b", re.IGNORECASE),
    re.compile(r"\bof\s+which\s+.*?\s+is\s+a\s+member\b", re.IGNORECASE),
    re.compile(r"\bwhose\s+dominion\b", re.IGNORECASE),
    re.compile(r"\bconnected\s+to\b", re.IGNORECASE),
    re.compile(r"\bparticipated\s+in\b", re.IGNORECASE),
    re.compile(r"\bmember\s+of\b", re.IGNORECASE),
    re.compile(r"\blair\s+of\b", re.IGNORECASE),
]

COMPARISON_PATTERNS = [
    re.compile(r"\bcompare\b", re.IGNORECASE),
    re.compile(r"\bdifference\s+between\b", re.IGNORECASE),
    re.compile(r"\bversus\b", re.IGNORECASE),
    re.compile(r"\bvs\.?\b", re.IGNORECASE),
]


class QueryAnalysis(BaseModel):
    """Structured analysis of a user question for hybrid retrieval."""
    original_query: str
    query_type: str = Field(
        ..., description="'simple', 'multi_entity', 'multi_hop', or 'comparison'"
    )
    entities_mentioned: List[str] = Field(default_factory=list)
    expanded_queries: List[str] = Field(default_factory=list)
    bm25_query: str = Field(..., description="Optimized lexical search query string")


def extract_entities_heuristic(query: str) -> List[str]:
    """Extract candidate entity names using capitalized phrase and title heuristics."""
    entities: List[str] = []
    seen = set()

    # Match quoted strings
    for m in re.finditer(r"['\"]([^'\"]+)['\"]", query):
        val = m.group(1).strip()
        if val and len(val) > 2 and val.lower() not in seen:
            seen.add(val.lower())
            entities.append(val)

    # Match formal titles / organizations ("House Morvain", "Ashen Vanguard", "Ser Vael", etc.)
    title_pattern = re.compile(
        r"\b(?:House|Ser|Lord|Lady|High|Warden|Archon|The)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b"
    )
    for m in title_pattern.finditer(query):
        val = m.group(0).strip()
        if val.lower() not in seen and len(val.split()) > 1:
            seen.add(val.lower())
            entities.append(val)

    # Match general multi-word capitalized sequences (e.g. "Weeping Lurker", "Greyfell Citadel", "Isolde Mournvale")
    cap_pattern = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")
    for m in cap_pattern.finditer(query):
        val = m.group(0).strip()
        # Exclude leading question words like "What Is"
        if any(val.lower().startswith(q) for q in ("what is", "according to", "which war", "in the")):
            continue
        if val.lower() not in seen and len(val) > 3:
            seen.add(val.lower())
            entities.append(val)

    return entities


def build_bm25_query(query: str, entities: List[str]) -> str:
    """
    Construct an optimized lexical query by prioritizing entities and core nouns,
    stripping interrogative stop words.
    """
    if entities:
        # If specific entities were found, start with them
        entity_str = " ".join(entities)
        # Add remaining significant content words from query
        tokens = re.findall(r"\b[A-Za-z0-9\-_]+\b", query)
        extra_keywords = [
            t for t in tokens 
            if t.lower() not in STOP_WORDS 
            and t.lower() not in entity_str.lower()
            and len(t) > 2
        ]
        return f"{entity_str} {' '.join(extra_keywords)}".strip()

    # Fallback: extract all non-stopword tokens
    tokens = re.findall(r"\b[A-Za-z0-9\-_]+\b", query)
    filtered = [t for t in tokens if t.lower() not in STOP_WORDS and len(t) > 2]
    return " ".join(filtered) if filtered else query


def classify_query_type(query: str, entities: List[str]) -> str:
    """Classify the question type based on syntactic structure and entity count."""
    for p in COMPARISON_PATTERNS:
        if p.search(query):
            return "comparison"

    for p in MULTI_HOP_PATTERNS:
        if p.search(query):
            return "multi_hop"

    if len(entities) >= 2:
        return "multi_entity"

    return "simple"


def analyze_query(
    query: str,
    llm: Optional[LLMProvider] = None,
    use_llm: bool = False,
) -> QueryAnalysis:
    """
    Analyze user question to extract entities, classify type, and produce optimized queries.
    
    Args:
        query: Raw user query
        llm: Optional LLM provider for richer analysis
        use_llm: Whether to invoke the LLM for expanded queries (default False for zero cost)
    """
    clean_q = query.strip()
    entities = extract_entities_heuristic(clean_q)
    q_type = classify_query_type(clean_q, entities)
    bm25_q = build_bm25_query(clean_q, entities)
    expanded = [clean_q]

    if use_llm and llm is not None:
        try:
            prompt = f"""Analyze this query about a fictional universe:
"{clean_q}"

Return JSON:
{{
  "query_type": "simple" | "multi_entity" | "multi_hop" | "comparison",
  "entities": ["..."],
  "alternative_phrasings": ["..."]
}}"""
            res = llm.generate(prompt=prompt, system_prompt="You are a query analyzer.")
            raw_text = res.content.strip()
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```[a-zA-Z]*\n?", "", raw_text)
                raw_text = re.sub(r"\n?```$", "", raw_text)
            data = json.loads(raw_text)
            if data.get("query_type"):
                q_type = data["query_type"]
            if data.get("entities"):
                # Combine heuristic & LLM entities
                for e in data["entities"]:
                    if e and e not in entities:
                        entities.append(e)
            if data.get("alternative_phrasings"):
                expanded.extend(data["alternative_phrasings"][:2])
        except Exception as e:
            logger.debug(f"LLM query analysis fallback to heuristic: {e}")

    return QueryAnalysis(
        original_query=clean_q,
        query_type=q_type,
        entities_mentioned=entities,
        expanded_queries=expanded,
        bm25_query=bm25_q,
    )
