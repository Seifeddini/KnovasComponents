"""PMVP-42: the client's two posture calls, on the wire (fakes only: nothing reaches a Core).

``rbac_posture()`` is CR-R1a's ``GET /secured/rbac/posture``; ``set_rbac_posture(mode)`` is CR-R8's ``PUT`` with
``{"mode": …}``. Held here:

1. the read keeps the five fields of the answer and nothing else; a Core without the route (404) answers None; any
   other failure raises an error that names the route and the status only;
2. the switch sends ``brokered`` or ``disabled`` once and nothing else: ``enforcing``, Probe or anything else raises
   before a request is made; a 409 is ``{"refused": <the Core's short error_code>}`` (``unknown`` when the code is no
   short identifier); a 404 is None; a 5xx raises;
3. no log line or error text carries the Core's message, at any level.
"""
from __future__ import annotations

import json
import logging

import pytest
import requests
import tenacity

from knovas_client import KnovasAPIClient
from test_knovas_client_hardening import FakeSession, make_client

ROUTE = "https://knovas.test/secured/rbac/posture"
MESSAGE = "Mandant Zwiebel-Erbschaft: zwei grüne Läufe fehlen"


class Answer:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.text = "" if body is None else json.dumps(body)
        self.content = self.text.encode("utf-8")
        self.headers = {"X-Request-ID": "req-1"}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} for url: {ROUTE}", response=self)


def _client(answer):
    client = make_client()
    client._session = FakeSession(lambda method, url, **kw: answer)
    return client


@pytest.fixture(autouse=True)
def _debug_and_fast(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="knovas_client")
    monkeypatch.setattr(KnovasAPIClient._make_request.retry, "wait", tenacity.wait_none())


POSTURE = {"mode": "disabled", "backfill_complete": True, "broker_key_registered": False,
           "not_ready": ["no_broker_key", "rights_runs_missing"], "tenant_switch": True,
           "tenant": "zwiebel", "groups": ["g-zwiebel"]}


# --- 1. the read --------------------------------------------------------------------------------------------------


def test_the_read_asks_the_route_and_keeps_its_five_fields():
    client = _client(Answer(200, POSTURE))
    assert client.rbac_posture() == {"mode": "disabled", "backfill_complete": True, "broker_key_registered": False,
                                     "not_ready": ["no_broker_key", "rights_runs_missing"], "tenant_switch": True}
    [call] = client._session.calls
    assert (call["method"], call["url"].split("?")[0]) == ("GET", ROUTE)


def test_a_core_without_the_route_answers_none():
    assert _client(Answer(404, {"error_code": "NOT_FOUND", "message": MESSAGE})).rbac_posture() is None


def test_a_failing_read_raises_with_the_route_and_status_only(caplog):
    with pytest.raises(requests.exceptions.HTTPError) as raised:
        _client(Answer(503, {"error_code": "posture_unreadable", "message": MESSAGE})).rbac_posture()
    assert "/secured/rbac/posture" in str(raised.value) and "503" in str(raised.value)
    assert MESSAGE not in str(raised.value) and MESSAGE not in caplog.text and "Zwiebel" not in caplog.text


# --- 2. the switch ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["brokered", "disabled"])
def test_the_switch_sends_the_mode_once(mode):
    client = _client(Answer(200, {"mode": mode, "tenant": "zwiebel"}))
    assert client.set_rbac_posture(mode) == {"mode": mode}
    [call] = client._session.calls
    assert (call["method"], call["url"].split("?")[0]) == ("PUT", ROUTE)
    assert call["json"]["mode"] == mode


@pytest.mark.parametrize("mode", ["enforcing", "probe", "ein", "", None, "BROKERED"])
def test_any_other_mode_is_refused_before_anything_is_sent(mode):
    client = _client(Answer(200, {"mode": "enforcing"}))
    with pytest.raises(ValueError):
        client.set_rbac_posture(mode)
    assert client._session.calls == []


@pytest.mark.parametrize("code, expected", [("rights_runs_missing", "rights_runs_missing"),
                                            ("no_broker_key", "no_broker_key"),
                                            ("backfill_incomplete", "backfill_incomplete"),
                                            ("something_new", "something_new"),
                                            ("zwei grüne Läufe fehlen", "unknown"), (None, "unknown")])
def test_a_refusal_is_the_cores_short_code(code, expected, caplog):
    body = {"message": MESSAGE} if code is None else {"error_code": code, "message": MESSAGE}
    client = _client(Answer(409, body))
    assert client.set_rbac_posture("brokered") == {"refused": expected}
    assert len(client._session.calls) == 1
    assert MESSAGE not in caplog.text and "Zwiebel" not in caplog.text


def test_a_core_without_the_route_answers_none_and_a_failure_raises(caplog):
    assert _client(Answer(404, {"error_code": "NOT_FOUND", "message": MESSAGE})).set_rbac_posture("disabled") is None
    with pytest.raises(requests.exceptions.HTTPError) as raised:
        _client(Answer(500, {"error_code": "INTERNAL", "message": MESSAGE})).set_rbac_posture("brokered")
    assert "/secured/rbac/posture" in str(raised.value) and MESSAGE not in str(raised.value)
    assert MESSAGE not in caplog.text and "Zwiebel" not in caplog.text
