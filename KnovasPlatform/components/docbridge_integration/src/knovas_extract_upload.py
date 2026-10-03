"""Build secured transmit parts from raw document bytes via knovas-extract.

Admin uploads (``/admin/documents``) mirror the RemoteController's sync
pipeline (``RemoteController/src/sync/document_text.py`` and
``knovas_uploader.py``) so a document uploaded here and the same document
synced by the RC reach the server in the same wire format:

* ``emit_markdown=False`` — markdown cost ~8 s per document and its expansion
  guard parked every mixed PDF and every DOCX with large tables (plan M0).
* The 0.4 keywords ``text_mode=`` and ``ocr=`` are sent only when
  ``extract_accepts(name)`` says the installed library takes them; the OCR
  page cap, time budget and page timeout live on ``Limits`` in 0.4.0a1 and
  are routed there the same way (``build_ocr_limits``). The same source runs
  against today's release and against 0.4.
* ``RC_PDF_TEXT_MODE`` plain | shadow | layout, ``RC_OCR_*`` and
  ``RC_TESSERACT_LANG`` carry the RC's names (plan §9: "Platform mirrors the
  names"). Shadow mode uploads the plain rendering and logs ONE numbers-only
  ``ShadowDiff`` against the layout rendering; both passes share one
  in-memory OCR cache object so every page is OCR'd once (plan D13). The
  Platform has no disk cache — nothing of the document is written to disk
  (GI-EXTRACT-04).
* Page-break markers (GI-INGEST-17) via ``knovas_transmit.page_markers``
  behind ``RC_PAGE_BREAK_MARKERS`` (default on); the context sidecar is
  always written from the UNMARKED text. No ``tables`` payload for PDF
  parts unless ``RC_SEND_PDF_TABLES`` is set (the server drops them at the
  Redis buffer).
* ``metadata.extra`` is read defensively for the OCR counts, by the
  Connector's partial rule (``partial_note_for``, spec E1); a ``partial``
  note (counts only) is surfaced in the upload result, the log and the
  sidecar (GI-EXTRACT-02).
* Extraction runs in a guarded child process (``extract_guarded``): wall
  clock ``RC_EXTRACT_TIMEOUT_SECONDS`` (default 120 s — an admin upload is
  one interactive request), ``nice 10``, ``RLIMIT_AS``, conservative OCR
  defaults (``workers=1``, ``max_ocr_pages=50``, 60 s budget). A gunicorn
  request thread must never run an unbounded ``extract()`` (plan §6,
  ``[C-perf-4]``). The timeout message starts with
  ``EXTRACT_TIMEOUT_ERROR_PREFIX`` and never with "resource limit exceeded",
  the prefix that means "the library flagged the input".
"""
from __future__ import annotations

import base64
import inspect
import logging
import multiprocessing as mp
import os
import queue
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from context_store import write_context_sidecar
from knovas_transmit.chunking import build_transmission_parts
from knovas_transmit.table_payload import map_extractor_tables

logger = logging.getLogger(__name__)

try:
    import knovas_extract
    from knovas_extract import extract
except ImportError:  # pragma: no cover - exercised where the library is absent
    knovas_extract = None  # type: ignore[assignment]
    extract = None  # type: ignore[assignment]

#: knovas-extract >= 0.4 exposes the OCR options class; today's release does
#: not, and then `ocr=` is never sent.
OcrOptions = getattr(knovas_extract, "OcrOptions", None)


def extract_accepts(param: str) -> bool:
    """Whether the installed knovas-extract's ``extract()`` takes ``param``.

    OCR keywords arrived after 0.2, ``text_mode=`` and ``ocr=`` arrive with
    0.4. Passing a keyword to an older extractor raises TypeError, which this
    module's broad except turns into "no parts" -- a PDF that ingests as an
    empty document rather than an error anyone would notice. Asking the
    function what it accepts keeps that from happening quietly.

    A version that cannot be introspected is treated as accepting them:
    dropping OCR silently would ingest scanned PDFs as empty documents, and
    a loud TypeError is the better failure.
    """
    if extract is None:
        return False
    try:
        return param in inspect.signature(extract).parameters
    except (TypeError, ValueError):
        return True


def extract_accepts_ocr() -> bool:
    """Whether the installed knovas-extract takes the OCR keywords."""
    return extract_accepts("use_ocr")


_EXT_TO_MIME = {
    ".txt": "text/plain",
    ".md": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".eml": "message/rfc822",
    ".msg": "application/vnd.ms-outlook",
}

_PART_MAX_CHARS = 50_000

# Wall-clock ceiling for one admin upload's extraction (child process).
# Override with RC_EXTRACT_TIMEOUT_SECONDS; 0 extracts in-process with no
# ceiling. Lower than the RC's 300 s: an admin upload is one interactive
# request, and gunicorn's --timeout has to stay above it. There is no
# per-page scaling here -- a 300-page scan belongs to the RC, not to a
# browser request.
DEFAULT_EXTRACT_TIMEOUT_SECONDS = 120

#: Prefix of the error raised when the child is killed on the wall-clock
#: ceiling. It deliberately does NOT start with "resource limit exceeded":
#: that prefix means "the library flagged the input"; a kill is the
#: Platform's doing and the document is worth another attempt.
EXTRACT_TIMEOUT_ERROR_PREFIX = "extraction timeout"

# Address-space ceiling for the extraction child (RLIMIT_AS). Override with
# RC_EXTRACT_RLIMIT_AS_MB; 0 disables. The child also runs at nice 10.
DEFAULT_EXTRACT_RLIMIT_AS_MB = 2048
EXTRACT_CHILD_NICE = 10

# Conservative OCR defaults for the admin path (plan §6): one worker, a
# 50-page cap and a 60 s budget inside the 120 s ceiling. The RC's names are
# honoured (RC_OCR_*), the defaults differ.
DEFAULT_TESSERACT_LANG = "deu+eng"
#: Language packs joined by "+" (the Connector's rule, spec E5).
_TESSERACT_LANG_RE = re.compile(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*")
DEFAULT_OCR_ENGINE = "auto"
OCR_ENGINES = ("auto", "tesserocr", "cli", "mupdf")
# RC_OCR_DPI: unset sends no dpi (the library's native-resolution rule).
OCR_DPI_MIN = 30
OCR_DPI_MAX = 1200
DEFAULT_OCR_WORKERS = 1
DEFAULT_OCR_MAX_WORKERS = 8
DEFAULT_OCR_MAX_PAGES = 50
DEFAULT_OCR_TIME_BUDGET_SECONDS = 60
DEFAULT_OCR_PAGE_TIMEOUT_SECONDS = 30
MIN_OCR_TIME_BUDGET_SECONDS = 10
# Margin the budget derivation keeps for rendering and reassembly.
_OCR_BUDGET_RENDER_MARGIN_SECONDS = 10
_OCR_BUDGET_DEFAULT_HEADROOM_SECONDS = 30

TEXT_MODES = ("plain", "shadow", "layout")
DEFAULT_PDF_TEXT_MODE = "layout"

logger_ocr_warned = False
logger_text_mode_warned = False
logger_shadow_warned = False


class ExtractionError(Exception):
    """Extraction failed for one document; ``str(exc)`` is safe to log
    (library error text or a Platform message, never document content)."""


def is_timeout_error(error: str | None) -> bool:
    """True for the Platform's own wall-clock kill (retryable, never the
    library's verdict on the input)."""
    return bool(error) and str(error).lower().startswith(EXTRACT_TIMEOUT_ERROR_PREFIX)


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
    """``raw`` as an int when it is ASCII digits only, else None (the
    Connector's rule)."""
    return int(raw) if raw.isascii() and raw.isdigit() else None


def _env_int_at_least(name: str, default: int, minimum: int) -> int:
    """A whole-number setting of at least ``minimum``; anything else logs one
    warning naming the setting and uses the default (the Connector's rule)."""
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


def extract_timeout_seconds() -> int:
    """Wall-clock ceiling for one document's extraction; 0 disables."""
    return _env_int("RC_EXTRACT_TIMEOUT_SECONDS", DEFAULT_EXTRACT_TIMEOUT_SECONDS)


def extract_rlimit_as_bytes() -> int:
    """Address-space limit for the extraction child; 0 disables."""
    return _env_int("RC_EXTRACT_RLIMIT_AS_MB", DEFAULT_EXTRACT_RLIMIT_AS_MB) * 1024 * 1024


def page_break_markers_enabled() -> bool:
    """``RC_PAGE_BREAK_MARKERS`` (default true): form feeds before every text
    page start inside a part, so the server serves hits on their own page
    (GI-INGEST-17)."""
    return _env_flag("RC_PAGE_BREAK_MARKERS", True)


def send_pdf_tables_enabled() -> bool:
    """``RC_SEND_PDF_TABLES`` (default false): the server drops ``tables`` at
    the Redis buffer -- only snippet, page and sentence survive -- so for
    PDFs the rows have to live in the text and the payload is wasted bytes."""
    return _env_flag("RC_SEND_PDF_TABLES", False)


def tesseract_language(default: str = DEFAULT_TESSERACT_LANG) -> str:
    """``RC_TESSERACT_LANG`` when set and valid, else the caller's language
    (the Platform config's ``advanced.extraction.ocr_language``) when valid,
    else ``deu+eng``. Valid is language packs joined by ``+``; anything else
    logs one warning naming the setting and is skipped -- ``OcrOptions``
    would refuse it and the upload would carry no text (spec E5)."""
    raw = (os.environ.get("RC_TESSERACT_LANG") or "").strip()
    if raw:
        if _TESSERACT_LANG_RE.fullmatch(raw):
            return raw
        logger.warning("Invalid RC_TESSERACT_LANG=%r; using the configured language", raw)
    configured = (default or "").strip()
    if not configured:
        return DEFAULT_TESSERACT_LANG
    if _TESSERACT_LANG_RE.fullmatch(configured):
        return configured
    logger.warning(
        "Invalid advanced.extraction.ocr_language=%r; using %s", configured, DEFAULT_TESSERACT_LANG
    )
    return DEFAULT_TESSERACT_LANG


def pdf_text_mode() -> str:
    """``RC_PDF_TEXT_MODE``: plain (default) | shadow | layout."""
    raw = (os.environ.get("RC_PDF_TEXT_MODE") or "").strip().lower()
    if not raw:
        return DEFAULT_PDF_TEXT_MODE
    if raw in TEXT_MODES:
        return raw
    logger.warning("Invalid RC_PDF_TEXT_MODE=%r; using %s", raw, DEFAULT_PDF_TEXT_MODE)
    return DEFAULT_PDF_TEXT_MODE


def ocr_engine() -> str:
    raw = (os.environ.get("RC_OCR_ENGINE") or "").strip().lower()
    if not raw:
        return DEFAULT_OCR_ENGINE
    if raw in OCR_ENGINES:
        return raw
    logger.warning("Invalid RC_OCR_ENGINE=%r; using %s", raw, DEFAULT_OCR_ENGINE)
    return DEFAULT_OCR_ENGINE


def ocr_dpi() -> Optional[int]:
    """``RC_OCR_DPI``, the Connector's rule (spec E3): unset sends no dpi --
    native resolution, at most 300, never upsampled; set must be 30-1200,
    anything else logs one warning and counts as unset."""
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


def ocr_workers() -> int:
    """``RC_OCR_WORKERS``, default 1 (a gunicorn worker shares the host with
    the search UI), at most 8."""
    return max(1, min(DEFAULT_OCR_MAX_WORKERS, _env_int("RC_OCR_WORKERS", DEFAULT_OCR_WORKERS, minimum=1)))


def ocr_time_budget_seconds(timeout_seconds: int, page_timeout_seconds: int) -> int:
    """The OCR time budget the child hands the library.

    Default ``min(60, timeout - 30)`` (``RC_OCR_TIME_BUDGET_SECONDS``
    overrides), never more than ``timeout - page_timeout - 10``, at least
    10 s: the pool stops SUBMITTING when the budget trips, the pages already
    running finish in parallel within one page timeout, and the partial
    result has to reach the parent before the wall-clock kill (plan
    ``[C-sec-0]``; the Connector's rule, spec E2 -- the cap used to subtract
    ``workers × page_timeout``, which only the default of one worker kept
    harmless). With the ceiling disabled (``timeout == 0``) the env value or
    60 s applies as is.
    """
    configured = _env_int("RC_OCR_TIME_BUDGET_SECONDS", -1, minimum=-1)
    if timeout_seconds <= 0:
        return configured if configured >= 0 else DEFAULT_OCR_TIME_BUDGET_SECONDS
    budget = configured if configured >= 0 else min(
        DEFAULT_OCR_TIME_BUDGET_SECONDS, timeout_seconds - _OCR_BUDGET_DEFAULT_HEADROOM_SECONDS
    )
    hard_cap = timeout_seconds - page_timeout_seconds - _OCR_BUDGET_RENDER_MARGIN_SECONDS
    return max(MIN_OCR_TIME_BUDGET_SECONDS, min(budget, hard_cap))


def ocr_options_kwargs(timeout_seconds: Optional[int] = None, language: str = DEFAULT_TESSERACT_LANG) -> dict[str, Any]:
    """Keyword arguments for the library's OCR configuration, from the
    environment: ``RC_OCR_ENGINE``, ``RC_OCR_DPI``, ``RC_OCR_WORKERS``,
    ``RC_OCR_MAX_PAGES``, ``RC_OCR_TIME_BUDGET_SECONDS`` (derived from the
    extraction timeout when unset), ``RC_OCR_PAGE_TIMEOUT_SECONDS``,
    ``RC_TESSERACT_LANG``. The cache is added by the caller (``cache=``).
    """
    timeout = extract_timeout_seconds() if timeout_seconds is None else int(timeout_seconds)
    page_timeout = _env_int_at_least("RC_OCR_PAGE_TIMEOUT_SECONDS", DEFAULT_OCR_PAGE_TIMEOUT_SECONDS, 1)
    options: dict[str, Any] = {
        "engine": ocr_engine(),
        "workers": ocr_workers(),
        "max_ocr_pages": _env_int_at_least("RC_OCR_MAX_PAGES", DEFAULT_OCR_MAX_PAGES, 1),
        "time_budget_seconds": ocr_time_budget_seconds(timeout, page_timeout),
        "page_timeout_seconds": page_timeout,
        "language": tesseract_language(language),
    }
    dpi = ocr_dpi()
    if dpi is not None:
        options["dpi"] = dpi
    return options


#: The library's field names are introspected; these are the spellings the
#: plan uses (§3 budgets, §6 ``OcrOptions(workers=1, max_ocr_pages=50, …)``)
#: plus the obvious variants, so a renamed field lands instead of raising.
_OCR_OPTION_ALIASES: dict[str, tuple[str, ...]] = {
    "engine": ("engine", "backend"),
    "dpi": ("dpi", "render_dpi", "max_dpi"),
    "workers": ("workers", "max_workers", "max_ocr_workers"),
    "max_ocr_pages": ("max_ocr_pages", "max_pages"),
    "time_budget_seconds": ("time_budget_seconds", "ocr_time_budget_seconds", "time_budget"),
    "page_timeout_seconds": ("page_timeout_seconds", "ocr_page_timeout_seconds", "page_timeout"),
    "language": ("language", "lang", "languages", "ocr_language"),
    "cache": ("cache",),
}

#: In 0.4.0a1 the page cap, the budgets and the worker ceiling are
#: ``Limits`` fields, not ``OcrOptions`` fields. Whatever ``OcrOptions``
#: does not take is offered to ``Limits`` under these spellings.
_OCR_LIMIT_ALIASES: dict[str, tuple[str, ...]] = {
    "max_ocr_pages": ("max_ocr_pages",),
    "time_budget_seconds": ("ocr_time_budget_seconds",),
    "page_timeout_seconds": ("ocr_page_timeout_seconds",),
    "workers": ("max_ocr_workers",),
}


def _signature_params(cls: Any) -> Optional[Any]:
    try:
        return inspect.signature(cls).parameters
    except (TypeError, ValueError):
        return None


def build_ocr_options(options: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Instantiate the library's ``OcrOptions`` with the fields it accepts.

    Returns ``(options_or_None, leftovers)``; the leftovers are the fields
    the class does not take (offered to ``Limits`` by ``build_ocr_limits``).
    """
    cls = OcrOptions
    if cls is None:
        return None, dict(options)
    params = _signature_params(cls)
    if params is None or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return cls(**options), {}
    accepted: dict[str, Any] = {}
    leftovers: dict[str, Any] = {}
    for ours, value in options.items():
        for name in _OCR_OPTION_ALIASES.get(ours, (ours,)):
            if name in params:
                accepted[name] = value
                break
        else:
            leftovers[ours] = value
    if leftovers:
        logger.debug("OcrOptions does not take %s", ", ".join(sorted(leftovers)))
    return cls(**accepted), leftovers


def build_ocr_limits(leftovers: dict[str, Any]) -> Any:
    """A ``Limits`` carrying the OCR budgets ``OcrOptions`` did not take, or
    None when the library's ``Limits`` has no such fields either (then the
    values stay at the library's defaults, logged once at debug)."""
    cls = getattr(knovas_extract, "Limits", None) if knovas_extract is not None else None
    if cls is None or not leftovers or not extract_accepts("limits"):
        return None
    params = _signature_params(cls)
    if params is None:
        return None
    fields: dict[str, Any] = {}
    dropped: list[str] = []
    for ours, value in leftovers.items():
        if ours == "cache":
            continue
        for name in _OCR_LIMIT_ALIASES.get(ours, ()):
            if name in params:
                fields[name] = value
                break
        else:
            dropped.append(ours)
    if dropped:
        logger.debug("neither OcrOptions nor Limits take %s; left at library defaults", ", ".join(dropped))
    if not fields:
        return None
    return cls(**fields)


# --- extracted content -------------------------------------------------------


class MemoryOcrCache:
    """Process-local, per-document OCR cache: what the shadow run's second
    rendering hits (plan decision D13). The Platform keeps no disk cache --
    nothing of the document is written to disk (GI-EXTRACT-04)."""

    def __init__(self) -> None:
        self._entries: dict[str, str] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[str]:
        value = self._entries.get(str(key))
        if value is None:
            self.misses += 1
            return None
        self.hits += 1
        return value

    def put(self, key: str, value: str) -> None:
        if isinstance(value, str):
            self._entries[str(key)] = value

    def close(self) -> None:
        self._entries.clear()


@dataclass
class ExtractedContent:
    """What the extraction child hands back: text + structure + scalar
    metadata. ``extra`` holds the scalar ``metadata.extra`` entries
    (``pdf:ocr_pages``, ``pdf:ocr_pages_skipped``, ``pdf:ocr_backend``, ...)
    plus ``platform:ocr_cache_hits`` / ``platform:ocr_cache_misses``. Counts
    and identifiers only, never text."""

    text: str
    sentences: Optional[list[Any]] = None
    sections: Optional[list[Any]] = None
    pages: Optional[list[Any]] = None
    tables: Optional[list[Any]] = None
    extra: dict[str, Any] = field(default_factory=dict)
    page_count: Optional[int] = None
    text_mode: str = DEFAULT_PDF_TEXT_MODE


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
#: note key -> metadata.extra key. Same as the Connector.
_PARTIAL_NOTE_COUNTS = (
    ("ocr_pages_skipped", "pdf:ocr_pages_skipped"),
    ("ocr_pages_failed", "pdf:ocr_pages_failed"),
    ("ocr_pages", "pdf:ocr_pages"),
    ("text_pages", "pdf:text_pages"),
)


def partial_note_for(extra: Optional[dict[str, Any]], *, expect_ocr: bool) -> Optional[dict[str, Any]]:
    """The partial note for a returned document, or None when it is complete.

    The Connector's rule (``RemoteController/src/sync/document_text.py``
    ``partial_note_for``, spec E1): partial when the library counted skipped
    OCR pages (page cap, budget, pixel cap, or no OCR engine -- 0.4 counts
    every page that needed OCR as skipped then), or failed OCR pages (a
    failed page is an empty page), or -- only for a library that does not
    count skipped pages -- when OCR was expected and it reports
    ``pdf:ocr_backend == "none"`` without ``pdf:ocr_pages_skipped``. A
    born-digital PDF reports backend ``none`` with zero skipped pages: no
    page needed OCR, so it is complete. Counts and the backend name only.
    """
    extra = extra or {}
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
        os.path.basename(document_key) if document_key else "-",
        " ".join(f"{k}={v}" for k, v in fields.items()),
    )


# --- extraction ----------------------------------------------------------------


def _pdf_extract_kwargs(
    *,
    use_ocr: bool | str,
    ocr_language: str,
    timeout_seconds: Optional[int],
) -> tuple[dict[str, Any], bool, Any, str]:
    """PDF keywords for ``extract()``: OCR switch, options (+ limits, + cache),
    text mode.

    Returns ``(kwargs, shadow, cache, mode)``; ``shadow`` says a second
    ``layout`` pass is due, ``cache`` is the OCR cache object both passes
    share, ``mode`` the effective text mode.
    """
    global logger_ocr_warned, logger_text_mode_warned, logger_shadow_warned
    kwargs: dict[str, Any] = {}
    cache: Any = None
    if extract_accepts_ocr():
        kwargs["use_ocr"] = use_ocr
        kwargs["ocr_language"] = tesseract_language(ocr_language)
    elif use_ocr and not logger_ocr_warned:
        logger_ocr_warned = True
        logger.warning(
            "PDF OCR requested but the installed knovas-extract does not "
            "accept it; continuing without OCR. Upgrade knovas-extract to a "
            "release with OCR support."
        )
    if use_ocr and OcrOptions is not None and extract_accepts("ocr"):
        cache = MemoryOcrCache()
        options, leftovers = build_ocr_options(
            {**ocr_options_kwargs(timeout_seconds, tesseract_language(ocr_language)), "cache": cache}
        )
        if options is not None:
            kwargs["ocr"] = options
        limits = build_ocr_limits(leftovers)
        if limits is not None:
            kwargs["limits"] = limits

    mode = pdf_text_mode()
    if mode == "plain":
        return kwargs, False, cache, "plain"
    if not extract_accepts("text_mode"):
        if not logger_text_mode_warned:
            logger_text_mode_warned = True
            logger.warning(
                "RC_PDF_TEXT_MODE=%s but the installed knovas-extract has no text_mode; using plain",
                mode,
            )
        return kwargs, False, cache, "plain"
    if mode == "layout":
        kwargs["text_mode"] = "layout"
        return kwargs, False, cache, "layout"
    # shadow: plain is uploaded, layout is rendered from the same OCR cache
    if use_ocr and "ocr" not in kwargs:
        if not logger_shadow_warned:
            logger_shadow_warned = True
            logger.warning(
                "RC_PDF_TEXT_MODE=shadow needs an OCR cache the installed knovas-extract "
                "cannot take (no ocr=); running plain only so no page is OCR'd twice"
            )
        return kwargs, False, cache, "plain"
    if cache is None:
        cache = MemoryOcrCache()
    kwargs["text_mode"] = "plain"
    return kwargs, True, cache, "shadow"


def _extract_bytes(
    raw: bytes,
    ext: str,
    *,
    use_ocr: bool | str = "auto",
    ocr_language: str = DEFAULT_TESSERACT_LANG,
    timeout_seconds: Optional[int] = None,
    document_key: Optional[str] = None,
) -> ExtractedContent:
    """One ``extract()`` call (plus the shadow pass) over in-memory bytes."""
    if extract is None:
        raise ExtractionError("knovas-extract not installed")
    mime = _EXT_TO_MIME.get(ext)
    if mime is None:
        raise ExtractionError(f"unsupported extension: {ext}")

    extract_kwargs: dict[str, Any] = {
        "mime": mime,
        "emit_sentences": True,
        # Never: ~8 s per document, and the expansion guard parked every mixed
        # PDF and every DOCX with large tables (plan M0).
        "emit_markdown": False,
    }
    shadow = False
    cache: Any = None
    mode = "plain"
    if ext == ".pdf":
        pdf_kwargs, shadow, cache, mode = _pdf_extract_kwargs(
            use_ocr=use_ocr, ocr_language=ocr_language, timeout_seconds=timeout_seconds
        )
        extract_kwargs.update(pdf_kwargs)

    started = time.monotonic()
    try:
        result = extract(raw, **extract_kwargs)
    except Exception as exc:
        raise ExtractionError(f"{type(exc).__name__}: {exc}") from exc
    seconds_plain = time.monotonic() - started

    content = result.content
    text = str(content.text or "")
    extra = _scalar_extra(result.metadata)
    if shadow and text.strip():
        _run_shadow_pass(raw, extract_kwargs, text, extra, seconds_plain, document_key)
    if cache is not None:
        extra["platform:ocr_cache_hits"] = int(getattr(cache, "hits", 0) or 0)
        extra["platform:ocr_cache_misses"] = int(getattr(cache, "misses", 0) or 0)
        close = getattr(cache, "close", None)
        if callable(close):
            close()

    return ExtractedContent(
        text=text,
        sentences=list(content.sentences) if content.sentences else None,
        sections=list(content.sections) if content.sections else None,
        pages=list(content.pages) if content.pages else None,
        tables=list(content.tables) if getattr(content, "tables", None) else None,
        extra=extra,
        page_count=_int_or_none(getattr(result.metadata, "page_count", None)),
        text_mode=mode,
    )


def _run_shadow_pass(
    raw: bytes,
    plain_kwargs: dict[str, Any],
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


# --- the guarded child -------------------------------------------------------


def _apply_child_limits() -> None:
    """Lower the extraction child's priority and cap its address space.

    Runs first thing in the forked child. ``nice 10`` keeps a long OCR from
    starving the gunicorn workers serving the search UI; ``RLIMIT_AS``
    (``RC_EXTRACT_RLIMIT_AS_MB``, 2 GiB by default) turns a pixel bomb or a
    runaway decode into "extractor died" instead of an OOM kill of the whole
    container. The soft limit is set; a hard limit already below it is
    respected.
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

        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY and hard < limit:
            limit = hard
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    except (ImportError, ValueError, OSError) as exc:
        logger.warning("RLIMIT_AS not applied to the extraction child: %s", type(exc).__name__)


def _extract_child(
    raw: bytes,
    ext: str,
    use_ocr: bool | str,
    ocr_language: str,
    timeout_seconds: int,
    document_key: Optional[str],
    result_queue: Any,
) -> None:
    """Run in a child process so a runaway extractor can be killed at the OS
    level -- a pure-Python hot loop (pysbd) or a C call that never yields
    cannot be interrupted from a gunicorn request thread."""
    _apply_child_limits()
    try:
        content = _extract_bytes(
            raw, ext,
            use_ocr=use_ocr, ocr_language=ocr_language,
            timeout_seconds=timeout_seconds, document_key=document_key,
        )
        result_queue.put(("ok", content))
    except ExtractionError as exc:
        result_queue.put(("error", str(exc)))
    except Exception as exc:  # noqa: BLE001 - report any failure to the parent
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def extract_guarded(
    raw: bytes,
    ext: str,
    *,
    use_ocr: bool | str = "auto",
    ocr_language: str = DEFAULT_TESSERACT_LANG,
    document_key: Optional[str] = None,
) -> ExtractedContent:
    """``_extract_bytes`` with a wall-clock ceiling, in a child process.

    An admin upload used to run ``extract()`` in the gunicorn request thread
    with no timeout at all: one hung OCR page held a worker thread until the
    container was restarted. On timeout raises
    ``ExtractionError("extraction timeout after …")`` -- a message that does
    NOT start with "resource limit exceeded", so nothing takes the kill for
    the library's verdict on the input (GI-EXTRACT-02).

    Set ``RC_EXTRACT_TIMEOUT_SECONDS=0`` to disable and extract in-process.
    """
    timeout = extract_timeout_seconds()
    if timeout <= 0:
        return _extract_bytes(
            raw, ext, use_ocr=use_ocr, ocr_language=ocr_language,
            timeout_seconds=0, document_key=document_key,
        )

    ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context("spawn")
    # Queue payloads are pickled, but both ends are our own processes -- no
    # untrusted data crosses this boundary.
    result_queue = ctx.Queue()
    proc = ctx.Process(
        target=_extract_child,
        args=(raw, ext, use_ocr, ocr_language, timeout, document_key, result_queue),
    )
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
                    # A last look: the child may have put its result right
                    # before exiting.
                    try:
                        payload = result_queue.get(timeout=0.5)
                    except queue.Empty:
                        pass
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
                "Extraction timed out after %ds (child killed): %s",
                timeout, os.path.basename(document_key) if document_key else "-",
            )
            raise ExtractionError(f"{EXTRACT_TIMEOUT_ERROR_PREFIX} after {timeout}s (child killed)")
        raise ExtractionError(f"extractor died (exit {proc.exitcode})")

    kind, value = payload
    if kind == "ok":
        return value
    raise ExtractionError(str(value))


# --- parts ---------------------------------------------------------------------


@dataclass
class ExtractedParts:
    """The admin upload's extraction outcome: the transmit parts, the
    partial note (counts only, None when complete), the scalar metadata and
    the error message when nothing could be built."""

    parts: List[Dict[str, Any]] = field(default_factory=list)
    partial: Optional[dict[str, Any]] = None
    extra: dict[str, Any] = field(default_factory=dict)
    text_mode: str = DEFAULT_PDF_TEXT_MODE
    error: Optional[str] = None


def _normalize_ext(ext: str) -> str:
    return (ext or "").strip().lower().lstrip(".")


def _context_store_dir() -> str:
    return (os.environ.get("SEARCH_CONTEXT_STORE_PATH") or "").strip()


def extract_parts_from_base64(
    content_base64: str,
    ext: str,
    *,
    part_max_chars: int = _PART_MAX_CHARS,
    pointer: str = "",
    path: str = "",
    write_sidecar: bool = True,
    use_ocr: bool | str = "auto",
    ocr_language: str = DEFAULT_TESSERACT_LANG,
) -> ExtractedParts:
    """Decode base64 document bytes and build Knovas transmit parts using the
    knovas-extract surface the RC uses: sentences (page/sentence), sections
    (headings), pages (boundaries + page-break markers) and -- for non-PDF
    input -- structured tables.
    """
    normalized = _normalize_ext(ext)
    dotted = f".{normalized}" if normalized else ""
    if _EXT_TO_MIME.get(dotted) is None:
        return ExtractedParts(error=f"unsupported extension: {dotted or '-'}")

    try:
        raw = base64.b64decode(content_base64, validate=False)
    except Exception as exc:
        logger.warning("knovas-extract upload: base64 decode failed: %s", exc)
        return ExtractedParts(error="base64 decode failed")

    if extract is None:
        logger.warning("knovas-extract not installed; cannot build transmission parts")
        return ExtractedParts(error="knovas-extract not installed")

    document_key = pointer or path or None
    try:
        content = extract_guarded(
            raw, dotted, use_ocr=use_ocr, ocr_language=ocr_language, document_key=document_key
        )
    except ExtractionError as exc:
        logger.warning("knovas-extract failed for .%s: %s", normalized, exc)
        return ExtractedParts(error=str(exc))
    except Exception as exc:  # noqa: BLE001 - never let an upload 500 on extraction
        logger.warning("knovas-extract failed for .%s: %s: %s", normalized, type(exc).__name__, exc)
        return ExtractedParts(error=f"{type(exc).__name__}: {exc}")

    text = content.text
    if not text.strip():
        return ExtractedParts(error="no extractable text", extra=content.extra, text_mode=content.text_mode)

    expect_ocr = dotted == ".pdf" and bool(use_ocr)
    partial = partial_note_for(content.extra, expect_ocr=expect_ocr)
    if partial:
        logger.info(
            "Admin upload partial for %s: %s",
            os.path.basename(document_key) if document_key else "-",
            " ".join(f"{k}={v}" for k, v in sorted(partial.items())),
        )

    tables = None
    if content.tables and (dotted != ".pdf" or send_pdf_tables_enabled()):
        tables = map_extractor_tables(content.tables, default_hint_prefix=normalized) or None

    if write_sidecar and pointer:
        # Always from the UNMARKED text: the sidecar's offsets are the
        # extractor's, the page-break markers exist only on the wire.
        write_context_sidecar(
            _context_store_dir() or None,
            pointer,
            path or pointer,
            content.text,
            content.sentences,
            partial=partial,
        )

    parts = build_transmission_parts(
        content.text,
        part_max_chars,
        sentences=content.sentences,
        sections=content.sections,
        pages=content.pages,
        tables=tables,
        page_markers=page_break_markers_enabled(),
    )
    return ExtractedParts(parts=parts, partial=partial, extra=content.extra, text_mode=content.text_mode)


def parts_from_base64(
    content_base64: str,
    ext: str,
    *,
    part_max_chars: int = _PART_MAX_CHARS,
    pointer: str = "",
    path: str = "",
    write_sidecar: bool = True,
    use_ocr: bool | str = "auto",
    ocr_language: str = DEFAULT_TESSERACT_LANG,
) -> List[Dict[str, Any]]:
    """The transmit parts alone (see ``extract_parts_from_base64``); an
    empty list when nothing could be built."""
    return extract_parts_from_base64(
        content_base64,
        ext,
        part_max_chars=part_max_chars,
        pointer=pointer,
        path=path,
        write_sidecar=write_sidecar,
        use_ocr=use_ocr,
        ocr_language=ocr_language,
    ).parts
