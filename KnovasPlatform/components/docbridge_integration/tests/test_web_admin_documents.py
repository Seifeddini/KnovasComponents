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
        write_at = body.index("set_document_access")
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
