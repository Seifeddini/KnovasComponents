// Experimente -- Verwaltung (/experiments/verwaltung). Vertrag: Plan §10, §14.
//
// Reiter fuer Verantwortliche (Bereiche, Typen, Metriken, Auswerter, Index)
// zeichnet das Template nur fuer sie; jede Aenderung prueft der Server
// ohnehin (403). Zugangsschluessel hat jede Person mit Experimente-Rolle.
// Servertexte gehen ueber KX.el/textContent in die Seite; Code, Definitionen
// und Protokolle stehen in <textarea>/<pre> als Text.
(function () {
    'use strict';

    const KX = window.KX;
    const { el, clear } = KX;
    const DASH = KX.DASH;
    const page = KX.pageData();
    const canManage = Boolean(page.canManage);

    const DOMAIN_KEY_RE = /^[a-z][a-z0-9-]{1,31}$/;
    const PREFIX_RE = /^[A-Z][A-Z0-9]{1,7}$/;
    const COLOR_RE = /^#[0-9A-Fa-f]{6}$/;
    const METRIC_KEY_RE = /^[a-z][a-z0-9_]{1,47}$/;
    const TYPE_KEY_RE = /^[a-z][a-z0-9_-]{1,47}$/;
    const EVALUATOR_KEY_RE = /^[a-z][a-z0-9_.-]{1,63}$/;

    const loaded = new Set();
    const state = { domains: [], meta: null };

    function panel(name) {
        return document.getElementById(`kxPanel-${name}`);
    }

    function sectionHead(title, hint, actions) {
        return el('div', { class: 'kx-section-head' },
            el('div', null, el('h2', { text: title }), hint ? el('p', { class: 'kx-hint', text: hint }) : null),
            actions && actions.length ? el('div', { class: 'kx-section-actions' }, actions) : null);
    }

    function button(text, onClick, variant) {
        return el('button', {
            type: 'button', class: ['btn', 'btn-sm', variant === 'primary' ? 'btn-primary' : 'btn-outline',
                variant === 'danger' ? 'btn-danger' : ''],
            text, onClick,
        });
    }

    function tableWrap(headers, rows) {
        return el('div', { class: 'kx-table-wrap' }, el('table', { class: 'kx-table' },
            el('thead', null, el('tr', null, headers.map((h) => (typeof h === 'string'
                ? el('th', { scope: 'col', text: h })
                : el('th', Object.assign({ scope: 'col' }, h.attrs || {}), h.node || h.text))))),
            el('tbody', null, rows)));
    }

    function failure(box, err, retry) {
        clear(box).appendChild(el('div', { class: 'kx-banner kx-banner--error', role: 'alert' },
            el('p', { text: KX.errorMessage(err) }),
            retry ? button('Erneut versuchen', retry) : null));
    }

    function personName(p) {
        if (!p) return null;
        return typeof p === 'string' ? p : (p.display_name ? String(p.display_name) : null);
    }

    function parseJsonObject(text, fieldName) {
        const raw = String(text || '').trim();
        if (!raw) return {};
        let parsed;
        try {
            parsed = JSON.parse(raw);
        } catch (_) {
            parsed = null;
        }
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
            throw new KX.ApiError('Bitte ein gültiges JSON-Objekt angeben.', 400,
                { [fieldName]: 'Kein gültiges JSON-Objekt.' });
        }
        return parsed;
    }

    async function ensureMeta() {
        if (!state.meta) {
            try {
                state.meta = await KX.meta();
            } catch (_) {
                state.meta = {};
            }
        }
        return state.meta;
    }

    async function loadDomains() {
        const data = await KX.api('GET', '/api/experiments/domains?archived=1');
        state.domains = Array.isArray(data.domains) ? data.domains : [];
        return state.domains;
    }

    function domainOptions(withGlobal, globalLabel) {
        const opts = withGlobal ? [{ value: '', label: globalLabel || 'Alle Bereiche (global)' }] : [];
        return opts.concat(state.domains.filter((d) => !d.archived).map((d) => ({ value: d.key, label: d.name })));
    }

    function domainName(key) {
        if (!key) return 'alle Bereiche';
        const d = state.domains.find((x) => x.key === key);
        return d ? d.name : key;
    }

    /** Aus einem Namen einen Schluessel-Vorschlag machen (ASCII, Bindestriche). */
    function suggestKey(name, sep, maxLen) {
        return String(name || '').toLowerCase()
            .replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue').replace(/ß/g, 'ss')
            .normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
            .replace(/[^a-z0-9]+/g, sep).replace(new RegExp(`^[^a-z]+|\\${sep}+$`, 'g'), '')
            .slice(0, maxLen);
    }

    // ── Reiter ──────────────────────────────────────────────────────────

    const LOADERS = {
        bereiche: renderDomainsTab,
        typen: renderTypesTab,
        metriken: renderMetricsTab,
        auswerter: renderEvaluatorsTab,
        zugangsschluessel: renderTokensTab,
        index: renderIndexTab,
    };

    function tabs() {
        return Array.from(document.querySelectorAll('#kxTabs [role=tab]'));
    }

    function selectTab(name, focus) {
        const all = tabs();
        const target = all.find((t) => t.dataset.tab === name) || all[0];
        if (!target) return;
        all.forEach((t) => {
            const on = t === target;
            t.setAttribute('aria-selected', on ? 'true' : 'false');
            t.tabIndex = on ? 0 : -1;
            const p = document.getElementById(t.getAttribute('aria-controls'));
            if (p) p.hidden = !on;
        });
        if (focus) target.focus();
        const tab = target.dataset.tab;
        try {
            window.history.replaceState(null, '', `#${tab}`);
        } catch (_) { /* nur Komfort */ }
        if (!loaded.has(tab) && LOADERS[tab]) {
            loaded.add(tab);
            LOADERS[tab]();
        }
    }

    function bindTabs() {
        const all = tabs();
        all.forEach((t, i) => {
            t.addEventListener('click', () => selectTab(t.dataset.tab, false));
            t.addEventListener('keydown', (e) => {
                let next = null;
                if (e.key === 'ArrowRight') next = all[(i + 1) % all.length];
                else if (e.key === 'ArrowLeft') next = all[(i - 1 + all.length) % all.length];
                else if (e.key === 'Home') next = all[0];
                else if (e.key === 'End') next = all[all.length - 1];
                if (next) {
                    e.preventDefault();
                    selectTab(next.dataset.tab, true);
                }
            });
        });
        window.addEventListener('hashchange', () => {
            const name = window.location.hash.replace(/^#/, '');
            if (all.some((t) => t.dataset.tab === name)) selectTab(name, false);
        });
    }

    // ── Bereiche ────────────────────────────────────────────────────────

    async function renderDomainsTab() {
        const box = panel('bereiche');
        clear(box).appendChild(KX.spinnerText());
        try {
            await loadDomains();
        } catch (err) {
            failure(box, err, renderDomainsTab);
            return;
        }
        clear(box);
        box.appendChild(sectionHead('Bereiche',
            'Ein Bereich bündelt Experimente, Typen und Metriken eines Arbeitsgebiets. Sein Kürzel bildet die Schlüssel der Experimente (z. B. MKT-12).',
            [button('Neuer Bereich', () => openDomainDialog(null), 'primary'), button('Paket importieren', openImportDialog)]));
        if (!state.domains.length) {
            box.appendChild(KX.emptyState('Noch keine Bereiche.', 'Ein Paket installieren oder einen eigenen Bereich anlegen.'));
        } else {
            box.appendChild(tableWrap(['Bereich', 'Schlüssel', 'Kürzel', { text: 'Experimente', attrs: { class: 'kx-num' } }, 'Paket', 'Zustand',
                { node: el('span', { class: 'kx-visually-hidden', text: 'Aktionen' }) }],
            state.domains.map((d) => el('tr', null,
                el('th', { scope: 'row' },
                    el('span', { class: 'kx-domain' }, KX.domainDot(d.color), el('strong', { text: d.name })),
                    d.description ? el('span', { class: 'kx-sub', text: d.description }) : null),
                el('td', { class: 'kx-mono', text: d.key }),
                el('td', { class: 'kx-mono', text: d.id_prefix }),
                el('td', { class: 'kx-num' }, KX.fmtNumber(Number(d.experiment_count) || 0, 0),
                    Number(d.running_count) ? el('span', { class: 'kx-sub', text: `${KX.fmtNumber(Number(d.running_count), 0)} laufend` }) : null),
                el('td', { class: 'kx-mono', text: d.pack || DASH }),
                el('td', null, d.archived ? KX.chip('archiviert', 'muted') : KX.chip('aktiv', 'good')),
                el('td', { class: 'kx-row-actions' },
                    button('Bearbeiten', () => openDomainDialog(d)), ' ',
                    button('Exportieren', (e) => exportDomain(d, e.currentTarget)))))));
        }
        const packsBox = el('div', { style: { 'margin-top': '28px' } });
        box.appendChild(packsBox);
        renderPacks(packsBox);
    }

    async function renderPacks(box) {
        clear(box).appendChild(sectionHead('Pakete',
            'Mitgelieferte Sammlungen aus Bereich, Typen, Metriken und Auswertern. Die Installation ergänzt nur, was fehlt.'));
        const list = el('div', { class: 'kx-pack-list' }, KX.spinnerText());
        box.appendChild(list);
        let packs;
        try {
            const data = await KX.api('GET', '/api/experiments/packs');
            packs = Array.isArray(data.packs) ? data.packs : [];
        } catch (err) {
            failure(list, err, () => renderPacks(box));
            return;
        }
        clear(list);
        packs.forEach((p) => {
            const install = el('button', {
                type: 'button', class: 'btn btn-outline btn-sm',
                text: p.installed ? 'Fehlendes ergänzen' : 'Installieren',
            });
            install.addEventListener('click', async () => {
                install.disabled = true;
                try {
                    const data = await KX.api('POST', `/api/experiments/packs/${encodeURIComponent(p.name)}/install`, {});
                    KX.toast(`Paket «${p.title || p.name}»: ${countsText(data.result)}.`, 'success');
                    loaded.delete('typen');
                    loaded.delete('metriken');
                    loaded.delete('auswerter');
                    renderDomainsTab();
                } catch (err) {
                    install.disabled = false;
                    KX.toast(KX.errorMessage(err), 'error');
                }
            });
            list.appendChild(el('div', { class: 'kx-pack' },
                el('strong', null, p.title || p.name, ' ', p.installed ? KX.chip('installiert', 'good') : null),
                el('p', { text: p.description || '' }),
                el('span', { class: 'kx-help', text: `Paket ${p.name}${p.version ? ` · Version ${p.version}` : ''}` }),
                install));
        });
    }

    function countsText(result) {
        const r = result && typeof result === 'object' ? result : {};
        const parts = [];
        // Nur, was wirklich neu ist: "0 Bereiche, 0 Typen, ..." sagte nichts,
        // und ohne Neues heisst es "nichts zu ergaenzen".
        const add = (n, one, many) => {
            const v = Number(n);
            if (Number.isFinite(v) && v > 0) parts.push(`${KX.fmtNumber(v, 0)} ${v === 1 ? one : many}`);
        };
        add(r.domain !== undefined ? (typeof r.domain === 'number' ? r.domain : (r.domain ? 1 : 0)) : undefined, 'Bereich', 'Bereiche');
        add(r.types, 'Typ', 'Typen');
        add(r.metrics, 'Metrik', 'Metriken');
        add(r.evaluators, 'Auswerter', 'Auswerter');
        return parts.length ? `${parts.join(', ')} neu` : 'nichts zu ergänzen';
    }

    async function openDomainDialog(domain) {
        const editing = Boolean(domain);
        const name = KX.input({ name: 'name', maxlength: 80, value: editing ? domain.name : '' });
        const key = KX.input({ name: 'key', maxlength: 32, class: 'kx-input kx-mono', value: editing ? domain.key : '', readOnly: editing });
        const prefix = KX.input({ name: 'id_prefix', maxlength: 8, class: 'kx-input kx-mono', value: editing ? domain.id_prefix : '', readOnly: editing });
        const colorValue = editing && COLOR_RE.test(domain.color || '') ? domain.color : '#5A6B80';
        const color = el('input', { type: 'color', name: 'color', value: colorValue, class: 'kx-input', style: { width: '64px', padding: '2px' } });
        const description = KX.textarea({ name: 'description', rows: 3, maxlength: 2000 }, editing ? domain.description || '' : '');
        const archived = el('input', { type: 'checkbox', name: 'archived' });
        archived.checked = Boolean(editing && domain.archived);
        let keyTouched = editing;
        let prefixTouched = editing;
        if (!editing) {
            key.addEventListener('input', () => { keyTouched = true; });
            prefix.addEventListener('input', () => { prefixTouched = true; });
            name.addEventListener('input', () => {
                if (!keyTouched) key.value = suggestKey(name.value, '-', 32);
                if (!prefixTouched) {
                    prefix.value = suggestKey(name.value, '', 8).toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 3);
                }
            });
        }
        const body = el('div', null,
            editing ? null : el('div', { class: 'kx-banner kx-banner--info' },
                el('p', { text: 'Neue Bereiche starten mit dem Typ «Allgemeine Hypothese». '
                    + 'Legen Sie danach unter «Metriken» die Metriken des Bereichs an – ohne sie gibt es '
                    + 'keine Messwerte.' })),
            KX.field({ label: 'Name', input: name, name: 'name', required: true }),
            el('div', { class: 'kx-form-row', style: { 'margin-top': '14px' } },
                KX.field({ label: 'Schlüssel', input: key, name: 'key', required: !editing, help: editing ? 'Lässt sich nicht ändern.' : 'Kleinbuchstaben, Ziffern, Bindestrich.' }),
                KX.field({ label: 'Kürzel', input: prefix, name: 'id_prefix', required: !editing, help: editing ? 'Lässt sich nicht ändern.' : 'Für Schlüssel wie MKT-12: 2–8 Grossbuchstaben oder Ziffern.' }),
                KX.field({ label: 'Farbe', input: color, name: 'color', help: 'Nur als Punkt neben dem Namen.' })),
            KX.field({ label: 'Beschreibung', input: description, name: 'description' }),
            editing ? el('div', { class: 'kx-field' }, el('label', { class: 'kx-check' }, archived, el('span', { text: 'Archiviert (keine neuen Experimente)' }))) : null);
        await KX.dialog({
            title: editing ? `Bereich «${domain.name}» bearbeiten` : 'Neuer Bereich',
            body,
            actions: [
                { label: 'Abbrechen', value: null },
                {
                    label: editing ? 'Speichern' : 'Anlegen',
                    primary: true,
                    onClick: async () => {
                        const errors = {};
                        if (!name.value.trim()) errors.name = 'Bitte einen Namen angeben.';
                        if (!editing && !DOMAIN_KEY_RE.test(key.value.trim())) errors.key = '2–32 Zeichen: Kleinbuchstaben, Ziffern, Bindestrich; zuerst ein Buchstabe.';
                        if (!editing && !PREFIX_RE.test(prefix.value.trim())) errors.id_prefix = '2–8 Zeichen: Grossbuchstaben und Ziffern, zuerst ein Buchstabe.';
                        if (!COLOR_RE.test(color.value)) errors.color = 'Bitte eine Farbe wählen.';
                        if (Object.keys(errors).length) throw new KX.ApiError('Bitte die markierten Angaben prüfen.', 400, errors);
                        if (editing) {
                            const changes = {};
                            if (name.value.trim() !== domain.name) changes.name = name.value.trim();
                            if (color.value.toLowerCase() !== String(domain.color || '').toLowerCase()) changes.color = color.value;
                            if (description.value.trim() !== (domain.description || '')) changes.description = description.value.trim();
                            if (archived.checked !== Boolean(domain.archived)) changes.archived = archived.checked;
                            if (!Object.keys(changes).length) return true;
                            await KX.api('PATCH', `/api/experiments/domains/${encodeURIComponent(domain.key)}`, changes);
                            KX.toast('Bereich gespeichert.', 'success');
                        } else {
                            await KX.api('POST', '/api/experiments/domains', {
                                key: key.value.trim(), name: name.value.trim(), id_prefix: prefix.value.trim(),
                                color: color.value, description: description.value.trim(),
                            });
                            KX.toast('Bereich angelegt.', 'success');
                        }
                        loaded.delete('typen');
                        loaded.delete('metriken');
                        renderDomainsTab();
                        return true;
                    },
                },
            ],
        });
    }

    async function exportDomain(domain, trigger) {
        trigger.disabled = true;
        try {
            const data = await KX.api('GET', `/api/experiments/domains/${encodeURIComponent(domain.key)}/export`);
            KX.downloadText(`${domain.key}.yaml`, data.text || '', 'application/x-yaml;charset=utf-8');
        } catch (err) {
            KX.toast(KX.errorMessage(err), 'error');
        } finally {
            trigger.disabled = false;
        }
    }

    async function openImportDialog() {
        const text = KX.textarea({ name: 'text', rows: 14, class: 'kx-input kx-textarea kx-code' });
        KX.codeEditor(text);
        const file = el('input', { type: 'file', accept: '.yaml,.yml,.json,text/yaml,application/json,text/plain', class: 'kx-input' });
        file.addEventListener('change', () => {
            const chosen = file.files && file.files[0];
            if (!chosen) return;
            if (chosen.size > 2 * 1024 * 1024) {
                KX.toast('Das Paket ist grösser als 2 MB.', 'error');
                return;
            }
            const reader = new FileReader();
            reader.onload = () => { text.value = String(reader.result || ''); };
            reader.readAsText(chosen, 'utf-8');
        });
        await KX.dialog({
            title: 'Paket importieren',
            description: 'Ein exportierter Bereich oder ein eigenes Paket (YAML oder JSON, höchstens 2 MB). Vorhandenes bleibt, Neues wird ergänzt.',
            wide: true,
            body: el('div', null,
                KX.field({ label: 'Datei laden (optional)', input: file, name: 'file' }),
                KX.field({ label: 'Inhalt', input: text, name: 'text', required: true })),
            actions: [
                { label: 'Abbrechen', value: null },
                {
                    label: 'Importieren',
                    primary: true,
                    onClick: async () => {
                        if (!text.value.trim()) throw new KX.ApiError('Das Paket ist leer.', 400, { text: 'Bitte ein Paket einfügen oder laden.' });
                        const data = await KX.api('POST', '/api/experiments/packs/import', { text: text.value });
                        KX.toast(`Import: ${countsText(data.result)}.`, 'success');
                        loaded.delete('typen');
                        loaded.delete('metriken');
                        loaded.delete('auswerter');
                        renderDomainsTab();
                        return true;
                    },
                },
            ],
        });
    }

    // ── Typen ───────────────────────────────────────────────────────────

    const TYPE_TEMPLATE = [
        '# Typ-Definition (YAML oder JSON). "Prüfen" zeigt Fehler je Pfad.',
        'fields: []',
        'states:',
        '  - {key: draft, label: Entwurf}',
        '  - {key: running, label: Läuft, phase: running}',
        '  - {key: analysis, label: Auswertung}',
        '  - {key: decided, label: Entschieden, phase: decided}',
        '  - {key: stopped, label: Abgebrochen, phase: stopped}',
        'initial: draft',
        'transitions:',
        '  - {from: draft, to: running, label: Starten, requires: [hypothesis, primary_metric, "variants:2"]}',
        '  - {from: running, to: analysis, label: Zur Auswertung, requires: [measurements]}',
        '  - {from: analysis, to: decided, label: Entscheiden, requires: [decision]}',
        '  - {from: analysis, to: running, label: Weiterlaufen lassen}',
        '  - {from: "*", to: stopped, label: Abbrechen}',
        'variants:',
        '  min: 2',
        '  max: 10',
        '  defaults:',
        '    - {key: A, name: Kontrolle, is_control: true}',
        '    - {key: B, name: Variante B}',
        'metrics: []',
        'evaluation:',
        '  - {evaluator: builtin.describe, metric: all, params: {}}',
        'decision: {require_learning: true}',
        '',
    ].join('\n');

    const typesState = { filter: '__all__', archived: false, items: [], selected: null };

    async function renderTypesTab() {
        const box = panel('typen');
        clear(box).appendChild(KX.spinnerText());
        try {
            if (!state.domains.length) await loadDomains();
        } catch (err) {
            failure(box, err, renderTypesTab);
            return;
        }
        const filter = KX.select({ 'aria-label': 'Bereich' }, [{ value: '__all__', label: 'Alle Bereiche' },
            { value: '', label: 'Nur bereichsübergreifende' }].concat(domainOptions(false)), typesState.filter);
        const archived = el('input', { type: 'checkbox' });
        archived.checked = typesState.archived;
        const listBox = el('div');
        const editorBox = el('div');
        filter.addEventListener('change', () => {
            typesState.filter = filter.value;
            loadTypes(listBox, editorBox);
        });
        archived.addEventListener('change', () => {
            typesState.archived = archived.checked;
            loadTypes(listBox, editorBox);
        });
        clear(box);
        box.appendChild(sectionHead('Typen',
            'Ein Typ legt Angaben, Status, Übergänge, Varianten, Metriken und Auswertungen fest. Jede Änderung wird eine neue Version; laufende Experimente behalten ihre.',
            [button('Neuer Typ', openNewTypeDialog, 'primary')]));
        box.appendChild(el('div', { class: 'kx-toolbar' },
            el('label', { class: 'kx-visually-hidden', text: 'Bereich' }), filter,
            el('label', { class: 'kx-check' }, archived, el('span', { text: 'Archivierte zeigen' }))));
        box.appendChild(el('div', { class: 'kx-split' }, listBox, editorBox));
        loadTypes(listBox, editorBox);
    }

    async function loadTypes(listBox, editorBox) {
        clear(listBox).appendChild(KX.spinnerText());
        const params = new URLSearchParams();
        if (typesState.filter !== '__all__' && typesState.filter) params.set('domain', typesState.filter);
        params.set('archived', typesState.archived ? '1' : '0');
        try {
            const data = await KX.api('GET', `/api/experiments/types?${params.toString()}`);
            let items = Array.isArray(data.types) ? data.types : [];
            if (typesState.filter === '') items = items.filter((t) => !t.domain_key);
            typesState.items = items;
        } catch (err) {
            failure(listBox, err, () => loadTypes(listBox, editorBox));
            return;
        }
        renderTypeList(listBox, editorBox);
        const keep = typesState.items.find((t) => t.id === typesState.selected);
        if (keep) openTypeEditor(keep.id, editorBox, listBox);
        else clear(editorBox).appendChild(KX.emptyState(null, 'Einen Typ links auswählen.'));
    }

    function renderTypeList(listBox, editorBox) {
        clear(listBox);
        if (!typesState.items.length) {
            listBox.appendChild(KX.emptyState(null, 'Keine Typen für diese Auswahl.'));
            return;
        }
        listBox.appendChild(el('ul', { class: 'kx-list', 'aria-label': 'Typen' }, typesState.items.map((t) => el('li', null,
            el('button', {
                type: 'button', class: 'kx-list-item', 'aria-current': t.id === typesState.selected ? 'true' : 'false',
                onClick: () => {
                    typesState.selected = t.id;
                    renderTypeList(listBox, editorBox);
                    openTypeEditor(t.id, editorBox, listBox);
                },
            },
            el('span', null, el('strong', { text: t.name }), t.archived ? ' ' : null, t.archived ? KX.chip('archiviert', 'muted') : null),
            el('span', { class: 'kx-sub', text: `${domainName(t.domain_key)} · Version ${t.current_version} · ${experimentCount(t.experiment_count)}` }))))));
    }

    function experimentCount(n) {
        const v = Number(n) || 0;
        return `${KX.fmtNumber(v, 0)} ${v === 1 ? 'Experiment' : 'Experimente'}`;
    }

    function definitionPreview(def) {
        const d = def && typeof def === 'object' ? def : {};
        const box = el('div');
        const fields = Array.isArray(d.fields) ? d.fields : [];
        box.appendChild(el('p', { class: 'kx-subhead', text: 'Angaben' }));
        box.appendChild(fields.length ? el('ul', { class: 'kx-preview-list' }, fields.map((f) => el('li', null,
            el('strong', { text: f.label || f.key }), ` (${f.key}, ${f.type}${f.required ? ', Pflicht' : ''})`,
            Array.isArray(f.options) && f.options.length ? `: ${f.options.join(', ')}` : '')))
            : el('p', { class: 'kx-muted', text: 'Keine.' }));
        const states = Array.isArray(d.states) ? d.states : [];
        box.appendChild(el('p', { class: 'kx-subhead', text: 'Status' }));
        box.appendChild(el('ol', { class: 'kx-steps' }, states.map((s) => el('li', {
            class: ['kx-step', s.key === d.initial ? 'kx-step--initial' : ''],
        }, el('span', { class: 'kx-step-label', text: `${s.label || s.key}${s.phase ? ` · Phase ${KX.label('phases', s.phase)}` : ''}` }),
        s.key === d.initial ? el('span', { class: 'kx-visually-hidden', text: '(Anfangsstatus)' }) : null))));
        const transitions = Array.isArray(d.transitions) ? d.transitions : [];
        box.appendChild(el('p', { class: 'kx-subhead', text: 'Übergänge' }));
        box.appendChild(transitions.length ? el('ul', { class: 'kx-preview-list' }, transitions.map((t) => el('li', null,
            el('strong', { text: t.label || `${t.from} → ${t.to}` }), ` ${t.from === '*' ? 'jeder Status' : t.from} → ${t.to}`,
            Array.isArray(t.requires) && t.requires.length ? ` · braucht ${t.requires.join(', ')}` : '',
            Array.isArray(t.roles) && t.roles.length ? ` · nur ${t.roles.join(', ')}` : '')))
            : el('p', { class: 'kx-muted', text: 'Keine.' }));
        const metrics = Array.isArray(d.metrics) ? d.metrics : [];
        if (metrics.length) {
            box.appendChild(el('p', { class: 'kx-subhead', text: 'Metriken' }));
            box.appendChild(el('ul', { class: 'kx-preview-list' }, metrics.map((m) => el('li', {
                text: `${m.metric} (${KX.label('metric_roles', m.role)}${m.role === 'guardrail' ? `, ${m.op === 'max' ? 'höchstens' : 'mindestens'} ${KX.fmtPlain(m.value)}` : ''})`,
            }))));
        }
        const evaluation = Array.isArray(d.evaluation) ? d.evaluation : [];
        if (evaluation.length) {
            box.appendChild(el('p', { class: 'kx-subhead', text: 'Auswertungen' }));
            box.appendChild(el('ul', { class: 'kx-preview-list' }, evaluation.map((e) => el('li', {
                text: `${e.evaluator} auf ${e.metric === 'all' ? 'alle Metriken' : e.metric === 'primary' ? 'die primäre Metrik' : e.metric}`,
            }))));
        }
        return box;
    }

    async function openTypeEditor(typeId, box, listBox) {
        clear(box).appendChild(KX.spinnerText());
        let type;
        try {
            const data = await KX.api('GET', `/api/experiments/types/${encodeURIComponent(typeId)}`);
            type = data.type;
        } catch (err) {
            failure(box, err, () => openTypeEditor(typeId, box, listBox));
            return;
        }
        const name = KX.input({ name: 'name', maxlength: 80, value: type.name });
        const description = KX.textarea({ name: 'description', rows: 2, maxlength: 2000 }, type.description || '');
        const text = KX.textarea({ name: 'definition_text', rows: 22, class: 'kx-input kx-textarea kx-code', 'aria-label': 'Definition' },
            JSON.stringify(type.definition || {}, null, 2));
        KX.codeEditor(text);
        const result = el('div', { 'aria-live': 'polite' });
        const preview = el('div', null, definitionPreview(type.definition));
        const showErrors = (err) => {
            const fields = err.fields || {};
            const keys = Object.keys(fields);
            // Die Meldung des Servers wiederholt den ersten Feldfehler
            // (schema.raise_invalid); steht die Liste darunter, genuegt ein Titel.
            const aboutDefinition = keys.every((k) => k !== 'name' && k !== 'description');
            const title = !keys.length ? KX.errorMessage(err)
                : `${aboutDefinition ? 'Die Definition ist ungültig' : 'Bitte die Angaben prüfen'} `
                    + `(${KX.fmtNumber(keys.length, 0)} Fehler).`;
            clear(result).appendChild(el('div', { class: 'kx-banner kx-banner--error', role: 'alert' },
                el('div', null, el('p', { text: title }),
                    Object.keys(fields).length ? el('ul', null, Object.keys(fields).slice(0, 50).map((k) => el('li', null,
                        el('span', { class: 'kx-mono', text: k }), `: ${fields[k]}`))) : null)));
        };
        const check = button('Prüfen', async (e) => {
            // currentTarget ist nach dem ersten await null -- vorher festhalten.
            const trigger = e.currentTarget;
            trigger.disabled = true;
            try {
                const data = await KX.api('POST', '/api/experiments/types/validate', {
                    definition_text: text.value, domain: type.domain_key || null,
                });
                clear(result).appendChild(el('div', { class: 'kx-banner kx-banner--ok', role: 'status' },
                    el('p', { text: 'Die Definition ist gültig.' })));
                clear(preview).appendChild(definitionPreview(data.definition));
            } catch (err) {
                showErrors(err);
            } finally {
                trigger.disabled = false;
            }
        });
        const save = button('Als neue Version speichern', async (e) => {
            const trigger = e.currentTarget;
            trigger.disabled = true;
            try {
                const payload = { definition_text: text.value };
                if (name.value.trim() && name.value.trim() !== type.name) payload.name = name.value.trim();
                if (description.value.trim() !== (type.description || '')) payload.description = description.value.trim();
                const data = await KX.api('POST', `/api/experiments/types/${encodeURIComponent(type.id)}/versions`, payload);
                const saved = data.type || {};
                KX.toast(saved.current_version && saved.current_version !== type.current_version
                    ? `Version ${saved.current_version} gespeichert.` : 'Gespeichert. Die Definition ist unverändert, es gibt keine neue Version.', 'success');
                typesState.selected = type.id;
                loadTypes(listBox, box);
            } catch (err) {
                trigger.disabled = false;
                showErrors(err);
            }
        }, 'primary');
        const archiveToggle = button(type.archived ? 'Wiederherstellen' : 'Archivieren', async (e) => {
            const trigger = e.currentTarget;
            trigger.disabled = true;
            try {
                await KX.api('POST', `/api/experiments/types/${encodeURIComponent(type.id)}/archive`, { archived: !type.archived });
                KX.toast(type.archived ? 'Typ wiederhergestellt.' : 'Typ archiviert. Laufende Experimente bleiben unverändert.', 'success');
                loadTypes(listBox, box);
            } catch (err) {
                trigger.disabled = false;
                KX.toast(KX.errorMessage(err), 'error');
            }
        });
        const copy = button('Kopieren nach …', () => openCopyTypeDialog(type, listBox, box));
        const versions = Array.isArray(type.versions) ? type.versions : [];
        clear(box).appendChild(el('div', { class: 'kx-card' },
            el('div', { class: 'kx-block-head' },
                el('h3', { text: `${type.name} · ${type.key}` }),
                el('div', { class: 'kx-section-actions' }, copy, archiveToggle)),
            el('p', { class: 'kx-help', text: `${domainName(type.domain_key)} · aktuelle Version ${type.current_version}${type.archived ? ' · archiviert' : ''}` }),
            el('div', { class: 'kx-form-row', style: { 'margin-top': '12px' } },
                KX.field({ label: 'Name', input: name, name: 'name' }),
                KX.field({ label: 'Beschreibung', input: description, name: 'description' })),
            KX.field({ label: 'Definition (YAML oder JSON)', input: text, name: 'definition_text', help: 'Tab rückt ein; Escape, dann Tab verlässt das Feld.' }),
            el('div', { class: 'kx-form-actions' }, check, save),
            result,
            el('details', { class: 'kx-details', open: true }, el('summary', { text: 'Vorschau' }), preview),
            versions.length ? el('details', { class: 'kx-details' }, el('summary', { text: `Versionen (${versions.length})` }),
                el('ul', { class: 'kx-preview-list' }, versions.map((v) => el('li', {
                    text: `Version ${v.version} · ${KX.fmtDateTime(v.created_at)}${personName(v.created_by) ? ` · ${personName(v.created_by)}` : ''}`,
                })))) : null));
    }

    async function openCopyTypeDialog(type, listBox, editorBox) {
        const domain = KX.select({ name: 'domain' }, domainOptions(true), type.domain_key || '');
        const key = KX.input({ name: 'key', maxlength: 48, class: 'kx-input kx-mono', value: type.key });
        const name = KX.input({ name: 'name', maxlength: 80, value: type.name });
        await KX.dialog({
            title: `«${type.name}» kopieren`,
            description: 'Die aktuelle Version wird Version 1 des neuen Typs. Metriken müssen im Zielbereich oder global bestehen.',
            body: el('div', null,
                KX.field({ label: 'Zielbereich', input: domain, name: 'domain' }),
                el('div', { class: 'kx-form-row', style: { 'margin-top': '14px' } },
                    KX.field({ label: 'Schlüssel', input: key, name: 'key', required: true }),
                    KX.field({ label: 'Name', input: name, name: 'name', required: true }))),
            actions: [
                { label: 'Abbrechen', value: null },
                {
                    label: 'Kopieren',
                    primary: true,
                    onClick: async () => {
                        if (!TYPE_KEY_RE.test(key.value.trim())) {
                            throw new KX.ApiError('Bitte die markierten Angaben prüfen.', 400, { key: '2–48 Zeichen: Kleinbuchstaben, Ziffern, «_» oder «-».' });
                        }
                        const data = await KX.api('POST', '/api/experiments/types', {
                            domain: domain.value || null, key: key.value.trim(), name: name.value.trim(),
                            description: type.description || '', copy_from: type.id,
                        });
                        KX.toast('Typ kopiert.', 'success');
                        if (data.type && data.type.id) typesState.selected = data.type.id;
                        loadTypes(listBox, editorBox);
                        return true;
                    },
                },
            ],
        });
    }

    async function openNewTypeDialog() {
        const domain = KX.select({ name: 'domain' }, domainOptions(true),
            typesState.filter && typesState.filter !== '__all__' ? typesState.filter : '');
        const key = KX.input({ name: 'key', maxlength: 48, class: 'kx-input kx-mono' });
        const name = KX.input({ name: 'name', maxlength: 80 });
        const description = KX.textarea({ name: 'description', rows: 2, maxlength: 2000 });
        const text = KX.textarea({ name: 'definition_text', rows: 18, class: 'kx-input kx-textarea kx-code' }, TYPE_TEMPLATE);
        KX.codeEditor(text);
        let keyTouched = false;
        key.addEventListener('input', () => { keyTouched = true; });
        name.addEventListener('input', () => { if (!keyTouched) key.value = suggestKey(name.value, '_', 48); });
        await KX.dialog({
            title: 'Neuer Typ',
            wide: true,
            body: el('div', null,
                el('div', { class: 'kx-form-row' },
                    KX.field({ label: 'Bereich', input: domain, name: 'domain' }),
                    KX.field({ label: 'Name', input: name, name: 'name', required: true }),
                    KX.field({ label: 'Schlüssel', input: key, name: 'key', required: true })),
                KX.field({ label: 'Beschreibung', input: description, name: 'description' }),
                KX.field({ label: 'Definition (YAML oder JSON)', input: text, name: 'definition_text', aliases: ['definition'] })),
            actions: [
                { label: 'Abbrechen', value: null },
                {
                    label: 'Anlegen',
                    primary: true,
                    onClick: async () => {
                        const errors = {};
                        if (!name.value.trim()) errors.name = 'Bitte einen Namen angeben.';
                        if (!TYPE_KEY_RE.test(key.value.trim())) errors.key = '2–48 Zeichen: Kleinbuchstaben, Ziffern, «_» oder «-».';
                        if (Object.keys(errors).length) throw new KX.ApiError('Bitte die markierten Angaben prüfen.', 400, errors);
                        const data = await KX.api('POST', '/api/experiments/types', {
                            domain: domain.value || null, key: key.value.trim(), name: name.value.trim(),
                            description: description.value.trim(), definition_text: text.value,
                        });
                        KX.toast('Typ angelegt.', 'success');
                        if (data.type && data.type.id) typesState.selected = data.type.id;
                        loaded.delete('typen');
                        selectTab('typen');
                        return true;
                    },
                },
            ],
        });
    }

    // ── Metriken ────────────────────────────────────────────────────────

    const metricsState = { filter: '__all__', archived: false };

    async function renderMetricsTab() {
        const box = panel('metriken');
        clear(box).appendChild(KX.spinnerText());
        try {
            await ensureMeta();
            if (!state.domains.length) await loadDomains();
        } catch (err) {
            failure(box, err, renderMetricsTab);
            return;
        }
        const filter = KX.select({ 'aria-label': 'Bereich' }, [{ value: '__all__', label: 'Alle Bereiche' },
            { value: '', label: 'Nur bereichsübergreifende' }].concat(domainOptions(false)), metricsState.filter);
        const archived = el('input', { type: 'checkbox' });
        archived.checked = metricsState.archived;
        const tableBox = el('div');
        filter.addEventListener('change', () => {
            metricsState.filter = filter.value;
            loadMetrics(tableBox);
        });
        archived.addEventListener('change', () => {
            metricsState.archived = archived.checked;
            loadMetrics(tableBox);
        });
        clear(box);
        box.appendChild(sectionHead('Metriken',
            'Eine Metrik wird einmal definiert und von allen Experimenten des Bereichs genutzt. Ihre Art bestimmt, wie Messwerte gelesen werden.',
            [button('Neue Metrik', () => openMetricDialog(null, tableBox), 'primary')]));
        box.appendChild(el('div', { class: 'kx-toolbar' }, filter,
            el('label', { class: 'kx-check' }, archived, el('span', { text: 'Archivierte zeigen' }))));
        box.appendChild(tableBox);
        loadMetrics(tableBox);
    }

    async function loadMetrics(box) {
        clear(box).appendChild(KX.spinnerText());
        const params = new URLSearchParams();
        if (metricsState.filter !== '__all__' && metricsState.filter) params.set('domain', metricsState.filter);
        params.set('archived', metricsState.archived ? '1' : '0');
        let items;
        try {
            const data = await KX.api('GET', `/api/experiments/metrics?${params.toString()}`);
            items = Array.isArray(data.metrics) ? data.metrics : [];
            if (metricsState.filter === '') items = items.filter((m) => !m.domain_key);
        } catch (err) {
            failure(box, err, () => loadMetrics(box));
            return;
        }
        clear(box);
        if (!items.length) {
            box.appendChild(KX.emptyState(null, 'Keine Metriken für diese Auswahl.'));
            return;
        }
        box.appendChild(tableWrap(['Metrik', 'Bereich', 'Art', 'Einheit', 'Richtung', 'In Gebrauch',
            { node: el('span', { class: 'kx-visually-hidden', text: 'Aktion' }) }],
        items.map((m) => el('tr', null,
            el('th', { scope: 'row' }, el('strong', { text: m.name }), m.archived ? ' ' : null,
                m.archived ? KX.chip('archiviert', 'muted') : null,
                el('span', { class: 'kx-sub kx-mono', text: m.key }),
                m.description ? el('span', { class: 'kx-sub', text: m.description }) : null),
            el('td', { text: domainName(m.domain_key) }),
            el('td', { text: m.kind_label || m.kind }),
            el('td', { text: m.unit || DASH }),
            el('td', { text: m.direction_label || KX.label('directions', m.direction) }),
            el('td', { text: m.in_use ? 'ja' : 'nein' }),
            el('td', { class: 'kx-row-actions' }, button('Bearbeiten', () => openMetricDialog(m, box)))))));
    }

    function levelsToText(levels) {
        if (!levels || typeof levels !== 'object') return '';
        return Object.keys(levels).map((k) => `${k} = ${levels[k]}`).join('\n');
    }

    function textToLevels(text) {
        const out = {};
        const lines = String(text || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
        for (const line of lines) {
            const idx = line.indexOf('=');
            if (idx === -1) throw new KX.ApiError('Stufen bitte als «Wert = Bezeichnung» angeben.', 400, { levels: `«${line.slice(0, 40)}»: bitte als 3 = neutral angeben.` });
            const key = line.slice(0, idx).trim();
            const labelText = line.slice(idx + 1).trim();
            const n = KX.parseNumber(key);
            if (n === null || !labelText) throw new KX.ApiError('Stufen bitte als «Wert = Bezeichnung» angeben.', 400, { levels: `«${line.slice(0, 40)}»: Wert muss eine Zahl sein.` });
            out[String(n)] = labelText.slice(0, 80);
        }
        return out;
    }

    async function openMetricDialog(metric, tableBox) {
        const meta = await ensureMeta();
        const editing = Boolean(metric);
        const kinds = Array.isArray(meta.kinds) && meta.kinds.length ? meta.kinds
            : ['proportion', 'mean', 'count', 'duration', 'currency', 'ratio', 'ordinal', 'categorical'].map((k) => ({ key: k, label: k }));
        const def = editing && metric.definition && typeof metric.definition === 'object' ? metric.definition : {};
        const domain = KX.select({ name: 'domain' }, domainOptions(true),
            editing ? metric.domain_key || '' : (metricsState.filter !== '__all__' ? metricsState.filter : ''));
        if (editing) domain.disabled = true;
        const key = KX.input({ name: 'key', maxlength: 48, class: 'kx-input kx-mono', value: editing ? metric.key : '', readOnly: editing });
        const name = KX.input({ name: 'name', maxlength: 80, value: editing ? metric.name : '' });
        const kind = KX.select({ name: 'kind' }, kinds.map((k) => ({ value: k.key, label: k.label })), editing ? metric.kind : 'proportion');
        const kindHelp = el('small', { class: 'kx-help' });
        const unit = KX.input({ name: 'unit', maxlength: 20, value: editing ? metric.unit || '' : '' });
        const direction = KX.select({ name: 'direction' }, ['higher', 'lower', 'none'].map((d) => ({ value: d, label: KX.label('directions', d) })),
            editing ? metric.direction : 'higher');
        const description = KX.textarea({ name: 'description', rows: 2, maxlength: 2000 }, editing ? metric.description || '' : '');
        const decimals = KX.input({ name: 'decimals', inputmode: 'numeric', value: def.decimals != null ? String(def.decimals) : '' });
        const min = KX.input({ name: 'min', inputmode: 'decimal', value: def.min != null ? KX.fmtPlain(def.min).replace(/'/g, '') : '' });
        const max = KX.input({ name: 'max', inputmode: 'decimal', value: def.max != null ? KX.fmtPlain(def.max).replace(/'/g, '') : '' });
        const levels = KX.textarea({ name: 'levels', rows: 5, class: 'kx-input kx-textarea kx-mono', placeholder: '1 = sehr unzufrieden\n2 = unzufrieden' }, levelsToText(def.levels));
        const archived = el('input', { type: 'checkbox' });
        archived.checked = Boolean(editing && metric.archived);
        const levelsField = KX.field({ label: 'Stufen', input: levels, name: 'levels', aliases: ['definition.levels'], help: 'Eine je Zeile: Wert = Bezeichnung. Pflicht für Kategorien, möglich für Skalen.' });
        const boundsRow = el('div', { class: 'kx-form-row', style: { 'margin-top': '14px' } },
            KX.field({ label: 'Nachkommastellen', input: decimals, name: 'decimals', aliases: ['definition.decimals'] }),
            KX.field({ label: 'Minimum', input: min, name: 'min', aliases: ['definition.min'] }),
            KX.field({ label: 'Maximum', input: max, name: 'max', aliases: ['definition.max'] }));
        const syncKind = () => {
            const spec = kinds.find((k) => k.key === kind.value) || {};
            kindHelp.textContent = spec.description || '';
            levelsField.hidden = !(kind.value === 'ordinal' || kind.value === 'categorical');
            const bounded = ['mean', 'duration', 'currency', 'ordinal'].indexOf(kind.value) !== -1;
            min.disabled = !bounded;
            max.disabled = !bounded;
        };
        kind.addEventListener('change', syncKind);
        syncKind();
        let keyTouched = editing;
        if (!editing) {
            key.addEventListener('input', () => { keyTouched = true; });
            name.addEventListener('input', () => { if (!keyTouched) key.value = suggestKey(name.value, '_', 48); });
        }
        const kindField = KX.field({ label: 'Art', input: kind, name: 'kind', required: true });
        kindField.insertBefore(kindHelp, kindField.querySelector('.kx-field-error'));
        const body = el('div', null,
            el('div', { class: 'kx-form-row' },
                KX.field({ label: 'Bereich', input: domain, name: 'domain', help: editing ? 'Lässt sich nicht ändern.' : null }),
                KX.field({ label: 'Name', input: name, name: 'name', required: true }),
                KX.field({ label: 'Schlüssel', input: key, name: 'key', required: !editing, help: editing ? 'Lässt sich nicht ändern.' : null })),
            el('div', { class: 'kx-form-row', style: { 'margin-top': '14px' } },
                kindField,
                KX.field({ label: 'Einheit', input: unit, name: 'unit', help: 'z. B. %, ms, CHF, Punkte' }),
                KX.field({ label: 'Richtung', input: direction, name: 'direction' })),
            editing && metric.in_use ? el('p', { class: 'kx-help', text: 'In Gebrauch: Die Art lässt sich nicht mehr ändern, sobald es Messwerte gibt.' }) : null,
            KX.field({ label: 'Beschreibung', input: description, name: 'description', help: 'Wie wird gemessen? Was ist eine Zeile?' }),
            boundsRow,
            levelsField,
            editing ? el('div', { class: 'kx-field' }, el('label', { class: 'kx-check' }, archived, el('span', { text: 'Archiviert (nicht mehr neu zuordenbar)' }))) : null);
        await KX.dialog({
            title: editing ? `Metrik «${metric.name}» bearbeiten` : 'Neue Metrik',
            wide: true,
            body,
            actions: [
                { label: 'Abbrechen', value: null },
                {
                    label: editing ? 'Speichern' : 'Anlegen',
                    primary: true,
                    onClick: async () => {
                        const errors = {};
                        if (!name.value.trim()) errors.name = 'Bitte einen Namen angeben.';
                        if (!editing && !METRIC_KEY_RE.test(key.value.trim())) errors.key = '2–48 Zeichen: Kleinbuchstaben, Ziffern, «_»; zuerst ein Buchstabe.';
                        const definition = {};
                        if (decimals.value.trim()) {
                            const d = KX.parseNumber(decimals.value);
                            if (d === null || !Number.isInteger(d) || d < 0 || d > 6) errors.decimals = '0 bis 6.';
                            else definition.decimals = d;
                        }
                        if (!min.disabled && min.value.trim()) {
                            const v = KX.parseNumber(min.value);
                            if (v === null) errors.min = 'Bitte eine Zahl eingeben.';
                            else definition.min = v;
                        }
                        if (!max.disabled && max.value.trim()) {
                            const v = KX.parseNumber(max.value);
                            if (v === null) errors.max = 'Bitte eine Zahl eingeben.';
                            else definition.max = v;
                        }
                        if (!levelsField.hidden && levels.value.trim()) {
                            try {
                                definition.levels = textToLevels(levels.value);
                            } catch (err) {
                                Object.assign(errors, err.fields);
                            }
                        }
                        if (Object.keys(errors).length) throw new KX.ApiError('Bitte die markierten Angaben prüfen.', 400, errors);
                        if (editing) {
                            const changes = {};
                            if (name.value.trim() !== metric.name) changes.name = name.value.trim();
                            if (kind.value !== metric.kind) changes.kind = kind.value;
                            if (unit.value.trim() !== (metric.unit || '')) changes.unit = unit.value.trim();
                            if (direction.value !== metric.direction) changes.direction = direction.value;
                            if (description.value.trim() !== (metric.description || '')) changes.description = description.value.trim();
                            if (JSON.stringify(definition) !== JSON.stringify(def)) changes.definition = definition;
                            if (archived.checked !== Boolean(metric.archived)) changes.archived = archived.checked;
                            if (!Object.keys(changes).length) return true;
                            await KX.api('PATCH', `/api/experiments/metrics/${encodeURIComponent(metric.id)}`, changes);
                            KX.toast('Metrik gespeichert.', 'success');
                        } else {
                            await KX.api('POST', '/api/experiments/metrics', {
                                domain: domain.value || null, key: key.value.trim(), name: name.value.trim(),
                                kind: kind.value, unit: unit.value.trim(), direction: direction.value,
                                description: description.value.trim(), definition,
                            });
                            KX.toast('Metrik angelegt.', 'success');
                        }
                        loadMetrics(tableBox);
                        return true;
                    },
                },
            ],
        });
    }

    // ── Auswerter ───────────────────────────────────────────────────────

    const PYTHON_TEMPLATE = [
        '# Auswerter-Vorlage (Python). evaluate(data) bekommt das Eingabe-Objekt',
        '# (experiment, metric, variants, aggregates, rows, rows_truncated, scope,',
        '# params) und gibt ein dict mit allen Schlüsseln des Ausgabevertrags zurück.',
        '# numpy, scipy, pandas und statsmodels sind installiert; es gibt kein Netz.',
        '',
        '',
        'def evaluate(data):',
        '    metric = data["metric"]',
        '    variants_in = data.get("variants") or []',
        '    aggregates = [a for a in data.get("aggregates") or [] if a.get("variant") is not None]',
        '    control = next((v["key"] for v in variants_in if v.get("is_control")),',
        '                   variants_in[0]["key"] if variants_in else None)',
        '    base = next((a for a in aggregates if a["variant"] == control), None)',
        '',
        '    variants = [{"variant": a["variant"], "n": a["n"], "value": a["estimate"], "sd": None,',
        '                 "sum": a["value_sum"], "ci_low": None, "ci_high": None} for a in aggregates]',
        '    comparisons = []',
        '    for a in aggregates:',
        '        if base is None or a["variant"] == control:',
        '            continue',
        '        if a["estimate"] is None or base["estimate"] is None:',
        '            continue',
        '        diff = a["estimate"] - base["estimate"]',
        '        comparisons.append({',
        '            "variant": a["variant"], "baseline": control, "label": "Differenz",',
        '            "estimate": diff, "ci_low": None, "ci_high": None, "p_value": None,',
        '            "prob_better": None,',
        '            "relative": diff / base["estimate"] if base["estimate"] else None,',
        '            "unit": "Pp." if metric["kind"] == "proportion" else metric.get("unit", ""),',
        '            "verdict": "inconclusive",',
        '        })',
        '',
        '    return {',
        '        "verdict": "inconclusive",',
        '        "headline": metric["name"] + ": " + str(len(comparisons)) + " Vergleiche ohne Test",',
        '        "summary": "Vorlage: Differenz der Schätzungen zur Kontrolle, ohne Unsicherheit.",',
        '        "comparisons": comparisons,',
        '        "variants": variants,',
        '        "values": {"rows": len(data.get("rows") or [])},',
        '        "table": {"headers": ["Variante", "n", "Schätzung"],',
        '                  "rows": [[v["variant"], v["n"], v["value"]] for v in variants]},',
        '        "warnings": [],',
        '    }',
        '',
    ].join('\n');

    const JULIA_TEMPLATE = [
        '# Auswerter-Vorlage (Julia). evaluate(data) bekommt das Eingabe-Objekt als',
        '# Dict{String,Any} und gibt ein Dict mit allen Schlüsseln des Ausgabevertrags',
        '# zurück. JSON3, Distributions, HypothesisTests, StatsBase und DataFrames sind',
        '# installiert; es gibt kein Netz.',
        '',
        'function evaluate(data)',
        '    metric = data["metric"]',
        '    aggregates = [a for a in data["aggregates"] if a["variant"] !== nothing]',
        '    variants = [Dict("variant" => a["variant"], "n" => a["n"], "value" => a["estimate"],',
        '                     "sd" => nothing, "sum" => a["value_sum"], "ci_low" => nothing,',
        '                     "ci_high" => nothing) for a in aggregates]',
        '    return Dict(',
        '        "verdict" => "n/a",',
        '        "headline" => string(metric["name"], ": ", length(variants), " Varianten"),',
        '        "summary" => "Vorlage: Schätzung je Variante, ohne Vergleich.",',
        '        "comparisons" => Any[],',
        '        "variants" => variants,',
        '        "values" => Dict("variants" => length(variants)),',
        '        "table" => Dict("headers" => ["Variante", "n"],',
        '                        "rows" => [[v["variant"], v["n"]] for v in variants]),',
        '        "warnings" => String[],',
        '    )',
        'end',
        '',
    ].join('\n');

    const PARAMS_TEMPLATE = JSON.stringify({ type: 'object', properties: {}, additionalProperties: false }, null, 2);

    const evaluatorsState = { items: [], selected: null };

    async function renderEvaluatorsTab() {
        const box = panel('auswerter');
        clear(box).appendChild(KX.spinnerText());
        const meta = await ensureMeta();
        const runner = meta.runner || {};
        const languages = runner.languages && typeof runner.languages === 'object'
            ? Object.keys(runner.languages).map((k) => `${k} ${runner.languages[k]}`).join(', ') : '';
        let banner;
        if (!runner.configured) {
            banner = el('div', { class: 'kx-banner' }, el('p', { text: 'Rechenumgebung: nicht eingerichtet. Python- und Julia-Auswerter brauchen die Rechenumgebung (Profil experiments); eingebaute Auswerter laufen immer.' }));
        } else if (!runner.ok) {
            banner = el('div', { class: 'kx-banner kx-banner--error' }, el('p', { text: 'Rechenumgebung: eingerichtet, aber nicht erreichbar.' }));
        } else {
            banner = el('div', { class: 'kx-banner kx-banner--ok' }, el('p', { text: `Rechenumgebung: erreichbar${languages ? ` (${languages})` : ''}.` }));
        }
        const listBox = el('div');
        const editorBox = el('div');
        clear(box);
        box.appendChild(sectionHead('Auswerter',
            'Eingebaute Auswerter rechnen in der Plattform. Eigene Python- und Julia-Auswerter laufen nur in der abgeschotteten Rechenumgebung, ohne Netz.',
            [button('Neuer Auswerter', () => {
                evaluatorsState.selected = null;
                renderEvaluatorList(listBox, editorBox);
                openEvaluatorEditor(null, editorBox, listBox);
            }, 'primary')]));
        box.appendChild(banner);
        box.appendChild(el('div', { class: 'kx-split', style: { 'margin-top': '14px' } }, listBox, editorBox));
        loadEvaluators(listBox, editorBox);
    }

    async function loadEvaluators(listBox, editorBox) {
        clear(listBox).appendChild(KX.spinnerText());
        try {
            const data = await KX.api('GET', '/api/experiments/evaluators');
            const items = Array.isArray(data.evaluators) ? data.evaluators : [];
            items.sort((a, b) => (a.builtin ? 0 : 1) - (b.builtin ? 0 : 1) || String(a.name).localeCompare(String(b.name), 'de'));
            evaluatorsState.items = items;
        } catch (err) {
            failure(listBox, err, () => loadEvaluators(listBox, editorBox));
            return;
        }
        renderEvaluatorList(listBox, editorBox);
        if (evaluatorsState.selected) openEvaluatorEditor(evaluatorsState.selected, editorBox, listBox);
        else clear(editorBox).appendChild(KX.emptyState(null, 'Einen Auswerter links auswählen oder einen neuen anlegen.'));
    }

    function renderEvaluatorList(listBox, editorBox) {
        clear(listBox).appendChild(el('ul', { class: 'kx-list', 'aria-label': 'Auswerter' }, evaluatorsState.items.map((e) => el('li', null,
            el('button', {
                type: 'button', class: 'kx-list-item', 'aria-current': e.id === evaluatorsState.selected ? 'true' : 'false',
                onClick: () => {
                    evaluatorsState.selected = e.id;
                    renderEvaluatorList(listBox, editorBox);
                    openEvaluatorEditor(e.id, editorBox, listBox);
                },
            },
            el('span', null, el('strong', { text: e.name }), ' ',
                KX.chip(e.builtin ? 'eingebaut' : e.language, 'lang'),
                e.archived ? ' ' : null, e.archived ? KX.chip('archiviert', 'muted') : null),
            el('span', { class: 'kx-sub kx-mono', text: e.key }))))));
    }

    async function openEvaluatorEditor(evaluatorId, box, listBox) {
        const meta = await ensureMeta();
        let ev = null;
        if (evaluatorId) {
            clear(box).appendChild(KX.spinnerText());
            try {
                const data = await KX.api('GET', `/api/experiments/evaluators/${encodeURIComponent(evaluatorId)}`);
                ev = data.evaluator;
            } catch (err) {
                failure(box, err, () => openEvaluatorEditor(evaluatorId, box, listBox));
                return;
            }
        }
        if (ev && (ev.builtin || ev.language === 'builtin')) {
            clear(box).appendChild(el('div', { class: 'kx-card' },
                el('div', { class: 'kx-block-head' }, el('h3', { text: `${ev.name} · ${ev.key}` }), KX.chip('eingebaut', 'lang')),
                el('p', { class: 'kx-prose', text: ev.description || '' }),
                el('p', { class: 'kx-subhead', text: 'Metrik-Arten' }),
                el('p', { text: (ev.input_kinds || []).map(kindLabel(meta)).join(', ') || DASH }),
                el('p', { class: 'kx-subhead', text: 'Parameter (JSON Schema)' }),
                el('pre', { class: 'kx-pre', text: JSON.stringify(ev.params_schema || {}, null, 2) }),
                el('p', { class: 'kx-help', text: 'Eingebaute Auswerter lassen sich nicht ändern.' })));
            return;
        }
        const editing = Boolean(ev);
        const kinds = Array.isArray(meta.kinds) ? meta.kinds : [];
        const key = KX.input({ name: 'key', maxlength: 64, class: 'kx-input kx-mono', value: editing ? ev.key : '', readOnly: editing, placeholder: 'team.bootstrap_mean' });
        const name = KX.input({ name: 'name', maxlength: 80, value: editing ? ev.name : '' });
        const language = KX.select({ name: 'language' }, [{ value: 'python', label: 'Python' }, { value: 'julia', label: 'Julia' }],
            editing ? ev.language : 'python');
        if (editing) language.disabled = true;
        const description = KX.textarea({ name: 'description', rows: 2, maxlength: 2000 }, editing ? ev.description || '' : '');
        const chosenKinds = new Set(editing ? ev.input_kinds || [] : ['mean', 'duration', 'currency']);
        const kindGroup = el('div', { class: 'kx-choice-group', role: 'group', 'aria-label': 'Metrik-Arten' },
            kinds.map((k) => {
                const box2 = el('input', { type: 'checkbox', value: k.key });
                box2.checked = chosenKinds.has(k.key);
                return el('label', { class: 'kx-choice' }, box2, el('span', { text: k.label }));
            }));
        const schema = KX.textarea({ name: 'params_schema', rows: 6, class: 'kx-input kx-textarea kx-code' },
            editing ? JSON.stringify(ev.params_schema || {}, null, 2) : PARAMS_TEMPLATE);
        KX.codeEditor(schema);
        const code = KX.textarea({ name: 'code', rows: 24, class: 'kx-input kx-textarea kx-code', 'aria-label': 'Code' },
            editing ? ev.code || '' : PYTHON_TEMPLATE);
        KX.codeEditor(code);
        let codeTouched = editing;
        code.addEventListener('input', () => { codeTouched = true; });
        language.addEventListener('change', () => {
            if (!codeTouched) code.value = language.value === 'julia' ? JULIA_TEMPLATE : PYTHON_TEMPLATE;
        });
        const readKinds = () => Array.from(kindGroup.querySelectorAll('input:checked')).map((b) => b.value);
        const result = el('div', { 'aria-live': 'polite' });
        const save = button(editing ? 'Als neue Version speichern' : 'Anlegen', async (e) => {
            const trigger = e.currentTarget;
            clear(result);
            KX.clearFieldErrors(box);
            try {
                const errors = {};
                if (!editing && (!EVALUATOR_KEY_RE.test(key.value.trim()) || key.value.trim().indexOf('builtin.') === 0)) {
                    errors.key = '2–64 Zeichen: Kleinbuchstaben, Ziffern, «_», «.» oder «-»; nicht «builtin.».';
                }
                if (!name.value.trim()) errors.name = 'Bitte einen Namen angeben.';
                if (!readKinds().length) errors.input_kinds = 'Mindestens eine Art wählen.';
                if (!code.value.trim()) errors.code = 'Der Code fehlt.';
                let paramsSchema;
                try {
                    paramsSchema = parseJsonObject(schema.value, 'params_schema');
                } catch (err) {
                    Object.assign(errors, err.fields);
                }
                if (Object.keys(errors).length) throw new KX.ApiError('Bitte die markierten Angaben prüfen.', 400, errors);
                trigger.disabled = true;
                const payload = {
                    name: name.value.trim(), description: description.value.trim(), code: code.value,
                    input_kinds: readKinds(), params_schema: paramsSchema,
                };
                let data;
                if (editing) {
                    data = await KX.api('POST', `/api/experiments/evaluators/${encodeURIComponent(ev.id)}/versions`, payload);
                } else {
                    data = await KX.api('POST', '/api/experiments/evaluators', Object.assign({ key: key.value.trim(), language: language.value }, payload));
                }
                const saved = data.evaluator || {};
                KX.toast(editing ? `Version ${saved.current_version || ''} gespeichert.`.replace('  ', ' ') : 'Auswerter angelegt.', 'success');
                evaluatorsState.selected = saved.id || (ev && ev.id) || null;
                loadEvaluators(listBox, box);
            } catch (err) {
                trigger.disabled = false;
                const unmatched = KX.showFieldErrors(box, err.fields);
                clear(result).appendChild(el('div', { class: 'kx-banner kx-banner--error', role: 'alert' },
                    el('div', null, el('p', { text: KX.errorMessage(err) }),
                        unmatched.length ? el('ul', null, unmatched.map((u) => el('li', { text: u }))) : null)));
            }
        }, 'primary');
        const versions = editing && Array.isArray(ev.versions) ? ev.versions : [];
        const card = el('div', { class: 'kx-card' },
            el('div', { class: 'kx-block-head' },
                el('h3', { text: editing ? `${ev.name} · ${ev.key}` : 'Neuer Auswerter' }),
                editing ? KX.chip(`${ev.language} · Version ${ev.current_version}`, 'lang') : null),
            el('div', { class: 'kx-form-row' },
                KX.field({ label: 'Schlüssel', input: key, name: 'key', required: !editing }),
                KX.field({ label: 'Name', input: name, name: 'name', required: true }),
                KX.field({ label: 'Sprache', input: language, name: 'language' })),
            KX.field({ label: 'Beschreibung', input: description, name: 'description' }),
            el('div', { class: 'kx-field', dataset: { field: 'input_kinds' } },
                el('span', { class: 'kx-label', text: 'Metrik-Arten' }), kindGroup,
                el('span', { class: 'kx-field-error', 'aria-live': 'polite' })),
            KX.field({ label: 'Parameter (JSON Schema)', input: schema, name: 'params_schema' }),
            KX.field({
                label: 'Code', input: code, name: 'code',
                help: 'evaluate(data) gibt verdict, headline, summary, comparisons, variants, values, table und warnings zurück. Tab rückt ein; Escape, dann Tab verlässt das Feld.',
            }),
            el('div', { class: 'kx-form-actions' }, save),
            result,
            versions.length ? el('details', { class: 'kx-details' }, el('summary', { text: `Versionen (${versions.length})` }),
                el('ul', { class: 'kx-preview-list' }, versions.map((v) => el('li', {
                    text: `Version ${v.version} · ${KX.fmtDateTime(v.created_at)}${personName(v.created_by) ? ` · ${personName(v.created_by)}` : ''}`,
                })))) : null);
        clear(box).appendChild(card);
        box.appendChild(testPanel(ev, code));
    }

    function kindLabel(meta) {
        const kinds = Array.isArray(meta.kinds) ? meta.kinds : [];
        return (k) => (kinds.find((x) => x.key === k) || {}).label || k;
    }

    /** Test gegen ein Experiment und eine Metrik, mit dem Code im Editor (auch ungespeichert). */
    function testPanel(ev, codeArea) {
        const panelBox = el('div', { class: 'kx-card', style: { 'margin-top': '14px' } });
        panelBox.appendChild(el('div', { class: 'kx-block-head' }, el('h3', { text: 'Testen' })));
        if (!ev) {
            panelBox.appendChild(el('p', { class: 'kx-muted', text: 'Zum Testen den Auswerter zuerst anlegen.' }));
            return panelBox;
        }
        const experiment = KX.input({ name: 'experiment', class: 'kx-input kx-mono', placeholder: 'MKT-1', maxlength: 20 });
        const metric = KX.select({ name: 'metric' }, [{ value: '', label: 'Zuerst ein Experiment angeben' }], '');
        metric.disabled = true;
        const params = KX.textarea({ name: 'params', rows: 3, class: 'kx-input kx-textarea kx-mono' }, '{}');
        const output = el('div', { 'aria-live': 'polite' });
        let lookupSeq = 0;
        const lookup = async () => {
            const keyValue = experiment.value.trim().toUpperCase();
            experiment.value = keyValue;
            const seq = ++lookupSeq;
            clear(metric);
            metric.disabled = true;
            if (!KX.isKey(keyValue)) {
                metric.appendChild(el('option', { value: '' }, 'Zuerst ein Experiment angeben'));
                return;
            }
            metric.appendChild(el('option', { value: '' }, 'Wird geladen …'));
            try {
                const data = await KX.api('GET', `/api/experiments/${keyValue}`);
                if (seq !== lookupSeq) return;
                const metrics = (data.experiment && data.experiment.metrics) || [];
                clear(metric);
                metrics.forEach((m) => metric.appendChild(el('option', { value: m.key }, `${m.name} (${m.kind_label || m.kind})`)));
                if (!metrics.length) metric.appendChild(el('option', { value: '' }, 'Keine Metriken zugeordnet'));
                metric.disabled = !metrics.length;
            } catch (err) {
                if (seq !== lookupSeq) return;
                clear(metric).appendChild(el('option', { value: '' }, KX.errorMessage(err)));
            }
        };
        experiment.addEventListener('change', lookup);
        const run = button('Testen', async (e) => {
            const trigger = e.currentTarget;
            clear(output);
            let parsed;
            try {
                parsed = parseJsonObject(params.value, 'params');
            } catch (err) {
                clear(output).appendChild(el('div', { class: 'kx-banner kx-banner--error' }, el('p', { text: err.message })));
                return;
            }
            if (!KX.isKey(experiment.value.trim()) || !metric.value) {
                clear(output).appendChild(el('div', { class: 'kx-banner' }, el('p', { text: 'Bitte ein Experiment und eine Metrik wählen.' })));
                return;
            }
            trigger.disabled = true;
            output.appendChild(KX.spinnerText('Läuft in der Rechenumgebung …'));
            try {
                const data = await KX.api('POST', `/api/experiments/evaluators/${encodeURIComponent(ev.id)}/test`, {
                    experiment: experiment.value.trim(), metric: metric.value, params: parsed,
                    code: codeArea.value,
                });
                const r = data.result || {};
                clear(output);
                output.appendChild(el('div', { class: `kx-banner ${r.ok ? 'kx-banner--ok' : 'kx-banner--error'}`, role: 'status' },
                    el('p', { text: r.ok ? 'Der Auswerter ist durchgelaufen.' : (r.error || 'Der Auswerter ist mit einem Fehler abgebrochen.') }),
                    KX.finite(r.duration_ms) !== null ? el('span', { class: 'kx-help', text: `${KX.fmtNumber(r.duration_ms / 1000, 2)} s` }) : null));
                if (r.output !== undefined && r.output !== null) {
                    output.appendChild(el('p', { class: 'kx-subhead', text: 'Ausgabe' }));
                    output.appendChild(el('pre', { class: 'kx-pre', text: JSON.stringify(r.output, null, 2) }));
                }
                output.appendChild(el('p', { class: 'kx-subhead', text: 'Protokoll' }));
                output.appendChild(el('pre', { class: 'kx-pre', text: r.logs ? String(r.logs) : 'Kein Protokoll.' }));
            } catch (err) {
                clear(output).appendChild(el('div', { class: 'kx-banner kx-banner--error', role: 'alert' }, el('p', { text: KX.errorMessage(err) })));
            } finally {
                trigger.disabled = false;
            }
        });
        panelBox.appendChild(el('p', { class: 'kx-help', text: 'Rechnet mit dem Code im Editor, auch wenn er noch nicht gespeichert ist. Das Ergebnis wird nicht gespeichert.' }));
        panelBox.appendChild(el('div', { class: 'kx-form-row', style: { 'margin-top': '10px' } },
            KX.field({ label: 'Experiment', input: experiment, name: 'experiment' }),
            KX.field({ label: 'Metrik', input: metric, name: 'metric' })));
        panelBox.appendChild(KX.field({ label: 'Parameter (JSON)', input: params, name: 'params' }));
        panelBox.appendChild(el('div', { class: 'kx-form-actions' }, run));
        panelBox.appendChild(output);
        return panelBox;
    }

    // ── Zugangsschluessel ───────────────────────────────────────────────

    async function renderTokensTab() {
        const box = panel('zugangsschluessel');
        clear(box);
        box.appendChild(sectionHead('Zugangsschlüssel',
            'Persönliche Schlüssel für CI und Skripte (Python-SDK, Julia, curl). Ein Schlüssel handelt mit Ihren Rollen, wie sie beim Aufruf gelten. Er wird nur beim Anlegen angezeigt.'));
        const prefBox = el('div', { class: 'kx-card', style: { 'margin-bottom': '16px' } });
        box.appendChild(prefBox);
        renderPreferences(prefBox);
        const createBox = el('div', { class: 'kx-card', style: { 'margin-bottom': '16px' } });
        box.appendChild(createBox);
        const listBox = el('div');
        box.appendChild(listBox);
        renderTokenForm(createBox, listBox);
        loadTokens(listBox);
    }

    async function renderPreferences(box) {
        const checkbox = el('input', { type: 'checkbox', id: 'kxPrefShowInSearch', disabled: true });
        const status = el('span', { class: 'kx-help', 'aria-live': 'polite' });
        clear(box).appendChild(el('label', { class: 'kx-check', for: 'kxPrefShowInSearch' }, checkbox,
            el('span', { text: 'Experimente in meiner normalen Suche zeigen' })));
        box.appendChild(el('p', { class: 'kx-help', text: 'Treffer aus Experimenten erscheinen dann auch in der Knovas-Suche (Seite «Suche»), mit einem Kolben-Symbol, sofern die Verwaltung sie nicht für alle ausgeschaltet hat.' }));
        box.appendChild(status);
        let settings = null;
        try {
            // Die Suche zeigt Experimente nur, wenn beides an ist: die
            // Einstellung hier und die der Verwaltung (Reiter Index). Die
            // lesen alle mit Experimente-Rolle.
            const [data, global] = await Promise.all([
                KX.api('GET', '/api/experiments/preferences'),
                KX.api('GET', '/api/experiments/settings').catch(() => null),
            ]);
            checkbox.checked = Boolean(data.preferences && data.preferences.show_in_search);
            checkbox.disabled = false;
            settings = global && global.settings ? global.settings : null;
        } catch (err) {
            status.textContent = KX.errorMessage(err);
            return;
        }
        if (settings && settings.show_in_search === false) {
            // Die eigene Einstellung bleibt waehlbar; sie gilt wieder, sobald
            // die Verwaltung die Experimente in der Suche einschaltet.
            box.insertBefore(el('div', { class: 'kx-banner kx-banner--info', role: 'status' },
                el('p', { text: 'Die Verwaltung hat Experimente in der normalen Suche für alle ausgeschaltet. '
                    + 'Ihre Einstellung hier gilt wieder, sobald sie eingeschaltet werden.' })), box.firstChild);
        }
        checkbox.addEventListener('change', async () => {
            checkbox.disabled = true;
            try {
                const data = await KX.api('PUT', '/api/experiments/preferences', { show_in_search: checkbox.checked });
                checkbox.checked = Boolean(data.preferences && data.preferences.show_in_search);
                status.textContent = 'Gespeichert.';
            } catch (err) {
                checkbox.checked = !checkbox.checked;
                status.textContent = KX.errorMessage(err);
            } finally {
                checkbox.disabled = false;
            }
        });
    }

    function renderTokenForm(box, listBox) {
        const name = KX.input({ name: 'name', maxlength: 80, placeholder: 'z. B. GitHub Actions search-quality' });
        const days = KX.input({ name: 'expires_days', inputmode: 'numeric', value: '90' });
        const shown = el('div', { 'aria-live': 'polite' });
        const form = el('form', { noValidate: true },
            el('div', { class: 'kx-block-head' }, el('h3', { text: 'Neuer Zugangsschlüssel' })),
            el('div', { class: 'kx-form-row' },
                KX.field({ label: 'Name', input: name, name: 'name', required: true }),
                KX.field({ label: 'Gültig (Tage)', input: days, name: 'expires_days', help: '1 bis 365 Tage.' })),
            el('div', { class: 'kx-form-actions' }, el('button', { type: 'submit', class: 'btn btn-primary btn-sm', text: 'Schlüssel anlegen' })),
            shown);
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            KX.clearFieldErrors(form);
            const errors = {};
            const n = KX.parseNumber(days.value);
            if (!name.value.trim()) errors.name = 'Bitte einen Namen angeben.';
            if (n === null || !Number.isInteger(n) || n < 1 || n > 365) errors.expires_days = 'Eine ganze Zahl von 1 bis 365.';
            if (Object.keys(errors).length) {
                KX.showFieldErrors(form, errors);
                return;
            }
            const submit = form.querySelector('button[type=submit]');
            submit.disabled = true;
            try {
                const data = await KX.api('POST', '/api/experiments/tokens', { name: name.value.trim(), expires_days: n });
                const token = data.token || {};
                name.value = '';
                showNewToken(shown, token);
                loadTokens(listBox);
            } catch (err) {
                const unmatched = KX.showFieldErrors(form, err.fields);
                KX.toast([KX.errorMessage(err)].concat(unmatched).join(' '), 'error');
            } finally {
                submit.disabled = false;
            }
        });
        clear(box).appendChild(form);
    }

    /** Den Klartext genau einmal zeigen, mit Kopierknopf und Beispielen. */
    function showNewToken(box, token) {
        const secret = typeof token.token === 'string' ? token.token : '';
        const origin = window.location.origin;
        const curl = `curl -H "Authorization: Bearer ${secret}" ${origin}/api/experiments/v1/ping`;
        const python = [
            '# knovas_experiments.py (nur Standardbibliothek) aus KnovasPlatform/experiments-sdk/python',
            '# In der CI als Umgebung: KNOVAS_URL=' + origin + ', KNOVAS_EXPERIMENTS_TOKEN=<Schlüssel>',
            'import os',
            'from knovas_experiments import Client',
            '',
            'client = Client(os.environ["KNOVAS_URL"], os.environ["KNOVAS_EXPERIMENTS_TOKEN"])',
            'client.log_run("ENG-1", variant="candidate", name="nightly",',
            '               rows=[{"metric": "ndcg_at_10", "value": 0.73, "dims": {"query": "q17"}}])',
            'for evaluation in client.evaluate("ENG-1"):',
            '    print(evaluation["verdict"], evaluation["headline"])',
        ].join('\n');
        const copyButton = button('Kopieren', async () => {
            const ok = await KX.copyText(secret);
            KX.toast(ok ? 'Schlüssel kopiert.' : 'Kopieren ging nicht; bitte markieren und kopieren.', ok ? 'success' : 'error');
        });
        clear(box).appendChild(el('div', { class: 'kx-banner kx-banner--ok', role: 'status', style: { display: 'block' } },
            el('p', null, el('strong', { text: `Schlüssel «${token.name || ''}» angelegt.` }),
                ` Er wird nur jetzt angezeigt; gültig bis ${KX.fmtDate(token.expires_at)}.`),
            el('div', { class: 'kx-token-box' }, el('code', { text: secret }), copyButton),
            el('p', { class: 'kx-subhead', text: 'Mit curl prüfen' }),
            el('pre', { class: 'kx-pre', text: curl }),
            el('p', { class: 'kx-subhead', text: 'Python-SDK in CI' }),
            el('pre', { class: 'kx-pre', text: python }),
            el('p', { class: 'kx-help', text: 'Den Schlüssel als Secret der CI hinterlegen, nie im Repository.' })));
    }

    async function loadTokens(box) {
        clear(box).appendChild(KX.spinnerText());
        let tokens;
        try {
            const data = await KX.api('GET', '/api/experiments/tokens');
            tokens = Array.isArray(data.tokens) ? data.tokens : [];
        } catch (err) {
            failure(box, err, () => loadTokens(box));
            return;
        }
        clear(box);
        if (!tokens.length) {
            box.appendChild(KX.emptyState(null, 'Sie haben noch keine Zugangsschlüssel.'));
            return;
        }
        const now = Date.now();
        box.appendChild(tableWrap(['Name', 'Schlüssel', 'Erstellt', 'Zuletzt benutzt', 'Läuft ab', 'Zustand',
            { node: el('span', { class: 'kx-visually-hidden', text: 'Aktion' }) }],
        tokens.map((t) => {
            const expires = KX.parseDate(t.expires_at);
            const expired = expires && expires.getTime() < now;
            const stateChip = t.revoked ? KX.chip('widerrufen', 'muted')
                : expired ? KX.chip('abgelaufen', 'open') : KX.chip('aktiv', 'good');
            return el('tr', null,
                el('th', { scope: 'row', text: t.name }),
                el('td', { class: 'kx-mono', text: t.hint || DASH }),
                el('td', { class: 'kx-nowrap', text: KX.fmtDate(t.created_at) }),
                el('td', { class: 'kx-nowrap', text: t.last_used_at ? KX.fmtDateTime(t.last_used_at) : 'nie' }),
                el('td', { class: 'kx-nowrap', text: KX.fmtDate(t.expires_at) }),
                el('td', null, stateChip),
                el('td', { class: 'kx-row-actions' }, t.revoked ? null : button('Widerrufen', async (e) => {
                    const ok = await KX.confirm('Schlüssel widerrufen',
                        `«${t.name}» funktioniert danach nicht mehr. CI-Jobs, die ihn nutzen, schlagen fehl.`, 'Widerrufen', true);
                    if (!ok) return;
                    try {
                        await KX.api('DELETE', `/api/experiments/tokens/${encodeURIComponent(t.id)}`);
                        KX.toast('Schlüssel widerrufen.', 'success');
                        loadTokens(box);
                    } catch (err) {
                        KX.toast(KX.errorMessage(err), 'error');
                    }
                }, 'danger')));
        })));
    }

    // ── Index ───────────────────────────────────────────────────────────

    async function renderIndexTab() {
        const box = panel('index');
        clear(box).appendChild(KX.spinnerText());
        let index;
        try {
            await ensureMeta();
            const data = await KX.api('GET', '/api/experiments/index');
            index = data.index || {};
        } catch (err) {
            failure(box, err, renderIndexTab);
            return;
        }
        clear(box);
        box.appendChild(sectionHead('Index',
            'Jedes Experiment wird als ein Dokument in Knovas geschrieben, damit die normale Suche Texte, Ergebnisse und Entscheidungen findet.',
            [button('Aktualisieren', renderIndexTab),
                button('Alles neu indexieren', async (e) => {
                    const ok = await KX.confirm('Alles neu indexieren',
                        'Alle Experimente werden erneut an Knovas übertragen, gedrosselt auf wenige Dokumente je Minute.', 'Neu indexieren');
                    if (!ok) return;
                    try {
                        const data = await KX.api('POST', '/api/experiments/index/reindex', {});
                        const n = data.result && Number(data.result.queued);
                        KX.toast(`${KX.fmtNumber(n || 0, 0)} Experimente zur Übertragung eingeplant.`, 'success');
                        renderIndexTab();
                    } catch (err) {
                        KX.toast(KX.errorMessage(err), 'error');
                    }
                }, 'primary')]));
        const groups = Array.isArray(index.access_groups) ? index.access_groups : [];
        let banner;
        if (!index.enabled) {
            banner = el('div', { class: 'kx-banner' }, el('p', { text: 'Die Übertragung nach Knovas ist ausgeschaltet (EXPERIMENTS_INDEX_ENABLED). Die Suche hier nutzt dann nur die Datenbank.' }));
        } else if (!groups.length && !index.unrestricted) {
            banner = el('div', { class: 'kx-banner kx-banner--error' }, el('p', { text: 'Keine Knovas-Zugriffsgruppe festgelegt (EXPERIMENTS_ACCESS_GROUPS): Experimente werden nicht in Knovas übertragen.' }));
        } else if (!groups.length) {
            banner = el('div', { class: 'kx-banner' }, el('p', { text: 'Ohne Zugriffsgruppe übertragen: in Knovas sichtbar für alle, die eine Ordnerregel auf das Präfix nicht ausschliesst.' }));
        } else {
            banner = el('div', { class: 'kx-banner kx-banner--ok' },
                el('p', { text: 'Übertragung eingeschaltet, sichtbar für die Zugriffsgruppen:' }),
                el('span', { class: 'kx-tags' }, groups.map((g) => el('span', { class: 'kx-tag kx-tag--static', text: String(g) }))));
        }
        box.appendChild(banner);
        const counts = index.counts && typeof index.counts === 'object' ? index.counts : {};
        box.appendChild(el('p', { class: 'kx-subhead', text: 'Stand der Experimente' }));
        box.appendChild(el('dl', { class: 'kx-stats' }, ['indexed', 'pending', 'error', 'off'].map((s) => el('div', { class: 'kx-stat' },
            el('dt', { text: KX.label('index_states', s) }), el('dd', { text: KX.fmtNumber(Number(counts[s]) || 0, 0) })))));
        const jobs = index.jobs && typeof index.jobs === 'object' ? index.jobs : {};
        const jobLabels = { pending: 'wartend', running: 'laufend', done: 'erledigt', dead: 'aufgegeben' };
        box.appendChild(el('p', { class: 'kx-subhead', text: 'Hintergrundaufträge' }));
        box.appendChild(el('dl', { class: 'kx-stats' }, Object.keys(jobLabels).map((s) => el('div', { class: 'kx-stat' },
            el('dt', { text: jobLabels[s] }), el('dd', { text: KX.fmtNumber(Number(jobs[s]) || 0, 0) })))));
        const failures = Array.isArray(index.failures) ? index.failures : [];
        box.appendChild(el('p', { class: 'kx-subhead', text: 'Letzte Fehler' }));
        box.appendChild(failures.length ? tableWrap(['Zeitpunkt', 'Auftrag', 'Meldung'], failures.map((f) => el('tr', null,
            el('td', { class: 'kx-nowrap', text: KX.fmtDateTime(f.at) }),
            el('td', { class: 'kx-mono', text: f.kind || DASH }),
            el('td', { text: f.error || DASH }))))
            : el('p', { class: 'kx-muted', text: 'Keine.' }));
        const warnings = Array.isArray(index.access_warnings) ? index.access_warnings : [];
        box.appendChild(el('p', { class: 'kx-subhead', text: 'Personen ohne Zugriffsgruppe' }));
        box.appendChild(warnings.length ? el('div', null,
            el('p', { class: 'kx-help', text: 'Diese Personen sehen Experimente in der Knovas-Suche nicht, bis sie die Gruppen unter Verwaltung → Personen erhalten; die Seite Experimente sucht für sie in der Datenbank.' }),
            tableWrap(['Person', 'Fehlende Gruppen'], warnings.map((w) => el('tr', null,
                el('td', { text: personName(w.user) || String(w.user || DASH) }),
                el('td', null, (Array.isArray(w.missing_groups) ? w.missing_groups : []).map((g) => el('span', { class: 'kx-tag kx-tag--static', text: String(g) })))))))
            : el('p', { class: 'kx-muted', text: 'Keine.' }));
        const runner = index.runner || {};
        box.appendChild(el('p', { class: 'kx-subhead', text: 'Rechenumgebung' }));
        box.appendChild(el('p', {
            text: !runner.configured ? 'Nicht eingerichtet.'
                : runner.ok ? `Erreichbar${KX.finite(runner.busy) !== null ? `, ${KX.fmtNumber(runner.busy, 0)} Aufträge laufen` : ''}.`
                    : 'Eingerichtet, aber nicht erreichbar.',
        }));
        const settingsBox = el('div', { class: 'kx-card', style: { 'margin-top': '20px' } });
        box.appendChild(settingsBox);
        renderGlobalSettings(settingsBox);
    }

    async function renderGlobalSettings(box) {
        const checkbox = el('input', { type: 'checkbox', id: 'kxSettingShowInSearch', disabled: true });
        const status = el('span', { class: 'kx-help', 'aria-live': 'polite' });
        clear(box).appendChild(el('label', { class: 'kx-check', for: 'kxSettingShowInSearch' }, checkbox,
            el('span', { text: 'Experimente in der normalen Suche zeigen' })));
        box.appendChild(el('p', { class: 'kx-help', text: 'Gilt für alle mit Experimente-Rolle; jede Person kann es für sich zusätzlich ausschalten. Wer keine Rolle hat, sieht Experimente nie.' }));
        box.appendChild(status);
        try {
            const data = await KX.api('GET', '/api/experiments/settings');
            checkbox.checked = Boolean(data.settings && data.settings.show_in_search);
            checkbox.disabled = false;
        } catch (err) {
            status.textContent = KX.errorMessage(err);
            return;
        }
        checkbox.addEventListener('change', async () => {
            checkbox.disabled = true;
            try {
                const data = await KX.api('PUT', '/api/experiments/settings', { show_in_search: checkbox.checked });
                checkbox.checked = Boolean(data.settings && data.settings.show_in_search);
                status.textContent = 'Gespeichert.';
            } catch (err) {
                checkbox.checked = !checkbox.checked;
                status.textContent = KX.errorMessage(err);
            } finally {
                checkbox.disabled = false;
            }
        });
    }

    // ── Start ───────────────────────────────────────────────────────────

    function init() {
        bindTabs();
        const all = tabs();
        const requested = window.location.hash.replace(/^#/, '');
        const initial = all.some((t) => t.dataset.tab === requested) ? requested
            : (canManage ? 'bereiche' : 'zugangsschluessel');
        selectTab(initial, false);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
