"""Workspaces, their versions and changes in PostgreSQL (migration 0005).

Functions take the connection at construction; Platform connections run in
autocommit mode, so every write of more than one statement opens its own
transaction. What the methods return is JSON-ready: ids are strings,
timestamps ISO 8601 with an offset.

One draft per workspace is the database's rule (a partial unique index), not
only this module's: two people importing at once cannot leave two drafts.

Edits (step B3) lock the draft row, change the model and write one change
row per element they touched, all in one transaction; the rows of one edit
share a batch id, which undo and redo take back as a whole.
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

    def create(self, *, name: str, model: dict, actor_id, source: dict, change: str = "create",
               tenant: Optional[str] = None) -> str:
        """A new workspace whose first draft is ``model``. Returns its id.

        The change row (path ``change``: ``create`` or ``import``) records
        where the model came from and its content hash, not the model a
        second time. ``tenant`` comes from the Platform's own certificate,
        never from a request.
        """
        digest = content_hash(model)
        with self.conn.transaction():
            workspace_id = self.conn.execute(
                "INSERT INTO builder_workspace (name, created_by, tenant) VALUES (%s, %s, %s) RETURNING id",
                (name.strip(), actor_id, tenant),
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

    def edit(self, workspace_id: str, actor_id, mutate) -> tuple[dict, list[dict], Optional[str]]:
        """Change the draft with ``mutate(model) -> [{path, before, after}]``.

        Returns the model, the changes and their batch id. All or nothing: an
        exception from ``mutate`` leaves the draft and the change log as they
        were. A workspace with only published versions gets a new draft first.
        """
        with self.conn.transaction():
            row = self.conn.execute(
                """SELECT id, model FROM builder_version
                    WHERE workspace = %s AND state = 'draft' FOR UPDATE""",
                (workspace_id,),
            ).fetchone()
            if row is None:
                row = self.conn.execute(
                    """INSERT INTO builder_version (workspace, number, state, model, content_hash)
                       SELECT workspace, number + 1, 'draft', model, content_hash FROM builder_version
                        WHERE workspace = %s ORDER BY number DESC LIMIT 1
                       RETURNING id, model""",
                    (workspace_id,),
                ).fetchone()
                if row is None:
                    raise LookupError(workspace_id)
            version_id, model = row
            changes = mutate(model)
            if not changes:
                return model, [], None
            batch = uuid.uuid4()
            self.conn.execute(
                "UPDATE builder_version SET model = %s::jsonb, content_hash = %s, updated_at = now() WHERE id = %s",
                (json.dumps(model), content_hash(model), version_id),
            )
            for change in changes:
                self.conn.execute(
                    """INSERT INTO builder_change (workspace, version, actor, path, before, after, batch)
                       VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)""",
                    (workspace_id, version_id, actor_id, change["path"],
                     json.dumps(change["before"]), json.dumps(change["after"]), batch),
                )
        return model, changes, str(batch)

    def batch(self, workspace_id: str, batch_id: str) -> list[dict]:
        """The changes of one edit, in the order they were made."""
        try:
            uuid.UUID(str(batch_id))
        except ValueError:
            return []
        rows = self.conn.execute(
            """SELECT path, before, after FROM builder_change
                WHERE workspace = %s AND batch = %s ORDER BY id""",
            (workspace_id, batch_id),
        ).fetchall()
        return [{"path": r[0], "before": r[1], "after": r[2]} for r in rows]

    # -------------------------------------------------------------- review and publish (B5)

    def draft(self, workspace_id: str) -> Optional[dict]:
        """The draft's id, number and content hash, or None when there is none."""
        row = self.conn.execute(
            "SELECT id, number, content_hash FROM builder_version WHERE workspace = %s AND state = 'draft'",
            (workspace_id,),
        ).fetchone()
        return {"id": str(row[0]), "number": row[1], "content_hash": row[2]} if row else None

    def draft_changes(self, workspace_id: str) -> list[dict]:
        """Every change of the current draft, oldest first, with who made it."""
        rows = self.conn.execute(
            """SELECT c.path, c.before, c.after, c.actor, u.display_name, c.at
                 FROM builder_change c
                 JOIN builder_version v ON v.id = c.version AND v.state = 'draft'
                 LEFT JOIN users u ON u.id = c.actor
                WHERE c.workspace = %s ORDER BY c.id""",
            (workspace_id,),
        ).fetchall()
        return [{"path": r[0], "before": r[1], "after": r[2], "actor": _uuid(r[3]), "actor_name": r[4],
                 "at": _iso(r[5])} for r in rows]

    def approve(self, workspace_id: str, reviewer_id, content_hash: str) -> bool:
        """Approve the draft as it is (``content_hash``); False when it changed since."""
        with self.conn.transaction():
            row = self.conn.execute(
                """SELECT id, content_hash FROM builder_version
                    WHERE workspace = %s AND state = 'draft' FOR UPDATE""",
                (workspace_id,),
            ).fetchone()
            if row is None or row[1] != content_hash:
                return False
            self.conn.execute(
                "INSERT INTO builder_approval (workspace, version, content_hash, reviewer) VALUES (%s, %s, %s, %s)",
                (workspace_id, row[0], content_hash, reviewer_id),
            )
        return True

    def approvals(self, workspace_id: str) -> list[dict]:
        """The approvals of the current draft, each with the hash it approved."""
        rows = self.conn.execute(
            """SELECT a.reviewer, u.display_name, a.at, a.content_hash
                 FROM builder_approval a
                 JOIN builder_version v ON v.id = a.version AND v.state = 'draft'
                 LEFT JOIN users u ON u.id = a.reviewer
                WHERE a.workspace = %s ORDER BY a.at""",
            (workspace_id,),
        ).fetchall()
        return [{"reviewer": _uuid(r[0]), "reviewer_name": r[1], "at": _iso(r[2]), "content_hash": r[3]}
                for r in rows]

    def record_run(self, workspace_id: str, *, kind: str, actor_id, tenant: Optional[str], content_hash: str,
                   bundle_hash: Optional[str], status: str, summary: dict) -> str:
        draft = self.draft(workspace_id)
        row = self.conn.execute(
            """INSERT INTO builder_run (workspace, version, kind, actor, tenant, content_hash, bundle_hash, status, summary)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) RETURNING id""",
            (workspace_id, draft["id"] if draft else None, kind, actor_id, tenant, content_hash, bundle_hash, status,
             json.dumps(summary)),
        ).fetchone()
        return str(row[0])

    def runs(self, workspace_id: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            """SELECT r.id, r.kind, r.actor, u.display_name, r.tenant, r.content_hash, r.bundle_hash, r.status,
                      r.summary, r.at
                 FROM builder_run r LEFT JOIN users u ON u.id = r.actor
                WHERE r.workspace = %s ORDER BY r.at DESC LIMIT %s""",
            (workspace_id, limit),
        ).fetchall()
        return [{"id": str(r[0]), "kind": r[1], "actor": _uuid(r[2]), "actor_name": r[3], "tenant": r[4],
                 "content_hash": r[5], "bundle_hash": r[6], "status": r[7], "summary": r[8], "at": _iso(r[9])}
                for r in rows]

    def publish(self, workspace_id: str, publisher_id, content_hash: str, bundle: dict, bundle_hash: str) -> Optional[int]:
        """Turn the draft into the published version with its bundle; its number, or None when it changed."""
        with self.conn.transaction():
            row = self.conn.execute(
                """SELECT id, number, content_hash FROM builder_version
                    WHERE workspace = %s AND state = 'draft' FOR UPDATE""",
                (workspace_id,),
            ).fetchone()
            if row is None or row[2] != content_hash:
                return None
            self.conn.execute(
                """UPDATE builder_version SET state = 'published', published_at = now(), published_by = %s,
                          bundle = %s::jsonb, bundle_hash = %s, updated_at = now()
                    WHERE id = %s""",
                (publisher_id, json.dumps(bundle), bundle_hash, row[0]),
            )
        return row[1]

    def published_bundle(self, workspace_id: str) -> Optional[dict]:
        """The bundle of the newest published version: what the Platform's views read."""
        row = self.conn.execute(
            """SELECT number, bundle, bundle_hash, published_at FROM builder_version
                WHERE workspace = %s AND state = 'published' ORDER BY number DESC LIMIT 1""",
            (workspace_id,),
        ).fetchone()
        if row is None or row[1] is None:
            return None
        return {"number": row[0], "bundle": row[1], "bundle_hash": row[2], "published_at": _iso(row[3])}

    def link_tenant(self, workspace_id: str, tenant: str) -> None:
        self.conn.execute("UPDATE builder_workspace SET tenant = %s WHERE id = %s", (tenant, workspace_id))
