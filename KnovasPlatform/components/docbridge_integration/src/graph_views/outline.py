"""OUTLINE: one page with everything a profile's ``list`` view names (``layout: outline``).

The definition (KnowledgeBase, profile schema) gives the root types, ``where`` and ``order_by`` on their facts,
``section_by`` (a fact whose value heads the rows that share it), ``columns``, and ``paths``: labelled relation paths
from each root, each with its own ``where``, ``order_by`` and ``columns``. A path with ``process`` also draws each
item's steps of that process and what it misses at its step. ``also`` lists the nodes of a type below the roots, tied
to none of them. ``labels`` gives display texts per fact value, ``emphasis`` marks the rows whose fact matches.

Nothing here names a type, a fact or a process: everything comes from the definition. ``outline_fact_budget`` bounds
the reads; what it does not cover is counted on the page, never dropped silently.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from graph_views import completeness as comp, descriptions, profiles
from graph_views.doc_pages import _where_ok as where_ok
from graph_views.facts import fact_values, read_facts
from graph_views.process_views import current_step
from graph_views.traverse import follow


def _ordered(ids: List[str], values: Dict[str, Dict[str, Any]], order_by: Optional[Dict[str, str]]) -> List[str]:
    """In the fact's order, nodes without the fact last; without ``order_by`` as found."""
    if not order_by:
        return ids
    fact = order_by["fact"]
    have = [n for n in ids if values.get(n, {}).get(fact) not in (None, "")]
    have.sort(key=lambda n: str(values[n][fact]), reverse=order_by.get("direction") == "desc")
    return have + [n for n in ids if n not in set(have)]


def _columns(type_name: str, names: List[str]) -> List[Dict[str, str]]:
    return [{"id": c, "label": descriptions.attribute_label(type_name, c)} for c in names]


def _steps(ctx: Any, defs: Any, proc_key: str, node_id: str) -> Optional[Dict[str, Any]]:
    """The process's steps with this item's place among them, and what it misses to leave its step.

    The first final step is the way a process ends well; another final step shows only for an item that ended there.
    """
    proc = defs.process(proc_key)
    if proc is None:
        return None
    ctx.index.ensure([node_id])
    current = current_step(ctx, defs, proc_key, node_id)
    final = list(proc.get("final") or [])
    shown = [s for s in proc["steps"] if s["id"] not in final[1:] or s["id"] == current]
    place = next((i for i, s in enumerate(shown) if s["id"] == current), -1)
    steps = [{"id": s["id"], "name": s.get("name") or s["id"],
              "state": "current" if i == place else ("done" if i < place else "todo")} for i, s in enumerate(shown)]
    step = next((s for s in proc["steps"] if s["id"] == current), None)
    missing = comp.missing_artefacts(ctx.index, node_id, step, max_hops=ctx.settings.max_hops,
                                     max_nodes_per_hop=ctx.settings.max_nodes_per_hop) \
        if step is not None and current not in final else []
    return {"process": proc_key, "steps": steps, "current": current,
            "missing": [descriptions.type_entry(m["type"])["label"] for m in missing]}


def draw(ctx: Any, defs: Any, key: str) -> Dict[str, Any]:
    profile, view = profiles.find(key)
    if not profiles.is_outline(view):
        raise profiles.NotAProfileView(f"Die Profilansicht {key} ist keine Übersicht.")
    d = view.get("definition") or {}
    index = ctx.index
    labels = d.get("labels") or {}
    settings = ctx.settings

    roots = [n for t in d.get("root_types") or [] for n in index.by_type.get(t, [])]
    walks: Dict[str, List[List[str]]] = {
        r: [follow(index, [r], p["path"], max_hops=settings.max_hops, max_nodes_per_hop=settings.max_nodes_per_hop).nodes
            for p in d.get("paths") or []] for r in roots}
    also = [list(index.by_type.get(a["type"], [])) for a in d.get("also") or []]
    wanted = list(dict.fromkeys(roots + [m for lists in walks.values() for ms in lists for m in ms]
                                + [m for ms in also for m in ms]))
    read = wanted[:settings.outline_fact_budget]
    facts = read_facts(ctx.state, ctx.reader, read)
    values = {n: fact_values(facts.get(n)) for n in read}

    def text(fact: str, value: Any) -> Any:
        return (labels.get(fact) or {}).get(str(value), value)

    def row(node: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        v = values.get(node, {})
        out = {"id": node, "name": (index.nodes.get(node) or {}).get("name") or node, "key": v.get("key"),
               "cells": {c: text(c, v.get(c)) for c in spec.get("columns") or []},
               "emphasis": bool(d.get("emphasis")) and where_ok(d["emphasis"], v)}
        if spec.get("process"):
            out["lifecycle"] = _steps(ctx, defs, spec["process"], node)
        return out

    def rows(ids: List[str], spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        kept = [n for n in ids if n in values and where_ok(spec.get("where"), values[n])]
        return [row(n, spec) for n in _ordered(kept, values, spec.get("order_by"))]

    drawn = []
    for r in rows(roots, d):
        sections = []
        for p, members in zip(d.get("paths") or [], walks[r["id"]]):
            found = rows(members, p)
            sections.append({"label": p.get("label") or "", "rows": found,
                             "columns": _columns(p["path"][-1]["to_type"], p.get("columns") or [])})
        by = d.get("section_by")
        drawn.append({**r, "sections": sections,
                      "section": text(by, values[r["id"]].get(by)) if by else None})
    extra = [{"label": a.get("label") or "", "rows": rows(ms, a), "columns": _columns(a["type"], a.get("columns") or [])}
             for a, ms in zip(d.get("also") or [], also)]
    return ctx.envelope(profile_view=key, profile={"id": profile["id"], "audience": profile.get("audience")},
                        name=str(view.get("label") or view["id"]), description=str(d.get("description") or ""),
                        columns=_columns((d.get("root_types") or [""])[0], d.get("columns") or []),
                        rows=drawn, also=extra,
                        unread=len(wanted) - len(read) + sum(1 for n in read if facts.get(n) is None))
