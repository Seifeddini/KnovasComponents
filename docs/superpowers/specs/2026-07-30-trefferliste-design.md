# Design: Trefferliste — kompakte Karten, Orientierung, Politur

Datum: 2026-07-30
Betrifft: `KnovasPlatform/components/docbridge_integration/src/web_interface`
Vorgänger: `2026-07-26-preview-feedback-branding-design.md`

## Ausgangslage

Nachdem der Klick auf einen Treffer das Dokument direkt im Modal öffnet und die
Karten entschachtelt wurden, wirkt die Trefferliste leer. Die Ursache ist nicht
fehlende Information — ein Treffer trägt deutlich mehr, als die Karte zeigt:

| Feld | Beispiel | heute sichtbar |
| --- | --- | --- |
| `title` | Mustervertrag Kaufvertrag Immobilie | ja |
| `document_date` / `modified_at` | 2024-03-15 / 2026-07-26 | ja, beide |
| `context_snippet` | before / match / after | ja |
| `first_page_preview` | Text der ersten Seite | ja |
| `type` | Vertrag | **nein** |
| `akten_id` | 2024-001 | **nein** |
| `path` | corpus/2024-001/Mustervertrag.pdf | **nein** |
| `top_chunks` | 2 Fundstellen | **nein** |
| `score` | 0.91 | **nein** |
| `file_size` | 1817 | **nein** |

Beim Entschachteln sind die Rahmen entfallen, ohne dass die Struktur
typografisch ersetzt wurde. Gleichzeitig steht auf der Karte Redundantes
(zwei Textblöcke) und fehlt Identifizierendes (Dokumentart, Akte).

## Nutzungsziel

Festgelegt: **Nutzer wollen das Dokument lesen.** Die Vorschau ist das Ziel.

Daraus folgt die Aufgabe der Karte: sie muss beim **Auswählen** helfen und dann
aus dem Weg gehen. Sie ist keine verkleinerte Dokumentansicht. Alles, was nicht
zur Frage „ist das der richtige Treffer?" beiträgt, kostet nur Platz.

Nicht Teil dieses Vorhabens, bewusst: Relevanzsignale (Fundstellen-Anzahl,
Ranking-Stärke) sowie Sortierung und Filter. Beides wurde erwogen und
zurückgestellt.

## 1. Kompakte Karte

```
┌────────────────────────────────────────────────────────────┐
│ ┌────┐  VERTRAG · PDF · 15.03.2024              [Öffnen]   │
│ │≡≡≡ │  Mustervertrag                                      │
│ │≡≡  │  … Der Kaufpreis in Höhe von EUR 485.000,00 ist     │
│ └────┘  bis zum Übergabetermin zu bezahlen …               │
└────────────────────────────────────────────────────────────┘
```

**Aufbau:** Vorschaubild links (80 px breit, feste Höhe), rechts daneben eine
Metazeile, darunter Titel und genau ein Textausschnitt.

**Metazeile** aus `type` (Grossbuchstaben, Guide-Farbe), Formatkürzel aus der
Dateiendung, und `document_date`. `modified_at` entfällt von der Karte — das
Änderungsdatum der Datei sagt beim Auswählen nichts; es gehört, wenn überhaupt,
ins Modal. Fehlt `type`, entfällt der erste Teil ersatzlos statt einer
Platzhalterbezeichnung.

**Nur ein Textausschnitt.** Der Trefferkontext gewinnt; „Erste Seite" entfällt,
sobald ein `context_snippet` vorliegt. Begründung: der Anfang eines Vertrags ist
bei jedem Vertrag derselbe Formeltext — er unterscheidet Treffer nicht und
kostet die halbe Karte. Nur wenn kein Trefferkontext existiert, tritt
`first_page_preview` an seine Stelle, und wenn auch der fehlt,
`ingested_summary`. Genau eine Textquelle, in dieser Rangfolge.

**Vorschaubild nur bei PDF.** Für DOCX, TXT und MSG gibt es ohne Konverter keine
renderbare Seite. Statt die Karten dort anders aussehen zu lassen, steht im
gleich grossen Rahmen ein Lucide-Icon je Format: `file-text` für DOCX und TXT,
`mail` für MSG. Die Karten bleiben dadurch in einer Flucht.

**Ergebnis:** Kartenhöhe sinkt von aktuell rund 250 px auf etwa 130 px. Statt
zwei passen vier bis fünf Treffer ins Sichtfeld.

## 2. Orientierung

### 2.1 Die Anfrage bleibt sichtbar

Die Ergebnisüberschrift lautet künftig `Suchergebnisse für „<query>"` statt
`Suchergebnisse`. Nach dem Scrollen und beim Verfeinern ist das die wichtigste
Information auf der Seite, und sie fehlt heute vollständig.

### 2.2 Gruppierung nach Akte

Treffer werden nach `akten_id` gruppiert, mit einer Zwischenüberschrift pro
Akte und der Anzahl darin.

**Gruppiert wird nur bei mindestens zwei verschiedenen `akten_id`.** Vier
Treffer aus einer einzigen Akte unter eine einzige Zwischenüberschrift zu
stellen wäre reines Rauschen — dann bleibt die Liste flach. Diese Bedingung ist
nicht optional; ohne sie verschlechtert die Gruppierung den häufigsten Fall.

Treffer ohne `akten_id` sammeln sich am Ende unter „Ohne Aktenbezug". Die
Reihenfolge der Gruppen ist die des **ersten Auftretens** in der Antwort: die
Akte, deren bestplatzierter Treffer am weitesten oben steht, kommt zuerst.
Innerhalb einer Gruppe bleibt die API-Reihenfolge unverändert — wir sortieren
nicht um, weil wir die Ranking-Logik nicht kennen.

Die Demo-Fixtures decken beide Fälle bereits ab: die Akten `2024-001`,
`2024-050`, `2023-088` und `2024-010` kommen darin vor, je nach Suchbegriff
also eine oder mehrere Gruppen.

### 2.3 Woher `akten_id` kommt — und wann es fehlt

**Die Knovas-API liefert kein `akten_id`.** `/secured/query` gibt Pointer, Scores
und Seitenangaben zurück, sonst nichts. Die Aktennummer entsteht ausschliesslich
durch lokale Anreicherung:

```
RemoteController schreibt   .search_enrichment.jsonl
        ↓  Pfad aus SEARCH_ENRICHMENT_PATH (Standard /mnt/autodoc/…)
_load_search_enrichment()   liest sie, gecacht nach mtime
        ↓
_lookup_enrichment_meta()   ordnet über doc_id / Pfad / Dateinamen zu
        ↓
app.py                      if meta.get("akten_id"): result["akten_id"] = …
```

Im Demo-Modus (`SEARCH_USE_TEST_RESULTS=true`) kommt der Wert stattdessen aus den
hartkodierten Fixtures in `app.py` — die Datei existiert dort gar nicht.

**Folge, die bewusst in Kauf genommen wird:** bei einer Installation ohne
konfigurierte Anreicherung tragen die Treffer kein `akten_id`. Die Gruppierung
erscheint dann **nie**, ohne Fehler und ohne Hinweis. Das ist insofern gutartig,
als die Bedingung „nur ab zwei verschiedenen Akten" ohnehin greift und die Liste
schlicht flach bleibt — wer die Gruppierung aber erwartet, bekommt keinen
Anhaltspunkt, warum sie ausbleibt.

Eine mögliche Abhilfe, nicht Teil dieses Vorhabens: der Systemstatus könnte
ausweisen, ob die Anreicherung geladen wurde. Das Feld
`onedrive_enrichment_loaded` steht in der Suchantwort bereits zur Verfügung.

## 3. Politur

### 3.1 Skelett statt Spinner

Der Ladezustand zeigt drei Skelett-Karten in der Form der echten Karten
(Bildfläche links, drei Textzeilen rechts) statt eines zentrierten Spinners.
Zweck ist nicht Dekoration: das Layout springt heute beim Eintreffen der
Ergebnisse, weil der Spinner eine andere Höhe hat als die Liste.

Die vorhandene `.preview-skeleton`-Shimmer-Animation wird wiederverwendet,
inklusive ihrer `prefers-reduced-motion`-Abschaltung.

### 3.2 Leerzustand

Statt „Versuchen Sie es mit anderen Suchbegriffen" zitiert der Leerzustand die
Anfrage und nennt konkrete nächste Schritte: kürzeren Begriff wählen,
Schreibweise prüfen, Ober- statt Unterbegriff. Er nennt ausserdem, dass die
Suche den Inhalt der Dokumente durchsucht und nicht nur Dateinamen — eine
Erwartung, die Nutzer regelmässig falsch haben.

### 3.3 „Mehr laden" statt Limit-Dropdown

Das Dropdown `Ergebnisse: 10/20/50/100` verschwindet. Darunter tritt ein
`Mehr laden`-Knopf unter der Liste, sichtbar nur, wenn die Antwort genau
`limit` Treffer enthielt (also plausibel mehr existieren).

**Bekannte Einschränkung, ausdrücklich festgehalten:** die Knovas-API kennt
kein `offset` (`POST /secured/query` nimmt nur `Input`). „Mehr laden" kann
deshalb nicht nachladen, sondern stellt dieselbe Anfrage mit erhöhtem `limit`
und ersetzt die Liste. Für den Nutzer sieht das aus wie Nachladen; technisch
ist es eine zweite vollständige Suche. Bei langsamer API ist das spürbar.

Der Zustand `limit` wandert dafür aus dem DOM in die Instanz
(`this._searchLimit`, Startwert aus `web.search.results_per_page`), und jeder
Klick verdoppelt ihn bis zu einer Obergrenze von 100 — dem bisherigen Maximum
des Dropdowns.

Die Scrollposition bleibt beim Nachladen erhalten: die Liste wird ersetzt, nicht
neu aufgebaut von oben.

## 4. Was sich im Code ändert

| Datei | Änderung |
| --- | --- |
| `static/js/app.js` | `createDocumentCard` neu aufgebaut; Gruppierung in `displayResults`; `_searchLimit` statt `resultsPerPage`; Skelett- und Leerzustand |
| `templates/index.html` | Limit-Dropdown raus, „Mehr laden"-Knopf rein, Ergebnisüberschrift mit Query-Platzhalter |
| `static/css/style.css` | Kartenlayout zweispaltig, Metazeile, Gruppenüberschriften, Skelett-Karten |

Keine Serveränderung. Alle benötigten Felder liefert `/api/search` bereits.

## 5. Testing

- **Gruppierung:** drei Fälle — alle Treffer eine Akte (flach), zwei Akten
  (gruppiert), Treffer ohne `akten_id` (eigene Gruppe am Ende)
- **Textquelle:** Karte mit Trefferkontext zeigt genau diesen; ohne ihn die
  erste Seite; ohne beides die Zusammenfassung; ohne alles keinen Textblock
- **Formate:** PDF zeigt das gerenderte Bild, DOCX/TXT das `file-text`-Icon,
  MSG das `mail`-Icon, alle im gleich grossen Rahmen
- **Mehr laden:** Knopf erscheint nur bei voller Trefferzahl, verdoppelt das
  Limit, verschwindet bei 100 oder wenn weniger als `limit` zurückkam
- **Browser:** keine JS-Fehler, Karte kompakter als die 250 px vor dem Umbau

**Nachtrag 2026-07-30:** die ursprüngliche Vorgabe „unter 150 px, mindestens vier
Karten im Sichtfeld" wurde bei der Umsetzung überstimmt. Das Vorschaubild bei
80 px liess nichts erkennen; erst ab 200 px Breite wird die Überschrift einer
Seite lesbar. Die Karte liegt damit bei 202 px und zwei Karten im Sichtfeld —
etwa die Dichte vor dem Umbau, nun aber mit lesbarer Vorschau und einem statt
zwei Textblöcken. Bewusster Tausch, kein Verfehlen der Vorgabe.

## 6. Offene Punkte

Unverändert aus `docs/search-ui-backlog.md`: Relevanzsignale, Sortierung und
Filter, Caching, eigener PDF-Viewer. Sortierung und Filter bleiben ausserdem
durch die API begrenzt — clientseitig ginge nur, was ohnehin geladen ist.
