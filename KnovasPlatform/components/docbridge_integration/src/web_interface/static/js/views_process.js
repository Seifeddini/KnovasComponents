/* Ansichten, render kinds "trace" and "board": a process drawn from its definition.
 *
 * TRACE: one instance; lanes left to right are the steps in order, each showing
 * what its declared paths reach; a context lane across the top. Missing required
 * artefacts and unmet exit criteria of the current step are flagged with the
 * reason. BOARD: all instances; columns are steps, a card opens its TRACE.
 */
(function (root) {
    'use strict';
    const { el, swatch, chipLabel } = root.ViewsCore;

    const STATE_LABEL = { current: 'aktuell', done: 'erledigt', future: 'offen', resting: 'Ruhezustand', unknown: '' };

    function itemButton(page, item) {
        return el('button', { type: 'button', className: 'views-link', title: 'Dossier öffnen',
            onclick: () => page.openDossier(item.id) }, [swatch(item.type, page.overrides), el('span', { text: item.name }),
            item.status ? el('span', { className: 'views-status', dataset: { status: item.status }, text: item.status }) : null]);
    }

    function artefactBlock(page, a, where, position, flagged) {
        const missing = flagged && a.missing;
        const head = el('div', { className: 'views-art-head' }, [
            el('strong', { text: page.typeLabel(a.type) }),
            el('span', { className: 'views-tech', text: ` ${a.path}` }),
            a.required ? el('span', { className: 'views-badge', text: 'Pflicht' }) : null,
        ]);
        const box = el('div', { className: `views-art${missing ? ' views-art-missing' : ''}${a.count ? '' : ' views-art-empty'}` }, [head]);
        if (missing) {
            const alt = a.alternative ? ` und keine Alternative (${a.alternative.type} ${a.alternative.path})` : '';
            box.appendChild(el('p', { className: 'views-flag', text: `Fehlt: ${a.count} von ${a.min_count}${alt}.${a.note ? ` (${a.note})` : ''}` }));
        }
        if (a.chip) {
            box.appendChild(el('button', { type: 'button', className: 'views-chip', onclick: () => page.openTraceItems(where, position, a) },
                [swatch(a.type, page.overrides), el('span', { text: chipLabel({ count: a.chip.count, breakdown: a.chip.breakdown, type: a.type }, page.typeLabel(a.type)) })]));
        } else if (a.items && a.items.length) {
            box.appendChild(el('ul', { className: 'views-list' }, a.items.map((i) => el('li', {}, [itemButton(page, i)]))));
        } else if (!missing) {
            box.appendChild(el('p', { className: 'views-muted', text: a.verified === false ? 'noch keine Kante dieser Form im Wissensnetz' : '—' }));
        }
        if (a.truncated) box.appendChild(el('p', { className: 'views-muted', text: 'gekürzt (Obergrenze erreicht)' }));
        return box;
    }

    function exitList(lane) {
        if (!lane.exit_criteria.length) return null;
        return el('ul', { className: 'views-exit' }, lane.exit_criteria.map((c) => {
            const mark = c.met === true ? '✓' : c.met === false ? '✗' : '·';
            const src = c.source === 'kb-report' ? ` — aus KB-Regelbericht, Stand ${c.as_of}` : '';
            return el('li', { className: c.met === false ? 'views-flag' : '' },
                [`${mark} ${c.id}`, c.why ? el('span', { className: 'views-tech', text: `: ${c.why}` }) : null,
                    src ? el('span', { className: 'views-tech', text: src }) : null]);
        }));
    }

    function renderTrace(box, d, page) {
        const head = el('div', { className: 'views-trace-head' }, [
            el('h2', { text: d.instance.name }),
            (d.sections || []).join(',') === 'context' ? el('span', { className: 'views-badge', text: 'nur Kontext' }) : null,
            el('p', { className: 'views-muted', text: [d.instance.key, d.process.name,
                d.instance.owner ? `verantwortlich ${d.instance.owner}` : null,
                d.current.step ? `seit ${d.current.since || '?'} in „${d.current.step}“ (${d.current.days ?? '?'} Tage)` : 'kein aktueller Schritt'].filter(Boolean).join(' · ') }),
            el('button', { type: 'button', className: 'views-button', text: 'Im Graph zeigen', onclick: () => page.center(d.instance.id) }),
        ]);
        box.appendChild(head);
        if (d.missing.length || d.unmet.length) {
            const parts = [];
            if (d.missing.length) parts.push(d.missing.length === 1 ? '1 Pflicht-Artefakt fehlt' : `${d.missing.length} Pflicht-Artefakte fehlen`);
            if (d.unmet.length) parts.push(d.unmet.length === 1 ? '1 Ausgangskriterium nicht erfüllt' : `${d.unmet.length} Ausgangskriterien nicht erfüllt`);
            box.appendChild(el('p', { className: 'views-flag views-summary', text: `Im aktuellen Schritt: ${parts.join(', ')}.` }));
        }
        const ctx = el('section', { className: 'views-context' }, [el('h3', { text: 'Kontext' })]);
        const ctxRow = el('div', { className: 'views-context-row' });
        d.context.forEach((a, i) => { if (!page.isHidden(a.type)) ctxRow.appendChild(artefactBlock(page, a, 'context', i, false)); });
        ctx.appendChild(ctxRow);
        box.appendChild(ctx);

        (d.skipped || []).forEach((s) => ctxRow.appendChild(el('p', { className: 'views-muted', text: `${s.type} ${s.path}: ${s.why}` })));
        if ((d.sections || []).join(',') === 'context') return;
        const lanes = el('div', { className: 'views-lanes' });
        d.lanes.forEach((lane) => {
            const flagged = lane.state === 'current';
            lanes.appendChild(el('section', { className: `views-lane views-lane-${lane.state}` }, [
                el('header', {}, [
                    el('span', { className: 'views-lane-order', text: String(lane.order) }),
                    el('strong', { text: lane.name }),
                    STATE_LABEL[lane.state] ? el('span', { className: 'views-badge', text: STATE_LABEL[lane.state] }) : null,
                    lane.entered ? el('div', { className: 'views-tech', text: `betreten ${lane.entered}` }) : null,
                ]),
                lane.description ? el('p', { className: 'views-lane-desc', text: lane.description }) : null,
                ...lane.artefacts.map((a, i) => (page.isHidden(a.type) ? null : artefactBlock(page, a, lane.id, i, flagged))),
                exitList(lane),
            ]));
        });
        box.appendChild(lanes);
        const current = lanes.querySelector && lanes.querySelector('.views-lane-current');
        if (current && current.scrollIntoView) current.scrollIntoView({ block: 'nearest', inline: 'center' });
    }

    const SORTS = {
        key: (a, b) => String(a.key || '').localeCompare(String(b.key || '')),
        name: (a, b) => a.name.localeCompare(b.name),
        days: (a, b) => (b.days_in_step || 0) - (a.days_in_step || 0),
        missing: (a, b) => b.missing - a.missing,
        risks: (a, b) => b.open_risks - a.open_risks,
    };

    function renderBoard(box, d, page) {
        const sort = SORTS[(page.viewSettings || {}).sorting];
        if (sort) d.columns.forEach((col) => col.cards.sort(sort));
        box.appendChild(el('p', { className: 'views-muted', text: `${d.total} Instanzen · ${d.process.name}` }));
        const cols = el('div', { className: 'views-board' });
        d.columns.forEach((col) => {
            cols.appendChild(el('section', { className: `views-column${col.resting ? ' views-column-resting' : ''}` }, [
                el('header', {}, [el('strong', { text: col.name }), el('span', { className: 'views-tech', text: ` ${col.cards.length}` })]),
                ...col.cards.map((c) => el('button', { type: 'button', className: 'views-card', onclick: () => page.openTrace(c.id) }, [
                    el('span', { className: 'views-tech', text: c.key || '' }),
                    el('strong', { text: c.name }),
                    el('span', { className: 'views-card-meta', text: [c.owner, c.days_in_step != null ? `${c.days_in_step} T. im Schritt` : null].filter(Boolean).join(' · ') }),
                    el('span', { className: 'views-card-badges' }, [
                        c.missing ? el('span', { className: 'views-badge views-badge-flag', text: `${c.missing} fehlt` }) : null,
                        c.open_risks ? el('span', { className: 'views-badge', text: `${c.open_risks} ${c.open_risks === 1 ? 'Risiko' : 'Risiken'}` }) : null,
                    ]),
                ])),
            ]));
        });
        box.appendChild(cols);
        if (d.unplaced.length) box.appendChild(el('p', { className: 'views-muted', text: `${d.unplaced.length} Instanz(en) ohne bekannten Schritt.` }));
    }

    root.ViewsProcess = { renderTrace, renderBoard };
}(typeof window !== 'undefined' ? window : globalThis));
