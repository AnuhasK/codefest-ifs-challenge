from fastapi.testclient import TestClient
from unittest.mock import patch

from src.api.main import app
from src.models.search import SearchResult

client = TestClient(app)


def make_mock_search_results(n: int = 2):
    return [
        SearchResult(
            chunk_id=f"c{i}",
            document_id=f"d{i}",
            document_title=f"Archive Volume {i}",
            source_category="chronicles",
            page_start=10 * i,
            section_title=f"Chapter {i}",
            content=f"Historic narrative excerpt {i} describing events.",
            score=0.95 - (i * 0.1),
        )
        for i in range(1, n + 1)
    ]


def test_search_validation_error():
    response = client.post("/search", json={"query": ""})
    assert response.status_code == 422


def test_search_unsupported_type():
    response = client.post("/search", json={"query": "test query", "search_type": "invalid_type"})
    assert response.status_code == 400
    assert "Unsupported search_type" in response.json()["detail"]


def test_search_hybrid():
    mock_res = make_mock_search_results(2)
    with patch("src.api.routes.search.retrieve", return_value=mock_res):
        response = client.post("/search", json={"query": "Marrowwatch siege", "search_type": "hybrid", "top_k": 5})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["search_type"] == "hybrid"
        assert len(data["results"]) == 2
        assert data["results"][0]["document_title"] == "Archive Volume 1"
        assert data["results"][0]["score"] == 0.85


def test_search_bm25():
    mock_res = make_mock_search_results(1)
    with patch("src.api.routes.search.bm25_search", return_value=mock_res):
        response = client.post("/search", json={"query": "Isolde", "search_type": "bm25", "top_k": 10})
        assert response.status_code == 200
        data = response.json()
        assert data["search_type"] == "bm25"
        assert data["total"] == 1


def test_search_dense():
    mock_res = make_mock_search_results(2)
    with patch("src.api.routes.search.dense_search", return_value=mock_res):
        response = client.post("/search", json={"query": "Emberdeep garrison", "search_type": "dense"})
        assert response.status_code == 200
        data = response.json()
        assert data["search_type"] == "dense"
        assert data["total"] == 2


def test_search_entity():
    mock_res = make_mock_search_results(1)
    with patch("src.api.routes.search.entity_search", return_value=mock_res):
        response = client.post("/search", json={"query": "Arch-Prelate", "search_type": "entity"})
        assert response.status_code == 200
        data = response.json()
        assert data["search_type"] == "entity"
        assert data["total"] == 1
