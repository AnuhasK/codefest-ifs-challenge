import logging
from typing import List, Dict, Any, Optional, Tuple, Set
from uuid import UUID

from src.models.query import HopResult
from src.models.search import SearchResult
from src.knowledge.graph import KnowledgeGraph
from src.database.postgres import get_db_connection

logger = logging.getLogger(__name__)


def multi_hop_search(
    start_entities: List[str],
    graph: Optional[KnowledgeGraph] = None,
    max_hops: int = 3,
    target_types: Optional[List[str]] = None,
    limit: int = 20,
) -> Tuple[List[HopResult], List[str], List[str]]:
    """
    Starting from recognized seed entities, traverse the knowledge graph
    to find connected entities, relationships, and supporting evidence chunk IDs.

    Returns:
        Tuple of:
        - traversal_hops: List[HopResult] structured hop details
        - discovered_entities: List[str] all unique intermediate/target entity names
        - evidence_chunk_ids: List[str] all chunk IDs linked to edges along paths
    """
    if not start_entities:
        return [], [], []

    kg = graph or KnowledgeGraph()
    traversal_hops: List[HopResult] = []
    discovered_entities_set: Set[str] = set()
    evidence_chunk_ids_set: Set[str] = set()
    seen_paths: Set[str] = set()

    for seed in start_entities:
        if not seed or not seed.strip():
            continue

        raw_paths = kg.find_multihop_paths(
            start_name=seed.strip(),
            target_types=target_types,
            max_hops=max_hops,
            limit=limit,
        )

        for path in raw_paths:
            nodes = path.get("nodes", [])
            relationships = path.get("relationships", [])

            if len(nodes) < 2 or not relationships:
                continue

            path_key = "->".join(n.get("name", "") for n in nodes)
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)

            for step_idx in range(len(relationships)):
                src_node = nodes[step_idx]
                tgt_node = nodes[step_idx + 1]
                rel = relationships[step_idx]

                src_name = src_node.get("name", "")
                tgt_name = tgt_node.get("name", "")
                rel_type = rel.get("type", "RELATED_TO")
                cid = rel.get("evidence_chunk_id")

                discovered_entities_set.add(src_name)
                discovered_entities_set.add(tgt_name)
                if cid:
                    evidence_chunk_ids_set.add(str(cid))

                path_desc = f"{src_name} -[{rel_type}]- {tgt_name}"
                traversal_hops.append(
                    HopResult(
                        hop_number=step_idx + 1,
                        source_entity=src_name,
                        target_entity=tgt_name,
                        relationship_type=rel_type,
                        direction="forward",
                        evidence_chunk_ids=[str(cid)] if cid else [],
                        path_description=path_desc,
                    )
                )

    # Filter out the initial seed entities from discovered entities
    seeds_lower = {s.lower() for s in start_entities}
    discovered_entities = [e for e in discovered_entities_set if e.lower() not in seeds_lower]

    logger.debug(
        "Multi-hop search from %s yielded %d hops, %d discovered entities, %d evidence chunks.",
        start_entities, len(traversal_hops), len(discovered_entities), len(evidence_chunk_ids_set),
    )

    return traversal_hops, discovered_entities, list(evidence_chunk_ids_set)


def hydrate_evidence_chunks(
    chunk_ids: List[str],
    source_category: Optional[str] = None,
    stream_name: str = "graph_multihop",
) -> List[SearchResult]:
    """
    Hydrate full chunk records from PostgreSQL for a list of chunk IDs retrieved
    from knowledge graph edge metadata.
    """
    if not chunk_ids:
        return []

    valid_uuids = []
    for cid in chunk_ids:
        try:
            valid_uuids.append(UUID(cid) if not isinstance(cid, UUID) else cid)
        except Exception:
            continue

    if not valid_uuids:
        return []

    sql = """
    SELECT c.id, c.document_id, c.content, c.contextualized_content,
           c.section_title, c.chapter, c.page_start, c.page_end, c.metadata,
           d.title AS document_title, d.source_category, d.source_path
    FROM chunks c
    LEFT JOIN documents d ON c.document_id = d.id
    WHERE c.id = ANY(%s)
    """
    params: List[Any] = [valid_uuids]
    if source_category:
        sql += " AND d.source_category = %s"
        params.append(source_category)

    hydrated_rows = []
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            hydrated_rows = cur.fetchall()

    results: List[SearchResult] = []
    for row in hydrated_rows:
        cid_str = str(row["id"])
        meta = dict(row.get("metadata") or {})
        meta["retrieval_stream"] = stream_name

        results.append(
            SearchResult(
                chunk_id=cid_str,
                document_id=str(row["document_id"]) if row.get("document_id") else "",
                content=row.get("contextualized_content") or row["content"],
                score=1.0,
                section_title=row.get("section_title"),
                chapter=row.get("chapter"),
                page_start=row.get("page_start"),
                page_end=row.get("page_end"),
                source_category=row.get("source_category"),
                source_path=row.get("source_path"),
                document_title=row.get("document_title"),
                metadata=meta,
            )
        )

    return results
