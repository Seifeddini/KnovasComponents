"""The Freigaben tab: what is waiting for a second person, and who acted alone.

Pflichtenheft B5 (KC-B5-4). The queue lists pending requests with approve and
reject; an approver confirms someone else's request and the console then
carries the change out, once. The page also lists every administrator
bypass, because a control that quietly did not apply is worse than one a
buyer knows they lack (SS-338).

Plan: docs/superpowers/plans/2026-09-02-admin-approvals-tab.md
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

from flask import render_template, request

from identity import audit
from identity.approvals import (
    ApprovalError,
    ApprovalService,
    InvalidTransitionError,
    NotAnApproverError,
    RequestExpiredError,
    SelfApprovalError,
    UnknownRequestError,
)

logger = logging.getLogger(__name__)

KIND_LABELS = {
    "acl_change": "Zugriffsaenderung",
    "matter_delete": "Akte loeschen",
    "bulk_export": "Massenexport",
    "purge_all_documents": "Alle Dokumente loeschen",
    "ingestion_profile_change": "Ingestion-Profil aendern",
}


def _summary(kind: str, payload: Mapping[str, Any]) -> str:
    groups = ", ".join(str(g) for g in (payload.get("access_groups") or [])) or "offen"
    action = payload.get("action")
    if action == "document_acl":
        n = len(payload.get("pointers") or [])
        return f"{n} Dokument(e) -> {groups}"
    if action == "folder_rule_save":
        return f"Ordner {payload.get('pointer_prefix') or payload.get('rule_id')} -> {groups}"
    if action == "folder_rule_delete":
        return f"Ordnerregel {payload.get('rule_id')} loeschen"
    return KIND_LABELS.get(kind, kind)


def attach_approval_routes(
    bp,
    gate,
    *,
    csrf_valid,
    csrf_token,
    page_context,
    require_approver,
    require_admin,
    executors: dict[str, Callable[[Mapping[str, Any], Any], Mapping[str, Any]]],
):
    """Mount the Freigaben routes on the admin blueprint.

    ``executors`` maps a guarded kind to ``fn(payload, actor) -> result``; an
    approved request of a kind with no executor stays ``approved`` and the
    page says so, rather than pretending it was carried out.
    """

    def _csrf_ok() -> bool:
        return csrf_valid(str(request.form.get("csrf_token", "") or ""))

    def _approvals() -> ApprovalService:
        return ApprovalService(gate.connection(), gate.users())

    def _fmt(ts) -> str:
        return ts.strftime("%d.%m.%Y %H:%M") if ts else ""

    def _page(error=None, notice=None, status=200):
        me = gate.current_user()
        service = _approvals()
        service.expire_stale()
        users = gate.users()
        pending = []
        for req in service.pending():
            requester = users.get(req.requested_by)
            pending.append({
                "id": str(req.id),
                "kind": req.kind,
                "kind_label": KIND_LABELS.get(req.kind, req.kind),
                "target_ref": req.target_ref,
                "requester_email": requester.email if requester else "?",
                "requested_at": _fmt(req.requested_at),
                "expires_at": _fmt(req.expires_at),
                "summary": _summary(req.kind, req.payload),
                "mine": bool(me and str(req.requested_by) == str(me.id)),
                "executable": req.kind in executors,
            })
        bypasses = [
            {**row, "occurred_at": _fmt(row["occurred_at"])}
            for row in audit.recent(gate.connection(), action="approval.bypassed", limit=25)
        ]
        return render_template(
            "admin_approvals.html",
            active_nav="admin",
            **page_context(),
            pending=pending,
            bypasses=bypasses,
            bypass_enabled=service.admin_bypass_enabled(),
            can_toggle=bool(me and "admin" in me.roles),
            me=me,
            error=error,
            notice=notice,
            csrf_token=csrf_token(),
        ), status

    @bp.route("/approvals")
    @require_approver
    def approvals():
        return _page()

    @bp.route("/approvals/<request_id>/approve", methods=["POST"])
    @require_approver
    def approve(request_id):
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        me = gate.current_user()
        service = _approvals()
        try:
            req = service.approve(request_id, me)
        except SelfApprovalError:
            return _page(error="Die eigene Anfrage kann man nicht selbst freigeben.", status=400)
        except NotAnApproverError:
            return _page(error="Dieses Konto darf nicht freigeben.", status=403)
        except (RequestExpiredError, InvalidTransitionError, UnknownRequestError) as exc:
            return _page(error=f"Anfrage nicht freigebbar: {exc}", status=400)

        execute = executors.get(req.kind)
        if execute is None:
            return _page(notice=(
                "Freigegeben. Diese Art von Aenderung kann die Konsole noch nicht "
                "selbst ausfuehren; sie bleibt als freigegeben vermerkt."
            ))
        try:
            result = dict(execute(req.payload, me) or {})
        except Exception as exc:  # noqa: BLE001 - surfaced, the request stays approved
            logger.warning("Freigegebene Anfrage %s nicht ausgefuehrt: %s", req.id, exc)
            return _page(error=(
                "Freigegeben, aber die Ausfuehrung ist fehlgeschlagen. "
                "Bitte spaeter erneut versuchen."
            ), status=200)
        service.mark_executed(req.id, result)
        return _page(notice="Freigegeben und ausgefuehrt.")

    @bp.route("/approvals/<request_id>/reject", methods=["POST"])
    @require_approver
    def reject(request_id):
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        reason = str(request.form.get("reason", "") or "").strip()
        if not reason:
            return _page(error="Bitte eine Begruendung angeben.", status=400)
        me = gate.current_user()
        try:
            _approvals().reject(request_id, me, reason=reason)
        except SelfApprovalError:
            return _page(error="Die eigene Anfrage kann man nicht selbst ablehnen.", status=400)
        except ApprovalError as exc:
            return _page(error=f"Anfrage nicht ablehnbar: {exc}", status=400)
        return _page(notice="Abgelehnt.")

    @bp.route("/approvals/admin-bypass", methods=["POST"])
    @require_admin
    def set_bypass():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        enabled = str(request.form.get("enabled", "") or "") == "1"
        me = gate.current_user()
        _approvals().set_admin_bypass(enabled, by=me)
        return _page(notice=(
            "Administratoren handeln jetzt ohne zweite Person; jede solche Handlung wird vermerkt."
            if enabled else
            "Administratoren muessen jetzt ebenfalls eine Freigabe einholen."
        ))

    return bp
