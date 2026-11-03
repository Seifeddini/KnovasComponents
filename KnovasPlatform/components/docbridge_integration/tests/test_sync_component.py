"""A sync component end to end: the real component over real HTTP against the Platform's protocol blueprint.

Only the Brain's side stands in: ``FakeRunner`` answers as brain-runner's command line does, ``fake_signer`` signs. The
sample package is compiled for real, and the key and the certificate request are real.
"""
from __future__ import annotations

import base64
import json
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from werkzeug.serving import make_server

SYNC_SRC = Path(__file__).resolve().parents[2] / "sync_component" / "src"
sys.path.insert(0, str(SYNC_SRC))

from sync_component.agent import Agent, schedule_due  # noqa: E402
from sync_component.client import PlatformClient, Refused  # noqa: E402
from sync_component.identity import Store  # noqa: E402
from sync_component.engine import RunnerEngine, _safe_path, EngineError  # noqa: E402
from sync_component.fetchers import FetchError, credential_for, fetch_http_json  # noqa: E402

from builder import editing, package, protocol  # noqa: E402
from builder_harness import SAMPLE_PACKAGE, FakeEngine, MemorySecrets, MemoryWorkspaceStore, StandInUser, make_app  # noqa: E402

TENANT = "tenant-a"
URL = "https://intern.example.test/kunden.json"


class FakeRunner:
    """brain-runner's command line: `plan` writes a plan file, `apply` prints its counts."""

    def __init__(self, *, blocked=()):
        self.calls, self.env = [], None
        self.blocked = list(blocked)

    def __call__(self, argv, **kw):
        args = argv[3:]
        self.calls.append(args)
        self.env = kw["env"]
        if args[0] == "plan":
            config = Path(args[args.index("--config") + 1])
            assert config.exists() and (config.parent / "response.json").exists(), "package and data are in place"
            mappings = [args[i + 1] for i, a in enumerate(args) if a == "--mapping"]
            plan = {"summary": {m: {"add": 2, "change": 1, "retire": 0} for m in mappings}, "blocked": self.blocked,
                    "guard": {m: {"types": {"Mandate": {"retire": 7, "share": 0.5}}} for m in self.blocked}}
            Path(args[args.index("--out") + 1]).write_text(json.dumps(plan), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        blocked = [] if "--approve-guard" in args else self.blocked
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
            {"writes": 3, "errors": 0, "done": {"m.added": 2, "m.modified": 1}, "blocked": blocked}))


def _fetch(settings, credential, root):
    (Path(root) / "response.json").write_text(json.dumps({"kunden": [{"name": "A"}]}), encoding="utf-8")
    return {"bytes": 1}


@pytest.fixture
def world(tmp_path):
    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    secrets = MemorySecrets()
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=secrets, require_certificate=False)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    http = app.test_client()
    answer = http.post("/builder/api/components/enrol", json={"kind": "sync_component", "name": "Kundendaten", "site": "Zürich", "owner": "IT"})
    assert answer.status_code == 201, answer.get_json()
    made = answer.get_json()
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    for op in ({"op": "source.create", "kind": "http_json", "name": "Kunden", "component": made["id"], "settings": {"url": URL}},
               {"op": "mapping.create", "id": "kunden-mandate", "source": "kunden", "collection": "$.kunden", "type": "Mandate"}):
        editing.apply(model, op)
    workspace = store.create(name="Firma", model=model, actor_id=admin.id, source={"kind": "blank"}, tenant=TENANT)
    client = PlatformClient(f"http://127.0.0.1:{server.server_port}/api/connectors/v1")
    keys = Store(tmp_path / "identity")
    client.use(keys.save(client.enrol(made["token"], keys.request("kundendaten"), ["dry_run", "apply"])))
    runner = FakeRunner()
    engine = RunnerEngine(tmp_path / "sources", {"BRAIN_API_URL": "https://brain.example.test"}, run=runner)
    yield SimpleNamespace(http=http, store=store, client=client, keys=keys, runner=runner, component=made["id"], workspace=workspace,
                          agent=Agent(client, engine, fetchers={"http_json": _fetch}), root=tmp_path, model=model, made=made)
    server.shutdown()
    thread.join(5)


def _queue(world, name, args):
    return world.store.add_command(world.component, name, args, None)


class TestEnrolling:
    def test_the_key_stays_on_site_and_only_its_owner_can_read_it(self, world):
        found = world.keys.load()
        assert found.component == world.component and found.key.exists()
        assert "PRIVATE KEY" in found.key.read_text(encoding="utf-8")
        if sys.platform != "win32":
            assert stat.S_IMODE(found.key.stat().st_mode) == 0o600

    def test_the_token_works_once(self, world, tmp_path):
        other = Store(tmp_path / "other")
        with pytest.raises(Refused) as refused:
            PlatformClient(world.client.url).enrol(world.made["token"], other.request("zweite"), [])
        assert refused.value.status == 401

    def test_a_second_start_uses_what_it_holds(self, world):
        assert Store(world.keys.folder).load().control_token == world.keys.load().control_token


class TestTheConfiguration:
    def test_it_learns_its_source_and_mapping_from_the_workspace(self, world):
        config = world.client.config()
        assert [(s["key"], s["kind"], s["settings"]["url"]) for s in config["sources"]] == [("kunden", "http_json", URL)]
        assert [m["id"] for m in config["mappings"]] == ["kunden-mandate"]

    def test_the_package_is_the_compiled_workspace(self, world):
        found = world.client.package()["workspaces"]
        assert [w["workspace"] for w in found] == [world.workspace]
        files = {p: base64.b64decode(d) for p, d in found[0]["files"].items()}
        assert "brain-config.yaml" in files and any("kunden-mandate" in p for p in files)

    def test_another_components_token_gets_nothing(self, world):
        world.client.credentials = type(world.client.credentials)(world.component, "kcc-wrong", *[None] * 3)
        with pytest.raises(Refused) as refused:
            world.client.package()
        assert refused.value.status == 401


class TestRunning:
    def test_a_dry_run_fetches_compiles_and_reports_counts(self, world):
        result = world.agent.run("dry_run")
        assert (result.status, result.added, result.changed) == ("ok", 2, 1)
        assert world.runner.calls[0][0] == "plan" and "kunden-mandate" in world.runner.calls[0]
        run = world.store.component_runs(world.component, 1)[0]
        assert (run["kind"], run["status"], run["added"], run["changed"]) == ("dry_run", "ok", 2, 1)

    def test_a_dry_run_never_calls_apply(self, world):
        world.agent.run("dry_run")
        assert [c[0] for c in world.runner.calls] == ["plan"]

    def test_an_apply_without_a_dry_run_is_refused_and_writes_nothing(self, world):
        result = world.agent.run("apply")
        assert result.status == "failed" and "apply.no_dry_run" in result.codes and world.runner.calls == []

    def test_an_apply_takes_the_dry_runs_plan_and_only_once(self, world):
        world.agent.run("dry_run")
        assert world.agent.run("apply").status == "ok"
        assert [c[0] for c in world.runner.calls] == ["plan", "apply"]
        assert world.agent.run("apply").status == "failed"                             # a new dry run first
        assert [c[0] for c in world.runner.calls] == ["plan", "apply"]

    def test_a_schedule_only_ever_dry_runs(self, world):
        world.store.update_component(world.component, schedule={"mode": "interval", "every_minutes": 5})
        world.agent.tick()
        assert [c[0] for c in world.runner.calls] == ["plan"]

    def test_a_paused_component_refuses_a_run(self, world):
        world.store.update_component(world.component, paused=True)
        _queue(world, "run", {"kind": "dry_run"})
        world.agent.tick()
        assert world.runner.calls == []

    def test_the_run_command_is_reported_as_finished(self, world):
        _queue(world, "run", {"kind": "dry_run"})
        world.agent.tick()
        assert world.store.component_runs(world.component, 1)[0]["status"] == "ok"


class TestTheGuard:
    def test_a_held_plan_goes_to_the_platform_and_its_approval_applies_it(self, world):
        world.runner.blocked = ["kunden-mandate"]
        assert world.agent.run("dry_run").status == "held"
        held = world.store.held_plans(world.component)
        assert [(h["mapping"], h["state"]) for h in held] == [("kunden-mandate", "held")]
        _queue(world, "approve_plan", {"plan": held[0]["id"], "mapping": "kunden-mandate", "reason": "Altbestand"})
        world.agent.tick()
        last = world.runner.calls[-1]
        assert last[0] == "apply" and last[last.index("--approve-guard") + 1] == "Altbestand"

    def test_a_rejected_plan_stays_unapplied(self, world):
        world.runner.blocked = ["kunden-mandate"]
        world.agent.run("dry_run")
        _queue(world, "reject_plan", {"plan": "p", "mapping": "kunden-mandate"})
        world.agent.tick()
        assert [c[0] for c in world.runner.calls] == ["plan"]


class TestTheReport:
    def test_no_address_or_key_reaches_the_platform(self, world):
        world.agent.run("dry_run")
        events = json.dumps(world.store.events(world.component, 50), default=str)
        assert "intern.example.test" not in events and "Kunden" not in events

    def test_a_failed_fetch_is_a_code_with_no_address(self, world):
        def broken(settings, credential, root):
            raise FetchError("fetch.http_401")

        world.agent.fetchers["http_json"] = broken
        result = world.agent.run("dry_run")
        assert result.status == "failed" and result.codes == ["fetch.http_401"]
        assert world.runner.calls == []
        codes = [e["code"] for e in world.store.events(world.component, 50)]
        assert "fetch.http_401" in codes


class _Answer:
    def __init__(self, status=200, body=b'{"a": 1}'):
        self.status_code, self.body = status, body

    def iter_content(self, size):
        yield self.body


class TestTheFetcher:
    def test_it_writes_the_document_the_mappings_read_with_the_token(self, tmp_path):
        seen = {}

        def get(url, **kw):
            seen.update(kw, url=url)
            return _Answer()

        fetch_http_json({"url": URL}, "geheim", tmp_path, get=get)
        assert (tmp_path / "response.json").read_bytes() == b'{"a": 1}'
        assert seen["headers"]["Authorization"] == "Bearer geheim" and seen["allow_redirects"] is False

    @pytest.mark.parametrize("settings,answer,code", [
        ({"url": "http://intern/x"}, _Answer(), "fetch.not_https"),
        ({"url": URL}, _Answer(status=302), "fetch.http_302"),
        ({"url": URL}, _Answer(status=401), "fetch.http_401"),
        ({"url": URL}, _Answer(body=b"<html>"), "fetch.not_json"),
    ])
    def test_what_it_refuses(self, tmp_path, settings, answer, code):
        with pytest.raises(FetchError, match=code):
            fetch_http_json(settings, None, tmp_path, get=lambda url, **kw: answer)
        assert not (tmp_path / "response.json").exists()

    def test_a_credential_comes_from_the_components_environment(self):
        assert credential_for("kunden-api", {"SYNC_CREDENTIAL_KUNDEN_API": "x"}) == "x"
        assert credential_for("kunden-api", {}) is None


class TestTheEngine:
    @pytest.mark.parametrize("path", ["../boese", "/etc/passwd", "a/../../b", "C:/x", "..\\boese"])
    def test_a_package_path_stays_in_its_folder(self, tmp_path, path):
        with pytest.raises(EngineError):
            _safe_path(tmp_path, path)

    @pytest.mark.parametrize("key", ["../x", "a/b", "", ".hidden"])
    def test_a_source_key_stays_in_its_folder(self, tmp_path, key):
        with pytest.raises(EngineError):
            RunnerEngine(tmp_path, {}).source_root("w", key)


class TestTheSchedule:
    def test_when_a_dry_run_is_due(self):
        import datetime as dt

        now = dt.datetime(2026, 11, 3, 10, 0, tzinfo=dt.timezone.utc)
        assert not schedule_due({"mode": "manual"}, None, now)
        assert schedule_due({"mode": "interval", "every_minutes": 5}, None, now)
        assert not schedule_due({"mode": "interval", "every_minutes": 5}, now - dt.timedelta(minutes=4), now)
        assert schedule_due({"mode": "interval", "every_minutes": 5}, now - dt.timedelta(minutes=6), now)


def test_the_wizard_offers_the_one_line_for_a_sync_component():
    web = Path(__file__).resolve().parents[1] / "src" / "web_interface"
    script = (web / "static" / "js" / "builder" / "components.js").read_text(encoding="utf-8")
    assert "docker run -d" in script and "SYNC_ENROL_TOKEN" in script and "SYNC_PLATFORM_URL" in script
    assert "data-enrol-command" in (web / "templates" / "builder" / "components.html").read_text(encoding="utf-8")
    assert protocol.VERSION == "v1"
