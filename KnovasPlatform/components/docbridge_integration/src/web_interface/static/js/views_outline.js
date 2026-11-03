/* Ansichten, render kind "outline": one page with everything a profile's list view names.
 *
 * Rows in the view's order, under the heading of their section; below each row its
 * paths as small tables. An item of a process shows its steps and what it misses to
 * leave its step. The page only reads: a click opens the node's dossier.
 */
(function (root) {
    'use strict';
    const { el } = root.ViewsCore;

    function cellText(value) {
        if (value === null || value === undefined || value === '') return '–';
        return String(value);
    }

    function nameButton(page, r) {
        return el('button', { type: 'button', className: 'views-link', onclick: () => page.openDossier(r.id) }, [el('span', { text: r.name })]);
    }

    function lifecycle(page, r) {
        const l = r.lifecycle;
        if (!l) return null;
        const strip = el('ol', { className: 'views-outline-steps' }, l.steps.map((s) => el('li', {
            className: `views-outline-step is-${s.state}`,
            'aria-current': s.state === 'current' ? 'step' : null,
            text: s.name,
        })));
        const parts = [strip];
        if (!l.current) parts.push(el('p', { className: 'views-muted', text: 'Noch keinem Schritt zugeordnet.' }));
        if (l.missing.length) parts.push(el('p', { className: 'views-outline-missing', text: `Fehlt für diesen Schritt: ${l.missing.join(', ')}` }));
        return el('div', {}, parts);
    }

    /** A path's rows, or the rows below all roots: one table, or one strip per item of a process. */
    function table(page, part) {
        if (!part.rows.length) return null;
        const head = el('tr', {}, [el('th', { text: '' })].concat(part.columns.map((c) => el('th', { text: c.label }))));
        const body = el('tbody');
        part.rows.forEach((r) => {
            body.appendChild(el('tr', { className: r.emphasis ? 'is-emphasis' : '' }, [
                el('td', {}, [nameButton(page, r)]),
                ...part.columns.map((c) => el('td', { text: cellText(r.cells[c.id]) })),
            ]));
            if (r.lifecycle) {
                body.appendChild(el('tr', { className: 'views-outline-steprow' }, [
                    el('td', { colSpan: part.columns.length + 1 }, [lifecycle(page, r)]),
                ]));
            }
        });
        return el('table', { className: 'views-outline-table' }, [el('thead', {}, [head]), body]);
    }

    function part(page, p) {
        const drawn = table(page, p);
        if (!drawn) return null;
        return el('section', { className: 'views-outline-part' }, [
            el('h4', { text: `${p.label} (${p.rows.length})` }), drawn]);
    }

    function render(box, d, page) {
        const wrap = el('div', { className: 'views-outline' }, [el('h2', { text: d.name })]);
        if (d.description) wrap.appendChild(el('p', { className: 'views-muted', text: d.description }));
        if (d.unread) {
            wrap.appendChild(el('p', { className: 'views-error', text: `${d.unread} Knoten nicht gelesen: die Ansicht ist über der Lesegrenze (views.outline_fact_budget).` }));
        }
        if (!d.rows.length && !d.also.length) wrap.appendChild(el('p', { className: 'views-muted', text: 'Der Mandant hat noch keine Einträge für diese Ansicht.' }));
        let heading = null;
        d.rows.forEach((r) => {
            if (r.section && r.section !== heading) {
                heading = r.section;
                wrap.appendChild(el('h3', { className: 'views-outline-section', text: heading }));
            }
            const meta = d.columns.filter((c) => r.cells[c.id] !== null && r.cells[c.id] !== undefined && r.cells[c.id] !== '')
                .map((c) => el('span', {}, [el('span', { className: 'views-muted', text: `${c.label}: ` }), String(r.cells[c.id])]));
            wrap.appendChild(el('article', { className: 'views-outline-root' }, [
                el('header', {}, [el('h4', {}, [nameButton(page, r)]), el('p', { className: 'views-outline-meta' }, meta)]),
                ...r.sections.map((s) => part(page, s)),
            ]));
        });
        d.also.forEach((a) => {
            const drawn = part(page, a);
            if (drawn) wrap.appendChild(drawn);
        });
        box.appendChild(wrap);
    }

    root.ViewsOutline = { render };
})(window);
