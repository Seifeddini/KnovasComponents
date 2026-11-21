"""PMVP-25: the Knovas Assistent through Claude, the provider the customer chose (assistant.providers).

The call (one ``client.beta.messages.create`` with effort medium, the server-side fallback, no temperature, no
thinking setting, at least 8,000 tokens), the client (the setting's key, 60 seconds, no retry, closed after the
call), the answer (the text blocks; a refusal fails; a cut answer stays as it is), the error mapping (busy on 429
and 529, failed on everything else, the SDK's exception never attached), the key (never built without one, never
in a log line or an exception) and POST-02/POST-04 through the whole service. "Verbindung testen" too.

No test reaches Anthropic: a fake client stands in, or the real SDK over an in-process transport, and the SDK's
own client factory is replaced by one that fails the test if it is ever used.
"""
from __future__ import annotations

import json
import logging

import anthropic
import httpx2
import pytest
from anthropic.types.beta import BetaFallbackBlock, BetaMessage, BetaTextBlock, BetaThinkingBlock, BetaUsage

from assistant import providers
from assistant.errors import CoreBusy, CoreFailed
from assistant.service import (BUSY_TEXT, DEFAULT_MAX_TOKENS, FAILED_TEXT, SYSTEM_INSTRUCTION, AssistantService,
                               Turn)

KEY = "sk-ant-test-not-a-key"                     # obviously fake; never a real key in a test
MODEL = "claude-opus-5-5"
URL = "https://api.anthropic.com/v1/messages"


def st(statement, source, quote):
    return json.dumps({"statement": statement, "source": source, "quote": quote})


PASSAGE_1 = "Die Kuendigungsfrist betraegt drei Monate zum Monatsende."
PASSAGE_2 = "Die Frist beginnt mit dem Zugang der Kuendigung."
QUESTION = "Welche Kuendigungsfrist gilt im Mandat Mueller?"
STATEMENT_1 = st("Die Kuendigungsfrist betraegt drei Monate.", 1, "Kuendigungsfrist betraegt drei Monate")
STATEMENT_2 = st("Sie beginnt mit dem Zugang der Kuendigung.", 2, "beginnt mit dem Zugang der Kuendigung")
ANSWER = STATEMENT_1 + "\n" + STATEMENT_2

ROWS = [
    {"doc_id": "Mandate/Mueller/vertrag.pdf", "title": "Vertrag", "page_number": 2, "snippet": PASSAGE_1},
    {"doc_id": "Mandate/Mueller/brief.pdf", "title": "Brief", "page_number": 1, "snippet": PASSAGE_2},
]


def text(value):
    return BetaTextBlock(type="text", text=value, citations=None)


def message(*blocks, stop="end_turn", model=MODEL):
    """A response as the SDK returns it (its own types, built without a request)."""
    return BetaMessage.model_construct(
        id="msg_test", type="message", role="assistant", model=model, stop_reason=stop, stop_sequence=None,
        content=list(blocks), usage=BetaUsage.model_construct(input_tokens=1200, output_tokens=180))


class FakeAnthropic:
    """Stands in for ``anthropic.Anthropic`` as a client factory: records how each client was built, what it was
    asked and whether it was closed. Answers ``response`` or raises ``error``; never calls out."""

    def __init__(self, response=None, error=None):
        self.response = response if response is not None else message(text(ANSWER))
        self.error = error
        self.built, self.requests = [], []
        self.closed = 0

    def __call__(self, **kwargs):
        self.built.append(kwargs)
        return _FakeClient(self)


class _FakeClient:
    def __init__(self, fake):
        self._fake = fake
        self.beta = type("Beta", (), {})()
        self.beta.messages = type("Messages", (), {})()
        self.beta.messages.create = self._create

    def _create(self, **request):
        self._fake.requests.append(request)
        if self._fake.error is not None:
            raise self._fake.error
        return self._fake.response

    def close(self):
        self._fake.closed += 1


@pytest.fixture(autouse=True)
def no_real_anthropic(monkeypatch):
    """The SDK's own client factory fails the test: nothing here may reach Anthropic."""

    def refuse(**kwargs):
        raise AssertionError("a test tried to build a real Anthropic client")

    monkeypatch.setattr(providers, "_sdk_client", refuse)


def _generate(fake=None, **kw):
    kw.setdefault("api_key", KEY)
    kw.setdefault("model", MODEL)
    return providers.make_anthropic_generate(client_factory=fake if fake is not None else FakeAnthropic(), **kw)


def _call(generate, max_tokens=DEFAULT_MAX_TOKENS, messages=None):
    return generate(system_instruction="Regeln", messages=messages or [{"role": "user", "content": "Frage?"}],
                    max_tokens=max_tokens)


def _search(rows=ROWS):
    calls = []

    def search(question, limit):
        calls.append((question, limit))
        return [dict(r) for r in rows]

    search.calls = calls
    return search


# --- the call -----------------------------------------------------------------------------------------


def test_the_request_is_the_ordered_shape_and_nothing_else():
    fake = FakeAnthropic()
    _call(_generate(fake))
    (request,) = fake.requests
    assert request == {
        "model": MODEL,
        "max_tokens": 8_000,
        "system": "Regeln",
        "messages": [{"role": "user", "content": "Frage?"}],
        "output_config": {"effort": "medium"},
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    }
    assert "temperature" not in request and "thinking" not in request


@pytest.mark.parametrize("asked, sent", [(1, 8_000), (DEFAULT_MAX_TOKENS, 8_000), (8_000, 8_000), (12_000, 12_000)])
def test_max_tokens_is_at_least_8000(asked, sent):
    fake = FakeAnthropic()
    _call(_generate(fake), max_tokens=asked)
    assert fake.requests[0]["max_tokens"] == sent


def test_the_client_gets_the_settings_key_60_seconds_and_no_retry_and_is_closed():
    fake = FakeAnthropic()
    _call(_generate(fake))
    assert fake.built == [{"api_key": KEY, "timeout": 60.0, "max_retries": 0}]
    assert fake.closed == 1


def test_the_client_is_built_without_a_retry():
    """The coordinator, 2026-11-21: a 429 or 529 is busy at once; the SDK would wait any retry-after first."""
    fake = FakeAnthropic()
    _call(_generate(fake))
    assert providers.ANTHROPIC_MAX_RETRIES == 0
    assert [b["max_retries"] for b in fake.built] == [0]


def test_the_client_is_closed_after_a_failure_too():
    fake = FakeAnthropic(error=anthropic.APIConnectionError(request=httpx2.Request("POST", URL)))
    with pytest.raises(CoreFailed):
        _call(_generate(fake))
    assert fake.closed == 1


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"])
def test_each_offered_model_is_asked_by_name(model):
    fake = FakeAnthropic()
    _call(_generate(fake, model=model))
    assert fake.requests[0]["model"] == model


def test_a_model_not_offered_builds_no_client_and_fails():
    fake = FakeAnthropic()
    with pytest.raises(CoreFailed) as caught:
        _call(_generate(fake, model="claude-3-opus"))
    assert caught.value.reason == "anthropic_model_unknown"
    assert fake.built == []


def test_key_and_model_are_read_on_every_call():
    current = {"key": KEY, "model": "claude-opus-5-5"}
    fake = FakeAnthropic()
    generate = _generate(fake, api_key=lambda: current["key"], model=lambda: current["model"])
    _call(generate)
    current.update(key="sk-ant-test-another-fake", model="claude-haiku-5-5")
    _call(generate)
    assert [b["api_key"] for b in fake.built] == [KEY, "sk-ant-test-another-fake"]
    assert [r["model"] for r in fake.requests] == ["claude-opus-5-5", "claude-haiku-5-5"]


# --- the key ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_without_a_key_no_client_is_built_and_the_answer_fails(missing, monkeypatch):
    # The SDK would take ANTHROPIC_API_KEY from the environment when no key is passed: nothing is built at all.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-from-the-environment")
    fake = FakeAnthropic()
    with pytest.raises(CoreFailed) as caught:
        _call(_generate(fake, api_key=lambda: missing))
    assert caught.value.reason == "anthropic_key_missing"
    assert fake.built == []


def test_without_a_key_and_without_a_fake_the_sdk_is_not_asked_either(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-from-the-environment")
    with pytest.raises(CoreFailed):
        _call(providers.make_anthropic_generate(api_key=None, model=MODEL))   # no_real_anthropic would fail


def test_the_default_client_is_the_sdk_with_the_settings_key(monkeypatch):
    monkeypatch.undo()                                   # the real factory, for this test only; it calls nothing
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-from-the-environment")
    client = providers._sdk_client(api_key=KEY, timeout=60.0, max_retries=0)
    try:
        assert isinstance(client, anthropic.Anthropic)
        assert client.api_key == KEY and client.max_retries == 0 and client.timeout == 60.0
    finally:
        client.close()


# --- the answer ---------------------------------------------------------------------------------------


def test_the_text_is_the_text_blocks_joined():
    response = message(BetaThinkingBlock(type="thinking", thinking="", signature="sig"),
                       BetaFallbackBlock.model_construct(type="fallback"),
                       text(STATEMENT_1 + "\n"), text(STATEMENT_2))
    assert _call(_generate(FakeAnthropic(response=response))) == ANSWER


def test_no_text_block_is_an_empty_answer():
    assert _call(_generate(FakeAnthropic(response=message()))) == ""


def test_a_refusal_fails():
    fake = FakeAnthropic(response=message(text(STATEMENT_1), stop="refusal"))
    with pytest.raises(CoreFailed) as caught:
        _call(_generate(fake))
    assert caught.value.reason == "anthropic_refusal"


def test_a_cut_answer_is_passed_on_as_it_is_and_the_check_drops_its_last_line():
    cut = STATEMENT_1 + "\n" + STATEMENT_2[:30]
    fake = FakeAnthropic(response=message(text(cut), stop="max_tokens"))
    assert _call(_generate(fake)) == cut
    reply = AssistantService(search=_search(), generate=_generate(FakeAnthropic(
        response=message(text(cut), stop="max_tokens")))).answer(QUESTION)
    assert reply.status == "answered"
    assert [s.text for s in reply.sentences] == ["Die Kuendigungsfrist betraegt drei Monate."]


# --- the error mapping --------------------------------------------------------------------------------
#
# Each SDK error carries the key and a passage in its message, as no real error should: the mapped error must
# carry neither, and must not hold the SDK's exception (whose request has the key in a header).

LEAKY = f"upstream said {KEY} about {PASSAGE_1}"


def _status_error(cls, status):
    request = httpx2.Request("POST", URL, headers={"x-api-key": KEY})
    return cls(LEAKY, response=httpx2.Response(status, request=request), body={"error": {"message": LEAKY}})


def _request():
    return httpx2.Request("POST", URL, headers={"x-api-key": KEY})


ERRORS = [
    pytest.param(_status_error(anthropic.RateLimitError, 429), CoreBusy, "anthropic_busy", 429, id="429-rate-limit"),
    pytest.param(_status_error(anthropic.OverloadedError, 529), CoreBusy, "anthropic_busy", 529, id="529-overloaded"),
    pytest.param(_status_error(anthropic.APIStatusError, 529), CoreBusy, "anthropic_busy", 529, id="529-as-status"),
    pytest.param(_status_error(anthropic.AuthenticationError, 401), CoreFailed, "anthropic_key_refused", 401,
                 id="401-key-refused"),
    pytest.param(_status_error(anthropic.PermissionDeniedError, 403), CoreFailed, "anthropic_permission_denied",
                 403, id="403-permission"),
    pytest.param(_status_error(anthropic.BadRequestError, 400), CoreFailed, "anthropic_http_error", 400, id="400"),
    pytest.param(_status_error(anthropic.NotFoundError, 404), CoreFailed, "anthropic_http_error", 404, id="404"),
    pytest.param(_status_error(anthropic.InternalServerError, 500), CoreFailed, "anthropic_http_error", 500,
                 id="500"),
    pytest.param(_status_error(anthropic.ServiceUnavailableError, 503), CoreFailed, "anthropic_http_error", 503,
                 id="503"),
    pytest.param(anthropic.APIConnectionError(message=LEAKY, request=_request()), CoreFailed,
                 "anthropic_no_connection", None, id="no-connection"),
    pytest.param(anthropic.APITimeoutError(request=_request()), CoreFailed, "anthropic_timeout", None, id="timeout"),
]


@pytest.mark.parametrize("error, mapped, reason, status", ERRORS)
def test_each_sdk_error_is_busy_or_failed_with_a_status_and_a_reason_only(error, mapped, reason, status, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(mapped) as caught:
        _call(_generate(FakeAnthropic(error=error)))
    exc = caught.value
    assert type(exc) is mapped and exc.reason == reason and exc.status == status
    assert exc.__cause__ is None and exc.__context__ is None
    for leak in (KEY, PASSAGE_1, "upstream said"):
        assert leak not in str(exc) and leak not in repr(exc) and leak not in caplog.text
    assert type(error).__name__ in caplog.text                     # class names only in the log
    assert "model=claude-opus-5-5" in caplog.text


@pytest.mark.parametrize("error, mapped, reason, status", ERRORS)
def test_the_service_says_busy_or_failed_and_nothing_else(error, mapped, reason, status, caplog):
    caplog.set_level(logging.DEBUG)
    reply = AssistantService(search=_search(), generate=_generate(FakeAnthropic(error=error))).answer(QUESTION)
    assert reply.status == ("busy" if mapped is CoreBusy else "failed")
    assert reply.message == (BUSY_TEXT if mapped is CoreBusy else FAILED_TEXT)
    assert reply.sentences == [] and reply.passages == []
    assert KEY not in caplog.text and PASSAGE_1 not in caplog.text and QUESTION not in caplog.text


# --- the real SDK over an in-process transport: the same request on the wire, the same mapping ---------


def _sdk_factory(handler, seen):
    def factory(**kwargs):
        def record(request):
            seen.append(request)
            return handler(request)

        return anthropic.Anthropic(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(record)))

    return factory


def _wire_message(answer):
    return {"id": "msg_wire", "type": "message", "role": "assistant", "model": MODEL, "stop_reason": "end_turn",
            "stop_sequence": None, "content": [{"type": "text", "text": answer, "citations": None}],
            "usage": {"input_tokens": 1200, "output_tokens": 180}}


def test_through_the_real_sdk_the_wire_carries_the_ordered_request():
    seen = []
    generate = _generate(_sdk_factory(lambda r: httpx2.Response(200, json=_wire_message(ANSWER)), seen))
    assert _call(generate) == ANSWER
    (request,) = seen
    body = json.loads(request.content)
    assert body == {"model": MODEL, "max_tokens": 8_000, "system": "Regeln",
                    "messages": [{"role": "user", "content": "Frage?"}],
                    "output_config": {"effort": "medium"}, "fallbacks": "default"}
    assert request.headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert KEY not in request.content.decode("utf-8")              # the key travels in a header only
    assert any(KEY in value for value in request.headers.values())


@pytest.mark.parametrize("status, mapped, reason", [(429, CoreBusy, "anthropic_busy"), (529, CoreBusy, "anthropic_busy"),
                                                    (401, CoreFailed, "anthropic_key_refused"),
                                                    (403, CoreFailed, "anthropic_permission_denied"),
                                                    (500, CoreFailed, "anthropic_http_error")])
def test_through_the_real_sdk_an_http_error_maps_the_same(status, mapped, reason):
    seen = []

    def handler(request):
        # A retry-after the SDK would honour, were it to retry: it must not (one request, no wait).
        return httpx2.Response(status, headers={"retry-after": "600"},
                               json={"type": "error", "error": {"type": "error", "message": "nein"}})

    with pytest.raises(mapped) as caught:
        _call(_generate(_sdk_factory(handler, seen)))
    assert type(caught.value) is mapped and caught.value.reason == reason and caught.value.status == status
    assert caught.value.__context__ is None and len(seen) == 1


@pytest.mark.parametrize("raised, reason", [(httpx2.ConnectError, "anthropic_no_connection"),
                                            (httpx2.ReadTimeout, "anthropic_timeout")])
def test_through_the_real_sdk_no_connection_and_a_timeout_fail(raised, reason):
    seen = []

    def handler(request):
        raise raised("nope", request=request)

    with pytest.raises(CoreFailed) as caught:
        _call(_generate(_sdk_factory(handler, seen)))
    assert caught.value.reason == reason and len(seen) == 1          # tried once, not again


# --- POST-02 and POST-04 through the whole service -----------------------------------------------------


def test_the_service_answers_through_claude_unchanged():
    fake = FakeAnthropic()
    search = _search()
    reply = AssistantService(search=search, generate=_generate(fake)).answer(QUESTION)
    assert reply.status == "answered"
    assert [(s.text, s.refs, s.quote) for s in reply.sentences] == [
        ("Die Kuendigungsfrist betraegt drei Monate.", (1,), "Kuendigungsfrist betraegt drei Monate"),
        ("Sie beginnt mit dem Zugang der Kuendigung.", (2,), "beginnt mit dem Zugang der Kuendigung")]
    assert search.calls == [(QUESTION, 10)]


def test_the_provider_receives_the_system_instruction_and_the_composed_messages_only():
    fake = FakeAnthropic()
    history = [Turn("user", "Worum geht es?"), Turn("assistant", "Um den Mietvertrag.")]
    AssistantService(search=_search(), generate=_generate(fake)).answer(QUESTION, history)
    (request,) = fake.requests
    assert set(request) == {"model", "max_tokens", "system", "messages", "output_config", "betas", "fallbacks"}
    assert request["system"] == SYSTEM_INSTRUCTION
    assert [m["role"] for m in request["messages"]] == ["user", "assistant", "user"]
    assert all(set(m) == {"role", "content"} for m in request["messages"])
    last = request["messages"][-1]["content"]
    assert PASSAGE_1 in last and PASSAGE_2 in last and QUESTION in last
    # Nothing but what search returned and what the person asked: no other passage, no pointer.
    assert "Mandate/Mueller" not in json.dumps(request)


def test_only_role_and_content_of_a_message_are_sent():
    fake = FakeAnthropic()
    _call(_generate(fake), messages=[{"role": "user", "content": "Frage?", "pointer": "Mandate/geheim.pdf"}])
    assert fake.requests[0]["messages"] == [{"role": "user", "content": "Frage?"}]


def test_no_customer_text_and_no_key_reach_a_log_line(caplog):
    caplog.set_level(logging.DEBUG)
    reply = AssistantService(search=_search(), generate=_generate(FakeAnthropic())).answer(
        QUESTION, [Turn("user", "Worum geht es im Mandat?"), Turn("assistant", "Um den Mietvertrag.")])
    assert reply.status == "answered"
    for secret in (KEY, QUESTION, PASSAGE_1, PASSAGE_2, "Kuendigungsfrist", "Mietvertrag", "Mandate/Mueller"):
        assert secret not in caplog.text
    line = next(r.getMessage() for r in caplog.records if "anthropic model=" in r.getMessage())
    assert line.startswith("Knovas Assistent: anthropic model=claude-opus-5-5 served_by=claude-opus-5-5 stop=end_turn")
    assert "input_tokens=1200 output_tokens=180" in line


# --- "Verbindung testen" ------------------------------------------------------------------------------


def test_the_connection_test_says_it_works_and_sends_invented_text_only(caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeAnthropic(response=message(text(st("Gegruendet am 3. Mai 2021.", 1, "am 3. Mai 2021"))))
    result = providers.connection_test(KEY, "claude-sonnet-5-5", client_factory=fake)
    assert result == {"ok": True, "message": "funktioniert", "model": "claude-sonnet-5-5"}
    (request,) = fake.requests
    assert request["system"] == SYSTEM_INSTRUCTION and request["model"] == "claude-sonnet-5-5"
    (only,) = request["messages"]
    assert providers.TEST_PASSAGE in only["content"] and providers.TEST_QUESTION in only["content"]
    assert fake.built == [{"api_key": KEY, "timeout": 60.0, "max_retries": 0}]
    assert "connection test ok" in caplog.text and KEY not in caplog.text and KEY not in json.dumps(result)


@pytest.mark.parametrize("error, outcome, says", [
    (_status_error(anthropic.AuthenticationError, 401), "key_refused", "Schlüssel abgelehnt"),
    (_status_error(anthropic.PermissionDeniedError, 403), "permission_denied", "Schlüssel abgelehnt"),
    (_status_error(anthropic.RateLimitError, 429), "busy", "ausgelastet"),
    (_status_error(anthropic.OverloadedError, 529), "busy", "ausgelastet"),
    (anthropic.APIConnectionError(request=_request()), "no_connection", "keine Verbindung"),
    (anthropic.APITimeoutError(request=_request()), "no_connection", "keine Verbindung"),
    (_status_error(anthropic.InternalServerError, 500), "error", "Fehler der Claude API (HTTP 500)"),
])
def test_the_connection_test_names_the_plain_failure(error, outcome, says, caplog):
    caplog.set_level(logging.DEBUG)
    result = providers.connection_test(KEY, MODEL, client_factory=FakeAnthropic(error=error))
    assert result["ok"] is False and result["outcome"] == outcome and result["error"].startswith(says)
    assert KEY not in json.dumps(result) and LEAKY not in json.dumps(result)
    assert f"connection test {outcome}" in caplog.text and KEY not in caplog.text


def test_the_connection_test_without_a_key_calls_nothing():
    fake = FakeAnthropic()
    result = providers.connection_test(None, MODEL, client_factory=fake)
    assert result == {"ok": False, "error": "kein Schlüssel gesetzt", "outcome": "no_key"}
    assert fake.built == []


def test_the_connection_test_survives_an_error_outside_the_sdk(caplog):
    caplog.set_level(logging.DEBUG)

    def broken(**kwargs):
        raise ValueError(f"Illegal header value {kwargs['api_key']}")   # e.g. the HTTP library refusing the key

    result = providers.connection_test(KEY, MODEL, client_factory=broken)
    assert result["ok"] is False and result["outcome"] == "error"
    assert KEY not in json.dumps(result) and KEY not in caplog.text and "ValueError" in caplog.text


# --- the switch's values ------------------------------------------------------------------------------


@pytest.mark.parametrize("value, on", [("an", True), ("AN", True), ("on", True), ("true", True), ("1", True),
                                       ("ja", True), (True, True), ("aus", False), ("off", False),
                                       ("false", False), ("0", False), ("", False), (None, False),
                                       ("vielleicht", False), (False, False)])
def test_what_switches_the_assistant_on(value, on):
    assert providers.is_enabled(value) is on


@pytest.mark.parametrize("value, provider", [("knovas", "knovas"), ("anthropic", "anthropic"),
                                             (" Anthropic ", "anthropic"), ("openai", None), ("", None),
                                             (None, None)])
def test_which_provider_a_value_names(value, provider):
    assert providers.chosen_provider(value) == provider
