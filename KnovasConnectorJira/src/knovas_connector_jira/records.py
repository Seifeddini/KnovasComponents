"""A Jira issue as a ``record`` stream message (connector protocol 2.0, "Reading"), and the issue's version.

- ``external_id`` is the issue key; ``record_kind`` is ``issue``.
- ``version`` is the issue's **change-log position**: the id of its newest change-log entry, ``"0"`` when it has none.
  Every change to a field Jira tracks (assignee, due date, status, links, ...) adds an entry, so the version moves with
  every change a record shows; an entity property (the connector's permit marker) adds none. Ids are compared for
  equality only, except where the entries since a version are wanted (side effects, a conflict's values): there they
  are read as increasing integers, as Jira gives them.
- ``fields`` holds the settings' fields by Jira's field ids, each as a scalar: a person as their account id (never a
  name or an email address), an option, status, priority or type as its name or value, a date as Jira writes it, a
  whole number as an integer and any other number as a string (the protocol carries no floating-point number).
- ``computed`` names the fields the source computes, from the issue's edit metadata: a field read but not editable by
  this credential (``created``, ``resolution``), and every field the settings name as set by an automation. ``status``
  is not computed unless the settings say so: it is written through a transition.
- ``links`` names the issues this one links to (by the link type's outward or inward name), its parent and its
  sub-tasks.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from knovas_connector_jira.client import LINK_FIELDS, Settings

RECORD_KIND = "issue"
MAX_LIST = 500
MAX_LINKS = 500
#: Keys that name an object's value, in the order they are looked for: a person, an option, a named object, a key.
_VALUE_KEYS = ("accountId", "value", "name", "key", "id")


def scalar(value: Any) -> Any:
    """One value as the protocol carries it: a string, an integer, a boolean or null."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value if abs(value) <= 2 ** 53 - 1 else str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, Mapping):
        for k in _VALUE_KEYS:
            if k in value and not isinstance(value[k], (Mapping, list)):
                return scalar(value[k])
        return None
    return None


def field_value(value: Any) -> Any:
    if isinstance(value, list):
        return [scalar(v) for v in value[:MAX_LIST] if not isinstance(v, list)]
    return scalar(value)


def version_of(histories: Optional[Iterable[Mapping]]) -> str:
    """The id of the newest change-log entry among ``histories``, ``"0"`` for none."""
    ids = [int(h["id"]) for h in histories or () if isinstance(h, Mapping) and str(h.get("id", "")).isdigit()]
    return str(max(ids)) if ids else "0"


def entries_since(histories: Optional[Iterable[Mapping]], version: str) -> list[Mapping]:
    """The change-log entries after ``version``, oldest first."""
    if not str(version).isdigit():
        return []
    after = [h for h in histories or () if isinstance(h, Mapping) and str(h.get("id", "")).isdigit()
             and int(h["id"]) > int(version)]
    return sorted(after, key=lambda h: int(h["id"]))


def changed_fields(entries: Iterable[Mapping]) -> list[str]:
    """The ids of the fields the entries changed."""
    out: set = set()
    for h in entries:
        for item in h.get("items") or ():
            name = (item or {}).get("fieldId") or (item or {}).get("field")
            if isinstance(name, str) and 0 < len(name) <= 200:
                out.add(name)
    return sorted(out)


def value_of(issue: Mapping, field: str) -> Any:
    """One field of the issue as a record carries it."""
    raw = issue.get("fields") if isinstance(issue.get("fields"), Mapping) else {}
    return field_value(raw.get(field))


def fields_of(issue: Mapping, settings: Settings) -> dict:
    return {f: value_of(issue, f) for f in settings.fields}


def links_of(issue: Mapping) -> list[dict]:
    raw = issue.get("fields") if isinstance(issue.get("fields"), Mapping) else {}
    links: set = set()
    for link in raw.get("issuelinks") or ():
        kind = (link or {}).get("type") or {}
        for side, name in (("outwardIssue", kind.get("outward")), ("inwardIssue", kind.get("inward"))):
            other = (link or {}).get(side)
            if isinstance(other, Mapping) and isinstance(other.get("key"), str) and isinstance(name, str) and name:
                links.add((name[:200], other["key"]))
    parent = raw.get("parent")
    if isinstance(parent, Mapping) and isinstance(parent.get("key"), str):
        links.add(("parent", parent["key"]))
    for sub in raw.get("subtasks") or ():
        if isinstance(sub, Mapping) and isinstance(sub.get("key"), str):
            links.add(("subtask", sub["key"]))
    return [{"relation": r, "external_id": k} for r, k in sorted(links)][:MAX_LINKS]


def editable(editmeta: Optional[Mapping]) -> set:
    """The fields the edit metadata lets this credential set."""
    fields = (editmeta or {}).get("fields") if isinstance((editmeta or {}).get("fields"), Mapping) else {}
    return {f for f, meta in fields.items()
            if not isinstance((meta or {}).get("operations"), list) or "set" in meta["operations"]}


def computed_of(settings: Settings, editmeta: Optional[Mapping]) -> list[str]:
    can_set = editable(editmeta)
    return sorted(f for f in settings.fields
                  if f in settings.computed_fields or (f != "status" and f not in can_set))


def record(source: str, issue: Mapping, settings: Settings, editmeta: Optional[Mapping]) -> dict:
    payload = {"record_kind": RECORD_KIND, "fields": fields_of(issue, settings),
               "computed": computed_of(settings, editmeta), "links": links_of(issue)}
    return {"type": "record", "source": source, "external_id": issue["key"],
            "version": version_of(((issue.get("changelog") or {}).get("histories"))), "payload": payload}


def requested_fields(settings: Settings) -> list[str]:
    return list(settings.fields) + [f for f in LINK_FIELDS if f not in settings.fields]
