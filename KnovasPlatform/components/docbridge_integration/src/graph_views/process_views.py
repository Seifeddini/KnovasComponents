"""TRACE (one instance) and BOARD (all instances) of a process, from its definition.

Lanes and columns are the definition's steps in order; what a lane shows is
what its ``requires`` and ``produces`` paths reach from the instance. Nothing
here knows a node type or a relation by name: everything comes from the
definition.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Set, Tuple

from graph_views import completeness as comp, descriptions
from graph_views.facts import fact_history, fact_since, fact_values, read_facts, status_of
from graph_views.grouping import breakdown, page
from graph_views.traverse import follow, path_text


class NotPlaceable(Exception):
    """The instance or process cannot be placed (unknown, invisible, no definition)."""


def _process_node(defs: Any, proc_key: str) -> Optional[str]:
    return next((nid for nid, key in defs.process_nodes.items() if key == proc_key), None)


def instances(ctx: Any, defs: Any, proc_key: str) -> List[str]:
    """Nodes that are ``instance_of`` the process, of one of its instance types."""
    proc = defs.process(proc_key)
    pnode = _process_node(defs, proc_key)
    if proc is None or pnode is None:
        return []
    ctx.index.ensure([pnode])
    types = set(proc["instance_types"])
    return [m for _, _, m in ctx.index.neighbours(pnode, "instance_of", "in")
            if ctx.index.type_of(m) in types]


def current_step(ctx: Any, defs: Any, proc_key: str, node_id: str) -> Optional[str]:
    for _, _, m in ctx.index.neighbours(node_id, "in_state", "out"):
        placed = defs.step_nodes.get(m)
        if placed and placed[0] == proc_key:
            return placed[1]
    return None


def _walk(ctx: Any, start: str, path: List[Dict[str, str]]):
    s = ctx.settings
    return follow(ctx.index, [start], path, max_hops=s.max_hops, max_nodes_per_hop=s.max_nodes_per_hop)


def _artefact(ctx: Any, node_id: str, a: Dict[str, Any], kind: str) -> Dict[str, Any]:
    walk = _walk(ctx, node_id, a["path"])
    found = walk.nodes
    alt_nodes: List[str] = []
    if a.get("alternative"):
        alt_nodes = _walk(ctx, node_id, a["alternative"]["path"]).nodes
    need = a.get("min_count", 1)
    entry = {"kind": kind, "type": a["type"], "required": bool(a.get("required")), "min_count": need,
             "path": path_text(a["path"]), "count": len(found), "verified": a.get("verified", True),
             "missing": bool(a.get("required")) and len(found) < need and len(alt_nodes) < need,
             "truncated": walk.truncated, "_members": found}
    if a.get("alternative"):
        entry["alternative"] = {"type": a["alternative"]["type"], "path": path_text(a["alternative"]["path"]),
                                "count": len(alt_nodes)}
        entry["_alt_members"] = alt_nodes
    if a.get("note"):
        entry["note"] = a["note"]
    return entry


def _dress(ctx: Any, entries: List[Dict[str, Any]], budget: int) -> int:
    """Turn members into drawn items or chips; spend the fact budget on states.

    Drawn items first (they are what the eye lands on), then chip breakdowns,
    smallest first. A chip the budget does not cover keeps ``breakdown: None``
    and loads its members a page at a time on click.
    """
    n = ctx.settings.fanout_threshold
    index = ctx.index
    wanted: List[str] = []
    chips: List[Tuple[int, Dict[str, Any], List[str]]] = []
    for e in entries:
        primary = e.pop("_members")
        members = primary + [m for m in e.pop("_alt_members", []) if m not in primary]
        label = ctx.status_label(e["type"]) if members else None
        e["status_attribute"] = label
        if len(members) <= n:
            e["items"] = [{"id": m, "name": index.nodes.get(m, {}).get("name") or m, "type": index.type_of(m)}
                          for m in members]
            e["chip"] = None
            if label:
                wanted += members
        else:
            e["items"] = []
            e["chip"] = {"count": len(members), "breakdown": None}
            if label and len(members) <= ctx.settings.chip_status_max:
                chips.append((len(members), e, members))
    chips.sort(key=lambda c: c[0])
    spend = list(dict.fromkeys(wanted))[:budget]
    for size, e, members in chips:
        if len(spend) + size > budget:
            break
        spend += members
    facts = read_facts(ctx.state, ctx.reader, spend) if spend else {}
    for e in entries:
        label = e.get("status_attribute")
        for item in e.get("items") or []:
            if item["id"] in facts:
                item["status"] = status_of(facts[item["id"]], label)
    for size, e, members in chips:
        if all(m in facts for m in members):
            label = e.get("status_attribute")
            e["chip"]["breakdown"] = breakdown(status_of(facts.get(m), label) for m in members)
    return len(spend)


def _entered(history: List[Tuple[str, str]], step_ids: List[str]) -> Dict[str, str]:
    """First date each step was entered, from the dated current_step facts."""
    out: Dict[str, str] = {}
    for when, value in history:
        if value in step_ids and value not in out:
            out[value] = when[:10]
    return out


def context_only(ctx: Any, defs: Any, proc_key: str, node_id: str, *,
                 show_meta: bool = False, fact_budget: Optional[int] = None) -> Dict[str, Any]:
    """Only the context lane (decisions, risks, events) of a process, for any node.

    The process's declared context paths are applied to the node; a path whose
    first hop cannot start at the node's type (relations.yaml domain/range) is
    left out and named, rather than walked.
    """
    fact_budget = ctx.settings.trace_fact_budget if fact_budget is None else fact_budget
    proc = defs.process(proc_key)
    if proc is None:
        raise NotPlaceable(f"Prozess {proc_key} ist nicht definiert.")
    index = ctx.index
    index.ensure([node_id])
    if node_id not in index.nodes:
        raise NotPlaceable("Knoten nicht gefunden oder nicht sichtbar.")
    start_type = index.type_of(node_id)
    instance = start_type in proc["instance_types"]
    context, skipped = [], []
    for a in proc.get("context") or []:
        if not instance and not descriptions.hop_fits(start_type, a["path"][0]):
            skipped.append({"type": a["type"], "path": path_text(a["path"]),
                            "why": f"{a['path'][0]['relation']} ({a['path'][0]['direction']}) beginnt laut relations.yaml nicht bei {start_type}"})
            continue
        context.append(_artefact(ctx, node_id, a, "context"))
    if not show_meta:
        for e in context:
            is_meta = ctx.meta.classify(e["_members"])
            e["_members"] = [m for m in e["_members"] if not is_meta.get(m)]
            e["count"] = len(e["_members"])
    spent = _dress(ctx, context, fact_budget)
    doc = descriptions.load()
    return ctx.envelope(
        legend=[descriptions.type_entry(t, doc) for t in sorted({e["type"] for e in context} | {start_type})],
        process={"key": proc_key, "name": proc["name"]}, sections=["context"],
        instance={"id": node_id, "name": index.nodes[node_id]["name"], "type": start_type,
                  "key": None, "owner": None, "status": None},
        current={"step": None, "since": None, "days": None}, lanes=[], context=context,
        skipped=skipped, missing=[], unmet=[], fact_reads=spent)


def trace(ctx: Any, defs: Any, proc_key: str, node_id: str, *,
          today: Optional[dt.date] = None, show_meta: bool = False,
          fact_budget: Optional[int] = None) -> Dict[str, Any]:
    today = today or dt.date.today()
    fact_budget = ctx.settings.trace_fact_budget if fact_budget is None else fact_budget
    proc = defs.process(proc_key)
    if proc is None:
        raise NotPlaceable(f"Prozess {proc_key} ist nicht definiert.")
    index = ctx.index
    index.ensure([node_id])
    if node_id not in index.nodes:
        raise NotPlaceable("Instanz nicht gefunden oder nicht sichtbar.")
    if index.type_of(node_id) not in proc["instance_types"]:
        raise NotPlaceable(f"{index.type_of(node_id)} ist kein Instanztyp von {proc_key}.")

    facts = read_facts(ctx.state, ctx.reader, [node_id]).get(node_id)
    values = fact_values(facts)
    step_ids = [s["id"] for s in proc["steps"]]
    current = current_step(ctx, defs, proc_key, node_id)
    since = (fact_since(facts, "current_step") or "")[:10] or None
    entered = _entered(fact_history(facts, "current_step"), step_ids)
    if current and since:
        entered.setdefault(current, since)
    order = {s["id"]: s["order"] for s in proc["steps"]}

    lanes, current_entries, other_entries = [], [], []
    for step in proc["steps"]:
        if current is None:
            state = "unknown"
        elif step["id"] == current:
            state = "current"
        elif step["id"] in proc["final"]:
            state = "resting" if order[step["id"]] < order.get(current, 0) else "future"
        else:
            state = "done" if order[step["id"]] < order.get(current, 0) else "future"
        entries = [_artefact(ctx, node_id, a, kind) for kind in ("requires", "produces")
                   for a in step.get(kind) or []]
        (current_entries if state == "current" else other_entries).extend(entries)
        lane = {"id": step["id"], "name": step.get("name") or step["id"], "order": step["order"],
                "description": step.get("description", ""), "owner_role": step.get("owner_role"),
                "resting": step.get("resting", False), "state": state,
                "entered": entered.get(step["id"]), "artefacts": entries,
                "exit_criteria": [{"id": c} for c in step.get("exit_criteria") or []]}
        if state == "current":
            lane["exit_criteria"] = comp.exit_criteria(step, defs.rules, values, values.get("key"),
                                                       step["id"], today)
        lanes.append(lane)

    context = [_artefact(ctx, node_id, a, "context") for a in proc.get("context") or []]
    if not show_meta:
        for e in context:
            is_meta = ctx.meta.classify(e["_members"])
            e["_members"] = [m for m in e["_members"] if not is_meta.get(m)]
            e["count"] = len(e["_members"])
    # States cost one read each: the current step and the context first,
    # then the other lanes with what is left of the budget.
    spent = _dress(ctx, current_entries + context, fact_budget)
    spent += _dress(ctx, other_entries, max(fact_budget - spent, 0))

    current_lane = next((l for l in lanes if l["state"] == "current"), None)
    missing = [{"kind": e["kind"], "type": e["type"], "path": e["path"], "found": e["count"],
                "min_count": e["min_count"]} for e in (current_lane or {}).get("artefacts", []) if e["missing"]]
    unmet = [c for c in (current_lane or {}).get("exit_criteria", []) if c.get("met") is False]
    report = comp.kb_report()
    types = {e["type"] for l in lanes for e in l["artefacts"]} | {e["type"] for e in context}
    doc = descriptions.load()
    return ctx.envelope(
        legend=[descriptions.type_entry(t, doc) for t in sorted(types)],
        process={"key": proc_key, "name": proc["name"]},
        instance={"id": node_id, "name": index.nodes[node_id]["name"], "type": index.type_of(node_id),
                  "key": values.get("key"), "owner": values.get("owner"),
                  "status": values.get(proc.get("governing_fact") or "status")},
        current={"step": current, "since": since, "days": comp.days_since(since, today)},
        lanes=lanes, context=context, missing=missing, unmet=unmet,
        kb_report={"generated_at": report["generated_at"]}, fact_reads=spent)


def trace_items(ctx: Any, defs: Any, proc_key: str, node_id: str, where: str, position: int,
                page_number: int = 0) -> Dict[str, Any]:
    """One page of an artefact's members (``where`` is a step id or ``context``)."""
    proc = defs.process(proc_key)
    if proc is None:
        raise NotPlaceable(f"Prozess {proc_key} ist nicht definiert.")
    if where == "context":
        arts = proc.get("context") or []
    else:
        step = next((s for s in proc["steps"] if s["id"] == where), None)
        if step is None:
            raise NotPlaceable(f"Schritt {where} gibt es in {proc_key} nicht.")
        arts = (step.get("requires") or []) + (step.get("produces") or [])
    if not 0 <= position < len(arts):
        raise NotPlaceable("Artefakt unbekannt.")
    a = arts[position]
    ctx.index.ensure([node_id])
    members = _walk(ctx, node_id, a["path"]).nodes
    if a.get("alternative"):
        members += [m for m in _walk(ctx, node_id, a["alternative"]["path"]).nodes if m not in members]
    shown, pages = page(members, page_number, ctx.settings.page_size)
    label = ctx.status_label(a["type"])
    facts = read_facts(ctx.state, ctx.reader, shown) if label else {}
    rows = [{"id": m, "name": ctx.index.nodes.get(m, {}).get("name") or m, "type": ctx.index.type_of(m),
             "status": status_of(facts.get(m), label) if label else None} for m in shown]
    return ctx.envelope(type=a["type"], path=path_text(a["path"]), count=len(members),
                        page=page_number, pages=pages, members=rows)


def board(ctx: Any, defs: Any, proc_key: str, *, owner: str = "", capability: str = "",
          status: str = "", today: Optional[dt.date] = None) -> Dict[str, Any]:
    today = today or dt.date.today()
    proc = defs.process(proc_key)
    if proc is None:
        raise NotPlaceable(f"Prozess {proc_key} ist nicht definiert.")
    if not defs.process_nodes:
        raise NotPlaceable("Die Definitionsknoten des Mandanten sind nicht lesbar; Instanzen lassen sich nicht zuordnen.")
    index = ctx.index
    ids = instances(ctx, defs, proc_key)
    facts = read_facts(ctx.state, ctx.reader, ids)
    governing = proc.get("governing_fact") or "status"
    steps = {s["id"]: s for s in proc["steps"]}

    risks_of: Dict[str, List[str]] = {}
    for nid in ids:
        risks_of[nid] = []
        for a in proc.get("context") or []:
            if a["type"] == "Risk":
                risks_of[nid] += [m for m in _walk(ctx, nid, a["path"]).nodes if m not in risks_of[nid]]
    all_risks = sorted({m for v in risks_of.values() for m in v})
    risk_facts = read_facts(ctx.state, ctx.reader, all_risks)
    risk_status = {m: fact_values(risk_facts.get(m)).get("status") for m in all_risks}

    cap_paths = [a["path"] for s in proc["steps"] for a in (s.get("requires") or []) + (s.get("produces") or [])
                 if a["type"] == "Capability"]
    cards, owners, statuses, capabilities = [], set(), set(), {}
    for nid in ids:
        v = fact_values(facts.get(nid))
        step_id = current_step(ctx, defs, proc_key, nid)
        since = (fact_since(facts.get(nid), "current_step") or "")[:10] or None
        caps: Set[str] = set()
        for p in cap_paths:
            caps |= set(_walk(ctx, nid, p).nodes)
        for c in caps:
            capabilities[c] = index.nodes.get(c, {}).get("name") or c
        if v.get("owner"):
            owners.add(v["owner"])
        if v.get(governing):
            statuses.add(v[governing])
        if owner and v.get("owner") != owner:
            continue
        if status and v.get(governing) != status:
            continue
        if capability and capability not in caps:
            continue
        missing = comp.missing_artefacts(index, nid, steps[step_id], max_hops=ctx.settings.max_hops,
                                         max_nodes_per_hop=ctx.settings.max_nodes_per_hop) \
            if step_id in steps and step_id not in proc["final"] else []
        open_risks = [m for m in risks_of[nid] if risk_status.get(m) != "closed"]
        cards.append({"id": nid, "key": v.get("key"), "name": index.nodes[nid]["name"],
                      "owner": v.get("owner"), "status": v.get(governing), "step": step_id,
                      "since": since, "days_in_step": comp.days_since(since, today),
                      "missing": len(missing), "missing_items": missing, "open_risks": len(open_risks)})
    columns = [{"id": s["id"], "name": s.get("name") or s["id"], "resting": s.get("resting", False),
                "cards": sorted([c for c in cards if c["step"] == s["id"]],
                                key=lambda c: (-c["missing"], -(c["days_in_step"] or 0), c["key"] or ""))}
               for s in proc["steps"]]
    unplaced = [c for c in cards if c["step"] not in steps]
    return ctx.envelope(
        process={"key": proc_key, "name": proc["name"], "governing_fact": governing},
        columns=columns, unplaced=unplaced, total=len(cards),
        filters={"owners": sorted(owners), "statuses": sorted(statuses),
                 "capabilities": [{"id": k, "name": v} for k, v in sorted(capabilities.items(), key=lambda kv: kv[1])]},
        applied={"owner": owner, "capability": capability, "status": status})


def search_instances(ctx: Any, defs: Any, proc_key: str, q: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Instances whose key or name contains ``q``; keys come from the instances' facts."""
    ids = instances(ctx, defs, proc_key)
    facts = read_facts(ctx.state, ctx.reader, ids)
    needle = " ".join(str(q or "").split()).lower()
    rows = []
    for nid in ids:
        key = fact_values(facts.get(nid)).get("key") or ""
        name = ctx.index.nodes[nid]["name"]
        if not needle or needle in key.lower() or needle in name.lower():
            rows.append({"id": nid, "key": key, "name": name,
                         "step": current_step(ctx, defs, proc_key, nid)})
    rows.sort(key=lambda r: (r["key"] or r["name"]))
    return rows[:limit]
