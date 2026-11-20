"""The catalogue of kinds of source (sources.yaml) and what each class costs (pricing.yaml)."""

from __future__ import annotations

from pathlib import Path

from ..util import load_yaml

from ..conditions import OPERATORS, placeholders

HERE = Path(__file__).parent
SOURCES = HERE / "sources.yaml"
PRICING = HERE / "pricing.yaml"

CLASSES = ("A", "B", "C", "D")
ON = ("fileshare", "directory", "mssql", "m365", "intake")
CATEGORIES = ("documents", "records", "identity", "mail", "collaboration", "finance", "other")
KNOVAS_STATUS = ("released", "built", "draft", "partly", "planned", "none")


def load_catalogue(path: Path | None = None) -> dict:
    return load_yaml(Path(path or SOURCES).read_text(encoding="utf-8"))


def load_pricing(path: Path | None = None) -> dict:
    return load_yaml(Path(path or PRICING).read_text(encoding="utf-8"))


def _fact_known(path: str, known: dict[str, str]) -> bool:
    """A fact path is known if a probe produces it, or it lies inside a fact that is a map (extensions.<ext>)."""
    if path in known:
        return True
    return any(path.startswith(k + ".") for k in known)


def check(catalogue: dict, pricing: dict, facts: dict[str, dict[str, str]]) -> list[str]:
    """Problems in the catalogue: unknown operators, facts no probe produces, duplicate keys, bad classes."""
    problems: list[str] = []
    entries = catalogue.get("entries") or []
    checks = catalogue.get("checks") or []
    drivers = set((pricing.get("effort_points") or {}).keys())

    def cond_problems(where: str, on: str, conditions) -> None:
        if not isinstance(conditions, list) or not conditions:
            problems.append(f"{where}: `when` must be a non-empty list")
            return
        for c in conditions:
            if not isinstance(c, dict) or "fact" not in c or "op" not in c:
                problems.append(f"{where}: a condition needs fact and op: {c!r}")
                continue
            if c["op"] not in OPERATORS:
                problems.append(f"{where}: unknown operator {c['op']!r}")
            if c["op"] in ("contains_any", "contains_all") and not isinstance(c.get("value"), list):
                problems.append(f"{where}: {c['op']} needs a list as value")
            if on in facts and not _fact_known(c["fact"], facts[on]):
                problems.append(f"{where}: no {on} probe produces the fact {c['fact']!r}")

    seen: set[str] = set()
    groups: dict[str, list[tuple[int, str]]] = {}
    for i, e in enumerate(entries):
        key = e.get("key") if isinstance(e, dict) else None
        where = f"entry {key or i}"
        if not key:
            problems.append(f"entry #{i}: no key")
            continue
        if key in seen:
            problems.append(f"{where}: duplicate key")
        seen.add(key)
        for field in ("name", "category", "class", "knovas", "detect"):
            if field not in e:
                problems.append(f"{where}: missing {field}")
        if e.get("class") not in CLASSES:
            problems.append(f"{where}: class must be one of {', '.join(CLASSES)}")
        if e.get("category") not in CATEGORIES:
            problems.append(f"{where}: unknown category {e.get('category')!r}")
        status = (e.get("knovas") or {}).get("status")
        if status not in KNOVAS_STATUS:
            problems.append(f"{where}: unknown knovas.status {status!r}")
        for j, block in enumerate(e.get("detect") or []):
            on = block.get("on")
            if on not in ON:
                problems.append(f"{where} detect #{j}: unknown `on` {on!r}")
                continue
            cond_problems(f"{where} detect #{j}", on, block.get("when"))
        if "group" in e:
            if not isinstance(e.get("priority"), int):
                problems.append(f"{where}: an entry with a group needs an integer priority")
            else:
                groups.setdefault(e["group"], []).append((e["priority"], key))
        for d in e.get("effort_drivers") or []:
            if d != "manual" and d not in drivers:
                problems.append(f"{where}: unknown effort driver {d!r} (not in pricing.yaml effort_points)")
        if "manual" in (e.get("effort_drivers") or []) and len(e["effort_drivers"]) > 1:
            problems.append(f"{where}: effort_drivers [manual] cannot be combined with other drivers")
        sig = e.get("signature")
        if sig is not None and not (isinstance(sig, dict) and isinstance(sig.get("tables"), list) and sig["tables"]):
            problems.append(f"{where}: signature needs a non-empty list `tables`")
        for field in ("needs_from_client", "risks", "questions"):
            if not isinstance(e.get(field, []), list):
                problems.append(f"{where}: {field} must be a list")
    for group, members in groups.items():
        prios = [p for p, _ in members]
        if len(prios) != len(set(prios)):
            problems.append(f"group {group}: two entries have the same priority")

    seen_checks: set[str] = set()
    for i, c in enumerate(checks):
        key = c.get("key") if isinstance(c, dict) else None
        where = f"check {key or i}"
        if not key:
            problems.append(f"check #{i}: no key")
            continue
        if key in seen_checks or key in seen:
            problems.append(f"{where}: duplicate key")
        seen_checks.add(key)
        on = c.get("on")
        if on not in ON:
            problems.append(f"{where}: unknown `on` {on!r}")
            continue
        cond_problems(where, on, c.get("when"))
        for p in placeholders(c.get("text") or ""):
            if on in facts and not _fact_known(p, facts[on]):
                problems.append(f"{where}: text names {p!r}, which no {on} probe produces")
        if not c.get("text"):
            problems.append(f"{where}: no text")

    ec = pricing.get("effort_classes") or {}
    bounds = [v.get("max_points") for v in ec.values()]
    if not ec or bounds[-1] is not None or any(b is None for b in bounds[:-1]) or bounds[:-1] != sorted(bounds[:-1]):
        problems.append("pricing: effort_classes must rise, and only the last may have max_points null")
    for cls in CLASSES:
        if cls not in (pricing.get("classes") or {}):
            problems.append(f"pricing: classes has no {cls}")
    return problems
