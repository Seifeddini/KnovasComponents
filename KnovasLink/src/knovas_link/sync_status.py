"""A component's sync status per source (connector protocol 1.2, KG-2): what its heartbeat may say about each source it
serves, checked before Link keeps it and passes it on in its own heartbeat's ``site.sources``.

Commits, times, counts and codes only: the commit the last apply without errors synced and when, the last check and
the next one, the commit the last poll planned, why a plan was not applied on its own (codes), and what the last apply
wrote (counts). Anything else in an entry is dropped; anything of the wrong shape is refused (400 ``bad_field``).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Optional

from knovas_link.errors import LocalError

COMMIT = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
CODE = re.compile(r"^[a-z][a-z0-9_.]{1,60}$")
MAX_SOURCES = 200
#: The fields an entry may carry besides its workspace and key, as Link passes them on.
FIELDS = ("synced_commit", "synced_at", "checked_at", "next_check_at", "planned_commit", "held_back", "last_apply")


def _bad(what: str) -> LocalError:
    return LocalError(400, "bad_field", f"sources.{what}")


def _commit(value: Any, what: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not COMMIT.match(value):
        raise _bad(f"{what}: a commit")
    return value


def _when(value: Any, what: str) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise _bad(f"{what}: an ISO 8601 time") from None
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).isoformat()


def _count(value: Any, what: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10 ** 9:
        raise _bad(f"{what}: a whole number")
    return value


def check(raw: Any) -> list[dict]:
    """The entries as Link keeps them; 400 ``bad_field`` for anything shaped like a name or free text."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_SOURCES:
        raise _bad(f"a list of up to {MAX_SOURCES}")
    out = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("workspace"), str) or not isinstance(item.get("key"), str):
            raise _bad("workspace and key")
        held = item.get("held_back") or []
        if not isinstance(held, list) or len(held) > 10 or not all(isinstance(c, str) and CODE.match(c) for c in held):
            raise _bad("held_back: up to 10 codes")
        entry = {"workspace": item["workspace"], "key": item["key"],
                 "synced_commit": _commit(item.get("synced_commit"), "synced_commit"),
                 "synced_at": _when(item.get("synced_at"), "synced_at"),
                 "checked_at": _when(item.get("checked_at"), "checked_at"),
                 "next_check_at": _when(item.get("next_check_at"), "next_check_at"),
                 "planned_commit": _commit(item.get("planned_commit"), "planned_commit"),
                 "held_back": list(held)}
        last = item.get("last_apply")
        if last is not None:
            if not isinstance(last, dict) or last.get("status") not in ("ok", "held", "failed"):
                raise _bad("last_apply: {at, status: ok|held|failed, counts, auto}")
            if last.get("auto") is not None and not isinstance(last.get("auto"), bool):
                raise _bad("last_apply.auto: true or false")
            entry["last_apply"] = {"at": _when(last.get("at"), "last_apply.at"), "status": last["status"],
                                   **{k: _count(last.get(k), f"last_apply.{k}") for k in ("added", "changed", "retired",
                                                                                          "errors")},
                                   "auto": bool(last.get("auto"))}
        out.append(entry)
    return out
