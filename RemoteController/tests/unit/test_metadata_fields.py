"""Extractor metadata to document fields (spec section 3.5).

``document_date`` comes only from an e-mail's Date header: never from the
file mtime, the Microsoft 365 lastModifiedDateTime or a PDF/DOCX/MD
created/modified date. Each of those is pinned below.
"""
import inspect
import io
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sync.metadata_fields import (
    EMAIL_DOC_TYPE,
    ITEM_TARGETS,
    JUNK_AUTHORS,
    METADATA_ITEMS,
    METADATA_MAPPING_VERSION,
    map_metadata,
    source_metadata_from,
)

ALL_ITEMS = frozenset(METADATA_ITEMS)
EML_MD = {
    "author": "Muster AG <info@muster.example>",
    "created": "2024-03-15T10:22:00+01:00",
    "eml:content_language": "de-CH",
}


def test_mapping_version_and_items():
    assert METADATA_MAPPING_VERSION == 1
    assert set(METADATA_ITEMS) == {
        "language", "email_date", "email_doc_type", "email_author", "document_author",
    }
    assert set(ITEM_TARGETS) == set(METADATA_ITEMS)
    # No Message-ID, sender or recipient mapping (spec section 3.5).
    assert set(ITEM_TARGETS.values()) == {"language", "document_date", "doc_type", "author"}


def test_nothing_enabled_maps_nothing():
    assert map_metadata(EML_MD, ".eml", ()) == {}
    assert map_metadata(EML_MD, ".eml", None) == {}


def test_unknown_items_are_ignored():
    assert map_metadata(EML_MD, ".eml", {"email_message_id", "sender"}) == {}


# --- email_date ---------------------------------------------------------------


@pytest.mark.parametrize("ext", [".eml", ".msg", ".EML", "msg"])
def test_email_date_is_the_date_header_verbatim(ext):
    assert map_metadata(EML_MD, ext, {"email_date"}) == {"document_date": "2024-03-15T10:22:00+01:00"}


@pytest.mark.parametrize("ext", [".pdf", ".docx", ".md", ".txt"])
def test_email_date_never_reads_a_document_created_or_modified_date(ext):
    md = {"created": "2019-01-02T03:04:05", "modified": "2020-01-02T03:04:05", "author": "Muster AG"}
    assert "document_date" not in map_metadata(md, ext, ALL_ITEMS)


def test_email_date_never_falls_back_to_modified():
    md = {"modified": "2024-03-15T10:22:00+01:00"}
    assert "document_date" not in map_metadata(md, ".eml", ALL_ITEMS)


def test_email_date_skips_a_missing_or_blank_date():
    assert map_metadata({}, ".eml", {"email_date"}) == {}
    assert map_metadata({"created": "  "}, ".msg", {"email_date"}) == {}


def test_mapping_takes_no_file_or_remote_date():
    # The only inputs are the extractor's source_metadata and the extension:
    # an mtime or an M365 lastModifiedDateTime has no way in.
    assert list(inspect.signature(map_metadata).parameters) == ["md", "ext", "enabled"]
    md = {"lastModifiedDateTime": "2024-03-15T10:22:00Z", "mtime": "2024-03-15T10:22:00Z"}
    for ext in (".pdf", ".docx", ".md", ".eml", ".msg"):
        assert "document_date" not in map_metadata(md, ext, ALL_ITEMS)


def test_source_metadata_never_carries_a_file_mtime(tmp_path):
    from sync.document_text import extract_document

    path = tmp_path / "note.md"
    path.write_text("Ein Satz. Noch ein Satz.", encoding="utf-8")
    stamp = datetime(2011, 5, 6, 7, 8, 9, tzinfo=timezone.utc).timestamp()
    os.utime(path, (stamp, stamp))
    doc = extract_document(path)
    assert "2011" not in " ".join(doc.source_metadata.values())
    assert map_metadata(doc.source_metadata, ".md", ALL_ITEMS) == {}


def test_m365_last_modified_never_reaches_the_mapping():
    # A RemoteFile's modified_iso (lastModifiedDateTime) is not an extractor
    # value: source_metadata_from reads the knovas-extract Metadata only.
    metadata = SimpleNamespace(
        author=None, language=None, created=None, modified=None,
        extra={"lastModifiedDateTime": "2024-03-15T10:22:00Z", "modified_iso": "2024-03-15T10:22:00Z"},
    )
    md = source_metadata_from(metadata)
    assert md == {}
    assert map_metadata(md, ".docx", ALL_ITEMS) == {}


def test_pdf_created_never_becomes_document_date(tmp_path):
    fitz = pytest.importorskip("fitz")
    from sync.document_text import extract_document

    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "Vertrag mit Muster AG.")
    pdf.set_metadata({"author": "Muster AG", "creationDate": "D:20190102030405", "modDate": "D:20200102030405"})
    path = tmp_path / "vertrag.pdf"
    pdf.save(path)
    doc = extract_document(path)
    assert doc.source_metadata.get("created", "").startswith("2019")
    mapped = map_metadata(doc.source_metadata, ".pdf", ALL_ITEMS)
    assert "document_date" not in mapped
    assert mapped.get("author") == "Muster AG"


def test_docx_created_never_becomes_document_date(tmp_path):
    docx = pytest.importorskip("docx")
    from sync.document_text import extract_document

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
    doc = extract_document(path)
    assert doc.source_metadata.get("created", "").startswith("2019")
    mapped = map_metadata(doc.source_metadata, ".docx", ALL_ITEMS)
    assert "document_date" not in mapped
    assert mapped == {"author": "Beispiel GmbH", "language": "de-CH"}


# --- email_doc_type -----------------------------------------------------------


@pytest.mark.parametrize("ext", [".eml", ".msg"])
def test_email_doc_type_for_mail(ext):
    assert map_metadata({}, ext, {"email_doc_type"}) == {"doc_type": EMAIL_DOC_TYPE}
    assert EMAIL_DOC_TYPE == "correspondence.email"


@pytest.mark.parametrize("ext", [".pdf", ".docx", ".md", ".txt"])
def test_email_doc_type_never_for_documents(ext):
    assert map_metadata(EML_MD, ext, {"email_doc_type"}) == {}


def test_pdf_in_an_email_only_source_maps_nothing():
    md = {"author": "Muster AG", "created": "2024-01-01", "language": "de"}
    items = {"email_date", "email_doc_type", "email_author"}
    assert map_metadata(md, ".pdf", items) == {}


# --- email_author ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Muster AG <info@muster.example>", "Muster AG"),
        ('"Muster, AG" <info@muster.example>', "Muster, AG"),
        ("info@muster.example", "info@muster.example"),
        ("<info@muster.example>", "info@muster.example"),
        ('"" <info@muster.example>', "info@muster.example"),
        ("Muster AG", "Muster AG"),
        ('"Beispiel GmbH"', "Beispiel GmbH"),
    ],
)
def test_email_author_is_the_display_name_else_the_address(raw, expected):
    assert map_metadata({"author": raw}, ".eml", {"email_author"}) == {"author": expected}
    assert map_metadata({"author": raw}, ".msg", {"email_author"}) == {"author": expected}


def test_email_author_skips_blank_from():
    assert map_metadata({"author": "  "}, ".eml", {"email_author"}) == {}
    assert map_metadata({}, ".eml", {"email_author"}) == {}
    assert map_metadata({"author": '""'}, ".eml", {"email_author"}) == {}


def test_email_author_is_not_a_document_author():
    assert map_metadata({"author": "Muster AG"}, ".pdf", {"email_author"}) == {}
    assert map_metadata({"author": "Muster AG"}, ".eml", {"document_author"}) == {}


# --- document_author --------------------------------------------------------------


@pytest.mark.parametrize("ext", [".pdf", ".docx", ".md"])
def test_document_author(ext):
    assert map_metadata({"author": "Beispiel GmbH"}, ext, {"document_author"}) == {"author": "Beispiel GmbH"}


@pytest.mark.parametrize(
    "junk",
    ["Administrator", "admin", "USER", "Owner", "unknown", "Author", "Microsoft Office User", "", "   "],
)
def test_junk_document_authors_are_skipped(junk):
    assert map_metadata({"author": junk}, ".docx", {"document_author"}) == {}


def test_junk_list_is_the_spec_list():
    assert JUNK_AUTHORS == {
        "administrator", "admin", "user", "owner", "unknown", "author", "microsoft office user",
    }


def test_document_author_never_for_txt_or_mail():
    for ext in (".txt", ".eml", ".msg"):
        assert map_metadata({"author": "Beispiel GmbH"}, ext, {"document_author"}) == {}


# --- language -------------------------------------------------------------------------


@pytest.mark.parametrize("ext", [".pdf", ".docx", ".md"])
def test_language_from_document_properties(ext):
    assert map_metadata({"language": "de-CH"}, ext, {"language"}) == {"language": "de-CH"}


def test_language_from_content_language_for_eml():
    assert map_metadata(EML_MD, ".eml", {"language"}) == {"language": "de-CH"}
    # .eml never reads the document-properties key
    assert map_metadata({"language": "fr"}, ".eml", {"language"}) == {}


def test_no_language_for_msg():
    md = {"language": "de", "eml:content_language": "de"}
    assert map_metadata(md, ".msg", {"language"}) == {}


def test_no_language_for_txt():
    assert map_metadata({"language": "de"}, ".txt", {"language"}) == {}


@pytest.mark.parametrize("value", ["de", "DE", "gsw", "de-CH", "en-US", "zh-Hant-TW", "sl-rozaj-biske"])
def test_language_pattern_accepts(value):
    assert map_metadata({"language": value}, ".docx", {"language"}) == {"language": value}


@pytest.mark.parametrize(
    "value",
    ["x-default", "X-Default", "und", "UND", "und-CH", "d", "deutsch", "de_CH", "de, en", "de-", "1de", "de-CHCHCHCHC"],
)
def test_language_pattern_refuses(value):
    assert map_metadata({"language": value}, ".pdf", {"language"}) == {}


# --- everything at once -----------------------------------------------------------


def test_all_items_for_an_eml():
    assert map_metadata(EML_MD, ".eml", ALL_ITEMS) == {
        "language": "de-CH",
        "document_date": "2024-03-15T10:22:00+01:00",
        "doc_type": "correspondence.email",
        "author": "Muster AG",
    }


def test_all_items_for_a_msg():
    assert map_metadata(EML_MD, ".msg", ALL_ITEMS) == {
        "document_date": "2024-03-15T10:22:00+01:00",
        "doc_type": "correspondence.email",
        "author": "Muster AG",
    }


# --- source_metadata_from --------------------------------------------------------------


def test_source_metadata_from_reads_the_four_attributes_and_content_language():
    metadata = SimpleNamespace(
        title="Betreff", author=" Muster AG ", language="de", created="2024-01-01T00:00:00",
        modified="2024-01-02T00:00:00", page_count=3,
        extra={"eml:content_language": "de-CH", "eml:message_id": "<x@y>", "eml:to": "a@b.example"},
    )
    assert source_metadata_from(metadata) == {
        "author": "Muster AG",
        "language": "de",
        "created": "2024-01-01T00:00:00",
        "modified": "2024-01-02T00:00:00",
        "eml:content_language": "de-CH",
    }


def test_source_metadata_from_skips_missing_and_non_string_values():
    metadata = SimpleNamespace(author=None, language="", created=42, extra=None)
    assert source_metadata_from(metadata) == {}
    assert source_metadata_from(None) == {}
    assert source_metadata_from(object()) == {}


def test_source_metadata_from_reads_the_file_properties():
    metadata = SimpleNamespace(
        author=None, language=None, created=None, modified=None,
        extra={"pdf:keywords": " Rechnung, Kreditor ", "docx:keywords": "Vertrag",
               "msg:categories": '["Projekt Alpha"]', "docx:content_status": "Final",
               "docx:category": "Intern", "pdf:subject": "Offerte", "docx:revision": "3"},
    )
    assert source_metadata_from(metadata) == {
        "pdf:keywords": "Rechnung, Kreditor",
        "docx:keywords": "Vertrag",
        "msg:categories": '["Projekt Alpha"]',
        "docx:content_status": "Final",
    }


def test_a_file_property_over_the_cap_is_left_out_not_cut():
    from sync.metadata_fields import MAX_SOURCE_VALUE_CHARS

    assert MAX_SOURCE_VALUE_CHARS == 4096
    metadata = SimpleNamespace(extra={
        "pdf:keywords": "k" * (MAX_SOURCE_VALUE_CHARS + 1),
        "docx:keywords": "k" * MAX_SOURCE_VALUE_CHARS,
        "docx:content_status": 3,
    })
    assert source_metadata_from(metadata) == {"docx:keywords": "k" * MAX_SOURCE_VALUE_CHARS}
