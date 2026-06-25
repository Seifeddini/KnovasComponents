import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from web_interface.app import (  # noqa: E402
    _apply_external_open_mode,
    _enrichment_lookup_keys,
    _lookup_enrichment_meta,
    _web_url_from_enrichment,
)


@pytest.fixture()
def enrichment():
    return {
        "corpus/Adiuvat/vertrag.pdf": {
            "doc_id": "corpus/Adiuvat/vertrag.pdf",
            "web_url": "https://contoso.sharepoint.com/vertrag.pdf",
            "title": "Vertrag",
        },
    }


def test_lookup_matches_pointer_with_prefix(monkeypatch, enrichment):
    monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "corpus")
    result = {"doc_id": "corpus/Adiuvat/vertrag.pdf", "path": "corpus/Adiuvat/vertrag.pdf"}
    meta = _lookup_enrichment_meta(enrichment, result)
    assert meta is not None
    assert _web_url_from_enrichment(meta).startswith("https://")


def test_lookup_matches_stripped_relative_path(monkeypatch, enrichment):
    monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "corpus")
    result = {"doc_id": "corpus/Adiuvat/vertrag.pdf", "path": "corpus/Adiuvat/vertrag.pdf"}
    keys = _enrichment_lookup_keys(result)
    assert "Adiuvat/vertrag.pdf" in keys
    assert _lookup_enrichment_meta(enrichment, {"doc_id": "Adiuvat/vertrag.pdf"}) is not None
    assert _lookup_enrichment_meta(enrichment, {"doc_id": "corpus/Adiuvat/vertrag.pdf"}) is not None


def test_web_url_accepts_weburl_camelcase():
    assert _web_url_from_enrichment({"webUrl": "https://example.com/doc.docx"}) == "https://example.com/doc.docx"


def test_external_open_mode_clears_local_open_flags():
    row = {"open_via_browser": True, "client_open_unc": r"\\x\y"}
    _apply_external_open_mode(row, "https://example.com/a.pdf")
    assert row["external_url"] == "https://example.com/a.pdf"
    assert row["open_mode"] == "external"
    assert "open_via_browser" not in row
    assert "client_open_unc" not in row
