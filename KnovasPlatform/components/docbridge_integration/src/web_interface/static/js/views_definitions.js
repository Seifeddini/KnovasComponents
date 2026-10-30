/* Ansichten, render kind "definitions": the processes and spines the views are
 * drawn from, read-only. Validation errors, gaps and differences are shown to
 * tenant admins only (the server leaves them out for everyone else).
 */
(function (root) {
    'use strict';
    const { el } = root.ViewsCore;

    /** "←describes– Record, →affects→ Component" for a path of hops. */
    function pathText(path) {
        return (path || []).map((h) => (h.direction === 'out'
            ? `–${h.relation}→ ${h.to_type}` : `←${h.relation}– ${h.to_type}`)).join(' ');
    }

    function artefact(a) {
        const bits = [`${a.type}`, el('span', { className: 'views-tech', text: ` ${pathText(a.path)}` })];
        if (a.alternative) bits.push(el('span', { className: 'views-tech', text: ` oder ${a.alternative.type} ${pathText(a.alternative.path)}` }));
        if (a.required) bits.push(el('span', { className: 'views-badge', text: a.min_count > 1 ? `Pflicht ×${a.min_count}` : 'Pflicht' }));
        if (a.verified === false) bits.push(el('span', { className: 'views-badge views-badge-muted', text: 'noch keine Kante dieser Form' }));
        return el('li', {}, bits);
    }

    function messages(title, items, kind) {
        if (!items || !items.length) return null;
        return el('section', { className: `views-messages views-messages-${kind}` }, [
            el('h3', { text: `${title} (${items.length})` }),
            el('ul', {}, items.map((m) => el('li', { text: m }))),
        ]);
    }

    /** ?open=<key> opens one definition, so it can be linked. */
    function openKey() {
        try { return new URLSearchParams(root.location.search).get('open') || ''; } catch (err) { return ''; }
    }

    function render(container, data) {
        const wanted = openKey();
        const page = el('div', { className: 'views-definitions' });
        const add = (node) => { if (node) page.appendChild(node); };
        page.appendChild(el('p', { className: 'views-muted', text: data.source === 'tenant'
            ? 'Quelle: Definitionsknoten des Mandanten.'
            : `Quelle: Definitionsbündel ${(data.pinned && data.pinned.label) || ''} aus KnowledgeBase (der Mandant war nicht lesbar oder nicht gültig).` }));
        add(messages('Fehler', data.errors, 'error'));
        add(messages('Lücken auf dem Mandanten', data.gaps, 'gap'));
        add(messages('Abweichungen Mandant ↔ Bündel', data.differences, 'diff'));

        page.appendChild(el('h2', { text: `Prozesse (${data.processes.length})` }));
        data.processes.forEach((p) => {
            const steps = el('ol', { className: 'views-steps' });
            p.steps.forEach((s) => {
                const parts = [el('strong', { text: s.name || s.id }),
                    s.resting ? el('span', { className: 'views-badge views-badge-muted', text: 'Ruhezustand' }) : null,
                    s.owner_role ? el('span', { className: 'views-tech', text: ` ${s.owner_role}` }) : null];
                if (s.description) parts.push(el('p', { text: s.description }));
                const arts = (s.requires || []).concat(s.produces || []);
                if (arts.length) parts.push(el('ul', { className: 'views-artefacts' }, arts.map(artefact)));
                if ((s.exit_criteria || []).length) parts.push(el('p', { className: 'views-tech', text: `Ausgangskriterien: ${s.exit_criteria.join(', ')}` }));
                steps.appendChild(el('li', {}, parts));
            });
            page.appendChild(el('details', { className: 'views-definition', open: p.key === wanted }, [
                el('summary', {}, [el('strong', { text: p.name }), el('span', { className: 'views-tech', text: ` ${p.key} · ${p.instance_types.join(', ')} · ${p.steps.length} Schritte` })]),
                p.description ? el('p', { text: p.description }) : null,
                (p.context || []).length ? el('div', {}, [el('h4', { text: 'Kontext (über alle Schritte)' }),
                    el('ul', { className: 'views-artefacts' }, p.context.map(artefact))]) : null,
                steps,
            ]));
        });

        page.appendChild(el('h2', { text: `Strukturachsen (${data.spines.length})` }));
        data.spines.forEach((sp) => {
            const chain = (levels) => el('ol', { className: 'views-steps' }, levels.map((l) => {
                const link = l.link_to_parent
                    ? ` · zur Ebene darüber: ${l.link_to_parent.relation} (${l.link_to_parent.direction})`
                    : (l.links_to_parent ? ` · ${Object.entries(l.links_to_parent).map(([t, k]) => `${t}: ${k.relation} (${k.direction})`).join('; ')}` : '');
                return el('li', {}, [el('strong', { text: l.name }),
                    el('span', { className: 'views-tech', text: ` ${l.types.join(', ')}${l.filter ? ` (Schlüssel ${l.filter.key_prefix}…)` : ''}${link}` }),
                    l.verified === false ? el('span', { className: 'views-badge views-badge-muted', text: 'noch keine Kante dieser Form' }) : null]);
            }));
            page.appendChild(el('details', { className: 'views-definition', open: sp.key === wanted }, [
                el('summary', {}, [el('strong', { text: sp.name }), el('span', { className: 'views-tech', text: ` ${sp.key} · ${sp.levels.length} Ebenen` })]),
                sp.description ? el('p', { text: sp.description }) : null,
                chain(sp.levels),
                ...(sp.branches || []).map((b) => el('div', {}, [el('h4', { text: b.name }), b.description ? el('p', { text: b.description }) : null, chain(b.levels)])),
            ]));
        });
        container.appendChild(page);
    }

    root.ViewsDefinitions = { render, pathText };
}(typeof window !== 'undefined' ? window : globalThis));
