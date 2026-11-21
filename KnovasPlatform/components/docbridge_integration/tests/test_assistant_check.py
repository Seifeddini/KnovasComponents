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
    NO_ANSWER_WORDINGS,
    TOO_LONG,
    UNKNOWN_SOURCE,
    check_answer,
    check_numbers,
    claimed_numbers,
    find_quote,
    locate_quote,
    offered_numbers,
    read_answer,
    says_no_answer,
    shown_text,
    Statement,
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
    """One statement against one passage; the quote defaults to the whole passage, so the
    passage's reading is tested (the numbers are looked for in the quote only)."""
    return _check(st(statement, 1, quote or passage), passages={1: passage})


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


@pytest.mark.parametrize("line", ["```" + FRIST, "```json " + FRIST, "``` " + FRIST, "````jsonl\t" + FRIST])
def test_an_object_after_a_code_fence_on_its_line_is_counted(line):
    # the verifier's input: such a line was dropped like a fence and not counted
    assert _check(line, MIETE) == ([MIETE_SHOWN], _counts(**{NOT_A_STATEMENT: 1}))


@pytest.mark.parametrize("fence", ["```", "```json", "``` JSON", "````", "```jsonl  "])
def test_a_line_that_only_opens_or_closes_a_fence_gives_nothing(fence):
    assert _check(fence, FRIST, fence) == ([FRIST_SHOWN], _counts())


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
    # "eine" is a number too (hardening): the quote holds it
    passage = "Es gilt eine Frist von drei Monaten, also eine K\u00fcndigungsfrist von drei Monaten."
    shown, removed_by = _one(statement, passage)
    assert [s[0] for s in shown] == [statement] and removed_by == _counts()


@pytest.mark.parametrize("statement", [
    "Dazu finde ich nichts in Ihren Unterlagen.",               # the verifier's two wordings
    "In Ihren Quellen finde ich dazu nichts.",
    "DAZU  FINDE ICH NICHTS in den Dokumenten.",
    "Hierzu finde ich nichts in den Passagen.",
    "Ich finde dazu nichts in Ihren Akten.",
    "Dazu finde ich keine Angaben in Ihren Quellen.",
    "Dazu habe ich nichts gefunden.",
    "Leider kann ich dazu nichts finden.",
    "Dar\u00fcber finde ich nichts.",
    "I cannot find anything about this in your sources.",
])
def test_another_wording_of_the_no_answer_sentence_is_not_a_statement(statement):
    # the quote is valid and the statement holds no number: only the wording removes it
    line = st(statement, 1, "K\u00fcndigungsfrist betr\u00e4gt drei Monate")
    assert _check(line) == ([], _counts(**{NOT_A_STATEMENT: 1}))


def test_the_other_wordings_are_a_short_closed_list():
    assert len(NO_ANSWER_WORDINGS) <= 30
    # a statement that only uses some of their words is one, also one that says a document lacks something
    for statement in ("Dazu finde ich die Frist im Vertrag.", "Im Vertrag steht nichts zur Kaution.",
                      "Der Vertrag enth\u00e4lt keine Regelung zur Kaution.",
                      "Die Unterlagen m\u00fcssen nicht beglaubigt sein."):
        assert says_no_answer(statement) is False


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
    "Die Dosis betr\u00e4gt f\u00fcnf \u03bcg.",                 # the Greek mu (the micro sign is let through)
    "Die Frist betr\u00e4gt \uff11\uff10 \uff34age.",          # full-width letters
    "Die Frist betr\u00e4gt \u02bb\u02bb drei Monate.",         # a modifier letter other than the four let through
])
def test_a_statement_with_a_letter_of_another_alphabet_is_not_a_statement(statement):
    assert _check(st(statement, 1, FRIST_SHOWN[2])) == ([], _counts(**{NOT_A_STATEMENT: 1}))


def test_latin_letters_with_accents_are_latin_letters():
    statement = "La dur\u00e9e est de trois mois; \u0141\u00f3d\u017a, \u00c5ngstr\u00f6m und Stra\u00dfe, drei Monate."
    assert [s[0] for s in _check(st(statement, 1, FRIST_SHOWN[2]))[0]] == [statement]


@pytest.mark.parametrize("statement, passage", [
    ("Die Dosis betr\u00e4gt 5 \u00b5g pro Tag.", "Die Dosis betr\u00e4gt 5 \u00b5g pro Tag."),      # the micro sign
    ("Es gilt Vertrag N\u00ba 5.", "Es gilt Vertrag N\u00ba 5 vom Mai."),                          # ordinal indicators
    ("Sie ist die 1\u00aa Instanz.", "Sie ist die 1\u00aa Instanz im Verfahren."),
    ("Der Mieter hei\u00dft O\u02bcNeill.", "Der Mieter hei\u00dft O\u02bcNeill und wohnt hier."),   # the letter apostrophe
])
def test_four_letters_of_real_documents_pass_the_latin_rule(statement, passage):
    shown, removed_by = _one(statement, passage)
    assert [s[0] for s in shown] == [statement] and removed_by == _counts()


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
    # only marks of this reply's passages (1, 2, 3) are taken out
    ("Die Frist [7] betr\u00e4gt drei Monate [1].", "Die Frist [7] betr\u00e4gt drei Monate."),
    ("Die Frist betr\u00e4gt seit [2024] drei Monate.", "Die Frist betr\u00e4gt seit [2024] drei Monate."),
    ("Die Frist betr\u00e4gt drei Monate [1, 4].", "Die Frist betr\u00e4gt drei Monate [1, 4]."),
    ("Die Frist betr\u00e4gt drei Monate [\u0661].", "Die Frist betr\u00e4gt drei Monate [\u0661]."),
])
def test_the_shown_text_has_no_number_marks_and_no_unseen_characters(statement, shown):
    assert shown_text(statement, {1, 2, 3}) == shown


def test_a_statement_that_repeats_its_passages_number_mark_is_shown_without_it():
    # passage 1 holds no "1": the mark is not one of the statement's numbers
    marked = st("Die K\u00fcndigungsfrist betr\u00e4gt drei Monate [1].", 1, FRIST_SHOWN[2])
    assert _check(marked) == ([FRIST_SHOWN], _counts())
    marks = st("Die K\u00fcndigungsfrist [3] betr\u00e4gt drei Monate [1][2].", 1, FRIST_SHOWN[2])
    assert _check(marks) == ([FRIST_SHOWN], _counts())


@pytest.mark.parametrize("statement", [
    # changed by the hardening: "[7]" is no passage of this reply, so it is text and 7 a number
    "Die K\u00fcndigungsfrist [7] betr\u00e4gt drei Monate [1][2].",
    # the verifier's input: the year in brackets was taken out and never checked
    "Die K\u00fcndigungsfrist betr\u00e4gt seit [2024] drei Monate.",
])
def test_a_bracket_with_another_number_stays_and_its_number_is_checked(statement):
    assert _check(st(statement, 1, FRIST_SHOWN[2])) == ([], _counts(**{NUMBER_NOT_FOUND: 1}))


def test_a_bracketed_year_the_quote_holds_is_shown_with_it():
    statement = "Die K\u00fcndigungsfrist betr\u00e4gt seit [2024] drei Monate."
    shown, removed_by = _one(statement, "Seit [2024] betr\u00e4gt die K\u00fcndigungsfrist drei Monate.")
    assert [s[0] for s in shown] == [statement] and removed_by == _counts()


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


@pytest.mark.parametrize("quote, passage, cut", [
    # the verifier's input: a quote that only adds a final full stop is found (hardening)
    ("K\u00fcndigungsfrist betr\u00e4gt drei Monate.", FRIST_TEXT, "K\u00fcndigungsfrist betr\u00e4gt drei Monate"),
    ("K\u00fcndigungsfrist betr\u00e4gt drei Monate .", FRIST_TEXT, "K\u00fcndigungsfrist betr\u00e4gt drei Monate"),
    ("\u00abK\u00fcndigungsfrist betr\u00e4gt drei Monate.\u00bb", FRIST_TEXT,
     "K\u00fcndigungsfrist betr\u00e4gt drei Monate"),
    ("Die Frist betr\u00e4gt drei Monate.", "Die Frist betr\u00e4gt drei Monate, ab Zugang.",
     "Die Frist betr\u00e4gt drei Monate"),
    # where the passage has the full stop, it is part of the cut
    ("drei Monate zum Monatsende.", FRIST_TEXT, "drei Monate zum Monatsende."),
])
def test_a_quote_that_only_adds_a_final_full_stop_is_found(quote, passage, cut):
    assert find_quote(quote, passage) == cut


@pytest.mark.parametrize("quote", [
    "K\u00fcndigungsfrist betr\u00e4gt drei Monate ...",
    "K\u00fcndigungsfrist betr\u00e4gt drei Monate..",
    "K\u00fcndigungsfrist betr\u00e4gt drei Monate!",
    "K\u00fcndigungsfrist betr\u00e4gt drei Monat.",              # the stop does not end a word early
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


def _shown_token(token):
    return token.replace("\x00", "#")


def _claimed(statement):
    """Each claim as one text: its tokens with "|" between them, "#" for the tag of a token
    that is not written with digits ("w" a word, "D" a month or date, "R" an enumeration)."""
    return {"|".join(sorted(_shown_token(token) for token in claim)) for claim in claimed_numbers(statement)}


def _offered(passage, span=None, words=False):
    """The tokens offered; without ``words`` those for a statement's "ein" (n#, o#) left out."""
    return {_shown_token(token) for token in offered_numbers(passage, span)
            if words or not token.startswith(("n" + chr(0), "o" + chr(0)))}


@pytest.mark.parametrize("text, claims", [
    # thousands: an apostrophe is not written, a space is
    ("CHF 1'500", {"1500"}),
    ("CHF 1\u2019500", {"1500"}),
    ("CHF 1500", {"1500"}),
    ("CHF 1 500", {"1 500"}),
    ("CHF 1\u202f500\u00a0000", {"1 500 000"}),
    ("CHF 1\u2002500 und 2  500", {"1 500", "2 500"}),                  # white space of any kind is one space
    ("CHF 1' 500", {"1 500"}),
    ("CHF 12\n500 und 3\t000", {"12 500", "3 000"}),                  # a statement has no lines
    ("CHF 1\u00ad500, 2\u200b500 und 3\u034f500", {"1500", "2500", "3500"}),   # what is not seen does not separate
    ("2023 150 F\u00e4lle", {"2023", "150"}),
    ("12 3456", {"12", "3456"}),
    # a stop or a comma between digits ties them
    ("CHF 1'500.50", {"1500.50"}),
    ("CHF 1'500.\u2013", {"1500"}),
    ("EUR 1.500", {"1.500"}),
    ("3,5 %", {"3,5%"}),                                             # changed: per cent belongs to the number
    ("am 31.12.2025.", {"31.12.2025"}),
    ("am 1. M\u00e4rz 2024.", {"D#1.3.2024"}),                        # changed: a date with a month is one number
    # what belongs to a number: letters and accents after it, one letter alone after it, a sign before it
    ("Art. 266a OR", {"266a"}),
    ("ART. 266A OR", {"266a"}),
    ("Art. 12bis", {"12bis"}),
    ("Art. 266 a OR", {"266 a"}),
    ("Art. 266-a OR", {"266-a"}),
    ("Art. 266 OR", {"266"}),
    ("5 e-mails und 6 l'an", {"5", "6"}),
    ("Format A4", {"4"}),
    ("Saldo -500 und \u2212200, Tel. +41", {"-500", "-200", "+41"}),
    ("Art. 266\u0363", {"266\u0363"}),
    # numbers that are told apart: a word, white space, list and sentence marks, brackets, a dash
    ("Art. 5, 6 und 7", {"5", "6", "7"}),
    ("Art. 5 (2); 2024", {"5", "2", "2024"}),
    ("5-7 Tage, 2023\u20132024, 8 - 9", {"5", "7", "2023", "2024", "8", "9"}),
    ("5% - 7%", {"5%", "7%"}),                                       # changed: per cent belongs to the number
    # numbers with any other mark between them are one claim, as they stand
    ("2/3 Mehrheit", {"2/3"}),
    ("2 / 3 Mehrheit", {"2 / 3"}),
    ("um 14:30 Uhr", {"14:30"}),
    ("1_500", {"1_500"}),
    ("3a2", {"3a2"}),
    ("3'30", {"3'30"}),                      # no thousands mark: three digits must follow
    ("1\u201a5", {"1\u201a5"}),              # a low quotation mark is not an apostrophe
    ("3\u20245", {"3\u20245"}),
    ("1\u2800500", {"1\u2800500"}),
    ("5 \u03017", {"5 \u03017"}),
    # raised digits, fractions and number signs
    ("Art. 271a\u00b9 und 50 m\u00b2", {"271a\u00b9", "50", "\u00b2"}),
    ("10\u00b3 Franken, 3\u00bd Zimmer", {"10\u00b3", "3\u00bd"}),
    ("CHF \u2075\u2070\u2070, \u00bd Jahr, Ziff. \u2460", {"\u2075\u2070\u2070", "\u00bd", "\u2460"}),
    # digits of another script and marks on a digit stay as written
    ("\uff13\uff10 Tage", {"\uff13\uff10"}),
    ("\u09ea Wochen", {"\u09ea"}),
    ("CHF 5\u03360\u03360\u0336", {"5\u03360\u03360\u0336"}),
    # number words
    ("drei Monate", {"3"}),
    ("Zw\u00f6lf Monate, zwoelf Tage, F\u00dcNF Jahre", {"12", "5"}),
    # changed by the hardening (red team): the noun "Acht" claims the noun or 8, the number 8
    ("in Acht Tagen, ausser Acht gelassen", {"8", "8|w#acht"}),
    ("keine Zahl", set()),
])
def test_what_a_statement_claims(text, claims):
    assert _claimed(text) == claims


@pytest.mark.parametrize("text, claims", [
    # number words 0 to 999,999 and their compounds; "ein" is the article or 1
    ("eins, eine Woche, ein Jahr, einem Monat",
     {"w#eins", "n#woch|o#woch|w#ein:woch", "n#jahr|o#jahr|w#ein:jahr", "n#monat|o#monat|w#ein:monat"}),
    ("dreimonatig, Dreizehn, zwanzig", {"3|w#dreimonatig", "13", "20"}),
    ("einundzwanzig, hundert, zweitausendvierundzwanzig, null, achtzehnhundert",
     {"21", "100", "2024", "0", "1800"}),
    ("die zweite Rate, eine Zweidrittelmehrheit, ein Drittel, zweieinhalb Jahre",
     {"w#zweit", "n#zweidrittelmehrheit|o#zweidrittelmehrheit|w#ein:zweidrittelmehrheit",
      "w#zweidrittelmehrheit", "n#drittel|o#drittel|w#ein:drittel", "w#drittel", "w#zweieinhalb"}),
    ("einj\u00e4hrig, einmal, einfach, Einsprache, Eintrag, Einsicht", {"w#einjaehrig", "w#einmal"}),
    ("sechsmonatigen, Zehnjahresfrist, zweij\u00e4hrige",
     {"6|w#sechsmonatig", "10|w#zehnjahresfrist", "2|w#zweijaehrig"}),
    ("Achtung, im Zweifel, die Zweigniederlassung, Elfenbein, dreist", set()),
    ("six months, twenty days, one year", {"6", "20", "n#year|o#year|w#ein:year"}),
    ("null und nichtig", {"w#null und nichtig"}),
    # months and dates
    ("M\u00e4rz, Maerz, im Mai, Juni 2025, am 1. M\u00e4rz, 31. Dezember 2025",
     {"D#.3.", "D#.5.", "D#.6.2025", "D#1.3.", "D#31.12.2025"}),
    ("Mrz., Jan Meier, August Keller", {"D#.3.|w#mrz", "D#.1.|w#jan", "D#.8.|w#august"}),
    ("am 3. Jan. 2025", {"D#3.1.2025"}),
    # Roman numerals and the letters of an enumeration
    ("Ziffer IV, Teil II, lit. c, Anhang B, AHV/IV, Kapitel vi", {"R#iv", "R#ii", "R#c", "R#b", "R#vi"}),
    ("lit. a, b und d", {"R#a", "R#b", "R#d"}),
    ("Der Mieter hat einen Teil der Miete bezahlt.", {"n#teil|o#teil|w#ein:teil"}),
    ("Es gilt eine dreimonatige Frist.", {"n#frist|o#frist|w#ein:frist", "3|w#dreimonatig"}),
    # signs, per cent and scales are part of the number
    ("minus 500, -500, minus drei", {"-500", "-3"}),
    ("5%, 5 %, 5 Prozent, 5\u2030, 5 Promille, f\u00fcnf Prozent", {"5%", "5\u2030"}),
    ("CHF 2 Mio., 2 Millionen, 3 Mrd., eine Million", {"2#e6", "3#e9", "1#e6|w#ein#e6"}),
])
def test_what_counts_as_a_number_since_the_hardening(text, claims):
    assert _claimed(text) == claims


@pytest.mark.parametrize("passage, holds, lacks", [
    ("Die Regel gilt 2023-2024.", ["2023", "2024"], ["2023-2024"]),
    # whole tokens only (hardening): no number of a run, no piece and no other reading of a number
    ("Es braucht 2/3 der Stimmen.", ["2/3"], ["2", "3", "2 / 3"]),
    ("Art. 4, 5, 6 und 7", ["4", "5", "6", "7"], ["4, 5"]),
    ("um 14:30 Uhr", ["14:30"], ["14", "30"]),
    ("CHF 1 500 pro Monat", ["1 500"], ["1500", "1", "500"]),
    ("CHF 1'500.50 pro Monat", ["1500.50"], ["1500", "50", "1"]),
    ("am 31.12.2025", ["31.12.2025"], ["31", "12", "2025"]),
    ("Die Busse betr\u00e4gt 10\u2076 Franken.", ["10\u2076"], ["10", "\u2076"]),
    ("Art. 271a\u00b9 OR", ["271a\u00b9"], ["271a", "\u00b9", "271"]),
    ("Art. 266 a contrario", ["266 a"], ["266", "266a"]),
    ("Art. 266a OR", ["266a"], ["266", "266 a"]),
    ("Art. 266\u0363 OR", ["266\u0363"], ["266"]),
    ("Saldo -500 Franken", ["-500"], ["500"]),
    ("Saldo minus 500 Franken", ["-500"], ["500"]),
    ("Zins 5 %, Geb\u00fchr 2\u2030", ["5%", "2\u2030"], ["5", "2"]),
    ("am 1. M\u00e4rz 2024", ["D#1.3.2024"], ["1", "2024", "D#.3.", "D#1.3."]),
    # a line break or a tab between two numbers: neither reading is offered
    ("nach Absatz 2\n500 Franken Busse", [], ["2", "500", "2 500", "2500"]),
    ("CHF 12\t500 pro Jahr", [], ["12", "500", "12 500", "12500"]),
    ("Absatz 2\nDie Busse betr\u00e4gt 500 Franken", ["2", "500"], ["2 500"]),
    # changed by the hardening (red team): a line break does not part a date (joined, it offers less)
    ("am 1. M\u00e4rz\n2024", ["D#1.3.2024"], ["D#1.3.", "2024"]),
    # words
    ("drei Monate, in acht Tagen", ["3", "8"], ["1"]),
    ("nicht ausser Acht lassen", [], ["8"]),
    ("Darauf ist Acht zu geben; er gibt acht und hat es in Acht genommen.", [], ["8"]),
    ("Die Parteien sind sich eins \u00fcber eine Frist.", ["w#ein"], ["1"]),
    ("eine dreimonatige Frist", ["w#ein", "w#dreimonatig", "3"], ["1"]),
    ("die Zweigniederlassung, Zweidrittelmehrheit", ["w#zweidrittelmehrheit"], ["2", "w#zweigniederlassung"]),
    ("Jan Meier kam im August.", ["w#jan", "w#august"], ["D#.1.", "D#.8."]),
    ("am 3. Jan. 2025 und im August 2024", ["D#3.1.2025", "D#.8.2024"], ["w#jan", "w#august"]),
    ("Der Vertrag ist null und nichtig.", ["w#null und nichtig"], ["0"]),
])
def test_what_a_passage_offers(passage, holds, lacks):
    offered = _offered(passage)
    assert [token for token in holds if token not in offered] == []
    assert [token for token in lacks if token in offered] == []


def test_only_what_stands_wholly_inside_the_quote_is_offered():
    passage = "Die Frist betr\u00e4gt 30 Tage; massgebend ist Art. 266 a OR seit dem 1. M\u00e4rz 2024."

    def inside(quote):
        return _offered(passage, locate_quote(quote, passage))

    assert inside("Die Frist betr\u00e4gt 30 Tage") == {"30"}
    assert inside("massgebend ist Art. 266") == set()          # "266 a" goes on after the quote
    assert inside("ist Art. 266 a OR") == {"266 a"}
    assert inside("OR seit dem 1. M\u00e4rz") == set()         # so does the date
    assert inside("seit dem 1. M\u00e4rz 2024.") == {"D#1.3.2024"}


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
    # since the hardening: other kinds of numbers, as the passage writes them
    ("Der Vertrag wurde am 1. Maerz 2024 unterzeichnet.", "Der Vertrag wurde am 1. M\u00e4rz 2024 unterzeichnet."),
    ("Es gilt eine dreimonatige Frist.", "Es gilt eine Frist von drei Monaten."),
    ("Die Frist betr\u00e4gt 3 Monate.", "Es gilt eine dreimonatige Frist."),
    ("Die Frist betr\u00e4gt einundzwanzig Tage.", "Die Frist betr\u00e4gt 21 Tage."),
    ("Die Frist betr\u00e4gt ein Jahr.", "Die Frist betr\u00e4gt 1 Jahr."),
    ("Es gilt eine dreimonatige Frist.", "Die Frist betr\u00e4gt drei Monate."),
    ("Der Mieter muss eine Kaution leisten.", "Der Mieter muss Kaution leisten."),
    ("Die Frist betr\u00e4gt ein Jahr.", "Die Frist betr\u00e4gt ein Jahr."),
    ("Massgebend ist Ziffer IV.", "Massgebend ist Ziffer IV des Vertrags."),
    ("Der Saldo betr\u00e4gt minus 500 Franken.", "Der Saldo betr\u00e4gt -500 Franken."),
    ("Der Zins betr\u00e4gt 5 Prozent.", "Der Zins betr\u00e4gt 5 %."),
    ("Die Busse betr\u00e4gt CHF 2 Mio.", "Die Busse betr\u00e4gt CHF 2 Millionen."),
    ("Es entscheidet die Zweidrittelmehrheit.", "Es entscheidet die Zweidrittelmehrheit der Stimmen."),
    ("Der Vertrag ist null und nichtig.", "Der Vertrag ist null und nichtig."),
    ("Die Frist betr\u00e4gt \u2462 Monate.", "Die Frist betr\u00e4gt \u2462 Monate."),
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
    # changed by the hardening: a raised digit after a number is part of it, a footnote mark or not
    ("Massgebend ist Art. 271a OR.", "Massgebend ist Art. 271a\u00b9 OR."),
])
def test_a_number_the_passage_does_not_write_removes_the_statement(statement, passage):
    assert _one(statement, passage) == ([], _counts(**{NUMBER_NOT_FOUND: 1}))


def test_the_numbers_are_looked_for_in_the_statements_own_passage_only():
    line = st("Die K\u00fcndigungsfrist betr\u00e4gt drei Monate seit 2024.", 1, FRIST_SHOWN[2])
    assert _check(line) == ([], _counts(**{NUMBER_NOT_FOUND: 1}))      # 2024 stands in passage 3


# --- the hardening: the verifier's inputs ---------------------------------------------------------


@pytest.mark.parametrize("statement, passage, quote", [
    # a number that stands in the passage, but not in the quote
    ("Es braucht 3 Stimmen.", "Nach Art. 3 braucht es die Mehrheit, also 2/3 der Stimmen.",
     "also 2/3 der Stimmen"),
    ("Die Frist betr\u00e4gt 1 Monat.", "Der Vertrag gilt 1 Jahr. Die Frist betr\u00e4gt 30 Tage.",
     "Die Frist betr\u00e4gt 30 Tage"),
    ("Die Frist betr\u00e4gt drei Monate.", "Die Frist betr\u00e4gt drei Monate. Sie beginnt mit dem Zugang.",
     "Sie beginnt mit dem Zugang"),
    # the passage read too generously: a whole token supports no piece of itself
    ("Es braucht 3 Stimmen.", "Es braucht die Mehrheit, also 2/3 der Stimmen.", None),
    ("Die Frist betr\u00e4gt 30 Tage.", "Die Frist l\u00e4uft ab dem Termin um 14:30 Uhr.", None),
    ("Die Busse betr\u00e4gt 10 Franken.", "Die Busse betr\u00e4gt 10\u2076 Franken.", None),
    ("Massgebend ist Art. 266 OR.", "Massgebend ist Art. 266 a OR.", None),
    # kinds of numbers that were not seen
    ("Der Vertrag wurde am 1. April 2024 unterzeichnet.", "Der Vertrag wurde am 1. Maerz 2024 unterzeichnet.",
     "am 1. Maerz 2024 unterzeichnet"),
    ("Die Frist endet am 31. Dezember 2025.", "Die Frist endet am 31.12.2025.", None),
    ("Es gilt eine dreimonatige Frist.", "Es gilt eine sechsmonatige Frist.", None),
    ("Die Frist betr\u00e4gt zwanzig Tage.", "Die Frist betr\u00e4gt zehn Tage.", None),
    ("Die Frist betr\u00e4gt dreizehn Tage.", "Die Frist betr\u00e4gt drei Tage.", None),
    ("Die Frist betr\u00e4gt ein Jahr.", "Die Frist betr\u00e4gt zwei Jahre.", None),
    ("Massgebend ist Ziffer IV.", "Massgebend ist Ziffer II.", None),
    ("Der Saldo betr\u00e4gt minus 500 Franken.", "Der Saldo betr\u00e4gt 500 Franken.", None),
    ("Der Zins betr\u00e4gt 5%.", "Der Zins betr\u00e4gt 5\u2030.", None),
    # and their like
    ("Die Frist betr\u00e4gt einen Monat.", "Die Frist betr\u00e4gt zwei Monate.", None),
    ("Es gen\u00fcgt eine Unterschrift.", "Es braucht zwei Unterschriften.", None),
    ("Es gilt eine einj\u00e4hrige Frist.", "Es gilt die zweij\u00e4hrige Frist.", None),
    ("Es entscheidet die Zweidrittelmehrheit.", "Es entscheidet die Dreiviertelmehrheit.", None),
    ("Massgebend ist die zweite Ziffer.", "Massgebend ist die dritte Ziffer.", None),
    ("Die Frist betr\u00e4gt zweieinhalb Jahre.", "Die Frist betr\u00e4gt zwei Jahre.", None),
    ("Massgebend ist lit. c.", "Massgebend ist lit. b.", None),
    ("Massgebend sind lit. a und c.", "Massgebend sind lit. a und b.", None),
    ("Die Frist endet im Januar.", "Herr Jan Meier hat gek\u00fcndigt.", None),
    ("Die Busse betr\u00e4gt CHF 2 Mio.", "Die Busse betr\u00e4gt CHF 2 Milliarden.", None),
    ("Die Busse betr\u00e4gt CHF 2.", "Die Busse betr\u00e4gt CHF 2 Mio.", None),
    ("Die Miete betr\u00e4gt 0 Franken.", "Der Vertrag ist null und nichtig.", None),
    ("The notice period is six months.", "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate.", None),
])
def test_the_verifiers_inputs_are_removed(statement, passage, quote):
    assert _one(statement, passage, quote) == ([], _counts(**{NUMBER_NOT_FOUND: 1}))


@pytest.mark.parametrize("statement, passage, quote", [
    # number words written with an accent, without the umlaut, with letters that look like plain ones
    ("Die Frist betr\u00e4gt dr\u00e9i Monate.", "Die Frist betr\u00e4gt sechs Monate.", None),
    ("Die Frist betr\u00e4gt funf Monate.", "Die Frist betr\u00e4gt drei Monate.", None),
    ("Die Frist betr\u00e4gt dre\u0131 Monate.", "Die Frist betr\u00e4gt sechs Monate.", None),
    ("Die Frist betr\u00e4gt \u1d05\u0280\u1d07\u026a Monate.", "Die Frist betr\u00e4gt sechs Monate.", None),
    ("Die Frist betr\u00e4gt dre\u0335i Monate.", "Die Frist betr\u00e4gt sechs Monate.", None),
    # a number written in several words is one number
    ("Die Busse betr\u00e4gt zwei Franken.", "Die Busse betr\u00e4gt zwei hundert Franken.", None),
    ("Die Busse betr\u00e4gt drei Franken.", "Die Busse betr\u00e4gt drei und zwanzig Franken.", None),
    ("Die Busse betr\u00e4gt 5 Franken.", "Die Busse betr\u00e4gt 5 Tausend Franken.", None),
    ("Die Busse betr\u00e4gt 5 Franken.", "Die Busse betr\u00e4gt 5 Tsd. Franken.", None),
    # a word broken at a line end: with a hyphen it is read neither way, with a soft hyphen as one word
    ("Die Frist betr\u00e4gt drei Tage.", "Die Frist betr\u00e4gt drei-\nzehn Tage.",
     "Frist betr\u00e4gt drei- zehn Tage"),
    ("Die Frist betr\u00e4gt dreizehn Tage.", "Die Frist betr\u00e4gt drei-\nzehn Tage.",
     "Frist betr\u00e4gt drei- zehn Tage"),
    ("Die Frist betr\u00e4gt drei Tage.", "Die Frist betr\u00e4gt drei\u00ad\nzehn Tage.", None),
    # a number word inside a word
    ("Es gilt die Mindestdreimonatsfrist.", "Es gilt die Mindestsechsmonatsfrist.", None),
    ("Es gilt die Mindestzweidrittelmehrheit.", "Es gilt die Mindestdreiviertelmehrheit.", None),
])
def test_number_words_cannot_hide_from_the_check(statement, passage, quote):
    assert _one(statement, passage, quote) == ([], _counts(**{NUMBER_NOT_FOUND: 1}))


@pytest.mark.parametrize("statement, passage", [
    ("Die Busse betr\u00e4gt 200 Franken.", "Die Busse betr\u00e4gt zwei hundert Franken."),
    ("Die Busse betr\u00e4gt dreiundzwanzig Franken.", "Die Busse betr\u00e4gt drei und zwanzig Franken."),
    ("Die Busse betr\u00e4gt 5 Tausend Franken.", "Die Busse betr\u00e4gt 5 Tsd. Franken."),
    ("Die Frist betr\u00e4gt dreizehn Tage.", "Die Frist betr\u00e4gt drei\u00ad\nzehn Tage."),
    ("Die Frist betr\u00e4gt f\u00fcnf Monate.", "Die Frist betr\u00e4gt fuenf Monate."),
    ("Es gilt die Mindestdreimonatsfrist.", "Es gilt eine Mindestfrist von drei Monaten."),
    # "acht" inside a word is no number there; "Merkmal", "Stichtag" hold no number word
    ("Die Pacht l\u00e4uft ein Pachtjahr.", "Die Pacht l\u00e4uft ein Pachtjahr lang."),
    ("Der P\u00e4chter zahlt im Pachtjahr.", "Der P\u00e4chter zahlt im Pachtjahr vorab."),
    ("Das Merkmal gilt seit dem Stichtag.", "Das Merkmal gilt seit dem Stichtag fest."),
    ("Die Frist gilt innerhalb des Monats.", "Die Frist gilt innerhalb des Monats."),
])
def test_what_reads_as_one_number_is_found_as_one(statement, passage):
    shown, removed_by = _one(statement, passage)
    assert [s[0] for s in shown] == [statement] and removed_by == _counts()


@pytest.mark.parametrize("statement", [
    "Die K\u00fcndigungsfrist betr\u00e4gt \u01bc Monate.",        # the verifier's look-alikes
    "Die K\u00fcndigungsfrist betr\u00e4gt \u01bb Monate.",
    "Die K\u00fcndigungsfrist betr\u00e4gt \u0292 Monate.",        # their like
    "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate, \ua76f Wochen.",
    "Die K\u00fcndigungsfrist betr\u00e4gt |0 Monate.",
    "Die K\u00fcndigungsfrist betr\u00e4gt \u2462 Monate.",         # numeric, no digit 0 to 9
    "Die K\u00fcndigungsfrist betr\u00e4gt \u2167 Monate.",
    "Die K\u00fcndigungsfrist betr\u00e4gt drei \u00bd Monate.",
])
def test_a_character_with_a_numeric_value_or_a_digits_look_must_stand_in_the_quote(statement):
    assert _check(st(statement, 1, FRIST_SHOWN[2])) == ([], _counts(**{NUMBER_NOT_FOUND: 1}))


def test_a_look_alike_the_quote_holds_is_shown():
    statement = "Das Zeichen \u01bc steht im Vertrag."
    shown, removed_by = _one(statement, "Das Zeichen \u01bc steht so im Vertrag.")
    assert [s[0] for s in shown] == [statement] and removed_by == _counts()


def test_check_numbers_finds_the_quote_itself_when_it_was_not_located():
    passage = "Die Frist betr\u00e4gt 3 Monate. Der Vertrag gilt 30 Tage."
    located = Statement("Die Frist betr\u00e4gt 3 Monate.", 1, "Der Vertrag gilt 30 Tage")
    assert check_numbers(located, {1: passage}) is False
    assert check_numbers(Statement("Die Frist betr\u00e4gt 3 Monate.", 1, "Frist betr\u00e4gt 3 Monate"),
                         {1: passage}) is True
    assert check_numbers(Statement("Die Frist betr\u00e4gt 3 Monate.", 1, "steht nirgends"), {1: passage}) is False


# --- the hardening: the cost (true statements that are now removed) --------------------------------


@pytest.mark.parametrize("statement, passage, quote", [
    # the number stands in the passage, but the model quoted another piece of it
    ("Die Frist betr\u00e4gt drei Monate ab Zugang.", "Die Frist betr\u00e4gt drei Monate. Sie l\u00e4uft ab Zugang.",
     "Sie l\u00e4uft ab Zugang"),
    # "ein" in the statement, none in the quote
    ("Der Mieter muss eine Kaution leisten.", "Der Mieter muss Sicherheit leisten, in bar.",
     "Der Mieter muss Sicherheit leisten"),
    # a piece of a whole token: the year of a date, a footnote mark, the month of a date
    ("Der Vertrag stammt aus dem Jahr 2024.", "Der Vertrag wurde am 1. M\u00e4rz 2024 unterzeichnet.", None),
    ("Der Vertrag wurde im M\u00e4rz 2024 unterzeichnet.", "Der Vertrag wurde am 1. M\u00e4rz 2024 unterzeichnet.",
     None),
    # a word that begins with a number word and names none, missing from the quote
    ("Im Zweifel gilt die Frist f\u00fcr Herrn Zehnder.", "Die Frist gilt f\u00fcr alle Mieter.", None),
    # a number written as a word one way and with digits the other, where only the word reads two ways
    ("Die Frist betr\u00e4gt 1 Jahr.", "Die Frist betr\u00e4gt ein Jahr.", None),
    ("Die Frist betr\u00e4gt 2/3 der Zeit.", "Die Frist betr\u00e4gt zwei Drittel der Zeit.", None),
    ("Die Frist endet am 31.12.2025.", "Die Frist endet am 31. Dezember 2025.", None),
])
def test_what_the_hardening_costs(statement, passage, quote):
    assert _one(statement, passage, quote) == ([], _counts(**{NUMBER_NOT_FOUND: 1}))


# --- check 5: the length ---------------------------------------------------------------------------


def test_the_length_is_measured_on_the_shown_text():
    base = "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate"
    longest = base + " " + "x" * (MAX_STATEMENT_CHARS - len(base) - 1)
    assert len(longest) == MAX_STATEMENT_CHARS
    shown, removed_by = _check(st(longest + " [1]", 1, FRIST_SHOWN[2]))
    assert [s[0] for s in shown] == [longest] and removed_by == _counts()
    assert _check(st(longest + "x", 1, FRIST_SHOWN[2])) == ([], _counts(**{TOO_LONG: 1}))


# --- the hardening: a second agent's attack on this check ---------------------------------------------
#
# Inputs of an attack by another agent on the hardened check, before it was pushed (it had the code and
# the rules, not the builder's reasoning). Each was shown, or removed though true, before the fix.

_MIETE_5 = "Die Miete wird nach 5 Jahren angepasst."
_SALDO = "Der Saldo betr\u00e4gt 500 Franken und ist innert 5 Tagen zu zahlen."
_FRIST_5 = "Die Frist betr\u00e4gt 5 Tage ab Zustellung."


@pytest.mark.parametrize("statement, passage, quote", [
    # "ein" was supported by any article of the quote, and by a list number
    ("Ein Arbeitnehmer kann mit einer Frist von einem Monat k\u00fcndigen.",
     "Ein Arbeitnehmer kann das Arbeitsverh\u00e4ltnis mit einer Frist von drei Monaten k\u00fcndigen.",
     "Ein Arbeitnehmer kann das Arbeitsverh\u00e4ltnis mit einer Frist"),
    ("Die K\u00fcndigungsfrist betr\u00e4gt einen Monat.", "1. Die K\u00fcndigungsfrist betr\u00e4gt drei Monate.",
     "1. Die K\u00fcndigungsfrist betr\u00e4gt"),
    ("Das Grundst\u00fcck ist mit einem Einfamilienhaus bebaut.",
     "Das Grundst\u00fcck ist mit einem Zweifamilienhaus bebaut.", None),
    ("Die Aufbewahrungspflicht dauert ein Jahrzehnt.", "Die Aufbewahrungspflicht dauert ein Jahr.", None),
    ("Die Lieferung umfasst ein Dutzend Ger\u00e4te.", "Die Lieferung umfasst ein Ger\u00e4t.", None),
    ("Es gilt eine Mindesteinjahresfrist.", "Es gilt eine Mindestzweijahresfrist.", None),
    ("Der Saldo betr\u00e4gt minus eins.", "Der Saldo betr\u00e4gt 1 Franken.", None),
    ("Der Saldo betr\u00e4gt minus einen Franken.", "Der Saldo betr\u00e4gt 1 Franken.", None),
    # words for numbers that were not read
    ("Der Vermieter kann die doppelte Kaution verlangen.", "Der Vermieter kann die dreifache Kaution verlangen.",
     "Der Vermieter kann die"),
    ("Die Frist endet im Febr.", "Die Frist endet im M\u00e4rz.", "Die Frist endet"),
    ("Die Frist endet im J\u00e4n.", "Die Frist endet im M\u00e4rz.", "Die Frist endet"),
    ("The term ends on 31 March 2025.", "The term ends on 31 May 2025.", None),
    ("The tenant must give notice within the third month.",
     "The tenant must give notice within the second month.", "The tenant must give notice"),
    ("The deposit is paid twice a year.", "The deposit is paid once a year.", "The deposit is paid"),
    ("F\u00fcr die Zone gilt die Empfindlichkeitsstufe I.", "F\u00fcr die Zone gilt die Empfindlichkeitsstufe III.",
     "F\u00fcr die Zone gilt"),
    ("Der Lohn richtet sich nach Lohnklasse V.", "Der Lohn richtet sich nach Lohnklasse II.",
     "Der Lohn richtet sich"),
    ("Der F\u00fchrerausweis der Kategorie C berechtigt dazu.",
     "Der F\u00fchrerausweis der Kategorie B berechtigt dazu.", "Der F\u00fchrerausweis der"),
    ("Es gilt Phase \u026a\u1d20.", "Es gilt Phase 2 der Planung.", None),
    # a number of the passage read in pieces
    ("Der Beschluss bedarf der Zustimmung von zwei Gesellschaftern.",
     "Der Beschluss bedarf der Zustimmung von zwei Dritteln der Gesellschafter.", None),
    ("Der Beschluss bedarf drei Stimmen.", "Der Beschluss bedarf drei Viertel der Stimmen.", None),
    ("Die Probezeit dauert 1 Jahr.", "Die Probezeit dauert 1 1/2 Jahre.", None),
    ("Die Probezeit dauert ein Jahr.", "Die Probezeit dauert 1 \u00bd Jahre.", None),
    ("Die Zahlungsfrist betr\u00e4gt 15 Tage.", "Der Vertrag vom 2024-01-15 regelt die Zahlungsfrist.", None),
    ("Die Zahlungsfrist betr\u00e4gt 12 Monate.", "Der Vertrag endet am 31. 12. 2025 ohne K\u00fcndigung.",
     "Der Vertrag endet am 31. 12. 2025"),
    ("Der Zins betr\u00e4gt 5 Franken.", "Der Zins betr\u00e4gt 5\n% pro Jahr.", "Der Zins betr\u00e4gt 5 % pro Jahr."),
    ("Die Frist betr\u00e4gt 31 Tage.", "Die Frist endet am 31.\nDezember 2025.",
     "Die Frist endet am 31. Dezember 2025."),
    ("Die K\u00fcndigung richtet sich nach Art. 266 OR.", "Die K\u00fcndigung richtet sich nach Art. 266\na OR.",
     "Die K\u00fcndigung richtet sich nach Art. 266 a OR."),
    ("Der Zins betr\u00e4gt 5 Prozent.", "Der Zins betr\u00e4gt -\n5 Prozent.", "Der Zins betr\u00e4gt - 5 Prozent."),
    ("Die Frist betr\u00e4gt drei Tage.", "Die Frist betr\u00e4gt drei- zehn Tage.", None),
    # a word that holds "ein" offered the digit 1; one that counts sides offered its number
    ("Die K\u00fcndigungsfrist betr\u00e4gt 1 Monat.",
     "Die K\u00fcndigung ist eine einseitige Willenserkl\u00e4rung; die K\u00fcndigungsfrist betr\u00e4gt drei Monate.",
     "Die K\u00fcndigung ist eine einseitige Willenserkl\u00e4rung"),
    ("Der Mieter hat 1 Monatsmiete bezahlt.",
     "Der Mieter hat nicht einmal die Miete bezahlt, obwohl drei Monate vergangen waren.",
     "Der Mieter hat nicht einmal die Miete bezahlt"),
    ("Die Frist betr\u00e4gt 2 Monate.", "Es gilt ein zweiseitiger Vertrag mit Frist.", None),
    # per cent, scale and sign
    ("Die Miete wird 5-prozentig erh\u00f6ht.", _MIETE_5, None),
    ("Die Miete wird f\u00fcnfprozentig erh\u00f6ht.", _MIETE_5, None),
    ("F\u00fcr die Miete gilt die 5-Prozent-Regel.", _MIETE_5, None),
    ("Die Miete steigt um 5 Prozentpunkte.", _MIETE_5, None),
    ("Die Miete steigt um 5 pct.", _MIETE_5, None),
    ("Der Zins betr\u00e4gt 5 Perzent.", _MIETE_5, None),
    ("Der Zins betr\u00e4gt 5\uff05.", _MIETE_5, None),
    ("Der Saldo betr\u00e4gt 500 Mia. Franken.", _SALDO, None),
    ("Der Saldo betr\u00e4gt 500 Bio. Franken.", _SALDO, None),
    ("Der Saldo betr\u00e4gt 500 Mill. Franken.", _SALDO, None),
    ("Der Saldo betr\u00e4gt 500 Billiarden Franken.", _SALDO, None),
    ("Der Saldo betr\u00e4gt \ufe63500 Franken.", _SALDO, None),
    ("Der Saldo betr\u00e4gt \uff0d500 Franken.", _SALDO, None),
    ("Der Saldo betr\u00e4gt \u207b500 Franken.", _SALDO, None),
    ("Der Saldo betr\u00e4gt - 500 Franken.", _SALDO, None),
    # number words written oddly
    ("Die Frist betr\u00e4gt zwei-hundert Tage.", "Nach 2 Mahnungen gilt eine Frist von 100 Tagen.", None),
    ("Die Frist betr\u00e4gt drei-zehn Tage.", "Nach 3 Mahnungen gilt eine Frist von 10 Tagen.", None),
    ("Die Frist betr\u00e4gt einund zwanzig Tage.", "Die Frist betr\u00e4gt 20 Tage.", None),
    ("Die Frist betr\u00e4gt ein-und-zwanzig Tage.", "Die Frist betr\u00e4gt ein Mal 20 Tage.", None),
    ("The fee is five hundred francs for 5 hours.", "The fee is CHF 100 per hour for 5 hours.", None),
    ("The fee is twenty-five francs.", "The fee is 20 francs and 5 francs.", None),
    # letters that look like plain letters, or like digits
    ("Die Frist betr\u00e4gt d\u027eei Tage.", _FRIST_5, "Die Frist betr\u00e4gt"),
    ("Die Frist betr\u00e4gt \u028bier Tage.", _FRIST_5, "Die Frist betr\u00e4gt"),
    ("Die Frist betr\u00e4gt dr\u025bi Tage.", _FRIST_5, "Die Frist betr\u00e4gt"),
    ("Die Frist betr\u00e4gt \ua723O Tage.", _FRIST_5, "Die Frist betr\u00e4gt"),
    ("Die Frist betr\u00e4gt lO Tage.", _FRIST_5, "Die Frist betr\u00e4gt"),
    ("Die Frist betr\u00e4gt \ua75b5 Tage.", _FRIST_5, "Die Frist betr\u00e4gt"),
    ("Die Frist betr\u00e4gt \u204a5 Tage.", _FRIST_5, "Die Frist betr\u00e4gt"),
    ("Die Frist betr\u00e4gt l5 Tage.", _FRIST_5, "Die Frist betr\u00e4gt"),
    ("Die Frist betr\u00e4gt O,5 Tage.", _FRIST_5, "Die Frist betr\u00e4gt"),
])
def test_the_second_attack_is_removed(statement, passage, quote):
    assert _one(statement, passage, quote) == ([], _counts(**{NUMBER_NOT_FOUND: 1}))


@pytest.mark.parametrize("statement", [
    "Ich habe dazu keine Informationen gefunden.",
    "Ich konnte dazu keine Angaben finden.",
    "Ich fand dazu nichts in Ihren Quellen.",
    "Ihre Quellen enthalten dazu keine Angaben.",
    "In Ihren Quellen steht dazu nichts.",
    "Dazu wurde in Ihren Quellen nichts gefunden.",
    "Dazu liegen mir keine Informationen vor.",
    "Dar\u00fcber geben Ihre Unterlagen keine Auskunft.",
    "Nichts gefunden.",
    "Die Quellen beantworten diese Frage nicht.",
    "Zu dieser Frage enthalten die Dokumente keine Aussage.",
    "Ich finde zu dieser spezifischen Frage in den von Ihnen bereitgestellten Quellen leider nichts.",
    "I couldn't find anything about this in your sources.",
    "Your sources do not contain this information.",
    "There is nothing about this in your sources.",
    "Je ne trouve rien dans vos sources.",
    "Dazu \ua730\u026a\u0274\u1d05\u1d07 \u026a\u1d04\u029c \u0274\u026a\u1d04\u029c\u1d1b\ua731 in Ihren Quellen.",
])
def test_the_second_attacks_no_answer_wordings_are_not_statements(statement):
    passage = "Der Mietvertrag kann unter Einhaltung der gesetzlichen Fristen gek\u00fcndigt werden."
    assert _one(statement, passage, "Der Mietvertrag kann unter Einhaltung") == ([], _counts(**{NOT_A_STATEMENT: 1}))


@pytest.mark.parametrize("statement, passage, quote", [
    ("Eine Mietzinserh\u00f6hung muss dem Mieter auf dem amtlichen Formular mitgeteilt werden.",
     "Mietzinserh\u00f6hungen sind dem Mieter auf dem amtlichen Formular mitzuteilen.", None),
    ("Dieser Umstand bleibt bei der Berechnung der Frist ausser Acht.",
     "Dieser Umstand bleibt bei der Berechnung der Frist ausser Acht.", None),
    ("Der Verzugszins betr\u00e4gt f\u00fcnf Prozent pro Jahr.",
     "Der Schuldner hat Verzugszinse zu f\u00fcnf vom Hundert f\u00fcr das Jahr zu bezahlen.", None),
    ("Der Grenzabstand f\u00fcr Hauptbauten betr\u00e4gt 10 Meter.",
     "Der Grenzabstand f\u00fcr Hauptbauten betr\u00e4gt 10 m.", None),
    ("Im Gesetz findet sich keine Regelung zur Untermiete von Parkpl\u00e4tzen.",
     "Im Gesetz findet sich keine Regelung zur Untermiete von Parkpl\u00e4tzen.", None),
    ("Die Frist endet am 31. Dezember 2025.", "Die Frist endet am 31.\nDezember 2025.", None),
    ("Der Zins betr\u00e4gt 5 %.", "Der Zins betr\u00e4gt 5\n% pro Jahr.", "Der Zins betr\u00e4gt 5 % pro Jahr."),
    ("Es gilt eine dreimonatige K\u00fcndigungsfrist.", "Die K\u00fcndigungsfrist betr\u00e4gt drei Monate.", None),
    ("Die Probezeit dauert 1 1/2 Jahre.", "Die Probezeit dauert 1 1/2 Jahre.", None),
    ("Der Beschluss bedarf der Zustimmung von zwei Dritteln der Gesellschafter.",
     "Der Beschluss bedarf der Zustimmung von zwei Dritteln der Gesellschafter.", None),
    ("Die Vereinbarung gilt seit dem 2024-01-15.", "Die Vereinbarung gilt seit dem 2024-01-15 unbefristet.", None),
])
def test_what_the_second_attack_found_too_costly_is_shown(statement, passage, quote):
    shown, removed_by = _one(statement, passage, quote)
    assert [s[0] for s in shown] == [statement] and removed_by == _counts()


def test_a_long_passage_is_read_once_per_answer():
    import time

    passage = ("Die Frist betr\u00e4gt 30 Tage ab Zustellung am 1. M\u00e4rz 2024, also CHF 1500 pro Monat. "
               * 1_800)[:150_000]
    answer = "\n".join(st("Die Frist betr\u00e4gt %d Tage." % n, 1, "Die Frist betr\u00e4gt 30 Tage")
                       for n in range(50))
    started = time.monotonic()
    shown, removed_by = check_answer(answer, {1: passage})
    assert len(shown) == 1 and removed_by[NUMBER_NOT_FOUND] == 49
    assert time.monotonic() - started < 3.0
