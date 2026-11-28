"""claude-haiku-4-5 as the assistant's model (the founder, 2026-11-28: a stand-in for a smaller, self-hosted model).

Claude Haiku 4.5 is of the previous generation: it takes no ``output_config.effort``, no server-side refusal fallback
(no ``fallbacks``, no fallback beta) and no adaptive thinking. Every request the assistant builds (the passages mode,
<<Brain-Werkzeuge>>, <<Wissensgraph>>, "Verbindung testen") asks it without them, through
``providers.model_fields``; the current models' requests are unchanged. No test reaches Anthropic. ASCII-only.
"""
from __future__ import annotations

import time

import pytest

from assistant import providers
from test_assistant_brain_graph import FakeAnthropic, message, text

KEY = "sk-ant-test-not-a-key"                               # obviously fake
GW_KEY = "knovas_gw_test-not-a-key-0123456789"              # obviously fake
CURRENT = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
OLD = "claude-haiku-4-5"
MODEL_FIELDS = ("output_config", "betas", "fallbacks")


@pytest.fixture(autouse=True)
def no_real_anthropic(monkeypatch):
    def refuse(**kwargs):
        raise AssertionError("a test tried to build a real Anthropic client")

    monkeypatch.setattr(providers, "_sdk_client", refuse)


def _answer():
    return FakeAnthropic(message(text('{"no_answer": true}')))


def _generate(model):
    fake = _answer()
    providers.make_anthropic_generate(api_key=KEY, model=model, client_factory=fake)(
        system_instruction="Regeln", messages=[{"role": "user", "content": "Frage"}], max_tokens=1536)
    return fake.requests[0]


def _brain_mcp(model):
    fake = _answer()
    providers.make_brain_mcp_ask(api_key=KEY, model=model, gateway_key=GW_KEY, client_factory=fake)(
        system_instruction="Regeln", messages=[{"role": "user", "content": "Frage"}], max_tokens=1536,
        deadline_at=time.monotonic() + 100, min_seconds=1)
    return fake.requests[0]


def _brain_graph(model):
    fake = _answer()
    providers.make_brain_graph_turn(api_key=KEY, model=model, client_factory=fake)(
        system_instruction="Regeln", messages=[{"role": "user", "content": "Frage"}], tools=[],
        tool_choice={"type": "auto"}, max_tokens=1536, deadline_at=time.monotonic() + 100, min_seconds=1)
    return fake.requests[0]


def _connection_test(model):
    fake = _answer()
    assert providers.connection_test(KEY, model, client_factory=fake)["ok"] is True
    return fake.requests[0]


BUILDERS = [_generate, _brain_mcp, _brain_graph, _connection_test]


def test_haiku_4_5_is_offered_by_the_setting_and_the_provider():
    import platform_settings as ps

    assert OLD in providers.ANTHROPIC_MODELS and ps.REGISTRY["assistant.model"].choices == providers.ANTHROPIC_MODELS
    assert "claude-haiku-4-5 1 / 5, \u00e4ltere Generation, zum Vergleich mit einem kleineren Modell" in \
        ps.REGISTRY["assistant.model"].help


@pytest.mark.parametrize("build", BUILDERS)
def test_the_haiku_4_5_request_carries_no_effort_no_fallback_and_no_thinking(build):
    request = build(OLD)
    assert request["model"] == OLD
    assert "output_config" not in request and "fallbacks" not in request and "thinking" not in request
    assert "server-side-fallback-2026-07-01" not in request.get("betas", [])


def test_haiku_4_5_keeps_the_mcp_connectors_beta_only():
    assert _brain_mcp(OLD)["betas"] == [providers.BRAIN_MCP_BETA]
    assert "betas" not in _generate(OLD) and "betas" not in _brain_graph(OLD)


@pytest.mark.parametrize("model", CURRENT)
@pytest.mark.parametrize("build", BUILDERS)
def test_the_current_models_requests_are_unchanged(build, model):
    request = build(model)
    extra = [providers.BRAIN_MCP_BETA] if build is _brain_mcp else []
    assert request["output_config"] == {"effort": "medium"}
    assert request["betas"] == ["server-side-fallback-2026-07-01"] + extra
    assert request["fallbacks"] == "default" and "thinking" not in request
    assert request["max_tokens"] == 8_000


def test_the_fields_keep_their_place_in_the_request():
    keys = list(_generate("claude-opus-5-5"))
    assert keys == ["model", "max_tokens", "system", "messages", "output_config", "betas", "fallbacks"]
    keys = list(_brain_mcp("claude-opus-5-5"))
    assert keys[-5:] == ["output_config", "betas", "fallbacks", "mcp_servers", "tools"]
    assert list(_generate(OLD)) == ["model", "max_tokens", "system", "messages"]
