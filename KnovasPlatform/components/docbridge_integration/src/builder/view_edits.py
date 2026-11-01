"""Edits of how a company sees its Brain: spines, pages, profiles and roles (build plan step B9).

Each operation changes one element -- a whole spine, page, profile or role
mapping, before and after -- so undo, redo and the review work as for a type.
The elements stay in the package's own format (builder.views): a spine's
levels in order, each with its types and its link to the level above; a page's
sections in order; a profile's views in order.

What an operation refuses is what would not be a definition any more (an id
twice, a section without content, a key the schema does not take). Whether the
definitions are right -- types and relations that exist, links and paths that
fit, a page for every type people read -- the validation says while editing
(builder.views.validate).
"""
from __future__ import annotations

import copy
import posixpath
import re
from typing import Any, Mapping, Optional

from builder import views as V
from builder.editing import (OPERATIONS, PAGES, PROFILE_ROLES, PROFILES, SPINES, EditError, _Edit, _names, _set_text,
                             _text)

SPINE_KEY = re.compile(r"^SPINE-[a-z-]+$")
PAGE_KEY = re.compile(r"^PAGE-[a-z0-9-]+$")
PROFILE_ID = re.compile(r"^[a-z][a-z0-9-]*$")
SECTION_ID = re.compile(r"^[a-z][a-z0-9_]*$")
ROLE_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
BRANCH_KEY = re.compile(r"^[a-z][a-z0-9-]*$")
DIRECTIONS = ("out", "in")


def _position(value: Any, length: int, what: str = "Position") -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < length:
        raise EditError(f"Ungültige {what}.")
    return value


def _insert_at(value: Any, length: int) -> int:
    if value is None:
        return length
    return _position(value, length + 1)


def _title(value: Any, what: str) -> dict:
    """{de, en}: both, the English one taken from the German when it is missing."""
    if not isinstance(value, Mapping):
        raise EditError(f"{what}: Angaben für de und en erwartet.")
    de = _text(value.get("de"), f"{what} (de)", limit=200)
    en = _text(value.get("en"), f"{what} (en)", limit=200) or de
    if not de:
        raise EditError(f"{what}: der deutsche Name fehlt.")
    return {"de": de, "en": en}


def _hops(value: Any, what: str) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise EditError(f"{what}: mindestens ein Schritt.")
    out = []
    for hop in value:
        if not isinstance(hop, Mapping):
            raise EditError(f"{what}: jeder Schritt hat Beziehung, Richtung und Zieltyp.")
        relation = _text(hop.get("relation"), "Beziehung", limit=80)
        direction = _text(hop.get("direction"), "Richtung", limit=4)
        to_type = _text(hop.get("to_type"), "Zieltyp", limit=200)
        if not relation or direction not in DIRECTIONS or not to_type:
            raise EditError(f"{what}: jeder Schritt hat Beziehung, Richtung (out oder in) und Zieltyp.")
        out.append({"relation": relation, "direction": direction, "to_type": to_type})
    return out


def _where(value: Any) -> Optional[dict]:
    if not value:
        return None
    if not isinstance(value, Mapping) or not value.get("fact"):
        raise EditError("Bedingung: ein Attribut und ein Wert.")
    where = {"fact": _text(value["fact"], "Bedingung", limit=80)}
    if "equals" in value:
        where["equals"] = _text(value["equals"], "Wert", limit=200)
    elif "in" in value:
        where["in"] = _names(value["in"], "Werte")
    elif "not_in" in value:
        where["not_in"] = _names(value["not_in"], "Werte")
    else:
        raise EditError("Bedingung: gleich, einer von oder keiner von.")
    return where


def _labelled_paths(value: Any, what: str) -> list[dict]:
    if not isinstance(value, list):
        raise EditError(f"{what}: eine Liste von Pfaden erwartet.")
    out = []
    for n, raw in enumerate(value, start=1):
        if not isinstance(raw, Mapping):
            raise EditError(f"{what}: Pfad {n} ist keine Angabe.")
        entry: dict[str, Any] = {}
        label = _text(raw.get("label"), "Beschriftung", limit=120)
        if label:
            entry["label"] = label
        entry["path"] = _hops(raw.get("path"), f"{what}, Pfad {n}")
        where = _where(raw.get("where"))
        if where:
            entry["where"] = where
        out.append(entry)
    return out


# ------------------------------------------------------------------ spines

def _spine(edit: _Edit, key: Any) -> tuple[dict, dict]:
    old = edit.find(SPINES, key)
    if old is None:
        raise EditError(f"Die Hierarchie «{key}» gibt es nicht.", 404)
    return old, copy.deepcopy(old)


def _chain(spine: dict, branch: Any) -> list:
    """The levels the operation works on: the spine's own, or a branch's."""
    if not branch:
        return spine.setdefault("levels", [])
    for b in spine.get("branches") or []:
        if b.get("key") == branch:
            return b.setdefault("levels", [])
    raise EditError(f"Den Zweig «{branch}» gibt es nicht.", 404)


def _level_index(levels: list, level_id: Any) -> int:
    for n, lv in enumerate(levels):
        if lv.get("id") == level_id:
            return n
    raise EditError(f"Die Ebene «{level_id}» gibt es nicht.", 404)


def _types(edit: _Edit, value: Any) -> list[str]:
    names = _names(value, "Typen")
    unknown = [n for n in names if edit.find("ontology.types", n) is None]
    if unknown:
        raise EditError("Unbekannte Typen.", 400, unknown)
    return names


def _link(value: Any) -> Optional[dict]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise EditError("Verbindung: Beziehung und Richtung.")
    relation = _text(value.get("relation"), "Beziehung", limit=80)
    direction = value.get("direction")
    if not relation:
        return None
    if direction not in DIRECTIONS:
        raise EditError("Verbindung: Richtung out (zum Elternknoten) oder in (vom Elternknoten).")
    return {"relation": relation, "direction": direction}


def _new_level_id(spine: dict, branch: Any) -> str:
    prefix = "B" if branch else "L"
    taken = {str(lv.get("id")) for _, levels in V.chains(spine) for lv in levels}
    n = 1
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def _spine_create(edit: _Edit, op: Mapping[str, Any]) -> None:
    key = _text(op.get("key"), "Schlüssel", limit=80)
    if not SPINE_KEY.match(key):
        raise EditError("Der Schlüssel einer Hierarchie beginnt mit SPINE- und hat Kleinbuchstaben und Bindestriche, "
                        "etwa SPINE-organisation.")
    if edit.find(SPINES, key) is not None:
        raise EditError(f"Die Hierarchie «{key}» gibt es schon.", 409)
    name = _text(op.get("name"), "Name", limit=200) or key
    item: dict[str, Any] = {"key": key, "name": name, "levels": []}
    _set_text(item, "description", _text(op.get("description"), "Beschreibung"))
    edit.write(SPINES, None, item)


def _spine_update(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _spine(edit, op.get("key"))
    fields = op.get("set") or {}
    if "name" in fields:
        new["name"] = _text(fields["name"], "Name", limit=200) or new["key"]
    if "description" in fields:
        _set_text(new, "description", _text(fields["description"], "Beschreibung"))
    edit.write(SPINES, old, new)


def _spine_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, _ = _spine(edit, op.get("key"))
    edit.write(SPINES, old, None)


def _branch_create(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _spine(edit, op.get("spine"))
    key = _text(op.get("key"), "Schlüssel des Zweigs", limit=60)
    if not BRANCH_KEY.match(key):
        raise EditError("Der Schlüssel eines Zweigs ist klein geschrieben, etwa sales.")
    if any(b.get("key") == key for b in new.get("branches") or []):
        raise EditError(f"Den Zweig «{key}» gibt es schon.", 409)
    branch: dict[str, Any] = {"key": key, "name": _text(op.get("name"), "Name", limit=200) or key, "levels": []}
    _set_text(branch, "description", _text(op.get("description"), "Beschreibung"))
    new.setdefault("branches", []).append(branch)
    edit.write(SPINES, old, new)


def _branch_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _spine(edit, op.get("spine"))
    branches = [b for b in new.get("branches") or [] if b.get("key") != op.get("branch")]
    if len(branches) == len(new.get("branches") or []):
        raise EditError(f"Den Zweig «{op.get('branch')}» gibt es nicht.", 404)
    if branches:
        new["branches"] = branches
    else:
        new.pop("branches", None)
    edit.write(SPINES, old, new)


def _level_fields(edit: _Edit, level: dict, fields: Mapping[str, Any]) -> None:
    if "name" in fields:
        name = _text(fields["name"], "Name der Ebene", limit=200)
        if not name:
            raise EditError("Eine Ebene braucht einen Namen.")
        level["name"] = name
    if "types" in fields:
        level["types"] = _types(edit, fields["types"])
        for t in list(level.get("links_to_parent") or {}):
            if t not in level["types"]:
                del level["links_to_parent"][t]
        if "links_to_parent" in level and not level["links_to_parent"]:
            del level["links_to_parent"]
    if "link" in fields:
        link = _link(fields["link"])
        level.pop("links_to_parent", None)
        if link:
            level["link_to_parent"] = link
        else:
            level.pop("link_to_parent", None)
    if "links" in fields:
        raw = fields["links"] or {}
        if not isinstance(raw, Mapping):
            raise EditError("Verbindungen: je Typ Beziehung und Richtung.")
        links = {str(t): _link(v) for t, v in raw.items()}
        links = {t: v for t, v in links.items() if v}
        level.pop("link_to_parent", None)
        level.pop("links_to_parent", None)
        distinct = {(v["relation"], v["direction"]) for v in links.values()}
        if len(distinct) == 1 and set(links) == set(level.get("types") or []):
            level["link_to_parent"] = next(iter(links.values()))    # one link for all: the short form
        elif links:
            level["links_to_parent"] = links
    if "key_prefix" in fields:
        prefix = _text(fields["key_prefix"], "Schlüsselanfang", limit=40)
        if prefix:
            level["filter"] = {"key_prefix": prefix}
        else:
            level.pop("filter", None)
    if "verified" in fields:
        if fields["verified"] is False:
            level["verified"] = False
        else:
            level.pop("verified", None)


def _level_add(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _spine(edit, op.get("spine"))
    levels = _chain(new, op.get("branch"))
    raw = op.get("level") or {}
    level: dict[str, Any] = {"id": _new_level_id(new, op.get("branch")), "name": "", "types": []}
    _level_fields(edit, level, {"name": raw.get("name") or "Neue Ebene", "types": raw.get("types") or [],
                                **({"link": raw["link"]} if raw.get("link") else {})})
    levels.insert(_insert_at(op.get("at"), len(levels)), level)
    edit.write(SPINES, old, new)


def _level_update(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _spine(edit, op.get("spine"))
    levels = _chain(new, op.get("branch"))
    level = levels[_level_index(levels, op.get("level"))]
    fields = op.get("set") or {}
    if not isinstance(fields, Mapping):
        raise EditError("Keine Änderung angegeben.")
    _level_fields(edit, level, fields)
    edit.write(SPINES, old, new)


def _level_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _spine(edit, op.get("spine"))
    levels = _chain(new, op.get("branch"))
    del levels[_level_index(levels, op.get("level"))]
    edit.write(SPINES, old, new)


def _level_move(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _spine(edit, op.get("spine"))
    levels = _chain(new, op.get("branch"))
    index = _level_index(levels, op.get("level"))
    to = _position(op.get("to"), len(levels))
    levels.insert(to, levels.pop(index))
    edit.write(SPINES, old, new)


# ------------------------------------------------------------------ pages

def _page(edit: _Edit, key: Any) -> tuple[dict, dict]:
    old = edit.find(PAGES, key)
    if old is None:
        raise EditError(f"Die Seite «{key}» gibt es nicht.", 404)
    return old, copy.deepcopy(old)


def _slug(text: str) -> str:
    out = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", text)
    return re.sub(r"[^a-z0-9]+", "-", out.lower()).strip("-") or "seite"


def _section_index(page: dict, section_id: Any) -> int:
    for n, s in enumerate(page.get("sections") or []):
        if s.get("id") == section_id:
            return n
    raise EditError(f"Den Abschnitt «{section_id}» gibt es nicht.", 404)


def _section(raw: Mapping[str, Any], base: Optional[Mapping[str, Any]] = None) -> dict:
    """A section from the editor's fields; what the editor does not show (status, stale) stays as it was."""
    s = copy.deepcopy(dict(base or {}))
    if "id" in raw or base is None:
        sid = _text(raw.get("id"), "Kennung", limit=60)
        if not SECTION_ID.match(sid):
            raise EditError("Die Kennung eines Abschnitts ist klein geschrieben, Wörter mit Unterstrich, etwa owners.")
        s["id"] = sid
    if "title" in raw or base is None:
        s["title"] = _title(raw.get("title") or {}, "Titel")
    if "render" in raw or base is None:
        render = raw.get("render") or "list"
        if render not in V.SECTION_RENDERS:
            raise EditError(f"Darstellung «{render}» gibt es nicht.")
        s["render"] = render
    if "required" in raw or base is None:
        s["required"] = bool(raw.get("required"))
    if "content" in raw or base is None:
        content = raw.get("content") or {}
        if not isinstance(content, Mapping):
            raise EditError("Inhalt: Feld, Attribute oder Pfade.")
        for k in ("field", "facts", "paths"):
            s.pop(k, None)
        if content.get("field"):
            if content["field"] not in V.SECTION_FIELDS:
                raise EditError("Feld: description oder name.")
            s["field"] = content["field"]
        elif content.get("facts"):
            s["facts"] = _names(content["facts"], "Attribute")
        elif content.get("paths"):
            s["paths"] = _labelled_paths(content["paths"], "Pfade")
        else:
            raise EditError("Was füllt den Abschnitt? Ein Feld, Attribute oder mindestens ein Pfad.")
        if "paths" not in s:
            for k in ("columns", "status", "order_by", "limit"):
                s.pop(k, None)
    if "columns" in raw:
        columns = _names(raw["columns"], "Spalten")
        if columns:
            s["columns"] = columns
        else:
            s.pop("columns", None)
    if "order_by" in raw:
        order = raw["order_by"]
        if order and isinstance(order, Mapping) and order.get("fact"):
            direction = order.get("direction") or "asc"
            if direction not in ("asc", "desc"):
                raise EditError("Sortierung: aufsteigend oder absteigend.")
            s["order_by"] = {"fact": _text(order["fact"], "Sortieren nach", limit=80), "direction": direction}
        else:
            s.pop("order_by", None)
    if "limit" in raw:
        limit = raw["limit"]
        if limit in (None, "", 0):
            s.pop("limit", None)
        elif isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= 1000:
            s["limit"] = limit
        else:
            raise EditError("Höchstens: eine Zahl von 1 bis 1000.")
    return s


def _page_create(edit: _Edit, op: Mapping[str, Any]) -> None:
    type_key = _text(op.get("type"), "Typ", limit=200)
    t = edit.find("ontology.types", type_key)
    if t is None:
        raise EditError(f"Den Typ «{type_key}» gibt es nicht.", 404)
    key = _text(op.get("key"), "Schlüssel", limit=80) or f"PAGE-{_slug(type_key)}"
    if not PAGE_KEY.match(key):
        raise EditError("Der Schlüssel einer Seite beginnt mit PAGE- und hat Kleinbuchstaben, Ziffern und Bindestriche.")
    if edit.find(PAGES, key) is not None:
        raise EditError(f"Die Seite «{key}» gibt es schon.", 409)
    label = t.get("label") or {}
    name = _title(op.get("name") or {"de": label.get("de") or type_key, "en": label.get("en") or type_key}, "Name")
    item: dict[str, Any] = {"key": key, "type": type_key, "name": name,
                            "sections": [{"id": "description", "title": {"de": "Beschreibung", "en": "Description"},
                                          "render": "text", "required": True, "field": "description"}]}
    prefix = _text(op.get("key_prefix"), "Schlüsselanfang", limit=40)
    if prefix:
        item["filter"] = {"key_prefix": prefix}
    edit.write(PAGES, None, item)


def _page_update(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _page(edit, op.get("key"))
    fields = op.get("set") or {}
    if "name" in fields:
        new["name"] = _title(fields["name"], "Name")
    if "type" in fields:
        type_key = _text(fields["type"], "Typ", limit=200)
        if edit.find("ontology.types", type_key) is None:
            raise EditError(f"Den Typ «{type_key}» gibt es nicht.", 404)
        new["type"] = type_key
    if "key_prefix" in fields:
        prefix = _text(fields["key_prefix"], "Schlüsselanfang", limit=40)
        if prefix:
            new["filter"] = {"key_prefix": prefix}
        else:
            new.pop("filter", None)
    edit.write(PAGES, old, new)


def _page_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, _ = _page(edit, op.get("key"))
    edit.write(PAGES, old, None)
    # A profile that lists the page lists it no more.
    for entry in list(edit.model.get("profiles") or []):
        pages = (entry.get("definition") or {}).get("pages") or []
        if old["key"] in pages:
            new = copy.deepcopy(entry)
            new["definition"]["pages"] = [p for p in pages if p != old["key"]]
            edit.write(PROFILES, entry, new)


def _slot_add(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _page(edit, op.get("page"))
    section = _section(op.get("slot") or {})
    sections = new.setdefault("sections", [])
    if any(s.get("id") == section["id"] for s in sections):
        raise EditError(f"Den Abschnitt «{section['id']}» gibt es schon.", 409)
    sections.insert(_insert_at(op.get("at"), len(sections)), section)
    edit.write(PAGES, old, new)


def _slot_update(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _page(edit, op.get("page"))
    index = _section_index(new, op.get("slot"))
    fields = op.get("set") or {}
    if not isinstance(fields, Mapping):
        raise EditError("Keine Änderung angegeben.")
    section = _section(fields, new["sections"][index])
    if any(n != index and s.get("id") == section["id"] for n, s in enumerate(new["sections"])):
        raise EditError(f"Den Abschnitt «{section['id']}» gibt es schon.", 409)
    new["sections"][index] = section
    edit.write(PAGES, old, new)


def _slot_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _page(edit, op.get("page"))
    del new["sections"][_section_index(new, op.get("slot"))]
    edit.write(PAGES, old, new)


def _slot_move(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _page(edit, op.get("page"))
    index = _section_index(new, op.get("slot"))
    to = _position(op.get("to"), len(new["sections"]))
    new["sections"].insert(to, new["sections"].pop(index))
    edit.write(PAGES, old, new)


# ------------------------------------------------------------------ profiles

def _profile(edit: _Edit, profile_id: Any) -> tuple[dict, dict]:
    old = edit.find(PROFILES, profile_id)
    if old is None:
        raise EditError(f"Das Profil «{profile_id}» gibt es nicht.", 404)
    return old, copy.deepcopy(old)


def _profile_folder(edit: _Edit) -> str:
    pattern = ((edit.model.get("package") or {}).get("manifest") or {}).get("profiles")
    if not isinstance(pattern, str):
        raise EditError("Das Paket dieses Arbeitsbereichs hat keinen Ordner für Profile.", 409)
    return posixpath.dirname(pattern) or "."


def _profile_create(edit: _Edit, op: Mapping[str, Any]) -> None:
    pid = _text(op.get("id"), "Kennung", limit=60)
    if not PROFILE_ID.match(pid):
        raise EditError("Die Kennung eines Profils ist klein geschrieben, Wörter mit Bindestrich, etwa partner.")
    if edit.find(PROFILES, pid) is not None:
        raise EditError(f"Das Profil «{pid}» gibt es schon.", 409)
    audience = _text(op.get("audience"), "Zielgruppe", limit=200)
    if not audience:
        raise EditError("Für wen ist das Profil? Die Zielgruppe fehlt.")
    folder = _profile_folder(edit)
    path = f"{pid}.yaml" if folder == "." else f"{folder}/{pid}.yaml"
    if any(e.get("file") == path for e in edit.model.get("profiles") or []):
        raise EditError(f"Die Datei {path} gibt es schon.", 409)
    definition = {"id": pid, "audience": audience, "delivered_through": "Platform",
                  "lens": {"spines": [], "types": [], "relations": []}, "views": [], "pages": [], "actions": [],
                  "context_packs": [], "rights": {"read": ["*"], "write": "none"}}
    edit.write(PROFILES, None, {"file": path, "definition": definition})


_PROFILE_TEXTS = (("audience", "Zielgruppe", 200), ("description", "Beschreibung", 4000),
                  ("delivered_through", "Ausgeliefert über", 200))


def _profile_update(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _profile(edit, op.get("id"))
    d = new["definition"]
    fields = op.get("set") or {}
    if not isinstance(fields, Mapping):
        raise EditError("Keine Änderung angegeben.")
    for name, label, limit in _PROFILE_TEXTS:
        if name in fields:
            value = _text(fields[name], label, limit=limit)
            if name == "audience" and not value:
                raise EditError("Die Zielgruppe darf nicht leer sein.")
            _set_text(d, name, value)
    if "role" in fields:
        role = _text(fields["role"], "Rolle", limit=80)
        if role and not re.match(r"^ROLE-[A-Z0-9-]+$", role):
            raise EditError("Die Rolle eines Profils ist ein Schlüssel wie ROLE-CTO.")
        _set_text(d, "role", role)
    lens = d.setdefault("lens", {"spines": [], "types": [], "relations": []})
    for name in ("spines", "types", "relations"):
        if f"lens_{name}" in fields:
            lens[name] = _names(fields[f"lens_{name}"], "Blickfeld")
    if "pages" in fields:
        d["pages"] = _names(fields["pages"], "Seiten")
    edit.write(PROFILES, old, new)


def _profile_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, _ = _profile(edit, op.get("id"))
    edit.write(PROFILES, old, None)
    pid = old["definition"].get("id")
    for entry in list(edit.model.get(PROFILE_ROLES) or []):
        if pid in (entry.get("profiles") or []):
            names = [p for p in entry["profiles"] if p != pid]
            edit.write(PROFILE_ROLES, entry, {"key": entry["key"], "profiles": names} if names else None)


def _view_index(d: dict, view_id: Any) -> int:
    for n, v in enumerate(d.get("views") or []):
        if v.get("id") == view_id:
            return n
    raise EditError(f"Die Ansicht «{view_id}» gibt es nicht.", 404)


_DEFINITION_LISTS = ("root_types", "columns", "color_by", "rules")


def _view(raw: Mapping[str, Any], base: Optional[Mapping[str, Any]] = None) -> dict:
    v = copy.deepcopy(dict(base or {}))
    if "id" in raw or base is None:
        vid = _text(raw.get("id"), "Kennung", limit=60)
        if not PROFILE_ID.match(vid):
            raise EditError("Die Kennung einer Ansicht ist klein geschrieben, Wörter mit Bindestrich, etwa akten-liste.")
        v["id"] = vid
    if "label" in raw or base is None:
        _set_text(v, "label", _text(raw.get("label"), "Beschriftung", limit=120))
    if "render" in raw or base is None:
        render = raw.get("render") or "list"
        if render not in V.RENDERS:
            raise EditError(f"Darstellung «{render}» gibt es nicht.")
        v["render"] = render
    d = v.setdefault("definition", {})
    fields = raw.get("definition") or {}
    if not isinstance(fields, Mapping):
        raise EditError("Definition: Angaben erwartet.")
    if "description" in fields:
        _set_text(d, "description", _text(fields["description"], "Beschreibung"))
    for name in _DEFINITION_LISTS:
        if name in fields:
            values = _names(fields[name], name)
            if values:
                d[name] = values
            else:
                d.pop(name, None)
    if "paths" in fields:
        paths = _labelled_paths(fields["paths"] or [], "Pfade")
        if paths:
            d["paths"] = paths
        else:
            d.pop("paths", None)
    for name, pattern, label in (("process", r"^PROC-[a-z0-9-]+$", "Prozess"), ("spine", r"^SPINE-[a-z0-9-]+$", "Hierarchie")):
        if name in fields:
            value = _text(fields[name], label, limit=80)
            if value and not re.match(pattern, value):
                raise EditError(f"{label}: «{value}» ist kein Schlüssel.")
            _set_text(d, name, value)
    if "group_by" in fields:
        _set_text(d, "group_by", _text(fields["group_by"], "Gruppieren nach", limit=200))
    return v


def _view_add(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _profile(edit, op.get("profile"))
    d = new["definition"]
    view = _view(op.get("view") or {})
    views = d.setdefault("views", [])
    if any(v.get("id") == view["id"] for v in views):
        raise EditError(f"Die Ansicht «{view['id']}» gibt es schon.", 409)
    views.insert(_insert_at(op.get("at"), len(views)), view)
    edit.write(PROFILES, old, new)


def _view_update(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _profile(edit, op.get("profile"))
    d = new["definition"]
    index = _view_index(d, op.get("view"))
    fields = op.get("set") or {}
    if not isinstance(fields, Mapping):
        raise EditError("Keine Änderung angegeben.")
    view = _view(fields, d["views"][index])
    if any(n != index and v.get("id") == view["id"] for n, v in enumerate(d["views"])):
        raise EditError(f"Die Ansicht «{view['id']}» gibt es schon.", 409)
    d["views"][index] = view
    edit.write(PROFILES, old, new)


def _view_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _profile(edit, op.get("profile"))
    d = new["definition"]
    del d["views"][_view_index(d, op.get("view"))]
    edit.write(PROFILES, old, new)


def _view_move(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _profile(edit, op.get("profile"))
    d = new["definition"]
    index = _view_index(d, op.get("view"))
    to = _position(op.get("to"), len(d["views"]))
    d["views"].insert(to, d["views"].pop(index))
    edit.write(PROFILES, old, new)


# ------------------------------------------------------------------ roles

def _roles_set(edit: _Edit, op: Mapping[str, Any]) -> None:
    """Which profiles a Platform role sees (``views.profile_roles``); none removes the role's entry."""
    role = _text(op.get("role"), "Rolle", limit=80)
    if not ROLE_KEY.match(role):
        raise EditError("Unbekannte Rolle.")
    names = _names(op.get("profiles"), "Profile")
    unknown = [n for n in names if edit.find(PROFILES, n) is None]
    if unknown:
        raise EditError("Unbekannte Profile.", 400, unknown)
    old = edit.find(PROFILE_ROLES, role)
    edit.write(PROFILE_ROLES, old, {"key": role, "profiles": names} if names else None)


OPERATIONS.update({
    "spine.create": _spine_create,
    "spine.update": _spine_update,
    "spine.remove": _spine_remove,
    "branch.create": _branch_create,
    "branch.remove": _branch_remove,
    "level.add": _level_add,
    "level.update": _level_update,
    "level.remove": _level_remove,
    "level.move": _level_move,
    "page.create": _page_create,
    "page.update": _page_update,
    "page.remove": _page_remove,
    "slot.add": _slot_add,
    "slot.update": _slot_update,
    "slot.remove": _slot_remove,
    "slot.move": _slot_move,
    "profile.create": _profile_create,
    "profile.update": _profile_update,
    "profile.remove": _profile_remove,
    "view.add": _view_add,
    "view.update": _view_update,
    "view.remove": _view_remove,
    "view.move": _view_move,
    "roles.set": _roles_set,
})
