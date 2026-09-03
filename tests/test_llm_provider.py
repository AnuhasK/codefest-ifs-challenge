from pydantic import BaseModel
from src.config import LLM_MODEL
from src.providers.key_rotator import GeminiKeyRotator
from src.providers.llm_provider import GeminiLLMProvider


def test_key_rotator_round_robin():
    keys = ["key_A", "key_B", "key_C"]
    rotator = GeminiKeyRotator(keys, max_rpm=5, max_daily=20)
    assert rotator.key_count == 3
    assert rotator.next_key() == "key_A"
    assert rotator.next_key() == "key_B"
    assert rotator.next_key() == "key_C"
    assert rotator.next_key() == "key_A"


def test_key_rotator_empty():
    rotator = GeminiKeyRotator([])
    assert rotator.key_count == 0
    assert rotator.next_key() == ""


def test_key_rotator_daily_limit():
    keys = ["key_1", "key_2"]
    rotator = GeminiKeyRotator(keys, max_rpm=50, max_daily=3)

    # Use key_1 and key_2 3 times each (total 6 requests)
    for _ in range(3):
        assert rotator.next_key() == "key_1"
        assert rotator.next_key() == "key_2"

    # Both keys have hit 3 requests (max_daily)
    assert rotator.available_count == 0
    assert rotator.has_available_keys() is False
    assert rotator.next_key() == ""


def test_key_rotator_mark_exhausted():
    keys = ["key_X", "key_Y"]
    rotator = GeminiKeyRotator(keys, max_rpm=5, max_daily=20)

    assert rotator.next_key() == "key_X"
    # Mark key_X exhausted (e.g. on 429 Resource Exhausted)
    rotator.mark_exhausted("key_X", reason="429 rate limit")

    # Now only key_Y should be returned
    assert rotator.next_key() == "key_Y"
    assert rotator.next_key() == "key_Y"


def test_key_rotator_token_limit():
    rotator = GeminiKeyRotator(["key_1"], max_tokens=100)
    # 100 tokens ~ 350 chars
    long_text = "A" * 1000
    safe_text = rotator.enforce_token_limit(long_text, max_tokens=100)
    assert len(safe_text) == 350
    assert len(safe_text) < len(long_text)


def test_llm_provider_initialization():
    provider = GeminiLLMProvider(api_keys=["test_key_1", "test_key_2"])
    assert provider.rotator.key_count == 2
    assert provider.default_model == LLM_MODEL
