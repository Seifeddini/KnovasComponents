"""The passage around each hit, read from its document's text (PMVP-20 context, Jira SS-732; FU-37).

The Core's search sends each hit's own chunk only (2 to 172 characters measured on the demo tenant), and no answer can
be quoted from that. The founder decided on 2026-11-22 that the assistant may read the Core's document-text route
(``GET /secured/document/<uuid>/text``, a document's indexed text in reading order, in parts of at most 20,000
characters) to give the model the text around each hit, under five limits. Each is kept here or in the reader that
``assistant.core.make_read_text`` builds:

1. **Only documents the person's own search just returned.** ``widen_passages`` reads the ``document_uuid`` of the
   passages this question's search returned (``Passage.document_uuid``, taken from the search row) and nothing
   else. No id from the question, the chat, the model's answer, an address or an earlier question reaches it, and
   nothing is kept from one question to the next.
2. **The Core decides every read.** Whatever it answers or fails to answer -- a 404, 401, 403, 422, 429, 5xx, a
   timeout, no connection, a malformed part -- leaves that hit's passage as the search's snippet.
   ``widen_passages`` never raises, so the answer never fails because a read failed. After a 401, a 429 or a 503
   (or no principal) no further document is read for this question: the Core would answer the same.
3. **As the person.** The reader is ``KnovasAPIClient.document_text``: the same client as the search, called inside
   the same ``asserting_for`` block (``AsTheAssistant``), so it carries the same principal and declares the same
   actor (``?actor=`` on a GET). The Core takes no claim on this GET yet (Core request CR-1c); it reads with the
   tenant's posture, as the search does under ``disabled``.
4. **Bounded.** At most ``ASSISTANT_CONTEXT_DOCS`` documents per question (default and most 8, the passages the
   prompt can hold; 0 reads none), at most ``MAX_PARTS`` parts of ``PART_CHARS`` characters per document, one read at
   a time, and ``ASSISTANT_CONTEXT_SECONDS`` (default and most 8) of wall-clock time for all reads of a question: the
   client gives a read up at that deadline however slowly the Core answers, and the remaining hits keep their
   snippets. One reading per document per question: two hits in one document share it.
5. **Nothing of the text in logs**, DEBUG included: one line per question with counts (passages, documents read,
   requests, parts, passages widened, and the hits that kept their snippet, by reason).

The context (``find_chunk``, ``cut_around``): the hit's chunk is looked for in the text read, white space
normalised and a leading or trailing ellipsis dropped -- first the whole snippet, else the longest run of at least
``MIN_RUN_WORDS`` consecutive words of it. The next part is read only while the chunk is not in the text read so far.
Around it, ``ASSISTANT_PASSAGE_CHARS`` characters (default 1,200, at most 2,000, the passage's own limit) of the
document's text, cut at word boundaries, the chunk inside and centred where the document allows. The context replaces
the snippet as the passage the model is given and the check reads; its page and sentence stay those of the chunk the
search named.

This module imports neither Flask, nor the app, nor the HTTP client. It is ASCII-only.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from assistant.errors import CoreBusy, CoreFailed

logger = logging.getLogger(__name__)

#: ``ASSISTANT_CONTEXT_DOCS``: the most documents read per question, 0 to 8 (0 reads none). A larger value is cut to
#: the most (PMVP-20 context's verification: the founder's limit is 8, the passages the prompt can hold).
DEFAULT_CONTEXT_DOCS = 8
MAX_CONTEXT_DOCS = 8
#: ``ASSISTANT_CONTEXT_SECONDS``: the wall-clock time all reads of one question may take, more than 0 and at most 8
#: seconds; a larger value is cut to the most.
DEFAULT_CONTEXT_SECONDS = 8.0
MAX_CONTEXT_SECONDS = 8.0
#: A read is not started with less time left than this.
MIN_READ_SECONDS = 0.2
#: The most parts read of one document, and the characters asked for per part (the Core's most).
MAX_PARTS = 3
PART_CHARS = 20_000
#: A run of the snippet's words that stands for the chunk when the whole snippet is not in the text.
MIN_RUN_WORDS = 8
#: ``ASSISTANT_PASSAGE_CHARS`` as ``knovas_client`` reads it (a test keeps the three equal): 100 to 2,000, a larger
#: value cut to 2,000. The client's search has already warned about a wrong value in the same question, so it is read
#: here without a warning.
DEFAULT_PASSAGE_CHARS = 1_200
MIN_PASSAGE_CHARS = 100
MAX_PASSAGE_CHARS = 2_000

ELLIPSIS = "\u2026"

#: Why a hit kept the search's snippet, in the order of the log line.
NOTHING_MORE = "nothing_more"      # the chunk was found, but the document holds no more text around it
NOT_FOUND = "not_found"            # the text was read, the chunk is not in it (or not in the parts allowed)
NOT_VISIBLE = "not_visible"        # 404: missing, or not visible to the principal (the Core answers alike)
REFUSED = "refused"                # 401, 403, 422, another 4xx; or nobody to read as
BUSY = "busy"                      # 429, 503
CORE_ERROR = "core_error"          # another 5xx
TIMEOUT = "timeout"
TRANSPORT = "transport"            # no connection, TLS
MALFORMED = "malformed"            # a 200 that is not a document-text part
BUDGET = "budget"                  # ASSISTANT_CONTEXT_SECONDS used up
LIMIT = "limit"                    # beyond ASSISTANT_CONTEXT_DOCS documents
STOPPED = "stopped"                # not read: an earlier read of this question was refused for every document
NO_ID = "no_id"                    # the row carried no document id
ERROR = "error"                    # anything else the reader raised
REASONS = (NOTHING_MORE, NOT_FOUND, NOT_VISIBLE, REFUSED, BUSY, CORE_ERROR, TIMEOUT, TRANSPORT, MALFORMED,
           BUDGET, LIMIT, STOPPED, NO_ID, ERROR)

#: ``read_text(document_uuid, offset, timeout) -> {"text", "next_offset", "offset"?}``; raises on any failure.
ReadText = Callable[[str, int, float], Mapping[str, Any]]

_WHITE = re.compile(r"\s+")
# What str.splitlines() breaks a line at.
_LINE_BREAK = re.compile("[\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")
_ELLIPSIS_SPLIT = re.compile(" ?" + ELLIPSIS + " ?")
_SECONDS = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,3})?")


# ---------------------------------------------------------------------------
# The settings, read per question
# ---------------------------------------------------------------------------


def context_docs() -> int:
    """``ASSISTANT_CONTEXT_DOCS``: a whole number from 0 to ``MAX_CONTEXT_DOCS``; a larger one is cut to it, anything
    else is the default, either with one warning that names the setting, never its value."""
    raw = (os.environ.get("ASSISTANT_CONTEXT_DOCS") or "").strip()
    if not raw:
        return DEFAULT_CONTEXT_DOCS
    if raw.isascii() and raw.isdigit():
        if int(raw) <= MAX_CONTEXT_DOCS:
            return int(raw)
        logger.warning("ASSISTANT_CONTEXT_DOCS is above %d; using %d", MAX_CONTEXT_DOCS, MAX_CONTEXT_DOCS)
        return MAX_CONTEXT_DOCS
    logger.warning("Invalid ASSISTANT_CONTEXT_DOCS (a whole number from 0 to %d); using %d",
                   MAX_CONTEXT_DOCS, DEFAULT_CONTEXT_DOCS)
    return DEFAULT_CONTEXT_DOCS


def context_seconds() -> float:
    """``ASSISTANT_CONTEXT_SECONDS``: more than 0 (``8`` or ``2.5``); a value above ``MAX_CONTEXT_SECONDS`` is cut to
    it, anything else is the default, either with one warning that names the setting, never its value."""
    raw = (os.environ.get("ASSISTANT_CONTEXT_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_CONTEXT_SECONDS
    if _SECONDS.fullmatch(raw) and float(raw) > 0:
        if float(raw) <= MAX_CONTEXT_SECONDS:
            return float(raw)
        logger.warning("ASSISTANT_CONTEXT_SECONDS is above %d; using %d", int(MAX_CONTEXT_SECONDS),
                       int(MAX_CONTEXT_SECONDS))
        return MAX_CONTEXT_SECONDS
    logger.warning("Invalid ASSISTANT_CONTEXT_SECONDS (seconds, more than 0 and at most %d); using %d",
                   int(MAX_CONTEXT_SECONDS), int(DEFAULT_CONTEXT_SECONDS))
    return DEFAULT_CONTEXT_SECONDS


def passage_chars() -> int:
    """``ASSISTANT_PASSAGE_CHARS`` as the client's search reads it, without its warning."""
    raw = (os.environ.get("ASSISTANT_PASSAGE_CHARS") or "").strip()
    if raw.isascii() and raw.isdigit() and int(raw) >= MIN_PASSAGE_CHARS:
        return min(int(raw), MAX_PASSAGE_CHARS)
    return DEFAULT_PASSAGE_CHARS


# ---------------------------------------------------------------------------
# Finding the chunk and cutting the context (pure)
# ---------------------------------------------------------------------------


def tidy(text: str) -> str:
    """``text`` with each run of white space as one character -- a line break where the run holds one, else a
    space -- and none at either end.

    Line breaks stay because the number check reads two numbers on two lines as two numbers; the quote check
    compares white space of any kind alike. One character per run, so a position here is a position in
    ``text.replace("\\n", " ")``, where the chunk is looked for.
    """
    return _WHITE.sub(lambda m: "\n" if _LINE_BREAK.search(m.group()) else " ", text.strip())


def find_chunk(snippet: str, text: str) -> Optional[Tuple[int, int]]:
    """Where the hit's chunk stands in ``text`` (as ``tidy`` gives it): ``(start, end)``, or None.

    The snippet with its white space normalised and a leading or trailing ellipsis dropped: first the whole of it,
    else the longest run of at least ``MIN_RUN_WORDS`` consecutive words of it, never across an ellipsis inside it
    (where two snippets of one chunk were joined). A match does not begin or end inside a word of the text
    (``Art. 5`` is not found in ``Art. 55``). Of several matches the first counts.
    """
    flat = text.replace("\n", " ")
    core = _WHITE.sub(" ", snippet).strip(ELLIPSIS + " ")
    if not core or not flat:
        return None
    whole = _find_bounded(core, flat)
    if whole is not None:
        return whole
    best: Optional[Tuple[int, int]] = None
    best_words = MIN_RUN_WORDS - 1
    for piece in _ELLIPSIS_SPLIT.split(core):
        words = [word for word in piece.split(" ") if word]
        if len(words) <= best_words:
            continue
        found, count = _longest_run(words, flat, best_words + 1)
        if found is not None:
            best, best_words = found, count
    return best


def _find_bounded(needle: str, flat: str) -> Optional[Tuple[int, int]]:
    """The first place of ``needle`` in ``flat`` that does not begin or end inside a word."""
    at = flat.find(needle)
    while at != -1:
        end = at + len(needle)
        inside_before = at > 0 and flat[at - 1].isalnum() and needle[0].isalnum()
        inside_after = end < len(flat) and flat[end].isalnum() and needle[-1].isalnum()
        if not inside_before and not inside_after:
            return at, end
        at = flat.find(needle, at + 1)
    return None


def _run_of(words: Sequence[str], count: int, flat: str) -> Optional[Tuple[int, int]]:
    """The first run of ``count`` consecutive ``words`` found in ``flat`` (the earliest run of the snippet)."""
    for i in range(len(words) - count + 1):
        found = _find_bounded(" ".join(words[i:i + count]), flat)
        if found is not None:
            return found
    return None


def _longest_run(words: Sequence[str], flat: str, at_least: int) -> Tuple[Optional[Tuple[int, int]], int]:
    """The longest run of at least ``at_least`` words found, and its length (a binary search: a run that is found
    holds shorter runs that are found)."""
    found = _run_of(words, at_least, flat)
    if found is None:
        return None, 0
    low, high = at_least, len(words)
    while low < high:
        middle = (low + high + 1) // 2
        hit = _run_of(words, middle, flat)
        if hit is not None:
            low, found = middle, hit
        else:
            high = middle - 1
    return found, low


def cut_around(text: str, start: int, end: int, limit: int) -> str:
    """At most ``limit`` characters of ``text`` around ``text[start:end]``, the chunk.

    The chunk is centred where the text allows (at its start or end the window takes more from the other side),
    and the window is cut at word boundaries: an edge that falls inside a word moves inward to the next white space,
    never into the chunk. White space at either end is dropped. A chunk of ``limit`` characters or more is returned
    alone.
    """
    size = end - start
    if size >= limit:
        return text[start:end]
    room = limit - size
    low = start - room // 2
    high = end + (room - room // 2)
    if low < 0:
        high, low = min(len(text), high - low), 0
    if high > len(text):
        low, high = max(0, low - (high - len(text))), len(text)
    while 0 < low < start and not text[low - 1].isspace() and not text[low].isspace():
        low += 1
    while end < high < len(text) and not text[high].isspace() and not text[high - 1].isspace():
        high -= 1
    return text[low:high].strip()


# ---------------------------------------------------------------------------
# Reading, under the five limits
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Document:
    """One document's reading in this question: the text read so far, where the next part starts."""

    raw: str = ""
    text: str = ""
    next_offset: Optional[int] = 0
    parts: int = 0
    failed: Optional[str] = None


def widen_passages(passages: Sequence[Any], read_text: ReadText, *,
                   clock: Callable[[], float] = time.monotonic) -> List[Any]:
    """The passages, each widened to the context around its chunk where the Core allowed it. Never raises.

    ``passages`` are this question's (``assistant.service.Passage``: ``text``, ``document_uuid``), in search order;
    a widened one is a copy with ``text`` replaced, everything else (number, pointer, title, page, sentence) kept.
    ``read_text`` is the only way a document is read (``assistant.core.make_read_text``). Hits are taken in search
    order, so the limits cut the lowest ranked first.
    """
    started = clock()
    out = list(passages)
    counts = dict.fromkeys(REASONS, 0)
    tally = {"widened": 0, "reads": 0, "parts": 0}
    documents: Dict[str, _Document] = {}
    try:
        most_documents, seconds, limit = context_docs(), context_seconds(), passage_chars()
        deadline = started + seconds
        stopped = [False]
        for index, passage in enumerate(passages):
            reason, context = _widen_one(passage, read_text, documents, most_documents, deadline, limit, clock,
                                         stopped, tally)
            if context is not None:
                out[index] = dataclasses.replace(passage, text=context)
                tally["widened"] += 1
            else:
                counts[reason] += 1
    except Exception as exc:  # noqa: BLE001 - the answer never fails because a read failed (limit 2)
        logger.warning("Knovas Assistent context: not widened (%s)", type(exc).__name__)
        return list(passages)
    logger.info(
        "Knovas Assistent context: passages=%d documents=%d reads=%d parts=%d widened=%d %s ms=%d",
        len(out), sum(1 for d in documents.values() if d.parts), tally["reads"], tally["parts"], tally["widened"],
        " ".join(f"{reason}={counts[reason]}" for reason in REASONS), int((clock() - started) * 1000),
    )
    return out


def _widen_one(passage: Any, read_text: ReadText, documents: Dict[str, _Document], most_documents: int,
               deadline: float, limit: int, clock: Callable[[], float], stopped: List[bool],
               tally: Dict[str, int]) -> Tuple[str, Optional[str]]:
    """``(reason, None)`` when the passage keeps its snippet, ``("", context)`` when it is widened."""
    document_uuid = getattr(passage, "document_uuid", None)
    snippet = getattr(passage, "text", None)
    if not isinstance(document_uuid, str) or not document_uuid or not isinstance(snippet, str):
        return NO_ID, None
    document = documents.get(document_uuid)
    if document is None:
        if len(documents) >= most_documents:
            return LIMIT, None
        document = documents[document_uuid] = _Document()
    while True:
        if document.parts:
            span = find_chunk(snippet, document.text)
            if span is not None:
                context = cut_around(document.text, span[0], span[1], limit)
                if len(context) > len(snippet.strip()):
                    return "", context
                return NOTHING_MORE, None
        if document.failed is not None:
            return document.failed, None
        if document.next_offset is None or document.parts >= MAX_PARTS:
            return NOT_FOUND, None
        if stopped[0]:
            return STOPPED, None
        left = deadline - clock()
        if left < MIN_READ_SECONDS:
            return BUDGET, None
        offset = document.next_offset
        tally["reads"] += 1
        try:
            part = read_text(document_uuid, offset, left)
        except Exception as exc:  # noqa: BLE001 - the Core decides; any failure keeps the snippet (limit 2)
            document.failed = _reason(exc)
            stopped[0] = stopped[0] or _stops_every_read(exc)
            continue
        text, next_offset = _part(part, offset)
        if text is None:
            document.failed = MALFORMED
            continue
        document.raw += text
        document.text = tidy(document.raw)
        document.next_offset = next_offset
        document.parts += 1
        tally["parts"] += 1


def _part(part: Any, offset: int) -> Tuple[Optional[str], Optional[int]]:
    """``(text, next_offset)`` of a part read at ``offset``, or ``(None, None)`` when it is not one."""
    if not isinstance(part, Mapping):
        return None, None
    text, next_offset, at = part.get("text"), part.get("next_offset"), part.get("offset", offset)
    if not isinstance(text, str) or len(text) > PART_CHARS or at != offset or isinstance(at, bool):
        return None, None
    if next_offset is not None and (not isinstance(next_offset, int) or isinstance(next_offset, bool)
                                    or next_offset <= offset):
        return None, None
    return text, next_offset


def _reason(exc: BaseException) -> str:
    """Why a read gave nothing, from what the reader raised (``assistant.core.make_read_text``)."""
    if isinstance(exc, CoreBusy):
        return BUSY
    if isinstance(exc, CoreFailed):
        status = exc.status
        if isinstance(status, int) and not isinstance(status, bool):
            if status == 404:
                return NOT_VISIBLE
            if status in (429, 503):
                return BUSY
            if status >= 500:
                return CORE_ERROR
            return REFUSED
        return {"timeout": TIMEOUT, "transport": TRANSPORT, "malformed": MALFORMED,
                "no_principal": REFUSED}.get(exc.reason, ERROR)
    return ERROR


def _stops_every_read(exc: BaseException) -> bool:
    """A refusal the Core would give for every document of this question: busy, unauthenticated, nobody to read as."""
    if isinstance(exc, CoreBusy):
        return True
    if isinstance(exc, CoreFailed):
        return exc.status in (401, 429, 503) or exc.reason == "no_principal"
    return False
