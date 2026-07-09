"""Adapter over `knovas-extract` for the RemoteController sync pipeline.

Wraps `knovas_extract.extract(..., emit_sentences=True)` and returns text +
per-sentence citations (with page back-pointers on PDFs). The uploader
threads the sentence list into the chunker so every transmission part
carries an accurate `page_number` / `sentence_number`.

Errors from `knovas-extract` are re-raised as `ConversionError` with
message substrings that `is_unconvertible_error()` recognizes, so
incremental-sync retry classification is unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from knovas_extract import (
    CorruptDocumentError,
    EncryptedDocumentError,
    ExtractError,
    ResourceExhaustedError,
    UnsupportedFormatError,
    extract,
)
from knovas_extract.result import Sentence

logger = logging.getLogger(__name__)

SYNCABLE_EXTENSIONS = frozenset({".md", ".txt", ".docx", ".pdf", ".eml", ".msg"})

PLAIN_TEXT_EXTENSIONS = frozenset({".md", ".txt"})

BINARY_CONVERT_EXTENSIONS = frozenset({".docx", ".pdf", ".eml", ".msg"})

DEFAULT_INCLUDE_GLOBS = [
    "**/*.md",
    "**/*.txt",
    "**/*.docx",
    "**/*.pdf",
    "**/*.eml",
    "**/*.msg",
    "*.md",
    "*.txt",
    "*.docx",
    "*.pdf",
    "*.eml",
    "*.msg",
]

_EXT_TO_MIME = {
    ".txt": "text/plain",
    # Route .md through the text extractor: preserves the file bytes verbatim
    # (no YAML-frontmatter stripping) and avoids pulling in the [md] extra.
    ".md": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".eml": "message/rfc822",
    ".msg": "application/vnd.ms-outlook",
}


class ConversionError(Exception):
    """Failed to extract text from a document."""

    def __init__(self, message: str, *, extension: str = "") -> None:
        super().__init__(message)
        self.extension = extension


@dataclass(frozen=True)
class ExtractedDocument:
    """Text + sentence citations + title for a single document.

    `sentences` is None when `content.sentences` was not populated (e.g. an
    older `knovas-extract` without `[sentences]`). Callers must tolerate
    `None` and skip per-chunk citation lookup.

    `title` is the extractor-supplied document title when present (email
    subject for EML/MSG, `/Title` metadata for PDF, core.xml title for
    DOCX). None when no title was extracted; callers should fall back to
    the filename.
    """

    text: str
    sentences: Optional[list[Sentence]]
    title: Optional[str] = None


def is_syncable_extension(suffix: str) -> bool:
    return suffix.lower() in SYNCABLE_EXTENSIONS


def is_unconvertible_error(error: str | None) -> bool:
    """True when a file cannot be converted and should not block incremental sync."""
    if not error:
        return False
    lowered = error.lower()
    if lowered.startswith("init failed:") or (lowered.startswith("part ") and " failed:" in lowered):
        return False
    return (
        "no extractable text" in lowered
        or "not valid utf-8" in lowered
        or lowered.startswith("unsupported extension")
        or lowered.startswith("corrupt ")
        or lowered.startswith("encrypted ")
        or lowered.startswith("resource limit exceeded")
        or "not a zip file" in lowered
        or "not a valid zip container" in lowered
        or "bad zipfile" in lowered
        or "bad magic number" in lowered
    )


def _extract_bytes(raw: bytes, ext: str) -> ExtractedDocument:
    mime = _EXT_TO_MIME.get(ext)
    if mime is None:
        raise ConversionError(f"unsupported extension: {ext}", extension=ext)

    try:
        result = extract(raw, mime=mime, emit_sentences=True)
    except UnsupportedFormatError as exc:
        raise ConversionError(f"unsupported extension: {ext}", extension=ext) from exc
    except CorruptDocumentError as exc:
        raise ConversionError(f"corrupt {ext}: {exc}", extension=ext) from exc
    except EncryptedDocumentError as exc:
        raise ConversionError(f"encrypted {ext}: {exc}", extension=ext) from exc
    except ResourceExhaustedError as exc:
        raise ConversionError(
            f"resource limit exceeded: {getattr(exc, 'what', 'unknown')}",
            extension=ext,
        ) from exc
    except ExtractError as exc:
        raise ConversionError(str(exc), extension=ext) from exc

    text = result.content.text
    if not text.strip():
        raise ConversionError(f"no extractable text from {ext} file", extension=ext)

    return ExtractedDocument(
        text=text,
        sentences=result.content.sentences,
        title=result.metadata.title,
    )


def extract_document(file_path: Path) -> ExtractedDocument:
    """Extract text + sentence citations from a local file.

    Returns an `ExtractedDocument`. Raises `ConversionError` on any recoverable
    per-file failure (unsupported format, corrupt bytes, encrypted, resource
    cap exceeded, empty output). Lets `DependencyMissingError` bubble — that
    is a deploy misconfiguration, not a per-file issue.
    """
    ext = file_path.suffix.lower()
    if ext not in SYNCABLE_EXTENSIONS:
        raise ConversionError(f"unsupported extension: {ext}", extension=ext)

    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise ConversionError(str(exc), extension=ext) from exc

    return _extract_bytes(raw, ext)


def bytes_to_markdown(raw_bytes: bytes, suffix: str) -> str:
    """Backwards-compat: return text only. Prefer `extract_document` for new code."""
    return _extract_bytes(raw_bytes, suffix.lower()).text


def file_to_markdown(file_path: Path) -> str:
    """Backwards-compat: return text only. Prefer `extract_document` for new code."""
    return extract_document(file_path).text
