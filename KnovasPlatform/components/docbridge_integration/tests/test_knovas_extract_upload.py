"""The admin upload's extraction: the RC's pipeline, mirrored.

Plan ``docs/superpowers/plans/2026-10-01-ocr-markdown-lite.md`` §6 and the
Platform rows of §7: ``emit_markdown=False``, 0.4 keywords only when the
installed library takes them, shadow mode on ONE cache, page-break markers
on the wire and never in the sidecar, no PDF tables, a partial note read
from ``metadata.extra`` (counts only), and extraction in a guarded child
with a wall-clock ceiling instead of an unbounded ``extract()`` in a gunicorn
request thread.
"""
from __future__ import annotations

import base64
import json
import logging
import multiprocessing as mp
import os
import time

import pytest

pytest.importorskip("knovas_extract")

import knovas_extract_upload as m  # noqa: E402
from context_store import sidecar_path_for_pointer  # noqa: E402
from knovas_extract_upload import ExtractedContent, ExtractedParts, parts_from_base64  # noqa: E402

_ENV = (
    "RC_PDF_TEXT_MODE", "RC_OCR_ENGINE", "RC_OCR_DPI", "RC_OCR_WORKERS", "RC_OCR_MAX_PAGES",
    "RC_OCR_TIME_BUDGET_SECONDS", "RC_OCR_PAGE_TIMEOUT_SECONDS", "RC_TESSERACT_LANG",
    "RC_PAGE_BREAK_MARKERS", "RC_SEND_PDF_TABLES", "RC_EXTRACT_TIMEOUT_SECONDS",
    "RC_EXTRACT_RLIMIT_AS_MB", "SEARCH_CONTEXT_STORE_PATH",
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(m, "logger_ocr_warned", False)
    monkeypatch.setattr(m, "logger_text_mode_warned", False)
    monkeypatch.setattr(m, "logger_shadow_warned", False)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _three_page_pdf() -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"Seite {i + 1}. Text der Seite {i + 1}.")
    raw = doc.tobytes()
    doc.close()
    return raw


def test_parts_from_base64_includes_location_and_section_heading():
    md = "# Title\n\n## Section\n\nHello world. Second sentence."
    parts = parts_from_base64(_b64(md.encode()), "md", part_max_chars=500)
    assert parts
    assert any("sentence_number" in p for p in parts)
    assert any(p["snippet"].startswith("#") for p in parts)


# --- M0: markdown is never requested ------------------------------------------


def test_emit_markdown_is_never_requested(monkeypatch):
    seen: dict = {}

    def extract_stub(raw, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stub")

    monkeypatch.setattr(m, "extract", extract_stub)
    for ext in (".docx", ".pdf", ".eml", ".txt"):
        seen.clear()
        with pytest.raises(m.ExtractionError):
            m._extract_bytes(b"stub", ext)
        assert seen["emit_markdown"] is False, ext
        assert seen["emit_sentences"] is True


# --- 0.4 keywords (text_mode=, ocr=, limits=) are introspected ---------------


def _signature_stub(*, ocr_options: bool, text_mode: bool, limits: bool = False):
    """A fake extract() whose signature matches the installed-library case."""
    seen: dict = {}
    params = ["raw", "*", "mime=None", "emit_markdown=False", "emit_sentences=False",
              "use_ocr='auto'", "ocr_language='deu+eng'"]
    if ocr_options:
        params.append("ocr=None")
    if text_mode:
        params.append("text_mode='plain'")
    if limits:
        params.append("limits=None")
    src = (
        f"def extract({', '.join(params)}):\n"
        "    seen.update({k: v for k, v in locals().items() if k != 'raw'})\n"
        "    raise RuntimeError('stub')\n"
    )
    namespace = {"seen": seen}
    exec(src, namespace)  # noqa: S102 - test-local signature stub
    return namespace["extract"], seen


class _FakeOcrOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.__dict__.update(kwargs)


def test_extract_accepts_reads_the_installed_signature(monkeypatch):
    extract_stub, _ = _signature_stub(ocr_options=True, text_mode=False)
    monkeypatch.setattr(m, "extract", extract_stub)
    assert m.extract_accepts("use_ocr") is True
    assert m.extract_accepts("ocr") is True
    assert m.extract_accepts("text_mode") is False
    assert m.extract_accepts_ocr() is True


def test_ocr_keywords_are_withheld_from_an_extractor_without_them(monkeypatch, caplog):
    seen: dict = {}

    def extract_without_ocr(raw, *, mime=None, emit_markdown=False, emit_sentences=False):
        seen.update(mime=mime)
        raise RuntimeError("stub")

    monkeypatch.setattr(m, "extract", extract_without_ocr)
    assert m.extract_accepts_ocr() is False
    with caplog.at_level(logging.WARNING):
        with pytest.raises(m.ExtractionError):
            m._extract_bytes(b"%PDF-1.4 stub", ".pdf", use_ocr="auto")
    assert seen["mime"] == "application/pdf", "the call still happened, without the OCR keywords"
    assert any("OCR" in r.message for r in caplog.records)


def test_text_mode_and_ocr_are_withheld_from_todays_library(monkeypatch):
    extract_stub, seen = _signature_stub(ocr_options=False, text_mode=False)
    monkeypatch.setattr(m, "extract", extract_stub)
    monkeypatch.setattr(m, "OcrOptions", None)
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "layout")

    with pytest.raises(m.ExtractionError):
        m._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert "text_mode" not in seen and "ocr" not in seen and "limits" not in seen
    assert seen["use_ocr"] == "auto"


def test_text_mode_and_ocr_options_are_sent_when_accepted(monkeypatch):
    extract_stub, seen = _signature_stub(ocr_options=True, text_mode=True)
    monkeypatch.setattr(m, "extract", extract_stub)
    monkeypatch.setattr(m, "OcrOptions", _FakeOcrOptions)
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "layout")
    monkeypatch.setenv("RC_OCR_ENGINE", "cli")
    monkeypatch.setenv("RC_OCR_DPI", "200")
    monkeypatch.setenv("RC_OCR_WORKERS", "1")
    monkeypatch.setenv("RC_OCR_MAX_PAGES", "77")
    monkeypatch.setenv("RC_TESSERACT_LANG", "deu+fra")

    with pytest.raises(m.ExtractionError):
        m._extract_bytes(b"%PDF-1.4 stub", ".pdf", ocr_language="deu+eng")
    assert seen["text_mode"] == "layout"
    assert seen["ocr_language"] == "deu+fra", "RC_TESSERACT_LANG wins over the config value"
    opts = seen["ocr"]
    assert isinstance(opts, _FakeOcrOptions)
    assert opts.kwargs["engine"] == "cli"
    assert opts.kwargs["dpi"] == 200
    assert opts.kwargs["workers"] == 1
    assert opts.kwargs["max_ocr_pages"] == 77
    # Platform defaults: min(60, 120 - 30) = 60, under the cap 120 - 1*30 - 10 = 80
    assert opts.kwargs["time_budget_seconds"] == 60
    assert opts.kwargs["language"] == "deu+fra"
    assert hasattr(opts.kwargs["cache"], "get") and hasattr(opts.kwargs["cache"], "put")


def test_conservative_ocr_defaults(monkeypatch):
    """Plan §6: workers=1, max_ocr_pages=50, a 60 s budget inside the 120 s
    ceiling -- an admin upload shares four cores with the search UI."""
    opts = m.ocr_options_kwargs()
    assert opts["workers"] == 1
    assert opts["max_ocr_pages"] == 50
    assert opts["time_budget_seconds"] == 60
    assert opts["page_timeout_seconds"] == 30
    assert opts["engine"] == "auto" and opts["language"] == "deu+eng"
    assert m.extract_timeout_seconds() == 120


def test_ocr_time_budget_derivation(monkeypatch):
    assert m.ocr_time_budget_seconds(120, 1, 30) == 60
    assert m.ocr_time_budget_seconds(120, 2, 30) == 50, "never past timeout - workers*page - 10"
    assert m.ocr_time_budget_seconds(40, 1, 30) == 10, "never below the floor"
    assert m.ocr_time_budget_seconds(0, 4, 30) == 60, "no ceiling: default budget"
    monkeypatch.setenv("RC_OCR_TIME_BUDGET_SECONDS", "900")
    assert m.ocr_time_budget_seconds(0, 1, 30) == 900
    assert m.ocr_time_budget_seconds(120, 1, 30) == 80, "the env value is still capped by the kill"


def test_ocr_budgets_route_to_limits_when_ocr_options_lacks_them(monkeypatch):
    """0.4.0a1: the page cap and the budgets are ``Limits`` fields. What
    ``OcrOptions`` does not take is offered to ``Limits``, introspected the
    same way -- otherwise the conservative budgets would never reach the
    library."""

    class Opts:
        def __init__(self, engine="auto", language="deu+eng", dpi=None, workers=None, cache=None):
            self.engine, self.language, self.dpi, self.workers, self.cache = engine, language, dpi, workers, cache

    class Lim:
        def __init__(self, max_ocr_pages=500, ocr_time_budget_seconds=240.0,
                     ocr_page_timeout_seconds=60.0, max_ocr_workers=8):
            self.max_ocr_pages = max_ocr_pages
            self.ocr_time_budget_seconds = ocr_time_budget_seconds
            self.ocr_page_timeout_seconds = ocr_page_timeout_seconds
            self.max_ocr_workers = max_ocr_workers

    class FakeLib:
        Limits = Lim

    extract_stub, seen = _signature_stub(ocr_options=True, text_mode=False, limits=True)
    monkeypatch.setattr(m, "extract", extract_stub)
    monkeypatch.setattr(m, "OcrOptions", Opts)
    monkeypatch.setattr(m, "knovas_extract", FakeLib)
    with pytest.raises(m.ExtractionError):
        m._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert isinstance(seen["ocr"], Opts) and seen["ocr"].workers == 1
    lim = seen["limits"]
    assert isinstance(lim, Lim)
    assert (lim.max_ocr_pages, lim.ocr_time_budget_seconds, lim.ocr_page_timeout_seconds) == (50, 60, 30)
    assert lim.max_ocr_workers == 8, "the worker count went to OcrOptions; Limits keeps its ceiling"


def test_ocr_options_are_not_sent_when_ocr_is_disabled(monkeypatch):
    extract_stub, seen = _signature_stub(ocr_options=True, text_mode=True, limits=True)
    monkeypatch.setattr(m, "extract", extract_stub)
    monkeypatch.setattr(m, "OcrOptions", _FakeOcrOptions)
    with pytest.raises(m.ExtractionError):
        m._extract_bytes(b"%PDF-1.4 stub", ".pdf", use_ocr=False)
    assert seen["use_ocr"] is False
    assert seen["ocr"] is None and seen["limits"] is None and seen["text_mode"] == "plain"


def test_pdf_text_mode_env(monkeypatch):
    assert m.pdf_text_mode() == "plain"
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "Shadow")
    assert m.pdf_text_mode() == "shadow"
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "rows")
    assert m.pdf_text_mode() == "plain"


def test_layout_mode_falls_back_to_plain_with_one_warning(monkeypatch, caplog):
    extract_stub, seen = _signature_stub(ocr_options=True, text_mode=False)
    monkeypatch.setattr(m, "extract", extract_stub)
    monkeypatch.setattr(m, "OcrOptions", _FakeOcrOptions)
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "layout")
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            with pytest.raises(m.ExtractionError):
                m._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert "text_mode" not in seen
    assert sum("no text_mode" in r.message for r in caplog.records) == 1


# --- shadow mode: one OCR pass, two renderings (plan decision D13) -----------


def _fake_library_extract(pages: dict, ocr_calls: list, caches: list):
    from knovas_extract.result import Content, ExtractionResult, Extractor, Metadata, Source

    def fake_extract(raw, *, mime=None, emit_markdown=False, emit_sentences=False,
                     use_ocr="auto", ocr_language="deu+eng", ocr=None, text_mode="plain", limits=None):
        caches.append(ocr.cache)
        texts = []
        for key, text in pages.items():
            cached = ocr.cache.get(key)
            if cached is None:
                ocr_calls.append(key)
                cached = text
                ocr.cache.put(key, cached)
            texts.append(cached if text_mode == "plain" else cached.replace("\n", " | "))
        content = Content(text="\n\n".join(texts))
        return ExtractionResult(
            spec_version="1.3.0",
            source=Source(mime_type="application/pdf", sha256="0" * 64, size_bytes=len(raw)),
            metadata=Metadata(page_count=3, extra={"pdf:ocr_pages": 3, "pdf:ocr_backend": "fake"}),
            content=content, warnings=[], extractor=Extractor(name="pdf", version="test"),
        )

    return fake_extract


def test_shadow_mode_shares_the_cache_and_uploads_plain(monkeypatch, caplog):
    """Shadow runs plain and layout over the same bytes; both share ONE
    in-memory cache object, so the layout pass hits what the plain pass
    OCR'd and the document is OCR'd exactly once. The plain text is what is
    uploaded and the ShadowDiff log carries numbers only."""
    pages = {"p1": "Bilanz per 31.12.2023\nAktiven 1'234.50", "p2": "Passiven 1'234.50", "p3": "Seite 3."}
    ocr_calls: list = []
    caches: list = []
    monkeypatch.setattr(m, "extract", _fake_library_extract(pages, ocr_calls, caches))
    monkeypatch.setattr(m, "OcrOptions", _FakeOcrOptions)
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "shadow")
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "0")

    with caplog.at_level(logging.INFO, logger="knovas_extract_upload"):
        out = m.extract_parts_from_base64(_b64(b"%PDF-1.4 stub"), "pdf", pointer="Mandant/Bilanz.pdf", write_sidecar=False)

    assert out.error is None and out.text_mode == "shadow"
    assert ocr_calls == ["p1", "p2", "p3"], "every page OCR'd exactly once across both passes"
    assert len(caches) == 2 and caches[0] is caches[1], "one cache object for both renderings"
    assert isinstance(caches[0], m.MemoryOcrCache)
    assert out.extra["platform:ocr_cache_hits"] == 3 and out.extra["platform:ocr_cache_misses"] == 3
    snippet = out.parts[0]["snippet"]
    assert " | " not in snippet, "the plain rendering is what gets uploaded"
    assert "Aktiven 1'234.50" in snippet
    shadow_lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("ShadowDiff")]
    assert len(shadow_lines) == 1
    line = shadow_lines[0]
    assert "numeric_jaccard=1.0" in line and "ocr_pages=3" in line and "file=Bilanz.pdf" in line
    for fragment in pages.values():
        assert fragment.split("\n")[0] not in line, "never text"


def test_shadow_diff_numbers():
    plain = "Flüssige Mittel\n1'234.50\nText ohne Zahl\n"
    layout = "Flüssige Mittel | 1'234.50\nText ohne Zahl\n"
    assert m.numeric_token_jaccard(plain, layout) == 1.0
    assert m.numeric_token_jaccard("a 12", "b 13") == 0.0
    assert m.row_line_ratio(plain) == 0.0
    assert m.row_line_ratio(layout) == 0.5
    diff = m.shadow_diff(plain, layout, ocr_pages=2, seconds_plain=1.0, seconds_layout=0.5)
    fields = diff.as_log_fields()
    assert set(fields) == {
        "numeric_jaccard", "row_line_ratio_plain", "row_line_ratio_layout", "length_ratio",
        "ocr_pages", "seconds_plain", "seconds_layout",
    }
    assert all(isinstance(v, (int, float)) for v in fields.values())


def test_shadow_without_an_ocr_cache_runs_plain_only(monkeypatch, caplog):
    """Without ``ocr=`` the two passes could not share a cache: every page
    would be OCR'd twice. Then only plain runs."""
    extract_stub, seen = _signature_stub(ocr_options=False, text_mode=True)
    monkeypatch.setattr(m, "extract", extract_stub)
    monkeypatch.setattr(m, "OcrOptions", None)
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "shadow")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(m.ExtractionError):
            m._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert seen["text_mode"] == "plain"
    assert any("shadow" in r.message.lower() for r in caplog.records)


# --- page markers on the wire, never in the sidecar (GI-INGEST-17) -----------


def test_markers_present_across_page_breaks_and_absent_from_the_sidecar(tmp_path, monkeypatch):
    """Through the real guarded child: a three-page PDF yields one part with
    two form feeds on the wire; the sidecar beside it holds the unmarked text
    with the pages on the sentence records."""
    monkeypatch.setenv("SEARCH_CONTEXT_STORE_PATH", str(tmp_path))
    pointer = "corpus/akten/drei.pdf"
    parts = parts_from_base64(_b64(_three_page_pdf()), "pdf", pointer=pointer, use_ocr=False)
    assert len(parts) == 1
    snippet = parts[0]["snippet"]
    assert snippet.count("\f") == 2
    assert not snippet.startswith("\f")
    assert "\n\n\fSeite 2." in snippet and "\n\n\fSeite 3." in snippet
    assert parts[0]["page_number"] == 1 and parts[0]["sentence_number"] == 1

    sidecar = sidecar_path_for_pointer(tmp_path, pointer)
    assert sidecar.is_file()
    text = sidecar.read_text(encoding="utf-8")
    assert "\f" not in text and "\\f" not in text and "\\u000c" not in text
    data = json.loads(text)
    pages = [s["p"] for s in data["sentences"]]
    assert sorted(set(pages)) == [1, 2, 3] and pages == sorted(pages)
    assert data["first_page"]["text"].startswith("Seite 1.")


def test_page_markers_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("RC_PAGE_BREAK_MARKERS", "false")
    parts = parts_from_base64(_b64(_three_page_pdf()), "pdf", use_ocr=False)
    assert "\f" not in parts[0]["snippet"]


# --- no tables payload for PDF parts -----------------------------------------


def _content_with_table(**extra_fields) -> ExtractedContent:
    return ExtractedContent(
        text="Seite eins. Zahl 12.00.",
        tables=[{"client_table_hint": "t1", "headers": ["H"], "rows": [["v"]]}],
        **extra_fields,
    )


def test_no_tables_for_pdf_parts_by_default(monkeypatch):
    monkeypatch.setattr(m, "extract_guarded", lambda *a, **k: _content_with_table())
    pdf = m.extract_parts_from_base64(_b64(b"%PDF stub"), "pdf", write_sidecar=False)
    assert pdf.parts and all("tables" not in p for p in pdf.parts), "the server drops PDF tables at the Redis buffer"
    docx = m.extract_parts_from_base64(_b64(b"PK stub"), "docx", write_sidecar=False)
    assert docx.parts[0]["tables"][0]["client_table_hint"] == "t1", "DOCX tables are still sent"


def test_pdf_tables_can_be_switched_on(monkeypatch):
    monkeypatch.setenv("RC_SEND_PDF_TABLES", "true")
    monkeypatch.setattr(m, "extract_guarded", lambda *a, **k: _content_with_table())
    pdf = m.extract_parts_from_base64(_b64(b"%PDF stub"), "pdf", write_sidecar=False)
    assert pdf.parts[0]["tables"][0]["client_table_hint"] == "t1"


# --- partial notes read defensively from metadata.extra ----------------------


def test_partial_note_for_reads_extra_defensively():
    assert m.partial_note_for({}, expect_ocr=True) is None
    assert m.partial_note_for(None, expect_ocr=True) is None

    skipped = {"pdf:ocr_pages_skipped": 12, "pdf:ocr_pages": 40, "pdf:ocr_backend": "tesserocr", "pdf:text_pages": 3}
    assert m.partial_note_for(skipped, expect_ocr=True) == {
        "ocr_pages_skipped": 12, "ocr_pages": 40, "ocr_backend": "tesserocr", "text_pages": 3,
    }
    assert m.partial_note_for(skipped, expect_ocr=False)["ocr_pages_skipped"] == 12

    # an older library that does not count: backend none is a defect when OCR was expected
    no_backend = {"pdf:ocr_backend": "none", "pdf:ocr_pages": 0}
    assert m.partial_note_for(no_backend, expect_ocr=True) == {"reason": "ocr_backend_none", "ocr_pages": 0, "ocr_backend": "none"}
    assert m.partial_note_for(no_backend, expect_ocr=False) is None, "OCR off: a missing backend is not a defect"

    # 0.4 counts: zero skipped pages and no backend = a born-digital PDF without Tesseract
    digital = {"pdf:ocr_backend": "none", "pdf:ocr_pages": 0, "pdf:ocr_pages_skipped": 0, "pdf:text_pages": 3}
    assert m.partial_note_for(digital, expect_ocr=True) is None
    assert m.partial_note_for({"pdf:ocr_pages_skipped": "0"}, expect_ocr=True) is None
    assert m.partial_note_for({"pdf:ocr_pages_skipped": "7", "pdf:ocr_backend": "cli"}, expect_ocr=True) == {
        "ocr_pages_skipped": 7, "ocr_backend": "cli",
    }


def test_partial_note_is_surfaced_in_result_log_and_sidecar(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("SEARCH_CONTEXT_STORE_PATH", str(tmp_path))
    content = ExtractedContent(
        text="Deckblatt der Jahresrechnung. Zweiter Satz.",
        extra={"pdf:ocr_pages_skipped": 12, "pdf:ocr_pages": 40, "pdf:ocr_backend": "tesserocr"},
    )
    monkeypatch.setattr(m, "extract_guarded", lambda *a, **k: content)
    pointer = "corpus/scan.pdf"
    with caplog.at_level(logging.INFO, logger="knovas_extract_upload"):
        out = m.extract_parts_from_base64(_b64(b"%PDF stub"), "pdf", pointer=pointer)
    assert out.partial == {"ocr_pages_skipped": 12, "ocr_pages": 40, "ocr_backend": "tesserocr"}
    assert out.parts and out.error is None
    lines = [r.getMessage() for r in caplog.records if "partial" in r.getMessage()]
    assert len(lines) == 1
    assert "ocr_pages_skipped=12" in lines[0] and "scan.pdf" in lines[0]
    assert "Deckblatt" not in lines[0], "counts only, never text"
    data = json.loads(sidecar_path_for_pointer(tmp_path, pointer).read_text(encoding="utf-8"))
    assert data["version"] == 2 and data["partial"] == out.partial


def test_a_complete_document_has_no_partial_note(monkeypatch):
    monkeypatch.setattr(m, "extract_guarded", lambda *a, **k: ExtractedContent(text="Alles da."))
    out = m.extract_parts_from_base64(_b64(b"%PDF stub"), "pdf", write_sidecar=False)
    assert out.partial is None and out.parts


def test_the_client_surfaces_the_partial_note(monkeypatch):
    import knovas_client

    monkeypatch.setattr(
        m, "extract_parts_from_base64",
        lambda *a, **k: ExtractedParts(parts=[{"snippet": "x", "sentence_number": 1}], partial={"ocr_pages_skipped": 3}),
    )
    parts, init_fields, partial = knovas_client._secured_transmit_parts_from_document(
        {"doc_id": "corpus/a.pdf", "content_base64": _b64(b"x"), "type": "pdf"}
    )
    assert parts == [{"snippet": "x", "sentence_number": 1}]
    assert partial == {"ocr_pages_skipped": 3}
    assert init_fields


# --- the guarded child: wall clock, nice, RLIMIT_AS -----------------------------


def _slow_child(*_args, **_kwargs):
    """A hung extractor: never answers. Module level so it survives pickling
    under the spawn start method; Linux forks."""
    time.sleep(30)


def test_a_slow_extractor_returns_a_timeout_inside_the_budget(monkeypatch):
    """The admin upload used to run extract() in the gunicorn request thread
    with no ceiling. The timeout is the Platform's doing, not the library's
    verdict on the input: the message never starts with "resource limit
    exceeded" (GI-EXTRACT-02)."""
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(m, "_extract_child", _slow_child)
    started = time.monotonic()
    with pytest.raises(m.ExtractionError) as exc:
        m.extract_guarded(b"Some text. More text.", ".txt")
    elapsed = time.monotonic() - started
    assert elapsed < 10, f"did not return inside the budget: {elapsed:.1f}s"
    message = str(exc.value)
    assert message.startswith(m.EXTRACT_TIMEOUT_ERROR_PREFIX)
    assert not message.lower().startswith("resource limit exceeded")
    assert m.is_timeout_error(message)

    out = m.extract_parts_from_base64(_b64(b"Some text."), "txt", write_sidecar=False)
    assert out.parts == [] and out.error and out.error.startswith(m.EXTRACT_TIMEOUT_ERROR_PREFIX)


def test_guarded_matches_in_process_extraction():
    raw = b"First sentence. Second sentence."
    direct = m._extract_bytes(raw, ".txt")
    guarded = m.extract_guarded(raw, ".txt")
    assert guarded.text == direct.text
    assert len(guarded.sentences or []) == len(direct.sentences or []) == 2


def test_guarded_disabled_runs_in_process(monkeypatch):
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "0")
    monkeypatch.setattr(m, "_extract_child", _slow_child)
    assert "First" in m.extract_guarded(b"First sentence.", ".txt").text


def test_guarded_propagates_the_childs_error():
    with pytest.raises(m.ExtractionError) as exc:
        m.extract_guarded(b"PK not a docx", ".docx")
    assert not m.is_timeout_error(str(exc.value))


def _report_child_limits(conn, env_mb):
    import resource as _resource

    os.environ["RC_EXTRACT_RLIMIT_AS_MB"] = env_mb
    m._apply_child_limits()
    soft, _hard = _resource.getrlimit(_resource.RLIMIT_AS)
    conn.send((soft, os.nice(0)))
    conn.close()


@pytest.mark.skipif("fork" not in mp.get_all_start_methods(), reason="fork only")
def test_child_limits_apply_nice_and_rlimit_as():
    import resource

    ctx = mp.get_context("fork")
    parent_nice = os.nice(0)
    _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_report_child_limits, args=(child_conn, "512"))
    proc.start()
    child_conn.close()
    soft, nice = parent_conn.recv()
    proc.join(10)
    assert nice >= min(19, parent_nice + m.EXTRACT_CHILD_NICE)
    expected = 512 * 1024 * 1024
    if hard != resource.RLIM_INFINITY and hard < expected:
        expected = hard
    assert soft == expected
    # the parent is untouched
    assert os.nice(0) == parent_nice
