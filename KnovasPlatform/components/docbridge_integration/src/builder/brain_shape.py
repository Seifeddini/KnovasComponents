"""What the tenant's graph already holds, as definitions: relations and hierarchies derived from its links.

A workspace started from the tenant takes over its node types only: the API lists types, but a relation is just a
name on an edge, with no domain or range, and a hierarchy is nowhere a definition. Yet the links are there: every
component ``part_of`` its area, every area ``part_of`` the system. This module reads the tenant's node types, nodes
and edges (an edge runs from ``node_lo`` to ``node_hi``, as brain-runner reads it) and proposes:

- **relations**: per relation name, the node types it actually links, as domain (from) and range (to); a new
  relation, or the types an existing one is missing;
- **hierarchies**: chains of containment links. A link is containment when its name says so (``CONTAINMENT``) and
  it is functional in the data: a child has one parent over it. Types that are never a child are the tops; each
  chain goes down level by level, a type that contains itself (a component of a component) gives one more level
  for each step the data has.

Each proposal carries the builder's own operations (relation.create/update, spine.create, level.add), so taking it
over is an edit of the draft like any other: one batch, one undo, the usual checks. Nothing is written to the
tenant. Only names and counts leave this module, never node names or document text.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

#: Relation names that mean "the source is inside the target" (out) or "the source holds the target" (in).
CONTAINMENT = {"part_of": "out", "belongs_to": "out", "member_of": "out", "child_of": "out", "within": "out",
               "contained_in": "out", "located_in": "out", "sub_of": "out", "in": "out",
               "contains": "in", "has_part": "in", "includes": "in", "has_member": "in", "parent_of": "in"}
#: Share of children with exactly one parent over a link for it to count as a hierarchy.
FUNCTIONAL = 0.9
#: Fewer links than this are noise, not structure.
MIN_LINKS = 2
#: Levels of one self-containing type (a part of a part of a part ...).
MAX_SELF_LEVELS = 3
MAX_LEVELS = 8
_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass
class Shape:
    relations: list[dict] = field(default_factory=list)    # {id, key, domain, range, links, new, ops}
    hierarchies: list[dict] = field(default_factory=list)  # {id, key, name, levels: [{types, nodes}], ops}
    skipped: list[str] = field(default_factory=list)       # relation names the builder cannot take
    counts: dict = field(default_factory=dict)


def _type_name(type_id: str, names: Mapping[str, str]) -> str:
    if type_id in names:
        return names[type_id]
    return type_id.split(":", 1)[-1] if ":" in type_id else type_id


def _slug(text: str) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", text)
    return re.sub(r"[^a-z]+", "-", text.lower()).strip("-") or "aufbau"


def derive(model: Mapping[str, Any], node_types: Iterable[Mapping[str, Any]], nodes: Iterable[Mapping[str, Any]],
           edges: Iterable[Mapping[str, Any]]) -> Shape:
    """Relations and hierarchies the tenant's links show, as proposals against ``model``."""
    names = {str(t.get("id")): str(t.get("name")) for t in node_types if t.get("id") is not None and t.get("name")}
    type_of = {str(n.get("id")): _type_name(str(n.get("node_type_id")), names)
               for n in nodes if n.get("id") is not None and n.get("node_type_id") is not None}
    onto = model.get("ontology") or {}
    known_types = {t.get("key") for t in onto.get("types") or [] if isinstance(t, Mapping) and not t.get("retired")}
    labels = {t.get("key"): (t.get("label") or {}).get("de") or t.get("key")
              for t in onto.get("types") or [] if isinstance(t, Mapping)}
    known_rel = {r.get("key"): r for r in onto.get("relations") or [] if isinstance(r, Mapping)}

    pairs: Counter = Counter()                                         # (relation, from type, to type) -> links
    parents: dict[tuple, dict[str, set]] = defaultdict(lambda: defaultdict(set))   # (rel, child t, parent t)
    child_of: dict[str, dict[str, str]] = defaultdict(dict)            # rel -> child node -> parent node
    total = 0
    for e in edges:
        lo, hi, rel = str(e.get("node_lo") or ""), str(e.get("node_hi") or ""), str(e.get("relation") or "")
        if lo not in type_of or hi not in type_of or not rel:
            continue
        total += 1
        a, b = type_of[lo], type_of[hi]
        pairs[(rel, a, b)] += 1
        direction = CONTAINMENT.get(rel)
        if direction == "out":
            parents[(rel, a, b)][lo].add(hi)
            child_of[rel][lo] = hi
        elif direction == "in":
            parents[(rel, b, a)][hi].add(lo)
            child_of[rel][hi] = lo

    shape = Shape(counts={"edges": total, "nodes": len(type_of), "types": len(set(type_of.values()))})

    # ---- relations: what each name links
    by_rel: dict[str, dict] = {}
    for (rel, a, b), n in sorted(pairs.items()):
        entry = by_rel.setdefault(rel, {"domain": set(), "range": set(), "links": 0})
        entry["links"] += n
        if a in known_types:
            entry["domain"].add(a)
        if b in known_types:
            entry["range"].add(b)
    for rel, entry in sorted(by_rel.items(), key=lambda kv: -kv[1]["links"]):
        if not _SNAKE.match(rel) or not entry["domain"] or not entry["range"]:
            shape.skipped.append(rel)
            continue
        domain, range_ = sorted(entry["domain"]), sorted(entry["range"])
        have = known_rel.get(rel)
        if have is None:
            ops = [{"op": "relation.create", "key": rel, "domain": domain, "range": range_}]
            new = True
        else:
            wild = "*"
            add_d = [t for t in domain if t not in (have.get("domain") or []) and wild not in (have.get("domain") or [])]
            add_r = [t for t in range_ if t not in (have.get("range") or []) and wild not in (have.get("range") or [])]
            if not add_d and not add_r:
                continue
            ops = [{"op": "relation.update", "key": rel,
                    "set": {"domain": list(have.get("domain") or []) + add_d,
                            "range": list(have.get("range") or []) + add_r}}]
            domain, range_, new = add_d or [], add_r or [], False
        shape.relations.append({"id": f"rel:{rel}", "key": rel, "domain": domain, "range": range_,
                                "links": entry["links"], "new": new, "ops": ops})

    # ---- hierarchies: functional containment links between known types
    links: dict[str, list[tuple[str, str, str]]] = defaultdict(list)   # parent type -> [(child type, rel, dir)]
    for (rel, child, parent), held in parents.items():
        if child not in known_types or parent not in known_types:
            continue
        count = sum(len(p) for p in held.values())
        single = sum(1 for p in held.values() if len(p) == 1)
        if count < MIN_LINKS or single < FUNCTIONAL * len(held):
            continue
        links[parent].append((child, rel, CONTAINMENT[rel]))
    # a top is never the child of another type (containing itself, as parts of parts, does not count)
    children = {c for parent, kids in links.items() for c, _, _ in kids if c != parent}
    tops = sorted(p for p in links if p not in children)
    nodes_of = Counter(type_of.values())
    taken_keys = {s.get("key") for s in ((model.get("spines") or {}).get("spines") or []) if isinstance(s, Mapping)}

    for top in tops:
        levels = [{"types": [top], "link": None}]
        seen = {top}
        current = [top]
        while len(levels) < MAX_LEVELS:
            nxt: list[tuple[str, str, str]] = []
            for p in current:
                for child, rel, direction in links.get(p, []):
                    if child not in seen and child not in [c for c, _, _ in nxt]:
                        nxt.append((child, rel, direction))
            # a type that contains itself: one more level per step the data has (part of a part ...)
            selves = [(c, rel, d) for p in current for c, rel, d in links.get(p, []) if c == p]
            if not nxt and selves:
                child, rel, direction = selves[0]
                depth = _self_depth(child_of.get(rel, {}), type_of, child)
                for _ in range(min(depth, MAX_SELF_LEVELS)):
                    if len(levels) >= MAX_LEVELS:
                        break
                    levels.append({"types": [child], "link": {"relation": rel, "direction": direction}})
                break
            if not nxt:
                break
            rels = {(rel, d) for _, rel, d in nxt}
            if len(rels) > 1:          # one link per level: keep the most common
                rel, d = Counter((rel, d) for _, rel, d in nxt).most_common(1)[0][0]
                nxt = [x for x in nxt if (x[1], x[2]) == (rel, d)]
            rel, direction = nxt[0][1], nxt[0][2]
            levels.append({"types": [c for c, _, _ in nxt], "link": {"relation": rel, "direction": direction}})
            seen.update(c for c, _, _ in nxt)
            current = [c for c, _, _ in nxt]
        if len(levels) < 2:
            continue
        base = f"SPINE-{_slug(top)}"
        key = base
        for suffix in "bcdefghijk":           # spine keys take letters and hyphens only
            if key not in taken_keys:
                break
            key = f"{base}-{suffix}"
        taken_keys.add(key)
        name = f"Aufbau {labels.get(top, top)}"
        ops: list[dict] = [{"op": "spine.create", "key": key, "name": name, "types": levels[0]["types"],
                            "level_name": labels.get(top, top)}]
        for depth, lv in enumerate(levels[1:], start=2):
            label = " / ".join(labels.get(t, t) for t in lv["types"])
            if lv["types"] == levels[depth - 2]["types"]:
                label = f"{label} (Teil)"
            ops.append({"op": "level.add", "spine": key,
                        "level": {"name": label, "types": lv["types"], "link": lv["link"]}})
        shape.hierarchies.append({
            "id": f"spine:{top}", "key": key, "name": name,
            "levels": [{"types": lv["types"], "nodes": sum(nodes_of.get(t, 0) for t in lv["types"]),
                        "link": lv["link"]} for lv in levels],
            "ops": ops})
    return shape


def _self_depth(child_of: Mapping[str, str], type_of: Mapping[str, str], t: str) -> int:
    """How many steps of ``t`` inside ``t`` the data has (1: parts of a whole; 2: parts of parts ...)."""
    best = 0
    for node in child_of:
        if type_of.get(node) != t:
            continue
        depth, at, seen = 0, node, set()
        while at in child_of and type_of.get(child_of[at]) == t and at not in seen:
            seen.add(at)
            at = child_of[at]
            depth += 1
        best = max(best, depth)
    return best


def operations(shape: Shape, chosen: Iterable[str]) -> list[dict]:
    """The operations of the chosen proposals, relations first (a hierarchy's links need them)."""
    wanted = set(chosen)
    ops = [op for r in shape.relations if r["id"] in wanted for op in r["ops"]]
    return ops + [op for h in shape.hierarchies if h["id"] in wanted for op in h["ops"]]


def as_view(shape: Shape) -> dict:
    """The proposals for the page: without the operations (the route derives them again on apply)."""
    return {"relations": [{k: v for k, v in r.items() if k != "ops"} for r in shape.relations],
            "hierarchies": [{k: v for k, v in h.items() if k != "ops"} for h in shape.hierarchies],
            "skipped": shape.skipped, "counts": shape.counts}
