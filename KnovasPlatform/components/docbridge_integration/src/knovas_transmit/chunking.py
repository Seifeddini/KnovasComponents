"""Build transmission parts from knovas-extract content.

Mirror of ``RemoteController/src/sync/chunking.py``: page-break markers
(GI-INGEST-17), section headings and part budgets behave exactly as in the
RC's sync pipeline, so an admin upload and a synced file produce the same
wire format.
"""
from __future__ import annotations

import bisect
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from knovas_extract.result import Page, Section, Sentence

from knovas_transmit.page_markers import (
    PageStart,
    apply_page_markers,
    marker_count_inside,
    markers_inside,
    text_page_starts,
)
from knovas_transmit.section_pages import (
    adjust_chunk_end,
    apply_section_heading,
    section_prefix_at_offset,
)
from knovas_transmit.table_payload import assign_tables_to_parts


def _fit_markers_in_budget(page_starts: Sequence[PageStart], start: int, end: int, budget: int) -> int:
    """Lower ``end`` until the part's text plus its page markers fit ``budget``.

    One char per marker is reserved from the part budget (a text page after
    an empty page needs two). Lowering the end can only drop page starts out
    of the part, so the loop converges.
    """
    while end > start + 1:
        markers = marker_count_inside(page_starts, start, end)
        if (end - start) + markers <= budget:
            break
        end = max(start + 1, start + budget - markers)
    return end


def _location_for_offset(
    starts: Sequence[int],
    sentences: Sequence[Sentence],
    offset: int,
) -> tuple[Optional[int], Optional[int]]:
    if not sentences:
        return None, None
    idx = bisect.bisect_right(starts, offset) - 1
    if idx < 0:
        idx = 0
    elif idx >= len(sentences):
        idx = len(sentences) - 1
    s = sentences[idx]
    return s.page_number, s.index + 1


def _is_char_boundary(text: str, index: int) -> bool:
    if index <= 0 or index >= len(text):
        return True
    try:
        text[index - 1 : index + 1]
        return True
    except Exception:
        return False


def iter_text_chunks_with_location(
    text: str,
    part_max_chars: int,
    *,
    sentences: Optional[Sequence[Sentence]] = None,
    sections: Optional[Sequence[Section]] = None,
    pages: Optional[Sequence[Page]] = None,
    page_markers: bool = False,
) -> Iterator[Tuple[str, Optional[int], Optional[int], int]]:
    """Yield (snippet, page_number, sentence_number, start_offset) per transmission part.

    ``sentences`` — knovas-extract ``Sentence`` objects (char offsets into
    ``content.text``). When provided, each chunk's location uses binary search
    on ``Sentence.char_start`` for ``page_number`` and ``index + 1`` as
    ``sentence_number``.

    ``sections`` / ``pages`` — when provided, chunk boundaries prefer section
    and page breaks; section headings are injected into snippets at section
    starts.

    ``page_markers`` — write form feeds before every text-page start strictly
    inside a part (GI-INGEST-17, see ``knovas_transmit.page_markers``). The
    markers are applied after the heading prefix, their room is reserved from
    the part budget, and ``page_number`` / ``sentence_number`` /
    ``start_offset`` are the same as without them: they describe the unmarked
    text.
    """
    if part_max_chars < 1:
        raise ValueError("part_max_chars must be >= 1")
    if not text:
        yield "", None, None, 0
        return

    starts = [s.char_start for s in sentences] if sentences else []
    page_starts: list[PageStart] = text_page_starts(pages, text) if (page_markers and pages) else []

    start = 0
    length = len(text)
    while start < length:
        prefix = section_prefix_at_offset(sections, text, start)
        budget = max(1, part_max_chars - len(prefix)) if prefix else part_max_chars
        end = min(start + budget, length)
        if page_starts:
            end = _fit_markers_in_budget(page_starts, start, end, budget)
        if end < length:
            end = adjust_chunk_end(text, start, end, sections=sections, pages=pages)
            while end > start and not _is_char_boundary(text, end):
                end -= 1
            if end == start:
                end = min(start + budget, length)
                if page_starts:
                    end = _fit_markers_in_budget(page_starts, start, end, budget)
        if sentences:
            page_number, sentence_number = _location_for_offset(starts, sentences, start)
        else:
            page_number, sentence_number = None, None
        snippet, shift = apply_section_heading(text[start:end], prefix)
        if page_starts:
            markers = [(offset - start, count) for offset, count in markers_inside(page_starts, start, end)]
            snippet = apply_page_markers(snippet, markers, shift)
        yield snippet, page_number, sentence_number, start
        start = end


def build_transmission_parts(
    text: str,
    part_max_chars: int,
    *,
    sentences: Optional[Sequence[Sentence]] = None,
    sections: Optional[Sequence[Section]] = None,
    pages: Optional[Sequence[Page]] = None,
    tables: Optional[List[Dict[str, Any]]] = None,
    page_markers: bool = False,
) -> List[Dict[str, Any]]:
    """Build part dicts with snippet, location, and optional tables for upload."""
    parts: List[Dict[str, Any]] = []
    for snippet, page_number, sentence_number, _start in iter_text_chunks_with_location(
        text,
        part_max_chars,
        sentences=sentences,
        sections=sections,
        pages=pages,
        page_markers=page_markers,
    ):
        part: Dict[str, Any] = {"snippet": snippet}
        if page_number is not None and page_number >= 1:
            part["page_number"] = int(page_number)
        if sentence_number is not None and sentence_number >= 1:
            part["sentence_number"] = int(sentence_number)
        parts.append(part)

    if tables:
        assign_tables_to_parts(parts, tables, text=text, part_max_chars=part_max_chars)
    return parts
