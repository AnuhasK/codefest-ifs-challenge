from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from src.api.main import app
from src.api.schemas import DatabaseStatus

client = TestClient(app)


def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "tracks_supported" in data
    assert response.headers.get("X-Process-Time") is not None
    assert response.headers.get("X-Request-ID") is not None


def test_health_endpoint_healthy():
    mock_db_status = DatabaseStatus(postgres="connected", neo4j="connected")
    with patch("src.api.routes.health.check_database_connections", return_value=mock_db_status):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["databases"]["postgres"] == "connected"
        assert data["databases"]["neo4j"] == "connected"
        assert "timestamp" in data


def test_health_endpoint_degraded():
    mock_db_status = DatabaseStatus(postgres="connected", neo4j="error: connection refused")
    with patch("src.api.routes.health.check_database_connections", return_value=mock_db_status):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["databases"]["postgres"] == "connected"
        assert "error" in data["databases"]["neo4j"]


def test_health_endpoint_unhealthy():
    mock_db_status = DatabaseStatus(postgres="error: timeout", neo4j="error: connection refused")
    with patch("src.api.routes.health.check_database_connections", return_value=mock_db_status):
        response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
