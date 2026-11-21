"""The check of the Knovas Assistent's answer (PMVP-20, rework 2 and hardening).

The model is told to answer in statements, one JSON object per line::

    {"statement": "...", "source": 2, "quote": "..."}

or ``{"no_answer": true}`` when the passages do not answer the question.
Nothing is cut into sentences: the unit is the statement the model declared.
A statement is shown only if all five checks pass; otherwise it is removed
and counted under the first check it failed:

1. ``check_form``     the line is such an object            ``not_a_statement``
2. ``check_source``   ``source`` is a retrieved passage     ``unknown_source``
3. ``check_quote``    ``quote`` stands in that passage      ``quote_not_found``
4. ``check_numbers``  every number stands in that quote     ``number_not_found``
5. ``check_length``   at most ``MAX_STATEMENT_CHARS``       ``too_long``

One function per check, called in ``check_answer``; a further check (a judge
model, follow-up FU-27) is one more function and one more line there.

Two readings run through the module. A statement is read strictly: what it
shows is what is checked, as written. A passage is read for what it surely
holds. Where a passage can be read two ways, it supports neither.

What the check cannot see: a statement that says more in words than its
quote supports. The reply therefore carries the quote, cut from the passage
itself and never the model's wording, for the page to show under the
statement.

This module imports nothing of the service. It is ASCII-only: other
characters are ``\\u`` escapes.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Any, Container, Dict, FrozenSet, List, Mapping, Optional, Set, Tuple

#: What the person reads when nothing supported is left. Kept here because
#: the check must recognise it; ``assistant.service`` hands it on.
NO_ANSWER_TEXT = "Dazu finde ich nichts in Ihren Quellen"

#: Other wordings of the no-answer sentence. A statement that holds one of
#: them is not a statement: it says that nothing was found, and no quote can
#: prove that. Case, white space and marks do not matter, and up to
#: ``_NO_ANSWER_GAP`` words may stand between two words of a wording ("Dazu
#: finde ich in Ihren Unterlagen nichts", "In Ihren Quellen finde ich dazu
#: nichts").
NO_ANSWER_WORDINGS = (
    "finde ich nichts",
    "finde ich keine",
    "ich finde nichts",
    "ich finde keine",
    "findet sich nichts",
    "findet sich keine",
    "habe ich nichts gefunden",
    "ich habe nichts gefunden",
    "habe ich keine gefunden",
    "kann ich nichts finden",
    "ich kann nichts finden",
    "konnte ich nichts finden",
    "ich konnte nichts finden",
    "I find nothing",
    "I found nothing",
    "I cannot find",
    "I can't find",
    "I could not find",
    "I did not find",
)
_NO_ANSWER_GAP = 6

# ---------------------------------------------------------------------------
# Knobs
# ---------------------------------------------------------------------------

#: The least words of a quote.
MIN_QUOTE_WORDS = 3
#: The most characters of a statement, as shown.
MAX_STATEMENT_CHARS = 500

# ---------------------------------------------------------------------------
# Why a statement was removed
# ---------------------------------------------------------------------------

NOT_A_STATEMENT = "not_a_statement"
UNKNOWN_SOURCE = "unknown_source"
QUOTE_NOT_FOUND = "quote_not_found"
NUMBER_NOT_FOUND = "number_not_found"
TOO_LONG = "too_long"
REASONS = (NOT_A_STATEMENT, UNKNOWN_SOURCE, QUOTE_NOT_FOUND, NUMBER_NOT_FOUND, TOO_LONG)


@dataclass(frozen=True)
class Statement:
    """One statement of the answer.

    ``text`` is the statement as the page shows it. ``quote`` is the model's
    wording until ``check_quote`` has found it; from then on it is the
    passage's own characters, and ``span`` is where they stand in the
    passage (start, end).
    """

    text: str
    source: int
    quote: str
    span: Optional[Tuple[int, int]] = None


def check_answer(text: str, passages: Mapping[int, str]) -> Tuple[List[Statement], Dict[str, int]]:
    """``(statements to show, removed counts per reason)`` for a generated answer.

    ``passages`` maps each retrieved passage's number to its text. Every
    reason of ``REASONS`` is a key of the counts. The no-answer line, as an
    object or as the plain sentence, is neither shown nor counted.
    """
    shown: List[Statement] = []
    removed_by = dict.fromkeys(REASONS, 0)
    for value in read_answer(text):
        if isinstance(value, dict) and value.get("no_answer") is True:
            continue
        statement = check_form(value, passages)
        if statement is None:
            removed_by[NOT_A_STATEMENT] += 1
            continue
        if not check_source(statement, passages):
            removed_by[UNKNOWN_SOURCE] += 1
            continue
        found = check_quote(statement, passages)
        if found is None:
            removed_by[QUOTE_NOT_FOUND] += 1
        elif not check_numbers(found, passages):
            removed_by[NUMBER_NOT_FOUND] += 1
        elif not check_length(found):
            removed_by[TOO_LONG] += 1
        else:
            shown.append(found)
    return shown, removed_by


# ---------------------------------------------------------------------------
# What is not seen
# ---------------------------------------------------------------------------

#: Marks that take no room and change nothing a person sees: the grapheme
#: joiner, variation selectors and their like. (Controls, soft hyphens and
#: zero-width spaces are told by their Unicode category.)
_UNSEEN_MARKS = frozenset(
    [0x034F, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180F]
    + list(range(0xFE00, 0xFE10)) + list(range(0xE0100, 0xE01F0))
)


def _is_seen(char: str) -> bool:
    return char.isspace() or (unicodedata.category(char)[0] != "C" and ord(char) not in _UNSEEN_MARKS)


def _is_mark(char: str) -> bool:
    return unicodedata.category(char)[0] == "M"


# ---------------------------------------------------------------------------
# Reading the answer: lines, not prose
# ---------------------------------------------------------------------------

#: A line that is not JSON at all (``null`` is JSON, so None cannot say it).
_NOT_JSON = object()
#: A line that only opens or closes a code fence: three backticks or more and
#: perhaps the name of a language. Anything else on such a line makes it a
#: line like any other.
_FENCE_LINE = re.compile("`{3,}\\s*[\\w.+-]*")
#: "- ", "* ", "2. ", "3) " before an object.
_LIST_MARK = re.compile("^(?:[-*\u2022\u2013]|\\d{1,3}[.)])\\s+")


def read_answer(text: str) -> List[Any]:
    """The values the answer's lines hold, in order.

    One value per line: the decoded JSON, or a marker for a line that is not
    JSON (prose, a broken object, an object with prose around it, an object
    after a code fence on its line). Blank lines, lines that only open or
    close a code fence, and the plain no-answer sentence give nothing. List
    marks before an object and the brackets and commas of an array are not
    part of the line. An answer that is one JSON array or one object spread
    over several lines is read as a whole.
    """
    # Only a line feed ends a line: JSON allows other line separators inside a text.
    lines = [line.strip() for line in (text or "").split("\n")]
    lines = [line for line in lines if line and not _FENCE_LINE.fullmatch(line)]
    whole = _decode("\n".join(lines))
    if isinstance(whole, dict):
        return [whole]
    if isinstance(whole, list):
        return whole
    values: List[Any] = []
    for line in lines:
        if _letters(line) == _NO_ANSWER_KEY:
            continue
        line = _LIST_MARK.sub("", line)
        value = _decode(line)
        if value is _NOT_JSON:
            # what an array leaves around an object, or on a line of its own
            line = (line[1:] if line.startswith("[") else line).strip().rstrip("], \t")
            if not line:
                continue
            value = _decode(line)
        if isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)
    return values


def _decode(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return _NOT_JSON


# ---------------------------------------------------------------------------
# Check 1: the form
# ---------------------------------------------------------------------------

_SOURCE_DIGITS = re.compile("[0-9]{1,6}")
#: A number mark inside a statement, with the space before it: "[1]",
#: "[1, 3]", and the full-width brackets some models write. Taken out only
#: when every number in it is a passage of this reply.
_MARK = re.compile("\\s?[\\[\u3010]\\s?([0-9]{1,4}(?:\\s?[,;]\\s?[0-9]{1,4})*)\\s?[\\]\u3011]")
#: Letters a statement may hold although their names are not Latin ones: the
#: ordinal indicators, the apostrophe that is a letter, and the micro sign.
#: They stand in real documents ("N\u00ba 5", "5 \u00b5g").
_LETTERS_TOO = frozenset("\u00ba\u00aa\u02bc\u00b5")


def check_form(value: Any, passages: Container[int]) -> Optional[Statement]:
    """The statement a decoded line holds, or None when it holds none.

    A statement is a JSON object with a text under ``statement``, a whole
    number under ``source`` (a digit string is read) and a text under
    ``quote``; further keys are ignored. Its text holds a letter, and all its
    letters are Latin ones (or one of ``_LETTERS_TOO``): a letter of another
    alphabet can look like a Latin letter or like a digit, and the checks
    could not tell. The no-answer sentence, or another wording of it, is not
    a statement, whatever source and quote come with it. ``passages`` holds
    the numbers of this reply's passages: only their marks are taken out of
    the text.
    """
    if not isinstance(value, dict):
        return None
    text, source, quote = value.get("statement"), value.get("source"), value.get("quote")
    if not isinstance(text, str) or not isinstance(quote, str) or not quote.strip():
        return None
    if isinstance(source, str):
        digits = source.strip(" \t")
        source = int(digits) if _SOURCE_DIGITS.fullmatch(digits) else None
    if not isinstance(source, int) or isinstance(source, bool):
        return None
    shown = shown_text(text, passages)
    letters = [char for char in shown if char.isalpha()]
    if not letters or not all(char in _LETTERS_TOO or unicodedata.name(char, "").startswith("LATIN")
                              for char in letters):
        return None
    if says_no_answer(shown):
        return None
    return Statement(text=shown, source=source, quote=quote)


def shown_text(statement: str, marks: Container[int]) -> str:
    """A statement as the page shows it: what is not seen (characters
    without width, controls) taken out, white space single, and the number
    marks of this reply's passages (``marks``) taken out. A bracket with any
    other number ("[7]" in a reply with four passages, "[2024]") stays text,
    and its number is checked. The later checks read this text, so nothing
    they did not see is shown."""

    def unmark(match: "re.Match[str]") -> str:
        numbers = [int(number) for number in re.findall("[0-9]+", match.group(1))]
        return "" if all(number in marks for number in numbers) else match.group(0)

    visible = "".join(char for char in statement if _is_seen(char))
    return " ".join(_MARK.sub(unmark, " ".join(visible.split())).split())


def says_no_answer(statement: str) -> bool:
    """Whether a statement is the no-answer sentence: as it stands, inside a
    wrapper, with a few letters changed or left out, or in one of the
    ``NO_ANSWER_WORDINGS``."""
    letters = _letters(statement[:MAX_STATEMENT_CHARS])
    if _NO_ANSWER_KEY in letters:
        return True
    words = [_letters(word) for word in statement[:MAX_STATEMENT_CHARS].split()]
    if any(_holds_in_order(words, wording) for wording in _NO_ANSWER_WORDING_KEYS):
        return True
    size = len(_NO_ANSWER_KEY)
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(_NO_ANSWER_KEY)
    for start in range(max(1, len(letters) - size + 1)):
        matcher.set_seq1(letters[start:start + size])
        if matcher.quick_ratio() >= _NO_ANSWER_LIKENESS and matcher.ratio() >= _NO_ANSWER_LIKENESS:
            return True
    return False


def _letters(text: str) -> str:
    """The plain letters a to z of ``text``, lower case: accents, letters of
    other alphabets, digits, marks and white space are left out."""
    plain = unicodedata.normalize("NFKD", text).lower()
    return "".join(char for char in plain if "a" <= char <= "z")


def _holds_in_order(words: List[str], wording: Tuple[str, ...]) -> bool:
    """Whether ``words`` hold the words of ``wording`` in their order, with at
    most ``_NO_ANSWER_GAP`` words between two of them."""
    for start, word in enumerate(words):
        if word != wording[0]:
            continue
        at = start
        for wanted in wording[1:]:
            window = words[at + 1:at + 2 + _NO_ANSWER_GAP]
            if wanted not in window:
                break
            at += 1 + window.index(wanted)
        else:
            return True
    return False


_NO_ANSWER_KEY = _letters(NO_ANSWER_TEXT)
_NO_ANSWER_WORDING_KEYS = tuple(tuple(_letters(word) for word in wording.split())
                                for wording in NO_ANSWER_WORDINGS)
#: 28 of the sentence's 31 letters in their order.
_NO_ANSWER_LIKENESS = 0.9


# ---------------------------------------------------------------------------
# Check 2: the source
# ---------------------------------------------------------------------------


def check_source(statement: Statement, passages: Mapping[int, str]) -> bool:
    """Whether ``source`` is the number of a passage the search returned."""
    return statement.source in passages


# ---------------------------------------------------------------------------
# Check 3: the quote
# ---------------------------------------------------------------------------

_SOFT_HYPHEN = "\u00ad"
_DOUBLE_MARKS = "\"\u201c\u201d\u201e\u201f\u00ab\u00bb"
_APOSTROPHES = "'\u2018\u2019\u02bc\u00b4`\u2032"
_SINGLE_MARKS = _APOSTROPHES + "\u201a\u201b\u2039\u203a"
_DASHES = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
#: Every kind of quotation mark, apostrophe and dash as its plain form.
_PLAIN = str.maketrans({
    **{mark: '"' for mark in _DOUBLE_MARKS},
    **{mark: "'" for mark in _SINGLE_MARKS},
    **{dash: "-" for dash in _DASHES},
})
#: What a quote may leave out of its first word and of its last word.
_OPENING = "\"'([{"
_CLOSING = "\"')]}.,;:!?\u2026"
#: A space that does not end a word: the one inside a number ("3 000",
#: "1' 500") and the one after a hyphen that ties a word to the next ("un-
#: zulaessig", broken at a line end).
_TIED_SPACE = re.compile("(?<=\\d)('?) ('?)(?=\\d{3}(?!\\d))|(?<=[^\\W_]-)() ()")
_TIE = chr(0)


def check_quote(statement: Statement, passages: Mapping[int, str]) -> Optional[Statement]:
    """The statement with its quote found in the source passage: ``quote``
    is then that piece of the passage, in the passage's own characters, and
    ``span`` where it stands. None when the quote does not stand there."""
    passage = passages[statement.source]
    span = locate_quote(statement.quote, passage)
    if span is None:
        return None
    return replace(statement, quote=passage[span[0]:span[1]], span=span)


def find_quote(quote: str, passage: str) -> Optional[str]:
    """The piece of ``passage`` that ``quote`` copies, or None (see ``locate_quote``)."""
    span = locate_quote(quote, passage)
    return None if span is None else passage[span[0]:span[1]]


def locate_quote(quote: str, passage: str) -> Optional[Tuple[int, int]]:
    """Where ``quote`` stands in ``passage``: ``(start, end)``, or None.

    The quote must stand there word for word and in one piece, and hold at
    least ``MIN_QUOTE_WORDS`` words. Not compared: upper and lower case, the
    amount and kind of white space, the kind of quotation mark, apostrophe or
    dash, characters that are not seen (soft hyphens; white space after a
    soft hyphen belongs to the hyphenation), composed and decomposed accents,
    and quotation marks around the whole quote. A full stop the quote adds
    after its last word is passed over when the passage has none there.

    The quote covers whole words of the passage. A word is what stands
    between two white spaces; of the first one only opening marks may be left
    out, of the last one only closing marks and stops. A number written with
    spaces ("3 000") is one word, and so is a word broken with a hyphen at a
    line end ("un- zulaessig"). So a quote can start neither inside
    "Nicht-Kuendigung" nor inside "1'500". Of several places the first one
    counts; a form with the passage's own full stop before one without.
    """
    wanted, _ = _fold(quote)
    forms = [wanted]
    bare = wanted.strip("\"' ")
    if bare != wanted:
        forms.append(bare)
    forms.extend([form[:-1].rstrip() for form in forms if form.endswith(".") and not form.endswith("..")])
    text, origin = _fold(passage)
    words = _as_words(text)
    for form in forms:
        if not form or _word_count(_as_words(form)) < MIN_QUOTE_WORDS:
            continue
        start = text.find(form)
        while start >= 0:
            end = start + len(form)
            if _covers_whole_words(words, start, end):
                return origin[start], origin[end - 1] + 1
            start = text.find(form, start + 1)
    return None


def _fold(text: str) -> Tuple[str, List[int]]:
    """``text`` as quotes are compared, and for each character of that form
    the index of the character of ``text`` it stands for."""
    chars: List[str] = []
    origin: List[int] = []
    hyphenated = False  # right after a soft hyphen: the word goes on
    for index, char in enumerate(text):
        if char == _SOFT_HYPHEN:
            hyphenated = True
        elif unicodedata.category(char) == "Cf" or ord(char) in _UNSEEN_MARKS:
            continue
        elif char.isspace():
            if not hyphenated and chars and chars[-1] != " ":
                chars.append(" ")
                origin.append(index)
        else:
            hyphenated = False
            for piece in unicodedata.normalize("NFD", char.translate(_PLAIN).lower()):
                chars.append(piece)
                origin.append(index)
    if chars and chars[-1] == " ":
        chars.pop()
        origin.pop()
    return "".join(chars), origin


def _as_words(folded: str) -> str:
    """``folded`` with every space that does not end a word replaced, length
    kept. An accent that stands on its own counts as a letter here."""
    letters = "".join("a" if _is_mark(char) else char for char in folded)
    return _TIED_SPACE.sub(lambda match: (match.group(1) or "") + _TIE + (match.group(2) or ""), letters)


def _word_count(words: str) -> int:
    return sum(1 for word in words.split(" ") if any(char.isalnum() for char in word))


def _covers_whole_words(words: str, start: int, end: int) -> bool:
    before = words[words.rfind(" ", 0, start) + 1:start]
    stop = words.find(" ", end)
    after = words[end:stop if stop >= 0 else len(words)]
    return all(char in _OPENING for char in before) and all(char in _CLOSING for char in after)


# ---------------------------------------------------------------------------
# Check 4: the numbers
# ---------------------------------------------------------------------------
#
# A text is read into tokens: each number it writes, as written. A
# statement's number is a claim, a set of tokens any one of which supports
# it; the quote offers the tokens of the passage that stand wholly inside it.
# Tokens that are not written with digits carry a tag that no text holds:
# "w" a word ("ein", "dreimonatig"), "D" a month or a date, "R" a Roman
# numeral or the letter of an enumeration, "e" a scale (million).

#: Apostrophes and dashes of any kind as their plain form. Other marks stay
#: as written: a low quotation mark between digits looks like a comma.
_NUMBER_PLAIN = str.maketrans({
    **{mark: "'" for mark in _APOSTROPHES},
    **{dash: "-" for dash in _DASHES},
})
#: The digits of one number: thousands set off by an apostrophe, by a space
#: or a line break, or by both ("1'500", "1 500"), then what a stop or a
#: comma ties on ("1.500", "3,5", "31.12.2025").
_DIGITS = re.compile("(?:\\d{1,3}(?:(?:'[ \\n]?|[ \\n]'?)\\d{3})+(?!\\d)|\\d+)(?:[.,]\\d+)*")
#: A sign right before the digits belongs to the number ("-500").
_SIGNS = "-+\u00b1"
#: Per cent, per mille and per ten thousand belong to the number before them.
_PER = "%\u2030\u2031"
#: What may stand between two numbers that are told apart: white space, the
#: marks of a list or a sentence, brackets, and a dash ("5-7" is 5 and 7).
#: Anything else between two numbers makes one of them ("2/3", "14:30").
_BETWEEN_NUMBERS = " \n,;.()[]{}\"!?-"
#: Characters that are written from right to left.
_RIGHT_TO_LEFT = ("R", "AL", "AN")
#: White space that ends a line of a passage; a tab too, it can stand where
#: a line ended.
_LINE_ENDS = frozenset("\n\t\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")
#: What a token that is not written with digits is tagged with.
_TAG = chr(0)

#: Letters that look like digits, and the bars that look like a 1 ("\u01bc"
#: is a 5, "\u01bb" a 2, "\u0292" a 3, "\ua72d" a 4, "\u0185" a 6, "\u0223"
#: an 8, "\ua76f" a 9). Such a character, like every character with a numeric
#: value that is not one of the digits 0 to 9, may stand in a statement only
#: when the same character stands in its quote.
_DIGIT_LOOKALIKES = frozenset(
    "\u01bb\u01bc\u01bd\u01a7\u01a8\u0184\u0185\u01b7\u01b8\u01b9\u01ba\u01ee\u01ef\u0292\u0293"
    "\u1d23\u021c\u021d\u025c\u025d\u025e\ua7ab\ua72a\ua72b\ua72c\ua72d\ua72e\ua72f\ua76a\ua76b"
    "\ua76e\ua76f\ua75c\ua75d\u0222\u0223\u1d15\u0196\u0269\u1d7c\u01c0\u01c1\ua7fe"
    "|\u00a6\u2223\u2502\u2503\u2758\u2759\u275a\ufe31\uff5c"
)

# --- number words -----------------------------------------------------------

_ONES = {"ein": 1, "zwei": 2, "zwo": 2, "drei": 3, "vier": 4, "fuenf": 5, "sechs": 6, "sieben": 7,
         "acht": 8, "neun": 9}
_TEENS = {"zehn": 10, "elf": 11, "zwoelf": 12, "dreizehn": 13, "vierzehn": 14, "fuenfzehn": 15,
          "sechzehn": 16, "siebzehn": 17, "achtzehn": 18, "neunzehn": 19}
_TENS = {"zwanzig": 20, "dreissig": 30, "vierzig": 40, "fuenfzig": 50, "sechzig": 60, "siebzig": 70,
         "achtzig": 80, "neunzig": 90}
_ENGLISH = {"zero": 0, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
            "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
            "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
            "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
            "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000}
#: "ein" and its forms: the article or the number 1. A statement's "ein"
#: claims one of them or the digit 1; a passage's offers only one of them.
_ONE = frozenset({"ein", "eine", "einen", "einem", "einer", "eines", "eins", "one"})
#: The longest German number word read ("neunhundertneunundneunzigtausend...").
_LONGEST_NUMBER_WORD = 60

#: What a number word is a part of when the word goes on: a unit it counts
#: ("dreimonatig", "zweij\u00e4hrig", "zehnfach" count 3, 2 and 10) ...
_COUNTED = ("jaehr", "jahr", "monat", "woech", "woch", "taeg", "tag", "stuend", "stund", "minuet",
            "minut", "sekuend", "sekund", "quartal", "semester", "mal", "zimmer", "seitig", "teilig",
            "stellig", "stufig", "koepfig", "geschoss", "stoeck", "sprachig", "prozent", "promill",
            "fach")
#: ... where "ein" counts 1 only before these ("einj\u00e4hrig", "einmal";
#: not "einfach", "Eintrag", "Einsprache") ...
_ONE_COUNTED = tuple(unit for unit in _COUNTED if unit != "fach")
#: ... or a fraction, which is no whole number ("Zweidrittelmehrheit",
#: "zweieinhalb", "Dreiviertel").
_FRACTIONS = ("einhalb", "halb", "drittel", "viertel", "fuenftel", "sechstel", "siebtel", "achtel",
              "neuntel", "zehntel", "zwoelftel", "zwanzigstel", "hundertstel", "tausendstel")
#: The fractions that count after a number word inside a word ("halb" stands
#: inside "innerhalb" and "deshalb").
_FRACTIONS_INSIDE = tuple(fraction for fraction in _FRACTIONS if fraction != "halb")
#: Beginnings of number words that are no number word on their own: "dritte",
#: "Drittel", "siebte", "halb", "H\u00e4lfte", "anderthalb".
_NUMBER_STEMS = ("anderthalb", "dritt", "haelft", "halb", "siebt")
#: Words that begin with a number word and name no number.
_NOT_NUMBERS = ("zweifel", "zweigniederlass", "zweigstell", "zweigbetrieb", "achtung", "achtsam",
                "achtlos", "achtbar", "elfenbein", "dreistigkeit", "nullit", "nullif")
_NOT_NUMBER_STEMS = frozenset({"zweig", "dreist"})
_ENDINGS = ("en", "er", "es", "em", "e", "n", "s")

#: "ausser Acht lassen", "in Acht nehmen", "Acht geben": in a passage "acht"
#: after "ausser" or beside one of these verbs is the noun, not the number 8.
_NOUN_ACHT_VERBS = frozenset({
    "geben", "gibt", "gab", "gegeben", "nehmen", "nimmt", "nahm", "genommen",
    "lassen", "laesst", "liess", "gelassen",
})
_NOUN_ACHT_BEFORE = _NOUN_ACHT_VERBS | {"ausser"}

_MONTHS = {"januar": 1, "jaenner": 1, "februar": 2, "feber": 2, "maerz": 3, "april": 4, "mai": 5,
           "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11,
           "dezember": 12}
_MONTHS_SHORT = {"jan": 1, "feb": 2, "maer": 3, "mrz": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
                 "sep": 9, "sept": 9, "okt": 10, "nov": 11, "dez": 12}
#: A month that is also a first name ("August Meier").
_NAME_MONTHS = frozenset({"august"})

#: Scales after a number: part of it ("2 Mio." is not "2"). "billion" is a
#: thousand million in English and a million million in German: its own.
_SCALES = {"tsd": "3", "mio": "6", "million": "6", "millionen": "6", "millions": "6", "mrd": "9",
           "milliarde": "9", "milliarden": "9", "billion": "billion", "billionen": "12",
           "billions": "9"}
#: "hundert" and "tausend" after digits scale them too ("5 Tausend" is not 5).
_SCALE_WORDS = {"hundert": "2", "tausend": "3", "hundred": "2", "thousand": "3"}
_PER_WORDS = {"prozent": "%", "proz": "%", "percent": "%", "promille": "\u2030"}

#: Words after which a Roman numeral or a single letter numbers something:
#: "Ziffer IV", "lit. c", "Anhang B".
_ENUMERATION_WORDS = frozenset({
    "ziffer", "ziff", "art", "artikel", "kapitel", "kap", "anhang", "anh", "abschnitt", "abschn",
    "lit", "litera", "bst", "buchst", "buchstabe", "teil", "titel", "abs", "absatz", "nr", "nummer",
    "band", "bd", "buch", "anlage", "beilage",
})
_ROMAN = re.compile("m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})")
_KEYWORD_GAP = re.compile("\\.?\\s+|\\.")
#: What joins the letters of one enumeration: "lit. a, b und c", "lit. a-c".
_LISTING_GAP = re.compile("\\s*(?:[,;/-]\\s*)?(?:(?:und|oder|bis|sowie)\\s+)?")
_DAY = re.compile("[0-9]{1,2}")
_YEAR = re.compile("[0-9]{4}")
_DAY_GAP = re.compile("\\. ?")
_YEAR_GAP = re.compile("\\.? ")


def check_numbers(statement: Statement, passages: Mapping[int, str]) -> bool:
    """Whether every number of the statement stands in its quote.

    The quote is the piece of the source passage ``check_quote`` found
    (``span``); a number elsewhere in the passage does not count. Each claim
    of ``claimed_numbers`` must meet a token that ``offered_numbers`` finds
    wholly inside the quote, and each character of the statement that has a
    numeric value but is no digit 0 to 9, or looks like a digit
    (``_DIGIT_LOOKALIKES``), must stand in the quote itself.
    """
    claims = claimed_numbers(statement.text)
    odd = _odd_characters(statement.text)
    if not claims and not odd:
        return True
    if any(unicodedata.bidirectional(char) in _RIGHT_TO_LEFT for char in statement.text):
        return False  # such a character can show two numbers in another order than they are read
    passage = passages[statement.source]
    span = statement.span if statement.span is not None else locate_quote(statement.quote, passage)
    if span is None:
        return False
    quote = passage[span[0]:span[1]]
    if any(char not in quote for char in odd):
        return False
    offered = offered_numbers(passage, span)
    return all(claim & offered for claim in claims)


def claimed_numbers(statement: str) -> List[FrozenSet[str]]:
    """The numbers a statement claims: for each, the tokens that support it.

    A number is, as written:

    - a run of digits with what belongs to it: thousands set off by an
      apostrophe or a space ("1'500", "1 500"); what a stop or a comma
      between digits ties on ("1.500", "3,5", "31.12.2025"); a sign right
      before it ("-500"), also the word "minus" ("minus 500" is "-500");
      letters, accents and raised digits right after it ("266a", "12bis",
      "10\u00b3"); one letter standing alone after it ("266 a"); a per cent
      or per mille sign or word after it ("5%", "5 Prozent", "5\u2030"); a
      scale word after it ("2 Mio."). Raised digits, fractions and other
      number signs on their own are numbers too. Two numbers with only marks
      between them are one ("2/3", "2 / 3", "14:30"); numbers with a word,
      white space, the marks of a list or a sentence, brackets or a dash
      between them are told apart ("5, 6", "5-7").
    - a German number word, 0 to 999,999 ("drei", "dreizehn", "zwanzig",
      "einundzwanzig", "hundert", "zweitausendvierundzwanzig"), or an English
      one (zero to twenty, the tens, hundred, thousand). It is the same as its
      digits: "drei" is "3".
    - "ein" and its forms ("eine", "einen", "eins", "one"): the article or
      the number 1. It claims a form of "ein" or the digit 1.
    - a word that begins with a number word and goes on ("dreimonatig",
      "zweite", "Zweidrittelmehrheit", "Drittel"): that word, without its
      ending; when it counts a unit ("dreimonatig"), its number too.
    - a month ("M\u00e4rz", "Maerz", "Mrz."), and with a day before it or a
      year after it a date ("1. M\u00e4rz 2024" is one number, not the same
      as "01.03.2024"). "Jan" and "August" may be names: they claim the
      month or the word.
    - a Roman numeral after "Ziffer", "Art.", "Teil" and the like, or one of
      two letters or more in capitals ("IV"); a single letter after such a
      word ("lit. c", "Anhang B") and the letters listed with it.
    - "null und nichtig" is a phrase of its own, not a 0.

    Numbers are compared as written, never by value: "1500.00" is not
    "1500", "266" is not "266a", "1 500" is not "1500", "31. Dezember 2025"
    is not "31.12.2025". One and the same are only "1'500" and "1500", upper
    and lower case, a number word and its digits, and the spellings of a
    month.
    """
    items, _, _ = _read(statement, passage=False)
    return [_tokens(item, passage=False) for item in items]


def offered_numbers(passage: str, span: Optional[Tuple[int, int]] = None) -> FrozenSet[str]:
    """The tokens a passage offers, read as ``claimed_numbers`` reads a
    statement; with ``span`` only those that stand wholly inside it.

    Whole tokens only: "2/3" offers neither 2 nor 3, "14:30" no 30, "10\u2076"
    no 10, "266 a" no 266. Where the passage can be read two ways it offers
    neither reading: two numbers with a line break or a tab between them
    ("Absatz 2" + line break + "500") match no statement; "acht" after
    "ausser" or beside "geben", "nehmen" or "lassen" is the noun and offers
    no 8; "ein" offers only the word, not the digit 1; "Jan" and "August"
    offer the month only in a date.
    """
    items, starts, ends = _read(passage, passage=True)
    offered: Set[str] = set()
    for item in items:
        if span is None or (span[0] <= starts[item.start] and ends[item.end - 1] <= span[1]):
            offered |= _tokens(item, passage=True)
    return frozenset(offered)


def _odd_characters(text: str) -> Set[str]:
    return {char for char in text if char in _DIGIT_LOOKALIKES
            or (not "0" <= char <= "9" and unicodedata.numeric(char, None) is not None)}


class _Item:
    """One number of a read text: where it stands in the read text, its kind
    and how it is written."""

    __slots__ = ("start", "end", "kind", "text", "value", "word", "sure", "per", "scale")

    def __init__(self, start: int, end: int, kind: str, text: str = "", *, value: str = "",
                 word: str = "", sure: bool = True) -> None:
        self.start, self.end, self.kind, self.text = start, end, kind, text
        self.value, self.word, self.sure = value, word, sure
        self.per, self.scale = "", ""


def _tokens(item: _Item, *, passage: bool) -> FrozenSet[str]:
    """What a statement's number accepts, or what a passage's number offers."""
    if item.kind in ("num", "ein"):
        tail = item.per + (_TAG + item.scale if item.scale else "")
        if item.kind == "num":
            return frozenset([item.text + tail])
        own = "w" + _TAG + "ein" + tail
        return frozenset([own] if passage else [own, "1" + tail])
    if item.kind == "month":
        month = "D" + _TAG + "." + item.text + "."
        if item.sure:
            return frozenset([month])
        word = "w" + _TAG + item.word
        return frozenset([word] if passage else [month, word])
    if item.kind == "date":
        return frozenset(["D" + _TAG + item.text])
    if item.kind == "roman":
        return frozenset(["R" + _TAG + item.text])
    if item.kind == "scale":
        return frozenset(["e" + _TAG + item.text])
    own = "w" + _TAG + item.text  # "compound", "phrase"
    return frozenset([own, item.value] if item.value else [own])


def _read(text: str, *, passage: bool) -> Tuple[List[_Item], List[int], List[int]]:
    """The numbers of ``text``, and for each character of the read text where
    it starts and ends in ``text``."""
    cased, starts, ends = _plain_for_numbers(text, keep_lines=passage)
    plain = "".join(char.lower() if len(char.lower()) == 1 else char for char in cased)
    items = _join_runs(plain, _digit_numbers(plain))
    items.extend(_word_numbers(plain, cased, items, passage=passage))
    items.sort(key=lambda item: item.start)
    return _join_dates(plain, _apply_modifiers(plain, items)), starts, ends


def _plain_for_numbers(text: str, *, keep_lines: bool) -> Tuple[str, List[int], List[int]]:
    """``text`` as numbers are read, with for each of its characters where it
    starts and ends in ``text``.

    What is not seen is left out (also inside a number: "1\u00ad500" is
    1500); accents are composed; apostrophes and dashes of any kind are
    plain. White space is one space, or -- with ``keep_lines`` -- one or two
    line breaks where it holds line breaks or tabs; after a soft hyphen it is
    nothing, the word goes on ("drei\u00ad" + line break + "zehn").
    """
    chars: List[str] = []
    starts: List[int] = []
    ends: List[int] = []
    at, size = 0, len(text)
    while at < size:
        if text[at].isspace() or not _is_seen(text[at]):
            stop, spaced, breaks, first_space = at, False, 0, -1
            while stop < size and (text[stop].isspace() or not _is_seen(text[stop])):
                char = text[stop]
                if char.isspace():
                    spaced = True
                    first_space = stop if first_space < 0 else first_space
                    if char in _LINE_ENDS or (char == "\r" and text[stop + 1:stop + 2] != "\n"):
                        breaks += 1
                stop += 1
            if spaced and not _after_soft_hyphen(text, first_space):
                for char in ("\n" * min(breaks, 2) if keep_lines and breaks else " "):
                    chars.append(char)
                    starts.append(at)
                    ends.append(stop)
            at = stop
            continue
        stop, last = at + 1, at
        while stop < size and not text[stop].isspace() and (_is_mark(text[stop]) or not _is_seen(text[stop])):
            if _is_seen(text[stop]):
                last = stop
            stop += 1
        piece = unicodedata.normalize("NFC", "".join(char for char in text[at:stop] if _is_seen(char)))
        for char in piece.translate(_NUMBER_PLAIN):
            chars.append(char)
            starts.append(at)
            ends.append(last + 1)
        at = stop
    return "".join(chars), starts, ends


def _after_soft_hyphen(text: str, at: int) -> bool:
    """Whether the last character before ``at`` that is not an unseen one
    other than the soft hyphen is a soft hyphen."""
    at -= 1
    while at >= 0 and text[at] != _SOFT_HYPHEN and not _is_seen(text[at]):
        at -= 1
    return at >= 0 and text[at] == _SOFT_HYPHEN


def _is_number_sign(char: str) -> bool:
    """A raised digit, a fraction, a circled number: a number, but no digit."""
    return char.isnumeric() and not char.isdecimal()


def _digit_numbers(plain: str) -> List[_Item]:
    """The numbers of ``plain`` that are written with digits or number signs."""
    found: List[_Item] = []
    at, size = 0, len(plain)
    while at < size:
        match = _DIGITS.match(plain, at) if plain[at].isdecimal() else None
        if match is None and not _is_number_sign(plain[at]):
            at += 1
            continue
        start = at
        if match is None:  # number signs on their own
            end = at + 1
            while end < size and _is_number_sign(plain[end]):
                end += 1
            written = plain[at:end]
        else:
            end = match.end()
            written = plain[at:end].replace("'", "")
            if at and plain[at - 1] in _SIGNS and not plain[at - 2:at - 1].isalnum():
                start, written = at - 1, plain[at - 1] + written
            while end < size and (plain[end].isalpha() or _is_mark(plain[end])
                                  or _is_number_sign(plain[end])):
                end += 1
            written += plain[match.end():end]
            if end == match.end() and _lone_letter_follows(plain, end):
                written += plain[end:end + 2]
                end += 2
        if plain[end:end + 1] and plain[end] in _PER:
            written += plain[end]
            end += 1
        elif plain[end:end + 1] == " " and plain[end + 1:end + 2] and plain[end + 1] in _PER:
            written += plain[end + 1]
            end += 2
        found.append(_Item(start, end, "num", written))
        at = end
    return found


def _lone_letter_follows(plain: str, at: int) -> bool:
    """Whether ``plain`` goes on at ``at`` with a space or a hyphen and one
    letter that is a word of its own ("266 a", "266-a")."""
    if plain[at:at + 1] not in (" ", "-") or not plain[at + 1:at + 2].isalpha():
        return False
    after = plain[at + 2:at + 3]
    return not (after.isalnum() or after in ("'", "-") or (after != "" and _is_mark(after)))


def _join_runs(plain: str, items: List[_Item]) -> List[_Item]:
    """Numbers with only marks between them become one ("2/3", "14:30")."""
    runs: List[_Item] = []
    for item in items:
        if runs:
            last = runs[-1]
            between = plain[last.end:item.start]
            if not any(char.isalnum() for char in between) and (
                    not between or any(char not in _BETWEEN_NUMBERS for char in between)):
                last.text += between + item.text
                last.end = item.end
                continue
        runs.append(item)
    return runs


#: Latin letters that look like plain ones: small capitals, the dotless i,
#: the script a and g. A number word is read through them ("dre\u0131").
_PLAIN_LETTERS = str.maketrans({
    "\u1d00": "a", "\u0299": "b", "\u1d04": "c", "\u1d05": "d", "\u1d07": "e", "\ua730": "f",
    "\u0262": "g", "\u029c": "h", "\u026a": "i", "\u1d0a": "j", "\u1d0b": "k", "\u029f": "l",
    "\u1d0d": "m", "\u0274": "n", "\u1d0f": "o", "\u1d18": "p", "\u0280": "r", "\ua731": "s",
    "\u1d1b": "t", "\u1d1c": "u", "\u1d20": "v", "\u1d21": "w", "\u028f": "y", "\u1d22": "z",
    "\u0131": "i", "\u0237": "j", "\u0251": "a", "\u0261": "g",
})
#: Spellings of number words and months without the umlaut.
_SPELLINGS = (("funf", "fuenf"), ("zwolf", "zwoelf"), ("marz", "maerz"), ("janner", "jaenner"))


def _word_key(word: str) -> str:
    """A word as number words are looked up: lower case, umlauts as ``ae``,
    ``oe``, ``ue`` and ``ss``, other accents and marks left out, letters that
    look like plain ones made plain, "funf" spelled "fuenf"."""
    folded = (word.replace("\u00e4", "ae").replace("\u00f6", "oe").replace("\u00fc", "ue")
              .replace("\u00df", "ss"))
    key = "".join(char for char in unicodedata.normalize("NFKD", folded) if not _is_mark(char))
    key = key.lower().translate(_PLAIN_LETTERS)
    for written, spelled in _SPELLINGS:
        key = key.replace(written, spelled)
    return key


def _word_spans(plain: str) -> List[Tuple[int, int]]:
    """Where the words of ``plain`` stand: letters, and the marks on them."""
    spans: List[Tuple[int, int]] = []
    at, size = 0, len(plain)
    while at < size:
        if not plain[at].isalpha():
            at += 1
            continue
        end = at + 1
        while end < size and (plain[end].isalpha() or _is_mark(plain[end])):
            end += 1
        spans.append((at, end))
        at = end
    return spans


def _word_numbers(plain: str, cased: str, taken: List[_Item], *, passage: bool) -> List[_Item]:
    """The numbers of ``plain`` written with words, and the words that change
    a number ("minus", "Prozent", "Mio.") as items to apply."""
    covered = bytearray(len(plain))
    for item in taken:
        covered[item.start:item.end] = b"\x01" * (item.end - item.start)
    spans = [span for span in _word_spans(plain) if not covered[span[0]]]
    words = [_word_key(plain[start:end]) for start, end in spans]
    skipped = _broken_words(plain, spans) if passage else set()
    found: List[_Item] = []
    listing_end, at = -1, 0
    while at < len(spans):
        current = at
        at += 1
        (start, end), word = spans[current], words[current]
        if current in skipped:
            continue
        if (word == "null" and words[current + 1:current + 3] == ["und", "nichtig"]
                and _spaced(plain, spans[current:current + 3])):
            found.append(_Item(start, spans[current + 2][1], "phrase", "null und nichtig"))
            at = current + 3
            continue
        joined, last = _joined_number_word(plain, spans, words, current)
        if last > current:
            found.append(_Item(start, spans[last][1], "num", str(_number_word(joined)), word=joined))
            at = last + 1
            continue
        month = _month(word)
        if month is not None:
            found.append(_Item(start, end, "month", str(month[0]), word=word, sure=month[1]))
            continue
        if word in _ONE:
            found.append(_Item(start, end, "ein", word=word))
            continue
        value = _number_word(word)
        if value is None:
            value = _ENGLISH.get(word)
        if value is not None:
            if not (passage and word == "acht" and _is_noun_acht(words, current)):
                found.append(_Item(start, end, "num", str(value), word=word))
            continue
        if word == "minus":
            found.append(_Item(start, end, "minus"))
            continue
        if word in _PER_WORDS:
            found.append(_Item(start, end, "per", _PER_WORDS[word]))
            continue
        if word in _SCALES:
            found.append(_Item(start, end, "scale", _SCALES[word]))
            continue
        letter = len(word) == 1 and "a" <= word <= "z"
        roman = _ROMAN.fullmatch(word) is not None
        after_keyword = (current > 0 and words[current - 1] in _ENUMERATION_WORDS
                         and _KEYWORD_GAP.fullmatch(plain[spans[current - 1][1]:start]) is not None)
        listed = listing_end >= 0 and _LISTING_GAP.fullmatch(plain[listing_end:start]) is not None
        if ((letter or roman) and (after_keyword or listed)) or (
                roman and len(word) >= 2 and cased[start:end].isupper()):
            found.append(_Item(start, end, "roman", word))
            listing_end = end
            continue
        compound = _compound(word)
        if compound is not None:
            found.append(_Item(start, end, "compound", compound[0], value=compound[1]))
    return found


def _spaced(plain: str, spans: List[Tuple[int, int]]) -> bool:
    """Whether only white space stands between these words."""
    return all(plain[left[1]:right[0]].isspace() for left, right in zip(spans, spans[1:]))


def _joined_number_word(plain: str, spans: List[Tuple[int, int]], words: List[str],
                        at: int) -> Tuple[str, int]:
    """A number written in several words ("zwei hundert", "drei und zwanzig",
    "ein tausend"): the word it makes and the index of its last word. A reader
    reads one number there, so it is one."""
    joined, last = words[at], at
    if _number_word(joined) is None and joined not in ("ein", "eine"):
        return joined, at
    while last + 1 < len(spans) and _spaced(plain, spans[last:last + 2]):
        following = words[last + 1]
        if following.startswith(("hundert", "tausend")):
            step = 1
        elif (following == "und" and last + 2 < len(spans) and words[last + 2] in _TENS
              and _spaced(plain, spans[last + 1:last + 3])):
            step = 2
        else:
            break
        candidate = (joined[:3] if joined == "eine" else joined) + "".join(words[last + 1:last + 1 + step])
        if _number_word(candidate) is None:
            break
        joined, last = candidate, last + step
    return joined, last


def _broken_words(plain: str, spans: List[Tuple[int, int]]) -> Set[int]:
    """The words of a passage that a hyphen and a line break join or part
    ("drei-" + line break + "zehn" is 13, or 3 and 10): read neither way."""
    broken: Set[int] = set()
    for at in range(len(spans) - 1):
        gap = plain[spans[at][1]:spans[at + 1][0]]
        if gap[:1] == "-" and "\n" in gap and gap[1:].isspace():
            broken.update((at, at + 1))
    return broken


def _month(word: str) -> Optional[Tuple[int, bool]]:
    """The month a word names, and whether surely (outside a date)."""
    if word in _MONTHS:
        return _MONTHS[word], word not in _NAME_MONTHS
    if word.endswith("s") and word[:-1] in _MONTHS and word != "mais":
        return _MONTHS[word[:-1]], word[:-1] not in _NAME_MONTHS
    if word in _MONTHS_SHORT:
        return _MONTHS_SHORT[word], False
    return None


def _is_noun_acht(words: List[str], at: int) -> bool:
    before = words[at - 1] if at else ""
    after = [word for word in words[at + 1:at + 3] if word != "zu"][:1]
    return before in _NOUN_ACHT_BEFORE or (bool(after) and after[0] in _NOUN_ACHT_VERBS)


def _number_word(word: str) -> Optional[int]:
    """The value of a German number word from 0 to 999,999 (umlauts as
    ``ae``, ``oe``, ``ue``, lower case), or None. "ein" and "eins" alone are
    not read here."""
    if len(word) > _LONGEST_NUMBER_WORD:
        return None
    if word == "null":
        return 0
    head, thousand, tail = word.partition("tausend")
    if not thousand:
        return _below_thousand(word)
    times = 1 if head in ("", "ein") else _below_thousand(head)
    rest = _rest(tail, _below_thousand)
    return None if times is None or rest is None else times * 1000 + rest


def _below_thousand(word: str) -> Optional[int]:
    head, hundred, tail = word.partition("hundert")
    if not hundred:
        return _below_hundred(word)
    times = 1 if head in ("", "ein") else _below_hundred(head)
    rest = _rest(tail, _below_hundred)
    return None if times is None or rest is None else times * 100 + rest


def _rest(tail: str, read: Any) -> Optional[int]:
    if not tail:
        return 0
    if tail.startswith("und"):
        tail = tail[3:]
    return 1 if tail in ("ein", "eins") else read(tail)


def _below_hundred(word: str) -> Optional[int]:
    if word in _ONES and word != "ein":
        return _ONES[word]
    if word in _TEENS:
        return _TEENS[word]
    if word in _TENS:
        return _TENS[word]
    for tens, value in _TENS.items():
        if word.endswith("und" + tens) and word[:-len(tens) - 3] in _ONES:
            return _ONES[word[:-len(tens) - 3]] + value
    return None


def _stem(word: str) -> str:
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[:-len(ending)]
    return word


def _compound(word: str) -> Optional[Tuple[str, str]]:
    """``(stem, value)`` when ``word`` begins with a number word and goes on:
    ``value`` is that number when the rest counts a unit ("dreimonatig": 3),
    else empty ("zweite", "Zweidrittelmehrheit"). Also when a number word
    stands inside the word before a unit or a fraction
    ("Mindestdreimonatsfrist"). None for other words."""
    if word.startswith(_NOT_NUMBERS) or _stem(word) in _NOT_NUMBER_STEMS:
        return None
    if word in _NUMBER_STEMS:
        return word, ""
    for cut in range(min(len(word) - 1, _LONGEST_NUMBER_WORD), 2, -1):
        head, rest = word[:cut], word[cut:]
        if head in ("ein", "eins"):
            if rest.startswith(_FRACTIONS):
                return _stem(word), ""
            if rest.startswith(_ONE_COUNTED):
                return _stem(word), "1"
            continue
        value = _number_word(head)
        if value is not None:
            counts = rest.startswith(_COUNTED) and not rest.startswith(_FRACTIONS)
            return _stem(word), (str(value) if counts else "")
        if head in _NUMBER_STEMS:
            return _stem(word), ""
    return _inner_number(word)


def _inner_number(word: str) -> Optional[Tuple[str, str]]:
    """A number word inside ``word``, right before a unit it counts or a
    fraction ("Mindestdreimonatsfrist", "Mindestzweidrittelmehrheit"). "acht"
    alone does not count there: "Pachtjahr", "Nachtmal"."""
    size = min(len(word), _LONGEST_NUMBER_WORD)
    for at in range(4, size - 2):
        rest = word[at:]
        counts = rest.startswith(_COUNTED)
        if not counts and not rest.startswith(_FRACTIONS_INSIDE):
            continue
        for start in range(1, at - 2):
            head = word[start:at]
            value = _number_word(head)
            if value is not None and head != "acht":
                return _stem(word), (str(value) if counts and not rest.startswith(_FRACTIONS) else "")
    return None


def _apply_modifiers(plain: str, items: List[_Item]) -> List[_Item]:
    """"minus" before a number, "Prozent" and "Promille" after it, and a
    scale after it ("2 Mio.") become part of that number."""
    out: List[_Item] = []
    for item in items:
        last = out[-1] if out else None
        joined = last is not None and plain[last.end:item.start].isspace()
        if joined and item.kind == "num" and last.kind == "minus":
            out.pop()
            item.start = last.start
            if not item.text.startswith(tuple(_SIGNS)):
                item.text = "-" + item.text
        elif (joined and item.kind == "per" and last.kind in ("num", "ein") and not last.per
              and not last.scale and not (last.text and last.text[-1] in _PER)):
            last.per, last.end = item.text, item.end
            continue
        elif joined and item.kind == "scale" and last.kind in ("num", "ein") and not last.scale:
            last.scale, last.end = item.text, item.end
            continue
        elif (joined and item.kind == "num" and item.word in _SCALE_WORDS and last.kind == "num"
              and not last.word and not last.per and not last.scale):
            last.scale, last.end = _SCALE_WORDS[item.word], item.end  # "5 Tausend" is not 5
            continue
        out.append(item)
    return [item for item in out if item.kind not in ("minus", "per")]


def _join_dates(plain: str, items: List[_Item]) -> List[_Item]:
    """A month with a day before it ("1. M\u00e4rz") or a year after it
    ("M\u00e4rz 2024"), or both, is one date."""
    out: List[_Item] = []
    at = 0
    while at < len(items):
        item = items[at]
        if item.kind == "month":
            day = out[-1] if out and _is_plain(out[-1], _DAY) else None
            if day is not None and _DAY_GAP.fullmatch(plain[day.end:item.start]) is None:
                day = None
            year = items[at + 1] if at + 1 < len(items) and _is_plain(items[at + 1], _YEAR) else None
            if year is not None and _YEAR_GAP.fullmatch(plain[item.end:year.start]) is None:
                year = None
            if day is not None or year is not None:
                if day is not None:
                    out.pop()
                out.append(_Item(day.start if day is not None else item.start,
                                 year.end if year is not None else item.end, "date",
                                 (day.text if day is not None else "") + "." + item.text + "."
                                 + (year.text if year is not None else "")))
                at += 2 if year is not None else 1
                continue
        out.append(item)
        at += 1
    return out


def _is_plain(item: _Item, pattern: "re.Pattern[str]") -> bool:
    """A number written in digits alone, nothing on it."""
    return (item.kind == "num" and not item.word and not item.per and not item.scale
            and pattern.fullmatch(item.text) is not None)


# ---------------------------------------------------------------------------
# Check 5: the length
# ---------------------------------------------------------------------------


def check_length(statement: Statement) -> bool:
    """Whether the statement is one statement and not an essay."""
    return len(statement.text) <= MAX_STATEMENT_CHARS
