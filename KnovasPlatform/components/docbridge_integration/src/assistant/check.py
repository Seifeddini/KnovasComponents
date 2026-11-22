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
from bisect import bisect_left
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from functools import lru_cache
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
    "habe ich nichts gefunden",
    "habe ich keine gefunden",
    "ich habe nichts gefunden",
    "ich habe keine gefunden",
    "kann ich nichts finden",
    "kann ich keine finden",
    "ich kann nichts finden",
    "ich kann keine finden",
    "konnte ich nichts finden",
    "konnte ich keine finden",
    "ich konnte nichts finden",
    "ich konnte keine finden",
    "ich fand nichts",
    "ich fand keine",
    "ist nichts bekannt",
    "steht nirgends",
    "gibt es keine Angaben",
    "geht nicht hervor",
    "ergibt sich nichts",
    "sagen nichts",
    "ist nicht verfuegbar",
    "nothing in the documents",
    "nothing in your",
    "I find nothing",
    "I found nothing",
    "I found no",
    "I cannot find",
    "I can't find",
    "I could not find",
    "I couldn't find",
    "I did not find",
    "I was unable to find",
)
_NO_ANSWER_GAP = 8
#: A statement that speaks of the person's sources, or of the assistant
#: itself, and says that something is not there, is not a statement either
#: ("Ihre Quellen enthalten dazu keine Angaben", "Dazu liegen mir keine
#: Informationen vor", "Your sources do not contain this"): a quote cannot
#: prove that something is missing.
_SOURCE_WORDS = frozenset({"quellen", "quelle", "passagen", "sources", "passages", "fonti", "fonte"})
_OWN_WORDS = frozenset({"unterlagen", "dokumente", "dokumenten", "akten", "documents", "documentation",
                        "materials", "files"})
_YOURS = frozenset({"ihre", "ihren", "ihrer", "your", "vos", "votre", "vorliegenden", "bereitgestellten"})
_QUESTION_WORDS = frozenset({"frage", "question", "domanda"})
_THIS = frozenset({"diese", "dieser", "this", "cette", "questa"})
_ASSISTANT_WORDS = frozenset({"ich", "mir", "mich", "i", "me", "je", "moi"})
_NEGATIONS = frozenset({"nichts", "nicht", "kein", "keine", "keinen", "keiner", "keinem", "keines", "nothing",
                        "no", "not", "none", "cannot", "cant", "couldnt", "didnt", "dont", "doesnt", "unable",
                        "rien", "aucun", "aucune", "pas", "nulla", "non", "nessun", "nessuna", "nix"})
#: Statements that are a no-answer as a whole ("Nichts gefunden.").
_NO_ANSWER_WHOLE = frozenset({"nichtsgefunden", "keineangaben", "keineinformationen", "keineantwort",
                              "keinetreffer", "nothingfound", "noanswer", "noinformation"})

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
    passage's own characters.
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


#: Qualifiers in a Latin letter's name that do not change how it reads.
_LETTER_QUALIFIERS = ("SMALL CAPITAL ", "CAPITAL ", "SMALL ", "OPEN ", "SCRIPT ", "DOTLESS ", "LONG ")


@lru_cache(maxsize=1024)
def _plain_letter(char: str) -> str:
    """A Latin letter as the plain letter it looks like ("\u0131" i, "\u026a"
    i, "\u027e" r, "\u00f8" o, "\u0153" oe); empty for any other character."""
    if "a" <= char <= "z":
        return char
    name = unicodedata.name(char, "")
    if not name.startswith("LATIN") or "LETTER " not in name:
        return ""
    rest = name.split("LETTER ", 1)[1]
    for qualifier in _LETTER_QUALIFIERS:
        if rest.startswith(qualifier):
            rest = rest[len(qualifier):]
    letters = rest.split(" ", 1)[0]
    return letters.lower() if 1 <= len(letters) <= 2 and letters.isalpha() and letters.isascii() else ""


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
    wrapper, with a few letters changed or left out, in one of the
    ``NO_ANSWER_WORDINGS``, or in other words that say something is missing
    from the person's sources or that the assistant found nothing."""
    letters = _letters(statement[:MAX_STATEMENT_CHARS])
    if _NO_ANSWER_KEY in letters:
        return True
    words = [_letters(word) for word in statement[:MAX_STATEMENT_CHARS].split()]
    if letters in _NO_ANSWER_WHOLE or any(_holds_in_order(words, wording) for wording in _NO_ANSWER_WORDING_KEYS):
        return True
    if _speaks_of_what_is_missing(words):
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
    """The plain letters a to z of ``text``, lower case, a Latin letter that
    looks like one as that one ("\u026a" i): accents, letters of other
    alphabets, digits, marks and white space are left out."""
    plain = unicodedata.normalize("NFKD", text).lower()
    return "".join(_plain_letter(char) for char in plain)


def _speaks_of_what_is_missing(words: List[str]) -> bool:
    """Whether a statement speaks of the person's sources or of the assistant
    and holds a negation (see ``_SOURCE_WORDS``)."""
    if not _NEGATIONS.intersection(words):
        return False
    if _SOURCE_WORDS.intersection(words) or _ASSISTANT_WORDS.intersection(words):
        return True
    if any(word in _QUESTION_WORDS and words[at - 1] in _THIS for at, word in enumerate(words) if at):
        return True
    return any(word in _OWN_WORDS and _YOURS.intersection(words[max(0, at - 2):at])
               for at, word in enumerate(words))


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
_DASHES = ("-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
           "\ufe63\uff0d\u2796\u02d7\u207b\u2e3a\u2e3b\ufe58\u2043\u208b\u2238\u2e1a")
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
    is then that piece of the passage, in the passage's own characters. None
    when the quote does not stand there."""
    found = find_quote(statement.quote, passages[statement.source])
    return None if found is None else replace(statement, quote=found)


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
    text, origin, words = _folded_passage(passage)
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


@lru_cache(maxsize=64)
def _folded_passage(passage: str) -> Tuple[str, List[int], str]:
    """A passage as quotes are looked for in it (``_fold``, ``_as_words``),
    kept: an answer looks for several quotes in one passage. Not changed
    afterwards."""
    text, origin = _fold(passage)
    return text, origin, _as_words(text)


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
# "w" a word ("dreimonatig", "ein" with the word after it), "n" a word of the
# quote with no number before it, "D" a month or a date, "R" a Roman numeral
# or the letter of an enumeration, "e" a scale (million).

#: The em dash and its longer kin are never a minus sign: they set words apart
#: ("Honorarvereinbarung \u2014 2019-031"), and German, French and Italian
#: write a minus as "-", "\u2212" or "\u2013". As numbers are read they are
#: one mark, ``_SEPARATOR``, which tells two numbers apart and signs none
#: (PMVP-20 context, the demo tenant's question 4: "\u2014 2019-031" read as
#: -2019 took the year from every statement that named the matter).
_SEPARATOR_DASHES = "\u2014\u2015\u2e3a\u2e3b"
_SEPARATOR = "\u2014"
#: Apostrophes and dashes of any kind as their plain form (the separating ones
#: as ``_SEPARATOR``), and the signs that look like per cent or per mille as
#: those. Other marks stay as written: a low quotation mark between digits
#: looks like a comma.
_NUMBER_PLAIN = str.maketrans({
    **{mark: "'" for mark in _APOSTROPHES},
    **{dash: "-" for dash in _DASHES if dash not in _SEPARATOR_DASHES},
    **{dash: _SEPARATOR for dash in _SEPARATOR_DASHES},
    "\uff05": "%", "\ufe6a": "%", "\u066a": "%", "\u0609": "\u2030", "\u060a": "\u2031",
})
#: The digits of one number: thousands set off by an apostrophe, by a space
#: or a line break, or by both ("1'500", "1 500"), then what a stop or a
#: comma ties on ("1.500", "3,5", "31.12.2025").
_DIGITS = re.compile("(?:\\d{1,3}(?:(?:'[ \\n]?|[ \\n]'?)\\d{3})+(?!\\d)|\\d+)(?:[.,]\\d+)*")
#: Dates written with dashes or with spaces after the stops: one number each.
_ISO_DATE = re.compile("\\d{4}-\\d{2}-\\d{2}(?!\\d)")
_SPACED_DATE = re.compile("\\d{1,2}\\. \\d{1,2}\\. \\d{4}(?!\\d)")
#: A sign right before the digits belongs to the number ("-500").
_SIGNS = "-+\u00b1"
#: Per cent, per mille and per ten thousand belong to the number before them.
_PER = "%\u2030\u2031"
#: What may stand between two numbers that are told apart: white space, the
#: marks of a list or a sentence, brackets, and a dash ("5-7" is 5 and 7).
#: Anything else between two numbers makes one of them ("2/3", "14:30").
_BETWEEN_NUMBERS = " \n,;.()[]{}\"!?-" + _SEPARATOR
#: Characters that are written from right to left.
_RIGHT_TO_LEFT = ("R", "AL", "AN")
#: White space that ends a line of a passage; a tab too, it can stand where
#: a line ended.
_LINE_ENDS = frozenset("\n\t\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")
#: What a token that is not written with digits is tagged with.
_TAG = chr(0)

#: Letters and signs that look like digits ("\u01bc" is a 5, "\u01bb" a 2,
#: "\u0292" and "\ua723" a 3, "\ua72d" a 4, "\u0185" a 6, "\u204a" a 7, "\u0223"
#: an 8, "\ua76f" a 9), and the bars that look like a 1. Such a character,
#: like every character with a numeric value that is not one of the digits 0
#: to 9, may stand in a statement only when the same character stands in its
#: quote.
_DIGIT_LOOKALIKES = frozenset(
    "\u01bb\u01bc\u01bd\u01a7\u01a8\u0184\u0185\u01b7\u01b8\u01b9\u01ba\u01ee\u01ef\u0292\u0293"
    "\u1d23\u021c\u021d\u025c\u025d\u025e\ua7ab\ua72a\ua72b\ua72c\ua72d\ua72e\ua72f\ua76a\ua76b"
    "\ua76e\ua76f\ua75c\ua75d\u0222\u0223\u1d15\u0196\u0269\u1d7c\u01c0\u01c1\ua7fe"
    "\ua722\ua723\ua75a\ua75b\u1d08\ua762\ua763\u2125\u204a"
    "|\u00a6\u2223\u2502\u2503\u2758\u2759\u275a\ufe31\uff5c"
    # signs that show digits but have no numeric value: the telegraph signs for the hours and the days, the
    # Kangxi radicals one, two and ten, the turned two and three, the keycap ten
    + "".join(chr(code) for code in range(0x3358, 0x3371)) + "".join(chr(code) for code in range(0x33E0, 0x33FF))
    + "\u2f00\u2f06\u2f17\u218a\u218b\U0001f51f"
)
#: Letters that read as digits next to digits ("l5" is 15, "O,5" is 0,5):
#: part of the number they stand before.
_DIGIT_LETTERS = "lio"
#: A lone letter after a number is mostly an article's letter ("266 a"); not
#: these, which are mostly units ("10 m", "5 l", "2 t", "30 s").
_UNIT_LETTERS = "mlts"
#: Latin words after a number and a space that are part of it ("Art. 12 bis", "Abs. 2 ter"); "bis"
#: only when no number follows it ("5 bis 7" is a range).
_LATIN_AFTER = re.compile("[ -](bis|ter|quater|quinquies|sexies|septies|octies|novies|decies)(?![^\\W\\d_])")
_NUMBER_NEXT = re.compile(" +(?:[0-9]|[^\\W\\d_]+)")
#: "%-Punkte", "Prozent-Punkte": percentage points, a unit of their own.
_POINTS = re.compile("[- ]?(?:punkt[^\\W\\d_]*|pkte?|pte?|points?)(?![^\\W\\d_])")
#: "v. H." (vom Hundert) after a number: per cent, not an article letter.
_PER_HUNDRED = re.compile("v\\. ?h(?![a-z])")

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
#: English words for an order or a count that are no number word: each its own token.
_ENGLISH_WORDS = frozenset({"first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
                            "ninth", "tenth", "once", "twice", "thrice", "double", "triple", "half"})
#: "ein" and its forms: the article or the number 1 (the number only before a
#: word of ``_ONE_UNITS``). A statement's "ein X" is supported by "ein X" or
#: "1 X" in the passage; a passage's "ein" is never the digit 1.
_ONE = frozenset({"ein", "eine", "einen", "einem", "einer", "eines", "eins", "one"})
#: The English article: read like "ein", but only in an English text ("an" is also German) and only with
#: the word it counts ("u.a.", "a.a.O." are no article).
_ARTICLES = frozenset({"a", "an"})
#: Words that tell an English text from a German one.
_ENGLISH_MARKS = frozenset({"the", "is", "are", "was", "of", "and", "to", "for", "with", "by", "be", "must",
                            "shall", "from", "this", "that", "has", "have", "will", "can", "after", "within"})
_GERMAN_MARKS = frozenset({"der", "die", "das", "und", "ist", "sind", "den", "dem", "des", "zu", "mit", "von",
                           "fuer", "nach", "bei", "auf", "im", "wird", "werden", "nicht", "eine", "einen"})
#: The longest German number word read ("neunhundertneunundneunzigtausend...").
_LONGEST_NUMBER_WORD = 60
#: The most letters of a number word spelled out one by one ("z-w-e-i"), and the most words of an English
#: number ("five hundred and ten"): longer runs are no number, and looking further would be slow.
_LONGEST_SPELLED = 30
_LONGEST_ENGLISH = 12

#: What a number word is a part of when the word goes on. A span of time, a
#: room or a multiple it counts: such a word is also its number
#: ("dreimonatig", "zweij\u00e4hrig", "zehnfach" are 3, 2 and 10) ...
_TIME_UNITS = ("jaehr", "jahr", "monat", "woech", "woch", "taeg", "tag", "stuend", "stund", "minuet",
               "minut", "sekuend", "sekund", "quartal", "semester", "zimmer", "fach")
#: ... other things it counts, where the word is only itself ("zweiseitig"
#: is also "bilateral", "einmal" also "even") ...
_COUNTED = ("mal", "seitig", "teilig", "stellig", "stufig", "koepfig", "geschoss", "stoeck", "sprachig",
            "familien", "personen", "spurig", "zeilig", "kind")
#: ... "ein" begins a number only before one of these (not "einfach",
#: "Eintrag", "Einsprache") ...
_ONE_COUNTED = tuple(unit for unit in _TIME_UNITS + _COUNTED if unit != "fach") + ("prozent", "promill")
#: ... and fractions, which are no whole number ("Zweidrittelmehrheit",
#: "zweieinhalb", "Dreiviertel").
_FRACTIONS = ("einhalb", "halb", "drittel", "viertel", "fuenftel", "sechstel", "siebtel", "achtel",
              "neuntel", "zehntel", "zwoelftel", "zwanzigstel", "hundertstel", "tausendstel")
#: The fractions that count after a number word inside a word ("halb" stands
#: inside "innerhalb" and "deshalb").
_FRACTIONS_INSIDE = tuple(fraction for fraction in _FRACTIONS if fraction != "halb")
#: A fraction word after a number is part of it ("zwei Drittel" is not 2).
_FRACTION_WORDS = frozenset(_FRACTIONS) | {"haelft", "haelfte"}
#: Beginnings of number words that are no number word on their own: "dritte",
#: "Drittel", "siebte", "halb", "H\u00e4lfte", "anderthalb", "doppelt".
_NUMBER_STEMS = ("anderthalb", "doppel", "dritt", "haelft", "halb", "siebt")
#: Words that begin with a number word and name no number.
_NOT_NUMBERS = ("zweifel", "zweigniederlass", "zweigstell", "zweigbetrieb", "achtung", "achtsam",
                "achtlos", "achtbar", "achtet", "achtest", "elfenbein", "dreistigkeit", "nullit", "nullif")
_NOT_NUMBER_STEMS = frozenset({"zweig", "dreist"})
_ENDINGS = ("en", "er", "es", "em", "e", "n", "s")

#: The most characters between "ein" and the word it counts ("einen (1) Monat").
_LONGEST_GAP_AFTER_ONE = 12
#: Words after "ein" that are a quantity of their own: "ein paar Tage" is no "ein Tag".
_QUANTITIES = frozenset({"paar", "bisschen", "wenig", "weniges", "dutzend", "few", "couple"})
#: "ein" and its forms (and the English "a", "an") are the number 1 only before one of these words, as ``_stem``
#: leaves them: a unit of time, money, share or count, or a quantity ("ein Monat", "einen Franken", "eine
#: H\u00e4lfte", "ein Exemplar", "ein paar"). Before any other word it is the article and is not checked: "ein
#: Ansatz", "eine Kaution" (PMVP-20 context, the coordinator's decision of 2026-11-22, after the demo tenant's
#: question 4 lost every statement to "ein Ansatz"). "eins" and "one" are always the number.
_ONE_UNITS = frozenset({
    "tag", "woch", "monat", "jahr", "jahrzehnt", "jahrhundert", "dekad", "stund", "minut", "sekund", "quartal",
    "semester",
    "prozent", "promill", "frank", "chf", "fr", "rapp", "euro", "eur", "dollar", "usd",
    "mal", "drittel", "viertel", "achtel", "haelft", "halb", "seit", "exemplar",
    "day", "week", "month", "year", "hour", "percent", "franc", "tim", "third", "quart", "half", "pag", "copy",
    "copi",
}) | _QUANTITIES
_ALWAYS_ONE = frozenset({"eins", "one"})
#: Markdown emphasis in a statement: left out before its numbers are read ("zw**ei**").
_MARKDOWN = re.compile("[*_]+")

#: "ausser Acht lassen", "in Acht nehmen", "Acht geben": "acht" after
#: "ausser" or beside one of these verbs is the noun, not the number 8.
_NOUN_ACHT_VERBS = frozenset({
    "geben", "gibt", "gab", "gegeben", "nehmen", "nimmt", "nahm", "genommen",
    "lassen", "laesst", "liess", "gelassen", "bleiben", "bleibt", "blieb", "geblieben",
})
_NOUN_ACHT_BEFORE = _NOUN_ACHT_VERBS | {"ausser"}

_MONTHS = {"januar": 1, "jaenner": 1, "februar": 2, "feber": 2, "maerz": 3, "april": 4, "mai": 5,
           "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11,
           "dezember": 12, "january": 1, "february": 2, "march": 3, "may": 5, "june": 6, "july": 7,
           "october": 10, "december": 12}
_MONTHS_SHORT = {"jan": 1, "jaen": 1, "feb": 2, "febr": 2, "maer": 3, "mrz": 3, "mar": 3, "apr": 4,
                 "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "okt": 10, "oct": 10, "nov": 11,
                 "dez": 12, "dec": 12}
#: Months that are also a name or a word ("August Meier", "may", "march").
_NAME_MONTHS = frozenset({"august", "march", "may"})

#: Scales after a number: part of it ("2 Mio." is not "2"). "billion" is a
#: thousand million in English and a million million in German: its own.
_SCALES = {"tsd": "3", "mio": "6", "million": "6", "millionen": "6", "millions": "6", "mill": "6",
           "mrd": "9", "milliarde": "9", "milliarden": "9", "md": "9", "mia": "9", "bn": "9",
           "billions": "9", "billion": "billion", "billionen": "12", "bio": "12", "billiarde": "15",
           "billiarden": "15"}
#: Scales that are also other words ("Mia", "Bio"): only right after a number.
_SCALES_AFTER_NUMBER = frozenset({"mill", "md", "mia", "bn", "bio"})
#: "hundert" and "tausend" after digits scale them too ("5 Tausend" is not 5).
_SCALE_WORDS = {"hundert": "2", "tausend": "3", "hundred": "2", "thousand": "3"}

#: Words after which a Roman numeral or a single letter numbers something:
#: "Ziffer IV", "lit. c", "Anhang B".
_ENUMERATION_WORDS = frozenset({
    "ziffer", "ziff", "art", "artikel", "kapitel", "kap", "anhang", "anh", "abschnitt", "abschn",
    "lit", "litera", "bst", "buchst", "buchstabe", "teil", "titel", "abs", "absatz", "nr", "nummer",
    "band", "bd", "buch", "anlage", "beilage", "stufe", "klasse", "kategorie", "zone", "phase",
})
_ROMAN = re.compile("m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})")
_KEYWORD_GAP = re.compile("\\.?\\s+|\\.")
#: What joins the letters of one enumeration: "lit. a, b und c", "lit. a-c".
_LISTING_GAP = re.compile("\\s*(?:[,;/-]\\s*)?(?:(?:und|oder|bis|sowie)\\s+)?")
_DAY = re.compile("[0-9]{1,2}")
_YEAR = re.compile("[0-9]{4}")
_DAY_GAP = re.compile("\\.?[ \\n]?")
_YEAR_GAP = re.compile("\\.?[ \\n]")
#: Spellings of number words and months without the umlaut.
_SPELLINGS = (("funf", "fuenf"), ("zwolf", "zwoelf"), ("marz", "maerz"), ("janner", "jaenner"))


def check_numbers(statement: Statement, passages: Mapping[int, str]) -> bool:
    """Whether every number of the statement stands in its source passage.

    Each claim of ``claimed_numbers`` must meet a token that
    ``offered_numbers`` reads in the passage, and each character of the
    statement that has a numeric value but is no digit 0 to 9, or shows a
    digit (``_DIGIT_LOOKALIKES``), must stand in the passage itself. The quote
    must stand in the passage too, but that is check 3: a number may stand in
    the passage outside the quote ("Art. 266c OR" in the passage's heading).
    """
    claims = claimed_numbers(statement.text)
    odd = _odd_characters(statement.text)
    if not claims and not odd:
        return True
    if any(unicodedata.bidirectional(char) in _RIGHT_TO_LEFT for char in statement.text):
        return False  # such a character can show two numbers in another order than they are read
    passage = passages[statement.source]
    if any(char not in passage for char in odd):
        return False
    offered = offered_numbers(passage)
    return all(claim & offered for claim in claims)


def claimed_numbers(statement: str) -> List[FrozenSet[str]]:
    """The numbers a statement claims: for each, the tokens that support it.

    A number is, as written:

    - a run of digits with what belongs to it: thousands set off by an
      apostrophe or a space ("1'500", "1 500"); what a stop or a comma
      between digits ties on ("1.500", "3,5", "31.12.2025"); a sign right
      before it ("-500"; "- 500" as written), also the word "minus" ("minus
      500" is "-500"); letters, accents and raised digits right after it
      ("266a", "12bis", "10\u00b3"); one letter standing alone after it
      ("266 a"; not a unit: "10 m" is 10); a Latin "bis", "ter", "quater" after
      it ("12 bis" is "12bis"; not "5 bis 7"); a letter that reads as a digit
      right before it ("l5", "O,5"); a per cent or per mille sign or word after
      it ("5%", "5 Prozent", "5-prozentig", "5 vom Hundert", "5\u2030";
      "5 Prozentpunkte", "5 %-Punkte" are their own); a scale after it ("2 Mio.", "500 Mia.",
      "5 Tausend"); a fraction after it ("zwei Drittel", "1 1/2", "1 \u00bd").
      Raised digits, fractions and other number signs on their own are
      numbers too. Two numbers with only marks between them are one ("2/3",
      "2 / 3", "14:30", "2024-01-15"); numbers with a word, white space, the
      marks of a list or a sentence, brackets or a dash between them are told
      apart ("5, 6", "5-7").
    - a German number word, 0 to 999,999, also in several words ("drei",
      "dreizehn", "zwei hundert", "drei und zwanzig", "zwei-hundert",
      "zweitausendvierundzwanzig"), or an English one ("six", "twenty-five",
      "five hundred"). It is the same as its digits: "drei" is "3". Words are
      read through accents, umlaut spellings and letters that look like plain
      ones ("dr\u00e9i", "funf", "dre\u0131").
    - "ein" and its forms ("eine", "einen", "eins", "one"), and the digit 1:
      the article or the number 1, read with the word they count ("einen
      Monat", "1 Monat"): "ein X" and "1 X" support each other, nothing else
      supports them but the digit 1 alone for a "1".
    - a word that begins with a number word and goes on ("dreimonatig",
      "zweite", "Zweidrittelmehrheit", "Drittel", "doppelt"), or holds one
      before a unit or a fraction ("Mindestdreimonatsfrist"): that word,
      without its ending; when it counts a span of time, rooms or a multiple,
      its number too; when it counts per cent ("f\u00fcnfprozentig"), that.
    - a month ("M\u00e4rz", "Maerz", "Mrz.", "Febr.", "March"), and with a
      day before it or a year after it a date ("1. M\u00e4rz 2024" is one
      number, not the same as "01.03.2024"). Months that are also names or
      words ("Jan", "August", "may") claim the month or the word.
    - a Roman numeral or a single letter after "Ziffer", "Art.", "lit.",
      "Teil", "Stufe", "Klasse" and the like, and the letters listed with it;
      anywhere a Roman numeral of two letters or more, or any single letter,
      in capitals ("IV", "Kategorie C"); a word of the letters l, I and O that
      reads as a number ("lO").
    - "acht" is always 8, unless it is the noun ("ausser Acht"): then the noun
      or 8. "null und nichtig" is a phrase of its own, not a 0. English words
      for an order or a count ("third", "twice") are each their own.

    Markdown emphasis marks (``*``, ``_``) are left out before a statement
    is read: "zw**ei**" is "zwei".

    Numbers are compared as written, never by value: "1500.00" is not
    "1500", "266" is not "266a", "1 500" is not "1500", "31. Dezember 2025"
    is not "31.12.2025". One and the same are only "1'500" and "1500", upper
    and lower case, a number word and its digits, a word that counts a span of
    time and its number, and the spellings of a month.
    """
    items = _read(_MARKDOWN.sub("", statement), passage=False)
    return [_tokens(item, passage=False) for item in items]


@lru_cache(maxsize=64)
def offered_numbers(passage: str) -> FrozenSet[str]:
    """The tokens a passage offers, read as ``claimed_numbers`` reads a
    statement; kept, as an answer checks several statements against one
    passage.

    Whole tokens only: "2/3" offers neither 2 nor 3, "14:30" no 30, "10\u2076"
    no 10, "266 a" no 266, "1. M\u00e4rz 2024" no 2024. Where the passage can
    be read two ways it offers neither reading: two numbers, a sign and a
    number, a number and its letter with a line break or a tab between them
    ("Absatz 2" + line break + "500"); words that a hyphen and a break join or
    part ("drei-" + line break + "zehn"); "acht" after "ausser" or beside
    "geben", "nehmen", "lassen", "bleiben" (the noun, no 8); "ein" (never the
    digit 1); "Jan", "August" and "may" outside a date (the word, no month).
    """
    offered: Set[str] = set()
    for item in _read(passage, passage=True):
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
    tail = item.per + (_TAG + "e" + item.scale if item.scale else "")
    if item.kind == "num":
        if item.value:  # "1 Monat" is "ein Monat"
            return frozenset([item.text + tail, "w" + _TAG + "ein:" + item.value])
        return frozenset([item.text + tail])
    if item.kind == "ein" and item.value:  # "minus ein ...": only the same words support it
        return frozenset(["w" + _TAG + "-ein:" + item.text + tail])
    if item.kind == "ein":
        if tail:
            own = "w" + _TAG + "ein" + tail
            return frozenset([own] if passage else [own, "1" + tail])
        if item.word == "eins" or not item.text:  # a bare "ein" is supported only by a bare one
            own = "w" + _TAG + ("eins" if item.word == "eins" else "ein")
            return frozenset([own, "w" + _TAG + "ein"] if passage else [own])
        return frozenset(["w" + _TAG + "ein:" + item.text])
    if item.kind == "noun":
        own = "w" + _TAG + item.word
        return frozenset([own] if passage else [own, item.text])
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
    own = "w" + _TAG + item.text  # "compound", "phrase", "word"
    return frozenset([own, item.value] if item.value else [own])


def _read(text: str, *, passage: bool) -> List[_Item]:
    """The numbers of ``text``, in their order."""
    cased, _, _ = _plain_for_numbers(text, keep_lines=passage)
    plain = "".join(char.lower() if len(char.lower()) == 1 else char for char in cased)
    items = _join_runs(plain, _digit_numbers(plain, passage=passage))
    covered = bytearray(len(plain))
    for item in items:
        covered[item.start:item.end] = b"\x01" * (item.end - item.start)
    spans = [span for span in _word_spans(plain) if not covered[span[0]]]
    words = [_word_key(plain[start:end]) for start, end in spans]
    items.extend(_word_numbers(plain, cased, spans, words, passage=passage))
    items.sort(key=lambda item: item.start)
    items = _join_dates(plain, _apply_modifiers(plain, items))
    begins = [start for start, _ in spans]
    for item in items:
        if item.kind == "num" and item.text == "1" and not item.word and not item.per and not item.scale:
            following = bisect_left(begins, item.end)
            if following < len(spans) and plain[item.end:spans[following][0]].isspace():
                item.value = _noun_from(plain, cased, spans, words, following)
    return items


def _plain_for_numbers(text: str, *, keep_lines: bool) -> Tuple[str, List[int], List[int]]:
    """``text`` as numbers are read, with for each of its characters where it
    starts and ends in ``text``.

    What is not seen is left out (also inside a number: "1\u00ad500" is
    1500); accents are composed; apostrophes, dashes and per cent signs of
    any kind are plain. White space is one space, or -- with ``keep_lines``
    -- one or two line breaks where it holds line breaks or tabs; after a
    soft hyphen it is nothing, the word goes on ("drei\u00ad" + line break +
    "zehn").
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


def _digit_numbers(plain: str, *, passage: bool) -> List[_Item]:
    """The numbers of ``plain`` that are written with digits or number signs."""
    found: List[_Item] = []
    at, size = 0, len(plain)
    while at < size:
        date = (_ISO_DATE.match(plain, at) or _SPACED_DATE.match(plain, at)) if plain[at].isdecimal() else None
        if date is not None and not plain[at - 1:at].isalnum():
            found.append(_Item(at, date.end(), "num", date.group().replace(" ", "")))
            at = date.end()
            continue
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
            start, written = _what_stands_before(plain, at, written, passage=passage)
            while end < size and (plain[end].isalpha() or _is_mark(plain[end])
                                  or _is_number_sign(plain[end])):
                end += 1
            written += plain[match.end():end]
            latin = _LATIN_AFTER.match(plain, end) if end == match.end() else None
            if latin is not None and latin.group(1) == "bis" and (
                    not _after_enumeration_word(plain, start) or _number_follows(plain, latin.end())):
                latin = None  # "von 3 bis zu 5", "5 bis 7": the German "bis"
            if latin is not None:
                written += latin.group(1)
                end = latin.end()
            elif end == match.end() and _lone_letter_follows(plain, end):
                written += plain[end:end + 2]
                end += 2
        if plain[end:end + 1] and plain[end] in _PER:
            written += plain[end]
            end += 1
        elif plain[end:end + 1] in (" ", "\n") and plain[end + 1:end + 2] and plain[end + 1] in _PER:
            written += plain[end + 1]
            end += 2
        points = _POINTS.match(plain, end) if written[-1:] == "%" else None
        if points is not None:
            written += "p"  # "5 %-Punkte": percentage points
            end = points.end()
        found.append(_Item(start, end, "num", written))
        at = end
    return found


def _what_stands_before(plain: str, at: int, written: str, *, passage: bool) -> Tuple[int, str]:
    """Where a number written with digits starts, and how: with its sign
    ("-500"; "- 500" when no number stands before the dash), with a code of
    up to three letters right before it ("Q1", "A4", "m2", "l5"), with a
    letter that reads as a digit before a stop or a comma ("o,5"), or, in a
    passage, with a sign a line break parts from it ("-" + line break + "5":
    read neither way)."""
    before = plain[at - 1:at]
    if before and before in _SIGNS and not plain[at - 2:at - 1].isalnum():
        return at - 1, before + written
    if before == " " and plain[at - 2:at - 1] and plain[at - 2] in _SIGNS and not plain[at - 3:at - 2].isalnum():
        back = at - 3
        while back >= 0 and plain[back] in " \n":
            back -= 1
        previous = plain[back] if back >= 0 else ""
        if not (previous.isnumeric() or (previous and previous in _PER)):
            return at - 2, plain[at - 2] + " " + written
    if passage and before == "\n" and plain[at - 2:at - 1] and plain[at - 2] in _SIGNS:
        return at - 2, plain[at - 2] + "\n" + written
    if before.isalpha():
        first = at - 1
        while first > 0 and plain[first - 1].isalpha():
            first -= 1
        if at - first <= 3 and not plain[first - 1:first].isalnum():
            return first, plain[first:at] + written  # "Q1", "H2", "A4", "m2", "l5"
    if (before in (",", ".") and plain[at - 2:at - 1] and plain[at - 2] in _DIGIT_LETTERS
            and not plain[at - 3:at - 2].isalpha()):
        return at - 2, plain[at - 2:at] + written
    return at, written


def _after_enumeration_word(plain: str, at: int) -> bool:
    """Whether the number at ``at`` follows "Art.", "Abs.", "Ziff.", "\u00a7" or the
    like, also over a line break."""
    back = at - 1
    while back >= 0 and plain[back] in " .\n":
        back -= 1
    if back >= 0 and plain[back] == "\u00a7":
        return True
    first = back
    while first >= 0 and plain[first].isalpha():
        first -= 1
    return back > first and plain[first + 1:back + 1] in _ENUMERATION_WORDS


def _number_follows(plain: str, at: int) -> bool:
    """Whether a number follows at ``at`` after a space ("bis 7", "bis drei")."""
    match = _NUMBER_NEXT.match(plain, at)
    if match is None:
        return False
    word = match.group().strip()
    return word[:1].isdecimal() or _number_word(_word_key(word)) is not None or word in _ENGLISH


def _lone_letter_follows(plain: str, at: int) -> bool:
    """Whether ``plain`` goes on at ``at`` with a space, a hyphen or a line
    break and one letter that is a word of its own ("266 a", "266-a"); not a
    unit letter ("10 m"). After a line break the letter stays with the number
    as written, and so matches no statement."""
    if plain[at:at + 1] not in (" ", "-", "\n") or not plain[at + 1:at + 2].isalpha():
        return False
    if plain[at + 1] in _UNIT_LETTERS or _PER_HUNDRED.match(plain, at + 1):  # "10 m"; "5 v.H." is per cent
        return False
    after = plain[at + 2:at + 3]
    return not (after.isalnum() or after in ("'", "-") or (after != "" and _is_mark(after)))


def _join_runs(plain: str, items: List[_Item]) -> List[_Item]:
    """Numbers with only marks between them become one ("2/3", "14:30"), and
    so does a whole number with a fraction after it ("1 1/2", "1 \u00bd")."""
    runs: List[_Item] = []
    for item in items:
        if runs:
            last = runs[-1]
            between = plain[last.end:item.start]
            if not (any(char.isalnum() for char in between)
                    or (between and all(char in _BETWEEN_NUMBERS for char in between))):
                last.text += between + item.text
                last.end = item.end
                continue
        runs.append(item)
    mixed: List[_Item] = []
    for item in runs:
        if mixed and plain[mixed[-1].end:item.start] == " " and mixed[-1].text.isdecimal() and _is_fraction(item.text):
            mixed[-1].text += " " + item.text
            mixed[-1].end = item.end
            continue
        mixed.append(item)
    return mixed


def _is_fraction(text: str) -> bool:
    if re.fullmatch("[0-9]+/[0-9]+", text):
        return True
    return len(text) == 1 and _is_number_sign(text) and 0 < unicodedata.numeric(text, 0) < 1


def _word_key(word: str) -> str:
    """A word as number words are looked up: lower case, umlauts as ``ae``,
    ``oe``, ``ue`` and ``ss``, other accents and marks left out, letters that
    look like plain ones made plain, "funf" spelled "fuenf"."""
    folded = (word.lower().replace("\u00e4", "ae").replace("\u00f6", "oe").replace("\u00fc", "ue")
              .replace("\u00df", "ss"))
    key = "".join(_plain_letter(char) for char in unicodedata.normalize("NFKD", folded)
                  if not _is_mark(char))
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


def _per_of(word: str) -> str:
    """The sign a word stands for after a number: per cent, per mille, or
    percentage points (their own); empty for other words."""
    if word.startswith(("prozentpunkt", "prozentpkt")):
        return "%p"
    if word.startswith(("prozent", "perzent")) or word in ("pct", "proz", "percent", "percentage"):
        return "%"
    if word.startswith("promill"):
        return "\u2030"
    return ""


def _word_numbers(plain: str, cased: str, spans: List[Tuple[int, int]], words: List[str], *,
                  passage: bool) -> List[_Item]:
    """The numbers of ``plain`` written with words, and the words that change
    a number ("minus", "Prozent", "Mio.") as items to apply."""
    skipped = _broken_words(plain, spans) if passage else set()
    english = sum(word in _ENGLISH_MARKS for word in words) > sum(word in _GERMAN_MARKS for word in words)
    found: List[_Item] = []
    listing_end, at, spelled_to = -1, 0, -1
    while at < len(spans):
        current = at
        at += 1
        (start, end), word = spans[current], words[current]
        if current in skipped:
            continue
        following = words[current + 1] if current + 1 < len(words) else ""
        spaced = bool(following) and plain[end:spans[current + 1][0]].isspace()
        if word == "null" and words[current + 1:current + 3] == ["und", "nichtig"] and _spaced(
                plain, spans[current:current + 3]):
            found.append(_Item(start, spans[current + 2][1], "phrase", "null und nichtig"))
            at = current + 3
            continue
        if word in ("per", "pro") and following in ("cent", "centum", "mille") and spaced:
            found.append(_Item(start, spans[current + 1][1], "per", "%" if following != "mille" else "\u2030"))
            at = current + 2
            continue
        if (word.startswith(("prozent", "percent")) and following.startswith(("punkt", "pkt", "pt", "point"))
                and plain[end:spans[current + 1][0]] in ("-", " ")):
            found.append(_Item(start, spans[current + 1][1], "per", "%p"))  # "Prozent-Punkte"
            at = current + 2
            continue
        if word == "vom" and following in ("hundert", "tausend") and spaced:
            found.append(_Item(start, spans[current + 1][1], "per", "%" if following == "hundert" else "\u2030"))
            at = current + 2
            continue
        if word == "v" and following == "h" and re.fullmatch("\\. ?", plain[end:spans[current + 1][0]]):
            found.append(_Item(start, spans[current + 1][1], "per", "%"))
            at = current + 2
            continue
        if len(word) == 1 and current > spelled_to:
            spelled, last = _spelled_out(plain, spans, words, current)
            if last > current:  # "z-w-e-i", "d r e i"
                found.append(_Item(start, spans[last][1], "num", spelled))
                at = last + 1
                continue
            spelled_to = _run_end(plain, spans, words, current)
        joined, last, value = _joined_number(plain, spans, words, current)
        if last > current:
            found.append(_Item(start, spans[last][1], "num", str(value), word=joined))
            at = last + 1
            continue
        month = _month(word)
        if month is not None:
            found.append(_Item(start, end, "month", str(month[0]), word=word, sure=month[1]))
            continue
        if word in _ONE:
            noun = _noun_after(plain, cased, spans, words, current)
            if word in _ALWAYS_ONE or not noun or noun in _ONE_UNITS:  # else the article: not checked
                found.append(_Item(start, end, "ein", noun, word=word))
            continue
        if word in _ARTICLES and english and not _enumerates(plain, cased, spans, words, current, listing_end,
                                                             passage=passage, article=True):
            noun = _noun_after(plain, cased, spans, words, current)
            if noun:  # "a month"; "u.a." is no article
                if noun in _ONE_UNITS:  # "a month"; "a tenant" is the article: not checked
                    found.append(_Item(start, end, "ein", noun, word=word))
                continue
        value = _number_word(word)
        if value is None:
            value = _ENGLISH.get(word)
        if value is not None:
            if word == "acht" and _is_noun_acht(words, current) and (passage or cased[start] == "A"):
                found.append(_Item(start, end, "noun", "8", word="acht"))
            elif word in _ONES or word in _TEENS or word in _TENS or _number_word(word) is not None:
                if word not in _SCALE_WORDS and _is_noun(plain, cased, start, end):  # "die Elf", "eine Drei"
                    found.append(_Item(start, end, "noun", str(value), word=word))
                else:
                    found.append(_Item(start, end, "num", str(value), word=word))
            else:
                found.append(_Item(start, end, "num", str(value), word=word))
            continue
        if word == "minus":
            found.append(_Item(start, end, "minus"))
            continue
        if _per_of(word):
            found.append(_Item(start, end, "per", _per_of(word)))
            continue
        if word in _SCALES:
            found.append(_Item(start, end, "scale", _SCALES[word], sure=word not in _SCALES_AFTER_NUMBER))
            continue
        if word in _ENGLISH_WORDS:
            found.append(_Item(start, end, "word", word))
            continue
        if _enumerates(plain, cased, spans, words, current, listing_end, passage=passage):
            found.append(_Item(start, end, "roman", word))
            listing_end = end
            continue
        written = cased[start:end]
        if len(written) >= 2 and "O" in written and all(char in "lIO" for char in written):
            found.append(_Item(start, end, "word", written))  # "lO", "IO": read as 10
            continue
        compound = _compound(word)
        if compound is not None:
            found.append(_Item(start, end, "compound", compound[0], value=compound[1]))
    return found


_SPELLING_GAPS = ("-", " ", ".", ". ", "_", "/")


def _spelled_out(plain: str, spans: List[Tuple[int, int]], words: List[str], at: int) -> Tuple[str, int]:
    """A number word spelled out letter by letter ("z-w-e-i", "d r e i",
    "d.r.e.i"): its value and the index of its last letter; else ``at``."""
    letters, last = words[at], at
    while (last + 1 < len(spans) and last - at < _LONGEST_SPELLED and len(words[last + 1]) == 1
           and plain[spans[last][1]:spans[last + 1][0]] in _SPELLING_GAPS):
        last += 1
        letters += words[last]
    if last == at:
        return "", at
    value = _number_word(letters)
    if value is None:
        value = _ENGLISH.get(letters)
    return ("", at) if value is None else (str(value), last)


_BEFORE_A_WORD = frozenset(" ([{" + chr(10) + chr(34) + chr(39))


def _run_end(plain: str, spans: List[Tuple[int, int]], words: List[str], at: int) -> int:
    """The index of the last single letter of the run ``at`` begins (at most
    ``_LONGEST_SPELLED`` letters further): none of them begins another try."""
    last = at
    while (last + 1 < len(spans) and last - at < _LONGEST_SPELLED and len(words[last + 1]) == 1
           and plain[spans[last][1]:spans[last + 1][0]] in _SPELLING_GAPS):
        last += 1
    return last


def _is_noun(plain: str, cased: str, start: int, end: int) -> bool:
    """Whether a number word is written as a noun: a capital and then small
    letters, not at the start of a sentence ("die Elf", "eine Drei")."""
    written = cased[start:end]
    return written[:1].isupper() and written[1:].islower() and not _starts_sentence(plain, start)


def _starts_sentence(plain: str, start: int) -> bool:
    """Whether a word at ``start`` begins the text or a sentence."""
    back = start - 1
    while back >= 0 and plain[back] in _BEFORE_A_WORD:
        back -= 1
    return back < 0 or plain[back] in ".!?:;"


def _noun_after(plain: str, cased: str, spans: List[Tuple[int, int]], words: List[str], at: int) -> str:
    """The word the "ein" at ``at`` counts (``_noun_from``), also over marks
    and digits ("einen (1) Monat"); empty when the sentence ends first or no
    word follows."""
    if at + 1 >= len(spans):
        return ""
    gap = plain[spans[at][1]:spans[at + 1][0]]
    if len(gap) > _LONGEST_GAP_AFTER_ONE or any(char in ".!?;:" or char.isalpha() for char in gap):
        return ""
    return _noun_from(plain, cased, spans, words, at + 1)


def _noun_from(plain: str, cased: str, spans: List[Tuple[int, int]], words: List[str], index: int) -> str:
    """The word an "ein" or a "1" counts, looked for from the word at
    ``index`` on: the first word in capitals among three ("eine dreimonatige
    Frist": Frist), else the first word. A word that is a number or a
    quantity itself ("halbe", "doppelte", "paar", "Drittel") is the one, so
    "eine halbe Stunde" is not "eine Stunde". Empty when a scale or a per
    cent word comes first: it becomes part of the number."""
    first = ""
    for at in range(index, min(index + 3, len(spans))):
        if at > index and not plain[spans[at - 1][1]:spans[at][0]].isspace():
            break
        word = words[at]
        if at == index:
            if word in _SCALES or _per_of(word):
                return ""
            first = _stem(word)
        if _counts_itself(word) or cased[spans[at][0]].isupper():
            return _stem(word)
    return first


def _counts_itself(word: str) -> bool:
    return (word in _QUANTITIES or word in _ONE or word in _ENGLISH or _number_word(word) is not None
            or _month(word) is not None or _compound(word) is not None)


def _enumerates(plain: str, cased: str, spans: List[Tuple[int, int]], words: List[str], at: int,
                listing_end: int, *, passage: bool, article: bool = False) -> bool:
    """Whether a word is a Roman numeral or the letter of an enumeration. With
    ``article`` (the English "a"): only after "lit.", "Anhang" and the like,
    in a listing, or as a capital inside a sentence ("Kategorie A")."""
    (start, end), word = spans[at], words[at]
    if plain[end:end + 1].isdecimal():
        return False  # "A4": a code, not an enumeration
    letter = len(word) == 1 and "a" <= word <= "z"
    roman = _ROMAN.fullmatch(word) is not None and bool(word)
    after_keyword = (at > 0 and words[at - 1] in _ENUMERATION_WORDS
                     and _KEYWORD_GAP.fullmatch(plain[spans[at - 1][1]:start]) is not None)
    listed = listing_end >= 0 and _LISTING_GAP.fullmatch(plain[listing_end:start]) is not None
    if (letter or roman) and (after_keyword or listed):
        return True
    written = cased[start:end]
    if article:
        return written == "A" and not _starts_sentence(plain, start)
    capitals = written.isupper() or all(unicodedata.name(char, "").startswith("LATIN LETTER SMALL CAPITAL")
                                        for char in written)
    if not capitals or not (letter or roman):
        return False
    if letter and passage and _is_initial(plain, cased, spans, at):
        return False  # "A. Meier": an initial
    return True


def _is_initial(plain: str, cased: str, spans: List[Tuple[int, int]], at: int) -> bool:
    end = spans[at][1]
    if plain[end:end + 1] != "." or at + 1 >= len(spans):
        return False
    following = spans[at + 1][0]
    return plain[end + 1:following].isspace() and cased[following].isupper()


def _spaced(plain: str, spans: List[Tuple[int, int]]) -> bool:
    """Whether only white space stands between these words."""
    return all(plain[left[1]:right[0]].isspace() for left, right in zip(spans, spans[1:]))


def _joined_number(plain: str, spans: List[Tuple[int, int]], words: List[str],
                   at: int) -> Tuple[str, int, Optional[int]]:
    """A number written in several words, as a reader reads it: the words
    joined, the index of its last word and its value. German: "zwei
    hundert", "drei und zwanzig", "ein tausend", and any number word joined
    with a hyphen ("zwei-hundert", "drei-zehn", "ein-und-zwanzig"); English:
    "twenty-five", "five hundred and ten". One word alone gives its index."""
    first = words[at]
    if first in _ENGLISH or first == "one":
        return _joined_english(plain, spans, words, at)
    if not (_number_word(first) is not None or first in ("ein", "eine")
            or (first.endswith("und") and first[:-3] in _ONES)):
        return first, at, None
    joined, last = first, at
    while last + 1 < len(spans):
        gap = plain[spans[last][1]:spans[last + 1][0]]
        hyphen, space = gap == "-", gap.isspace()
        following = words[last + 1]
        base = "ein" if joined == "eine" else joined
        if following == "und" and last + 2 < len(spans):
            gap_after = plain[spans[last + 1][1]:spans[last + 2][0]]
            tens = words[last + 2]
            if (hyphen and gap_after == "-") or (space and gap_after.isspace() and tens in _TENS):
                candidate, step = base + "und" + tens, 2
            else:
                break
        elif hyphen or (space and (following.startswith(("hundert", "tausend"))
                                   or (base.endswith("und") and following in _TENS))):
            candidate, step = base + following, 1
        else:
            break
        if _number_word(candidate) is None:
            break
        joined, last = candidate, last + step
    if last == at:
        return first, at, None
    return joined, last, _number_word(joined)


def _joined_english(plain: str, spans: List[Tuple[int, int]], words: List[str],
                    at: int) -> Tuple[str, int, Optional[int]]:
    total, current, count, last, index = 0, 0, 0, at, at
    while index < len(spans):
        if index > at:
            gap = plain[spans[index - 1][1]:spans[index][0]]
            if not (gap == "-" or gap.isspace()):
                break
        word = words[index]
        if word == "and" and count and index + 1 < len(spans) and (
                words[index + 1] in _ENGLISH or words[index + 1] == "one"):
            index += 1
            continue
        value = 1 if word == "one" else _ENGLISH.get(word)
        if value is None or count >= _LONGEST_ENGLISH:
            break
        if value == 100:
            current = (current or 1) * 100
        elif value == 1000:
            total, current = total + (current or 1) * 1000, 0
        else:
            current += value
        count, last, index = count + 1, index, index + 1
    if count < 2:
        return words[at], at, None
    return " ".join(words[at:last + 1]), last, total + current


def _broken_words(plain: str, spans: List[Tuple[int, int]]) -> Set[int]:
    """The words of a passage that a hyphen and white space join or part
    ("drei-" + line break + "zehn", "drei- zehn" is 13, or 3 and 10): read
    neither way."""
    broken: Set[int] = set()
    for at in range(len(spans) - 1):
        gap = plain[spans[at][1]:spans[at + 1][0]]
        if gap[:1] == "-" and gap[1:].isspace():
            broken.update((at, at + 1))
    return broken


def _month(word: str) -> Optional[Tuple[int, bool]]:
    """The month a word names, and whether surely (outside a date)."""
    if word in _MONTHS:
        return _MONTHS[word], word not in _NAME_MONTHS
    if word.endswith("s") and word[:-1] in _MONTHS and word not in ("mais", "mays"):
        return _MONTHS[word[:-1]], word[:-1] not in _NAME_MONTHS
    if word in _MONTHS_SHORT:
        return _MONTHS_SHORT[word], False
    return None


def _is_noun_acht(words: List[str], at: int) -> bool:
    before = words[at - 1] if at else ""
    after = [word for word in words[at + 1:at + 3] if word != "zu"][:1]
    return before in _NOUN_ACHT_BEFORE or (bool(after) and after[0] in _NOUN_ACHT_VERBS)


@lru_cache(maxsize=8192)
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


@lru_cache(maxsize=8192)
def _compound(word: str) -> Optional[Tuple[str, str]]:
    """``(stem, value)`` when ``word`` begins with a number word and goes on:
    ``value`` is that number when the rest counts a span of time, rooms or a
    multiple ("dreimonatig": 3), the number with its sign when it counts per
    cent ("f\u00fcnfprozentig": 5%), else empty ("zweite",
    "Zweidrittelmehrheit", "einj\u00e4hrig": "ein" is no sure 1). Also when a
    number word stands inside the word before a unit or a fraction
    ("Mindestdreimonatsfrist"). None for other words."""
    if word.startswith(_NOT_NUMBERS) or _stem(word) in _NOT_NUMBER_STEMS:
        return None
    if word in _NUMBER_STEMS:
        return word, ""
    for cut in range(min(len(word) - 1, _LONGEST_NUMBER_WORD), 2, -1):
        head, rest = word[:cut], word[cut:]
        if head in ("ein", "eins"):
            if rest.startswith(_ONE_COUNTED + _FRACTIONS):
                return _stem(word), ""
            continue
        value = _number_word(head)
        if value is not None:
            return _stem(word), _counted_value(value, rest)
        if head in _NUMBER_STEMS:
            return _stem(word), ""
    return _inner_number(word)


def _counted_value(value: int, rest: str) -> str:
    if rest.startswith(_FRACTIONS):
        return ""
    per = _per_of(rest)
    if per:
        return str(value) + per
    return str(value) if rest.startswith(_TIME_UNITS) else ""


#: What a number word inside a word stands before, and the longest such number word looked for.
_INNER_STEMS = _TIME_UNITS + _COUNTED + _FRACTIONS_INSIDE + ("prozent", "promill")
_LONGEST_INNER = 30


def _inner_number(word: str) -> Optional[Tuple[str, str]]:
    """A number word inside ``word``, right before a unit it counts or a
    fraction ("Mindestdreimonatsfrist", "Mindestzweidrittelmehrheit"). "acht"
    alone does not count there: "Pachtjahr", "Nachtmal"."""
    size = min(len(word), _LONGEST_NUMBER_WORD)
    for at in range(4, size - 2):
        rest = word[at:]
        if not rest.startswith(_INNER_STEMS):
            continue
        for start in range(max(1, at - _LONGEST_INNER), at - 2):
            head = word[start:at]
            if head in ("ein", "eins") and rest.startswith(_ONE_COUNTED):
                # "Mindesteinjahresfrist": the word stands for itself, as "einjaehrig" does; the article
                # before it is not checked since "ein" counts only before a unit (PMVP-20 context).
                return _stem(word), ""
            value = _number_word(head)
            if value is not None and head != "acht":
                return _stem(word), _counted_value(value, rest)
    return None


def _apply_modifiers(plain: str, items: List[_Item]) -> List[_Item]:
    """"minus" before a number; a per cent or per mille sign or word, a
    scale ("2 Mio.", "5 Tausend") and a fraction ("zwei Drittel") after it
    become part of that number. A scale that is also another word ("Mia")
    counts only there."""
    out: List[_Item] = []
    for item in items:
        last = out[-1] if out else None
        gap = plain[last.end:item.start] if last is not None else ""
        joined = gap.isspace()
        number = last is not None and last.kind in ("num", "ein") and not last.scale
        if joined and item.kind in ("num", "ein") and last.kind == "minus":
            out.pop()
            item.start = last.start
            if item.kind == "ein":
                item.value = "-"
            elif not item.text.startswith(tuple(_SIGNS)):
                item.text = "-" + item.text
        elif ((joined or gap == "-") and item.kind == "per" and number and not last.per
              and not (last.text and last.text[-1] in _PER)):
            last.per, last.end = item.text, item.end
            continue
        elif joined and item.kind == "scale" and number:
            last.scale, last.end = item.text, item.end
            continue
        elif (joined and item.kind == "num" and item.word in _SCALE_WORDS and number
              and last.kind == "num" and not last.word and not last.per):
            last.scale, last.end = _SCALE_WORDS[item.word], item.end  # "5 Tausend" is not 5
            continue
        elif (joined and item.kind == "compound" and item.text in _FRACTION_WORDS and number
              and last.kind == "num" and not last.per):
            last.scale, last.end = "/" + item.text, item.end  # "zwei Drittel" is not 2
            continue
        out.append(item)
    return [item for item in out if item.kind not in ("minus", "per") and (item.sure or item.kind == "month")]


def _join_dates(plain: str, items: List[_Item]) -> List[_Item]:
    """A month with a day before it ("1. M\u00e4rz", "31 March") or a year after
    it ("M\u00e4rz 2024"), or both, is one date."""
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
