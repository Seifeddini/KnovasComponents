"""What an instance is missing at its step (decision D4).

Missing artefacts and P-rules are computed here the way KnowledgeBase's
scripts/client_zero/process_completeness.py computes them: the step's
``requires`` and its ``produces`` with ``required: true``, followed along their
declared paths, the alternative counted, ``min_count`` respected. R-rules are
not re-implemented: their results are read from KnowledgeBase's
process-completeness report (named in the tenant's bundle) and labelled with its time.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from graph_views import bundle
from graph_views.traverse import follow, path_text


def missing_artefacts(index: Any, node_id: str, step: Dict[str, Any], *,
                      max_hops: int = 4, max_nodes_per_hop: int = 2000) -> List[Dict[str, Any]]:
    out = []
    for kind in ("requires", "produces"):
        for a in step.get(kind) or []:
            if not a.get("required"):
                continue
            n = len(follow(index, [node_id], a["path"], max_hops=max_hops,
                           max_nodes_per_hop=max_nodes_per_hop).nodes)
            alt = a.get("alternative")
            n_alt = len(follow(index, [node_id], alt["path"], max_hops=max_hops,
                               max_nodes_per_hop=max_nodes_per_hop).nodes) if alt else 0
            need = a.get("min_count", 1)
            if n < need and n_alt < need:
                row = {"kind": kind, "type": a["type"], "path": path_text(a["path"]),
                       "found": n, "min_count": need}
                if alt:
                    row["alternative"] = f"{alt['type']} {path_text(alt['path'])}"
                if a.get("note"):
                    row["note"] = a["note"]
                out.append(row)
    return out


def p_rule_met(rule: Dict[str, Any], facts: Dict[str, str], today: dt.date) -> bool:
    check = rule.get("check")
    if check == "fact_present":
        return bool(facts.get(rule["fact"]))
    if check == "facts_present":
        return all(facts.get(f) and facts.get(f) != "not set" for f in rule["facts"])
    if check == "date_not_past":
        value = facts.get(rule["fact"], "")
        return bool(value) and value[:10] >= today.isoformat()
    return False


def _report() -> Dict[str, Any]:
    return bundle.current().report("process_completeness")


def kb_report() -> Dict[str, Any]:
    """KnowledgeBase's completeness report: rows by instance key, and its time."""
    data = _report()
    return {"generated_at": data.get("generated_at"),
            "rows": {r["instance"]: r for r in data.get("instances") or []}}


def exit_criteria(step: Dict[str, Any], rules: List[Dict[str, Any]], facts: Dict[str, str],
                  key: Optional[str], step_id: str, today: dt.date) -> List[Dict[str, Any]]:
    """Each exit criterion of the step: met, not met, or the KB report's word on it."""
    by_id = {r["id"]: r for r in rules}
    report = kb_report()
    row = report["rows"].get(key or "")
    out = []
    for crit in step.get("exit_criteria") or []:
        if crit in by_id:
            met = p_rule_met(by_id[crit], facts, today)
            out.append({"id": crit, "met": met, "source": "platform",
                        "why": None if met else by_id[crit].get("description") or by_id[crit]["name"]})
            continue
        entry = {"id": crit, "source": "kb-report", "as_of": report["generated_at"]}
        if row is None:
            entry.update(met=None, why="nicht im KB-Regelbericht")
        elif row.get("step") != step_id:
            entry.update(met=None, why=f"KB-Regelbericht stand bei Schritt {row.get('step')}")
        else:
            hit = next((u for u in row.get("unmet") or [] if u.get("id") == crit), None)
            entry.update(met=hit is None, why=hit.get("why") if hit else None)
        out.append(entry)
    return out


def days_since(date_text: Optional[str], today: dt.date) -> Optional[int]:
    if not date_text:
        return None
    try:
        return (today - dt.date.fromisoformat(str(date_text)[:10])).days
    except ValueError:
        return None

