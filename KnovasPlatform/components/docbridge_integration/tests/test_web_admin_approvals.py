"""The Freigaben tab: a queue for pending requests, and every bypass in view."""

from __future__ import annotations

import inspect
import pathlib
import types

import pytest

flask = pytest.importorskip("flask")

from conftest import DummyKnovasClient, platform_db_reachable

TEMPLATES = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"
)
APP_PY = TEMPLATES.parent / "app.py"


def _logout(client):
    """End the session through the real /logout route.

    /logout exists but is POST-only and CSRF-gated (see src/web_interface/app.py),
    so a persona switch cannot use a bare GET the way _console.sign_in reads a
    GET-rendered login form. The CSRF token is read out of the server-side
    session rather than off a page, since which pages a given persona may even
    load is exactly what some of these tests are checking.
    """
    with client.session_transaction() as sess:
        token = sess.get("csrf_token")
    client.post("/logout", data={"csrf_token": token})


class TestRoutesAreGated:
    def test_every_route_carries_a_role_gate_and_posts_check_csrf_first(self):
        from web_interface import admin_approvals

        src = inspect.getsource(admin_approvals)
        assert src.count("@bp.route") == (
            src.count("@require_approver") + src.count("@require_admin")
        )
        for fn in ("def approve(", "def execute(", "def reject(", "def set_bypass("):
            body = src[src.index(fn):src.index(fn) + 900]
            assert body.index("csrf_ok") < body.index("_approvals()")

    def test_the_bypass_toggle_is_admin_only(self):
        from web_interface import admin_approvals

        src = inspect.getsource(admin_approvals)
        idx = src.index("def set_bypass(")
        assert "@require_admin" in src[idx - 120:idx]


class TestApproverOnlyReachesTheTab:
    """An approver holds no 'admin' role and never reached the console
    before: _console_url must offer Freigaben instead of Personen (finding 4)."""

    def test_console_url_offers_approvals_to_a_pure_approver(self):
        body = APP_PY.read_text(encoding="utf-8")
        idx = body.index("def _console_url(")
        window = body[idx:idx + 1200]
        assert "url_for('admin.approvals')" in window
        assert "'approver'" in window

    def test_tabs_hide_admin_only_pages_for_an_approver(self):
        import jinja2

        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(TEMPLATES)),
            autoescape=True, undefined=jinja2.StrictUndefined,
        )
        env.globals["url_for"] = lambda endpoint, **kw: "/" + endpoint.replace(".", "/")
        me = types.SimpleNamespace(roles={"approver"})
        html = env.get_template("_admin_tabs.html").render(admin_tab="approvals", me=me)
        assert "/admin/people" not in html
        assert "/admin/approvals" in html

    def test_tabs_still_render_when_me_is_none(self):
        """The stub render tests pass me=None; every tab must still appear."""
        import jinja2

        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(TEMPLATES)),
            autoescape=True, undefined=jinja2.StrictUndefined,
        )
        env.globals["url_for"] = lambda endpoint, **kw: "/" + endpoint.replace(".", "/")
        html = env.get_template("_admin_tabs.html").render(admin_tab="approvals", me=None)
        assert "/admin/people" in html
        assert "/admin/approvals" in html


class TestTemplate:
    def test_every_post_form_carries_the_csrf_token(self):
        html = (TEMPLATES / "admin_approvals.html").read_text(encoding="utf-8")
        assert html.count('method="post"') > 0
        assert html.count('name="csrf_token"') >= html.count('method="post"')

    def test_bypasses_are_shown_not_hidden(self):
        html = (TEMPLATES / "admin_approvals.html").read_text(encoding="utf-8")
        assert "bypasses" in html and "Umgehung" in html

    def test_the_strip_knows_the_tab(self):
        assert "admin.approvals" in (TEMPLATES / "_admin_tabs.html").read_text(encoding="utf-8")

    def test_the_approved_section_offers_execute(self):
        html = (TEMPLATES / "admin_approvals.html").read_text(encoding="utf-8")
        assert "noch nicht ausgef\u00fchrt" in html
        assert "admin.execute" in html

    def test_it_renders_with_stub_data(self):
        import jinja2

        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)),
                                 autoescape=True, undefined=jinja2.StrictUndefined)
        env.globals["url_for"] = lambda endpoint, **kw: "/" + endpoint.replace(".", "/")
        html = env.get_template("admin_approvals.html").render(
            app_title="Knovas", company_name="Kanzlei", feedback_url=None,
            console_url="/admin/people", active_nav="admin", csrf_token="t",
            error=None, notice=None, me=None, asset_version="1",
            can_toggle=True, bypass_enabled=True,
            kind_labels={"acl_change": "Zugriffsaenderung"},
            pending=[{"id": "abc", "kind": "acl_change", "kind_label": "Zugriffsaenderung",
                      "target_ref": "rc-sync/a.docx", "requester_email": "x@kanzlei.ch",
                      "requested_at": "2026-09-02 10:00", "expires_at": "2026-09-03 10:00",
                      "summary": "1 Dokument -> g-lit", "mine": False, "executable": True}],
            approved=[{"id": "def", "kind": "acl_change", "kind_label": "Zugriffsaenderung",
                       "target_ref": "rc-sync/c.docx", "requester_email": "y@kanzlei.ch",
                       "approved_at": "2026-09-02 11:00", "summary": "1 Dokument -> g-lit",
                       "executable": True}],
            bypasses=[{"occurred_at": "2026-09-02 09:00", "actor_email": "chef@kanzlei.ch",
                       "target_type": "acl_change", "target_id": "rc-sync/b.docx",
                       "detail": {"result": {"changed": 1}}}],
        )
        assert "rc-sync/a.docx" in html and "chef@kanzlei.ch" in html
        assert "rc-sync/c.docx" in html


class TestIngestionSummaries:
    """I3: an approver confirming an ingestion change saw only the kind label
    -- a folder list, walls, a schedule and a throughput they could not see,
    and a halt they could not tell apart from a profile change."""

    def test_a_stop_reads_as_a_stop(self):
        from web_interface.admin_approvals import _summary

        assert _summary("ingestion_profile_change", {"action": "stop"}) == "Abgleich anhalten"

    def test_a_profile_summary_names_folders_walls_schedule_and_throughput(self):
        from web_interface.admin_approvals import _summary

        payload = {"profile": {
            "sources": [{"path": "/a", "access_groups": ["g-lit"]},
                        {"path": "/b", "access_groups": []},
                        {"path": "/c", "access_groups": ["g-lit", "g-tax"]}],
            "file_types": ["documents", "email"],
            "schedule": "nightly", "throughput": "normal"}}
        assert _summary("ingestion_profile_change", payload) == (
            "3 Ordner (2 mit Zugriffsgruppen), nightly, normal, documents, email")

    def test_the_two_never_read_the_same(self):
        from web_interface.admin_approvals import _summary

        stop = _summary("ingestion_profile_change", {"action": "stop"})
        change = _summary("ingestion_profile_change", {"profile": {"sources": []}})
        assert stop != change

    def test_the_row_says_who_can_carry_it_out(self):
        """An approver who is not an admin cannot execute it; the page must
        say so rather than letting them find out from a failed execution."""
        import jinja2

        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)),
                                 autoescape=True, undefined=jinja2.StrictUndefined)
        env.globals["url_for"] = lambda endpoint, **kw: "/" + endpoint.replace(".", "/")
        row = {"id": "abc", "kind": "ingestion_profile_change",
               "kind_label": "Ingestion-Profil aendern", "target_ref": "ingestion_profile:default",
               "requester_email": "x@kanzlei.ch", "requested_at": "2026-09-02 10:00",
               "expires_at": "2026-09-03 10:00", "approved_at": "2026-09-02 11:00",
               "summary": "2 Ordner (1 mit Zugriffsgruppen), nightly, normal, documents",
               "mine": False, "executable": True}
        acl = {**row, "id": "zzz", "kind": "acl_change", "kind_label": "Zugriffsaenderung",
               "summary": "1 Dokument -> g-lit"}
        html = env.get_template("admin_approvals.html").render(
            app_title="Knovas", company_name="Kanzlei", feedback_url=None,
            console_url="/admin/people", active_nav="admin", csrf_token="t",
            error=None, notice=None, me=None, asset_version="1",
            can_toggle=True, bypass_enabled=False,
            kind_labels={"ingestion_profile_change": "Ingestion-Profil aendern"},
            pending=[row, acl], approved=[row], bypasses=[])
        assert html.count("Ausfuehrung durch admin oder ingestion_manager") == 2, (
            "once on the pending row, once on the approved row, never on the ACL row")


@pytest.mark.skipif(not platform_db_reachable(),
                    reason="No PostgreSQL at the identity test DSN")
class TestLive:
    @pytest.fixture
    def people(self, identity_repo):
        from _console import PASSWORD

        out = {}
        for email, role in (("chef@kanzlei.ch", "admin"), ("pruefer@kanzlei.ch", "approver"),
                            ("anwalt@kanzlei.ch", "member")):
            u = identity_repo.create(email=email, display_name=email.split("@")[0],
                                     password=PASSWORD)
            identity_repo.grant_role(u.id, role)
            out[role] = identity_repo.get(u.id)
        return out

    @pytest.fixture
    def queued(self, identity_client, people, platform_db, identity_repo):
        """An admin with the bypass off queues one ACL change; returns its id."""
        from _console import post_form, sign_in
        from identity.approvals import ApprovalService

        ApprovalService(platform_db, identity_repo).set_admin_bypass(False, by=people["admin"])
        sign_in(identity_client, "chef@kanzlei.ch")
        post_form(identity_client, "/admin/documents/acl", page="/admin/documents",
                  pointer="rc-sync/a.docx", access_group="g-lit")
        _logout(identity_client)
        (req,) = ApprovalService(platform_db, identity_repo).pending()
        return str(req.id)

    def test_who_may_open_it(self, identity_client, people):
        from _console import sign_in

        assert identity_client.get("/admin/approvals").status_code in (302, 303)
        sign_in(identity_client, "anwalt@kanzlei.ch")
        assert identity_client.get("/admin/approvals").status_code == 403
        _logout(identity_client)
        sign_in(identity_client, "pruefer@kanzlei.ch")
        assert identity_client.get("/admin/approvals").status_code == 200

    def test_an_approver_confirms_and_the_change_is_executed(
        self, identity_client, people, queued, platform_db, identity_repo
    ):
        from _console import post_form, sign_in
        from identity.approvals import ApprovalService

        sign_in(identity_client, "pruefer@kanzlei.ch")
        r = post_form(identity_client, f"/admin/approvals/{queued}/approve",
                      page="/admin/approvals")
        assert r.status_code == 200
        assert DummyKnovasClient.last_instance.acl_calls == [
            ("set_document_access", "rc-sync/a.docx", ["g-lit"])
        ]
        row = platform_db.execute(
            "SELECT status, executed_at FROM approval_requests WHERE id = %s", (queued,)
        ).fetchone()
        assert row[0] == "executed" and row[1] is not None

    def test_the_requester_cannot_confirm_their_own(self, identity_client, people, queued):
        from _console import post_form, sign_in

        sign_in(identity_client, "chef@kanzlei.ch")
        r = post_form(identity_client, f"/admin/approvals/{queued}/approve",
                      page="/admin/approvals")
        assert r.status_code == 400
        assert "selbst" in r.data.decode("utf-8")
        assert DummyKnovasClient.last_instance.acl_calls == []

    def test_reject_needs_a_reason_and_keeps_it(
        self, identity_client, people, queued, platform_db
    ):
        from _console import post_form, sign_in

        sign_in(identity_client, "pruefer@kanzlei.ch")
        assert post_form(identity_client, f"/admin/approvals/{queued}/reject",
                         page="/admin/approvals", reason="").status_code == 400
        r = post_form(identity_client, f"/admin/approvals/{queued}/reject",
                      page="/admin/approvals", reason="Falscher Mandant.")
        assert r.status_code == 200
        row = platform_db.execute(
            "SELECT status, decision_reason FROM approval_requests WHERE id = %s", (queued,)
        ).fetchone()
        assert row == ("rejected", "Falscher Mandant.")

    def test_only_an_admin_flips_the_bypass(
        self, identity_client, people, platform_db, identity_repo
    ):
        from _console import post_form, sign_in
        from identity.approvals import ApprovalService

        sign_in(identity_client, "pruefer@kanzlei.ch")
        assert post_form(identity_client, "/admin/approvals/admin-bypass",
                         page="/admin/approvals", enabled="0").status_code == 403
        _logout(identity_client)
        sign_in(identity_client, "chef@kanzlei.ch")
        assert post_form(identity_client, "/admin/approvals/admin-bypass",
                         page="/admin/approvals", enabled="0").status_code == 200
        assert ApprovalService(platform_db, identity_repo).admin_bypass_enabled() is False

    def test_bypasses_appear_in_the_queue_page(self, identity_client, people):
        from _console import post_form, sign_in

        sign_in(identity_client, "chef@kanzlei.ch")
        post_form(identity_client, "/admin/documents/acl", page="/admin/documents",
                  pointer="rc-sync/b.docx", access_group="g-lit")
        html = identity_client.get("/admin/approvals").data.decode("utf-8")
        assert "rc-sync/b.docx" in html and "chef@kanzlei.ch" in html

    def test_a_failed_execution_stays_approved_and_can_be_retried(
        self, identity_client, people, queued, platform_db, identity_repo
    ):
        """An approved request a backend failure left stranded is visible and
        retryable, not silently lost (the gap this fix round closes)."""
        from _console import post_form, sign_in

        DummyKnovasClient.last_instance.fail_next = True
        sign_in(identity_client, "pruefer@kanzlei.ch")
        r = post_form(identity_client, f"/admin/approvals/{queued}/approve",
                      page="/admin/approvals")
        assert r.status_code == 200
        assert "konnten nicht geaendert werden" in r.data.decode("utf-8")
        assert DummyKnovasClient.last_instance.acl_calls == []
        row = platform_db.execute(
            "SELECT status FROM approval_requests WHERE id = %s", (queued,)
        ).fetchone()
        assert row[0] == "approved"

        html = identity_client.get("/admin/approvals").data.decode("utf-8")
        assert "rc-sync/a.docx" in html

        r2 = post_form(identity_client, f"/admin/approvals/{queued}/execute",
                       page="/admin/approvals")
        assert r2.status_code == 200
        row2 = platform_db.execute(
            "SELECT status FROM approval_requests WHERE id = %s", (queued,)
        ).fetchone()
        assert row2[0] == "executed"
        assert DummyKnovasClient.last_instance.acl_calls == [
            ("set_document_access", "rc-sync/a.docx", ["g-lit"])
        ]

    def test_a_request_with_no_executor_appears_without_an_execute_button(
        self, identity_client, people, platform_db, identity_repo
    ):
        from _console import post_form, sign_in
        from identity.approvals import ApprovalService

        req = ApprovalService(platform_db, identity_repo).request(
            people["admin"], kind="bulk_export", target_ref="export-1", payload={}
        )
        sign_in(identity_client, "pruefer@kanzlei.ch")
        r = post_form(identity_client, f"/admin/approvals/{req.id}/approve",
                      page="/admin/approvals")
        assert r.status_code == 200

        html = identity_client.get("/admin/approvals").data.decode("utf-8")
        assert "export-1" in html
        assert f"/admin/approvals/{req.id}/execute" not in html
        assert "Wird von der Konsole nicht ausgef\u00fchrt." in html
