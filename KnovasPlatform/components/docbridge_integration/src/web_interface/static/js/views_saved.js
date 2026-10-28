/* Ansichten: saved views — the picker, "save current as personal view", "share
 * with the tenant" (admins), delete, and the change log of shared views.
 *
 * A view is presentation only (name, source, render kind, settings); applying
 * one sets the page's state and draws it as usual.
 */
(function (root) {
    'use strict';
    const { el, clear, api } = root.ViewsCore;

    const KIND_SOURCE = { trace: 'process', board: 'process', tree: 'spine', matrix: 'spine', doc: 'spine' };

    class SavedViews {
        constructor(page) {
            this.page = page;
            this.views = [];
            this.isAdmin = document.body && document.body.dataset.admin === 'true';
            this.select = page.$('viewsSaved');
        }

        async init() {
            if (!this.select) return;
            this.select.addEventListener('change', () => {
                const v = this.views.find((x) => x.id === this.select.value);
                if (v) this.apply(v);
            });
            this.page.$('viewsSavedMenu').addEventListener('click', () => this.openMenu());
            await this.load();
            const wanted = this.page.state.view;
            const v = wanted && this.views.find((x) => x.id === wanted || x.name === wanted);
            if (v) this.apply(v);
        }

        async load() {
            try {
                const d = await api('/api/views/saved');
                this.views = d.views;
                this.isAdmin = d.is_admin;
            } catch (err) {
                this.views = [];
                this.page.$('viewsPicker').hidden = true;
                return;
            }
            this.page.$('viewsPicker').hidden = false;
            const sel = clear(this.select);
            sel.appendChild(el('option', { value: '', text: 'Ansicht wählen…' }));
            const groups = [['shared', 'Geteilt'], ['personal', 'Persönlich']];
            groups.forEach(([aud, label]) => {
                const mine = this.views.filter((v) => v.audience === aud);
                if (!mine.length) return;
                sel.appendChild(el('optgroup', { label }, mine.map((v) => el('option', { value: v.id, text: v.name, selected: v.id === this.current }))));
            });
        }

        /** Set the page to a view and draw it. */
        apply(v) {
            const s = this.page.state;
            const settings = v.settings || {};
            const filters = settings.filters || {};
            this.current = v.id;
            this.select.value = v.id;
            s.view = v.id;
            if (v.source.process) s.process = v.source.process;
            if (v.source.spine) s.spine = v.source.spine;
            s.owner = filters.owner || '';
            s.capability = filters.capability || '';
            s.status = filters.status || '';
            s.branch = filters.branch || '';
            if (filters.rows) s.rows = filters.rows;
            if (filters.cols) s.cols = filters.cols;
            s.meta = !!filters.meta;
            if (settings.threshold) s.threshold = settings.threshold;
            if (v.render_kind === 'trace') s.node = settings.default_root || s.node;
            if (v.render_kind === 'doc') s.docKey = '';           // opens the System page
            s.expand = v.render_kind === 'tree' && settings.default_root ? settings.default_root.split(',') : [];
            s.sections = settings.sections || [];
            this.page.viewSettings = { hidden: new Set(settings.hidden_types || []), sorting: settings.sorting || '' };
            this.page.overrides = Object.fromEntries(Object.entries(settings.colours || {}).map(([t, c]) => [t, { color: c }]));
            if (this.page.fillSpineControls) this.page.fillSpineControls();
            this.page.setKind(v.render_kind);
        }

        /** The current page state as a view body (presentation only). */
        snapshot(name, description) {
            const s = this.page.state;
            const kind = KIND_SOURCE[s.kind] ? s.kind : null;
            if (!kind) return null;
            const filters = {};
            if (kind === 'board') ['owner', 'capability', 'status'].forEach((k) => { if (s[k]) filters[k] = s[k]; });
            if (kind === 'tree' || kind === 'matrix') { if (s.branch) filters.branch = s.branch; }
            if (kind === 'matrix') { filters.rows = s.rows; filters.cols = s.cols; }
            if (kind === 'trace' && s.meta) filters.meta = true;
            const settings = { filters, threshold: s.threshold };
            if (kind === 'trace' && s.node) settings.default_root = s.node;
            if (kind === 'tree' && s.expand && s.expand.length) settings.default_root = s.expand.join(',');
            if (kind === 'trace' && s.sections && s.sections.length) settings.sections = s.sections;
            const vs = this.page.viewSettings || {};
            if (vs.hidden && vs.hidden.size) settings.hidden_types = Array.from(vs.hidden);
            if (vs.sorting) settings.sorting = vs.sorting;
            const colours = Object.fromEntries(Object.entries(this.page.overrides || {}).filter(([, o]) => o.color).map(([t, o]) => [t, o.color]));
            if (Object.keys(colours).length) settings.colours = colours;
            const source = KIND_SOURCE[kind] === 'process' ? { process: s.process } : { spine: s.spine };
            return { name, description, source, render_kind: kind, settings };
        }

        openMenu() {
            const page = this.page;
            const body = page.openPanel('Ansichten');
            const current = this.views.find((v) => v.id === this.current);
            const canSave = !!KIND_SOURCE[page.state.kind];
            const msg = el('p', { className: 'views-muted', role: 'status' });

            if (canSave) {
                const name = el('input', { type: 'text', maxlength: '80', placeholder: 'Name', 'aria-label': 'Name der Ansicht' });
                const desc = el('input', { type: 'text', maxlength: '500', placeholder: 'Beschreibung (optional)', 'aria-label': 'Beschreibung' });
                body.appendChild(el('h3', { text: 'Aktuelle Darstellung speichern' }));
                body.appendChild(el('form', { className: 'views-form', onsubmit: async (e) => {
                    e.preventDefault();
                    try {
                        const d = await api('/api/views/saved', {}, { method: 'POST', body: JSON.stringify(this.snapshot(name.value, desc.value)) });
                        this.current = d.view.id;
                        await this.load();
                        msg.textContent = `Gespeichert: ${d.view.name}`;
                    } catch (err) { msg.textContent = err.message; }
                } }, [name, desc, el('button', { type: 'submit', className: 'views-button', text: 'Als persönliche Ansicht speichern' })]));
            } else {
                body.appendChild(el('p', { className: 'views-muted', text: 'Gespeichert werden Ablauf, Board, Baum und Matrix.' }));
            }

            if (current) {
                body.appendChild(el('h3', { text: `Gewählt: ${current.name}` }));
                if (current.description) body.appendChild(el('p', { text: current.description }));
                const actions = el('div', { className: 'views-pager' });
                const mayChange = current.audience === 'personal' || this.isAdmin;
                if (mayChange && canSave) {
                    actions.appendChild(el('button', { type: 'button', className: 'views-button', text: 'Mit aktueller Darstellung überschreiben', onclick: async () => {
                        try {
                            const snap = this.snapshot(current.name, current.description);
                            await api(`/api/views/saved/${current.id}`, {}, { method: 'PATCH', body: JSON.stringify(snap) });
                            await this.load();
                            msg.textContent = 'Aktualisiert.';
                        } catch (err) { msg.textContent = err.message; }
                    } }));
                }
                if (current.audience === 'personal' && this.isAdmin) {
                    actions.appendChild(el('button', { type: 'button', className: 'views-button', text: 'Mit dem Mandanten teilen', onclick: async () => {
                        try {
                            const d = await api(`/api/views/saved/${current.id}/share`, {}, { method: 'POST', body: JSON.stringify({}) });
                            this.current = d.view.id;
                            await this.load();
                            msg.textContent = `Geteilt: ${d.view.name}`;
                        } catch (err) { msg.textContent = err.message; }
                    } }));
                }
                if (mayChange) {
                    // Two clicks instead of a confirm() dialog.
                    const del = el('button', { type: 'button', className: 'views-button', text: 'Löschen', onclick: async () => {
                        if (del.dataset.armed !== 'yes') { del.dataset.armed = 'yes'; del.textContent = 'Wirklich löschen?'; return; }
                        try {
                            await api(`/api/views/saved/${current.id}`, {}, { method: 'DELETE' });
                            this.current = null;
                            await this.load();
                            msg.textContent = 'Gelöscht.';
                        } catch (err) { msg.textContent = err.message; }
                    } });
                    actions.appendChild(del);
                }
                body.appendChild(actions);
            }
            body.appendChild(msg);
            if (this.isAdmin) {
                body.appendChild(el('button', { type: 'button', className: 'views-button', text: 'Standardansichten anlegen', onclick: async () => {
                    try {
                        const d = await api('/api/views/saved/defaults', {}, { method: 'POST', body: JSON.stringify({ set: 'knovas' }) });
                        await this.load();
                        msg.textContent = d.created.length ? `Angelegt: ${d.created.join(', ')}` : 'Alle Standardansichten gibt es schon.';
                    } catch (err) { msg.textContent = err.message; }
                } }));
                this.showLog(body);
            }
        }

        async showLog(body) {
            body.appendChild(el('h3', { text: 'Änderungen an geteilten Ansichten' }));
            const list = el('ul', { className: 'views-list views-log' });
            body.appendChild(list);
            try {
                const d = await api('/api/views/saved/audit');
                d.entries.forEach((e) => {
                    const what = { 'view.shared.created': 'angelegt', 'view.shared.updated': 'geändert',
                        'view.shared.deleted': 'gelöscht', 'view.shared.shared': 'geteilt' }[e.action] || e.action;
                    const changed = e.detail && e.detail.changed ? ` (${e.detail.changed.join(', ')})` : '';
                    list.appendChild(el('li', {}, [el('span', { className: 'views-tech', text: String(e.occurred_at).replace('T', ' ').slice(0, 19) }),
                        ` ${e.actor_email || '?'}: „${(e.detail && e.detail.name) || e.target_id}“ ${what}${changed}`]));
                });
                if (!d.entries.length) list.appendChild(el('li', { className: 'views-muted', text: 'Noch keine.' }));
            } catch (err) {
                list.appendChild(el('li', { className: 'views-error', text: err.message }));
            }
        }
    }

    root.SavedViews = SavedViews;
}(typeof window !== 'undefined' ? window : globalThis));
