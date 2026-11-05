"""Credentials as an env line (connection layer MVP-24, KC-7b).

A source read through a component on a site takes no credential in the Platform: its credential field becomes the
exact line for knovas-site.env (``SYNC_CREDENTIAL_<KEY>=``) with a copy button, and a value sent anyway is refused.
A source read through a component without a site keeps the credential field it had.
"""
import datetime as dt
import json
from pathlib import Path

import pytest

from builder import editing, package
from builder_harness import (SAMPLE_PACKAGE, FakeEngine, MemorySecrets, MemoryWorkspaceStore, ProtocolClient, StandInUser,
                             make_app)

TENANT = "t-credentials"
WEB = Path(__file__).resolve().parents[1] / "src" / "web_interface"


@pytest.fixture
def world():
    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    component_secrets, source_secrets = MemorySecrets(), MemorySecrets()
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=component_secrets,
                   secrets=source_secrets)
    http = app.test_client()
    made = http.post("/builder/api/sites", json={"name": "Kanzlei Bern", "options": ["github"]}).get_json()
    package_zip = http.post(f"/builder/api/sites/{made['id']}/package", json={}).data
    import io
    import zipfile

    env = zipfile.ZipFile(io.BytesIO(package_zip)).read("knovas-site.env").decode()
    token = next(line.split("=", 1)[1] for line in env.splitlines() if line.startswith("LINK_ENROL_TOKEN="))
    link = ProtocolClient(http)
    link.enrol(token)
    link.heartbeat(site={"certificate": "present", "work_channel": "connected",
                         "probe": {"last_at": dt.datetime.now(dt.timezone.utc).isoformat(), "ok": True, "round_trip_ms": 5},
                         "components": [{"name": "sync-component", "kind": "sync_component", "version": "0.2.0",
                                         "state": "online", "capabilities": []}]})
    [child] = store.children(link.component)
    direct = store.register_component(TENANT, kind="github_runner", name="Runner", site="", owner="", settings={},
                                      actor_id=admin.id)
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    for op in ({"op": "source.create", "kind": "github", "name": "Akten-Repo", "component": child["id"],
                "settings": {"repository": "muster-partner/akten", "ref": "main"}},
               {"op": "source.create", "kind": "github", "name": "Vorlagen", "component": direct,
                "settings": {"repository": "muster-partner/vorlagen", "ref": "main"}}):
        editing.apply(model, op)
    workspace = store.create(name="Muster & Partner", model=model, actor_id=admin.id, source={"kind": "blank"},
                             tenant=TENANT)
    http.get(f"/builder/w/{workspace}")
    return {"http": http, "store": store, "secrets": source_secrets, "workspace": workspace, "child": child,
            "gate": app.extensions["builder_gate"]}


def _sources(world):
    page = world["http"].get("/builder/quellen").get_data(as_text=True)
    raw = page.split('<script type="application/json" id="b-src-data">', 1)[1].split("</script>", 1)[0]
    return {s["key"]: s for s in json.loads(raw)["sources"]}


def test_a_source_on_a_site_shows_the_line_for_the_env_file(world):
    found = _sources(world)
    on_site = found["akten-repo"]
    assert on_site["on_site"] is True and on_site["credential_line"] == "SYNC_CREDENTIAL_AKTEN_REPO="
    assert on_site["site_name"] == "Kanzlei Bern"
    assert on_site["check_url"] == f"/builder/api/components/{world['child']['id']}/check"
    assert found["vorlagen"]["on_site"] is False and "credential_line" not in found["vorlagen"]


def test_no_value_is_typed_into_the_platform_for_it(world):
    url = f"/builder/api/w/{world['workspace']}/sources/akten-repo/credential"
    refused = world["http"].post(url, json={"value": "ghp_secret"})
    assert refused.status_code == 409 and "knovas-site.env" in refused.get_json()["error"]
    assert world["secrets"].values == {}
    assert not world["store"].model(world["workspace"])["sources"][0]["credential_set"]
    # A source without a site keeps its credential field.
    assert world["http"].post(f"/builder/api/w/{world['workspace']}/sources/vorlagen/credential",
                              json={"value": "ghp_other"}).status_code == 200


def test_the_page_offers_the_line_with_a_copy_button():
    script = (WEB / "static/js/builder/sources.js").read_text(encoding="utf-8")
    assert "credential_line" in script and "Kopieren" in script and "navigator.clipboard" in script
    assert "Danach neu starten und Verbindung prüfen" in script
    assert "check_url" in script and "Verbindung prüfen" in script


def test_verbindung_pruefen_asks_the_component_through_link(world):
    answer = world["http"].post(f"/builder/api/components/{world['child']['id']}/check")
    assert answer.status_code == 200 and answer.get_json()["queued"]


def test_a_viewer_sees_the_line_but_cannot_write_a_credential(world):
    world["gate"].user = StandInUser({"builder_viewer"})
    url = f"/builder/api/w/{world['workspace']}/sources/vorlagen/credential"
    assert world["http"].post(url, json={"value": "x"}).status_code == 403
