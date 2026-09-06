"""Adapter over `knovas-extract` for the RemoteController sync pipeline.

Wraps `knovas_extract.extract(...)` and returns text + per-sentence
citations (with page back-pointers on PDFs). The uploader threads the
sentence list into the chunker so every transmission part carries an
accurate `page_number` / `sentence_number`.

Image-only PDFs (no text layer) are OCR'd when `RC_PDF_OCR_ENABLED` is true
(default) and Tesseract is installed. Language packs are selected via
`RC_TESSERACT_LANG` (default `deu+eng`).

Sentence emission is skipped for inputs larger than
`RC_SENTENCE_EMIT_MAX_BYTES` (default 2 MiB). `split_sentences` degrades
badly on large, weakly-punctuated text — tariff tables and similar
dumps — where it can occupy the single sync worker for many minutes per
file and stall ingestion. Text extraction and upload are unaffected;
only sentence-level citations and context previews are dropped.

Errors from `knovas-extract` are re-raised as `ConversionError` with
message substrings that `is_unconvertible_error()` recognizes, so
incremental-sync retry classification is unchanged.
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import os
import queue
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from knovas_extract import (
    CorruptDocumentError,
    DependencyMissingError,
    EncryptedDocumentError,
    ExtractError,
    ResourceExhaustedError,
    UnsupportedFormatError,
    extract,
)
from knovas_extract.result import Page, Section, Sentence

from sync.extract_content import description_from_metadata, payload_from_extraction_result

logger_ocr_warned = False


def extract_accepts_ocr() -> bool:
    """Whether the installed knovas-extract takes the OCR keywords.

    OCR arrived after 0.2. Passing the keywords to an older extractor is a
    TypeError on every PDF, so the pin used to demand a version that was never
    published to PyPI and no CI job could install. Asking the function what it
    accepts costs one introspection and lets the same source run against both.

    A version that cannot be introspected is treated as accepting them: dropping
    OCR silently would ingest scanned PDFs as empty documents, and a loud
    TypeError is the better failure.
    """
    import inspect

    try:
        return "use_ocr" in inspect.signature(extract).parameters
    except (TypeError, ValueError):
        return True

logger = logging.getLogger(__name__)

SYNCABLE_EXTENSIONS = frozenset({".md", ".txt", ".docx", ".pdf", ".eml", ".msg"})

# Inputs above this size skip sentence emission. Override with
# RC_SENTENCE_EMIT_MAX_BYTES; 0 disables sentence emission entirely.
DEFAULT_SENTENCE_EMIT_MAX_BYTES = 2 * 1024 * 1024

# Wall-clock ceiling for one document's extraction. Override with
# RC_EXTRACT_TIMEOUT_SECONDS; 0 extracts in-process with no ceiling.
DEFAULT_EXTRACT_TIMEOUT_SECONDS = 300

# PDF OCR via knovas-extract + system Tesseract (see RC_PDF_OCR_ENABLED).
DEFAULT_TESSERACT_LANG = "deu+eng"

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

    `description` is optional abstract text when the extractor provides it.

    `tables` holds API-ready structured tables from `content.tables`.

    `sections` / `pages` mirror knovas-extract structure for section headings and
    page-aware chunk boundaries during upload.
    """

    text: str
    sentences: Optional[list[Sentence]]
    title: Optional[str] = None
    description: Optional[str] = None
    tables: Optional[list[dict[str, Any]]] = None
    sections: Optional[list[Section]] = None
    pages: Optional[list[Page]] = None


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
        or lowered.startswith("valueerror:")
        or "invalid literal for int" in lowered
        or "not a zip file" in lowered
        or "not a valid zip container" in lowered
        or "bad zipfile" in lowered
        or "bad magic number" in lowered
    )


def sentence_emit_max_bytes() -> int:
    """Size ceiling for sentence emission (see module docstring)."""
    raw = (os.environ.get("RC_SENTENCE_EMIT_MAX_BYTES") or "").strip()
    if not raw:
        return DEFAULT_SENTENCE_EMIT_MAX_BYTES
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning(
            "Invalid RC_SENTENCE_EMIT_MAX_BYTES=%r; using default %d",
            raw,
            DEFAULT_SENTENCE_EMIT_MAX_BYTES,
        )
        return DEFAULT_SENTENCE_EMIT_MAX_BYTES


def extract_timeout_seconds() -> int:
    """Wall-clock ceiling for one document's extraction (see module docstring)."""
    raw = (os.environ.get("RC_EXTRACT_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_EXTRACT_TIMEOUT_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning(
            "Invalid RC_EXTRACT_TIMEOUT_SECONDS=%r; using default %d",
            raw,
            DEFAULT_EXTRACT_TIMEOUT_SECONDS,
        )
        return DEFAULT_EXTRACT_TIMEOUT_SECONDS


def pdf_ocr_enabled() -> bool | str:
    """Whether knovas-extract should OCR image-only PDFs (`auto` when enabled)."""
    raw = (os.environ.get("RC_PDF_OCR_ENABLED") or "").strip().lower()
    if raw in ("", "1", "true", "yes", "on"):
        return "auto"
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning("Invalid RC_PDF_OCR_ENABLED=%r; enabling OCR (auto)", raw)
    return "auto"


def tesseract_language() -> str:
    raw = (os.environ.get("RC_TESSERACT_LANG") or "").strip()
    return raw or DEFAULT_TESSERACT_LANG


def _is_parser_value_error(message: str) -> bool:
    """True when a binary parser raised ValueError on malformed structure."""
    lowered = message.lower()
    return lowered.startswith("valueerror:") or "invalid literal for int" in lowered


def _corrupt_conversion_error(ext: str, exc: BaseException | str) -> ConversionError:
    return ConversionError(f"corrupt {ext}: {exc}", extension=ext)


def _extract_bytes(raw: bytes, ext: str) -> ExtractedDocument:
    mime = _EXT_TO_MIME.get(ext)
    if mime is None:
        raise ConversionError(f"unsupported extension: {ext}", extension=ext)

    max_sentence_bytes = sentence_emit_max_bytes()
    emit_sentences = len(raw) <= max_sentence_bytes
    if not emit_sentences:
        logger.info(
            "Skipping sentence emission: %d bytes exceeds %d (ext=%s)",
            len(raw),
            max_sentence_bytes,
            ext,
        )

    extract_kwargs: dict[str, object] = {
        "mime": mime,
        "emit_sentences": emit_sentences,
        "emit_markdown": True,
    }
    if ext == ".pdf":
        if extract_accepts_ocr():
            extract_kwargs["use_ocr"] = pdf_ocr_enabled()
            extract_kwargs["ocr_language"] = tesseract_language()
        elif pdf_ocr_enabled():
            global logger_ocr_warned
            if not logger_ocr_warned:
                logger_ocr_warned = True
                logger.warning(
                    "PDF OCR is enabled but the installed knovas-extract does not "
                    "support it; scanned PDFs will yield no text. Upgrade "
                    "knovas-extract to a release with OCR support."
                )

    try:
        result = extract(raw, **extract_kwargs)
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
    except DependencyMissingError as exc:
        raise ConversionError(str(exc), extension=ext) from exc
    except ExtractError as exc:
        raise ConversionError(str(exc), extension=ext) from exc
    except ValueError as exc:
        raise _corrupt_conversion_error(ext, exc) from exc

    text = result.content.text
    if not text.strip():
        raise ConversionError(f"no extractable text from {ext} file", extension=ext)

    payload = payload_from_extraction_result(result)
    description = payload.description or description_from_metadata(result.metadata)

    return ExtractedDocument(
        text=payload.text,
        sentences=payload.sentences,
        title=payload.title,
        description=description,
        tables=payload.tables,
        sections=payload.sections,
        pages=payload.pages,
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


def _extract_child(path_str: str, result_queue: Any) -> None:
    """Run in a child process so a runaway extractor can be killed at the OS
    level — a pure-Python hot loop (pysbd) or a C call that never yields cannot
    be interrupted in-process. Mirrors scripts/build_context_sidecars.py."""
    try:
        result_queue.put(("ok", extract_document(Path(path_str))))
    except ConversionError as exc:
        result_queue.put(("conversion", str(exc)))
    except ValueError as exc:
        ext = Path(path_str).suffix.lower()
        result_queue.put(("conversion", f"corrupt {ext}: {exc}"))
    except Exception as exc:  # noqa: BLE001 - report any failure to the parent
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def extract_document_guarded(file_path: Path) -> ExtractedDocument:
    """`extract_document` with a wall-clock ceiling.

    A single pathological document must not occupy the sync worker forever —
    RC runs one worker, so a stuck extraction halts all ingestion while
    `/sync/status` still reports `running`.

    On timeout raises `ConversionError("resource limit exceeded: ...")`, which
    `is_unconvertible_error()` matches, so incremental sync records the path as
    skipped instead of retrying it every cycle.

    Set `RC_EXTRACT_TIMEOUT_SECONDS=0` to disable and extract in-process.
    """
    timeout = extract_timeout_seconds()
    if timeout <= 0:
        return extract_document(file_path)

    ext = file_path.suffix.lower()
    ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context("spawn")
    # Queue payloads are pickled, but both ends are our own processes — no
    # untrusted data crosses this boundary.
    result_queue = ctx.Queue()
    proc = ctx.Process(target=_extract_child, args=(str(file_path), result_queue))
    proc.start()

    payload: Optional[tuple[str, Any]] = None
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            try:
                payload = result_queue.get(timeout=0.5)
                break
            except queue.Empty:
                if not proc.is_alive():
                    break
    finally:
        # Distinguish "we killed it" from "it died on its own" before the
        # kill overwrites exitcode with the signal number.
        timed_out = payload is None and proc.is_alive()
        if proc.is_alive():
            proc.kill()
        proc.join(10)

    if payload is None:
        if timed_out:
            logger.warning(
                "Extraction timed out after %ds, skipping: %s", timeout, file_path
            )
            raise ConversionError(
                f"resource limit exceeded: extraction timeout after {timeout}s",
                extension=ext,
            )
        raise ConversionError(
            f"extractor died (exit {proc.exitcode})", extension=ext
        )

    kind, value = payload
    if kind == "ok":
        return value
    message = str(value)
    if kind == "conversion":
        raise ConversionError(message, extension=ext)
    if _is_parser_value_error(message):
        raise _corrupt_conversion_error(ext, message)
    raise ConversionError(message, extension=ext)


def bytes_to_markdown(raw_bytes: bytes, suffix: str) -> str:
    """Backwards-compat: return text only. Prefer `extract_document` for new code."""
    return _extract_bytes(raw_bytes, suffix.lower()).text


def file_to_markdown(file_path: Path) -> str:
    """Backwards-compat: return text only. Prefer `extract_document` for new code."""
    return extract_document(file_path).text
