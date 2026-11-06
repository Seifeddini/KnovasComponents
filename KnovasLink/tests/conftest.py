"""In-process fakes of the Platform (connector protocol v1, the control plane) and of secure-api (the work channel and
the routes Link relays), each implementing the shared contract as the real side does, and a running Link between
them. The companies, repositories and sources here are made up."""
from __future__ import annotations

import base64
import contextlib
import datetime as dt
import json
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

LINK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LINK_ROOT / "src"))

from knovas_link.link import Link  # noqa: E402
from knovas_link.local_api import create_app  # noqa: E402
from knovas_link.settings import Settings  # noqa: E402

PLATFORM_URL = "https://platform.test/api/connectors/v1"
BRAIN_URL = "https://brain.test"
SITE_TOKEN = "kce_" + "s" * 40
LINK_ID = "11111111-2222-4333-8444-555555555555"
CONTROL = "kcc_" + "c" * 40
SERIAL = "4F2A"
#: The site's tenant as the Platform's enrolment answer names it (a made-up client id).
TENANT = "00000000-0000-4000-8000-000000000000"
MAX_PART = 4 * 1024 * 1024


def eventually(check, timeout: float = 5.0, every: float = 0.01):
    """Wait until ``check()`` is true (the fakes and Link run on the test client's own thread)."""
    until = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() > until:
            raise AssertionError("not reached in time")
        time.sleep(every)


def deadline_in(seconds: float) -> str:
    t = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_job(kind: str, source: Optional[str] = "handbook", *, seconds: float = 20, **args) -> dict:
    if kind == "fetch":
        args = {"external_id": "docs/intro.md", "max_bytes": 26214400, **args}
    elif kind == "write":
        args = {"mapping": "handbook-pages", "node_key": "page-1", "external_id": "docs/intro.md",
                "expected_version": "a" * 40, "facts": {"summary": "One sentence."}, "mode": "proposal", **args}
    job = {"id": str(uuid.uuid4()), "kind": kind, "source": source, "target_serial": None, "deadline": deadline_in(seconds),
           "actor": {"kind": "agent", "id": "agent-7", "on_behalf_of": {"kind": "user", "id": "user-3"}}, "args": args}
    if kind == "probe":
        job.pop("source")
    return job


# ------------------------------------------------------------------ a test CA that signs Link's request

class TestCA:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Brain CA")])
        now = dt.datetime.now(dt.timezone.utc)
        self.cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(self.key.public_key())
                     .serial_number(1).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
                     .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                     .sign(self.key, hashes.SHA256()))
        self.pem = self.cert.public_bytes(serialization.Encoding.PEM).decode()

    def sign(self, csr_pem: str) -> str:
        csr = x509.load_pem_x509_csr(csr_pem.encode())
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(csr.subject).issuer_name(self.cert.subject)
                .public_key(csr.public_key()).serial_number(int(SERIAL, 16))
                .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
                .sign(self.key, hashes.SHA256()))
        return cert.public_bytes(serialization.Encoding.PEM).decode()


CA = TestCA()


class Switch(httpx.AsyncBaseTransport):
    """An in-process transport that can be switched off: then every call fails as a network error does."""

    def __init__(self, app):
        self.inner = httpx.ASGITransport(app=app)
        self.down = False

    async def handle_async_request(self, request):
        if self.down:
            raise httpx.ConnectError("switched off")
        return await self.inner.handle_async_request(request)


# ------------------------------------------------------------------ the Platform

class FakePlatform:
    """Connector protocol v1 as builder/protocol.py answers it, plus the sub-component header Link adds."""

    def __init__(self):
        self.lock = threading.Lock()
        self.issue_certificate = True
        #: Changes to the enrolment answer, for a Platform that names no tenant or client CA.
        self.enrol_answer: dict = {}
        self.tokens = {SITE_TOKEN}
        self.enrolments: list[dict] = []
        self.heartbeats: list[dict] = []
        self.done: list[tuple] = []
        self.reports: list[tuple] = []
        self.commands: list[dict] = []
        self.open_commands: set = set()
        self.paused = False
        self.config = {
            "sources": [
                {"workspace": "ws-1", "key": "handbook", "kind": "github", "access": "read_write", "fetch": True,
                 "settings": {"repository": "example-org/handbook", "ref": "main"}, "component": "sync-component"},
                {"workspace": "ws-1", "key": "archive", "kind": "github", "access": "read",
                 "settings": {"repository": "example-org/archive", "ref": "main"}, "component": "sync-component"},
                {"workspace": "ws-2", "key": "shares", "kind": "knovas_connector", "access": "read", "fetch": True,
                 "settings": {}, "component": "knovas-connector"},
                {"workspace": "ws-2", "key": "crm", "kind": "http_json", "access": "read", "fetch": True,
                 "settings": {"url": "https://crm.example.test/export.json"}, "component": "records-reader"},
            ],
            "mappings": [{"workspace": "ws-1", "id": "handbook-pages", "source": "handbook"},
                         {"workspace": "ws-1", "id": "archive-notes", "source": "archive"},
                         {"workspace": "ws-2", "id": "share-docs", "source": "shares"}],
        }
        self.package = {"workspaces": [{"workspace": "ws-1", "files": {"mappings/a.yaml": base64.b64encode(b"a: 1").decode()}},
                                       {"workspace": "ws-2", "files": {"mappings/b.yaml": base64.b64encode(b"b: 2").decode()}}]}
        self.app = Starlette(routes=[
            Route("/api/connectors/v1/enrol", self.enrol, methods=["POST"]),
            Route("/api/connectors/v1/heartbeat", self.heartbeat, methods=["POST"]),
            Route("/api/connectors/v1/config", self.get_config, methods=["GET"]),
            Route("/api/connectors/v1/package", self.get_package, methods=["GET"]),
            Route("/api/connectors/v1/commands/{cid}/done", self.command_done, methods=["POST"]),
            Route("/api/connectors/v1/{kind:str}", self.report, methods=["POST"]),
        ])
        self.transport = Switch(self.app)

    def _auth(self, request: Request) -> Optional[Response]:
        if request.headers.get("x-component") != LINK_ID:
            return JSONResponse({"error": "unknown_component", "message": "x"}, status_code=401)
        if request.headers.get("authorization") != f"Bearer {CONTROL}":
            return JSONResponse({"error": "bad_token", "message": "x"}, status_code=401)
        return None

    async def enrol(self, request: Request):
        body = await request.json()
        with self.lock:
            self.enrolments.append(body)
            if body.get("token") not in self.tokens:
                return JSONResponse({"error": "bad_token", "message": "x"}, status_code=401)
            self.tokens.discard(body["token"])
        certificate = CA.sign(body["csr"]) if self.issue_certificate else None
        # As builder/protocol.py answers: the Platform's tenant and Knovas's client CA (S-4 M2).
        return JSONResponse({"component": LINK_ID, "control_token": CONTROL, "certificate": certificate,
                             "certificate_chain": [CA.pem] if certificate else None, "config": {},
                             "brain_url": BRAIN_URL, "brain_ca": CA.pem, "tenant": TENANT, "client_ca": CA.pem,
                             **self.enrol_answer}, status_code=201)

    async def heartbeat(self, request: Request):
        if (refused := self._auth(request)) is not None:
            return refused
        body = await request.json()
        if body.get("state") not in ("online", "degraded"):
            return JSONResponse({"error": "bad_field", "message": "x"}, status_code=400)
        with self.lock:
            self.heartbeats.append(body)
            commands, self.commands = self.commands, []
            self.open_commands |= {c["id"] for c in commands}
        return JSONResponse({"paused": self.paused, "schedule": {"mode": "interval", "every_minutes": 60},
                             "commands": commands})

    async def get_config(self, request: Request):
        if (refused := self._auth(request)) is not None:
            return refused
        return JSONResponse({"component": LINK_ID, "paused": self.paused, "schedule": {"mode": "manual"},
                             "brain_url": BRAIN_URL, **self.config})

    async def get_package(self, request: Request):
        if (refused := self._auth(request)) is not None:
            return refused
        return JSONResponse(self.package)

    async def command_done(self, request: Request):
        if (refused := self._auth(request)) is not None:
            return refused
        body = await request.json()
        cid = request.path_params["cid"]
        with self.lock:
            self.done.append((cid, body, request.headers.get("x-sub-component")))
            if cid not in self.open_commands:
                return JSONResponse({"error": "unknown_command", "message": "x"}, status_code=404)
            self.open_commands.discard(cid)
        return JSONResponse({"ok": True})

    async def report(self, request: Request):
        if (refused := self._auth(request)) is not None:
            return refused
        kind = request.path_params["kind"]
        if kind not in ("runs", "events", "checks"):
            return JSONResponse({"error": "not_found"}, status_code=404)
        raw = await request.body()
        with self.lock:
            self.reports.append((kind, raw, request.headers.get("x-sub-component")))
        return JSONResponse({"id": "run-1"} if kind == "runs" else {"stored": 1}, status_code=201)


# ------------------------------------------------------------------ secure-api

class FakeSecureApi:
    """The work channel as knovas-software's services/connector_jobs.py answers it (envelope, codes, lease, the
    result rules), and an echo of the routes Link relays."""

    def __init__(self):
        self.lock = threading.Lock()
        self.queued: list[dict] = []
        self.jobs: dict[str, dict] = {}
        self.claims: list[dict] = []
        self.relayed: list[dict] = []
        self.busy = False
        self.part_status: Optional[int] = None
        self.app = Starlette(routes=[
            Route("/secured/connector/jobs", self.claim, methods=["GET"]),
            Route("/secured/connector/jobs/{jid}/part", self.part, methods=["POST"]),
            Route("/secured/connector/jobs/{jid}/result", self.result, methods=["POST"]),
            Route("/secured/connector/probe", self.probe, methods=["POST"]),
            Route("/{path:path}", self.echo, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]),
        ])
        self.transport = Switch(self.app)

    @staticmethod
    def _error(status: int, code: str, **headers) -> JSONResponse:
        return JSONResponse({"status": "error", "error": "x", "error_code": code}, status_code=status, headers=headers)

    def enqueue(self, job: dict) -> dict:
        with self.lock:
            self.queued.append(job)
            self.jobs[job["id"]] = {"job": job, "parts": [], "result": None, "state": "queued", "lease": None,
                                    "leases": [], "refused": []}
        return job

    def claim_now(self, job: dict) -> dict:
        """Enqueue and claim at once, as the long-poll would: the job with its lease."""
        self.enqueue(job)
        with self.lock:
            self.queued.remove(job)
            entry = self.jobs[job["id"]]
            entry["state"], entry["lease"] = "claimed", "lease-" + uuid.uuid4().hex
            entry["leases"].append(entry["lease"])
            return {**job, "lease": entry["lease"]}

    def lease_runs_out(self, jid: str, *, claimed_by_another: bool = False) -> None:
        """The claim's lease ends: the job is claimable again, or another claimer took it under a new lease."""
        with self.lock:
            entry = self.jobs[jid]
            if claimed_by_another:
                entry["lease"] = "lease-other-" + uuid.uuid4().hex
                entry["leases"].append(entry["lease"])
            else:
                entry["state"], entry["lease"] = "queued", None
                self.queued.append(entry["job"])

    def _lease_held(self, request: Request, entry: dict) -> bool:
        if entry["state"] == "claimed" and request.headers.get("x-knovas-lease") == entry["lease"]:
            return True
        entry["refused"].append(request.url.path.rsplit("/", 1)[-1])
        return False

    def outcome(self, jid: str) -> Optional[dict]:
        with self.lock:
            return self.jobs[jid]["result"]

    def parts(self, jid: str) -> list:
        with self.lock:
            return list(self.jobs[jid]["parts"])

    async def claim(self, request: Request):
        import asyncio

        if self.busy:
            return self._error(429, "waiters_exhausted", **{"Retry-After": "1"})
        sources = [s for s in (request.query_params.get("source") or "").split(",") if s]
        wait = min(int(request.query_params.get("wait") or 0), 25)
        with self.lock:
            self.claims.append({"sources": sources, "wait": wait})
        until = time.monotonic() + min(wait, 0.2)
        while True:
            with self.lock:
                for job in self.queued:
                    if (job["kind"] == "probe" and job.get("target_serial") == SERIAL) or job.get("source") in sources:
                        self.queued.remove(job)
                        entry = self.jobs[job["id"]]
                        entry["state"] = "claimed"
                        entry["lease"] = "lease-" + uuid.uuid4().hex          # fresh per claim
                        entry["leases"].append(entry["lease"])
                        return JSONResponse({"status": "success", "message": "Job claimed",
                                             "job": {**job, "lease": entry["lease"]}})
            if time.monotonic() >= until:
                return Response(status_code=204)
            await asyncio.sleep(0.01)

    async def part(self, request: Request):
        jid = request.path_params["jid"]
        data = await request.body()
        with self.lock:
            entry = self.jobs.get(jid)
            if entry is None:
                return self._error(404, "unknown_job")
            if not self._lease_held(request, entry):
                return self._error(409, "lease_lost")
            if entry["job"]["kind"] != "fetch":
                return self._error(409, "parts_not_expected")
            n = request.query_params.get("n") or ""
            if not n.isdigit() or int(n) != len(entry["parts"]):
                return self._error(400, "invalid_part")
            if len(data) > MAX_PART:
                return self._error(413, "part_too_large")
            if self.part_status:
                return self._error(self.part_status, "x")
            entry["parts"].append(data)
        return JSONResponse({"status": "success", "n": int(n), "delivered": True}, status_code=202)

    async def result(self, request: Request):
        jid = request.path_params["jid"]
        body = await request.json()
        with self.lock:
            entry = self.jobs.get(jid)
            if entry is None:
                return self._error(404, "unknown_job")
            if not self._lease_held(request, entry):
                return self._error(409, "lease_lost")
            status = body.get("status")
            if status not in ("ok", "refused", "conflict", "unsupported", "unreachable", "failed"):
                return self._error(400, "invalid_result")
            if status != "ok" and not body.get("code"):
                return self._error(400, "invalid_result")
            if entry["job"]["kind"] == "fetch" and status == "ok" and any(
                    body.get(k) is None for k in ("version", "bytes_sha256", "bytes", "parts")):
                return self._error(400, "invalid_result")
            entry["result"] = body
            entry["state"] = "done"
        return JSONResponse({"status": "success", "message": "Result recorded", "job_id": jid, "state": "done"})

    async def probe(self, request: Request):
        job = make_job("probe")
        job["target_serial"] = SERIAL
        job["actor"] = {"kind": "system", "id": "undeclared", "on_behalf_of": None}
        self.enqueue(job)
        return JSONResponse({"status": "success", "message": "Probe queued", "job_id": job["id"]}, status_code=201)

    async def echo(self, request: Request):
        body = await request.body()
        seen = {"method": request.method, "path": request.url.path, "raw_path": request.scope.get("raw_path", b"").decode(),
                "query": request.url.query, "body": body.decode("utf-8", "replace"),
                "headers": {k: v for k, v in request.headers.items()}}
        with self.lock:
            self.relayed.append(seen)
        if request.url.path.endswith("/missing"):
            return self._error(404, "not_found")
        if request.url.path.endswith("/flaky"):
            return self._error(503, "admission_rejected", **{"Retry-After": "2"})
        return JSONResponse({"echo": {k: seen[k] for k in ("method", "path", "query", "body")}}, status_code=200,
                            headers={"X-Brain-Answer": "yes"})


class World:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.platform = FakePlatform()
        self.secure = FakeSecureApi()
        self.tokens_dir = tmp / "link"
        self.data_dir = tmp / "data"

    def settings(self, **overrides) -> Settings:
        values = {"platform_url": PLATFORM_URL, "enrol_token": SITE_TOKEN, "data_dir": self.data_dir,
                  "tokens_dir": self.tokens_dir, "heartbeat_seconds": 3600, "probe_seconds": 3600}
        values.update(overrides)
        return Settings(**values)

    def link(self, *, loops=("channel",), **overrides) -> Link:
        link = Link(self.settings(**overrides), platform_transport=self.platform.transport,
                    brain_transport=self.secure.transport, loops=loops, key_size=2048)
        link.channel.backoff_base = 0.05
        return link

    @contextlib.contextmanager
    def running(self, *, loops=("channel",), **overrides):
        link = self.link(loops=loops, **overrides)
        with TestClient(create_app(link), base_url="http://knovas-link:8460") as client:
            yield client, link

    def token(self, name: str) -> str:
        return (self.tokens_dir / name / "token").read_text().strip()

    def headers(self, name: str = "sync-component") -> dict:
        return {"Authorization": f"Bearer {self.token(name)}", "X-Component": name}


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def register(world: World, client: TestClient, name: str = "sync-component", *, kind: str = "sync_component",
             job_kinds: Any = ("fetch", "write", "probe"), capabilities: Any = ("dry_run", "apply", "write:github")):
    answer = client.post("/local/v1/register", headers=world.headers(name),
                         json={"component": name, "kind": kind, "version": "0.2.0", "capabilities": list(capabilities),
                               "job_kinds": list(job_kinds)})
    assert answer.status_code == 200, answer.text
    return answer.json()


def as_json(raw: bytes) -> Any:
    return json.loads(raw)
