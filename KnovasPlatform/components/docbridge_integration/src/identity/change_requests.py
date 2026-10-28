"""Change requests the Platform opened as pull requests (Step 7).

Remembers which PR belongs to which instance, so the TRACE can say
"Änderung beantragt: PR #n" until the client-zero loader has carried the merge
into the tenant. Rows are tuples, mapped by position, like the rest of
identity/.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from uuid import UUID

_COLS = ("id, node_id, process_key, instance_key, plan_path, change_class, fields, expected, "
         "pr_number, pr_url, branch, state, created_by, created_at")


def _row(r) -> Dict[str, Any]:
    load = lambda v: v if isinstance(v, (dict, list)) else json.loads(v or "null")  # noqa: E731
    return {"id": str(r[0]), "node_id": str(r[1]), "process_key": r[2], "instance_key": r[3],
            "plan_path": r[4], "class": r[5], "fields": load(r[6]) or [], "expected": load(r[7]) or {},
            "pr_number": r[8], "pr_url": r[9], "branch": r[10], "state": r[11],
            "created_by": str(r[12]) if r[12] else None,
            "created_at": r[13].isoformat(timespec="seconds") if r[13] else None}


class ChangeRequestStore:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def record(self, *, node_id: str, process_key: str, instance_key: str, plan_path: str,
               change_class: str, fields: List[str], expected: Dict[str, Any], pr_number: Optional[int],
               pr_url: Optional[str], branch: str, user: Any) -> Dict[str, Any]:
        r = self._conn.execute(
            "INSERT INTO change_requests (node_id, process_key, instance_key, plan_path, change_class, fields, "
            f"expected, pr_number, pr_url, branch, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {_COLS}",
            (str(UUID(str(node_id))), process_key, instance_key, plan_path, change_class, json.dumps(fields),
             json.dumps(expected), pr_number, pr_url, branch, str(user.id) if user is not None else None)).fetchone()
        return _row(r)

    def unfinished(self, node_id: str) -> List[Dict[str, Any]]:
        """Open and merged-but-not-yet-applied requests of one instance, newest first."""
        try:
            nid = str(UUID(str(node_id)))
        except ValueError:
            return []
        rows = self._conn.execute(
            f"SELECT {_COLS} FROM change_requests WHERE node_id = %s AND state IN ('open', 'merged') "
            "ORDER BY created_at DESC", (nid,)).fetchall()
        return [_row(r) for r in rows]

    def set_state(self, request_id: str, state: str) -> None:
        self._conn.execute("UPDATE change_requests SET state = %s, updated_at = now() WHERE id = %s",
                           (state, request_id))


def refresh(rows: List[Dict[str, Any]], store: ChangeRequestStore, repo: Any,
            node_facts: Dict[str, str]) -> List[Dict[str, Any]]:
    """Advance each row: open → merged/closed by the PR, merged → applied when the tenant shows it."""
    out = []
    for row in rows:
        state = row["state"]
        if state == "open" and repo is not None and hasattr(repo, "get_pull") and row.get("pr_number"):
            pr = repo.get_pull(row["pr_number"])
            if pr and pr["merged"]:
                state = "merged"
            elif pr and pr["state"] == "closed":
                state = "closed"
        if state == "merged" and row["expected"] and all(
                str(node_facts.get(k, "")).strip() == str(v).strip() for k, v in row["expected"].items()):
            state = "applied"
        if state != row["state"]:
            store.set_state(row["id"], state)
        if state in ("open", "merged"):
            out.append({**row, "state": state})
    return out
