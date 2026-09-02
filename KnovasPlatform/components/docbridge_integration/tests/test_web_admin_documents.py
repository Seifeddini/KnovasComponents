"""The Dokumente tab: authorised on the route, cursor-fed, wall-respecting."""

from __future__ import annotations

import inspect
import pathlib

import pytest

flask = pytest.importorskip("flask")

TEMPLATES = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src" / "web_interface" / "templates"
)


class _FakeClient:
    def __init__(self, pages=None):
        self._pages = pages or [
            {"documents": [{"pointer": "rc-sync/a.docx", "title": "A",
                            "access_groups": [], "status": "active"}],
             "next_after": None, "total_count": 1}
        ]
        self.acl_writes = []

    def documents(self, **kw):
        return self._pages[0]

    def set_document_access(self, pointer, access_groups, acting_as=None):
        self.acl_writes.append((pointer, list(access_groups)))
        return {"pointer": pointer, "access_groups": list(access_groups)}

    def access_groups(self):
        return [{"group_id": "g-lit", "name": "Litigation", "children": []}]


class TestRouteAuthorisation:
    def test_every_route_is_admin_gated_not_merely_hidden(self):
        from web_interface import admin_documents

        src = inspect.getsource(admin_documents)
        # Count decorated views: each @bp.route must be followed by
        # @require_admin. Hiding a link is presentation; refusing the request
        # is the control.
        assert src.count("@bp.route") == src.count("@require_admin"), (
            "every route must carry @require_admin"
        )

    def test_acl_post_validates_csrf_before_writing(self):
        from web_interface import admin_documents

        src = inspect.getsource(admin_documents)
        idx = src.index("def set_document_acl(")
        body = src[idx:idx + 1200]
        csrf_at = body.index("csrf_ok")
        write_at = body.index("run_guarded(")
        assert csrf_at < write_at, "CSRF must be checked before the write"


class TestNoSystemPrincipal:
    def test_view_never_asks_for_an_unfiltered_listing(self):
        from web_interface import admin_documents

        src = inspect.getsource(admin_documents)
        for forbidden in ("system_principal", "show_all", "bypass"):
            assert forbidden not in src, (
                f"spec D1: walls bind the administrator too; found {forbidden!r}"
            )


class TestDocumentsView:
    def test_page_returns_rows_cursor_and_count(self):
        from web_interface.admin_documents import DocumentsView

        view = DocumentsView(_FakeClient())
        page = view.page()
        assert page["total_count"] == 1
        assert page["next_after"] is None
        assert page["documents"][0]["pointer"] == "rc-sync/a.docx"

    def test_page_forwards_filters_verbatim(self):
        from web_interface.admin_documents import DocumentsView

        class _Recording(_FakeClient):
            def __init__(self):
                super().__init__()
                self.kw = None

            def documents(self, **kw):
                self.kw = kw
                return self._pages[0]

        client = _Recording()
        DocumentsView(client).page(after="x", prefix="rc-sync/m/",
                                   unrestricted=True)
        assert client.kw["after"] == "x"
        assert client.kw["prefix"] == "rc-sync/m/"
        assert client.kw["unrestricted"] is True


class TestTemplate:
    def test_template_exists(self):
        assert (TEMPLATES / "admin_documents.html").is_file()

    def test_every_mutating_form_carries_the_csrf_token(self):
        html = (TEMPLATES / "admin_documents.html").read_text(encoding="utf-8")
        post_forms = html.count('method="post"')
        tokens = html.count('name="csrf_token"')
        assert post_forms > 0
        assert tokens >= post_forms, (
            "every POST form needs a hidden csrf_token"
        )

    def test_list_is_cursor_fed_not_offset_paged(self):
        html = (TEMPLATES / "admin_documents.html").read_text(encoding="utf-8")
        assert "next_after" in html
        assert "page=" not in html, (
            "the inventory pages by cursor; a page number implies an offset"
        )

    def test_count_comes_from_the_backend_aggregate(self):
        html = (TEMPLATES / "admin_documents.html").read_text(encoding="utf-8")
        assert "total_count" in html


class TestConsoleShell:
    """SS-387: one tab strip, shared by every console page; a way in."""

    def test_tab_strip_is_one_partial_included_by_every_console_page(self):
        partial = TEMPLATES / "_admin_tabs.html"
        assert partial.is_file(), "the tab strip must be a shared partial"
        for page in ("admin_people.html", "admin_documents.html"):
            html = (TEMPLATES / page).read_text(encoding="utf-8")
            assert "_admin_tabs.html" in html, f"{page} must include the strip"

    def test_tab_strip_names_every_tab_that_exists(self):
        html = (TEMPLATES / "_admin_tabs.html").read_text(encoding="utf-8")
        for endpoint in ("admin.people", "admin.documents", "admin.access_groups",
                         "admin.approvals", "admin.ingestion"):
            assert endpoint in html

    def test_sidebar_offers_the_console_only_when_told_to(self):
        html = (TEMPLATES / "_sidebar.html").read_text(encoding="utf-8")
        # The link is presentation; require_admin on the route is the control.
        # But a console nobody can navigate to is not a console.
        assert "console_url" in html
        assert "Verwaltung" in html


class TestAccessGroupsTab:
    """Task 6: the Zugriffsgruppen tab and its folder rules."""

    def test_routes_exist_and_are_admin_gated(self):
        from web_interface import admin_documents

        src = inspect.getsource(admin_documents)
        for route in ('"/access-groups"', '"/access-groups/create"',
                      '"/folder-rules/save"', '"/folder-rules/delete"'):
            assert route in src, f"missing route {route}"
        assert src.count("@bp.route") == src.count("@require_admin")

    def test_folder_rule_save_is_csrf_gated(self):
        from web_interface import admin_documents

        src = inspect.getsource(admin_documents)
        idx = src.index("def save_folder_rule(")
        # Skip the def line: its own name would match "folder_rule" first.
        body = src[idx + len("def save_folder_rule("):idx + 1400]
        assert body.index("csrf_ok") < body.index("run_guarded("), (
            "CSRF must be checked before any folder-rule write"
        )

    def test_template_explains_that_a_rule_change_is_cheap(self):
        html = (TEMPLATES / "admin_access_groups.html").read_text(encoding="utf-8")
        assert "Ordnerregel" in html
        assert "sofort" in html.lower() or "unmittelbar" in html.lower()

    def test_every_mutating_form_carries_the_csrf_token(self):
        html = (TEMPLATES / "admin_access_groups.html").read_text(encoding="utf-8")
        post_forms = html.count('method="post"')
        assert post_forms > 0
        assert html.count('name="csrf_token"') >= post_forms


class TestAppWiring:
    """Task 7 and SS-387: the console reaches Knovas through the search
    path's client, and the sidebar offers it only to administrators."""

    def test_app_passes_a_client_factory_to_the_console(self):
        app_py = (TEMPLATES.parent / "app.py").read_text(encoding="utf-8")
        idx = app_py.index("create_admin_blueprint(")
        assert "client_factory" in app_py[idx:idx + 600]

    def test_sidebar_link_is_gated_on_the_admin_role(self):
        app_py = (TEMPLATES.parent / "app.py").read_text(encoding="utf-8")
        idx = app_py.index("def _console_url(")
        body = app_py[idx:idx + 1200]
        assert "'admin'" in body
        assert "url_for('admin.people')" in body


class TestTemplatesRender:
    """Every console template compiles and renders with stub data.

    No PostgreSQL, no Flask app: a bare Jinja environment with a stub
    url_for. This catches a typo in a partial before the People tests,
    which skip without a database, ever get to run.
    """

    @staticmethod
    def _env():
        import jinja2

        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(TEMPLATES)),
            autoescape=True,
            undefined=jinja2.StrictUndefined,
        )
        env.globals["url_for"] = (
            lambda endpoint, **kw: "/" + endpoint.replace(".", "/"))
        return env

    @staticmethod
    def _context(**extra):
        base = {
            "app_title": "Knovas", "company_name": "Kanzlei",
            "feedback_url": None, "console_url": "/admin/people",
            "active_nav": "admin", "csrf_token": "t", "error": None,
            "notice": None, "me": None, "asset_version": "1",
        }
        base.update(extra)
        return base

    def test_tab_strip_renders_and_marks_exactly_one_active_tab(self):
        html = self._env().get_template("_admin_tabs.html").render(
            self._context(admin_tab="documents"))
        assert html.count('aria-current="page"') == 1
        assert html.count('class="active"') == 1

    def test_documents_page_renders(self):
        html = self._env().get_template("admin_documents.html").render(
            self._context(
                documents=[{"pointer": "rc-sync/a.docx", "title": "A",
                            "access_groups": ["g-lit"], "status": "active"}],
                next_after="abc", total_count=1,
                filters={"prefix": None, "group": None, "unrestricted": False,
                         "conflicts": False, "status": None},
                groups=[{"group_id": "g-lit", "name": "Litigation"}],
            ))
        assert "rc-sync/a.docx" in html
        assert "Verwaltung" in html
        assert 'data-next-after="abc"' in html

    def test_access_groups_page_renders(self):
        html = self._env().get_template("admin_access_groups.html").render(
            self._context(
                groups=[{"group_id": "g-lit", "name": "Litigation",
                         "parent_id": None}],
                rules=[{"rule_id": "r1", "pointer_prefix": "rc-sync/m/",
                        "access_groups": ["g-lit"], "version": 3}],
            ))
        assert "rc-sync/m/" in html

    def test_people_page_still_renders_with_the_strip(self):
        html = self._env().get_template("admin_people.html").render(
            self._context(people=[], assignable_roles=["admin", "member"]))
        assert "Zugriffsgruppen</a>" in html


from conftest import DummyKnovasClient, platform_db_reachable


class TestGuardedAclRoutes:
    """SS-392 AC 1, 2, 4 at the route: the ACL actions are four-eyes guarded."""

    def test_every_acl_route_goes_through_run_guarded(self):
        from web_interface import admin_documents

        src = inspect.getsource(admin_documents)
        for fn in ("def set_document_acl(", "def save_folder_rule(",
                   "def delete_folder_rule("):
            body = src[src.index(fn):src.index(fn) + 1600]
            assert "run_guarded(" in body, f"{fn} must go through run_guarded"
            assert body.index("csrf_ok") < body.index("run_guarded("), (
                "CSRF before the guard, always"
            )

    def test_execution_lives_in_one_function_the_approve_path_can_reuse(self):
        from web_interface import admin_documents

        assert callable(getattr(admin_documents, "execute_acl_change", None))
        src = inspect.getsource(admin_documents.execute_acl_change)
        for call in ("set_document_access", "create_folder_rule",
                     "update_folder_rule", "delete_folder_rule"):
            assert call in src


@pytest.mark.skipif(not platform_db_reachable(),
                    reason="No PostgreSQL at the identity test DSN")
class TestGuardedAclRoutesLive:
    @pytest.fixture
    def admin(self, identity_repo):
        user = identity_repo.create(email="chef@kanzlei.ch", display_name="Chef",
                                    password="korrektes-pferd-batterie")
        identity_repo.grant_role(user.id, "admin")
        return identity_repo.get(user.id)

    @pytest.fixture
    def as_admin(self, identity_client, admin):
        from _console import sign_in
        return sign_in(identity_client, "chef@kanzlei.ch")

    def test_with_the_bypass_on_an_admin_acts_and_the_bypass_is_recorded(
        self, as_admin, platform_db
    ):
        from _console import post_form
        from identity import audit

        r = post_form(as_admin, "/admin/documents/acl", page="/admin/documents",
                      pointer="rc-sync/a.docx", access_group="g-lit")
        assert r.status_code == 200
        assert DummyKnovasClient.last_instance.acl_calls == [
            ("set_document_access", "rc-sync/a.docx", ["g-lit"])
        ]
        rows = audit.recent(platform_db, action="approval.bypassed")
        assert rows and rows[0]["target_type"] == "acl_change"

    def test_with_the_bypass_off_the_same_action_is_queued_and_not_run(
        self, as_admin, platform_db, identity_repo, admin
    ):
        from _console import post_form
        from identity.approvals import ApprovalService

        ApprovalService(platform_db, identity_repo).set_admin_bypass(False, by=admin)
        r = post_form(as_admin, "/admin/documents/acl", page="/admin/documents",
                      pointer="rc-sync/a.docx", access_group="g-lit")
        assert r.status_code == 200
        assert "Freigabe" in r.data.decode("utf-8")
        assert DummyKnovasClient.last_instance.acl_calls == []
        pending = ApprovalService(platform_db, identity_repo).pending()
        assert len(pending) == 1 and pending[0].kind == "acl_change"
        assert pending[0].payload["pointers"] == ["rc-sync/a.docx"]
