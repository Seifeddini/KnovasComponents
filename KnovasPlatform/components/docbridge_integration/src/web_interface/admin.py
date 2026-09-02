"""The firm's administration console.

Four tabs — Personen, Dokumente, Zugriffsgruppen, Freigaben. The others
(Walls, Ingestion) attach to the same blueprint and reuse ``require_admin``.
The document and folder-rule routes live in ``admin_documents.py`` and the
approvals routes in ``admin_approvals.py``; both are mounted here so there is
one blueprint and one gate.

Every route is authorised on the *route*, not on whether the link is drawn.
Hiding a page is presentation; refusing the POST is the control, and the tests
assert the POST directly for exactly that reason.

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-B1-5)
"""
from __future__ import annotations

import functools
import logging

from flask import (
    Blueprint, abort, current_app, redirect, render_template, request, session, url_for
)

from identity import audit
from identity.passwords import WeakPasswordError
from identity.users import EmailTakenError, UnknownRoleError

logger = logging.getLogger(__name__)

ASSIGNABLE_ROLES = ("admin", "approver", "ingestion_manager", "member")


def create_admin_blueprint(
    gate, *, csrf_valid, csrf_token, page_context, client_factory
):
    """Build the console blueprint.

    Takes the gate and the app's own CSRF helpers rather than importing them,
    so this module has no dependency on ``app.py`` and can be tested against a
    minimal app. ``client_factory`` returns the Knovas client the console uses
    to reach the RBAC endpoints — the same one the search path uses, so mTLS
    material, retries and rate limiting are configured in exactly one place.
    """
    bp = Blueprint("admin", __name__, url_prefix="/admin")

    def _require_roles(allowed: frozenset[str]):
        """A route gate: signed in, and holding at least one of ``allowed``."""

        def decorator(view):
            @functools.wraps(view)
            def wrapped(*args, **kwargs):
                user = gate.current_user()
                if user is None:
                    return redirect(
                        url_for("login", next=request.full_path or "/admin/people")
                    )
                if not (allowed & set(user.roles)):
                    # 403, not 404: the person is authenticated and the
                    # console is not a secret. Hiding it would only make a
                    # misconfigured account harder to diagnose.
                    abort(403)
                return view(*args, **kwargs)

            return wrapped

        return decorator

    require_admin = _require_roles(frozenset({"admin"}))
    require_approver = _require_roles(frozenset({"admin", "approver"}))

    def _form_csrf_ok() -> bool:
        return csrf_valid(str(request.form.get("csrf_token", "") or ""))

    def _people_page(error=None, notice=None, status=200):
        repo = gate.users()
        people = [
            {
                "user": person,
                "access_groups": repo.access_groups_of(person.id),
            }
            for person in repo.list_all()
        ]
        return render_template(
            "admin_people.html",
            active_nav="admin",
            **page_context(),
            people=people,
            assignable_roles=ASSIGNABLE_ROLES,
            me=gate.current_user(),
            error=error,
            notice=notice,
            csrf_token=csrf_token(),
        ), status

    @bp.route("/")
    @require_admin
    def index():
        return redirect(url_for("admin.people"))

    @bp.route("/people")
    @require_admin
    def people():
        return _people_page()

    @bp.route("/people/create", methods=["POST"])
    @require_admin
    def create_person():
        if not _form_csrf_ok():
            return _people_page(error="Formular ist abgelaufen. Bitte erneut versuchen.",
                                status=400)
        email = str(request.form.get("email", "") or "").strip()
        display_name = str(request.form.get("display_name", "") or "").strip() or email
        password = str(request.form.get("password", "") or "")
        repo = gate.users()
        try:
            # must_change_password is not optional here: an administrator who
            # picks someone else's password means two people know it, and only
            # one of them should after the first sign-in.
            created = repo.create(
                email=email,
                display_name=display_name,
                password=password,
                must_change_password=True,
                created_by=gate.current_user().id,
            )
        except WeakPasswordError as exc:
            return _people_page(error="; ".join(exc.reasons), status=400)
        except EmailTakenError as exc:
            return _people_page(error=str(exc), status=400)

        repo.grant_role(created.id, "member", by=gate.current_user().id)
        audit.record(
            gate.connection(), action="user.created", actor=gate.current_user(),
            target_type="user", target_id=str(created.id), detail={"email": email},
        )
        return _people_page(notice=f"{email} wurde angelegt.")

    @bp.route("/people/disable", methods=["POST"])
    @require_admin
    def disable_person():
        if not _form_csrf_ok():
            return _people_page(error="Formular ist abgelaufen.", status=400)
        me = gate.current_user()
        user_id = str(request.form.get("user_id", "") or "")
        if user_id == str(me.id):
            # Locking the last administrator out of their own system is a
            # support call, not a security control.
            return _people_page(
                error="Sie können Ihr eigenes Konto nicht deaktivieren.", status=400
            )
        gate.users().disable(user_id, by=me.id)
        revoked = gate.sessions().revoke_all_for_user(user_id)
        audit.record(
            gate.connection(), action="user.disabled", actor=me,
            target_type="user", target_id=user_id,
            detail={"sessions_revoked": revoked},
        )
        return _people_page(notice="Konto deaktiviert und abgemeldet.")

    @bp.route("/people/enable", methods=["POST"])
    @require_admin
    def enable_person():
        if not _form_csrf_ok():
            return _people_page(error="Formular ist abgelaufen.", status=400)
        user_id = str(request.form.get("user_id", "") or "")
        gate.users().enable(user_id)
        audit.record(
            gate.connection(), action="user.enabled", actor=gate.current_user(),
            target_type="user", target_id=user_id,
        )
        return _people_page(notice="Konto wieder aktiviert.")

    @bp.route("/people/roles", methods=["POST"])
    @require_admin
    def set_roles():
        if not _form_csrf_ok():
            return _people_page(error="Formular ist abgelaufen.", status=400)
        user_id = str(request.form.get("user_id", "") or "")
        wanted = [r for r in request.form.getlist("roles") if r in ASSIGNABLE_ROLES]
        repo = gate.users()
        current = repo.roles_of(user_id)
        try:
            for role in set(current) - set(wanted):
                repo.revoke_role(user_id, role)
            for role in set(wanted) - set(current):
                repo.grant_role(user_id, role, by=gate.current_user().id)
        except UnknownRoleError as exc:
            return _people_page(error=str(exc), status=400)
        audit.record(
            gate.connection(), action="user.roles_changed", actor=gate.current_user(),
            target_type="user", target_id=user_id, detail={"roles": sorted(wanted)},
        )
        return _people_page(notice="Rollen aktualisiert.")

    @bp.route("/people/groups", methods=["POST"])
    @require_admin
    def set_groups():
        if not _form_csrf_ok():
            return _people_page(error="Formular ist abgelaufen.", status=400)
        user_id = str(request.form.get("user_id", "") or "")
        raw = str(request.form.get("access_groups", "") or "")
        wanted = [part.strip() for part in raw.split(",") if part.strip()]
        applied = gate.users().set_access_groups(
            user_id, wanted, by=gate.current_user().id
        )
        audit.record(
            gate.connection(), action="user.access_groups_changed",
            actor=gate.current_user(), target_type="user", target_id=user_id,
            detail={"access_groups": list(applied)},
        )
        return _people_page(notice="Zugriffsgruppen aktualisiert.")

    @bp.route("/people/reset-password", methods=["POST"])
    @require_admin
    def reset_password():
        if not _form_csrf_ok():
            return _people_page(error="Formular ist abgelaufen.", status=400)
        user_id = str(request.form.get("user_id", "") or "")
        password = str(request.form.get("password", "") or "")
        repo = gate.users()
        try:
            repo.set_password(user_id, password)
        except WeakPasswordError as exc:
            return _people_page(error="; ".join(exc.reasons), status=400)
        repo._conn.execute(
            "UPDATE users SET must_change_password = TRUE WHERE id = %s", (user_id,)
        )
        # A reset is what you do when a credential may be compromised; leaving
        # the old sessions alive would defeat it.
        revoked = gate.sessions().revoke_all_for_user(user_id)
        audit.record(
            gate.connection(), action="user.password_reset", actor=gate.current_user(),
            target_type="user", target_id=user_id, detail={"sessions_revoked": revoked},
        )
        return _people_page(notice="Passwort zurückgesetzt und Sitzungen beendet.")

    @bp.route("/people/sign-out", methods=["POST"])
    @require_admin
    def sign_out_person():
        if not _form_csrf_ok():
            return _people_page(error="Formular ist abgelaufen.", status=400)
        user_id = str(request.form.get("user_id", "") or "")
        revoked = gate.sessions().revoke_all_for_user(user_id)
        audit.record(
            gate.connection(), action="user.sessions_revoked",
            actor=gate.current_user(), target_type="user", target_id=user_id,
            detail={"sessions_revoked": revoked},
        )
        return _people_page(notice=f"{revoked} Sitzung(en) beendet.")

    from web_interface.admin_documents import attach_document_routes

    attach_document_routes(
        bp,
        gate,
        csrf_valid=csrf_valid,
        csrf_token=csrf_token,
        page_context=page_context,
        client_factory=client_factory,
        require_admin=require_admin,
    )

    from web_interface.admin_approvals import attach_approval_routes
    from web_interface.admin_documents import execute_acl_change

    attach_approval_routes(
        bp,
        gate,
        csrf_valid=csrf_valid,
        csrf_token=csrf_token,
        page_context=page_context,
        require_approver=require_approver,
        require_admin=require_admin,
        executors={
            "acl_change": lambda payload, actor: execute_acl_change(
                client_factory(), payload, actor=actor, conn=gate.connection()
            ),
        },
    )

    return bp
