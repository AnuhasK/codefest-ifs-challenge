from src.models.search import SearchResult
from src.retrieval.fusion import rrf_fusion


def _make_result(cid: str, title: str, rank: int, score: float = 0.5) -> SearchResult:
    return SearchResult(
        chunk_id=cid,
        document_id=f"doc_{cid}",
        content=f"Content for {cid}",
        score=score,
        document_title=title,
        rank=rank,
    )


def test_rrf_fusion_overlap_promotion():
    """Documents appearing in multiple lists should be promoted by RRF."""
    # List 1: c1 (rank 1), c2 (rank 2)
    list1 = [_make_result("c1", "Doc1", 1), _make_result("c2", "Doc2", 2)]
    # List 2: c2 (rank 1), c3 (rank 2)
    list2 = [_make_result("c2", "Doc2", 1), _make_result("c3", "Doc3", 2)]

    fused = rrf_fusion([list1, list2], k=60)

    # c2 appears in both at rank 2 and rank 1:
    # score(c2) = 1/(60+2) + 1/(60+1) = 0.016129 + 0.016393 = ~0.0325
    # score(c1) = 1/(60+1) = ~0.0164
    # score(c3) = 1/(60+2) = ~0.0161
    assert len(fused) == 3
    assert fused[0].chunk_id == "c2"
    assert fused[0].rank == 1
    assert fused[0].metadata["stream_count"] == 2
    assert fused[1].chunk_id == "c1"
    assert fused[2].chunk_id == "c3"


def test_rrf_fusion_empty_and_single():
    assert rrf_fusion([]) == []
    assert rrf_fusion([[]]) == []

    list1 = [_make_result("c1", "Doc1", 1), _make_result("c2", "Doc2", 2)]
    fused = rrf_fusion([list1], k=60)
    assert len(fused) == 2
    assert fused[0].chunk_id == "c1"
    assert fused[1].chunk_id == "c2"


def test_rrf_fusion_score_sorting_and_dedup():
    list1 = [_make_result("c1", "Doc1", 1), _make_result("c2", "Doc2", 2)]
    list2 = [_make_result("c3", "Doc3", 1), _make_result("c1", "Doc1", 2)]

    fused = rrf_fusion([list1, list2], k=60)
    # Deduplication
    unique_ids = [r.chunk_id for r in fused]
    assert len(unique_ids) == len(set(unique_ids))

    # Descending score ordering
    for i in range(len(fused) - 1):
        assert fused[i].score >= fused[i + 1].score
        assert fused[i].rank == i + 1
