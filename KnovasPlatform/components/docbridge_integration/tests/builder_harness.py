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


class MemoryDirectory:
    """identity's UserRepository for the invitations (step B11): accounts by e-mail, made with a password."""

    def __init__(self, store=None, users=()):
        self.store = store
        self.accounts = {u.email: u for u in users}
        self.passwords = {}

    def add(self, user):
        self.accounts[user.email] = user
        if self.store is not None:
            self.store.user_names[str(user.id)] = user.display_name
        return user

    def get_by_email(self, email):
        return self.accounts.get(email)

    def create(self, *, email, display_name, password=None, must_change_password=False, created_by=None, **_):
        user = self.add(StandInUser(frozenset(), display_name, email))
        self.passwords[email] = password
        return user

    # The access window's group grants (B14), as identity.users.UserRepository keeps them.
    def list_all(self):
        return sorted(self.accounts.values(), key=lambda u: u.email)

    def _grants(self):
        if not hasattr(self, "group_grants"):
            self.group_grants = {}
        return self.group_grants

    def access_grants_of(self, user_id):
        return [{"group_id": g, "source": "manual", "ends_on": e, "granted_at": None}
                for g, e in sorted(self._grants().get(str(user_id), {}).items())]

    def access_groups_of(self, user_id):
        from datetime import date

        today = date.today().isoformat()
        return tuple(g for g, e in sorted(self._grants().get(str(user_id), {}).items()) if not e or e >= today)

    def grant_access_group(self, user_id, group_id, *, ends_on=None, by=None):
        self._grants().setdefault(str(user_id), {})[str(group_id)] = ends_on

    def revoke_access_group(self, user_id, group_id):
        return self._grants().get(str(user_id), {}).pop(str(group_id), "gone") != "gone"


class MemoryWorkspaceStore:
    """``builder.store.WorkspaceStore`` without PostgreSQL, for the click-dummy.

    Same methods, same return types; one draft per workspace, as the
    database's index enforces. ``tests/test_builder_store.py`` holds the real
    store to the same behaviour.
    """

    def __init__(self, user_names=None):
        self.rows: dict[str, dict] = {}
        self.user_names = dict(user_names or {})
        self.component_rows: list[dict] = []

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
        rows = sorted((r for r in self.rows.values() if not r.get("deleted_at")),
                      key=lambda r: (r["name"].lower(), r["created_at"]))
        return [self._workspace(r) for r in rows]

    def get(self, workspace_id):
        row = self.rows.get(str(workspace_id))
        return self._workspace(row) if row and not row.get("deleted_at") else None

    def delete(self, workspace_id, actor_id):
        row = self.rows.get(str(workspace_id))
        if row is None or row.get("deleted_at"):
            return False
        row["deleted_at"], row["deleted_by"] = self._now(), self._actor(actor_id)[0]
        return True

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

    _APPROVAL = ("reviewer", "reviewer_name", "at", "content_hash", "own_changes", "reason")

    def approve(self, workspace_id, reviewer_id, content_hash_, *, own_changes=False, reason=None):
        draft = self.draft(workspace_id)
        if draft is None or draft["content_hash"] != content_hash_:
            return False
        actor, name = self._actor(reviewer_id)
        self.rows[workspace_id]["approvals"].append({"reviewer": actor, "reviewer_name": name, "at": self._now(),
                                                     "content_hash": content_hash_, "version": draft["id"],
                                                     "own_changes": bool(own_changes), "reason": reason})
        return True

    def approvals(self, workspace_id):
        draft = self.draft(workspace_id)
        if draft is None:
            return []
        return [{k: a[k] for k in self._APPROVAL}
                for a in self.rows[workspace_id]["approvals"] if a["version"] == draft["id"]]

    def approval_history(self, workspace_id):
        return [{k: a[k] for k in self._APPROVAL} for a in self.rows[workspace_id]["approvals"]]

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

    # -------------------------------------------------------- the team (B11)

    def grant_access(self, workspace_id, *, user_id, email, roles, ends_on, actor_id):
        grants = self.rows[workspace_id].setdefault("grants", [])
        old = next((g for g in grants if g["user_id"] == str(user_id)), None)
        grant = {"id": old["id"] if old else str(uuid.uuid4()), "user_id": str(user_id), "email": email,
                 "display_name": self.user_names.get(str(user_id)), "roles": list(roles), "ends_on": ends_on,
                 "invited_at": self._now(), "invited_by_name": self._actor(actor_id)[1], "revoked_at": None,
                 "revoked_by_name": None}
        if old:
            grants.remove(old)
        grants.insert(0, grant)
        return grant["id"]

    def revoke_access(self, workspace_id, grant_id, actor_id):
        grant = next((g for g in self.rows[workspace_id].get("grants") or [] if g["id"] == grant_id), None)
        if grant is None or grant["revoked_at"]:
            return False
        grant.update(revoked_at=self._now(), revoked_by_name=self._actor(actor_id)[1])
        return True

    def access_grants(self, workspace_id):
        return copy.deepcopy(self.rows[workspace_id].get("grants") or [])

    def grants_of(self, user_id):
        return [{"workspace": wid, "roles": list(g["roles"]), "ends_on": g["ends_on"]}
                for wid, row in self.rows.items() for g in row.get("grants") or []
                if g["user_id"] == str(user_id) and not g["revoked_at"]]

    def add_comment(self, workspace_id, target, body, author_id):
        actor, name = self._actor(author_id)
        comment = {"id": str(uuid.uuid4()), "target": target, "body": body, "author": actor, "author_name": name,
                   "at": self._now(), "resolved_at": None, "resolved_by_name": None}
        self.rows[workspace_id].setdefault("comments", []).append(comment)
        return comment["id"]

    def comments(self, workspace_id, target=None):
        return [copy.deepcopy(c) for c in self.rows[workspace_id].get("comments") or []
                if target is None or c["target"] == target]

    def resolve_comment(self, workspace_id, comment_id, actor_id):
        comment = next((c for c in self.rows[workspace_id].get("comments") or [] if c["id"] == comment_id), None)
        if comment is None or comment["resolved_at"]:
            return False
        comment.update(resolved_at=self._now(), resolved_by_name=self._actor(actor_id)[1])
        return True

    def log_access(self, workspace_id, *, user_id, email, method, action, status, via):
        self.access_rows = getattr(self, "access_rows", [])
        self.access_rows.insert(0, {"workspace": workspace_id, "at": self._now(), "user_id": str(user_id),
                                    "email": email, "actor_name": self.user_names.get(str(user_id)) or email,
                                    "method": method, "action": action, "status": status, "via": via})

    def access_log(self, workspace_id, limit=100):
        return [{k: v for k, v in r.items() if k != "workspace"}
                for r in getattr(self, "access_rows", []) if r["workspace"] == workspace_id][:limit]

    def record_review(self, workspace_id, node_type, actor_id):
        self.rows[workspace_id].setdefault("reviews", {})[node_type] = {"at": self._now(),
                                                                       "actor_name": self._actor(actor_id)[1]}

    def reviews(self, workspace_id):
        return copy.deepcopy(self.rows[workspace_id].get("reviews") or {})

    # Sync components (B7), per tenant.
    _COMPONENT_DEFAULTS = {"transport": "adapter", "paused": False, "schedule": {"mode": "manual"}, "version": "",
                           "capabilities": [], "enrol_expires_at": None, "enrolled_at": None,
                           "certificate_fingerprint": None, "last_heartbeat_at": None, "reported_state": None,
                           "lag_seconds": None, "open_errors": 0, "revoked_at": None, "source_checks": {}}

    def components(self, tenant, *, every=False):
        states = ("registered", "enrolled", "pending", "revoked") if every else ("registered", "enrolled")
        found = [c for c in self.component_rows if c["tenant"] == tenant and c["state"] in states]
        return [{k: copy.deepcopy(v) for k, v in c.items() if k != "tenant"} for c in sorted(found, key=lambda c: c["name"])]

    def component(self, tenant, component_id, *, any_state=False):
        return next((c for c in self.components(tenant, every=any_state) if c["id"] == component_id), None)

    def register_component(self, tenant, *, kind, name, site, owner, settings, actor_id):
        if any(c["tenant"] == tenant and c["name"] == name for c in self.component_rows):
            return None
        row = {"id": str(uuid.uuid4()), "tenant": tenant, "kind": kind, "name": name, "site": site, "owner": owner,
               "settings": copy.deepcopy(settings), "state": "registered", "registered_at": self._now(),
               "registered_by_name": self._actor(actor_id)[1], **copy.deepcopy(self._COMPONENT_DEFAULTS)}
        self.component_rows.append(row)
        return row["id"]

    # -------------------------------------------------------- enrolment and monitoring (B13)

    def create_component(self, tenant, *, kind, name, site, owner, actor_id, enrol_expires_at):
        cid = self.register_component(tenant, kind=kind, name=name, site=site, owner=owner, settings={}, actor_id=actor_id)
        if cid is not None:
            self.update_component(cid, state="pending", enrol_expires_at=enrol_expires_at)
            self._row(cid)["transport"] = "v1"
        return cid

    def _row(self, component_id):
        return next(c for c in self.component_rows if c["id"] == component_id)

    def delete_component(self, tenant, component_id):
        before = len(self.component_rows)
        self.component_rows = [c for c in self.component_rows if not (c["tenant"] == tenant and c["id"] == component_id)]
        if len(self.component_rows) == before:
            return False
        for bag in self.__dict__.get("_component_bags", {}).values():      # as the foreign keys cascade
            bag[:] = [row for row in bag if row.get("component") != component_id]
        return True

    def update_component(self, component_id, **fields):
        row = self._row(component_id)
        unknown = set(fields) - set(self._COMPONENT_DEFAULTS) - {"state", "name", "site", "owner", "settings"}
        if unknown:
            raise ValueError(f"not a component field: {sorted(unknown)}")
        row.update(copy.deepcopy(fields))

    def pending_components(self, tenant):
        return [c for c in self.components(tenant, every=True) if c["state"] == "pending"]

    def _bag(self, name):
        if not hasattr(self, "_component_bags"):
            self._component_bags = {}
        return self._component_bags.setdefault(name, [])

    def add_event(self, component_id, code, counts):
        self._bag("events").insert(0, {"component": component_id, "at": self._now(), "code": code,
                                       "counts": dict(counts or {})})

    def events(self, component_id, limit=50):
        return [{k: e[k] for k in ("at", "code", "counts")} for e in self._bag("events") if e["component"] == component_id][:limit]

    def add_run(self, component_id, *, kind, status, started_at, finished_at, duration_ms, added, changed, retired,
                errors, requested_by=None):
        run = {"id": str(uuid.uuid4()), "component": component_id, "kind": kind, "status": status,
               "started_at": started_at or self._now(), "finished_at": finished_at, "duration_ms": duration_ms,
               "added": added, "changed": changed, "retired": retired, "errors": errors}
        self._bag("runs").append(run)
        return run["id"]

    def component_runs(self, component_id, limit=30):
        mine = [r for r in self._bag("runs") if r["component"] == component_id]
        return [{k: v for k, v in r.items() if k != "component"}
                for r in sorted(mine, key=lambda r: r["started_at"], reverse=True)][:limit]

    def add_held(self, component_id, *, mapping, retire, share):
        plan = {"id": str(uuid.uuid4()), "component": component_id, "mapping": mapping, "held_at": self._now(),
                "retire": dict(retire or {}), "share": share, "state": "held", "decided_by_name": None,
                "decided_at": None, "reason": ""}
        self._bag("held").insert(0, plan)
        return plan["id"]

    def held_plans(self, component_id):
        return [{k: v for k, v in h.items() if k != "component"} for h in self._bag("held") if h["component"] == component_id]

    def decide_held(self, component_id, plan_id, *, approve, actor_id, reason):
        plan = next((h for h in self._bag("held") if h["component"] == component_id and h["id"] == plan_id), None)
        if plan is None or plan["state"] != "held":
            return False
        plan.update(state="approved" if approve else "rejected", decided_by_name=self._actor(actor_id)[1],
                    decided_at=self._now(), reason=reason)
        return True

    def add_command(self, component_id, command, args, actor_id):
        cmd = {"id": str(uuid.uuid4()), "component": component_id, "command": command, "args": dict(args or {}),
               "created_at": self._now(), "delivered_at": None, "done_at": None, "result": None,
               "by": self._actor(actor_id)[1]}
        self._bag("commands").append(cmd)
        return cmd["id"]

    def deliver_commands(self, component_id, at):
        out = []
        for cmd in self._bag("commands"):
            if cmd["component"] == component_id and cmd["delivered_at"] is None:
                cmd["delivered_at"] = at
                out.append({k: copy.deepcopy(cmd[k]) for k in ("id", "command", "args", "created_at")})
        return out

    def finish_command(self, component_id, command_id, result, at):
        cmd = next((c for c in self._bag("commands") if c["component"] == component_id and c["id"] == command_id), None)
        if cmd is None or cmd["done_at"] is not None:
            return False
        cmd.update(done_at=at, result=result)
        return True

    def commands(self, component_id, limit=20):
        mine = [c for c in self._bag("commands") if c["component"] == component_id]
        return [{k: v for k, v in c.items() if k != "component"} for c in reversed(mine)][:limit]

    def request_posture(self, tenant, mode, actor_id):
        req = {"id": str(uuid.uuid4()), "tenant": tenant, "mode": mode, "requested_by": str(actor_id),
               "requested_by_name": self._actor(actor_id)[1], "requested_at": self._now(), "approved_by_name": None,
               "approved_at": None, "outcome": None}
        self._bag("posture").insert(0, req)
        return req["id"]

    def posture_requests(self, tenant, limit=10):
        return [{k: v for k, v in r.items() if k != "tenant"} for r in self._bag("posture") if r["tenant"] == tenant][:limit]

    def approve_posture(self, tenant, request_id, actor_id, outcome):
        req = next((r for r in self._bag("posture") if r["tenant"] == tenant and r["id"] == request_id), None)
        if req is None or req["approved_at"] or req["requested_by"] == str(actor_id):
            return False
        req.update(approved_by_name=self._actor(actor_id)[1], approved_at=self._now(), outcome=outcome)
        return True

    def served_by(self, component_id):
        sources, mappings = [], []
        for w in self.list():
            model = self.model(w.id) or {}
            mine = [s for s in model.get("sources") or [] if s.get("component") == component_id]
            keys = {s.get("key") for s in mine}
            sources += [{"workspace": w.id, "workspace_name": w.name, "key": s.get("key"), "name": s.get("name"),
                         "kind": s.get("kind"), "access": s.get("access") or "read", "settings": s.get("settings") or {}} for s in mine]
            mappings += [{"workspace": w.id, "id": (m.get("definition") or {}).get("id"),
                          "source": (m.get("builder") or {}).get("source")}
                         for m in model.get("mappings") or [] if (m.get("builder") or {}).get("source") in keys]
        return {"sources": sources, "mappings": mappings}


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
        self.events = []
        self.nodes = {}
        self.mapping_plans = []

    def version(self):
        return self._version

    def missing(self):
        return self._missing

    def compile(self, model):
        from builder import package
        from builder.publisher import Compiled
        import json

        files = package.write_package(model)
        sections = {"types": [t["key"] for t in model["ontology"]["types"] if not t.get("retired")],
                    "processes": model.get("processes")}
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

    def mapping_plan(self, model, mapping_id, run):
        """brain-runner's plan shape over the nodes the stand-in tenant holds (``self.nodes``: type -> key -> facts)."""
        from brain_runner import guard

        self.mapping_plans.append(mapping_id)
        seen, add, change, unchanged = set(), 0, 0, 0
        for cand in run.candidates:
            held = self.nodes.get(cand.type, {})
            seen.add((cand.type, cand.key))
            facts = {k: v.value for k, v in cand.facts.items()}
            if cand.key not in held:
                add += 1
            elif held[cand.key] != facts:
                change += 1
            else:
                unchanged += 1
        types = {c.type for c in run.candidates}
        owned = {t: len(self.nodes.get(t, {})) for t in types}
        retire = {t: sum(1 for k in self.nodes.get(t, {}) if (t, k) not in seen) for t in types}
        verdict = guard.check(next(m["definition"] for m in model["mappings"] if m["definition"]["id"] == mapping_id),
                              owned, retire)
        return {"summary": {mapping_id: {"add": add, "change": change, "retire": sum(retire.values()),
                                         "unchanged": unchanged, "drift": 0}},
                "guard": {mapping_id: verdict}, "blocked": [mapping_id] if verdict["tripped"] else [],
                "observed": {mapping_id: {"source_items": run.source_items, "candidates": len(run.candidates)}}}

    def run_event(self, model, **facts):
        self.events.append(facts)
        return {"written": True, "key": f"EVT-builder-publish-v{facts['version']}", "errors": [], "why": None}

    def read(self, model, types):
        """The stand-in tenant's graph (``self.graph``, as ``seed_graph`` makes it) for ``types``."""
        graph = getattr(self, "graph", None) or {"taken_at": None, "nodes": [], "edges": []}
        nodes = [n for n in graph["nodes"] if n["type"] in types]
        ids = {n["id"] for n in nodes}
        return {"taken_at": graph.get("taken_at"), "nodes": copy.deepcopy(nodes),
                "edges": [dict(e) for e in graph["edges"] if e["lo"] in ids and e["hi"] in ids]}


def seed_graph(*documents, at="2026-10-30T09:00:00+00:00", ages=None):
    """A tenant's graph from seed documents ({nodes: [{type, key, name, facts, description?}], edges: [{from,
    relation, to}]}), as brain-runner's snapshot gives it: every fact valid from ``at``, or ``ages[key]``."""
    nodes, by_key, edges = [], {}, []
    for doc in documents:
        for raw in (doc or {}).get("nodes") or []:
            if not isinstance(raw, dict) or not raw.get("type") or not raw.get("key"):
                continue
            when = (ages or {}).get(raw["key"]) or raw.get("seen") or at
            facts = {"key": {"value": raw["key"], "valid_from": when, "created_at": when}}
            for name, value in (raw.get("facts") or {}).items():
                facts[name] = {"value": str(value), "valid_from": when, "created_at": when}
            if raw.get("source"):
                facts["source"] = {"value": str(raw["source"]), "valid_from": when, "created_at": when}
            known = by_key.get(raw["key"])
            if known is not None and known["type"] == raw["type"]:
                # The tenant holds one node per type and key: a second source adds to it.
                known["facts"].update({k: v for k, v in facts.items() if k not in known["facts"]})
                known["description"] = known["description"] or str(raw.get("description") or "")
                continue
            node = {"id": f"n{len(nodes) + 1}", "type": raw["type"], "name": str(raw.get("name") or ""),
                    "description": str(raw.get("description") or ""), "facts": facts}
            nodes.append(node)
            by_key.setdefault(raw["key"], node)
        for raw in (doc or {}).get("edges") or []:
            lo, hi = by_key.get(str(raw.get("from"))), by_key.get(str(raw.get("to")))
            if lo and hi and raw.get("relation") and not any(
                    e["lo"] == lo["id"] and e["hi"] == hi["id"] and e["relation"] == raw["relation"] for e in edges):
                edges.append({"id": f"e{len(edges) + 1}", "lo": lo["id"], "hi": hi["id"], "relation": raw["relation"]})
    return {"taken_at": at, "nodes": nodes, "edges": edges}


def seed_from_runs(*runs):
    """brain-runner mapping runs as a seed document: each candidate a node (facts as written, seen as of its
    source item), each of its relations an edge."""
    nodes, edges = [], []
    for run in runs:
        for c in run.candidates:
            seen = str(c.seen or "")
            nodes.append({"type": c.type, "key": c.key, "name": c.name, "description": c.description or "",
                          "facts": {k: v.value for k, v in c.facts.items()},
                          "seen": (seen + "T00:00:00+00:00") if len(seen) == 10 else (seen or None)})
            for r in c.relations:
                ends = (r["to_key"], c.key) if r.get("direction") == "in" else (c.key, r["to_key"])
                edges.append({"from": ends[0], "relation": r["relation"], "to": ends[1]})
    return {"nodes": nodes, "edges": edges}


class FakeAccess:
    """The Brain's access groups, folder rules and documents in memory (builder.access_backend.BrainAccess's face).

    It decides readability as the Brain does: a document governed by the longest matching folder rule is read by
    the members of the rule's groups and of every group above them.
    """

    def __init__(self, directory, documents=()):
        self.directory = directory
        self.groups = {"g-admin": {"group_id": "g-admin", "name": "tenant-admin", "is_admin": True, "parent_id": None}}
        self.rule_rows = []
        self.document_pointers = list(documents)
        self.node_type_rows = []
        self.posture_state = {"mode": "disabled", "backfill_complete": True}
        self.moves_supported = True

    def tree(self):
        from builder.access import Tree

        return Tree.of(copy.deepcopy(list(self.groups.values())))

    def create(self, name, parent, acting_as):
        gid = "g-" + "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
        self.groups[gid] = {"group_id": gid, "name": name, "is_admin": False, "parent_id": parent}
        return gid

    def rename(self, gid, name):
        self.groups[gid]["name"] = name

    def move(self, gid, parent):
        from builder.access import AccessError

        if not self.moves_supported:
            raise AccessError("Das Brain hat die Gruppe nicht verschoben: diese Brain-Version kann Gruppen noch nicht "
                              "umhängen (PATCH /secured/access_groups mit parent).", 501, "move_unsupported")
        self.groups[gid]["parent_id"] = parent

    def delete(self, gid):
        del self.groups[gid]

    def rules(self):
        return copy.deepcopy(self.rule_rows)

    def create_rule(self, prefix, groups, acting_as):
        rule = {"rule_id": f"r{len(self.rule_rows) + 1}", "pointer_prefix": prefix, "access_groups": list(groups)}
        self.rule_rows.append(rule)
        return rule

    def delete_rule(self, rule_id):
        self.rule_rows = [r for r in self.rule_rows if r["rule_id"] != rule_id]

    def _rule_of(self, pointer):
        matching = [r for r in self.rule_rows if pointer.startswith(r["pointer_prefix"])]
        return max(matching, key=lambda r: len(r["pointer_prefix"]), default=None)

    def documents(self, gid):
        n = sum(1 for d in self.document_pointers if gid in ((self._rule_of(d) or {}).get("access_groups") or []))
        return n, False

    def node_types(self):
        return copy.deepcopy(self.node_type_rows)

    def document_access(self, pointer):
        rule = self._rule_of(pointer)
        return {"pointer": pointer, "access_groups": list((rule or {}).get("access_groups") or []), "rule": rule}

    def readable_as(self, user, pointer):
        acl = self.document_access(pointer)["access_groups"]
        if not acl:
            return True
        tree = self.tree()
        mine = self.directory.access_groups_of(user.id)
        return any(g == target or g in tree.ancestors(target) for target in acl for g in mine)

    def posture(self):
        return dict(self.posture_state)


class MemorySecrets:
    """``platform_settings.SourceSecrets`` in memory: write-only to the builder, readable by the readers."""

    def __init__(self, available=True):
        self.values: dict[str, str] = {}
        self.available = available
        self.reason = "" if available else "PLATFORM_SETTINGS_KEY ist nicht gesetzt: Geheimnisse können nicht gespeichert werden."

    def put(self, key, value, user):
        from platform_settings import NoSecretKey

        if not self.available:
            raise NoSecretKey(self.reason)
        self.values[key] = value

    def clear(self, key, user):
        self.values.pop(key, None)

    def get(self, key):
        return self.values.get(key)


class FakeConnector:
    """A KnovasConnector's /discover over a folder on disk: the files as the connector reports them."""

    def __init__(self, root):
        self.root = Path(root)
        self.calls = []

    def discover(self, root=None, max_depth=3):
        self.calls.append(root)
        base = self.root / (root or "")
        if not base.is_dir():
            from knovas_connector_client import KnovasConnectorError

            raise KnovasConnectorError("Root path is outside allowed watch roots", status=403)
        entries = []
        for path in sorted(base.rglob("*")):
            rel = path.relative_to(base).as_posix()
            if len(path.relative_to(base).parts) > max_depth + 1:
                continue
            if path.is_dir():
                entries.append({"path": rel, "name": path.name, "type": "directory"})
            else:
                entries.append({"path": rel, "name": path.name, "type": "file", "size_bytes": path.stat().st_size,
                                "modified_at": "2026-10-30T09:15:00Z"})
        return {"status": "success", "root": str(base), "truncated": False, "entries": entries}


def stand_in_readers(github_root=None, connector=None, http=None):
    """A reader factory as the app's, with stand-in transports: brain-runner reads ``github_root`` (a
    checkout) the way it reads a repository; the connector is a FakeConnector; ``http`` answers GETs."""
    from builder import sources

    def factory(component, source, secret, workspace_id):
        settings = source.get("settings") or {}
        if source["kind"] == "github":
            from brain_runner.sources.local import LocalSource

            factory.secrets.append(secret)
            return sources.GitHubReader(LocalSource(Path(github_root)))
        if source["kind"] == "knovas_connector":
            return sources.KnovasConnectorReader(connector, settings.get("folder") or "")
        return sources.HttpJsonReader(settings["url"], secret, get=http)

    factory.secrets = []
    return factory


def make_app(user=None, store=None, tenant=None, engine=None, tenant_types=None, publish_sink=None,
             secrets=None, readers=None, roles=None, roles_sink=None, sync=None, directory=None,
             component_secrets=None, adapters=None, limits=None, signer=None, require_certificate=True,
             access=None, assistant=None, graph=None, brain_url=None, brain_ca=None,
             issue_certificates=True, views_source=None, tenant_bundle=None) -> Flask:
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
        tenant_bundle=tenant_bundle,
        engine_factory=(lambda: engine) if engine is not None else None,
        publish_sink=publish_sink,
        views_source=views_source,
        source_secrets=(lambda: secrets) if secrets is not None else None,
        reader_factory=readers,
        platform_roles=(lambda: roles) if roles is not None else None,
        roles_sink=roles_sink,
        sync_status=(lambda: sync) if sync is not None else None,
        user_directory=(lambda: directory) if directory is not None else None,
        component_secrets=(lambda: component_secrets) if component_secrets is not None else None,
        component_adapter=adapters,
        component_limits=(lambda: limits) if limits is not None else None,
        issue_certificates=issue_certificates,
        access_backend=(lambda: access) if access is not None else None,
        assistant_client=(lambda: assistant) if assistant is not None else None,
        tenant_graph=(lambda: graph) if graph is not None else None,
    ))
    if component_secrets is not None and store is not None:
        from web_interface.connector_protocol import create_connector_protocol_blueprint

        app.register_blueprint(create_connector_protocol_blueprint(
            store_factory=lambda: store, secrets=lambda: component_secrets, tenant_provider=lambda: tenant,
            signer=signer or fake_signer, require_client_certificate=require_certificate,
            brain_url=(lambda: brain_url) if brain_url else None, brain_ca=(lambda: brain_ca) if brain_ca else None,
            issue_certificates=issue_certificates))
    return app


def fake_signer(csr, name):
    """The Brain's /secured/sign_certificate, standing in: a certificate whose body is derived from the CSR."""
    import base64
    import hashlib

    body = base64.b64encode(hashlib.sha256(csr.encode("utf-8")).digest() * 8).decode("ascii")
    return {"certificate": f"-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\n", "certificate_chain": None}


FAKE_CSR = "-----BEGIN CERTIFICATE REQUEST-----\nMIIBfake\n-----END CERTIFICATE REQUEST-----\n"


class ProtocolClient:
    """A component speaking connector protocol v1 to the Platform, as the KnovasConnector and the GitHub runner
    will (KN-1, KB-3.1): enrol, heartbeat, take its commands, report its runs and the plans its guard holds."""

    def __init__(self, http, base="/api/connectors/v1"):
        self.http, self.base = http, base
        self.component = self.token = self.certificate = None
        #: What the TLS terminator in front of the Platform adds: the verified certificate's fingerprint.
        self.send_fingerprint = True

    def _send(self, method, path, body=None, auth=True):
        headers = {}
        if auth:
            from builder.protocol import certificate_fingerprint

            headers = {"Authorization": f"Bearer {self.token}", "X-Component": self.component or ""}
            if self.certificate and self.send_fingerprint:
                headers["X-Client-Cert-Fingerprint"] = certificate_fingerprint(self.certificate)
        return self.http.open(self.base + path, method=method, json=body, headers=headers)

    def enrol(self, token, version="2.4.0", capabilities=("dry_run", "apply", "held_plans")):
        answer = self._send("POST", "/enrol", {"token": token, "csr": FAKE_CSR, "version": version,
                                               "capabilities": list(capabilities)}, auth=False)
        if answer.status_code == 201:
            body = answer.get_json()
            self.component, self.token, self.certificate = body["component"], body["control_token"], body["certificate"]
        return answer

    def heartbeat(self, **body):
        return self._send("POST", "/heartbeat", {"state": "online", "version": "2.4.0", **body})

    def config(self):
        return self._send("GET", "/config")

    def report_run(self, **body):
        return self._send("POST", "/runs", body)

    def done(self, command_id, result="ok"):
        return self._send("POST", f"/commands/{command_id}/done", {"result": result})

    def events(self, *events):
        return self._send("POST", "/events", {"events": list(events)})
