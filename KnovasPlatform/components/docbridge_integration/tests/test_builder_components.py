"""Sync components (build plan step B13): enrolment, protocol v1, the registry, actions, adapters, alerts.

A component is played by builder_harness.ProtocolClient against the Platform's /api/connectors/v1, the Brain's
certificate signing by fake_signer. The adapters read stand-ins of the KnovasConnector and the GitHub sync.
test_builder_screens.py runs the wizard, the registry and the detail page in a browser.
"""
import datetime as dt

import pytest

from builder import adapters, components as C
from builder_harness import (FakeEngine, MemorySecrets, MemoryWorkspaceStore, ProtocolClient, StandInUser, make_app)

TENANT = "t-1"
NOW = dt.datetime(2026, 11, 1, 12, tzinfo=dt.timezone.utc)
LIMITS = dict(C.LIMITS)


class TestTheRules:
    def test_state_of_a_component(self):
        base = {"transport": "v1", "state": "enrolled", "last_heartbeat_at": (NOW - dt.timedelta(minutes=5)).isoformat()}
        assert C.state_of(base, NOW, LIMITS) == "online"
        assert C.state_of({**base, "open_errors": 2}, NOW, LIMITS) == "degraded"
        assert C.state_of({**base, "paused": True}, NOW, LIMITS) == "paused"
        assert C.state_of({**base, "last_heartbeat_at": (NOW - dt.timedelta(hours=2)).isoformat()}, NOW, LIMITS) == "offline"
        assert C.state_of({**base, "state": "pending"}, NOW, LIMITS) == "pending"
        assert C.state_of({**base, "state": "revoked"}, NOW, LIMITS) == "revoked"

    def test_the_alerts(self):
        old = (NOW - dt.timedelta(hours=3)).isoformat()
        comps = [{"id": "a", "name": "Ablage", "transport": "v1", "state": "enrolled", "last_heartbeat_at": old},
                 {"id": "b", "name": "Runner", "transport": "v1", "state": "enrolled", "last_heartbeat_at": NOW.isoformat(),
                  "lag_seconds": 30 * 3600}]
        runs = {"b": [{"status": "failed"}, {"status": "failed"}, {"status": "ok"}]}
        held = {"b": [{"state": "held", "mapping": "akten", "held_at": (NOW - dt.timedelta(hours=30)).isoformat()}]}
        codes = [(a["component"], a["code"]) for a in C.alerts(comps, runs, held, NOW, LIMITS)]
        assert codes == [("a", "offline"), ("b", "failed_twice"), ("b", "lag"), ("b", "held_plan")]
        assert "seit 3 Stunden" in C.alerts(comps, runs, held, NOW, LIMITS)[0]["text"]

    def test_offline_beyond_the_limit_comes_first(self):
        rows = [C.row({"id": n, "name": n, "transport": "v1", "state": "enrolled", "last_heartbeat_at": t}, [], [], NOW, LIMITS)
                for n, t in (("Alpha", NOW.isoformat()), ("Beta", (NOW - dt.timedelta(hours=5)).isoformat()))]
        assert [r["name"] for r in C.registry(rows)] == ["Beta", "Alpha"]
        assert [r["name"] for r in C.registry(rows, state="online")] == ["Alpha"]

    @pytest.mark.parametrize("raw,ok", [({"mode": "manual"}, True), ({"mode": "interval", "every_minutes": 60}, True),
                                        ({"mode": "interval", "every_minutes": 1}, False),
                                        ({"mode": "window", "every_minutes": 30, "days": ["mo", "di"], "from": "22:00", "to": "05:00"}, True),
                                        ({"mode": "window", "every_minutes": 30, "days": [], "from": "22:00", "to": "05:00"}, False),
                                        ({"mode": "cron"}, False)])
    def test_a_schedule(self, raw, ok):
        if ok:
            assert C.check_schedule(raw)["mode"] == raw["mode"]
        else:
            with pytest.raises(ValueError):
                C.check_schedule(raw)

    def test_tokens_are_kept_as_hashes(self):
        token, digest = C.new_token("kce")
        assert token.startswith("kce_") and token not in digest and C.token_matches(token, digest)
        assert not C.token_matches(token + "x", digest) and not C.token_matches("", digest)

    def test_event_counts_carry_no_names(self):
        assert C.clean_counts({"files": 3, "Mandat Müller.pdf": 1, "errors": True, "path": "/a/b"}) == {"files": 3}


@pytest.fixture
def world():
    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    secrets = MemorySecrets()
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=secrets)
    return {"http": app.test_client(), "store": store, "secrets": secrets, "gate": app.extensions["builder_gate"],
            "admin": admin}


def _enrol(world, kind="knovas_connector", name="Dateiablage Zürich"):
    answer = world["http"].post("/builder/api/components/enrol", json={"kind": kind, "name": name, "site": "Zürich",
                                                                       "owner": "IT"})
    assert answer.status_code == 201, answer.get_json()
    return answer.get_json()


class TestEnrolment:
    def test_the_token_is_shown_once_and_kept_as_a_hash(self, world):
        made = _enrol(world)
        assert made["token"].startswith("kce_")
        assert world["secrets"].values == {f"builder.components.{made['id']}.enrol": C.token_hash(made["token"])}
        page = world["http"].get(made["url"]).get_data(as_text=True)
        assert made["token"] not in page and "Wartet auf die Anmeldung" in page

    def test_a_component_enrols_and_appears_online(self, world):
        made = _enrol(world)
        component = ProtocolClient(world["http"])
        answer = component.enrol(made["token"])
        assert answer.status_code == 201
        body = answer.get_json()
        assert body["control_token"].startswith("kcc_") and "BEGIN CERTIFICATE" in body["certificate"]
        assert body["config"]["schedule"] == {"mode": "manual"}
        stored = world["store"].component(TENANT, made["id"])
        assert stored["state"] == "enrolled" and stored["certificate_fingerprint"] and stored["version"] == "2.4.0"
        keys = set(world["secrets"].values)
        assert keys == {f"builder.components.{made['id']}.control"}            # the enrolment token is used up
        assert body["control_token"] not in str(world["secrets"].values)
        assert ProtocolClient(world["http"]).enrol(made["token"]).status_code == 401  # once only
        page = world["http"].get("/builder/verbinden").get_data(as_text=True)
        assert 'data-component-state="online"' in page and "Dateiablage Zürich" in page

    def test_an_expired_token_is_refused(self, world):
        made = _enrol(world)
        world["store"].update_component(made["id"], enrol_expires_at=(dt.datetime.now(dt.timezone.utc)
                                                                       - dt.timedelta(minutes=1)).isoformat())
        answer = ProtocolClient(world["http"]).enrol(made["token"])
        assert answer.status_code == 401 and answer.get_json()["error"] == "token_expired"

    def test_the_planned_database_kind_cannot_be_enrolled(self, world):
        answer = world["http"].post("/builder/api/components/enrol", json={"kind": "database_connector", "name": "DB"})
        assert answer.status_code == 400

    def test_only_an_admin_enrols(self, world):
        world["gate"].user = StandInUser({"builder_author"})
        assert world["http"].post("/builder/api/components/enrol", json={"kind": "knovas_connector", "name": "X"}).status_code == 403


class TestTheProtocol:
    def _online(self, world):
        made = _enrol(world)
        component = ProtocolClient(world["http"])
        component.enrol(made["token"])
        return made, component

    def test_every_call_needs_the_control_token(self, world):
        made, component = self._online(world)
        assert component.heartbeat().status_code == 200
        thief = ProtocolClient(world["http"])
        thief.component, thief.token = made["id"], "kcc_guessed"
        assert thief.heartbeat().status_code == 401
        answer = world["http"].post("/api/connectors/v1/heartbeat", json={"state": "online"},
                                    headers={"Authorization": f"Bearer {component.token}", "X-Component": made["id"],
                                             "X-Client-Cert-Fingerprint": "00" * 32})
        assert answer.status_code == 401 and answer.get_json()["error"] == "bad_certificate"

    def test_commands_runs_and_a_held_plan(self, world):
        made, component = self._online(world)
        http = world["http"]
        # An author asks for a dry run; an apply waits for one.
        world["gate"].user = StandInUser({"builder_author"})
        assert http.post(f"/builder/api/components/{made['id']}/run", json={"kind": "apply"}).status_code == 403
        assert http.post(f"/builder/api/components/{made['id']}/run", json={"kind": "dry_run"}).get_json()["queued"]
        world["gate"].user = world["admin"]
        assert http.post(f"/builder/api/components/{made['id']}/run", json={"kind": "apply"}).status_code == 409
        [command] = component.heartbeat().get_json()["commands"]
        assert command["command"] == "run" and command["args"] == {"kind": "dry_run"}
        assert component.heartbeat().get_json()["commands"] == []       # delivered once
        answer = component.report_run(kind="dry_run", status="held", started_at="2026-11-01T10:00:00Z",
                                      finished_at="2026-11-01T10:00:42Z", duration_ms=42000, added=3, changed=12,
                                      retired=40, errors=0, held=[{"mapping": "ablage-akten", "retire": {"Akte": 40},
                                                                    "share": 0.31}], command=command["id"])
        assert answer.status_code == 201
        [plan] = world["store"].held_plans(made["id"])
        assert plan["mapping"] == "ablage-akten" and plan["retire"] == {"Akte": 40}
        # Approving needs a reason; then the component gets the decision.
        assert http.post(f"/builder/api/components/{made['id']}/plans/{plan['id']}", json={"approve": True}).status_code == 400
        assert http.post(f"/builder/api/components/{made['id']}/plans/{plan['id']}",
                         json={"approve": True, "reason": "Altbestand bereinigt"}).status_code == 200
        [decision] = component.heartbeat().get_json()["commands"]
        assert decision["command"] == "approve_plan" and decision["args"]["reason"] == "Altbestand bereinigt"
        assert component.done(decision["id"]).status_code == 200
        # Now an apply may run.
        assert http.post(f"/builder/api/components/{made['id']}/run", json={"kind": "apply"}).status_code == 200
        page = http.get(f"/builder/komponenten/{made['id']}").get_data(as_text=True)
        assert "ablage-akten" in page and "freigegeben von Ada Admin: Altbestand bereinigt" in page
        assert "plan.held" in page and "retire=40" in page

    def test_pause_resume_schedule_and_config(self, world):
        made, component = self._online(world)
        http = world["http"]
        assert http.post(f"/builder/api/components/{made['id']}/pause").status_code == 200
        beat = component.heartbeat().get_json()
        assert beat["paused"] and [c["command"] for c in beat["commands"]] == ["pause"]
        assert http.post(f"/builder/api/components/{made['id']}/run", json={"kind": "dry_run"}).status_code == 409
        assert http.post(f"/builder/api/components/{made['id']}/resume").status_code == 200
        schedule = {"mode": "window", "every_minutes": 30, "days": ["mo", "fr"], "from": "22:00", "to": "05:00"}
        answer = http.post(f"/builder/api/components/{made['id']}/schedule", json={"schedule": schedule})
        assert answer.get_json()["text"] == "Mo, Fr 22:00–05:00, alle 30 Minuten"
        assert component.config().get_json()["schedule"] == schedule

    def test_reports_carry_codes_and_counts_only(self, world):
        made, component = self._online(world)
        assert component.events({"code": "scan.finished", "counts": {"files": 120}}).status_code == 201
        assert component.events({"code": "Mandat Müller.pdf gelesen"}).status_code == 400
        assert component.report_run(kind="apply", status="ok", held=[{"mapping": "Ordner /Mandate/Müller"}]).status_code == 400
        assert world["store"].events(made["id"])[0] == {"at": world["store"].events(made["id"])[0]["at"],
                                                         "code": "scan.finished", "counts": {"files": 120}}

    def test_rotate_and_revoke(self, world):
        made, component = self._online(world)
        http = world["http"]
        new = http.post(f"/builder/api/components/{made['id']}/rotate").get_json()["token"]
        assert component.heartbeat().status_code == 401                 # the old control token is gone
        again = ProtocolClient(http)
        assert again.enrol(new).status_code == 201 and again.heartbeat().status_code == 200
        assert http.post(f"/builder/api/components/{made['id']}/revoke").status_code == 200
        assert again.heartbeat().status_code == 401 and world["secrets"].values == {}
        assert world["store"].component(TENANT, made["id"]) is None      # no source reads through it any more


class FakeSync:
    def __init__(self, history):
        self._history = history
        self.calls = []

    def history(self, limit=20):
        return self._history[:limit]

    def current(self):
        return {"at": dt.datetime.now(dt.timezone.utc).timestamp() - 7200}

    def probe(self, user):
        self.calls.append(("probe",))
        return "r3"

    def apply(self, user, approve_guard=None):
        self.calls.append(("apply", approve_guard))
        return "r4"


class FakeConnectorStatus:
    def __init__(self, status=None, fail=False):
        self._status, self.fail, self.calls = status or {}, fail, []

    def status(self):
        if self.fail:
            raise ConnectionError("unreachable")
        return self._status

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")


class TestAdapters:
    def test_the_github_runner_over_the_sync(self):
        sync = FakeSync([{"id": "r2", "kind": "probe", "status": "ok", "finished_at": NOW.isoformat(), "duration_ms": 9000,
                          "counts": {"golden-invariants": {"add": 0, "change": 2, "retire": 40, "guard_tripped": True,
                                                           "blocked": True}}},
                         {"id": "r1", "kind": "apply", "status": "ok", "duration_ms": 30000,
                          "counts": {"component-manifest": {"add": 148}}}])
        adapter = adapters.GitHubRunnerAdapter(sync)
        assert [(r["kind"], r["status"], r["added"]) for r in adapter.runs()] == [("dry_run", "held", 0), ("apply", "ok", 148)]
        [plan] = adapter.held()
        assert plan["mapping"] == "golden-invariants" and plan["retire"] == {"*": 40}
        adapter.decide("golden-invariants", True, "geprüft", None)
        assert sync.calls == [("apply", "geprüft")]
        assert adapter.observe()["lag_seconds"] >= 7200

    def test_the_knovas_connector_over_its_status(self):
        client = FakeConnectorStatus({"scheduler_state": "running", "last_run_at": NOW.isoformat(), "files_processed": 12,
                                      "worker_alive": True, "extraction": {"knovas_extract_version": "0.4.0"}})
        adapter = adapters.KnovasConnectorAdapter(client)
        seen = adapter.observe()
        assert seen["reachable"] and seen["reported_state"] == "online" and seen["version"] == "0.4.0"
        with pytest.raises(adapters.AdapterError):
            adapter.run("dry_run", None)
        adapter.pause(None)
        assert client.calls == ["stop"]
        assert adapters.KnovasConnectorAdapter(FakeConnectorStatus(fail=True)).observe()["reachable"] is False

    def test_an_adapter_component_in_the_registry(self):
        admin = StandInUser({"admin"})
        store = MemoryWorkspaceStore()
        connector = FakeConnectorStatus(fail=True)
        app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=MemorySecrets(),
                       adapters=lambda c: adapters.KnovasConnectorAdapter(connector) if c["kind"] == "knovas_connector" else None)
        http = app.test_client()
        http.post("/builder/api/components", json={"kind": "knovas_connector", "name": "Ablage alt"})
        page = http.get("/builder/verbinden").get_data(as_text=True)
        assert 'data-component-state="offline"' in page and "Adapter" in page
        assert "Ablage alt ist offline, noch nie gemeldet." in page
        assert 'data-alert="offline"' in http.get("/builder/arbeitsbereich").get_data(as_text=True)
        # And on the Betrieb stage of a published workspace.
        import io

        from builder import package
        from builder_harness import SAMPLE_PACKAGE

        data = package.zip_files(package.files_from_directory(SAMPLE_PACKAGE))
        http.post("/builder/api/import", data={"package": (io.BytesIO(data), "p.zip"), "name": "Beispiel AG"},
                  content_type="multipart/form-data")
        [workspace] = store.list()
        store.link_tenant(workspace.id, TENANT)
        store.publish(workspace.id, admin.id, store.draft(workspace.id)["content_hash"], {"sections": {}}, "h")
        assert 'data-alert="offline"' in http.get("/builder/betrieb").get_data(as_text=True)
