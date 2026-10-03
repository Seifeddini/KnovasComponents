// Experimente -- Detailseite (/experiments/<KEY>). Vertrag: GET/PATCH
// /api/experiments/<KEY> und die Unterpfade (Plan §9, §10, §14).
//
// Alle Servertexte gehen ueber KX.el/textContent in die Seite; Markdown nur
// ueber KX.renderMarkdown (KnovasMarkdown escaped zuerst). Jede Aenderung am
// Experiment schickt die zuletzt gelesene row_version mit; ein 409 zeigt den
// Hinweis "Neu laden" statt still zu ueberschreiben.
(function () {
    'use strict';

    const KX = window.KX;
    const { el, clear } = KX;
    const DASH = KX.DASH;

    const page = KX.pageData();
    const KEY = KX.isKey(page.experimentKey) ? page.experimentKey : null;
    const API = KEY ? `/api/experiments/${KEY}` : null;
    const POLL_MS = 3000;
    const POLL_LIMIT_MS = 10 * 60 * 1000;
    /** "In Knovas: ausstehend" wird alle 4 s nachgefragt, hoechstens 5 Minuten
        lang (Standard-Verzoegerung 60 s plus Abholen durch den Worker). */
    const INDEX_POLL_MS = 4000;
    const INDEX_POLL_LIMIT_MS = 5 * 60 * 1000;
    /** Editoren, deren Inhalt auf einer row_version beruht (state.bases). */
    const VERSIONED_EDITORS = ['title', 'hypothesis', 'description', 'fields', 'variants', 'metrics'];
    const VARIANT_KEY_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$/;
    const DIM_KEY_RE = /^[A-Za-z0-9_.-]{1,40}$/;
    const MEAN_LIKE = new Set(['mean', 'duration', 'currency']);
    const TRIGGER_LABELS = { manual: 'von Hand', pipeline: 'automatisch', api: 'API' };

    /** Beschriftungen der Messarten, falls meta nicht geladen werden konnte
        (Quelle: experiments/kinds.py KINDS). */
    const KIND_FALLBACK = {
        proportion: { label: 'Anteil', value_label: 'Erfolge', count_label: 'Versuche', denominator_label: '', sum_sq_label: '', integral_value: true, needs_denominator: false, is_distribution: false },
        mean: { label: 'Mittelwert', value_label: 'Summe der Werte', count_label: 'Anzahl', denominator_label: '', sum_sq_label: 'Quadratsumme (optional)', integral_value: false, needs_denominator: false, is_distribution: false },
        count: { label: 'Rate', value_label: 'Ereignisse', count_label: 'Einheiten', denominator_label: '', sum_sq_label: '', integral_value: true, needs_denominator: false, is_distribution: false },
        duration: { label: 'Dauer', value_label: 'Summe der Werte', count_label: 'Anzahl', denominator_label: '', sum_sq_label: 'Quadratsumme (optional)', integral_value: false, needs_denominator: false, is_distribution: false },
        currency: { label: 'Geldbetrag', value_label: 'Summe der Werte', count_label: 'Anzahl', denominator_label: '', sum_sq_label: 'Quadratsumme (optional)', integral_value: false, needs_denominator: false, is_distribution: false },
        ratio: { label: 'Verhältnis', value_label: 'Zähler', count_label: 'Einheiten', denominator_label: 'Nenner', sum_sq_label: '', integral_value: false, needs_denominator: true, is_distribution: false },
        ordinal: { label: 'Skala', value_label: 'Stufe', count_label: 'Anzahl', denominator_label: '', sum_sq_label: '', integral_value: false, needs_denominator: false, is_distribution: true },
        categorical: { label: 'Kategorie', value_label: 'Kategorie', count_label: 'Anzahl', denominator_label: '', sum_sq_label: '', integral_value: true, needs_denominator: false, is_distribution: true },
    };

    const state = {
        exp: null,
        meta: null,
        canManage: Boolean(page.canManage),
        loading: null,
        runsNext: null,
        /** Bloecke im Bearbeitungsmodus: ein Neuaufbau laesst sie stehen. */
        editors: new Set(),
        /** row_version, auf der ein offener Editor aufsetzt. Gespeichert wird mit
            dieser Fassung, nicht mit der zuletzt geladenen: sonst ueberschriebe
            ein Editor, der vor einem Hintergrund-Neuladen geoeffnet wurde, still
            die Aenderung einer anderen Person. */
        bases: new Map(),
        runsFor: null,
        buckets: {},
        series: new Map(),
        batches: { items: [], nextAfter: null, loadedFor: null },
        runs: null,
        evalDetails: new Map(),
        polls: new Map(),
        importNote: null,
        catalog: null,
        /** Eine Anfrage lief schon, als load() erneut gerufen wurde: danach
            noch einmal lesen (sie kann vor der eigenen Aenderung gelesen haben). */
        reloadAgain: false,
        /** Hoechste row_version, die eine eigene Aenderung bestaetigt hat; ein
            aelterer Stand aus einem GET ist ueberholt und wird nicht gezeigt. */
        minVersion: 0,
        indexTimer: null,
        indexPollStarted: null,
        /** "Fruehere Auswertungen" aufgeklappt (bleibt beim Neuzeichnen). */
        earlierOpen: false,
        /** ids der ueberholten Auswertungen der gezeichneten Liste. */
        supersededIds: new Set(),
    };

    const $ = (id) => document.getElementById(id);

    // ── Hilfen ──────────────────────────────────────────────────────────

    function kindSpec(kind) {
        const fromMeta = state.meta && Array.isArray(state.meta.kinds)
            ? state.meta.kinds.find((k) => k && k.key === kind) : null;
        return fromMeta || KIND_FALLBACK[kind] || KIND_FALLBACK.mean;
    }

    function decimalsOf(metric) {
        const d = metric && metric.definition ? metric.definition.decimals : null;
        return typeof d === 'number' ? d : null;
    }

    function personName(p) {
        if (!p) return null;
        if (typeof p === 'string') return p;
        return p.display_name ? String(p.display_name) : null;
    }

    function variantByKey(key) {
        return ((state.exp && state.exp.variants) || []).find((v) => v.key === key) || null;
    }

    function variantLabel(key) {
        if (key === null || key === undefined || key === '') return 'ohne Variante';
        const v = variantByKey(key);
        return v && v.name ? `${key} · ${v.name}` : String(key);
    }

    function metricByKey(key) {
        return ((state.exp && state.exp.metrics) || []).find((m) => m.key === key) || null;
    }

    function definition() {
        return (state.exp && state.exp.definition) || {};
    }

    function editButton(labelText, onClick, extraClass) {
        return el('button', {
            type: 'button', class: ['btn', 'btn-outline', 'btn-sm', extraClass || ''],
            text: labelText, onClick,
        });
    }

    function blockHead(title, actions) {
        return el('div', { class: 'kx-block-head' }, el('h3', { text: title }),
            actions ? el('div', { class: 'kx-section-actions' }, actions) : null);
    }

    /** Fehler eines Editors in seinem Meldungskasten: die Meldung und die
        Feldfehler, die sie nicht schon woertlich enthaelt. */
    function showEditorError(box, err, unmatched) {
        const extra = unmatched || Object.keys((err && err.fields) || {}).map((k) => `${k}: ${err.fields[k]}`);
        clear(box);
        KX.errorLines(err, extra).forEach((line) => box.appendChild(el('p', { text: line })));
        box.hidden = false;
    }

    function guardrailText(m) {
        if (!m || m.role !== 'guardrail' || !m.guardrail_op || KX.finite(m.guardrail_value) === null) return '';
        const v = KX.fmtEstimate(m.kind, m.guardrail_value, m.unit, decimalsOf(m));
        return m.guardrail_op === 'max' ? `höchstens ${v}` : `mindestens ${v}`;
    }

    function guardrailChip(m) {
        if (m.guardrail_status === 'violated') return KX.chip('Leitplanke verletzt', 'bad');
        if (m.guardrail_status === 'ok') return KX.chip('Leitplanke eingehalten', 'good');
        return null;
    }

    function scopeText(scope) {
        const s = scope && typeof scope === 'object' ? scope : {};
        const parts = [];
        if (s.runs === 'latest') parts.push('Neuester Lauf je Variante');
        else if (Array.isArray(s.runs)) parts.push(`${KX.fmtNumber(s.runs.length, 0)} ausgewählte Läufe`);
        if (s.since || s.until) {
            parts.push(`Zeitraum ${s.since ? KX.fmtDate(s.since) : '…'}–${s.until ? KX.fmtDate(s.until) : '…'}`);
        }
        if (s.dims && typeof s.dims === 'object') {
            const dims = Object.keys(s.dims).map((k) => `${k}=${s.dims[k]}`);
            if (dims.length) parts.push(dims.join(', '));
        }
        return parts.length ? parts.join(' · ') : 'Alle Daten';
    }

    function fmtRelative(x) {
        const v = KX.finite(x);
        if (v === null) return DASH;
        const text = KX.fmtNumber(v * 100, 1);
        return `${text.charAt(0) === '-' ? '' : '+'}${text} %`;
    }

    /** Eine Differenz im Einheitensinn des Auswerters: "Pp." bei Anteilen,
        "x" bei Verhaeltnissen (Referenz 1), sonst die Einheit der Metrik. */
    function comparisonFormat(cmp, metric) {
        const dec = decimalsOf(metric);
        const unit = cmp && cmp.unit != null ? String(cmp.unit) : null;
        if (unit === 'Pp.' || (unit === null && metric && metric.kind === 'proportion')) {
            return { reference: 0, fmt: (v) => KX.fmtDiff('proportion', v, '', dec) };
        }
        if (unit === 'x') {
            return { reference: 1, fmt: (v) => (KX.finite(v) === null ? DASH : `${KX.fmtNumber(v, 2)} x`) };
        }
        const u = unit !== null ? unit : (metric ? metric.unit : '');
        return { reference: 0, fmt: (v) => KX.fmtDiff('mean', v, u, dec) };
    }

    function showConflict(message) {
        const box = $('kxConflict');
        clear(box).appendChild(el('div', { class: 'kx-banner', role: 'alert' },
            el('p', { text: message || 'Das Experiment wurde inzwischen geändert. Bitte neu laden.' }),
            el('button', {
                type: 'button', class: 'btn btn-outline btn-sm', text: 'Neu laden',
                onClick: () => {
                    box.hidden = true;
                    reloadKeepingDrafts();
                },
            })));
        box.hidden = false;
        box.scrollIntoView({ block: 'nearest' });
    }

    /**
     * "Neu laden" nach einem 409: der neue Stand wird geladen, ohne dass eine
     * Eingabe verloren geht. Notiz- und Entscheidungsentwurf haengen an keiner
     * Fassung und bleiben stehen. Offene Editoren bleiben offen, ruecken auf
     * die neue Fassung und zeigen den aktuellen Stand zum Abgleich: Speichern
     * setzt den Entwurf dann bewusst durch, "Entwurf verwerfen" zeigt den
     * neuen Stand.
     */
    async function reloadKeepingDrafts() {
        const open = VERSIONED_EDITORS.filter((name) => state.editors.has(name));
        await load();
        if (!state.exp) return;
        open.forEach((name) => {
            if (!state.editors.has(name)) return;
            state.bases.set(name, state.exp.row_version);
            markStaleDraft(name);
        });
    }

    const BLOCK_RENDERERS = {
        hypothesis: () => renderHypothesis(),
        description: () => renderDescription(),
        fields: () => renderFields(),
        variants: () => renderVariants(),
        metrics: () => renderMetrics(),
        title: () => renderHeader(),
    };
    const EDITOR_BOXES = {
        hypothesis: 'kxHypothesis', description: 'kxDescription', fields: 'kxFields',
        variants: 'kxVariants', metrics: 'kxMetrics',
    };

    /** Einen offenen Editor als "beruht auf einer aelteren Fassung" markieren. */
    function markStaleDraft(name) {
        const container = name === 'title'
            ? $('kxHeader').querySelector('.kx-title-edit') : $(EDITOR_BOXES[name]);
        if (!container) return;
        const textual = name === 'title' || name === 'hypothesis' || name === 'description';
        if (textual) {
            const input = container.querySelector(name === 'title' ? 'input' : 'textarea');
            const draft = input ? input.value.trim() : '';
            if (input && draft === String(state.exp[name] || '').trim()) {
                // Der Entwurf ist inzwischen der Stand: nichts abzugleichen.
                closeEditor(name);
                BLOCK_RENDERERS[name]();
                return;
            }
        }
        const old = container.querySelector('.kx-draft-note');
        if (old) old.remove();
        const current = !textual ? null
            : name === 'description' && state.exp.description ? KX.renderMarkdown(state.exp.description)
                : el('p', { class: 'kx-prose', text: String(state.exp[name] || '') || DASH });
        const note = el('div', { class: 'kx-banner kx-banner--info kx-draft-note', role: 'status' },
            el('div', null,
                el('p', { text: textual
                    ? 'Ihr Entwurf beruht auf einer älteren Fassung. Der aktuelle Stand ist geladen – '
                        + 'bitte abgleichen und erneut speichern.'
                    : 'Ihre Eingaben hier beruhen auf einer älteren Fassung. Der aktuelle Stand ist geladen – '
                        + 'bitte prüfen und erneut speichern, oder den Entwurf verwerfen, um ihn zu sehen.' }),
                current ? el('details', { class: 'kx-details', open: true },
                    el('summary', { text: 'Aktueller Stand' }), current) : null,
                el('div', { class: 'kx-form-actions' }, el('button', {
                    type: 'button', class: 'btn btn-outline btn-sm', text: 'Entwurf verwerfen',
                    onClick: () => {
                        closeEditor(name);
                        BLOCK_RENDERERS[name]();
                    },
                }))));
        if (name === 'title') container.appendChild(note);
        else container.insertBefore(note, container.children[1] || null);
    }

    /**
     * Fehler melden. Ein 404 mit `gone` betrifft ein Teilstueck (Erfassung,
     * Notiz, Auswertung), das inzwischen fehlt -- das Experiment gibt es noch;
     * die Seite laedt neu und zeigt den aktuellen Stand (oder, falls doch das
     * Experiment fehlt, dass es es nicht mehr gibt).
     */
    function reportError(err, gone) {
        if (err && err.status === 409) {
            showConflict(KX.errorMessage(err));
            return;
        }
        if (err && err.status === 404 && gone) {
            KX.toast(gone, 'error');
            load();
            return;
        }
        if (err && err.status === 404 && err.message === 'Nicht gefunden.') {
            KX.toast('Das Experiment gibt es nicht mehr.', 'error');
            return;
        }
        KX.toast(KX.errorMessage(err), 'error');
    }

    /**
     * Die Antwort einer eigenen Aenderung uebernehmen. Ihre row_version ist
     * der Stand nach dem Speichern: offene Editoren ruecken hier mit, und kein
     * spaeter eintreffender, aelterer GET darf ihn wieder verdecken
     * (state.minVersion). Enthaelt die Antwort nicht alles, was die Seite
     * braucht (Definition, Uebergaenge), wird neu geladen.
     */
    async function applySnapshot(exp, sentVersion) {
        if (exp && exp.key === KEY && typeof exp.row_version === 'number') {
            rebaseEditors(sentVersion, exp.row_version);
            state.minVersion = Math.max(state.minVersion, exp.row_version);
        }
        if (exp && exp.key === KEY && exp.definition && Array.isArray(exp.transitions)) {
            state.exp = exp;
            render();
            loadActivity();
            scheduleIndexPoll(true);
            return;
        }
        await load();
    }

    /**
     * Die eigene, gerade gelungene Aenderung hebt row_version um eins. Offene
     * Editoren, die auf der alten Fassung standen, ruecken mit -- das war keine
     * fremde Aenderung. Jeder andere Sprung stammt von jemand anderem und
     * fuehrt beim Speichern zum 409.
     */
    function rebaseEditors(sentVersion, newVersion) {
        if (typeof sentVersion !== 'number' || newVersion !== sentVersion + 1) return;
        state.bases.forEach((base, name) => {
            if (base === sentVersion) state.bases.set(name, newVersion);
        });
    }

    function openEditor(name) {
        state.editors.add(name);
        if (!state.bases.has(name)) state.bases.set(name, state.exp.row_version);
    }

    function closeEditor(name) {
        state.editors.delete(name);
        state.bases.delete(name);
    }

    /** Die Fassung, mit der ein Editor speichert (ohne Editor: die geladene). */
    function versionFor(editor) {
        return editor && state.bases.has(editor) ? state.bases.get(editor) : state.exp.row_version;
    }

    /** PATCH mit der row_version, auf der die Aenderung beruht. */
    async function patchExperiment(changes, editor) {
        const sent = versionFor(editor);
        const body = Object.assign({}, changes, { row_version: sent });
        const data = await KX.api('PATCH', API, body);
        if (editor) closeEditor(editor);
        await applySnapshot(data.experiment, sent);
        return data;
    }

    // ── Laden ───────────────────────────────────────────────────────────

    /**
     * Den Stand lesen und zeigen. Laeuft schon eine Anfrage, wird nach ihr
     * noch einmal gelesen und erst dann gezeichnet: sie kann vor der Aenderung
     * gelesen haben, fuer die der zweite Aufruf neu laedt. Ein Stand unter
     * state.minVersion (aelter als eine bestaetigte eigene Aenderung) wird nie
     * gezeigt.
     */
    async function load() {
        if (!API) {
            renderMissing();
            return;
        }
        if (state.loading) {
            state.reloadAgain = true;
            return state.loading;
        }
        state.loading = (async () => {
            try {
                let exp = null;
                for (let attempt = 0; attempt < 5; attempt += 1) {
                    state.reloadAgain = false;
                    const data = await KX.api('GET', API);
                    exp = data.experiment || null;
                    if (!exp) throw new KX.ApiError('Nicht gefunden.', 404);
                    const outdated = typeof exp.row_version === 'number' && exp.row_version < state.minVersion;
                    if (!state.reloadAgain && !outdated) break;
                }
                state.exp = exp;
                if (state.runsFor !== exp.run_count) {
                    // Neue Laeufe: die nachgeladene Liste waere veraltet.
                    state.runs = null;
                    state.runsNext = null;
                }
                render();
                loadActivity();
                scheduleIndexPoll(true);
            } catch (err) {
                if (err && err.status === 404) renderMissing();
                else if (!state.exp) renderLoadError(err);
                else reportError(err);
            } finally {
                state.loading = null;
            }
        })();
        return state.loading;
    }

    function renderMissing() {
        const header = $('kxHeader');
        header.setAttribute('aria-busy', 'false');
        clear(header).appendChild(KX.emptyState('Dieses Experiment gibt es nicht.',
            'Vielleicht wurde es gelöscht, oder der Schlüssel ist falsch.',
            [el('a', { class: 'btn btn-outline btn-sm', href: '/experiments', text: 'Zu allen Experimenten' })]));
        $('kxMain').hidden = true;
        $('kxSubnav').hidden = true;
        clear($('kxLifecycle'));
    }

    function renderLoadError(err) {
        const header = $('kxHeader');
        header.setAttribute('aria-busy', 'false');
        clear(header).appendChild(el('div', { class: 'kx-banner kx-banner--error', role: 'alert' },
            el('p', { text: KX.errorMessage(err) }),
            el('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Erneut versuchen', onClick: () => load() })));
    }

    /**
     * Solange der Knovas-Stand "ausstehend" ist, nur ihn nachfragen und den
     * Kopf neu zeichnen (row_version, Editoren und die uebrigen Abschnitte
     * bleiben unberuehrt). `restart` beginnt die 5-Minuten-Frist neu -- nach
     * jedem vollen Laden, also nach jeder eigenen Aenderung.
     */
    function scheduleIndexPoll(restart) {
        const idx = state.exp && state.exp.index;
        if (!idx || idx.state !== 'pending') {
            window.clearTimeout(state.indexTimer);
            state.indexTimer = null;
            state.indexPollStarted = null;
            return;
        }
        if (restart || !state.indexPollStarted) state.indexPollStarted = Date.now();
        if (state.indexTimer) return;
        if (Date.now() - state.indexPollStarted > INDEX_POLL_LIMIT_MS) return;
        state.indexTimer = window.setTimeout(async () => {
            state.indexTimer = null;
            if (document.visibilityState !== 'hidden') {
                try {
                    const data = await KX.api('GET', API);
                    const next = data.experiment && data.experiment.index;
                    if (next && state.exp && !state.loading) {
                        state.exp.index = next;
                        renderHeader();
                    }
                } catch (_) { /* weiter versuchen, bis die Frist um ist */ }
            }
            scheduleIndexPoll(false);
        }, INDEX_POLL_MS);
    }

    /** Neu laden, ausser ein Dialog ist offen (er liest den Stand beim
        Absenden). Offene Editoren bleiben stehen und behalten ihre Fassung. */
    function softReload() {
        if (document.querySelector('dialog[open]')) return;
        load();
    }

    function render() {
        const exp = state.exp;
        document.title = `${exp.key} · ${exp.title} · Experimente`;
        renderHeader();
        renderLifecycle();
        if (!state.editors.has('hypothesis')) renderHypothesis();
        if (!state.editors.has('description')) renderDescription();
        if (!state.editors.has('fields')) renderFields();
        renderFacts();
        if (!state.editors.has('variants')) renderVariants();
        if (!state.editors.has('metrics')) renderMetrics();
        renderSampleSize();
        renderMeasurements();
        renderRuns();
        renderEvaluations();
        renderNotes();
        renderDecision();
        $('kxMain').hidden = false;
        $('kxSubnav').hidden = false;
        loadBatchesIfStale();
    }

    // ── Kopf ────────────────────────────────────────────────────────────

    function renderHeader() {
        const exp = state.exp;
        const header = $('kxHeader');
        header.setAttribute('aria-busy', 'false');
        const domain = exp.domain || {};
        const type = exp.type || {};
        const meta = el('div', { class: 'kx-detail-meta' },
            el('span', { class: 'kx-domain' }, KX.domainDot(domain.color), el('span', { text: domain.name || domain.key || '' })),
            el('span', { text: `${type.name || type.key || ''}${type.version ? ` · Version ${type.version}` : ''}` }),
            KX.statusChip(exp.status_label || exp.status, exp.status_phase),
            exp.archived ? KX.chip('archiviert', 'muted') : null);

        const titleRow = el('div', { class: 'kx-title-row' });
        if (state.editors.has('title')) {
            // Beim Neuaufbau das offene Feld samt Eingabe weiterverwenden.
            titleRow.appendChild(header.querySelector('.kx-title-edit') || titleEditor());
        } else {
            titleRow.appendChild(el('h1', { text: exp.title }));
            titleRow.appendChild(el('button', {
                type: 'button', class: 'btn btn-outline btn-sm', text: 'Titel bearbeiten',
                onClick: () => {
                    openEditor('title');
                    renderHeader();
                    const input = header.querySelector('.kx-title-edit input');
                    if (input) input.focus();
                },
            }));
        }

        const tags = el('div', { class: 'kx-tags', role: 'group', 'aria-label': 'Schlagwörter' });
        (exp.tags || []).forEach((tag) => tags.appendChild(el('span', { class: 'kx-tag' },
            el('span', { text: tag }),
            el('button', {
                type: 'button', 'aria-label': `Schlagwort «${tag}» entfernen`, text: '×',
                onClick: () => saveTags((exp.tags || []).filter((t) => t !== tag)),
            }))));
        if ((exp.tags || []).length < 20) {
            const tagInput = KX.input({
                class: 'kx-input kx-tag-input', maxlength: 50, placeholder: '+ Schlagwort',
                'aria-label': 'Schlagwort hinzufügen (Eingabetaste)',
            });
            tagInput.addEventListener('keydown', (e) => {
                if (e.key !== 'Enter') return;
                e.preventDefault();
                const value = tagInput.value.trim();
                if (!value) return;
                if ((exp.tags || []).indexOf(value) !== -1) {
                    tagInput.value = '';
                    return;
                }
                saveTags((exp.tags || []).concat([value]));
            });
            tags.appendChild(tagInput);
        }

        const index = exp.index || {};
        const indexText = index.state === 'error'
            ? `In Knovas: Fehler${index.error ? `: ${index.error}` : ''}`
            : `In Knovas: ${index.state_label || KX.label('index_states', index.state)}`;
        const status = el('div', { class: 'kx-header-status' },
            exp.owner && personName(exp.owner) ? el('span', { text: `Verantwortlich: ${personName(exp.owner)}` }) : null,
            el('span', { text: `Aktualisiert ${KX.fmtDateTime(exp.updated_at)}` }),
            el('span', {
                class: 'kx-index-state', dataset: { state: index.state || '' },
                title: index.indexed_at ? `Zuletzt übertragen ${KX.fmtDateTime(index.indexed_at)}` : null,
            }, el('span', { class: 'status-dot', 'aria-hidden': 'true' }), el('span', { text: indexText })),
            el('span', { class: 'kx-section-actions' },
                el('button', {
                    type: 'button', class: 'btn btn-outline btn-sm', text: 'Neu indexieren',
                    // Aus, wenn die Uebertragung nach Knovas ausgeschaltet ist
                    // (der Server plant dann nichts ein).
                    disabled: Boolean(state.meta && state.meta.index_enabled === false),
                    title: state.meta && state.meta.index_enabled === false
                        ? 'Die Übertragung nach Knovas ist ausgeschaltet.' : null,
                    onClick: (e) => reindex(e.currentTarget),
                }),
                el('button', {
                    type: 'button', class: 'btn btn-outline btn-sm',
                    text: exp.archived ? 'Wiederherstellen' : 'Archivieren',
                    onClick: (e) => toggleArchive(e.currentTarget),
                }),
                state.canManage ? el('button', {
                    type: 'button', class: 'btn btn-danger btn-sm', text: 'Löschen', onClick: deleteExperiment,
                }) : null));
        clear(header);
        header.appendChild(meta);
        header.appendChild(titleRow);
        header.appendChild(tags);
        header.appendChild(status);
    }

    function titleEditor() {
        const input = KX.input({ value: state.exp.title, maxlength: 300, 'aria-label': 'Titel' });
        const save = el('button', { type: 'button', class: 'btn btn-primary btn-sm', text: 'Speichern' });
        const cancel = el('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Abbrechen' });
        const close = () => {
            closeEditor('title');
            renderHeader();
        };
        const submit = async () => {
            const title = input.value.trim();
            if (!title) {
                KX.toast('Der Titel darf nicht leer sein.', 'error');
                input.focus();
                return;
            }
            if (title === state.exp.title) {
                close();
                return;
            }
            save.disabled = true;
            try {
                await patchExperiment({ title }, 'title');
            } catch (err) {
                save.disabled = false;
                reportError(err);
            }
        };
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                submit();
            } else if (e.key === 'Escape') {
                e.preventDefault();
                close();
            }
        });
        save.addEventListener('click', submit);
        cancel.addEventListener('click', close);
        return el('div', { class: 'kx-title-edit' }, input, save, cancel);
    }

    async function saveTags(tags) {
        try {
            await patchExperiment({ tags });
        } catch (err) {
            reportError(err);
        }
    }

    async function reindex(button) {
        button.disabled = true;
        try {
            const data = await KX.api('POST', `${API}/reindex`, {});
            // {queued: false}: die Uebertragung ist ausgeschaltet, der Server
            // hat nur den Stand auf "aus" gesetzt.
            if (data && data.result && data.result.queued) {
                KX.toast('Die Übertragung nach Knovas ist eingeplant.', 'success');
            } else {
                KX.toast('Die Übertragung nach Knovas ist ausgeschaltet; es wurde nichts eingeplant.', 'info');
            }
            await load();
        } catch (err) {
            reportError(err);
        } finally {
            button.disabled = false;
        }
    }

    async function toggleArchive(button) {
        const archived = !state.exp.archived;
        button.disabled = true;
        try {
            await patchExperiment({ archived });
            KX.toast(archived ? 'Das Experiment ist archiviert.' : 'Das Experiment ist wiederhergestellt.', 'success');
        } catch (err) {
            button.disabled = false;
            reportError(err);
        }
    }

    async function deleteExperiment() {
        const ok = await KX.confirm('Experiment löschen',
            `«${state.exp.key} · ${state.exp.title}» wird mit allen Varianten, Messwerten, Läufen, `
            + 'Notizen, Auswertungen und Entscheidungen gelöscht, auch aus der Knovas-Suche. '
            + 'Das lässt sich nicht rückgängig machen.', 'Endgültig löschen', true);
        if (!ok) return;
        try {
            await KX.api('DELETE', API);
            window.location.assign('/experiments');
        } catch (err) {
            reportError(err);
        }
    }

    // ── Lebenszyklus ────────────────────────────────────────────────────

    function renderLifecycle() {
        const exp = state.exp;
        const box = $('kxLifecycle');
        const states = definition().states || [];
        const steps = el('ol', { class: 'kx-steps', 'aria-label': 'Status im Ablauf' });
        states.forEach((s) => {
            const current = s.key === exp.status;
            steps.appendChild(el('li', {
                class: 'kx-step', 'aria-current': current ? 'step' : null,
            }, el('span', { class: 'kx-step-label', text: s.label || s.key }),
            current ? el('span', { class: 'kx-visually-hidden', text: '(aktuell)' }) : null));
        });
        const transitions = el('div', { class: 'kx-transitions' });
        // Ein Schritt nach vorn ist hervorgehoben; Abbrechen bleibt leise.
        let primaryGiven = false;
        (exp.transitions || []).forEach((t) => {
            const target = states.find((s) => s.key === t.to) || {};
            const stops = target.phase === 'stopped';
            const primary = !stops && !primaryGiven && (t.allowed || t.decides);
            if (primary) primaryGiven = true;
            transitions.appendChild(transitionControl(t, primary ? 'primary' : (stops ? 'danger' : 'outline')));
        });
        clear(box).appendChild(el('div', { class: 'kx-lifecycle' }, steps,
            (exp.transitions || []).length ? transitions : null));
    }

    function transitionControl(t, look) {
        const missing = Array.isArray(t.missing) ? t.missing : [];
        const wrap = el('div', { class: 'kx-transition' });
        const button = el('button', {
            type: 'button',
            class: ['btn', 'btn-sm', look === 'primary' ? 'btn-primary' : 'btn-outline', look === 'danger' ? 'btn-danger' : ''],
            text: t.label || t.to,
        });
        if (t.decides) {
            // Die Entscheidung haelt das Formular fest; es prueft die
            // Voraussetzungen mit der neuen Entscheidung zusammen.
            button.addEventListener('click', openDecisionForm);
            wrap.appendChild(button);
            wrap.appendChild(el('small', { class: 'kx-help', text: 'Über das Formular «Entscheidung» festhalten.' }));
            return wrap;
        }
        if (!t.allowed) {
            button.disabled = true;
            if (missing.length) {
                const listId = KX.uid('kx-missing');
                button.setAttribute('aria-describedby', listId);
                wrap.appendChild(button);
                wrap.appendChild(el('ul', { class: 'kx-missing', id: listId },
                    missing.map((m) => el('li', { text: m }))));
                return wrap;
            }
        }
        button.addEventListener('click', () => runTransition(t));
        wrap.appendChild(button);
        return wrap;
    }

    function stateLabel(key) {
        const s = (definition().states || []).find((x) => x.key === key);
        return (s && s.label) || (key == null ? DASH : String(key));
    }

    /**
     * Beschriftung des Dialogs zu einem Statuswechsel. Fuehrt er in die Phase
     * "stopped", heisst die Aktion ausdruecklich "Experiment abbrechen" und ist
     * als gefaehrlich markiert, und der Knopf zum Schliessen heisst "Zurueck"
     * -- sonst stuenden zwei Knoepfe "Abbrechen" nebeneinander, von denen einer
     * das Experiment endgueltig beendet.
     */
    function transitionDialogText(t) {
        const target = (definition().states || []).find((s) => s.key === t.to) || {};
        const stops = target.phase === 'stopped';
        return {
            stops,
            title: stops ? 'Experiment abbrechen' : (t.label || 'Status wechseln'),
            confirm: stops ? 'Experiment abbrechen' : (t.label || 'Wechseln'),
            back: 'Zurück',
            intro: `Der Status wechselt von «${stateLabel(state.exp.status)}» zu «${stateLabel(t.to)}».`,
            final: stops ? 'Ein abgebrochenes Experiment lässt sich nicht fortsetzen.' : null,
        };
    }

    async function runTransition(t) {
        const text0 = transitionDialogText(t);
        const to = stateLabel(t.to);
        const comment = KX.textarea({ name: 'comment', rows: 3, maxlength: 5000 });
        const intro = el('p', { text: text0.intro });
        const body = el('div', null,
            intro,
            text0.final ? el('p', { class: 'kx-help', text: text0.final }) : null,
            KX.field({
                label: t.needs_comment ? 'Grund' : 'Kommentar (optional)', input: comment, name: 'comment',
                required: Boolean(t.needs_comment),
                help: 'Wird als Notiz gespeichert und ist in der Suche auffindbar.',
            }));
        await KX.dialog({
            title: text0.title,
            body,
            actions: [
                { label: text0.back, value: null },
                {
                    label: text0.confirm,
                    primary: true,
                    danger: text0.stops,
                    onClick: async () => {
                        const text = comment.value.trim();
                        if (t.needs_comment && !text) {
                            throw new KX.ApiError('Bitte einen Grund angeben.', 400,
                                { comment: 'Bitte einen Grund für den Abbruch angeben.' });
                        }
                        const sent = state.exp.row_version;
                        const payload = { to: t.to, row_version: sent };
                        if (text) payload.comment = text;
                        let data;
                        try {
                            data = await KX.api('POST', `${API}/transition`, payload);
                        } catch (err) {
                            if (err.status === 409) {
                                // Den neuen Stand laden, der Dialog samt Grund
                                // bleibt offen; ein zweites Bestaetigen sendet
                                // die neue row_version.
                                await load();
                                intro.textContent = transitionDialogText(t).intro;
                                const still = (state.exp.transitions || []).some((x) => x.to === t.to);
                                throw new KX.ApiError(still
                                    ? 'Das Experiment wurde inzwischen geändert; der neue Stand ist geladen. '
                                        + 'Bitte prüfen und erneut bestätigen.'
                                    : 'Das Experiment wurde inzwischen geändert; dieser Statuswechsel ist '
                                        + 'nicht mehr möglich.', 409);
                            }
                            throw err;
                        }
                        KX.toast(`Status: ${to}.`, 'success');
                        await applySnapshot(data.experiment, sent);
                        return true;
                    },
                },
            ],
        });
    }

    // ── Ueberblick ──────────────────────────────────────────────────────

    function renderHypothesis() {
        const box = $('kxHypothesis');
        const text = state.exp.hypothesis || '';
        clear(box).appendChild(blockHead('Hypothese',
            editButton('Bearbeiten', () => openTextEditor('hypothesis', box, 'Hypothese', 20000, false))));
        box.appendChild(text
            ? el('p', { class: 'kx-prose', text })
            : el('p', { class: 'kx-muted', text: 'Noch keine Hypothese. Was wird erwartet, und woran wäre es zu erkennen?' }));
    }

    function renderDescription() {
        const box = $('kxDescription');
        const text = state.exp.description || '';
        clear(box).appendChild(blockHead('Beschreibung',
            editButton('Bearbeiten', () => openTextEditor('description', box, 'Beschreibung', 50000, true))));
        box.appendChild(text ? KX.renderMarkdown(text) : el('p', { class: 'kx-muted', text: 'Keine Beschreibung.' }));
    }

    /** Freitext bearbeiten; die Beschreibung mit Markdown-Vorschau. */
    function openTextEditor(name, box, title, maxLength, markdown) {
        openEditor(name);
        const area = KX.textarea({ rows: markdown ? 10 : 5, maxlength: maxLength, 'aria-label': title },
            state.exp[name] || '');
        const preview = el('div', { class: 'kx-card', hidden: true });
        const write = el('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Schreiben', 'aria-pressed': 'true' });
        const show = el('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Vorschau', 'aria-pressed': 'false' });
        write.addEventListener('click', () => {
            write.setAttribute('aria-pressed', 'true');
            show.setAttribute('aria-pressed', 'false');
            preview.hidden = true;
            area.hidden = false;
            area.focus();
        });
        show.addEventListener('click', () => {
            write.setAttribute('aria-pressed', 'false');
            show.setAttribute('aria-pressed', 'true');
            clear(preview).appendChild(area.value.trim()
                ? KX.renderMarkdown(area.value) : el('p', { class: 'kx-muted', text: 'Nichts zu zeigen.' }));
            preview.hidden = false;
            area.hidden = true;
        });
        const close = () => {
            closeEditor(name);
            if (name === 'hypothesis') renderHypothesis();
            else renderDescription();
        };
        const save = el('button', { type: 'button', class: 'btn btn-primary btn-sm', text: 'Speichern' });
        save.addEventListener('click', async () => {
            save.disabled = true;
            try {
                await patchExperiment({ [name]: markdown ? area.value.replace(/\s+$/, '') : area.value.trim() }, name);
                KX.toast(`${title} gespeichert.`, 'success');
            } catch (err) {
                save.disabled = false;
                reportError(err);
            }
        });
        clear(box).appendChild(blockHead(title));
        if (markdown) {
            box.appendChild(el('div', { class: 'kx-edit-toggle' }, write, show));
            box.appendChild(el('p', { class: 'kx-help', text: 'Markdown: **fett**, *kursiv*, `Code`, Listen mit «- », Links [Text](https://…).' }));
        }
        box.appendChild(area);
        box.appendChild(preview);
        box.appendChild(el('div', { class: 'kx-form-actions' }, save,
            el('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Abbrechen', onClick: close })));
        area.focus();
    }

    function renderFields() {
        const box = $('kxFields');
        const defs = definition().fields || [];
        const exp = state.exp;
        clear(box).appendChild(blockHead('Angaben',
            defs.length ? editButton('Bearbeiten', openFieldsEditor) : null));
        if (!defs.length) {
            box.appendChild(el('p', { class: 'kx-muted', text: 'Dieser Typ hat keine eigenen Angaben.' }));
            return;
        }
        const shown = new Map((exp.fields || []).map((f) => [f.key, f]));
        const list = el('dl', { class: 'kx-fieldlist' });
        defs.forEach((f) => {
            const current = shown.get(f.key);
            const display = current && current.display != null && current.display !== '' ? String(current.display) : DASH;
            list.appendChild(el('dt', { text: f.label || f.key }));
            list.appendChild(f.type === 'url' && current && typeof current.value === 'string' && /^https?:\/\//i.test(current.value)
                ? el('dd', null, el('a', { href: current.value, target: '_blank', rel: 'noopener noreferrer', text: display }))
                : el('dd', { class: f.type === 'longtext' ? 'kx-prose' : null, text: display }));
        });
        box.appendChild(list);
    }

    function openFieldsEditor() {
        openEditor('fields');
        const box = $('kxFields');
        const defs = definition().fields || [];
        const values = state.exp.field_values || {};
        const form = el('form', { class: 'kx-fields-form', noValidate: true });
        const added = state.exp.field_options || {};
        const domainKey = (state.exp.domain || {}).key || '';
        defs.forEach((f) => form.appendChild(KX.renderFieldInput(f, values[f.key], {
            extra: added[f.key],
            onAdd: async (def) => {
                const value = await KX.addFieldOption(domainKey, def, state.exp.key);
                if (value) {
                    state.exp.field_options = Object.assign({}, state.exp.field_options || {});
                    state.exp.field_options[def.key] = (state.exp.field_options[def.key] || []).concat([value]);
                }
                return value;
            },
        })));
        const error = el('div', { class: 'kx-dialog-error', role: 'alert', hidden: true, style: { margin: '12px 0 0' } });
        const save = el('button', { type: 'submit', class: 'btn btn-primary btn-sm', text: 'Angaben speichern' });
        const close = () => {
            closeEditor('fields');
            renderFields();
        };
        form.appendChild(error);
        form.appendChild(el('div', { class: 'kx-form-actions' }, save,
            el('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Abbrechen', onClick: close })));
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            KX.clearFieldErrors(form);
            error.hidden = true;
            const changed = {};
            defs.forEach((f) => {
                const row = form.querySelector(`[data-field-key="${CSS.escape(f.key)}"]`);
                const next = KX.readFieldInput(f, row);
                const before = values[f.key] === undefined ? null : values[f.key];
                if (JSON.stringify(next) !== JSON.stringify(before)) changed[f.key] = next;
            });
            if (!Object.keys(changed).length) {
                close();
                return;
            }
            save.disabled = true;
            try {
                await patchExperiment({ fields: changed }, 'fields');
                KX.toast('Angaben gespeichert.', 'success');
            } catch (err) {
                save.disabled = false;
                if (err.status === 409) {
                    showConflict(err.message);
                    return;
                }
                showEditorError(error, err, KX.showFieldErrors(form, err.fields));
            }
        });
        clear(box).appendChild(blockHead('Angaben'));
        box.appendChild(form);
        const first = form.querySelector('input, select, textarea');
        if (first) first.focus();
    }

    function renderFacts() {
        const exp = state.exp;
        const box = $('kxFacts');
        const rows = [
            ['Erstellt', KX.fmtDate(exp.created_at)],
            ['Gestartet', exp.started_at ? KX.fmtDate(exp.started_at) : DASH],
            ['Beendet', exp.ended_at ? KX.fmtDate(exp.ended_at) : DASH],
            ['Entschieden', exp.decided_at ? KX.fmtDate(exp.decided_at) : DASH],
            ['Messwerte', KX.fmtNumber(Number(exp.measurement_count) || 0, 0)],
            ['Erfassungen', KX.fmtNumber(Number(exp.batch_count) || 0, 0)],
            ['Läufe', KX.fmtNumber(Number(exp.run_count) || 0, 0)],
        ];
        clear(box).appendChild(blockHead('Eckdaten'));
        box.appendChild(el('dl', { class: 'kx-fieldlist' }, rows.map(([k, v]) => [
            el('dt', { text: k }), el('dd', { text: v })])));
    }

    // ── Varianten ───────────────────────────────────────────────────────

    function variantRules() {
        const v = definition().variants || {};
        return {
            min: Number.isFinite(Number(v.min)) ? Number(v.min) : 0,
            max: Number.isFinite(Number(v.max)) ? Number(v.max) : 20,
        };
    }

    function renderVariants() {
        const box = $('kxVariants');
        const variants = state.exp.variants || [];
        const rules = variantRules();
        clear(box).appendChild(blockHead('Varianten',
            rules.max > 0 ? editButton('Varianten bearbeiten', openVariantsEditor) : null));
        if (!variants.length) {
            box.appendChild(el('p', { class: 'kx-muted', text: rules.min === 0
                ? 'Keine Varianten. Dieser Typ kommt auch ohne aus.'
                : `Noch keine Varianten. Dieser Typ braucht mindestens ${rules.min}.` }));
            return;
        }
        box.appendChild(el('div', { class: 'kx-table-wrap' }, el('table', { class: 'kx-table kx-table--compact' },
            el('thead', null, el('tr', null,
                el('th', { scope: 'col', text: 'Schlüssel' }),
                el('th', { scope: 'col', text: 'Name' }),
                el('th', { scope: 'col', text: 'Beschreibung' }),
                el('th', { scope: 'col', class: 'kx-num', text: 'Zuteilung' }),
                el('th', { scope: 'col', text: 'Daten' }))),
            el('tbody', null, variants.map((v) => el('tr', null,
                el('th', { scope: 'row' }, el('span', { class: 'kx-key', text: v.key }), ' ',
                    v.is_control ? KX.chip('Kontrolle', 'neutral') : null),
                el('td', { text: v.name || DASH }),
                el('td', { class: 'kx-prose', text: v.description || '' }),
                el('td', { class: 'kx-num', text: KX.finite(v.allocation) === null ? DASH : KX.fmtEstimate('proportion', v.allocation, '', 0) }),
                el('td', { class: 'kx-muted', text: v.has_data ? 'hat Messwerte' : DASH })))))));
    }

    function openVariantsEditor() {
        openEditor('variants');
        const box = $('kxVariants');
        const rules = variantRules();
        const tbody = el('tbody');
        const controlName = KX.uid('kx-control');
        const addButton = el('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Variante hinzufügen' });

        function updateAdd() {
            addButton.disabled = tbody.children.length >= rules.max;
        }

        function addRow(v) {
            const locked = Boolean(v && v.has_data);
            const key = KX.input({ value: v ? v.key : '', maxlength: 40, 'aria-label': 'Schlüssel', readOnly: locked, class: 'kx-input kx-mono' });
            const name = KX.input({ value: v ? v.name || '' : '', maxlength: 120, 'aria-label': 'Name' });
            const description = KX.input({ value: v ? v.description || '' : '', maxlength: 5000, 'aria-label': 'Beschreibung' });
            const control = el('input', { type: 'radio', name: controlName, 'aria-label': 'Kontrolle' });
            control.checked = Boolean(v && v.is_control);
            const allocation = KX.input({
                inputmode: 'decimal', 'aria-label': 'Zuteilung in Prozent', placeholder: '–',
                value: v && KX.finite(v.allocation) !== null ? KX.fmtPlain(v.allocation * 100, 4).replace(/'/g, '') : '',
            });
            const remove = el('button', {
                type: 'button', class: 'kx-icon-button', text: '×',
                'aria-label': locked ? 'Hat Messwerte und kann nicht entfernt werden' : 'Variante entfernen',
                title: locked ? 'Hat Messwerte und kann nicht entfernt werden.' : 'Entfernen',
                disabled: locked,
            });
            const row = el('tr', { dataset: { original: v ? v.key : '' } },
                el('td', null, key), el('td', null, name), el('td', null, description),
                el('td', { style: { 'text-align': 'center' } }, control),
                el('td', null, allocation), el('td', { class: 'kx-row-actions' }, remove));
            remove.addEventListener('click', () => {
                row.remove();
                updateAdd();
            });
            tbody.appendChild(row);
            updateAdd();
            return row;
        }

        (state.exp.variants || []).forEach((v) => addRow(v));
        addButton.addEventListener('click', () => {
            const row = addRow(null);
            row.querySelector('input').focus();
        });
        const error = el('div', { class: 'kx-dialog-error', role: 'alert', hidden: true, style: { margin: '12px 0 0' } });
        const save = el('button', { type: 'button', class: 'btn btn-primary btn-sm', text: 'Varianten speichern' });
        const close = () => {
            closeEditor('variants');
            renderVariants();
        };
        save.addEventListener('click', async () => {
            error.hidden = true;
            const problems = [];
            const seen = new Set();
            const variants = Array.from(tbody.children).map((row, i) => {
                const inputs = row.querySelectorAll('input');
                const key = inputs[0].value.trim();
                const allocRaw = inputs[4].value.trim();
                const alloc = allocRaw === '' ? null : KX.parseNumber(allocRaw);
                if (!VARIANT_KEY_RE.test(key)) problems.push(`Zeile ${i + 1}: Schlüssel aus Buchstaben, Ziffern, «_», «.» oder «-» (höchstens 40 Zeichen).`);
                else if (seen.has(key)) problems.push(`Zeile ${i + 1}: Der Schlüssel «${key}» kommt doppelt vor.`);
                seen.add(key);
                if (allocRaw !== '' && (alloc === null || alloc < 0 || alloc > 100)) {
                    problems.push(`Zeile ${i + 1}: Die Zuteilung liegt zwischen 0 und 100 %.`);
                }
                return {
                    key,
                    name: inputs[1].value.trim(),
                    description: inputs[2].value.trim(),
                    is_control: inputs[3].checked,
                    allocation: alloc === null ? null : alloc / 100,
                };
            });
            if (variants.length < rules.min) problems.push(`Dieser Typ braucht mindestens ${rules.min} Varianten.`);
            if (variants.length > rules.max) problems.push(`Dieser Typ erlaubt höchstens ${rules.max} Varianten.`);
            // Eingegeben wird in Prozent; die Summe darf 100 % nicht uebersteigen.
            const total = variants.reduce((sum, v) => sum + (v.allocation || 0), 0);
            if (total > 1 + 1e-9) {
                problems.push(`Die Zuteilungen ergeben zusammen ${KX.fmtPlain(total * 100, 2)} %, mehr als 100 %.`);
            }
            if (problems.length) {
                clear(error);
                problems.forEach((p) => error.appendChild(el('p', { text: p })));
                error.hidden = false;
                return;
            }
            save.disabled = true;
            try {
                const sent = versionFor('variants');
                const data = await KX.api('PUT', `${API}/variants`, { variants, row_version: sent });
                closeEditor('variants');
                KX.toast('Varianten gespeichert.', 'success');
                await applySnapshot(data.experiment, sent);
            } catch (err) {
                save.disabled = false;
                if (err.status === 409) {
                    showConflict(err.message);
                    return;
                }
                showEditorError(error, err);
            }
        });
        clear(box).appendChild(blockHead('Varianten'));
        box.appendChild(el('p', { class: 'kx-help', text: rules.min === rules.max
            ? `Dieser Typ verlangt genau ${rules.min} Varianten.`
            : `Mindestens ${rules.min}, höchstens ${rules.max} Varianten. Varianten mit Messwerten bleiben erhalten.` }));
        box.appendChild(el('div', { class: 'kx-table-wrap' }, el('table', { class: 'kx-table kx-table--compact' },
            el('thead', null, el('tr', null,
                el('th', { scope: 'col', text: 'Schlüssel' }),
                el('th', { scope: 'col', text: 'Name' }),
                el('th', { scope: 'col', text: 'Beschreibung' }),
                el('th', { scope: 'col', text: 'Kontrolle' }),
                el('th', { scope: 'col', text: 'Zuteilung %' }),
                el('th', { scope: 'col' }, el('span', { class: 'kx-visually-hidden', text: 'Aktion' })))),
            tbody)));
        box.appendChild(error);
        box.appendChild(el('div', { class: 'kx-form-actions' }, addButton, save,
            el('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Abbrechen', onClick: close })));
    }

    // ── Metriken ────────────────────────────────────────────────────────

    function renderMetrics() {
        const box = $('kxMetrics');
        const metrics = state.exp.metrics || [];
        clear(box).appendChild(blockHead('Metriken', editButton('Metriken bearbeiten', openMetricsEditor)));
        if (!metrics.length) {
            box.appendChild(el('p', { class: 'kx-muted', text: 'Noch keine Metrik zugeordnet. Ohne primäre Metrik lässt sich meist nicht starten.' }));
            return;
        }
        box.appendChild(el('div', { class: 'kx-table-wrap' }, el('table', { class: 'kx-table kx-table--compact' },
            el('thead', null, el('tr', null,
                el('th', { scope: 'col', text: 'Metrik' }),
                el('th', { scope: 'col', text: 'Art' }),
                el('th', { scope: 'col', text: 'Rolle' }),
                el('th', { scope: 'col', text: 'Richtung' }),
                el('th', { scope: 'col', text: 'Leitplanke' }))),
            el('tbody', null, metrics.map((m) => el('tr', null,
                el('th', { scope: 'row' }, el('span', { text: m.name || m.key }),
                    el('span', { class: 'kx-sub kx-mono', text: m.key })),
                el('td', { text: m.kind_label || kindSpec(m.kind).label }),
                el('td', null, KX.chip(m.role_label || KX.label('metric_roles', m.role), m.role === 'primary' ? 'running' : 'muted')),
                el('td', { text: m.direction_label || KX.label('directions', m.direction) }),
                el('td', null, m.role === 'guardrail'
                    ? [el('span', { text: guardrailText(m) }), ' ', guardrailChip(m)]
                    : el('span', { class: 'kx-muted', text: DASH }))))))));
    }

    /** Die waehlbaren Metriken des Bereichs (und globale). `fresh` fragt eine
        leere Liste neu ab: legt jemand unter Verwaltung eine Metrik an, sieht
        man sie beim naechsten Oeffnen des Editors. */
    async function loadCatalog(fresh) {
        if (state.catalog && (state.catalog.length || !fresh)) return state.catalog;
        const domain = (state.exp.domain || {}).key || '';
        const data = await KX.api('GET', `/api/experiments/metrics?domain=${encodeURIComponent(domain)}`);
        state.catalog = (data.metrics || []).filter((m) => !m.archived);
        return state.catalog;
    }

    /**
     * Hinweis, wenn der Bereich keine einzige Metrik hat: Experimentierende
     * koennen keine anlegen (nur Verantwortliche, unter Verwaltung ->
     * Metriken), und ohne Metrik gibt es keine Messwerte und keine Auswertung.
     */
    function emptyCatalogState() {
        const domain = state.exp.domain || {};
        return KX.emptyState(null,
            `Für den Bereich «${domain.name || domain.key || ''}» gibt es noch keine Metriken. `
            + (state.canManage ? 'Legen Sie zuerst eine an.'
                : 'Metriken legen Verantwortliche der Experimente unter Verwaltung → Metriken an.'),
            state.canManage ? [el('a', {
                class: 'btn btn-outline btn-sm', href: '/experiments/verwaltung#metriken',
                text: 'Metrik unter Verwaltung → Metriken anlegen',
            })] : null);
    }

    async function openMetricsEditor() {
        const box = $('kxMetrics');
        let catalog;
        try {
            catalog = await loadCatalog(true);
        } catch (err) {
            reportError(err);
            return;
        }
        // Zugeordnete Metriken bleiben waehlbar, auch wenn sie inzwischen archiviert sind.
        const byKey = new Map(catalog.map((m) => [m.key, m]));
        (state.exp.metrics || []).forEach((m) => { if (!byKey.has(m.key)) byKey.set(m.key, m); });
        const options = Array.from(byKey.values()).sort((a, b) => String(a.name).localeCompare(String(b.name), 'de'));
        if (!options.length) {
            // Nichts zu waehlen: keine leere Auswahlliste, sondern wer weiterhilft.
            clear(box).appendChild(blockHead('Metriken'));
            box.appendChild(emptyCatalogState());
            box.appendChild(el('div', { class: 'kx-form-actions' },
                el('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Schliessen', onClick: renderMetrics })));
            return;
        }
        openEditor('metrics');
        const tbody = el('tbody');

        function addRow(m) {
            const metricSelect = KX.select({ 'aria-label': 'Metrik' },
                [{ value: '', label: 'Metrik wählen' }].concat(options.map((o) => ({
                    value: o.key, label: `${o.name} (${o.kind_label || kindSpec(o.kind).label}${o.unit ? `, ${o.unit}` : ''})`,
                }))), m ? m.key : '');
            const role = KX.select({ 'aria-label': 'Rolle' }, [
                { value: 'primary', label: KX.label('metric_roles', 'primary') },
                { value: 'secondary', label: KX.label('metric_roles', 'secondary') },
                { value: 'guardrail', label: KX.label('metric_roles', 'guardrail') },
            ], m ? m.role : 'secondary');
            const op = KX.select({ 'aria-label': 'Leitplanke' }, [
                { value: 'max', label: 'höchstens' }, { value: 'min', label: 'mindestens' },
            ], m && m.guardrail_op ? m.guardrail_op : 'max');
            const value = KX.input({ inputmode: 'decimal', 'aria-label': 'Grenzwert' });
            const unit = el('span', { class: 'kx-help' });
            const setValue = () => {
                const meta = byKey.get(metricSelect.value);
                const isProp = meta && meta.kind === 'proportion';
                unit.textContent = isProp ? '%' : (meta && meta.unit) || '';
                if (m && m.key === metricSelect.value && KX.finite(m.guardrail_value) !== null && !value.dataset.touched) {
                    value.value = KX.fmtPlain(isProp ? m.guardrail_value * 100 : m.guardrail_value, 6).replace(/'/g, '');
                }
            };
            const syncRole = () => {
                const on = role.value === 'guardrail';
                op.disabled = !on;
                value.disabled = !on;
            };
            value.addEventListener('input', () => { value.dataset.touched = '1'; });
            metricSelect.addEventListener('change', setValue);
            role.addEventListener('change', syncRole);
            setValue();
            syncRole();
            const remove = el('button', { type: 'button', class: 'kx-icon-button', text: '×', 'aria-label': 'Metrik entfernen' });
            const row = el('tr', null,
                el('td', null, metricSelect), el('td', null, role), el('td', null, op),
                el('td', null, el('span', { class: 'kx-domain' }, value, unit)),
                el('td', { class: 'kx-row-actions' }, remove));
            row._read = () => {
                const meta = byKey.get(metricSelect.value);
                const out = { metric: metricSelect.value, role: role.value };
                if (role.value === 'guardrail') {
                    const n = KX.parseNumber(value.value);
                    out.guardrail_op = op.value;
                    out.guardrail_value = n === null ? null : (meta && meta.kind === 'proportion' ? n / 100 : n);
                }
                return out;
            };
            remove.addEventListener('click', () => row.remove());
            tbody.appendChild(row);
            return row;
        }

        (state.exp.metrics || []).forEach((m) => addRow(m));
        const error = el('div', { class: 'kx-dialog-error', role: 'alert', hidden: true, style: { margin: '12px 0 0' } });
        const add = el('button', {
            type: 'button', class: 'btn btn-outline btn-sm', text: 'Metrik hinzufügen',
            disabled: !options.length,
            onClick: () => addRow(null).querySelector('select').focus(),
        });
        const save = el('button', { type: 'button', class: 'btn btn-primary btn-sm', text: 'Metriken speichern' });
        const close = () => {
            closeEditor('metrics');
            renderMetrics();
        };
        save.addEventListener('click', async () => {
            error.hidden = true;
            const rows = Array.from(tbody.children).map((r) => r._read());
            const problems = [];
            const seen = new Set();
            rows.forEach((r, i) => {
                if (!r.metric) problems.push(`Zeile ${i + 1}: Bitte eine Metrik wählen.`);
                else if (seen.has(r.metric)) problems.push(`Zeile ${i + 1}: Die Metrik ist schon zugeordnet.`);
                seen.add(r.metric);
                if (r.role === 'guardrail' && r.guardrail_value === null) {
                    problems.push(`Zeile ${i + 1}: Eine Leitplanke braucht einen Grenzwert.`);
                }
            });
            if (rows.filter((r) => r.role === 'primary').length > 1) problems.push('Es gibt höchstens eine primäre Metrik.');
            if (problems.length) {
                clear(error);
                problems.forEach((p) => error.appendChild(el('p', { text: p })));
                error.hidden = false;
                return;
            }
            save.disabled = true;
            try {
                const sent = versionFor('metrics');
                const data = await KX.api('PUT', `${API}/metrics`, { metrics: rows, row_version: sent });
                closeEditor('metrics');
                KX.toast('Metriken gespeichert.', 'success');
                state.series.clear();
                await applySnapshot(data.experiment, sent);
            } catch (err) {
                save.disabled = false;
                if (err.status === 409) {
                    showConflict(err.message);
                    return;
                }
                showEditorError(error, err);
            }
        });
        clear(box).appendChild(blockHead('Metriken'));
        box.appendChild(el('p', { class: 'kx-help', text: 'Eine primäre Metrik entscheidet; sekundäre ergänzen; Leitplanken dürfen einen Grenzwert nicht verletzen. Anteile als Prozent eingeben.' }));
        box.appendChild(el('div', { class: 'kx-table-wrap' }, el('table', { class: 'kx-table kx-table--compact' },
            el('thead', null, el('tr', null,
                el('th', { scope: 'col', text: 'Metrik' }),
                el('th', { scope: 'col', text: 'Rolle' }),
                el('th', { scope: 'col', text: 'Leitplanke' }),
                el('th', { scope: 'col', text: 'Grenzwert' }),
                el('th', { scope: 'col' }, el('span', { class: 'kx-visually-hidden', text: 'Aktion' })))),
            tbody)));
        box.appendChild(error);
        box.appendChild(el('div', { class: 'kx-form-actions' }, add, save,
            el('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Abbrechen', onClick: close })));
    }

    // ── Stichprobe planen (nur vor dem Start) ───────────────────────────

    function renderSampleSize() {
        const box = $('kxSampleSize');
        const exp = state.exp;
        if (exp.started_at || exp.status_phase) {
            box.hidden = true;
            return;
        }
        // Einmal aufgebaut bleibt der Rechner stehen, samt Eingaben.
        if (box.dataset.rendered === '1') {
            box.hidden = false;
            return;
        }
        box.dataset.rendered = '1';
        box.hidden = false;
        const primary = (exp.metrics || []).find((m) => m.role === 'primary');
        const kind = KX.select({ name: 'kind' }, [
            { value: 'proportion', label: 'Anteil (z. B. Klickrate)' },
            { value: 'mean', label: 'Mittelwert' },
        ], primary && !MEAN_LIKE.has(primary.kind) && primary.kind !== 'ordinal' ? 'proportion'
            : (primary ? 'mean' : 'proportion'));
        const base = KX.input({ name: 'base', inputmode: 'decimal' });
        const sd = KX.input({ name: 'sd', inputmode: 'decimal' });
        const mde = KX.input({ name: 'mde', inputmode: 'decimal' });
        const alpha = KX.input({ name: 'alpha', inputmode: 'decimal', value: '0,05' });
        const power = KX.input({ name: 'power', inputmode: 'decimal', value: '0,8' });
        const result = el('p', { class: 'kx-headline', 'aria-live': 'polite' });
        // Bei mehr als zwei Varianten: das Niveau, mit dem jeder einzelne
        // Vergleich gegen die Kontrolle gerechnet wurde.
        const alphaNote = el('p', { class: 'kx-help', 'aria-live': 'polite' });
        alphaNote.hidden = true;
        // Basisrate aus der Kontrolle vorschlagen, wenn schon Daten da sind.
        if (primary && primary.kind === 'proportion') {
            const control = (exp.variants || []).find((v) => v.is_control);
            const agg = (primary.aggregates || []).find((a) => control && a.variant === control.key);
            if (agg && KX.finite(agg.estimate) !== null) base.value = KX.fmtPlain(agg.estimate * 100, 3).replace(/'/g, '');
        }
        const baseField = KX.field({ label: 'Basisrate (%)', input: base, name: 'base', help: 'Heutiger Anteil, z. B. 1,2 für 1,2 %.' });
        const sdField = KX.field({ label: 'Standardabweichung', input: sd, name: 'sd' });
        // Die Einheit steht unter dem Feld, nicht im Label: ein zweizeiliges
        // Label schoebe das Feld aus der Reihe der uebrigen.
        const mdeField = KX.field({
            label: 'Kleinster relevanter Unterschied', input: mde, name: 'mde',
            help: 'In Prozentpunkten, z. B. 0,3.',
        });
        const sync = () => {
            const isProp = kind.value === 'proportion';
            baseField.hidden = !isProp;
            sdField.hidden = isProp;
            const help = mdeField.querySelector('.kx-help');
            if (help) help.textContent = isProp ? 'In Prozentpunkten, z. B. 0,3.' : 'In der Einheit der Metrik.';
        };
        kind.addEventListener('change', sync);
        sync();
        const form = el('form', { noValidate: true },
            el('div', { class: 'kx-form-row kx-form-row--aligned' },
                KX.field({ label: 'Art der Metrik', input: kind, name: 'kind' }),
                baseField, sdField, mdeField,
                KX.field({ label: 'Signifikanzniveau', input: alpha, name: 'alpha' }),
                KX.field({ label: 'Teststärke', input: power, name: 'power' })),
            el('div', { class: 'kx-form-actions' },
                el('button', { type: 'submit', class: 'btn btn-outline btn-sm', text: 'Berechnen' })),
            result, alphaNote);
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            KX.clearFieldErrors(form);
            alphaNote.textContent = '';
            alphaNote.hidden = true;
            const isProp = kind.value === 'proportion';
            const params = new URLSearchParams({ kind: kind.value });
            const num = (node) => KX.parseNumber(node.value);
            const mdeValue = num(mde);
            if (isProp) {
                const b = num(base);
                if (b === null || mdeValue === null) {
                    result.textContent = 'Bitte Basisrate und Unterschied angeben.';
                    return;
                }
                params.set('base', String(b / 100));
                params.set('mde', String(mdeValue / 100));
            } else {
                const s = num(sd);
                if (s === null || mdeValue === null) {
                    result.textContent = 'Bitte Standardabweichung und Unterschied angeben.';
                    return;
                }
                params.set('sd', String(s));
                params.set('mde', String(mdeValue));
            }
            if (num(alpha) !== null) params.set('alpha', String(num(alpha)));
            if (num(power) !== null) params.set('power', String(num(power)));
            // Die Varianten von jetzt, nicht die vom Aufbau des Rechners:
            // er bleibt stehen, auch wenn Varianten dazukommen.
            const count = Math.max(2, ((state.exp && state.exp.variants) || []).length);
            // Jede weitere Variante ist ein weiterer Vergleich mit der
            // Kontrolle; die Auswertung korrigiert dafuer (Holm), also muss
            // die Planung mit dem strengeren Niveau je Vergleich rechnen.
            if (count > 2) params.set('comparisons', String(count - 1));
            result.textContent = 'Wird berechnet …';
            try {
                const data = await KX.api('GET', `/api/experiments/sample-size?${params.toString()}`);
                const n = data.result && KX.finite(data.result.per_variant);
                result.textContent = n === null || n === undefined ? DASH
                    : `Rund ${KX.fmtNumber(n, 0)} je Variante, bei ${count} Varianten ${KX.fmtNumber(n * count, 0)} insgesamt.`;
                const used = data.result ? KX.finite(data.result.alpha_used) : null;
                const comparisons = data.result ? Number(data.result.comparisons) : 1;
                if (n !== null && n !== undefined && used !== null && used > 0 && comparisons > 1) {
                    // Drei gueltige Stellen: 0,025 / 0,0167 / 0,00333.
                    const digits = Math.max(3, Math.ceil(-Math.log10(used)) + 2);
                    alphaNote.textContent = `Signifikanzniveau je Vergleich: ${KX.fmtPlain(used, digits)} (Bonferroni, ${comparisons} Vergleiche)`;
                    alphaNote.hidden = false;
                }
            } catch (err) {
                const unmatched = KX.showFieldErrors(form, err.fields);
                result.textContent = [KX.errorMessage(err)].concat(unmatched).join(' ');
            }
        });
        clear(box).appendChild(blockHead('Stichprobe planen'));
        box.appendChild(el('p', { class: 'kx-help', text: 'Wie viele Einheiten je Variante nötig sind, um den Unterschied mit der gewählten Sicherheit zu erkennen.' }));
        box.appendChild(form);
    }

    // ── Messwerte ───────────────────────────────────────────────────────

    function renderMeasurements() {
        const exp = state.exp;
        const actions = $('kxMeasurementActions');
        const metrics = exp.metrics || [];
        clear(actions).appendChild(el('button', {
            type: 'button', class: 'btn btn-primary btn-sm', text: 'Messwert erfassen',
            disabled: !metrics.length, onClick: openMeasurementDialog,
        }));
        actions.appendChild(el('button', {
            type: 'button', class: 'btn btn-outline btn-sm', text: 'CSV importieren',
            disabled: !metrics.length, onClick: openCsvDialog,
        }));
        renderImportNote();
        const box = clear($('kxMeasurements'));
        if (!metrics.length) {
            const hint = KX.emptyState(null, 'Zuerst unter «Varianten und Metriken» eine Metrik zuordnen.');
            box.appendChild(hint);
            // Hat der Bereich gar keine Metrik, fuehrt der Hinweis ins Leere:
            // dann sagen, wer eine anlegen kann.
            loadCatalog().then((catalog) => {
                if (!catalog.length && hint.parentNode === box) box.replaceChild(emptyCatalogState(), hint);
            }).catch(() => null);
            return;
        }
        if (!(Number(exp.measurement_count) > 0)) {
            box.appendChild(KX.emptyState(null,
                'Noch keine Messwerte. Erfassen Sie Werte von Hand, laden Sie eine CSV-Datei hoch '
                + 'oder senden Sie sie aus CI (Zugangsschlüssel unter Verwaltung).'));
            return;
        }
        const ordered = metrics.slice().sort((a, b) => roleOrder(a.role) - roleOrder(b.role));
        ordered.forEach((m) => box.appendChild(metricCard(m)));
    }

    function roleOrder(role) {
        return role === 'primary' ? 0 : role === 'secondary' ? 1 : 2;
    }

    function renderImportNote() {
        const box = clear($('kxImportNote'));
        const note = state.importNote;
        if (!note) return;
        box.appendChild(el('div', { class: 'kx-banner kx-banner--info', role: 'status' },
            el('p', { text: note }),
            el('button', {
                type: 'button', class: 'kx-icon-button', 'aria-label': 'Hinweis schliessen', text: '×',
                onClick: () => {
                    state.importNote = null;
                    renderImportNote();
                },
            })));
    }

    /**
     * 95-%-Intervall je Variante fuer die Tabelle einer Metrik. Die Spalten n
     * und Schaetzung zeigen alle Daten (aggregates); ein Intervall daneben
     * muss ueber dieselben Daten gerechnet sein. Also nur aus der neuesten
     * fertigen, nicht ueberholten Auswertung ohne Datenbereich (scope {}) und
     * mit dem Niveau 95 % (alpha 0,05), und je Variante nur, wenn ihr n noch
     * dem der Aggregate entspricht (sonst sind seither Messwerte dazugekommen
     * oder weggefallen). Ohne passendes Intervall bleibt die Zelle leer.
     */
    function intervalsFor(metricKey, aggregates) {
        const nByVariant = {};
        (aggregates || []).forEach((a) => {
            if (a && a.variant != null) nByVariant[a.variant] = Number(a.n);
        });
        const out = {};
        for (const ev of KX.splitEvaluations(state.exp.evaluations).current) {
            if (ev.status !== 'done' || ev.metric_key !== metricKey) continue;
            if (ev.scope && typeof ev.scope === 'object' && Object.keys(ev.scope).length) continue;
            const alpha = KX.finite((ev.params || {}).alpha);
            if (alpha !== null && Math.abs(alpha - 0.05) > 1e-9) continue;
            const output = (state.evalDetails.get(ev.id) || {}).output || ev.output;
            const variants = output && Array.isArray(output.variants) ? output.variants : [];
            let found = false;
            variants.forEach((v) => {
                if (v && KX.finite(v.ci_low) !== null && KX.finite(v.ci_high) !== null && !(v.variant in out)
                        && Number(v.n) === nByVariant[v.variant]) {
                    out[v.variant] = [v.ci_low, v.ci_high];
                    found = true;
                }
            });
            if (found) return out;
        }
        return out;
    }

    function metricCard(m) {
        const spec = kindSpec(m.kind);
        const dec = decimalsOf(m);
        const aggregates = Array.isArray(m.aggregates) ? m.aggregates : [];
        const intervals = intervalsFor(m.key, aggregates);
        const levels = m.definition && m.definition.levels && typeof m.definition.levels === 'object' ? m.definition.levels : null;
        const card = el('article', { class: 'kx-card kx-metric-card', 'aria-labelledby': `kx-metric-${m.key}` });
        const head = el('div', { class: 'kx-metric-head' },
            el('div', null,
                el('h3', { id: `kx-metric-${m.key}` }, el('span', { text: m.name || m.key }),
                    KX.chip(m.role_label || KX.label('metric_roles', m.role), m.role === 'primary' ? 'running' : 'muted'),
                    guardrailChip(m)),
                el('p', { class: 'kx-metric-sub', text: [
                    m.kind_label || spec.label,
                    m.direction_label || KX.label('directions', m.direction),
                    m.unit && m.kind !== 'proportion' ? `Einheit ${m.unit}` : '',
                    m.role === 'guardrail' ? `Leitplanke ${guardrailText(m)}` : '',
                ].filter(Boolean).join(' · ') })));
        card.appendChild(head);
        if (!aggregates.length) {
            card.appendChild(el('p', { class: 'kx-muted', text: 'Für diese Metrik gibt es noch keine Messwerte.' }));
            return card;
        }
        const rows = aggregates.slice().sort((a, b) => variantPos(a.variant) - variantPos(b.variant));
        const total = (a) => Number(a.n) || 0;
        const table = el('table', { class: 'kx-table kx-table--compact' },
            el('thead', null, el('tr', null,
                el('th', { scope: 'col', text: 'Variante' }),
                el('th', { scope: 'col', class: 'kx-num', text: 'n' }),
                el('th', { scope: 'col', class: spec.is_distribution && m.kind === 'categorical' ? null : 'kx-num', text: m.kind === 'categorical' ? 'Verteilung' : 'Schätzung' }),
                m.kind === 'categorical' ? null : el('th', { scope: 'col', class: 'kx-num', text: '95 %-Intervall' }),
                spec.is_distribution && m.kind === 'ordinal' ? el('th', { scope: 'col', text: 'Verteilung' }) : null)),
            el('tbody', null, rows.map((a) => {
                const iv = intervals[a.variant];
                return el('tr', null,
                    el('th', { scope: 'row', text: variantLabel(a.variant) }),
                    el('td', { class: 'kx-num', text: KX.fmtNumber(total(a), 0) }),
                    m.kind === 'categorical'
                        ? el('td', null, levelBars(a.levels, levels, total(a)))
                        : el('td', { class: 'kx-num' }, el('strong', { text: KX.fmtEstimate(m.kind, a.estimate, m.unit, dec) }),
                            m.kind === 'ratio' ? el('span', { class: 'kx-sub', text: `${KX.fmtPlain(a.value_sum)} / ${KX.fmtPlain(a.denominator_sum)}` }) : null),
                    m.kind === 'categorical' ? null : el('td', {
                        class: 'kx-num kx-muted',
                        text: iv ? `${KX.fmtEstimate(m.kind, iv[0], m.unit, dec)} – ${KX.fmtEstimate(m.kind, iv[1], m.unit, dec)}` : DASH,
                    }),
                    spec.is_distribution && m.kind === 'ordinal' ? el('td', null, levelBars(a.levels, levels, total(a))) : null);
            })));
        card.appendChild(el('div', { class: 'kx-table-wrap' }, table));
        if (m.kind !== 'categorical') card.appendChild(chartBlock(m));
        return card;
    }

    function variantPos(key) {
        if (key === null || key === undefined) return 1e6;
        const idx = (state.exp.variants || []).findIndex((v) => v.key === key);
        return idx === -1 ? 1e5 : idx;
    }

    function levelBars(counts, labels, total) {
        // null: der Server hat die Verteilung weggelassen, weil die Variante
        // mehr verschiedene Werte hat, als er zaehlt (eine Skala ohne
        // definierte Stufen). Das sind Daten, nicht "keine Daten".
        if (counts === null) return el('span', { class: 'kx-muted', text: 'Zu viele verschiedene Werte für eine Verteilung' });
        const entries = counts && typeof counts === 'object' ? Object.keys(counts) : [];
        if (!entries.length) return el('span', { class: 'kx-muted', text: DASH });
        const order = labels ? Object.keys(labels) : [];
        entries.sort((a, b) => {
            const ia = order.indexOf(a);
            const ib = order.indexOf(b);
            if (ia !== -1 || ib !== -1) return (ia === -1 ? 1e6 : ia) - (ib === -1 ? 1e6 : ib);
            return Number(a) - Number(b);
        });
        const sum = entries.reduce((s, k) => s + (Number(counts[k]) || 0), 0) || total || 1;
        return el('div', { class: 'kx-levels' }, entries.map((k) => {
            const n = Number(counts[k]) || 0;
            const share = n / sum;
            const name = labels && labels[k] ? `${labels[k]} (${k})` : `Stufe ${k}`;
            return el('div', { class: 'kx-level-row' },
                el('span', { text: name }),
                el('span', { class: 'kx-level-bar', 'aria-hidden': 'true' },
                    el('span', { style: { width: `${Math.round(share * 1000) / 10}%` } })),
                el('span', { class: 'kx-nowrap', text: `${KX.fmtNumber(n, 0)} · ${KX.fmtEstimate('proportion', share, '', 0)}` }));
        }));
    }

    function chartBlock(m) {
        const bucket = state.buckets[m.key] || 'week';
        const holder = el('div', { class: 'kx-chart-holder' });
        const switcher = el('div', { class: 'kx-bucket-switch', role: 'group', 'aria-label': 'Zeitraster' },
            [['day', 'Tag'], ['week', 'Woche'], ['month', 'Monat']].map(([value, text]) => el('button', {
                type: 'button', text, 'aria-pressed': bucket === value ? 'true' : 'false',
                onClick: () => {
                    if (state.buckets[m.key] === value) return;
                    state.buckets[m.key] = value;
                    switcher.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', b.textContent === text ? 'true' : 'false'));
                    loadChart(m, holder);
                },
            })));
        const block = el('div', { class: 'kx-chart-block' },
            el('div', { class: 'kx-block-head', style: { 'margin-top': '14px' } },
                el('h3', { text: 'Verlauf' }), switcher),
            holder);
        loadChart(m, holder);
        return block;
    }

    async function loadChart(m, holder) {
        const bucket = state.buckets[m.key] || 'week';
        const cacheKey = `${m.key}|${bucket}|${state.exp.measurement_count}|${state.exp.batch_count}`;
        const draw = (series) => {
            clear(holder).appendChild(KX.lineChart(series, {
                kind: m.kind, unit: m.unit, decimals: decimalsOf(m), bucket,
                variants: (state.exp.variants || []).map((v) => ({ key: v.key, name: v.name })),
                label: `Verlauf von ${m.name || m.key} je ${bucket === 'day' ? 'Tag' : bucket === 'month' ? 'Monat' : 'Woche'}`,
            }));
        };
        if (state.series.has(cacheKey)) {
            draw(state.series.get(cacheKey));
            return;
        }
        if (holder.firstChild) holder.classList.add('kx-refreshing');
        else holder.appendChild(KX.spinnerText('Verlauf wird geladen …'));
        try {
            const data = await KX.api('GET',
                `${API}/metrics/${encodeURIComponent(m.key)}/timeseries?bucket=${bucket}`);
            const series = Array.isArray(data.series) ? data.series : [];
            state.series.set(cacheKey, series);
            if ((state.buckets[m.key] || 'week') === bucket) draw(series);
        } catch (err) {
            clear(holder).appendChild(el('p', { class: 'kx-muted', text: `Verlauf nicht geladen: ${KX.errorMessage(err)}` }));
        } finally {
            holder.classList.remove('kx-refreshing');
        }
    }

    function variantOptions(withNone) {
        const opts = withNone ? [{ value: '', label: 'ohne Variante' }] : [];
        return opts.concat((state.exp.variants || []).map((v) => ({ value: v.key, label: variantLabel(v.key) })));
    }

    /** "key=value" je Zeile -> dims-Objekt (hoechstens 20 Schluessel). */
    function parseDims(text) {
        const dims = {};
        const lines = String(text || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
        for (const line of lines) {
            const idx = line.indexOf('=');
            const key = idx === -1 ? '' : line.slice(0, idx).trim();
            const value = idx === -1 ? '' : line.slice(idx + 1).trim();
            if (!DIM_KEY_RE.test(key)) throw new KX.ApiError(`Merkmal «${line.slice(0, 40)}»: bitte als name=wert angeben.`, 400, { dims: 'Ein Merkmal je Zeile, z. B. query=q17.' });
            dims[key] = value.slice(0, 200);
        }
        if (Object.keys(dims).length > 20) throw new KX.ApiError('Höchstens 20 Merkmale.', 400, { dims: 'Höchstens 20 Merkmale.' });
        return dims;
    }

    async function openMeasurementDialog() {
        const metrics = state.exp.metrics || [];
        if (!metrics.length) return;
        await KX.meta().then((m) => { state.meta = m; }).catch(() => null);
        const metricSelect = KX.select({ name: 'metric' },
            metrics.map((m) => ({ value: m.key, label: m.name || m.key })), metrics[0].key);
        const variantSelect = KX.select({ name: 'variant' }, variantOptions(true), '');
        const observed = KX.input({ name: 'observed_at', type: 'date', value: KX.todayIso() });
        const dims = KX.textarea({ name: 'dims', rows: 3, placeholder: 'query=q17' });
        const kindBox = el('div', { class: 'kx-form-row' });
        const inputs = {};

        function renderKindInputs() {
            const m = metricByKey(metricSelect.value);
            const spec = kindSpec(m.kind);
            const levels = m.definition && m.definition.levels && typeof m.definition.levels === 'object' ? m.definition.levels : null;
            clear(kindBox);
            Object.keys(inputs).forEach((k) => delete inputs[k]);
            if (levels && Object.keys(levels).length) {
                inputs.value = KX.select({ name: 'value' }, Object.keys(levels).map((k) => ({ value: k, label: `${levels[k]} (${k})` })));
            } else {
                inputs.value = KX.input({ name: 'value', inputmode: 'decimal' });
            }
            kindBox.appendChild(KX.field({
                label: spec.value_label || 'Wert', input: inputs.value, name: 'value', required: true,
                help: m.kind === 'proportion' ? 'Anzahl Erfolge, z. B. Klicks.' : (MEAN_LIKE.has(m.kind) ? 'Ein Messwert (Anzahl 1) oder die Summe mehrerer.' : null),
            }));
            inputs.count = KX.input({ name: 'count', inputmode: 'numeric', value: '1' });
            kindBox.appendChild(KX.field({
                label: spec.count_label || 'Anzahl', input: inputs.count, name: 'count',
                help: m.kind === 'proportion' ? 'Anzahl Versuche, z. B. Impressionen.' : null,
            }));
            if (spec.needs_denominator) {
                inputs.denominator = KX.input({ name: 'denominator', inputmode: 'decimal' });
                kindBox.appendChild(KX.field({ label: spec.denominator_label || 'Nenner', input: inputs.denominator, name: 'denominator', required: true }));
            }
            if (spec.sum_sq_label) {
                inputs.sum_sq = KX.input({ name: 'sum_sq', inputmode: 'decimal' });
                kindBox.appendChild(KX.field({
                    label: spec.sum_sq_label, input: inputs.sum_sq, name: 'sum_sq',
                    help: 'Nur für zusammengefasste Zeilen (Anzahl > 1): Summe der quadrierten Werte.',
                }));
            }
        }
        metricSelect.addEventListener('change', renderKindInputs);
        renderKindInputs();

        const body = el('div', null,
            el('div', { class: 'kx-form-row' },
                KX.field({ label: 'Metrik', input: metricSelect, name: 'metric' }),
                KX.field({ label: 'Variante', input: variantSelect, name: 'variant' }),
                KX.field({ label: 'Beobachtet am', input: observed, name: 'observed_at' })),
            el('div', { style: { 'margin-top': '14px' } }, kindBox),
            el('details', { class: 'kx-details' },
                el('summary', { text: 'Merkmale (optional)' }),
                KX.field({
                    label: 'Merkmale', input: dims, name: 'dims',
                    help: 'Ein Merkmal je Zeile als name=wert, z. B. query=q17 für Werte je Anfrage.',
                })));

        await KX.dialog({
            title: 'Messwert erfassen',
            wide: true,
            body,
            actions: [
                { label: 'Abbrechen', value: null },
                {
                    label: 'Erfassen',
                    primary: true,
                    onClick: async () => {
                        const m = metricByKey(metricSelect.value);
                        const spec = kindSpec(m.kind);
                        const row = { metric: m.key };
                        const errors = {};
                        const value = KX.parseNumber(inputs.value.value);
                        if (value === null) errors.value = 'Bitte eine Zahl eingeben.';
                        else row.value = value;
                        const count = KX.parseNumber(inputs.count.value);
                        if (inputs.count.value.trim() !== '') {
                            if (count === null || !Number.isInteger(count) || count < 1) errors.count = 'Bitte eine ganze Zahl ab 1 eingeben.';
                            else row.count = count;
                        }
                        if (spec.needs_denominator) {
                            const den = KX.parseNumber(inputs.denominator.value);
                            if (den === null || den < 0) errors.denominator = 'Bitte eine Zahl ab 0 eingeben.';
                            else row.denominator = den;
                        }
                        if (inputs.sum_sq && inputs.sum_sq.value.trim() !== '') {
                            const sq = KX.parseNumber(inputs.sum_sq.value);
                            if (sq === null) errors.sum_sq = 'Bitte eine Zahl eingeben.';
                            else row.sum_sq = sq;
                        }
                        if (Object.keys(errors).length) {
                            throw new KX.ApiError('Bitte die markierten Werte prüfen.', 400, errors);
                        }
                        if (variantSelect.value) row.variant = variantSelect.value;
                        if (observed.value) row.observed_at = `${observed.value}T00:00:00+00:00`;
                        const d = parseDims(dims.value);
                        if (Object.keys(d).length) row.dims = d;
                        const data = await KX.api('POST', `${API}/measurements`, { rows: [row] });
                        const inserted = data.result && Number(data.result.inserted);
                        KX.toast(inserted === 1 || !inserted ? 'Messwert erfasst.' : `${KX.fmtNumber(inserted, 0)} Messwerte erfasst.`, 'success');
                        await load();
                        return true;
                    },
                },
            ],
        });
    }

    async function openCsvDialog() {
        const meta = await KX.meta().catch(() => null);
        if (meta) state.meta = meta;
        const maxRows = meta && KX.finite(meta.max_csv_rows) !== null ? meta.max_csv_rows : 200000;
        const file = el('input', { type: 'file', name: 'file', accept: '.csv,.tsv,.txt,text/csv,text/plain', class: 'kx-input' });
        const metricKeys = (state.exp.metrics || []).map((m) => m.key);
        const variantKeys = (state.exp.variants || []).map((v) => v.key);
        const help = el('div', { class: 'kx-help' },
            el('p', { text: 'UTF-8, Trennzeichen Komma, Semikolon oder Tabulator, erste Zeile mit Spaltennamen. '
                + `Höchstens ${KX.fmtNumber(maxRows, 0)} Zeilen und 20 MB je Datei. Zahlen mit Dezimalkomma oder -punkt; `
                + 'Daten als JJJJ-MM-TT oder TT.MM.JJJJ.' }),
            el('p', { class: 'kx-subhead', text: 'Langes Format' }),
            el('p', { text: 'Eine Zeile je Messwert mit den Spalten metric, variant, value, count, denominator, sum_sq, observed_at, '
                + 'run (Lauf-ID aus der Tabelle «Läufe», dort mit «ID kopieren») und dim.<name>.' }),
            el('pre', { class: 'kx-pre', text: 'metric;variant;value;count;observed_at\nctr;A;129;10688;2026-09-14\nctr;B;175;10714;2026-09-14' }),
            el('p', { class: 'kx-subhead', text: 'Breites Format' }),
            el('p', { text: 'Ohne Spalte metric: eine Spalte je Metrik (ihr Schlüssel) und <schlüssel>.count, <schlüssel>.denominator, <schlüssel>.sum_sq. Zum Beispiel wöchentliche LinkedIn-Zahlen:' }),
            el('pre', { class: 'kx-pre', text: 'variant;observed_at;ctr;ctr.count;cost_per_click;cost_per_click.denominator\nA;2026-09-14;129;10688;412,50;129\nB;2026-09-14;175;10714;398,00;175' }),
            el('p', { text: `Metriken dieses Experiments: ${metricKeys.join(', ') || DASH}. Varianten: ${variantKeys.join(', ') || DASH}. Unbekannte Spalten werden übersprungen und nach dem Import genannt.` }));
        await KX.dialog({
            title: 'CSV importieren',
            wide: true,
            body: el('div', null, KX.field({ label: 'Datei', input: file, name: 'file', required: true }), help),
            actions: [
                { label: 'Abbrechen', value: null },
                {
                    label: 'Importieren',
                    primary: true,
                    onClick: async () => {
                        const chosen = file.files && file.files[0];
                        if (!chosen) throw new KX.ApiError('Bitte eine Datei auswählen.', 400, { file: 'Bitte eine Datei auswählen.' });
                        const data = await KX.upload(`${API}/measurements/csv`, chosen);
                        const result = data.result || {};
                        const ignored = Array.isArray(result.ignored_columns) ? result.ignored_columns : [];
                        KX.toast(`${KX.fmtNumber(Number(result.inserted) || 0, 0)} Messwerte importiert.`, 'success');
                        state.importNote = ignored.length
                            ? `Import «${chosen.name}»: nicht verwendete Spalten: ${ignored.join(', ')}.` : null;
                        await load();
                        return true;
                    },
                },
            ],
        });
    }

    // ── Erfassungen (Batches) ───────────────────────────────────────────

    function loadBatchesIfStale() {
        const marker = `${state.exp.batch_count}|${state.exp.measurement_count}`;
        if (state.batches.loadedFor === marker) {
            renderBatches();
            return;
        }
        loadBatches(true, marker);
    }

    async function loadBatches(reset, marker) {
        const box = $('kxBatches');
        if (!(Number(state.exp.batch_count) > 0)) {
            state.batches = { items: [], nextAfter: null, loadedFor: marker };
            clear(box);
            return;
        }
        try {
            const after = reset ? '' : `?after=${encodeURIComponent(state.batches.nextAfter || '')}`;
            const data = await KX.api('GET', `${API}/batches${after}`);
            const result = data.result || {};
            const items = Array.isArray(result.items) ? result.items : [];
            state.batches.items = reset ? items : state.batches.items.concat(items);
            state.batches.nextAfter = result.next_after || null;
            if (marker) state.batches.loadedFor = marker;
            renderBatches();
        } catch (err) {
            clear(box).appendChild(el('p', { class: 'kx-muted', text: `Erfassungen nicht geladen: ${KX.errorMessage(err)}` }));
        }
    }

    function renderBatches() {
        const box = clear($('kxBatches'));
        const items = state.batches.items;
        if (!items.length) return;
        box.appendChild(el('h3', { class: 'kx-subhead', text: 'Erfassungen und Importe' }));
        box.appendChild(el('div', { class: 'kx-table-wrap' }, el('table', { class: 'kx-table kx-table--compact' },
            el('thead', null, el('tr', null,
                el('th', { scope: 'col', text: 'Zeitpunkt' }),
                el('th', { scope: 'col', text: 'Quelle' }),
                el('th', { scope: 'col', class: 'kx-num', text: 'Zeilen' }),
                el('th', { scope: 'col', text: 'Metriken' }),
                el('th', { scope: 'col', text: 'Datei' }),
                el('th', { scope: 'col', text: 'Von' }),
                el('th', { scope: 'col' }, el('span', { class: 'kx-visually-hidden', text: 'Aktion' })))),
            el('tbody', null, items.map((b) => el('tr', null,
                el('td', { class: 'kx-nowrap', text: KX.fmtDateTime(b.created_at) }),
                el('td', { text: b.source_label || KX.label('sources', b.source) }),
                el('td', { class: 'kx-num', text: KX.fmtNumber(Number(b.rows) || 0, 0) }),
                el('td', { class: 'kx-mono', text: (b.metric_keys || []).join(', ') || DASH }),
                el('td', { text: b.filename || DASH }),
                el('td', { text: personName(b.created_by) || DASH }),
                el('td', { class: 'kx-row-actions' }, el('button', {
                    type: 'button', class: 'btn btn-outline btn-sm', text: 'Rückgängig',
                    'aria-label': `Erfassung vom ${KX.fmtDateTime(b.created_at)} rückgängig machen`,
                    onClick: () => undoBatch(b),
                }))))))));
        if (state.batches.nextAfter) {
            box.appendChild(el('div', { class: 'kx-more' }, el('button', {
                type: 'button', class: 'btn btn-outline btn-sm', text: 'Mehr laden',
                onClick: () => loadBatches(false),
            })));
        }
    }

    async function undoBatch(b) {
        const rows = KX.fmtNumber(Number(b.rows) || 0, 0);
        const ok = await KX.confirm('Erfassung rückgängig machen',
            `Die ${rows} Messwerte dieser Erfassung (${b.source_label || KX.label('sources', b.source)}, `
            + `${KX.fmtDateTime(b.created_at)}) werden gelöscht. Auswertungen danach neu starten.`,
            'Messwerte löschen', true);
        if (!ok) return;
        try {
            const data = await KX.api('DELETE', `${API}/batches/${encodeURIComponent(b.batch_id)}`);
            const deleted = data.result && Number(data.result.deleted);
            KX.toast(`${KX.fmtNumber(deleted || 0, 0)} Messwerte gelöscht.`, 'success');
            await load();
        } catch (err) {
            reportError(err, 'Diese Erfassung gibt es nicht mehr.');
        }
    }

    // ── Laeufe ──────────────────────────────────────────────────────────

    function renderRuns() {
        const exp = state.exp;
        clear($('kxRunActions')).appendChild(el('button', {
            type: 'button', class: 'btn btn-outline btn-sm', text: 'Lauf erfassen', onClick: openRunDialog,
        }));
        const box = clear($('kxRuns'));
        const runs = state.runs || exp.runs || [];
        if (!runs.length) {
            box.appendChild(KX.emptyState(null,
                'Noch keine Läufe. Ein Lauf fasst Messwerte einer Durchführung zusammen, etwa eines Benchmarks in CI.'));
            return;
        }
        box.appendChild(el('div', { class: 'kx-table-wrap' }, el('table', { class: 'kx-table kx-table--compact' },
            el('thead', null, el('tr', null,
                el('th', { scope: 'col', text: 'Lauf' }),
                el('th', { scope: 'col', text: 'Variante' }),
                el('th', { scope: 'col', text: 'Status' }),
                el('th', { scope: 'col', text: 'Commit' }),
                el('th', { scope: 'col', text: 'Parameter' }),
                el('th', { scope: 'col', text: 'Metriken' }))),
            el('tbody', null, runs.map(runRow)))));
        const total = Number(exp.run_count) || runs.length;
        if (total > runs.length) {
            box.appendChild(el('div', { class: 'kx-more' }, el('button', {
                type: 'button', class: 'btn btn-outline btn-sm',
                text: `Weitere Läufe laden (${KX.fmtNumber(total - runs.length, 0)})`,
                onClick: (e) => loadMoreRuns(e.currentTarget),
            })));
        }
    }

    /** Die Lauf-ID in die Zwischenablage (fuer die CSV-Spalte run). */
    async function copyRunId(id) {
        const ok = await KX.copyText(id);
        KX.toast(ok ? 'Lauf-ID kopiert.' : `Lauf-ID: ${id}`, ok ? 'success' : 'info');
    }

    function runRow(r) {
        const params = r.params && typeof r.params === 'object' ? Object.keys(r.params) : [];
        const metrics = r.metrics && typeof r.metrics === 'object' ? Object.keys(r.metrics) : [];
        const when = r.ended_at || r.started_at || r.created_at;
        const id = r.id ? String(r.id) : '';
        return el('tr', null,
            el('th', { scope: 'row' }, el('span', { text: r.name || 'ohne Namen' }),
                el('span', { class: 'kx-sub', text: `${KX.fmtDateTime(when)} · ${KX.label('sources', r.source)}` }),
                // Die ID braucht die CSV-Spalte run; gezeigt wird der Anfang,
                // kopiert (und im Tooltip) die ganze.
                id ? el('span', { class: 'kx-sub kx-run-id' },
                    el('span', { class: 'kx-mono', title: id, text: `ID ${id.slice(0, 8)}…` }),
                    el('button', {
                        type: 'button', class: 'kx-link-button', text: 'ID kopieren',
                        'aria-label': `Lauf-ID von «${r.name || 'ohne Namen'}» kopieren`,
                        onClick: () => copyRunId(id),
                    })) : null),
            el('td', { text: r.variant ? variantLabel(r.variant) : DASH }),
            el('td', null, KX.chip(r.status_label || KX.label('run_statuses', r.status),
                r.status === 'failed' ? 'bad' : r.status === 'running' ? 'running' : 'muted')),
            el('td', { class: 'kx-mono', title: r.commit || null, text: r.commit ? String(r.commit).slice(0, 12) : DASH }),
            el('td', { class: 'kx-mono' }, params.length
                ? params.slice(0, 6).map((k) => el('span', { class: 'kx-sub', text: `${k}=${shortJson(r.params[k])}` }))
                : DASH,
            params.length > 6 ? el('span', { class: 'kx-sub', text: `… ${params.length - 6} weitere` }) : null),
            el('td', null, metrics.length
                ? metrics.map((k) => {
                    const m = metricByKey(k);
                    const v = r.metrics[k];
                    const text = m ? KX.fmtEstimate(m.kind, v, m.unit, decimalsOf(m)) : KX.fmtPlain(v);
                    return el('span', { class: 'kx-sub' }, `${m ? m.name : k}: `, el('b', { text }));
                })
                : DASH));
    }

    function shortJson(v) {
        let text;
        try {
            text = typeof v === 'string' ? v : JSON.stringify(v);
        } catch (_) {
            text = String(v);
        }
        return text && text.length > 60 ? `${text.slice(0, 59)}…` : text;
    }

    /**
     * Weitere Laeufe anhaengen. Die Tabelle beginnt mit den Laeufen des
     * Stands (store.SNAPSHOT_RUNS) und liest hinter dem letzten weiter
     * (runs_next_after). Liefert der Server diesen Cursor nicht, beginnt die
     * erste Seite wieder oben -- mit 200 Laeufen, mehr als der Stand zeigt --,
     * und doppelte werden nach id zusammengefuehrt: die Liste waechst immer
     * und schrumpft nie.
     */
    async function loadMoreRuns(button) {
        button.disabled = true;
        try {
            if (!state.runs) {
                state.runs = (state.exp.runs || []).slice();
                state.runsNext = state.exp.runs_next_after || null;
            }
            const url = state.runsNext
                ? `${API}/runs?limit=100&after=${encodeURIComponent(state.runsNext)}`
                : `${API}/runs?limit=200`;
            const data = await KX.api('GET', url);
            const result = data.result || {};
            const items = Array.isArray(result.items) ? result.items : [];
            const seen = new Set(state.runs.map((r) => r && r.id));
            state.runs = state.runs.concat(items.filter((r) => r && !seen.has(r.id)));
            state.runsNext = result.next_after || null;
            state.runsFor = state.exp.run_count;
            if (!state.runsNext && state.runs.length < (Number(state.exp.run_count) || 0)) {
                state.exp.run_count = state.runs.length;
            }
            renderRuns();
        } catch (err) {
            button.disabled = false;
            reportError(err);
        }
    }

    /**
     * Fehler zu einem Lauf auf die Felder des Formulars beziehen. Aeltere
     * Server nennen die Messwerte eines Laufs "Messwert 1" und schluesseln sie
     * als rows.<i>.<feld>; Zeile i ist die i-te Metrik des gesendeten Objekts
     * metrics (der Server behaelt dessen Reihenfolge). Neuere senden schon
     * metrics.<schluessel> -- dann bleibt der Fehler, wie er ist.
     */
    function runErrorForForm(err, order) {
        if (!err || err.status !== 400 || !err.fields || !order.length) return err;
        const fields = {};
        let changed = false;
        Object.keys(err.fields).forEach((k) => {
            const m = /^rows\.(\d+)\.([A-Za-z_]+)$/.exec(k);
            const i = m ? Number(m[1]) : -1;
            if (m && i < order.length) {
                const base = `metrics.${order[i]}`;
                fields[['value', 'row', 'metric'].indexOf(m[2]) !== -1 ? base : `${base}.${m[2]}`] = err.fields[k];
                changed = true;
            } else {
                fields[k] = err.fields[k];
            }
        });
        if (!changed) return err;
        const message = String(err.message).replace(/Messwert (\d+):/g, (all, n) => {
            const key = order[Number(n) - 1];
            if (!key) return all;
            const metric = metricByKey(key);
            return `«${(metric && metric.name) || key}»:`;
        });
        return new KX.ApiError(message, err.status, fields);
    }

    async function openRunDialog() {
        const name = KX.input({ name: 'name', maxlength: 200, placeholder: 'z. B. nightly 2026-09-28' });
        const variant = KX.select({ name: 'variant' }, variantOptions(true), '');
        const status = KX.select({ name: 'status' }, ['finished', 'failed', 'cancelled'].map((s) => ({
            value: s, label: KX.label('run_statuses', s),
        })), 'finished');
        const commit = KX.input({ name: 'commit', maxlength: 200, class: 'kx-input kx-mono' });
        const params = KX.textarea({ name: 'params', rows: 3, class: 'kx-input kx-textarea kx-mono', placeholder: '{"top_k": 20}' });
        const note = KX.textarea({ name: 'note', rows: 2 });
        const metricInputs = [];
        const metricBox = el('div', { class: 'kx-form-row' });
        (state.exp.metrics || []).forEach((m) => {
            const spec = kindSpec(m.kind);
            if (m.kind === 'ordinal' || m.kind === 'categorical') return;
            if (MEAN_LIKE.has(m.kind)) {
                const input = KX.input({ inputmode: 'decimal' });
                metricInputs.push({ m, value: input });
                metricBox.appendChild(KX.field({ label: `${m.name}${m.unit ? ` (${m.unit})` : ''}`, input, name: `metrics.${m.key}`, aliases: [m.key] }));
                return;
            }
            const value = KX.input({ inputmode: 'decimal' });
            const second = KX.input({ inputmode: 'decimal' });
            const secondIsDenominator = Boolean(spec.needs_denominator);
            metricInputs.push({ m, value, second, secondIsDenominator });
            metricBox.appendChild(KX.field({ label: `${m.name}: ${spec.value_label}`, input: value, name: `metrics.${m.key}`, aliases: [m.key] }));
            metricBox.appendChild(KX.field({ label: `${m.name}: ${secondIsDenominator ? spec.denominator_label : spec.count_label}`, input: second, name: `metrics.${m.key}.${secondIsDenominator ? 'denominator' : 'count'}` }));
        });
        const body = el('div', null,
            el('div', { class: 'kx-form-row' },
                KX.field({ label: 'Name', input: name, name: 'name' }),
                KX.field({ label: 'Variante', input: variant, name: 'variant' }),
                KX.field({ label: 'Status', input: status, name: 'status' }),
                KX.field({ label: 'Commit', input: commit, name: 'commit' })),
            KX.field({ label: 'Parameter (JSON-Objekt)', input: params, name: 'params' }),
            metricInputs.length ? el('p', { class: 'kx-subhead', text: 'Messwerte des Laufs (optional)' }) : null,
            metricBox,
            KX.field({ label: 'Notiz', input: note, name: 'note' }));
        await KX.dialog({
            title: 'Lauf erfassen',
            description: 'Läufe kommen meist aus CI über das Python-SDK; hier lassen sie sich von Hand nachtragen.',
            wide: true,
            body,
            actions: [
                { label: 'Abbrechen', value: null },
                {
                    label: 'Lauf speichern',
                    primary: true,
                    onClick: async () => {
                        const payload = { status: status.value };
                        const errors = {};
                        if (name.value.trim()) payload.name = name.value.trim();
                        if (variant.value) payload.variant = variant.value;
                        if (commit.value.trim()) payload.commit = commit.value.trim();
                        if (note.value.trim()) payload.note = note.value.trim();
                        if (params.value.trim()) {
                            try {
                                const parsed = JSON.parse(params.value);
                                if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('kein Objekt');
                                payload.params = parsed;
                            } catch (_) {
                                errors.params = 'Bitte ein JSON-Objekt angeben, z. B. {"top_k": 20}.';
                            }
                        }
                        const metrics = {};
                        metricInputs.forEach((entry) => {
                            const key = entry.m.key;
                            const raw = entry.value.value.trim();
                            const rawSecond = entry.second ? entry.second.value.trim() : '';
                            if (!raw && !rawSecond) return;
                            const v = KX.parseNumber(raw);
                            if (v === null) {
                                errors[`metrics.${key}`] = 'Bitte eine Zahl eingeben.';
                                return;
                            }
                            if (!entry.second) {
                                metrics[key] = v;
                                return;
                            }
                            const s = KX.parseNumber(rawSecond);
                            if (s === null) {
                                errors[`metrics.${key}.${entry.secondIsDenominator ? 'denominator' : 'count'}`] = 'Bitte eine Zahl eingeben.';
                                return;
                            }
                            metrics[key] = entry.secondIsDenominator ? { value: v, denominator: s } : { value: v, count: s };
                        });
                        if (Object.keys(errors).length) throw new KX.ApiError('Bitte die markierten Angaben prüfen.', 400, errors);
                        if (Object.keys(metrics).length) payload.metrics = metrics;
                        let data;
                        try {
                            data = await KX.api('POST', `${API}/runs`, payload);
                        } catch (err) {
                            throw runErrorForForm(err, Object.keys(metrics));
                        }
                        const saved = (data && data.run) || {};
                        KX.toast(saved.id ? `Lauf gespeichert. Lauf-ID: ${saved.id}` : 'Lauf gespeichert.', 'success');
                        state.runs = null;
                        state.runsNext = null;
                        await load();
                        return true;
                    },
                },
            ],
        });
    }

    // ── Auswertungen ────────────────────────────────────────────────────

    function renderEvaluations() {
        const exp = state.exp;
        const actions = clear($('kxEvaluationActions'));
        actions.appendChild(el('button', {
            type: 'button', class: 'btn btn-outline btn-sm', text: 'Auswertung starten',
            disabled: !(exp.metrics || []).length, onClick: openEvaluationDialog,
        }));
        actions.appendChild(el('button', {
            type: 'button', class: 'btn btn-primary btn-sm', text: 'Alle Auswertungen des Typs ausführen',
            onClick: (e) => runPipeline(e.currentTarget),
        }));
        const box = clear($('kxEvaluations'));
        const evaluations = exp.evaluations || [];
        if (!evaluations.length) {
            box.appendChild(KX.emptyState(null,
                'Noch keine Auswertung. «Alle Auswertungen des Typs ausführen» startet die vorgesehenen.'));
            return;
        }
        // Oben nur das aktuelle Ergebnis je Auswerter, Metrik, Parameter und
        // Datenbereich; was eine neuere Auswertung derselben Art abgeloest hat
        // (fruehere Daten, wiederholte Laeufe), steht zugeklappt darunter.
        const { current, earlier } = KX.splitEvaluations(evaluations);
        state.supersededIds = new Set(earlier.map((ev) => ev.id));
        current.forEach((ev) => box.appendChild(evaluationCard(ev)));
        if (earlier.length) {
            const details = el('details', { class: 'kx-details kx-earlier-evaluations', open: state.earlierOpen },
                el('summary', { text: `Frühere Auswertungen (${KX.fmtNumber(earlier.length, 0)})` }),
                el('p', { class: 'kx-help', text: 'Abgelöst von einer neueren Auswertung mit demselben Auswerter, '
                    + 'derselben Metrik, denselben Parametern und demselben Datenbereich. Sie beschreiben einen '
                    + 'früheren Stand der Daten.' }),
                earlier.map((ev) => evaluationCard(ev)));
            details.addEventListener('toggle', () => { state.earlierOpen = details.open; });
            box.appendChild(details);
        }
        evaluations.forEach((ev) => {
            if (ev.status === 'queued' || ev.status === 'running') startPoll(ev.id);
        });
    }

    function evaluationCard(evIn) {
        const detail = state.evalDetails.get(evIn.id);
        const ev = detail ? Object.assign({}, evIn, detail) : evIn;
        const metric = metricByKey(ev.metric_key);
        const custom = ev.language && ev.language !== 'builtin';
        const pending = ev.status === 'queued' || ev.status === 'running';
        const superseded = Boolean(state.supersededIds && state.supersededIds.has(ev.id));
        const card = el('article', {
            class: ['kx-card', 'kx-eval-card', superseded ? 'kx-eval-card--superseded' : ''],
            dataset: { evaluationId: ev.id },
            'aria-busy': pending ? 'true' : 'false',
        });
        const metaParts = [
            scopeText(ev.scope),
            TRIGGER_LABELS[ev.trigger] || ev.trigger,
            KX.fmtDateTime(ev.finished_at || ev.created_at),
            ev.requested_by && personName(ev.requested_by) ? personName(ev.requested_by) : null,
            KX.finite(ev.duration_ms) !== null ? `${KX.fmtNumber(ev.duration_ms / 1000, 1)} s` : null,
            ev.evaluator_version ? `Version ${ev.evaluator_version}` : null,
        ].filter(Boolean);
        let statusNode;
        if (ev.status === 'done') statusNode = KX.verdictChip(ev.verdict);
        else if (ev.status === 'failed') statusNode = KX.chip(ev.status_label || KX.label('evaluation_statuses', 'failed'), 'bad');
        else statusNode = KX.chip(ev.status_label || KX.label('evaluation_statuses', ev.status), 'running');
        card.appendChild(el('div', { class: 'kx-eval-head' },
            el('div', null,
                el('h3', { class: 'kx-eval-title' },
                    el('span', { text: ev.evaluator_name || ev.evaluator_key }),
                    custom ? KX.chip(ev.language, 'lang') : null,
                    el('span', { class: 'kx-muted', text: `· ${metric ? metric.name : (ev.metric_key || 'ohne Metrik')}` }),
                    superseded ? KX.chip('überholt', 'muted', {
                        title: 'Eine neuere Auswertung derselben Art hat diese abgelöst.',
                    }) : null),
                el('p', { class: 'kx-eval-meta', text: metaParts.join(' · ') })),
            statusNode));
        if (ev.headline) card.appendChild(el('p', { class: 'kx-headline', text: ev.headline }));
        if (ev.status === 'failed') {
            card.appendChild(el('div', { class: 'kx-banner kx-banner--error' },
                el('p', { text: ev.error || 'Die Auswertung ist fehlgeschlagen.' })));
        }
        if (pending) {
            const polling = state.polls.get(ev.id);
            card.appendChild(el('p', { class: 'kx-muted', text: polling && polling.gaveUp
                ? 'Die Auswertung dauert ungewöhnlich lange. Die Seite später neu laden.'
                : (ev.status === 'queued' ? 'Wartet auf die Rechenumgebung …' : 'Wird berechnet …') }));
        }
        const output = ev.output && typeof ev.output === 'object' ? ev.output : null;
        if (output) {
            appendOutput(card, output, metric);
        } else if (ev.status === 'done') {
            card.appendChild(el('button', {
                type: 'button', class: 'kx-link-button', text: 'Details laden',
                onClick: (e) => loadEvaluationDetail(ev.id, e.currentTarget),
            }));
        }
        if (custom && (ev.status === 'done' || ev.status === 'failed')) {
            const logBox = el('div');
            card.appendChild(el('div', { class: 'kx-form-actions' }, el('button', {
                type: 'button', class: 'btn btn-outline btn-sm', text: 'Protokoll',
                'aria-expanded': 'false',
                onClick: (e) => toggleLogs(ev.id, e.currentTarget, logBox),
            })));
            card.appendChild(logBox);
        }
        return card;
    }

    function appendOutput(card, output, metric) {
        const comparisons = Array.isArray(output.comparisons) ? output.comparisons.filter((c) => c && typeof c === 'object') : [];
        if (comparisons.length) {
            const formats = comparisons.map((c) => comparisonFormat(c, metric));
            // Eine gemeinsame Skala, damit die Balken untereinander vergleichbar sind.
            let extent = 0;
            comparisons.forEach((c, i) => {
                [c.estimate, c.ci_low, c.ci_high].forEach((v) => {
                    if (KX.finite(v) !== null) extent = Math.max(extent, Math.abs(v - formats[i].reference));
                });
            });
            const list = el('div', { class: 'kx-comparisons' });
            comparisons.forEach((c, i) => {
                const f = formats[i];
                const stats = el('div', { class: 'kx-comparison-stats' },
                    el('span', null, `${c.label || 'Differenz'} `, el('b', { text: f.fmt(c.estimate) })),
                    KX.finite(c.ci_low) !== null && KX.finite(c.ci_high) !== null
                        ? el('span', null, '95 %-KI ', el('b', { text: `${f.fmt(c.ci_low)} bis ${f.fmt(c.ci_high)}` })) : null,
                    KX.finite(c.relative) !== null ? el('span', null, 'relativ ', el('b', { text: fmtRelative(c.relative) })) : null,
                    KX.finite(c.p_value) !== null ? el('span', null, 'p ', el('b', { text: KX.fmtPValue(c.p_value) })) : null,
                    KX.finite(c.prob_better) !== null ? el('span', null, 'P(besser) ', el('b', { text: KX.fmtPercent(c.prob_better) })) : null,
                    c.verdict ? KX.verdictChip(c.verdict) : null);
                list.appendChild(el('div', { class: 'kx-comparison' },
                    el('div', { class: 'kx-comparison-name' },
                        el('strong', { text: `${variantLabel(c.variant)}` }),
                        el('span', { class: 'kx-muted', text: `gegenüber ${variantLabel(c.baseline)}` })),
                    KX.intervalBar(c.estimate, c.ci_low, c.ci_high, {
                        direction: metric ? metric.direction : 'none', kind: metric ? metric.kind : null,
                        unit: c.unit, reference: f.reference, extent: extent * 1.15 || null, formatter: f.fmt,
                    }),
                    stats));
            });
            card.appendChild(list);
        }
        const variants = Array.isArray(output.variants) ? output.variants.filter((v) => v && typeof v === 'object') : [];
        if (variants.length) {
            const kind = metric ? metric.kind : null;
            const unit = metric ? metric.unit : '';
            const dec = decimalsOf(metric);
            const fmtV = (x) => (kind ? KX.fmtEstimate(kind, x, unit, dec) : KX.fmtPlain(x));
            card.appendChild(el('div', { class: 'kx-table-wrap', style: { 'margin-top': '12px' } },
                el('table', { class: 'kx-table kx-table--compact' },
                    el('thead', null, el('tr', null,
                        el('th', { scope: 'col', text: 'Variante' }),
                        el('th', { scope: 'col', class: 'kx-num', text: 'n' }),
                        el('th', { scope: 'col', class: 'kx-num', text: 'Wert' }),
                        el('th', { scope: 'col', class: 'kx-num', text: '95 %-Intervall' }),
                        el('th', { scope: 'col', class: 'kx-num', text: 'Standardabw.' }))),
                    el('tbody', null, variants.map((v) => el('tr', null,
                        el('th', { scope: 'row', text: variantLabel(v.variant) }),
                        el('td', { class: 'kx-num', text: KX.fmtNumber(v.n, 0) }),
                        el('td', { class: 'kx-num', text: fmtV(v.value) }),
                        el('td', {
                            class: 'kx-num',
                            text: KX.finite(v.ci_low) !== null && KX.finite(v.ci_high) !== null
                                ? `${fmtV(v.ci_low)} – ${fmtV(v.ci_high)}` : DASH,
                        }),
                        el('td', { class: 'kx-num', text: KX.finite(v.sd) !== null ? KX.fmtNumber(v.sd, dec == null ? 3 : dec) : DASH })))))));
        }
        const table = output.table && typeof output.table === 'object' ? output.table : null;
        if (table && Array.isArray(table.headers) && Array.isArray(table.rows) && table.rows.length) {
            card.appendChild(el('details', { class: 'kx-details' },
                el('summary', { text: 'Tabelle' }),
                el('div', { class: 'kx-table-wrap' }, el('table', { class: 'kx-table kx-table--compact' },
                    el('thead', null, el('tr', null, table.headers.map((h) => el('th', { scope: 'col', text: String(h) })))),
                    el('tbody', null, table.rows.filter(Array.isArray).map((r) => el('tr', null,
                        r.map((cell) => el('td', { text: cell == null ? '' : String(cell) })))))))));
        }
        const warnings = Array.isArray(output.warnings) ? output.warnings : [];
        if (warnings.length) {
            card.appendChild(el('ul', { class: 'kx-warnings', 'aria-label': 'Warnungen' },
                warnings.map((w) => el('li', { text: String(w) }))));
        }
        const values = output.values && typeof output.values === 'object' ? Object.keys(output.values) : [];
        if (values.length) {
            card.appendChild(el('details', { class: 'kx-details' },
                el('summary', { text: 'Weitere Werte' }),
                el('dl', { class: 'kx-fieldlist' }, values.map((k) => {
                    const v = output.values[k];
                    const text = typeof v === 'number' ? KX.fmtPlain(v) : typeof v === 'boolean' ? (v ? 'ja' : 'nein') : String(v);
                    return [el('dt', { text: k }), el('dd', { text })];
                }))));
        }
        if (output.summary) {
            const summary = String(output.summary);
            card.appendChild(el('details', { class: 'kx-details', open: summary.length < 600 },
                el('summary', { text: 'Zusammenfassung' }),
                KX.renderMarkdown(summary)));
        }
    }

    function replaceEvaluationCard(id) {
        const ev = (state.exp.evaluations || []).find((e) => e.id === id);
        const old = document.querySelector(`.kx-eval-card[data-evaluation-id="${CSS.escape(String(id))}"]`);
        if (ev && old) old.replaceWith(evaluationCard(ev));
    }

    async function loadEvaluationDetail(id, button) {
        if (button) button.disabled = true;
        try {
            const data = await KX.api('GET', `${API}/evaluations/${encodeURIComponent(id)}`);
            if (data.evaluation) state.evalDetails.set(id, data.evaluation);
            replaceEvaluationCard(id);
            return data.evaluation;
        } catch (err) {
            if (button) button.disabled = false;
            reportError(err, 'Diese Auswertung gibt es nicht mehr.');
            return null;
        }
    }

    async function toggleLogs(id, button, box) {
        if (button.getAttribute('aria-expanded') === 'true') {
            button.setAttribute('aria-expanded', 'false');
            clear(box);
            return;
        }
        let ev = state.evalDetails.get(id);
        if (!ev || ev.logs === undefined) {
            button.disabled = true;
            try {
                const data = await KX.api('GET', `${API}/evaluations/${encodeURIComponent(id)}`);
                ev = data.evaluation || {};
                state.evalDetails.set(id, ev);
            } catch (err) {
                reportError(err, 'Diese Auswertung gibt es nicht mehr.');
                return;
            } finally {
                button.disabled = false;
            }
        }
        button.setAttribute('aria-expanded', 'true');
        clear(box).appendChild(el('pre', { class: 'kx-pre', text: ev.logs ? String(ev.logs) : 'Kein Protokoll.' }));
    }

    /** queued/running alle 3 s nachfragen, hoechstens 10 Minuten lang. */
    function startPoll(id) {
        if (state.polls.has(id)) return;
        const entry = { started: Date.now(), timer: null, gaveUp: false };
        state.polls.set(id, entry);
        const tick = async () => {
            if (Date.now() - entry.started > POLL_LIMIT_MS) {
                entry.gaveUp = true;
                replaceEvaluationCard(id);
                return;
            }
            try {
                const data = await KX.api('GET', `${API}/evaluations/${encodeURIComponent(id)}`);
                const ev = data.evaluation;
                if (ev && ev.status !== 'queued' && ev.status !== 'running') {
                    state.evalDetails.set(id, ev);
                    const list = state.exp.evaluations || [];
                    const idx = list.findIndex((e) => e.id === id);
                    if (idx !== -1) list[idx] = Object.assign({}, list[idx], ev);
                    state.polls.delete(id);
                    replaceEvaluationCard(id);
                    // Intervalle, Leitplanken und Knovas-Stand haengen am Ergebnis.
                    if (![...state.polls.keys()].length) softReload();
                    return;
                }
            } catch (err) {
                if (err && err.status === 404) {
                    state.polls.delete(id);
                    return;
                }
            }
            entry.timer = window.setTimeout(tick, POLL_MS);
        };
        entry.timer = window.setTimeout(tick, POLL_MS);
    }

    function paramDefaults(schema) {
        const props = schema && schema.properties && typeof schema.properties === 'object' ? schema.properties : {};
        const out = {};
        Object.keys(props).forEach((k) => {
            if (props[k] && Object.prototype.hasOwnProperty.call(props[k], 'default')) out[k] = props[k].default;
        });
        return out;
    }

    function paramHelp(schema) {
        const props = schema && schema.properties && typeof schema.properties === 'object' ? schema.properties : {};
        const keys = Object.keys(props);
        if (!keys.length) return 'Dieser Auswerter hat keine Parameter.';
        return keys.map((k) => {
            const p = props[k] || {};
            return `${k}: ${p.description || p.type || ''}`.trim();
        }).join(' · ');
    }

    async function openEvaluationDialog() {
        const meta = await KX.meta().catch(() => null);
        if (meta) state.meta = meta;
        const runnerOk = Boolean(meta && meta.runner && meta.runner.ok);
        const metrics = state.exp.metrics || [];
        const evaluators = (state.exp.evaluators || []).filter((e) => !e.archived);
        const primary = metrics.find((m) => m.role === 'primary') || metrics[0];
        const metricSelect = KX.select({ name: 'metric' },
            metrics.map((m) => ({ value: m.key, label: `${m.name} (${m.role_label || KX.label('metric_roles', m.role)})` })),
            primary ? primary.key : '');
        const evaluatorSelect = KX.select({ name: 'evaluator' }, [], null);
        const runnerHint = el('p', {
            class: 'kx-help', hidden: true,
            text: 'Python- und Julia-Auswerter brauchen die Rechenumgebung (Profil experiments).',
        });
        const evaluatorHelp = el('p', { class: 'kx-help' });
        const scopeName = KX.uid('kx-scope');
        const scopeChoice = (value, text, checked) => {
            const radio = el('input', { type: 'radio', name: scopeName, value });
            radio.checked = Boolean(checked);
            return el('label', { class: 'kx-choice' }, radio, el('span', { text }));
        };
        const scopeGroup = el('div', { class: 'kx-choice-group', role: 'radiogroup', 'aria-label': 'Datenbereich' },
            scopeChoice('all', 'Alle Daten', true),
            scopeChoice('latest', 'Neuester Lauf je Variante', false),
            scopeChoice('period', 'Zeitraum', false));
        const since = KX.input({ type: 'date', name: 'since' });
        const until = KX.input({ type: 'date', name: 'until' });
        const period = el('div', { class: 'kx-form-row', hidden: true, style: { 'margin-top': '10px' } },
            KX.field({ label: 'Von', input: since, name: 'since', aliases: ['scope.since'] }),
            KX.field({ label: 'Bis', input: until, name: 'until', aliases: ['scope.until'] }));
        scopeGroup.addEventListener('change', () => {
            const chosen = scopeGroup.querySelector('input:checked');
            period.hidden = !chosen || chosen.value !== 'period';
        });
        const params = KX.textarea({ name: 'params', rows: 4, class: 'kx-input kx-textarea kx-mono' }, '{}');
        let paramsTouched = false;
        params.addEventListener('input', () => { paramsTouched = true; });

        function currentEvaluator() {
            return evaluators.find((e) => e.key === evaluatorSelect.value) || null;
        }

        function fillEvaluators() {
            const m = metricByKey(metricSelect.value);
            const previous = evaluatorSelect.value;
            clear(evaluatorSelect);
            const usable = evaluators.filter((e) => !m || !Array.isArray(e.input_kinds) || !e.input_kinds.length
                || e.input_kinds.indexOf(m.kind) !== -1);
            let anyCustom = false;
            usable.forEach((e) => {
                const isCustom = e.language && e.language !== 'builtin';
                if (isCustom) anyCustom = true;
                const option = el('option', { value: e.key },
                    isCustom ? `${e.name} (${e.language})` : e.name);
                if (isCustom && !runnerOk) option.disabled = true;
                evaluatorSelect.appendChild(option);
            });
            if (!usable.length) evaluatorSelect.appendChild(el('option', { value: '' }, 'Kein Auswerter für diese Metrik'));
            runnerHint.hidden = !(anyCustom && !runnerOk);
            const keep = usable.find((e) => e.key === previous && !(e.language !== 'builtin' && !runnerOk));
            const firstUsable = usable.find((e) => e.language === 'builtin' || runnerOk);
            evaluatorSelect.value = keep ? keep.key : (firstUsable ? firstUsable.key : '');
            syncEvaluator();
        }

        function syncEvaluator() {
            const e = currentEvaluator();
            evaluatorHelp.textContent = e ? [e.description, `Parameter: ${paramHelp(e.params_schema)}`].filter(Boolean).join(' ') : '';
            if (!paramsTouched) params.value = JSON.stringify(e ? paramDefaults(e.params_schema) : {}, null, 2);
        }

        metricSelect.addEventListener('change', fillEvaluators);
        evaluatorSelect.addEventListener('change', syncEvaluator);
        fillEvaluators();

        const body = el('div', null,
            el('div', { class: 'kx-form-row' },
                KX.field({ label: 'Metrik', input: metricSelect, name: 'metric' }),
                KX.field({ label: 'Auswerter', input: evaluatorSelect, name: 'evaluator' })),
            runnerHint,
            evaluatorHelp,
            el('div', { class: 'kx-field', dataset: { field: 'scope' } },
                el('span', { class: 'kx-label', text: 'Datenbereich' }), scopeGroup,
                el('span', { class: 'kx-field-error', 'aria-live': 'polite' })),
            period,
            KX.field({ label: 'Parameter (JSON)', input: params, name: 'params' }));

        await KX.dialog({
            title: 'Auswertung starten',
            wide: true,
            body,
            actions: [
                { label: 'Abbrechen', value: null },
                {
                    label: 'Starten',
                    primary: true,
                    onClick: async () => {
                        const e = currentEvaluator();
                        if (!e) throw new KX.ApiError('Bitte einen Auswerter wählen.', 400, { evaluator: 'Bitte einen Auswerter wählen.' });
                        let parsed;
                        try {
                            parsed = params.value.trim() ? JSON.parse(params.value) : {};
                        } catch (_) {
                            parsed = null;
                        }
                        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
                            throw new KX.ApiError('Die Parameter sind kein JSON-Objekt.', 400, { params: 'Bitte ein JSON-Objekt angeben, z. B. {"alpha": 0.05}.' });
                        }
                        const chosen = (scopeGroup.querySelector('input:checked') || {}).value || 'all';
                        const scope = {};
                        if (chosen === 'latest') scope.runs = 'latest';
                        if (chosen === 'period') {
                            if (!since.value && !until.value) {
                                throw new KX.ApiError('Bitte einen Zeitraum angeben.', 400, { since: 'Bitte ein Datum angeben.' });
                            }
                            if (since.value) scope.since = `${since.value}T00:00:00+00:00`;
                            // "Bis" schliesst den genannten Tag ein.
                            if (until.value) scope.until = `${until.value}T23:59:59+00:00`;
                        }
                        const data = await KX.api('POST', `${API}/evaluations`, {
                            evaluator: e.key, metric: metricSelect.value, params: parsed, scope,
                        });
                        const ev = data.evaluation || {};
                        KX.toast(ev.status === 'done' ? 'Auswertung fertig.' : 'Auswertung gestartet.', 'success');
                        await load();
                        const card = ev.id ? document.querySelector(`.kx-eval-card[data-evaluation-id="${CSS.escape(String(ev.id))}"]`) : null;
                        if (card) card.scrollIntoView({ block: 'nearest' });
                        return true;
                    },
                },
            ],
        });
    }

    async function runPipeline(button) {
        button.disabled = true;
        try {
            const data = await KX.api('POST', `${API}/pipeline`, {});
            const list = Array.isArray(data.evaluations) ? data.evaluations : [];
            const started = list.filter((e) => e && e.id).length;
            const warnings = list.map((e) => e && e.warning).filter(Boolean);
            KX.toast(started
                ? `${KX.fmtNumber(started, 0)} ${started === 1 ? 'Auswertung' : 'Auswertungen'} ausgeführt oder eingeplant.`
                : 'Der Typ sieht keine Auswertung vor.', started ? 'success' : 'info');
            warnings.slice(0, 3).forEach((w) => KX.toast(String(w), 'info'));
            await load();
        } catch (err) {
            reportError(err);
        } finally {
            button.disabled = false;
        }
    }

    // ── Notizen ─────────────────────────────────────────────────────────

    function renderNotes() {
        const box = $('kxNotes');
        if (state.editors.has('note') && box.firstChild) {
            // Den Entwurf stehen lassen, nur die Liste erneuern.
            const list = box.querySelector('.kx-notes-list');
            if (list) list.replaceWith(notesList());
            return;
        }
        clear(box).appendChild(noteForm());
        box.appendChild(notesList());
    }

    function noteForm() {
        const kinds = Object.keys(KX.LABELS.note_kinds).filter((k) => k !== 'status');
        const kind = KX.select({ name: 'kind' }, kinds.map((k) => ({ value: k, label: KX.label('note_kinds', k) })), 'note');
        const variant = KX.select({ name: 'variant' }, variantOptions(true), '');
        const text = KX.textarea({ name: 'body', rows: 3, maxlength: 50000, placeholder: 'Beobachtung, Interview-Notiz, Rückmeldung …' });
        const save = el('button', { type: 'submit', class: 'btn btn-primary btn-sm', text: 'Notiz speichern' });
        const form = el('form', { class: 'kx-card', noValidate: true },
            KX.field({ label: 'Neue Notiz', input: text, name: 'body', help: 'Markdown ist erlaubt. Notizen sind in der Knovas-Suche auffindbar.' }),
            el('div', { class: 'kx-form-row kx-form-row--narrow', style: { 'margin-top': '10px' } },
                KX.field({ label: 'Art der Notiz', input: kind, name: 'kind' }),
                (state.exp.variants || []).length ? KX.field({ label: 'Variante', input: variant, name: 'variant' }) : null),
            el('div', { class: 'kx-form-actions' }, save));
        text.addEventListener('input', () => {
            if (text.value.trim()) state.editors.add('note');
            else state.editors.delete('note');
        });
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            KX.clearFieldErrors(form);
            const body = text.value.trim();
            if (!body) {
                KX.showFieldErrors(form, { body: 'Bitte einen Text eingeben.' });
                text.focus();
                return;
            }
            save.disabled = true;
            try {
                const payload = { body, kind: kind.value };
                if (variant.value) payload.variant = variant.value;
                await KX.api('POST', `${API}/notes`, payload);
                state.editors.delete('note');
                KX.toast('Notiz gespeichert.', 'success');
                await load();
            } catch (err) {
                save.disabled = false;
                const unmatched = KX.showFieldErrors(form, err.fields);
                KX.toast([KX.errorMessage(err)].concat(unmatched).join(' '), 'error');
            }
        });
        return form;
    }

    function notesList() {
        const notes = state.exp.notes || [];
        if (!notes.length) return el('p', { class: 'kx-muted kx-notes-list', style: { 'margin-top': '12px' }, text: 'Noch keine Notizen.' });
        return el('ul', { class: 'kx-timeline kx-notes-list', style: { 'margin-top': '12px' } }, notes.map((n) => el('li', { class: 'kx-timeline-item' },
            el('div', { class: 'kx-timeline-head' },
                KX.chip(n.kind_label || KX.label('note_kinds', n.kind), n.kind === 'status' ? 'running' : 'muted'),
                el('span', { text: personName(n.created_by) || DASH }),
                el('time', { datetime: n.created_at || null, text: KX.fmtDateTime(n.created_at) }),
                n.variant ? el('span', { text: `Variante ${variantLabel(n.variant)}` }) : null,
                el('span', { class: 'kx-spacer' }),
                n.can_delete ? el('button', {
                    type: 'button', class: 'kx-link-button', text: 'Löschen',
                    'aria-label': `Notiz vom ${KX.fmtDateTime(n.created_at)} löschen`,
                    onClick: () => deleteNote(n),
                }) : null),
            KX.renderMarkdown(n.body))));
    }

    async function deleteNote(n) {
        const ok = await KX.confirm('Notiz löschen', 'Die Notiz wird gelöscht, auch aus der Knovas-Suche.', 'Löschen', true);
        if (!ok) return;
        try {
            await KX.api('DELETE', `${API}/notes/${encodeURIComponent(n.id)}`);
            KX.toast('Notiz gelöscht.', 'success');
            await load();
        } catch (err) {
            reportError(err, 'Diese Notiz gibt es nicht mehr.');
        }
    }

    // ── Entscheidung ────────────────────────────────────────────────────

    function decidesTransition() {
        return (state.exp.transitions || []).find((t) => t.decides) || null;
    }

    /** Typen ohne Zustand mit Phase "decided" halten Entscheidungen nur fest. */
    function recordsOnly() {
        return !(definition().states || []).some((s) => s.phase === 'decided');
    }

    function renderDecision() {
        const section = $('kx-entscheidung');
        const box = $('kxDecision');
        const decisions = state.exp.decisions || [];
        const canDecide = Boolean(decidesTransition()) || recordsOnly();
        const show = canDecide || decisions.length > 0;
        section.hidden = !show;
        $('kxSubnavDecision').hidden = !show;
        if (!show) {
            clear(box);
            return;
        }
        if (state.editors.has('decision') && box.firstChild) {
            const history = box.querySelector('.kx-decision-history');
            if (history) history.replaceWith(decisionHistory(decisions));
            return;
        }
        clear(box);
        const violated = (state.exp.metrics || []).filter((m) => m.guardrail_status === 'violated');
        if (violated.length) {
            box.appendChild(el('div', { class: 'kx-banner kx-banner--error', role: 'note' },
                el('div', null,
                    el('p', null, el('strong', { text: 'Leitplanke verletzt' })),
                    el('ul', null, violated.map((m) => el('li', {
                        text: `${m.name}: ${guardrailText(m)}; gemessen ${(m.aggregates || [])
                            .filter((a) => a.variant != null)
                            .map((a) => `${a.variant} ${KX.fmtEstimate(m.kind, a.estimate, m.unit, decimalsOf(m))}`)
                            .join(', ') || DASH}`,
                    }))))));
        }
        if (canDecide) box.appendChild(decisionForm());
        box.appendChild(decisionHistory(decisions));
    }

    function decisionForm() {
        const requireLearning = Boolean((definition().decision || {}).require_learning);
        const verdict = KX.select({ name: 'verdict', id: 'kxDecisionVerdict' },
            [{ value: '', label: 'Bitte wählen' }].concat(Object.keys(KX.LABELS.decision_verdicts).map((k) => ({
                value: k, label: KX.label('decision_verdicts', k),
            }))), '');
        const rationale = KX.textarea({ name: 'rationale', rows: 4, maxlength: 20000 });
        const learning = KX.textarea({ name: 'learning', rows: 4, maxlength: 20000 });
        const save = el('button', { type: 'submit', class: 'btn btn-primary btn-sm', text: 'Entscheidung festhalten' });
        const error = el('div', { class: 'kx-dialog-error', role: 'alert', hidden: true, style: { margin: '12px 0 0' } });
        const t = decidesTransition();
        const form = el('form', { class: 'kx-card kx-decision-box', noValidate: true },
            // Der Name des Zielstatus ("Entschieden"), nicht die Beschriftung
            // des Uebergangs ("Entscheiden").
            el('p', { class: 'kx-help', text: t
                ? `Mit der Entscheidung wechselt der Status zu «${stateLabel(t.to)}».`
                : 'Die Entscheidung wird festgehalten; der Status bleibt.' }),
            el('div', { class: 'kx-form-row kx-form-row--narrow', style: { 'margin-top': '10px' } },
                KX.field({ label: 'Entscheidung', input: verdict, name: 'verdict', required: true })),
            KX.field({ label: 'Begründung', input: rationale, name: 'rationale', help: 'Warum so entschieden? Welche Auswertung trägt die Entscheidung?' }),
            KX.field({
                label: 'Erkenntnis', input: learning, name: 'learning', required: requireLearning,
                help: 'Was nehmen wir mit, unabhängig vom Ergebnis? Die Erkenntnis ist in der Suche auffindbar.',
            }),
            error,
            el('div', { class: 'kx-form-actions' }, save));
        form.addEventListener('input', () => state.editors.add('decision'));
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            KX.clearFieldErrors(form);
            error.hidden = true;
            const missing = {};
            if (!verdict.value) missing.verdict = 'Bitte eine Entscheidung wählen.';
            if (requireLearning && !learning.value.trim()) missing.learning = 'Die Erkenntnis fehlt.';
            if (Object.keys(missing).length) {
                KX.showFieldErrors(form, missing);
                const invalid = form.querySelector('[aria-invalid="true"]');
                if (invalid) invalid.focus();
                return;
            }
            save.disabled = true;
            try {
                const sent = state.exp.row_version;
                const data = await KX.api('POST', `${API}/decisions`, {
                    verdict: verdict.value,
                    rationale: rationale.value.trim(),
                    learning: learning.value.trim(),
                    row_version: sent,
                });
                state.editors.delete('decision');
                KX.toast('Entscheidung festgehalten.', 'success');
                await applySnapshot(data.experiment, sent);
            } catch (err) {
                save.disabled = false;
                if (err.status === 409) {
                    // Der Entwurf bleibt beim Neu laden stehen; erneut
                    // festhalten sendet dann die neue row_version.
                    state.editors.add('decision');
                    showConflict(err.message);
                    return;
                }
                showEditorError(error, err, KX.showFieldErrors(form, err.fields));
            }
        });
        return form;
    }

    function decisionHistory(decisions) {
        if (!decisions.length) return el('div', { class: 'kx-decision-history' });
        return el('div', { class: 'kx-decision-history' },
            el('h3', { class: 'kx-subhead', text: 'Bisherige Entscheidungen' }),
            el('ul', { class: 'kx-timeline' }, decisions.map((d) => el('li', { class: 'kx-timeline-item' },
                el('div', { class: 'kx-timeline-head' },
                    KX.decisionChip(d.verdict),
                    el('span', { text: personName(d.decided_by) || DASH }),
                    el('time', { datetime: d.decided_at || null, text: KX.fmtDateTime(d.decided_at) })),
                d.rationale ? [el('p', { class: 'kx-subhead', text: 'Begründung' }), KX.renderMarkdown(d.rationale)] : null,
                d.learning ? [el('p', { class: 'kx-subhead', text: 'Erkenntnis' }), KX.renderMarkdown(d.learning)] : null))));
    }

    function openDecisionForm() {
        const section = $('kx-entscheidung');
        if (section.hidden) return;
        section.scrollIntoView({ behavior: 'smooth', block: 'start' });
        const select = document.getElementById('kxDecisionVerdict');
        if (select) window.setTimeout(() => select.focus({ preventScroll: true }), 300);
    }

    // ── Aktivitaet ──────────────────────────────────────────────────────

    let activitySeq = 0;

    /** Namen der geaenderten Angaben (detail.changed von experiment.update). */
    const CHANGED_LABELS = {
        title: 'Titel', hypothesis: 'Hypothese', description: 'Beschreibung',
        fields: 'Angaben des Typs', tags: 'Schlagwörter', owner_id: 'Verantwortlich', archived: 'Archivierung',
    };

    function listText(values, max) {
        const shown = values.slice(0, max || 5).map(String);
        return values.length > shown.length ? `${shown.join(', ')} …` : shown.join(', ');
    }

    function metricNames(keys) {
        return keys.map((k) => {
            const m = metricByKey(k);
            return m && m.name ? m.name : String(k);
        });
    }

    /**
     * Ein Eintrag der Aktivitaet in Worten. Die API liefert zu jeder Aktion
     * eine feste Beschriftung und die Einzelheiten (detail): was geaendert
     * wurde, von welchem zu welchem Status, wie viele Messwerte. Ohne sie
     * hiesse jede Bearbeitung "Angaben geaendert" -- wie der Kasten der
     * Typ-Angaben --, und ein Statuswechsel sagte nicht, wohin.
     */
    function describeActivity(a) {
        const item = a || {};
        const d = item.detail && typeof item.detail === 'object' ? item.detail : {};
        const base = String(item.label || item.action || '');
        const count = (n) => KX.fmtNumber(Number(n) || 0, 0);
        switch (item.action) {
        case 'experiments.experiment.update': {
            const changed = Array.isArray(d.changed) ? d.changed.map(String) : [];
            if (changed.length === 1 && changed[0] === 'archived') {
                if (d.archived === true) return 'Archiviert';
                if (d.archived === false) return 'Wiederhergestellt';
                return 'Archiviert oder wiederhergestellt';
            }
            if (!changed.length) return base;
            return `Geändert: ${changed.map((k) => CHANGED_LABELS[k] || k).join(', ')}`;
        }
        case 'experiments.experiment.transition':
            if (d.from == null && d.to == null) return base;
            return `Status «${stateLabel(d.from)}» → «${stateLabel(d.to)}»`;
        case 'experiments.experiment.decide': {
            const verdict = d.verdict ? KX.label('decision_verdicts', d.verdict) : '';
            const moved = d.to != null && d.to !== d.from
                ? `; Status «${stateLabel(d.from)}» → «${stateLabel(d.to)}»` : '';
            return `${base}${verdict ? `: ${verdict}` : ''}${moved}`;
        }
        case 'experiments.experiment.variants': {
            const added = Array.isArray(d.added) ? d.added : [];
            const removed = Array.isArray(d.removed) ? d.removed : [];
            if (!added.length && !removed.length) return 'Varianten gespeichert (ohne neue oder entfernte)';
            const parts = [];
            if (added.length) parts.push(`neu: ${listText(added)}`);
            if (removed.length) parts.push(`entfernt: ${listText(removed)}`);
            return `${base} (${parts.join('; ')})`;
        }
        case 'experiments.experiment.metrics': {
            const metrics = Array.isArray(d.metrics) ? d.metrics : [];
            const removed = Array.isArray(d.removed) ? d.removed : [];
            const parts = [`${count(metrics.length)} zugeordnet`];
            if (removed.length) parts.push(`entfernt: ${listText(metricNames(removed))}`);
            return `${base} (${parts.join('; ')})`;
        }
        case 'experiments.measurements.add':
        case 'experiments.measurements.import': {
            if (d.rows == null) return base;
            const metrics = Array.isArray(d.metrics) ? d.metrics : [];
            const rows = Number(d.rows) || 0;
            return `${base}: ${count(rows)} ${rows === 1 ? 'Messwert' : 'Messwerte'}`
                + `${metrics.length ? ` (${listText(metricNames(metrics))})` : ''}`;
        }
        case 'experiments.batch.delete':
            return d.rows == null ? base : `${base}: ${count(d.rows)}`;
        case 'experiments.run.add': {
            const parts = [];
            if (d.status) parts.push(KX.label('run_statuses', d.status));
            if (Number(d.rows)) parts.push(`${count(d.rows)} ${Number(d.rows) === 1 ? 'Messwert' : 'Messwerte'}`);
            return parts.length ? `${base} (${parts.join(', ')})` : base;
        }
        case 'experiments.evaluation.run': {
            if (!d.evaluator) return base;
            const evaluator = ((state.exp && state.exp.evaluators) || []).find((e) => e && e.key === d.evaluator);
            const name = (evaluator && evaluator.name) || String(d.evaluator);
            return `${base}: ${name}${d.metric ? ` auf ${listText(metricNames([d.metric]))}` : ''}`;
        }
        case 'experiments.pipeline.run':
            return d.created == null ? base : `${base} (${count(d.created)} neu)`;
        case 'experiments.note.add':
        case 'experiments.note.delete':
            return d.kind ? `${base} (${KX.label('note_kinds', d.kind)})` : base;
        case 'experiments.experiment.reindex':
            return d.queued === false ? `${base} (Übertragung ausgeschaltet)` : base;
        default:
            return base;
        }
    }

    async function loadActivity() {
        const box = $('kxActivity');
        const seq = ++activitySeq;
        try {
            const data = await KX.api('GET', `${API}/activity`);
            if (seq !== activitySeq) return;
            const items = Array.isArray(data.activity) ? data.activity : [];
            clear(box);
            if (!items.length) {
                box.appendChild(el('p', { class: 'kx-muted', text: 'Noch keine Einträge.' }));
                return;
            }
            box.appendChild(el('ul', { class: 'kx-activity' }, items.map((a) => el('li', null,
                el('time', { datetime: a.at || null, text: KX.fmtDateTime(a.at) }),
                el('span', null, el('span', { text: describeActivity(a) }),
                    a.actor ? el('span', { class: 'kx-muted', text: ` · ${a.actor}` }) : null)))));
        } catch (err) {
            if (seq !== activitySeq) return;
            clear(box).appendChild(el('p', { class: 'kx-muted', text: `Aktivität nicht geladen: ${KX.errorMessage(err)}` }));
        }
    }

    // ── Start ───────────────────────────────────────────────────────────

    function init() {
        KX.meta().then((m) => {
            state.meta = m;
            // Beschriftungen aus meta: Entscheidungs- und Notizarten neu
            // zeichnen; der Kopf kennt jetzt index_enabled.
            if (state.exp) {
                renderHeader();
                renderNotes();
                renderDecision();
            }
        }).catch(() => null);
        load();
        window.addEventListener('pageshow', (e) => {
            if (e.persisted) softReload();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
