/* Ansichten: finding a starting point (Step 6b).
 *
 * The start panel ("Startpunkt wählen": tiles per type in spine order plus
 * suggestions), the list view ("Liste"), and the typeahead that searches names,
 * keys and short descriptions and offers suggestions while it is empty.
 */
(function (root) {
    'use strict';
    const { el, clear, swatch } = root.ViewsCore;

    function hitButton(page, hit, onPick) {
        return el('button', { type: 'button', className: 'views-hit', onclick: () => onPick(hit) }, [
            swatch(hit.type, page.overrides),
            el('span', { className: 'views-hit-name', text: hit.name }),
            el('span', { className: 'views-tech', text: [hit.type_label || hit.type, hit.key && !hit.name.startsWith(hit.key) ? hit.key : null].filter(Boolean).join(' · ') }),
            hit.step ? el('span', { className: 'views-badge views-badge-muted', text: hit.step }) : null,
            hit.degree ? el('span', { className: 'views-badge views-badge-muted', text: `${hit.degree} Kanten` }) : null,
        ]);
    }

    function suggestionBlock(page, groups, onPick) {
        const box = el('div', { className: 'views-suggestions' });
        groups.forEach((g) => {
            const list = el('ul', { className: 'views-list' });
            g.items.forEach((h) => list.appendChild(el('li', {}, [hitButton(page, h, onPick)])));
            if (!g.items.length) list.appendChild(el('li', { className: 'views-muted', text: g.note || 'keine' }));
            box.appendChild(el('section', {}, [el('h3', { text: g.label }), list]));
        });
        return box;
    }

    function renderStart(box, start, sugg, page) {
        box.appendChild(el('h2', { text: 'Startpunkt wählen' }));
        box.appendChild(el('p', { className: 'views-muted', text: 'Einen Typ öffnen, oben suchen (Name oder Schlüssel wie FEAT-, L2-, DEC-) oder einen Vorschlag nehmen.' }));
        if (sugg) box.appendChild(suggestionBlock(page, sugg.groups, (h) => page.select(h.id)));
        start.groups.forEach((g) => {
            box.appendChild(el('h3', { className: 'views-tiles-head', text: g.name }));
            box.appendChild(el('div', { className: 'views-tiles' }, g.tiles.map((t) => el('button', {
                type: 'button', className: `views-tile${t.meta ? ' views-tile-meta' : ''}`, title: t.description || t.name,
                onclick: () => page.openList(t.name),
            }, [el('span', { className: 'views-tile-head' }, [swatch(t.name, page.overrides), el('strong', { text: t.label })]),
                el('span', { className: 'views-tile-count', text: String(t.count) }),
                t.description ? el('span', { className: 'views-tile-desc', text: t.description }) : null]))));
        });
    }

    const COLUMNS = [['key', 'Schlüssel'], ['name', 'Name'], ['status', 'Status'], ['owner', 'Verantwortlich'],
        ['step', 'Schritt'], ['last_confirmed', 'Zuletzt bestätigt'], ['degree', 'Kanten']];

    function renderList(box, d, page) {
        const s = page.state;
        box.appendChild(el('h2', { text: `${d.type.label} (${d.total})` }));
        if (d.type.description) box.appendChild(el('p', { className: 'views-muted', text: d.type.description }));
        if (!d.complete_facts) {
            box.appendChild(el('p', { className: 'views-muted', text: 'Status, Verantwortlich und Schritt werden seitenweise gelesen: Sortieren und Filtern danach gilt für die gezeigte Seite.' }));
        }
        const filterBar = el('div', { className: 'views-list-filters' });
        const q = el('input', { type: 'search', placeholder: 'in dieser Liste suchen…', value: s.listQuery || '' });
        let timer = null;
        q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { s.listQuery = q.value.trim(); s.listPage = 0; page.reload(); }, 300); });
        filterBar.appendChild(q);
        [['owner', d.filters.owners, 'alle Verantwortlichen'], ['status', d.filters.statuses, 'alle Status'], ['step', d.filters.steps, 'alle Schritte']]
            .forEach(([k, values, label]) => {
                if (!values.length && !s.listFilters[k]) return;
                const sel = el('select', { 'aria-label': label, onchange: (e) => { s.listFilters[k] = e.target.value; s.listPage = 0; page.reload(); } },
                    [el('option', { value: '', text: label })].concat(values.map((v) => el('option', { value: v, text: v, selected: v === s.listFilters[k] }))));
                filterBar.appendChild(sel);
            });
        box.appendChild(filterBar);

        const table = el('table', { className: 'views-table views-list-table' });
        table.appendChild(el('thead', {}, [el('tr', {}, COLUMNS.map(([k, label]) => el('th', {}, [
            el('button', { type: 'button', className: 'views-sort', onclick: () => {
                s.listDesc = s.listSort === k ? !s.listDesc : false; s.listSort = k; page.reload();
            } }, [label, s.listSort === k ? (s.listDesc ? ' ▾' : ' ▴') : '']),
        ])).concat([el('th', { text: '' })]))]));
        const body = el('tbody');
        d.rows.forEach((r) => {
            const actions = el('td', { className: 'views-row-actions' }, [
                r.process ? el('button', { type: 'button', className: 'views-button', text: 'Ablauf', onclick: () => page.select(r.id, 'trace') }) : null,
                el('button', { type: 'button', className: 'views-button', text: 'Graph', onclick: () => page.select(r.id, 'graph') }),
                d.in_spine ? el('button', { type: 'button', className: 'views-button', text: 'Baum', onclick: () => page.select(r.id, 'tree') }) : null,
            ]);
            body.appendChild(el('tr', { className: r.id === s.node ? 'views-row-selected' : '' }, [
                el('td', { className: 'views-tech', text: r.key || '' }),
                el('td', {}, [el('button', { type: 'button', className: 'views-link', onclick: () => page.openDossier(r.id) }, [el('span', { text: r.name })])]),
                el('td', {}, [r.status ? el('span', { className: 'views-status', dataset: { status: r.status }, text: r.status }) : '']),
                el('td', { text: r.owner || '' }), el('td', { text: r.step || '' }),
                el('td', { text: r.last_confirmed || '—' }), el('td', { text: String(r.degree) }), actions,
            ]));
        });
        table.appendChild(body);
        box.appendChild(table);
        const nav = el('div', { className: 'views-pager' });
        if (d.page > 0) nav.appendChild(el('button', { type: 'button', className: 'views-button', text: '‹ Zurück', onclick: () => { s.listPage -= 1; page.reload(); } }));
        nav.appendChild(el('span', { className: 'views-muted', text: `Seite ${d.page + 1} von ${d.pages}` }));
        if (d.page + 1 < d.pages) nav.appendChild(el('button', { type: 'button', className: 'views-button', text: 'Weiter ›', onclick: () => { s.listPage += 1; page.reload(); } }));
        box.appendChild(nav);
    }

    /** The typeahead: suggestions while empty, hits while typing. */
    function bindSearch(page, input, list) {
        let timer = null;
        let seq = 0;
        const show = (build) => { clear(list); build(); list.hidden = false; };
        const pick = (h) => { list.hidden = true; input.value = ''; page.select(h.id); };
        const run = async () => {
            const q = input.value.trim();
            const mine = ++seq;
            try {
                if (q.length < 2) {
                    const d = await root.ViewsCore.api('/api/views/suggestions');
                    if (mine !== seq) return;
                    show(() => list.appendChild(el('li', {}, [suggestionBlock(page, d.groups, pick)])));
                    return;
                }
                const d = await root.ViewsCore.api('/api/views/search', { q, meta: page.state.meta });
                if (mine !== seq) return;
                show(() => {
                    d.results.forEach((h) => list.appendChild(el('li', { role: 'option' }, [hitButton(page, h, pick)])));
                    if (!d.results.length) list.appendChild(el('li', { className: 'views-muted', text: 'Nichts gefunden.' }));
                    if (d.unkeyed && d.unkeyed.length) list.appendChild(el('li', { className: 'views-muted', text: `Nur nach Namen gesucht (ohne Schlüssel): ${d.unkeyed.join(', ')}` }));
                });
            } catch (err) {
                page.notice(err.message);
            }
        };
        input.addEventListener('focus', run);
        input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(run, 200); });
        input.addEventListener('keydown', (e) => { if (e.key === 'Escape') list.hidden = true; });
        document.addEventListener('click', (e) => { if (!list.contains(e.target) && e.target !== input) list.hidden = true; });
    }

    root.ViewsDiscover = { renderStart, renderList, bindSearch, suggestionBlock };
}(typeof window !== 'undefined' ? window : globalThis));
