from pathlib import Path
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from pgvector.psycopg import register_vector

from src.config import (
    POSTGRES_DB,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_URL,
)


def get_connection():
    """Create and return a direct psycopg connection with pgvector registered."""
    conn = psycopg.connect(
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        row_factory=dict_row,
    )
    register_vector(conn)
    return conn


@contextmanager
def get_db_connection():
    """Context manager for managing PostgreSQL transactions safely."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_postgres():
    """Run database migration script to initialize tables."""
    migration_file = Path(__file__).parent / "migrations" / "001_initial_schema.sql"
    sql = migration_file.read_text(encoding="utf-8")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    print("PostgreSQL schema successfully initialized.")
