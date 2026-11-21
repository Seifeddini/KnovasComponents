"""PMVP-25: the Knovas Assistent's four entries on the settings page.

The registry (the switch off by default and pinned by ASSISTANT_ENABLED, the provider, the model with its prices,
the key as a secret), the page (administrators only, as every other entry; the key never comes back; the page
says when anthropic is chosen without a key) and "Verbindung testen" (administrators only, behind the CSRF gate,
one call with invented text, the plain outcome, the outcome class only in the log). No test reaches Anthropic.
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
from test_assistant_providers import KEY, FakeAnthropic, message, text
from web_interface.settings_routes import create_settings_blueprint

ROLES = ("admin", "approver", "ingestion_manager", "member")
ASSISTANT_KEYS = ("assistant.enabled", "assistant.provider", "assistant.model", "assistant.anthropic_api_key")
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
    """The identity gate as the blueprint uses it: who is signed in."""

    def __init__(self, user):
        self.user = user

    def current_user(self):
        return self.user


@pytest.fixture
def resolver(tmp_path):
    raw = (Path(__file__).resolve().parents[1] / "config" / "config.yaml").read_text(encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(raw, encoding="utf-8")
    store = _MemoryStore()
    r = ps.Resolver(_Config(path), tenant="tenant-a", store=lambda: store, environ={},
                    box=ps.SecretBox(Fernet.generate_key().decode()))
    r.store = store
    return r


def _page(resolver, user, claude=None):
    app = Flask(__name__)
    app.register_blueprint(create_settings_blueprint(
        resolver, gate=_Gate(user), roles=lambda: list(ROLES), profiles=lambda: [],
        bundle_github=lambda repo: None, repository=lambda: "", make_repo=lambda repo, token: None,
        anthropic_client=claude if claude is not None else FakeAnthropic()))
    return app.test_client()


ADMIN, MEMBER = _Person("admin"), _Person("member")
TEST_URL = "/api/settings/platform/test/assistant.anthropic_api_key"


def _save(resolver, **values):
    resolver.save(ADMIN, {f"assistant.{k}": v for k, v in values.items()}, roles=ROLES)


# --- the registry -------------------------------------------------------------------------------------


def test_the_four_entries_are_registered_for_the_whole_installation():
    for key in ASSISTANT_KEYS:
        s = ps.REGISTRY[key]
        assert s.scope == "installation" and s.label and s.help
    assert ps.REGISTRY["assistant.enabled"].kind == "choice"
    assert ps.REGISTRY["assistant.enabled"].choices == ("aus", "an")
    assert ps.REGISTRY["assistant.enabled"].env == ("ASSISTANT_ENABLED",)
    assert ps.REGISTRY["assistant.provider"].choices == ("knovas", "anthropic")
    assert ps.REGISTRY["assistant.model"].choices == ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
    assert ps.REGISTRY["assistant.anthropic_api_key"].kind == "secret"
    assert ps.REGISTRY["assistant.anthropic_api_key"].file_key is None     # never read from a file


def test_the_page_and_the_assistant_name_the_same_entries():
    from web_interface import settings_routes

    assert (settings_routes.ASSISTANT_KEY, settings_routes.ASSISTANT_MODEL) == (providers.SETTING_API_KEY,
                                                                                providers.SETTING_MODEL)
    assert set(ASSISTANT_KEYS) == {providers.SETTING_ENABLED, providers.SETTING_PROVIDER, providers.SETTING_MODEL,
                                   providers.SETTING_API_KEY}
    assert ps.REGISTRY[providers.SETTING_MODEL].choices == providers.ANTHROPIC_MODELS
    assert ps.REGISTRY[providers.SETTING_PROVIDER].choices == providers.PROVIDERS


def test_the_defaults_are_off_knovas_and_opus(resolver):
    assert resolver.value("assistant.enabled") == "aus"
    assert resolver.value("assistant.provider") == "knovas"
    assert resolver.value("assistant.model") == "claude-opus-5-5"
    assert resolver.secret("assistant.anthropic_api_key") is None


def test_the_model_help_names_each_price_and_opus_as_the_default():
    help_text = ps.REGISTRY["assistant.model"].help
    for model, price in (("claude-opus-5-5", "4 / 20"), ("claude-sonnet-5-5", "2 / 10"),
                         ("claude-haiku-5-5", "0,10 / 0,50")):
        assert f"{model} {price}" in help_text
    assert "je Million Tokens" in help_text and "claude-opus-5-5 4 / 20, der Standard" in help_text


def test_the_deployment_pins_the_switch(tmp_path):
    raw = (Path(__file__).resolve().parents[1] / "config" / "config.yaml").read_text(encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(raw, encoding="utf-8")
    r = ps.Resolver(_Config(path), tenant="t", store=lambda: _MemoryStore(), environ={"ASSISTANT_ENABLED": "true"},
                    box=ps.SecretBox(Fernet.generate_key().decode()))
    e = r.effective("assistant.enabled")
    assert e.pinned and e.value == "true" and "ASSISTANT_ENABLED" in e.detail["reason"]
    with pytest.raises(ps.Pinned):
        r.save(ADMIN, {"assistant.enabled": "aus"}, roles=ROLES)


@pytest.mark.parametrize("key, raw", [("assistant.enabled", "vielleicht"), ("assistant.provider", "openai"),
                                      ("assistant.model", "claude-3-opus")])
def test_a_value_not_offered_is_refused(resolver, key, raw):
    with pytest.raises(ps.SettingError):
        resolver.save(ADMIN, {key: raw}, roles=ROLES)


def test_the_key_is_sealed_audited_as_changed_and_never_shown(resolver, caplog):
    caplog.set_level(logging.DEBUG)
    _save(resolver, anthropic_api_key=KEY)
    assert resolver.secret("assistant.anthropic_api_key") == KEY
    (row,) = [v for (t, k), v in resolver.store.rows_.items() if k == "assistant.anthropic_api_key"]
    assert KEY.encode() not in bytes(row["secret"]) and row["value"] is None
    assert resolver.store.log == [("assistant.anthropic_api_key", "geändert", "geändert")]
    entry = next(e for e in resolver.entries() if e["key"] == "assistant.anthropic_api_key")
    assert entry["set"] is True and entry["value"] is None
    assert KEY not in json.dumps(resolver.entries()) and KEY not in caplog.text


# --- the page -----------------------------------------------------------------------------------------


def test_only_an_administrator_reads_or_tests_the_entries(resolver):
    claude = FakeAnthropic()
    member = _page(resolver, MEMBER, claude)
    assert member.get("/api/settings/platform").status_code == 403
    assert member.post(TEST_URL, json={"value": KEY}).status_code == 403
    assert _page(resolver, None, claude).post(TEST_URL, json={"value": KEY}).status_code == 401
    assert claude.built == []
    body = _page(resolver, ADMIN, claude).get("/api/settings/platform").get_json()
    assert set(ASSISTANT_KEYS) <= {e["key"] for e in body["settings"]} and body["can_edit"]


def test_a_member_cannot_change_them(resolver):
    r = _page(resolver, MEMBER).post("/api/settings/platform", json={"values": {"assistant.enabled": "an"}})
    assert r.status_code == 403 and resolver.value("assistant.enabled") == "aus"


def test_the_page_says_when_anthropic_is_chosen_without_a_key(resolver):
    page = _page(resolver, ADMIN)
    assert page.get("/api/settings/platform").get_json()["problems"] == {}
    _save(resolver, provider="anthropic")
    problems = page.get("/api/settings/platform").get_json()["problems"]
    assert problems == {"assistant.anthropic_api_key": providers.KEY_MISSING_TEXT}
    assert "kein lesbarer Schlüssel" in problems["assistant.anthropic_api_key"]
    _save(resolver, anthropic_api_key=KEY)
    body = page.get("/api/settings/platform")
    assert body.get_json()["problems"] == {} and KEY not in body.get_data(as_text=True)


def test_a_key_that_no_longer_opens_counts_as_missing(resolver):
    _save(resolver, provider="anthropic", anthropic_api_key=KEY)
    other = ps.Resolver(resolver._config, tenant="tenant-a", store=lambda: resolver.store, environ={},
                        box=ps.SecretBox(Fernet.generate_key().decode()))      # the deployment's key changed
    assert providers.settings_problems(other) == {"assistant.anthropic_api_key": providers.KEY_MISSING_TEXT}


# --- "Verbindung testen" ------------------------------------------------------------------------------


def test_the_typed_key_is_tested_with_the_chosen_model_and_never_echoed(resolver, caplog):
    caplog.set_level(logging.DEBUG)
    _save(resolver, model="claude-haiku-5-5")
    claude = FakeAnthropic(response=message(text("{\"no_answer\": true}")))
    r = _page(resolver, ADMIN, claude).post(TEST_URL, json={"value": f"  {KEY} "})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "message": "funktioniert", "model": "claude-haiku-5-5"}
    assert claude.built[0]["api_key"] == KEY and claude.requests[0]["model"] == "claude-haiku-5-5"
    assert KEY not in r.get_data(as_text=True) and KEY not in caplog.text
    assert "connection test ok" in caplog.text


def test_without_a_typed_key_the_saved_one_is_tested(resolver):
    _save(resolver, anthropic_api_key=KEY)
    claude = FakeAnthropic()
    r = _page(resolver, ADMIN, claude).post(TEST_URL, json={})
    assert r.get_json()["ok"] is True and claude.built[0]["api_key"] == KEY


def test_without_any_key_nothing_is_called(resolver):
    claude = FakeAnthropic()
    r = _page(resolver, ADMIN, claude).post(TEST_URL, json={"value": ""})
    assert r.get_json() == {"ok": False, "error": "kein Schlüssel gesetzt", "outcome": "no_key"}
    assert claude.built == []


def test_a_refused_key_says_so(resolver):
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    refused = anthropic.AuthenticationError("invalid x-api-key", response=httpx2.Response(401, request=request),
                                            body=None)
    r = _page(resolver, ADMIN, FakeAnthropic(error=refused)).post(TEST_URL, json={"value": KEY})
    assert r.get_json() == {"ok": False, "error": "Schlüssel abgelehnt", "outcome": "key_refused"}


# --- through the real app (the CSRF gate and the roles), against the test database -----------------------


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
    for name in ("ASSISTANT_ENABLED", "ASSISTANT_PROVIDER", "ASSISTANT_MODEL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def people(identity_repo):
    admin = identity_repo.create(email="chef@kanzlei.ch", display_name="Chef", password="korrektes-pferd-batterie")
    identity_repo.grant_role(admin.id, "admin")
    member = identity_repo.create(email="ida@kanzlei.ch", display_name="Ida", password="korrektes-pferd-batterie")
    identity_repo.grant_role(member.id, "member")
    return admin, member


@needs_db
class TestThroughTheApp:
    def test_an_admin_switches_it_on_saves_the_key_and_tests_it(self, settings_key, identity_client, people,
                                                                  identity_app, platform_db, monkeypatch, caplog):
        from identity import audit

        caplog.set_level(logging.DEBUG)
        claude = FakeAnthropic()
        monkeypatch.setattr(providers, "_sdk_client", claude)
        _sign_in(identity_client, "chef@kanzlei.ch")
        h = {"X-CSRF-Token": _token(identity_client)}
        r = identity_client.post("/api/settings/platform", headers=h, json={"values": {
            "assistant.enabled": "an", "assistant.provider": "anthropic", "assistant.anthropic_api_key": KEY}})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["problems"] == {}
        resolver = identity_app.extensions["platform_settings"]
        assert resolver.value("assistant.enabled") == "an" and resolver.secret("assistant.anthropic_api_key") == KEY
        tested = identity_client.post(TEST_URL, headers=h, json={})
        assert tested.get_json() == {"ok": True, "message": "funktioniert", "model": "claude-opus-5-5"}
        assert claude.built[0]["api_key"] == KEY
        for body in (r.get_data(as_text=True), tested.get_data(as_text=True),
                     identity_client.get("/api/settings/platform").get_data(as_text=True),
                     identity_client.get("/settings").get_data(as_text=True)):
            assert KEY not in body
        rows = audit.recent(platform_db, action="settings.changed")
        detail = {row["detail"]["key"]: row["detail"] for row in rows}
        assert detail["assistant.anthropic_api_key"]["new"] == "geändert"
        assert KEY not in json.dumps(rows, default=str) and KEY not in caplog.text

    def test_the_test_needs_the_csrf_token_and_an_admin(self, settings_key, identity_client, people, monkeypatch):
        claude = FakeAnthropic()
        monkeypatch.setattr(providers, "_sdk_client", claude)
        _sign_in(identity_client, "chef@kanzlei.ch")
        assert identity_client.post(TEST_URL, json={"value": KEY}).status_code == 403          # no token
        identity_client.post("/logout", data={"csrf_token": _token(identity_client)})
        _sign_in(identity_client, "ida@kanzlei.ch")
        r = identity_client.post(TEST_URL, json={"value": KEY}, headers={"X-CSRF-Token": _token(identity_client)})
        assert r.status_code == 403 and claude.built == []

    def test_the_page_offers_verbindung_testen(self, settings_key, identity_client, people):
        _sign_in(identity_client, "chef@kanzlei.ch")
        script = identity_client.get("/static/js/platform_settings.js").get_data(as_text=True)
        assert "'Verbindung testen'" in script and "state.problems" in script
