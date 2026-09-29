// Experimente -- Liste (/experiments). Vertrag: /api/experiments (Plan §10, §14).
// Alle Servertexte gehen ueber KX.el/textContent in die Seite (siehe
// experiments_common.js); Links entstehen nur aus gueltigen Schluesseln.
(function () {
    'use strict';

    const KX = window.KX;
    const { el, clear } = KX;
    const PAGE_SIZE = 50;
    const PACKS_FOR_EMPTY_STATE = ['engineering', 'marketing', 'sales', 'product'];
    const STATUS_PHASE_GUESS = { running: 'running', decided: 'decided', stopped: 'stopped' };

    const page = KX.pageData();
    const state = {
        canManage: Boolean(page.canManage),
        domains: [],
        domainsLoaded: false,
        domainsFailed: false,
        domainsPromise: null,
        filters: { domain: '', status: '', q: '', tag: '', archived: false },
        items: [],
        nextAfter: null,
        total: 0,
        loading: false,
        listLoaded: false,
        listAbort: null,
        searchAbort: null,
    };

    const dom = {
        body: document.getElementById('kxListBody'),
        chips: document.getElementById('kxDomainChips'),
        count: document.getElementById('kxListCount'),
        more: document.getElementById('kxLoadMore'),
        text: document.getElementById('kxFilterText'),
        status: document.getElementById('kxFilterStatus'),
        tag: document.getElementById('kxFilterTag'),
        archived: document.getElementById('kxFilterArchived'),
        newButton: document.getElementById('kxNewExperiment'),
        searchForm: document.getElementById('kxSearchForm'),
        searchInput: document.getElementById('kxSearchInput'),
        searchResult: document.getElementById('kxSearchResult'),
    };

    // ── Filterzustand in der Adresse ────────────────────────────────────

    function readFiltersFromUrl() {
        const params = new URLSearchParams(window.location.search);
        const f = state.filters;
        f.domain = (params.get('domain') || '').slice(0, 40);
        f.status = (params.get('status') || '').slice(0, 40);
        f.q = (params.get('q') || '').slice(0, 200);
        f.tag = (params.get('tag') || '').slice(0, 50);
        f.archived = params.get('archived') === '1';
        dom.text.value = f.q;
        dom.tag.value = f.tag;
        dom.archived.checked = f.archived;
    }

    function writeFiltersToUrl() {
        const f = state.filters;
        const params = new URLSearchParams();
        if (f.domain) params.set('domain', f.domain);
        if (f.status) params.set('status', f.status);
        if (f.q) params.set('q', f.q);
        if (f.tag) params.set('tag', f.tag);
        if (f.archived) params.set('archived', '1');
        const query = params.toString();
        const url = window.location.pathname + (query ? `?${query}` : '');
        try {
            window.history.replaceState(null, '', url);
        } catch (_) { /* nur Komfort */ }
    }

    function hasNarrowingFilter() {
        const f = state.filters;
        return Boolean(f.status || f.q || f.tag);
    }

    // ── Bereiche ────────────────────────────────────────────────────────

    /**
     * Alle Bereiche, auch archivierte: renderDomainChips zeigt diese nur mit
     * "Archivierte zeigen" oder wenn sie gewaehlt sind (ein Link auf die
     * Experimente eines archivierten Bereichs). Ein Filter auf einen
     * unbekannten Bereich faellt weg -- und die Liste wird ohne ihn neu
     * geladen --, aber nur, wenn die Bereiche wirklich geladen wurden.
     * Laeuft das Laden schon, wird auf dieselbe Anfrage gewartet.
     */
    function loadDomains() {
        if (!state.domainsPromise) {
            state.domainsPromise = fetchDomains().finally(() => { state.domainsPromise = null; });
        }
        return state.domainsPromise;
    }

    async function fetchDomains() {
        try {
            const data = await KX.api('GET', '/api/experiments/domains?archived=1');
            state.domains = Array.isArray(data.domains) ? data.domains : [];
            state.domainsFailed = false;
        } catch (err) {
            state.domains = [];
            state.domainsFailed = true;
            KX.toast(KX.errorMessage(err), 'error');
        }
        state.domainsLoaded = true;
        if (!state.domainsFailed && state.filters.domain
                && !state.domains.some((d) => d.key === state.filters.domain)) {
            state.filters.domain = '';
            writeFiltersToUrl();
            loadList(true);
        }
        renderDomainChips();
    }

    function renderDomainChips() {
        clear(dom.chips);
        const visible = state.domains.filter((d) => !d.archived || state.filters.archived
            || d.key === state.filters.domain);
        if (!visible.length) {
            dom.chips.hidden = true;
            return;
        }
        dom.chips.hidden = false;
        const total = visible.reduce((sum, d) => sum + (Number(d.experiment_count) || 0), 0);
        dom.chips.appendChild(domainChip('', 'Alle', null, total));
        visible.forEach((d) => dom.chips.appendChild(
            domainChip(d.key, d.name, d.color, d.experiment_count)));
        if (state.canManage) {
            dom.chips.appendChild(el('a', {
                class: 'kx-domain-chip kx-domain-chip--add', href: '/experiments/verwaltung#bereich-neu',
                text: '+ Neuer Bereich',
            }));
        }
    }

    function domainChip(key, name, color, count) {
        const pressed = state.filters.domain === key;
        return el('button', {
            type: 'button', class: 'kx-domain-chip', 'aria-pressed': pressed ? 'true' : 'false',
            onClick: () => {
                if (state.filters.domain === key) return;
                state.filters.domain = key;
                writeFiltersToUrl();
                renderDomainChips();
                loadList(true);
            },
        },
        color !== null ? KX.domainDot(color) : null,
        el('span', { text: name }),
        Number.isFinite(Number(count)) ? el('span', { class: 'kx-count', text: KX.fmtNumber(Number(count), 0) }) : null);
    }

    // ── Status-Auswahl aus den Zustaenden aller Typen ──────────────────

    async function loadStatusOptions() {
        const labels = new Map();
        try {
            const data = await KX.api('GET', '/api/experiments/types');
            for (const type of data.types || []) {
                const states = (type.definition && type.definition.states) || [];
                for (const s of states) {
                    if (s && typeof s.key === 'string' && !labels.has(s.key)) {
                        labels.set(s.key, String(s.label || s.key));
                    }
                }
            }
        } catch (_) {
            // Ohne Typen bleibt "Alle Status"; die Liste selbst funktioniert.
        }
        if (state.filters.status && !labels.has(state.filters.status)) {
            labels.set(state.filters.status, state.filters.status);
        }
        const current = state.filters.status;
        clear(dom.status);
        dom.status.appendChild(el('option', { value: '' }, 'Alle Status'));
        labels.forEach((label, key) => dom.status.appendChild(el('option', { value: key }, label)));
        dom.status.value = current;
    }

    // ── Liste ───────────────────────────────────────────────────────────

    function listUrl(after) {
        const f = state.filters;
        const params = new URLSearchParams();
        if (f.domain) params.set('domain', f.domain);
        if (f.status) params.set('status', f.status);
        if (f.q) params.set('q', f.q);
        if (f.tag) params.set('tag', f.tag);
        params.set('archived', f.archived ? '1' : '0');
        params.set('limit', String(PAGE_SIZE));
        if (after) params.set('after', after);
        return `/api/experiments?${params.toString()}`;
    }

    async function loadList(reset) {
        if (state.listAbort) state.listAbort.abort();
        const controller = new AbortController();
        state.listAbort = controller;
        const after = reset ? null : state.nextAfter;
        state.loading = true;
        dom.body.setAttribute('aria-busy', 'true');
        dom.more.disabled = true;
        if (reset) dom.body.classList.add('kx-refreshing');
        try {
            const data = await KX.api('GET', listUrl(after), undefined, { signal: controller.signal });
            if (controller !== state.listAbort) return;
            const result = data.result || {};
            // Eine Zeile ohne gueltigen Schluessel hat kein Ziel; sie zaehlt auch nicht mit.
            const items = (Array.isArray(result.items) ? result.items : []).filter((i) => i && KX.isKey(i.key));
            state.items = reset ? items : mergeByKey(state.items, items);
            state.nextAfter = result.next_after || null;
            state.total = Number(result.total) || state.items.length;
            state.listLoaded = true;
            renderList();
        } catch (err) {
            if (err && err.name === 'AbortError') return;
            if (reset) {
                clear(dom.body).appendChild(el('div', { class: 'kx-banner kx-banner--error', role: 'alert' },
                    el('p', { text: KX.errorMessage(err) }),
                    el('button', {
                        type: 'button', class: 'btn btn-outline btn-sm', text: 'Erneut versuchen',
                        onClick: () => loadList(true),
                    })));
                dom.more.hidden = true;
            } else {
                KX.toast(KX.errorMessage(err), 'error');
            }
        } finally {
            if (controller === state.listAbort) {
                state.loading = false;
                dom.body.setAttribute('aria-busy', 'false');
                dom.body.classList.remove('kx-refreshing');
                dom.more.disabled = false;
            }
        }
    }

    /**
     * Eine weitere Seite in die Liste einfuegen. Ein Experiment, das sich
     * seit der vorigen Seite geaendert hat, rutscht ueber den Cursor; der
     * Server haengt es der naechsten Seite an (moved: true), und es kann schon
     * in der Liste stehen. Darum nach Schluessel zusammenfuehren (die neue
     * Fassung ersetzt die alte) und wieder nach updated_at absteigend ordnen
     * -- stabil, gleiche Zeiten behalten die Reihenfolge des Servers.
     */
    function mergeByKey(existing, incoming) {
        const merged = existing.slice();
        const index = new Map(merged.map((item, i) => [item.key, i]));
        incoming.forEach((item) => {
            if (index.has(item.key)) {
                merged[index.get(item.key)] = item;
            } else {
                index.set(item.key, merged.length);
                merged.push(item);
            }
        });
        const time = (item) => {
            const t = Date.parse(item.updated_at);
            return Number.isFinite(t) ? t : 0;
        };
        return merged.sort((a, b) => time(b) - time(a));
    }

    function renderList() {
        clear(dom.body);
        const shown = state.items.length;
        dom.count.textContent = shown
            ? `${KX.fmtNumber(shown, 0)} von ${KX.fmtNumber(Math.max(state.total, shown), 0)} Experimenten`
            : '';
        dom.more.hidden = !state.nextAfter;
        if (!shown) {
            dom.body.appendChild(emptyListState());
            return;
        }
        dom.body.appendChild(experimentTable(state.items, false));
    }

    function emptyListState() {
        if (state.domainsLoaded && !state.domainsFailed && !state.domains.some((d) => !d.archived)) {
            return noDomainsState();
        }
        if (hasNarrowingFilter()) {
            return KX.emptyState('Keine Experimente für diese Auswahl.',
                'Andere Filter wählen oder die Filter zurücksetzen.', [
                    el('button', {
                        type: 'button', class: 'btn btn-outline btn-sm', text: 'Filter zurücksetzen',
                        onClick: resetFilters,
                    }),
                ]);
        }
        const newButton = el('button', {
            type: 'button', class: 'btn btn-primary btn-sm', text: 'Neues Experiment',
            onClick: () => openNewExperiment(state.filters.domain),
        });
        if (state.filters.domain) {
            return KX.emptyState('Noch keine Experimente in diesem Bereich.', null, [newButton]);
        }
        return KX.emptyState('Noch keine Experimente.',
            'Ein Experiment hält eine Hypothese fest, dazu Varianten, Messwerte, Auswertungen und die Entscheidung.',
            [newButton]);
    }

    function resetFilters() {
        const f = state.filters;
        f.status = '';
        f.q = '';
        f.tag = '';
        dom.status.value = '';
        dom.text.value = '';
        dom.tag.value = '';
        writeFiltersToUrl();
        loadList(true);
    }

    function statusPhase(item) {
        if (item.status_phase !== undefined) return item.status_phase;
        return STATUS_PHASE_GUESS[item.status] || null;
    }

    /** Tabelle der Experimente; withSnippet zeigt den Treffertext der Suche. */
    function experimentTable(items, withSnippet) {
        const rows = items.map((item) => experimentRow(item, withSnippet)).filter(Boolean);
        return el('div', { class: 'kx-table-wrap' },
            el('table', { class: 'kx-table kx-table--rows' },
                el('thead', null, el('tr', null,
                    el('th', { scope: 'col', text: 'Schlüssel' }),
                    el('th', { scope: 'col', text: 'Experiment' }),
                    el('th', { scope: 'col', text: 'Bereich' }),
                    el('th', { scope: 'col', text: 'Status' }),
                    el('th', { scope: 'col', text: 'Primäre Metrik' }),
                    el('th', { scope: 'col', text: 'Letztes Ergebnis' }),
                    el('th', { scope: 'col', text: 'Aktualisiert' }))),
                el('tbody', null, rows)));
    }

    function experimentRow(item, withSnippet) {
        const url = KX.experimentUrl(item && item.key);
        if (!url) return null;
        const domain = item.domain || {};
        const type = item.type || {};
        const metric = item.primary_metric;
        const latest = item.latest;
        const violations = Number(item.guardrail_violations) || 0;
        const resultCell = el('div', { class: 'kx-result-cell' });
        if (latest && (latest.headline || latest.verdict)) {
            if (latest.headline) resultCell.appendChild(el('span', { text: latest.headline }));
            const chips = el('span', { class: 'kx-chips' });
            if (latest.verdict) chips.appendChild(KX.verdictChip(latest.verdict));
            if (violations) {
                chips.appendChild(KX.chip('Leitplanke verletzt', 'bad', {
                    title: violations > 1 ? `${violations} Leitplanken verletzt` : 'Eine Leitplanke ist verletzt',
                }));
            }
            resultCell.appendChild(chips);
        } else if (violations) {
            resultCell.appendChild(KX.chip('Leitplanke verletzt', 'bad'));
        } else {
            resultCell.appendChild(el('span', { class: 'kx-muted', text: 'noch keine Auswertung' }));
        }
        const titleCell = el('td', { class: 'kx-title-cell' },
            el('strong', null, el('a', { href: url, text: item.title || item.key })),
            el('span', { class: 'kx-sub' },
                type.name || '',
                item.archived ? ' · archiviert' : ''),
            withSnippet && item.snippet ? el('span', { class: 'kx-sub', text: item.snippet }) : null,
            Array.isArray(item.tags) && item.tags.length
                ? el('span', { class: 'kx-tags' }, item.tags.slice(0, 6).map(
                    (t) => el('span', { class: 'kx-tag kx-tag--static', text: t })))
                : null);
        const row = el('tr', null,
            el('td', null, el('a', { class: 'kx-key-link', href: url, text: item.key, tabindex: '-1' })),
            titleCell,
            el('td', null, el('span', { class: 'kx-domain' }, KX.domainDot(domain.color),
                el('span', { text: domain.name || domain.key || KX.DASH }))),
            el('td', null, KX.statusChip(item.status_label || item.status || KX.DASH, statusPhase(item))),
            el('td', null, metric
                ? el('span', { text: metric.name || metric.key })
                : el('span', { class: 'kx-muted', text: KX.DASH })),
            el('td', null, resultCell),
            el('td', { class: 'kx-nowrap', text: KX.fmtDate(item.updated_at) }));
        // Die ganze Zeile fuehrt zum Experiment; Links darin behalten ihr
        // eigenes Verhalten (Mittelklick, neuer Tab).
        row.addEventListener('click', (e) => {
            if (e.target.closest('a, button, input, select, textarea')) return;
            if (window.getSelection && String(window.getSelection())) return;
            window.location.assign(url);
        });
        return row;
    }

    // ── Knovas-Suche ────────────────────────────────────────────────────

    const SOURCE_NOTES = {
        knovas: 'Gefunden mit Knovas',
        'knovas+database': 'Knovas und Datenbank',
        database: 'Datenbanksuche',
    };

    async function runSearch(query) {
        const q = String(query || '').trim();
        if (state.searchAbort) state.searchAbort.abort();
        if (!q) {
            closeSearch();
            return;
        }
        const controller = new AbortController();
        state.searchAbort = controller;
        dom.searchResult.hidden = false;
        dom.searchResult.setAttribute('aria-busy', 'true');
        clear(dom.searchResult).appendChild(KX.spinnerText('Suche läuft …'));
        try {
            const data = await KX.api('GET',
                `/api/experiments/search?q=${encodeURIComponent(q)}&limit=30`, undefined,
                { signal: controller.signal });
            if (controller !== state.searchAbort) return;
            renderSearch(q, data.result || {});
        } catch (err) {
            if (err && err.name === 'AbortError') return;
            clear(dom.searchResult).appendChild(el('div', { class: 'kx-banner kx-banner--error', role: 'alert' },
                el('p', { text: KX.errorMessage(err) })));
        } finally {
            if (controller === state.searchAbort) dom.searchResult.setAttribute('aria-busy', 'false');
        }
    }

    function renderSearch(q, result) {
        const items = Array.isArray(result.items) ? result.items : [];
        const note = SOURCE_NOTES[result.source] || 'Datenbanksuche';
        const head = el('div', { class: 'kx-search-note' },
            el('span', null, el('strong', { text: note }),
                ` · ${KX.fmtNumber(items.length, 0)} Treffer für „${q}“`),
            el('button', {
                type: 'button', class: 'kx-link-button', text: 'Suche zurücksetzen', onClick: closeSearch,
            }));
        clear(dom.searchResult).appendChild(head);
        if (result.warning) {
            dom.searchResult.appendChild(el('p', { class: 'kx-search-warning', text: result.warning }));
        }
        if (!items.length) {
            dom.searchResult.appendChild(KX.emptyState(null,
                'Kein Experiment passt zu dieser Suche. Andere Begriffe versuchen oder die Liste unten filtern.'));
            return;
        }
        dom.searchResult.appendChild(experimentTable(items, true));
    }

    function closeSearch() {
        if (state.searchAbort) state.searchAbort.abort();
        state.searchAbort = null;
        dom.searchInput.value = '';
        dom.searchResult.hidden = true;
        clear(dom.searchResult);
    }

    // ── Neues Experiment ────────────────────────────────────────────────

    async function openNewExperiment(presetDomain) {
        // Nach einem Fehlschlag erneut fragen: "noch kein Bereich" darf nur
        // heissen, dass der Server wirklich keinen geliefert hat. Schlaegt es
        // wieder fehl, hat loadDomains den Fehler schon gemeldet.
        if (!state.domainsLoaded || state.domainsFailed) await loadDomains();
        if (state.domainsFailed) return;
        const domains = state.domains.filter((d) => !d.archived);
        if (!domains.length) {
            KX.toast('Es gibt noch keinen Bereich. Zuerst einen Bereich einrichten.', 'error');
            return;
        }
        const initialDomain = domains.some((d) => d.key === presetDomain) ? presetDomain : domains[0].key;
        const domainSelect = KX.select({ name: 'domain' },
            domains.map((d) => ({ value: d.key, label: d.name })), initialDomain);
        const typeSelect = KX.select({ name: 'type' }, [{ value: '', label: 'Wird geladen …' }], '');
        const typeHelp = el('p', { class: 'kx-help' });
        const titleInput = KX.input({ name: 'title', maxlength: 300, autocomplete: 'off' });
        const hypothesis = KX.textarea({
            name: 'hypothesis', rows: 4, maxlength: 20000,
            placeholder: 'Wenn wir …, dann …, weil …',
        });
        const fieldsBox = el('div', { class: 'kx-type-fields' });
        const fieldsHead = el('p', { class: 'kx-subhead', text: 'Angaben', hidden: true });
        let types = [];
        // Values added to the domain's selection fields (a new segment, say).
        let fieldOptions = {};
        let loadSeq = 0;

        function currentType() {
            return types.find((t) => t.id === typeSelect.value) || null;
        }

        function renderTypeFields() {
            clear(fieldsBox);
            const type = currentType();
            typeHelp.textContent = type && type.description ? type.description : '';
            const defs = (type && type.definition && type.definition.fields) || [];
            fieldsHead.hidden = !defs.length;
            const domainKey = domainSelect.value;
            defs.forEach((f) => fieldsBox.appendChild(KX.renderFieldInput(f, null, {
                extra: fieldOptions[f.key],
                onAdd: async (def) => {
                    const added = await KX.addFieldOption(domainKey, def);
                    if (added && domainKey === domainSelect.value) {
                        fieldOptions[def.key] = (fieldOptions[def.key] || []).concat([added]);
                    }
                    return added;
                },
            })));
        }

        async function loadTypes() {
            const seq = ++loadSeq;
            typeSelect.disabled = true;
            try {
                const domainKey = encodeURIComponent(domainSelect.value);
                const [data, added] = await Promise.all([
                    KX.api('GET', `/api/experiments/types?domain=${domainKey}`),
                    KX.api('GET', `/api/experiments/domains/${domainKey}/field-options`)
                        .catch(() => ({ result: { by_field: {} } })),
                ]);
                if (seq !== loadSeq) return;
                fieldOptions = ((added && added.result) || {}).by_field || {};
                types = (data.types || []).filter((t) => !t.archived);
                // Typen des Bereichs zuerst, die bereichsuebergreifenden danach.
                types.sort((a, b) => (a.domain_key ? 0 : 1) - (b.domain_key ? 0 : 1)
                    || String(a.name).localeCompare(String(b.name), 'de'));
                clear(typeSelect);
                if (!types.length) {
                    typeSelect.appendChild(el('option', { value: '' }, 'Kein Typ verfügbar'));
                } else {
                    types.forEach((t) => typeSelect.appendChild(el('option', { value: t.id },
                        t.domain_key ? t.name : `${t.name} (alle Bereiche)`)));
                }
            } catch (err) {
                if (seq !== loadSeq) return;
                types = [];
                clear(typeSelect).appendChild(el('option', { value: '' }, 'Typen nicht geladen'));
                typeHelp.textContent = KX.errorMessage(err);
            } finally {
                if (seq === loadSeq) {
                    typeSelect.disabled = !types.length;
                    renderTypeFields();
                }
            }
        }

        domainSelect.addEventListener('change', loadTypes);
        typeSelect.addEventListener('change', renderTypeFields);
        loadTypes();

        const body = el('div', null,
            el('div', { class: 'kx-form-row' },
                KX.field({ label: 'Bereich', input: domainSelect, name: 'domain', required: true }),
                KX.field({ label: 'Typ', input: typeSelect, name: 'type', required: true })),
            typeHelp,
            KX.field({ label: 'Titel', input: titleInput, name: 'title', required: true }),
            KX.field({
                label: 'Hypothese', input: hypothesis, name: 'hypothesis',
                help: 'Was wird erwartet, und woran wäre es zu erkennen?',
            }),
            fieldsHead,
            fieldsBox);

        await KX.dialog({
            title: 'Neues Experiment',
            wide: true,
            body,
            actions: [
                { label: 'Abbrechen', value: null },
                {
                    label: 'Anlegen',
                    primary: true,
                    onClick: async () => {
                        const title = titleInput.value.trim();
                        const type = currentType();
                        const missing = {};
                        if (!title) missing.title = 'Bitte einen Titel angeben.';
                        if (!type) missing.type = 'Bitte einen Typ wählen.';
                        if (Object.keys(missing).length) {
                            throw new KX.ApiError('Bitte die markierten Angaben ergänzen.', 400, missing);
                        }
                        const fields = {};
                        const defs = (type.definition && type.definition.fields) || [];
                        defs.forEach((f) => {
                            const row = fieldsBox.querySelector(`[data-field-key="${CSS.escape(f.key)}"]`);
                            const value = KX.readFieldInput(f, row);
                            if (value !== null) fields[f.key] = value;
                        });
                        const data = await KX.api('POST', '/api/experiments', {
                            domain: domainSelect.value,
                            type: type.id,
                            title,
                            hypothesis: hypothesis.value.trim(),
                            fields,
                        });
                        const url = KX.experimentUrl(data.experiment && data.experiment.key);
                        if (url) {
                            window.location.assign(url);
                        } else {
                            loadList(true);
                        }
                        return true;
                    },
                },
            ],
        });
    }

    // ── Leerer Zustand ohne Bereiche ────────────────────────────────────

    function noDomainsState() {
        if (!state.canManage) {
            return KX.emptyState('Noch keine Bereiche.',
                'Bitten Sie eine verantwortliche Person, Bereiche einzurichten.');
        }
        const box = KX.emptyState('Noch keine Bereiche.',
            'Pakete installieren richtet Bereiche mit passenden Typen und Metriken ein. '
            + 'Eigene Bereiche entstehen unter Verwaltung.');
        const list = el('div', { class: 'kx-pack-list' });
        box.appendChild(list);
        const actions = el('div', { class: 'kx-empty-actions' },
            el('a', { class: 'btn btn-outline btn-sm', href: '/experiments/verwaltung#bereiche', text: 'Eigenen Bereich anlegen' }));
        box.appendChild(actions);
        renderPackOffers(list);
        return box;
    }

    async function renderPackOffers(list) {
        let packs = [];
        try {
            const data = await KX.api('GET', '/api/experiments/packs');
            packs = (data.packs || []).filter((p) => PACKS_FOR_EMPTY_STATE.indexOf(p.name) !== -1);
        } catch (_) {
            packs = PACKS_FOR_EMPTY_STATE.map((name) => ({ name, title: name, description: '' }));
        }
        packs.sort((a, b) => PACKS_FOR_EMPTY_STATE.indexOf(a.name) - PACKS_FOR_EMPTY_STATE.indexOf(b.name));
        clear(list);
        packs.forEach((p) => {
            const button = el('button', {
                type: 'button', class: 'btn btn-outline btn-sm',
                text: p.installed ? 'Installiert' : 'Installieren', disabled: Boolean(p.installed),
            });
            button.addEventListener('click', async () => {
                button.disabled = true;
                button.textContent = 'Wird installiert …';
                try {
                    await KX.api('POST', `/api/experiments/packs/${encodeURIComponent(p.name)}/install`, {});
                    KX.toast(`Paket «${p.title || p.name}» installiert.`, 'success');
                    await loadDomains();
                    await loadStatusOptions();
                    loadList(true);
                } catch (err) {
                    button.disabled = false;
                    button.textContent = 'Installieren';
                    KX.toast(KX.errorMessage(err), 'error');
                }
            });
            list.appendChild(el('div', { class: 'kx-pack' },
                el('strong', { text: p.title || p.name }),
                el('p', { text: p.description || '' }),
                button));
        });
    }

    // ── Start ───────────────────────────────────────────────────────────

    function bindFilters() {
        const onText = KX.debounce(() => {
            const q = dom.text.value.trim();
            if (q === state.filters.q) return;
            state.filters.q = q;
            writeFiltersToUrl();
            loadList(true);
        }, 300);
        dom.text.addEventListener('input', onText);
        const onTag = KX.debounce(() => {
            const tag = dom.tag.value.trim();
            if (tag === state.filters.tag) return;
            state.filters.tag = tag;
            writeFiltersToUrl();
            loadList(true);
        }, 300);
        dom.tag.addEventListener('input', onTag);
        dom.status.addEventListener('change', () => {
            state.filters.status = dom.status.value;
            writeFiltersToUrl();
            loadList(true);
        });
        dom.archived.addEventListener('change', () => {
            state.filters.archived = dom.archived.checked;
            writeFiltersToUrl();
            renderDomainChips();
            loadList(true);
        });
        dom.more.addEventListener('click', () => {
            if (!state.loading && state.nextAfter) loadList(false);
        });
        dom.newButton.addEventListener('click', () => openNewExperiment(state.filters.domain));
        dom.searchForm.addEventListener('submit', (e) => {
            e.preventDefault();
            runSearch(dom.searchInput.value);
        });
        dom.searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && dom.searchInput.value) {
                e.preventDefault();
                closeSearch();
            }
        });
    }

    function init() {
        readFiltersFromUrl();
        bindFilters();
        KX.meta().catch(() => null);
        Promise.all([loadDomains(), loadStatusOptions()]).then(() => {
            // Der leere Zustand haengt davon ab, ob es Bereiche gibt.
            if (state.listLoaded && !state.loading && !state.items.length) renderList();
        });
        loadList(true);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
