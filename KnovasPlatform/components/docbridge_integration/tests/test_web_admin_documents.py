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

    def test_bulk_selection_has_one_shared_toolbar_and_select_all_control(self):
        html = (TEMPLATES / "admin_documents.html").read_text(encoding="utf-8")
        script = (
            TEMPLATES.parent / "static" / "js" / "admin_documents.js"
        ).read_text(encoding="utf-8")
        assert 'id="document-bulk-bar"' in html
        assert 'id="document-selected-count"' in html
        assert 'id="document-select-all"' in html
        assert 'id="document-clear-selection"' in html
        assert "syncSelection" in script


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
                         "admin.approvals", "admin.ingestion", "admin.doc_fields"):
            assert endpoint in html

    def test_the_doc_fields_tab_is_drawn_only_while_knovas_offers_them(self):
        """H6: no tab for a feature the server does not have. The capability
        reaches the strip through the blueprint's context processor."""
        import jinja2

        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)),
                                 autoescape=True, undefined=jinja2.StrictUndefined)
        env.globals["url_for"] = lambda endpoint, **kw: "/" + endpoint.replace(".", "/")

        class Cap:
            def __init__(self, on):
                self.shows_values = on

        strip = env.get_template("_admin_tabs.html")
        assert "Dokumentfelder" not in strip.render(admin_tab="people")
        assert "Dokumentfelder" not in strip.render(admin_tab="people",
                                                    doc_fields_capability=Cap(False))
        shown = strip.render(admin_tab="doc_fields", doc_fields_capability=Cap(True))
        assert "/admin/doc_fields" in shown and shown.count('aria-current="page"') == 1

        class Approver:
            roles = ("approver",)

        def explode():
            raise AssertionError("an approver's strip must not ask for the capability")

        class Boom:
            @property
            def shows_values(self):
                explode()

        assert "Dokumentfelder" not in strip.render(admin_tab="approvals", me=Approver(),
                                                    doc_fields_capability=Boom())

    def test_sidebar_offers_the_console_only_when_told_to(self):
        html = (TEMPLATES / "_sidebar.html").read_text(encoding="utf-8")
        # The link is presentation; require_admin on the route is the control.
        # But a console nobody can navigate to is not a console.
        assert "console_url" in html
        assert "Verwaltung" in html

    def test_every_console_page_uses_the_scoped_admin_shell(self):
        pages = (
            "admin_people.html", "admin_documents.html",
            "admin_access_groups.html", "admin_ingestion.html",
            "admin_system.html", "admin_approvals.html",
        )
        for page in pages:
            html = (TEMPLATES / page).read_text(encoding="utf-8")
            assert '<body class="admin-page">' in html, page
            assert html.index("<h1>") < html.index("_admin_tabs.html"), page

    def test_people_page_has_one_master_detail_view(self):
        html = (TEMPLATES / "admin_people.html").read_text(encoding="utf-8")
        assert 'id="people-table"' in html
        assert "data-person-detail" in html
        assert 'id="person-create-dialog"' in html
        assert "admin_people.js" in html


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

    def test_child_groups_are_listed_with_their_parent_name(self):
        html = self._env().get_template("admin_access_groups.html").render(
            self._context(
                groups=[
                    {"group_id": "g-ra", "name": "Rechtsanwalt", "parent_id": None},
                    {"group_id": "g-bo", "name": "Backoffice", "parent_id": "g-ra",
                     "parent_name": "Rechtsanwalt"},
                ],
                rules=[],
            ))
        assert "Backoffice" in html
        assert "Rechtsanwalt" in html

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


def test_documents_view_only_passes_arguments_the_real_client_accepts():
    """The console tests run against a fake client that takes **kw, so a
    keyword the real KnovasAPIClient.documents() does not accept would only
    surface in production, as a TypeError on every page load. Pin the
    contract: every parameter of DocumentsView.page must exist on the client."""
    import inspect
    from knovas_client import KnovasAPIClient
    from web_interface.admin_documents import DocumentsView

    accepted = set(inspect.signature(KnovasAPIClient.documents).parameters) - {"self"}
    used = set(inspect.signature(DocumentsView.page).parameters) - {"self"}
    assert used <= accepted, sorted(used - accepted)



# ---------------------------------------------------------------------------
# Document fields: the drawer and the Feldfilter (spec 4.7)
# ---------------------------------------------------------------------------

import json as _json

from doc_fields_fakes import FakeDocFieldsApi

POINTER = "kanzlei/Mandate/Sentinel Muster AG/vertrag.pdf"


def _csrf(client):
    with client.session_transaction() as sess:
        return sess["csrf_token"]


def _post_json(client, path, body, *, csrf=True):
    headers = {"X-CSRF-Token": _csrf(client)} if csrf else {}
    return client.post(path, json=body, headers=headers)


def _df_calls(api, name):
    return [args for method, args in api.doc_calls if method == name]


class TestDocFieldsPure:
    def _registry(self, *extra):
        from doc_fields_fakes import core_fields, legal_ch_fields
        from doc_fields_view import sanitize_registry

        return sanitize_registry(core_fields() + [f for f, _ in legal_ch_fields()] + list(extra))

    def test_values_view_shows_layers_badges_and_the_manual_time(self):
        from web_interface.admin_doc_fields import values_view

        raw = {
            "pointer": "k/a.pdf", "document_uuid": "u1", "version": 3, "title": "A",
            "title_source": "manual", "description": None,
            "fields": {"doc_type": "invoice", "party": [{"name": "Muster AG"}, {"hidden": True}]},
            "layers": {
                "doc_type": [
                    {"layer": "manual", "value": "invoice", "verified": True, "effective": True,
                     "source_ref": None, "created_at": "2026-09-30T08:00:00Z"},
                    {"layer": "rule", "value": "contract", "verified": False, "effective": False,
                     "source_ref": "rule:r1", "created_at": "2026-09-01T08:00:00Z"}],
                "party": [{"layer": "upload", "value": [{"name": "Muster AG"}, {"hidden": True}],
                           "verified": False, "effective": True, "source_ref": "transmission:t",
                           "created_at": "2026-09-01T08:00:00Z"}],
            },
            "warnings": ["unresolved_entity"],
        }
        view = values_view(raw, self._registry(), roles=["admin"], edit_roles={"admin"})
        fields = {f["key"]: f for f in view["fields"]}
        assert fields["doc_type"]["text"] == "Rechnung"
        assert fields["doc_type"]["layer_label"] == "Manuell"
        assert fields["doc_type"]["changed_at"] == "2026-09-30T08:00:00Z"
        assert [layer["layer_label"] for layer in fields["doc_type"]["layers"]] == [
            "Manuell", "Ordnervorgabe"]
        assert fields["party"]["text"] == "Muster AG; verborgen"
        assert fields["party"]["changed_at"] is None
        inputs = {i["key"]: i for i in view["inputs"]}
        assert inputs["doc_type"]["kind"] == "select" and inputs["doc_type"]["value"] == "invoice"
        assert inputs["party"]["locked"] is True
        assert inputs["privileged"]["kind"] == "bool"
        assert "Kennzeichnung" in inputs["privileged"]["hint"]
        assert "title" in view["editable_keys"] and "patient" in view["editable_keys"]
        assert view["warnings"] == [{"code": "unresolved_entity", "text": "nicht verkn\u00fcpft"}]

    def test_a_held_document_is_read_only(self):
        from web_interface.admin_doc_fields import values_view

        view = values_view({"pointer": "k/a.pdf", "acl_mode": "quarantined", "version": 2},
                           self._registry(), roles=["admin"], edit_roles={"admin"})
        assert view["held"] is True and view["can_edit"] is False
        assert view["fields"] == [] and "zur\u00fcckgehalten" in view["message"]

    def test_who_may_edit(self):
        from web_interface.admin_doc_fields import editable_keys

        registry = self._registry()
        assert editable_keys(registry, roles=["admin"], edit_roles={"member"}) == []
        member = editable_keys(registry, roles=["member"], edit_roles={"member"})
        assert "doc_type" in member and "patient" not in member and "title" in member

    def test_edit_ops_keep_to_what_the_person_may_change(self):
        from web_interface.admin_doc_fields import FormError, edit_ops_from_body

        registry = self._registry()
        ops = edit_ops_from_body({"set": {"doc_type": None, "title": "Neu"},
                                  "unset": ["status"], "add": {"party": "Beispiel GmbH"}},
                                 registry, roles=["admin"], edit_roles={"admin"})
        assert ops == {"set": {"doc_type": None, "title": "Neu"}, "unset": ["status"],
                       "add": {"party": ["Beispiel GmbH"]}}
        for body in ({"set": {"patient": "x"}}, {"set": {"path": "x"}},
                     {"unset": ["title"]}, {"add": {"doc_type": ["invoice"]}},
                     {"set": {"doc_type": {"nested": True}}}, {"set": {"title": ""}},
                     {"set": "doc_type"}):
            with pytest.raises(FormError):
                edit_ops_from_body(body, registry, roles=["member"], edit_roles={"member"})

    def test_the_audit_detail_carries_no_value(self):
        from doc_fields_view import values_edit_audit_detail

        detail = values_edit_audit_detail(
            {"set": {"title": "Sentinel", "counterparty": "Sentinel AG"}, "unset": ["status"]},
            version_from=1, version_to=2, warning_codes=["unresolved_entity"])
        assert detail == {"keys": ["counterparty", "status"],
                          "ops": {"set": 2, "unset": 1, "add": 0, "remove": 0},
                          "title_changed": True, "description_changed": False,
                          "version_from": 1, "version_to": 2,
                          "warning_codes": ["unresolved_entity"]}
        assert "Sentinel" not in _json.dumps(detail)

    def test_where_from_pairs_sends_names_never_node_ids(self):
        from web_interface.admin_doc_fields import FormError, where_from_pairs

        registry = self._registry()
        where = where_from_pairs([
            {"field": "mandant", "value": " Muster AG "},
            {"field": "mandant", "value": "Beispiel GmbH"},
            {"field": "doc_type", "value": "invoice"},
            {"field": "privileged", "value": "true"},
            {"field": "legal_area", "value": ""},
        ], registry)
        assert where == {"mandant": [{"name": "Muster AG"}, {"name": "Beispiel GmbH"}],
                         "doc_type": "invoice", "privileged": True}
        assert "node_id" not in _json.dumps(where)
        with pytest.raises(FormError):
            where_from_pairs([], registry)
        with pytest.raises(FormError):
            where_from_pairs([{"field": "nope", "value": "x"}], registry)

    def test_sort_only_by_a_date(self):
        from web_interface.admin_doc_fields import FormError, sort_from_body

        registry = self._registry()
        assert sort_from_body({"field": "deadline", "order": "desc"}, registry) == {
            "field": "deadline", "order": "desc"}
        assert sort_from_body(None, registry) is None
        with pytest.raises(FormError):
            sort_from_body({"field": "doc_type"}, registry)

    def test_the_drawer_script_builds_no_url_with_a_pointer(self):
        js = (TEMPLATES.parent / "static" / "js" / "admin_documents.js").read_text(encoding="utf-8")
        assert "innerHTML" not in js and "insertAdjacentHTML" not in js
        assert "searchParams.set('pointer'" not in js and "?pointer=" not in js
        assert "'X-CSRF-Token': csrf" in js


@pytest.mark.skipif(not platform_db_reachable(),
                    reason="No PostgreSQL at the identity test DSN")
class TestDocFieldsLive:
    @pytest.fixture
    def admin(self, identity_repo):
        from conftest import _person

        return _person(identity_repo, "chef@kanzlei.ch", "Chef", "admin")

    @pytest.fixture
    def build(self, platform_db, tmp_path, monkeypatch):
        from conftest import _identity_app

        def make(mode="values"):
            return _identity_app(platform_db, tmp_path, monkeypatch,
                                 client_cls=FakeDocFieldsApi.bind(mode))
        return make

    def _client(self, app, email="chef@kanzlei.ch"):
        from _console import sign_in

        client = app.test_client()
        sign_in(client, email)
        return client

    def _seed(self, api):
        api.install_doc_field_pack("legal_ch")
        api.add_document(POINTER, title="Vertrag", fields={"doc_type": "contract",
                                                          "mandant": {"name": "Muster AG"}})
        api.doc_calls.clear()

    # -- the documents page --------------------------------------------------

    def test_the_page_offers_the_drawer_only_with_the_capability(self, build, admin):
        rows = {"documents": [{"pointer": POINTER, "pointers": [POINTER, "kanzlei/b.pdf"],
                               "title": "Vertrag", "access_groups": [], "status": "active"}],
                "next_after": None, "total_count": 1}
        for mode, drawer, listing in (("off", False, False), ("values", True, False),
                                      ("filters", True, True)):
            app = build(mode)
            FakeDocFieldsApi.current.documents = lambda **kw: rows
            html = self._client(app).get("/admin/documents").data.decode("utf-8")
            assert ("data-df-open" in html) is drawer, mode
            assert ('id="doc-fields-drawer"' in html) is drawer, mode
            assert ('id="doc-fields-filter"' in html) is listing, mode
            if drawer:
                assert "kanzlei/b.pdf" in html.split("data-pointers=", 1)[1].split(">", 1)[0]
            # One process-wide capability per deployment: forget it before
            # the next mode's app.
            import doc_fields_capability

            doc_fields_capability.reset_for_tests()

    # -- read ----------------------------------------------------------------

    def test_read_needs_the_csrf_header(self, build, admin):
        client = self._client(build("values"))
        api = FakeDocFieldsApi.current
        self._seed(api)
        response = _post_json(client, "/admin/documents/fields/read", {"pointer": POINTER},
                              csrf=False)
        assert response.status_code == 403
        assert _df_calls(api, "doc_values") == []

    def test_a_member_is_refused(self, build, admin, identity_repo):
        from conftest import _person

        _person(identity_repo, "mia@kanzlei.ch", "Mia", "member")
        client = self._client(build("values"), "mia@kanzlei.ch")
        for path in ("/admin/documents/fields/read", "/admin/documents/fields/edit",
                     "/admin/documents/fields/find"):
            assert _post_json(client, path, {"pointer": POINTER}).status_code == 403

    def test_read_shows_the_layers_with_the_pointer_in_the_body(self, build, admin):
        client = self._client(build("values"))
        api = FakeDocFieldsApi.current
        self._seed(api)
        response = _post_json(client, "/admin/documents/fields/read", {"pointer": POINTER})
        assert response.status_code == 200
        view = response.get_json()
        assert view["success"] is True and view["version"] == 1
        fields = {f["key"]: f for f in view["fields"]}
        assert fields["doc_type"]["text"] == "Vertrag"
        assert fields["doc_type"]["layer_label"] == "Upload"
        assert "counterparty" in view["editable_keys"] and "title" in view["editable_keys"]
        assert _df_calls(api, "doc_values") == [{"pointer": POINTER}]

    def test_an_unknown_pointer_is_404(self, build, admin):
        client = self._client(build("values"))
        response = _post_json(client, "/admin/documents/fields/read", {"pointer": "k/none.pdf"})
        assert response.status_code == 404

    def test_a_server_without_body_pointers_asks_for_an_update(self, build, admin):
        client = self._client(build("values"))
        FakeDocFieldsApi.current.fail_call("doc_values", 400, "invalid_value", path="pointer")
        response = _post_json(client, "/admin/documents/fields/read", {"pointer": POINTER})
        assert response.status_code == 400
        assert "Knovas-Update n\u00f6tig" in response.get_json()["message"]

    def test_a_held_document_is_read_only(self, build, admin):
        client = self._client(build("values"))
        FakeDocFieldsApi.current.add_document(POINTER, title="Vertrag", held=True)
        view = _post_json(client, "/admin/documents/fields/read", {"pointer": POINTER}).get_json()
        assert view["held"] is True and view["can_edit"] is False
        assert "zur\u00fcckgehalten" in view["message"]

    # -- edit ----------------------------------------------------------------

    def test_an_edit_is_non_strict_shows_warnings_and_reads_again(self, build, admin,
                                                                  platform_db, caplog):
        import logging

        from identity import audit

        caplog.set_level(logging.DEBUG)
        client = self._client(build("values"))
        api = FakeDocFieldsApi.current
        self._seed(api)
        response = _post_json(client, "/admin/documents/fields/edit", {
            "pointer": POINTER, "if_version": 1,
            "set": {"counterparty": "Sentinel Beispiel GmbH"}})
        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        assert body["warnings"] == [{"key": "counterparty", "label": "Gegenpartei",
                                     "code": "unresolved_entity", "text": "nicht verkn\u00fcpft"}]
        patch = _df_calls(api, "patch_doc_values")[0]
        assert patch["fields_strict"] is False
        assert patch["actor_ref"] == f"platform-user:{admin.id}"
        assert patch["set"] == {"counterparty": "Sentinel Beispiel GmbH"}
        fields = {f["key"]: f for f in body["view"]["fields"]}
        assert fields["counterparty"]["text"] == "Sentinel Beispiel GmbH"
        assert fields["counterparty"]["layer_label"] == "Manuell"
        assert len(_df_calls(api, "doc_values")) == 1
        row = audit.recent(platform_db, action="document.values_edited")[0]
        assert row["outcome"] == "ok"
        assert row["target_type"] == "document"
        assert row["target_id"] == api.docs[POINTER]["document_uuid"]
        assert row["detail"]["keys"] == ["counterparty"]
        assert row["detail"]["version_from"] == 1 and row["detail"]["version_to"] == 2
        assert row["detail"]["warning_codes"] == ["unresolved_entity"]
        assert "Sentinel" not in _json.dumps(row, default=str)
        assert not [r for r in caplog.records if "Sentinel" in r.getMessage()]

    def test_a_revert_sends_null(self, build, admin):
        client = self._client(build("values"))
        api = FakeDocFieldsApi.current
        self._seed(api)
        api.patch_doc_values(POINTER, 1, set={"doc_type": "invoice"})
        api.doc_calls.clear()
        response = _post_json(client, "/admin/documents/fields/edit",
                              {"pointer": POINTER, "if_version": 2, "set": {"doc_type": None}})
        assert response.status_code == 200
        assert _df_calls(api, "patch_doc_values")[0]["set"] == {"doc_type": None}
        fields = {f["key"]: f for f in response.get_json()["view"]["fields"]}
        assert fields["doc_type"]["text"] == "Vertrag"

    def test_a_conflict_reads_again_and_overwrites_nothing(self, build, admin, platform_db):
        from identity import audit

        client = self._client(build("values"))
        api = FakeDocFieldsApi.current
        self._seed(api)
        response = _post_json(client, "/admin/documents/fields/edit",
                              {"pointer": POINTER, "if_version": 0,
                               "set": {"doc_type": "invoice"}})
        assert response.status_code == 409
        body = response.get_json()
        assert body["error"] == "version_conflict" and body["current_version"] == 1
        assert body["view"]["version"] == 1
        assert {f["key"]: f["text"] for f in body["view"]["fields"]}["doc_type"] == "Vertrag"
        row = audit.recent(platform_db, action="document.values_edited")[0]
        # audit_log admits ok/denied/error only: a refusal -- a conflict
        # included -- is "denied", named by Knovas's code; the search panel's
        # edit route records the same (doc_fields_view).
        assert row["outcome"] == "denied" and row["detail"]["code"] == "version_conflict"
        assert row["detail"]["version_to"] == 1

    @pytest.mark.parametrize("setup, status", [
        ("denied", 403),
        ("quarantined", 409),
    ])
    def test_a_refusal_turns_the_drawer_read_only(self, build, admin, platform_db, setup,
                                                   status):
        from identity import audit

        client = self._client(build("values"))
        api = FakeDocFieldsApi.current
        self._seed(api)
        if setup == "denied":
            api.change_denied.add(POINTER)
        else:
            api.fail_call("patch_doc_values", 409, "anchor_quarantined")
        response = _post_json(client, "/admin/documents/fields/edit",
                              {"pointer": POINTER, "if_version": 1,
                               "set": {"doc_type": "invoice"}})
        assert response.status_code == status
        assert response.get_json()["read_only"] is True
        row = audit.recent(platform_db, action="document.values_edited")[0]
        assert row["outcome"] == "denied"
        assert row["detail"]["code"] == ("change_not_authorized" if setup == "denied"
                                         else "anchor_quarantined")
        assert row["detail"]["version_to"] is None

    def test_a_field_error_is_shown_at_the_field(self, build, admin):
        client = self._client(build("values"))
        api = FakeDocFieldsApi.current
        self._seed(api)
        response = _post_json(client, "/admin/documents/fields/edit",
                              {"pointer": POINTER, "if_version": 1,
                               "set": {"doc_type": "Kreditorenrechnung"}})
        assert response.status_code == 400
        body = response.get_json()
        assert body["field"] == "doc_type" and "Dokumentart" in body["message"]
        assert "Kreditorenrechnung" not in body["message"]

    def test_roles_outside_edit_roles_read_only(self, build, admin, monkeypatch):
        import dataclasses

        import doc_fields_capability as dfc

        original = dfc.settings
        monkeypatch.setattr(dfc, "settings", lambda config: dataclasses.replace(
            original(config), edit_roles=frozenset({"member"})))
        client = self._client(build("values"))
        api = FakeDocFieldsApi.current
        self._seed(api)
        view = _post_json(client, "/admin/documents/fields/read", {"pointer": POINTER}).get_json()
        assert view["can_edit"] is False and view["editable_keys"] == []
        response = _post_json(client, "/admin/documents/fields/edit",
                              {"pointer": POINTER, "if_version": 1,
                               "set": {"doc_type": "invoice"}})
        assert response.status_code == 400
        assert _df_calls(api, "patch_doc_values") == []

    def test_the_feature_off_answers_without_calling_knovas(self, build, admin):
        client = self._client(build("off"))
        for path in ("/admin/documents/fields/read", "/admin/documents/fields/edit"):
            response = _post_json(client, path, {"pointer": POINTER, "if_version": 1,
                                                 "set": {"doc_type": "invoice"}})
            assert response.status_code == 409
        assert FakeDocFieldsApi.current.doc_calls == []

    # -- Feldfilter ------------------------------------------------------------

    def test_the_feldfilter_needs_the_listing(self, build, admin):
        client = self._client(build("values"))
        response = _post_json(client, "/admin/documents/fields/find",
                              {"pairs": [{"field": "doc_type", "value": "contract"}]})
        assert response.status_code == 409
        assert _df_calls(FakeDocFieldsApi.current, "find_doc_values") == []

    def test_the_feldfilter_lists_by_name_and_says_what_it_understood(self, build, admin):
        client = self._client(build("filters"))
        api = FakeDocFieldsApi.current
        self._seed(api)
        api.add_document("kanzlei/b.pdf", fields={"mandant": {"name": "Beispiel GmbH"}})
        response = _post_json(client, "/admin/documents/fields/find",
                              {"pairs": [{"field": "mandant", "value": "Muster AG"}]})
        assert response.status_code == 200
        body = response.get_json()
        assert body["filter_state"] == "applied"
        assert [d["pointer"] for d in body["documents"]] == [POINTER]
        assert body["documents"][0]["fields_display"]
        assert body["resolved"][0]["text"] == "Muster AG"
        assert body["notice"] == {"incomplete": False, "text": None, "total_count": 1}
        call = _df_calls(api, "find_doc_values")[0]
        assert call["where"] == {"mandant": {"name": "Muster AG"}}
        assert "title" in call["return_fields"] and "patient" not in call["return_fields"]

    def test_without_the_echo_there_are_no_rows(self, build, admin):
        import doc_fields_capability as dfc

        client = self._client(build("filters"))
        api = FakeDocFieldsApi.current
        self._seed(api)
        api.drop_where_echo = True
        response = _post_json(client, "/admin/documents/fields/find",
                              {"pairs": [{"field": "doc_type", "value": "contract"}]})
        assert response.status_code == 409
        assert response.get_json()["error"] == "filter_not_applied"
        assert response.get_json()["documents"] == []
        assert dfc.shared_cache().peek() is None  # echo_missing: probe again

    def test_the_notice_comes_only_on_the_last_page(self, build, admin):
        client = self._client(build("filters"))
        api = FakeDocFieldsApi.current
        echo = {"applied": True, "clauses": 1, "resolved": []}
        api.scripted_pages = [
            {"documents": [{"pointer": "k/a.pdf", "title": "a.pdf"}], "next_after": "c1",
             "complete": False, "total_count": 3, "where": echo},
            {"documents": [], "next_after": None, "complete": False, "where": echo},
        ]
        first = _post_json(client, "/admin/documents/fields/find",
                           {"pairs": [{"field": "doc_type", "value": "contract"}]}).get_json()
        assert first["notice"] == {"incomplete": False, "text": None, "total_count": 3}
        assert first["next_after"] == "c1"
        last = _post_json(client, "/admin/documents/fields/find",
                          {"pairs": [{"field": "doc_type", "value": "contract"}],
                           "after": "c1"}).get_json()
        assert last["notice"]["incomplete"] is True
        assert last["notice"]["text"] == "Liste unvollst\u00e4ndig \u2013 Filter eingrenzen"
        assert _df_calls(api, "find_doc_values")[1]["after"] == "c1"

    def test_a_sort_by_deadline_carries_the_banner(self, build, admin):
        from doc_fields_view import DEADLINE_BANNER

        client = self._client(build("filters"))
        api = FakeDocFieldsApi.current
        self._seed(api)
        body = _post_json(client, "/admin/documents/fields/find",
                          {"pairs": [{"field": "doc_type", "value": "contract"}],
                           "sort": {"field": "deadline", "order": "asc"}}).get_json()
        assert body["deadline_banner"] == DEADLINE_BANNER
        bad = _post_json(client, "/admin/documents/fields/find",
                         {"pairs": [{"field": "doc_type", "value": "contract"}],
                          "sort": {"field": "doc_type"}})
        assert bad.status_code == 400

    def test_listing_only_still_lists(self, build, admin):
        import doc_fields_capability as dfc

        client = self._client(build("listing_only"))
        api = FakeDocFieldsApi.current
        self._seed(api)
        client.get("/admin/documents")
        dfc.observe("needs_calibration")
        response = _post_json(client, "/admin/documents/fields/find",
                              {"pairs": [{"field": "doc_type", "value": "contract"}]})
        assert response.status_code == 200 and response.get_json()["filter_state"] == "applied"
        assert 'id="doc-fields-filter"' in client.get("/admin/documents").data.decode("utf-8")

    def test_the_feldfilter_needs_the_csrf_header(self, build, admin):
        client = self._client(build("filters"))
        response = _post_json(client, "/admin/documents/fields/find",
                              {"pairs": [{"field": "doc_type", "value": "contract"}]},
                              csrf=False)
        assert response.status_code == 403
        assert _df_calls(FakeDocFieldsApi.current, "find_doc_values") == []
