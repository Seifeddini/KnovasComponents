"""Workspaces, their versions and changes in PostgreSQL (migrations 0011-0014).

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


def _approval(r) -> dict:
    return {"reviewer": _uuid(r[0]), "reviewer_name": r[1], "at": _iso(r[2]), "content_hash": r[3],
            "own_changes": bool(r[4]), "reason": r[5], "review_hash": (r[6] or None), "own_import": bool(r[7])}


def _uuid(value) -> Optional[str]:
    return str(value) if value is not None else None


def served_source(workspace, source: dict, published: Optional[dict]) -> dict:
    """One source as a component's configuration names it (served_by). Its switches come from the published version
    only: a source published before them neither fetches nor writes (MVP-25). served_by passes the published source
    as both: a component never sees a draft (BB-07, B2)."""
    from builder.sources import fetch_of, write_of

    return {"workspace": workspace.id, "workspace_name": workspace.name, "key": source.get("key"),
            "name": source.get("name"), "kind": source.get("kind"), "access": source.get("access") or "read",
            "settings": source.get("settings") or {},
            "fetch": fetch_of(published or {}), "write": write_of(published or {})}


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
        rows = self.conn.execute(self._SELECT + " WHERE w.deleted_at IS NULL ORDER BY lower(w.name), w.created_at").fetchall()
        return [self._workspace(row) for row in rows]

    def get(self, workspace_id: str) -> Optional[Workspace]:
        try:
            uuid.UUID(str(workspace_id))
        except ValueError:
            return None
        row = self.conn.execute(self._SELECT + " WHERE w.id = %s AND w.deleted_at IS NULL", (workspace_id,)).fetchone()
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

    def delete(self, workspace_id: str, actor_id) -> bool:
        """Take the workspace out of every list and page; its history stays (migration 0019)."""
        row = self.conn.execute(
            """UPDATE builder_workspace SET deleted_at = now(), deleted_by = %s
                WHERE id = %s AND deleted_at IS NULL RETURNING id""",
            (actor_id, workspace_id),
        ).fetchone()
        return row is not None

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

    def approve(self, workspace_id: str, reviewer_id, content_hash: str, *, own_changes: bool = False,
                reason: Optional[str] = None, review_hash: Optional[str] = None, own_import: bool = False) -> bool:
        """Approve the draft as it is (``content_hash``); False when it changed since. ``own_changes``: an
        administrator approving a draft they changed, with ``reason`` (migration 0020). ``review_hash``: the digest of
        everything the dry run showed a publish would change (builder.review.review_hash); ``own_import``: the
        approver is the person whose import or creation the draft started from (migration 0023, BB-07 B1)."""
        with self.conn.transaction():
            row = self.conn.execute(
                """SELECT id, content_hash FROM builder_version
                    WHERE workspace = %s AND state = 'draft' FOR UPDATE""",
                (workspace_id,),
            ).fetchone()
            if row is None or row[1] != content_hash:
                return False
            self.conn.execute(
                """INSERT INTO builder_approval (workspace, version, content_hash, reviewer, own_changes, reason,
                                                review_hash, own_import)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (workspace_id, row[0], content_hash, reviewer_id, own_changes, reason, review_hash, own_import),
            )
        return True

    def approvals(self, workspace_id: str) -> list[dict]:
        """The approvals of the current draft, each with the hash it approved."""
        rows = self.conn.execute(
            """SELECT a.reviewer, u.display_name, a.at, a.content_hash, a.own_changes, a.reason, a.review_hash,
                      a.own_import
                 FROM builder_approval a
                 JOIN builder_version v ON v.id = a.version AND v.state = 'draft'
                 LEFT JOIN users u ON u.id = a.reviewer
                WHERE a.workspace = %s ORDER BY a.at""",
            (workspace_id,),
        ).fetchall()
        return [_approval(r) for r in rows]

    def approval_history(self, workspace_id: str) -> list[dict]:
        """Every approval of every version, oldest first (the activity feed)."""
        rows = self.conn.execute(
            """SELECT a.reviewer, u.display_name, a.at, a.content_hash, a.own_changes, a.reason, a.review_hash,
                      a.own_import
                 FROM builder_approval a LEFT JOIN users u ON u.id = a.reviewer
                WHERE a.workspace = %s ORDER BY a.at""",
            (workspace_id,),
        ).fetchall()
        return [_approval(r) for r in rows]

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

    # -------------------------------------------------------- health reviews (B10)

    def record_review(self, workspace_id: str, node_type: str, actor_id) -> None:
        self.conn.execute("INSERT INTO builder_review (workspace, node_type, reviewed_by) VALUES (%s, %s, %s)",
                          (workspace_id, node_type, actor_id))

    def reviews(self, workspace_id: str) -> dict:
        """The latest review of each type: {type: {at, actor_name}}."""
        rows = self.conn.execute(
            """SELECT DISTINCT ON (r.node_type) r.node_type, r.at, u.display_name
                 FROM builder_review r LEFT JOIN users u ON u.id = r.reviewed_by
                WHERE r.workspace = %s ORDER BY r.node_type, r.at DESC""",
            (workspace_id,),
        ).fetchall()
        return {r[0]: {"at": _iso(r[1]), "actor_name": r[2]} for r in rows}

    # -------------------------------------------------------- sync components (B7)

    _COMPONENT = """SELECT c.id, c.kind, c.name, c.site, c.owner, c.settings, c.state, c.registered_at, u.display_name,
                           c.transport, c.paused, c.schedule, c.version, c.capabilities, c.enrol_expires_at,
                           c.enrolled_at, c.certificate_fingerprint, c.last_heartbeat_at, c.reported_state,
                           c.lag_seconds, c.open_errors, c.revoked_at, c.source_checks,
                           c.site_id, c.parent_id, c.local_name, c.telemetry
                      FROM builder_component c LEFT JOIN users u ON u.id = c.registered_by"""
    #: The states a source may be read through (B7) or a component may run in.
    _USABLE = "('registered', 'enrolled')"

    @staticmethod
    def _component(row) -> dict:
        return {"id": str(row[0]), "kind": row[1], "name": row[2], "site": row[3], "owner": row[4],
                "settings": row[5] or {}, "state": row[6], "registered_at": _iso(row[7]), "registered_by_name": row[8],
                "transport": row[9], "paused": bool(row[10]), "schedule": row[11] or {"mode": "manual"},
                "version": row[12] or "", "capabilities": list(row[13] or []), "enrol_expires_at": _iso(row[14]),
                "enrolled_at": _iso(row[15]), "certificate_fingerprint": row[16], "last_heartbeat_at": _iso(row[17]),
                "reported_state": row[18], "lag_seconds": row[19], "open_errors": row[20] or 0,
                "revoked_at": _iso(row[21]), "source_checks": row[22] or {},
                # Sites (migration 0022): the site a component runs at, and for one behind Knovas Link its parent and
                # its name on the site; Link's own row keeps what its heartbeat reports of the site.
                "site_id": _uuid(row[23]), "parent_id": _uuid(row[24]), "local_name": row[25], "telemetry": row[26] or {}}

    def components(self, tenant: str, *, every: bool = False) -> list[dict]:
        """The tenant's sync components a source can use, by name; ``every``: pending and revoked ones too."""
        states = "('registered', 'enrolled', 'pending', 'revoked')" if every else self._USABLE
        rows = self.conn.execute(self._COMPONENT + f" WHERE c.tenant = %s AND c.state IN {states} ORDER BY c.name",
                                 (tenant,)).fetchall()
        return [self._component(r) for r in rows]

    def component(self, tenant: str, component_id: str, *, any_state: bool = False) -> Optional[dict]:
        try:
            uuid.UUID(str(component_id))
        except ValueError:
            return None
        states = "('registered', 'enrolled', 'pending', 'revoked')" if any_state else self._USABLE
        row = self.conn.execute(self._COMPONENT + f" WHERE c.tenant = %s AND c.id = %s AND c.state IN {states}",
                                (tenant, component_id)).fetchone()
        return self._component(row) if row else None

    # -------------------------------------------------------- enrolment and monitoring (B13)

    def create_component(self, tenant: str, *, kind: str, name: str, site: str, owner: str, actor_id,
                         enrol_expires_at: str, site_id: Optional[str] = None) -> Optional[str]:
        """A component that speaks protocol v1, waiting for its enrolment; None when the name is taken. ``site_id``:
        Knovas Link, made with its site (migration 0022)."""
        row = self.conn.execute(
            """INSERT INTO builder_component (tenant, kind, name, site, owner, registered_by, state, transport,
                                              enrol_expires_at, site_id)
               VALUES (%s, %s, %s, %s, %s, %s, 'pending', 'v1', %s, %s)
               ON CONFLICT (tenant, name) DO NOTHING RETURNING id""",
            (tenant, kind, name, site, owner, actor_id, enrol_expires_at, site_id),
        ).fetchone()
        return str(row[0]) if row else None

    _UPDATABLE = {"state", "paused", "schedule", "version", "capabilities", "enrol_expires_at", "enrolled_at",
                  "certificate_fingerprint", "last_heartbeat_at", "reported_state", "lag_seconds", "open_errors",
                  "revoked_at", "name", "site", "owner", "settings", "source_checks", "telemetry"}

    def update_component(self, component_id: str, **fields) -> None:
        unknown = set(fields) - self._UPDATABLE
        if unknown:
            raise ValueError(f"not a component field: {sorted(unknown)}")
        if not fields:
            return
        names = sorted(fields)
        documents = ("schedule", "capabilities", "settings", "source_checks", "telemetry")
        values = [json.dumps(fields[n]) if n in documents else fields[n] for n in names]
        sets = ", ".join(f"{n} = %s::jsonb" if n in documents else f"{n} = %s" for n in names)
        self.conn.execute(f"UPDATE builder_component SET {sets} WHERE id = %s", (*values, component_id))

    def delete_component(self, tenant: str, component_id: str) -> bool:
        """The component, with its runs, events, held plans and commands (their foreign keys cascade)."""
        done = self.conn.execute("DELETE FROM builder_component WHERE tenant = %s AND id = %s", (tenant, component_id))
        return done.rowcount > 0

    # -------------------------------------------------------- sites and Knovas Link (migration 0022)

    _SITE = """SELECT s.id, s.name, s.owner, s.options, s.created_at, u.display_name
                 FROM builder_site s LEFT JOIN users u ON u.id = s.created_by"""

    @staticmethod
    def _site(row) -> dict:
        return {"id": str(row[0]), "name": row[1], "owner": row[2], "options": list(row[3] or []),
                "created_at": _iso(row[4]), "created_by_name": row[5]}

    def create_site(self, tenant: str, *, name: str, owner: str, options: list, actor_id) -> Optional[str]:
        """A site of the tenant; None when it has one of that name."""
        row = self.conn.execute(
            """INSERT INTO builder_site (tenant, name, owner, options, created_by) VALUES (%s, %s, %s, %s::jsonb, %s)
               ON CONFLICT (tenant, name) DO NOTHING RETURNING id""",
            (tenant, name, owner, json.dumps(list(options)), actor_id)).fetchone()
        return str(row[0]) if row else None

    def sites(self, tenant: str) -> list[dict]:
        rows = self.conn.execute(self._SITE + " WHERE s.tenant = %s ORDER BY lower(s.name)", (tenant,)).fetchall()
        return [self._site(r) for r in rows]

    def site(self, tenant: str, site_id: str) -> Optional[dict]:
        try:
            uuid.UUID(str(site_id))
        except ValueError:
            return None
        row = self.conn.execute(self._SITE + " WHERE s.tenant = %s AND s.id = %s", (tenant, site_id)).fetchone()
        return self._site(row) if row else None

    def children(self, parent_id: str) -> list[dict]:
        """The components behind Knovas Link ``parent_id``, by their name on the site."""
        rows = self.conn.execute(self._COMPONENT + " WHERE c.parent_id = %s ORDER BY c.local_name",
                                 (parent_id,)).fetchall()
        return [self._component(r) for r in rows]

    def upsert_child(self, tenant: str, *, parent_id: str, site_id: Optional[str], local_name: str, kind: str,
                     name: str) -> str:
        """The row of the component ``local_name`` behind Link ``parent_id``, made the first time Link reports it."""
        def mine():
            return self.conn.execute("SELECT id FROM builder_component WHERE parent_id = %s AND local_name = %s",
                                     (parent_id, local_name)).fetchone()

        for attempt in range(5):
            found = mine()
            if found:
                return str(found[0])
            # The tenant's names are unique; a name someone else took gets a number.
            candidate = name if attempt == 0 else f"{name[:110]} ({attempt + 1})"
            row = self.conn.execute(
                """INSERT INTO builder_component (tenant, kind, name, site, owner, state, transport, site_id, parent_id,
                                                  local_name, enrolled_at)
                   SELECT %s, %s, %s, p.site, p.owner, 'enrolled', 'link', %s, p.id, %s, now()
                     FROM builder_component p WHERE p.id = %s
                   ON CONFLICT DO NOTHING RETURNING id""",
                (tenant, kind, candidate, site_id, local_name, parent_id)).fetchone()
            if row:
                return str(row[0])
        raise LookupError("no free name for a component behind Knovas Link")

    def pending_components(self, tenant: str) -> list[dict]:
        rows = self.conn.execute(self._COMPONENT + " WHERE c.tenant = %s AND c.state = 'pending'", (tenant,)).fetchall()
        return [self._component(r) for r in rows]

    def add_event(self, component_id: str, code: str, counts: dict) -> None:
        self.conn.execute("INSERT INTO builder_component_event (component, code, counts) VALUES (%s, %s, %s::jsonb)",
                          (component_id, code, json.dumps(counts or {})))

    def events(self, component_id: str, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT at, code, counts FROM builder_component_event WHERE component = %s ORDER BY at DESC, id DESC LIMIT %s",
            (component_id, limit)).fetchall()
        return [{"at": _iso(r[0]), "code": r[1], "counts": r[2] or {}} for r in rows]

    def add_run(self, component_id: str, *, kind: str, status: str, started_at: Optional[str], finished_at: Optional[str],
                duration_ms: int, added: int, changed: int, retired: int, errors: int, requested_by=None) -> str:
        row = self.conn.execute(
            """INSERT INTO builder_component_run (component, kind, status, started_at, finished_at, duration_ms, added,
                                                  changed, retired, errors, requested_by)
               VALUES (%s, %s, %s, COALESCE(%s::timestamptz, now()), %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (component_id, kind, status, started_at, finished_at, duration_ms, added, changed, retired, errors,
             requested_by)).fetchone()
        return str(row[0])

    def component_runs(self, component_id: str, limit: int = 30) -> list[dict]:
        rows = self.conn.execute(
            """SELECT id, kind, status, started_at, finished_at, duration_ms, added, changed, retired, errors
                 FROM builder_component_run WHERE component = %s ORDER BY started_at DESC LIMIT %s""",
            (component_id, limit)).fetchall()
        return [{"id": str(r[0]), "kind": r[1], "status": r[2], "started_at": _iso(r[3]), "finished_at": _iso(r[4]),
                 "duration_ms": r[5], "added": r[6], "changed": r[7], "retired": r[8], "errors": r[9]} for r in rows]

    def add_held(self, component_id: str, *, mapping: str, retire: dict, share: Optional[float]) -> str:
        row = self.conn.execute(
            "INSERT INTO builder_held_plan (component, mapping, retire, share) VALUES (%s, %s, %s::jsonb, %s) RETURNING id",
            (component_id, mapping, json.dumps(retire or {}), share)).fetchone()
        return str(row[0])

    def held_plans(self, component_id: str) -> list[dict]:
        rows = self.conn.execute(
            """SELECT h.id, h.mapping, h.held_at, h.retire, h.share, h.state, u.display_name, h.decided_at, h.reason
                 FROM builder_held_plan h LEFT JOIN users u ON u.id = h.decided_by
                WHERE h.component = %s ORDER BY h.held_at DESC LIMIT 100""",
            (component_id,)).fetchall()
        return [{"id": str(r[0]), "mapping": r[1], "held_at": _iso(r[2]), "retire": r[3] or {}, "share": r[4],
                 "state": r[5], "decided_by_name": r[6], "decided_at": _iso(r[7]), "reason": r[8]} for r in rows]

    def decide_held(self, component_id: str, plan_id: str, *, approve: bool, actor_id, reason: str) -> bool:
        try:
            uuid.UUID(str(plan_id))
        except ValueError:
            return False
        row = self.conn.execute(
            """UPDATE builder_held_plan SET state = %s, decided_by = %s, decided_at = now(), reason = %s
                WHERE component = %s AND id = %s AND state = 'held' RETURNING id""",
            ("approved" if approve else "rejected", actor_id, reason, component_id, plan_id)).fetchone()
        return row is not None

    def add_command(self, component_id: str, command: str, args: dict, actor_id) -> str:
        row = self.conn.execute(
            """INSERT INTO builder_component_command (component, command, args, created_by)
               VALUES (%s, %s, %s::jsonb, %s) RETURNING id""",
            (component_id, command, json.dumps(args or {}), actor_id)).fetchone()
        return str(row[0])

    def deliver_commands(self, component_id: str, at: str) -> list[dict]:
        """The commands not yet delivered, marked delivered now: a component gets each one once."""
        rows = self.conn.execute(
            """UPDATE builder_component_command SET delivered_at = %s
                WHERE component = %s AND delivered_at IS NULL RETURNING id, command, args, created_at""",
            (at, component_id)).fetchall()
        return sorted(({"id": str(r[0]), "command": r[1], "args": r[2] or {}, "created_at": _iso(r[3])} for r in rows),
                      key=lambda c: c["created_at"])

    def finish_command(self, component_id: str, command_id: str, result: str, at: str) -> bool:
        try:
            uuid.UUID(str(command_id))
        except ValueError:
            return False
        row = self.conn.execute(
            """UPDATE builder_component_command SET done_at = %s, result = %s
                WHERE component = %s AND id = %s AND done_at IS NULL RETURNING id""",
            (at, result, component_id, command_id)).fetchone()
        return row is not None

    def commands(self, component_id: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            """SELECT c.id, c.command, c.args, c.created_at, c.delivered_at, c.done_at, c.result, u.display_name
                 FROM builder_component_command c LEFT JOIN users u ON u.id = c.created_by
                WHERE c.component = %s ORDER BY c.created_at DESC LIMIT %s""", (component_id, limit)).fetchall()
        return [{"id": str(r[0]), "command": r[1], "args": r[2] or {}, "created_at": _iso(r[3]), "delivered_at": _iso(r[4]),
                 "done_at": _iso(r[5]), "result": r[6], "by": r[7]} for r in rows]

    # -------------------------------------------------------- the access window (B14)

    def request_posture(self, tenant: str, mode: str, actor_id) -> str:
        row = self.conn.execute(
            "INSERT INTO access_posture_request (tenant, mode, requested_by) VALUES (%s, %s, %s) RETURNING id",
            (tenant, mode, actor_id)).fetchone()
        return str(row[0])

    def posture_requests(self, tenant: str, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            """SELECT p.id, p.mode, p.requested_by, r.display_name, p.requested_at, a.display_name, p.approved_at, p.outcome
                 FROM access_posture_request p
                 LEFT JOIN users r ON r.id = p.requested_by LEFT JOIN users a ON a.id = p.approved_by
                WHERE p.tenant = %s ORDER BY p.requested_at DESC LIMIT %s""", (tenant, limit)).fetchall()
        return [{"id": str(x[0]), "mode": x[1], "requested_by": _uuid(x[2]), "requested_by_name": x[3],
                 "requested_at": _iso(x[4]), "approved_by_name": x[5], "approved_at": _iso(x[6]), "outcome": x[7]}
                for x in rows]

    def approve_posture(self, tenant: str, request_id: str, actor_id, outcome: str) -> bool:
        """A second administrator approves; never the one who asked (the table refuses it too)."""
        try:
            uuid.UUID(str(request_id))
        except ValueError:
            return False
        row = self.conn.execute(
            """UPDATE access_posture_request SET approved_by = %s, approved_at = now(), outcome = %s
                WHERE tenant = %s AND id = %s AND approved_at IS NULL AND requested_by <> %s RETURNING id""",
            (actor_id, outcome, tenant, request_id, actor_id)).fetchone()
        return row is not None

    def published_model(self, workspace_id: str) -> Optional[dict]:
        """The model of the newest published version, or None before the first publish."""
        row = self.conn.execute(
            """SELECT model FROM builder_version WHERE workspace = %s AND state = 'published'
                ORDER BY number DESC LIMIT 1""", (workspace_id,)).fetchone()
        return row[0] if row else None

    def served_by(self, component_id: str) -> dict:
        """The sources served through the component, in every workspace, and the mappings that read them.

        All of it comes from each workspace's newest **published** version (BB-07, B2): a source's settings (its
        address, its repository), its switches (MVP-25), which component serves it and the mappings that read it.
        A draft reaches a component -- a site's included -- only by being reviewed, approved and published; an edit
        that is not cannot steer a site's credential to another address."""
        sources, mappings = [], []
        for w in self.list():
            model = self.published_model(w.id) or {}
            mine = [s for s in model.get("sources") or [] if s.get("component") == component_id]
            keys = {s.get("key") for s in mine}
            sources += [served_source(w, s, s) for s in mine]
            mappings += [{"workspace": w.id, "id": (m.get("definition") or {}).get("id"),
                          "source": (m.get("builder") or {}).get("source")}
                         for m in model.get("mappings") or [] if (m.get("builder") or {}).get("source") in keys]
        return {"sources": sources, "mappings": mappings}

    def register_component(self, tenant: str, *, kind: str, name: str, site: str, owner: str, settings: dict,
                           actor_id) -> Optional[str]:
        """The new component's id; None when the tenant has one of that name."""
        row = self.conn.execute(
            """INSERT INTO builder_component (tenant, kind, name, site, owner, settings, registered_by)
               VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s) ON CONFLICT (tenant, name) DO NOTHING RETURNING id""",
            (tenant, kind, name, site, owner, json.dumps(settings), actor_id),
        ).fetchone()
        return str(row[0]) if row else None

    # -------------------------------------------------------- the team (B11)

    def grant_access(self, workspace_id: str, *, user_id, email: str, roles: list[str], ends_on: str, actor_id) -> str:
        """Grant ``roles`` on one workspace until ``ends_on`` (an ISO date); a second grant replaces the first."""
        row = self.conn.execute(
            """INSERT INTO builder_access (workspace, user_id, email, roles, ends_on, invited_by)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (workspace, user_id) DO UPDATE
                  SET roles = EXCLUDED.roles, ends_on = EXCLUDED.ends_on, email = EXCLUDED.email,
                      invited_by = EXCLUDED.invited_by, invited_at = now(), revoked_by = NULL, revoked_at = NULL
               RETURNING id""",
            (workspace_id, user_id, email, list(roles), ends_on, actor_id),
        ).fetchone()
        return str(row[0])

    def revoke_access(self, workspace_id: str, grant_id: str, actor_id) -> bool:
        try:
            uuid.UUID(str(grant_id))
        except ValueError:
            return False
        row = self.conn.execute(
            """UPDATE builder_access SET revoked_by = %s, revoked_at = now()
                WHERE workspace = %s AND id = %s AND revoked_at IS NULL RETURNING id""",
            (actor_id, workspace_id, grant_id),
        ).fetchone()
        return row is not None

    def access_grants(self, workspace_id: str) -> list[dict]:
        """Every grant on the workspace, newest first, with who holds it and who granted it."""
        rows = self.conn.execute(
            """SELECT a.id, a.user_id, a.email, u.display_name, a.roles, a.ends_on, a.invited_at, i.display_name,
                      a.revoked_at, r.display_name
                 FROM builder_access a
                 LEFT JOIN users u ON u.id = a.user_id
                 LEFT JOIN users i ON i.id = a.invited_by
                 LEFT JOIN users r ON r.id = a.revoked_by
                WHERE a.workspace = %s ORDER BY a.invited_at DESC""",
            (workspace_id,),
        ).fetchall()
        return [{"id": str(r[0]), "user_id": _uuid(r[1]), "email": r[2], "display_name": r[3], "roles": list(r[4]),
                 "ends_on": r[5].isoformat(), "invited_at": _iso(r[6]), "invited_by_name": r[7],
                 "revoked_at": _iso(r[8]), "revoked_by_name": r[9]} for r in rows]

    def grants_of(self, user_id) -> list[dict]:
        """The user's grants that are not revoked (expired ones too: the caller tells them apart by ends_on)."""
        rows = self.conn.execute(
            "SELECT workspace, roles, ends_on FROM builder_access WHERE user_id = %s AND revoked_at IS NULL",
            (user_id,),
        ).fetchall()
        return [{"workspace": str(r[0]), "roles": list(r[1]), "ends_on": r[2].isoformat()} for r in rows]

    def add_comment(self, workspace_id: str, target: str, body: str, author_id) -> str:
        row = self.conn.execute(
            "INSERT INTO builder_comment (workspace, target, body, author) VALUES (%s, %s, %s, %s) RETURNING id",
            (workspace_id, target, body, author_id),
        ).fetchone()
        return str(row[0])

    def comments(self, workspace_id: str, target: Optional[str] = None) -> list[dict]:
        """Comments, oldest first: all of the workspace, or those on one target."""
        rows = self.conn.execute(
            """SELECT c.id, c.target, c.body, c.author, u.display_name, c.at, c.resolved_at, r.display_name
                 FROM builder_comment c
                 LEFT JOIN users u ON u.id = c.author
                 LEFT JOIN users r ON r.id = c.resolved_by
                WHERE c.workspace = %s AND (%s::text IS NULL OR c.target = %s)
                ORDER BY c.at, c.id""",
            (workspace_id, target, target),
        ).fetchall()
        return [{"id": str(r[0]), "target": r[1], "body": r[2], "author": _uuid(r[3]), "author_name": r[4],
                 "at": _iso(r[5]), "resolved_at": _iso(r[6]), "resolved_by_name": r[7]} for r in rows]

    def resolve_comment(self, workspace_id: str, comment_id: str, actor_id) -> bool:
        try:
            uuid.UUID(str(comment_id))
        except ValueError:
            return False
        row = self.conn.execute(
            """UPDATE builder_comment SET resolved_by = %s, resolved_at = now()
                WHERE workspace = %s AND id = %s AND resolved_at IS NULL RETURNING id""",
            (actor_id, workspace_id, comment_id),
        ).fetchone()
        return row is not None

    def log_access(self, workspace_id: Optional[str], *, user_id, email: str, method: str, action: str, status: int,
                   via: str) -> None:
        self.conn.execute(
            """INSERT INTO builder_access_log (workspace, user_id, email_snapshot, method, action, status, via)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (workspace_id, user_id, email, method, action, status, via),
        )

    def access_log(self, workspace_id: str, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            """SELECT l.at, l.user_id, l.email_snapshot, u.display_name, l.method, l.action, l.status, l.via
                 FROM builder_access_log l LEFT JOIN users u ON u.id = l.user_id
                WHERE l.workspace = %s ORDER BY l.at DESC, l.id DESC LIMIT %s""",
            (workspace_id, limit),
        ).fetchall()
        return [{"at": _iso(r[0]), "user_id": _uuid(r[1]), "email": r[2], "actor_name": r[3] or r[2], "method": r[4],
                 "action": r[5], "status": r[6], "via": r[7]} for r in rows]
