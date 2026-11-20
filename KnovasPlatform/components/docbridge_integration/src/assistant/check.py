"""The check of the Knovas Assistent's answer (PMVP-20, rework 2).

The model is told to answer in statements, one JSON object per line::

    {"statement": "...", "source": 2, "quote": "..."}

or ``{"no_answer": true}`` when the passages do not answer the question.
Nothing is cut into sentences: the unit is the statement the model declared.
A statement is shown only if all five checks pass; otherwise it is removed
and counted under the first check it failed:

1. ``check_form``     the line is such an object            ``not_a_statement``
2. ``check_source``   ``source`` is a retrieved passage     ``unknown_source``
3. ``check_quote``    ``quote`` stands in that passage      ``quote_not_found``
4. ``check_numbers``  every number stands in that passage   ``number_not_found``
5. ``check_length``   at most ``MAX_STATEMENT_CHARS``       ``too_long``

One function per check, called in ``check_answer``; a further check (a judge
model, follow-up FU-27) is one more function and one more line there.

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
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

#: What the person reads when nothing supported is left. Kept here because
#: the check must recognise it; ``assistant.service`` hands it on.
NO_ANSWER_TEXT = "Dazu finde ich nichts in Ihren Quellen"

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
    wording until ``check_quote`` has found it; what ``check_answer`` returns
    carries the passage's own characters instead.
    """

    text: str
    source: int
    quote: str


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
        statement = check_form(value)
        if statement is None:
            removed_by[NOT_A_STATEMENT] += 1
            continue
        if not check_source(statement, passages):
            removed_by[UNKNOWN_SOURCE] += 1
            continue
        found = check_quote(statement, passages)
        if found is None:
            removed_by[QUOTE_NOT_FOUND] += 1
        elif not check_numbers(statement, passages):
            removed_by[NUMBER_NOT_FOUND] += 1
        elif not check_length(statement):
            removed_by[TOO_LONG] += 1
        else:
            shown.append(replace(statement, quote=found))
    return shown, removed_by


# ---------------------------------------------------------------------------
# Reading the answer: lines, not prose
# ---------------------------------------------------------------------------

#: A line that is not JSON at all (``null`` is JSON, so None cannot say it).
_NOT_JSON = object()
_FENCE = "```"
#: "- ", "* ", "2. ", "3) " before an object.
_LIST_MARK = re.compile("^(?:[-*\u2022\u2013]|\\d{1,3}[.)])\\s+")
#: What an array leaves on a line of its own or around an object.
_ARRAY_MARKS = re.compile("^\\[\\s*|[\\],\\s]+$")


def read_answer(text: str) -> List[Any]:
    """The values the answer's lines hold, in order.

    One value per line: the decoded JSON, or a marker for a line that is not
    JSON (prose, a broken object, an object with prose around it). Blank
    lines, code fence lines and the plain no-answer sentence give nothing.
    List marks before an object and the brackets and commas of an array are
    not part of the line. An answer that is one JSON array or one object
    spread over several lines is read as a whole.
    """
    # Only a line feed ends a line: JSON allows other line separators inside a text.
    lines = [line.strip() for line in (text or "").split("\n")]
    lines = [line for line in lines if line and not line.startswith(_FENCE)]
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
            line = _ARRAY_MARKS.sub("", line)
            if not line:
                continue  # a bracket or a comma alone
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

_SOURCE_DIGITS = re.compile("\\s*[0-9]{1,6}\\s*")
#: A passage's number mark inside a statement, with the space before it:
#: "[1]", "[1, 3]", and the full-width brackets some models write.
_MARK = re.compile("\\s*[\\[\u3010]\\s*\\d{1,4}(?:\\s*[,;]\\s*\\d{1,4})*\\s*[\\]\u3011]")


def check_form(value: Any) -> Optional[Statement]:
    """The statement a decoded line holds, or None when it holds none.

    A statement is a JSON object with a text under ``statement``, a whole
    number under ``source`` (a digit string is read) and a text under
    ``quote``; further keys are ignored. The no-answer sentence is not a
    statement, whatever source and quote come with it.
    """
    if not isinstance(value, dict):
        return None
    text, source, quote = value.get("statement"), value.get("source"), value.get("quote")
    if not isinstance(text, str) or not isinstance(quote, str) or not quote.strip():
        return None
    if isinstance(source, str) and _SOURCE_DIGITS.fullmatch(source):
        source = int(source)
    if not isinstance(source, int) or isinstance(source, bool):
        return None
    shown = shown_text(text)
    if not any(char.isalpha() for char in shown) or _NO_ANSWER_KEY in _letters(shown):
        return None
    return Statement(text=shown, source=source, quote=quote)


def shown_text(statement: str) -> str:
    """A statement as the page shows it: number marks and characters that
    have no width or are controls taken out, white space single. Checks 4 and
    5 read this text, so nothing they did not see is shown."""
    visible = "".join(char for char in statement
                      if char.isspace() or unicodedata.category(char)[0] != "C")
    return " ".join(_MARK.sub("", visible).split())


def _letters(text: str) -> str:
    return "".join(char for char in text.lower() if char.isalpha())


_NO_ANSWER_KEY = _letters(NO_ANSWER_TEXT)


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
_SINGLE_MARKS = "'\u2018\u2019\u201a\u201b\u2039\u203a\u02bc\u00b4`\u2032"
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


def check_quote(statement: Statement, passages: Mapping[int, str]) -> Optional[str]:
    """The piece of the source passage that the quote copies, in the
    passage's own characters, or None when the quote does not stand there."""
    return find_quote(statement.quote, passages[statement.source])


def find_quote(quote: str, passage: str) -> Optional[str]:
    """Where ``quote`` stands in ``passage``: that piece of the passage, or None.

    The quote must stand there word for word and in one piece, and hold at
    least ``MIN_QUOTE_WORDS`` words. Not compared: upper and lower case, the
    amount and kind of white space, the kind of quotation mark, apostrophe or
    dash, characters without width (soft hyphens; white space after a soft
    hyphen belongs to the hyphenation), composed and decomposed accents, and
    quotation marks around the whole quote.

    The quote covers whole words of the passage. A word is what stands
    between two white spaces; of the first one only opening marks may be left
    out, of the last one only closing marks and stops. So a quote can start
    neither inside "Nicht-Kuendigung" nor inside "1'500". Of several places
    the first one counts.
    """
    wanted, _ = _fold(quote)
    forms = [wanted]
    bare = wanted.strip("\"' ")
    if bare != wanted:
        forms.append(bare)
    text, origin = _fold(passage)
    for form in forms:
        if _word_count(form) < MIN_QUOTE_WORDS:
            continue
        start = text.find(form)
        while start >= 0:
            end = start + len(form)
            if _covers_whole_words(text, start, end):
                return passage[origin[start]:origin[end - 1] + 1]
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
        elif unicodedata.category(char) == "Cf":
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


def _word_count(folded: str) -> int:
    return sum(1 for word in folded.split(" ") if any(char.isalnum() for char in word))


def _covers_whole_words(folded: str, start: int, end: int) -> bool:
    before = folded[folded.rfind(" ", 0, start) + 1:start]
    stop = folded.find(" ", end)
    after = folded[end:stop if stop >= 0 else len(folded)]
    return all(char in _OPENING for char in before) and all(char in _CLOSING for char in after)


# ---------------------------------------------------------------------------
# Check 4: the numbers
# ---------------------------------------------------------------------------

#: The digits of a number: between two digits may stand an apostrophe
#: ("1'500"), a stop or a comma ("1.500", "3,5", "31.12.2025"), or a space
#: before exactly three digits ("1 500"). Letters right after the digits
#: belong to the number ("266a", "12bis"); ``numbers_in`` adds them.
_DIGITS = re.compile("(?:\\d{1,3}(?: \\d{3})+(?!\\d)|\\d+(?:'\\d+)*)(?:[.,]\\d+)*")
_WORD = re.compile("[^\\W\\d_]+")
#: 1 to 12 as German words. "ein", "eine" ... are not here: they are the
#: article far more often than the number.
_NUMBER_WORDS = {
    "eins": "1", "zwei": "2", "drei": "3", "vier": "4", "f\u00fcnf": "5", "fuenf": "5",
    "sechs": "6", "sieben": "7", "acht": "8", "neun": "9", "zehn": "10", "elf": "11",
    "zw\u00f6lf": "12", "zwoelf": "12",
}


def check_numbers(statement: Statement, passages: Mapping[int, str]) -> bool:
    """Whether every number of the statement stands in its source passage."""
    return numbers_in(statement.text) <= numbers_in(passages[statement.source])


def numbers_in(text: str) -> Set[str]:
    """The numbers ``text`` holds, each in one spelling.

    Numbers are compared as written, never by value: "1500.00" is not "1500"
    and "266" is not "266a". Only these are one number: the thousands marks
    ("1'500", "1 500" and "1500"), upper and lower case of attached letters,
    digits of other scripts, and the German words for 1 to 12 ("drei" and
    "3"), in the statement as in the passage. Raised digits, fractions and
    other number signs are numbers of their own and never part of another.

    Read is what a person sees: characters without width, controls and marks
    set over or through a character are skipped, and white space of any kind
    is one space. A tab or a line break always separates two numbers.
    """
    plain = unicodedata.normalize("NFC", text).translate(_PLAIN).lower().replace("\t", "\n")
    plain = "".join(char for char in plain
                    if char.isspace() or unicodedata.category(char)[0] not in "CM")
    plain = "\n".join(" ".join(line.split()) for line in plain.splitlines())
    found = {_NUMBER_WORDS[word] for word in _WORD.findall(plain) if word in _NUMBER_WORDS}
    found.update("".join(char if char.isnumeric() and not char.isdecimal() else " "
                         for char in plain).split())
    for match in _DIGITS.finditer(plain):
        end = match.end()
        while end < len(plain) and plain[end].isalpha():
            end += 1
        found.add(_number_key(plain[match.start():end]))
    return found


def _number_key(number: str) -> str:
    return "".join(str(int(char)) if char.isdecimal() else char
                   for char in number if char not in "' ")


# ---------------------------------------------------------------------------
# Check 5: the length
# ---------------------------------------------------------------------------


def check_length(statement: Statement) -> bool:
    """Whether the statement is one statement and not an essay."""
    return len(statement.text) <= MAX_STATEMENT_CHARS
