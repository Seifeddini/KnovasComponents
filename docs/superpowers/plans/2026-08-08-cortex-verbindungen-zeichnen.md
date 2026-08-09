# Verbindungen im Cortex-Graphen zeichnen — Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verbindungen lassen sich direkt im Graphen ziehen — zwischen Typen (als Vorgabe) und zwischen Entitäten (als echte Kante).

**Architecture:** Die Zieh-Geste kommt in eine eigene Datei (`ontology_connect.js`), weil `ontology.js` bereits 1339 Zeilen hat. Sie kennt nur Cytoscape und meldet einen fertigen Zug per Rückruf; das Anlegen und Neuzeichnen bleibt in `CortexApp`. Serverseitig kommen zwei Routen dazu (Vorgabe anlegen, beide löschen); beide Quellen — Fixture und Graph-API — bekommen dieselben Methoden.

**Tech Stack:** Vanilla ES6 ohne Build-Schritt, Cytoscape 3.30.4 (vendored, keine Erweiterungen), Flask, pytest.

## Global Constraints

- **Keine neuen Farbwerte.** Ausschliesslich Design-Tokens aus `style.css` (`--accent`, `--primary-color`, `--border-color`, `--text-secondary`, `--error-color`, `--surface-sunken`, `--card-bg`, `--highlight`).
- **Keine Striche in UI-Texten.** Keine Bindestriche in zusammengesetzten Begriffen, keine Gedankenstriche; ganze Sätze schreiben.
- **Neue Routen niemals in `_CSRF_EXEMPT_ENDPOINTS` oder das Login-Exempt-Set.** Schreibende Routen verlangen den Header `X-CSRF-Token`.
- **Fremdtext immer escapen** vor jeder Interpolation in `innerHTML` (`CortexApp.esc`).
- **Fehler eskalieren nie.** Kaputte Daten werden gefiltert und geloggt, die Seite zeigt weniger, aber nie einen 500.
- **Tests laufen mit** `cd KnovasPlatform/components/docbridge_integration && .venv/bin/python -m pytest tests/` und müssen grün bleiben (aktuell 192 passed, 3 skipped).
- **Die versionierte Demo-Fixture nicht verändern.** Der Entwicklungsserver arbeitet auf `ontology_fixture.local.json` (gitignored).

---

## Dateien

| Datei | Verantwortung |
|---|---|
| `src/web_interface/static/js/ontology_connect.js` (neu) | Nur die Zieh-Geste: Griff zeigen, Linie mitziehen, gültiges Ziel erkennen, Ergebnis melden |
| `src/web_interface/static/js/ontology.js` (ändern) | Rückruf verdrahten, Namensabfrage, Anlegen, Linie zeichnen, Klick auf Linie |
| `src/web_interface/static/css/ontology.css` (ändern) | Griff, Namensfeld, Kantenauswahl |
| `src/web_interface/templates/ontology.html` (ändern) | `ontology_connect.js` einbinden |
| `src/ontology_store.py` (ändern) | `create_type_relation`, `delete_type_relation`, `delete_relation` |
| `src/ontology_graph.py` (ändern) | Dieselben Methoden gegen Schema-Attribute und Kanten |
| `src/knovas_client.py` (ändern) | `graph_create_schema_attribute`, `graph_delete_schema_attribute`, `graph_delete_edge` |
| `src/web_interface/app.py` (ändern) | `POST /api/ontology/type-relations`, `DELETE` auf beide |
| `tests/test_ontology_store.py` (ändern) | Vorgaben anlegen und löschen, Verbindungen löschen |
| `tests/test_ontology_graph.py` (ändern) | Abbildung auf Schema-Attribut und Kante |
| `tests/test_ontology_api.py` (ändern) | Auth und CSRF der neuen Routen |

---

### Task 1: Vorgaben und Löschen in der Fixture-Quelle

**Files:**
- Modify: `src/ontology_store.py`
- Test: `tests/test_ontology_store.py`

**Interfaces:**
- Consumes: `OntologyStore._persist(mutate)`, `OntologyStore._prune`, bestehende Listen `_types`, `_relations`, `_entity_relations`
- Produces:
  - `OntologyStore.create_type_relation(src: str, predicate: str, dst: str) -> Optional[Dict]` → `{"src","predicate","dst","count"}` oder `None`
  - `OntologyStore.delete_type_relation(src: str, predicate: str, dst: str) -> bool`
  - `OntologyStore.delete_relation(src: str, predicate: str, dst: str) -> bool`

- [ ] **Step 1: Write the failing test**

In `tests/test_ontology_store.py` anhängen:

```python
def test_create_type_relation_is_a_declaration(tmp_path):
    """Vorgaben haben count 0 und bleiben ohne Instanzen bestehen."""
    path = _fixture(tmp_path, VALID)
    store = load_ontology(path)
    a, b = VALID["types"][0]["id"], VALID["types"][1]["id"]

    created = store.create_type_relation(a, "  hat   Dossier ", b)
    assert created == {"src": a, "predicate": "hat Dossier", "dst": b, "count": 0}
    # zweimal anlegen ergibt keinen zweiten Eintrag
    assert store.create_type_relation(a, "hat Dossier", b) == created
    assert store.create_type_relation(a, "", b) is None
    assert store.create_type_relation(a, "x", "gibt-es-nicht") is None
    assert store.create_type_relation(a, "x", a) is None       # Selbstbezug

    frisch = load_ontology(path)
    vorgaben = [r for r in frisch.summary()["relations"] if r["count"] == 0]
    assert {"src": a, "predicate": "hat Dossier", "dst": b, "count": 0} in vorgaben


def test_delete_type_relation_and_relation(tmp_path):
    path = _fixture(tmp_path, VALID)
    store = load_ontology(path)
    a, b = VALID["types"][0]["id"], VALID["types"][1]["id"]
    store.create_type_relation(a, "hat Dossier", b)

    assert store.delete_type_relation(a, "hat Dossier", b) is True
    assert store.delete_type_relation(a, "hat Dossier", b) is False
    assert all(r["predicate"] != "hat Dossier"
               for r in load_ontology(path).summary()["relations"])

    # Entitaetsverbindung loeschen
    e1 = VALID["entities"][0]["id"]
    e2 = store.create_entity("Partner AG", a)["id"]
    store.create_relation(e2, "arbeitet mit", e1)
    assert store.delete_relation(e2, "arbeitet mit", e1) is True
    assert store.delete_relation(e2, "arbeitet mit", e1) is False
    assert load_ontology(path).entity_detail(e2)["relations"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd KnovasPlatform/components/docbridge_integration && .venv/bin/python -m pytest tests/test_ontology_store.py::test_create_type_relation_is_a_declaration -v`
Expected: FAIL mit `AttributeError: 'OntologyStore' object has no attribute 'create_type_relation'`

- [ ] **Step 3: Write minimal implementation**

In `src/ontology_store.py` direkt nach `create_relation` einfügen:

```python
    def create_type_relation(self, src: str, predicate: str,
                             dst: str) -> Optional[Dict[str, Any]]:
        """Vorgabe auf Typebene: "Mandanten haben Dossiers".

        count bleibt 0 - daran erkennt die Oberflaeche eine Vorgabe. Sobald
        echte Verbindungen dieser Art entstehen, zaehlt die Verdichtung hoch.
        """
        predicate = " ".join(str(predicate or "").split())
        src, dst = str(src or "").strip(), str(dst or "").strip()
        known = {t["id"] for t in self._types}
        if not predicate or src == dst or src not in known or dst not in known:
            return None
        for existing in self._relations:
            if (existing["src"] == src and existing["dst"] == dst
                    and existing["predicate"] == predicate):
                return dict(existing)
        relation = {"src": src, "predicate": predicate, "dst": dst, "count": 0}
        self._relations.append(relation)
        self._persist(lambda raw: raw.setdefault("relations", []).append(dict(relation)))
        return dict(relation)

    def delete_type_relation(self, src: str, predicate: str, dst: str) -> bool:
        predicate = " ".join(str(predicate or "").split())
        vorher = len(self._relations)
        self._relations[:] = [
            r for r in self._relations
            if not (r["src"] == src and r["dst"] == dst
                    and r["predicate"] == predicate)]
        if len(self._relations) == vorher:
            return False
        self._persist(lambda raw: raw.__setitem__("relations", [
            r for r in raw.get("relations") or []
            if not (str(r.get("src")) == src and str(r.get("dst")) == dst
                    and str(r.get("predicate")) == predicate)]))
        return True

    def delete_relation(self, src: str, predicate: str, dst: str) -> bool:
        predicate = " ".join(str(predicate or "").split())
        vorher = len(self._entity_relations)
        self._entity_relations[:] = [
            r for r in self._entity_relations
            if not (r["src"] == src and r["dst"] == dst
                    and r["predicate"] == predicate)]
        if len(self._entity_relations) == vorher:
            return False
        self._persist(lambda raw: raw.__setitem__("entity_relations", [
            r for r in raw.get("entity_relations") or []
            if not (str(r.get("src")) == src and str(r.get("dst")) == dst
                    and str(r.get("predicate")) == predicate)]))
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ontology_store.py -v`
Expected: PASS, alle bisherigen Tests bleiben grün

- [ ] **Step 5: Commit**

```bash
git add KnovasPlatform/components/docbridge_integration/src/ontology_store.py \
        KnovasPlatform/components/docbridge_integration/tests/test_ontology_store.py
git commit -m "feat(cortex): Vorgaben auf Typebene und Loeschen von Verbindungen in der Fixture"
```

---

### Task 2: Dieselben Operationen gegen die Knovas API

**Files:**
- Modify: `src/knovas_client.py`, `src/ontology_graph.py`
- Test: `tests/test_ontology_graph.py`

**Interfaces:**
- Consumes: `KnovasAPIClient._graph_request`, `GraphOntologySource._invalidate`, `_type_id`, `_type_label`
- Produces:
  - `KnovasAPIClient.graph_create_schema_attribute(type_id: str, name: str, datatype: str = "entity_ref") -> Optional[Dict]`
  - `KnovasAPIClient.graph_delete_schema_attribute(type_id: str, attribute_id: str) -> Optional[Dict]`
  - `KnovasAPIClient.graph_delete_edge(edge_id: str) -> Optional[Dict]`
  - `GraphOntologySource.create_type_relation(src, predicate, dst) -> Optional[Dict]` (gleiche Form wie Task 1)
  - `GraphOntologySource.delete_type_relation(src, predicate, dst) -> bool`
  - `GraphOntologySource.delete_relation(src, predicate, dst) -> bool`

- [ ] **Step 1: Write the failing test**

In `tests/test_ontology_graph.py` anhängen:

```python
def test_type_relation_maps_to_schema_attribute():
    """Die API kennt keine Typ-Kante; eine Vorgabe wird ein Schema-Attribut."""
    client = FakeGraphClient()
    aufrufe = []
    client.graph_create_schema_attribute = lambda type_id, name, datatype="entity_ref": (
        aufrufe.append((type_id, name, datatype))
        or {"status": "success", "attribute": {"id": "a-1", "name": name}})
    source = GraphOntologySource(client)

    erstellt = source.create_type_relation("t-mandant", "hat Dossier", "t-dossier")
    assert erstellt == {"src": "t-mandant", "predicate": "hat Dossier",
                        "dst": "t-dossier", "count": 0}
    assert aufrufe == [("t-mandant", "hat Dossier", "entity_ref")]
    assert source.create_type_relation("t-mandant", "", "t-dossier") is None
    assert source.create_type_relation("t-x", "y", "t-x") is None


def test_delete_relation_removes_matching_edge():
    client = FakeGraphClient()
    geloescht = []
    client.graph_delete_edge = lambda edge_id: (geloescht.append(edge_id)
                                                or {"status": "success"})
    client.edges = [{"id": "e-1", "node_lo": "n-1", "node_hi": "n-2",
                     "relation": "hat_Dossier"}]
    source = GraphOntologySource(client)

    assert source.delete_relation("n-1", "hat_Dossier", "n-2") is True
    assert geloescht == ["e-1"]
    assert source.delete_relation("n-1", "gibt_es_nicht", "n-2") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ontology_graph.py::test_type_relation_maps_to_schema_attribute -v`
Expected: FAIL mit `AttributeError: 'GraphOntologySource' object has no attribute 'create_type_relation'`

- [ ] **Step 3: Write minimal implementation**

In `src/knovas_client.py` neben die anderen Graph-Methoden (nach `graph_create_edge`):

```python
    def graph_create_schema_attribute(self, type_id: str, name: str,
                                      datatype: str = 'entity_ref'
                                      ) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/node-types/<id>/schema - Attributdefinition.

        Fuer Vorgaben auf Typebene nutzen wir datatype entity_ref; laut
        Datentyp-Tabelle materialisiert der eine typisierte Kante. Der Body
        des Endpunkts ist in der Spezifikation nicht gezeigt, deshalb beim
        ersten Lauf gegen eine echte Instanz pruefen (Task 17).
        """
        return self._graph_request(
            'POST', f'/node-types/{quote(str(type_id), safe="")}/schema',
            data={'name': name, 'datatype': datatype})

    def graph_delete_schema_attribute(self, type_id: str,
                                      attribute_id: str) -> Optional[Dict[str, Any]]:
        """DELETE /secured/graph/node-types/<id>/schema/<aid>."""
        return self._graph_request(
            'DELETE',
            f'/node-types/{quote(str(type_id), safe="")}'
            f'/schema/{quote(str(attribute_id), safe="")}')

    def graph_delete_edge(self, edge_id: str) -> Optional[Dict[str, Any]]:
        """DELETE /secured/graph/edges/<id> - nur manuelle Kanten."""
        return self._graph_request(
            'DELETE', f'/edges/{quote(str(edge_id), safe="")}')
```

In `src/ontology_graph.py` nach `create_relation` einfügen:

```python
    def create_type_relation(self, src: str, predicate: str,
                             dst: str) -> Optional[Dict[str, Any]]:
        """Vorgabe auf Typebene. Die API kennt keine Kante zwischen Typen,
        deshalb ein Schema-Attribut vom Typ entity_ref auf dem Quelltyp."""
        predicate = " ".join(str(predicate or "").split())
        src, dst = str(src or "").strip(), str(dst or "").strip()
        if not predicate or not src or not dst or src == dst:
            return None
        if self._client.graph_create_schema_attribute(src, predicate) is None:
            return None
        self._invalidate()
        return {"src": src, "predicate": predicate, "dst": dst, "count": 0}

    def delete_type_relation(self, src: str, predicate: str, dst: str) -> bool:
        """Attribut anhand seines Namens auf dem Quelltyp suchen und loeschen."""
        predicate = " ".join(str(predicate or "").split())
        for node_type in self._export()["node_types"]:
            if _type_id(node_type) != str(src):
                continue
            for attribut in node_type.get("schema") or node_type.get("attributes") or []:
                if str(_first(attribut, "name", "label")) != predicate:
                    continue
                ok = self._client.graph_delete_schema_attribute(
                    src, str(_first(attribut, "id", "attribute_id"))) is not None
                if ok:
                    self._invalidate()
                return ok
        return False

    def delete_relation(self, src: str, predicate: str, dst: str) -> bool:
        """Passende Kante suchen und loeschen; Richtung beidseitig pruefen."""
        predicate = " ".join(str(predicate or "").split())
        for edge in self._export()["edges"]:
            enden = _edge_ends(edge)
            if enden is None:
                continue
            e_src, e_dst, e_pred = enden
            passt = (e_pred == predicate
                     and {e_src, e_dst} == {str(src), str(dst)})
            if not passt:
                continue
            edge_id = str(_first(edge, "id", "edge_id", "uuid"))
            if not edge_id:
                return False
            ok = self._client.graph_delete_edge(edge_id) is not None
            if ok:
                self._invalidate()
            return ok
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ontology_graph.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add KnovasPlatform/components/docbridge_integration/src/knovas_client.py \
        KnovasPlatform/components/docbridge_integration/src/ontology_graph.py \
        KnovasPlatform/components/docbridge_integration/tests/test_ontology_graph.py
git commit -m "feat(cortex): Vorgaben als Schema-Attribut, Kanten loeschen ueber die API"
```

---

### Task 3: Routen für Vorgaben und Löschen

**Files:**
- Modify: `src/web_interface/app.py` (bei den anderen Cortex-Routen, nach `ontology_relation_create` um Zeile 1777)
- Test: `tests/test_ontology_api.py`

**Interfaces:**
- Consumes: `_ontology_source()`, `_GENERIC_ERROR_MESSAGE`
- Produces:
  - `POST /api/ontology/type-relations` mit `{src, predicate, dst}` → `201 {success, relation}`
  - `DELETE /api/ontology/type-relations` mit `{src, predicate, dst}` → `200 {success}` / `404`
  - `DELETE /api/ontology/relations` mit `{src, predicate, dst}` → `200 {success}` / `404`

- [ ] **Step 1: Write the failing test**

In `tests/test_ontology_api.py` anhängen:

```python
def test_type_relation_routes_require_login_and_csrf(app):
    client = app.test_client()
    assert client.post("/api/ontology/type-relations", json={}).status_code == 401
    _login(client)
    ohne_token = client.post("/api/ontology/type-relations",
                             json={"src": "mandant", "predicate": "x",
                                   "dst": "dossier"})
    assert ohne_token.status_code == 403


def test_type_relation_create_and_delete(app):
    client = app.test_client()
    _login(client)
    headers = _csrf_header(client)

    angelegt = client.post("/api/ontology/type-relations",
                           json={"src": "mandant", "predicate": "hat Dossier",
                                 "dst": "dossier"}, headers=headers)
    assert angelegt.status_code == 201
    assert angelegt.get_json()["relation"]["count"] == 0

    # taucht als Vorgabe in der Zusammenfassung auf
    zusammenfassung = client.get("/api/ontology/summary").get_json()
    assert any(r["predicate"] == "hat Dossier" and r["count"] == 0
               for r in zusammenfassung["relations"])

    weg = client.delete("/api/ontology/type-relations",
                        json={"src": "mandant", "predicate": "hat Dossier",
                              "dst": "dossier"}, headers=headers)
    assert weg.status_code == 200
    nochmal = client.delete("/api/ontology/type-relations",
                            json={"src": "mandant", "predicate": "hat Dossier",
                                  "dst": "dossier"}, headers=headers)
    assert nochmal.status_code == 404


def test_relation_delete_route(app):
    client = app.test_client()
    _login(client)
    headers = _csrf_header(client)
    neu = client.post("/api/ontology/entities",
                      json={"type": "mandant", "label": "Partner AG"},
                      headers=headers).get_json()["entity"]
    client.post("/api/ontology/relations",
                json={"src": neu["id"], "predicate": "arbeitet mit",
                      "dst": "e-001"}, headers=headers)

    weg = client.delete("/api/ontology/relations",
                        json={"src": neu["id"], "predicate": "arbeitet mit",
                              "dst": "e-001"}, headers=headers)
    assert weg.status_code == 200
    detail = client.get(f"/api/ontology/entities/{neu['id']}").get_json()
    assert detail["relations"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ontology_api.py::test_type_relation_create_and_delete -v`
Expected: FAIL mit Status 405 (Methode nicht erlaubt) oder 404

- [ ] **Step 3: Write minimal implementation**

In `src/web_interface/app.py` direkt nach `ontology_relation_create` einfügen:

```python
    @app.route('/api/ontology/type-relations', methods=['POST'])
    def ontology_type_relation_create():
        """Vorgabe auf Typebene. Die API kennt keine Kante zwischen Typen,
        deshalb wird daraus ein Schema-Attribut (siehe Design 2026-08-08)."""
        try:
            payload = request.get_json(silent=True) or {}
            created = _ontology_source().create_type_relation(
                str(payload.get('src') or '').strip(),
                str(payload.get('predicate') or '').strip(),
                str(payload.get('dst') or '').strip())
            if created is None:
                return jsonify({'success': False,
                                'error': 'Vorgabe nicht anlegbar'}), 400
            return jsonify({'success': True, 'relation': created}), 201
        except Exception:
            logger.error("Ontology type relation create error", exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/ontology/type-relations', methods=['DELETE'])
    def ontology_type_relation_delete():
        try:
            payload = request.get_json(silent=True) or {}
            entfernt = _ontology_source().delete_type_relation(
                str(payload.get('src') or '').strip(),
                str(payload.get('predicate') or '').strip(),
                str(payload.get('dst') or '').strip())
            if not entfernt:
                return jsonify({'success': False, 'error': 'Vorgabe nicht gefunden'}), 404
            return jsonify({'success': True})
        except Exception:
            logger.error("Ontology type relation delete error", exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/ontology/relations', methods=['DELETE'])
    def ontology_relation_delete():
        try:
            payload = request.get_json(silent=True) or {}
            entfernt = _ontology_source().delete_relation(
                str(payload.get('src') or '').strip(),
                str(payload.get('predicate') or '').strip(),
                str(payload.get('dst') or '').strip())
            if not entfernt:
                return jsonify({'success': False,
                                'error': 'Verbindung nicht gefunden'}), 404
            return jsonify({'success': True})
        except Exception:
            logger.error("Ontology relation delete error", exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS, keine Regression

- [ ] **Step 5: Commit**

```bash
git add KnovasPlatform/components/docbridge_integration/src/web_interface/app.py \
        KnovasPlatform/components/docbridge_integration/tests/test_ontology_api.py
git commit -m "feat(cortex): Routen fuer Vorgaben und das Loeschen von Verbindungen"
```

---

### Task 4: Die Zieh-Geste als eigene Datei

**Files:**
- Create: `src/web_interface/static/js/ontology_connect.js`
- Modify: `src/web_interface/templates/ontology.html` (Skript einbinden, vor `ontology.js`)
- Modify: `src/web_interface/static/css/ontology.css` (Griff)

**Interfaces:**
- Consumes: eine Cytoscape-Instanz, `cssToken` aus `ontology.js` (global im selben Skriptkontext)
- Produces: globale Klasse `ConnectGesture` mit
  - `new ConnectGesture(cy, { onConnect })` — `onConnect({ srcId, dstId, ebene })`, `ebene` ist `'typ'` oder `'entitaet'`
  - `.destroy()` — Ereignisse und Griff entfernen

- [ ] **Step 1: Write the failing test**

Es gibt keine JS-Testinfrastruktur im Projekt (kein Node, kein Karma). Diese Aufgabe wird deshalb im Browser geprüft, nicht per Unit-Test. Prüfskript für die Konsole, das **vor** der Umsetzung fehlschlägt:

```js
// Erwartung: ConnectGesture existiert und meldet einen Zug
typeof ConnectGesture === 'function'
```

- [ ] **Step 2: Run check to verify it fails**

Seite `/ontology` laden, in der Konsole `typeof ConnectGesture` ausführen.
Expected: `"undefined"`

- [ ] **Step 3: Write minimal implementation**

`src/web_interface/static/js/ontology_connect.js` anlegen:

```js
// Zieh-Geste fuer Verbindungen im Cortex-Graphen.
// Kennt nur Cytoscape und meldet einen fertigen Zug per Rueckruf; was daraus
// entsteht, entscheidet CortexApp. Bewusst eigene Datei, weil ontology.js
// bereits gross ist.
'use strict';

/** Ebene eines Knotens: Typen tragen ein Symbol, Entitaeten die Klasse entity. */
function nodeEbene(node) {
    if (node.hasClass('entity')) return 'entitaet';
    if (node.hasClass('filter-node')) return null;      // Filter verbinden nicht
    return node.data('icon') ? 'typ' : null;
}

class ConnectGesture {
    constructor(cy, { onConnect }) {
        this.cy = cy;
        this.onConnect = onConnect;
        this.quelle = null;
        this.griff = null;
        this._bind();
    }

    _bind() {
        this._onOver = (evt) => this._zeigeGriff(evt.target);
        this._onOut = () => this._versteckeGriff();
        this._onPan = () => this._versteckeGriff();
        this.cy.on('mouseover', 'node', this._onOver);
        this.cy.on('mouseout', 'node', this._onOut);
        this.cy.on('pan zoom', this._onPan);
    }

    _griffElement() {
        if (!this.griff) {
            this.griff = document.createElement('button');
            this.griff.type = 'button';
            this.griff.className = 'connect-handle';
            this.griff.setAttribute('aria-label', 'Verbindung ziehen');
            this.griff.addEventListener('mousedown', (e) => this._start(e));
            this.cy.container().parentElement.appendChild(this.griff);
        }
        return this.griff;
    }

    _zeigeGriff(node) {
        if (this.quelle) return;                    // waehrend eines Zuges nicht
        if (!nodeEbene(node)) { this._versteckeGriff(); return; }
        const p = node.renderedPosition();
        const radius = (node.renderedWidth() / 2) + 6;
        const griff = this._griffElement();
        griff.dataset.nodeId = node.id();
        griff.style.left = `${p.x + radius}px`;
        griff.style.top = `${p.y - radius}px`;
        griff.hidden = false;
    }

    _versteckeGriff() {
        if (this.griff && !this.quelle) this.griff.hidden = true;
    }

    _start(event) {
        event.preventDefault();
        event.stopPropagation();
        const node = this.cy.getElementById(this.griff.dataset.nodeId);
        if (node.empty()) return;
        this.quelle = node;
        this.ebene = nodeEbene(node);
        this.cy.userPanningEnabled(false);
        this.cy.boxSelectionEnabled(false);

        // Vorschaulinie ueber einen unsichtbaren Zielknoten
        const p = node.position();
        this.zeiger = this.cy.add({
            group: 'nodes', classes: 'connect-pointer',
            data: { id: '__connect_pointer__' }, position: { x: p.x, y: p.y },
        });
        this.zeiger.ungrabify();
        this.vorschau = this.cy.add({
            group: 'edges', classes: 'connect-preview',
            data: { id: '__connect_preview__', source: node.id(),
                    target: '__connect_pointer__' },
        });

        this._onMove = (e) => this._bewege(e);
        this._onUp = (e) => this._beende(e);
        window.addEventListener('mousemove', this._onMove);
        window.addEventListener('mouseup', this._onUp, { once: true });
    }

    _modellPunkt(event) {
        const box = this.cy.container().getBoundingClientRect();
        const zoom = this.cy.zoom();
        const pan = this.cy.pan();
        return { x: (event.clientX - box.left - pan.x) / zoom,
                 y: (event.clientY - box.top - pan.y) / zoom };
    }

    _zielUnter(event) {
        const box = this.cy.container().getBoundingClientRect();
        const punkt = { x: event.clientX - box.left, y: event.clientY - box.top };
        let treffer = null;
        this.cy.nodes().forEach((n) => {
            if (n.id() === this.quelle.id() || n.id() === '__connect_pointer__') return;
            if (nodeEbene(n) !== this.ebene) return;
            const r = n.renderedPosition();
            const radius = n.renderedWidth() / 2;
            const d = Math.hypot(r.x - punkt.x, r.y - punkt.y);
            if (d <= radius) treffer = n;
        });
        return treffer;
    }

    _bewege(event) {
        if (!this.quelle) return;
        this.zeiger.position(this._modellPunkt(event));
        const ziel = this._zielUnter(event);
        this.cy.nodes('.connect-target').removeClass('connect-target');
        if (ziel) ziel.addClass('connect-target');
    }

    _beende(event) {
        window.removeEventListener('mousemove', this._onMove);
        const ziel = this._zielUnter(event);
        const quelle = this.quelle;
        const ebene = this.ebene;
        this._aufraeumen();
        if (ziel && this.onConnect) {
            this.onConnect({ srcId: quelle.id(), dstId: ziel.id(), ebene });
        }
    }

    _aufraeumen() {
        this.cy.nodes('.connect-target').removeClass('connect-target');
        if (this.vorschau) { this.vorschau.remove(); this.vorschau = null; }
        if (this.zeiger) { this.zeiger.remove(); this.zeiger = null; }
        this.quelle = null;
        this.ebene = null;
        this.cy.userPanningEnabled(true);
        this.cy.boxSelectionEnabled(true);
        this._versteckeGriff();
    }

    destroy() {
        this.cy.removeListener('mouseover', 'node', this._onOver);
        this.cy.removeListener('mouseout', 'node', this._onOut);
        this.cy.removeListener('pan zoom', this._onPan);
        this._aufraeumen();
        if (this.griff) { this.griff.remove(); this.griff = null; }
    }
}
```

In `src/web_interface/templates/ontology.html` das Skript **vor** `ontology.js` einbinden:

```html
    <script src="{{ url_for('static', filename='js/ontology_connect.js') }}?v={{ asset_version }}"></script>
```

In `src/web_interface/static/css/ontology.css` anhängen:

```css
/* Griff zum Verbinden: erscheint beim Zeigen auf einen Knoten. Absolut
   ueber dem Canvas, damit er unabhaengig von Cytoscape klickbar bleibt. */
.connect-handle {
    position: absolute;
    z-index: 2;
    width: 16px;
    height: 16px;
    margin: -8px 0 0 -8px;
    padding: 0;
    border: 2px solid var(--card-bg);
    border-radius: 50%;
    background: var(--accent);
    cursor: crosshair;
    transition: transform .12s ease;
}
.connect-handle:hover { transform: scale(1.25); }
.connect-handle:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
.connect-handle[hidden] { display: none; }
```

- [ ] **Step 4: Run check to verify it passes**

Seite neu laden, in der Konsole:

```js
typeof ConnectGesture === 'function'   // true
```

Danach `.venv/bin/python -m pytest tests/ -q` — muss weiterhin grün sein (keine Serveränderung).

- [ ] **Step 5: Commit**

```bash
git add KnovasPlatform/components/docbridge_integration/src/web_interface/static/js/ontology_connect.js \
        KnovasPlatform/components/docbridge_integration/src/web_interface/templates/ontology.html \
        KnovasPlatform/components/docbridge_integration/src/web_interface/static/css/ontology.css
git commit -m "feat(cortex): Zieh-Geste fuer Verbindungen als eigene Datei"
```

---

### Task 5: Geste verdrahten, Namensabfrage, Linie zeichnen

**Files:**
- Modify: `src/web_interface/static/js/ontology.js`
- Modify: `src/web_interface/static/css/ontology.css`

**Interfaces:**
- Consumes: `ConnectGesture` aus Task 4, `postJson`, `deleteJson`, `CortexApp.esc`, `askDelete`
- Produces:
  - `CortexApp.onConnectDrawn({srcId, dstId, ebene})` — fragt den Namen ab und legt an
  - `CortexApp.addRelationToGraph(relation, ebene)` — zeichnet die Linie ohne Neuaufbau

- [ ] **Step 1: Write the failing check**

Browserkonsole nach dem Laden:

```js
typeof window.cortexApp.onConnectDrawn === 'function'
```

- [ ] **Step 2: Run check to verify it fails**

Expected: `false`

- [ ] **Step 3: Write minimal implementation**

In `src/web_interface/static/js/ontology.js`:

**(a)** Kantenstile ergänzen — in das Stylesheet-Array nach dem Block `{ selector: 'edge', style: {...} }`:

```js
                // Vorgabe: gestrichelt und ohne Zahl. Bleibt sichtbar, solange
                // keine echte Verbindung dieser Art existiert.
                { selector: 'edge.declared', style: {
                    'line-style': 'dashed',
                    'width': 1.5,
                    'line-color': cssToken('--callout'),
                    'target-arrow-color': cssToken('--callout'),
                } },
                { selector: 'edge.connect-preview', style: {
                    'line-style': 'dashed',
                    'line-color': cssToken('--primary-color'),
                    'target-arrow-shape': 'none',
                    'width': 2,
                    'label': '',
                } },
                { selector: 'node.connect-pointer', style: {
                    'width': 1, 'height': 1, 'opacity': 0, 'label': '',
                } },
                { selector: 'node.connect-target', style: {
                    'border-color': cssToken('--primary-color'),
                    'border-width': 4,
                } },
```

**(b)** Im Kantenaufbau in `renderGraph` die Vorgaben markieren — den bestehenden `edges`-Ausdruck ersetzen:

```js
        const edges = data.relations.map((r, i) => ({
            data: { id: `r-${i}`, source: r.src, target: r.dst,
                    src: r.src, dst: r.dst, predicate: r.predicate,
                    label: r.count ? `${r.predicate} (${formatCount(r.count)})`
                                   : r.predicate,
                    width: r.count ? 1.5 + 3 * (r.count / maxRel) : 1.5 },
            classes: r.count ? '' : 'declared',
        }));
```

**(c)** Geste starten — am Ende von `renderGraph`, nach den `tap`-Bindungen:

```js
        if (this.connect) this.connect.destroy();
        this.connect = new ConnectGesture(this.cy, {
            onConnect: (zug) => this.onConnectDrawn(zug),
        });
```

**(d)** Neue Methoden — vor `static filterStateText(f)` einfügen:

```js
    /** Nach einem Zug den Namen erfragen und die Verbindung anlegen. */
    onConnectDrawn({ srcId, dstId, ebene }) {
        const esc = CortexApp.esc;
        const vorschlaege = [...new Set(
            this.cy.edges().map((e) => e.data('predicate')).filter(Boolean))];
        this.openEntityDrawer();
        this.setDrawerDelete(null);
        document.getElementById('entityPaneTitle').textContent = 'Neue Verbindung';
        const body = document.getElementById('entityPaneBody');
        const label = (id) => esc(this.cy.getElementById(id).data('label') || id);
        body.innerHTML = `
            <div class="entity-detail">
                <p class="entity-hint">${label(srcId)} zu ${label(dstId)}</p>
                <div class="create-row">
                    <input type="text" id="connectInput" maxlength="80" list="connectVorschlaege"
                           placeholder="Beziehung, z. B. hat Dossier"
                           aria-label="Art der Beziehung">
                    <datalist id="connectVorschlaege">${vorschlaege
                        .map((v) => `<option value="${esc(v)}"></option>`).join('')}</datalist>
                    <button type="button" id="connectSubmit" class="btn btn-primary">Verbinden</button>
                </div>
                <p class="ontology-empty" id="connectFehler" hidden></p>
            </div>`;
        const eingabe = document.getElementById('connectInput');
        const senden = () => this.onConnectSubmit(srcId, dstId, ebene, eingabe.value);
        document.getElementById('connectSubmit').addEventListener('click', senden);
        eingabe.addEventListener('keydown', (evt) => {
            if (evt.key === 'Enter') { evt.preventDefault(); senden(); }
            if (evt.key === 'Escape') this.closeDrawers();
        });
        eingabe.focus({ preventScroll: true });
    }

    async onConnectSubmit(srcId, dstId, ebene, predicate) {
        predicate = String(predicate || '').trim();
        const fehler = document.getElementById('connectFehler');
        if (!predicate) { document.getElementById('connectInput').focus({ preventScroll: true }); return; }
        const url = ebene === 'typ' ? '/api/ontology/type-relations'
                                    : '/api/ontology/relations';
        // Satelliten tragen die Entitaets-Id im Datenfeld, nicht in der Knoten-Id.
        const kennung = (id) => {
            const n = this.cy.getElementById(id);
            return n.data('entityId') || id;
        };
        try {
            const data = await this.postJson(url, {
                src: kennung(srcId), predicate, dst: kennung(dstId) });
            if (!data) return;
            this.addRelationToGraph(srcId, dstId, predicate, ebene);
            this.closeDrawers();
        } catch (err) {
            console.error('Cortex: Verbindung nicht anlegbar', err);
            if (fehler) {
                fehler.textContent = 'Verbindung konnte nicht angelegt werden.';
                fehler.hidden = false;
            }
        }
    }

    /** Linie einfuegen, ohne den Graphen neu aufzubauen. */
    addRelationToGraph(srcId, dstId, predicate, ebene) {
        const id = `neu:${srcId}:${predicate}:${dstId}`;
        if (this.cy.getElementById(id).nonempty()) return;
        this.cy.add({
            group: 'edges',
            classes: ebene === 'typ' ? 'declared' : 'entity-edge',
            data: { id, source: srcId, target: dstId, src: srcId, dst: dstId,
                    predicate, label: ebene === 'typ' ? predicate : '', width: 1.5 },
        });
    }
```

**(e)** In `ontology.css` anhängen:

```css
.connect-hint { color: var(--text-secondary); font-size: 0.85rem; margin-bottom: 8px; }
```

- [ ] **Step 4: Run check to verify it passes**

Browser: Seite laden, auf einen Typ zeigen, Griff erscheint, auf einen anderen Typ ziehen, Name eingeben, Eingabetaste. Die Linie erscheint gestrichelt. Danach:

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add KnovasPlatform/components/docbridge_integration/src/web_interface/static/js/ontology.js \
        KnovasPlatform/components/docbridge_integration/src/web_interface/static/css/ontology.css
git commit -m "feat(cortex): gezogene Verbindungen anlegen und als Linie zeichnen"
```

---

### Task 6: Linie anklicken und löschen

**Files:**
- Modify: `src/web_interface/static/js/ontology.js`

**Interfaces:**
- Consumes: `askDelete`, `deleteJson`, Kantendaten `src`, `dst`, `predicate` aus Task 5
- Produces: `CortexApp.onEdgeSelect(edge)`

- [ ] **Step 1: Write the failing check**

Browserkonsole:

```js
typeof window.cortexApp.onEdgeSelect === 'function'
```

- [ ] **Step 2: Run check to verify it fails**

Expected: `false`

- [ ] **Step 3: Write minimal implementation**

In `renderGraph` bei den `tap`-Bindungen ergänzen (nach der Knotenbindung):

```js
        this.cy.on('tap', 'edge', (evt) => {
            const kante = evt.target;
            if (kante.hasClass('entity-edge') && !kante.data('predicate')) return;
            this.onEdgeSelect(kante);
        });
```

Neue Methode vor `static filterStateText(f)`:

```js
    /** Eine Linie zeigen und zum Loeschen anbieten. */
    onEdgeSelect(edge) {
        const esc = CortexApp.esc;
        const predicate = edge.data('predicate') || '';
        const vorgabe = edge.hasClass('declared');
        const quelle = this.cy.getElementById(edge.data('src'));
        const ziel = this.cy.getElementById(edge.data('dst'));
        this.openEntityDrawer();
        this.setDrawerDelete(null);
        document.getElementById('entityPaneTitle').textContent =
            vorgabe ? 'Vorgabe' : 'Verbindung';
        document.getElementById('entityPaneBody').innerHTML = `
            <div class="entity-detail">
                <h3>${esc(predicate)}</h3>
                <p class="entity-hint">${esc(quelle.data('label') || '')}
                   zu ${esc(ziel.data('label') || '')}</p>
                <p class="confirm-detail">${vorgabe
                    ? 'Eine Vorgabe beschreibt, was vorgesehen ist. Sie bleibt sichtbar, solange keine Verbindung dieser Art besteht.'
                    : 'Eine gezogene Verbindung zwischen zwei Entitäten.'}</p>
                <div class="confirm-actions">
                    <button type="button" id="edgeDelete" class="btn btn-danger">Löschen</button>
                </div>
            </div>`;
        document.getElementById('edgeDelete').addEventListener('click', () => {
            this.askDelete({
                title: `${predicate} löschen?`,
                detail: vorgabe
                    ? 'Die Vorgabe wird entfernt. Bestehende Verbindungen bleiben.'
                    : 'Die Verbindung zwischen den beiden Entitäten wird entfernt.',
                onConfirm: () => this.onEdgeDelete(edge, vorgabe),
                onCancel: () => this.onEdgeSelect(edge),
            });
        });
    }

    async onEdgeDelete(edge, vorgabe) {
        const url = vorgabe ? '/api/ontology/type-relations'
                            : '/api/ontology/relations';
        const kennung = (id) => {
            const n = this.cy.getElementById(id);
            return n.data('entityId') || id;
        };
        try {
            const resp = await fetch(url, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json',
                           'X-CSRF-Token': csrfToken() },
                body: JSON.stringify({ src: kennung(edge.data('src')),
                                       predicate: edge.data('predicate'),
                                       dst: kennung(edge.data('dst')) }),
            });
            if (resp.status === 401) { window.location.assign('/login'); return; }
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            edge.remove();
            this.closeDrawers();
        } catch (err) {
            console.error('Cortex: Verbindung nicht löschbar', err);
        }
    }
```

- [ ] **Step 4: Run check to verify it passes**

Browser: eine gezogene Linie anklicken, Löschen bestätigen, Linie verschwindet. Neu laden: sie bleibt weg.

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add KnovasPlatform/components/docbridge_integration/src/web_interface/static/js/ontology.js
git commit -m "feat(cortex): Linie anklicken und loeschen"
```

---

### Task 7: Formular im Drawer entfernen und Abnahme

**Files:**
- Modify: `src/web_interface/static/js/ontology.js` (Block `connect-form` in `onEntitySelect`)
- Modify: `docs/superpowers/specs/2026-08-04-wissensnetz-ontology-mvp-design.md` (Abschnitt zur Bedienung)

**Interfaces:**
- Consumes: nichts Neues
- Produces: nichts Neues

- [ ] **Step 1: Formular entfernen**

Der Block `<div class="connect-form">…</div>` in `onEntitySelect` sowie `fillRelationTargets`, `onRelationCreate` und ihre Bindungen entfallen — das Ziehen ersetzt sie. Die Methoden ersatzlos löschen und prüfen, dass `grep -c "connect-form\|fillRelationTargets\|onRelationCreate" src/web_interface/static/js/ontology.js` **0** ergibt.

- [ ] **Step 2: Abnahmeliste im Browser durchgehen**

Jeder Punkt einzeln prüfen und abhaken:

- [ ] Zeigen auf einen Typ zeigt den Griff, Wegzeigen blendet ihn aus
- [ ] Zug von Typ zu Typ hebt nur Typen hervor, nicht die Satelliten
- [ ] Zug von Satellit zu Satellit hebt nur Satelliten hervor
- [ ] Zug ins Leere legt nichts an und hinterlässt keine Vorschaulinie
- [ ] Angelegte Vorgabe erscheint gestrichelt ohne Zahl
- [ ] Nach dem Neuladen ist die Vorgabe noch da
- [ ] Klick auf eine Linie öffnet den Drawer, Löschen entfernt sie dauerhaft
- [ ] Verschieben eines Knotens funktioniert weiterhin (Zug am Knoten, nicht am Griff)
- [ ] Der Graph verschiebt sich beim Ziehen nicht (Pan bleibt aus während des Zuges)

- [ ] **Step 3: Volle Testsuite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add KnovasPlatform/components/docbridge_integration/src/web_interface/static/js/ontology.js \
        docs/superpowers/specs/2026-08-04-wissensnetz-ontology-mvp-design.md
git commit -m "refactor(cortex): Verbindungsformular entfaellt zugunsten der Zieh-Geste"
```

---

## Selbstprüfung des Plans

**Abdeckung der Spec:** Zwei Bedeutungen am Strich (Task 5b, 5a) · Geste mit Griff (Task 4) · Ebene folgt dem Knotentyp (Task 4 `nodeEbene`, `_zielUnter`) · Benennung mit Vorschlägen (Task 5d) · Vorgabe bleibt ohne Instanzen sichtbar (Task 1, `count: 0`) · Griff bei Typen und Entitäten (Task 4) · Datenwege beide Modi (Tasks 1 und 2) · Routen (Task 3) · Löschen (Task 6) · Fehlerbehandlung: Zug ins Leere (Task 4 `_beende`), Anlegen scheitert (Task 5 `onConnectSubmit` mit Hinweis, Linie wird erst nach Erfolg gezeichnet), doppelte Verbindung (Task 1 gibt den bestehenden Eintrag zurück).

**Platzhalter:** keine — jeder Schritt enthält vollständigen Code oder eine konkrete Prüfhandlung.

**Typkonsistenz:** `create_type_relation` liefert in beiden Quellen `{"src","predicate","dst","count"}`. `ConnectGesture` meldet `{srcId, dstId, ebene}` mit `ebene ∈ {'typ','entitaet'}`; Task 5 und 6 verwenden genau diese Namen. `kennung()` löst Satelliten-Knoten-Ids (`ent:<id>`) über `entityId` auf, passend zu `renderEntityNodes`.

**Bekannte Grenze:** Für JavaScript gibt es im Projekt keine Testinfrastruktur; die Tasks 4 bis 7 werden im Browser geprüft. Die Zieh-Geste selbst lässt sich in einem verdeckten Tab nicht automatisiert auslösen (Chrome hält `requestAnimationFrame` an), die Abnahme in Task 7 ist deshalb manuell.
