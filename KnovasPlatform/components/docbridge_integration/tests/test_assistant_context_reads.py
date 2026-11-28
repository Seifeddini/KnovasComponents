"""PMVP-20 context: the reads under the founder's limits, and the passage the model gets and the check reads.

Work order docs/platform-mvp/work-orders/PMVP-20-context.md (founder, 2026-11-22: "Yes, with limits"). The service
with a recording reader (``read_text(document_uuid, offset, timeout)``, as ``assistant.core.make_read_text`` builds it
from the client) and a fake clock: found and widened, found in the second part, not found; each refusal and failure
keeps the snippet and the answer is still produced; only this question's documents are read, each once; the limits
on documents, parts and seconds; the cut, the page and the sentence; the quote check against the context. The same
over the real client and a recording fake Core: test_assistant_context_core.py.
"""
from __future__ import annotations

import json
import logging

import pytest

from assistant import context as ctx
from assistant.errors import CoreBusy, CoreFailed
from assistant.service import NO_ANSWER_TEXT, AssistantService, Passage, Turn, passages_from_rows

DOC_A = "aaaaaaaa-0000-4000-8000-000000000001"
DOC_B = "bbbbbbbb-0000-4000-8000-000000000002"
DOC_C = "cccccccc-0000-4000-8000-000000000003"

SNIPPET_A = "Die Kuendigungsfrist betraegt drei Monate zum Ende eines Kalendermonats."
SNIPPET_B = "Der Mietzins betraegt CHF 2'150 pro Monat."


def _filler(n, word):
    return " ".join(f"{word}{i}" for i in range(n))


#: A document: the hit's chunk in the middle, words around it, and a sentence far away.
FAR = "Die Parteien vereinbaren einen Gerichtsstand in Zuerich fuer alle Streitigkeiten."
TEXT_A = (_filler(150, "vorher") + "\nParagraph 4 Kuendigung. Die Vermieterin kann den Vertrag schriftlich kuendigen. "
          + SNIPPET_A + " Massgebend ist der Zugang beim Empfaenger.\n" + _filler(150, "nachher") + "\n"
          + _filler(600, "weit") + " " + FAR)
TEXT_B = "Paragraph 5 Mietzins. " + SNIPPET_B + " Er ist monatlich im Voraus zahlbar. Nebenkosten sind separat."


def _row(n, snippet, uuid, *, page=4, sentence=9):
    row = {"doc_id": f"Mandate/Mueller/doc-{n}.pdf", "title": f"Dokument {n}", "snippet": snippet,
           "snippet_page_number": page, "snippet_sentence_number": sentence}
    if uuid is not None:
        row["document_uuid"] = uuid
    return row


def st(statement, source, quote):
    return json.dumps({"statement": statement, "source": source, "quote": quote}, ensure_ascii=False)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Reader:
    """The documents' texts by id; serves parts as the Core does (``text_part``) and records every read.

    A value that is an exception is raised instead; ``cost`` seconds pass on the clock per read.
    """

    def __init__(self, texts, *, clock=None, cost=0.0, part=ctx.PART_CHARS):
        self.texts, self.clock, self.cost, self.part = dict(texts), clock, cost, part
        self.calls = []

    def __call__(self, document_uuid, offset, timeout):
        self.calls.append((document_uuid, offset, timeout))
        if self.clock is not None:
            self.clock.now += self.cost
        text = self.texts[document_uuid]
        if isinstance(text, BaseException):
            raise text
        if callable(text):
            return text(offset)
        end = offset + self.part
        return {"text": text[offset:end], "offset": offset, "next_offset": end if end < len(text) else None,
                "total_chars": len(text), "truncated": False}

    def documents(self):
        return [c[0] for c in self.calls]


class Search:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __call__(self, question, limit):
        self.calls.append((question, limit))
        return [dict(r) for r in self.rows]


class Generate:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, *, system_instruction, messages, max_tokens):
        self.calls.append([dict(m) for m in messages])
        return self.answer


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    for name in ("ASSISTANT_CONTEXT_DOCS", "ASSISTANT_CONTEXT_SECONDS", "ASSISTANT_PASSAGE_CHARS"):
        monkeypatch.delenv(name, raising=False)


def _answer(rows, reader, answer, question="Welche Kuendigungsfrist gilt?", history=()):
    generate = Generate(answer)
    reply = AssistantService(search=Search(rows), generate=generate, read_text=reader).answer(question, history)
    return reply, generate


def _passages(rows):
    return passages_from_rows(rows)


# --- found and widened; second part; not found ----------------------------------------------------------------------


def test_the_passage_becomes_the_context_around_the_chunk():
    reader = Reader({DOC_A: TEXT_A})
    (out,) = ctx.widen_passages(_passages([_row(1, SNIPPET_A, DOC_A)]), reader)
    assert SNIPPET_A in out.text and len(out.text) <= 1200 and len(out.text) > 1000
    assert "Die Vermieterin kann den Vertrag schriftlich kuendigen." in out.text
    assert "Massgebend ist der Zugang beim Empfaenger." in out.text
    assert FAR not in out.text
    assert reader.calls == [(DOC_A, 0, pytest.approx(8.0, abs=0.5))]


def test_the_chunk_is_found_in_the_second_part():
    text = _filler(3000, "teil") + " " + SNIPPET_A + " " + _filler(500, "rest")
    assert text.index(SNIPPET_A) > ctx.PART_CHARS
    reader = Reader({DOC_A: text})
    (out,) = ctx.widen_passages(_passages([_row(1, SNIPPET_A, DOC_A)]), reader)
    assert SNIPPET_A in out.text and len(out.text) > 1000
    assert [c[1] for c in reader.calls] == [0, ctx.PART_CHARS]


def test_a_chunk_cut_by_the_part_boundary_is_found_across_it():
    text = _filler(5000, "x")
    at = ctx.PART_CHARS - 20
    text = text[:at] + " " + SNIPPET_A + " " + text[at:]
    (out,) = ctx.widen_passages(_passages([_row(1, SNIPPET_A, DOC_A)]), Reader({DOC_A: text}))
    assert SNIPPET_A in out.text


def test_not_found_keeps_the_snippet_after_at_most_three_parts():
    text = _filler(12000, "nichts")
    reader = Reader({DOC_A: text})
    (out,) = ctx.widen_passages(_passages([_row(1, SNIPPET_A, DOC_A)]), reader)
    assert out.text == SNIPPET_A
    assert [c[1] for c in reader.calls] == [0, 20000, 40000]


def test_a_document_that_is_only_the_chunk_keeps_the_snippet():
    reader = Reader({DOC_A: SNIPPET_A})
    (out,) = ctx.widen_passages(_passages([_row(1, SNIPPET_A, DOC_A)]), reader)
    assert out.text == SNIPPET_A and len(reader.calls) == 1


def test_page_and_sentence_stay_those_of_the_searchs_chunk():
    (out,) = ctx.widen_passages(_passages([_row(1, SNIPPET_A, DOC_A, page=7, sentence=31)]),
                                Reader({DOC_A: TEXT_A}))
    assert (out.number, out.pointer, out.title, out.page, out.sentence, out.document_uuid) == (
        1, "Mandate/Mueller/doc-1.pdf", "Dokument 1", 7, 31, DOC_A)


def test_assistant_passage_chars_sets_the_contexts_length(monkeypatch):
    monkeypatch.setenv("ASSISTANT_PASSAGE_CHARS", "400")
    (out,) = ctx.widen_passages(_passages([_row(1, SNIPPET_A, DOC_A)]), Reader({DOC_A: TEXT_A}))
    assert SNIPPET_A in out.text and 380 <= len(out.text) <= 400


# --- the Core decides every read ------------------------------------------------------------------------------------


FAILURES = [
    CoreFailed("http_error", status=404), CoreFailed("http_error", status=401),
    CoreFailed("http_error", status=403), CoreFailed("http_error", status=422),
    CoreBusy(status=429), CoreFailed("http_error", status=500), CoreFailed("http_error", status=502),
    CoreBusy(status=503), CoreFailed("timeout"), CoreFailed("transport"), CoreFailed("malformed"),
    CoreFailed("no_principal"), RuntimeError("boom"), AssertionError("x"),
]
REASON_OF = ["not_visible", "refused", "refused", "refused", "busy", "core_error", "core_error", "busy", "timeout",
             "transport", "malformed", "refused", "error", "error"]


@pytest.mark.parametrize("failure, reason", list(zip(FAILURES, REASON_OF)), ids=[r for r in REASON_OF])
def test_each_refusal_and_failure_keeps_the_snippet_and_the_answer_is_produced(failure, reason, caplog):
    reader = Reader({DOC_A: failure, DOC_B: TEXT_B})
    rows = [_row(1, SNIPPET_A, DOC_A), _row(2, SNIPPET_B, DOC_B)]
    answer = st("Die Kuendigungsfrist betraegt drei Monate.", 1, "Kuendigungsfrist betraegt drei Monate")
    with caplog.at_level(logging.INFO, logger="assistant"):
        reply, generate = _answer(rows, reader, answer)
    assert reply.status == "answered"
    assert [s.quote for s in reply.sentences] == ["Kuendigungsfrist betraegt drei Monate"]
    assert reply.passages[0].text == SNIPPET_A
    (line,) = [r.getMessage() for r in caplog.records if r.name == "assistant.context"]
    assert f"{reason}=1" in line.split()
    stops = reason == "busy" or isinstance(failure, CoreFailed) and (failure.status == 401
                                                                    or failure.reason == "no_principal")
    if stops:   # the Core would refuse every document of this question alike: no second read
        assert reader.documents() == [DOC_A] and reply.passages[1].text == SNIPPET_B
        assert "stopped=1" in line.split()
    else:
        assert reader.documents() == [DOC_A, DOC_B] and "Er ist monatlich im Voraus zahlbar." in reply.passages[1].text


@pytest.mark.parametrize("part", [None, "text", {"text": 5}, {"text": "x", "offset": 3},
                                  {"text": "x", "next_offset": 0}, {"text": "x", "next_offset": "20000"},
                                  {"text": "x", "next_offset": True}, {"text": "y" * 20001}])
def test_a_malformed_part_keeps_the_snippet(part):
    reader = Reader({DOC_A: lambda offset: part})
    (out,) = ctx.widen_passages(_passages([_row(1, SNIPPET_A, DOC_A)]), reader)
    assert out.text == SNIPPET_A and len(reader.calls) == 1


def test_a_failed_later_part_keeps_what_the_first_part_found():
    second = _filler(3000, "teil") + " " + "Der Vermieter haftet nur fuer grobe Fahrlaessigkeit und Vorsatz bei Schaeden."
    full = TEXT_B + " " + second

    def serve(offset):
        if offset:
            raise CoreFailed("timeout")
        return {"text": full[:ctx.PART_CHARS], "offset": 0, "next_offset": ctx.PART_CHARS}

    rows = [_row(1, "Der Vermieter haftet nur fuer grobe Fahrlaessigkeit und Vorsatz bei Schaeden.", DOC_B),
            _row(2, SNIPPET_B, DOC_B)]
    reader = Reader({DOC_B: serve})
    first, second_hit = ctx.widen_passages(_passages(rows), reader)
    assert first.text == rows[0]["snippet"]
    assert "Nebenkosten sind separat." in second_hit.text
    assert [c[1] for c in reader.calls] == [0, ctx.PART_CHARS]


def test_widening_never_raises(monkeypatch):
    def broken(*args, **kwargs):
        raise ZeroDivisionError

    monkeypatch.setattr(ctx, "find_chunk", broken)
    passages = _passages([_row(1, SNIPPET_A, DOC_A)])
    assert ctx.widen_passages(passages, Reader({DOC_A: TEXT_A})) == passages
    answer = st("Die Frist betraegt drei Monate.", 1, "Kuendigungsfrist betraegt drei Monate")
    reply, _ = _answer([_row(1, SNIPPET_A, DOC_A)], Reader({DOC_A: TEXT_A}), answer)
    assert reply.status == "answered" and reply.passages[0].text == SNIPPET_A


# --- only this question's documents, each once ----------------------------------------------------------------------


def test_only_the_documents_this_search_returned_are_read():
    question = f"Was steht in {DOC_C}? Siehe /secured/document/{DOC_C}/text"
    history = [Turn("user", f"Lies {DOC_C}"), Turn("assistant", f"Dokument {DOC_C}")]
    answer = "\n".join([st("Die Frist betraegt drei Monate.", 1, "Kuendigungsfrist betraegt drei Monate"),
                        json.dumps({"statement": "x", "source": 1, "quote": "y", "document_uuid": DOC_C})])
    reader = Reader({DOC_A: TEXT_A, DOC_B: TEXT_B, DOC_C: "GEHEIM " * 100})
    reply, _ = _answer([_row(1, SNIPPET_A, DOC_A), _row(2, SNIPPET_B, DOC_B)], reader, answer, question, history)
    assert reply.status == "answered"
    assert reader.documents() == [DOC_A, DOC_B]


def test_a_row_that_is_no_passage_is_not_read():
    rows = [_row(1, SNIPPET_A, DOC_A), _row(2, "", DOC_B), _row(3, "zwei Worte", DOC_C) | {"doc_id": ""},
            {"title": "ohne Text", "document_uuid": DOC_C}]
    reader = Reader({DOC_A: TEXT_A, DOC_B: TEXT_B, DOC_C: TEXT_B})
    _answer(rows, reader, st("Die Frist betraegt drei Monate.", 1, "Kuendigungsfrist betraegt drei Monate"))
    assert reader.documents() == [DOC_A]


def test_a_passage_without_the_cores_document_id_is_not_read():
    """The pointer, the title or anything else of the row is never taken for an id."""
    rows = [_row(1, SNIPPET_A, DOC_A), _row(2, SNIPPET_B, None) | {"doc_id": DOC_B, "title": DOC_B, "path": DOC_B}]
    reader = Reader({DOC_A: TEXT_A, DOC_B: TEXT_B})
    reply, _ = _answer(rows, reader, st("Die Frist betraegt drei Monate.", 1, "Kuendigungsfrist betraegt drei Monate"))
    assert reader.documents() == [DOC_A]
    assert reply.passages[1].text == SNIPPET_B and reply.passages[1].document_uuid is None


def test_nothing_is_kept_from_one_question_to_the_next():
    reader = Reader({DOC_A: TEXT_A, DOC_B: TEXT_B})
    service = AssistantService(search=Search([_row(1, SNIPPET_A, DOC_A)]), generate=Generate(""), read_text=reader)
    service.answer("Erste Frage?")
    service._search = Search([_row(1, SNIPPET_B, DOC_B)])
    service.answer("Zweite Frage?")
    service.answer("Zweite Frage nochmals?")
    assert reader.documents() == [DOC_A, DOC_B, DOC_B]


def test_two_hits_in_one_document_share_one_reading():
    second = "Die Vermieterin kann den Vertrag schriftlich kuendigen."
    rows = [_row(1, SNIPPET_A, DOC_A), _row(2, second, DOC_A, page=2, sentence=3), _row(3, SNIPPET_B, DOC_B)]
    reader = Reader({DOC_A: TEXT_A, DOC_B: TEXT_B})
    one, two, three = ctx.widen_passages(_passages(rows), reader)
    assert reader.calls == [(DOC_A, 0, pytest.approx(8.0, abs=0.5)), (DOC_B, 0, pytest.approx(8.0, abs=0.5))]
    assert SNIPPET_A in one.text and second in two.text and (two.page, two.sentence) == (2, 3)


def test_without_a_reader_nothing_is_read_and_the_snippet_is_the_passage():
    generate = Generate(st("Die Frist betraegt drei Monate.", 1, "Kuendigungsfrist betraegt drei Monate"))
    reply = AssistantService(search=Search([_row(1, SNIPPET_A, DOC_A)]), generate=generate).answer("Frist?")
    assert reply.passages[0].text == SNIPPET_A


def test_no_passages_means_no_read():
    reader = Reader({DOC_A: TEXT_A})
    reply, generate = _answer([{"doc_id": "x.pdf", "document_uuid": DOC_A}], reader, "")
    assert reply.status == "no_answer" and reply.message == NO_ANSWER_TEXT
    # PMVP-20 conversation (the coordinator's re-pin): with no passage the model is asked once, for a reply only;
    # still nothing is read
    assert reader.calls == [] and len(generate.calls) <= 1


# --- bounded --------------------------------------------------------------------------------------------------------


def _ten_documents():
    ids = [f"{i:08d}-0000-4000-8000-000000000000" for i in range(10)]
    texts = {d: f"Vorher steht hier etwas. Satz Nummer {i} steht hier im Dokument. Nachher folgt mehr Text."
             for i, d in enumerate(ids)}
    rows = [_row(i, f"Satz Nummer {i} steht hier im Dokument.", d) for i, d in enumerate(ids)]
    return ids, texts, rows


def test_at_most_eight_documents_are_read_the_lowest_ranked_keep_their_snippets():
    ids, texts, rows = _ten_documents()
    reader = Reader(texts)
    out = ctx.widen_passages(_passages(rows), reader)
    assert reader.documents() == ids[:8]
    assert all(p.text.startswith("Vorher") for p in out[:8])
    assert [p.text for p in out[8:]] == [r["snippet"] for r in rows[8:]]


@pytest.mark.parametrize("value, read", [("2", 2), ("0", 0), ("8", 8), ("50", 8)])
def test_assistant_context_docs_sets_how_many(monkeypatch, value, read):
    ids, texts, rows = _ten_documents()
    monkeypatch.setenv("ASSISTANT_CONTEXT_DOCS", value)
    reader = Reader(texts)
    ctx.widen_passages(_passages(rows), reader)
    assert reader.documents() == ids[:read]


def test_the_time_budget_stops_further_reads_and_bounds_each_read():
    ids, texts, rows = _ten_documents()
    clock = Clock()
    reader = Reader(texts, clock=clock, cost=3.0)
    out = ctx.widen_passages(_passages(rows), reader, clock=clock)
    # 8 s: reads start with 8, 5 and 2 s left; the fourth would start with -1 s
    assert [round(c[2], 3) for c in reader.calls] == [8.0, 5.0, 2.0]
    assert all(p.text.startswith("Vorher") for p in out[:3])
    assert [p.text for p in out[3:]] == [r["snippet"] for r in rows[3:]]


def test_assistant_context_seconds_sets_the_budget(monkeypatch):
    ids, texts, rows = _ten_documents()
    monkeypatch.setenv("ASSISTANT_CONTEXT_SECONDS", "4")
    clock = Clock()
    reader = Reader(texts, clock=clock, cost=1.5)
    ctx.widen_passages(_passages(rows), reader, clock=clock)
    assert [round(c[2], 3) for c in reader.calls] == [4.0, 2.5, 1.0]


def test_a_read_is_not_started_with_almost_no_time_left():
    ids, texts, rows = _ten_documents()
    clock = Clock()
    reader = Reader(texts, clock=clock, cost=7.9)
    ctx.widen_passages(_passages(rows), reader, clock=clock)
    assert len(reader.calls) == 1


def test_reads_are_one_at_a_time_in_search_order():
    ids, texts, rows = _ten_documents()
    reader = Reader(texts)
    ctx.widen_passages(_passages(list(reversed(rows))), reader)
    assert reader.documents() == list(reversed(ids))[:8]


# --- the passage the model gets and the check reads -----------------------------------------------------------------


def test_the_prompt_holds_the_context():
    reply, generate = _answer([_row(1, SNIPPET_A, DOC_A)], Reader({DOC_A: TEXT_A}),
                              st("Die Frist betraegt drei Monate.", 1, "Kuendigungsfrist betraegt drei Monate"))
    prompt = generate.calls[0][-1]["content"]
    assert "[1] Dokument 1, page 4\n" in prompt
    assert reply.passages[0].text in prompt and "Massgebend ist der Zugang beim Empfaenger." in prompt


def test_a_quote_from_the_context_beyond_the_snippet_is_shown():
    answer = st("Massgebend ist der Zugang.", 1, "Massgebend ist der Zugang beim Empfaenger")
    reply, _ = _answer([_row(1, SNIPPET_A, DOC_A)], Reader({DOC_A: TEXT_A}), answer)
    assert reply.status == "answered"
    assert [(s.text, s.quote) for s in reply.sentences] == [
        ("Massgebend ist der Zugang.", "Massgebend ist der Zugang beim Empfaenger")]


def test_a_quote_from_outside_the_context_is_removed():
    answer = st("Der Gerichtsstand ist Zuerich.", 1, "vereinbaren einen Gerichtsstand in Zuerich")
    reply, _ = _answer([_row(1, SNIPPET_A, DOC_A)], Reader({DOC_A: TEXT_A}), answer)
    assert reply.status == "no_answer" and reply.removed_by["quote_not_found"] == 1
    assert FAR in TEXT_A and FAR not in reply.passages[0].text


def test_a_number_is_checked_against_the_context():
    text = "Paragraph 9. Die Kaution betraegt drei Monatsmieten. Sie ist bis zum 30. April 2025 zu leisten. Ende."
    rows = [_row(1, "Die Kaution betraegt drei Monatsmieten.", DOC_A)]
    shown = st("Die Kaution ist bis zum 30. April 2025 zu leisten.", 1, "Sie ist bis zum 30. April 2025")
    reply, _ = _answer(rows, Reader({DOC_A: text}), shown)
    assert reply.status == "answered"
    wrong = st("Die Kaution ist bis zum 31. Mai 2025 zu leisten.", 1, "Die Kaution betraegt drei Monatsmieten")
    reply, _ = _answer(rows, Reader({DOC_A: text}), wrong)
    assert reply.removed_by["number_not_found"] == 1


def test_the_passage_the_page_keeps_is_the_context():
    reply, _ = _answer([_row(1, SNIPPET_A, DOC_A)], Reader({DOC_A: TEXT_A}),
                       st("Die Frist betraegt drei Monate.", 1, "Kuendigungsfrist betraegt drei Monate"))
    (passage,) = reply.passages
    assert isinstance(passage, Passage) and passage.text != SNIPPET_A and SNIPPET_A in passage.text


def test_the_service_refuses_a_reader_that_is_not_callable():
    with pytest.raises(TypeError):
        AssistantService(search=Search([]), generate=Generate(""), read_text="GET /secured/document")
