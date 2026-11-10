"""A Jira issue as a ``record`` stream message (connector protocol 2.0, "Reading"), and the issue's version.

- ``external_id`` is the issue key; ``record_kind`` is ``issue``.
- ``version`` is the issue's **change-log position**: the id of its newest change-log entry, ``"0"`` when it has none.
  Every change to a field Jira tracks (assignee, due date, status, links, ...) adds an entry, so the version moves with
  every change a record shows; an entity property (the connector's permit marker) adds none. Ids are compared for
  equality only, except where the entries since a version are wanted (side effects, a conflict's values): there they
  are read as increasing integers, as Jira gives them. The change log is read for that id alone: nothing else of it
  (an earlier value, an author, a name) reaches a record or any other stream message.
- ``fields`` holds the settings' fields by Jira's field ids (``client.DEFAULT_FIELDS`` when they name none), each as a
  scalar: a person as their account id, never a name or an email address (an object that looks like a person and has
  no account id is null), an option, status, priority or type as its name or value, a date as Jira writes it, a
  whole number as an integer and any other number as a string (the protocol carries no floating-point number), and a
  rich-text field (Atlassian Document Format: the description, the environment) as plain text, at most 32,768
  bytes of utf-8, cut on a character boundary (``adf.py``). A field reaches the record only when the settings name
  it in ``fields``: a mapping that reads the description needs ``description`` there.
- ``computed`` names the fields the source computes, from the issue's edit metadata: a field read but not editable by
  this credential (``created``, ``resolutiondate``), and every field the settings name as set by an automation.
  ``status`` is not computed unless the settings say so: it is written through a transition. The edit metadata
  reaches the stream only so, as field ids of the settings' fields.
- ``links`` names the parent (``{"relation": "parent", "external_id": <its key>}``), which the mapping reads, and
  nothing else: issue links and sub-tasks are not read.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from knovas_connector_jira import adf
from knovas_connector_jira.client import LINK_FIELDS, Settings

RECORD_KIND = "issue"
MAX_LIST = 500
#: Keys that name an object's value, in the order they are looked for: a person, an option, a named object, a key.
_VALUE_KEYS = ("accountId", "value", "name", "key", "id")
#: Keys only a person (a Jira user object) has: such an object is its account id or nothing, never its name.
_PERSON_KEYS = frozenset({"accountId", "displayName", "emailAddress", "accountType"})


def scalar(value: Any) -> Any:
    """One value as the protocol carries it: a string, an integer, a boolean or null. A rich-text document (ADF)
    is its plain text (``adf.to_text``)."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value if abs(value) <= 2 ** 53 - 1 else str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if adf.is_adf(value):
        return adf.to_text(value)
    if isinstance(value, Mapping):
        if _PERSON_KEYS & set(value):
            account = value.get("accountId")
            return account if isinstance(account, str) else None
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
    """The parent, by its key; nothing else (issue links and sub-tasks are not carried)."""
    raw = issue.get("fields") if isinstance(issue.get("fields"), Mapping) else {}
    parent = raw.get("parent")
    if isinstance(parent, Mapping) and isinstance(parent.get("key"), str) and 0 < len(parent["key"]) <= 1024:
        return [{"relation": "parent", "external_id": parent["key"]}]
    return []


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


def requested_fields(settings: Settings, also: Iterable[str] = LINK_FIELDS) -> list[str]:
    """The fields a call asks Jira for: the settings' fields and ``also`` (for a listing the parent, the one link a
    record carries; for a preview or an act the fields its actions write), nothing else."""
    return list(settings.fields) + [f for f in also if f not in settings.fields]
