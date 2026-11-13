"""The read a custom view makes (spec P-4): exactly what its code gets back from the Brain.

Every path keeps the manifest's limits. The query's type and the facts it names
or filters on must be in ``reads`` (checked again here); a link is returned only
when the node at its other end has a type named in ``reads``, the rest are
counted in ``links_hidden``; ``linked.to`` ids of other types match nothing; a
link end's facts are never read or returned.

Reads go through the views' per-subject state (``SubjectState``: the topology
index and the facts cache it shares with the existing views) and the views'
``GraphReader``, which the route builds without the views' pacer. The per-node
fallback expands through that reader too, never through the one the index keeps
for the existing views. The budget is charged one unit per node whose facts the read
needs and does not have cached yet, before anything is read; a read that needs
no facts takes nothing. Never silently partial: ``incomplete_why`` names
``node_cap``, ``facts_unreadable`` and ``topology_partial``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from graph_views.facts import fact_values

from .budget import RefusingBudget
from .manifest import Manifest
from .query import Query, authorize

Facts = Optional[List[Dict[str, Any]]]


class BudgetExceeded(Exception):
    """The custom views' budget cannot cover this read now; nothing was read."""

    def __init__(self, retry_after: float):
        super().__init__("Das Lesebudget der eigenen Ansichten ist aufgebraucht.")
        self.retry_after = float(retry_after)


class TooBroad(Exception):
    """``where`` over more than ``max_nodes`` candidates (-> 422)."""

    def __init__(self, max_nodes: int):
        super().__init__(f"„where“ ist nur auf höchstens {max_nodes} Knoten erlaubt; "
                         "die Anfrage mit „ids“ oder „linked“ eingrenzen.")


def _norm(text: str) -> str:
    return text.strip().casefold()


def _order(index: Any, node_id: str) -> Tuple[str, str]:
    """By name, case ignored, then id: one answer for one question."""
    node = index.nodes.get(node_id) or {}
    return (str(node.get("name") or "").casefold(), node_id)


def _linked(index: Any, candidates: List[str], query: Query, reads: Mapping[str, Any],
            reader: Any) -> List[str]:
    """The candidates with a ``linked`` neighbour in ``linked.to`` of a type ``reads`` names."""
    spec = query.linked
    targets = [t for t in dict.fromkeys(query.linked_to or ()) if index.type_of(t) in reads]
    if not targets:
        return []
    index.ensure(targets, reader=reader)        # the per-node fallback learns their edges
    wanted = set(targets)
    return [c for c in candidates
            if any(other in wanted for _, _, other in index.neighbours(c, spec.relation, spec.direction))]


def _facts(state: Any, reader: Any, budget: RefusingBudget, node_ids: Sequence[str]) -> Dict[str, Facts]:
    """Facts per node from the shared cache; charges and reads only the uncached ids."""
    if not node_ids:
        return {}
    with state.lock:
        missing = [n for n in node_ids if n not in state.facts]
    if missing:
        wait = budget.take(len(missing))
        if wait is not None:
            raise BudgetExceeded(wait)
        got = reader.facts_batch(missing)
        with state.lock:
            state.facts.update({n: got.get(n) for n in missing})
    with state.lock:
        return {n: state.facts.get(n) for n in node_ids}


def _matches(values: Mapping[str, str], where: Mapping[str, frozenset]) -> bool:
    for name, wanted in where.items():
        have = values.get(name)
        if have is None or _norm(have) not in wanted:
            return False
    return True


def _links(index: Any, node_id: str, specs: Iterable[Any], reads: Mapping[str, Any]) -> Tuple[List[Dict[str, str]], int]:
    """The node's links per spec whose other end has a type ``reads`` names, and how many were left out."""
    out: List[Dict[str, str]] = []
    hidden = 0
    seen = set()
    for spec in specs:
        ends = []
        for relation, direction, other in index.neighbours(node_id, spec.relation, spec.direction):
            if (relation, direction, other) in seen:
                continue
            seen.add((relation, direction, other))
            if index.type_of(other) not in reads:
                hidden += 1
                continue
            ends.append((relation, direction, other))
        for relation, direction, other in sorted(ends, key=lambda e: _order(index, e[2])):
            node = index.nodes.get(other) or {}
            out.append({"relation": relation, "direction": direction, "id": other,
                        "name": str(node.get("name") or ""), "type": index.type_of(other)})
    return out, hidden


def run_read(query: Query, manifest: Manifest, state: Any, reader: Any, budget: RefusingBudget,
             *, max_nodes: int) -> Dict[str, Any]:
    """Answer one custom-view query; see the module docstring for the rules.

    Raises ``Forbidden`` (the query reaches outside the manifest), ``TooBroad``
    and ``BudgetExceeded``; the last two before any fact is read.
    """
    authorize(query, manifest)
    reads = manifest.reads
    index = state.index
    limit = min(query.limit, max_nodes)

    candidates = list(index.by_type.get(query.type, ()))
    if query.ids is not None:
        ids = set(query.ids)
        candidates = [n for n in candidates if n in ids]
    if query.linked is not None:
        candidates = _linked(index, candidates, query, reads, reader)
    if query.where and len(candidates) > max_nodes:
        raise TooBroad(max_nodes)

    ordered = sorted(candidates, key=lambda n: _order(index, n))
    # with where every candidate is checked; without, only the first page is looked at
    pool = ordered if query.where else ordered[:limit]
    needs_facts = bool(query.where or query.facts)
    facts = _facts(state, reader, budget, pool if needs_facts else [])

    where = {name: frozenset(_norm(v) for v in values) for name, values in query.where.items()}
    kept: List[Tuple[str, Dict[str, str]]] = []
    unreadable = 0
    for nid in pool:
        values: Dict[str, str] = {}
        if needs_facts:
            if facts.get(nid) is None:          # hidden, unknown or failed: never partial data
                unreadable += 1
                continue
            values = fact_values(facts[nid])
            if where and not _matches(values, where):
                continue
        kept.append((nid, values))

    if query.where:
        total, page = len(kept), kept[:limit]
    else:
        total, page = len(ordered) - unreadable, kept

    specs = list(dict.fromkeys(query.links))
    if specs and page:
        index.ensure([nid for nid, _ in page], reader=reader)
    nodes: List[Dict[str, Any]] = []
    links_hidden = 0
    for nid, values in page:
        header = index.nodes.get(nid) or {}
        links, hidden = _links(index, nid, specs, reads)
        links_hidden += hidden
        nodes.append({"id": nid, "name": str(header.get("name") or ""), "type": index.type_of(nid),
                      "description": str(header.get("description") or ""),
                      "facts": {name: values[name] for name in query.facts if name in values},
                      "links": links})

    why: List[str] = []
    if total > len(page):
        why.append("node_cap")
    if unreadable:
        why.append("facts_unreadable")
    if not index.complete:
        why.append("topology_partial")
    return {"nodes": nodes, "total": total, "links_hidden": links_hidden, "complete": not why,
            "incomplete_why": why, "read_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
