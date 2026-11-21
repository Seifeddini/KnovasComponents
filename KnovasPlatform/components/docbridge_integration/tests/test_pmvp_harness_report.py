"""PMVP-03, the builder's own tests: scoring cases the frozen file leaves open, and what the two report files
show. The automatic verdict's rules are the frozen file's, plus "ok only when really tested" (coordinator,
2026-11-20); what counts as tested and as a reply is in ``test_pmvp_harness_verdict.py``."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pmvp_harness import Question, Report, Rights, Row, run, score, write_report
from pmvp_harness.report import judging_sheet, report_data

VERTRAG = "Mandate/2024-017/Vertrag.pdf"
KLAGE = "Mandate/2024-017/Klage.pdf"
MEMO = "Mandate/2024-055/Memo.docx"
NO_ANSWER = "Dazu finde ich nichts in Ihren Quellen"

FACT = Question(id="q-frist", question="Welche Frist gilt?", kind="fact", expected_points=("drei Monate", "Zugang"),
                supporting_pointers=(VERTRAG,), in_mvp_set=True)
NEGATIVE = Question(id="q-bonus", question="Wie hoch war der Bonus?", kind="negative")
RIGHTS = Question(id="q-recht", question="Worauf stützt sich die Akte?", kind="rights", supporting_pointers=(MEMO,),
                  asked_as="lz")


def passage(number, pointer, page=None):
    return SimpleNamespace(number=number, pointer=pointer, page=page)


def sentence(text, *refs):
    return SimpleNamespace(text=text, refs=tuple(refs))


def reply(status="answered", sentences=(), passages=(), removed=0, message=None, **more):
    return SimpleNamespace(status=status, sentences=list(sentences), passages=list(passages), removed=removed,
                           message=message, **more)


ANSWER = reply(sentences=[sentence("Die Frist beträgt drei Monate.", 1)], passages=[passage(1, VERTRAG, 7)])


# --- scoring ----------------------------------------------------------------------------------------


def test_a_document_that_fills_two_passages_leaks_once():
    r = reply(status="no_answer", passages=[passage(1, MEMO), passage(2, VERTRAG), passage(3, MEMO)],
              message=NO_ANSWER)
    assert list(score(RIGHTS, r).leaked_pointers) == [MEMO]


def test_a_leaked_document_is_named_as_the_truth_files_spell_it():
    r = reply(status="no_answer", passages=[passage(1, "share\\" + MEMO.replace("/", "\\"))], message=NO_ANSWER)
    assert list(score(RIGHTS, r, pointer_prefix="share/").leaked_pointers) == [MEMO]


def test_a_no_answer_reply_that_still_shows_sentences_is_no_no_answer_reply():
    r = reply(status="no_answer", sentences=[sentence("Der Bonus betrug CHF 5'000.", 1)],
              passages=[passage(1, KLAGE)], message=NO_ANSWER)
    assert score(NEGATIVE, r).ok is False


@pytest.mark.parametrize("status", ["busy", "failed"])
def test_a_rights_question_is_not_ok_when_nothing_was_shown(status):
    """The coordinator's decision of 2026-11-20. The frozen rule for rights names the passages only, and a busy or
    failed reply has none: it would pass without anything having been tested."""
    marks = score(RIGHTS, reply(status=status, message="gerade ausgelastet"))
    assert marks.ok is False and marks.status == status
    assert list(marks.leaked_pointers) == []


@pytest.mark.parametrize("status", ["busy", "failed"])
@pytest.mark.parametrize("question", [FACT, NEGATIVE, RIGHTS], ids=lambda question: question.kind)
def test_a_busy_or_failed_reply_is_never_ok_whatever_the_kind(question, status):
    table = Rights([("lz", MEMO, False)])
    marks = score(question, reply(status=status, message="gerade ausgelastet"), rights=table)
    assert marks.ok is False
    assert list(marks.leaked_pointers) == []


def test_a_rights_question_is_ok_when_a_reply_shows_none_of_its_documents():
    nothing_found = reply(status="no_answer", passages=[passage(1, VERTRAG)], message=NO_ANSWER)
    answered = reply(sentences=[sentence("Dazu steht etwas im Vertrag.", 1)], passages=[passage(1, VERTRAG)])
    assert score(RIGHTS, nothing_found).ok is True
    assert score(RIGHTS, answered).ok is True


def test_a_run_against_a_core_that_is_down_scores_nothing():
    summary = run([FACT, NEGATIVE, RIGHTS], lambda question: reply(status="failed", message="...")).summary()
    assert (summary["ok"], summary["leaked"], summary["errors"]) == (0, 0, 0)
    assert summary["by_kind"]["rights"] == {"questions": 1, "ok": 0}
    assert summary["mvp_set"] == {"questions": 1, "ok": 0}
    assert summary["by_status"]["failed"] == 3


def test_a_reply_that_cannot_be_scored_is_an_error_row():
    report = run([FACT], lambda question: SimpleNamespace(status="answered", sentences=7))
    assert report.rows[0].error == "NotAReply" and report.rows[0].score is None
    assert report.summary()["error_classes"] == {"NotAReply": 1}


# --- the summary ------------------------------------------------------------------------------------


def _report():
    replies = {
        FACT.id: ANSWER,
        NEGATIVE.id: reply(status="busy", message="gerade ausgelastet"),
        RIGHTS.id: reply(status="seltsam", passages=[passage(1, MEMO), passage(2, MEMO)], message=NO_ANSWER),
    }
    return run([FACT, NEGATIVE, RIGHTS], lambda question: replies[question.id])


def test_the_summary_keeps_documents_and_questions_apart():
    summary = _report().summary()
    assert (summary["leaked"], summary["leaking_questions"]) == (1, 1)
    assert summary["by_status"] == {"answered": 1, "no_answer": 0, "busy": 1, "failed": 0, "other": 1}
    assert summary["error_classes"] == {}
    assert summary["by_kind"]["document"] == {"questions": 0, "ok": 0}      # every kind, asked or not
    assert summary["mvp_set"] == {"questions": 1, "ok": 1}


def test_an_empty_run_has_a_summary():
    summary = Report([]).summary()
    assert summary["questions"] == 0 and summary["ok"] == 0 and summary["mvp_set"] == {"questions": 0, "ok": 0}


# --- report.json ------------------------------------------------------------------------------------


def test_report_json_rows_hold_counts_and_no_words_of_the_reply():
    data = report_data(_report())
    assert data["format"] == 1
    fact, negative, rights = data["rows"]
    assert fact == {"id": "q-frist", "kind": "fact", "in_mvp_set": True, "ok": True, "error": None,
                    "status": "answered", "sentences": 1, "passages": 1, "refs_shown": 1, "refs_matching": 1,
                    "unreferenced_shown": 0, "source_hit": True, "points_found": [True, False], "leaked": 0,
                    "unknown_pointers": 0, "removed": 0}
    assert negative["status"] == "busy" and negative["ok"] is False and negative["source_hit"] is None
    # A status the contract does not know could be anything, so it is not copied into the report.
    assert rights["status"] == "other" and rights["leaked"] == 1
    assert "seltsam" not in json.dumps(data)


def test_an_error_row_has_its_class_and_no_marks():
    def ask(question):
        raise ConnectionError("https://kanzlei.example/geheim")

    row = report_data(run([FACT], ask))["rows"][0]
    assert row == {"id": "q-frist", "kind": "fact", "in_mvp_set": True, "ok": False, "error": "ConnectionError",
                   "status": None}


# --- judging.md -------------------------------------------------------------------------------------


def test_the_sheet_has_a_line_to_tick_for_every_question():
    sheet = judging_sheet(_report())
    assert sheet.count("**Urteil:** [ ] richtig   [ ] falsch   [ ] unklar") == 3
    assert sheet.count("**Automatische Prüfung: ok**") == 1
    assert sheet.count("**Automatische Prüfung: nicht ok**") == 2


def test_the_sheet_shows_the_answer_with_its_numbers_and_the_passages_with_their_pages():
    sheet = judging_sheet(run([FACT], lambda question: ANSWER))
    assert "1. Die Frist beträgt drei Monate. [1]" in sheet
    assert f"| 1 | `{VERTRAG}` | 7 | zitiert, Belegdokument |" in sheet
    assert "- drei Monate (gefunden)" in sheet and "- Zugang (nicht gefunden)" in sheet
    assert "Verweise: 1 gezeigt, 1 passend" in sheet
    assert "Belegdokument zitiert: ja" in sheet


def test_the_sheet_shows_what_the_check_cut_when_the_reply_carries_the_models_answer():
    raw = "Die Frist beträgt drei Monate [1].\nSie ist zwingend."
    cut = reply(sentences=[sentence("Die Frist beträgt drei Monate.", 1)], passages=[passage(1, VERTRAG)],
                removed=1, raw_answer=raw)
    sheet = judging_sheet(run([FACT], lambda question: cut))
    assert "Ungeprüfte Antwort des Modells" in sheet
    assert "> Die Frist beträgt drei Monate [1].\n> Sie ist zwingend." in sheet
    assert "entfernte Sätze (removed): 1" in sheet
    assert "Ungeprüfte Antwort" not in judging_sheet(run([FACT], lambda question: ANSWER))


def test_the_sheet_marks_a_passage_the_person_must_not_see():
    leaking = reply(status="no_answer", passages=[passage(1, VERTRAG), passage(2, "share/" + MEMO)],
                    message=NO_ANSWER)
    sheet = judging_sheet(run([RIGHTS], lambda question: leaking, pointer_prefix="share/"))
    assert "gefragt als: lz" in sheet
    assert f"| 2 | `share/{MEMO}` |  | **nicht erlaubt** |" in sheet
    assert f"Nicht erlaubte Dokumente unter den Stellen: `{MEMO}`" in sheet
    assert f"> {NO_ANSWER}" in sheet
    assert "Belegdokument zitiert" not in sheet                  # a line for questions that want an answer


def test_the_sheet_marks_a_forbidden_passage_however_the_instance_spells_it():
    shown = "MANDATE\\2024-055\\memo.DOCX "
    leaking = reply(status="no_answer", passages=[passage(1, shown)], message=NO_ANSWER)
    sheet = judging_sheet(run([RIGHTS], lambda question: leaking))
    assert f"| 1 | `{shown.strip()}` |  | **nicht erlaubt** |" in sheet
    assert f"Nicht erlaubte Dokumente unter den Stellen: `{MEMO}`" in sheet        # as the truth files spell it


def test_the_sheet_and_the_report_count_the_passages_the_truth_files_do_not_know():
    reply_ = reply(sentences=[sentence("Die Frist beträgt drei Monate.", 1)],
                   passages=[passage(1, VERTRAG), passage(2, "Mandate/2024-017/Unbekannt.pdf"), passage(3, KLAGE)])
    report = run([FACT], lambda question: reply_)
    assert "Stellen aus Dokumenten, die die Wahrheitsdateien nicht kennen: 2 von 3" in judging_sheet(report)
    data = report_data(report)
    assert data["rows"][0]["unknown_pointers"] == 2
    assert (data["summary"]["unknown_pointers"], data["summary"]["passages"]) == (2, 3)


def test_the_sheet_says_that_a_passage_without_a_document_cannot_be_checked():
    unnamed = reply(status="no_answer", passages=[passage(1, VERTRAG), passage(2, None), passage(3, "  ")],
                    message=NO_ANSWER)
    sheet = judging_sheet(run([RIGHTS], lambda question: unnamed))
    assert "| 2 | (keines genannt) |  | **ohne Dokument, nicht prüfbar** |" in sheet
    assert "| 3 | (keines genannt) |  | **ohne Dokument, nicht prüfbar** |" in sheet
    assert "**Automatische Prüfung: nicht ok**" in sheet


def test_the_sheet_says_when_a_question_got_no_reply():
    def ask(question):
        raise RuntimeError("GEHEIM")

    sheet = judging_sheet(run([FACT], ask))
    assert "Keine Antwort erhalten" in sheet and "RuntimeError" in sheet and "GEHEIM" not in sheet


def test_a_bar_in_a_document_name_does_not_break_the_table():
    odd = reply(sentences=[sentence("Die Frist beträgt drei Monate.", 1)], passages=[passage(1, "Mandate/a|b_c*.pdf")])
    sheet = judging_sheet(run([FACT], lambda question: odd))
    assert "| 1 | `Mandate/a\\|b_c*.pdf` |  | zitiert |" in sheet


def test_both_files_are_written_as_utf8_into_a_new_folder(tmp_path):
    report_path, sheet_path = write_report(run([FACT], lambda question: ANSWER), tmp_path / "a" / "b")
    assert report_path.name == "report.json" and sheet_path.name == "judging.md"
    assert json.loads(report_path.read_text(encoding="utf-8"))["summary"]["ok"] == 1
    assert "beträgt" in sheet_path.read_text(encoding="utf-8")
    assert b"\r\n" not in sheet_path.read_bytes()


def test_a_row_is_ok_only_with_a_score():
    assert Row(question=FACT, error="RuntimeError").ok is False
