import io
from pathlib import Path

import pytest

from sync.document_text import (
    ConversionError,
    bytes_to_markdown,
    extract_document,
    file_to_markdown,
    is_syncable_extension,
    is_unconvertible_error,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_is_syncable_extension():
    assert is_syncable_extension(".pdf")
    assert is_syncable_extension(".DOCX")
    assert not is_syncable_extension(".doc")


def test_plain_text_md(tmp_path):
    p = tmp_path / "note.md"
    p.write_text("# Hello\n\nWorld", encoding="utf-8")
    assert "Hello" in file_to_markdown(p)


def test_plain_text_utf8_sig(tmp_path):
    p = tmp_path / "note.txt"
    p.write_bytes(b"\xef\xbb\xbfUTF-8 BOM")
    assert "UTF-8 BOM" in file_to_markdown(p)


def test_extract_document_returns_sentences_for_plain_text(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("First sentence. Second sentence. Third one!", encoding="utf-8")
    doc = extract_document(p)
    assert doc.sentences is not None
    assert len(doc.sentences) == 3
    # Contract: text[char_start:char_end] == text.
    s = doc.sentences[0]
    assert doc.text[s.char_start : s.char_end] == s.text


def test_eml_fixture(tmp_path):
    raw = (FIXTURES / "sample.eml").read_bytes()
    # Body ends up in .text; subject moved to .title (uploader threads it into
    # the transmission `title` field, so subject-line search still works).
    md = bytes_to_markdown(raw, ".eml")
    assert "Hello from the sample email" in md

    p = tmp_path / "sample.eml"
    p.write_bytes(raw)
    doc = extract_document(p)
    assert doc.title == "Sample Email"
    assert "Hello from the sample email" in doc.text


def test_docx_conversion():
    docx = pytest.importorskip("docx")
    buf = io.BytesIO()
    document = docx.Document()
    document.add_heading("Section One", level=1)
    document.add_paragraph("Paragraph text.")
    document.save(buf)
    md = bytes_to_markdown(buf.getvalue(), ".docx")
    assert "Section One" in md
    assert "Paragraph text." in md


def test_pdf_conversion_and_page_backpointers(tmp_path):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "Hello PDF content.")
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Second page here.")
    p = tmp_path / "twopage.pdf"
    p.write_bytes(doc.tobytes())
    doc.close()

    result = extract_document(p)
    assert "Hello PDF content" in result.text
    assert "Second page here" in result.text
    # Every sentence must have a populated page_number for PDFs.
    assert result.sentences is not None
    assert all(s.page_number is not None for s in result.sentences)
    # First sentence lives on page 1, and at least one sentence claims page 2.
    assert result.sentences[0].page_number == 1
    assert any(s.page_number == 2 for s in result.sentences)


def test_empty_pdf_raises(monkeypatch):
    monkeypatch.setenv("RC_PDF_OCR_ENABLED", "false")
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    doc.new_page()
    raw = doc.tobytes()
    doc.close()
    with pytest.raises(ConversionError, match="no extractable text"):
        bytes_to_markdown(raw, ".pdf")


def test_unsupported_extension(tmp_path):
    p = tmp_path / "file.bin"
    p.write_bytes(b"data")
    with pytest.raises(ConversionError, match="unsupported extension"):
        file_to_markdown(p)


def test_fake_docx_raises_conversion_error():
    with pytest.raises(ConversionError, match="corrupt .docx"):
        bytes_to_markdown(b"not a real docx", ".docx")


def test_is_unconvertible_error():
    assert is_unconvertible_error("File is not a zip file")
    assert is_unconvertible_error("no extractable text from .pdf file")
    assert is_unconvertible_error("corrupt .docx: something broke")
    assert is_unconvertible_error("corrupt .msg: invalid literal for int() with base 10: '\\x1c4'")
    assert is_unconvertible_error("ValueError: invalid literal for int() with base 10: '\\x1f3'")
    assert is_unconvertible_error("encrypted .pdf: password required")
    assert is_unconvertible_error("resource limit exceeded: page_count")
    assert is_unconvertible_error("unsupported extension: .bin")
    assert not is_unconvertible_error("init failed: 503")
    assert not is_unconvertible_error("part 2 failed: 500")
    assert not is_unconvertible_error(None)


def test_msg_valueerror_from_extract_is_corrupt_and_skippable(monkeypatch):
    import sync.document_text as dt

    def raise_value_error(*_args, **_kwargs):
        raise ValueError("invalid literal for int() with base 10: '\\x1c4'")

    monkeypatch.setattr(dt, "extract", raise_value_error)

    with pytest.raises(ConversionError, match=r"corrupt \.msg:") as exc:
        dt.bytes_to_markdown(b"fake-msg-bytes", ".msg")

    assert is_unconvertible_error(str(exc.value))


def test_scan_pdf_in_executor(tmp_watch_root):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Sync PDF text")
    (tmp_watch_root / "doc.pdf").write_bytes(doc.tobytes())
    doc.close()

    from sync.sync_executor import scan_document_inventory

    body = {
        "mode": "incremental",
        "sources": [{"path": str(tmp_watch_root), "recursive": True}],
        "filters": {},
        "ingestion": {"identifier_prefix": "rc"},
    }
    summary = scan_document_inventory(body, include_documents=True)
    assert summary.total >= 1
    paths = [d.relative_path for d in summary.documents]
    assert "doc.pdf" in paths


# --- sentence emission size guard -------------------------------------------
# split_sentences degrades badly on large weakly-punctuated text (tariff
# tables); above the ceiling we keep the text and drop the citations.


def test_sentence_emit_max_bytes_default(monkeypatch):
    from sync.document_text import DEFAULT_SENTENCE_EMIT_MAX_BYTES, sentence_emit_max_bytes

    monkeypatch.delenv("RC_SENTENCE_EMIT_MAX_BYTES", raising=False)
    assert sentence_emit_max_bytes() == DEFAULT_SENTENCE_EMIT_MAX_BYTES


def test_sentence_emit_max_bytes_env_override(monkeypatch):
    from sync.document_text import sentence_emit_max_bytes

    monkeypatch.setenv("RC_SENTENCE_EMIT_MAX_BYTES", "4096")
    assert sentence_emit_max_bytes() == 4096


def test_sentence_emit_max_bytes_invalid_falls_back(monkeypatch):
    from sync.document_text import DEFAULT_SENTENCE_EMIT_MAX_BYTES, sentence_emit_max_bytes

    monkeypatch.setenv("RC_SENTENCE_EMIT_MAX_BYTES", "not-a-number")
    assert sentence_emit_max_bytes() == DEFAULT_SENTENCE_EMIT_MAX_BYTES


def test_large_text_skips_sentences_but_keeps_text(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_SENTENCE_EMIT_MAX_BYTES", "64")
    p = tmp_path / "tariff.txt"
    p.write_text("Alpha beta. " * 100, encoding="utf-8")

    doc = extract_document(p)

    assert doc.sentences is None or doc.sentences == []
    assert "Alpha beta." in doc.text


def test_small_text_still_emits_sentences(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_SENTENCE_EMIT_MAX_BYTES", "1048576")
    p = tmp_path / "note.txt"
    p.write_text("First sentence. Second sentence.", encoding="utf-8")

    doc = extract_document(p)

    assert doc.sentences is not None
    assert len(doc.sentences) == 2


# --- per-file extraction timeout --------------------------------------------
# One pathological document must not occupy the single sync worker forever.


def slow_extract_child(path_str, result_queue):  # noqa: ARG001 - child target
    """Stand-in for a runaway extractor. Module level so it is picklable."""
    import time as _time

    _time.sleep(30)


def test_extract_timeout_seconds_default(monkeypatch):
    from sync.document_text import DEFAULT_EXTRACT_TIMEOUT_SECONDS, extract_timeout_seconds

    monkeypatch.delenv("RC_EXTRACT_TIMEOUT_SECONDS", raising=False)
    assert extract_timeout_seconds() == DEFAULT_EXTRACT_TIMEOUT_SECONDS


def test_extract_timeout_seconds_env_override(monkeypatch):
    from sync.document_text import extract_timeout_seconds

    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "30")
    assert extract_timeout_seconds() == 30


def test_extract_timeout_seconds_invalid_falls_back(monkeypatch):
    from sync.document_text import DEFAULT_EXTRACT_TIMEOUT_SECONDS, extract_timeout_seconds

    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "soon")
    assert extract_timeout_seconds() == DEFAULT_EXTRACT_TIMEOUT_SECONDS


def test_guarded_disabled_runs_in_process(tmp_path, monkeypatch):
    from sync.document_text import extract_document_guarded

    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "0")
    p = tmp_path / "note.txt"
    p.write_text("First sentence. Second sentence.", encoding="utf-8")

    doc = extract_document_guarded(p)

    assert "First sentence." in doc.text
    assert doc.sentences is not None


def test_guarded_matches_direct_extraction(tmp_path, monkeypatch):
    from sync.document_text import extract_document_guarded

    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "120")
    p = tmp_path / "note.txt"
    p.write_text("First sentence. Second sentence.", encoding="utf-8")

    direct = extract_document(p)
    guarded = extract_document_guarded(p)

    assert guarded.text == direct.text
    assert len(guarded.sentences or []) == len(direct.sentences or [])


def test_guarded_propagates_conversion_error(tmp_path, monkeypatch):
    from sync.document_text import extract_document_guarded

    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "120")
    p = tmp_path / "empty.txt"
    p.write_text("   ", encoding="utf-8")

    with pytest.raises(ConversionError):
        extract_document_guarded(p)


def test_guarded_timeout_is_retryable_never_unconvertible(tmp_path, monkeypatch):
    """A wall-clock kill is the RC's doing, not the library's verdict on the
    input: one hung OCR page must not park the whole file forever
    (GI-EXTRACT-02). The executor retries it, capped."""
    import sync.document_text as dt

    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "1")
    # Module-level target so it survives pickling under the spawn start
    # method (Windows); production runs fork on Linux.
    monkeypatch.setattr(dt, "_extract_child", slow_extract_child)

    p = tmp_path / "slow.txt"
    p.write_text("Some text. More text.", encoding="utf-8")

    with pytest.raises(ConversionError) as exc:
        dt.extract_document_guarded(p)

    message = str(exc.value)
    assert message.startswith(dt.EXTRACT_TIMEOUT_ERROR_PREFIX)
    assert not message.lower().startswith("resource limit exceeded")
    assert is_unconvertible_error(message) is False


def test_extract_timeout_scales_with_page_count(monkeypatch):
    from sync.document_text import extract_timeout_seconds

    monkeypatch.delenv("RC_EXTRACT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("RC_EXTRACT_TIMEOUT_PER_PAGE_SECONDS", raising=False)
    monkeypatch.delenv("RC_EXTRACT_TIMEOUT_MAX_SECONDS", raising=False)
    assert extract_timeout_seconds(None) == 300
    assert extract_timeout_seconds(20) == 300, "the base ceiling is the floor"
    assert extract_timeout_seconds(300) == 600, "2 s/page past the floor"
    assert extract_timeout_seconds(5000) == 1800, "capped"
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_PER_PAGE_SECONDS", "5")
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_MAX_SECONDS", "900")
    assert extract_timeout_seconds(100) == 500
    assert extract_timeout_seconds(1000) == 900
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "0")
    assert extract_timeout_seconds(1000) == 0, "0 disables the ceiling whatever the page count"


def test_guarded_pdf_deadline_follows_the_childs_page_count(tmp_path, monkeypatch):
    """The child reports the page count before extracting; the parent moves
    its deadline accordingly instead of killing a 300-page scan at 300 s."""
    import sync.document_text as dt

    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"Seite {i + 1}.")
    p = tmp_path / "three.pdf"
    p.write_bytes(doc.tobytes())
    doc.close()

    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_PER_PAGE_SECONDS", "100")
    seen: list[str] = []

    def record(msg, *args, **kwargs):
        seen.append(msg % args if args else msg)

    monkeypatch.setattr(dt.logger, "info", record)
    extracted = dt.extract_document_guarded(p)
    assert extracted.page_count == 3
    assert any("60s -> 300s" in line for line in seen), seen


def test_ocr_keywords_are_only_sent_to_an_extractor_that_takes_them(monkeypatch, caplog):
    """The pin used to demand an unpublished release for exactly this call.

    Against an extractor without OCR support the keywords must be withheld
    rather than raising TypeError on every PDF -- and the operator has to be
    told, because scanned PDFs then yield no text.
    """
    import logging

    from sync import document_text

    seen = {}

    def extract_without_ocr(raw, *, mime=None, emit_markdown=False, emit_sentences=False):
        seen.update(mime=mime, emit_markdown=emit_markdown)
        raise document_text.UnsupportedFormatError("stub")

    monkeypatch.setattr(document_text, "extract", extract_without_ocr)
    monkeypatch.setattr(document_text, "pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(document_text, "logger_ocr_warned", False)

    assert document_text.extract_accepts_ocr() is False
    with caplog.at_level(logging.WARNING):
        with pytest.raises(document_text.ConversionError):
            document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf")

    assert seen["mime"], "the call still happened, just without the OCR keywords"
    assert any("OCR" in r.message for r in caplog.records)


def test_ocr_keywords_are_sent_when_the_extractor_accepts_them(monkeypatch):
    from sync import document_text

    seen = {}

    def extract_with_ocr(raw, *, mime=None, emit_markdown=False, emit_sentences=False,
                         use_ocr=False, ocr_language="deu+eng"):
        seen.update(use_ocr=use_ocr, ocr_language=ocr_language)
        raise document_text.UnsupportedFormatError("stub")

    monkeypatch.setattr(document_text, "extract", extract_with_ocr)
    monkeypatch.setattr(document_text, "pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(document_text, "tesseract_language", lambda: "deu")

    assert document_text.extract_accepts_ocr() is True
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert seen == {"use_ocr": True, "ocr_language": "deu"}


# --- M0: markdown is never requested ------------------------------------------
# emit_markdown cost ~8 s/document and its expansion guard parked every mixed
# PDF and every DOCX with large tables ("resource limit exceeded: markdown
# expansion ratio").


def test_emit_markdown_is_never_requested(monkeypatch):
    from sync import document_text

    seen = {}

    def extract_stub(raw, **kwargs):
        seen.update(kwargs)
        raise document_text.UnsupportedFormatError("stub")

    monkeypatch.setattr(document_text, "extract", extract_stub)
    for ext in (".docx", ".pdf", ".eml", ".txt"):
        seen.clear()
        with pytest.raises(document_text.ConversionError):
            document_text._extract_bytes(b"stub", ext)
        assert seen["emit_markdown"] is False, ext


# --- 0.4 keywords (text_mode=, ocr=) are introspected ------------------------


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
        "    raise UnsupportedFormatError('stub')\n"
    )
    from knovas_extract import UnsupportedFormatError

    namespace = {"seen": seen, "UnsupportedFormatError": UnsupportedFormatError}
    exec(src, namespace)  # noqa: S102 - test-local signature stub
    return namespace["extract"], seen


class _FakeOcrOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.__dict__.update(kwargs)


def test_text_mode_and_ocr_are_withheld_from_todays_library(monkeypatch):
    from sync import document_text

    extract_stub, seen = _signature_stub(ocr_options=False, text_mode=False)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.setattr(document_text, "OcrOptions", None)
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "layout")
    monkeypatch.setattr(document_text, "logger_text_mode_warned", False)

    assert document_text.extract_accepts("text_mode") is False
    assert document_text.extract_accepts("ocr") is False
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert "text_mode" not in seen and "ocr" not in seen
    assert seen["use_ocr"] == "auto"


def test_text_mode_and_ocr_options_are_sent_when_accepted(monkeypatch):
    from sync import document_text

    extract_stub, seen = _signature_stub(ocr_options=True, text_mode=True)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.setattr(document_text, "OcrOptions", _FakeOcrOptions)
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "layout")
    monkeypatch.setenv("RC_OCR_ENGINE", "cli")
    monkeypatch.setenv("RC_OCR_DPI", "200")
    monkeypatch.setenv("RC_OCR_WORKERS", "2")
    monkeypatch.setenv("RC_OCR_MAX_PAGES", "77")
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "0")
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "300")
    monkeypatch.delenv("RC_OCR_TIME_BUDGET_SECONDS", raising=False)

    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert seen["text_mode"] == "layout"
    opts = seen["ocr"]
    assert isinstance(opts, _FakeOcrOptions)
    assert opts.kwargs["engine"] == "cli"
    assert opts.kwargs["dpi"] == 200
    assert opts.kwargs["workers"] == 2
    assert opts.kwargs["max_ocr_pages"] == 77
    # min(240, 300 - 30) = 240, then never more than 300 - 2*60 - 10 = 170
    assert opts.kwargs["time_budget_seconds"] == 170
    assert hasattr(opts.kwargs["cache"], "get") and hasattr(opts.kwargs["cache"], "put")


def test_ocr_budgets_reach_the_library_limits(monkeypatch):
    """GI-EXTRACT-01/02: in knovas-extract 0.4 the page cap, the time budget
    and the per-page timeout are `Limits` fields, not `OcrOptions` fields —
    sending them only to OcrOptions leaves the library at its defaults (a
    500-page cap and 240 s on a host configured for 50 pages)."""
    from knovas_extract.result import Limits

    from sync import document_text

    extract_stub, seen = _signature_stub(ocr_options=True, text_mode=False, limits=True)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.setattr(document_text, "OcrOptions", _FakeOcrOptions)
    monkeypatch.setenv("RC_OCR_WORKERS", "2")
    monkeypatch.setenv("RC_OCR_MAX_PAGES", "77")
    monkeypatch.setenv("RC_OCR_PAGE_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "0")
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "300")
    monkeypatch.delenv("RC_OCR_TIME_BUDGET_SECONDS", raising=False)

    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    limits = seen["limits"]
    assert isinstance(limits, Limits)
    assert limits.max_ocr_pages == 77
    assert limits.ocr_page_timeout_seconds == 45
    assert limits.max_ocr_workers == 2
    # min(240, 300 - 30) = 240, then never more than 300 - 2*45 - 10 = 200
    assert limits.ocr_time_budget_seconds == 200
    # the non-OCR limits keep the library defaults
    assert limits.max_pages == Limits().max_pages


def test_limits_are_withheld_when_the_library_does_not_take_them(monkeypatch):
    from sync import document_text

    extract_stub, seen = _signature_stub(ocr_options=True, text_mode=False, limits=False)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.setattr(document_text, "OcrOptions", _FakeOcrOptions)
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "0")
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert "limits" not in seen


def test_ocr_options_are_not_sent_when_ocr_is_disabled(monkeypatch):
    from sync import document_text

    extract_stub, seen = _signature_stub(ocr_options=True, text_mode=True)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.setattr(document_text, "OcrOptions", _FakeOcrOptions)
    monkeypatch.setenv("RC_PDF_OCR_ENABLED", "false")
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "plain")
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert seen["use_ocr"] is False
    # the stub records its defaults too: neither keyword was passed
    assert seen["ocr"] is None and seen["text_mode"] == "plain"


def test_ocr_option_names_follow_the_library_signature(monkeypatch):
    """The field names are introspected: a library spelling the page cap
    `max_pages` still gets the value, an unknown field is simply not sent."""
    from sync import document_text

    class Opts:
        def __init__(self, engine="auto", max_pages=500, cache=None):
            self.engine, self.max_pages, self.cache = engine, max_pages, cache

    monkeypatch.setattr(document_text, "OcrOptions", Opts)
    built = document_text.build_ocr_options(
        {"engine": "cli", "max_ocr_pages": 12, "dpi": 300, "cache": "c"}
    )
    assert (built.engine, built.max_pages, built.cache) == ("cli", 12, "c")


def test_ocr_time_budget_derivation(monkeypatch):
    from sync.document_text import ocr_time_budget_seconds

    monkeypatch.delenv("RC_OCR_TIME_BUDGET_SECONDS", raising=False)
    assert ocr_time_budget_seconds(300, 1, 60) == 230, "min(240, 270) then <= 300-60-10"
    assert ocr_time_budget_seconds(1800, 4, 60) == 240
    assert ocr_time_budget_seconds(120, 4, 60) == 10, "never below the floor"
    assert ocr_time_budget_seconds(0, 4, 60) == 240, "no ceiling: default budget"
    monkeypatch.setenv("RC_OCR_TIME_BUDGET_SECONDS", "900")
    assert ocr_time_budget_seconds(1800, 2, 60) == 900
    assert ocr_time_budget_seconds(300, 2, 60) == 170, "the env value is still capped by the kill"


def test_pdf_text_mode_env(monkeypatch):
    from sync.document_text import pdf_text_mode

    monkeypatch.delenv("RC_PDF_TEXT_MODE", raising=False)
    assert pdf_text_mode() == "plain"
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "Shadow")
    assert pdf_text_mode() == "shadow"
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "rows")
    assert pdf_text_mode() == "plain"


# --- shadow mode: one OCR pass, two renderings (plan decision D13) -----------


class _CountingCache:
    """Duck-typed OCR cache that counts what the two passes would OCR."""

    def __init__(self):
        self.entries: dict[str, str] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key):
        if key in self.entries:
            self.hits += 1
            return self.entries[key]
        self.misses += 1
        return None

    def put(self, key, value):
        self.entries[key] = value


def test_shadow_mode_ocrs_each_page_once(monkeypatch, caplog):
    """Shadow runs plain and layout over the same bytes; both share ONE cache
    object, so the layout pass hits what the plain pass OCR'd and the
    document is OCR'd exactly once. The plain text is what gets uploaded and
    the ShadowDiff log carries numbers only."""
    import logging

    from knovas_extract.result import Content, ExtractionResult, Extractor, Metadata, Source

    from sync import document_text

    pages = {"p1": "Bilanz per 31.12.2023\nAktiven 1'234.50", "p2": "Passiven 1'234.50", "p3": "Seite 3."}
    ocr_calls: list[str] = []
    cache = _CountingCache()

    def fake_extract(raw, *, mime=None, emit_markdown=False, emit_sentences=False,
                     use_ocr="auto", ocr_language="deu+eng", ocr=None, text_mode="plain"):
        texts = []
        for key, text in pages.items():
            cached = ocr.cache.get(key)
            if cached is None:
                ocr_calls.append(key)
                cached = text
                ocr.cache.put(key, cached)
            texts.append(cached if text_mode == "plain" else cached.replace("\n", " | "))
        content = Content(text="\n\n".join(texts), pages=None, sections=None, markdown=None, sentences=None, tables=None)
        return ExtractionResult(
            spec_version="1.3.0",
            source=Source(mime_type="application/pdf", sha256="0" * 64, size_bytes=len(raw)),
            metadata=Metadata(page_count=3, extra={"pdf:ocr_pages": 3, "pdf:ocr_backend": "fake"}),
            content=content, warnings=[], extractor=Extractor(name="pdf", version="test"),
        )

    monkeypatch.setattr(document_text, "extract", fake_extract)
    monkeypatch.setattr(document_text, "OcrOptions", _FakeOcrOptions)
    monkeypatch.setattr(document_text, "ocr_cache_for_document", lambda key: cache)
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "shadow")
    monkeypatch.setenv("RC_PDF_OCR_ENABLED", "true")

    with caplog.at_level(logging.INFO, logger="sync.document_text"):
        doc = document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf", document_key="Mandant/Bilanz.pdf")

    assert ocr_calls == ["p1", "p2", "p3"], "every page OCR'd exactly once across both passes"
    assert cache.hits == 3 and cache.misses == 3
    assert " | " not in doc.text, "the plain rendering is what gets uploaded"
    assert doc.extra["pdf:ocr_pages"] == 3
    assert doc.extra["rc:ocr_cache_hits"] == 3
    shadow_lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("ShadowDiff")]
    assert len(shadow_lines) == 1
    line = shadow_lines[0]
    assert "numeric_jaccard=1.0" in line and "ocr_pages=3" in line and "file=Bilanz.pdf" in line
    for fragment in pages.values():
        assert fragment.split("\n")[0] not in line, "never text"


def test_shadow_diff_numbers():
    from sync.document_text import numeric_token_jaccard, row_line_ratio, shadow_diff

    plain = "Flüssige Mittel\n1'234.50\nText ohne Zahl\n"
    layout = "Flüssige Mittel | 1'234.50\nText ohne Zahl\n"
    assert numeric_token_jaccard(plain, layout) == 1.0
    assert numeric_token_jaccard("a 12", "b 13") == 0.0
    assert numeric_token_jaccard("", "") == 1.0
    assert row_line_ratio(plain) == 0.0
    assert row_line_ratio(layout) == 0.5
    diff = shadow_diff(plain, layout, ocr_pages=2, seconds_plain=1.0, seconds_layout=0.5)
    assert diff.length_ratio == pytest.approx(len(layout) / len(plain))
    fields = diff.as_log_fields()
    assert set(fields) == {
        "numeric_jaccard", "row_line_ratio_plain", "row_line_ratio_layout", "length_ratio",
        "ocr_pages", "seconds_plain", "seconds_layout",
    }
    assert all(isinstance(v, (int, float)) for v in fields.values())


# --- partial notes read defensively from metadata.extra ----------------------


def test_partial_note_for_reads_extra_defensively():
    from sync.document_text import ExtractedDocument, partial_note_for

    complete = ExtractedDocument(text="x", sentences=None, extra={})
    assert partial_note_for(complete, expect_ocr=True) is None
    legacy = ExtractedDocument(text="x", sentences=None, extra=None)
    assert partial_note_for(legacy, expect_ocr=True) is None

    skipped = ExtractedDocument(
        text="x", sentences=None,
        extra={"pdf:ocr_pages_skipped": 12, "pdf:ocr_pages": 40, "pdf:ocr_backend": "tesserocr", "pdf:text_pages": 3},
    )
    assert partial_note_for(skipped, expect_ocr=True) == {
        "ocr_pages_skipped": 12, "ocr_pages": 40, "ocr_backend": "tesserocr", "text_pages": 3,
    }
    assert partial_note_for(skipped, expect_ocr=False)["ocr_pages_skipped"] == 12

    no_backend = ExtractedDocument(text="x", sentences=None, extra={"pdf:ocr_backend": "none", "pdf:ocr_pages": 0})
    assert partial_note_for(no_backend, expect_ocr=True) == {"reason": "ocr_backend_none", "ocr_pages": 0, "ocr_backend": "none"}
    assert partial_note_for(no_backend, expect_ocr=False) is None, "OCR off: a missing backend is not a defect"

    zero = ExtractedDocument(text="x", sentences=None, extra={"pdf:ocr_pages_skipped": "0"})
    assert partial_note_for(zero, expect_ocr=True) is None


# --- the extraction child: nice + RLIMIT_AS ---------------------------------


def _report_child_limits(conn, env_mb):
    import os as _os
    import resource as _resource

    _os.environ["RC_EXTRACT_RLIMIT_AS_MB"] = env_mb
    from sync.document_text import _apply_child_limits

    _apply_child_limits()
    soft, _hard = _resource.getrlimit(_resource.RLIMIT_AS)
    conn.send((soft, _os.nice(0)))
    conn.close()


@pytest.mark.skipif("fork" not in __import__("multiprocessing").get_all_start_methods(), reason="fork only")
def test_child_limits_apply_nice_and_rlimit_as():
    import multiprocessing as mp

    ctx = mp.get_context("fork")
    for env_mb, expected in (("64", 64 * 1024 * 1024), ("0", None)):
        receiver, sender = ctx.Pipe(duplex=False)
        proc = ctx.Process(target=_report_child_limits, args=(sender, env_mb))
        proc.start()
        sender.close()
        soft, nice = receiver.recv()
        proc.join(10)
        assert nice >= 10, "the child runs at a lower priority"
        if expected is not None:
            assert soft == expected
        else:
            import resource

            assert soft == resource.getrlimit(resource.RLIMIT_AS)[0], "0 disables the limit"


def test_extract_under_default_rlimit_as_still_works(tmp_path, monkeypatch):
    """The 2 GiB default must leave room for PyMuPDF: a real PDF extracts in
    the limited child."""
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Unter dem Limit.")
    p = tmp_path / "limited.pdf"
    p.write_bytes(doc.tobytes())
    doc.close()
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "120")
    monkeypatch.delenv("RC_EXTRACT_RLIMIT_AS_MB", raising=False)
    from sync.document_text import extract_document_guarded

    assert "Unter dem Limit" in extract_document_guarded(p).text
