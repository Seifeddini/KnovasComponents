"""PMVP-20 context: "ein" is the number 1 only before a unit or a quantity word (the coordinator's decision, 2026-11-22).

On the demo tenant question 4 lost every remaining statement to "ein Ansatz": the strict "ein" of the hardening rework
read every article as the number 1 bound to its noun. Now "ein", "eine", "einer", "eines", "einem", "einen" (and
the English "a", "an") count only before a unit of time, money, share or count, or a quantity (Tag, Woche, Monat,
Jahr, Jahrzehnt, Stunde, Minute, Prozent, Franken, CHF, Euro, Mal, Drittel, Viertel, Haelfte, Seite, Exemplar, paar,
...), in any inflected form; before any other word they are the article and are not checked. "eins" and "one" are
always the number. A word with "ein" before a unit inside it ("Mindesteinjahresfrist") stands for itself.
"""
from __future__ import annotations

import json

import pytest

from assistant.check import NUMBER_NOT_FOUND, check_answer, claimed_numbers


def st(statement, quote):
    return json.dumps({"statement": statement, "source": 1, "quote": quote}, ensure_ascii=False)


def _shown(statement, passage, quote=None):
    shown, removed_by = check_answer(st(statement, quote or passage), {1: passage})
    return [s.text for s in shown], {k: v for k, v in removed_by.items() if v}


# --- the coordinator's three pins --------------------------------------------------------------------------------


def test_ein_ansatz_passes():
    passage = "Der Stundenansatz betr\xe4gt CHF 340 zuz\xfcglich Mehrwertsteuer."
    shown, removed = _shown("Es gilt ein Ansatz von CHF 340 pro Stunde.", passage)
    assert shown == ["Es gilt ein Ansatz von CHF 340 pro Stunde."] and removed == {}


def test_ein_monat_against_drei_monate_is_removed():
    shown, removed = _shown("Die Frist betr\xe4gt ein Monat.", "Die Frist betr\xe4gt drei Monate.")
    assert shown == [] and removed == {NUMBER_NOT_FOUND: 1}


def test_einen_monat_against_einen_monat_passes():
    shown, removed = _shown("Die Frist betr\xe4gt einen Monat.", "Die Frist betr\xe4gt einen Monat ab Zugang.")
    assert shown == ["Die Frist betr\xe4gt einen Monat."] and removed == {}


# --- before a unit or a quantity "ein" is 1, in every form ------------------------------------------------------


@pytest.mark.parametrize("phrase, other", [
    ("einen Tag", "zwei Tage"), ("eine Woche", "zwei Wochen"), ("einem Monat", "sechs Monaten"),
    ("eines Jahres", "zweier Jahre"), ("ein Jahrzehnt", "ein Jahr"), ("eine Stunde", "zwei Stunden"),
    ("einer Minute", "drei Minuten"), ("ein Prozent", "zwei Prozent"), ("einen Franken", "zwei Franken"),
    ("ein Mal", "zwei Mal"), ("ein Drittel", "zwei Drittel"), ("ein Viertel", "drei Viertel"),
    ("eine H\xe4lfte", "zwei H\xe4lften"), ("eine Seite", "zwei Seiten"), ("ein Exemplar", "drei Exemplare"),
    ("ein paar Tage", "drei Tage"),
])
def test_before_a_unit_ein_is_one_and_another_number_does_not_support_it(phrase, other):
    shown, removed = _shown(f"Es gilt {phrase} als Frist.", f"Es gilt {other} als Frist ab Zugang.", "als Frist ab")
    assert shown == [] and removed == {NUMBER_NOT_FOUND: 1}


def test_an_english_a_before_a_unit_is_one():
    shown, removed = _shown("The notice period is a month.", "The notice period is two months.",
                            "The notice period is")
    assert shown == [] and removed == {NUMBER_NOT_FOUND: 1}


@pytest.mark.parametrize("phrase, same", [
    ("einen Tag", "1 Tag"), ("eine Woche", "einer Woche"), ("ein Jahr", "1 Jahr"), ("einen Franken", "ein Franken"),
    ("eine Million", "eine Million"),
])
def test_before_a_unit_ein_is_supported_by_ein_or_1(phrase, same):
    shown, removed = _shown(f"Es gilt {phrase} als Frist.", f"Es gilt {same} als Frist ab Zugang.", "als Frist ab")
    assert shown == [f"Es gilt {phrase} als Frist."] and removed == {}


# --- before any other word it is the article and is not checked -------------------------------------------------


@pytest.mark.parametrize("statement, passage", [
    ("Der Mieter muss eine Kaution leisten.", "Der Mieter muss Kaution leisten, in bar."),
    ("Eine Mietzinserh\xf6hung muss dem Mieter auf dem amtlichen Formular mitgeteilt werden.",
     "Mietzinserh\xf6hungen sind dem Mieter auf dem amtlichen Formular mitzuteilen."),
    ("Es gilt eine dreimonatige K\xfcndigungsfrist.", "Die K\xfcndigungsfrist betr\xe4gt drei Monate."),
    ("Einer der Mieter hat gek\xfcndigt.", "Die Mieter haben gek\xfcndigt."),
    ("The tenant must pay a deposit.", "The tenant must pay the deposit in cash."),
])
def test_the_article_is_not_checked(statement, passage):
    shown, removed = _shown(statement, passage)
    assert shown == [statement] and removed == {}


def test_what_the_narrowed_rule_lets_through():
    """The cost of the decision: a count of a thing that is no unit is no longer checked (the verifier's input of
    the hardening, "eine Unterschrift" against "zwei Unterschriften", is shown now)."""
    shown, removed = _shown("Es gen\xfcgt eine Unterschrift.", "Es braucht zwei Unterschriften.")
    assert shown == ["Es gen\xfcgt eine Unterschrift."] and removed == {}


def test_eins_and_one_are_always_the_number():
    assert [sorted(c) for c in claimed_numbers("Die Parteien sind sich eins.")] == [["w\x00eins"]]
    assert claimed_numbers("one tenant") != []


def test_ein_before_a_unit_inside_a_word_stands_for_the_word():
    """With the article no longer checked, "eine Mindesteinjahresfrist" would pass against
    "Mindestzweijahresfrist"; the word now claims itself, as "einjaehrig" does (also without an article)."""
    for statement in ("Es gilt eine Mindesteinjahresfrist.", "Es gilt die Mindesteinjahresfrist."):
        shown, removed = _shown(statement, "Es gilt eine Mindestzweijahresfrist.", "Es gilt eine")
        assert shown == [] and removed == {NUMBER_NOT_FOUND: 1}
    shown, removed = _shown("Es gilt eine Mindesteinjahresfrist.", "Es gilt eine Mindesteinjahresfrist.")
    assert removed == {}
