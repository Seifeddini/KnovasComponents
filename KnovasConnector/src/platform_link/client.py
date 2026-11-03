"""Connector protocol v1, the connector's side: it calls out to the Platform; the Platform never calls in."""
from __future__ import annotations

from typing import Any, Optional

import requests

from platform_link.identity import Enrolled

VERSION = "connector-1"


class Refused(RuntimeError):
    """The Platform answered with an error; ``code`` is the protocol's, for logs and events."""

    def __init__(self, status: int, code: str):
        super().__init__(f"{status} {code}")
        self.status, self.code = status, code


class PlatformClient:
    def __init__(self, url: str, *, ca_file: Optional[str] = None, session: Any = None, timeout: float = 60):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        if ca_file:
            self.session.verify = ca_file
        self.enrolled: Optional[Enrolled] = None

    def use(self, enrolled: Enrolled) -> None:
        self.enrolled = enrolled
        self.session.cert = (str(enrolled.certificate), str(enrolled.key))

    def _call(self, method: str, path: str, body: Optional[dict] = None, *, auth: bool = True) -> dict:
        headers = {}
        if auth:
            if self.enrolled is None:
                raise Refused(401, "not_enrolled")
            headers = {"Authorization": f"Bearer {self.enrolled.control_token}", "X-Component": self.enrolled.component}
        answer = self.session.request(method, self.url + path, json=body, headers=headers, timeout=self.timeout,
                                      allow_redirects=False)
        try:
            data = answer.json()
        except ValueError:
            data = {}
        if answer.status_code >= 400 or not isinstance(data, dict):
            raise Refused(answer.status_code, str(data.get("error") or "no_answer") if isinstance(data, dict) else "no_answer")
        return data

    def enrol(self, token: str, csr: str, capabilities: list[str]) -> dict:
        return self._call("POST", "/enrol", {"token": token, "csr": csr, "version": VERSION, "capabilities": capabilities},
                          auth=False)

    def heartbeat(self, *, state: str, lag_seconds: Optional[int], open_errors: int, capabilities: list[str]) -> dict:
        return self._call("POST", "/heartbeat", {"state": state, "version": VERSION, "lag_seconds": lag_seconds,
                                                 "open_errors": open_errors, "capabilities": capabilities})

    def config(self) -> dict:
        return self._call("GET", "/config")

    def report_run(self, **report: Any) -> str:
        return str(self._call("POST", "/runs", report).get("id") or "")

    def command_done(self, command_id: str, result: str) -> None:
        self._call("POST", f"/commands/{command_id}/done", {"result": result})

    def events(self, events: list[dict]) -> None:
        self._call("POST", "/events", {"events": events})

    def report_checks(self, checks: list[dict]) -> None:
        self._call("POST", "/checks", {"checks": checks})
