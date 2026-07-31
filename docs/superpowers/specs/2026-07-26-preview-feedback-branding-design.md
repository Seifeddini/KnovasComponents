# Design: Multi-Format-Preview, Feedback-Entfernung, Knovas-Branding

Datum: 2026-07-26
Betrifft: `KnovasPlatform/components/docbridge_integration`

## Kontext

Die Such-UI ist Flask + Jinja2 mit einer Vanilla-JS-Klasse (`static/js/app.js`, 1.243 Zeilen)
und handgeschriebenem CSS. Es gibt keinen Build-Schritt und keine JS-Dependencies.

Drei Arbeitspakete, in dieser Reihenfolge:

1. Dokument-Preview für PDF, DOCX, TXT und MSG (Priorität 1)
2. Relevanz-/Bewertungs-Feedback vollständig entfernen, inklusive Endpunkte
3. Branding auf den offiziellen Knovas-Guide umstellen

## 1. Multi-Format-Preview

### 1.1 Ist-Zustand

`GET /api/document/<doc_id>/preview` (`app.py:1102`) liefert ausschließlich PDF inline und
antwortet für jedes andere Format mit `415`. Es gibt keinen Konvertierungspfad im
Serving-Code; Konvertierung passiert nur im RemoteController beim Ingest.

### 1.2 Ansatz

Serverseitige Extraktion nach **Markdown** über `knovas_extract`, Rendering im Seitenpanel.

PDF wird bewusst **nicht** konvertiert: der Client bettet den bestehenden
`/preview`-Endpunkt in ein `<iframe>` ein und nutzt den nativen Browser-Viewer. Beste
Darstellungstreue bei null Konversionskosten.

| Format | Pfad |
| --- | --- |
| `.pdf` | bestehender `/preview`-Endpunkt, `<iframe>`, nativer Viewer — **erfordert die Header-Anpassung aus 1.3** |
| `.docx` | `knovas_extract.extract(..., emit_markdown=True)` |
| `.txt` | dito |
| `.msg` | dito |

Verworfene Alternativen:

- **Alles nach PDF konvertieren** (LibreOffice headless): mehrere hundert MB Image-Zuwachs,
  neue System-Abhängigkeit, größere Angriffsfläche. Layout-Treue ist für eine Such-Vorschau
  selten entscheidend.
- **Nur den vorhandenen `.search_context`-Text zeigen**: kein Aufwand, aber keine
  Formatierung, keine MSG-Kopfzeilen, und abhängig davon, dass der Sidecar existiert.

### 1.3 Voraussetzung: Same-Origin-Framing erlauben

nginx setzt auf allen proxied Antworten `X-Frame-Options: DENY` und
`Content-Security-Policy: frame-ancestors 'none'`. Beide verbieten Framing
**vollständig, auch same-origin**. Empirisch bestätigt: ein `<iframe>` oder `<object>` auf
eine eigene Seite wird abgewiesen mit

```
Framing '…' violates the following Content Security Policy directive:
"frame-ancestors 'none'". The request has been blocked.
```

Daraus folgt zweierlei:

1. Die PDF-Darstellung im Panel braucht eine Lockerung, sonst bleibt das Panel für PDF leer.
2. Die bestehende `<object type="application/pdf">`-Einbettung in der Hover-Preview
   (`app.js:653`) ist **bereits heute wirkungslos** — ein produktiver Bug, unabhängig von
   diesem Vorhaben.

Änderung in `nginx/docbridge-web-local.conf` und im Produktions-Template unter
`deploy/host-nginx/`:

```nginx
add_header X-Frame-Options "SAMEORIGIN" always;
add_header Content-Security-Policy "frame-ancestors 'self'" always;
```

Das ist eine bewusste, minimale Lockerung: die Anwendung darf eigene Seiten einbetten,
Framing durch fremde Herkünfte bleibt vollständig verboten. Der Clickjacking-Schutz
gegenüber Dritten ist unverändert.

Verworfene Alternativen: PDF über pdf.js rendern (widerspricht dem dependency-freien
Frontend) oder PDF in neuem Tab öffnen (bricht die einheitliche Panel-Interaktion).

**Nebenbefund**, nicht Teil dieses Vorhabens: `location = /health` setzt ein eigenes
`add_header Content-Type` und verliert dadurch nach nginx-Vererbungsregeln **sämtliche**
Security-Header aus dem `server`-Block. Die Route gibt nur `ok` zurück, der Effekt ist
also gering — sollte aber bei Gelegenheit korrigiert werden.

### 1.4 Endpunkt

```
GET /api/document/<doc_id>/preview-content?path=<relativer Pfad>
→ 200 {"kind": "docx|txt|msg", "markdown": str, "meta": {...}, "warnings": [str]}
→ 400 Pfad fehlt oder nicht erlaubt
→ 404 Datei nicht gefunden
→ 415 Format nicht unterstützt
```

- Pfad-Auflösung über den vorhandenen `_confine_to_autodoc`-Guard. Kein neuer Traversal-Code.
- Session- und CSRF-Verhalten wie bei allen anderen `/api/`-Routen (GET, daher kein
  CSRF-Header nötig; die Login-Pflicht greift über `require_company_login`).
- Ressourcengrenzen explizit über `knovas_extract.Limits`: `max_text_bytes`,
  `max_recursion_depth`, `max_markdown_expansion_ratio`.
- `meta` trägt `title`, `page_count`, `word_count`, `modified` aus `ExtractionResult.metadata`.

### 1.5 Sicherheitsmodell

Der Server liefert **Markdown, niemals HTML**.

`knovas_extract/_markdown.py` bezeichnet sich als "single trust boundary for producing
Markdown output from hostile inputs" und leistet: Entfernen von `script`/`style`/`iframe`/
`object`/`embed`/`svg`/`math` samt Inhalt, Entfernen von Kommentaren und CDATA, Verwerfen
von Event-Handlern und `style`-Attributen, URL-Scheme-Allowlist (`http`, `https`, `mailto`,
`tel`), Ersetzen von `<img>` durch Alt-Text (verhindert Beaconing beim Rendern) sowie
DoS-Schranken.

**Diese Garantie deckt Markup ab, nicht Textinhalt.** Nachgewiesen: ein DOCX-Absatz mit dem
wörtlichen Text `<script>alert(1)</script>` erscheint unescaped im Markdown. Der Client
muss deshalb zwingend in dieser Reihenfolge arbeiten:

1. `escapeHtml()` auf den kompletten Markdown-String (Funktion existiert, `app.js:1183`)
2. danach das Markdown-Subset anwenden: Überschriften, Fett, Kursiv, Listen, Code, Links
3. Link-Schemata clientseitig erneut gegen `http`/`https`/`mailto` prüfen (Defence in Depth)

Ein `innerHTML` mit unescaptem Extraktions-Output ist ein Fehler, kein Stilproblem.

### 1.6 Dependency-Fix

`requirements.txt:33` deklariert:

```
knovas-extract[pdf,docx,msg,markdown,sentences]>=0.2
```

Das ist unvollständig. `selectolax` gehört zum Extra **`html`**, nicht zu `markdown`
(`markdown` zieht nur `markdownify`). DOCX geht den Weg DOCX → mammoth → HTML → Markdown
und schlägt ohne den HTML-Parser zur Laufzeit fehl:

```
DependencyMissingError: missing optional dependency 'selectolax'
```

Korrektur:

```
knovas-extract[pdf,docx,msg,markdown,html,sentences]>=0.2
```

Verifiziert: nach Nachinstallation von `selectolax` liefert DOCX korrektes Markdown.

### 1.7 Seitenpanel

Dreispaltig auf Desktop: Ergebnisliste links (schmaler), Panel rechts. Klick auf eine Karte
öffnet das Panel, Esc und Schließen-Button kollabieren es.

- Panel-Kopf: Titel, Format-Badge, Seiten-/Wortzahl, Aktionen (Öffnen, Download)
- Skeleton-Platzhalter während des Ladens
- Fehlerzustand mit Fallback auf Öffnen und Download
- Mobil: Vollbild-Overlay statt Panel
- Laufende Anfragen werden bei Panel-Wechsel per `AbortController` abgebrochen

### 1.8 Bekannter Bug: Hover-Preview

Die Hover-Preview löst nicht aus. Diagnose: ein direkter Aufruf von `_showHoverPreview()`
rendert das Popover korrekt, und die Capture-Events erreichen den Handler nachweislich
(`capture:document-card`), trotzdem sind `_hoverPreviewCard` und `_hoverPreviewTimer`
danach leer. Die Enter/Leave-Zustandsmaschine löscht sich selbst; die genaue Ursache ist
offen.

Da das Seitenpanel dieselbe Aufgabe besser erfüllt, wird die Hover-Preview **entfernt**
statt repariert. Das streicht `_ensureHoverPreview`, `_onResultHoverEnter`,
`_onResultHoverLeave`, `_showHoverPreview`, `_hideHoverPreview`, den `_hoverPdfCache` und
die zugehörigen Listener.

## 2. Feedback entfernen

Vollständige Entfernung inklusive Endpunkten.

**Bewusste Konsequenz:** Laut `docs/KnovasAPI/Analytics_Integration_Guide.md` sind die
Engagement-Signale (view, click, download, dismiss) genau das, womit Knovas Suchqualität
misst und verbessert. Dieser Rückkanal entfällt. Der Schnitt wird so gelegt, dass er über
die Git-Historie rückholbar ist.

`static/js/app.js` — entfernen:

- `_buildRatingsSection`, `_scorePickerHtml`, `_setScoreSelection`, `_readSelectedScore`
- `_postRelevanceFeedback`, `_savePermanentDocumentRating`, `_loadPermanentDocumentRating`
- `_queueEngagement`, `_flushEngagement`, `_flushEngagementSoon`, `_reportEngagementForDocId`
- den "Nicht relevant"-Button und die zugehörigen Zweige in `_onResultsClick`
- die `querySessionId`-Buchführung

`web_interface/app.py` — entfernen:

- `POST /api/analytics/relevance-feedback`
- `POST /api/analytics/engagement`
- `GET|POST /api/document/rating`

`knovas_client.py` — die zugehörigen Client-Methoden entfernen.

Tests:

- `tests/test_engagement.py` entfällt
- `tests/test_csrf_enforcement.py` deckt fünf Endpunkte ab; die drei entfallenden Fälle
  werden gestrichen. `POST /api/search` und `POST /api/document/<id>/open` bleiben gated,
  der Test behält also seine Aussagekraft ohne Umbau. Die zugehörigen Dummy-Methoden im
  Test-Client (`post_relevance_feedback`, `post_engagement_events`, `post_document_rating`)
  entfallen mit.

Nicht angefasst: `query_session_id` kommt weiterhin aus `/secured/query` zurück. Das Feld
bleibt ungenutzt, ist aber harmlos.

## 3. Branding

Quelle: `Knovas Branding.pdf` (Jara Wullschleger, Juli 2026).

### 3.1 Palette

| Variable | Hex | Brand-Name | Verwendung laut Guide |
| --- | --- | --- | --- |
| `--primary-color` | `#3B79F2` | Azure Blue | Knovas Blue |
| `--primary-hover` | `#1A45C7` | Royal Blue | Callout Text |
| `--text-primary` | `#283647` | Slate Blue | Text |
| `--text-secondary` | `#73869B` | Slate Gray | Subtitles |
| `--title-color` | `#07172D` | Midnight Blue | Title |
| `--bg-color` | `#F4F6FC` | Ice Blue | Background |
| `--card-bg` | `#FDFDFD` | Off White | Background |
| `--border-color` | `#D1D6DF` | Light Ice Blue | — |

Weiter im Guide, vorerst ungenutzt: Cornflower Blue `#6E88DC` (Callout-Container),
Marine Blue `#193284` und Grayish Blue `#D9E0F7` (Gradienten).

### 3.2 Typografie

- Headings: **IBM Plex Mono**
- Body: **IBM Plex Sans**

Beide SIL OFL. Sie werden als woff2 unter `static/fonts/` **selbst gehostet** — kein CDN.
Das ist keine Stilfrage: die Anwendung läuft kundengehostet, teils offline, mit strikten
Security-Headern. Eine externe Font-URL bräche dieses Modell.

### 3.3 Logo und Favicon

Originale SVG-Dateien werden nachgeliefert. Bis dahin sind hochauflösende PNGs aus dem
Brand-PDF extrahierbar (Wordmark und K-Monogramm liegen dort als Vektoren vor, ca. 20 kB
bei 200 dpi). Der SVG-Export via PyMuPDF ist unbrauchbar — 3,6 MB, bettet den
Folienhintergrund als Rasterbild ein und verliert die Gradienten.

- Header: Wordmark
- Favicon: K-Monogramm (behebt nebenbei den bestehenden `/favicon.ico` → 404)

### 3.4 Draft-Themes

`static/css/drafts/` (atelier, ledger, horizon, helvetia — zusammen 1.065 Zeilen) wird
gelöscht, ebenso die `draft_theme`-Logik in `index.html` und `app.py`. Die Themes wären
nach dem Rebranding durchweg veraltet und arbeiten gegen das offizielle Branding. Sie
bleiben über die Git-Historie zugänglich.

## 4. Testing

- **Extraktion**: je ein Fixture für DOCX, TXT und MSG; Assertion auf erwartetes Markdown
  und auf gesetzte `meta`-Felder
- **Sicherheit**: ein DOCX-Fixture mit `<script>` als Absatz**text** und eines mit einem
  `javascript:`-Link. Erwartung: im gerenderten DOM entsteht weder ein `<script>`-Element
  noch ein `href` mit verbotenem Schema
- **Endpunkt**: 400 bei fehlendem Pfad, 400 bei Traversal-Versuch, 404 bei fehlender Datei,
  415 bei unbekannter Endung, 401 ohne Session
- **Regression**: die CSRF-Suite läuft nach dem Umhängen auf `/api/open-tokens/mint` grün
- **End-to-End**: Playwright-Skript im Scratchpad (Login → Suche → Panel öffnen je Format)

## 5. Reihenfolge

1. `requirements.txt` korrigieren, Image neu bauen
2. Extraktions-Endpunkt plus Tests
3. Markdown-Renderer im Client plus Sicherheitstests
4. Seitenpanel, Hover-Preview entfernen
5. Feedback-Entfernung (UI, Routen, Client-Methoden, Tests)
6. Branding (Palette, Fonts, Logo, Draft-Themes löschen)

Schritt 6 hängt an den Logo-Dateien. Palette und Typografie sind unabhängig davon machbar.

## 6. Verifikationsstand

Alle vier Formate wurden gegen `knovas_extract` durchgetestet:

| Format | Ergebnis |
| --- | --- |
| `.txt` | Markdown korrekt, keine Warnungen |
| `.pdf` | Markdown korrekt, `page_count` gesetzt |
| `.docx` | funktioniert **erst nach** dem Extra-Fix aus 1.6 |
| `.msg` | Titel, `msg:from`, `msg:to`, `msg:body_source`, Datum, Markdown — keine Warnungen |

### MSG-Fixture

Es gab keine `.msg` zum Testen, deshalb wird eine erzeugt. `extract_msg` bringt einen
`OleWriter` mit, mit dem sich ein echtes OLE/CFB-Dokument schreiben lässt — kein
selbstgebauter CFB-Writer nötig. Der Generator legt an:

- `__properties_version1.0` mit 32-Byte-Kopf, je ein `__substg1.0_<TAG>`-Stream pro
  variabel langer Property
- eine Empfänger-Storage `__recip_version1.0_#00000000` mit eigenem 8-Byte-Property-Kopf
- `__nameid_version1.0` mit den drei erwarteten (leeren) Streams

Zwei Details, die beim Bauen Zeit gekostet haben und im Generator kommentiert gehören:

- `msg.date` liest **`PR_CLIENT_SUBMIT_TIME` (`00390040`)**, nicht die Delivery- oder
  Creation-Time.
- Das Datum erscheint nur, wenn die Nachricht als gesendet gilt — `isSent` prüft
  `PR_MESSAGE_FLAGS` auf das `MSGFLAG_UNSENT`-Bit.

Das Datum wird aus einem festen `datetime` berechnet, damit die Fixture reproduzierbar
bleibt (5.120 Bytes). Der Generator gehört als Helper neben die Tests, damit die Fixture
nachvollziehbar entsteht statt als undurchsichtige Binärdatei im Repo zu liegen.

## 7. Offene Punkte

- Original-Logos (SVG/AI) stehen aus. Blockiert nur 3.3, nicht Palette und Typografie.
- Der laufende Dev-Container hat ein manuell nachinstalliertes `selectolax`. Nach der
  `requirements.txt`-Korrektur muss neu gebaut werden, damit Container und Image
  übereinstimmen.
