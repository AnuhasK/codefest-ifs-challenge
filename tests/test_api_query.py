from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from src.api.main import app
from src.models.evidence import FinalAnswer, EvidenceRecord, Conflict
from src.models.search import SearchResult

client = TestClient(app)


def test_query_validation_error():
    # Empty question should fail validation (422)
    response = client.post("/query", json={"question": ""})
    assert response.status_code == 422


def test_query_endpoint_success():
    mock_ev = EvidenceRecord(
        id="EVIDENCE_001",
        chunk_id="c1",
        document_id="d1",
        content="The Ashen Citadel fell in the year 412 of the Third Era.",
        source_category="chronicles",
        source_subtype="chronicle_entry",
        page=42,
        document_title="Chronicles of the Vale",
        score=0.92,
        metadata={},
    )
    mock_final = FinalAnswer(
        question="When did the Ashen Citadel fall?",
        answer_text="The Ashen Citadel fell in 412 of the Third Era [Chronicles of the Vale, p.42].",
        raw_answer_text="The Ashen Citadel fell in 412 of the Third Era [EVIDENCE_001].",
        evidence=[mock_ev],
        citations=[{
            "evidence_id": "EVIDENCE_001",
            "document_title": "Chronicles of the Vale",
            "page": 42,
            "excerpt": "The Ashen Citadel fell in the year 412...",
            "source_path": "chronicles/vale.pdf",
        }],
        conflicts=[],
        evidence_status="HIGH",
        query_trace={"query_type": "simple", "retrieval_hops": 1},
    )

    with patch("src.api.routes.query.answer_question", return_value=mock_final):
        response = client.post("/query", json={
            "question": "When did the Ashen Citadel fall?",
            "max_hops": 2,
            "top_k": 10,
            "include_trace": True,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["question"] == "When did the Ashen Citadel fall?"
        assert "[Chronicles of the Vale, p.42]" in data["answer"]
        assert len(data["citations"]) == 1
        assert data["citations"][0]["document_title"] == "Chronicles of the Vale"
        assert data["citations"][0]["page"] == 42
        assert len(data["evidence"]) == 1
        assert data["evidence"][0]["id"] == "EVIDENCE_001"
        assert data["evidence_status"] == "HIGH"
        assert data["trace"] is not None
        assert data["trace"]["retrieval_hops"] == 1


def test_query_endpoint_track1a_image_evidence():
    """Track 1A: asset references populated when evidence chunk includes asset_id."""
    mock_ev = EvidenceRecord(
        id="EVIDENCE_001",
        chunk_id="c_asset_1",
        document_id="d_asset_1",
        content="Figure plate for Marrowwatch: Garrison strength 3,107.",
        source_category="codex",
        source_subtype="codex_entry",
        page=1,
        document_title="Codex Vaeloria",
        score=0.98,
        metadata={
            "is_asset_chunk": True,
            "asset_id": "6a79eee8-f19c-4559-83f0-0a97e765eed9",
            "asset_type": "figure_plate",
            "entity_name": "Marrowwatch",
            "file_path": "plate_00_location_marrowwatch.png",
            "extracted_data": {"RECORDEDGARRISONSTRENGTH": 3107},
        },
    )
    mock_final = FinalAnswer(
        question="What is the garrison strength of Marrowwatch?",
        answer_text="Marrowwatch has a recorded garrison strength of 3,107 souls under arms [Codex Vaeloria, p.1].",
        evidence=[mock_ev],
        citations=[{
            "evidence_id": "EVIDENCE_001",
            "document_title": "Codex Vaeloria",
            "page": 1,
            "excerpt": "Garrison strength 3,107",
        }],
        conflicts=[],
        evidence_status="HIGH",
    )

    with patch("src.api.routes.query.answer_question", return_value=mock_final):
        response = client.post("/query", json={"question": "What is the garrison strength of Marrowwatch?"})
        assert response.status_code == 200
        data = response.json()
        assert len(data["asset_references"]) == 1
        asset = data["asset_references"][0]
        assert asset["asset_id"] == "6a79eee8-f19c-4559-83f0-0a97e765eed9"
        assert asset["asset_type"] == "figure_plate"
        assert asset["entity_name"] == "Marrowwatch"
        assert asset["image_url"] == "/assets/6a79eee8-f19c-4559-83f0-0a97e765eed9/image"
        extracted = asset["extracted_data"]
        val = (
            extracted.get("RECORDEDGARRISONSTRENGTH")
            or extracted.get("all_metrics", {}).get("RECORDEDGARRISONSTRENGTH")
            or extracted.get("numerical_value")
        )
        assert val == 3107


def test_query_endpoint_insufficient_evidence():
    mock_final = FinalAnswer(
        question="What was the captain's interstellar warp drive speed?",
        answer_text="Based on the provided archive evidence, there is insufficient information to answer this question.",
        evidence=[],
        citations=[],
        conflicts=[],
        evidence_status="INSUFFICIENT",
    )

    with patch("src.api.routes.query.answer_question", return_value=mock_final):
        response = client.post("/query", json={"question": "What was the captain's interstellar warp drive speed?"})
        assert response.status_code == 200
        data = response.json()
        assert data["evidence_status"] == "INSUFFICIENT"
        assert "insufficient information" in data["answer"].lower()
