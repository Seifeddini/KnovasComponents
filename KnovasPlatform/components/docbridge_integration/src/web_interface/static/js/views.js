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
                // One selection for every tab (Step 6b); ?instance= of older links is the same thing.
                node: params.get('node') || params.get('instance') || '',
                meta: params.get('meta') === '1',
                threshold: parseInt(params.get('n') || '5', 10) || 5,
                process: params.get('process') || 'PROC-feature-lifecycle',
                listType: params.get('type') || '',
                listQuery: '', listSort: 'name', listDesc: false, listPage: 0,
                listFilters: { owner: '', status: '', step: '' },
                owner: params.get('owner') || '',
                capability: params.get('capability') || '',
                status: params.get('status') || '',
                spine: params.get('spine') || 'SPINE-product',
                profileView: params.get('pview') || '',            // a profile's map (KC-2): <profile>/<view>
                branch: params.get('branch') || '',
                rows: params.get('rows') || 'L3',
                cols: params.get('cols') || 'L5',
                expand: (params.get('expand') || '').split(',').filter(Boolean),
                view: params.get('view') || '',
                sections: params.get('sections') === 'context' ? ['context'] : [],
                // "Lücken": feature plans first
                gapProcess: params.get('gaps') || 'PROC-feature-lifecycle', gapStep: '', gapType: '', gapOwner: '',
                // "Dokumentation": /views/doc/<key> is a page's stable address
                docKey: (document.body && document.body.dataset && document.body.dataset.docKey) || params.get('doc') || '',
            };
            if (this.state.docKey && !params.get('kind')) this.state.kind = 'doc';
            this.typeLabels = {};
            this.viewSettings = { hidden: new Set(), sorting: '' };
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
            root.ViewsDiscover.bindSearch(this, this.$('viewsSearchInput'), this.$('viewsSearchResults'));
            this.$('viewsSearch').addEventListener('submit', (e) => e.preventDefault());
            this.$('viewsStartButton').addEventListener('click', () => this.showStart());
            this.bindProcessControls();
            this.bindSpineControls();
            document.querySelectorAll('#viewsKinds button[data-kind]').forEach((b) => {
                b.addEventListener('click', () => this.setKind(b.dataset.kind));
            });
            if (root.ViewsChange) {
                this.changes = new root.ViewsChange.ChangeSet(this);
                this.changes.refresh();
            }
            this.restoreSelection().then(() => this.setKind(this.state.kind, true));
            if (root.SavedViews) {
                this.saved = new root.SavedViews(this);
                this.saved.init();
            }
        }

        isHidden(type) {
            return !!(this.viewSettings && this.viewSettings.hidden && this.viewSettings.hidden.has(type));
        }

        /** Render kinds drawn as DOM (everything but the graph), by name. */
        static get DOM_KINDS() {
            return {
                definitions: { path: () => ['/api/views/definitions', {}], render: (box, body) => root.ViewsDefinitions.render(box, body, this) },
                trace: {
                    path: (s) => (s.node ? ['/api/views/trace', { process: s.process, instance: s.node, meta: s.meta,
                        sections: s.sections.join(',') === 'context' ? 'context' : '' }] : null),
                    start: true,
                    render: (box, body, page) => { page.learnLabels(body.legend); root.ViewsProcess.renderTrace(box, body, page); },
                },
                tree: {
                    path: (s) => ['/api/views/tree', s.profileView ? { view: s.profileView } : { spine: s.spine, branch: s.branch }],
                    render: (box, body, page) => root.ViewsSpine.renderTree(box, body, page),
                },
                matrix: {
                    path: (s) => ['/api/views/matrix', { spine: s.spine, branch: s.branch, rows: s.rows, cols: s.cols }],
                    render: (box, body, page) => root.ViewsSpine.renderMatrix(box, body, page),
                },
                list: {
                    path: (s) => (s.listType ? ['/api/views/list', { type: s.listType, q: s.listQuery, sort: s.listSort,
                        desc: s.listDesc, page: s.listPage, meta: s.meta, ...s.listFilters }] : null),
                    start: true,
                    render: (box, body, page) => root.ViewsDiscover.renderList(box, body, page),
                },
                board: {
                    path: (s) => ['/api/views/board', { process: s.process, owner: s.owner, capability: s.capability, status: s.status }],
                    render: (box, body, page) => { page.fillFilters(body.filters, body.applied); root.ViewsProcess.renderBoard(box, body, page); },
                },
                gaps: {
                    path: (s) => ['/api/views/gaps', { process: s.gapProcess, step: s.gapStep, type: s.gapType, owner: s.gapOwner }],
                    render: (box, body, page) => root.ViewsGaps.render(box, body, page),
                },
                changes: {
                    path: () => ['/api/views/changeset', {}],
                    render: (box, body, page) => root.ViewsChange.renderReview(box, body, page),
                },
                doc: {
                    path: () => ['/api/views/doc/tree', {}],
                    render: (box, body, page) => { page.docs = new root.ViewsDocs.Docs(page); page.docs.mount(box, body); },
                },
            };
        }

        setKind(kind, initial) {
            const known = kind === 'graph' || ViewsPage.DOM_KINDS[kind];
            this.state.kind = known ? kind : 'graph';
            document.querySelectorAll('#viewsKinds button[data-kind]').forEach((b) => {
                b.setAttribute('aria-pressed', String(b.dataset.kind === this.state.kind));
            });
            document.querySelectorAll('[data-kinds]').forEach((n) => {
                n.hidden = !n.dataset.kinds.split(' ').includes(this.state.kind);
            });
            const isGraph = this.state.kind === 'graph' && !!this.state.node;
            this.$('viewsCanvas').hidden = !isGraph;
            this.$('viewsDoc').hidden = isGraph;
            this.$('viewsLegend').hidden = !isGraph;
            if (!initial) this.closePanel();
            if (['trace', 'board', 'tree', 'matrix'].includes(this.state.kind)) this.loadProcesses();
            this.reload();
        }

        syncUrl() {
            // Keep parameters this controller does not own (?open= of the definitions).
            const params = new URLSearchParams(root.location.search);
            ['node', 'meta', 'n', 'process', 'instance', 'owner', 'capability', 'status', 'spine', 'branch', 'rows', 'cols', 'expand', 'sections', 'type', 'gaps', 'pview'].forEach((k) => params.delete(k));
            params.set('kind', this.state.kind);
            if (this.state.view) params.set('view', this.state.view); else params.delete('view');
            const own = { sections: ['trace'], process: ['trace', 'board'], gapProcess: ['gaps'], listType: ['list'], owner: ['board'], capability: ['board'], status: ['board'],
                spine: ['tree', 'matrix'], branch: ['tree', 'matrix'], rows: ['matrix'], cols: ['matrix'], expand: ['tree'], profileView: ['tree'] };
            Object.entries(own).forEach(([k, kinds]) => {
                const v = Array.isArray(this.state[k]) ? this.state[k].join(',') : this.state[k];
                if (v && kinds.includes(this.state.kind)) params.set({ listType: 'type', gapProcess: 'gaps', profileView: 'pview' }[k] || k, v);
            });
            if (this.state.node) params.set('node', this.state.node);
            if (this.state.meta) params.set('meta', '1');
            if (this.state.threshold !== 5) params.set('n', String(this.state.threshold));
            if (this.state.kind === 'doc') {
                // A documentation page's address is its key, so links in AGENTS.md and context packs stay valid.
                const key = this.state.docKey;
                root.history.replaceState(null, '', key ? `/views/doc/${encodeURIComponent(key)}` : '/views?kind=doc');
                return;
            }
            root.history.replaceState(null, '', `/views?${params}`);
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
            const domKind = ViewsPage.DOM_KINDS[this.state.kind];
            if (domKind) return this.reloadDom(domKind, refresh);
            if (!this.state.node) return this.showStart();
            this.$('viewsCanvas').hidden = false;
            this.$('viewsDoc').hidden = true;
            this.$('viewsLegend').hidden = false;
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

        async reloadDom(kind, refresh) {
            this.$('viewsEmpty').hidden = true;
            const box = clear(this.$('viewsDoc'));
            box.appendChild(el('p', { className: 'views-muted', text: 'Lädt…' }));
            try {
                const target = kind.path(this.state);
                if (!target) {
                    clear(box);
                    if (kind.start) return this.renderStart(box);
                    box.appendChild(el('p', { className: 'views-muted', text: kind.empty || '' }));
                    return;
                }
                const [path, params] = target;
                const body = await api(path, Object.assign({}, params, { refresh: !!refresh }));
                clear(box);
                this.showAnswerMeta(body);
                kind.render(box, body, this);
            } catch (err) {
                clear(box);
                box.appendChild(el('p', { className: 'views-error', text: err.message }));
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
            this.select(id, 'graph');
        }

        openTrace(id) {
            this.select(id, 'trace');
        }

        /** The one selection of the page: every tab shows it, and it is remembered per user. */
        async select(id, kind) {
            this.state.node = id;
            this.closePanel();
            try {
                const loc = await api(`/api/views/locate/${encodeURIComponent(id)}`);
                this.location = loc;
                if (loc.process) this.state.process = loc.process;
                if (loc.tree) {
                    this.state.spine = loc.tree.spine;
                    this.state.branch = loc.tree.branch;
                    this.state.expand = loc.tree.expand;
                }
                if (loc.node && loc.node.type) this.state.listType = loc.node.type;
            } catch (err) {
                this.location = null;
            }
            api('/api/views/selection', {}, { method: 'PUT', body: JSON.stringify({ node: id, kind: kind || this.state.kind }) })
                .catch(() => null);
            if (this.fillSpineControls) this.fillSpineControls();
            this.setKind(kind || this.state.kind);
        }

        /** No selection in the URL: take the user's last one from the Platform. */
        async restoreSelection() {
            if (this.state.node) return;
            try {
                const d = await api('/api/views/selection');
                if (d.selection) this.state.node = d.selection;
            } catch (err) { /* without accounts there is nothing to restore */ }
        }

        openList(type) {
            this.state.listType = type;
            this.state.listPage = 0;
            this.state.listQuery = '';
            this.state.listFilters = { owner: '', status: '', step: '' };
            this.setKind('list');
        }

        /** "Startpunkt wählen": tiles per type and suggestions, never a blank canvas. */
        showStart() {
            this.$('viewsCanvas').hidden = true;
            this.$('viewsLegend').hidden = true;
            this.$('viewsEmpty').hidden = true;
            const box = clear(this.$('viewsDoc'));
            box.hidden = false;
            return this.renderStart(box);
        }

        async renderStart(box) {
            box.appendChild(el('p', { className: 'views-muted', text: 'Lädt…' }));
            try {
                const [start, sugg] = await Promise.all([api('/api/views/start', { meta: this.state.meta }),
                    api('/api/views/suggestions').catch(() => null)]);
                clear(box);
                this.showAnswerMeta(start);
                root.ViewsDiscover.renderStart(box, start, sugg, this);
            } catch (err) {
                clear(box);
                box.appendChild(el('p', { className: 'views-error', text: err.message }));
            }
        }

        learnLabels(legend) {
            (legend || []).forEach((t) => { this.typeLabels[t.name] = t.label; this.legend[t.name] = t; });
        }

        typeLabel(type) {
            return this.typeLabels[type] || type;
        }

        async loadProcesses() {
            if (this.processesLoaded) return;
            this.processesLoaded = true;
            const select = this.$('viewsProcess');
            try {
                const d = await api('/api/views/definitions');
                clear(select);
                d.processes.forEach((p) => select.appendChild(el('option', { value: p.key, text: p.name, selected: p.key === this.state.process })));
                this.spines = d.spines;
                this.fillSpineControls();
            } catch (err) {
                this.processesLoaded = false;
            }
        }

        bindProcessControls() {
            const select = this.$('viewsProcess');
            select.appendChild(el('option', { value: this.state.process, text: this.state.process }));
            select.addEventListener('change', () => {
                this.state.process = select.value;
                this.state.owner = '';
                this.state.capability = '';
                this.state.status = '';
                this.reload();
            });
            [['owner', 'viewsFilterOwner'], ['capability', 'viewsFilterCapability'], ['status', 'viewsFilterStatus']].forEach(([k, id]) => {
                this.$(id).addEventListener('change', (e) => { this.state[k] = e.target.value; this.reload(); });
            });
        }

        fillSpineControls() {
            const spines = this.spines || [];
            const spineSel = clear(this.$('viewsSpine'));
            spines.forEach((s) => spineSel.appendChild(el('option', { value: s.key, text: s.name, selected: s.key === this.state.spine })));
            const spine = spines.find((s) => s.key === this.state.spine) || spines[0];
            if (!spine) return;
            const branchSel = clear(this.$('viewsBranch'));
            branchSel.appendChild(el('option', { value: '', text: 'Hauptachse' }));
            (spine.branches || []).forEach((b) => branchSel.appendChild(el('option', { value: b.key, text: b.name, selected: b.key === this.state.branch })));
            this.$('viewsBranch').parentNode.hidden = !(spine.branches || []).length || !['tree', 'matrix'].includes(this.state.kind);
            const chain = this.state.branch ? ((spine.branches || []).find((b) => b.key === this.state.branch) || spine) : spine;
            ['viewsRows', 'viewsCols'].forEach((id) => {
                const key = id === 'viewsRows' ? 'rows' : 'cols';
                const sel = clear(this.$(id));
                chain.levels.forEach((l) => sel.appendChild(el('option', { value: l.id, text: `${l.id} ${l.name}`, selected: l.id === this.state[key] })));
            });
        }

        bindSpineControls() {
            this.$('viewsSpine').addEventListener('change', (e) => {
                this.state.spine = e.target.value;
                this.state.branch = '';
                this.state.profileView = '';
                const spine = (this.spines || []).find((s) => s.key === this.state.spine);
                if (spine && spine.levels.length > 2) { this.state.rows = spine.levels[0].id; this.state.cols = spine.levels[spine.levels.length - 1].id; }
                this.fillSpineControls();
                this.reload();
            });
            this.$('viewsBranch').addEventListener('change', (e) => { this.state.branch = e.target.value; this.fillSpineControls(); this.reload(); });
            this.$('viewsRows').addEventListener('change', (e) => { this.state.rows = e.target.value; this.reload(); });
            this.$('viewsCols').addEventListener('change', (e) => { this.state.cols = e.target.value; this.reload(); });
        }

        fillFilters(filters, applied) {
            const fill = (id, values, current, label) => {
                const select = clear(this.$(id));
                select.appendChild(el('option', { value: '', text: label }));
                values.forEach((v) => {
                    const value = typeof v === 'string' ? v : v.id;
                    const text = typeof v === 'string' ? v : v.name;
                    select.appendChild(el('option', { value, text, selected: value === current }));
                });
            };
            fill('viewsFilterOwner', filters.owners, applied.owner, 'alle Verantwortlichen');
            fill('viewsFilterCapability', filters.capabilities, applied.capability, 'alle Fähigkeiten');
            fill('viewsFilterStatus', filters.statuses, applied.status, 'alle Status');
        }

        async openTraceItems(where, position, artefact) {
            const body = this.openPanel(artefact.count + ' × ' + this.typeLabel(artefact.type));
            const load = async (pageNo) => {
                clear(body);
                try {
                    const d = await api('/api/views/trace/items', { process: this.state.process, instance: this.state.node, where, position, page: pageNo });
                    body.appendChild(el('p', { className: 'views-muted', text: d.path + ' · Seite ' + (d.page + 1) + ' von ' + d.pages }));
                    const fix = this.lastFix && artefact.edit && artefact.edit.routable && root.ViewsChange;
                    body.appendChild(el('ul', { className: 'views-list' }, d.members.map((m) => el('li', {}, [
                        this.nodeLink(m), m.status ? el('span', { className: 'views-status', dataset: { status: m.status }, text: m.status }) : null,
                        fix ? el('button', { type: 'button', className: 'views-button views-button-small', text: 'falsch / entfernen',
                            onclick: () => root.ViewsChange.askRemove(this, { process: this.lastFix.process, node: this.lastFix.node, where, position,
                                artefact, targetNode: m.id, targetName: m.name }) }) : null]))));
                    const nav = el('div', { className: 'views-pager' });
                    if (d.page > 0) nav.appendChild(el('button', { type: 'button', className: 'views-button', text: '‹ Zurück', onclick: () => load(d.page - 1) }));
                    if (d.page + 1 < d.pages) nav.appendChild(el('button', { type: 'button', className: 'views-button', text: 'Weiter ›', onclick: () => load(d.page + 1) }));
                    body.appendChild(nav);
                } catch (err) {
                    body.appendChild(el('p', { className: 'views-error', text: err.message }));
                }
            };
            load(0);
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
    }

    root.ViewsPage = ViewsPage;
    if (typeof document !== 'undefined' && document.addEventListener) {
        document.addEventListener('DOMContentLoaded', () => {
            root.viewsPage = new ViewsPage();
            root.viewsPage.init();
        });
    }
}(typeof window !== 'undefined' ? window : globalThis));
