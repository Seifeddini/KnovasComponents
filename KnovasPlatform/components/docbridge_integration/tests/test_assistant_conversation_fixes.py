"""PMVP-20 conversation, the fix round after verification (with notes): the verifier's examples, one test per note.

1. Brain tools: the reply is checked against every text the tool results returned (titles, summaries, passages).
2. Plain-word facts: a reply that shares four consecutive words with what the model was given, or names a person by
   an honorific or a title, is dropped; every reply is labelled \u00abHinweis \u2013 ohne Quelle\u00bb (the pages: in
   test_assistant_conversation_pages.py); the instruction forbids yes/no answers and statements about the documents.
3. Names: after "Dr." or another abbreviation, a city after a postal code, at the start of a line or after a colon,
   names of two letters, a common word a passage uses as a name ("Herr Richter").
4. Numbers and dates: French and Italian number words, "Dutzend", small Roman numerals standing alone, number words
   split by a hyphen, weekdays, holidays, ends of periods, "letzten"/"naechsten" before a span.

Fakes only: no Core, no model. This file is ASCII-only: umlauts are \\u escapes.
"""
from __future__ import annotations

import json

import pytest

from assistant import brain_tools
from assistant.brain_tools import BrainToolsService
from assistant.check import REPLY_DROPPED
from assistant.conversation import (RUN_WORDS, Seen, check_reply, copies_a_passage, holds_a_number,
                                    names_a_passage, names_a_person, passage_names, passage_runs)
from assistant.service import NO_ANSWER_TEXT, SYSTEM_INSTRUCTION, AssistantService, Passage


def _row(n, text, title="Vertrag"):
    return {"doc_id": f"akten/{title}-{n}.pdf", "path": f"akten/{title}-{n}.pdf", "title": title,
            "page_number": 1, "score": 0.9, "snippet": text}


def _answer(reply, rows, kind="clarify"):
    line = json.dumps({"reply": reply, "kind": kind}, ensure_ascii=False)
    return AssistantService(search=lambda q, n: [dict(r) for r in rows],
                            generate=lambda **kw: line).answer("Frage?")


def _shown(reply, *texts):
    return _answer(reply, [_row(n, t) for n, t in enumerate(texts, start=1)]).reply_text == reply


# --- 1. the Brain's tools: every text of the tool results ----------------------------------------------------------


class _Response:
    def __init__(self, blocks):
        self.content, self.stop_reason, self.id = blocks, "end_turn", "msg_fix_1"


def _brain(reply, results):
    from assistant.providers import BRAIN_MCP_SERVER_NAME

    found = json.dumps({"results": results}, ensure_ascii=False)
    blocks = [{"type": "mcp_tool_use", "id": "u1", "name": "search", "server_name": BRAIN_MCP_SERVER_NAME,
               "input": {"query": "Vertrag"}},
              {"type": "mcp_tool_result", "tool_use_id": "u1", "is_error": False,
               "content": [{"type": "text", "text": found}]},
              {"type": "text", "text": json.dumps({"reply": reply, "kind": "clarify"}, ensure_ascii=False)}]
    return BrainToolsService(ask=lambda **_kw: type("C", (), {"responses": [_Response(blocks)]})()).answer("Vertrag?")


SUMMARY_ONLY = [{"id": "g1", "title": "kanzlei/Mandate/Vertrag.pdf", "passages": [],
                 "summary": "Mietvertrag zwischen Sabine Vogt und der Muster AG."}]
TITLE_ONLY = [{"id": "g2", "title": "kanzlei/Hardturm/Aktennotiz.docx", "passages": []}]
WITH_PASSAGE = [{"id": "g3", "title": "kanzlei/Mandate/Brief.pdf", "passages": ["Der Brief ist kurz."],
                 "summary": "Ein Brief an Reto Imhof."}]


@pytest.mark.parametrize("reply, results", [
    ("Meinen Sie den Vertrag mit Frau Vogt?", SUMMARY_ONLY),          # the verifier's example
    ("Meinen Sie den Vertrag mit Vogt?", SUMMARY_ONLY),               # without the honorific: the summary's name
    ("Geht es um Hardturm?", TITLE_ONLY),                              # the title of a result without passages
    ("Geht es um Imhof?", WITH_PASSAGE),                               # the summary beside a passage
])
def test_brain_tools_a_reply_naming_any_text_of_the_tool_results_is_dropped(reply, results):
    answer = _brain(reply, results)
    assert answer.reply_text == "" and answer.message == NO_ANSWER_TEXT and answer.removed_by[REPLY_DROPPED] == 1


def test_brain_tools_a_reply_naming_nothing_still_passes():
    for results in (SUMMARY_ONLY, TITLE_ONLY, WITH_PASSAGE):
        assert _brain("Um welches Mandat geht es genau?", results).reply_text == "Um welches Mandat geht es genau?"


def test_brain_tools_the_seen_texts_hold_titles_summaries_and_passages():
    from assistant.providers import BRAIN_MCP_SERVER_NAME

    found = brain_tools.read_tool_results([_Response([
        {"type": "mcp_tool_use", "id": "u1", "name": "search", "server_name": BRAIN_MCP_SERVER_NAME, "input": {}},
        {"type": "mcp_tool_result", "tool_use_id": "u1", "is_error": False,
         "content": [{"type": "text", "text": json.dumps({"results": SUMMARY_ONLY + WITH_PASSAGE})}]}])])
    assert [s.title for s in found.seen] == ["kanzlei/Mandate/Vertrag.pdf", "kanzlei/Mandate/Brief.pdf"]
    assert "Sabine Vogt" in found.seen[0].text and "Reto Imhof" in found.seen[1].text
    assert "Der Brief ist kurz." in found.seen[1].text and len(found.passages) == 1


# --- 2. plain-word facts --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("reply, passage", [
    ("Die K\u00fcndigung muss schriftlich erfolgen.", "Die K\u00fcndigung muss schriftlich erfolgen."),
    ("Ja, der Vertrag wurde unterschrieben.", "Am Ende der Vertrag wurde unterschrieben und versandt."),
    ("Wie gesagt: die K\u00fcndigung muss  SCHRIFTLICH erfolgen", "Die Kuendigung muss schriftlich erfolgen."),
])
def test_a_reply_copying_four_words_of_a_passage_is_dropped(reply, passage):
    assert RUN_WORDS == 4
    assert not _shown(reply, passage)


def test_three_shared_words_are_no_copy():
    assert _shown("Geht es um die K\u00fcndigung?", "Es geht um die K\u00fcndigung des Mietvertrags.")
    runs = passage_runs([Passage(number=1, pointer="p", title="t", page=None, text="eins zwei drei vier")])
    assert copies_a_passage("Eins, zwei, drei, vier!", runs) and not copies_a_passage("eins zwei drei", runs)


@pytest.mark.parametrize("reply", [
    "Herr Richter hat unterschrieben.",                     # the verifier's example; no passage holds "Richter"
    "Frau Keller ist zust\u00e4ndig.", "Meinen Sie Herrn Meier?", "Ist Dr. Vogt gemeint?", "Prof. Huber schreibt.",
    "Mr Smith signed.", "Mrs Brown is in charge.", "Ms Green?", "Me Dupont a sign\u00e9.",
])
def test_a_reply_naming_a_person_by_an_honorific_is_dropped(reply):
    assert names_a_person(reply)
    assert not _shown(reply, "Der Vertrag gilt ab sofort.")


@pytest.mark.parametrize("reply", [
    "Fragen Sie mich nach einer Frau oder einem Herrn?", "Sehr geehrte Frau, wie kann ich helfen?",
    "Me too.", "Hallo! Wie kann ich Ihnen helfen?",
])
def test_an_honorific_without_a_name_is_no_person(reply):
    assert not names_a_person(reply)


def test_the_instruction_forbids_yes_no_answers_and_statements_about_the_documents():
    for instruction in (SYSTEM_INSTRUCTION, brain_tools.BRAIN_SYSTEM_INSTRUCTION):
        assert "never answers a yes/no question about the documents, never states anything about them" in instruction
        assert len(instruction.encode("utf-8")) <= 4_000


def test_what_the_rules_cannot_see_is_shown_and_labelled_on_the_page():
    """The residual risk (management/assistant.md): a plain-word statement that copies no four words and names no
    one passes the rules; the instruction forbids it and the page labels every reply \u00abHinweis - ohne Quelle\u00bb."""
    assert _shown("Ja, er ist g\u00fcltig.", "Der Vertrag wurde am Montag unterschrieben.")


# --- 3. names the first round missed -------------------------------------------------------------------------------


TEXT = ("Vertreten durch Dr. Meier, Bahnhofplatz, 8001 Z\u00fcrich.\nVogt unterschreibt.\nVertreter: Imhof. "
        "Frau Li und Herr Richter sowie Hans Richter. Der Vertrag gilt. Geschrieben von S. Keller.")


@pytest.mark.parametrize("reply", [
    "Meinen Sie den Bericht von Meier?",            # after "Dr."
    "Geht es um Z\u00fcrich?",                        # a city after a postal code
    "Geht es um Vogt?",                               # at the start of a line
    "Geht es um Imhof?",                              # after a colon
    "Geht es um Li?",                                 # two letters
    "Meinen Sie den Richter?",                        # a common word a passage uses as a name
    "Geht es um Keller?",                             # after an initial
])
def test_names_the_first_round_missed_are_names_now(reply):
    names = passage_names([Passage(number=1, pointer="p", title="t", page=None, text=TEXT)])
    assert names_a_passage(reply, names)
    assert not _shown(reply, TEXT)


@pytest.mark.parametrize("reply", [
    "Um welchen Vertrag geht es?",                    # "Vertrag" stands mid-sentence in passages, as German nouns do
    "Welche Art von Dokument suchen Sie?", "Gern helfe ich Ihnen weiter.",
])
def test_common_nouns_stay_common_where_no_passage_uses_them_as_a_name(reply):
    assert _shown(reply, TEXT, "Die Parteien schliessen den Vertrag. Der Vertrag gilt ab Juni.")


def test_a_sentence_start_and_an_enumeration_are_still_no_names():
    names = passage_names([Passage(number=1, pointer="p", title="t", page=None,
                                   text="Der Mieter zahlt. Gem\u00e4ss Vertrag gilt das. 1. Kaution: bezahlt.")])
    assert names == frozenset()


# --- 4. numbers and dates the first round missed -------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Il y a deux contrats.", "Le d\u00e9lai est de trois mois.", "dix jours", "cent pages", "mille francs",
    "un mois", "une semaine", "Ci sono due mesi.", "tre giorni", "dieci anni", "cento pagine", "uno anno",
    "ein Dutzend Fragen", "a dozen letters", "Dutzende Fragen",
    "Teil iv", "Ziffer ii gilt", "Abschnitt xii",
    "dr-ei Monate", "zw-ei Wochen",
    "am Montag", "bis Freitag", "vor Weihnachten", "nach Ostern", "zum Monatsende", "per Jahresende",
    "Quartalsende", "letzten Monat", "n\u00e4chsten Freitag", "naechste Woche", "kommenden Jahr", "last week",
    "next month", "lunedi", "dimanche",
])
def test_numbers_and_dates_the_first_round_missed_are_dropped(text):
    assert holds_a_number(text)
    assert not _shown(text, "Der Vertrag gilt.")


@pytest.mark.parametrize("text", [
    "Kannst du mir helfen?", "Das ist due.", "un dossier", "Was ist der n\u00e4chste Schritt?",
    "Ich helfe gern.", "I can help with that.", "Hallo! Wie kann ich Ihnen helfen?", "Das ist ein Mix.",
    "Um welches Mandat geht es?",
])
def test_words_that_are_no_numbers_still_pass(text):
    assert not holds_a_number(text)


def test_seen_is_checked_like_a_passage():
    names = passage_names([Seen(title="a/Hardturm.pdf", text="Zusammenfassung: Vertrag mit Sabine Vogt.")])
    assert {"hardturm", "sabine", "vogt"} <= names
    assert check_reply({"reply": "Geht es um Hardturm?"}, names) is None
