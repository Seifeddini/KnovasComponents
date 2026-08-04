# Wissensnetz (Ontology Explorer) — MVP-Design

**Datum:** 2026-08-04 · **Ziel-Demo:** Sonntag, 2026-08-09 · **Heute-Ziel:** klickbarer localhost-MVP

## Zweck

Die im Knovas-Backend vorhandene Ontologie sichtbar machen: welche Typen und
Entitäten in den Dokumenten eines Korpus stecken, wie sie zusammenhängen, und
— entscheidend — **aus welchem Dokument jeder Zusammenhang belegt ist**.
Demo-Fläche zuerst, Produkt später. Nicht Teil dieses Vorhabens: die Suche
ersetzen, Extraktion bauen, Backend ändern.

## Namensgebung und Vision

- **Vision (Pitch-Ebene):** Knovas agiert als **Gehirn** der Kanzlei — es
  liest die Dokumente und versteht ihre Struktur. Das Wissensnetz ist der
  Blick in dieses Gehirn: „Knovas hat Ihre 4'200 Dokumente gelesen — das
  Wissensnetz zeigt, was es verstanden hat." Die Gehirn-Metapher trägt die
  Demo-Erzählung, erscheint aber nicht als UI-Beschriftung.
- **UI-Label (sichtbar):** „Wissensnetz" — deutsch, kein Jargon, bewusst
  nicht „Cloud" (DSG-Konnotation bei Kanzleien) und nicht „Ontologie"
  (verkauft Technologie statt Befund).
- **Code/Routen (stabil, englisch):** `ontology` — Route `/ontology`,
  `/api/ontology/…`, `ontology_store.py`, `ontology.js`, `ontology.css`,
  `ontology.html`. Deckungsgleich mit dem später erwarteten
  Backend-Endpunkt.

## Entscheidungen aus dem Brainstorming

| Frage | Entscheid |
|---|---|
| Zweck | Demo zuerst, Produkt später |
| Termin | Demo Sonntag 2026-08-09; localhost-MVP **heute** |
| Korpus | Kuratierter Testkorpus, muss diese Woche entstehen (paralleler Strang) |
| Datenquelle | Ontologie existiert im Knovas-Backend; Client-API hat (noch) keinen Endpunkt → **Mock hinter sauberem Datenvertrag**, echter Endpunkt später |
| Klicktiefe | Bis ins echte Dokument (PDF öffnet auf der belegten Seite) |
| Frontend-Ansatz | **A: neuer Tab im bestehenden Vanilla-Stack** (docbridge_integration Web-UI), vendored Cytoscape, kein Build-Schritt. React-Migration nach der Demo, gegen dieselben Verträge |
| CI | Knovas Corporate Identity zwingend: ausschliesslich bestehende Design-Tokens aus `style.css`, keine neuen Farben, IBM Plex Mono (Headings) / Sans (Body), bestehendes Logo, deutsche UI-Texte, Schweizer Zahlenformat (`1'847`) |

## Architektur

Alles innerhalb `KnovasPlatform/components/docbridge_integration`:

```
Browser ── GET /ontology ─────────────► Flask: templates/ontology.html
        ── GET /api/ontology/summary ─► ontology_store (Fixture-JSON)
        ── GET /api/ontology/entities?type=…
        ── GET /api/ontology/entities/<id>
        ── GET /api/document/<id>/preview?path=…#page=N   (existiert bereits)
```

- **`src/ontology_store.py`** — lädt und validiert die Fixture. Flask-frei
  (Muster: `preview.py`), damit isoliert testbar. Cached per mtime.
- **Fixture** `docbridge_test_data/ontology/ontology_fixture.json` — von Hand
  gepflegter Mock. `document.path`-Werte müssen auf real vorhandene PDFs
  unterhalb der Autodoc-Roots zeigen (derselbe Allowlist-Mechanismus wie
  Suchtreffer, `_resolve_autodoc_path`).
- **Neue Flask-Routen** in `app.py`, hinter `require_company_login` wie alle
  bestehenden; nur Registrierung dort, Logik im Store.
- **Kein neuer Service, keine DB.** Der spätere echte Datenweg ersetzt nur
  das Innere des Stores (Fixture → Knovas-API-Call), der Vertrag bleibt.

## Datenvertrag (v1)

Der Vertrag ist die Migrationsversicherung: Frontend und späteres Backend
programmieren beide gegen diese Shapes.

```jsonc
// GET /api/ontology/summary — Typ-Ebene (der Graph, ~10–25 Knoten)
{
  "types": [
    { "id": "mandant", "label": "Mandant", "count": 12 }
  ],
  "relations": [
    { "src": "mandant", "predicate": "hat_Dossier", "dst": "dossier", "count": 47 }
  ]
}

// GET /api/ontology/entities?type=<typeId> — Instanzliste eines Typs
{
  "entities": [
    { "id": "e-001", "label": "Müller Bau AG", "type": "mandant", "doc_count": 8 }
  ]
}

// GET /api/ontology/entities/<id> — Detail: Relationen + Belege
{
  "entity":    { "id": "e-001", "label": "Müller Bau AG", "type": "mandant" },
  "relations": [
    { "predicate": "hat_Dossier",
      "target": { "id": "e-014", "label": "Dossier 2024-001", "type": "dossier" } }
  ],
  "evidence": [
    { "document": { "path": "AutoDoc/corpus/2024-001/Mustervertrag.pdf",
                    "title": "Mustervertrag" },
      "page": 3,
      "quote": "…zwischen der Müller Bau AG…" }
  ]
}
```

Regeln:

- Fehlerformat und Auth wie bestehende `/api/*`-Routen (JSON, generische
  Fehlermeldung, kein Stacktrace nach aussen).
- `count`/`doc_count` sind ganze Zahlen; Anzeige immer formatiert (`1'847`).
- Optionale Felder (z. B. `confidence`) kommen erst, wenn das Backend sie
  liefert — der Vertrag wird erweitert, nicht geraten.

## UI

Route `/ontology`, Link „Wissensnetz" im bestehenden Site-Header. Drei
Spalten (CSS-Grid), kein Modal — der Graph bleibt immer sichtbar:

```
┌────────────┬──────────────────┬────────────────────┐
│ Graph      │ Entitäten (Typ)  │ Dokument           │
│ Cytoscape  │ → Detail         │ iframe auf         │
│ vendored   │ → Belegliste     │ /preview#page=N    │
└────────────┴──────────────────┴────────────────────┘
   Typ ────────► Entität ────────► Beleg ────► Seite
```

- **Graph:** Cytoscape.js als vendored UMD (`static/js/vendor/`), Layout
  `concentric` (deterministisch, keine Zusatz-Dependency; fcose ist
  Feinschliff nach dem MVP). Knotengrösse ∝ `count`, Kantenbreite ∝
  Relations-`count`, Kantenlabel `hat_Dossier (47)`.
- **Farben nur aus Tokens:** Knoten `--surface-sunken` mit Rand `--accent`,
  Selektion `--primary-color`, Labels `--text-primary`, Kanten
  `--border-color` / selektiert `--accent`. Keine neuen Hexwerte.
- **Zoom-Navigation (Pflicht, MVP):** sichtbare Controls am Graph-Pane —
  „+", „−" und „Ansicht einpassen" (fit) — als Button-Gruppe im bestehenden
  `btn`-Stil, zusätzlich Scrollrad/Pinch (Cytoscape-Standard). Doppelklick
  auf freie Fläche = fit. Zoom-Grenzen setzen (`minZoom`/`maxZoom`), damit
  niemand sich „verliert".
- **Anspruch „fertiges Produkt":** keine Platzhalter-Optik — Hover- und
  Fokuszustände auf allen Interaktiv-Elementen, ruhige Übergänge,
  konsistente Abstände nach bestehendem Raster, sauberer leerer Zustand je
  Pane. Alles ausschliesslich mit vorhandenen Tokens/Komponentenmustern
  (Buttons, Karten, Chips) der bestehenden UI.
- **Mittlere Spalte:** schlichte HTML-Tabelle (Label, Belegzahl), darunter
  bei Auswahl Detail + Belegliste (Zitat, Dokumenttitel, Seite).
- **Rechte Spalte:** leerer Zustand mit Hinweistext („Beleg wählen, um die
  Fundstelle zu sehen"); bei Belegklick iframe auf den bestehenden
  Preview-Endpunkt mit `#page=N` (browsernativer PDF-Viewer).
- **Empty-States sind Befunde, keine leeren Flächen** (z. B. „Keine Belege
  über dem Schwellenwert").

## Fehlerbehandlung

- Store validiert beim Laden: Referenz-Integrität (`relations.src/dst`
  existieren als Typ, Evidence-Pfade existieren auf Platte). Verletzungen →
  Log-Warnung + Eintrag gefiltert, nie 500.
- Panes degradieren einzeln: PDF-Ladefehler zeigt Fehlertext in der rechten
  Spalte, Graph und Listen bleiben stehen.
- Fetch-Fehler im Frontend: Hinweis im betroffenen Pane, Retry durch erneuten
  Klick; in-flight Requests werden bei neuem Klick abgebrochen
  (`AbortController`).

## Tests

`tests/test_ontology_store.py` + Route-Tests nach Hausmuster
(`test_enrichment_lookup.py` als Vorlage):

1. Vertrag-Shape der drei Endpunkte (Fixture → JSON)
2. Filterung kaputter Referenzen (fehlender Pfad, unbekannter Typ) ohne 500
3. Auth: alle `/api/ontology/*`-Routen und `/ontology` verlangen Login
4. Zahlformatierung `1'847` (JS-seitig trivial, ein Unit-Test im Store für
   die Datenintegrität genügt serverseitig)

## Nicht im MVP (bewusst)

Ego-Graph pro Entität · Merge-UI / Entity-Resolution-Korrektur ·
bbox-Highlighting im PDF (Extraktor liefert heute keine Wort-Geometrie;
Seiten-Genauigkeit reicht für die Demo) · echter Backend-Endpunkt ·
React/Vite-Migration (nach der Demo, gegen dieselben Verträge) ·
Confidence-Anzeige.

## Wochenplan bis zur Demo

| Tag | Strang |
|---|---|
| Di (heute) | MVP localhost: Routen + Store + Fixture (klein) + drei Spalten + Klickpfad bis ins PDF |
| Mi–Do | Korpus-Strang: realistische Test-PDFs; Fixture ausbauen (~15 Typen, ~50 Entitäten) |
| Fr | Feinschliff: Layout, Empty/Error-States, Formatierung, ggf. fcose |
| Sa | Demo-Probelauf im Docker-Stack, Fixes |

## Migrationspfad (nach der Demo)

`knovas-app` (Vite + React + TS) übernimmt `/ontology` als erste Route.
Wiederverwendet werden: Datenvertrag, `ontology_store.py`, Fixture,
Preview-Kette, Design-Tokens (als Tailwind-Theme). Ersetzt werden:
`ontology.html` und `ontology.js`. Der Store tauscht sein Inneres gegen den
echten `/secured/ontology`-Call, sobald er existiert.
