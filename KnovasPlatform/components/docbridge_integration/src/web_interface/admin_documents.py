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

Document fields (spec 4.7), only while Knovas offers them: a fields drawer
per row, opened on click so the list costs no read per row, and a
Feldfilter that lists documents by field values. Both are JSON POST routes
with the pointer in the body -- a pointer is never part of a URL the console
builds -- and both check the ``X-CSRF-Token`` header themselves, because
console routes are exempt from the app's header gate.

Plan: docs/superpowers/plans/2026-08-29-admin-document-rbac-components.md
"""
from __future__ import annotations

import logging

from flask import jsonify, render_template, request

import doc_fields_capability as dfc
from doc_fields_capability import Capability
from doc_fields_view import (
    AUDIT_OUTCOME_REFUSED,
    DEADLINE_BANNER,
    VALUES_EDIT_REFUSALS,
    card_return_fields,
    error_message,
    filter_state,
    find_row,
    is_deadline_field,
    listing_empty_text,
    listing_notice,
    resolved_chips,
    validate_where,
    values_edit_audit_detail,
)
from identity import audit
from identity.approvals import ApprovalService
from knovas_client import DocFieldsError, DocFieldsUnavailable
from web_interface.admin_doc_fields import (
    OFF_TEXT,
    FormError,
    edit_ops_from_body,
    edit_warnings,
    filter_fields,
    knovas_message,
    path_key,
    sort_from_body,
    values_view,
    where_from_pairs,
)
from web_interface.guarded import run_guarded

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
            # True when the endpoint answered 404. The screen must not present
            # that as an empty inventory: one is a tenant with no documents, the
            # other is a feature this tenant does not have.
            "unavailable": bool(payload.get("unavailable")),
        }


def _filters_from_request() -> dict:
    return {
        "prefix": (request.args.get("prefix") or "").strip() or None,
        "group": (request.args.get("group") or "").strip() or None,
        "unrestricted": request.args.get("unrestricted") == "1",
        "conflicts": request.args.get("conflicts") == "1",
        "status": (request.args.get("status") or "").strip() or None,
    }


def execute_corpus_purge(client, payload, *, actor, conn) -> dict:
    """Erase the tenant's whole corpus, and record who caused it.

    Called from the route when the actor may act alone, and from the Approvals
    tab once a second person has confirmed — one function, so the guarded and
    the unguarded path cannot drift.

    The audit entry is written *after* the call returns. A record of a deletion
    that did not happen is worse than a missing one: this is the only trace that
    the corpus is gone, and it has to mean what it says.
    """
    confirm = str(payload.get("confirm_client_id") or "")
    result = client.delete_all_documents(confirm)
    audit.record(
        conn,
        action="documents.purged_all",
        actor=actor,
        target_type="tenant",
        target_id=confirm,
        detail={
            "message": str(result.get("message") or ""),
            "weaviate_tenant_reset": bool(result.get("weaviate_tenant_reset")),
            "postgres_rows_deleted": result.get("postgres_rows_deleted") or {},
        },
    )
    logger.warning(
        "Dokumentbestand des Mandanten %s geloescht durch %s.",
        confirm, getattr(actor, "email", "?"),
    )
    return {"purged": True, "message": str(result.get("message") or "")}


def execute_acl_change(client, payload, *, actor, conn) -> dict:
    """Carry out one ``acl_change`` payload against Knovas and audit it.

    Called from the route when the actor may act alone, and from the
    Approvals tab when someone else has confirmed. One function, so the two
    paths cannot drift.
    """
    action = str(payload.get("action") or "")
    groups = [str(g) for g in (payload.get("access_groups") or []) if g]

    if action == "document_acl":
        pointers = [str(p) for p in (payload.get("pointers") or []) if p]
        changed, failed = 0, []
        for pointer in pointers:
            try:
                client.set_document_access(pointer, groups)
                changed += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("ACL nicht gesetzt fuer %s: %s", pointer, exc)
                failed.append(pointer)
        audit.record(
            conn, action="document.acl_changed", actor=actor,
            target_type="document",
            target_id=pointers[0] if len(pointers) == 1 else f"{len(pointers)} Dokumente",
            detail={"access_groups": groups, "changed": changed, "failed": len(failed)},
        )
        return {"changed": changed, "failed": failed}

    if action == "folder_rule_save":
        rule_id = str(payload.get("rule_id") or "")
        prefix = str(payload.get("pointer_prefix") or "")
        if rule_id:
            result = client.update_folder_rule(rule_id, groups)
        else:
            result = client.create_folder_rule(prefix, groups)
        if result is None:
            # 404 from the RBAC endpoint: nothing was stored. Raising here is
            # what keeps the audit line and the "gespeichert" notice honest --
            # both are below this point, and both would otherwise describe a
            # rule that does not exist.
            raise RuntimeError(
                "Die Knovas-API hat die Ordnerregel nicht gespeichert (HTTP 404)."
            )
        saved_id = str((result or {}).get("rule_id") or rule_id or prefix)
        audit.record(
            conn, action="folder_rule.saved", actor=actor, target_type="folder_rule",
            target_id=saved_id, detail={"access_groups": groups, "pointer_prefix": prefix},
        )
        return {"rule_id": saved_id}

    if action == "folder_rule_delete":
        rule_id = str(payload.get("rule_id") or "")
        client.delete_folder_rule(rule_id)
        audit.record(
            conn, action="folder_rule.deleted", actor=actor, target_type="folder_rule",
            target_id=rule_id, detail={},
        )
        return {"rule_id": rule_id}

    raise ValueError(f"Unbekannte Aktion in der Zugriffsaenderung: {action!r}")


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

    def _approvals() -> ApprovalService:
        return ApprovalService(gate.connection(), gate.users())

    def _queued_notice(req) -> str:
        return (
            "Zur Freigabe eingereicht (Nr. "
            f"{str(req.id)[:8]}). Eine zweite Person muss bestaetigen, "
            "bevor die Aenderung wirkt."
        )

    def _doc_fields_capability(client) -> Capability:
        try:
            return dfc.capability_for(client)
        except Exception as exc:  # noqa: BLE001 - the inventory must render
            logger.warning("Dokumentfelder-Faehigkeit nicht ermittelbar: %s", type(exc).__name__)
            return Capability.off

    def _filter_fields(client, capability) -> list:
        """The Feldfilter's fields; read only while the listing exists."""
        if not capability.shows_listing:
            return []
        me = gate.current_user()
        try:
            return filter_fields(dfc.registry_for(client, getattr(me, "id", None)))
        except Exception as exc:  # noqa: BLE001 - the filter stays empty
            dfc.observe_exception(exc)
            logger.warning("Feldverzeichnis nicht abrufbar: %s", type(exc).__name__)
            return []

    def _documents_page(error=None, notice=None, status=200):
        view = DocumentsView(client_factory())
        filters = _filters_from_request()
        try:
            first = view.page(**filters)
        except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
            logger.warning("Dokumentliste nicht abrufbar: %s", exc)
            # Every key the template reads must be here: the fallback is what
            # renders when the fetch failed, and a missing one turns a handled
            # error into a KeyError 500 on the page meant to report it.
            first = {"documents": [], "next_after": None, "total_count": 0,
                     "unavailable": False}
            error = error or (
                "Die Dokumentliste ist derzeit nicht abrufbar. "
                "Bitte spaeter erneut versuchen."
            )
        groups = []
        try:
            groups = client_factory().access_groups()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Zugriffsgruppen nicht abrufbar: %s", exc)
        capability = _doc_fields_capability(client_factory())
        return render_template(
            "admin_documents.html",
            active_nav="admin",
            **page_context(),
            doc_fields_on=capability.shows_values,
            doc_fields_listing=capability.shows_listing,
            filter_fields=_filter_fields(client_factory(), capability),
            documents=first["documents"],
            next_after=first["next_after"],
            total_count=first["total_count"],
            unavailable=first["unavailable"],
            filters=filters,
            groups=groups,
            # Shown next to the purge form so the confirmation can be read off
            # the page. It is a typo guard, not a secret: the API derives the
            # tenant from the certificate regardless.
            tenant_id=str(getattr(client_factory(), "customer_id", "") or ""),
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
                error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400
            )
        pointers = [p for p in request.form.getlist("pointer") if p]
        groups = [g for g in request.form.getlist("access_group") if g]
        if not pointers:
            return _documents_page(error="Kein Dokument ausgewaehlt.", status=400)

        me = gate.current_user()
        payload = {"action": "document_acl", "pointers": pointers, "access_groups": groups}
        try:
            outcome = run_guarded(
                _approvals(), me, kind="acl_change",
                target_ref=pointers[0] if len(pointers) == 1 else f"{len(pointers)} Dokumente",
                payload=payload,
                execute=lambda: execute_acl_change(
                    client_factory(), payload, actor=me, conn=gate.connection()
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Zugriffsaenderung nicht gespeichert: %s", exc)
            return _documents_page(
                error="Zugriffsaenderung konnte nicht gespeichert werden.", status=400
            )
        if outcome.queued:
            return _documents_page(notice=_queued_notice(outcome.request))
        result = outcome.result or {}
        failed = result.get("failed") or []
        if failed:
            return _documents_page(
                error=f"{len(failed)} Dokument(e) konnten nicht geaendert werden.",
                notice=f"{result.get('changed', 0)} Dokument(e) geaendert.",
            )
        return _documents_page(notice=f"{result.get('changed', 0)} Dokument(e) geaendert.")


    @bp.route("/documents/purge", methods=["POST"])
    @require_admin
    def purge_documents():
        """Erase the tenant's entire corpus.

        Four things stand between a stray click and an empty tenant, and each
        stops a different mistake:

        - ``require_admin`` and the CSRF token, as everywhere in the console;
        - the typed tenant id, which is the API's own typo guard and cannot be
          produced by clicking;
        - ``run_guarded``: ``purge_all_documents`` is a guarded kind, so unless
          the approvals policy lets an administrator act alone, this is queued
          for a second person rather than carried out;
        - the audit record in ``execute_corpus_purge``, written after the call
          returns, because this is the only trace left afterwards.

        There is no undo. Knovas cannot restore the documents and neither can
        the firm without ingesting them again.
        """
        if not _csrf_ok():
            return _documents_page(
                error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400
            )
        confirm = str(request.form.get("confirm_client_id", "") or "").strip()
        expected = str(getattr(client_factory(), "customer_id", "") or "").strip()
        if not confirm or confirm != expected:
            # Deliberately before anything else runs: a mistyped confirmation
            # must not reach the API, and must not read as a system failure.
            return _documents_page(
                error=(
                    "Die eingegebene Mandanten-Id stimmt nicht. Es wurde nichts "
                    "geloescht."
                ),
                status=400,
            )
        me = gate.current_user()
        payload = {"action": "purge_all_documents", "confirm_client_id": confirm}
        try:
            outcome = run_guarded(
                _approvals(), me, kind="purge_all_documents",
                target_ref=confirm, payload=payload,
                execute=lambda: execute_corpus_purge(
                    client_factory(), payload, actor=me, conn=gate.connection()
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Bestand nicht geloescht: %s", exc, exc_info=True)
            return _documents_page(
                error=(
                    "Der Bestand wurde NICHT geloescht — die Knovas-API hat den "
                    "Aufruf abgelehnt. Das Log von docbridge-web nennt den Grund."
                ),
                status=502,
            )
        if outcome.queued:
            return _documents_page(notice=_queued_notice(outcome.request))
        return _documents_page(
            notice="Der gesamte Dokumentbestand dieses Mandanten wurde geloescht."
        )

    # ---- Dokumentfelder: drawer and Feldfilter (spec 4.7) ----

    def _header_csrf_ok() -> bool:
        return csrf_valid(str(request.headers.get("X-CSRF-Token", "") or ""))

    def _json_error(code, message, status, **extra):
        return jsonify({"success": False, "error": code, "message": message, **extra}), status

    def _json_pointer(body):
        pointer = body.get("pointer") if isinstance(body, dict) else None
        if not isinstance(pointer, str) or not pointer.strip() or len(pointer) > 1000:
            return None
        return pointer

    def _registry(client, me):
        """The person's registry view; DocFieldsUnavailable propagates."""
        return dfc.registry_for(client, getattr(me, "id", None))

    def _read_view(client, me, pointer):
        """``(status, payload)`` for the drawer: the pointer's values view."""
        try:
            raw = client.doc_values(pointer)
        except DocFieldsUnavailable as exc:
            dfc.observe_exception(exc)
            return 409, {"success": False, "error": "doc_fields_off", "message": OFF_TEXT}
        except DocFieldsError as exc:
            logger.warning("Dokumentwerte nicht lesbar: %s %s", exc.status, exc.error_code)
            message, status = knovas_message(exc)
            return status, {"success": False, "error": exc.error_code, "message": message}
        if raw is None:
            return 404, {"success": False, "error": "not_found",
                         "message": error_message("NOT_FOUND")}
        registry = []
        try:
            registry = _registry(client, me)
        except DocFieldsUnavailable as exc:
            dfc.observe_exception(exc)
            return 409, {"success": False, "error": "doc_fields_off", "message": OFF_TEXT}
        except Exception as exc:  # noqa: BLE001 - values still show, without inputs
            logger.warning("Feldverzeichnis nicht abrufbar: %s", type(exc).__name__)
        cfg = dfc.settings(getattr(client, "config", None))
        view = values_view(raw, registry, roles=getattr(me, "roles", ()),
                           edit_roles=cfg.edit_roles)
        return 200, {"success": True, **view}

    @bp.route("/documents/fields/read", methods=["POST"])
    @require_admin
    def document_fields_read():
        """The fields drawer of one document: every layer, and what this
        person may change. The pointer travels in the body."""
        if not _header_csrf_ok():
            return _json_error("csrf", "Sitzung abgelaufen. Bitte die Seite neu laden.", 403)
        pointer = _json_pointer(request.get_json(silent=True))
        if pointer is None:
            return _json_error("invalid_pointer", "Kein Dokument angegeben.", 400)
        client = client_factory()
        if not _doc_fields_capability(client).shows_values:
            return _json_error("doc_fields_off", OFF_TEXT, 409)
        status, payload = _read_view(client, gate.current_user(), pointer)
        return jsonify(payload), status

    @bp.route("/documents/fields/edit", methods=["POST"])
    @require_admin
    def document_fields_edit():
        """One manual edit, sent once and non-strict (D12), then read again.

        Knovas decides: 403 ``change_not_authorized`` turns the drawer
        read-only, 409 ``version_conflict`` re-reads and says so -- nothing
        is overwritten automatically -- and a held document stays read-only.
        The audit row carries keys, counts, versions and warning codes.
        """
        if not _header_csrf_ok():
            return _json_error("csrf", "Sitzung abgelaufen. Bitte die Seite neu laden.", 403)
        body = request.get_json(silent=True)
        pointer = _json_pointer(body)
        if pointer is None:
            return _json_error("invalid_pointer", "Kein Dokument angegeben.", 400)
        if_version = body.get("if_version")
        if isinstance(if_version, bool) or not isinstance(if_version, int) or if_version < 0:
            return _json_error("invalid_version", "Bitte die Werte neu laden.", 400)
        client = client_factory()
        if not _doc_fields_capability(client).shows_values:
            return _json_error("doc_fields_off", OFF_TEXT, 409)
        me = gate.current_user()
        try:
            registry = _registry(client, me)
        except DocFieldsUnavailable as exc:
            dfc.observe_exception(exc)
            return _json_error("doc_fields_off", OFF_TEXT, 409)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Feldverzeichnis nicht abrufbar: %s", type(exc).__name__)
            return _json_error("registry_unavailable",
                               "Das Feldverzeichnis ist derzeit nicht abrufbar.", 503)
        cfg = dfc.settings(getattr(client, "config", None))
        try:
            ops = edit_ops_from_body(body, registry, roles=getattr(me, "roles", ()),
                                     edit_roles=cfg.edit_roles)
        except FormError as exc:
            return _json_error("invalid_edit", str(exc), 400)
        if not ops:
            return _json_error("no_change", "Keine \u00c4nderung.", 400)
        failure = None
        try:
            result = client.patch_doc_values(
                pointer, if_version, set=ops.get("set"), unset=ops.get("unset"),
                add=ops.get("add"), remove=ops.get("remove"), fields_strict=False,
                actor_ref=f"platform-user:{me.id}",
            )
        except DocFieldsUnavailable as exc:
            dfc.observe_exception(exc)
            return _json_error("doc_fields_off", OFF_TEXT, 409)
        except DocFieldsError as exc:
            failure = exc
            result = None
        if failure is None and result is None:
            return _json_error("not_found", error_message("NOT_FOUND"), 404)
        read_status, after = _read_view(client, me, pointer)
        view = after if read_status == 200 else None
        target = (view or {}).get("document_uuid")

        def _audit(outcome, version_to, codes=(), code=None):
            audit.record(
                gate.connection(), action="document.values_edited", actor=me,
                target_type="document", target_id=str(target) if target else None,
                outcome=outcome,
                detail=values_edit_audit_detail(ops, version_from=if_version,
                                                version_to=version_to, warning_codes=codes,
                                                code=code),
            )

        if failure is not None:
            code = failure.error_code
            details = failure.details or {}
            logger.warning("Dokumentwerte nicht gespeichert: %s %s", failure.status, code)
            message, status = knovas_message(failure, registry)
            if code == "version_conflict":
                status = 409
            if code in VALUES_EDIT_REFUSALS:
                # Refused by Knovas, as designed: "denied" with its code (one
                # convention with the search panel's edit route).
                _audit(AUDIT_OUTCOME_REFUSED,
                       details.get("current_version") if code == "version_conflict" else None,
                       code=code)
            return jsonify({
                "success": False, "error": code, "message": message,
                "field": path_key(details.get("path")),
                "current_version": details.get("current_version"),
                "read_only": code in ("change_not_authorized", "anchor_quarantined"),
                "view": view,
            }), status
        warnings = edit_warnings(result.get("warnings"), registry)
        version_to = result.get("version")
        _audit("ok", version_to, [w["code"] for w in warnings])
        return jsonify({"success": True, "version": version_to, "warnings": warnings,
                        "view": view})

    @bp.route("/documents/fields/find", methods=["POST"])
    @require_admin
    def document_fields_find():
        """Feldfilter: one keyset page of documents by field values.

        Shown as filtered only when Knovas echoed ``where.applied``; without
        that echo there are no rows (H2). The incomplete-list notice comes
        from ``listing_notice`` and so only on the last page (H5).
        """
        if not _header_csrf_ok():
            return _json_error("csrf", "Sitzung abgelaufen. Bitte die Seite neu laden.", 403)
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        client = client_factory()
        if not _doc_fields_capability(client).shows_listing:
            return _json_error("listing_unavailable",
                               "Der Feldfilter ist bei Knovas f\u00fcr diesen Mandanten "
                               "nicht freigeschaltet.", 409)
        me = gate.current_user()
        try:
            registry = _registry(client, me)
        except DocFieldsUnavailable as exc:
            dfc.observe_exception(exc)
            return _json_error("doc_fields_off", OFF_TEXT, 409)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Feldverzeichnis nicht abrufbar: %s", type(exc).__name__)
            return _json_error("registry_unavailable",
                               "Das Feldverzeichnis ist derzeit nicht abrufbar.", 503)
        try:
            where = where_from_pairs(body.get("pairs"), registry)
            sort = sort_from_body(body.get("sort"), registry)
        except FormError as exc:
            return _json_error("invalid_filter", str(exc), 400)
        try:
            validate_where(where)
        except ValueError:
            return _json_error("invalid_filter",
                               "Der Filter ist zu umfangreich (h\u00f6chstens 8 Felder).", 400)
        after = body.get("after")
        after = after if isinstance(after, str) and 0 < len(after) <= 512 else None
        cfg = dfc.settings(getattr(client, "config", None))
        try:
            page = client.find_doc_values(where, sort=sort, limit=cfg.find_page_size,
                                          after=after, return_fields=card_return_fields(registry))
        except DocFieldsUnavailable as exc:
            dfc.observe_exception(exc)
            return _json_error("doc_fields_off", OFF_TEXT, 409)
        except DocFieldsError as exc:
            dfc.observe_exception(exc)
            if exc.error_code == "unknown_field":
                dfc.invalidate(getattr(me, "id", None))
            logger.warning("Feldfilter abgelehnt: %s %s", exc.status, exc.error_code)
            message, status = knovas_message(exc, registry)
            return _json_error(exc.error_code or "knovas_error", message, status)
        state = filter_state(where, page)
        if state == "not_applied":
            dfc.observe("echo_missing")
            return _json_error("filter_not_applied", error_message("filter_not_applied"), 409,
                               documents=[])
        documents = []
        for doc in page.get("documents") or ():
            if not isinstance(doc, dict):
                continue
            row = find_row(doc, registry)
            documents.append({"pointer": row["doc_id"], "title": row["title"],
                              "fields_display": row.get("fields_display") or []})
        next_after = page.get("next_after")
        payload = {
            "success": True,
            "documents": documents,
            "next_after": next_after if isinstance(next_after, str) and next_after else None,
            "complete": page.get("complete") is True,
            "notice": listing_notice(page),
            "filter_state": state,
            "resolved": resolved_chips(where, page.get("where"), registry),
        }
        if not documents:
            # Only a walk Knovas calls complete may say "no document" (H8, H9).
            empty = listing_empty_text(dict(page, documents=documents,
                                            next_after=payload["next_after"]))
            if empty:
                payload["empty_text"] = empty
        keys = [(sort or {}).get("field"), *(where or {})]
        if any(is_deadline_field(registry, k) for k in keys):
            payload["deadline_banner"] = DEADLINE_BANNER
        return jsonify(payload)

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
            created = client_factory().create_access_group(name, parent=parent)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gruppe nicht angelegt: %s", exc)
            return _groups_page(error="Gruppe konnte nicht angelegt werden.",
                                status=400)
        # None heisst: die API hat mit 404 geantwortet und nichts angelegt. Das
        # ist kein Fehler, den ein except faengt -- und ohne diese Pruefung
        # meldete die Seite den Erfolg, waehrend die Liste darunter die neue
        # Gruppe nicht enthielt. Ein leeres dict (204) ist dagegen ein Erfolg.
        if created is None:
            logger.error("Gruppe %r wurde von der Knovas-API nicht angelegt (404).", name)
            return _groups_page(
                error=(
                    "Die Knovas-API hat die Gruppe nicht angelegt — sie kennt "
                    "diesen Endpunkt nicht (HTTP 404). Das Log von docbridge-web "
                    "nennt die Antwort im Wortlaut."
                ),
                status=502,
            )
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

        me = gate.current_user()
        payload = {"action": "folder_rule_save", "rule_id": rule_id or None,
                   "pointer_prefix": prefix, "access_groups": groups}
        try:
            outcome = run_guarded(
                _approvals(), me, kind="acl_change", target_ref=rule_id or prefix,
                payload=payload,
                execute=lambda: execute_acl_change(
                    client_factory(), payload, actor=me, conn=gate.connection()
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ordnerregel nicht gespeichert: %s", exc)
            return _groups_page(
                error="Ordnerregel konnte nicht gespeichert werden.", status=400
            )
        if outcome.queued:
            return _groups_page(notice=_queued_notice(outcome.request))
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

        me = gate.current_user()
        payload = {"action": "folder_rule_delete", "rule_id": rule_id}
        try:
            outcome = run_guarded(
                _approvals(), me, kind="acl_change", target_ref=rule_id,
                payload=payload,
                execute=lambda: execute_acl_change(
                    client_factory(), payload, actor=me, conn=gate.connection()
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ordnerregel nicht geloescht: %s", exc)
            return _groups_page(error="Regel konnte nicht geloescht werden.", status=400)
        if outcome.queued:
            return _groups_page(notice=_queued_notice(outcome.request))
        return _groups_page(notice="Ordnerregel geloescht.")

    return bp
