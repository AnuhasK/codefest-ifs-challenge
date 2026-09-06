import logging
from typing import List, Optional
import psycopg

from src.database.postgres import get_db_connection
from src.models.search import SearchResult
from src.config import BM25_TOP_K

logger = logging.getLogger(__name__)


def bm25_search(
    query: str,
    top_k: int = BM25_TOP_K,
    source_category: Optional[str] = None,
) -> List[SearchResult]:
    """
    Execute lexical retrieval using PostgreSQL Full-Text Search / BM25-style ranking.
    
    Uses cover density ranking (ts_rank_cd) over the indexed tsvector column to account
    for term proximity and frequency, matching the retrieval behavior of BM25.
    
    Args:
        query: User search query or question
        top_k: Maximum number of candidate results to return
        source_category: Optional filter by document category (e.g. 'wiki', 'codex')
        
    Returns:
        Ranked list of SearchResult models sorted by lexical score descending.
    """
    if not query or not query.strip():
        return []

    clean_query = query.strip()

    # Query with ts_rank_cd (cover density ranking) against GIN-indexed search_vector
    sql = """
        SELECT 
            c.id AS chunk_id,
            c.document_id,
            c.content,
            c.page_start,
            c.page_end,
            c.chapter,
            c.section_title,
            c.metadata,
            d.title AS document_title,
            d.source_category,
            d.source_path,
            ts_rank_cd(c.search_vector, plainto_tsquery('english', %(query)s)) AS score
        FROM chunks c
        JOIN documents d ON c.document_id = d.id
        WHERE c.search_vector @@ plainto_tsquery('english', %(query)s)
    """
    params = {"query": clean_query, "limit": top_k}

    if source_category:
        sql += " AND d.source_category = %(cat)s"
        params["cat"] = source_category

    sql += " ORDER BY score DESC LIMIT %(limit)s;"

    results: List[SearchResult] = []
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

                # Fallback to websearch_to_tsquery if plainto_tsquery produced no rows for complex syntax
                if not rows:
                    fallback_sql = sql.replace("plainto_tsquery", "websearch_to_tsquery")
                    cur.execute(fallback_sql, params)
                    rows = cur.fetchall()

                for rank, row in enumerate(rows, start=1):
                    res = SearchResult(
                        chunk_id=str(row["chunk_id"]),
                        document_id=str(row["document_id"]),
                        content=row["content"],
                        score=float(row["score"]),
                        distance=None,
                        page_start=row.get("page_start"),
                        page_end=row.get("page_end"),
                        chapter=row.get("chapter"),
                        section_title=row.get("section_title"),
                        source_category=row.get("source_category"),
                        source_path=row.get("source_path"),
                        document_title=row.get("document_title"),
                        rank=rank,
                        metadata=row.get("metadata") or {},
                    )
                    results.append(res)
    except Exception as e:
        logger.warning(f"Error during PostgreSQL FTS BM25 search for query '{query}': {e}")
        return []

    return results
