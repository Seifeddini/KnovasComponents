import io
from pathlib import Path

import pytest

from sync.document_text import (
    ConversionError,
    bytes_to_markdown,
    file_to_markdown,
    is_syncable_extension,
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


def test_eml_fixture():
    raw = (FIXTURES / "sample.eml").read_bytes()
    md = bytes_to_markdown(raw, ".eml")
    assert "Sample Email" in md
    assert "Hello from the sample email" in md


def test_docx_conversion():
    docx = pytest.importorskip("docx")
    buf = io.BytesIO()
    document = docx.Document()
    document.add_heading("Section One", level=1)
    document.add_paragraph("Paragraph text.")
    document.save(buf)
    md = bytes_to_markdown(buf.getvalue(), ".docx")
    assert "# Section One" in md
    assert "Paragraph text." in md


def test_pdf_conversion():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello PDF content")
    raw = doc.tobytes()
    doc.close()
    md = bytes_to_markdown(raw, ".pdf")
    assert "Hello PDF content" in md
    assert "## Page 1" in md


def test_empty_pdf_raises():
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
    with pytest.raises(ConversionError):
        file_to_markdown(p)


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
