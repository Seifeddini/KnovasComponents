// Knovas Document Search - JavaScript

class DocumentSearchApp {
    constructor() {
        this.searchInput = document.getElementById('searchInput');
        this.searchButton = document.getElementById('searchButton');
        this.resultsSection = document.getElementById('resultsSection');
        this.resultsContainer = document.getElementById('resultsContainer');
        this.resultsCount = document.getElementById('resultsCount');
        this.loadingIndicator = document.getElementById('loadingIndicator');
        this.errorMessage = document.getElementById('errorMessage');
        this.exactMatch = document.getElementById('exactMatch');
        this.resultsPerPage = document.getElementById('resultsPerPage');
        
        this.currentQuery = '';
        this.currentResults = [];
        /** @type {string|null} Knovas query_session_id from last /secured/query (for relevance feedback). */
        this.querySessionId = null;
        
        this.initializeEventListeners();
    }
    
    initializeEventListeners() {
        this.searchButton.addEventListener('click', () => this.performSearch());
        
        this.searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.performSearch();
            }
        });
        
        document.getElementById('healthCheck').addEventListener('click', (e) => {
            e.preventDefault();
            this.checkHealth();
        });

        this.resultsContainer.addEventListener('click', (e) => this._onResultsClick(e));
    }

    _redirectIfLoginRequired(response) {
        if (response.status === 401) {
            window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
            return true;
        }
        return false;
    }

    /**
     * @param {string} pointer
     * @param {'relevance'|'importance'|'quality'} kind
     */
    _setScoreSelection(wrap, kind, value) {
        wrap.querySelectorAll(`.js-rating-score[data-kind="${kind}"]`).forEach((btn) => {
            const on = parseInt(btn.dataset.value, 10) === value;
            btn.classList.toggle('selected', on);
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
    }

    _onResultsClick(e) {
        const scoreBtn = e.target.closest('.js-rating-score');
        if (scoreBtn) {
            const wrap = scoreBtn.closest('.document-ratings');
            if (!wrap) return;
            const pointer = wrap.getAttribute('data-pointer');
            if (!pointer) return;
            const kind = scoreBtn.dataset.kind;
            const value = parseInt(scoreBtn.dataset.value, 10);
            if (!kind || value < 1 || value > 5) return;

            wrap.querySelectorAll(`.js-rating-score[data-kind="${kind}"]`).forEach((btn) => {
                btn.classList.remove('selected');
            });
            scoreBtn.classList.add('selected');
            wrap.querySelectorAll(`.js-rating-score[data-kind="${kind}"]`).forEach((btn) => {
                btn.setAttribute('aria-pressed', btn.classList.contains('selected') ? 'true' : 'false');
            });

            if (kind === 'relevance') {
                const hint = wrap.querySelector('.js-relevance-hint');
                this._postRelevanceFeedback(pointer, value, hint);
            }
            return;
        }

        const saveBtn = e.target.closest('.js-save-doc-rating');
        if (saveBtn) {
            const wrap = saveBtn.closest('.document-ratings');
            if (!wrap) return;
            const pointer = wrap.getAttribute('data-pointer');
            const hint = wrap.querySelector('.js-permanent-hint');
            this._savePermanentDocumentRating(wrap, pointer, hint);
            return;
        }

        const loadBtn = e.target.closest('.js-load-doc-rating');
        if (loadBtn) {
            const wrap = loadBtn.closest('.document-ratings');
            if (!wrap) return;
            const pointer = wrap.getAttribute('data-pointer');
            const hint = wrap.querySelector('.js-permanent-hint');
            this._loadPermanentDocumentRating(wrap, pointer, hint);
        }
    }

    async _postRelevanceFeedback(pointer, relevanceScore, hintEl) {
        if (!this.querySessionId) {
            if (hintEl) {
                hintEl.textContent = 'Keine Such-Session — Relevanz kann nicht gemeldet werden.';
                hintEl.classList.add('rating-hint-error');
            }
            return;
        }
        if (hintEl) {
            hintEl.textContent = 'Sende…';
            hintEl.classList.remove('rating-hint-error', 'rating-hint-ok');
        }
        try {
            const response = await fetch('/api/analytics/relevance-feedback', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    pointer,
                    relevance_score: relevanceScore,
                    query_session_id: this.querySessionId,
                }),
            });
            if (this._redirectIfLoginRequired(response)) return;
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `${response.status}`);
            }
            if (hintEl) {
                hintEl.textContent = 'Relevanz für diese Suche gemeldet.';
                hintEl.classList.add('rating-hint-ok');
            }
        } catch (err) {
            console.warn('Relevance feedback:', err);
            if (hintEl) {
                hintEl.textContent = 'Konnte nicht senden (Knovas API).';
                hintEl.classList.add('rating-hint-error');
            }
        }
    }

    _readSelectedScore(wrap, kind) {
        const sel = wrap.querySelector(`.js-rating-score[data-kind="${kind}"].selected`);
        return sel ? parseInt(sel.dataset.value, 10) : null;
    }

    async _savePermanentDocumentRating(wrap, pointer, hintEl) {
        if (!pointer) return;
        const imp = this._readSelectedScore(wrap, 'importance');
        const qual = this._readSelectedScore(wrap, 'quality');
        if (imp == null && qual == null) {
            if (hintEl) {
                hintEl.textContent = 'Bitte Wichtigkeit und/oder Qualität wählen.';
                hintEl.classList.add('rating-hint-error');
            }
            return;
        }
        if (hintEl) {
            hintEl.textContent = 'Speichere…';
            hintEl.classList.remove('rating-hint-error', 'rating-hint-ok');
        }
        try {
            const body = { pointer };
            if (imp != null) body.importance_score = imp;
            if (qual != null) body.quality_score = qual;
            const response = await fetch('/api/document/rating', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (this._redirectIfLoginRequired(response)) return;
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `${response.status}`);
            }
            if (hintEl) {
                hintEl.textContent = 'Dauerhafte Bewertung gespeichert.';
                hintEl.classList.add('rating-hint-ok');
            }
        } catch (err) {
            console.warn('Document rating:', err);
            if (hintEl) {
                hintEl.textContent = 'Konnte nicht speichern (Knovas API).';
                hintEl.classList.add('rating-hint-error');
            }
        }
    }

    async _loadPermanentDocumentRating(wrap, pointer, hintEl) {
        if (!pointer) return;
        if (hintEl) {
            hintEl.textContent = 'Lade…';
            hintEl.classList.remove('rating-hint-error', 'rating-hint-ok');
        }
        try {
            const url = `/api/document/rating?pointer=${encodeURIComponent(pointer)}`;
            const response = await fetch(url, { credentials: 'same-origin' });
            if (this._redirectIfLoginRequired(response)) return;
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `${response.status}`);
            }
            const r = data.rating;
            if (r && typeof r === 'object') {
                if (r.importance_score != null) {
                    this._setScoreSelection(wrap, 'importance', parseInt(r.importance_score, 10));
                }
                if (r.quality_score != null) {
                    this._setScoreSelection(wrap, 'quality', parseInt(r.quality_score, 10));
                }
            }
            const rf = data.relevance_feedback;
            let extra = '';
            if (rf && typeof rf === 'object' && rf.total_ratings > 0) {
                extra = ` — Historie: Ø ${Number(rf.avg_relevance).toFixed(2)} (${rf.total_ratings}×)`;
            }
            if (hintEl) {
                hintEl.textContent = (r ? 'Stand geladen.' : 'Keine dauerhafte Bewertung gesetzt.') + extra;
                hintEl.classList.add('rating-hint-ok');
            }
        } catch (err) {
            console.warn('Load document rating:', err);
            if (hintEl) {
                hintEl.textContent = 'Konnte nicht laden (Knovas API).';
                hintEl.classList.add('rating-hint-error');
            }
        }
    }

    /** Five score buttons 1–5 for one dimension (relevance, importance, or quality). */
    _scorePickerHtml(kind, disabled) {
        const parts = [];
        for (let v = 1; v <= 5; v++) {
            const d = disabled ? ' disabled' : '';
            parts.push(
                `<button type="button" class="score-btn js-rating-score" data-kind="${kind}" data-value="${v}"${d} aria-pressed="false">${v}</button>`
            );
        }
        return `<div class="score-picker" role="group"${disabled ? ' aria-disabled="true"' : ''}>${parts.join('')}</div>`;
    }

    /**
     * @param {string} pointer Raw Knovas document pointer (doc_id)
     */
    _buildRatingsSection(pointer) {
        const safePointer = this.escapeAttr(pointer);
        const hasSession = Boolean(this.querySessionId);
        const relevanceHint = hasSession
            ? 'Tip: Wählen Sie 1 (nicht relevant) bis 5 (sehr relevant) für diese Suche.'
            : 'Nur verfügbar, wenn die Suche eine Knovas-Session-ID geliefert hat (gesicherter Modus).';
        const relevanceDisabled = !hasSession;

        return `
            <div class="document-ratings" data-pointer="${safePointer}">
                <div class="rating-section">
                    <div class="rating-section-title">Relevanz für diese Suche</div>
                    <p class="rating-help">${this.escapeHtml(relevanceHint)}</p>
                    ${this._scorePickerHtml('relevance', relevanceDisabled)}
                    <span class="rating-hint js-relevance-hint" aria-live="polite"></span>
                </div>
                <div class="rating-section">
                    <div class="rating-section-title">Dauerhafte Bewertung (Mandant)</div>
                    <p class="rating-help">Wichtigkeit und Qualität des Dokuments — unabhängig von einzelnen Suchanfragen (Knovas speichert pro Dokument).</p>
                    <div class="permanent-rating-grid">
                        <div>
                            <div class="sub-label">Wichtigkeit (1–5)</div>
                            ${this._scorePickerHtml('importance', false)}
                        </div>
                        <div>
                            <div class="sub-label">Qualität (1–5)</div>
                            ${this._scorePickerHtml('quality', false)}
                        </div>
                    </div>
                    <div class="rating-actions">
                        <button type="button" class="btn btn-secondary btn-sm js-save-doc-rating">Speichern</button>
                        <button type="button" class="btn-text js-load-doc-rating">Aktuellen Stand laden</button>
                    </div>
                    <span class="rating-hint js-permanent-hint" aria-live="polite"></span>
                </div>
            </div>
        `;
    }
    
    async performSearch() {
        const query = this.searchInput.value.trim();
        
        if (!query) {
            this.showError('Bitte geben Sie einen Suchbegriff ein.');
            return;
        }
        
        this.currentQuery = query;
        this.showLoading();
        this.hideError();
        
        try {
            const response = await fetch('/api/search', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    query: query,
                    limit: parseInt(this.resultsPerPage.value),
                    filters: {
                        exact_match: this.exactMatch.checked
                    }
                })
            });
            if (this._redirectIfLoginRequired(response)) return;
            
            const data = await response.json().catch(() => ({}));
            
            if (!response.ok) {
                const msg = data.error || `${response.status} ${response.statusText}`;
                throw new Error(msg);
            }
            
            if (data.success) {
                this.currentResults = data.results || [];
                const sx = data.semantix;
                this.querySessionId =
                    sx && sx.query_session_id != null && String(sx.query_session_id).trim()
                        ? String(sx.query_session_id).trim()
                        : null;
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
    
    displayResults(results, total, semantix) {
        this.resultsSection.style.display = 'block';
        this.resultsContainer.innerHTML = '';
        this._renderKnovasBanner(semantix);
        
        if (!results || results.length === 0) {
            this.showEmptyState(semantix);
            return;
        }
        
        this.resultsCount.textContent = `${results.length} von ${total || results.length} Ergebnissen`;
        
        results.forEach((doc, index) => {
            const card = this.createDocumentCard(doc, index);
            this.resultsContainer.appendChild(card);
        });
    }
    
    _renderKnovasBanner(semantix) {
        const el = document.getElementById('semantixResponseBanner');
        if (!el) return;
        if (!semantix || typeof semantix !== 'object') {
            el.style.display = 'none';
            el.innerHTML = '';
            return;
        }
        const status = semantix.status != null ? String(semantix.status) : '';
        const message = semantix.message != null ? String(semantix.message) : '';
        const rc = semantix.result_count;
        const pointers = Array.isArray(semantix.pointers) ? semantix.pointers : [];
        const lines = [];
        if (status || message) {
            lines.push(`<strong>Knovas</strong>: ${this.escapeHtml(status)}${status && message ? ' — ' : ''}${this.escapeHtml(message)}`);
        }
        if (rc != null && rc !== '') {
            lines.push(`<span class="semantix-rc">Treffer laut API: ${this.escapeHtml(String(rc))}</span>`);
        }
        if (pointers.length) {
            const plist = pointers.map((p) => this.escapeHtml(String(p))).join(', ');
            lines.push(`<span class="semantix-pointers">Zeiger: ${plist}</span>`);
        }
        const qsid = semantix.query_session_id;
        if (qsid != null && qsid !== '') {
            lines.push(`<span class="semantix-session">Session: ${this.escapeHtml(String(qsid))}</span>`);
        }
        if (!lines.length) {
            el.style.display = 'none';
            el.innerHTML = '';
            return;
        }
        el.style.display = 'block';
        el.innerHTML = `<div class="semantix-response-inner">${lines.join('<br>')}</div>`;
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

    createDocumentCard(doc, index) {
        const card = document.createElement('div');
        card.className = 'document-card';
        card.setAttribute('data-index', index);
        
        const title = this.displayTitle(doc);
        const docId = doc.doc_id || 'N/A';
        const aktenId = doc.akten_id || 'N/A';
        const docType = doc.type || doc.doc_type || 'Unbekannt';
        const desc = doc.description ? String(doc.description).trim() : '';
        const ingestedSummary = this.ingestedSummaryText(doc);
        const fromKnovas = (doc.snippet || doc.content || '').trim();
        const snippetSource = fromKnovas || desc;
        const showSummaryBlock = Boolean(ingestedSummary);
        const path = doc.path || '';
        const extRaw = doc.external_url ? String(doc.external_url).trim() : '';
        const externalUrl = /^https?:\/\//i.test(extRaw) ? extRaw : '';
        const localAvailable = doc.file_exists === true && path && !externalUrl;
        const cfg = typeof window !== 'undefined' ? window.__DOCBRIDGE__ || {} : {};
        const useBrowserClientOpen =
            !!cfg.browserClientOpenEnabled && doc.open_via_browser === true;
        const useCompanion =
            !useBrowserClientOpen && !!cfg.companionEnabled && doc.open_via_companion === true;
        const isPdf = path.toLowerCase().endsWith('.pdf');
        const canPreviewPdf = !!cfg.pdfInlineInBrowser && isPdf;
        const showDegradedDownload = !!cfg.allowDegradedDownloadOpen;

        let actionsHtml;
        if (externalUrl) {
            actionsHtml = `
                <a class="btn btn-success" href="${this.escapeAttr(externalUrl)}" target="_blank" rel="noopener noreferrer">
                    🔗 In OneDrive öffnen
                </a>
            `;
        } else if (localAvailable) {
            const previewBtn = canPreviewPdf
                ? `<a class="btn btn-outline" target="_blank" rel="noopener noreferrer" href="/api/document/${encodeURIComponent(docId)}/preview?path=${encodeURIComponent(path)}">PDF Vorschau</a>`
                : '';
            const downloadBtn = showDegradedDownload
                ? `<button type="button" class="btn btn-secondary" onclick="app.downloadDocument('${this.escapeJsString(docId)}', '${this.escapeJsString(path)}')">
                    💾 Download (degradiert)
                </button>`
                : '';
            actionsHtml = `
                <button type="button" class="btn btn-success" onclick="app.openDocument('${this.escapeJsString(docId)}', '${this.escapeJsString(path)}', ${useBrowserClientOpen ? 'true' : 'false'}, ${useCompanion ? 'true' : 'false'})">
                    📂 Öffnen
                </button>
                ${previewBtn}
                ${downloadBtn}
            `;
        } else {
            actionsHtml = `<span class="badge badge-error">Datei nicht verfügbar</span>`;
        }
        
        const defaultNoPreview = 'Keine Vorschau verfügbar';
        let snippetHtml = '';
        let showSnippetSection = true;
        if (snippetSource) {
            snippetHtml = this.escapeHtml(this.firstSentencesExcerpt(snippetSource, 4));
        } else if (showSummaryBlock) {
            showSnippetSection = false;
        } else {
            snippetHtml = this.escapeHtml(defaultNoPreview);
        }
        const summaryStr = showSummaryBlock
            ? this.firstSentencesExcerpt(ingestedSummary, 4, 1200)
            : '';
        const summaryHtml = summaryStr ? this.escapeHtml(summaryStr) : '';
        
        const pageNum = doc.page_number != null && doc.page_number !== '' ? doc.page_number : doc.page;
        const sentNum = doc.sentence_number;
        const hasLocation = (pageNum != null && pageNum !== '') || (sentNum != null && sentNum !== '');
        let locationLine = '';
        if (hasLocation) {
            const parts = [];
            if (pageNum != null && pageNum !== '') parts.push(`Seite ${this.escapeHtml(String(pageNum))}`);
            if (sentNum != null && sentNum !== '') parts.push(`Satz ${this.escapeHtml(String(sentNum))}`);
            locationLine = `<div class="document-location">${parts.join(' · ')}</div>`;
        }
        const cosSim = doc.cosine_similarity;
        const cosDist = doc.cosine_distance;
        let metricsLine = '';
        if ((cosSim != null && cosSim !== '') || (cosDist != null && cosDist !== '')) {
            const bits = [];
            if (cosSim != null && cosSim !== '') bits.push(`cos θ ${this.escapeHtml(String(cosSim))}`);
            if (cosDist != null && cosDist !== '') bits.push(`Distanz ${this.escapeHtml(String(cosDist))}`);
            metricsLine = `<div class="document-metrics">${bits.join(' · ')}</div>`;
        }
        
        const documentDate = doc.document_date || doc.date || doc.timestamp || doc.created_at || null;
        const fileModified = doc.modified_at || null;
        const pointer =
            doc.doc_id != null && String(doc.doc_id).trim() ? String(doc.doc_id).trim() : '';

        card.innerHTML = `
            <div class="document-header">
                <div>
                    <div class="document-title">${this.escapeHtml(title)}</div>
                    <div class="document-id">ID: ${this.escapeHtml(docId)}</div>
                    ${locationLine}
                    ${metricsLine}
                </div>
                <div class="document-actions">
                    ${actionsHtml}
                </div>
            </div>
            
            <div class="document-meta">
                <div class="meta-item">
                    <span>📁</span>
                    <strong>Akten-ID:</strong> ${this.escapeHtml(aktenId)}
                </div>
                <div class="meta-item">
                    <span>📄</span>
                    <strong>Typ:</strong> ${this.escapeHtml(docType)}
                </div>
                ${documentDate ? `
                    <div class="meta-item">
                        <span>📅</span>
                        <strong>Dokumentdatum:</strong> ${this.formatDate(documentDate)}
                    </div>
                ` : ''}
                ${fileModified ? `
                    <div class="meta-item">
                        <span>🕐</span>
                        <strong>Datei geändert:</strong> ${this.formatDate(fileModified)}
                    </div>
                ` : ''}
                ${doc.file_size ? `
                    <div class="meta-item">
                        <span>📊</span>
                        <strong>Größe:</strong> ${this.formatFileSize(doc.file_size)}
                    </div>
                ` : ''}
            </div>
            
            ${summaryHtml ? `
            <div class="document-ingested-summary" role="region" aria-label="Dokumentzusammenfassung">
                <div class="document-ingested-summary-label">Zusammenfassung</div>
                <div class="document-ingested-summary-text">${summaryHtml}</div>
            </div>
            ` : ''}
            ${showSnippetSection ? `
            <div class="document-snippet"${fromKnovas ? ` aria-label="Trefferausschnitt"` : ''}>
                ${snippetHtml}
            </div>
            ` : ''}
            ${pointer ? this._buildRatingsSection(pointer) : ''}
        `;
        
        return card;
    }
    
    async openDocument(docId, path, useBrowserClientOpen, useCompanion) {
        if (useBrowserClientOpen === true) {
            return this.openDocumentOnClient(docId, path);
        }
        if (useCompanion === true) {
            return this.openDocumentCompanion(docId, path);
        }
        try {
            const response = await fetch(`/api/document/${encodeURIComponent(docId)}/open`, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                },
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
            this.showError(`Dokument konnte nicht geöffnet werden: ${error.message}`);
        }
    }

    /**
     * Open a file on the user's PC using a UNC or local path (no companion install).
     * Requires the client machine to have the same share mounted / accessible.
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
            const launched = this._launchClientFile(data.unc, data.path);
            if (launched) {
                this.showSuccess('Dokument wird auf Ihrem Rechner geöffnet…');
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
        const hrefs = [];
        if (unc && String(unc).startsWith('\\\\')) {
            const fileUri =
                'file:///' + String(unc).slice(2).replace(/\\/g, '/');
            hrefs.push(fileUri, String(unc));
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

    async openDocumentCompanion(docId, path) {
        const cfg = window.__DOCBRIDGE__ || {};
        const csrf = cfg.csrfToken || '';
        try {
            const response = await fetch('/api/open-tokens/mint', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': csrf,
                },
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
            
            const status = data.semantix_api ? '✅ Online' : '❌ Offline';
            alert(`System Status:\n\nWeb Interface: ✅ Online\nKnovas API: ${status}\n\nZeitstempel: ${data.timestamp}`);
            
        } catch (error) {
            alert(`System Status:\n\n❌ Verbindungsfehler: ${error.message}`);
        }
    }
    
    showLoading() {
        // Loading markup lives inside #resultsSection, which starts hidden — show it
        // so the first search (and any search) displays the spinner immediately.
        this.resultsSection.style.display = 'block';
        this.resultsSection.setAttribute('aria-busy', 'true');
        this.loadingIndicator.style.display = 'block';
        this.resultsContainer.style.display = 'none';
        const banner = document.getElementById('semantixResponseBanner');
        if (banner) {
            banner.style.display = 'none';
            banner.innerHTML = '';
        }
        if (this.resultsCount) {
            this.resultsCount.textContent = '';
        }
        this.searchButton.disabled = true;
    }
    
    hideLoading() {
        this.loadingIndicator.style.display = 'none';
        this.resultsContainer.style.display = 'flex';
        this.resultsSection.setAttribute('aria-busy', 'false');
        this.searchButton.disabled = false;
    }
    
    showError(message) {
        this.errorMessage.textContent = message;
        this.errorMessage.style.display = 'block';
        setTimeout(() => {
            this.hideError();
        }, 5000);
    }
    
    hideError() {
        this.errorMessage.style.display = 'none';
    }
    
    showSuccess(message) {
        const successDiv = document.createElement('div');
        successDiv.className = 'success-message';
        successDiv.textContent = message;
        
        const container = this.resultsSection || document.querySelector('.container');
        container.insertBefore(successDiv, container.firstChild);
        
        setTimeout(() => {
            successDiv.remove();
        }, 3000);
    }
    
    showEmptyState(semantix) {
        this._renderKnovasBanner(semantix || null);
        this.resultsContainer.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">🔍</div>
                <h3>Keine Ergebnisse gefunden</h3>
                <p>Ihre Suche nach "${this.escapeHtml(this.currentQuery)}" ergab keine Treffer.</p>
                <p class="mt-20">Versuchen Sie es mit anderen Suchbegriffen.</p>
            </div>
        `;
        this.resultsCount.textContent = '0 Ergebnisse';
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
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
