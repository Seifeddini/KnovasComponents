"""Walk only the paths the definitions declare.

A path is a list of hops ``{relation, direction, to_type}`` from the instance
(KnowledgeBase, generated/README.md): ``out`` follows an edge from the current
node to the target, ``in`` an edge that points at the current node. Nothing
else is followed — no undeclared relation, no other direction, no other type —
and every walk has a hop cap and a node cap per hop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Set


@dataclass
class Walk:
    nodes: List[str]
    truncated: bool = False
    hops: int = 0
    reasons: List[str] = field(default_factory=list)


def follow(index: Any, start: Iterable[str], path: List[Dict[str, str]], *,
           max_hops: int = 4, max_nodes_per_hop: int = 2000) -> Walk:
    """Nodes reached from ``start`` along ``path``, in a stable order."""
    frontier: List[str] = list(dict.fromkeys(start))
    walk = Walk(nodes=[])
    if len(path) > max_hops:
        walk.truncated = True
        walk.reasons.append(f"Pfad mit {len(path)} Schritten über der Obergrenze von {max_hops}")
        return walk
    for hop in path:
        relation, direction, to_type = hop.get("relation"), hop.get("direction"), hop.get("to_type")
        if direction not in ("in", "out") or not relation or not to_type:
            walk.reasons.append(f"ungültiger Schritt {hop!r}")
            return walk
        index.ensure(frontier)
        seen: Set[str] = set()
        nxt: List[str] = []
        for node in frontier:
            for _, _, other in index.neighbours(node, relation, direction):
                if other in seen or index.type_of(other) != to_type:
                    continue
                seen.add(other)
                nxt.append(other)
                if len(nxt) >= max_nodes_per_hop:
                    walk.truncated = True
                    walk.reasons.append(f"mehr als {max_nodes_per_hop} Knoten bei {relation}")
                    break
            if walk.truncated:
                break
        walk.hops += 1
        frontier = nxt
        if not frontier:
            break
    walk.nodes = frontier
    if getattr(index, "truncated", False):
        walk.truncated = True
    return walk


def path_text(path: List[Dict[str, str]]) -> str:
    """The notation of process_completeness.py: ``→affects→ Component ←governs– ComponentContract``."""
    return " ".join(f"→{h['relation']}→ {h['to_type']}" if h["direction"] == "out"
                    else f"←{h['relation']}– {h['to_type']}" for h in path)
