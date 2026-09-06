from typing import List, Dict, Any
from src.models.search import SearchResult
from src.config import RRF_K


def rrf_fusion(
    ranked_lists: List[List[SearchResult]],
    k: int = RRF_K,
    top_n: int = 100,
) -> List[SearchResult]:
    """
    Combine multiple ranked result lists using Reciprocal Rank Fusion (RRF).
    
    Formula:
        RRF_score(d) = sum_{list_i} 1 / (k + rank_i(d))
        
    Args:
        ranked_lists: List of ranked SearchResult lists from different retrieval streams
                      (e.g., [bm25_results, standard_dense_results, contextual_dense_results])
        k: Smoothing constant (default 60)
        top_n: Maximum number of merged results to return
        
    Returns:
        Fused, deduplicated list of SearchResult models sorted by RRF score descending.
    """
    if not ranked_lists:
        return []

    # Filter out empty lists
    active_lists = [rl for rl in ranked_lists if rl]
    if not active_lists:
        return []

    scores: Dict[str, float] = {}
    best_instances: Dict[str, SearchResult] = {}
    stream_ranks: Dict[str, Dict[str, int]] = {}

    for stream_idx, result_list in enumerate(active_lists):
        stream_name = f"stream_{stream_idx}"
        for rank, res in enumerate(result_list, start=1):
            cid = res.chunk_id
            rrf_contrib = 1.0 / (k + rank)
            scores[cid] = scores.get(cid, 0.0) + rrf_contrib

            if cid not in stream_ranks:
                stream_ranks[cid] = {}
            stream_ranks[cid][stream_name] = rank

            # Keep the representation that achieved the highest rank in any stream
            if cid not in best_instances:
                best_instances[cid] = res
            else:
                existing_best = best_instances[cid]
                # Prefer instance with lower rank number (closer to top)
                if rank < existing_best.rank:
                    best_instances[cid] = res

    # Sort chunks by accumulated RRF score descending
    sorted_chunk_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)
    top_chunk_ids = sorted_chunk_ids[:top_n]

    fused_results: List[SearchResult] = []
    for final_rank, cid in enumerate(top_chunk_ids, start=1):
        orig_res = best_instances[cid]
        final_score = scores[cid]

        # Merge fusion metadata
        meta = dict(orig_res.metadata)
        meta["rrf_score"] = round(final_score, 6)
        meta["fusion_ranks"] = stream_ranks[cid]
        meta["stream_count"] = len(stream_ranks[cid])

        fused = SearchResult(
            chunk_id=orig_res.chunk_id,
            document_id=orig_res.document_id,
            content=orig_res.content,
            score=round(final_score, 6),
            distance=orig_res.distance,
            page_start=orig_res.page_start,
            page_end=orig_res.page_end,
            chapter=orig_res.chapter,
            section_title=orig_res.section_title,
            source_category=orig_res.source_category,
            source_path=orig_res.source_path,
            document_title=orig_res.document_title,
            rank=final_rank,
            metadata=meta,
        )
        fused_results.append(fused)

    return fused_results
