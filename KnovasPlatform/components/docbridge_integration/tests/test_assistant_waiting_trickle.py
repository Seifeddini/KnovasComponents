"""PMVP-24, the verifier's finding 2: the deadline holds by the wall clock against a peer that trickles.

A read timeout bounds each read only. A peer that sends its headers and then one byte every few seconds (or that
trickles even its headers) would keep a search or a generation alive far past the answer's deadline. Here a real
socket server does exactly that, much faster (a byte every 0.2 s, a deadline of 2 s), against the Platform's own
Knovas client and against Anthropic's own SDK. Each call must give up by the deadline (plus a little for the
threads), as a timeout, and the connection must be dropped soon after rather than kept for the rest of the trickle.

No Core, no model, no network beyond 127.0.0.1. This file is ASCII-only.
"""
from __future__ import annotations

import socket
import threading
import time
import types

import pytest
import requests

from assistant.core import Deadline, make_search
from assistant.errors import CoreBusy, CoreFailed
from experiments.search import SearchIntegration
from identity.broker_key import load_or_create_signer
from knovas_client import KnovasAPIClient

DEADLINE = 2.0
GRACE = 1.5                      # threads starting and stopping, and one more trickled byte
INTERVAL = 0.2                   # the peer sends a byte this often: far less than any read timeout
TENANT = "tenant-a"


class TrickleServer:
    """An HTTP server on 127.0.0.1 that answers every request slowly.

    ``mode`` "body": the status line and the headers at once (a body of 100,000 bytes announced), then the body one
    byte every ``INTERVAL``. ``mode`` "headers": the status line and endless header lines, one byte every
    ``INTERVAL``. ``dropped`` records, per connection, when the client was seen to have gone.
    """

    def __init__(self, mode):
        self.mode = mode
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.started, self.dropped = [], []
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def _accept(self):
        self.listener.settimeout(0.2)
        while not self.stop.is_set():
            try:
                conn, _ = self.listener.accept()
            except OSError:
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    @staticmethod
    def _read_request(conn):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(65536)
            if not chunk:
                return False
            data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        length = 0
        for line in head.split(b"\r\n"):
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                length = int(value.strip())
        while len(body) < length:
            chunk = conn.recv(65536)
            if not chunk:
                return False
            body += chunk
        return True

    def _serve(self, conn):
        with conn:
            conn.settimeout(30)
            try:
                if not self._read_request(conn):
                    return
                self.started.append(time.monotonic())
                if self.mode == "body":
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                 b"Content-Length: 100000\r\n\r\n")
                    trickle = b"{" + b" " * 99_999
                else:
                    trickle = b"HTTP/1.1 200 OK\r\n" + b"X-Slow: a\r\n" * 100_000
                for byte in trickle:
                    if self.stop.is_set():
                        return
                    conn.sendall(bytes([byte]))
                    time.sleep(INTERVAL)
            except OSError:
                self.dropped.append(time.monotonic())

    def close(self):
        self.stop.set()
        self.listener.close()


@pytest.fixture(params=["body", "headers"])
def server(request):
    trickling = TrickleServer(request.param)
    yield trickling
    trickling.close()


class _StubConfig:
    def __init__(self, values):
        self._v = dict(values)

    def get(self, key, default=None):
        return self._v.get(key, default)

    def get_bool(self, key, default=False):
        return bool(self._v.get(key, default))

    def get_int(self, key, default=0):
        return int(self._v.get(key, default))


class _User:
    id = "55555555-5555-5555-5555-555555555555"
    roles = ("member",)


class _Broker:
    def __init__(self, signer):
        self._signer = signer

    def current_user(self):
        return _User()

    def assertion_for(self, user):
        return self._signer.mint(subject=str(user.id), tenant=TENANT, groups=["g"], roles=list(user.roles))


def _client(base, tmp_path, monkeypatch):
    """The Platform's client against the trickling server: secured mode as configured, plain HTTP on the wire."""
    monkeypatch.delenv("SEMANTIX_HTTP_READ_TIMEOUT", raising=False)
    (tmp_path / "broker").mkdir(exist_ok=True)
    client = KnovasAPIClient(config_loader=_StubConfig({
        "api.base_url": "https://core.test", "api.customer_id": TENANT, "api.cert_path": "client.crt", "api.key_path": "client.key",
        "api.ca_cert_path": "ca.crt", "api.cert_auto_renew_enabled": False, "api.rate_limit.requests_per_second": 0,
        "api.http_read_timeout": 30, "web.doc_fields.ui": "off"}))
    client.attach_principal_broker(_Broker(load_or_create_signer(tmp_path / "broker")))
    client.base_url = base                           # the trickling server, as RB-67's wire test points its client
    client._session = requests.Session()             # no client certificate on a plain socket
    return client


def _split():
    return SearchIntegration(settings=types.SimpleNamespace(pointer_prefix="experiments"),
                             connection=lambda: None, current_user=lambda: None, enabled=False).split


def _dropped_soon(server, after, within=GRACE + 2 * INTERVAL + 1.0):
    """The client's connection went away soon after it gave up, rather than staying for the rest of the trickle."""
    end = time.monotonic() + within + 1.0
    while time.monotonic() < end and not server.dropped:
        time.sleep(0.05)
    return bool(server.dropped) and server.dropped[0] - after <= within


def test_the_search_gives_up_by_the_deadline(server, tmp_path, monkeypatch):
    client = _client(server.base, tmp_path, monkeypatch)
    deadline = Deadline(seconds=DEADLINE)
    with pytest.raises(CoreBusy) as caught:
        make_search(client, split=_split(), deadline=deadline)("Wie lange gilt die Frist?", 5)
    took = deadline.elapsed()
    assert caught.value.reason == "search_timeout"
    assert DEADLINE - 0.1 <= took <= DEADLINE + GRACE, took
    assert len(server.started) == 1                                    # no retry without time for it
    if server.mode == "body":
        assert _dropped_soon(server, deadline.started + took)


def test_without_the_bound_the_trickle_would_hold_the_search(server, tmp_path, monkeypatch):
    """The control: the same search outside ``within_time`` is still waiting when the deadline has long passed."""
    client = _client(server.base, tmp_path, monkeypatch)
    finished = threading.Event()

    def search():
        try:
            client.search_documents("Wie lange gilt die Frist?", limit=5)
        except Exception:  # noqa: BLE001 - only whether it ended matters
            pass
        finally:
            finished.set()

    threading.Thread(target=search, daemon=True).start()
    assert not finished.wait(DEADLINE + GRACE + 1.0)


def test_the_cores_generation_gives_up_by_the_deadline(server, tmp_path, monkeypatch):
    client = _client(server.base, tmp_path, monkeypatch)
    deadline = Deadline(seconds=DEADLINE)
    with pytest.raises(CoreFailed) as caught, client.within_time(deadline.left):
        client.llm_generate([{"role": "user", "content": "Frage?"}], max_tokens=10)
    took = deadline.elapsed()
    assert caught.value.reason == "timeout"                            # the answer's deadline reads it as busy
    assert DEADLINE - 0.1 <= took <= DEADLINE + GRACE, took
    assert len(server.started) == 1                                    # never retried
    if server.mode == "body":
        assert _dropped_soon(server, deadline.started + took)


def test_claude_gives_up_by_the_deadline(server):
    anthropic = pytest.importorskip("anthropic")
    from assistant import providers

    deadline = Deadline(seconds=DEADLINE)
    built = []

    def factory(**kwargs):
        built.append(kwargs)
        return anthropic.Anthropic(base_url=server.base, **kwargs)

    generate = providers.make_anthropic_generate(api_key="sk-ant-test", client_factory=factory,
                                                 time_left=deadline.left)
    with pytest.raises(CoreFailed) as caught:
        generate(system_instruction="Regeln", messages=[{"role": "user", "content": "Frage?"}], max_tokens=10)
    took = deadline.elapsed()
    assert caught.value.reason == "anthropic_timeout"                  # the answer's deadline reads it as busy
    assert DEADLINE - 0.1 <= took <= DEADLINE + GRACE, took
    assert built[0]["max_retries"] == 0 and len(server.started) == 1
    if server.mode == "body":
        assert _dropped_soon(server, deadline.started + took)
