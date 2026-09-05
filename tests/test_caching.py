import json
from pathlib import Path
from uuid import uuid4
from src.providers.embeddings import EmbeddingDiskCache
from src.ingestion.images import _load_image_cache, _save_image_cache
from src.ingestion.ocr import _load_ocr_cache, _save_ocr_cache
from src.ingestion.contextualization import _load_prefix_cache, _save_prefix_cache, generate_llm_prefix
from src.ingestion.entities import _load_entity_cache, _save_entity_cache, classify_unknown_entities_with_llm
from src.models.document import Chunk
from src.providers.llm_provider import LLMProvider, LLMResponse


class MockLLM(LLMProvider):
    def __init__(self):
        self.generate_call_count = 0

    def generate(self, prompt: str, system_prompt: str = "", model=None) -> LLMResponse:
        self.generate_call_count += 1
        return LLMResponse(
            content='{"entities": [{"mention": "Lord Drovenath", "canonical_name": "Drovenath", "type": "PERSON", "confidence": 0.95}]}',
            tokens_used=20,
            model="mock",
        )

    def generate_structured(self, prompt: str, response_schema, system_prompt="", model=None):
        return None

    def describe_image(self, image_path: str, prompt: str, model=None) -> str:
        return "mock description"


def test_ocr_disk_cache_persistence(tmp_path: Path, monkeypatch):
    """Verify OCR results are saved to disk and reloaded without API calls."""
    test_cache_file = tmp_path / "test_ocr_cache.json"
    monkeypatch.setattr("src.ingestion.ocr.OCR_CACHE_FILE", test_cache_file)

    # Empty initially
    cache = _load_ocr_cache()
    assert len(cache) == 0

    # Save transcription
    cache["scanned_doc.pdf_p1"] = "Transcribed text from page 1."
    _save_ocr_cache(cache)
    assert test_cache_file.exists()

    # Reload from disk
    reloaded = _load_ocr_cache()
    assert reloaded.get("scanned_doc.pdf_p1") == "Transcribed text from page 1."


def test_prefix_disk_cache_persistence(tmp_path: Path, monkeypatch):
    """Verify contextual prefixes are cached on disk and avoid duplicate LLM calls."""
    test_cache_file = tmp_path / "test_prefix_cache.json"
    monkeypatch.setattr("src.ingestion.contextualization.PREFIX_CACHE_FILE", test_cache_file)

    chunk = Chunk(
        id=uuid4(),
        document_id=uuid4(),
        content="He ordered the retreat across the river.",
    )
    mock_llm = MockLLM()

    # First call: calls LLM and saves to disk cache
    prefix1 = generate_llm_prefix(
        chunk=chunk,
        document_title="Chronicle of Ash",
        llm=mock_llm,
    )
    assert mock_llm.generate_call_count == 1
    assert test_cache_file.exists()

    # Second call: loads from cache, LLM call count remains 1!
    prefix2 = generate_llm_prefix(
        chunk=chunk,
        document_title="Chronicle of Ash",
        llm=mock_llm,
    )
    assert mock_llm.generate_call_count == 1
    assert prefix1 == prefix2


def test_entity_extraction_disk_cache_persistence(tmp_path: Path, monkeypatch):
    """Verify targeted entity extraction is cached on disk and avoids duplicate LLM calls."""
    test_cache_file = tmp_path / "test_entity_cache.json"
    monkeypatch.setattr("src.ingestion.entities.ENTITY_CACHE_FILE", test_cache_file)

    chunk = Chunk(
        id=uuid4(),
        document_id=uuid4(),
        content="Meeting with Lord Drovenath at the gate.",
    )
    mock_llm = MockLLM()

    # First call: calls LLM and writes to cache
    entities1 = classify_unknown_entities_with_llm(
        chunk=chunk,
        candidates=["Lord Drovenath"],
        llm=mock_llm,
    )
    assert mock_llm.generate_call_count == 1
    assert test_cache_file.exists()
    assert len(entities1) == 1
    assert entities1[0].name == "Drovenath"

    # Second call: loads from cache, LLM call count remains 1!
    entities2 = classify_unknown_entities_with_llm(
        chunk=chunk,
        candidates=["Lord Drovenath"],
        llm=mock_llm,
    )
    assert mock_llm.generate_call_count == 1
    assert len(entities2) == 1
    assert entities2[0].name == "Drovenath"
