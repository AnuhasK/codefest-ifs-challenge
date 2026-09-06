import logging
from typing import List, Optional, Set
from src.models.query import QueryState, SufficiencyScore
from src.providers.llm_provider import LLMProvider

logger = logging.getLogger(__name__)


def assess_evidence_sufficiency(
    query: str,
    query_state: QueryState,
    llm: Optional[LLMProvider] = None,
) -> SufficiencyScore:
    """
    Assess whether the gathered evidence is sufficient to reliably answer the query.

    Factors evaluated:
    - Target entity coverage: does retrieved evidence mention the query entities?
    - Multi-hop document diversity: does evidence come from >= 2 unique documents?
    - Hop evidence presence: are both hop 1 and hop 2 represented in evidence_per_hop?
    """
    evidence = query_state.retrieved_evidence
    if not evidence:
        return SufficiencyScore(
            level="INSUFFICIENT",
            coverage=0.0,
            source_count=0,
            unique_documents=0,
            missing=["No evidence chunks retrieved"],
            reasoning="Retrieval returned an empty candidate list.",
        )

    # Document diversity
    doc_ids: Set[str] = {item.document_id for item in evidence if item.document_id}
    doc_titles: Set[str] = {item.document_title for item in evidence if item.document_title}
    unique_docs = max(len(doc_ids), len(doc_titles))

    # Entity mention coverage in retrieved text
    combined_text = " ".join(f"{item.document_title} {item.content}" for item in evidence[:10]).lower()
    missing_entities: List[str] = []
    for ent in query_state.identified_entities:
        if ent.lower() not in combined_text:
            missing_entities.append(ent)

    # Check hop coverage for multi-hop queries
    hop_count = len(query_state.evidence_per_hop)
    has_multiple_hops = hop_count >= 2

    # If LLM is provided and enabled, could use LLM assessment.
    # Otherwise, calculate deterministic heuristic score:
    entity_coverage = 1.0 - (len(missing_entities) / max(len(query_state.identified_entities), 1))

    if query_state.query_type == "multi_hop":
        # Multi-hop requirements: needs distinct documents and hop coverage
        if unique_docs >= 2 and entity_coverage >= 0.8 and (has_multiple_hops or query_state.traversal_hops):
            level = "HIGH"
            coverage = 0.95
            reasoning = f"Sufficient cross-document evidence found across {unique_docs} documents with multi-hop connections."
        elif unique_docs >= 2 and entity_coverage >= 0.5:
            level = "MEDIUM"
            coverage = 0.65
            reasoning = f"Partial multi-hop evidence across {unique_docs} documents; some relationship details may need further refinement."
        elif unique_docs >= 1 and entity_coverage >= 0.5:
            level = "LOW"
            coverage = 0.4
            reasoning = f"Evidence is confined to {unique_docs} document for a multi-hop query; second-hop evidence is missing."
        else:
            level = "INSUFFICIENT"
            coverage = 0.1
            reasoning = "Insufficient cross-document evidence to resolve multi-hop query."
    else:
        # Simple / single-entity query
        if entity_coverage >= 0.8:
            level = "HIGH"
            coverage = 1.0
            reasoning = "Core query entities directly covered in retrieved evidence."
        elif entity_coverage >= 0.5:
            level = "MEDIUM"
            coverage = 0.6
            reasoning = "Partial entity match in retrieved evidence."
        else:
            level = "LOW"
            coverage = 0.3
            reasoning = f"Key entities missing from retrieved evidence: {missing_entities}"

    return SufficiencyScore(
        level=level,
        coverage=round(coverage, 2),
        source_count=len(evidence),
        unique_documents=unique_docs,
        missing=missing_entities,
        reasoning=reasoning,
    )
