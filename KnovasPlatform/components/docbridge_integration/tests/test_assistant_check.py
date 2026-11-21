"""PMVP-20, rework 2: the check of the answer (assistant.check) beyond the frozen acceptance tests.

The model answers in statements, one JSON object per line, each with one
source and a word-for-word quote. These tests pin what the frozen file
(tests/test_pmvp20_statements_acceptance.py) leaves open: how a line is read,
where a quote may start and end, what a number is, and which reason a removed
statement is counted under.
"""
from __future__ import annotations

import json
import unicodedata

import pytest

from assistant.check import (
    MAX_STATEMENT_CHARS,
    NO_ANSWER_TEXT,
    NOT_A_STATEMENT,
    NUMBER_NOT_FOUND,
    QUOTE_NOT_FOUND,
    REASONS,
    TOO_LONG,
    UNKNOWN_SOURCE,
    check_answer,
    claimed_numbers,
    find_quote,
    offered_numbers,
    read_answer,
    shown_text,
)

FRIST_TEXT = "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate zum Monatsende."
MIETE_TEXT = "Die Miete betr\u00e4gt CHF 1\u2019500 pro Monat; massgebend ist Art. 266a OR."
PASSAGES = {1: FRIST_TEXT, 2: MIETE_TEXT, 3: "Der Vertrag wurde am 1. M\u00e4rz 2024 unterzeichnet."}


def st(statement, source, quote, **more):
    return json.dumps({"statement": statement, "source": source, "quote": quote, **more},
                      ensure_ascii=False)


FRIST = st("Die K\u00fcndigungsfrist betr\u00e4gt drei Monate.", 1,
           "K\u00fcndigungsfrist betr\u00e4gt drei Monate")
MIETE = st("Die Miete betr\u00e4gt CHF 1'500 pro Monat.", 2, "Miete betr\u00e4gt CHF 1'500 pro Monat")
FRIST_SHOWN = ("Die K\u00fcndigungsfrist betr\u00e4gt drei Monate.", 1,
               "K\u00fcndigungsfrist betr\u00e4gt drei Monate")
MIETE_SHOWN = ("Die Miete betr\u00e4gt CHF 1'500 pro Monat.", 2,
               "Miete betr\u00e4gt CHF 1\u2019500 pro Monat")


def _check(*lines, passages=PASSAGES):
    shown, removed_by = check_answer("\n".join(lines), passages)
    return [(s.text, s.source, s.quote) for s in shown], removed_by


def _counts(**some):
    return {**dict.fromkeys(REASONS, 0), **some}


def _one(statement, passage, quote=None):
    """One statement against one passage; the quote defaults to the passage's first three words."""
    quote = quote or " ".join(passage.split()[:3])
    return _check(st(statement, 1, quote), passages={1: passage})


# --- removed_by: every reason, and which one a statement is counted under -------------------------


def test_every_reason_is_counted_under_its_own_name():
    shown, removed_by = _check(
        FRIST,
        "Allgemein gelten die gesetzlichen Fristen.",
        st("Das Gericht hat anders entschieden.", 9, "Das Gericht hat anders entschieden"),
        st("Die K\u00fcndigungsfrist betr\u00e4gt drei Monate.", 1, "Frist betr\u00e4gt sechs Monate"),
        st("Die K\u00fcndigungsfrist betr\u00e4gt 6 Monate.", 1, "K\u00fcndigungsfrist betr\u00e4gt drei Monate"),
        st("Die K\u00fcndigungsfrist betr\u00e4gt drei Monate " + "und so weiter " * 40 + ".", 1,
           "K\u00fcndigungsfrist betr\u00e4gt drei Monate"),
        MIETE,
    )
    assert shown == [FRIST_SHOWN, MIETE_SHOWN]
    assert removed_by == {NOT_A_STATEMENT: 1, UNKNOWN_SOURCE: 1, QUOTE_NOT_FOUND: 1,
                          NUMBER_NOT_FOUND: 1, TOO_LONG: 1}
    assert tuple(removed_by) == REASONS


def test_a_statement_is_counted_under_the_first_check_it_fails():
    long_tail = " und so weiter" * 40
    _, removed_by = _check(
        st("Seit 2023 gilt das" + long_tail, 9, "steht nirgends im Text"),      # source, quote, number, length
        st("Seit 2023 gilt das" + long_tail, 1, "steht nirgends im Text"),      # quote, number, length
        st("Seit 2023 gilt das" + long_tail, 1, "betr\u00e4gt drei Monate"),    # number, length
    )
    assert removed_by == _counts(**{UNKNOWN_SOURCE: 1, QUOTE_NOT_FOUND: 1, NUMBER_NOT_FOUND: 1})


def test_every_reason_is_a_key_even_when_nothing_was_removed():
    assert check_answer("", PASSAGES) == ([], _counts())
    assert _check(FRIST) == ([FRIST_SHOWN], _counts())


# --- reading the answer: one object per line ---------------------------------------------------------


def test_an_answer_cut_off_in_the_middle_of_an_object_loses_only_that_line():
    cut_off = '{"statement": "Die Miete betr\u00e4gt CHF 1\'500 pro Monat.", "source": 2, "quo'
    assert _check(FRIST, cut_off) == ([FRIST_SHOWN], _counts(**{NOT_A_STATEMENT: 1}))


def test_an_array_cut_off_in_the_middle_keeps_its_whole_lines():
    cut_off = '{"statement": "Massgebend ist Art. 266a OR.", "source": 2, "quote": "massgebend ist'
    shown, removed_by = _check("[" + FRIST + ",", MIETE + ",", cut_off)
    assert shown == [FRIST_SHOWN, MIETE_SHOWN]
    assert removed_by == _counts(**{NOT_A_STATEMENT: 1})


@pytest.mark.parametrize("line", [
    "Antwort: " + FRIST,
    FRIST + " Das gilt immer.",
    "Laut Quelle " + FRIST + " ist das so.",
    FRIST + ".",
    FRIST + " " + MIETE,
    "> " + FRIST,
])
def test_an_object_with_prose_around_it_on_its_line_is_prose(line):
    assert _check(line) == ([], _counts(**{NOT_A_STATEMENT: 1}))


def test_one_object_spread_over_several_lines_is_read_as_a_whole():
    spread = json.dumps(json.loads(FRIST), ensure_ascii=False, indent=2)
    assert "\n" in spread
    assert _check(spread) == ([FRIST_SHOWN], _counts())
    array = json.dumps([json.loads(FRIST), json.loads(MIETE)], ensure_ascii=False, indent=2)
    assert _check("```json", array, "```") == ([FRIST_SHOWN, MIETE_SHOWN], _counts())


def test_the_marks_of_an_array_are_not_part_of_a_line():
    assert _check("[", FRIST + ",", MIETE, "]") == ([FRIST_SHOWN, MIETE_SHOWN], _counts())
    # a whole array on one line, beside a line of prose
    shown, removed_by = _check("Hier ist die Antwort:", "[" + FRIST + ", " + MIETE + "]")
    assert shown == [FRIST_SHOWN, MIETE_SHOWN]
    assert removed_by == _counts(**{NOT_A_STATEMENT: 1})


def test_what_an_array_holds_besides_statements_is_counted():
    answer = json.dumps([json.loads(FRIST), "Allgemein gilt das so.", 7, None, [json.loads(MIETE)]],
                        ensure_ascii=False)
    assert _check(answer) == ([FRIST_SHOWN], _counts(**{NOT_A_STATEMENT: 4}))


def test_statements_under_another_key_are_not_the_agreed_form():
    wrapped = json.dumps({"statements": [json.loads(FRIST)]}, ensure_ascii=False)
    assert _check(wrapped) == ([], _counts(**{NOT_A_STATEMENT: 1}))


def test_json_nested_too_deep_is_a_line_that_is_not_a_statement():
    assert _check("[" * 5_000, FRIST) == ([FRIST_SHOWN], _counts(**{NOT_A_STATEMENT: 1}))


def test_read_answer_gives_one_value_per_line():
    values = read_answer("\n".join(["```json", "- " + FRIST, "", "Prosa.", "42", "```"]))
    assert values[0] == json.loads(FRIST)
    assert values[2] == 42
    assert len(values) == 3 and not isinstance(values[1], (dict, list, str, int, type(None)))
    assert read_answer("[\n],\n,\n]]\n\r\n") == []          # the marks of an array alone


def test_only_a_line_feed_ends_a_line():
    """JSON allows the Unicode line separators inside a text; a passage may hold one."""
    passage = "Die K\u00fcndigungsfrist betr\u00e4gt\u2028drei Monate\x85zum Monatsende."
    line = st("Die K\u00fcndigungsfrist betr\u00e4gt drei Monate.", 1,
              "K\u00fcndigungsfrist betr\u00e4gt\u2028drei Monate\x85zum Monatsende")
    shown, removed_by = _check(line + "\r", passages={1: passage})
    assert [s[2] for s in shown] == [passage[4:-1]]
    assert removed_by == _counts()


# --- the no-answer line ---------------------------------------------------------------------------


@pytest.mark.parametrize("line", [
    '{"no_answer": true}',
    '{"no_answer": true, "statement": "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate.", "source": 1, '
    '"quote": "K\u00fcndigungsfrist betr\u00e4gt drei Monate"}',
    NO_ANSWER_TEXT,
    "- " + NO_ANSWER_TEXT + ".",
    '"' + NO_ANSWER_TEXT + '"',
])
def test_the_no_answer_line_is_neither_shown_nor_counted(line):
    assert _check(line) == ([], _counts())
    assert _check(FRIST, line) == ([FRIST_SHOWN], _counts())


@pytest.mark.parametrize("line", [
    '{"no_answer": false}',
    '{"no_answer": "true"}',
    '{"no_answer": 1}',
    NO_ANSWER_TEXT + ", aber allgemein gilt eine Frist von drei Monaten.",
])
def test_a_line_that_only_looks_like_the_no_answer_line_is_counted(line):
    assert _check(line) == ([], _counts(**{NOT_A_STATEMENT: 1}))


@pytest.mark.parametrize("statement", [
    NO_ANSWER_TEXT,
    "**" + NO_ANSWER_TEXT + ".**",
    "Antwort: " + NO_ANSWER_TEXT + " [1].",
    NO_ANSWER_TEXT.upper(),
    "Dazu finde ich\nnichts in Ihren Quellen, leider.",
    "D\u0430zu finde ich nichts in Ihren Quellen.",            # a Cyrillic letter that looks like "a"
    "Dazu f\u0131nde ich n\u0131chts in Ihren Quellen.",       # dotless i
    "Dazu finde ich nichts in Ihren\u115f Quellen.",          # a letter that is not seen
    "Dazu finde ich nichts in ihren Qellen",                  # a letter left out
])
def test_the_no_answer_sentence_is_never_a_statement(statement):
    line = st(statement, 1, "K\u00fcndigungsfrist betr\u00e4gt drei Monate")
    assert _check(line) == ([], _counts(**{NOT_A_STATEMENT: 1}))


@pytest.mark.parametrize("statement", [
    "Dazu findet sich in den Quellen eine Frist von drei Monaten.",
    "In Ihren Quellen steht eine K\u00fcndigungsfrist von drei Monaten.",
    "Die Quellen nennen drei Monate; dazu finde ich die Frist im Vertrag.",
])
def test_a_statement_that_only_shares_words_with_the_no_answer_sentence_is_one(statement):
    shown, removed_by = _check(st(statement, 1, "K\u00fcndigungsfrist betr\u00e4gt drei Monate"))
    assert [s[0] for s in shown] == [statement] and removed_by == _counts()


# --- check 1: the form -----------------------------------------------------------------------------


@pytest.mark.parametrize("source", [1, "1", " 1 "])
def test_a_whole_number_or_a_digit_string_names_the_source(source):
    assert _check(st(FRIST_SHOWN[0], source, FRIST_SHOWN[2])) == ([FRIST_SHOWN], _counts())


@pytest.mark.parametrize("source", [True, 1.0, "1.0", "[1]", "1.", "Nr. 1", "", "\u0661", {"n": 1},
                                    "\x1c1", "1\n", "1234567"])
def test_any_other_source_is_not_the_form(source):
    assert _check(st(FRIST_SHOWN[0], source, FRIST_SHOWN[2])) == ([], _counts(**{NOT_A_STATEMENT: 1}))


@pytest.mark.parametrize("statement", ["1'500", "...", "[1]", "\u200b", 7, None, {"text": "Die Frist"}])
def test_a_statement_without_a_letter_is_not_a_statement(statement):
    assert _check(st(statement, 1, FRIST_SHOWN[2])) == ([], _counts(**{NOT_A_STATEMENT: 1}))


@pytest.mark.parametrize("statement", [
    "Die Frist betr\u00e4gt \u0417\u041e Tage.",               # Cyrillic letters that read "30"
    "Die K\u00fcndigungsfrist betr\u00e4gt dr\u0435i Monate.",   # a Cyrillic "e"
    "Die Frist betr\u00e4gt 5\u1160 Tage.",                    # a letter that is not seen
    "Die Dosis betr\u00e4gt f\u00fcnf \u00b5g.",                 # the micro sign: the price of the rule
    "Die Frist betr\u00e4gt \uff11\uff10 \uff34age.",          # full-width letters
])
def test_a_statement_with_a_letter_of_another_alphabet_is_not_a_statement(statement):
    assert _check(st(statement, 1, FRIST_SHOWN[2])) == ([], _counts(**{NOT_A_STATEMENT: 1}))


def test_latin_letters_with_accents_are_latin_letters():
    statement = "La dur\u00e9e est de trois mois; \u0141\u00f3d\u017a, \u00c5ngstr\u00f6m und Stra\u00dfe, drei Monate."
    assert [s[0] for s in _check(st(statement, 1, FRIST_SHOWN[2]))[0]] == [statement]


# --- what is shown: marks and unseen characters out --------------------------------------------------


@pytest.mark.parametrize("statement, shown", [
    ("Die Frist betr\u00e4gt drei Monate [1].", "Die Frist betr\u00e4gt drei Monate."),
    ("[1] Die Frist betr\u00e4gt drei Monate.", "Die Frist betr\u00e4gt drei Monate."),
    ("Die Frist [1][2] betr\u00e4gt drei Monate [1, 3].", "Die Frist betr\u00e4gt drei Monate."),
    ("Die Frist betr\u00e4gt drei Monate \u30102\u3011.", "Die Frist betr\u00e4gt drei Monate."),
    ("Er schrieb [sic] seit [dem Jahr 2023] nichts.", "Er schrieb [sic] seit [dem Jahr 2023] nichts."),
    ("  Die Frist\nbetr\u00e4gt\t drei  Monate.  ", "Die Frist betr\u00e4gt drei Monate."),
    ("Die K\u00fcndi\u00adgungs\u200bfrist\ufeff gilt\u202e.", "Die K\u00fcndigungsfrist gilt."),
    ("Die Frist\x00 gilt\x07.", "Die Frist gilt."),
    ("Die Fr\u034fist g\ufe0filt.", "Die Frist gilt."),
])
def test_the_shown_text_has_no_number_marks_and_no_unseen_characters(statement, shown):
    assert shown_text(statement) == shown


def test_a_statement_that_repeats_its_passages_number_mark_is_shown_without_it():
    # passage 1 holds no "1": the mark is not one of the statement's numbers
    marked = st("Die K\u00fcndigungsfrist betr\u00e4gt drei Monate [1].", 1, FRIST_SHOWN[2])
    assert _check(marked) == ([FRIST_SHOWN], _counts())
    other = st("Die K\u00fcndigungsfrist [7] betr\u00e4gt drei Monate [1][2].", 1, FRIST_SHOWN[2])
    assert _check(other) == ([FRIST_SHOWN], _counts())


@pytest.mark.parametrize("statement", [
    "Die Miete betr\u00e4gt CHF 5[1]00 pro Monat.",          # reads "500" once the mark is out
    "Die Miete betr\u00e4gt CHF 5\u200b00 pro Monat.",       # a zero-width space inside the number
    "Die Miete betr\u00e4gt CHF 5\u00ad00 pro Monat.",       # a soft hyphen inside the number
])
def test_a_number_cannot_hide_behind_what_is_taken_out(statement):
    passage = "Die Miete betr\u00e4gt CHF 5 pro Tag und CHF 00 pro Monat."
    assert _one(statement, passage) == ([], _counts(**{NUMBER_NOT_FOUND: 1}))


# --- check 3: the quote ----------------------------------------------------------------------------


def test_of_two_places_the_first_one_is_cut():
    passage = "Die FRIST betr\u00e4gt  drei Monate. Die Frist betr\u00e4gt drei Monate ab Zugang."
    assert find_quote("Frist betr\u00e4gt drei Monate", passage) == "FRIST betr\u00e4gt  drei Monate"


def test_a_place_inside_a_word_is_passed_over_for_a_later_whole_one():
    passage = "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate. Die Frist betr\u00e4gt drei Monate ab Zugang."
    assert find_quote("frist betr\u00e4gt drei Monate", passage) == "Frist betr\u00e4gt drei Monate"
    assert find_quote("frist betr\u00e4gt drei Monate", passage[:45]) is None


def test_soft_hyphens_and_line_breaks_in_the_passage_do_not_matter():
    passage = ("Die K\u00fcndi\u00adgungs\u00adfrist be\u00ad\ntr\u00e4gt\r\n   drei\u00a0Monate\n"
               "zum Monats\u00ad  ende.")
    quote = "K\u00fcndigungsfrist betr\u00e4gt drei Monate zum Monatsende"
    cut = find_quote(quote, passage)
    assert cut == passage[4:-1]                    # the passage's own characters, hyphens and breaks kept
    shown, removed_by = _check(st("Die K\u00fcndigungsfrist betr\u00e4gt drei Monate.", 1, quote),
                               passages={1: passage})
    assert [s[2] for s in shown] == [cut]
    assert removed_by == _counts()


def test_a_hard_hyphen_at_a_line_break_stays_a_hyphen():
    passage = "Die K\u00fcndi-\ngungsfrist betr\u00e4gt drei Monate."
    assert find_quote("K\u00fcndigungsfrist betr\u00e4gt drei Monate", passage) is None
    assert find_quote("K\u00fcndi- gungsfrist betr\u00e4gt drei Monate", passage) == passage[4:-1]


def test_a_word_broken_with_a_hyphen_at_a_line_end_is_one_word():
    passage = "Die Untervermietung ist in diesem Fall un-\nzul\u00e4ssig, sofern der Vermieter nicht zustimmt."
    assert find_quote("zul\u00e4ssig, sofern der Vermieter", passage) is None           # starts inside "unzulaessig"
    assert find_quote("ist in diesem Fall un-", passage) is None                        # ends inside it
    assert find_quote("un- zul\u00e4ssig, sofern der Vermieter", passage) == passage[39:-16]
    listed = "Der Miet- und Pachtvertrag ist k\u00fcndbar."
    assert find_quote("und Pachtvertrag ist k\u00fcndbar", listed) is None                # tied to "Miet-"
    assert find_quote("Miet- und Pachtvertrag ist", listed) == "Miet- und Pachtvertrag ist"
    accent = "Die Erkl\u00e4-\nrung des Mieters ist g\u00fcltig."                           # an accent before the hyphen
    assert find_quote("rung des Mieters ist g\u00fcltig", accent) is None
    assert find_quote("rung des Mieters ist g\u00fcltig", unicodedata.normalize("NFD", accent)) is None


def test_a_number_written_with_spaces_is_one_word():
    passage = "Es gilt eine Frist von 3 000 Tagen ab Zustellung; der Betrag ist 1 500\n000 Franken."
    assert find_quote("eine Frist von 3", passage) is None                              # ends inside 3 000
    assert find_quote("000 Tagen ab Zustellung", passage) is None                       # starts inside it
    assert find_quote("eine Frist von 3 000 Tagen", passage) == "eine Frist von 3 000 Tagen"
    assert find_quote("1 500 000", passage) is None                                     # one word, not three
    assert find_quote("ist 1 500 000 Franken", passage) == "ist 1 500\n000 Franken"
    assert find_quote("500 Franken pro Monat", "Die Miete ist CHF 1' 500 Franken pro Monat.") is None


def test_composed_and_decomposed_accents_are_the_same_letters():
    decomposed = "Die Ku\u0308ndigungsfrist betra\u0308gt drei Monate."
    composed = "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate."
    assert find_quote("K\u00fcndigungsfrist betr\u00e4gt drei Monate", decomposed) == decomposed[4:-1]
    assert find_quote("Ku\u0308ndigungsfrist betra\u0308gt drei Monate", composed) == composed[4:-1]
    assert find_quote("Kundigungsfrist betragt drei Monate", composed) is None
    assert find_quote("Kuendigungsfrist betraegt drei Monate", composed) is None


def test_marks_that_are_not_seen_do_not_matter_in_a_quote():
    passage = "Die K\u00fcndigungs\u034ffrist betr\u00e4gt\ufe0f drei Monate."
    assert find_quote("K\u00fcndigungsfrist betr\u00e4gt drei Monate", passage) == passage[4:-1]
    assert find_quote("K\u00fcndigungsfrist betr\u00e4gt\u034f drei Monate", FRIST_TEXT) == FRIST_SHOWN[2]


@pytest.mark.parametrize("quote, passage", [
    # the start is inside a word of the passage
    ("K\u00fcndigung ist zul\u00e4ssig", "Die Nicht-K\u00fcndigung ist zul\u00e4ssig."),
    ("500 pro Monat", "Die Miete betr\u00e4gt CHF 1'500 pro Monat."),
    ("500 pro Monat", "Die Miete betr\u00e4gt CHF 1.500 pro Monat."),
    ("5 Prozent Zins", "Geschuldet sind 3,5 Prozent Zins."),
    ("30 Tagen ab Zugang", "Innert >30 Tagen ab Zugang."),
    ("500 Franken netto", "Der Saldo betr\u00e4gt -500 Franken netto."),
    ("Sie beginnt mit dem Zugang", "Die Frist betr\u00e4gt drei Monate.Sie beginnt mit dem Zugang."),
    ("oder der Vermieter k\u00fcndigt", "Der Mieter und/oder der Vermieter k\u00fcndigt."),
    ("r alle Mieter", "Die Frist gilt fu\u0308r alle Mieter."),
    # the end is inside a word of the passage
    ("Die Miete betr\u00e4gt CHF 1", "Die Miete betr\u00e4gt CHF 1'500 pro Monat."),
    ("Die Miete betr\u00e4gt 1500", "Die Miete betr\u00e4gt 1500.50 Franken."),
    ("ein Zins von 5", "Geschuldet ist ein Zins von 5% pro Jahr."),
    ("gilt das K\u00fcndigungs", "Hier gilt das K\u00fcndigungs- und Mietrecht."),
    ("Die Frist gilt fu", "Die Frist gilt fu\u0308r alle Mieter."),
    ("betr\u00e4gt Fr. 1'500", "Die Miete betr\u00e4gt Fr. 1'500.\u2013 pro Monat."),
])
def test_a_quote_covers_whole_words_of_the_passage(quote, passage):
    assert find_quote(quote, passage) is None


@pytest.mark.parametrize("quote, passage, cut", [
    ("vgl. Art. 5 OR", "Das gilt (vgl. Art. 5 OR) auch hier.", "vgl. Art. 5 OR"),
    ("Ich k\u00fcndige den Vertrag", "Er schrieb: \u00abIch k\u00fcndige den Vertrag.\u00bb",
     "Ich k\u00fcndige den Vertrag"),
    ("\"Ich k\u00fcndige den Vertrag.\"", "Er schrieb: \u00abIch k\u00fcndige den Vertrag.\u00bb",
     "\u00abIch k\u00fcndige den Vertrag.\u00bb"),
    ("\u201eIch k\u00fcndige den Vertrag\u201c", "Er schrieb, ich k\u00fcndige den Vertrag heute.",
     "ich k\u00fcndige den Vertrag"),
    ("betr\u00e4gt Fr. 1'500.-", "Die Miete betr\u00e4gt Fr. 1\u2019500.\u2013 pro Monat.",
     "betr\u00e4gt Fr. 1\u2019500.\u2013"),
    ("und der Vermieter k\u00fcndigt", "Der Mieter - und der Vermieter k\u00fcndigt.",
     "und der Vermieter k\u00fcndigt"),
    (">30 Tagen ab Zugang", "Innert >30 Tagen ab Zugang.", ">30 Tagen ab Zugang"),
])
def test_marks_around_the_first_and_the_last_word_may_be_left_out(quote, passage, cut):
    assert find_quote(quote, passage) == cut


@pytest.mark.parametrize("quote", [
    "K\u00fcndigungsfrist betr\u00e4gt drei Monate.",       # the passage goes on after "Monate"
    "K\u00fcndigungsfrist betr\u00e4gt drei Monate ...",
    "K\u00fcndigungsfrist betr\u00e4gt drei Monate\u2026",
    "K\u00fcndigungsfrist, betr\u00e4gt drei Monate",
    "K\u00fcndigungsfrist betr\u00e4gt drei  Monate zum Monats ende",
    "Kuendigungsfrist betraegt drei Monate",
])
def test_punctuation_and_spelling_the_model_adds_are_not_in_the_passage(quote):
    assert find_quote(quote, FRIST_TEXT) is None


@pytest.mark.parametrize("quote, passage", [
    ("\u00a7 5 OR", "Nach \u00a7 5 OR gilt das."),
    ("- - - -", "Es gilt - - - - nichts."),
    ("\u00ab \u00bb drei Monate", "Es gilt \u00ab \u00bb drei Monate lang."),
    ("\"drei Monate\"", "Die Frist betr\u00e4gt drei Monate."),
])
def test_marks_alone_are_not_words(quote, passage):
    assert find_quote(quote, passage) is None


def test_three_words_with_digits_are_three_words():
    assert find_quote("Art. 5 OR", "Nach Art. 5 OR gilt das.") == "Art. 5 OR"


# --- check 4: the numbers --------------------------------------------------------------------------


def _fenced(text):
    return text.replace(chr(1), "<").replace(chr(2), ">")


def _claimed(statement):
    return {_fenced(claim) for claim in claimed_numbers(statement)}


@pytest.mark.parametrize("text, claims", [
    # thousands: an apostrophe is not written, a space is
    ("CHF 1'500", {"<1500>"}),
    ("CHF 1\u2019500", {"<1500>"}),
    ("CHF 1500", {"<1500>"}),
    ("CHF 1 500", {"<1 500>"}),
    ("CHF 1\u202f500\u00a0000", {"<1 500 000>"}),
    ("CHF 1\u2002500 und 2  500", {"<1 500>", "<2 500>"}),                  # white space of any kind is one space
    ("CHF 1' 500", {"<1 500>"}),
    ("CHF 12\n500 und 3\t000", {"<12 500>", "<3 000>"}),                  # a statement has no lines
    ("CHF 1\u00ad500, 2\u200b500 und 3\u034f500", {"<1500>", "<2500>", "<3500>"}),   # what is not seen does not separate
    ("2023 150 F\u00e4lle", {"<2023>", "<150>"}),
    ("12 3456", {"<12>", "<3456>"}),
    # a stop or a comma between digits ties them
    ("CHF 1'500.50", {"<1500.50>"}),
    ("CHF 1'500.\u2013", {"<1500>"}),
    ("EUR 1.500", {"<1.500>"}),
    ("3,5 %", {"<3,5>"}),
    ("am 31.12.2025.", {"<31.12.2025>"}),
    ("am 1. M\u00e4rz 2024.", {"<1>", "<2024>"}),
    # what belongs to a number: letters and accents after it, one letter alone after it, a sign before it
    ("Art. 266a OR", {"<266a>"}),
    ("ART. 266A OR", {"<266a>"}),
    ("Art. 12bis", {"<12bis>"}),
    ("Art. 266 a OR", {"<266 a>"}),
    ("Art. 266-a OR", {"<266-a>"}),
    ("Art. 266 OR", {"<266>"}),
    ("5 e-mails und 6 l'an", {"<5>", "<6>"}),
    ("Format A4", {"<4>"}),
    ("Saldo -500 und \u2212200, Tel. +41", {"<-500>", "<-200>", "<+41>"}),
    ("Art. 266\u0363", {"<266\u0363>"}),
    # numbers that are told apart: a word, white space, list and sentence marks, brackets, a dash
    ("Art. 5, 6 und 7", {"<5>", "<6>", "<7>"}),
    ("Art. 5 (2); 2024", {"<5>", "<2>", "<2024>"}),
    ("5-7 Tage, 2023\u20132024, 8 - 9", {"<5>", "<7>", "<2023>", "<2024>", "<8>", "<9>"}),
    # numbers with any other mark between them are one claim, as they stand
    ("2/3 Mehrheit", {"<2>/<3>"}),
    ("2 / 3 Mehrheit", {"<2> / <3>"}),
    ("um 14:30 Uhr", {"<14>:<30>"}),
    ("5% - 7%", {"<5>% - <7>"}),
    ("1_500", {"<1>_<500>"}),
    ("3a2", {"<3a><2>"}),
    ("3'30", {"<3>'<30>"}),                      # no thousands mark: three digits must follow
    ("1\u201a5", {"<1>\u201a<5>"}),              # a low quotation mark is not an apostrophe
    ("3\u20245", {"<3>\u2024<5>"}),
    ("1\u2800500", {"<1>\u2800<500>"}),
    ("5 \u03017", {"<5> \u0301<7>"}),
    # raised digits, fractions and number signs
    ("Art. 271a\u00b9 und 50 m\u00b2", {"<271a\u00b9>", "<50>", "<\u00b2>"}),
    ("10\u00b3 Franken, 3\u00bd Zimmer", {"<10\u00b3>", "<3\u00bd>"}),
    ("CHF \u2075\u2070\u2070, \u00bd Jahr, Ziff. \u2460", {"<\u2075\u2070\u2070>", "<\u00bd>", "<\u2460>"}),
    # digits of another script and marks on a digit stay as written
    ("\uff13\uff10 Tage", {"<\uff13\uff10>"}),
    ("\u09ea Wochen", {"<\u09ea>"}),
    ("CHF 5\u03360\u03360\u0336", {"<5\u0336><0\u0336><0\u0336>"}),
    # the German words for 2 to 12
    ("drei Monate", {"<3>"}),
    ("Zw\u00f6lf Monate, zwoelf Tage, F\u00dcNF Jahre", {"<12>", "<5>"}),
    ("in Acht Tagen, ausser Acht gelassen", {"<8>"}),      # a statement is read strictly
    ("eins, eine Woche, ein Jahr, einem Monat", set()),
    ("dreimonatig, Dreizehn, zwanzig", set()),
    ("keine Zahl", set()),
])
def test_what_a_statement_claims(text, claims):
    assert _claimed(text) == claims


@pytest.mark.parametrize("passage, holds, lacks", [
    ("Die Regel gilt 2023-2024.", ["<2023>", "<2024>"], ["<2023-2024>"]),
    ("Es braucht 2/3 der Stimmen.", ["<2>/<3>", "<2>", "<3>"], ["<2> / <3>"]),
    ("Art. 4, 5, 6 und 7", ["<4>", "<5>", "<6>", "<7>"], ["<4>, <5>"]),
    ("um 14:30 Uhr", ["<14>:<30>", "<14>", "<30>"], ["<14>.<30>"]),
    ("CHF 1 500 pro Monat", ["<1 500>"], ["<1500>", "<1>", "<500>"]),
    ("CHF 1'500.50 pro Monat", ["<1500.50>"], ["<1500>", "<50>", "<1>"]),
    ("am 31.12.2025", ["<31.12.2025>"], ["<31>", "<12>", "<2025>"]),
    # a line break or a tab between two numbers: neither reading is offered
    ("nach Absatz 2\n500 Franken Busse", [], ["<2>", "<500>", "<2 500>", "<2500>"]),
    ("CHF 12\t500 pro Jahr", [], ["<12>", "<500>", "<12 500>", "<12500>"]),
    ("Absatz 2\nDie Busse betr\u00e4gt 500 Franken", ["<2>", "<500>"], ["<2 500>"]),
    # readings a passage offers besides the strict one
    ("Art. 271a\u00b9 OR", ["<271a\u00b9>", "<271a>", "<\u00b9>"], ["<271>"]),
    ("Art. 266 a contrario", ["<266 a>", "<266>"], ["<266a>"]),
    ("Art. 266a OR", ["<266a>"], ["<266>", "<266 a>"]),
    ("Art. 266\u0363 OR", ["<266\u0363>"], ["<266>"]),
    ("Saldo -500 Franken", ["<-500>"], ["<500>"]),
    # words
    ("drei Monate, in acht Tagen", ["<3>", "<8>"], ["<1>"]),
    ("nicht ausser Acht lassen", [], ["<8>"]),
    ("Darauf ist Acht zu geben; er gibt acht und hat es in Acht genommen.", [], ["<8>"]),
    ("Die Parteien sind sich eins \u00fcber eine Frist.", [], ["<1>"]),
])
def test_what_a_passage_offers(passage, holds, lacks):
    offered = _fenced(offered_numbers(passage))
    assert [claim for claim in holds if claim not in offered] == []
    assert [claim for claim in lacks if claim in offered] == []


@pytest.mark.parametrize("statement, passage", [
    # a thousands apostrophe on one side only, either side
    ("Die Miete betr\u00e4gt CHF 1500.", "Die Miete betr\u00e4gt CHF 1\u2019500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 1'500.", "Die Miete betr\u00e4gt CHF 1500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 1'500.", "Die Miete betr\u00e4gt CHF 1'500.\u2013 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 1 500.", "Die Miete betr\u00e4gt CHF 1\u202f500 pro Monat."),
    # 2 to 12 as a word on either side
    ("Die Frist betr\u00e4gt 3 Monate.", "Die Frist betr\u00e4gt drei Monate."),
    ("Die Frist betr\u00e4gt drei Monate.", "Die Frist betr\u00e4gt 3 Monate."),
    ("Die Frist betr\u00e4gt Drei Monate.", "Die Frist betr\u00e4gt drei Monate."),
    ("Die Frist betr\u00e4gt acht Tage.", "Die Frist betr\u00e4gt in der Regel acht Tage."),
    # as the passage writes it
    ("Die Frist endet am 31.12.2025.", "Die Frist endet am 31.12.2025, sp\u00e4testens."),
    ("Die Fl\u00e4che betr\u00e4gt 85 m\u00b2.", "Die Fl\u00e4che betr\u00e4gt 85 m\u00b2 netto."),
    ("Es braucht eine 2/3 Mehrheit.", "Es braucht eine 2/3 Mehrheit der Stimmen."),
    ("Massgebend ist Art. 266 a OR.", "Massgebend ist Art. 266 a OR, nicht Art. 271."),
    ("Der Saldo betr\u00e4gt -500 Franken.", "Der Saldo betr\u00e4gt \u2212500 Franken netto."),
    # numbers that are told apart may come from different places of the passage
    ("Die Regel gilt 2023\u20132024.", "Die Regel gilt von 2023 bis 2024."),
    ("Die Regel gilt ab 2023.", "Die Regel gilt 2023-2024 f\u00fcr alle."),
    ("Die Lieferfrist betr\u00e4gt 5-7 Tage.", "Die Lieferfrist betr\u00e4gt 5 bis 7 Tage."),
    ("Massgebend sind Art. 266a, 266b und 271 OR.", "Massgebend sind Art. 266a und 266b sowie Art. 271 OR."),
    ("Massgebend ist Art. 271a OR.", "Massgebend ist Art. 271a\u00b9 OR."),          # a footnote mark
])
def test_a_number_written_as_the_passage_writes_it_is_found(statement, passage):
    shown, removed_by = _one(statement, passage)
    assert [s[0] for s in shown] == [statement]
    assert removed_by == _counts()


@pytest.mark.parametrize("statement, passage", [
    # a piece of a number is not the number
    ("Die Miete betr\u00e4gt CHF 500.", "Die Miete betr\u00e4gt CHF 1'500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 500.", "Die Miete betr\u00e4gt CHF 1 500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 500.", "Die Miete betr\u00e4gt CHF 1.500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 500.", "Die Miete betr\u00e4gt CHF 1\u00ad500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 500.", "Die Miete betr\u00e4gt CHF 1\u2008500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 500.", "Die Miete betr\u00e4gt CHF 1' 500 pro Jahr."),
    ("Die Miete betr\u00e4gt CHF 50.", "Die Miete betr\u00e4gt CHF 1.50 pro Tag."),
    ("Die Miete betr\u00e4gt CHF 15.", "Die Miete betr\u00e4gt CHF 1'500 pro Monat."),
    # a line break or a tab between two numbers of the passage: neither reading
    ("Die Miete betr\u00e4gt CHF 500.", "Die Miete betr\u00e4gt CHF 12\n500 pro Jahr."),
    ("Die Miete betr\u00e4gt CHF 500.", "Die Miete betr\u00e4gt CHF 12\t500 pro Jahr."),
    ("Die Miete betr\u00e4gt CHF 12 500.", "Die Miete betr\u00e4gt CHF 12\n500 pro Jahr."),
    ("Die Busse betr\u00e4gt 2 500 Franken.", "Die Busse folgt aus Absatz 2\n500 Franken sind geschuldet."),
    # the letter of an article
    ("Massgebend ist Art. 266b OR.", "Massgebend ist Art. 266a OR."),
    ("Massgebend ist Art. 266 OR.", "Massgebend ist Art. 266a OR."),
    ("Massgebend ist Art. 266a OR.", "Massgebend ist Art. 266 OR."),
    ("Massgebend ist Art. 266 a OR.", "Massgebend ist Art. 266 OR."),
    ("Massgebend ist Art. 266-a OR.", "Massgebend ist Art. 266 OR."),
    ("Massgebend ist Art. 266a OR.", "Massgebend ist Art. 266 a contrario."),
    ("Massgebend ist Art. 257f OR.", "Massgebend ist Art. 257 f. OR."),
    ("Massgebend ist Art. 266\u0363 OR.", "Massgebend ist Art. 266 OR."),
    # words, other digits, signs
    ("Die Frist betr\u00e4gt sechs Monate.", "Die Frist betr\u00e4gt drei Monate."),
    ("Die Frist betr\u00e4gt sechs Monate.", "Die Frist betr\u00e4gt 3 Monate."),
    ("Die Frist betr\u00e4gt 8 Tage.", "Das darf nicht ausser Acht gelassen werden. Die Frist betr\u00e4gt 30 Tage."),
    ("Die K\u00fcndigung wirkt in Acht Tagen.", "Die K\u00fcndigung wirkt in sechs Monaten."),
    ("Die Frist betr\u00e4gt 1 Tag.", "Die Parteien sind sich eins. Die Frist betr\u00e4gt 30 Tage."),
    ("Die Frist betr\u00e4gt \uff16 Monate.", "Die Frist betr\u00e4gt drei Monate."),
    ("Die Frist betr\u00e4gt \u09ea Wochen.", "Die Frist betr\u00e4gt 4 Wochen."),         # looks like an 8
    ("Die Frist betr\u00e4gt 1 Monat.", "Die Frist betr\u00e4gt einen Monat ab Zugang."),
    ("Die Frist betr\u00e4gt 13 Monate.", "Die Frist betr\u00e4gt 1 Jahr und 3 Monate."),
    ("Die Busse betr\u00e4gt CHF \u2075\u2070\u2070.", "Die Busse betr\u00e4gt CHF 5 bis 500."),
    ("Die Busse betr\u00e4gt CHF 5\u033600\u0336.", "Die Busse betr\u00e4gt CHF 5 pro Tag, um 10:00 Uhr."),
    ("Die Busse betr\u00e4gt 10\u00b3 Franken.", "Die Busse betr\u00e4gt 10 Franken \u00b3 und ist f\u00e4llig."),
    ("Die Fl\u00e4che betr\u00e4gt 85 m\u00b3.", "Die Fl\u00e4che betr\u00e4gt 85 m\u00b2."),
    ("Der Saldo betr\u00e4gt -500 Franken.", "Der Saldo betr\u00e4gt 500 Franken."),
    # a number put together from numbers of the passage
    ("Es entscheidet eine 2/3 Mehrheit.", "Es entscheidet nach Art. 2 Abs. 3 die Mehrheit."),
    ("Es entscheidet eine 2 / 3 Mehrheit.", "Es entscheidet nach Art. 2 Abs. 3 die Mehrheit."),
    ("Die Busse betr\u00e4gt 1_500 Franken.", "Die Busse betr\u00e4gt 1 Franken, h\u00f6chstens 500 Franken."),
    ("Die Busse betr\u00e4gt 1\u2800500 Franken.", "Die Busse betr\u00e4gt 1 Franken, h\u00f6chstens 500 Franken."),
    ("Die Busse betr\u00e4gt 1 \u034f500 Franken.", "Die Busse nach Absatz 1 betr\u00e4gt 500 Franken."),
    ("Der Zins betr\u00e4gt 3\u20245 Prozent.", "Der Zins betr\u00e4gt 3 Prozent, nach 5 Jahren mehr."),
    ("Der Zins betr\u00e4gt 1\u201a5 Prozent.", "Der Zins betr\u00e4gt 15 Prozent pro Jahr."),
    ("Die Frist betr\u00e4gt 3'30 Tage.", "Die Frist betr\u00e4gt 330 Tage."),
    ("Die Busse nach Abs. 1 500 Franken.", "Die Busse betr\u00e4gt 1'500 Franken."),
    # shown from right to left: two numbers change places
    ("Die Busse betr\u00e4gt \u05be500 1 Franken.", "Die Busse betr\u00e4gt 1 Franken, h\u00f6chstens 500 Franken."),
    ("Die Busse betr\u00e4gt \u0665\u0660\u0660 \u0661 Franken.", "Die Busse betr\u00e4gt \u0665\u0660\u0660 \u0661 Franken."),
    # as written, never by value
    ("Der Vertrag stammt von 2024.", "Der Vertrag wurde am 01.03.2024 unterzeichnet."),
    ("Die Miete betr\u00e4gt CHF 1'500.", "Die Miete betr\u00e4gt CHF 1'500.00 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 1'500.", "Die Miete betr\u00e4gt CHF 1 500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 1 500.", "Die Miete betr\u00e4gt CHF 1'500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 1,500.", "Die Miete betr\u00e4gt CHF 1'500 pro Monat."),
    ("Der Zins betr\u00e4gt 3.5 Prozent.", "Der Zins betr\u00e4gt 3,5 Prozent."),
    ("Massgebend ist Art. 266 a OR.", "Massgebend ist Art. 266a OR."),
])
def test_a_number_the_passage_does_not_write_removes_the_statement(statement, passage):
    assert _one(statement, passage) == ([], _counts(**{NUMBER_NOT_FOUND: 1}))


def test_the_numbers_are_looked_for_in_the_statements_own_passage_only():
    line = st("Die K\u00fcndigungsfrist betr\u00e4gt drei Monate seit 2024.", 1, FRIST_SHOWN[2])
    assert _check(line) == ([], _counts(**{NUMBER_NOT_FOUND: 1}))      # 2024 stands in passage 3


# --- check 5: the length ---------------------------------------------------------------------------


def test_the_length_is_measured_on_the_shown_text():
    base = "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate"
    longest = base + " " + "x" * (MAX_STATEMENT_CHARS - len(base) - 1)
    assert len(longest) == MAX_STATEMENT_CHARS
    shown, removed_by = _check(st(longest + " [1]", 1, FRIST_SHOWN[2]))
    assert [s[0] for s in shown] == [longest] and removed_by == _counts()
    assert _check(st(longest + "x", 1, FRIST_SHOWN[2])) == ([], _counts(**{TOO_LONG: 1}))
