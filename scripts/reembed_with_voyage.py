"""
Re-embed all chunks and assets using Voyage AI (voyage-3-large).

Switches the active embedding provider from Gemini to Voyage AI:
1. NULLs existing Gemini vectors in PostgreSQL (chunks + assets tables)
2. Re-embeds all chunks using VoyageEmbeddingProvider
3. Rebuilds HNSW indexes on embedding columns

The Gemini disk cache (data/embeddings_cache.sqlite) is NEVER touched.
Voyage vectors are cached in data/embeddings_cache_voyage.sqlite.
If interrupted, re-running resumes from the Voyage disk cache.

Usage:
    python scripts/reembed_with_voyage.py

Prerequisites:
    - EMBEDDING_PROVIDER=voyage in .env
    - EMBEDDING_MODEL=voyage-3-large in .env
    - VOYAGE_API_KEY set in .env
    - Docker Compose running (PostgreSQL)
"""

import sys
import time
from pathlib import Path

# Ensure project root is on the path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import POSTGRES_URL, EMBEDDING_PROVIDER, EMBEDDING_MODEL, EMBEDDING_DIMENSION
from src.database.postgres import get_connection
from src.providers.embeddings import get_embedding_provider


def null_existing_embeddings(conn) -> tuple[int, int]:
    """NULL out Gemini vectors in chunks and assets tables."""
    with conn.cursor() as cur:
        cur.execute("UPDATE chunks SET embedding = NULL, contextual_embedding = NULL")
        chunks_updated = cur.rowcount
        cur.execute("UPDATE assets SET embedding = NULL")
        assets_updated = cur.rowcount
    conn.commit()
    return chunks_updated, assets_updated


def drop_vector_indexes(conn) -> None:
    """Drop existing HNSW indexes before re-embedding (required to rebuild cleanly)."""
    indexes = [
        "chunks_embedding_idx",
        "chunks_contextual_embedding_idx",
        "assets_embedding_idx",
    ]
    with conn.cursor() as cur:
        for idx in indexes:
            cur.execute(f"DROP INDEX IF EXISTS {idx}")
    conn.commit()
    print("  Dropped existing HNSW indexes.")


def rebuild_vector_indexes(conn) -> None:
    """Rebuild HNSW indexes after re-embedding."""
    with conn.cursor() as cur:
        print("  Building chunks_embedding_idx ...", flush=True)
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks
              USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)
            """
        )
        print("  Building chunks_contextual_embedding_idx ...", flush=True)
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS chunks_contextual_embedding_idx ON chunks
              USING hnsw (contextual_embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)
            """
        )
        print("  Building assets_embedding_idx ...", flush=True)
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS assets_embedding_idx ON assets
              USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)
            """
        )
    conn.commit()
    print("  All HNSW indexes rebuilt.")


def fetch_chunks(conn) -> list[dict]:
    """Fetch all chunks (standard content + contextualized content)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, content, contextualized_content
            FROM chunks
            ORDER BY id
            """
        )
        rows = cur.fetchall()
    return [{"id": r[0], "content": r[1], "contextualized_content": r[2]} for r in rows]


def fetch_assets(conn) -> list[dict]:
    """Fetch all assets that have a description."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, description
            FROM assets
            WHERE description IS NOT NULL AND description != ''
            ORDER BY id
            """
        )
        rows = cur.fetchall()
    return [{"id": r[0], "description": r[1]} for r in rows]


def update_chunk_embeddings(conn, chunk_id: str, embedding: list[float], contextual_embedding: list[float]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE chunks SET embedding = %s::vector, contextual_embedding = %s::vector WHERE id = %s",
            (embedding, contextual_embedding, chunk_id),
        )


def update_asset_embedding(conn, asset_id: str, embedding: list[float]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE assets SET embedding = %s::vector WHERE id = %s",
            (embedding, asset_id),
        )


def main():
    print("=" * 60)
    print("Voyage AI Re-Embedding Script")
    print(f"  Provider: {EMBEDDING_PROVIDER}")
    print(f"  Model:    {EMBEDDING_MODEL}")
    print(f"  Dims:     {EMBEDDING_DIMENSION}")
    print("=" * 60)

    if EMBEDDING_PROVIDER != "voyage":
        print(f"\n[ERROR] EMBEDDING_PROVIDER is '{EMBEDDING_PROVIDER}', expected 'voyage'.")
        print("  Set EMBEDDING_PROVIDER=voyage in .env before running this script.")
        sys.exit(1)

    # Initialise provider (validates API key, creates Voyage cache DB)
    print("\n[1/5] Initialising VoyageEmbeddingProvider...")
    provider = get_embedding_provider()
    print(f"  Provider ready: {provider.__class__.__name__} ({provider.dimension} dims)")

    conn = get_connection()

    # Step 2: Drop indexes
    print("\n[2/5] Dropping existing HNSW indexes...")
    drop_vector_indexes(conn)

    # Step 3: NULL out existing Gemini vectors
    print("\n[3/5] NULLing existing embeddings in PostgreSQL...")
    chunks_nulled, assets_nulled = null_existing_embeddings(conn)
    print(f"  Nulled {chunks_nulled} chunk rows (embedding + contextual_embedding)")
    print(f"  Nulled {assets_nulled} asset rows (embedding)")
    print("  Note: data/embeddings_cache.sqlite (Gemini cache) is untouched.")

    # Step 4a: Re-embed chunks
    print("\n[4/5] Re-embedding chunks with Voyage AI...")
    chunks = fetch_chunks(conn)
    print(f"  Found {len(chunks)} chunks to embed.")

    standard_texts = [c["content"] for c in chunks]
    contextual_texts = [c["contextualized_content"] or c["content"] for c in chunks]

    print(f"\n  Generating standard embeddings ({len(standard_texts)} chunks)...")
    standard_vecs = provider.embed_texts(standard_texts, batch_size=16)

    print(f"\n  Generating contextual embeddings ({len(contextual_texts)} chunks)...")
    contextual_vecs = provider.embed_texts(contextual_texts, batch_size=16)

    print("\n  Writing chunk embeddings to PostgreSQL...")
    for i, chunk in enumerate(chunks):
        update_chunk_embeddings(conn, chunk["id"], standard_vecs[i], contextual_vecs[i])
        if (i + 1) % 100 == 0 or (i + 1) == len(chunks):
            conn.commit()
            print(f"    Committed {i + 1}/{len(chunks)} chunk embeddings...", flush=True)
    conn.commit()
    print(f"  Done. {len(chunks)} chunks re-embedded.")

    # Step 4b: Re-embed assets
    assets = fetch_assets(conn)
    if assets:
        print(f"\n  Re-embedding {len(assets)} image assets...")
        asset_texts = [a["description"] for a in assets]
        asset_vecs = provider.embed_texts(asset_texts, batch_size=16)
        for i, asset in enumerate(assets):
            update_asset_embedding(conn, asset["id"], asset_vecs[i])
        conn.commit()
        print(f"  Done. {len(assets)} assets re-embedded.")
    else:
        print("  No assets with descriptions found - skipping.")

    # Step 5: Rebuild indexes
    print("\n[5/5] Rebuilding HNSW indexes...")
    rebuild_vector_indexes(conn)

    conn.close()

    print("\n" + "=" * 60)
    print("Re-embedding complete.")
    print("  Active provider: Voyage AI (voyage-3-large)")
    print("  Voyage cache:    data/embeddings_cache_voyage.sqlite")
    print("  Gemini cache:    data/embeddings_cache.sqlite (preserved)")
    print("=" * 60)


if __name__ == "__main__":
    main()
