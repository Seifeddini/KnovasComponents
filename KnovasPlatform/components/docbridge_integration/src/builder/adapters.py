"""Components as they report today, until they speak connector protocol v1 (build plan step B13).

Protocol v1 (SS-534) is not built yet in the components themselves. Until it
is, the registry shows the two that exist through adapters over what they
already report:

- the KnovasConnector: GET /health (reachable, scheduler, watch roots) and
  GET /sync/status (scheduler state, last run, files processed, the last
  worker error); pause and resume are its POST /sync/stop and /sync/start.
  A dry run it cannot do: its sync is not brain-runner's.
- the GitHub runner: the Platform's own sync of the configuration package
  (brain_sync): its runs are dry runs (probe) and applies, its held plans the
  mappings the deletion guard blocked in the latest probe, approved by an apply
  that names the reason (brain-runner's ``approve_guard``).

An adapter answers ``observe()`` with the fields a v1 heartbeat carries, so the
registry treats both alike. It never reads document names or content.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Mapping, Optional


class AdapterError(RuntimeError):
    """What the component cannot do through its adapter; the message is for the page."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class KnovasConnectorAdapter:
    """The KnovasConnector through its HTTP API (knovas_connector_client.KnovasConnectorClient)."""

    def __init__(self, client: Any):
        self.client = client

    def observe(self) -> dict:
        try:
            status = self.client.status() or {}
        except Exception as exc:  # noqa: BLE001 - unreachable is a state, not an error page
            return {"reachable": False, "reported_state": "offline", "why": type(exc).__name__}
        extraction = status.get("extraction") or {}
        worker_error = bool(status.get("last_worker_error"))
        scheduler = str(status.get("scheduler_state") or "")
        return {"reachable": True, "heartbeat_at": _now(),
                "reported_state": "degraded" if worker_error or status.get("worker_alive") is False else "online",
                "paused": scheduler in ("stopped", "paused"),
                "version": str(extraction.get("knovas_extract_version") or ""),
                "capabilities": list(status.get("capabilities") or []),
                "open_errors": 1 if worker_error else 0,
                "last_run": {"at": status.get("last_run_at"), "status": str(status.get("current_status") or ""),
                             "items": int(status.get("files_processed") or 0)}}

    def run(self, kind: str, user: Any) -> str:
        if kind != "apply":
            raise AdapterError("Einen Probelauf kann der KnovasConnector erst mit dem Connector-Protokoll v1.")
        self.client.start()
        return "gestartet"

    def pause(self, user: Any) -> None:
        self.client.stop()

    def resume(self, user: Any) -> None:
        self.client.start()

    def runs(self, limit: int = 30) -> list[dict]:
        return []

    def held(self) -> list[dict]:
        return []

    def decide(self, mapping: str, approve: bool, reason: str, user: Any) -> str:
        raise AdapterError("Der KnovasConnector hält heute keine Pläne zurück.")


class GitHubRunnerAdapter:
    """The Platform's sync of the configuration package from GitHub (brain_sync.SyncService)."""

    def __init__(self, sync: Any):
        self.sync = sync

    def observe(self) -> dict:
        try:
            from brain_sync.runner import PackageRunner

            version = PackageRunner.version()
        except Exception:  # noqa: BLE001 - brain-runner missing from the image
            version = ""
        runs = self.runs(limit=5)
        failed = [r for r in runs if r["status"] == "failed"]
        last = runs[0] if runs else None
        return {"reachable": True, "heartbeat_at": _now(), "version": version,
                "reported_state": "degraded" if not version or (last and last["status"] == "failed") else "online",
                "paused": False, "open_errors": len(failed[:1]),
                "lag_seconds": self._lag(), "capabilities": ["dry_run", "apply", "held_plans"]}

    def _lag(self) -> Optional[int]:
        cur = self.sync.current() if hasattr(self.sync, "current") else None
        at = (cur or {}).get("at")
        return int(dt.datetime.now(dt.timezone.utc).timestamp() - float(at)) if at else None

    def runs(self, limit: int = 30) -> list[dict]:
        out = []
        for r in self.sync.history(limit) or []:
            counts = r.get("counts") if isinstance(r.get("counts"), Mapping) else {}
            total = {k: sum(int((c or {}).get(k) or 0) for c in counts.values()) for k in ("add", "change", "retire")}
            status = {"ok": "ok", "failed": "failed", "refused": "failed", "running": "running"}.get(r.get("status"), "ok")
            if r.get("kind") == "probe" and any((c or {}).get("blocked") for c in counts.values()):
                status = "held"
            out.append({"id": r.get("id"), "kind": "dry_run" if r.get("kind") == "probe" else "apply", "status": status,
                        "started_at": r.get("started_at"), "finished_at": r.get("finished_at"),
                        "duration_ms": r.get("duration_ms"), "added": total["add"], "changed": total["change"],
                        "retired": total["retire"], "errors": 1 if r.get("status") == "failed" else 0})
        return out

    def held(self) -> list[dict]:
        """The mappings the deletion guard blocked in the newest probe, while no apply came after it."""
        history = self.sync.history(20) or []
        out = []
        for r in history:
            if r.get("kind") == "apply" and r.get("status") == "ok":
                break
            if r.get("kind") == "probe" and r.get("status") == "ok":
                for mid, c in (r.get("counts") or {}).items():
                    if (c or {}).get("guard_tripped"):
                        out.append({"id": f"{r.get('id')}:{mid}", "mapping": mid, "held_at": r.get("finished_at"),
                                    "retire": {"*": int((c or {}).get("retire") or 0)}, "state": "held",
                                    "run": r.get("id")})
                break
        return out

    def run(self, kind: str, user: Any) -> str:
        return self.sync.probe(user) if kind == "dry_run" else self.sync.apply(user)

    def pause(self, user: Any) -> None:
        raise AdapterError("Die Synchronisation von GitHub läuft heute nur auf Anforderung; Pausieren kommt mit Protokoll v1.")

    def resume(self, user: Any) -> None:
        raise AdapterError("Die Synchronisation von GitHub läuft heute nur auf Anforderung.")

    def decide(self, mapping: str, approve: bool, reason: str, user: Any) -> str:
        if not approve:
            return "abgelehnt: der nächste Probelauf plant neu"
        return self.sync.apply(user, approve_guard=reason)
