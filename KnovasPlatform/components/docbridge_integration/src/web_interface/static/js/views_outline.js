/* Ansichten, render kind "outline": one page with everything a profile's list view names.
 *
 * What is marked to come first stands on top; then the rows in the view's order, under the
 * heading of their section, each with its title, one sentence and its labelled lines. What is
 * below a row can be folded: its paths as small tables, an item of a process with its steps
 * and what it misses to leave its step. The page only reads: a click opens the node's dossier.
 */
(function (root) {
    'use strict';
    const { el } = root.ViewsCore;

    function filled(value) {
        return value !== null && value !== undefined && value !== '';
    }

    function nameButton(page, r) {
        return el('button', { type: 'button', className: 'views-link', onclick: () => page.openDossier(r.id) }, [el('span', { text: r.title })]);
    }

    function subLines(r) {
        return (r.sub || []).map((s) => el('p', { className: 'views-outline-sub' }, [el('span', { className: 'views-muted', text: `${s.label}: ` }), String(s.value)]));
    }

    function lifecycle(page, r) {
        const l = r.lifecycle;
        const strip = el('ol', { className: 'views-outline-steps' }, l.steps.map((s) => el('li', {
            className: `views-outline-step is-${s.state}`,
            'aria-current': s.state === 'current' ? 'step' : null,
            text: s.name,
        })));
        const parts = [strip];
        if (!l.current) parts.push(el('p', { className: 'views-muted', text: 'Noch keinem Schritt zugeordnet.' }));
        if (l.missing.length) parts.push(el('p', { className: 'views-outline-sub views-muted', text: `Fehlt für diesen Schritt: ${l.missing.join(', ')}` }));
        parts.push(el('button', { type: 'button', className: 'views-link views-outline-all', onclick: () => page.select(r.id, 'trace') }, [el('span', { text: 'Ablauf dieses Plans' })]));
        return el('div', { className: 'views-outline-life' }, parts);
    }

    /** The rows of a path or of an "also" part: the title, what is below it, and the columns with a value. */
    function table(page, part) {
        const body = el('tbody');
        part.rows.forEach((r) => {
            body.appendChild(el('tr', { className: r.emphasis ? 'is-emphasis' : '' }, [
                el('td', {}, [
                    nameButton(page, r),
                    r.title !== r.name ? el('p', { className: 'views-outline-sub views-muted', text: r.name }) : null,
                    ...subLines(r),
                    r.lifecycle ? lifecycle(page, r) : null,
                ]),
                ...part.columns.map((c) => el('td', { className: 'views-outline-cell', text: filled(r.cells[c.id]) ? String(r.cells[c.id]) : '' })),
            ]));
        });
        return el('table', { className: 'views-outline-table' }, [body]);
    }

    function part(page, p) {
        if (!p.rows.length) return null;
        return el('section', { className: 'views-outline-part' }, [el('h4', { text: p.label }), table(page, p)]);
    }

    function rootRow(page, r, d) {
        const meta = d.columns.filter((c) => filled(r.cells[c.id])).map((c, i) => el('span', { text: i ? `${c.label}: ${r.cells[c.id]}` : String(r.cells[c.id]) }));
        const head = [
            el('div', { className: 'views-outline-head' }, [el('strong', { text: r.title }), el('span', { className: 'views-outline-meta' }, meta)]),
            r.lead ? el('p', { className: 'views-outline-lead', text: r.lead }) : null,
            ...subLines(r),
        ];
        const below = [...r.sections.map((s) => part(page, s)),
            el('button', { type: 'button', className: 'views-link views-outline-all', onclick: () => page.openDossier(r.id) }, [el('span', { text: 'Alle Angaben' })])];
        if (!d.collapsed) return el('article', { className: 'views-outline-root' }, [...head, ...below]);
        return el('details', { className: 'views-outline-root' }, [el('summary', {}, head), el('div', { className: 'views-outline-below' }, below)]);
    }

    function topPart(page, p, notes) {
        const lines = notes.map((n) => el('li', { text: n }));
        return el('section', { className: 'views-outline-top' }, [
            el('h3', { text: p ? `${p.label} (${p.rows.length + notes.length})` : 'Hinweise' }),
            p && p.rows.length ? table(page, p) : null,
            lines.length ? el('ul', {}, lines) : null,
        ]);
    }

    function render(box, d, page) {
        const wrap = el('div', { className: 'views-outline' }, [el('h2', { text: d.name })]);
        if (d.description) wrap.appendChild(el('p', { className: 'views-muted', text: d.description }));
        if (d.unread) {
            wrap.appendChild(el('p', { className: 'views-error', text: `${d.unread} Knoten nicht gelesen: die Ansicht ist unvollständig (Lesegrenze views.outline_fact_budget oder ein Lesefehler).` }));
        }
        if (!d.rows.length && !d.also.length) wrap.appendChild(el('p', { className: 'views-muted', text: 'Der Mandant hat noch keine Einträge für diese Ansicht.' }));
        const top = d.also.filter((a) => a.position === 'top');
        if (top.length || d.notes.length) wrap.appendChild(topPart(page, top[0], d.notes));
        top.slice(1).forEach((a) => wrap.appendChild(topPart(page, a, [])));
        let heading = null;
        d.rows.forEach((r) => {
            if (r.section && r.section !== heading) {
                heading = r.section;
                wrap.appendChild(el('h3', { className: 'views-outline-section', text: heading }));
            }
            wrap.appendChild(rootRow(page, r, d));
        });
        d.also.filter((a) => a.position !== 'top' && a.rows.length).forEach((a) => {
            wrap.appendChild(a.collapsed
                ? el('details', { className: 'views-outline-root' }, [el('summary', {}, [el('strong', { text: `${a.label} (${a.rows.length})` })]), el('div', { className: 'views-outline-below' }, [table(page, a)])])
                : part(page, a));
        });
        box.appendChild(wrap);
    }

    root.ViewsOutline = { render };
})(window);
