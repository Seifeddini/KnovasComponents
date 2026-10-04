"""``ExtractedDocument.source_metadata`` (spec section 3.5, L1).

The extractor's author, language, created and modified (plus the .eml
Content-Language header and the keyword and status file properties) travel
with the extracted document, through the forked extraction child and its
result queue, to the metadata mapping.
"""
import io
import pickle
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from sync import document_text
from sync.document_text import ExtractedDocument, extract_document, extract_document_guarded
from sync.metadata_fields import map_metadata

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

EML_WITH_LANGUAGE = (
    b"From: Muster AG <info@muster.example>\r\n"
    b"To: kanzlei@beispiel.example\r\n"
    b"Subject: Offerte\r\n"
    b"Date: Fri, 15 Mar 2024 10:22:00 +0100\r\n"
    b"Content-Language: de-CH\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Guten Tag. Anbei die Offerte.\r\n"
)


def test_default_is_an_empty_dict_and_old_constructors_still_work():
    doc = ExtractedDocument(text="x", sentences=None)
    assert doc.source_metadata == {}
    other = ExtractedDocument(text="y", sentences=None)
    assert doc.source_metadata is not other.source_metadata


def test_eml_fixture_carries_author_and_date(tmp_path):
    path = tmp_path / "sample.eml"
    path.write_bytes((FIXTURES / "sample.eml").read_bytes())
    doc = extract_document(path)
    assert doc.source_metadata["author"] == "sender@example.com"
    assert doc.source_metadata["created"].startswith("2024-01-01T12:00:00")
    # The Date header's day: Knovas refuses a timestamp with time and offset.
    assert map_metadata(doc.source_metadata, ".eml", {"email_date", "email_author"}) == {
        "document_date": "2024-01-01",
        "author": "sender@example.com",
    }


def test_eml_content_language_is_carried(tmp_path):
    path = tmp_path / "offerte.eml"
    path.write_bytes(EML_WITH_LANGUAGE)
    doc = extract_document(path)
    assert doc.source_metadata["eml:content_language"] == "de-CH"
    assert doc.source_metadata["author"] == "Muster AG <info@muster.example>"
    assert map_metadata(doc.source_metadata, ".eml", {"language", "email_author", "email_doc_type"}) == {
        "language": "de-CH",
        "author": "Muster AG",
        "doc_type": "correspondence.email",
    }


def test_docx_core_properties_are_carried(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("Vertrag mit Beispiel GmbH.")
    document.core_properties.author = "Beispiel GmbH"
    document.core_properties.language = "de-CH"
    document.core_properties.created = datetime(2019, 1, 2, 3, 4, 5)
    document.core_properties.modified = datetime(2020, 1, 2, 3, 4, 5)
    buf = io.BytesIO()
    document.save(buf)
    path = tmp_path / "vertrag.docx"
    path.write_bytes(buf.getvalue())
    md = extract_document(path).source_metadata
    assert md["author"] == "Beispiel GmbH"
    assert md["language"] == "de-CH"
    assert md["created"].startswith("2019-01-02")
    assert md["modified"].startswith("2020-01-02")


def test_pdf_metadata_is_carried(tmp_path):
    fitz = pytest.importorskip("fitz")
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "Rechnung der Muster AG.")
    pdf.set_metadata({"author": "Muster AG", "creationDate": "D:20190102030405", "modDate": "D:20200102030405"})
    path = tmp_path / "rechnung.pdf"
    pdf.save(path)
    md = extract_document(path).source_metadata
    assert md["author"] == "Muster AG"
    assert md["created"].startswith("2019-01-02")
    assert md["modified"].startswith("2020-01-02")


def test_docx_keywords_and_content_status_are_carried(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("Mietvertrag mit Beispiel GmbH.")
    document.core_properties.keywords = "Vertrag, Miete"
    document.core_properties.content_status = "Final"
    buf = io.BytesIO()
    document.save(buf)
    path = tmp_path / "mietvertrag.docx"
    path.write_bytes(buf.getvalue())
    md = extract_document(path).source_metadata
    assert md["docx:keywords"] == "Vertrag, Miete"
    assert md["docx:content_status"] == "Final"


def test_pdf_keywords_are_carried(tmp_path):
    fitz = pytest.importorskip("fitz")
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "Rechnung der Muster AG.")
    pdf.set_metadata({"keywords": "Rechnung; Kreditor"})
    path = tmp_path / "rechnung.pdf"
    pdf.save(path)
    assert extract_document(path).source_metadata["pdf:keywords"] == "Rechnung; Kreditor"


def test_a_keyword_list_the_library_cut_is_left_out(tmp_path):
    fitz = pytest.importorskip("fitz")
    from knovas_extract.result import Limits

    from sync.metadata_fields import MAX_SOURCE_VALUE_CHARS

    # The library crops a longer extra value to exactly its cap, mid-word.
    assert Limits().max_metadata_value_length == MAX_SOURCE_VALUE_CHARS
    keywords = "; ".join(f"Kreditorenrechnung {i:04d}" for i in range(250))
    assert len(keywords) > MAX_SOURCE_VALUE_CHARS
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "Rechnung der Muster AG.")
    pdf.set_metadata({"author": "Muster AG", "keywords": keywords})
    path = tmp_path / "rechnung.pdf"
    pdf.save(path)
    md = extract_document(path).source_metadata
    assert md["author"] == "Muster AG"
    assert "pdf:keywords" not in md


def test_plain_text_has_no_source_metadata(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("Ein Satz. Noch einer.", encoding="utf-8")
    assert extract_document(path).source_metadata == {}


def test_source_metadata_survives_the_fork_and_the_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "120")
    path = tmp_path / "offerte.eml"
    path.write_bytes(EML_WITH_LANGUAGE)
    direct = extract_document(path)
    guarded = extract_document_guarded(path, document_key="Postfach/offerte.eml")
    assert guarded.source_metadata == direct.source_metadata
    assert guarded.source_metadata["eml:content_language"] == "de-CH"


def test_extracted_document_with_source_metadata_pickles():
    doc = ExtractedDocument(
        text="x",
        sentences=None,
        title="Offerte",
        source_metadata={"author": "Muster AG", "created": "2024-03-15T10:22:00+01:00"},
    )
    assert pickle.loads(pickle.dumps(doc)) == doc


def _fake_result(metadata):
    content = SimpleNamespace(text="Ein Satz.", sentences=None, sections=None, pages=None, tables=None)
    return SimpleNamespace(content=content, metadata=metadata)


def test_extractor_without_metadata_attributes_gives_an_empty_dict(monkeypatch):
    monkeypatch.setattr(document_text, "extract", lambda raw, **kw: _fake_result(SimpleNamespace(extra={})))
    assert document_text._extract_bytes(b"x", ".txt").source_metadata == {}


def test_only_the_named_attributes_are_kept(monkeypatch):
    metadata = SimpleNamespace(
        title="Offerte",
        author="Muster AG",
        language=None,
        created="2024-03-15T10:22:00+01:00",
        modified="",
        page_count=None,
        extra={"eml:content_language": "de", "eml:message_id": "<id@muster.example>", "eml:to": "x@y.example"},
    )
    monkeypatch.setattr(document_text, "extract", lambda raw, **kw: _fake_result(metadata))
    doc = document_text._extract_bytes(b"x", ".eml")
    assert doc.source_metadata == {
        "author": "Muster AG",
        "created": "2024-03-15T10:22:00+01:00",
        "eml:content_language": "de",
    }
    # The existing extra copy is unchanged by the new field.
    assert doc.extra["eml:message_id"] == "<id@muster.example>"
