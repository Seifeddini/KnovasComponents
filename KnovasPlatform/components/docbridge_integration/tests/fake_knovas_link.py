"""An in-process Knovas Link: its local API as ``KnovasLink/contracts/local-api.md`` has it, for the sync component's
link-mode tests (MVP-20 … MVP-22). A test helper only.

It checks what Link checks, with Link's own code and the shared contract where it can: the two headers of every call,
reports with ``knovas_link.relay.check_report`` (codes and counts only), the Brain relay's allow-list with
``knovas_link.relay.relayed_path``, every job result against ``job-result.schema.json``, and parts numbered from 0,
non-empty and at most 4 MiB. Behind the relay stands an empty tenant (no node types, nodes or edges). Everything a
component sends is recorded for the tests to look at.
"""
from __future__ import annotations

import base64
import json
import sys
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, Response, jsonify, request
from jsonschema import Draft202012Validator
from werkzeug.serving import make_server

LINK = Path(__file__).resolve().parents[4] / "KnovasLink"
CONTRACTS = LINK / "contracts"
sys.path.insert(0, str(LINK / "src"))

from knovas_link import relay as LINK_RELAY  # noqa: E402
from knovas_link.errors import LocalError  # noqa: E402

PART_MAX = 4 * 1024 * 1024
RESULT_SCHEMA = Draft202012Validator(json.loads((CONTRACTS / "job-result.schema.json").read_text(encoding="utf-8")))
JOB_SCHEMA = Draft202012Validator(json.loads((CONTRACTS / "job.schema.json").read_text(encoding="utf-8")))


def files_of(folder: Path) -> dict[str, str]:
    """A configuration folder as a package's files: path → base64 of the bytes."""
    return {p.relative_to(folder).as_posix(): base64.b64encode(p.read_bytes()).decode("ascii")
            for p in sorted(folder.rglob("*")) if p.is_file()}


class FakeLink:
    def __init__(self, *, tokens: dict[str, str], sources: list[dict], mappings: list[dict], package: dict,
                 paused: bool = False, schedule: dict | None = None):
        self.tokens = dict(tokens)
        self.sources = sources                    # each with "component": the name it is assigned to
        self.mappings = mappings
        self.package = package                    # {"workspaces": [{"workspace", "files"}]}
        self.paused = paused
        self.schedule = schedule or {"mode": "manual"}
        self.has_config = True
        self.registered: dict[str, dict] = {}
        self.beats: list[dict] = []
        self.commands: dict[str, list] = {}
        self.delivered: dict[str, str] = {}
        self.done: list[tuple[str, str, str]] = []
        self.reports: list[tuple[str, str, dict]] = []
        self.brain_calls: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self.jobs: dict[str, list] = {}
        self.handed: dict[str, str] = {}
        self.parts: dict[str, list[bytes]] = {}
        self.results: dict[str, dict] = {}
        self.refused_results: list[tuple[str, dict]] = []
        self.lease_lost_after: dict[str, int] = {}      # job id → parts taken before 409 lease_lost
        self.cond = threading.Condition()
        self.app = self._app()

    # ------------------------------------------------------------------ the test's side

    def queue(self, component: str, job: dict) -> str:
        assert not list(JOB_SCHEMA.iter_errors(job)), "the test's job follows job.schema.json"
        with self.cond:
            self.jobs.setdefault(component, []).append({k: v for k, v in job.items() if k != "lease"})
            self.cond.notify_all()
        return job["id"]

    def command(self, component: str, name: str, args: dict | None = None) -> str:
        cid = "c" + uuid.uuid4().hex[:12]
        self.commands.setdefault(component, []).append({"id": cid, "command": name, "args": args or {}})
        return cid

    def data(self, jid: str) -> bytes:
        return b"".join(self.parts.get(jid, []))

    # ------------------------------------------------------------------ the local API

    def _app(self) -> Flask:
        app = Flask("fake_knovas_link")
        link = self

        def error(status: int, code: str):
            return jsonify({"error": code, "message": code}), status

        def who() -> str:
            name = request.headers.get("X-Component", "")
            auth = request.headers.get("Authorization", "")
            if name not in link.tokens:
                raise LocalError(401, "unknown_component", "")
            if auth != f"Bearer {link.tokens[name]}":
                raise LocalError(401, "bad_token", "")
            link.calls.append((request.method, request.path))
            return name

        def mine(name: str) -> list[dict]:
            return [{k: s[k] for k in ("workspace", "key", "kind", "access", "settings", "fetch")}
                    for s in link.sources if s.get("component") == name]

        @app.errorhandler(LocalError)
        def local_error(exc: LocalError):
            return error(exc.status, exc.code)

        @app.post("/local/v1/register")
        def register():
            name = who()
            body = request.get_json(silent=True) or {}
            if body.get("component") != name or not body.get("kind") or not body.get("version"):
                return error(400, "bad_field")
            if not link.has_config:
                return error(503, "unreachable")
            link.registered[name] = body
            return jsonify({"sources": mine(name)})

        @app.post("/local/v1/heartbeat")
        def heartbeat():
            name = who()
            body = request.get_json(silent=True) or {}
            if body.get("state", "online") not in ("online", "degraded"):
                return error(400, "bad_field")
            link.beats.append({"component": name, **body})
            out = link.commands.pop(name, [])
            for c in out:
                link.delivered[c["id"]] = name
            return jsonify({"paused": link.paused, "schedule": link.schedule, "commands": out})

        @app.post("/local/v1/commands/<cid>/done")
        def command_done(cid):
            name = who()
            result = (request.get_json(silent=True) or {}).get("result")
            if result not in ("ok", "failed", "refused"):
                return error(400, "bad_field")
            if link.delivered.get(cid) != name:
                return error(404, "unknown_command")
            link.done.append((name, cid, result))
            return jsonify({"ok": True})

        @app.get("/local/v1/config")
        def config():
            name = who()
            keys = {(s["workspace"], s["key"]) for s in mine(name)}
            return jsonify({"component": name, "paused": link.paused, "schedule": link.schedule,
                            "brain_url": request.host_url.rstrip("/") + "/local/v1/brain", "sources": mine(name),
                            "mappings": [m for m in link.mappings if (m.get("workspace"), m.get("source")) in keys]})

        @app.get("/local/v1/package")
        def package():
            name = who()
            workspaces = {s["workspace"] for s in mine(name)}
            return jsonify({"workspaces": [w for w in link.package["workspaces"] if w["workspace"] in workspaces]})

        @app.get("/local/v1/ping")
        def ping():
            who()
            return jsonify({"version": "1.1.0", "certificate": "present", "work_channel": "connected"})

        @app.get("/local/v1/jobs")
        def jobs():
            name = who()
            raw = request.args.get("wait", "25")
            if not raw.isdigit():
                return error(400, "bad_field")
            wait = min(int(raw), 25)
            kinds = set((link.registered.get(name) or {}).get("job_kinds") or [])
            with link.cond:
                link.cond.wait_for(lambda: any(j["kind"] in kinds for j in link.jobs.get(name, [])), timeout=wait)
                for j in list(link.jobs.get(name, [])):
                    if j["kind"] in kinds:
                        link.jobs[name].remove(j)
                        link.handed[j["id"]] = name
                        link.parts.setdefault(j["id"], [])
                        return jsonify({"job": j})
            return Response(status=204)

        def held(name: str, jid: str) -> bool:
            return link.handed.get(jid) == name and jid not in link.results

        @app.post("/local/v1/jobs/<jid>/part")
        def part(jid):
            name = who()
            if not held(name, jid):
                return error(409, "lease_lost")
            n, body = request.args.get("n"), request.get_data()
            got = link.parts[jid]
            if jid in link.lease_lost_after and len(got) >= link.lease_lost_after[jid]:
                link.results[jid] = {"status": "unreachable", "code": "unreachable", "duration_ms": 0}
                return error(409, "lease_lost")
            if request.content_type != "application/octet-stream":
                return error(400, "bad_field")
            if len(body) > PART_MAX:
                return error(413, "bad_field")
            if n is None or not n.isdigit() or int(n) != len(got) or not body:
                return error(400, "bad_field")
            got.append(body)
            return jsonify({"accepted": True})

        @app.post("/local/v1/jobs/<jid>/result")
        def result(jid):
            name = who()
            if not held(name, jid):
                return error(409, "lease_lost")
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or list(RESULT_SCHEMA.iter_errors(body)):
                link.refused_results.append((jid, body))
                return error(400, "bad_field")
            link.results[jid] = body
            return jsonify({"accepted": True})

        @app.route("/local/v1/brain/<path:rest>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
        def brain(rest):
            name = who()
            raw = request.environ.get("RAW_URI", request.full_path).split("?", 1)[0]
            path = LINK_RELAY.relayed_path(raw[len("/local/v1/brain/"):])
            link.brain_calls.append({"component": name, "method": request.method, "path": path,
                                     "authorization": request.headers.get("Authorization"),
                                     "client_certificate": request.environ.get("SSL_CLIENT_CERT")})
            if path is None:
                return error(403, "not_relayed")
            # brain-runner 0.3 also reads the document list (with source references) and the folder rules
            empty = {"secured/graph/node-types": {"node_types": []}, "secured/graph/nodes": {"nodes": []},
                     "secured/graph/edges": {"edges": []}, "secured/documents": {"documents": [], "next_after": None},
                     "secured/folder_rules": {"rules": []}}
            if request.method == "GET" and path in empty:
                return jsonify(empty[path])
            return jsonify({"error_code": "not_in_this_fake"}), 404

        @app.post("/local/v1/report/<kind>")
        def report(kind):
            name = who()
            body = request.get_json(silent=True)
            LINK_RELAY.check_report(kind, body)
            link.reports.append((name, kind, body))
            return jsonify({"id": "r" + uuid.uuid4().hex[:8]} if kind == "runs" else {"ok": True})

        return app

    # ------------------------------------------------------------------ serving

    def serve(self) -> SimpleNamespace:
        server = make_server("127.0.0.1", 0, self.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        return SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", server=server, thread=thread)


def stop(served: SimpleNamespace) -> None:
    served.server.shutdown()
    served.thread.join(5)
