"""Roles as bundles of functions, stored in the Platform's own database: the store the role builder builds on.

A role is a name, a description, **functions** (keys of the table ``roles``: what a person may do; the route gates
check them), at most one **Core group** (a Knovas access group id: what the role's members may read) and **members**
(people). A person's functions are the union of their roles' functions (``UserRepository.roles_of``); a person's claim
groups are their running direct grants plus their roles' Core groups (``claim_groups``). Plan
FEAT-2026-11-19-platform-mvp, items RB-1 and RB-2; design ``docs/platform-mvp/design/role-builder.md``; schema
``migrations/0027_roles.sql``, which also moved every person from the old per-key grants into roles.

Load-bearing properties:

    Two roles, one of each kind. «Administration» (kind ``administration``) always holds the function ``admin`` and
    is the only role that may; an administrator is therefore exactly a member of it. «Mitglied» (kind ``mitglied``)
    holds ``member``. Neither can be deleted. The kind, not the name, identifies them; both may be renamed.

    The last administrator stays. A change that would leave Administration without an active person is refused with
    ``LastAdministratorError``, nothing changed. The role's row is held (``FOR UPDATE``) while the members are
    counted, so two removals at once queue and the second one sees the first.

    At most 32 groups in a claim (the Core's ``QUERY_MAX_ASSERTED_GROUPS``). A change that gives a person a group they
    did not hold and leaves them with more than ``MAX_CLAIM_GROUPS`` is refused with ``TooManyGroupsError``, nothing
    written. A group held twice (directly and through a role, or through two roles) counts once. A change that adds
    no group is never refused, so a person stored with more groups before the cap can still be corrected.

    Nothing reaches the tenant. The store takes a connection and no client: it records ``core_group_id`` and never
    creates, renames or links a group in the Core (RB-4 does, on an administrator's action).

    Named errors, no customer text. Every refusal is a ``RoleError`` with a plain German message that is safe to
    show and names nobody: no role name, description, person's name or address. Log lines carry ids and counts only,
    DEBUG included; a Core group id is not logged either, since a tenant may choose a speaking one. A missing role, a
    role of another installation and a malformed id raise the same ``RoleNotFound``.

    One transaction per change (``conn.transaction()``, a savepoint inside a caller's transaction); every statement
    names the role or the person by id. One installation per database, as in the rest of identity/: no tenant column.

    One lock order, so two changes never deadlock: role rows first, then people's rows (in id order). A change of one
    role holds that role's row; a change that may touch several roles (``grant_role``, ``revoke_role``, the people
    page's form, ``role_for_function``) first holds every role's row in id order (``lock_all``), before any person's
    row. Two such changes therefore queue instead of each holding a role the other waits for.

``by`` is the acting person's id. It is stored as ``created_by`` / ``added_by`` and refuses nothing: granting oneself
a role needs no second person (founder, 21 November). RB-4 adds the audit rows and the rule that a creator gives only
functions she holds.

Rows are tuples, mapped by position, like the rest of identity/.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, List, Optional, Tuple
from uuid import UUID

import psycopg

logger = logging.getLogger(__name__)

#: The most access groups one claim may carry (the Core's QUERY_MAX_ASSERTED_GROUPS, design § 2.3).
MAX_CLAIM_GROUPS = 32

#: The longest role name, after trimming and collapsing white space.
NAME_MAX = 80

ADMINISTRATION = "administration"
MITGLIED = "mitglied"
CUSTOM = "custom"

#: The function only Administration holds, and the one Mitglied gives.
ADMIN = "admin"
MEMBER = "member"

#: The functions in plain words, in the order of design § 2.1. Migration 0027 names the roles of the move with the
#: same words in the same order; tests/test_identity_roles_store.py keeps the two in step.
FUNCTION_WORDS = {
    "member": "Fragen und suchen",
    "admin": "Verwaltung",
    "approver": "Freigaben bestätigen",
    "ingestion_manager": "Einlesen",
    "experimenter": "Experimente",
    "experiments_manager": "Experimente verwalten",
    "builder_viewer": "Builder lesen",
    "builder_author": "Builder bearbeiten",
    "builder_reviewer": "Builder prüfen",
    "builder_publisher": "Builder veröffentlichen",
}

class RoleError(Exception):
    """Every refusal of the store. Plain German, safe to show; never a name, a description or an address."""

    default = "Die Änderung an den Rollen wurde abgelehnt."

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(message or self.default)


class RoleNotFound(RoleError):
    """No role by that id here: missing, of another installation, or not an id at all. Says which never."""

    default = "Rolle nicht gefunden."


class RoleNameTaken(RoleError):
    default = "Eine Rolle mit diesem Namen gibt es schon."


class UnknownFunction(RoleError):
    """A function outside the keys of the table ``roles``."""

    default = "Diese Funktion gibt es nicht."


class ProtectedRoleError(RoleError):
    """Deleting Administration or Mitglied, taking ``admin`` from Administration, or giving it to another role."""

    default = "Diese Änderung ist bei den Rollen Administration und Mitglied nicht möglich."


class LastAdministratorError(RoleError):
    default = "Die Administration braucht mindestens eine aktive Person."


class TooManyGroupsError(RoleError):
    """A person would hold more than ``MAX_CLAIM_GROUPS`` access groups."""

    default = (f"Eine Person kann höchstens {MAX_CLAIM_GROUPS} Zugriffsgruppen haben, direkt und über ihre Rollen "
               "zusammen. Diese Änderung ergäbe mehr.")


_NAME_RULE = f"Ein Rollenname hat 1 bis {NAME_MAX} Zeichen."
_NO_PERSON = "Person nicht gefunden."
_DELETE_PROTECTED = "Die Rollen Administration und Mitglied können nicht gelöscht werden."
_ADMIN_STAYS = "Die Administration behält die Funktion «Verwaltung»."
_ADMIN_ONLY = "Die Funktion «Verwaltung» gehört allein zur Administration."


@dataclass(frozen=True)
class Role:
    id: UUID
    name: str
    description: str
    #: "administration" | "mitglied" | "custom"
    kind: str
    functions: frozenset
    core_group_id: Optional[str]
    #: the ids of the people in the role; agents come with RB-6 and RB-7, in tables of their own
    members: frozenset


def _uuid(value: Any) -> Optional[UUID]:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def clean_name(value: Any) -> str:
    """A role name as stored: trimmed, runs of white space as one space, 1 to 80 characters."""
    if not isinstance(value, str):
        raise RoleError(_NAME_RULE)
    name = " ".join(value.split())
    if not 1 <= len(name) <= NAME_MAX:
        raise RoleError(_NAME_RULE)
    return name


def _description(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RoleError("Die Beschreibung einer Rolle ist Text.")
    return value.strip()


def _core_group(value: Any) -> Optional[str]:
    """A Core group id, or None for none. An empty one is none."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise RoleError("Die Core-Gruppe einer Rolle ist eine Gruppen-Id.")
    return value.strip() or None


def _by(value: Any) -> Optional[UUID]:
    return _uuid(value)


# -- the claim's groups ---------------------------------------------------------------------------------------------


def claim_groups(conn: Any, user_id: Any) -> Tuple[str, ...]:
    """A person's claim groups: the direct grants that still run, plus the Core groups of their roles.

    Sorted and deduplicated by the database, as the direct grants always were, so two claims for the same person stay
    byte-comparable. What is stored comes back, even beyond the cap: the people page must still show a person whose
    grants were stored before it; the principal broker refuses to sign them.
    """
    rows = conn.execute(
        "SELECT group_id FROM user_access_groups "
        "WHERE user_id = %s AND (ends_on IS NULL OR ends_on >= current_date) "
        "UNION "
        "SELECT r.core_group_id FROM tenant_role_members m JOIN tenant_roles r ON r.id = m.role_id "
        "WHERE m.user_id = %s AND r.core_group_id IS NOT NULL "
        "ORDER BY 1",
        (str(user_id), str(user_id)),
    ).fetchall()
    return tuple(r[0] for r in rows)


@contextmanager
def group_cap(conn: Any, user_ids: Iterable[Any]) -> Iterator[None]:
    """Refuse the change made inside this block if it gives one of ``user_ids`` more than 32 groups.

    Use it inside the change's transaction: on refusal ``TooManyGroupsError`` leaves the block and the transaction
    is rolled back, so nothing is written. The people's rows are held for the block (``FOR NO KEY UPDATE``, in id
    order), so two changes for one person at once queue instead of both passing the count. A change counts as giving
    a group when the person holds one afterwards that they did not hold before; one that only keeps or removes groups
    passes, even above the cap.
    """
    people = sorted({str(u) for u in user_ids})
    if people:
        conn.execute("SELECT id FROM users WHERE id = ANY(%s::uuid[]) ORDER BY id FOR NO KEY UPDATE", (people,))
    before = {person: set(claim_groups(conn, person)) for person in people}
    yield
    for person in people:
        after = claim_groups(conn, person)
        if len(after) > MAX_CLAIM_GROUPS and set(after) - before[person]:
            logger.info("Refused: user %s would hold %d access groups (at most %d)",
                        person, len(after), MAX_CLAIM_GROUPS)
            raise TooManyGroupsError()


# -- the store -------------------------------------------------------------------------------------------------------

_ROLE_COLUMNS = (
    "SELECT r.id, r.name::text, r.description, r.kind, r.core_group_id, "
    "ARRAY(SELECT f.function FROM tenant_role_functions f WHERE f.role_id = r.id ORDER BY f.function), "
    "ARRAY(SELECT m.user_id FROM tenant_role_members m WHERE m.role_id = r.id ORDER BY m.user_id) "
    "FROM tenant_roles r "
)
#: Administration, Mitglied, then by name (case-insensitive: the name is citext).
_ROLE_ORDER = " ORDER BY CASE r.kind WHEN 'administration' THEN 0 WHEN 'mitglied' THEN 1 ELSE 2 END, r.name, r.id"


def _role(row: Any) -> Role:
    return Role(id=row[0], name=row[1], description=row[2], kind=row[3], core_group_id=row[4],
                functions=frozenset(row[5] or ()), members=frozenset(row[6] or ()))


class RoleStore:
    """The roles of this installation. Takes a live connection so a caller can compose it into a transaction."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # -- reading -------------------------------------------------------------------------------------------------

    def _select(self, where: str, params: tuple) -> List[Role]:
        return [_role(r) for r in self._conn.execute(_ROLE_COLUMNS + where + _ROLE_ORDER, params).fetchall()]

    def list_roles(self) -> List[Role]:
        """Every role: Administration, Mitglied, then the others by name."""
        return self._select("", ())

    def get_role(self, role_id: Any) -> Role:
        ident = _uuid(role_id)
        found = self._select("WHERE r.id = %s", (ident,)) if ident is not None else []
        if not found:
            raise RoleNotFound()
        return found[0]

    def _of_kind(self, kind: str) -> Role:
        found = self._select("WHERE r.kind = %s", (kind,))
        if not found:  # only if the migration did not run: the two roles are seeded with the tables
            raise RoleNotFound()
        return found[0]

    def administration(self) -> Role:
        return self._of_kind(ADMINISTRATION)

    def mitglied(self) -> Role:
        return self._of_kind(MITGLIED)

    def roles_of_user(self, user_id: Any) -> List[Role]:
        """The roles a person is a member of, in the order of ``list_roles``. None for a malformed id."""
        ident = _uuid(user_id)
        if ident is None:
            return []
        return self._select("WHERE r.id IN (SELECT m.role_id FROM tenant_role_members m WHERE m.user_id = %s)",
                            (ident,))

    # -- checks shared by the writes ----------------------------------------------------------------------------------

    def lock_all(self) -> None:
        """Hold every role's row, in id order, until the transaction ends.

        The first step of a change that may touch several roles (``grant_role``, ``revoke_role``, the people page's
        form): it takes all its role locks before any other lock, in one order, so two such changes queue and never
        deadlock. Rows this transaction already holds are taken again without waiting. Use inside a transaction.
        """
        self._conn.execute("SELECT id FROM tenant_roles ORDER BY id FOR UPDATE")

    def _lock(self, role_id: Any) -> Tuple[UUID, str]:
        """Hold the role's row until the transaction ends; its id and kind. Missing, foreign, malformed: RoleNotFound."""
        ident = _uuid(role_id)
        row = None if ident is None else self._conn.execute(
            "SELECT id, kind FROM tenant_roles WHERE id = %s FOR UPDATE", (ident,)).fetchone()
        if row is None:
            raise RoleNotFound()
        return row[0], row[1]

    def _person(self, user_id: Any) -> UUID:
        ident = _uuid(user_id)
        if ident is None or self._conn.execute("SELECT 1 FROM users WHERE id = %s", (ident,)).fetchone() is None:
            raise RoleError(_NO_PERSON)
        return ident

    def _functions(self, functions: Iterable[str], *, kind: str) -> frozenset:
        """The functions as given, checked: keys of ``roles`` only, and ``admin`` in Administration and nowhere else."""
        if isinstance(functions, str):
            raise TypeError("functions is a collection of keys, not one string")
        wanted = frozenset(functions or ())
        known = {r[0] for r in self._conn.execute("SELECT key FROM roles")}
        if not all(isinstance(f, str) and f in known for f in wanted):
            raise UnknownFunction()
        if kind == ADMINISTRATION and ADMIN not in wanted:
            raise ProtectedRoleError(_ADMIN_STAYS)
        if kind != ADMINISTRATION and ADMIN in wanted:
            raise ProtectedRoleError(_ADMIN_ONLY)
        return wanted

    def _name_free(self, name: str, role_id: Optional[UUID] = None) -> None:
        if self._conn.execute(
                "SELECT 1 FROM tenant_roles WHERE name = %s::citext AND id IS DISTINCT FROM %s",
                (name, role_id)).fetchone() is not None:
            raise RoleNameTaken()

    def _write_functions(self, role_id: UUID, functions: frozenset) -> None:
        self._conn.execute("DELETE FROM tenant_role_functions WHERE role_id = %s", (role_id,))
        for function in sorted(functions):
            self._conn.execute("INSERT INTO tenant_role_functions (role_id, function) VALUES (%s, %s)",
                               (role_id, function))

    # -- writing -------------------------------------------------------------------------------------------------

    def create_role(self, *, name: str, functions: Iterable[str] = (), description: str = "",
                    core_group_id: Optional[str] = None, by: Any = None) -> Role:
        """A new role of the tenant's own (kind ``custom``), without members.

        ``core_group_id`` is only recorded: creating the group in the Core is RB-4's, before it calls this.
        """
        clean = clean_name(name)
        text = _description(description)
        group = _core_group(core_group_id)
        try:
            with self._conn.transaction():
                wanted = self._functions(functions, kind=CUSTOM)
                self._name_free(clean)
                role_id = self._conn.execute(
                    "INSERT INTO tenant_roles (name, description, kind, core_group_id, created_by) "
                    "VALUES (%s, %s, 'custom', %s, %s) RETURNING id",
                    (clean, text, group, _by(by))).fetchone()[0]
                self._write_functions(role_id, wanted)
                # RB-6: the Knovas Assistent is a member of every role but Administration, also of later ones.
                from identity.agents import join_new_role

                join_new_role(self._conn, role_id, by=by)
        except psycopg.errors.UniqueViolation:
            # Another writer took the name between the check and the insert. The database's message names it.
            raise RoleNameTaken() from None
        logger.info("Role %s created with %d functions, %s Core group", role_id, len(wanted),
                    "a" if group else "no")
        return self.get_role(role_id)

    def rename_role(self, role_id: Any, *, name: str, by: Any = None) -> Role:
        """A new name; the id, the functions, the Core group and the members stay. Administration and Mitglied too."""
        clean = clean_name(name)
        try:
            with self._conn.transaction():
                ident, _ = self._lock(role_id)
                self._name_free(clean, ident)
                self._conn.execute("UPDATE tenant_roles SET name = %s, updated_at = now() WHERE id = %s",
                                   (clean, ident))
        except psycopg.errors.UniqueViolation:
            raise RoleNameTaken() from None
        logger.info("Role %s renamed", ident)
        return self.get_role(ident)

    def set_functions(self, role_id: Any, functions: Iterable[str], *, by: Any = None) -> Role:
        """Replace the role's functions. Its members' functions change on their next request."""
        with self._conn.transaction():
            ident, kind = self._lock(role_id)
            wanted = self._functions(functions, kind=kind)
            self._write_functions(ident, wanted)
            self._conn.execute("UPDATE tenant_roles SET updated_at = now() WHERE id = %s", (ident,))
        logger.info("Role %s now has %d functions", ident, len(wanted))
        return self.get_role(ident)

    def set_core_group(self, role_id: Any, core_group_id: Optional[str], *, by: Any = None) -> Role:
        """Record the role's Core group (None for none). Refused when a member would hold more than 32 groups.

        Only recorded: creating, adopting or renaming the group in the Core is RB-4's, before it calls this.
        """
        group = _core_group(core_group_id)
        with self._conn.transaction():
            ident, _ = self._lock(role_id)
            members = [r[0] for r in self._conn.execute(
                "SELECT user_id FROM tenant_role_members WHERE role_id = %s", (ident,))]
            # RB-6: also no 33rd group for an agent of the role acting on its own (people's rows, then agents').
            from identity.agents import agent_group_cap

            with group_cap(self._conn, members), agent_group_cap(self._conn, ident):
                self._conn.execute(
                    "UPDATE tenant_roles SET core_group_id = %s, updated_at = now() WHERE id = %s", (group, ident))
        logger.info("Role %s: Core group %s (%d members)", ident, "set" if group else "removed", len(members))
        return self.get_role(ident)

    def delete_role(self, role_id: Any, *, by: Any = None) -> None:
        """Delete a role of the tenant's own; its members leave it. Administration and Mitglied stay.

        Only the Platform's rows: removing the role's data and Core group in the Core first is RB-4's.
        """
        with self._conn.transaction():
            ident, kind = self._lock(role_id)
            if kind != CUSTOM:
                raise ProtectedRoleError(_DELETE_PROTECTED)
            members = self._conn.execute(
                "SELECT count(*) FROM tenant_role_members WHERE role_id = %s", (ident,)).fetchone()[0]
            self._conn.execute("DELETE FROM tenant_roles WHERE id = %s", (ident,))
        logger.info("Role %s deleted (%d members left it)", ident, members)

    def add_member(self, role_id: Any, user_id: Any, *, by: Any = None) -> None:
        """Put a person into the role; already a member is no change. ``by`` may be the person: no second person needed.

        Raises TooManyGroupsError, nothing written, when the role's Core group would be the person's 33rd group.
        """
        with self._conn.transaction():
            ident, _ = self._lock(role_id)
            person = self._person(user_id)
            with group_cap(self._conn, [person]):
                added = self._conn.execute(
                    "INSERT INTO tenant_role_members (role_id, user_id, added_by) VALUES (%s, %s, %s) "
                    "ON CONFLICT (role_id, user_id) DO NOTHING RETURNING user_id",
                    (ident, person, _by(by))).fetchone()
        if added is not None:
            logger.info("Role %s: user %s added", ident, person)

    def remove_member(self, role_id: Any, user_id: Any, *, by: Any = None) -> None:
        """Take a person out of the role; not a member is no change.

        Raises LastAdministratorError, nothing changed, when Administration would keep no active person.
        """
        with self._conn.transaction():
            ident, kind = self._lock(role_id)
            person = self._person(user_id)
            removed = self._conn.execute(
                "DELETE FROM tenant_role_members WHERE role_id = %s AND user_id = %s RETURNING user_id",
                (ident, person)).fetchone()
            if removed is not None and kind == ADMINISTRATION:
                # Counted while the role's row is held (_lock), so a second removal waits and counts after this one.
                active = self._conn.execute(
                    "SELECT count(*) FROM tenant_role_members m JOIN users u ON u.id = m.user_id "
                    "WHERE m.role_id = %s AND u.status = 'active'", (ident,)).fetchone()[0]
                if active == 0:
                    logger.info("Refused: removing user %s would leave Administration without an active person",
                                person)
                    raise LastAdministratorError()
        if removed is not None:
            logger.info("Role %s: user %s removed", ident, person)

    # -- one function, one role (grant_role and revoke_role) ----------------------------------------------------------

    def role_for_function(self, function: str, *, by: Any = None) -> Role:
        """The role that gives exactly ``function``: what ``UserRepository.grant_role`` puts a person into.

        ``admin`` is Administration and ``member`` is Mitglied. Any other function is the oldest role of the tenant's
        own whose functions are exactly that one and which has no Core group, so granting a function never widens
        what a person may read. It is created on first use, named with the function's words (design § 2.1), or with
        " (2)", " (3)" … when another role already has that name.
        """
        if function == ADMIN:
            return self.administration()
        if function == MEMBER:
            return self.mitglied()
        with self._conn.transaction():
            self._functions([function], kind=CUSTOM)
            # Every role's row, so two grants of one function at once queue here: the second one finds the role the
            # first one created instead of making another.
            self.lock_all()
            row = self._conn.execute(
                "SELECT r.id FROM tenant_roles r WHERE r.kind = 'custom' AND r.core_group_id IS NULL "
                "AND ARRAY(SELECT f.function FROM tenant_role_functions f WHERE f.role_id = r.id) = ARRAY[%s]::text[] "
                "ORDER BY r.created_at, r.id LIMIT 1", (function,)).fetchone()
            if row is not None:
                return self.get_role(row[0])
            taken = {r[0].lower() for r in self._conn.execute("SELECT name::text FROM tenant_roles")}
            base = FUNCTION_WORDS.get(function, function)
            name, number = base, 1
            while name.lower() in taken:
                number += 1
                name = f"{base} ({number})"
            return self.create_role(name=name, functions={function}, by=by)
