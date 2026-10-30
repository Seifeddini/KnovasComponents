"""TREE and MATRIX of a spine, from its definition.

A spine is an ordered list of levels; each level names its node types and the
link to the level above: ``{relation, direction}``, where ``out`` means the
edge goes from the child to the parent and ``in`` from the parent to the
child (KnowledgeBase, generated/README.md). A level may link per type
(``links_to_parent``) and narrow a type to a key prefix (``filter.key_prefix``,
e.g. L2- vs L3- components); the prefix is checked on the ``key`` fact, read
only for the nodes a view shows.

The same TREE draws a profile's map (``profile_map``, WP KC-2): its levels come
from the profile view's paths (``profiles.levels``) and its nodes carry the
colour signals the view names (``signals``).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from graph_views.facts import fact_values, read_facts, status_of
from graph_views.grouping import breakdown


class NotDrawable(Exception):
    """The spine, branch or level does not exist in the definition."""


def chain(defs: Any, spine_key: str, branch: str = "") -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    spine = defs.spine(spine_key)
    if spine is None:
        raise NotDrawable(f"Achse {spine_key} ist nicht definiert.")
    if not branch:
        return spine, spine["levels"]
    for b in spine.get("branches") or []:
        if b["key"] == branch:
            return spine, b["levels"]
    raise NotDrawable(f"Zweig {branch} gibt es in {spine_key} nicht.")


def _link(level: Dict[str, Any], type_name: str) -> Optional[Dict[str, str]]:
    per_type = level.get("links_to_parent") or {}
    return per_type.get(type_name) or level.get("link_to_parent")


def _candidates_below(ctx: Any, parent: str, level: Dict[str, Any]) -> List[str]:
    """Nodes of the level's types linked to ``parent`` as the definition says."""
    out: List[str] = []
    for t in level["types"]:
        link = _link(level, t)
        if not link:
            continue
        # out: child -rel-> parent, so from the parent the edge comes in.
        direction = "in" if link["direction"] == "out" else "out"
        for _, _, m in ctx.index.neighbours(parent, link["relation"], direction):
            if ctx.index.type_of(m) == t and m not in out:
                out.append(m)
    return out


def _prefix(level: Dict[str, Any]) -> str:
    return ((level.get("filter") or {}).get("key_prefix")) or ""


def _keyed(ctx: Any, ids: List[str], level: Dict[str, Any], *, read: bool) -> Tuple[List[str], bool]:
    """Apply the level's key prefix. Returns (ids, exact).

    With ``read`` the keys are read (bounded by the caller); without it only
    keys already cached count, and the answer is marked inexact when some are
    unknown.
    """
    prefix = _prefix(level)
    if not prefix or not ids:
        return ids, True
    if read:
        facts = read_facts(ctx.state, ctx.reader, ids)
    else:
        with ctx.state.lock:
            facts = {n: ctx.state.facts.get(n) for n in ids if n in ctx.state.facts}
    kept, exact = [], True
    for n in ids:
        if n not in facts:
            exact = False
            kept.append(n)            # unknown key: counted as "up to"
            continue
        if (fact_values(facts[n]).get("key") or "").startswith(prefix):
            kept.append(n)
    return kept, exact


def level_nodes(ctx: Any, levels: List[Dict[str, Any]], index_of_level: int) -> List[str]:
    """Every node at a level, walked down from the roots along the links."""
    current = roots(ctx, levels)
    for i in range(1, index_of_level + 1):
        nxt: List[str] = []
        for p in current:
            for m in _candidates_below(ctx, p, levels[i]):
                if m not in nxt:
                    nxt.append(m)
        current, _ = _keyed(ctx, nxt, levels[i], read=True)
    return current


def roots(ctx: Any, levels: List[Dict[str, Any]]) -> List[str]:
    ids = [n for t in levels[0]["types"] for n in ctx.index.by_type.get(t, ())]
    kept, _ = _keyed(ctx, ids, levels[0], read=True)
    return kept


def _dress(ctx: Any, ids: List[str], next_level: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    index = ctx.index
    labels = {t: ctx.status_label(t) for t in {index.type_of(n) for n in ids}}
    with_state = [n for n in ids if labels.get(index.type_of(n))]
    facts = read_facts(ctx.state, ctx.reader, list(dict.fromkeys(ids)))
    rows = []
    for n in ids:
        t = index.type_of(n)
        v = fact_values(facts.get(n))
        row = {"id": n, "name": index.nodes.get(n, {}).get("name") or n, "type": t,
               "key": v.get("key"), "status": status_of(facts.get(n), labels.get(t)) if n in with_state else None}
        if next_level is not None:
            kids, exact = _keyed(ctx, _candidates_below(ctx, n, next_level), next_level, read=False)
            row["children"] = len(kids)
            row["children_exact"] = exact
        rows.append(row)
    return rows


def tree(ctx: Any, defs: Any, spine_key: str, *, parent: str = "", level: str = "",
         branch: str = "") -> Dict[str, Any]:
    """One level: the roots, or the children of ``parent`` at the next level."""
    spine, levels = chain(defs, spine_key, branch)
    head = {"key": spine["key"], "name": spine["name"], "description": spine.get("description", ""),
            "branches": [{"key": b["key"], "name": b["name"]} for b in spine.get("branches") or []]}
    return _tree(ctx, head, levels, parent=parent, level=level, branch=branch, where=spine_key)


def profile_map(ctx: Any, key: str, *, parent: str = "", level: str = "") -> Dict[str, Any]:
    """A profile view's map: the TREE over its paths, nodes coloured by its signals (WP KC-2)."""
    from graph_views import profiles, signals

    profile, view = profiles.find(key)
    levels, skipped = profiles.levels(view)
    d = view.get("definition") or {}
    names = list(d.get("color_by") or [])
    head = {"key": None, "name": str(view.get("label") or view["id"]),
            "description": str(d.get("description") or ""), "branches": []}
    body = _tree(ctx, head, levels, parent=parent, level=level, branch="", where=key,
                 colour=lambda rows: signals.colour(ctx, rows, names, ctx.settings.map_fact_budget))
    body.update(profile_view=key, profile={"id": profile["id"], "audience": profile.get("audience")},
                signals=signals.legend(names), skipped=skipped,
                unknown_signals=[n for n in names if n not in signals.known(names)])
    return body


def _tree(ctx: Any, head: Dict[str, Any], levels: List[Dict[str, Any]], *, parent: str, level: str, branch: str,
          where: str, colour: Optional[Callable[[List[Dict[str, Any]]], None]] = None) -> Dict[str, Any]:
    if len(levels) < 1 or not levels[0]["types"]:
        raise NotDrawable(f"{where} hat keine Wurzeltypen.")
    ids_by_level = {l["id"]: i for i, l in enumerate(levels)}
    if not parent:
        i = 0
        ids = roots(ctx, levels)
    else:
        if level not in ids_by_level:
            raise NotDrawable(f"Ebene {level} gibt es in {where} nicht.")
        i = ids_by_level[level] + 1
        if i >= len(levels):
            raise NotDrawable("Die unterste Ebene hat keine Kinder.")
        ctx.index.ensure([parent])
        ids, _ = _keyed(ctx, _candidates_below(ctx, parent, levels[i]), levels[i], read=True)
    ids = ids[: ctx.settings.max_nodes_per_hop]
    nxt = levels[i + 1] if i + 1 < len(levels) else None
    rows = sorted(_dress(ctx, ids, nxt), key=lambda r: (r["key"] or r["name"]).lower())
    if colour is not None:
        colour(rows)
    states = breakdown(r["status"] for r in rows)
    return ctx.envelope(
        spine=head,
        branch=branch, levels=[{"id": l["id"], "name": l["name"], "types": l["types"]} for l in levels],
        level={"id": levels[i]["id"], "name": levels[i]["name"], "types": levels[i]["types"],
               "verified": levels[i].get("verified", True)},
        next_level=({"id": nxt["id"], "name": nxt["name"]} if nxt else None),
        parent=parent or None, nodes=rows, states=states)


def matrix(ctx: Any, defs: Any, spine_key: str, rows_level: str, cols_level: str,
           branch: str = "") -> Dict[str, Any]:
    """Rows × columns of two levels; a cell says whether the spine links them."""
    spine, levels = chain(defs, spine_key, branch)
    pos = {l["id"]: i for i, l in enumerate(levels)}
    if rows_level not in pos or cols_level not in pos:
        raise NotDrawable("Zeilen- und Spaltenebene müssen Ebenen der Achse sein.")
    top, bottom = sorted((pos[rows_level], pos[cols_level]))
    if top == bottom:
        raise NotDrawable("Zwei verschiedene Ebenen wählen.")
    upper = level_nodes(ctx, levels, top)
    # Descendants of every upper node down to the lower level, along the links.
    below: Dict[str, set] = {}
    lower_all: List[str] = []
    for u in upper:
        frontier = [u]
        for i in range(top + 1, bottom + 1):
            nxt = []
            for p in frontier:
                for m in _candidates_below(ctx, p, levels[i]):
                    if m not in nxt:
                        nxt.append(m)
            frontier, _ = _keyed(ctx, nxt, levels[i], read=True)
        below[u] = set(frontier)
        lower_all += [m for m in frontier if m not in lower_all]
    lower_rows = _dress(ctx, lower_all, None)
    upper_rows = _dress(ctx, upper, None)
    order = lambda rows: sorted(rows, key=lambda r: (r["key"] or r["name"]).lower())  # noqa: E731
    if pos[rows_level] == top:
        rows, cols = order(upper_rows), order(lower_rows)
        cell = lambda r, c: c["id"] in below[r["id"]]  # noqa: E731
    else:
        rows, cols = order(lower_rows), order(upper_rows)
        cell = lambda r, c: r["id"] in below[c["id"]]  # noqa: E731
    status_of_col = {c["id"]: c["status"] for c in cols}
    status_of_row = {r["id"]: r["status"] for r in rows}
    cells = [[{"linked": cell(r, c),
               "status": (status_of_col[c["id"]] or status_of_row[r["id"]]) if cell(r, c) else None}
              for c in cols] for r in rows]
    return ctx.envelope(
        spine={"key": spine["key"], "name": spine["name"]}, branch=branch,
        rows_level={"id": rows_level, "name": levels[pos[rows_level]]["name"]},
        cols_level={"id": cols_level, "name": levels[pos[cols_level]]["name"]},
        levels=[{"id": l["id"], "name": l["name"]} for l in levels],
        rows=rows, cols=cols, cells=cells,
        unlinked_rows=[r["id"] for r, line in zip(rows, cells) if not any(x["linked"] for x in line)])
