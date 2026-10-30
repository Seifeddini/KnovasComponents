"""The builder blueprint on a bare Flask app, for tests and the click-dummy.

No identity database: the gate is a stand-in that returns whichever user the
caller hands it, which is all ``create_builder_blueprint`` asks of a gate, and
the workspace store keeps its rows in memory.
``scripts/builder_preview.py`` serves the same app for screenshots.
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask

from builder.package import content_hash, summarise
from builder.store import Workspace
from web_interface.builder_routes import create_builder_blueprint

WEB = Path(__file__).resolve().parents[1] / "src" / "web_interface"
#: A small generic configuration package (a fictitious company).
SAMPLE_PACKAGE = Path(__file__).resolve().parent / "fixtures" / "builder" / "sample-package"


class StandInUser:
    def __init__(self, roles=frozenset(), display_name="Alex Beispiel", email="alex@example.org"):
        self.id = uuid.uuid4()
        self.roles = frozenset(roles)
        self.display_name = display_name
        self.email = email


class StandInGate:
    def __init__(self, user=None):
        self.user = user

    def current_user(self):
        return self.user


class MemoryWorkspaceStore:
    """``builder.store.WorkspaceStore`` without PostgreSQL, for the click-dummy.

    Same methods, same return types; one draft per workspace, as the
    database's index enforces. ``tests/test_builder_store.py`` holds the real
    store to the same behaviour.
    """

    def __init__(self, user_names=None):
        self.rows: dict[str, dict] = {}
        self.user_names = dict(user_names or {})

    def _workspace(self, row) -> Workspace:
        change = row["changes"][0] if row["changes"] else None
        last = None
        if change:
            last = {"path": change["path"], "at": change["at"], "actor": change["actor"],
                    "actor_name": change["actor_name"]}
        return Workspace(
            id=row["id"], name=row["name"], tenant=None, created_at=row["created_at"],
            created_by=row["created_by"], draft_number=1, published_number=None,
            summary=summarise(row["model"]), last_change=last,
        )

    def list(self):
        rows = sorted(self.rows.values(), key=lambda r: (r["name"].lower(), r["created_at"]))
        return [self._workspace(r) for r in rows]

    def get(self, workspace_id):
        row = self.rows.get(str(workspace_id))
        return self._workspace(row) if row else None

    def model(self, workspace_id):
        row = self.rows.get(str(workspace_id))
        return copy.deepcopy(row["model"]) if row else None

    def changes(self, workspace_id, limit=20):
        row = self.rows.get(str(workspace_id))
        return list(row["changes"][:limit]) if row else []

    def create_from_import(self, *, name, model, actor_id, source):
        workspace_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        actor = str(actor_id) if actor_id else None
        self.rows[workspace_id] = {
            "id": workspace_id, "name": name.strip(), "created_at": now, "created_by": actor,
            "model": copy.deepcopy(model),
            "changes": [{"path": "import", "at": now, "actor": actor,
                         "actor_name": self.user_names.get(actor),
                         "after": {**source, "content_hash": content_hash(model)}}],
        }
        return workspace_id


def make_app(user=None, store=None) -> Flask:
    """The builder on a bare app. ``store``: a MemoryWorkspaceStore, or None for none."""
    app = Flask(
        __name__,
        template_folder=str(WEB / "templates"),
        static_folder=str(WEB / "static"),
    )
    app.testing = True
    app.secret_key = "builder-harness"

    @app.route("/login")
    def login():
        return "login"

    @app.route("/")
    def index():
        return "platform"

    app.register_blueprint(create_builder_blueprint(
        StandInGate(user),
        page_context=lambda: {
            "app_title": "Knovas",
            "brand": "Knovas",
            "asset_version": "test",
            "platform_url": "/",
            "csrf_token": "harness",
        },
        store_factory=(lambda: store) if store is not None else None,
    ))
    return app
