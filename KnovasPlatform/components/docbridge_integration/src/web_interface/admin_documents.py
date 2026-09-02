"""The firm's document inventory and its access controls.

Two tabs of the administration console: Dokumente (every document the tenant
has uploaded, as far as the signed-in administrator may see it) and the
folder-rule half of Zugriffsgruppen.

The inventory is cursor-fed. The screen holds one page, never the corpus:
`/admin/documents/page` returns JSON for the next keyset page and the browser
appends it. That is what makes the tab usable on a ten-million-document
tenant.

Walls bind the administrator too (design §2 D1). There is no "show
everything" switch here, and no route asks the backend for one.

Plan: docs/superpowers/plans/2026-08-29-admin-document-rbac-components.md
"""
from __future__ import annotations

import logging

from flask import jsonify, render_template, request

from identity import audit

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


class DocumentsView:
    """Composes one page of the inventory from the Knovas client.

    Kept free of Flask so it can be tested without an app context.
    """

    def __init__(self, client) -> None:
        self._client = client

    def page(
        self,
        after: str | None = None,
        *,
        prefix: str | None = None,
        group: str | None = None,
        unrestricted: bool = False,
        conflicts: bool = False,
        status: str | None = None,
        limit: int = PAGE_SIZE,
    ) -> dict:
        payload = self._client.documents(
            after=after,
            limit=limit,
            prefix=prefix,
            group=group,
            unrestricted=unrestricted,
            conflicts=conflicts,
            status=status,
        )
        return {
            "documents": list(payload.get("documents") or []),
            "next_after": payload.get("next_after"),
            "total_count": int(payload.get("total_count") or 0),
        }


def _filters_from_request() -> dict:
    return {
        "prefix": (request.args.get("prefix") or "").strip() or None,
        "group": (request.args.get("group") or "").strip() or None,
        "unrestricted": request.args.get("unrestricted") == "1",
        "conflicts": request.args.get("conflicts") == "1",
        "status": (request.args.get("status") or "").strip() or None,
    }


def attach_document_routes(
    bp,
    gate,
    *,
    csrf_valid,
    csrf_token,
    page_context,
    client_factory,
    require_admin,
):
    """Mount the Dokumente routes onto the existing admin blueprint.

    Takes ``require_admin`` from the blueprint factory rather than redefining
    it, so there is exactly one definition of "who may reach the console".
    """

    def _csrf_ok() -> bool:
        return csrf_valid(str(request.form.get("csrf_token", "") or ""))

    def _documents_page(error=None, notice=None, status=200):
        view = DocumentsView(client_factory())
        filters = _filters_from_request()
        try:
            first = view.page(**filters)
        except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
            logger.warning("Dokumentliste nicht abrufbar: %s", exc)
            first = {"documents": [], "next_after": None, "total_count": 0}
            error = error or (
                "Die Dokumentliste ist derzeit nicht abrufbar. "
                "Bitte spaeter erneut versuchen."
            )
        groups = []
        try:
            groups = client_factory().access_groups()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Zugriffsgruppen nicht abrufbar: %s", exc)
        return render_template(
            "admin_documents.html",
            active_nav="admin",
            **page_context(),
            documents=first["documents"],
            next_after=first["next_after"],
            total_count=first["total_count"],
            filters=filters,
            groups=groups,
            me=gate.current_user(),
            error=error,
            notice=notice,
            csrf_token=csrf_token(),
        ), status

    @bp.route("/documents")
    @require_admin
    def documents():
        return _documents_page()

    @bp.route("/documents/page")
    @require_admin
    def documents_page():
        """One further keyset page, as JSON, for the infinite list."""
        view = DocumentsView(client_factory())
        filters = _filters_from_request()
        after = (request.args.get("after") or "").strip() or None
        try:
            return jsonify(view.page(after=after, **filters))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Seite nicht abrufbar: %s", exc)
            return jsonify({"documents": [], "next_after": None,
                            "total_count": 0, "error": "unavailable"}), 503

    @bp.route("/documents/acl", methods=["POST"])
    @require_admin
    def set_document_acl():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _documents_page(
                error="Formular ist abgelaufen. Bitte erneut versuchen.",
                status=400,
            )
        pointers = [p for p in request.form.getlist("pointer") if p]
        groups = [g for g in request.form.getlist("access_group") if g]
        if not pointers:
            return _documents_page(error="Kein Dokument ausgewaehlt.", status=400)

        me = gate.current_user()
        client = client_factory()
        changed = 0
        failed: list[str] = []
        for pointer in pointers:
            try:
                client.set_document_access(pointer, groups)
                changed += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("ACL nicht gesetzt fuer %s: %s", pointer, exc)
                failed.append(pointer)

        audit.record(
            gate.connection(), action="document.acl_changed", actor=me,
            target_type="document",
            target_id=pointers[0] if len(pointers) == 1 else f"{len(pointers)} Dokumente",
            detail={"access_groups": groups, "changed": changed,
                    "failed": len(failed)},
        )
        if failed:
            return _documents_page(
                error=f"{len(failed)} Dokument(e) konnten nicht geaendert werden.",
                notice=f"{changed} Dokument(e) geaendert.",
                status=200,
            )
        return _documents_page(notice=f"{changed} Dokument(e) geaendert.")

    return bp
