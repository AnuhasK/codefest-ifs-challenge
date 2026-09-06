import logging
import re
from typing import Any, Dict, List, Optional
from src.database.neo4j_db import get_neo4j_connection, Neo4jConnection
from src.models.entity import Entity

logger = logging.getLogger(__name__)

VALID_REL_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class KnowledgeGraph:
    """High-level operations interface for the Ashen Era Neo4j knowledge graph."""

    def __init__(self, neo4j: Optional[Neo4jConnection] = None):
        self.neo4j: Neo4jConnection = neo4j or get_neo4j_connection()

    def create_entity(self, entity: Entity) -> None:
        """Create or merge an entity node in Neo4j."""
        cypher = """
        MERGE (e:Entity {id: $id})
        SET e.name = $name,
            e.type = $entity_type,
            e.aliases = $aliases,
            e.source = $source,
            e.confidence = $confidence,
            e.mention_count = $mention_count
        """
        params = {
            "id": entity.id,
            "name": entity.name,
            "entity_type": entity.entity_type,
            "aliases": entity.aliases or [entity.name],
            "source": entity.source,
            "confidence": float(entity.confidence),
            "mention_count": int(entity.mention_count),
        }
        self.neo4j.execute_write(cypher, params)

    def create_relationship(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Create a directed relationship between two entities.
        Example rel_types: MEMBER_OF, LED, PARTICIPATED_IN, OCCURRED_AT, LOCATED_IN
        """
        clean_rel = rel_type.strip().upper()
        if not VALID_REL_PATTERN.match(clean_rel):
            raise ValueError(f"Invalid relationship type '{rel_type}'. Must be uppercase alphanumeric/underscore.")

        props = properties or {}
        cypher = f"""
        MATCH (s:Entity {{id: $source_id}})
        MATCH (t:Entity {{id: $target_id}})
        MERGE (s)-[r:{clean_rel}]->(t)
        SET r += $props
        """
        self.neo4j.execute_write(cypher, {"source_id": source_id, "target_id": target_id, "props": props})

    def link_entity_to_chunk(
        self,
        entity_id: str,
        chunk_id: str,
        document_id: Optional[str] = None,
    ) -> None:
        """Create MENTIONED_IN edge to Chunk and optionally APPEARS_IN edge to Document."""
        cypher = """
        MATCH (e:Entity {id: $entity_id})
        MERGE (c:Chunk {id: $chunk_id})
        MERGE (e)-[:MENTIONED_IN]->(c)
        WITH e
        WHERE $document_id IS NOT NULL
        MERGE (d:Document {id: $document_id})
        MERGE (e)-[:APPEARS_IN]->(d)
        """
        self.neo4j.execute_write(
            cypher,
            {"entity_id": entity_id, "chunk_id": chunk_id, "document_id": document_id},
        )

    def find_entity(self, name: str) -> Optional[Entity]:
        """
        Find an entity by canonical name or alias.
        Matches exact canonical name, lowercase canonical name, or any alias.
        """
        if not name or not name.strip():
            return None

        clean_name = name.strip()
        cypher = """
        MATCH (e:Entity)
        WHERE toLower(e.name) = toLower($name)
           OR ANY(a IN e.aliases WHERE toLower(a) = toLower($name))
        RETURN e.id AS id,
               e.name AS name,
               e.type AS entity_type,
               e.aliases AS aliases,
               e.source AS source,
               e.confidence AS confidence,
               e.mention_count AS mention_count
        ORDER BY
          CASE WHEN toLower(e.name) = toLower($name) THEN 0 ELSE 1 END,
          e.mention_count DESC
        LIMIT 1
        """
        records = self.neo4j.execute_query(cypher, {"name": clean_name})
        if not records:
            return None

        r = records[0]
        return Entity(
            id=r["id"],
            name=r["name"],
            entity_type=r.get("entity_type") or "UNKNOWN",
            aliases=r.get("aliases") or [],
            source=r.get("source") or "unknown",
            confidence=float(r.get("confidence") or 1.0),
            mention_count=int(r.get("mention_count") or 1),
        )

    def find_entities_by_type(self, entity_type: str) -> List[Entity]:
        """Find all entities matching a specific ontology type."""
        cypher = """
        MATCH (e:Entity)
        WHERE toLower(e.type) = toLower($type)
        RETURN e.id AS id,
               e.name AS name,
               e.type AS entity_type,
               e.aliases AS aliases,
               e.source AS source,
               e.confidence AS confidence,
               e.mention_count AS mention_count
        ORDER BY e.mention_count DESC
        """
        records = self.neo4j.execute_query(cypher, {"type": entity_type.strip()})
        entities = []
        for r in records:
            entities.append(
                Entity(
                    id=r["id"],
                    name=r["name"],
                    entity_type=r.get("entity_type") or entity_type.upper(),
                    aliases=r.get("aliases") or [],
                    source=r.get("source") or "unknown",
                    confidence=float(r.get("confidence") or 1.0),
                    mention_count=int(r.get("mention_count") or 1),
                )
            )
        return entities

    def get_entity_chunks(self, entity_id: str) -> List[str]:
        """Get all chunk IDs where an entity is mentioned."""
        cypher = """
        MATCH (e:Entity {id: $entity_id})-[:MENTIONED_IN]->(c:Chunk)
        RETURN c.id AS chunk_id
        """
        records = self.neo4j.execute_query(cypher, {"entity_id": entity_id})
        return [r["chunk_id"] for r in records if "chunk_id" in r]

    def get_related_entities(
        self,
        entity_id: str,
        relationship_types: Optional[List[str]] = None,
    ) -> List[Entity]:
        """Get entities connected to a given entity via domain relationships."""
        if relationship_types:
            clean_types = [t.strip().upper() for t in relationship_types if VALID_REL_PATTERN.match(t.strip().upper())]
            rel_filter = ":" + "|:".join(clean_types) if clean_types else ""
        else:
            rel_filter = ""

        cypher = f"""
        MATCH (e:Entity {{id: $entity_id}})-[r{rel_filter}]-(target:Entity)
        WHERE type(r) <> 'MENTIONED_IN' AND type(r) <> 'APPEARS_IN'
        RETURN DISTINCT target.id AS id,
                        target.name AS name,
                        target.type AS entity_type,
                        target.aliases AS aliases,
                        target.source AS source,
                        target.confidence AS confidence,
                        target.mention_count AS mention_count
        ORDER BY target.mention_count DESC
        """
        records = self.neo4j.execute_query(cypher, {"entity_id": entity_id})
        return [
            Entity(
                id=r["id"],
                name=r["name"],
                entity_type=r.get("entity_type") or "UNKNOWN",
                aliases=r.get("aliases") or [],
                source=r.get("source") or "unknown",
                confidence=float(r.get("confidence") or 1.0),
                mention_count=int(r.get("mention_count") or 1),
            )
            for r in records
        ]

    def get_entity_statistics(self) -> Dict[str, Any]:
        """Collect global statistics on graph entities, types, and edge counts."""
        stats: Dict[str, Any] = {
            "total_entities": 0,
            "entities_by_type": {},
            "entities_by_source": {},
            "mentioned_in_edges": 0,
            "appears_in_edges": 0,
        }
        try:
            # Total entities
            t_rec = self.neo4j.execute_query("MATCH (e:Entity) RETURN count(e) AS cnt")
            stats["total_entities"] = t_rec[0]["cnt"] if t_rec else 0

            # By type
            type_recs = self.neo4j.execute_query(
                "MATCH (e:Entity) RETURN e.type AS type, count(e) AS cnt ORDER BY cnt DESC"
            )
            stats["entities_by_type"] = {r["type"]: r["cnt"] for r in type_recs if r.get("type")}

            # By source
            src_recs = self.neo4j.execute_query(
                "MATCH (e:Entity) RETURN e.source AS source, count(e) AS cnt ORDER BY cnt DESC"
            )
            stats["entities_by_source"] = {r["source"]: r["cnt"] for r in src_recs if r.get("source")}

            # Edges
            m_rec = self.neo4j.execute_query("MATCH ()-[r:MENTIONED_IN]->() RETURN count(r) AS cnt")
            stats["mentioned_in_edges"] = m_rec[0]["cnt"] if m_rec else 0

            a_rec = self.neo4j.execute_query("MATCH ()-[r:APPEARS_IN]->() RETURN count(r) AS cnt")
            stats["appears_in_edges"] = a_rec[0]["cnt"] if a_rec else 0

        except Exception as e:
            logger.error("Failed to retrieve entity statistics: %s", e)

        return stats
