"""The «Rollen» page of the administration console (RB-3) and its forms (RB-4).

Plan FEAT-2026-11-19-platform-mvp, items RB-3 and RB-4; work order docs/platform-mvp/work-orders/RB-3-4.md; design
docs/platform-mvp/design/role-builder.md (§ 1, § 3, § 6 rules 1, 3, 4, 5); mock-up
docs/platform-mvp/reference-screens/07-rollen.html. The store is RB-1's ``identity.roles.RoleStore``.

Load-bearing properties:

    One gate. Every route is the console's ``require_admin``, handed in by ``create_admin_blueprint`` and never
    redefined here. Every POST reads the form's ``csrf_token`` first (400), then finds the role (404), then runs the
    store's own checks (400), and only then asks the Core.

    The Core first (rule 5). A new role's group is created at the Core (a root group, named like the role) before the
    role is saved; a rename renames the group before the role, and the group's id stays. Members live in the Platform
    and reach the Core in the next claim, so adding and removing people makes no Core call. Deleting goes: the
    members (committed), the Core group, the role. A group the Core no longer knows (its 404) is gone already; any
    other refusal keeps the role, without its members. Administration and Mitglied get their groups only on «Mit
    Knovas verbinden». Nothing is written to the Core at start or on a GET.

    Every change with its row (rule 4). The store's write and its audit row (``audit.record_or_raise``) run in one
    transaction: a row that cannot be written undoes the change, and the page reports no success. The row names the
    role and the people by id and the functions by key; never a name or a description.

    A role never gives more than its creator holds (rule 1): ``roles.check_grantable`` on everything a form gives.
    ``admin`` holds every function, so it refuses nobody until RB-10. Granting oneself a role needs no second person:
    nothing here queues an approval.

    No customer text in logs (rule 7): ids, counts and the names of error classes. Never a role's name or
    description, a person's name or address, a Core group id (a tenant may choose a speaking one; the audit row holds
    it) or an error's text (a Core error quotes the address, which holds the group id).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Iterable, Optional

from flask import abort, render_template, request

from identity import audit
from identity.roles import (
    CUSTOM,
    FUNCTION_WORDS,
    RoleError,
    RoleNotFound,
    RoleStore,
    check_grantable,
)
from web_interface.admin_agents import role_agents

logger = logging.getLogger(__name__)

#: The group «Mit Knovas verbinden» creates for Mitglied (design § 3).
MITGLIED_GROUP_NAME = "Mitglied"

_EXPIRED = "Formular ist abgelaufen. Bitte erneut versuchen."
_NO_PERSON = "Person nicht gefunden."
_PROTECTED_NAME = "Administration und Mitglied behalten ihre Namen."
_PROTECTED_DELETE = "Die Rollen Administration und Mitglied können nicht gelöscht werden."
_NOT_SAVED = ("Die Änderung konnte nicht protokolliert werden und wurde deshalb nicht gespeichert. "
              "Bitte später erneut versuchen.")
_CORE_CREATE = ("Knovas hat die Gruppe der neuen Rolle nicht angelegt. Die Rolle wurde nicht angelegt; "
                "bitte später erneut versuchen.")
_CORE_RENAME = "Knovas hat die Gruppe der Rolle nicht umbenannt. Die Rolle behält ihren Namen."
_CORE_DELETE = ("Knovas hat die Gruppe der Rolle behalten, zum Beispiel weil ihr noch Daten zugeordnet sind. "
                "Die Rolle bleibt bestehen, jetzt ohne Mitglieder. Löschen Sie sie erneut, sobald Knovas die "
                "Gruppe freigibt.")
_CORE_UNREACHABLE = "Knovas hat die Gruppen des Mandanten nicht genannt. Nichts wurde verbunden."
_CORE_NO_ADMIN_GROUP = ("Knovas nennt für diesen Mandanten nicht genau eine Administratorgruppe. "
                        "Nichts wurde verbunden.")
_CORE_MITGLIED = "Knovas hat die Gruppe «Mitglied» nicht angelegt. Nichts wurde verbunden."
_GROUP_REMOVED = " Die neue Gruppe bei Knovas wurde wieder gelöscht."
_GROUP_LEFT = " Die neue Gruppe bei Knovas konnte nicht wieder gelöscht werden; sie steht im Protokoll."


class _NoChange(Exception):
    """Raised inside a change's transaction when the form changes nothing: nothing is written, no row."""


def _parse_id(value: Any) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value or "").strip())
    except ValueError:
        return None


def attach_role_routes(bp, gate, *, csrf_valid, csrf_token, page_context, client_factory, require_admin):
    """Mount the Rollen page and its forms onto the console's blueprint.

    Takes ``require_admin`` from the blueprint factory rather than redefining it, so there is exactly one definition
    of "who may reach the console", as ``attach_document_routes`` does. Returns the page function
    (``page(role_id, *, error, notice, status)``): the agents' forms of a role (``admin_agents``, RB-7) answer with it.
    """

    def _csrf_ok() -> bool:
        return csrf_valid(str(request.form.get("csrf_token", "") or ""))

    def _store() -> RoleStore:
        return RoleStore(gate.connection())

    # -- the page (RB-3) ------------------------------------------------------------------------------------------

    def _page(role_id: Any = None, *, error: Optional[str] = None, notice: Optional[str] = None,
              status: int = 200, create: Optional[dict] = None):
        """The page on ``role_id`` (on the first role without one). An unknown role is 404 for a page that is asked
        for, and the first role for a form's answer (a refusal's message must still be shown somewhere)."""
        roles = _store().list_roles()
        shown = roles[0] if roles else None
        if role_id is not None:
            wanted = _parse_id(role_id)
            found = next((r for r in roles if wanted is not None and r.id == wanted), None)
            if found is None and status == 200:
                abort(404)
            shown = found or shown
        if shown is None:  # only if the migration did not run: it seeds the two roles
            abort(404)
        people = sorted(gate.users().list_all(), key=lambda p: (p.display_name or "").casefold())
        members = [p for p in people if p.id in shown.members]
        by_kind = {r.kind: r for r in roles}
        # RB-6, RB-7: the role's agents under «Mitglieder» and the agents «+ Agent hinzufügen» offers.
        agents = role_agents(gate.connection(), shown, {p.id: p.display_name or p.email for p in people})
        return render_template(
            "admin_roles.html",
            active_nav="admin",
            **page_context(),
            roles=roles,
            shown=shown,
            members=members,
            candidates=[p for p in people if p.id not in shown.members],
            agents=agents,
            function_words=FUNCTION_WORDS,
            offered=offered_functions(),
            unlinked=[r for r in (by_kind.get("administration"), by_kind.get("mitglied"))
                      if r is not None and r.core_group_id is None],
            create=create,
            error=error,
            notice=notice,
            csrf_token=csrf_token(),
        ), status

    @bp.route("/roles")
    @require_admin
    def roles():
        return _page()

    @bp.route("/roles/<role_id>")
    @require_admin
    def role(role_id):
        return _page(role_id)

    # -- shared steps of the forms (RB-4) ---------------------------------------------------------------------------

    def _role_or_404(store: RoleStore, role_id: Any):
        try:
            return store.get_role(role_id)
        except RoleNotFound:
            abort(404)

    def _person_id(value: Any) -> uuid.UUID:
        """The person the form names: an id of an account of this installation, or a refusal."""
        ident = _parse_id(value)
        if ident is None or gate.users().get(ident) is None:
            raise RoleError(_NO_PERSON)
        return ident

    def _failed(role_id: Any, exc: Exception, what: str):
        """The answer to a change that did not stand: the store's refusal (400) or anything else (500)."""
        if isinstance(exc, RoleError):
            logger.info("Role %s: %s refused (%s)", role_id, what, type(exc).__name__)
            return _page(role_id, error=str(exc), status=400)
        logger.error("Role %s: %s not saved, %s", role_id, what, audit._error_code(exc))
        return _page(role_id, error=_NOT_SAVED, status=500)

    def _core_create(name: str, what: str) -> Optional[str]:
        """A new root group at the Core; its id, or None when the Core created none."""
        try:
            answer = client_factory().create_access_group(name, parent=None)
        except Exception as exc:  # noqa: BLE001 - every Core refusal is shown, none is raised
            logger.warning("Knovas refused the group of %s: %s", what, type(exc).__name__)
            return None
        group_id = answer.get("group_id") if isinstance(answer, dict) else None
        if not group_id:
            logger.warning("Knovas created no group for %s (no group id in its answer)", what)
            return None
        return str(group_id)

    def _core_delete_quietly(group_id: str, *, actor, role_id: Any = None) -> bool:
        """Delete a group this request created at the Core and could not use. Best effort: the outcome goes into the
        audit log by id (the log line names no group), and a group left behind is reported, never raised."""
        try:
            removed = client_factory().delete_access_group(group_id) is not False
        except Exception as exc:  # noqa: BLE001
            logger.warning("A Core group this request created could not be removed again: %s", type(exc).__name__)
            removed = False
        audit.record(gate.connection(), action="role.core_group_abandoned", actor=actor, target_type="role",
                     target_id=None if role_id is None else str(role_id), outcome="ok" if removed else "error",
                     detail={"core_group_id": group_id, "removed_at_core": removed})
        return removed

    # -- «Rolle anlegen» ----------------------------------------------------------------------------------------------

    @bp.route("/roles/create", methods=["POST"])
    @require_admin
    def create_role():
        if not _csrf_ok():
            return _page(error=_EXPIRED, status=400)
        me, conn, store = gate.current_user(), gate.connection(), _store()
        sent = {"name": str(request.form.get("name", "") or ""),
                "description": str(request.form.get("description", "") or ""),
                "functions": request.form.getlist("function")}
        try:
            name, functions, description = store.check_new(
                name=sent["name"], functions=sent["functions"], description=sent["description"])
            check_grantable(me.roles, functions)
        except RoleError as exc:
            logger.info("Role not created: %s (user %s)", type(exc).__name__, me.id)
            return _page(error=str(exc), status=400, create=sent)

        group_id = _core_create(name, "a new role")
        if group_id is None:
            return _page(error=_CORE_CREATE, status=502, create=sent)
        try:
            with conn.transaction():
                created = store.create_role(name=name, description=description, functions=functions,
                                            core_group_id=group_id, by=me.id)
                audit.record_or_raise(conn, action="role.created", actor=me, target_type="role",
                                      target_id=str(created.id),
                                      detail={"core_group_id": group_id, "functions": sorted(functions)})
        except Exception as exc:  # noqa: BLE001 - the Core's group must not outlive a role that was not saved
            suffix = _GROUP_REMOVED if _core_delete_quietly(group_id, actor=me) else _GROUP_LEFT
            if isinstance(exc, RoleError):
                logger.info("Role not created: %s (user %s)", type(exc).__name__, me.id)
                return _page(error=str(exc) + suffix, status=400, create=sent)
            logger.error("Role not created, %s", audit._error_code(exc))
            return _page(error=_NOT_SAVED + suffix, status=500, create=sent)
        return _page(created.id, notice="Rolle angelegt.")

    # -- «Umbenennen» -------------------------------------------------------------------------------------------------

    @bp.route("/roles/<role_id>/rename", methods=["POST"])
    @require_admin
    def rename_role(role_id):
        if not _csrf_ok():
            return _page(role_id, error=_EXPIRED, status=400)
        me, conn, store = gate.current_user(), gate.connection(), _store()
        role = _role_or_404(store, role_id)
        if role.kind != CUSTOM:  # decision 3: the two roles keep their names
            return _page(role.id, error=_PROTECTED_NAME, status=400)
        try:
            name = store.check_name(role.id, request.form.get("name", ""))
        except RoleError as exc:
            return _failed(role.id, exc, "rename")
        if name == role.name:
            return _page(role.id, notice="Der Name ist unverändert.")

        group_id = role.core_group_id
        if group_id is not None:
            try:
                answer = client_factory().rename_access_group(group_id, name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Role %s: Knovas refused the rename: %s", role.id, type(exc).__name__)
                answer = None
            if answer is None:
                return _page(role.id, error=_CORE_RENAME, status=502)
        try:
            with conn.transaction():
                store.rename_role(role.id, name=name, by=me.id)
                audit.record_or_raise(conn, action="role.renamed", actor=me, target_type="role",
                                      target_id=str(role.id), detail={"core_group_id": group_id})
        except Exception as exc:  # noqa: BLE001 - the Core's group gets its old name back
            if group_id is not None:
                try:
                    client_factory().rename_access_group(group_id, role.name)
                except Exception as back:  # noqa: BLE001
                    logger.warning("Role %s: the Core group keeps the new name: %s", role.id, type(back).__name__)
            return _failed(role.id, exc, "rename")
        return _page(role.id, notice="Rolle umbenannt.")

    # -- «Darf tun» → «Ändern» -------------------------------------------------------------------------------------

    @bp.route("/roles/<role_id>/functions", methods=["POST"])
    @require_admin
    def set_role_functions(role_id):
        if not _csrf_ok():
            return _page(role_id, error=_EXPIRED, status=400)
        me, conn, store = gate.current_user(), gate.connection(), _store()
        role = _role_or_404(store, role_id)
        try:
            functions = store.check_functions(role.id, request.form.getlist("function"))
            with conn.transaction():
                before = store.hold(role.id).functions
                if functions == before:
                    raise _NoChange()
                check_grantable(me.roles, functions - before)
                store.set_functions(role.id, functions, by=me.id)
                audit.record_or_raise(conn, action="role.functions_changed", actor=me, target_type="role",
                                      target_id=str(role.id),
                                      detail={"before": sorted(before), "after": sorted(functions)})
        except _NoChange:
            return _page(role.id, notice="Die Funktionen sind unverändert.")
        except Exception as exc:  # noqa: BLE001
            return _failed(role.id, exc, "functions")
        return _page(role.id, notice="Funktionen gespeichert. Sie gelten ab der nächsten Anfrage der Mitglieder.")

    # -- «Mitglieder» -------------------------------------------------------------------------------------------------

    @bp.route("/roles/<role_id>/members/add", methods=["POST"])
    @require_admin
    def add_role_member(role_id):
        if not _csrf_ok():
            return _page(role_id, error=_EXPIRED, status=400)
        me, conn, store = gate.current_user(), gate.connection(), _store()
        role = _role_or_404(store, role_id)
        try:
            person = _person_id(request.form.get("user_id"))
            with conn.transaction():
                current = store.hold(role.id)
                if person in current.members:
                    raise _NoChange()
                check_grantable(me.roles, current.functions)
                store.add_member(role.id, person, by=me.id)
                audit.record_or_raise(conn, action="role.member_added", actor=me, target_type="role",
                                      target_id=str(role.id), detail={"user_id": str(person)})
        except _NoChange:
            return _page(role.id, notice="Die Person ist schon Mitglied.")
        except Exception as exc:  # noqa: BLE001
            return _failed(role.id, exc, "add member")
        return _page(role.id, notice="Mitglied hinzugefügt. Es gilt ab der nächsten Anfrage der Person.")

    @bp.route("/roles/<role_id>/members/remove", methods=["POST"])
    @require_admin
    def remove_role_member(role_id):
        if not _csrf_ok():
            return _page(role_id, error=_EXPIRED, status=400)
        me, conn, store = gate.current_user(), gate.connection(), _store()
        role = _role_or_404(store, role_id)
        try:
            person = _person_id(request.form.get("user_id"))
            with conn.transaction():
                if person not in store.hold(role.id).members:
                    raise _NoChange()
                # Administration's row is held (hold, then remove_member), so a deactivation or another removal
                # at once waits and counts after this one (rule 3).
                store.remove_member(role.id, person, by=me.id)
                audit.record_or_raise(conn, action="role.member_removed", actor=me, target_type="role",
                                      target_id=str(role.id), detail={"user_id": str(person)})
        except _NoChange:
            return _page(role.id, notice="Die Person ist nicht Mitglied.")
        except Exception as exc:  # noqa: BLE001
            return _failed(role.id, exc, "remove member")
        return _page(role.id, notice="Mitglied entfernt. Das gilt ab der nächsten Anfrage der Person.")

    # -- deleting (design § 3: data, members, Core group, role; data is RB-5's) ---------------------------------

    def _remove_members(store: RoleStore, conn, role_id, me) -> list:
        """Every member out, each with its row; inside the caller's transaction. The ids removed."""
        removed = []
        for member in sorted(store.hold(role_id).members, key=str):
            store.remove_member(role_id, member, by=me.id)
            audit.record_or_raise(conn, action="role.member_removed", actor=me, target_type="role",
                                  target_id=str(role_id), detail={"user_id": str(member), "deleting_role": True})
            removed.append(str(member))
        return removed

    @bp.route("/roles/<role_id>/delete", methods=["POST"])
    @require_admin
    def delete_role(role_id):
        if not _csrf_ok():
            return _page(role_id, error=_EXPIRED, status=400)
        me, conn, store = gate.current_user(), gate.connection(), _store()
        role = _role_or_404(store, role_id)
        if role.kind != CUSTOM:
            return _page(role.id, error=_PROTECTED_DELETE, status=400)

        # 1. The members leave, committed: the Core is asked while the role stands without them.
        try:
            with conn.transaction():
                removed = _remove_members(store, conn, role.id, me)
                group_id = store.hold(role.id).core_group_id
        except Exception as exc:  # noqa: BLE001
            return _failed(role.id, exc, "delete (members)")

        # 2. The Core group. True: deleted. False (the client's 404): gone already (decision 11). Raised: refused.
        already_absent = False
        if group_id is not None:
            try:
                already_absent = client_factory().delete_access_group(group_id) is False
            except Exception as exc:  # noqa: BLE001 - 409 (data still name the group) or any other refusal
                logger.warning("Role %s: Knovas kept its group: %s; the role stays, without its %d members",
                               role.id, type(exc).__name__, len(removed))
                audit.record(conn, action="role.deleted", actor=me, target_type="role", target_id=str(role.id),
                             outcome="error",
                             detail={"core_group_id": group_id, "core_refused": True, "members_removed": removed})
                return _page(role.id, error=_CORE_DELETE, status=502)

        # 3. The role, and anyone who joined it since step 1.
        try:
            with conn.transaction():
                removed += _remove_members(store, conn, role.id, me)
                store.delete_role(role.id, by=me.id)
                detail = {"core_group_id": group_id, "members_removed": removed}
                if already_absent:
                    detail["core_group_already_absent"] = True
                audit.record_or_raise(conn, action="role.deleted", actor=me, target_type="role",
                                      target_id=str(role.id), detail=detail)
        except RoleNotFound:
            return _page(notice="Die Rolle gibt es nicht mehr.")
        except Exception as exc:  # noqa: BLE001 - the group is gone at the Core; deleting again finishes the role
            return _failed(role.id, exc, "delete (role)")
        return _page(notice="Rolle gelöscht.")

    # -- «Mit Knovas verbinden» (decision 2) ----------------------------------------------------------------------

    @bp.route("/roles/connect", methods=["POST"])
    @require_admin
    def connect_roles():
        if not _csrf_ok():
            return _page(error=_EXPIRED, status=400)
        me, conn, store = gate.current_user(), gate.connection(), _store()
        administration, mitglied = store.administration(), store.mitglied()
        if administration.core_group_id is not None and mitglied.core_group_id is not None:
            return _page(notice="Administration und Mitglied sind mit Knovas verbunden.")

        # Administration: the tenant's administrator group, read, never created (design § 3).
        links = []
        if administration.core_group_id is None:
            from web_interface.admin_doc_fields import admin_group_ids

            try:
                found = sorted(admin_group_ids(client_factory().access_groups()))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Knovas did not list the tenant's groups: %s", type(exc).__name__)
                return _page(error=_CORE_UNREACHABLE, status=502)
            if len(found) != 1:
                logger.warning("Administration not linked: Knovas names %d administrator groups", len(found))
                return _page(error=_CORE_NO_ADMIN_GROUP, status=502)
            links.append((administration.id, found[0]))

        # Mitglied: a new root group.
        created = None
        if mitglied.core_group_id is None:
            created = _core_create(MITGLIED_GROUP_NAME, "Mitglied")
            if created is None:
                return _page(error=_CORE_MITGLIED, status=502)
            links.append((mitglied.id, created))

        used = set()
        try:
            with conn.transaction():
                for role_id, group_id in links:
                    if store.hold(role_id).core_group_id is not None:  # linked by someone else meanwhile
                        continue
                    store.set_core_group(role_id, group_id, by=me.id)
                    audit.record_or_raise(conn, action="role.core_group_linked", actor=me, target_type="role",
                                          target_id=str(role_id), detail={"core_group_id": group_id})
                    used.add(group_id)
        except Exception as exc:  # noqa: BLE001
            if created is not None:
                _core_delete_quietly(created, actor=me, role_id=mitglied.id)
            return _failed(None, exc, "connect")
        if created is not None and created not in used:
            _core_delete_quietly(created, actor=me, role_id=mitglied.id)
        return _page(notice="Administration und Mitglied sind mit Knovas verbunden.")

    return _page


def offered_functions(words: Iterable[str] = FUNCTION_WORDS) -> list:
    """The functions a form offers as boxes, in the order of design § 2.1: every one but ``admin``, which belongs to
    Administration alone and is never a box (Administration's form keeps it as a hidden field)."""
    return [key for key in words if key != "admin"]
