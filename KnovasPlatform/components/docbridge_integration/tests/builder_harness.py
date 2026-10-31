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

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def _actor(self, actor_id):
        actor = str(actor_id) if actor_id else None
        return actor, self.user_names.get(actor)

    def _workspace(self, row) -> Workspace:
        change = row["changes"][0] if row["changes"] else None
        last = None
        if change:
            last = {"path": change["path"], "at": change["at"], "actor": change["actor"],
                    "actor_name": change["actor_name"]}
        return Workspace(
            id=row["id"], name=row["name"], tenant=row.get("tenant"), created_at=row["created_at"],
            created_by=row["created_by"], draft_number=row["draft"]["number"] if row["draft"] else None,
            published_number=row["published"][-1]["number"] if row["published"] else None,
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
        return self.create(name=name, model=model, actor_id=actor_id, source=source, change="import")

    def create(self, *, name, model, actor_id, source, change="create", tenant=None):
        workspace_id = str(uuid.uuid4())
        now = self._now()
        actor, actor_name = self._actor(actor_id)
        draft = {"id": str(uuid.uuid4()), "number": 1}
        self.rows[workspace_id] = {
            "id": workspace_id, "name": name.strip(), "created_at": now, "created_by": actor, "tenant": tenant,
            "model": copy.deepcopy(model), "draft": draft, "published": [], "approvals": [], "runs": [],
            "changes": [{"path": change, "at": now, "actor": actor, "actor_name": actor_name, "before": None,
                         "after": {**source, "content_hash": content_hash(model)}, "version": draft["id"]}],
        }
        return workspace_id

    def edit(self, workspace_id, actor_id, mutate):
        row = self.rows.get(workspace_id)
        if row is None:
            raise LookupError(workspace_id)
        model = copy.deepcopy(row["model"])
        changes = mutate(model)  # an exception leaves the row as it was
        if not changes:
            return model, [], None
        if row["draft"] is None:
            row["draft"] = {"id": str(uuid.uuid4()), "number": row["published"][-1]["number"] + 1}
        batch = str(uuid.uuid4())
        now = self._now()
        actor, actor_name = self._actor(actor_id)
        row["model"] = copy.deepcopy(model)
        for change in changes:
            row["changes"].insert(0, {"path": change["path"], "at": now, "actor": actor, "actor_name": actor_name,
                                      "before": copy.deepcopy(change["before"]),
                                      "after": copy.deepcopy(change["after"]), "batch": batch,
                                      "version": row["draft"]["id"]})
        return model, changes, batch

    def batch(self, workspace_id, batch_id):
        row = self.rows.get(workspace_id) or {"changes": []}
        return [{"path": c["path"], "before": copy.deepcopy(c["before"]), "after": copy.deepcopy(c["after"])}
                for c in reversed(row["changes"]) if c.get("batch") == batch_id]

    # ---- review and publish (B5)

    def draft(self, workspace_id):
        row = self.rows.get(workspace_id)
        if row is None or row["draft"] is None:
            return None
        return {**row["draft"], "content_hash": content_hash(row["model"])}

    def draft_changes(self, workspace_id):
        row = self.rows.get(workspace_id)
        if row is None or row["draft"] is None:
            return []
        version = row["draft"]["id"]
        return [{k: copy.deepcopy(c.get(k)) for k in ("path", "before", "after", "actor", "actor_name", "at")}
                for c in reversed(row["changes"]) if c.get("version") == version]

    def approve(self, workspace_id, reviewer_id, content_hash_):
        draft = self.draft(workspace_id)
        if draft is None or draft["content_hash"] != content_hash_:
            return False
        actor, name = self._actor(reviewer_id)
        self.rows[workspace_id]["approvals"].append({"reviewer": actor, "reviewer_name": name, "at": self._now(),
                                                     "content_hash": content_hash_, "version": draft["id"]})
        return True

    def approvals(self, workspace_id):
        draft = self.draft(workspace_id)
        if draft is None:
            return []
        return [{k: a[k] for k in ("reviewer", "reviewer_name", "at", "content_hash")}
                for a in self.rows[workspace_id]["approvals"] if a["version"] == draft["id"]]

    def record_run(self, workspace_id, *, kind, actor_id, tenant, content_hash, bundle_hash, status, summary):
        actor, name = self._actor(actor_id)
        run = {"id": str(uuid.uuid4()), "kind": kind, "actor": actor, "actor_name": name, "tenant": tenant,
               "content_hash": content_hash, "bundle_hash": bundle_hash, "status": status,
               "summary": copy.deepcopy(summary), "at": self._now()}
        self.rows[workspace_id]["runs"].insert(0, run)
        return run["id"]

    def runs(self, workspace_id, limit=20):
        return copy.deepcopy(self.rows[workspace_id]["runs"][:limit])

    def publish(self, workspace_id, publisher_id, content_hash_, bundle, bundle_hash):
        draft = self.draft(workspace_id)
        if draft is None or draft["content_hash"] != content_hash_:
            return None
        row = self.rows[workspace_id]
        row["published"].append({"number": draft["number"], "bundle": copy.deepcopy(bundle), "bundle_hash": bundle_hash,
                                 "published_at": self._now(), "published_by": self._actor(publisher_id)[0]})
        row["draft"] = None
        return draft["number"]

    def published_bundle(self, workspace_id):
        row = self.rows.get(workspace_id)
        if not row or not row["published"]:
            return None
        last = row["published"][-1]
        return {k: copy.deepcopy(last[k]) for k in ("number", "bundle", "bundle_hash", "published_at")}

    def link_tenant(self, workspace_id, tenant):
        self.rows[workspace_id]["tenant"] = tenant


class FakeEngine:
    """``builder.publisher.Engine`` against a tenant held in memory.

    The tenant knows node types by name with their attribute names; a plan
    creates what it lacks, refuses types named in ``refuse`` and lists the
    attributes the graph store cannot hold (not enum) as not configured. The
    bundle is the package's own files' digest, enough to tell versions apart.
    """

    def __init__(self, tenant_types=None, refuse=(), missing=None, version="0.1.0"):
        self.tenant = {k: set(v) for k, v in (tenant_types or {}).items()}
        self.refuse = set(refuse)
        self._missing = missing
        self._version = version
        self.applied = []

    def version(self):
        return self._version

    def missing(self):
        return self._missing

    def compile(self, model):
        from builder import package
        from builder.publisher import Compiled
        import json

        files = package.write_package(model)
        sections = {"types": [t["key"] for t in model["ontology"]["types"] if not t.get("retired")]}
        digest = content_hash(sections)
        bundle = {"header": {"format": "brain-definitions-bundle", "format_version": 1, "content_hash": "sha256:" + digest},
                  "sections": sections}
        return Compiled(files=files, bundle=bundle, bundle_bytes=json.dumps(bundle, sort_keys=True).encode("utf-8"),
                        content_hash=bundle["header"]["content_hash"])

    def plan(self, model, tenant):
        items, not_configured, unresolved = [], [], []
        for t in model["ontology"]["types"]:
            if t.get("retired"):
                continue
            if t["key"] in self.refuse:
                unresolved.append(t["key"])
                continue
            have = self.tenant.get(t["key"])
            if have is None:
                items.append({"op": "create_type", "type": t["key"]})
                have = set()
            for a in t.get("attributes") or []:
                if a.get("datatype") != "enum":
                    not_configured.append({"type": t["key"], "attribute": a["name"]})
                elif a["name"] not in have:
                    items.append({"op": "create_attribute", "type": t["key"], "attribute": a["name"]})
        return {"items": items, "not_configured": not_configured, "unresolved_parents": unresolved, "hierarchy": True}

    def apply(self, model, tenant, plan):
        written = 0
        for item in plan["items"]:
            self.tenant.setdefault(item["type"], set())
            if item["op"] == "create_attribute":
                self.tenant[item["type"]].add(item["attribute"])
            written += 1
        self.applied.append(plan)
        return {"written": written, "refused": list(plan["unresolved_parents"]), "skipped": [], "errors": []}


def make_app(user=None, store=None, tenant=None, engine=None, tenant_types=None) -> Flask:
    """The builder on a bare app. ``store``: a MemoryWorkspaceStore, or None for none.

    ``app.extensions["builder_gate"]`` is the stand-in gate: set its ``user``
    to act as someone else (a second person approving).
    """
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

    gate = StandInGate(user)
    app.extensions["builder_gate"] = gate
    app.register_blueprint(create_builder_blueprint(
        gate,
        page_context=lambda: {
            "app_title": "Knovas",
            "brand": "Knovas",
            "asset_version": "test",
            "platform_url": "/",
            "csrf_token": "harness",
        },
        store_factory=(lambda: store) if store is not None else None,
        tenant_provider=lambda: tenant,
        tenant_ontology=(lambda: (tenant_types[0], tenant_types[1])) if tenant_types is not None else None,
        engine_factory=(lambda: engine) if engine is not None else None,
    ))
    return app
