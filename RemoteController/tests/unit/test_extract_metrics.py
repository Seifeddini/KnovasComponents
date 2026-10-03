"""Extraction metrics and the extractor's version (spec L5).

``/metrics`` is unauthenticated: labels are versions, configured settings and
closed classes -- never a warning's text. The extraction child's registry
dies with it, so the uploader counts each returned document once.
"""
from __future__ import annotations

import math
import tomllib
from pathlib import Path

import pytest

prometheus_client = pytest.importorskip("prometheus_client")

from sync import extract_metrics as em  # noqa: E402
from sync.document_text import ExtractedDocument  # noqa: E402

SENTINEL = "Mandant Sentinel Muster AG"

#: knovas-extract's own warning texts (``warnings.append`` in
#: src/knovas_extract, numbers filled in) and the class of each. The two
#: marked "PR" arrive with the library PR (spec 4.2, 4.4).
REAL_WARNINGS = [
    ("pdf: OCR applied to 7 of 13 pages via tesserocr", "ocr"),
    ("pdf: 2 pages skipped: OCR budget exhausted", "ocr"),
    ("pdf: 1 page skipped: image exceeds max_ocr_image_megapixels", "ocr"),
    ("pdf: 3 pages failed OCR", "ocr"),
    ("pdf: OCR backend unavailable; 4 pages left without OCR", "ocr"),
    ("first page produced no text (OCR may help for scanned PDFs)", "ocr"),
    ("pdf: layout words unavailable on 2 pages; emitted as plain text", "layout"),
    ("pdf: layout rendering failed (ValueError); plain text emitted", "layout"),
    ("text_mode='layout' is implemented for PDF only; plain text emitted", "layout"),
    ("text_mode='layout' is implemented for PDF and DOCX only; plain text emitted", "layout"),  # PR
    ("metadata: 2 values truncated", "metadata"),
    ("metadata: 1 values dropped for NUL / control / bidi-override characters", "metadata"),
    ("metadata: 1 values dropped as unserializable", "metadata"),
    ("pdf: xmp metadata exceeded max_xmp_bytes; skipped", "metadata"),
    ("pdf: xmp metadata unparseable; skipped", "metadata"),
    ("markdown: 2 <script> tags stripped", "markdown"),
    ("markdown: 1 on* event-handler attrs dropped", "markdown"),
    ("docx: mammoth conversion failed; content.markdown left null", "markdown"),
    ("pdf: content.markdown omitted for OCR output (no structure to preserve)", "markdown"),
    ("msg: only RTF body available; content.markdown left null", "markdown"),
    ("docx: tables[0].rows[3] padded from 2 to 3 cells", "tables"),
    ("docx: tables[1].rows[0].[2] truncated at 32768 chars", "tables"),
    ("docx: table extraction failed: KeyError", "tables"),
    ("html: table extraction stopped at 200 tables (spec cap)", "tables"),
    ("pdf: tables[4].rows[2] truncated at 32768 chars (page 12)", "tables"),
    ("pdf: table extraction stopped at 200 tables (spec cap)", "tables"),
    ("pdf: page 3 could not load for table scan (RuntimeError)", "tables"),
    ("pdf: table detection failed on page 2 (ValueError)", "tables"),
    ("pdf: structured table pass failed (ValueError)", "tables"),
    ("sentences: 3 segments could not be located", "sentences"),
    ("sentences: page text could not be aligned to document", "sentences"),
    ("sentences: 120 beyond max_sentences omitted", "sentences"),  # PR
    ("DOCX contains VBA macros; payload ignored (never executed)", "other"),
    ("PDF embedded JavaScript ignored (never executed)", "other"),
    ("Subject header contains embedded newline (header-injection attempt)", "other"),
    ("html: dropped 2 URL(s) with disallowed scheme from meta / link", "other"),
    ("page 4: could not load (cannot load page)", "other"),
]


def _value(name: str, **labels: str) -> float:
    return prometheus_client.REGISTRY.get_sample_value(name, labels) or 0.0


def _warnings_by_class() -> dict[str, float]:
    return {c: _value("rc_extract_warnings_total", **{"class": c}) for c in em.WARNING_CLASSES}


def _pages_by_result() -> dict[str, float]:
    return {r: _value("rc_ocr_pages_total", result=r) for r in ("ocr", "failed", "skipped")}


@pytest.mark.parametrize(("text", "expected"), REAL_WARNINGS)
def test_each_library_warning_has_its_class(text, expected):
    assert em.warning_class(text) == expected


def test_the_classes_are_closed_and_each_is_reached():
    assert em.WARNING_CLASSES == ("ocr", "layout", "metadata", "markdown", "tables", "sentences", "other")
    assert {expected for _, expected in REAL_WARNINGS} == set(em.WARNING_CLASSES)


@pytest.mark.parametrize("text", [SENTINEL, "", None, 42, "democracy is a stable notion"])
def test_anything_else_is_other(text):
    assert em.warning_class(text) == "other"


def test_one_document_is_counted_by_page_result_seconds_and_warning_class():
    pages, seconds, warnings = _pages_by_result(), _value("rc_ocr_seconds_total"), _warnings_by_class()
    em.record_extraction(ExtractedDocument(
        text="x",
        sentences=None,
        extra={"pdf:ocr_pages": 7, "pdf:ocr_pages_failed": 1, "pdf:ocr_pages_skipped": 2,
               "pdf:ocr_seconds": 12.5, "pdf:ocr_backend": "tesserocr", "pdf:text_pages": 3},
        warnings=(
            "pdf: OCR applied to 7 of 13 pages via tesserocr",
            "pdf: 2 pages skipped: OCR budget exhausted",
            "pdf: 1 page failed OCR",
            "pdf: table detection failed on page 3 (ValueError)",
            SENTINEL,
        ),
    ))
    assert {r: v - pages[r] for r, v in _pages_by_result().items()} == {"ocr": 7, "failed": 1, "skipped": 2}
    assert _value("rc_ocr_seconds_total") - seconds == pytest.approx(12.5)
    assert {c: v - warnings[c] for c, v in _warnings_by_class().items()} == {
        "ocr": 3, "layout": 0, "metadata": 0, "markdown": 0, "tables": 1, "sentences": 0, "other": 1,
    }


def test_values_that_are_not_counts_count_nothing():
    pages, seconds = _pages_by_result(), _value("rc_ocr_seconds_total")
    em.record_extraction(ExtractedDocument(text="x", sentences=None, extra={
        "pdf:ocr_pages": True, "pdf:ocr_pages_failed": "3", "pdf:ocr_pages_skipped": -2,
        "pdf:ocr_seconds": math.nan,
    }))
    em.record_extraction(ExtractedDocument(text="x", sentences=None, extra=None))
    em.record_extraction(object())  # not a document: nothing counted, nothing raised
    assert _pages_by_result() == pages
    assert _value("rc_ocr_seconds_total") == seconds


def test_no_label_carries_warning_text():
    em.record_extraction(ExtractedDocument(
        text="x", sentences=None, warnings=(SENTINEL, "metadata: 1 values truncated")))
    em.set_build_info()
    allowed = em.label_sets()
    seen = 0
    for metric in prometheus_client.REGISTRY.collect():
        if not metric.name.startswith(("rc_ocr_pages", "rc_ocr_seconds", "rc_extract_warnings", "rc_build_info")):
            continue
        for sample in metric.samples:
            for key, value in sample.labels.items():
                seen += 1
                assert SENTINEL not in value
                if key == "class":
                    assert value in allowed["rc_extract_warnings_total"]
                if key == "result":
                    assert value in allowed["rc_ocr_pages_total"]
    assert seen, "the metrics were exercised"


def test_extraction_info_names_the_library_and_the_settings(monkeypatch):
    import knovas_extract

    monkeypatch.setenv("RC_PDF_TEXT_MODE", "shadow")
    monkeypatch.setenv("RC_DOCX_TEXT_MODE", "plain")
    monkeypatch.setenv("RC_OCR_ENGINE", "cli")
    monkeypatch.delenv("RC_PDF_OCR_ENABLED", raising=False)
    assert em.extraction_info() == {
        "knovas_extract_version": knovas_extract.__version__,
        "pdf_text_mode": "shadow",
        "docx_text_mode": "plain",
        "ocr_engine": "cli",
    }
    monkeypatch.setenv("RC_PDF_OCR_ENABLED", "false")
    assert em.extraction_info()["ocr_engine"] == "off"


def test_build_info_is_one_series_under_the_current_settings(monkeypatch):
    import knovas_extract

    monkeypatch.setenv("RC_PDF_TEXT_MODE", "plain")
    monkeypatch.setenv("RC_DOCX_TEXT_MODE", "layout")
    monkeypatch.setenv("RC_OCR_ENGINE", "tesserocr")
    monkeypatch.delenv("RC_PDF_OCR_ENABLED", raising=False)
    em.set_build_info()
    assert _value("rc_build_info", rc_version=em.RC_VERSION,
                  knovas_extract_version=knovas_extract.__version__, pdf_text_mode="plain",
                  docx_text_mode="layout", ocr_engine="tesserocr") == 1.0
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "layout")
    em.set_build_info()
    series = [s for m in prometheus_client.REGISTRY.collect() if m.name == "rc_build_info" for s in m.samples]
    assert [s.labels["pdf_text_mode"] for s in series] == ["layout"], "an earlier label set never lingers"


def test_rc_version_is_the_pyproject_version():
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    assert em.RC_VERSION == tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
