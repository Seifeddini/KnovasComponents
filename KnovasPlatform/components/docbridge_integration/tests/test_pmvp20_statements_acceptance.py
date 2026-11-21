"""PMVP-20 acceptance tests, second design: the assistant answers in statements, each with one source and a
word-for-word quote, and the Platform checks all three (FEAT-2026-11-19-platform-mvp, Jira SS-732).

FROZEN. Written by the coordinator on 2026-11-20; a builder never edits this file. Its git blob id is in
docs/platform-mvp/frozen-tests.txt. It replaces test_pmvp20_assistant_acceptance.py and
test_pmvp20_sentence_split_acceptance.py, which pinned the first design (free prose with [n] marks, cut into
sentences by punctuation). That design failed verification twice: no rule set splits German legal prose safely.

The design (founder, 2026-11-20: "research how others solve it and improve our system"):

  The model is told to answer with one JSON object per line and nothing else:
      {"statement": "...", "source": 2, "quote": "..."}
  or, when the passages do not answer the question:
      {"no_answer": true}

  A statement is shown only if all of this holds; otherwise it is removed and counted:
    1. the line is such an object (a line of prose, a broken object, a wrong type: removed);
    2. `source` is the number of a passage the person's search returned;
    3. `quote` stands word for word in that passage (three words or more, in one piece; case, white space and the
       kind of quotation mark, apostrophe or dash do not matter);
    4. every number in the statement stands in that passage (digits; 1'500 equals 1500; a number from 1 to 12 may
       be written as a word in the passage);
    5. the statement is at most 500 characters and is not the no-answer sentence.

  The quote the reply carries is cut from the passage itself, so it can never be the model's wording.
  Nothing is cut into sentences any more: the unit is the statement the model declared, and the check is
  mechanical. What it cannot see is a statement that says more in words than its quote supports; the page shows the
  quote under the statement so the reader sees the evidence (plan section 7, risks).

Contract of L2-PLATFORM-ASSISTANT (plan section 7): POST-01 every shown reference points to a retrieved passage;
POST-02 no passage enters the prompt that the person's own search did not return; POST-04 no question, answer or
passage text in a log line; POST-05 no supported statement -> "Dazu finde ich nichts in Ihren Quellen"; POST-06 a
failed or busy Core gives a plain message and no partial answer. Plus the Core's limits for /secured/llm/generate.

The service is tested with two fakes: `search(question, limit) -> rows` (rows as KnovasAPIClient.search_documents
returns them, passage text under "snippet") and `generate(*, system_instruction, messages, max_tokens) -> str`.
"""
from __future__ import annotations

import json
import logging

import pytest

from assistant.errors import CoreBusy, CoreFailed
from assistant.service import NO_ANSWER_TEXT, AssistantService, Turn

CORE_MAX_MESSAGES = 32
CORE_MAX_MESSAGE_BYTES = 50_000
CORE_MAX_SYSTEM_BYTES = 4_000
CORE_MAX_TOKENS = 2_048


def _row(n, text, *, title=None, page=1, score=None):
    return {
        "doc_id": f"Mandate/Mueller/doc-{n}.pdf",
        "path": f"Mandate/Mueller/doc-{n}.pdf",
        "title": title or f"Dokument {n}",
        "page_number": page,
        "score": score if score is not None else 1.0 - n / 100,
        "snippet": text,
    }


ROWS = [
    _row(1, "Die Kuendigungsfrist betraegt drei Monate zum Monatsende."),
    _row(2, "Die Frist beginnt mit dem Zugang der Kuendigung."),
    _row(3, "Der Vertrag wurde am 1. Maerz 2024 unterzeichnet."),
    _row(4, "Die Miete betraegt CHF 1’500 pro Monat; massgebend ist Art. 266a OR."),
]


def st(statement, source, quote, **more):
    return json.dumps({"statement": statement, "source": source, "quote": quote, **more}, ensure_ascii=False)


FRIST = st("Die Kuendigungsfrist betraegt drei Monate.", 1, "Kuendigungsfrist betraegt drei Monate")
BEGINN = st("Sie beginnt mit dem Zugang der Kuendigung.", 2, "beginnt mit dem Zugang der Kuendigung")
DATUM = st("Der Vertrag wurde am 1. Maerz 2024 unterzeichnet.", 3, "am 1. Maerz 2024 unterzeichnet")
NO_ANSWER_LINE = json.dumps({"no_answer": True})


class FakeSearch:
    def __init__(self, rows=None, exc=None):
        self.rows = list(ROWS if rows is None else rows)
        self.exc = exc
        self.calls = []

    def __call__(self, question, limit):
        self.calls.append((question, limit))
        if self.exc is not None:
            raise self.exc
        return [dict(r) for r in self.rows]


class FakeGenerate:
    def __init__(self, answer="", exc=None):
        self.answer = answer
        self.exc = exc
        self.calls = []

    def __call__(self, *, system_instruction, messages, max_tokens):
        self.calls.append({"system_instruction": system_instruction,
                           "messages": [dict(m) for m in messages],
                           "max_tokens": max_tokens})
        if self.exc is not None:
            raise self.exc
        return self.answer


def _service(search=None, generate=None):
    return AssistantService(search=search or FakeSearch(), generate=generate or FakeGenerate())


def _reply(*lines, rows=None):
    return _service(FakeSearch(rows), FakeGenerate("\n".join(lines))).answer("Welche Frist gilt?")


def _prompt_text(call):
    return call["system_instruction"] + "\n" + "\n".join(m["content"] for m in call["messages"])


def _shown(reply):
    return [(s.text, s.refs) for s in reply.sentences]


# --- POST-02: the prompt holds only what the person's search returned ---------------------------


def test_the_question_is_searched_once_as_given():
    search = FakeSearch()
    _service(search, FakeGenerate(FRIST)).answer("Welche Kuendigungsfrist gilt?")
    assert len(search.calls) == 1
    assert search.calls[0][0] == "Welche Kuendigungsfrist gilt?"
    assert search.calls[0][1] >= 1


def test_every_retrieved_passage_is_numbered_in_the_prompt():
    generate = FakeGenerate(FRIST)
    _service(FakeSearch(), generate).answer("Welche Kuendigungsfrist gilt?")
    assert len(generate.calls) == 1
    prompt = _prompt_text(generate.calls[0])
    for n, row in enumerate(ROWS, start=1):
        assert f"[{n}]" in prompt
        assert row["snippet"] in prompt


def test_the_prompt_asks_for_the_agreed_form():
    generate = FakeGenerate(FRIST)
    _service(FakeSearch(), generate).answer("Welche Kuendigungsfrist gilt?")
    prompt = _prompt_text(generate.calls[0])
    for word in ('"statement"', '"source"', '"quote"', '"no_answer"'):
        assert word in prompt


def test_no_text_enters_the_prompt_that_search_did_not_return():
    marker = "GEHEIMER-TEXT-AUS-EINEM-ANDEREN-MANDAT"
    generate = FakeGenerate(FRIST)
    reply = _service(FakeSearch(), generate).answer("Welche Kuendigungsfrist gilt?")
    assert marker not in _prompt_text(generate.calls[0])
    # the passages the reply offers are exactly the retrieved ones, numbered 1..n in order
    assert [p.number for p in reply.passages] == [1, 2, 3, 4]
    assert [p.text for p in reply.passages] == [r["snippet"] for r in ROWS]
    assert [p.pointer for p in reply.passages] == [r["doc_id"] for r in ROWS]


def test_rows_without_passage_text_are_not_passages():
    rows = [_row(1, "Die Frist betraegt drei Monate."), _row(2, ""), dict(_row(3, "x"), snippet=None)]
    generate = FakeGenerate(st("Die Frist betraegt drei Monate.", 1, "Frist betraegt drei Monate"))
    reply = _service(FakeSearch(rows), generate).answer("Frist?")
    assert [p.text for p in reply.passages] == ["Die Frist betraegt drei Monate."]
    assert "[2]" not in _prompt_text(generate.calls[0])


# --- reading the answer: statements, nothing else ----------------------------------------------------


def test_statements_with_a_source_and_a_true_quote_are_shown():
    reply = _reply(FRIST, BEGINN)
    assert reply.status == "answered"
    assert reply.removed == 0
    assert _shown(reply) == [("Die Kuendigungsfrist betraegt drei Monate.", (1,)),
                             ("Sie beginnt mit dem Zugang der Kuendigung.", (2,))]
    assert [s.quote for s in reply.sentences] == ["Kuendigungsfrist betraegt drei Monate",
                                                  "beginnt mit dem Zugang der Kuendigung"]


def test_the_answer_may_be_one_array_of_statements():
    reply = _reply("[" + FRIST + ",\n " + BEGINN + "]")
    assert [s.refs for s in reply.sentences] == [(1,), (2,)]
    assert reply.removed == 0


def test_a_code_fence_list_marks_and_blank_lines_around_the_statements_do_not_matter():
    reply = _reply("```json", "- " + FRIST, "", "2. " + BEGINN, "```")
    assert [s.refs for s in reply.sentences] == [(1,), (2,)]
    assert reply.removed == 0


def test_further_keys_in_a_statement_are_ignored():
    reply = _reply(st("Die Kuendigungsfrist betraegt drei Monate.", 1, "betraegt drei Monate", sicher="ja"))
    assert [s.refs for s in reply.sentences] == [(1,)]


def test_a_source_written_as_a_digit_string_is_read():
    reply = _reply(st("Die Kuendigungsfrist betraegt drei Monate.", "1", "betraegt drei Monate"))
    assert [s.refs for s in reply.sentences] == [(1,)]


PROSE = [
    "Im Allgemeinen gelten gesetzliche Fristen.",
    "Die Kuendigungsfrist betraegt drei Monate [1].",
    "Der Streitwert betraegt CHF 2 Mio. Die Kuendigungsfrist betraegt drei Monate [1].",
    "Hier ist die Antwort:",
]


@pytest.mark.parametrize("line", PROSE)
def test_a_line_of_prose_is_removed_and_counted_with_or_without_numbers(line):
    reply = _reply(line, BEGINN)
    assert _shown(reply) == [("Sie beginnt mit dem Zugang der Kuendigung.", (2,))]
    assert reply.removed == 1


def test_an_answer_in_prose_only_is_no_answer():
    reply = _reply("Die Kuendigungsfrist betraegt drei Monate [1]. Sie beginnt mit dem Zugang [2].")
    assert reply.status == "no_answer"
    assert reply.sentences == []
    assert reply.removed == 1
    assert [p.number for p in reply.passages] == [1, 2, 3, 4]


BROKEN = [
    '{"statement": "Die Kuendigungsfrist betraegt drei Monate.", "source": 1, "quote": "betraegt drei Mon',
    '{"statement": "Die Kuendigungsfrist betraegt drei Monate.", "source": 1}',
    '{"statement": "Die Kuendigungsfrist betraegt drei Monate.", "quote": "betraegt drei Monate"}',
    '{"source": 1, "quote": "betraegt drei Monate"}',
    st("", 1, "betraegt drei Monate"),
    st("   ", 1, "betraegt drei Monate"),
    st("Die Kuendigungsfrist betraegt drei Monate.", 1, ""),
    st("Die Kuendigungsfrist betraegt drei Monate.", "eins", "betraegt drei Monate"),
    st("Die Kuendigungsfrist betraegt drei Monate.", 1.5, "betraegt drei Monate"),
    st("Die Kuendigungsfrist betraegt drei Monate.", [1, 2], "betraegt drei Monate"),
    st("Die Kuendigungsfrist betraegt drei Monate.", None, "betraegt drei Monate"),
    st(["Die Kuendigungsfrist betraegt drei Monate."], 1, "betraegt drei Monate"),
    st("Die Kuendigungsfrist betraegt drei Monate.", 1, ["betraegt drei Monate"]),
    '"Die Kuendigungsfrist betraegt drei Monate."',
    "42",
    "null",
]


@pytest.mark.parametrize("line", BROKEN)
def test_a_broken_or_incomplete_statement_is_removed_and_counted(line):
    reply = _reply(line, BEGINN)
    assert [s.refs for s in reply.sentences] == [(2,)]
    assert reply.removed == 1


# --- check 2: the source is a retrieved passage --------------------------------------------------------


@pytest.mark.parametrize("source", [9, 0, -1, 5])
def test_a_statement_from_a_passage_that_was_not_retrieved_is_removed(source):
    reply = _reply(FRIST, st("Das Gericht hat anders entschieden.", source, "Gericht hat anders entschieden"))
    assert [s.refs for s in reply.sentences] == [(1,)]
    assert reply.removed == 1
    assert all("Gericht" not in s.text for s in reply.sentences)


# --- check 3: the quote stands in the passage, word for word ---------------------------------------------


def test_a_quote_from_another_passage_does_not_support_the_statement():
    # the quote is real, but it is in passage 2, and the statement points to passage 1
    reply = _reply(st("Die Frist beginnt mit dem Zugang.", 1, "beginnt mit dem Zugang der Kuendigung"), FRIST)
    assert [s.refs for s in reply.sentences] == [(1,)]
    assert reply.sentences[0].text == "Die Kuendigungsfrist betraegt drei Monate."
    assert reply.removed == 1


@pytest.mark.parametrize("quote", [
    "Die Kuendigungsfrist betraegt sechs Monate",          # changed
    "Die Frist betraegt drei Monate",                      # shortened inside
    "frist betraegt drei Monate",                          # starts inside a word (Kuendigungsfrist)
    "Kuendigungsfrist betraegt drei Monat",                # ends inside a word (Monate)
    "Kuendigungsfrist betraegt ... zum Monatsende",        # two pieces
    "Kuendigungsfrist betraegt … zum Monatsende",
    "Monate zum Monatsende. Die Frist beginnt",            # runs over the passage's end
    "drei Monate",                                         # two words
    "Monatsende",                                          # one word
    "zum zum zum",                                         # three words, not in the passage
])
def test_a_quote_that_is_not_in_the_passage_in_one_piece_is_removed(quote):
    reply = _reply(st("Die Kuendigungsfrist betraegt drei Monate.", 1, quote), BEGINN)
    assert [s.refs for s in reply.sentences] == [(2,)]
    assert reply.removed == 1


@pytest.mark.parametrize("quote, cut_from_the_passage", [
    ("betraegt drei Monate", "betraegt drei Monate"),                                  # three words are enough
    ("die  KUENDIGUNGSFRIST betraegt\tdrei monate", "Die Kuendigungsfrist betraegt drei Monate"),
    ("  Kuendigungsfrist betraegt drei Monate zum Monatsende.  ", "Kuendigungsfrist betraegt drei Monate zum Monatsende."),
    ("«Kuendigungsfrist betraegt drei Monate»", "Kuendigungsfrist betraegt drei Monate"),
    ("Die Kuendigungsfrist betraegt drei Monate zum Monatsende.", ROWS[0]["snippet"]),   # the whole passage
])
def test_case_white_space_and_surrounding_quotation_marks_do_not_matter(quote, cut_from_the_passage):
    reply = _reply(st("Die Kuendigungsfrist betraegt drei Monate.", 1, quote))
    assert [s.refs for s in reply.sentences] == [(1,)]
    assert reply.sentences[0].quote == cut_from_the_passage     # the passage's own characters, not the model's


def test_apostrophes_and_dashes_of_another_kind_do_not_matter():
    rows = [_row(1, "Die Miete betraegt CHF 1’500 – zahlbar im Voraus.")]
    reply = _reply(st("Die Miete betraegt CHF 1'500.", 1, "Miete betraegt CHF 1'500 - zahlbar im Voraus"), rows=rows)
    assert [s.refs for s in reply.sentences] == [(1,)]
    assert reply.sentences[0].quote == "Miete betraegt CHF 1’500 – zahlbar im Voraus"


# --- check 4: every number in the statement stands in the passage ----------------------------------------


@pytest.mark.parametrize("statement", [
    "Die Kuendigungsfrist betraegt drei Monate, der Streitwert CHF 2 Mio.",
    "Die Kuendigungsfrist betraegt seit 2023 drei Monate.",
    "Die Kuendigungsfrist betraegt drei Monate (Art. 266a OR).",       # 266a is in passage 4, not in passage 1
    "Die Kuendigungsfrist betraegt 6 Monate.",
    "Die Kuendigungsfrist betraegt drei Monate und endet am 31.12.2025.",
])
def test_a_statement_with_a_number_its_passage_does_not_hold_is_removed(statement):
    reply = _reply(st(statement, 1, "Kuendigungsfrist betraegt drei Monate"), BEGINN)
    assert [s.refs for s in reply.sentences] == [(2,)]
    assert reply.removed == 1


@pytest.mark.parametrize("statement, source, quote", [
    ("Der Vertrag wurde am 1. Maerz 2024 unterzeichnet.", 3, "am 1. Maerz 2024 unterzeichnet"),
    ("Die Miete betraegt CHF 1500 pro Monat.", 4, "Miete betraegt CHF 1'500 pro Monat"),
    ("Die Miete betraegt CHF 1'500 pro Monat.", 4, "Miete betraegt CHF 1'500 pro Monat"),
    ("Massgebend ist Art. 266a OR.", 4, "massgebend ist Art. 266a OR"),
    ("Die Kuendigungsfrist betraegt 3 Monate.", 1, "Kuendigungsfrist betraegt drei Monate"),   # 3 = drei
    ("Die Kuendigungsfrist betraegt drei Monate.", 1, "Kuendigungsfrist betraegt drei Monate"),
])
def test_a_statement_whose_numbers_all_stand_in_its_passage_is_shown(statement, source, quote):
    reply = _reply(st(statement, source, quote))
    assert _shown(reply) == [(statement, (source,))]
    assert reply.removed == 0


# --- check 5: one statement, not an essay, and never the no-answer sentence ------------------------------


def test_a_statement_longer_than_500_characters_is_removed():
    long_statement = "Die Kuendigungsfrist betraegt drei Monate " + "und noch etwas " * 35 + "."
    assert len(long_statement) > 500
    reply = _reply(st(long_statement, 1, "Kuendigungsfrist betraegt drei Monate"), BEGINN)
    assert [s.refs for s in reply.sentences] == [(2,)]
    assert reply.removed == 1


def test_reference_marks_inside_a_statement_are_not_shown():
    reply = _reply(st("Die Kuendigungsfrist betraegt drei Monate [1].", 1, "Kuendigungsfrist betraegt drei Monate"))
    assert _shown(reply) == [("Die Kuendigungsfrist betraegt drei Monate.", (1,))]


# --- POST-05: nothing supported -> say so, show the closest passages -------------------------------


@pytest.mark.parametrize("answer", [
    NO_ANSWER_LINE,
    "```json\n" + NO_ANSWER_LINE + "\n```",
    NO_ANSWER_TEXT,
    NO_ANSWER_TEXT + ".",
    "**" + NO_ANSWER_TEXT + ".**",
    "«" + NO_ANSWER_TEXT + "»",
    "",
    "   \n  ",
])
def test_the_model_saying_there_is_no_answer_gives_the_no_answer_reply(answer):
    reply = _reply(answer)
    assert reply.status == "no_answer"
    assert reply.message == NO_ANSWER_TEXT
    assert reply.sentences == []
    assert [p.number for p in reply.passages] == [1, 2, 3, 4]
    assert reply.removed == 0


def test_the_no_answer_sentence_is_never_shown_as_a_statement():
    reply = _reply(FRIST, st(NO_ANSWER_TEXT + ".", 2, "beginnt mit dem Zugang der Kuendigung"))
    assert _shown(reply) == [("Die Kuendigungsfrist betraegt drei Monate.", (1,))]
    assert all("finde ich nichts" not in s.text for s in reply.sentences)


def test_a_no_answer_line_beside_supported_statements_does_not_take_them_away():
    reply = _reply(FRIST, NO_ANSWER_LINE)
    assert reply.status == "answered"
    assert [s.refs for s in reply.sentences] == [(1,)]
    assert reply.removed == 0


def test_nothing_left_after_the_check_gives_the_no_answer_reply():
    reply = _reply(st("Das steht im Gesetz.", 8, "Das steht im Gesetz"), "Allgemein gilt das so.")
    assert reply.status == "no_answer"
    assert reply.sentences == []
    assert reply.message == NO_ANSWER_TEXT
    assert [p.number for p in reply.passages] == [1, 2, 3, 4]
    assert reply.removed == 2


def test_no_passages_means_no_generation_and_the_no_answer_reply():
    generate = FakeGenerate(FRIST)
    reply = _service(FakeSearch(rows=[]), generate).answer("Frist?")
    assert generate.calls == []
    assert reply.status == "no_answer"
    assert reply.message == NO_ANSWER_TEXT
    assert reply.sentences == []
    assert reply.passages == []


def test_no_answer_text_is_the_agreed_sentence():
    assert NO_ANSWER_TEXT == "Dazu finde ich nichts in Ihren Quellen"


# --- POST-06: failures give a plain message and no partial answer ----------------------------------


def test_a_busy_core_gives_a_plain_busy_message_and_no_answer_text():
    reply = _service(generate=FakeGenerate(exc=CoreBusy())).answer("Frist?")
    assert reply.status == "busy"
    assert reply.sentences == []
    assert reply.message and "ausgelastet" in reply.message


def test_a_failed_core_gives_a_plain_message_and_no_answer_text():
    reply = _service(generate=FakeGenerate(exc=CoreFailed())).answer("Frist?")
    assert reply.status == "failed"
    assert reply.sentences == []
    assert reply.message


@pytest.mark.parametrize("exc", [CoreBusy(), CoreFailed(), RuntimeError("boom")])
def test_a_failed_search_gives_no_generation_and_no_exception(exc):
    generate = FakeGenerate(FRIST)
    reply = _service(FakeSearch(exc=exc), generate).answer("Frist?")
    assert generate.calls == []
    assert reply.status in {"busy", "failed"}
    assert reply.sentences == []
    assert reply.message


def test_an_unexpected_generation_error_does_not_escape():
    reply = _service(generate=FakeGenerate(exc=ValueError("kaputt"))).answer("Frist?")
    assert reply.status == "failed"
    assert reply.sentences == []


# --- The Core's limits for /secured/llm/generate (W-06 groundwork) ----------------------------------


def _assert_within_core_limits(call):
    assert 1 <= len(call["messages"]) <= CORE_MAX_MESSAGES
    assert {m["role"] for m in call["messages"]} <= {"user", "assistant"}
    for m in call["messages"]:
        assert isinstance(m["content"], str)
        assert len(m["content"].encode("utf-8")) <= CORE_MAX_MESSAGE_BYTES
    assert len(call["system_instruction"].encode("utf-8")) <= CORE_MAX_SYSTEM_BYTES
    assert 1 <= call["max_tokens"] <= CORE_MAX_TOKENS


def test_a_long_history_stays_within_the_core_limits_and_ends_with_the_question():
    history = [Turn(role="user" if i % 2 == 0 else "assistant", text=f"Runde {i} " + "x" * 200)
               for i in range(40)]
    generate = FakeGenerate(FRIST)
    _service(generate=generate).answer("Und wann beginnt die Frist?", history)
    call = generate.calls[0]
    _assert_within_core_limits(call)
    assert call["messages"][-1]["role"] == "user"
    assert "Und wann beginnt die Frist?" in call["messages"][-1]["content"]
    # the most recent turn is kept, the oldest is dropped first
    joined = "\n".join(m["content"] for m in call["messages"])
    assert "Runde 39" in joined
    assert "Runde 0 " not in joined


def test_long_passages_are_cut_to_fit_the_core_limits():
    rows = [_row(n, f"Passage {n} " + "Wort " * 12_000) for n in range(1, 9)]
    generate = FakeGenerate(st("Das steht in der ersten Stelle.", 1, "Wort Wort Wort"))
    reply = _service(FakeSearch(rows), generate).answer("Frist?")
    _assert_within_core_limits(generate.calls[0])
    assert reply.status == "answered"


def test_a_short_history_is_passed_on_in_order():
    history = [Turn(role="user", text="Wer ist die Gegenpartei?"),
               Turn(role="assistant", text="Die Gegenpartei ist die Muster AG.")]
    generate = FakeGenerate(FRIST)
    _service(generate=generate).answer("Und die Frist?", history)
    messages = generate.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == "Wer ist die Gegenpartei?"
    assert messages[1]["content"] == "Die Gegenpartei ist die Muster AG."


# --- POST-04: no question, answer or passage text in a log line -------------------------------------


def test_no_customer_text_reaches_a_log_line(caplog):
    question = "FRAGE-ZEICHENKETTE-4711 zur Frist?"
    rows = [_row(1, "PASSAGE-ZEICHENKETTE-0815 gilt fuer drei Monate."), _row(2, "PASSAGE-ZEICHENKETTE-0816")]
    answers = [
        FakeGenerate("\n".join([
            st("ANTWORT-ZEICHENKETTE-42 gilt.", 1, "PASSAGE-ZEICHENKETTE-0815 gilt fuer drei"),
            st("ERFUNDEN-ZEICHENKETTE-99 gilt.", 9, "ZITAT-ZEICHENKETTE-55 steht nirgends"),
            "PROSA-ZEICHENKETTE-77 ohne Form.",
            '{"statement": "KAPUTT-ZEICHENKETTE-66',
        ])),
        FakeGenerate(exc=CoreBusy()),
        FakeGenerate(exc=CoreFailed()),
        FakeGenerate(exc=ValueError("FEHLER-ZEICHENKETTE-13")),
    ]
    with caplog.at_level(logging.DEBUG):
        for generate in answers:
            _service(FakeSearch(rows), generate).answer(question, [Turn("user", "VERLAUF-ZEICHENKETTE-7")])
        _service(FakeSearch(exc=RuntimeError("SUCHFEHLER-ZEICHENKETTE-8")), FakeGenerate()).answer(question)
    logged = "\n".join(
        r.getMessage() + " " + (r.exc_text or "") + " " + str(getattr(r, "__dict__", {}))
        for r in caplog.records
    )
    for token in ("FRAGE-ZEICHENKETTE", "PASSAGE-ZEICHENKETTE", "ANTWORT-ZEICHENKETTE", "ERFUNDEN-ZEICHENKETTE",
                  "ZITAT-ZEICHENKETTE", "PROSA-ZEICHENKETTE", "KAPUTT-ZEICHENKETTE", "FEHLER-ZEICHENKETTE",
                  "VERLAUF-ZEICHENKETTE", "SUCHFEHLER-ZEICHENKETTE", "Mandate/Mueller"):
        assert token not in logged, token
