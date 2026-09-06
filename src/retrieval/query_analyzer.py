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
    sub_questions: List[str] = Field(default_factory=list)
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


def decompose_query(
    query: str,
    llm: Optional[LLMProvider] = None,
    use_llm: bool = False,
) -> List[str]:
    """
    Decompose a complex multi-hop question into simpler sub-questions.
    Uses pattern-based heuristic decomposition as the deterministic offline base,
    falling back to or augmented by LLM if enabled.
    """
    clean_q = query.strip()
    if not clean_q:
        return []

    # Heuristic pattern matching for common multi-hop query structures
    # 1. "Which accord/war was won by the faction of which X is a member?"
    m1 = re.search(
        r"which\s+(accord|war|battle|event|conflict)\s+(?:was\s+ultimately\s+won|was\s+won)\s+by\s+the\s+faction\s+(?:of\s+which\s+)?(.*?)\s+is\s+a\s+member",
        clean_q, re.IGNORECASE
    )
    if m1:
        event_kind, person = m1.group(1).strip(), m1.group(2).strip()
        return [
            f"Which faction is {person} a member of?",
            f"Which {event_kind} was won by that faction?",
        ]

    # 2. "Which individual was a member of the faction that ultimately won the X?"
    m2 = re.search(
        r"which\s+individual\s+was\s+a\s+member\s+of\s+the\s+faction\s+that\s+(?:ultimately\s+)?won\s+(?:the\s+)?(.*?)\??$",
        clean_q, re.IGNORECASE
    )
    if m2:
        war_name = m2.group(1).strip()
        return [
            f"Which faction won {war_name}?",
            "Which individuals were members of that victor faction?",
        ]

    # 3. "Which war did X's own faction ultimately win?"
    m3 = re.search(
        r"which\s+war\s+did\s+(.*?)(?:'s|\s+own)\s+faction\s+(?:ultimately\s+)?win\??$",
        clean_q, re.IGNORECASE
    )
    if m3:
        person = m3.group(1).strip()
        return [
            f"Which faction does {person} belong to?",
            "Which war did that faction win?",
        ]

    # 4. "Whose dominion encompasses the lair of the X?"
    m4 = re.search(
        r"whose\s+dominion\s+encompasses\s+the\s+lair\s+of\s+(?:the\s+)?(.*?)\??$",
        clean_q, re.IGNORECASE
    )
    if m4:
        creature = m4.group(1).strip()
        return [
            f"Where is the lair of {creature}?",
            "Whose dominion encompasses that location?",
        ]

    # 5. "Which war was won by the organization that included X as one of its members?"
    m5 = re.search(
        r"which\s+war\s+was\s+won\s+by\s+the\s+(?:organization|faction)\s+that\s+included\s+(.*?)\s+as\s+one\s+of\s+its\s+members\??$",
        clean_q, re.IGNORECASE
    )
    if m5:
        person = m5.group(1).strip()
        return [
            f"Which organization or faction included {person} as a member?",
            "Which war was won by that organization?",
        ]

    # 6. "In what way is X connected to the victors of the Y?"
    m6 = re.search(
        r"in\s+what\s+way\s+is\s+(.*?)\s+connected\s+to\s+the\s+victors\s+of\s+(?:the\s+)?(.*?)\??$",
        clean_q, re.IGNORECASE
    )
    if m6:
        person, event = m6.group(1).strip(), m6.group(2).strip()
        return [
            f"Who were the victors of {event}?",
            f"How is {person} affiliated with or connected to that victor faction?",
        ]

    # 7. "To which shadowed redoubt must one journey to examine the relic long borne by X since Y?"
    m7 = re.search(
        r"to\s+which\s+.*?\s+must\s+one\s+journey\s+to\s+examine\s+the\s+relic\s+long\s+borne\s+by\s+(.*?)(?:\s+since|\?|$)",
        clean_q, re.IGNORECASE
    )
    if m7:
        person = m7.group(1).strip()
        return [
            f"What relic was borne or wielded by {person}?",
            "Where is that relic housed, kept, or located?",
        ]

    # Optional LLM-assisted decomposition
    if use_llm and llm is not None:
        try:
            prompt = f"""Decompose the following question about a fictional universe into simpler sub-questions that can be answered sequentially.
If the question is simple, return the question unchanged. Maximum 5 sub-questions.

Question: {clean_q}

Respond in JSON:
{{"sub_questions": ["sub-question 1", "sub-question 2"]}}"""
            res = llm.generate(prompt=prompt, system_prompt="You are a query decomposition assistant.")
            raw_text = res.content.strip()
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```[a-zA-Z]*\n?", "", raw_text)
                raw_text = re.sub(r"\n?```$", "", raw_text)
            data = json.loads(raw_text)
            if data.get("sub_questions"):
                return data["sub_questions"][:5]
        except Exception as e:
            logger.debug("LLM decomposition fallback: %s", e)

    return [clean_q]


def analyze_query(
    query: str,
    llm: Optional[LLMProvider] = None,
    use_llm: bool = False,
) -> QueryAnalysis:
    """
    Analyze user question to extract entities, classify type, produce sub-questions,
    and generate optimized queries.
    
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
    sub_qs = decompose_query(clean_q, llm=llm, use_llm=use_llm) if q_type == "multi_hop" else []

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
        sub_questions=sub_qs,
        bm25_query=bm25_q,
    )
