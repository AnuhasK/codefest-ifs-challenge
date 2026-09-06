from src.models.search import SearchResult
from src.knowledge.evidence import EvidenceManager


def test_evidence_manager_unique_ids():
    manager = EvidenceManager()
    for i in range(10):
        sr = SearchResult(
            chunk_id=f"chunk_{i}",
            document_id=f"doc_{i}",
            content=f"Content for chunk {i}",
            score=0.8,
            document_title=f"Document {i}",
            source_path=f"Ashen_Era_Archive/ephemera/letter_concerning_{i}.txt",
        )
        rec = manager.register_evidence(sr)
        expected_id = f"EVIDENCE_{i+1:03d}"
        assert rec.id == expected_id

    assert len(manager.evidence) == 10
    assert manager.evidence[0].id == "EVIDENCE_001"
    assert manager.evidence[9].id == "EVIDENCE_010"


def test_evidence_manager_deduplicate():
    manager = EvidenceManager()
    sr1 = SearchResult(
        chunk_id="chunk_duplicate",
        document_id="doc_1",
        content="First instance",
        score=0.9,
        document_title="Doc 1",
    )
    sr2 = SearchResult(
        chunk_id="chunk_duplicate",
        document_id="doc_1",
        content="Second instance with same chunk_id",
        score=0.7,
        document_title="Doc 1",
    )
    sr3 = SearchResult(
        chunk_id="chunk_unique",
        document_id="doc_2",
        content="Unique chunk",
        score=0.8,
        document_title="Doc 2",
    )

    manager.register_evidence(sr1)
    manager.register_evidence(sr2)
    manager.register_evidence(sr3)

    assert len(manager.evidence) == 3
    manager.deduplicate()
    assert len(manager.evidence) == 2
    assert manager.evidence[0].chunk_id == "chunk_duplicate"
    assert manager.evidence[0].content == "First instance"
    assert manager.evidence[1].chunk_id == "chunk_unique"


def test_evidence_manager_group_by_entity():
    manager = EvidenceManager()
    manager.register_evidence(
        SearchResult(
            chunk_id="c1",
            document_id="d1",
            content="Ser Vael rode into Red Vale to meet the Ashen Vanguard.",
            score=0.9,
        )
    )
    manager.register_evidence(
        SearchResult(
            chunk_id="c2",
            document_id="d2",
            content="Ederon Fellgard commanded the fortress of Greyfell Citadel.",
            score=0.85,
        )
    )
    manager.register_evidence(
        SearchResult(
            chunk_id="c3",
            document_id="d3",
            content="Ser Vael spoke with Ederon Fellgard during the accord.",
            score=0.8,
        )
    )

    groups = manager.group_by_entity(["Ser Vael", "Ederon Fellgard", "Pale Covenant"])
    assert len(groups["Ser Vael"]) == 2
    assert len(groups["Ederon Fellgard"]) == 2
    assert len(groups["Pale Covenant"]) == 0


def test_evidence_manager_group_by_document():
    manager = EvidenceManager()
    manager.register_evidence(
        SearchResult(
            chunk_id="c1",
            document_id="doc_a",
            document_title="The Kindling Years",
            content="Chapter 1 content",
            score=0.9,
        )
    )
    manager.register_evidence(
        SearchResult(
            chunk_id="c2",
            document_id="doc_a",
            document_title="The Kindling Years",
            content="Chapter 2 content",
            score=0.8,
        )
    )
    manager.register_evidence(
        SearchResult(
            chunk_id="c3",
            document_id="doc_b",
            document_title="Codex Vaeloria",
            content="Codex entry content",
            score=0.85,
        )
    )

    doc_groups = manager.group_by_document()
    assert len(doc_groups) == 2
    assert len(doc_groups["The Kindling Years"]) == 2
    assert len(doc_groups["Codex Vaeloria"]) == 1


def test_evidence_manager_source_diversity():
    manager = EvidenceManager()
    manager.register_evidence(
        SearchResult(
            chunk_id="c1",
            document_id="d1",
            document_title="Doc 1",
            content="Text 1",
            score=0.9,
            source_path="Ashen_Era_Archive/ephemera/ballad_concerning_vael.docx",
        )
    )
    manager.register_evidence(
        SearchResult(
            chunk_id="c2",
            document_id="d2",
            document_title="Doc 2",
            content="Text 2",
            score=0.85,
            source_path="Ashen_Era_Archive/codex/codex_vaeloria_i.pdf",
        )
    )

    diversity = manager.get_source_diversity()
    assert diversity["total_records"] == 2
    assert diversity["unique_documents"] == 2
    assert diversity["unique_categories"] == 2
    assert "ballad" in diversity["subtypes"]
    assert "codex_entry" in diversity["subtypes"]


def test_evidence_manager_get_evidence_by_id():
    manager = EvidenceManager()
    rec1 = manager.register_evidence(
        SearchResult(
            chunk_id="c1",
            document_id="d1",
            document_title="Doc 1",
            content="Text 1",
            score=0.9,
        )
    )
    rec2 = manager.register_evidence(
        SearchResult(
            chunk_id="c2",
            document_id="d2",
            document_title="Doc 2",
            content="Text 2",
            score=0.8,
        )
    )

    assert manager.get_evidence_by_id("EVIDENCE_001") == rec1
    assert manager.get_evidence_by_id("EVIDENCE_1") == rec1
    assert manager.get_evidence_by_id("[EVIDENCE_002]") == rec2
    assert manager.get_evidence_by_id("EVIDENCE_999") is None
