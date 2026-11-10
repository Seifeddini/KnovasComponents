"""An in-process Knovas Link local API (``KnovasLink/contracts/local-api.md``) for the Jira connector's site runner: a
``requests``-like session that answers register, heartbeat, config, commands, checks, jobs and results, and keeps what
it was sent. A test helper only; the source and its settings are made up."""
from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import urlsplit

URL = "http://knovas-link.example.invalid:8460"
TOKEN = "t" * 43


class Answer:
    def __init__(self, status: int, body: Any = None):
        self.status_code, self._body = status, body
        self.content = b"" if body is None else json.dumps(body).encode()

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeLink:
    def __init__(self, sources: list[dict], *, name: str = "jira-connector", token: str = TOKEN):
        self.sources, self.name, self.token = sources, name, token
        self.registered: list[dict] = []
        self.beats: list[dict] = []
        self.checks: list[list] = []
        self.done: dict[str, str] = {}
        self.results: dict[str, dict] = {}
        self.jobs: list[dict] = []
        self.commands: list[dict] = []
        self.config_reads = 0
        self.headers_seen: list[dict] = []

    def request(self, method: str, url: str, *, json: Any = None, params: Optional[dict] = None,
                headers: Optional[dict] = None, timeout: Any = None, allow_redirects: bool = True, **_: Any) -> Answer:
        parts = urlsplit(url)
        if f"{parts.scheme}://{parts.netloc}" != URL or not parts.path.startswith("/local/v1/"):
            return Answer(404, {"error": "not_found"})
        headers = dict(headers or {})
        self.headers_seen.append(headers)
        if headers.get("X-Component") != self.name:
            return Answer(401, {"error": "unknown_component"})
        if headers.get("Authorization") != f"Bearer {self.token}":
            return Answer(401, {"error": "bad_token"})
        path = parts.path[len("/local/v1"):]
        if method == "POST" and path == "/register":
            if json.get("component") != self.name:
                return Answer(400, {"error": "bad_field"})
            self.registered.append(json)
            return Answer(200, {"sources": list(self.sources)})
        if method == "POST" and path == "/heartbeat":
            self.beats.append(json)
            commands, self.commands = self.commands, []
            return Answer(200, {"paused": False, "schedule": {"mode": "manual"}, "commands": commands})
        if method == "GET" and path == "/config":
            self.config_reads += 1
            return Answer(200, {"component": "c-1", "paused": False, "sources": list(self.sources), "mappings": []})
        if method == "POST" and path.startswith("/commands/") and path.endswith("/done"):
            self.done[path.split("/")[2]] = json["result"]
            return Answer(200, {"ok": True})
        if method == "POST" and path == "/report/checks":
            self.checks.append(json["checks"])
            return Answer(200, {"kept": len(json["checks"])})
        if method == "GET" and path == "/jobs":
            return Answer(200, {"job": self.jobs.pop(0)}) if self.jobs else Answer(204)
        if method == "POST" and path.startswith("/jobs/") and path.endswith("/result"):
            self.results[path.split("/")[2]] = json
            return Answer(200, {"accepted": True})
        return Answer(405, {"error": "method_not_allowed"})
