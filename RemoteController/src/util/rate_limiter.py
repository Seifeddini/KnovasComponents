"""Token-bucket rate limiter (vendored from knovas-software, read-only port)."""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod


class IRateLimitStrategy(ABC):
    @abstractmethod
    def is_allowed(self, identifier: str) -> bool: ...


class TokenBucketStrategy(IRateLimitStrategy):
    def __init__(self, max_tokens: int = 60, refill_rate: float = 1.0, *, fail_open: bool = True):
        if max_tokens <= 0:
            raise ValueError("max_tokens must be > 0")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be > 0")
        self._max_tokens = max_tokens
        self._refill_rate = refill_rate
        self._fail_open = fail_open
        self._buckets: dict[str, list] = {}
        self._lock = threading.Lock()

    def is_allowed(self, identifier: str) -> bool:
        try:
            now = time.monotonic()
            with self._lock:
                if identifier not in self._buckets:
                    self._buckets[identifier] = [float(self._max_tokens), now]
                bucket = self._buckets[identifier]
                elapsed = now - bucket[1]
                bucket[0] = min(self._max_tokens, bucket[0] + elapsed * self._refill_rate)
                bucket[1] = now
                if bucket[0] >= 1.0:
                    bucket[0] -= 1.0
                    return True
                return False
        except Exception:
            return self._fail_open


class RateLimiter:
    def __init__(self, strategy: IRateLimitStrategy | None = None):
        self._strategy = strategy or TokenBucketStrategy()

    def is_allowed(self, identifier: str) -> bool:
        return self._strategy.is_allowed(identifier)
