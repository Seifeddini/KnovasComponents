"""PMVP-20 brain-mcp: <<Brain-Werkzeuge>>, Claude answering with the Knovas Brain's MCP tools (assistant.brain_tools,
assistant.providers.make_brain_mcp_ask).

Work order docs/platform-mvp/work-orders/PMVP-20-brain-mcp.md ("Your tests"): the request carries the MCP server, the
toolset and the beta (and keeps today's settings); the passages are the tool results' texts; a quote outside them is
removed; no tool result is no_answer; provider errors and the deadline (PMVP-24's numbers, a timeout busy); nothing of
the question, the answer, a passage, a key or a grant in any log line at DEBUG (FU-46, the loggers set explicitly);
the factory chooses the mode, and the passages mode is unchanged.

No test reaches Anthropic or a gateway: a fake client returns the SDK's own MCP block types, or the real SDK runs over
an in-process transport. The keys are obviously fake. This file is ASCII-only.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import anthropic
import httpx2
import pytest
from anthropic.types.beta import (BetaMCPToolResultBlock, BetaMCPToolUseBlock, BetaMessage, BetaTextBlock,
                                  BetaUsage)
from cryptography.fernet import Fernet

import platform_settings as ps
from assistant import brain_tools, check, providers
from assistant.brain_tools import BrainToolsService, ToolsReply
from assistant.errors import CoreBusy, CoreFailed
from assistant.service import BUSY_TEXT, FAILED_TEXT, NO_ANSWER_TEXT, AssistantService, Turn
from test_assistant_providers import REAL_SDK_CLIENT

KEY = "sk-ant-test-not-a-key"                               # obviously fake
GW_KEY = "knovas_gw_test-not-a-key-0123456789"              # obviously fake
GW_URL = "https://mcp.knovas.ch/mcp"
MODEL = "claude-opus-5-5"
SERVER = providers.BRAIN_MCP_SERVER_NAME

QUESTION = "Welche Kuendigungsfrist gilt im Mietvertrag Hardturm?"
PTR_LEASE = "kanzlei/Mandate/Hardturm/Mietvertrag_Hardturm.pdf"
PTR_NOTE = "kanzlei/Mandate/Hardturm/Aktennotiz.docx"
SNIP_1 = "Die Kuendigungsfrist betraegt drei Monate auf Ende Juni."
SNIP_2 = "Der Mietzins betraegt CHF 4'200 pro Monat."
SNIP_3 = "Besprechung mit Frau Meier am 12. Maerz 2024 zur Kuendigung."
SUMMARY = "Zusammenfassung: Mietvertrag ueber Bueroraeume mit sechs Monaten Kuendigungsfrist."
FETCHED = ("Mietvertrag Hardturm. Art. 4 Kuendigung. Die Kuendigungsfrist betraegt drei Monate auf Ende Juni. "
           "Eine Kuendigung bedarf der Schriftform und muss eingeschrieben zugestellt werden.")
GRANT_1 = "gAAAAABgrant-one-not-a-real-grant"
GRANT_2 = "gAAAAABgrant-two-not-a-real-grant"

S_FRIST = {"statement": "Die Kuendigungsfrist betraegt drei Monate.", "source": PTR_LEASE,
           "quote": "Die Kuendigungsfrist betraegt drei Monate"}
S_FORM = {"statement": "Die Kuendigung muss schriftlich erfolgen.", "source": PTR_LEASE,
          "quote": "Eine Kuendigung bedarf der Schriftform"}
S_MEIER = {"statement": "Frau Meier wurde am 12. Maerz 2024 besprochen.", "source": PTR_NOTE,
           "quote": "Besprechung mit Frau Meier am 12. Maerz 2024"}


def lines(*statements):
    return "\n".join(json.dumps(s, ensure_ascii=False) for s in statements)


def search_json(*results):
    return json.dumps({"notice": "quoted", "result_count": len(results), "results": list(results)},
                      ensure_ascii=False)


def hit(pointer, passages, *, grant=GRANT_1, page=None, summary=None):
    out = {"id": grant, "title": pointer, "passages": list(passages)}
    if page is not None:
        out["page"] = page
    if summary is not None:
        out["summary"] = summary
    return out


def fetch_json(pointer, text, *, grant=GRANT_1, page_from=3):
    return json.dumps({"id": grant, "title": pointer, "text": text,
                       "metadata": {"page_from": page_from, "page_to": page_from, "part_chars": len(text),
                                    "total_chars": len(text), "next_id": None, "source": "indexed_text",
                                    "truncated": False},
                       "notice": "quoted"}, ensure_ascii=False)


def use(block_id, name, tool_input, server=SERVER):
    return BetaMCPToolUseBlock(type="mcp_tool_use", id=block_id, name=name, server_name=server, input=tool_input)


def result(block_id, text, *, error=False, as_string=False):
    content = text if as_string else [BetaTextBlock(type="text", text=text, citations=None)]
    return BetaMCPToolResultBlock(type="mcp_tool_result", tool_use_id=block_id, is_error=error, content=content)


def text(value):
    return BetaTextBlock(type="text", text=value, citations=None)


def message(*blocks, stop="end_turn", msg_id="msg_brain_1"):
    return BetaMessage.model_construct(
        id=msg_id, type="message", role="assistant", model=MODEL, stop_reason=stop, stop_sequence=None,
        content=list(blocks), usage=BetaUsage.model_construct(input_tokens=5000, output_tokens=600))


def tool_turn(answer=None, *, preamble=None):
    """Two searches and one fetch, then the answer: the turn as the MCP connector returns it."""
    blocks = []
    if preamble:
        blocks.append(text(preamble))
    blocks += [
        use("mcptoolu_1", "search", {"query": "Kuendigungsfrist Hardturm"}),
        result("mcptoolu_1", search_json(hit(PTR_LEASE, [SNIP_1, SNIP_2], page=4, summary=SUMMARY),
                                         hit(PTR_NOTE, [SNIP_3], grant=GRANT_2))),
        use("mcptoolu_2", "fetch", {"id": GRANT_1}),
        result("mcptoolu_2", fetch_json(PTR_LEASE, FETCHED)),
        use("mcptoolu_3", "search", {"query": "Schriftform Kuendigung"}),
        result("mcptoolu_3", search_json(hit(PTR_LEASE, [SNIP_1]))),
    ]
    blocks.append(text(answer if answer is not None else lines(S_FRIST, S_FORM, S_MEIER)))
    return message(*blocks)


class FakeAnthropic:
    """A client factory: records how each client was built and what it was asked; answers in turn from ``replies``
    (a response, an exception to raise, or a callable taking the request)."""

    def __init__(self, *replies):
        self.replies = list(replies) or [tool_turn()]
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
        self._fake.requests.append(json.loads(json.dumps(request, default=lambda o: o.model_dump())))
        reply = self._fake.replies.pop(0) if len(self._fake.replies) > 1 else self._fake.replies[0]
        if isinstance(reply, BaseException):
            raise reply
        return reply(request) if callable(reply) else reply

    def close(self):
        self._fake.closed += 1


@pytest.fixture(autouse=True)
def no_real_anthropic(monkeypatch):
    def refuse(**kwargs):
        raise AssertionError("a test tried to build a real Anthropic client")

    monkeypatch.setattr(providers, "_sdk_client", refuse)


def _ask(fake, **kw):
    kw.setdefault("api_key", KEY)
    kw.setdefault("model", MODEL)
    kw.setdefault("gateway_url", GW_URL)
    kw.setdefault("gateway_key", GW_KEY)
    return providers.make_brain_mcp_ask(client_factory=fake, **kw)


def _service(fake=None, **kw):
    return BrainToolsService(ask=_ask(fake if fake is not None else FakeAnthropic()), **kw)


def _sdk_factory(handler, seen=None):
    def record(request):
        if seen is not None:
            seen.append(request)
        return handler(request)

    def factory(**kwargs):
        return REAL_SDK_CLIENT(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(record)))

    return factory


def _wire(answer=None, stop="end_turn"):
    return json.loads(json.dumps(tool_turn(answer).model_dump(), default=str)) | {"stop_reason": stop}


# --- the request ---------------------------------------------------------------------------------------------------


def test_the_request_carries_the_mcp_server_the_toolset_and_the_beta():
    fake = FakeAnthropic()
    reply = _service(fake).answer(QUESTION)
    assert reply.status == "answered"
    (request,) = fake.requests
    assert request == {
        "model": MODEL,
        "max_tokens": 8_000,
        "system": brain_tools.BRAIN_SYSTEM_INSTRUCTION,
        "messages": [{"role": "user", "content": "Question: " + QUESTION + "\n\n"
                      "Search, then answer only with the JSON lines your instructions describe."}],
        "output_config": {"effort": "medium"},
        "betas": ["server-side-fallback-2026-07-01", "mcp-client-2025-11-20"],
        "fallbacks": "default",
        "mcp_servers": [{"type": "url", "url": GW_URL, "name": "knovas-brain", "authorization_token": GW_KEY}],
        "tools": [{"type": "mcp_toolset", "mcp_server_name": "knovas-brain", "default_config": {"enabled": False},
                   "configs": {"search": {"enabled": True}, "fetch": {"enabled": True}}}],
    }


def test_todays_client_settings_stay():
    fake = FakeAnthropic()
    _service(fake).answer(QUESTION)
    (built,) = fake.built
    assert built["api_key"] == KEY and built["max_retries"] == 0
    assert 59 <= built["timeout"] <= providers.ANTHROPIC_TIMEOUT_SECONDS == 60.0
    assert fake.closed == 1


def test_through_the_real_sdk_the_wire_carries_the_connector_and_the_beta_header():
    seen = []
    ask = _ask(_sdk_factory(lambda r: httpx2.Response(200, json=_wire()), seen))
    reply = BrainToolsService(ask=ask).answer(QUESTION)
    assert reply.status == "answered" and len(reply.sentences) == 3
    (request,) = seen
    assert request.url.host == "api.anthropic.com" and request.url.path == "/v1/messages"
    body = json.loads(request.content)
    assert body["mcp_servers"] == [{"type": "url", "url": GW_URL, "name": "knovas-brain",
                                    "authorization_token": GW_KEY}]
    assert body["tools"] == [{"type": "mcp_toolset", "mcp_server_name": "knovas-brain",
                              "default_config": {"enabled": False},
                              "configs": {"search": {"enabled": True}, "fetch": {"enabled": True}}}]
    assert "betas" not in body
    assert request.headers["anthropic-beta"].split(",") == ["server-side-fallback-2026-07-01",
                                                           "mcp-client-2025-11-20"]
    assert not any(GW_KEY in value for value in request.headers.values())   # the gateway key is in the body only
    assert KEY not in request.content.decode("utf-8")


def test_the_history_is_sent_as_the_passages_mode_sends_it_and_the_search_text_is_not_used():
    fake = FakeAnthropic()
    history = [Turn("user", "Wer vermietet?"), Turn("assistant", "Die Hardturm AG vermietet.")]
    _service(fake).answer(QUESTION, history, search_text="Wer vermietet?\n" + QUESTION)
    messages = fake.requests[0]["messages"]
    assert messages[:2] == [{"role": "user", "content": "Wer vermietet?"},
                            {"role": "assistant", "content": "Die Hardturm AG vermietet."}]
    assert messages[2]["content"].startswith("Question: " + QUESTION + "\n\n") and len(messages) == 3


@pytest.mark.parametrize("change, reason", [
    ({"api_key": None}, "anthropic_key_missing"),
    ({"api_key": "  "}, "anthropic_key_missing"),
    ({"gateway_key": None}, "brain_mcp_key_missing"),
    ({"gateway_key": ""}, "brain_mcp_key_missing"),
    ({"gateway_url": "http://mcp.knovas.ch/mcp"}, "brain_mcp_url_refused"),
    ({"gateway_url": "https://mcp.example.com/mcp"}, "brain_mcp_url_refused"),
    ({"gateway_url": "https://mcp.knovas.ch.example.com/mcp"}, "brain_mcp_url_refused"),
    ({"gateway_url": "https://someone@mcp.knovas.ch/mcp"}, "brain_mcp_url_refused"),
    ({"model": "claude-something-else"}, "anthropic_model_unknown"),
])
def test_nothing_is_sent_without_both_keys_or_with_a_refused_address(change, reason):
    fake = FakeAnthropic()
    ask = _ask(fake, **change)
    with pytest.raises(CoreFailed) as raised:
        ask(system_instruction="x", messages=[{"role": "user", "content": "y"}], max_tokens=10,
            deadline_at=time.monotonic() + 150, min_seconds=30)
    assert raised.value.reason == reason
    assert fake.built == [] and fake.requests == []


def test_the_address_defaults_to_the_knovas_gateway():
    fake = FakeAnthropic()
    BrainToolsService(ask=_ask(fake, gateway_url=None)).answer(QUESTION)
    assert fake.requests[0]["mcp_servers"][0]["url"] == "https://mcp.knovas.ch/mcp"


# --- the passages are the tool results -----------------------------------------------------------------------------


def test_the_passages_are_the_texts_the_tool_results_returned():
    found = brain_tools.read_tool_results([tool_turn()])
    got = [(p.number, p.pointer, p.title, p.page, p.text) for p in found.passages]
    assert got == [
        (1, PTR_LEASE, "Mietvertrag_Hardturm.pdf", 4, SNIP_1),
        (2, PTR_LEASE, "Mietvertrag_Hardturm.pdf", 4, SNIP_2),
        (3, PTR_NOTE, "Aktennotiz.docx", None, SNIP_3),
        (4, PTR_LEASE, "Mietvertrag_Hardturm.pdf", 3, FETCHED),
    ]                                                   # the second search's SNIP_1 is the same passage: once
    assert found.calls == {"search": 2, "fetch": 1, "other": 0} and found.results == 3 and found.documents == 2
    assert all(SUMMARY != p.text for p in found.passages)            # a summary is not the document's own words


def test_only_the_gateways_answered_and_readable_results_count():
    turn = message(
        use("u1", "search", {"query": "a"}, server="another-server"),
        result("u1", search_json(hit("fremd/Dokument.pdf", ["Ein fremder Text mit mehreren Worten."]))),
        use("u2", "search", {"query": "b"}),
        result("u2", search_json(hit(PTR_NOTE, [SNIP_3])), error=True),
        result("u9", search_json(hit(PTR_NOTE, [SNIP_3]))),                  # answers no tool call of ours
        use("u3", "search", {"query": "c"}),
        result("u3", "kein JSON", as_string=True),
        use("u4", "fetch", {"id": GRANT_1}),
        result("u4", fetch_json(PTR_LEASE, FETCHED), as_string=True),         # a plain string content counts
        use("u5", "search", {"query": "d"}),
        result("u5", json.dumps({"results": [], "result_count": 0, "busy": True, "message": "busy"})),
        use("u6", "search", {"query": "e"}),
        result("u6", search_json({"id": GRANT_2, "passages": ["ohne Titel kein Dokument"]},
                                 hit(" ", ["leerer Titel"]))),
        text(lines(S_FRIST)),
    )
    found = brain_tools.read_tool_results([turn])
    assert [(p.pointer, p.text) for p in found.passages] == [(PTR_LEASE, FETCHED)]
    assert (found.results, found.errors, found.unmatched, found.unreadable, found.busy) == (7, 1, 2, 1, 1)
    assert found.calls == {"search": 4, "fetch": 1, "other": 0}


def test_an_answer_from_the_tool_results_is_shown_with_the_passages_it_cites():
    reply = _service().answer(QUESTION)
    assert isinstance(reply, ToolsReply) and reply.opens_directly is False
    assert reply.status == "answered" and reply.removed == 0
    assert [(s.text, s.refs, s.quote) for s in reply.sentences] == [
        ("Die Kuendigungsfrist betraegt drei Monate.", (1,), "Die Kuendigungsfrist betraegt drei Monate"),
        ("Die Kuendigung muss schriftlich erfolgen.", (2,), "Eine Kuendigung bedarf der Schriftform"),
        ("Frau Meier wurde am 12. Maerz 2024 besprochen.", (3,), "Besprechung mit Frau Meier am 12. Maerz 2024"),
    ]
    # Only the cited passages, renumbered in the order first cited; the shortest passage that holds the quote.
    assert [(p.number, p.pointer, p.text) for p in reply.passages] == [
        (1, PTR_LEASE, SNIP_1), (2, PTR_LEASE, FETCHED), (3, PTR_NOTE, SNIP_3)]


def test_a_quote_outside_the_tool_results_is_removed():
    outside = [
        {**S_FRIST, "quote": "Mietvertrag ueber Bueroraeume mit sechs Monaten"},      # the summary: not a passage
        {**S_FRIST, "quote": "Die Kuendigungsfrist betraegt sechs Monate"},           # invented
        {**S_FRIST, "source": "kanzlei/Mandate/Andere/Vertrag.pdf"},                  # a title no tool returned
        {**S_FRIST, "source": 1},                                                     # a number names nothing
        {**S_FRIST, "source": PTR_NOTE},                                              # the wrong document
        {"statement": "Die Kuendigungsfrist betraegt drei Monate.", "quote": "x y z"},  # no source
    ]
    reply = _service(FakeAnthropic(tool_turn(lines(*outside)))).answer(QUESTION)
    assert reply.status == "no_answer" and reply.sentences == [] and reply.message == NO_ANSWER_TEXT
    assert reply.removed == 6
    assert reply.removed_by == {"not_a_statement": 1, "unknown_source": 2, "quote_not_found": 3,
                                "number_not_found": 0, "too_long": 0}
    assert [p.pointer for p in reply.passages] == [PTR_LEASE, PTR_NOTE]           # the closest: one per document


def test_a_quote_is_never_put_together_from_two_passages():
    across = {**S_FRIST, "quote": "auf Ende Juni. Der Mietzins betraegt"}           # end of SNIP_1 + SNIP_2
    reply = _service(FakeAnthropic(tool_turn(lines(across, S_FRIST)))).answer(QUESTION)
    assert [s.quote for s in reply.sentences] == ["Die Kuendigungsfrist betraegt drei Monate"]
    assert reply.removed_by["quote_not_found"] == 1


def test_a_number_must_stand_in_the_passage_its_quote_is_from():
    wrong = {"statement": "Die Kuendigungsfrist betraegt sechs Monate.", "source": PTR_LEASE,
             "quote": "Die Kuendigungsfrist betraegt drei Monate"}
    reply = _service(FakeAnthropic(tool_turn(lines(wrong)))).answer(QUESTION)
    assert reply.status == "no_answer" and reply.removed_by["number_not_found"] == 1


def test_the_title_may_be_given_without_its_folders_when_one_document_has_it():
    short = {**S_FRIST, "source": "mietvertrag_hardturm.PDF"}
    reply = _service(FakeAnthropic(tool_turn(lines(short)))).answer(QUESTION)
    assert reply.status == "answered" and reply.passages[0].pointer == PTR_LEASE


def test_the_check_is_the_unchanged_one(monkeypatch):
    calls = []
    real = check.check_answer

    def spy(answer, passages):
        calls.append(dict(passages))
        return real(answer, passages)

    monkeypatch.setattr(check, "check_answer", spy)
    _service().answer(QUESTION)
    assert calls[-1] == {1: SNIP_1, 2: SNIP_2, 3: SNIP_3, 4: FETCHED}               # the last run: every passage


def test_what_claude_writes_before_a_tool_call_is_not_the_answer():
    reply = _service(FakeAnthropic(tool_turn(lines(S_FRIST), preamble="Ich suche zuerst."))).answer(QUESTION)
    assert reply.status == "answered" and reply.removed == 0


@pytest.mark.parametrize("turn", [
    message(text(lines(S_FRIST))),                                                        # no tool called
    message(use("u1", "search", {"query": "q"}), result("u1", "nicht lesbar", as_string=True), text(lines(S_FRIST))),
    message(use("u1", "search", {"query": "q"}), result("u1", search_json(hit(PTR_LEASE, [SNIP_1])), error=True),
            text(lines(S_FRIST))),
    message(use("u1", "search", {"query": "q"}), result("u1", search_json()), text('{"no_answer": true}')),
])
def test_no_tool_result_means_no_answer(turn):
    reply = _service(FakeAnthropic(turn)).answer(QUESTION)
    assert reply.status == "no_answer" and reply.sentences == [] and reply.passages == []
    assert reply.message == NO_ANSWER_TEXT


def test_a_gateway_that_was_only_busy_is_busy():
    turn = message(use("u1", "search", {"query": "q"}),
                   result("u1", json.dumps({"results": [], "result_count": 0, "busy": True})),
                   text('{"no_answer": true}'))
    reply = _service(FakeAnthropic(turn)).answer(QUESTION)
    assert reply.status == "busy" and reply.message == BUSY_TEXT


def test_a_paused_turn_is_continued_as_it_came_and_its_results_count():
    first = message(use("mcptoolu_1", "search", {"query": "Kuendigungsfrist"}),
                    result("mcptoolu_1", search_json(hit(PTR_LEASE, [SNIP_1]))), stop="pause_turn", msg_id="msg_a")
    second = message(text(lines(S_FRIST)), msg_id="msg_b")
    fake = FakeAnthropic(first, second)
    reply = _service(fake).answer(QUESTION)
    assert reply.status == "answered" and reply.passages[0].text == SNIP_1
    assert len(fake.requests) == 2
    again = fake.requests[1]["messages"]
    assert again[:-1] == fake.requests[0]["messages"] and again[-1]["role"] == "assistant"
    assert [b["type"] for b in again[-1]["content"]] == ["mcp_tool_use", "mcp_tool_result"]


def test_a_turn_paused_too_often_ends_as_it_is():
    paused = message(use("mcptoolu_1", "search", {"query": "q"}),
                     result("mcptoolu_1", search_json(hit(PTR_LEASE, [SNIP_1]))), stop="pause_turn")
    fake = FakeAnthropic(paused)
    reply = _service(fake).answer(QUESTION)
    assert len(fake.requests) == 1 + providers.BRAIN_MCP_MAX_CONTINUATIONS
    assert reply.status == "no_answer" and [p.text for p in reply.passages] == [SNIP_1]


# --- errors and the deadline ---------------------------------------------------------------------------------------


def _status(code):
    return lambda request: httpx2.Response(code, json={"type": "error", "error": {"type": "x", "message": "m"}})


@pytest.mark.parametrize("handler, status", [
    (_status(429), "busy"),
    (_status(529), "busy"),
    (_status(401), "failed"),
    (_status(403), "failed"),
    (_status(400), "failed"),
    (_status(500), "failed"),
    (_status(503), "failed"),
])
def test_provider_errors_map_as_in_the_passages_mode(handler, status):
    seen = []
    reply = BrainToolsService(ask=_ask(_sdk_factory(handler, seen))).answer(QUESTION)
    assert reply.status == status and len(seen) == 1                      # no retry
    assert reply.message == (BUSY_TEXT if status == "busy" else FAILED_TEXT)
    assert reply.sentences == [] and reply.passages == []


def test_no_connection_fails_and_a_refusal_fails():
    def gone(request):
        raise httpx2.ConnectError("no route", request=request)

    assert BrainToolsService(ask=_ask(_sdk_factory(gone))).answer(QUESTION).status == "failed"
    refused = _wire(lines(S_FRIST), stop="refusal")
    assert BrainToolsService(ask=_ask(_sdk_factory(lambda r: httpx2.Response(200, json=refused)))).answer(
        QUESTION).status == "failed"


def test_a_timeout_is_busy_as_pmvp24_decided():
    def slow(request):
        raise httpx2.ReadTimeout("slow", request=request)

    reply = BrainToolsService(ask=_ask(_sdk_factory(slow))).answer(QUESTION)
    assert reply.status == "busy" and reply.message == BUSY_TEXT


def test_the_mapped_error_carries_nothing_of_the_sdks():
    for error in (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")),
                  RuntimeError("text that may hold " + GW_KEY)):
        ask = _ask(FakeAnthropic(error))
        with pytest.raises((CoreBusy, CoreFailed)) as raised:
            ask(system_instruction="x", messages=[{"role": "user", "content": "y"}], max_tokens=10,
                deadline_at=time.monotonic() + 150, min_seconds=30)
        assert raised.value.__cause__ is None and raised.value.__context__ is None
        assert GW_KEY not in str(raised.value) and KEY not in str(raised.value)


def test_the_wall_clock_deadline_ends_a_call_that_does_not_come_back():
    release = threading.Event()

    def hangs(request):
        release.wait(10)
        return tool_turn()

    fake = FakeAnthropic(hangs)
    started = time.monotonic()
    reply = _service(fake, deadline_seconds=0.5, min_seconds=0.0).answer(QUESTION)
    took = time.monotonic() - started
    release.set()
    assert reply.status == "busy" and took < 3
    assert fake.built[0]["timeout"] <= 0.5


def test_a_request_is_not_started_with_less_than_the_least_time_left():
    fake = FakeAnthropic()
    reply = _service(fake, deadline_seconds=20).answer(QUESTION)             # 20 s left, 30 s needed
    assert reply.status == "busy" and fake.built == []


def test_each_request_waits_at_most_the_time_left_and_the_answer_at_most_150_seconds():
    assert brain_tools.ANSWER_DEADLINE_SECONDS == 150.0 and brain_tools.MIN_GENERATE_SECONDS == 30.0
    now = [1000.0]
    fake = FakeAnthropic()
    service = BrainToolsService(ask=providers.make_brain_mcp_ask(
        api_key=KEY, gateway_key=GW_KEY, client_factory=fake, clock=lambda: now[0] + 110.0),
        clock=lambda: now[0])                                                  # 110 s gone when the call starts
    assert service.answer(QUESTION).status == "answered"
    assert 39 <= fake.built[0]["timeout"] <= 40.0


def test_a_continuation_is_not_started_without_time_and_the_turn_ends_as_it_is():
    now = [0.0]
    paused = message(use("mcptoolu_1", "search", {"query": "q"}),
                     result("mcptoolu_1", search_json(hit(PTR_LEASE, [SNIP_1]))), stop="pause_turn")

    def answer_and_age(request):
        now[0] += 130.0                                                         # the first request took 130 s
        return paused

    fake = FakeAnthropic(answer_and_age)
    service = BrainToolsService(ask=providers.make_brain_mcp_ask(
        api_key=KEY, gateway_key=GW_KEY, client_factory=fake, clock=lambda: now[0]), clock=lambda: now[0])
    reply = service.answer(QUESTION)
    assert len(fake.requests) == 1 and reply.status == "no_answer"


def test_an_unexpected_error_fails_and_answer_never_raises():
    def broken(**kwargs):
        raise ValueError("boom")

    assert BrainToolsService(ask=broken).answer(QUESTION).status == "failed"
    assert BrainToolsService(ask=lambda **kw: object()).answer(QUESTION).status == "no_answer"
    assert BrainToolsService(ask=broken).answer("   ").status == "no_answer"
    assert BrainToolsService(ask=broken).answer("x" * 5000).status == "failed"


# --- logs ----------------------------------------------------------------------------------------------------------

LOGGERS = ("", "assistant", "assistant.brain_tools", "assistant.providers", "assistant.service", "web_interface",
           "anthropic", "anthropic._base_client", "anthropic._response", "httpx", "httpx2", "httpcore", "httpcore2")
NEVER = (QUESTION, SNIP_1, SNIP_2, SNIP_3, FETCHED, SUMMARY, S_FRIST["statement"], S_FORM["statement"],
         S_FRIST["quote"], S_MEIER["quote"], KEY, GW_KEY, GRANT_1, GRANT_2, PTR_LEASE, PTR_NOTE,
         "Mietvertrag_Hardturm", "Aktennotiz", "Hardturm", "Kuendigung")


@pytest.fixture
def debug_everywhere():
    """FU-46: every logger that could see the call set to DEBUG explicitly, and back afterwards."""
    saved = {name: logging.getLogger(name).level for name in LOGGERS}
    for name in LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


def test_no_question_answer_passage_key_or_grant_in_any_log_line_at_debug(debug_everywhere, caplog):
    caplog.set_level(logging.DEBUG)
    handlers = [lambda r: httpx2.Response(200, json=_wire()), _status(401)]
    for handler in handlers:
        BrainToolsService(ask=_ask(_sdk_factory(handler))).answer(
            QUESTION, [Turn("user", "Wer vermietet Hardturm?"), Turn("assistant", "Die Hardturm AG vermietet.")])

    def gone(request):
        raise httpx2.ConnectError("no route to host", request=request)

    BrainToolsService(ask=_ask(_sdk_factory(gone))).answer(QUESTION)
    every = "\n".join(f"{r.name} {r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    assert caplog.records, "nothing was logged"
    for secret in NEVER:
        assert secret not in every, secret
    counted = [r.getMessage() for r in caplog.records if "brain tools status=answered" in r.getMessage()]
    assert len(counted) == 1
    assert ("messages=msg_brain_1 stop=end_turn tool_calls=3 search=2 fetch=1 other=0 tool_results=3 errors=0 busy=0 "
            "unreadable=0 unmatched=0 passages=4 documents=2") in counted[0]
    assert "statements=3 removed=0" in counted[0] and "cited_passages=3" in counted[0]


def test_control_without_the_guard_the_sdk_would_log_the_gateway_key(debug_everywhere, caplog, monkeypatch):
    """Shows the log test above can fail: the SDK's own DEBUG line holds the request body."""
    monkeypatch.setattr(providers, "_hold_back_brain_request_options", lambda: None)
    target = logging.getLogger("anthropic._base_client")
    monkeypatch.setattr(target, "filters", [f for f in target.filters if f is not providers._REQUEST_OPTIONS_GUARD])
    caplog.set_level(logging.DEBUG)
    BrainToolsService(ask=_ask(_sdk_factory(lambda r: httpx2.Response(200, json=_wire())))).answer(QUESTION)
    assert GW_KEY in caplog.text and QUESTION in caplog.text


# --- the factory: the mode chooses, passages unchanged ------------------------------------------------------------


class _Config:
    def __init__(self, path: Path):
        self.config_path = str(path)

    def get(self, key, default=None):
        return default


class _MemoryStore:
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


@pytest.fixture
def resolver(tmp_path):
    def build(environ=None):
        raw = (Path(__file__).resolve().parents[1] / "config" / "config.yaml").read_text(encoding="utf-8")
        path = tmp_path / "config.yaml"
        path.write_text(raw, encoding="utf-8")
        return ps.Resolver(_Config(path), tenant="t", store=lambda: store, environ=environ or {},
                           box=ps.SecretBox(Fernet.generate_key().decode()))

    store = _MemoryStore()
    return build


class _Client:
    """The Knovas client as make_service needs it: nothing is searched here."""

    def secured_mode(self):
        return True


def _split(answer):
    return answer, []


def _make(settings, fake):
    from assistant.core import make_service

    return make_service(settings, _Client(), split=_split, anthropic_client=fake)


def test_the_default_mode_is_passages_and_its_request_is_todays(resolver):
    settings = resolver()
    settings.save(_Admin(), {"assistant.enabled": "an", "assistant.provider": "anthropic",
                             "assistant.anthropic_api_key": KEY, "assistant.brain_mcp_key": GW_KEY},
                  roles=("admin",))
    assert settings.value("assistant.mode") == "passages"
    assert settings.value("assistant.brain_mcp_url") == "https://mcp.knovas.ch/mcp"
    assert isinstance(_make(settings, FakeAnthropic()), AssistantService)


def test_brain_mcp_with_anthropic_uses_the_saved_keys_model_and_address(resolver):
    settings = resolver()
    settings.save(_Admin(), {"assistant.enabled": "an", "assistant.provider": "anthropic",
                             "assistant.model": "claude-sonnet-5-5", "assistant.anthropic_api_key": KEY,
                             "assistant.brain_mcp_key": GW_KEY, "assistant.mode": "brain_mcp",
                             "assistant.brain_mcp_url": "https://gateway.knovas.ch/mcp"}, roles=("admin",))
    fake = FakeAnthropic()
    service = _make(settings, fake)
    assert isinstance(service, BrainToolsService)
    assert service.answer(QUESTION).status == "answered"
    (request,) = fake.requests
    assert request["model"] == "claude-sonnet-5-5" and fake.built[0]["api_key"] == KEY
    assert request["mcp_servers"][0]["url"] == "https://gateway.knovas.ch/mcp"
    assert request["mcp_servers"][0]["authorization_token"] == GW_KEY


@pytest.mark.parametrize("environ", [
    {"ASSISTANT_MODE": "brain_mcp", "ASSISTANT_ENABLED": "an", "ASSISTANT_PROVIDER": "knovas"},
    {"ASSISTANT_MODE": "something", "ASSISTANT_ENABLED": "an", "ASSISTANT_PROVIDER": "anthropic"},
    {"ASSISTANT_MODE": "brain_mcp", "ASSISTANT_ENABLED": "an", "ASSISTANT_PROVIDER": "anthropic",
     "ASSISTANT_BRAIN_MCP_URL": "https://gateway.example.com/mcp"},
])
def test_brain_mcp_pinned_without_what_it_needs_fails_and_sends_nothing(resolver, environ):
    settings = resolver(environ)
    settings.save(_Admin(), {"assistant.anthropic_api_key": KEY, "assistant.brain_mcp_key": GW_KEY},
                  roles=("admin",))
    fake = FakeAnthropic()
    service = _make(settings, fake)
    assert isinstance(service, BrainToolsService)
    assert service.answer(QUESTION).status == "failed" and fake.built == []


def test_brain_mcp_without_the_gateway_key_fails_and_sends_nothing(resolver):
    settings = resolver({"ASSISTANT_MODE": "brain_mcp"})
    settings.save(_Admin(), {"assistant.enabled": "an", "assistant.provider": "anthropic",
                             "assistant.anthropic_api_key": KEY}, roles=("admin",))
    fake = FakeAnthropic()
    assert _make(settings, fake).answer(QUESTION).status == "failed" and fake.built == []


def test_the_mode_applies_without_a_restart(resolver):
    settings = resolver()
    settings.save(_Admin(), {"assistant.enabled": "an", "assistant.provider": "anthropic",
                             "assistant.anthropic_api_key": KEY, "assistant.brain_mcp_key": GW_KEY},
                  roles=("admin",))
    assert isinstance(_make(settings, FakeAnthropic()), AssistantService)
    settings.save(_Admin(), {"assistant.mode": "brain_mcp"}, roles=("admin",))
    assert isinstance(_make(settings, FakeAnthropic()), BrainToolsService)
    settings.save(_Admin(), {"assistant.mode": None}, roles=("admin",))
    assert isinstance(_make(settings, FakeAnthropic()), AssistantService)


def test_switched_off_nothing_is_built_whatever_the_mode(resolver):
    from assistant.core import OFF

    settings = resolver({"ASSISTANT_MODE": "brain_mcp"})
    assert _make(settings, FakeAnthropic()) is OFF


def test_the_passages_requests_carry_no_connector():
    """The passages mode is today's: test_assistant_providers.py pins its request; here only that nothing of the
    connector leaks into it."""
    from test_assistant_providers import FakeAnthropic as PassagesFake
    from test_assistant_providers import _call, _generate

    fake = PassagesFake()
    _call(_generate(fake))
    (request,) = fake.requests
    assert "mcp_servers" not in request and "tools" not in request
    assert request["betas"] == ["server-side-fallback-2026-07-01"]


# --- the verifier's notes (fix round) ------------------------------------------------------------------------------


def test_a_passage_from_a_tool_result_is_cut_to_the_gateways_part_size():
    huge = ("Die Kuendigungsfrist betraegt drei Monate auf Ende Juni. " * 100_000)[:5_600_000]
    turn = message(use("u1", "fetch", {"id": GRANT_1}), result("u1", fetch_json(PTR_LEASE, huge)),
                   text(lines(S_FRIST)))
    started = time.monotonic()
    found = brain_tools.read_tool_results([turn])
    (passage,) = found.passages
    assert len(passage.text) <= brain_tools.MAX_PASSAGE_CHARS == 20_000 and found.cut == 1
    assert huge.startswith(passage.text) and huge[len(passage.text)] == " "   # cut at a word boundary
    reply = _service(FakeAnthropic(turn)).answer(QUESTION)
    assert reply.status == "answered" and len(reply.passages[0].text) <= 20_000
    assert time.monotonic() - started < 5


def test_all_passages_of_one_answer_together_are_capped():
    blocks = []
    for i in range(14):                                                        # 14 whole parts: 280,000 characters
        part = (f"Teil {i} " + "Wort " * 4_000)[:20_000]
        blocks += [use(f"u{i}", "fetch", {"id": GRANT_1}), result(f"u{i}", fetch_json(f"akte/Dok{i}.pdf", part))]
    found = brain_tools.read_tool_results([message(*blocks, text('{"no_answer": true}'))])
    assert sum(len(p.text) for p in found.passages) <= brain_tools.MAX_TOOL_CHARS == 200_000
    assert len(found.passages) == 10 and found.not_read == 4


def test_a_second_pause_keeps_one_assistant_message_after_the_question():
    first = message(use("mcptoolu_1", "search", {"query": "Kuendigungsfrist"}),
                    result("mcptoolu_1", search_json(hit(PTR_LEASE, [SNIP_1]))), stop="pause_turn", msg_id="msg_a")
    second = message(use("mcptoolu_2", "fetch", {"id": GRANT_1}),
                     result("mcptoolu_2", fetch_json(PTR_LEASE, FETCHED)), stop="pause_turn", msg_id="msg_b")
    third = message(text(lines(S_FRIST, S_FORM)), msg_id="msg_c")
    fake = FakeAnthropic(first, second, third)
    reply = _service(fake).answer(QUESTION, [Turn("user", "Wer vermietet?"), Turn("assistant", "Die Hardturm AG.")])
    assert reply.status == "answered" and len(reply.sentences) == 2
    asked = fake.requests[0]["messages"]
    for request in fake.requests[1:]:
        roles = [m["role"] for m in request["messages"]]
        assert all(a != b for a, b in zip(roles, roles[1:])), roles             # the roles alternate
        assert request["messages"][:-1] == asked and roles[-1] == "assistant"
    assert [b["id"] for b in fake.requests[1]["messages"][-1]["content"] if b["type"] == "mcp_tool_use"] == [
        "mcptoolu_1"]
    assert [b["id"] for b in fake.requests[2]["messages"][-1]["content"] if b["type"] == "mcp_tool_use"] == [
        "mcptoolu_1", "mcptoolu_2"]


@pytest.mark.parametrize("address", ["https://mcp.kn\u00f6vas.ch/mcp", "https://mcp.knovas.ch/m\u00fcp",
                                     "https://mcp.knovas\uff0ech/mcp", "https://xn--mcp.knovas.ch/\u2024"])
def test_an_address_that_is_not_all_ascii_is_refused(address):
    assert providers.brain_mcp_url_problem(address) == (
        "Die Adresse des Knovas-Gateways darf nur ASCII-Zeichen enthalten.")
    fake = FakeAnthropic()
    assert BrainToolsService(ask=_ask(fake, gateway_url=address)).answer(QUESTION).status == "failed"
    assert fake.built == []


def test_the_routes_deadline_bounds_a_brain_turn(resolver):
    from assistant.core import Deadline

    settings = resolver({"ASSISTANT_MODE": "brain_mcp", "ASSISTANT_ENABLED": "an", "ASSISTANT_PROVIDER": "anthropic"})
    settings.save(_Admin(), {"assistant.anthropic_api_key": KEY, "assistant.brain_mcp_key": GW_KEY},
                  roles=("admin",))
    from assistant.core import make_service

    late = FakeAnthropic()
    service = make_service(settings, _Client(), split=_split, anthropic_client=late,
                           deadline=Deadline(150, started=time.monotonic() - 125))       # 25 s left
    assert service.answer(QUESTION).status == "busy" and late.built == []
    fake = FakeAnthropic()
    service = make_service(settings, _Client(), split=_split, anthropic_client=fake,
                           deadline=Deadline(150, started=time.monotonic() - 110))       # 40 s left
    assert service.answer(QUESTION).status == "answered"
    assert 38 <= fake.built[0]["timeout"] <= 40.0
