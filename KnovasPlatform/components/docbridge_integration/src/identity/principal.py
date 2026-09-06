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

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-B2-1)
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from identity.assertion import AssertionSigner

logger = logging.getLogger(__name__)

#: The field name the Secure API reads groups from today. The Platform never
#: sends it and refuses to relay one.
CLIENT_GROUPS_FIELD = "access_groups"


class ClientAssertedGroupsError(ValueError):
    """The caller tried to choose their own access groups."""


class PrincipalBroker:
    """Turns an authenticated session into a signed principal."""

    def __init__(self, *, user_repo: Any, signer: AssertionSigner, tenant_id: str) -> None:
        self._users = user_repo
        self._signer = signer
        self._tenant_id = tenant_id

    def groups_for(self, user: Any) -> tuple[str, ...]:
        """This user's granted access groups, straight from the database."""
        return self._users.access_groups_of(user.id)

    def assertion_for(self, user: Any) -> str:
        """Sign the principal for one outbound request.

        The subject is the local user id — opaque, and deliberately not an
        address or a name. Knovas has no business learning who works here.
        """
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
