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

    def _call(self, method: str, path: str, *, body: Any = None, query: dict | None = None) -> Any:
        url = f"{self._base}{path}"
        if query:
            url += "?" + urlencode({k: v for k, v in query.items() if v is not None})
        try:
            resp = self._session.request(method, url, json=body, headers=self._headers(),
                                         timeout=self._timeout)
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
        return self._call("GET", "/discover", query={"root": root, "max_depth": max_depth})

    def status(self) -> dict:
        return self._call("GET", "/sync/status")

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
        """Config first, then the folder list. If RemoteController refuses the
        folder list, the previous config is put back so the two never diverge."""
        previous = self._previous_sync_config()
        self._call("POST", "/sync/config", body=compiled.sync_config)
        try:
            return self._call("POST", "/sync", body=compiled.sync_request)
        except RemoteControllerError:
            try:
                self._call("POST", "/sync/config", body=previous)
            except Exception as rollback_exc:  # noqa: BLE001
                logger.error("Rollback der Sync-Konfiguration fehlgeschlagen: %s", rollback_exc)
            raise
