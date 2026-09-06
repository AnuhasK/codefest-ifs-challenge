import tempfile
from pathlib import Path
from uuid import uuid4
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from src.api.main import app

client = TestClient(app)


def test_list_documents_mocked():
    mock_rows = [
        {"id": uuid4(), "title": "Chronicles of the Vale", "source_category": "chronicles", "source_path": "chronicles/vale.pdf", "chunk_count": 15},
        {"id": uuid4(), "title": "Codex Vaeloria", "source_category": "codex", "source_path": "codex/codex.pdf", "chunk_count": 28},
    ]

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = mock_rows
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("src.api.routes.documents.get_db_connection") as mock_get_db:
        mock_get_db.return_value.__enter__.return_value = mock_conn
        response = client.get("/documents")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["title"] == "Chronicles of the Vale"
        assert data[0]["chunk_count"] == 15


def test_get_document_detail_success():
    doc_id = uuid4()
    mock_doc = {
        "id": doc_id,
        "title": "Annals of Mournthrone",
        "source_category": "chronicles",
        "source_path": "annals.docx",
        "metadata": {"author": "Unknown Monk"},
        "chunk_count": 8,
    }
    mock_sections = [
        {"id": uuid4(), "title": "Prologue", "level": 1, "position": 0},
        {"id": uuid4(), "title": "The Siege", "level": 1, "position": 1},
    ]

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = mock_doc
    mock_cur.fetchall.return_value = mock_sections
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("src.api.routes.documents.get_db_connection") as mock_get_db:
        mock_get_db.return_value.__enter__.return_value = mock_conn
        response = client.get(f"/documents/{doc_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(doc_id)
        assert data["title"] == "Annals of Mournthrone"
        assert len(data["sections"]) == 2


def test_get_document_invalid_uuid():
    response = client.get("/documents/invalid-uuid-string")
    assert response.status_code == 400


def test_get_document_chunks():
    doc_id = uuid4()
    mock_chunks = [
        {
            "id": uuid4(),
            "document_id": doc_id,
            "content": "Paragraph 1 describing the gates.",
            "page_start": 1,
            "page_end": 1,
            "section_title": "Gates",
            "position": 0,
            "metadata": {},
        }
    ]

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = mock_chunks
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("src.api.routes.documents.get_db_connection") as mock_get_db:
        mock_get_db.return_value.__enter__.return_value = mock_conn
        response = client.get(f"/documents/{doc_id}/chunks")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert "Paragraph 1" in data[0]["content"]


def test_list_entities():
    mock_entities = [
        {"id": "e1", "name": "Isolde Mournvale", "entity_type": "PERSON", "aliases": ["The Pale Lady"], "mention_count": 45, "confidence": 0.98},
        {"id": "e2", "name": "Marrowwatch", "entity_type": "LOCATION", "aliases": [], "mention_count": 30, "confidence": 0.95},
    ]

    mock_neo4j = MagicMock()
    mock_neo4j.execute_query.return_value = mock_entities

    with patch("src.api.routes.documents.get_neo4j_connection", return_value=mock_neo4j):
        response = client.get("/entities?entity_type=PERSON")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["name"] == "Isolde Mournvale"
        assert data[0]["mention_count"] == 45


def test_get_entity_detail():
    entity_data = [{"id": "e1", "name": "Isolde Mournvale", "entity_type": "PERSON", "aliases": ["The Pale Lady"], "mention_count": 45, "confidence": 0.98}]
    relationships = [{"rel_type": "MEMBER_OF", "target_id": "f1", "target_name": "Silent Choir", "target_type": "FACTION"}]
    chunks = [{"chunk_id": "c1"}, {"chunk_id": "c2"}]

    mock_neo4j = MagicMock()
    mock_neo4j.execute_query.side_effect = [entity_data, relationships, chunks]

    with patch("src.api.routes.documents.get_neo4j_connection", return_value=mock_neo4j):
        response = client.get("/entities/e1")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Isolde Mournvale"
        assert len(data["relationships"]) == 1
        assert data["relationships"][0]["rel_type"] == "MEMBER_OF"
        assert len(data["sample_chunk_ids"]) == 2


def test_get_asset_image():
    asset_id = uuid4()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        tmp_path = tmp.name

    try:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {"file_path": tmp_path, "asset_type": "figure_plate"}
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        with patch("src.api.routes.documents.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            response = client.get(f"/assets/{asset_id}/image")
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/png"
            assert len(response.content) > 0
    finally:
        Path(tmp_path).unlink(missing_ok=True)
