from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class SourceCharacteristics(BaseModel):
    """Fine-grained epistemological and provenance metadata for an archive source."""
    source_type: str  # codex, chronicle, wiki, ephemera, unknown
    document_subtype: str  # ballad, trial_transcript, decree, field_report, sermon, letter, interrogation_record, petition, contract, auction_catalogue, muster_roll, quartermaster_ledger, wiki_article, codex_entry, chronicle_entry, unknown
    claim_strength: str  # assertion, allegation, rumor, observation, decree
    narrative_voice: str  # official, personal, third_party, in_character, narrative
    temporal_reliability: str  # contemporary, retrospective, mythological


class EvidenceRecord(BaseModel):
    """Deterministic evidence container tracking a single archive passage."""
    id: str  # Sequential ID: EVIDENCE_001, EVIDENCE_002, ...
    chunk_id: str
    document_id: str
    content: str
    source_category: str
    source_subtype: str
    page: Optional[int] = None
    section_title: Optional[str] = None
    document_title: str
    source_file: str = ""
    score: float = 0.0
    source_characteristics: Optional[SourceCharacteristics] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Conflict(BaseModel):
    """Structured representation of a factual contradiction or qualification between sources."""
    claim_summary: str
    supporting_evidence: List[EvidenceRecord] = Field(default_factory=list)
    opposing_evidence: List[EvidenceRecord] = Field(default_factory=list)
    conflict_type: str = "contradiction"  # "contradiction", "qualification", "uncertainty"


class CitationIssue(BaseModel):
    """Discrepancy or validation error in an answer's cited references."""
    evidence_id: str
    issue_type: str  # "missing", "invalid", "unsupported"
    description: str


class ClaimCheck(BaseModel):
    """Post-generation claim verification status."""
    claim_text: str
    has_evidence: bool
    evidence_ids: List[str] = Field(default_factory=list)
    verdict: str  # "supported", "unsupported", "partially_supported"


class VerificationResult(BaseModel):
    """Full evaluation of an answer's factual fidelity and citation accuracy."""
    is_verified: bool
    claim_checks: List[ClaimCheck] = Field(default_factory=list)
    citation_issues: List[CitationIssue] = Field(default_factory=list)
    conflict_acknowledgements: List[str] = Field(default_factory=list)
    unsupported_claims: List[str] = Field(default_factory=list)


class FinalAnswer(BaseModel):
    """Comprehensive grounded answer artifact returned by the Phase 6 pipeline."""
    question: str
    answer_text: str  # Resolved citation text: [Document Title, p.X]
    raw_answer_text: str = ""  # Deterministic reference text: [EVIDENCE_001]
    evidence: List[EvidenceRecord] = Field(default_factory=list)
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    conflicts: List[Conflict] = Field(default_factory=list)
    evidence_status: str = "HIGH"  # "HIGH", "MEDIUM", "LOW", "INSUFFICIENT"
    verification_result: Optional[VerificationResult] = None
    query_trace: Dict[str, Any] = Field(default_factory=dict)
