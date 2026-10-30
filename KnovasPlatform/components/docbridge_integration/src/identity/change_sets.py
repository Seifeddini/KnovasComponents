"""The change sets users build in the Ansichten views (Step 7): one open set per user.

A change set is a list of operations (graph_views.change_set) at one base commit of KnowledgeBase,
plus the user's reasons for decision records. On submit it becomes one pull request; afterwards the
row remembers which, so every view can say "Änderung ausstehend: PR #n" until the client-zero loader
has carried the merge into the tenant. Rows are tuples, mapped by position, like the rest of identity/.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from uuid import UUID

_COLS = ("id, user_id, state, base_branch, base_commit, operations, reasons, pr_number, pr_url, branch, "
         "created_at, updated_at, submitted_at")


def _iso(v: Any) -> Optional[str]:
    return v.isoformat(timespec="seconds") if v else None


def _row(r) -> Dict[str, Any]:
    load = lambda v: v if isinstance(v, (dict, list)) else json.loads(v or "null")  # noqa: E731
    return {"id": str(r[0]), "user_id": str(r[1]), "state": r[2],
            "base": {"branch": r[3], "commit": str(r[4]).strip()},
            "ops": load(r[5]) or [], "reasons": load(r[6]) or {},
            "pr_number": r[7], "pr_url": r[8], "branch": r[9],
            "created_at": _iso(r[10]), "updated_at": _iso(r[11]), "submitted_at": _iso(r[12])}


class ChangeSetStore:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def open_for(self, user_id: Any) -> Optional[Dict[str, Any]]:
        r = self._conn.execute(f"SELECT {_COLS} FROM change_sets WHERE user_id = %s AND state = 'open'",
                               (str(user_id),)).fetchone()
        return _row(r) if r else None

    def create(self, user_id: Any, branch: str, commit: str) -> Dict[str, Any]:
        r = self._conn.execute(
            f"INSERT INTO change_sets (user_id, base_branch, base_commit) VALUES (%s, %s, %s) RETURNING {_COLS}",
            (str(user_id), branch, commit)).fetchone()
        return _row(r)

    def save(self, change_set: Dict[str, Any]) -> Dict[str, Any]:
        """Operations, reasons and base of an open set."""
        r = self._conn.execute(
            "UPDATE change_sets SET operations = %s, reasons = %s, base_commit = %s, updated_at = now() "
            f"WHERE id = %s AND state = 'open' RETURNING {_COLS}",
            (json.dumps(change_set["ops"]), json.dumps(change_set.get("reasons") or {}),
             change_set["base"]["commit"], change_set["id"])).fetchone()
        return _row(r) if r else change_set

    def discard(self, set_id: str) -> None:
        self._conn.execute("UPDATE change_sets SET state = 'discarded', updated_at = now() "
                           "WHERE id = %s AND state = 'open'", (set_id,))

    def submitted(self, set_id: str, *, pr_number: Optional[int], pr_url: Optional[str], branch: str,
                  reasons: Dict[str, str]) -> None:
        self._conn.execute(
            "UPDATE change_sets SET state = 'submitted', pr_number = %s, pr_url = %s, branch = %s, reasons = %s, "
            "submitted_at = now(), updated_at = now() WHERE id = %s",
            (pr_number, pr_url, branch, json.dumps(reasons), set_id))

    def set_state(self, set_id: str, state: str) -> None:
        self._conn.execute("UPDATE change_sets SET state = %s, updated_at = now() WHERE id = %s", (state, set_id))

    def pending_for(self, node_id: str) -> List[Dict[str, Any]]:
        """Submitted or merged sets with an operation on this instance, newest first."""
        try:
            nid = str(UUID(str(node_id)))
        except ValueError:
            return []
        rows = self._conn.execute(
            f"SELECT {_COLS} FROM change_sets WHERE state IN ('submitted', 'merged') AND operations @> %s::jsonb "
            "ORDER BY submitted_at DESC", (json.dumps([{"instance": nid}]),)).fetchall()
        return [_row(r) for r in rows]

    def pending(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            f"SELECT {_COLS} FROM change_sets WHERE state IN ('submitted', 'merged') ORDER BY submitted_at DESC").fetchall()
        return [_row(r) for r in rows]


def refresh(rows: List[Dict[str, Any]], store: ChangeSetStore, repo: Any, applied: Any) -> List[Dict[str, Any]]:
    """Advance each set: submitted → merged/closed by its PR; merged → applied once ``applied(set)`` says the
    tenant shows every operation. Returns the sets still pending."""
    out = []
    for row in rows:
        state = row["state"]
        if state == "submitted" and repo is not None and hasattr(repo, "get_pull") and row.get("pr_number"):
            pr = repo.get_pull(row["pr_number"])
            if pr and pr.get("merged"):
                state = "merged"
            elif pr and pr.get("state") == "closed":
                state = "closed"
        if state == "merged" and applied(row):
            state = "applied"
        if state != row["state"]:
            store.set_state(row["id"], state)
        if state in ("submitted", "merged"):
            out.append({**row, "state": state})
    return out
