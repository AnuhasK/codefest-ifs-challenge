import time
import os
from abc import ABC, abstractmethod
from typing import List, Optional
import voyageai

from src.config import VOYAGE_API_KEY, GEMINI_API_KEYS, EMBEDDING_MODEL, EMBEDDING_DIMENSION
from src.providers.key_rotator import GeminiKeyRotator


class EmbeddingProvider(ABC):
    """Abstract base class for all embedding model providers."""

    @abstractmethod
    def embed_texts(self, texts: List[str], batch_size: int = 16) -> List[List[float]]:
        """Generate document embeddings for a batch of texts."""
        pass

    @abstractmethod
    def embed_query(self, query: str) -> List[float]:
        """Generate a single query embedding (asymmetric retrieval)."""
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the vector dimensionality."""
        pass


class VoyageEmbeddingProvider(EmbeddingProvider):
    """
    Voyage AI embedding provider (e.g. voyage-3-large, 1024 dims).
    Includes intelligent adaptive rate limit backoff for unpaid tier (3 RPM / 10K TPM)
    and standard tier (300 RPM).
    """

    def __init__(self, api_key: Optional[str] = None, model: str = EMBEDDING_MODEL):
        self.api_key = api_key or VOYAGE_API_KEY
        if not self.api_key:
            raise ValueError("VOYAGE_API_KEY is required for VoyageEmbeddingProvider.")
        self.model = model
        self.client = voyageai.Client(api_key=self.api_key)
        self._dim = EMBEDDING_DIMENSION

    @property
    def dimension(self) -> int:
        return self._dim

    def embed_texts(self, texts: List[str], batch_size: int = 16) -> List[List[float]]:
        """
        Embed document chunks in batches using input_type='document'.
        Automatically handles rate limits with exponential / adaptive backoff.
        """
        if not texts:
            return []

        all_embeddings: List[List[float]] = []
        total_batches = (len(texts) + batch_size - 1) // batch_size

        for b_idx, i in enumerate(range(0, len(texts), batch_size), start=1):
            batch = texts[i : i + batch_size]
            cleaned_batch = [t if t and t.strip() else " " for t in batch]

            retries = 10
            for attempt in range(retries):
                try:
                    result = self.client.embed(
                        cleaned_batch,
                        model=self.model,
                        input_type="document",
                    )
                    all_embeddings.extend(result.embeddings)
                    if b_idx % 5 == 0 or b_idx == total_batches:
                        print(f"  [Voyage AI] Embedded {len(all_embeddings)}/{len(texts)} chunks (batch {b_idx}/{total_batches})...", flush=True)
                    break
                except Exception as e:
                    err_msg = str(e).lower()
                    if "rate" in err_msg or "429" in err_msg or "tpm" in err_msg or "rpm" in err_msg:
                        wait_seconds = 21 + (attempt * 5)
                        print(f"  [Voyage AI Rate Limit] Batch {b_idx}/{total_batches} hit rate limit. Waiting {wait_seconds}s before retry (attempt {attempt+1}/{retries})...", flush=True)
                        time.sleep(wait_seconds)
                    else:
                        if attempt == retries - 1:
                            raise RuntimeError(f"Failed embedding batch with Voyage AI: {e}") from e
                        time.sleep(2 ** attempt)

        return all_embeddings

    def embed_query(self, query: str) -> List[float]:
        """Embed a search query using input_type='query'."""
        cleaned_query = query.strip() if query and query.strip() else " "
        retries = 10
        for attempt in range(retries):
            try:
                result = self.client.embed(
                    [cleaned_query],
                    model=self.model,
                    input_type="query",
                )
                return result.embeddings[0]
            except Exception as e:
                err_msg = str(e).lower()
                if "rate" in err_msg or "429" in err_msg:
                    time.sleep(21)
                else:
                    if attempt == retries - 1:
                        raise RuntimeError(f"Failed embedding query with Voyage AI: {e}") from e
                    time.sleep(2 ** attempt)
        return []


class GeminiEmbeddingProvider(EmbeddingProvider):
    """Google Gemini embedding provider fallback (text-embedding-004, 768 dims) with key rotation."""

    def __init__(self, api_keys: Optional[List[str]] = None, model: str = "text-embedding-004"):
        from google import genai
        keys = api_keys or GEMINI_API_KEYS
        self.rotator = GeminiKeyRotator(keys)
        self.model = model
        self._dim = 768

    @property
    def dimension(self) -> int:
        return self._dim

    def embed_texts(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        if not texts:
            return []
        from google import genai
        embeddings: List[List[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            for t in batch:
                content = t.strip() if t and t.strip() else " "
                client = genai.Client(api_key=self.rotator.next_key())
                res = client.models.embed_content(
                    model=self.model,
                    contents=content,
                )
                embeddings.append(res.embedding.values)
        return embeddings

    def embed_query(self, query: str) -> List[float]:
        from google import genai
        content = query.strip() if query and query.strip() else " "
        client = genai.Client(api_key=self.rotator.next_key())
        res = client.models.embed_content(
            model=self.model,
            contents=content,
        )
        return res.embedding.values


def get_embedding_provider() -> EmbeddingProvider:
    """Factory creating the configured embedding provider."""
    provider_type = os.getenv("EMBEDDING_PROVIDER", "voyage").lower()
    if provider_type == "gemini" and GEMINI_API_KEYS:
        return GeminiEmbeddingProvider()
    elif VOYAGE_API_KEY:
        return VoyageEmbeddingProvider()
    elif GEMINI_API_KEYS:
        return GeminiEmbeddingProvider()
    else:
        raise ValueError("No embedding API key found in configuration (VOYAGE_API_KEY or GEMINI_API_KEYS).")
