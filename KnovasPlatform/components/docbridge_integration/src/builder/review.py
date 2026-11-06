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
            "processes": "Prozesse", "processes.rules": "Prozessregeln", "processes.report_rules": "Berichtsregeln",
            "sources": "Quellen", "mappings": "Zuordnungen",
            "spines": "Hierarchien", "pages": "Seiten", "profiles": "Profile", "profile_roles": "Rollen und Profile",
            "access.groups": "Zugriffsgruppen", "access.rules": "Ordnerregeln"}
KINDS = {"ontology.types": "Typ", "ontology.relations": "Beziehung", "packs": "Paketstand",
         "processes": "Prozess", "processes.rules": "Prozessregel", "processes.report_rules": "Berichtsregel",
         "sources": "Quelle", "mappings": "Zuordnung",
         "spines": "Hierarchie", "pages": "Seite", "profiles": "Profil", "profile_roles": "Rolle",
         "access.groups": "Zugriffsgruppe", "access.rules": "Ordnerregel"}
#: Change rows that are not about one element: where the draft came from.
ORIGINS = {"import": "Paket importiert", "create": "Angelegt"}

_FIELDS = {"label": "Namen", "description": "Beschreibung", "example": "Beispiel",
           "not_to_confuse": "Nicht verwechseln mit", "parent": "Elterntyp", "domain": "Von", "range": "Nach",
           "family": "Familie", "inverse": "Umkehrung", "sentence": "Lesart", "iri": "IRI", "reason": "Begründung",
           "version": "Version", "name": "Name", "owner": "Verantwortlich", "component": "Komponente",
           "settings": "Einstellungen", "credential_set": "Zugangsdaten",
           # A source's switches and an access rule (BB-07, B1): named, not «Kleine Änderungen».
           "access": "Zugriff", "fetch": "Originale abrufen", "prefix": "Ordner", "groups": "Gruppen"}


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
        # A source: where it reads and through which component, so that a reviewer sees an address before a site
        # reads it (BB-07, B1/B2).
        settings = item.get("settings") if isinstance(item.get("settings"), Mapping) else {}
        where = ", ".join(f"{k}: {v}" for k, v in sorted(settings.items()))
        return (f"{item.get('name')} · {item.get('kind')}" + (f" · {where}" if where else "")
                + (f" · Komponente {item['component']}" if item.get("component") else ""))
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
        if field == "settings" and isinstance(new, Mapping) and isinstance(old, Mapping):
            # Each setting with its new value: an address a site's credential goes to is read, not guessed.
            for name in sorted(set(old) | set(new)):
                if old.get(name) != new.get(name):
                    lines.append({"sign": "changed" if name in old and name in new else ("added" if name in new else "removed"),
                                  "text": f"{label}: {name} {old.get(name) or '–'} → {new.get(name) or '–'}"})
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


# ------------------------------------------------------------------ the whole diff against the published state (BB-07, B1)
#
# change_set folds the draft's change log, and an import or a creation is one origin row with no content: a reviewer
# of an imported workspace saw "Paket importiert" and nothing else, while publish wrote everything the package
# brought -- the ontology, processes, mappings, access groups, folder rules and the role mapping. model_diff compares
# the draft with the newest published version (an empty model before the first publish) element by element, in every
# section and in every other part of the model, so the review shows all a publish hands on. Who brought an element is
# kept as before: the authors of its change rows, else the person whose import or creation the draft started from.

#: Every other part of the model, one element each: what an import brings beside the sections above.
PARTS = "parts"
SECTIONS[PARTS] = "Weitere Teile des Pakets"
KINDS[PARTS] = "Teil"
_LIST_SECTIONS = ("packs", "sources", "mappings", "profiles", "profile_roles")
_DOCUMENT_SECTIONS = ("spines", "pages")
_PROCESS_SECTIONS = {"processes": "processes", "processes.rules": "rules", "processes.report_rules": "report_rules"}


def _items(model: Optional[Mapping[str, Any]], section: str) -> list:
    """The section's elements, read without changing the model (builder.editing._items adds what is missing)."""
    model = model or {}
    if section in _LIST_SECTIONS:
        found = model.get(section)
    elif section in ("access.groups", "access.rules"):
        found = (model.get("access") or {}).get(section.split(".", 1)[1]) if isinstance(model.get("access"), Mapping) else None
    elif section in _DOCUMENT_SECTIONS:
        doc = model.get(section)
        found = doc.get(section) if isinstance(doc, Mapping) else None
    elif section in _PROCESS_SECTIONS:
        doc = model.get("processes")
        found = doc.get(_PROCESS_SECTIONS[section]) if isinstance(doc, Mapping) else None
    else:
        ontology = model.get("ontology")
        found = ontology.get(section.split(".", 1)[1]) if isinstance(ontology, Mapping) else None
    return [x for x in found or [] if isinstance(x, Mapping)]


def _keyed(model: Optional[Mapping[str, Any]], section: str) -> dict[str, Mapping[str, Any]]:
    from builder.editing import key_of

    return {str(key_of(section, item)): item for item in _items(model, section)}


def _residual(model: Optional[Mapping[str, Any]]) -> dict:
    """The model without the elements of the sections: its other parts (company, package files, ontology headers,
    owners, health, the documents' own fields, …). A part that is None counts as absent."""
    import copy

    rest = copy.deepcopy(dict(model or {}))
    for name in _LIST_SECTIONS:
        rest.pop(name, None)
    if isinstance(rest.get("ontology"), dict):
        for name in ("types", "relations"):
            rest["ontology"].pop(name, None)
    if isinstance(rest.get("processes"), dict):
        for name in _PROCESS_SECTIONS.values():
            rest["processes"].pop(name, None)
    for name in _DOCUMENT_SECTIONS:
        if isinstance(rest.get(name), dict):
            rest[name].pop(name, None)
    if isinstance(rest.get("access"), dict):
        for name in ("groups", "rules"):
            rest["access"].pop(name, None)
    return {k: v for k, v in rest.items() if v not in (None, {}, [])}


def _part_diff(name: str, before: Any, after: Any) -> Optional[dict]:
    if before == after:
        return None
    if before is None:
        state, lines = "added", [{"sign": "added", "text": _part_text(name, after)}]
    elif after is None:
        state, lines = "removed", [{"sign": "removed", "text": "Entfernt"}]
    else:
        state = "changed"
        if isinstance(before, Mapping) and isinstance(after, Mapping):
            lines = [{"sign": "added", "text": f"{k}"} for k in sorted(set(after) - set(before), key=str)]
            lines += [{"sign": "removed", "text": f"{k}"} for k in sorted(set(before) - set(after), key=str)]
            lines += [{"sign": "changed", "text": f"{k} geändert"} for k in sorted(set(before) & set(after), key=str)
                      if before[k] != after[k]]
        else:
            lines = [{"sign": "changed", "text": "Geändert"}]
    return {"section": PARTS, "kind": KINDS[PARTS], "key": name, "path": f"{PARTS}/{name}", "state": state,
            "lines": lines}


def _part_text(name: str, value: Any) -> str:
    if isinstance(value, Mapping):
        return f"{name}: " + ", ".join(sorted(str(k) for k in value)) if value else name
    return f"{name}: {value}" if isinstance(value, (str, int, float)) else name


def model_diff(base: Optional[Mapping[str, Any]], draft: Optional[Mapping[str, Any]],
               rows: Iterable[Mapping[str, Any]] = (), author: Optional[str] = None) -> dict:
    """Everything a publish of ``draft`` changes against ``base`` (the newest published model, None before the
    first publish), grouped by section as change_set groups it, with the same keys.

    ``rows``: the draft's change rows (store.draft_changes), oldest first, for who changed each element; an element
    with none came with the draft's origin (an import, a creation) and is that person's (``from_origin``).
    ``author``: keep only that person's elements."""
    rows = list(rows)
    origin, origin_actor = None, None
    folded: dict[str, dict] = {}
    authors: dict[str, str] = {}
    for row in rows:
        actor = str(row["actor"]) if row.get("actor") else ""
        if actor:
            authors[actor] = row.get("actor_name") or actor
        if row["path"] in ORIGINS:
            origin = {"label": ORIGINS[row["path"]], "at": row.get("at"), "actor_name": row.get("actor_name"),
                      "actor": actor or None, "kind": row["path"]}
            origin_actor = actor or None
            continue
        entry = folded.setdefault(row["path"], {"first_before": row["before"], "authors": {}, "count": 0,
                                                "last_at": row.get("at")})
        entry["count"] += 1
        entry["last_at"] = row.get("at")
        if actor:
            entry["authors"][actor] = row.get("actor_name") or actor
    groups: list[dict] = []
    from builder.editing import SECTIONS as EDIT_SECTIONS

    for section in EDIT_SECTIONS:
        old, new = _keyed(base, section), _keyed(draft, section)
        for key in list(new) + [k for k in old if k not in new]:
            before, after = old.get(key), new.get(key)
            if before == after:
                continue
            group = element_diff(f"{section}/{key}", before, after)
            if group["state"] == "unchanged":
                continue
            if group["state"] == "added" and (after or {}).get("retired"):
                group["lines"].append({"sign": "removed", "text": "Als stillgelegt markiert"})
            groups.append(group)
    old_rest, new_rest = _residual(base), _residual(draft)
    for name in sorted(set(old_rest) | set(new_rest)):
        group = _part_diff(name, old_rest.get(name), new_rest.get(name))
        if group is not None:
            groups.append(group)
    sections: dict[str, list] = {}
    for group in groups:
        entry = folded.get(group["path"])
        who = dict(entry["authors"]) if entry else {}
        # The origin brought the element when it already differed from the published state at the origin: with no
        # change row since, or as it was before its first edit. Its importer is then one of its authors.
        if origin is not None:
            at_origin = entry["first_before"] if entry else _element(draft, group["path"])
            if at_origin != _element(base, group["path"]) and origin_actor:
                who = {origin_actor: origin["actor_name"] or origin_actor, **who}
        group.update(authors=list(who.values()), author_ids=list(who), count=entry["count"] if entry else 0,
                     last_at=(entry or {}).get("last_at") or (origin or {}).get("at"),
                     from_origin=entry is None and origin is not None)
        if author and author not in who:
            continue
        sections.setdefault(group["section"], []).append(group)
    ordered = [{"section": s, "label": SECTIONS.get(s, s),
                "elements": sorted(sections[s], key=lambda g: str(g["key"]).lower())}
               for s in SECTIONS if s in sections]
    counts = {state: sum(1 for s in ordered for g in s["elements"] if g["state"] == state)
              for state in ("added", "changed", "removed", "restored")}
    return {"origin": origin, "sections": ordered, "counts": counts, "authors": authors,
            "total": sum(counts.values()), "against": "published" if base is not None else "empty"}


def _element(model: Optional[Mapping[str, Any]], path: str) -> Optional[Mapping[str, Any]]:
    section, _, key = path.partition("/")
    if section == PARTS:
        return _residual(model).get(key)
    return _keyed(model, section).get(key)


def diff_summary(diff: Mapping[str, Any]) -> list[dict]:
    """Per section, how many elements a publish adds, changes and removes: for the dry run's table."""
    out = []
    for s in diff["sections"]:
        counts = {state: sum(1 for g in s["elements"] if g["state"] == state)
                  for state in ("added", "changed", "removed", "restored")}
        out.append({"section": s["section"], "label": s["label"], **counts})
    return out


# ------------------------------------------------------------------ what publish writes beside the ontology (BB-07, B1)

def access_plan(design: Optional[Mapping[str, Any]], tree: Any, live_prefixes: Iterable[str]) -> dict:
    """What publish writes of the draft's access design onto the live tenant, without writing it: the groups it
    makes (by name, under their parent; one of that name already there is used, not made), then the folder rules
    whose prefix the tenant has no rule for. Never deletes or moves anything live. ``tree``: builder.access.Tree."""
    design = design or {}
    admin = next((k for k in tree.roots() if tree.is_admin(k)), None)
    planned: dict[str, str] = {}               # design key -> the group's name
    made: set[str] = set()                     # names this publish makes, lower case
    groups = []
    pending = [g for g in design.get("groups") or [] if isinstance(g, Mapping)]
    while pending:
        progress = False
        for gr in list(pending):
            parent = gr.get("parent")
            if parent and parent not in planned:
                continue
            name = str(gr.get("name") or "")
            exists = tree.by_name(name) is not None or name.strip().lower() in made
            if not exists:
                made.add(name.strip().lower())
            planned[str(gr.get("key"))] = name
            groups.append({"key": str(gr.get("key")), "name": name, "exists": exists,
                           "parent": planned[parent] if parent else tree.name(admin)})
            pending.remove(gr)
            progress = True
        if not progress:
            break
    have = {str(p) for p in live_prefixes}
    rules = []
    for r in design.get("rules") or []:
        if not isinstance(r, Mapping):
            continue
        rules.append({"prefix": str(r.get("prefix")), "exists": str(r.get("prefix")) in have,
                      "groups": [planned[g] for g in r.get("groups") or [] if g in planned],
                      "missing": [str(g) for g in r.get("groups") or [] if g not in planned]})
    return {"groups": groups, "rules": rules, "unplaced": [str(g.get("key")) for g in pending]}


def roles_plan(current: Optional[Mapping[str, Any]], mapping: Mapping[str, list]) -> dict:
    """What publish writes as the role mapping (views.profile_roles, replaced whole): per role, the profiles it saw
    and will see, for every role whose line changes."""
    current = {str(k): [str(p) for p in (v or [])] for k, v in (current or {}).items()} if isinstance(current, Mapping) else {}
    rows = []
    for role in sorted(set(current) | set(mapping)):
        before, after = current.get(role), mapping.get(role)
        if before != after:
            rows.append({"role": role, "before": before, "after": after})
    return {"rows": rows, "mapping": dict(mapping)}


def effect_rows(effects: Mapping[str, Any]) -> list[dict]:
    """The dry run's table rows for what publish writes beside the ontology: access groups and folder rules on the
    tenant, the role mapping on the Platform. Status as the ontology's rows: create, update, refused, not_configured."""
    rows = []
    access = effects.get("access") or {}
    if access.get("error"):
        rows.append({"part": "access", "element": "Zugriffsentwurf", "status": "not_configured",
                     "detail": f"Wird nicht geschrieben: {access['error']}"})
    for g in access.get("groups") or []:
        if not g["exists"]:
            rows.append({"part": "access", "element": f"Zugriffsgruppe {g['name']}", "status": "create",
                         "detail": f"Gruppe anlegen, unter {g['parent']}"})
    for key in access.get("unplaced") or []:
        rows.append({"part": "access", "element": f"Zugriffsgruppe {key}", "status": "refused",
                     "detail": "Ihre Elterngruppe fehlt im Entwurf: wird nicht angelegt"})
    for r in access.get("rules") or []:
        if not r["exists"]:
            rows.append({"part": "access", "element": f"Ordnerregel {r['prefix']}", "status": "create",
                         "detail": "Nur lesbar für " + (", ".join(r["groups"]) or "niemanden außer den Administratoren")
                                   + (f" (fehlt im Entwurf: {', '.join(r['missing'])})" if r["missing"] else "")})
    roles = effects.get("roles") or {}
    if roles.get("error"):
        rows.append({"part": "roles", "element": "Rollen und Profile", "status": "not_configured",
                     "detail": f"Wird nicht geschrieben: {roles['error']}"})
    for r in roles.get("rows") or []:
        detail = ("sieht " + ", ".join(r["after"]) if r["after"] else "sieht kein Profil mehr") if r["after"] is not None \
            else "fällt aus der Zuordnung"
        rows.append({"part": "roles", "element": f"Rolle {r['role']}",
                     "status": "create" if r["before"] is None else "update",
                     "detail": detail + (f" (bisher: {', '.join(r['before']) or 'keines'})" if r["before"] is not None else "")})
    return rows


def review_hash(*, base_hash: Optional[str], content_hash: str, plan_rows: list, effects: Mapping[str, Any]) -> str:
    """The digest of everything a publish changes: the published state it starts from, the draft (both together fix
    the model diff), the ontology plan against the tenant and what it writes beside it. An approval holds it; a
    publish whose fresh digest differs is not the approved one (BB-07, B1)."""
    import hashlib
    import json

    # The plan's rows in a fixed order: the same plan listed in another order is the same publish.
    rows = sorted((dict(r) for r in plan_rows), key=lambda r: json.dumps(r, sort_keys=True, ensure_ascii=False))
    body = {"base": base_hash, "draft": content_hash, "plan": rows,
            "access": effects.get("access"), "roles": effects.get("roles")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()
