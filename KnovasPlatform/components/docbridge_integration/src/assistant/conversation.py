"""The Knovas Assistent's own words: the reply (PMVP-20 conversation).

The founder, 2026-11-22: "the knovas assistant doesnt really behave like an LLM". Besides statements, the model
may now write one line of its own::

    {"reply": "...", "kind": "chat"}        a greeting, thanks, what the assistant can do
    {"reply": "...", "kind": "clarify"}     ask the person to be more specific, or say that nothing fits

A reply makes no claim about the documents, so it carries no source and no quote, and the page shows it as the
assistant speaking. What keeps a reply from becoming an unchecked claim is this module, not only the instruction.
A reply is shown only if all of this holds; otherwise it is dropped and counted (``check.REPLY_DROPPED``):

1. It is a JSON object with a text under ``reply`` and no ``statement`` (an object with both is a statement).
2. Its text, as shown (``check.shown_text``: what is not seen taken out, white space single), holds a letter, and
   all its letters are Latin ones, as a statement's must be.
3. At most ``MAX_REPLY_CHARS`` characters; beside statements, a lead-in of at most ``MAX_LEAD_IN_CHARS``.
4. **No number**: no digit (no character of Unicode's number categories), and nothing the statements' number check
   reads as a number (``check.claimed_numbers``: "drei", "einen Monat", a month, a Roman numeral) and no character
   that looks like a digit. A reply cannot state an amount, a deadline, a date or a file number.
5. **No name of the passages**: no word that names a document, a person, a firm, a place or a matter the passages
   hold (``passage_names``): every word of a passage's title and pointer, and every capitalised word of a
   passage's text that does not begin a sentence, unless it is a common word (``is_common``). Words are compared
   folded (case, accents and umlaut spellings do not matter) and by their German stem.
6. **One reply a turn.** The first that passes is shown; every further reply line is dropped.
7. **A lead-in needs its statements.** When the answer holds statements (objects with ``statement``), a reply is a
   lead-in: it is shown above them only if at least one of them passed the check; otherwise it would lead into
   nothing and is dropped.

What these rules cannot see: a reply that states a fact in common words without a number or a name ("Ja, das ist
erlaubt."). The instruction forbids it; the rules keep such a line from carrying the specifics (who, which
document, which matter, how much, how long, when).

``clean_title`` is the title the pages show (a leading UUID dropped, ``<matter>_<kind>`` as "matter \u00b7 kind");
``nearest`` the passages shown under a clarifying reply or a no-answer.

This module imports neither the service, nor Flask, nor an HTTP client. It is ASCII-only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from assistant import check

try:  # the Platform's German stemmer (snowball); without it words are compared as folded
    from german_text import stem as _german_stem
except Exception:  # pragma: no cover - only without the package
    def _german_stem(word: str) -> str:
        return word

# ---------------------------------------------------------------------------
# Knobs
# ---------------------------------------------------------------------------

REPLY_KEY = "reply"
KIND_KEY = "kind"
#: A greeting, thanks, what the assistant can do: shown alone, no passages under it.
KIND_CHAT = "chat"
#: Asking to be more specific, or saying that nothing fits: the nearest passages under it.
KIND_CLARIFY = "clarify"
KINDS = (KIND_CHAT, KIND_CLARIFY)
#: The most characters of a reply that stands alone.
MAX_REPLY_CHARS = 400
#: The most characters of a reply beside statements ("a short lead-in").
MAX_LEAD_IN_CHARS = 200
#: The most passages listed under a clarifying reply or a no-answer ("Die naechsten Stellen").
NEAREST_PASSAGES = 5
#: Words shorter than this are never names (articles, "AG", "OR", initials).
MIN_NAME_LETTERS = 3


@dataclass(frozen=True)
class Said:
    """A reply that passed: its text as shown, and its kind (``KIND_CHAT`` or ``KIND_CLARIFY``; ``""`` for a
    lead-in beside statements)."""

    text: str
    kind: str


# ---------------------------------------------------------------------------
# Reading the replies of an answer
# ---------------------------------------------------------------------------


def check_replies(text: str, passages: Sequence[Any], *, statements_shown: int) -> Tuple[Optional[Said], int]:
    """``(the reply to show or None, how many reply lines were dropped)`` for a generated answer.

    ``passages`` are the passages the model was given (anything with ``title``, ``pointer`` and ``text``);
    ``statements_shown`` how many statements of this answer passed ``check.check_answer``.
    """
    values = check.read_answer(text)
    replies = [value for value in values if check.is_reply(value)]
    if not replies:
        return None, 0
    lead_in = any(isinstance(value, dict) and "statement" in value for value in values)
    if lead_in and statements_shown <= 0:
        return None, len(replies)
    names = passage_names(passages)
    said: Optional[Said] = None
    dropped = 0
    for value in replies:
        found = None if said is not None else check_reply(value, names, lead_in=lead_in)
        if found is None:
            dropped += 1
        else:
            said = found
    return said, dropped


def check_reply(value: Any, names: FrozenSet[str], *, lead_in: bool = False) -> Optional[Said]:
    """The reply ``value`` holds, or None when it breaks a rule. ``names`` are ``passage_names`` of the answer's
    passages."""
    if not check.is_reply(value):
        return None
    raw = value.get(REPLY_KEY)
    if not isinstance(raw, str):
        return None
    shown = check.shown_text(raw, ())
    limit = MAX_LEAD_IN_CHARS if lead_in else MAX_REPLY_CHARS
    if not shown or len(shown) > limit:
        return None
    if not _latin_letters_only(shown) or holds_a_number(shown) or names_a_passage(shown, names):
        return None
    if lead_in:
        return Said(text=shown, kind="")
    kind = value.get(KIND_KEY)
    return Said(text=shown, kind=kind if kind in KINDS else KIND_CHAT)


def _latin_letters_only(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    return bool(letters) and all(char in check._LETTERS_TOO or unicodedata.name(char, "").startswith("LATIN")
                                 for char in letters)


def holds_a_number(text: str) -> bool:
    """Whether ``text`` holds a digit, a character of another number category or one that looks like a digit, or
    anything the statements' number check reads as a number."""
    if any(unicodedata.category(char)[0] == "N" for char in text):
        return True
    if check._odd_characters(text):
        return True
    # The number check reads a capital letter standing alone as a number ("Kategorie C", "Teil I"): "E-Mail" and the
    # English "I" before a contraction or a verb (the demo tenant, 2026-11-22: "I'm the Knovas Assistent") are words.
    return bool(check.claimed_numbers(_ENGLISH_I.sub("me", _E_MAIL.sub("Email", text))))


_E_MAIL = re.compile("(?<![^\\W_])[Ee]-[Mm]ail")
_ENGLISH_I = re.compile("(?<![^\\s\"'(\u201c\u201e\u00ab])I(?=['\u2019](?:m|ll|ve|d)\\b|\\s+(?:am|can|cannot|"
                        "could|would|will|have|had|do|did|don|found|looked|searched|see|think|help|need|was|only|just|"
                        "also|couldn|didn|haven|wasn)\\b)")


# ---------------------------------------------------------------------------
# Names of the passages
# ---------------------------------------------------------------------------

_WORD = re.compile("[^\\W\\d_]+")
#: What ends a sentence or opens a part, so that the capital after it may be the sentence's and not a name's.
_STARTERS = frozenset(".!?:;\u2026([{\"'\u00ab\u00bb\u201c\u201d\u201e\u2018\u2019\u201a\u2022*/|>\u2013\u2014")


def passage_names(passages: Sequence[Any]) -> FrozenSet[str]:
    """The folded words (and their stems) that would name something of the passages.

    Every word of a passage's ``title`` and ``pointer`` (the file name, a client's folder), and every word of its
    ``text`` that is capitalised and does not begin a sentence, a line or a list item; each only when it is no
    common word (``is_common``) and has at least ``MIN_NAME_LETTERS`` letters.
    """
    found: Set[str] = set()
    for passage in passages or ():
        for value in (getattr(passage, "title", None), getattr(passage, "pointer", None)):
            if isinstance(value, str):
                _add_names(found, (match.group(0) for match in _WORD.finditer(strip_uuid(value))))
        text = getattr(passage, "text", None)
        if isinstance(text, str):
            _add_names(found, (match.group(0) for match in _WORD.finditer(text)
                               if match.group(0)[:1].isupper() and not _begins(text, match.start())))
    return frozenset(found)


def _add_names(found: Set[str], words: Iterable[str]) -> None:
    for word in words:
        if len(word) < MIN_NAME_LETTERS or is_common(word):
            continue
        folded = fold(word)
        found.add(folded)
        found.add(_key(folded))


def _begins(text: str, at: int) -> bool:
    """Whether the word at ``at`` begins the text, a line, a sentence or a list item."""
    before = text[:at]
    kept = before.rstrip()
    if not kept or "\n" in before[len(kept):]:
        return True
    last = kept[-1]
    if last == "-":  # a dash between spaces or a list mark, never the hyphen of "Mueller-Meier"
        return len(kept) < len(before) and (len(kept) == 1 or kept[-2].isspace())
    return last in _STARTERS or last.isdigit()


def names_a_passage(text: str, names: FrozenSet[str]) -> bool:
    """Whether a word of ``text`` is one of ``names`` (folded, by its stem, or with a genitive s)."""
    if not names:
        return False
    for match in _WORD.finditer(text):
        word = match.group(0)
        if len(word) < MIN_NAME_LETTERS or is_common(word):
            continue
        folded = fold(word)
        if folded in names or _key(folded) in names or (folded.endswith("s") and folded[:-1] in names):
            return True
    return False


def fold(word: str) -> str:
    """``word`` lower case, without accents, with the umlaut spellings as the plain letter: "M\u00fcller",
    "Mueller" and "MULLER" are one."""
    plain = unicodedata.normalize("NFKD", word.lower().replace("\u00df", "ss"))
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return plain.replace("ae", "a").replace("oe", "o").replace("ue", "u")


@lru_cache(maxsize=16384)
def _key(folded: str) -> str:
    return _german_stem(folded) or folded


@lru_cache(maxsize=16384)
def is_common(word: str) -> bool:
    """Whether ``word`` is a common word, which names nothing: a function word, a word of the general vocabulary a
    reply needs (``_NOUNS``), an inflected form of one (by its stem), or a compound of such nouns ("Mietvertrag",
    "Kuendigungsfrist")."""
    folded = fold(word)
    if len(folded) < MIN_NAME_LETTERS:
        return True
    return _common_folded(folded)


def _common_folded(folded: str) -> bool:
    return folded in _COMMON or _key(folded) in _COMMON_KEYS or _compound(folded)


@lru_cache(maxsize=16384)
def _compound(folded: str) -> bool:
    """Whether ``folded`` is a noun of ``_NOUNS`` followed by a common word, with a linking letter or two."""
    for end in range(MIN_NAME_LETTERS, len(folded) - MIN_NAME_LETTERS + 1):
        if folded[:end] not in _HEADS:
            continue
        rest = folded[end:]
        for link in _LINKS:
            if rest.startswith(link) and len(rest) - len(link) >= MIN_NAME_LETTERS:
                tail = rest[len(link):]
                if tail in _COMMON or _key(tail) in _COMMON_KEYS or _compound(tail):
                    return True
    return False


_LINKS = ("", "s", "es", "n", "en", "e", "er")

#: Function words of German and English: articles, pronouns (the polite "Sie", "Ihnen" too), prepositions,
#: conjunctions, adverbs, modal and auxiliary verbs, greetings. Written with ae/oe/ue/ss; ``fold`` reads both.
_FUNCTION_WORDS = """
der die das den dem des ein eine einen einem einer eines kein keine keinen keinem keiner keines
ich du er sie es wir ihr mich dich sich uns euch mir dir ihm ihn ihnen man
mein meine meinen meinem meiner meines dein deine deinen sein seine seinen seinem seiner seines
ihre ihren ihrem ihrer ihres unser unsere unseren unserem unserer euer eure
dieser diese dieses diesen diesem jener jene jenes welcher welche welches welchen welchem
derselbe dieselbe dasselbe solche solcher solches solchen
was wer wen wem wessen wie wo wann warum weshalb wieso wozu woher wohin womit worum wofuer worauf woran worin
wovon worueber
und oder aber denn sondern doch jedoch sowie sowohl weder noch als wenn ob dass weil damit obwohl waehrend
bevor nachdem seit bis falls sofern soweit sobald solange
um fuer mit ohne gegen durch ueber unter vor hinter neben zwischen an auf aus bei nach von zu zum zur im ins am
ans vom beim gemaess laut betreffend bezueglich wegen trotz innerhalb ausserhalb anstatt statt pro per
hier dort heute gestern morgen jetzt nun gerade bald immer nie oft manchmal wieder darin darauf dazu dafuer
davon damit darueber hierzu
bitte gerne gern danke dank vielen herzlich herzlichen hallo hey guten gut tag abend willkommen gruezi gruess
gruesse gott servus salut ciao tschuess freut freude herr herrn frau fraeulein dr prof
ja nein nicht nichts auch noch schon nur sehr mehr weniger viel viele alle allen aller alles jede jeder jedes
jeden jedem etwas einige einigen manche andere anderen anderer anderes weitere weiteren weiterer genau eher
etwa also dann sonst gleich leider vielleicht natuerlich selbstverstaendlich gewiss sicher bestimmt einfach
kurz klar passend passende passenden konkret konkrete konkreten
kann kannst koennen koennte koennten will willst wollen moechte moechten moechtest soll sollen sollte sollten
muss musst muessen darf duerfen
ist bist sind war waren bin seid sei seien habe hast hat haben hatte hatten wird wirst werden wurde wurden
wuerde wuerden gibt geben gab
hiermit hierbei dabei daher deshalb somit ferner zudem ausserdem insbesondere ebenso ebenfalls allerdings zwar
bzw usw etc vgl evtl ggf inkl zzgl exkl
the and or but if of to in on at for with by from about as into this that these those it its you your yours
we our us they them their he she his her me my mine
what which who whom whose how when where why can could would should will shall may might must do does did is
are was were be been being have has had
hello hey thanks thank please welcome sure yes not
"""

#: The general vocabulary a reply needs to talk about documents without naming one: the assistant, questions,
#: documents and their parts, matters and parties, the common kinds of legal documents and their parts, time.
#: These are also the heads of compounds ("Miet|vertrag", "Kuendigung|s|frist").
_NOUNS = """
knovas assistent assistentin
frage antwort hilfe auskunft information angabe hinweis anliegen wunsch anfrage
dokument datei unterlage akte dossier ablage ordner quelle stelle passage fundstelle suche suchbegriff begriff
stichwort text wortlaut zitat aussage beleg
name titel art typ sorte beispiel thema inhalt zusammenhang teil seite abschnitt kapitel absatz punkt ziffer
artikel paragraph paragraf detail ergebnis treffer liste uebersicht zusammenfassung
mandat mandant mandantin klient klientin kunde kundin kanzlei firma unternehmen gesellschaft betrieb buero team
fall sache angelegenheit geschaeft vorgang verfahren prozess ablauf schritt aufgabe termin
person partei gegenpartei gegenseite beteiligte anwalt anwaeltin rechtsanwalt rechtsanwaeltin richter gericht
behoerde amt
vertrag vereinbarung abrede klausel bestimmung regelung regel recht pflicht gesetz verordnung
frist datum zeitraum dauer beginn ende zeitpunkt
kuendigung miete mieter vermieter arbeit arbeitgeber arbeitnehmer kauf darlehen
vollmacht klage klageschrift klageantwort beschwerde berufung urteil entscheid verfuegung beschluss
brief schreiben korrespondenz mail email nachricht notiz memo protokoll bericht gutachten offerte angebot
rechnung beilage anhang anlage nachtrag entwurf fassung version kopie original
weisung richtlinie vorlage formular checkliste
woche monat jahr stunde
pdf docx doc xlsx xls pptx msg eml txt rtf odt csv
gmbh sarl
"""


def _folded_words(block: str) -> FrozenSet[str]:
    return frozenset(fold(word) for word in block.split())


_NOUN_WORDS = _folded_words(_NOUNS)
_COMMON = _folded_words(_FUNCTION_WORDS) | _NOUN_WORDS
#: The stems of the nouns only: a function word is common as written ("must" is no stem of "Muster").
_COMMON_KEYS = frozenset(_key(word) for word in _NOUN_WORDS)
_HEADS = frozenset(word for word in _NOUN_WORDS | {_key(word) for word in _NOUN_WORDS}
                   if len(word) >= MIN_NAME_LETTERS)


# ---------------------------------------------------------------------------
# What the pages show of a passage
# ---------------------------------------------------------------------------

_UUID_PREFIX = re.compile("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}_")
#: A file number: groups of digits joined by "-", "." or "/" ("2024-055", "24.117", "2023/17").
_MATTER = re.compile("(\\d+(?:[-./]\\d+)+)_(.+)")
SEPARATOR = " \u00b7 "


def strip_uuid(title: str) -> str:
    """``title`` without a leading UUID and its underscore (when something follows them)."""
    match = _UUID_PREFIX.match(title)
    if match and title[match.end():].strip(" _"):
        return title[match.end():]
    return title


def clean_title(title: Any) -> str:
    """The title the pages show: white space single, a leading UUID and its underscore dropped, and a
    ``<matter>_<kind>`` name as "matter \u00b7 kind" (the kind's further underscores as spaces):
    ``0ef33273-...-e31584baf6a7_2024-055_Beilage-Vertrag`` is "2024-055 \u00b7 Beilage-Vertrag". Any other title
    stays as it is."""
    text = " ".join(str(title if title is not None else "").split())
    rest = strip_uuid(text)
    match = _MATTER.fullmatch(rest)
    if match:
        kind = " ".join(match.group(2).replace("_", " ").split())
        if kind:
            return match.group(1) + SEPARATOR + kind
    return rest


def nearest(passages: Sequence[Any]) -> List[Any]:
    """The passages listed under a clarifying reply or a no-answer: the first ``NEAREST_PASSAGES`` in search
    order."""
    return list(passages or ())[:NEAREST_PASSAGES]


__all__: Sequence[str] = (
    "KINDS", "KIND_CHAT", "KIND_CLARIFY", "MAX_LEAD_IN_CHARS", "MAX_REPLY_CHARS", "NEAREST_PASSAGES", "Said",
    "check_replies", "check_reply", "clean_title", "fold", "holds_a_number", "is_common", "names_a_passage",
    "nearest", "passage_names", "strip_uuid",
)
