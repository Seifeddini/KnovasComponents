"""Extractor metadata to document fields (spec section 3.5).

``document_date`` comes only from an e-mail's Date header: never from the
file mtime, the Microsoft 365 lastModifiedDateTime or a PDF/DOCX/MD
created/modified date. Each of those is pinned below.
"""
import inspect
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from sync import metadata_fields
from sync.metadata_fields import (
    EMAIL_DOC_TYPE,
    FILE_PROPERTY_KEYS,
    ITEM_TARGETS,
    JUNK_AUTHORS,
    KEYWORD_SOURCES,
    METADATA_ITEMS,
    ITEM_RULE_VERSIONS,
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


VECTORS = Path(__file__).resolve().parents[2] / "contracts" / "vectors" / "metadata_fields.json"


def test_mapping_version_and_items():
    # New items need no bump: a bump re-sends every source that uses file properties.
    assert METADATA_MAPPING_VERSION == 1
    # A rule change for one item re-sends only the files that item applies to.
    assert ITEM_RULE_VERSIONS == {"email_date": 2}
    assert METADATA_ITEMS == (
        "language", "email_date", "email_doc_type", "email_author", "document_author",
        "keywords", "document_status",
    )
    assert set(ITEM_TARGETS) == set(METADATA_ITEMS)
    # No Message-ID, sender or recipient mapping (spec section 3.5).
    assert set(ITEM_TARGETS.values()) == {
        "language", "document_date", "doc_type", "author", "keywords", "status"}


def test_nothing_enabled_maps_nothing():
    assert map_metadata(EML_MD, ".eml", ()) == {}
    assert map_metadata(EML_MD, ".eml", None) == {}


def test_unknown_items_are_ignored():
    assert map_metadata(EML_MD, ".eml", {"email_message_id", "sender"}) == {}


# --- email_date ---------------------------------------------------------------


@pytest.mark.parametrize("ext", [".eml", ".msg", ".EML", "msg"])
def test_email_date_is_the_day_of_the_date_header(ext):
    # Knovas reads a date as a day, month, quarter or year; it refuses a
    # timestamp with time and offset as invalid_value.
    assert map_metadata(EML_MD, ext, {"email_date"}) == {"document_date": "2024-03-15"}


@pytest.mark.parametrize("created,day", [
    ("2024-03-15T23:30:00-05:00", "2024-03-15"),   # the header's own day, never converted
    ("2024-03-15T00:10:00+02:00", "2024-03-15"),
    ("2024-03-15 10:22:00", "2024-03-15"),
    ("2024-03-15", "2024-03-15"),
    ("Fri, 15 Mar 2024 10:22:00 +0100", "2024-03-15"),   # RFC 2822, as the header writes it
])
def test_email_date_takes_the_day_the_header_names(created, day):
    assert map_metadata({"created": created}, ".eml", {"email_date"}) == {"document_date": day}


@pytest.mark.parametrize("created", ["gestern", "2024-13-45T10:00:00", "2024-02-30", "15.03.2024 10:22"])
def test_email_date_skips_a_value_that_names_no_day(created):
    assert map_metadata({"created": created}, ".msg", {"email_date"}) == {}


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
        "document_date": "2024-03-15",
        "doc_type": "correspondence.email",
        "author": "Muster AG",
    }


def test_all_items_for_a_msg():
    assert map_metadata(EML_MD, ".msg", ALL_ITEMS) == {
        "document_date": "2024-03-15",
        "doc_type": "correspondence.email",
        "author": "Muster AG",
    }


# --- keywords and document_status (spec L1) ----------------------------------------------


def test_golden_vectors():
    cases = json.loads(VECTORS.read_text(encoding="utf-8"))
    assert len(cases) >= 12
    assert {"keywords", "document_status"} <= {item for case in cases for item in case["items"]}
    assert set(FILE_PROPERTY_KEYS) <= {key for case in cases for key in case["source_metadata"]}
    failed = [case["name"] for case in cases
              if map_metadata(case["source_metadata"], case["ext"], case["items"]) != case["fields"]]
    assert failed == []


def test_keywords_are_capped_at_32_values_in_order():
    raw = ", ".join(f"Stichwort {i}" for i in range(40))
    assert map_metadata({"docx:keywords": raw}, ".docx", {"keywords"}) == {
        "keywords": [f"Stichwort {i}" for i in range(32)]}


def test_a_keyword_over_256_characters_is_skipped_not_cut():
    raw = ";".join(["a" * 257, "b" * 256, "c"])
    assert map_metadata({"pdf:keywords": raw}, ".pdf", {"keywords"}) == {"keywords": ["b" * 256, "c"]}


def test_skipped_keywords_do_not_use_up_the_cap():
    raw = ", ".join([" "] * 10 + ["x" * 300] * 10 + [f"k{i}" for i in range(40)])
    assert map_metadata({"pdf:keywords": raw}, ".pdf", {"keywords"})["keywords"] == [
        f"k{i}" for i in range(32)]


def test_status_final_is_sent_as_written_and_empty_values_are_skipped():
    assert map_metadata({"docx:content_status": "Final"}, ".docx", {"document_status"}) == {
        "status": "Final"}
    assert map_metadata({"docx:content_status": "  "}, ".docx", {"document_status"}) == {}
    assert map_metadata({"docx:keywords": " ; , "}, ".docx", {"keywords"}) == {}


def test_word_keywords_and_status_end_to_end(tmp_path):
    docx = pytest.importorskip("docx")
    from sync.document_text import extract_document

    document = docx.Document()
    document.add_paragraph("Mietvertrag mit Beispiel GmbH.")
    document.core_properties.keywords = "Vertrag, Miete; vertrag"
    document.core_properties.content_status = "Final"
    buf = io.BytesIO()
    document.save(buf)
    path = tmp_path / "mietvertrag.docx"
    path.write_bytes(buf.getvalue())
    md = extract_document(path).source_metadata
    assert map_metadata(md, ".docx", {"keywords", "document_status"}) == {
        "keywords": ["Vertrag", "Miete"], "status": "Final"}


def test_a_keyword_utf8_cannot_encode_is_skipped_and_does_not_count():
    # Lone surrogates, and a pair kept as two code points: the payload goes out
    # as UTF-8 JSON, which cannot carry them, so keeping one would stop the
    # document's upload, not only its fields.
    raw = ", ".join(["\ud800", "Akte \udcff", chr(0xD83D) + chr(0xDE00)] + [f"k{i}" for i in range(40)])
    for ext, key in sorted(KEYWORD_SOURCES.items()):
        assert map_metadata({key: raw}, ext, {"keywords"}) == {
            "keywords": [f"k{i}" for i in range(32)]}, ext


@pytest.mark.parametrize("ext,key", sorted(KEYWORD_SOURCES.items()))
def test_brackets_nested_deeper_than_the_json_decoder_goes_yield_no_keywords(ext, key):
    # The longest value source_metadata_from carries; the JSON decoder of a
    # Windows Python gives up after about 3000 levels with a RecursionError.
    raw = "[" * 4095
    assert source_metadata_from(SimpleNamespace(extra={key: raw})) == {key: raw}
    assert map_metadata({key: raw}, ext, {"keywords"}) == {}


def test_a_recursion_error_of_the_json_decoder_reads_the_categories_as_text(monkeypatch):
    # Pins the guard on every platform: a Linux Python only gives up at 10000 levels.
    def too_deep(_raw):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(metadata_fields, "json", SimpleNamespace(loads=too_deep))
    assert map_metadata({"msg:categories": '["Projekt Alpha"]'}, ".msg", {"keywords"}) == {
        "keywords": ['["Projekt Alpha"]']}


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


def test_a_file_property_at_or_over_the_cap_is_left_out_not_cut():
    from sync.metadata_fields import MAX_SOURCE_VALUE_CHARS

    assert MAX_SOURCE_VALUE_CHARS == 4096
    # knovas-extract strips an extra value, then crops it to exactly the cap:
    # a value of that length may be cut, also one that ends in a space.
    metadata = SimpleNamespace(extra={
        "pdf:keywords": "k" * (MAX_SOURCE_VALUE_CHARS + 1),
        "docx:keywords": "k" * MAX_SOURCE_VALUE_CHARS,
        "msg:categories": "k" * (MAX_SOURCE_VALUE_CHARS - 1) + " ",
        "docx:content_status": 3,
    })
    assert source_metadata_from(metadata) == {}
    shorter = SimpleNamespace(extra={"docx:keywords": "k" * (MAX_SOURCE_VALUE_CHARS - 1)})
    assert source_metadata_from(shorter) == {"docx:keywords": "k" * (MAX_SOURCE_VALUE_CHARS - 1)}
