"""A source of a kind the Builder has no form for (SPEC-write-back-13: a new source kind needs no Platform change;
FEAT-2026-11-10-write-back C-8; BET-2026-11-write-back WB-12, WB-07).

With BUILDER_PROTOCOL_V2 on, a kind a connector at a site declared (here ``jira``, as the Jira connector declares it)
and the Builder's catalogue does not know gets one generic form: the source's key, its kind, its component at the site,
and its settings as names and values (text, number, yes/no, list of texts). Anything that looks like a credential is
refused; the credential belongs at the site, in the connector's own file (knovas-jira.env, never knovas-sources.env,
which the sync component reads: verifier KC-3 N5). Publishing carries the settings to the site exactly
as for a known kind, and the Jira connector reads them. The read and write switches and the staging follow the
declaration as for a known kind. Known kinds keep their forms; with the switch off nothing of this is offered.
The company, its site and its Jira project are made up.
"""
import datetime as dt
import importlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

from builder import package
from builder import sources as S
from builder_harness import (SAMPLE_PACKAGE, FakeEngine, MemorySecrets, MemoryWorkspaceStore, ProtocolClient, StandInUser,
                             make_app, publish_as_reviewed)

TENANT = "t-generic-kind"
READ = ["replay_changes_nothing", "deletion_guard", "no_credential_leaves_site"]
WRITE = ["preview_equals_receipt", "exactly_once", "permit_refusals"]
SETTINGS = {"site": "https://muster-partner.atlassian.net", "project": "SS", "fields": ["summary", "status", "duedate"],
            "computed_fields": ["status"], "page_size": 50, "include_archived": False}


def jira_declaration(kind="jira", passed=READ + WRITE, fetch=False):
    """What the Jira connector declares (KnovasConnectorJira, connector.py), with a made-up package digest."""
    def action(name, params, grouping):
        return {"name": name, "target_kind": "issue", "params": params, "preview": "exact", "staging": ["direct"],
                "grouping": grouping, "inverse": None}
    return {"protocol": "2.0", "connector": {"name": "jira", "version": "0.1.0", "package_sha256": "0" * 64},
            "source_kind": kind, "streams": ["record", "tombstone", "generation", "state"], "fetch": fetch,
            "actions": [action("set_assignee", [{"name": "assignee", "kind": "reference", "required": True}], "item"),
                        action("transition_issue", [{"name": "status", "kind": "string", "required": True}], "none"),
                        action("set_due_date", [{"name": "due_date", "kind": "date", "required": True}], "item")],
            "conformance": {"suite": "2.0.0", "passed": list(passed), "report_sha256": "0" * 64}}


def _site(http, store, components, site="Büro Basel"):
    """Knovas Link at a site with its components, each as Link's heartbeat reports it: (name, declaration) or
    (name, declaration, component kind), the kind ``sync_component`` unless given."""
    made = http.post("/builder/api/sites", json={"name": site, "options": ["github"]}).get_json()
    env = zipfile.ZipFile(io.BytesIO(http.post(f"/builder/api/sites/{made['id']}/package", json={}).data)) \
        .read("knovas-link.env").decode()
    link = ProtocolClient(http)
    link.enrol(next(line.split("=", 1)[1] for line in env.splitlines() if line.startswith("LINK_ENROL_TOKEN=")))
    link.heartbeat(site={"certificate": "present", "work_channel": "connected",
                         "probe": {"last_at": dt.datetime.now(dt.timezone.utc).isoformat(), "ok": True},
                         "components": [{"name": name, "kind": kind[0] if kind else "sync_component", "version": "0.1.0",
                                         "state": "online", "capabilities": [],
                                         **({"declaration": d} if d is not None else {})}
                                        for name, d, *kind in components]})
    return {c["local_name"]: c for c in store.children(link.component)}, link


def _world(monkeypatch, on=True, components=None):
    if on:
        monkeypatch.setenv("BUILDER_PROTOCOL_V2", "true")
    else:
        monkeypatch.delenv("BUILDER_PROTOCOL_V2", raising=False)
    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=MemorySecrets(),
                   secrets=MemorySecrets())
    http = app.test_client()
    children, link = _site(http, store, components if components is not None else [
        ("jira", jira_declaration()),
        ("jira-untested", jira_declaration(passed=["no_credential_leaves_site"] + WRITE)),
        ("records", jira_declaration(kind="example-records")),
        ("sync-component", None)])
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    model["sources"] = []
    workspace = store.create(name="Muster & Partner", model=model, actor_id=admin.id, source={"kind": "blank"},
                             tenant=TENANT)
    http.get(f"/builder/w/{workspace}")
    return {"http": http, "store": store, "workspace": workspace, "admin": admin, "link": link, "children": children}


def _edit(world, **op):
    return world["http"].post(f"/builder/api/w/{world['workspace']}/ontology?view=sources", json=op)


def _create(world, settings=None, component="jira", **extra):
    return _edit(world, op="source.create", kind=extra.pop("kind", "jira"), name="Jira Projekte", key="jira-projekte",
                 component=world["children"][component]["id"],
                 settings=dict(SETTINGS) if settings is None else settings, **extra)


def _source(world, key="jira-projekte"):
    return next(s for s in world["store"].model(world["workspace"])["sources"] if s["key"] == key)


def _page(world):
    page = world["http"].get("/builder/quellen").get_data(as_text=True)
    raw = page.split('<script type="application/json" id="b-src-data">', 1)[1].split("</script>", 1)[0]
    return page, json.loads(raw)


# ------------------------------------------------------------------ the offer and the form

def test_a_declared_kind_without_a_form_is_offered_with_the_generic_form(monkeypatch):
    world = _world(monkeypatch)
    page, data = _page(world)
    assert data["generic_form"] is True
    jira = data["kinds"]["jira"]
    assert jira["generic"] is True and jira["settings"] == [] and jira["credential_required"] is False
    assert jira["component_ids"] == [world["children"]["jira"]["id"]], \
        "only the component that declared the kind and passed the read properties"
    assert [a["value"] for a in jira["access"]] == ["read"], "writing is switched on afterwards, as for a known kind"
    assert data["kinds"]["example-records"]["component_ids"] == [world["children"]["records"]["id"]]
    assert {t["value"] for t in data["setting_types"]} == {"text", "number", "boolean", "list"}
    assert 'value="jira" data-sn-generic' in page and "Von einem Connector am Standort angemeldet" in page


def test_a_source_of_a_declared_kind_is_created_with_its_key_kind_site_and_typed_settings(monkeypatch):
    world = _world(monkeypatch)
    made = _create(world)
    assert made.status_code == 200, made.get_json()
    source = _source(world)
    assert source["kind"] == "jira" and source["component"] == world["children"]["jira"]["id"]
    assert source["access"] == "read" and source["credential_set"] is False
    assert source["settings"] == SETTINGS, "kept as typed: a list stays a list, a number a number, no a no"
    [shown] = [s for s in made.get_json()["sources"]["sources"] if s["key"] == "jira-projekte"]
    assert shown["generic"] is True and shown["on_site"] is True and shown["site_name"]
    assert shown["credential_line"] == "SYNC_CREDENTIAL_JIRA_PROJEKTE=", "the credential's line for the site"
    assert shown["credential_file"] == "knovas-jira.env", "the connector's own file, which the sync component never reads"
    assert shown["credential_label"] == S.GENERIC_CREDENTIAL


def test_empty_values_are_left_out_and_text_is_trimmed(monkeypatch):
    world = _world(monkeypatch)
    assert _create(world, settings={"site": "  https://muster-partner.atlassian.net ", "project": "SS", "jql": "",
                                    "fields": [" summary ", ""], "labels": []}).status_code == 200
    assert _source(world)["settings"] == {"site": "https://muster-partner.atlassian.net", "project": "SS",
                                          "fields": ["summary"]}


@pytest.mark.parametrize("settings, says", [
    ({"Project": "SS"}, "Einstellung 1: der Name klein geschrieben"),
    ({"site.url": "https://x.atlassian.net"}, "Einstellung 1"),
    ({"project": {"key": "SS"}}, "«project»: Text, Zahl, Ja/Nein oder eine Liste von Texten"),
    ({"fields": ["summary", 3]}, "«fields»: eine Liste von Texten"),
    ({"project": "S" * 501}, "«project»: höchstens 500 Zeichen"),
    ({"jql": "project = SS\nORDER BY key"}, "«jql»: eine Zeile"),
    ({"page_size": 2 ** 60}, "«page_size»: eine Zahl"),
    ({f"s{i}": "x" for i in range(31)}, "höchstens 30"),
])
def test_settings_the_generic_form_does_not_take_are_refused_and_nothing_is_written(monkeypatch, settings, says):
    world = _world(monkeypatch)
    refused = _create(world, settings=settings)
    assert refused.status_code == 400 and says in refused.get_json()["error"]
    assert world["store"].model(world["workspace"])["sources"] == []


# ------------------------------------------------------------------ no credential in the Builder

@pytest.mark.parametrize("name", ["api_token", "password", "passwort", "client_secret", "private_key", "api_key",
                                  "jira_pat", "credentials", "auth", "access_key"])
def test_a_setting_named_like_a_credential_is_refused_and_said_to_belong_at_the_site(monkeypatch, name):
    world = _world(monkeypatch)
    refused = _create(world, settings={**SETTINGS, name: "irgendwas"})
    assert refused.status_code == 400
    error = refused.get_json()["error"]
    assert f"«{name}»" in error and "knovas-jira.env" in error and "SYNC_CREDENTIAL_JIRA_PROJEKTE=" in error
    assert "knovas-sources.env" not in error
    assert world["store"].model(world["workspace"])["sources"] == []


SECRETS = ["ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
           "ATATT3xFfGF0" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0Fe",
           "-----BEGIN RSA PRIVATE KEY-----MIIEpAIBAAKCAQEA",
           "Bearer abc.def.ghi",
           "Basic dXNlcjpwYXNz",
           "https://ada:geheim@muster-partner.atlassian.net",
           "https://muster-partner.atlassian.net/rest?token=abc123",
           "ada@muster-partner.example:ATT1234567890",
           "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
           "Q7fK2mZ9xLp4Rt8Wv3Yb6Nc1Hd5Jg0Se2Ua7Io9Pe4",
           "knovas_ook_" + "x" * 30]


@pytest.mark.parametrize("value", SECRETS)
def test_a_value_that_looks_like_a_credential_is_refused_without_repeating_it(monkeypatch, value):
    world = _world(monkeypatch)
    refused = _create(world, settings={**SETTINGS, "project": value})
    assert refused.status_code == 400
    error = refused.get_json()["error"]
    assert "Der Wert von «project»" in error and "knovas-jira.env" in error and "knovas-sources.env" not in error
    assert value not in json.dumps(refused.get_json()), "a refusal never repeats the value"
    assert world["store"].model(world["workspace"])["sources"] == []
    in_a_list = _create(world, settings={**SETTINGS, "fields": ["summary", value]})
    assert in_a_list.status_code == 400 and "knovas-jira.env" in in_a_list.get_json()["error"]


@pytest.mark.parametrize("name, value", [
    ("project_key", "SS"), ("key", "SS-1"), ("site", "https://muster-partner.atlassian.net"),
    ("commit", "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b"), ("board_id", "3f2504e0-4f89-11d3-9a0c-0305e82c3301"),
    ("folder", "Mandate/Laufende Mandate/2026"), ("jql", "project = SS AND statusCategory != Done ORDER BY key"),
    ("credential_line_hint", None)])
def test_names_and_values_that_are_not_credentials_are_kept(name, value):
    if value is None:
        assert S.credential_name(name)
        return
    assert not S.credential_name(name)
    assert S.generic_settings({name: value}) == {name: value}


def test_the_credential_is_never_entered_in_the_builder(monkeypatch):
    world = _world(monkeypatch)
    assert _create(world).status_code == 200
    refused = world["http"].post(f"/builder/api/w/{world['workspace']}/sources/jira-projekte/credential",
                                 json={"value": "irgendwas"})
    assert refused.status_code == 409 and "SYNC_CREDENTIAL_JIRA_PROJEKTE=" in refused.get_json()["error"]
    assert "knovas-jira.env" in refused.get_json()["error"] and "knovas-sources.env" not in refused.get_json()["error"]
    assert _source(world)["credential_set"] is False


def test_it_is_read_at_the_site_never_by_the_platform(monkeypatch):
    world = _world(monkeypatch)
    assert _create(world).status_code == 200
    tested = world["http"].post(f"/builder/api/w/{world['workspace']}/sources/jira-projekte/test", json={})
    assert tested.status_code == 502 and "Standort" in tested.get_json()["error"]


# ------------------------------------------------------------------ the component that serves it

def test_a_component_that_did_not_declare_the_kind_or_pass_the_read_properties_is_refused(monkeypatch):
    world = _world(monkeypatch)
    for component, says in (("records", "nicht angemeldet"), ("sync-component", "nicht mit Protokoll 2"),
                            ("jira-untested", "replay_changes_nothing, deletion_guard")):
        refused = _create(world, component=component)
        assert refused.status_code == 409 and says in refused.get_json()["error"], component
    assert world["store"].model(world["workspace"])["sources"] == []
    assert _create(world).status_code == 200
    moved = _edit(world, op="source.update", key="jira-projekte",
                  fields={"component": world["children"]["records"]["id"]})
    assert moved.status_code == 409 and _source(world)["component"] == world["children"]["jira"]["id"]


# ------------------------------------------------------------------ editing and publishing

def test_its_settings_are_edited_as_an_edit_of_the_draft(monkeypatch):
    world = _world(monkeypatch)
    assert _create(world).status_code == 200
    changed = _edit(world, op="source.update", key="jira-projekte",
                    fields={"settings": {**SETTINGS, "project": "KB", "computed_fields": ["status", "resolution"]}})
    assert changed.status_code == 200, changed.get_json()
    assert _source(world)["settings"]["project"] == "KB"
    assert _source(world)["settings"]["computed_fields"] == ["status", "resolution"]
    refused = _edit(world, op="source.update", key="jira-projekte", fields={"settings": {**SETTINGS, "api_token": "x"}})
    assert refused.status_code == 400 and _source(world)["settings"]["project"] == "KB"
    undone = world["http"].post(f"/builder/api/w/{world['workspace']}/undo")
    assert undone.status_code == 200 and _source(world)["settings"]["project"] == "SS"


def _jira_settings_of():
    """The Jira connector's own reading of a source's settings (KnovasConnectorJira/src, client.settings_of)."""
    pytest.importorskip("requests")
    src = Path(__file__).resolve().parents[4] / "KnovasConnectorJira" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from knovas_connector_jira.client import settings_of

    return settings_of


def test_publishing_carries_the_settings_to_the_site_and_the_jira_connector_reads_them(monkeypatch):
    world = _world(monkeypatch)
    assert _create(world).status_code == 200
    assert world["link"].config().get_json()["sources"] == [], "a draft does not reach the site"
    publish_as_reviewed(world["store"], world["workspace"], world["admin"].id)
    [served] = world["link"].config().get_json()["sources"]
    assert served["kind"] == "jira" and served["key"] == "jira-projekte" and served["component"] == "jira"
    assert served["settings"] == SETTINGS and served["write"] is False and served["fetch"] is False
    read = _jira_settings_of()(served)
    assert (read.site, read.project, read.fields, read.computed_fields, read.page_size) == (
        "https://muster-partner.atlassian.net", "SS", ("summary", "status", "duedate"), frozenset({"status"}), 50)
    # A later edit reaches the site only once published, exactly as for a known kind.
    _edit(world, op="source.update", key="jira-projekte", fields={"settings": {**SETTINGS, "project": "KB"}})
    assert world["link"].config().get_json()["sources"][0]["settings"]["project"] == "SS"
    publish_as_reviewed(world["store"], world["workspace"], world["admin"].id)
    assert world["link"].config().get_json()["sources"][0]["settings"]["project"] == "KB"


def test_an_exported_workspace_keeps_the_settings_as_typed(monkeypatch):
    world = _world(monkeypatch)
    assert _create(world).status_code == 200
    files = package.write_package(world["store"].model(world["workspace"]))
    [again] = package.read_package(files).model["sources"]
    assert again["kind"] == "jira" and again["settings"] == SETTINGS


# ------------------------------------------------------------------ switches and staging, as for a known kind

def test_the_switches_and_the_staging_follow_the_declaration(monkeypatch):
    world = _world(monkeypatch)
    assert _create(world).status_code == 200
    _, data = _page(world)
    [shown] = [s for s in data["sources"] if s["key"] == "jira-projekte"]
    assert [a["name"] for a in shown["v2_actions"]] == ["set_assignee", "transition_issue", "set_due_date"]
    assert shown["write_reason"] is None and "fetch" in shown["fetch_reason"]
    assert _edit(world, op="source.switches", key="jira-projekte", access="read_write").status_code == 200
    assert _edit(world, op="source.switches", key="jira-projekte", staging="direct").status_code == 200
    assert _edit(world, op="source.switches", key="jira-projekte", staging="native_review").status_code == 409
    refused = _edit(world, op="source.switches", key="jira-projekte", fetch=True)
    assert refused.status_code == 409 and "fetch" in refused.get_json()["error"]
    publish_as_reviewed(world["store"], world["workspace"], world["admin"].id)
    [served] = world["link"].config().get_json()["sources"]
    assert served["access"] == "read_write" and served["write"] is True and served["fetch"] is False


def test_fetching_is_offered_where_the_connector_declared_fetch(monkeypatch):
    world = _world(monkeypatch, components=[("jira", jira_declaration(fetch=True))])
    assert _create(world).status_code == 200
    assert _edit(world, op="source.switches", key="jira-projekte", fetch=True).status_code == 200
    assert _source(world)["fetch"] is True


def test_writing_is_not_offered_without_the_write_properties(monkeypatch):
    world = _world(monkeypatch, components=[("jira", jira_declaration(passed=READ))])
    assert _create(world).status_code == 200
    refused = _edit(world, op="source.switches", key="jira-projekte", access="read_write")
    assert refused.status_code == 409 and "preview_equals_receipt" in refused.get_json()["error"]


# ------------------------------------------------------------------ known kinds and the switch off

def test_a_known_kind_keeps_its_form(monkeypatch):
    world = _world(monkeypatch, components=[("sync-component", jira_declaration(kind="github"))])
    _, data = _page(world)
    github = data["kinds"]["github"]
    assert "generic" not in github and [f["name"] for f in github["settings"]] == ["repository", "ref"]
    assert "github" not in [k for k, v in data["kinds"].items() if v.get("generic")], \
        "a kind of the catalogue is never offered with the generic form, whoever declares it"
    made = _edit(world, op="source.create", kind="github", name="Akten", component=world["children"]["sync-component"]["id"],
                 settings={"repository": "muster-partner/akten", "ref": "main", "page_size": 5, "api_token": "x"})
    assert made.status_code == 200, made.get_json()
    assert _source(world, "akten")["settings"] == {"repository": "muster-partner/akten", "ref": "main"}, \
        "a known kind keeps only its own named settings, as before"
    refused = _edit(world, op="source.create", kind="github", name="Falsch",
                    component=world["children"]["sync-component"]["id"], settings={"repository": "kein-repository"})
    assert refused.status_code == 400 and refused.get_json()["error"] == "Repository: in der Form owner/name."
    [akten] = [s for s in _page(world)[1]["sources"] if s["key"] == "akten"]
    assert akten["generic"] is False


def test_with_the_switch_off_the_generic_form_is_hidden(monkeypatch):
    world = _world(monkeypatch)
    assert _create(world).status_code == 200
    monkeypatch.delenv("BUILDER_PROTOCOL_V2")
    page, data = _page(world)
    assert data["generic_form"] is False and "jira" not in data["kinds"] and "data-sn-generic" not in page
    refused = _edit(world, op="source.create", kind="jira", name="Noch eins", component=world["children"]["jira"]["id"],
                    settings=SETTINGS)
    assert refused.status_code == 400 and "Art: eine der angebotenen" in refused.get_json()["error"]
    unchanged = _edit(world, op="source.update", key="jira-projekte", fields={"settings": {**SETTINGS, "project": "KB"}})
    assert unchanged.status_code == 409 and _source(world)["settings"]["project"] == "SS"
    assert _edit(world, op="source.switches", key="jira-projekte", fetch=True).status_code == 400


# ------------------------------------------------------------------ a component of the connector's own kind (C-8, Q1)
#
# Decided by the programme's coordinator on 2026-11-10: with the switch on, the Platform keeps a component behind Knovas
# Link whose declaration the protocol's schema allows, whatever its component kind; one without such a declaration is
# dropped and counted as before, and with the switch off nothing changes.

def test_a_component_of_the_connectors_own_kind_is_kept_and_shown(monkeypatch):
    world = _world(monkeypatch, components=[("jira", jira_declaration(), "jira")])
    child = world["children"]["jira"]
    assert child["kind"] == "jira" and child["telemetry"]["declaration"]["source_kind"] == "jira"
    _, data = _page(world)
    assert data["kinds"]["jira"]["component_ids"] == [child["id"]]
    assert world["http"].get(f"/builder/komponenten/{child['id']}").status_code == 200
    site = world["http"].get(f"/builder/standorte/{child['site_id']}")
    assert site.status_code == 200 and child["name"] in site.get_data(as_text=True)
    assert not any(e["code"] == "site.component_unknown_kind" for e in world["store"].events(world["link"].component))
    assert _create(world).status_code == 200
    publish_as_reviewed(world["store"], world["workspace"], world["admin"].id)
    [served] = world["link"].config().get_json()["sources"]
    assert served["component"] == "jira" and served["settings"] == SETTINGS


def test_a_component_of_an_unknown_kind_without_a_valid_declaration_is_dropped_as_before(monkeypatch):
    world = _world(monkeypatch, components=[("plain", None, "jira"),
                                            ("refused", {**jira_declaration(), "source_kind": "Jira!"}, "jira"),
                                            ("jira", jira_declaration(), "jira")])
    assert sorted(world["children"]) == ["jira"]
    [event] = [e for e in world["store"].events(world["link"].component) if e["code"] == "site.component_unknown_kind"]
    assert event["counts"] == {"components": 2}


def test_link_s_own_kind_is_never_a_child_s_even_with_a_declaration(monkeypatch):
    world = _world(monkeypatch, components=[("fake-link", jira_declaration(), "knovas_link"),
                                            ("jira", jira_declaration(), "jira")])
    assert sorted(world["children"]) == ["jira"]


def test_with_the_switch_off_a_component_of_an_unknown_kind_is_dropped_as_before(monkeypatch):
    world = _world(monkeypatch, on=False, components=[("jira", jira_declaration(), "jira"),
                                                      ("sync-component", jira_declaration(kind="github"))])
    assert sorted(world["children"]) == ["sync-component"]
    assert any(e["code"] == "site.component_unknown_kind" for e in world["store"].events(world["link"].component))


@pytest.mark.skipif(not importlib.import_module("conftest").platform_db_reachable(),
                    reason="No PostgreSQL for the migration test (PLATFORM_DB_TEST_DSN)")
def test_the_migration_lets_only_a_child_take_its_connector_s_kind(platform_db):
    """0025 on PostgreSQL: a child behind Link may have the connector's own kind; a component enrolled on its own keeps
    the list, a kind not of a heartbeat's shape is refused, and a child never takes Link's own kind (verifier KC-3,
    note 2): each refusal is the constraint's CheckViolation, nothing else."""
    from psycopg.errors import CheckViolation

    from builder.store import WorkspaceStore

    store = WorkspaceStore(platform_db)
    site_id = store.create_site(TENANT, name="Büro Basel", owner="IT", options=["github"], actor_id=None)
    link_id = store.create_component(TENANT, kind="knovas_link", name="Knovas Link · Büro Basel", site="Büro Basel",
                                     owner="IT", actor_id=None,
                                     enrol_expires_at=dt.datetime.now(dt.timezone.utc).isoformat(), site_id=site_id)
    child_id = store.upsert_child(TENANT, parent_id=link_id, site_id=site_id, local_name="jira", kind="jira",
                                  name="Knovas Link · Büro Basel · jira")
    [child] = store.children(link_id)
    assert child["id"] == child_id and child["kind"] == "jira"
    with pytest.raises(CheckViolation, match="builder_component_kind_check"):
        store.register_component(TENANT, kind="jira", name="Für sich", site="", owner="", settings={}, actor_id=None)
    with pytest.raises(CheckViolation, match="builder_component_kind_check"):
        store.upsert_child(TENANT, parent_id=link_id, site_id=site_id, local_name="falsch", kind="Jira!",
                           name="Knovas Link · Büro Basel · falsch")
    with pytest.raises(CheckViolation, match="builder_component_kind_check"):
        store.upsert_child(TENANT, parent_id=link_id, site_id=site_id, local_name="zweiter-link", kind="knovas_link",
                           name="Knovas Link · Büro Basel · zweiter-link")
    for kind in ("sync_component", "github_runner"):      # a child of a listed kind is kept as before
        assert store.upsert_child(TENANT, parent_id=link_id, site_id=site_id, local_name=kind, kind=kind,
                                  name=f"Knovas Link · Büro Basel · {kind}")
    assert [c["kind"] for c in store.components(TENANT, every=True) if c["kind"] == "knovas_link"] == ["knovas_link"]
