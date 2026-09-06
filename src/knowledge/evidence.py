import re
import logging
from typing import List, Optional, Set, Dict, Any, Union
from collections import defaultdict

from src.models.search import SearchResult
from src.models.query import QueryState, SufficiencyScore
from src.models.evidence import EvidenceRecord, SourceCharacteristics
from src.knowledge.source_classification import classify_source
from src.providers.llm_provider import LLMProvider

logger = logging.getLogger(__name__)


class EvidenceManager:
    """
    Comprehensive manager for gathering, registering, deduplicating,
    and classifying evidence records with deterministic identifiers.
    """

    def __init__(self):
        self.evidence: List[EvidenceRecord] = []
        self._counter: int = 0
        self._seen_chunk_ids: Set[str] = set()

    def register_evidence(self, search_result: SearchResult) -> EvidenceRecord:
        """
        Register a SearchResult as an EvidenceRecord with a unique deterministic ID.
        Assigns sequentially numbered IDs: EVIDENCE_001, EVIDENCE_002, ...
        """
        self._counter += 1
        ev_id = f"EVIDENCE_{self._counter:03d}"

        # Classify source characteristics
        source_chars = classify_source(search_result)

        source_file = (
            search_result.source_path
            or search_result.metadata.get("source_file", "")
            or search_result.metadata.get("source_path", "")
        )
        doc_title = search_result.document_title or search_result.metadata.get("document_title", "Unknown Document")

        record = EvidenceRecord(
            id=ev_id,
            chunk_id=search_result.chunk_id,
            document_id=search_result.document_id,
            content=search_result.content,
            source_category=source_chars.source_type,
            source_subtype=source_chars.document_subtype,
            page=search_result.page_start,
            section_title=search_result.section_title,
            document_title=doc_title,
            source_file=source_file,
            score=search_result.score,
            source_characteristics=source_chars,
            metadata=search_result.metadata,
        )

        self.evidence.append(record)
        self._seen_chunk_ids.add(search_result.chunk_id)
        return record

    def register_evidence_record(self, record: EvidenceRecord) -> EvidenceRecord:
        """Register an existing EvidenceRecord, generating an ID if missing."""
        if not record.id:
            self._counter += 1
            record.id = f"EVIDENCE_{self._counter:03d}"
        if not record.source_characteristics:
            record.source_characteristics = classify_source(record)
            record.source_subtype = record.source_characteristics.document_subtype
            record.source_category = record.source_characteristics.source_type

        self.evidence.append(record)
        self._seen_chunk_ids.add(record.chunk_id)
        return record

    def deduplicate(self) -> None:
        """
        Remove duplicate evidence (e.g. same chunk appearing from multiple retrieval paths).
        Preserves the first occurrence or highest-scoring copy.
        """
        seen: Set[str] = set()
        deduped: List[EvidenceRecord] = []

        for rec in self.evidence:
            if rec.chunk_id not in seen:
                seen.add(rec.chunk_id)
                deduped.append(rec)

        self.evidence = deduped
        self._seen_chunk_ids = seen

    def group_by_entity(self, entities: List[str]) -> Dict[str, List[EvidenceRecord]]:
        """Group evidence records by which entity they mention."""
        grouped: Dict[str, List[EvidenceRecord]] = {ent.strip(): [] for ent in entities if ent.strip()}
        for ent_clean in grouped:
            pattern = re.compile(rf"\b{re.escape(ent_clean)}\b", re.IGNORECASE)
            for rec in self.evidence:
                target_text = f"{rec.document_title} {rec.section_title or ''} {rec.content}"
                if pattern.search(target_text):
                    grouped[ent_clean].append(rec)
        return grouped


    def group_by_document(self) -> Dict[str, List[EvidenceRecord]]:
        """Group evidence records by source document."""
        grouped: Dict[str, List[EvidenceRecord]] = defaultdict(list)
        for rec in self.evidence:
            doc_key = rec.document_title or rec.document_id or "Unknown Document"
            grouped[doc_key].append(rec)
        return dict(grouped)

    def get_source_diversity(self) -> Dict[str, Any]:
        """Count how many unique documents, categories, and subtypes are represented."""
        unique_docs: Set[str] = {
            rec.document_title or rec.document_id for rec in self.evidence if rec.document_title or rec.document_id
        }
        category_counts: Dict[str, int] = defaultdict(int)
        subtype_counts: Dict[str, int] = defaultdict(int)

        for rec in self.evidence:
            category_counts[rec.source_category] += 1
            subtype_counts[rec.source_subtype] += 1

        return {
            "total_records": len(self.evidence),
            "unique_documents": len(unique_docs),
            "unique_categories": len(category_counts),
            "unique_subtypes": len(subtype_counts),
            "categories": dict(category_counts),
            "subtypes": dict(subtype_counts),
        }

    def get_evidence_by_id(self, evidence_id: str) -> Optional[EvidenceRecord]:
        """
        Look up evidence by its deterministic ID.
        Accepts formats: 'EVIDENCE_001', 'EVIDENCE_1', '[EVIDENCE_001]', '1'.
        """
        cleaned = evidence_id.strip("[] \t\r\n").upper()
        # Extract integer part if possible
        num_match = re.search(r"\d+", cleaned)
        target_num = int(num_match.group()) if num_match else None

        for rec in self.evidence:
            if rec.id.upper() == cleaned:
                return rec
            if target_num is not None:
                rec_match = re.search(r"\d+", rec.id)
                if rec_match and int(rec_match.group()) == target_num:
                    return rec

        return None


def assess_evidence_sufficiency(
    query: str,
    query_state: Union[QueryState, EvidenceManager],
    llm: Optional[LLMProvider] = None,
) -> SufficiencyScore:
    """
    Assess whether the gathered evidence is sufficient to reliably answer the query.

    Factors evaluated:
    - Target entity coverage: does retrieved evidence mention the query entities?
    - Multi-hop document diversity: does evidence come from >= 2 unique documents?
    - Hop evidence presence: are both hop 1 and hop 2 represented in evidence_per_hop?
    """
    # Support both QueryState and direct EvidenceManager
    if isinstance(query_state, EvidenceManager):
        evidence_records = query_state.evidence
        if not evidence_records:
            return SufficiencyScore(
                level="INSUFFICIENT",
                coverage=0.0,
                source_count=0,
                unique_documents=0,
                missing=["No evidence chunks registered"],
                reasoning="EvidenceManager contains 0 evidence records.",
            )

        doc_ids = {r.document_id for r in evidence_records if r.document_id}
        doc_titles = {r.document_title for r in evidence_records if r.document_title}
        unique_docs = max(len(doc_ids), len(doc_titles))

        combined_text = " ".join(f"{r.document_title} {r.content}" for r in evidence_records[:10]).lower()
        # Heuristic entity extraction if query_state was EvidenceManager
        entities = [w for w in re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", query) if len(w) > 2]
        if not entities:
            from src.retrieval.query_analyzer import STOP_WORDS
            entities = [w for w in re.findall(r"\b[a-zA-Z]{4,}\b", query) if w.lower() not in STOP_WORDS]

        missing_entities = [e for e in entities if e.lower() not in combined_text]
        entity_coverage = 1.0 - (len(missing_entities) / max(len(entities), 1)) if entities else 0.0

        if entity_coverage < 0.35:
            level = "INSUFFICIENT"
            reasoning = f"Core query terms not found in gathered archive records: {missing_entities[:3]}"
        elif unique_docs >= 2 and entity_coverage >= 0.7:
            level = "HIGH"
            reasoning = f"Evidence gathered across {unique_docs} documents with high query alignment."
        elif unique_docs >= 1 and entity_coverage >= 0.45:
            level = "MEDIUM"
            reasoning = f"Moderate evidence across {unique_docs} documents."
        else:
            level = "LOW"
            reasoning = f"Limited evidence alignment ({entity_coverage * 100:.0f}%)."

        return SufficiencyScore(
            level=level,
            coverage=round(entity_coverage, 2),
            source_count=len(evidence_records),
            unique_documents=unique_docs,
            missing=missing_entities,
            reasoning=reasoning,
        )


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
