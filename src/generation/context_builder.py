from typing import List, Tuple, Dict, Any, Union, Optional

from src.models.search import SearchResult
from src.models.evidence import EvidenceRecord, Conflict
from src.models.query import SufficiencyScore
from src.knowledge.evidence import EvidenceManager
from src.knowledge.source_classification import classify_source


def build_evidence_context(
    evidence: Union[EvidenceManager, List[EvidenceRecord], List[SearchResult]],
    conflicts: Optional[List[Conflict]] = None,
    sufficiency: Optional[SufficiencyScore] = None,
    max_tokens: int = 3500,
) -> Tuple[str, Dict[str, Any]]:
    """
    Format evidence records or search results into structured evidence blocks with sequential IDs.

    Supports:
    - Phase 6 EvidenceManager / List[EvidenceRecord] with full metadata, conflict alerts, and source notes.
    - Legacy List[SearchResult] with backwards-compatible (context_string, evidence_map) output.

    Returns:
        Tuple of (formatted_context_string, evidence_map)
    """
    context_blocks = ["## Evidence\n"]
    evidence_map: Dict[str, Any] = {}
    source_notes: List[str] = []

    records: List[Any] = []
    if isinstance(evidence, EvidenceManager):
        records = evidence.evidence
    elif isinstance(evidence, list):
        records = evidence

    current_approx_tokens = 0

    for i, res in enumerate(records, start=1):
        if isinstance(res, EvidenceRecord):
            ev_id = res.id
            doc_title = res.document_title or "Unknown Document"
            loc_parts = []
            if res.page:
                loc_parts.append(f"p.{res.page}")
            if res.section_title:
                loc_parts.append(f"Section: {res.section_title}")
            loc_str = f" ({', '.join(loc_parts)})" if loc_parts else ""

            char_str = ""
            if res.source_characteristics:
                char_str = f" — {res.source_characteristics.claim_strength}, {res.source_characteristics.document_subtype}"
                note = f"- {ev_id} is from {res.source_characteristics.source_type} ({res.source_characteristics.claim_strength}, {res.source_characteristics.narrative_voice} voice)"
                source_notes.append(note)

            block = f"[{ev_id}] (Source: {doc_title}{loc_str}{char_str})\n\"{res.content.strip()}\"\n"
            evidence_map[ev_id] = res
            evidence_map[f"EVIDENCE_{i}"] = res

        elif isinstance(res, SearchResult):
            ev_id = f"EVIDENCE_{i}"
            doc_title = res.document_title or res.metadata.get("source_file", "Unknown Document")
            loc_parts = []
            if res.page_start:
                if res.page_end and res.page_end != res.page_start:
                    loc_parts.append(f"pp. {res.page_start}-{res.page_end}")
                else:
                    loc_parts.append(f"p. {res.page_start}")
            if res.chapter:
                loc_parts.append(f"Chapter: {res.chapter}")
            if res.section_title:
                loc_parts.append(f"Section: {res.section_title}")

            loc_str = f" ({', '.join(loc_parts)})" if loc_parts else ""

            # Check if source characteristics can be determined
            chars = classify_source(res)
            char_str = f" — {chars.claim_strength}, {chars.document_subtype}"
            source_notes.append(f"- {ev_id} is from {chars.source_type} ({chars.claim_strength}, {chars.narrative_voice} voice)")

            block = f"[{ev_id}] Source: {doc_title}{loc_str}{char_str}\n{res.content.strip()}\n"
            evidence_map[ev_id] = res
            evidence_map[f"EVIDENCE_{i:03d}"] = res
        else:
            continue

        approx_tokens = len(block.split()) * 1.3
        if current_approx_tokens + approx_tokens > max_tokens and len(context_blocks) > 1:
            break

        context_blocks.append(block)
        current_approx_tokens += approx_tokens

    # Append Conflicts section if present
    if conflicts:
        context_blocks.append("\n## Conflicts Detected")
        for c in conflicts:
            sup_ids = ", ".join(r.id for r in c.supporting_evidence) or "Sources"
            opp_ids = ", ".join(r.id for r in c.opposing_evidence) or "opposing sources"
            context_blocks.append(f"- {c.claim_summary} (Supported by: {sup_ids}; Opposed by: {opp_ids}) [{c.conflict_type}]")

    # Append Source Notes section if present
    if source_notes:
        context_blocks.append("\n## Source Notes")
        context_blocks.extend(source_notes[:10])

    # Append Sufficiency / Evidence Status if present
    if sufficiency:
        context_blocks.append(f"\n## Evidence Status: {sufficiency.level} (Coverage: {sufficiency.coverage * 100:.0f}%)")
        if sufficiency.reasoning:
            context_blocks.append(f"Archive assessment: {sufficiency.reasoning}")

    formatted_context = "\n".join(context_blocks)
    return formatted_context, evidence_map
