import logging
from abc import ABC, abstractmethod
from typing import List, Optional
from pydantic import BaseModel, Field

from src.config import (
    RERANKER_PROVIDER,
    RERANKER_MODEL,
    VOYAGE_API_KEY,
    GEMINI_API_KEYS,
)

logger = logging.getLogger(__name__)


class RerankResult(BaseModel):
    """Container for a single passage scored by a cross-encoder reranker."""
    index: int = Field(..., description="Original index in the input passage list")
    score: float = Field(..., description="Cross-encoder relevance score")
    content: str = Field(..., description="Passage content text")


class RerankerProvider(ABC):
    """Abstract interface for all cross-encoder reranker providers."""

    @abstractmethod
    def rerank(self, query: str, documents: List[str], top_k: int = 20) -> List[RerankResult]:
        """Score each (query, passage) pair jointly and return the top_k results."""
        pass


class FlashRankRerankerProvider(RerankerProvider):
    """
    Local cross-encoder reranker powered by FlashRank (ONNX runtime on CPU).
    Provides ultra-low latency (~20ms), high relevance scoring, and zero API calls.
    """

    def __init__(self, model_name: str = RERANKER_MODEL):
        from flashrank import Ranker
        self.model_name = model_name
        self.ranker = Ranker(model_name=self.model_name)

    def rerank(self, query: str, documents: List[str], top_k: int = 20) -> List[RerankResult]:
        if not query or not documents:
            return []

        from flashrank import RerankRequest

        passages = [{"id": idx, "text": doc} for idx, doc in enumerate(documents)]
        req = RerankRequest(query=query, passages=passages)
        raw_results = self.ranker.rerank(req)

        results: List[RerankResult] = []
        for r in raw_results[:top_k]:
            results.append(
                RerankResult(
                    index=int(r["id"]),
                    score=float(r["score"]),
                    content=r["text"],
                )
            )
        return results


class VoyageRerankerProvider(RerankerProvider):
    """
    Voyage AI Rerank API provider (e.g. rerank-2 or rerank-2-lite).
    Used when external API reranking is explicitly configured.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "rerank-2"):
        import voyageai
        self.api_key = api_key or VOYAGE_API_KEY
        if not self.api_key:
            raise ValueError("VOYAGE_API_KEY is required for VoyageRerankerProvider.")
        self.model = model
        self.client = voyageai.Client(api_key=self.api_key)

    def rerank(self, query: str, documents: List[str], top_k: int = 20) -> List[RerankResult]:
        if not query or not documents:
            return []

        response = self.client.rerank(
            query=query,
            documents=documents,
            model=self.model,
            top_k=top_k,
        )

        results: List[RerankResult] = []
        for r in response.results:
            results.append(
                RerankResult(
                    index=r.index,
                    score=float(r.relevance_score),
                    content=r.document,
                )
            )
        return results


class GeminiRerankerProvider(RerankerProvider):
    """
    LLM-based relevance scorer using Gemini Flash when no local/external reranker is available.
    """

    def __init__(self):
        from src.providers.llm_provider import get_llm_provider
        self.llm = get_llm_provider()

    def rerank(self, query: str, documents: List[str], top_k: int = 20) -> List[RerankResult]:
        if not query or not documents:
            return []

        # Score top candidates in a single prompt
        candidates_to_score = documents[:30]
        prompt = f"""You are a cross-encoder relevance judge. Given a query and candidate passages, score each passage's relevance to answering the query on a continuous scale from 0.0 to 1.0.
Query: "{query}"

Passages:
{chr(10).join([f"[{i}] {doc[:300]}" for i, doc in enumerate(candidates_to_score)])}

Return JSON with list of scores:
{{"scores": [{{"index": 0, "score": 0.95}}, ...]}}"""

        try:
            res = self.llm.generate(prompt=prompt, system_prompt="You are a relevance judge.")
            import json, re
            raw = res.content.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
            data = json.loads(raw)
            scored = []
            for item in data.get("scores", []):
                idx = int(item["index"])
                if 0 <= idx < len(candidates_to_score):
                    scored.append(
                        RerankResult(
                            index=idx,
                            score=float(item["score"]),
                            content=candidates_to_score[idx],
                        )
                    )
            scored.sort(key=lambda x: x.score, reverse=True)
            return scored[:top_k]
        except Exception as e:
            logger.warning(f"Gemini reranker fallback: {e}")
            return [
                RerankResult(index=i, score=1.0 / (i + 1), content=doc)
                for i, doc in enumerate(documents[:top_k])
            ]


def get_reranker_provider(provider_type: Optional[str] = None) -> RerankerProvider:
    """
    Factory creating the configured cross-encoder reranker provider.
    Defaults to FlashRank (local CPU ONNX, zero API calls).
    """
    ptype = (provider_type or RERANKER_PROVIDER).lower()

    if ptype == "flashrank":
        return FlashRankRerankerProvider()
    elif ptype == "voyage":
        if VOYAGE_API_KEY:
            return VoyageRerankerProvider()
        logger.warning("VOYAGE_API_KEY not configured, falling back to FlashRank.")
        return FlashRankRerankerProvider()
    elif ptype == "gemini":
        return GeminiRerankerProvider()
    else:
        return FlashRankRerankerProvider()
