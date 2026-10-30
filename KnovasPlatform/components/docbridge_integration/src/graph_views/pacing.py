"""A token bucket that keeps the views' reads under the tenant's rate limit.

The Secure API allows 600 calls a minute per tenant, shared by everything that
holds the certificate (the loader in CI, every Platform worker). The views take
half of that by default and burst for a board, then refill. One bucket per
process: the limit is the tenant's, not a user's.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional


class TokenBucket:
    def __init__(self, rate_per_minute: float, burst: int,
                 clock: Optional[Callable[[], float]] = None,
                 sleep: Optional[Callable[[float], None]] = None):
        self._rate = max(float(rate_per_minute), 1.0) / 60.0
        self._capacity = max(int(burst), 1)
        self._tokens = float(self._capacity)
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._last = self._clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Take one token, waiting for the refill when the bucket is empty."""
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(self._capacity,
                                   self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            self._sleep(wait)


_shared: Optional[TokenBucket] = None
_shared_lock = threading.Lock()


def shared_bucket(rate_per_minute: float, burst: int) -> TokenBucket:
    """The process-wide bucket, created on first use."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = TokenBucket(rate_per_minute, burst)
        return _shared
