"""Connector protocol 2.0 in the Builder (draft; FEAT-2026-11-10-write-back C-1; SPEC-write-back -12, -14; GI-CONN-08):
Knovas Link reports each connector's declaration in its heartbeat; the Platform checks and keeps it; with
BUILDER_PROTOCOL_V2 on, writing back is offered where the component serving the source declared actions for its kind
and passed the conformance suite's write properties, and the sources page lists the declared actions with the ways
each can write. Off by default. The company and the repository are made up.
"""
import datetime as dt
import io
import json
import zipfile

import pytest

from builder import package
from builder import protocol_v2 as V2
from builder.protocol import ProtocolError, check_site
from builder_harness import (SAMPLE_PACKAGE, FakeEngine, MemorySecrets, MemoryWorkspaceStore, ProtocolClient, StandInUser,
                             make_app)

TENANT = "t-protocol-v2"
WRITE = ["preview_equals_receipt", "exactly_once", "permit_refusals", "no_credential_leaves_site"]


def declaration(kind="github", passed=WRITE + ["review_outcome"], actions=None):
    return {"protocol": "2.0", "connector": {"name": "github", "version": "0.1.0", "package_sha256": "0" * 64},
            "source_kind": kind, "streams": [], "fetch": True,
            "actions": actions if actions is not None else [
                {"name": "set_front_matter", "target_kind": "file",
                 "params": [{"name": "mapping", "kind": "string", "required": True},
                            {"name": "status", "kind": "enum", "required": False, "values": ["offen", "erledigt"]}],
                 "preview": "exact", "staging": ["direct", "native_review"], "grouping": "none", "inverse": None}],
            "conformance": {"suite": "2.0.0", "passed": list(passed), "report_sha256": "0" * 64}}


def _child(http, store, decl, site="Kanzlei Basel"):
    made = http.post("/builder/api/sites", json={"name": site, "options": ["github"]}).get_json()
    env = zipfile.ZipFile(io.BytesIO(http.post(f"/builder/api/sites/{made['id']}/package", json={}).data)) \
        .read("knovas-link.env").decode()
    link = ProtocolClient(http)
    link.enrol(next(line.split("=", 1)[1] for line in env.splitlines() if line.startswith("LINK_ENROL_TOKEN=")))
    component = {"name": "sync-component", "kind": "sync_component", "version": "0.4.0", "state": "online",
                 "capabilities": ["dry_run", "apply"]}            # no write:github: only the declaration can offer it
    if decl is not None:
        component["declaration"] = decl
    link.heartbeat(site={"certificate": "present", "work_channel": "connected",
                         "probe": {"last_at": dt.datetime.now(dt.timezone.utc).isoformat(), "ok": True},
                         "components": [component]})
    return store.children(link.component)[0]


def _world(monkeypatch, decl, on=True):
    if on:
        monkeypatch.setenv("BUILDER_PROTOCOL_V2", "true")
    else:
        monkeypatch.delenv("BUILDER_PROTOCOL_V2", raising=False)
    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=MemorySecrets(),
                   secrets=MemorySecrets())
    http = app.test_client()
    writer = _child(http, store, decl)
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    model["sources"] = [{"key": "akten", "name": "Akten", "kind": "github", "component": writer["id"],
                         "sync_mode": "manual", "settings": {"repository": "muster-partner/akten", "ref": "main"},
                         "credential_set": False}]
    workspace = store.create(name="Muster & Partner", model=model, actor_id=admin.id, source={"kind": "blank"},
                             tenant=TENANT)
    store.publish(workspace, admin.id, store.draft(workspace)["content_hash"], {"sections": {}}, "h1")
    http.get(f"/builder/w/{workspace}")
    return {"http": http, "store": store, "workspace": workspace, "writer": writer}


def _switch(world, **op):
    return world["http"].post(f"/builder/api/w/{world['workspace']}/ontology?view=sources",
                              json={"op": "source.switches", **op})


def test_the_platform_keeps_a_checked_declaration_from_the_heartbeat():
    site = check_site({"components": [{"name": "sync-component", "kind": "sync_component", "version": "0.4.0",
                                       "state": "online", "capabilities": [], "declaration": declaration()}]})
    kept = site["components"][0]["declaration"]
    assert kept["source_kind"] == "github" and kept["actions"][0]["staging"] == ["direct", "native_review"]
    assert "connector" not in kept, "only the checked fields are kept"


@pytest.mark.parametrize("change", [
    {"source_kind": "GitHub!"},
    {"actions": [{"name": "set front matter", "params": [], "preview": "exact", "staging": ["direct"], "grouping": "none"}]},
    {"actions": [{"name": "set", "params": [], "preview": "exact", "staging": ["proposal"], "grouping": "none"}]},
    {"actions": [{"name": "set", "params": [{"name": "x", "kind": "enum", "values": ["a\nb"]}], "preview": "exact",
                  "staging": ["direct"], "grouping": "none"}]},
    {"conformance": {"passed": ["trust_me"]}},
])
def test_a_declaration_the_protocol_does_not_allow_refuses_the_heartbeat(change):
    with pytest.raises(ProtocolError):
        check_site({"components": [{"name": "sync-component", "kind": "sync_component", "version": "0.4.0",
                                    "state": "online", "capabilities": [], "declaration": {**declaration(), **change}}]})


def test_with_the_switch_on_a_declared_and_conformant_connector_is_offered_writing(monkeypatch):
    world = _world(monkeypatch, declaration())
    assert _switch(world, key="akten", access="read_write").status_code == 200


def test_with_the_switch_off_the_declaration_offers_nothing(monkeypatch):
    world = _world(monkeypatch, declaration(), on=False)
    page = world["http"].get("/builder/quellen").get_data(as_text=True)
    raw = page.split('<script type="application/json" id="b-src-data">', 1)[1].split("</script>", 1)[0]
    assert all(s["v2_actions"] == [] for s in json.loads(raw)["sources"])
    refused = _switch(world, key="akten", access="read_write")
    assert refused.status_code == 409 and "write:github" in refused.get_json()["error"]


def test_a_connector_that_did_not_pass_the_write_properties_is_not_offered_writing(monkeypatch):
    world = _world(monkeypatch, declaration(passed=["replay_changes_nothing", "no_credential_leaves_site"]))
    assert _switch(world, key="akten", access="read_write").status_code == 409
    why = V2.write_reason("github", world["store"].component(TENANT, world["writer"]["id"]))
    assert "preview_equals_receipt" in why and "exactly_once" in why


def test_a_declaration_for_another_kind_offers_nothing_for_this_one(monkeypatch):
    world = _world(monkeypatch, declaration(kind="example-records"))
    assert _switch(world, key="akten", access="read_write").status_code == 409


def test_the_sources_page_lists_the_declared_actions_and_how_each_writes(monkeypatch):
    world = _world(monkeypatch, declaration())
    page = world["http"].get("/builder/quellen").get_data(as_text=True)
    raw = page.split('<script type="application/json" id="b-src-data">', 1)[1].split("</script>", 1)[0]
    [akten] = [s for s in json.loads(raw)["sources"] if s["key"] == "akten"]
    assert akten["write_reason"] is None
    assert akten["v2_actions"] == [{"name": "set_front_matter", "staging": ["direct", "native_review"],
                                    "staging_labels": [V2.STAGING_LABELS["direct"], V2.STAGING_LABELS["native_review"]],
                                    "params": ["mapping", "status"], "approvable": True, "undo": False}]
