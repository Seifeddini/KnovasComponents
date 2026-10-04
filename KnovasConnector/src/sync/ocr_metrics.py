"""Prometheus counters for extraction, OCR and the OCR disk cache.

Counts only — never document text (GI-EXTRACT-04). Cache hits and misses
happen inside the forked extraction child, whose registry dies with it, so
the child reports its numbers in ``ExtractedDocument.extra`` and the parent
(the uploader) feeds them into these counters.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class _NoopCounter:
    def inc(self, amount: float = 1) -> None:  # noqa: ARG002 - mirrors prometheus_client
        return None


def _counter(name: str, documentation: str):
    try:
        from prometheus_client import Counter

        return Counter(name, documentation)
    except ImportError:
        return _NoopCounter()
    except ValueError:
        # Already registered (module re-imported under a test runner).
        from prometheus_client import REGISTRY

        existing = getattr(REGISTRY, "_names_to_collectors", {}).get(name)
        return existing if existing is not None else _NoopCounter()


OCR_CACHE_HITS = _counter("rc_ocr_cache_hits_total", "OCR disk cache hits (per page image)")
OCR_CACHE_MISSES = _counter("rc_ocr_cache_misses_total", "OCR disk cache misses (per page image)")
OCR_PARTIAL = _counter(
    "rc_ocr_partial_total",
    "Documents recorded partial (OCR pages skipped, or extraction retries exhausted)",
)
OCR_BACKEND_DEGRADED = _counter(
    "rc_ocr_backend_degraded_total",
    "PDFs returned without an OCR backend although OCR was configured",
)
EXTRACT_RETRIES = _counter(
    "rc_extract_retry_total",
    "Extraction failures recorded as retryable (wall-clock kill, killed child, transient error)",
)
SKIP_UNCONVERTIBLE = _counter(
    "rc_skip_unconvertible_total",
    "Files parked as skip:unconvertible because the library flagged the input",
)
