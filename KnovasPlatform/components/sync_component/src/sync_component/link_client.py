"""Knovas Link's local API, the component's side (MVP-20; Link's ``contracts/local-api.md``).

In link mode the component talks to Link only, over plain HTTP on the site's own network: every call carries
``Authorization: Bearer <the token Link wrote to /link/token>`` and ``X-Component: <its name>``. Heartbeat, commands,
configuration, package and reports go through Link to the Platform; jobs come from Link's long-poll; a fetch's parts
and every job's result go back to Link. The component never calls the Platform or the Brain itself, holds no Knovas
certificate and opens no port.

``LinkClient`` answers to the Agent as ``PlatformClient`` does (heartbeat, config, package, report_run, command_done,
events, report_checks), so the loop is the same in both modes.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

import requests

from sync_component import VERSION
from sync_component.client import Refused

PREFIX = "/local/v1"
#: The longest wait Link's long-poll takes, in seconds.
WAIT_MAX = 25
#: The component's kind as the Platform knows it.
KIND = "sync_component"
# urllib3 logs every request's path at DEBUG; a relayed Brain path or a job's path may name a file (GI-DATA-02).
logging.getLogger("urllib3").setLevel(logging.WARNING)


class LinkClient:
    def __init__(self, url: str, name: str, token: str, *, session: Any = None, timeout: float = 60):
        self.url = url.rstrip("/") + PREFIX
        self.name = name
        self.timeout = timeout
        self._given = session
        self._local = threading.local()             # the heartbeat and the job threads each have their own session
        self.headers = {"Authorization": f"Bearer {token}", "X-Component": name}

    @property
    def session(self) -> Any:
        if self._given is not None:
            return self._given
        found = getattr(self._local, "session", None)
        if found is None:
            found = self._local.session = requests.Session()
            found.trust_env = False                 # Link is on the site's own network: no proxy sees the token
        return found

    def _call(self, method: str, path: str, body: Optional[dict] = None, *, data: Optional[bytes] = None,
              params: Optional[dict] = None, timeout: Optional[float] = None) -> Optional[dict]:
        headers = dict(self.headers)
        if data is not None:
            headers["Content-Type"] = "application/octet-stream"
        answer = self.session.request(method, self.url + path, json=body, data=data, params=params, headers=headers,
                                      timeout=timeout or self.timeout, allow_redirects=False)
        if answer.status_code == 204:
            return None
        try:
            parsed = answer.json()
        except ValueError:
            parsed = None
        if answer.status_code >= 300 or not isinstance(parsed, dict):
            code = parsed.get("error") if isinstance(parsed, dict) else None
            raise Refused(answer.status_code, str(code or "no_answer"))
        return parsed

    # ------------------------------------------------------------------ registration and the control plane

    def register(self, capabilities: list[str], job_kinds: list[str]) -> list[dict]:
        """The sources Link assigns to this component's name; no credential among them."""
        found = self._call("POST", "/register", {"component": self.name, "kind": KIND, "version": VERSION,
                                                 "capabilities": capabilities, "job_kinds": job_kinds})
        return list((found or {}).get("sources") or [])

    def heartbeat(self, *, state: str, lag_seconds: Optional[int], open_errors: int, capabilities: list[str],
                  sources: Optional[list] = None) -> dict:
        """``sources``: per source its sync status (KG-2), sent only when there is one; a Platform or a Knovas Link
        that does not know the field ignores it."""
        body = {"state": state, "version": VERSION, "lag_seconds": lag_seconds, "open_errors": open_errors,
                "capabilities": capabilities}
        if sources:
            body["sources"] = sources
        return self._call("POST", "/heartbeat", body) or {}

    def config(self) -> dict:
        return self._call("GET", "/config") or {}

    def package(self) -> dict:
        return self._call("GET", "/package") or {}

    def report_run(self, **report: Any) -> str:
        return str((self._call("POST", "/report/runs", report) or {}).get("id") or "")

    def command_done(self, command_id: str, result: str) -> None:
        self._call("POST", f"/commands/{command_id}/done", {"result": result})

    def events(self, events: list[dict]) -> None:
        self._call("POST", "/report/events", {"events": events})

    def report_checks(self, checks: list[dict]) -> None:
        self._call("POST", "/report/checks", {"checks": checks})

    # ------------------------------------------------------------------ the work channel

    def next_job(self, wait: int = WAIT_MAX) -> Optional[dict]:
        """The next job, waiting up to ``wait`` seconds; None when none came."""
        wait = max(0, min(int(wait), WAIT_MAX))
        found = self._call("GET", "/jobs", params={"wait": str(wait)}, timeout=wait + 30)
        job = (found or {}).get("job")
        return job if isinstance(job, dict) else None

    def post_part(self, job_id: str, n: int, data: bytes) -> None:
        self._call("POST", f"/jobs/{job_id}/part", data=data, params={"n": str(n)})

    def post_result(self, job_id: str, result: dict) -> None:
        self._call("POST", f"/jobs/{job_id}/result", result)


def read_token(path) -> str:
    """The token Link wrote for this component: one line, nothing else."""
    with open(path, encoding="ascii") as handle:
        token = handle.read().strip()
    if not token or any(c.isspace() for c in token):
        raise ValueError("not a token")
    return token


__all__ = ["LinkClient", "Refused", "read_token", "KIND"]
