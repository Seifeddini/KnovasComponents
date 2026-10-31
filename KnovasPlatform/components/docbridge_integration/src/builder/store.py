"""Workspaces, their versions and changes in PostgreSQL (migration 0005).

Functions take the connection at construction; Platform connections run in
autocommit mode, so every write of more than one statement opens its own
transaction. What the methods return is JSON-ready: ids are strings,
timestamps ISO 8601 with an offset.

One draft per workspace is the database's rule (a partial unique index), not
only this module's: two people importing at once cannot leave two drafts.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from builder.package import content_hash, summarise


@dataclass(frozen=True)
class Workspace:
    id: str
    name: str
    tenant: Optional[str]
    created_at: str
    created_by: Optional[str]
    draft_number: Optional[int]
    published_number: Optional[int]
    summary: dict
    last_change: Optional[dict]


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _uuid(value) -> Optional[str]:
    return str(value) if value is not None else None


class WorkspaceStore:
    """The builder's record. Built per request on the request's connection."""

    def __init__(self, conn):
        self.conn = conn

    # -------------------------------------------------------------- reads

    _SELECT = """
        SELECT w.id, w.name, w.tenant, w.created_at, w.created_by,
               d.number, p.number, COALESCE(d.model, p.model),
               c.path, c.at, c.actor, u.display_name
          FROM builder_workspace w
          LEFT JOIN builder_version d ON d.workspace = w.id AND d.state = 'draft'
          LEFT JOIN LATERAL (
                SELECT number, model FROM builder_version
                 WHERE workspace = w.id AND state = 'published'
                 ORDER BY number DESC LIMIT 1) p ON TRUE
          LEFT JOIN LATERAL (
                SELECT path, at, actor FROM builder_change
                 WHERE workspace = w.id ORDER BY at DESC, id DESC LIMIT 1) c ON TRUE
          LEFT JOIN users u ON u.id = c.actor
    """

    def _workspace(self, row) -> Workspace:
        last = None
        if row[8] is not None:
            last = {"path": row[8], "at": _iso(row[9]), "actor": _uuid(row[10]), "actor_name": row[11]}
        return Workspace(
            id=str(row[0]), name=row[1], tenant=row[2], created_at=_iso(row[3]),
            created_by=_uuid(row[4]), draft_number=row[5], published_number=row[6],
            summary=summarise(row[7]) if row[7] is not None else summarise({}),
            last_change=last,
        )

    def list(self) -> list[Workspace]:
        rows = self.conn.execute(self._SELECT + " ORDER BY lower(w.name), w.created_at").fetchall()
        return [self._workspace(row) for row in rows]

    def get(self, workspace_id: str) -> Optional[Workspace]:
        try:
            uuid.UUID(str(workspace_id))
        except ValueError:
            return None
        row = self.conn.execute(self._SELECT + " WHERE w.id = %s", (workspace_id,)).fetchone()
        return self._workspace(row) if row else None

    def model(self, workspace_id: str) -> Optional[dict]:
        """The draft's model, or the newest published one when there is no draft."""
        row = self.conn.execute(
            """SELECT model FROM builder_version WHERE workspace = %s
                ORDER BY (state = 'draft') DESC, number DESC LIMIT 1""",
            (workspace_id,),
        ).fetchone()
        return row[0] if row else None

    def changes(self, workspace_id: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            """SELECT c.path, c.at, c.actor, u.display_name, c.after
                 FROM builder_change c LEFT JOIN users u ON u.id = c.actor
                WHERE c.workspace = %s ORDER BY c.at DESC, c.id DESC LIMIT %s""",
            (workspace_id, limit),
        ).fetchall()
        return [{"path": r[0], "at": _iso(r[1]), "actor": _uuid(r[2]), "actor_name": r[3], "after": r[4]}
                for r in rows]

    # -------------------------------------------------------------- writes

    def create_from_import(self, *, name: str, model: dict, actor_id, source: dict) -> str:
        """A new workspace whose first draft is an imported package's model."""
        return self.create(name=name, model=model, actor_id=actor_id, source=source, change="import")

    def create(self, *, name: str, model: dict, actor_id, source: dict, change: str = "create") -> str:
        """A new workspace whose first draft is ``model``. Returns its id.

        The change row (path ``change``: ``create`` or ``import``) records
        where the model came from and its content hash, not the model a
        second time.
        """
        digest = content_hash(model)
        with self.conn.transaction():
            workspace_id = self.conn.execute(
                "INSERT INTO builder_workspace (name, created_by) VALUES (%s, %s) RETURNING id",
                (name.strip(), actor_id),
            ).fetchone()[0]
            version_id = self.conn.execute(
                """INSERT INTO builder_version (workspace, number, state, model, content_hash)
                   VALUES (%s, 1, 'draft', %s::jsonb, %s) RETURNING id""",
                (workspace_id, json.dumps(model), digest),
            ).fetchone()[0]
            self.conn.execute(
                """INSERT INTO builder_change (workspace, version, actor, path, before, after)
                   VALUES (%s, %s, %s, %s, NULL, %s::jsonb)""",
                (workspace_id, version_id, actor_id, change,
                 json.dumps({**source, "content_hash": digest})),
            )
        return str(workspace_id)
