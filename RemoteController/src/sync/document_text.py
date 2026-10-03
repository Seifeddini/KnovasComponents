"""Adapter over `knovas-extract` for the RemoteController sync pipeline.

Wraps `knovas_extract.extract(...)` and returns text + per-sentence
citations (with page back-pointers on PDFs). The uploader threads the
sentence list into the chunker so every transmission part carries an
accurate `page_number` / `sentence_number`.

Image-only PDF pages are OCR'd when `RC_PDF_OCR_ENABLED` is true (default)
and Tesseract is installed. Language packs are selected via
`RC_TESSERACT_LANG` (default `deu+eng`). With a knovas-extract that takes
`ocr=OcrOptions(...)` the OCR engine, the page cap, the time budget, the
RC's disk cache (`sync.ocr_cache`) and -- only when set -- the worker count
and the dpi are passed along (unset, the library decides both); with one that
takes `text_mode=` the `RC_PDF_TEXT_MODE` switch selects `plain`, `layout`
(markdown-lite rows for fiduciary tables) or `shadow` (upload plain, log one
numbers-only `ShadowDiff` against layout — the two renderings share ONE OCR
cache object, so each page is OCR'd once; plan decision D13), and
`RC_DOCX_TEXT_MODE` (`layout` | `plain`) asks for DOCX tables as rows in
the text (spec L3). Every new
keyword is sent only when `extract_accepts(name)` says the installed library
takes it, so the same source runs against today's release.

Markdown is never requested (`emit_markdown=False`): it cost ~8 s per
document and its expansion guard parked every mixed PDF and every DOCX with
large tables as "resource limit exceeded: markdown expansion ratio".

Sentences are emitted for every PDF (spec E4): knovas-extract splits a PDF
page by page, in time linear in its pages. Every other input is split as
ONE text, and pysbd maps each sentence back by searching that text from its
start, so the time grows with the square of the text (2 MiB of export rows
~40 s, 8 MiB past the 300 s ceiling; knovas-extract 0.3 fixed only its own
line counting). Such input gets sentences while its extracted text is at
most `UNPAGED_SENTENCE_MAX_CHARS` (2 MiB): a DOCX (tables in the text), an
MSG or a file larger than that is extracted without sentences first and
again with them when its text is short enough. `RC_SENTENCE_EMIT_MAX_BYTES`
(default `0`: off) is the old gate on raw file size, which switched off the
citations of most multi-page scans; a positive value applies it to every
input. Without sentences only the citations and context previews are
dropped, the text is still uploaded.

Errors from `knovas-extract` are re-raised as `ConversionError` with
message substrings that `is_unconvertible_error()` recognizes, so
incremental-sync retry classification is unchanged. A wall-clock kill of
the extraction child, a killed child ("extractor died") and an OCR budget
trip are NEVER unconvertible (GI-EXTRACT-02): the executor retries them with
a cap and then records the file partial for the OCR-disabled backfill pass.
"""
from __future__ import annotations

import inspect
import logging
import multiprocessing as mp
import os
import queue
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import knovas_extract
from knovas_extract import (
    CorruptDocumentError,
    DependencyMissingError,
    EncryptedDocumentError,
    ExtractError,
    ResourceExhaustedError,
    UnsupportedFormatError,
    extract,
)
from knovas_extract import result as knovas_extract_result
from knovas_extract.result import Page, Section, Sentence

from sync.extract_content import description_from_metadata, payload_from_extraction_result
from sync.metadata_fields import source_metadata_from
from sync.ocr_cache import MemoryOcrCache, ocr_cache_for_document

logger_ocr_warned = False
logger_text_mode_warned = False
logger_shadow_warned = False

#: knovas-extract >= 0.4 exposes the OCR options class; today's release does
#: not, and then `ocr=` is never sent.
OcrOptions = getattr(knovas_extract, "OcrOptions", None)


def extract_accepts(param: str) -> bool:
    """Whether the installed knovas-extract's `extract()` takes `param`.

    OCR keywords arrived after 0.2, `text_mode=` and `ocr=` arrive with 0.4.
    Passing a keyword to an older extractor is a TypeError on every PDF, so
    the pin used to demand a version that was never published to PyPI and no
    CI job could install. Asking the function what it accepts costs one
    introspection and lets the same source run against both.

    A version that cannot be introspected is treated as accepting them:
    dropping OCR silently would ingest scanned PDFs as empty documents, and
    a loud TypeError is the better failure.
    """
    try:
        return param in inspect.signature(extract).parameters
    except (TypeError, ValueError):
        return True


def extract_accepts_ocr() -> bool:
    """Whether the installed knovas-extract takes the OCR keywords."""
    return extract_accepts("use_ocr")


logger = logging.getLogger(__name__)

SYNCABLE_EXTENSIONS = frozenset({".md", ".txt", ".docx", ".pdf", ".eml", ".msg"})

# RC_SENTENCE_EMIT_MAX_BYTES: 0 (default) is no gate on the raw size; a
# positive value skips sentence emission above that many raw bytes.
DEFAULT_SENTENCE_EMIT_MAX_BYTES = 0

#: Input other than PDF gets sentences only while its extracted text is at
#: most this many characters: pysbd's time grows with the square of one text
#: (module docstring). 2 MiB, the old gate's size, keeps it near 40 s.
UNPAGED_SENTENCE_MAX_CHARS = 2 * 1024 * 1024

#: Formats whose text is never longer than the file -- decoded and stripped
#: of markup, never decompressed -- so a file of at most
#: `UNPAGED_SENTENCE_MAX_CHARS` bytes is split in one pass. A DOCX (zipped
#: XML, table rows in the text) or an MSG (compressed RTF body) can hold far
#: more text than bytes: its text is measured first.
_TEXT_NOT_LONGER_THAN_FILE = frozenset({".txt", ".md", ".eml"})

# Wall-clock ceiling for one document's extraction. Override with
# RC_EXTRACT_TIMEOUT_SECONDS; 0 extracts in-process with no ceiling. When the
# page count is known (PDF) the ceiling grows by RC_EXTRACT_TIMEOUT_PER_PAGE_
# SECONDS per page up to RC_EXTRACT_TIMEOUT_MAX_SECONDS.
DEFAULT_EXTRACT_TIMEOUT_SECONDS = 300
DEFAULT_EXTRACT_TIMEOUT_PER_PAGE_SECONDS = 2
DEFAULT_EXTRACT_TIMEOUT_MAX_SECONDS = 1800

#: Prefix of the ConversionError raised when the child is killed on the
#: wall-clock ceiling. It deliberately does NOT start with "resource limit
#: exceeded": that prefix means "the library flagged the input" and parks the
#: file forever (skip:unconvertible); a kill is retryable.
EXTRACT_TIMEOUT_ERROR_PREFIX = "extraction timeout"

#: Prefix of the ConversionError raised when the library refuses the
#: Connector's own OCR settings (``ValueError`` from ``OcrOptions`` /
#: ``Limits``, spec E5). No document is at fault: ``is_unconvertible_error``
#: never matches it and the executor never counts it toward the extraction
#: retries. It used to surface as "corrupt .pdf" and park every PDF for good.
CONFIG_INVALID_PREFIX = "extraction configuration invalid"

# Address-space ceiling for the extraction child (RLIMIT_AS). Override with
# RC_EXTRACT_RLIMIT_AS_MB; 0 disables. The child also runs at nice 10.
DEFAULT_EXTRACT_RLIMIT_AS_MB = 2048
EXTRACT_CHILD_NICE = 10

# PDF OCR via knovas-extract + system Tesseract (see RC_PDF_OCR_ENABLED).
DEFAULT_TESSERACT_LANG = "deu+eng"
#: RC_TESSERACT_LANG: language packs joined by "+" (spec E5; doctor.sh applies
#: the same pattern). OcrOptions refuses spaces, slashes and NUL.
_TESSERACT_LANG_RE = re.compile(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*")
DEFAULT_OCR_ENGINE = "auto"
OCR_ENGINES = ("auto", "tesserocr", "cli", "mupdf")
# RC_OCR_DPI: unset sends no dpi (the library's native-resolution rule); a
# set value must lie in the range OcrOptions accepts.
OCR_DPI_MIN = 30
OCR_DPI_MAX = 1200
DEFAULT_OCR_MAX_PAGES = 500
DEFAULT_OCR_TIME_BUDGET_SECONDS = 240
DEFAULT_OCR_PAGE_TIMEOUT_SECONDS = 60
DEFAULT_OCR_MAX_WORKERS = 8
MIN_OCR_TIME_BUDGET_SECONDS = 10
# Margin the budget derivation keeps for rendering and reassembly.
_OCR_BUDGET_RENDER_MARGIN_SECONDS = 10
_OCR_BUDGET_DEFAULT_HEADROOM_SECONDS = 30

TEXT_MODES = ("plain", "shadow", "layout")
DEFAULT_PDF_TEXT_MODE = "layout"
DOCX_TEXT_MODES = ("layout", "plain")
DEFAULT_DOCX_TEXT_MODE = "layout"

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

#: Error prefixes that are retryable whatever else the message says: the
#: RC's own wall-clock kill, OCR settings the library refused, a killed
#: child (OOM / RLIMIT_AS) and an OCR budget trip that leaked out of an
#: older library as an error.
_NEVER_UNCONVERTIBLE_PREFIXES = (
    EXTRACT_TIMEOUT_ERROR_PREFIX,
    CONFIG_INVALID_PREFIX,
    "extractor died",
    "ocr budget exceeded",
    "resource limit exceeded: ocr",
)


class ConversionError(Exception):
    """Failed to extract text from a document."""

    def __init__(self, message: str, *, extension: str = "") -> None:
        super().__init__(message)
        self.extension = extension


#: The library's option field -> the Connector setting it is built from.
_SETTING_FOR_OPTION_FIELD = {
    "engine": "RC_OCR_ENGINE",
    "language": "RC_TESSERACT_LANG",
    "dpi": "RC_OCR_DPI",
    "workers": "RC_OCR_WORKERS",
    "max_ocr_workers": "RC_OCR_WORKERS",
    "max_ocr_pages": "RC_OCR_MAX_PAGES",
    "ocr_time_budget_seconds": "RC_OCR_TIME_BUDGET_SECONDS",
    "ocr_page_timeout_seconds": "RC_OCR_PAGE_TIMEOUT_SECONDS",
}
_OPTION_FIELD_RE = re.compile(r"\b(?:OcrOptions|Limits)\.([a-z_]+)")


def config_invalid_error(exc: ValueError) -> ConversionError:
    """The retryable error for OCR settings the library refused (spec E5).

    Names the setting when the library's message names the field
    (``OcrOptions.language must be …``), else "OCR options"; never the value
    -- the message is logged and stored in the sync state.
    """
    match = _OPTION_FIELD_RE.search(str(exc))
    setting = _SETTING_FOR_OPTION_FIELD.get(match.group(1), "OCR options") if match else "OCR options"
    return ConversionError(f"{CONFIG_INVALID_PREFIX}: {setting}", extension=".pdf")


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

    `extra` is a copy of the scalar `metadata.extra` entries (`pdf:ocr_pages`,
    `pdf:ocr_pages_skipped`, `pdf:ocr_backend`, ...) plus the RC's own
    `rc:ocr_cache_hits` / `rc:ocr_cache_misses`; `page_count` the metadata
    page count. Counts and identifiers only, never text.

    `source_metadata` holds the extractor's `author`, `language`, `created`
    and `modified` plus the .eml `eml:content_language` header, as strings
    (`sync.metadata_fields.source_metadata_from`). Only the opted-in
    metadata mapping reads it (`sync.metadata_fields.map_metadata`); it is
    customer data and is never logged. Plain str values, so it pickles
    across the extraction child's queue.
    """

    text: str
    sentences: Optional[list[Sentence]]
    title: Optional[str] = None
    description: Optional[str] = None
    tables: Optional[list[dict[str, Any]]] = None
    sections: Optional[list[Section]] = None
    pages: Optional[list[Page]] = None
    extra: Optional[dict[str, Any]] = None
    page_count: Optional[int] = None
    source_metadata: dict[str, str] = field(default_factory=dict)


def is_syncable_extension(suffix: str) -> bool:
    return suffix.lower() in SYNCABLE_EXTENSIONS


def is_unconvertible_error(error: str | None) -> bool:
    """True when a file cannot be converted and should not block incremental sync.

    Only what the library itself flags — corrupt, encrypted, unsupported,
    empty, its own resource limits (page count, text size, ...) — is
    unconvertible. The RC's wall-clock kill, a killed child and an OCR budget
    trip are retryable (GI-EXTRACT-02).
    """
    if not error:
        return False
    lowered = error.lower()
    if lowered.startswith("init failed:") or (lowered.startswith("part ") and " failed:" in lowered):
        return False
    if lowered.startswith(_NEVER_UNCONVERTIBLE_PREFIXES):
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


# --- environment -------------------------------------------------------------


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; using default %d", name, raw, default)
        return default


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning("Invalid %s=%r; using default %s", name, raw, default)
    return default


def _non_negative_int(raw: str) -> Optional[int]:
    """`raw` as an int when it is ASCII digits only, else None -- the rule
    `scripts/doctor.sh` applies to the same settings (no sign, no `1_000`)."""
    return int(raw) if raw.isascii() and raw.isdigit() else None


def _env_int_at_least(name: str, default: int, minimum: int) -> int:
    """A whole-number setting of at least ``minimum`` (spec E5). Anything else
    -- not ASCII digits, or below the minimum -- logs one warning naming the
    setting and uses the default; ``_env_int(minimum=)`` would clamp it
    silently (a page timeout of 0 became 1 s and failed every page)."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    value = _non_negative_int(raw)
    if value is None or value < minimum:
        logger.warning(
            "Invalid %s=%r (a whole number of at least %d); using default %d",
            name, raw, minimum, default,
        )
        return default
    return value


def sentence_emit_max_bytes() -> int:
    """`RC_SENTENCE_EMIT_MAX_BYTES`: 0 (default) is no gate; a positive value
    skips sentence emission above that many raw bytes (see module docstring)."""
    return _env_int("RC_SENTENCE_EMIT_MAX_BYTES", DEFAULT_SENTENCE_EMIT_MAX_BYTES)


def extract_timeout_seconds(page_count: Optional[int] = None) -> int:
    """Wall-clock ceiling for one document's extraction (see module docstring).

    With a known page count the ceiling is at least
    `RC_EXTRACT_TIMEOUT_PER_PAGE_SECONDS` × pages, capped at
    `RC_EXTRACT_TIMEOUT_MAX_SECONDS`; `RC_EXTRACT_TIMEOUT_SECONDS` is always
    honoured as the floor. 0 disables the ceiling.
    """
    base = _env_int("RC_EXTRACT_TIMEOUT_SECONDS", DEFAULT_EXTRACT_TIMEOUT_SECONDS)
    if base <= 0:
        return 0
    if page_count is None or page_count <= 0:
        return base
    per_page = _env_int("RC_EXTRACT_TIMEOUT_PER_PAGE_SECONDS", DEFAULT_EXTRACT_TIMEOUT_PER_PAGE_SECONDS)
    cap = _env_int("RC_EXTRACT_TIMEOUT_MAX_SECONDS", DEFAULT_EXTRACT_TIMEOUT_MAX_SECONDS, minimum=1)
    return max(base, min(per_page * int(page_count), cap))


def extract_rlimit_as_bytes() -> int:
    """Address-space limit for the extraction child; 0 disables."""
    return _env_int("RC_EXTRACT_RLIMIT_AS_MB", DEFAULT_EXTRACT_RLIMIT_AS_MB) * 1024 * 1024


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
    """`RC_TESSERACT_LANG` (default `deu+eng`): language packs joined by `+`.
    Anything else (`deu eng`, `deu,eng`) logs one warning naming the setting
    and uses the default -- `OcrOptions` would refuse it (spec E5)."""
    raw = (os.environ.get("RC_TESSERACT_LANG") or "").strip()
    if not raw:
        return DEFAULT_TESSERACT_LANG
    if _TESSERACT_LANG_RE.fullmatch(raw):
        return raw
    logger.warning("Invalid RC_TESSERACT_LANG=%r; using %s", raw, DEFAULT_TESSERACT_LANG)
    return DEFAULT_TESSERACT_LANG


def pdf_text_mode() -> str:
    """`RC_PDF_TEXT_MODE`: layout (default) | plain | shadow."""
    raw = (os.environ.get("RC_PDF_TEXT_MODE") or "").strip().lower()
    if not raw:
        return DEFAULT_PDF_TEXT_MODE
    if raw in TEXT_MODES:
        return raw
    logger.warning("Invalid RC_PDF_TEXT_MODE=%r; using %s", raw, DEFAULT_PDF_TEXT_MODE)
    return DEFAULT_PDF_TEXT_MODE


def docx_text_mode() -> str:
    """`RC_DOCX_TEXT_MODE`: layout (default) | plain (spec L3). Layout asks
    knovas-extract to write Word tables into the text, in place, as
    markdown-lite rows -- the server drops the `tables` payload, so before
    this a DOCX table's content never reached the search index. Plain is
    paragraphs only. Invalid values log one warning and use the default."""
    raw = (os.environ.get("RC_DOCX_TEXT_MODE") or "").strip().lower()
    if not raw:
        return DEFAULT_DOCX_TEXT_MODE
    if raw in DOCX_TEXT_MODES:
        return raw
    logger.warning("Invalid RC_DOCX_TEXT_MODE=%r; using %s", raw, DEFAULT_DOCX_TEXT_MODE)
    return DEFAULT_DOCX_TEXT_MODE


def docx_tables_in_text(doc: ExtractedDocument) -> bool:
    """Whether knovas-extract wrote this DOCX's tables into its text: it
    reports `docx:text_mode == "layout"` (DOCX layout mode). Then the upload
    sends no `tables` payload -- the rows would be indexed twice if the
    server ever stopped dropping it. A library without DOCX layout mode
    reports nothing, its text has no rows, and the payload stays."""
    value = (doc.extra or {}).get("docx:text_mode")
    return isinstance(value, str) and value.strip().lower() == "layout"


def ocr_engine() -> str:
    raw = (os.environ.get("RC_OCR_ENGINE") or "").strip().lower()
    if not raw:
        return DEFAULT_OCR_ENGINE
    if raw in OCR_ENGINES:
        return raw
    logger.warning("Invalid RC_OCR_ENGINE=%r; using %s", raw, DEFAULT_OCR_ENGINE)
    return DEFAULT_OCR_ENGINE


def ocr_dpi() -> Optional[int]:
    """`RC_OCR_DPI` (spec E3). Unset (default): None, no `dpi` is passed and
    the library renders each page at its native resolution, at most 300 dpi,
    never upsampled -- a 150 dpi fax is OCR'd at 150 dpi. Set: every page is
    rendered at exactly that resolution (30-1200, the range `OcrOptions`
    accepts), so a lower-resolution scan IS upsampled. Anything else logs
    one warning and counts as unset."""
    raw = (os.environ.get("RC_OCR_DPI") or "").strip()
    if not raw:
        return None
    value = _non_negative_int(raw)
    if value is None or not OCR_DPI_MIN <= value <= OCR_DPI_MAX:
        logger.warning(
            "Invalid RC_OCR_DPI=%r (%d-%d); rendering at the native resolution",
            raw, OCR_DPI_MIN, OCR_DPI_MAX,
        )
        return None
    return value


def ocr_workers() -> Optional[int]:
    """`RC_OCR_WORKERS`. Unset (default): None, and the library sizes the pool
    itself -- `min(Limits.max_ocr_workers, CPUs - 1)`, the CPUs counted from
    the affinity mask AND the container's cgroup v2 CPU quota (the
    Connector's old default, cores - 2, saw neither the quota nor the
    library's ceiling). Set: that many, 1 to 8, as before; a value that is
    not a number logs one warning and counts as unset."""
    raw = (os.environ.get("RC_OCR_WORKERS") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid RC_OCR_WORKERS=%r; the library chooses the worker count", raw)
        return None
    return max(1, min(DEFAULT_OCR_MAX_WORKERS, value))


def ocr_time_budget_seconds(timeout_seconds: int, page_timeout_seconds: int) -> int:
    """The OCR time budget the child hands the library (spec E2).

    Default `min(240, timeout - 30)` (`RC_OCR_TIME_BUDGET_SECONDS`
    overrides), never more than `timeout - page_timeout - 10`, at least
    10 s. The pool stops SUBMITTING pages when the budget trips; the pages
    already running finish IN PARALLEL within one page timeout, and the
    partial result then needs the margin to reach the parent before the
    wall-clock kill (plan `[C-sec-0]`). The cap used to subtract
    `workers × page_timeout`, as if those pages ran one after another: from
    five workers on (7+ cores) the budget of every PDF up to ~150 pages fell
    to the 10 s floor and long scans came back partial. With the ceiling
    disabled (`timeout <= 0`) the env value or 240 s applies as is.
    """
    configured = _env_int("RC_OCR_TIME_BUDGET_SECONDS", -1, minimum=-1)
    if timeout_seconds <= 0:
        return configured if configured >= 0 else DEFAULT_OCR_TIME_BUDGET_SECONDS
    budget = configured if configured >= 0 else min(
        DEFAULT_OCR_TIME_BUDGET_SECONDS, timeout_seconds - _OCR_BUDGET_DEFAULT_HEADROOM_SECONDS
    )
    hard_cap = timeout_seconds - page_timeout_seconds - _OCR_BUDGET_RENDER_MARGIN_SECONDS
    return max(MIN_OCR_TIME_BUDGET_SECONDS, min(budget, hard_cap))


def ocr_options_kwargs(timeout_seconds: Optional[int] = None) -> dict[str, Any]:
    """Keyword arguments for the library's `OcrOptions`, from the environment.

    `RC_OCR_ENGINE`, `RC_OCR_DPI`, `RC_OCR_WORKERS` (None while unset: the
    library sizes the pool), `RC_OCR_MAX_PAGES`, `RC_OCR_TIME_BUDGET_SECONDS`
    (derived from the extraction timeout when unset),
    `RC_OCR_PAGE_TIMEOUT_SECONDS`, `RC_TESSERACT_LANG`. The cache is added by
    the caller (`cache=`); its size is `RC_OCR_CACHE_MAX_MB` (see
    `sync.ocr_cache`).
    """
    timeout = extract_timeout_seconds() if timeout_seconds is None else int(timeout_seconds)
    page_timeout = _env_int_at_least("RC_OCR_PAGE_TIMEOUT_SECONDS", DEFAULT_OCR_PAGE_TIMEOUT_SECONDS, 1)
    options: dict[str, Any] = {
        "engine": ocr_engine(),
        "workers": ocr_workers(),
        "max_ocr_pages": _env_int_at_least("RC_OCR_MAX_PAGES", DEFAULT_OCR_MAX_PAGES, 1),
        "time_budget_seconds": ocr_time_budget_seconds(timeout, page_timeout),
        "page_timeout_seconds": page_timeout,
        "language": tesseract_language(),
    }
    dpi = ocr_dpi()
    if dpi is not None:
        # Only when set: an explicit dpi is used as is and upsamples a 150 dpi
        # fax to 300 (CER 0.145 against 0.028 at its native resolution).
        options["dpi"] = dpi
    return options


#: The library's field names are introspected; these are the spellings the
#: plan uses (§3 budgets, §6 `OcrOptions(workers=1, max_ocr_pages=50, …)`)
#: plus the obvious variants, so a renamed field lands instead of raising.
_OCR_OPTION_ALIASES: dict[str, tuple[str, ...]] = {
    # Not "backend": that slot takes an injected IOcrBackend object, and an
    # engine name there would be a type error at the first scanned page.
    "engine": ("engine",),
    "dpi": ("dpi", "render_dpi", "max_dpi"),
    "workers": ("workers", "max_workers", "max_ocr_workers"),
    "max_ocr_pages": ("max_ocr_pages", "max_pages"),
    "time_budget_seconds": ("time_budget_seconds", "ocr_time_budget_seconds", "time_budget"),
    "page_timeout_seconds": ("page_timeout_seconds", "ocr_page_timeout_seconds", "page_timeout"),
    "language": ("language", "lang", "languages", "ocr_language"),
    "cache": ("cache",),
}


#: Budget fields that live on the library's `Limits` (knovas-extract >= 0.4),
#: not on `OcrOptions`: ours -> the Limits field spellings.
_OCR_LIMIT_ALIASES: dict[str, tuple[str, ...]] = {
    "max_ocr_pages": ("max_ocr_pages",),
    "time_budget_seconds": ("ocr_time_budget_seconds",),
    "page_timeout_seconds": ("ocr_page_timeout_seconds",),
    "workers": ("max_ocr_workers",),
}


def build_ocr_limits(options: dict[str, Any]) -> Any:
    """Instantiate the library's `Limits` with the OCR budgets it holds.

    `OcrOptions` carries engine/dpi/workers/cache; the page cap, the time
    budget and the per-page timeout are `Limits` fields — passing them to
    `OcrOptions` silently leaves them at the library defaults (found by the
    Platform mirror). Returns None when the installed library has no
    `Limits` or none of the fields.
    """
    limits_cls = getattr(knovas_extract_result, "Limits", None)
    if limits_cls is None:
        return None
    try:
        params = inspect.signature(limits_cls).parameters
    except (TypeError, ValueError):
        return None
    accepted: dict[str, Any] = {}
    for ours, value in options.items():
        if value is None:
            # RC_OCR_WORKERS unset: Limits.max_ocr_workers keeps the library's
            # ceiling (8); None there would break the library's min().
            continue
        for name in _OCR_LIMIT_ALIASES.get(ours, ()):
            if name in params:
                accepted[name] = value
                break
    if not accepted:
        return None
    return limits_cls(**accepted)


def build_ocr_options(options: dict[str, Any]) -> Any:
    """Instantiate the library's `OcrOptions` with the fields it accepts."""
    cls = OcrOptions
    if cls is None:
        return None
    try:
        params = inspect.signature(cls).parameters
    except (TypeError, ValueError):
        params = None
    if params is None or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return cls(**options)
    accepted: dict[str, Any] = {}
    dropped: list[str] = []
    for ours, value in options.items():
        for name in _OCR_OPTION_ALIASES.get(ours, (ours,)):
            if name in params:
                accepted[name] = value
                break
        else:
            dropped.append(ours)
    if dropped:
        logger.debug("OcrOptions does not take %s; left at library defaults", ", ".join(dropped))
    return cls(**accepted)


# --- extraction ----------------------------------------------------------------


def _is_parser_value_error(message: str) -> bool:
    """True when a binary parser raised ValueError on malformed structure."""
    lowered = message.lower()
    return lowered.startswith("valueerror:") or "invalid literal for int" in lowered


def _corrupt_conversion_error(ext: str, exc: BaseException | str) -> ConversionError:
    return ConversionError(f"corrupt {ext}: {exc}", extension=ext)


def pdf_page_count(raw: bytes) -> Optional[int]:
    """Page count of a PDF, or None when it cannot be opened cheaply.

    Called in the extraction CHILD only — opening a hostile PDF in the
    parent is exactly what the wall-clock guard exists to avoid.
    """
    try:
        import fitz
    except ImportError:
        return None
    try:
        doc = fitz.open(stream=raw, filetype="pdf")
    except Exception:  # noqa: BLE001 - the real extraction reports the error
        return None
    try:
        if doc.is_encrypted and not doc.authenticate(""):
            return None
        return int(doc.page_count)
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass


def _scalar_extra(metadata: Any) -> dict[str, Any]:
    extra = getattr(metadata, "extra", None)
    if not isinstance(extra, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in extra.items():
        if value is None or isinstance(value, (bool, int, float)):
            out[str(key)] = value
        elif isinstance(value, str) and len(value) <= 128:
            out[str(key)] = value
    return out


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


#: The library's OCR counts a partial note carries (counts only, never text):
#: note key -> metadata.extra key.
_PARTIAL_NOTE_COUNTS = (
    ("ocr_pages_skipped", "pdf:ocr_pages_skipped"),
    ("ocr_pages_failed", "pdf:ocr_pages_failed"),
    ("ocr_pages", "pdf:ocr_pages"),
    ("text_pages", "pdf:text_pages"),
)


def partial_note_for(doc: ExtractedDocument, *, expect_ocr: bool) -> Optional[dict[str, Any]]:
    """The partial note for a returned document, or None when it is complete.

    One rule in the Connector and the Platform (spec E1; the mirror is
    ``knovas_extract_upload.partial_note_for``). A document is partial when

    * the library counted skipped OCR pages (``pdf:ocr_pages_skipped > 0``):
      the page cap, the time budget, the pixel cap -- or no OCR engine, for
      which 0.4 counts every page that needed OCR as skipped;
    * or failed OCR pages (``pdf:ocr_pages_failed > 0``): a failed page is an
      empty page;
    * or -- only for a library that does not count skipped pages -- OCR was
      expected, ``pdf:ocr_backend == "none"`` and ``pdf:ocr_pages_skipped`` is
      absent.

    With ``ocr=`` passed, 0.4 reports every OCR key: a born-digital PDF says
    backend ``"none"`` with zero skipped pages -- nothing needed OCR, so it is
    complete wherever Tesseract is installed (the rule before counted it
    partial, metered a degraded backend and backfilled it on every run).
    The note carries the counts the library reported and the backend name.
    """
    extra = doc.extra or {}
    counts = {key: _int_or_none(extra.get(source)) for key, source in _PARTIAL_NOTE_COUNTS}
    skipped, failed = counts["ocr_pages_skipped"], counts["ocr_pages_failed"]
    backend = extra.get("pdf:ocr_backend")
    backend_s = str(backend).strip().lower() if isinstance(backend, str) else ""
    partial = (
        (skipped is not None and skipped > 0)
        or (failed is not None and failed > 0)
        or (expect_ocr and backend_s == "none" and skipped is None)
    )
    if not partial:
        return None
    note: dict[str, Any] = {key: value for key, value in counts.items() if value is not None}
    if backend_s:
        note["ocr_backend"] = backend_s
    return note


def ocr_backend_missing(note: Optional[dict[str, Any]]) -> bool:
    """Whether a partial note says pages went without OCR for want of an
    engine: the library reports ``ocr_backend == "none"``. 0.4 names the
    engine whenever OCR ran, so a partial note with ``none`` is either the
    uncounted case of ``partial_note_for`` or pages the library skipped
    because no backend was available. Feeds ``rc_ocr_backend_degraded_total``
    only; a budget trip or a failed page is not a degraded backend."""
    return bool(note) and note.get("ocr_backend") == "none"


# --- shadow mode (numbers only, never text) ----------------------------------

_NUMERIC_TOKEN_RE = re.compile(r"\d(?:[\d'’.,]*\d)?")
_AMOUNT_RE = re.compile(r"\d{1,3}(?:['’.]\d{3})+(?:[.,]\d{2})?|\d+[.,]\d{2}(?!\d)")
_LETTER_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")


@dataclass(frozen=True)
class ShadowDiff:
    """One numbers-only comparison of the plain and the layout rendering."""

    numeric_jaccard: float
    row_line_ratio_plain: float
    row_line_ratio_layout: float
    length_ratio: float
    ocr_pages: Optional[int]
    seconds_plain: float
    seconds_layout: float

    def as_log_fields(self) -> dict[str, Any]:
        return {
            "numeric_jaccard": round(self.numeric_jaccard, 3),
            "row_line_ratio_plain": round(self.row_line_ratio_plain, 3),
            "row_line_ratio_layout": round(self.row_line_ratio_layout, 3),
            "length_ratio": round(self.length_ratio, 3),
            "ocr_pages": self.ocr_pages,
            "seconds_plain": round(self.seconds_plain, 1),
            "seconds_layout": round(self.seconds_layout, 1),
        }


def numeric_token_jaccard(a: str, b: str) -> float:
    """Jaccard similarity of the numeric tokens (amounts, dates, ids)."""
    left = set(_NUMERIC_TOKEN_RE.findall(a or ""))
    right = set(_NUMERIC_TOKEN_RE.findall(b or ""))
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def row_line_ratio(text: str) -> float:
    """Share of non-empty lines that carry letters AND an amount."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return 0.0
    rows = sum(1 for line in lines if _LETTER_RE.search(line) and _AMOUNT_RE.search(line))
    return rows / len(lines)


def shadow_diff(
    plain_text: str,
    layout_text: str,
    *,
    ocr_pages: Optional[int],
    seconds_plain: float,
    seconds_layout: float,
) -> ShadowDiff:
    return ShadowDiff(
        numeric_jaccard=numeric_token_jaccard(plain_text, layout_text),
        row_line_ratio_plain=row_line_ratio(plain_text),
        row_line_ratio_layout=row_line_ratio(layout_text),
        length_ratio=(len(layout_text) / len(plain_text)) if plain_text else 0.0,
        ocr_pages=ocr_pages,
        seconds_plain=seconds_plain,
        seconds_layout=seconds_layout,
    )


def _log_shadow_diff(diff: ShadowDiff, document_key: Optional[str]) -> None:
    fields = diff.as_log_fields()
    logger.info(
        "ShadowDiff file=%s %s",
        Path(document_key).name if document_key else "-",
        " ".join(f"{k}={v}" for k, v in fields.items()),
    )


def _pdf_extract_kwargs(
    *,
    timeout_seconds: Optional[int],
    document_key: Optional[str],
) -> tuple[dict[str, Any], bool, Any]:
    """PDF keywords for `extract()`: OCR switch, options + cache, text mode.

    Returns `(kwargs, shadow, cache)`; `shadow` says a second `layout` pass
    is due, `cache` is the OCR cache object both passes share.
    """
    global logger_ocr_warned, logger_text_mode_warned, logger_shadow_warned
    kwargs: dict[str, Any] = {}
    use_ocr = pdf_ocr_enabled()
    cache: Any = None
    if extract_accepts_ocr():
        kwargs["use_ocr"] = use_ocr
        kwargs["ocr_language"] = tesseract_language()
    elif use_ocr and not logger_ocr_warned:
        logger_ocr_warned = True
        logger.warning(
            "PDF OCR is enabled but the installed knovas-extract does not "
            "support it; scanned PDFs will yield no text. Upgrade "
            "knovas-extract to a release with OCR support."
        )
    if use_ocr and OcrOptions is not None and extract_accepts("ocr"):
        cache = ocr_cache_for_document(document_key)
        ocr_kwargs = ocr_options_kwargs(timeout_seconds)
        try:
            options = build_ocr_options({**ocr_kwargs, "cache": cache})
            limits = build_ocr_limits(ocr_kwargs) if extract_accepts("limits") else None
        except ValueError as exc:
            # The library refused the Connector's own settings: no document
            # is at fault (spec E5). Reported as a configuration error, never
            # as "corrupt .pdf", which parked every PDF for good.
            close = getattr(cache, "close", None)
            if callable(close):
                close()
            raise config_invalid_error(exc) from exc
        if options is not None:
            kwargs["ocr"] = options
        if limits is not None:
            kwargs["limits"] = limits

    mode = pdf_text_mode()
    if mode == "plain":
        return kwargs, False, cache
    if not extract_accepts("text_mode"):
        if not logger_text_mode_warned:
            logger_text_mode_warned = True
            logger.warning(
                "RC_PDF_TEXT_MODE=%s but the installed knovas-extract has no text_mode; using plain",
                mode,
            )
        return kwargs, False, cache
    if mode == "layout":
        kwargs["text_mode"] = "layout"
        return kwargs, False, cache
    # shadow: plain is uploaded, layout is rendered from the same OCR cache
    if use_ocr and "ocr" not in kwargs:
        if not logger_shadow_warned:
            logger_shadow_warned = True
            logger.warning(
                "RC_PDF_TEXT_MODE=shadow needs an OCR cache the installed knovas-extract "
                "cannot take (no ocr=); running plain only so no page is OCR'd twice"
            )
        return kwargs, False, cache
    if cache is None:
        cache = MemoryOcrCache()
    kwargs["text_mode"] = "plain"
    return kwargs, True, cache


def _text_may_exceed_sentence_limit(raw: bytes, ext: str) -> bool:
    """Whether an input's text may be longer than `UNPAGED_SENTENCE_MAX_CHARS`
    before anything is extracted: never for a PDF (split per page), for
    TXT, Markdown and EML only when the file is, always for DOCX and MSG."""
    if ext == ".pdf":
        return False
    return ext not in _TEXT_NOT_LONGER_THAN_FILE or len(raw) > UNPAGED_SENTENCE_MAX_CHARS


def _with_sentences_if_short(raw: bytes, ext: str, kwargs: dict[str, object], result: Any) -> Any:
    """`result` was extracted without sentences to measure its text: extract
    again with sentences when the text is at most `UNPAGED_SENTENCE_MAX_CHARS`
    characters, else keep it (text uploaded, no citations; counts only in
    the log)."""
    text = str(result.content.text or "")
    if len(text) > UNPAGED_SENTENCE_MAX_CHARS:
        logger.info(
            "Skipping sentence emission: %d characters of text exceed %d (ext=%s)",
            len(text),
            UNPAGED_SENTENCE_MAX_CHARS,
            ext,
        )
        return result
    if not text.strip():
        return result  # "no extractable text" follows; nothing to split
    return extract(raw, **{**kwargs, "emit_sentences": True})


def _extract_bytes(
    raw: bytes,
    ext: str,
    *,
    timeout_seconds: Optional[int] = None,
    document_key: Optional[str] = None,
) -> ExtractedDocument:
    mime = _EXT_TO_MIME.get(ext)
    if mime is None:
        raise ConversionError(f"unsupported extension: {ext}", extension=ext)

    max_sentence_bytes = sentence_emit_max_bytes()
    emit_sentences = max_sentence_bytes <= 0 or len(raw) <= max_sentence_bytes
    if not emit_sentences:
        logger.info(
            "Skipping sentence emission: %d bytes exceeds %d (ext=%s)",
            len(raw),
            max_sentence_bytes,
            ext,
        )
    # Input other than PDF is split as one text (module docstring): unless the
    # file size bounds that text, extract without sentences first and measure.
    measure_text_first = emit_sentences and _text_may_exceed_sentence_limit(raw, ext)

    extract_kwargs: dict[str, object] = {
        "mime": mime,
        "emit_sentences": emit_sentences and not measure_text_first,
        # Never: ~8 s per document, and the expansion guard parked every mixed
        # PDF and every DOCX with large tables (plan M0).
        "emit_markdown": False,
    }
    shadow = False
    cache: Any = None
    if ext == ".pdf":
        pdf_kwargs, shadow, cache = _pdf_extract_kwargs(
            timeout_seconds=timeout_seconds, document_key=document_key
        )
        extract_kwargs.update(pdf_kwargs)
    elif ext == ".docx" and docx_text_mode() == "layout" and extract_accepts("text_mode"):
        # Word tables in place, as markdown-lite rows, so their content is
        # searchable (spec L3); a library without DOCX layout mode returns the
        # plain text with one warning and the tables payload stays.
        extract_kwargs["text_mode"] = "layout"

    started = time.monotonic()
    try:
        result = extract(raw, **extract_kwargs)
        if measure_text_first:
            result = _with_sentences_if_short(raw, ext, extract_kwargs, result)
    except UnsupportedFormatError as exc:
        raise ConversionError(f"unsupported extension: {ext}", extension=ext) from exc
    except CorruptDocumentError as exc:
        raise ConversionError(f"corrupt {ext}: {exc}", extension=ext) from exc
    except EncryptedDocumentError as exc:
        raise ConversionError(f"encrypted {ext}: {exc}", extension=ext) from exc
    except ResourceExhaustedError as exc:
        what = str(getattr(exc, "what", "unknown"))
        if "ocr" in what.lower():
            # An OCR budget is fail-soft in >= 0.4; an older library that
            # raised it must not park the file (GI-EXTRACT-02).
            raise ConversionError(f"ocr budget exceeded: {what}", extension=ext) from exc
        raise ConversionError(f"resource limit exceeded: {what}", extension=ext) from exc
    except DependencyMissingError as exc:
        raise ConversionError(str(exc), extension=ext) from exc
    except ExtractError as exc:
        raise ConversionError(str(exc), extension=ext) from exc
    except ValueError as exc:
        raise _corrupt_conversion_error(ext, exc) from exc
    seconds_plain = time.monotonic() - started

    text = result.content.text
    if not text.strip():
        raise ConversionError(f"no extractable text from {ext} file", extension=ext)

    extra = _scalar_extra(result.metadata)
    if shadow:
        _run_shadow_pass(raw, extract_kwargs, text, extra, seconds_plain, document_key)
    if cache is not None:
        extra["rc:ocr_cache_hits"] = int(getattr(cache, "hits", 0) or 0)
        extra["rc:ocr_cache_misses"] = int(getattr(cache, "misses", 0) or 0)
        close = getattr(cache, "close", None)
        if callable(close):
            close()

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
        extra=extra,
        page_count=_int_or_none(getattr(result.metadata, "page_count", None)),
        source_metadata=source_metadata_from(result.metadata),
    )


def _run_shadow_pass(
    raw: bytes,
    plain_kwargs: dict[str, object],
    plain_text: str,
    extra: dict[str, Any],
    seconds_plain: float,
    document_key: Optional[str],
) -> None:
    """The layout rendering of shadow mode: same bytes, same OCR cache
    object (every page OCR'd once), result logged as numbers and dropped.
    A failure here never fails the upload of the plain text."""
    started = time.monotonic()
    try:
        layout = extract(raw, **{**plain_kwargs, "text_mode": "layout"})
        layout_text = str(layout.content.text or "")
    except Exception as exc:  # noqa: BLE001 - shadow only
        logger.warning("Shadow layout pass failed: %s", type(exc).__name__)
        return
    diff = shadow_diff(
        plain_text,
        layout_text,
        ocr_pages=_int_or_none(extra.get("pdf:ocr_pages")),
        seconds_plain=seconds_plain,
        seconds_layout=time.monotonic() - started,
    )
    _log_shadow_diff(diff, document_key)


def extract_document(
    file_path: Path,
    *,
    document_key: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> ExtractedDocument:
    """Extract text + sentence citations from a local file.

    Returns an `ExtractedDocument`. Raises `ConversionError` on any per-file
    failure (unsupported format, corrupt bytes, encrypted, resource cap
    exceeded, empty output) and also for a `DependencyMissingError`: a
    missing extra is a deploy misconfiguration, and its message ("missing
    optional dependency …") is not one `is_unconvertible_error` matches, so
    the file stays retryable (counted toward `RC_EXTRACT_MAX_RETRIES`) and is
    never parked as unconvertible.

    `document_key` names the document for the OCR cache (the sync-relative
    path; defaults to the file path). `timeout_seconds` is the wall-clock
    ceiling the OCR time budget is derived from (defaults to the env value).
    """
    ext = file_path.suffix.lower()
    if ext not in SYNCABLE_EXTENSIONS:
        raise ConversionError(f"unsupported extension: {ext}", extension=ext)

    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise ConversionError(str(exc), extension=ext) from exc

    return _extract_bytes(
        raw, ext, timeout_seconds=timeout_seconds, document_key=document_key or str(file_path)
    )


def _apply_child_limits() -> None:
    """Lower the extraction child's priority and cap its address space.

    Runs first thing in the forked child. `nice 10` keeps a long OCR from
    starving the API worker and the customer's other services; `RLIMIT_AS`
    (`RC_EXTRACT_RLIMIT_AS_MB`, 2 GiB by default) turns a pixel bomb or a
    runaway decode into "extractor died" — retryable, capped — instead of an
    OOM kill of the whole container. The soft limit is set; a hard limit
    already below it is respected.
    """
    try:
        os.nice(EXTRACT_CHILD_NICE)
    except (OSError, AttributeError):
        pass
    limit = extract_rlimit_as_bytes()
    if limit <= 0:
        return
    try:
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY and hard < limit:
            limit = hard
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    except (ImportError, ValueError, OSError) as exc:
        logger.warning("RLIMIT_AS not applied to the extraction child: %s", type(exc).__name__)


def _extract_child(path_str: str, result_queue: Any, document_key: Optional[str] = None) -> None:
    """Run in a child process so a runaway extractor can be killed at the OS
    level — a pure-Python hot loop (pysbd) or a C call that never yields cannot
    be interrupted in-process. Mirrors scripts/build_context_sidecars.py.

    For a PDF the child reports the page count first (`("pages", {...})`) so
    the parent can scale its deadline; the same number sizes the OCR budget.
    """
    _apply_child_limits()
    path = Path(path_str)
    ext = path.suffix.lower()
    try:
        if ext not in SYNCABLE_EXTENSIONS:
            raise ConversionError(f"unsupported extension: {ext}", extension=ext)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ConversionError(str(exc), extension=ext) from exc
        page_count = pdf_page_count(raw) if ext == ".pdf" else None
        timeout = extract_timeout_seconds(page_count)
        if page_count is not None:
            result_queue.put(("pages", {"page_count": page_count, "timeout": timeout}))
        doc = _extract_bytes(raw, ext, timeout_seconds=timeout, document_key=document_key or path_str)
        result_queue.put(("ok", doc))
    except ConversionError as exc:
        result_queue.put(("conversion", str(exc)))
    except ValueError as exc:
        result_queue.put(("conversion", f"corrupt {ext}: {exc}"))
    except Exception as exc:  # noqa: BLE001 - report any failure to the parent
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def extract_document_guarded(file_path: Path, *, document_key: Optional[str] = None) -> ExtractedDocument:
    """`extract_document` with a wall-clock ceiling.

    A single pathological document must not occupy the sync worker forever —
    RC runs one worker, so a stuck extraction halts all ingestion while
    `/sync/status` still reports `running`.

    On timeout raises `ConversionError("extraction timeout after …")`, which
    `is_unconvertible_error()` does NOT match: the executor retries the file
    (capped by `RC_EXTRACT_MAX_RETRIES`) and then records it partial for the
    OCR-disabled backfill pass, instead of parking it forever for one hung
    OCR page (GI-EXTRACT-02). The ceiling grows with the PDF page count the
    child reports (`RC_EXTRACT_TIMEOUT_PER_PAGE_SECONDS`).

    Set `RC_EXTRACT_TIMEOUT_SECONDS=0` to disable and extract in-process.
    """
    timeout = extract_timeout_seconds()
    if timeout <= 0:
        return extract_document(file_path, document_key=document_key)

    ext = file_path.suffix.lower()
    ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context("spawn")
    # Queue payloads are pickled, but both ends are our own processes — no
    # untrusted data crosses this boundary.
    result_queue = ctx.Queue()
    kwargs = {"document_key": document_key} if document_key is not None else {}
    proc = ctx.Process(target=_extract_child, args=(str(file_path), result_queue), kwargs=kwargs)
    proc.start()

    payload: Optional[tuple[str, Any]] = None
    started = time.monotonic()
    deadline = started + timeout
    try:
        while time.monotonic() < deadline:
            try:
                message = result_queue.get(timeout=0.5)
            except queue.Empty:
                if not proc.is_alive():
                    break
                continue
            if message and message[0] == "pages":
                info = message[1] if isinstance(message[1], dict) else {}
                scaled = int(info.get("timeout") or timeout)
                if scaled > timeout:
                    logger.info(
                        "Extraction ceiling %ds -> %ds for %s pages: %s",
                        timeout, scaled, info.get("page_count"), file_path.name,
                    )
                    timeout = scaled
                    deadline = started + timeout
                continue
            payload = message
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
                "Extraction timed out after %ds, will retry: %s", timeout, file_path.name
            )
            raise ConversionError(
                f"{EXTRACT_TIMEOUT_ERROR_PREFIX} after {timeout}s (child killed)",
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
