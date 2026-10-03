"""``pytest -k doc_fields_render``: every document-fields surface, rendered.

The compose ``mock`` demo runs in legacy mode and shows no document-field UI,
so a manual click-through cannot cover these states. This test does it
instead: the real app, its real Knovas client and the mock Knovas API (over
an in-process ``WsgiSession``) in each server state of spec 2.1 -- ``off``,
``values``, ``filters`` without a relevance calibration (``listing_only``)
and ``filters``. Per state it fetches the search page, the registry view, a
search, the field panel, a listing, and the admin pages (Dokumentfelder,
Dokumente, System), checks that each gated part shows exactly where its
capability holds (H6, D11), and writes every answer to a temporary directory
for review (the path is printed with ``-s``).

Placeholder names only. Skipped when the mock or PostgreSQL is missing.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("flask")

from conftest import platform_db_reachable  # noqa: E402
from test_doc_fields_mock_contract import _app_on_mock  # noqa: E402
from test_web_search_doc_fields import signed_in  # noqa: E402

pytestmark = pytest.mark.skipif(not platform_db_reachable(),
                                reason="identity routes need a real PostgreSQL")

STATES = [
    ("off", "off", {}),
    ("values", "values", {}),
    ("listing_only", "filters", {"calibrated": False}),
    ("filters", "filters", {}),
]
SYSTEM_LABELS = {
    "off": "aus",
    "values": "Werte (ohne Filter)",
    "listing_only": "Werte + Liste (Feldfilter bei Knovas vor\u00fcbergehend nicht verf\u00fcgbar)",
    "filters": "Werte + Filter",
}


@pytest.fixture(scope="module")
def render_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("doc_fields_render")
    print(f"\ndoc_fields_render output: {path}")
    return path


def _save(directory, name, response):
    data = response.get_data(as_text=True)
    if response.is_json:
        data = json.dumps(response.get_json(), indent=2, ensure_ascii=False)
        name += ".json"
    else:
        name += ".html"
    (directory / name).write_text(data, encoding="utf-8")
    return data


@pytest.mark.parametrize("state, mode, kw", STATES, ids=[s[0] for s in STATES])
def test_doc_fields_render(platform_db, tmp_path, monkeypatch, identity_repo, render_dir,
                           state, mode, kw):
    app, mock = _app_on_mock(platform_db, tmp_path, monkeypatch, mode, **kw)
    client = signed_in(app, identity_repo, role="admin")
    out = render_dir / state
    out.mkdir()
    listing = state in ("listing_only", "filters")
    values = state != "off"

    page = client.get("/")
    assert page.status_code == 200
    html = _save(out, "search_page", page)
    # The rail ships hidden; doc_fields.js shows it from /api/doc-fields.
    assert 'id="docFieldsRail" hidden' in html and "js/doc_fields.js" in html

    # The probe cannot tell listing_only from filters (spec 1): the first
    # filtered search that Knovas refuses for the calibration teaches it.
    probed = "filters" if state == "listing_only" else state
    registry = client.get("/api/doc-fields")
    _save(out, "api_doc_fields", registry)
    assert registry.get_json()["capability"] == probed
    assert bool(registry.get_json()["fields"]) is values

    filtered = client.post("/api/search", json={"query": "contract",
                                                "where": {"doc_type": "contract"}})
    _save(out, "search_filtered", filtered)
    if state == "filters":
        assert filtered.status_code == 200
        assert filtered.get_json()["document_fields"]["filter_state"] == "applied"
    else:
        assert filtered.status_code == 409, "no filter is ever shown as applied (H1, H2)"
        assert "results" not in filtered.get_json()
    assert client.get("/api/doc-fields").get_json()["capability"] == state

    found = client.post("/api/search", json={"query": "lease"})
    _save(out, "search", found)
    body = found.get_json()
    assert found.status_code == 200 and body["results"]
    assert body["document_fields"]["capability"] == state
    cards = [r.get("fields_display") for r in body["results"]]
    assert any(cards) is listing, "typed values on cards only with the listing (D11)"

    panel = client.post("/api/document-fields/read", json={"doc_id": "demo-001"})
    _save(out, "field_panel", panel)
    if values:
        assert panel.status_code == 200
        rows = {r["key"]: r for r in panel.get_json()["fields"]}
        assert rows["doc_type"]["text"] == "Vertrag"
    else:
        assert panel.status_code == 409
        assert panel.get_json()["error_code"] == "doc_fields_off"

    listed = client.post("/api/documents/find", json={"where": {"doc_type": "contract"}})
    _save(out, "listing", listed)
    if listing:
        assert listed.status_code == 200
        assert {d["doc_id"] for d in listed.get_json()["documents"]} == {"demo-001", "demo-002"}
    else:
        assert listed.status_code == 409

    admin_fields = client.get("/admin/doc-fields")
    admin_html = _save(out, "admin_doc_fields", admin_fields)
    assert admin_fields.status_code == 200
    if values:
        assert "Dokumentart" in admin_html and "Ordnervorgaben" in admin_html
    else:
        assert "nicht freigeschaltet" in admin_html

    documents = client.get("/admin/documents")
    documents_html = _save(out, "admin_documents", documents)
    assert documents.status_code == 200
    assert ('href="/admin/doc-fields"' in documents_html) is values, "the tab (H6)"
    assert ("Feldfilter" in documents_html) is listing

    system = client.get("/admin/system")
    system_html = _save(out, "admin_system", system)
    assert system.status_code == 200
    assert SYSTEM_LABELS[state] in system_html

    # Nothing of the feature reaches Knovas in a URL (D6, D10).
    for sent in mock.requests:
        assert not {"pointer", "q"} & set(sent["query"]), (sent["method"], sent["path"])
