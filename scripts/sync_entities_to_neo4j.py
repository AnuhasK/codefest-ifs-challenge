import sys
import argparse
import spacy
from pathlib import Path
from uuid import UUID

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import CORPUS_PATH
from src.database.postgres import get_db_connection
from src.database.neo4j_db import get_neo4j_connection
from src.models.document import Chunk, LogicalDocument
from src.ingestion.entities import extract_entities_from_corpus
from src.ingestion.graph_storage import store_entities_in_neo4j


def load_chunks_from_postgres():
    """Load chunks and documents directly from PostgreSQL database."""
    chunks = []
    documents = []
    doc_ids_seen = set()

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
                doc_ids_seen.add(str(doc.id))

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
        description="Sync extracted entities and relations to Neo4j without regenerating embeddings."
    )
    parser.add_argument(
        "--corpus-path",
        type=str,
        default=str(CORPUS_PATH),
        help="Path to the Ashen_Era_Archive root folder.",
    )
    parser.add_argument(
        "--from-postgres",
        action="store_true",
        default=True,
        help="Load chunks and documents from PostgreSQL (default: True).",
    )

    args = parser.parse_args()
    corpus_root = Path(args.corpus_path).resolve()

    print(f"=== Syncing Entities to Neo4j ===", flush=True)

    # 1. Check Neo4j connection
    neo4j_conn = get_neo4j_connection()
    try:
        neo4j_conn.init_schema()
        print("Connected to Neo4j and verified constraints.", flush=True)
    except Exception as e:
        print(f"Error: Could not connect to Neo4j ({e}). Please make sure Neo4j container is running.", flush=True)
        sys.exit(1)

    # 2. Retrieve chunks from PostgreSQL or corpus
    print("Fetching chunks from PostgreSQL...", flush=True)
    try:
        documents, chunks = load_chunks_from_postgres()
        print(f"Loaded {len(chunks)} chunks across {len(documents)} documents from PostgreSQL.", flush=True)
    except Exception as e:
        print(f"Could not load chunks from PostgreSQL ({e}). Please ensure ingestion has been run.", flush=True)
        sys.exit(1)

    if not chunks:
        print("No chunks found in database. Please run scripts/ingest.py first.", flush=True)
        sys.exit(1)

    # 3. Extract entities (Gazette + spaCy + Cache)
    print("Extracting entities (using gazette, regex rules, and disk cache)...", flush=True)
    try:
        nlp = spacy.load("en_core_web_sm")
    except OSError:
        print("spaCy model 'en_core_web_sm' not found. Run: python -m spacy download en_core_web_sm", flush=True)
        sys.exit(1)

    all_entities, chunk_to_entities = extract_entities_from_corpus(
        chunks=chunks,
        corpus_path=corpus_root,
        llm=None,  # fast sync with cached/rule-based NER
        nlp=nlp,
    )
    print(f"Extracted {len(all_entities)} entity occurrences across {len(chunk_to_entities)} chunks.", flush=True)

    # 4. Store in Neo4j
    print("Persisting entities and relations to Neo4j...", flush=True)
    res = store_entities_in_neo4j(
        entities=all_entities,
        chunk_to_entities=chunk_to_entities,
        documents=documents,
        neo4j_conn=neo4j_conn,
    )

    print("\n=== Neo4j Entity Synchronization Summary ===")
    print(f"Status:             {res.get('status')}")
    print(f"Unique Entities:    {res.get('entities_saved', 0)}")
    print(f"MENTIONED_IN Edges: {res.get('chunk_links', 0)}")
    print(f"APPEARS_IN Edges:   {res.get('doc_links', 0)}")
    print("==============================================")


if __name__ == "__main__":
    main()
