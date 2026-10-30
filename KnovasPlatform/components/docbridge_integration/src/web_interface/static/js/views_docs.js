/* Ansichten, render kind "doc" ("Dokumentation"): the software documentation (Step 8).
 *
 * Left, the tree of pages the page definitions give (for Knovas: System →
 * software areas → L2 → L3) with a search and a marker per node; right, the
 * page drawn from KnowledgeBase's page definition: sections in order, each with
 * its render hint, "Quelle · Stand", stale and missing flags. A page with
 * children draws them as a layered diagram (no force layout: the server places
 * the boxes). Which types have pages comes from the server (KC-3). Every page
 * has a stable address, /views/doc/<key>. Nothing is stored in the browser.
 */
(function (root) {
    'use strict';
    const { el, clear, api } = root.ViewsCore;
    const SVG = 'http://www.w3.org/2000/svg';

    function svg(tag, attrs, children) {
        const node = document.createElementNS(SVG, tag);
        Object.entries(attrs || {}).forEach(([k, v]) => { if (v !== undefined && v !== null) node.setAttribute(k, String(v)); });
        (children || []).forEach((c) => { if (c) node.appendChild(c); });
        return node;
    }

    function svgText(x, y, text, cls) {
        const t = svg('text', { x, y, class: cls });
        t.textContent = text;
        return t;
    }

    /** Two lines of at most ~34 characters, cut at a word. */
    function wrap(text, width) {
        const words = String(text || '').split(/\s+/).filter(Boolean);
        const lines = [''];
        words.forEach((w) => {
            const cur = lines[lines.length - 1];
            if ((cur + ' ' + w).trim().length > width && cur) {
                if (lines.length === 2) { lines[1] = `${lines[1].replace(/[.,;:…]+$/, '')}…`; return; }
                lines.push(w);
            } else lines[lines.length - 1] = (cur + ' ' + w).trim();
        });
        return lines.filter(Boolean).slice(0, 2);
    }

    /** A diagram from server-placed boxes: name and one-line description, arrows labelled with the relation. */
    function drawDiagram(d, onOpen) {
        const pad = 24;
        const w = d.width + pad * 2;
        const h = d.height + pad * 2;
        const root_ = svg('svg', { viewBox: `0 0 ${w} ${h}`, width: w, height: h, class: 'views-docs-diagram', role: 'img',
            'aria-label': 'Diagramm' });
        root_.appendChild(svg('defs', {}, [svg('marker', { id: 'docArrow', viewBox: '0 0 10 10', refX: 9, refY: 5,
            markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, [svg('path', { d: 'M0,0 L10,5 L0,10 z', class: 'views-docs-arrowhead' })])]));
        const at = {};
        d.boxes.forEach((b) => { at[b.id] = { x: b.x + pad, y: b.y + pad }; });
        const bw = d.box_w;
        const bh = d.box_h;
        // Where the line from a box's centre towards a point leaves the box.
        const clip = (cx, cy, dx, dy) => {
            const t = Math.min(dx ? (bw / 2) / Math.abs(dx) : Infinity, dy ? (bh / 2) / Math.abs(dy) : Infinity);
            return [cx + dx * t, cy + dy * t];
        };
        const pairs = new Set(d.edges.map((e) => `${e.from}>${e.to}`));
        d.edges.forEach((e) => {
            const a = at[e.from];
            const b = at[e.to];
            if (!a || !b) return;
            const ax = a.x + bw / 2; const ay = a.y + bh / 2; const bx = b.x + bw / 2; const by = b.y + bh / 2;
            const dx = bx - ax; const dy = by - ay; const len = Math.hypot(dx, dy) || 1;
            // Both directions between two boxes: bend each to its own side so they do not overlap.
            const bend = pairs.has(`${e.to}>${e.from}`) ? 18 : 0;
            const nx = -dy / len * bend; const ny = dx / len * bend;
            const [x1, y1] = clip(ax, ay, dx + nx, dy + ny);
            const [x2, y2] = clip(bx, by, -dx + nx, -dy + ny);
            const mx = (x1 + x2) / 2 + nx; const my = (y1 + y2) / 2 + ny;
            root_.appendChild(svg('path', { d: `M${x1},${y1} Q${mx},${my} ${x2},${y2}`, class: 'views-docs-edge', 'marker-end': 'url(#docArrow)' }));
            root_.appendChild(svgText((x1 + 2 * mx + x2) / 4, (y1 + 2 * my + y2) / 4 - 3, e.label, 'views-docs-edge-label'));
        });
        d.boxes.forEach((b) => {
            const p = at[b.id];
            const flags = (b.incomplete && b.incomplete.length ? ' views-docs-box-incomplete' : '') + (b.stale && b.stale.length ? ' views-docs-box-stale' : '');
            const g = svg('g', { class: `views-docs-box${b.self ? ' views-docs-box-self' : ''}${flags}`, tabindex: 0, role: 'link' }, [
                svg('rect', { x: p.x, y: p.y, width: bw, height: bh, rx: 6 }),
                svgText(p.x + 10, p.y + 18, b.key || b.type, 'views-docs-box-key'),
                svgText(p.x + 10, p.y + 34, wrap(b.name, 32)[0], 'views-docs-box-name'),
                ...wrap(b.description, 40).map((line, i) => svgText(p.x + 10, p.y + 50 + i * 12, line, 'views-docs-box-desc')),
            ]);
            g.appendChild(svg('title', {}, [document.createTextNode(`${b.full_name || b.name}${b.description ? ` — ${b.description}` : ''}`)]));
            if (!b.self && onOpen) {
                g.addEventListener('click', () => onOpen(b));
                g.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') onOpen(b); });
            }
            root_.appendChild(g);
        });
        return el('div', { className: 'views-docs-diagram-box' }, [root_]);
    }

    const STATUS = { passing: ['✓', 'geprüft'], failing: ['✗', 'nicht bestanden'], unknown: ['?', 'Stand unbekannt'], none: ['·', 'keine Prüfung'] };

    class Docs {
        constructor(page) {
            this.page = page;
            this.open = new Set();
            this.docTypes = [];                                // until the tree says which types have pages
        }

        /** Mount into the page's document area: tree left, page right. */
        mount(box, rootsBody) {
            this.roots = rootsBody.nodes;
            this.docTypes = rootsBody.types || [];          // the types with a page definition
            this.nav = el('nav', { className: 'views-docs-nav', 'aria-label': 'Dokumentation' });
            this.main = el('article', { className: 'views-docs-page' });
            box.appendChild(el('div', { className: 'views-docs' }, [this.nav, this.main]));
            this.drawNav();
            const key = this.page.state.docKey;
            if (key) this.openKey(key);
            else if (this.roots.length) this.openNode(this.roots[0].id);
            else this.main.appendChild(el('p', { className: 'views-empty', text: 'Keine Seite der obersten Ebene im Wissensnetz.' }));
        }

        drawNav() {
            clear(this.nav);
            const input = el('input', { type: 'search', placeholder: 'Komponente, Bereich, Schnittstelle…', 'aria-label': 'Dokumentation durchsuchen' });
            const results = el('ul', { className: 'views-docs-results', hidden: true });
            let timer = null;
            input.addEventListener('input', () => {
                clearTimeout(timer);
                timer = setTimeout(async () => {
                    clear(results);
                    if (input.value.trim().length < 2) { results.hidden = true; return; }
                    try {
                        const d = await api('/api/views/search', { q: input.value.trim() });
                        const hits = d.results.filter((r) => this.docTypes.includes(r.type)).slice(0, 12);
                        hits.forEach((r) => results.appendChild(el('li', {}, [el('button', { type: 'button', className: 'views-link',
                            onclick: () => { results.hidden = true; input.value = ''; this.openNode(r.id); } },
                        [el('span', { className: 'views-tech', text: r.key || r.type_label }), ` ${r.name}`])])));
                        if (!hits.length) results.appendChild(el('li', { className: 'views-muted', text: 'Keine dokumentierte Seite gefunden.' }));
                        results.hidden = false;
                    } catch (err) { results.hidden = true; }
                }, 200);
            });
            this.nav.appendChild(el('div', { className: 'views-docs-search' }, [input, results]));
            this.treeBox = el('ul', { className: 'views-docs-tree', role: 'tree' });
            this.nav.appendChild(this.treeBox);
            this.nav.appendChild(el('p', { className: 'views-docs-legend views-muted' }, [
                el('span', { className: 'views-docs-mark views-docs-mark-incomplete', text: '●' }), ' Pflichtabschnitt leer  ',
                el('span', { className: 'views-docs-mark views-docs-mark-stale', text: '●' }), ' veraltet (KB-Bericht)']));
            this.roots.forEach((r) => this.treeBox.appendChild(this.treeItem(r, 0)));
        }

        treeItem(n, depth) {
            const li = el('li', { role: 'treeitem', dataset: { node: n.id } });
            const kids = el('ul', { role: 'group', hidden: true });
            const toggle = n.has_children ? el('button', { type: 'button', className: 'views-docs-toggle', 'aria-label': 'aufklappen', text: '▸',
                onclick: () => this.toggle(n, li, kids, toggle) }) : el('span', { className: 'views-docs-toggle' });
            const marks = [];
            if (n.incomplete && n.incomplete.length) marks.push(el('span', { className: 'views-docs-mark views-docs-mark-incomplete', title: `Leer: ${n.incomplete.join(', ')}`, text: '●' }));
            if (n.stale && n.stale.length) marks.push(el('span', { className: 'views-docs-mark views-docs-mark-stale', title: `Veraltet: ${n.stale.join(', ')}`, text: '●' }));
            const label = n.key && !n.name.startsWith(n.key) ? `${n.name}` : n.name.replace(/^(\S+) — /, '');
            li.appendChild(el('div', { className: 'views-docs-row', style: `padding-left:${depth * 12}px` }, [toggle,
                el('button', { type: 'button', className: 'views-docs-link', title: n.name, onclick: () => this.openNode(n.id) }, [
                    n.key ? el('span', { className: 'views-tech', text: `${n.key} ` }) : null, label]), ...marks]));
            li.appendChild(kids);
            li.kids = kids;
            li.toggleBtn = toggle;
            li.depth = depth;
            li.loaded = false;
            return li;
        }

        async toggle(n, li, kids, btn, forceOpen) {
            const opening = forceOpen || kids.hidden;
            if (opening && !li.loaded) {
                try {
                    const d = await api('/api/views/doc/tree', { parent: n.id });
                    d.nodes.forEach((c) => kids.appendChild(this.treeItem(c, li.depth + 1)));
                    li.loaded = true;
                } catch (err) { kids.appendChild(el('li', { className: 'views-error', text: err.message })); }
            }
            kids.hidden = !opening;
            btn.textContent = opening ? '▾' : '▸';
        }

        async reveal(path) {
            // Open the tree from the System down to the page's node, and the node's own children.
            let list = this.treeBox;
            for (const id of path) {
                const li = [...list.children].find((x) => x.dataset && x.dataset.node === id);
                if (!li) return;
                this.nav.querySelectorAll('.views-docs-current').forEach((x) => x.classList.remove('views-docs-current'));
                li.firstChild.classList.add('views-docs-current');
                if (li.kids && li.toggleBtn.tagName === 'BUTTON') {
                    await this.toggle({ id }, li, li.kids, li.toggleBtn, true);
                    list = li.kids;
                }
            }
        }

        async openKey(key) {
            try {
                const loc = await api('/api/views/doc/locate', { key });
                this.openNode(loc.node);
            } catch (err) {
                clear(this.main).appendChild(el('p', { className: 'views-error', text: `${key}: ${err.message}` }));
            }
        }

        async openNode(id) {
            const main = clear(this.main);
            main.appendChild(el('p', { className: 'views-muted', text: 'Lädt die Seite…' }));
            try {
                const d = await api('/api/views/doc/page', { node: id });
                this.current = d;
                this.page.state.docKey = d.node.key || '';
                this.page.state.node = id;
                this.page.syncUrl();
                this.page.showAnswerMeta(d);
                clear(main);
                this.drawPage(main, d);
                this.reveal(d.path);
            } catch (err) {
                clear(main).appendChild(el('p', { className: 'views-error', text: err.message }));
            }
        }

        exportUrl(scope) {
            const q = new URLSearchParams({ node: this.current.node.id, scope });
            return `/api/views/doc/export?${q}`;
        }

        drawPage(main, d) {
            const n = d.node;
            const link = n.key ? `${root.location.origin}/views/doc/${encodeURIComponent(n.key)}` : '';
            main.appendChild(el('header', { className: 'views-docs-head' }, [
                el('p', { className: 'views-tech', text: `${d.page.name}${n.key ? ` · ${n.key}` : ''}` }),
                el('h2', { text: n.name }),
                el('div', { className: 'views-actions views-docs-actions' }, [
                    el('button', { type: 'button', className: 'views-button', text: 'Im Graph zeigen', onclick: () => this.page.center(n.id) }),
                    el('a', { className: 'views-button', href: this.exportUrl('page'), text: 'Markdown (Seite)' }),
                    d.diagram ? el('a', { className: 'views-button', href: this.exportUrl('subtree'), text: 'Markdown (Teilbaum)' }) : null,
                    el('button', { type: 'button', className: 'views-button', text: 'Drucken / PDF', onclick: () => root.print() }),
                    link ? el('a', { className: 'views-docs-permalink views-tech', href: link, text: link }) : null,
                ]),
                el('p', { className: 'views-muted views-docs-stamp', text: `Stand ${d.as_of}, KnowledgeBase ${(d.kb_commit || '?').slice(0, 12)}` +
                    (d.report.generated_at ? ` · Vollständigkeitsbericht ${d.report.generated_at}` : '') }),
            ]));
            if (d.diagram) {
                main.appendChild(el('section', { className: 'views-docs-section' }, [
                    el('h3', { text: d.diagram.title || 'Übersicht' }),
                    drawDiagram(d.diagram, (b) => this.openNode(b.id)),
                    el('p', { className: 'views-tech', text: d.diagram.note }),
                ]));
            }
            d.sections.forEach((s) => main.appendChild(this.drawSection(s, d)));
        }

        drawSection(s, d) {
            const flags = [];
            if (s.required) flags.push(el('span', { className: 'views-badge views-badge-muted', text: 'Pflicht' }));
            if (s.missing) flags.push(el('span', { className: 'views-badge views-badge-flag', text: 'fehlt' }));
            if (s.stale) flags.push(el('span', { className: 'views-badge views-badge-stale', title: `${s.stale.source}, Stand ${s.stale.as_of}`, text: 'veraltet' }));
            const sec = el('section', { className: `views-docs-section${s.missing ? ' views-docs-missing' : ''}${s.stale ? ' views-docs-stale' : ''}`, dataset: { section: s.id } }, [
                el('h3', {}, [s.title, ...flags]),
                el('p', { className: 'views-docs-source views-tech', text: `Quelle: ${s.source || '—'} · Stand ${(s.as_of || '').replace('T', ' ')}` +
                    (s.stale ? ` · veraltet laut KB-Bericht vom ${s.stale.as_of}` : '') }),
            ]);
            if (s.missing) {
                sec.appendChild(el('p', { className: 'views-flag', text: `fehlt — ${s.missing}` }));
                return sec;
            }
            const body = el('div', { className: 'views-docs-body' });
            sec.appendChild(body);
            this.drawBody(body, s, d);
            return sec;
        }

        drawBody(body, s, d) {
            if (s.render === 'text') {
                if (s.text) s.text.split(/\n{2,}/).forEach((para) => body.appendChild(el('p', { text: para })));
                if (s.facts) {
                    body.appendChild(el('dl', { className: 'views-facts-head' }, s.facts.flatMap((f) => [
                        el('dt', { text: f.label }), el('dd', { text: f.value === null || f.value === undefined || f.value === '' ? '—' : String(f.value) })])));
                }
                if (!s.text && !s.facts) body.appendChild(el('p', { className: 'views-muted', text: '—' }));
                return;
            }
            if (s.deferred) {
                body.appendChild(el('p', { className: 'views-muted', text: `${s.count} Einträge; Spalten und Status sind noch nicht gelesen (Lesebudget der Seite).` }));
                body.appendChild(el('button', { type: 'button', className: 'views-button views-button-small', text: `Laden (${s.deferred_reads} Lesezugriffe)`,
                    onclick: async () => {
                        clear(body).appendChild(el('p', { className: 'views-muted', text: 'Lädt…' }));
                        try {
                            const one = await api('/api/views/doc/section', { node: d.node.id, section: s.id });
                            const fresh = one.sections[0];
                            clear(body);
                            this.drawBody(body, fresh, d);
                        } catch (err) { clear(body).appendChild(el('p', { className: 'views-error', text: err.message })); }
                    } }));
            }
            if (s.render === 'diagram' && s.diagram && s.diagram.boxes.length > 1) {
                body.appendChild(drawDiagram(s.diagram, (b) => this.openNode(b.id)));
                if (s.diagram.details.length) {
                    body.appendChild(el('ul', { className: 'views-list views-docs-details' }, s.diagram.details.map((x) => el('li', {}, [
                        el('strong', { text: x.name }), x.description ? el('span', { className: 'views-muted', text: ` — ${x.description}` }) : null]))));
                }
            } else if ((s.render === 'table') && s.columns.length && !s.deferred) {
                const table = el('table', { className: 'views-table views-docs-table' });
                table.appendChild(el('tr', {}, [el('th', { text: 'Name' }), ...s.column_labels.map((c) => el('th', { text: c }))]));
                s.items.forEach((r) => table.appendChild(el('tr', {}, [el('td', {}, [this.itemLink(r)]),
                    ...s.columns.map((c) => el('td', { text: r.cells[c] === null || r.cells[c] === undefined ? '—' : String(r.cells[c]) }))])));
                body.appendChild(table);
            } else if (s.render !== 'diagram' || !s.diagram) {
                body.appendChild(el('ul', { className: `views-list views-docs-list${s.render === 'status-list' ? ' views-docs-status' : ''}` }, s.items.map((r) => {
                    const st = r.status ? STATUS[r.status] : null;
                    return el('li', {}, [
                        st ? el('span', { className: `views-docs-st views-docs-st-${r.status}`, title: `${st[1]}${r.checks ? ` (${r.checks} Prüfungen)` : ''}`, text: st[0] }) : null,
                        this.itemLink(r),
                        r.label ? el('span', { className: 'views-badge views-badge-muted', text: r.label }) : null,
                        s.columns.length && !s.deferred ? el('span', { className: 'views-tech', text: ` ${s.columns.map((c) => r.cells[c]).filter((v) => v !== null && v !== undefined).join(' · ')}` }) : null,
                        r.description && s.render === 'list' ? el('div', { className: 'views-muted', text: r.description }) : null,
                    ]);
                })));
            }
            if (s.more) body.appendChild(el('p', { className: 'views-muted', text: `… und ${s.more} weitere` }));
            if (s.total && s.total > s.count) body.appendChild(el('p', { className: 'views-muted', text: `die neuesten ${s.count} von ${s.total}` }));
        }

        itemLink(r) {
            if (r.url) {
                return el('a', { href: r.url, target: '_blank', rel: 'noopener noreferrer', title: r.path || '', text: r.name });
            }
            if (this.docTypes.includes(r.type)) {
                return el('button', { type: 'button', className: 'views-link', title: 'Seite öffnen', onclick: () => this.openNode(r.id) }, [r.name]);
            }
            return el('button', { type: 'button', className: 'views-link', title: 'Dossier öffnen', onclick: () => this.page.openDossier(r.id) }, [r.name]);
        }
    }

    root.ViewsDocs = { Docs, drawDiagram, wrap };
}(typeof window !== 'undefined' ? window : globalThis));
