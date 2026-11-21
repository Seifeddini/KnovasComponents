"""The Platform as trusted identity broker (KC-B2-1).

Pflichtenheft B2: *"The RBAC groups a request asserts must come from an
authenticated identity, not from whatever the client claims."*

Read literally, that is two obligations, and this module is the first:

    Groups are resolved **server-side**, from ``user_access_groups``, for the
    person whose session this request carries. They are never read from the
    request the browser sent.

    A browser that supplies ``access_groups`` is **rejected**, not ignored.
    Dropping the field silently would let a caller believe a scope applied when
    it never did — and if a future bug ever merged it, the failure would be
    invisible. Failing loudly here means a caller finds out at once.

The second obligation belongs to KnowledgeBase: it must be able to tell a
brokered assertion from a hand-written one. Without that half, a `curl` holding
the tenant certificate still reads everything, and B2 stays PARTIAL no matter
how careful this file is.

There is no caching. The grant table is read for every assertion, so an
administrator's change lands on the user's next request rather than whenever a
cache happened to expire.

A claim carries at most 32 groups (the Core's QUERY_MAX_ASSERTED_GROUPS): the
person's running direct grants plus the Core groups of their roles. For more,
the broker raises ``TooManyGroupsError`` and signs nothing. It never cuts the
list, because a cut claim would silently drop groups the person was given.

An agent acting (RB-6) is signed the same way, from the one claim every agent
gets (``identity.agents.AgentStore.claim_for``): ``sub`` is the agent's id,
``grp`` and ``rol`` the groups and functions of its roles, narrowed to the
person it acts for. ``assertion_for`` signs it for an ``AgentPrincipal`` (the
assistant answering a person); ``agent_assertions`` signs the pool of an
external agent the Platform hands the Core (RB-7, ``identity.agent_delivery``).

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-B2-1);
the cap: FEAT-2026-11-19-platform-mvp, RB-2 (docs/platform-mvp/work-orders/RB-1-2.md);
agents: RB-6, RB-7 (docs/platform-mvp/work-orders/RB-6-7.md)
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from identity.assertion import DEFAULT_LIFETIME_SECONDS, AssertionSigner
from identity.roles import MAX_CLAIM_GROUPS, TooManyGroupsError

logger = logging.getLogger(__name__)

#: The field name the Secure API reads groups from today. The Platform never
#: sends it and refuses to relay one.
CLIENT_GROUPS_FIELD = "access_groups"


class ClientAssertedGroupsError(ValueError):
    """The caller tried to choose their own access groups."""


class PrincipalBroker:
    """Turns an authenticated session into a signed principal."""

    def __init__(self, *, user_repo: Any, signer: AssertionSigner, tenant_id: str, agents: Any = None) -> None:
        self._users = user_repo
        self._signer = signer
        self._tenant_id = tenant_id
        # identity.agents.AgentStore; by default on the people's connection, built on first use (RB-6).
        self._agents = agents

    def _agent_store(self) -> Any:
        if self._agents is None:
            from identity.agents import AgentStore

            self._agents = AgentStore(self._users._conn)
        return self._agents

    def groups_for(self, user: Any) -> tuple[str, ...]:
        """This user's claim groups, straight from the database: direct grants and the groups of their roles.

        Raises:
            TooManyGroupsError: more than 32. Nothing is signed, and the list is never cut.
        """
        groups = self._users.access_groups_of(user.id)
        if len(groups) > MAX_CLAIM_GROUPS:
            logger.warning("Refused to sign a claim for user %s: %d access groups, at most %d",
                           user.id, len(groups), MAX_CLAIM_GROUPS)
            raise TooManyGroupsError()
        return groups

    def agent_assertions(self, agent_id: Any, *, on_behalf_of: Any = None, count: int = 1,
                         lifetime_seconds: int = DEFAULT_LIFETIME_SECONDS) -> list[str]:
        """Sign ``count`` principals of an agent, each with its own ``jti`` (RB-6, RB-7).

        One claim, read now (``AgentStore.claim_for``): the subject is the
        agent's id, the groups and functions those of its roles, narrowed to
        the person it acts for. Nothing is signed for a refused claim.

        Raises:
            AgentError: no such agent, revoked, its person missing or not
                active, the assistant without a person.
            TooManyGroupsError: more than 32 groups.
            ValueError: a lifetime over 300 seconds (the signer's cap).
        """
        claim = self._agent_store().claim_for(agent_id, on_behalf_of=on_behalf_of)
        return [
            self._signer.mint(subject=claim.subject, tenant=self._tenant_id, groups=claim.groups,
                              roles=claim.functions, lifetime_seconds=lifetime_seconds)
            for _ in range(int(count))
        ]

    def assertion_for(self, user: Any) -> str:
        """Sign the principal for one outbound request.

        The subject is the local user id — opaque, and deliberately not an
        address or a name. Knovas has no business learning who works here.
        For an ``AgentPrincipal`` (the assistant answering a person) it is the
        agent's claim instead (``agent_assertions``).
        """
        from identity.agents import AgentPrincipal

        if isinstance(user, AgentPrincipal):
            return self.agent_assertions(user.agent_id, on_behalf_of=user.on_behalf_of)[0]
        return self._signer.mint(
            subject=str(user.id),
            tenant=self._tenant_id,
            groups=self.groups_for(user),
            roles=sorted(getattr(user, "roles", ()) or ()),
        )

    def dual_control_token(
        self, *, action: str, target: str, requester: Any, approver: Any
    ) -> str:
        """Sign a two-person decision for the backend to enforce.

        Raises:
            ValueError: requester and approver are the same person.
        """
        return self._signer.mint_dual_control(
            tenant=self._tenant_id,
            action=action,
            target=target,
            requester=str(requester.id),
            approver=str(approver.id),
        )

    @staticmethod
    def reject_client_assertion(body: Mapping[str, Any] | None) -> None:
        """Refuse a request that tried to name its own access groups.

        Raises:
            ClientAssertedGroupsError: the field is present, with any value.
        """
        if body and CLIENT_GROUPS_FIELD in body:
            logger.warning(
                "Rejected a request carrying a client-supplied %s", CLIENT_GROUPS_FIELD
            )
            raise ClientAssertedGroupsError(
                "Access groups are determined by your account, not by the request. "
                "Remove the access_groups field."
            )
