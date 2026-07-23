"""Build transmission parts from knovas-extract content."""
from __future__ import annotations

import bisect
from typing import Any, Dict, List, Optional, Sequence

from knovas_extract.result import Page, Section, Sentence

from knovas_transmit.section_pages import adjust_chunk_end, section_prefix_at_offset
from knovas_transmit.table_payload import assign_tables_to_parts


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


def build_transmission_parts(
    text: str,
    part_max_chars: int,
    *,
    sentences: Optional[Sequence[Sentence]] = None,
    sections: Optional[Sequence[Section]] = None,
    pages: Optional[Sequence[Page]] = None,
    tables: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if part_max_chars < 1:
        raise ValueError("part_max_chars must be >= 1")
    if not text:
        return [{"snippet": ""}]

    starts = [s.char_start for s in sentences] if sentences else []
    parts: List[Dict[str, Any]] = []
    start = 0
    length = len(text)

    while start < length:
        prefix = section_prefix_at_offset(sections, text, start)
        budget = max(1, part_max_chars - len(prefix)) if prefix else part_max_chars
        end = min(start + budget, length)
        if end < length:
            end = adjust_chunk_end(
                text, start, end, sections=sections, pages=pages
            )
            while end > start and not _is_char_boundary(text, end):
                end -= 1
            if end == start:
                end = min(start + part_max_chars, length)

        page_number, sentence_number = (
            _location_for_offset(starts, sentences, start) if sentences else (None, None)
        )
        snippet = text[start:end]
        if prefix and not snippet.startswith(prefix):
            snippet = prefix + snippet

        part: Dict[str, Any] = {"snippet": snippet}
        if page_number is not None and page_number >= 1:
            part["page_number"] = int(page_number)
        if sentence_number is not None and sentence_number >= 1:
            part["sentence_number"] = int(sentence_number)
        parts.append(part)
        start = end

    if tables:
        assign_tables_to_parts(parts, tables, text=text, part_max_chars=part_max_chars)
    return parts
