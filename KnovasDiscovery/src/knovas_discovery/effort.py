"""Effort of a custom source, in points from measured facts (docs/design.md, "Effort of a custom source").

Every driver returns what it measured and the points it gives, so the total can be checked by hand.
"""

from __future__ import annotations

LABELS = {
    "record_tables": "Tables that hold the firm's records",
    "foreign_keys": "No foreign keys (relations have to be worked out)",
    "change_markers": "Change markers on the record tables",
    "removed_rows": "Rows that are removed without a flag",
    "rights_in_source": "Rights kept in the source",
    "custom_fields": "Fields the client defined itself",
    "unknown_product": "Product not in the catalogue",
    "volume": "More than a million rows in the record tables",
    "authentication": "Only Windows authentication",
}


def _record_rows(facts: dict) -> int:
    if facts.get("record_rows") is not None:
        return int(facts["record_rows"])
    return sum(int(t.get("rows") or 0) for t in facts.get("table_list") or [] if t.get("role") == "records")


def driver(name: str, facts: dict, cfg: dict) -> dict:
    """One driver: {driver, label, measured, rule, points}."""
    c = cfg.get(name) or {}
    if name == "record_tables":
        n = int(facts.get("record_tables") or 0)
        pts = min(n * int(c.get("each", 1)), int(c.get("max", n)))
        return _d(name, f"{n} record tables", f"{c.get('each')} each, at most {c.get('max')}", pts)
    if name == "foreign_keys":
        n = int(facts.get("foreign_keys") or 0)
        return _d(name, f"{n} declared", f"none: {c.get('none')}", int(c.get("none", 0)) if n == 0 else 0)
    if name == "change_markers":
        total = int(facts.get("record_tables") or 0)
        without = int(facts.get("record_tables_without_change_marker") or 0)
        state = "all" if without == 0 else ("none" if without >= total else "some")
        return _d(name, f"{total - without} of {total} record tables have one ({state})",
                  f"all {c.get('all')}, some {c.get('some')}, none {c.get('none')}", int(c.get(state, 0)))
    if name == "removed_rows":
        n = int(facts.get("record_tables_without_delete_flag") or 0)
        return _d(name, f"{n} record tables without a delete flag", f"present: {c.get('present')}",
                  int(c.get("present", 0)) if n > 0 else 0)
    if name == "rights_in_source":
        n = int(facts.get("rights_tables") or 0)
        return _d(name, f"{n} rights tables", f"present: {c.get('present')}", int(c.get("present", 0)) if n > 0 else 0)
    if name == "custom_fields":
        n = int(facts.get("custom_field_tables") or 0)
        return _d(name, f"{n} custom-field tables", f"present: {c.get('present')}",
                  int(c.get("present", 0)) if n > 0 else 0)
    if name == "unknown_product":
        product = facts.get("product")
        return _d(name, f"product: {product}" if product else "product not recognised", f"present: {c.get('present')}",
                  int(c.get("present", 0)) if not product else 0)
    if name == "volume":
        rows = _record_rows(facts)
        return _d(name, f"{rows:,} rows in record tables", f"over one million: {c.get('over_one_million_rows')}",
                  int(c.get("over_one_million_rows", 0)) if rows > 1_000_000 else 0)
    if name == "authentication":
        auth = facts.get("authentication")
        return _d(name, f"authentication: {auth or 'unknown'}", f"Windows only: {c.get('windows_only')}",
                  int(c.get("windows_only", 0)) if auth == "windows" else 0)
    raise ValueError(f"unknown effort driver {name!r}")


def _d(name: str, measured: str, rule: str, points: int) -> dict:
    return {"driver": name, "label": LABELS[name], "measured": measured, "rule": rule, "points": int(points)}


def effort_class(points: int, classes: dict) -> str:
    for name, spec in classes.items():
        limit = (spec or {}).get("max_points")
        if limit is None or points <= int(limit):
            return name
    return list(classes)[-1]


def price(source_class: str, eclass: str | None, pricing: dict) -> str:
    classes = pricing.get("classes") or {}
    currency = pricing.get("currency") or "CHF"
    if source_class == "D":
        return "not offered"
    spec = classes.get(source_class) or {}
    value = spec.get(eclass) if source_class == "C" else spec.get("chf")
    if source_class == "C" and eclass is None:
        return "price not set"
    if value is None:
        return "price not set"
    return f"{currency} {int(value):,}" if isinstance(value, (int, float)) else str(value)


def compute(entry_drivers: list[str], facts: dict | None, review_points: list[dict], pricing: dict,
            computable: bool) -> dict:
    """The effort of a class C source: computed from facts, or set by the consultant (manual)."""
    cfg = pricing.get("effort_points") or {}
    added = [{"points": int(r.get("points") or 0), "reason": str(r.get("reason") or "")} for r in review_points]
    manual = not entry_drivers or entry_drivers == ["manual"] or not computable or facts is None
    drivers = [] if manual else [driver(d, facts, cfg) for d in entry_drivers]
    if manual and not added:
        return {"mode": "manual", "drivers": [], "review_points": [], "points": None, "effort_class": None,
                "note": "effort: set by the consultant in review.yaml"}
    points = sum(d["points"] for d in drivers) + sum(a["points"] for a in added)
    return {"mode": "manual" if manual else "computed", "drivers": drivers, "review_points": added,
            "points": points, "effort_class": effort_class(points, pricing.get("effort_classes") or {}),
            "note": None}
