"""Section/page boundary helpers for knovas-extract aligned chunking."""
from __future__ import annotations

from typing import Optional, Sequence

from knovas_extract.result import Page, Section


def offset_for_line(text: str, line_1based: int) -> int:
    if line_1based <= 1:
        return 0
    pos = 0
    line = 1
    while pos < len(text) and line < line_1based:
        nl = text.find("\n", pos)
        if nl < 0:
            return len(text)
        pos = nl + 1
        line += 1
    return pos


def line_number_at_offset(text: str, offset: int) -> int:
    if offset <= 0:
        return 1
    if offset > len(text):
        offset = len(text)
    return text.count("\n", 0, offset) + 1


def section_prefix_at_offset(
    sections: Optional[Sequence[Section]],
    text: str,
    offset: int,
) -> str:
    if not sections:
        return ""
    line = line_number_at_offset(text, offset)
    for sec in sections:
        if sec.line_start is None or sec.line_start != line:
            continue
        heading = str(sec.heading or "").strip()
        if not heading:
            return ""
        level = max(1, min(6, int(sec.level or 1)))
        return f"{'#' * level} {heading}\n\n"
    return ""


def adjust_chunk_end(
    text: str,
    start: int,
    end: int,
    *,
    sections: Optional[Sequence[Section]] = None,
    pages: Optional[Sequence[Page]] = None,
    min_chunk_chars: int = 200,
) -> int:
    if end >= len(text):
        return end
    floor = min(end, start + min_chunk_chars)
    candidates: list[int] = []

    if pages:
        for page in pages:
            if page.line_end is None:
                continue
            boundary = offset_for_line(text, page.line_end + 1)
            if floor < boundary < end:
                candidates.append(boundary)

    if sections:
        for sec in sections:
            if sec.line_start is None or sec.line_start <= 1:
                continue
            boundary = offset_for_line(text, sec.line_start)
            if floor < boundary < end:
                candidates.append(boundary)

    if not candidates:
        return end
    return max(candidates)
