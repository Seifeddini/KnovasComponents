"""Edits to a workspace's ontology (build plan step B3).

An edit is an operation the canvas sends ({"op": "type.create", ...}). It is
checked here, against the model, and turns into changes: one per element of
the model it touched, each ``{path, before, after}`` with the whole element
before and after (``None`` where there was none). ``ontology.types/Invoice``
names the type ``Invoice``; ``ontology.relations/billed_to`` a relation.

A change is all it takes to undo or redo: ``put`` applies one, or its
reverse, and refuses when the element is no longer what the change expects
(someone else changed it since).

What an edit refuses is what would leave the model unreadable: an unknown
type, a key that is taken or malformed, a parent that does not exist or
closes a circle, a change to a core or pack term. What it lets through and
the problems list shows instead is what a consultant fixes over time: an enum
without values, a relation without a range, a missing description.

Retiring is not deleting: a retired type or relation stays in the model,
marked, until publishing decides (step B5). Nothing here knows a company.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Iterable, Mapping

from builder import ontology_view

TYPES = "ontology.types"
RELATIONS = "ontology.relations"
SECTIONS = (TYPES, RELATIONS)

#: The datatypes an attribute can be given in the builder.
DATATYPES = ("text", "date", "money", "enum", "entity_ref")

TYPE_KEY = re.compile(r"^[A-Z][A-Za-z0-9]*$")
SNAKE_KEY = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
MAX_TEXT = 4000

#: The parts of a model that name types outside the ontology. A type one of
#: them names keeps its key, and retiring it is a problem.
USING_PARTS = (("processes", "Prozesse"), ("spines", "Hierarchien"), ("pages", "Seiten"),
               ("owners", "Zuständigkeiten"), ("edit_sources", "Bearbeitungsquellen"),
               ("health", "Gesundheit"), ("mappings", "Zuordnung"), ("profiles", "Profil"))


class EditError(ValueError):
    """An edit the model cannot take; ``message`` is for the person who made it."""

    def __init__(self, message: str, status: int = 400, details: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = list(details or [])


# ------------------------------------------------------------------ changes


def _items(model: dict, section: str) -> list:
    if section not in SECTIONS:
        raise EditError("Unbekannter Teil des Modells.")
    return model.setdefault("ontology", {}).setdefault(section.split(".", 1)[1], [])


def put(model: dict, change: Mapping[str, Any], *, reverse: bool = False) -> None:
    """Apply ``change`` to ``model`` (or take it back, ``reverse``)."""
    section = change["path"].split("/", 1)[0]
    before, after = (change["after"], change["before"]) if reverse else (change["before"], change["after"])
    items = _items(model, section)
    if before is None:
        if any(item.get("key") == after["key"] for item in items):
            raise EditError(f"«{after['key']}» gibt es inzwischen schon.", 409)
        items.append(copy.deepcopy(after))
        return
    index = next((n for n, item in enumerate(items) if item.get("key") == before["key"]), None)
    if index is None or items[index] != before:
        raise EditError(f"«{before['key']}» wurde inzwischen geändert.", 409)
    if after is None:
        del items[index]
    else:
        items[index] = copy.deepcopy(after)


class _Edit:
    """One operation's view of the model: lookups, and writes that record a change."""

    def __init__(self, model: dict):
        self.model = model
        self.changes: list[dict] = []

    def find(self, section: str, key: Any) -> dict | None:
        return next((item for item in _items(self.model, section) if item.get("key") == key), None)

    def type(self, key: Any) -> dict:
        item = self.find(TYPES, key)
        if item is None:
            raise EditError(f"Den Typ «{key}» gibt es nicht.", 404)
        return item

    def own_type(self, key: Any) -> dict:
        item = self.type(key)
        if item.get("level") != "company":
            raise EditError(f"{key} gehört zum Kern oder zu einem Paket und ist schreibgeschützt.", 403)
        return item

    def relation(self, key: Any) -> dict:
        item = self.find(RELATIONS, key)
        if item is None:
            raise EditError(f"Die Beziehung «{key}» gibt es nicht.", 404)
        return item

    def write(self, section: str, old: dict | None, new: dict | None) -> None:
        if old == new:
            return
        named = old if old is not None else new
        assert named is not None
        change = {"path": f"{section}/{named['key']}", "before": copy.deepcopy(old), "after": copy.deepcopy(new)}
        put(self.model, change)
        self.changes.append(change)


# ------------------------------------------------------------------ inputs


def _text(value: Any, what: str, *, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise EditError(f"{what}: Text erwartet.")
    value = value.strip()
    if len(value) > limit:
        raise EditError(f"{what}: höchstens {limit} Zeichen.")
    return value


def _labels(value: Any) -> dict:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EditError("Name: Angaben für de und en erwartet.")
    out = {}
    for lang in ("de", "en"):
        text = _text(value.get(lang), f"Name ({lang})", limit=200)
        if text:
            out[lang] = text
    return out


def _set_text(item: dict, key: str, value: Any) -> None:
    """Set ``key`` to ``value``, or drop it when the value is empty."""
    if value:
        item[key] = value
    else:
        item.pop(key, None)


def _names(value: Any, what: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise EditError(f"{what}: eine Liste von Namen erwartet.")
    out: list[str] = []
    for v in value:
        if v.strip() not in out:
            out.append(v.strip())
    return out


def _type_key(edit: _Edit, value: Any, *, current: str | None = None) -> str:
    key = _text(value, "Schlüssel", limit=80)
    if not TYPE_KEY.match(key):
        raise EditError("Der Schlüssel eines Typs beginnt mit einem Großbuchstaben und hat nur Buchstaben "
                        "und Ziffern, etwa «Invoice» oder «LegalCase».")
    for item in _items(edit.model, TYPES):
        if item.get("key") != current and str(item.get("key", "")).lower() == key.lower():
            raise EditError(f"Den Typ «{item['key']}» gibt es schon.", 409)
    return key


# ------------------------------------------------------------------ usage


def _strings(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, Mapping):
        for k, v in node.items():
            if isinstance(k, str):
                yield k
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def usages(model: Mapping[str, Any], keys: Iterable[str]) -> dict[str, list[str]]:
    """Where processes, mappings and the other parts name each of ``keys``."""
    wanted = set(keys)
    found: dict[str, list[str]] = {}
    for part, label in USING_PARTS:
        value = model.get(part)
        if part in ("mappings", "profiles"):
            sources = [(f"{label} {item.get('file', '')}".strip(), item.get("definition")) for item in value or []]
        elif part == "processes" and isinstance(value, Mapping) and isinstance(value.get("processes"), list):
            sources = [(f"Prozess {p.get('key') or p.get('name') or p.get('id') or ''}".strip(), p)
                       for p in value["processes"] if isinstance(p, Mapping)]
            sources.append((label, {k: v for k, v in value.items() if k != "processes"}))
        else:
            sources = [(label, value)]
        for where, node in sources:
            hits = wanted.intersection(_strings(node))
            for key in hits:
                if where not in found.setdefault(key, []):
                    found[key].append(where)
    return found


# ------------------------------------------------------------------ parents


_HEADER_PREFIX = re.compile(r"^\s*\[[^\]]*\]\s*")


def _header(text: str) -> str:
    match = _HEADER_PREFIX.match(text or "")
    return match.group(0) if match else ""


def _with_header_parent(text: str, parent: str | None) -> str:
    """Rewrite ``parent=`` in a description header (DEC-0004), if it has one."""
    header = _header(text)
    if not header or "parent=" not in header:
        return text
    inner = header.strip()[1:-1]
    parts = [p for p in (s.strip() for s in inner.split(";")) if p]
    parts = [f"parent={parent or 'none'}" if p.startswith("parent=") else p for p in parts]
    return "[" + "; ".join(parts) + "] " + text[len(header):]


def _check_parents(model: dict, key: str) -> None:
    view = ontology_view.build(model)
    for problem in view["problems"]:
        if problem["kind"] in ("parent_unknown", "parent_cycle") and problem["target"] == f"type:{key}":
            raise EditError(problem["message"], 409)


# ------------------------------------------------------------------ operations


def _type_create(edit: _Edit, op: Mapping[str, Any]) -> None:
    key = _type_key(edit, op.get("key"))
    item: dict[str, Any] = {"key": key, "level": "company", "attributes": []}
    parent = _text(op.get("parent"), "Elterntyp", limit=200)
    if parent:
        item["parent"] = parent
    labels = _labels(op.get("label"))
    if labels:
        item["label"] = labels
    edit.write(TYPES, None, item)
    if parent:
        _check_parents(edit.model, key)


def _type_update(edit: _Edit, op: Mapping[str, Any]) -> None:
    old = edit.own_type(op.get("key"))
    new = copy.deepcopy(old)
    fields = op.get("set") or {}
    if not isinstance(fields, Mapping):
        raise EditError("Keine Änderung angegeben.")
    if "label" in fields:
        labels = _labels(fields["label"])
        _set_text(new, "label", labels or "")
    if "description" in fields:
        text = _text(fields["description"], "Beschreibung")
        description = dict(new.get("description") or {})
        header = _header(description.get("en", ""))
        if text or header:
            description["en"] = header + text if text else header.strip()
        else:
            description.pop("en", None)
        _set_text(new, "description", description or "")
    for name, label in (("example", "Beispiel"), ("not_to_confuse", "Nicht verwechseln mit"), ("reason", "Begründung")):
        if name in fields:
            _set_text(new, name, _text(fields[name], label))
    parent_changed = "parent" in fields
    if parent_changed:
        parent = _text(fields["parent"], "Elterntyp", limit=200) or None
        if parent == new["key"]:
            raise EditError("Ein Typ kann nicht sein eigener Elterntyp sein.", 409)
        new["parent"] = parent
        if (new.get("description") or {}).get("en"):
            new["description"]["en"] = _with_header_parent(new["description"]["en"], parent)
    edit.write(TYPES, old, new)
    if parent_changed:
        _check_parents(edit.model, new["key"])


def _type_rename(edit: _Edit, op: Mapping[str, Any]) -> None:
    old = edit.own_type(op.get("key"))
    key = old["key"]
    new_key = _type_key(edit, op.get("new_key"), current=key)
    if new_key == key:
        return
    used = usages(edit.model, [key]).get(key)
    if used:
        raise EditError(f"{key} wird verwendet und behält seinen Schlüssel, bis diese Stellen ihn mitändern "
                        "können.", 409, used)
    renamed = copy.deepcopy(old)
    renamed["key"] = new_key
    edit.write(TYPES, old, renamed)
    for item in list(_items(edit.model, TYPES)):
        new = copy.deepcopy(item)
        if new.get("parent") == key:
            new["parent"] = new_key
        en = (new.get("description") or {}).get("en")
        if en and ontology_view.header_fields(en).get("parent") == key:
            new["description"]["en"] = _with_header_parent(en, new_key)
        for attr in new.get("attributes") or []:
            if attr.get("target") == key:
                attr["target"] = new_key
        edit.write(TYPES, item, new)
    for item in list(_items(edit.model, RELATIONS)):
        new = copy.deepcopy(item)
        for side in ("domain", "range"):
            if key in (new.get(side) or []):
                new[side] = [new_key if v == key else v for v in new[side]]
        edit.write(RELATIONS, item, new)


def _retire(edit: _Edit, section: str, item: dict, retired: Any) -> None:
    new = copy.deepcopy(item)
    if retired is not False:
        new["retired"] = True
    else:
        new.pop("retired", None)
    edit.write(section, item, new)


def _type_retire(edit: _Edit, op: Mapping[str, Any]) -> None:
    _retire(edit, TYPES, edit.own_type(op.get("key")), op.get("retired", True))


def _attribute_input(raw: Any, base: Mapping[str, Any] | None = None) -> dict:
    if not isinstance(raw, Mapping):
        raise EditError("Attribut: Angaben erwartet.")
    attr = copy.deepcopy(dict(base or {"level": "company"}))
    if "name" in raw or base is None:
        name = _text(raw.get("name"), "Attributname", limit=80)
        if not SNAKE_KEY.match(name):
            raise EditError("Ein Attributname ist klein geschrieben, Wörter mit Unterstrich getrennt, "
                            "etwa «due_date».")
        attr["name"] = name
    if "datatype" in raw or base is None:
        datatype = _text(raw.get("datatype") or "text", "Datentyp", limit=40)
        if datatype not in DATATYPES:
            raise EditError(f"Datentyp «{datatype}» gibt es nicht.")
        attr["datatype"] = datatype
    if "required" in raw:
        if raw["required"]:
            attr["required"] = True
        else:
            attr.pop("required", None)
    if "enum_values" in raw:
        attr["enum_values"] = _names(raw["enum_values"], "Werte")
    if "target" in raw:
        _set_text(attr, "target", _text(raw["target"], "Zieltyp", limit=200))
    if "label" in raw:
        _set_text(attr, "label", _labels(raw["label"]) or "")
    if "description" in raw:
        text = _text(raw["description"], "Beschreibung")
        description = dict(attr.get("description") or {})
        _set_text(description, "en", text)
        _set_text(attr, "description", description or "")
    # What only one datatype uses goes with it.
    if attr.get("datatype") != "enum":
        attr.pop("enum_values", None)
        attr.pop("enum_labels", None)
    elif "enum_values" not in attr:
        attr["enum_values"] = []
    if attr.get("datatype") != "entity_ref":
        attr.pop("target", None)
    return attr


def _attribute_index(item: dict, name: Any) -> int:
    for n, attr in enumerate(item.get("attributes") or []):
        if attr.get("name") == name:
            return n
    raise EditError(f"{item['key']} hat kein Attribut «{name}».", 404)


def _unique_attribute(item: dict, name: str, skip: int | None = None) -> None:
    for n, attr in enumerate(item.get("attributes") or []):
        if n != skip and str(attr.get("name", "")).lower() == name.lower():
            raise EditError(f"{item['key']} hat schon ein Attribut «{attr['name']}».", 409)


def _attribute_add(edit: _Edit, op: Mapping[str, Any]) -> None:
    old = edit.own_type(op.get("key"))
    attr = _attribute_input(op.get("attribute"))
    _unique_attribute(old, attr["name"])
    new = copy.deepcopy(old)
    new.setdefault("attributes", []).append(attr)
    edit.write(TYPES, old, new)


def _attribute_update(edit: _Edit, op: Mapping[str, Any]) -> None:
    old = edit.own_type(op.get("key"))
    index = _attribute_index(old, op.get("name"))
    attr = _attribute_input(op.get("set") or {}, old["attributes"][index])
    _unique_attribute(old, attr["name"], skip=index)
    new = copy.deepcopy(old)
    new["attributes"][index] = attr
    edit.write(TYPES, old, new)


def _attribute_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    old = edit.own_type(op.get("key"))
    index = _attribute_index(old, op.get("name"))
    new = copy.deepcopy(old)
    del new["attributes"][index]
    edit.write(TYPES, old, new)


def _attribute_move(edit: _Edit, op: Mapping[str, Any]) -> None:
    old = edit.own_type(op.get("key"))
    index = _attribute_index(old, op.get("name"))
    to = op.get("to")
    if not isinstance(to, int) or isinstance(to, bool) or not 0 <= to < len(old["attributes"]):
        raise EditError("Ungültige Position.")
    new = copy.deepcopy(old)
    new["attributes"].insert(to, new["attributes"].pop(index))
    edit.write(TYPES, old, new)


_RELATION_TEXTS = (("family", "Familie", 80), ("inverse", "Umkehrung", 80), ("reason", "Begründung", MAX_TEXT),
                   ("sentence", "Lesart", MAX_TEXT))


def _relation_fields(edit: _Edit, item: dict, fields: Mapping[str, Any], *, own: bool) -> None:
    for side, label in (("domain", "Von"), ("range", "Nach")):
        if side in fields:
            names = _names(fields[side], label)
            unknown = [n for n in names if n != ontology_view.WILDCARD and edit.find(TYPES, n) is None]
            if unknown:
                raise EditError(f"{label}: unbekannte Typen.", 400, unknown)
            item[side] = names
    rest = [name for name in ("label", *(t[0] for t in _RELATION_TEXTS)) if name in fields]
    if rest and not own:
        raise EditError(f"{item['key']} gehört zum Kern; nur Von und Nach lassen sich erweitern.", 403)
    if "label" in fields:
        _set_text(item, "label", _labels(fields["label"]) or "")
    for name, label, limit in _RELATION_TEXTS:
        if name in fields:
            value = _text(fields[name], label, limit=limit)
            if name == "inverse" and value and not SNAKE_KEY.match(value):
                raise EditError("Die Umkehrung ist ein Beziehungsname wie «contains».")
            _set_text(item, name, value)


def _relation_create(edit: _Edit, op: Mapping[str, Any]) -> None:
    key = _text(op.get("key"), "Beziehungsname", limit=80)
    if not SNAKE_KEY.match(key):
        raise EditError("Ein Beziehungsname ist klein geschrieben, Wörter mit Unterstrich getrennt, "
                        "etwa «billed_to».")
    if edit.find(RELATIONS, key) is not None:
        raise EditError(f"Die Beziehung «{key}» gibt es schon; erweitern Sie dort Von und Nach.", 409)
    item: dict[str, Any] = {"key": key, "level": "company", "domain": [], "range": []}
    _relation_fields(edit, item, op, own=True)
    edit.write(RELATIONS, None, item)


def _relation_update(edit: _Edit, op: Mapping[str, Any]) -> None:
    old = edit.relation(op.get("key"))
    fields = op.get("set") or {}
    if not isinstance(fields, Mapping):
        raise EditError("Keine Änderung angegeben.")
    new = copy.deepcopy(old)
    _relation_fields(edit, new, fields, own=old.get("level") == "company")
    edit.write(RELATIONS, old, new)


def _relation_retire(edit: _Edit, op: Mapping[str, Any]) -> None:
    item = edit.relation(op.get("key"))
    if item.get("level") != "company":
        raise EditError(f"{item['key']} gehört zum Kern und ist schreibgeschützt.", 403)
    _retire(edit, RELATIONS, item, op.get("retired", True))


OPERATIONS: dict[str, Callable[[_Edit, Mapping[str, Any]], None]] = {
    "type.create": _type_create,
    "type.update": _type_update,
    "type.rename": _type_rename,
    "type.retire": _type_retire,
    "attribute.add": _attribute_add,
    "attribute.update": _attribute_update,
    "attribute.remove": _attribute_remove,
    "attribute.move": _attribute_move,
    "relation.create": _relation_create,
    "relation.update": _relation_update,
    "relation.retire": _relation_retire,
}


def apply(model: dict, op: Any) -> list[dict]:
    """Apply one operation to ``model`` in place; the changes it made."""
    if not isinstance(op, Mapping) or op.get("op") not in OPERATIONS:
        raise EditError("Unbekannte Änderung.")
    edit = _Edit(model)
    OPERATIONS[op["op"]](edit, op)
    return edit.changes


def undo(model: dict, changes: list[Mapping[str, Any]]) -> list[dict]:
    """Take ``changes`` back, last first; the changes that does."""
    taken = []
    for change in reversed(changes):
        put(model, change, reverse=True)
        taken.append({"path": change["path"], "before": copy.deepcopy(change["after"]),
                      "after": copy.deepcopy(change["before"])})
    return taken


def redo(model: dict, changes: list[Mapping[str, Any]]) -> list[dict]:
    """Make ``changes`` again, in their order; the changes that does."""
    for change in changes:
        put(model, change)
    return [copy.deepcopy(dict(c)) for c in changes]
