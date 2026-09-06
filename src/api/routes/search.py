import logging
from typing import List
from fastapi import APIRouter, HTTPException

from src.api.schemas import SearchRequest, SearchResponse, SearchResultItem
from src.retrieval.orchestrator import retrieve, RetrievalConfig
from src.retrieval.bm25_search import bm25_search
from src.retrieval.dense_search import dense_search
from src.retrieval.entity_search import entity_search

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Search"])


@router.post("/search", response_model=SearchResponse)
async def search_endpoint(request: SearchRequest) -> SearchResponse:
    """
    Search the Ashen Era Archive directly without LLM answer generation.
    Supports search types:
    - 'hybrid': 4-way RRF fusion + Cross-Encoder Reranking
    - 'bm25': PostgreSQL Full-Text Search (ts_rank_cd)
    - 'dense': Semantic dense vector search via pgvector
    - 'entity': Neo4j knowledge-graph entity-linked retrieval
    """
    try:
        search_type = request.search_type.lower().strip()
        top_k = request.top_k

        if search_type == "bm25":
            results = bm25_search(query=request.query, top_k=top_k)
        elif search_type == "dense":
            results = dense_search(query=request.query, top_k=top_k)
        elif search_type == "entity":
            results = entity_search(query=request.query, top_k=top_k)
        elif search_type == "hybrid":
            config = RetrievalConfig(reranker_top_k=top_k)
            results = retrieve(query=request.query, config=config)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported search_type '{request.search_type}'. Expected one of: 'hybrid', 'bm25', 'dense', 'entity'.",
            )

        items: List[SearchResultItem] = []
        for r in results:
            items.append(
                SearchResultItem(
                    chunk_id=r.chunk_id,
                    document_id=r.document_id,
                    document_title=r.document_title,
                    source_category=r.source_category,
                    content=r.content,
                    score=round(r.score, 4),
                    page=r.page_start,
                    section_title=r.section_title,
                    source_path=r.source_path,
                    metadata=r.metadata,
                )
            )

        return SearchResponse(
            results=items,
            total=len(items),
            search_type=search_type,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Error executing search: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred during search execution: {str(exc)}",
        )
