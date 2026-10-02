"""Page-break markers on the wire (GI-INGEST-17, plan decision D11).

The server advances a chunk's page once per form feed (``\\f``) it finds
before the chunk's first character and otherwise copies the part's declared
``page_number`` onto every chunk. Without markers a hit on page 7 of a
30-page Jahresrechnung is reported on page 1 — the page the part started on.

The contract is stated in ONE coordinate system, the unmarked ``content.text``
the extractor produced (Alloy: ``mechanisms/client_pipeline.als`` section C,
proved by ``data_plane/page_break_provenance.als``):

* the part's ``page_number`` is the page of its first character; the join
  whitespace between two pages belongs to the preceding page (that is what
  ``chunking._location_for_offset`` already computes from the sentence that
  contains the part's first offset);
* before the first character of every TEXT page whose start lies STRICTLY
  inside the part the RC writes ``page.index - previous_text_page.index``
  form feeds: one for the page itself plus one per EMPTY page in between
  (blank duplex backs, OCR-skipped or failed pages have no text and no
  ``line_start``, so they cannot carry a marker of their own — writing one
  marker per text page would label every page after a blank one too low);
* never a marker at the part's first character (the declared page already
  names it; a marker there makes the server advance twice), never for page
  starts that precede the part (offsets are relative to the part, not the
  document); leading empty pages produce nothing, there is no earlier text
  page to count from.

The chunker (``knovas_transmit.chunking.iter_text_chunks_with_location(..., page_markers=
True)``) reserves room for the markers inside the part budget and applies
them AFTER the heading prefix is prepended, so the marker still lands before
the page's first character on the wire. The context sidecar is always built
from the unmarked text.
"""
from __future__ import annotations

import bisect
from typing import Sequence

from knovas_extract.result import Page

#: INGESTION_PAGE_BREAK_MARKER on the server (form feed by default).
PAGE_BREAK_MARKER = "\f"

#: (document offset of the page's first character, number of markers to
#: write before it) for every text page that has an earlier text page.
PageStart = tuple[int, int]


def line_start_offsets(text: str) -> list[int]:
    """Offset of the first character of every line; line 1 starts at 0."""
    offsets = [0]
    pos = text.find("\n")
    while pos >= 0:
        offsets.append(pos + 1)
        pos = text.find("\n", pos + 1)
    return offsets


def text_page_starts(pages: Sequence[Page] | None, text: str) -> list[PageStart]:
    """Marker positions for a document, computed once per document.

    A TEXT page is one with non-empty ``text`` and a ``line_start``; every
    other page is EMPTY and only counted. The first text page never gets a
    marker (its number is carried by ``page_number``), so the list holds one
    entry per text page after the first, with the count of pages passed
    since the previous text page.
    """
    if not pages:
        return []
    offsets = line_start_offsets(text)
    starts: list[PageStart] = []
    previous_text_index: int | None = None
    for page in sorted(pages, key=lambda p: int(p.index)):
        if not page.text or page.line_start is None:
            continue
        line = int(page.line_start)
        if line < 1 or line > len(offsets):
            continue
        index = int(page.index)
        if previous_text_index is not None:
            count = index - previous_text_index
            if count >= 1:
                starts.append((offsets[line - 1], count))
        previous_text_index = index
    starts.sort()
    return starts


def markers_inside(starts: Sequence[PageStart], part_start: int, part_end: int) -> list[PageStart]:
    """Page starts strictly inside the part ``[part_start, part_end)``.

    ``part_end`` is one past the part's last character, so "at or before the
    last character" (Alloy ``posLte[g.pgStart, u.upLast]``) is ``offset <
    part_end``; "strictly after the first character" is ``offset >
    part_start``. A page beginning exactly where the NEXT part begins gets
    no marker here — that part declares it.
    """
    if not starts or part_end <= part_start + 1:
        return []
    lo = bisect.bisect_right(starts, part_start, key=lambda item: item[0])
    hi = bisect.bisect_left(starts, part_end, key=lambda item: item[0])
    return list(starts[lo:hi])


def marker_count_inside(starts: Sequence[PageStart], part_start: int, part_end: int) -> int:
    """Characters the markers of this part add to the snippet."""
    return sum(count for _offset, count in markers_inside(starts, part_start, part_end))


def apply_page_markers(snippet: str, markers: Sequence[PageStart], shift: int = 0) -> str:
    """Insert ``count`` form feeds at every (part-relative offset + ``shift``).

    ``shift`` is the number of characters the chunker put in front of the
    raw part (the heading prefix) or by which it changed the first line;
    page starts are line starts after the first line, so the shift never
    moves a marker across one.
    """
    if not markers:
        return snippet
    pieces: list[str] = []
    cursor = 0
    for relative, count in markers:
        pos = relative + shift
        if pos <= cursor or pos > len(snippet):
            continue
        pieces.append(snippet[cursor:pos])
        pieces.append(PAGE_BREAK_MARKER * int(count))
        cursor = pos
    pieces.append(snippet[cursor:])
    return "".join(pieces)


def insert_page_markers(snippet: str, pages: Sequence[Page], part_start: int, text: str) -> str:
    """Return ``snippet`` (``text[part_start:part_start + len(snippet)]``)
    with the page-break markers of this part inserted.

    Offsets are relative to the part: a page that starts before
    ``part_start`` never produces a marker, and neither does the page the
    part starts on.
    """
    starts = text_page_starts(pages, text)
    part_end = part_start + len(snippet)
    markers = [(offset - part_start, count) for offset, count in markers_inside(starts, part_start, part_end)]
    return apply_page_markers(snippet, markers)
