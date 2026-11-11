"""The local API (MVP-16, MVP-17, MVP-18): every endpoint of ``contracts/local-api.md``.

It listens only on the site's internal Docker network. Every call carries ``Authorization: Bearer <token>`` (the
token Link wrote to ``/link/<component>/token``) and ``X-Component: <name>``; without them, or with a token that is
not that name's, the answer is 401 ``bad_token`` or ``unknown_component``. Errors are ``{"error", "message"}``; a
message is English and never holds a name or a value.

Long-polls and streamed bodies run on the event loop, so a waiting component holds no thread.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
from typing import Any

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from knovas_link import VERSION, contracts, relay, sync_status
from knovas_link.brain_client import BrainUnreachable
from knovas_link.errors import LocalError
from knovas_link.link import Link

log = logging.getLogger("knovas_link.local_api")

PREFIX = "/local/v1"
#: A JSON body of the local API (register, heartbeat, done, result, reports): the Platform's own limit.
MAX_JSON_BYTES = 256 * 1024
WAIT_MAX = 25
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,59}$")
_KIND = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_.:-]{0,60}$")
_COMMAND_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_RELAY_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def _error(status: int, code: str, message: str = "") -> JSONResponse:
    return JSONResponse({"error": code, "message": message or code}, status_code=status)


def _platform_answer(status: int, data: Any) -> Response:
    """The Platform's answer, as it gave it."""
    if data is None:
        return Response(status_code=status)
    return JSONResponse(data, status_code=status)


async def _read(request: Request, limit: int = MAX_JSON_BYTES) -> bytes:
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > limit:
            raise LocalError(400, "bad_field", "body too large")
    return bytes(raw)


async def _json(request: Request) -> tuple[dict, bytes]:
    raw = await _read(request)
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise LocalError(400, "bad_field", "a JSON object")
    return body, raw


def _strings(value: Any, pattern: re.Pattern, what: str, limit: int = 30) -> list:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit or not all(isinstance(x, str) and pattern.match(x) for x in value):
        raise LocalError(400, "bad_field", f"{what}: a list of codes")
    return value


def create_app(link: Link) -> Any:
    def who(request: Request) -> str:
        auth = request.headers.get("authorization", "")
        bearer = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        return link.registry.check(request.headers.get("x-component", ""), bearer)

    # ------------------------------------------------------------------ registration and control plane

    async def register(request: Request) -> Response:
        name = who(request)
        body, _ = await _json(request)
        if body.get("component") != name:
            raise LocalError(400, "bad_field", "component: the name in X-Component")
        kind, version = body.get("kind"), body.get("version")
        if not isinstance(kind, str) or not _KIND.match(kind):
            raise LocalError(400, "bad_field", "kind: the component's kind")
        if not isinstance(version, str) or not _VERSION.match(version):
            raise LocalError(400, "bad_field", "version: a version")
        capabilities = _strings(body.get("capabilities"), _CAPABILITY, "capabilities")
        job_kinds = _strings(body.get("job_kinds"), _CAPABILITY, "job_kinds", limit=10)
        declaration = None
        if link.v2 is not None and body.get("declaration") is not None:      # protocol 2.0 (draft), behind its switch
            declaration = body["declaration"]
            if not contracts.v2_valid("declaration.schema.json", declaration):
                raise LocalError(400, "bad_field", "declaration: as declaration.schema.json has it")
        link.registry.register(name, kind=kind, version=version, capabilities=capabilities, job_kinds=job_kinds,
                               declaration=declaration)
        if declaration is not None:
            link.channel.spawn(link.v2.declare_site())
        log.info("component.registered, job kinds %d", len(job_kinds))
        return JSONResponse({"sources": await link.sources_for(name)})

    async def heartbeat(request: Request) -> Response:
        name = who(request)
        body, _ = await _json(request)
        state = body.get("state") or "online"
        if state not in ("online", "degraded"):
            raise LocalError(400, "bad_field", "state: online or degraded")
        version = body.get("version")
        if version is not None and (not isinstance(version, str) or not _VERSION.match(version)):
            raise LocalError(400, "bad_field", "version: a version")
        for field, maximum in (("lag_seconds", 10 ** 9), ("open_errors", 100000)):
            value = body.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum):
                raise LocalError(400, "bad_field", f"{field}: a whole number")
        _strings(body.get("capabilities"), _CAPABILITY, "capabilities")
        synced = sync_status.check(body.get("sources"))           # protocol 1.2: checked before anything is kept
        link.registry.beat(name, state=state, version=version)
        if synced:
            link.note_sync(name, synced)
        commands = link.commands.deliver(name)
        return JSONResponse({"paused": link.paused_for(name), "schedule": link.schedule_for(name), "commands": commands})

    async def command_done(request: Request) -> Response:
        name = who(request)
        cid = request.path_params["command_id"]
        body, _ = await _json(request)
        result = body.get("result")
        if result not in ("ok", "failed", "refused"):
            raise LocalError(400, "bad_field", "result: ok|failed|refused")
        if not _COMMAND_ID.match(cid) or link.commands.owner(cid) != name:
            raise LocalError(404, "unknown_command", "no open command of that id was delivered to this component")
        try:
            status, data = await link.platform.command_done(cid, result, component=name)
        except Exception:                                   # noqa: BLE001 - Unreachable or not enrolled
            raise LocalError(503, "unreachable", "the Platform did not answer") from None
        link.commands.open.pop(cid, None)
        return _platform_answer(status, data)

    async def config(request: Request) -> Response:
        name = who(request)
        return JSONResponse(await link.config_for(name, str(request.base_url).rstrip("/") + PREFIX + "/brain"))

    async def package(request: Request) -> Response:
        name = who(request)
        return JSONResponse(await link.package_for(name))

    async def ping(request: Request) -> Response:
        who(request)
        return JSONResponse({"version": VERSION, "certificate": link.certificate, "work_channel": link.channel.state})

    # ------------------------------------------------------------------ the work channel (MVP-18)

    async def jobs(request: Request) -> Response:
        name = who(request)
        raw = request.query_params.get("wait")
        if raw is None:
            wait = WAIT_MAX
        elif re.fullmatch(r"[0-9]{1,9}", raw):
            wait = min(int(raw), WAIT_MAX)
        else:
            raise LocalError(400, "bad_field", "wait: whole seconds from 0 to 25")
        job = await link.channel.next_job(name, wait)
        if job is None:
            return Response(status_code=204)
        return JSONResponse({"job": job})

    async def part(request: Request) -> Response:
        name = who(request)
        await link.channel.part(name, request.path_params["job_id"], request.query_params.get("n"),
                                request.headers.get("content-length"), request.stream().__aiter__())
        return JSONResponse({"accepted": True})

    async def result(request: Request) -> Response:
        name = who(request)
        jid = request.path_params["job_id"]
        link.channel.mine(name, jid)                         # 409 before reading a body that cannot count
        raw = await _read(request)
        await link.channel.finish(name, jid, raw)
        return JSONResponse({"accepted": True})

    # ------------------------------------------------------------------ the relays (MVP-17)

    async def brain(request: Request) -> Response:
        who(request)
        raw_path = request.scope.get("raw_path") or request.url.path.encode("latin-1")
        raw_path = raw_path.split(b"?", 1)[0].decode("latin-1")
        rest = raw_path[len(PREFIX + "/brain/"):] if raw_path.startswith(PREFIX + "/brain/") else ""
        path = relay.relayed_path(rest)
        if path is None:
            log.info("relay.not_relayed")
            raise LocalError(403, "not_relayed", "this Brain route is not relayed")
        if not link.certificate_present:
            raise LocalError(503, "waiting_for_certificate", "Knovas Link has no certificate yet")
        if link.brain is None:
            raise LocalError(503, "unreachable", "Knovas Link has no Brain address yet")
        headers = {k: request.headers[k] for k in relay.REQUEST_HEADERS if k in request.headers}
        has_body = request.headers.get("content-length", "0") not in ("", "0") or "transfer-encoding" in request.headers
        try:
            upstream = await link.brain.relay(request.method, path, request.scope.get("query_string", b""), headers,
                                              request.stream() if has_body else None)
        except BrainUnreachable:
            raise LocalError(503, "unreachable", "secure-api did not answer") from None
        log.info("relay.answered %d", upstream.status_code)
        return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
                                 headers=relay.response_headers(upstream.headers), background=BackgroundTask(upstream.aclose))

    async def report(request: Request) -> Response:
        name = who(request)
        kind = request.path_params["kind"]
        body, raw = await _json(request)
        relay.check_report(kind, body)
        link.note_report(name, kind, body)
        try:
            status, data = await link.platform.report(kind, raw, component=name)
        except Exception:                                   # noqa: BLE001 - Unreachable or not enrolled
            raise LocalError(503, "unreachable", "the Platform did not answer") from None
        return _platform_answer(status, data)

    # ------------------------------------------------------------------ the app

    async def local_error(request: Request, exc: LocalError) -> Response:
        return _error(exc.status, exc.code, exc.message)

    async def http_error(request: Request, exc: HTTPException) -> Response:
        return _error(exc.status_code, "not_found" if exc.status_code == 404 else "bad_request")

    @contextlib.asynccontextmanager
    async def lifespan(app):
        await link.start()
        try:
            yield
        finally:
            await link.stop()

    routes = [
        Route(PREFIX + "/register", register, methods=["POST"]),
        Route(PREFIX + "/heartbeat", heartbeat, methods=["POST"]),
        Route(PREFIX + "/commands/{command_id}/done", command_done, methods=["POST"]),
        Route(PREFIX + "/config", config, methods=["GET"]),
        Route(PREFIX + "/package", package, methods=["GET"]),
        Route(PREFIX + "/ping", ping, methods=["GET"]),
        Route(PREFIX + "/jobs", jobs, methods=["GET"]),
        Route(PREFIX + "/jobs/{job_id}/part", part, methods=["POST"]),
        Route(PREFIX + "/jobs/{job_id}/result", result, methods=["POST"]),
        Route(PREFIX + "/brain", brain, methods=_RELAY_METHODS),
        Route(PREFIX + "/brain/{path:path}", brain, methods=_RELAY_METHODS),
        Route(PREFIX + "/report/{kind}", report, methods=["POST"]),
    ]
    app = Starlette(routes=routes, lifespan=lifespan,
                    exception_handlers={LocalError: local_error, HTTPException: http_error})
    return Guard(app)


class Guard:
    """The outermost layer: an exception nobody handled is answered 500 and logged by its type only, so that no
    path, name or value reaches a log line through a traceback."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def sending(message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, sending)
        except Exception as exc:                              # noqa: BLE001
            log.error("request.failed (%s)", type(exc).__name__)
            if not started:
                body = json.dumps({"error": "internal", "message": "internal"}).encode()
                await send({"type": "http.response.start", "status": 500,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
