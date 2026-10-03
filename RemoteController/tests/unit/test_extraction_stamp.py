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

#: The real lookups, captured before any test replaces them.
REAL_VERSION = extraction_stamp._knovas_extract_version
REAL_COMMIT = extraction_stamp.knovas_extract_commit
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
    monkeypatch.setattr(extraction_stamp, "knovas_extract_commit", lambda: None)


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

    def test_the_library_commit_changes_it(self, monkeypatch):
        """A pin bump keeps the version string (0.4.0a1 before and after
        the release): the commit alone marks every document for re-extraction."""
        before = current_extraction_stamp()
        monkeypatch.setattr(extraction_stamp, "knovas_extract_commit", lambda: "b" * 40)
        pinned = current_extraction_stamp()
        monkeypatch.setattr(extraction_stamp, "knovas_extract_commit", lambda: "c" * 40)
        assert len({before, pinned, current_extraction_stamp()}) == 3

    def test_the_schema_changes_it(self, monkeypatch):
        before = current_extraction_stamp()
        monkeypatch.setattr(extraction_stamp, "EXTRACTION_SCHEMA", EXTRACTION_SCHEMA + 1)
        assert current_extraction_stamp() != before

    def test_it_covers_versions_and_settings_only(self):
        assert stamp_inputs() == {
            "knovas_extract": "0.4.0a1", "knovas_extract_commit": None,
            "pdf_text_mode": "layout", "docx_text_mode": "layout",
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


class TestCanonicalJson:
    """Strings enter the hashes escaped as the transmission sends them
    (requests' ``json=``: non-ASCII as ``\\u`` escapes)."""

    def test_lone_surrogates_hash(self):
        # PyMuPDF returns a PDF /Title or /Subject cut through a UTF-16 pair
        # with lone surrogates; the upload sends them, so they must hash.
        digest = upload_text_sha256([{"snippet": "a\udc80"}], None,
                                    title="R\udced\udcb0\udc80", description="M\udced\udca0\udcbd")
        assert re.fullmatch(r"[0-9a-f]{64}", digest)
        assert digest != upload_text_sha256([{"snippet": "a"}], None, title="R", description="M")
        assert re.fullmatch(r"[0-9a-f]{64}", fields_values_digest({"keywords": ["a\udc80"]}))

    def test_the_form_is_ascii_escaped(self):
        # Stored hashes depend on it: a changed form re-uploads, and bills,
        # every document at the next re-extraction.
        assert extraction_stamp._canonical({"t": "Prüf\udc80"}) == b'{"t":"Pr\\u00fcf\\udc80"}'


class TestFieldsValuesDigest:
    def test_none_is_none_and_key_order_does_not_matter(self):
        assert fields_values_digest(None) is None
        assert fields_values_digest({"a": 1, "b": ["x", "y"]}) == \
            fields_values_digest({"b": ["x", "y"], "a": 1})

    def test_a_clear_differs_from_values(self):
        assert fields_values_digest({}) != fields_values_digest({"doc_type": "invoice"})
        assert re.fullmatch(r"[0-9a-f]{64}", fields_values_digest({}))


class _Dist:
    def __init__(self, direct_url):
        self._direct_url = direct_url

    def read_text(self, name):
        assert name == "direct_url.json"
        return self._direct_url


@pytest.mark.parametrize("direct_url,commit", [
    ('{"url": "https://github.com/x/y.git", "vcs_info": {"vcs": "git", "commit_id": "%s"}}' % ("a1" * 20),
     "a1" * 20),
    ('{"url": "file:///src", "dir_info": {"editable": true}}', None),   # an editable checkout
    (None, None),                                                       # a release from PyPI
    ('{"vcs_info": {"commit_id": "main"}}', None),                      # never a branch name
    ('{"vcs_info": {"commit_id": "%s"}}' % ("A" * 40), None),
    ("not json", None),
])
def test_the_commit_comes_from_pips_direct_url(monkeypatch, direct_url, commit):
    monkeypatch.setattr(extraction_stamp.metadata, "distribution", lambda name: _Dist(direct_url))
    REAL_COMMIT.cache_clear()
    try:
        assert REAL_COMMIT() == commit
    finally:
        REAL_COMMIT.cache_clear()


def test_no_installed_library_means_no_commit(monkeypatch):
    def missing(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(extraction_stamp.metadata, "distribution", missing)
    REAL_COMMIT.cache_clear()
    try:
        assert REAL_COMMIT() is None
    finally:
        REAL_COMMIT.cache_clear()
