import sqlite3
import struct
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.providers.embeddings import (
    EmbeddingProvider,
    GeminiEmbeddingProvider,
    VoyageEmbeddingProvider,
    EmbeddingDiskCache,
)
from src.providers.key_rotator import GeminiKeyRotator


def test_embedding_provider_interface():
    """Verify provider classes satisfy the abstract base class interface."""
    assert issubclass(VoyageEmbeddingProvider, EmbeddingProvider)
    assert issubclass(GeminiEmbeddingProvider, EmbeddingProvider)


def test_voyage_provider_initialization():
    """Verify Voyage AI provider initialization and default dimensions."""
    provider = VoyageEmbeddingProvider(api_key="mock-key-for-test", model="voyage-3-large")
    assert provider.dimension == 1024
    assert provider.model == "voyage-3-large"


def test_embedding_disk_cache(tmp_path: Path):
    """Test SQLite disk cache persistence, vector packing, and hit/miss behavior."""
    cache_db = tmp_path / "test_cache.sqlite"
    cache = EmbeddingDiskCache(db_path=cache_db, dimension=1024)

    # Cache miss
    assert cache.get_embedding("Non-existent text") is None

    # Cache insert
    text = "The Ashen Vanguard held the breach."
    mock_vector = [0.123] * 1024
    cache.set_embeddings([text], [mock_vector])

    # Cache hit
    retrieved = cache.get_embedding(text)
    assert retrieved is not None
    assert len(retrieved) == 1024
    assert pytest.approx(retrieved[0], rel=1e-5) == 0.123
    assert pytest.approx(retrieved[1023], rel=1e-5) == 0.123


def test_gemini_embedding_disk_cache_zero_api_calls(tmp_path: Path):
    """Verify that when texts are already in disk cache, 0 API calls are made."""
    cache_db = tmp_path / "test_gemini_cache.sqlite"
    provider = GeminiEmbeddingProvider(api_keys=["fake-key-1"])
    provider.cache = EmbeddingDiskCache(db_path=cache_db, dimension=1024)

    texts = [f"Chunk number {i}" for i in range(10)]
    precomputed = [[float(i)] * 1024 for i in range(10)]
    provider.cache.set_embeddings(texts, precomputed)

    # Call embed_texts: with cache full, it should return vectors without touching genai
    with patch("google.genai.Client") as mock_client:
        results = provider.embed_texts(texts, batch_size=5)
        assert len(results) == 10
        assert mock_client.call_count == 0
        for i, vec in enumerate(results):
            assert len(vec) == 1024
            assert vec[0] == float(i)


def test_gemini_embedding_rate_limit_retry_does_not_exhaust(tmp_path: Path):
    """Verify 429 rate limit triggers temporary backoff without marking key exhausted."""
    provider = GeminiEmbeddingProvider(api_keys=["fake-key-rate-limit"])
    provider.cache = EmbeddingDiskCache(db_path=tmp_path / "cache.sqlite", dimension=1024)

    mock_res = MagicMock()
    mock_emb = MagicMock()
    mock_emb.values = [0.5] * 1024
    mock_res.embeddings = [mock_emb]

    # First attempt raises 429, second attempt succeeds
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("429 RESOURCE_EXHAUSTED: Rate limit exceeded")
        return mock_res

    with patch("google.genai.Client") as mock_client_cls, patch("time.sleep") as mock_sleep:
        mock_instance = MagicMock()
        mock_instance.models.embed_content.side_effect = side_effect
        mock_client_cls.return_value = mock_instance

        results = provider.embed_texts(["Test text"], batch_size=1)
        assert len(results) == 1
        assert len(results[0]) == 1024
        # Key must NOT be exhausted
        assert provider.rotator.has_available_keys() is True
        assert mock_sleep.called
