"""Edits of a workspace's mappings (build plan step B8).

Each operation changes one mapping -- one change of the whole entry, before
and after, so undo, redo and the review work as everywhere else. The
definition stays brain-runner's mapping; ``builder`` beside it records the
workspace source it reads, its collection, the default fact priority and the
steps an expression was built from (``"<emit>:<target>"`` -> field and steps).

Targets of an emit: ``key``, ``name``, ``description``, ``fact:<attribute>``
and ``relation:<index>`` (the field whose value names the other node).
"""
from __future__ import annotations

import copy
import posixpath
from typing import Any, Mapping

from builder import mappings as M
from builder.editing import MAPPINGS, OPERATIONS, SOURCES, EditError, _Edit, _names, _text, ensure_part

DEFAULT_PRIORITY = 10


def _mapping(edit: _Edit, mapping_id: Any) -> tuple[dict, dict]:
    old = edit.find(MAPPINGS, mapping_id)
    if old is None:
        raise EditError(f"Die Zuordnung «{mapping_id}» gibt es nicht.", 404)
    new = copy.deepcopy(old)
    new.setdefault("builder", {})
    return old, new


def _emit(entry: dict, index: Any) -> dict:
    emits = entry["definition"].get("emits") or []
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(emits):
        raise EditError("Diesen Zieltyp gibt es in der Zuordnung nicht.", 404)
    return emits[index]


def _var(entry: dict) -> str:
    return str((entry["definition"].get("source") or {}).get("as") or M.VAR)


def _compiled(entry: dict, op: Mapping[str, Any]) -> tuple[str, dict]:
    field = _text(op.get("field"), "Feld", limit=300)
    steps = op.get("steps") or []
    if not isinstance(steps, list) or not all(isinstance(s, Mapping) for s in steps):
        raise EditError("Schritte: eine Liste erwartet.")
    try:
        expr = M.compile_steps(field, steps, _var(entry))
    except M.MappingError as exc:
        raise EditError(str(exc)) from None
    return expr, {"field": field, "steps": [dict(s) for s in steps]}


def _set_target(entry: dict, emit_index: int, target: str, expr: str | None, *, priority: int | None = None) -> None:
    em = _emit(entry, emit_index)
    if target in ("key", "name"):
        em[target] = expr or ""
    elif target == "description":
        if expr:
            em["description"] = expr
        else:
            em.pop("description", None)
    elif target.startswith("fact:"):
        attr = target[5:]
        facts = em.setdefault("facts", {})
        if expr:
            spec = dict(facts.get(attr) or {})
            spec["value"] = expr
            spec.setdefault("priority", priority if priority is not None else DEFAULT_PRIORITY)
            facts[attr] = spec
        else:
            facts.pop(attr, None)
    elif target.startswith("relation:"):
        rels = em.get("relations") or []
        index = int(target.split(":", 1)[1]) if target.split(":", 1)[1].isdigit() else -1
        if not 0 <= index < len(rels):
            raise EditError("Diese Beziehung gibt es in der Zuordnung nicht.", 404)
        rels[index]["to_key"] = expr or ""
    else:
        raise EditError("Unbekanntes Ziel.")


def _steps(entry: dict) -> dict:
    return entry["builder"].setdefault("steps", {})


# ------------------------------------------------------------------ mappings

def _mapping_create(edit: _Edit, op: Mapping[str, Any]) -> None:
    ensure_part(edit.model, "mappings")
    manifest = (edit.model.get("package") or {}).get("manifest") or {}
    pattern = manifest.get("mappings")
    if not isinstance(pattern, str):
        raise EditError("Dieses Paket hat keinen Ort für Zuordnungen (mappings in brain-config.yaml).", 409)
    mapping_id = _text(op.get("id"), "Kennung", limit=60)
    if not M.MAPPING_ID.match(mapping_id):
        raise EditError("Kennung: klein geschrieben, Wörter mit Bindestrich, z. B. ablage-mandate.")
    if edit.find(MAPPINGS, mapping_id) is not None:
        raise EditError(f"Die Zuordnung «{mapping_id}» gibt es schon.")
    source = edit.find(SOURCES, op.get("source"))
    if source is None:
        raise EditError("Quelle: eine Quelle dieses Arbeitsbereichs.", 404)
    type_key = _text(op.get("type"), "Zieltyp", limit=80)
    if not M.Ontology(edit.model).live_type(type_key):
        raise EditError(f"Den Typ «{type_key}» gibt es in der Ontologie nicht.", 404)
    collection = _text(op.get("collection"), "Sammlung", limit=400)
    try:
        spec = M.source_spec(source, collection)
    except M.MappingError as exc:
        raise EditError(str(exc)) from None
    folder = posixpath.dirname(pattern) or "."
    entry = {"file": posixpath.normpath(f"{folder}/{mapping_id}.yaml"),
             "definition": M.new_definition(mapping_id, spec, type_key, _text(op.get("description"), "Beschreibung")),
             "builder": {"source": source["key"], "collection": collection, "priority": DEFAULT_PRIORITY, "steps": {}}}
    edit.write(MAPPINGS, None, entry)


def _mapping_source(edit: _Edit, op: Mapping[str, Any]) -> None:
    """Read a mapping (an imported one too) through a workspace source, for the preview and the dry run."""
    old, new = _mapping(edit, op.get("id"))
    if op.get("source") in (None, ""):
        new["builder"].pop("source", None)
    elif edit.find(SOURCES, op.get("source")) is None:
        raise EditError("Quelle: eine Quelle dieses Arbeitsbereichs.", 404)
    else:
        new["builder"]["source"] = op["source"]
    edit.write(MAPPINGS, old, new)


def _mapping_settings(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _mapping(edit, op.get("id"))
    fields = op.get("fields") or {}
    if not isinstance(fields, Mapping):
        raise EditError("Felder: Angaben erwartet.")
    defn = new["definition"]
    if "description" in fields:
        defn["description"] = _text(fields["description"], "Beschreibung")
    if "edit_policy" in fields:
        if fields["edit_policy"] not in M.EDIT_POLICIES:
            raise EditError("Bearbeitung: eine der angebotenen Regeln.")
        defn["edit_policy"] = fields["edit_policy"]
    guard = dict(defn.get("deletion_guard") or {})
    if "max_retire_share" in fields:
        share = fields["max_retire_share"]
        if not isinstance(share, (int, float)) or isinstance(share, bool) or not 0 < share <= 1:
            raise EditError("Löschschutz: ein Anteil über 0 und höchstens 1.")
        guard["max_retire_share"] = round(float(share), 3)
    if "min_retire" in fields:
        least = fields["min_retire"]
        if not isinstance(least, int) or isinstance(least, bool) or least < 1:
            raise EditError("Löschschutz: mindestens 1 Eintrag.")
        guard["min_retire"] = least
    defn["deletion_guard"] = guard
    if "freshness" in fields:
        window = _text(fields["freshness"], "Frische", limit=20)
        if not window or not window.split(" ")[0].isdigit():
            raise EditError("Frische: eine Dauer wie «7 d».")
        defn["freshness"] = {"window": window}
    for key, what in (("select", "Auswahl"), ("as", "Name des Eintrags")):
        if key in fields:
            value = _text(fields[key], what, limit=400)
            if not value:
                raise EditError(f"{what}: bitte angeben.")
            defn["source"][key] = value
    if "priority" in fields:
        priority = fields["priority"]
        if not isinstance(priority, int) or isinstance(priority, bool) or not 0 <= priority <= 100:
            raise EditError("Priorität: eine ganze Zahl von 0 bis 100.")
        new["builder"]["priority"] = priority
        for em in defn.get("emits") or []:
            for spec in (em.get("facts") or {}).values():
                spec["priority"] = priority
    edit.write(MAPPINGS, old, new)


def _mapping_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, _ = _mapping(edit, op.get("id"))
    edit.write(MAPPINGS, old, None)


# ------------------------------------------------------------------ emits

def _emit_add(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _mapping(edit, op.get("id"))
    type_key = _text(op.get("type"), "Zieltyp", limit=80)
    if not M.Ontology(edit.model).live_type(type_key):
        raise EditError(f"Den Typ «{type_key}» gibt es in der Ontologie nicht.", 404)
    new["definition"]["emits"].append({"type": type_key, "identify": ["key"], "key": "", "name": "",
                                       "facts": {}, "relations": []})
    edit.write(MAPPINGS, old, new)


def _emit_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _mapping(edit, op.get("id"))
    index = op.get("emit")
    _emit(new, index)
    if len(new["definition"]["emits"]) == 1:
        raise EditError("Eine Zuordnung braucht mindestens einen Zieltyp.")
    del new["definition"]["emits"][index]
    steps = _steps(new)
    moved = {}
    for k, v in steps.items():
        i, _, rest = k.partition(":")
        if int(i) == index:
            continue
        moved[f"{int(i) - 1 if int(i) > index else int(i)}:{rest}"] = v
    new["builder"]["steps"] = moved
    edit.write(MAPPINGS, old, new)


def _identify(edit: _Edit, op: Mapping[str, Any]) -> None:
    """The identify rule: which attributes tell an existing node apart, in order of trust."""
    old, new = _mapping(edit, op.get("id"))
    em = _emit(new, op.get("emit"))
    attrs = _names(op.get("attributes"), "Attribute")
    if not attrs:
        raise EditError("Erkennen: mindestens ein Attribut.")
    em["identify"] = attrs
    edit.write(MAPPINGS, old, new)


def _bind(edit: _Edit, op: Mapping[str, Any]) -> None:
    """A field, after its steps, into a target: key, name, description or an attribute."""
    old, new = _mapping(edit, op.get("id"))
    target = _text(op.get("target"), "Ziel", limit=200)
    expr, steps = _compiled(new, op)
    _set_target(new, op.get("emit"), target, expr, priority=new["builder"].get("priority"))
    _steps(new)[f"{op.get('emit')}:{target}"] = steps
    edit.write(MAPPINGS, old, new)


def _expression(edit: _Edit, op: Mapping[str, Any]) -> None:
    """An expression written by hand (the expert view): it replaces the steps of its target."""
    old, new = _mapping(edit, op.get("id"))
    target = _text(op.get("target"), "Ziel", limit=200)
    expr = _text(op.get("expression"), "Ausdruck", limit=2000)
    why = M.expression_problem(expr) if expr else None
    if why:
        raise EditError(why)
    _set_target(new, op.get("emit"), target, expr, priority=new["builder"].get("priority"))
    _steps(new).pop(f"{op.get('emit')}:{target}", None)
    edit.write(MAPPINGS, old, new)


def _unbind(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _mapping(edit, op.get("id"))
    target = _text(op.get("target"), "Ziel", limit=200)
    _set_target(new, op.get("emit"), target, None)
    _steps(new).pop(f"{op.get('emit')}:{target}", None)
    edit.write(MAPPINGS, old, new)


def _relation(edit: _Edit, op: Mapping[str, Any]) -> None:
    """Add a relation built from a field whose value names the other node (looked up by its key), or remove one."""
    old, new = _mapping(edit, op.get("id"))
    emit_index = op.get("emit")
    em = _emit(new, emit_index)
    rels = em.setdefault("relations", [])
    if op.get("remove"):
        index = op.get("index")
        if not isinstance(index, int) or not 0 <= index < len(rels):
            raise EditError("Diese Beziehung gibt es in der Zuordnung nicht.", 404)
        del rels[index]
        steps = _steps(new)
        moved = {}
        for k, v in steps.items():
            e, _, target = k.partition(":")
            if int(e) == emit_index and target.startswith("relation:"):
                j = int(target.split(":")[1])
                if j == index:
                    continue
                k = f"{e}:relation:{j - 1 if j > index else j}"
            moved[k] = v
        new["builder"]["steps"] = moved
        edit.write(MAPPINGS, old, new)
        return
    relation = _text(op.get("relation"), "Beziehung", limit=80)
    to_type = _text(op.get("to_type"), "Zieltyp", limit=80)
    if not relation or not to_type:
        raise EditError("Beziehung: Name und Typ des anderen Knotens angeben.")
    expr, steps = _compiled(new, op)
    rel = {"relation": relation, "to_type": to_type, "to_key": expr, "origin": "canonical"}
    if op.get("direction") == "in":
        rel["direction"] = "in"
    if op.get("required"):
        rel["required"] = True
    rels.append(rel)
    _steps(new)[f"{emit_index}:relation:{len(rels) - 1}"] = steps
    edit.write(MAPPINGS, old, new)


# ------------------------------------------------------------------ access and writing back (connection layer MVP-26)

def _check_access(raw: Any, model: Mapping[str, Any]) -> dict:
    """``{fixed: all}``, ``{fixed: [group keys of the access design]}`` or ``{inherit: folder_rule}``."""
    if not isinstance(raw, Mapping) or len(raw) != 1:
        raise EditError("Zugriff: «Wie die Ordnerregel», «Alle im Mandanten» oder Gruppen.")
    if "from_source" in raw:
        raise EditError("Zugriff «aus der Quelle» kommt mit der Berechtigungsübernahme.")
    if raw.get("inherit") == "folder_rule":
        return {"inherit": "folder_rule"}
    fixed = raw.get("fixed")
    if fixed == "all":
        return {"fixed": "all"}
    if isinstance(fixed, list):
        known = {g.get("key") for g in (model.get("access") or {}).get("groups") or []}
        keys = list(dict.fromkeys(str(k) for k in fixed))
        if not keys:
            raise EditError("Zugriff: mindestens eine Gruppe.")
        unknown = [k for k in keys if k not in known]
        if unknown:
            raise EditError("Zugriff: diese Gruppen gibt es im Zugriffsentwurf nicht: " + ", ".join(unknown) + ".")
        return {"fixed": keys}
    raise EditError("Zugriff: «Wie die Ordnerregel», «Alle im Mandanten» oder Gruppen.")


def _mapping_access(edit: _Edit, op: Mapping[str, Any]) -> None:
    """«Zugriff»: who reads the documents the mapping uploads; ``None`` takes the choice back (no choice is preset)."""
    old, new = _mapping(edit, op.get("id"))
    if op.get("access") is None:
        new["definition"].pop("access", None)
    else:
        new["definition"]["access"] = _check_access(op.get("access"), edit.model)
    edit.write(MAPPINGS, old, new)


def _refused_write(fact: str, code: str) -> EditError:
    from builder import codes

    found = codes.describe(code)
    return EditError(f"«{fact}» lässt sich nicht zurückschreiben: {found['title']}. {found['explanation']}")


def _mapping_writes(edit: _Edit, op: Mapping[str, Any]) -> None:
    """«Zurückschreiben»: a fact accepted changes may be written back to, at the plain path its expression reads.
    Switching one on needs the source's write switch (MVP-25); switching off always works."""
    from builder.sources import write_of

    old, new = _mapping(edit, op.get("id"))
    defn = new["definition"]
    type_key = _text(op.get("type"), "Typ", limit=80)
    fact = _text(op.get("fact"), "Attribut", limit=80)
    if not isinstance(op.get("on"), bool):
        raise EditError("Zurückschreiben: an oder aus.")
    writes = copy.deepcopy(defn.get("writes") or {})
    if op["on"]:
        source = edit.find(SOURCES, new["builder"].get("source"))
        if source is None or not write_of(source):
            raise EditError("Zurückschreiben ist für die Quelle dieser Zuordnung ausgeschaltet: erst an der Quelle "
                            "«Änderungen dürfen zurückgeschrieben werden» einschalten.", 409)
        if M.is_computed(M.Ontology(edit.model).attribute(type_key, fact)):
            raise _refused_write(fact, "computed_attribute")
        verdict = M.invertible(defn, edit.model).get(type_key, {}).get(fact)
        if verdict is None:
            raise _refused_write(fact, "not_writable")
        if not verdict[0]:
            raise _refused_write(fact, verdict[1] or "not_invertible")
        spec: dict = {"field": M.write_field(defn, type_key, fact)}
        if op.get("allowed") is not None:
            allowed = op["allowed"]
            if not isinstance(allowed, list) or not 1 <= len(allowed) <= 50 or \
                    not all(isinstance(v, str) and 0 < len(v) <= 200 for v in allowed):
                raise EditError("Erlaubte Werte: eine Liste von 1 bis 50 Werten.")
            spec["allowed"] = list(dict.fromkeys(allowed))
        writes.setdefault(type_key, {})[fact] = spec
    else:
        (writes.get(type_key) or {}).pop(fact, None)
        writes = {k: v for k, v in writes.items() if v}
    if writes:
        defn["writes"] = writes
    else:
        defn.pop("writes", None)
    edit.write(MAPPINGS, old, new)


def keep_writes(model: Mapping[str, Any], entry: dict) -> str | None:
    """After an edit of the mapping: a written fact that is no longer invertible leaves ``writes``, and one whose
    plain path moved follows it -- in the same change. The sentence that says so, or None."""
    defn = entry.get("definition") or {}
    writes = defn.get("writes") or {}
    if not writes:
        return None
    verdicts = M.invertible(defn, model)
    removed, moved, kept = [], [], {}
    for type_key, facts in writes.items():
        for fact, spec in (facts or {}).items():
            verdict = verdicts.get(type_key, {}).get(fact)
            if not verdict or not verdict[0]:
                removed.append(fact)
                continue
            field = M.write_field(defn, type_key, fact)
            if field != (spec or {}).get("field"):
                moved.append(fact)
                spec = {**(spec or {}), "field": field}
            kept.setdefault(type_key, {})[fact] = spec
    if not removed and not moved:
        return None
    if kept:
        defn["writes"] = kept
    else:
        defn.pop("writes", None)
    said = []
    if removed:
        said.append("Aus «Zurückschreiben» entfernt, weil nicht mehr umkehrbar: " + ", ".join(removed) + ".")
    if moved:
        said.append("«Zurückschreiben» folgt dem neuen Feld: " + ", ".join(moved) + ".")
    return " ".join(said)


class _KeepingWrites:
    """An edit of a mapping whose ``writes`` stay what can be written back (keep_writes), in the same change."""

    def __init__(self, edit: _Edit):
        self._edit = edit

    def __getattr__(self, name):
        return getattr(self._edit, name)

    def write(self, section: str, old, new) -> None:
        notice = keep_writes(self._edit.model, new) if section == MAPPINGS and new is not None else None
        before = len(self._edit.changes)
        self._edit.write(section, old, new)
        if notice and len(self._edit.changes) > before:
            self._edit.changes[-1]["notice"] = notice


def _keeping_writes(operation):
    def run(edit: _Edit, op: Mapping[str, Any]) -> None:
        operation(_KeepingWrites(edit), op)
    run.__name__ = operation.__name__
    return run


OPERATIONS.update({
    "mapping.create": _mapping_create,
    "mapping.source": _mapping_source,
    "mapping.settings": _mapping_settings,
    "mapping.remove": _mapping_remove,
    "mapping.emit.add": _emit_add,
    "mapping.emit.remove": _emit_remove,
    "mapping.identify": _identify,
    "mapping.bind": _bind,
    "mapping.expression": _expression,
    "mapping.unbind": _unbind,
    "mapping.relation": _relation,
    "mapping.access": _mapping_access,
    "mapping.writes": _mapping_writes,
})
# Every other edit of a mapping keeps its writes invertible (MVP-26).
for _name in ("mapping.source", "mapping.settings", "mapping.emit.add", "mapping.emit.remove", "mapping.identify",
              "mapping.bind", "mapping.expression", "mapping.unbind", "mapping.relation"):
    OPERATIONS[_name] = _keeping_writes(OPERATIONS[_name])
