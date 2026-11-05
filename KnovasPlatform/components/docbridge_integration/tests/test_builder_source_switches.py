"""A source's two switches (connection layer MVP-25, KC-7b).

«Agenten dürfen Originale abrufen» (``fetch``) and «Änderungen dürfen zurückgeschrieben werden» (``access:
read_write``) are off by default, also for every source published before they existed. Writing is offered only where
the kind declares it and the component serving the source has ``write:<kind>``. Each switch is an edit of the
workspace draft: it is reviewed and published like any other, and only the published value reaches the component.
"""
import datetime as dt
import json

import pytest

from builder import editing, package
from builder import sources as S
from builder_harness import (SAMPLE_PACKAGE, FakeEngine, MemorySecrets, MemoryWorkspaceStore, ProtocolClient, StandInUser,
                             make_app)

TENANT = "t-switches"


def _child(http, store, capabilities, site="Kanzlei Basel"):
    """A sync component behind Knovas Link with ``capabilities``."""
    import io
    import zipfile

    made = http.post("/builder/api/sites", json={"name": site, "options": ["github"]}).get_json()
    env = zipfile.ZipFile(io.BytesIO(http.post(f"/builder/api/sites/{made['id']}/package", json={}).data)) \
        .read("knovas-link.env").decode()
    link = ProtocolClient(http)
    link.enrol(next(line.split("=", 1)[1] for line in env.splitlines() if line.startswith("LINK_ENROL_TOKEN=")))
    link.heartbeat(site={"certificate": "present", "work_channel": "connected",
                         "probe": {"last_at": dt.datetime.now(dt.timezone.utc).isoformat(), "ok": True},
                         "components": [{"name": "sync-component", "kind": "sync_component", "version": "0.2.0",
                                         "state": "online", "capabilities": list(capabilities)}]})
    return store.children(link.component)[0], link


@pytest.fixture
def world():
    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=MemorySecrets(),
                   secrets=MemorySecrets())
    http = app.test_client()
    writer, link = _child(http, store, ["dry_run", "apply", "fetch:github", "write:github"])
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    # A source as it was published before the switches: no fetch, no access.
    model["sources"] = [{"key": "akten", "name": "Akten", "kind": "github", "component": writer["id"], "sync_mode": "manual",
                         "settings": {"repository": "muster-partner/akten", "ref": "main"}, "credential_set": False},
                        {"key": "kunden", "name": "Kunden", "kind": "http_json", "component": writer["id"],
                         "sync_mode": "manual", "settings": {"url": "https://api.muster-partner.example/kunden"},
                         "credential_set": False}]
    workspace = store.create(name="Muster & Partner", model=model, actor_id=admin.id, source={"kind": "blank"},
                             tenant=TENANT)
    store.publish(workspace, admin.id, store.draft(workspace)["content_hash"], {"sections": {}}, "h1")
    http.get(f"/builder/w/{workspace}")
    return {"http": http, "store": store, "admin": admin, "workspace": workspace, "writer": writer, "link": link,
            "gate": app.extensions["builder_gate"]}


def _switch(world, **op):
    return world["http"].post(f"/builder/api/w/{world['workspace']}/ontology?view=sources",
                              json={"op": "source.switches", **op})


def _served(world):
    return {s["key"]: s for s in world["store"].served_by(world["writer"]["id"])["sources"]}


def _publish(world):
    store = world["store"]
    store.publish(world["workspace"], world["admin"].id, store.draft(world["workspace"])["content_hash"], {"sections": {}}, "h2")


def test_a_source_published_before_neither_fetches_nor_writes(world):
    assert S.fetch_of({"key": "alt"}) is False and S.write_of({"key": "alt"}) is False
    served = _served(world)["akten"]
    assert served["fetch"] is False and served["write"] is False
    assert world["link"].config().get_json()["sources"][0]["fetch"] is False


def test_fetch_reaches_the_component_only_once_published(world):
    answer = _switch(world, key="akten", fetch=True)
    assert answer.status_code == 200, answer.get_json()
    assert world["store"].model(world["workspace"])["sources"][0]["fetch"] is True
    assert _served(world)["akten"]["fetch"] is False                 # a draft is not what the component runs
    [change] = [c for c in world["store"].draft_changes(world["workspace"]) if c["path"] == "sources/akten"]
    assert change["before"].get("fetch") is None and change["after"]["fetch"] is True   # shown in the review
    _publish(world)
    assert _served(world)["akten"]["fetch"] is True
    [akten] = [s for s in world["link"].config().get_json()["sources"] if s["key"] == "akten"]
    assert akten["fetch"] is True and akten["write"] is False


def test_write_is_offered_where_the_kind_and_the_component_can(world):
    assert _switch(world, key="akten", access="read_write").status_code == 200
    _publish(world)
    assert _served(world)["akten"]["write"] is True and _served(world)["akten"]["access"] == "read_write"
    assert _switch(world, key="akten", access="read").status_code == 200
    _publish(world)
    assert _served(world)["akten"]["write"] is False


def test_write_is_refused_where_the_kind_cannot_be_written(world):
    refused = _switch(world, key="kunden", access="read_write")
    assert refused.status_code == 400 and "zurückgeschrieben" in refused.get_json()["error"]
    assert _switch(world, key="kunden", fetch=True).status_code == 400


def test_write_is_refused_while_the_component_lacks_write_for_the_kind(world):
    reader, _ = _child(world["http"], world["store"], ["dry_run", "apply"], site="Kanzlei Luzern")
    # A second site's component only reads; the source moves onto it in the draft.
    store = world["store"]
    store.edit(world["workspace"], world["admin"].id, lambda m: editing.apply(
        m, {"op": "source.update", "key": "akten", "fields": {"component": reader["id"]}}))
    refused = _switch(world, key="akten", access="read_write")
    assert refused.status_code == 409 and "write:github" in refused.get_json()["error"]


def test_the_switches_keep_the_old_routes_strict(world):
    # source.create and source.update still offer only what the kind's static modes allow.
    answer = world["http"].post(f"/builder/api/w/{world['workspace']}/ontology",
                                json={"op": "source.update", "key": "akten", "fields": {"access": "read_write"}})
    assert answer.status_code == 400
    assert _switch(world, key="akten", fetch="ja").status_code == 400
    assert _switch(world, key="akten", access="schreiben").status_code == 400


def test_the_sources_page_says_what_is_switched_and_what_is_published(world):
    _switch(world, key="akten", fetch=True)
    page = world["http"].get("/builder/quellen").get_data(as_text=True)
    raw = page.split('<script type="application/json" id="b-src-data">', 1)[1].split("</script>", 1)[0]
    found = {s["key"]: s for s in json.loads(raw)["sources"]}
    akten, kunden = found["akten"], found["kunden"]
    assert akten["fetch"] is True and akten["fetch_published"] is False and akten["write"] is False
    assert akten["fetch_reason"] is None and akten["write_reason"] is None
    assert kunden["fetch_reason"] and kunden["write_reason"]
    script = (__import__("pathlib").Path(__file__).resolve().parents[1]
              / "src/web_interface/static/js/builder/sources.js").read_text(encoding="utf-8")
    assert "Agenten dürfen Originale abrufen" in script and "Änderungen dürfen zurückgeschrieben werden" in script
    assert "'source.switches'" in script


def test_switching_needs_edit(world):
    world["gate"].user = StandInUser({"builder_viewer"})
    assert _switch(world, key="akten", fetch=True).status_code == 403
