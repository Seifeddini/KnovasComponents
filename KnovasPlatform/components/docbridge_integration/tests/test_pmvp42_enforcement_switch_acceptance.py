"""PMVP-42 acceptance tests: the enforcement position shown, and the switch with a second administrator
(FEAT-2026-11-19-platform-mvp § 4 A-06, § 8.1, spec item SPEC-platform-mvp-14; DEC-0082 "a real pilot with
enforcement on", never cut; work order docs/platform-mvp/work-orders/PMVP-42.md; Core requests CR-R1, CR-R1a and CR-R7
in docs/platform-mvp/core-requests.md).

FROZEN once the coordinator pins it. Written for the coordinator before the build; a builder never edits this file.
Its git blob id goes into docs/platform-mvp/frozen-tests.txt.

The Platform never switches a tenant itself: the posture is set by Knovas staff through the Core's internal admin API
(``PUT /admin/clients/<id>/rbac-enforcement``), which the Platform cannot call. What the Platform does is show the
position it can read, and record a request to switch that a second administrator approves, so that Knovas carries out
only a decision two of the firm's administrators made, and «Ein» only after two green end-to-end rights runs (§ 8.1).

What is pinned (the sections below carry the same numbers):

1. Storage. The requests live in new tables only: no table that existed before (migrations up to ``0028_agents``)
   changes shape, and no new column holds a key, secret, hash or token.
2. The position. ``GET /admin/access`` says in one sentence which position is active, on an element
   ``data-enforcement-position``: ``ein`` when the client reports ``rbac_posture() == {"mode": "brokered"}``, ``aus``
   for ``disabled``, ``teilweise`` for ``enforcing`` (the Core filters but ignores the Platform's claim), and
   ``unbekannt`` when the client has no such call (the real client before CR-R1a), it fails or it reports anything
   else. «Probe» is not offered (it needs CR-R4). Opening the page writes nothing.
3. The request and the § 8.1 gate. ``POST /admin/access/enforcement/request`` (``mode`` = ``ein`` | ``aus``). A
   request for «Ein» is refused (409, nothing stored) unless the installation's configuration lists at least two
   distinct runs of the Core's end-to-end rights test with ``result: green``, ``posture: brokered`` and a ``commit``
   (``enforcement.rights_runs``); the page shows them (``data-rights-run``). A stored request waits
   (``data-enforcement-request``, ``data-mode``, ``data-status="wartet"``) and is audited by id. The administrators'
   four-eyes bypass (``approvals.admin_bypass``) does not apply. One open request at a time. A request for the
   position the Core already reports, or for «Ein» while the Core reports its backfill incomplete, is refused.
4. The second administrator. ``POST /admin/access/enforcement/<id>/approve``: never the requester, never an approver
   or a member, only another administrator; the gate is checked again. Approved, the request says Knovas carries it
   out (``data-status="freigegeben"``); nothing is sent to the Core and the position stays what the Core reports.
   ``POST /admin/access/enforcement/<id>/cancel``: any administrator withdraws an open request. «Aus» needs a second
   administrator too, but no rights runs.
5. Gates and tokens. The three routes admit administrators only; each checks ``csrf_token``.
6. One way to ask. The Builder's old switch request (``POST /builder/api/access/posture``, which asked for
   ``enforcing``) records nothing any more.
7. Audit and logs. Audit rows name people by id only; no name or address reaches a log line, DEBUG included.

Nothing reaches a tenant: the Core is a recording fake.
"""
from __future__ import annotations

import json
import logging
import re
import uuid

import pytest
import requests

import conftest
from conftest import PLATFORM_DB_TEST_DSN, FakeGraphApi, platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity import migrate  # noqa: E402
from identity.approvals import ApprovalService  # noqa: E402
from identity.users import UserRepository  # noqa: E402

PAGE = "/admin/access"
REQUEST = "/admin/access/enforcement/request"
BEFORE = "0028_agents"
REQUESTED, APPROVED, CANCELLED = "enforcement.requested", "enforcement.approved", "enforcement.cancelled"


def APPROVE(request_id):                                             # noqa: N802 - an address, like a constant
    return f"/admin/access/enforcement/{request_id}/approve"


def CANCEL(request_id):                                              # noqa: N802
    return f"/admin/access/enforcement/{request_id}/cancel"


def _runs(*entries):
    lines = ["enforcement:", "  rights_runs:"]
    for entry in entries:
        lines.append("    - {" + ", ".join(f"{k}: {json.dumps(v)}" for k, v in entry.items()) + "}")
    return "\n".join(lines) + "\n"


RUN_A = {"run": "9100001", "date": "2026-12-01", "commit": "aaaa111", "posture": "brokered", "result": "green"}
RUN_B = {"run": "9100002", "date": "2026-12-02", "commit": "bbbb222", "posture": "brokered", "result": "green"}
TWO_GREEN = _runs(RUN_A, RUN_B)


# --- the Core: a recording fake ----------------------------------------------------------------------------------------

_WRITE = re.compile(r"^(graph_)?(create|rename|move|delete|set|update|put|patch|post|install|deprecate|retire|"
                    r"upload|purge|push|start|stop|requeue|reextract|apply|grant|revoke|switch|enforce)(_|$)")


class PostureCore(FakeGraphApi):
    """The Core as the page sees it. ``posture``: what ``rbac_posture()`` reports (None: no such call, the real client
    before CR-R1a; "raise": it fails; else the mode). ``backfill``: whether the Core reports its backfill complete.
    Every call whose name writes, or names the posture or enforcement, is recorded."""

    calls: list = []
    posture = None
    backfill = True

    def __getattribute__(self, name):
        if name == "rbac_posture" and PostureCore.posture is None:
            raise AttributeError(name)
        attr = object.__getattribute__(self, name)
        if callable(attr) and (_WRITE.match(name) or (("posture" in name or "enforc" in name) and name != "rbac_posture")):
            def recorded(*args, **kwargs):
                PostureCore.calls.append(name)
                return attr(*args, **kwargs)
            return recorded
        return attr

    def rbac_posture(self):
        if PostureCore.posture == "raise":
            raise requests.exceptions.ConnectionError("unreachable")
        if PostureCore.posture == "not-a-dict":
            return "brokered"
        return {"mode": PostureCore.posture, "backfill_complete": PostureCore.backfill}


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


def _position(html):
    found = re.findall(r'<([a-z0-9]+)[^>]*\sdata-enforcement-position="([^"]*)"[^>]*>(.*?)</\1>', html, re.S)
    assert len(found) == 1, f"one position sentence, found {len(found)}"
    _, value, inner = found[0]
    text = re.sub(r"<[^>]+>", " ", inner).strip()
    assert text, "the position needs its sentence"
    return value, " ".join(text.split())


def _open_requests(html):
    """(id, mode, status, its text) of every request on the page that waits or is approved. A request's text runs from
    its element to the next request's element (or at most 3,000 characters), so its mark-up may nest freely."""
    out = []
    tags = list(re.finditer(r'<[a-z0-9]+(\s[^>]*data-enforcement-request="[^"]*"[^>]*)>', html))
    for n, match in enumerate(tags):
        attrs = match.group(1)
        request_id = re.search(r'data-enforcement-request="([^"]*)"', attrs).group(1)
        mode = re.search(r'data-mode="([^"]*)"', attrs)
        status = re.search(r'data-status="([^"]*)"', attrs)
        end = tags[n + 1].start() if n + 1 < len(tags) else len(html)
        inner = html[match.end(): min(end, match.end() + 3000)]
        if status and status.group(1) in ("wartet", "freigegeben"):
            out.append((request_id, mode.group(1) if mode else None, status.group(1),
                        " ".join(re.sub(r"<[^>]+>", " ", inner).split())))
    return out


class World:
    def __init__(self, conn, app):
        self.conn, self.app = conn, app
        self.repo = UserRepository(conn)
        self.chef = self.person("chef", "admin", display_name="Chef Zwiebel")
        self.zweit = self.person("zweit", "admin", display_name="Zweit Zwiebel")
        self.paula = self.person("paula", "approver", display_name="Paula Zwiebel")
        self.mia = self.person("mia", "member", display_name="Mia Zwiebel")
        self.as_chef, self.as_zweit = Browser(app, self.chef.email), Browser(app, self.zweit.email)

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

    def open(self, browser=None):
        return _open_requests((browser or self.as_chef).page())

    def request(self, mode="ein", browser=None):
        return (browser or self.as_chef).post(REQUEST, {"mode": mode})


def _app(conn, tmp_path, monkeypatch, extra_yaml=""):
    return conftest._identity_app(conn, tmp_path, monkeypatch, client_cls=PostureCore, extra_yaml=extra_yaml)


def _reset():
    PostureCore.calls = []
    PostureCore.posture = None
    PostureCore.backfill = True


@pytest.fixture
def world(platform_db, tmp_path, monkeypatch):
    """Two green brokered runs configured; the posture unknown (the client cannot read it)."""
    _reset()
    return World(platform_db, _app(platform_db, tmp_path, monkeypatch, TWO_GREEN))


@pytest.fixture
def bare(platform_db, tmp_path, monkeypatch):
    """No rights runs configured."""
    _reset()
    return World(platform_db, _app(platform_db, tmp_path, monkeypatch))


# === 1. storage ========================================================================================================


def _shape(conn):
    shape: dict[str, list] = {}
    queries = (
        "SELECT table_name, 'column', column_name || ' ' || data_type || ' ' || is_nullable || ' ' || "
        "coalesce(column_default, '') FROM information_schema.columns WHERE table_schema = current_schema()",
        "SELECT tablename, 'index', indexdef FROM pg_indexes WHERE schemaname = current_schema()",
        "SELECT c.conrelid::regclass::text, 'constraint', c.conname || ' ' || pg_get_constraintdef(c.oid) "
        "FROM pg_constraint c WHERE c.connamespace = current_schema()::regnamespace AND c.conrelid <> 0",
        "SELECT event_object_table, 'trigger', trigger_name || ' ' || event_manipulation || ' ' || action_statement "
        "FROM information_schema.triggers WHERE trigger_schema = current_schema()",
    )
    for query in queries:
        for table, kind, text in conn.execute(query).fetchall():
            shape.setdefault(str(table).split(".")[-1].strip('"'), []).append((kind, text))
    return {table: sorted(items) for table, items in shape.items()}


@pytest.fixture
def before_switch():
    import psycopg

    schema = f"t{uuid.uuid4().hex[:12]}"
    with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as setup:
        setup.execute(f'CREATE SCHEMA "{schema}"')
    conn = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True)
    conn.execute(f'SET search_path TO "{schema}"')
    try:
        migrate.apply(conn, [m for m in migrate.discover() if m.version <= BEFORE])
        yield conn
    finally:
        conn.close()
        with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as cleanup:
            cleanup.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_requests_live_in_new_tables_only_and_hold_no_credential(before_switch):
    before = _shape(before_switch)
    migrate.apply(before_switch)
    after = _shape(before_switch)
    assert {table: after.get(table) for table in before} == before
    added = set(after) - set(before)
    assert added, "the requests need a table of their own"
    columns = [text.split(" ")[0] for table in added for kind, text in after[table] if kind == "column"]
    assert not [c for c in columns if any(w in c.lower() for w in ("key", "secret", "hash", "token"))], columns


# === 2. the position ===================================================================================================


def test_the_page_says_in_one_sentence_which_position_is_active(world):
    expected = ((None, "unbekannt"), ("raise", "unbekannt"), ("disabled", "aus"), ("enforcing", "teilweise"),
                ("brokered", "ein"), ("probe", "unbekannt"), ("not-a-dict", "unbekannt"))
    for posture, value in expected:
        PostureCore.posture = posture
        shown, text = _position(world.as_chef.page())
        assert shown == value, (posture, shown)
        if value == "aus":
            assert "«Aus»" in text
        if value == "ein":
            assert "«Ein»" in text


def test_probe_is_not_offered_and_opening_the_page_writes_nothing(world):
    html = world.as_chef.page()
    forms = [part[: part.index("</form>")] for part in html.split(f'action="{REQUEST}"')[1:]]
    assert forms, "no form asks for a switch"
    assert all(re.search(r'name="mode"', form) for form in forms)
    offered = {v for form in forms for v in re.findall(r'value="([^"]*)"', form) if v in ("ein", "aus", "probe")}
    assert offered == {"ein", "aus"}                                 # the position is unknown: both, never Probe
    assert "probe" not in " ".join(forms).lower()
    assert PostureCore.calls == [] and _open_requests(html) == []
    assert world.audit(REQUESTED) == []


# === 3. the request and the § 8.1 gate =================================================================================


@pytest.mark.parametrize("runs", [
    "",
    _runs(RUN_A),
    _runs(RUN_A, dict(RUN_B, run=RUN_A["run"])),
    _runs(RUN_A, dict(RUN_B, result="red")),
    _runs(RUN_A, dict(RUN_B, posture="enforcing")),
    _runs(RUN_A, {k: v for k, v in RUN_B.items() if k != "commit"}),
], ids=["none", "one", "the-same-run-twice", "one-red", "one-not-brokered", "one-without-commit"])
def test_ein_is_refused_without_two_green_brokered_runs(platform_db, tmp_path, monkeypatch, runs):
    _reset()
    world = World(platform_db, _app(platform_db, tmp_path, monkeypatch, runs))
    response = world.request("ein")
    assert response.status_code == 409
    assert world.open() == [] and world.audit(REQUESTED) == []
    assert PostureCore.calls == []


def test_with_two_green_runs_ein_waits_for_a_second_administrator(world):
    response = world.request("ein")
    assert response.status_code == 200
    html = world.as_chef.page()
    [(request_id, mode, status, _)] = _open_requests(html)
    assert (mode, status) == ("ein", "wartet")
    assert {"9100001", "9100002"} <= set(re.findall(r'data-rights-run="([^"]*)"', html))
    [row] = world.audit(REQUESTED)
    assert (row["target_type"], row["target_id"], row["actor"]) == ("enforcement", request_id, str(world.chef.id))
    assert row["detail"]["mode"] == "ein"
    assert _position(html)[0] == "unbekannt"                         # a request switches nothing
    assert PostureCore.calls == []


def test_the_administrators_bypass_does_not_apply(world):
    ApprovalService(world.conn, world.repo).set_admin_bypass(True, by=world.chef)
    assert world.request("ein").status_code == 200
    [(_, _, status, _)] = world.open()
    assert status == "wartet"
    assert world.audit("approval.bypassed") == [] and world.audit(APPROVED) == []
    assert world.conn.execute("SELECT count(*) FROM approval_requests").fetchone()[0] == 0


def test_one_open_request_at_a_time(world):
    assert world.request("ein").status_code == 200
    assert world.request("ein").status_code == 409
    assert world.request("aus", world.as_zweit).status_code == 409
    [(request_id, _, _, _)] = world.open()
    assert world.as_zweit.post(APPROVE(request_id)).status_code == 200
    assert world.request("aus").status_code == 409                   # approved, not yet carried out: still open
    assert len(world.open()) == 1 and len(world.audit(REQUESTED)) == 1


def test_the_position_already_active_or_an_incomplete_backfill_is_refused(world):
    PostureCore.posture = "brokered"
    assert world.request("ein").status_code == 409
    PostureCore.posture = "disabled"
    assert world.request("aus").status_code == 409
    PostureCore.backfill = False
    assert world.request("ein").status_code == 409
    assert world.open() == [] and world.audit(REQUESTED) == []


def test_a_bad_mode_is_refused(world):
    for mode in ("", "probe", "enforcing", "brokered", "EIN"):
        assert world.request(mode).status_code == 400, mode
    assert world.open() == []


# === 4. the second administrator =======================================================================================


def test_the_requester_cannot_approve_her_own_request(world):
    world.request("ein")
    [(request_id, _, _, _)] = world.open()
    assert world.as_chef.post(APPROVE(request_id)).status_code == 403
    assert [r[2] for r in world.open()] == ["wartet"] and world.audit(APPROVED) == []


def test_an_approver_and_a_member_cannot_approve(world):
    world.request("ein")
    [(request_id, _, _, _)] = world.open()
    for someone in (world.paula, world.mia):
        assert world.browser(someone).post(APPROVE(request_id)).status_code == 403
    assert [r[2] for r in world.open()] == ["wartet"] and world.audit(APPROVED) == []


def test_a_second_administrator_approves_and_knovas_carries_it_out(world):
    world.request("ein")
    [(request_id, _, _, _)] = world.open()
    assert world.as_zweit.post(APPROVE(request_id)).status_code == 200
    html = world.as_chef.page()
    [(same, mode, status, text)] = _open_requests(html)
    assert (same, mode, status) == (request_id, "ein", "freigegeben")
    assert "Knovas" in text                                          # who carries it out
    [row] = world.audit(APPROVED)
    assert (row["target_type"], row["target_id"], row["actor"]) == ("enforcement", request_id, str(world.zweit.id))
    assert _position(html)[0] == "unbekannt"                         # the Platform reports what the Core reports
    assert PostureCore.calls == []                                   # nothing was sent to the Core
    assert world.as_zweit.post(APPROVE(request_id)).status_code == 409


def test_the_approval_checks_the_gate_again(platform_db, tmp_path, monkeypatch):
    _reset()
    first = World(platform_db, _app(platform_db, tmp_path, monkeypatch, TWO_GREEN))
    assert first.request("ein").status_code == 200
    [(request_id, _, _, _)] = first.open()
    (tmp_path / "later").mkdir()
    later = _app(platform_db, tmp_path / "later", monkeypatch, _runs(RUN_A))    # a run withdrawn since
    as_zweit = Browser(later, first.zweit.email)
    assert as_zweit.post(APPROVE(request_id)).status_code == 409
    assert [r[2] for r in _open_requests(as_zweit.page())] == ["wartet"] and first.audit(APPROVED) == []


def test_aus_needs_a_second_administrator_but_no_rights_runs(bare):
    PostureCore.posture = "brokered"
    assert bare.request("aus").status_code == 200
    [(request_id, mode, status, _)] = bare.open()
    assert (mode, status) == ("aus", "wartet")
    assert bare.as_chef.post(APPROVE(request_id)).status_code == 403
    assert bare.as_zweit.post(APPROVE(request_id)).status_code == 200
    assert [r[2] for r in bare.open()] == ["freigegeben"]
    assert PostureCore.calls == []


def test_any_administrator_withdraws_an_open_request(world):
    world.request("ein")
    [(request_id, _, _, _)] = world.open()
    assert world.browser(world.mia).post(CANCEL(request_id)).status_code == 403
    assert world.as_zweit.post(CANCEL(request_id)).status_code == 200
    assert world.open() == []
    [row] = world.audit(CANCELLED)
    assert (row["target_id"], row["actor"]) == (request_id, str(world.zweit.id))
    assert world.as_zweit.post(APPROVE(request_id)).status_code == 409
    assert world.request("ein").status_code == 200                   # a new request may follow
    [(again, _, _, _)] = world.open()
    assert world.as_zweit.post(APPROVE(again)).status_code == 200
    assert world.as_chef.post(CANCEL(again)).status_code == 200      # approved, not carried out: may be withdrawn
    assert world.open() == []


def test_an_unknown_request_is_not_found(world):
    for request_id in (str(uuid.uuid4()), "keine-id"):
        assert world.as_zweit.post(APPROVE(request_id)).status_code == 404
        assert world.as_zweit.post(CANCEL(request_id)).status_code == 404


# === 5. gates and tokens ===============================================================================================


def test_the_switch_routes_admit_administrators_only(world):
    adapter = world.app.url_map.bind("localhost")
    for path in (REQUEST, APPROVE(uuid.uuid4()), CANCEL(uuid.uuid4())):
        endpoint, _ = adapter.match(path, method="POST")
        assert getattr(world.app.view_functions[endpoint], "allowed_roles", None) == frozenset({"admin"}), path


def test_signed_out_a_member_and_an_approver_cannot_request(world):
    anonymous = world.app.test_client()
    response = anonymous.post(REQUEST, data={"mode": "ein"})
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    for someone in (world.mia, world.paula):
        assert world.browser(someone).post(REQUEST, {"mode": "ein"}).status_code == 403
        assert world.browser(someone).client.get(PAGE).status_code == 403
    assert world.open() == [] and world.audit(REQUESTED) == []


def test_each_form_checks_its_token(world):
    for token in (None, "falsch"):
        assert world.as_chef.post(REQUEST, {"mode": "ein"}, token=token).status_code == 400
    assert world.open() == []
    world.request("ein")
    [(request_id, _, _, _)] = world.open()
    for token in (None, "falsch"):
        assert world.as_zweit.post(APPROVE(request_id), token=token).status_code == 400
        assert world.as_zweit.post(CANCEL(request_id), token=token).status_code == 400
    assert [r[2] for r in world.open()] == ["wartet"]
    assert world.audit(APPROVED) == [] and world.audit(CANCELLED) == []


# === 6. one way to ask =================================================================================================


def test_the_builder_no_longer_records_a_switch_request(world):
    for mode in ("enforcing", "disabled"):
        response = world.as_chef.client.post("/builder/api/access/posture", json={"mode": mode},
                                             headers={"X-CSRF-Token": world.as_chef.token})
        assert response.status_code != 201, mode
    assert world.conn.execute("SELECT count(*) FROM access_posture_request").fetchone()[0] == 0
    assert world.open() == []


# === 7. audit and logs =================================================================================================


def test_audit_rows_and_log_lines_name_people_by_id_only(world, caplog):
    caplog.clear()
    for name in (None, "web_interface", "identity", "knovas_client", "builder"):
        caplog.set_level(logging.DEBUG, logger=name)
    world.request("ein")
    [(request_id, _, _, _)] = world.open()
    world.as_chef.post(APPROVE(request_id))                          # refused: her own
    world.browser(world.paula).post(APPROVE(request_id))             # refused: not an administrator
    world.as_zweit.post(APPROVE(request_id))
    world.as_chef.post(CANCEL(request_id))
    world.request("aus", world.as_zweit)
    world.as_chef.page()

    rows = world.audit(REQUESTED) + world.audit(APPROVED) + world.audit(CANCELLED)
    assert len(rows) == 4
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
