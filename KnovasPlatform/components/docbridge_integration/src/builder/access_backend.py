"""The access window's line to the Brain (build plan step B14): groups, folder rules, documents, readability.

Over the Platform's own client (knovas_client.KnovasClient) on its certificate, so the tenant is the Platform's
and never one a request names. Every call carries the signed-in person's principal assertion; the Brain checks
that they dominate what they assign. ``readable_as`` asks the Brain whether *another* person may read a
document -- the Platform asserts that person's groups for this one read -- so "explain access" gives the
Brain's answer, not a second copy of its policy.

A move of a group is PATCH /secured/access_groups/<id> with ``parent``. A Brain that does not move groups yet
ignores the field; the backend reads the tree back and says so instead of reporting a move that did not happen.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from builder.access import AccessError, Tree

logger = logging.getLogger(__name__)

#: How many documents of a group the window counts before it shows "n+".
COUNT_CAP = 500


class BrainAccess:
    def __init__(self, client: Any):
        self.client = client

    def tree(self) -> Tree:
        return Tree.of(self.client.access_groups())

    def create(self, name: str, parent: Optional[str], acting_as: Iterable[str]) -> str:
        made = self.client.create_access_group(name, parent)
        if not made or not made.get("group_id"):
            raise AccessError("Das Brain hat die Gruppe nicht angelegt.", 502)
        return str(made["group_id"])

    def rename(self, gid: str, name: str) -> None:
        if self.client.rename_access_group(gid, name) is None:
            raise AccessError("Das Brain hat die Gruppe nicht umbenannt.", 502)

    def move(self, gid: str, parent: str) -> None:
        self.client.move_access_group(gid, parent)
        if self.tree().parent(gid) != parent:
            raise AccessError("Das Brain hat die Gruppe nicht verschoben: diese Brain-Version kann Gruppen noch nicht "
                              "umhängen (PATCH /secured/access_groups mit parent).", 501, "move_unsupported")

    def delete(self, gid: str) -> None:
        if not self.client.delete_access_group(gid):
            raise AccessError("Das Brain hat die Gruppe nicht gelöscht (noch zugewiesen?).", 409, "group_in_use")

    def rules(self) -> list[dict]:
        return list(self.client.folder_rules())

    def create_rule(self, prefix: str, groups: list[str], acting_as: Iterable[str]) -> dict:
        made = self.client.create_folder_rule(prefix, groups, acting_as=list(acting_as))
        if not made:
            raise AccessError("Das Brain hat die Ordnerregel nicht angelegt (dominieren Sie die Gruppe?).", 403)
        return made

    def delete_rule(self, rule_id: str) -> None:
        if not self.client.delete_folder_rule(rule_id):
            raise AccessError("Diese Ordnerregel gibt es nicht mehr.", 404)

    def documents(self, gid: str) -> tuple[int, bool]:
        """How many documents are assigned to the group (counted up to COUNT_CAP), and whether there are more."""
        page = self.client.documents(group=gid, limit=COUNT_CAP)
        return len(page.get("documents") or []), bool(page.get("next_after"))

    def node_types(self) -> list[dict]:
        return list(self.client.graph_node_types())

    def document_access(self, pointer: str) -> Optional[dict]:
        return self.client.document_access(pointer)

    def readable_as(self, user: Any, pointer: str) -> Optional[bool]:
        try:
            with self.client.asserting_for(user):
                return bool(self.client.document_readable(pointer))
        except Exception as exc:  # noqa: BLE001 - unknown, not "no"
            logger.warning("access window: readability not asked (%s)", type(exc).__name__)
            return None

    def posture(self) -> Optional[dict]:
        """The tenant's enforcement mode and ACL backfill; the Brain shows them only Knovas-internally today."""
        getter = getattr(self.client, "rbac_posture", None)
        return getter() if getter else None
