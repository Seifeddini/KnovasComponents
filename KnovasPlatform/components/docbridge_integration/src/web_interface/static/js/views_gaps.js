/* Ansichten, render kind "gaps" ("Lücken"): every missing artefact and flagged rule, across instances.
 *
 * One row per gap: instance, step, what is missing, the top suggestion from
 * KnowledgeBase (suggestions.json, with its reason), owner. A row's choice can
 * be changed before accepting; accepted suggestions go into the change set,
 * one at a time or many at once ("Vorschläge übernehmen").
 */
(function (root) {
    'use strict';
    const { el, clear } = root.ViewsCore;

    function filterBar(body, page) {
        const s = page.state;
        const select = (label, values, current, key, toOption) => {
            const sel = el('select', { 'aria-label': label, onchange: (e) => { s[key] = e.target.value; page.reload(); } });
            sel.appendChild(el('option', { value: '', text: label }));
            values.forEach((v) => {
                const [value, text] = toOption ? toOption(v) : [v, v];
                sel.appendChild(el('option', { value, text, selected: value === current }));
            });
            return sel;
        };
        return el('div', { className: 'views-gaps-filters' }, [
            select('alle Prozesse', body.filters.processes, s.gapProcess, 'gapProcess', (p) => [p.key, p.name]),
            select('alle Schritte', body.filters.steps, s.gapStep, 'gapStep'),
            select('alle Typen', body.filters.types, s.gapType, 'gapType', (t) => [t, page.typeLabel(t)]),
            select('alle Verantwortlichen', body.filters.owners, s.gapOwner, 'gapOwner'),
        ]);
    }

    /** The choice of one row: a suggestion, "andere…" (picker), or why nothing can be chosen here. */
    function choiceCell(row, page, chosen) {
        const C = root.ViewsChange;
        if (row.kind === 'rule') return el('span', { className: 'views-muted', text: 'Regel: an der Quelle beheben (KB-Regelbericht)' });
        if (!row.edit || !row.edit.routable) return el('span', { className: 'views-lock', text: `🔒 ${C.lockText(row.edit)}` });
        if (!row.edit.can_add) return el('span', { className: 'views-muted', text: 'über „Bearbeiten“ im Plan' });
        const sel = el('select', { 'aria-label': 'Vorschlag', onchange: (e) => {
            if (e.target.value === '__pick__') {
                e.target.value = chosen.get(row) || '';
                new C.Picker(page, { process: row.process, node: row.instance, where: row.where, position: row.position,
                    artefact: { type: row.type, path: row.path }, mode: 'add' }).open();
                return;
            }
            chosen.set(row, e.target.value);
        } });
        sel.appendChild(el('option', { value: '', text: row.candidates.length ? '— kein Vorschlag gewählt —' : (row.no_rule ? 'keine Regel: andere wählen' : 'keine Vorschläge') }));
        row.candidates.forEach((c, i) => sel.appendChild(el('option', { value: c.target, selected: i === 0,
            text: `${c.target} (Punkte ${c.score})` })));
        sel.appendChild(el('option', { value: '__pick__', text: 'andere…' }));
        if (row.candidates.length) chosen.set(row, row.candidates[0].target);
        const top = row.candidates[0];
        return el('div', {}, [sel, top ? el('div', { className: 'views-muted', text: top.reason }) : null]);
    }

    function payload(row, target) {
        return { process: row.process, node: row.instance, where: row.where, position: row.position, kind: 'add', target };
    }

    function render(box, body, page) {
        box.appendChild(el('h2', { text: 'Lücken' }));
        box.appendChild(el('p', { className: 'views-muted', text:
            `${body.rows.length} von ${body.total} Lücken · Vorschläge aus KnowledgeBase, Stand ${body.suggestions_as_of || '?'} · ` +
            `Regeln aus dem KB-Regelbericht, Stand ${body.kb_report_as_of || '?'}` }));
        box.appendChild(filterBar(body, page));
        const chosen = new Map();
        const picked = new Set();
        const result = el('div', { className: 'views-change-result' });
        const accept = async (rows) => {
            clear(result);
            const items = rows.filter((r) => chosen.get(r)).map((r) => payload(r, chosen.get(r)));
            if (!items.length) { result.appendChild(el('p', { className: 'views-error', text: 'Keine Zeile mit gewähltem Vorschlag.' })); return; }
            try {
                const r = await page.changes.add(items);
                result.appendChild(el('p', { className: 'views-ok', text: `${r.added.length} übernommen; Änderungssatz: ${r.count}.` }));
                (r.refused || []).forEach((x) => result.appendChild(el('p', { className: 'views-error', text: `${rows[x.index] ? rows[x.index].key : ''}: ${x.error}` })));
            } catch (err) {
                result.appendChild(el('p', { className: 'views-error', text: err.message }));
            }
        };
        const table = el('table', { className: 'views-table views-gaps' });
        table.appendChild(el('tr', {}, ['', 'Instanz', 'Schritt', 'Was fehlt', 'Vorschlag', 'Verantwortlich', ''].map((h) => el('th', { text: h }))));
        body.rows.forEach((row) => {
            const selectable = row.kind === 'missing' && row.edit && row.edit.routable && row.edit.can_add;
            table.appendChild(el('tr', { className: row.kind === 'rule' ? 'views-gaps-rule' : '' }, [
                el('td', {}, [selectable ? el('input', { type: 'checkbox', 'aria-label': 'auswählen',
                    onchange: (e) => (e.target.checked ? picked.add(row) : picked.delete(row)) }) : null]),
                el('td', {}, [el('button', { type: 'button', className: 'views-link', title: 'Ablauf öffnen',
                    onclick: () => { page.state.process = row.process; page.select(row.instance, 'trace'); } },
                [el('span', { className: 'views-tech', text: row.key || '' }), el('span', { text: ` ${row.name}` })])]),
                el('td', { text: row.step || '—' }),
                el('td', {}, row.kind === 'rule'
                    ? [el('strong', { text: row.rule }), el('div', { className: 'views-muted', text: row.why })]
                    : [el('strong', { text: page.typeLabel(row.type) }), el('span', { className: 'views-tech', text: ` ${row.path}` }),
                        el('div', { className: 'views-muted', text: row.why })]),
                el('td', {}, [choiceCell(row, page, chosen)]),
                el('td', { text: row.owner || '—' }),
                el('td', {}, [selectable ? el('button', { type: 'button', className: 'views-button views-button-small', text: 'Übernehmen',
                    onclick: () => accept([row]) }) : null]),
            ]));
        });
        box.appendChild(el('div', { className: 'views-actions' }, [
            el('button', { type: 'button', className: 'views-button', text: 'Vorschläge übernehmen (ausgewählte)',
                onclick: () => accept(body.rows.filter((r) => picked.has(r))) }),
        ]));
        box.appendChild(result);
        box.appendChild(table);
    }

    root.ViewsGaps = { render, payload };
}(typeof window !== 'undefined' ? window : globalThis));
