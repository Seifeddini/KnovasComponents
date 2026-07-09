"""Split text into chunks without breaking UTF-8 codepoints."""
from __future__ import annotations

import bisect
from pathlib import Path
from typing import Iterator, Optional, Sequence, Tuple

from knovas_extract.result import Sentence


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
) -> Iterator[Tuple[str, Optional[int], Optional[int]]]:
    """Yield (snippet, page_number, sentence_number) for each transmission part.

    `sentences` — the `content.sentences` list from `knovas-extract`. When
    provided, each chunk's location is looked up via binary search on
    `Sentence.char_start`, using `Sentence.page_number` (populated for PDFs)
    and `Sentence.index + 1` for the sentence number. When None, yields
    `(chunk, None, None)`.
    """
    if part_max_chars < 1:
        raise ValueError("part_max_chars must be >= 1")
    if not text:
        yield "", None, None
        return

    starts = [s.char_start for s in sentences] if sentences else []

    start = 0
    length = len(text)
    while start < length:
        end = min(start + part_max_chars, length)
        if end < length:
            while end > start and not _is_char_boundary(text, end):
                end -= 1
            if end == start:
                end = min(start + part_max_chars, length)
        if sentences:
            page_number, sentence_number = _location_for_offset(starts, sentences, start)
        else:
            page_number, sentence_number = None, None
        yield text[start:end], page_number, sentence_number
        start = end


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
