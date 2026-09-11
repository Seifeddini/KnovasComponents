# Trefferliste und Dokumentvorschau — Umsetzungsplan

Datum: 2026-09-11
Betrifft: `KnovasPlatform/components/docbridge_integration/src/web_interface` (Platform);
optional `RemoteController/src/sync` und `docker-compose.yml`
Vorgänger: `docs/superpowers/specs/2026-07-30-trefferliste-design.md`,
`docs/superpowers/specs/2026-07-26-preview-feedback-branding-design.md`,
`docs/search-ui-backlog.md`
Entwurf der Dokumentvorschau: Artefakt «Dokumentvorschau» vom 2026-09-07
(<https://claude.ai/code/artifact/b5e597b8-57cf-453a-b721-a8366c1e91db>)
Pflichtenheft-Bezug: F7 «Sprung zur Fundstelle» (§6.4, geplant), H4 «Tabellen
überleben bis in die Vorschau» (§6.3)

Status: **angebotsreif.** Umfang, Abnahmekriterien, Aufwand und Optionen sind
so gefasst, dass sie einem Kunden vorgelegt werden können. Tagessätze und
Preise trägt der Vertrieb ein (Abschnitt 10).

---

## 0 · Auf einen Blick

Drei Ergebnisse, die der Nutzer sieht:

| | Ergebnis | Heute | Nachher |
| --- | --- | --- | --- |
| **A** | **Vorschaubild für jedes Format** — links auf jeder Trefferkarte die erste Seite des Dokuments | nur PDF; DOCX, E-Mail, TXT zeigen ein Icon in einem leeren Kasten | PDF, gescanntes PDF, DOCX, E-Mail (MSG/EML), TXT und MD zeigen eine gerenderte Seite; E-Mails einen «Umschlag» mit Von/An/Betreff |
| **B** | **Trefferkarten, die je Format das Richtige sagen** | eine Metazeile für alles; E-Mails ohne Absender; keine Angabe, wie oft der Begriff im Dokument vorkommt; Liste nur per Tab bedienbar | Dokumentart · Format · Datum · Akte; bei E-Mails Absender und Betreff; Fundstellen-Zähler; Pfeiltasten in der Liste |
| **C** | **Dokumentvorschau nach dem Entwurf vom 7.9.** — Seite als Seite, Fundstellen-Leiste, Sprung zur Fundstelle | PDF im browsereigenen Viewer (fremde Optik, kein Sprung zur Fundstelle); übrige Formate als Fliesstext; EML und MD: gar keine Vorschau (HTTP 415) | ein Viewer in Knovas-Optik für alle Formate; Fundstellen als Liste mit Seite und Kontext; «Fundstelle 2 von 7» springt und markiert; Aktionen dort, wo man sie sucht |

Dazu, weil ein Kunde danach fragt: ein Cache, damit es auch auf einem
SMB-Share schnell ist; eine Berechtigungsprüfung auf den Vorschau-Endpunkten;
Tests je Format; Dokumentation; ein Demo-Drehbuch.

**Aufwand Basispaket: 31 Personentage (PT) netto, 36 PT mit Reserve.**
Sieben Wochen mit einer Entwicklerin, vier Wochen mit zwei.
**Optionen:** Vorwärmen im RemoteController (2 PT), Render-Dienst für
layouttreue Office-Seiten (6–8 PT), durchsuchbare Textebene für Scans (3 PT).

---

## 1 · Ausgangslage

### 1.1 Was der Nutzer heute sieht

Gemessen am Demo-Kanzlei-Korpus (rund 5 300 Dateien: PDF, gescannte PDF,
DOCX, EML, MSG, TXT, MD — `docs/superpowers/specs/2026-09-06-demo-kanzlei-korpus-design.md` §5.1):

| Format | Anteil im Korpus | Trefferkarte heute | Vorschau heute |
| --- | --- | --- | --- |
| PDF | gross | gerenderte erste Seite (PyMuPDF, 480 px) | browsereigener PDF-Viewer im `<iframe>`; dunkle Leiste, eigene Knöpfe, in jedem Browser anders; kein Sprung zur Fundstelle |
| PDF, gescannt (OCR) | ≈ 420 Dateien | gerenderte erste Seite | wie PDF; OCR-Text liegt nicht im PDF, also nichts markierbar |
| DOCX | gross | Icon `file-text` im 260×224-Kasten | Fliesstext aus Markdown, ohne Seitenbild, ohne Seitenzahlen |
| MSG | ≈ 1 300 E-Mails (EML + MSG) | Icon `mail` | Kopfzeilen Von/An, darunter der Text |
| EML | | Icon `file-text` | **keine Vorschau** — `preview-content` kennt nur `.pdf/.docx/.txt/.msg` und antwortet 415; der Dialog zeigt «Vorschau nicht verfügbar (HTTP 415)» |
| TXT | | Icon `file-text` | Fliesstext |
| MD | | Icon `file-text` | **keine Vorschau** (415), wie EML |
| XLSX / PPTX | nicht indexiert | — | — (Pflichtenheft F2, nicht Teil dieses Plans) |

Bei einer Kanzlei sind E-Mails 40–50 % des Bestands. Dass EML heute gar
keine Vorschau hat, ist deshalb kein Randfall, sondern der häufigste
Fehlerfall der Demo.

### 1.2 Woran es liegt

- `preview.py::PREVIEW_KIND_BY_SUFFIX` hat vier Einträge; `knovas_extract`
  kann längst auch EML (`message/rfc822`) und MD.
- `render_first_page_png` rendert nur PDF. Für die übrigen Formate «gäbe es
  ohne Konverter keine Seite» — das war die bewusste Entscheidung vom
  2026-07-30. Sie hält nicht mehr, sobald die Karte das Hauptwerkzeug zum
  Auswählen ist: ein leerer Kasten mit Icon hilft beim Auswählen nicht.
- Das Vorschaubild wird **bei jeder Anfrage neu gerendert**, auch wenn der
  Browser ein passendes ETag mitschickt: `document_thumbnail` rendert zuerst
  und ruft `make_conditional` erst danach auf. Bei 20 Treffern pro Suche sind
  das 20 PDF-Renderings pro Suche und Nutzer, sobald die fünf Minuten
  `max-age` abgelaufen sind.
- Der Dialog zeigt die Fundstellen nicht. Die Suchantwort trägt je Treffer
  `top_chunks[]` mit `page_number`/`sentence_number`, und der Kontext-Sidecar
  (`.search_context`, vom RemoteController geschrieben) hat den Text jedes
  Satzes samt Seite. Beides liegt vor; es wird nur der erste Treffer zu einem
  `context_snippet` verarbeitet.
- Die Datei-Endpunkte (`/preview`, `/thumbnail`, `/preview-content`,
  `/download`, `/client-path`) prüfen nur die Anmeldung. Wer einen Pfad kennt,
  bekommt die Vorschau — auch für ein Dokument, das die Suche ihm wegen einer
  Zugriffsgruppe nie geliefert hätte. Mit der Verwaltung von Zugriffsgruppen
  (`KnovasPlatform/docs/features/document-administration.md`) ist das eine
  Lücke, die ein Kunde mit Ethical Walls finden wird.

### 1.3 Worauf wir aufbauen

Vorhanden und getestet:

- **Kontext-Sidecar je Dokument** (`context_store.py`): alle Sätze mit Index
  `i`, Text `t` und Seite `p` (bei PDF), dazu der Text der ersten Seite.
  Geschrieben vom RemoteController beim Indexieren, gelesen von der Platform
  über das gemeinsame Volume `rc-state` (`/var/rc-state/search_context`).
- **Trefferorte von Knovas**: `page_number`, `sentence_number` und
  `top_chunks[]` mit `cosine_similarity` je Fundstelle.
- **PyMuPDF 1.28** (bereits Pflichtabhängigkeit) — rendert PDF-Seiten und
  bringt mit `Story` einen HTML/CSS-Layouter mit, der Text ohne Browser und
  ohne LibreOffice in eine Seite setzt.
- **knovas-extract** mit `pdf, docx, msg, html, markdown` — liefert
  sanitisiertes Markdown für DOCX, TXT, MD, MSG und EML.
- **Natives `<dialog>`**, Lucide-Icons inline, IBM Plex selbst gehostet,
  Brand-Tokens in `style.css`, `markdown.js` mit Escape-zuerst-Regel.
- **Der Entwurf vom 7.9.** — Kopfzeile, Werkzeugleiste, Seitenansicht,
  Fundstellen-Leiste, Dokumentdaten, Hervorhebung in zwei Lautstärken. Sein
  CSS ist die Vorgabe für C1.

---

## 2 · Zielbild

Festgelegt bleibt der Nutzungsgrundsatz vom 2026-07-30: **Nutzer wollen das
Dokument lesen.** Die Karte hilft beim Auswählen, der Dialog beim Lesen.

### 2.1 Trefferkarte

```
┌──────────────────────────────────────────────────────────────────────┐
│ ┌──────────┐  VERTRAG · PDF · 15.03.2024 · AKTE 2024-017              │
│ │ ▔▔▔▔▔▔   │  Mietvertrag Schaffhauserstrasse 12                      │
│ │ ▔▔▔▔▔▔▔  │  … Die Kündigungsfrist beträgt drei Monate auf Ende     │
│ │ ▔▔▔▔     │  eines Quartals …                        3 Fundstellen   │
│ └──────────┘                                                         │
└──────────────────────────────────────────────────────────────────────┘
```

Was das Vorschaubild je Format zeigt — alle im gleich grossen Rahmen, damit
die Karten in einer Flucht bleiben:

| Format | Vorschaubild | Wie es entsteht |
| --- | --- | --- |
| PDF, Scan | erste Seite als Bild (wie heute), neu in 2× für HiDPI, aus dem Cache | PyMuPDF `get_pixmap` |
| DOCX, TXT, MD | **typografische Seite**: Titel und die ersten Absätze des extrahierten Texts, in IBM Plex gesetzt, auf einem Blatt | Markdown → eigenes HTML aus escaptem Text → PyMuPDF `Story` → PNG |
| MSG, EML | **Umschlag**: Kopfblock Von · An · Betreff · Datum, darunter die ersten Zeilen des Texts | wie oben, mit Mail-Vorlage |
| jedes Format bei Fehler | Format-Icon mit Kürzel und Grund als Tooltip («verschlüsselt», «zu gross», «beschädigt») | Fehlerklassen aus `PreviewFailed` |

In der Ecke jedes Vorschaubilds ein kleines Format-Kürzel (PDF · DOCX ·
E-MAIL · TXT). Bei Scans zusätzlich «OCR», damit klar ist, warum sich später
im Viewer nichts markieren lässt.

**Metazeile** je Format: Dokumente `TYP · FORMAT · DATUM · AKTE`; E-Mails
`E-MAIL · von <Absender> · DATUM`. Titel bei E-Mails ist der Betreff.
**Fundstellen-Zähler** («3 Fundstellen») rechts unten, sobald
`top_chunks` mehr als einen Eintrag hat — die Zahl ist der Grund, das Dokument
zu öffnen, und der Dialog löst sie ein. Relevanz-Scores bleiben weiterhin
verborgen (Entscheidung vom 2026-07-30 unverändert).

**Tastatur**: ↑/↓ wandern durch die Karten, Enter öffnet, Escape schliesst,
der Fokus kehrt zur Karte zurück. Trefferzahl per `aria-live`.

**Mobil** (< 640 px): Vorschaubild als 96-px-Kachel links, Text daneben; die
Karte bleibt eine Zeile pro Treffer hoch.

### 2.2 Dokumentvorschau

Aufbau exakt nach dem Entwurf vom 7.9.:

```
┌─ Kopfzeile ─────────────────────────────────────────────────────────┐
│ [PDF]  Mietvertrag Schaffhauserstrasse 12         Dokument 2 von 18 │
│        Akte 2024-017 · 15.03.2024 · 8 Seiten · 1,2 MB     ‹ › ✕     │
├─ Werkzeugleiste ────────────────────────────────────────────────────┤
│ (Öffnen) (In OneDrive öffnen) (Pfad kopieren)   Fundstelle 2 von 3 ‹ › │
├─ Seitenansicht ──────────────────────────┬─ Fundstellen-Leiste ─────┤
│  ┌──────────────────────────────┐        │ FUNDSTELLEN · 3          │
│  │ Mietvertrag      Seite 3 von 8│        │ ▸ Seite 1  … Mietzins …  │
│  │                              │        │ ▸ Seite 3 · aktiv        │
│  │ § 7 Kündigung                │        │   … Kündigungsfrist …    │
│  │ ▌Die Kündigungsfrist beträgt │        │ ▸ Seite 7  … Frist …     │
│  │ ▌drei Monate auf Ende …      │        │                          │
│  │                              │        │ DOKUMENTDATEN            │
│  └──────────────────────────────┘        │ Akte      2024-017       │
│                                          │ Quelle    AutoDoc-Share  │
│                                          │ Geändert  15.03.2024     │
└──────────────────────────────────────────┴──────────────────────────┘
```

Verhalten:

- Öffnet auf der **besten Fundstelle** (`page_number`/`sentence_number` des
  Treffers), nicht auf Seite 1. Die aktive Fundstelle bekommt Fläche
  (Grayish Blue) und eine Azure-Kante; weitere Fundstellen auf derselben Seite
  nur den leisen Ton.
- **Fundstelle ‹ ›** springt zur nächsten Stelle, auch über Seiten hinweg;
  die Leiste scrollt mit, der Eintrag wird aktiv. Klick auf einen Eintrag in
  der Leiste tut dasselbe.
- **Dokument ‹ ›** blättert durch die Treffer der Liste wie heute (Pfeiltasten
  ↑/↓); Fundstellen mit ←/→. Escape schliesst.
- **Vorladen**: beim Öffnen werden Vorschau-Inhalt und erstes Seitenbild des
  nächsten und vorherigen Treffers im Hintergrund geholt; laufende Anfragen
  werden bei Wechsel abgebrochen (`AbortController`, wie heute).
- **Zustände**: Skelett während des Ladens, Fehlerzustand mit den Aktionen
  «Öffnen» und «Pfad kopieren», Hinweis bei deaktivierter PDF-Vorschau.
- **Mobil** (< 900 px): Vollbild, die Fundstellen-Leiste liegt als Reiter
  unter der Seitenansicht.
- **Deep-Link**: `/?doc=<pointer>&hit=2` öffnet den Dialog direkt — nötig für
  den «Warum?»-Pfad aus dem Cortex (Pflichtenheft G3) und für Links in
  Notizen.

Seitenansicht je Format:

| Format | Ansicht im Dialog | Sprung zur Fundstelle |
| --- | --- | --- |
| PDF | **pdf.js**, selbst gehostet; Seiten als Blätter auf Ice Blue, Zoom «Seitenbreite / ganze Seite», Seitennavigation, «Seite 3 von 8» | Seite aus `page_number`; der Satz wird über die Textebene gesucht und markiert |
| PDF, Scan | wie PDF, Seitenbild | nur Seite; die Leiste zeigt den OCR-Text der Fundstelle, weil das PDF keine Textebene hat (Option E3 ändert das) |
| DOCX, TXT, MD | **typografische Seitenansicht**: Markdown als Blatt gesetzt, Überschriften, Listen, Tabellen (H4) | Satz aus dem Sidecar wird im gerenderten Text gefunden und markiert; kein «Seite x von y», weil die Extraktion keine Seiten kennt (Option E2 ändert das für DOCX) |
| MSG, EML | Kopfblock Von · An · CC · Datum · Betreff, darunter der Text; Anhänge als Liste, sofern der Extraktor ihre Namen liefert | wie Textformate |

### 2.3 Fundstellen — das Datenmodell

Die Suchantwort trägt je Treffer neu:

```json
"hits": [
  {"page": 3, "sentence": 41, "score": 0.91,
   "before": "…", "match": "Die Kündigungsfrist beträgt drei Monate …", "after": "…"}
],
"hit_count": 3,
"preview_kind": "pdf",
"thumbnail": true
```

Quelle: `top_chunks[]` × Kontext-Sidecar, Radius 1 Satz, höchstens 10
Fundstellen, nach Seite und Satz dedupliziert, in der Reihenfolge der API.
`context_snippet` (Radius 10) bleibt für die Karte bestehen. Fehlt der
Sidecar, fehlt `hits` — die Leiste zeigt dann «Fundstellen werden nach dem
nächsten Indexlauf angezeigt» statt zu schweigen.

Sobald Knovas `chunk_uuid` und `snippet` je Treffer liefert (Pflichtenheft
§5.3.1), ersetzt das den Sidecar-Text; der Client merkt es nicht.

---

## 3 · Technischer Entwurf

### 3.1 Vorschau-Pipeline

```
Datei auf dem Share ──► Renderer ──► Cache (Pfad + mtime + Grösse + Breite) ──► Trefferkarte / Dialog
                         │
                         ├─ pdf   : PyMuPDF get_pixmap, Seite 1, Breite 520 px (2× für 260 px)
                         ├─ page  : Markdown → escaptes HTML → PyMuPDF Story → Pixmap
                         └─ mail  : Kopfblock-Vorlage + Text → Story → Pixmap
```

Neues Modul `web_interface/preview_render.py` (kein Flask-Wissen, isoliert
testbar, wie `preview.py`):

- `preview_kind(path)` wächst auf `pdf | docx | txt | md | msg | eml`; der
  Server entscheidet die Art und schreibt sie als `preview_kind` in die
  Suchantwort, damit der Client nicht mehr an der Endung rät.
- `render_thumbnail(path, kind, width) -> bytes` mit einem Renderer je Art.
  Der `Story`-Renderer baut sein HTML **ausschliesslich aus escaptem Text**;
  Markup aus Dokumenten erreicht ihn nie. Schriften: IBM Plex als TTF für den
  Server (`web_interface/preview_fonts/`, OFL, ~300 kB), über
  `pymupdf.Archive` eingebunden.
- Grenzen wie heute (`MAX_INPUT_BYTES` 25 MB, `MAX_TEXT_BYTES` 2 MB), dazu
  ein Zeitbudget pro Rendering (`web.preview.render_timeout_s`, Vorgabe 8 s).
  Überschreitung → 422 mit Grund, Karte zeigt das Icon. Ein Rendering blockiert
  nie die Suche: die Karte lädt das Bild nach, die Suchantwort wartet nicht.

**Cache** auf dem bestehenden Volume `docbridge_integration_data`
(`/app/data/preview-cache/`):

- Schlüssel `sha256(relativer Pfad)-<mtime_ns>-<size>-w<width>.png` für
  Bilder, `…-content.json` für extrahiertes Markdown samt `meta`.
- `If-None-Match` wird **vor** dem Rendern geprüft (behebt 1.2); Treffer
  liefern 304 ohne Dateizugriff auf dem Share.
- Obergrenze `web.preview.cache_max_mb` (Vorgabe 2 048), Verdrängung nach
  Zugriffszeit, Sweep beim Start und stündlich in einem Hintergrund-Thread.
- `/api/stats` weist Trefferquote, Grösse und mittlere Renderzeit aus, damit
  die Zahlen aus dem Backlog (DOCX 103–387 ms, alles ungecacht) nach der
  Einführung auf dem Kundensystem gemessen werden können — die Bedingung aus
  `docs/search-ui-backlog.md` §2 bleibt: erst messen, dann verbreitern.

### 3.2 Schnittstellen

| Endpunkt | Änderung |
| --- | --- |
| `POST /api/search` | je Treffer neu `hits[]`, `hit_count`, `preview_kind`, `thumbnail` (bool), `preview_token` (siehe 3.4) |
| `GET /api/document/<id>/thumbnail?path=&w=260\|520` | alle Arten statt nur PDF; 200 PNG / 304 / 400 / 404 / 415 / 422; ETag vor Rendern; `Cache-Control: private, max-age=3600` |
| `GET /api/document/<id>/preview-content?path=` | Arten `eml` und `md` dazu; `meta` trägt bei Mails `msg:cc`, `msg:date`, `msg:attachments[]`, sofern vorhanden; Antwort aus dem Cache |
| `GET /api/document/<id>/preview?path=` | unverändert (PDF-Bytes); pdf.js lädt von hier, `Range` wird bereits beantwortet |
| `GET /api/stats` | Kennzahlen des Vorschau-Caches |

Konfiguration (`config.yaml`, `web.preview.*`, alles mit Vorgaben):
`cache_dir`, `cache_max_mb`, `thumbnail_widths`, `render_timeout_s`,
`kinds` (Allowlist), `require_token` (3.4). Keine neue Umgebungsvariable für
den Basisbetrieb; die Optionen aus 3.5–3.7 bringen ihre eigenen.

### 3.3 Client

| Datei | Änderung |
| --- | --- |
| `static/js/app.js` | Karte nach `preview_kind`; Vorschaubild für alle Arten mit `srcset` 1×/2×; Fundstellen-Zähler; Tastaturnavigation in der Liste; `onclick`-Attribute durch `data-`-Attribute ersetzt (Backlog §4) |
| `static/js/viewer.js` (neu) | Dialog-Controller: Zustand (Dokumentindex, Fundstellenindex), Lader je Art, Vorladen, Abbruch, Tastatur, Deep-Link |
| `static/js/vendor/pdfjs/` (neu) | `pdf.mjs`, `pdf.worker.mjs`, `LICENSE`; gepinnte Version; kein CDN, kein Build-Schritt — wie die Schriften |
| `static/js/markdown.js` | Pipe-Tabellen (H4); Hilfsfunktion, die einen Satz im gerenderten DOM findet und als `<mark>` auszeichnet |
| `templates/index.html` | Dialog-Markup nach Entwurf: Kopfzeile, Werkzeugleiste, Seitenansicht + Leiste, Dokumentdaten |
| `static/css/style.css` | Blatt, Leiste, Fundstellen-Zustände, Format-Kürzel, Karte je Art — die Tokens sind vorhanden, das CSS des Entwurfs ist die Vorlage |

Der Viewer bleibt **ohne Build-Schritt und ohne npm**; pdf.js ist die einzige
Fremdbibliothek und wird als zwei Dateien mitgeliefert (rund 1 MB, wird nur
beim ersten PDF geladen).

### 3.4 Sicherheit

- **Markdown, niemals HTML** vom Server; der Client escaped zuerst und
  formatiert danach. Unverändert, und die Tests mit feindlichen
  DOCX/EML-Fixtures (`<script>` als Absatztext, `javascript:`-Link) laufen
  auch gegen den neuen Viewer und gegen den `Story`-Renderer.
- **Pfad-Konfinierung** über `_confine_to_autodoc` für jeden Endpunkt, wie
  heute.
- **Vorschau-Token** (D2): die Suchantwort trägt je Treffer ein kurzlebiges,
  signiertes Token (HMAC über Nutzer-Subject + Pfad + Ablauf, 30 Minuten,
  Schlüssel aus `WEB_SECRET_KEY`). `/thumbnail`, `/preview`,
  `/preview-content`, `/download` und `/client-path` verlangen es, wenn
  `web.preview.require_token` gesetzt ist (Vorgabe: an, sobald
  `IDENTITY_ENABLED`). Damit erreicht ein Nutzer nur Dateien, die die Suche
  ihm — und damit die Zugriffsgruppen von Knovas — tatsächlich geliefert hat.
  Deep-Links holen sich das Token über eine Suche nach dem Pointer.
- **CSP**: heute nur `frame-ancestors 'self'`. pdf.js braucht keinen weiteren
  Eintrag; wird die CSP später um `script-src` erweitert, gehört
  `worker-src 'self'` dazu (Pflichtenheft §6.4). Als Task in D3 notiert, damit
  es nicht vergessen geht.
- **MIME**: `mimetypes.add_type` für `.woff2` und `.mjs` in `app.py` — das
  schlanke Image hat keine `/etc/mime.types`, und ein Modul-Skript mit
  falschem Typ lädt der Browser nicht.
- **pdf.js-Pflege**: Version gepinnt, Aktualisierung als vierteljährliche
  Betriebsaufgabe in `docs/operations` beschrieben (pdf.js hatte CVEs).

### 3.5 Option E2 · Render-Dienst für layouttreue Office-Seiten

Die typografische Seite (3.1) zeigt Inhalt und Struktur, nicht das Layout
des Originals: Briefkopf, Logo, Tabellenlinien, Seitenumbrüche fehlen. Wer
das will, bekommt es als eigenen Dienst:

- Compose-Service `knovas-render`, Profil `render`, eigenes Image mit
  LibreOffice headless (≈ 400 MB), kein Netzwerkzugang nach aussen,
  Dokumente `:ro`, CPU/Memory-Limits, `POST /render?path=` → PDF, Zeitlimit
  60 s, ein Auftrag pro Kern.
- Die Platform nutzt ihn, wenn `RENDER_SERVICE_URL` gesetzt ist: Vorschaubild
  aus der echten Seite; DOCX im Dialog als **paginiertes PDF** in pdf.js —
  mit «Seite 3 von 12», Sprung auf die Seite, Markierung über die Textebene.
  Ohne den Dienst greift die typografische Seite. Kein Kunde muss ihn
  betreiben.
- Ergebnisse landen im selben Cache (Schlüssel wie 3.1, Suffix `-lo.pdf`).
- Öffnet den Weg für XLSX/PPTX, sobald Pflichtenheft F2 sie indexiert.

Bewusst nicht im `docbridge-web`-Image: die Gründe vom 2026-07-26
(Imagegrösse, Angriffsfläche) gelten dort weiter; ein isolierter Dienst hebt
sie auf.

### 3.6 Option E1 · Vorwärmen im RemoteController

Der RemoteController liest beim Indexieren ohnehin jede Datei und schreibt
den Kontext-Sidecar nach `/var/rc-state/search_context`. Mit dieser Option
schreibt er daneben `search_preview/<sha256>-w520.png` — die Platform findet
das Bild auf dem gemeinsamen Volume (`rc-state`, bereits `:ro` eingebunden)
und rendert nur, was fehlt. Empfohlen für Bestände über 10 000 Dokumente oder
Shares über SMB, wo das Lesen der Datei die Renderzeit dominiert.
Voraussetzung: PyMuPDF im RC-Image (kommt über `knovas-extract[pdf]`, sonst
eine Zeile in `pyproject.toml`).

### 3.7 Option E3 · Durchsuchbare Textebene für Scans

Gescannte PDFs haben keine Textebene; die OCR läuft beim Indexieren und ihr
Text geht nur an Knovas und in den Sidecar. Mit dieser Option legt der
RemoteController je Scan eine Kopie mit unsichtbarer Textebene
(`search_ocr/<sha256>.pdf`) ab; der Viewer nutzt sie für die Markierung,
«Öffnen» öffnet weiterhin das Original. Kostet Plattenplatz in der Grösse
des Scan-Bestands.

---

## 4 · Arbeitspakete und Aufwand

PT = Personentag (8 h). Netto ohne Reserve; Reserve 15 % in Abschnitt 10.

### A · Vorschau-Pipeline

| WP | Inhalt | Ergebnis | PT |
| --- | --- | --- | --- |
| A1 | Renderer je Format: `preview_render.py`, typografische Seite (Story) für DOCX/TXT/MD, Umschlag für MSG/EML, PDF in 2×; Server-Schriften; Fixtures je Format | jedes Format liefert ein PNG oder einen typisierten Fehler | 3,0 |
| A2 | Cache und Auslieferung: Schlüssel, ETag vor dem Rendern, Sweep mit Obergrenze, Konfiguration, Kennzahlen in `/api/stats` | zweite Anfrage ohne Rendern; Zahlen messbar | 2,0 |
| A4 | `preview-content` für EML und MD; Mail-`meta` (CC, Datum, Anhänge); Antwort aus dem Cache | die zwei 415-Fälle sind weg | 0,5 |
| | **Summe A** | | **5,5** |

### B · Trefferkarte

| WP | Inhalt | Ergebnis | PT |
| --- | --- | --- | --- |
| B1 | Karte je `preview_kind`: Vorschaubild für alle Arten mit `srcset`, Format-Kürzel, Icon-Fallback mit Grund, Mail-Metazeile, mobile Kachel | Karten in einer Flucht, für jedes Format | 2,0 |
| B2 | Fundstellen in der Suchantwort (`hits[]`, `hit_count`) aus `top_chunks` × Sidecar; Zähler auf der Karte | «3 Fundstellen» stimmt mit der Leiste überein | 1,5 |
| B3 | Tastatur und Zugänglichkeit: ↑/↓ in der Liste, Fokusrückgabe, `aria-live`, Kontrastprüfung der neuen Elemente | Liste und Dialog ohne Maus bedienbar | 1,0 |
| | **Summe B** | | **4,5** |

### C · Dokumentvorschau

| WP | Inhalt | Ergebnis | PT |
| --- | --- | --- | --- |
| C1 | Dialog-Gerüst nach Entwurf: Markup, Kopfzeile, Werkzeugleiste, Raster Seitenansicht + Leiste, Dokumentdaten, Zustände, mobil | der Dialog sieht aus wie der Entwurf, mit echten Daten | 3,0 |
| C2 | Seitenansicht für Textformate: Blatt, Tabellen in `markdown.js`, Satz-Anker aus dem Sidecar, aktive/leise Markierung, Scroll, Fundstelle ‹ › | DOCX/TXT/MD/E-Mail springen und markieren | 3,0 |
| C3 | pdf.js-Viewer: selbst gehostet, Seiten als Blätter, Textebene, Zoom, Seitennavigation, Sprung auf `page_number`, Markierung des Satzes, Scan-Fallback, Speicherfreigabe beim Blättern | PDF springt und markiert; Optik ist Knovas | 5,0 |
| C4 | E-Mail-Ansicht: Kopfblock, Anhangsliste, Zitatblöcke gedämpft | E-Mails lesen sich wie E-Mails | 1,0 |
| C5 | Navigation und Laden: Vorladen ±1, Abbruch, Fehlerzustände, Aktionen Öffnen / OneDrive / Pfad kopieren, Deep-Link `?doc=&hit=` | Wechsel zwischen Treffern ohne Wartezeit | 1,5 |
| | **Summe C** | | **13,5** |

### D · Qualität, Sicherheit, Betrieb

| WP | Inhalt | Ergebnis | PT |
| --- | --- | --- | --- |
| D1 | Tests: Renderer je Format inkl. feindlicher Fixtures; Endpunkt-Matrix (200/304/400/401/404/415/422); Playwright-Durchlauf je Format auf dem Demo-Korpus; Screenshot-Vergleich für Karte und Dialog in CI | CI grün, Regressionen sichtbar | 3,0 |
| D2 | Vorschau-Token auf den Datei-Endpunkten (3.4) | Vorschau nur für Treffer, die die Suche geliefert hat | 2,0 |
| D3 | Härtung: MIME-Typen, `data-`-Attribute statt `onclick`, pdf.js-Pin und Aktualisierungsanleitung, CSP-Notiz | Backlog §4 abgeräumt | 1,0 |
| D4 | Dokumentation und Release: `docs/features/dokumentvorschau.md`, Setup (Cache, Optionen), `RELEASE_NOTES.md`, Backlog nachführen, Demo-Drehbuch | Kunde kann es betreiben und vorführen | 1,5 |
| | **Summe D** | | **7,5** |

### E · Optionen

| WP | Inhalt | PT |
| --- | --- | --- |
| E1 | Vorwärmen im RemoteController (3.6) | 2,0 |
| E2 | Render-Dienst LibreOffice (3.5): Image, API, Limits, Platform-Anbindung für Vorschaubild und paginierte DOCX-Ansicht, Setup-Doku | 6–8 |
| E3 | Textebene für Scans (3.7) | 3,0 |

**Basispaket A + B + C + D = 31,0 PT.**

---

## 5 · Zeitplan und Meilensteine

Eine Entwicklerin, 4,5 produktive PT pro Woche. Mit zwei (eine Backend:
A, B2, D2; eine Frontend: B1, B3, C) verkürzt sich der Plan auf vier Wochen;
die Schnittstelle zwischen beiden ist 3.2 und wird in Woche 1 festgezurrt.

| Woche | Meilenstein | Enthält | Was der Kunde sieht |
| --- | --- | --- | --- |
| 1–2 | **M1 · Jedes Format hat ein Vorschaubild** | A1, A2, A4, B1 | Trefferliste auf dem Demo-Korpus: PDF, DOCX, E-Mail, Scan, TXT — alle mit Seite; EML und MD haben eine Vorschau |
| 3–4 | **M2 · Der neue Dialog** | B2, B3, C1, C2, C4, C5 | Dialog nach Entwurf; Fundstellen-Leiste; Sprung und Markierung für DOCX, E-Mail, TXT |
| 5–6 | **M3 · PDF springt zur Fundstelle** | C3 | pdf.js in Knovas-Optik; Seite und Satz markiert; Scans mit Seitensprung |
| 7 | **M4 · Abnahme und Release** | D1 (läuft ab Woche 1 mit), D2, D3, D4 | Abnahme nach Abschnitt 6; Release-Notes; Betriebsdoku |

Jeder Meilenstein wird auf dem Demo-Kanzlei-Korpus vorgeführt
(`docs/client/README.md`: Schaffhauserstrasse, Meierhans, 2024-017).

---

## 6 · Abnahmekriterien

Messbar, auf dem Demo-Korpus und auf einem Kundensystem mit Share-Anbindung:

1. **Vollständigkeit**: 100 % der Treffer zeigen ein gerendertes Vorschaubild
   oder ein Format-Icon mit Grund. Kein leerer Kasten, kein zerbrochenes
   Bild. Gemessen über alle sieben Formate des Demo-Korpus.
2. **Kein 415 mehr** für EML und MD; jedes indexierte Format hat eine
   Dialog-Ansicht.
3. **Geschwindigkeit**: Vorschaubild aus dem Cache P95 < 300 ms
   (Server-seitig); Erstrendering ≤ 2 s für PDFs bis 25 MB auf lokaler
   Platte; auf SMB wird gemessen und im Abnahmeprotokoll festgehalten
   (Backlog §2). Öffnen eines gecachten Treffers bis zur sichtbaren Seite
   < 1 s.
4. **Fundstellen**: Zähler auf der Karte = Einträge in der Leiste. Bei PDFs
   mit Textebene wird der Satz in ≥ 95 % der Demo-Fundstellen auf der
   richtigen Seite markiert; bei Scans wird die Seite gezeigt und die Leiste
   trägt den Text. Bei Textformaten wird der Satz in ≥ 95 % gefunden und
   markiert.
5. **Bedienung**: Liste und Dialog vollständig per Tastatur; Fokus kehrt
   zurück; Screenreader lesen Trefferzahl, Position und Fundstelle; keine
   JS-Fehler in der Konsole; Lighthouse Accessibility ≥ 90 auf der
   Ergebnisseite.
6. **Optik**: Dialog und Karte entsprechen dem Entwurf vom 7.9. (Abgleich per
   Screenshot); der browsereigene PDF-Viewer erscheint nirgends mehr.
7. **Sicherheit**: die feindlichen Fixtures erzeugen weder `<script>` noch
   verbotene `href`-Schemata im DOM; Pfade ausserhalb von AutoDoc werden mit
   400 abgewiesen; mit D2 werden Pfade ohne gültiges Token mit 403
   abgewiesen.
8. **Betrieb**: Cache hält die Obergrenze ein; `/api/stats` zeigt die
   Kennzahlen; Neustart ohne Cache funktioniert (nur langsamer).

---

## 7 · Risiken und Annahmen

| Risiko | Wirkung | Massnahme |
| --- | --- | --- |
| SMB-Latenz dominiert das Rendern | erste Suche auf einer Akte spürbar langsam | Cache (A2); Option E1 verlagert das Rendern ins Indexieren; vorher messen |
| Scans ohne Textebene | keine Markierung im PDF | Seitensprung + Leiste (C3); Option E3 |
| DOCX kennt keine Seiten | kein «Seite x von y» bei Textformaten | Satz-Anker statt Seiten (C2); Option E2 liefert echte Seiten |
| Grosse oder beschädigte Dateien | Rendering hängt oder scheitert | Grössen- und Zeitbudget, typisierte Fehler, Icon-Fallback; die Suche wartet nie auf ein Bild |
| pdf.js-Sicherheitslücken | Pflegeaufwand | gepinnte Version, vierteljährliche Aktualisierung als Betriebsaufgabe (D3/D4) |
| Schriften im Server-Rendering | falsche Zeichen bei Sonderzeichen | IBM Plex TTF mit Fallback auf MuPDF-Basisschriften; Test mit Umlauten, ß, «» |
| Kontext-Sidecar fehlt (Installation ohne RC-Sidecar) | keine Fundstellen-Leiste | Hinweis in der Leiste statt Schweigen; Systemstatus weist es aus |
| Knovas-API ohne `offset`/Filter | «Mehr laden» bleibt eine zweite Suche | unverändert, nicht Teil dieses Plans (Pflichtenheft F3) |

Annahmen: Browser sind aktuelle Evergreen-Versionen (natives `<dialog>` ist
schon heute Voraussetzung); der RemoteController schreibt Sidecars (Standard
im vereinten Stack); das Kundensystem hat auf dem Volume
`docbridge_integration_data` 2 GB frei für den Cache.

---

## 8 · Nicht im Umfang

Bewusst abgegrenzt, damit das Angebot hält, was es verspricht:

- Facetten, Sortierung, echte Pagination — `POST /secured/query` nimmt nur
  `Input` (Backlog §5, Pflichtenheft F3).
- XLSX/PPTX-Indexierung (Pflichtenheft F2). Option E2 bereitet den Viewer
  darauf vor.
- Anmerkungen, Kommentare, Bearbeiten im Viewer.
- Versionsliste (F6), «Ähnliche Dokumente» (F8), Metadaten-Bearbeitung —
  gehören zum Dokument-Dialog, aber zu einem anderen Vorhaben.
- Dunkles Farbschema: das Produkt ist ein helles UI; der Entwurf hält das fest.

---

## 9 · Demo-Drehbuch

Auf dem Demo-Kanzlei-Korpus, fünf Minuten:

1. **«Schaffhauserstrasse»** suchen. Die Liste zeigt Mietvertrag (PDF),
   Aktennotiz (DOCX), E-Mail-Verlauf (EML/MSG), Eingangspost (Scan) — jede
   Karte mit einer Seite, jede mit Zähler.
2. Die **Aktennotiz** öffnen: Blatt in Knovas-Typografie, Satz markiert,
   Leiste mit drei Fundstellen. «Fundstelle ›» springt.
3. **↓** zur E-Mail: Umschlag-Kopf, Betreff, Text, Anhänge. Absender steht
   schon auf der Karte.
4. **↓** zum **Mietvertrag** (PDF): pdf.js in Knovas-Optik, öffnet auf
   Seite 3, Satz markiert; «Seite 3 von 8»; Zoom.
5. **↓** zur **Eingangspost** (Scan): Seite als Bild, Leiste trägt den
   OCR-Text. Hier die Option E3 erwähnen.
6. **Öffnen** — Word/Acrobat öffnet die Datei vom Share; **Pfad kopieren**.
7. Zum Schluss `/api/stats`: Cache-Trefferquote nach fünf Minuten Demo.

---

## 10 · Kommerzielles Gerüst

| Position | Umfang | PT netto | mit 15 % Reserve | Preis |
| --- | --- | --- | --- | --- |
| **Basispaket** | A + B + C + D (Abschnitt 4) | 31,0 | 36 | PT × Tagessatz |
| Option E1 | Vorwärmen im RemoteController | 2,0 | 2,5 | |
| Option E2 | Render-Dienst (layouttreue Office-Seiten, paginierte DOCX-Ansicht) | 6–8 | 7–9 | |
| Option E3 | Textebene für Scans | 3,0 | 3,5 | |
| Betrieb | pdf.js-Aktualisierung vierteljährlich, Cache-Kennzahlen prüfen | 0,5 / Quartal | | Wartungsvertrag |

Lieferung als Release der Knovas Components (`RELEASE_NOTES.md`), Abnahme
nach Abschnitt 6, Vorführung je Meilenstein nach Abschnitt 5. Das Basispaket
setzt nichts voraus, was der Kunde nicht schon betreibt; die Optionen bringen
je einen Compose-Service (E2) oder eine RC-Einstellung (E1, E3) mit.

---

## Anhang A · Dateien

**Platform — neu**
- `src/web_interface/preview_render.py` — Renderer je Art, Story-Vorlagen, Fehlerklassen
- `src/web_interface/preview_cache.py` — Schlüssel, Sweep, Kennzahlen
- `src/web_interface/preview_fonts/*.ttf` — IBM Plex für den Server (OFL)
- `src/web_interface/static/js/viewer.js` — Dialog-Controller
- `src/web_interface/static/js/vendor/pdfjs/` — `pdf.mjs`, `pdf.worker.mjs`, `LICENSE`
- `tests/test_preview_render.py`, `tests/test_preview_cache.py`, `tests/test_search_hits.py`, `tests/test_preview_token.py`
- `tests/fixtures/` — je Format eine gutartige und eine feindliche Datei (EML neu; MSG über `make_msg.py` wie bisher)
- `KnovasPlatform/docs/features/dokumentvorschau.md`

**Platform — geändert**
- `src/web_interface/preview.py` — `PREVIEW_KIND_BY_SUFFIX` um `eml`, `md`; Mail-`meta`
- `src/web_interface/app.py` — `/thumbnail` für alle Arten, ETag vor Rendern, `hits[]`/`preview_kind`/`preview_token` in `/api/search`, Token-Prüfung, MIME-Typen, `/api/stats`
- `src/context_store.py` — `hits_for_result(result, entry, radius=1, limit=10)`
- `src/web_interface/static/js/app.js`, `markdown.js`, `templates/index.html`, `static/css/style.css`
- `config/config.yaml` — Block `web.preview`
- `requirements.txt` — unverändert (PyMuPDF und knovas-extract sind da)
- `RELEASE_NOTES.md`, `docs/search-ui-backlog.md`, `KnovasPlatform/docs/README.md`

**Optionen**
- E1: `RemoteController/src/sync/preview_sidecar.py`, `RC_PREVIEW_PREWARM`
- E2: `KnovasPlatform/components/knovas_render/` (Dockerfile, `app.py`), `docker-compose.yml` Profil `render`, `RENDER_SERVICE_URL`
- E3: `RemoteController/src/sync/ocr_layer.py`, `RC_OCR_TEXT_LAYER`

## Anhang B · Offene Entscheidungen

Vor Start zu klären, keine davon blockiert M1:

1. **Fundstellen-Zähler auf der Karte** — vorgesehen (2.1). Die Spec vom
   2026-07-30 hatte Relevanzsignale zurückgestellt; der Zähler ist kein
   Score, sondern eine Anzahl, und der Dialog löst ihn ein. Falls nein: B2
   liefert nur die Leiste.
2. **Dokumentdaten in der Leiste** — der Entwurf zeigt «Eigentümer» und
   «Zugriff». Einen Autor liefert die Suche heute nicht (der RC extrahiert
   ihn, verwirft ihn aber beim Upload — Pflichtenheft §1.6). Vorschlag:
   Akte · Typ · Datum · Geändert · Grösse · Seiten · Quelle · Pfad;
   «Zugriff» nur für Administratoren aus der Platform-DB, als Folgeschritt.
3. **Breite des Vorschaubilds** — 260 px bleibt (Nachtrag 2026-07-30:
   darunter ist nichts erkennbar). Bei zwei Karten pro Bildschirm bleibt es.
4. **Token-Pflicht (D2) als Vorgabe an** — empfohlen; Installationen ohne
   Identitätsdienst (`IDENTITY_ENABLED=false`) behalten das heutige
   Verhalten.
5. **Option E2 im Angebot** — als Option führen, nicht ins Basispaket: die
   typografische Seite reicht zum Auswählen und Lesen; Layouttreue ist ein
   Wunsch, den man vorführen und dann verkaufen kann.
