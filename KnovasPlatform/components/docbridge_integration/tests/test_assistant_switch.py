"""PMVP-25: the assistant's switch and the provider the customer chose (assistant.core.make_service).

Read through the settings' Resolver, as the app reads them: off by default (``OFF``, nothing searched), on by the
deployment's ASSISTANT_ENABLED or the page's value, a change applying without a restart; ``knovas`` is the Core's
call exactly as built, ``anthropic`` is Claude on the saved key and model, and either way the search is the
person's own (POST-02). No test reaches Anthropic or a Core.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

import platform_settings as ps
from assistant import providers
from assistant.core import OFF, make_service
from assistant.service import AssistantService
from experiments.search import SearchIntegration
from test_assistant_core import ANSWER, _Core
from test_assistant_core import _client as _core_client
from test_assistant_providers import KEY, FakeAnthropic, message, text

QUESTION = "Welche Frist gilt?"


class _Config:
    def __init__(self, path: Path):
        self.config_path = str(path)

    def get(self, key, default=None):
        return default


class _MemoryStore:
    """The settings' page layer in memory (what SettingStore does, without a database)."""

    def __init__(self):
        self.rows_ = {}

    def rows(self, tenant):
        return {k: v for (t, k), v in self.rows_.items() if t in (tenant, "")}

    def put(self, key, tenant, value, user, old=None):
        self.rows_[(tenant, key)] = {"value": value, "secret": None, "updated_by": user.email, "updated_at": "now"}

    def put_secret(self, key, tenant, sealed, user):
        self.rows_[(tenant, key)] = {"value": None, "secret": sealed, "updated_by": user.email, "updated_at": "now"}

    def clear(self, key, tenant, user, old=None):
        self.rows_.pop((tenant, key), None)


class _Admin:
    id = "00000000-0000-0000-0000-000000000001"
    email = "admin@example.test"
    roles = frozenset({"admin"})


@pytest.fixture(autouse=True)
def no_real_anthropic(monkeypatch):
    def refuse(**kwargs):
        raise AssertionError("a test tried to build a real Anthropic client")

    monkeypatch.setattr(providers, "_sdk_client", refuse)


@pytest.fixture
def settings(tmp_path):
    """A Resolver over the real config.yaml's assistant section, a page layer in memory, no environment."""

    def build(environ=None):
        raw = (Path(__file__).resolve().parents[1] / "config" / "config.yaml").read_text(encoding="utf-8")
        path = tmp_path / "config.yaml"
        path.write_text(raw, encoding="utf-8")
        return ps.Resolver(_Config(path), tenant="tenant-a", store=lambda: store, environ=environ or {},
                           box=ps.SecretBox(Fernet.generate_key().decode()))

    store = _MemoryStore()
    return build


@pytest.fixture
def key_dir(tmp_path):
    path = tmp_path / "broker"
    path.mkdir()
    return path


@pytest.fixture
def split():
    return SearchIntegration(settings=types.SimpleNamespace(pointer_prefix="experiments"),
                             connection=lambda: None, current_user=lambda: None, enabled=False).split


def _save(resolver, **values):
    resolver.save(_Admin(), {f"assistant.{k}": v for k, v in values.items()}, roles=("admin",))


# --- the switch ---------------------------------------------------------------------------------------


def test_off_by_default_nothing_is_built_and_nothing_is_searched(settings, key_dir, monkeypatch, split):
    resolver = settings()
    assert resolver.value("assistant.enabled") == "aus"
    core = _Core()
    assert make_service(resolver, _core_client(key_dir, monkeypatch, core), split=split) is OFF
    assert core.sent == []


@pytest.mark.parametrize("pinned, on", [("true", True), ("an", True), ("false", False), ("aus", False),
                                        ("irgendwas", False)])
def test_the_deployment_pins_the_switch(settings, key_dir, monkeypatch, split, pinned, on):
    resolver = settings({"ASSISTANT_ENABLED": pinned})
    built = make_service(resolver, _core_client(key_dir, monkeypatch, _Core()), split=split)
    assert isinstance(built, AssistantService) if on else built is OFF


def test_the_page_switches_it_on_and_off_without_a_restart(settings, key_dir, monkeypatch, split):
    resolver = settings()
    client = _core_client(key_dir, monkeypatch, _Core())
    _save(resolver, enabled="an")
    assert isinstance(make_service(resolver, client, split=split), AssistantService)
    _save(resolver, enabled="aus")
    assert make_service(resolver, client, split=split) is OFF


def test_a_pinned_switch_cannot_be_changed_on_the_page(settings):
    resolver = settings({"ASSISTANT_ENABLED": "false"})
    with pytest.raises(ps.Pinned):
        _save(resolver, enabled="an")


# --- the provider -------------------------------------------------------------------------------------


def test_knovas_is_the_cores_call_as_built(settings, key_dir, monkeypatch, split):
    resolver = settings()
    _save(resolver, enabled="an")
    assert resolver.value("assistant.provider") == "knovas"
    core, claude = _Core(), FakeAnthropic()
    service = make_service(resolver, _core_client(key_dir, monkeypatch, core), split=split, anthropic_client=claude)
    reply = service.answer(QUESTION)
    assert reply.status == "answered" and len(reply.sentences) == 2
    assert len(core.bodies("/secured/query")) == 1 and len(core.bodies("/secured/llm/generate")) == 1
    assert core.bodies("/secured/llm/generate")[0]["temperature"] == 0.2
    assert claude.built == []


def test_anthropic_answers_through_claude_on_the_saved_key_and_model(settings, key_dir, monkeypatch, split):
    resolver = settings()
    _save(resolver, enabled="an", provider="anthropic", model="claude-sonnet-5-5", anthropic_api_key=KEY)
    core, claude = _Core(), FakeAnthropic(response=message(text(ANSWER)))
    service = make_service(resolver, _core_client(key_dir, monkeypatch, core), split=split, anthropic_client=claude)
    reply = service.answer(QUESTION)
    assert reply.status == "answered" and len(reply.sentences) == 2
    assert claude.built == [{"api_key": KEY, "timeout": 60.0, "max_retries": 1}]
    assert claude.requests[0]["model"] == "claude-sonnet-5-5"
    # POST-02: the person's own search, once, and the Core generates nothing.
    assert len(core.bodies("/secured/query")) == 1 and core.bodies("/secured/llm/generate") == []
    prompt = claude.requests[0]["messages"][-1]["content"]
    assert "Die Kuendigungsfrist betraegt drei Monate." in prompt and "Die Frist beginnt mit dem Zugang." in prompt
    assert "Hypothese" not in prompt                              # the experiment document search took out


def test_a_changed_model_applies_to_the_next_answer(settings, key_dir, monkeypatch, split):
    resolver = settings()
    _save(resolver, enabled="an", provider="anthropic", anthropic_api_key=KEY)
    claude = FakeAnthropic()
    service = make_service(resolver, _core_client(key_dir, monkeypatch, _Core()), split=split,
                           anthropic_client=claude)
    service.answer(QUESTION)
    _save(resolver, model="claude-haiku-5-5")
    service.answer(QUESTION)
    assert [r["model"] for r in claude.requests] == ["claude-opus-5-5", "claude-haiku-5-5"]


def test_anthropic_without_a_key_fails_and_builds_no_client(settings, key_dir, monkeypatch, split):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-from-the-environment")   # never taken from here
    resolver = settings()
    _save(resolver, enabled="an", provider="anthropic")
    claude = FakeAnthropic()
    reply = make_service(resolver, _core_client(key_dir, monkeypatch, _Core()), split=split,
                         anthropic_client=claude).answer(QUESTION)
    assert reply.status == "failed" and reply.sentences == []
    assert claude.built == []


def test_the_builders_key_is_not_the_assistants(settings, key_dir, monkeypatch, split):
    """ANTHROPIC_API_KEY pins the views assistant's key (builder.assistant.api_key), not this one."""
    resolver = settings({"ANTHROPIC_API_KEY": "sk-ant-test-builder-only"})
    _save(resolver, enabled="an", provider="anthropic")
    assert resolver.secret("assistant.anthropic_api_key") is None
    claude = FakeAnthropic()
    make_service(resolver, _core_client(key_dir, monkeypatch, _Core()), split=split,
                 anthropic_client=claude).answer(QUESTION)
    assert claude.built == []


def test_a_provider_not_offered_fails_every_answer(settings, key_dir, monkeypatch, split):
    resolver = settings({"ASSISTANT_ENABLED": "true", "ASSISTANT_PROVIDER": "openai"})
    core, claude = _Core(), FakeAnthropic()
    reply = make_service(resolver, _core_client(key_dir, monkeypatch, core), split=split,
                         anthropic_client=claude).answer(QUESTION)
    assert reply.status == "failed"
    assert core.bodies("/secured/llm/generate") == [] and claude.built == []
