"""Facts for the nodes a view draws (decision D3), cached with the index."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from graph_views.reader import GraphReader
from graph_views.topology import SubjectState

#: Fact labels that carry a node's state, tried in this order. The governing
#: fact of a process (status, stage, onboarding_state) is among them.
STATUS_LABELS = ("status", "stage", "onboarding_state", "outcome", "result")

Facts = List[Dict[str, Any]]


def read_facts(state: SubjectState, reader: GraphReader,
               node_ids: Iterable[str]) -> Dict[str, Optional[Facts]]:
    """Facts per node; reads only the ids this subject has not read yet."""
    ids = list(dict.fromkeys(node_ids))
    with state.lock:
        missing = [n for n in ids if n not in state.facts]
    if missing:
        got = reader.facts_many(missing)
        with state.lock:
            state.facts.update(got)
    with state.lock:
        return {n: state.facts.get(n) for n in ids}


def _open(facts: Optional[Facts]) -> List[Dict[str, Any]]:
    """Facts still in force: no ``valid_to``, newest first."""
    rows = [f for f in facts or [] if not f.get("valid_to")]
    return sorted(rows, key=lambda f: str(f.get("valid_from") or f.get("created_at") or ""),
                  reverse=True)


def fact_values(facts: Optional[Facts]) -> Dict[str, str]:
    """label -> value of the current fact (the newest open one per label)."""
    out: Dict[str, str] = {}
    for f in _open(facts):
        label = str(f.get("label") or "")
        if label and label not in out:
            out[label] = "" if f.get("value") is None else str(f.get("value"))
    return out


def fact_since(facts: Optional[Facts], label: str) -> Optional[str]:
    """``valid_from`` of the current fact under ``label``, as written."""
    for f in _open(facts):
        if f.get("label") == label:
            return str(f.get("valid_from") or "") or None
    return None


def fact_history(facts: Optional[Facts], label: str) -> List[Tuple[str, str]]:
    """(valid_from, value) of every fact under ``label``, oldest first."""
    rows = [(str(f.get("valid_from") or ""), "" if f.get("value") is None else str(f.get("value")))
            for f in facts or [] if f.get("label") == label]
    return sorted(r for r in rows if r[0])


def status_of(facts: Optional[Facts], preferred: Optional[str] = None) -> Optional[str]:
    values = fact_values(facts)
    for label in ((preferred,) if preferred else ()) + STATUS_LABELS:
        if label and values.get(label):
            return values[label]
    return None


class KeyedTTL:
    """A small per-key cache for slow-changing reads (schemas, definitions)."""

    def __init__(self, ttl_seconds: float, now: Optional[Callable[[], float]] = None):
        self._ttl = ttl_seconds
        self._now = now or time.time
        self._items: Dict[Any, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any, load: Callable[[], Any], refresh: bool = False) -> Any:
        now = self._now()
        if key is not None and not refresh:
            with self._lock:
                hit = self._items.get(key)
                if hit is not None and now - hit[0] < self._ttl:
                    return hit[1]
        value = load()
        if key is not None:
            with self._lock:
                self._items[key] = (now, value)
        return value

    def peek(self, key: Any) -> Any:
        """The cached value if still fresh, else None; never loads."""
        with self._lock:
            hit = self._items.get(key)
            if hit is not None and self._now() - hit[0] < self._ttl:
                return hit[1]
        return None

    def put(self, key: Any, value: Any) -> None:
        with self._lock:
            self._items[key] = (self._now(), value)

    def drop(self, predicate: Callable[[Any], bool]) -> None:
        with self._lock:
            for key in [k for k in self._items if predicate(k)]:
                self._items.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
