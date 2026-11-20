"""PMVP-03, the builder's own tests: what the harness reads (questions, rights, recorded replies) beyond what the
frozen acceptance file pins. A refusal names the file's role, the line and the reason, and never the content."""
from __future__ import annotations

import json
import unicodedata
from types import SimpleNamespace

import pytest

from pmvp_harness import load_questions, load_replies, load_rights, reply_record
from pmvp_harness.pointers import normalise

SECRET = "GEHEIM"
RECORD = {"id": "q-1", "question": f"Was ist {SECRET}?", "kind": "fact", "expected_points": [SECRET],
          "supporting_pointers": [f"Mandate/{SECRET}.pdf"], "asked_as": "", "in_mvp_set": True}


def _jsonl(tmp_path, records, name="file.jsonl"):
    path = tmp_path / name
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    return path


def _refused(loader, path):
    with pytest.raises(ValueError) as err:
        loader(path)
    assert SECRET not in str(err.value)
    # no chained error a traceback would print: the decoder's own exception holds the line
    assert err.value.__cause__ is None and (err.value.__context__ is None or err.value.__suppress_context__)
    return str(err.value)


# --- pointers ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("given,prefix,expected", [
    ("Mandate/a.pdf", "", "Mandate/a.pdf"),
    ("Mandate\\a.pdf", "", "Mandate/a.pdf"),
    ("/Mandate/a.pdf", "", "Mandate/a.pdf"),
    ("share/Mandate/a.pdf", "share/", "Mandate/a.pdf"),
    ("share/Mandate/a.pdf", "share", "Mandate/a.pdf"),
    ("\\\\srv\\share\\Mandate\\a.pdf", "//srv/share", "Mandate/a.pdf"),
    ("other/Mandate/a.pdf", "share/", "other/Mandate/a.pdf"),
    ("MANDATE/a.pdf", "", "MANDATE/a.pdf"),                       # case is kept
    ("db:AKTEN/2024-017", "share/", "db:AKTEN/2024-017"),
    (None, "", ""),
])
def test_one_spelling_for_a_pointer(given, prefix, expected):
    assert normalise(given, prefix) == expected


def test_a_composed_and_a_decomposed_umlaut_are_the_same_name():
    composed = unicodedata.normalize("NFC", "Mandate/Rüegg.pdf")
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    assert normalise(decomposed) == normalise(composed)


# --- questions --------------------------------------------------------------------------------------


def test_a_file_with_a_byte_order_mark_is_read(tmp_path):
    path = tmp_path / "q.jsonl"
    path.write_text(json.dumps(RECORD) + "\n", encoding="utf-8-sig")
    assert [q.id for q in load_questions(path)] == ["q-1"]


def test_the_fields_the_generator_could_leave_out_have_defaults(tmp_path):
    record = {"id": "q-neg", "question": "Gibt es das?", "kind": "negative"}
    question = load_questions(_jsonl(tmp_path, [record]))[0]
    assert (question.expected_points, question.supporting_pointers, question.asked_as, question.in_mvp_set) == \
        ((), (), "", False)


@pytest.mark.parametrize("change,reason", [
    ({"id": ""}, "'id'"),
    ({"id": 7}, "'id'"),
    ({"kind": None}, "'kind'"),
    ({"expected_points": SECRET}, "'expected_points'"),
    ({"supporting_pointers": [1]}, "'supporting_pointers'"),
    ({"asked_as": 3}, "'asked_as'"),
    ({"in_mvp_set": "yes"}, "'in_mvp_set'"),
    ({"supporting_pointers": []}, "supporting pointer"),           # an answer needs a document that holds it
    ({"kind": "rights"}, "'asked_as'"),                            # a rights question is asked as somebody
])
def test_a_record_the_generator_would_not_write_is_refused(tmp_path, change, reason):
    message = _refused(load_questions, _jsonl(tmp_path, [RECORD, {**RECORD, "id": "q-2", **change}]))
    assert "line 2" in message and reason in message


def test_a_line_that_is_json_but_no_record_is_refused(tmp_path):
    path = tmp_path / "q.jsonl"
    path.write_text(json.dumps(RECORD) + "\n" + json.dumps([SECRET]) + "\n", encoding="utf-8")
    assert "line 2" in _refused(load_questions, path)


def test_an_id_used_twice_is_refused_with_both_lines(tmp_path):
    message = _refused(load_questions, _jsonl(tmp_path, [RECORD, dict(RECORD, kind="document"), RECORD]))
    assert "line 2" in message and "line 1" in message


def test_a_file_that_is_not_utf8_is_refused_without_its_bytes(tmp_path):
    path = tmp_path / "q.jsonl"
    path.write_bytes(json.dumps(RECORD).replace("Was", "Wäs").encode("latin-1") + b"\n")
    message = _refused(load_questions, path)
    assert "UTF-8" in message


# --- rights -----------------------------------------------------------------------------------------


def _rights(tmp_path, body):
    path = tmp_path / "rights.csv"
    path.write_text("person,pointer,may_read,rule\n" + body, encoding="utf-8")
    return path


def test_the_table_is_read_as_the_generator_writes_it(tmp_path):
    rights = load_rights(_rights(tmp_path, 'lz,"Mandate/Akte, alt/Memo.docx",false,locked:2024-055\n'
                                           "mb,Mandate/Memo.docx,TRUE,default:kanzlei\n\n"))
    assert len(rights) == 2
    assert rights.may_read("lz", "Mandate/Akte, alt/Memo.docx") is False
    assert rights.may_read("mb", "Mandate\\Memo.docx") is True
    assert rights.may_read("mb", "share/Mandate/Memo.docx", pointer_prefix="share/") is True
    assert rights.leaks("lz", ["share/Mandate/Akte, alt/Memo.docx"], pointer_prefix="share") == \
        ["share/Mandate/Akte, alt/Memo.docx"]


@pytest.mark.parametrize("body,reason", [
    (f"lz,Mandate/{SECRET}.pdf,vielleicht,x\n", "'may_read'"),
    (f"lz,Mandate/{SECRET}.pdf,false\n", "columns"),
    (f",Mandate/{SECRET}.pdf,false,x\n", "empty"),
])
def test_a_row_that_cannot_be_read_is_refused(tmp_path, body, reason):
    message = _refused(load_rights, _rights(tmp_path, "mb,a.pdf,true,x\n" + body))
    assert "line 3" in message and reason in message


def test_two_rows_that_contradict_each_other_are_refused(tmp_path):
    body = f"lz,Mandate/{SECRET}.pdf,false,x\nlz,Mandate\\{SECRET}.pdf,true,y\n"
    message = _refused(load_rights, _rights(tmp_path, body))
    assert "line 3" in message and "line 2" in message


def test_the_same_row_twice_is_not_a_contradiction(tmp_path):
    assert len(load_rights(_rights(tmp_path, "lz,a.pdf,false,x\nlz,a.pdf,false,x\n"))) == 1


def test_an_empty_rights_file_is_refused(tmp_path):
    path = tmp_path / "rights.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        load_rights(path)


# --- recorded replies -------------------------------------------------------------------------------


def _reply():
    return SimpleNamespace(
        status="answered",
        sentences=[SimpleNamespace(text="Die Frist beträgt drei Monate.", refs=(1, 2))],
        passages=[SimpleNamespace(number=1, pointer="Mandate/a.pdf", page=3, title="Vertrag", text="lang"),
                  SimpleNamespace(number=2, pointer="Mandate/b.pdf", page=None, title="", text="lang")],
        removed=1, message="", raw_answer="Die Frist beträgt drei Monate [1][2]. Sonst nichts.")


def test_a_reply_is_recorded_without_the_passages_text(tmp_path):
    record = reply_record("q-1", _reply())
    assert record == {
        "id": "q-1", "status": "answered",
        "sentences": [{"text": "Die Frist beträgt drei Monate.", "refs": [1, 2]}],
        "passages": [{"number": 1, "pointer": "Mandate/a.pdf", "page": 3, "title": "Vertrag"},
                     {"number": 2, "pointer": "Mandate/b.pdf"}],
        "removed": 1, "message": None,
        "raw_answer": "Die Frist beträgt drei Monate [1][2]. Sonst nichts.",
    }


def test_a_recorded_reply_reads_back_as_the_reply_it_was(tmp_path):
    loaded = load_replies(_jsonl(tmp_path, [reply_record("q-1", _reply())]))["q-1"]
    assert loaded.status == "answered" and loaded.removed == 1 and loaded.message is None
    assert [(s.text, s.refs) for s in loaded.sentences] == [("Die Frist beträgt drei Monate.", (1, 2))]
    assert [(p.number, p.pointer, p.page) for p in loaded.passages] == \
        [(1, "Mandate/a.pdf", 3), (2, "Mandate/b.pdf", None)]
    assert loaded.raw_answer.endswith("Sonst nichts.")


def test_a_reply_without_an_answer_needs_only_its_status(tmp_path):
    loaded = load_replies(_jsonl(tmp_path, [{"id": "q-1", "status": "busy", "message": "ausgelastet"}]))["q-1"]
    assert (loaded.sentences, loaded.passages, loaded.removed, loaded.message) == ([], [], 0, "ausgelastet")
    assert loaded.raw_answer is None


@pytest.mark.parametrize("record", [
    {"status": "answered"},
    {"id": "q-2"},
    {"id": "q-2", "status": "answered", "sentences": [SECRET]},
    {"id": "q-2", "status": "answered", "sentences": [{"text": SECRET, "refs": ["1"]}]},
    {"id": "q-2", "status": "answered", "sentences": [{"text": SECRET, "refs": [True]}]},
    {"id": "q-2", "status": "answered", "passages": [{"number": "1", "pointer": SECRET}]},
    {"id": "q-2", "status": "answered", "passages": [{"number": 1}]},
    {"id": "q-2", "status": "answered", "removed": SECRET},
    [SECRET],
])
def test_a_recorded_reply_that_cannot_be_read_is_refused(tmp_path, record):
    good = {"id": "q-1", "status": "no_answer"}
    assert "line 2" in _refused(load_replies, _jsonl(tmp_path, [good, record]))


def test_two_replies_to_one_question_are_refused(tmp_path):
    good = {"id": "q-1", "status": "no_answer"}
    message = _refused(load_replies, _jsonl(tmp_path, [good, good]))
    assert "line 2" in message and "line 1" in message


def test_a_replies_line_that_is_not_json_is_refused(tmp_path):
    path = tmp_path / "replies.jsonl"
    path.write_text('{"id": "q-1", "status": "no_answer"}\n{"id": "q-2", "message": "' + SECRET + "\n",
                    encoding="utf-8")
    assert "line 2" in _refused(load_replies, path)
