from src.retrieval.bm25_search import bm25_search
from src.models.search import SearchResult


def test_bm25_search_exact_name():
    """Verify BM25 finds exact entities like 'Weeping Lurker'."""
    results = bm25_search("Weeping Lurker", top_k=5)
    assert len(results) > 0
    assert all(isinstance(r, SearchResult) for r in results)
    
    # Check that Weeping Lurker is in the top result
    top_res = results[0]
    assert "weeping lurker" in (top_res.content + " " + (top_res.document_title or "")).lower()
    assert top_res.score > 0.0


def test_bm25_search_empty_and_nonsense():
    """Verify empty query returns empty list and nonsense query returns 0 or low results."""
    assert bm25_search("") == []
    assert bm25_search("   ") == []

    nonsense = bm25_search("zyxwvutsrqponmlkjihgfedcba123456789")
    assert len(nonsense) == 0


def test_bm25_search_score_ordering():
    """Verify results are sorted by score descending."""
    results = bm25_search("House Morvain Ironfell Citadel", top_k=10)
    if len(results) >= 2:
        for i in range(len(results) - 1):
            assert results[i].score >= results[i + 1].score
            assert results[i].rank == i + 1


def test_bm25_search_category_filter():
    """Verify source_category filtering works."""
    results = bm25_search("House Morvain", top_k=10, source_category="wiki")
    for r in results:
        assert r.source_category == "wiki"
