# Trefferliste — kompakte Karten, Orientierung, Politur — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Trefferliste so umbauen, dass eine Karte beim Auswählen hilft statt eine verkleinerte Dokumentansicht zu sein — kompakter, mit den bereits vorhandenen aber ungenutzten Feldern, orientiert durch Suchanfrage und Aktengruppen.

**Architecture:** Nahezu reines Frontend. Alle Trefferfelder liefert `/api/search` bereits; die einzige Serveränderung ist, `web.search.results_per_page` ans Template durchzureichen. Betroffen sind: `static/js/app.js`, `templates/index.html`, `static/css/style.css`.

**Tech Stack:** Vanilla JavaScript (ES2020), handgeschriebenes CSS, Jinja2. Kein Build-Schritt, keine npm-Abhängigkeiten, kein Test-Runner für JS.

**Spec:** `docs/superpowers/specs/2026-07-30-trefferliste-design.md`

## Global Constraints

- Arbeitsverzeichnis: `KnovasPlatform/components/docbridge_integration`. Die Python-Suite läuft mit blankem `pytest` und muss grün bleiben (aktuell 144 passed, 3 skipped). Sie deckt das Frontend nicht ab — die Verifikation dieses Plans ist der Browser.
- **Keine neuen Laufzeit-Abhängigkeiten.** Kein npm, kein `package.json`, kein Build-Schritt.
- **Kein Dokumentinhalt ohne Escaping ins DOM.** Titel, Snippets und Metadaten stammen aus fremden Dokumenten. Vorhandene Helfer benutzen: `escapeHtml`, `escapeAttr`, `escapeJsString`.
- UI-Texte auf Deutsch.
- Nach jeder Änderung an `src/` muss das Image neu gebaut werden — der Code liegt per `COPY src/` im Image, ein `docker compose restart` übernimmt ihn nicht:
  ```bash
  cd /Users/janik/Knovas/repos/KnovasComponents/KnovasPlatform
  docker compose build docbridge-web && docker compose up -d --force-recreate docbridge-web docbridge-web-nginx
  ```
- Node 22 steht unter `node` für `node --check` zur Verfügung. Ein Syntaxfehler in `app.js` legt die gesamte UI lahm — das ist die billigste Prüfung und gehört vor jeden Commit.
- Anmeldung für Handprüfungen: `http://localhost:8081`, `admin` / `knovas-local-demo-2026`. Das Formularfeld heisst `login_name`, nicht `username`.

## File Structure

Keine neuen Dateien. Drei bestehende:

| Datei | Verantwortung nach diesem Plan |
| --- | --- |
| `src/web_interface/static/js/app.js` | Kartenaufbau, Gruppierung, Limit-Zustand, Skelett- und Leerzustand |
| `src/web_interface/templates/index.html` | Ergebniskopf mit Query, „Mehr laden"-Knopf, Limit-Dropdown entfernt |
| `src/web_interface/static/css/style.css` | Zweispaltiges Kartenlayout, Metazeile, Gruppenüberschriften, Skelett-Karten |
| `src/web_interface/app.py` | einzige Serveränderung: `results_per_page` ans Template durchreichen |

`app.js` ist mit rund 880 Zeilen gross, aber gewachsen und konsistent. Dieser Plan restrukturiert sie nicht; er ersetzt gezielt `createDocumentCard`, `displayResults`, `showLoading`, `showEmptyState` und den Limit-Zugriff.

---

## Task 1: Kompakte Karte

**Files:**
- Modify: `src/web_interface/static/js/app.js` — `createDocumentCard`, dort wo `firstPageHtml` und `card.innerHTML` gesetzt werden
- Modify: `src/web_interface/static/css/style.css`

**Interfaces:**
- Consumes: die vorhandenen Helfer `displayTitle(doc)`, `escapeHtml`, `escapeAttr`, `formatDate`, `_buildContextSnippetHtml`, `_buildFirstPageHtml`, `capSummaryLength`, `ingestedSummaryText`, `lucide(name)`
- Produces: Karten-DOM mit `.document-card`, darin `.document-thumb`, `.document-body`,
  `.document-headline`, `.document-headline-text`, `.document-metaline`, `.document-title`,
  `.document-actions`. Der Textausschnitt behält die bestehenden Klassen
  `.document-context-snippet-text` bzw. `.document-first-page-text` — sie werden
  von `_buildContextSnippetHtml` und `_buildFirstPageHtml` erzeugt und bleiben unverändert.

- [ ] **Step 1: Lucide-Icons für die Formate ergänzen**

In `app.js` das Objekt `LUCIDE_ICONS` am Dateianfang um zwei Einträge erweitern. `file-text` existiert bereits, `mail` fehlt:

```javascript
    'mail': '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
```

- [ ] **Step 2: Format- und Typ-Helfer schreiben**

Als neue Methoden auf `DocumentSearchApp`, direkt vor `createDocumentCard`:

```javascript
    /** Formatkürzel aus der Dateiendung, z. B. "PDF". Leer wenn unbekannt. */
    _formatLabel(path) {
        const m = /\.([a-z0-9]+)$/i.exec(String(path || ''));
        return m ? m[1].toUpperCase() : '';
    }

    /**
     * Vorschaubild links auf der Karte. PDFs zeigen die gerenderte erste
     * Seite; fuer die uebrigen Formate gibt es ohne Konverter keine Seite,
     * deshalb steht dort ein Icon im gleich grossen Rahmen -- sonst haetten
     * die Karten je nach Format eine andere Hoehe.
     */
    _thumbHtml(doc, docId, path, title, localAvailable) {
        const ext = this._formatLabel(path);
        if (ext === 'PDF' && localAvailable) {
            const src = `/api/document/${encodeURIComponent(docId)}/thumbnail`
                + `?path=${encodeURIComponent(path)}`;
            return `<div class="document-thumb"><img loading="lazy"`
                + ` alt="Erste Seite von ${this.escapeAttr(title)}"`
                + ` src="${this.escapeAttr(src)}"></div>`;
        }
        const icon = ext === 'MSG' ? 'mail' : 'file-text';
        return `<div class="document-thumb document-thumb--icon">${lucide(icon)}</div>`;
    }

    /**
     * Genau eine Textquelle, in dieser Rangfolge: Trefferkontext, sonst erste
     * Seite, sonst Zusammenfassung. Zwei Textbloecke nebeneinander helfen beim
     * Auswaehlen nicht und kosten die halbe Karte.
     */
    _snippetHtml(doc) {
        if (doc.context_snippet) {
            const html = this._buildContextSnippetHtml(doc.context_snippet);
            if (html) return html;
        }
        if (doc.first_page_preview) {
            return this._buildFirstPageHtml(doc.first_page_preview);
        }
        const summary = this.ingestedSummaryText(doc);
        if (summary) {
            return `<div class="document-first-page-text">`
                + `${this.escapeHtml(this.capSummaryLength(summary, 400))}</div>`;
        }
        return '';
    }
```

- [ ] **Step 3: `createDocumentCard` auf den neuen Aufbau umstellen**

Den Block, der `summaryStr`, `summaryHtml`, `firstPageHtml`, `contextHtml`, `documentDate` und `fileModified` berechnet, ersetzen durch:

```javascript
        const documentDate = doc.document_date || doc.date || doc.timestamp || doc.created_at || null;
        const metaParts = [];
        if (doc.type) metaParts.push(this.escapeHtml(String(doc.type).toUpperCase()));
        const fmt = this._formatLabel(path);
        if (fmt) metaParts.push(fmt);
        if (documentDate) metaParts.push(this.escapeHtml(this.formatDate(documentDate)));
```

`modified_at` entfällt bewusst von der Karte: das Änderungsdatum der Datei hilft beim Auswählen nicht.

Anschliessend `card.innerHTML` vollständig ersetzen durch:

```javascript
        card.innerHTML = `
            ${this._thumbHtml(doc, docId, path, title, localAvailable)}
            <div class="document-body">
                <div class="document-headline">
                    <div class="document-headline-text">
                        ${metaParts.length ? `<div class="document-metaline">${metaParts.join(' · ')}</div>` : ''}
                        <div class="document-title">${this.escapeHtml(title)}</div>
                    </div>
                    <div class="document-actions">${actionsHtml}</div>
                </div>
                ${this._snippetHtml(doc)}
            </div>
        `;
```

Die Variablen `hasContext`, `ingestedSummary`, `isPdf`, `canPreviewPdf` bleiben weiter oben stehen, weil `actionsHtml` sie benutzt. Nur `summaryStr`, `summaryHtml`, `firstPageHtml`, `contextHtml` und `fileModified` werden nicht mehr gebraucht — entfernen, damit keine toten Berechnungen zurückbleiben.

- [ ] **Step 4: Kartenlayout im CSS**

Die Regel `.document-card` erweitern und die alten Textblock-Regeln ersetzen. `.document-first-page`, `.document-context-snippet` und `.document-ingested-summary` sind nach diesem Umbau keine eigenen Container mehr; ihre Textregeln (`.document-first-page-text`, `.document-context-snippet-text`) bleiben und werden vom Snippet weiterverwendet.

```css
.document-card {
    display: flex;
    gap: 16px;
    align-items: flex-start;
    background: var(--card-bg);
    border: 1px solid var(--border-color);
    border-radius: var(--radius);
    padding: 14px 16px;
    transition: border-color 0.2s;
    cursor: pointer;
}

/* Bild und Icon teilen sich Groesse und Rahmen, damit die Karten unabhaengig
   vom Format in einer Flucht bleiben. */
.document-thumb {
    flex: 0 0 auto;
    width: 80px;
    height: 104px;
    border: 1px solid var(--border-color);
    border-radius: 4px;
    background: var(--surface-sunken);
    overflow: hidden;
}

.document-thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: top;
    display: block;
}

.document-thumb--icon {
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-secondary);
}

.document-thumb--icon svg {
    width: 26px;
    height: 26px;
}

.document-body {
    flex: 1 1 0;
    min-width: 0;
}

.document-headline {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
}

.document-headline-text {
    min-width: 0;
}

.document-metaline {
    font-family: var(--font-heading);
    font-size: 0.68rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-secondary);
    margin-bottom: 3px;
}

.document-title {
    font-size: 1rem;
    font-weight: 600;
    color: var(--text-primary);
    overflow-wrap: anywhere;
}

.document-body .document-first-page-text,
.document-body .document-context-snippet-text {
    margin-top: 8px;
    /* Drei Zeilen reichen zum Beurteilen; alles darueber gehoert ins Modal. */
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
}
```

Ausserdem entfallen die Regeln `.document-header`, `.document-header-text`, `.document-meta`, `.meta-item`, `.meta-item-muted`, `.document-first-page-image` und die Gruppe `.document-first-page, .document-context-snippet` samt ihrer Label-Regeln — die Labels „ERSTE SEITE" und „TREFFERKONTEXT" verschwinden mit dem neuen Aufbau. Vor dem Löschen jeweils mit `grep` prüfen, dass die Klasse nirgends sonst benutzt wird.

- [ ] **Step 5: Syntax prüfen und bauen**

```bash
cd /Users/janik/Knovas/repos/KnovasComponents/KnovasPlatform/components/docbridge_integration
node --check src/web_interface/static/js/app.js
cd /Users/janik/Knovas/repos/KnovasComponents/KnovasPlatform
docker compose build docbridge-web && docker compose up -d --force-recreate docbridge-web docbridge-web-nginx
```

- [ ] **Step 6: Im Browser prüfen**

Anmelden, nach `Vertrag` suchen, und diese vier Punkte belegen — mit Zahlen, nicht mit Eindruck:

1. Kartenhöhe unter 150 px (`document.querySelector('.document-card').getBoundingClientRect().height`)
2. Mindestens vier Karten im Sichtfeld bei 1440×1000
3. Der PDF-Treffer zeigt ein Bild, DOCX und TXT das `file-text`-Icon, MSG das `mail`-Icon — alle im gleich grossen Rahmen
4. Pro Karte genau ein Textblock (`document.querySelectorAll('.document-card')[0].querySelectorAll('.document-first-page-text, .document-context-snippet-text').length === 1`)

- [ ] **Step 7: Commit**

```bash
git add src/web_interface/static/js/app.js src/web_interface/static/css/style.css
git commit -m "feat: compact result cards with type, format and a single snippet"
```

---

## Task 2: Suchanfrage und Aktengruppen

**Files:**
- Modify: `src/web_interface/static/js/app.js` — `displayResults`
- Modify: `src/web_interface/templates/index.html` — `.results-header`
- Modify: `src/web_interface/static/css/style.css`

**Interfaces:**
- Consumes: `createDocumentCard(doc, index)` aus Task 1
- Produces: `_groupByAkte(results)` liefert `Array<{akte: string|null, items: Array<{doc, index}>}>`

- [ ] **Step 1: Ergebniskopf im Template um die Anfrage erweitern**

In `index.html`:

```html
                <div class="results-header">
                    <h2>Suchergebnisse<span id="resultsQuery" class="results-query"></span></h2>
                    <span id="resultsCount" class="results-count"></span>
                </div>
```

- [ ] **Step 2: Gruppierung schreiben**

Als neue Methode auf `DocumentSearchApp`, vor `displayResults`:

```javascript
    /**
     * Treffer nach Akte gruppieren, in der Reihenfolge ihres ersten Auftretens
     * -- die Akte mit dem bestplatzierten Treffer steht oben. Innerhalb einer
     * Gruppe bleibt die API-Reihenfolge unveraendert: wir kennen die
     * Ranking-Logik nicht und sortieren deshalb nicht um.
     * Treffer ohne Akte sammeln sich am Ende.
     */
    _groupByAkte(results) {
        const groups = new Map();
        const ohne = [];
        results.forEach((doc, index) => {
            const akte = String(doc.akten_id || '').trim();
            if (!akte) {
                ohne.push({ doc, index });
                return;
            }
            if (!groups.has(akte)) groups.set(akte, []);
            groups.get(akte).push({ doc, index });
        });
        const out = [...groups.entries()].map(([akte, items]) => ({ akte, items }));
        if (ohne.length) out.push({ akte: null, items: ohne });
        return out;
    }
```

- [ ] **Step 3: `displayResults` umbauen**

```javascript
    displayResults(results, total, semantix) {
        this.closePreview();
        this.resultsSection.style.display = 'block';
        this.resultsContainer.innerHTML = '';

        if (!results || results.length === 0) {
            this.showEmptyState(semantix);
            return;
        }

        this.resultsQuery.textContent = this.currentQuery ? ` für „${this.currentQuery}“` : '';
        this.resultsCount.textContent = `${results.length} von ${total || results.length} Ergebnissen`;

        const groups = this._groupByAkte(results);
        // Eine einzige Gruppe braucht keine Zwischenueberschrift -- die waere
        // reines Rauschen und verschlechtert den haeufigsten Fall.
        const grouped = groups.filter((g) => g.akte).length > 1;

        groups.forEach((group) => {
            if (grouped) {
                const head = document.createElement('div');
                head.className = 'results-group';
                const label = group.akte ? `Akte ${group.akte}` : 'Ohne Aktenbezug';
                head.innerHTML = `<span class="results-group-label">${this.escapeHtml(label)}</span>`
                    + `<span class="results-group-count">${group.items.length}</span>`;
                this.resultsContainer.appendChild(head);
            }
            group.items.forEach(({ doc, index }) => {
                this.resultsContainer.appendChild(this.createDocumentCard(doc, index));
            });
        });
    }
```

`this.resultsQuery` im Konstruktor ergänzen, neben `this.resultsCount`:

```javascript
        this.resultsQuery = document.getElementById('resultsQuery');
```

Wichtig: `index` ist der Index in `results`, nicht der Index in der Gruppe. `openPreview(index)` und die Vor/Zurück-Navigation im Modal beziehen sich auf `this.currentResults` — würde hier der Gruppenindex landen, öffnete jede Karte das falsche Dokument.

- [ ] **Step 4: CSS für Query und Gruppen**

```css
.results-query {
    color: var(--text-secondary);
    font-weight: 400;
}

/* Zwischenueberschrift je Akte. Flach gehalten: eine Linie und ein Label,
   kein Kasten -- sonst waere die Verschachtelung wieder da, die wir gerade
   abgebaut haben. */
.results-group {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin: 22px 0 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border-color);
}

.results-group:first-child {
    margin-top: 4px;
}

.results-group-label {
    font-family: var(--font-heading);
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--primary-color);
}

.results-group-count {
    font-size: 0.78rem;
    color: var(--text-secondary);
}
```

- [ ] **Step 5: Bauen und beide Fälle prüfen**

```bash
node --check src/web_interface/static/js/app.js
cd /Users/janik/Knovas/repos/KnovasComponents/KnovasPlatform
docker compose build docbridge-web && docker compose up -d --force-recreate docbridge-web docbridge-web-nginx
```

Die Demo-Fixtures decken beide Fälle ab. Im Browser belegen:

1. Suche `Vertrag` — alle Treffer aus Akte `2024-001`, also **keine** Zwischenüberschrift (`document.querySelectorAll('.results-group').length === 0`)
2. Suche `Miete` oder `Gutachten` — Treffer aus mehreren Akten, also Zwischenüberschriften mit korrekten Anzahlen
3. Der Ergebniskopf zeigt `Suchergebnisse für „<Anfrage>"`
4. **Ein Klick auf die zweite Karte einer zweiten Gruppe öffnet das richtige Dokument** — das ist die Stelle, an der ein Indexfehler sich zeigen würde

- [ ] **Step 6: Commit**

```bash
git add src/web_interface/static/js/app.js src/web_interface/templates/index.html src/web_interface/static/css/style.css
git commit -m "feat: echo the query and group results by case"
```

---

## Task 3: Skelett, Leerzustand, „Mehr laden"

**Files:**
- Modify: `src/web_interface/static/js/app.js` — `showLoading`, `hideLoading`, `showEmptyState`, `performSearch`, Konstruktor
- Modify: `src/web_interface/templates/index.html` — Limit-Dropdown raus, „Mehr laden" rein, Spinner raus, `resultsPerPage` in `__DOCBRIDGE__`
- Modify: `src/web_interface/app.py` — `results_per_page` ans Template
- Modify: `src/web_interface/static/css/style.css`

**Interfaces:**
- Consumes: `displayResults` aus Task 2
- Produces: `this._searchLimit` (Number), `loadMore()`

- [ ] **Step 1: Template umbauen**

Den Block `.search-options` samt `#resultsPerPage` ersatzlos entfernen — er ist eine Limit-Einstellung, keine Nutzerfrage.

Den Spinner-Block `#loadingIndicator` ebenfalls entfernen; das Skelett kommt in den Ergebniscontainer.

Nach `#resultsContainer` einfügen:

```html
                <div class="results-more">
                    <button type="button" id="loadMoreButton" class="btn btn-outline" hidden>
                        Mehr laden
                    </button>
                </div>
```

- [ ] **Step 2: Limit-Zustand und Knopf verdrahten**

Die Spec verlangt als Startwert `web.search.results_per_page`. Der Wert erreicht
den Client heute nicht — er muss zuerst durchgereicht werden.

In `app.py`, beim `render_template('index.html', ...)`, ein Argument ergänzen:

```python
        results_per_page=config.get_int('web.search.results_per_page', 20),
```

In `index.html`, im `window.__DOCBRIDGE__`-Objekt:

```javascript
            resultsPerPage: {{ results_per_page|tojson }},
```

Im Konstruktor `this.resultsPerPage` ersetzen durch:

```javascript
        this.loadMoreButton = document.getElementById('loadMoreButton');
        /** Wieviele Treffer angefragt werden. Waechst ueber "Mehr laden". */
        this._searchLimitBase = Number(cfg.resultsPerPage) || 20;
        this._searchLimit = this._searchLimitBase;
        /** Fuer welche Anfrage das aktuelle Limit gilt. */
        this._limitQuery = '';
```

`cfg` ist die bereits im Konstruktor vorhandene Ableitung von `window.__DOCBRIDGE__`.

In `initializeEventListeners`:

```javascript
        this.loadMoreButton.addEventListener('click', () => this.loadMore());
```

In `performSearch` die Zeile `limit: parseInt(this.resultsPerPage.value),` ersetzen durch `limit: this._searchLimit,`.

`performSearch` liest den Begriff heute aus dem Eingabefeld. „Mehr laden" muss
aber die **laufende** Anfrage erweitern, nicht das, was inzwischen im Feld steht.
Deshalb bekommt die Methode einen optionalen Parameter — die erste Zeile

```javascript
    async performSearch() {
        const query = this.searchInput.value.trim();
```

wird zu

```javascript
    /** @param {string} [queryOverride] Erweitert eine laufende Suche ("Mehr laden"). */
    async performSearch(queryOverride) {
        const query = String(queryOverride != null ? queryOverride : this.searchInput.value).trim();
```

Ohne das würde ein Klick auf „Mehr laden", nachdem der Nutzer etwas Neues
eingetippt hat, die neue Eingabe suchen statt die angezeigte Trefferliste zu
erweitern.

Ausserdem muss eine **neue** Suche das Limit zurücksetzen. In `performSearch`, direkt nach `this.currentQuery = query;`:

```javascript
        if (query !== this._limitQuery) {
            this._searchLimit = this._searchLimitBase;
            this._limitQuery = query;
        }
```

Ohne das behielte eine neue Suche das hochgeschraubte Limit der vorherigen.

- [ ] **Step 3: `loadMore` schreiben**

```javascript
    /**
     * Die Knovas-API kennt kein offset (POST /secured/query nimmt nur Input),
     * es laesst sich also nicht nachladen. Stattdessen wird dieselbe Anfrage
     * mit hoeherem Limit gestellt und die Liste ersetzt. Fuer den Nutzer sieht
     * das wie Nachladen aus, ist aber eine zweite vollstaendige Suche.
     */
    loadMore() {
        if (this._searchLimit >= 100) return;
        this._searchLimit = Math.min(100, this._searchLimit * 2);
        const y = window.scrollY;
        this.performSearch(this.currentQuery).then(() => window.scrollTo({ top: y }));
    }
```

`performSearch` ist bereits `async` und gibt damit ein Promise zurück; der frühe
`return` bei leerem Begriff greift hier nicht, weil `loadMore` die gespeicherte
`currentQuery` übergibt.

Am Ende von `displayResults`, nach der Gruppenschleife:

```javascript
        // Nur anbieten, wenn die Antwort das Limit ausgeschoepft hat -- sonst
        // gibt es plausibel nichts mehr zu holen.
        const more = results.length >= this._searchLimit && this._searchLimit < 100;
        this.loadMoreButton.hidden = !more;
```

Und in `showEmptyState` sowie am Anfang von `showLoading`: `this.loadMoreButton.hidden = true;`

- [ ] **Step 4: Skelett statt Spinner**

`showLoading` und `hideLoading` ersetzen:

```javascript
    showLoading() {
        this.resultsSection.style.display = 'block';
        this.resultsSection.setAttribute('aria-busy', 'true');
        this.loadMoreButton.hidden = true;
        if (this.resultsCount) this.resultsCount.textContent = '';
        // Skelett in der Form der echten Karten: ohne das springt das Layout,
        // sobald die Ergebnisse eintreffen.
        this.resultsContainer.innerHTML = Array.from({ length: 3 }, () => `
            <div class="document-card document-card--skeleton" aria-hidden="true">
                <div class="document-thumb skeleton-block"></div>
                <div class="document-body">
                    <span class="skeleton-line skeleton-line--meta"></span>
                    <span class="skeleton-line skeleton-line--title"></span>
                    <span class="skeleton-line"></span>
                    <span class="skeleton-line skeleton-line--short"></span>
                </div>
            </div>
        `).join('');
        this.searchButton.disabled = true;
    }

    hideLoading() {
        this.resultsSection.setAttribute('aria-busy', 'false');
        this.searchButton.disabled = false;
    }
```

`hideLoading` leert den Container nicht — `displayResults` und `showEmptyState` überschreiben ihn ohnehin, und ein Zwischenschritt mit leerem Container würde erneut das Layout springen lassen.

- [ ] **Step 5: Leerzustand**

```javascript
    showEmptyState(semantix) {
        this.loadMoreButton.hidden = true;
        this.resultsContainer.innerHTML = `
            <div class="empty-state">
                <h3>Keine Treffer für „${this.escapeHtml(this.currentQuery)}“</h3>
                <p>Die Suche durchsucht den <strong>Inhalt</strong> der Dokumente, nicht nur die Dateinamen.</p>
                <ul class="empty-state-hints">
                    <li>Kürzeren oder allgemeineren Begriff versuchen</li>
                    <li>Schreibweise prüfen</li>
                    <li>Oberbegriff statt Fachbegriff verwenden</li>
                </ul>
            </div>
        `;
        this.resultsCount.textContent = '0 Ergebnisse';
    }
```

- [ ] **Step 6: CSS für Skelett, Leerzustand, Knopf**

```css
.results-more {
    display: flex;
    justify-content: center;
    margin-top: 18px;
}

.results-more button[hidden] {
    display: none;
}

/* Skelett: gleiche Kartenform, damit beim Eintreffen der Ergebnisse nichts
   springt. Nutzt dieselbe Shimmer-Animation wie die Vorschau. */
.document-card--skeleton {
    cursor: default;
}

.skeleton-block,
.skeleton-line {
    background: linear-gradient(90deg, var(--surface-sunken), var(--border-color), var(--surface-sunken));
    background-size: 200% 100%;
    animation: preview-shimmer 1.2s ease-in-out infinite;
    border-radius: 4px;
}

.skeleton-line {
    display: block;
    height: 11px;
    margin-bottom: 9px;
}

.skeleton-line--meta { width: 34%; height: 8px; }
.skeleton-line--title { width: 52%; height: 15px; margin-bottom: 12px; }
.skeleton-line--short { width: 68%; }

@media (prefers-reduced-motion: reduce) {
    .skeleton-block,
    .skeleton-line { animation: none; }
}

.empty-state-hints {
    margin: 10px 0 0 18px;
    color: var(--text-secondary);
}
```

Die Keyframes `preview-shimmer` existieren bereits für die Vorschau — nicht doppelt anlegen. Vorher prüfen: `grep -n "preview-shimmer" src/web_interface/static/css/style.css`.

Ausserdem entfallen die Regeln `.search-options`, `.search-options-label`, `.loading` und `.spinner`, sofern nichts anderes sie nutzt. Mit `grep` prüfen.

- [ ] **Step 7: Bauen und prüfen**

```bash
node --check src/web_interface/static/js/app.js
cd /Users/janik/Knovas/repos/KnovasComponents/KnovasPlatform
docker compose build docbridge-web && docker compose up -d --force-recreate docbridge-web docbridge-web-nginx
```

Im Browser belegen:

1. Während der Suche erscheinen drei Skelett-Karten, kein Spinner
2. Leere Suche → Fehler-Toast; Suche ohne Treffer → neuer Leerzustand mit der Anfrage im Titel
3. `Mehr laden` ist bei vier Treffern **unsichtbar** (weniger als das Limit von 20)
4. Kein `#resultsPerPage` mehr im DOM

- [ ] **Step 8: Volle Suite**

```bash
cd /Users/janik/Knovas/repos/KnovasComponents/KnovasPlatform/components/docbridge_integration && pytest
```

Erwartet: 144 passed, 3 skipped. Die einzige Python-Änderung ist das
durchgereichte `results_per_page`; schlägt hier etwas fehl, betrifft es den
`render_template`-Aufruf.

- [ ] **Step 9: Commit**

```bash
git add src/web_interface/static/js/app.js src/web_interface/templates/index.html \
        src/web_interface/static/css/style.css src/web_interface/app.py
git commit -m "feat: skeleton cards, a useful empty state, and load-more"
```

---

## Task 4: Abschluss

- [ ] **Step 1: Sauber neu bauen**

```bash
cd /Users/janik/Knovas/repos/KnovasComponents/KnovasPlatform
docker compose build --no-cache docbridge-web
docker compose up -d --force-recreate docbridge-web docbridge-web-nginx
```

- [ ] **Step 2: Durchgang von Hand**

Anmelden und der Reihe nach prüfen:

1. Suche `Vertrag`: kompakte Karten, Metazeile mit Art/Format/Datum, ein Textblock, PDF mit Bild und die übrigen mit Icon
2. Keine Gruppenüberschrift, weil alle Treffer aus einer Akte stammen
3. Suche `Gutachten` oder `Miete`: Gruppenüberschriften erscheinen, Anzahlen stimmen
4. Klick auf eine Karte in der zweiten Gruppe öffnet das **richtige** Dokument
5. Im Modal blättern die Pfeile weiterhin über alle Treffer hinweg, nicht nur innerhalb der Gruppe
6. Skelett beim Suchen, Leerzustand bei einer Anfrage ohne Treffer
7. Konsole ohne Fehler

- [ ] **Step 3: Backlog nachziehen**

In `docs/search-ui-backlog.md` die erledigten Punkte streichen — Format-Badge und kompaktere Karten sind mit diesem Plan umgesetzt. Relevanzsignale, Sortierung, Filter, Caching und der eigene PDF-Viewer bleiben offen.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs: mark card density and format badge as done"
```
