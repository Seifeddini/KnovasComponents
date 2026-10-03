"""The extraction stamp and the uploaded-text hash (spec L6)."""
from __future__ import annotations

import re
from importlib import metadata

import pytest

from sync import extraction_stamp
from sync.extraction_stamp import (
    EXTRACTION_SCHEMA,
    current_extraction_stamp,
    fields_values_digest,
    stamp_inputs,
    upload_text_sha256,
)

#: The real lookup, captured before any test replaces it.
REAL_VERSION = extraction_stamp._knovas_extract_version
SETTINGS = ("RC_PDF_TEXT_MODE", "RC_DOCX_TEXT_MODE", "RC_OCR_ENGINE", "RC_OCR_DPI",
            "RC_SENTENCE_EMIT_MAX_BYTES")
PARTS = [
    {"snippet": "Rechnung 17 an Muster AG", "page_number": 1, "sentence_number": 1},
    {"snippet": "\fSeite zwei: Honorar CHF 1'200", "page_number": 2, "sentence_number": 4},
]


@pytest.fixture(autouse=True)
def defaults(monkeypatch):
    for key in SETTINGS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(extraction_stamp, "_knovas_extract_version", lambda: "0.4.0a1")


class TestStamp:
    def test_sixteen_hex_characters_and_stable(self):
        stamp = current_extraction_stamp()
        assert re.fullmatch(r"[0-9a-f]{16}", stamp)
        assert current_extraction_stamp() == stamp

    @pytest.mark.parametrize("key,value", [
        ("RC_PDF_TEXT_MODE", "plain"),
        ("RC_DOCX_TEXT_MODE", "plain"),
        ("RC_OCR_ENGINE", "cli"),
        ("RC_OCR_DPI", "200"),
        ("RC_SENTENCE_EMIT_MAX_BYTES", "1048576"),
    ])
    def test_each_setting_changes_it(self, monkeypatch, key, value):
        before = current_extraction_stamp()
        monkeypatch.setenv(key, value)
        assert current_extraction_stamp() != before

    def test_the_library_version_changes_it(self, monkeypatch):
        before = current_extraction_stamp()
        monkeypatch.setattr(extraction_stamp, "_knovas_extract_version", lambda: "0.4.0")
        assert current_extraction_stamp() != before
        monkeypatch.setattr(extraction_stamp, "_knovas_extract_version", lambda: None)
        assert re.fullmatch(r"[0-9a-f]{16}", current_extraction_stamp())

    def test_the_schema_changes_it(self, monkeypatch):
        before = current_extraction_stamp()
        monkeypatch.setattr(extraction_stamp, "EXTRACTION_SCHEMA", EXTRACTION_SCHEMA + 1)
        assert current_extraction_stamp() != before

    def test_it_covers_versions_and_settings_only(self):
        assert stamp_inputs() == {
            "knovas_extract": "0.4.0a1", "pdf_text_mode": "layout", "docx_text_mode": "layout",
            "ocr_engine": "auto", "ocr_dpi": None, "sentence_emit_max_bytes": 0, "schema": 1,
        }

    def test_the_version_is_the_installed_distribution(self):
        REAL_VERSION.cache_clear()
        assert REAL_VERSION() == metadata.version("knovas-extract")


class TestUploadTextSha256:
    def test_equal_content_gives_an_equal_hash(self):
        same = [dict(reversed(list(part.items()))) for part in PARTS]
        assert upload_text_sha256(same, "d") == upload_text_sha256(PARTS, "d")
        assert re.fullmatch(r"[0-9a-f]{64}", upload_text_sha256(PARTS, None))

    def test_the_order_of_the_parts_matters(self):
        assert upload_text_sha256(list(reversed(PARTS)), None) != upload_text_sha256(PARTS, None)

    @pytest.mark.parametrize("change", [
        {"snippet": "Rechnung 18 an Muster AG"}, {"page_number": 3}, {"sentence_number": 2},
    ])
    def test_text_and_locations_matter(self, change):
        changed = [dict(PARTS[0], **change), PARTS[1]]
        assert upload_text_sha256(changed, None) != upload_text_sha256(PARTS, None)

    def test_numbers_count_as_the_transmission_sends_them(self):
        assert upload_text_sha256([{"snippet": "x", "page_number": 0}], None) == \
            upload_text_sha256([{"snippet": "x"}], None)
        assert upload_text_sha256([{"snippet": "x", "sentence_number": "4"}], None) == \
            upload_text_sha256([{"snippet": "x", "sentence_number": 4}], None)

    def test_tables_are_left_out(self):
        with_tables = [dict(PARTS[0], tables=[{"headers": ["Betrag"], "rows": [["1200"]]}]),
                       PARTS[1]]
        assert upload_text_sha256(with_tables, None) == upload_text_sha256(PARTS, None)

    def test_fields_title_and_description_matter(self):
        base = upload_text_sha256(PARTS, None)
        assert upload_text_sha256(PARTS, fields_values_digest({})) != base
        assert upload_text_sha256(PARTS, None, title="Rechnung 17") != base
        assert upload_text_sha256(PARTS, None, description="Mandat 17") != base


class TestFieldsValuesDigest:
    def test_none_is_none_and_key_order_does_not_matter(self):
        assert fields_values_digest(None) is None
        assert fields_values_digest({"a": 1, "b": ["x", "y"]}) == \
            fields_values_digest({"b": ["x", "y"], "a": 1})

    def test_a_clear_differs_from_values(self):
        assert fields_values_digest({}) != fields_values_digest({"doc_type": "invoice"})
        assert re.fullmatch(r"[0-9a-f]{64}", fields_values_digest({}))
