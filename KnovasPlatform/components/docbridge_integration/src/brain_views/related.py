"""The dossier's related things by kind (PMVP-30b, plan B-01, B-03), read as the signed-in person, per thing.

1. ``graph_neighbors(node, depth=1, include_edges=True)``: the things one hop away this person may see (the Core walks
   the person's visible subgraph; nothing here filters by rights) and the edges among them. The start (hop 0), rows
   not one hop away and rows without a usable id are left out.
2. One ``graph_get_many`` round of ``/node-types/<type>``: the names of the ``MAX_NAMED_KINDS`` kinds with the most
   things; the rest, a kind whose read fails and things without a kind are «Weitere Einträge».

Never a whole-tenant read. A row's relation comes from the edges between the start and it (several joined). A kind's
name in the plural comes from one table, ``plurals.json`` beside this file (the Brain has no plural); a kind outside
it keeps its own name. The step the thing is in (PMVP-32a, the dossier's card «Läuft gerade»): the one neighbour
joined by ``in_state`` of kind ``ProcessStep``, its process the one joined by ``instance_of`` of kind ``Process``; no
further read. The lists sort and filter what was read (the Core has no paged neighbours read). Nothing here logs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

from knovas_client import KnowledgeGraphDisabled

from brain_views.dossier import UNNAMED_NODE, valid_node_id, valid_type_id

# The lists' helpers (sort, filter) moved to brain_views/lists.py (PMVP-32a), unchanged.
from brain_views.lists import sort_key

MAX_NAMED_KINDS = 20
OTHER_KIND = "ohne"
OTHER_KIND_NAME = "Weitere Einträge"


class Unavailable(Exception):
    """The neighbours read failed; the message is the error's type name only."""


@dataclass(frozen=True)
class Thing:
    """A related thing: its id (its dossier's address), its name, and how it relates to the starting thing."""

    id: str
    name: str
    relation: Optional[str]


@dataclass(frozen=True)
class Kind:
    """One kind of related things: ``key`` is the type's uuid, or ``OTHER_KIND`` for «Weitere Einträge»."""

    key: str
    name: str
    things: Tuple[Thing, ...]

    @property
    def count(self) -> int:
        return len(self.things)


@dataclass(frozen=True)
class Related:
    kinds: Tuple[Kind, ...]
    total: int
    # PMVP-32a: the step this thing is in, and its process, when the neighbours show exactly one of each
    in_state: Optional[Thing] = None
    process: Optional[Thing] = None

    @property
    def step_name(self) -> Optional[str]:
        """The step's name without the «<process name>: » the convention puts before it (as the process line names
        its steps), or None without a step."""
        if self.in_state is None:
            return None
        name, prefix = self.in_state.name, f"{self.process.name}: " if self.process is not None else ""
        return (name[len(prefix):].strip() if prefix and name.startswith(prefix) else "") or name

    def kind(self, key: Any) -> Optional[Kind]:
        """The kind with this key, or None (an unknown key, or one this thing does not have)."""
        for kind in self.kinds:
            if kind.key == key:
                return kind
        return None


NOTHING = Related(kinds=(), total=0)


# -- the plurals ---------------------------------------------------------------------------------------------------

# By the kind's name as the Brain gives it (compared without regard to case and spacing): brain_views/plurals.json,
# one section each for the legal pack, the reference screens' words and the pilot's tenant (Knovas GmbH).
_TABLE = json.loads((Path(__file__).with_name("plurals.json")).read_text(encoding="utf-8"))
PLURALS: Dict[str, str] = {name: word for section, names in _TABLE.items() if not section.startswith("_")
                           for name, word in names.items()}


def _folded(text: str) -> str:
    return " ".join(text.split()).casefold()


_PLURALS_FOLDED = {_folded(name): plural for name, plural in PLURALS.items()}


def plural(name: str) -> str:
    """The kind's name in the plural from ``PLURALS``; a name outside the table as the Brain gives it."""
    return _PLURALS_FOLDED.get(_folded(name), name)


# -- the read ------------------------------------------------------------------------------------------------------


def read_related(client: Any, node_id: str) -> Related:
    """The things one hop from this node, by kind, as the person the client signs for may see them.

    A graph switched off in the Core and an unknown or hidden node (the client gives an empty answer for its 404)
    are "nothing related". Any other failure of the neighbours read raises Unavailable; a failed kind read only
    leaves that kind unnamed.
    """
    if not valid_node_id(node_id):
        return NOTHING
    try:
        answer = client.graph_neighbors(node_id, depth=1, include_edges=True)
    except KnowledgeGraphDisabled:
        return NOTHING
    except Exception as exc:  # noqa: BLE001 - every other failure is one plain line on the page
        raise Unavailable(type(exc).__name__) from None
    if not isinstance(answer, Mapping):
        return NOTHING
    rows = _rows(answer.get("neighbors"), node_id)
    if not rows:
        return NOTHING
    by_relation = _relations(answer.get("edges"), node_id)
    relations = {other: ", ".join(sorted(found, key=sort_key)) for other, found in by_relation.items()}
    # The kinds of the things joined by in_state or instance_of are always read (for «Läuft gerade»), beside the
    # twenty most frequent: a step whose kind went unread must not make the card disappear (fix round 2026-11-22).
    must = [type_id for row, type_id in rows
            if by_relation.get(row["id"].lower(), set()) & {"in_state", "instance_of"}]
    names = _kind_names(client, [type_id for _, type_id in rows], must=must)
    grouped: Dict[str, List[Thing]] = {}
    for thing_row, type_id in rows:
        key = type_id if type_id in names else OTHER_KIND
        thing = Thing(id=thing_row["id"], name=thing_row["name"], relation=relations.get(thing_row["id"].lower()))
        grouped.setdefault(key, []).append(thing)
    kinds = [Kind(key=key, name=plural(names[key]) if key != OTHER_KIND else OTHER_KIND_NAME,
                  things=tuple(sorted(things, key=lambda t: (sort_key(t.name), t.id))))
             for key, things in grouped.items()]
    # By count, highest first, then by name; «Weitere Einträge», which is no kind of its own, comes last.
    kinds.sort(key=lambda k: (k.key == OTHER_KIND, -k.count, sort_key(k.name), k.key))

    def joined_by(relation: str, kind_name: str) -> Optional[Thing]:
        """The one row joined to the start by ``relation`` whose kind the Brain names ``kind_name``, or None."""
        found = [Thing(id=row["id"], name=row["name"], relation=relation) for row, type_id in rows
                 if relation in by_relation.get(row["id"].lower(), ())
                 and type_id is not None and _folded(names.get(type_id, "")) == _folded(kind_name)]
        return found[0] if len(found) == 1 else None

    return Related(kinds=tuple(kinds), total=sum(k.count for k in kinds),
                   in_state=joined_by("in_state", "ProcessStep"), process=joined_by("instance_of", "Process"))


def _rows(neighbours: Any, node_id: str) -> List[Tuple[Dict[str, str], Optional[str]]]:
    """(the thing's id and name, its type id) for every row one hop away, each id once, the start left out."""
    found: List[Tuple[Dict[str, str], Optional[str]]] = []
    seen = {node_id.lower()}
    for row in neighbours if isinstance(neighbours, list) else []:
        if not isinstance(row, Mapping):
            continue
        hop = row.get("hop")
        if isinstance(hop, bool) or hop != 1:
            continue
        thing_id = row.get("id")
        if not valid_node_id(thing_id) or thing_id.lower() in seen:
            continue
        seen.add(thing_id.lower())
        type_id = row.get("node_type_id")
        found.append(({"id": thing_id, "name": _clean(row.get("name")) or UNNAMED_NODE},
                      type_id.lower() if valid_type_id(type_id) else None))
    return found


def _relations(edges: Any, node_id: str) -> Dict[str, set]:
    """For each thing joined to the start by an edge (by its id in lower case), the names of its relations."""
    start = node_id.lower()
    found: Dict[str, set] = {}
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, Mapping):
            continue
        ends = [edge.get("node_lo"), edge.get("node_hi")]
        if not all(isinstance(end, str) for end in ends):
            continue
        lowered = [end.lower() for end in ends]
        if start not in lowered or lowered[0] == lowered[1]:
            continue
        other = ends[1] if lowered[0] == start else ends[0]
        relation = _clean(edge.get("relation"))
        if relation:
            found.setdefault(other.lower(), set()).add(relation)
    return found


def _kind_names(client: Any, type_ids: Sequence[Optional[str]], *,
                must: Sequence[Optional[str]] = ()) -> Dict[str, str]:
    """{type id: its name} for the kinds in ``must`` and the kinds with the most things, at most MAX_NAMED_KINDS of
    those, in one round of reads."""
    counts: Dict[str, int] = {}
    for type_id in type_ids:
        if type_id:
            counts[type_id] = counts.get(type_id, 0) + 1
    needed = sorted({t for t in must if t})
    chosen = needed + [t for t in sorted(counts, key=lambda t: (-counts[t], t)) if t not in needed][:MAX_NAMED_KINDS]
    if not chosen:
        return {}
    try:
        answers = client.graph_get_many([(f"/node-types/{quote(t, safe='')}", None) for t in chosen],
                                        workers=min(4, len(chosen)))
    except Exception:  # noqa: BLE001 - the kinds go unnamed; the things are still listed
        return {}
    if not isinstance(answers, Sequence) or len(answers) != len(chosen):
        return {}
    names: Dict[str, str] = {}
    for type_id, answer in zip(chosen, answers):
        if not isinstance(answer, Sequence) or len(answer) != 2:
            continue
        status, payload = answer
        node_type = payload.get("node_type") if status == 200 and isinstance(payload, Mapping) else None
        name = _clean(node_type.get("name")) if isinstance(node_type, Mapping) else ""
        if name:
            names[type_id] = name
    return names


def _clean(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""

