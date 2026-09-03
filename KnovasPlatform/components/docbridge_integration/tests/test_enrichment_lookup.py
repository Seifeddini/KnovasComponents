import re
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from web_interface.app import (  # noqa: E402
    _apply_external_open_mode,
    _enrichment_lookup_keys,
    _load_search_enrichment,
    _lookup_enrichment_meta,
    _resolve_onedrive_url,
    _web_url_from_enrichment,
)


@pytest.fixture()
def enrichment():
    rec = {
        "doc_id": "corpus/acme/vertrag.pdf",
        "web_url": "https://contoso.sharepoint.com/vertrag.pdf",
        "title": "Vertrag",
    }
    return {
        "corpus/acme/vertrag.pdf": rec,
    }


def test_lookup_matches_pointer_with_prefix(monkeypatch, enrichment):
    monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "corpus")
    result = {"doc_id": "corpus/acme/vertrag.pdf", "path": "corpus/acme/vertrag.pdf"}
    meta = _lookup_enrichment_meta(enrichment, result)
    assert meta is not None
    assert _web_url_from_enrichment(meta).startswith("https://")


def test_lookup_matches_stripped_relative_path(monkeypatch, enrichment):
    monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "corpus")
    result = {"doc_id": "corpus/acme/vertrag.pdf", "path": "corpus/acme/vertrag.pdf"}
    keys = _enrichment_lookup_keys(result)
    assert "acme/vertrag.pdf" in keys
    assert _lookup_enrichment_meta(enrichment, {"doc_id": "acme/vertrag.pdf"}) is not None
    assert _lookup_enrichment_meta(enrichment, {"doc_id": "corpus/acme/vertrag.pdf"}) is not None


def test_web_url_accepts_weburl_camelcase():
    assert _web_url_from_enrichment({"webUrl": "https://example.com/doc.docx"}) == "https://example.com/doc.docx"


def test_external_open_mode_clears_local_open_flags():
    row = {"open_via_browser": True, "client_open_unc": r"\\x\y"}
    _apply_external_open_mode(row, "https://example.com/a.pdf")
    assert row["external_url"] == "https://example.com/a.pdf"
    assert row["open_mode"] == "external"
    assert "open_via_browser" not in row
    assert "client_open_unc" not in row


def test_load_enrichment_registers_alias_keys(monkeypatch, tmp_path):
    import web_interface.app as wa

    wa._search_enrichment_cache = {}
    wa._search_enrichment_unique = []
    wa._search_enrichment_by_basename = {}
    wa._search_enrichment_inferred_prefixes = []
    wa._search_enrichment_mtime = 0.0
    monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "corpus")
    enrichment_file = tmp_path / ".search_enrichment.jsonl"
    enrichment_file.write_text(
        '{"doc_id": "corpus/acme/vertrag.pdf", "web_url": "https://contoso.sharepoint.com/vertrag.pdf"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SEARCH_ENRICHMENT_PATH", str(enrichment_file))
    loaded = _load_search_enrichment()
    assert "corpus/acme/vertrag.pdf" in loaded
    assert "acme/vertrag.pdf" in loaded
    assert _resolve_onedrive_url("acme/vertrag.pdf", "acme/vertrag.pdf") == (
        "https://contoso.sharepoint.com/vertrag.pdf"
    )


def test_prefix_inferred_and_case_insensitive(monkeypatch, tmp_path):
    import web_interface.app as wa

    wa._search_enrichment_cache = {}
    wa._search_enrichment_unique = []
    wa._search_enrichment_by_basename = {}
    wa._search_enrichment_inferred_prefixes = []
    wa._search_enrichment_mtime = 0.0
    enrichment_file = tmp_path / ".search_enrichment.jsonl"
    enrichment_file.write_text(
        '{"doc_id": "tenant/1338 - Cases/Annual report 2016 - Example AG.pdf", '
        '"web_url": "https://contoso.sharepoint.com/report.pdf"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SEARCH_ENRICHMENT_PATH", str(enrichment_file))
    _load_search_enrichment()
    url = _resolve_onedrive_url(
        "tenant/1338 - Cases/Annual report 2016 - Example AG.pdf",
        "tenant/1338 - Cases/Annual report 2016 - Example AG.pdf",
    )
    assert url == "https://contoso.sharepoint.com/report.pdf"
    url2 = _resolve_onedrive_url(
        "Annual report 2016 - Example AG.pdf",
        "Annual report 2016 - Example AG.pdf",
    )
    assert url2 == "https://contoso.sharepoint.com/report.pdf"


def test_enrichment_path_falls_back_to_autodoc_mount(monkeypatch, tmp_path):
    import web_interface.app as wa

    missing = tmp_path / "missing.jsonl"
    good = tmp_path / "autodoc.jsonl"
    good.write_text(
        '{"doc_id": "tenant/a.pdf", "web_url": "https://example.com/a.pdf"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SEARCH_ENRICHMENT_PATH", str(missing))
    monkeypatch.setattr(wa, "_DEFAULT_ENRICHMENT_PATH", str(good))
    assert wa._enrichment_path_from_config() == str(good)


def test_external_open_redirect(tmp_path, monkeypatch):
    import web_interface.app as wa

    wa._search_enrichment_cache = {}
    wa._search_enrichment_unique = []
    wa._search_enrichment_by_basename = {}
    wa._search_enrichment_inferred_prefixes = []
    wa._search_enrichment_mtime = 0.0
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-enrichment-open")
    monkeypatch.setenv("COMPANY_LOGIN_ENABLED", "true")
    monkeypatch.setenv("COMPANY_DISPLAY_NAME", "Test Company")
    monkeypatch.setenv("COMPANY_LOGIN_NAME", "office")
    monkeypatch.setenv("COMPANY_LOGIN_PASSWORD", "s3cret")
    monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "corpus")

    enrichment_file = tmp_path / ".search_enrichment.jsonl"
    enrichment_file.write_text(
        '{"doc_id": "corpus/sub/hello.pdf", "web_url": "https://contoso.sharepoint.com/hello.pdf"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SEARCH_ENRICHMENT_PATH", str(enrichment_file))

    ad = tmp_path / "autodoc"
    ad.mkdir()
    config_path = tmp_path / "config.yaml"
    ad_str = str(ad).replace("\\", "/")
    config_path.write_text(
        f"""
web:
  secret_key: "${{WEB_SECRET_KEY}}"
  session_lifetime: 3600
  login:
    enabled: "${{COMPANY_LOGIN_ENABLED:-true}}"
    company_name: "${{COMPANY_DISPLAY_NAME:-Knovas}}"
    username: "${{COMPANY_LOGIN_NAME}}"
    password: "${{COMPANY_LOGIN_PASSWORD}}"
  search:
    results_per_page: 20
identity:
  enabled: false
api:
  base_url: "http://example.test"
open:
  browser_client_path: false
  companion_enabled: false
  local_root: "{ad_str}"
""",
        encoding="utf-8",
    )

    from web_interface import app as web_app

    flask_app = web_app.create_app(str(config_path))
    client = flask_app.test_client()
    login_page = client.get("/login")
    csrf = re.search(
        r'name="csrf_token"\s+value="([^"]+)"',
        login_page.get_data(as_text=True),
    )
    assert csrf
    resp_login = client.post(
        "/login",
        data={
            "login_name": "office",
            "password": "s3cret",
            "csrf_token": csrf.group(1),
        },
        follow_redirects=True,
    )
    assert resp_login.status_code == 200

    resp = client.get(
        "/api/document/corpus%2Fsub%2Fhello.pdf/external-open?path=corpus%2Fsub%2Fhello.pdf"
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "https://contoso.sharepoint.com/hello.pdf"
