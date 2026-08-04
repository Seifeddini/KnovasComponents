# Wissensnetz (Ontology Explorer) MVP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Neuer Tab „Wissensnetz" in der bestehenden docbridge-Web-UI: Typ-Graph → Entitäten → Belege → PDF auf der belegten Seite, Daten aus einer Mock-Fixture hinter dem stabilen `/api/ontology`-Vertrag.

**Architecture:** Alles in `KnovasPlatform/components/docbridge_integration`. Flask-freier `ontology_store` lädt/validiert eine JSON-Fixture; drei neue GET-Routen liefern den Datenvertrag; ein neues Jinja-Template mit vendored Cytoscape rendert drei Spalten. Dokumentanzeige über die existierende Preview-Kette (`/api/document/<id>/preview?path=…#page=N`).

**Tech Stack:** Flask (bestehend), Cytoscape.js 3.30.4 als vendored UMD (kein Build-Schritt), Vanilla ES6, pytest.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-04-wissensnetz-ontology-mvp-design.md` — bei Widerspruch gewinnt die Spec.
- **CI zwingend:** ausschliesslich vorhandene CSS-Tokens aus `style.css` (`--primary-color`, `--accent`, `--surface-sunken`, `--highlight`, `--text-primary`, `--text-secondary`, `--border-color`, `--radius`…). **Keine neuen Hexwerte.** Farbwerte in JS zur Laufzeit via `getComputedStyle` aus den Tokens lesen.
- **Deutsch (Schweiz):** alle UI-Texte deutsch, Zahlen im Format `1'847` (ASCII-Apostroph, nicht `Intl` — de-CH liefert U+2019).
- **UI-Label** ist „Wissensnetz"; **Code/Routen** heissen `ontology`.
- **Kein Build-Schritt, keine neuen Python-Dependencies.** Einzige neue Frontend-Datei von aussen: vendored `cytoscape.min.js`.
- **Auth:** alle neuen Routen laufen automatisch hinter `require_company_login` (before_request-Hook) — die neuen Endpoint-Namen dürfen NICHT in die Exempt-Sets in `app.py:861` / `app.py:884` aufgenommen werden. Alle neuen API-Routen sind GET-only (kein CSRF-Thema).
- **Kein Modal.** Panes degradieren einzeln; Empty-States sind formulierte Befunde, keine leeren Flächen.
- **Arbeitsverzeichnis aller Tasks:** `KnovasPlatform/components/docbridge_integration/` (Pfade unten relativ dazu, ausser explizit anders). Tests laufen mit `.venv/bin/python -m pytest` von dort.
- **Branch:** `feat/wissensnetz-ontology` (existiert bereits, Spec-Commit liegt drauf).

---

### Task 1: Ontology-Store (Fixture laden, validieren, filtern)

**Files:**
- Create: `src/ontology_store.py`
- Test: `tests/test_ontology_store.py`

**Interfaces:**
- Consumes: nichts (Flask-frei, Muster wie `src/web_interface/preview.py`)
- Produces:
  - `load_ontology(path: str, path_exists: Optional[Callable[[str], bool]] = None) -> OntologyStore`
  - `get_ontology(path_exists=None) -> OntologyStore` — liest Pfad aus Env `ONTOLOGY_FIXTURE_PATH`, cached per (path, mtime); fehlende/kaputte Datei ⇒ leerer Store + Warnung, nie Exception
  - `OntologyStore.summary() -> Dict` · `.entities_for_type(type_id: str) -> Dict` · `.entity_detail(entity_id: str) -> Optional[Dict]` · `.warnings: List[str]`

**Fixture-Format (eine JSON-Datei, flache Listen — nah am künftigen Backend):**

```jsonc
{
  "types":            [{ "id": "mandant", "label": "Mandant", "count": 12 }],
  "relations":        [{ "src": "mandant", "predicate": "hat_Dossier", "dst": "dossier", "count": 47 }],
  "entities":         [{ "id": "e-001", "label": "Müller Bau AG", "type": "mandant", "doc_count": 8 }],
  "entity_relations": [{ "src": "e-001", "predicate": "hat_Dossier", "dst": "e-014" }],
  "evidence":         [{ "entity_id": "e-001",
                         "document": { "path": "corpus/2024-001/Mustervertrag.pdf", "title": "Mustervertrag" },
                         "page": 3, "quote": "…zwischen der Müller Bau AG…" }]
}
```

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/test_ontology_store.py
"""Tests fuer den Wissensnetz-Ontology-Store (Fixture laden/validieren)."""
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ontology_store import get_ontology, load_ontology  # noqa: E402


def _fixture(tmp_path, data):
    p = tmp_path / "ontology_fixture.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


VALID = {
    "types": [
        {"id": "mandant", "label": "Mandant", "count": 12},
        {"id": "dossier", "label": "Dossier", "count": 47},
    ],
    "relations": [
        {"src": "mandant", "predicate": "hat_Dossier", "dst": "dossier", "count": 47},
    ],
    "entities": [
        {"id": "e-001", "label": "Müller Bau AG", "type": "mandant", "doc_count": 8},
        {"id": "e-014", "label": "Dossier 2024-001", "type": "dossier", "doc_count": 8},
    ],
    "entity_relations": [
        {"src": "e-001", "predicate": "hat_Dossier", "dst": "e-014"},
    ],
    "evidence": [
        {
            "entity_id": "e-001",
            "document": {"path": "corpus/2024-001/Mustervertrag.pdf", "title": "Mustervertrag"},
            "page": 3,
            "quote": "…zwischen der Müller Bau AG…",
        },
    ],
}


def test_summary_shape(tmp_path):
    store = load_ontology(_fixture(tmp_path, VALID))
    s = store.summary()
    assert [t["id"] for t in s["types"]] == ["mandant", "dossier"]
    assert s["relations"][0]["predicate"] == "hat_Dossier"
    assert s["relations"][0]["count"] == 47


def test_entities_for_type_filters(tmp_path):
    store = load_ontology(_fixture(tmp_path, VALID))
    ents = store.entities_for_type("mandant")["entities"]
    assert [e["id"] for e in ents] == ["e-001"]
    assert store.entities_for_type("unbekannt")["entities"] == []


def test_entity_detail_joins_relations_and_evidence(tmp_path):
    store = load_ontology(_fixture(tmp_path, VALID))
    d = store.entity_detail("e-001")
    assert d["entity"]["label"] == "Müller Bau AG"
    assert d["relations"][0]["predicate"] == "hat_Dossier"
    assert d["relations"][0]["target"]["id"] == "e-014"
    assert d["evidence"][0]["page"] == 3
    assert store.entity_detail("gibt-es-nicht") is None


def test_broken_references_filtered_not_500(tmp_path):
    data = json.loads(json.dumps(VALID))
    data["relations"].append({"src": "mandant", "predicate": "kaputt", "dst": "fehlt", "count": 1})
    data["entities"].append({"id": "e-099", "label": "Waise", "type": "fehlt", "doc_count": 1})
    data["entity_relations"].append({"src": "e-001", "predicate": "kaputt", "dst": "e-999"})
    store = load_ontology(_fixture(tmp_path, data))
    assert [r["predicate"] for r in store.summary()["relations"]] == ["hat_Dossier"]
    assert store.entities_for_type("fehlt")["entities"] == []
    assert [r["predicate"] for r in store.entity_detail("e-001")["relations"]] == ["hat_Dossier"]
    assert any("kaputt" in w or "fehlt" in w or "e-999" in w for w in store.warnings)


def test_evidence_with_missing_file_filtered(tmp_path):
    store = load_ontology(_fixture(tmp_path, VALID), path_exists=lambda p: False)
    assert store.entity_detail("e-001")["evidence"] == []
    assert store.warnings  # Warnung statt Fehler


def test_missing_or_invalid_file_yields_empty_store(tmp_path):
    store = load_ontology(str(tmp_path / "fehlt.json"))
    assert store.summary() == {"types": [], "relations": []}
    bad = tmp_path / "kaputt.json"
    bad.write_text("{nicht json", encoding="utf-8")
    assert load_ontology(str(bad)).summary()["types"] == []


def test_get_ontology_uses_env_and_mtime_cache(tmp_path, monkeypatch):
    path = _fixture(tmp_path, VALID)
    monkeypatch.setenv("ONTOLOGY_FIXTURE_PATH", path)
    first = get_ontology()
    assert first is get_ontology()  # gleiche mtime ⇒ gecached
    data = json.loads(json.dumps(VALID))
    data["types"].append({"id": "vertrag", "label": "Vertrag", "count": 5})
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    import os
    os.utime(path, (0, Path(path).stat().st_mtime + 10))
    assert len(get_ontology().summary()["types"]) == 3
```

- [ ] **Step 2: Tests laufen lassen — müssen fehlschlagen**

Run: `.venv/bin/python -m pytest tests/test_ontology_store.py -v`
Expected: FAIL / ERROR mit `ModuleNotFoundError: No module named 'ontology_store'`

- [ ] **Step 3: Store implementieren**

```python
# src/ontology_store.py
"""Wissensnetz: Fixture-JSON laden und validieren.

Traegt bewusst kein Flask-Wissen (Muster: web_interface/preview.py).
Der Store ist der spaetere Andockpunkt fuer den echten Knovas-Endpunkt:
Vertrag bleibt, nur die Datenquelle wird getauscht.

Validierungsposition: kaputte Referenzen werden gefiltert und geloggt,
niemals eskaliert -- die Seite zeigt dann weniger, aber nie einen 500.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ENV_FIXTURE_PATH = "ONTOLOGY_FIXTURE_PATH"

_EMPTY: Dict[str, Any] = {
    "types": [], "relations": [], "entities": [],
    "entity_relations": [], "evidence": [],
}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class OntologyStore:
    def __init__(self, data: Dict[str, Any], warnings: List[str]):
        self._types: List[Dict[str, Any]] = data["types"]
        self._relations: List[Dict[str, Any]] = data["relations"]
        self._entities: List[Dict[str, Any]] = data["entities"]
        self._entity_relations: List[Dict[str, Any]] = data["entity_relations"]
        self._evidence: List[Dict[str, Any]] = data["evidence"]
        self._entity_by_id = {e["id"]: e for e in self._entities}
        self.warnings = warnings

    def summary(self) -> Dict[str, Any]:
        return {
            "types": [dict(t) for t in self._types],
            "relations": [dict(r) for r in self._relations],
        }

    def entities_for_type(self, type_id: str) -> Dict[str, Any]:
        return {"entities": [dict(e) for e in self._entities if e["type"] == type_id]}

    def entity_detail(self, entity_id: str) -> Optional[Dict[str, Any]]:
        entity = self._entity_by_id.get(entity_id)
        if entity is None:
            return None
        relations = [
            {"predicate": r["predicate"], "target": dict(self._entity_by_id[r["dst"]])}
            for r in self._entity_relations
            if r["src"] == entity_id
        ]
        evidence = [
            {"document": dict(ev["document"]), "page": ev["page"], "quote": ev["quote"]}
            for ev in self._evidence
            if ev["entity_id"] == entity_id
        ]
        return {"entity": dict(entity), "relations": relations, "evidence": evidence}


def _validate(raw: Any, path_exists: Optional[Callable[[str], bool]]) -> Tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []
    if not isinstance(raw, dict):
        return dict(_EMPTY), ["Fixture ist kein JSON-Objekt"]

    types = []
    seen_type_ids = set()
    for t in raw.get("types") or []:
        tid = str(t.get("id") or "").strip()
        label = str(t.get("label") or "").strip()
        if not tid or not label or tid in seen_type_ids:
            warnings.append(f"Typ verworfen: {t!r}")
            continue
        seen_type_ids.add(tid)
        types.append({"id": tid, "label": label, "count": _as_int(t.get("count"))})

    relations = []
    for r in raw.get("relations") or []:
        src, dst = str(r.get("src") or ""), str(r.get("dst") or "")
        pred = str(r.get("predicate") or "").strip()
        if not pred or src not in seen_type_ids or dst not in seen_type_ids:
            warnings.append(f"Typ-Relation verworfen (unbekannter Typ): {r!r}")
            continue
        relations.append({"src": src, "predicate": pred, "dst": dst, "count": _as_int(r.get("count"))})

    entities = []
    seen_entity_ids = set()
    for e in raw.get("entities") or []:
        eid = str(e.get("id") or "").strip()
        label = str(e.get("label") or "").strip()
        etype = str(e.get("type") or "")
        if not eid or not label or eid in seen_entity_ids or etype not in seen_type_ids:
            warnings.append(f"Entität verworfen: {e!r}")
            continue
        seen_entity_ids.add(eid)
        entities.append({"id": eid, "label": label, "type": etype,
                         "doc_count": _as_int(e.get("doc_count"))})

    entity_relations = []
    for r in raw.get("entity_relations") or []:
        src, dst = str(r.get("src") or ""), str(r.get("dst") or "")
        pred = str(r.get("predicate") or "").strip()
        if not pred or src not in seen_entity_ids or dst not in seen_entity_ids:
            warnings.append(f"Entitäts-Relation verworfen: {r!r}")
            continue
        entity_relations.append({"src": src, "predicate": pred, "dst": dst})

    evidence = []
    for ev in raw.get("evidence") or []:
        doc = ev.get("document") or {}
        doc_path = str(doc.get("path") or "").strip()
        eid = str(ev.get("entity_id") or "")
        page = _as_int(ev.get("page"), default=0)
        if not doc_path or eid not in seen_entity_ids or page < 1:
            warnings.append(f"Beleg verworfen (Pflichtfeld fehlt): {ev!r}")
            continue
        if path_exists is not None and not path_exists(doc_path):
            warnings.append(f"Beleg verworfen (Datei nicht gefunden): {doc_path}")
            continue
        evidence.append({
            "entity_id": eid,
            "document": {"path": doc_path, "title": str(doc.get("title") or doc_path)},
            "page": page,
            "quote": str(ev.get("quote") or ""),
        })

    return (
        {"types": types, "relations": relations, "entities": entities,
         "entity_relations": entity_relations, "evidence": evidence},
        warnings,
    )


def load_ontology(path: str,
                  path_exists: Optional[Callable[[str], bool]] = None) -> OntologyStore:
    """Fixture laden; fehlende/kaputte Datei ⇒ leerer Store + Warnung, nie Exception."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning("Ontology-Fixture nicht ladbar (%s): %s", path, exc)
        return OntologyStore(dict(_EMPTY), [f"Fixture nicht ladbar: {exc}"])
    data, warnings = _validate(raw, path_exists)
    for w in warnings:
        logger.warning("Ontology-Fixture: %s", w)
    return OntologyStore(data, warnings)


_cache: Optional[Tuple[str, float, OntologyStore]] = None


def get_ontology(path_exists: Optional[Callable[[str], bool]] = None) -> OntologyStore:
    """Env-konfigurierter Store, gecached per (Pfad, mtime)."""
    global _cache
    path = (os.environ.get(ENV_FIXTURE_PATH) or "").strip()
    if not path:
        return OntologyStore(dict(_EMPTY), [f"{ENV_FIXTURE_PATH} nicht gesetzt"])
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        mtime = -1.0
    if _cache is not None and _cache[0] == path and _cache[1] == mtime:
        return _cache[2]
    store = load_ontology(path, path_exists)
    _cache = (path, mtime, store)
    return store
```

- [ ] **Step 4: Tests laufen lassen — müssen bestehen**

Run: `.venv/bin/python -m pytest tests/test_ontology_store.py -v`
Expected: 7 passed

- [ ] **Step 5: Bestehende Tests unberührt prüfen + Commit**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: alle grün (keine bestehenden Tests brechen)

```bash
git add src/ontology_store.py tests/test_ontology_store.py
git commit -m "feat(ontology): Flask-freier Ontology-Store mit Fixture-Validierung"
```

---

### Task 2: `/api/ontology/*`-Routen + Fixture v1 + AUTODOC_PATH-Interpolation

**Files:**
- Modify: `src/web_interface/app.py` (Routen nach dem Block `def stats()` bei `app.py:1584` einfügen; Import oben bei den anderen `from …` Imports)
- Modify: `config/config.yaml:20` und `config/config.template.yaml` (autodoc.path env-interpolierbar)
- Create: `../../docbridge_test_data/ontology/ontology_fixture.json` (Platform-Ebene: `KnovasPlatform/docbridge_test_data/ontology/`)
- Test: `tests/test_ontology_api.py`

**Interfaces:**
- Consumes: `ontology_store.get_ontology(path_exists)` aus Task 1; `_resolve_autodoc_path` (existiert in `create_app`-Scope, `app.py:853`)
- Produces (für Frontend-Tasks 4–6):
  - `GET /api/ontology/summary` → `{"success": true, "types": […], "relations": […], "warnings_count": <int>}`
  - `GET /api/ontology/entities?type=<id>` → `{"success": true, "entities": […]}`
  - `GET /api/ontology/entities/<id>` → `{"success": true, "entity": …, "relations": […], "evidence": […]}` oder 404 `{"success": false, "error": "Entität nicht gefunden"}`
  - Endpoint-Namen: `ontology_summary`, `ontology_entities`, `ontology_entity_detail`

- [ ] **Step 1: Failing Tests schreiben** (App-Fixture 1:1 nach dem Muster `tests/test_csrf_enforcement.py:20-90` — `_build_app` mit tmp-Config, `DummyKnovasClient`, `TmpAutodocHandler`, `_login_and_token`; hier gekürzt auf das Neue)

```python
# tests/test_ontology_api.py
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
    import ontology_store
    ontology_store._cache = None  # Test-Isolation

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
    assert "Wissensnetz".encode("utf-8") in resp.data
```

- [ ] **Step 2: Tests laufen lassen — müssen fehlschlagen**

Run: `.venv/bin/python -m pytest tests/test_ontology_api.py -v`
Expected: FAIL — 404 statt 401/200 (Routen existieren nicht). `test_ontology_page_*` schlägt bis Task 3 fehl — für diesen Task zählen die fünf API-Tests; die beiden Page-Tests dürfen erst nach Task 3 grün sein.

- [ ] **Step 3: Routen in `app.py` implementieren**

Import oben zu den bestehenden ergänzen (`app.py`, bei den anderen lokalen Imports um Zeile 27–45):

```python
from ontology_store import get_ontology
```

Routen einfügen (nach `def stats()`-Block, vor `_unique_enrichment_records`):

```python
    # --- Wissensnetz (Ontology Explorer) -----------------------------------
    # Datenvertrag siehe docs/superpowers/specs/2026-08-04-wissensnetz-ontology-mvp-design.md
    # Mock hinter stabilem Vertrag: get_ontology() liest die Fixture; der
    # spaetere echte Knovas-Endpunkt ersetzt nur das Innere des Stores.

    def _ontology_path_exists(rel_path: str) -> bool:
        full = _resolve_autodoc_path(rel_path)
        return bool(full) and os.path.exists(full)

    @app.route('/api/ontology/summary', methods=['GET'])
    def ontology_summary():
        store = get_ontology(path_exists=_ontology_path_exists)
        payload = store.summary()
        return jsonify({'success': True,
                        'types': payload['types'],
                        'relations': payload['relations'],
                        'warnings_count': len(store.warnings)})

    @app.route('/api/ontology/entities', methods=['GET'])
    def ontology_entities():
        type_id = str(request.args.get('type') or '').strip()
        store = get_ontology(path_exists=_ontology_path_exists)
        return jsonify({'success': True, **store.entities_for_type(type_id)})

    @app.route('/api/ontology/entities/<entity_id>', methods=['GET'])
    def ontology_entity_detail(entity_id: str):
        store = get_ontology(path_exists=_ontology_path_exists)
        detail = store.entity_detail(entity_id)
        if detail is None:
            return jsonify({'success': False, 'error': 'Entität nicht gefunden'}), 404
        return jsonify({'success': True, **detail})
```

- [ ] **Step 4: config env-interpolierbar machen**

In `config/config.yaml:20` und in `config/config.template.yaml` (Abschnitt `autodoc:`) die Zeile

```yaml
  path: "/mnt/autodoc"
```

ersetzen durch (Interpolationssyntax wie `config.yaml:200`):

```yaml
  path: "${AUTODOC_PATH:-/mnt/autodoc}"
```

- [ ] **Step 5: Fixture v1 anlegen** — `KnovasPlatform/docbridge_test_data/ontology/ontology_fixture.json`, referenziert das existierende Test-PDF:

```json
{
  "types": [
    {"id": "mandant", "label": "Mandant", "count": 3},
    {"id": "dossier", "label": "Dossier", "count": 4},
    {"id": "vertrag", "label": "Vertrag", "count": 5},
    {"id": "gegenpartei", "label": "Gegenpartei", "count": 3}
  ],
  "relations": [
    {"src": "mandant", "predicate": "hat_Dossier", "dst": "dossier", "count": 4},
    {"src": "dossier", "predicate": "enthält_Vertrag", "dst": "vertrag", "count": 5},
    {"src": "vertrag", "predicate": "Partei", "dst": "gegenpartei", "count": 5}
  ],
  "entities": [
    {"id": "e-001", "label": "Müller Bau AG", "type": "mandant", "doc_count": 2},
    {"id": "e-014", "label": "Dossier 2024-001", "type": "dossier", "doc_count": 2},
    {"id": "e-020", "label": "Werkvertrag Neubau Ost", "type": "vertrag", "doc_count": 1},
    {"id": "e-030", "label": "Immo Invest GmbH", "type": "gegenpartei", "doc_count": 1}
  ],
  "entity_relations": [
    {"src": "e-001", "predicate": "hat_Dossier", "dst": "e-014"},
    {"src": "e-014", "predicate": "enthält_Vertrag", "dst": "e-020"},
    {"src": "e-020", "predicate": "Partei", "dst": "e-030"}
  ],
  "evidence": [
    {"entity_id": "e-001",
     "document": {"path": "corpus/2024-001/Mustervertrag.pdf", "title": "Mustervertrag"},
     "page": 1, "quote": "zwischen der Müller Bau AG als Auftraggeberin"},
    {"entity_id": "e-020",
     "document": {"path": "corpus/2024-001/Mustervertrag.pdf", "title": "Mustervertrag"},
     "page": 1, "quote": "Werkvertrag betreffend Neubau Ost"}
  ]
}
```

(Zitate in Task 7 gegen echten PDF-Inhalt abgleichen; hier zählt der Klickpfad.)

- [ ] **Step 6: Tests laufen lassen**

Run: `.venv/bin/python -m pytest tests/test_ontology_api.py -v`
Expected: 5 API-Tests PASS, die 2 `/ontology`-Page-Tests noch FAIL (Template kommt in Task 3)

- [ ] **Step 7: Gesamtsuite + Commit**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: nur die 2 bekannten Page-Tests rot, sonst alles grün

```bash
git add src/web_interface/app.py tests/test_ontology_api.py config/config.yaml config/config.template.yaml ../../docbridge_test_data/ontology/ontology_fixture.json
git commit -m "feat(ontology): /api/ontology-Routen hinter Login, Fixture v1, AUTODOC_PATH-Interpolation"
```

---

### Task 3: Seite `/ontology` — Template, CSS-Gerüst, Header-Navigation, vendored Cytoscape

**Files:**
- Create: `src/web_interface/templates/ontology.html`
- Create: `src/web_interface/static/css/ontology.css`
- Create: `src/web_interface/static/js/vendor/cytoscape.min.js` (vendored)
- Create: `src/web_interface/static/js/ontology.js` (nur Gerüst; Logik in Tasks 4–6)
- Modify: `src/web_interface/app.py` (Route `ontology_page` neben `index`, `app.py:984`)
- Modify: `src/web_interface/templates/index.html` (Nav-Link im `site-header-bar`, Zeile 13–31)

**Interfaces:**
- Consumes: Endpoint-Namen aus Task 2 (nur via fetch, erst ab Task 4); Jinja-Kontextmuster von `index()` (`app.py:984-1002`)
- Produces: DOM-IDs für Tasks 4–6: `#graphPane`, `#graphContainer`, `#zoomIn`, `#zoomOut`, `#zoomFit`, `#entityPane`, `#entityPaneBody`, `#docPane`, `#docPaneBody`; globales `window.cytoscape` (UMD)

- [ ] **Step 1: Cytoscape vendoren**

```bash
mkdir -p src/web_interface/static/js/vendor
curl -fsSL https://unpkg.com/cytoscape@3.30.4/dist/cytoscape.min.js \
  -o src/web_interface/static/js/vendor/cytoscape.min.js
head -c 200 src/web_interface/static/js/vendor/cytoscape.min.js   # Sanity: minified JS, kein HTML-Fehltext
```

(Falls unpkg klemmt: `https://cdn.jsdelivr.net/npm/cytoscape@3.30.4/dist/cytoscape.min.js` — Datei wird committet, Laufzeit bleibt offline-fähig.)

- [ ] **Step 2: Route ergänzen** (direkt unter `index()`, `app.py:1002`)

```python
    @app.route('/ontology')
    def ontology_page():
        """Wissensnetz: Ontologie-Explorer (Typ-Graph -> Entitaeten -> Belege -> PDF)."""
        return render_template(
            'ontology.html',
            app_title=web_app_title,
            csrf_token=_ensure_csrf_token(),
            asset_version=_static_asset_version(),
        )
```

- [ ] **Step 3: Template anlegen** — Kopf/Fuss exakt im Muster von `index.html` (gleiche Logo-/Logout-Struktur, `templates/index.html:11-31`), dazwischen:

```html
<!-- src/web_interface/templates/ontology.html -->
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Wissensnetz – {{ app_title }}</title>
    <link rel="icon" href="{{ url_for('static', filename='img/favicon.svg') }}" type="image/svg+xml">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}?v={{ asset_version }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/ontology.css') }}?v={{ asset_version }}">
</head>
<body>
    <div class="container container-wide">
        <header class="site-header">
            <div class="site-header-bar">
                <a class="site-brand" href="{{ url_for('index') }}" aria-label="{{ app_title }}">
                    <img src="{{ url_for('static', filename='img/knovas-logo.svg') }}"
                         alt="Knovas" width="132" height="18">
                </a>
                <nav class="site-nav" aria-label="Hauptnavigation">
                    <a href="{{ url_for('index') }}">Suche</a>
                    <a href="{{ url_for('ontology_page') }}" aria-current="page">Wissensnetz</a>
                </nav>
                <form method="post" action="{{ url_for('logout') }}" class="logout-form">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                    <button type="submit" class="btn btn-outline logout-button">Abmelden</button>
                </form>
            </div>
            <h1 class="site-greeting">Wissensnetz<br>
                <span class="site-greeting-question">Was Knovas in Ihren Dokumenten verstanden hat</span>
            </h1>
        </header>

        <main class="ontology-layout">
            <section class="ontology-pane" id="graphPane" aria-label="Ontologie-Graph">
                <div class="ontology-pane-header">
                    <h2>Struktur</h2>
                    <div class="graph-toolbar" role="toolbar" aria-label="Zoom-Navigation">
                        <button type="button" id="zoomIn" class="btn btn-outline" aria-label="Vergrössern">+</button>
                        <button type="button" id="zoomOut" class="btn btn-outline" aria-label="Verkleinern">−</button>
                        <button type="button" id="zoomFit" class="btn btn-outline">Einpassen</button>
                    </div>
                </div>
                <div id="graphContainer" aria-hidden="true"></div>
                <p class="ontology-empty" id="graphEmpty" hidden>Noch keine Ontologie-Daten vorhanden.</p>
            </section>

            <section class="ontology-pane" id="entityPane" aria-label="Entitäten und Belege">
                <div class="ontology-pane-header"><h2 id="entityPaneTitle">Entitäten</h2></div>
                <div id="entityPaneBody">
                    <p class="ontology-empty">Typ im Netz wählen, um Entitäten zu sehen.</p>
                </div>
            </section>

            <section class="ontology-pane" id="docPane" aria-label="Dokument">
                <div class="ontology-pane-header"><h2 id="docPaneTitle">Dokument</h2></div>
                <div id="docPaneBody">
                    <p class="ontology-empty">Beleg wählen, um die Fundstelle zu sehen.</p>
                </div>
            </section>
        </main>
    </div>

    <script src="{{ url_for('static', filename='js/vendor/cytoscape.min.js') }}?v={{ asset_version }}"></script>
    <script src="{{ url_for('static', filename='js/ontology.js') }}?v={{ asset_version }}"></script>
</body>
</html>
```

- [ ] **Step 4: CSS-Gerüst** — nur Tokens, keine neuen Farben:

```css
/* src/web_interface/static/css/ontology.css — Wissensnetz-Layout.
   Ausschliesslich Design-Tokens aus style.css; keine neuen Farbwerte. */

.container-wide { max-width: 1440px; }

.ontology-layout {
    display: grid;
    grid-template-columns: minmax(320px, 5fr) minmax(280px, 4fr) minmax(320px, 6fr);
    gap: 16px;
    align-items: stretch;
    min-height: 640px;
}

.ontology-pane {
    background: var(--surface-sunken);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-lg);
    display: flex;
    flex-direction: column;
    min-height: 0;
}

.ontology-pane-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border-color);
}

.ontology-pane-header h2 {
    font-family: var(--font-heading);
    font-size: 0.95rem;
    color: var(--text-primary);
}

.graph-toolbar { display: flex; gap: 6px; }
.graph-toolbar .btn { padding: 4px 10px; line-height: 1; }

#graphContainer { flex: 1; min-height: 480px; }

.ontology-empty {
    color: var(--text-secondary);
    padding: 24px 16px;
    text-align: center;
}

.site-nav { display: flex; gap: 16px; margin-inline: auto; }
.site-nav a {
    color: var(--text-secondary);
    text-decoration: none;
    font-family: var(--font-heading);
    font-size: 0.9rem;
    padding: 4px 2px;
    border-bottom: 2px solid transparent;
}
.site-nav a:hover { color: var(--primary-color); }
.site-nav a[aria-current="page"] {
    color: var(--primary-color);
    border-bottom-color: var(--accent);
}

#entityPaneBody, #docPaneBody { flex: 1; min-height: 0; overflow: auto; }
```

- [ ] **Step 5: JS-Gerüst** (`static/js/ontology.js` — nur Shell, Logik folgt):

```javascript
// Knovas Wissensnetz — Ontologie-Explorer (Vertrag: /api/ontology/*)
'use strict';

/** Schweizer Tausendertrennung: 1847 -> "1'847" (ASCII-Apostroph). */
function formatCount(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, "'");
}

/** CI-Farben zur Laufzeit aus den Design-Tokens lesen (keine Hexwerte im JS). */
function cssToken(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

class WissensnetzApp {
    constructor() {
        this.cy = null;
        this.selectedType = null;
        this.selectedEntity = null;
        this.entityAbort = null;
        this.init();
    }

    async init() {
        // Task 4: Graph laden + rendern
    }
}

document.addEventListener('DOMContentLoaded', () => { new WissensnetzApp(); });
```

- [ ] **Step 6: Nav auch in `index.html`** — in `templates/index.html` innerhalb `site-header-bar` (nach dem `site-brand`-`</a>`, Zeile 18) einfügen:

```html
                <nav class="site-nav" aria-label="Hauptnavigation">
                    <a href="{{ url_for('index') }}" aria-current="page">Suche</a>
                    <a href="{{ url_for('ontology_page') }}">Wissensnetz</a>
                </nav>
```

und im `<head>` von `index.html` (nach der style.css-Zeile) `ontology.css` NICHT einbinden — die `.site-nav`-Regeln gehören stattdessen ans Ende von `style.css`? **Nein:** Regeln für `.site-nav` aus Step 4 in `style.css` verschieben (ans Dateiende, mit Kommentar `/* Hauptnavigation (Suche | Wissensnetz) */`), damit beide Seiten sie ohne Doppel-Include haben. `ontology.css` behält nur Wissensnetz-eigene Regeln.

- [ ] **Step 7: Tests laufen lassen**

Run: `.venv/bin/python -m pytest tests/test_ontology_api.py -v`
Expected: alle 7 PASS (jetzt auch die beiden `/ontology`-Page-Tests)

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: alles grün

- [ ] **Step 8: Manuelle Verifikation localhost**

```bash
export AUTODOC_PATH="$(cd ../.. && pwd)/docbridge_test_data/AutoDoc"
export ONTOLOGY_FIXTURE_PATH="$(cd ../.. && pwd)/docbridge_test_data/ontology/ontology_fixture.json"
# dann wie gehabt starten (run_local.sh um diese zwei Exporte ergänzen, falls vorhanden)
```

Checkliste: `/ontology` lädt nach Login · Header mit Logo + Nav „Suche | Wissensnetz" · drei Panes sichtbar, CI-konform · keine Konsolen-Fehler · `window.cytoscape` definiert (DevTools).

- [ ] **Step 9: Commit**

```bash
git add src/web_interface/templates/ontology.html src/web_interface/templates/index.html \
        src/web_interface/static/css/ontology.css src/web_interface/static/css/style.css \
        src/web_interface/static/js/ontology.js src/web_interface/static/js/vendor/cytoscape.min.js \
        src/web_interface/app.py
git commit -m "feat(ontology): Seite /ontology mit Drei-Spalten-Gerüst, Nav und vendored Cytoscape"
```

---

### Task 4: Graph-Pane — Rendern, CI-Styling, Zoom-Navigation

**Files:**
- Modify: `src/web_interface/static/js/ontology.js`

**Interfaces:**
- Consumes: `GET /api/ontology/summary` (Task 2), DOM-IDs (Task 3), `cssToken`/`formatCount` (Task 3)
- Produces: `WissensnetzApp.onTypeSelect(typeId, label)` — Hook, den Task 5 implementiert (hier als Stub `console.debug`); Cytoscape-Instanz in `this.cy` (eine Instanz, wird nie neu erzeugt)

- [ ] **Step 1: `init()` + Graph implementieren** — Inhalt von `WissensnetzApp` ersetzen/erweitern:

```javascript
    async init() {
        try {
            const resp = await fetch('/api/ontology/summary');
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            if (!data.types.length) {
                document.getElementById('graphContainer').hidden = true;
                document.getElementById('graphEmpty').hidden = false;
                return;
            }
            this.renderGraph(data);
            this.bindZoomControls();
        } catch (err) {
            console.error('Wissensnetz: Summary nicht ladbar', err);
            const empty = document.getElementById('graphEmpty');
            empty.textContent = 'Ontologie konnte nicht geladen werden. Seite neu laden.';
            empty.hidden = false;
        }
    }

    renderGraph(data) {
        const maxCount = Math.max(...data.types.map((t) => t.count), 1);
        const nodes = data.types.map((t) => ({
            data: { id: t.id, label: t.label, count: t.count,
                    size: 32 + Math.round(40 * (t.count / maxCount)) },
        }));
        const maxRel = Math.max(...data.relations.map((r) => r.count), 1);
        const edges = data.relations.map((r, i) => ({
            data: { id: `r-${i}`, source: r.src, target: r.dst,
                    label: `${r.predicate} (${formatCount(r.count)})`,
                    width: 1.5 + 3 * (r.count / maxRel) },
        }));

        this.cy = cytoscape({
            container: document.getElementById('graphContainer'),
            elements: { nodes, edges },
            minZoom: 0.2,
            maxZoom: 4,
            wheelSensitivity: 0.2,
            style: [
                { selector: 'node', style: {
                    'background-color': cssToken('--surface-sunken'),
                    'border-width': 2,
                    'border-color': cssToken('--accent'),
                    'width': 'data(size)',
                    'height': 'data(size)',
                    'label': 'data(label)',
                    'font-family': cssToken('--font-body') || 'IBM Plex Sans, sans-serif',
                    'font-size': 12,
                    'color': cssToken('--text-primary'),
                    'text-valign': 'bottom',
                    'text-margin-y': 6,
                } },
                { selector: 'node:selected', style: {
                    'background-color': cssToken('--highlight'),
                    'border-color': cssToken('--primary-color'),
                    'border-width': 3,
                } },
                { selector: 'edge', style: {
                    'line-color': cssToken('--border-color'),
                    'target-arrow-shape': 'triangle',
                    'target-arrow-color': cssToken('--border-color'),
                    'curve-style': 'bezier',
                    'width': 'data(width)',
                    'label': 'data(label)',
                    'font-size': 10,
                    'color': cssToken('--text-secondary'),
                    'text-rotation': 'autorotate',
                    'text-background-color': cssToken('--bg-color'),
                    'text-background-opacity': 0.85,
                    'text-background-padding': 2,
                } },
                { selector: 'edge:selected', style: {
                    'line-color': cssToken('--accent'),
                    'target-arrow-color': cssToken('--accent'),
                } },
            ],
            // Deterministisch (Spec Regel 4): concentric sortiert stabil nach count.
            layout: { name: 'concentric', fit: true, padding: 40, minNodeSpacing: 60,
                      concentric: (n) => n.data('count'), levelWidth: () => 1 },
        });

        this.cy.on('tap', 'node', (evt) => {
            this.onTypeSelect(evt.target.id(), evt.target.data('label'));
        });
        this.cy.on('dbltap', (evt) => {
            if (evt.target === this.cy) this.cy.fit(undefined, 40);
        });
    }

    bindZoomControls() {
        const zoomBy = (factor) => {
            this.cy.zoom({
                level: this.cy.zoom() * factor,
                renderedPosition: { x: this.cy.width() / 2, y: this.cy.height() / 2 },
            });
        };
        document.getElementById('zoomIn').addEventListener('click', () => zoomBy(1.25));
        document.getElementById('zoomOut').addEventListener('click', () => zoomBy(0.8));
        document.getElementById('zoomFit').addEventListener('click', () => this.cy.fit(undefined, 40));
    }

    onTypeSelect(typeId, label) {
        console.debug('Typ gewählt:', typeId, label);  // Task 5 ersetzt dies
    }
```

- [ ] **Step 2: Manuelle Verifikation** (Server aus Task 3 Step 8 läuft)

Checkliste `/ontology`: 4 Typ-Knoten mit Labels, Grösse ∝ count · Kantenlabels `hat_Dossier (4)` mit Apostroph-Format bei ≥4 Stellen · **+ / − / Einpassen funktionieren**, Scrollrad zoomt, Doppelklick auf Fläche passt ein · Zoom stoppt bei min/max (nichts „verliert sich") · Reload ⇒ identisches Layout (deterministisch) · Klick auf Knoten markiert ihn im Token-Blau und loggt `Typ gewählt:` · Farben = CI (mit DevTools gegen `--accent`/`--primary-color` prüfen).

- [ ] **Step 3: Commit**

```bash
git add src/web_interface/static/js/ontology.js
git commit -m "feat(ontology): Typ-Graph mit CI-Styling, deterministischem Layout und Zoom-Navigation"
```

---

### Task 5: Mittlere Spalte — Entitätenliste, Detail, Belegliste

**Files:**
- Modify: `src/web_interface/static/js/ontology.js`
- Modify: `src/web_interface/static/css/ontology.css`

**Interfaces:**
- Consumes: `GET /api/ontology/entities?type=…`, `GET /api/ontology/entities/<id>` (Task 2); `onTypeSelect`-Hook (Task 4)
- Produces: `WissensnetzApp.onEvidenceSelect(evidence)` — Hook für Task 6 (hier Stub); Belegliste rendert `<button class="evidence-item">` mit `data-`Attributen `path`, `page`, `title`

- [ ] **Step 1: Fetch-Helfer + Zustandslogik implementieren** — in `WissensnetzApp` ergänzen; `onTypeSelect`-Stub ersetzen:

```javascript
    /** Escaping vor jeder Interpolation — Fixture-/Backend-Text ist Fremdtext. */
    static esc(s) {
        const d = document.createElement('span');
        d.textContent = String(s ?? '');
        return d.innerHTML;
    }

    async fetchJson(url) {
        if (this.entityAbort) this.entityAbort.abort();     // Spec Regel 5
        this.entityAbort = new AbortController();
        const resp = await fetch(url, { signal: this.entityAbort.signal });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json();
    }

    async onTypeSelect(typeId, label) {
        this.selectedType = typeId;
        const body = document.getElementById('entityPaneBody');
        document.getElementById('entityPaneTitle').textContent = label;
        body.innerHTML = '<p class="ontology-empty">Lädt…</p>';
        try {
            const data = await this.fetchJson(
                `/api/ontology/entities?type=${encodeURIComponent(typeId)}`);
            if (!data.entities.length) {
                body.innerHTML = '<p class="ontology-empty">Keine Entitäten dieses Typs im Korpus.</p>';
                return;
            }
            const esc = WissensnetzApp.esc;
            body.innerHTML = `
                <table class="entity-table">
                  <thead><tr><th>Entität</th><th class="num">Dokumente</th></tr></thead>
                  <tbody>${data.entities.map((e) => `
                    <tr><td><button type="button" class="btn-text entity-link"
                                data-id="${esc(e.id)}">${esc(e.label)}</button></td>
                        <td class="num">${formatCount(e.doc_count)}</td></tr>`).join('')}
                  </tbody>
                </table>`;
            body.querySelectorAll('.entity-link').forEach((btn) =>
                btn.addEventListener('click', () => this.onEntitySelect(btn.dataset.id)));
        } catch (err) {
            if (err.name === 'AbortError') return;
            body.innerHTML = '<p class="ontology-empty">Entitäten konnten nicht geladen werden.</p>';
        }
    }

    async onEntitySelect(entityId) {
        this.selectedEntity = entityId;
        const body = document.getElementById('entityPaneBody');
        try {
            const data = await this.fetchJson(
                `/api/ontology/entities/${encodeURIComponent(entityId)}`);
            const esc = WissensnetzApp.esc;
            const relations = data.relations.length
                ? `<ul class="entity-relations">${data.relations.map((r) => `
                     <li><span class="predicate">${esc(r.predicate)}</span>
                         <button type="button" class="btn-text entity-link"
                                 data-id="${esc(r.target.id)}">${esc(r.target.label)}</button></li>`).join('')}
                   </ul>`
                : '<p class="ontology-empty">Keine Verbindungen erfasst.</p>';
            const evidence = data.evidence.length
                ? `<ol class="evidence-list">${data.evidence.map((ev, i) => `
                     <li><button type="button" class="evidence-item" data-index="${i}"
                                 data-path="${esc(ev.document.path)}" data-page="${ev.page}"
                                 data-title="${esc(ev.document.title)}">
                         <span class="evidence-quote">«${esc(ev.quote)}»</span>
                         <span class="evidence-source">${esc(ev.document.title)}, Seite ${formatCount(ev.page)}</span>
                     </button></li>`).join('')}
                   </ol>`
                : '<p class="ontology-empty">Keine Belege über dem Schwellenwert.</p>';
            body.innerHTML = `
                <div class="entity-detail">
                    <button type="button" class="btn-text" id="entityBack">← Zurück zur Liste</button>
                    <h3>${esc(data.entity.label)}</h3>
                    <h4>Verbindungen</h4>${relations}
                    <h4>Belege</h4>${evidence}
                </div>`;
            document.getElementById('entityBack').addEventListener('click', () => {
                const node = this.cy.getElementById(this.selectedType);
                this.onTypeSelect(this.selectedType, node.data('label'));
            });
            body.querySelectorAll('.entity-link').forEach((btn) =>
                btn.addEventListener('click', () => this.onEntitySelect(btn.dataset.id)));
            body.querySelectorAll('.evidence-item').forEach((btn) =>
                btn.addEventListener('click', () => {
                    body.querySelectorAll('.evidence-item.selected')
                        .forEach((b) => b.classList.remove('selected'));
                    btn.classList.add('selected');
                    this.onEvidenceSelect({ path: btn.dataset.path,
                                            page: Number(btn.dataset.page),
                                            title: btn.dataset.title });
                }));
        } catch (err) {
            if (err.name === 'AbortError') return;
            body.innerHTML = '<p class="ontology-empty">Entität konnte nicht geladen werden.</p>';
        }
    }

    onEvidenceSelect(evidence) {
        console.debug('Beleg gewählt:', evidence);  // Task 6 ersetzt dies
    }
```

- [ ] **Step 2: CSS für Tabelle/Detail/Belege** — an `ontology.css` anhängen (nur Tokens):

```css
.entity-table { width: 100%; border-collapse: collapse; }
.entity-table th, .entity-table td {
    padding: 8px 16px;
    border-bottom: 1px solid var(--border-color);
    text-align: left;
    color: var(--text-primary);
}
.entity-table th {
    font-family: var(--font-heading);
    font-size: 0.8rem;
    color: var(--text-secondary);
}
.entity-table .num { text-align: right; font-variant-numeric: tabular-nums; }

.entity-detail { padding: 12px 16px; }
.entity-detail h3 { font-family: var(--font-heading); color: var(--text-primary); margin: 8px 0; }
.entity-detail h4 {
    font-family: var(--font-heading);
    font-size: 0.8rem;
    color: var(--text-secondary);
    margin: 16px 0 6px;
}
.entity-relations { list-style: none; padding: 0; }
.entity-relations li { padding: 4px 0; }
.entity-relations .predicate {
    font-family: var(--font-heading);
    font-size: 0.8rem;
    color: var(--text-secondary);
    margin-right: 8px;
}

.evidence-list { list-style: none; padding: 0; }
.evidence-item {
    display: block;
    width: 100%;
    text-align: left;
    background: var(--card-bg);
    border: 1px solid var(--border-color);
    border-radius: var(--radius);
    padding: 10px 12px;
    margin-bottom: 8px;
    cursor: pointer;
    font: inherit;
    color: var(--text-primary);
}
.evidence-item:hover { border-color: var(--accent); }
.evidence-item:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.evidence-item.selected { background: var(--highlight); border-color: var(--primary-color); }
.evidence-quote { display: block; }
.evidence-source { display: block; margin-top: 4px; font-size: 0.85rem; color: var(--text-secondary); }
```

- [ ] **Step 3: Manuelle Verifikation**

Checkliste: Klick „Mandant" ⇒ Tabelle mit „Müller Bau AG · 2" · Klick Entität ⇒ Detail mit Verbindungen (klickbar ⇒ springt zu Ziel-Entität) und Belegen · „← Zurück zur Liste" funktioniert · Beleg-Klick markiert Karte (`--highlight`) und loggt `Beleg gewählt:` · schneller Doppel-Klick auf zwei Typen ⇒ keine veraltete Antwort gewinnt (AbortController) · leerer Typ zeigt Befund-Text.

- [ ] **Step 4: Commit**

```bash
git add src/web_interface/static/js/ontology.js src/web_interface/static/css/ontology.css
git commit -m "feat(ontology): Entitätenliste, Detailansicht und Belegliste mit Abbruch veralteter Requests"
```

---

### Task 6: Rechte Spalte — PDF auf der belegten Seite

**Files:**
- Modify: `src/web_interface/static/js/ontology.js` (Stub `onEvidenceSelect` ersetzen)
- Modify: `src/web_interface/static/css/ontology.css`

**Interfaces:**
- Consumes: bestehender Endpunkt `GET /api/document/<doc_id>/preview?path=…` (`app.py:1257`, Flag `open.pdf_inline_in_browser` default true, `app.py:723`); Hook + `data-`Attribute aus Task 5
- Produces: kompletter Klickpfad Typ → Entität → Beleg → PDF-Seite

- [ ] **Step 1: `onEvidenceSelect` implementieren**

```javascript
    onEvidenceSelect(evidence) {
        const body = document.getElementById('docPaneBody');
        document.getElementById('docPaneTitle').textContent =
            `${evidence.title} – Seite ${formatCount(evidence.page)}`;
        // Query VOR dem Fragment: der browsernative PDF-Viewer liest #page=N.
        const url = `/api/document/${encodeURIComponent(evidence.title)}/preview` +
                    `?path=${encodeURIComponent(evidence.path)}#page=${evidence.page}`;
        body.innerHTML = '';
        const frame = document.createElement('iframe');
        frame.className = 'doc-frame';
        frame.title = `Vorschau: ${evidence.title}`;
        frame.src = url;
        frame.addEventListener('error', () => {
            body.innerHTML =
                '<p class="ontology-empty">Dokument konnte nicht geladen werden.</p>';
        });
        body.appendChild(frame);
    }
```

- [ ] **Step 2: CSS ergänzen**

```css
.doc-frame { width: 100%; height: 100%; min-height: 560px; border: 0;
             border-radius: 0 0 var(--radius-lg) var(--radius-lg); background: var(--card-bg); }
```

- [ ] **Step 3: Manuelle Verifikation — der Beweis-Moment**

Checkliste: Beleg-Klick ⇒ PDF erscheint rechts, Titelzeile „Mustervertrag – Seite 1" · Graph und Liste bleiben unverändert stehen (Panes unabhängig) · Beleg mit absichtlich falschem Pfad in lokaler Fixture-Kopie ⇒ Beleg erscheint gar nicht erst (Store filtert; Log-Warnung im Flask-Output) · kompletter Pfad in <4 Klicks: Knoten → Entität → Beleg → Seite.

- [ ] **Step 4: Gesamtsuite + Commit**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: alles grün

```bash
git add src/web_interface/static/js/ontology.js src/web_interface/static/css/ontology.css
git commit -m "feat(ontology): Dokumentvorschau auf belegter Seite — Klickpfad komplett"
```

---

### Task 7: Demo-Korpus + Fixture-Ausbau + Smoke-Test

**Files:**
- Create: `KnovasPlatform/docbridge_test_data/AutoDoc/wissensnetz/` (3 generierte Demo-PDFs)
- Modify: `KnovasPlatform/docbridge_test_data/ontology/ontology_fixture.json` (Ausbau auf ~8 Typen / ~20 Entitäten, Belege auf die neuen PDFs)
- Create: `scripts/make_wissensnetz_demo_pdfs.py` (Komponenten-Ebene, reproduzierbar)

**Interfaces:**
- Consumes: alles aus Tasks 1–6
- Produces: demo-fähiger localhost-Stand; Zitate in der Fixture stimmen wörtlich mit den PDFs überein

- [ ] **Step 1: PDF-Generator-Skript** (pymupdf ist installiert — `requirements.txt:‹pymupdf›`; Inhalte: fiktive Schweizer Kanzlei-Dokumente, mehrseitig, damit `#page=N` sichtbar wirkt)

```python
# scripts/make_wissensnetz_demo_pdfs.py
"""Erzeugt fiktive Kanzlei-PDFs fuer die Wissensnetz-Demo (einmalig, committet).

Wichtig: Die quote-Felder der Fixture muessen woertlich im PDF stehen --
der Beleg-Klick zeigt die Seite, und der Satz muss dort auffindbar sein.
"""
from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parents[1].parent.parent / "docbridge_test_data" / "AutoDoc" / "wissensnetz"

DOCS = {
    "werkvertrag_neubau_ost.pdf": [
        ("Werkvertrag", [
            "Werkvertrag betreffend Neubau Ost",
            "zwischen der Müller Bau AG als Auftraggeberin",
            "und der Immo Invest GmbH als Bestellerin.",
        ]),
        ("Vertragsgegenstand", [
            "Die Parteien vereinbaren die Erstellung des Rohbaus",
            "gemäss Baubeschrieb vom 12. März 2024.",
        ]),
        ("Fristen", [
            "Die Kündigungsfrist beträgt drei Monate per Monatsende.",
            "Baubeginn ist der 1. Mai 2024.",
        ]),
    ],
    "schreiben_gericht_2024_001.pdf": [
        ("Bezirksgericht Zürich", [
            "In Sachen Müller Bau AG gegen Immo Invest GmbH",
            "betreffend Forderung aus Werkvertrag",
            "wird die Frist zur Klageantwort auf den 30. September 2024 angesetzt.",
        ]),
    ],
    "mandatsvereinbarung_mueller.pdf": [
        ("Mandatsvereinbarung", [
            "Die Müller Bau AG erteilt der Kanzlei das Mandat",
            "zur Vertretung im Dossier 2024-001.",
        ]),
        ("Honorar", [
            "Es gilt ein Stundenansatz von 350 Franken.",
        ]),
    ],
}

OUT.mkdir(parents=True, exist_ok=True)
for filename, pages in DOCS.items():
    doc = pymupdf.open()
    for heading, lines in pages:
        page = doc.new_page()
        page.insert_text((72, 96), heading, fontsize=16, fontname="hebo")
        for i, line in enumerate(lines):
            page.insert_text((72, 140 + 22 * i), line, fontsize=11, fontname="helv")
    doc.save(OUT / filename)
    print(f"geschrieben: {OUT / filename}")
```

Run: `.venv/bin/python scripts/make_wissensnetz_demo_pdfs.py`
Expected: 3 Zeilen `geschrieben: …/wissensnetz/…pdf`

- [ ] **Step 2: Fixture ausbauen** — Typen ergänzen zu: `mandant, dossier, vertrag, gegenpartei, gericht, frist, honorar, dokumenttyp` (8 Typen, je 2–4 Entitäten, Relationen mit plausiblen Zählwerten, mind. ein Wert ≥ 1000 z. B. `{"id": "dokumenttyp", "label": "Dokumenttyp", "count": 1847}` damit `1'847` sichtbar wird). Jede Entität mit mindestens einem Beleg; `document.path` = `wissensnetz/<datei>.pdf`, `quote` wörtlich aus Step 1, `page` passend (Werkvertrag Fristen = Seite 3, Gerichtsschreiben = Seite 1, Mandat Honorar = Seite 2).

- [ ] **Step 3: Validierung, dass die Fixture sauber ist**

```bash
ONTOLOGY_FIXTURE_PATH="$(cd ../.. && pwd)/docbridge_test_data/ontology/ontology_fixture.json" \
.venv/bin/python -c "
import os, sys; sys.path.insert(0, 'src')
from ontology_store import get_ontology
s = get_ontology()
print('types:', len(s.summary()['types']), 'warnings:', s.warnings)
assert not s.warnings, 'Fixture hat Validierungswarnungen!'
"
```

Expected: `types: 8 warnings: []`

- [ ] **Step 4: Voller Smoke-Test localhost** (Env wie Task 3 Step 8)

Checkliste — das ist die Demo-Generalprobe: Login ⇒ Nav „Wissensnetz" ⇒ Graph mit 8 Typen, `1'847` auf einer Kante/einem Knoten sichtbar · Zoom +/−/Einpassen · Typ ⇒ Liste ⇒ Entität ⇒ Beleg ⇒ **PDF öffnet auf der richtigen Seite und das Zitat steht dort wörtlich** · Verbindungen springen zwischen Entitäten · alle Texte deutsch, keine Konsolen-Fehler, CI durchgängig.

- [ ] **Step 5: Gesamtsuite + Commit**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: alles grün

```bash
git add scripts/make_wissensnetz_demo_pdfs.py \
        ../../docbridge_test_data/AutoDoc/wissensnetz/ \
        ../../docbridge_test_data/ontology/ontology_fixture.json
git commit -m "feat(ontology): Demo-Korpus (3 fiktive Kanzlei-PDFs) und ausgebaute Fixture"
```

---

### Task 8: Finished-Product-Pass — Skeletons, Übergänge, Hover, Responsive

**Files:**
- Modify: `src/web_interface/static/js/ontology.js`
- Modify: `src/web_interface/static/css/ontology.css`

**Interfaces:**
- Consumes: alles aus Tasks 3–6
- Produces: der Zustand, der am Sonntag gezeigt wird — nichts blitzt leer, alles hat Hover/Fokus, nichts sieht nach Prototyp aus

- [ ] **Step 1: Skeleton statt „Lädt…"-Text** (Spec/Kontextdokument: „Skeletons, never spinners"). In `ontology.css`:

```css
/* Lade-Skeleton: schimmernde Platzhalterzeilen statt Spinner/Textblitzer.
   Nur Token-Farben; die Animation nutzt Transparenz, keinen neuen Farbwert. */
.skeleton-row {
    height: 14px;
    margin: 12px 16px;
    border-radius: var(--radius);
    background: var(--highlight);
    animation: skeleton-pulse 1.2s ease-in-out infinite;
}
.skeleton-row:nth-child(2) { width: 70%; }
.skeleton-row:nth-child(3) { width: 85%; }
@keyframes skeleton-pulse {
    0%, 100% { opacity: 0.45; }
    50%      { opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
    .skeleton-row { animation: none; opacity: 0.6; }
}
```

In `ontology.js` einen Helfer ergänzen und **beide** `'<p class="ontology-empty">Lädt…</p>'`-Stellen (falls vorhanden) sowie den Body-Reset in `onTypeSelect` ersetzen:

```javascript
function skeleton(rows = 3) {
    return Array.from({ length: rows }, () => '<div class="skeleton-row"></div>').join('');
}
// in onTypeSelect: body.innerHTML = skeleton(4);
// in onEntitySelect: vor dem fetch ebenfalls body.innerHTML = skeleton(5);
```

- [ ] **Step 2: Ruhige Übergänge + Hover im Graph.** In `ontology.css`:

```css
.evidence-item, .site-nav a, .btn { transition: border-color .15s ease, background-color .15s ease, color .15s ease; }
```

In `renderGraph` (Task 4) zwei Style-Einträge ergänzen (nach `node:selected`):

```javascript
                { selector: 'node.hovered', style: {
                    'border-color': cssToken('--primary-color'),
                } },
```

und nach den bestehenden `cy.on(...)`-Bindings:

```javascript
        this.cy.on('mouseover', 'node', (evt) => evt.target.addClass('hovered'));
        this.cy.on('mouseout', 'node', (evt) => evt.target.removeClass('hovered'));
        this.cy.on('mouseover', 'node', () => {
            document.getElementById('graphContainer').style.cursor = 'pointer';
        });
        this.cy.on('mouseout', 'node', () => {
            document.getElementById('graphContainer').style.cursor = '';
        });
```

- [ ] **Step 3: Responsive-Verhalten** — unter ~1100px stapeln statt quetschen. In `ontology.css`:

```css
@media (max-width: 1100px) {
    .ontology-layout { grid-template-columns: 1fr; }
    #graphContainer { min-height: 360px; }
    .doc-frame { min-height: 480px; }
}
```

- [ ] **Step 4: Manuelle Abnahme gegen die Spec-Liste „Anspruch fertiges Produkt"**

Checkliste: kein Pane blitzt je leer (Skeleton sichtbar bei künstlicher Drossel: DevTools → Network → Slow 3G) · jeder interaktive Zustand hat Hover UND `:focus-visible` (mit Tab durchgehen!) · Graph-Knoten zeigen Cursor + Hover-Rand · Fenster schmal ziehen ⇒ Spalten stapeln sauber · `prefers-reduced-motion` respektiert (macOS: Bedienungshilfen) · Seitentitel „Wissensnetz – …", Favicon vorhanden · Gesamteindruck neben der Suche: gleiche Familie, gleiche Dichte, gleiche Ruhe.

- [ ] **Step 5: Gesamtsuite + Commit**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: alles grün

```bash
git add src/web_interface/static/js/ontology.js src/web_interface/static/css/ontology.css
git commit -m "feat(ontology): Finished-Product-Pass — Skeletons, Übergänge, Hover, Responsive"
```
