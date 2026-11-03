"""GitHub and file connectors under connector protocol v1: the real components over real HTTP against the Platform's protocol.

A sync component reads records from a GitHub repository and a mounted share; the Knovas Connector's link enrols, reports
and takes commands (its scan and upload stand in here; KnovasConnector/tests/unit/test_platform_link.py runs the real
ones). What a component reports about reading its sources reaches its page, and never a name or an address.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from werkzeug.serving import make_server

SYNC_SRC = Path(__file__).resolve().parents[2] / "sync_component" / "src"
LINK_SRC = Path(__file__).resolve().parents[4] / "KnovasConnector" / "src"
sys.path[:0] = [str(SYNC_SRC), str(LINK_SRC)]

from sync_component.agent import Agent as SyncAgent  # noqa: E402
from sync_component.client import PlatformClient as SyncClient  # noqa: E402
from sync_component.engine import RunnerEngine  # noqa: E402
from sync_component.fetchers import (FetchError, check, check_files, check_github, credential_for, fetch_files,  # noqa: E402
                                     fetch_github)
from sync_component.identity import Store as SyncStore  # noqa: E402

from platform_link import bootstrap  # noqa: E402
from platform_link.agent import Agent as LinkAgent, Outcome  # noqa: E402
from platform_link.client import PlatformClient as LinkClient  # noqa: E402
from platform_link.identity import Store as LinkStore  # noqa: E402

from builder import components as C  # noqa: E402
from builder import editing, package, sources as S  # noqa: E402
from builder_harness import SAMPLE_PACKAGE, FakeEngine, MemorySecrets, MemoryWorkspaceStore, StandInUser, make_app  # noqa: E402

TENANT = "tenant-a"
TOKEN = "ghp_secret-token"
FOLDER = "Akten/Vertraulich"


class Answer:
    def __init__(self, status):
        self.status_code = status


class Runner:
    """brain-runner's command line, recording how it was called."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(SimpleNamespace(argv=argv, env=kw["env"]))
        out = Path(argv[argv.index("--out") + 1])
        ids = [argv[i + 1] for i, a in enumerate(argv) if a == "--mapping"]
        out.write_text(json.dumps({"summary": {m: {"add": 4} for m in ids}}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class Ops:
    """The connector's scan and upload stand in; what it reports does not."""

    def __init__(self):
        self.calls, self.stopped, self.release = [], 0, None

    def dry_run(self, config):
        self.calls.append(("dry_run", [s["key"] for s in config["sources"]]))
        return Outcome(added=3, changed=1)

    def apply(self, config):
        self.calls.append(("apply", []))
        if self.release is not None:
            self.release.wait(10)
        return Outcome(added=3)

    def check(self, source, config):
        return "source.ok", {"files": 12}

    def stop(self):
        self.stopped += 1


@pytest.fixture
def platform():
    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=MemorySecrets(), require_certificate=False,
                   brain_url="https://brain.example.test")
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    model["mappings"] = []
    yield SimpleNamespace(http=app.test_client(), store=store, admin=admin, model=model,
                          url=f"http://127.0.0.1:{server.server_port}/api/connectors/v1")
    server.shutdown()
    thread.join(5)


def _enrol(world, kind, name="Verbindung"):
    answer = world.http.post("/builder/api/components/enrol", json={"kind": kind, "name": name, "site": "Zürich", "owner": "IT"})
    assert answer.status_code == 201, answer.get_json()
    return answer.get_json()


def _workspace(world, *ops):
    for op in ops:
        editing.apply(world.model, op)
    return world.store.create(name="Firma", model=world.model, actor_id=world.admin.id, source={"kind": "blank"}, tenant=TENANT)


def _sync(world, made, tmp_path, *, fetchers=None, checks=None, environ=None):
    client = SyncClient(world.url)
    keys = SyncStore(tmp_path / "identity")
    client.use(keys.save(client.enrol(made["token"], keys.request("x"), ["dry_run"]), made["token"]))
    runner = Runner()
    return SyncAgent(client, RunnerEngine(tmp_path / "sources", {"BRAIN_API_URL": "https://brain.example.test"}, run=runner),
                     fetchers=fetchers, checks=checks, environ=environ if environ is not None else {}), runner


# ------------------------------------------------------------------ GitHub on a sync component

class TestGitHub:
    def test_a_repository_is_read_by_the_runner_with_the_token_in_its_environment_only(self, tmp_path):
        seen = {}

        def get(url, **kw):
            seen.update(url=url, auth=kw["headers"].get("Authorization"))
            return Answer(200)

        reading = fetch_github({"repository": "firma/daten", "ref": "main"}, TOKEN, tmp_path, get=get)
        assert seen == {"url": "https://api.github.com/repos/firma/daten/commits/main", "auth": f"Bearer {TOKEN}"}
        assert reading.args[:6] == ["--source", "github", "--repo", "firma/daten", "--ref", "main"]
        assert TOKEN not in " ".join(reading.args), "a command line is visible to every process on the machine"
        assert reading.env == {"BRAIN_GITHUB_TOKEN": TOKEN}

    @pytest.mark.parametrize("status", [401, 403, 404])
    def test_a_refusal_is_a_code_with_a_sentence_for_the_person(self, status):
        with pytest.raises(FetchError, match=f"fetch.http_{status}"):
            check_github({"repository": "firma/daten"}, TOKEN, get=lambda url, **kw: Answer(status))
        assert f"fetch.http_{status}" in C.CHECK_TEXTS

    @pytest.mark.parametrize("settings", [{"repository": "keine-form"}, {"repository": "a/b", "ref": "../x"}])
    def test_settings_that_are_not_a_repository_are_refused_before_any_call(self, settings):
        def get(url, **kw):
            raise AssertionError("no call")

        with pytest.raises(FetchError, match="fetch.bad_repository"):
            check_github(settings, None, get=get)

    def test_a_credential_for_every_source_of_a_kind(self):
        env = {"SYNC_CREDENTIAL_GITHUB": "for-all", "SYNC_CREDENTIAL_EIGENE": "own"}
        assert credential_for("andere", "github", environ=env) == "for-all"
        assert credential_for("eigene", "github", environ=env) == "own", "a source's own wins"
        assert credential_for("andere", "http_json", environ=env) is None

    def test_a_dry_run_end_to_end(self, platform, tmp_path):
        made = _enrol(platform, "github_runner")
        _workspace(platform, {"op": "source.create", "kind": "github", "name": "Daten", "component": made["id"],
                              "settings": {"repository": "firma/daten", "ref": "main"}},
                   {"op": "mapping.create", "id": "daten-mandate", "source": "daten", "collection": "yaml_file:daten/**/*.yaml", "type": "Mandate"})
        fetcher = lambda settings, credential, root: fetch_github(settings, credential, root, get=lambda url, **kw: Answer(200))  # noqa: E731
        agent, runner = _sync(platform, made, tmp_path, fetchers={"github": fetcher}, environ={"SYNC_CREDENTIAL_GITHUB": TOKEN})
        result = agent.run("dry_run")
        assert result.status == "ok" and result.added == 4, result.codes
        call = runner.calls[0]
        assert call.argv[1:3] == ["-m", "brain_runner"] and "--repo" in call.argv and TOKEN not in call.argv
        assert call.env["BRAIN_GITHUB_TOKEN"] == TOKEN
        assert [r["added"] for r in platform.store.component_runs(made["id"])] == [4]


# ------------------------------------------------------------------ a file share on a sync component

class TestFiles:
    def env(self, tmp_path):
        return {"SYNC_FILES_ROOT": str(tmp_path / "ablage")}

    def test_a_share_that_did_not_mount_is_refused_not_read_as_empty(self, tmp_path):
        with pytest.raises(FetchError, match="fetch.folder_missing"):
            check_files({"folder": ""}, environ=self.env(tmp_path))
        (tmp_path / "ablage").mkdir()
        with pytest.raises(FetchError, match="fetch.folder_empty"):
            check_files({"folder": ""}, environ=self.env(tmp_path))

    def test_it_counts_files_and_tells_the_runner_the_root_of_the_share(self, tmp_path):
        (tmp_path / "ablage" / "Akten").mkdir(parents=True)
        (tmp_path / "ablage" / "Akten" / "a.pdf").write_text("x")
        assert check_files({"folder": "Akten"}, environ=self.env(tmp_path)) == {"files": 1}
        reading = fetch_files({"folder": "Akten"}, None, tmp_path, environ=self.env(tmp_path))
        assert reading.share_root == str(tmp_path / "ablage"), "the mapping's glob already starts with the folder"

    def test_no_way_out_of_the_share(self, tmp_path):
        with pytest.raises(FetchError, match="fetch.bad_folder"):
            check_files({"folder": "../etc"}, environ=self.env(tmp_path))

    def test_the_runner_reads_it_through_the_share_source(self, tmp_path):
        engine_runner = Runner()
        engine = RunnerEngine(tmp_path / "s", {}, run=engine_runner)
        root = engine.source_root("w", "k")
        engine.dry_run(root, ["m"], SimpleNamespace(args=[], env={}, share_root="/sources"))
        assert engine_runner.calls[0].argv[1:5] == ["-m", "sync_component.share", "--root", "/sources"]


class TestShareSource:
    def source(self, root):
        pytest.importorskip("brain_runner")
        from sync_component.share import ShareSource

        return ShareSource(root)

    def test_it_lists_files_with_the_file_systems_date_and_never_asks_git(self, tmp_path, monkeypatch):
        share = self.source(tmp_path)
        (tmp_path / "Akten").mkdir()
        (tmp_path / "Akten" / "b.pdf").write_text("x")
        (tmp_path / "Akten" / "a.pdf").write_text("x")

        def no_git(*a, **kw):
            raise AssertionError("no git on a share")

        monkeypatch.setattr("brain_runner.sources.local.git_output", no_git)
        assert share.glob("Akten/**/*") == ["Akten/a.pdf", "Akten/b.pdf"]
        assert share.last_commit("Akten/a.pdf") is None and share.modified("Akten/a.pdf")

    @pytest.mark.skipif(os.name == "nt", reason="symbolic links need rights on Windows")
    def test_a_link_out_of_the_share_is_not_read(self, tmp_path):
        outside = tmp_path / "geheim"
        outside.mkdir()
        (outside / "s.txt").write_text("x")
        root = tmp_path / "ablage"
        root.mkdir()
        (root / "a.txt").write_text("x")
        (root / "link.txt").symlink_to(outside / "s.txt")
        (root / "ordner").symlink_to(outside)
        assert self.source(root).glob("**/*") == ["a.txt"]

    def test_the_real_extractor_gives_the_fields_a_mapping_is_designed_on(self, tmp_path):
        share = self.source(tmp_path)
        (tmp_path / "Akten").mkdir()
        (tmp_path / "Akten" / "a.pdf").write_text("x")
        from brain_runner.extractors import extract

        doc = next(iter(extract(share, {"extractor": "file_glob", "glob": "Akten/**/*"})))
        assert set(S.OnSiteFiles.EXAMPLE) <= set(doc), "the example has the fields a real file has"


# ------------------------------------------------------------------ what a component reports about its sources

class TestChecks:
    def test_right_after_it_starts_its_page_says_what_is_wrong(self, platform, tmp_path):
        made = _enrol(platform, "sync_component")
        _workspace(platform, {"op": "source.create", "kind": "knovas_connector", "name": "Ablage", "component": made["id"],
                              "settings": {"folder": FOLDER}})
        from sync_component.fetchers import CHECKS, FETCHERS

        agent, _ = _sync(platform, made, tmp_path, fetchers=FETCHERS, checks=CHECKS)
        os.environ.pop("SYNC_FILES_ROOT", None)
        agent.tick()
        stored = platform.store.component(TENANT, made["id"])["source_checks"]
        assert [v["code"] for v in stored.values()] == ["fetch.folder_missing"]
        page = platform.http.get(f"/builder/komponenten/{made['id']}").get_data(as_text=True)
        assert "Den Ordner gibt es nicht" in page and "Verbindung prüfen" in page

    def test_a_person_can_ask_again_and_it_answers_with_the_next_heartbeat(self, platform, tmp_path):
        made = _enrol(platform, "sync_component")
        _workspace(platform, {"op": "source.create", "kind": "http_json", "name": "Kunden", "component": made["id"],
                              "settings": {"url": "https://intern.example.test/k.json"}})
        calls = []

        def fetcher(settings, credential, root):
            calls.append(1)

        agent, _ = _sync(platform, made, tmp_path, fetchers={"http_json": fetcher})
        agent.tick()
        assert platform.http.post(f"/builder/api/components/{made['id']}/check", json={}).status_code == 200
        agent.tick()
        assert len(calls) == 2
        assert [c["result"] for c in platform.store.commands(made["id"]) if c["command"] == "check"] == ["ok"]

    def test_a_check_is_only_repeated_when_a_source_changed(self, platform, tmp_path):
        made = _enrol(platform, "sync_component")
        _workspace(platform, {"op": "source.create", "kind": "http_json", "name": "Kunden", "component": made["id"],
                              "settings": {"url": "https://intern.example.test/k.json"}})
        calls = []
        agent, _ = _sync(platform, made, tmp_path, fetchers={"http_json": lambda *a: calls.append(1)})
        agent.check_sources()
        agent.check_sources()
        assert len(calls) == 1

    def test_no_name_or_address_can_reach_the_platform_through_a_check(self, platform, tmp_path):
        made = _enrol(platform, "sync_component")
        workspace = _workspace(platform, {"op": "source.create", "kind": "http_json", "name": "Kunden", "component": made["id"],
                                          "settings": {"url": "https://intern.example.test/k.json"}})
        agent, _ = _sync(platform, made, tmp_path, fetchers={"http_json": lambda *a: None})
        client = agent.client
        client.report_checks([{"workspace": workspace, "source": "kunden", "code": "source.ok",
                               "counts": {"Akten/Vertraulich/Lohn.pdf": 1, "/etc/passwd": 2, "files": 3}},
                              {"workspace": workspace, "source": "fremd", "code": "source.ok", "counts": {}}])
        stored = platform.store.component(TENANT, made["id"])["source_checks"]
        assert list(stored) == [f"{workspace}/kunden"], "a source it does not serve is dropped"
        assert stored[f"{workspace}/kunden"]["counts"] == {"files": 3}
        with pytest.raises(Exception) as refused:
            client.report_checks([{"workspace": workspace, "source": "kunden", "code": "Akten/Vertraulich/Lohn.pdf"}])
        assert getattr(refused.value, "status", None) == 400

    def test_every_code_the_components_report_has_a_sentence(self):
        import re

        codes = set()
        for path in list((SYNC_SRC / "sync_component").glob("*.py")) + list((LINK_SRC / "platform_link").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            codes |= {c for c in re.findall(r'"((?:fetch|documents|source)\.[a-z_]+)"', text)}
        assert codes and codes <= set(C.CHECK_TEXTS), codes - set(C.CHECK_TEXTS)

    def test_a_kind_without_a_check_is_checked_by_fetching_into_a_folder_that_is_gone_afterwards(self):
        folders = []

        def fetcher(settings, credential, root):
            folders.append(root)

        assert check("x", {}, None, fetchers={"x": fetcher}, checks={})[0] == "source.ok"
        assert not folders[0].exists()
        assert check("y", {}, None, fetchers={"x": fetcher}, checks={})[0] == "source.kind_unknown"


# ------------------------------------------------------------------ designing a mapping for a source read at the company

class TestDesigningOnSite:
    def test_a_share_is_designed_on_a_made_up_example(self, platform, tmp_path):
        made = _enrol(platform, "sync_component")
        workspace = _workspace(platform, {"op": "source.create", "kind": "knovas_connector", "name": "Ablage", "component": made["id"],
                                          "settings": {"folder": FOLDER}})
        agent, _ = _sync(platform, made, tmp_path, fetchers={"knovas_connector": lambda *a: None},
                         checks={"knovas_connector": lambda s, c: {"files": 12}})
        agent.tick()
        component = platform.store.component(TENANT, made["id"])
        assert component["state"] == "enrolled"
        tested = platform.http.post(f"/builder/api/w/{workspace}/sources/ablage/test", json={}).get_json()
        assert tested["success"] and tested["result"]["counts"] == [["Dateien", 12]]
        sample = platform.http.post(f"/builder/api/w/{workspace}/sources/ablage/sample", json={"collection": "*"}).get_json()
        assert "Beispiel/Unterordner/Dokument.pdf" in json.dumps(sample), "an example, nothing from the company"

    def test_github_without_a_token_here_says_where_it_is_read(self, platform, tmp_path):
        made = _enrol(platform, "github_runner")
        workspace = _workspace(platform, {"op": "source.create", "kind": "github", "name": "Daten", "component": made["id"],
                                          "settings": {"repository": "firma/daten", "ref": "main"}})
        _sync(platform, made, tmp_path)
        answer = platform.http.post(f"/builder/api/w/{workspace}/sources/daten/test", json={})
        assert answer.status_code == 502 and "bei der Firma" in answer.get_json()["error"]

    def test_a_dry_run_on_a_made_up_example_is_refused(self, platform, tmp_path):
        made = _enrol(platform, "sync_component")
        workspace = _workspace(platform, {"op": "source.create", "kind": "knovas_connector", "name": "Ablage", "component": made["id"],
                                          "settings": {"folder": FOLDER}},
                               {"op": "mapping.create", "id": "ablage-akten", "source": "ablage", "collection": "*", "type": "Mandate"})
        _sync(platform, made, tmp_path, fetchers={"knovas_connector": lambda *a: None})
        answer = platform.http.post(f"/builder/api/w/{workspace}/mappings/ablage-akten/dry-run", json={})
        assert answer.status_code == 409 and "läuft dort" in answer.get_json()["error"], answer.get_json()

    @pytest.mark.parametrize("kind", ["github", "knovas_connector", "http_json"])
    def test_every_kind_of_source_can_go_on_a_sync_component(self, platform, kind, tmp_path):
        made = _enrol(platform, "sync_component")
        _sync(platform, made, tmp_path)
        settings = {"github": {"repository": "a/b"}, "knovas_connector": {"folder": "x"},
                    "http_json": {"url": "https://x.example.test/"}}[kind]
        workspace = _workspace(platform)
        answer = platform.http.post(f"/builder/api/w/{workspace}/ontology", json={"op": "source.create", "kind": kind, "name": "Q",
                                                                              "component": made["id"], "settings": settings})
        assert answer.status_code == 200, answer.get_json()


# ------------------------------------------------------------------ a new token

class TestNewToken:
    def test_token_erneuern_enrols_again_instead_of_failing_for_good(self, platform, tmp_path):
        from sync_component.__main__ import enrolled

        made = _enrol(platform, "sync_component")
        client = SyncClient(platform.url)
        keys = SyncStore(tmp_path / "identity")
        first = enrolled(client, keys, made["token"], "x")
        client.use(first)
        assert enrolled(client, keys, made["token"], "x").control_token == first.control_token, "a restart does not enrol"
        assert enrolled(client, keys, "", "x").control_token == first.control_token, "the token may be gone after the first start"
        fresh = platform.http.post(f"/builder/api/components/{made['id']}/rotate", json={}).get_json()["token"]
        second = enrolled(client, keys, fresh, "x")
        assert second.control_token != first.control_token
        client.use(second)
        client.heartbeat(state="online", lag_seconds=None, open_errors=0, capabilities=[])

    def test_the_one_time_token_is_not_kept(self, platform, tmp_path):
        made = _enrol(platform, "sync_component")
        keys = SyncStore(tmp_path / "identity")
        client = SyncClient(platform.url)
        keys.save(client.enrol(made["token"], keys.request("x"), []), made["token"])
        assert made["token"] not in (tmp_path / "identity" / "identity.json").read_text(encoding="utf-8")


# ------------------------------------------------------------------ the Knovas Connector's link

class TestConnectorLink:
    def linked(self, world, tmp_path, *, kind="knovas_connector"):
        made = _enrol(world, kind)
        environ = {"RC_PLATFORM_URL": world.url, "RC_ENROL_TOKEN": made["token"], "RC_ENROL_NAME": "kanzlei",
                   "RC_SYNC_STATE_PATH": str(tmp_path / "state" / ".rc-sync-state.json")}
        client, found = bootstrap.prepare(environ)
        return made, environ, client, found

    def test_one_line_is_all_it_needs(self, platform, tmp_path):
        made, environ, _, found = self.linked(platform, tmp_path)
        component = platform.store.component(TENANT, made["id"])
        assert component["state"] == "enrolled" and found.component == made["id"]
        assert environ["SEMANTIX_SECURE_BASE_URL"] == "https://brain.example.test"
        assert environ["SEMANTIX_CLIENT_CERT_PATH"] == str(found.certificate) and environ["SEMANTIX_CLIENT_KEY_PATH"] == str(found.key)
        assert environ["RC_WATCH_ROOTS"] == "/sources"
        assert environ["RC_SYNC_AUTO_START_CONTINUOUS"] == "false", "it sends nothing until a person says so"

    def test_the_brains_ca_comes_from_the_enrolment_and_nothing_is_copied_by_hand(self, tmp_path):
        ca = "-----BEGIN CERTIFICATE-----\nQ0E=\n-----END CERTIFICATE-----\n"
        admin = StandInUser({"admin"}, "Ada Admin")
        store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
        app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=MemorySecrets(), require_certificate=False,
                       brain_url="https://brain.example.test", brain_ca=ca)
        server = make_server("127.0.0.1", 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            made = app.test_client().post("/builder/api/components/enrol", json={"kind": "knovas_connector", "name": "D"}).get_json()
            environ = {"RC_PLATFORM_URL": f"http://127.0.0.1:{server.server_port}/api/connectors/v1", "RC_ENROL_TOKEN": made["token"],
                       "RC_SYNC_STATE_PATH": str(tmp_path / "s.json")}
            bootstrap.prepare(environ)
            assert Path(environ["SEMANTIX_CA_CERT_PATH"]).read_text(encoding="utf-8") == ca
        finally:
            server.shutdown()

    def test_without_a_ca_from_the_enrolment_it_still_starts_and_trusts_public_ones(self, platform, tmp_path):
        import requests

        _, environ, _, _ = self.linked(platform, tmp_path)
        assert environ["SEMANTIX_CA_CERT_PATH"] == requests.certs.where()

    def test_an_installations_own_certificate_stays(self, platform, tmp_path):
        made = _enrol(platform, "knovas_connector")
        environ = {"RC_PLATFORM_URL": platform.url, "RC_ENROL_TOKEN": made["token"], "SEMANTIX_CLIENT_CERT_PATH": "/certs/own.pem",
                   "SEMANTIX_SECURE_BASE_URL": "https://own.example.test", "RC_SYNC_STATE_PATH": str(tmp_path / ".s.json")}
        bootstrap.prepare(environ)
        assert environ["SEMANTIX_CLIENT_CERT_PATH"] == "/certs/own.pem"
        assert environ["SEMANTIX_SECURE_BASE_URL"] == "https://own.example.test"

    def test_without_the_address_nothing_happens(self):
        environ = {}
        assert bootstrap.prepare(environ) is None and environ == {}

    def test_a_used_token_says_so_and_does_not_show_itself(self, platform, tmp_path):
        made = _enrol(platform, "knovas_connector")
        bootstrap.prepare({"RC_PLATFORM_URL": platform.url, "RC_ENROL_TOKEN": made["token"], "RC_SYNC_STATE_PATH": str(tmp_path / "a" / "s.json")})
        with pytest.raises(RuntimeError) as refused:
            bootstrap.prepare({"RC_PLATFORM_URL": platform.url, "RC_ENROL_TOKEN": made["token"], "RC_SYNC_STATE_PATH": str(tmp_path / "b" / "s.json")})
        assert "bad_token" in str(refused.value) and made["token"] not in str(refused.value)

    def test_a_dry_run_reports_counts_and_an_apply_needs_a_persons_command(self, platform, tmp_path):
        made, _, client, _ = self.linked(platform, tmp_path)
        _workspace(platform, {"op": "source.create", "kind": "knovas_connector", "name": "Ablage", "component": made["id"],
                              "settings": {"folder": FOLDER, "access_groups": "alle"}})
        ops = Ops()
        agent = LinkAgent(client, ops, background=False)
        agent.tick()
        assert ops.calls == [], "started, nothing read or sent"
        assert platform.http.post(f"/builder/api/components/{made['id']}/run", json={"kind": "apply"}).status_code == 409, "first a dry run"
        assert platform.http.post(f"/builder/api/components/{made['id']}/run", json={"kind": "dry_run"}).status_code == 200
        agent.tick()
        assert ops.calls == [("dry_run", ["ablage"])]
        run = platform.store.component_runs(made["id"])[0]
        assert (run["kind"], run["status"], run["added"], run["changed"]) == ("dry_run", "ok", 3, 1)
        assert platform.http.post(f"/builder/api/components/{made['id']}/run", json={"kind": "apply"}).status_code == 200
        agent.tick()
        assert ops.calls[-1][0] == "apply"
        assert FOLDER not in json.dumps(platform.store.events(made["id"], 50), default=str)

    def test_a_schedule_only_ever_dry_runs(self, platform, tmp_path):
        made, _, client, _ = self.linked(platform, tmp_path)
        platform.store.update_component(made["id"], schedule={"mode": "interval", "every_minutes": 5})
        ops = Ops()
        LinkAgent(client, ops, background=False).tick()
        assert ops.calls == [("dry_run", [])]

    def test_heartbeats_go_on_during_a_long_transfer_and_a_pause_stops_it(self, platform, tmp_path):
        made, _, client, _ = self.linked(platform, tmp_path)
        ops = Ops()
        ops.release = threading.Event()
        agent = LinkAgent(client, ops)
        platform.store.add_command(made["id"], "run", {"kind": "apply"}, None)
        agent.tick()
        for _ in range(50):
            if agent.busy and ops.calls:
                break
            time.sleep(0.02)
        assert agent.busy
        second = platform.store.add_command(made["id"], "run", {"kind": "dry_run"}, None)
        agent.tick()
        assert next(c for c in platform.store.commands(made["id"]) if c["id"] == second)["result"] == "refused", "one run at a time"
        platform.store.add_command(made["id"], "pause", {}, None)
        agent.tick()
        assert ops.stopped == 1
        ops.release.set()
        agent._running.join(5)
        assert platform.store.component_runs(made["id"])[0]["kind"] == "apply"

    def test_an_exception_never_carries_a_path_to_the_platform(self, platform, tmp_path):
        made, _, client, _ = self.linked(platform, tmp_path)

        class Broken(Ops):
            def dry_run(self, config):
                raise OSError(f"cannot read /sources/{FOLDER}/Lohn.pdf")

        LinkAgent(client, Broken(), background=False)._work("dry_run", None)
        run = platform.store.component_runs(made["id"])[0]
        assert run["status"] == "failed"
        events = json.dumps(platform.store.events(made["id"], 50), default=str)
        assert "Lohn.pdf" not in events and "run.oserror" in events

    def test_its_page_shows_it_like_any_other_component(self, platform, tmp_path):
        made, _, client, _ = self.linked(platform, tmp_path)
        _workspace(platform, {"op": "source.create", "kind": "knovas_connector", "name": "Ablage", "component": made["id"],
                              "settings": {"folder": FOLDER}})
        LinkAgent(client, Ops(), background=False).tick()
        page = platform.http.get(f"/builder/komponenten/{made['id']}").get_data(as_text=True)
        assert "Liest die Quelle." in page and "12" in page


def test_the_wizard_has_a_working_line_for_every_kind_it_offers():
    web = Path(__file__).resolve().parents[1] / "src" / "web_interface"
    script = (web / "static" / "js" / "builder" / "components.js").read_text(encoding="utf-8")
    for kind, info in C.KINDS.items():
        if not info.get("planned") and not info.get("adapter_only"):
            assert f"kind === '{kind}'" in script, f"the wizard offers {kind} but has no line for it"


def test_every_image_the_wizard_names_is_one_this_repository_builds():
    root = Path(__file__).resolve().parents[4]
    script = (root / "KnovasPlatform/components/docbridge_integration/src/web_interface/static/js/builder/components.js").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    connector = (root / "KnovasConnector/docker-compose.yml").read_text(encoding="utf-8")
    for image in ("sync-component:0.1.0",):
        assert image in script and f"image: {image}" in compose
    assert "knovas-connector:0.1.1" in script and "image: knovas-connector:0.1.1" in connector


def test_the_compose_file_is_valid_and_the_component_is_a_service():
    """It stood under networks: once, and compose then refused the whole file."""
    import yaml

    root = Path(__file__).resolve().parents[4]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["sync-component"]["profiles"] == ["sync-component"]
    assert set(compose["networks"]) == {"knovas-internal"}


def test_no_module_of_a_component_is_ignored_by_git():
    """credentials.py was: the repository's secrets rule ignores credentials.*, so a fresh checkout could not start."""
    import subprocess

    root = Path(__file__).resolve().parents[4]
    files = [str(p.relative_to(root)) for base in (SYNC_SRC, LINK_SRC / "platform_link") for p in base.rglob("*.py")]
    ignored = subprocess.run(["git", "check-ignore", *files], cwd=root, capture_output=True, text=True)
    assert ignored.stdout.strip() == "", ignored.stdout
