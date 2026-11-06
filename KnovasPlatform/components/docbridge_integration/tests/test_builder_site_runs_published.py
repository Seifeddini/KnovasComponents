"""A site runs the published version only (BB-07, finding B2; FEAT-2026-11-05-knovas-brain-v1, S-5).

The pre-review reproduced it: an author's draft edit of an ``http_json`` source's address reached Knovas Link's
``GET /config`` at once, so a site's credential (``SYNC_CREDENTIAL_<KEY>``) would go to a host no reviewer saw; a
mapping edited in a draft reached ``GET /package``; a publisher could apply such a draft on a component without any
approval; and any author -- a consultant with a grant on one workspace included -- could bind a source to any of the
tenant's site components. Here:

- configuration and package come from the newest published version only; a draft reaches a site only by being
  reviewed, approved and published;
- binding a source to a component at a site (by an edit, an undo or redo, or an import) needs the permission that
  manages the tenant's sites («Zugänge verwalten»); a grant on one workspace never gives it.
"""
import datetime as dt
import io
import json
import zipfile

import pytest
import yaml

from builder import editing, package
from builder_harness import (SAMPLE_PACKAGE, FakeEngine, MemorySecrets, MemoryWorkspaceStore, ProtocolClient, StandInUser,
                             make_app, publish_as_reviewed)

TENANT = "t-published-only"
PUBLISHED_URL = "https://api.muster-partner.example/kunden"


def _child(http, store, site="Kanzlei Basel"):
    """A sync component behind Knovas Link, as the site reports it."""
    made = http.post("/builder/api/sites", json={"name": site, "options": ["github"]}).get_json()
    env = zipfile.ZipFile(io.BytesIO(http.post(f"/builder/api/sites/{made['id']}/package", json={}).data)) \
        .read("knovas-link.env").decode()
    link = ProtocolClient(http)
    link.enrol(next(line.split("=", 1)[1] for line in env.splitlines() if line.startswith("LINK_ENROL_TOKEN=")))
    link.heartbeat(site={"certificate": "present", "work_channel": "connected",
                         "probe": {"last_at": dt.datetime.now(dt.timezone.utc).isoformat(), "ok": True},
                         "components": [{"name": "sync-component", "kind": "sync_component", "version": "0.2.0",
                                         "state": "online", "capabilities": ["dry_run", "apply"]}]})
    return store.children(link.component)[0], link


@pytest.fixture
def world():
    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=MemorySecrets(),
                   secrets=MemorySecrets())
    http = app.test_client()
    child, link = _child(http, store)
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    for op in ({"op": "source.create", "kind": "http_json", "name": "Kunden", "component": child["id"],
                "settings": {"url": PUBLISHED_URL}},
               {"op": "mapping.create", "id": "kunden-mandate", "source": "kunden", "collection": "$.kunden",
                "type": "Mandate"},
               {"op": "mapping.settings", "id": "kunden-mandate", "fields": {"description": "Wie veröffentlicht"}}):
        editing.apply(model, op)
    workspace = store.create(name="Muster & Partner", model=model, actor_id=admin.id, source={"kind": "blank"},
                             tenant=TENANT)
    publish_as_reviewed(store, workspace, admin.id)
    http.get(f"/builder/w/{workspace}")
    return {"http": http, "store": store, "admin": admin, "workspace": workspace, "child": child, "link": link,
            "gate": app.extensions["builder_gate"]}


def _edit(world, **op):
    return world["http"].post(f"/builder/api/w/{world['workspace']}/ontology?view=sources", json=op)


def _package_files(world):
    [found] = world["link"]._send("GET", "/package").get_json()["workspaces"]
    import base64

    return {path: base64.b64decode(data).decode("utf-8") for path, data in found["files"].items()}


class TestTheReproduction:
    def test_a_draft_address_does_not_reach_link_s_config(self, world):
        """The pre-review's steps: before, Link's /config answered the attacker's address at once."""
        answer = _edit(world, op="source.update", key="kunden",
                       fields={"settings": {"url": "https://attacker.example/collect"}})
        assert answer.status_code == 200, answer.get_json()
        assert world["store"].draft(world["workspace"]) is not None, "the workspace now has an unpublished draft"
        [served] = world["link"].config().get_json()["sources"]
        assert served["settings"] == {"url": PUBLISHED_URL}, "a draft is not what the site runs"
        assert "attacker.example" not in json.dumps(world["link"].config().get_json())

    def test_it_reaches_the_site_once_published(self, world):
        _edit(world, op="source.update", key="kunden", fields={"settings": {"url": "https://neu.muster-partner.example/k"}})
        publish_as_reviewed(world["store"], world["workspace"], world["admin"].id)
        [served] = world["link"].config().get_json()["sources"]
        assert served["settings"] == {"url": "https://neu.muster-partner.example/k"}

    def test_a_draft_mapping_does_not_reach_link_s_package(self, world):
        answer = _edit(world, op="mapping.settings", id="kunden-mandate", fields={"description": "Nur im Entwurf"})
        assert answer.status_code == 200, answer.get_json()
        [mapping] = [text for path, text in _package_files(world).items() if "kunden-mandate" in path]
        assert yaml.safe_load(mapping)["description"] == "Wie veröffentlicht"
        publish_as_reviewed(world["store"], world["workspace"], world["admin"].id)
        [mapping] = [text for path, text in _package_files(world).items() if "kunden-mandate" in path]
        assert yaml.safe_load(mapping)["description"] == "Nur im Entwurf"

    def test_a_source_bound_in_a_draft_only_is_not_served(self, world):
        answer = _edit(world, op="source.create", kind="http_json", name="Neu", component=world["child"]["id"],
                       settings={"url": "https://neu.example/k"})
        assert answer.status_code == 200, answer.get_json()
        assert [s["key"] for s in world["link"].config().get_json()["sources"]] == ["kunden"]

    def test_an_apply_on_the_component_runs_the_published_version(self, world):
        """An apply needs no approval of its own: what it applies is the published version, which had one."""
        _edit(world, op="source.update", key="kunden", fields={"settings": {"url": "https://attacker.example/collect"}})
        world["store"].add_run(world["child"]["id"], kind="dry_run", status="ok", started_at=None, finished_at=None,
                               duration_ms=1, added=0, changed=0, retired=0, errors=0)
        assert world["http"].post(f"/builder/api/components/{world['child']['id']}/run",
                                  json={"kind": "apply"}).status_code == 200
        assert [s["settings"]["url"] for s in world["link"].config().get_json()["sources"]] == [PUBLISHED_URL]
        assert "attacker.example" not in json.dumps(world["link"]._send("GET", "/package").get_json())


class TestWhoMayBindASourceToASite:
    def _grant(self, world, roles):
        consultant = StandInUser(frozenset(), "Bea Beraterin", "bea@example.org")
        world["store"].user_names[str(consultant.id)] = consultant.display_name
        world["store"].grant_access(world["workspace"], user_id=consultant.id, email=consultant.email, roles=roles,
                                    ends_on=(dt.date.today() + dt.timedelta(days=30)).isoformat(),
                                    actor_id=world["admin"].id)
        return consultant

    def test_a_consultant_with_a_grant_on_one_workspace_cannot(self, world):
        world["gate"].user = self._grant(world, ["builder_author"])
        before = world["store"].model(world["workspace"])
        refused = _edit(world, op="source.create", kind="http_json", name="Fremd", component=world["child"]["id"],
                        settings={"url": "https://fremd.example/k"})
        assert refused.status_code == 403 and "Standort" in refused.get_json()["error"]
        assert world["store"].model(world["workspace"]) == before, "nothing written"
        moved = _edit(world, op="source.update", key="kunden", fields={"component": world["child"]["id"]})
        assert moved.status_code == 200, "a source already bound there may be edited (it reaches the site published)"

    def test_a_tenant_wide_author_cannot_either(self, world):
        world["gate"].user = StandInUser({"builder_author"}, "Theo Autor")
        refused = _edit(world, op="source.create", kind="http_json", name="Fremd", component=world["child"]["id"],
                        settings={"url": "https://fremd.example/k"})
        assert refused.status_code == 403

    def test_moving_a_source_onto_a_site_component_is_binding_it(self, world):
        other, _ = _child(world["http"], world["store"], site="Kanzlei Luzern")
        world["gate"].user = StandInUser({"builder_author"}, "Theo Autor")
        refused = _edit(world, op="source.update", key="kunden", fields={"component": other["id"]})
        assert refused.status_code == 403

    def test_who_manages_the_sites_can(self, world):
        answer = _edit(world, op="source.create", kind="http_json", name="Neu", component=world["child"]["id"],
                       settings={"url": "https://neu.example/k"})
        assert answer.status_code == 200, answer.get_json()

    def test_a_component_the_platform_runs_needs_no_more_than_editing(self, world):
        made = world["http"].post("/builder/api/components", json={"kind": "http_json", "name": "Leser"}).get_json()
        world["gate"].user = StandInUser({"builder_author"}, "Theo Autor")
        answer = _edit(world, op="source.create", kind="http_json", name="Hier", component=made["id"],
                       settings={"url": "https://hier.example/k"})
        assert answer.status_code == 200, answer.get_json()

    def test_redo_cannot_bind_it_for_someone_who_may_not(self, world):
        http = world["http"]
        assert _edit(world, op="source.create", kind="http_json", name="Neu", component=world["child"]["id"],
                     settings={"url": "https://neu.example/k"}).status_code == 200
        assert http.post(f"/builder/api/w/{world['workspace']}/undo").status_code == 200
        world["gate"].user = StandInUser({"builder_author"}, "Theo Autor")
        with http.session_transaction() as s:
            history = s.get("builder_history")
        assert history, "the session's redo stack holds the binding"
        refused = http.post(f"/builder/api/w/{world['workspace']}/redo")
        assert refused.status_code == 403
        assert not any(s["key"] == "neu" for s in world["store"].model(world["workspace"])["sources"])

    def test_an_import_that_binds_a_source_to_a_site_needs_it_too(self, world):
        files = package.write_package(world["store"].model(world["workspace"]))
        data = package.zip_files(files)
        world["gate"].user = StandInUser({"builder_author"}, "Theo Autor")
        refused = world["http"].post("/builder/api/import", data={"name": "Kopie", "package": (io.BytesIO(data), "p.zip")},
                                     content_type="multipart/form-data")
        assert refused.status_code == 403 and refused.get_json()["details"] == ["kunden"]
        assert len(world["store"].list()) == 1, "no workspace made"
        world["gate"].user = world["admin"]
        made = world["http"].post("/builder/api/import", data={"name": "Kopie", "package": (io.BytesIO(data), "p.zip")},
                                  content_type="multipart/form-data")
        assert made.status_code == 201, made.get_json()
