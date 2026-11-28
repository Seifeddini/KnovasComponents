"""<<Ohne Beleg>>: the statements the check removed, shown apart and marked when an administrator switches it on
(the founder, 2026-11-28: "why are <<Aussagen ohne Beleg>> not shown? I want to be able to show them").

The check keeps what it removed (``check.check_answer_keeping``, ``removed_statements``) without changing what it
shows or counts; the three modes carry it on the reply (``Reply.unchecked``, capped); the chat stores it only while
``assistant.show_unchecked`` is on and the page shows it only while it is on; it never becomes a chip, a grant, a
node of the graph or part of the history. No Core and no model is reached. This file is ASCII-only.
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
from types import SimpleNamespace

import pytest

from assistant import check
from assistant.service import (MAX_UNCHECKED, MAX_UNCHECKED_CHARS, AssistantService, Passage, Reply, Sentence,
                               Unchecked, clean_unchecked)
from web_interface import assistant_routes
from web_interface.assistant_routes import (UNCHECKED_OTHER, UNCHECKED_REASON_TEXT, history_of, shown_reply,
                                            stored_answer, stored_reply)

SHOW_ENV = "ASSISTANT_SHOW_UNCHECKED"

P1 = "Die Kuendigungsfrist betraegt drei Monate auf Ende Juni."
P2 = "Der Mietzins betraegt CHF 4'200 pro Monat."


def st(statement, source, quote="Kuendigungsfrist betraegt drei Monate"):
    return json.dumps({"statement": statement, "source": source, "quote": quote}, ensure_ascii=False)


GOOD = st("Die Kuendigungsfrist betraegt drei Monate.", 1)
BAD_QUOTE = st("Die Frist laeuft bis Ende Juli.", 1, quote="Frist laeuft bis Ende Juli")
BAD_NUMBER = st("Die Kuendigungsfrist betraegt vier Monate.", 1)
BAD_SOURCE = st("Der Vertrag ist befristet.", 9, quote="Vertrag ist befristet")
NO_QUOTE = json.dumps({"statement": "Der Mieter zahlt die Nebenkosten.", "source": 2})
TOO_LONG = st("Die Kuendigungsfrist betraegt drei Monate " + "und so weiter " * 60 + ".", 1)
PROSE = "Hier ist meine Antwort in Prosa."
NOTHING_FOUND = st("Dazu finde ich nichts in Ihren Quellen.", 1, quote="Frist betraegt drei")
MIXED = "\n".join([GOOD, BAD_QUOTE, BAD_NUMBER, BAD_SOURCE, NO_QUOTE, TOO_LONG, PROSE, NOTHING_FOUND])
PASSAGES_BY_NUMBER = {1: P1, 2: P2}


# --- the check ---------------------------------------------------------------------------------------------------


def test_the_check_keeps_what_it_removed_and_shows_and_counts_exactly_as_before():
    shown, counts = check.check_answer(MIXED, PASSAGES_BY_NUMBER)
    kept_shown, kept_counts, removed = check.check_answer_keeping(MIXED, PASSAGES_BY_NUMBER)
    assert (kept_shown, kept_counts) == (shown, counts)
    assert [s.text for s in shown] == ["Die Kuendigungsfrist betraegt drei Monate."]
    assert counts[check.NOT_A_STATEMENT] == 3 and counts[check.UNKNOWN_SOURCE] == 1     # no quote, prose, nothing
    assert [(r.reason, r.source) for r in removed] == [
        (check.QUOTE_NOT_FOUND, 1), (check.NUMBER_NOT_FOUND, 1), (check.UNKNOWN_SOURCE, None),
        (check.NOT_A_STATEMENT, 2), (check.TOO_LONG, 1)]
    assert removed[0].text == "Die Frist laeuft bis Ende Juli."
    assert check.removed_statements(MIXED, PASSAGES_BY_NUMBER) == removed


def test_prose_and_a_statement_that_only_says_nothing_was_found_are_counted_never_kept():
    removed = check.removed_statements("\n".join([PROSE, NOTHING_FOUND]), PASSAGES_BY_NUMBER)
    assert removed == []


def test_a_form_failure_names_its_source_only_when_it_is_a_passage_of_the_answer():
    line = json.dumps({"statement": "Der Mieter zahlt.", "source": "7"})
    (removed,) = check.removed_statements(line, PASSAGES_BY_NUMBER)
    assert (removed.reason, removed.source) == (check.NOT_A_STATEMENT, None)
    (removed,) = check.removed_statements(json.dumps({"statement": "Der Mieter zahlt.", "source": "2"}),
                                          PASSAGES_BY_NUMBER)
    assert removed.source == 2


# --- the service (passages) --------------------------------------------------------------------------------------


def _row(n, text):
    return {"doc_id": f"Akten/doc-{n}.pdf", "path": f"Akten/doc-{n}.pdf", "title": f"Dokument {n}",
            "page_number": n, "snippet": text}


def passages_service(answer, rows=None):
    rows = rows if rows is not None else [_row(1, P1), _row(2, P2)]
    return AssistantService(search=lambda q, limit: [dict(r) for r in rows],
                            generate=lambda **kw: answer)


def test_the_passages_reply_carries_the_removed_statements_and_is_otherwise_unchanged():
    reply = passages_service(MIXED).answer("Welche Frist?")
    assert reply.status == "answered" and len(reply.sentences) == 1
    assert reply.removed == 7
    assert [(u.reason, u.source) for u in reply.unchecked] == [
        ("quote_not_found", 1), ("number_not_found", 1), ("unknown_source", None), ("not_a_statement", 2),
        ("too_long", 1)]
    assert len(reply.unchecked[-1].text) <= MAX_UNCHECKED_CHARS and reply.unchecked[-1].text.endswith("\u2026")
    # equality and repr ignore it: the reply compares and prints as before
    assert reply == Reply(**{**reply.__dict__, "unchecked": ()})
    assert "Juli" not in repr(reply)


def test_nothing_removed_means_nothing_kept():
    reply = passages_service(GOOD).answer("Welche Frist?")
    assert reply.status == "answered" and reply.unchecked == ()


def test_at_most_ten_are_kept():
    answer = "\n".join(st(f"Die Frist laeuft bis Ende Juli {i}.", 1, quote="Frist laeuft bis Ende") for i in range(15))
    reply = passages_service(answer).answer("Welche Frist?")
    assert reply.removed == 15 and len(reply.unchecked) == MAX_UNCHECKED


def test_all_removed_stays_no_answer_and_keeps_them_with_the_nearest_passages_numbers():
    reply = passages_service("\n".join([BAD_QUOTE, BAD_SOURCE])).answer("Welche Frist?")
    assert reply.status == "no_answer" and reply.sentences == [] and reply.removed == 2
    assert [(u.reason, u.source) for u in reply.unchecked] == [("quote_not_found", 1), ("unknown_source", None)]


def test_the_log_line_names_no_removed_statement(caplog):
    caplog.set_level(logging.DEBUG)
    passages_service(MIXED).answer("Welche Frist?")
    logged = " ".join(r.getMessage() for r in caplog.records)
    for secret in ("Juli", "vier Monate", "befristet", "Nebenkosten", "und so weiter"):
        assert secret not in logged


# --- brain_mcp and brain_graph -------------------------------------------------------------------------------------


def test_brain_mcp_keeps_them_and_names_a_source_only_when_the_reply_lists_it():
    from test_assistant_brain_tools import PTR_LEASE, PTR_NOTE, S_FRIST, FakeAnthropic, _service, lines, tool_turn

    wrong_lease = {"statement": "Die Frist endet im Juli.", "source": PTR_LEASE, "quote": "Die Frist endet im Juli"}
    wrong_note = {"statement": "Die Besprechung war kurz.", "source": PTR_NOTE, "quote": "Die Besprechung war kurz"}
    reply = _service(FakeAnthropic(tool_turn(lines(S_FRIST, wrong_lease, wrong_note)))).answer("Welche Frist?")
    assert reply.status == "answered" and len(reply.sentences) == 1 and reply.removed == 2
    assert [p.pointer for p in reply.passages] == [PTR_LEASE]
    assert [(u.reason, u.source) for u in reply.unchecked] == [("quote_not_found", 1), ("quote_not_found", None)]


def test_brain_graph_keeps_them_and_they_never_enter_the_graph():
    from test_assistant_brain_graph import QUESTION, FakeAnthropic, find_then_count, lines, service

    def answer(counted):
        number = counted["count_passage"]["passage"]
        return lines({"statement": "Dr. Markus Baer fuehrt 2 offene Akten: 2024-017 und 2024-021.", "source": number,
                      "quote": "Anzahl der Treffer: 2 Treffer: 2024-017 Huber gegen Rueegg"},
                     {"statement": "Dr. Markus Baer fuehrt 5 offene Akten.", "source": number,
                      "quote": "Anzahl der Treffer: 2"},
                     {"statement": "Er ist Partner seit langem.", "source": number + 40, "quote": "ist Partner seit"})

    reply = service(FakeAnthropic(*find_then_count(answer))).answer(QUESTION)
    assert reply.status == "answered" and len(reply.sentences) == 1 and reply.removed == 2
    assert [(u.reason, u.source) for u in reply.unchecked] == [("number_not_found", 1), ("unknown_source", None)]
    assert reply.graph is not None
    assert [n.get("passage") for n in reply.graph["nodes"] if n.get("cited")] == [1]     # only the shown statement's


# --- stored and read back ----------------------------------------------------------------------------------------


PASSAGES = [Passage(number=1, pointer="Akten/a.pdf", title="a.pdf", page=1, text=P1),
            Passage(number=2, pointer="Akten/b.pdf", title="b.pdf", page=2, text=P2)]
SENTENCE = Sentence(text="Die Kuendigungsfrist betraegt drei Monate.", refs=(1,), quote="Kuendigungsfrist betraegt")
UNCHECKED = (Unchecked(text="Die Frist laeuft bis Ende\x00 Juli.", reason="quote_not_found", source=1),
             Unchecked(text="Der Vertrag ist befristet.", reason="unknown_source"),
             Unchecked(text="Der Mieter zahlt.", reason="not_a_statement", source=2))


def with_unchecked(reply, unchecked=UNCHECKED):
    from dataclasses import replace

    return replace(reply, unchecked=tuple(unchecked))


def answered_reply():
    return Reply(status="answered", sentences=[SENTENCE], passages=list(PASSAGES), removed=3)


def message_of(text, references):
    return SimpleNamespace(text=text, references=references)


def test_off_stores_exactly_what_it_stored_before():
    plain = answered_reply()
    assert stored_answer(with_unchecked(plain)) == stored_answer(plain)
    assert "unchecked" not in json.loads(stored_answer(with_unchecked(plain))[0])


def test_on_stores_them_cleaned_and_reads_them_back():
    text, references = stored_answer(with_unchecked(answered_reply()), keep_unchecked=True)
    data = json.loads(text)
    assert data["unchecked"] == [{"text": "Die Frist laeuft bis Ende Juli.", "reason": "quote_not_found", "source": 1},
                                 {"text": "Der Vertrag ist befristet.", "reason": "unknown_source", "source": None},
                                 {"text": "Der Mieter zahlt.", "reason": "not_a_statement", "source": 2}]
    assert "\x00" not in text
    assert [r["number"] for r in references] == [1, 2]                     # the references are the reply's only
    back = stored_reply(message_of(text, references))
    assert [(u.text, u.reason, u.source) for u in back.unchecked] == [(d["text"], d["reason"], d["source"])
                                                                      for d in data["unchecked"]]
    assert back.sentences == [SENTENCE]


def test_on_with_nothing_removed_stores_no_key():
    text, _ = stored_answer(answered_reply(), keep_unchecked=True)
    assert "unchecked" not in json.loads(text)


def test_the_stored_list_is_capped_and_cut():
    many = [Unchecked(text="x " * 400, reason="too_long", source=1)] * 25
    text, _ = stored_answer(with_unchecked(answered_reply(), many), keep_unchecked=True)
    stored = json.loads(text)["unchecked"]
    assert len(stored) == MAX_UNCHECKED and all(len(u["text"]) <= MAX_UNCHECKED_CHARS for u in stored)


def test_an_older_answer_has_none_and_one_not_in_the_shape_is_left_out():
    text, references = stored_answer(answered_reply())
    assert stored_reply(message_of(text, references)).unchecked == ()
    data = json.loads(text)
    for broken in ("x", [1, None], [{"text": 3, "reason": "too_long"}], [{"text": "a", "reason": "elsewhere"}],
                   [{"text": "a", "reason": "too_long", "source": True}]):
        data["unchecked"] = broken
        reply = stored_reply(message_of(json.dumps(data), references))
        assert reply.status == "answered" and reply.sentences == [SENTENCE]
        assert all(u.source is None for u in reply.unchecked) and len(reply.unchecked) <= 1
    assert clean_unchecked([{"text": "a", "reason": "elsewhere"}]) == ()


def test_the_history_never_holds_them():
    text, references = stored_answer(with_unchecked(answered_reply()), keep_unchecked=True)
    chat = SimpleNamespace(messages=[SimpleNamespace(role="user", text="Welche Frist?", references=[]),
                                     SimpleNamespace(role="assistant", text=text, references=references)])
    (question, answer) = history_of(chat)
    assert answer.text == SENTENCE.text and "Juli" not in answer.text


# --- what the page draws ------------------------------------------------------------------------------------------


def test_shown_reply_is_as_before_unless_asked_and_then_lists_plain_reasons():
    from flask import Flask

    app = Flask(__name__)
    app.add_url_rule("/download/<path:doc_id>", "download_document", lambda doc_id: doc_id)
    reply = with_unchecked(answered_reply(), UNCHECKED + (Unchecked(text="Zu viel.", reason="too_long", source=7),
                                                          Unchecked(text="Zahl.", reason="number_not_found")))
    with app.test_request_context("/"):
        assert "unchecked" not in shown_reply(reply, False)
        listed = shown_reply(reply, False, unchecked=True)["unchecked"]
    assert [(u.reason, u.source) for u in listed] == [
        (UNCHECKED_REASON_TEXT["quote_not_found"], 1), (UNCHECKED_REASON_TEXT["unknown_source"], None),
        (UNCHECKED_OTHER, 2), (UNCHECKED_REASON_TEXT["too_long"], None),                  # 7: not a listed passage
        (UNCHECKED_REASON_TEXT["number_not_found"], None)]
    assert UNCHECKED_REASON_TEXT == {"unknown_source": "Quelle nicht unter den gefundenen Textstellen",
                                     "quote_not_found": "Zitat steht nicht w\u00f6rtlich in der Quelle",
                                     "number_not_found": "Zahl steht nicht in der Quelle", "too_long": "zu lang"}
    assert UNCHECKED_OTHER == "nicht pr\u00fcfbar"


@pytest.fixture
def ask_service(workbench_app, monkeypatch):
    from test_assistant_chats_routes import FakeService

    monkeypatch.setenv(assistant_routes.SWITCH_ENV, "true")
    monkeypatch.delenv(SHOW_ENV, raising=False)
    fake = FakeService(with_unchecked(answered_reply()))
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    return fake


def _asked(client):
    from test_assistant_chats_routes import _ask, _chat_id

    return _chat_id(_ask(client))


def _main_of(client, chat_id):
    from test_assistant_chats_routes import _main, _page

    return _main(_page(client, f"/fragen/{chat_id}"))


def _stored_text(platform_db, alice, chat_id):
    from identity.chats import ChatStore

    chat = ChatStore(platform_db).get_chat(chat_id, owner=alice.id)
    return [m.text for m in chat.messages if m.role == "assistant"][-1]


def _block(main):
    return re.search(r'<details class="ask-unchecked">(.*?)</details>', main, flags=re.S).group(1)


def test_off_by_default_nothing_is_stored_or_shown(alice_client, platform_db, ask_service):
    main = _main_of(alice_client, _asked(alice_client))
    assert "ask-unchecked" not in main and "Ohne Beleg" not in main and "nicht belegt" not in main
    assert "Juli" not in main


def test_on_they_are_stored_and_shown_apart_and_marked(alice_client, platform_db, ask_service, monkeypatch):
    monkeypatch.setenv(SHOW_ENV, "an")
    main = _main_of(alice_client, _asked(alice_client))
    block = _block(main)
    assert "Ohne Beleg (3)" in main
    assert html_lib.unescape(block).count("nicht belegt") == 3
    text = " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", block)).split())
    assert "Die Frist laeuft bis Ende Juli." in text
    assert "Zitat steht nicht w\u00f6rtlich in der Quelle \u00b7 behauptete Quelle 1" in text
    assert "Quelle nicht unter den gefundenen Textstellen" in text and "nicht pr\u00fcfbar" in text
    assert "behauptete Quelle 2" in text
    # never a chip, a link or a quote
    assert "<a " not in block and "aria-controls" not in block and "ask-quote" not in block
    assert '<details class="ask-unchecked" open' not in main                         # closed by default
    # under the checked statements and the removed line, before the sources
    assert main.index("ask-statement") < main.index("ask-removed") < main.index("ask-unchecked") < main.index("Quellen")
    # the chips are the checked statements' only
    assert main.count('aria-controls="zitat-') == 1


def test_an_admin_turning_it_off_hides_stored_ones(alice_client, platform_db, ask_service, monkeypatch):
    monkeypatch.setenv(SHOW_ENV, "an")
    chat_id = _asked(alice_client)
    assert "ask-unchecked" in _main_of(alice_client, chat_id)
    monkeypatch.setenv(SHOW_ENV, "aus")
    assert "ask-unchecked" not in _main_of(alice_client, chat_id)


def test_with_on_an_answer_stored_while_off_shows_none(alice_client, platform_db, ask_service, monkeypatch):
    chat_id = _asked(alice_client)
    monkeypatch.setenv(SHOW_ENV, "an")
    assert "ask-unchecked" not in _main_of(alice_client, chat_id)


def test_all_removed_shows_the_no_answer_and_the_block(alice_client, platform_db, ask_service, monkeypatch):
    monkeypatch.setenv(SHOW_ENV, "an")
    ask_service.reply = with_unchecked(Reply(status="no_answer", passages=list(PASSAGES), removed=3,
                                             message="Dazu finde ich nichts in Ihren Quellen"))
    main = _main_of(alice_client, _asked(alice_client))
    assert "Dazu finde ich nichts in Ihren Quellen" in main and "ask-statement" not in main
    assert "Ohne Beleg (3)" in main
    assert main.index("ask-unchecked") < main.index("Die n\u00e4chsten Stellen")


def test_the_stored_json_holds_them_only_while_on(alice, alice_client, platform_db, ask_service, monkeypatch):
    assert "unchecked" not in json.loads(_stored_text(platform_db, alice, _asked(alice_client)))
    monkeypatch.setenv(SHOW_ENV, "an")
    stored = json.loads(_stored_text(platform_db, alice, _asked(alice_client)))
    assert [u["reason"] for u in stored["unchecked"]] == ["quote_not_found", "unknown_source", "not_a_statement"]


def test_the_setting_is_registered_off_by_default():
    from platform_settings import REGISTRY

    setting = REGISTRY["assistant.show_unchecked"]
    assert (setting.label, setting.kind, setting.scope, setting.choices, setting.fallback, setting.env) == (
        "Nicht belegte Aussagen anzeigen", "choice", "installation", ("aus", "an"), "aus", (SHOW_ENV,))
    assert "nur gez\u00e4hlt" in setting.help and "nicht belegt" in setting.help
