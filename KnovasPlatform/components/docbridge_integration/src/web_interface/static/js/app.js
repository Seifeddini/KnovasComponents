// Knovas Document Search - JavaScript

/**
 * Lucide-Icons (ISC) als Inline-SVG. Bewusst keine Icon-Library als
 * Dependency: es sind eine Handvoll Pfade, und das Frontend kommt ohne
 * Build-Schritt aus. currentColor laesst sie die Textfarbe erben.
 */
const LUCIDE_ICONS = {
    'external-link': '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h6"/>',
    'file-text': '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
    'mail': '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
    'download': '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>',
    'clipboard': '<rect width="8" height="4" x="8" y="2" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>',
};

/** @param {keyof LUCIDE_ICONS} name */
function lucide(name) {
    return `<svg class="icon" viewBox="0 0 24 24" width="16" height="16" fill="none" `
        + `stroke="currentColor" stroke-width="2" stroke-linecap="round" `
        + `stroke-linejoin="round" aria-hidden="true" focusable="false">${LUCIDE_ICONS[name]}</svg>`;
}

class DocumentSearchApp {
    constructor() {
        this.searchInput = document.getElementById('searchInput');
        this.searchButton = document.getElementById('searchButton');
        this.resultsSection = document.getElementById('resultsSection');
        this.resultsContainer = document.getElementById('resultsContainer');
        this.resultsCount = document.getElementById('resultsCount');
        this.resultsQuery = document.getElementById('resultsQuery');
        this.toastContainer = document.getElementById('toastContainer');
        this.loadMoreButton = document.getElementById('loadMoreButton');
        this.previewDialog = document.getElementById('previewDialog');
        this.previewTitle = document.getElementById('previewTitle');
        this.previewMeta = document.getElementById('previewMeta');
        this.previewBody = document.getElementById('previewBody');
        this.previewActions = document.getElementById('previewActions');
        this.previewClose = document.getElementById('previewClose');
        this.previewPrev = document.getElementById('previewPrev');
        this.previewNext = document.getElementById('previewNext');
        this.previewPosition = document.getElementById('previewPosition');
        /** @type {AbortController|null} laufende Vorschau-Anfrage */
        this._previewAbort = null;
        /** @type {number|null} Index des aktuell gezeigten Treffers */
        this._previewIndex = null;

        this.currentQuery = '';
        this.currentResults = [];
        const cfg = typeof window !== 'undefined' ? window.__DOCBRIDGE__ || {} : {};
        this.onedriveEnrichmentLoaded = !!cfg.onedriveEnrichmentLoaded;
        /** CSRF token for state-changing requests (server enforces it on every POST). */
        this.csrfToken = cfg.csrfToken || '';
        /** Wieviele Treffer angefragt werden. Waechst ueber "Mehr laden". */
        this._searchLimitBase = Number(cfg.resultsPerPage) || 20;
        this._searchLimit = this._searchLimitBase;
        /** Fuer welche Anfrage das aktuelle Limit gilt. */
        this._limitQuery = '';

        this.initializeEventListeners();
    }

    /**
     * Headers for a state-changing JSON request. Attaches the X-CSRF-Token the same
     * way the companion-mint call does, so every POST/PUT/PATCH/DELETE the server
     * gates uniformly is accepted. Read-only GETs do not need this.
     */
    _jsonHeadersWithCsrf(extra) {
        return Object.assign(
            { 'Content-Type': 'application/json', 'X-CSRF-Token': this.csrfToken },
            extra || {},
        );
    }
    
    initializeEventListeners() {
        this.searchButton.addEventListener('click', () => this.performSearch());
        
        this.searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.performSearch();
            }
        });
        
        // Der Systemstatus liegt in den Einstellungen; auf der Suchseite gibt
        // es den Ausloeser nicht mehr. Ohne Pruefung wuerde die gesamte
        // Initialisierung an dieser Zeile abbrechen.
        const healthCheckLink = document.getElementById('healthCheck');
        if (healthCheckLink) {
            healthCheckLink.addEventListener('click', (e) => {
                e.preventDefault();
                this.checkHealth();
            });
        }

        this.loadMoreButton.addEventListener('click', () => this.loadMore());

        this.resultsContainer.addEventListener('click', (e) => this._onResultsClick(e));

        // Bildfehler steigen nicht auf, deshalb capture. Ein Vorschaubild, dessen
        // Datei zwischen Indexierung und Suche verschwunden ist, faellt auf das
        // Icon zurueck -- sonst steht der Alt-Text als Textblock in der Karte.
        // load steigt wie error nicht auf -- deshalb capture.
        this.resultsContainer.addEventListener('load', (e) => {
            const img = e.target;
            if (!img || !img.matches || !img.matches('.document-thumb img')) return;
            const thumb = img.closest('.document-thumb');
            if (thumb) thumb.classList.remove('document-thumb--loading');
        }, true);

        this.resultsContainer.addEventListener('error', (e) => {
            const img = e.target;
            if (!img || !img.matches || !img.matches('.document-thumb img')) return;
            const thumb = img.closest('.document-thumb');
            if (!thumb) return;
            thumb.classList.remove('document-thumb--loading');
            thumb.classList.add('document-thumb--icon');
            thumb.innerHTML = lucide('file-text');
        }, true);

        this.previewClose.addEventListener('click', () => this.closePreview());
        this.previewPrev.addEventListener('click', () => this.stepPreview(-1));
        this.previewNext.addEventListener('click', () => this.stepPreview(1));

        // <dialog> feuert 'close' bei Escape und bei close() gleichermassen --
        // ein Ort fuer das Aufraeumen statt einer eigenen Escape-Behandlung.
        this.previewDialog.addEventListener('close', () => this._afterPreviewClosed());

        // Klick auf den Backdrop schliesst. Das Ereignis landet auf dem Dialog
        // selbst, deshalb wird gegen sein Rechteck geprueft statt gegen contains().
        this.previewDialog.addEventListener('click', (e) => {
            if (e.target !== this.previewDialog) return;
            const r = this.previewDialog.getBoundingClientRect();
            const inside = e.clientX >= r.left && e.clientX <= r.right
                && e.clientY >= r.top && e.clientY <= r.bottom;
            if (!inside) this.closePreview();
        });

        // Pfeiltasten blaettern durch die Treffer, solange das Modal offen ist.
        this.previewDialog.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
                e.preventDefault();
                this.stepPreview(1);
            } else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {
                e.preventDefault();
                this.stepPreview(-1);
            }
        });

        // Die Karten liegen per tabindex in der Tab-Reihenfolge, reagierten aber
        // nur auf Klicks -- damit war die Vorschau fuer Tastaturnutzer nicht
        // erreichbar. Enter und Leertaste loesen jetzt dasselbe aus wie ein Klick.
        this.resultsContainer.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            if (e.target.closest('a, button')) return;
            const card = e.target.closest('.document-card');
            if (!card) return;
            const idx = parseInt(card.getAttribute('data-index') || '-1', 10);
            if (idx < 0) return;
            e.preventDefault();
            this.openPreview(idx);
        });
    }

    _redirectIfLoginRequired(response) {
        if (response.status === 401) {
            window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
            return true;
        }
        return false;
    }

    _onResultsClick(e) {
        // Buttons und Links behalten ihr eigenes Verhalten.
        if (!e.target.closest('a, button')) {
            const openCard = e.target.closest('.document-card');
            if (openCard) {
                const idx = parseInt(openCard.getAttribute('data-index') || '-1', 10);
                if (idx >= 0) {
                    this.openPreview(idx);
                    return;
                }
            }
        }
    }

    /** Menschenlesbare Kopfzeile aus den Metadaten der Extraktion. */
    _previewMetaText(kind, meta) {
        const parts = [kind.toUpperCase()];
        if (meta) {
            if (meta.page_count) parts.push(`${meta.page_count} Seiten`);
            if (meta.word_count) parts.push(`${meta.word_count} Wörter`);
        }
        return parts.join(' · ');
    }

    /**
     * Absender und Empfaenger einer E-Mail als beschrifteter Kopf ueber dem
     * Text. In der Metazeile standen sie in einer Reihe mit Format und
     * Wortzahl -- bei einer Mail sind das aber Kopffelder, keine Metadaten,
     * und aneinandergereiht liest sie niemand.
     */
    _mailHeaderHtml(meta) {
        if (!meta) return '';
        const rows = [
            ['Von', meta['msg:from']],
            ['An', meta['msg:to']],
        ].filter(([, v]) => v);
        if (!rows.length) return '';
        const body = rows.map(([label, value]) =>
            `<div class="mail-header-label">${label}</div>`
            + `<div class="mail-header-value">${this.escapeHtml(String(value))}</div>`
        ).join('');
        return `<div class="mail-header">${body}</div>`;
    }

    _previewActionsHtml(doc) {
        const docId = doc.doc_id || '';
        const path = doc.path || '';
        const extRaw = doc.external_url ? String(doc.external_url).trim() : '';
        const externalUrl = /^https?:\/\//i.test(extRaw) ? extRaw : '';
        if (externalUrl) {
            const href = this.externalOpenHref(docId, path || docId);
            return `<a class="btn btn-success" href="${this.escapeAttr(href)}" target="_blank" rel="noopener noreferrer">${lucide('external-link')}In OneDrive öffnen</a>`;
        }
        // Der degradierte Download hing frueher als dritter Knopf an der Karte.
        // Mit den Karten-Aktionen waere er ersatzlos entfallen und
        // allowDegradedDownloadOpen ein Schalter ohne Wirkung geworden.
        const cfg = typeof window !== 'undefined' ? window.__DOCBRIDGE__ || {} : {};
        const download = cfg.allowDegradedDownloadOpen
            ? `<button type="button" class="btn btn-secondary" onclick="app.downloadDocument('${this.escapeJsString(docId)}', '${this.escapeJsString(path)}')">Download</button>`
            : '';
        return `<button type="button" class="btn btn-success" onclick="app.openDocument('${this.escapeJsString(docId)}', '${this.escapeJsString(path)}')">${lucide('external-link')}Öffnen</button>${download}`;
    }

    closePreview() {
        if (this.previewDialog.open) {
            this.previewDialog.close();   // loest 'close' aus -> _afterPreviewClosed
        } else {
            this._afterPreviewClosed();
        }
    }

    /** Aufraeumen nach dem Schliessen, egal ob per Escape, Button oder Backdrop. */
    _afterPreviewClosed() {
        if (this._previewAbort) {
            this._previewAbort.abort();
            this._previewAbort = null;
        }
        this._previewIndex = null;
        this._markActiveCard(null);
        this.previewBody.classList.remove('is-pdf');
        this.previewBody.innerHTML = '';
        this.previewActions.innerHTML = '';
        this.previewPosition.textContent = '';
    }

    /** Blaettert relativ zum aktuellen Treffer, ohne ueber die Enden zu laufen. */
    stepPreview(delta) {
        if (this._previewIndex == null) return;
        const next = this._previewIndex + delta;
        if (next < 0 || next >= this.currentResults.length) return;
        this.openPreview(next);
    }

    /** Zaehler und Pfeil-Zustaende an die Position anpassen. */
    _updatePreviewPosition(index) {
        const total = this.currentResults.length;
        this.previewPosition.textContent = total ? `${index + 1} von ${total}` : '';
        this.previewPrev.disabled = index <= 0;
        this.previewNext.disabled = index >= total - 1;
    }

    /** Hebt die Karte hervor, deren Dokument gerade im Panel steht. */
    _markActiveCard(index) {
        this.resultsContainer.querySelectorAll('.document-card.is-active')
            .forEach((el) => el.classList.remove('is-active'));
        if (index == null) return;
        const card = this.resultsContainer.querySelector(`.document-card[data-index="${index}"]`);
        if (card) card.classList.add('is-active');
    }

    async openPreview(index) {
        const doc = this.currentResults[index];
        if (!doc) return;

        // Laufende Anfrage abbrechen, damit ein schneller Kartenwechsel nicht
        // die Antwort des vorherigen Dokuments einblendet.
        if (this._previewAbort) this._previewAbort.abort();
        const controller = new AbortController();
        this._previewAbort = controller;
        this._previewIndex = index;

        const docId = String(doc.doc_id || doc.pointer || '');
        const path = String(doc.path || '');
        const title = this.displayTitle(doc);

        if (!this.previewDialog.open) {
            this.previewDialog.showModal();
        }
        this._markActiveCard(index);
        this._updatePreviewPosition(index);
        this.previewTitle.textContent = title;
        this.previewMeta.textContent = '';
        this.previewActions.innerHTML = this._previewActionsHtml(doc);
        this.previewBody.classList.remove('is-pdf');
        this.previewBody.innerHTML =
            '<div class="preview-skeleton"><span></span><span></span><span></span><span></span></div>';

        if (path.toLowerCase().endsWith('.pdf')) {
            const cfg = typeof window !== 'undefined' ? window.__DOCBRIDGE__ || {} : {};
            if (!cfg.pdfInlineInBrowser) {
                this.previewMeta.textContent = 'PDF';
                this.previewBody.innerHTML =
                    '<p class="preview-error">Die PDF-Vorschau ist deaktiviert. Nutzen Sie „Öffnen“.</p>';
                this._previewAbort = null;
                return;
            }
            const src = `/api/document/${encodeURIComponent(docId)}/preview?path=${encodeURIComponent(path)}`;
            try {
                const probe = await fetch(src, { method: 'GET', headers: { Range: 'bytes=0-0' },
                                                 credentials: 'same-origin', signal: controller.signal });
                if (this._redirectIfLoginRequired(probe)) return;
                if (this._previewIndex !== index) return;
                if (!probe.ok && probe.status !== 206) {
                    throw new Error(`HTTP ${probe.status}`);
                }
                this.previewMeta.textContent = 'PDF';
                this.previewBody.classList.add('is-pdf');
                this.previewBody.innerHTML =
                    `<iframe src="${this.escapeAttr(src)}" title="PDF-Vorschau"></iframe>`;
            } catch (error) {
                if (error.name === 'AbortError') return;
                this.previewBody.innerHTML =
                    `<p class="preview-error">Vorschau nicht verfügbar (${this.escapeHtml(error.message)}). Nutzen Sie „Öffnen“.</p>`;
            } finally {
                if (this._previewAbort === controller) this._previewAbort = null;
            }
            return;
        }

        try {
            const url = `/api/document/${encodeURIComponent(docId)}/preview-content?path=${encodeURIComponent(path)}`;
            const response = await fetch(url, {
                credentials: 'same-origin',
                signal: controller.signal,
            });
            if (this._redirectIfLoginRequired(response)) return;
            const data = await response.json().catch(() => ({}));
            // Zwischenzeitlicher Kartenwechsel: Antwort verwerfen, bevor sie
            // irgendetwas ins Panel schreibt -- Erfolg wie Fehler.
            if (this._previewIndex !== index) return;
            if (!response.ok || !data.success) {
                throw new Error(data.error || `HTTP ${response.status}`);
            }

            this.previewMeta.textContent = this._previewMetaText(data.kind, data.meta);
            this.previewBody.innerHTML = this._mailHeaderHtml(data.meta)
                + window.KnovasMarkdown.render(data.markdown);
        } catch (error) {
            if (error.name === 'AbortError') return;
            console.warn('Preview:', error);
            this.previewBody.innerHTML =
                `<p class="preview-error">Vorschau nicht verfügbar (${this.escapeHtml(error.message)}). Nutzen Sie „Öffnen“.</p>`;
        } finally {
            if (this._previewAbort === controller) this._previewAbort = null;
        }
    }

    /** @param {string} [queryOverride] Erweitert eine laufende Suche ("Mehr laden"). */
    async performSearch(queryOverride) {
        const query = String(queryOverride != null ? queryOverride : this.searchInput.value).trim();

        if (!query) {
            this.showError('Bitte geben Sie einen Suchbegriff ein.');
            return;
        }

        this.currentQuery = query;
        if (query !== this._limitQuery) {
            this._searchLimit = this._searchLimitBase;
            this._limitQuery = query;
        }
        this.showLoading();

        try {
            const response = await fetch('/api/search', {
                method: 'POST',
                credentials: 'same-origin',
                headers: this._jsonHeadersWithCsrf(),
                body: JSON.stringify({
                    query: query,
                    limit: this._searchLimit,
                    filters: {}
                })
            });
            if (this._redirectIfLoginRequired(response)) return;
            
            const data = await response.json().catch(() => ({}));
            
            if (!response.ok) {
                const msg = data.error || `${response.status} ${response.statusText}`;
                throw new Error(msg);
            }
            
            if (data.success) {
                if (data.onedrive_enrichment_loaded != null) {
                    this.onedriveEnrichmentLoaded = !!data.onedrive_enrichment_loaded;
                }
                this.currentResults = data.results || [];
                this.displayResults(data.results, data.total, data.semantix);
            } else {
                throw new Error(data.error || 'Suche fehlgeschlagen');
            }
            
        } catch (error) {
            console.error('Search error:', error);
            this.showError(`Fehler bei der Suche: ${error.message}`);
        } finally {
            this.hideLoading();
        }
    }

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

        // Nur anbieten, wenn die Antwort das Limit ausgeschoepft hat -- sonst
        // gibt es plausibel nichts mehr zu holen.
        const more = results.length >= this._searchLimit && this._searchLimit < 100;
        this.loadMoreButton.hidden = !more;
    }

    /** Up to maxSentences sentences from plain text (falls back to char limit). */
    firstSentencesExcerpt(text, maxSentences = 4, maxChars = 6000) {
        const raw = String(text || '').trim();
        if (!raw) return '';
        const parts = raw.split(/(?<=[.!?…])\s+/).filter((s) => s.trim().length > 0);
        let out = parts.slice(0, maxSentences).join(' ').trim();
        if (!out) {
            out = raw.length > maxChars ? raw.substring(0, maxChars) + '…' : raw;
        } else if (out.length > maxChars) {
            out = out.substring(0, maxChars) + '…';
        }
        return out;
    }

    /**
     * Soft-truncate an LLM summary at a char cap. Preserves the abstractive
     * summary the server produced instead of slicing it to the first N sentences
     * (which reads exactly like the document's opening for extractive-style LLM
     * output). Keep the char cap ≤ the server-side LLM_SUMMARIZE_MAX_OUTPUT_CHARS.
     */
    capSummaryLength(text, maxChars = 2000) {
        const raw = String(text || '').trim();
        if (!raw) return '';
        if (raw.length <= maxChars) return raw;
        return raw.substring(0, maxChars - 1).trimEnd() + '…';
    }

    /**
     * Human-readable title: corpus pointers often ship a run-on "title" from ingestion;
     * prefer the filename stem (e.g. corpus/foo/Infocuria.txt → Infocuria).
     */
    displayTitle(doc) {
        const path = String(doc.path || doc.doc_id || '').replace(/\\/g, '/').trim();
        const base = path ? path.split('/').pop() : '';
        const stem = base.replace(/\.[^./]+$/, '') || base;
        const raw = String(doc.title || '').trim();
        if (stem && (!raw || raw.length > 100 || raw === path || raw.toLowerCase().startsWith(stem.toLowerCase() + ' '))) {
            return stem || raw || path || 'Unbenanntes Dokument';
        }
        return raw || stem || path || 'Unbenanntes Dokument';
    }

    /** Knovas /secured/query: string or { present, text }. */
    ingestedSummaryText(doc) {
        const v = doc.ingested_summary ?? doc.ingestedSummary;
        if (typeof v === 'string') return v.trim();
        if (v && typeof v === 'object' && v.present !== false) {
            const t = v.text ?? v.summary ?? v.content;
            if (typeof t === 'string') return t.trim();
        }
        return '';
    }

    hasContextPreview(doc) {
        return Boolean(
            (doc.first_page_preview && String(doc.first_page_preview).trim()) ||
            (doc.context_snippet && (doc.context_snippet.match || doc.context_snippet.before || doc.context_snippet.after)),
        );
    }

    _buildContextSnippetHtml(snippet) {
        if (!snippet || typeof snippet !== 'object') return '';
        const before = this.escapeHtml(String(snippet.before || '').trim());
        const match = this.escapeHtml(String(snippet.match || '').trim());
        const after = this.escapeHtml(String(snippet.after || '').trim());
        if (!before && !match && !after) return '';
        const matchHtml = match ? `<mark class="context-snippet-match">${match}</mark>` : '';
        return `<div class="document-context-snippet-text">${before}${before && matchHtml ? ' ' : ''}${matchHtml}${matchHtml && after ? ' ' : ''}${after}</div>`;
    }

    _buildFirstPageHtml(text) {
        const raw = String(text || '').trim();
        if (!raw) return '';
        return `<div class="document-first-page-text">${this.escapeHtml(this.capSummaryLength(raw, 4000))}</div>`;
    }

    /** Nur das Datum, ohne Uhrzeit -- die Minute sagt beim Auswaehlen nichts. */
    _formatDateShort(dateString) {
        try {
            return new Date(dateString).toLocaleDateString('de-DE', {
                year: 'numeric', month: '2-digit', day: '2-digit',
            });
        } catch {
            return dateString;
        }
    }

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
            return `<div class="document-thumb document-thumb--loading"><img loading="lazy"`
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

    createDocumentCard(doc, index) {
        const card = document.createElement('div');
        card.className = 'document-card';
        card.setAttribute('tabindex', '0');
        card.setAttribute('data-index', index);
        
        const title = this.displayTitle(doc);
        const docId = doc.doc_id || 'N/A';
        const path = doc.path || '';
        const extRaw = doc.external_url ? String(doc.external_url).trim() : '';
        const externalUrl = /^https?:\/\//i.test(extRaw) ? extRaw : '';
        const hasOneDrive =
            Boolean(externalUrl) ||
            doc.open_mode === 'external' ||
            !!doc.onedrive_open_available ||
            (this.onedriveEnrichmentLoaded && path);
        const localAvailable =
            path &&
            !hasOneDrive &&
            (doc.file_exists === true ||
                (doc.file_exists == null && doc.can_open === true));
        // Keine Aktionen auf der Karte. Die Karte selbst oeffnet die Vorschau
        // (_onResultsClick), und dort steht "Oeffnen" bzw. "In OneDrive
        // oeffnen" -- der Weg geht also nicht verloren, er liegt eine Ebene
        // tiefer. Knoepfe hier haben ihn verdeckt: _onResultsClick ueberspringt
        // Klicks auf a und button, sie konkurrierten also mit genau der Geste,
        // die der Nutzer lernen soll.
        const actionsHtml = localAvailable || hasOneDrive
            ? ''
            : '<span class="badge badge-error">Datei nicht verf\u00fcgbar</span>';

        const documentDate = doc.document_date || doc.date || doc.timestamp || doc.created_at || null;
        // Nur Format und Datum: die Dokumentart steht meist schon im Titel,
        // und drei Angaben nebeneinander lesen sich als Datenzeile statt als
        // Einordnung.
        const metaParts = [];
        const fmt = this._formatLabel(path);
        if (fmt) metaParts.push(fmt);
        if (documentDate) metaParts.push(this.escapeHtml(this._formatDateShort(documentDate)));

        card.innerHTML = `
            ${this._thumbHtml(doc, docId, path, title, localAvailable)}
            <div class="document-body">
                <div class="document-headline">
                    <div class="document-headline-text">
                        ${metaParts.length ? `<div class="document-metaline">${metaParts.join(' · ')}</div>` : ''}
                        <div class="document-title">${this.escapeHtml(title)}</div>
                    </div>
                    ${actionsHtml ? `<div class="document-actions">${actionsHtml}</div>` : ''}
                </div>
                ${this._snippetHtml(doc)}
            </div>
        `;

        return card;
    }
    
    async openDocument(docId, pathOrBrowserFlag, browserOrCompanionFlag, companionFlag) {
        let path = pathOrBrowserFlag;
        let useBrowserClientOpen = browserOrCompanionFlag;
        let useCompanion = companionFlag;
        // Legacy signature: openDocument(docId, useBrowser, useCompanion) — path === docId
        if (typeof pathOrBrowserFlag === 'boolean') {
            path = docId;
            useBrowserClientOpen = pathOrBrowserFlag;
            useCompanion = browserOrCompanionFlag;
        }
        const cfg = typeof window !== 'undefined' ? window.__DOCBRIDGE__ || {} : {};
        const browserOpen = useBrowserClientOpen === true || !!cfg.browserClientOpenEnabled;
        const companionOpen = useCompanion === true || !!cfg.companionEnabled;
        const onHttps = typeof window !== 'undefined' && window.location.protocol === 'https:';

        // Companion first on HTTPS — browsers block UNC/file: launches.
        if (onHttps && companionOpen) {
            return this.openDocumentCompanion(docId, path);
        }
        if (browserOpen) {
            return this.openDocumentOnClient(docId, path);
        }
        if (companionOpen) {
            return this.openDocumentCompanion(docId, path);
        }

        try {
            const response = await fetch(`/api/document/${encodeURIComponent(docId)}/open`, {
                method: 'POST',
                credentials: 'same-origin',
                headers: this._jsonHeadersWithCsrf(),
                body: JSON.stringify({ path: path }),
            });
            if (this._redirectIfLoginRequired(response)) return;

            const data = await response.json();

            if (data.success) {
                this.showSuccess('Dokument wird geöffnet...');
            } else {
                throw new Error(data.error || 'Fehler beim Öffnen');
            }
        } catch (error) {
            console.error('Error opening document:', error);
            this.showError(`Fehler beim Öffnen: ${error.message}`);
        }
    }

    /**
     * Open a file on the user's PC using a UNC or local path (no companion install).
     * Works only on HTTP/intranet where the browser allows file/UNC navigation.
     * On HTTPS, UNC/file launches are blocked — use Companion or copy the path manually.
     */
    async openDocumentOnClient(docId, path) {
        try {
            const url =
                `/api/document/${encodeURIComponent(docId)}/client-path?path=${encodeURIComponent(path)}`;
            const response = await fetch(url, { credentials: 'same-origin' });
            if (this._redirectIfLoginRequired(response)) return;
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.success) {
                throw new Error(data.error || `HTTP ${response.status}`);
            }
            const pathHint = data.unc ? String(data.unc) : data.path ? String(data.path) : '';
            const onHttps = window.location.protocol === 'https:';

            if (pathHint) {
                await this._copyTextOptional(pathHint);
            }

            if (onHttps) {
                this.showSuccess(
                    pathHint
                        ? `HTTPS blockiert direktes Öffnen. UNC-Pfad kopiert — Win+R, Einfügen, Enter: ${pathHint}`
                        : 'HTTPS blockiert direktes Öffnen. Installieren Sie den Open Companion.',
                );
                return;
            }

            const launched = this._launchClientFile(data.unc, data.path);
            if (launched) {
                this.showSuccess(
                    pathHint
                        ? `Dokument wird geöffnet… (${pathHint})`
                        : 'Dokument wird auf Ihrem Rechner geöffnet…',
                );
            } else {
                throw new Error('Browser konnte den lokalen Pfad nicht starten');
            }
        } catch (error) {
            console.error('Client-path open:', error);
            this.showError(
                `Öffnen fehlgeschlagen: ${error.message}. ` +
                    'Prüfen Sie Zugriff auf die Freigabe auf diesem PC und ggf. Browser-Richtlinien (siehe docs/integration/opening-documents.md).',
            );
        }
    }

    _launchClientFile(unc, clientPath) {
        // Browsers block file:// to UNC/network paths from HTTPS origins.
        if (window.location.protocol === 'https:') {
            return false;
        }
        const hrefs = [];
        if (unc && String(unc).startsWith('\\\\')) {
            hrefs.push(String(unc));
        }
        if (clientPath) {
            const p = String(clientPath);
            if (p.startsWith('/')) {
                hrefs.push('file://' + p);
            }
            hrefs.push(p);
        }
        for (const href of hrefs) {
            try {
                const a = document.createElement('a');
                a.href = href;
                a.rel = 'noopener';
                a.style.display = 'none';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                return true;
            } catch (e) {
                console.warn('Launch attempt failed:', href, e);
            }
        }
        return false;
    }

    async _copyTextOptional(text) {
        if (!text || !navigator.clipboard || !navigator.clipboard.writeText) {
            return false;
        }
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (e) {
            console.warn('Clipboard copy failed:', e);
            return false;
        }
    }

    async openDocumentCompanion(docId, path) {
        try {
            const response = await fetch('/api/open-tokens/mint', {
                method: 'POST',
                credentials: 'same-origin',
                headers: this._jsonHeadersWithCsrf(),
                body: JSON.stringify({ doc_id: docId, path }),
            });
            if (this._redirectIfLoginRequired(response)) return;
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.success) {
                throw new Error(data.error || `HTTP ${response.status}`);
            }
            if (data.companion_href) {
                window.location.href = data.companion_href;
                return;
            }
            throw new Error('Kein companion_href in Antwort');
        } catch (error) {
            console.error('Companion open:', error);
            this.showError(
                `Öffnen über Companion fehlgeschlagen: ${error.message}. ` +
                    'Ist der Knovas Open Companion auf diesem Rechner installiert? Öffnen erfolgt lokal auf dem Client, nicht auf dem Server. Siehe docs/integration/opening-documents.md.',
            );
        }
    }
    
    async downloadDocument(docId, path) {
        try {
            const idSeg = encodeURIComponent(docId);
            window.location.href = `/api/document/${idSeg}/download?path=${encodeURIComponent(path)}`;
            this.showSuccess('Download wird gestartet...');
        } catch (error) {
            console.error('Error downloading document:', error);
            this.showError(`Download fehlgeschlagen: ${error.message}`);
        }
    }
    
    async checkHealth() {
        try {
            const response = await fetch('/api/health', { credentials: 'same-origin' });
            const data = await response.json();
            const status = data.semantix_api ? 'Online' : 'Offline';
            this.showToast(
                `Systemstatus\nWeb-Oberfläche: Online\nKnovas API: ${status}\nZeitstempel: ${data.timestamp}`,
                data.semantix_api ? 'success' : 'error',
            );
        } catch (error) {
            this.showToast(`Systemstatus konnte nicht geladen werden: ${error.message}`, 'error');
        }
    }
    
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
    
    /** Wie lange ein Toast stehen bleibt, bevor er sich selbst entfernt. */
    static TOAST_TIMEOUT_MS = { error: 10000, success: 6000, info: 6000 };

    /**
     * Einziger Weg, dem Nutzer etwas mitzuteilen. Jeder Toast verschwindet von
     * selbst; Fehler bekommen laenger Zeit, weil sie mehr Text tragen und
     * gelesen werden wollen. Wer schneller ist, klickt das x.
     * @param {'info'|'success'|'error'} kind
     */
    showToast(message, kind = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast toast--${kind}`;

        const text = document.createElement('div');
        text.className = 'toast-text';
        text.textContent = message;

        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'toast-close';
        close.setAttribute('aria-label', 'Meldung schliessen');
        close.textContent = '×';
        close.addEventListener('click', () => toast.remove());

        toast.appendChild(text);
        toast.appendChild(close);
        this.toastContainer.appendChild(toast);

        const timeout = DocumentSearchApp.TOAST_TIMEOUT_MS[kind]
            || DocumentSearchApp.TOAST_TIMEOUT_MS.info;
        window.setTimeout(() => toast.remove(), timeout);
    }

    showError(message) {
        this.showToast(message, 'error');
    }

    showSuccess(message) {
        this.showToast(message, 'success');
    }

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
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /** Same-origin redirect; server resolves OneDrive webUrl from enrichment JSONL. */
    externalOpenHref(docId, path) {
        const idSeg = encodeURIComponent(docId || '');
        const pathSeg = encodeURIComponent(path || docId || '');
        return `/api/document/${idSeg}/external-open?path=${pathSeg}`;
    }
    
    /** Escape for use inside double-quoted HTML attributes (e.g. href). */
    escapeAttr(text) {
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/</g, '&lt;')
            .replace(/\r|\n/g, ' ');
    }
    
    /** Escape for single-quoted JavaScript string literals in inline handlers. */
    escapeJsString(text) {
        return String(text)
            .replace(/\\/g, '\\\\')
            .replace(/'/g, "\\'")
            .replace(/\r|\n/g, ' ');
    }
    
    formatDate(dateString) {
        try {
            const date = new Date(dateString);
            return date.toLocaleDateString('de-DE', {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit'
            });
        } catch {
            return dateString;
        }
    }
    
    formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        
        return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
    }
}

// Initialize app when DOM is ready
let app;
document.addEventListener('DOMContentLoaded', () => {
    app = new DocumentSearchApp();
});
