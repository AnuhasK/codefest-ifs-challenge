import sys
import argparse
import logging
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import BASE_DIR, CORPUS_PATH
from src.knowledge.graph import KnowledgeGraph
from src.ingestion.relationships import (
    extract_infobox_relationships,
    save_relationships_cache,
    load_relationships_cache,
)
from src.ingestion.graph_storage import (
    store_relationships_in_neo4j,
    build_cooccurrence_graph,
)
from src.database.postgres import get_db_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("extract_relationships")


def get_chunk_to_entities_from_neo4j(kg: KnowledgeGraph):
    """Retrieve chunk-to-entity mapping from Neo4j MENTIONED_IN edges."""
    cypher = """
    MATCH (e:Entity)-[:MENTIONED_IN]->(c:Chunk)
    RETURN c.id AS chunk_id, collect(e.name) AS entities
    """
    try:
        records = kg.neo4j.execute_query(cypher)
        return {r["chunk_id"]: r["entities"] for r in records}
    except Exception as e:
        logger.warning("Could not fetch chunk entities from Neo4j: %s", e)
        return {}


def main():
    parser = argparse.ArgumentParser(description="Extract domain relationships and sync to Neo4j.")
    parser.add_argument("--infobox-only", action="store_true", default=True,
                        help="Extract deterministic relationships from wiki infoboxes (zero API calls).")
    parser.add_argument("--sync-neo4j", action="store_true",
                        help="Persist extracted relationships into Neo4j.")
    parser.add_argument("--build-cooccurrence", action="store_true",
                        help="Build CO_OCCURS_WITH edges between co-occurring entities.")
    args = parser.parse_args()

    print("=== Phase 5 Relationship Extraction & Neo4j Sync ===", flush=True)

    # 1. Check existing cache
    cached = load_relationships_cache()
    if cached and not args.infobox_only:
        print(f"Found {len(cached)} cached relationships in data/extracted_relationships.json.", flush=True)
        relationships = cached
    else:
        print("Extracting canonical relationships from wiki infoboxes...", flush=True)
        relationships = extract_infobox_relationships(CORPUS_PATH)
        save_relationships_cache(relationships)

    print(f"Total extracted relationships: {len(relationships)}", flush=True)

    # Breakdown by type
    type_counts = {}
    for r in relationships:
        type_counts[r.relationship_type] = type_counts.get(r.relationship_type, 0) + 1
    for r_type, count in sorted(type_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {r_type}: {count}", flush=True)

    kg = KnowledgeGraph()

    # 2. Sync to Neo4j if requested
    if args.sync_neo4j:
        print("\nStoring relationships in Neo4j graph...", flush=True)
        res = store_relationships_in_neo4j(relationships, neo4j_conn=kg.neo4j)
        print(f"Neo4j sync status: {res.get('status')}, {res.get('relationships_saved', 0)} relationships saved across types {res.get('types')}.", flush=True)

    # 3. Build co-occurrence graph if requested
    if args.build_cooccurrence:
        print("\nBuilding entity co-occurrence edges in Neo4j...", flush=True)
        chunk_to_entities = get_chunk_to_entities_from_neo4j(kg)
        if chunk_to_entities:
            c_res = build_cooccurrence_graph(chunk_to_entities, neo4j_conn=kg.neo4j, min_weight=2)
            print(f"Co-occurrence sync: {c_res.get('status')}, {c_res.get('edges_saved', 0)} edges created.", flush=True)
        else:
            print("No chunk entity mappings found to compute co-occurrence.", flush=True)

    stats = kg.get_relationship_statistics()
    print("\nUpdated Neo4j Relationship Statistics:", flush=True)
    for r_type, count in stats.items():
        print(f"  - {r_type}: {count}", flush=True)


if __name__ == "__main__":
    main()
