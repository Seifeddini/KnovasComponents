import base64
import io
import time
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
    assert DEFAULT_SENTENCE_EMIT_MAX_BYTES == 0, "no gate by default (spec E4)"
    assert sentence_emit_max_bytes() == 0


def test_sentences_are_emitted_for_large_inputs_by_default(monkeypatch):
    """The 2 MiB gate on raw file size switched off the citations -- and every
    part's page number -- of most multi-page scans. A PDF is split page by
    page, in time linear in its pages; a positive value restores the gate."""
    from sync import document_text

    seen = {}

    def extract_stub(raw, **kwargs):
        seen.update(kwargs)
        raise document_text.UnsupportedFormatError("stub")

    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.setenv("RC_PDF_OCR_ENABLED", "false")
    large = b"x" * (3 * 1024 * 1024)
    monkeypatch.delenv("RC_SENTENCE_EMIT_MAX_BYTES", raising=False)
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(large, ".pdf")
    assert seen["emit_sentences"] is True
    monkeypatch.setenv("RC_SENTENCE_EMIT_MAX_BYTES", "2097152")
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(large, ".pdf")
    assert seen["emit_sentences"] is False, "a positive value is the old gate"


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


# --- one unpaged text gets sentences only up to UNPAGED_SENTENCE_MAX_CHARS ----
# pysbd maps every sentence back by searching the text from its start, so one
# unpaged text costs time in the square of its size (2 MiB of export rows
# ~40 s, 8 MiB past the 300 s ceiling). A PDF is split page by page.


def _spy_extract(monkeypatch):
    """The real extract(), recording `emit_sentences` of every call (its
    signature kept, so `extract_accepts` still sees `text_mode=`)."""
    import functools

    from sync import document_text

    calls = []
    real = document_text.extract

    @functools.wraps(real)
    def spy(raw, **kwargs):
        calls.append(kwargs.get("emit_sentences"))
        return real(raw, **kwargs)

    monkeypatch.setattr(document_text, "extract", spy)
    return calls


def _export_rows(size: int) -> bytes:
    """A weakly punctuated text export: one short "sentence" per row."""
    rows, total, i = [], 0, 0
    while total < size:
        row = f"{i:07d};K{1000 + i % 9000};{i % 99999}.{i % 100:02d};Konto {100 + i % 900} Mandant {1 + i % 50}\n"
        rows.append(row)
        total += len(row)
        i += 1
    return "".join(rows).encode()


def _docx_bytes(paragraphs, table_rows: int = 0) -> bytes:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    if table_rows:
        table = document.add_table(rows=0, cols=3)
        for i in range(table_rows):
            cells = table.add_row().cells
            cells[0].text, cells[1].text, cells[2].text = f"Pos {i}", f"Konto {1000 + i}", f"{i}.50"
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def test_a_short_text_is_split_in_one_pass(monkeypatch):
    from sync import document_text

    monkeypatch.delenv("RC_SENTENCE_EMIT_MAX_BYTES", raising=False)
    calls = _spy_extract(monkeypatch)
    doc = document_text._extract_bytes(b"First sentence. Second sentence.", ".txt")
    assert calls == [True], "the file size bounds a text file's text"
    assert len(doc.sentences) == 2


def test_an_unpaged_text_over_the_limit_is_uploaded_without_sentences(monkeypatch, caplog):
    import logging

    from sync import document_text

    monkeypatch.delenv("RC_SENTENCE_EMIT_MAX_BYTES", raising=False)
    monkeypatch.setattr(document_text, "UNPAGED_SENTENCE_MAX_CHARS", 1000)
    calls = _spy_extract(monkeypatch)
    with caplog.at_level(logging.INFO, logger="sync.document_text"):
        doc = document_text._extract_bytes(_export_rows(5000), ".txt")
    assert calls == [False], "extracted once, without sentences"
    assert doc.sentences is None
    assert doc.text.startswith("0000000;K1000;")
    assert any("Skipping sentence emission" in r.getMessage() for r in caplog.records)
    assert not any("Konto" in r.getMessage() for r in caplog.records), "counts only"


def test_a_large_mail_with_a_short_body_keeps_its_sentences(monkeypatch):
    """An attachment makes the file large, not the text: measured, then split."""
    from sync import document_text

    monkeypatch.delenv("RC_SENTENCE_EMIT_MAX_BYTES", raising=False)
    monkeypatch.setattr(document_text, "UNPAGED_SENTENCE_MAX_CHARS", 2000)
    calls = _spy_extract(monkeypatch)
    raw = (
        "From: a@example.invalid\r\nTo: b@example.invalid\r\nSubject: Beilage\r\n"
        "MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=XX\r\n\r\n"
        "--XX\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nErster Satz. Zweiter Satz.\r\n"
        "--XX\r\nContent-Type: application/octet-stream\r\n"
        "Content-Disposition: attachment; filename=beilage.bin\r\n"
        "Content-Transfer-Encoding: base64\r\n\r\n"
        + base64.encodebytes(bytes(6000)).decode().replace("\n", "\r\n")
        + "--XX--\r\n"
    ).encode()
    assert len(raw) > 2000
    doc = document_text._extract_bytes(raw, ".eml")
    assert calls == [False, True]
    assert [s.text for s in doc.sentences] == ["Erster Satz.", "Zweiter Satz."]


def test_a_docx_is_measured_before_it_is_split(monkeypatch):
    """A DOCX is zipped XML and carries its tables in the text: its file
    size says nothing about its text."""
    from sync import document_text

    monkeypatch.delenv("RC_SENTENCE_EMIT_MAX_BYTES", raising=False)
    calls = _spy_extract(monkeypatch)
    doc = document_text._extract_bytes(_docx_bytes(["Erster Satz. Zweiter Satz."]), ".docx")
    assert calls == [False, True]
    assert [s.text for s in doc.sentences] == ["Erster Satz.", "Zweiter Satz."]


def test_a_docx_table_over_the_limit_is_uploaded_without_sentences(monkeypatch):
    from sync import document_text

    monkeypatch.delenv("RC_SENTENCE_EMIT_MAX_BYTES", raising=False)
    monkeypatch.delenv("RC_DOCX_TEXT_MODE", raising=False)
    monkeypatch.setattr(document_text, "UNPAGED_SENTENCE_MAX_CHARS", 20_000)
    calls = _spy_extract(monkeypatch)
    doc = document_text._extract_bytes(_docx_bytes(["Kontoauszug."], table_rows=2000), ".docx")
    if not document_text.docx_tables_in_text(doc):
        pytest.skip("the installed knovas-extract has no DOCX layout mode")
    assert calls == [False], "extracted once: the rows are over the limit"
    assert doc.sentences is None
    assert "Pos 1999" in doc.text


def test_a_pdf_is_split_per_page_whatever_its_text_size(monkeypatch):
    fitz = pytest.importorskip("fitz")
    from sync import document_text

    monkeypatch.delenv("RC_SENTENCE_EMIT_MAX_BYTES", raising=False)
    monkeypatch.setenv("RC_PDF_OCR_ENABLED", "false")
    monkeypatch.setattr(document_text, "UNPAGED_SENTENCE_MAX_CHARS", 10)
    calls = _spy_extract(monkeypatch)
    pdf = fitz.open()
    for i in range(3):
        pdf.new_page().insert_text((72, 72), f"Seite {i + 1}. Text der Seite {i + 1}.")
    raw = pdf.tobytes()
    pdf.close()
    doc = document_text._extract_bytes(raw, ".pdf")
    assert calls == [True]
    assert {s.page_number for s in doc.sentences} == {1, 2, 3}


def test_a_4_mib_text_export_is_extracted_in_seconds(monkeypatch):
    """Real library, real size: split whole, this text took pysbd minutes."""
    from sync import document_text

    monkeypatch.delenv("RC_SENTENCE_EMIT_MAX_BYTES", raising=False)
    raw = _export_rows(4 * 1024 * 1024)
    started = time.monotonic()
    doc = document_text._extract_bytes(raw, ".txt")
    assert time.monotonic() - started < 30
    assert doc.sentences is None
    assert len(doc.text) > document_text.UNPAGED_SENTENCE_MAX_CHARS


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
    # min(240, 300 - 30) = 240, then never more than 300 - 60 - 10 = 230
    # (the worker count no longer enters the cap)
    assert opts.kwargs["time_budget_seconds"] == 230
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
    # min(240, 300 - 30) = 240, under the cap 300 - 45 - 10 = 245
    assert limits.ocr_time_budget_seconds == 240
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


_BUDGET_ENV = ("RC_EXTRACT_TIMEOUT_SECONDS", "RC_EXTRACT_TIMEOUT_PER_PAGE_SECONDS",
               "RC_EXTRACT_TIMEOUT_MAX_SECONDS", "RC_OCR_TIME_BUDGET_SECONDS")


@pytest.mark.parametrize("pages, timeout, page_timeout, expected", [
    # The audit's table (60 s page timeout, ceiling 300 s + 2 s/page): with
    # five or more OCR workers the old cap took these to the 10 s floor.
    (10, 300, 60, 230),
    (50, 300, 60, 230),
    (150, 300, 60, 230),
    (250, 500, 60, 240),
    (400, 800, 60, 240),
    (900, 1800, 60, 240),
    # short ceilings: one page timeout and the margin still fit
    (None, 120, 60, 50),
    (None, 90, 60, 20),
    (None, 120, 30, 80),
    (None, 60, 60, 10),   # never below the floor
    (None, 0, 60, 240),   # no ceiling: the default budget
])
def test_ocr_time_budget_derivation(monkeypatch, pages, timeout, page_timeout, expected):
    from sync.document_text import extract_timeout_seconds, ocr_time_budget_seconds

    for name in _BUDGET_ENV:
        monkeypatch.delenv(name, raising=False)
    if pages is not None:
        assert extract_timeout_seconds(pages) == timeout, "the ceiling the child derives for this PDF"
    assert ocr_time_budget_seconds(timeout, page_timeout) == expected


def test_ocr_time_budget_env_override_is_still_capped(monkeypatch):
    from sync.document_text import ocr_time_budget_seconds

    monkeypatch.setenv("RC_OCR_TIME_BUDGET_SECONDS", "900")
    assert ocr_time_budget_seconds(1800, 60) == 900
    assert ocr_time_budget_seconds(300, 60) == 230, "the env value is still capped by the kill"
    assert ocr_time_budget_seconds(0, 60) == 900, "no ceiling: the env value as is"


@pytest.mark.parametrize("workers", [None, "1", "5", "8"])
def test_the_budget_does_not_depend_on_the_worker_count(monkeypatch, workers):
    """7+ cores no longer matter: pages in flight finish in parallel."""
    from sync.document_text import ocr_options_kwargs

    for name in _BUDGET_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("RC_OCR_PAGE_TIMEOUT_SECONDS", raising=False)
    if workers is None:
        monkeypatch.delenv("RC_OCR_WORKERS", raising=False)
    else:
        monkeypatch.setenv("RC_OCR_WORKERS", workers)
    assert ocr_options_kwargs(300)["time_budget_seconds"] == 230


def test_ocr_workers_env(monkeypatch, caplog):
    import logging

    from sync.document_text import ocr_workers

    monkeypatch.delenv("RC_OCR_WORKERS", raising=False)
    assert ocr_workers() is None, "unset: the library sizes the pool"
    for raw, expected in (("3", 3), ("64", 8), ("0", 1), ("-2", 1)):
        monkeypatch.setenv("RC_OCR_WORKERS", raw)
        assert ocr_workers() == expected, raw
    monkeypatch.setenv("RC_OCR_WORKERS", "many")
    with caplog.at_level(logging.WARNING, logger="sync.document_text"):
        assert ocr_workers() is None
    assert any("RC_OCR_WORKERS" in r.getMessage() for r in caplog.records)


def test_unset_workers_leave_the_pool_and_its_ceiling_to_the_library(monkeypatch):
    """OcrOptions(workers=None) -- the library's cgroup-aware default -- and
    Limits.max_ocr_workers at the library's 8: None there would break the
    library's min()."""
    from knovas_extract.result import Limits

    from sync import document_text

    extract_stub, seen = _signature_stub(ocr_options=True, text_mode=False, limits=True)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.setattr(document_text, "OcrOptions", _FakeOcrOptions)
    monkeypatch.delenv("RC_OCR_WORKERS", raising=False)
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "0")
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert seen["ocr"].kwargs["workers"] is None
    assert seen["limits"].max_ocr_workers == Limits().max_ocr_workers


def test_the_library_takes_workers_none(monkeypatch):
    from sync import document_text

    if document_text.OcrOptions is None:
        pytest.skip("needs knovas-extract >= 0.4")
    monkeypatch.delenv("RC_OCR_WORKERS", raising=False)
    options = document_text.build_ocr_options(document_text.ocr_options_kwargs(300))
    limits = document_text.build_ocr_limits(document_text.ocr_options_kwargs(300))
    assert options.workers is None
    assert limits.max_ocr_workers == 8


def test_pdf_text_mode_env(monkeypatch):
    from sync.document_text import pdf_text_mode

    monkeypatch.delenv("RC_PDF_TEXT_MODE", raising=False)
    assert pdf_text_mode() == "layout", "markdown-lite layout is the default since 0.2.0"
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "Shadow")
    assert pdf_text_mode() == "shadow"
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "plain")
    assert pdf_text_mode() == "plain"
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "rows")
    assert pdf_text_mode() == "layout", "an invalid value falls back to the default"


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


# --- partial notes: one rule in both components (spec E1) --------------------


def test_partial_note_for_reads_extra_defensively():
    from sync.document_text import ExtractedDocument, partial_note_for

    assert partial_note_for(ExtractedDocument(text="x", sentences=None, extra={}), expect_ocr=True) is None
    assert partial_note_for(ExtractedDocument(text="x", sentences=None, extra=None), expect_ocr=True) is None
    as_strings = ExtractedDocument(
        text="x", sentences=None,
        extra={"pdf:ocr_pages_skipped": "7", "pdf:ocr_pages_failed": "0", "pdf:ocr_backend": " CLI "},
    )
    assert partial_note_for(as_strings, expect_ocr=True) == {
        "ocr_pages_skipped": 7, "ocr_pages_failed": 0, "ocr_backend": "cli",
    }
    zero = ExtractedDocument(text="x", sentences=None, extra={"pdf:ocr_pages_skipped": "0"})
    assert partial_note_for(zero, expect_ocr=True) is None


@pytest.mark.parametrize("case, expect_ocr, note, degraded", [
    ("born_digital", True, None, False),
    ("mixed", True, None, False),
    ("starved", True, {"ocr_pages_skipped": 12, "ocr_pages_failed": 0, "ocr_pages": 40,
                       "text_pages": 0, "ocr_backend": "tesserocr"}, False),
    ("failed", True, {"ocr_pages_skipped": 0, "ocr_pages_failed": 1, "ocr_pages": 9,
                      "text_pages": 2, "ocr_backend": "cli"}, False),
    ("no_engine", True, {"ocr_pages_skipped": 5, "ocr_pages_failed": 0, "ocr_pages": 0,
                         "text_pages": 2, "ocr_backend": "none"}, True),
    ("uncounted", True, {"ocr_pages": 0, "ocr_backend": "none"}, True),
    ("uncounted", False, None, False),
])
def test_partial_rule_on_the_0_4_key_combinations(case, expect_ocr, note, degraded):
    """Spec E1 on the metadata knovas-extract 0.4 really reports: a
    born-digital PDF extracted with ``ocr=`` is complete, a failed page makes
    a document partial, and only a missing engine is a degraded backend."""
    from tests.helpers import OCR_EXTRA_04

    from sync.document_text import ExtractedDocument, ocr_backend_missing, partial_note_for

    doc = ExtractedDocument(text="x", sentences=None, extra=dict(OCR_EXTRA_04[case]))
    got = partial_note_for(doc, expect_ocr=expect_ocr)
    assert got == note
    assert ocr_backend_missing(got) is degraded


def test_a_born_digital_pdf_extracted_with_ocr_options_is_complete(tmp_path, monkeypatch):
    """The audit's reproduction: with ``ocr=`` the library reports backend
    "none" and zero skipped pages for a PDF that needed no OCR -- whether or
    not Tesseract is installed. Before spec E1 every such PDF was partial."""
    fitz = pytest.importorskip("fitz")
    from sync import document_text

    if document_text.OcrOptions is None or not document_text.extract_accepts("ocr"):
        pytest.skip("needs knovas-extract >= 0.4 (ocr=)")
    monkeypatch.setenv("RC_PDF_OCR_ENABLED", "true")
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "0")
    pdf = fitz.open()
    for i in range(2):
        pdf.new_page().insert_text((72, 72), f"Seite {i + 1}. Digitaler Text.")
    path = tmp_path / "digital.pdf"
    path.write_bytes(pdf.tobytes())
    pdf.close()

    doc = document_text.extract_document(path)

    assert doc.extra["pdf:ocr_pages_skipped"] == 0
    assert doc.extra["pdf:ocr_backend"] == "none"
    assert document_text.partial_note_for(doc, expect_ocr=True) is None


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


# --- resolution: the library's native-resolution rule unless set (spec E3) ---


def test_ocr_dpi_env(monkeypatch, caplog):
    import logging

    from sync.document_text import ocr_dpi

    monkeypatch.delenv("RC_OCR_DPI", raising=False)
    assert ocr_dpi() is None, "unset: native resolution, never upsampled"
    for raw, expected in (("200", 200), ("30", 30), ("1200", 1200), (" 150 ", 150), ("0150", 150)):
        monkeypatch.setenv("RC_OCR_DPI", raw)
        assert ocr_dpi() == expected, raw
    # The same values scripts/lib/test_rc_extraction_settings.sh refuses.
    for raw in ("29", "1201", "0", "-300", "300dpi", "3e2"):
        monkeypatch.setenv("RC_OCR_DPI", raw)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="sync.document_text"):
            assert ocr_dpi() is None, raw
        assert [r.getMessage().split("=")[0] for r in caplog.records] == ["Invalid RC_OCR_DPI"], raw


def test_no_dpi_reaches_the_library_unless_configured(monkeypatch):
    from sync import document_text

    extract_stub, seen = _signature_stub(ocr_options=True, text_mode=False)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.setattr(document_text, "OcrOptions", _FakeOcrOptions)
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "0")
    monkeypatch.delenv("RC_OCR_DPI", raising=False)
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert "dpi" not in seen["ocr"].kwargs
    monkeypatch.setenv("RC_OCR_DPI", "150")
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert seen["ocr"].kwargs["dpi"] == 150


def test_the_library_default_dpi_applies_when_unset(monkeypatch):
    """With the real OcrOptions: no dpi keeps its None default -- native
    resolution capped at 300, never upsampled (a 150 dpi fax: CER 0.028
    native against 0.145 upsampled to 300)."""
    from sync import document_text

    if document_text.OcrOptions is None:
        pytest.skip("needs knovas-extract >= 0.4")
    monkeypatch.delenv("RC_OCR_DPI", raising=False)
    options = document_text.build_ocr_options(document_text.ocr_options_kwargs(300))
    assert options.dpi is None


# --- OCR settings validated (spec E5) ----------------------------------------


def test_tesseract_language_is_validated(monkeypatch, caplog):
    import logging

    from sync.document_text import tesseract_language

    monkeypatch.delenv("RC_TESSERACT_LANG", raising=False)
    assert tesseract_language() == "deu+eng"
    for good in ("deu", "deu+eng", "deu+fra+ita", "chi_sim+eng", "osd"):
        monkeypatch.setenv("RC_TESSERACT_LANG", good)
        assert tesseract_language() == good
    # The same values scripts/lib/test_rc_extraction_settings.sh refuses.
    for bad in ("deu eng", "deu,eng", "deu+", "+eng", "deu++eng", "../deu", "deu/eng", "dé"):
        monkeypatch.setenv("RC_TESSERACT_LANG", bad)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="sync.document_text"):
            assert tesseract_language() == "deu+eng", bad
        assert len(caplog.records) == 1 and "RC_TESSERACT_LANG" in caplog.records[0].getMessage(), bad


def test_ocr_page_timeout_and_page_cap_must_be_at_least_one(monkeypatch, caplog):
    import logging

    from sync.document_text import (
        DEFAULT_OCR_MAX_PAGES,
        DEFAULT_OCR_PAGE_TIMEOUT_SECONDS,
        ocr_options_kwargs,
    )

    monkeypatch.setenv("RC_OCR_PAGE_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("RC_OCR_MAX_PAGES", "none")
    with caplog.at_level(logging.WARNING, logger="sync.document_text"):
        opts = ocr_options_kwargs(300)
    assert opts["page_timeout_seconds"] == DEFAULT_OCR_PAGE_TIMEOUT_SECONDS
    assert opts["max_ocr_pages"] == DEFAULT_OCR_MAX_PAGES
    names = " ".join(r.getMessage() for r in caplog.records)
    assert "RC_OCR_PAGE_TIMEOUT_SECONDS" in names and "RC_OCR_MAX_PAGES" in names
    monkeypatch.setenv("RC_OCR_PAGE_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("RC_OCR_MAX_PAGES", "1")
    opts = ocr_options_kwargs(300)
    assert (opts["page_timeout_seconds"], opts["max_ocr_pages"]) == (1, 1)


@pytest.mark.parametrize("message, setting", [
    ("OcrOptions.language must be a Tesseract language string like 'deu+eng'", "RC_TESSERACT_LANG"),
    ("OcrOptions.dpi must be between 30 and 1200", "RC_OCR_DPI"),
    ("OcrOptions.engine must be one of auto|tesserocr|cli|mupdf", "RC_OCR_ENGINE"),
    ("OcrOptions.workers must be >= 1", "RC_OCR_WORKERS"),
    ("Limits.max_ocr_pages must be >= 1", "RC_OCR_MAX_PAGES"),
    ("something the Connector does not know", "OCR options"),
])
def test_a_refused_ocr_setting_is_a_retryable_configuration_error(monkeypatch, message, setting):
    """The library still refuses the options: never "corrupt .pdf" (that
    parked every PDF for good), but a message naming the setting."""
    from sync import document_text

    class RefusingOcrOptions:
        def __init__(self, **kwargs):
            raise ValueError(message)

    extract_stub, seen = _signature_stub(ocr_options=True, text_mode=True, limits=True)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.setattr(document_text, "OcrOptions", RefusingOcrOptions)
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "0")
    with pytest.raises(document_text.ConversionError) as exc:
        document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert str(exc.value) == f"{document_text.CONFIG_INVALID_PREFIX}: {setting}"
    assert is_unconvertible_error(str(exc.value)) is False
    assert seen == {}, "extract() is never called with options the library refused"


def test_the_child_reports_a_refused_setting_as_configuration_not_corrupt(tmp_path, monkeypatch):
    import queue as queue_mod

    from sync import document_text

    class RefusingOcrOptions:
        def __init__(self, **kwargs):
            raise ValueError("OcrOptions.language must be a Tesseract language string like 'deu+eng'")

    extract_stub, _seen = _signature_stub(ocr_options=True, text_mode=True, limits=True)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.setattr(document_text, "OcrOptions", RefusingOcrOptions)
    # nice + RLIMIT_AS would hit the test process itself
    monkeypatch.setattr(document_text, "_apply_child_limits", lambda: None)
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "0")
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    out: queue_mod.Queue = queue_mod.Queue()
    document_text._extract_child(str(pdf), out)
    messages = []
    while not out.empty():
        messages.append(out.get_nowait())
    assert messages[-1] == ("conversion", "extraction configuration invalid: RC_TESSERACT_LANG"), messages


def test_an_engine_name_never_reaches_a_backend_slot(monkeypatch):
    """``OcrOptions.backend`` takes an injected IOcrBackend object. The alias
    engine -> backend would have passed the engine NAME there if a library
    dropped ``engine`` (spec E7)."""
    from sync import document_text

    class OnlyBackend:
        def __init__(self, backend=None, language="deu+eng", cache=None):
            self.backend, self.language, self.cache = backend, language, cache

    monkeypatch.setattr(document_text, "OcrOptions", OnlyBackend)
    built = document_text.build_ocr_options({"engine": "cli", "language": "deu", "cache": "c"})
    assert built.backend is None
    assert (built.language, built.cache) == ("deu", "c")


# --- DOCX layout mode (spec L3) ----------------------------------------------


def test_docx_text_mode_env(monkeypatch, caplog):
    import logging

    from sync.document_text import docx_text_mode

    monkeypatch.delenv("RC_DOCX_TEXT_MODE", raising=False)
    assert docx_text_mode() == "layout", "tables in the text by default"
    monkeypatch.setenv("RC_DOCX_TEXT_MODE", " Plain ")
    assert docx_text_mode() == "plain"
    monkeypatch.setenv("RC_DOCX_TEXT_MODE", "shadow")
    with caplog.at_level(logging.WARNING, logger="sync.document_text"):
        assert docx_text_mode() == "layout", "shadow is a PDF mode: invalid here"
    assert any("RC_DOCX_TEXT_MODE" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("env, ext, sent", [
    (None, ".docx", "layout"),
    ("layout", ".docx", "layout"),
    ("plain", ".docx", None),
    (None, ".eml", None),
    (None, ".msg", None),
    (None, ".txt", None),
])
def test_docx_layout_mode_is_requested_for_docx_only(monkeypatch, env, ext, sent):
    from sync import document_text

    extract_stub, seen = _signature_stub(ocr_options=False, text_mode=True)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    if env is None:
        monkeypatch.delenv("RC_DOCX_TEXT_MODE", raising=False)
    else:
        monkeypatch.setenv("RC_DOCX_TEXT_MODE", env)
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"PK stub", ext)
    # the stub records its own default ("plain") when nothing was sent
    assert seen["text_mode"] == (sent or "plain")


def test_docx_layout_mode_is_withheld_from_a_library_without_text_mode(monkeypatch):
    from sync import document_text

    extract_stub, seen = _signature_stub(ocr_options=False, text_mode=False)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.delenv("RC_DOCX_TEXT_MODE", raising=False)
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"PK stub", ".docx")
    assert "text_mode" not in seen


def test_docx_tables_in_text_reads_the_library_metadata():
    from sync.document_text import ExtractedDocument, docx_tables_in_text

    layout = ExtractedDocument(text="x", sentences=None, extra={"docx:text_mode": "layout", "docx:layout_tables": 2})
    assert docx_tables_in_text(layout) is True
    assert docx_tables_in_text(ExtractedDocument(text="x", sentences=None, extra={})) is False
    assert docx_tables_in_text(ExtractedDocument(text="x", sentences=None, extra=None)) is False
