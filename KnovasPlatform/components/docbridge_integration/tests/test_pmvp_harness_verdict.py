"""PMVP-03, the builder's own tests for the rework after the first verification (2026-11-20).

Two wrong verdicts were found, and both are pinned here:

1. A question counts as ok only when it was really tested: the status is exactly ``answered`` or ``no_answer``, and
   what was handed in is a reply at all. Anything else is not ok, or an error row ``NotAReply`` without a score.
2. A leak is counted however the pointer is spelled, and the passages the truth files do not know are counted
   (``unknown_pointers``), so a wrong pointer prefix cannot make a leak disappear unnoticed.

The frozen acceptance file is unchanged; nothing here contradicts it.
"""
from __future__ import annotations

import unicodedata
from types import SimpleNamespace as NS

import pytest

from pmvp_harness import NotAReply, Question, Rights, run, score

VERTRAG = "Mandate/2024-017/Vertrag.pdf"
KLAGE = "Mandate/2024-017/Klage.pdf"
MEMO = "Mandate/2024-055/Memo.docx"
LOHN = "Personal/Lohnliste.xlsx"
SECRET = "GEHEIM"

FACT = Question(id="q-fact", question="Welche Frist gilt?", kind="fact", supporting_pointers=(VERTRAG,))
DOCUMENT = Question(id="q-document", question="Zeig mir die Klage.", kind="document", supporting_pointers=(KLAGE,))
PROCESS = Question(id="q-process", question="Was kommt zuerst?", kind="process", supporting_pointers=(VERTRAG,))
NEGATIVE = Question(id="q-negative", question="Wie hoch war der Bonus?", kind="negative")
RIGHTS = Question(id="q-rights", question="Worauf stützt sich die Akte?", kind="rights", supporting_pointers=(MEMO,),
                  asked_as="lz")
FACT_AS_LZ = Question(id="q-fact-lz", question="Welche Frist gilt?", kind="fact", supporting_pointers=(VERTRAG,),
                      asked_as="lz")
EVERY_KIND = [FACT, DOCUMENT, PROCESS, NEGATIVE, RIGHTS]
TABLE = Rights([("lz", MEMO, False), ("mb", MEMO, True), ("lz", VERTRAG, True), ("lz", LOHN, False)])


def passage(number, pointer, **more):
    return NS(number=number, pointer=pointer, **more)


def sentence(text, *refs):
    return NS(text=text, refs=tuple(refs))


def reply(status="answered", sentences=(), passages=(), removed=0, message=None):
    return NS(status=status, sentences=list(sentences), passages=list(passages), removed=removed, message=message)


def best_reply(question, status=None):
    """The reply that is ok for ``question``, optionally under another status."""
    if question.kind == "negative":
        return reply(status="no_answer" if status is None else status, passages=[passage(1, KLAGE)], message="nichts")
    if question.kind == "rights":
        return reply(status="no_answer" if status is None else status, passages=[passage(1, VERTRAG)],
                     message="nichts")
    return reply(status="answered" if status is None else status, sentences=[sentence("Die Antwort.", 1)],
                 passages=[passage(1, question.supporting_pointers[0])])


# --- 1. ok only when really tested ----------------------------------------------------------------------


@pytest.mark.parametrize("question", EVERY_KIND, ids=lambda question: question.kind)
def test_the_best_reply_of_every_kind_is_ok(question):
    assert score(question, best_reply(question)).ok is True


@pytest.mark.parametrize("status", ["busy", "failed", "", "error", "timeout", "other", "Busy", "BUSY", "Failed",
                                    " busy", "busy ", "Answered", "ANSWERED", "No_Answer", "no answer",
                                    " no_answer", "answered "])
@pytest.mark.parametrize("question", EVERY_KIND, ids=lambda question: question.kind)
def test_ok_only_for_the_status_answered_or_no_answer_exactly(question, status):
    """A closed list. The verifier found a rights question ok under ``""``, ``"error"`` and ``"Busy"``."""
    for candidate in (best_reply(question, status), reply(status=status, message="nichts")):
        marks = score(question, candidate)
        assert marks.ok is False and marks.status == status


@pytest.mark.parametrize("question", [RIGHTS, NEGATIVE], ids=lambda question: question.kind)
def test_an_answered_reply_that_shows_no_sentence_is_not_ok(question):
    assert score(question, reply(status="answered", passages=[passage(1, VERTRAG)])).ok is False
    assert score(question, reply(status="answered")).ok is False


def test_a_no_answer_reply_that_shows_sentences_is_not_ok():
    contradicting = reply(status="no_answer", sentences=[sentence("Dazu steht etwas im Vertrag.", 1)],
                          passages=[passage(1, VERTRAG)])
    assert score(RIGHTS, contradicting).ok is False
    assert score(NEGATIVE, contradicting).ok is False


NOT_REPLIES = {
    "None": None,
    "an empty dict": {},
    "a dict with a reply's keys": {"status": "no_answer", "sentences": [], "passages": []},
    "a dict that would leak": {"status": "answered", "sentences": [{"text": SECRET, "refs": [1]}],
                               "passages": [{"number": 1, "pointer": MEMO}]},
    "a text": f"{SECRET}: keine Antwort",
    "a number": 7,
    "a list": [],
    "no status": NS(sentences=[], passages=[]),
    "status None": NS(status=None, sentences=[], passages=[]),
    "status a number": NS(status=7, sentences=[], passages=[]),
    "only a status": NS(status="no_answer"),
    "no sentences": NS(status="no_answer", passages=[]),
    "no passages": NS(status="no_answer", sentences=[]),
    "sentences None": NS(status="no_answer", sentences=None, passages=[]),
    "passages None": NS(status="no_answer", sentences=[], passages=None),
    "sentences a text": NS(status="answered", sentences=SECRET, passages=[]),
    "passages a number": NS(status="no_answer", sentences=[], passages=5),
    "a sentence that is a text": NS(status="answered", sentences=[SECRET], passages=[]),
    "a sentence that is a dict": NS(status="answered", sentences=[{"text": SECRET, "refs": [1]}], passages=[]),
    "a sentence without text": NS(status="answered", sentences=[NS(refs=(1,))], passages=[passage(1, VERTRAG)]),
    "a sentence whose text is a number": NS(status="answered", sentences=[NS(text=5, refs=())], passages=[]),
    "refs a text": NS(status="answered", sentences=[NS(text=SECRET, refs="1")], passages=[passage(1, VERTRAG)]),
    "refs a number": NS(status="answered", sentences=[NS(text=SECRET, refs=1)], passages=[passage(1, VERTRAG)]),
    "refs missing": NS(status="answered", sentences=[NS(text=SECRET)], passages=[passage(1, VERTRAG)]),
    "a reference as text": NS(status="answered", sentences=[sentence(SECRET, "1")], passages=[passage(1, VERTRAG)]),
    "a reference True": NS(status="answered", sentences=[sentence(SECRET, True)], passages=[passage(1, VERTRAG)]),
    "a reference 1.0": NS(status="answered", sentences=[sentence(SECRET, 1.0)], passages=[passage(1, VERTRAG)]),
    "a passage that is a dict": NS(status="no_answer", sentences=[], passages=[{"number": 1, "pointer": MEMO}]),
    "a passage without number": NS(status="no_answer", sentences=[], passages=[NS(pointer=MEMO)]),
    "a passage number as text": NS(status="no_answer", sentences=[], passages=[passage("1", MEMO)]),
    "a passage number True": NS(status="no_answer", sentences=[], passages=[passage(True, MEMO)]),
    "a passage pointer that is a number": NS(status="no_answer", sentences=[], passages=[passage(1, 7)]),
    "two passages with one number": NS(status="no_answer", sentences=[],
                                       passages=[passage(1, VERTRAG), passage(1, MEMO)]),
    "removed a text": NS(status="no_answer", sentences=[], passages=[], removed=SECRET),
    "removed negative": NS(status="no_answer", sentences=[], passages=[], removed=-1),
    "removed True": NS(status="no_answer", sentences=[], passages=[], removed=True),
    "removed 2.5": NS(status="no_answer", sentences=[], passages=[], removed=2.5),
}


@pytest.mark.parametrize("name", NOT_REPLIES)
@pytest.mark.parametrize("question", [RIGHTS, FACT, NEGATIVE], ids=lambda question: question.kind)
def test_what_is_not_a_reply_raises_without_a_word_of_it(question, name):
    """The verifier found ``score(rights_question, None).ok`` true. In ``score`` called directly it is a
    ``TypeError`` now, and its message names the part that is wrong, never what the part held."""
    with pytest.raises(TypeError) as err:
        score(question, NOT_REPLIES[name], rights=TABLE)
    assert isinstance(err.value, NotAReply)
    message = str(err.value)
    assert message.startswith("not a reply: ") and SECRET not in message and MEMO not in message
    assert err.value.__cause__ is None and err.value.__context__ is None


@pytest.mark.parametrize("name", NOT_REPLIES)
def test_what_is_not_a_reply_is_an_error_row_without_a_score(name, caplog):
    report = run([RIGHTS, NEGATIVE], lambda question: NOT_REPLIES[name], rights=TABLE)
    for row in report.rows:
        assert (row.error, row.score, row.reply, row.ok) == ("NotAReply", None, None, False)
    summary = report.summary()
    assert (summary["ok"], summary["errors"], summary["error_classes"]) == (0, 2, {"NotAReply": 2})
    assert SECRET not in caplog.text


def test_a_missing_removed_counts_as_zero():
    assert score(NEGATIVE, NS(status="no_answer", sentences=[], passages=[])).removed == 0
    assert score(NEGATIVE, NS(status="no_answer", sentences=[], passages=[], removed=None)).removed == 0
    assert score(NEGATIVE, reply(status="no_answer", removed=3)).removed == 3


def test_a_reply_may_be_any_object_with_the_fields_and_its_lists_may_be_tuples():
    class Reply:
        status = "answered"
        sentences = (sentence("Die Frist beträgt drei Monate.", 1),)
        passages = (passage(1, VERTRAG),)
        removed = 0

    assert score(FACT, Reply()).ok is True


# --- 2. a leak is counted however the pointer is spelled ------------------------------------------------

SPELLINGS = {
    "as the truth files spell it": lambda pointer: pointer,
    "lower case": str.lower,
    "upper case": str.upper,
    "a trailing slash": lambda pointer: pointer + "/",
    "a doubled slash": lambda pointer: pointer.replace("/", "//", 1),
    "a leading slash": lambda pointer: "/" + pointer,
    "a leading ./": lambda pointer: "./" + pointer,
    "backslashes": lambda pointer: pointer.replace("/", "\\"),
    "a trailing space": lambda pointer: pointer + " ",
    "a leading space": lambda pointer: " " + pointer,
    "the other Unicode form": lambda pointer: unicodedata.normalize("NFD", pointer),
    "all of it": lambda pointer: " .\\" + pointer.upper().replace("/", "\\\\") + "\\ ",
}
UMLAUT = unicodedata.normalize("NFC", "Mandate/2024-055/Verfügung Müller.pdf")


@pytest.mark.parametrize("spelling", SPELLINGS)
@pytest.mark.parametrize("forbidden", [MEMO, UMLAUT], ids=["plain", "umlaut"])
def test_a_rights_questions_document_leaks_however_the_instance_spells_it(forbidden, spelling):
    question = Question(id="q", question="?", kind="rights", supporting_pointers=(forbidden,), asked_as="lz")
    shown = SPELLINGS[spelling](forbidden)
    marks = score(question, reply(status="no_answer", passages=[passage(1, VERTRAG), passage(2, shown)]))
    assert list(marks.leaked_pointers) == [forbidden]             # named as the truth files spell it
    assert marks.ok is False


@pytest.mark.parametrize("spelling", SPELLINGS)
@pytest.mark.parametrize("forbidden", [MEMO, UMLAUT], ids=["plain", "umlaut"])
def test_the_rights_table_catches_a_forbidden_passage_however_it_is_spelled(forbidden, spelling):
    table = Rights([("lz", forbidden, False), ("lz", VERTRAG, True)])
    answered = reply(sentences=[sentence("Die Frist beträgt drei Monate.", 1)],
                     passages=[passage(1, VERTRAG), passage(2, SPELLINGS[spelling](forbidden))])
    marks = score(FACT_AS_LZ, answered, rights=table)
    assert list(marks.leaked_pointers) == [forbidden] and marks.ok is False
    assert marks.unknown_pointers == 0


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_the_table_and_the_question_may_be_the_ones_that_are_spelled_differently(spelling):
    odd = SPELLINGS[spelling](MEMO)
    no_answer = reply(status="no_answer", passages=[passage(1, MEMO)])
    question = Question(id="q", question="?", kind="rights", supporting_pointers=(odd,), asked_as="lz")
    assert score(question, no_answer).ok is False
    assert score(FACT_AS_LZ, reply(sentences=[sentence("x", 1)], passages=[passage(1, VERTRAG), passage(2, MEMO)]),
                 rights=Rights([("lz", odd, False)])).ok is False


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_the_supporting_document_is_hit_however_the_instance_spells_it(spelling):
    answered = reply(sentences=[sentence("Die Frist beträgt drei Monate.", 1)],
                     passages=[passage(1, SPELLINGS[spelling](VERTRAG))])
    marks = score(FACT, answered)
    assert marks.source_hit is True and marks.ok is True and marks.unknown_pointers == 0


def test_a_forbidden_document_under_the_instances_prefix_leaks_with_the_prefix_given():
    shown = reply(status="no_answer", passages=[passage(1, "Kanzlei-Share\\" + MEMO.upper().replace("/", "\\"))])
    for prefix in ("kanzlei-share/", "kanzlei-share", "KANZLEI-SHARE\\"):
        marks = score(RIGHTS, shown, pointer_prefix=prefix)
        assert list(marks.leaked_pointers) == [MEMO] and marks.unknown_pointers == 0
    # Without the prefix, or with one that only begins like it, the document is not recognised: no leak is seen,
    # and that is what unknown_pointers is for.
    for prefix in ("", "kanzlei", "kanzlei-sh"):
        marks = score(RIGHTS, shown, pointer_prefix=prefix)
        assert list(marks.leaked_pointers) == [] and marks.unknown_pointers == 1


def test_a_prefix_is_cut_only_at_a_folder_so_another_document_is_not_mistaken_for_the_source():
    other = reply(sentences=[sentence("Die Frist beträgt drei Monate.", 1)],
                  passages=[passage(1, "altMandate/2024-017/Vertrag.pdf")])
    marks = score(FACT, other, pointer_prefix="alt")
    assert marks.source_hit is False and marks.ok is False and marks.unknown_pointers == 1


# --- the passages the truth files do not know -------------------------------------------------------------


def test_unknown_pointers_counts_passages_neither_the_table_nor_a_question_names():
    shown = reply(status="no_answer", passages=[passage(1, VERTRAG), passage(2, "Mandate/2024-017/Notiz.docx"),
                                               passage(3, LOHN), passage(4, "Mandate/2024-017/Notiz.docx")])
    assert score(NEGATIVE, shown).unknown_pointers == 4            # no table, a question without documents
    assert score(NEGATIVE, shown, rights=TABLE).unknown_pointers == 2     # the table knows two of the documents
    assert score(FACT, shown).unknown_pointers == 3                # the question's own document is known
    assert score(NEGATIVE, shown, known=[KLAGE, VERTRAG.upper() + "/"]).unknown_pointers == 3
    assert score(NEGATIVE, reply(status="no_answer")).unknown_pointers == 0


def test_in_a_run_every_questions_documents_are_known():
    shown = reply(status="no_answer", passages=[passage(1, VERTRAG), passage(2, KLAGE), passage(3, LOHN)])
    report = run([NEGATIVE, FACT, DOCUMENT], lambda question: shown)
    assert [row.score.unknown_pointers for row in report.rows] == [1, 1, 1]      # only LOHN, in each reply
    summary = report.summary()
    assert (summary["unknown_pointers"], summary["passages"]) == (3, 9)
    with_table = run([NEGATIVE, FACT, DOCUMENT], lambda question: shown, rights=TABLE).summary()
    assert (with_table["unknown_pointers"], with_table["passages"]) == (0, 9)


def test_a_forgotten_prefix_shows_as_every_passage_unknown():
    shown = reply(status="no_answer", passages=[passage(1, "kanzlei-share/" + VERTRAG),
                                               passage(2, "kanzlei-share/" + MEMO)])
    forgotten = run([RIGHTS, FACT], lambda question: shown, rights=TABLE).summary()
    assert (forgotten["leaked"], forgotten["unknown_pointers"], forgotten["passages"]) == (0, 4, 4)
    given = run([RIGHTS, FACT], lambda question: shown, rights=TABLE, pointer_prefix="kanzlei-share/").summary()
    assert (given["leaked"], given["unknown_pointers"], given["passages"]) == (1, 0, 4)


# --- 4. passages without a pointer ------------------------------------------------------------------------


@pytest.mark.parametrize("pointer", [None, "", "   ", "/"])
def test_a_passage_without_a_pointer_makes_a_rights_question_not_ok(pointer):
    """It may be the forbidden document: nobody can tell."""
    for shown in (reply(status="no_answer", passages=[passage(1, VERTRAG), passage(2, pointer)]),
                  reply(sentences=[sentence("Aus dem Memo.", 2)], passages=[passage(1, VERTRAG), passage(2, pointer)])):
        marks = score(RIGHTS, shown, known=[VERTRAG])
        assert marks.ok is False and list(marks.leaked_pointers) == []
        assert marks.unknown_pointers == 1                         # it is not a document the truth files know
    without_the_attribute = reply(status="no_answer", passages=[NS(number=1)])
    assert score(RIGHTS, without_the_attribute).ok is False


def test_a_passage_without_a_pointer_is_not_ok_wherever_the_rights_table_is_applied():
    answered = reply(sentences=[sentence("Die Frist beträgt drei Monate.", 1)],
                     passages=[passage(1, VERTRAG), passage(2, None)])
    assert score(FACT_AS_LZ, answered, rights=TABLE).ok is False
    # Asked as nobody, or without a table, rights are not checked: the answer stands on its supporting document.
    assert score(FACT, answered, rights=TABLE).ok is True
    assert score(FACT_AS_LZ, answered).ok is True
    assert score(FACT, answered).unknown_pointers == 1


def test_a_passage_without_a_pointer_never_counts_as_a_supporting_document():
    """Not even for a question built by hand with an empty supporting pointer (a file with one is refused)."""
    by_hand = Question(id="q", question="?", kind="fact", supporting_pointers=("", " / "))
    cited = reply(sentences=[sentence("Die Frist beträgt drei Monate.", 1)], passages=[passage(1, None)])
    marks = score(by_hand, cited)
    assert marks.source_hit is None and marks.ok is False
    rights_by_hand = Question(id="q", question="?", kind="rights", supporting_pointers=("",), asked_as="lz")
    assert list(score(rights_by_hand, reply(status="no_answer", passages=[passage(1, "")])).leaked_pointers) == []


def test_two_passages_with_one_number_cannot_hide_a_forbidden_document():
    """Malformed, so not a reply: which of the two a reference means cannot be told."""
    hidden = reply(status="no_answer", passages=[passage(1, VERTRAG), passage(1, MEMO)])
    with pytest.raises(NotAReply):
        score(RIGHTS, hidden)
    row = run([RIGHTS], lambda question: hidden).rows[0]
    assert (row.error, row.ok) == ("NotAReply", False)
