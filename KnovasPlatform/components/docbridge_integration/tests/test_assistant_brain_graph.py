"""PMVP-20 brain-graph: <<Wissensgraph>>, Claude answering with client tools that read the tenant's knowledge graph
(assistant.brain_graph, assistant.graph_reads, assistant.providers.make_brain_graph_turn).

The tool loop (the assistant's content sent back, every tool result of a round in one message, the last request
without tools), the passages (a node's facts as plain lines, a count as "Anzahl der Treffer: N", the pointer
brain:<node id>), the unchanged check (a quote outside the passages and a number not in the cited passage are
removed), the bounds (rounds, tool calls, the fact budget, the time reserve), a 429 waited out and a busy graph, the
no-answer, the logs (no question, no name, no fact value), the factory, the settings and the dossier link.

No test reaches Anthropic or a Core: a fake Core holds an invented graph in memory, a fake client returns the SDK's own
block types, or the real SDK runs over an in-process transport. The keys are obviously fake. This file is ASCII-only.
"""
from __future__ import annotations

import contextlib
import json
import logging

import anthropic
import httpx2
import pytest
from anthropic.types.beta import BetaMessage, BetaTextBlock, BetaToolUseBlock, BetaUsage
from flask import Blueprint, Flask

from assistant import brain_graph, check, graph_reads as gr, providers
from assistant.brain_graph import BrainGraphService, GraphReply
from assistant.errors import CoreBusy, CoreFailed
from assistant.service import BUSY_TEXT, FAILED_TEXT, NO_ANSWER_TEXT
from test_assistant_providers import REAL_SDK_CLIENT

KEY = "sk-ant-test-not-a-key"                               # obviously fake
MODEL = "claude-opus-5-5"
TODAY = "2026-11-28"

QUESTION = "Wie viele offene Akten fuehrt Dr. Markus Baer?"

T_AKTE, T_MA, T_PARTEI, T_DOK, T_RE = "t-akte", "t-ma", "t-partei", "t-dok", "t-re"
TYPES = {T_AKTE: "Akte", T_MA: "Mitarbeiter", T_PARTEI: "Partei", T_DOK: "Dokument", T_RE: "Rechnung"}


def fact(label, value, **extra):
    return {"label": label, "value": value, "valid_from": "2026-01-01", "valid_to": None, "curation_status": "manual",
            **extra}


NODES = {
    "ma-1": ("Dr. Markus Baer", T_MA, "Rechtsanwalt, Partner (MB)",
             [fact("key", "MA-MB"), fact("rolle", "Partner"), fact("mapping", "kanzleidb-stamm")]),
    "ma-2": ("Anna Steiner", T_MA, "", [fact("key", "MA-AS")]),
    "akte-1": ("2024-017 Huber gegen Rueegg", T_AKTE, "",
               [fact("key", "2024-017"), fact("status", "offen"), fact("ampel", "rot"),
                fact("status", "sistiert", valid_to="2025-01-01"),            # closed: not in force
                fact("streitwert", "CHF 999", curation_status="rejected")]),  # rejected: never shown
    "akte-2": ("2024-021 Keller Erbteilung", T_AKTE, "", [fact("key", "2024-021"), fact("status", "offen")]),
    "akte-3": ("2023-090 Moser Mietrecht", T_AKTE, "", [fact("key", "2023-090"), fact("status", "abgeschlossen")]),
    "akte-4": ("2024-030 Frei Arbeitsrecht", T_AKTE, "", [fact("key", "2024-030"), fact("status", "offen")]),
    "p-1": ("Rueegg Haustechnik AG", T_PARTEI, "", [fact("key", "ADR-7")]),
    "p-2": ("Huber Bau GmbH", T_PARTEI, "", [fact("key", "ADR-8")]),
    "p-3": ("Keller Erben", T_PARTEI, "", [fact("key", "ADR-9")]),
    "dok-1": ("Klage", T_DOK, "", [fact("key", "DOK-1"), fact("doktyp", "Klage")]),
    "dok-2": ("Ergebnis Konfliktpruefung", T_DOK, "", [fact("key", "DOK-2"), fact("doktyp", "Aktennotiz")]),
    "re-1": ("Honorarnote 51", T_RE, "", [fact("key", "RE-1"), fact("zahlstatus", "gemahnt")]),
    "re-2": ("Honorarnote 52", T_RE, "", [fact("key", "RE-2"), fact("zahlstatus", "bezahlt")]),
}
EDGES = [
    ("akte-1", "verantwortlich", "ma-1"), ("akte-2", "verantwortlich", "ma-1"), ("akte-3", "verantwortlich", "ma-1"),
    ("akte-4", "verantwortlich", "ma-2"),
    ("akte-1", "mandant", "p-2"), ("akte-1", "gegenpartei", "p-1"), ("akte-2", "mandant", "p-3"),
    ("dok-1", "dokument_von", "akte-1"), ("dok-2", "dokument_von", "akte-1"), ("dok-2", "konfliktpruefung_zu", "akte-1"),
    ("re-1", "rechnung_von", "akte-2"), ("re-2", "rechnung_von", "akte-1"),
]


class FakeCore:
    """The graph routes the tools read, in memory. ``statuses``: what the next calls answer instead (429 ...)."""

    def __init__(self, *, statuses=(), batch=True):
        self.statuses = list(statuses)
        self.batch = batch
        self.paths, self.batches, self.bounded = [], [], 0

    @contextlib.contextmanager
    def within_time(self, time_left):
        self.bounded += 1
        yield

    def _injected(self):
        return self.statuses.pop(0) if self.statuses else 200

    def graph_read(self, path):
        self.paths.append(path)
        status = self._injected()
        if status != 200:
            return status, {"error_code": "rate_limited"}
        if path == "/node-types":
            return 200, {"node_types": [{"id": k, "name": v, "description": f"Beschreibung {v}"}
                                        for k, v in TYPES.items()]}
        if path == "/nodes":
            return 200, {"nodes": [{"id": k, "name": n, "node_type_id": t, "description": d}
                                   for k, (n, t, d, _f) in NODES.items()]}
        if path == "/edges":
            return 200, {"edges": [{"id": f"e{i}", "node_lo": lo, "node_hi": hi, "relation": rel}
                                   for i, (lo, rel, hi) in enumerate(EDGES)]}
        if path.startswith("/nodes/") and path.endswith("/facts"):
            node = path.split("/")[2]
            return (200, {"facts": NODES[node][3]}) if node in NODES else (404, {})
        if path.startswith("/node-types/") and path.endswith("/schema"):
            return 200, {"attributes": [{"name": "status", "datatype": "enum", "enum_values": ["offen", "abgeschlossen"],
                                         "description": "Stand der Akte"}, {"name": "mapping"}]}
        return 404, {}

    def graph_facts_batch(self, ids):
        self.batches.append(list(ids))
        status = self._injected()
        if not self.batch:
            return 404, {}
        if status != 200:
            return status, {}
        return 200, {"facts_by_node": {i: NODES[i][3] for i in ids if i in NODES}}


FREE = gr.TokenBucket(rate_per_minute=10 ** 9, burst=10 ** 6)


def session_for(core, *, time_left=lambda: 150.0, reserve=0.0, sleeps=None, **kw):
    port = gr.GraphPort(core, time_left=time_left, reserve_seconds=reserve, bucket=FREE,
                        sleep=(sleeps.append if sleeps is not None else (lambda s: None)))
    return gr.GraphSession(port, today=TODAY, **kw)


# --- the fake Claude ---------------------------------------------------------------------------------------------


def text(value):
    return BetaTextBlock(type="text", text=value, citations=None)


def tool_use(block_id, name, tool_input):
    return BetaToolUseBlock.model_construct(type="tool_use", id=block_id, name=name, input=tool_input)


def message(*blocks, stop="end_turn", msg_id="msg_graph_1"):
    return BetaMessage.model_construct(
        id=msg_id, type="message", role="assistant", model=MODEL, stop_reason=stop, stop_sequence=None,
        content=list(blocks), usage=BetaUsage.model_construct(input_tokens=3000, output_tokens=300))


def calls(*uses, msg_id="msg_graph_1"):
    return message(*[tool_use(f"toolu_{msg_id}_{i}", name, args) for i, (name, args) in enumerate(uses)],
                   stop="tool_use", msg_id=msg_id)


def lines(*values):
    return "\n".join(json.dumps(v, ensure_ascii=False) for v in values)


def results_of(request):
    """The tool results of the request's last message, decoded."""
    content = request["messages"][-1]["content"]
    return [(block, json.loads(block["content"])) for block in content]


class FakeAnthropic:
    """A client factory answering in turn from ``replies`` (a response, an exception, or a callable taking the
    request); records each request as JSON."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests, self.raw = [], []

    def __call__(self, **kwargs):
        fake = self

        class Client:
            def __init__(self):
                self.beta = type("Beta", (), {})()
                self.beta.messages = type("Messages", (), {})()
                self.beta.messages.create = self.create

            def create(self, **request):
                fake.raw.append(request)
                fake.requests.append(json.loads(json.dumps(request, default=lambda o: o.model_dump())))
                reply = fake.replies.pop(0) if len(fake.replies) > 1 else fake.replies[0]
                if isinstance(reply, BaseException):
                    raise reply
                return reply(request) if callable(reply) else reply

            def close(self):
                pass

        return Client()


@pytest.fixture(autouse=True)
def no_real_anthropic(monkeypatch):
    def refuse(**kwargs):
        raise AssertionError("a test tried to build a real Anthropic client")

    monkeypatch.setattr(providers, "_sdk_client", refuse)


def service(fake, core=None, *, search=None, sleeps=None, time_left_reserve=0.0, **kw):
    core = core if core is not None else FakeCore()
    turn = providers.make_brain_graph_turn(api_key=KEY, model=MODEL, client_factory=fake)
    return BrainGraphService(turn=turn, search=search,
                             open_graph=lambda left: session_for(core, time_left=left, reserve=time_left_reserve,
                                                                 sleeps=sleeps), **kw)


def find_then_count(answer):
    """Round 1: find the person; round 2: count the open matters joined to them; round 3: ``answer(count passage)``."""

    def second(request):
        (_block, found), = results_of(request)
        node_id = found["nodes"][0]["node_id"]
        return calls((brain_graph.TOOL_NEIGHBOURS, {"node_id": node_id, "relation": "verantwortlich",
                                                    "direction": "in", "type": "Akte",
                                                    "where": [{"fact": "status", "op": "equals", "value": "offen"}]}),
                     msg_id="msg_graph_2")

    def third(request):
        (_block, counted), = results_of(request)
        return message(text(answer(counted)), msg_id="msg_graph_3")

    return [calls((brain_graph.TOOL_FIND, {"text": "Markus Baer", "type": "Mitarbeiter"})), second, third]


def count_statement(counted, statement="Dr. Markus Baer fuehrt 2 offene Akten: 2024-017 und 2024-021.",
                    quote="Anzahl der Treffer: 2 Treffer: 2024-017 Huber gegen Rueegg"):
    return lines({"statement": statement, "source": counted["count_passage"]["passage"], "quote": quote})


# --- the tool loop -----------------------------------------------------------------------------------------------


def test_the_loop_counts_on_the_graph_and_the_count_is_a_checked_statement():
    fake = FakeAnthropic(*find_then_count(count_statement))
    reply = service(fake).answer(QUESTION)
    assert isinstance(reply, GraphReply) and reply.opens_directly is False
    assert reply.status == "answered", reply
    (sentence,) = reply.sentences
    assert sentence.text.startswith("Dr. Markus Baer fuehrt 2 offene Akten")
    assert sentence.quote == "Anzahl der Treffer: 2\nTreffer: 2024-017 Huber gegen Rueegg"
    (passage,) = reply.passages                                   # only the cited one, renumbered
    assert passage.number == 1 and sentence.refs == (1,)
    assert passage.pointer == gr.QUERY_POINTER_PREFIX[:-len("abfrage:")] + "ma-1"   # brain:<the person's node>
    assert "Anzahl der Treffer: 2" in passage.text and "2023-090" not in passage.text
    assert len(fake.requests) == 3


def test_each_request_offers_the_tools_and_sends_the_rounds_back_as_they_came():
    fake = FakeAnthropic(*find_then_count(count_statement))
    service(fake).answer(QUESTION)
    first, second, third = fake.requests
    assert [t["name"] for t in first["tools"]] == list(brain_graph.TOOL_NAMES)
    assert first["tool_choice"] == {"type": "auto"} and "mcp_servers" not in first
    assert first["betas"] == list(providers.ANTHROPIC_BETAS) and first["fallbacks"] == "default"
    assert first["output_config"] == {"effort": "medium"} and first["max_tokens"] >= 8_000
    assert first["system"] == brain_graph.GRAPH_SYSTEM_INSTRUCTION
    assert first["messages"][-1]["content"].startswith("Question: " + QUESTION)
    # round 2: the assistant's content as it came, then the tool result answering its tool_use id
    assistant, results = second["messages"][-2:]
    assert assistant["role"] == "assistant" and assistant["content"][0]["type"] == "tool_use"
    (block,) = results["content"]
    assert block["type"] == "tool_result" and block["tool_use_id"] == assistant["content"][0]["id"]
    assert "is_error" not in block and block["cache_control"] == {"type": "ephemeral"}
    assert len(third["messages"]) == len(first["messages"]) + 4


def test_parallel_tool_calls_are_answered_in_one_message():
    def answer(request):
        assert len(request["messages"][-1]["content"]) == 2
        return message(text(lines({"no_answer": True})))

    fake = FakeAnthropic(calls((brain_graph.TOOL_DESCRIBE, {}), (brain_graph.TOOL_FIND, {"text": "Huber"})), answer)
    reply = service(fake).answer("Was gibt es zu Huber?")
    assert reply.status == "no_answer" and reply.message == NO_ANSWER_TEXT
    assert [p.number for p in reply.passages] == list(range(1, len(reply.passages) + 1))


def test_a_quote_the_tools_did_not_return_is_removed():
    fake = FakeAnthropic(*find_then_count(lambda c: count_statement(c, quote="Baer leitet zwei offene Mandate")))
    reply = service(fake).answer(QUESTION)
    assert reply.status == "no_answer" and reply.removed_by[check.QUOTE_NOT_FOUND] == 1
    assert reply.passages and all(p.pointer.startswith("brain:") for p in reply.passages)


def test_a_number_not_in_the_cited_passage_is_removed():
    fake = FakeAnthropic(*find_then_count(lambda c: count_statement(
        c, statement="Dr. Markus Baer fuehrt 3 offene Akten.", quote="Anzahl der Treffer: 2")))
    reply = service(fake).answer(QUESTION)
    assert reply.status == "no_answer" and reply.removed_by[check.NUMBER_NOT_FOUND] == 1


def test_a_greeting_needs_no_tool_and_no_passage():
    fake = FakeAnthropic(message(text(lines({"reply": "Guten Tag, wie kann ich helfen?", "kind": "chat"}))))
    reply = service(fake).answer("Hallo")
    assert reply.status == "no_answer" and reply.reply_kind == "chat" and reply.passages == []


def test_no_answer_without_tools():
    reply = service(FakeAnthropic(message(text(lines({"no_answer": True}))))).answer(QUESTION)
    assert reply.status == "no_answer" and reply.message == NO_ANSWER_TEXT and reply.passages == []


# --- the passages and the tools ----------------------------------------------------------------------------------


def test_a_node_passage_is_its_type_name_and_current_facts_with_the_key_first():
    session = session_for(FakeCore())
    result = session.read_node("akte-1")
    node = result["node"]
    (passage,) = session.book.passages
    assert passage.pointer == "brain:akte-1" and passage.title == "Akte: 2024-017 Huber gegen Rueegg"
    assert node["passage"] == 1 and node["text"] == passage.text
    body = passage.text.splitlines()
    assert body[0] == "Akte: 2024-017 Huber gegen Rueegg" and body[1] == "key: 2024-017"
    assert "status: offen" in body and "ampel: rot" in body
    assert "sistiert" not in passage.text and "CHF 999" not in passage.text      # closed and rejected facts
    assert "Verbindungen:" in body
    assert "mandant -> Partei (1): Huber Bau GmbH" in body
    assert "dokument_von <- Dokument (2): Klage; Ergebnis Konfliktpruefung" in body
    assert {n["node_id"] for n in result["neighbours"]} >= {"p-1", "p-2", "dok-1", "ma-1"}


def test_bookkeeping_facts_are_left_out():
    session = session_for(FakeCore())
    session.read_node("ma-1")
    assert "mapping" not in session.book.passages[0].text and "rolle: Partner" in session.book.passages[0].text


def test_find_nodes_ignores_case_accents_and_umlaut_spellings_and_finds_a_key_among_facts():
    session = session_for(FakeCore())
    assert session.find_nodes("r\u00fcegg haustechnik")["nodes"][0]["node_id"] == "p-1"
    assert session.find_nodes("Partner MB")["total_matches"] == 1                 # in the description
    by_key = session.find_nodes("ADR-9", type="Partei")
    assert by_key["matched_by"] == "fact" and by_key["nodes"][0]["node_id"] == "p-3"
    with pytest.raises(gr.ToolInputError, match="Typen: Akte"):
        session.find_nodes("x", type="Gibtsnicht")


def test_list_nodes_finds_what_a_rule_asks_for_and_is_missing():
    session = session_for(FakeCore())
    result = session.list_nodes("Akte", without_relation=[{"relation": "konfliktpruefung_zu", "direction": "in"}],
                                where=[{"fact": "status", "op": "not_equals", "value": "abgeschlossen"}])
    assert result["count"] == 2
    assert [n["node_id"] for n in result["nodes"]] == ["akte-2", "akte-4"]
    text_ = result["count_passage"]["text"]
    assert "ohne konfliktpruefung_zu (eingehend)" in text_ and "Anzahl der Treffer: 2" in text_


def test_list_nodes_joins_along_a_path():
    session = session_for(FakeCore())
    result = session.list_nodes("Rechnung", where=[{"fact": "zahlstatus", "op": "equals", "value": "gemahnt"}],
                                via=[{"relation": "rechnung_von", "direction": "out"},
                                     {"relation": "mandant", "direction": "out", "type": "Partei"}])
    assert result["count"] == 1 and result["start_count"] == 1
    assert [n["node_id"] for n in result["nodes"]] == ["p-3"]
    assert "Treffer: Keller Erben" in result["count_passage"]["text"]


def test_a_certain_no_is_a_count_of_zero_for_exactly_the_question():
    session = session_for(FakeCore())
    result = session.neighbours("akte-1", type="Dokument", contains="Berufung")
    assert result["count"] == 0 and result["nodes"] == []
    assert result["count_passage"]["text"].startswith(
        "Abfrage im Wissensgraphen: verbunden mit Akte \u00ab2024-017 Huber gegen Rueegg\u00bb, Typ Dokument, "
        "enth\u00e4lt \u00abBerufung\u00bb\nAnzahl der Treffer: 0")


def test_describe_graph_gives_the_vocabulary_and_a_types_facts_without_bookkeeping():
    session = session_for(FakeCore())
    result = session.describe_graph("Akte")
    assert {"type": "Akte", "count": 4, "description": "Beschreibung Akte"} in result["types"]
    assert {"relation": "verantwortlich", "from": "Akte", "to": "Mitarbeiter", "count": 4} in result["relations"]
    assert [f["fact"] for f in result["facts_of_type"]["facts"]] == ["status"]
    assert session.book.passages == [] and session.book.seen


def test_unknown_names_are_refused_with_the_known_ones():
    session = session_for(FakeCore())
    with pytest.raises(gr.ToolInputError, match="Beziehungen: dokument_von"):
        session.neighbours("akte-1", relation="gibtsnicht")
    with pytest.raises(gr.ToolInputError):
        session.read_node("nicht-da")


def test_the_topology_is_read_once_and_facts_once_per_node():
    core = FakeCore()
    session = session_for(core)
    session.read_node("akte-1")
    session.read_node("akte-1")
    session.list_nodes("Akte", where=[{"fact": "status", "op": "equals", "value": "offen"}])
    assert core.paths == ["/node-types", "/nodes", "/edges"]
    assert sorted(i for batch in core.batches for i in batch) == ["akte-1", "akte-2", "akte-3", "akte-4"]
    assert core.bounded == len(core.paths) + len(core.batches)      # every read inside within_time


def test_without_the_batch_route_facts_are_read_node_by_node():
    core = FakeCore(batch=False)
    session = session_for(core)
    assert session.list_nodes("Akte", where=[{"fact": "status", "op": "equals", "value": "offen"}])["count"] == 3
    assert "/nodes/akte-1/facts" in core.paths


# --- the bounds --------------------------------------------------------------------------------------------------


def test_the_fact_budget_refuses_a_filter_over_too_many_nodes():
    session = session_for(FakeCore(), max_fact_nodes=3)
    with pytest.raises(gr.GraphTooLarge, match="4 Knoten"):
        session.list_nodes("Akte", where=[{"fact": "status", "op": "equals", "value": "offen"}])


def test_a_result_holds_at_most_limit_node_passages_but_counts_all():
    session = session_for(FakeCore())
    result = session.list_nodes("Akte", limit=1)
    assert result["count"] == 4 and len(result["nodes"]) == 1 and "3 weitere" in result["note"]
    assert "Treffer: 2023-090 Moser Mietrecht; 2024-017" in result["count_passage"]["text"]


def test_after_the_last_round_the_request_offers_no_tool():
    fake = FakeAnthropic(calls((brain_graph.TOOL_DESCRIBE, {})), calls((brain_graph.TOOL_DESCRIBE, {})),
                         message(text(lines({"no_answer": True}))))
    service(fake, max_rounds=2).answer(QUESTION)
    assert [r["tool_choice"] for r in fake.requests] == [{"type": "auto"}, {"type": "auto"}, {"type": "none"}]


def test_tool_calls_beyond_the_limit_are_refused(monkeypatch):
    monkeypatch.setattr(brain_graph, "MAX_TOOL_CALLS", 1)

    def answer(request):
        first, second = results_of(request)
        assert "is_error" not in first[0] and second[0]["is_error"] is True
        assert "Werkzeugaufrufe" in second[1]["error"]
        return message(text(lines({"no_answer": True})))

    fake = FakeAnthropic(calls((brain_graph.TOOL_DESCRIBE, {}), (brain_graph.TOOL_DESCRIBE, {})), answer)
    assert service(fake).answer(QUESTION).status == "no_answer"


def test_a_bad_tool_input_is_an_error_result_not_a_failed_answer():
    def answer(request):
        ((block, body),) = results_of(request)
        assert block["is_error"] is True and "Typen:" in body["error"]
        return message(text(lines({"no_answer": True})))

    fake = FakeAnthropic(calls((brain_graph.TOOL_LIST, {"type": "Gibtsnicht"})), answer)
    assert service(fake).answer(QUESTION).status == "no_answer"


def test_with_too_little_time_left_reads_stop_and_the_next_request_must_answer():
    def answer(request):
        ((block, body),) = results_of(request)
        assert block["is_error"] is True and "Zeit" in body["error"]
        return message(text(lines({"no_answer": True})))

    core = FakeCore()
    fake = FakeAnthropic(calls((brain_graph.TOOL_DESCRIBE, {})), answer)
    reply = service(fake, core, time_left_reserve=1_000.0).answer(QUESTION)
    assert reply.status == "no_answer" and core.paths == []
    assert fake.requests[-1]["tool_choice"] == {"type": "none"}


def test_no_request_starts_without_the_time_for_it():
    fake = FakeAnthropic(message(text("x")))
    turn = providers.make_brain_graph_turn(api_key=KEY, model=MODEL, client_factory=fake)
    with pytest.raises(CoreBusy):
        turn(system_instruction="s", messages=[{"role": "user", "content": "q"}], tools=[], tool_choice={"type": "auto"},
             max_tokens=10, deadline_at=10.0, min_seconds=30.0)
    assert fake.requests == []


# --- 429 and failures --------------------------------------------------------------------------------------------


def test_a_429_is_waited_out_and_the_read_goes_on():
    sleeps = []
    core = FakeCore(statuses=[429, 429])
    session = session_for(core, sleeps=sleeps)
    assert session.describe_graph()["types"]
    assert sleeps == list(gr.BACKOFF_SECONDS[:2]) and core.paths[:3] == ["/node-types"] * 3
    assert session.port.busy == 2


def test_a_429_that_does_not_end_is_busy_and_so_is_the_answer():
    core = FakeCore(statuses=[429] * 10)

    def answer(request):
        ((block, body),) = results_of(request)
        assert block["is_error"] is True and "ausgelastet" in body["error"]
        return message(text(lines({"no_answer": True})))

    fake = FakeAnthropic(calls((brain_graph.TOOL_DESCRIBE, {})), answer)
    reply = service(fake, core, sleeps=[]).answer(QUESTION)
    assert reply.status == "busy" and reply.message == BUSY_TEXT


def test_a_wait_that_would_eat_the_reserve_is_not_made():
    sleeps = []
    session = session_for(FakeCore(statuses=[429]), time_left=lambda: 10.5, reserve=10.0, sleeps=sleeps)
    with pytest.raises(gr.GraphBusy):
        session.topology()
    assert sleeps == []


def test_a_graph_switched_off_is_one_plain_error():
    class Off(FakeCore):
        def graph_read(self, path):
            return 404, {"error_code": "knowledge_graph_disabled"}

    with pytest.raises(gr.GraphUnavailable, match="nicht eingeschaltet"):
        session_for(Off()).topology()


def test_claude_busy_and_failed_are_the_plain_messages():
    busy = anthropic.RateLimitError("rate limited", response=httpx2.Response(
        429, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")), body=None)
    assert service(FakeAnthropic(busy)).answer(QUESTION).status == "busy"
    refused = message(text(""), stop="refusal")
    reply = service(FakeAnthropic(refused)).answer(QUESTION)
    assert reply.status == "failed" and reply.message == FAILED_TEXT


def test_without_a_key_nothing_is_sent():
    fake = FakeAnthropic(message(text("x")))
    turn = providers.make_brain_graph_turn(api_key="", model=MODEL, client_factory=fake)
    with pytest.raises(CoreFailed):
        turn(system_instruction="s", messages=[], tools=[], tool_choice={"type": "auto"}, max_tokens=10,
             deadline_at=1e12, min_seconds=0)
    assert fake.requests == []


# --- the search tool ---------------------------------------------------------------------------------------------


def test_the_search_tool_numbers_the_persons_hits_beside_the_nodes():
    asked = []

    def search(query, limit):
        asked.append((query, limit))
        return [{"doc_id": "Akten/2024-017/Klage.pdf", "title": "Klage.pdf", "page_number": 2,
                 "snippet": "Die Klage wurde am 3. Mai 2024 beim Bezirksgericht eingereicht."}]

    def answer(request):
        ((_block, body),) = results_of(request)
        return message(text(lines({"statement": "Die Klage wurde am 3. Mai 2024 eingereicht.",
                                   "source": body["passages"][0]["passage"],
                                   "quote": "Die Klage wurde am 3. Mai 2024 beim Bezirksgericht eingereicht"})))

    fake = FakeAnthropic(calls((brain_graph.TOOL_SEARCH, {"query": "Klage eingereicht"})), answer)
    reply = service(fake, search=search).answer("Wann wurde die Klage eingereicht?")
    assert reply.status == "answered" and asked == [("Klage eingereicht", brain_graph.SEARCH_LIMIT)]
    assert reply.passages[0].pointer == "Akten/2024-017/Klage.pdf" and reply.passages[0].page == 2


def test_the_search_tool_is_off_without_a_search():
    def answer(request):
        ((block, _body),) = results_of(request)
        assert block["is_error"] is True
        return message(text(lines({"no_answer": True})))

    fake = FakeAnthropic(calls((brain_graph.TOOL_SEARCH, {"query": "x"})), answer)
    assert service(fake).answer(QUESTION).status == "no_answer"


# --- the real SDK on the wire ------------------------------------------------------------------------------------


def test_through_the_real_sdk_the_assistants_content_goes_back_unchanged():
    seen = []
    wire = [
        {"id": "msg_w1", "type": "message", "role": "assistant", "model": MODEL, "stop_reason": "tool_use",
         "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5},
         "content": [{"type": "thinking", "thinking": "", "signature": "sig-not-real"},
                     {"type": "tool_use", "id": "toolu_w1", "name": "find_nodes",
                      "input": {"text": "Markus Baer"}}]},
        {"id": "msg_w2", "type": "message", "role": "assistant", "model": MODEL, "stop_reason": "end_turn",
         "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5},
         "content": [{"type": "text", "text": '{"no_answer": true}', "citations": None}]},
    ]

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=wire[len(seen) - 1])

    def factory(**kwargs):
        return REAL_SDK_CLIENT(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(handle)))

    turn = providers.make_brain_graph_turn(api_key=KEY, model=MODEL, client_factory=factory)
    core = FakeCore()
    reply = BrainGraphService(turn=turn, open_graph=lambda left: session_for(core, time_left=left)).answer(QUESTION)
    assert reply.status == "no_answer"
    first, second = seen
    assert "mcp_servers" not in first and first["tool_choice"] == {"type": "auto"}
    assert second["messages"][-2] == {"role": "assistant", "content": wire[0]["content"]}
    (result,) = second["messages"][-1]["content"]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_w1"


# --- the logs ----------------------------------------------------------------------------------------------------


def test_no_question_name_or_fact_value_in_any_log_line(caplog):
    caplog.set_level(logging.DEBUG)
    for name in ("anthropic", "anthropic._base_client", "httpx", "httpx2", "httpcore"):
        caplog.set_level(logging.DEBUG, logger=name)              # restored after the test
    fake = FakeAnthropic(*find_then_count(count_statement))
    reply = service(fake).answer(QUESTION)
    assert reply.status == "answered"
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "brain graph status=answered" in logged and "tool_calls=2" in logged
    for secret in ("Markus", "Baer", "2024-017", "Huber", "offen", "verantwortlich", "Anzahl", KEY):
        assert secret not in logged, secret


# --- the factory, the settings and the page ------------------------------------------------------------------------


class _Settings:
    def __init__(self, values, secrets=None):
        self.values, self.secrets = values, secrets or {}

    def value(self, key):
        return self.values.get(key)

    def secret(self, key):
        return self.secrets.get(key)


class _Client:
    def secured_mode(self):
        return True


def _make(values, secrets=None, client=None):
    from assistant.core import make_service

    return make_service(_Settings({"assistant.enabled": "an", **values}, secrets), client or _Client(),
                        split=lambda answer: (answer, []), anthropic_client=FakeAnthropic(message(text("x"))))


def test_the_factory_builds_the_graph_mode_only_with_anthropic():
    built = _make({"assistant.mode": "brain_graph", "assistant.provider": "anthropic"},
                  {"assistant.anthropic_api_key": KEY})
    assert isinstance(built, BrainGraphService)
    refused = _make({"assistant.mode": "brain_graph", "assistant.provider": "knovas"})
    assert isinstance(refused, BrainGraphService) and refused.answer(QUESTION).status == "failed"


def test_the_settings_offer_the_mode_warn_and_refuse_it_without_anthropic_or_key():
    import platform_settings as ps

    mode = ps.REGISTRY["assistant.mode"]
    assert mode.choices == ("passages", "brain_mcp", "brain_graph")
    warning = providers.settings_warnings()["assistant.mode"]
    assert providers.BRAIN_MCP_WARNING in warning and providers.BRAIN_GRAPH_WARNING in warning
    settings = _Settings({"assistant.provider": "knovas"})
    assert providers.brain_mcp_refusal(settings, {"assistant.mode": "brain_graph"}) == \
        providers.BRAIN_GRAPH_NEEDS_ANTHROPIC
    assert providers.brain_mcp_refusal(settings, {"assistant.mode": "brain_graph", "assistant.provider": "anthropic"}) \
        == providers.BRAIN_GRAPH_NEEDS_API_KEY
    assert providers.brain_mcp_refusal(settings, {"assistant.mode": "brain_graph", "assistant.provider": "anthropic",
                                                  "assistant.anthropic_api_key": KEY}) is None
    assert providers.settings_problems(_Settings({"assistant.mode": "brain_graph", "assistant.provider": "knovas"})) \
        == {"assistant.mode": providers.BRAIN_GRAPH_NEEDS_ANTHROPIC}


def _page_app(views_on):
    app = Flask(__name__)
    bp = Blueprint("brain_views", __name__)
    bp.add_url_rule("/dossier/<node_id>", "dossier", lambda node_id: node_id)
    app.register_blueprint(bp)
    app.add_url_rule("/download/<path:doc_id>", "download_document", lambda doc_id: doc_id)
    app.extensions["platform_settings"] = _Settings({"brain_views.enabled": "an" if views_on else "aus"})
    return app


def test_a_node_passage_opens_its_dossier_while_the_brain_views_are_on():
    from assistant.service import Passage, Sentence
    from web_interface.assistant_routes import shown_reply

    reply = GraphReply(status="answered", sentences=[Sentence(text="Sie ist offen.", refs=(1,), quote="status: offen")],
                       passages=[Passage(number=1, pointer="brain:akte-1", title="Akte: 2024-017", page=None, text=""),
                                 Passage(number=2, pointer="brain:abfrage:t-akte", title="Abfrage", page=None,
                                         text=""),
                                 Passage(number=3, pointer="Akten/x.docx", title="x.docx", page=None, text="")])
    for views_on, first in ((True, "/dossier/akte-1"), (False, "")):
        with _page_app(views_on).test_request_context("/"):
            shown = shown_reply(reply, False)
            assert [p.href for p in shown["passages"]] == [first, "", ""]


def test_a_name_that_starts_with_the_words_ranks_before_a_match_inside_a_word():
    class WithLombardi(FakeCore):
        def graph_read(self, path):
            status, payload = super().graph_read(path)
            if path == "/nodes":
                payload["nodes"].insert(0, {"id": "ma-3", "name": "Carla Lombardi", "node_type_id": T_MA})
            return status, payload

    found = session_for(WithLombardi()).find_nodes("B\u00e4r", "Mitarbeiter")
    assert [n["node_id"] for n in found["nodes"]] == ["ma-1", "ma-3"]
