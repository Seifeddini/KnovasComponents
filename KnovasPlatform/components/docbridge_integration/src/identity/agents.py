"""Agents as members of roles: the Knovas Assistent and external agents, and the one claim every agent gets.

An agent is a member of roles that is not a person (plan FEAT-2026-11-19-platform-mvp, items RB-6 and RB-7; design
``docs/platform-mvp/design/role-builder.md`` § 4 and § 5; schema ``migrations/0028_agents.sql``):

- the **Knovas Assistent** (kind ``assistant``), exactly one. It goes through the Platform and always acts for the
  signed-in person (``web_interface.assistant_routes``). It is a member of every role but Administration, also of
  roles created later (``RoleStore.create_role``), until an administrator takes it out of one;
- an **external agent** (kind ``external``), registered by an administrator. It goes through the Core's MCP gateway,
  acts for one person fixed when it is created (``on_behalf_of``) or on its own, and starts with no role. Its key is
  the gateway's, issued by Knovas for the agent's id (CR-R6): the Platform holds no agent credential.

Load-bearing properties:

    One claim for every agent (``claim_for``). Subject: the agent's id. Groups and functions: those of its roles.
    Acting for a person (an external agent's fixed person, or the person the assistant answers), only what that
    person also holds: the person's claim groups (``roles.claim_groups``) and functions (her roles' functions). The
    person must be active. A revoked agent has no claim, nor has the assistant without a person. A person's direct
    group grants reach no agent (FU-41): an agent's groups come from its roles only, the person narrows them.

    No agent joins Administration. ``add_to_role`` refuses it with RB-1's ``ProtectedRoleError``, and the claim
    leaves Administration and the function ``admin`` out even if a row said otherwise.

    At most 32 groups (``roles.MAX_CLAIM_GROUPS``). ``claim_for`` raises RB-1's ``TooManyGroupsError`` for more and
    never cuts the list. An external agent acting on its own carries all its roles' groups, so ``add_to_role`` (and
    ``RoleStore.set_core_group``, through ``agent_group_cap``) refuse it a 33rd group, nothing written. The assistant
    and person-bound agents are narrowed to a person, who holds at most 32 (RB-2).

    Every change is audited by id in its own transaction: ``agent.created``, ``agent.revoked``, ``agent.role_added``,
    ``agent.role_removed``, ``actor_user_id`` = ``by``, target the agent, ids in ``detail``. An audit row that
    cannot be written takes the change with it.

    Named errors, no customer text. Every refusal is an ``AgentError`` (or RB-1's ``RoleError`` for roles and
    groups) with a plain German message that is safe to show and names nobody. Log lines carry ids and counts only,
    DEBUG included: never an agent's name, a person's name, a group id or a claim. A missing agent, an agent of
    another installation and a malformed id raise the same ``AgentNotFound``.

    One lock order with ``identity.roles``: role rows first, then people's rows, then agents' rows (in id order).
    Nothing here reaches the tenant: the store takes a connection and no client. Delivering external agents' claims
    to the Core is ``identity.agent_delivery``'s, after a change is committed.

``by`` is the acting person's id; it is stored as ``created_by`` / ``revoked_by`` / ``added_by`` and as the audit
row's actor. Rows are tuples, mapped by position, like the rest of identity/.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple
from uuid import UUID

import psycopg

from identity.roles import (
    ADMIN,
    ADMINISTRATION,
    MAX_CLAIM_GROUPS,
    ProtectedRoleError,
    Role,
    RoleNotFound,
    RoleStore,
    TooManyGroupsError,
    claim_groups,
)

logger = logging.getLogger(__name__)

ASSISTANT = "assistant"
EXTERNAL = "external"

#: The assistant's name, as the migration seeds it.
ASSISTANT_NAME = "Knovas Assistent"

#: The longest agent name, after trimming and collapsing white space.
NAME_MAX = 80


class AgentError(Exception):
    """Every refusal about an agent. Plain German, safe to show; never a name, an address or a claim."""

    default = "Die Änderung an den Agenten wurde abgelehnt."

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(message or self.default)


class AgentNotFound(AgentError):
    """No agent by that id here: missing, of another installation, or not an id at all. Says which never."""

    default = "Agent nicht gefunden."


class AgentNameTaken(AgentError):
    default = "Einen Agenten mit diesem Namen gibt es schon."


class ProtectedAgentError(AgentError):
    """Revoking the Knovas Assistent: it has its own switch (``assistant.enabled``)."""

    default = ("Der Knovas Assistent kann nicht widerrufen werden. Er wird mit seinem Schalter in den Einstellungen "
               "ausgeschaltet; aus einer Rolle nehmen Sie ihn auf der Seite der Rolle.")


class AgentGroupsError(TooManyGroupsError):
    """An agent acting on its own would carry more than 32 groups. A ``TooManyGroupsError`` (RB-1)."""

    default = (f"Ein Agent, der selbständig handelt, kann höchstens {MAX_CLAIM_GROUPS} Zugriffsgruppen haben, über "
               "seine Rollen zusammen. Diese Änderung ergäbe mehr.")


_NAME_RULE = f"Ein Agentenname hat 1 bis {NAME_MAX} Zeichen."
_NO_PERSON = "Ein Agent handelt für eine aktive Person oder selbständig. Diese Person gibt es nicht oder sie ist nicht aktiv."
_REVOKED = "Dieser Agent ist widerrufen und hat keine Rechte."
_PERSON_INACTIVE = "Die Person, für die dieser Agent handelt, ist nicht aktiv. Er hat keine Rechte."
_NEEDS_PERSON = "Der Knovas Assistent handelt nur für eine angemeldete Person."
_OTHER_PERSON = "Dieser Agent handelt nicht für diese Person."
_NO_ADMINISTRATION = "Kein Agent gehört zur Administration."


@dataclass(frozen=True)
class Agent:
    id: UUID
    name: str
    #: "assistant" | "external"
    kind: str
    #: the person an external agent acts for, fixed when it is created; None: on its own (and the assistant)
    on_behalf_of: Optional[UUID]
    #: the ids of the roles the agent is a member of
    roles: frozenset
    revoked: bool


@dataclass(frozen=True)
class AgentClaim:
    """What the Platform signs for an agent: ``sub``, ``grp`` and ``rol`` of the principal assertion."""

    subject: str
    groups: Tuple[str, ...]
    functions: Tuple[str, ...]
    on_behalf_of: Optional[UUID]


@dataclass(frozen=True)
class AgentPrincipal:
    """An agent acting for a person, as ``KnovasAPIClient.asserting_for`` signs it into a Core call.

    ``PrincipalBroker.assertion_for`` signs ``AgentStore.claim_for(agent_id, on_behalf_of=...)`` for it, and the
    client declares ``declared_actor`` on the call (the body's ``actor``, or ``?actor=`` on a GET): the Core records
    it and decides nothing on it (``services/request_actor.py``).
    """

    agent_id: UUID
    on_behalf_of: Optional[UUID] = None

    @property
    def declared_actor(self) -> Dict[str, Any]:
        actor: Dict[str, Any] = {"kind": "agent", "id": str(self.agent_id)}
        if self.on_behalf_of is not None:
            actor["on_behalf_of"] = {"kind": "user", "id": str(self.on_behalf_of)}
        return actor


def _uuid(value: Any) -> Optional[UUID]:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return UUID(value.strip())
    except ValueError:
        return None


def clean_name(value: Any) -> str:
    """An agent name as stored: trimmed, runs of white space as one space, 1 to 80 characters."""
    if not isinstance(value, str):
        raise AgentError(_NAME_RULE)
    name = " ".join(value.split())
    if not 1 <= len(name) <= NAME_MAX:
        raise AgentError(_NAME_RULE)
    return name


def _audit(conn: Any, action: str, agent_id: UUID, by: Optional[UUID], detail: Dict[str, Any]) -> None:
    """The change's audit row, by id, in the change's transaction (design § 6 rule 4). Raises: no row, no change."""
    conn.execute(
        "INSERT INTO audit_log (actor_user_id, action, target_type, target_id, outcome, detail) "
        "VALUES (%s, %s, 'agent', %s, 'ok', %s::jsonb)",
        (by, action, str(agent_id), json.dumps(detail)))


# -- the groups and functions of an agent's roles ------------------------------------------------------------------


def _role_groups(conn: Any, agent_id: Any) -> Tuple[str, ...]:
    """The Core groups of the agent's roles, sorted and deduplicated; never Administration's group.

    The Core decides on group ids, not on roles: Administration's group is left out even when another role carries
    the same id, so no agent ever reads with the administrators' group.
    """
    rows = conn.execute(
        "SELECT DISTINCT r.core_group_id FROM tenant_role_agents a JOIN tenant_roles r ON r.id = a.role_id "
        "WHERE a.agent_id = %s AND r.core_group_id IS NOT NULL AND r.kind <> 'administration' "
        "AND r.core_group_id NOT IN (SELECT x.core_group_id FROM tenant_roles x "
        "WHERE x.kind = 'administration' AND x.core_group_id IS NOT NULL) ORDER BY 1",
        (str(agent_id),)).fetchall()
    return tuple(r[0] for r in rows)


@contextmanager
def _snapshot(conn: Any) -> Iterator[None]:
    """One consistent read for a claim: a read-only REPEATABLE READ transaction when the connection is idle.

    Inside a caller's transaction the reads join it (its snapshot, its isolation) as before.
    """
    try:
        from psycopg import pq

        idle = conn.info.transaction_status == pq.TransactionStatus.IDLE
    except Exception:  # noqa: BLE001 - not a psycopg connection: read as it comes
        idle = False
    if not idle:
        yield
        return
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        yield


def _role_functions(conn: Any, agent_id: Any) -> Tuple[str, ...]:
    """The functions of the agent's roles, sorted and deduplicated; never Administration's, never ``admin``."""
    rows = conn.execute(
        "SELECT DISTINCT f.function FROM tenant_role_agents a "
        "JOIN tenant_roles r ON r.id = a.role_id JOIN tenant_role_functions f ON f.role_id = a.role_id "
        "WHERE a.agent_id = %s AND r.kind <> 'administration' AND f.function <> %s ORDER BY 1",
        (str(agent_id), ADMIN)).fetchall()
    return tuple(r[0] for r in rows)


def _person_functions(conn: Any, user_id: Any) -> frozenset:
    """A person's functions: the functions of her roles (as ``UserRepository.roles_of`` reads them)."""
    rows = conn.execute(
        "SELECT DISTINCT f.function FROM tenant_role_members m JOIN tenant_role_functions f ON f.role_id = m.role_id "
        "WHERE m.user_id = %s", (str(user_id),)).fetchall()
    return frozenset(r[0] for r in rows)


@contextmanager
def agent_group_cap(conn: Any, role_id: Any) -> Iterator[None]:
    """Refuse the change made inside this block if it gives an agent of ``role_id`` acting on its own a 33rd group.

    For ``RoleStore.set_core_group`` (and ``add_to_role``): use it inside the change's transaction, after the role's
    row is held. The agents' rows are held for the block (``FOR NO KEY UPDATE``, in id order). Only external agents
    acting on their own and not revoked count: the assistant and person-bound agents are narrowed to a person, who
    holds at most 32. As for people (``roles.group_cap``), a change that gives no new group passes, even above 32.
    """
    agents = [r[0] for r in conn.execute(
        "SELECT a.id FROM agents a JOIN tenant_role_agents m ON m.agent_id = a.id "
        "WHERE m.role_id = %s AND a.kind = 'external' AND a.on_behalf_of IS NULL AND a.revoked_at IS NULL "
        "ORDER BY a.id FOR NO KEY UPDATE OF a", (str(role_id),)).fetchall()]
    before = {agent: set(_role_groups(conn, agent)) for agent in agents}
    yield
    for agent in agents:
        after = _role_groups(conn, agent)
        if len(after) > MAX_CLAIM_GROUPS and set(after) - before[agent]:
            logger.info("Refused: agent %s would hold %d access groups (at most %d)",
                        agent, len(after), MAX_CLAIM_GROUPS)
            raise AgentGroupsError()


def join_new_role(conn: Any, role_id: Any, *, by: Any = None) -> None:
    """The Knovas Assistent joins a role just created (``RoleStore.create_role``, in its transaction).

    Every role but Administration has it (design § 4); ``create_role`` makes custom roles only. Audited as
    ``agent.role_added`` with ``"with_the_role": true``.
    """
    added = conn.execute(
        "INSERT INTO tenant_role_agents (role_id, agent_id, added_by) "
        "SELECT r.id, a.id, %s FROM tenant_roles r CROSS JOIN agents a "
        "WHERE r.id = %s AND r.kind <> 'administration' AND a.kind = 'assistant' "
        "ON CONFLICT DO NOTHING RETURNING agent_id",
        (_uuid(by), str(role_id))).fetchone()
    if added is not None:
        _audit(conn, "agent.role_added", added[0], _uuid(by), {"role_id": str(role_id), "with_the_role": True})


# -- the store -----------------------------------------------------------------------------------------------------

_AGENT_COLUMNS = (
    "SELECT a.id, a.name::text, a.kind, a.on_behalf_of, a.revoked_at IS NOT NULL, "
    "ARRAY(SELECT m.role_id FROM tenant_role_agents m WHERE m.agent_id = a.id ORDER BY m.role_id) "
    "FROM agents a "
)
#: The assistant first, then by name (case-insensitive: the name is citext).
_AGENT_ORDER = " ORDER BY CASE a.kind WHEN 'assistant' THEN 0 ELSE 1 END, a.name, a.id"


def _agent(row: Any) -> Agent:
    return Agent(id=row[0], name=row[1], kind=row[2], on_behalf_of=row[3], revoked=bool(row[4]),
                 roles=frozenset(row[5] or ()))


class AgentStore:
    """The agents of this installation. Takes a live connection so a caller can compose it into a transaction."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # -- reading -------------------------------------------------------------------------------------------------

    def _select(self, where: str, params: tuple) -> List[Agent]:
        return [_agent(r) for r in self._conn.execute(_AGENT_COLUMNS + where + _AGENT_ORDER, params).fetchall()]

    def assistant(self) -> Agent:
        found = self._select("WHERE a.kind = 'assistant'", ())
        if not found:  # only if the migration did not run: the assistant is seeded with the tables
            raise AgentNotFound()
        return found[0]

    def list_agents(self) -> List[Agent]:
        """Every agent: the assistant first, then by name. Revoked agents too."""
        return self._select("", ())

    def get_agent(self, agent_id: Any) -> Agent:
        ident = _uuid(agent_id)
        found = self._select("WHERE a.id = %s", (ident,)) if ident is not None else []
        if not found:
            raise AgentNotFound()
        return found[0]

    def roles_of_agent(self, agent_id: Any) -> List[Role]:
        """The roles the agent is a member of, in the order of ``RoleStore.list_roles``."""
        held = self.get_agent(agent_id).roles
        return [role for role in RoleStore(self._conn).list_roles() if role.id in held]

    # -- the claim -----------------------------------------------------------------------------------------------

    def claim_for(self, agent_id: Any, *, on_behalf_of: Any = None) -> AgentClaim:
        """The one claim of an agent (design § 4): its roles' groups and functions, narrowed to the person it acts for.

        ``on_behalf_of``: the person the assistant answers (required for it); for an external agent bound to a
        person it may be given and must be that person; an external agent acting on its own acts for nobody else.

        Raises:
            AgentNotFound: no such agent.
            AgentError: the agent is revoked; its person is missing or not active; the assistant without a person;
                another person than the agent's.
            TooManyGroupsError: more than 32 groups. Nothing is cut.
        """
        with _snapshot(self._conn):  # the agent, its roles and the person as of one moment
            return self._claim(agent_id, on_behalf_of)

    def _claim(self, agent_id: Any, on_behalf_of: Any) -> AgentClaim:
        agent = self.get_agent(agent_id)
        if agent.revoked:
            raise AgentError(_REVOKED)
        given = None
        if on_behalf_of is not None and on_behalf_of != "":
            given = _uuid(on_behalf_of)
            if given is None:
                raise AgentError(_PERSON_INACTIVE)
        if agent.kind == ASSISTANT:
            if given is None:
                raise AgentError(_NEEDS_PERSON)
            person = given
        elif agent.on_behalf_of is not None:
            if given is not None and given != agent.on_behalf_of:
                raise AgentError(_OTHER_PERSON)
            person = agent.on_behalf_of
        else:
            if given is not None:
                raise AgentError(_OTHER_PERSON)
            person = None
        groups = _role_groups(self._conn, agent.id)
        functions = _role_functions(self._conn, agent.id)
        if person is not None:
            row = self._conn.execute("SELECT status FROM users WHERE id = %s", (person,)).fetchone()
            if row is None or row[0] != "active":
                raise AgentError(_PERSON_INACTIVE)
            held = set(claim_groups(self._conn, person))
            groups = tuple(g for g in groups if g in held)
            may = _person_functions(self._conn, person)
            functions = tuple(f for f in functions if f in may)
        if len(groups) > MAX_CLAIM_GROUPS:
            logger.warning("Refused to sign a claim for agent %s: %d access groups, at most %d",
                           agent.id, len(groups), MAX_CLAIM_GROUPS)
            raise AgentGroupsError()
        return AgentClaim(subject=str(agent.id), groups=groups, functions=functions, on_behalf_of=person)

    # -- writing -------------------------------------------------------------------------------------------------

    def _lock(self, agent_id: Any) -> Tuple[UUID, str, Optional[UUID], bool]:
        """Hold the agent's row until the transaction ends: id, kind, person, revoked. Else AgentNotFound."""
        ident = _uuid(agent_id)
        row = None if ident is None else self._conn.execute(
            "SELECT id, kind, on_behalf_of, revoked_at IS NOT NULL FROM agents WHERE id = %s FOR NO KEY UPDATE",
            (ident,)).fetchone()
        if row is None:
            raise AgentNotFound()
        return row[0], row[1], row[2], bool(row[3])

    def _lock_role(self, role_id: Any) -> Tuple[UUID, str]:
        """Hold the role's row (RB-1's lock order: roles first); its id and kind. Else RB-1's RoleNotFound."""
        ident = _uuid(role_id)
        row = None if ident is None else self._conn.execute(
            "SELECT id, kind FROM tenant_roles WHERE id = %s FOR UPDATE", (ident,)).fetchone()
        if row is None:
            raise RoleNotFound()
        return row[0], row[1]

    def _active_person(self, value: Any) -> Optional[UUID]:
        """The person an external agent acts for: None for nobody, else an active person's id (AgentError)."""
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        ident = _uuid(value)
        row = None if ident is None else self._conn.execute(
            "SELECT status FROM users WHERE id = %s FOR KEY SHARE", (ident,)).fetchone()
        if row is None or row[0] != "active":
            raise AgentError(_NO_PERSON)
        return ident

    def create_agent(self, *, name: str, on_behalf_of: Any = None, by: Any = None) -> Agent:
        """Register an external agent, without a role. ``on_behalf_of``: an active person, fixed for its life.

        Raises:
            AgentError: the name is not 1 to 80 characters; the person is missing or not active.
            AgentNameTaken: an agent with that name exists (regardless of case; the assistant's name too).
        """
        clean = clean_name(name)
        actor = _uuid(by)
        try:
            with self._conn.transaction():
                person = self._active_person(on_behalf_of)
                if self._conn.execute("SELECT 1 FROM agents WHERE name = %s::citext", (clean,)).fetchone():
                    raise AgentNameTaken()
                ident = self._conn.execute(
                    "INSERT INTO agents (name, kind, on_behalf_of, created_by) VALUES (%s, 'external', %s, %s) "
                    "RETURNING id", (clean, person, actor)).fetchone()[0]
                _audit(self._conn, "agent.created", ident, actor,
                       {"kind": EXTERNAL, "on_behalf_of": str(person) if person else None})
        except psycopg.errors.UniqueViolation:
            # Another writer took the name between the check and the insert. The database's message names it.
            raise AgentNameTaken() from None
        logger.info("Agent %s created (%s)", ident, "for a person" if person else "on its own")
        return self.get_agent(ident)

    def revoke_agent(self, agent_id: Any, *, by: Any = None) -> Agent:
        """Revoke an external agent: from now on it has no claim. Its roles stay, as a record. Again: no change.

        Raises:
            AgentNotFound: no such agent.
            ProtectedAgentError: the Knovas Assistent (it has its switch).
        """
        actor = _uuid(by)
        with self._conn.transaction():
            ident, kind, _, revoked = self._lock(agent_id)
            if kind == ASSISTANT:
                raise ProtectedAgentError()
            if not revoked:
                self._conn.execute("UPDATE agents SET revoked_at = now(), revoked_by = %s WHERE id = %s",
                                   (actor, ident))
                _audit(self._conn, "agent.revoked", ident, actor, {})
        if not revoked:
            logger.info("Agent %s revoked", ident)
        return self.get_agent(ident)

    def add_to_role(self, role_id: Any, agent_id: Any, *, by: Any = None) -> None:
        """Make the agent a member of the role; already a member is no change.

        Raises:
            RoleNotFound: no such role (RB-1's).
            ProtectedRoleError: Administration (no agent joins it).
            AgentNotFound: no such agent. AgentError: a revoked agent.
            TooManyGroupsError: an external agent acting on its own would carry a 33rd group. Nothing written.
        """
        actor = _uuid(by)
        with self._conn.transaction():
            role, kind = self._lock_role(role_id)
            if kind == ADMINISTRATION:
                raise ProtectedRoleError(_NO_ADMINISTRATION)
            ident, agent_kind, person, revoked = self._lock(agent_id)
            if revoked:
                raise AgentError(_REVOKED)
            own = agent_kind == EXTERNAL and person is None
            before = set(_role_groups(self._conn, ident)) if own else set()
            added = self._conn.execute(
                "INSERT INTO tenant_role_agents (role_id, agent_id, added_by) VALUES (%s, %s, %s) "
                "ON CONFLICT (role_id, agent_id) DO NOTHING RETURNING agent_id", (role, ident, actor)).fetchone()
            if added is not None and own:
                after = _role_groups(self._conn, ident)
                if len(after) > MAX_CLAIM_GROUPS and set(after) - before:
                    logger.info("Refused: agent %s would hold %d access groups (at most %d)",
                                ident, len(after), MAX_CLAIM_GROUPS)
                    raise AgentGroupsError()
            if added is not None:
                _audit(self._conn, "agent.role_added", ident, actor, {"role_id": str(role)})
        if added is not None:
            logger.info("Role %s: agent %s added", role, ident)

    def remove_from_role(self, role_id: Any, agent_id: Any, *, by: Any = None) -> None:
        """Take the agent out of the role, the assistant included («darf nicht von KI genutzt werden»).

        Not a member is no change. Raises RoleNotFound and AgentNotFound as above.
        """
        actor = _uuid(by)
        with self._conn.transaction():
            role, _ = self._lock_role(role_id)
            ident, _, _, _ = self._lock(agent_id)
            removed = self._conn.execute(
                "DELETE FROM tenant_role_agents WHERE role_id = %s AND agent_id = %s RETURNING agent_id",
                (role, ident)).fetchone()
            if removed is not None:
                _audit(self._conn, "agent.role_removed", ident, actor, {"role_id": str(role)})
        if removed is not None:
            logger.info("Role %s: agent %s removed", role, ident)


__all__ = (
    "ASSISTANT", "ASSISTANT_NAME", "EXTERNAL", "Agent", "AgentClaim", "AgentError", "AgentGroupsError",
    "AgentNameTaken", "AgentNotFound", "AgentPrincipal", "AgentStore", "ProtectedAgentError", "agent_group_cap",
    "clean_name", "join_new_role",
)
