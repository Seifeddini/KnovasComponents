"""Split text into chunks without breaking UTF-8 codepoints."""
from __future__ import annotations

import bisect
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from knovas_extract.result import Page, Section, Sentence

from sync.section_pages import adjust_chunk_end, section_prefix_at_offset
from sync.table_payload import assign_tables_to_parts


def iter_text_chunks(text: str, part_max_chars: int) -> Iterator[str]:
    """Yield chunks from an in-memory string without building a full list."""
    if part_max_chars < 1:
        raise ValueError("part_max_chars must be >= 1")
    if not text:
        yield ""
        return
    start = 0
    length = len(text)
    while start < length:
        end = min(start + part_max_chars, length)
        if end < length:
            while end > start and not _is_char_boundary(text, end):
                end -= 1
            if end == start:
                end = min(start + part_max_chars, length)
        yield text[start:end]
        start = end


def _location_for_offset(
    starts: Sequence[int],
    sentences: Sequence[Sentence],
    offset: int,
) -> Tuple[Optional[int], Optional[int]]:
    """Return (page_number, sentence_number) for a chunk-start offset.

    Uses binary search over the ascending `char_start` array. The sentence
    containing `offset` is `sentences[i-1]` where `i = bisect_right(starts, offset)`;
    clamps to first/last sentence for out-of-range offsets.
    """
    if not sentences:
        return None, None
    idx = bisect.bisect_right(starts, offset) - 1
    if idx < 0:
        idx = 0
    elif idx >= len(sentences):
        idx = len(sentences) - 1
    s = sentences[idx]
    return s.page_number, s.index + 1


def iter_text_chunks_with_location(
    text: str,
    part_max_chars: int,
    *,
    sentences: Optional[Sequence[Sentence]] = None,
    sections: Optional[Sequence[Section]] = None,
    pages: Optional[Sequence[Page]] = None,
) -> Iterator[Tuple[str, Optional[int], Optional[int], int]]:
    """Yield (snippet, page_number, sentence_number, start_offset) per transmission part.

    `sentences` — `content.sentences` from knovas-extract (char offsets refer to
    `content.text`). When provided, each chunk's location uses binary search on
    `Sentence.char_start` for `page_number` and `index + 1` as `sentence_number`.

  `sections` / `pages` — when provided, chunk boundaries prefer section and page
    breaks; section headings are injected into snippets at section starts.
    """
    if part_max_chars < 1:
        raise ValueError("part_max_chars must be >= 1")
    if not text:
        yield "", None, None, 0
        return

    starts = [s.char_start for s in sentences] if sentences else []

    start = 0
    length = len(text)
    while start < length:
        prefix = section_prefix_at_offset(sections, text, start)
        budget = max(1, part_max_chars - len(prefix)) if prefix else part_max_chars
        end = min(start + budget, length)
        if end < length:
            end = adjust_chunk_end(
                text,
                start,
                end,
                sections=sections,
                pages=pages,
            )
            while end > start and not _is_char_boundary(text, end):
                end -= 1
            if end == start:
                end = min(start + budget, length)
        if sentences:
            page_number, sentence_number = _location_for_offset(starts, sentences, start)
        else:
            page_number, sentence_number = None, None
        snippet = text[start:end]
        if prefix and not snippet.startswith(prefix):
            snippet = prefix + snippet
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
) -> List[Dict[str, Any]]:
    """Build part dicts with snippet, location, and optional tables for upload."""
    parts: List[Dict[str, Any]] = []
    for snippet, page_number, sentence_number, _start in iter_text_chunks_with_location(
        text,
        part_max_chars,
        sentences=sentences,
        sections=sections,
        pages=pages,
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


def chunk_text(text: str, part_max_chars: int) -> list[str]:
    if part_max_chars < 1:
        raise ValueError("part_max_chars must be >= 1")
    if not text:
        return [""]
    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + part_max_chars, length)
        if end < length:
            while end > start and not _is_char_boundary(text, end):
                end -= 1
            if end == start:
                end = min(start + part_max_chars, length)
        chunks.append(text[start:end])
        start = end
    return chunks


def _take_first_chunk(text: str, part_max_chars: int) -> tuple[str, str]:
    if not text:
        return "", ""
    if len(text) <= part_max_chars:
        return text, ""
    end = part_max_chars
    while end > 0 and not _is_char_boundary(text, end):
        end -= 1
    if end == 0:
        end = part_max_chars
    return text[:end], text[end:]


def iter_file_text_chunks(file_path: Path, part_max_chars: int) -> Iterator[str]:
    """Stream UTF-8 text file in bounded-size chunks (at most one part buffer in memory)."""
    if part_max_chars < 1:
        raise ValueError("part_max_chars must be >= 1")
    with open(file_path, encoding="utf-8") as f:
        buf = ""
        any_data = False
        while True:
            block = f.read(part_max_chars)
            if not block:
                break
            any_data = True
            buf += block
            while len(buf) > part_max_chars:
                chunk, buf = _take_first_chunk(buf, part_max_chars)
                yield chunk
        if not any_data:
            yield ""
            return
        while buf:
            chunk, buf = _take_first_chunk(buf, part_max_chars)
            if not chunk:
                break
            yield chunk


def count_file_text_parts(file_path: Path, part_max_chars: int) -> int:
    return sum(1 for _ in iter_file_text_chunks(file_path, part_max_chars))


def _is_char_boundary(text: str, index: int) -> bool:
    if index <= 0 or index >= len(text):
        return True
    try:
        text[index - 1 : index + 1]
        return True
    except Exception:
        return False
