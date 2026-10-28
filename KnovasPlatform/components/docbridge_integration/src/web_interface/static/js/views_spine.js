/* Ansichten, render kinds "tree" and "matrix": a spine drawn from its definition.
 *
 * TREE expands level by level (one request per expansion); each node shows its
 * key, name, status and how many children it has at the next level. MATRIX puts
 * two levels against each other; a cell says whether the spine links them.
 */
(function (root) {
    'use strict';
    const { el, clear, swatch, api, hash } = root.ViewsCore;

    const STATUS_COLORS = ['#1a6b4a', '#1A45C7', '#b45309', '#6d28d9', '#0f766e', '#be123c', '#4d7c0f', '#334155'];

    /** A colour per status value: stable, and the same on every page. */
    function statusColor(status) {
        if (!status) return '#B8C1D1';
        return STATUS_COLORS[hash(String(status)) % STATUS_COLORS.length];
    }

    function statusDot(status) {
        return el('span', { className: 'views-dot', style: `background:${statusColor(status)}`, title: status || 'kein Status' });
    }

    function statusLegend(states) {
        if (!states || !states.length) return null;
        return el('p', { className: 'views-status-legend' }, states.map((s) =>
            el('span', {}, [statusDot(s.status), ` ${s.status || 'ohne Status'} (${s.count})`])));
    }

    function nodeRow(page, spine, branch, level, nextLevel, n) {
        const li = el('li', { className: 'views-tree-node' });
        const children = el('ul', { className: 'views-tree', hidden: true });
        let loaded = false;
        const toggle = async () => {
            if (!nextLevel || !n.children) return;
            children.hidden = !children.hidden;
            button.setAttribute('aria-expanded', String(!children.hidden));
            if (loaded || children.hidden) return;
            loaded = true;
            children.appendChild(el('li', { className: 'views-muted', text: 'Lädt…' }));
            try {
                const d = await api('/api/views/tree', { spine, branch, parent: n.id, level: level.id });
                clear(children);
                page.showAnswerMeta(d);
                children.appendChild(el('li', { className: 'views-tree-level' }, [el('span', { className: 'views-tech', text: d.level.name }), statusLegend(d.states)]));
                d.nodes.filter((c) => !page.isHidden || !page.isHidden(c.type))
                    .forEach((c) => children.appendChild(nodeRow(page, spine, branch, d.level, d.next_level, c)));
                if (!d.nodes.length) children.appendChild(el('li', { className: 'views-muted', text: 'keine' }));
            } catch (err) {
                clear(children);
                children.appendChild(el('li', { className: 'views-error', text: err.message }));
            }
        };
        const count = nextLevel ? (n.children ? `${n.children_exact ? '' : 'bis zu '}${n.children} ${nextLevel.name}` : `keine ${nextLevel.name}`) : '';
        const button = el('button', { type: 'button', className: 'views-tree-toggle', 'aria-expanded': 'false',
            disabled: !(nextLevel && n.children), onclick: toggle, text: nextLevel && n.children ? '▸' : '·' });
        li.appendChild(el('div', { className: 'views-tree-row' }, [
            button, statusDot(n.status), swatch(n.type, page.overrides),
            el('button', { type: 'button', className: 'views-link', onclick: () => page.openDossier(n.id) }, [
                n.key ? el('span', { className: 'views-tech', text: n.key }) : null, el('span', { text: n.name })]),
            n.status ? el('span', { className: 'views-status', text: n.status }) : null,
            count ? el('span', { className: 'views-tech', text: count }) : null,
        ]));
        li.appendChild(children);
        li.viewsExpand = async () => { if (!loaded) await toggle(); return children; };
        li.dataset.nodeId = n.id;
        return li;
    }

    function renderTree(box, d, page) {
        box.appendChild(el('h2', { text: d.spine.name }));
        if (d.spine.description) box.appendChild(el('p', { className: 'views-muted', text: d.spine.description }));
        box.appendChild(el('p', { className: 'views-tech', text: d.levels.map((l) => l.name).join(' → ') }));
        box.appendChild(statusLegend(d.states) || el('span'));
        const list = el('ul', { className: 'views-tree views-tree-root' });
        d.nodes.filter((n) => !page.isHidden || !page.isHidden(n.type))
            .forEach((n) => list.appendChild(nodeRow(page, d.spine.key, d.branch, d.level, d.next_level, n)));
        if (!d.nodes.length) list.appendChild(el('li', { className: 'views-muted', text: `Keine Knoten auf der Ebene ${d.level.name}.` }));
        box.appendChild(list);
        expandPath(list, (page.state && page.state.expand) || []);
    }

    /** Open a path of node ids, one level after the other (?expand=a,b,c). */
    async function expandPath(list, ids) {
        let scope = list;
        for (const id of ids) {
            const li = Array.from((scope && scope.children) || []).find((c) => c && c.dataset && c.dataset.nodeId === id);
            if (!li || !li.viewsExpand) return;
            scope = await li.viewsExpand();
        }
    }

    function renderMatrix(box, d, page) {
        if (page.isHidden) {
            const keepRows = d.rows.map((r) => !page.isHidden(r.type));
            const keepCols = d.cols.map((c) => !page.isHidden(c.type));
            d = Object.assign({}, d, {
                rows: d.rows.filter((r, i) => keepRows[i]),
                cols: d.cols.filter((c, j) => keepCols[j]),
                cells: d.cells.filter((r, i) => keepRows[i]).map((line) => line.filter((c, j) => keepCols[j])),
            });
        }
        box.appendChild(el('h2', { text: `${d.spine.name}: ${d.rows_level.name} × ${d.cols_level.name}` }));
        box.appendChild(el('p', { className: 'views-muted', text:
            `${d.rows.length} × ${d.cols.length}; ${d.unlinked_rows.length} Zeile(n) ohne Verbindung.` }));
        const table = el('table', { className: 'views-matrix' });
        const head = el('tr', {}, [el('th', { className: 'views-matrix-corner' })]);
        d.cols.forEach((c) => head.appendChild(el('th', { className: 'views-matrix-col', title: c.name },
            [el('button', { type: 'button', className: 'views-link', onclick: () => page.openDossier(c.id) }, [el('span', { text: c.key || c.name })])])));
        table.appendChild(el('thead', {}, [head]));
        const body = el('tbody');
        d.rows.forEach((r, i) => {
            const tr = el('tr', { className: d.unlinked_rows.includes(r.id) ? 'views-matrix-unlinked' : '' }, [
                el('th', { className: 'views-matrix-row' }, [statusDot(r.status),
                    el('button', { type: 'button', className: 'views-link', onclick: () => page.openDossier(r.id) },
                        [el('span', { text: `${r.key ? `${r.key} · ` : ''}${r.name}` })])]),
            ]);
            d.cells[i].forEach((cell, j) => tr.appendChild(el('td', {
                className: cell.linked ? 'views-matrix-on' : '',
                title: cell.linked ? `${r.name} ↔ ${d.cols[j].name}${cell.status ? ` (${cell.status})` : ''}` : '',
            }, [cell.linked ? statusDot(cell.status) : null])));
            body.appendChild(tr);
        });
        table.appendChild(body);
        box.appendChild(el('div', { className: 'views-matrix-wrap' }, [table]));
    }

    root.ViewsSpine = { renderTree, renderMatrix, statusColor };
}(typeof window !== 'undefined' ? window : globalThis));
