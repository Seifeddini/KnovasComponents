"""Fixing in place (Step 7): what each TRACE artefact allows, the picker, and the gaps worklist ("Lücken").

Only a declared path of one hop is a single edge of the instance, so only such an artefact can be
fixed from the view; a longer path names its intermediate node instead. Whether the edge can be
changed here, and where it is fixed otherwise, comes from the edit map (graph_views.writers).
Candidates come from KnowledgeBase's suggestions.json (DEC-0025), on top, with their reason; then
a search over the nodes of the target type.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from graph_views import completeness as comp
from graph_views import descriptions, discovery, edit_map, process_views
from graph_views.change_set import lock_info
from graph_views.facts import KeyedTTL, fact_values, read_facts
from graph_views.traverse import path_text


def _artefacts(proc: Dict[str, Any], where: str) -> List[Dict[str, Any]]:
    if where == "context":
        return proc.get("context") or []
    step = next((s for s in proc["steps"] if s["id"] == where), None)
    return ((step.get("requires") or []) + (step.get("produces") or [])) if step else []


def edit_of(instance_type: str, artefact: Dict[str, Any]) -> Dict[str, Any]:
    """The edit info of one declared artefact of an instance."""
    hops = artefact["path"]
    if len(hops) != 1:
        via = " → ".join(h["to_type"] for h in hops[:-1])
        return {"routable": False, "known": False, "editable": False, "indirect": True,
                "why": f"Indirekter Pfad über {via}: dort ändern, nicht an dieser Instanz."}
    info = lock_info(edit_map.relation_id(instance_type, hops[0]))
    info["indirect"] = False
    return info


def annotate_trace(defs: Any, proc_key: str, body: Dict[str, Any], pending: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Add ``edit`` to every lane and context artefact, and the instance's pending pull requests."""
    proc = defs.process(proc_key)
    itype = body["instance"]["type"]
    for lane in body.get("lanes") or []:
        for a, d in zip(lane["artefacts"], _artefacts(proc, lane["id"])):
            a["edit"] = edit_of(itype, d)
    for a, d in zip(body.get("context") or [], proc.get("context") or []):
        a["edit"] = edit_of(itype, d)
    body["pending"] = [{"pr_number": s["pr_number"], "pr_url": s["pr_url"], "state": s["state"],
                        "ops": [{k: o.get(k) for k in ("edit_id", "kind", "target", "new_target", "field")}
                                for o in s["ops"] if o.get("instance") == body["instance"]["id"]]}
                       for s in pending]
    return body


def target_key(ctx: Any, node_id: str) -> str:
    """The key of a shown node: what a source file names it by."""
    key = fact_values(read_facts(ctx.state, ctx.reader, [node_id]).get(node_id)).get("key")
    if not key:
        raise ValueError("Der Knoten hat keinen Schlüssel (Fakt key); er lässt sich nicht in einer Datei benennen.")
    return key


def picker(ctx: Any, defs: Any, cache: KeyedTTL, *, process: str, node: str, where: str, position: int,
           q: str = "", key_index_max: int = 60, limit: int = 20) -> Dict[str, Any]:
    """Candidates for one artefact: suggestions (with reason and score) on top, then search results."""
    proc = defs.process(process)
    arts = _artefacts(proc, where) if proc else []
    if not 0 <= position < len(arts):
        raise process_views.NotPlaceable("Artefakt unbekannt.")
    a = arts[position]
    itype = ctx.index.type_of(node)
    edit = edit_of(itype, a)
    target_type = a["path"][-1]["to_type"]
    key = fact_values(read_facts(ctx.state, ctx.reader, [node]).get(node)).get("key") or ""
    item = edit_map.suggestions_for(key, path_text(a["path"]))
    keys = discovery.key_index(ctx, cache, [target_type], key_index_max)
    by_key = {k: nid for nid, k in keys["keys"].items()}
    suggestions = []
    for c in (item or {}).get("candidates") or []:
        nid = by_key.get(c["target"])
        suggestions.append({**c, "id": nid, "name": ctx.index.nodes[nid]["name"] if nid else c["target"]})
    results = []
    needle = " ".join(str(q or "").split()).lower()
    if len(needle) >= 2:
        for nid in ctx.index.by_type.get(target_type, []):
            n = ctx.index.nodes[nid]
            k = keys["keys"].get(nid) or ""
            if needle in n["name"].lower() or needle in k.lower():
                results.append({"id": nid, "name": n["name"], "key": k or None})
            if len(results) >= limit:
                break
    return ctx.envelope(type=descriptions.type_entry(target_type), path=path_text(a["path"]), edit=edit,
                        suggestions=suggestions, no_rule=bool(item and item.get("no_rule")),
                        suggestions_as_of=edit_map.suggestions_generated_at(), results=results)


def gaps(ctx: Any, defs: Any, *, process: str = "", step: str = "", type_name: str = "",
         owner: str = "") -> Dict[str, Any]:
    """One row per missing artefact or flagged rule, across all instances of the chosen processes."""
    keys = [process] if process else [p["key"] for p in defs.processes]
    report = comp.kb_report()
    rows, options = [], {"processes": [], "steps": set(), "types": set(), "owners": set()}
    for pk in keys:
        proc = defs.process(pk)
        if proc is None:
            continue
        options["processes"].append({"key": pk, "name": proc["name"]})
        try:
            board = process_views.board(ctx, defs, pk)
        except process_views.NotPlaceable:
            continue
        steps = {s["id"]: s for s in proc["steps"]}
        for col in board["columns"]:
            for card in col["cards"]:
                base = {"instance": card["id"], "key": card["key"], "name": card["name"], "process": pk,
                        "process_name": proc["name"], "step": card["step"], "owner": card["owner"]}
                itype = ctx.index.type_of(card["id"])
                for m in card["missing_items"]:
                    arts = _artefacts(proc, card["step"]) if card["step"] in steps else []
                    pos = next((i for i, a in enumerate(arts) if a["type"] == m["type"] and path_text(a["path"]) == m["path"]), None)
                    sug = edit_map.suggestions_for(card["key"] or "", m["path"])
                    rows.append({**base, "kind": "missing", "type": m["type"], "path": m["path"],
                                 "why": f"Fehlt: {m['found']} von {m['min_count']}" + (f" (oder {m['alternative']})" if m.get("alternative") else ""),
                                 "where": card["step"], "position": pos,
                                 "edit": edit_of(itype, arts[pos]) if pos is not None else None,
                                 "candidates": (sug or {}).get("candidates") or [],
                                 "no_rule": bool(sug and sug.get("no_rule"))})
                for r in (report["rows"].get(card["key"] or "") or {}).get("rule_results") or []:
                    if r.get("passed") is False:
                        rows.append({**base, "kind": "rule", "type": None, "path": None, "rule": r["id"],
                                     "why": "; ".join(r.get("why") or []) or r["id"], "edit": None,
                                     "candidates": [], "no_rule": True, "source": "kb-report"})
    for r in rows:
        options["steps"].add(r["step"] or "")
        options["owners"].add(r["owner"] or "")
        if r["type"]:
            options["types"].add(r["type"])
    shown = [r for r in rows if (not step or r["step"] == step) and (not type_name or r["type"] == type_name)
             and (not owner or (r["owner"] or "") == owner)]
    return ctx.envelope(rows=shown, total=len(rows), kb_report_as_of=report["generated_at"],
                        suggestions_as_of=edit_map.suggestions_generated_at(),
                        filters={"processes": options["processes"], "steps": sorted(s for s in options["steps"] if s),
                                 "types": sorted(options["types"]), "owners": sorted(o for o in options["owners"] if o)},
                        applied={"process": process, "step": step, "type": type_name, "owner": owner})


def suggestion_for(defs: Any, process: str, key: str, where: str, position: int, target: str) -> Optional[Dict[str, Any]]:
    """The suggestion a chosen target came from, so the PR and the audit can name its rule and reason."""
    proc = defs.process(process)
    arts = _artefacts(proc, where) if proc else []
    if not 0 <= position < len(arts):
        return None
    item = edit_map.suggestions_for(key, path_text(arts[position]["path"]))
    return next(({k: c.get(k) for k in ("rule", "score", "reason")} for c in (item or {}).get("candidates") or []
                 if c["target"] == target), None)


def op_applied(ctx: Any, op: Dict[str, Any]) -> bool:
    """Does the tenant show a link operation's result? (Field operations count as shown once merged.)"""
    if op["kind"] not in ("add", "remove", "replace"):
        return True
    inst = op["instance"]
    itype = ctx.index.type_of(inst)
    frm, rel, to = op["edit_id"].split(" ")
    direction, other_type = ("out", to) if frm == itype else ("in", frm)
    others = [m for _, _, m in ctx.index.neighbours(inst, rel, direction) if ctx.index.type_of(m) == other_type]
    facts = read_facts(ctx.state, ctx.reader, others[:200])
    keys = {fact_values(facts.get(m)).get("key") for m in others[:200]} - {None}
    if other_type == "AlloyCheck":                      # a check stands for its model
        keys = {k[len("ALLOY-"):].split("#", 1)[0] for k in keys if k.startswith("ALLOY-")}
    if op["kind"] == "replace":
        return op["target"] not in keys and op["new_target"] in keys
    return (op["target"] in keys) == (op["kind"] == "add")


def edit_id_at(defs: Any, process: str, instance_type: str, where: str, position: int) -> str:
    proc = defs.process(process)
    arts = _artefacts(proc, where) if proc else []
    if not 0 <= position < len(arts) or len(arts[position]["path"]) != 1:
        raise process_views.NotPlaceable("Dieses Artefakt ist kein einzelner Schritt von der Instanz aus.")
    return edit_map.relation_id(instance_type, arts[position]["path"][0])
