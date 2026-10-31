"""Edits of a company's processes (build plan step B6).

Each operation changes one process -- written as one change of the whole
process, before and after, so undo, redo and the review work as for a type --
and, for a new exit criterion, the process rule it names. The process stays in
the package's own format (builder.processes): steps in order with their
``order``, transitions ``[from, to]`` / ``[from, to, rework]``, resting steps
both marked and listed under ``final``.

What an operation refuses is what would not be a process any more (a step id
twice, a transition to nowhere). Whether a process is right -- reachable steps,
a status map that covers the values, paths that fit the relations -- the
validation says while editing (builder.processes.validate).
"""
from __future__ import annotations

import copy
import re
from typing import Any, Mapping, Optional

from builder import processes as P
from builder.editing import (OPERATIONS, PROCESS_RULES, PROCESSES, EditError, _Edit, _names, _set_text, _text)

PROCESS_KEY = re.compile(r"^PROC-[a-z]+(-[a-z]+)*$")
STEP_ID = re.compile(r"^[a-z][a-z0-9-]*$")
RULE_ID = re.compile(r"^P-[a-z]+(-[a-z]+)*$")
ARTEFACT_KINDS = ("requires", "produces", "context")


# ------------------------------------------------------------------ lookups

def _process(edit: _Edit, key: Any) -> tuple[dict, dict]:
    old = edit.find(PROCESSES, key)
    if old is None:
        raise EditError(f"Den Prozess «{key}» gibt es nicht.", 404)
    return old, copy.deepcopy(old)


def _step_index(proc: dict, step_id: Any) -> int:
    for n, raw in enumerate(proc.get("steps") or []):
        if str(P.step_of(raw).get("id")) == step_id:
            return n
    raise EditError(f"Den Schritt «{step_id}» gibt es im Prozess nicht.", 404)


def _step(proc: dict, step_id: Any) -> dict:
    """The step as a definition (a plain name becomes one), in place."""
    n = _step_index(proc, step_id)
    proc["steps"][n] = dict(P.step_of(proc["steps"][n]))
    return proc["steps"][n]


def _renumber(proc: dict) -> None:
    """``order`` follows the position: the canvas reorders by moving steps in the list."""
    steps = []
    for n, raw in enumerate(proc.get("steps") or [], start=1):
        step = dict(P.step_of(raw))
        step["order"] = n
        steps.append(step)
    proc["steps"] = steps


def _new_step_id(proc: dict, value: Any) -> str:
    step_id = _text(value, "Schritt-ID", limit=60)
    if not STEP_ID.match(step_id):
        raise EditError("Schritt-ID: klein geschrieben, Wörter mit Bindestrich, z. B. in-pruefung.")
    if step_id in P.step_ids(proc):
        raise EditError(f"Den Schritt «{step_id}» gibt es im Prozess schon.")
    return step_id


def _set_final(proc: dict, step_id: str, resting: bool) -> None:
    final = [str(x) for x in proc.get("final") or []]
    if resting and step_id not in final:
        final.append(step_id)
    if not resting:
        final = [x for x in final if x != step_id]
    proc["final"] = final


def _status_map(proc: dict) -> dict:
    return {str(t): dict(m or {}) for t, m in (proc.get("status_map") or {}).items()}


# ------------------------------------------------------------------ inputs

def _artefact_input(raw: Any) -> dict:
    """An artefact as the package writes it, from the panel's input."""
    if not isinstance(raw, Mapping):
        raise EditError("Artefakt: Angaben erwartet.")
    path = _path_input(raw.get("path"), "Pfad")
    out: dict[str, Any] = {"type": path[-1]["to_type"]}
    if raw.get("required"):
        out["required"] = True
    count = raw.get("min_count", 1)
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 999:
        raise EditError("Mindestanzahl: eine ganze Zahl ab 1.")
    if count != 1:
        out["min_count"] = count
    out["path"] = path
    alt = raw.get("alternative")
    if alt:
        if not isinstance(alt, Mapping):
            raise EditError("Alternative: Angaben erwartet.")
        alt_path = _path_input(alt.get("path"), "Pfad der Alternative")
        out["alternative"] = {"type": alt_path[-1]["to_type"], "path": alt_path}
    if raw.get("verified") is False:
        out["verified"] = False
    where = raw.get("where")
    if where:
        if not isinstance(where, Mapping):
            raise EditError("Bedingung: Angaben erwartet.")
        fact, equals = _text(where.get("fact"), "Bedingung", limit=80), _text(where.get("equals"), "Wert", limit=200)
        if fact and equals:
            out["where"] = {"fact": fact, "equals": equals}
    for key, what in (("note", "Notiz"), ("label", "Bezeichnung")):
        _set_text(out, key, _text(raw.get(key), what, limit=400))
    return out


def _path_input(raw: Any, what: str) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise EditError(f"{what}: mindestens ein Schritt über eine Beziehung.")
    hops = []
    for hop in raw:
        if not isinstance(hop, Mapping):
            raise EditError(f"{what}: jeder Schritt braucht Beziehung, Richtung und Typ.")
        relation, to_type = _text(hop.get("relation"), "Beziehung", limit=80), _text(hop.get("to_type"), "Typ", limit=80)
        direction = hop.get("direction")
        if not relation or not to_type or direction not in P.DIRECTIONS:
            raise EditError(f"{what}: jeder Schritt braucht Beziehung, Richtung (out oder in) und Typ.")
        hops.append({"relation": relation, "direction": direction, "to_type": to_type})
    return hops


# ------------------------------------------------------------------ processes

def _process_create(edit: _Edit, op: Mapping[str, Any]) -> None:
    if "processes" not in ((edit.model.get("package") or {}).get("manifest") or {}):
        raise EditError("Dieses Paket hat keine Prozessdatei (processes in brain-config.yaml).", 409)
    key = _text(op.get("key"), "Schlüssel", limit=80)
    if not PROCESS_KEY.match(key):
        raise EditError("Schlüssel: PROC- und kleine Wörter mit Bindestrich, z. B. PROC-mandatsaufnahme.")
    if edit.find(PROCESSES, key) is not None:
        raise EditError(f"Den Prozess «{key}» gibt es schon.")
    name = _text(op.get("name"), "Name", limit=200) or key
    types = _names(op.get("instance_types"), "Instanztypen")
    process: dict[str, Any] = {"key": key, "name": name, "instance_types": types, "governing_fact": "status",
                               "status_map": {t: {} for t in types}, "owner_role": _text(op.get("owner_role"), "Rolle", limit=80),
                               "steps": [], "transitions": [], "final": []}
    _set_text(process, "description", _text(op.get("description"), "Beschreibung"))
    edit.write(PROCESSES, None, process)


_PROCESS_TEXTS = (("name", "Name", 200), ("description", "Beschreibung", 4000), ("owner_role", "Rolle", 80),
                  ("governing_fact", "Steuerndes Attribut", 80), ("value_stream", "Wertstrom", 80),
                  ("exists_today", "Heute", 400))


def _process_update(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _process(edit, op.get("key"))
    fields = op.get("fields") or {}
    if not isinstance(fields, Mapping):
        raise EditError("Felder: Angaben erwartet.")
    for key, what, limit in _PROCESS_TEXTS:
        if key in fields:
            value = _text(fields[key], what, limit=limit)
            if key in ("name", "owner_role", "governing_fact"):
                new[key] = value  # required by the schema: empty is a problem the check names
            else:
                _set_text(new, key, value)
    if "instance_types" in fields:
        types = _names(fields["instance_types"], "Instanztypen")
        new["instance_types"] = types
        status = _status_map(new)
        new["status_map"] = {t: status.get(t, {}) for t in types}
    edit.write(PROCESSES, old, new)


def _process_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, _ = _process(edit, op.get("key"))
    edit.write(PROCESSES, old, None)


def _status_set(edit: _Edit, op: Mapping[str, Any]) -> None:
    """One governing-fact value: which step it means (``step``), none told by it (``step: null``) or unmapped."""
    old, new = _process(edit, op.get("key"))
    type_key, value = op.get("type"), _text(op.get("value"), "Wert", limit=200)
    if type_key not in (new.get("instance_types") or []):
        raise EditError(f"{type_key} ist kein Instanztyp dieses Prozesses.")
    if not value:
        raise EditError("Wert: bitte angeben.")
    status = _status_map(new)
    entry = status.setdefault(type_key, {})
    if op.get("unmapped"):
        entry.pop(value, None)
    else:
        step = op.get("step")
        if step is not None and step not in P.step_ids(new):
            raise EditError(f"Den Schritt «{step}» gibt es im Prozess nicht.", 404)
        entry[value] = step
    new["status_map"] = status
    edit.write(PROCESSES, old, new)


# ------------------------------------------------------------------ steps

def _step_create(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _process(edit, op.get("key"))
    step_id = _new_step_id(new, op.get("id"))
    step: dict[str, Any] = {"id": step_id}
    _set_text(step, "name", _text(op.get("name"), "Name", limit=200))
    _set_text(step, "owner_role", _text(op.get("owner_role"), "Rolle", limit=80))
    steps = list(new.get("steps") or [])
    after = op.get("after")
    position = len(steps) if after in (None, "") else _step_index(new, after) + 1
    steps.insert(position, step)
    new["steps"] = steps
    _renumber(new)
    edit.write(PROCESSES, old, new)


def _step_update(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _process(edit, op.get("key"))
    step_id = op.get("step")
    step = _step(new, step_id)
    fields = op.get("fields") or {}
    if not isinstance(fields, Mapping):
        raise EditError("Felder: Angaben erwartet.")
    for key, what, limit in (("name", "Name", 200), ("description", "Beschreibung", 4000), ("owner_role", "Rolle", 80)):
        if key in fields:
            _set_text(step, key, _text(fields[key], what, limit=limit))
    if "resting" in fields:
        resting = bool(fields["resting"])
        if resting:
            step["resting"] = True
        else:
            step.pop("resting", None)
        _set_final(new, step_id, resting)
    if "exit_criteria" in fields:
        criteria = _names(fields["exit_criteria"], "Abschlusskriterien")
        if criteria:
            step["exit_criteria"] = criteria
        else:
            step.pop("exit_criteria", None)
    edit.write(PROCESSES, old, new)


def _step_rename(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _process(edit, op.get("key"))
    was = op.get("step")
    step = _step(new, was)
    now = _new_step_id(new, op.get("to"))
    step["id"] = now
    swap = (lambda s: now if str(s) == was else s)
    new["transitions"] = [P.transition_entry(swap(a), swap(b), r) for a, b, r in P.transitions_of(new)]
    new["final"] = [swap(x) for x in new.get("final") or []]
    new["status_map"] = {t: {v: swap(s) if s is not None else None for v, s in m.items()}
                         for t, m in _status_map(new).items()}
    cc = new.get("change_classes")
    if isinstance(cc, dict) and isinstance(cc.get("on_substantive"), dict):
        cc["on_substantive"] = {k: swap(v) for k, v in cc["on_substantive"].items()}
    edit.write(PROCESSES, old, new)


def _step_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    """The step, its transitions and its place in the final steps; status values that meant it become unmapped."""
    old, new = _process(edit, op.get("key"))
    step_id = op.get("step")
    del new["steps"][_step_index(new, step_id)]
    _renumber(new)
    new["transitions"] = [P.transition_entry(a, b, r) for a, b, r in P.transitions_of(new) if step_id not in (a, b)]
    _set_final(new, step_id, False)
    new["status_map"] = {t: {v: s for v, s in m.items() if s != step_id} for t, m in _status_map(new).items()}
    edit.write(PROCESSES, old, new)


def _step_move(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _process(edit, op.get("key"))
    steps = list(new.get("steps") or [])
    step = steps.pop(_step_index(new, op.get("step")))
    to = op.get("to")
    if not isinstance(to, int) or isinstance(to, bool) or not 0 <= to <= len(steps):
        raise EditError("Position: eine Stelle in der Reihe der Schritte.")
    steps.insert(to, step)
    new["steps"] = steps
    _renumber(new)
    edit.write(PROCESSES, old, new)


def _criterion_add(edit: _Edit, op: Mapping[str, Any]) -> None:
    """An exit criterion: a rule that exists (R0-R8, a process rule), or a new process rule written as text."""
    old, new = _process(edit, op.get("key"))
    step = _step(new, op.get("step"))
    rule = op.get("rule")
    if isinstance(rule, Mapping):
        rule_id = _new_rule(edit, rule)
    else:
        rule_id = _text(rule, "Kriterium", limit=80)
        known = rule_id in P.R_RULES or edit.find(PROCESS_RULES, rule_id) is not None
        if not known:
            raise EditError(f"Die Regel «{rule_id}» gibt es nicht.", 404)
    criteria = [str(c) for c in step.get("exit_criteria") or []]
    if rule_id in criteria:
        raise EditError(f"{rule_id} ist schon ein Abschlusskriterium dieses Schritts.")
    step["exit_criteria"] = criteria + [rule_id]
    edit.write(PROCESSES, old, new)


def _slug(text: str) -> str:
    folded = text.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    return "-".join(re.findall(r"[a-z]+", folded))[:40].strip("-")


def _new_rule(edit: _Edit, raw: Mapping[str, Any]) -> str:
    name = _text(raw.get("name"), "Kriterium", limit=200)
    if not name:
        raise EditError("Kriterium: bitte als Text angeben.")
    check = raw.get("check")
    if check not in P.CHECKS:
        raise EditError("Prüfung: eine der angebotenen Arten.")
    rule_id = _text(raw.get("id"), "Regel-ID", limit=60) or f"P-{_slug(name) or 'kriterium'}"
    if not RULE_ID.match(rule_id):
        raise EditError("Regel-ID: P- und kleine Wörter mit Bindestrich.")
    base, n = rule_id, 2
    while edit.find(PROCESS_RULES, rule_id) is not None:
        rule_id = f"{base}-{'abcdefghijklmnopqrstuvwxyz'[min(n, 27) - 2]}"
        n += 1
    rule: dict[str, Any] = {"id": rule_id, "name": name, "check": check}
    if check == "facts_present":
        facts = _names(raw.get("facts"), "Attribute")
        if not facts:
            raise EditError("Attribute: mindestens eines.")
        rule["facts"] = facts
    else:
        fact = _text(raw.get("fact"), "Attribut", limit=80)
        if not fact:
            raise EditError("Attribut: bitte wählen.")
        rule["fact"] = fact
    _set_text(rule, "description", _text(raw.get("description"), "Beschreibung"))
    edit.write(PROCESS_RULES, None, rule)
    return rule_id


# ------------------------------------------------------------------ transitions

def _transition_index(proc: dict, source: Any, target: Any) -> Optional[int]:
    return next((n for n, (a, b, _) in enumerate(P.transitions_of(proc)) if a == source and b == target), None)


def _transition_add(edit: _Edit, op: Mapping[str, Any]) -> None:
    """A transition; drawn back to an earlier step it is rework unless the operation says otherwise."""
    old, new = _process(edit, op.get("key"))
    source, target = op.get("from"), op.get("to")
    ids = P.step_ids(new)
    if source not in ids or target not in ids:
        raise EditError("Ein Übergang verbindet zwei Schritte des Prozesses.", 404)
    if source == target:
        raise EditError("Ein Übergang führt zu einem anderen Schritt.")
    if _transition_index(new, source, target) is not None:
        raise EditError(f"Den Übergang {source} → {target} gibt es schon.")
    rework = op.get("rework")
    if rework is None:
        rework = ids.index(target) < ids.index(source)
    new["transitions"] = [P.transition_entry(a, b, r) for a, b, r in P.transitions_of(new)] + [
        P.transition_entry(source, target, bool(rework))]
    edit.write(PROCESSES, old, new)


def _transition_update(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _process(edit, op.get("key"))
    index = _transition_index(new, op.get("from"), op.get("to"))
    if index is None:
        raise EditError("Diesen Übergang gibt es nicht.", 404)
    items = P.transitions_of(new)
    items[index] = (items[index][0], items[index][1], bool(op.get("rework")))
    new["transitions"] = [P.transition_entry(a, b, r) for a, b, r in items]
    edit.write(PROCESSES, old, new)


def _transition_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _process(edit, op.get("key"))
    index = _transition_index(new, op.get("from"), op.get("to"))
    if index is None:
        raise EditError("Diesen Übergang gibt es nicht.", 404)
    items = P.transitions_of(new)
    del items[index]
    new["transitions"] = [P.transition_entry(a, b, r) for a, b, r in items]
    edit.write(PROCESSES, old, new)


# ------------------------------------------------------------------ artefacts

def _artefact_set(edit: _Edit, op: Mapping[str, Any]) -> None:
    """Add (no ``index``), replace or remove (``artefact: null``) an artefact of a step or of the process's context."""
    old, new = _process(edit, op.get("key"))
    kind = op.get("kind")
    if kind not in ARTEFACT_KINDS:
        raise EditError("Art: braucht, liefert oder Kontext.")
    owner = new if kind == "context" else _step(new, op.get("step"))
    items = list(owner.get(kind) or [])
    index = op.get("index")
    raw = op.get("artefact")
    if index is not None and (not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(items)):
        raise EditError("Dieses Artefakt gibt es nicht.", 404)
    if raw is None:
        if index is None:
            raise EditError("Welches Artefakt?")
        del items[index]
    elif index is None:
        items.append(_artefact_input(raw))
    else:
        items[index] = _artefact_input(raw)
    if items:
        owner[kind] = items
    else:
        owner.pop(kind, None)
    edit.write(PROCESSES, old, new)


OPERATIONS.update({
    "process.create": _process_create,
    "process.update": _process_update,
    "process.remove": _process_remove,
    "status.set": _status_set,
    "step.create": _step_create,
    "step.update": _step_update,
    "step.rename": _step_rename,
    "step.remove": _step_remove,
    "step.move": _step_move,
    "criterion.add": _criterion_add,
    "transition.add": _transition_add,
    "transition.update": _transition_update,
    "transition.remove": _transition_remove,
    "artefact.set": _artefact_set,
})
