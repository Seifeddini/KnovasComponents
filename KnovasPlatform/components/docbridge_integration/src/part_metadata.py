"""Derive Knovas transmit_document_part location fields from ingested text."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_PAGE_HEADING = re.compile(r"^##\s+Page\s+(\d+)\s*$", re.MULTILINE | re.IGNORECASE)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])\s+")


def page_number_at_offset(text: str, offset: int) -> Optional[int]:
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
    if not text or not text.strip():
        return None
    if offset < 0:
        offset = 0
    if offset >= len(text):
        offset = max(0, len(text) - 1)
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


def enrich_transmit_parts_with_location(
    parts: List[Dict[str, Any]],
    *,
    full_text: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fill missing page_number / sentence_number on transmission parts."""
    if not parts:
        return parts
    if full_text is None:
        full_text = "".join(str(p.get("snippet") or "") for p in parts)
    out: List[Dict[str, Any]] = []
    offset = 0
    for part in parts:
        row = dict(part)
        snippet = str(row.get("snippet") or "")
        page, sentence = location_for_snippet(full_text, offset)
        if row.get("page_number") is None and page is not None:
            row["page_number"] = page
        if row.get("sentence_number") is None and sentence is not None:
            row["sentence_number"] = sentence
        offset += len(snippet)
        out.append(row)
    return out
