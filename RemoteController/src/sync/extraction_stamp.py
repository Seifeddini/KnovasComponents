"""Extraction stamp and uploaded-text hash (spec L6).

Every upload the Knovas Connector makes is recorded with two values:

* the **extraction stamp** -- 16 hex characters of sha256 over the settings
  that shape a document's text: the installed knovas-extract version, the
  PDF and DOCX text modes, the OCR engine, the DPI setting, the sentence
  gate and ``EXTRACTION_SCHEMA``. A row whose stamp is not
  ``current_extraction_stamp()`` (or that has none) was produced by an
  older extraction;
* the **uploaded-text hash** (``upload_text_sha256``) -- sha256 over exactly
  what the upload carried to the index. A re-extraction whose hash equals
  the stored one is not sent again: every upload is billed.

No I/O besides reading the settings and the installed version, and no
logging. Stamps and hashes may be logged and reported; what goes into the
hash (text, field values) never leaves this module.
"""
from __future__ import annotations

import functools
import hashlib
import json
from importlib import metadata
from typing import Any, Mapping, Optional, Sequence

from sync.document_text import (
    docx_text_mode,
    ocr_dpi,
    ocr_engine,
    pdf_text_mode,
    sentence_emit_max_bytes,
)

#: Bump when the Connector's own processing changes what an upload carries
#: for the same extractor output (chunking, page markers, part numbering):
#: every document then counts as produced by an older extraction.
EXTRACTION_SCHEMA = 1

#: Hex characters of the stamp.
STAMP_LENGTH = 16


@functools.lru_cache(maxsize=1)
def _knovas_extract_version() -> Optional[str]:
    """The installed knovas-extract version; None when it is not installed.
    Read once per process: another version means another image."""
    try:
        return metadata.version("knovas-extract")
    except metadata.PackageNotFoundError:
        return None


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def stamp_inputs() -> dict[str, Any]:
    """What the stamp covers: versions and settings, never document data."""
    return {
        "knovas_extract": _knovas_extract_version(),
        "pdf_text_mode": pdf_text_mode(),
        "docx_text_mode": docx_text_mode(),
        "ocr_engine": ocr_engine(),
        "ocr_dpi": ocr_dpi(),
        "sentence_emit_max_bytes": sentence_emit_max_bytes(),
        "schema": EXTRACTION_SCHEMA,
    }


def current_extraction_stamp() -> str:
    """16 hex characters of sha256 over the canonical JSON of ``stamp_inputs()``."""
    return hashlib.sha256(_canonical(stamp_inputs())).hexdigest()[:STAMP_LENGTH]


def fields_values_digest(values: Optional[Mapping[str, Any]]) -> Optional[str]:
    """sha256 of the ``fields`` object an init carries; None for an init
    without one.

    Not the stored ``fields_digest`` (``doc_fields_payload.config_digest``):
    that one covers the configuration and by design leaves out the values
    the extractor supplies (author, e-mail date, language), which a newer
    extractor may read differently.
    """
    if values is None:
        return None
    return hashlib.sha256(_canonical(dict(values))).hexdigest()


def _wire_number(value: Any) -> Optional[int]:
    """A part's page or sentence number as the transmission sends it
    (``knovas_uploader._transmit_part_body``: numbers >= 1 only)."""
    if value is None:
        return None
    number = int(value)
    return number if number >= 1 else None


def upload_text_sha256(
    parts: Sequence[Mapping[str, Any]],
    fields_digest: Optional[str],
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """sha256 over what one upload carries to the index.

    Canonical JSON (sorted keys, ``ensure_ascii=False``) of every part's
    snippet -- page markers included, as transmitted -- with its page and
    sentence number, in order; the fields digest (``fields_values_digest``);
    and the init's title and description, which the extractor supplies too.
    ``tables`` payloads are left out: the server drops them at its part
    buffer, so they never reach the index.
    """
    canonical = {
        "parts": [
            {
                "snippet": str(part.get("snippet") or ""),
                "page_number": _wire_number(part.get("page_number")),
                "sentence_number": _wire_number(part.get("sentence_number")),
            }
            for part in parts
        ],
        "fields": fields_digest,
        "title": title,
        "description": description,
    }
    return hashlib.sha256(_canonical(canonical)).hexdigest()
