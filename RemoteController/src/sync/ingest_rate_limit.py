"""Ingest rate limiter for Semantix HTTP calls (fail closed)."""
from __future__ import annotations

import logging
import time

from util.rate_limiter import RateLimiter, TokenBucketStrategy

logger = logging.getLogger(__name__)

_IDENTITY = "rc-sync-ingest"
_limiter: RateLimiter | None = None


def configure(max_per_minute: int, burst: int) -> None:
    global _limiter
    refill = max_per_minute / 60.0
    _limiter = RateLimiter(
        TokenBucketStrategy(max_tokens=burst, refill_rate=refill, fail_open=False)
    )


def acquire(*, max_wait_seconds: float = 300.0) -> bool:
    if _limiter is None:
        logger.error("Ingest rate limiter not configured")
        return False
    deadline = time.monotonic() + max_wait_seconds
    while time.monotonic() < deadline:
        try:
            if _limiter.is_allowed(_IDENTITY):
                return True
        except Exception:
            logger.error("Ingest rate limiter error")
            return False
        time.sleep(min(5.0, deadline - time.monotonic()))
    return False
