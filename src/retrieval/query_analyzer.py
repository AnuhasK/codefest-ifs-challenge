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
    re.compile(r"\bwhich\s+[a-z0-9_\-]+\s+.*?\b(?:that|which|who|of\s+which|by\s+the)\b", re.IGNORECASE),
    re.compile(r"\b(?:won\s+by|ruled\s+by|held\s+by|controlled\s+by|allied\s+with|affiliated\s+with|member\s+of|lair\s+of|seat\s+of|stronghold\s+of|dominion|journey\s+to\s+examine)\b", re.IGNORECASE),
    re.compile(r"\bwhose\s+[a-z0-9_\-]+\b", re.IGNORECASE),
    re.compile(r"\b(?:connected\s+to|related\s+to|participated\s+in)\b", re.IGNORECASE),
    re.compile(r"\b(?:war|accord|event|faction|citadel|keep|treaty|dominion|lair)\b", re.IGNORECASE),
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

    # Match capitalized phrases connected by grammatical particles (e.g. "the", "of", "in", "and", "de", "da", "for", "von")
    # Examples: "Cerys Sablewood the Ashen", "Knight of the Hollow", "Order of the Eclipse"
    epithet_pattern = re.compile(
        r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:\s+(?:the|of|in|and|de|da|for|von)\s+[A-Z][a-z]+)+\b"
    )
    for m in epithet_pattern.finditer(query):
        val = m.group(0).strip()
        if any(val.lower().startswith(q) for q in ("what is", "according to", "which war", "in the", "to which", "where was", "who was")):
            continue
        if val.lower() not in seen and len(val) > 3:
            seen.add(val.lower())
            entities.append(val)

    # Match general multi-word capitalized sequences (e.g. "Weeping Lurker", "Greyfell Citadel", "Isolde Mournvale")
    cap_pattern = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")
    for m in cap_pattern.finditer(query):
        val = m.group(0).strip()
        # Exclude leading question words like "What Is", "To Which"
        if any(val.lower().startswith(q) for q in ("what is", "according to", "which war", "in the", "to which", "where was", "who was", "how many", "at what")):
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
    Uses generalized syntactic clause decomposition as the deterministic offline base,
    falling back to or augmented by LLM if enabled.
    """
    clean_q = query.strip()
    if not clean_q:
        return []

    # 1. Optional LLM-assisted decomposition (invoked if explicitly requested or provided)
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

    # 2. Generalized Syntactic Heuristic Decomposition
    # Structure A: Generalized Relative Clause Pattern:
    # "Which/What [Target] was [Predicate] by the [Bridge Type] (that/which/who/of which) (Condition)?"
    m_rel = re.search(
        r"^(which|what|who)\s+(.*?)\s+(?:was\s+ultimately\s+|was\s+|were\s+)?(.*?)\s+by\s+(?:the\s+)?([a-z0-9_\-]+)\s+(?:that|which|who|of\s+which)\s+(.*?)\??$",
        clean_q, re.IGNORECASE
    )
    if m_rel:
        q_word, target, verb_phrase, bridge_type, condition = (
            m_rel.group(1).strip(),
            m_rel.group(2).strip(),
            m_rel.group(3).strip(),
            m_rel.group(4).strip(),
            m_rel.group(5).strip(),
        )
        m_member = re.search(
            r"^(.*?)\s+is\s+a\s+(?:member|leader|soldier|commander|standard-bearer|falconer)",
            condition, re.IGNORECASE
        )
        if m_member:
            entity = m_member.group(1).strip()
            return [
                f"Which {bridge_type} is {entity} a member of?",
                f"{q_word.capitalize()} {target} was {verb_phrase} by that {bridge_type}?",
            ]
        m_inc = re.search(
            r"^included\s+(.*?)\s+as\s+(?:one\s+of\s+its\s+members|a\s+member)",
            condition, re.IGNORECASE
        )
        if m_inc:
            entity = m_inc.group(1).strip()
            return [
                f"Which {bridge_type} included {entity} as a member?",
                f"{q_word.capitalize()} {target} was {verb_phrase} by that {bridge_type}?",
            ]
        return [
            f"Which {bridge_type} {condition}?",
            f"{q_word.capitalize()} {target} was {verb_phrase} by that {bridge_type}?",
        ]

    # Structure B: Generalized "Which individual was a member of the [Bridge Type] that [Predicate] [Target]?"
    m_indiv = re.search(
        r"^(which|what)\s+(individual|person|figure|member|commander|leader|ruler)\s+(?:was|is)\s+(?:a\s+)?([a-z0-9_\-]+)\s+(?:of|in)\s+(?:the\s+)?([a-z0-9_\-]+)\s+(?:that|which|who)\s+(.*?)\s+(?:the\s+)?(.*?)\??$",
        clean_q, re.IGNORECASE
    )
    if m_indiv:
        q_word, role_word, rel_word, bridge_type, verb, target = (
            m_indiv.group(1).strip(),
            m_indiv.group(2).strip(),
            m_indiv.group(3).strip(),
            m_indiv.group(4).strip(),
            m_indiv.group(5).strip(),
            m_indiv.group(6).strip(),
        )
        return [
            f"Which {bridge_type} {verb} {target}?",
            f"Which {role_word}s were {rel_word}s of that {bridge_type}?",
        ]

    # Structure C: Generalized Nested Possessive / Preposition:
    # "Whose/Who/Which [Target Relation] [Verb] the [Bridge] of (the)? [Seed]?"
    m_nest = re.search(
        r"^(whose|who|which|what)\s+(.*?)\s+(?:encompasses|contains|holds|houses|controls|rules|guards|is|was)\s+(?:the\s+)?(.*?)\s+of\s+(?:the\s+)?(.*?)\??$",
        clean_q, re.IGNORECASE
    )
    if m_nest:
        q_word, target_rel, bridge, seed = (
            m_nest.group(1).strip(),
            m_nest.group(2).strip(),
            m_nest.group(3).strip(),
            m_nest.group(4).strip(),
        )
        return [
            f"Where is the {bridge} of {seed}?",
            f"{q_word.capitalize()} {target_rel} encompasses that location?",
        ]

    # Structure D: Generalized Double Possessive / Genitive ("Who was the ruler of the stronghold of X")
    m_of = re.search(
        r"^(who|what)\s+(?:was|is)\s+the\s+(.*?)\s+of\s+(?:the\s+)?(.*?)\s+of\s+(?:the\s+)?(.*?)\??$",
        clean_q, re.IGNORECASE
    )
    if m_of:
        q_word, target, bridge, seed = m_of.group(1).strip(), m_of.group(2).strip(), m_of.group(3).strip(), m_of.group(4).strip()
        return [
            f"Where is the {bridge} of {seed}?",
            f"{q_word.capitalize()} was the {target} of that {bridge}?",
        ]

    # Structure E: Generalized Possessive Genitive ('s):
    # "Which [Target] did [Seed]'s [Bridge Type] [Action]?"
    m_gen = re.search(
        r"^(which|what)\s+(.*?)\s+did\s+(.*?)(?:'s|\s+own)\s+(.*?)\s+(?:ultimately\s+)?(win|conquer|rule|control|hold|command)\??$",
        clean_q, re.IGNORECASE
    )
    if m_gen:
        q_word, target, seed, bridge_type, action = (
            m_gen.group(1).strip(),
            m_gen.group(2).strip(),
            m_gen.group(3).strip(),
            m_gen.group(4).strip(),
            m_gen.group(5).strip(),
        )
        return [
            f"Which {bridge_type} does {seed} belong to or serve?",
            f"{q_word.capitalize()} {target} did that {bridge_type} {action}?",
        ]

    # Structure F: Generalized Connection / Relation to Entity Role:
    # "In what way is [Seed] connected/related to the [Role] of [Event/Entity]?"
    m_conn = re.search(
        r"^(?:in\s+what\s+way\s+is|how\s+is)\s+(.*?)\s+(?:connected|affiliated|related|allied)\s+to\s+(?:the\s+)?(.*?)\s+of\s+(?:the\s+)?(.*?)\??$",
        clean_q, re.IGNORECASE
    )
    if m_conn:
        seed, role, event = m_conn.group(1).strip(), m_conn.group(2).strip(), m_conn.group(3).strip()
        return [
            f"Who were the {role} of {event}?",
            f"How is {seed} connected or affiliated to that {role}?",
        ]

    # Structure G: Generalized Participial / Relic / Location inquiry:
    # "To which [Location] ... [Item] ... [Participle] by [Seed]?"
    m_part = re.search(
        r"^(?:to\s+which|where|in\s+which)\s+(.*?)\s+.*?examine\s+(?:the\s+)?(.*?)\s+(?:long\s+)?(borne|wielded|held|created|forged|carried|crafted)\s+by\s+(.*?)(?:\s+since|\?|$)",
        clean_q, re.IGNORECASE
    )
    if m_part:
        loc, item, part, seed = m_part.group(1).strip(), m_part.group(2).strip(), m_part.group(3).strip(), m_part.group(4).strip()
        return [
            f"What {item} was {part} by {seed}?",
            f"Where or in which {loc} is that {item} housed or located?",
        ]

    # Structure H: Generic Relative Clause Splitter Fallback
    m_split = re.search(r"^(.*?)\s+(that|who|which)\s+(.*?)\??$", clean_q, re.IGNORECASE)
    if m_split and any(w in clean_q.lower() for w in ("which", "what", "who", "where", "whose")):
        c1, rel, c2 = m_split.group(1).strip(), m_split.group(2).strip(), m_split.group(3).strip()
        if len(c1.split()) >= 3 and len(c2.split()) >= 3:
            return [
                f"Which entity {c2}?",
                f"{c1} that entity?",
            ]

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
