"""A fake Knovas Platform over real HTTP for the SDK tests.

The client is tested against sockets, not mocks of urllib: redirects, TLS,
timeouts and header handling are exactly what a mock would get wrong. Kept
out of conftest.py so that test modules can import it under a name no other
test suite in the repository uses.
"""

from __future__ import annotations

import json
import ssl
import threading
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

TOKEN = "kxp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v"


@dataclass
class Recorded:
    method: str
    path: str
    query: Dict[str, List[str]]
    headers: Dict[str, str]
    raw: bytes

    @property
    def json(self) -> Any:
        return json.loads(self.raw.decode("utf-8")) if self.raw else None


@dataclass
class Reply:
    status: int = 200
    payload: Any = None
    raw: Optional[bytes] = None
    headers: Dict[str, str] = field(default_factory=dict)
    delay: float = 0.0


class FakePlatform:
    """Scripted answers per (method, path); every request is recorded."""

    def __init__(self) -> None:
        self.requests: List[Recorded] = []
        self._routes: Dict[Tuple[str, str], List[Any]] = {}
        self._lock = threading.Lock()
        self.url = ""
        self.release = threading.Event()

    def on(self, method: str, path: str, *replies: Any) -> None:
        """Queue replies; the last one repeats. A reply may be a callable(Recorded)."""
        self._routes[(method, path)] = list(replies)

    def ok(self, method: str, path: str, key: str, value: Any, status: int = 200) -> None:
        self.on(method, path, Reply(status, {"success": True, key: value}))

    def next_reply(self, recorded: Recorded) -> Reply:
        with self._lock:
            self.requests.append(recorded)
            queue = self._routes.get((recorded.method, recorded.path))
            if not queue:
                return Reply(404, {"success": False, "error": "Nicht gefunden."})
            reply = queue[0] if len(queue) == 1 else queue.pop(0)
        return reply(recorded) if callable(reply) else reply


def _handler(fake: FakePlatform):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # keep test output clean
            pass

        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            parsed = urllib.parse.urlsplit(self.path)
            recorded = Recorded(
                method=self.command, path=parsed.path,
                query=urllib.parse.parse_qs(parsed.query),
                headers={k: v for k, v in self.headers.items()}, raw=raw,
            )
            reply = fake.next_reply(recorded)
            if reply.delay:
                fake.release.wait(reply.delay)
            body = reply.raw
            content_type = "text/html; charset=utf-8"
            if body is None:
                body = json.dumps(reply.payload).encode("utf-8")
                content_type = "application/json"
            self.send_response(reply.status)
            self.send_header("Content-Type", reply.headers.get("Content-Type", content_type))
            for name, value in reply.headers.items():
                if name != "Content-Type":
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

    return Handler


def serve_fake(fake: FakePlatform, context: Optional[ssl.SSLContext] = None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    server.daemon_threads = True
    scheme = "http"
    if context is not None:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    fake.url = f"{scheme}://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02},
                              daemon=True)
    thread.start()
    return server
