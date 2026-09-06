import os
import json
import time
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, Type
from pydantic import BaseModel
from PIL import Image
from google import genai
from google.genai import types

from src.config import GEMINI_API_KEYS, LLM_MODEL, LLM_MODEL_STRONG
from src.providers.key_rotator import GeminiKeyRotator


class LLMResponse(BaseModel):
    """Encapsulates response text, token usage, and model metadata."""
    content: str
    tokens_used: int = 0
    model: str = ""


class LLMProvider(ABC):
    """Abstract interface for LLM operations."""

    @abstractmethod
    def generate(
        self, prompt: str, system_prompt: str = "", model: Optional[str] = None
    ) -> LLMResponse:
        """Generate open text response."""
        pass

    @abstractmethod
    def generate_structured(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        system_prompt: str = "",
        model: Optional[str] = None,
    ) -> Any:
        """Generate structured response conforming to a Pydantic schema."""
        pass

    @abstractmethod
    def describe_image(
        self, image_path: str, prompt: str, model: Optional[str] = None
    ) -> str:
        """Describe or extract structured data from an image."""
        pass


class GeminiLLMProvider(LLMProvider):
    """Gemini LLM provider with key rotation and multimodal support."""

    def __init__(
        self,
        api_keys: Optional[List[str]] = None,
        default_model: str = LLM_MODEL,
        strong_model: str = LLM_MODEL_STRONG,
    ):
        if api_keys is not None:
            self.rotator = GeminiKeyRotator(api_keys)
        else:
            from src.providers.key_rotator import get_shared_gemini_rotator
            self.rotator = get_shared_gemini_rotator()

        self.default_model = default_model
        self.strong_model = strong_model

    def generate(
        self, prompt: str, system_prompt: str = "", model: Optional[str] = None
    ) -> LLMResponse:
        chosen_model = model or self.default_model
        safe_prompt = self.rotator.enforce_token_limit(prompt)
        safe_system = self.rotator.enforce_token_limit(system_prompt) if system_prompt else None

        config = types.GenerateContentConfig(
            system_instruction=safe_system,
            temperature=0.2,
        )

        retries = max(3, self.rotator.available_count)
        for attempt in range(retries):
            key = self.rotator.next_key()
            if not key:
                break
            try:
                client = genai.Client(
                    api_key=key,
                    http_options=types.HttpOptions(timeout=30000),
                )
                response = client.models.generate_content(
                    model=chosen_model,
                    contents=safe_prompt,
                    config=config,
                )
                text = response.text or ""
                token_count = 0
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    token_count = getattr(response.usage_metadata, "total_token_count", 0)
                return LLMResponse(content=text, tokens_used=token_count, model=chosen_model)
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    # ONLY mark exhausted if error message explicitly denotes daily quota exhaustion.
                    if any(
                        term in err_str.lower()
                        for term in (
                            "daily",
                            "per day",
                            "perday",
                            "perproject",
                            "quotafailure",
                            "free_tier_requests",
                            "quota_limit_value: 0",
                            "day limit",
                        )
                    ):
                        self.rotator.mark_exhausted(key, reason="Daily quota exceeded")
                    else:
                        time.sleep(15.0 + (attempt * 2.0))
                    continue
                if any(err_code in err_str for err_code in ("404", "400", "403", "NOT_FOUND", "API_KEY_INVALID", "PERMISSION_DENIED")):
                    self.rotator.mark_exhausted(key, reason=f"Invalid/inactive key: {err_str[:60]}")
                    continue
                if attempt == retries - 1:
                    raise RuntimeError(f"Gemini generate call failed: {e}") from e
                time.sleep(2 ** min(attempt, 3))


        return LLMResponse(content="", tokens_used=0, model=chosen_model)

    def generate_structured(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        system_prompt: str = "",
        model: Optional[str] = None,
    ) -> Any:
        chosen_model = model or self.default_model
        safe_prompt = self.rotator.enforce_token_limit(prompt)
        safe_system = self.rotator.enforce_token_limit(system_prompt) if system_prompt else None

        config = types.GenerateContentConfig(
            system_instruction=safe_system,
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=0.1,
        )

        retries = max(3, self.rotator.available_count)
        for attempt in range(retries):
            key = self.rotator.next_key()
            if not key:
                break
            try:
                client = genai.Client(
                    api_key=key,
                    http_options=types.HttpOptions(timeout=30000),
                )
                response = client.models.generate_content(
                    model=chosen_model,
                    contents=safe_prompt,
                    config=config,
                )
                text = response.text or "{}"
                if hasattr(response, "parsed") and response.parsed:
                    return response.parsed
                data = json.loads(text)
                return response_schema.model_validate(data)
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    if any(
                        term in err_str.lower()
                        for term in (
                            "daily",
                            "per day",
                            "perday",
                            "perproject",
                            "quotafailure",
                            "free_tier_requests",
                            "quota_limit_value: 0",
                            "day limit",
                        )
                    ):
                        self.rotator.mark_exhausted(key, reason="Daily quota exceeded")
                    else:
                        time.sleep(15.0 + (attempt * 2.0))
                    continue
                if any(err_code in err_str for err_code in ("404", "400", "403", "NOT_FOUND", "API_KEY_INVALID", "PERMISSION_DENIED")):
                    self.rotator.mark_exhausted(key, reason=f"Invalid/inactive key: {err_str[:60]}")
                    continue
                if attempt == retries - 1:
                    raise RuntimeError(f"Gemini structured generate call failed: {e}") from e
                time.sleep(2 ** min(attempt, 3))

    def describe_image(
        self, image_path: str, prompt: str, model: Optional[str] = None
    ) -> str:
        chosen_model = model or self.default_model
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found at {image_path}")

        safe_prompt = self.rotator.enforce_token_limit(prompt)
        image = Image.open(image_path)
        retries = max(3, self.rotator.available_count)

        for attempt in range(retries):
            key = self.rotator.next_key()
            if not key:
                break
            try:
                client = genai.Client(api_key=key)
                response = client.models.generate_content(
                    model=chosen_model,
                    contents=[image, safe_prompt],
                )
                return response.text or ""
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    if any(term in err_str.lower() for term in ("daily", "per day", "quota_limit_value: 0", "day limit")):
                        self.rotator.mark_exhausted(key, reason="Daily quota exceeded")
                    else:
                        time.sleep(15.0 + (attempt * 2.0))
                    continue
                if any(err_code in err_str for err_code in ("404", "400", "403", "NOT_FOUND", "API_KEY_INVALID", "PERMISSION_DENIED")):
                    self.rotator.mark_exhausted(key, reason=f"Invalid/inactive key: {err_str[:60]}")
                    continue
                if attempt == retries - 1:
                    raise RuntimeError(f"Gemini vision call failed for {image_path}: {e}") from e
                time.sleep(2 ** min(attempt, 3))

        return ""


def get_llm_provider() -> LLMProvider:
    """Factory creating the default Gemini LLM provider."""
    return GeminiLLMProvider()

