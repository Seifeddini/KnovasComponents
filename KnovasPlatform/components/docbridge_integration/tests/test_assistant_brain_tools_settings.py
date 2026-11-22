"""PMVP-20 brain-mcp: the three settings of <<Brain-Werkzeuge>> on the settings page.

The registry (the mode, passages by default and pinned by ASSISTANT_MODE; the gateway's address, only https under
knovas.ch; the gateway key, a secret), the page (the warning line under the mode, the problems a deployment's choice
leaves, the refusal of brain_mcp without anthropic or a key, in the page's own words, nothing saved) and the key
(encrypted at rest, never shown back, never in the audit log or a log line). No test reaches Anthropic or a gateway.
This file is ASCII-only.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from flask import Flask

import platform_settings as ps
from assistant import providers
from conftest import platform_db_reachable
from test_assistant_brain_tools import GW_KEY, KEY
from web_interface.settings_routes import create_settings_blueprint

ROLES = ("admin", "approver", "ingestion_manager", "member")
WARNING = ("Brain-Werkzeuge lesen alles, was der Schl\u00fcssel Ihres Unternehmens sieht, unabh\u00e4ngig von den "
           "Rollen der Person. Nur ohne \u00abEin\u00bb verwenden.")
needs_db = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL for the Platform's tests")


@pytest.fixture(autouse=True)
def no_real_anthropic(monkeypatch):
    def refuse(**kwargs):
        raise AssertionError("a test tried to build a real Anthropic client")

    monkeypatch.setattr(providers, "_sdk_client", refuse)


class _Config:
    def __init__(self, path: Path):
        self.config_path = str(path)

    def get(self, key, default=None):
        return default


class _MemoryStore:
    def __init__(self):
        self.rows_, self.log = {}, []

    def rows(self, tenant):
        return {k: v for (t, k), v in self.rows_.items() if t in (tenant, "")}

    def put(self, key, tenant, value, user, old=None):
        self.rows_[(tenant, key)] = {"value": value, "secret": None, "updated_by": user.email, "updated_at": "now"}
        self.log.append((key, old, value))

    def put_secret(self, key, tenant, sealed, user):
        self.rows_[(tenant, key)] = {"value": None, "secret": sealed, "updated_by": user.email, "updated_at": "now"}
        self.log.append((key, ps.SECRET_SET, ps.SECRET_SET))

    def clear(self, key, tenant, user, old=None):
        self.rows_.pop((tenant, key), None)
        self.log.append((key, old, None))


class _Person:
    def __init__(self, *roles):
        self.id = "00000000-0000-0000-0000-00000000000" + str(len(roles))
        self.email = f"{'-'.join(roles) or 'niemand'}@example.test"
        self.roles = frozenset(roles)


class _Gate:
    def __init__(self, user):
        self.user = user

    def current_user(self):
        return self.user


ADMIN, MEMBER = _Person("admin"), _Person("member")


@pytest.fixture
def make_resolver(tmp_path):
    def build(environ=None):
        raw = (Path(__file__).resolve().parents[1] / "config" / "config.yaml").read_text(encoding="utf-8")
        path = tmp_path / "config.yaml"
        path.write_text(raw, encoding="utf-8")
        store = _MemoryStore()
        r = ps.Resolver(_Config(path), tenant="tenant-a", store=lambda: store, environ=environ or {},
                        box=ps.SecretBox(Fernet.generate_key().decode()))
        r.store = store
        return r

    return build


@pytest.fixture
def resolver(make_resolver):
    return make_resolver()


def _page(resolver, user=ADMIN):
    app = Flask(__name__)
    app.register_blueprint(create_settings_blueprint(
        resolver, gate=_Gate(user), roles=lambda: list(ROLES), profiles=lambda: [],
        bundle_github=lambda repo: None, repository=lambda: "", make_repo=lambda repo, token: None))
    return app.test_client()


def _post(client, values):
    return client.post("/api/settings/platform", json={"values": values})


ANTHROPIC_READY = {"assistant.provider": "anthropic", "assistant.anthropic_api_key": KEY}


# --- the registry --------------------------------------------------------------------------------------------------


def test_the_three_entries_are_registered_for_the_whole_installation():
    mode, url, key = (ps.REGISTRY[k] for k in ("assistant.mode", "assistant.brain_mcp_url", "assistant.brain_mcp_key"))
    assert all(s.scope == "installation" and s.label and s.help for s in (mode, url, key))
    assert mode.kind == "choice" and mode.choices == ("passages", "brain_mcp") and mode.fallback == "passages"
    assert mode.env == ("ASSISTANT_MODE",)
    assert url.kind == "text" and url.env == ("ASSISTANT_BRAIN_MCP_URL",)
    assert url.fallback == "https://mcp.knovas.ch/mcp"
    assert key.kind == "secret" and key.env == () and key.file_key is None


def test_the_defaults_are_passages_and_the_knovas_gateway(resolver):
    assert resolver.value("assistant.mode") == "passages"
    assert resolver.value("assistant.brain_mcp_url") == "https://mcp.knovas.ch/mcp"
    assert resolver.secret("assistant.brain_mcp_key") is None


def test_the_environment_pins_the_mode_and_the_address(make_resolver):
    r = make_resolver({"ASSISTANT_MODE": "brain_mcp", "ASSISTANT_BRAIN_MCP_URL": "https://test.knovas.ch/mcp"})
    assert r.value("assistant.mode") == "brain_mcp" and r.effective("assistant.mode").pinned
    assert r.value("assistant.brain_mcp_url") == "https://test.knovas.ch/mcp"
    with pytest.raises(ps.Pinned):
        r.save(ADMIN, {"assistant.mode": "passages"}, roles=ROLES)


@pytest.mark.parametrize("address", [
    "http://mcp.knovas.ch/mcp", "https://mcp.example.com/mcp", "https://mcp.knovas.ch.example.com/mcp",
    "https://xknovas.ch/mcp", "https://someone@mcp.knovas.ch/mcp", "https://mcp.knovas.ch/mcp?k=1",
    "https://mcp.knovas.ch/mcp#x", "https://mcp.knovas.ch%2f.example.com/", "https://mcp.knovas.ch\\@example.com/",
    "ftp://mcp.knovas.ch/mcp", "mcp.knovas.ch/mcp", "https://mcp.kn\u00f6vas.ch/mcp", "https://mcp.knovas.ch/\u00fc",
])
def test_an_address_outside_knovas_ch_or_without_https_is_refused(resolver, address):
    with pytest.raises(ps.SettingError):
        resolver.save(ADMIN, {"assistant.brain_mcp_url": address}, roles=ROLES)
    assert resolver.value("assistant.brain_mcp_url") == "https://mcp.knovas.ch/mcp"


@pytest.mark.parametrize("address", ["https://mcp.knovas.ch/mcp", "https://gateway.knovas.ch/mcp",
                                     "https://knovas.ch/mcp", "https://mcp.knovas.ch:8443/mcp"])
def test_an_https_address_under_knovas_ch_is_kept(resolver, address):
    resolver.save(ADMIN, {"assistant.brain_mcp_url": address}, roles=ROLES)
    assert resolver.value("assistant.brain_mcp_url") == address


# --- the key: encrypted, never shown, never logged -----------------------------------------------------------------


def test_the_gateway_key_is_sealed_at_rest_and_never_comes_back(resolver, caplog):
    caplog.set_level(logging.DEBUG)
    client = _page(resolver)
    r = _post(client, {"assistant.brain_mcp_key": GW_KEY})
    assert r.status_code == 200, r.get_json()
    row = resolver.store.rows_[("", "assistant.brain_mcp_key")]
    assert row["value"] is None and GW_KEY.encode() not in bytes(row["secret"])
    assert resolver.box.open(row["secret"]) == GW_KEY == resolver.secret("assistant.brain_mcp_key")
    entries = {e["key"]: e for e in r.get_json()["settings"]}
    assert entries["assistant.brain_mcp_key"]["set"] is True and entries["assistant.brain_mcp_key"]["value"] is None
    for body in (r.get_data(as_text=True), client.get("/api/settings/platform").get_data(as_text=True)):
        assert GW_KEY not in body
    assert ("assistant.brain_mcp_key", ps.SECRET_SET, ps.SECRET_SET) in resolver.store.log
    assert GW_KEY not in caplog.text


def test_without_a_settings_key_the_gateway_key_cannot_be_saved(tmp_path):
    raw = (Path(__file__).resolve().parents[1] / "config" / "config.yaml").read_text(encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(raw, encoding="utf-8")
    store = _MemoryStore()
    r = ps.Resolver(_Config(path), store=lambda: store, environ={}, box=ps.SecretBox(None))
    with pytest.raises(ps.NoSecretKey):
        r.save(ADMIN, {"assistant.brain_mcp_key": GW_KEY}, roles=ROLES)
    assert store.rows_ == {}


def test_a_member_cannot_set_the_mode_or_the_key(resolver):
    client = _page(resolver, MEMBER)
    assert _post(client, {"assistant.brain_mcp_key": GW_KEY}).status_code == 403
    assert client.get("/api/settings/platform").status_code == 403
    assert resolver.store.rows_ == {}


# --- the refusal and the lines of the page -------------------------------------------------------------------------


def test_the_warning_stands_under_the_mode_always(resolver):
    body = _page(resolver).get("/api/settings/platform").get_json()
    assert body["warnings"] == {"assistant.mode": WARNING}
    script = (Path(__file__).resolve().parents[1] / "src" / "web_interface" / "static" / "js"
              / "platform_settings.js").read_text(encoding="utf-8")
    assert "state.warnings[s.key]" in script and "platform-settings-warning" in script


@pytest.mark.parametrize("values, words", [
    ({"assistant.mode": "brain_mcp"}, providers.BRAIN_MCP_NEEDS_ANTHROPIC),
    ({"assistant.mode": "brain_mcp", "assistant.provider": "knovas", "assistant.brain_mcp_key": GW_KEY,
      "assistant.anthropic_api_key": KEY}, providers.BRAIN_MCP_NEEDS_ANTHROPIC),
    ({"assistant.mode": "brain_mcp", "assistant.provider": "anthropic", "assistant.brain_mcp_key": GW_KEY},
     providers.BRAIN_MCP_NEEDS_API_KEY),
    ({"assistant.mode": "brain_mcp", **ANTHROPIC_READY}, providers.BRAIN_MCP_NEEDS_GATEWAY_KEY),
    ({"assistant.mode": "brain_mcp", **ANTHROPIC_READY, "assistant.brain_mcp_key": ""},
     providers.BRAIN_MCP_NEEDS_GATEWAY_KEY),
])
def test_brain_mcp_is_refused_without_anthropic_or_a_key_and_nothing_is_saved(resolver, values, words):
    r = _post(_page(resolver), values)
    assert r.status_code == 400 and r.get_json() == {"success": False, "error": words}
    assert resolver.store.rows_ == {} and resolver.value("assistant.mode") == "passages"


def test_brain_mcp_is_saved_with_anthropic_and_both_keys_at_once_or_one_after_the_other(resolver):
    client = _page(resolver)
    r = _post(client, {"assistant.mode": "brain_mcp", **ANTHROPIC_READY, "assistant.brain_mcp_key": GW_KEY})
    assert r.status_code == 200, r.get_json()
    assert resolver.value("assistant.mode") == "brain_mcp" and r.get_json()["problems"] == {}
    _post(client, {"assistant.mode": None})
    assert resolver.value("assistant.mode") == "passages"
    assert _post(client, {"assistant.mode": "brain_mcp"}).status_code == 200       # the keys are saved already


def test_with_brain_mcp_chosen_neither_key_nor_the_provider_can_be_taken_away(resolver):
    client = _page(resolver)
    _post(client, {"assistant.mode": "brain_mcp", **ANTHROPIC_READY, "assistant.brain_mcp_key": GW_KEY})
    for values, words in (({"assistant.brain_mcp_key": None}, providers.BRAIN_MCP_NEEDS_GATEWAY_KEY),
                          ({"assistant.anthropic_api_key": None}, providers.BRAIN_MCP_NEEDS_API_KEY),
                          ({"assistant.provider": "knovas"}, providers.BRAIN_MCP_NEEDS_ANTHROPIC),
                          ({"assistant.provider": None}, providers.BRAIN_MCP_NEEDS_ANTHROPIC)):
        r = _post(client, values)
        assert r.status_code == 400 and r.get_json()["error"] == words
    assert resolver.secret("assistant.brain_mcp_key") == GW_KEY and resolver.value("assistant.provider") == "anthropic"
    # Back to passages first, then the key may go.
    assert _post(client, {"assistant.mode": "passages", "assistant.brain_mcp_key": None}).status_code == 200
    assert resolver.secret("assistant.brain_mcp_key") is None


def test_an_unrelated_save_is_not_refused_for_a_deployments_choice(make_resolver):
    r = make_resolver({"ASSISTANT_MODE": "brain_mcp"})
    assert _post(_page(r), {"views.ttl_seconds": "30"}).status_code == 200


def test_the_page_names_what_a_deployments_brain_mcp_lacks(make_resolver):
    r = make_resolver({"ASSISTANT_MODE": "brain_mcp", "ASSISTANT_BRAIN_MCP_URL": "https://gateway.example.com/mcp"})
    problems = _page(r).get("/api/settings/platform").get_json()["problems"]
    assert problems["assistant.mode"] == providers.BRAIN_MCP_NEEDS_ANTHROPIC
    assert problems["assistant.brain_mcp_key"] == providers.BRAIN_MCP_KEY_MISSING_TEXT
    assert "knovas.ch" in problems["assistant.brain_mcp_url"]
    assert providers.settings_problems(make_resolver()) == {}


# --- through the real app, against the test database ---------------------------------------------------------------


def _csrf_from(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start:html.index('"', start)]


def _sign_in(client, email, password="korrektes-pferd-batterie"):
    page = client.get("/login")
    client.post("/login", data={"login_name": email, "password": password,
                                "csrf_token": _csrf_from(page.data.decode("utf-8"))})


def _token(client):
    return _csrf_from(client.get("/settings").data.decode("utf-8"))


@pytest.fixture
def settings_key(monkeypatch):
    monkeypatch.setenv("PLATFORM_SETTINGS_KEY", Fernet.generate_key().decode())
    for name in ("ASSISTANT_ENABLED", "ASSISTANT_PROVIDER", "ASSISTANT_MODEL", "ASSISTANT_MODE",
                 "ASSISTANT_BRAIN_MCP_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def people(identity_repo):
    admin = identity_repo.create(email="chef@kanzlei.ch", display_name="Chef", password="korrektes-pferd-batterie")
    identity_repo.grant_role(admin.id, "admin")
    return admin


@needs_db
def test_through_the_app_the_key_is_sealed_in_the_database_audited_as_changed_and_never_shown(
        settings_key, identity_client, people, identity_app, platform_db, caplog):
    from identity import audit

    caplog.set_level(logging.DEBUG)
    _sign_in(identity_client, "chef@kanzlei.ch")
    h = {"X-CSRF-Token": _token(identity_client)}
    refused = identity_client.post("/api/settings/platform", headers=h, json={"values": {
        "assistant.mode": "brain_mcp", "assistant.brain_mcp_key": GW_KEY}})
    assert refused.status_code == 400 and refused.get_json()["error"] == providers.BRAIN_MCP_NEEDS_ANTHROPIC
    assert platform_db.execute("SELECT count(*) FROM platform_settings").fetchone()[0] == 0
    r = identity_client.post("/api/settings/platform", headers=h, json={"values": {
        "assistant.enabled": "an", "assistant.provider": "anthropic", "assistant.anthropic_api_key": KEY,
        "assistant.mode": "brain_mcp", "assistant.brain_mcp_key": GW_KEY}})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["warnings"] == {"assistant.mode": WARNING}
    resolver = identity_app.extensions["platform_settings"]
    assert resolver.value("assistant.mode") == "brain_mcp" and resolver.secret("assistant.brain_mcp_key") == GW_KEY
    value, sealed = platform_db.execute("SELECT value, secret FROM platform_settings WHERE key = %s",
                                        ("assistant.brain_mcp_key",)).fetchone()
    assert value is None and GW_KEY.encode() not in bytes(sealed)
    for body in (refused.get_data(as_text=True), r.get_data(as_text=True),
                 identity_client.get("/api/settings/platform").get_data(as_text=True),
                 identity_client.get("/settings").get_data(as_text=True)):
        assert GW_KEY not in body
    rows = audit.recent(platform_db, action="settings.changed")
    detail = {row["detail"]["key"]: row["detail"] for row in rows}
    assert detail["assistant.brain_mcp_key"]["new"] == "ge\u00e4ndert"
    assert detail["assistant.mode"]["old"] == "passages" and detail["assistant.mode"]["new"] == "brain_mcp"
    assert GW_KEY not in json.dumps(rows, default=str) and GW_KEY not in caplog.text
