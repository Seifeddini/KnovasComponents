"""The firm's administration console.

Five tabs -- Personen, Dokumente, Zugriffsgruppen, Ingestion, Freigaben. Walls
attaches to the same blueprint without a tab of its own. The first three and
Walls reuse ``require_admin``, Ingestion reuses ``require_ingestion`` (admin or
ingestion_manager), Freigaben ``require_approver`` (approver or admin). A
sixth, Dokumentfelder (``admin_doc_fields.py``, ``require_admin``), appears
only while Knovas offers document fields for this tenant.
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
import uuid
from datetime import datetime

from flask import Blueprint, abort, redirect, render_template, request, url_for

from identity import audit
from identity.passwords import WeakPasswordError
from identity.roles import FUNCTION_WORDS, LastAdministratorError, RoleError, RoleNotFound, RoleStore, check_grantable
from identity.users import EmailTakenError

logger = logging.getLogger(__name__)

#: experimenter / experiments_manager open the Experimente module (migration
#: 0003); without one of them a person never sees it. Since RB-4 the people
#: page offers roles (identity.roles), not these keys; the tuple stays because
#: tests name the console's functions through it.
ASSIGNABLE_ROLES = (
    "admin", "approver", "ingestion_manager", "member",
    "experimenter", "experiments_manager",
)


def create_admin_blueprint(
    gate, *, csrf_valid, csrf_token, page_context, client_factory, rc_client_factory=None
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
        """A route gate: signed in, and holding at least one of ``allowed``.

        The view it returns carries ``allowed`` as ``allowed_roles``: the very
        set the gate checks, so the web app's menu asks the route which roles
        it lets in instead of keeping a second list (PMVP-11). The attribute
        is read, never used to decide; the check below stays the control.
        """

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

            wrapped.allowed_roles = allowed
            return wrapped

        return decorator

    require_admin = _require_roles(frozenset({"admin"}))
    require_approver = _require_roles(frozenset({"admin", "approver"}))
    require_ingestion = _require_roles(frozenset({"admin", "ingestion_manager"}))

    def _form_csrf_ok() -> bool:
        return csrf_valid(str(request.form.get("csrf_token", "") or ""))

    from web_interface.admin_doc_fields import LazyCapability, admin_group_ids

    @bp.context_processor
    def _doc_fields_context():
        """``doc_fields_capability`` for every console template.

        Lazy: nothing is asked of Knovas until a template reads it (the tab
        strip does so only for an administrator), and it is asked at most
        once per render. Injected here rather than through ``page_context``
        so the app's own context stays as it is.
        """
        return {"doc_fields_capability": LazyCapability(client_factory)}

    def _knovas_admin_groups() -> frozenset:
        """Knovas's administrator group ids, for the People tab's hint.

        Only while document fields are on (the hint explains who may change
        the field registry); otherwise the tab makes no Knovas call, as
        before.
        """
        if not LazyCapability(client_factory).shows_values:
            return frozenset()
        try:
            return admin_group_ids(client_factory().access_groups())
        except Exception as exc:  # noqa: BLE001 - a hint must not break the page
            logger.warning("Knovas-Administratorgruppe nicht abrufbar: %s", type(exc).__name__)
            return frozenset()

    def _people_page(error=None, notice=None, status=200):
        repo = gate.users()
        knovas_admin_groups = _knovas_admin_groups()
        # The page offers roles (RB-4): every role of list_roles(), each person's checked. A new installation has
        # exactly Administration and Mitglied (founder, 2026-11-21); the tenant's own roles follow as created.
        roles = RoleStore(gate.connection()).list_roles()
        roles_of = {}
        for role in roles:
            for member in role.members:
                roles_of.setdefault(member, []).append(role)
        people = []
        for person in repo.list_all():
            people.append({
                "user": person,
                "roles": roles_of.get(person.id, []),
                # The person's own grants only: the form saves this list back
                # (set_groups), and a role's Core group copied into it would
                # outlive the role (RB-2).
                "access_groups": repo.direct_access_groups_of(person.id),
                # A hint, not a control: Knovas decides at every write. Read
                # from the claim's groups, which include the roles' groups.
                "knovas_admin": bool(knovas_admin_groups)
                and bool(knovas_admin_groups & set(repo.access_groups_of(person.id))),
            })
        return render_template(
            "admin_people.html",
            active_nav="admin",
            **page_context(),
            people=people,
            roles=roles,
            function_words=FUNCTION_WORDS,
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

    @bp.route("/system")
    @require_admin
    def system():
        """Ob die Aussenverbindungen wirklich tragen — als Seite, nicht als Log.

        Bewusst hinter require_admin: die Pruefungen nennen Basis-URL, Mandant
        und Pfade, und ein Fehlertext der API gehoert nicht auf eine Seite, die
        jede angemeldete Person sehen kann.
        """
        from web_interface import admin_system

        checks = admin_system.collect(
            client_factory, gate=gate, rc_client_factory=rc_client_factory
        )
        return render_template(
            "admin_system.html",
            checks=checks,
            summary=admin_system.summarise(checks),
            checked_at=datetime.now().strftime("%H:%M:%S"),
            **page_context(),
        )

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
        try:
            # Administration's row first, the lock every removal from it takes, then the count: a deactivation
            # and a removal at once queue, so the two can never leave Administration without an active person
            # (rule 3; the coordinator's amendment to RB-3/RB-4, F3).
            with gate.connection().transaction():
                RoleStore(gate.connection()).check_deactivation(user_id)
                gate.users().disable(user_id, by=me.id)
        except LastAdministratorError as exc:
            return _people_page(error=str(exc), status=400)
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
        """The person's roles become exactly the checked ones (RB-4): the form sends ``role_id`` per checked role.

        The old field ``roles`` (keys) is not read: every function comes from a role the page shows. One
        transaction for the whole form and its one audit row (``user.roles_changed``, before and after as role
        ids): a refusal half-way (the last administrator, a 33rd group, an unknown role or person) or a row that
        cannot be written leaves every role as it was. Every role's row is held first, in id order, before the
        person's roles are read: two saves queue, never deadlock, and the diff is current. Granting oneself a role
        needs no second person (founder, 2026-11-21).
        """
        if not _form_csrf_ok():
            return _people_page(error="Formular ist abgelaufen.", status=400)
        me = gate.current_user()
        conn = gate.connection()
        store = RoleStore(conn)
        wanted = {str(value).strip() for value in request.form.getlist("role_id")}
        try:
            with conn.transaction():
                store.lock_all()
                person = _person_id(request.form.get("user_id"))
                by_id = {str(role.id): role for role in store.list_roles()}
                if wanted - set(by_id):
                    raise RoleNotFound()
                before = {str(role.id) for role in store.roles_of_user(person)}
                # Design rule 1: nobody gives a function she does not hold herself (admin holds every one).
                check_grantable(me.roles, frozenset().union(*(by_id[r].functions for r in wanted - before)))
                # Leaving first, then joining: a person at the cap may swap one role's group for another's.
                for role_id in sorted(before - wanted):
                    store.remove_member(role_id, person, by=me.id)
                for role_id in sorted(wanted - before):
                    store.add_member(role_id, person, by=me.id)
                if before != wanted:
                    audit.record_or_raise(
                        conn, action="user.roles_changed", actor=me, target_type="user",
                        target_id=str(person), detail={"before": sorted(before), "after": sorted(wanted)},
                    )
        except RoleError as exc:
            logger.info("Roles of a person not changed: %s", type(exc).__name__)
            return _people_page(error=str(exc), status=400)
        except Exception as exc:  # noqa: BLE001 - the audit row or the database refused: nothing was kept
            logger.error("Roles of a person not changed, %s", audit._error_code(exc))
            return _people_page(
                error="Die Änderung konnte nicht protokolliert werden und wurde deshalb nicht gespeichert.",
                status=500,
            )
        return _people_page(notice="Rollen aktualisiert.")

    def _person_id(value):
        """An account of this installation by its id, or the store's refusal (``RoleError``)."""
        try:
            ident = uuid.UUID(str(value or "").strip())
        except ValueError:
            raise RoleError("Person nicht gefunden.") from None
        if gate.users().get(ident) is None:
            raise RoleError("Person nicht gefunden.")
        return ident

    @bp.route("/people/groups", methods=["POST"])
    @require_admin
    def set_groups():
        if not _form_csrf_ok():
            return _people_page(error="Formular ist abgelaufen.", status=400)
        user_id = str(request.form.get("user_id", "") or "")
        raw = str(request.form.get("access_groups", "") or "")
        wanted = [part.strip() for part in raw.split(",") if part.strip()]
        try:
            applied = gate.users().set_access_groups(
                user_id, wanted, by=gate.current_user().id
            )
        except RoleError as exc:  # more than 32 groups (RB-2): nothing was written
            return _people_page(error=str(exc), status=400)
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

    from web_interface.admin_roles import attach_role_routes

    role_page = attach_role_routes(
        bp,
        gate,
        csrf_valid=csrf_valid,
        csrf_token=csrf_token,
        page_context=page_context,
        client_factory=client_factory,
        require_admin=require_admin,
    )

    # Agents (RB-6, RB-7): the agent pages and the agents of a role, behind the same require_admin; a role's
    # agent forms answer with the Rollen page.
    from web_interface.admin_agents import attach_agent_routes

    attach_agent_routes(bp, gate, csrf_valid=csrf_valid, csrf_token=csrf_token, page_context=page_context,
                        require_admin=require_admin, role_page=role_page)

    from web_interface.admin_doc_fields import attach_doc_field_routes

    attach_doc_field_routes(
        bp,
        gate,
        csrf_valid=csrf_valid,
        csrf_token=csrf_token,
        page_context=page_context,
        client_factory=client_factory,
        rc_client_factory=rc_client_factory,
        require_admin=require_admin,
    )

    from web_interface.admin_approvals import attach_approval_routes
    from web_interface.admin_documents import execute_acl_change, execute_corpus_purge

    executors = {
        "acl_change": lambda payload, actor: execute_acl_change(
            client_factory(), payload, actor=actor, conn=gate.connection()
        ),
        # Registered so a queued purge can actually be carried out once a second
        # person confirms. Without an executor the Approvals tab records the
        # approval and tells the approver the console cannot perform it — which
        # for this action would leave the firm believing a wipe had been
        # authorised and not knowing whether it ran.
        "purge_all_documents": lambda payload, actor: execute_corpus_purge(
            client_factory(), payload, actor=actor, conn=gate.connection()
        ),
    }

    # A deployment without Knovas Connector still gets the other tabs; the
    # Ingestion routes and their executor only exist when rc_client_factory
    # is given.
    if rc_client_factory is not None:
        from web_interface.admin_ingestion import (
            attach_ingestion_routes,
            execute_ingestion_change,
        )

        attach_ingestion_routes(
            bp,
            gate,
            csrf_valid=csrf_valid,
            csrf_token=csrf_token,
            page_context=page_context,
            client_factory=client_factory,
            rc_client_factory=rc_client_factory,
            require_ingestion=require_ingestion,
            require_admin=require_admin,
        )
        executors["ingestion_profile_change"] = lambda payload, actor: (
            execute_ingestion_change(payload, actor, conn=gate.connection(),
                                     rc_client=rc_client_factory())
        )

    attach_approval_routes(
        bp,
        gate,
        csrf_valid=csrf_valid,
        csrf_token=csrf_token,
        page_context=page_context,
        require_approver=require_approver,
        require_admin=require_admin,
        executors=executors,
    )

    return bp
