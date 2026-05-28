"""Split text into chunks without breaking UTF-8 codepoints."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator


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
