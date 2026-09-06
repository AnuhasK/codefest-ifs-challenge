import hashlib
import os
from pathlib import Path
import sqlite3
import struct
import time
from abc import ABC, abstractmethod
from typing import List, Optional
import voyageai

from src.config import (
    BASE_DIR,
    VOYAGE_API_KEY,
    GEMINI_API_KEYS,
    EMBEDDING_MODEL,
    EMBEDDING_DIMENSION,
    EMBEDDINGS_CACHE_PATH,
)
from src.providers.key_rotator import GeminiKeyRotator

CACHE_DB_PATH = EMBEDDINGS_CACHE_PATH


class EmbeddingDiskCache:
    """
    Persistent SQLite disk cache for vector embeddings.
    Prevents redundant API calls on reruns or network restarts.
    """

    def __init__(self, db_path: Path = CACHE_DB_PATH, dimension: int = EMBEDDING_DIMENSION):
        self.db_path = db_path
        self.dimension = dimension
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    text_hash TEXT PRIMARY KEY,
                    vector BLOB
                )
                """
            )

    def get_embedding(self, text: str) -> Optional[List[float]]:
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.cursor()
            cur.execute("SELECT vector FROM embeddings WHERE text_hash = ?", (h,))
            row = cur.fetchone()
            if row:
                return list(struct.unpack(f"{self.dimension}f", row[0]))
        return None

    def set_embeddings(self, texts: List[str], vectors: List[List[float]]) -> None:
        records = []
        for t, vec in zip(texts, vectors):
            h = hashlib.sha256(t.encode("utf-8")).hexdigest()
            blob = struct.pack(f"{len(vec)}f", *vec)
            records.append((h, blob))
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings (text_hash, vector) VALUES (?, ?)",
                records,
            )


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
        self.cache = EmbeddingDiskCache(dimension=self._dim)

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
        """Embed a search query using input_type='query' with disk caching."""
        cleaned_query = query.strip() if query and query.strip() else " "
        cache_key = f"voyage:{self.model}:query:{cleaned_query}"
        cached = self.cache.get_embedding(cache_key)
        if cached is not None:
            return cached

        retries = 10
        for attempt in range(retries):
            try:
                result = self.client.embed(
                    [cleaned_query],
                    model=self.model,
                    input_type="query",
                )
                vec = result.embeddings[0]
                self.cache.set_embeddings([cache_key], [vec])
                return vec
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
    """
    Google Gemini embedding provider (gemini-embedding-001, 1024 dims).
    Supports 1024-dimensional truncation (matching Postgres schema),
    fast multi-text batching, key rotation, persistent disk caching, and rate-limit backoff.
    """

    def __init__(
        self,
        api_keys: Optional[List[str]] = None,
        model: str = "gemini-embedding-001",
        dimension: int = EMBEDDING_DIMENSION,
    ):
        keys = api_keys or GEMINI_API_KEYS
        if not keys:
            raise ValueError("GEMINI_API_KEYS is required for GeminiEmbeddingProvider.")
        # Free Tier Rate Protection:
        # - Google limit: 100 RPM, 30,000 TPM, 1,000 RPD
        # - With batch_size=5 (~1,750 tokens), max_rpm=8 guarantees peak TPM <= 24,576 TPM (< 30k TPM)
        # - max_daily=1000 allows all ~177 requests per key to finish without hitting daily caps
        self.rotator = GeminiKeyRotator(
            keys,
            max_rpm=8,
            max_daily=1000,
            max_tokens=30000,
        )
        self.model = model
        self._dim = dimension
        self.cache = EmbeddingDiskCache(dimension=dimension)

    @property
    def dimension(self) -> int:
        return self._dim

    def embed_texts(self, texts: List[str], batch_size: int = 5) -> List[List[float]]:
        if not texts:
            return []

        results: List[Optional[List[float]]] = [None] * len(texts)
        missing_indices: List[int] = []
        missing_texts: List[str] = []

        # 1. Check disk cache first
        for idx, t in enumerate(texts):
            cached = self.cache.get_embedding(t)
            if cached is not None:
                results[idx] = cached
            else:
                missing_indices.append(idx)
                missing_texts.append(t)

        if not missing_texts:
            print(f"  [Gemini Embedding] All {len(texts)} embeddings loaded from disk cache (0 API calls).", flush=True)
            return [r for r in results if r is not None]

        if len(missing_texts) < len(texts):
            print(
                f"  [Gemini Embedding] Loaded {len(texts) - len(missing_texts)}/{len(texts)} embeddings from disk cache. Generating remaining {len(missing_texts)}...",
                flush=True,
            )

        from google import genai
        from google.genai import types

        total_batches = (len(missing_texts) + batch_size - 1) // batch_size

        for b_idx, i in enumerate(range(0, len(missing_texts), batch_size), start=1):
            batch = missing_texts[i : i + batch_size]
            batch_indices = missing_indices[i : i + batch_size]
            cleaned_batch = [t if t and t.strip() else " " for t in batch]

            retries = 10
            for attempt in range(retries):
                key = self.rotator.next_key()
                if not key:
                    raise RuntimeError("All Gemini API keys exhausted.")

                try:
                    client = genai.Client(
                        api_key=key,
                        http_options=types.HttpOptions(timeout=60000),
                    )
                    config = types.EmbedContentConfig(output_dimensionality=self._dim)
                    res = client.models.embed_content(
                        model=self.model,
                        contents=cleaned_batch,
                        config=config,
                    )

                    batch_vecs = []
                    if hasattr(res, "embeddings") and res.embeddings:
                        for emb in res.embeddings:
                            batch_vecs.append(emb.values)
                    elif hasattr(res, "embedding") and res.embedding:
                        batch_vecs.append(res.embedding.values)

                    # Update results and commit to SQLite cache immediately
                    for orig_idx, vec in zip(batch_indices, batch_vecs):
                        results[orig_idx] = vec
                    self.cache.set_embeddings(batch, batch_vecs)

                    done_count = (len(texts) - len(missing_texts)) + min(i + batch_size, len(missing_texts))
                    if b_idx % 5 == 0 or b_idx == total_batches:
                        print(f"  [Gemini Embedding] Embedded {done_count}/{len(texts)} chunks (batch {b_idx}/{total_batches})...", flush=True)

                    # Polite 1.0s pause between batches to stay well within free tier TPM
                    time.sleep(1.0)
                    break

                except Exception as e:
                    err_str = str(e).lower()
                    if "429" in err_str or "resource_exhausted" in err_str:
                        wait_s = 15.0 + (attempt * 3.0)
                        print(f"  [Gemini Embedding] Per-minute rate limit reached. Pausing {wait_s:.0f}s before retrying (attempt {attempt+1}/{retries})...", flush=True)
                        time.sleep(wait_s)
                        continue
                    elif "503" in err_str or "unavailable" in err_str:
                        time.sleep(3.0)
                        continue
                    else:
                        if attempt == retries - 1:
                            raise RuntimeError(f"Failed embedding batch with Gemini: {e}") from e
                        time.sleep(2 ** attempt)

        return [r for r in results if r is not None]

    def embed_query(self, query: str) -> List[float]:
        """Embed a search query using asymmetric retrieval with 1024 dims and disk caching."""
        cached = self.cache.get_embedding(query)
        if cached is not None:
            return cached

        from google import genai
        from google.genai import types

        cleaned_query = query.strip() if query and query.strip() else " "
        retries = 10
        for attempt in range(retries):
            key = self.rotator.next_key()
            if not key:
                raise RuntimeError("All Gemini API keys exhausted.")

            try:
                client = genai.Client(
                    api_key=key,
                    http_options=types.HttpOptions(timeout=60000),
                )
                config = types.EmbedContentConfig(output_dimensionality=self._dim)
                res = client.models.embed_content(
                    model=self.model,
                    contents=[cleaned_query],
                    config=config,
                )
                res_vec = None
                if hasattr(res, "embeddings") and res.embeddings:
                    res_vec = res.embeddings[0].values
                elif hasattr(res, "embedding") and res.embedding:
                    res_vec = res.embedding.values

                if res_vec is not None:
                    self.cache.set_embeddings([cleaned_query], [res_vec])
                    return res_vec
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "resource_exhausted" in err_str:
                    time.sleep(10.0)
                    continue
                elif "503" in err_str or "unavailable" in err_str:
                    time.sleep(2.0)
                    continue
                else:
                    if attempt == retries - 1:
                        raise RuntimeError(f"Failed embedding query with Gemini: {e}") from e
                    time.sleep(2 ** attempt)
        return []


def get_embedding_provider() -> EmbeddingProvider:
    """
    Factory creating the configured embedding provider.
    Reads EMBEDDING_PROVIDER from config/env:
    - 'gemini' (default): GeminiEmbeddingProvider (gemini-embedding-001, 1024 dims)
    - 'voyage': VoyageEmbeddingProvider (voyage-4-large / voyage-3-large, 1024 dims)
    """
    from src.config import EMBEDDING_PROVIDER

    provider_type = EMBEDDING_PROVIDER.lower()
    if provider_type == "gemini":
        if GEMINI_API_KEYS:
            return GeminiEmbeddingProvider()
        raise ValueError("GEMINI_API_KEYS is required when EMBEDDING_PROVIDER=gemini.")
    elif provider_type == "voyage":
        if VOYAGE_API_KEY:
            return VoyageEmbeddingProvider()
        raise ValueError("VOYAGE_API_KEY is required when EMBEDDING_PROVIDER=voyage.")
    else:
        # Fallback to whichever is available
        if GEMINI_API_KEYS:
            return GeminiEmbeddingProvider()
        elif VOYAGE_API_KEY:
            return VoyageEmbeddingProvider()
        else:
            raise ValueError("No embedding API key found in configuration.")
