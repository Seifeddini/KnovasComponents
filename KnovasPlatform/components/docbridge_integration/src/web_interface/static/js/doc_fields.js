// Knovas Dokumentfelder in der Suche: Filterleiste, Liste nach Feldern und
// das Feldpanel in der Vorschau.
//
// Was hier gilt:
// - Nur was Knovas fuer diesen Mandanten kann, wird gezeigt (H6): das Panel
//   ab "values", die Liste ab "listing_only", Filter in der Suche nur bei
//   "filters". Den Stand liefert GET /api/doc-fields; der Server entscheidet.
// - Ein Filter ist ehrlich oder gar nicht da: ob er angewendet wurde, sagt
//   die Antwort des Servers ("Verstanden als", filter_state); hier wird
//   nichts davon berechnet. Den Hinweis "Liste unvollständig" berechnet der
//   Server, nie dieses Skript.
// - Serverdaten gehen nur per textContent in die Seite -- diese Datei setzt
//   kein innerHTML. Namen und Werte gehen nur im JSON-Koerper an den
//   Server, nie in eine URL.
// - Jedes Feld der Leiste hat eine Bedingung (OPERATORS). Den where-Wert
//   baut nur buildOperand -- fuer die Suche und "Liste anzeigen" derselbe.

class DocFieldsUI {
    /** Die Faehigkeiten, unter denen ein Teil der Oberflaeche erscheint (D11). */
    static VALUE_CAPS = ['values', 'listing_only', 'filters'];
    static LISTING_CAPS = ['listing_only', 'filters'];
    static FILTER_CAPS = ['filters'];

    /** Leere Seiten mit Fortsetzung, die ohne Klick uebersprungen werden. */
    static MAX_EMPTY_HOPS = 5;

    /** Verzoegerung der Namensvorschlaege (ms). */
    static SUGGEST_DELAY_MS = 300;

    /** Uebergabe aus dem Cortex: {field, name}, nie in der URL. */
    static HANDOFF_KEY = 'knovas.docFieldsHandoff';

    /**
     * Bedingungen je Feldtyp (Spec F2), die erste ist die Vorgabe. Was sie
     * an Knovas schicken, entscheidet buildOperand.
     */
    static OPERATORS = {
        enum: [['in', 'ist'], ['prefix', 'beginnt mit'], ['exists', 'hat einen Wert']],
        code: [['eq', 'ist'], ['prefix', 'beginnt mit'], ['in', 'eine von'],
            ['exists', 'hat einen Wert']],
        text: [['eq', 'ist'], ['prefix', 'beginnt mit'], ['exists', 'hat einen Wert']],
        money: [['eq', 'ist'], ['range', 'ab / bis'], ['exists', 'hat einen Wert']],
        number: [['eq', 'ist'], ['range', 'ab / bis'], ['exists', 'hat einen Wert']],
        date: [['overlaps', 'Zeitraum'], ['range', 'von / bis'], ['within', 'liegt ganz in'],
            ['exists', 'hat einen Wert']],
        period: [['overlaps', 'Zeitraum'], ['range', 'von / bis'], ['within', 'liegt ganz in'],
            ['exists', 'hat einen Wert']],
        bool: [['', '– alle –'], ['true', 'Ja'], ['false', 'Nein'], ['exists', 'hat einen Wert']],
        entity_ref: [['in', 'ist'], ['exists', 'hat einen Wert']],
    };

    /** Der Datentyp der Leiste: ein unbekannter gilt als Text. */
    static railType(datatype) {
        return Object.prototype.hasOwnProperty.call(DocFieldsUI.OPERATORS, datatype)
            ? datatype : 'text';
    }

    /** "a, b,, a" -> ["a", "b"]: getrimmt, ohne Leere und Wiederholungen. */
    static splitList(text, separator) {
        const out = [];
        String(text == null ? '' : text).split(separator).forEach((part) => {
            const item = part.trim();
            if (item && !out.includes(item)) out.push(item);
        });
        return out;
    }

    /**
     * Der where-Wert eines Feldes aus dem, was die Leiste haelt -- rein, ohne
     * DOM (tests/test_frontend_static.py prueft ihn unter Node). state:
     * {op, text, choices, lo, hi, partly}; undefined heisst kein Filter.
     */
    static buildOperand(datatype, state) {
        const type = DocFieldsUI.railType(datatype);
        const s = state || {};
        const op = String(s.op == null ? '' : s.op);
        const text = String(s.text == null ? '' : s.text).trim();
        const one = (items) => (items.length === 1 ? items[0] : items);
        if (op === 'exists') return { exists: true };
        if (type === 'bool') {
            if (op === 'true') return true;
            if (op === 'false') return false;
            return undefined;
        }
        if (op === 'prefix') return text ? { prefix: text } : undefined;
        if (type === 'enum') {
            const codes = (Array.isArray(s.choices) ? s.choices : [])
                .map((code) => String(code)).filter(Boolean);
            return codes.length ? one(codes) : undefined;
        }
        if (type === 'entity_ref') {
            const names = DocFieldsUI.splitList(text, ';').map((name) => ({ name }));
            return names.length ? one(names) : undefined;
        }
        if (op === 'in') {
            const codes = DocFieldsUI.splitList(text, ',');
            return codes.length ? one(codes) : undefined;
        }
        if (op === 'range') {
            const lo = String(s.lo == null ? '' : s.lo).trim();
            const hi = String(s.hi == null ? '' : s.hi).trim();
            if (!lo && !hi) return undefined;
            if (type === 'date' || type === 'period') {
                const range = {};
                if (lo) range.gte = lo;
                if (hi) range.lte = hi;
                if (s.partly) range.match = 'possible';
                return range;
            }
            if (lo && hi) return { between: [lo, hi] };
            return lo ? { gte: lo } : { lte: hi };
        }
        if (op === 'within') return text ? { within: text } : undefined;
        return text || undefined;   // ist; Zeitraum (der einfache Wert ueberschneidet)
    }

    /** Die Belegung der Leiste zu einem where-Wert (Wiederherstellen, Cortex-Uebergabe). */
    static parseOperand(datatype, operand) {
        const type = DocFieldsUI.railType(datatype);
        const state = {
            op: DocFieldsUI.OPERATORS[type][0][0], text: '', choices: [], lo: '', hi: '',
            partly: false,
        };
        if (operand === undefined || operand === null) return state;
        const object = typeof operand === 'object' && !Array.isArray(operand);
        if (object && operand.exists === true) return Object.assign(state, { op: 'exists' });
        if (type === 'bool') {
            if (operand === true || operand === false) {
                return Object.assign(state, { op: String(operand) });
            }
            return state;
        }
        if (object && !('name' in operand) && !('node_id' in operand)) {
            if (typeof operand.prefix === 'string') {
                return Object.assign(state, { op: 'prefix', text: operand.prefix });
            }
            if (typeof operand.within === 'string') {
                return Object.assign(state, { op: 'within', text: operand.within });
            }
            if (typeof operand.overlaps === 'string') {
                return Object.assign(state, { op: 'overlaps', text: operand.overlaps });
            }
            if (Array.isArray(operand.between) && operand.between.length === 2) {
                return Object.assign(state, {
                    op: 'range', lo: String(operand.between[0]), hi: String(operand.between[1]),
                });
            }
            if (typeof operand.gte === 'string' || typeof operand.lte === 'string') {
                return Object.assign(state, {
                    op: 'range',
                    lo: typeof operand.gte === 'string' ? operand.gte : '',
                    hi: typeof operand.lte === 'string' ? operand.lte : '',
                    partly: operand.match === 'possible',
                });
            }
            return state;
        }
        const items = Array.isArray(operand) ? operand : [operand];
        if (type === 'entity_ref') {
            const names = items.map((v) => (v && typeof v === 'object' ? v.name : v))
                .filter((v) => typeof v === 'string' && v);
            return Object.assign(state, { op: 'in', text: names.join('; ') });
        }
        const texts = items.filter((v) => typeof v === 'string' || typeof v === 'number')
            .map((v) => String(v));
        if (type === 'enum') return Object.assign(state, { op: 'in', choices: texts });
        if (type === 'code' && texts.length > 1) {
            return Object.assign(state, { op: 'in', text: texts.join(', ') });
        }
        return Object.assign(state, { text: texts[0] || '' });
    }

    /** Platzhalter der Werteingabe je Typ und Bedingung. */
    static placeholder(type, op) {
        if (op === 'prefix') return type === 'code' ? 'Anfang, z. B. E11' : 'Anfang';
        if (op === 'in' && type === 'code') return 'mehrere mit Komma trennen';
        if (type === 'entity_ref') return 'Name, mehrere mit ; trennen';
        if (op === 'within') return type === 'period' ? 'z. B. GJ 2024' : 'z. B. 2024';
        if (type === 'period') return 'z. B. GJ 2024';
        if (type === 'date') return 'z. B. März 2024';
        if (type === 'money') return "z. B. CHF 1'000";
        if (type === 'number') return "z. B. 1'234.5";
        return '';
    }

    constructor(app) {
        this.app = app;
        this.capability = 'off';
        this.fields = [];
        this.editableKeys = [];
        this.partialHint = '';
        this.rail = document.getElementById('docFieldsRail');
        this.railTitle = document.getElementById('docFieldsRailTitle');
        this.railHint = document.getElementById('docFieldsRailHint');
        this.railInputs = document.getElementById('docFieldsRailInputs');
        this.railSort = document.getElementById('docFieldsRailSort');
        this.listButton = document.getElementById('docFieldsListButton');
        this.clearButton = document.getElementById('docFieldsClearButton');
        this.understood = document.getElementById('docFieldsUnderstood');
        this.listNotice = document.getElementById('docFieldsNotice');
        this.banner = document.getElementById('docFieldsBanner');
        this.panelSection = document.getElementById('previewFieldsSection');
        this.panel = document.getElementById('previewFields');
        /** @type {{key: string, read: function(): *, write: function(*): void}[]} */
        this._controls = [];
        this._sortControl = null;
        this._listing = null;   // {where, sort, next, total}
        this._panelSeq = 0;
        this._panelDocId = '';
        this._panelView = null;

        if (this.listButton) this.listButton.addEventListener('click', () => this.showListing());
        if (this.clearButton) {
            this.clearButton.addEventListener('click', () => this.clearAndRefresh());
        }
        this.ready = this.load();
    }

    get showsValues() { return DocFieldsUI.VALUE_CAPS.includes(this.capability); }

    get showsListing() { return DocFieldsUI.LISTING_CAPS.includes(this.capability); }

    get showsFilters() { return DocFieldsUI.FILTER_CAPS.includes(this.capability); }

    /** Registry und Faehigkeit vom Server; danach die Leiste neu aufbauen. */
    async load() {
        const kept = this._readControls();
        try {
            const response = await fetch('/api/doc-fields', { credentials: 'same-origin' });
            const data = response.ok ? await response.json().catch(() => ({})) : {};
            this.capability = String(data.capability || 'off');
            this.fields = Array.isArray(data.fields) ? data.fields : [];
            this.editableKeys = Array.isArray(data.editable_keys) ? data.editable_keys : [];
            this.partialHint = String(data.partial_hint || '');
        } catch (error) {
            this.capability = 'off';
            this.fields = [];
            this.editableKeys = [];
        }
        this.renderRail(kept);
        this._applyHandoff();
    }

    _field(key) {
        return this.fields.find((f) => f && f.key === key) || null;
    }

    // -- Filterleiste ----------------------------------------------------

    /**
     * Die Leiste aus den Facettenfeldern. Bei "filters" gilt sie fuer Suche
     * und Liste; bei "listing_only" nur fuer die Liste -- die Suche bekommt
     * dann nie ein where, und die Leiste sagt das in ihrem Titel.
     */
    renderRail(kept) {
        this._controls = [];
        this._sortControl = null;
        if (!this.rail) return;
        if (!this.showsListing) {
            this.rail.hidden = true;
            if (this.railInputs) this.railInputs.replaceChildren();
            return;
        }
        this.railTitle.textContent = this.showsFilters ? 'Filter' : 'Dokumentliste nach Feldern';
        this.railHint.textContent = this.showsFilters
            ? 'Gilt für die Suche. Ohne Suchbegriff zeigt „Liste anzeigen“ alle passenden Dokumente.'
            : 'Zeigt alle passenden Dokumente als Liste.';
        const facets = this.fields.filter((f) => f && f.facet && f.status !== 'deprecated');
        const nodes = facets.map((field) => this._controlFor(field));
        this.railInputs.replaceChildren(...nodes.filter(Boolean));
        this._renderSort();
        this._writeControls(kept || {});
        this.rail.hidden = facets.length === 0;
    }

    _option(value, text) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = text;
        return option;
    }

    /**
     * Ein Filter je Facettenfeld: die Bedingung (OPERATORS) und die Eingaben,
     * die sie braucht. read() liefert den where-Wert aus buildOperand oder
     * undefined, write() stellt einen ueber parseOperand wieder her.
     */
    _controlFor(field) {
        const type = DocFieldsUI.railType(field.datatype);
        const label = String(field.label || field.key);
        const wrap = document.createElement('div');
        wrap.className = 'doc-fields-control';
        const caption = document.createElement('label');
        const mode = document.createElement('select');
        mode.id = `dfFilter_${field.key}`;
        mode.className = 'doc-fields-op';
        caption.htmlFor = mode.id;
        caption.textContent = label;
        DocFieldsUI.OPERATORS[type].forEach(([value, text]) => {
            mode.appendChild(this._option(value, text));
        });
        wrap.append(caption, mode);
        if (type === 'bool') {
            // Ja / Nein / hat einen Wert: die Bedingung ist der Wert.
            this._controls.push({
                key: field.key,
                read: () => DocFieldsUI.buildOperand(type, { op: mode.value }),
                write: (v) => { mode.value = DocFieldsUI.parseOperand(type, v).op; },
            });
            return wrap;
        }
        const choices = document.createElement('select');
        choices.multiple = true;
        choices.title = 'Mehrere mit Strg- oder Cmd-Klick wählen';
        choices.setAttribute('aria-label', `${label}: Auswahl`);
        (field.enum || []).forEach((item) => {
            if (item && item.code) choices.appendChild(this._option(item.code, item.label || item.code));
        });
        choices.size = Math.min(5, Math.max(2, choices.options.length));
        const input = document.createElement('input');
        input.type = 'text';
        input.autocomplete = 'off';
        input.setAttribute('aria-label', `${label}: Wert`);
        const dated = type === 'date' || type === 'period';
        const range = document.createElement('div');
        range.className = 'doc-fields-range';
        const from = document.createElement('input');
        const to = document.createElement('input');
        [from, to].forEach((el) => { el.type = 'text'; el.autocomplete = 'off'; });
        from.placeholder = dated ? 'von' : 'ab';
        to.placeholder = 'bis';
        from.setAttribute('aria-label', `${label} ${from.placeholder}`);
        to.setAttribute('aria-label', `${label} bis`);
        range.append(from, to);
        const partlyLabel = document.createElement('label');
        partlyLabel.className = 'doc-fields-partly';
        const partly = document.createElement('input');
        partly.type = 'checkbox';
        partlyLabel.append(partly, ' auch teilweise');
        wrap.append(choices, input, range, partlyLabel);
        if (type === 'entity_ref' && field.has_target && field.sensitivity === 'normal') {
            // Vorschlaege nur fuer Felder mit Zieltyp und nie fuer besonders
            // schuetzenswerte: dort bleibt es bei freiem Text.
            wrap.appendChild(this._suggestions(field, input));
        }
        const sync = () => {
            const op = mode.value;
            const listed = type === 'enum' && op === 'in';
            choices.hidden = !listed;
            input.hidden = listed || op === 'range' || op === 'exists';
            range.hidden = op !== 'range';
            partlyLabel.hidden = !(dated && op === 'range');
            input.placeholder = DocFieldsUI.placeholder(type, op);
        };
        mode.addEventListener('change', sync);
        const state = () => ({
            op: mode.value,
            text: input.value,
            choices: Array.from(choices.options).filter((o) => o.selected).map((o) => o.value),
            lo: from.value,
            hi: to.value,
            partly: partly.checked,
        });
        this._controls.push({
            key: field.key,
            read: () => DocFieldsUI.buildOperand(type, state()),
            write: (v) => {
                const s = DocFieldsUI.parseOperand(type, v);
                mode.value = s.op;
                input.value = s.text;
                Array.from(choices.options).forEach((o) => { o.selected = s.choices.includes(o.value); });
                from.value = s.lo;
                to.value = s.hi;
                partly.checked = !!s.partly;
                sync();
            },
        });
        sync();
        return wrap;
    }

    /**
     * Namensvorschlaege fuer ein Entitaetsfeld. Bei mehreren Namen (";") gilt
     * der Vorschlag dem letzten; die Option traegt die ganze Zeile, damit
     * die Auswahl die schon getippten Namen behaelt. Der getippte Text geht
     * nur im JSON-Koerper an die Plattform (suggest), nie an Knovas.
     */
    _suggestions(field, input) {
        const list = document.createElement('datalist');
        list.id = `dfSuggest_${field.key}`;
        input.setAttribute('list', list.id);
        let timer = null;
        let seq = 0;
        input.addEventListener('input', () => {
            window.clearTimeout(timer);
            const typed = input.value;
            timer = window.setTimeout(async () => {
                const mine = ++seq;
                const before = typed.split(';');
                const last = before.pop();
                const names = await this.suggest(field.key, last);
                if (mine !== seq) return;
                const head = before.map((part) => part.trim()).filter(Boolean);
                list.replaceChildren(...names.map((name) => this._option(
                    [...head, name].join('; '), name)));
            }, DocFieldsUI.SUGGEST_DELAY_MS);
        });
        return list;
    }

    /** Sortierung der Liste: Dokumentpfad oder ein Datums-/Zeitraumfeld. */
    _renderSort() {
        if (!this.railSort) return;
        const dated = this.fields.filter((f) => f && f.status !== 'deprecated'
            && (f.datatype === 'date' || f.datatype === 'period'));
        const select = document.createElement('select');
        select.id = 'dfSort';
        select.appendChild(this._option('pointer:asc', 'Dokumentpfad aufsteigend'));
        select.appendChild(this._option('pointer:desc', 'Dokumentpfad absteigend'));
        dated.forEach((f) => {
            const label = String(f.label || f.key);
            select.appendChild(this._option(`${f.key}:desc`, `${label}, neueste zuerst`));
            select.appendChild(this._option(`${f.key}:asc`, `${label}, älteste zuerst`));
        });
        const label = document.createElement('label');
        label.htmlFor = select.id;
        label.textContent = 'Liste sortieren nach';
        this.railSort.replaceChildren(label, select);
        this._sortControl = select;
    }

    _readControls() {
        const out = {};
        this._controls.forEach((c) => {
            const v = c.read();
            if (v !== undefined) out[c.key] = v;
        });
        return out;
    }

    _writeControls(values) {
        this._controls.forEach((c) => c.write(values[c.key]));
    }

    collectWhere() {
        const where = this._readControls();
        return Object.keys(where).length ? where : null;
    }

    collectSort() {
        const raw = this._sortControl ? this._sortControl.value : 'pointer:asc';
        const [field, order] = String(raw || 'pointer:asc').split(':');
        return { field: field || 'pointer', order: order === 'desc' ? 'desc' : 'asc' };
    }

    /** Der Filter fuer eine Suche -- nur bei "filters" (H1), sonst null. */
    searchWhere() {
        return this.showsFilters ? this.collectWhere() : null;
    }

    hasSearchFilters() { return !!this.searchWhere(); }

    hasListingFilters() { return this.showsListing && !!this.collectWhere(); }

    clearFilters() {
        this._writeControls({});
    }

    /**
     * "Filter entfernen" der Leiste: gefilterte Treffer duerfen nicht als
     * ungefilterte stehen bleiben. Eine gefilterte Suche laeuft ohne Filter
     * neu; eine Liste (immer gefiltert) verschwindet.
     */
    clearAndRefresh() {
        const filtered = !!(this.understood && !this.understood.hidden);
        this.clearFilters();
        this._renderChips([]);
        this.clearListingState();
        if (this.app._mode === 'listing') {
            this.app.clearResults();
        } else if (filtered && this.app.currentQuery) {
            this.app.performSearch(this.app.currentQuery);
        }
    }

    /** Hinweis, Fristbanner und Listenstand weg: sie beschreiben nichts mehr. */
    clearListingState() {
        this._listing = null;
        this._setText(this.listNotice, '');
        this._setText(this.banner, '');
    }

    /** Namen zu einem Entitaetsfeld; der Text bleibt auf der Plattform. */
    async suggest(fieldKey, typed) {
        const text = String(typed || '').trim();
        if (text.length < 2) return [];
        try {
            const response = await fetch('/api/doc-fields/entities', {
                method: 'POST',
                credentials: 'same-origin',
                headers: this.app._jsonHeadersWithCsrf(),
                body: JSON.stringify({ field: fieldKey, q: text }),
            });
            if (!response.ok) return [];
            const data = await response.json().catch(() => ({}));
            return (data.items || []).map((i) => String(i && i.name || '')).filter(Boolean);
        } catch (error) {
            return [];
        }
    }

    // -- "Verstanden als" und Hinweise -----------------------------------

    _chip(text, extra) {
        const chip = document.createElement('span');
        chip.className = 'field-chip';
        const t = document.createElement('span');
        t.className = 'field-chip-text';
        t.textContent = text;
        chip.appendChild(t);
        if (extra) {
            const e = document.createElement('span');
            e.className = 'field-chip-label';
            e.textContent = extra;
            chip.appendChild(e);
        }
        return chip;
    }

    /** Die Chips aus dem, was der Server zurueckgibt (resolved_chips). */
    _renderChips(resolved, partial) {
        if (!this.understood) return;
        const items = Array.isArray(resolved) ? resolved : [];
        if (!items.length) {
            this.understood.hidden = true;
            this.understood.replaceChildren();
            return;
        }
        const head = document.createElement('span');
        head.className = 'doc-fields-understood-label';
        head.textContent = 'Verstanden als';
        const chips = items.map((c) => this._chip(
            `${c.label || c.field}: ${c.text || ''}`, c.linked_text || ''));
        this.understood.replaceChildren(head, ...chips);
        if (partial && this.partialHint) {
            const hint = document.createElement('p');
            hint.className = 'doc-fields-partial';
            hint.textContent = this.partialHint;
            this.understood.appendChild(hint);
        }
        this.understood.hidden = false;
    }

    _setText(el, text) {
        if (!el) return;
        el.textContent = text || '';
        el.hidden = !text;
    }

    /**
     * Nach einer Suche: Chips nur, wenn der Server "applied" gesagt hat.
     * Bei "listing_only" gehen Werte der Leiste nie mit der Suche: stehen
     * welche drin, sagt der Hinweis des Servers, dass die Suche ohne sie lief.
     */
    renderSearchState(documentFields) {
        const df = documentFields || {};
        const state = df.filter_state;
        this.clearListingState();
        if (state === 'applied' || state === 'partial') {
            this._renderChips(df.resolved, state === 'partial');
        } else {
            this._renderChips([]);
            if (!this.showsFilters && this.showsListing && this.collectWhere()) {
                this._setText(this.listNotice, String(df.rail_not_applied || ''));
            }
        }
        if (df.capability && df.capability !== this.capability) {
            // Knovas hat waehrend der Suche etwas anderes gesagt: neu laden.
            this.load();
        }
    }

    /**
     * Ein abgelehnter Filter: Erklaerung statt Treffer. "Ohne Filter suchen"
     * ist eine ausdrueckliche Wahl -- nie ein stiller zweiter Versuch.
     * Gibt true zurueck, wenn die Antwort hier behandelt wurde.
     */
    handleSearchRefusal(data, query, { listing = false } = {}) {
        const code = data && data.error_code;
        const handled = ['filter_not_applied', 'filters_need_calibration',
            'filters_unavailable', 'filter_invalid', 'filter_temporarily_unavailable',
            'filter_not_authorized'];
        // Die Liste ersetzt ihre Seite immer durch die Erklaerung; die Suche
        // nur bei Filterfehlern (alles andere bleibt ihr Fehler-Toast).
        if (!listing && !handled.includes(code)) return false;
        const box = document.createElement('div');
        box.className = 'empty-state doc-fields-refusal';
        const text = document.createElement('p');
        text.textContent = String(data.error || 'Der Filter konnte nicht angewendet werden.');
        box.appendChild(text);
        if (code !== 'filter_invalid' && query) {
            const plain = document.createElement('button');
            plain.type = 'button';
            plain.className = 'btn btn-outline';
            plain.textContent = 'Ohne Filter suchen';
            plain.addEventListener('click', () => {
                this.clearFilters();
                this._renderChips([]);
                this.app.performSearch(query);
            });
            box.appendChild(plain);
        }
        this._renderChips([]);
        this.app.displayRefusal(box);   // leert auch Hinweis und Fristbanner
        if (code === 'filters_need_calibration' || code === 'filters_unavailable'
                || code === 'filter_not_applied') {
            // Die Faehigkeit hat sich geaendert (oder ist unklar): neu fragen.
            this.load();
        }
        return true;
    }

    // -- Liste nach Feldern ----------------------------------------------

    async showListing() {
        if (!this.showsListing) return;
        const where = this.collectWhere();
        if (!where) {
            this.app.showError('Bitte mindestens ein Feld wählen.');
            return;
        }
        this._listing = { where, sort: this.collectSort(), next: null, total: null };
        await this._fetchPages(false);
    }

    async nextPage() {
        if (!this._listing || !this._listing.next) return;
        await this._fetchPages(true);
    }

    /**
     * Seiten holen, bis eine Dokumente bringt oder die Liste zu Ende ist.
     * Eine leere Seite mit Fortsetzung heisst nur "hier nichts", nicht
     * "nirgends" -- also weiter, hoechstens MAX_EMPTY_HOPS Seiten ohne Klick.
     */
    async _fetchPages(append) {
        const state = this._listing;
        if (!append) this.app.showLoading();
        else this.app.loadMoreButton.disabled = true;
        let after = append ? state.next : null;
        let hops = 0;
        try {
            for (;;) {
                const body = { where: state.where, sort: state.sort };
                if (after) body.after = after;
                const response = await fetch('/api/documents/find', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: this.app._jsonHeadersWithCsrf(),
                    body: JSON.stringify(body),
                });
                if (this.app._redirectIfLoginRequired(response)) return;
                const data = await response.json().catch(() => ({}));
                if (this._listing !== state) return;   // inzwischen neu gestartet
                if (!response.ok || !data.success) {
                    this._listing = null;
                    this.handleSearchRefusal(Object.assign(
                        { error: `Die Liste ist nicht verfügbar (${response.status}).` }, data),
                        '', { listing: true });
                    return;
                }
                if (!append && after === null && Number.isInteger(data.total_count)) {
                    state.total = data.total_count;
                }
                const rows = Array.isArray(data.documents) ? data.documents : [];
                state.next = data.next_after || null;
                if (!rows.length && state.next && hops < DocFieldsUI.MAX_EMPTY_HOPS) {
                    after = state.next;
                    hops += 1;
                    continue;
                }
                this.app.displayListing(rows, {
                    append, totalCount: state.total, more: !!state.next,
                    emptyText: String(data.empty_text || ''),
                });
                this._renderListingState(data);
                return;
            }
        } catch (error) {
            this._listing = null;
            this.handleSearchRefusal({ error: `Fehler bei der Liste: ${error.message}` }, '',
                { listing: true });
        } finally {
            if (!append) this.app.hideLoading();
            this.app.loadMoreButton.disabled = false;
        }
    }

    _renderListingState(data) {
        const df = data.document_fields || {};
        this._renderChips(df.resolved, false);
        // Der Hinweis kommt fertig vom Server (H5): nur auf der letzten Seite.
        const notice = data.notice || {};
        this._setText(this.listNotice, notice.incomplete ? notice.text : '');
        // H9: eine Liste nach Frist ist keine Fristenkontrolle.
        this._setText(this.banner, data.deadline_banner || '');
    }

    /** Uebergabe aus dem Cortex: Feld und Name aus sessionStorage, einmal. */
    _applyHandoff() {
        let raw = null;
        try {
            raw = window.sessionStorage.getItem(DocFieldsUI.HANDOFF_KEY);
            window.sessionStorage.removeItem(DocFieldsUI.HANDOFF_KEY);
        } catch (error) {
            return;
        }
        if (!raw || !this.showsListing) return;
        let handoff = null;
        try {
            handoff = JSON.parse(raw);
        } catch (error) {
            return;
        }
        const field = handoff && this._field(handoff.field);
        const name = handoff && typeof handoff.name === 'string' ? handoff.name.trim() : '';
        if (!field || field.datatype !== 'entity_ref' || !name) return;
        const control = this._controls.find((c) => c.key === field.key);
        if (control) {
            control.write({ name });
            this.showListing();
            return;
        }
        // Kein Eingabefeld in der Leiste (keine Facette): direkt listen.
        const where = {};
        where[field.key] = { name };
        this._listing = { where, sort: this.collectSort(), next: null, total: null };
        this._fetchPages(false);
    }

    // -- Feldpanel in der Vorschau ---------------------------------------

    clearPanel() {
        this._panelSeq += 1;
        this._panelDocId = '';
        this._panelView = null;
        if (this.panel) this.panel.replaceChildren();
        if (this.panelSection) this.panelSection.hidden = true;
    }

    async loadPanel(doc, message) {
        this.clearPanel();
        if (!this.showsValues || !this.panel) return;
        const docId = String((doc && (doc.doc_id || doc.pointer)) || '');
        if (!docId) return;
        const seq = this._panelSeq;
        this._panelDocId = docId;
        try {
            const response = await fetch('/api/document-fields/read', {
                method: 'POST',
                credentials: 'same-origin',
                headers: this.app._jsonHeadersWithCsrf(),
                body: JSON.stringify({ doc_id: docId }),
            });
            const data = await response.json().catch(() => ({}));
            if (seq !== this._panelSeq) return;
            if (!response.ok || !data.success) {
                if (response.status === 404 || data.error_code === 'doc_fields_off') return;
                this._panelMessage(String(data.error || 'Dokumentfelder nicht verfügbar.'));
                return;
            }
            this.renderPanel(data, message);
        } catch (error) {
            // Das Panel ist eine Zugabe: ohne es bleibt die Vorschau, wie sie war.
        }
    }

    _showPanel() {
        this.panelSection.hidden = false;
        if (this.app._syncPreviewSidebar) this.app._syncPreviewSidebar();
    }

    _panelMessage(text, kind) {
        const p = document.createElement('p');
        p.className = `doc-fields-message${kind ? ` doc-fields-message--${kind}` : ''}`;
        p.textContent = text;
        this.panel.prepend(p);
        this._showPanel();
    }

    _formatDate(iso) {
        const d = new Date(String(iso || ''));
        if (Number.isNaN(d.getTime())) return '';
        return d.toLocaleDateString('de-DE', { year: 'numeric', month: '2-digit', day: '2-digit' });
    }

    _row(label, valueNodes, key) {
        const row = document.createElement('div');
        row.className = 'doc-fields-row';
        if (key) row.dataset.key = key;
        const dt = document.createElement('div');
        dt.className = 'doc-fields-row-label';
        dt.textContent = label;
        const dd = document.createElement('div');
        dd.className = 'doc-fields-row-value';
        dd.append(...valueNodes.filter(Boolean));
        row.append(dt, dd);
        return row;
    }

    _span(className, text) {
        const span = document.createElement('span');
        span.className = className;
        span.textContent = text;
        return span;
    }

    _button(text, onClick, className = 'btn-text doc-fields-action') {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = className;
        b.textContent = text;
        b.addEventListener('click', onClick);
        return b;
    }

    /** Das Panel: Werte mit Herkunft, und Bearbeiten nur fuer editable_keys. */
    renderPanel(view, message) {
        this._panelView = view;
        this.panel.replaceChildren();
        const editable = new Set(Array.isArray(view.editable_keys) ? view.editable_keys : []);
        if (view.held) {
            this.panel.appendChild(this._span('doc-fields-held', String(view.held_text || '')));
            this._showPanel();
            if (message) this._panelMessage(message.text, message.kind);
            return;
        }
        const list = document.createElement('div');
        list.className = 'doc-fields-list';

        const titleValue = this._span('doc-fields-text', String(view.title || ''));
        const titleNodes = [titleValue];
        if (view.title_source === 'manual') titleNodes.push(this._span('doc-fields-layer', 'Manuell'));
        if (editable.has('title')) {
            titleNodes.push(this._span('doc-fields-note', String(view.title_note || '')));
            titleNodes.push(this._button('Bearbeiten', (e) => this._openEditor(e.target, {
                key: 'title', label: 'Titel', edit_value: view.title || '', datatype: 'title',
                layer: view.title_source === 'manual' ? 'manual' : null,
            })));
        }
        list.appendChild(this._row('Titel', titleNodes, 'title'));

        if (view.description || editable.has('description')) {
            const nodes = [this._span('doc-fields-text', String(view.description || '–'))];
            if (editable.has('description')) {
                nodes.push(this._button('Bearbeiten', (e) => this._openEditor(e.target, {
                    key: 'description', label: 'Beschreibung', edit_value: view.description || '',
                    datatype: 'description', layer: null,
                })));
            }
            list.appendChild(this._row('Beschreibung', nodes, 'description'));
        }

        const shown = new Set();
        (view.fields || []).forEach((field) => {
            shown.add(field.key);
            const nodes = [];
            nodes.push(this._span('doc-fields-text',
                field.unset ? 'entfernt' : String(field.text || '–')));
            if (field.layer_label) {
                nodes.push(this._span(`doc-fields-layer doc-fields-layer--${field.layer || 'x'}`,
                    String(field.layer_label)));
            }
            if (field.changed_at) {
                const date = this._formatDate(field.changed_at);
                if (date) nodes.push(this._span('doc-fields-note', `zuletzt manuell geändert am ${date}`));
            }
            if (field.note) nodes.push(this._span('doc-fields-note', String(field.note)));
            if (editable.has(field.key)) {
                nodes.push(this._button('Bearbeiten', (e) => this._openEditor(e.target, field)));
            }
            list.appendChild(this._row(String(field.label || field.key), nodes, field.key));
        });
        this.panel.appendChild(list);

        // Felder ohne Wert, die diese Person setzen darf.
        const addable = this.fields.filter((f) => f && editable.has(f.key) && !shown.has(f.key)
            && f.status !== 'deprecated');
        if (addable.length) {
            const select = document.createElement('select');
            select.className = 'doc-fields-add';
            select.setAttribute('aria-label', 'Feld hinzufügen');
            select.appendChild(this._option('', 'Feld hinzufügen …'));
            addable.forEach((f) => select.appendChild(this._option(f.key, String(f.label || f.key))));
            select.addEventListener('change', () => {
                const spec = this._field(select.value);
                if (!spec) return;
                this._openEditor(select, {
                    key: spec.key, label: spec.label, edit_value: null, datatype: spec.datatype,
                    cardinality: spec.cardinality, layer: null,
                });
            });
            this.panel.appendChild(select);
        }
        this._showPanel();
        if (message) this._panelMessage(message.text, message.kind);
    }

    /** Ein Eingabeelement fuer einen Wert (Bearbeiten). */
    _editorInput(field) {
        const spec = this._field(field.key) || {};
        const datatype = field.datatype || spec.datatype;
        const many = (field.cardinality || spec.cardinality) === 'many';
        if (datatype === 'enum') {
            const select = document.createElement('select');
            if (many) select.multiple = true;
            else select.appendChild(this._option('', '–'));
            const current = Array.isArray(field.edit_value) ? field.edit_value : [field.edit_value];
            (spec.enum || []).forEach((item) => {
                const opt = this._option(item.code, item.label || item.code);
                opt.selected = current.includes(item.code);
                select.appendChild(opt);
            });
            return {
                node: select,
                read: () => (many ? Array.from(select.selectedOptions).map((o) => o.value)
                    : (select.value || null)),
            };
        }
        if (datatype === 'bool') {
            const select = document.createElement('select');
            select.append(this._option('', '–'), this._option('true', 'Ja'), this._option('false', 'Nein'));
            select.value = field.edit_value === true ? 'true' : field.edit_value === false ? 'false' : '';
            return {
                node: select,
                read: () => (select.value === '' ? null : select.value === 'true'),
            };
        }
        if (datatype === 'description') {
            const area = document.createElement('textarea');
            area.maxLength = 2000;
            area.rows = 3;
            area.value = String(field.edit_value || '');
            return { node: area, read: () => area.value };
        }
        const input = document.createElement('input');
        input.type = 'text';
        input.autocomplete = 'off';
        if (datatype === 'title') input.maxLength = 500;
        const value = field.edit_value;
        input.value = Array.isArray(value) ? value.filter((v) => v != null).join('; ')
            : (value == null ? '' : String(value));
        if (many) input.placeholder = 'mehrere Werte mit ; trennen';
        return {
            node: input,
            read: () => {
                const text = input.value.trim();
                if (many) return text ? text.split(';').map((s) => s.trim()).filter(Boolean) : [];
                return text || null;
            },
        };
    }

    _openEditor(anchor, field) {
        const view = this._panelView;
        if (!view) return;
        this.panel.querySelectorAll('.doc-fields-editor').forEach((el) => el.remove());
        const form = document.createElement('div');
        form.className = 'doc-fields-editor';
        const label = this._span('doc-fields-editor-label', String(field.label || field.key));
        const editor = this._editorInput(field);
        const error = this._span('doc-fields-error', '');
        error.hidden = true;
        const save = this._button('Speichern', () => {
            const value = editor.read();
            const empty = value == null || value === '' || (Array.isArray(value) && !value.length);
            const set = {};
            if (field.key === 'title') {
                // Ein leerer Titel faellt auf den hochgeladenen zurueck.
                set.title = empty ? null : value;
            } else if (field.key === 'description') {
                set.description = value == null ? '' : value;
            } else if (empty) {
                // Leer gespeichert heisst "kein Wert", auch gegen Upload und
                // Ordnervorgabe -- dafuer gibt es unset. Zurueck auf diese
                // Werte fuehrt der eigene Knopf.
                this._save({ unset: [field.key] }, error);
                return;
            } else {
                set[field.key] = value;
            }
            this._save({ set }, error);
        }, 'btn btn-success doc-fields-save');
        const cancel = this._button('Abbrechen', () => form.remove(), 'btn btn-outline');
        const actions = document.createElement('div');
        actions.className = 'doc-fields-editor-actions';
        actions.append(save, cancel);
        if (field.layer === 'manual') {
            // Zurueck auf die tieferen Ebenen: set null loescht den manuellen Wert.
            actions.appendChild(this._button('Zurück zum Upload-/Ordnerwert', () => {
                const set = {};
                set[field.key] = null;
                this._save({ set }, error);
            }, 'btn btn-outline'));
        }
        form.append(label, editor.node, error, actions);
        const row = anchor.closest('.doc-fields-row');
        if (row) row.after(form);
        else this.panel.appendChild(form);
        editor.node.focus();
    }

    /** Speichern: einmal, mit der gelesenen Version. Konflikte nie ueberschreiben. */
    async _save(ops, errorEl) {
        const view = this._panelView;
        if (!view) return;
        const docId = this._panelDocId;
        const seq = this._panelSeq;
        let response;
        let data;
        try {
            response = await fetch('/api/document-fields/edit', {
                method: 'POST',
                credentials: 'same-origin',
                headers: this.app._jsonHeadersWithCsrf(),
                body: JSON.stringify(Object.assign({ doc_id: docId, if_version: view.version }, ops)),
            });
            data = await response.json().catch(() => ({}));
        } catch (error) {
            errorEl.textContent = `Speichern fehlgeschlagen: ${error.message}`;
            errorEl.hidden = false;
            return;
        }
        if (seq !== this._panelSeq) return;
        if (response.ok && data.success) {
            const notes = (data.warnings || []).map((w) => `${w.label || w.key || ''}: ${w.text || w.code}`);
            if (data.document) {
                this.renderPanel(data.document, notes.length
                    ? { text: `Gespeichert. Hinweise – ${notes.join('; ')}`, kind: 'warning' }
                    : { text: 'Gespeichert.', kind: 'ok' });
            } else {
                this.loadPanel({ doc_id: docId }, { text: 'Gespeichert.', kind: 'ok' });
            }
            return;
        }
        const code = data.error_code;
        const text = String(data.error || `Fehler ${response.status}`);
        if (code === 'version_conflict') {
            // Nie automatisch ueberschreiben: neu lesen und sagen, was geschah.
            const message = { text: `${text} (inzwischen geändert)`, kind: 'warning' };
            if (data.document) this.renderPanel(data.document, message);
            else this.loadPanel({ doc_id: docId }, message);
            return;
        }
        if (code === 'change_not_authorized' || code === 'anchor_quarantined') {
            const readOnly = Object.assign({}, data.document || view, { editable_keys: [] });
            this.renderPanel(readOnly, { text, kind: 'warning' });
            return;
        }
        errorEl.textContent = text;
        errorEl.hidden = false;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    // app.js legt `app` im selben DOMContentLoaded an; dieses Skript kommt
    // danach, sein Listener also auch.
    if (typeof app === 'undefined' || !app) return;
    app.docFields = new DocFieldsUI(app);
});
