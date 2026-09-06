import re
import json
import logging
from typing import List, Optional, Union, Any
from pydantic import BaseModel, Field

from src.models.evidence import EvidenceRecord, SourceCharacteristics
from src.knowledge.source_classification import classify_source
from src.providers.llm_provider import LLMProvider

logger = logging.getLogger(__name__)


class Claim(BaseModel):
    """
    Structured assertion with epistemological predicate preservation.
    """
    id: str = Field(default="", description="Unique claim ID e.g. CLAIM_001")
    subject: str = Field(..., description="Subject entity making or central to the claim")
    predicate: str = Field(..., description="Canonical or epistemologically qualified predicate e.g. accused_of, founded")
    object: str = Field(..., description="Target entity, event, or charge")
    temporal_context: Optional[str] = Field(None, description="In-world timeframe or year")
    source_evidence_id: str = Field(default="", description="Identifier of supporting evidence")
    source_type: str = Field(default="unknown", description="Source document type e.g. ephemera, codex")
    claim_strength: str = Field(default="assertion", description="Epistemological strength: assertion, allegation, rumor")


EPISTEMOLOGICAL_RULES = [
    # Allegations
    (
        re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:was\s+)?(?:accused\s+of|charged\s+with)\s+([^.,;]+)", re.IGNORECASE),
        "accused_of",
        "allegation",
    ),
    # Rumors
    (
        re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:was\s+)?rumored\s+to\s+(?:have\s+)?([^.,;]+)", re.IGNORECASE),
        "rumored_to_have",
        "rumor",
    ),
    # Claims / Pretense
    (
        re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:was\s+)?claimed\s+to\s+be\s+([^.,;]+)", re.IGNORECASE),
        "claimed_to_be",
        "allegation",
    ),
    # Founding
    (
        re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:founded|established)\s+(?:the\s+)?([^.,;]+)", re.IGNORECASE),
        "founded",
        "assertion",
    ),
    # Destruction
    (
        re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:destroyed|shattered|razed)\s+(?:the\s+)?([^.,;]+)", re.IGNORECASE),
        "destroyed",
        "assertion",
    ),
    # Commission / Participation
    (
        re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:committed|perpetrated)\s+([^.,;]+)", re.IGNORECASE),
        "committed",
        "assertion",
    ),
    # Membership
    (
        re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:was\s+a\s+member\s+of|belonged\s+to)\s+(?:the\s+)?([^.,;]+)", re.IGNORECASE),
        "member_of",
        "assertion",
    ),
]


def extract_claims_from_chunk(
    chunk: Union[Any, str],
    entities_in_chunk: Optional[List[Any]] = None,
    llm: Optional[LLMProvider] = None,
) -> List[Claim]:
    """
    Extract factual assertions from a chunk as structured claims.

    Enforces epistemological integrity:
    - 'accused_of' != 'committed'
    - 'rumored_to_have' != 'did'
    - 'claimed_to_be' != 'is'
    """
    text = ""
    evidence_id = ""
    source_type = "unknown"
    default_strength = "assertion"

    if isinstance(chunk, str):
        text = chunk
    elif isinstance(chunk, EvidenceRecord):
        text = chunk.content
        evidence_id = chunk.id
        source_type = chunk.source_category
        if chunk.source_characteristics:
            default_strength = chunk.source_characteristics.claim_strength
    elif hasattr(chunk, "content"):
        text = chunk.content
        evidence_id = getattr(chunk, "chunk_id", "")
        chars = classify_source(chunk)
        source_type = chars.source_type
        default_strength = chars.claim_strength

    # Temporal context extraction (e.g. "in 312 AS", "during the Ashen War")
    temporal_match = re.search(r"\b(?:in\s+\d+\s+AS|during\s+the\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b", text, flags=re.IGNORECASE)
    temporal_ctx = temporal_match.group(0) if temporal_match else None


    claims: List[Claim] = []
    claim_count = 0

    # If LLM is provided and requested, we can use LLM structured extraction
    if llm is not None:
        prompt = f"""Extract factual assertions from this text as structured claims.
Preserve the exact epistemological status:
- If someone is accused of something, the predicate MUST be 'accused_of', NEVER 'committed'.
- If something is rumored, the predicate MUST be 'rumored_to_have'.
- If someone claims to be something, the predicate MUST be 'claimed_to_be'.

Text:
"{text}"

Return JSON:
{{"claims": [
  {{"subject": "...", "predicate": "...", "object": "...", "claim_strength": "assertion|allegation|rumor"}}
]}}"""
        try:
            resp = llm.generate(prompt=prompt, system_prompt="You are an epistemological assertion extractor.")
            match = re.search(r"(\{.*\})", resp.content, re.DOTALL)
            if match:
                data = json.loads(match.group(1))
                for c in data.get("claims", []):
                    claim_count += 1
                    claims.append(
                        Claim(
                            id=f"CLAIM_{claim_count:03d}",
                            subject=c.get("subject", ""),
                            predicate=c.get("predicate", ""),
                            object=c.get("object", ""),
                            temporal_context=temporal_ctx,
                            source_evidence_id=evidence_id,
                            source_type=source_type,
                            claim_strength=c.get("claim_strength", default_strength),
                        )
                    )
                if claims:
                    return claims
        except Exception as e:
            logger.debug("LLM claim extraction failed, using heuristic rules: %s", e)

    # Heuristic Rule Extraction
    for pattern, predicate, strength in EPISTEMOLOGICAL_RULES:
        for match in pattern.finditer(text):
            subj = match.group(1).strip()
            # Clean trailing auxiliary verbs if captured in subject
            subj = re.sub(r"\s+\b(?:was|is|were|are|been|had)\b.*$", "", subj, flags=re.IGNORECASE).strip()
            obj = match.group(2).strip()

            # Clean trailing punctuation
            obj = re.sub(r"[,;.]+$", "", obj).strip()


            claim_count += 1
            claims.append(
                Claim(
                    id=f"CLAIM_{claim_count:03d}",
                    subject=subj,
                    predicate=predicate,
                    object=obj,
                    temporal_context=temporal_ctx,
                    source_evidence_id=evidence_id,
                    source_type=source_type,
                    claim_strength=strength if strength != "assertion" else default_strength,
                )
            )

    return claims
