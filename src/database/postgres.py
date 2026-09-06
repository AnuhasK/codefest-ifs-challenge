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


def get_connection(register_vec: bool = True):
    """Create and return a direct psycopg connection with pgvector registered if available."""
    conn = psycopg.connect(
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        row_factory=dict_row,
    )
    if register_vec:
        try:
            register_vector(conn)
        except Exception:
            pass
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
    """Run database migration scripts in sequence to initialize tables, vector indexes, and FTS."""
    migrations_dir = Path(__file__).parent / "migrations"
    migration_files = sorted(migrations_dir.glob("*.sql"))

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for mf in migration_files:
                sql = mf.read_text(encoding="utf-8")
                cur.execute(sql)
                print(f"Applied migration: {mf.name}")
    print("PostgreSQL schema successfully initialized.")

