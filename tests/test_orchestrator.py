from src.retrieval.orchestrator import retrieve, RetrievalConfig
from src.models.search import SearchResult


def test_orchestrator_full_pipeline():
    query = "What numerical rating is assigned to the creature known as the Weeping Lurker?"
    config = RetrievalConfig(
        bm25_top_k=20,
        dense_top_k=20,
        contextual_top_k=20,
        rrf_top_n=20,
        reranker_top_k=5,
        enable_bm25=True,
        enable_dense=True,
        enable_contextual=True,
        enable_diversity=True,
        enable_reranker=True,
    )
    results = retrieve(query, config=config)

    assert len(results) <= 5
    assert len(results) > 0
    assert all(isinstance(r, SearchResult) for r in results)

    # Top result should contain Weeping Lurker
    top_res = results[0]
    combined_text = (top_res.content + " " + (top_res.document_title or "")).lower()
    assert "weeping lurker" in combined_text


def test_orchestrator_toggle_options():
    query = "Ederon Fellgard"

    # Disable dense & contextual -> BM25 only
    cfg_bm25_only = RetrievalConfig(
        enable_bm25=True,
        enable_dense=False,
        enable_contextual=False,
        enable_reranker=False,
        bm25_top_k=5,
        rrf_top_n=5,
        reranker_top_k=5,
    )
    res_bm25 = retrieve(query, config=cfg_bm25_only)
    assert len(res_bm25) > 0
    assert all(isinstance(r, SearchResult) for r in res_bm25)

    # Disable BM25 -> dense only
    cfg_dense_only = RetrievalConfig(
        enable_bm25=False,
        enable_dense=True,
        enable_contextual=False,
        enable_reranker=False,
        dense_top_k=5,
        rrf_top_n=5,
        reranker_top_k=5,
    )
    res_dense = retrieve(query, config=cfg_dense_only)
    assert len(res_dense) > 0


def test_orchestrator_entity_search_flag():
    cfg = RetrievalConfig()
    assert hasattr(cfg, "enable_entity_search")
    assert cfg.enable_entity_search is True
    assert cfg.entity_top_k == 50

    # With enable_entity_search=False, toggle works cleanly
    cfg_disabled = RetrievalConfig(enable_entity_search=False)
    assert cfg_disabled.enable_entity_search is False

    # With enable_entity_search=True, should run cleanly without error
    cfg_with_entity = RetrievalConfig(
        enable_bm25=True,
        enable_dense=False,
        enable_contextual=False,
        enable_entity_search=True,
        enable_reranker=False,
        bm25_top_k=5,
        rrf_top_n=5,
        reranker_top_k=5,
    )
    res = retrieve("Vaelith", config=cfg_with_entity)
    assert isinstance(res, list)

