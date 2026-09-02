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
        resp = self._session.request(method, url, json=body, headers=self._headers(),
                                     timeout=self._timeout)
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

    def push(self, compiled: CompiledIngestion) -> dict:
        """Config first, then the folder list. If RemoteController refuses the
        folder list, the previous config is put back so the two never diverge."""
        previous = self.get_sync_config()
        self._call("POST", "/sync/config", body=compiled.sync_config)
        try:
            return self._call("POST", "/sync", body=compiled.sync_request)
        except RemoteControllerError:
            try:
                self._call("POST", "/sync/config", body=previous)
            except RemoteControllerError as rollback_exc:  # noqa: BLE001
                logger.error("Rollback der Sync-Konfiguration fehlgeschlagen: %s", rollback_exc)
            raise
