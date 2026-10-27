/* Ansichten: the page controller.
 *
 * Holds the current selection in the URL (?kind=&node=&meta=&n=) so a view can
 * be linked; holds nothing from the brain beyond the page's lifetime (plan D1).
 */
(function (root) {
    'use strict';
    const { el, clear, swatch, api, asOfText } = root.ViewsCore;

    class ViewsPage {
        constructor() {
            const params = new URLSearchParams(root.location.search);
            this.state = {
                kind: params.get('kind') || 'graph',
                node: params.get('node') || '',
                meta: params.get('meta') === '1',
                threshold: parseInt(params.get('n') || '5', 10) || 5,
            };
            this.overrides = {};
            this.legend = {};
            this.cy = null;
            this.$ = (id) => document.getElementById(id);
        }

        init() {
            if (!this.$('viewsCanvas')) return;
            this.$('viewsMeta').checked = this.state.meta;
            this.$('viewsThreshold').value = String(this.state.threshold);
            this.$('viewsMeta').addEventListener('change', (e) => {
                this.state.meta = e.target.checked; this.reload();
            });
            this.$('viewsThreshold').addEventListener('change', (e) => {
                const n = parseInt(e.target.value, 10);
                this.state.threshold = Number.isFinite(n) && n > 0 ? Math.min(n, 50) : 5;
                this.reload();
            });
            this.$('viewsRefresh').addEventListener('click', () => this.reload(true));
            this.$('viewsPanelClose').addEventListener('click', () => this.closePanel());
            this.bindSearch();
            this.reload();
        }

        syncUrl() {
            const params = new URLSearchParams();
            params.set('kind', this.state.kind);
            if (this.state.node) params.set('node', this.state.node);
            if (this.state.meta) params.set('meta', '1');
            if (this.state.threshold !== 5) params.set('n', String(this.state.threshold));
            root.history.replaceState(null, '', `${root.location.pathname}?${params}`);
        }

        notice(text) {
            const box = this.$('viewsNotice');
            box.textContent = text || '';
            box.hidden = !text;
        }

        showAnswerMeta(body) {
            this.$('viewsAsOf').textContent = asOfText(body.as_of);
            const notes = [];
            if (body.fallback) notes.push(`Eingeschränkt: ${body.fallback}; die Ansicht wird Knoten für Knoten geladen.`);
            if (body.hidden && body.hidden.meta) notes.push(`${body.hidden.meta} Meta-Knoten ausgeblendet.`);
            if (body.hidden && body.hidden.by_type && body.hidden.by_type.length && !this.state.meta) {
                notes.push(`Nach Typ ausgeblendet: ${body.hidden.by_type.join(', ')}.`);
            }
            if (body.truncated) notes.push('Nicht alle Nachbarn geladen (Obergrenze erreicht).');
            this.notice(notes.join(' '));
        }

        async reload(refresh) {
            this.syncUrl();
            if (!this.state.node) {
                this.$('viewsEmpty').hidden = false;
                return;
            }
            this.$('viewsEmpty').hidden = true;
            try {
                const body = await api('/api/views/graph', {
                    node: this.state.node, meta: this.state.meta, n: this.state.threshold, refresh: !!refresh,
                });
                this.draw(body);
            } catch (err) {
                this.notice(err.message);
            }
        }

        draw(body) {
            this.showAnswerMeta(body);
            this.renderLegend(body.legend || []);
            if (this.cy) this.cy.destroy();
            this.cy = root.ViewsGraph.render(this.$('viewsCanvas'), body, {
                node: (id) => this.openDossier(id),
                chip: (chip) => this.openGroup(chip, 0),
                center: (id) => this.center(id),
                hover: (type, evt) => this.hover(type, evt),
            }, this.overrides);
        }

        center(id) {
            this.state.node = id;
            this.closePanel();
            this.reload();
        }

        renderLegend(types) {
            const box = clear(this.$('viewsLegend'));
            this.legend = {};
            box.appendChild(el('h2', { text: 'Typen' }));
            const list = el('ul');
            types.forEach((t) => {
                this.legend[t.name] = t;
                list.appendChild(el('li', {
                    tabindex: '0', dataset: { type: t.name },
                    onmouseenter: (e) => this.hover(t.name, { originalEvent: e }),
                    onmouseleave: () => this.hover(null),
                    onfocus: (e) => this.hover(t.name, { originalEvent: e }),
                    onblur: () => this.hover(null),
                }, [swatch(t.name, this.overrides), el('span', { text: t.label })]));
            });
            box.appendChild(list);
        }

        hover(typeName, evt) {
            const tip = this.$('viewsTooltip');
            const t = typeName && this.legend[typeName];
            if (!t) { tip.hidden = true; return; }
            clear(tip);
            tip.appendChild(el('strong', { text: t.label }));
            if (t.label !== t.name) tip.appendChild(el('span', { className: 'views-tech', text: ` (${t.name})` }));
            if (t.description) tip.appendChild(el('p', { text: t.description }));
            if (t.confuse) tip.appendChild(el('p', { className: 'views-confuse', text: `Nicht verwechseln: ${t.confuse}` }));
            const e = evt && (evt.originalEvent || evt);
            const x = e && e.clientX !== undefined ? e.clientX : 40;
            const y = e && e.clientY !== undefined ? e.clientY : 120;
            tip.style.left = `${Math.min(x + 14, root.innerWidth - 340)}px`;
            tip.style.top = `${y + 14}px`;
            tip.hidden = false;
        }

        openPanel(title) {
            this.$('viewsPanelTitle').textContent = title;
            const wasHidden = this.$('viewsPanel').hidden;
            this.$('viewsPanel').hidden = false;
            if (wasHidden && this.cy) { this.cy.resize(); this.cy.fit(undefined, 30); }
            return clear(this.$('viewsPanelBody'));
        }

        closePanel() {
            if (this.$('viewsPanel').hidden) return;
            this.$('viewsPanel').hidden = true;
            if (this.cy) { this.cy.resize(); this.cy.fit(undefined, 30); }
        }

        nodeLink(node) {
            return el('button', { type: 'button', className: 'views-link', title: 'Als Mittelpunkt zeigen',
                onclick: () => this.center(node.id) }, [swatch(node.type, this.overrides), el('span', { text: node.name })]);
        }

        async openDossier(id) {
            const body = this.openPanel('Lädt…');
            try {
                const d = await api(`/api/views/node/${encodeURIComponent(id)}`, { meta: this.state.meta });
                const n = d.node;
                this.$('viewsPanelTitle').textContent = n.name;
                const rows = [['Schlüssel', n.key], ['Typ', n.type_label || n.type], ['Verantwortlich', n.owner],
                    ['Status', n.status], ['Quelle', n.source],
                    ['Zuletzt bestätigt', n.last_confirmed || 'nie bestätigt']];
                const dl = el('dl', { className: 'views-facts-head' });
                rows.forEach(([k, v]) => { if (v) { dl.appendChild(el('dt', { text: k })); dl.appendChild(el('dd', { text: v })); } });
                body.appendChild(dl);
                if (n.description) body.appendChild(el('p', { className: 'views-description', text: n.description }));
                if (id !== this.state.node) {
                    body.appendChild(el('button', { type: 'button', className: 'views-button', text: 'Als Mittelpunkt zeigen',
                        onclick: () => this.center(id) }));
                }
                body.appendChild(el('h3', { text: 'Fakten' }));
                const facts = el('table', { className: 'views-table' });
                (d.facts || []).forEach((f) => {
                    facts.appendChild(el('tr', {}, [
                        el('th', { text: f.label }),
                        el('td', {}, [f.value, f.source ? el('div', { className: 'views-source', text: `Quelle: ${f.source}` }) : null,
                            f.tier ? el('div', { className: 'views-source', text: `Vertrauen: ${f.tier}` }) : null]),
                    ]));
                });
                body.appendChild(facts);
                body.appendChild(el('h3', { text: 'Nachbarn' }));
                (d.neighbours || []).forEach((g) => {
                    const arrow = g.direction === 'out' ? '→' : '←';
                    body.appendChild(el('h4', { text: `${arrow} ${g.relation_label} · ${g.type_label} (${g.count})` }));
                    const ul = el('ul', { className: 'views-list' });
                    g.items.forEach((item) => ul.appendChild(el('li', {}, [this.nodeLink(item)])));
                    if (g.more > 0) ul.appendChild(el('li', { className: 'views-more', text: `… und ${g.more} weitere` }));
                    body.appendChild(ul);
                });
            } catch (err) {
                body.appendChild(el('p', { className: 'views-error', text: err.message }));
            }
        }

        async openGroup(chip, page) {
            const t = this.legend[chip.type];
            const body = this.openPanel(`${chip.count} × ${t ? t.label : chip.type}`);
            try {
                const d = await api('/api/views/graph/group', { node: this.state.node, relation: chip.relation,
                    direction: chip.direction, type: chip.type, page, meta: this.state.meta });
                body.appendChild(el('p', { className: 'views-muted',
                    text: `${chip.direction === 'out' ? '→' : '←'} ${chip.relation_label || chip.relation} · Seite ${d.page + 1} von ${d.pages}` }));
                const ul = el('ul', { className: 'views-list' });
                d.members.forEach((m) => ul.appendChild(el('li', {}, [
                    this.nodeLink(m), m.status ? el('span', { className: 'views-status', dataset: { status: m.status }, text: m.status }) : null,
                ])));
                body.appendChild(ul);
                const nav = el('div', { className: 'views-pager' });
                if (d.page > 0) nav.appendChild(el('button', { type: 'button', className: 'views-button', text: '‹ Zurück', onclick: () => this.openGroup(chip, d.page - 1) }));
                if (d.page + 1 < d.pages) nav.appendChild(el('button', { type: 'button', className: 'views-button', text: 'Weiter ›', onclick: () => this.openGroup(chip, d.page + 1) }));
                body.appendChild(nav);
            } catch (err) {
                body.appendChild(el('p', { className: 'views-error', text: err.message }));
            }
        }

        bindSearch() {
            const input = this.$('viewsSearchInput');
            const list = this.$('viewsSearchResults');
            let timer = null;
            const run = async () => {
                const q = input.value.trim();
                if (q.length < 2) { list.hidden = true; return; }
                try {
                    const d = await api('/api/views/search', { q, meta: this.state.meta });
                    clear(list);
                    d.results.forEach((r) => list.appendChild(el('li', { role: 'option' }, [
                        el('button', { type: 'button', onclick: () => { list.hidden = true; input.value = ''; this.center(r.id); } },
                            [swatch(r.type, this.overrides), el('span', { text: r.name }), el('span', { className: 'views-tech', text: r.type })]),
                    ])));
                    if (!d.results.length) list.appendChild(el('li', { className: 'views-muted', text: 'Nichts gefunden.' }));
                    list.hidden = false;
                } catch (err) {
                    this.notice(err.message);
                }
            };
            input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(run, 250); });
            this.$('viewsSearch').addEventListener('submit', (e) => { e.preventDefault(); run(); });
        }
    }

    root.ViewsPage = ViewsPage;
    if (typeof document !== 'undefined' && document.addEventListener) {
        document.addEventListener('DOMContentLoaded', () => {
            root.viewsPage = new ViewsPage();
            root.viewsPage.init();
        });
    }
}(typeof window !== 'undefined' ? window : globalThis));
