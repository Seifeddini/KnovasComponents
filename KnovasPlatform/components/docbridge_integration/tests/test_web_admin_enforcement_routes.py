"""PMVP-42: the switch's route, where the frozen acceptance file leaves it open (the real app, the identity database,
a fake Core).

1. a Core without the route (None) is recorded as a refusal by id (``code`` ``no_route``), the page waits for Knovas;
2. a switch the Core made but whose audit row cannot be written answers 500 and says so; the Core was asked once;
3. a Core that fails writes no row and the page reads the position again;
4. while the position is unknown both positions are offered, disabled, and a refused confirmation opens the form it
   came from again;
5. the row names the tenant by the client's tenant id and the administrator by id.
"""
from __future__ import annotations

import re

import pytest

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

    def rows():
        return platform_db.execute("SELECT action, target_type, target_id, actor_user_id::text, outcome, detail "
                                   "FROM audit_log WHERE action LIKE 'enforcement.%' ORDER BY id").fetchall()

    return {"client": client, "switch": switch, "rows": rows, "chef": chef}


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
    def refuse(*args, **kwargs):
        raise RuntimeError("the audit table refused")

    monkeypatch.setattr(admin_access.audit, "record_or_raise", refuse)
    response = world["switch"]("ein", bestaetigt="1")
    assert response.status_code == 500
    assert "Knovas hat umgeschaltet" in _text(response) and "Protokolleintrag" in _text(response)
    assert Core.sent == ["brokered"]
    assert 'data-enforcement-position="ein"' in _text(response)       # what the Core reports now


def test_a_failing_core_writes_no_row_and_the_page_reads_the_position_again(world):
    Core.answer = "raise"
    response = world["switch"]("ein", bestaetigt="1")
    assert response.status_code == 502 and 'data-enforcement-position="aus"' in _text(response)
    assert world["rows"]() == [] and Core.sent == ["brokered"]


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
