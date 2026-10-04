"""Prometheus metrics for extraction: which library, which settings, and what
it reported (spec L5).

``/metrics`` is unauthenticated (docs/operations.md), so no label carries
document data: versions, the configured text modes and OCR engine, and
closed sets. The library's warnings are mapped to a fixed set of classes
(``warning_class``); their text never becomes a label. Extraction runs in a
forked child whose registry dies with it, so the parent (the uploader) counts
each returned ``ExtractedDocument`` once, from its scalar ``extra``
(``pdf:ocr_*``) and its ``warnings``.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, Optional

from sync.document_text import (
    ExtractedDocument,
    docx_text_mode,
    effective_ocr_engine,
    pdf_text_mode,
)

logger = logging.getLogger(__name__)

#: This Knovas Connector's version: ``[project].version`` of pyproject.toml
#: (a test keeps the two equal). The image runs from ``src/`` without
#: installing the package, so there is no distribution metadata to read.
RC_VERSION = "0.3.0"

OTHER = "other"
#: ``rc_extract_warnings_total{class}``.
WARNING_CLASSES = ("ocr", "layout", "metadata", "markdown", "tables", "sentences", OTHER)
#: ``rc_ocr_pages_total{result}``, each with the ``metadata.extra`` key it counts.
OCR_PAGE_KEYS = (
    ("ocr", "pdf:ocr_pages"),
    ("failed", "pdf:ocr_pages_failed"),
    ("skipped", "pdf:ocr_pages_skipped"),
)
#: ``rc_build_info`` labels, in this order.
BUILD_INFO_LABELS = (
    "rc_version",
    "knovas_extract_version",
    "pdf_text_mode",
    "docx_text_mode",
    "ocr_engine",
)

#: Library warning -> class; the first match wins. Read off knovas-extract's
#: own texts (``warnings.append(...)`` in src/knovas_extract): markdown first
#: ("pdf: content.markdown omitted for OCR output" is about the markdown), the
#: prefixes before the keyword rules.
_WARNING_RULES = (
    ("markdown", re.compile(r"^markdown:|content\.markdown")),
    ("sentences", re.compile(r"^sentences:")),
    ("metadata", re.compile(r"^metadata:|^pdf: xmp metadata")),
    ("layout", re.compile(r"^text_mode=|^(?:pdf|docx): layout")),
    ("ocr", re.compile(r"(?<![a-z])ocr(?![a-z])")),
    ("tables", re.compile(r"(?<![a-z])tables?(?![a-z])")),
)


class _Noop:
    def labels(self, *args: Any, **kwargs: Any) -> "_Noop":  # noqa: ARG002
        return self

    def inc(self, amount: float = 1) -> None:  # noqa: ARG002 - mirrors prometheus_client
        return None

    def set(self, value: float) -> None:  # noqa: ARG002 - mirrors prometheus_client
        return None

    def clear(self) -> None:
        return None


def _metric(kind: str, name: str, documentation: str, labels: tuple[str, ...] = ()):
    try:
        import prometheus_client

        return getattr(prometheus_client, kind)(name, documentation, list(labels))
    except ImportError:
        return _Noop()
    except ValueError:
        # Already registered (module re-imported under a test runner).
        from prometheus_client import REGISTRY

        existing = getattr(REGISTRY, "_names_to_collectors", {}).get(name)
        return existing if existing is not None else _Noop()


BUILD_INFO = _metric(
    "Gauge",
    "rc_build_info",
    "Always 1: this Knovas Connector's version, its knovas-extract version, the "
    "PDF and DOCX text modes and the OCR engine (off when OCR is disabled)",
    BUILD_INFO_LABELS,
)
OCR_PAGES = _metric(
    "Counter",
    "rc_ocr_pages_total",
    "PDF pages by OCR result: ocr (text from OCR), failed (left empty), skipped "
    "(time budget, page or pixel cap, no OCR engine)",
    ("result",),
)
OCR_SECONDS = _metric(
    "Counter",
    "rc_ocr_seconds_total",
    "Wall-clock seconds knovas-extract spent on OCR",
)
EXTRACT_WARNINGS = _metric(
    "Counter",
    "rc_extract_warnings_total",
    "knovas-extract warnings by class (never their text)",
    ("class",),
)


def warning_class(text: str) -> str:
    """The class of one knovas-extract warning: one of ``WARNING_CLASSES``,
    ``other`` for anything the rules do not know."""
    if not isinstance(text, str):
        return OTHER
    lowered = text.strip().lower()
    for name, pattern in _WARNING_RULES:
        if pattern.search(lowered):
            return name
    return OTHER


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    return max(0, int(value))


def _seconds(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    seconds = float(value)
    return seconds if math.isfinite(seconds) and seconds > 0 else 0.0


def record_extraction(doc: ExtractedDocument) -> None:
    """Count one extracted document: OCR pages by result, OCR seconds and the
    library's warnings by class. Called once per extraction, in the process
    that serves ``/metrics``. Never raises."""
    try:
        extra = getattr(doc, "extra", None) or {}
        for result, key in OCR_PAGE_KEYS:
            pages = _count(extra.get(key))
            if pages:
                OCR_PAGES.labels(result).inc(pages)
        seconds = _seconds(extra.get("pdf:ocr_seconds"))
        if seconds:
            OCR_SECONDS.inc(seconds)
        for text in getattr(doc, "warnings", None) or ():
            EXTRACT_WARNINGS.labels(warning_class(text)).inc()
    except Exception as exc:  # noqa: BLE001 - a metric never fails an upload
        logger.debug("extraction metrics not counted: %s", type(exc).__name__)


def knovas_extract_version() -> Optional[str]:
    """``knovas_extract.__version__``; None when the library cannot say."""
    try:
        import knovas_extract
    except Exception:  # noqa: BLE001 - reported as unknown, never fatal
        return None
    version = getattr(knovas_extract, "__version__", None)
    return version if isinstance(version, str) and version else None


def knovas_extract_commit() -> Optional[str]:
    """The git commit knovas-extract was installed from; None for a release
    from PyPI (``sync.extraction_stamp.knovas_extract_commit``)."""
    from sync import extraction_stamp  # imported late: no cycle through document_text

    return extraction_stamp.knovas_extract_commit()


def extraction_info() -> dict[str, Optional[str]]:
    """The extractor and its settings: the ``extraction`` block of
    ``GET /sync/status`` and, but for the commit, the labels of
    ``rc_build_info``. ``ocr_engine`` is ``off`` while
    ``RC_PDF_OCR_ENABLED`` is false."""
    return {
        "knovas_extract_version": knovas_extract_version(),
        "knovas_extract_commit": knovas_extract_commit(),
        "pdf_text_mode": pdf_text_mode(),
        "docx_text_mode": docx_text_mode(),
        "ocr_engine": effective_ocr_engine(),
    }


def set_build_info() -> None:
    """``rc_build_info`` = 1 under the current labels, replacing any earlier
    label set (one series per process). Called once at app start."""
    info = extraction_info()
    try:
        BUILD_INFO.clear()
        BUILD_INFO.labels(
            RC_VERSION,
            info["knovas_extract_version"] or "unknown",
            info["pdf_text_mode"],
            info["docx_text_mode"],
            info["ocr_engine"],
        ).set(1)
    except Exception as exc:  # noqa: BLE001 - a metric never stops the app
        logger.debug("rc_build_info not set: %s", type(exc).__name__)


def label_sets() -> dict[str, tuple[str, ...]]:
    """The closed label values of the counters (tests pin them)."""
    return {
        "rc_ocr_pages_total": tuple(result for result, _ in OCR_PAGE_KEYS),
        "rc_extract_warnings_total": WARNING_CLASSES,
    }
