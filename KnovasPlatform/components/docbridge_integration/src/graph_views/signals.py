"""Colour signals of a map (WP KC-2): what colours a node, as a profile view's ``color_by`` names it.

* ``docs_completeness``: the node's row in KnowledgeBase's documentation completeness report
  (report ``docs_completeness`` of the bundle, rows by key): ``complete`` (no empty required
  section), ``incomplete`` (some), ``stale`` (complete, but sections are out of date), ``none`` (not
  in the report).
* ``check_status``: the status rule of the node's page definition (a section with ``status``: a
  path to the checks, the fact and the value that means passing). Every item of that section (an
  invariant the component keeps) is checked through its first check, the way the documentation
  page marks it: ``passing`` (every item verified by a passing check), ``failing`` (a check did not
  pass), ``partial`` (some items have no check), ``none`` (no item, or the page has no such rule),
  ``unread`` (the fact budget ran out before every check was read).

Nothing here names a relation, type or fact: the report, the page definitions and the path
definitions say them. Check facts cost a read each and are paced like every fact read (plan D3).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from graph_views import bundle, doc_pages
from graph_views.facts import fact_values, read_facts
from graph_views.traverse import follow

#: The signals a map knows, with the value order the legend uses (best first).
SIGNALS: Dict[str, Dict[str, Any]] = {
    "docs_completeness": {"label": "Dokumentation",
                          "values": ["complete", "stale", "incomplete", "none"]},
    "check_status": {"label": "Prüfungen",
                     "values": ["passing", "partial", "failing", "unread", "none"]},
}
LABELS = {"complete": "vollständig", "stale": "veraltet", "incomplete": "unvollständig", "none": "keine Angabe",
          "passing": "bestanden", "partial": "teilweise geprüft", "failing": "nicht bestanden",
          "unread": "nicht gelesen"}


def known(names: List[str]) -> List[str]:
    return [n for n in names or [] if n in SIGNALS]


def legend(names: List[str]) -> List[Dict[str, Any]]:
    return [{"id": n, "label": SIGNALS[n]["label"],
             "values": [{"value": v, "label": LABELS[v]} for v in SIGNALS[n]["values"]]} for n in known(names)]


def docs_completeness(key: Optional[str]) -> str:
    rows = bundle.current().memo("docs_completeness_rows", lambda: {
        r.get("component"): r for r in bundle.current().report("docs_completeness").get("components") or []})
    row = rows.get(key or "")
    if row is None:
        return "none"
    if row.get("empty_required"):
        return "incomplete"
    return "stale" if row.get("stale") else "complete"


def _status_rule(type_name: str, key: Optional[str]) -> Optional[Dict[str, Any]]:
    page = doc_pages.page_for(type_name, key) or {}
    return next((s for s in page.get("sections") or [] if s.get("status") and s.get("paths")), None)


def check_status(ctx: Any, rows: List[Dict[str, Any]], budget: int) -> Dict[str, str]:
    """``check_status`` per row id. Reads at most ``budget`` check facts not yet cached."""
    items: Dict[str, List[str]] = {}
    checks: Dict[str, Optional[str]] = {}
    rules: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        rule = _status_rule(r["type"], r.get("key"))
        if rule is None:
            continue
        rules[r["id"]] = rule
        members = list(dict.fromkeys(m for p in rule["paths"] for m in follow(ctx.index, [r["id"]], p["path"]).nodes
                                     if m != r["id"]))
        items[r["id"]] = members
        for m in members:
            found = follow(ctx.index, [m], rule["status"]["path"]).nodes
            checks[m] = found[0] if found else None
    wanted = [c for c in dict.fromkeys(c for c in checks.values() if c)]
    with ctx.state.lock:
        cached = {c for c in wanted if c in ctx.state.facts}
    to_read = [c for c in wanted if c not in cached][:max(0, budget)]
    facts = read_facts(ctx.state, ctx.reader, [c for c in wanted if c in cached] + to_read)
    out: Dict[str, str] = {}
    for r in rows:
        rule = rules.get(r["id"])
        members = items.get(r["id"]) or []
        if rule is None or not members:
            out[r["id"]] = "none"
            continue
        passing = rule["status"].get("passing")
        fact = rule["status"].get("fact")
        states = []
        for m in members:
            c = checks.get(m)
            if c is None:
                states.append("unchecked")
            elif c not in facts:
                states.append("unread")
            else:
                got = fact_values(facts.get(c)).get(fact)
                # A check without the fact says nothing (the page marks it "unknown").
                states.append("unchecked" if not got else ("passing" if got == passing else "failing"))
        if "failing" in states:
            out[r["id"]] = "failing"
        elif "unread" in states:
            out[r["id"]] = "unread"
        elif "unchecked" in states:
            out[r["id"]] = "partial"
        else:
            out[r["id"]] = "passing"
    return out


def colour(ctx: Any, rows: List[Dict[str, Any]], names: List[str], budget: int) -> None:
    """Add ``signals`` (signal -> value) to every row, for the signals the view names."""
    wanted = known(names)
    checks = check_status(ctx, rows, budget) if "check_status" in wanted else {}
    for r in rows:
        values = {}
        if "docs_completeness" in wanted:
            values["docs_completeness"] = docs_completeness(r.get("key"))
        if "check_status" in wanted:
            values["check_status"] = checks.get(r["id"], "none")
        r["signals"] = values
