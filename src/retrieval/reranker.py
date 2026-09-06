from typing import List, Optional
from src.models.search import SearchResult
from src.providers.reranker_provider import RerankerProvider, get_reranker_provider
from src.config import RERANKER_TOP_K


def rerank_candidates(
    query: str,
    candidates: List[SearchResult],
    reranker: Optional[RerankerProvider] = None,
    top_k: int = RERANKER_TOP_K,
) -> List[SearchResult]:
    """
    Rerank a list of candidate SearchResults using a cross-encoder model.
    
    The cross-encoder jointly assesses the query and the chunk text, producing
    a more accurate relevance score than bi-encoder cosine similarity.
    
    Args:
        query: User search query or question
        candidates: Candidate search results (e.g. from RRF fusion / diversity filter)
        reranker: Optional RerankerProvider instance (defaults to configured FlashRank)
        top_k: Number of final reranked results to return
        
    Returns:
        List of SearchResult models sorted by cross-encoder score descending, with updated ranks.
    """
    if not query or not candidates:
        return []

    if reranker is None:
        reranker = get_reranker_provider()

    passages = [c.content for c in candidates]
    rerank_results = reranker.rerank(query=query, documents=passages, top_k=top_k)

    reranked: List[SearchResult] = []
    for rank, item in enumerate(rerank_results, start=1):
        orig_candidate = candidates[item.index]

        meta = dict(orig_candidate.metadata)
        meta["reranker_score"] = round(item.score, 6)
        meta["pre_rerank_rank"] = orig_candidate.rank
        meta["pre_rerank_score"] = orig_candidate.score

        res = SearchResult(
            chunk_id=orig_candidate.chunk_id,
            document_id=orig_candidate.document_id,
            content=orig_candidate.content,
            score=round(item.score, 6),
            distance=orig_candidate.distance,
            page_start=orig_candidate.page_start,
            page_end=orig_candidate.page_end,
            chapter=orig_candidate.chapter,
            section_title=orig_candidate.section_title,
            source_category=orig_candidate.source_category,
            source_path=orig_candidate.source_path,
            document_title=orig_candidate.document_title,
            rank=rank,
            metadata=meta,
        )
        reranked.append(res)

    return reranked
