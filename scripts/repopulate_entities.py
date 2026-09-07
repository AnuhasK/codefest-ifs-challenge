import sys
import argparse
import logging
from pathlib import Path
from uuid import UUID

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import (
    CORPUS_PATH,
    BASE_DIR,
    GEMINI_NER_BATCH_SIZE,
    GEMINI_REL_BATCH_SIZE,
)
from src.database.postgres import get_db_connection
from src.database.neo4j_db import get_neo4j_connection
from src.models.document import Chunk, LogicalDocument
from src.providers.llm_provider import get_llm_provider
from src.ingestion.entities import (
    extract_entities_from_corpus,
    store_entities_in_neo4j,
    clear_entity_graph,
    ENTITY_CACHE_FILE,
)
from src.ingestion.relationships import (
    extract_all_relationships,
    BATCH_REL_CACHE_FILE,
)
from src.ingestion.graph_storage import (
    store_relationships_in_neo4j,
    build_cooccurrence_graph,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("repopulate_entities")


def load_chunks_from_postgres():
    """Load chunks and documents directly from PostgreSQL database."""
    chunks = []
    documents = []

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, title, source_category, source_path FROM documents;")
            for row in cur.fetchall():
                doc_id = row["id"] if isinstance(row["id"], UUID) else UUID(str(row["id"]))
                doc = LogicalDocument(
                    id=doc_id,
                    title=row["title"],
                    source_category=row["source_category"],
                    source_path=row["source_path"],
                )
                documents.append(doc)

            cur.execute("""
                SELECT id, document_id, content, contextualized_content, 
                       section_title, token_count, metadata
                FROM chunks;
            """)
            for row in cur.fetchall():
                chunk_id = row["id"] if isinstance(row["id"], UUID) else UUID(str(row["id"]))
                raw_doc_id = row.get("document_id")
                if raw_doc_id is None:
                    doc_id = None
                elif isinstance(raw_doc_id, UUID):
                    doc_id = raw_doc_id
                else:
                    try:
                        doc_id = UUID(str(raw_doc_id))
                    except Exception:
                        doc_id = None

                c = Chunk(
                    id=chunk_id,
                    document_id=doc_id,
                    content=row["content"],
                    contextualized_content=row["contextualized_content"],
                    section_title=row["section_title"],
                    token_count=row["token_count"] or 0,
                    metadata=row["metadata"] or {},
                )
                chunks.append(c)

    return documents, chunks


def main():
    parser = argparse.ArgumentParser(
        description="Overhaul entity extraction: wipe Neo4j, clear cache, re-run Gemini-primary NER & relationships."
    )
    parser.add_argument(
        "--no-wipe-neo4j",
        action="store_true",
        help="Skip wiping Neo4j graph before re-running.",
    )
    parser.add_argument(
        "--keep-entity-cache",
        action="store_true",
        help="Preserve existing data/entity_extraction_cache.json instead of wiping.",
    )
    parser.add_argument(
        "--skip-relationships",
        action="store_true",
        help="Skip relationship extraction pass.",
    )
    parser.add_argument(
        "--corpus-path",
        type=str,
        default=str(CORPUS_PATH),
        help="Path to the Ashen_Era_Archive root folder.",
    )

    args = parser.parse_args()
    corpus_root = Path(args.corpus_path).resolve()

    print("=================================================================", flush=True)
    print("   ASHEN ERA ARCHIVE: ENTITY EXTRACTION OVERHAUL (MIGRATION)   ", flush=True)
    print("=================================================================", flush=True)

    # 1. Verify Neo4j connection
    neo4j_conn = get_neo4j_connection()
    try:
        neo4j_conn.init_schema()
        print("[1/6] Neo4j connection verified and constraints initialized.", flush=True)
    except Exception as e:
        print(f"[!] Error: Could not connect to Neo4j ({e}). Make sure Neo4j is running.", flush=True)
        sys.exit(1)

    # 2. Wipe Neo4j if requested (Option A)
    if not args.no_wipe_neo4j:
        print("[2/6] Wiping Neo4j graph (MATCH (n) DETACH DELETE n)...", flush=True)
        wipe_res = clear_entity_graph(neo4j_conn)
        print(f"      Neo4j wipe status: {wipe_res.get('status')}", flush=True)
    else:
        print("[2/6] Skipping Neo4j wipe (--no-wipe-neo4j specified).", flush=True)

    # 3. Clear entity extraction cache
    if not args.keep_entity_cache:
        print("[3/6] Clearing old entity extraction cache...", flush=True)
        if ENTITY_CACHE_FILE.exists():
            ENTITY_CACHE_FILE.unlink()
            print(f"      Deleted {ENTITY_CACHE_FILE}", flush=True)
        if BATCH_REL_CACHE_FILE.exists():
            BATCH_REL_CACHE_FILE.unlink()
            print(f"      Deleted {BATCH_REL_CACHE_FILE}", flush=True)
    else:
        print("[3/6] Preserving existing entity cache (--keep-entity-cache specified).", flush=True)

    # 4. Load chunks from PostgreSQL
    print("[4/6] Fetching chunks from PostgreSQL...", flush=True)
    try:
        documents, chunks = load_chunks_from_postgres()
        print(f"      Loaded {len(chunks)} chunks across {len(documents)} documents.", flush=True)
    except Exception as e:
        print(f"[!] Error: Could not load chunks from PostgreSQL ({e}).", flush=True)
        sys.exit(1)

    if not chunks:
        print("[!] No chunks found in PostgreSQL. Please run ingestion first.", flush=True)
        sys.exit(1)

    # 5. Extract entities (Gemini-primary NER + Gazette + Deduplication)
    print(f"[5/6] Running Gemini-primary Entity Extraction (batch size: {GEMINI_NER_BATCH_SIZE})...", flush=True)
    llm = get_llm_provider()

    all_entities, chunk_to_entities = extract_entities_from_corpus(
        chunks=chunks,
        corpus_path=corpus_root,
        llm=llm,
    )
    print(f"      Extracted {len(all_entities)} canonical entity occurrences across {len(chunk_to_entities)} chunks.", flush=True)

    print("      Storing entities in Neo4j...", flush=True)
    neo_res = store_entities_in_neo4j(
        entities=all_entities,
        chunk_to_entities=chunk_to_entities,
        documents=documents,
        neo4j_conn=neo4j_conn,
    )
    print(
        f"      Entities saved: {neo_res.get('entities_saved', 0)}, "
        f"Chunk links: {neo_res.get('chunk_links', 0)}, "
        f"Doc links: {neo_res.get('doc_links', 0)}.",
        flush=True,
    )

    # 6. Extract and persist relationships
    if not args.skip_relationships:
        print(f"[6/6] Extracting domain relationships (batch size: {GEMINI_REL_BATCH_SIZE})...", flush=True)
        relationships = extract_all_relationships(
            chunks=chunks,
            chunk_to_entities=chunk_to_entities,
            llm=llm,
            corpus_path=corpus_root,
        )
        print(f"      Total relationships extracted: {len(relationships)}.", flush=True)

        print("      Persisting relationships to Neo4j...", flush=True)
        rel_res = store_relationships_in_neo4j(relationships, neo4j_conn=neo4j_conn)
        print(
            f"      Relationships saved: {rel_res.get('relationships_saved', 0)} "
            f"across types {rel_res.get('types')}.",
            flush=True,
        )

        print("      Building co-occurrence edges...", flush=True)
        cooccur_res = build_cooccurrence_graph(chunk_to_entities, neo4j_conn=neo4j_conn, min_weight=2)
        print(f"      Co-occurrence edges created: {cooccur_res.get('edges_saved', 0)}.", flush=True)
    else:
        print("[6/6] Skipping relationship extraction (--skip-relationships specified).", flush=True)

    print("\n=================================================================", flush=True)
    print("                REPOPULATION COMPLETE                           ", flush=True)
    print("=================================================================", flush=True)


if __name__ == "__main__":
    main()
