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
- **UI-Label (sichtbar):** „Cortex" (Marketing: „Knovas Cortex") — catchy
  SaaS-Anglizismus, trägt die Gehirn-Vision; Subheadline deutsch:
  „Ihr Wissen, vernetzt." Entschieden am 2026-08-06, ersetzt das erste
  Label „Wissensnetz" (dem User nicht catchy genug). Weiterhin tabu im UI:
  „Cloud" (DSG-Konnotation bei Kanzleien) und „Ontologie" (verkauft
  Technologie statt Befund).
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
    { "predicate": "hat_Dossier", "direction": "out",
      "target": { "id": "e-014", "label": "Dossier 2024-001", "type": "dossier" } }
  ],
  "evidence": [
    { "document": { "path": "AutoDoc/corpus/2024-001/Mustervertrag.pdf",
                    "title": "Mustervertrag" },
      "page": 3,
      "quote": "…zwischen der Müller Bau AG…" }
  ]
}

// entity_detail("e-014") — dieselbe Relation aus Sicht des Ziels: target zeigt
// zurück auf die QUELLE, direction kennzeichnet die Blickrichtung (v1.1)
{
  "relations": [
    { "predicate": "hat_Dossier", "direction": "in",
      "target": { "id": "e-001", "label": "Müller Bau AG", "type": "mandant" } }
  ]
}
```

Regeln:

- `relations` in `entity_detail` sind bidirektional: jede Zeile trägt
  `direction: "out" | "in"`. Bei `"in"` ist `target` die QUELL-Entität der
  Relation (nicht das übliche Ziel) — so bleibt z. B. "Dossier 2024-001 | ←
  hat_Dossier | Müller Bau AG" lesbar, auch wenn die Entität nur als `dst`
  vorkommt.
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
  zu dieser Entität erfasst").

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

## Filter (Automatic Knowledge Filtering) — Erweiterung 2026-08-06

Req-Doc 2.2 („junior associate that never sleeps"): Der Kunde beschreibt in
Alltagssprache einen Filter auf einer Entität; passende Passagen aus den
Dokumenten der Entität landen als prüfbare Vorschläge in einem eigenen
Unter-Knoten. Ablehnung ist **permanent** (Rejection-Memory).

**Echt, nicht gemockt** — Entscheid nach interner Deadline-Klärung:

- **`src/ontology_filters.py`** (Flask-frei): extrahiert Text der
  Entitäts-Dokumente per pymupdf (Cache per mtime), segmentiert satzweise,
  scored lexikalisch (Token-Normalisierung, Umlaut-Folding, Präfix-Matching
  für Komposita: „Kündigungsklauseln" trifft „Kündigungsfrist"). Später
  ersetzt der echte Knovas-Endpunkt nur das Matching — Vertrag bleibt.
- **Dokumente einer Entität** = Evidence-Dokumente der Entität plus der
  direkt verbundenen Entitäten (1 Hop; deckt „the matter's documents" ab).
- **Persistenz** `ONTOLOGY_FILTER_STATE_PATH` (JSON): Filter +
  Entscheidungen, gekeyt per Fingerprint sha1(pfad|seite|normalisiertes
  Zitat) — stabil über Re-Uploads/Re-Runs. Rejected ist endgültig
  (nie wieder „zur Prüfung", auch nach Neustart).
- **Vertrag v1.2:** `entity_detail.filters[{id,label,status,counts}]`;
  `GET /api/ontology/filters/<id>` (Vorschläge mit quote/page/score/state/
  document); `POST /api/ontology/filters` {entity_id,label};
  `POST /api/ontology/filters/<id>/decision` {proposal_id, action}.
  POSTs hinter bestehendem CSRF-Header-Gate (X-CSRF-Token), Auth wie alle.
- **UX:** Filter-Bereich im Entitäts-Detail mit Dreischritt-Explainer
  (Beschreiben → Knovas liest laufend → Sie prüfen); Filter-Unter-Knoten
  am Entitäts-Satelliten (Trichter-Icon, Badge = zur Prüfung); Prüf-Panel
  mit Reitern Zur Prüfung/Übernommen/Abgelehnt, Karten im Beleg-Stil mit
  Zuversicht-Chip und Übernehmen/Ablehnen; beim Ablehnen inline:
  „Verstanden — wird nie wieder vorgeschlagen." Kein Treffer → ehrlicher
  Sammel-Zustand („Knovas liest den Aktenbestand laufend …").
- **Nicht enthalten:** semantisches Routing (kommt vom Backend),
  Score-Kalibrierung (Gate R), Mehrbenutzer-Rollen.

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
