"""The access design in a workspace draft (build plan step B14): the initial group tree and folder rules.

A consultant designs them in the draft like any other part of the model -- each change one element, before and
after, undone and reviewed like a type -- and publishing (step B5) creates them on the tenant: the groups that
do not exist yet (by name, under their parent), then the folder rules. After that, administrators change the
live tree in the access window. The model holds ``access.groups`` ({key, name, parent}) and ``access.rules``
({key, prefix, groups, source}); the administrator group is the tenant's own and is never part of a design.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from builder import access as A
from builder.editing import ACCESS_GROUPS, ACCESS_RULES, OPERATIONS, EditError, _Edit, _items, _text

ADMIN = "admin"


def _slug(name: str) -> str:
    folded = name.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")[:60] or "gruppe"


def draft_tree(model: Mapping[str, Any]) -> A.Tree:
    """The design as a tree: the tenant's administrator group on top, the designed groups below it."""
    groups = {ADMIN: {"group_id": ADMIN, "name": "Administratoren", "is_admin": True, "parent_id": None}}
    for g in (model.get("access") or {}).get("groups") or []:
        groups[g["key"]] = {"group_id": g["key"], "name": g["name"], "is_admin": False, "parent_id": g.get("parent") or ADMIN}
    return A.Tree(groups)


def _check(edit: _Edit, fn, *args) -> Any:
    try:
        return fn(draft_tree(edit.model), *args)
    except A.AccessError as exc:
        raise EditError(exc.message, exc.status) from None


def _group(edit: _Edit, key: Any) -> dict:
    found = edit.find(ACCESS_GROUPS, key)
    if found is None:
        raise EditError("Diese Gruppe gibt es im Entwurf nicht.", 404)
    return found


def _group_create(edit: _Edit, op: Mapping[str, Any]) -> None:
    parent = op.get("parent") or ADMIN
    name = _check(edit, A.check_create, op.get("name"), parent)
    key = _slug(name)
    while edit.find(ACCESS_GROUPS, key) is not None or key == ADMIN:
        key += "-2"
    edit.write(ACCESS_GROUPS, None, {"key": key, "name": name, "parent": None if parent == ADMIN else parent})


def _group_rename(edit: _Edit, op: Mapping[str, Any]) -> None:
    old = _group(edit, op.get("key"))
    name = _check(edit, A.check_rename, old["key"], op.get("name"))
    edit.write(ACCESS_GROUPS, old, {**copy.deepcopy(old), "name": name})


def _group_move(edit: _Edit, op: Mapping[str, Any]) -> None:
    old = _group(edit, op.get("key"))
    parent = op.get("parent") or ADMIN
    _check(edit, A.check_move, old["key"], parent)
    edit.write(ACCESS_GROUPS, old, {**copy.deepcopy(old), "parent": None if parent == ADMIN else parent})


def _group_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    old = _group(edit, op.get("key"))
    rules = sum(1 for r in _items(edit.model, ACCESS_RULES, create=False) if old["key"] in (r.get("groups") or []))
    _check(edit, A.check_delete, old["key"], {"folder_rules": rules})
    edit.write(ACCESS_GROUPS, old, None)


def _rule_add(edit: _Edit, op: Mapping[str, Any]) -> None:
    prefix = _text(op.get("prefix"), "Ordner", limit=400)
    if not prefix.startswith("/") or ".." in prefix.split("/"):
        raise EditError("Ordner: ein Pfad wie /Mandate/Meier, ohne «..».")
    groups = [str(g) for g in op.get("groups") or []]
    if not groups or any(edit.find(ACCESS_GROUPS, g) is None for g in groups):
        raise EditError("Welcher Gruppe gehört der Ordner?")
    if any(r.get("prefix") == prefix for r in _items(edit.model, ACCESS_RULES, create=False)):
        raise EditError(f"Für {prefix} gibt es schon eine Ordnerregel.", 409)
    rule = {"key": _slug(prefix) or "regel", "prefix": prefix, "groups": groups}
    source = _text(op.get("source"), "Quelle", limit=120)
    if source:
        rule["source"] = source
    while edit.find(ACCESS_RULES, rule["key"]) is not None:
        rule["key"] += "-2"
    edit.write(ACCESS_RULES, None, rule)


def _rule_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    old = edit.find(ACCESS_RULES, op.get("key"))
    if old is None:
        raise EditError("Diese Ordnerregel gibt es im Entwurf nicht.", 404)
    edit.write(ACCESS_RULES, old, None)


OPERATIONS.update({
    "access.group.create": _group_create,
    "access.group.rename": _group_rename,
    "access.group.move": _group_move,
    "access.group.remove": _group_remove,
    "access.rule.add": _rule_add,
    "access.rule.remove": _rule_remove,
})
