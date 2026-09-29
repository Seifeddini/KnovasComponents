"""Switching Experimente on and off, and what create_app wires when it is on.

Off (the default) means off everywhere: no navigation item, the pages go back
to the search, every API path -- the CI machine API included -- answers 404
"Experimente sind nicht eingeschaltet.", no blueprint, no index client, no
worker thread. And experiment hits are still stripped from every search: a
deployment that switched the module off must not start showing the documents
it once wrote to Knovas as ordinary files.

On, create_app registers the built-in evaluators and the core pack, both
blueprints, the index client (always, deletions need it), the runner client
when a URL is set and the workers when they are enabled.

Also here: the small pieces part E adds elsewhere -- the IdentityGate's
bearer endpoints, the assignable roles in the console, the config.yaml
section, MAX_CONTENT_LENGTH.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md (sections 2, 13)
"""

from __future__ import annotations

import json
import logging

import pytest

pytest.importorskip("flask")

from conftest import (  # noqa: E402
    DummyKnovasClient,
    FakeIndexClient,
    _identity_app,
    _signed_in,
    platform_db_reachable,
)

needs_db = pytest.mark.skipif(not platform_db_reachable(),
                              reason="No PostgreSQL at the identity test DSN")

SWITCHED_OFF = {"success": False, "error": "Experimente sind nicht eingeschaltet."}
OFF_YAML = 'experiments:\n  enabled: "false"\n'
EXP_POINTER = "experiments/marketing/MKT-1"


def on_yaml(**extra):
    """The module on (worker and index off unless given), plus extra keys."""
    lines = ["experiments:", '  enabled: "true"']
    worker = extra.pop("worker", "false")
    index = extra.pop("index", "false")
    runner = extra.pop("runner", None)
    lines += ["  index:", f'    enabled: "{index}"', "  worker:", f'    enabled: "{worker}"']
    if runner is not None:
        lines += ["  runner:", f'    url: "{runner}"']
    assert not extra
    return "\n".join(lines) + "\n"


def _no_index_client(config):  # pragma: no cover - must not be called
    raise AssertionError("the index client is built only while the module is on")


@pytest.fixture
def off_app(platform_db, tmp_path, monkeypatch):
    monkeypatch.setattr("experiments.indexer.make_index_client", _no_index_client)
    return _identity_app(platform_db, tmp_path, monkeypatch, extra_yaml=OFF_YAML)


@pytest.fixture
def default_app(platform_db, tmp_path, monkeypatch):
    """No experiments section at all: the default must be off."""
    monkeypatch.setattr("experiments.indexer.make_index_client", _no_index_client)
    monkeypatch.delenv("EXPERIMENTS_ENABLED", raising=False)
    return _identity_app(platform_db, tmp_path, monkeypatch)


def _nav(html):
    return 'href="/experiments"' in html


# -- off ---------------------------------------------------------------------------


@needs_db
class TestSwitchedOff:
    def test_the_default_is_off(self, default_app):
        assert default_app.extensions["experiments"]["settings"].enabled is False
        assert not any(name.startswith(("experiments.", "experiments_api."))
                       for name in default_app.view_functions)

    def test_no_navigation_item_even_for_an_experimenter(self, off_app, experimenter):
        client = _signed_in(off_app, experimenter.email)
        assert not _nav(client.get("/").get_data(as_text=True))

    @pytest.mark.parametrize("path", ["/experiments", "/experiments/verwaltung",
                                      "/experiments/MKT-1"])
    def test_the_pages_go_back_to_the_search(self, off_app, experimenter, path):
        client = _signed_in(off_app, experimenter.email)
        r = client.get(path)
        assert r.status_code == 302 and r.headers["Location"].endswith("/")

    @pytest.mark.parametrize("method, path", [
        ("GET", "/api/experiments"), ("POST", "/api/experiments"),
        ("GET", "/api/experiments/meta"), ("PUT", "/api/experiments/preferences"),
        ("GET", "/api/experiments/MKT-1"), ("POST", "/api/experiments/MKT-1/measurements/csv"),
    ])
    def test_the_api_says_it_is_off(self, off_app, experimenter, method, path):
        client = _signed_in(off_app, experimenter.email)
        r = client.open(path, method=method, json={})
        assert r.status_code == 404 and r.get_json() == SWITCHED_OFF

    def test_even_before_sign_in_and_without_csrf(self, off_app):
        client = off_app.test_client()
        assert client.get("/api/experiments").get_json() == SWITCHED_OFF
        assert client.post("/api/experiments", json={}).get_json() == SWITCHED_OFF
        assert client.get("/experiments").status_code == 302

    @pytest.mark.parametrize("method, path", [
        ("GET", "/api/experiments/v1/ping"), ("POST", "/api/experiments/v1/experiments"),
        ("POST", "/api/experiments/v1/experiments/MKT-1/runs"),
    ])
    def test_the_machine_api_says_it_is_off(self, off_app, method, path):
        r = off_app.test_client().open(path, method=method, json={},
                                       headers={"Authorization": "Bearer kxp_irgendwas"})
        assert r.status_code == 404 and r.get_json() == SWITCHED_OFF

    def test_nothing_runs_in_the_background(self, off_app):
        ext = off_app.extensions["experiments"]
        assert ext["index_client"] is None and ext["runner"] is None and ext["workers"] == []
        assert ext["search"].enabled is False

    def test_the_search_still_strips_experiment_hits(self, off_app, experimenter, monkeypatch):
        """With the module off nobody may see experiment documents -- not
        even people who hold the role."""
        client = _signed_in(off_app, experimenter.email)
        knovas = DummyKnovasClient.last_instance
        knovas.search_results = [
            {"doc_id": "corpus/a.pdf", "path": "corpus/a.pdf", "title": "a", "score": 0.9},
            {"doc_id": EXP_POINTER, "path": EXP_POINTER, "title": "MKT-1", "score": 0.95},
        ]
        original = knovas.search_documents

        def with_semantix(query, limit=20, filters=None):
            out = original(query, limit=limit, filters=filters)
            out["semantix"] = {"status": "ok", "result_count": 2,
                               "pointers": ["corpus/a.pdf", EXP_POINTER]}
            return out

        monkeypatch.setattr(knovas, "search_documents", with_semantix)
        r = client.post("/api/search", json={"query": "Vertrag"})
        payload = r.get_json()
        assert [row["doc_id"] for row in payload["results"]] == ["corpus/a.pdf"]
        assert payload["semantix"]["pointers"] == ["corpus/a.pdf"]
        assert payload["semantix"]["result_count"] == 1
        assert EXP_POINTER not in json.dumps(payload)

    def test_experiment_pointers_are_never_files(self, off_app, experimenter, tmp_path):
        from document_grants import DocumentGrantStore

        client = _signed_in(off_app, experimenter.email)
        DocumentGrantStore(str(tmp_path / "grants.sqlite3")).grant(str(experimenter.id),
                                                                   [EXP_POINTER])
        assert client.get(f"/api/document/{EXP_POINTER}").status_code == 404


class TestWithoutIdentity:
    """The module needs per-user accounts; the legacy shared login keeps it off
    whatever EXPERIMENTS_ENABLED says, and says so in the log."""

    def _legacy_app(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-key-for-experiments-switch")
        monkeypatch.setenv("EXPERIMENTS_ENABLED", "true")
        monkeypatch.setattr("experiments.indexer.make_index_client", _no_index_client)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            'web:\n'
            '  secret_key: "${WEB_SECRET_KEY}"\n'
            '  login:\n'
            '    enabled: false\n'
            'identity:\n'
            '  enabled: false\n'
            'api:\n'
            '  base_url: "http://example.test"\n'
            'experiments:\n'
            '  enabled: "${EXPERIMENTS_ENABLED:-false}"\n',
            encoding="utf-8",
        )
        from conftest import DummyFileHandler
        from web_interface import app as web_app

        monkeypatch.setattr(web_app, "KnovasAPIClient", DummyKnovasClient)
        monkeypatch.setattr(web_app, "AutoDocFileHandler", DummyFileHandler)
        flask_app = web_app.create_app(str(config_path))
        flask_app.config.update(TESTING=True)
        return flask_app

    def test_it_stays_off_and_says_why(self, tmp_path, monkeypatch, caplog):
        with caplog.at_level(logging.WARNING, logger="web_interface.app"):
            app = self._legacy_app(tmp_path, monkeypatch)
        assert app.extensions["experiments"]["settings"].enabled is False
        assert any("EXPERIMENTS_ENABLED" in r.getMessage() for r in caplog.records)
        client = app.test_client()
        assert client.get("/api/experiments").get_json() == SWITCHED_OFF
        assert client.get("/experiments").status_code == 302
        assert not _nav(client.get("/").get_data(as_text=True))

    def test_its_search_strips_experiment_hits_too(self, tmp_path, monkeypatch):
        app = self._legacy_app(tmp_path, monkeypatch)
        client = app.test_client()
        DummyKnovasClient.last_instance.search_results = [
            {"doc_id": EXP_POINTER, "path": EXP_POINTER, "title": "MKT-1", "score": 0.9}]
        page = client.get("/").get_data(as_text=True)
        marker = 'csrfToken: "'
        token = page[page.index(marker) + len(marker):page.index('"', page.index(marker) + len(marker))]
        r = client.post("/api/search", json={"query": "Vertrag"}, headers={"X-CSRF-Token": token})
        assert r.status_code == 200
        assert r.get_json()["results"] == []


# -- on ------------------------------------------------------------------------------


@needs_db
class TestSwitchedOn:
    def test_navigation_for_viewers_only(self, experiments_app, experimenter_client,
                                         exp_manager_client, exp_member_client):
        assert _nav(experimenter_client.get("/").get_data(as_text=True))
        assert _nav(exp_manager_client.get("/").get_data(as_text=True))
        assert not _nav(exp_member_client.get("/").get_data(as_text=True))

    def test_the_navigation_survives_a_broken_role_lookup(self, experiments_app,
                                                          experimenter_client, monkeypatch):
        import experiments.permissions as permissions

        def boom(user):
            raise RuntimeError("roles unavailable")

        monkeypatch.setattr(permissions, "can_view", boom)
        r = experimenter_client.get("/")
        assert r.status_code == 200 and not _nav(r.get_data(as_text=True))

    def test_what_create_app_wired(self, experiments_app, fake_index_client):
        ext = experiments_app.extensions["experiments"]
        assert ext["settings"].enabled is True
        assert ext["index_client"] is fake_index_client
        assert ext["runner"] is None
        assert ext["workers"] == []
        assert ext["search"].enabled is True
        assert DummyKnovasClient.last_instance is not fake_index_client

    def test_builtin_evaluators_and_the_core_pack_are_installed_at_start(
            self, experiments_app, platform_db):
        from experiments import evaluators

        keys = {r[0] for r in platform_db.execute(
            "SELECT key FROM exp_evaluators WHERE language = 'builtin'")}
        assert keys == set(evaluators.BUILTINS)
        core = platform_db.execute(
            "SELECT count(*) FROM exp_types WHERE domain_id IS NULL AND key = 'hypothesis'"
        ).fetchone()[0]
        assert core == 1

    def test_a_second_start_changes_nothing(self, experiments_app, platform_db, tmp_path,
                                            monkeypatch, fake_index_client):
        before = platform_db.execute(
            "SELECT (SELECT count(*) FROM exp_evaluator_versions), "
            "(SELECT count(*) FROM exp_type_versions)").fetchone()
        again = tmp_path / "again"
        again.mkdir()
        _identity_app(platform_db, again, monkeypatch, extra_yaml=on_yaml())
        after = platform_db.execute(
            "SELECT (SELECT count(*) FROM exp_evaluator_versions), "
            "(SELECT count(*) FROM exp_type_versions)").fetchone()
        assert before == after

    def test_a_failing_core_pack_does_not_stop_the_platform(self, platform_db, tmp_path,
                                                            monkeypatch, caplog):
        from experiments import store

        def boom(conn):
            raise RuntimeError("pack broken")

        monkeypatch.setattr(store, "ensure_core_pack", boom)
        monkeypatch.setattr("experiments.indexer.make_index_client", FakeIndexClient)
        with caplog.at_level(logging.ERROR, logger="web_interface.experiments_routes"):
            app = _identity_app(platform_db, tmp_path, monkeypatch, extra_yaml=on_yaml())
        assert app.extensions["experiments"]["settings"].enabled is True
        assert any("core pack" in r.getMessage() for r in caplog.records)
        # The advisory lock was released: a second start does not hang.
        other = tmp_path / "other"
        other.mkdir()
        _identity_app(platform_db, other, monkeypatch, extra_yaml=on_yaml())

    def test_the_machine_api_bypasses_the_session_gate(self, experiments_app):
        """Without a session the answer is the token refusal (the machine API
        ran), not the session gate's 'Anmeldung erforderlich'."""
        r = experiments_app.test_client().get("/api/experiments/v1/ping")
        assert r.status_code == 401
        assert r.get_json()["error"] == ("Ung\u00fcltiger oder abgelaufener "
                                         "Zugangsschl\u00fcssel.")

    def test_the_session_api_does_not(self, experiments_app):
        r = experiments_app.test_client().get("/api/experiments/meta")
        assert r.status_code == 401 and r.get_json()["error"] == "Anmeldung erforderlich"

    def test_pages_are_not_cached(self, experimenter_client):
        for path in ("/experiments", "/experiments/verwaltung"):
            assert "no-store" in experimenter_client.get(path).headers["Cache-Control"]

    def test_the_request_size_limit(self, experiments_app):
        assert experiments_app.config["MAX_CONTENT_LENGTH"] == 32 * 1024 * 1024


@needs_db
class TestWiringOptions:
    def test_a_runner_url_builds_the_runner_client(self, platform_db, tmp_path, monkeypatch):
        from experiments.runner_client import RunnerClient

        monkeypatch.setattr("experiments.indexer.make_index_client", FakeIndexClient)
        monkeypatch.setattr(RunnerClient, "health", lambda self: {
            "configured": True, "ok": True, "languages": {"python": "3.11"}, "busy": 0})
        app = _identity_app(platform_db, tmp_path, monkeypatch,
                            extra_yaml=on_yaml(runner="unix:///run/experiments-runner/runner.sock"))
        runner = app.extensions["experiments"]["runner"]
        assert isinstance(runner, RunnerClient)
        assert runner.url == "unix:///run/experiments-runner/runner.sock"
        assert runner.timeout_seconds == 90

    def test_meta_reports_the_runner(self, platform_db, tmp_path, monkeypatch, identity_repo):
        from conftest import _person
        from experiments.runner_client import RunnerClient

        monkeypatch.setattr("experiments.indexer.make_index_client", FakeIndexClient)
        monkeypatch.setattr(RunnerClient, "health", lambda self: {
            "configured": True, "ok": True, "languages": {"python": "3.11"}, "busy": 1})
        app = _identity_app(platform_db, tmp_path, monkeypatch,
                            extra_yaml=on_yaml(runner="http://127.0.0.1:8090"))
        eva = _person(identity_repo, "eva@knovas.ch", "Eva", "experimenter")
        meta = _signed_in(app, eva.email).get("/api/experiments/meta").get_json()["meta"]
        assert meta["runner"] == {"configured": True, "ok": True, "languages": {"python": "3.11"}}

    def test_workers_start_when_enabled(self, platform_db, tmp_path, monkeypatch):
        from experiments import jobs

        calls = []

        def fake_start(**kwargs):
            calls.append(kwargs)
            return ["worker-a", "worker-b"]

        monkeypatch.setattr(jobs, "start_workers_once", fake_start)
        monkeypatch.setattr("experiments.indexer.make_index_client", FakeIndexClient)
        app = _identity_app(platform_db, tmp_path, monkeypatch, extra_yaml=on_yaml(worker="true"))
        assert app.extensions["experiments"]["workers"] == ["worker-a", "worker-b"]
        assert len(calls) == 1
        call = calls[0]
        assert set(call) == {"settings", "connect", "handlers", "on_dead", "maintenance"}
        assert set(call["handlers"]) == {"index", "unindex", "evaluate", "pipeline"}
        assert callable(call["connect"]) and callable(call["maintenance"])
        # Bound to the test's DSN at construction: the connection reaches the
        # test schema.
        conn = call["connect"]()
        try:
            assert conn.execute("SELECT count(*) FROM exp_jobs").fetchone()[0] >= 0
        finally:
            conn.close()

    def test_workers_stay_off_when_disabled(self, experiments_app, monkeypatch):
        assert experiments_app.extensions["experiments"]["workers"] == []


class TestWorkerConnect:
    def test_the_dsn_when_set(self, monkeypatch):
        import functools

        from web_interface.experiments_routes import worker_connect

        monkeypatch.setenv("PLATFORM_DB_DSN", "postgresql://u:p@db/x")
        connect = worker_connect()
        assert isinstance(connect, functools.partial)
        assert connect.args == ("postgresql://u:p@db/x",)
        assert connect.keywords == {"autocommit": True}

    def test_the_identity_settings_otherwise(self, monkeypatch):
        from identity import db as identity_db
        from web_interface.experiments_routes import worker_connect

        monkeypatch.delenv("PLATFORM_DB_DSN", raising=False)
        assert worker_connect() is identity_db.connect


# -- the pieces in other files ---------------------------------------------------------


class TestBearerEndpointsOnTheGate:
    def test_default_is_empty(self):
        from identity.webauth import IdentityGate

        assert IdentityGate(connect=lambda: None).bearer_endpoints == frozenset()

    def test_names_accumulate(self):
        from identity.webauth import IdentityGate

        gate = IdentityGate(connect=lambda: None)
        gate.allow_bearer_endpoints(["experiments_api.ping"])
        gate.allow_bearer_endpoints(n for n in ["experiments_api.add_run"])
        assert gate.bearer_endpoints == frozenset({"experiments_api.ping",
                                                   "experiments_api.add_run"})

    def test_a_bare_string_is_refused(self):
        from identity.webauth import IdentityGate

        with pytest.raises(TypeError):
            IdentityGate(connect=lambda: None).allow_bearer_endpoints("experiments_api.ping")

    def test_the_guard_stands_aside_without_reading_the_session(self):
        from flask import Flask

        from identity.webauth import IdentityGate

        class Tripwire(IdentityGate):
            def current_session(self):  # pragma: no cover - must not be called
                raise AssertionError("the session was read")

        gate = Tripwire(connect=lambda: None)
        gate.allow_bearer_endpoints(["open"])
        app = Flask(__name__)
        app.testing = True  # let the tripwire propagate instead of becoming a 500
        app.secret_key = "x" * 32
        app.before_request(gate.guard)
        app.add_url_rule("/api/open", "open", lambda: "ok")
        app.add_url_rule("/api/closed", "closed", lambda: "no")
        client = app.test_client()
        assert client.get("/api/open").get_data(as_text=True) == "ok"
        with pytest.raises(AssertionError):
            client.get("/api/closed")


class TestConsoleRoles:
    def test_the_roles_can_be_assigned(self):
        from web_interface.admin import ASSIGNABLE_ROLES

        assert "experimenter" in ASSIGNABLE_ROLES and "experiments_manager" in ASSIGNABLE_ROLES
        assert {"admin", "approver", "ingestion_manager", "member"} <= set(ASSIGNABLE_ROLES)

    @needs_db
    def test_an_administrator_grants_the_role_in_the_console(self, workbench_app, admin_client,
                                                              member, identity_repo):
        page = admin_client.get("/admin/people").get_data(as_text=True)
        assert 'value="experimenter"' in page and 'value="experiments_manager"' in page
        with admin_client._client.session_transaction() as sess:
            token = sess["csrf_token"]
        r = admin_client._client.post("/admin/people/roles", data={
            "csrf_token": token, "user_id": str(member.id),
            "roles": ["member", "experimenter"]})
        assert r.status_code == 200
        assert set(identity_repo.roles_of(member.id)) == {"member", "experimenter"}


class TestConfigYaml:
    """config/config.yaml, read the way the container reads it."""

    ENV = ("EXPERIMENTS_ENABLED", "EXPERIMENTS_POINTER_PREFIX", "EXPERIMENTS_INDEX_ENABLED",
           "EXPERIMENTS_INDEX_PER_MINUTE", "EXPERIMENTS_INDEX_DEBOUNCE_SECONDS",
           "EXPERIMENTS_ACCESS_GROUPS", "EXPERIMENTS_INDEX_UNRESTRICTED", "EXPERIMENTS_RUNNER_URL",
           "EXPERIMENTS_RUNNER_TIMEOUT", "EXPERIMENTS_WORKER_ENABLED",
           "EXPERIMENTS_WORKER_POLL_SECONDS", "EXPERIMENTS_MAX_CSV_ROWS")

    def _settings(self, monkeypatch, **env):
        import pathlib

        from config_loader import ConfigLoader
        from experiments.settings import load_settings

        for name in self.ENV:
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        path = pathlib.Path(__file__).resolve().parents[1] / "config" / "config.yaml"
        return load_settings(ConfigLoader(str(path)), identity_enabled=True)

    def test_defaults(self, monkeypatch):
        s = self._settings(monkeypatch)
        assert s.enabled is False
        assert s.pointer_prefix == "experiments"
        assert s.index_enabled is True and s.index_per_minute == 2
        assert s.index_debounce_seconds == 60
        assert s.index_access_groups == () and s.index_unrestricted is False
        assert s.runner_url == "" and s.runner_timeout_seconds == 90
        assert s.worker_enabled is True and s.worker_poll_seconds == 5.0
        assert s.max_csv_rows == 200_000

    def test_every_variable_is_read(self, monkeypatch):
        s = self._settings(
            monkeypatch, EXPERIMENTS_ENABLED="true", EXPERIMENTS_POINTER_PREFIX="lab",
            EXPERIMENTS_INDEX_ENABLED="false", EXPERIMENTS_INDEX_PER_MINUTE="3",
            EXPERIMENTS_INDEX_DEBOUNCE_SECONDS="30", EXPERIMENTS_ACCESS_GROUPS="exp; team",
            EXPERIMENTS_INDEX_UNRESTRICTED="true",
            EXPERIMENTS_RUNNER_URL="unix:///run/experiments-runner/runner.sock",
            EXPERIMENTS_RUNNER_TIMEOUT="120", EXPERIMENTS_WORKER_ENABLED="false",
            EXPERIMENTS_WORKER_POLL_SECONDS="2", EXPERIMENTS_MAX_CSV_ROWS="5000")
        assert s.enabled is True and s.pointer_prefix == "lab"
        assert s.index_enabled is False and s.index_per_minute == 3
        assert s.index_debounce_seconds == 30
        assert s.index_access_groups == ("exp", "team") and s.index_unrestricted is True
        assert s.runner_url == "unix:///run/experiments-runner/runner.sock"
        assert s.runner_timeout_seconds == 120
        assert s.worker_enabled is False and s.worker_poll_seconds == 2.0
        assert s.max_csv_rows == 5000

    def test_set_but_empty_means_default(self, monkeypatch):
        s = self._settings(monkeypatch, EXPERIMENTS_INDEX_ENABLED="",
                           EXPERIMENTS_WORKER_ENABLED="", EXPERIMENTS_ENABLED="")
        assert s.enabled is False and s.index_enabled is True and s.worker_enabled is True
