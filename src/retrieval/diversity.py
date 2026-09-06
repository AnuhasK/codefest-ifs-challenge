from typing import List, Dict
from src.models.search import SearchResult
from src.config import RETRIEVAL_MAX_CHUNKS_PER_DOC


def apply_document_diversity(
    candidates: List[SearchResult],
    max_per_doc: int = RETRIEVAL_MAX_CHUNKS_PER_DOC,
    preserve_top_k: int = 3,
) -> List[SearchResult]:
    """
    Controlled document diversity filter to prevent single-document clustering.
    
    Ensures the cross-encoder and evidence selection examine a diverse set of source
    documents across the archive, rather than being saturated by 8-10 chunks from
    the same novel or wiki article.
    
    Strategy:
    1. The top `preserve_top_k` chunks are kept unconditionally to respect highest-confidence hits.
    2. Subsequent chunks are accepted only if their source document has fewer than `max_per_doc` chunks.
    3. Any excess chunks are kept in reserve and appended at the end if candidate volume is low.
    
    Args:
        candidates: Pre-ranked candidate list (e.g. from RRF fusion)
        max_per_doc: Maximum allowed chunks per individual document (default: 3)
        preserve_top_k: Number of absolute top results guaranteed entry without document quota check
        
    Returns:
        Diversity-balanced list of SearchResult models with preserved metadata.
    """
    if not candidates or len(candidates) <= preserve_top_k:
        return candidates

    doc_counts: Dict[str, int] = {}
    selected: List[SearchResult] = []
    overflow: List[SearchResult] = []

    # 1. Process top preserved items
    for idx in range(min(preserve_top_k, len(candidates))):
        res = candidates[idx]
        doc_key = res.document_id or res.document_title or "unknown"
        doc_counts[doc_key] = doc_counts.get(doc_key, 0) + 1
        selected.append(res)

    # 2. Process remaining items applying quota
    for idx in range(preserve_top_k, len(candidates)):
        res = candidates[idx]
        doc_key = res.document_id or res.document_title or "unknown"
        current_count = doc_counts.get(doc_key, 0)

        if current_count < max_per_doc:
            doc_counts[doc_key] = current_count + 1
            selected.append(res)
        else:
            overflow.append(res)

    # 3. Append overflow at the end if total candidates is still small
    combined = selected + overflow

    # 4. Re-index ranks
    reindexed: List[SearchResult] = []
    for rank, res in enumerate(combined, start=1):
        res_copy = res.model_copy()
        res_copy.rank = rank
        reindexed.append(res_copy)

    return reindexed
