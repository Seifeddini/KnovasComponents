"""Documents in OneDrive/SharePoint: search, snippets, open and preview with no local files."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from context_store import write_context_sidecar

POINTER = "tenant/Mandanten/Meierhans/Klage.pdf"
WEB_URL = "https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents/Akten/Mandanten/Meierhans/Klage.pdf"


class _Sentence:
    def __init__(self, index, char_start, page_number=None):
        self.index = index
        self.char_start = char_start
        self.page_number = page_number


def _reset_enrichment():
    import web_interface.app as wa

    wa._search_enrichment_cache = {}
    wa._search_enrichment_unique = []
    wa._search_enrichment_by_basename = {}
    wa._search_enrichment_inferred_prefixes = []
    wa._search_enrichment_mtime = 0.0


@pytest.fixture
def links(tmp_path, monkeypatch):
    """What RemoteController publishes on rc-state for a Microsoft 365 folder."""
    _reset_enrichment()
    path = tmp_path / "rc-state" / "m365" / "links.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "doc_id": POINTER, "web_url": WEB_URL, "title": "Klage.pdf",
        "modified_at": "2019-03-04T05:06:07Z", "item_id": "f-b",
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("SEARCH_ENRICHMENT_PATH", str(path))
    monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "tenant")
    yield path
    _reset_enrichment()


def test_a_microsoft_365_hit_keeps_its_snippets(links, tmp_path, monkeypatch):
    """Opening in SharePoint used to skip the indexed text: no snippet, no Fundstellen."""
    from web_interface import app as web_app

    store = tmp_path / "ctx"
    write_context_sidecar(
        str(store), POINTER, "Mandanten/Meierhans/Klage.pdf",
        "Klage gegen Meierhans. Die Forderung betraegt CHF 12'000. Frist 30 Tage.",
        [_Sentence(0, 0, 1), _Sentence(1, 23, 1), _Sentence(2, 57, 2)],
    )
    monkeypatch.setenv("SEARCH_CONTEXT_STORE_PATH", str(store))

    class _Cfg:
        def get_bool(self, key, default=False):
            return default

        def get_int(self, key, default=0):
            return 10 if key == "web.search.context_sentences" else default

        def get(self, key, default=""):
            return default

    class _Handler:
        autodoc_path = str(tmp_path / "empty-autodoc")

    results = {"results": [{"doc_id": POINTER, "path": POINTER, "sentence_number": 2}]}
    hit = web_app._enhance_search_results(results, _Handler(), _Cfg(), query="Forderung")["results"][0]
    assert hit["external_url"] == WEB_URL
    assert hit["can_open"] is True and hit["open_mode"] == "external"
    assert hit.get("first_page_preview")
    assert "Forderung" in hit["context_snippet"]["match"]
    assert "autodoc_rel_path" not in hit  # nothing on this server was looked up


def _app(tmp_path, monkeypatch, *, source):
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-m365-mode")
    monkeypatch.setenv("COMPANY_LOGIN_ENABLED", "true")
    monkeypatch.setenv("COMPANY_LOGIN_NAME", "office")
    monkeypatch.setenv("COMPANY_LOGIN_PASSWORD", "s3cret-m365")
    monkeypatch.delenv("OPEN_ALLOW_DEGRADED_DOWNLOAD_OPEN", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
web:
  secret_key: "${{WEB_SECRET_KEY}}"
  session_lifetime: 3600
  login:
    enabled: "${{COMPANY_LOGIN_ENABLED:-true}}"
    company_name: "Kanzlei"
    username: "${{COMPANY_LOGIN_NAME}}"
    password: "${{COMPANY_LOGIN_PASSWORD}}"
  search:
    results_per_page: 20
identity:
  enabled: false
api:
  base_url: "http://example.test"
documents:
  source: "{source}"
remote_controller:
  base_url: "http://remote-controller:5001"
open:
  browser_client_path: true
  companion_enabled: false
""",
        encoding="utf-8",
    )
    from web_interface import app as web_app

    client = web_app.create_app(str(config_path)).test_client()
    page = client.get("/login").get_data(as_text=True)
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', page).group(1)
    client.post("/login", data={"login_name": "office", "password": "s3cret-m365", "csrf_token": csrf})
    return client


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.content = json.dumps(body).encode()

    def json(self):
        return self._body


def _preview_url(page=None):
    url = f"/api/document/{POINTER.replace('/', '%2F')}/m365-preview?path={POINTER.replace('/', '%2F')}"
    return url + (f"&page={page}" if page else "")


def test_preview_comes_from_microsoft_via_remote_controller(links, tmp_path, monkeypatch):
    import web_interface.app as wa

    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json))
        return _Resp(200, {"status": "ok", "getUrl": "https://contoso.sharepoint.com/embed?x=1",
                           "web_url": WEB_URL})

    monkeypatch.setattr(wa.requests, "post", fake_post)
    client = _app(tmp_path, monkeypatch, source="m365")
    resp = client.get(_preview_url(page=3))
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json() == {"success": True, "embed_url": "https://contoso.sharepoint.com/embed?x=1"}
    # The identifier exactly as RemoteController published it, and the page.
    assert calls == [("http://remote-controller:5001/m365/preview", {"doc_id": POINTER, "page": 3})]


def test_preview_refuses_unknown_documents_and_unsafe_urls(links, tmp_path, monkeypatch):
    import web_interface.app as wa

    monkeypatch.setattr(wa.requests, "post", lambda *a, **k: _Resp(200, {"getUrl": "javascript:alert(1)"}))
    client = _app(tmp_path, monkeypatch, source="m365")
    assert client.get("/api/document/tenant%2Fnope.pdf/m365-preview?path=tenant%2Fnope.pdf").status_code == 404
    assert client.get(_preview_url()).status_code == 502


def test_preview_failure_is_a_502_the_dialog_can_fall_back_from(links, tmp_path, monkeypatch):
    import web_interface.app as wa

    monkeypatch.setattr(wa.requests, "post", lambda *a, **k: _Resp(503, {"error": "Graph throttled"}))
    client = _app(tmp_path, monkeypatch, source="m365")
    resp = client.get(_preview_url())
    assert resp.status_code == 502
    assert "Graph" not in resp.get_json()["error"]  # no backend detail to the browser


def test_preview_route_is_off_for_a_file_share(links, tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch, source="files")
    assert client.get(_preview_url()).status_code == 404


def test_page_flags_microsoft_365_mode_and_drops_the_dead_download(links, tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch, source="m365")
    html = client.get("/").get_data(as_text=True)
    assert "m365Mode: true" in html
    assert "allowDegradedDownloadOpen: false" in html

    share = _app(tmp_path, monkeypatch, source="files").get("/").get_data(as_text=True)
    assert "m365Mode: false" in share
    # A share with no client path still offers the download, as before.
    assert "allowDegradedDownloadOpen: true" in share


def test_external_open_goes_to_sharepoint(links, tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch, source="m365")
    resp = client.get(f"/api/document/{POINTER.replace('/', '%2F')}/external-open?path={POINTER.replace('/', '%2F')}")
    assert resp.status_code == 302 and resp.headers["Location"] == WEB_URL


def test_remote_controller_client_signs_the_preview_call():
    from remote_controller_client import PRINCIPAL_HEADER, RemoteControllerClient

    class _Broker:
        def current_user(self):
            return object()

        def assertion_for(self, user):
            return "signed-assertion"

    sent = {}

    class _Session:
        def request(self, method, url, json=None, headers=None, timeout=None):
            sent.update(method=method, url=url, json=json, headers=headers, timeout=timeout)
            return _Resp(200, {"status": "ok", "getUrl": "https://x/embed"})

    rc = RemoteControllerClient("http://rc:5001", principal_broker=_Broker(), session=_Session())
    assert rc.m365_preview(POINTER, 2)["getUrl"] == "https://x/embed"
    assert sent["method"] == "POST" and sent["url"] == "http://rc:5001/m365/preview"
    assert sent["json"] == {"doc_id": POINTER, "page": 2}
    assert sent["headers"][PRINCIPAL_HEADER] == "signed-assertion"
    assert sent["timeout"] <= 10


def test_front_end_uses_microsofts_viewer_for_microsoft_365_hits():
    """No JS test runner here; pin the wiring the dialog depends on."""
    js = (Path(__file__).resolve().parents[1] / "src/web_interface/static/js/app.js").read_text(encoding="utf-8")
    assert "this.m365Mode && doc.external_url" in js
    assert "/m365-preview" in js
    assert "In ${label} \u00f6ffnen" in js
    assert "_externalSourceLabel(" in js


def test_cortex_quotes_come_from_the_indexed_text_when_no_file_is_here(tmp_path):
    from context_store import indexed_pages, load_context
    from ontology_text import DocumentTextResolver

    write_context_sidecar(
        str(tmp_path), POINTER, "Mandanten/Meierhans/Klage.pdf",
        "Klage gegen Meierhans wegen Mietzins. Die Forderung betraegt CHF 12'000 netto. "
        "Die Frist betraegt dreissig Tage ab Zustellung.",
        [_Sentence(0, 0, 1), _Sentence(1, 38, 1), _Sentence(2, 80, 2)],
    )
    pages = indexed_pages(load_context(str(tmp_path), [POINTER]))
    assert sorted(pages) == [1, 2] and "Forderung" in pages[1]

    resolver = DocumentTextResolver(
        resolve_path=lambda p: None,
        indexed_pages=lambda p: indexed_pages(load_context(str(tmp_path), [p])),
    )
    assert resolver.page_count(POINTER) == 2
    assert "Frist" in resolver.quote_on_page(POINTER, 2)
    assert resolver.find_mention(POINTER, "Forderung")[0] == 1
    # Without a fallback nothing is invented, exactly as before.
    assert DocumentTextResolver(resolve_path=lambda p: None).page_count(POINTER) == 0


def test_indexed_pages_without_page_numbers_is_one_page():
    from context_store import indexed_pages

    entry = {"sentences": [{"i": 1, "t": "Erster Satz."}, {"i": 2, "t": "Zweiter Satz."}]}
    assert indexed_pages(entry) == {1: "Erster Satz. Zweiter Satz."}
    assert indexed_pages(None) == {}
