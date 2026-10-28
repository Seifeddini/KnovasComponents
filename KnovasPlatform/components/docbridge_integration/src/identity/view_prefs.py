"""Per-user memory of the Ansichten page: the last selection and recently opened nodes.

Kept in the Platform's ``settings`` table under ``views.user.<id>`` (the
experiments module keeps its per-user preferences the same way), never in the
browser. Holds node ids and the kind last used, nothing from the brain.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

RECENT_MAX = 12
_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
KINDS = ("graph", "trace", "board", "tree", "matrix", "list", "definitions")


def _key(user: Any) -> str:
    return f"views.user.{user.id}"


def get(conn: Any, user: Any) -> Dict[str, Any]:
    row = conn.execute("SELECT value FROM settings WHERE key = %s", (_key(user),)).fetchone()
    value = row[0] if row else {}
    if isinstance(value, str):
        value = json.loads(value or "{}")
    return {"selection": value.get("selection") or None, "kind": value.get("kind") or None,
            "recent": [r for r in value.get("recent") or [] if isinstance(r, str)]}


def remember(conn: Any, user: Any, node_id: str, kind: str = "") -> Dict[str, Any]:
    """Make ``node_id`` the selection and put it first in the recent list."""
    if not _ID.match(str(node_id or "")):
        raise ValueError("Keine gültige Knoten-Id.")
    current = get(conn, user)
    recent: List[str] = [node_id] + [r for r in current["recent"] if r != node_id]
    value = {"selection": node_id, "kind": kind if kind in KINDS else current["kind"],
             "recent": recent[:RECENT_MAX]}
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (%s, %s, now(), %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(), "
        "updated_by = EXCLUDED.updated_by",
        (_key(user), json.dumps(value), str(user.id)))
    return value
