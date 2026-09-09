import re
import json
import logging
from typing import List, Optional, Dict, Any

from src.models.evidence import EvidenceRecord, Conflict
from src.providers.llm_provider import LLMProvider

logger = logging.getLogger(__name__)

CONFLICT_PROMPT_TEMPLATE = """Given the following evidence passages from the fictional Ashen Era archive, identify any contradictions, disagreements, or qualifications between them.

Evidence:
{evidence_texts}

For each conflict found, provide:
- claim: clear summary of what the dispute/conflict is about
- supporting: list of evidence IDs that support one view (e.g. ["EVIDENCE_001"])
- opposing: list of evidence IDs that support the opposing view (e.g. ["EVIDENCE_002"])
- type: "contradiction" (direct factual disagreement), "qualification" (one source adds nuance/condition), or "uncertainty" (sources express doubt/ambiguity)

If there are no conflicts, return an empty list.

Respond in valid JSON format:
{{"conflicts": [
  {{
    "claim": "...",
    "supporting": ["EVIDENCE_001"],
    "opposing": ["EVIDENCE_002"],
    "type": "contradiction"
  }}
]}}
"""

OPPOSING_POLARITY_PAIRS = [
    (
        {"betray", "betrayal", "betrayed", "traitor", "treason", "accused", "alleged", "charged", "guilty"},
        {"loyal", "loyalty", "faithful", "never wavered", "denied", "innocent", "unbroken", "steadfast"},
    ),
    (
        {"won", "victorious", "triumphed", "prevailed"},
        {"lost", "defeated", "fallen", "crushed", "failed"},
    ),
    (
        {"killed", "slain", "murdered", "executed", "died"},
        {"survived", "escaped", "lived", "endured", "alive"},
    ),
]


def detect_conflicts_heuristic(evidence: List[EvidenceRecord]) -> List[Conflict]:
    """
    Deterministic rule-based conflict detector for offline execution and fast-path filtering.
    """
    if len(evidence) < 2:
        return []

    conflicts: List[Conflict] = []
    rec_map = {r.id: r for r in evidence}

    # Pairwise inspection
    for i in range(len(evidence)):
        r1 = evidence[i]
        text1 = (r1.content + " " + (r1.source_characteristics.claim_strength if r1.source_characteristics else "")).lower()
        for j in range(i + 1, len(evidence)):
            r2 = evidence[j]
            text2 = (r2.content + " " + (r2.source_characteristics.claim_strength if r2.source_characteristics else "")).lower()

            for neg_set, pos_set in OPPOSING_POLARITY_PAIRS:
                has_neg_1 = any(w in text1 for w in neg_set)
                has_pos_2 = any(w in text2 for w in pos_set)
                has_pos_1 = any(w in text1 for w in pos_set)
                has_neg_2 = any(w in text2 for w in neg_set)

                if (has_neg_1 and has_pos_2) or (has_pos_1 and has_neg_2):
                    sup = [r1] if (has_pos_1 or has_neg_1) else [r2]
                    opp = [r2] if sup == [r1] else [r1]
                    summary = f"Disagreement regarding character/allegation between {r1.id} and {r2.id}"
                    conf_type = "contradiction"
                    conflicts.append(
                        Conflict(
                            claim_summary=summary,
                            supporting_evidence=sup,
                            opposing_evidence=opp,
                            conflict_type=conf_type,
                        )
                    )
                    break

    return conflicts


def should_run_llm_conflict_detection(evidence: List[EvidenceRecord]) -> bool:
    """
    Fast-path heuristic: returns True only if evidence contains potential conflict signals.
    Avoids 15-20s LLM round trip when all sources are consistent or single-genre.
    """
    if len(evidence) < 2:
        return False
    types = {r.source_category for r in evidence}
    has_allegation = any(r.source_characteristics and r.source_characteristics.claim_strength == "allegation" for r in evidence)
    has_rumor = any(r.source_characteristics and r.source_characteristics.claim_strength == "rumor" for r in evidence)
    has_opposing_genres = (has_allegation and (has_rumor or "wiki" in types or "chronicle" in types))

    heuristic_conflicts = detect_conflicts_heuristic(evidence)
    return has_opposing_genres or len(heuristic_conflicts) > 0 or ("ephemera" in types and len(types) > 1)


def detect_conflicts(
    evidence: List[EvidenceRecord],
    llm: Optional[LLMProvider] = None,
) -> List[Conflict]:
    """
    Analyze a set of evidence records for contradictions, qualifications, or uncertainty.

    Strategy:
    1. If evidence has < 2 records, return empty list.
    2. Check fast-path: if evidence is uniform and polarity clean, skip LLM.
    3. If LLM is provided, ask LLM to identify contradictions using structured JSON.
    4. If LLM fails or is omitted, fallback to deterministic heuristic check.
    """
    if not evidence or len(evidence) < 2:
        return []

    if llm is None or not should_run_llm_conflict_detection(evidence):
        return detect_conflicts_heuristic(evidence)

    evidence_map: Dict[str, EvidenceRecord] = {}
    evidence_lines: List[str] = []


    for rec in evidence:
        evidence_map[rec.id] = rec
        doc_part = f"Source: {rec.document_title}"
        if rec.page:
            doc_part += f", p.{rec.page}"
        if rec.source_characteristics:
            doc_part += f" ({rec.source_characteristics.claim_strength}, {rec.source_characteristics.document_subtype})"
        evidence_lines.append(f"[{rec.id}] {doc_part}\n\"{rec.content.strip()}\"")

    if llm is None:
        return detect_conflicts_heuristic(evidence)

    prompt = CONFLICT_PROMPT_TEMPLATE.format(
        evidence_texts="\n\n".join(evidence_lines[:8])
    )

    try:
        response = llm.generate(
            prompt=prompt,
            system_prompt="You are an archival intelligence evaluator tasked with detecting contradictions and qualifications across historical sources.",
        )
        content = response.content.strip()
        if not content:
            return detect_conflicts_heuristic(evidence)

        # Parse JSON
        json_match = re.search(r"(\{.*\})", content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(1))
        else:
            data = json.loads(content)

        conflict_dicts = data.get("conflicts", [])
        conflicts: List[Conflict] = []

        for c_dict in conflict_dicts:
            claim = c_dict.get("claim", "Unspecified conflict")
            c_type = c_dict.get("type", "contradiction")
            if c_type not in ("contradiction", "qualification", "uncertainty"):
                c_type = "contradiction"

            supporting_ids = c_dict.get("supporting", [])
            opposing_ids = c_dict.get("opposing", [])

            # Map IDs to actual EvidenceRecord instances
            sup_records = [evidence_map[eid] for eid in supporting_ids if eid in evidence_map]
            opp_records = [evidence_map[eid] for eid in opposing_ids if eid in evidence_map]

            if sup_records or opp_records:
                conflicts.append(
                    Conflict(
                        claim_summary=claim,
                        supporting_evidence=sup_records,
                        opposing_evidence=opp_records,
                        conflict_type=c_type,
                    )
                )

        return conflicts

    except Exception as e:
        logger.warning("LLM conflict detection failed, falling back to heuristic: %s", e)
        return detect_conflicts_heuristic(evidence)
