"""The sync history (migration 0010_brain_sync): one row per Probelauf or Anwenden, counts and hashes only.

At most one running row per tenant (a partial unique index), so a second start answers Busy. Every finished run is
also an audit entry (``brain_sync.probe`` / ``brain_sync.apply``) with the commit, the result and the counts.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: A running row older than this is a sync that died with its worker; it no longer blocks the tenant.
STALE_RUNNING_MINUTES = 120

_COLUMNS = ("r.id, r.tenant, r.kind, r.branch, r.commit_sha, u.email, r.started_at, r.finished_at, r.status, "
            "r.progress, r.files_fetched, r.counts, r.result, r.duration_ms, r.plan_sha256, r.tenant_ref, r.probe_id")


class Busy(Exception):
    """Another sync of this tenant is running."""


def _row(r) -> Dict[str, Any]:
    counts = r[11] if isinstance(r[11], dict) else json.loads(r[11] or "{}")
    return {"id": str(r[0]), "tenant": r[1], "kind": r[2], "branch": r[3], "commit": r[4], "started_by": r[5],
            "started_at": r[6].isoformat() if r[6] else None, "finished_at": r[7].isoformat() if r[7] else None,
            "status": r[8], "progress": r[9], "files_fetched": r[10], "counts": counts, "result": r[12],
            "duration_ms": r[13], "plan_sha256": r[14], "tenant_ref": r[15], "probe_id": str(r[16]) if r[16] else None}


class SyncStore:
    def __init__(self, conn: Any, *, owned: bool = False):
        self._conn = conn
        self._owned = owned

    def close(self) -> None:
        if self._owned:
            try:
                self._conn.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("brain sync: close failed (%s)", type(exc).__name__)

    def start(self, tenant: str, kind: str, user: Any, *, branch: str = "", commit: Optional[str] = None,
              tenant_ref: str = "", probe_id: Optional[str] = None) -> str:
        """A running row for the tenant; Busy when one is running already."""
        import psycopg

        self._conn.execute(
            "UPDATE brain_sync_runs SET status = 'failed', finished_at = now(), "
            "result = 'abgebrochen (der Prozess lief nicht mehr)' WHERE tenant = %s AND status = 'running' "
            f"AND started_at < now() - interval '{STALE_RUNNING_MINUTES} minutes'", (tenant,))
        try:
            r = self._conn.execute(
                "INSERT INTO brain_sync_runs (tenant, kind, branch, commit_sha, started_by, tenant_ref, probe_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (tenant, kind, branch, commit, str(user.id) if user is not None else None, tenant_ref, probe_id)).fetchone()
        except psycopg.errors.UniqueViolation:
            raise Busy("Für diesen Mandanten läuft schon ein Sync. Warten, bis er fertig ist.") from None
        return str(r[0])

    def progress(self, run_id: str, text: str, **fields: Any) -> None:
        sets, values = ["progress = %s"], [text[:200]]
        for k in ("commit_sha", "files_fetched", "plan_sha256"):
            if k in fields:
                sets.append(f"{k} = %s")
                values.append(fields[k])
        if "counts" in fields:
            sets.append("counts = %s")
            values.append(json.dumps(fields["counts"]))
        self._conn.execute(f"UPDATE brain_sync_runs SET {', '.join(sets)} WHERE id = %s", (*values, run_id))

    def finish(self, run_id: str, status: str, *, result: str = "", user: Any = None, audit: bool = True) -> Dict[str, Any]:
        self._conn.execute(
            "UPDATE brain_sync_runs SET status = %s, result = %s, finished_at = now(), progress = '', "
            "duration_ms = (EXTRACT(EPOCH FROM (now() - started_at)) * 1000)::int WHERE id = %s",
            (status, result[:500], run_id))
        row = self.get(run_id)
        if audit and row is not None:
            from identity import audit as audit_log

            audit_log.record(self._conn, action=f"brain_sync.{row['kind']}", actor=user, target_type="brain_sync_run",
                             target_id=run_id, outcome="ok" if status == "ok" else status,
                             detail={"tenant": row["tenant"], "commit": row["commit"], "status": status,
                                     "files_fetched": row["files_fetched"], "counts": row["counts"],
                                     "plan_sha256": row["plan_sha256"], "result": row["result"]})
        return row

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        r = self._conn.execute(f"SELECT {_COLUMNS} FROM brain_sync_runs r LEFT JOIN users u ON u.id = r.started_by "
                               "WHERE r.id = %s", (run_id,)).fetchone()
        return _row(r) if r else None

    def latest(self, tenant: str, kind: str, status: Optional[str] = None) -> Optional[Dict[str, Any]]:
        sql = (f"SELECT {_COLUMNS} FROM brain_sync_runs r LEFT JOIN users u ON u.id = r.started_by "
               "WHERE r.tenant = %s AND r.kind = %s")
        params: List[Any] = [tenant, kind]
        if status:
            sql += " AND r.status = %s"
            params.append(status)
        r = self._conn.execute(sql + " ORDER BY r.started_at DESC, r.id DESC LIMIT 1", params).fetchone()
        return _row(r) if r else None

    def history(self, tenant: str, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM brain_sync_runs r LEFT JOIN users u ON u.id = r.started_by "
            "WHERE r.tenant = %s ORDER BY r.started_at DESC, r.id DESC LIMIT %s", (tenant, int(limit))).fetchall()
        return [_row(r) for r in rows]
