import logging
import uuid
from typing import Dict, List, Optional, Any, Set
from collections import defaultdict

from src.database.neo4j_db import get_neo4j_connection, Neo4jConnection
from src.ingestion.entities import ExtractedEntity
from src.models.document import LogicalDocument

logger = logging.getLogger(__name__)

# Source priority ordering (higher score wins for primary source attribution)
SOURCE_PRIORITY = {
    "gazette": 3,
    "gemini_ner": 2,
    "rules": 1,
    "unknown": 0,
}


def generate_entity_id(name: str, entity_type: str = "") -> str:
    """Generate deterministic UUID5 string for an entity given canonical name and type."""
    normalized_name = name.strip().lower()
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"ashen_era:entity:{normalized_name}"))


def store_entities_in_neo4j(
    entities: List[ExtractedEntity],
    chunk_to_entities: Optional[Dict[str, List[ExtractedEntity]]] = None,
    documents: Optional[List[LogicalDocument]] = None,
    neo4j_conn: Optional[Neo4jConnection] = None,
    batch_size: int = 500,
) -> Dict[str, Any]:
    """
    Persist extracted and alias-resolved entities into Neo4j graph database.

    1. Ensures schema constraints exist in Neo4j.
    2. Aggregates chunk-level ExtractedEntity instances into canonical Entity records:
       - id: deterministic UUID5 based on normalized name
       - name: canonical entity name
       - type: entity type from the 15-type Ashen Era ontology
       - aliases: list of distinct surface forms / mention strings
       - source: primary source ('gazette', 'gemini_ner', or 'rules')
       - confidence: maximum confidence score across mentions
       - mention_count: total occurrences across the corpus
    3. Batched Cypher creation of (e:Entity) nodes.
    4. Batched creation of (e)-[:MENTIONED_IN]->(c:Chunk) edges.
    5. Batched creation of (e)-[:APPEARS_IN]->(d:Document) edges.

    Returns:
        Dict with summary counts: entities_saved, chunk_links, doc_links, status.
    """
    if not entities:
        logger.info("No entities provided to store in Neo4j.")
        return {"entities_saved": 0, "chunk_links": 0, "doc_links": 0, "status": "empty"}

    conn = neo4j_conn or get_neo4j_connection()

    # Verify Neo4j connectivity before proceeding
    try:
        conn.init_schema()
    except Exception as e:
        logger.warning("Failed to connect to Neo4j (%s). Skipping Neo4j entity persistence.", e)
        return {"entities_saved": 0, "chunk_links": 0, "doc_links": 0, "status": "connection_failed", "error": str(e)}

    # 1. Aggregate extracted entities by canonical entity key (name.lower(), entity_type)
    canonical_map: Dict[str, Dict[str, Any]] = {}
    chunk_links: Set[tuple[str, str]] = set()  # (entity_id, chunk_id)
    doc_links: Set[tuple[str, str]] = set()    # (entity_id, doc_id)

    for ent in entities:
        if not ent.name or not ent.name.strip():
            continue

        ent_id = generate_entity_id(ent.name, ent.entity_type)

        if ent_id not in canonical_map:
            canonical_map[ent_id] = {
                "id": ent_id,
                "name": ent.name.strip(),
                "type": ent.entity_type or "UNKNOWN",
                "aliases": set(),
                "source": ent.source or "unknown",
                "source_priority": SOURCE_PRIORITY.get(ent.source, 0),
                "confidence": ent.confidence if ent.confidence is not None else 1.0,
                "mention_count": 0,
            }

        rec = canonical_map[ent_id]
        rec["mention_count"] += 1

        # Track aliases / mentions
        rec["aliases"].add(ent.name.strip())
        if ent.mentions:
            for m in ent.mentions:
                if m and m.strip():
                    rec["aliases"].add(m.strip())

        # Update source if higher priority
        cur_prio = SOURCE_PRIORITY.get(ent.source, 0)
        if cur_prio > rec["source_priority"]:
            rec["source"] = ent.source
            rec["source_priority"] = cur_prio

        # Max confidence
        if ent.confidence is not None and ent.confidence > rec["confidence"]:
            rec["confidence"] = ent.confidence

        # Record chunk link
        if ent.chunk_id:
            chunk_links.add((ent_id, str(ent.chunk_id)))

        # Record document link
        if ent.document_id:
            doc_links.add((ent_id, str(ent.document_id)))

    # Prepare serialized entity records
    entity_records = []
    for rec in canonical_map.values():
        entity_records.append({
            "id": rec["id"],
            "name": rec["name"],
            "type": rec["type"],
            "aliases": sorted(list(rec["aliases"])),
            "source": rec["source"],
            "confidence": float(rec["confidence"]),
            "mention_count": int(rec["mention_count"]),
        })

    serialized_chunk_links = [{"entity_id": eid, "chunk_id": cid} for eid, cid in chunk_links]
    serialized_doc_links = [{"entity_id": eid, "doc_id": did} for eid, did in doc_links]

    logger.info(
        "Persisting to Neo4j: %d unique entities, %d chunk edges, %d document edges...",
        len(entity_records), len(serialized_chunk_links), len(serialized_doc_links),
    )

    try:
        # 2. Batch write Entity nodes
        cypher_entities = """
        UNWIND $batch AS item
        MERGE (e:Entity {id: item.id})
        SET e.name = item.name,
            e.type = item.type,
            e.aliases = item.aliases,
            e.source = item.source,
            e.confidence = item.confidence,
            e.mention_count = item.mention_count
        """
        for i in range(0, len(entity_records), batch_size):
            batch = entity_records[i : i + batch_size]
            conn.execute_write(cypher_entities, {"batch": batch})

        # 3. Batch write Chunk nodes & MENTIONED_IN edges
        cypher_chunks = """
        UNWIND $batch AS item
        MERGE (c:Chunk {id: item.chunk_id})
        WITH c, item
        MATCH (e:Entity {id: item.entity_id})
        MERGE (e)-[:MENTIONED_IN]->(c)
        """
        for i in range(0, len(serialized_chunk_links), batch_size):
            batch = serialized_chunk_links[i : i + batch_size]
            conn.execute_write(cypher_chunks, {"batch": batch})

        # 4. Batch write Document nodes & APPEARS_IN edges
        cypher_docs = """
        UNWIND $batch AS item
        MERGE (d:Document {id: item.doc_id})
        WITH d, item
        MATCH (e:Entity {id: item.entity_id})
        MERGE (e)-[:APPEARS_IN]->(d)
        """
        for i in range(0, len(serialized_doc_links), batch_size):
            batch = serialized_doc_links[i : i + batch_size]
            conn.execute_write(cypher_docs, {"batch": batch})

        logger.info(
            "Successfully stored %d entities, %d MENTIONED_IN edges, %d APPEARS_IN edges in Neo4j.",
            len(entity_records), len(serialized_chunk_links), len(serialized_doc_links),
        )
        return {
            "entities_saved": len(entity_records),
            "chunk_links": len(serialized_chunk_links),
            "doc_links": len(serialized_doc_links),
            "status": "success",
        }

    except Exception as e:
        logger.error("Error storing entities in Neo4j: %s", e)
        return {
            "entities_saved": 0,
            "chunk_links": 0,
            "doc_links": 0,
            "status": "error",
            "error": str(e),
        }
