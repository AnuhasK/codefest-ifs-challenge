from unittest.mock import MagicMock, patch
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
    # Mark key_X exhausted (e.g. on genuine daily quota exhaustion)
    rotator.mark_exhausted("key_X", reason="Daily quota exceeded")

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


def test_llm_provider_429_does_not_mark_exhausted():
    """Verify temporary 429 does not exhaust key; retries successfully."""
    provider = GeminiLLMProvider(api_keys=["test_key_429"])

    mock_resp = MagicMock()
    mock_resp.text = "Grounded response."
    mock_resp.usage_metadata = MagicMock(total_token_count=15)

    call_count = 0

    def mock_generate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("429 RESOURCE_EXHAUSTED: Rate limit per minute exceeded")
        return mock_resp

    with patch("google.genai.Client") as mock_client_cls, patch("time.sleep"):
        mock_instance = MagicMock()
        mock_instance.models.generate_content.side_effect = mock_generate
        mock_client_cls.return_value = mock_instance

        res = provider.generate("Test prompt")
        assert res.content == "Grounded response."
        # Key must NOT be exhausted after a per-minute 429
        assert provider.rotator.has_available_keys() is True


def test_llm_provider_daily_quota_error_marks_exhausted():
    """Verify explicit daily quota error marks key exhausted."""
    provider = GeminiLLMProvider(api_keys=["test_key_daily"])

    def mock_generate(*args, **kwargs):
        raise Exception("429 RESOURCE_EXHAUSTED: daily quota exceeded for project")

    with patch("google.genai.Client") as mock_client_cls, patch("time.sleep"):
        mock_instance = MagicMock()
        mock_instance.models.generate_content.side_effect = mock_generate
        mock_client_cls.return_value = mock_instance

        res = provider.generate("Test prompt")
        # Key SHOULD be marked exhausted
        assert provider.rotator.has_available_keys() is False
