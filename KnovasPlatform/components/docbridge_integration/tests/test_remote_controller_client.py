"""The console reaches RemoteController as the signed-in person, never anonymously."""

from __future__ import annotations

import pytest
import requests

from identity.ingestion_compiler import CompiledIngestion
from remote_controller_client import RemoteControllerClient, RemoteControllerError


class _Broker:
    def __init__(self, user="u-1"):
        self._user = user

    def current_user(self):
        return self._user

    def assertion_for(self, user):
        return f"token-for-{user}"


class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body if body is not None else {}
        self.ok = status < 400

    def json(self):
        return self._body


class _Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("json"), dict(kw.get("headers") or {})))
        handler = self.routes.get((method, url.rsplit("/", 1)[-1] if "?" not in url else url.rsplit("/", 1)[-1].split("?")[0]))
        return handler(kw) if callable(handler) else (handler or _Resp(200))


BASE = "http://remote-controller:5001"


def test_every_call_carries_the_principal_header():
    session = _Session({("GET", "status"): _Resp(200, {"state": "idle"})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    assert client.status() == {"state": "idle"}
    _, url, _, headers = session.calls[0]
    assert url == f"{BASE}/sync/status"
    assert headers["X-Platform-Principal"] == "token-for-u-1"


def test_no_user_means_no_call():
    session = _Session({})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(user=None), session=session)
    with pytest.raises(PermissionError):
        client.status()
    assert session.calls == []


def test_push_sends_config_then_request():
    session = _Session({("POST", "config"): _Resp(200, {"ok": True}),
                        ("POST", "sync"): _Resp(200, {"accepted": 3}),
                        ("GET", "config"): _Resp(200, {"old": True})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    out = client.push(CompiledIngestion(sync_config={"mode": "scheduled"},
                                        sync_request={"mode": "incremental"}))
    assert out == {"accepted": 3}
    methods_urls = [(m, u.rsplit("/", 1)[-1]) for m, u, _, _ in session.calls]
    assert methods_urls == [("GET", "config"), ("POST", "config"), ("POST", "sync")]


def test_a_refused_request_restores_the_previous_config():
    session = _Session({("GET", "config"): _Resp(200, {"old": True}),
                        ("POST", "config"): _Resp(200),
                        ("POST", "sync"): _Resp(400, {"error": "bad body"})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    with pytest.raises(RemoteControllerError) as excinfo:
        client.push(CompiledIngestion(sync_config={"new": True}, sync_request={}))
    assert excinfo.value.status == 400
    posted_configs = [body for m, u, body, _ in session.calls if m == "POST" and u.endswith("/sync/config")]
    assert posted_configs == [{"new": True}, {"old": True}], "rolled back to the old config"


def test_discover_passes_root_and_depth():
    session = _Session({("GET", "discover"): _Resp(200, {"folders": []})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    client.discover(root="/mnt/autodoc", max_depth=2)
    _, url, _, _ = session.calls[0]
    assert "root=%2Fmnt%2Fautodoc" in url and "max_depth=2" in url


def test_a_transport_failure_on_sync_still_rolls_back_and_is_a_client_error():
    def sync_raises(_kw):
        raise requests.exceptions.ConnectionError("down")
    session = _Session({("GET", "config"): _Resp(200, {"old": True}),
                        ("POST", "config"): _Resp(200),
                        ("POST", "sync"): sync_raises})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    with pytest.raises(RemoteControllerError) as excinfo:
        client.push(CompiledIngestion(sync_config={"new": True}, sync_request={}))
    assert excinfo.value.status is None
    posted_configs = [body for m, u, body, _ in session.calls if m == "POST" and u.endswith("/sync/config")]
    assert posted_configs == [{"new": True}, {"old": True}], "rolled back to the old config"


def test_a_failing_rollback_does_not_mask_the_original_error():
    call_count = {"config_post": 0}
    def config_post_raises(_kw):
        call_count["config_post"] += 1
        if call_count["config_post"] == 2:
            raise RuntimeError("boom")
        return _Resp(200)
    session = _Session({("GET", "config"): _Resp(200, {"old": True}),
                        ("POST", "config"): config_post_raises,
                        ("POST", "sync"): _Resp(400, {"error": "bad body"})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    with pytest.raises(RemoteControllerError) as excinfo:
        client.push(CompiledIngestion(sync_config={"new": True}, sync_request={}))
    assert excinfo.value.status == 400


def test_a_disabled_sync_config_api_names_the_variable():
    """C1: RC answers 404 on GET /sync/config when RC_SYNC_CONFIG_API_ENABLED
    is false. "Sync config API is disabled" points the administrator at
    nothing; the message must name the variable that turns it on."""
    session = _Session({("GET", "config"): _Resp(404, {"error": "Sync config API is disabled"})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    with pytest.raises(RemoteControllerError) as excinfo:
        client.push(CompiledIngestion(sync_config={"mode": "continuous"}, sync_request={}))
    assert excinfo.value.status == 404
    assert "RC_SYNC_CONFIG_API_ENABLED=false" in str(excinfo.value)
    assert [m for m, _u, _b, _h in session.calls] == ["GET"], "nothing is written after the refusal"
