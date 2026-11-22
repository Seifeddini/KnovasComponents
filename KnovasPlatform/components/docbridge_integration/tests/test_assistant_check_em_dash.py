"""PMVP-20 context's investigation: the em dash is never a minus sign in the number check.

On the demo tenant question 4 ("Wie hoch ist das vereinbarte Honorar?") lost every statement to the number check.
Among the causes: four of the documents write the matter number after an em dash ("Honorarvereinbarung \u2014
2019-031"), and the check read "\u2014 2019" as the signed number -2019, so a statement naming "Mandat 2019-031" did
not find its year. German, French and Italian write a minus as "-", "\u2212" or "\u2013", never as an em dash; the em
dash and its longer kin (U+2015, U+2E3A, U+2E3B) now tell numbers apart and sign none. Every other dash reads as
before, the en dash with a space included (its minus reading is a decision, not this fix).
"""
from __future__ import annotations

import json

import pytest

from assistant.check import NUMBER_NOT_FOUND, check_answer, claimed_numbers, offered_numbers

EM = "\u2014"


def st(statement, source, quote):
    return json.dumps({"statement": statement, "source": source, "quote": quote}, ensure_ascii=False)


def _shown(statement, passage, quote):
    shown, removed_by = check_answer(st(statement, 1, quote), {1: passage})
    return [s.text for s in shown], {k: v for k, v in removed_by.items() if v}


def _claims(text):
    return {"|".join(sorted(claim)) for claim in claimed_numbers(text)}


@pytest.mark.parametrize("dash", [EM, "\u2015", "\u2e3a", "\u2e3b"])
def test_a_number_after_an_em_dash_is_not_signed(dash):
    offered = offered_numbers(f"Honorarvereinbarung {dash} 2019-031 Stundenansatz CHF 340")
    assert {"2019", "031", "340"} <= offered
    assert not any(token.startswith("-") for token in offered)


def test_the_matter_number_after_an_em_dash_supports_the_statement():
    passage = (f"Honorarvereinbarung {EM} 2019-031\nDer Stundenansatz betr\xe4gt CHF 340 zuz\xfcglich "
               "Mehrwertsteuer.")
    shown, removed = _shown("Im Mandat 2019-031 betr\xe4gt der Stundenansatz CHF 340.", passage,
                            "Der Stundenansatz betr\xe4gt CHF 340")
    assert shown == ["Im Mandat 2019-031 betr\xe4gt der Stundenansatz CHF 340."] and removed == {}


def test_an_em_dash_in_the_statement_signs_nothing_either():
    assert _claims(f"Saldo {EM} 500 Franken") == {"500"}
    assert _claims(f"Saldo {EM}500 Franken") == {"500"}
    shown, removed = _shown(f"Der Saldo {EM} 500 Franken.", "Der Saldo betr\xe4gt \u2212500 Franken.",
                            "Der Saldo betr\xe4gt")
    assert shown == [] and removed == {NUMBER_NOT_FOUND: 1}         # -500 does not support 500


@pytest.mark.parametrize("passage, signed", [
    ("Der Saldo ist -500 Franken", "-500"), ("Der Saldo ist \u2212500 Franken", "-500"),
    ("Der Saldo ist \u2013500 Franken", "-500"), ("Der Saldo ist - 500 Franken", "- 500"),
    ("Der Saldo ist \u2013 500 Franken", "- 500"),
])
def test_every_other_minus_still_signs(passage, signed):
    assert signed in offered_numbers(passage) and "500" not in offered_numbers(passage)
    shown, removed = _shown("Der Saldo ist 500 Franken.", passage, "Der Saldo ist")
    assert shown == [] and removed == {NUMBER_NOT_FOUND: 1}


def test_an_em_dash_between_numbers_tells_them_apart():
    assert {"5", "7"} <= offered_numbers(f"5{EM}7 Tage")
    assert {"1", "500"} <= offered_numbers(f"Variante 1{EM}500 Franken")
    assert "1500" not in offered_numbers(f"Variante 1{EM}500 Franken")
    assert _claims(f"2023{EM}2024") == {"2023", "2024"}


def test_an_amount_written_with_a_dash_after_it_is_unchanged():
    for dash in (EM, "\u2013", "-"):
        assert "1500" in offered_numbers(f"Fr. 1'500.{dash} pro Monat")
