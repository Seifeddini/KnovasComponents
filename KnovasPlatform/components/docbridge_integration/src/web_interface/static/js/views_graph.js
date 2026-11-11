/* Ansichten, render kind "graph": one node and its neighbours, decluttered.
 *
 * A concentric layout (the node in the middle, its neighbours around it) instead
 * of a force simulation: the picture is the same every time it is drawn. Fan-outs
 * arrive from the server as chips and are drawn as one labelled box each.
 */
(function (root) {
    'use strict';
    const { typeStyle, chipLabel } = root.ViewsCore;

    /** Cytoscape elements for an /api/views/graph answer. Pure, for tests. */
    function elements(data, overrides) {
        const labels = {};
        (data.legend || []).forEach((t) => { labels[t.name] = t.label; });
        const out = [];
        const rootStyle = typeStyle(data.root.type, overrides);
        out.push({
            group: 'nodes',
            data: { id: data.root.id, label: data.root.name, type: data.root.type,
                color: rootStyle.color, shape: rootStyle.shape, kind: 'root' },
            classes: data.root.meta ? 'root meta' : 'root',
        });
        (data.nodes || []).forEach((n, i) => {
            const style = typeStyle(n.type, overrides);
            const id = `n:${n.id}:${i}`;
            out.push({ group: 'nodes',
                data: { id, nodeId: n.id, label: n.name, type: n.type, color: style.color,
                    shape: style.shape, kind: 'node' },
                classes: n.meta ? 'meta' : '' });
            out.push({ group: 'edges', data: edgeData(data.root.id, id, n.direction, n.relation, i) });
        });
        (data.chips || []).forEach((c, i) => {
            const style = typeStyle(c.type, overrides);
            const id = `chip:${c.key}`;
            out.push({ group: 'nodes',
                data: { id, chipKey: c.key, label: chipLabel(c, labels[c.type]), type: c.type,
                    color: style.color, kind: 'chip', relation: c.relation, direction: c.direction },
                classes: 'chip' });
            out.push({ group: 'edges', data: edgeData(data.root.id, id, c.direction, c.relation_label || c.relation, `c${i}`) });
        });
        return out;
    }

    function edgeData(rootId, otherId, direction, relation, suffix) {
        const outgoing = direction === 'out';
        return { id: `e:${suffix}:${otherId}`, source: outgoing ? rootId : otherId,
            target: outgoing ? otherId : rootId, label: relation };
    }

    const STYLE = [
        { selector: 'node', style: {
            'background-color': 'data(color)', 'shape': 'data(shape)', 'label': 'data(label)',
            'font-family': 'IBM Plex Sans, system-ui, sans-serif', 'font-size': 12,
            'color': '#283647', 'text-valign': 'bottom', 'text-margin-y': 6,
            'text-wrap': 'ellipsis', 'text-max-width': 120, 'width': 22, 'height': 22 } },
        { selector: 'node.root', style: { 'width': 38, 'height': 38, 'font-size': 13,
            'font-weight': 600, 'border-width': 3, 'border-color': '#FDFDFD' } },
        { selector: 'node.meta', style: { 'opacity': 0.55, 'border-style': 'dashed',
            'border-width': 2, 'border-color': '#5A6B80' } },
        { selector: 'node.chip', style: { 'shape': 'round-rectangle', 'background-color': '#F4F6FC',
            'border-width': 2, 'border-color': 'data(color)', 'width': 'label', 'height': 30,
            'padding': 8, 'text-valign': 'center', 'text-margin-y': 0, 'text-max-width': 260,
            'font-size': 11 } },
        { selector: 'node:selected', style: { 'border-width': 3, 'border-color': '#1A45C7' } },
        { selector: 'edge', style: { 'width': 1.2, 'line-color': '#B8C1D1',
            'target-arrow-color': '#B8C1D1', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.8,
            'curve-style': 'bezier', 'label': 'data(label)', 'font-size': 9, 'color': '#5A6B80',
            'text-rotation': 'autorotate', 'text-background-color': '#FDFDFD',
            'text-background-opacity': 1, 'text-background-padding': 2 } },
    ];

    /** The node in the middle, its neighbours on a ring sized by their number,
     *  chips (wide boxes) on a ring of their own outside it. */
    function ringPositions(els) {
        const nodes = els.filter((e) => e.group === 'nodes' && e.data.kind === 'node');
        const chips = els.filter((e) => e.group === 'nodes' && e.data.kind === 'chip');
        const radius = Math.max(150, Math.min(340, nodes.length * 16));
        const out = {};
        const ring = (list, r, offset) => list.forEach((e, i) => {
            const angle = -Math.PI / 2 + offset + (2 * Math.PI * i) / Math.max(list.length, 1);
            out[e.data.id] = { x: Math.round(r * Math.cos(angle)), y: Math.round(r * Math.sin(angle)) };
        });
        ring(nodes, radius, 0);
        ring(chips, nodes.length ? radius + 110 : radius, nodes.length ? Math.PI / Math.max(nodes.length, 1) : 0);
        els.filter((e) => e.data.kind === 'root').forEach((e) => { out[e.data.id] = { x: 0, y: 0 }; });
        return out;
    }

    function render(container, data, handlers, overrides) {
        const els = elements(data, overrides);
        const positions = ringPositions(els);
        const cy = root.cytoscape({
            container, elements: els, style: STYLE,
            layout: { name: 'preset', positions: (n) => positions[n.id()], fit: true, padding: 50 },
            wheelSensitivity: 0.25, minZoom: 0.2, maxZoom: 1.3,
        });
        cy.on('tap', 'node', (evt) => {
            const n = evt.target;
            if (n.data('kind') === 'chip') handlers.chip((data.chips || []).find((c) => `chip:${c.key}` === n.id()));
            else handlers.node(n.data('kind') === 'root' ? data.root.id : n.data('nodeId'));
        });
        cy.on('dbltap', 'node', (evt) => {
            const n = evt.target;
            if (n.data('kind') === 'node') handlers.center(n.data('nodeId'));
        });
        cy.on('mouseover', 'node', (evt) => handlers.hover && handlers.hover(evt.target.data('type'), evt));
        cy.on('mouseout', 'node', () => handlers.hover && handlers.hover(null));
        // A fit or pan moves nodes from under the pointer without a mouseout.
        cy.on('viewport', () => handlers.hover && handlers.hover(null));
        return cy;
    }

    root.ViewsGraph = { elements, render, ringPositions };
}(typeof window !== 'undefined' ? window : globalThis));
