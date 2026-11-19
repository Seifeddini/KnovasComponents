"""PMVP-20 rework acceptance tests: the reference check never joins two statements
(FEAT-2026-11-19-platform-mvp, Jira SS-732).

FROZEN. Written by the coordinator after the first verification of PMVP-20 failed (2026-11-19); a builder never
edits this file. Its git blob id is in docs/platform-mvp/frozen-tests.txt.

Why: the reference check works sentence by sentence (POST-01, W-03). A splitter that joins two statements lets one
without a reference pass under the other's number, which is the one thing the check exists to stop. A wrong cut
only drops or shortens text, a loss the acceptance harness measures. So when the splitter is unsure, it cuts. The
cases that must still stay one sentence are the common ones in German and Swiss legal text.

The first work order said the opposite ("a cautious splitter that keeps an abbreviation inside one sentence is
better than one that cuts it"); that was the coordinator's mistake and this file replaces it.
"""
from __future__ import annotations

import pytest

from assistant.service import NO_ANSWER_TEXT, AssistantService

ROWS = [
    {"doc_id": f"Mandate/Mueller/doc-{n}.pdf", "path": f"Mandate/Mueller/doc-{n}.pdf", "title": f"Dokument {n}",
     "page_number": 1, "score": 1.0 - n / 100, "snippet": text}
    for n, text in [
        (1, "Die Kuendigungsfrist betraegt drei Monate zum Monatsende."),
        (2, "Die Frist beginnt mit dem Zugang der Kuendigung."),
        (3, "Der Vertrag wurde am 1. Maerz 2024 unterzeichnet."),
    ]
]


def _reply(answer):
    def search(question, limit):
        return [dict(r) for r in ROWS]

    def generate(*, system_instruction, messages, max_tokens):
        return answer

    return AssistantService(search=search, generate=generate).answer("Welche Frist gilt?")


# --- safety: a statement after a referenced one is checked on its own -------------------------------

AFTER_A_REFERENCE = [
    "Die Frist betraegt drei Monate [1]. 2023 wurde sie auf sechs Monate verlaengert.",
    "Die Frist betraegt drei Monate [1]. danach gelten sechs Monate.",
    "Die Frist betraegt drei Monate [1]. (Das gilt auch fuer die Mieterin.)",
    "Die Frist betraegt drei Monate [1].Sie wurde 2023 verlaengert.",
    "Die Frist betraegt drei Monate [1]\nSie wurde 2023 verlaengert.",
    "- Die Frist betraegt drei Monate [1]\n- Sie wurde 2023 verlaengert",
    "Die Frist betraegt drei Monate [1]; danach darf 2023 verlaengert werden.",
]


@pytest.mark.parametrize("answer", AFTER_A_REFERENCE)
def test_a_statement_after_a_referenced_one_is_checked_on_its_own(answer):
    reply = _reply(answer)
    assert reply.status == "answered"
    assert [s.refs for s in reply.sentences] == [(1,)]
    assert "drei Monate" in reply.sentences[0].text
    shown = " ".join(s.text for s in reply.sentences)
    for word in ("2023", "danach", "Mieterin", "verlaengert"):
        assert word not in shown
    assert reply.removed == 1


# --- safety: a number before a stop does not join two statements -------------------------------------

BEFORE_A_REFERENCE = [
    "Die Miete betraegt CHF 500. Die Frist betraegt drei Monate [1].",
    "Die Miete betraegt CHF 50. Die Frist betraegt drei Monate [1].",
    "Die Miete betraegt CHF 1'500. Die Frist betraegt drei Monate [1].",
    "Der Vertrag gilt seit dem 1.1.2024. Die Frist betraegt drei Monate [1].",
    "Der Vertrag stammt aus dem Jahr 2023. Die Frist betraegt drei Monate [1].",
    "Massgebend ist Ziffer 5. Die Frist betraegt drei Monate [1].",
]


@pytest.mark.parametrize("answer", BEFORE_A_REFERENCE)
def test_a_number_before_a_stop_does_not_join_two_statements(answer):
    reply = _reply(answer)
    assert [s.refs for s in reply.sentences] == [(1,)]
    assert reply.sentences[0].text.startswith("Die Frist")
    assert reply.removed == 1


# --- quality: common abbreviations, ordinals and dates stay in one sentence -------------------------

KEPT_WHOLE = [
    ("Die Kuendigung ist bis zum 1. März 2024 moeglich [1].", "1. März 2024 moeglich"),
    ("Die Frist endet am 31. Dezember [1].", "am 31. Dezember"),
    ("Gemaess Art. 5 Abs. 2 OR gilt eine Frist von drei Monaten [1].", "Gemaess Art. 5 Abs. 2 OR gilt"),
    ("Das gilt z. B. fuer Mietvertraege [1].", "z. B. fuer Mietvertraege"),
    ("Siehe S. 3 und Nr. 4 des Vertrags [1].", "Siehe S. 3 und Nr. 4 des Vertrags"),
    ("Hr. Mueller hat den Vertrag unterzeichnet [1].", "Hr. Mueller hat"),
    ("Die Zahlung erfolgt im 3. Quartal [1].", "im 3. Quartal"),
]


@pytest.mark.parametrize("answer, kept", KEPT_WHOLE)
def test_common_abbreviations_ordinals_and_dates_stay_in_one_sentence(answer, kept):
    reply = _reply(answer)
    assert reply.removed == 0
    assert [s.refs for s in reply.sentences] == [(1,)]
    assert kept in reply.sentences[0].text


# --- POST-05: the no-answer sentence is never shown as a cited answer --------------------------------


@pytest.mark.parametrize("answer", [NO_ANSWER_TEXT + " [1].", NO_ANSWER_TEXT + " [1][2].", NO_ANSWER_TEXT + "."])
def test_the_no_answer_sentence_is_never_a_cited_answer(answer):
    reply = _reply(answer)
    assert reply.status == "no_answer"
    assert reply.message == NO_ANSWER_TEXT
    assert reply.sentences == []
    assert [p.number for p in reply.passages] == [1, 2, 3]


def test_a_no_answer_sentence_inside_an_answer_is_not_shown():
    reply = _reply("Die Frist betraegt drei Monate [1]. " + NO_ANSWER_TEXT + " [2].")
    assert reply.status == "answered"
    assert [s.refs for s in reply.sentences] == [(1,)]
    assert all("finde ich nichts" not in s.text for s in reply.sentences)
