"""PMVP-42: the switch's route, where the frozen acceptance file leaves it open (the real app, the identity database,
a fake Core).

1. a Core without the route (None) is recorded as a refusal by id (``code`` ``no_route``), the page waits for Knovas;
2. a switch the Core made but whose audit row cannot be written answers 500 and says so; the Core was asked once;
3. a Core that fails is recorded as an unknown outcome and the page reads the position again;
4. while the position is unknown both positions are offered, disabled, and a refused confirmation opens the form it
   came from again;
5. the row names the tenant by the client's tenant id and the administrator by id;
6. every attempt is recorded before it is sent (``enforcement.switch_attempted``, a write that raises: without it
   nothing is sent), and its outcome after: a Core that switches but whose answer is lost is «unbekannt» in the
   page's words and in the log (``enforcement.switch_unknown``), and the page shows what the Core reports then.
"""
from __future__ import annotations

import re

import pytest
import requests

import conftest
from conftest import DummyKnovasClient, platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from web_interface import admin_access  # noqa: E402

SWITCH = "/admin/access/enforcement"


class Core(DummyKnovasClient):
    posture = "disabled"
    answer = None
    sent: list = []

    def rbac_posture(self):
        if Core.posture is None:
            return None
        return {"mode": Core.posture, "not_ready": [], "tenant_switch": True}

    def set_rbac_posture(self, mode):
        Core.sent.append(mode)
        if Core.answer == "missing":
            return None
        if Core.answer == "raise":
            raise ConnectionError("unreachable")
        if Core.answer == "switch-then-timeout":
            Core.posture = mode
            raise requests.exceptions.ReadTimeout("the answer was lost")
        Core.posture = mode
        return {"mode": mode}


@pytest.fixture
def world(platform_db, identity_repo, tmp_path, monkeypatch):
    Core.posture, Core.answer, Core.sent = "disabled", None, []
    app = conftest._identity_app(platform_db, tmp_path, monkeypatch, client_cls=Core)
    chef = conftest._person(identity_repo, "chef@kanzlei.ch", "Chef", "admin")
    client = conftest._signed_in(app, chef.email, with_csrf=False)
    with client.session_transaction() as sess:
        token = sess["csrf_token"]

    def switch(mode, **extra):
        return client.post(SWITCH, data={"mode": mode, "csrf_token": token, **extra})

    def rows(attempts=False):
        return [r for r in platform_db.execute(
            "SELECT action, target_type, target_id, actor_user_id::text, outcome, detail "
            "FROM audit_log WHERE action LIKE 'enforcement.%' ORDER BY id").fetchall()
            if attempts or r[0] != "enforcement.switch_attempted"]

    return {"client": client, "switch": switch, "rows": rows, "chef": chef, "conn": platform_db}


def _text(response):
    return response.data.decode("utf-8")


def test_a_core_without_the_route_is_recorded_as_a_refusal_and_waits(world):
    Core.answer = "missing"
    response = world["switch"]("ein", bestaetigt="1")
    assert response.status_code == 409 and "wartet auf Knovas" in _text(response)
    [row] = world["rows"]()
    assert row[0] == "enforcement.switch_refused" and row[5] == {"to": "brokered", "code": "no_route"}
    assert (row[1], row[2], row[3], row[4]) == ("enforcement", "tenant-a", str(world["chef"].id), "denied")


def test_a_switch_without_its_audit_row_answers_500_and_says_so(world, monkeypatch):
    real = admin_access.audit.record_or_raise

    def refuse(conn, *, action, **kwargs):
        if action == "enforcement.switched":                          # the attempt's row is written, its outcome's not
            raise RuntimeError("the audit table refused")
        return real(conn, action=action, **kwargs)

    monkeypatch.setattr(admin_access.audit, "record_or_raise", refuse)
    response = world["switch"]("ein", bestaetigt="1")
    assert response.status_code == 500
    assert "Knovas hat umgeschaltet" in _text(response) and "Protokolleintrag" in _text(response)
    assert Core.sent == ["brokered"]
    assert 'data-enforcement-position="ein"' in _text(response)       # what the Core reports now


def test_a_failing_core_is_recorded_as_unknown_and_the_page_reads_the_position_again(world):
    Core.answer = "raise"
    response = world["switch"]("ein", bestaetigt="1")
    assert response.status_code == 502 and 'data-enforcement-position="aus"' in _text(response)
    assert [r[0] for r in world["rows"]()] == ["enforcement.switch_unknown"] and Core.sent == ["brokered"]


def test_while_unknown_both_positions_are_offered_disabled(world):
    Core.posture = None
    html = _text(world["client"].get("/admin/access"))
    forms = [part[: part.index("</form>")] for part in html.split(f'action="{SWITCH}"')[1:]]
    assert sorted(re.search(r'name="mode" value="([a-z]+)"', f).group(1) for f in forms) == ["aus", "ein"]
    assert all("<fieldset disabled>" in f for f in forms)


def test_a_refused_confirmation_opens_its_form_again(world):
    response = world["switch"]("ein")
    assert response.status_code == 400
    assert re.search(r'<details class="access-switch" open>\s*<summary>Auf «Ein» schalten', _text(response))
    assert Core.sent == [] and world["rows"]() == []


def test_a_switch_whose_answer_is_lost_is_recorded_as_unknown_and_the_page_reads_the_position(world):
    Core.answer = "switch-then-timeout"
    response = world["switch"]("ein", bestaetigt="1")
    assert response.status_code == 502
    html = _text(response)
    assert "Ob umgeschaltet wurde, ist unbekannt" in html
    assert 'data-enforcement-position="ein"' in html                  # what the Core reports now
    assert Core.sent == ["brokered"]
    rows = world["rows"](attempts=True)
    assert [r[0] for r in rows] == ["enforcement.switch_attempted", "enforcement.switch_unknown"]
    assert rows[0][5] == {"from": "disabled", "to": "brokered"} and rows[1][4] == "error"
    assert rows[1][5] == {"to": "brokered", "error": "ReadTimeout", "status": None}


def test_every_attempt_is_recorded_before_it_is_sent_and_without_its_row_nothing_is_sent(world):
    for mode, posture in (("ein", "disabled"), ("aus", "brokered")):
        Core.posture = posture
        assert world["switch"](mode, bestaetigt="1").status_code == 200
    rows = world["rows"](attempts=True)
    assert [r[0] for r in rows] == ["enforcement.switch_attempted", "enforcement.switched"] * 2
    world["conn"].execute("ALTER TABLE audit_log ADD CONSTRAINT no_attempt "
                          "CHECK (action <> 'enforcement.switch_attempted') NOT VALID")
    Core.posture, Core.sent = "disabled", []
    response = world["switch"]("ein", bestaetigt="1")
    assert response.status_code == 503 and "Es wurde nichts an Knovas gesendet" in _text(response)
    assert Core.sent == [] and len(world["rows"](attempts=True)) == 4
