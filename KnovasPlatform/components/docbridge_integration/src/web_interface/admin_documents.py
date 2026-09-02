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


    # ---- Zugriffsgruppen: group tree and folder rules (plan Task 6) ----

    def _groups_page(error=None, notice=None, status=200):
        client = client_factory()
        groups, rules = [], []
        try:
            groups = client.access_groups()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Zugriffsgruppen nicht abrufbar: %s", exc)
            error = error or "Zugriffsgruppen sind derzeit nicht abrufbar."
        try:
            rules = client.folder_rules()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ordnerregeln nicht abrufbar: %s", exc)
        return render_template(
            "admin_access_groups.html",
            active_nav="admin",
            **page_context(),
            groups=groups,
            rules=rules,
            me=gate.current_user(),
            error=error,
            notice=notice,
            csrf_token=csrf_token(),
        ), status

    @bp.route("/access-groups")
    @require_admin
    def access_groups():
        return _groups_page()

    @bp.route("/access-groups/create", methods=["POST"])
    @require_admin
    def create_access_group():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _groups_page(
                error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400
            )
        name = str(request.form.get("name", "") or "").strip()
        parent = str(request.form.get("parent", "") or "").strip() or None
        if not name:
            return _groups_page(error="Bitte einen Namen angeben.", status=400)
        try:
            client_factory().create_access_group(name, parent=parent)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gruppe nicht angelegt: %s", exc)
            return _groups_page(error="Gruppe konnte nicht angelegt werden.",
                                status=400)
        audit.record(
            gate.connection(), action="access_group.created",
            actor=gate.current_user(), target_type="access_group",
            target_id=name, detail={"parent": parent},
        )
        return _groups_page(notice=f'Gruppe "{name}" wurde angelegt.')

    @bp.route("/folder-rules/save", methods=["POST"])
    @require_admin
    def save_folder_rule():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _groups_page(
                error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400
            )
        rule_id = str(request.form.get("rule_id", "") or "").strip()
        prefix = str(request.form.get("pointer_prefix", "") or "").strip()
        groups = [g for g in request.form.getlist("access_group") if g]
        if not rule_id and not prefix:
            return _groups_page(error="Bitte einen Ordner angeben.", status=400)
        client = client_factory()
        try:
            if rule_id:
                client.update_folder_rule(rule_id, groups)
            else:
                client.create_folder_rule(prefix, groups)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ordnerregel nicht gespeichert: %s", exc)
            return _groups_page(
                error="Ordnerregel konnte nicht gespeichert werden.", status=400
            )
        audit.record(
            gate.connection(), action="folder_rule.saved",
            actor=gate.current_user(), target_type="folder_rule",
            target_id=rule_id or prefix, detail={"access_groups": groups},
        )
        return _groups_page(notice="Ordnerregel gespeichert.")

    @bp.route("/folder-rules/delete", methods=["POST"])
    @require_admin
    def delete_folder_rule():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _groups_page(
                error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400
            )
        rule_id = str(request.form.get("rule_id", "") or "").strip()
        if not rule_id:
            return _groups_page(error="Keine Regel ausgewaehlt.", status=400)
        try:
            client_factory().delete_folder_rule(rule_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ordnerregel nicht geloescht: %s", exc)
            return _groups_page(error="Regel konnte nicht geloescht werden.",
                                status=400)
        audit.record(
            gate.connection(), action="folder_rule.deleted",
            actor=gate.current_user(), target_type="folder_rule",
            target_id=rule_id, detail={},
        )
        return _groups_page(notice="Ordnerregel geloescht.")

    return bp
