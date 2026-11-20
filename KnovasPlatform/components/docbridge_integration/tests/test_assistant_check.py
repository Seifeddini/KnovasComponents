"""PMVP-20, rework 2: the check of the answer (assistant.check) beyond the frozen acceptance tests.

The model answers in statements, one JSON object per line, each with one
source and a word-for-word quote. These tests pin what the frozen file
(tests/test_pmvp20_statements_acceptance.py) leaves open: how a line is read,
where a quote may start and end, what a number is, and which reason a removed
statement is counted under.
"""
from __future__ import annotations

import json

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
    find_quote,
    numbers_in,
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
])
def test_the_no_answer_sentence_is_never_a_statement(statement):
    line = st(statement, 1, "K\u00fcndigungsfrist betr\u00e4gt drei Monate")
    assert _check(line) == ([], _counts(**{NOT_A_STATEMENT: 1}))


# --- check 1: the form -----------------------------------------------------------------------------


@pytest.mark.parametrize("source", [1, "1", " 1 "])
def test_a_whole_number_or_a_digit_string_names_the_source(source):
    assert _check(st(FRIST_SHOWN[0], source, FRIST_SHOWN[2])) == ([FRIST_SHOWN], _counts())


@pytest.mark.parametrize("source", [True, 1.0, "1.0", "[1]", "1.", "Nr. 1", "", "\u0661", {"n": 1}])
def test_any_other_source_is_not_the_form(source):
    assert _check(st(FRIST_SHOWN[0], source, FRIST_SHOWN[2])) == ([], _counts(**{NOT_A_STATEMENT: 1}))


@pytest.mark.parametrize("statement", ["1'500", "...", "[1]", "\u200b", 7, None, {"text": "Die Frist"}])
def test_a_statement_without_a_letter_is_not_a_statement(statement):
    assert _check(st(statement, 1, FRIST_SHOWN[2])) == ([], _counts(**{NOT_A_STATEMENT: 1}))


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


def test_composed_and_decomposed_accents_are_the_same_letters():
    decomposed = "Die Ku\u0308ndigungsfrist betra\u0308gt drei Monate."
    composed = "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate."
    assert find_quote("K\u00fcndigungsfrist betr\u00e4gt drei Monate", decomposed) == decomposed[4:-1]
    assert find_quote("Ku\u0308ndigungsfrist betra\u0308gt drei Monate", composed) == composed[4:-1]
    assert find_quote("Kundigungsfrist betragt drei Monate", composed) is None
    assert find_quote("Kuendigungsfrist betraegt drei Monate", composed) is None


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


@pytest.mark.parametrize("text, numbers", [
    ("CHF 1'500", {"1500"}),
    ("CHF 1\u2019500", {"1500"}),
    ("CHF 1500", {"1500"}),
    ("CHF 1 500", {"1500"}),
    ("CHF 1\u202f500\u00a0000", {"1500000"}),
    ("CHF 1\u2002500 und 2  500", {"1500", "2500"}),                # white space of any kind is one space
    ("CHF 1\u00ad500 und 2\u200b500", {"1500", "2500"}),            # what is not seen does not separate
    ("Art. 5\n300 Franken und 100\t200", {"5", "300", "100", "200"}),      # a line break and a tab do
    ("CHF 1'500.50", {"1500.50"}),
    ("CHF 1'500.\u2013", {"1500"}),
    ("EUR 1.500", {"1.500"}),
    ("3,5 %", {"3,5"}),
    ("am 31.12.2025.", {"31.12.2025"}),
    ("am 1. M\u00e4rz 2024.", {"1", "2024"}),
    ("Art. 266a OR", {"266a"}),
    ("ART. 266A OR", {"266a"}),
    ("Art. 12bis", {"12bis"}),
    ("2023-2024", {"2023", "2024"}),
    ("2023 150 F\u00e4lle", {"2023", "150"}),
    ("12 3456", {"12", "3456"}),
    ("Art. 5, 6 und 7", {"5", "6", "7"}),
    ("Format A4", {"4"}),
    ("Art. 271a\u00b9 und 50 m\u00b2", {"271a", "\u00b9", "50", "\u00b2"}),      # raised: never part of 271a
    ("Abs. 2\u00b3", {"2", "\u00b3"}),
    ("CHF \u2075\u2070\u2070, \u00bd Jahr, Ziff. \u2460", {"\u2075\u2070\u2070", "\u00bd", "\u2460"}),
    ("CHF 5\u03360\u03360\u0336", {"500"}),                                           # struck through
    ("\uff13\uff10 Tage", {"30"}),
    ("drei Monate", {"3"}),
    ("Zw\u00f6lf Monate, zwoelf Tage, F\u00dcNF Jahre", {"12", "5"}),
    ("eins", {"1"}),
    ("eine Woche, ein Jahr, einem Monat", set()),
    ("dreimonatig, Dreizehn, zwanzig", set()),
    ("keine Zahl", set()),
])
def test_what_a_number_is(text, numbers):
    assert numbers_in(text) == numbers


@pytest.mark.parametrize("statement, passage", [
    # a thousands apostrophe on one side only, either side
    ("Die Miete betr\u00e4gt CHF 1500.", "Die Miete betr\u00e4gt CHF 1\u2019500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 1'500.", "Die Miete betr\u00e4gt CHF 1500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 1'500.", "Die Miete betr\u00e4gt CHF 1 500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 1'500.", "Die Miete betr\u00e4gt CHF 1'500.\u2013 pro Monat."),
    # 1 to 12 as a word on either side
    ("Die Frist betr\u00e4gt 3 Monate.", "Die Frist betr\u00e4gt drei Monate."),
    ("Die Frist betr\u00e4gt drei Monate.", "Die Frist betr\u00e4gt 3 Monate."),
    ("Die Frist betr\u00e4gt Drei Monate.", "Die Frist betr\u00e4gt drei Monate."),
    ("Die Frist endet am 31.12.2025.", "Die Frist endet am 31.12.2025, sp\u00e4testens."),
    ("Die Fl\u00e4che betr\u00e4gt 85 m\u00b2.", "Die Fl\u00e4che betr\u00e4gt 85 m\u00b2 netto."),
])
def test_a_number_written_as_the_passage_writes_it_is_found(statement, passage):
    shown, removed_by = _one(statement, passage)
    assert [s[0] for s in shown] == [statement]
    assert removed_by == _counts()


@pytest.mark.parametrize("statement, passage", [
    ("Die Miete betr\u00e4gt CHF 500.", "Die Miete betr\u00e4gt CHF 1'500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 500.", "Die Miete betr\u00e4gt CHF 1 500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 500.", "Die Miete betr\u00e4gt CHF 1.500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 500.", "Die Miete betr\u00e4gt CHF 1\u00ad500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 500.", "Die Miete betr\u00e4gt CHF 1\u2008500 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 50.", "Die Miete betr\u00e4gt CHF 1.50 pro Tag."),
    ("Die Miete betr\u00e4gt CHF 15.", "Die Miete betr\u00e4gt CHF 1'500 pro Monat."),
    ("Massgebend ist Art. 266b OR.", "Massgebend ist Art. 266a OR."),
    ("Massgebend ist Art. 266 OR.", "Massgebend ist Art. 266a OR."),
    ("Massgebend ist Art. 266a OR.", "Massgebend ist Art. 266 OR."),
    ("Die Frist betr\u00e4gt sechs Monate.", "Die Frist betr\u00e4gt drei Monate."),
    ("Die Frist betr\u00e4gt sechs Monate.", "Die Frist betr\u00e4gt 3 Monate."),
    ("Die Frist betr\u00e4gt \uff16 Monate.", "Die Frist betr\u00e4gt drei Monate."),
    ("Die Frist betr\u00e4gt 1 Monat.", "Die Frist betr\u00e4gt einen Monat ab Zugang."),
    ("Die Frist betr\u00e4gt 13 Monate.", "Die Frist betr\u00e4gt 1 Jahr und 3 Monate."),
    ("Die Busse betr\u00e4gt CHF \u2075\u2070\u2070.", "Die Busse betr\u00e4gt CHF 5 bis 500."),
    ("Die Busse betr\u00e4gt CHF 5\u033600\u0336.", "Die Busse betr\u00e4gt CHF 5 pro Tag, um 10:00 Uhr."),
    ("Die Fl\u00e4che betr\u00e4gt 85 m\u00b3.", "Die Fl\u00e4che betr\u00e4gt 85 m\u00b2."),
    # as written, never by value
    ("Der Vertrag stammt von 2024.", "Der Vertrag wurde am 01.03.2024 unterzeichnet."),
    ("Die Miete betr\u00e4gt CHF 1'500.", "Die Miete betr\u00e4gt CHF 1'500.00 pro Monat."),
    ("Die Miete betr\u00e4gt CHF 1,500.", "Die Miete betr\u00e4gt CHF 1'500 pro Monat."),
    ("Der Zins betr\u00e4gt 3.5 Prozent.", "Der Zins betr\u00e4gt 3,5 Prozent."),
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
