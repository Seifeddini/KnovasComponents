"""Section/page boundary helpers for knovas-extract aligned chunking.

Mirror of ``KnovasConnector/src/sync/section_pages.py``: the Platform's
admin upload builds the same wire format as the RC's sync pipeline.
"""
from __future__ import annotations

from typing import Optional, Sequence

from knovas_extract.result import Page, Section

#: The server recognises headings via ``^(#{1,4})\s+(.+)`` only; a deeper
#: level would reach the embedder as a literal "##### " prefix.
MAX_HEADING_LEVEL = 4


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


def heading_line(heading: str, level: int) -> str:
    """The markdown heading line the server parses: ``#`` × level (capped
    at 4) + space + heading text."""
    capped = max(1, min(MAX_HEADING_LEVEL, int(level or 1)))
    return f"{'#' * capped} {heading}"


def section_heading_at_offset(
    sections: Optional[Sequence[Section]],
    text: str,
    offset: int,
) -> Optional[tuple[str, int]]:
    """``(heading, level)`` when a chunk starts on a section heading line."""
    if not sections:
        return None
    line = line_number_at_offset(text, offset)
    for sec in sections:
        if sec.line_start is None or sec.line_start != line:
            continue
        heading = str(sec.heading or "").strip()
        if not heading:
            return None
        return heading, max(1, min(MAX_HEADING_LEVEL, int(sec.level or 1)))
    return None


def section_prefix_at_offset(
    sections: Optional[Sequence[Section]],
    text: str,
    offset: int,
) -> str:
    """Markdown heading prefix when a chunk starts on a section heading line."""
    found = section_heading_at_offset(sections, text, offset)
    if found is None:
        return ""
    heading, level = found
    return f"{heading_line(heading, level)}\n\n"


def apply_section_heading(raw: str, prefix: str) -> tuple[str, int]:
    """Put the section heading in front of a part that starts on its line.

    Returns the snippet and the number of characters by which everything
    after the first line moved (the chunker shifts page markers by it).

    DOCX (and any extractor whose ``content.text`` carries the heading as a
    plain first line) used to yield ``"# Intro\\n\\nIntro"``: the heading
    prepended AND repeated. When the part's first line is the heading text —
    bare or already marked up at another level — that line is REPLACED by
    the canonical heading line instead; a part that already starts with the
    full prefix is left alone; anything else gets the prefix prepended.
    """
    if not prefix:
        return raw, 0
    if raw.startswith(prefix):
        return raw, 0
    heading_text = prefix.rstrip("\n")
    heading = heading_text.lstrip("#").strip()
    newline = raw.find("\n")
    first_line = raw if newline < 0 else raw[:newline]
    if first_line.strip().lstrip("#").strip() == heading:
        replaced = heading_text + raw[len(first_line):]
        return replaced, len(heading_text) - len(first_line)
    return prefix + raw, len(prefix)


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
