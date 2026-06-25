"""Derive Knovas transmit_document_part location fields from ingested text."""
from __future__ import annotations

import re
from typing import List, Optional

_PAGE_HEADING = re.compile(r"^##\s+Page\s+(\d+)\s*$", re.MULTILINE | re.IGNORECASE)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])\s+")


def page_number_at_offset(text: str, offset: int) -> Optional[int]:
    """Last PDF page heading (## Page N) at or before byte offset."""
    if offset < 0:
        return None
    last: Optional[int] = None
    for match in _PAGE_HEADING.finditer(text):
        if match.start() > offset:
            break
        last = int(match.group(1))
    return last


def _sentence_start_offsets(text: str) -> List[int]:
    if not text:
        return [0]
    starts = [0]
    for match in _SENTENCE_BOUNDARY.finditer(text):
        nxt = match.end()
        if nxt < len(text):
            starts.append(nxt)
    return starts


def sentence_number_at_offset(text: str, offset: int) -> Optional[int]:
    """1-based sentence index at offset (simple .!? split)."""
    if not text or not text.strip():
        return None
    if offset < 0:
        offset = 0
    if offset >= len(text):
        offset = len(text) - 1
    starts = _sentence_start_offsets(text)
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
        if start <= offset < end:
            return idx + 1
    return len(starts) if starts else None


def location_for_snippet(full_text: str, snippet_start: int) -> tuple[Optional[int], Optional[int]]:
    return (
        page_number_at_offset(full_text, snippet_start),
        sentence_number_at_offset(full_text, snippet_start),
    )
