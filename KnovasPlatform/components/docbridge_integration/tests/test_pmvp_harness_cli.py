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
from pmvp_harness.files import inside_a_work_tree

TESTS = Path(__file__).resolve().parent
STUBS = TESTS / "fixtures" / "pmvp"
QUESTIONS = STUBS / "questions.stub.jsonl"
RIGHTS = STUBS / "rights.stub.csv"
REPLIES = STUBS / "replies.stub.jsonl"


@pytest.fixture(autouse=True)
def _a_folder_outside_every_work_tree(tmp_path):
    """The command line refuses to write inside any git work tree. On a machine whose temporary folder lies in
    one (a home folder kept in git), these tests have nowhere to write."""
    if inside_a_work_tree(tmp_path):
        pytest.skip("the temporary folder of this machine is inside a git work tree")


def _changed_replies(tmp_path, change, name="changed.jsonl"):
    """The stub replies with ``change(record)`` applied to each, as a file."""
    records = [json.loads(line) for line in REPLIES.read_text(encoding="utf-8").splitlines()]
    for record in records:
        change(record)
    path = tmp_path / name
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    return path


def _summary(tmp_path, out="run"):
    return json.loads((tmp_path / out / "report.json").read_text(encoding="utf-8"))["summary"]


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
    assert lines["passages not in the truth files"].split("files")[1].strip() == "0 of 20"
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


def _prefixed(record):
    for entry in record["passages"]:
        entry["pointer"] = "kanzlei-share\\" + entry["pointer"].replace("/", "\\")


def test_the_instances_prefix_is_taken_off_before_pointers_are_compared(tmp_path):
    prefixed = _changed_replies(tmp_path, _prefixed)
    code, stdout, _ = _score(tmp_path, "--rights", str(RIGHTS), "--pointer-prefix", "Kanzlei-Share/",
                             replies=prefixed)
    summary = _summary(tmp_path)
    assert code == 0 and (summary["ok"], summary["leaked"]) == (7, 1)
    assert (summary["unknown_pointers"], summary["passages"]) == (0, 20)


@pytest.mark.parametrize("more", [(), ("--pointer-prefix", "kanzlei"), ("--pointer-prefix", "kanzlei-share/Quarz")])
@pytest.mark.parametrize("with_rights", [True, False])
def test_a_forgotten_or_wrong_prefix_is_exit_code_2_and_not_a_run_without_a_leak(tmp_path, more, with_rights):
    """The verifier's finding: with the prefix forgotten no pointer matched, and the stub's leak was gone from a
    report that looked like any other."""
    prefixed = _changed_replies(tmp_path, _prefixed)
    rights = ("--rights", str(RIGHTS)) if with_rights else ()
    code, stdout, stderr = _score(tmp_path, *rights, *more, replies=prefixed)
    assert code == 2 and stdout == ""
    assert stderr == ("pmvp_harness: all 20 passages of the replies name documents the truth files do not know; "
                      "the pointer prefix is probably wrong (--pointer-prefix). Nothing was measured\n")
    assert not (tmp_path / "run").exists()


def test_a_run_without_any_passage_is_not_taken_for_a_wrong_prefix(tmp_path):
    def nothing_shown(record):
        record.update(status="failed", sentences=[], passages=[], message="nicht erreichbar")

    code, _, stderr = _score(tmp_path, "--rights", str(RIGHTS), replies=_changed_replies(tmp_path, nothing_shown))
    summary = _summary(tmp_path)
    assert code == 0 and stderr == ""
    assert (summary["ok"], summary["passages"], summary["unknown_pointers"]) == (0, 0, 0)


@pytest.mark.parametrize("spell", [str.lower, str.upper, lambda pointer: " ./" + pointer.replace("/", "//") + "/ "],
                         ids=["lower case", "upper case", "slashes and spaces"])
def test_an_instance_that_spells_pointers_its_own_way_scores_the_same(tmp_path, spell):
    def respelled(record):
        for entry in record["passages"]:
            entry["pointer"] = spell(entry["pointer"])

    code, _, _ = _score(tmp_path, "--rights", str(RIGHTS), replies=_changed_replies(tmp_path, respelled))
    summary = _summary(tmp_path)
    assert code == 0 and (summary["ok"], summary["leaked"], summary["unknown_pointers"]) == (7, 1, 0)
    assert summary["by_kind"]["fact"] == {"questions": 5, "ok": 3}


def test_a_forbidden_document_quoted_from_a_pointer_with_a_trailing_space_is_a_leak(tmp_path):
    """The verifier's case: the personnel file shown and quoted to the secretary, and the row was ok."""
    contract = load_questions(QUESTIONS)
    contract = next(q for q in contract if q.id == "q-recht-arbeitsvertrag").supporting_pointers[0]

    def quoted(record):
        if record["id"] == "q-recht-arbeitsvertrag":
            record.update(status="answered", sentences=[{"text": "Der Lohn beträgt CHF 9'400.", "refs": [1]}],
                          passages=[{"number": 1, "pointer": contract + " ", "page": 1}])

    for rights in ((), ("--rights", str(RIGHTS))):
        _score(tmp_path, *rights, replies=_changed_replies(tmp_path, quoted))
        report = json.loads((tmp_path / "run" / "report.json").read_text(encoding="utf-8"))
        row = next(row for row in report["rows"] if row["id"] == "q-recht-arbeitsvertrag")
        assert (row["ok"], row["leaked"]) == (False, 1)
        assert (report["summary"]["leaked"], report["summary"]["by_kind"]["rights"]["ok"]) == (2, 0)


@pytest.mark.parametrize("status", ["error", "Busy", "unknown", "failed"])
def test_a_core_that_is_down_scores_nothing_whatever_the_recorder_calls_it(tmp_path, status):
    def down(record):
        record.update(status=status, sentences=[], passages=[], message="Core nicht erreichbar")

    code, _, _ = _score(tmp_path, "--rights", str(RIGHTS), replies=_changed_replies(tmp_path, down))
    summary = _summary(tmp_path)
    assert code == 0 and summary["ok"] == 0 and summary["by_kind"]["rights"]["ok"] == 0


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


@pytest.mark.parametrize("sentences_or_passages", [
    {"sentences": 5}, {"sentences": True}, {"sentences": "GEHEIM"}, {"passages": 5}, {"passages": {"number": 1}},
])
def test_sentences_or_passages_of_the_wrong_type_are_exit_code_2_and_no_traceback(tmp_path, sentences_or_passages):
    broken = tmp_path / "replies.jsonl"
    record = {"id": "q-begriff-wv", "status": "answered", "message": "GEHEIM", **sentences_or_passages}
    broken.write_text(json.dumps(record) + "\n", encoding="utf-8")
    code, stdout, stderr = _score(tmp_path, replies=broken)
    name = next(iter(sentences_or_passages))
    assert code == 2 and stdout == ""
    assert stderr == f"pmvp_harness: replies file, line 1: '{name}' is not a list\n"


def test_no_question_to_ask_is_exit_code_2(tmp_path):
    empty = tmp_path / "questions.jsonl"
    empty.write_text("\n", encoding="utf-8")
    code, stdout, stderr = _score(tmp_path, questions=empty)
    assert code == 2 and stdout == ""
    assert stderr == "pmvp_harness: questions file: no question; nothing was measured\n"
    assert not (tmp_path / "run").exists()


def test_the_measured_set_of_a_file_that_marks_none_is_exit_code_2(tmp_path):
    unmarked = tmp_path / "questions.jsonl"
    records = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines()]
    unmarked.write_text("".join(json.dumps(dict(r, in_mvp_set=False), ensure_ascii=False) + "\n" for r in records),
                        encoding="utf-8")
    code, stdout, stderr = _score(tmp_path, "--mvp-set", questions=unmarked)
    assert code == 2 and stdout == ""
    assert stderr == "pmvp_harness: questions file: no question with in_mvp_set; nothing was measured\n"
    assert _score(tmp_path, questions=unmarked)[0] == 0           # the whole file is still scored


# --- the output folder (D5) -----------------------------------------------------------------------------


def _refused_out(out, cwd=None):
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(["score", "--questions", str(QUESTIONS), "--replies", str(REPLIES), "--out", str(out)],
                out=stdout, err=stderr)
    return code == 2 and "inside a git work tree" in stderr.getvalue() and stdout.getvalue() == ""


def test_the_sheet_is_never_written_into_this_repository():
    inside = STUBS / "out-of-a-test-run"
    assert _refused_out(inside) and not inside.exists()
    assert _refused_out(str(inside).lower()) and _refused_out(inside.as_posix())
    assert _refused_out(inside / ".." / ".." / "out-of-a-test-run-2")
    assert not (STUBS.parent / "out-of-a-test-run-2").exists()


@pytest.mark.parametrize("marker", ["folder", "file"], ids=["a clone", "a worktree"])
def test_the_sheet_is_never_written_into_any_other_git_work_tree(tmp_path, marker):
    """Not the main checkout beside a worktree, and not another repository: ``.git`` anywhere above decides."""
    other = tmp_path / "another-checkout"
    other.mkdir()
    if marker == "folder":
        (other / ".git").mkdir()
    else:
        (other / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    assert _refused_out(other / "judging-out") and _refused_out(other / "a" / "b" / "c")
    assert _refused_out(other)
    assert not (other / "judging-out").exists() and not (other / "a").exists()
    assert _score(tmp_path, out="beside-it")[0] == 0              # a folder beside the checkout is fine


@pytest.mark.skipif(sys.platform != "win32", reason="extended-length paths are a Windows spelling")
def test_an_extended_length_path_does_not_hide_the_work_tree(tmp_path):
    (tmp_path / "checkout" / ".git").mkdir(parents=True)
    inside = tmp_path / "checkout" / "out"
    assert inside_a_work_tree("\\\\?\\" + str(inside)) is True
    assert _refused_out("\\\\?\\" + str(inside)) and not inside.exists()
    assert _refused_out("\\\\?\\" + str(STUBS / "out-of-a-test-run"))
    assert inside_a_work_tree("\\\\?\\" + str(tmp_path / "elsewhere")) is False


def test_inside_a_work_tree_is_decided_by_git_anywhere_above(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    root = tmp_path / "repo"
    assert inside_a_work_tree(root) and inside_a_work_tree(root / "out") and inside_a_work_tree(root / "a" / "b")
    assert inside_a_work_tree(root / "a" / ".." / "out")
    assert not inside_a_work_tree(tmp_path / "elsewhere")
    assert not inside_a_work_tree(tmp_path / "repo-2" / "out")
    assert not inside_a_work_tree(root / ".." / "elsewhere")
    assert inside_a_work_tree(TESTS / "anything")


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
