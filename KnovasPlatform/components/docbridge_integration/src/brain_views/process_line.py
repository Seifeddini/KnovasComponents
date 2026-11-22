"""The process line for one thing (PMVP-32a, plan B-06, B-01, B-02): where a thing stands in its process, since when,
what it still needs and what comes next, read as the signed-in person, per thing.

**The data** (the Client Zero convention, as the Knovas GmbH tenant holds it; decision PMVP-32, option A): a process
is a node of kind ``Process``; its steps are ``ProcessStep`` nodes joined to it by ``has_step``; a step's facts carry
``order`` (a whole number as text), ``owner_role``, ``requires`` and ``produces``; steps are joined by
``transitions_to`` (forward) and ``reworks_to`` (back). An instance is joined to its process by ``instance_of`` and to
its current step by ``in_state``; its facts carry ``current_step``, ``step_history`` (``[[step, from, to or null],
...]``) and ``owner``.

**An edge keeps no direction**: the Core stores its two ends in id order (``node_lo``, ``node_hi``). The direction
comes from the steps' ``order``: a ``transitions_to`` goes from the lower order to the higher, a ``reworks_to`` from
the higher to the lower. A step without a usable ``order``, two steps with the same ``order`` or an edge that cannot
be oriented (both ends one step, or two steps of the same order) leave the line "not completely described": no line
is drawn, and the process's id and a reason code are logged.

**The main path and the variants** (coordinator, 2026-11-22): the steps sorted by ``order``; the main path starts at
the lowest order, and from each of its steps the next is the ``transitions_to`` target with the smallest order
greater than its own; it ends where there is none. Every other step and every ``reworks_to`` is a variant.

**The reads**, each signed as the person through the web app's one client (never ``graph_export``, ``graph_edges``,
``graph_nodes`` or ``graph_node_types``); a page view costs the same whatever the number of steps (the fix round of
2026-11-22 made the process's neighbours the only path: the per-step ``/edges`` reads cost 26 + 3 N calls):

1. ``GET /nodes/<id>`` (``graph_node``): the thing, its kind and its facts. Unknown or hidden: the dossier's NotFound.
2. ``GET /nodes/<id>/neighbors?depth=1&include_edges=true`` (``graph_neighbors``): its process (``instance_of``) and
   its current step (``in_state``). None of either, or more than one of either: not in a process.
3. One ``graph_get_many`` round of ``/node-types/<id>``: the thing's own kind, the process's and the step's (at most
   three), to recognise ``Process`` and ``ProcessStep`` by name. Of another kind: not in a process.
4. ``graph_neighbors(<process>, depth=1, include_edges=True)``: the steps (the neighbours of the current step's kind),
   the edges among them, and the process's ``instance_of`` edges (how many things are in it).
5. ``POST /facts/batch`` (``graph_facts_batch``): the steps' facts (``order``, ``requires``).
6. Only when the current step requires things over one hop: one round of ``/node-types/<id>`` for the kinds of the
   thing's neighbours over those relations (at most ``MAX_KIND_READS``).

So a line costs 7 calls (with three distinct kinds), and one kind read more per unread kind its step requires;
the list of the others (``read_instances``: 1 to 4) costs 6.

**What is still missing** (PMVP-32b, coordinator's point 4): the current step's ``requires`` entries whose path is one
hop. For each, the thing's neighbours joined to it over that ``relation`` whose kind the Brain names ``to_type``;
fewer than ``min_count`` is missing, and only a ``required`` entry is marked. **Nothing is concluded from a kind that
was not read**: a neighbour over that relation whose kind could not be read (beyond ``MAX_KIND_READS``, or its read
failed) makes the entry «unknown», not missing. Edges keep no direction, so the hop's ``direction`` is not checked.
Longer paths are left out (``more_requirements``). Things, not documents.

Nothing here logs a name or a fact; only ids of the process and reason codes (GI-DATA-04).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import quote

from knovas_client import KnowledgeGraphDisabled

from brain_views.lists import sort_key
from brain_views.reads import NotFound, Unavailable, node_payload, valid_node_id, valid_type_id

logger = logging.getLogger(__name__)

PROCESS_KIND = "Process"
STEP_KIND = "ProcessStep"
INSTANCE_OF = "instance_of"
IN_STATE = "in_state"
TRANSITIONS_TO = "transitions_to"
REWORKS_TO = "reworks_to"
MAX_KIND_READS = 20
_KEY = re.compile(r"^[A-Z]+-")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

__all__ = ["Instances", "Line", "NotFound", "Requirement", "Step", "Unavailable", "Way", "read_instances", "read_line",
           "main_path", "step_name"]


@dataclass(frozen=True)
class Step:
    id: str
    name: str
    order: int


@dataclass(frozen=True)
class Way:
    """Another way from the current step: a ``transitions_to`` off the main path, or a ``reworks_to`` back."""

    step: Step
    relation: str


@dataclass(frozen=True)
class Line:
    """One thing's process line. ``process_id`` None: the thing is in no process. ``complete`` False: the process is
    not completely described, and nothing of the line is shown."""

    node_id: str
    name: str
    kind: Optional[str]
    process_id: Optional[str] = None
    process_name: Optional[str] = None
    complete: bool = False
    main: Tuple[Step, ...] = ()
    current: Optional[Step] = None
    position: Optional[int] = None
    next_step: Optional[Step] = None
    ways: Tuple[Way, ...] = ()
    since: Optional[date] = None
    who: Optional[str] = None
    # PMVP-32b: what the current step still needs, and how many things are in this process (this one included)
    requirements: Tuple["Requirement", ...] = ()
    more_requirements: bool = False
    instances: int = 0

    @property
    def in_process(self) -> bool:
        return self.process_id is not None


@dataclass(frozen=True)
class Requirement:
    """One ``requires`` entry of the current step over one hop: what the thing has of it, and whether that is enough.

    ``unknown`` counts the thing's neighbours over that relation whose kind could not be read: while there are any,
    too few present is not concluded to be missing (``state`` "unknown")."""

    type: str
    required: bool
    min_count: int
    present: Tuple[Tuple[str, str], ...]          # (id, name) of the things the thing has of it, by name
    unknown: int = 0

    @property
    def state(self) -> str:
        if len(self.present) >= self.min_count:
            return "ok"
        return "unknown" if self.unknown else "missing"

    @property
    def missing(self) -> bool:
        return self.state == "missing"


@dataclass(frozen=True)
class Instances:
    """The other things in the same process as one thing (PMVP-32b); ``process_id`` None: in no process."""

    node_id: str
    name: str
    kind: Optional[str]
    process_id: Optional[str] = None
    process_name: Optional[str] = None
    others: Tuple[Tuple[str, str], ...] = ()      # (id, name)

    @property
    def in_process(self) -> bool:
        return self.process_id is not None


@dataclass(frozen=True)
class _Context:
    """What every read of a thing's process starts from: the thing, its neighbours, its process and its step."""

    node_id: str
    name: str
    kind: Optional[str]
    facts: List[Mapping[str, Any]]
    rows: Mapping[str, Mapping[str, Any]]
    mine: List[Mapping[str, Any]]
    kinds: Mapping[str, str]
    process: Optional[Mapping[str, Any]]
    state: Optional[Mapping[str, Any]]


@dataclass(frozen=True)
class _Process:
    """What the process's neighbours give: its steps (id -> name), the edges among them, the things in it."""

    steps: Mapping[str, str]
    edges: List[Tuple[str, str, str]]
    instances: Set[str]
    rows: Mapping[str, Mapping[str, Any]]


class _Incomplete(Exception):
    """The process is not completely described: its reason code."""


# -- the read --------------------------------------------------------------------------------------------------------


def read_line(client: Any, node_id: str) -> Line:
    """The process line of one thing, as the person the client signs for may see it.

    NotFound as the dossier raises it (a malformed, unknown or hidden id); Unavailable when a read the line needs
    fails. A thing in no process gives a Line whose ``process_id`` is None."""
    ctx = _context(client, node_id)
    base: Dict[str, Any] = dict(node_id=node_id, name=ctx.name, kind=ctx.kind)
    if ctx.process is None or ctx.state is None:
        return Line(**base)
    process, state = ctx.process, ctx.state
    process_name = _clean(process.get("name")) or "Ohne Namen"
    base.update(process_id=process["id"], process_name=process_name)
    found = _process(client, process["id"], state)
    step_facts = _facts(client, list(found.steps))
    base.update(instances=len(found.instances | {node_id.lower()}))
    try:
        line = _line(found.steps, found.edges, step_facts, state["id"].lower(), process_name)
    except _Incomplete as reason:
        logger.info("Process line: process %s is not completely described (%s)", process["id"], reason)
        return Line(**base)
    main, current, position, next_step, ways = line
    requirements, more = _requirements(client, step_facts.get(state["id"].lower()) or (), ctx)
    return Line(**base, complete=True, main=main, current=current, position=position, next_step=next_step,
                ways=ways, since=_since(ctx.facts), who=_who(ctx.facts), requirements=requirements,
                more_requirements=more)


def read_instances(client: Any, node_id: str) -> Instances:
    """The other things in the same process as this one, by name, from the process's neighbours (one read; no read
    per instance). NotFound and Unavailable as read_line raises them; in no process: ``process_id`` None."""
    ctx = _context(client, node_id)
    if ctx.process is None or ctx.state is None:
        return Instances(node_id=node_id, name=ctx.name, kind=ctx.kind)
    process_id = ctx.process["id"]
    found = _process(client, process_id, ctx.state)
    others = sorted(((found.rows[i]["id"], _clean(found.rows[i].get("name")) or "Ohne Namen")
                     for i in found.instances if i in found.rows and i != node_id.lower()),
                    key=lambda pair: (sort_key(pair[1]), pair[0]))
    return Instances(node_id=node_id, name=ctx.name, kind=ctx.kind, process_id=process_id,
                     process_name=_clean(ctx.process.get("name")) or "Ohne Namen", others=tuple(others))


def _context(client: Any, node_id: str) -> _Context:
    """The thing (NotFound as the dossier raises it), its neighbours; its process and its step when it has exactly one
    of each, of kind Process and ProcessStep (one round of at most three kind reads)."""
    payload, node = node_payload(client, node_id)
    own_type = node.get("node_type_id")
    facts = payload.get("facts") if isinstance(payload.get("facts"), list) else []
    try:
        near = client.graph_neighbors(node_id, depth=1, include_edges=True)
    except KnowledgeGraphDisabled:
        raise NotFound() from None
    except Exception as exc:  # noqa: BLE001 - one plain message on the page
        raise Unavailable(type(exc).__name__) from None
    rows = {r["id"].lower(): r for r in _rows(near.get("neighbors") if isinstance(near, Mapping) else None)}
    edges = [e for e in (near.get("edges") if isinstance(near, Mapping) else None) or [] if isinstance(e, Mapping)]
    mine = [e for e in edges if _touches(e, node_id)]
    processes = {_other(e, node_id) for e in mine if e.get("relation") == INSTANCE_OF} & set(rows)
    states = {_other(e, node_id) for e in mine if e.get("relation") == IN_STATE} & set(rows)
    process = rows[next(iter(processes))] if len(processes) == 1 else None
    state = rows[next(iter(states))] if len(states) == 1 else None
    must = [r.get("node_type_id") for r in (process, state) if r is not None] if process and state else []
    kinds = _kind_names(client, [own_type] + must, must=must)
    if process is None or state is None or (
            _folded(kinds.get(str(process.get("node_type_id")).lower(), "")) != _folded(PROCESS_KIND)
            or _folded(kinds.get(str(state.get("node_type_id")).lower(), "")) != _folded(STEP_KIND)):
        process = state = None
    return _Context(node_id=node_id, name=_clean(node.get("name")) or "Ohne Namen",
                    kind=kinds.get(str(own_type).lower()) if valid_type_id(own_type) else None, facts=facts,
                    rows=rows, mine=mine, kinds=kinds, process=process, state=state)


def _process(client: Any, process_id: str, state: Mapping[str, Any]) -> _Process:
    """One ``graph_neighbors(<process>)``: the steps (its neighbours of the current step's kind) with their names, the
    transitions and reworks among them, and the things joined to it by ``instance_of``."""
    try:
        near = client.graph_neighbors(process_id, depth=1, include_edges=True)
    except Exception as exc:  # noqa: BLE001
        raise Unavailable(type(exc).__name__) from None
    rows = {r["id"].lower(): r for r in _rows(near.get("neighbors") if isinstance(near, Mapping) else None)}
    step_type = str(state.get("node_type_id")).lower()
    steps = {i: _clean(r.get("name")) for i, r in rows.items() if str(r.get("node_type_id")).lower() == step_type}
    edges = [e for e in (near.get("edges") if isinstance(near, Mapping) else None) or [] if isinstance(e, Mapping)]
    instances = {_other(e, process_id) for e in edges if e.get("relation") == INSTANCE_OF and _touches(e, process_id)}
    return _Process(steps=steps, edges=_among(edges, steps), instances=instances - {None}, rows=rows)


def _among(edges: Iterable[Mapping[str, Any]], steps: Mapping[str, str]) -> List[Tuple[str, str, str]]:
    """The transitions and reworks between two steps: (one end, the other end, relation), each edge once."""
    seen: Set[Any] = set()
    out = []
    for edge in edges:
        relation = edge.get("relation")
        if relation not in (TRANSITIONS_TO, REWORKS_TO):
            continue
        ends = [edge.get("node_lo"), edge.get("node_hi")]
        if not all(isinstance(end, str) for end in ends):
            continue
        lo, hi = ends[0].lower(), ends[1].lower()
        if lo not in steps or hi not in steps:
            continue
        key = edge.get("id") or (lo, hi, relation)
        if key in seen:
            continue
        seen.add(key)
        out.append((lo, hi, relation))
    return out


def _facts(client: Any, step_ids: List[str]) -> Dict[str, List[Mapping[str, Any]]]:
    if not step_ids:
        return {}
    try:
        status, payload = client.graph_facts_batch(step_ids)
    except Exception as exc:  # noqa: BLE001
        raise Unavailable(type(exc).__name__) from None
    by_node = payload.get("facts_by_node") if status == 200 and isinstance(payload, Mapping) else None
    if not isinstance(by_node, Mapping):
        raise Unavailable(f"facts status {status}")
    return {str(k).lower(): [f for f in v if isinstance(f, Mapping)] for k, v in by_node.items() if isinstance(v, list)}


# -- the line --------------------------------------------------------------------------------------------------------


def _line(steps: Mapping[str, str], edges: Sequence[Tuple[str, str, str]], facts: Mapping[str, Any], current_id: str,
          process_name: str):
    if current_id not in steps:
        raise _Incomplete("current_step_not_in_process")
    orders: Dict[str, int] = {}
    for step_id in steps:
        order = _order(facts.get(step_id) or ())
        if order is None:
            raise _Incomplete("order_missing")
        orders[step_id] = order
    if len(set(orders.values())) != len(orders):
        raise _Incomplete("order_tie")
    named = {s: Step(id=s, name=step_name(steps[s], process_name), order=orders[s]) for s in steps}
    forward: Dict[str, Set[str]] = {s: set() for s in steps}
    back: Dict[str, Set[str]] = {s: set() for s in steps}
    for lo, hi, relation in edges:
        if lo == hi or orders[lo] == orders[hi]:
            raise _Incomplete("edge_against_order")
        low, high = (lo, hi) if orders[lo] < orders[hi] else (hi, lo)
        if relation == TRANSITIONS_TO:
            forward[low].add(high)
        else:
            back[high].add(low)
    main = main_path(orders, forward)
    current = named[current_id]
    on_main = current_id in main
    position = main.index(current_id) + 1 if on_main else None
    next_id = main[main.index(current_id) + 1] if on_main and main.index(current_id) + 1 < len(main) else None
    ways = [Way(named[t], TRANSITIONS_TO) for t in sorted(forward[current_id], key=orders.get) if t != next_id]
    ways += [Way(named[t], REWORKS_TO) for t in sorted(back[current_id], key=orders.get, reverse=True)]
    return (tuple(named[s] for s in main), current, position, named[next_id] if next_id else None, tuple(ways))


def main_path(orders: Mapping[str, int], forward: Mapping[str, Set[str]]) -> List[str]:
    """From the lowest order, each time to the transitions_to target with the smallest order above its own."""
    if not orders:
        return []
    step = min(orders, key=orders.get)
    path = [step]
    while True:
        targets = [t for t in forward.get(step, ()) if orders[t] > orders[step]]
        if not targets:
            return path
        step = min(targets, key=orders.get)
        path.append(step)


def step_name(name: str, process_name: str) -> str:
    """A step's name without the «<process name>: » the convention puts before it."""
    name, prefix = _clean(name), f"{_clean(process_name)}: "
    rest = name[len(prefix):].strip() if name.startswith(prefix) else name
    return rest or name or "Ohne Namen"


def _order(facts: Iterable[Mapping[str, Any]]) -> Optional[int]:
    for fact in facts:
        if _folded(_clean(fact.get("label"))) != "order" or fact.get("curation_status") == "rejected":
            continue
        value = fact.get("value")
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isascii() and value.strip().lstrip("-").isdigit():
            return int(value.strip())
        return None
    return None


def _fact(facts: Iterable[Mapping[str, Any]], label: str) -> Optional[Mapping[str, Any]]:
    for fact in facts:
        if isinstance(fact, Mapping) and _folded(_clean(fact.get("label"))) == label \
                and fact.get("curation_status") != "rejected":
            return fact
    return None


def _since(facts: Iterable[Mapping[str, Any]]) -> Optional[date]:
    """Since when the thing is in its step: the last open entry of ``step_history``, else ``current_step``'s
    ``valid_from``."""
    facts = list(facts)
    history = _fact(facts, "step_history")
    value = history.get("value") if history else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    if isinstance(value, list):
        for entry in reversed(value):
            if isinstance(entry, (list, tuple)) and len(entry) >= 2 and (len(entry) < 3 or entry[2] in (None, "")):
                found = _date(entry[1])
                if found:
                    return found
    current = _fact(facts, "current_step")
    return _date(current.get("valid_from")) if current else None


def _who(facts: Iterable[Mapping[str, Any]]) -> Optional[str]:
    """The ``owner`` fact when it is a name; a key (``PERS-…``) is left out: no per-thing read looks one up."""
    owner = _fact(facts, "owner")
    text = _clean(owner.get("value")) if owner else ""
    return text if text and not _KEY.match(text) else None


def _date(value: Any) -> Optional[date]:
    match = _DATE.match(value.strip()) if isinstance(value, str) else None
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _requirements(client: Any, facts: Iterable[Mapping[str, Any]],
                  ctx: _Context) -> Tuple[Tuple[Requirement, ...], bool]:
    """The current step's one-hop ``requires`` entries with what the thing has of each; and whether entries with
    longer (or unreadable) paths were left out. The kinds of the thing's neighbours over those relations are read in
    one round (those the context has not read yet); a neighbour whose kind stays unread counts as «unknown»."""
    fact = _fact(facts, "requires")
    value = fact.get("value") if fact else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    if not isinstance(value, list):
        return (), False
    entries: List[Tuple[str, bool, int, str, str]] = []
    more = False
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        path = entry.get("path")
        hop = path[0] if isinstance(path, list) and len(path) == 1 and isinstance(path[0], Mapping) else None
        relation = _clean(hop.get("relation")) if hop else ""
        to_type = _clean(hop.get("to_type")) if hop else ""
        if not relation or not to_type:
            more = True
            continue
        count = entry.get("min_count")
        entries.append((_clean(entry.get("type")) or to_type, entry.get("required") is not False,
                        count if isinstance(count, int) and not isinstance(count, bool) and count >= 1 else 1,
                        relation, to_type))
    if not entries:
        return (), more
    relations = {relation for _, _, _, relation, _ in entries}
    joined: Dict[str, Set[str]] = {}
    for edge in ctx.mine:
        relation, other = _clean(edge.get("relation")), _other(edge, ctx.node_id)
        if relation in relations and other in ctx.rows:
            joined.setdefault(relation, set()).add(other)
    kinds = dict(ctx.kinds)
    unread = [ctx.rows[i].get("node_type_id") for ids in joined.values() for i in ids
              if str(ctx.rows[i].get("node_type_id")).lower() not in kinds]
    kinds.update(_kind_names(client, unread))
    found: List[Requirement] = []
    for label, required, min_count, relation, to_type in entries:
        present: List[Tuple[str, str]] = []
        unknown = 0
        for other in joined.get(relation, ()):
            row = ctx.rows[other]
            type_id = row.get("node_type_id")
            if type_id is None:
                continue                                   # a thing without a kind is of no kind it could need
            name = kinds.get(str(type_id).lower())
            if name is None:
                unknown += 1
            elif _folded(name) == _folded(to_type):
                present.append((row["id"], _clean(row.get("name")) or "Ohne Namen"))
        found.append(Requirement(type=label, required=required, min_count=min_count,
                                 present=tuple(sorted(present, key=lambda pair: (sort_key(pair[1]), pair[0]))),
                                 unknown=unknown))
    return tuple(found), more


# -- helpers ---------------------------------------------------------------------------------------------------------


def _get_many(client: Any, calls: List[Tuple[str, Optional[Dict[str, str]]]]) -> List[Any]:
    try:
        answers = client.graph_get_many(calls, workers=min(4, len(calls)))
    except Exception as exc:  # noqa: BLE001
        raise Unavailable(type(exc).__name__) from None
    if not isinstance(answers, Sequence) or len(answers) != len(calls):
        raise Unavailable("answers do not match the calls")
    return list(answers)


def _kind_names(client: Any, type_ids: Iterable[Any], *, must: Sequence[Any] = ()) -> Dict[str, str]:
    """{type id: name} for the given kinds in one round: those in ``must`` and the most frequent of the rest, at most
    MAX_KIND_READS. A kind whose read fails is left out (its things are «unknown» to the caller); a failed read of
    a ``must`` kind (not 404) is Unavailable."""
    counts: Dict[str, int] = {}
    for type_id in type_ids:
        if valid_type_id(type_id):
            counts[type_id.lower()] = counts.get(type_id.lower(), 0) + 1
    needed = {t.lower() for t in must if valid_type_id(t)}
    chosen = list(dict.fromkeys(sorted(needed) + sorted(counts, key=lambda t: (-counts[t], t))))
    chosen = [t for t in chosen if t in needed] + [t for t in chosen if t not in needed][:MAX_KIND_READS]
    if not chosen:
        return {}
    answers = _get_many(client, [(f"/node-types/{quote(t, safe='')}", None) for t in chosen])
    names: Dict[str, str] = {}
    for type_id, answer in zip(chosen, answers):
        status, payload = answer if isinstance(answer, Sequence) and len(answer) == 2 else (599, None)
        node_type = payload.get("node_type") if status == 200 and isinstance(payload, Mapping) else None
        name = _clean(node_type.get("name")) if isinstance(node_type, Mapping) else ""
        if name:
            names[type_id] = name
        elif status not in (200, 404) and type_id in needed:
            raise Unavailable(f"kind status {status}")
    return names


def _rows(neighbours: Any) -> List[Dict[str, Any]]:
    out = []
    for row in neighbours if isinstance(neighbours, list) else []:
        if isinstance(row, Mapping) and row.get("hop") == 1 and not isinstance(row.get("hop"), bool) \
                and valid_node_id(row.get("id")):
            out.append(dict(row))
    return out


def _touches(edge: Mapping[str, Any], node_id: str) -> bool:
    ends = (edge.get("node_lo"), edge.get("node_hi"))
    return any(isinstance(end, str) and end.lower() == node_id.lower() for end in ends)


def _other(edge: Mapping[str, Any], node_id: str) -> Optional[str]:
    lo, hi = edge.get("node_lo"), edge.get("node_hi")
    if not (isinstance(lo, str) and isinstance(hi, str)):
        return None
    if lo.lower() == node_id.lower():
        return hi.lower()
    if hi.lower() == node_id.lower():
        return lo.lower()
    return None


def _folded(text: str) -> str:
    return " ".join(text.split()).casefold()


def _clean(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""
