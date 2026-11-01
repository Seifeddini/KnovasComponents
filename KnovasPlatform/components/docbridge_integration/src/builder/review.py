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

from builder import processes

SECTIONS = {"ontology.types": "Typen", "ontology.relations": "Beziehungen", "packs": "Pakete",
            "processes": "Prozesse", "processes.rules": "Prozessregeln", "sources": "Quellen", "mappings": "Zuordnungen",
            "spines": "Hierarchien", "pages": "Seiten", "profiles": "Profile", "profile_roles": "Rollen und Profile",
            "access.groups": "Zugriffsgruppen", "access.rules": "Ordnerregeln"}
KINDS = {"ontology.types": "Typ", "ontology.relations": "Beziehung", "packs": "Paketstand",
         "processes": "Prozess", "processes.rules": "Prozessregel", "sources": "Quelle", "mappings": "Zuordnung",
         "spines": "Hierarchie", "pages": "Seite", "profiles": "Profil", "profile_roles": "Rolle",
         "access.groups": "Zugriffsgruppe", "access.rules": "Ordnerregel"}
#: Change rows that are not about one element: where the draft came from.
ORIGINS = {"import": "Paket importiert", "create": "Angelegt"}

_FIELDS = {"label": "Namen", "description": "Beschreibung", "example": "Beispiel",
           "not_to_confuse": "Nicht verwechseln mit", "parent": "Elterntyp", "domain": "Von", "range": "Nach",
           "family": "Familie", "inverse": "Umkehrung", "sentence": "Lesart", "iri": "IRI", "reason": "Begründung",
           "version": "Version", "name": "Name", "owner": "Verantwortlich", "component": "Komponente",
           "settings": "Einstellungen", "credential_set": "Zugangsdaten"}


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
    if "prefix" in item and "groups" in item:
        return f"{item['prefix']} → {', '.join(item['groups'])}"
    if set(item) <= {"key", "name", "parent"} and "name" in item:
        return f"{item['name']}" + (f" unter {item['parent']}" if item.get("parent") else " unter den Administratoren")
    if isinstance(item.get("definition"), Mapping) and "audience" in item["definition"]:
        d = item["definition"]
        return f"{d.get('audience')} · {len(d.get('views') or [])} Ansichten · {item.get('file')}"
    if "profiles" in item and "key" in item and len(item) == 2:
        return f"sieht {', '.join(item['profiles'])}"
    if "levels" in item:
        return f"{item.get('name') or item.get('key')} · " + " → ".join(str(lv.get("name")) for lv in item["levels"])
    if "sections" in item:
        return f"Seite für {item.get('type')} · " + ", ".join(
            str((s.get("title") or {}).get("de") or s.get("id")) for s in item["sections"])
    if "steps" in item:
        steps = processes.step_ids(item)
        return f"{item.get('name') or item.get('key')} · {len(steps)} Schritte" + (f": {' → '.join(steps)}" if steps else "")
    if "check" in item:
        return f"{item.get('name') or item.get('id')} ({item['id']})"
    if "sync_mode" in item:
        return f"{item.get('name')} · {item.get('kind')}"
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


_PROCESS_FIELDS = {"name": "Name", "description": "Beschreibung", "owner_role": "Verantwortliche Rolle",
                   "instance_types": "Instanztypen", "governing_fact": "Steuerndes Attribut", "value_stream": "Wertstrom",
                   "context": "Kontext-Artefakte", "change_classes": "Änderungsklassen"}
_STEP_FIELDS = {"name": "Name", "description": "Beschreibung", "owner_role": "Rolle", "resting": "Ruheschritt",
                "requires": "braucht", "produces": "liefert", "exit_criteria": "Abschlusskriterien"}


def process_lines(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict]:
    """The differences between two versions of a process: steps, transitions, status map, the rest."""
    lines = []
    old_steps = {str(processes.step_of(s).get("id")): processes.step_of(s) for s in before.get("steps") or []}
    new_steps = {str(processes.step_of(s).get("id")): processes.step_of(s) for s in after.get("steps") or []}
    for sid, step in new_steps.items():
        if sid not in old_steps:
            lines.append({"sign": "added", "text": f"Schritt {sid}"})
            continue
        was = old_steps[sid]
        changed = [label for field, label in _STEP_FIELDS.items() if was.get(field) != step.get(field)]
        if changed:
            lines.append({"sign": "changed", "text": f"Schritt {sid}: {', '.join(changed)}"})
    for sid in old_steps:
        if sid not in new_steps:
            lines.append({"sign": "removed", "text": f"Schritt {sid}"})
    shared = [s for s in new_steps if s in old_steps]
    if [s for s in old_steps if s in new_steps] != shared:
        lines.append({"sign": "changed", "text": "Reihenfolge der Schritte"})
    old_t = {(a, b): r for a, b, r in processes.transitions_of(before)}
    new_t = {(a, b): r for a, b, r in processes.transitions_of(after)}
    for (a, b), rework in new_t.items():
        kind = "Nacharbeit" if rework else "Übergang"
        if (a, b) not in old_t:
            lines.append({"sign": "added", "text": f"{kind} {a} → {b}"})
        elif old_t[(a, b)] != rework:
            lines.append({"sign": "changed", "text": f"{a} → {b} ist jetzt {'Nacharbeit' if rework else 'ein Übergang vorwärts'}"})
    for (a, b), rework in old_t.items():
        if (a, b) not in new_t:
            lines.append({"sign": "removed", "text": f"{'Nacharbeit' if rework else 'Übergang'} {a} → {b}"})
    old_map, new_map = before.get("status_map") or {}, after.get("status_map") or {}
    for t in sorted(set(old_map) | set(new_map)):
        o, n = old_map.get(t) or {}, new_map.get(t) or {}
        for value in [v for v in n if v not in o or o[v] != n[v]]:
            lines.append({"sign": "changed" if value in o else "added",
                          "text": f"Status {t} = {value} → {n[value] or '(nicht aus dem Wert)'}"})
        for value in [v for v in o if v not in n]:
            lines.append({"sign": "removed", "text": f"Status {t} = {value}"})
    for field, label in _PROCESS_FIELDS.items():
        if before.get(field) != after.get(field):
            lines.append({"sign": "changed", "text": f"{label} geändert"})
    return lines


def _ordered_lines(old: list, new: list, key: str, label: str, name) -> list[dict]:
    """Added, removed, changed and reordered elements of two lists keyed by ``key``."""
    lines = []
    o = {str(x.get(key)): x for x in old if isinstance(x, Mapping)}
    n = {str(x.get(key)): x for x in new if isinstance(x, Mapping)}
    for k, x in n.items():
        if k not in o:
            lines.append({"sign": "added", "text": f"{label} {name(x)}"})
        elif o[k] != x:
            lines.append({"sign": "changed", "text": f"{label} {name(x)} geändert"})
    for k, x in o.items():
        if k not in n:
            lines.append({"sign": "removed", "text": f"{label} {name(x)}"})
    if [k for k in o if k in n] != [k for k in n if k in o]:
        lines.append({"sign": "changed", "text": f"Reihenfolge: {label}"})
    return lines


def view_lines(section: str, before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict]:
    """The differences between two versions of a spine, page, profile or role mapping (step B9)."""
    if section == "spines":
        lines = _ordered_lines(before.get("levels") or [], after.get("levels") or [], "id", "Ebene",
                               lambda lv: lv.get("name") or lv.get("id"))
        lines += _ordered_lines(before.get("branches") or [], after.get("branches") or [], "key", "Zweig",
                                lambda b: b.get("name") or b.get("key"))
        fields = (("name", "Name"), ("description", "Beschreibung"))
    elif section == "pages":
        lines = _ordered_lines(before.get("sections") or [], after.get("sections") or [], "id", "Abschnitt",
                               lambda s: (s.get("title") or {}).get("de") or s.get("id"))
        fields = (("name", "Name"), ("type", "Typ"), ("filter", "Schlüsselanfang"))
    elif section == "profiles":
        before, after = before.get("definition") or {}, after.get("definition") or {}
        lines = _ordered_lines(before.get("views") or [], after.get("views") or [], "id", "Ansicht",
                               lambda v: v.get("label") or v.get("id"))
        old_pages, new_pages = before.get("pages") or [], after.get("pages") or []
        lines += [{"sign": "added", "text": f"Seite {p}"} for p in new_pages if p not in old_pages]
        lines += [{"sign": "removed", "text": f"Seite {p}"} for p in old_pages if p not in new_pages]
        fields = (("audience", "Zielgruppe"), ("description", "Beschreibung"), ("role", "Rolle"),
                  ("delivered_through", "Ausgeliefert über"), ("lens", "Blickfeld"), ("actions", "Aktionen"),
                  ("rights", "Rechte"))
    else:
        old, new = before.get("profiles") or [], after.get("profiles") or []
        return ([{"sign": "added", "text": f"sieht {p}"} for p in new if p not in old]
                + [{"sign": "removed", "text": f"sieht {p} nicht mehr"} for p in old if p not in new])
    return lines + [{"sign": "changed", "text": f"{label} geändert"}
                    for f, label in fields if before.get(f) != after.get(f)]


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
        if section in ("spines", "pages", "profiles", "profile_roles"):
            lines = view_lines(section, before or {}, after or {})
        else:
            lines = (process_lines if section == "processes" else field_lines)(before or {}, after or {})
        lines = lines or [{"sign": "changed", "text": "Kleine Änderungen"}]
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
