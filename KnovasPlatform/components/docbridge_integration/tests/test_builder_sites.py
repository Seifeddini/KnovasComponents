"""Sites and the install package (connection layer MVP-23, KC-7b).

A site is one machine at a company with Knovas Link and the components ticked for it. The Builder makes the site,
its install package (docker-compose.yml and knovas-site.env) and Link's one-time enrolment token; Link enrols through
the existing /api/connectors/v1/enrol. Its heartbeat carries the object ``site`` (codes and counts only), which the
Platform keeps as telemetry on Link's row and turns into child rows for the components behind it. Link forwards its
components' reports with the component named as a sub-component (``X-Sub-Component``).
"""
import datetime as dt
import io
import zipfile

import pytest
import yaml

from builder import components as C
from builder import protocol, sites
from builder_harness import (FakeEngine, MemorySecrets, MemoryWorkspaceStore, ProtocolClient, StandInUser, make_app)

TENANT = "t-site"


@pytest.fixture
def world():
    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    secrets = MemorySecrets()
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=secrets)
    return {"http": app.test_client(), "store": store, "secrets": secrets, "gate": app.extensions["builder_gate"],
            "admin": admin}


def _site(world, name="Kanzlei Zürich", options=("github",)):
    answer = world["http"].post("/builder/api/sites", json={"name": name, "owner": "IT", "options": list(options)})
    assert answer.status_code == 201, answer.get_json()
    return answer.get_json()


def _package(world, site_id, url="https://platform.example.test/api/connectors/v1"):
    answer = world["http"].post(f"/builder/api/sites/{site_id}/package", json={"platform_url": url})
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
    assert answer.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(answer.data)) as z:
        return {n: z.read(n).decode("utf-8") for n in z.namelist()}


def _env(text):
    return dict(line.split("=", 1) for line in text.splitlines() if line and not line.startswith("#"))


def _online_link(world, options=("github",)):
    made = _site(world, options=options)
    token = _env(_package(world, made["id"])["knovas-site.env"])["LINK_ENROL_TOKEN"]
    link = ProtocolClient(world["http"])
    assert link.enrol(token, version="1.1.0", capabilities=["work_channel"]).status_code == 201
    return made, link


TELEMETRY = {
    "certificate": "present", "work_channel": "connected",
    "probe": {"last_at": None, "ok": True, "round_trip_ms": 410},
    "components": [{"name": "sync-component", "kind": "sync_component", "version": "0.2.0", "state": "online",
                    "capabilities": ["dry_run", "apply", "fetch:github", "write:github"], "local_ping_ms": 3}],
    "sources": [],
    "jobs": {"fetch": {"ok": 41, "unreachable": 2}, "write": {"ok": 1}},
}


def _telemetry(**changes):
    import copy

    out = copy.deepcopy(TELEMETRY)
    out["probe"]["last_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    out.update(changes)
    return out


class TestTheRules:
    def test_a_site_needs_a_name_and_known_options(self):
        assert sites.check_name("  Kanzlei Zürich ") == "Kanzlei Zürich"
        for bad in ("", "x" * 121):
            with pytest.raises(ValueError):
                sites.check_name(bad)
        assert sites.check_options(["share", "github", "github"]) == ["github", "share"]
        with pytest.raises(ValueError):
            sites.check_options(["crm"])

    def test_the_compose_file(self):
        compose = yaml.safe_load(sites.compose_file({"name": "Büro"}, ["github"]))
        services = compose["services"]
        assert set(services) == {"knovas-link", "sync-component"}
        # Images by digest: no registry yet, so the digest is a placeholder variable.
        assert services["knovas-link"]["image"].startswith("knovas-link@${KNOVAS_LINK_DIGEST")
        assert services["sync-component"]["image"].startswith("sync-component@${SYNC_COMPONENT_DIGEST")
        # The internal network; Link alone is also on the outbound one.
        assert compose["networks"]["knovas-site"]["internal"] is True
        assert sorted(services["knovas-link"]["networks"]) == ["knovas-outbound", "knovas-site"]
        assert services["sync-component"]["networks"] == ["knovas-site"]
        assert "ports" not in services["knovas-link"] and "ports" not in services["sync-component"]
        # The token volume: Link writes /link/<component>/; each component mounts only its own folder.
        link_mounts = services["knovas-link"]["volumes"]
        assert {"type": "volume", "source": "link-tokens", "target": "/link"} in link_mounts
        [token] = [v for v in services["sync-component"]["volumes"] if isinstance(v, dict) and v["source"] == "link-tokens"]
        assert token["volume"] == {"subpath": "sync-component"} and token["read_only"] is True and token["target"] == "/link"
        # Link's local API: service knovas-link, port 8460, on the internal network; Link's data at /data.
        assert services["sync-component"]["environment"]["SYNC_LINK_URL"] == "http://knovas-link:8460"
        assert "link-data:/data" in link_mounts and services["knovas-link"]["env_file"] == ["knovas-site.env"]
        # No share is mounted unless Dateiablage is ticked.
        assert not any(isinstance(v, dict) and v.get("target") == "/sources" for v in services["sync-component"]["volumes"])

    def test_a_ticked_share_is_mounted_read_only_from_a_placeholder(self):
        compose = yaml.safe_load(sites.compose_file({"name": "Büro"}, ["share"]))
        [share] = [v for v in compose["services"]["sync-component"]["volumes"] if isinstance(v, dict)
                   and v.get("target") == "/sources"]
        assert share["read_only"] is True and share["type"] == "bind" and "SHARE" in share["source"]

    def test_a_site_with_link_only(self):
        compose = yaml.safe_load(sites.compose_file({"name": "Büro"}, []))
        assert set(compose["services"]) == {"knovas-link"}

    def test_the_env_file_holds_the_platform_address_and_the_token_only(self):
        text = sites.env_file("https://p.example.test/api/connectors/v1", "kce_abc")
        assert _env(text) == {"LINK_PLATFORM_URL": "https://p.example.test/api/connectors/v1", "LINK_ENROL_TOKEN": "kce_abc"}

    def test_the_credential_line_names_the_variable_the_sync_component_reads(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sync_component" / "src"))
        from sync_component.fetchers import credential_for

        line = sites.credential_line("akten-repo")
        assert line == "SYNC_CREDENTIAL_AKTEN_REPO="
        assert credential_for("akten-repo", "github", {line[:-1]: "t0ken"}) == "t0ken"


class TestASite:
    def test_creating_a_site_makes_a_pending_knovas_link(self, world):
        made = _site(world)
        [link] = [c for c in world["store"].components(TENANT, every=True) if c["kind"] == "knovas_link"]
        assert link["state"] == "pending" and link["site_id"] == made["id"] and link["transport"] == "v1"
        assert world["store"].site(TENANT, made["id"])["options"] == ["github"]
        assert world["http"].post("/builder/api/sites", json={"name": "Kanzlei Zürich", "options": []}).status_code == 409

    def test_the_package_carries_a_token_link_enrols_with(self, world):
        made = _site(world)
        files = _package(world, made["id"])
        assert set(files) == {"docker-compose.yml", "knovas-site.env"}
        env = _env(files["knovas-site.env"])
        assert set(env) == {"LINK_PLATFORM_URL", "LINK_ENROL_TOKEN"}
        assert env["LINK_PLATFORM_URL"] == "https://platform.example.test/api/connectors/v1"
        # A second download replaces the token: the first package no longer enrols.
        second = _env(_package(world, made["id"])["knovas-site.env"])["LINK_ENROL_TOKEN"]
        assert second != env["LINK_ENROL_TOKEN"]
        assert ProtocolClient(world["http"]).enrol(env["LINK_ENROL_TOKEN"]).status_code == 401
        link = ProtocolClient(world["http"])
        assert link.enrol(second, version="1.1.0").status_code == 201
        stored = world["store"].component(TENANT, link.component)
        assert stored["kind"] == "knovas_link" and stored["state"] == "enrolled"
        # Downloaded again once Link runs: the files, without a token, and Link keeps running.
        again = _package(world, made["id"])
        assert _env(again["knovas-site.env"])["LINK_ENROL_TOKEN"] == ""
        assert link.heartbeat().status_code == 200

    def test_renewing_the_token(self, world):
        made, link = _online_link(world)
        assert world["http"].post(f"/builder/api/sites/{made['id']}/rotate").status_code == 200
        assert link.heartbeat().status_code == 401
        token = _env(_package(world, made["id"])["knovas-site.env"])["LINK_ENROL_TOKEN"]
        assert ProtocolClient(world["http"]).enrol(token).status_code == 201

    def test_the_site_page(self, world):
        made = _site(world)
        page = world["http"].get(made["url"]).get_data(as_text=True)
        assert "docker compose --env-file knovas-site.env up -d" in page
        assert "wie in DEC-0056 entschieden; bis dahin wartet Knovas Link (waiting_for_certificate)" in page
        assert "Installationspaket herunterladen" in page and "Token erneuern" in page
        assert "Platzhalter" in page and "Registry" in page
        for line in ("Paket installiert", "Zertifikat", "Knovas Link online", "GitHub-Komponente gemeldet",
                     "Arbeitskanal geprüft"):
            assert line in page
        assert 'data-check="installed" data-ok="false"' in page

    def test_the_checklist_ticks_off_as_the_site_comes_up(self, world):
        made, link = _online_link(world)
        assert link.heartbeat(site=_telemetry()).status_code == 200
        page = world["http"].get(made["url"]).get_data(as_text=True)
        for check in ("installed", "certificate", "link_online", "component:sync_component", "work_channel"):
            assert f'data-check="{check}" data-ok="true"' in page, check
        assert link.heartbeat(site=_telemetry(certificate="waiting")).status_code == 200
        page = world["http"].get(made["url"]).get_data(as_text=True)
        assert 'data-check="certificate" data-ok="false"' in page and "waiting_for_certificate" in page

    def test_the_components_page_lists_sites(self, world):
        made = _site(world)
        page = world["http"].get("/builder/verbinden").get_data(as_text=True)
        assert "Standort anlegen" in page and "Kanzlei Zürich" in page and made["url"] in page
        # Link is made with its site, not in the component wizard.
        assert 'value="knovas_link"' not in page
        refused = world["http"].post("/builder/api/components/enrol", json={"kind": "knovas_link", "name": "L"})
        assert refused.status_code == 400

    @pytest.mark.parametrize("roles", [{"builder_author"}, {"builder_publisher"}, {"builder_viewer"}])
    def test_sites_and_packages_need_manage(self, world, roles):
        made = _site(world)
        world["gate"].user = StandInUser(roles)
        assert world["http"].post("/builder/api/sites", json={"name": "B", "options": []}).status_code == 403
        assert world["http"].post(f"/builder/api/sites/{made['id']}/package", json={}).status_code == 403
        assert world["http"].post(f"/builder/api/sites/{made['id']}/rotate").status_code == 403
        assert world["http"].post(f"/builder/api/sites/{made['id']}/probe").status_code == 403
        assert world["http"].get(made["url"]).status_code == 403

    def test_link_is_not_deleted_while_a_source_reads_through_its_component(self, world):
        from builder import editing, package
        from builder_harness import SAMPLE_PACKAGE

        made, link = _online_link(world)
        link.heartbeat(site=_telemetry())
        [child] = world["store"].children(link.component)
        model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
        editing.apply(model, {"op": "source.create", "kind": "github", "name": "Akten", "component": child["id"],
                              "settings": {"repository": "muster-partner/akten"}})
        world["store"].create(name="Muster & Partner", model=model, actor_id=world["admin"].id, source={"kind": "blank"},
                              tenant=TENANT)
        assert world["http"].post(f"/builder/api/components/{link.component}/revoke").status_code == 200
        refused = world["http"].post(f"/builder/api/components/{link.component}/delete")
        assert refused.status_code == 409 and "Akten" in refused.get_json()["error"]

    def test_a_probe_on_request_goes_to_link(self, world):
        made, link = _online_link(world)
        assert world["http"].post(f"/builder/api/sites/{made['id']}/probe").status_code == 200
        [command] = link.heartbeat().get_json()["commands"]
        assert command["command"] == "probe"


class TestTheHeartbeat:
    def test_the_site_object_is_kept_and_its_components_become_children(self, world):
        made, link = _online_link(world)
        answer = link.heartbeat(site=_telemetry())
        assert answer.status_code == 200, answer.get_json()
        store = world["store"]
        row = store.component(TENANT, link.component)
        assert row["telemetry"]["certificate"] == "present" and row["telemetry"]["work_channel"] == "connected"
        assert row["telemetry"]["probe"]["ok"] is True and row["telemetry"]["probe"]["round_trip_ms"] == 410
        assert row["telemetry"]["jobs"] == {"fetch": {"ok": 41, "unreachable": 2}, "write": {"ok": 1}}
        [child] = store.children(link.component)
        assert child["parent_id"] == link.component and child["transport"] == "link" and child["site_id"] == made["id"]
        assert child["kind"] == "sync_component" and child["local_name"] == "sync-component"
        assert child["version"] == "0.2.0" and "write:github" in child["capabilities"]
        assert child["state"] == "enrolled" and child["telemetry"]["local_ping_ms"] == 3
        # A second beat updates the same child.
        link.heartbeat(site=_telemetry(components=[{**TELEMETRY["components"][0], "version": "0.2.1"}]))
        [child] = store.children(link.component)
        assert child["version"] == "0.2.1"
        # A child is a component a source can be read through.
        assert child["id"] in {c["id"] for c in store.components(TENANT)}

    @pytest.mark.parametrize("bad", [
        {"certificate": "maybe"},
        {"work_channel": "Verbindung zu Müller AG"},
        {"probe": {"last_at": "gestern", "ok": True, "round_trip_ms": 3}},
        {"probe": {"last_at": None, "ok": "ja", "round_trip_ms": 3}},
        {"components": [{"name": "Mandat Müller.pdf", "kind": "sync_component", "version": "1", "state": "online",
                         "capabilities": []}]},
        {"components": [{"name": "sync", "kind": "sync_component", "version": "1", "state": "online",
                         "capabilities": ["lesen von /Mandate/Müller"]}]},
        {"components": [{"name": "sync", "kind": "sync_component", "version": "eine Version", "state": "online",
                         "capabilities": []}]},
        {"components": [{"name": "sync", "kind": "sync_component", "version": "1", "state": "läuft", "capabilities": []}]},
        {"sources": [{"workspace": "w", "key": "Ordner /Mandate/Müller", "last_read_ok_at": None, "last_run_status": "ok"}]},
        {"sources": [{"workspace": "w", "key": "kb", "last_read_ok_at": None, "last_run_status": "Datei fehlt"}]},
        {"jobs": {"fetch": {"Mandat Müller.pdf": 1}}},
        {"jobs": {"fetch": {"ok": "viele"}}},
    ])
    def test_names_and_free_text_are_refused(self, world, bad):
        _, link = _online_link(world)
        answer = link.heartbeat(site=_telemetry(**bad))
        assert answer.status_code == 400 and answer.get_json()["error"] == "bad_field"
        assert world["store"].children(link.component) == []

    def test_a_field_this_platform_does_not_know_is_dropped_not_stored(self, world):
        _, link = _online_link(world)
        assert link.heartbeat(site=_telemetry(note="Mandat Müller AG")).status_code == 200
        assert "note" not in world["store"].component(TENANT, link.component)["telemetry"]

    def test_an_unknown_component_kind_is_skipped_not_stored(self, world):
        _, link = _online_link(world)
        future = {"name": "crm-connector", "kind": "crm_connector", "version": "0.1.0", "state": "online", "capabilities": []}
        assert link.heartbeat(site=_telemetry(components=[future])).status_code == 200
        assert world["store"].children(link.component) == []
        assert any(e["code"] == "site.component_unknown_kind" for e in world["store"].events(link.component))

    def test_only_knovas_link_reports_a_site(self, world):
        made = world["http"].post("/builder/api/components/enrol", json={"kind": "sync_component", "name": "S"}).get_json()
        direct = ProtocolClient(world["http"])
        direct.enrol(made["token"])
        answer = direct.heartbeat(site=_telemetry())
        assert answer.status_code == 400 and answer.get_json()["error"] == "bad_field"
        assert direct.heartbeat().status_code == 200            # direct mode unchanged

    def test_a_child_s_command_is_delivered_through_link(self, world):
        _, link = _online_link(world)
        link.heartbeat(site=_telemetry())
        [child] = world["store"].children(link.component)
        assert world["http"].post(f"/builder/api/components/{child['id']}/check").status_code == 200
        [command] = link.heartbeat().get_json()["commands"]
        assert command["command"] == "check" and command["component"] == "sync-component"

    def test_link_s_own_state_needs_a_probe(self, world):
        now = dt.datetime.now(dt.timezone.utc)
        base = {"kind": "knovas_link", "transport": "v1", "state": "enrolled", "last_heartbeat_at": now.isoformat(),
                "enrolled_at": (now - dt.timedelta(hours=1)).isoformat()}
        fresh = {"probe": {"last_at": (now - dt.timedelta(minutes=3)).isoformat(), "ok": True}}
        stale = {"probe": {"last_at": (now - dt.timedelta(minutes=12)).isoformat(), "ok": True}}
        failed = {"probe": {"last_at": now.isoformat(), "ok": False}}
        assert C.state_of({**base, "telemetry": fresh}, now, C.LIMITS) == "online"
        assert C.state_of({**base, "telemetry": stale}, now, C.LIMITS) == "degraded"
        assert C.state_of({**base, "telemetry": failed}, now, C.LIMITS) == "degraded"
        assert C.state_of({**base, "telemetry": {}}, now, C.LIMITS) == "degraded"
        gone = {**base, "last_heartbeat_at": (now - dt.timedelta(minutes=11)).isoformat(), "telemetry": stale}
        assert C.state_of(gone, now, C.LIMITS) == "offline"
        # Other kinds keep the heartbeat and offline_after_minutes 30.
        other = {"kind": "sync_component", "transport": "v1", "state": "enrolled",
                 "last_heartbeat_at": (now - dt.timedelta(minutes=20)).isoformat()}
        assert C.state_of(other, now, C.LIMITS) == "online"


class TestSubComponents:
    def _child(self, world):
        made, link = _online_link(world)
        link.heartbeat(site=_telemetry())
        [child] = world["store"].children(link.component)
        return made, link, child

    def _as(self, link, name):
        """Link's client with the header that names the component a report is from."""
        original = link._send

        def send(method, path, body=None, auth=True):
            answer = link.http.open(link.base + path, method=method, json=body, headers={
                "Authorization": f"Bearer {link.token}", "X-Component": link.component,
                "X-Client-Cert-Fingerprint": protocol.certificate_fingerprint(link.certificate),
                "X-Sub-Component": name})
            return answer

        link._send = send
        return original

    def test_runs_events_and_checks_land_on_the_child(self, world):
        _, link, child = self._child(world)
        self._as(link, "sync-component")
        assert link.report_run(kind="dry_run", status="ok", added=2, changed=0, retired=0, errors=0).status_code == 201
        assert link.events({"code": "scan.finished", "counts": {"files": 3}}).status_code == 201
        store = world["store"]
        assert [r["kind"] for r in store.component_runs(child["id"])] == ["dry_run"]
        assert store.component_runs(link.component) == []
        assert any(e["code"] == "scan.finished" for e in store.events(child["id"]))
        assert link.config().get_json()["component"] == child["id"]

    def test_a_field_names_the_sub_component_too(self, world):
        _, link, child = self._child(world)
        assert link.report_run(kind="apply", status="ok", sub_component="sync-component").status_code == 201
        assert [r["kind"] for r in world["store"].component_runs(child["id"])] == ["apply"]

    def test_a_child_s_command_is_done_as_the_child_s(self, world):
        _, link, child = self._child(world)
        assert world["http"].post(f"/builder/api/components/{child['id']}/check").status_code == 200
        [command] = link.heartbeat().get_json()["commands"]
        assert command["component"] == "sync-component"
        # Without naming the component, Link cannot finish it: the command is the child's, not Link's.
        assert link.done(command["id"]).status_code == 404
        self._as(link, "sync-component")
        assert link.done(command["id"]).status_code == 200
        [done] = world["store"].commands(child["id"])
        assert done["result"] == "ok" and done["done_at"]

    def test_link_s_configuration_names_the_component_serving_each_source(self, world):
        from builder import editing, package
        from builder_harness import SAMPLE_PACKAGE

        _, link, child = self._child(world)
        model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
        editing.apply(model, {"op": "source.create", "kind": "github", "name": "Akten", "component": child["id"],
                              "settings": {"repository": "muster-partner/akten", "ref": "main"}})
        world["store"].create(name="Muster & Partner", model=model, actor_id=world["admin"].id, source={"kind": "blank"},
                              tenant=TENANT)
        [source] = link.config().get_json()["sources"]
        assert source["component"] == "sync-component" and source["key"] == "akten"
        assert source["fetch"] is False and source["write"] is False
        assert [w["workspace"] for w in link._send("GET", "/package").get_json()["workspaces"]] == [source["workspace"]]
        # Asked for one component, the configuration is that component's.
        self._as(link, "sync-component")
        [own] = link.config().get_json()["sources"]
        assert own["key"] == "akten" and "component" not in own

    def test_an_unknown_sub_component_is_refused(self, world):
        _, link, _ = self._child(world)
        self._as(link, "jira-connector")
        answer = link.report_run(kind="dry_run", status="ok")
        assert answer.status_code == 409 and answer.get_json()["error"] == "unknown_sub_component"

    def test_a_direct_component_names_no_sub_component(self, world):
        made = world["http"].post("/builder/api/components/enrol", json={"kind": "sync_component", "name": "S"}).get_json()
        direct = ProtocolClient(world["http"])
        direct.enrol(made["token"])
        answer = direct.report_run(kind="dry_run", status="ok", sub_component="sync-component")
        assert answer.status_code == 400
        assert direct.report_run(kind="dry_run", status="ok").status_code == 201


def test_the_migration_and_the_store(platform_db):
    """0022 on PostgreSQL: the site, Link's kind, the children and their telemetry through the real store."""
    from builder.store import WorkspaceStore

    store = WorkspaceStore(platform_db)
    site_id = store.create_site(TENANT, name="Büro", owner="IT", options=["github"], actor_id=None)
    assert store.create_site(TENANT, name="Büro", owner="", options=[], actor_id=None) is None
    assert store.site(TENANT, site_id)["options"] == ["github"] and [s["id"] for s in store.sites(TENANT)] == [site_id]
    link_id = store.create_component(TENANT, kind="knovas_link", name="Knovas Link · Büro", site="Büro", owner="IT",
                                     actor_id=None, enrol_expires_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                                     site_id=site_id)
    store.update_component(link_id, state="enrolled", telemetry={"certificate": "waiting"})
    child_id = store.upsert_child(TENANT, parent_id=link_id, site_id=site_id, local_name="sync-component",
                                  kind="sync_component", name="Knovas Link · Büro · sync-component")
    assert store.upsert_child(TENANT, parent_id=link_id, site_id=site_id, local_name="sync-component",
                              kind="sync_component", name="Knovas Link · Büro · sync-component") == child_id
    store.update_component(child_id, version="0.2.0", telemetry={"local_ping_ms": 3})
    [child] = store.children(link_id)
    assert child["transport"] == "link" and child["parent_id"] == link_id and child["telemetry"] == {"local_ping_ms": 3}
    assert store.component(TENANT, link_id)["telemetry"] == {"certificate": "waiting"}
    assert store.component(TENANT, link_id)["site_id"] == site_id
    platform_db.execute("INSERT INTO builder_component_command (component, command) VALUES (%s, 'probe')", (link_id,))
    with pytest.raises(Exception):
        platform_db.execute("UPDATE builder_component SET transport = 'carrier pigeon' WHERE id = %s", (child_id,))
