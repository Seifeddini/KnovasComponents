"""The console's client for the firm's own RemoteController.

Every call goes out as the signed-in person: the same Ed25519 assertion the
Platform sends Knovas, here in the X-Platform-Principal header, verified by
RemoteController's require_operator_or_tenant_admin. No session, no call.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import requests

from identity.ingestion_compiler import CompiledIngestion

logger = logging.getLogger(__name__)

PRINCIPAL_HEADER = "X-Platform-Principal"

#: The scheduler states RemoteController reports while a continuous worker
#: exists. Read off ``RC/src/sync/sync_scheduler.py::_set_status``: every
#: status ``_run_once`` sets is set *by the worker*, so seeing one means a
#: worker is looping and will re-read the body at its next cycle. Everything
#: else -- ``not_running``, ``completed``, ``awaiting_initial_sync_body``,
#: ``worker_crashed``, ``worker_stopped``, and a missing key -- is idle, and
#: an idle scheduler has to be started for the profile to take effect.
SCHEDULER_RUNNING_STATES = frozenset({
    "running",
    "paused_outside_window",
    "idle_between_cycles",
    "backlog_pending",
    "subfolders_complete",
    "disabled",
    "error",
    # paused_reason values the executor reports mid-cycle; the worker is alive
    "scan_limit_reached",
    "cycle_time_limit",
    "stop_requested",
    "rate_limited",
})

#: A status render happens on every page load and a preview makes one
#: discover call per folder, so neither may sit on the long push timeout: a
#: RemoteController that blackholes instead of refusing would turn the tab
#: into a gunicorn timeout (M4).
STATUS_TIMEOUT_SECONDS = 5.0
DISCOVER_TIMEOUT_SECONDS = 10.0


class RemoteControllerError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RemoteControllerClient:
    def __init__(self, base_url: str, *, principal_broker, session=None,
                 timeout: float = 20.0) -> None:
        self._base = base_url.rstrip("/")
        self._broker = principal_broker
        self._session = session or requests.Session()
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        user = self._broker.current_user()
        if user is None:
            raise PermissionError("Kein angemeldeter Benutzer; RemoteController wird nicht aufgerufen.")
        return {PRINCIPAL_HEADER: self._broker.assertion_for(user),
                "Content-Type": "application/json"}

    def _call(self, method: str, path: str, *, body: Any = None, query: dict | None = None,
              timeout: float | None = None) -> Any:
        url = f"{self._base}{path}"
        if query:
            url += "?" + urlencode({k: v for k, v in query.items() if v is not None})
        try:
            resp = self._session.request(method, url, json=body, headers=self._headers(),
                                         timeout=self._timeout if timeout is None else timeout)
        except requests.RequestException as exc:
            raise RemoteControllerError(f"RemoteController nicht erreichbar: {exc}", status=None) from exc
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {}
        if resp.status_code >= 400:
            message = str((payload or {}).get("error") or f"HTTP {resp.status_code}")
            raise RemoteControllerError(message, status=resp.status_code)
        return payload

    def discover(self, root: str | None = None, max_depth: int = 3) -> dict:
        return self._call("GET", "/discover", query={"root": root, "max_depth": max_depth},
                          timeout=DISCOVER_TIMEOUT_SECONDS)

    def status(self) -> dict:
        return self._call("GET", "/sync/status", timeout=STATUS_TIMEOUT_SECONDS)

    def start(self) -> dict:
        return self._call("POST", "/sync/start", body={})

    def stop(self) -> dict:
        return self._call("POST", "/sync/stop", body={})

    def get_sync_config(self) -> dict:
        return self._call("GET", "/sync/config")

    def _previous_sync_config(self) -> dict:
        """The config a rollback would restore, or a sentence naming the switch.

        RemoteController's sync-config API is off unless
        RC_SYNC_CONFIG_API_ENABLED is true, and a disabled API answers 404
        with "Sync config API is disabled" -- true, and useless to the
        administrator who reads it in the console. Name the variable instead.
        """
        try:
            return self.get_sync_config()
        except RemoteControllerError as exc:
            if exc.status == 404:
                raise RemoteControllerError(
                    "RemoteController hat die Sync-Konfigurations-API abgeschaltet "
                    "(RC_SYNC_CONFIG_API_ENABLED=false); ohne sie kann das Profil "
                    "nicht uebertragen werden.",
                    status=404,
                ) from exc
            raise

    def push(self, compiled: CompiledIngestion) -> dict:
        """Config first, then the folder list, then whatever it takes to make
        the profile actually run. Returns ``{"applied": ...}``, one of:

        - ``"started"``  -- the scheduler was idle and has been started;
        - ``"next_cycle"`` -- a worker is already running and re-reads the
          body at the top of its next cycle;
        - ``"stored"`` -- a one_time (``manual``) profile, which only runs
          when a person presses Start; or a continuous one whose start
          failed, in which case ``start_error`` carries the reason.

        Never ``POST /sync``: that route answers ``already_running`` and
        changes nothing when a worker holds the lock, and in one_time mode it
        performs a whole scan-and-upload inside the request. ``/sync/body``
        stores, and starting is a separate decision.

        If RemoteController refuses the folder list, the previous config is
        put back so the two never diverge. A failed *start* is not rolled
        back: the profile is on RemoteController, and undoing the config
        would create exactly the divergence the rollback exists to prevent.
        """
        previous = self._previous_sync_config()
        self._call("POST", "/sync/config", body=compiled.sync_config)
        try:
            self._call("POST", "/sync/body", body=compiled.sync_request)
        except RemoteControllerError:
            try:
                self._call("POST", "/sync/config", body=previous)
            except Exception as rollback_exc:  # noqa: BLE001
                logger.error("Rollback der Sync-Konfiguration fehlgeschlagen: %s", rollback_exc)
            raise

        if compiled.sync_config.get("mode") != "continuous":
            return {"applied": "stored"}
        try:
            state = str((self.status() or {}).get("scheduler_state") or "")
            if state in SCHEDULER_RUNNING_STATES:
                return {"applied": "next_cycle"}
            self.start()
        except RemoteControllerError as exc:
            # Reading the state or starting the worker failed. Say so; do not
            # pretend the profile did not arrive, and do not roll it back.
            return {"applied": "stored", "start_error": str(exc)}
        return {"applied": "started"}
