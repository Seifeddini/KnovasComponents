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
    assert is_unconvertible_error("encrypted .pdf: password required")
    assert is_unconvertible_error("resource limit exceeded: page_count")
    assert is_unconvertible_error("unsupported extension: .bin")
    assert not is_unconvertible_error("init failed: 503")
    assert not is_unconvertible_error("part 2 failed: 500")
    assert not is_unconvertible_error(None)


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


def test_guarded_timeout_is_classified_unconvertible(tmp_path, monkeypatch):
    """A timed-out file must be skipped, not retried every cycle."""
    import sync.document_text as dt

    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "1")
    # Module-level target so it survives pickling under the spawn start
    # method (Windows); production runs fork on Linux.
    monkeypatch.setattr(dt, "_extract_child", slow_extract_child)

    p = tmp_path / "slow.txt"
    p.write_text("Some text. More text.", encoding="utf-8")

    with pytest.raises(ConversionError) as exc:
        dt.extract_document_guarded(p)

    assert is_unconvertible_error(str(exc.value))
