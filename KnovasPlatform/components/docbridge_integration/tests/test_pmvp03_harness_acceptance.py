"""PMVP-03 acceptance tests: the acceptance harness reads the demo plan's truth files and scores the
assistant's replies (FEAT-2026-11-19-platform-mvp section 11, Jira SS-727).

FROZEN. Written by the coordinator before the build; a builder never edits this file. Its git blob id is in
docs/platform-mvp/frozen-tests.txt.

What is pinned: the package `pmvp_harness` in this tests folder (test tooling, never part of the image) with

    load_questions(path, *, only_mvp_set=False) -> list of questions
    load_rights(path)                           -> rights table (.may_read, .leaks)
    score(question, reply, *, rights=None, pointer_prefix="") -> score of one reply
    run(questions, ask, *, rights=None, pointer_prefix="")    -> report (.rows, .summary())
    write_report(report, dest)                  -> report.json (no customer text) and judging.md (for the judges)

The file formats are the demo law firm plan's (FEAT-2026-11-19-demo-law-firm, its generator's truth.py): one JSON
record per line with id, question, kind, expected_points, supporting_pointers, asked_as, in_mvp_set; and a CSV with
the header person,pointer,may_read,rule. A reply is whatever the assistant returns: .status, .sentences (each
.text and .refs), .passages (each .number and .pointer), .removed, .message (see the PMVP-20 acceptance tests).

The automatic verdict `ok` is a first pass, people judge whether an answer is right (plan section 11):
  fact, document, process: answered, every shown sentence has references that match passages, and at least one
                           referenced passage is one of the question's supporting documents
  negative:                the reply is the no-answer reply
  rights:                  none of the question's supporting documents is among the passages (shown or not cited)
and in every kind: no shown reference without a passage, no shown sentence without a reference, and no passage
the rights table forbids to the person the question is asked as.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from pmvp_harness import load_questions, load_rights, run, score, write_report

VERTRAG = "Mandate/2024-017/Vertrag.pdf"
KLAGE = "Mandate/2024-017/Klage.pdf"
WEISUNG = "Kanzlei/Weisung 1.docx"
MEMO = "Mandate/2024-055/Memo.docx"
LOHN = "Personal/Lohnliste.xlsx"

RECORDS = [
    {"id": "q-2024-017-frist", "question": "Welche Frist ist in der Akte 2024-017 vermerkt?", "kind": "fact",
     "expected_points": ["drei Monate"], "supporting_pointers": [VERTRAG], "asked_as": "", "in_mvp_set": True},
    {"id": "q-2024-017-dok-klage", "question": "Zeig mir die Klage in Sachen Meierhans.", "kind": "document",
     "expected_points": ["Klage in der Akte 2024-017"], "supporting_pointers": [KLAGE], "asked_as": "",
     "in_mvp_set": True},
    {"id": "q-prozess-mandatsannahme", "question": "Welche Schritte schreibt unsere Weisung zuerst vor?",
     "kind": "process", "expected_points": ["Konfliktpruefung", "Vollmacht einholen"],
     "supporting_pointers": [WEISUNG], "asked_as": "", "in_mvp_set": False},
    {"id": "q-ohne-antwort-bonus", "question": "Wie hoch war der Bonus von Frau Steiner?", "kind": "negative",
     "expected_points": [], "supporting_pointers": [], "asked_as": "", "in_mvp_set": True},
    {"id": "q-2024-055-recht-lz", "question": "Auf welche Bestimmungen stuetzt sich die Akte 2024-055?",
     "kind": "rights", "expected_points": [], "supporting_pointers": [MEMO], "asked_as": "lz", "in_mvp_set": True},
]

RIGHTS_CSV = (
    "person,pointer,may_read,rule\n"
    f"lz,{MEMO},false,information barrier\n"
    f"mb,{MEMO},true,partner\n"
    f"lz,{VERTRAG},true,team\n"
)


def _write_questions(tmp_path, records=RECORDS, name="questions.jsonl"):
    path = tmp_path / name
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    return path


@pytest.fixture
def questions(tmp_path):
    return {q.id: q for q in load_questions(_write_questions(tmp_path))}


@pytest.fixture
def rights(tmp_path):
    path = tmp_path / "rights.csv"
    path.write_text(RIGHTS_CSV, encoding="utf-8")
    return load_rights(path)


def passage(number, pointer, text="Text der Stelle."):
    return SimpleNamespace(number=number, pointer=pointer, text=text, page=1, title="")


def sentence(text, *refs):
    return SimpleNamespace(text=text, refs=tuple(refs))


def reply(status="answered", sentences=(), passages=(), removed=0, message=None):
    return SimpleNamespace(status=status, sentences=list(sentences), passages=list(passages), removed=removed,
                           message=message)


NO_ANSWER = "Dazu finde ich nichts in Ihren Quellen"


# --- the question file ----------------------------------------------------------------------------


def test_the_demo_plans_records_are_read(tmp_path):
    records = [dict(r) for r in RECORDS]
    records[1]["a_later_field"] = "ignored"
    path = tmp_path / "questions.jsonl"
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    path.write_text("\n".join(lines[:2]) + "\n\n" + "\n".join(lines[2:]) + "\n", encoding="utf-8")

    loaded = load_questions(path)

    assert [q.id for q in loaded] == [r["id"] for r in RECORDS]
    first, last = loaded[0], loaded[-1]
    assert first.question == "Welche Frist ist in der Akte 2024-017 vermerkt?"
    assert first.kind == "fact"
    assert list(first.expected_points) == ["drei Monate"]
    assert list(first.supporting_pointers) == [VERTRAG]
    assert first.asked_as == ""
    assert first.in_mvp_set is True
    assert last.kind == "rights" and last.asked_as == "lz"
    assert loaded[2].in_mvp_set is False


def test_only_the_set_the_plan_measures_on(tmp_path):
    loaded = load_questions(_write_questions(tmp_path), only_mvp_set=True)
    assert [q.id for q in loaded] == [r["id"] for r in RECORDS if r["in_mvp_set"]]


def test_a_record_without_its_question_names_the_line_and_no_text(tmp_path):
    records = [dict(r) for r in RECORDS]
    del records[2]["question"]
    records[2]["expected_points"] = ["GEHEIM"]
    with pytest.raises(ValueError) as err:
        load_questions(_write_questions(tmp_path, records))
    assert "3" in str(err.value)
    assert "GEHEIM" not in str(err.value)


def test_a_line_that_is_not_json_names_the_line_and_no_text(tmp_path):
    path = _write_questions(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[3] = '{"id": "q-kaputt", "question": "GEHEIM'
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError) as err:
        load_questions(path)
    assert "4" in str(err.value)
    assert "GEHEIM" not in str(err.value)


def test_an_unknown_kind_is_refused(tmp_path):
    records = [dict(r) for r in RECORDS]
    records[0]["kind"] = "opinion"
    with pytest.raises(ValueError):
        load_questions(_write_questions(tmp_path, records))


# --- the rights table ------------------------------------------------------------------------------


def test_the_rights_table_answers_per_person_and_document(rights):
    assert rights.may_read("lz", MEMO) is False
    assert rights.may_read("mb", MEMO) is True
    assert rights.may_read("lz", VERTRAG) is True
    assert rights.may_read("lz", "Mandate/unbekannt.pdf") is None


def test_leaks_are_the_documents_the_table_forbids(rights):
    assert list(rights.leaks("lz", [MEMO, VERTRAG, "Mandate/unbekannt.pdf"])) == [MEMO]
    assert list(rights.leaks("mb", [MEMO, VERTRAG])) == []


def test_a_rights_file_with_another_header_is_refused(tmp_path):
    path = tmp_path / "rights.csv"
    path.write_text("who,what,ok\nlz,x.pdf,false\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_rights(path)


# --- scoring one reply -----------------------------------------------------------------------------


def test_an_answer_from_the_right_document_is_ok(questions):
    r = reply(sentences=[sentence("Die Frist betraegt DREI  Monate.", 1)],
              passages=[passage(1, VERTRAG), passage(2, KLAGE)], removed=2)
    s = score(questions["q-2024-017-frist"], r)
    assert s.ok is True
    assert s.status == "answered"
    assert (s.refs_shown, s.refs_matching) == (1, 1)
    assert s.unreferenced_shown == 0
    assert s.source_hit is True
    assert list(s.points_found) == [True]        # ignoring case and the amount of white space
    assert list(s.leaked_pointers) == []
    assert s.removed == 2


def test_an_answer_from_another_document_is_not_ok(questions):
    r = reply(sentences=[sentence("Die Frist betraegt drei Monate.", 2)],
              passages=[passage(1, VERTRAG), passage(2, KLAGE)])
    s = score(questions["q-2024-017-frist"], r)
    assert s.source_hit is False
    assert s.ok is False


def test_expected_points_are_a_hint_for_the_judges_not_a_verdict(questions):
    r = reply(sentences=[sentence("Die Frist betraegt ein Quartal.", 1)], passages=[passage(1, VERTRAG)])
    s = score(questions["q-2024-017-frist"], r)
    assert list(s.points_found) == [False]
    assert s.ok is True


def test_each_expected_point_is_looked_up_on_its_own(questions):
    r = reply(sentences=[sentence("Zuerst kommt die Konfliktpruefung.", 1)], passages=[passage(1, WEISUNG)])
    s = score(questions["q-prozess-mandatsannahme"], r)
    assert list(s.points_found) == [True, False]


def test_a_reference_that_matches_no_passage_fails(questions):
    r = reply(sentences=[sentence("Die Frist betraegt drei Monate.", 1, 4)],
              passages=[passage(1, VERTRAG), passage(2, KLAGE)])
    s = score(questions["q-2024-017-frist"], r)
    assert (s.refs_shown, s.refs_matching) == (2, 1)
    assert s.ok is False


def test_a_shown_sentence_without_a_reference_fails(questions):
    r = reply(sentences=[sentence("Die Frist betraegt drei Monate.", 1), sentence("Sie wurde verlaengert.")],
              passages=[passage(1, VERTRAG)])
    s = score(questions["q-2024-017-frist"], r)
    assert s.unreferenced_shown == 1
    assert s.ok is False


@pytest.mark.parametrize("status", ["no_answer", "busy", "failed"])
def test_no_answer_to_a_question_that_has_one_is_not_ok(questions, status):
    r = reply(status=status, passages=[passage(1, VERTRAG)] if status == "no_answer" else [], message=NO_ANSWER)
    s = score(questions["q-2024-017-frist"], r)
    assert s.status == status
    assert s.refs_shown == 0
    assert s.source_hit is False
    assert s.ok is False


def test_a_negative_question_wants_the_no_answer_reply(questions):
    q = questions["q-ohne-antwort-bonus"]
    said_so = score(q, reply(status="no_answer", passages=[passage(1, LOHN)], message=NO_ANSWER))
    assert said_so.ok is True
    assert said_so.source_hit is None            # there is no supporting document to hit

    answered = score(q, reply(sentences=[sentence("Der Bonus betrug CHF 5'000.", 1)], passages=[passage(1, LOHN)]))
    assert answered.ok is False

    busy = score(q, reply(status="busy", message="gerade ausgelastet"))
    assert busy.ok is False


def test_a_rights_question_leaks_through_the_closest_passages_too(questions):
    q = questions["q-2024-055-recht-lz"]
    r = reply(status="no_answer", passages=[passage(1, VERTRAG), passage(2, MEMO)], message=NO_ANSWER)
    s = score(q, r)
    assert list(s.leaked_pointers) == [MEMO]
    assert s.ok is False


def test_a_rights_question_without_the_forbidden_document_is_ok(questions):
    q = questions["q-2024-055-recht-lz"]
    s = score(q, reply(status="no_answer", passages=[passage(1, VERTRAG)], message=NO_ANSWER))
    assert list(s.leaked_pointers) == []
    assert s.ok is True


def test_the_rights_table_catches_a_forbidden_passage_on_any_question(tmp_path, rights):
    record = dict(RECORDS[0], id="q-2024-017-frist-lz", asked_as="lz")
    q = load_questions(_write_questions(tmp_path, [record], name="one.jsonl"))[0]
    r = reply(sentences=[sentence("Die Frist betraegt drei Monate.", 1)],
              passages=[passage(1, VERTRAG), passage(2, MEMO)])

    with_table = score(q, r, rights=rights)
    assert list(with_table.leaked_pointers) == [MEMO]
    assert with_table.ok is False

    without_table = score(q, r)
    assert list(without_table.leaked_pointers) == []
    assert without_table.ok is True


def test_the_rights_table_is_not_applied_to_a_question_asked_as_nobody(questions, rights):
    r = reply(sentences=[sentence("Die Frist betraegt drei Monate.", 1)],
              passages=[passage(1, VERTRAG), passage(2, MEMO)])
    s = score(questions["q-2024-017-frist"], r, rights=rights)
    assert list(s.leaked_pointers) == []
    assert s.ok is True


def test_pointers_are_compared_without_the_instances_prefix_and_with_either_slash(questions):
    q = questions["q-2024-017-frist"]
    answer = [sentence("Die Frist betraegt drei Monate.", 1)]

    prefixed = reply(sentences=answer, passages=[passage(1, "kanzlei-share/" + VERTRAG)])
    assert score(q, prefixed).source_hit is False
    assert score(q, prefixed, pointer_prefix="kanzlei-share/").source_hit is True

    backslashes = reply(sentences=answer, passages=[passage(1, VERTRAG.replace("/", "\\"))])
    assert score(q, backslashes).source_hit is True


# --- a whole run -------------------------------------------------------------------------------------


def _canned(question):
    if question.kind == "fact":
        return reply(sentences=[sentence("Die Frist betraegt drei Monate.", 1)], passages=[passage(1, VERTRAG)],
                     removed=2)
    if question.kind == "document":
        return reply(sentences=[sentence("Die Klage liegt in der Akte.", 1)], passages=[passage(1, KLAGE)])
    if question.kind == "process":
        raise RuntimeError("GEHEIM im Fehlertext")
    if question.kind == "negative":
        return reply(sentences=[sentence("Der Bonus betrug CHF 5'000.", 1)], passages=[passage(1, LOHN)])
    return reply(status="no_answer", passages=[passage(1, MEMO)], message=NO_ANSWER)


@pytest.fixture
def report(tmp_path, rights):
    return run(load_questions(_write_questions(tmp_path)), _canned, rights=rights)


def test_every_question_is_asked_once_in_order(tmp_path):
    asked = []

    def ask(question):
        asked.append(question.id)
        return reply(status="no_answer", message=NO_ANSWER)

    result = run(load_questions(_write_questions(tmp_path)), ask)
    assert asked == [r["id"] for r in RECORDS]
    assert [row.question.id for row in result.rows] == asked


def test_a_question_that_raises_is_an_error_row_and_the_run_goes_on(report):
    rows = {row.question.id: row for row in report.rows}
    broken = rows["q-prozess-mandatsannahme"]
    assert broken.error == "RuntimeError"        # the class name, never the message
    assert broken.reply is None and broken.score is None
    assert len(report.rows) == len(RECORDS)
    assert rows["q-2024-017-frist"].error is None
    assert rows["q-2024-017-frist"].score.ok is True
    assert rows["q-2024-055-recht-lz"].score.ok is False


def test_the_summary_counts_what_the_plan_measures(report):
    summary = report.summary()
    assert summary["questions"] == 5
    assert summary["ok"] == 2
    assert summary["errors"] == 1
    assert (summary["refs_shown"], summary["refs_matching"]) == (3, 3)
    assert summary["unreferenced_shown"] == 0
    assert summary["leaked"] == 1
    assert summary["removed"] == 2
    assert summary["by_kind"] == {
        "fact": {"questions": 1, "ok": 1},
        "document": {"questions": 1, "ok": 1},
        "process": {"questions": 1, "ok": 0},
        "negative": {"questions": 1, "ok": 0},
        "rights": {"questions": 1, "ok": 0},
    }
    assert summary["mvp_set"] == {"questions": 4, "ok": 2}


# --- the two report files ----------------------------------------------------------------------------

TEXTS = [r["question"] for r in RECORDS] + [
    "Die Frist betraegt drei Monate.", "Der Bonus betrug", VERTRAG, KLAGE, MEMO, LOHN, "GEHEIM",
]


def test_report_json_carries_numbers_and_ids_and_no_customer_text(report, tmp_path):
    dest = tmp_path / "out" / "run-1"
    write_report(report, dest)

    raw = (dest / "report.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["summary"] == json.loads(json.dumps(report.summary()))
    assert [row["id"] for row in data["rows"]] == [r["id"] for r in RECORDS]
    by_id = {row["id"]: row for row in data["rows"]}
    assert by_id["q-2024-017-frist"]["ok"] is True
    assert by_id["q-2024-055-recht-lz"]["ok"] is False
    assert by_id["q-prozess-mandatsannahme"]["error"] == "RuntimeError"
    for text in TEXTS:
        assert text not in raw


def test_the_judging_sheet_shows_question_answer_and_passages(report, tmp_path):
    dest = tmp_path / "out"
    write_report(report, dest)

    sheet = (dest / "judging.md").read_text(encoding="utf-8")
    for record in RECORDS:
        assert record["id"] in sheet
        assert record["question"] in sheet
    assert "Die Frist betraegt drei Monate." in sheet
    assert VERTRAG in sheet and MEMO in sheet
    assert NO_ANSWER in sheet
    assert "GEHEIM" not in sheet                 # an exception's message is nobody's business


def test_no_customer_text_reaches_a_log_line(tmp_path, rights, caplog):
    with caplog.at_level(logging.DEBUG):
        result = run(load_questions(_write_questions(tmp_path)), _canned, rights=rights)
        write_report(result, tmp_path / "out")
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for text in TEXTS:
        assert text not in logged
