"""PMVP-20 acceptance tests: the assistant service (FEAT-2026-11-19-platform-mvp, Jira SS-732).

FROZEN. Written by the coordinator before the build (plan § 6.2); a builder never edits this file.
Its SHA-256 is in docs/platform-mvp/work-orders/PMVP-20.md and the verifier checks it.

What is tested is the contract of L2-PLATFORM-ASSISTANT (plan § 7):
POST-01 every shown reference points to a retrieved passage; POST-02 no passage enters the prompt
that the person's own search did not return; POST-04 no question, answer or passage text in a
log line; POST-05 no supporting passage -> "Dazu finde ich nichts in Ihren Quellen"; POST-06 a
failed or busy Core gives a plain message and no partial answer. Plus the Core's limits for
/secured/llm/generate (32 messages, roles user/assistant, 50,000 bytes per message, 4,000 bytes of
system instruction, max_tokens <= 2,048).

The service is tested with two fakes: `search(question, limit) -> rows` (rows as
KnovasAPIClient.search_documents returns them, passage text under "snippet") and
`generate(*, system_instruction, messages, max_tokens) -> str`.
"""
from __future__ import annotations

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
]


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


def _prompt_text(call):
    return call["system_instruction"] + "\n" + "\n".join(m["content"] for m in call["messages"])


# --- POST-02: the prompt holds only what the person's search returned ---------------------------


def test_the_question_is_searched_once_as_given():
    search = FakeSearch()
    _service(search, FakeGenerate("Drei Monate [1].")).answer("Welche Kuendigungsfrist gilt?")
    assert len(search.calls) == 1
    assert search.calls[0][0] == "Welche Kuendigungsfrist gilt?"
    assert search.calls[0][1] >= 1


def test_every_retrieved_passage_is_numbered_in_the_prompt():
    generate = FakeGenerate("Drei Monate [1].")
    _service(FakeSearch(), generate).answer("Welche Kuendigungsfrist gilt?")
    assert len(generate.calls) == 1
    prompt = _prompt_text(generate.calls[0])
    for n, row in enumerate(ROWS, start=1):
        assert f"[{n}]" in prompt
        assert row["snippet"] in prompt


def test_no_text_enters_the_prompt_that_search_did_not_return():
    marker = "GEHEIMER-TEXT-AUS-EINEM-ANDEREN-MANDAT"
    generate = FakeGenerate("Drei Monate [1].")
    reply = _service(FakeSearch(), generate).answer("Welche Kuendigungsfrist gilt?")
    assert marker not in _prompt_text(generate.calls[0])
    # the passages the reply offers are exactly the retrieved ones, numbered 1..n in order
    assert [p.number for p in reply.passages] == [1, 2, 3]
    assert [p.text for p in reply.passages] == [r["snippet"] for r in ROWS]
    assert [p.pointer for p in reply.passages] == [r["doc_id"] for r in ROWS]


def test_rows_without_passage_text_are_not_passages():
    rows = [_row(1, "Die Frist betraegt drei Monate."), _row(2, ""), dict(_row(3, "x"), snippet=None)]
    generate = FakeGenerate("Drei Monate [1].")
    reply = _service(FakeSearch(rows), generate).answer("Frist?")
    assert [p.text for p in reply.passages] == ["Die Frist betraegt drei Monate."]
    assert "[2]" not in _prompt_text(generate.calls[0])


# --- POST-01 / W-05: the reference check -----------------------------------------------------------


def test_sentences_with_valid_references_are_kept():
    answer = "Die Kuendigungsfrist betraegt drei Monate [1]. Sie beginnt mit dem Zugang [2]."
    reply = _service(generate=FakeGenerate(answer)).answer("Frist?")
    assert reply.status == "answered"
    assert reply.removed == 0
    assert [s.refs for s in reply.sentences] == [(1,), (2,)]
    assert "drei Monate" in reply.sentences[0].text
    assert all("[" not in s.text for s in reply.sentences)


def test_several_references_on_one_sentence():
    answer = "Frist und Beginn sind geregelt [1][2]. Unterzeichnet wurde 2024 [1, 3]."
    reply = _service(generate=FakeGenerate(answer)).answer("Frist?")
    assert [s.refs for s in reply.sentences] == [(1, 2), (1, 3)]


def test_a_sentence_citing_an_unknown_number_is_removed_and_counted():
    answer = "Die Frist betraegt drei Monate [1]. Das Gericht hat 2019 anders entschieden [9]."
    reply = _service(generate=FakeGenerate(answer)).answer("Frist?")
    assert reply.status == "answered"
    assert [s.refs for s in reply.sentences] == [(1,)]
    assert reply.removed == 1
    assert all("2019" not in s.text for s in reply.sentences)


def test_a_sentence_mixing_a_valid_and_an_unknown_number_is_removed():
    answer = "Die Frist betraegt drei Monate [1]. Sie ist zwingend [2][7]."
    reply = _service(generate=FakeGenerate(answer)).answer("Frist?")
    assert [s.refs for s in reply.sentences] == [(1,)]
    assert reply.removed == 1


def test_a_sentence_without_any_reference_is_removed_and_counted():
    answer = "Im Allgemeinen gelten gesetzliche Fristen. Hier betraegt sie drei Monate [1]."
    reply = _service(generate=FakeGenerate(answer)).answer("Frist?")
    assert [s.refs for s in reply.sentences] == [(1,)]
    assert reply.removed == 1


# --- POST-05: nothing supported -> say so, show the closest passages -------------------------------


def test_nothing_left_after_the_check_gives_the_no_answer_reply():
    reply = _service(generate=FakeGenerate("Das steht im Gesetz [8]. Allgemein gilt das so.")).answer("Frist?")
    assert reply.status == "no_answer"
    assert reply.sentences == []
    assert reply.message == NO_ANSWER_TEXT
    assert [p.number for p in reply.passages] == [1, 2, 3]
    assert reply.removed == 2


def test_no_passages_means_no_generation_and_the_no_answer_reply():
    generate = FakeGenerate("Drei Monate [1].")
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
    generate = FakeGenerate("Drei Monate [1].")
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
    generate = FakeGenerate("Drei Monate [1].")
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
    generate = FakeGenerate("Passage eins [1].")
    reply = _service(FakeSearch(rows), generate).answer("Frist?")
    _assert_within_core_limits(generate.calls[0])
    assert reply.status == "answered"


def test_a_short_history_is_passed_on_in_order():
    history = [Turn(role="user", text="Wer ist die Gegenpartei?"),
               Turn(role="assistant", text="Die Gegenpartei ist die Muster AG.")]
    generate = FakeGenerate("Drei Monate [1].")
    _service(generate=generate).answer("Und die Frist?", history)
    messages = generate.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == "Wer ist die Gegenpartei?"
    assert messages[1]["content"] == "Die Gegenpartei ist die Muster AG."


# --- POST-04: no question, answer or passage text in a log line -------------------------------------


def test_no_customer_text_reaches_a_log_line(caplog):
    question = "FRAGE-ZEICHENKETTE-4711 zur Frist?"
    rows = [_row(1, "PASSAGE-ZEICHENKETTE-0815 drei Monate."), _row(2, "PASSAGE-ZEICHENKETTE-0816")]
    answers = [
        FakeGenerate("ANTWORT-ZEICHENKETTE-42 [1]. ERFUNDEN-ZEICHENKETTE-99 [9]."),
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
    for token in ("FRAGE-ZEICHENKETTE", "PASSAGE-ZEICHENKETTE", "ANTWORT-ZEICHENKETTE",
                  "ERFUNDEN-ZEICHENKETTE", "FEHLER-ZEICHENKETTE", "VERLAUF-ZEICHENKETTE",
                  "SUCHFEHLER-ZEICHENKETTE", "Mandate/Mueller"):
        assert token not in logged, token
