import itertools
import threading
import time
import logging
from typing import List, Dict, Optional, Any
from datetime import date

from src.config import (
    GEMINI_API_KEYS,
    GEMINI_MAX_RPM_PER_KEY,
    GEMINI_MAX_DAILY_PER_KEY,
    GEMINI_MAX_INPUT_TOKENS,
)

logger = logging.getLogger(__name__)


class GeminiKeyRotator:
    """
    Thread-safe, rate-limiting API key rotator for Gemini API calls.

    Enforces:
    - Max Requests Per Minute (RPM) per key (default: 5)
    - Peak Daily Requests per key (default: 20)
    - Max Input Tokens per request (default: 100,000)
    - Auto-cooldown when rate limited, and fallback when daily quota is reached.
    """

    def __init__(
        self,
        api_keys: Optional[List[str]] = None,
        max_rpm: int = GEMINI_MAX_RPM_PER_KEY,
        max_daily: int = GEMINI_MAX_DAILY_PER_KEY,
        max_tokens: int = GEMINI_MAX_INPUT_TOKENS,
    ):
        raw_keys = api_keys if api_keys is not None else GEMINI_API_KEYS
        clean_keys = [
            k.strip()
            for k in raw_keys
            if k and k.strip() and not k.strip().startswith("your_")
        ]
        self._keys = clean_keys
        self._cycle = itertools.cycle(clean_keys) if clean_keys else None
        self._lock = threading.Lock()

        self.max_rpm = max_rpm
        self.max_daily = max_daily
        self.max_tokens = max_tokens

        self._request_timestamps: Dict[str, List[float]] = {k: [] for k in self._keys}
        self._daily_counts: Dict[str, int] = {k: 0 for k in self._keys}
        self._exhausted_keys: Dict[str, bool] = {k: False for k in self._keys}
        self._current_date = date.today()

    def _reset_if_new_day(self) -> None:
        """Reset daily counters if calendar date changed."""
        today = date.today()
        if today != self._current_date:
            self._current_date = today
            self._daily_counts = {k: 0 for k in self._keys}
            self._exhausted_keys = {k: False for k in self._keys}
            logger.info("New calendar day detected. Reset daily quota counters.")

    def next_key(self) -> str:
        """
        Get next available API key in round-robin sequence that respects:
        1. Peak daily request limit (<= 20 reqs/day)
        2. Max requests per minute (<= 5 RPM)
        
        If all unexhausted keys are currently at their 5 RPM limit, waits
        for the earliest key slot to open up before returning.
        Returns empty string if all keys have exhausted their daily quota.
        """
        if not self._keys:
            return ""

        with self._lock:
            self._reset_if_new_day()

            # Filter out keys that have reached the daily limit
            available_keys = [
                k
                for k in self._keys
                if not self._exhausted_keys.get(k, False)
                and self._daily_counts.get(k, 0) < self.max_daily
            ]

            if not available_keys:
                logger.warning(
                    "All configured Gemini API keys have exhausted their daily quota of %d requests.",
                    self.max_daily,
                )
                return ""

            # Attempt round-robin among available keys
            attempts = 0
            while attempts < len(available_keys) * 2:
                attempts += 1
                candidate = next(self._cycle)
                if candidate not in available_keys:
                    continue

                now = time.time()
                # Clean up timestamps older than 60s
                self._request_timestamps[candidate] = [
                    t for t in self._request_timestamps[candidate] if now - t < 60.0
                ]

                # If this candidate has capacity under 5 RPM, use it!
                if len(self._request_timestamps[candidate]) < self.max_rpm:
                    self._request_timestamps[candidate].append(now)
                    self._daily_counts[candidate] += 1
                    return candidate

            # If all available keys are currently rate-limited (at 5 RPM),
            # calculate minimum wait time across available keys
            now = time.time()
            min_wait = 60.0
            best_candidate = available_keys[0]

            for k in available_keys:
                ts = self._request_timestamps[k]
                if ts:
                    earliest = ts[0]
                    wait_for_k = 60.0 - (now - earliest)
                    if wait_for_k < min_wait:
                        min_wait = wait_for_k
                        best_candidate = k

            wait_time = max(0.1, min_wait + 0.1)
            logger.info(
                "Gemini keys at 5 RPM rate limit. Pausing for %.1fs until key slot opens...",
                wait_time,
            )
            time.sleep(wait_time)

            now = time.time()
            self._request_timestamps[best_candidate] = [
                t for t in self._request_timestamps[best_candidate] if now - t < 60.0
            ]
            self._request_timestamps[best_candidate].append(now)
            self._daily_counts[best_candidate] += 1
            return best_candidate

    def mark_exhausted(self, key: str, reason: str = "Quota exceeded") -> None:
        """Mark a specific key as exhausted for the day (e.g. on 429 quota error)."""
        with self._lock:
            if key in self._keys:
                self._exhausted_keys[key] = True
                self._daily_counts[key] = self.max_daily
                masked = key[:8] + "..." if len(key) > 8 else key
                logger.warning("Marked key %s as exhausted (%s).", masked, reason)

    def has_available_keys(self) -> bool:
        """Check if any keys remain with daily quota available."""
        with self._lock:
            self._reset_if_new_day()
            return any(
                not self._exhausted_keys.get(k, False)
                and self._daily_counts.get(k, 0) < self.max_daily
                for k in self._keys
            )

    @staticmethod
    def enforce_token_limit(text: str, max_tokens: int = GEMINI_MAX_INPUT_TOKENS) -> str:
        """
        Ensure prompt does not exceed peak input token limit (default: 100,000 tokens).
        Uses a conservative ratio of ~3.5 characters per token.
        """
        if not text:
            return text
        max_chars = int(max_tokens * 3.5)
        if len(text) > max_chars:
            logger.warning(
                "Prompt length (%d chars) exceeds token threshold (~%d tokens). Truncating to %d chars.",
                len(text),
                max_tokens,
                max_chars,
            )
            return text[:max_chars]
        return text

    def get_status_summary(self) -> Dict[str, Any]:
        """Return a diagnostic summary of key usage and limits."""
        with self._lock:
            now = time.time()
            summary = {}
            for k in self._keys:
                active_rpm = len([t for t in self._request_timestamps[k] if now - t < 60.0])
                masked = k[:6] + "..." + k[-4:] if len(k) > 10 else k
                summary[masked] = {
                    "used_today": self._daily_counts[k],
                    "remaining_today": max(0, self.max_daily - self._daily_counts[k]),
                    "rpm_last_minute": active_rpm,
                    "is_exhausted": self._exhausted_keys[k],
                }
            return summary

    @property
    def key_count(self) -> int:
        """Return the total number of configured API keys."""
        return len(self._keys)

    @property
    def available_count(self) -> int:
        """Return number of keys currently not exhausted today."""
        with self._lock:
            self._reset_if_new_day()
            return sum(
                1
                for k in self._keys
                if not self._exhausted_keys.get(k, False)
                and self._daily_counts.get(k, 0) < self.max_daily
            )

    @property
    def keys(self) -> List[str]:
        """Return list of all configured API keys."""
        return list(self._keys)


# Global singleton instance for shared process-wide rate limiting
_shared_rotator: Optional[GeminiKeyRotator] = None


def get_shared_gemini_rotator() -> GeminiKeyRotator:
    """Get or create the process-wide shared GeminiKeyRotator."""
    global _shared_rotator
    if _shared_rotator is None:
        _shared_rotator = GeminiKeyRotator()
    return _shared_rotator
