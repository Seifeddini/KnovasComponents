"""Connector protocol v1, Link's side towards the Platform (the control plane): Link calls out, the Platform never
calls in. Adapted from the sync component's ``client.py`` (MVP-15) to asyncio, so that the local API's long-polls do
not each hold a thread.

Calls Link makes for one of its components carry ``X-Sub-Component: <name>``: the component's command reports and its
run, event and check reports. Link's own heartbeat carries the site's telemetry (MVP-19, ``Link.heartbeat_body``).
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from knovas_link import CAPABILITIES, KIND, VERSION, tls
from knovas_link.identity import Credentials


class Refused(RuntimeError):
    """The Platform answered with an error; ``code`` is the protocol's, for logs."""

    def __init__(self, status: int, code: str):
        super().__init__(f"{status} {code}")
        self.status, self.code = status, code


class Unreachable(RuntimeError):
    """The Platform did not answer; the text is the exception's type, never an address."""


def _code(data: Any) -> str:
    return str(data.get("error") or "no_answer") if isinstance(data, dict) else "no_answer"


class PlatformClient:
    def __init__(self, url: str, *, ca_file: Optional[str] = None, transport: Optional[httpx.AsyncBaseTransport] = None,
                 timeout: float = 30.0):
        self.url = url.rstrip("/")
        self.ca_file = ca_file
        self.transport = transport
        self.timeout = timeout
        self.credentials: Optional[Credentials] = None
        self._http: Optional[httpx.AsyncClient] = None

    async def use(self, credentials: Credentials) -> None:
        """Call with these credentials from now on: the control token, and the issued client certificate (never the
        tenant's shared one, which is for the Brain only)."""
        self.credentials = credentials
        await self.aclose()

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            if self.transport is not None:
                self._http = httpx.AsyncClient(transport=self.transport, timeout=self.timeout)
            else:
                c = self.credentials
                cert = str(c.certificate) if c and c.certificate else None
                key = str(c.key) if c and c.certificate else None
                self._http = httpx.AsyncClient(verify=tls.context(self.ca_file, cert, key), timeout=self.timeout)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def send(self, method: str, path: str, *, json: Any = None, content: Optional[bytes] = None,
                   headers: Optional[dict] = None, auth: bool = True) -> tuple[int, Any]:
        """(status, JSON answer or None). Raises ``Unreachable`` when the Platform does not answer."""
        sent = {}
        if auth:
            if self.credentials is None:
                raise Refused(401, "not_enrolled")
            sent = {"Authorization": f"Bearer {self.credentials.control_token}", "X-Component": self.credentials.component}
        if content is not None:
            sent["Content-Type"] = "application/json"
        sent.update(headers or {})
        try:
            answer = await self._client().request(method, self.url + path, json=json, content=content, headers=sent,
                                                  follow_redirects=False)
        except httpx.HTTPError as exc:
            raise Unreachable(type(exc).__name__) from None
        try:
            data = answer.json()
        except ValueError:
            data = None
        return answer.status_code, data

    async def _call(self, method: str, path: str, body: Optional[dict] = None, *, auth: bool = True) -> dict:
        status, data = await self.send(method, path, json=body, auth=auth)
        if status >= 300 or not isinstance(data, dict):     # a redirect is not the Platform answering
            raise Refused(status, _code(data))
        return data

    # ------------------------------------------------------------------ protocol v1

    async def enrol(self, token: str, csr: str) -> dict:
        return await self._call("POST", "/enrol", {"token": token, "csr": csr, "version": VERSION,
                                                   "capabilities": CAPABILITIES, "kind": KIND}, auth=False)

    async def heartbeat(self, body: dict) -> dict:
        return await self._call("POST", "/heartbeat", body)

    async def config(self) -> dict:
        return await self._call("GET", "/config")

    async def package(self) -> dict:
        return await self._call("GET", "/package")

    async def command_done(self, command_id: str, result: str, *, component: Optional[str] = None) -> tuple[int, Any]:
        """A command's outcome; for a component's, its name in the body and in ``X-Sub-Component``."""
        body: dict = {"result": result}
        headers = {}
        if component:
            body["component"] = component
            headers["X-Sub-Component"] = component
        return await self.send("POST", f"/commands/{command_id}/done", json=body, headers=headers)

    async def report(self, kind: str, raw: bytes, *, component: str) -> tuple[int, Any]:
        """A component's run, event or check report, passed on unchanged."""
        assert kind in ("runs", "events", "checks")
        return await self.send("POST", f"/{kind}", content=raw, headers={"X-Sub-Component": component})
