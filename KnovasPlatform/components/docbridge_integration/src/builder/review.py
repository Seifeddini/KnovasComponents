"""Reviewing a draft before it is published (build plan steps B4 and B5).

What a reviewer reads is diff lines: one group per element of the model
(a type, a relation, a pack's version), and in it one line per thing that
changed -- an attribute added, the parent changed, a type retired. The same
lines show a pack update before it is taken over (B4) and every change of a
draft since the last published version (B5).

``change_set`` takes the draft's change log (builder_change rows) and folds
it per element: the element as it was before the draft's first change and as
it is after its last, so an edit that was undone shows as nothing. With an
author it keeps only that person's changes. Who changed what is kept beside
each element.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

SECTIONS = {"ontology.types": "Typen", "ontology.relations": "Beziehungen", "packs": "Pakete"}
KINDS = {"ontology.types": "Typ", "ontology.relations": "Beziehung", "packs": "Paketstand"}
#: Change rows that are not about one element: where the draft came from.
ORIGINS = {"import": "Paket importiert", "create": "Angelegt"}

_FIELDS = {"label": "Namen", "description": "Beschreibung", "example": "Beispiel",
           "not_to_confuse": "Nicht verwechseln mit", "parent": "Elterntyp", "domain": "Von", "range": "Nach",
           "family": "Familie", "inverse": "Umkehrung", "sentence": "Lesart", "iri": "IRI", "reason": "Begründung",
           "version": "Version"}


def state_of(before: Optional[Mapping[str, Any]], after: Optional[Mapping[str, Any]]) -> str:
    """added, removed (gone or newly retired), restored (retirement taken back) or changed."""
    if before is None and after is None:
        return "unchanged"
    if before is None:
        return "added"
    if after is None or (after.get("retired") and not before.get("retired")):
        return "removed"
    if before.get("retired") and not after.get("retired"):
        return "restored"
    return "changed" if before != after else "unchanged"


def describe(item: Mapping[str, Any]) -> str:
    """One line for an element that is new."""
    parts = [(item.get("label") or {}).get("de") or str(item.get("key"))]
    if "version" in item and "key" in item and len(item) == 2:
        return f"Version {item['version']}"
    if item.get("parent"):
        parts.append(f"Untertyp von {item['parent']}")
    if item.get("attributes"):
        parts.append(f"{len(item['attributes'])} Attribute: " + ", ".join(a["name"] for a in item["attributes"]))
    if item.get("domain") or item.get("range"):
        parts.append(f"{', '.join(item.get('domain') or [])} → {', '.join(item.get('range') or [])}")
    return " · ".join(parts)


def field_lines(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict]:
    """The differences between two versions of an element, one line each."""
    lines = []
    old_attrs = {a["name"]: a for a in before.get("attributes") or []}
    new_attrs = {a["name"]: a for a in after.get("attributes") or []}
    for name, attr in new_attrs.items():
        if name not in old_attrs:
            lines.append({"sign": "added", "text": f"Attribut {name} ({attr.get('datatype', '')})"})
        elif attr != old_attrs[name]:
            lines.append({"sign": "changed", "text": f"Attribut {name} geändert"})
    for name in old_attrs:
        if name not in new_attrs:
            lines.append({"sign": "removed", "text": f"Attribut {name}"})
    if list(old_attrs) != list(new_attrs) and set(old_attrs) == set(new_attrs):
        lines.append({"sign": "changed", "text": "Reihenfolge der Attribute"})
    for field, label in _FIELDS.items():
        old, new = before.get(field), after.get(field)
        if old == new:
            continue
        if isinstance(new, list) and isinstance(old, list):
            added = [v for v in new if v not in old]
            removed = [v for v in old if v not in new]
            if added:
                lines.append({"sign": "added", "text": f"{label}: {', '.join(map(str, added))}"})
            if removed:
                lines.append({"sign": "removed", "text": f"{label}: {', '.join(map(str, removed))}"})
        elif field == "version":
            lines.append({"sign": "changed", "text": f"Version {old or '–'} → {new}"})
        elif not old:
            lines.append({"sign": "added", "text": f"{label}"})
        elif not new:
            lines.append({"sign": "removed", "text": f"{label}"})
        else:
            lines.append({"sign": "changed", "text": f"{label} geändert"})
    return lines


def element_diff(path: str, before: Optional[Mapping[str, Any]], after: Optional[Mapping[str, Any]]) -> dict:
    """One element's group of diff lines."""
    section, _, key = path.partition("/")
    state = state_of(before, after)
    if state == "added":
        lines = [{"sign": "added", "text": describe(after or {})}]
    elif state == "removed":
        lines = [{"sign": "removed", "text": "Stillgelegt: bleibt im Modell, bis die Veröffentlichung entscheidet."
                  if after is not None else "Entfernt"}]
    elif state == "restored":
        lines = [{"sign": "added", "text": "Wieder aktiv"}] + field_lines(
            {**(before or {}), "retired": False}, {**(after or {}), "retired": False})
    elif state == "changed":
        lines = field_lines(before or {}, after or {}) or [{"sign": "changed", "text": "Kleine Änderungen"}]
    else:
        lines = []
    return {"section": section, "kind": KINDS.get(section, section), "key": key, "path": path, "state": state,
            "lines": lines}


def diff(changes: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Diff groups for changes as builder.editing makes them (a pack update preview)."""
    return [element_diff(c["path"], c["before"], c["after"]) for c in changes]


def change_set(rows: Iterable[Mapping[str, Any]], author: Optional[str] = None) -> dict:
    """The draft's changes, folded per element and grouped by section.

    ``rows``: builder_change rows of the draft, oldest first, with path,
    before, after, actor, actor_name, at. ``author``: an actor id to keep
    only that person's changes.
    """
    origin = None
    elements: dict[str, dict] = {}
    authors: dict[str, str] = {}
    for row in rows:
        actor = str(row["actor"]) if row.get("actor") else ""
        if actor:
            authors[actor] = row.get("actor_name") or actor
        if row["path"] in ORIGINS:
            origin = {"label": ORIGINS[row["path"]], "at": row.get("at"), "actor_name": row.get("actor_name")}
            continue
        if author and actor != author:
            continue
        entry = elements.setdefault(row["path"], {"before": row["before"], "after": row["after"], "authors": {},
                                                  "count": 0, "last_at": row.get("at")})
        entry["after"] = row["after"]
        entry["count"] += 1
        entry["last_at"] = row.get("at")
        if actor:
            entry["authors"][actor] = row.get("actor_name") or actor
    sections: dict[str, list] = {}
    for path, entry in elements.items():
        group = element_diff(path, entry["before"], entry["after"])
        if group["state"] == "unchanged":
            continue
        group.update(authors=list(entry["authors"].values()), author_ids=list(entry["authors"]),
                     count=entry["count"], last_at=entry["last_at"])
        sections.setdefault(group["section"], []).append(group)
    ordered = [{"section": s, "label": SECTIONS.get(s, s),
                "elements": sorted(sections[s], key=lambda g: str(g["key"]).lower())}
               for s in SECTIONS if s in sections]
    counts = {state: sum(1 for s in ordered for g in s["elements"] if g["state"] == state)
              for state in ("added", "changed", "removed", "restored")}
    return {"origin": origin, "sections": ordered, "counts": counts, "authors": authors,
            "total": sum(counts.values())}


def destructive(change_set_: Mapping[str, Any]) -> list[dict]:
    """The elements a publish would retire or remove: they need a second, explicit confirmation."""
    return [g for s in change_set_["sections"] for g in s["elements"] if g["state"] == "removed"]
