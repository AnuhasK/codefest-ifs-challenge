from src.models.search import SearchResult
from src.retrieval.diversity import apply_document_diversity


def _make_res(cid: str, doc_id: str, title: str, rank: int) -> SearchResult:
    return SearchResult(
        chunk_id=cid,
        document_id=doc_id,
        content=f"Content for {cid}",
        score=1.0 / rank,
        document_title=title,
        rank=rank,
    )


def test_diversity_preserves_top_k():
    # 5 chunks all from docA
    candidates = [_make_res(f"c{i}", "docA", "Doc A", i) for i in range(1, 6)]
    # preserve top 2, max per doc 2
    diverse = apply_document_diversity(candidates, max_per_doc=2, preserve_top_k=2)

    assert diverse[0].chunk_id == "c1"
    assert diverse[1].chunk_id == "c2"


def test_diversity_limits_cluster_and_promotes_others():
    # docA has 4 chunks, docB has 2 chunks
    # Order: docA_1, docA_2, docA_3, docA_4, docB_1, docB_2
    candidates = [
        _make_res("a1", "docA", "Doc A", 1),
        _make_res("a2", "docA", "Doc A", 2),
        _make_res("a3", "docA", "Doc A", 3),
        _make_res("a4", "docA", "Doc A", 4),
        _make_res("b1", "docB", "Doc B", 5),
        _make_res("b2", "docB", "Doc B", 6),
    ]

    # preserve top 1, max per doc 2
    # a1 kept (preserve_top_k=1)
    # a2 kept (docA count reaches 2)
    # a3 overflow
    # a4 overflow
    # b1 kept (docB count=1)
    # b2 kept (docB count=2)
    diverse = apply_document_diversity(candidates, max_per_doc=2, preserve_top_k=1)

    top_4_cids = [r.chunk_id for r in diverse[:4]]
    assert "b1" in top_4_cids
    assert "b2" in top_4_cids
    # Verify all chunks are preserved in total
    assert len(diverse) == len(candidates)
    # Verify ranks are re-indexed
    for i, r in enumerate(diverse, start=1):
        assert r.rank == i
