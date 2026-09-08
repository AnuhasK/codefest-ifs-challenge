from typing import List, Optional
from src.database.postgres import get_db_connection
from src.models.search import SearchResult
from src.providers.embeddings import EmbeddingProvider, get_embedding_provider


def dense_search(
    query: str,
    provider: Optional[EmbeddingProvider] = None,
    top_k: int = 50,
    use_contextual: bool = False,
    source_category: Optional[str] = None,
) -> List[SearchResult]:
    """
    Execute dense semantic similarity search against pgvector.
    
    Args:
        query: User search query or question
        provider: Embedding provider (defaults to configured Voyage AI)
        top_k: Maximum number of candidate results
        use_contextual: If True, search contextual_embedding column; else standard embedding
        source_category: Optional filter by document category (e.g. 'wiki', 'codex', 'chronicles')
        
    Returns:
        Ranked list of SearchResult models sorted by cosine similarity descending.
    """
    if not query or not query.strip():
        return []

    if provider is None:
        provider = get_embedding_provider()

    # Generate query embedding with asymmetric input_type='query'
    query_vector = provider.embed_query(query)
    if not query_vector:
        return []

    vector_col = "contextual_embedding" if use_contextual else "embedding"

    # Query using pgvector cosine distance operator <=>
    sql = f"""
        SELECT 
            c.id AS chunk_id,
            c.document_id,
            c.content,
            c.page_start,
            c.page_end,
            c.chapter,
            c.section_title,
            c.metadata,
            COALESCE(d.title, c.section_title, 'Visual Archive Asset') AS document_title,
            COALESCE(d.source_category, 'images') AS source_category,
            COALESCE(d.source_path, c.metadata->>'file_path') AS source_path,
            (1 - (c.{vector_col} <=> %(qvec)s::vector)) AS score,
            (c.{vector_col} <=> %(qvec)s::vector) AS distance
        FROM chunks c
        LEFT JOIN documents d ON c.document_id = d.id
        WHERE c.{vector_col} IS NOT NULL
    """
    params: dict = {"qvec": query_vector, "limit": top_k}

    if source_category:
        sql += " AND COALESCE(d.source_category, 'images') = %(cat)s"
        params["cat"] = source_category

    sql += f" ORDER BY c.{vector_col} <=> %(qvec)s::vector ASC LIMIT %(limit)s;"

    results: List[SearchResult] = []
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            for rank, row in enumerate(rows, start=1):
                res = SearchResult(
                    chunk_id=str(row["chunk_id"]),
                    document_id=str(row["document_id"] or row["chunk_id"]),
                    content=row["content"],
                    score=float(row["score"]),
                    distance=float(row["distance"]) if row.get("distance") is not None else None,
                    page_start=row.get("page_start"),
                    page_end=row.get("page_end"),
                    chapter=row.get("chapter"),
                    section_title=row.get("section_title"),
                    source_category=row.get("source_category") or "images",
                    source_path=row.get("source_path"),
                    document_title=row.get("document_title") or "Visual Archive Asset",
                    rank=rank,
                    metadata=row.get("metadata") or {},
                )
                results.append(res)

    return results
