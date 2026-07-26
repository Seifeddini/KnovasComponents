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
        this.resultsPerPage = document.getElementById('resultsPerPage');
        
        this.currentQuery = '';
        this.currentResults = [];
        const cfg = typeof window !== 'undefined' ? window.__DOCBRIDGE__ || {} : {};
        this.onedriveEnrichmentLoaded = !!cfg.onedriveEnrichmentLoaded;
        /** CSRF token for state-changing requests (server enforces it on every POST). */
        this.csrfToken = cfg.csrfToken || '';
        /** @type {string|null} Knovas query_session_id from last /secured/query (for engagement analytics). */
        this.querySessionId = null;
        /** @type {Array<{action: string, pointer: string, position?: number}>} */
        this._engagementQueue = [];
        this._engagementFlushTimer = null;
        this._hoverPreviewEl = null;
        this._hoverPreviewTimer = null;
        this._hoverPreviewCard = null;
        this._hoverPdfCache = new Map();

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
        
        document.getElementById('healthCheck').addEventListener('click', (e) => {
            e.preventDefault();
            this.checkHealth();
        });

        this.resultsContainer.addEventListener('click', (e) => this._onResultsClick(e));
        this.resultsContainer.addEventListener('mouseenter', (e) => this._onResultHoverEnter(e), true);
        this.resultsContainer.addEventListener('mouseleave', (e) => this._onResultHoverLeave(e), true);
        this.resultsContainer.addEventListener('focusin', (e) => this._onResultHoverEnter(e));
        this.resultsContainer.addEventListener('focusout', (e) => this._onResultHoverLeave(e));
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') this._hideHoverPreview();
        });
        window.addEventListener('scroll', () => this._hideHoverPreview(), true);
    }

    _pointerForDoc(doc) {
        const p = doc && (doc.doc_id || doc.pointer || doc.path);
        return p != null ? String(p).trim() : '';
    }

    _queueEngagement(action, pointer, position) {
        if (!this.querySessionId || !pointer) return;
        const ev = { action, pointer: String(pointer).trim() };
        if (position != null && position >= 1) {
            ev.position = position;
        }
        this._engagementQueue.push(ev);
        if (this._engagementQueue.length >= 50) {
            this._flushEngagement();
        } else {
            this._flushEngagementSoon();
        }
    }

    _flushEngagementSoon() {
        if (this._engagementFlushTimer) return;
        this._engagementFlushTimer = window.setTimeout(() => this._flushEngagement(), 300);
    }

    async _flushEngagement() {
        if (this._engagementFlushTimer) {
            window.clearTimeout(this._engagementFlushTimer);
            this._engagementFlushTimer = null;
        }
        if (!this.querySessionId || !this._engagementQueue.length) return;
        const events = this._engagementQueue.splice(0, 50);
        try {
            const response = await fetch('/api/analytics/engagement', {
                method: 'POST',
                credentials: 'same-origin',
                headers: this._jsonHeadersWithCsrf(),
                body: JSON.stringify({
                    query_session_id: this.querySessionId,
                    events,
                }),
            });
            if (this._redirectIfLoginRequired(response)) return;
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                console.debug('Engagement not accepted:', data.error || response.status);
            }
        } catch (err) {
            console.debug('Engagement send failed:', err);
        }
        if (this._engagementQueue.length) {
            this._flushEngagementSoon();
        }
    }

    _redirectIfLoginRequired(response) {
        if (response.status === 401) {
            window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
            return true;
        }
        return false;
    }

    _onResultsClick(e) {
        const previewLink = e.target.closest('a[href*="/preview?"]');
        if (previewLink) {
            const card = previewLink.closest('.document-card');
            if (card) {
                const pointer = card.getAttribute('data-pointer');
                const pos = parseInt(card.getAttribute('data-display-position') || '0', 10);
                this._queueEngagement('view', pointer, pos > 0 ? pos : undefined);
            }
            return;
        }

        const externalLink = e.target.closest('a[href*="/external-open?"]');
        if (externalLink) {
            const card = externalLink.closest('.document-card');
            if (card) {
                const pointer = card.getAttribute('data-pointer');
                const pos = parseInt(card.getAttribute('data-display-position') || '0', 10);
                this._queueEngagement('view', pointer, pos > 0 ? pos : undefined);
            }
            return;
        }

        const dismissBtn = e.target.closest('.js-dismiss-result');
        if (dismissBtn) {
            const card = dismissBtn.closest('.document-card');
            if (!card) return;
            const pointer = card.getAttribute('data-pointer');
            const pos = parseInt(card.getAttribute('data-display-position') || '0', 10);
            this._queueEngagement('dismiss', pointer, pos > 0 ? pos : undefined);
            card.style.display = 'none';
            return;
        }

        const titleEl = e.target.closest('.document-title');
        if (titleEl) {
            const card = titleEl.closest('.document-card');
            if (card) {
                const pointer = card.getAttribute('data-pointer');
                const pos = parseInt(card.getAttribute('data-display-position') || '0', 10);
                this._queueEngagement('click', pointer, pos > 0 ? pos : undefined);
            }
        }

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
                headers: this._jsonHeadersWithCsrf(),
                body: JSON.stringify({
                    query: query,
                    limit: parseInt(this.resultsPerPage.value),
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

    _ensureHoverPreview() {
        if (this._hoverPreviewEl) return this._hoverPreviewEl;
        const el = document.createElement('div');
        el.className = 'document-hover-preview';
        el.setAttribute('role', 'tooltip');
        el.hidden = true;
        document.body.appendChild(el);
        this._hoverPreviewEl = el;
        return el;
    }

    _docForCard(card) {
        if (!card) return null;
        const idx = parseInt(card.getAttribute('data-index') || '-1', 10);
        if (Number.isNaN(idx) || idx < 0) return null;
        return this.currentResults[idx] || null;
    }

    _onResultHoverEnter(e) {
        const card = e.target.closest('.document-card');
        if (!card || !this.resultsContainer.contains(card)) return;
        const cfg = typeof window !== 'undefined' ? window.__DOCBRIDGE__ || {} : {};
        if (!cfg.hoverPreviewEnabled) return;
        if (this._hoverPreviewTimer) {
            clearTimeout(this._hoverPreviewTimer);
            this._hoverPreviewTimer = null;
        }
        this._hoverPreviewCard = card;
        this._hoverPreviewTimer = window.setTimeout(() => {
            this._hoverPreviewTimer = null;
            if (this._hoverPreviewCard === card) {
                this._showHoverPreview(card);
            }
        }, 150);
    }

    _onResultHoverLeave(e) {
        const card = e.target.closest('.document-card');
        if (!card) return;
        const related = e.relatedTarget;
        if (related && (card.contains(related) || this._hoverPreviewEl?.contains(related))) {
            return;
        }
        if (this._hoverPreviewTimer) {
            clearTimeout(this._hoverPreviewTimer);
            this._hoverPreviewTimer = null;
        }
        if (this._hoverPreviewCard === card) {
            this._hideHoverPreview();
        }
    }

    _showHoverPreview(card) {
        const doc = this._docForCard(card);
        if (!doc) return;
        const title = this.displayTitle(doc);
        const firstPage = String(doc.first_page_preview || '').trim();
        const snippet = doc.context_snippet;
        const summary = this.ingestedSummaryText(doc);
        const cfg = typeof window !== 'undefined' ? window.__DOCBRIDGE__ || {} : {};
        const path = String(doc.path || '');
        const docId = String(doc.doc_id || doc.pointer || path || '');
        const isPdf = path.toLowerCase().endsWith('.pdf');
        const canEmbedPdf = !!cfg.pdfInlineInBrowser && isPdf && doc.can_open === true && path;
        const extRaw = doc.external_url ? String(doc.external_url).trim() : '';
        const externalUrl = /^https?:\/\//i.test(extRaw) ? extRaw : '';

        let bodyHtml = '';
        if (firstPage) {
            bodyHtml += `<div class="document-hover-preview-section"><div class="document-hover-preview-label">Erste Seite</div>${this._buildFirstPageHtml(firstPage)}</div>`;
        }
        if (snippet) {
            bodyHtml += `<div class="document-hover-preview-section"><div class="document-hover-preview-label">Trefferkontext</div>${this._buildContextSnippetHtml(snippet)}</div>`;
        }
        if (!bodyHtml && summary) {
            bodyHtml = `<div class="document-hover-preview-section"><div class="document-hover-preview-label">Zusammenfassung</div><div class="document-first-page-text">${this.escapeHtml(this.capSummaryLength(summary, 1200))}</div></div>`;
        }
        if (!bodyHtml) return;

        const pop = this._ensureHoverPreview();
        let pdfHtml = '';
        if (canEmbedPdf) {
            const cacheKey = `${docId}|${path}`;
            let pdfUrl = this._hoverPdfCache.get(cacheKey);
            if (!pdfUrl) {
                pdfUrl = `/api/document/${encodeURIComponent(docId)}/preview?path=${encodeURIComponent(path)}#page=1&view=FitH`;
                this._hoverPdfCache.set(cacheKey, pdfUrl);
            }
            pdfHtml = `<div class="document-hover-preview-pdf"><object type="application/pdf" data="${this.escapeAttr(pdfUrl)}" aria-label="PDF Vorschau Seite 1"></object></div>`;
        }
        const footerHtml = externalUrl
            ? `<div class="document-hover-preview-footer"><a href="${this.escapeAttr(externalUrl)}" target="_blank" rel="noopener noreferrer">In OneDrive öffnen</a></div>`
            : '';

        pop.innerHTML = `
            <div class="document-hover-preview-title">${this.escapeHtml(title)}</div>
            ${pdfHtml}
            <div class="document-hover-preview-body">${bodyHtml}</div>
            ${footerHtml}
        `;
        pop.hidden = false;
        const rect = card.getBoundingClientRect();
        const popRect = pop.getBoundingClientRect();
        let top = rect.bottom + 8;
        let left = rect.left;
        if (left + popRect.width > window.innerWidth - 12) {
            left = Math.max(12, window.innerWidth - popRect.width - 12);
        }
        if (top + 200 > window.innerHeight - 12) {
            top = Math.max(12, rect.top - popRect.height - 8);
        }
        pop.style.top = `${top + window.scrollY}px`;
        pop.style.left = `${left + window.scrollX}px`;
    }

    _hideHoverPreview() {
        if (this._hoverPreviewTimer) {
            clearTimeout(this._hoverPreviewTimer);
            this._hoverPreviewTimer = null;
        }
        this._hoverPreviewCard = null;
        if (this._hoverPreviewEl) {
            this._hoverPreviewEl.hidden = true;
            this._hoverPreviewEl.innerHTML = '';
        }
    }

    /** Knovas /secured/query returns page/sentence only — no matching chunk text. */
    _formatLocation(pageNum, sentNum) {
        const parts = [];
        if (pageNum != null && pageNum !== '') {
            parts.push(`Seite ${String(pageNum)}`);
        }
        if (sentNum != null && sentNum !== '') {
            parts.push(`Satz ${String(sentNum)}`);
        }
        return parts.join(' · ');
    }

    _formatSimilarity(value) {
        if (value == null || value === '') return '';
        const n = Number(value);
        if (Number.isNaN(n)) return '';
        return n.toFixed(2);
    }

    _collectMatchLocations(doc) {
        const chunks = Array.isArray(doc.top_chunks) ? doc.top_chunks : [];
        let primaryPage = doc.page_number != null && doc.page_number !== '' ? doc.page_number : doc.page;
        let primarySent = doc.sentence_number;
        if ((primaryPage == null || primaryPage === '') && chunks.length) {
            primaryPage = chunks[0].page_number ?? chunks[0].page;
            if (primarySent == null || primarySent === '') {
                primarySent = chunks[0].sentence_number;
            }
        }
        const primaryKey = `${primaryPage ?? ''}|${primarySent ?? ''}`;
        const locations = [];
        const primaryLabel = this._formatLocation(primaryPage, primarySent);
        if (primaryLabel) {
            locations.push({
                label: primaryLabel,
                sim: doc.cosine_similarity,
                primary: true,
            });
        }
        const extras = [];
        for (const chunk of chunks) {
            const key = `${chunk.page_number ?? ''}|${chunk.sentence_number ?? ''}`;
            if (key === primaryKey) continue;
            const label = this._formatLocation(chunk.page_number, chunk.sentence_number);
            if (!label) continue;
            extras.push({
                label,
                sim: chunk.cosine_similarity,
                primary: false,
            });
        }
        extras.sort((a, b) => (Number(b.sim) || 0) - (Number(a.sim) || 0));
        return locations.concat(extras);
    }

    _buildMatchLocationsHtml(doc) {
        const locations = this._collectMatchLocations(doc);
        if (!locations.length) return '';

        const count = locations.length;
        const maxDisplayed = 4;
        const displayed = locations.slice(0, maxDisplayed);
        const hiddenCount = count - displayed.length;
        const chips = displayed.map((loc) => {
            const sim = this._formatSimilarity(loc.sim);
            const chipClass = loc.primary
                ? 'match-location-chip match-location-chip--primary'
                : 'match-location-chip';
            const scoreHtml = sim
                ? `<span class="match-location-chip-score">${this.escapeHtml(sim)}</span>`
                : '';
            return `<span class="${chipClass}">${this.escapeHtml(loc.label)}${scoreHtml}</span>`;
        }).join('');
        const moreHint = hiddenCount > 0
            ? ` <span class="document-match-more">+${hiddenCount} weitere</span>`
            : '';

        return `
            <div class="document-match" role="region" aria-label="Trefferstellen">
                <div class="document-match-header">
                    <span class="document-match-label">Trefferstellen</span>
                    <span class="document-match-count">${count} im Dokument${moreHint}</span>
                </div>
                <div class="match-location-list">${chips}</div>
            </div>`;
    }

    createDocumentCard(doc, index) {
        const card = document.createElement('div');
        card.className = 'document-card';
        card.setAttribute('tabindex', '0');
        card.setAttribute('data-index', index);
        const pointer = this._pointerForDoc(doc);
        if (pointer) {
            card.setAttribute('data-pointer', pointer);
        }
        card.setAttribute('data-display-position', String(index + 1));
        
        const title = this.displayTitle(doc);
        const docId = doc.doc_id || 'N/A';
        const ingestedSummary = this.ingestedSummaryText(doc);
        const hasContext = this.hasContextPreview(doc);
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
        const cfg = typeof window !== 'undefined' ? window.__DOCBRIDGE__ || {} : {};
        const useBrowserClientOpen = !!cfg.browserClientOpenEnabled;
        const useCompanion = !!cfg.companionEnabled;
        const isPdf = path.toLowerCase().endsWith('.pdf');
        const canPreviewPdf = !!cfg.pdfInlineInBrowser && isPdf;
        const showDegradedDownload = !!cfg.allowDegradedDownloadOpen;
        const onHttps = typeof window !== 'undefined' && window.location.protocol === 'https:';
        const openBtnLabel =
            onHttps && !useCompanion ? '📋 Pfad kopieren (Win+R)' : '📂 Öffnen';

        let actionsHtml;
        if (hasOneDrive) {
            const openHref = this.externalOpenHref(docId, path || docId);
            actionsHtml = `
                <a class="btn btn-success" href="${this.escapeAttr(openHref)}" target="_blank" rel="noopener noreferrer">
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
                    ${openBtnLabel}
                </button>
                ${previewBtn}
                ${downloadBtn}
            `;
        } else {
            actionsHtml = `<span class="badge badge-error">Datei nicht verfügbar</span>`;
        }
        
        const summaryStr = !hasContext && ingestedSummary
            ? this.capSummaryLength(ingestedSummary, 2000)
            : '';
        const summaryHtml = summaryStr ? this.escapeHtml(summaryStr) : '';
        const firstPageHtml = hasContext && doc.first_page_preview
            ? this._buildFirstPageHtml(doc.first_page_preview)
            : '';
        const contextHtml = hasContext && doc.context_snippet
            ? this._buildContextSnippetHtml(doc.context_snippet)
            : '';
        const matchHtml = this._buildMatchLocationsHtml(doc);
        const primaryLocation = this._formatLocation(
            doc.page_number != null && doc.page_number !== '' ? doc.page_number : doc.page,
            doc.sentence_number,
        );
        const documentDate = doc.document_date || doc.date || doc.timestamp || doc.created_at || null;
        const fileModified = doc.modified_at || null;

        card.innerHTML = `
            <div class="document-header">
                <div class="document-header-text">
                    <div class="document-title">${this.escapeHtml(title)}</div>
                    ${primaryLocation ? `<div class="document-location">${this.escapeHtml(primaryLocation)}</div>` : ''}
                </div>
                <div class="document-actions">
                    ${actionsHtml}
                </div>
            </div>
            
            ${matchHtml}
            
            ${documentDate || fileModified ? `
            <div class="document-meta">
                ${documentDate ? `
                    <span class="meta-item">${this.formatDate(documentDate)}</span>
                ` : ''}
                ${fileModified ? `
                    <span class="meta-item meta-item-muted">Geändert ${this.formatDate(fileModified)}</span>
                ` : ''}
            </div>
            ` : ''}
            
            ${firstPageHtml ? `
            <div class="document-first-page" role="region" aria-label="Erste Seite">
                <div class="document-first-page-label">Erste Seite</div>
                ${firstPageHtml}
            </div>
            ` : ''}
            
            ${contextHtml ? `
            <div class="document-context-snippet" role="region" aria-label="Trefferkontext">
                <div class="document-context-snippet-label">Trefferkontext</div>
                ${contextHtml}
            </div>
            ` : ''}
            
            ${summaryHtml ? `
            <div class="document-ingested-summary" role="region" aria-label="Dokumentzusammenfassung">
                <div class="document-ingested-summary-label">Dokumentzusammenfassung (Knovas)</div>
                <div class="document-ingested-summary-text">${summaryHtml}</div>
            </div>
            ` : ''}
            <div class="document-feedback-row">
                <button type="button" class="btn-text js-dismiss-result" title="Als nicht relevant markieren">Nicht relevant</button>
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
        this._reportEngagementForDocId(docId, 'view');
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
    
    _reportEngagementForDocId(docId, action) {
        const cards = this.resultsContainer.querySelectorAll('.document-card');
        for (const card of cards) {
            const pointer = card.getAttribute('data-pointer');
            if (pointer && pointer === String(docId)) {
                const pos = parseInt(card.getAttribute('data-display-position') || '0', 10);
                this._queueEngagement(action, pointer, pos > 0 ? pos : undefined);
                return;
            }
        }
        this._queueEngagement(action, docId);
    }

    async downloadDocument(docId, path) {
        try {
            this._reportEngagementForDocId(docId, 'download');
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
        }, 8000);
    }
    
    showEmptyState(semantix) {
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
