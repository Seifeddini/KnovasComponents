"""Vertrag- und Auth-Tests fuer /api/ontology/* und /ontology."""
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
WEB_SRC = SRC / "web_interface"
for p in (SRC, WEB_SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


class DummyKnovasClient:
    def __init__(self, config):
        self.config = config

    def health_check(self):
        return True

    def search_documents(self, query, limit=20, filters=None):
        return {"results": [], "total": 0}


class TmpAutodocHandler:
    def __init__(self, root):
        self.autodoc_path = str(root)


FIXTURE = {
    "types": [
        {"id": "mandant", "label": "Mandant", "count": 12},
        {"id": "dossier", "label": "Dossier", "count": 47},
    ],
    "relations": [
        {"src": "mandant", "predicate": "hat_Dossier", "dst": "dossier", "count": 47},
    ],
    "entities": [
        {"id": "e-001", "label": "Müller Bau AG", "type": "mandant", "doc_count": 8},
    ],
    "entity_relations": [],
    "evidence": [
        {"entity_id": "e-001",
         "document": {"path": "sub/vertrag.pdf", "title": "Vertrag"},
         "page": 2, "quote": "…Müller Bau AG…"},
        {"entity_id": "e-001",
         "document": {"path": "sub/fehlt.pdf", "title": "Weg"},
         "page": 1, "quote": "wird gefiltert"},
    ],
}


def _build_app(tmp_path, monkeypatch, autodoc_root):
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-ontology")
    monkeypatch.setenv("COMPANY_LOGIN_ENABLED", "true")
    monkeypatch.setenv("COMPANY_DISPLAY_NAME", "Test Company")
    monkeypatch.setenv("COMPANY_LOGIN_NAME", "office")
    monkeypatch.setenv("COMPANY_LOGIN_PASSWORD", "s3cret")
    monkeypatch.delenv("AUTODOC_IDENTIFIER_PREFIX", raising=False)

    fixture_path = tmp_path / "ontology_fixture.json"
    fixture_path.write_text(json.dumps(FIXTURE), encoding="utf-8")
    monkeypatch.setenv("ONTOLOGY_FIXTURE_PATH", str(fixture_path))
    monkeypatch.setenv("ONTOLOGY_FILTER_STATE_PATH", str(tmp_path / "filter_state.json"))
    import ontology_store
    ontology_store._cache = None  # Test-Isolation
    import ontology_filters
    ontology_filters._engine_cache = None

    ad_str = str(autodoc_root).replace("\\", "/")
    config_path = tmp_path / "config.yaml"
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
api:
  base_url: "http://example.test"
open:
  companion_enabled: false
  local_root: "{ad_str}"
""",
        encoding="utf-8",
    )

    import web_interface.app as web_app
    monkeypatch.setattr(web_app, "KnovasAPIClient", DummyKnovasClient)
    monkeypatch.setattr(web_app, "AutoDocFileHandler", lambda: TmpAutodocHandler(autodoc_root))
    flask_app = web_app.create_app(str(config_path))
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture()
def app(tmp_path, monkeypatch):
    ad = tmp_path / "autodoc"
    (ad / "sub").mkdir(parents=True)
    (ad / "sub" / "vertrag.pdf").write_bytes(b"%PDF-1.4 minimal")
    return _build_app(tmp_path, monkeypatch, ad)


def _login(client):
    client.get("/login")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    client.post("/login", data={"login_name": "office", "password": "s3cret",
                                "csrf_token": token})


def test_ontology_api_requires_login(app):
    client = app.test_client()
    assert client.get("/api/ontology/summary").status_code == 401
    assert client.get("/api/ontology/entities?type=mandant").status_code == 401
    assert client.get("/api/ontology/entities/e-001").status_code == 401


def test_ontology_page_redirects_to_login(app):
    client = app.test_client()
    resp = client.get("/ontology")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_summary_contract(app):
    client = app.test_client()
    _login(client)
    data = client.get("/api/ontology/summary").get_json()
    assert data["success"] is True
    assert [t["id"] for t in data["types"]] == ["mandant", "dossier"]
    assert data["relations"][0]["count"] == 47


def test_entities_contract(app):
    client = app.test_client()
    _login(client)
    data = client.get("/api/ontology/entities?type=mandant").get_json()
    assert data["success"] is True
    assert data["entities"][0]["label"] == "Müller Bau AG"
    assert client.get("/api/ontology/entities?type=x").get_json()["entities"] == []


def test_entity_detail_contract_filters_missing_files(app):
    client = app.test_client()
    _login(client)
    data = client.get("/api/ontology/entities/e-001").get_json()
    assert data["success"] is True
    # sub/fehlt.pdf existiert nicht im tmp-Autodoc-Root -> gefiltert
    assert [ev["document"]["path"] for ev in data["evidence"]] == ["sub/vertrag.pdf"]
    assert data["evidence"][0]["page"] == 2
    assert client.get("/api/ontology/entities/e-404").status_code == 404


def test_ontology_page_renders_after_login(app):
    client = app.test_client()
    _login(client)
    resp = client.get("/ontology")
    assert resp.status_code == 200
    assert "Cortex".encode("utf-8") in resp.data


def _csrf_header(client):
    with client.session_transaction() as sess:
        return {"X-CSRF-Token": sess["csrf_token"]}


def test_filter_api_requires_login(app):
    client = app.test_client()
    assert client.post("/api/ontology/filters", json={}).status_code == 401
    assert client.get("/api/ontology/filters/f-x").status_code == 401


def test_filter_create_requires_csrf_header(app):
    client = app.test_client()
    _login(client)
    resp = client.post("/api/ontology/filters",
                       json={"entity_id": "e-001", "label": "Fristen"})
    assert resp.status_code == 403


def test_filter_create_and_detail_contract(app):
    client = app.test_client()
    _login(client)
    resp = client.post("/api/ontology/filters",
                       json={"entity_id": "e-001", "label": "Fristen"},
                       headers=_csrf_header(client))
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["success"] is True
    assert data["filter"]["label"] == "Fristen"
    # vertrag.pdf ist kein echtes PDF -> keine Segmente -> ehrlicher Sammel-Zustand
    assert data["filter"]["status"] == "collecting"
    assert data["proposals"] == []

    detail = client.get(f"/api/ontology/filters/{data['filter']['id']}").get_json()
    assert detail["success"] is True
    assert detail["filter"]["id"] == data["filter"]["id"]

    # Entity-Detail traegt den Filter jetzt mit
    entity = client.get("/api/ontology/entities/e-001").get_json()
    assert [f["label"] for f in entity["filters"]] == ["Fristen"]


def test_filter_create_validates_input(app):
    client = app.test_client()
    _login(client)
    headers = _csrf_header(client)
    assert client.post("/api/ontology/filters", json={"entity_id": "e-404",
                       "label": "x"}, headers=headers).status_code == 404
    assert client.post("/api/ontology/filters", json={"entity_id": "e-001",
                       "label": ""}, headers=headers).status_code == 400


def test_filter_decision_validates(app):
    client = app.test_client()
    _login(client)
    headers = _csrf_header(client)
    created = client.post("/api/ontology/filters",
                          json={"entity_id": "e-001", "label": "Fristen"},
                          headers=headers).get_json()
    fid = created["filter"]["id"]
    assert client.post(f"/api/ontology/filters/{fid}/decision",
                       json={"proposal_id": "x", "action": "vielleicht"},
                       headers=headers).status_code == 400
    assert client.post(f"/api/ontology/filters/{fid}/decision",
                       json={"proposal_id": "x", "action": "reject"},
                       headers=headers).status_code == 404
    assert client.post("/api/ontology/filters/f-unbekannt/decision",
                       json={"proposal_id": "x", "action": "reject"},
                       headers=headers).status_code == 404
