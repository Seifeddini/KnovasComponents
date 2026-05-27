"""Read local files and convert to Markdown for Semantix ingest."""
from __future__ import annotations

import logging
from pathlib import Path

from sync.converters.docx import docx_bytes_to_markdown
from sync.converters.email import eml_bytes_to_markdown, msg_bytes_to_markdown
from sync.converters.pdf import pdf_bytes_to_markdown

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


class ConversionError(Exception):
    """Failed to extract text from a document."""

    def __init__(self, message: str, *, extension: str = "") -> None:
        super().__init__(message)
        self.extension = extension


def is_syncable_extension(suffix: str) -> bool:
    return suffix.lower() in SYNCABLE_EXTENSIONS


def _read_plain_text(file_path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return file_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ConversionError(
        f"not valid UTF-8: {file_path.name}",
        extension=file_path.suffix.lower(),
    )


def bytes_to_markdown(raw_bytes: bytes, suffix: str) -> str:
    ext = suffix.lower()
    if ext in PLAIN_TEXT_EXTENSIONS:
        for encoding in ("utf-8-sig", "utf-8"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ConversionError(f"not valid UTF-8 for {ext}", extension=ext)

    if ext == ".docx":
        text = docx_bytes_to_markdown(raw_bytes)
    elif ext == ".pdf":
        text = pdf_bytes_to_markdown(raw_bytes)
    elif ext == ".eml":
        text = eml_bytes_to_markdown(raw_bytes)
    elif ext == ".msg":
        text = msg_bytes_to_markdown(raw_bytes)
    else:
        raise ConversionError(f"unsupported extension: {ext}", extension=ext)

    if not text.strip():
        raise ConversionError(f"no extractable text from {ext} file", extension=ext)
    return text


def file_to_markdown(file_path: Path) -> str:
    """Read file and return Markdown/plain text suitable for chunking."""
    ext = file_path.suffix.lower()
    if ext not in SYNCABLE_EXTENSIONS:
        raise ConversionError(
            f"unsupported extension: {ext}",
            extension=ext,
        )

    if ext in PLAIN_TEXT_EXTENSIONS:
        return _read_plain_text(file_path)

    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise ConversionError(str(exc), extension=ext) from exc

    try:
        return bytes_to_markdown(raw, ext)
    except ImportError as exc:
        raise ConversionError(str(exc), extension=ext) from exc
