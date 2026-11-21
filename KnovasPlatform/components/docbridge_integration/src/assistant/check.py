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
from typing import Any, Dict, List, Mapping, Optional, Tuple

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
_FENCE = "```"
#: "- ", "* ", "2. ", "3) " before an object.
_LIST_MARK = re.compile("^(?:[-*\u2022\u2013]|\\d{1,3}[.)])\\s+")


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
#: A passage's number mark inside a statement, with the space before it:
#: "[1]", "[1, 3]", and the full-width brackets some models write.
_MARK = re.compile("\\s?[\\[\u3010]\\s?\\d{1,4}(?:\\s?[,;]\\s?\\d{1,4})*\\s?[\\]\u3011]")


def check_form(value: Any) -> Optional[Statement]:
    """The statement a decoded line holds, or None when it holds none.

    A statement is a JSON object with a text under ``statement``, a whole
    number under ``source`` (a digit string is read) and a text under
    ``quote``; further keys are ignored. Its text holds a letter, and all its
    letters are Latin ones: a letter of another alphabet can look like a
    Latin letter or like a digit, and the checks could not tell. The
    no-answer sentence is not a statement, whatever source and quote come
    with it.
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
    shown = shown_text(text)
    letters = [char for char in shown if char.isalpha()]
    if not letters or not all(unicodedata.name(char, "").startswith("LATIN") for char in letters):
        return None
    if says_no_answer(shown):
        return None
    return Statement(text=shown, source=source, quote=quote)


def shown_text(statement: str) -> str:
    """A statement as the page shows it: number marks and what is not seen
    (characters without width, controls) taken out, white space single. The
    later checks read this text, so nothing they did not see is shown."""
    visible = "".join(char for char in statement if _is_seen(char))
    return " ".join(_MARK.sub("", " ".join(visible.split())).split())


def says_no_answer(statement: str) -> bool:
    """Whether a statement is the no-answer sentence: as it stands, inside a
    wrapper, or with a few letters changed or left out."""
    letters = _letters(statement[:MAX_STATEMENT_CHARS])
    if _NO_ANSWER_KEY in letters:
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


_NO_ANSWER_KEY = _letters(NO_ANSWER_TEXT)
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


def check_quote(statement: Statement, passages: Mapping[int, str]) -> Optional[str]:
    """The piece of the source passage that the quote copies, in the
    passage's own characters, or None when the quote does not stand there."""
    return find_quote(statement.quote, passages[statement.source])


def find_quote(quote: str, passage: str) -> Optional[str]:
    """Where ``quote`` stands in ``passage``: that piece of the passage, or None.

    The quote must stand there word for word and in one piece, and hold at
    least ``MIN_QUOTE_WORDS`` words. Not compared: upper and lower case, the
    amount and kind of white space, the kind of quotation mark, apostrophe or
    dash, characters that are not seen (soft hyphens; white space after a
    soft hyphen belongs to the hyphenation), composed and decomposed accents,
    and quotation marks around the whole quote.

    The quote covers whole words of the passage. A word is what stands
    between two white spaces; of the first one only opening marks may be left
    out, of the last one only closing marks and stops. A number written with
    spaces ("3 000") is one word, and so is a word broken with a hyphen at a
    line end ("un- zulaessig"). So a quote can start neither inside
    "Nicht-Kuendigung" nor inside "1'500". Of several places the first one
    counts.
    """
    wanted, _ = _fold(quote)
    forms = [wanted]
    bare = wanted.strip("\"' ")
    if bare != wanted:
        forms.append(bare)
    text, origin = _fold(passage)
    words = _as_words(text)
    for form in forms:
        if _word_count(_as_words(form)) < MIN_QUOTE_WORDS:
            continue
        start = text.find(form)
        while start >= 0:
            end = start + len(form)
            if _covers_whole_words(words, start, end):
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
#: What may stand between two numbers that are told apart: white space, the
#: marks of a list or a sentence, brackets, and a dash ("5-7" is 5 and 7).
#: Anything else between two numbers makes one of them ("2/3", "14:30").
_BETWEEN_NUMBERS = " \n,;.()[]{}\"!?-"
_OPEN, _CLOSE = chr(1), chr(2)
_WORD = re.compile("[^\\W\\d_]+")
#: 2 to 12 as German words. 1 is always a digit: "ein", "eine", "eins" are
#: the article or another word far more often than the number.
_NUMBER_WORDS = {
    "zwei": "2", "drei": "3", "vier": "4", "f\u00fcnf": "5", "fuenf": "5", "sechs": "6",
    "sieben": "7", "acht": "8", "neun": "9", "zehn": "10", "elf": "11", "zw\u00f6lf": "12",
    "zwoelf": "12",
}
#: "ausser Acht lassen", "in Acht nehmen", "Acht geben": in a passage "acht"
#: after "ausser" or beside one of these verbs is the noun, not the number 8.
_NOUN_ACHT_VERBS = frozenset({
    "geben", "gibt", "gab", "gegeben", "nehmen", "nimmt", "nahm", "genommen",
    "lassen", "l\u00e4sst", "laesst", "liess", "lie\u00df", "gelassen",
})
_NOUN_ACHT_BEFORE = _NOUN_ACHT_VERBS | {"ausser", "au\u00dfer"}
#: Characters that are written from right to left.
_RIGHT_TO_LEFT = ("R", "AL", "AN")


def check_numbers(statement: Statement, passages: Mapping[int, str]) -> bool:
    """Whether every number of the statement stands in its source passage."""
    claimed = claimed_numbers(statement.text)
    if claimed and any(unicodedata.bidirectional(char) in _RIGHT_TO_LEFT for char in statement.text):
        return False  # such a character can show two numbers in another order than they are read
    offered = offered_numbers(passages[statement.source])
    return all(claim in offered for claim in claimed)


def claimed_numbers(statement: str) -> List[str]:
    """The numbers a statement claims, each as written.

    A number is a run of digits, with what belongs to it: thousands set off
    by an apostrophe or a space ("1'500", "1 500"); what a stop or a comma
    between digits ties on ("1.500", "3,5", "31.12.2025"); a sign right
    before it ("-500"); letters, accents and raised digits right after it
    ("266a", "12bis", "10\u00b3"); and one letter standing alone after it
    ("266 a", "266-a"). Raised digits, fractions and other number signs on
    their own are numbers too.

    Numbers are compared as written, never by value: "1500.00" is not "1500",
    "266" is not "266a", "1 500" is not "1500". One and the same are only
    "1'500" and "1500", upper and lower case, and the German words for 2 to
    12 ("drei" and "3").

    Two numbers with only marks between them are one claim, as they stand:
    "2/3" is not a 2 and a 3, and neither is "2 / 3" or "14:30". Told apart
    are numbers with a word between them, or with nothing but white space,
    the marks of a list or a sentence, brackets or a dash ("5, 6", "5-7").

    Each claim is a text in which every number is fenced, to be looked for in
    ``offered_numbers`` of the passage.
    """
    plain = _plain_for_numbers(statement, keep_lines=False)
    numbers = _numbers(plain)
    claims = _runs(plain, numbers)
    claims.extend(_OPEN + _NUMBER_WORDS[word] + _CLOSE for word in _WORD.findall(plain)
                  if word in _NUMBER_WORDS)
    return claims


def offered_numbers(passage: str) -> str:
    """The numbers a passage holds, as one text in which every number is fenced.

    The passage is read like a statement, with these differences. A line
    break or a tab between "2" and "500" can be the space inside 2 500 or the
    end of one number and the start of another, so such a pair is offered
    only as it stands, break included, and matches no statement. A raised
    digit after a number can be a footnote mark, so "271a\u00b9" also offers
    "271a" and the mark. "266 a" also offers "266". "acht" after "ausser" or
    beside "geben", "nehmen" or "lassen" is the noun and offers no 8.
    """
    plain = _plain_for_numbers(passage, keep_lines=True)
    numbers = _numbers(plain)
    offered = _runs(plain, numbers)
    offered.extend(_OPEN + other + _CLOSE for number in numbers for other in number[3])
    words = _WORD.findall(plain)
    for at, word in enumerate(words):
        if word in _NUMBER_WORDS and not (word == "acht" and _is_noun_acht(words, at)):
            offered.append(_OPEN + _NUMBER_WORDS[word] + _CLOSE)
    return "\n".join(offered)


def _is_noun_acht(words: List[str], at: int) -> bool:
    before = words[at - 1] if at else ""
    after = [word for word in words[at + 1:at + 3] if word != "zu"][:1]
    return before in _NOUN_ACHT_BEFORE or (bool(after) and after[0] in _NOUN_ACHT_VERBS)


def _plain_for_numbers(text: str, *, keep_lines: bool) -> str:
    seen = "".join(char for char in unicodedata.normalize("NFC", text) if _is_seen(char))
    if keep_lines:
        seen = "\n".join(" ".join(line.split()) for line in seen.replace("\t", "\n").splitlines())
    else:
        seen = " ".join(seen.split())
    return seen.translate(_NUMBER_PLAIN).lower()


def _is_number_sign(char: str) -> bool:
    """A raised digit, a fraction, a circled number: a number, but no digit."""
    return char.isnumeric() and not char.isdecimal()


def _numbers(plain: str) -> List[Tuple[int, int, str, List[str]]]:
    """Every number of ``plain``: where it starts and ends, how it is
    written, and what else a passage offers with it."""
    numbers: List[Tuple[int, int, str, List[str]]] = []
    at, size = 0, len(plain)
    while at < size:
        match = _DIGITS.match(plain, at) if plain[at].isdecimal() else None
        if match is None and not _is_number_sign(plain[at]):
            at += 1
            continue
        start, others = at, []
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
            while end < size and (plain[end].isalpha() or _is_mark(plain[end])):
                end += 1
            written += plain[match.end():end]
            tail = end
            while end < size and (plain[end].isalpha() or _is_mark(plain[end])
                                  or _is_number_sign(plain[end])):
                end += 1
            if end > tail:  # raised digits: perhaps a footnote mark
                others.append(written)
                others.extend("".join(char if _is_number_sign(char) else " "
                                      for char in plain[tail:end]).split())
                written += plain[tail:end]
            elif end == match.end() and _lone_letter_follows(plain, end):
                others.append(written)
                written += plain[end:end + 2]
                end += 2
        numbers.append((start, end, written, others))
        at = end
    return numbers


def _lone_letter_follows(plain: str, at: int) -> bool:
    """Whether ``plain`` goes on at ``at`` with a space or a hyphen and one
    letter that is a word of its own ("266 a", "266-a")."""
    if plain[at:at + 1] not in (" ", "-") or not plain[at + 1:at + 2].isalpha():
        return False
    after = plain[at + 2:at + 3]
    return not (after.isalnum() or after in ("'", "-") or (after != "" and _is_mark(after)))


def _runs(plain: str, numbers: List[Tuple[int, int, str, List[str]]]) -> List[str]:
    """The numbers as fenced texts; numbers with only marks between them in one text."""
    runs: List[str] = []
    run, run_end = "", 0
    for start, end, written, _ in numbers:
        between = plain[run_end:start]
        if run and not any(char.isalnum() for char in between) and (
                not between or any(char not in _BETWEEN_NUMBERS for char in between)):
            run += between + _OPEN + written + _CLOSE
        else:
            if run:
                runs.append(run)
            run = _OPEN + written + _CLOSE
        run_end = end
    if run:
        runs.append(run)
    return runs


# ---------------------------------------------------------------------------
# Check 5: the length
# ---------------------------------------------------------------------------


def check_length(statement: Statement) -> bool:
    """Whether the statement is one statement and not an essay."""
    return len(statement.text) <= MAX_STATEMENT_CHARS
