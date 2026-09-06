from src.models.search import SearchResult
from src.knowledge.evidence import EvidenceManager
from src.generation.citations import CitationResolver


def test_resolve_single_citation():
    manager = EvidenceManager()
    manager.register_evidence(
        SearchResult(
            chunk_id="c1",
            document_id="d1",
            document_title="Royal Annals",
            page_start=84,
            content="Ser Vael was a member of the Ashen Vanguard.",
            score=0.95,
        )
    )

    resolver = CitationResolver(manager)
    raw_answer = "Ser Vael was a member [EVIDENCE_001] of the Ashen Vanguard."
    resolved = resolver.resolve_citations(raw_answer)

    assert resolved == "Ser Vael was a member [Royal Annals, p.84] of the Ashen Vanguard."


def test_resolve_multiple_citations_in_bracket():
    manager = EvidenceManager()
    manager.register_evidence(
        SearchResult(
            chunk_id="c1",
            document_id="d1",
            document_title="Royal Annals",
            page_start=84,
            content="Ser Vael was a member of the Ashen Vanguard.",
            score=0.95,
        )
    )
    manager.register_evidence(
        SearchResult(
            chunk_id="c2",
            document_id="d2",
            document_title="Trial Transcript",
            page_start=7,
            content="Charges were brought against Ser Vael.",
            score=0.9,
        )
    )

    resolver = CitationResolver(manager)
    raw_answer = "Vael served the Vanguard and later faced charges [EVIDENCE_001, EVIDENCE_002]."
    resolved = resolver.resolve_citations(raw_answer)

    assert "[Royal Annals, p.84; Trial Transcript, p.7]" in resolved


def test_validate_citations_valid_and_missing():
    manager = EvidenceManager()
    manager.register_evidence(
        SearchResult(
            chunk_id="c1",
            document_id="d1",
            document_title="Royal Annals",
            page_start=84,
            content="Ser Vael was a member of the Ashen Vanguard.",
            score=0.95,
        )
    )

    resolver = CitationResolver(manager)
    valid_text = "Ser Vael was a member [EVIDENCE_001] of the Vanguard."
    issues = resolver.validate_citations(valid_text)
    assert len(issues) == 0

    invalid_text = "Ser Vael lived in Greyfell [EVIDENCE_999]."
    issues = resolver.validate_citations(invalid_text)
    assert len(issues) == 1
    assert issues[0].evidence_id == "EVIDENCE_999"
    assert issues[0].issue_type == "missing"


def test_get_citation_details():
    manager = EvidenceManager()
    rec = manager.register_evidence(
        SearchResult(
            chunk_id="c1",
            document_id="d1",
            document_title="Codex Vaeloria I",
            page_start=42,
            section_title="The Founding Era",
            content="The Ashen Vanguard was established in 312 AS.",
            score=0.92,
            source_path="Ashen_Era_Archive/codex/codex_vaeloria_i.pdf",
        )
    )

    resolver = CitationResolver(manager)
    details = resolver.get_citation_details("EVIDENCE_001")

    assert details["found"] is True
    assert details["document_title"] == "Codex Vaeloria I"
    assert details["page"] == 42
    assert details["section_title"] == "The Founding Era"
    assert details["source_category"] == "codex"
    assert "Ashen Vanguard" in details["original_text"]

    missing_details = resolver.get_citation_details("EVIDENCE_888")
    assert missing_details["found"] is False
