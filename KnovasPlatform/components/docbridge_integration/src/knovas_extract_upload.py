"""Build secured transmit parts from raw document bytes via knovas-extract."""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List

from context_store import write_context_sidecar
from knovas_transmit.chunking import build_transmission_parts
from knovas_transmit.table_payload import map_extractor_tables

logger = logging.getLogger(__name__)

_EXT_TO_MIME = {
    ".txt": "text/plain",
    ".md": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".eml": "message/rfc822",
    ".msg": "application/vnd.ms-outlook",
}

_PART_MAX_CHARS = 50_000


def _normalize_ext(ext: str) -> str:
    return (ext or "").strip().lower().lstrip(".")


def _context_store_dir() -> str:
    import os

    return (os.environ.get("SEARCH_CONTEXT_STORE_PATH") or "").strip()


def parts_from_base64(
    content_base64: str,
    ext: str,
    *,
    part_max_chars: int = _PART_MAX_CHARS,
    pointer: str = "",
    path: str = "",
    write_sidecar: bool = True,
) -> List[Dict[str, Any]]:
    """
    Decode base64 document bytes and return Knovas transmit part dicts using the
    full knovas-extract surface: sentences (page/sentence), sections (headings),
    pages (boundaries), and structured tables.
    """
    normalized = _normalize_ext(ext)
    dotted = f".{normalized}" if normalized else ""
    mime = _EXT_TO_MIME.get(dotted)
    if mime is None:
        return []

    try:
        raw = base64.b64decode(content_base64, validate=False)
    except Exception as exc:
        logger.warning("knovas-extract upload: base64 decode failed: %s", exc)
        return []

    try:
        from knovas_extract import extract
    except ImportError:
        logger.warning("knovas-extract not installed; cannot build transmission parts")
        return []

    try:
        result = extract(raw, mime=mime, emit_sentences=True, emit_markdown=True)
    except Exception as exc:
        logger.warning("knovas-extract failed for .%s: %s", normalized, exc)
        return []

    content = result.content
    text = (content.text or "").strip()
    if not text:
        return []

    tables_raw = getattr(content, "tables", None)
    tables = (
        map_extractor_tables(tables_raw, default_hint_prefix=normalized) if tables_raw else None
    )

    if write_sidecar and pointer:
        write_context_sidecar(
            _context_store_dir() or None,
            pointer,
            path or pointer,
            content.text,
            content.sentences,
        )

    return build_transmission_parts(
        content.text,
        part_max_chars,
        sentences=content.sentences,
        sections=content.sections,
        pages=content.pages,
        tables=tables,
    )
