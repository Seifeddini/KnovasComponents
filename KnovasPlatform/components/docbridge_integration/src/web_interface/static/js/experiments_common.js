// Experimente -- gemeinsame Bausteine der drei Seiten (window.KX).
//
// SICHERHEIT: Jeder Text vom Server (Namen, Labels, Einheiten, Headlines,
// Tabellenzellen, Warnungen, Protokolle, Meldungen, Notizen) geht ueber
// textContent in die Seite -- el() macht aus Zeichenketten Textknoten, nie
// HTML. Markdown laeuft ausschliesslich durch window.KnovasMarkdown.render,
// das zuerst escaped. Links entstehen nur aus Experiment-Schluesseln, die
// KEY_RE bestehen; innerHTML sieht hier nie einen Serverwert.
//
// Zahlen folgen denselben Regeln wie experiments/kinds.py (format_number,
// format_value, format_diff): Dezimalkomma, ASCII-Apostroph als
// Tausendertrennung, Prozent mit Leerzeichen, Anteilsdifferenzen in
// Prozentpunkten. Intl de-CH wird bewusst nicht benutzt: seine Trennzeichen
// unterscheiden sich zwischen Browsern, und dieselbe Zahl soll auf der Seite,
// in der API und im Knovas-Dokument gleich aussehen.
(function () {
    'use strict';

    const KEY_RE = /^[A-Z][A-Z0-9]{1,7}-[0-9]{1,9}$/;
    const COLOR_RE = /^#[0-9A-Fa-f]{6}$/;
    const DASH = '–';
    const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
    const SVG_NS = 'http://www.w3.org/2000/svg';
    /** Kategoriale Reihenfarben (CSS-Klassen kx-s1..kx-s8), feste Reihenfolge. */
    const SERIES_SLOTS = 8;

    /** Fallback-Beschriftungen, bis meta() die Serverfassung liefert
        (experiments/labels.py ist die Quelle). */
    const LABELS = {
        decision_verdicts: {
            ship: 'Übernehmen', iterate: 'Weiterentwickeln',
            stop: 'Verwerfen', inconclusive: 'Ohne klares Ergebnis',
        },
        evaluation_verdicts: {
            better: 'besser', worse: 'schlechter', inconclusive: 'offen', 'n/a': DASH,
        },
        note_kinds: {
            note: 'Notiz', observation: 'Beobachtung', interview: 'Interview',
            feedback: 'Rückmeldung', status: 'Statuswechsel',
        },
        directions: {
            higher: 'höher ist besser', lower: 'tiefer ist besser', none: 'ohne Richtung',
        },
        metric_roles: { primary: 'primär', secondary: 'sekundär', guardrail: 'Leitplanke' },
        run_statuses: {
            running: 'läuft', finished: 'abgeschlossen', failed: 'fehlgeschlagen',
            cancelled: 'abgebrochen',
        },
        evaluation_statuses: {
            queued: 'wartet', running: 'läuft', done: 'fertig', failed: 'fehlgeschlagen',
        },
        index_states: { pending: 'ausstehend', indexed: 'aktuell', error: 'Fehler', off: 'aus' },
        sources: { manual: 'von Hand', api: 'API', csv: 'CSV' },
    };

    // ── Anfragen ────────────────────────────────────────────────────────

    function csrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') || '' : '';
    }

    class ApiError extends Error {
        constructor(message, status, fields) {
            super(message);
            this.name = 'ApiError';
            this.status = status || 0;
            this.fields = fields && typeof fields === 'object' ? fields : {};
        }
    }

    function redirectToLogin() {
        const next = window.location.pathname + window.location.search;
        window.location.href = `/login?next=${encodeURIComponent(next)}`;
    }

    /** Deutscher Text fuer eine Antwort ohne eigene Fehlermeldung (z. B. eine
        HTML-Seite des Proxys). Nie der Rohtext der Antwort. */
    function statusMessage(status, tooLarge) {
        if (status === 403) return 'Dafür fehlt Ihnen die Berechtigung.';
        if (status === 404) return 'Nicht gefunden.';
        if (status === 409) return 'Das Experiment wurde inzwischen geändert. Bitte neu laden.';
        if (status === 413) return tooLarge || 'Die Anfrage ist zu gross.';
        if (status === 429) return 'Zu viele Anfragen. Bitte einen Moment warten.';
        if (status === 500) return 'Interner Serverfehler';
        if (status >= 500) return 'Der Server ist gerade nicht erreichbar. Bitte später erneut versuchen.';
        return `Die Anfrage ist fehlgeschlagen (HTTP ${status}).`;
    }

    async function handleResponse(resp, tooLarge) {
        if (resp.status === 401) {
            redirectToLogin();
            throw new ApiError('Bitte melden Sie sich erneut an.', 401);
        }
        let data = null;
        const type = resp.headers.get('Content-Type') || '';
        if (type.indexOf('application/json') !== -1) {
            try {
                data = await resp.json();
            } catch (_) {
                data = null;
            }
        }
        if (!resp.ok || !data || typeof data !== 'object' || data.success === false) {
            const serverText = data && typeof data.error === 'string' && data.error.trim();
            const message = serverText || statusMessage(resp.status, tooLarge);
            throw new ApiError(message, resp.status, data && data.fields);
        }
        return data;
    }

    /**
     * JSON-Anfrage an die Experimente-API. Gibt die ganze Antwort zurueck
     * ({success: true, <key>: ...}); wirft ApiError mit .status und .fields.
     * @param {string} method
     * @param {string} url
     * @param {*} [body] wird als JSON gesendet, wenn gesetzt
     * @param {{signal?: AbortSignal}} [options]
     */
    async function api(method, url, body, options) {
        const verb = String(method || 'GET').toUpperCase();
        const headers = { Accept: 'application/json' };
        const init = { method: verb, credentials: 'same-origin', headers };
        if (verb !== 'GET' && verb !== 'HEAD') headers['X-CSRF-Token'] = csrfToken();
        if (body !== undefined && body !== null) {
            headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(body);
        }
        if (options && options.signal) init.signal = options.signal;
        let resp;
        try {
            resp = await fetch(url, init);
        } catch (err) {
            if (err && err.name === 'AbortError') throw err;
            throw new ApiError('Keine Verbindung zum Server. Bitte später erneut versuchen.', 0);
        }
        return handleResponse(resp);
    }

    /** Datei als multipart/form-data (Feld "file"). Die 20-MB-Grenze wird
        vorab geprueft, damit niemand eine grosse Datei vergeblich hochlaedt. */
    async function upload(url, file) {
        const tooLarge = 'Die Datei ist grösser als 20 MB.';
        if (!file) throw new ApiError('Bitte eine Datei auswählen.', 400);
        if (file.size > MAX_UPLOAD_BYTES) throw new ApiError(tooLarge, 413);
        const form = new FormData();
        form.append('file', file, file.name);
        let resp;
        try {
            resp = await fetch(url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'X-CSRF-Token': csrfToken(), Accept: 'application/json' },
                body: form,
            });
        } catch (_) {
            throw new ApiError('Keine Verbindung zum Server. Bitte später erneut versuchen.', 0);
        }
        return handleResponse(resp, tooLarge);
    }

    function errorMessage(err) {
        if (err && err.name === 'ApiError' && err.message) return err.message;
        return 'Das hat nicht geklappt. Bitte erneut versuchen.';
    }

    let metaPromise = null;

    /** GET /api/experiments/meta, einmal je Seite. Ein Fehlschlag wird nicht
        zwischengespeichert, der naechste Aufruf versucht es erneut. */
    function meta() {
        if (!metaPromise) {
            metaPromise = api('GET', '/api/experiments/meta').then((data) => {
                const m = data.meta || {};
                for (const name of Object.keys(LABELS)) {
                    if (m[name] && typeof m[name] === 'object') {
                        LABELS[name] = Object.assign({}, LABELS[name], m[name]);
                    }
                }
                return m;
            }).catch((err) => {
                metaPromise = null;
                throw err;
            });
        }
        return metaPromise;
    }

    /** Die JSON-Insel der Seite (#kxPageData), die das Template mit |tojson fuellt. */
    function pageData() {
        const node = document.getElementById('kxPageData');
        if (!node) return {};
        try {
            const data = JSON.parse(node.textContent || '{}');
            return data && typeof data === 'object' ? data : {};
        } catch (_) {
            return {};
        }
    }

    // ── DOM ─────────────────────────────────────────────────────────────

    function esc(text) {
        return String(text == null ? '' : text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /** Nur same-origin-Pfade, Anker und http(s)/mailto. "//host" und "/\host"
        waeren fremde Hosts, javascript: sowieso nicht. */
    function safeUrl(url) {
        const s = String(url == null ? '' : url).trim();
        return /^(\/(?![\/\\])|#|\?|https?:\/\/|mailto:)/i.test(s);
    }

    const BOOLEAN_PROPS = new Set(['checked', 'disabled', 'selected', 'hidden', 'required',
        'multiple', 'readOnly', 'open', 'noValidate']);
    const URL_ATTRS = new Set(['href', 'src', 'action', 'formaction']);

    function applyAttrs(node, attrs) {
        if (!attrs) return;
        for (const name of Object.keys(attrs)) {
            const value = attrs[name];
            if (value === undefined || value === null || value === false) continue;
            if (name === 'class' || name === 'className') {
                node.setAttribute('class', Array.isArray(value)
                    ? value.filter(Boolean).join(' ') : String(value));
            } else if (name === 'text') {
                node.textContent = String(value);
            } else if (name === 'dataset') {
                for (const k of Object.keys(value)) {
                    if (value[k] != null) node.dataset[k] = String(value[k]);
                }
            } else if (name === 'style' && typeof value === 'object') {
                for (const k of Object.keys(value)) node.style.setProperty(k, String(value[k]));
            } else if (/^on[A-Za-z]/.test(name)) {
                // Nur Funktionen; ein String-Handler waere Inline-Script.
                if (typeof value === 'function') {
                    node.addEventListener(name.slice(2).toLowerCase(), value);
                }
            } else if (URL_ATTRS.has(name)) {
                if (safeUrl(value)) node.setAttribute(name, String(value));
            } else if (BOOLEAN_PROPS.has(name)) {
                node[name] = Boolean(value);
                if (name !== 'checked' && name !== 'selected') {
                    node.setAttribute(name === 'readOnly' ? 'readonly' : name.toLowerCase(), '');
                }
            } else if (name === 'value' && 'value' in node) {
                node.value = String(value);
            } else {
                node.setAttribute(name, value === true ? '' : String(value));
            }
        }
    }

    function appendChildren(node, children) {
        for (const child of children) {
            if (child === null || child === undefined || child === false || child === true) continue;
            if (Array.isArray(child)) {
                appendChildren(node, child);
            } else if (typeof child === 'object' && typeof child.nodeType === 'number') {
                node.appendChild(child);
            } else {
                node.appendChild(document.createTextNode(String(child)));
            }
        }
    }

    /** el('div', {class: 'x', onClick: fn}, 'Text', otherNode, [a, b]) --
        Zeichenketten werden Textknoten, nie HTML. */
    function el(tag, attrs, ...children) {
        const node = document.createElement(tag);
        applyAttrs(node, attrs);
        appendChildren(node, children);
        return node;
    }

    /** SVG-Element; Attribute als Strings, Kinder wie bei el(). */
    function svgEl(tag, attrs, ...children) {
        const node = document.createElementNS(SVG_NS, tag);
        if (attrs) {
            for (const name of Object.keys(attrs)) {
                const value = attrs[name];
                if (value === undefined || value === null || value === false) continue;
                if (/^on/i.test(name)) continue;
                if (name === 'href' || name === 'xlink:href') continue;
                node.setAttribute(name, String(value));
            }
        }
        appendChildren(node, children);
        return node;
    }

    function clear(node) {
        while (node && node.firstChild) node.removeChild(node.firstChild);
        return node;
    }

    let idCounter = 0;
    function uid(prefix) {
        idCounter += 1;
        return `${prefix || 'kx'}-${idCounter}`;
    }

    function debounce(fn, ms) {
        let timer = null;
        return function debounced(...args) {
            if (timer) window.clearTimeout(timer);
            timer = window.setTimeout(() => {
                timer = null;
                fn.apply(this, args);
            }, ms);
        };
    }

    // ── Datum und Zahlen ────────────────────────────────────────────────

    function pad2(n) {
        return String(n).padStart(2, '0');
    }

    /** ISO 8601 -> Date. Ein reines Datum (JJJJ-MM-TT) gilt als lokaler Tag,
        damit es nicht je nach Zeitzone auf den Vortag rutscht. */
    function parseDate(iso) {
        if (iso === null || iso === undefined || iso === '') return null;
        const s = String(iso).trim();
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
        if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
        const d = new Date(s);
        return Number.isNaN(d.getTime()) ? null : d;
    }

    function fmtDate(iso) {
        const d = parseDate(iso);
        if (!d) return DASH;
        return `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}.${d.getFullYear()}`;
    }

    function fmtDateTime(iso) {
        const d = parseDate(iso);
        if (!d) return DASH;
        return `${fmtDate(iso)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
    }

    /** Datum eines UTC-Zeitfensters (Wochen- und Monatsbeginn der Verlaufsdaten). */
    function fmtDateUTC(iso, month) {
        const d = parseDate(iso);
        if (!d) return DASH;
        if (month) return `${pad2(d.getUTCMonth() + 1)}.${d.getUTCFullYear()}`;
        return `${pad2(d.getUTCDate())}.${pad2(d.getUTCMonth() + 1)}.${d.getUTCFullYear()}`;
    }

    /** Heutiges Datum als JJJJ-MM-TT (lokal), fuer Datumsfelder. */
    function todayIso() {
        const d = new Date();
        return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
    }

    /** Wie kinds._as_float: nur echte, endliche Zahlen; true und "12" nicht. */
    function finite(x) {
        return typeof x === 'number' && Number.isFinite(x) ? x : null;
    }

    /** Wie kinds._decimals: None/bool -> Standard, sonst ganzzahlig 0..10. */
    function clampDecimals(d, fallback) {
        if (d === null || d === undefined || typeof d === 'boolean') return fallback;
        const n = Math.trunc(Number(d));
        if (!Number.isFinite(n)) return fallback;
        return Math.max(0, Math.min(10, n));
    }

    /** 10714.5 -> "10'714,50"; ohne Zahl "–". Rundung wie toFixed: auf dem
        exakten Binaerwert, bei Gleichstand weg von null (kinds.format_number
        rundet genauso). */
    function fmtNumber(x, decimals) {
        const v = finite(x);
        if (v === null) return DASH;
        const d = clampDecimals(decimals, 2);
        const abs = Math.abs(v);
        let fixed;
        if (abs >= 1e21) {
            // toFixed schaltet hier auf Exponentialschreibweise um; so grosse
            // Doubles sind ganzzahlig, BigInt liefert ihre exakten Ziffern.
            fixed = BigInt(abs).toString() + (d > 0 ? `.${'0'.repeat(d)}` : '');
        } else {
            fixed = abs.toFixed(d);
        }
        const parts = fixed.split('.');
        const grouped = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, "'");
        const text = parts.length > 1 ? `${grouped},${parts[1]}` : grouped;
        // Was auf null rundet, bekommt kein Minus ("-0,00" liest sich als Verlust).
        if (v < 0 && /[1-9]/.test(text)) return `-${text}`;
        return text;
    }

    /** Ohne aufgefuellte Nullen (kinds.format_plain): 0.5 -> "0,5", 1000 -> "1'000". */
    function fmtPlain(x, maxDecimals) {
        const v = finite(x);
        if (v === null) return DASH;
        if (Number.isInteger(v) && Math.abs(v) < 1e15) return fmtNumber(v, 0);
        let text = fmtNumber(v, clampDecimals(maxDecimals, 6));
        if (text.indexOf(',') !== -1) text = text.replace(/0+$/, '').replace(/,$/, '');
        return (text === '' || text === '-0' || text === '-') ? '0' : text;
    }

    /** Schaetzwert fuer Menschen (kinds.format_value): Anteile in Prozent. */
    function fmtEstimate(kind, x, unit, decimals) {
        const d = clampDecimals(decimals, 2);
        if (kind === 'proportion') {
            const v = finite(x);
            if (v === null) return DASH;
            const text = fmtNumber(v * 100, d);
            return text === DASH ? DASH : `${text} %`;
        }
        const text = fmtNumber(x, d);
        if (text === DASH) return DASH;
        const u = String(unit == null ? '' : unit).trim();
        return u ? `${text} ${u}` : text;
    }

    /** Vorzeichenbehaftete Differenz (kinds.format_diff): Anteile in Pp. */
    function fmtDiff(kind, x, unit, decimals) {
        const d = clampDecimals(decimals, 2);
        let v = finite(x);
        if (v === null) return DASH;
        let suffix;
        if (kind === 'proportion') {
            v *= 100;
            suffix = ' Pp.';
        } else {
            const u = String(unit == null ? '' : unit).trim();
            suffix = u ? ` ${u}` : '';
        }
        let text = fmtNumber(v, d);
        if (text === DASH) return DASH;
        if (text.charAt(0) !== '-') text = `+${text}`;
        return text + suffix;
    }

    /** Wahrscheinlichkeit 0..1 als Prozent mit einer Stelle. */
    function fmtPercent(p, decimals) {
        return fmtEstimate('proportion', p, '', decimals == null ? 1 : decimals);
    }

    function fmtPValue(p) {
        const v = finite(p);
        if (v === null) return DASH;
        if (v < 0.001) return '< 0,001';
        return fmtNumber(v, 3);
    }

    /** Eingabe wie der Server sie liest (schema._parse_number): "1'250,5" -> 1250.5.
        null fuer leer oder keine Zahl. */
    function parseNumber(raw) {
        if (typeof raw === 'number') return Number.isFinite(raw) ? raw : null;
        if (typeof raw !== 'string') return null;
        let text = raw.trim().replace(/['’\s\u00a0]/g, '');
        if (text === '') return null;
        if (text.indexOf(',') !== -1 && text.indexOf('.') === -1
            && text.split(',').length === 2) {
            text = text.replace(',', '.');
        }
        if (!/^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d{1,3})?$/.test(text)) return null;
        const value = Number(text);
        return Number.isFinite(value) ? value : null;
    }

    // ── Schluessel und Links ────────────────────────────────────────────

    function isKey(key) {
        return typeof key === 'string' && KEY_RE.test(key);
    }

    /** /experiments/<KEY> fuer einen gueltigen Schluessel, sonst null. */
    function experimentUrl(key) {
        return isKey(key) ? `/experiments/${key}` : null;
    }

    /** app_url aus einem Suchtreffer: nur /experiments/<KEY>, nichts sonst. */
    function safeAppUrl(url) {
        if (typeof url !== 'string' || url.indexOf('/experiments/') !== 0) return null;
        return isKey(url.slice('/experiments/'.length)) ? url : null;
    }

    // ── Rueckmeldungen ──────────────────────────────────────────────────

    const TOAST_TIMEOUT_MS = { error: 10000, success: 6000, info: 6000 };

    /** Dieselben Toasts wie auf der Suchseite (style.css .toast). */
    function toast(message, kind) {
        const variant = kind === 'error' || kind === 'success' ? kind : 'info';
        let container = document.getElementById('toastContainer');
        if (!container) {
            container = el('div', {
                class: 'toast-container', id: 'toastContainer', role: 'status',
                'aria-live': 'polite', 'aria-atomic': 'false',
            });
            document.body.appendChild(container);
        }
        const node = el('div', { class: `toast toast--${variant}` },
            el('div', { class: 'toast-text', text: String(message == null ? '' : message) }));
        const close = el('button', {
            type: 'button', class: 'toast-close', 'aria-label': 'Meldung schliessen',
            text: '×', onClick: () => node.remove(),
        });
        node.appendChild(close);
        container.appendChild(node);
        window.setTimeout(() => node.remove(), TOAST_TIMEOUT_MS[variant]);
        return node;
    }

    // ── Formularfelder ──────────────────────────────────────────────────

    /**
     * Eine Formularzeile: Label, Eingabe, Hilfetext und ein Platz fuer die
     * Fehlermeldung des Servers. `name` verbindet die Zeile mit den Schluesseln
     * in err.fields (siehe showFieldErrors).
     */
    function field(opts) {
        const input = opts.input;
        const id = input.id || uid('kx-field');
        input.id = id;
        const errorId = `${id}-error`;
        const helpId = opts.help ? `${id}-help` : null;
        const described = [helpId, errorId].filter(Boolean).join(' ');
        input.setAttribute('aria-describedby', described);
        const names = [].concat(opts.name || [], opts.aliases || []).filter(Boolean);
        const labelText = opts.required ? `${opts.label} *` : opts.label;
        return el('div', {
            class: ['kx-field', opts.inline ? 'kx-field--inline' : '', opts.class || ''],
            dataset: { field: names.join(' ') },
        },
        el('label', { for: id, class: 'kx-label', text: labelText }),
        input,
        opts.help ? el('small', { class: 'kx-help', id: helpId, text: opts.help }) : null,
        el('span', { class: 'kx-field-error', id: errorId, 'aria-live': 'polite' }));
    }

    function input(attrs) {
        return el('input', Object.assign({ class: 'kx-input', type: 'text' }, attrs || {}));
    }

    function textarea(attrs, value) {
        const node = el('textarea', Object.assign({ class: 'kx-input kx-textarea' }, attrs || {}));
        if (value != null) node.value = String(value);
        return node;
    }

    /** Auswahlliste; options: [{value, label, disabled?}] oder [value, label]-Paare. */
    function select(attrs, options, current) {
        const node = el('select', Object.assign({ class: 'kx-input kx-select' }, attrs || {}));
        for (const opt of options) {
            const o = Array.isArray(opt) ? { value: opt[0], label: opt[1] } : opt;
            const option = el('option', { value: o.value == null ? '' : String(o.value) },
                String(o.label == null ? o.value : o.label));
            if (o.disabled) option.disabled = true;
            node.appendChild(option);
        }
        if (current !== undefined && current !== null) node.value = String(current);
        return node;
    }

    function clearFieldErrors(container) {
        if (!container) return;
        container.querySelectorAll('.kx-field-error').forEach((n) => { n.textContent = ''; });
        container.querySelectorAll('[aria-invalid="true"]').forEach((n) => n.removeAttribute('aria-invalid'));
    }

    /**
     * Feldfehler des Servers an die passenden Zeilen haengen. Liefert die
     * Meldungen, die keiner Zeile zugeordnet werden konnten (fuer eine
     * Sammelmeldung). Pfade wie "fields.channel" oder "rows.0.value" treffen
     * auch eine Zeile namens "channel" bzw. "value".
     */
    function showFieldErrors(container, fields) {
        const unmatched = [];
        if (!fields || typeof fields !== 'object') return unmatched;
        const rows = container ? Array.from(container.querySelectorAll('.kx-field[data-field]')) : [];
        for (const key of Object.keys(fields)) {
            const message = String(fields[key]);
            const candidates = [key, key.replace(/^(fields|definition|data)\./, ''),
                key.split('.').pop()];
            const row = rows.find((r) => {
                const names = (r.dataset.field || '').split(' ');
                return candidates.some((c) => c && names.indexOf(c) !== -1);
            });
            if (row) {
                const slot = row.querySelector('.kx-field-error');
                if (slot) slot.textContent = slot.textContent ? `${slot.textContent} ${message}` : message;
                const control = row.querySelector('input, select, textarea');
                if (control) control.setAttribute('aria-invalid', 'true');
            } else {
                unmatched.push(`${key}: ${message}`);
            }
        }
        return unmatched;
    }

    /** Eingabe fuer ein Feld der Experimentart (schema FIELD_TYPES). */
    function renderFieldInput(fieldDef, value) {
        const f = fieldDef || {};
        const key = String(f.key || '');
        const label = String(f.label || key);
        const helpParts = [];
        if (f.help) helpParts.push(String(f.help));
        if ((f.type === 'number' || f.type === 'integer') && (finite(f.min) !== null || finite(f.max) !== null)) {
            if (finite(f.min) !== null && finite(f.max) !== null) {
                helpParts.push(`Erlaubt: ${fmtPlain(f.min)}–${fmtPlain(f.max)}.`);
            } else if (finite(f.min) !== null) {
                helpParts.push(`Mindestens ${fmtPlain(f.min)}.`);
            } else {
                helpParts.push(`Höchstens ${fmtPlain(f.max)}.`);
            }
        }
        const empty = value === null || value === undefined || value === '';
        let control;
        switch (f.type) {
        case 'longtext':
            control = textarea({ name: key, rows: 4, maxlength: 20000 }, empty ? '' : value);
            break;
        case 'number':
        case 'integer':
            control = input({
                name: key, inputmode: f.type === 'integer' ? 'numeric' : 'decimal',
                value: empty ? '' : (typeof value === 'number' ? fmtPlain(value).replace(/'/g, '') : String(value)),
            });
            break;
        case 'enum': {
            const opts = [{ value: '', label: DASH }].concat(
                (f.options || []).map((o) => ({ value: o, label: o })));
            control = select({ name: key }, opts, empty ? '' : value);
            break;
        }
        case 'multi_enum': {
            const chosen = new Set(Array.isArray(value) ? value.map(String) : (empty ? [] : [String(value)]));
            const legendId = uid('kx-legend');
            control = el('div', { class: 'kx-choice-group', role: 'group', 'aria-labelledby': legendId });
            control.appendChild(el('span', { id: legendId, class: 'kx-visually-hidden', text: label }));
            for (const opt of f.options || []) {
                const box = el('input', { type: 'checkbox', value: String(opt) });
                box.checked = chosen.has(String(opt));
                control.appendChild(el('label', { class: 'kx-choice' }, box, el('span', { text: String(opt) })));
            }
            break;
        }
        case 'date':
            control = input({ name: key, type: 'date', value: empty ? '' : String(value).slice(0, 10) });
            break;
        case 'url':
            control = input({ name: key, type: 'url', maxlength: 2000, value: empty ? '' : String(value),
                placeholder: 'https://' });
            break;
        case 'boolean':
            control = select({ name: key }, [
                { value: '', label: DASH }, { value: 'true', label: 'ja' }, { value: 'false', label: 'nein' },
            ], empty ? '' : (value === true || value === 'true' ? 'true' : 'false'));
            break;
        default:
            control = input({ name: key, maxlength: 500, value: empty ? '' : String(value) });
        }
        if (f.required) control.setAttribute('aria-required', 'true');
        const row = field({
            label, input: control, name: key, aliases: [`fields.${key}`],
            help: helpParts.join(' ') || null, required: Boolean(f.required),
        });
        row.dataset.fieldKey = key;
        row.dataset.fieldType = String(f.type || 'text');
        return row;
    }

    /**
     * Wert aus renderFieldInput zuruecklesen. null heisst "leer". Eine Zahl,
     * die sich nicht lesen laesst, geht als Text an den Server -- dessen
     * Meldung sagt genauer, was nicht stimmt.
     */
    function readFieldInput(fieldDef, row) {
        const f = fieldDef || {};
        if (!row) return null;
        if (f.type === 'multi_enum') {
            const values = Array.from(row.querySelectorAll('input[type=checkbox]'))
                .filter((b) => b.checked).map((b) => b.value);
            return values.length ? values : null;
        }
        const control = row.querySelector('input, select, textarea');
        if (!control) return null;
        const raw = String(control.value == null ? '' : control.value);
        const trimmed = raw.trim();
        if (trimmed === '') return null;
        switch (f.type) {
        case 'number':
        case 'integer': {
            const n = parseNumber(trimmed);
            return n === null ? trimmed : n;
        }
        case 'boolean':
            return trimmed === 'true';
        case 'longtext':
            return raw.replace(/\s+$/, '');
        default:
            return trimmed;
        }
    }

    // ── Dialog ──────────────────────────────────────────────────────────

    /**
     * Nativer <dialog>: Backdrop, Escape, Fokusfalle und inerter Hintergrund
     * kommen vom Browser.
     *
     * actions: [{label, value?, primary?, danger?, onClick?(ctx)}]. onClick
     * darf async sein; wirft es, bleibt der Dialog offen und zeigt die
     * Meldung (Feldfehler an den Zeilen); gibt es false zurueck, bleibt er
     * ebenfalls offen. Das Promise liefert value (bzw. das Ergebnis von
     * onClick) oder null, wenn der Dialog abgebrochen wurde.
     */
    function dialog(opts) {
        const options = opts || {};
        return new Promise((resolve) => {
            const titleId = uid('kx-dialog-title');
            const previous = document.activeElement;
            let busy = false;
            let settled = false;
            const dlg = el('dialog', {
                class: ['kx-dialog', options.wide ? 'kx-dialog--wide' : ''],
                'aria-labelledby': titleId,
            });
            const form = el('form', { method: 'dialog', class: 'kx-dialog-form', noValidate: true });
            const closeButton = el('button', {
                type: 'button', class: 'kx-icon-button', 'aria-label': 'Dialog schliessen', text: '×',
            });
            const header = el('header', { class: 'kx-dialog-header' },
                el('div', null,
                    el('h2', { id: titleId, text: String(options.title || '') }),
                    options.description ? el('p', { class: 'kx-hint', text: String(options.description) }) : null),
                closeButton);
            const body = el('div', { class: 'kx-dialog-body' });
            if (options.body !== undefined && options.body !== null) {
                appendChildren(body, [typeof options.body === 'object' ? options.body
                    : el('p', { text: String(options.body) })]);
            }
            const errorBox = el('div', { class: 'kx-dialog-error', role: 'alert', hidden: true });
            const footer = el('div', { class: 'kx-dialog-actions' });
            const buttons = [];
            const actions = options.actions && options.actions.length
                ? options.actions : [{ label: 'Schliessen', value: null }];

            function finish(value) {
                if (settled) return;
                settled = true;
                if (dlg.open) dlg.close();
                dlg.remove();
                if (previous && typeof previous.focus === 'function' && document.contains(previous)) {
                    previous.focus();
                }
                resolve(value);
            }

            function setBusy(state) {
                busy = state;
                buttons.forEach((b) => { b.disabled = state; });
                closeButton.disabled = state;
                form.setAttribute('aria-busy', state ? 'true' : 'false');
            }

            const ctx = {
                dialog: dlg,
                body,
                close: (value) => finish(value === undefined ? null : value),
                setError(message) {
                    errorBox.textContent = String(message || '');
                    errorBox.hidden = !message;
                },
            };

            async function run(action) {
                if (busy) return;
                errorBox.hidden = true;
                errorBox.textContent = '';
                clearFieldErrors(body);
                if (typeof action.onClick !== 'function') {
                    finish(action.value === undefined ? null : action.value);
                    return;
                }
                setBusy(true);
                try {
                    const result = await action.onClick(ctx);
                    setBusy(false);
                    if (result === false) return;
                    finish(action.value !== undefined ? action.value : (result === undefined ? true : result));
                } catch (err) {
                    setBusy(false);
                    const unmatched = showFieldErrors(body, err && err.fields);
                    const lines = [errorMessage(err)].concat(unmatched);
                    clear(errorBox);
                    lines.forEach((line, i) => {
                        errorBox.appendChild(el('p', { class: i ? 'kx-dialog-error-detail' : null, text: line }));
                    });
                    errorBox.hidden = false;
                    const invalid = body.querySelector('[aria-invalid="true"]');
                    if (invalid) invalid.focus();
                }
            }

            let primary = null;
            for (const action of actions) {
                const button = el('button', {
                    type: action.primary ? 'submit' : 'button',
                    class: ['btn', action.primary ? 'btn-primary' : 'btn-outline',
                        action.danger ? 'btn-danger' : '', 'btn-sm'],
                    text: String(action.label),
                });
                if (action.primary) {
                    primary = action;
                } else {
                    button.addEventListener('click', () => run(action));
                }
                buttons.push(button);
                footer.appendChild(button);
            }
            form.addEventListener('submit', (e) => {
                e.preventDefault();
                if (primary) run(primary);
            });
            closeButton.addEventListener('click', () => { if (!busy) finish(null); });
            dlg.addEventListener('cancel', (e) => {
                e.preventDefault();
                if (!busy) finish(null);
            });
            form.appendChild(header);
            form.appendChild(body);
            form.appendChild(errorBox);
            form.appendChild(footer);
            dlg.appendChild(form);
            document.body.appendChild(dlg);
            dlg.showModal();
            const first = body.querySelector('input:not([type=hidden]):not([disabled]), select, textarea');
            if (first) first.focus();
            else if (buttons.length) buttons[buttons.length - 1].focus();
        });
    }

    /** Rueckfrage mit zwei Knoepfen; true bei Bestaetigung. */
    function confirm(title, text, confirmLabel, danger) {
        return dialog({
            title,
            body: el('p', { text: String(text || '') }),
            actions: [
                { label: 'Abbrechen', value: false },
                { label: confirmLabel || 'Bestätigen', value: true, primary: true, danger: Boolean(danger) },
            ],
        }).then((v) => v === true);
    }

    // ── Chips und Punkte ────────────────────────────────────────────────

    function label(map, key) {
        const table = LABELS[map] || {};
        const k = String(key == null ? '' : key);
        return Object.prototype.hasOwnProperty.call(table, k) ? table[k] : k;
    }

    function chip(text, variant, attrs) {
        return el('span', Object.assign({
            class: ['kx-chip', variant ? `kx-chip--${variant}` : ''],
        }, attrs || {}), String(text == null ? '' : text));
    }

    const VERDICT_VARIANT = { better: 'good', worse: 'bad', inconclusive: 'open', 'n/a': 'muted' };

    /** Ergebnis einer Auswertung (better/worse/inconclusive/n/a). */
    function verdictChip(verdict) {
        const v = VERDICT_VARIANT[verdict] ? verdict : 'n/a';
        const text = v === 'n/a' ? 'ohne Urteil' : label('evaluation_verdicts', v);
        return chip(text, VERDICT_VARIANT[v], { dataset: { verdict: v } });
    }

    const DECISION_VARIANT = { ship: 'good', iterate: 'open', stop: 'bad', inconclusive: 'muted' };

    function decisionChip(verdict) {
        return chip(label('decision_verdicts', verdict), DECISION_VARIANT[verdict] || 'muted');
    }

    /** Status eines Experiments; die Phase (running/decided/stopped) faerbt. */
    function statusChip(text, phase) {
        const variant = phase === 'running' ? 'running' : phase === 'decided' ? 'good'
            : phase === 'stopped' ? 'muted' : 'neutral';
        return chip(text, variant, { dataset: { phase: phase || '' } });
    }

    /** Farbpunkt eines Bereichs; nur #RRGGBB wird uebernommen. */
    function domainDot(color) {
        const dot = el('span', { class: 'kx-dot', 'aria-hidden': 'true' });
        if (typeof color === 'string' && COLOR_RE.test(color)) dot.style.backgroundColor = color;
        return dot;
    }

    /** Leerer Zustand mit optionalen Aktionen. */
    function emptyState(title, text, actions) {
        return el('div', { class: 'kx-empty' },
            title ? el('p', { class: 'kx-empty-title', text: title }) : null,
            text ? el('p', { class: 'kx-empty-text', text }) : null,
            actions && actions.length ? el('div', { class: 'kx-empty-actions' }, actions) : null);
    }

    function spinnerText(text) {
        return el('p', { class: 'kx-loading', role: 'status' }, text || 'Wird geladen …');
    }

    /** Markdown nur ueber KnovasMarkdown (escaped zuerst), sonst als Text. */
    function renderMarkdown(md) {
        const box = el('div', { class: 'kx-markdown' });
        const text = String(md == null ? '' : md);
        const renderer = window.KnovasMarkdown;
        if (renderer && typeof renderer.render === 'function') {
            box.innerHTML = renderer.render(text);
        } else {
            box.appendChild(el('pre', { class: 'kx-pre', text }));
        }
        return box;
    }

    async function copyText(text) {
        try {
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(String(text));
                return true;
            }
        } catch (_) { /* Fallback unten */ }
        const area = el('textarea', { class: 'kx-visually-hidden', readOnly: true });
        area.value = String(text);
        document.body.appendChild(area);
        area.select();
        let ok = false;
        try {
            ok = document.execCommand('copy');
        } catch (_) {
            ok = false;
        }
        area.remove();
        return ok;
    }

    /** Text als Datei speichern (Export eines Bereichs). */
    function downloadText(filename, text, type) {
        const safeName = String(filename || 'export.txt').replace(/[^A-Za-z0-9._-]+/g, '_').slice(0, 120);
        const blob = new Blob([String(text == null ? '' : text)], { type: type || 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = safeName;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    /** Tab-Taste fuegt im Code-Feld vier Leerzeichen ein statt den Fokus zu
        verschieben; Escape gibt die Taste wieder frei (Tastaturfalle vermeiden). */
    function codeEditor(node) {
        let tabTraps = true;
        node.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                tabTraps = false;
                return;
            }
            if (e.key !== 'Tab' || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey || !tabTraps) return;
            e.preventDefault();
            const start = node.selectionStart;
            const end = node.selectionEnd;
            node.value = `${node.value.slice(0, start)}    ${node.value.slice(end)}`;
            node.selectionStart = start + 4;
            node.selectionEnd = start + 4;
        });
        node.addEventListener('focus', () => { tabTraps = true; });
        node.setAttribute('spellcheck', 'false');
        return node;
    }

    // ── Diagramme ───────────────────────────────────────────────────────

    /**
     * Intervall einer Differenz als kleiner Balken mit Referenzlinie (0 fuer
     * Differenzen, 1 fuer Verhaeltnisse). Gruen, wenn das ganze Intervall auf
     * der guten Seite liegt, rot auf der schlechten, sonst neutral -- die
     * Chips daneben sagen dasselbe in Worten.
     *
     * opts: {direction, unit, kind, decimals, reference (0), extent (halbe
     * Achsenbreite, fuer gemeinsame Skalen), text (Beschriftung)}
     */
    function intervalBar(estimate, lo, hi, opts) {
        const o = opts || {};
        const est = finite(estimate);
        const low = finite(lo);
        const high = finite(hi);
        const ref = finite(o.reference) === null ? 0 : o.reference;
        const width = 240;
        const height = 30;
        const padX = 8;
        const values = [est, low, high].filter((v) => v !== null);
        if (!values.length) return el('span', { class: 'kx-muted', text: DASH });
        let extent = finite(o.extent);
        if (extent === null || extent <= 0) {
            extent = Math.max(...values.map((v) => Math.abs(v - ref))) * 1.15;
        }
        if (!(extent > 0)) extent = ref === 0 ? 1 : Math.abs(ref) || 1;
        const x = (v) => padX + ((Math.max(ref - extent, Math.min(ref + extent, v)) - (ref - extent))
            / (2 * extent)) * (width - 2 * padX);
        let tone = 'neutral';
        if (low !== null && high !== null && o.direction !== 'none') {
            const above = low > ref;
            const below = high < ref;
            if (above || below) {
                const good = o.direction === 'lower' ? below : above;
                tone = good ? 'good' : 'bad';
            }
        }
        const fmt = (v) => (o.formatter ? o.formatter(v) : fmtDiff(o.kind, v, o.unit, o.decimals));
        const text = o.text || (low !== null && high !== null
            ? `${fmt(est)} (95 %-Intervall ${fmt(low)} bis ${fmt(high)})`
            : fmt(est));
        const svg = svgEl('svg', {
            class: `kx-interval kx-interval--${tone}`, viewBox: `0 0 ${width} ${height}`,
            width, height, role: 'img', 'aria-label': text, focusable: 'false',
        }, svgEl('title', null, text));
        svg.appendChild(svgEl('line', {
            class: 'kx-interval-axis', x1: padX, x2: width - padX, y1: height / 2, y2: height / 2,
        }));
        svg.appendChild(svgEl('line', {
            class: 'kx-interval-ref', x1: x(ref), x2: x(ref), y1: 4, y2: height - 4,
        }));
        if (low !== null && high !== null) {
            svg.appendChild(svgEl('line', {
                class: 'kx-interval-range', x1: x(low), x2: x(high), y1: height / 2, y2: height / 2,
            }));
        }
        if (est !== null) {
            svg.appendChild(svgEl('circle', { class: 'kx-interval-point', cx: x(est), cy: height / 2, r: 4.5 }));
        }
        return svg;
    }

    /** Runde Achsenschritte: 1, 2, 2.5, 5 x 10^n. */
    function niceStep(span, target) {
        const raw = span / Math.max(1, target);
        const power = Math.pow(10, Math.floor(Math.log10(raw)));
        const norm = raw / power;
        const nice = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
        return nice * power;
    }

    /** Nachkommastellen, die ein Vielfaches von step exakt zeigen (0..6). */
    function decimalsForStep(step) {
        if (!(step > 0)) return 0;
        for (let d = 0; d <= 6; d += 1) {
            const scaled = step * Math.pow(10, d);
            if (Math.abs(scaled - Math.round(scaled)) < 1e-6 * Math.max(1, scaled)) return d;
        }
        return 6;
    }

    /**
     * Verlauf je Variante (store.timeseries). Eine Linie je Variante in fester
     * Farbreihenfolge (die Farbe folgt der Variante, nicht dem Rang);
     * Fadenkreuz mit Tooltip ueber alle Varianten, auch per Pfeiltasten;
     * <title> an jedem Punkt; Legende ab zwei Linien; Werte als Tabelle.
     *
     * opts: {kind, unit, decimals, bucket ('day'|'week'|'month'),
     *        variants: [{key, name}] (Reihenfolge = Farbe), label}
     */
    function lineChart(series, opts) {
        const o = opts || {};
        const rows = (Array.isArray(series) ? series : []).filter((r) => r && typeof r === 'object');
        const bucketTimes = [];
        const seen = new Set();
        for (const r of rows) {
            const d = parseDate(r.bucket_start);
            if (!d) continue;
            const t = d.getTime();
            if (!seen.has(t)) {
                seen.add(t);
                bucketTimes.push(t);
            }
        }
        bucketTimes.sort((a, b) => a - b);
        const variantOrder = (o.variants || []).map((v) => String(v.key));
        const names = {};
        (o.variants || []).forEach((v) => { names[String(v.key)] = v.name ? `${v.key} · ${v.name}` : String(v.key); });
        const groups = new Map();
        for (const r of rows) {
            const key = r.variant == null ? '' : String(r.variant);
            if (!groups.has(key)) groups.set(key, new Map());
            const d = parseDate(r.bucket_start);
            if (d) groups.get(key).set(d.getTime(), r);
        }
        const keys = Array.from(groups.keys()).sort((a, b) => {
            const ia = a === '' ? 1e6 : (variantOrder.indexOf(a) === -1 ? 1e5 : variantOrder.indexOf(a));
            const ib = b === '' ? 1e6 : (variantOrder.indexOf(b) === -1 ? 1e5 : variantOrder.indexOf(b));
            return ia - ib || a.localeCompare(b);
        });
        const slotOf = (key) => {
            const idx = variantOrder.indexOf(key);
            return idx === -1 ? null : idx;
        };
        let nextFree = variantOrder.length;
        const slots = {};
        keys.forEach((k) => {
            let s = k === '' ? null : slotOf(k);
            if (s === null) s = nextFree++;
            slots[k] = s < SERIES_SLOTS ? s + 1 : null;
        });
        const plotted = keys.filter((k) => slots[k] !== null);
        const omitted = keys.filter((k) => slots[k] === null);
        const values = [];
        plotted.forEach((k) => groups.get(k).forEach((r) => {
            const v = finite(r.estimate);
            if (v !== null) values.push(v);
        }));
        const wrap = el('div', { class: 'kx-chart' });
        if (!bucketTimes.length || !values.length) {
            wrap.appendChild(el('p', { class: 'kx-muted kx-chart-empty', text: 'Noch keine Verlaufsdaten.' }));
            return wrap;
        }
        const isMonth = o.bucket === 'month';
        const seriesName = (k) => (k === '' ? 'ohne Variante' : (names[k] || k));
        const fmtY = (v, d) => fmtEstimate(o.kind, v, o.unit, d);
        const width = 640;
        const height = 220;
        const m = { left: 64, right: 16, top: 14, bottom: 30 };
        const plotW = width - m.left - m.right;
        const plotH = height - m.top - m.bottom;
        let yMin = Math.min(...values);
        let yMax = Math.max(...values);
        if (yMin === yMax) {
            const bump = Math.abs(yMin) * 0.1 || (o.kind === 'proportion' ? 0.01 : 1);
            yMin -= bump;
            yMax += bump;
        }
        const step = niceStep(yMax - yMin, 4);
        yMin = Math.floor(yMin / step) * step;
        yMax = Math.ceil(yMax / step) * step;
        if (o.kind === 'proportion') yMin = Math.max(0, yMin);
        const tickDecimals = decimalsForStep(o.kind === 'proportion' ? step * 100 : step);
        const xOf = (i) => m.left + (bucketTimes.length === 1 ? plotW / 2 : (i / (bucketTimes.length - 1)) * plotW);
        const yOf = (v) => m.top + plotH - ((v - yMin) / (yMax - yMin || 1)) * plotH;
        const chartLabel = o.label || 'Verlauf je Variante';
        const svg = svgEl('svg', {
            class: 'kx-linechart', viewBox: `0 0 ${width} ${height}`, role: 'img',
            'aria-label': chartLabel, preserveAspectRatio: 'xMidYMid meet', focusable: 'false',
        }, svgEl('title', null, chartLabel));
        const grid = svgEl('g', { class: 'kx-chart-grid' });
        const tickCount = Math.min(12, Math.round((yMax - yMin) / step));
        for (let i = 0; i <= tickCount; i += 1) {
            const v = yMin + i * step;
            const y = yOf(v);
            grid.appendChild(svgEl('line', { x1: m.left, x2: width - m.right, y1: y, y2: y }));
            grid.appendChild(svgEl('text', {
                class: 'kx-chart-tick', x: m.left - 8, y: y + 4, 'text-anchor': 'end',
            }, fmtY(v, tickDecimals)));
        }
        svg.appendChild(grid);
        // Hoechstens sechs Datumsbeschriftungen; die letzte steht immer, und
        // eine davor, die ihr zu nahe kaeme, faellt weg statt zu ueberlappen.
        const labelEvery = Math.max(1, Math.ceil(bucketTimes.length / 6));
        const last = bucketTimes.length - 1;
        const labelled = [];
        for (let i = 0; i <= last; i += labelEvery) labelled.push(i);
        if (labelled[labelled.length - 1] !== last) {
            if (last - labelled[labelled.length - 1] < labelEvery / 2 && labelled.length > 1) labelled.pop();
            labelled.push(last);
        }
        const xAxis = svgEl('g', { class: 'kx-chart-xaxis' });
        labelled.forEach((i) => {
            xAxis.appendChild(svgEl('text', {
                class: 'kx-chart-tick', x: xOf(i), y: height - 8, 'text-anchor': 'middle',
            }, fmtDateUTC(new Date(bucketTimes[i]).toISOString(), isMonth)));
        });
        svg.appendChild(xAxis);
        const crosshair = svgEl('line', {
            class: 'kx-chart-crosshair', x1: 0, x2: 0, y1: m.top, y2: m.top + plotH, visibility: 'hidden',
        });
        svg.appendChild(crosshair);
        const pointText = (k, t, r) => `${seriesName(k)} · ${fmtDateUTC(new Date(t).toISOString(), isMonth)}: `
            + `${fmtY(r.estimate, o.decimals)} (n = ${fmtNumber(r.n, 0)})`;
        plotted.forEach((k) => {
            const cls = `kx-s${slots[k]}`;
            const byTime = groups.get(k);
            let d = '';
            let open = false;
            bucketTimes.forEach((t, i) => {
                const r = byTime.get(t);
                const v = r ? finite(r.estimate) : null;
                if (v === null) {
                    open = false;
                    return;
                }
                d += `${open ? 'L' : 'M'}${xOf(i).toFixed(1)} ${yOf(v).toFixed(1)} `;
                open = true;
            });
            if (d) svg.appendChild(svgEl('path', { class: `kx-line ${cls}`, d: d.trim() }));
            bucketTimes.forEach((t, i) => {
                const r = byTime.get(t);
                const v = r ? finite(r.estimate) : null;
                if (v === null) return;
                svg.appendChild(svgEl('circle', { class: `kx-marker ${cls}`, cx: xOf(i), cy: yOf(v), r: 4 }));
                svg.appendChild(svgEl('circle', {
                    class: 'kx-hit', cx: xOf(i), cy: yOf(v), r: 12,
                }, svgEl('title', null, pointText(k, t, r))));
            });
        });
        const tooltip = el('div', { class: 'kx-tooltip', hidden: true, 'aria-hidden': 'true' });
        const live = el('p', { class: 'kx-visually-hidden', 'aria-live': 'polite' });
        const frame = el('div', {
            class: 'kx-chart-frame', tabindex: '0',
            'aria-label': `${chartLabel}. Pfeiltasten links und rechts zeigen die Werte je Zeitraum.`,
        }, svg, tooltip);
        let active = -1;
        function show(i) {
            if (i < 0 || i >= bucketTimes.length) return;
            active = i;
            const t = bucketTimes[i];
            const cx = xOf(i);
            crosshair.setAttribute('x1', cx);
            crosshair.setAttribute('x2', cx);
            crosshair.setAttribute('visibility', 'visible');
            clear(tooltip);
            tooltip.appendChild(el('p', { class: 'kx-tooltip-title', text: fmtDateUTC(new Date(t).toISOString(), isMonth) }));
            const lines = [];
            plotted.forEach((k) => {
                const r = groups.get(k).get(t);
                if (!r || finite(r.estimate) === null) return;
                const value = fmtY(r.estimate, o.decimals);
                lines.push(`${seriesName(k)}: ${value}`);
                tooltip.appendChild(el('p', { class: 'kx-tooltip-row' },
                    el('span', { class: `kx-swatch kx-s${slots[k]}`, 'aria-hidden': 'true' }),
                    el('strong', { text: value }),
                    el('span', { class: 'kx-tooltip-name', text: ` ${seriesName(k)} · n = ${fmtNumber(r.n, 0)}` })));
            });
            if (!lines.length) tooltip.appendChild(el('p', { class: 'kx-tooltip-row', text: 'Keine Werte.' }));
            tooltip.hidden = false;
            const leftPct = (cx / width) * 100;
            tooltip.style.left = `${leftPct}%`;
            tooltip.classList.toggle('kx-tooltip--flip', leftPct > 60);
            live.textContent = `${fmtDateUTC(new Date(t).toISOString(), isMonth)}: ${lines.join('; ')}`;
        }
        function hide() {
            active = -1;
            crosshair.setAttribute('visibility', 'hidden');
            tooltip.hidden = true;
        }
        frame.addEventListener('pointermove', (e) => {
            const rect = svg.getBoundingClientRect();
            if (!rect.width) return;
            const px = ((e.clientX - rect.left) / rect.width) * width;
            let best = 0;
            let bestDist = Infinity;
            bucketTimes.forEach((t, i) => {
                const dist = Math.abs(xOf(i) - px);
                if (dist < bestDist) {
                    best = i;
                    bestDist = dist;
                }
            });
            show(best);
        });
        frame.addEventListener('pointerleave', hide);
        frame.addEventListener('blur', hide);
        frame.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
                e.preventDefault();
                const delta = e.key === 'ArrowRight' ? 1 : -1;
                show(active === -1 ? (delta > 0 ? 0 : bucketTimes.length - 1)
                    : Math.max(0, Math.min(bucketTimes.length - 1, active + delta)));
            } else if (e.key === 'Home' || e.key === 'End') {
                e.preventDefault();
                show(e.key === 'Home' ? 0 : bucketTimes.length - 1);
            } else if (e.key === 'Escape') {
                hide();
            }
        });
        wrap.appendChild(frame);
        wrap.appendChild(live);
        if (plotted.length >= 2) {
            wrap.appendChild(el('ul', { class: 'kx-legend', 'aria-label': 'Legende' },
                plotted.map((k) => el('li', null,
                    el('span', { class: `kx-swatch kx-s${slots[k]}`, 'aria-hidden': 'true' }),
                    seriesName(k)))));
        }
        if (omitted.length) {
            wrap.appendChild(el('p', {
                class: 'kx-help',
                text: `Nicht als Linie gezeigt: ${omitted.map(seriesName).join(', ')} (siehe Tabelle).`,
            }));
        }
        const table = el('table', { class: 'kx-table kx-table--compact' },
            el('thead', null, el('tr', null,
                el('th', { scope: 'col', text: 'Zeitraum' }),
                keys.map((k) => el('th', { scope: 'col', text: seriesName(k) })))),
            el('tbody', null, bucketTimes.map((t) => el('tr', null,
                el('th', { scope: 'row', text: fmtDateUTC(new Date(t).toISOString(), isMonth) }),
                keys.map((k) => {
                    const r = groups.get(k).get(t);
                    return el('td', {
                        text: r ? `${fmtY(r.estimate, o.decimals)} (n = ${fmtNumber(r.n, 0)})` : DASH,
                    });
                })))));
        wrap.appendChild(el('details', { class: 'kx-chart-table' },
            el('summary', { text: 'Werte als Tabelle' }),
            el('div', { class: 'kx-table-wrap' }, table)));
        return wrap;
    }

    window.KX = {
        KEY_RE, DASH, LABELS, ApiError,
        csrfToken, api, upload, meta, pageData, errorMessage,
        esc, el, svgEl, clear, uid, debounce, safeUrl,
        parseDate, fmtDate, fmtDateTime, fmtDateUTC, todayIso,
        fmtNumber, fmtPlain, fmtEstimate, fmtDiff, fmtPercent, fmtPValue, parseNumber, finite,
        isKey, experimentUrl, safeAppUrl,
        toast, dialog, confirm, field, input, textarea, select,
        showFieldErrors, clearFieldErrors, renderFieldInput, readFieldInput,
        label, chip, verdictChip, decisionChip, statusChip, domainDot, emptyState, spinnerText,
        renderMarkdown, copyText, downloadText, codeEditor,
        intervalBar, lineChart,
    };
})();
