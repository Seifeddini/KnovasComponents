"""KC-0.1: the Platform's settings, editable on the settings page.

Precedence (deployment pin, page, config.yaml default), validation of each type, secrets that never
leave encrypted storage, a change that applies without a restart, the Test buttons, the migration,
and the page itself: admin only, CSRF-guarded, audited.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

import platform_settings as ps
from graph_views import service as views
from graph_views.github_repo import GitHubError
from views_fixture import KNOVAS_BUNDLE, KNOVAS_SOURCE, LAWFIRM_BUNDLE, FakeViewsClient, client_zero_graph

TOKEN = "github_pat_" + "x" * 40                       # a made-up token: never a real one in a test
ROLES = ("admin", "approver", "ingestion_manager", "member")


class _Config:
    """ConfigLoader's surface: values after substitution, and the raw file for the defaults."""

    def __init__(self, path: Path, values: dict):
        self.config_path = str(path)
        self._values = values

    def get(self, key, default=None):
        node = self._values
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def get_int(self, key, default=0):
        return int(self.get(key, default))

    def get_float(self, key, default=0.0):
        return float(self.get(key, default))


RAW = """views:
  config_source: "${VIEWS_CONFIG_SOURCE:-}"
  profile_roles: "${VIEWS_PROFILE_ROLES:-}"
  map_fact_budget: "${VIEWS_MAP_FACT_BUDGET:-120}"
  ttl_seconds: "${VIEWS_TTL_SECONDS:-60}"
  definitions_ttl_seconds: "${VIEWS_DEFINITIONS_TTL_SECONDS:-600}"
  edge_limit: "${VIEWS_EDGE_LIMIT:-100000}"
  edge_budget_seconds: "${VIEWS_EDGE_BUDGET_SECONDS:-2}"
  change_requests:
    github_repo: "${VIEWS_KB_GITHUB_REPO:-owner/repo}"
    github_token_file: "${VIEWS_KB_GITHUB_TOKEN_FILE:-/nonexistent/token}"
"""


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(RAW, encoding="utf-8")
    return _Config(path, {"views": {"config_source": "", "profile_roles": "", "map_fact_budget": "120",
                                    "ttl_seconds": "60", "definitions_ttl_seconds": "600", "edge_limit": "100000",
                                    "edge_budget_seconds": "2",
                                    "change_requests": {"github_repo": "owner/repo",
                                                        "github_token_file": "/nonexistent/token"}}})


class _MemoryStore:
    """The page layer in memory: what SettingStore does, without a database."""

    def __init__(self):
        self.rows_, self.log = {}, []

    def rows(self, tenant):
        return {k: v for (t, k), v in self.rows_.items() if t in (tenant, "")}

    def put(self, key, tenant, value, user, old=None):
        self.rows_[(tenant, key)] = {"value": value, "secret": None, "updated_by": user.email, "updated_at": "now"}
        self.log.append((key, old, value))

    def put_secret(self, key, tenant, sealed, user):
        self.rows_[(tenant, key)] = {"value": None, "secret": sealed, "updated_by": user.email, "updated_at": "now"}
        self.log.append((key, "geändert", "geändert"))

    def clear(self, key, tenant, user, old=None):
        self.rows_.pop((tenant, key), None)
        self.rows_.pop(("", key), None)
        self.log.append((key, old, None))


class _User:
    id = "00000000-0000-0000-0000-000000000001"
    email = "admin@example.test"
    roles = frozenset({"admin"})


def _resolver(config, store=None, environ=None, box=None, **kw):
    store = store if store is not None else _MemoryStore()
    return ps.Resolver(config, tenant="tenant-a", store=lambda: store, environ=environ if environ is not None else {},
                       box=box if box is not None else ps.SecretBox(Fernet.generate_key().decode()), **kw), store


# -- the registry ---------------------------------------------------------------------------------------


class TestRegistry:
    def test_the_views_settings_and_the_token_are_registered(self):
        assert set(ps.REGISTRY) == {
            "views.config_source", "views.profile_roles", "views.map_fact_budget", "views.ttl_seconds",
            "views.definitions_ttl_seconds", "views.edge_limit", "views.edge_budget_seconds",
            "views.change_requests.github_token"}
        for s in ps.REGISTRY.values():
            assert s.label and s.help and s.kind in ps.KINDS and s.scope in ("tenant", "installation")

    def test_each_settings_environment_variable_is_the_one_config_yaml_reads(self):
        raw = (Path(__file__).resolve().parents[1] / "config" / "config.yaml").read_text(encoding="utf-8")
        for s in ps.REGISTRY.values():
            if s.kind != "secret":
                assert any(f"${{{e}:-" in raw for e in s.env), s.key


# -- precedence -------------------------------------------------------------------------------------------


class TestPrecedence:
    def test_without_page_or_pin_the_config_yaml_default_applies(self, config):
        r, _ = _resolver(config)
        e = r.effective("views.map_fact_budget")
        assert (e.value, e.source) == (120, "default") and not e.pinned

    def test_a_page_value_wins_over_the_default(self, config):
        r, _ = _resolver(config)
        r.save(_User(), {"views.map_fact_budget": "80"}, roles=ROLES)
        e = r.effective("views.map_fact_budget")
        assert (e.value, e.source, e.detail["updated_by"]) == (80, "page", "admin@example.test")

    def test_a_deployment_pin_wins_and_is_read_only(self, config):
        r, store = _resolver(config, environ={"VIEWS_MAP_FACT_BUDGET": "50"})
        e = r.effective("views.map_fact_budget")
        assert (e.value, e.source, e.pinned) == (50, "deployment", True)
        assert "VIEWS_MAP_FACT_BUDGET" in e.detail["reason"]
        with pytest.raises(ps.Pinned):
            r.save(_User(), {"views.map_fact_budget": "80"}, roles=ROLES)
        assert store.rows_ == {}

    def test_an_empty_environment_variable_does_not_pin(self, config):
        r, _ = _resolver(config, environ={"VIEWS_CONFIG_SOURCE": ""})
        assert r.effective("views.config_source").source == "default"

    def test_clearing_a_page_value_falls_back_to_the_default(self, config):
        r, _ = _resolver(config)
        r.save(_User(), {"views.ttl_seconds": "30"}, roles=ROLES)
        r.save(_User(), {"views.ttl_seconds": None}, roles=ROLES)
        assert (r.effective("views.ttl_seconds").value, r.effective("views.ttl_seconds").source) == (60, "default")

    def test_the_token_pins_from_the_environment_or_a_file(self, config, tmp_path):
        r, _ = _resolver(config, environ={"KB_GITHUB_TOKEN": TOKEN})
        e = r.effective("views.change_requests.github_token")
        assert e.source == "deployment" and e.value is None and r.secret("views.change_requests.github_token") == TOKEN
        f = tmp_path / "token"
        f.write_text(TOKEN + "\n", encoding="utf-8")
        config._values["views"]["change_requests"]["github_token_file"] = str(f)
        r2, _ = _resolver(config)
        assert r2.effective("views.change_requests.github_token").source == "deployment"
        assert r2.secret("views.change_requests.github_token") == TOKEN


# -- validation ---------------------------------------------------------------------------------------------


class TestValidation:
    @pytest.mark.parametrize("key, raw, says", [
        ("views.config_source", "https://example.test/bundle.json", "ungültig"),
        ("views.config_source", "github:owner/repo:no-ref.json", "ungültig"),
        ("views.map_fact_budget", "zwölf", "Zahl"),
        ("views.map_fact_budget", "-1", "mindestens"),
        ("views.ttl_seconds", "1.5", "ganze Zahl"),
        ("views.edge_limit", "999999999", "höchstens"),
        ("views.edge_budget_seconds", "0", "mindestens"),
        ("views.profile_roles", "owner=CTO", "Unbekannte Rolle"),
        ("views.profile_roles", "admin", "Rolle=Profil"),
        ("views.change_requests.github_token", "has a space", "Leerzeichen"),
        ("views.change_requests.github_token", "", "leer"),
    ])
    def test_a_bad_value_is_refused_with_its_reason(self, key, raw, says):
        with pytest.raises(ps.SettingError, match=says):
            ps.clean(ps.REGISTRY[key], raw, roles=ROLES)

    @pytest.mark.parametrize("key, raw, value", [
        ("views.config_source", " file:/data/bundle.json ", "file:/data/bundle.json"),
        ("views.config_source", "github:owner/repo@main:docs/bundle.json", "github:owner/repo@main:docs/bundle.json"),
        ("views.config_source", "", ""),
        ("views.map_fact_budget", "80", 80),
        ("views.edge_budget_seconds", "1.5", 1.5),
        ("views.profile_roles", "member=Engineer; admin=CTO, Engineer", "admin=CTO, Engineer; member=Engineer"),
        ("views.profile_roles", {"admin": ["CTO"], "member": "Engineer"}, "admin=CTO; member=Engineer"),
        ("views.profile_roles", {"admin": [], "member": ""}, ""),
    ])
    def test_a_good_value_is_stored_in_its_canonical_form(self, key, raw, value):
        assert ps.clean(ps.REGISTRY[key], raw, roles=ROLES) == value

    def test_a_choice_is_one_of_its_choices(self):
        s = ps.Setting("x.mode", "Modus", "Hilfe", "choice", "installation", env=(), choices=("a", "b"))
        assert ps.clean(s, "a", roles=ROLES) == "a"
        with pytest.raises(ps.SettingError, match="eines von a, b"):
            ps.clean(s, "c", roles=ROLES)

    def test_nothing_is_saved_when_one_value_of_a_save_is_bad(self, config):
        r, store = _resolver(config)
        with pytest.raises(ps.SettingError):
            r.save(_User(), {"views.ttl_seconds": "30", "views.map_fact_budget": "viele"}, roles=ROLES)
        assert store.rows_ == {}

    def test_an_unknown_key_is_refused(self, config):
        r, _ = _resolver(config)
        with pytest.raises(ps.SettingError, match="Unbekannte Einstellung"):
            r.save(_User(), {"views.no_such": "1"}, roles=ROLES)


# -- secrets ------------------------------------------------------------------------------------------------


class TestSecrets:
    def test_the_token_is_stored_encrypted_and_never_returned(self, config):
        r, store = _resolver(config)
        r.save(_User(), {"views.change_requests.github_token": TOKEN}, roles=ROLES)
        row = store.rows_[("tenant-a", "views.change_requests.github_token")]
        assert row["value"] is None and TOKEN.encode() not in row["secret"]
        assert r.secret("views.change_requests.github_token") == TOKEN
        page = json.dumps([e for e in r.entries()], default=str)
        assert TOKEN not in page
        entry = next(e for e in r.entries() if e["key"] == "views.change_requests.github_token")
        assert entry["value"] is None and entry["set"] is True and entry["source"] == "page"

    def test_without_a_settings_key_a_secret_cannot_be_saved(self, config):
        r, store = _resolver(config, box=ps.SecretBox(None))
        with pytest.raises(ps.NoSecretKey, match="PLATFORM_SETTINGS_KEY"):
            r.save(_User(), {"views.change_requests.github_token": TOKEN}, roles=ROLES)
        assert store.rows_ == {}
        assert next(e for e in r.entries() if e["key"] == "views.change_requests.github_token")["secrets"] is False

    def test_the_key_comes_from_the_environment_or_a_file_and_a_bad_one_is_named(self, tmp_path):
        key = Fernet.generate_key().decode()
        assert ps.SecretBox.from_env({"PLATFORM_SETTINGS_KEY": key}).available
        f = tmp_path / "k"
        f.write_text(key + "\n", encoding="utf-8")
        assert ps.SecretBox.from_env({"PLATFORM_SETTINGS_KEY_FILE": str(f)}).available
        bad = ps.SecretBox.from_env({"PLATFORM_SETTINGS_KEY": "not-a-key"})
        assert not bad.available and "ungültig" in bad.reason
        assert "nicht gesetzt" in ps.SecretBox.from_env({}).reason

    def test_a_secret_sealed_with_another_key_reads_as_unset(self, config):
        r, store = _resolver(config)
        r.save(_User(), {"views.change_requests.github_token": TOKEN}, roles=ROLES)
        other, _ = _resolver(config, store=store)
        assert other.secret("views.change_requests.github_token") is None
        entry = next(e for e in other.entries() if e["key"] == "views.change_requests.github_token")
        assert entry["set"] is True and entry["readable"] is False

    def test_the_token_is_never_logged(self, config, caplog):
        caplog.set_level(logging.DEBUG)
        r, _ = _resolver(config)
        r.save(_User(), {"views.change_requests.github_token": TOKEN}, roles=ROLES)
        r.secret("views.change_requests.github_token")
        r.entries()
        assert TOKEN not in caplog.text

    def test_audit_says_changed_for_a_secret_and_old_and_new_otherwise(self, config):
        r, store = _resolver(config)
        r.save(_User(), {"views.map_fact_budget": "80", "views.change_requests.github_token": TOKEN}, roles=ROLES)
        assert ("views.map_fact_budget", 120, 80) in store.log
        assert ("views.change_requests.github_token", "geändert", "geändert") in store.log
        assert TOKEN not in json.dumps(store.log)


# -- a change applies without a restart -------------------------------------------------------------------


class _NoWait:
    def acquire(self):
        return None


class TestNoRestart:
    def test_view_settings_follow_the_page(self, config):
        r, _ = _resolver(config, ttl=0)
        assert r.view_settings().map_fact_budget == 120
        r.save(_User(), {"views.map_fact_budget": "40", "views.config_source": KNOVAS_SOURCE}, roles=ROLES)
        s = r.view_settings()
        assert (s.map_fact_budget, s.config_source) == (40, KNOVAS_SOURCE)

    def test_the_view_service_reads_the_new_bundle_source_and_ttl(self, config):
        r, _ = _resolver(config, ttl=0)
        r.save(_User(), {"views.config_source": KNOVAS_SOURCE}, roles=ROLES)
        client = FakeViewsClient(client_zero_graph())
        svc = views.ViewService(lambda: client, settings_provider=r.view_settings, bucket=_NoWait(), markers=lambda: set())
        assert svc.bundle().label == "KNOVAS"
        r.save(_User(), {"views.config_source": f"file:{LAWFIRM_BUNDLE}", "views.ttl_seconds": "15"}, roles=ROLES)
        assert svc.bundle().label == "KANZLEI-X"
        assert svc.settings.ttl_seconds == 15 and svc.cache._settings.ttl_seconds == 15

    def test_a_cached_value_lasts_its_ttl(self, config):
        now = [0.0]
        r, store = _resolver(config, ttl=5, now=lambda: now[0])
        assert r.view_settings().ttl_seconds == 60
        store.rows_[("", "views.ttl_seconds")] = {"value": 20, "secret": None, "updated_by": "x", "updated_at": "now"}
        assert r.view_settings().ttl_seconds == 60          # another worker's change: seen after the TTL
        now[0] = 6
        assert r.view_settings().ttl_seconds == 20


# -- the Test buttons -----------------------------------------------------------------------------------------


class TestButtons:
    def test_a_good_bundle_source_shows_version_hash_and_counts(self):
        got = ps.test_bundle_source(KNOVAS_SOURCE)
        doc = json.loads(KNOVAS_BUNDLE.read_text(encoding="utf-8"))
        assert got["ok"] and got["format_version"] == 1 and got["content_hash"] == doc["header"]["content_hash"]
        assert got["counts"] == {"types": len(doc["sections"]["types"]["node_types"]),
                                 "processes": len(doc["sections"]["processes"]["processes"]),
                                 "profiles": len(doc["sections"]["profiles"])}

    def test_a_broken_bundle_source_shows_the_exact_error(self, tmp_path):
        doc = json.loads(LAWFIRM_BUNDLE.read_text(encoding="utf-8"))
        doc["sections"]["relations"]["relations"] = []
        (tmp_path / "bundle.json").write_text(json.dumps(doc), encoding="utf-8")
        got = ps.test_bundle_source(f"file:{tmp_path / 'bundle.json'}")
        assert not got["ok"] and "Prüfsumme stimmt nicht" in got["error"]
        assert "nicht gefunden" in ps.test_bundle_source(f"file:{tmp_path / 'none.json'}")["error"]
        assert "ungültig" in ps.test_bundle_source("ftp://x")["error"]

    def test_the_token_test_checks_read_access_and_never_echoes_the_token(self):
        class Repo:
            def __init__(self, status):
                self.status = status

            def access(self):
                if self.status != 200:
                    raise GitHubError("read repository", self.status)
                return {"read": True, "write": False}

        good = ps.test_repository_access("owner/repo", TOKEN, lambda repo, token: Repo(200))
        assert good == {"ok": True, "repository": "owner/repo", "read": True, "write": False}
        bad = ps.test_repository_access("owner/repo", TOKEN, lambda repo, token: Repo(401))
        assert not bad["ok"] and "HTTP 401" in bad["error"] and TOKEN not in json.dumps(bad)
        assert "kein Token" in ps.test_repository_access("owner/repo", None, lambda repo, token: Repo(200))["error"]


# -- the database ------------------------------------------------------------------------------------------------

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable  # noqa: E402

needs_db = pytest.mark.skipif(not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


@needs_db
class TestMigration:
    def test_on_an_empty_database(self, platform_db):
        cols = {r[0] for r in platform_db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'platform_settings' "
            "AND table_schema = current_schema()").fetchall()}
        assert cols == {"tenant", "key", "value", "secret", "updated_by", "updated_at"}

    def test_on_an_existing_database_with_data(self):
        import uuid

        import psycopg

        from identity import migrate

        schema = f"t{uuid.uuid4().hex[:12]}"
        with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as conn:
            conn.execute(f'CREATE SCHEMA "{schema}"')
            conn.execute(f'SET search_path TO "{schema}"')
            try:
                before = [m for m in migrate.discover() if m.version < "0009"]
                migrate.apply(conn, before)
                conn.execute("INSERT INTO saved_views (audience, name, source_kind, source_key, render_kind) "
                             "VALUES ('shared', 'Board', 'process', 'PROC-x', 'board')")
                assert [m.version for m in migrate.pending(conn)] == ["0009_platform_settings"]
                migrate.apply(conn)
                assert conn.execute("SELECT count(*) FROM saved_views").fetchone()[0] == 1
                assert conn.execute("SELECT count(*) FROM platform_settings").fetchone()[0] == 0
            finally:
                conn.execute(f'DROP SCHEMA "{schema}" CASCADE')

    def test_the_store_keeps_values_and_sealed_secrets_with_their_author(self, platform_db, identity_repo):
        user = identity_repo.create(email="admin@kanzlei.ch", display_name="A", password="korrektes-pferd-batterie")
        store = ps.SettingStore(platform_db)
        store.put("views.ttl_seconds", "", 30, user, old=60)
        store.put_secret("views.change_requests.github_token", "tenant-a", b"sealed-bytes", user)
        rows = store.rows("tenant-a")
        assert rows["views.ttl_seconds"]["value"] == 30 and rows["views.ttl_seconds"]["updated_by"] == "admin@kanzlei.ch"
        assert bytes(rows["views.change_requests.github_token"]["secret"]) == b"sealed-bytes"
        assert store.rows("tenant-b").keys() == {"views.ttl_seconds"}          # installation-wide values only


# -- the page, through the real app ------------------------------------------------------------------------------


def _csrf_from(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start:html.index('"', start)]


def _sign_in(client, email, password="korrektes-pferd-batterie"):
    page = client.get("/login")
    client.post("/login", data={"login_name": email, "password": password,
                                "csrf_token": _csrf_from(page.data.decode("utf-8"))})


@pytest.fixture
def settings_key(monkeypatch):
    monkeypatch.setenv("PLATFORM_SETTINGS_KEY", Fernet.generate_key().decode())
    for name in ("VIEWS_MAP_FACT_BUDGET", "VIEWS_CONFIG_SOURCE", "KB_GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def people(identity_repo):
    admin = identity_repo.create(email="chef@kanzlei.ch", display_name="Chef", password="korrektes-pferd-batterie")
    identity_repo.grant_role(admin.id, "admin")
    member = identity_repo.create(email="ida@kanzlei.ch", display_name="Ida", password="korrektes-pferd-batterie")
    identity_repo.grant_role(member.id, "member")
    return admin, member


def _token(client):
    return _csrf_from(client.get("/settings").data.decode("utf-8"))


@needs_db
class TestPage:
    def test_an_admin_sees_the_section_and_a_member_does_not(self, settings_key, identity_client, people):
        _sign_in(identity_client, "chef@kanzlei.ch")
        page = identity_client.get("/settings").data.decode("utf-8")
        assert "Ansichten und Wissensbasis" in page and 'id="platformSettings"' in page
        body = identity_client.get("/api/settings/platform").get_json()
        assert {e["key"] for e in body["settings"]} == set(ps.REGISTRY) and body["can_edit"]
        identity_client.post("/logout", data={"csrf_token": _token(identity_client)})
        _sign_in(identity_client, "ida@kanzlei.ch")
        assert "Ansichten und Wissensbasis" not in identity_client.get("/settings").data.decode("utf-8")
        assert identity_client.get("/api/settings/platform").status_code == 403

    def test_a_member_cannot_change_anything(self, settings_key, identity_client, people, platform_db):
        _sign_in(identity_client, "ida@kanzlei.ch")
        r = identity_client.post("/api/settings/platform", json={"values": {"views.ttl_seconds": "5"}},
                                 headers={"X-CSRF-Token": _token(identity_client)})
        assert r.status_code == 403
        assert platform_db.execute("SELECT count(*) FROM platform_settings").fetchone()[0] == 0

    def test_saving_needs_the_csrf_token(self, settings_key, identity_client, people):
        _sign_in(identity_client, "chef@kanzlei.ch")
        r = identity_client.post("/api/settings/platform", json={"values": {"views.ttl_seconds": "5"}})
        assert r.status_code == 403

    def test_an_admin_saves_it_is_audited_and_the_token_never_comes_back(self, settings_key, identity_client, people,
                                                                          platform_db, caplog):
        from identity import audit

        caplog.set_level(logging.DEBUG)
        _sign_in(identity_client, "chef@kanzlei.ch")
        r = identity_client.post("/api/settings/platform", headers={"X-CSRF-Token": _token(identity_client)},
                                 json={"values": {"views.map_fact_budget": "80",
                                                  "views.change_requests.github_token": TOKEN}})
        assert r.status_code == 200, r.get_json()
        assert TOKEN not in r.get_data(as_text=True)
        assert TOKEN not in identity_client.get("/api/settings/platform").get_data(as_text=True)
        assert TOKEN not in identity_client.get("/settings").get_data(as_text=True)
        entries = {e["key"]: e for e in r.get_json()["settings"]}
        assert entries["views.map_fact_budget"]["value"] == 80 and entries["views.map_fact_budget"]["source"] == "page"
        assert entries["views.change_requests.github_token"]["set"] is True
        rows = audit.recent(platform_db, action="settings.changed")
        details = {row["detail"]["key"]: row["detail"] for row in rows}
        assert details["views.map_fact_budget"]["old"] == 120 and details["views.map_fact_budget"]["new"] == 80
        assert details["views.change_requests.github_token"]["new"] == "geändert"
        assert {row["actor_email"] for row in rows} == {"chef@kanzlei.ch"}
        assert TOKEN not in json.dumps(rows, default=str) and TOKEN not in caplog.text
        sealed = platform_db.execute("SELECT secret FROM platform_settings WHERE key = %s",
                                     ("views.change_requests.github_token",)).fetchone()[0]
        assert TOKEN.encode() not in bytes(sealed)

    def test_a_pinned_setting_is_read_only(self, settings_key, identity_client, people, monkeypatch):
        monkeypatch.setenv("VIEWS_MAP_FACT_BUDGET", "33")
        _sign_in(identity_client, "chef@kanzlei.ch")
        entry = next(e for e in identity_client.get("/api/settings/platform").get_json()["settings"]
                     if e["key"] == "views.map_fact_budget")
        assert entry["pinned"] and entry["source"] == "deployment" and entry["value"] == 33
        assert "VIEWS_MAP_FACT_BUDGET" in entry["reason"]
        r = identity_client.post("/api/settings/platform", json={"values": {"views.map_fact_budget": "80"}},
                                 headers={"X-CSRF-Token": _token(identity_client)})
        assert r.status_code == 409 and "VIEWS_MAP_FACT_BUDGET" in r.get_json()["error"]

    def test_the_test_buttons(self, settings_key, identity_client, people):
        _sign_in(identity_client, "chef@kanzlei.ch")
        h = {"X-CSRF-Token": _token(identity_client)}
        good = identity_client.post("/api/settings/platform/test/views.config_source", headers=h,
                                    json={"value": KNOVAS_SOURCE}).get_json()
        assert good["ok"] and good["counts"]["profiles"] == 4
        bad = identity_client.post("/api/settings/platform/test/views.config_source", headers=h,
                                   json={"value": "file:/no/such/bundle.json"}).get_json()
        assert not bad["ok"] and "nicht gefunden" in bad["error"]

    def test_a_saved_bundle_source_applies_to_the_views_without_restart(self, settings_key, identity_client, people,
                                                                         identity_app):
        _sign_in(identity_client, "chef@kanzlei.ch")
        h = {"X-CSRF-Token": _token(identity_client)}
        identity_client.post("/api/settings/platform", headers=h, json={"values": {"views.config_source": KNOVAS_SOURCE}})
        assert identity_app.extensions["views"].settings.config_source == KNOVAS_SOURCE
        identity_client.post("/api/settings/platform", headers=h,
                             json={"values": {"views.config_source": f"file:{LAWFIRM_BUNDLE}"}})
        assert identity_app.extensions["views"].settings.config_source == f"file:{LAWFIRM_BUNDLE}"
