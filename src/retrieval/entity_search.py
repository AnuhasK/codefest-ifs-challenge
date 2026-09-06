import logging
from typing import Dict, List, Optional, Set, Any
from uuid import UUID

from src.models.search import SearchResult
from src.retrieval.query_analyzer import QueryAnalysis, extract_entities_heuristic
from src.knowledge.graph import KnowledgeGraph
from src.database.postgres import get_db_connection

logger = logging.getLogger(__name__)


def entity_search(
    query: str = "",
    query_analysis: Optional[QueryAnalysis] = None,
    graph: Optional[KnowledgeGraph] = None,
    top_k: int = 50,
    source_category: Optional[str] = None,
    query_entities: Optional[List[str]] = None,
) -> List[SearchResult]:
    """
    Execute entity-aware retrieval by traversing Neo4j knowledge graph and hydrating PostgreSQL chunks.

    1. Resolves candidate entity mentions from query / query_analysis / query_entities.
    2. Looks up canonical entity nodes and surface form aliases in Neo4j.
    3. Retrieves all chunk IDs linked via [:MENTIONED_IN] edges.
    4. Hydrates full chunk records from PostgreSQL in a single batch query.
    5. Scores chunks based on exact/alias matches, entity confidence, and multi-entity co-occurrence.
    6. Returns ranked SearchResult objects ready for 4-way RRF fusion.
    """
    # 1. Determine target entity mentions
    candidate_names: List[str] = []
    if query_entities:
        candidate_names = [e.strip() for e in query_entities if e and e.strip()]
    elif query_analysis and query_analysis.entities_mentioned:
        candidate_names = [e.strip() for e in query_analysis.entities_mentioned if e and e.strip()]
    elif query:
        candidate_names = extract_entities_heuristic(query)

    if not candidate_names:
        logger.debug("No entity mentions identified in query for entity_search.")
        return []

    kg = graph or KnowledgeGraph()

    # 2. Resolve entities in Neo4j and collect linked chunk IDs
    chunk_to_entity_matches: Dict[str, List[Dict[str, Any]]] = {}  # chunk_id -> list of entity match info
    resolved_entity_ids: Set[str] = set()

    for name in candidate_names:
        ent = kg.find_entity(name)
        if not ent:
            continue

        resolved_entity_ids.add(ent.id)
        linked_chunk_ids = kg.get_entity_chunks(ent.id)

        is_exact = (ent.name.lower() == name.lower())
        match_info = {
            "entity_id": ent.id,
            "canonical_name": ent.name,
            "entity_type": ent.entity_type,
            "query_mention": name,
            "is_exact": is_exact,
            "confidence": ent.confidence,
        }

        for cid in linked_chunk_ids:
            if cid not in chunk_to_entity_matches:
                chunk_to_entity_matches[cid] = []
            chunk_to_entity_matches[cid].append(match_info)

    if not chunk_to_entity_matches:
        logger.debug("No graph chunk references found for query entities: %s", candidate_names)
        return []

    # 3. Hydrate chunk data from PostgreSQL
    chunk_uuids = []
    for cid_str in chunk_to_entity_matches.keys():
        try:
            chunk_uuids.append(UUID(cid_str) if not isinstance(cid_str, UUID) else cid_str)
        except Exception:
            continue

    if not chunk_uuids:
        return []

    sql = """
    SELECT c.id, c.document_id, c.content, c.contextualized_content,
           c.section_title, c.chapter, c.page_start, c.page_end, c.metadata,
           d.title AS document_title, d.source_category, d.source_path
    FROM chunks c
    LEFT JOIN documents d ON c.document_id = d.id
    WHERE c.id = ANY(%s)
    """
    params: List[Any] = [chunk_uuids]
    if source_category:
        sql += " AND d.source_category = %s"
        params.append(source_category)

    hydrated_chunks = []
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            hydrated_chunks = cur.fetchall()

    if not hydrated_chunks:
        return []

    # 4. Compute relevance scores and assemble SearchResult objects
    results: List[SearchResult] = []

    for row in hydrated_chunks:
        cid_str = str(row["id"])
        matches = chunk_to_entity_matches.get(cid_str, [])
        if not matches:
            continue

        # Score calculation:
        # - Base score per match: 1.0 for exact, 0.8 for alias
        # - Weight by entity confidence
        # - Multi-entity synergy: bonus for chunks containing multiple distinct query entities
        unique_entities_in_chunk = {m["canonical_name"] for m in matches}
        match_score = 0.0
        for m in matches:
            base = 1.0 if m["is_exact"] else 0.8
            conf = float(m["confidence"]) if m["confidence"] is not None else 1.0
            match_score += base * (0.5 + 0.5 * conf)

        # Multi-entity synergy multiplier (valuable for multi-hop queries)
        synergy_mult = 1.0 + 0.5 * (len(unique_entities_in_chunk) - 1)
        final_score = match_score * synergy_mult

        meta = dict(row.get("metadata") or {})
        meta["matched_entities"] = [m["canonical_name"] for m in matches]
        meta["retrieval_stream"] = "entity_search"

        results.append(
            SearchResult(
                chunk_id=cid_str,
                document_id=str(row["document_id"]) if row.get("document_id") else "",
                content=row.get("contextualized_content") or row["content"],
                score=round(final_score, 4),
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

    # Sort descending by score
    results.sort(key=lambda r: r.score, reverse=True)

    # Normalize scores between 0.0 and 1.0
    if results:
        max_score = results[0].score
        if max_score > 0:
            for r in results:
                r.score = round(r.score / max_score, 4)

    # Assign ranks and truncate to top_k
    final_results = results[:top_k]
    for idx, r in enumerate(final_results, start=1):
        r.rank = idx

    logger.debug(
        "Entity search retrieved %d chunks across %d query entities (top_k=%d).",
        len(final_results), len(candidate_names), top_k,
    )
    return final_results
