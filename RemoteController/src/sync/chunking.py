"""Split text into chunks without breaking UTF-8 codepoints."""
from __future__ import annotations


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


def _is_char_boundary(text: str, index: int) -> bool:
    if index <= 0 or index >= len(text):
        return True
    try:
        text[index - 1 : index + 1]
        return True
    except Exception:
        return False
