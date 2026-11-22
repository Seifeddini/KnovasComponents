"""PMVP-20 conversation: the Knovas Assistent may answer in its own words, and every claim is still a checked
statement (assistant.conversation, assistant.service, assistant.brain_tools).

Work order docs/platform-mvp/work-orders/PMVP-20-conversation.md ("Your tests"): a greeting gives a reply without
sources; "document" gives a clarifying reply and at most five nearest passages; a reply with a digit, or naming a
passage's matter, title or a name of its text, is dropped; a document question still needs quotes; replies and
statements mixed; clean titles; nothing of the text in any log line at DEBUG (FU-46, the loggers set explicitly). The
pages and the stored chat are in test_assistant_conversation_pages.py.

Fakes only: no Core, no model. This file is ASCII-only: umlauts are \\u escapes.
"""
from __future__ import annotations

import json
import logging

import pytest

from assistant import brain_tools, check, conversation
from assistant.brain_tools import BrainToolsService
from assistant.check import REASONS, REPLY_DROPPED
from assistant.conversation import (KIND_CHAT, KIND_CLARIFY, MAX_LEAD_IN_CHARS, MAX_REPLY_CHARS, NEAREST_PASSAGES,
                                    check_reply, clean_title, holds_a_number, is_common, names_a_passage,
                                    passage_names)
from assistant.service import NO_ANSWER_TEXT, SYSTEM_INSTRUCTION, AssistantService, Passage, Turn

UUID_1 = "0ef33273-3627-56a4-8353-e31584baf6a7"
UUID_2 = "df1b01fd-0a1b-4c2d-8e3f-1234567890ab"


def _row(n, text, title):
    return {"doc_id": f"kanzlei/Mandanten/Limmat/{title}.pdf", "path": f"kanzlei/Mandanten/Limmat/{title}.pdf",
            "title": title, "page_number": n, "score": 1.0 - n / 100, "snippet": text}


TEXTS = [
    "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate auf Ende eines Monats.",
    "Der Mieter Sabine Vogt zahlt die Miete monatlich im Voraus.",
    "Vertreten durch Reto Imhof, Rechtsanwalt, Z\u00fcrich.",
    "Die Beilage erg\u00e4nzt den Vertrag in Punkt vier.",
    "Die Parteien vereinbaren die Schriftform f\u00fcr \u00c4nderungen.",
    "Die Abmahnung wurde eingeschrieben zugestellt.",
    "Der Nachtrag \u00e4ndert die Mindestdauer.",
]
TITLES = [
    f"{UUID_1}_2024-055_Beilage-Vertrag",
    f"{UUID_2}_2019-029_Beilage-Vertrag",
    "Mietvertrag_Seefeldstrasse_48",
    "2023-041_Abmahnung",
    "Nachtrag_2_2023",
    f"{UUID_1}_2024-055_Mietvertrag",
    "Protokoll Hardturm",
]
ROWS = [_row(n, text, title) for n, (text, title) in enumerate(zip(TEXTS, TITLES), start=1)]

GREETING = {"reply": "Hallo! Wie kann ich Ihnen helfen?", "kind": "chat"}
CLARIFY = {"reply": "Gern helfe ich Ihnen. Um welches Mandat geht es, und welche Art von Dokument suchen Sie?",
           "kind": "clarify"}
FRIST = {"statement": "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate.", "source": 1,
         "quote": "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate"}
FORM = {"statement": "F\u00fcr \u00c4nderungen gilt die Schriftform.", "source": 5,
        "quote": "vereinbaren die Schriftform f\u00fcr \u00c4nderungen"}


def lines(*values):
    return "\n".join(json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v for v in values)


class FakeSearch:
    def __init__(self, rows=None):
        self.rows = list(ROWS if rows is None else rows)
        self.calls = []

    def __call__(self, question, limit):
        self.calls.append((question, limit))
        return [dict(r) for r in self.rows]


class FakeGenerate:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, *, system_instruction, messages, max_tokens):
        self.calls.append({"system": system_instruction, "messages": messages})
        return self.answer


def _answer(*values, question="Welche Frist gilt?", rows=None, history=()):
    generate = FakeGenerate(lines(*values))
    return AssistantService(search=FakeSearch(rows), generate=generate).answer(question, list(history))


def _counts(**counts):
    return {**dict.fromkeys(REASONS, 0), **counts}


# --- a greeting, a meta question, a vague request ---------------------------------------------------------------


def test_a_greeting_gets_a_reply_without_sources():
    reply = _answer(GREETING, question="wagwan")
    assert reply.status == "no_answer"                          # no claim about the documents
    assert reply.reply_text == reply.message == "Hallo! Wie kann ich Ihnen helfen?"
    assert reply.reply_kind == KIND_CHAT
    assert reply.message != NO_ANSWER_TEXT
    assert reply.sentences == [] and reply.passages == []       # no source and no list under small talk
    assert reply.removed == 0 and reply.removed_by == _counts()


def test_what_can_you_do_gets_a_reply():
    said = {"reply": "Ich beantworte Fragen zu den Dokumenten Ihrer Kanzlei und belege jede Aussage mit einem "
                     "w\u00f6rtlichen Zitat. Fragen Sie zum Beispiel nach einer Frist oder einer Partei.", "kind": "chat"}
    reply = _answer(said, question="Was kannst du?")
    assert reply.reply_text.startswith("Ich beantworte Fragen") and reply.passages == []


def test_document_gets_a_clarifying_reply_and_at_most_five_nearest_passages():
    reply = _answer(CLARIFY, question="document")
    assert reply.status == "no_answer" and reply.reply_kind == KIND_CLARIFY
    assert reply.message == CLARIFY["reply"] and reply.sentences == []
    assert NEAREST_PASSAGES == 5
    assert [p.number for p in reply.passages] == [1, 2, 3, 4, 5]                # search order, the first five
    assert [p.title for p in reply.passages] == TITLES[:5]                       # raw; the page cleans them


def test_a_reply_without_a_kind_is_small_talk():
    reply = _answer({"reply": "Gern geschehen!"}, question="Danke")
    assert reply.reply_kind == KIND_CHAT and reply.passages == []


@pytest.mark.parametrize("kind", ["CLARIFY", "", None, 1, ["clarify"], "frage"])
def test_any_other_kind_is_small_talk(kind):
    assert _answer({"reply": "Gern geschehen!", "kind": kind}).reply_kind == KIND_CHAT


def test_the_no_answer_sentence_comes_only_without_statements_and_without_a_reply():
    for values in [({"no_answer": True},), ("",), ("Hallo, wie geht's?",)]:
        reply = _answer(*values)
        assert reply.status == "no_answer" and reply.message == NO_ANSWER_TEXT and reply.reply_text == ""
        assert [p.number for p in reply.passages] == [1, 2, 3, 4, 5]
    assert _answer(GREETING).message != NO_ANSWER_TEXT


def _nothing_found(*values, question="wagwan", history=()):
    generate = FakeGenerate(lines(*values))
    reply = AssistantService(search=FakeSearch([]), generate=generate).answer(question, list(history))
    return reply, generate


def test_nothing_found_the_model_is_asked_once_with_no_passage():
    """The coordinator's re-pin of 2026-11-22: with no passages the model may be asked once."""
    reply, generate = _nothing_found(GREETING)
    assert len(generate.calls) == 1
    last = generate.calls[0]["messages"][-1]["content"]
    assert last.startswith("Numbered passages: none.") and "[1]" not in last and "Question: wagwan" in last
    assert reply.status == "no_answer" and reply.reply_text == reply.message == GREETING["reply"]
    assert reply.reply_kind == KIND_CHAT and reply.passages == [] and reply.sentences == []


def test_nothing_found_a_clarifying_reply_lists_no_passage():
    reply, _ = _nothing_found(CLARIFY, question="document")
    assert reply.reply_text == CLARIFY["reply"] and reply.reply_kind == KIND_CLARIFY and reply.passages == []


def test_nothing_found_every_statement_is_an_unknown_source():
    reply, _ = _nothing_found(FRIST, FORM, {"statement": "Die Frist ist kurz.", "source": "1", "quote": "a b c"})
    assert reply.status == "no_answer" and reply.message == NO_ANSWER_TEXT and reply.sentences == []
    assert reply.passages == [] and reply.removed_by == _counts(unknown_source=3)


def test_nothing_found_without_a_valid_reply_the_no_answer_sentence_and_no_passage():
    for values, counts in [((), _counts()), (({"no_answer": True},), _counts()),
                           (({"reply": "Ich sehe 3 Dokumente.", "kind": "chat"},), _counts(reply_dropped=1)),
                           (({"reply": "Dazu habe ich das gefunden:"}, FRIST), _counts(unknown_source=1,
                                                                                         reply_dropped=1))]:
        reply, generate = _nothing_found(*values)
        assert len(generate.calls) == 1
        assert reply.status == "no_answer" and reply.message == NO_ANSWER_TEXT and reply.reply_text == ""
        assert reply.passages == [] and reply.removed_by == counts, values


def test_nothing_found_the_chat_so_far_still_goes_along():
    reply, generate = _nothing_found(GREETING, question="Danke!",
                                     history=[Turn("user", "document"), Turn("assistant", CLARIFY["reply"])])
    assert [m["role"] for m in generate.calls[0]["messages"]] == ["user", "assistant", "user"]
    assert reply.reply_text == GREETING["reply"]


def test_an_empty_question_is_still_neither_searched_nor_generated():
    search, generate = FakeSearch(), FakeGenerate(lines(GREETING))
    reply = AssistantService(search=search, generate=generate).answer("   ")
    assert search.calls == [] and generate.calls == [] and reply.message == NO_ANSWER_TEXT


# --- the rules: a reply that breaks one is dropped ----------------------------------------------------------------


NUMBERS = [
    "Ich habe 3 passende Dokumente gefunden.",
    "Ich habe drei passende Dokumente gefunden.",
    "Die Frist l\u00e4uft Ende M\u00e4rz ab.",
    "Meinen Sie das Mandat 2024-055?",
    "Siehe Quelle [1].",
    "Es gibt \u00b2 Treffer.",
    "Das ist Teil \u2163.",
    "Ich finde einen Monat Frist.",
    "Es geht um zw\u00f6lf Wochen.",
    "Das steht in Art. 266a.",
]


@pytest.mark.parametrize("text", NUMBERS)
def test_a_reply_with_a_number_is_dropped(text):
    assert holds_a_number(text)
    reply = _answer({"reply": text, "kind": "chat"})
    assert reply.reply_text == "" and reply.message == NO_ANSWER_TEXT
    assert reply.removed_by == _counts(reply_dropped=1) and reply.removed == 1
    assert [p.number for p in reply.passages] == [1, 2, 3, 4, 5]                # the old no-answer path


@pytest.mark.parametrize("text", [
    "Hallo! Wie kann ich Ihnen helfen?",
    "Gern helfe ich Ihnen. Um welches Mandat geht es?",
    "Schreiben Sie mir, ob es um eine E-Mail oder einen Brief geht.",
    "Ich habe eine Frage an Sie: Welche Art von Dokument meinen Sie?",
])
def test_words_that_are_no_numbers_pass(text):
    assert not holds_a_number(text)


NAMING = [
    "Meinen Sie den Vertrag von Frau Vogt?",                     # a person of a passage's text
    "Geht es um die Unterlagen von Reto Imhof?",                 # first name and family name
    "Geht es um Imhofs Vertretung?",                             # a genitive s
    "Geht es um den Mietvertrag Seefeldstrasse?",                # a word of a title
    "Geht es um das Mandat Limmat?",                             # a folder of a pointer: a client
    "Geht es um das Protokoll Hardturm?",                        # a title with a place
    "Suchen Sie etwas zu VOGT?",                                 # case does not matter
    "Geht es um Zuerich?",                                        # an umlaut spelling does not matter
]


@pytest.mark.parametrize("text", NAMING)
def test_a_reply_naming_something_of_the_passages_is_dropped(text):
    reply = _answer({"reply": text, "kind": "clarify"})
    assert reply.reply_text == "" and reply.message == NO_ANSWER_TEXT and reply.removed_by[REPLY_DROPPED] == 1


def test_a_reply_naming_the_matter_of_a_title_is_dropped():
    """The matter number of '<uuid>_2024-055_Beilage-Vertrag' is a number; its kind is a common word."""
    assert not _answer({"reply": "Meinen Sie das Mandat 2024-055?", "kind": "clarify"}).reply_text
    assert not _answer({"reply": f"Meinen Sie {TITLES[0]}?", "kind": "clarify"}).reply_text
    assert _answer({"reply": "Meinen Sie eine Beilage zu einem Vertrag?", "kind": "clarify"}).reply_text


def test_the_same_names_pass_when_no_passage_holds_them():
    rows = [_row(1, "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate.", "Vertrag")]
    assert _answer({"reply": "Zu Herrn Huber finde ich nichts. Um welches Mandat geht es?", "kind": "clarify"},
                   rows=rows).reply_text


def test_the_names_of_the_passages():
    passages = [Passage(number=1, pointer="kanzlei/Mandanten/Limmat/x.pdf", title=TITLES[0], page=1,
                        text="Gem\u00e4ss Vertrag zahlt Sabine Vogt. Der Mieter, Herr M\u00fcller-Meier, zahlt."),
                Passage(number=2, pointer="p", title="Mietvertrag_Seefeldstrasse_48", page=None,
                        text="- Imhof vertritt die Muster AG.\nVogt unterschreibt.")]
    names = passage_names(passages)
    for name in ("vogt", "sabine", "muller", "meier", "limmat", "seefeldstrasse", "muster"):
        assert name in names, name
    for common in ("vertrag", "mieter", "herr", "gemass", "beilage", "mietvertrag", "kanzlei", "mandanten", "pdf"):
        assert conversation.fold(common) not in names, common
    assert "imhof" not in names                       # it begins a list item: the capital may be the sentence's
    assert names_a_passage("Zu Frau M\u00fcller?", names) and names_a_passage("Zu Frau Mueller?", names)


@pytest.mark.parametrize("word", ["K\u00fcndigungsfrist", "Mietvertr\u00e4gen", "Dokumenten", "Arbeitsvertrag", "Sie",
                                  "Ihnen", "Mandats", "Klageantwort", "Gegenpartei", "Kanzlei", "Abschnitt"])
def test_common_words_name_nothing(word):
    assert is_common(word)


@pytest.mark.parametrize("word", ["Vogt", "Seefeldstrasse", "Limmat", "Hardturm", "Muster", "Imhof", "Alpenblick"])
def test_names_are_no_common_words(word):
    assert not is_common(word)


def test_a_reply_longer_than_four_hundred_characters_is_dropped():
    base = "Bitte sagen Sie mir genauer, worum es geht"
    exact = (base + " " + "bitte " * 80)[:MAX_REPLY_CHARS - 1].rstrip() + "?"
    assert len(exact) <= MAX_REPLY_CHARS
    assert _answer({"reply": exact, "kind": "chat"}).reply_text == exact
    longer = exact[:-1] + " und mehr?"
    assert len(longer) > MAX_REPLY_CHARS
    assert _answer({"reply": longer, "kind": "chat"}).reply_text == ""


@pytest.mark.parametrize("value", [
    {"reply": ""}, {"reply": "   "}, {"reply": None}, {"reply": 42}, {"reply": ["Hallo"]}, {"reply": {"t": "Hallo"}},
    {"reply": "!!!"}, {"reply": "\u041f\u0440\u0438\u0432\u0435\u0442, \u0447\u0435\u043c \u043f\u043e\u043c\u043e\u0447\u044c?"}, {"reply": "Hallo \u0397\u03bb\u03af\u03b1\u03c2"},
])
def test_a_reply_that_is_not_plain_latin_text_is_dropped(value):
    reply = _answer(value)
    assert reply.reply_text == "" and reply.removed_by[REPLY_DROPPED] == 1


def test_one_reply_a_turn():
    second = {"reply": "Kann ich sonst noch helfen?", "kind": "chat"}
    reply = _answer(GREETING, second)
    assert reply.reply_text == GREETING["reply"] and reply.removed_by == _counts(reply_dropped=1)
    # a first reply that fails leaves room for the next
    reply = _answer({"reply": "Ich sehe 3 Dokumente.", "kind": "chat"}, second)
    assert reply.reply_text == second["reply"] and reply.removed_by == _counts(reply_dropped=1)


def test_white_space_and_unseen_characters_are_taken_out_of_a_reply():
    reply = _answer({"reply": "  Hallo!\u200b\n\n  Wie kann ich\thelfen?  ", "kind": "chat"})
    assert reply.reply_text == "Hallo! Wie kann ich helfen?"


def test_the_english_i_is_a_word_and_a_roman_one_a_number():
    """The demo tenant, 2026-11-22 ("wagwan", Claude answered in English): "I'm" read as the Roman numeral one."""
    said = ("Hey, not much, all good here! I'm the Knovas Assistent, so just ask me anything about the firm's "
            "documents and I'll dig in.")
    reply, _ = _nothing_found({"reply": said, "kind": "chat"})
    assert reply.reply_text == said
    for text in ("I can help with that.", "What can I do for you?", "I found nothing. Which matter is it?"):
        assert not holds_a_number(text), text
    for text in ("Gilt Teil I?", "Is it part I of the contract?", "Es geht um Kategorie C."):
        assert holds_a_number(text), text


# --- a document question still needs quotes ---------------------------------------------------------------------


def test_a_fact_written_as_a_reply_is_not_shown():
    reply = _answer({"reply": "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate.", "kind": "chat"})
    assert reply.status == "no_answer" and reply.message == NO_ANSWER_TEXT and reply.sentences == []


def test_a_document_question_is_answered_with_checked_statements_only():
    reply = _answer(FRIST, FORM)
    assert reply.status == "answered" and reply.reply_text == ""
    assert [(s.text, s.refs) for s in reply.sentences] == [(FRIST["statement"], (1,)), (FORM["statement"], (5,))]
    assert [p.number for p in reply.passages] == list(range(1, 8))              # all of them, as before


def test_a_statement_with_a_reply_key_is_a_statement_and_needs_its_quote():
    reply = _answer({**FRIST, "reply": "x", "quote": "steht nirgends im Text"})
    assert reply.status == "no_answer" and reply.removed_by == _counts(quote_not_found=1)


def test_a_reply_with_a_source_and_a_quote_is_still_a_reply_and_shows_neither():
    reply = _answer({"reply": "Gern geschehen!", "source": 1, "quote": "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate"})
    assert reply.reply_text == "Gern geschehen!" and reply.sentences == [] and reply.passages == []


# --- replies and statements mixed -----------------------------------------------------------------------------------


def test_a_short_lead_in_stands_above_the_statements():
    lead = {"reply": "Dazu habe ich Folgendes gefunden:"}
    reply = _answer(lead, FRIST, FORM)
    assert reply.status == "answered" and reply.reply_text == lead["reply"] and reply.reply_kind == ""
    assert len(reply.sentences) == 2 and reply.removed == 0


def test_a_lead_in_obeys_the_same_rules_and_is_shorter():
    too_long = {"reply": "Gern! " + "Hier ist, was ich gefunden habe, " * 6 + "bitte sehr:"}
    assert MAX_LEAD_IN_CHARS < len(too_long["reply"]) <= MAX_REPLY_CHARS
    reply = _answer(too_long, FRIST)
    assert reply.status == "answered" and reply.reply_text == "" and reply.removed_by == _counts(reply_dropped=1)
    reply = _answer({"reply": "Zu Frau Vogt habe ich das gefunden:"}, FRIST)
    assert reply.status == "answered" and reply.reply_text == "" and reply.removed_by == _counts(reply_dropped=1)
    reply = _answer({"reply": "Ich habe 2 Stellen gefunden:"}, FRIST)
    assert reply.reply_text == ""


def test_a_lead_in_without_a_statement_left_is_dropped():
    lead = {"reply": "Dazu habe ich Folgendes gefunden:", "kind": "chat"}
    bad = {**FRIST, "quote": "steht nirgends im Text"}
    reply = _answer(lead, bad)
    assert reply.status == "no_answer" and reply.message == NO_ANSWER_TEXT and reply.reply_text == ""
    assert reply.removed_by == _counts(quote_not_found=1, reply_dropped=1) and reply.removed == 2


def test_prose_beside_a_reply_is_removed_as_before():
    reply = _answer("Hier meine Antwort:", GREETING)
    assert reply.reply_text == GREETING["reply"] and reply.removed_by == _counts(not_a_statement=1)


def test_the_check_of_statements_leaves_replies_alone():
    shown, removed_by = check.check_answer(lines(GREETING, FRIST), {1: TEXTS[0]})
    assert [s.source for s in shown] == [1] and removed_by == _counts()
    assert check.is_reply(GREETING) and not check.is_reply(FRIST) and not check.is_reply({**FRIST, "reply": "x"})


# --- the instruction ----------------------------------------------------------------------------------------------


def test_the_instruction_teaches_the_reply_and_its_limits():
    for words in ('{"reply": "...", "kind": "chat"}', "friendly, natural way", "language of the question",
                  '"clarify"', "no digit and no number", "names no document, person, firm, place or matter",
                  "asks one helpful follow-up question", "at most one reply", "short reply as a lead-in",
                  'output only this line: {"no_answer": true}'):
        assert words in SYSTEM_INSTRUCTION, words
    for words in ('{"reply": "...", "kind": "chat"}', "needs no tool", "no digit and no number",
                  "asks one helpful follow-up question"):
        assert words in brain_tools.BRAIN_SYSTEM_INSTRUCTION, words
    assert len(brain_tools.BRAIN_SYSTEM_INSTRUCTION.encode("utf-8")) <= 4_000


def test_a_clarifying_reply_reaches_the_next_turn_as_the_assistant_turn():
    generate = FakeGenerate(lines(FRIST))
    AssistantService(search=FakeSearch(), generate=generate).answer(
        "Den Mietvertrag", [Turn("user", "document"), Turn("assistant", CLARIFY["reply"])])
    messages = generate.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"] == CLARIFY["reply"]


# --- clean titles -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("title, shown", [
    (f"{UUID_1}_2024-055_Beilage-Vertrag", "2024-055 \u00b7 Beilage-Vertrag"),
    (f"{UUID_2}_2019-029_Beilage-Vertrag", "2019-029 \u00b7 Beilage-Vertrag"),
    (f"{UUID_1.upper()}_2024-055_Beilage-Vertrag", "2024-055 \u00b7 Beilage-Vertrag"),
    ("2024-055_Beilage-Vertrag", "2024-055 \u00b7 Beilage-Vertrag"),
    ("2023-041_Abmahnung_an_Mieter", "2023-041 \u00b7 Abmahnung an Mieter"),
    ("24.117_Vollmacht.pdf", "24.117 \u00b7 Vollmacht.pdf"),
    (f"{UUID_1}_Mietvertrag Seefeld.pdf", "Mietvertrag Seefeld.pdf"),
    ("Mietvertrag_Seefeldstrasse_48.pdf", "Mietvertrag_Seefeldstrasse_48.pdf"),
    ("Nachtrag_2_2023-03-14.pdf", "Nachtrag_2_2023-03-14.pdf"),
    ("2024_Bericht", "2024_Bericht"),
    (UUID_1, UUID_1),
    (f"{UUID_1}_", f"{UUID_1}_"),
    (f"x{UUID_1}_2024-055_Akte", f"x{UUID_1}_2024-055_Akte"),
    ("  Vertrag \n  Meier ", "Vertrag Meier"),
    ("", ""),
    (None, ""),
])
def test_clean_titles(title, shown):
    assert clean_title(title) == shown


# --- the Brain's tools: the same rules -----------------------------------------------------------------------------


class _Block(dict):
    pass


class _Response:
    def __init__(self, blocks, stop="end_turn"):
        self.content = blocks
        self.stop_reason = stop
        self.id = "msg_conversation_1"


class _Call:
    def __init__(self, *responses):
        self.responses = list(responses)


def _text(value):
    return {"type": "text", "text": value}


def _tool_turn(answer):
    from assistant.providers import BRAIN_MCP_SERVER_NAME

    found = json.dumps({"results": [{"id": "g1", "title": "kanzlei/Limmat/Mietvertrag_Hardturm.pdf",
                                     "passages": [TEXTS[0], "Vertreten durch Reto Imhof, Rechtsanwalt."]}]})
    return [{"type": "mcp_tool_use", "id": "u1", "name": "search", "server_name": BRAIN_MCP_SERVER_NAME,
             "input": {"query": "Frist"}},
            {"type": "mcp_tool_result", "tool_use_id": "u1", "is_error": False,
             "content": [{"type": "text", "text": found}]},
            _text(answer)]


def _brain(blocks):
    service = BrainToolsService(ask=lambda **_kw: _Call(_Response(blocks)))
    return service.answer("wagwan")


def test_brain_tools_a_greeting_needs_no_tool():
    reply = _brain([_text(lines(GREETING))])
    assert reply.status == "no_answer" and reply.reply_text == GREETING["reply"] and reply.passages == []
    assert reply.message == GREETING["reply"] and not reply.opens_directly


@pytest.mark.parametrize("text", ["Ich sehe 3 Dokumente.", "\u041f\u0440\u0438\u0432\u0435\u0442!", "x" * 401])
def test_brain_tools_a_reply_breaking_a_rule_without_tools_is_dropped(text):
    reply = _brain([_text(lines({"reply": text}))])
    assert reply.reply_text == "" and reply.message == NO_ANSWER_TEXT and reply.removed_by[REPLY_DROPPED] == 1


def test_brain_tools_a_reply_naming_a_tool_result_is_dropped():
    for text in ("Geht es um den Mietvertrag Hardturm?", "Meinen Sie Herrn Imhof?", "Geht es um Limmat?"):
        reply = _brain(_tool_turn(lines({"reply": text, "kind": "clarify"})))
        assert reply.reply_text == "" and reply.message == NO_ANSWER_TEXT, text
    reply = _brain(_tool_turn(lines(CLARIFY)))
    assert reply.reply_text == CLARIFY["reply"] and len(reply.passages) == 1   # nearest: one per document


def test_brain_tools_mixed_and_statements_still_checked():
    frist = {**FRIST, "source": "kanzlei/Limmat/Mietvertrag_Hardturm.pdf"}
    reply = _brain(_tool_turn(lines({"reply": "Das habe ich gefunden:"}, frist)))
    assert reply.status == "answered" and reply.reply_text == "Das habe ich gefunden:"
    reply = _brain(_tool_turn(lines({"reply": "Das habe ich gefunden:"}, {**frist, "quote": "steht nirgends"})))
    assert reply.status == "no_answer" and reply.reply_text == "" and reply.removed_by[REPLY_DROPPED] == 1
    assert brain_tools.CLOSEST_PASSAGES == NEAREST_PASSAGES


# --- nothing of the text in any log line ----------------------------------------------------------------------------


SECRET_REPLY = "Hallo REPLYGEHEIM, wie kann ich helfen?"
SECRET_DROPPED = "Meinen Sie DROPGEHEIM Vogt?"


@pytest.fixture
def debug_everywhere():
    names = ["", "assistant", "assistant.service", "assistant.conversation", "assistant.check",
             "assistant.brain_tools", "web_interface", "web_interface.assistant_routes"]
    saved = {name: logging.getLogger(name).level for name in names}
    for name in names:
        logging.getLogger(name).setLevel(logging.DEBUG)
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


def test_no_reply_text_reaches_a_log_line_at_debug(debug_everywhere, caplog):
    caplog.set_level(logging.DEBUG)
    _answer({"reply": SECRET_REPLY, "kind": "chat"})
    _answer({"reply": SECRET_DROPPED, "kind": "clarify"})
    _answer({"reply": "Vorweg REPLYGEHEIM:"}, FRIST)
    _brain([_text(lines({"reply": SECRET_REPLY}))])
    _brain(_tool_turn(lines({"reply": SECRET_DROPPED})))
    logged = "\n".join(f"{r.name} {r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    assert caplog.records
    for secret in ("REPLYGEHEIM", "DROPGEHEIM", "Vogt", "Imhof", "Limmat", "Hardturm", "wagwan"):
        assert secret not in logged, secret
    assert "reply=chat" in logged and "reply=lead_in" in logged and "reply_dropped=1" in logged
