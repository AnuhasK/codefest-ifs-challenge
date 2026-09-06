import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Response, status

from src.api.schemas import HealthResponse, DatabaseStatus
from src.database.postgres import get_db_connection
from src.database.neo4j_db import get_neo4j_connection

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


def check_database_connections() -> DatabaseStatus:
    """Validate connectivity to PostgreSQL and Neo4j databases."""
    db_status = DatabaseStatus()
    details = {}

    # 1. Check PostgreSQL
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS alive;")
                row = cur.fetchone()
                if row and row.get("alive") == 1:
                    db_status.postgres = "connected"
                else:
                    db_status.postgres = "unexpected_response"
    except Exception as exc:
        logger.warning(f"PostgreSQL health check failed: {exc}")
        db_status.postgres = f"error: {str(exc)[:100]}"
        details["postgres_error"] = str(exc)

    # 2. Check Neo4j
    try:
        neo4j_conn = get_neo4j_connection()
        res = neo4j_conn.execute_query("RETURN 1 AS alive;")
        if res and res[0].get("alive") == 1:
            db_status.neo4j = "connected"
        else:
            db_status.neo4j = "unexpected_response"
    except Exception as exc:
        logger.warning(f"Neo4j health check failed: {exc}")
        db_status.neo4j = f"error: {str(exc)[:100]}"
        details["neo4j_error"] = str(exc)

    if details:
        db_status.details = details

    return db_status


@router.get("/health", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    """
    Health check endpoint verifying database connectivity.
    Returns:
        - 200 OK when both PostgreSQL and Neo4j are connected
        - 503 SERVICE_UNAVAILABLE or 207 MULTI-STATUS / degraded when databases are unreachable
    """
    databases = check_database_connections()
    timestamp = datetime.now(timezone.utc).isoformat()

    if databases.postgres == "connected" and databases.neo4j == "connected":
        overall_status = "healthy"
    elif databases.postgres == "connected" or databases.neo4j == "connected":
        overall_status = "degraded"
        response.status_code = status.HTTP_200_OK
    else:
        overall_status = "unhealthy"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status=overall_status,
        timestamp=timestamp,
        databases=databases,
    )
