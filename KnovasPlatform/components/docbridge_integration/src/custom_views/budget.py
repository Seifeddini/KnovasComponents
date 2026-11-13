"""A budget that refuses instead of waiting, and one read per user at a time.

Unlike ``graph_views.pacing.TokenBucket`` (which sleeps until a token is
there), ``RefusingBudget.take`` never blocks: a caller that asks for more than
is left is told how many seconds to wait and takes nothing. The web layer turns
that number into ``429`` with ``Retry-After``.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Callable, Dict, Optional, Set, Tuple


class RefusingBudget:
    def __init__(self, per_minute: int, capacity: Optional[int] = None,
                 clock: Callable[[], float] = time.monotonic):
        """Refills ``per_minute`` a minute up to ``capacity`` (default: the
        rate); starts full."""
        rate = float(max(int(per_minute), 1))
        self._capacity = float(max(int(capacity if capacity is not None else rate), 1))
        self._rate = rate / 60.0
        self._clock = clock
        self._tokens = self._capacity
        self._last = clock()
        self._lock = threading.Lock()

    def take(self, units: int) -> Optional[float]:
        """Take ``units`` and return None, or take nothing and return the
        seconds (rounded up, at least 1) until they would be there.

        More than the capacity can never be met: ValueError."""
        if units < 0:
            raise ValueError("units must not be negative")
        if units > self._capacity:
            raise ValueError("units exceed the budget's capacity")
        if units == 0:
            return None
        with self._lock:
            now = self._clock()
            self._tokens = min(self._capacity,
                               self._tokens + max(now - self._last, 0.0) * self._rate)
            self._last = now
            if self._tokens >= units:
                self._tokens -= units
                return None
            wait = (units - self._tokens) / self._rate
            return float(max(1, math.ceil(wait - 1e-9)))


_shared: Dict[Tuple[int, int], RefusingBudget] = {}
_shared_lock = threading.Lock()


def shared_budget(per_minute: int, capacity: Optional[int] = None) -> RefusingBudget:
    """The process-wide budget for this rate and capacity, created on first use."""
    key = (per_minute, per_minute if capacity is None else capacity)
    with _shared_lock:
        if key not in _shared:
            _shared[key] = RefusingBudget(per_minute, capacity)
        return _shared[key]


class OneAtATime:
    """Lets a key in once; it may enter again only after ``leave``."""

    def __init__(self) -> None:
        self._active: Set[str] = set()
        self._lock = threading.Lock()

    def try_enter(self, key: str) -> bool:
        with self._lock:
            if key in self._active:
                return False
            self._active.add(key)
            return True

    def leave(self, key: str) -> None:
        with self._lock:
            self._active.discard(key)
