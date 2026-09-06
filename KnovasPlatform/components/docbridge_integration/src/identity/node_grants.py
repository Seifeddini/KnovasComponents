"""Who may edit a knowledge-graph node.

This is write control only. Read visibility belongs to the backend ACL
(GI-GRAPH-12) and this module never narrows a listing — two systems answering
"may I see this?" is the failure mode the design exists to avoid.

Honest about its limit: these grants are enforced by the Platform's own routes.
Anything holding the tenant certificate and calling /secured/graph directly
bypasses them. That is not new here — principal_resolver.py states the same
boundary for RBAC itself — but it must be described to buyers as a control over
who may edit through the product, not a cryptographic guarantee.

Plan: docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md (C1)
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

OWNER = "owner"
EDITOR = "editor"


class OwnerRevokeError(Exception):
    """The owner's grant cannot be revoked, only transferred by an admin."""


class NodeGrantStore:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def set_owner(self, node_id: str, user_id: UUID | str) -> None:
        """CreateMechanism: record the creator. Idempotent; a second owner on
        the same node hits idx_node_grants_one_owner and raises, which is the
        right answer — transfer is an admin action, not a race."""
        self._conn.execute(
            "INSERT INTO node_grants (node_id, user_id, role, granted_by) "
            "VALUES (%s, %s, 'owner', %s) "
            "ON CONFLICT (node_id, user_id) DO UPDATE SET role = 'owner'",
            (str(node_id), str(user_id), str(user_id)),
        )

    def grant_editor(self, node_id: str, user_id: UUID | str,
                     granted_by: UUID | str | None = None) -> None:
        """Add an editor. A no-op for the owner, who already outranks it."""
        self._conn.execute(
            "INSERT INTO node_grants (node_id, user_id, role, granted_by) "
            "VALUES (%s, %s, 'editor', %s) "
            "ON CONFLICT (node_id, user_id) DO NOTHING",
            (str(node_id), str(user_id),
             str(granted_by) if granted_by is not None else None),
        )

    def revoke(self, node_id: str, user_id: UUID | str) -> None:
        """RevokeMechanism: DELETE ... AND role = 'editor' — the owner row is
        never touched, so a node cannot end up with nobody who may grant."""
        removed = self._conn.execute(
            "DELETE FROM node_grants WHERE node_id = %s AND user_id = %s "
            "AND role = 'editor' RETURNING user_id",
            (str(node_id), str(user_id)),
        ).fetchall()
        if not removed:
            # Either they were never an editor, or they are the owner. Only the
            # second is an error worth a message: silently succeeding would let
            # an admin believe they removed access they did not.
            if self.for_node(node_id)["owner"] == str(user_id):
                raise OwnerRevokeError(
                    "Die Eigentuemerschaft kann nur uebertragen, nicht entzogen werden.")

    def for_node(self, node_id: str) -> dict:
        # psycopg 3 tuple rows, indexed by position like identity/users.py.
        rows = self._conn.execute(
            "SELECT user_id, role FROM node_grants WHERE node_id = %s "
            "ORDER BY role, granted_at",
            (str(node_id),),
        ).fetchall()
        owner = next((str(user_id) for user_id, role in rows if role == OWNER), None)
        editors = [str(user_id) for user_id, role in rows if role == EDITOR]
        return {"owner": owner, "editors": editors}

    def may_write(self, node_id: str, user: Any) -> bool:
        """mayWrite: the owner, an editor, or any admin.

        An admin passes even when a node has no grants at all: nodes created
        before this feature have no owner, and somebody has to be able to
        repair them (an_admin_always_writes).
        """
        if user is None:
            return False
        if "admin" in getattr(user, "roles", frozenset()):
            return True
        grants = self.for_node(node_id)
        return str(user.id) == grants["owner"] or str(user.id) in grants["editors"]

    def nodes_for_user(self, user_id: UUID | str) -> list[str]:
        """Node ids this user owns or edits. For an account-deletion review."""
        rows = self._conn.execute(
            "SELECT node_id FROM node_grants WHERE user_id = %s", (str(user_id),)
        ).fetchall()
        return [str(node_id) for (node_id,) in rows]
