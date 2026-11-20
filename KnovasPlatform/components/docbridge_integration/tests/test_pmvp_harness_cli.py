"""PMVP-03, the builder's own tests: the stub truth files and the command line on top of the harness.

    py -3 -m pmvp_harness score --questions <file> --replies <file> [--rights <file>]
                                [--pointer-prefix <text>] [--mvp-set] --out <folder>

The stub files are invented law-firm data in the demo plan's style. They stand in for the plan's truth files
until those are built, and they show every case the harness tells apart: right, wrong, a leak, a missing reply.
"""
from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from pmvp_harness import KINDS, load_questions, load_replies, load_rights
from pmvp_harness.cli import main
from pmvp_harness.files import inside_the_repository, working_tree

TESTS = Path(__file__).resolve().parent
STUBS = TESTS / "fixtures" / "pmvp"
QUESTIONS = STUBS / "questions.stub.jsonl"
RIGHTS = STUBS / "rights.stub.csv"
REPLIES = STUBS / "replies.stub.jsonl"


def _score(tmp_path, *more, questions=QUESTIONS, replies=REPLIES, out="run"):
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(["score", "--questions", str(questions), "--replies", str(replies), "--out", str(tmp_path / out),
                 *more], out=stdout, err=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def _customer_texts():
    """Every text of the stub files a person wrote or a document is called: none of it may leave the sheet."""
    texts = set()
    for line in QUESTIONS.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        texts.add(record["question"])
        texts.update(record["expected_points"])
        texts.update(record["supporting_pointers"])
    for line in REPLIES.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        texts.update(s["text"] for s in record["sentences"])
        texts.update(p["pointer"] for p in record["passages"])
        texts.update(filter(None, [record.get("message"), record.get("raw_answer")]))
    return sorted(text for text in texts if len(text) >= 5)


# --- the stub files -----------------------------------------------------------------------------------


def test_the_stub_questions_cover_every_kind():
    questions = load_questions(QUESTIONS)
    kinds = [q.kind for q in questions]
    assert 10 <= len(questions) <= 15
    assert set(kinds) == set(KINDS)
    assert kinds.count("rights") >= 2 and kinds.count("negative") >= 2
    assert all(q.asked_as for q in questions if q.kind == "rights")
    assert 0 < len(load_questions(QUESTIONS, only_mvp_set=True)) < len(questions)


def test_the_stub_rights_table_forbids_what_the_rights_questions_point_to():
    rights = load_rights(RIGHTS)
    for question in load_questions(QUESTIONS):
        if question.kind == "rights":
            assert rights.leaks(question.asked_as, question.supporting_pointers) == list(question.supporting_pointers)


def test_the_stub_replies_leave_one_question_without_a_reply():
    asked = {q.id for q in load_questions(QUESTIONS)}
    assert len(asked - set(load_replies(REPLIES))) == 1


# --- score ----------------------------------------------------------------------------------------------


def test_the_stubs_score_some_right_some_wrong_one_leak_one_missing(tmp_path):
    code, stdout, stderr = _score(tmp_path, "--rights", str(RIGHTS))
    assert code == 0 and stderr == ""

    report = json.loads((tmp_path / "run" / "report.json").read_text(encoding="utf-8"))
    summary = report["summary"]
    assert (summary["questions"], summary["ok"], summary["errors"]) == (14, 7, 1)
    assert summary["error_classes"] == {"MissingReply": 1}
    assert (summary["refs_shown"], summary["refs_matching"], summary["unreferenced_shown"]) == (9, 8, 1)
    assert (summary["leaked"], summary["leaking_questions"], summary["removed"]) == (1, 1, 3)
    assert summary["by_kind"] == {"fact": {"questions": 5, "ok": 3}, "document": {"questions": 2, "ok": 0},
                                  "process": {"questions": 2, "ok": 1}, "negative": {"questions": 3, "ok": 2},
                                  "rights": {"questions": 2, "ok": 1}}
    assert summary["mvp_set"] == {"questions": 8, "ok": 4}
    rows = {row["id"]: row for row in report["rows"]}
    assert rows["q-2024-055-dok-vollmacht"]["error"] == "MissingReply"
    assert rows["q-2024-055-recht-lz"]["leaked"] == 1 and rows["q-2024-055-recht-lz"]["ok"] is False
    assert rows["q-db-stunden-2024-017"]["ok"] is False           # the answer is only in the practice database


def test_the_summary_is_printed_with_the_plans_target_beside_it(tmp_path):
    code, stdout, _ = _score(tmp_path, "--rights", str(RIGHTS))
    lines = {line.split("  ")[1].strip(): line for line in stdout.splitlines() if line.startswith("  ")}
    assert stdout.splitlines()[0] == "pmvp_harness score"
    assert lines["questions"].split()[-1] == "14"
    assert lines["ok (automatic first pass)"].split()[-1] == "7"
    assert "MissingReply 1" in lines["no reply (errors)"]
    assert "answered 7, no_answer 5, busy 1, failed 0" in lines["statuses"]
    assert "9 / 8" in lines["references shown / matching"]
    assert "fact 3/5, document 0/2, process 1/2, negative 2/3, rights 1/2" in lines["by kind (ok/questions)"]
    assert "4 of 8 ok" in lines["measured set"]
    assert "at least 24 of 30" in lines["measured set"] and "not enforced" in lines["measured set"]
    assert lines["replies not used"].split()[-1] == "1"
    assert stdout.isascii()                                       # a Windows console prints it whatever its code page


def test_a_poor_score_is_still_exit_code_0(tmp_path):
    replies = tmp_path / "none.jsonl"
    replies.write_text("", encoding="utf-8")
    code, stdout, _ = _score(tmp_path, replies=replies)
    assert code == 0
    assert json.loads((tmp_path / "run" / "report.json").read_text(encoding="utf-8"))["summary"]["ok"] == 0


def test_only_the_measured_set(tmp_path):
    code, stdout, _ = _score(tmp_path, "--mvp-set")
    summary = json.loads((tmp_path / "run" / "report.json").read_text(encoding="utf-8"))["summary"]
    assert code == 0 and summary["questions"] == 8 and summary["mvp_set"] == {"questions": 8, "ok": 4}


def test_the_instances_prefix_is_taken_off_before_pointers_are_compared(tmp_path):
    prefixed = tmp_path / "prefixed.jsonl"
    lines = []
    for line in REPLIES.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        for entry in record["passages"]:
            entry["pointer"] = "kanzlei-share\\" + entry["pointer"].replace("/", "\\")
        lines.append(json.dumps(record, ensure_ascii=False))
    prefixed.write_text("\n".join(lines) + "\n", encoding="utf-8")

    _score(tmp_path, "--rights", str(RIGHTS), replies=prefixed, out="without")
    _score(tmp_path, "--rights", str(RIGHTS), "--pointer-prefix", "kanzlei-share/", replies=prefixed, out="with")
    without = json.loads((tmp_path / "without" / "report.json").read_text(encoding="utf-8"))["summary"]
    with_prefix = json.loads((tmp_path / "with" / "report.json").read_text(encoding="utf-8"))["summary"]
    assert (with_prefix["ok"], with_prefix["leaked"]) == (7, 1)
    assert without["leaked"] == 0 and without["ok"] < 7           # nothing matches: neither a source nor a leak


def test_customer_text_is_in_the_judging_sheet_and_nowhere_else(tmp_path, caplog):
    with caplog.at_level(logging.DEBUG):
        code, stdout, stderr = _score(tmp_path, "--rights", str(RIGHTS))
    raw = (tmp_path / "run" / "report.json").read_text(encoding="utf-8")
    sheet = (tmp_path / "run" / "judging.md").read_text(encoding="utf-8")
    logged = "\n".join(record.getMessage() for record in caplog.records)
    texts = _customer_texts()
    assert len(texts) > 40
    asked = {q.id for q in load_questions(QUESTIONS)}
    answered = {key: reply for key, reply in load_replies(REPLIES).items() if key in asked}
    for text in texts:
        assert text not in raw and text not in stdout and text not in stderr and text not in logged
    for question in load_questions(QUESTIONS):
        assert question.question in sheet
    for reply in answered.values():
        for sentence in reply.sentences:
            assert sentence.text in sheet
        for passage in reply.passages:
            assert passage.pointer in sheet
    assert sorted(path.name for path in (tmp_path / "run").iterdir()) == ["judging.md", "report.json"]


# --- files that cannot be used ------------------------------------------------------------------------


def test_a_missing_file_is_exit_code_2_and_named_by_its_role(tmp_path):
    code, stdout, stderr = _score(tmp_path, questions=tmp_path / "Kanzlei Muster" / "fragen.jsonl")
    assert code == 2 and stdout == ""
    assert stderr == "pmvp_harness: questions file: cannot be read (FileNotFoundError)\n"
    assert not (tmp_path / "run").exists()


def test_a_broken_line_is_exit_code_2_with_its_number_and_no_text(tmp_path):
    broken = tmp_path / "replies.jsonl"
    broken.write_text('{"id": "q-begriff-wv", "status": "no_answer"}\n{"id": "q-x", "message": "GEHEIM\n',
                      encoding="utf-8")
    code, _, stderr = _score(tmp_path, replies=broken)
    assert code == 2 and stderr == "pmvp_harness: replies file, line 2: not a JSON record\n"


def test_a_rights_file_with_another_header_is_exit_code_2(tmp_path):
    other = tmp_path / "rights.csv"
    other.write_text("who,what\nlz,x\n", encoding="utf-8")
    code, _, stderr = _score(tmp_path, "--rights", str(other))
    assert code == 2 and "rights file" in stderr and "header" in stderr


def test_the_sheet_is_never_written_into_the_repository(tmp_path):
    inside = STUBS / "out-of-a-test-run"
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(["score", "--questions", str(QUESTIONS), "--replies", str(REPLIES), "--out", str(inside)],
                out=stdout, err=stderr)
    assert code == 2 and "inside this repository" in stderr.getvalue()
    assert not inside.exists()


def test_inside_the_repository_is_decided_by_the_working_tree(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    root = tmp_path / "repo"
    assert working_tree(root / "a" / "b" / "file.py") == root.resolve()
    assert inside_the_repository(root / "out", root=root) and inside_the_repository(root, root=root)
    assert not inside_the_repository(tmp_path / "elsewhere", root=root)
    assert not inside_the_repository(tmp_path / "repo-2" / "out", root=root)


# --- as a module, the way the README says -------------------------------------------------------------


def test_it_runs_as_a_module_from_the_tests_folder(tmp_path):
    done = subprocess.run(
        [sys.executable, "-m", "pmvp_harness", "score", "--questions", str(QUESTIONS), "--replies", str(REPLIES),
         "--rights", str(RIGHTS), "--out", str(tmp_path / "run")],
        cwd=TESTS, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[0] == "pmvp_harness score"
    assert (tmp_path / "run" / "report.json").is_file() and (tmp_path / "run" / "judging.md").is_file()
    for text in _customer_texts():
        assert text not in done.stdout and text not in done.stderr


def test_without_a_command_it_explains_itself():
    with pytest.raises(SystemExit) as stop:
        main([])
    assert stop.value.code == 2
