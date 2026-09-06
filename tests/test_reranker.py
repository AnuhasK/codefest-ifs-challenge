from src.models.search import SearchResult
from src.providers.reranker_provider import (
    FlashRankRerankerProvider,
    get_reranker_provider,
    RerankerProvider,
    RerankResult,
)
from src.retrieval.reranker import rerank_candidates


class MockReranker(RerankerProvider):
    def rerank(self, query: str, documents: list[str], top_k: int = 20):
        # Reverse order score
        return [
            RerankResult(index=i, score=float(i + 1) / len(documents), content=documents[i])
            for i in reversed(range(len(documents)))
        ][:top_k]


def test_flashrank_reranker_provider_execution():
    provider = FlashRankRerankerProvider()
    docs = [
        "The garrison strength of Greyfell Citadel is recorded as 3,695 souls under arms.",
        "An ancient ballad tells of shadows wandering the Leadencrag.",
        "The Weeping Lurker threat rating is 10 on the Vanguard scale.",
    ]
    query = "What is the garrison strength of Greyfell Citadel?"
    results = provider.rerank(query, docs, top_k=2)

    assert len(results) == 2
    assert results[0].index == 0
    assert "3,695" in results[0].content
    assert results[0].score > results[1].score


def test_rerank_candidates_integration():
    candidates = [
        SearchResult(
            chunk_id="c1",
            document_id="d1",
            content="Random lore about moss and trees.",
            score=0.9,
            rank=1,
            document_title="Forest Tales",
        ),
        SearchResult(
            chunk_id="c2",
            document_id="d2",
            content="The banner of House Morvain displays two crossed golden keys.",
            score=0.8,
            rank=2,
            document_title="House Morvain Heraldry",
        ),
    ]

    reranked = rerank_candidates(
        query="What is on the banner of House Morvain?",
        candidates=candidates,
        top_k=2,
    )

    assert len(reranked) == 2
    # c2 should be reranked to rank 1
    assert reranked[0].chunk_id == "c2"
    assert reranked[0].rank == 1
    assert reranked[0].document_title == "House Morvain Heraldry"
    assert "reranker_score" in reranked[0].metadata
    assert reranked[0].score >= reranked[1].score
