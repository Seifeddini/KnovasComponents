"""Ingest rate limiter for Semantix HTTP calls (fail closed).

Primary gate: transmitted characters per minute (aligns with SemantixBenchmark
chars/s ramp). Secondary gate: HTTP requests per minute (safety net for
multi-part documents).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from util.rate_limiter import RateLimiter, TokenBucketStrategy

logger = logging.getLogger(__name__)

_CHARS_IDENTITY = "rc-sync-ingest-chars"
_REQUESTS_IDENTITY = "rc-sync-ingest-requests"
_chars_limiter: Optional[RateLimiter] = None
_requests_limiter: Optional[RateLimiter] = None


def configure(
    max_per_minute: int,
    burst: int,
    *,
    max_chars_per_minute: Optional[int] = None,
    burst_chars: Optional[int] = None,
) -> None:
    global _chars_limiter, _requests_limiter
    refill = max_per_minute / 60.0
    _requests_limiter = RateLimiter(
        TokenBucketStrategy(max_tokens=burst, refill_rate=refill, fail_open=False)
    )
    if max_chars_per_minute is not None and max_chars_per_minute > 0:
        chars_burst = burst_chars if burst_chars is not None else max_chars_per_minute
        chars_refill = max_chars_per_minute / 60.0
        _chars_limiter = RateLimiter(
            TokenBucketStrategy(
                max_tokens=max(chars_burst, 1),
                refill_rate=chars_refill,
                fail_open=False,
            )
        )
    else:
        _chars_limiter = None


def _wait_for(limiter: RateLimiter, identity: str, cost: float, *, max_wait_seconds: float) -> bool:
    deadline = time.monotonic() + max_wait_seconds
    while time.monotonic() < deadline:
        try:
            if limiter.acquire(identity, cost):
                return True
        except Exception:
            logger.error("Ingest rate limiter error")
            return False
        time.sleep(min(5.0, deadline - time.monotonic()))
    return False


def acquire_chars(cost: int, *, max_wait_seconds: float = 300.0) -> bool:
    """Acquire `cost` transmitted characters before an HTTP ingest call."""
    if cost <= 0:
        return True
    if _chars_limiter is None:
        return acquire_request(max_wait_seconds=max_wait_seconds)
    return _wait_for(_chars_limiter, _CHARS_IDENTITY, float(cost), max_wait_seconds=max_wait_seconds)


def acquire_request(*, max_wait_seconds: float = 300.0) -> bool:
    """Acquire one HTTP request slot (init or transmit)."""
    if _requests_limiter is None:
        logger.error("Ingest rate limiter not configured")
        return False
    return _wait_for(_requests_limiter, _REQUESTS_IDENTITY, 1.0, max_wait_seconds=max_wait_seconds)


def acquire(*, max_wait_seconds: float = 300.0) -> bool:
    """Backward-compatible alias: one HTTP request token."""
    return acquire_request(max_wait_seconds=max_wait_seconds)
