"""PMVP-42 acceptance tests: the enforcement position shown, and one administrator switching it (FEAT-2026-11-19-platform-
mvp § 4 A-06 and § 8.1 as the founder answered them on 2026-11-22, spec item SPEC-platform-mvp-14, DEC-0082 (never
cut); work order docs/platform-mvp/work-orders/PMVP-42.md; Core requests CR-R1a, CR-R7 and CR-R8 in
docs/platform-mvp/core-requests.md).

FROZEN once the coordinator pins it. Written for the coordinator before the build; a builder never edits this file.
Its git blob id goes into docs/platform-mvp/frozen-tests.txt.

The founder decided on 2026-11-22: "An admin alone should be able to switch such things", and RBAC is handled in the
Core. So the posture belongs to the Core: it reports it (CR-R1a), it sets it for the tenant (CR-R8), and it refuses
«Ein» (``brokered``) until its own prerequisites hold: a broker key, a complete backfill, two green end-to-end rights
runs under ``brokered`` (CR-R7). The Platform shows the position, lets one administrator switch after a confirmation,
and shows the Core's reason when it refuses. It checks no gate and keeps no record of runs.

What is pinned (the sections below carry the same numbers):

1. The position. ``GET /admin/access`` says in one sentence which position is active, on one element
   ``data-enforcement-position``: ``ein`` when the client reports ``rbac_posture()["mode"] == "brokered"``, ``aus``
   for ``disabled``, ``teilweise`` for ``enforcing``, and ``unbekannt`` when the client has no such call (the real
   client before CR-R1a), it fails or it reports anything else. While the Core reports ``not_ready`` codes, the page
   says why «Ein» is not possible yet, in its own words (never the bare code). Opening the page writes nothing.
2. The switch. ``POST /admin/access/enforcement`` (``mode`` = ``ein`` | ``aus``, ``bestaetigt`` = ``1``): one
   administrator, no second person and no bypass setting involved. Without the confirmation 400 and nothing sent.
   The Platform calls ``set_rbac_posture("brokered")`` for «Ein» and ``set_rbac_posture("disabled")`` for «Aus»,
   once, never ``enforcing``, never for the position already active; the page then shows what the Core reports.
   The form offers only the other position (both while ``teilweise``), never Probe, and carries the checkbox
   ``bestaetigt``. Every attempt that reaches the Core is audited by id: ``enforcement.switched`` (``detail.to``) or
   ``enforcement.switch_refused`` (``detail.code``).
3. Waiting for Knovas. Until the Core reports ``tenant_switch: true`` (CR-R8 offered), the switch is shown disabled
   with «wartet auf Knovas», and a POST sends nothing (409). A Core that answers None (no route) says the same.
4. The Core refuses. A refusal (``{"refused": <code>}``: ``no_broker_key``, ``backfill_incomplete``,
   ``rights_runs_missing``, or a code the Platform does not know) is 409 with the reason in German words of its own
   per code, never the bare code; the position stays what the Core reports. A Core that fails is 502.
5. Gates and tokens. The route admits administrators only and checks ``csrf_token``.
6. One way to switch. The Builder's old switch request (``POST /builder/api/access/posture``) records nothing.
7. Audit and logs. People by id only; no name or address in a log line, DEBUG included.

Nothing reaches a tenant: the Core is a recording fake.
"""
from __future__ import annotations

import json
import logging
import re

import pytest
import requests

import conftest
from conftest import FakeGraphApi, platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity.approvals import ApprovalService  # noqa: E402
from identity.users import UserRepository  # noqa: E402

PAGE = "/admin/access"
SWITCH = "/admin/access/enforcement"
SWITCHED, REFUSED = "enforcement.switched", "enforcement.switch_refused"
WAITS = "wartet auf Knovas"
CODES = ("no_broker_key", "backfill_incomplete", "rights_runs_missing")

_WRITE = re.compile(r"^(graph_)?(create|rename|move|delete|set|update|put|patch|post|install|deprecate|retire|"
                    r"upload|purge|push|start|stop|requeue|reextract|apply|grant|revoke|switch|enforce)(_|$)")


# --- the Core: a recording fake ----------------------------------------------------------------------------------------


class PostureCore(FakeGraphApi):
    """The Core as the page sees it through the client.

    ``posture``: the mode ``rbac_posture()`` reports (None: no such call, the real client before CR-R1a; "raise": it
    fails; "not-a-dict": it answers garbage). ``tenant_switch``: whether the Core offers CR-R8. ``not_ready``: the
    codes it reports for «Ein». ``answer``: what ``set_rbac_posture`` does: None applies the mode, "refuse:<code>"
    answers ``{"refused": <code>}``, "missing" answers None (no route), "raise" fails. Every call whose name writes is
    recorded with its arguments."""

    calls: list = []
    posture = None
    tenant_switch = True
    not_ready: list = []
    answer = None

    def __getattribute__(self, name):
        if name == "rbac_posture" and PostureCore.posture is None:
            raise AttributeError(name)
        attr = object.__getattribute__(self, name)
        if callable(attr) and (_WRITE.match(name) or "posture" in name or "enforc" in name) and name != "rbac_posture":
            def recorded(*args, **kwargs):
                PostureCore.calls.append((name, args, kwargs))
                return attr(*args, **kwargs)
            return recorded
        return attr

    def rbac_posture(self):
        if PostureCore.posture == "raise":
            raise requests.exceptions.ConnectionError("unreachable")
        if PostureCore.posture == "not-a-dict":
            return "brokered"
        return {"mode": PostureCore.posture, "backfill_complete": True, "broker_key_registered": True,
                "not_ready": list(PostureCore.not_ready), "tenant_switch": PostureCore.tenant_switch}

    def set_rbac_posture(self, mode):
        answer = PostureCore.answer
        if answer == "raise":
            raise requests.exceptions.ConnectionError("unreachable")
        if answer == "missing":
            return None
        if answer and answer.startswith("refuse:"):
            return {"refused": answer.split(":", 1)[1]}
        PostureCore.posture = mode
        return {"mode": mode}


def _sent():
    return [(args[0] if args else kwargs.get("mode")) for name, args, kwargs in PostureCore.calls
            if name == "set_rbac_posture"]


# --- the world ----------------------------------------------------------------------------------------------------------


class Browser:
    def __init__(self, app, email):
        self.client = app.test_client()
        page = self.client.get("/login")
        self.client.post("/login", data={"login_name": email, "password": conftest.PASSWORD,
                                         "csrf_token": conftest._csrf_from(page.data.decode("utf-8"))})
        with self.client.session_transaction() as sess:
            self.token = sess.get("csrf_token")

    def page(self, path=PAGE):
        response = self.client.get(path)
        assert response.status_code == 200, f"{path} answered {response.status_code}"
        return response.data.decode("utf-8")

    def post(self, path, data=None, *, token=True):
        fields = dict(data or {})
        if token is True:
            fields["csrf_token"] = self.token
        elif token:
            fields["csrf_token"] = token
        return self.client.post(path, data=fields)

    def switch(self, mode, confirmed=True):
        return self.post(SWITCH, {"mode": mode, **({"bestaetigt": "1"} if confirmed else {})})


def _text(html):
    """The visible text: tags (and the attributes inside them) removed."""
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _position(html):
    found = re.findall(r'<([a-z0-9]+)[^>]*\sdata-enforcement-position="([^"]*)"[^>]*>(.*?)</\1>', html, re.S)
    assert len(found) == 1, f"one position sentence, found {len(found)}"
    _, value, inner = found[0]
    text = _text(inner)
    assert text, "the position needs its sentence"
    return value, text


def _forms(html):
    """Every form posting to the switch, as its HTML up to ``</form>``."""
    return [part[: part.index("</form>")] for part in html.split(f'action="{SWITCH}"')[1:]]


def _offered(html):
    return {v for form in _forms(html) for v in re.findall(r'value="([^"]*)"', form) if v in ("ein", "aus", "probe")}


class World:
    def __init__(self, conn, app):
        self.conn, self.app = conn, app
        self.repo = UserRepository(conn)
        self.chef = self.person("chef", "admin", display_name="Chef Zwiebel")
        self.paula = self.person("paula", "approver", display_name="Paula Zwiebel")
        self.mia = self.person("mia", "member", display_name="Mia Zwiebel")
        self.admin = Browser(app, self.chef.email)

    def person(self, name, key, *, display_name):
        user = self.repo.create(email=f"{name}@kanzlei.ch", display_name=display_name, password=conftest.PASSWORD)
        self.repo.grant_role(user.id, key)
        return self.repo.get(user.id)

    def browser(self, person):
        return Browser(self.app, person.email)

    def audit(self, action):
        rows = self.conn.execute(
            "SELECT target_type, target_id, actor_user_id::text, detail FROM audit_log WHERE action = %s ORDER BY id",
            (action,)).fetchall()
        return [dict(zip(("target_type", "target_id", "actor", "detail"), row)) for row in rows]


@pytest.fixture
def world(platform_db, tmp_path, monkeypatch):
    PostureCore.calls = []
    PostureCore.posture = None
    PostureCore.tenant_switch = True
    PostureCore.not_ready = []
    PostureCore.answer = None
    app = conftest._identity_app(platform_db, tmp_path, monkeypatch, client_cls=PostureCore)
    PostureCore.calls = []
    return World(platform_db, app)


# === 1. the position ===================================================================================================


def test_the_page_says_in_one_sentence_which_position_is_active(world):
    expected = ((None, "unbekannt"), ("raise", "unbekannt"), ("disabled", "aus"), ("enforcing", "teilweise"),
                ("brokered", "ein"), ("probe", "unbekannt"), ("not-a-dict", "unbekannt"))
    for posture, value in expected:
        PostureCore.posture = posture
        shown, text = _position(world.admin.page())
        assert shown == value, (posture, shown)
        if value == "aus":
            assert "«Aus»" in text
        if value == "ein":
            assert "«Ein»" in text
    assert PostureCore.calls == [] and world.audit(SWITCHED) == []          # opening the page writes nothing


def test_the_page_says_why_ein_is_not_possible_yet_in_its_own_words(world):
    PostureCore.posture = "disabled"
    texts = {}
    for code in CODES:
        PostureCore.not_ready = [code]
        text = _text(world.admin.page())
        assert code not in text, code
        texts[code] = text
    assert len(set(texts.values())) == len(CODES)                          # each reason has words of its own
    PostureCore.not_ready = []
    assert len({_text(world.admin.page())} | set(texts.values())) == len(CODES) + 1


# === 2. the switch: one administrator, with a confirmation =============================================================


def test_the_switch_offers_the_other_position_with_a_confirmation_and_never_probe(world):
    for posture, wanted in (("disabled", {"ein"}), ("brokered", {"aus"}), ("enforcing", {"ein", "aus"})):
        PostureCore.posture = posture
        html = world.admin.page()
        forms = _forms(html)
        assert forms, posture
        assert _offered(html) == wanted, (posture, _offered(html))
        assert all(re.search(r'name="mode"', f) for f in forms)
        assert all(re.search(r'<input[^>]*type="checkbox"[^>]*name="bestaetigt"|<input[^>]*name="bestaetigt"[^>]*'
                             r'type="checkbox"', f) for f in forms), posture
        assert "probe" not in " ".join(forms).lower()
        assert WAITS not in html
    assert PostureCore.calls == []


def test_one_administrator_switches_to_ein_alone(world):
    PostureCore.posture = "disabled"
    ApprovalService(world.conn, world.repo).set_admin_bypass(False, by=world.chef)   # no second person either way
    response = world.admin.switch("ein")
    assert response.status_code == 200
    assert _sent() == ["brokered"]
    assert _position(world.admin.page())[0] == "ein"
    [row] = world.audit(SWITCHED)
    assert (row["target_type"], row["actor"]) == ("enforcement", str(world.chef.id))
    assert row["detail"]["to"] == "brokered"
    assert world.conn.execute("SELECT count(*) FROM approval_requests").fetchone()[0] == 0
    assert world.audit("approval.bypassed") == [] and world.audit(REFUSED) == []


def test_one_administrator_switches_to_aus(world):
    PostureCore.posture = "brokered"
    assert world.admin.switch("aus").status_code == 200
    assert _sent() == ["disabled"]
    assert _position(world.admin.page())[0] == "aus"
    assert [r["detail"]["to"] for r in world.audit(SWITCHED)] == ["disabled"]


def test_from_teilweise_both_ways_are_open(world):
    PostureCore.posture = "enforcing"
    assert world.admin.switch("ein").status_code == 200
    PostureCore.posture = "enforcing"
    assert world.admin.switch("aus").status_code == 200
    assert _sent() == ["brokered", "disabled"]


def test_without_the_confirmation_nothing_is_sent(world):
    PostureCore.posture = "disabled"
    assert world.admin.switch("ein", confirmed=False).status_code == 400
    assert world.admin.post(SWITCH, {"mode": "ein", "bestaetigt": "0"}).status_code == 400
    assert _sent() == [] and world.audit(SWITCHED) == [] and world.audit(REFUSED) == []
    assert _position(world.admin.page())[0] == "aus"


def test_a_bad_mode_is_refused_and_enforcing_is_never_sent(world):
    PostureCore.posture = "disabled"
    for mode in ("", "probe", "enforcing", "brokered", "disabled", "EIN"):
        assert world.admin.switch(mode).status_code == 400, mode
    assert _sent() == [] and world.audit(SWITCHED) == []


def test_the_position_already_active_is_not_sent(world):
    PostureCore.posture = "brokered"
    assert world.admin.switch("ein").status_code == 409
    PostureCore.posture = "disabled"
    assert world.admin.switch("aus").status_code == 409
    assert _sent() == [] and world.audit(SWITCHED) == []


# === 3. waiting for Knovas =============================================================================================


def test_until_the_core_offers_the_switch_it_waits_for_knovas(world):
    for posture, offered in ((None, True), ("raise", True), ("disabled", False), ("not-a-dict", True)):
        PostureCore.posture, PostureCore.tenant_switch = posture, offered
        html = world.admin.page()
        assert WAITS in _text(html), posture
        for form in _forms(html):
            assert re.search(r"\sdisabled(\s|>|=)", form), f"an active switch while it waits ({posture})"
        assert world.admin.switch("ein").status_code == 409, posture
        assert world.admin.switch("aus").status_code == 409, posture
    assert _sent() == [] and world.audit(SWITCHED) == [] and world.audit(REFUSED) == []


def test_a_core_without_the_route_also_waits_for_knovas(world):
    PostureCore.posture, PostureCore.answer = "disabled", "missing"
    response = world.admin.switch("ein")
    assert response.status_code == 409
    assert WAITS in _text(response.data.decode("utf-8"))
    assert _sent() == ["brokered"] and world.audit(SWITCHED) == []
    assert _position(world.admin.page())[0] == "aus"


# === 4. the Core refuses ===============================================================================================


def test_a_refusal_is_shown_in_words_of_its_own_and_changes_nothing(world):
    PostureCore.posture = "disabled"
    texts = {}
    for code in CODES + ("something_new",):
        PostureCore.answer = f"refuse:{code}"
        response = world.admin.switch("ein")
        assert response.status_code == 409, code
        html = response.data.decode("utf-8")
        assert re.search(r'role="alert"', html), code
        text = _text(html)
        assert code not in text, code
        texts[code] = text
        assert _position(html)[0] == "aus"
    assert len({texts[c] for c in CODES}) == len(CODES)                    # each known code has words of its own
    assert _sent() == ["brokered"] * 4
    rows = world.audit(REFUSED)
    assert [r["detail"]["code"] for r in rows] == list(CODES) + ["something_new"]
    assert all(r["actor"] == str(world.chef.id) for r in rows) and world.audit(SWITCHED) == []


def test_a_core_that_fails_is_502_and_changes_nothing(world):
    PostureCore.posture, PostureCore.answer = "disabled", "raise"
    assert world.admin.switch("ein").status_code == 502
    assert _position(world.admin.page())[0] == "aus" and world.audit(SWITCHED) == []


# === 5. gates and tokens ===============================================================================================


def test_the_switch_admits_administrators_only(world):
    endpoint, _ = world.app.url_map.bind("localhost").match(SWITCH, method="POST")
    assert getattr(world.app.view_functions[endpoint], "allowed_roles", None) == frozenset({"admin"})


def test_signed_out_a_member_and_an_approver_cannot_switch(world):
    PostureCore.posture = "disabled"
    response = world.app.test_client().post(SWITCH, data={"mode": "ein", "bestaetigt": "1"})
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    for someone in (world.mia, world.paula):
        assert world.browser(someone).switch("ein").status_code == 403
    assert _sent() == [] and world.audit(SWITCHED) == []


def test_the_switch_checks_its_token(world):
    PostureCore.posture = "disabled"
    for token in (None, "falsch"):
        response = world.admin.post(SWITCH, {"mode": "ein", "bestaetigt": "1"}, token=token)
        assert response.status_code == 400
    assert _sent() == [] and world.audit(SWITCHED) == []


# === 6. one way to switch ==============================================================================================


def test_the_builder_no_longer_records_a_switch_request(world):
    for mode in ("enforcing", "disabled"):
        response = world.admin.client.post("/builder/api/access/posture", json={"mode": mode},
                                           headers={"X-CSRF-Token": world.admin.token})
        assert response.status_code != 201, mode
    assert world.conn.execute("SELECT count(*) FROM access_posture_request").fetchone()[0] == 0
    assert _sent() == []


# === 7. audit and logs =================================================================================================


def test_audit_rows_and_log_lines_name_people_by_id_only(world, caplog):
    caplog.clear()
    for name in (None, "web_interface", "identity", "knovas_client", "builder"):
        caplog.set_level(logging.DEBUG, logger=name)
    PostureCore.posture = "disabled"
    PostureCore.answer = "refuse:rights_runs_missing"
    world.admin.switch("ein")
    PostureCore.answer = None
    world.admin.switch("ein")
    world.admin.switch("aus")
    world.browser(world.paula).switch("ein")
    world.admin.page()

    rows = world.audit(SWITCHED) + world.audit(REFUSED)
    assert len(rows) == 3
    dump = json.dumps([[r["target_id"], r["detail"]] for r in rows], ensure_ascii=False)
    assert "@" not in dump and "zwiebel" not in dump.lower()
    leaked = []
    for record in caplog.records:
        line = record.getMessage()
        if record.exc_info:
            line += logging.Formatter().formatException(record.exc_info)
        if "zwiebel" in line.lower() or "@kanzlei.ch" in line.lower():
            leaked.append((record.name, record.levelname, line[:200]))
    assert leaked == []
