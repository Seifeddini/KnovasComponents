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
        self.routes, self.calls, self.timeouts = routes, [], []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("json"), dict(kw.get("headers") or {})))
        self.timeouts.append(kw.get("timeout"))
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


def _steps(session):
    return [(m, u.rsplit("/", 1)[-1]) for m, u, _b, _h in session.calls]


IDLE_STATUS = _Resp(200, {"scheduler_state": "not_running"})
RUNNING_STATUS = _Resp(200, {"scheduler_state": "idle_between_cycles"})


def test_push_stores_the_body_and_starts_an_idle_continuous_scheduler():
    session = _Session({("GET", "config"): _Resp(200, {"old": True}),
                        ("POST", "config"): _Resp(200, {"ok": True}),
                        ("POST", "body"): _Resp(200, {"status": "stored"}),
                        ("GET", "status"): IDLE_STATUS,
                        ("POST", "start"): _Resp(200, {"scheduler_status": "running"})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    out = client.push(CompiledIngestion(sync_config={"mode": "continuous"},
                                        sync_request={"mode": "incremental"}))
    assert out == {"applied": "started"}
    assert _steps(session) == [("GET", "config"), ("POST", "config"),
                               ("POST", "body"), ("GET", "status"), ("POST", "start")]


def test_push_leaves_a_running_scheduler_alone_and_says_next_cycle():
    """C2: POST /sync answered already_running and the running worker kept
    its old folder list, while the console said "uebertragen". The worker now
    rereads the body each cycle, so the honest answer is "at the next one"."""
    session = _Session({("GET", "config"): _Resp(200, {"old": True}),
                        ("POST", "config"): _Resp(200),
                        ("POST", "body"): _Resp(200, {"status": "stored"}),
                        ("GET", "status"): RUNNING_STATUS,
                        ("POST", "start"): _Resp(200)})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    out = client.push(CompiledIngestion(sync_config={"mode": "continuous"}, sync_request={}))
    assert out == {"applied": "next_cycle"}
    assert ("POST", "start") not in _steps(session)


def test_a_one_time_profile_is_only_stored_never_run():
    """The `manual` preset. POST /sync in one_time mode ran a whole scan
    inside the request against a 20 s timeout; push must not do that."""
    session = _Session({("GET", "config"): _Resp(200, {"old": True}),
                        ("POST", "config"): _Resp(200),
                        ("POST", "body"): _Resp(200, {"status": "stored"})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    out = client.push(CompiledIngestion(sync_config={"mode": "one_time"}, sync_request={}))
    assert out == {"applied": "stored"}
    assert _steps(session) == [("GET", "config"), ("POST", "config"), ("POST", "body")]


def test_a_refused_body_restores_the_previous_config():
    session = _Session({("GET", "config"): _Resp(200, {"old": True}),
                        ("POST", "config"): _Resp(200),
                        ("POST", "body"): _Resp(400, {"error": "bad body"})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    with pytest.raises(RemoteControllerError) as excinfo:
        client.push(CompiledIngestion(sync_config={"mode": "continuous"}, sync_request={}))
    assert excinfo.value.status == 400
    posted_configs = [body for m, u, body, _ in session.calls if m == "POST" and u.endswith("/sync/config")]
    assert posted_configs == [{"mode": "continuous"}, {"old": True}], "rolled back"


def test_a_failed_start_is_reported_without_rolling_the_profile_back():
    """The profile IS on RemoteController; only starting it failed. Undoing
    the config here would leave the folder list and the schedule disagreeing."""
    session = _Session({("GET", "config"): _Resp(200, {"old": True}),
                        ("POST", "config"): _Resp(200),
                        ("POST", "body"): _Resp(200, {"status": "stored"}),
                        ("GET", "status"): IDLE_STATUS,
                        ("POST", "start"): _Resp(400, {"error": "No sync body available"})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    out = client.push(CompiledIngestion(sync_config={"mode": "continuous"}, sync_request={}))
    assert out["applied"] == "stored"
    assert "No sync body available" in out["start_error"]
    posted_configs = [body for m, u, body, _ in session.calls if m == "POST" and u.endswith("/sync/config")]
    assert posted_configs == [{"mode": "continuous"}], "no rollback"


def test_status_and_discover_do_not_wait_the_full_push_timeout():
    """M4: _page() calls status() on every render and preview() makes one
    discover call per folder; a RemoteController that blackholes must not
    turn the tab into a gunicorn timeout."""
    session = _Session({("GET", "status"): _Resp(200, {}),
                        ("GET", "discover"): _Resp(200, {}),
                        ("POST", "stop"): _Resp(200, {})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    client.status()
    client.discover(root="/mnt")
    client.stop()
    assert session.timeouts == [5.0, 10.0, 20.0]


def test_discover_passes_root_and_depth():
    session = _Session({("GET", "discover"): _Resp(200, {"folders": []})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    client.discover(root="/mnt/autodoc", max_depth=2)
    _, url, _, _ = session.calls[0]
    assert "root=%2Fmnt%2Fautodoc" in url and "max_depth=2" in url


def test_a_transport_failure_on_the_body_still_rolls_back_and_is_a_client_error():
    def body_raises(_kw):
        raise requests.exceptions.ConnectionError("down")
    session = _Session({("GET", "config"): _Resp(200, {"old": True}),
                        ("POST", "config"): _Resp(200),
                        ("POST", "body"): body_raises})
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
                        ("POST", "body"): _Resp(400, {"error": "bad body"})})
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
