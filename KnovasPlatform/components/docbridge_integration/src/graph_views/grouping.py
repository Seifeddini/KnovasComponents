"""Fan-out grouping: many same-type neighbours over one relation become one chip.

One component has 980 Alloy checks attached; drawn one by one they bury every
other neighbour. More than ``threshold`` neighbours sharing (relation,
direction, type) collapse into a chip that names the count and, when the
members are few enough to read, their status breakdown. The members themselves
load a page at a time from the side panel, never all at once.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

Neighbour = Tuple[str, str, str]   # (relation, direction, node id)


def group_key(relation: str, direction: str, type_name: str) -> str:
    return f"{direction}:{relation}:{type_name}"


def group(neighbours: Iterable[Neighbour], type_of: Callable[[str], str],
          threshold: int) -> Tuple[List[Neighbour], List[Dict[str, Any]]]:
    """Split neighbours into those drawn as nodes and chips.

    A node reached twice over the same relation and direction counts once.
    Returns (drawn, chips); every chip carries ``members`` (ids, in a stable
    order) for the caller to page through — they are not sent to the browser.
    """
    buckets: Dict[Tuple[str, str, str], List[str]] = {}
    seen = set()
    for relation, direction, other in neighbours:
        key = (relation, direction, type_of(other))
        if (key, other) in seen:
            continue
        seen.add((key, other))
        buckets.setdefault(key, []).append(other)
    drawn: List[Neighbour] = []
    chips: List[Dict[str, Any]] = []
    limit = max(int(threshold), 1)
    for (relation, direction, type_name), members in buckets.items():
        if len(members) > limit:
            chips.append({"key": group_key(relation, direction, type_name),
                          "relation": relation, "direction": direction,
                          "type": type_name, "count": len(members), "members": members})
        else:
            drawn += [(relation, direction, m) for m in members]
    chips.sort(key=lambda c: (-int(c["count"]), str(c["key"])))
    return drawn, chips


def breakdown(statuses: Iterable[Optional[str]]) -> List[Dict[str, Any]]:
    """Counts per status, the most frequent first; nodes without one as None."""
    counts = Counter(s or None for s in statuses)
    return [{"status": s, "count": n}
            for s, n in sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))]


def page(members: List[str], number: int, size: int) -> Tuple[List[str], int]:
    """One page of members (0-based) and the number of pages."""
    size = max(int(size), 1)
    pages = max((len(members) + size - 1) // size, 1)
    number = min(max(int(number), 0), pages - 1)
    return members[number * size:(number + 1) * size], pages
