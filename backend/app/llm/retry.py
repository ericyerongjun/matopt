"""Model-request retry policy, ported from Codex.

Codex classifies every sampling failure by type (``protocol/src/error.rs``
``retry_delay``) and retries the transient ones with exponential backoff and
jitter, preferring the server's ``Retry-After`` (``core/src/responses_retry.rs``,
``async-utils/src/backoff.rs``: 200 ms × 2^(n−1), ±10%). MatOpt used to retry
once after a fixed 1.5 s on a string match, and the OpenAI SDK runs with
``max_retries=0`` so nothing else retried either.

Classification works on the OpenAI SDK's exception types and HTTP status when
present, and falls back to the old message heuristic for adapters that raise
plain exceptions. Context-length errors are never retried as-is: they are
reported separately so the harness can compact and try again.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529})
TERMINAL_STATUS = frozenset({400, 401, 402, 403, 404, 405, 413, 415, 422})
RETRYABLE_TYPES = frozenset({"APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError",
                             "ReadTimeout", "ConnectTimeout", "ConnectError", "RemoteProtocolError", "TimeoutError"})
_CONTEXT_MARKERS = ("context_length_exceeded", "maximum context length", "context window", "too many tokens",
                    "prompt is too long", "reduce the length", "input is too long")
MAX_RETRY_AFTER = 60.0


@dataclass(frozen=True)
class RetryDecision:
    retryable: bool
    context_overflow: bool = False
    retry_after: Optional[float] = None


def _status(exc: BaseException) -> Optional[int]:
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _retry_after(exc: BaseException) -> Optional[float]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    for name in ("retry-after-ms", "retry-after"):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        seconds = value / 1000 if name.endswith("-ms") else value
        if seconds >= 0:
            return min(seconds, MAX_RETRY_AFTER)
    return None


def is_context_overflow(exc: BaseException) -> bool:
    text = f"{getattr(exc, 'code', '') or ''} {exc}".lower()
    return any(marker in text for marker in _CONTEXT_MARKERS)


def classify(exc: BaseException) -> RetryDecision:
    if is_context_overflow(exc):
        return RetryDecision(retryable=False, context_overflow=True)
    status = _status(exc)
    if status is not None:
        if status in RETRYABLE_STATUS or status >= 500:
            return RetryDecision(retryable=True, retry_after=_retry_after(exc))
        if status in TERMINAL_STATUS or 400 <= status < 500:
            return RetryDecision(retryable=False)
    if type(exc).__name__ in RETRYABLE_TYPES:
        return RetryDecision(retryable=True, retry_after=_retry_after(exc))
    message = str(exc).lower()
    return RetryDecision(retryable="timeout" in message or "connection" in message or "temporarily" in message)


def backoff_delay(attempt: int, base: float = 0.2, retry_after: Optional[float] = None,
                  cap: float = 30.0, rng: random.Random | None = None) -> float:
    """Seconds to wait before retry number ``attempt`` (1-based)."""
    if retry_after is not None:
        return retry_after
    jitter = (rng or random).uniform(0.9, 1.1)
    return min(cap, base * (2 ** max(0, attempt - 1)) * jitter)
