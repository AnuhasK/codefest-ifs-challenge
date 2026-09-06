import logging
from typing import List, Optional
from pydantic import BaseModel, Field

from src.models.search import SearchResult
from src.retrieval.query_analyzer import QueryAnalysis, analyze_query
from src.retrieval.bm25_search import bm25_search
from src.retrieval.dense_search import dense_search
from src.retrieval.fusion import rrf_fusion
from src.retrieval.diversity import apply_document_diversity
from src.retrieval.reranker import rerank_candidates
from src.providers.embeddings import EmbeddingProvider
from src.providers.reranker_provider import RerankerProvider
from src.config import (
    BM25_TOP_K,
    DENSE_TOP_K,
    RERANKER_TOP_K,
    RRF_K,
    RETRIEVAL_MAX_CHUNKS_PER_DOC,
)

logger = logging.getLogger(__name__)


class RetrievalConfig(BaseModel):
    """Configuration options for the hybrid retrieval orchestrator."""
    bm25_top_k: int = BM25_TOP_K
    dense_top_k: int = DENSE_TOP_K
    contextual_top_k: int = DENSE_TOP_K
    rrf_k: int = RRF_K
    rrf_top_n: int = 100
    reranker_top_k: int = RERANKER_TOP_K
    max_chunks_per_doc: int = RETRIEVAL_MAX_CHUNKS_PER_DOC

    enable_bm25: bool = True
    enable_dense: bool = True
    enable_contextual: bool = True
    enable_diversity: bool = True
    enable_reranker: bool = True


def retrieve(
    query: str,
    query_analysis: Optional[QueryAnalysis] = None,
    config: Optional[RetrievalConfig] = None,
    embedding_provider: Optional[EmbeddingProvider] = None,
    reranker_provider: Optional[RerankerProvider] = None,
    source_category: Optional[str] = None,
) -> List[SearchResult]:
    """
    Execute the full hybrid retrieval pipeline:
    
    1. Query Analysis (heuristic or LLM-assisted)
    2. BM25-style lexical search (PostgreSQL FTS with ts_rank_cd)
    3. Dense semantic search (raw chunk embeddings)
    4. Contextual dense search (contextualized chunk embeddings)
    5. RRF Fusion across all active streams
    6. Controlled Document Diversity filter
    7. Cross-Encoder Reranking
    
    Returns:
        Ranked list of top candidate SearchResults ready for context construction.
    """
    if not query or not query.strip():
        return []

    if config is None:
        config = RetrievalConfig()

    # Step 1: Query Analysis
    if query_analysis is None:
        query_analysis = analyze_query(query)

    search_streams: List[List[SearchResult]] = []

    # Step 2: PostgreSQL FTS / BM25 Search
    if config.enable_bm25:
        # Search using optimized bm25 query or original query
        lexical_query = query_analysis.bm25_query or query
        bm25_results = bm25_search(
            query=lexical_query,
            top_k=config.bm25_top_k,
            source_category=source_category,
        )
        if bm25_results:
            search_streams.append(bm25_results)

    # Step 3: Standard Dense Search
    if config.enable_dense:
        std_dense_results = dense_search(
            query=query,
            provider=embedding_provider,
            top_k=config.dense_top_k,
            use_contextual=False,
            source_category=source_category,
        )
        if std_dense_results:
            search_streams.append(std_dense_results)

    # Step 4: Contextual Dense Search
    if config.enable_contextual:
        ctx_dense_results = dense_search(
            query=query,
            provider=embedding_provider,
            top_k=config.contextual_top_k,
            use_contextual=True,
            source_category=source_category,
        )
        if ctx_dense_results:
            search_streams.append(ctx_dense_results)

    if not search_streams:
        return []

    # Step 5: Reciprocal Rank Fusion
    if len(search_streams) == 1:
        fused = search_streams[0][:config.rrf_top_n]
    else:
        fused = rrf_fusion(
            ranked_lists=search_streams,
            k=config.rrf_k,
            top_n=config.rrf_top_n,
        )

    # Step 6: Controlled Document Diversity Filter
    if config.enable_diversity and len(fused) > config.reranker_top_k:
        diverse_candidates = apply_document_diversity(
            candidates=fused,
            max_per_doc=config.max_chunks_per_doc,
            preserve_top_k=3,
        )
    else:
        diverse_candidates = fused

    # Step 7: Cross-Encoder Reranker
    if config.enable_reranker and diverse_candidates:
        final_results = rerank_candidates(
            query=query,
            candidates=diverse_candidates,
            reranker=reranker_provider,
            top_k=config.reranker_top_k,
        )
    else:
        final_results = diverse_candidates[:config.reranker_top_k]

    return final_results
