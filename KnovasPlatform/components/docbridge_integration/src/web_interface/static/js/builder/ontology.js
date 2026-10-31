/* Brain Builder -- the ontology, read-only (build plan step B2).
 *
 * Three views of one JSON document (#b-onto-data, builder.ontology_view):
 * a Cytoscape canvas (types as cards, subtype links as thin lines, relations
 * as labelled arrows from domain to range), the type tree, and the problems
 * list. Filters and the side panel are shared by all three. The canvas opens
 * as a tree, root on the left; grouped by level and free are the other two
 * arrangements.
 *
 * The Cytoscape setup follows Cortex (static/js/ontology.js), which this file
 * does not touch: colours come from the design tokens at runtime, the brand
 * fonts are loaded before the first frame, the force layout is deterministic
 * on a fixed area, and a selection fades what it does not touch.
 *
 * Editing (step B3) lives in ontology-edit.js, which registers itself as
 * window.BuilderOntologyEditor before this file starts. This file then draws
 * the editor's panels for the types and relations the company owns, lets a
 * relation be drawn from card to card and a parent be set by dragging in the
 * type tree, and reloads everything from the view the server returns after
 * each edit.
 */
(function () {
    'use strict';

    var dataEl = document.getElementById('b-onto-data');
    var graphEl = document.getElementById('b-onto-graph');
    if (!dataEl || !graphEl || typeof cytoscape !== 'function') return;

    var started = performance.now();
    var editor = window.BuilderOntologyEditor || null;
    var data, nodesById, relationsById, problemsById, childrenOf;

    function index(next) {
        data = next;
        nodesById = {};
        data.nodes.forEach(function (n) { nodesById[n.id] = n; });
        relationsById = {};
        data.relations.forEach(function (r) { relationsById[r.id] = r; });
        problemsById = {};
        data.problems.forEach(function (p) { problemsById[p.id] = p; });
        /* Children in tree order: data.nodes is depth first, children by name. */
        childrenOf = {};
        data.nodes.forEach(function (n) {
            if (n.parent && nodesById[n.parent]) (childrenOf[n.parent] = childrenOf[n.parent] || []).push(n.id);
        });
        below = null;
    }

    var LEVELS = { core: 'Kern', pack: 'Paket', company: 'Firma' };
    /* builder.ontology_view's name for the attributes every type carries. */
    var COMMON = 'Alle Typen';
    var CARD_W = 168, CARD_H = 56, GAP = 24, GROUP_GAP = 96;
    var TREE_COL = CARD_W + 72, TREE_ROW = CARD_H + 16, TREE_MIN_ZOOM = 0.8;
    var below = null;
    index(JSON.parse(dataEl.textContent));

    /* ---- helpers ------------------------------------------------------ */

    function token(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }
    /* Cytoscape refuses quotes in a font stack; the canvas does not need them. */
    function fontToken(name) { return token(name).replace(/['"]/g, ''); }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    function tag(text, tone) {
        return el('span', 'b-tag' + (tone ? ' b-tag--' + tone : ''), text);
    }

    function seriousProblems(node) {
        return (node.problems || []).filter(function (id) {
            return problemsById[id] && problemsById[id].severity !== 'info';
        });
    }

    function prefixes(node) {
        return (node.id_prefixes || []).map(function (p) {
            return p.replace(/^\^/, '').replace(/[\\$].*$/, '').replace(/\[.*$/, '');
        }).filter(Boolean);
    }

    function displayName(node) {
        return node.name;
    }

    /* ---- filters ------------------------------------------------------ */

    var filters = { search: '', levels: { core: true, pack: true, company: true }, pack: '', meta: '', problems: false };

    function matches(node) {
        if (!filters.levels[node.level]) return false;
        if (filters.pack === '-' && node.pack) return false;
        if (filters.pack && filters.pack !== '-' && node.pack !== filters.pack) return false;
        if (filters.meta === 'yes' && !node.meta) return false;
        if (filters.meta === 'no' && node.meta) return false;
        if (filters.problems && !seriousProblems(node).length) return false;
        if (filters.search) {
            var q = filters.search.toLowerCase();
            var hay = [node.name, node.key, node.label.de || '', node.label.en || ''].concat(prefixes(node));
            if (!hay.some(function (s) { return String(s).toLowerCase().indexOf(q) === 0 || String(s).toLowerCase().indexOf(' ' + q) >= 0; })) {
                return false;
            }
        }
        return true;
    }

    function readFilters() {
        var form = document.querySelector('[data-onto-filters]');
        filters.search = form.querySelector('[data-filter="search"]').value.trim();
        Array.prototype.forEach.call(form.querySelectorAll('[data-filter="level"]'), function (box) {
            filters.levels[box.value] = box.checked;
        });
        filters.pack = form.querySelector('[data-filter="pack"]').value;
        filters.meta = form.querySelector('[data-filter="meta"]').value;
        filters.problems = form.querySelector('[data-filter="problems"]').checked;
    }

    /* ---- canvas ------------------------------------------------------- */

    function packName(id) {
        var pack = (data.packs || []).concat(data.term_packs || []).find(function (p) { return p.id === id; });
        return pack ? (pack.name.de || pack.id) : id;
    }

    function cardLabel(node) {
        var parts = [node.level === 'pack' && node.pack ? 'Paket ' + packName(node.pack) : (LEVELS[node.level] || node.level)];
        if (node.source === 'model') parts.push(node.attribute_count + (node.attribute_count === 1 ? ' Attribut' : ' Attribute'));
        if (node.meta) parts.push('◆ Meta');
        if (node.retired) parts.push('stillgelegt');
        return displayName(node) + '\n' + parts.join(' · ');
    }

    function elements() {
        var nodes = [], edges = [];
        ['core', 'pack', 'company'].forEach(function (level) {
            nodes.push({ group: 'nodes', data: { id: 'group:' + level, label: LEVELS[level] }, classes: 'group' });
        });
        data.nodes.forEach(function (n) {
            var classes = ['type', 'level-' + n.level];
            if (n.source !== 'model') classes.push('reference');
            if (n.meta) classes.push('meta');
            if (n.retired) classes.push('retired');
            if (n.editable) classes.push('editable');
            if (seriousProblems(n).length) classes.push('problem');
            nodes.push({ group: 'nodes', data: { id: n.id, label: cardLabel(n), level: n.level }, classes: classes.join(' ') });
            if (n.parent && nodesById[n.parent]) {
                edges.push({ group: 'edges', data: { id: 'sub:' + n.id, source: n.id, target: n.parent }, classes: 'subtype' });
            }
        });
        data.relations.forEach(function (r) {
            r.domain.forEach(function (d) {
                r.range.forEach(function (t) {
                    edges.push({ group: 'edges', data: { id: r.id + '|' + d + '|' + t, source: d, target: t, relation: r.id, label: r.key },
                                 classes: 'relation' + (d === t ? ' loop' : '') + (r.retired ? ' retired' : '') });
                });
            });
        });
        return nodes.concat(edges);
    }

    function style() {
        var sans = fontToken('--font-sans') || 'IBM Plex Sans, sans-serif';
        return [
            { selector: 'node.type', style: {
                'shape': 'round-rectangle', 'width': CARD_W, 'height': CARD_H,
                'background-color': token('--color-surface'),
                'border-width': 2, 'border-color': token('--color-accent'),
                'label': 'data(label)', 'text-wrap': 'wrap', 'text-max-width': CARD_W - 16,
                'text-valign': 'center', 'text-halign': 'center',
                'font-family': sans, 'font-size': 12, 'line-height': 1.4, 'font-weight': 500,
                'color': token('--color-text'), 'min-zoomed-font-size': 6,
            } },
            { selector: 'node.level-core', style: {
                'background-color': token('--color-surface-sunken'), 'border-color': token('--color-border-strong'),
            } },
            { selector: 'node.level-pack', style: { 'background-color': token('--color-accent-soft') } },
            { selector: 'node.reference', style: {
                'border-style': 'dashed', 'border-width': 1.5, 'color': token('--color-text-muted'),
            } },
            { selector: 'node.meta', style: { 'border-style': 'double', 'border-width': 4 } },
            { selector: 'node.problem', style: { 'border-color': token('--color-critical'), 'border-width': 3 } },
            { selector: 'node.retired', style: { 'border-style': 'dashed', 'opacity': 0.55 } },
            { selector: 'edge.relation.retired', style: { 'line-style': 'dashed' } },
            { selector: 'node.connect-source', style: { 'border-color': token('--color-focus'), 'border-width': 4 } },
            { selector: 'node.connect-target', style: { 'border-color': token('--color-accent-hover'), 'border-width': 4 } },
            { selector: 'node.ghost', style: { 'width': 1, 'height': 1, 'opacity': 0, 'events': 'no' } },
            { selector: 'edge.ghost', style: {
                'width': 2, 'line-color': token('--color-focus'), 'target-arrow-color': token('--color-focus'),
                'target-arrow-shape': 'triangle', 'line-style': 'dashed', 'curve-style': 'straight', 'events': 'no',
            } },
            { selector: 'node.group', style: {
                'shape': 'round-rectangle', 'background-color': token('--color-surface'), 'background-opacity': 0.55,
                'border-width': 1, 'border-color': token('--color-border'), 'border-style': 'solid',
                'label': 'data(label)', 'text-valign': 'top', 'text-halign': 'left', 'text-margin-x': 16, 'text-margin-y': -8,
                'font-family': sans, 'font-size': 16, 'font-weight': 600, 'color': token('--color-text-muted'),
                'padding': 24,
            } },
            { selector: 'node.group:childless', style: { 'display': 'none' } },
            { selector: 'node.type:selected', style: {
                'border-color': token('--color-focus'), 'border-width': 4, 'overlay-opacity': 0,
            } },
            { selector: 'node.hovered', style: { 'border-color': token('--color-accent-hover') } },
            { selector: 'edge.subtype', style: {
                'width': 1, 'line-color': token('--color-border-strong'), 'curve-style': 'straight',
                'target-arrow-shape': 'none', 'opacity': 0.8,
            } },
            { selector: 'edge.relation', style: {
                'width': 1.5, 'line-color': token('--color-accent'), 'target-arrow-color': token('--color-accent'),
                // Quiet until something is selected: 400 arrows at once are a
                // texture, not information. A selection brings its own forward.
                'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'opacity': 0.22,
                'label': 'data(label)', 'font-family': sans, 'font-size': 11, 'color': token('--color-text-muted'),
                'text-rotation': 'autorotate', 'text-background-color': token('--color-surface'),
                'text-background-opacity': 0.9, 'text-background-padding': 2, 'min-zoomed-font-size': 10,
            } },
            /* The tree: elbow lines from parent to child; relations wait for a selection. */
            { selector: 'edge.subtype.tree', style: {
                'curve-style': 'taxi', 'taxi-direction': 'horizontal', 'taxi-turn': 36, 'taxi-turn-min-distance': 8,
                'width': 1.25, 'opacity': 1,
            } },
            { selector: 'edge.relation.tree', style: { 'opacity': 0, 'events': 'no' } },
            { selector: 'edge.loop', style: { 'loop-direction': '0deg', 'loop-sweep': '-40deg', 'control-point-step-size': 48 } },
            { selector: 'edge.relation.focus', style: { 'opacity': 1, 'width': 2.5, 'z-index': 10, 'events': 'yes' } },
            { selector: '.faded', style: { 'opacity': 0.15, 'text-opacity': 0 } },
            { selector: 'node.type.faded', style: { 'opacity': 0.3, 'text-opacity': 0.3 } },
            { selector: 'edge.relation.tree.faded', style: { 'opacity': 0 } },
        ];
    }

    /* Grouped: one box per level, the types inside in tree order. */
    function groupedPositions() {
        var columns = { core: 6, pack: 4, company: 2 };
        var positions = {};
        var x0 = 0;
        ['core', 'pack', 'company'].forEach(function (level) {
            var members = data.nodes.filter(function (n) { return n.level === level; });
            if (!members.length) return;
            var cols = Math.min(columns[level], Math.max(1, Math.ceil(Math.sqrt(members.length))));
            members.forEach(function (n, i) {
                positions[n.id] = { x: x0 + (i % cols) * (CARD_W + GAP), y: Math.floor(i / cols) * (CARD_H + GAP) };
            });
            x0 += cols * (CARD_W + GAP) + GROUP_GAP;
        });
        return positions;
    }

    /* Tree: depth to the right, one row per leaf, a parent centred on its
     * children. Only what the filters show takes room; a type whose parent is
     * hidden starts a tree of its own. */
    function treePositions(visible) {
        var shown = function (id) { return !visible || visible[id]; };
        var positions = {};
        var row = 0;
        function place(id, depth) {
            positions[id] = null;  // placed: a parent cycle stops here
            var kids = (childrenOf[id] || []).filter(function (k) { return shown(k) && !(k in positions); });
            var y;
            if (!kids.length) {
                y = row++ * TREE_ROW;
            } else {
                var ys = kids.map(function (k) { return place(k, depth + 1); });
                y = (ys[0] + ys[ys.length - 1]) / 2;
            }
            positions[id] = { x: depth * TREE_COL, y: y };
            return y;
        }
        data.nodes.forEach(function (n) {
            if (!shown(n.id) || n.id in positions) return;
            var parentShown = n.parent && nodesById[n.parent] && shown(n.parent);
            if (parentShown) return;
            if (row) row += 0.5;  // a little air between separate trees
            place(n.id, 0);
        });
        // Types caught in a parent cycle have a shown parent but no root above them.
        data.nodes.forEach(function (n) {
            if (shown(n.id) && !(n.id in positions)) { row += 0.5; place(n.id, 0); }
        });
        return positions;
    }

    var cy = null;
    var layoutMode = 'tree';
    var currentVisible = null;  // id -> shown by the filters; null before the first pass

    function applyLayoutParents(mode) {
        cy.batch(function () {
            cy.nodes('.type').forEach(function (node) {
                var wanted = mode === 'grouped' ? 'group:' + node.data('level') : null;
                var current = node.parent().length ? node.parent().id() : null;
                if (current !== wanted) node.move({ parent: wanted });
            });
            cy.edges()[mode === 'tree' ? 'addClass' : 'removeClass']('tree');
        });
    }

    function applyLayout(mode, options) {
        layoutMode = mode;
        applyLayoutParents(mode);
        if (mode === 'tree') {
            var placed = treePositions(currentVisible);
            cy.nodes('.type').positions(function (node) { return placed[node.id()] || node.position(); });
        } else if (mode === 'grouped') {
            var positions = groupedPositions();
            cy.nodes('.type').positions(function (node) { return positions[node.id()] || { x: 0, y: 0 }; });
        } else {
            cy.elements('node.type, edge').layout({
                name: 'cose', animate: false, randomize: false, fit: false, padding: 40,
                boundingBox: { x1: 0, y1: 0, w: 2400, h: 1600 },
                nodeDimensionsIncludeLabels: false,
                idealEdgeLength: function (edge) { return edge.hasClass('subtype') ? 120 : 220; },
                nodeRepulsion: function () { return 90000; },
                edgeElasticity: function (edge) { return edge.hasClass('subtype') ? 200 : 40; },
                gravity: 1.2, numIter: 1200,
            }).run();
        }
        if (!(options && options.keepView)) fit();
    }

    function fit(eles) {
        var whole = !(eles && eles.length);
        cy.fit(whole ? cy.elements(':visible') : eles, 40);
        // A whole tree fitted into the screen is too small to read: keep the
        // cards legible and start at the root, top left.
        if (whole && layoutMode === 'tree' && cy.zoom() < TREE_MIN_ZOOM) {
            var box = cy.elements(':visible').boundingBox();
            cy.zoom(TREE_MIN_ZOOM);
            cy.pan({ x: 40 - box.x1 * TREE_MIN_ZOOM, y: 88 - box.y1 * TREE_MIN_ZOOM });  // below the toolbar
        }
    }

    function buildCanvas() {
        cy = cytoscape({
            container: graphEl,
            elements: elements(),
            style: style(),
            layout: { name: 'preset' },
            minZoom: 0.08, maxZoom: 3, wheelSensitivity: 0.2,
            boxSelectionEnabled: false, autoungrabify: false,
        });
        applyLayout('tree');

        cy.on('tap', 'node.type', function (evt) { select(evt.target.id(), { from: 'canvas' }); });
        cy.on('tap', 'edge.relation', function (evt) { selectRelation(evt.target.data('relation')); });
        cy.on('tap', function (evt) { if (evt.target === cy) clearSelection(); });
        cy.on('mouseover', 'node.type', function (evt) { evt.target.addClass('hovered'); graphEl.style.cursor = 'pointer'; });
        cy.on('mouseout', 'node.type', function (evt) { evt.target.removeClass('hovered'); graphEl.style.cursor = ''; });
        cy.on('dbltap', function (evt) { if (evt.target === cy) fit(); });
        if (editor) wireConnect();

        // Theme switches re-read the tokens; the canvas draws with colours, not CSS.
        var restyle = function () { cy.style(style()); };
        new MutationObserver(restyle).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
        if (window.matchMedia) {
            var media = window.matchMedia('(prefers-color-scheme: dark)');
            if (media.addEventListener) media.addEventListener('change', restyle);
        }
    }

    /* ---- drawing a relation from card to card -------------------------- */

    var connectMode = false;
    var connect = null;  // {source, ghost, edge}

    function setConnectMode(on) {
        connectMode = !!on;
        cy.autoungrabify(connectMode);
        cy.userPanningEnabled(!connectMode);
        graphEl.classList.toggle('is-connecting', connectMode);
        var button = document.querySelector('[data-onto-connect]');
        if (button) button.setAttribute('aria-pressed', String(connectMode));
    }

    function nodeAt(position) {
        var hit = null;
        cy.nodes('.type:visible').forEach(function (n) {
            var box = n.boundingBox();
            if (position.x >= box.x1 && position.x <= box.x2 && position.y >= box.y1 && position.y <= box.y2) hit = n;
        });
        return hit;
    }

    function endConnect() {
        if (!connect) return;
        cy.remove(connect.ghost);
        cy.nodes().removeClass('connect-source connect-target');
        connect = null;
    }

    function wireConnect() {
        // Draw: in the connect mode, or with Shift held, press on a card and
        // release on another (or the same) card.
        cy.on('tapstart', 'node.type', function (evt) {
            var shift = evt.originalEvent && evt.originalEvent.shiftKey;
            if (!connectMode && !shift) return;
            if (shift && !connectMode) { cy.autoungrabify(true); cy.userPanningEnabled(false); }
            var source = evt.target;
            var ghost = cy.add([
                { group: 'nodes', data: { id: 'ghost:node' }, position: Object.assign({}, evt.position), classes: 'ghost' },
                { group: 'edges', data: { id: 'ghost:edge', source: source.id(), target: 'ghost:node' }, classes: 'ghost' },
            ]);
            source.addClass('connect-source');
            connect = { source: source, ghost: ghost };
        });
        cy.on('tapdrag', function (evt) {
            if (!connect) return;
            cy.getElementById('ghost:node').position(evt.position);
            cy.nodes('.type').removeClass('connect-target');
            var over = nodeAt(evt.position);
            if (over) over.addClass('connect-target');
        });
        cy.on('tapend', function (evt) {
            if (!connect) return;
            var source = connect.source;
            var target = nodeAt(evt.position);
            endConnect();
            if (!connectMode) { cy.autoungrabify(false); cy.userPanningEnabled(true); }
            if (target) editor.drawRelation(nodesById[source.id()], nodesById[target.id()]);
        });
    }

    function applyFiltersToCanvas() {
        var visible = {};
        data.nodes.forEach(function (n) { visible[n.id] = matches(n); });
        cy.batch(function () {
            cy.nodes('.type').forEach(function (node) { node.style('display', visible[node.id()] ? 'element' : 'none'); });
        });
        var shown = data.nodes.filter(function (n) { return visible[n.id]; });
        document.querySelector('[data-onto-canvas-empty]').hidden = shown.length > 0;
        var types = shown.filter(function (n) { return n.source === 'model'; }).length;
        document.querySelector('[data-onto-count]').textContent =
            types + ' von ' + data.counts.types + ' Typen' + (shown.length > types ? ', ' + (shown.length - types) + ' Bezugsklassen' : '');
        return visible;
    }

    /* ---- selection and side panel ------------------------------------- */

    var panel = document.getElementById('b-onto-panel');
    var panelBody = panel.querySelector('[data-onto-panel-body]');
    var selectedId = null;

    function focusOn(eles) {
        cy.elements().removeClass('faded focus');
        // Nothing drawn to focus on (a relation without domain or range): fade nothing.
        if (!eles || !eles.length) return;
        cy.elements().not(eles).addClass('faded');
        eles.filter('edge.relation').addClass('focus');
    }

    function clearSelection() {
        selectedId = null;
        cy.elements().unselect();
        focusOn(null);
        panel.hidden = true;
        markTree(null);
    }

    function section(title) {
        var box = el('section', 'b-panel-section');
        box.appendChild(el('h5', null, title));
        return box;
    }

    function dl(rows) {
        var list = el('dl', 'b-dl');
        rows.forEach(function (row) {
            list.appendChild(el('dt', null, row[0]));
            var dd = el('dd');
            if (row[1]) dd.textContent = row[1];
            else { dd.appendChild(el('span', 'b-missing', 'fehlt')); }
            list.appendChild(dd);
        });
        return list;
    }

    function attributeTable(node) {
        var wrap = el('div', 'b-table-wrap');
        var table = el('table', 'b-table b-table--compact');
        var head = el('thead');
        var hr = el('tr');
        ['Attribut', 'Datentyp', 'Pflicht', 'Werte'].forEach(function (h) {
            var th = el('th', null, h); th.scope = 'col'; hr.appendChild(th);
        });
        head.appendChild(hr);
        table.appendChild(head);
        var body = el('tbody');
        function row(attr, inherited) {
            var tr = el('tr', inherited ? 'is-inherited' : '');
            var name = el('td');
            name.appendChild(el('span', 'b-mono', attr.name));
            if (inherited) { name.appendChild(el('br')); name.appendChild(el('span', 'b-small', 'von ' + attr.from)); }
            tr.appendChild(name);
            var type = el('td', null, attr.datatype || '–');
            if (!attr.held && !inherited) { type.appendChild(document.createTextNode(' ')); type.appendChild(tag('Nur Beschriftung', 'warning')); }
            tr.appendChild(type);
            tr.appendChild(el('td', null, attr.required ? 'Ja' : 'Nein'));
            tr.appendChild(el('td', null, attr.enum_values.length ? attr.enum_values.join(', ') : '–'));
            body.appendChild(tr);
        }
        node.attributes.forEach(function (a) { row(a, false); });
        node.inherited.forEach(function (a) { row(a, true); });
        if (!node.attributes.length && !node.inherited.length) {
            var tr = el('tr'); var td = el('td', 'b-muted', 'Keine Attribute'); td.colSpan = 4; tr.appendChild(td); body.appendChild(tr);
        }
        table.appendChild(body);
        wrap.appendChild(table);
        return wrap;
    }

    function relationList(ids, side) {
        var list = el('ul', 'b-rel-list');
        if (!ids.length) { list.appendChild(el('li', 'b-muted', 'Keine')); return list; }
        ids.forEach(function (id) {
            var r = relationsById[id];
            var other = (side === 'out' ? r.range : r.domain).map(function (o) { return nodesById[o] ? nodesById[o].name : o; });
            var item = el('li');
            var button = el('button', null, r.key);
            button.type = 'button';
            button.addEventListener('click', function () { selectRelation(id); });
            item.appendChild(button);
            item.appendChild(document.createTextNode((side === 'out' ? ' → ' : ' ← ') + (other.join(', ') || 'alle Typen')));
            list.appendChild(item);
        });
        return list;
    }

    function problemList(ids) {
        var box = el('div', 'b-callout b-callout--warning');
        box.appendChild(el('p', 'b-callout-title', ids.length === 1 ? '1 Befund' : ids.length + ' Befunde'));
        var list = el('ul');
        ids.forEach(function (id) { list.appendChild(el('li', null, problemsById[id].message)); });
        box.appendChild(list);
        return box;
    }

    function openPanel(title, key) {
        panel.querySelector('[data-onto-panel-title]').textContent = title;
        panel.querySelector('[data-onto-panel-key]').textContent = key || '';
        panelBody.textContent = '';
        var wasHidden = panel.hidden;
        panel.hidden = false;
        if (wasHidden) panel.querySelector('[data-panel-close]').focus();
    }

    function select(nodeId, options) {
        var node = nodesById[nodeId];
        if (!node) return;
        selectedId = nodeId;
        var cyNode = cy.getElementById(nodeId);
        cy.elements().unselect();
        cyNode.select();
        focusOn(cyNode.closedNeighborhood().union(cyNode.ancestors()));
        if (options && options.center) cy.animate({ center: { eles: cyNode }, zoom: Math.max(cy.zoom(), 0.9) }, { duration: 200 });
        markTree(nodeId);

        openPanel(node.label.de || node.name, node.key + (node.iri && node.iri !== node.key ? ' · ' + node.iri : ''));
        var tags = el('div', 'b-tag-row');
        tags.appendChild(tag(LEVELS[node.level], node.level === 'core' ? null : 'accent'));
        if (node.pack) tags.appendChild(tag('Paket ' + packName(node.pack)));
        if (node.candidate !== null && node.candidate !== undefined) tags.appendChild(tag(node.candidate ? 'Kandidat für ' + packName(node.candidate) : 'Kandidat für ein Paket', 'accent'));
        if (node.meta) tags.appendChild(tag('Meta'));
        if (node.readonly) tags.appendChild(tag('Schreibgeschützt'));
        if (node.source !== 'model') tags.appendChild(tag('Bezugsklasse, nicht im Modell'));
        panelBody.appendChild(tags);

        if (node.retired) tags.appendChild(tag('Stillgelegt', 'warning'));

        var serious = seriousProblems(node);
        if (serious.length) panelBody.appendChild(problemList(serious));

        if (editor && node.editable) { editor.typePanel(node, panelBody); return; }

        var parent = node.parent && nodesById[node.parent];
        var about = section('Beschreibung');
        about.appendChild(dl([
            ['Name (de)', node.label.de], ['Name (en)', node.label.en],
            ['Beschreibung (de)', node.description.de], ['Beschreibung (en)', node.description.en],
            ['Beispiel', node.example], ['Nicht verwechseln mit', node.not_to_confuse],
            ['Elterntyp', parent ? parent.name : ''],
        ]));
        panelBody.appendChild(about);

        var attrs = section('Attribute');
        attrs.appendChild(attributeTable(node));
        panelBody.appendChild(attrs);

        var out = section('Ausgehende Beziehungen');
        out.appendChild(relationList(node.outgoing, 'out'));
        panelBody.appendChild(out);
        var inc = section('Eingehende Beziehungen');
        inc.appendChild(relationList(node.incoming, 'in'));
        panelBody.appendChild(inc);
        if (editor) editor.readonlyNote(node, panelBody);
    }

    function selectRelation(relationId) {
        var r = relationsById[relationId];
        if (!r) return;
        selectedId = relationId;
        var edges = cy.edges().filter(function (e) { return e.data('relation') === relationId; });
        cy.elements().unselect();
        focusOn(edges.union(edges.connectedNodes()));
        markTree(null);
        openPanel(r.label.de || r.key, r.key);
        var tags = el('div', 'b-tag-row');
        tags.appendChild(tag(LEVELS[r.level], r.level === 'core' ? null : 'accent'));
        if (r.inverse) tags.appendChild(tag('Umkehrung: ' + r.inverse));
        panelBody.appendChild(tags);
        if (r.retired) tags.appendChild(tag('Stillgelegt', 'warning'));
        var serious = (r.problems || []).filter(function (id) { return problemsById[id].severity !== 'info'; });
        if (serious.length) panelBody.appendChild(problemList(serious));
        if (editor) { editor.relationPanel(r, panelBody); if (edges.length) fit(edges.union(edges.connectedNodes())); return; }
        var about = section('Beschreibung');
        about.appendChild(dl([
            ['Name (de)', r.label.de], ['Name (en)', r.label.en], ['Lesart', r.sentence],
            ['Von (Definitionsbereich)', r.domain_any ? 'alle Typen' : r.domain_names.join(', ')],
            ['Nach (Wertebereich)', r.range_any ? 'alle Typen' : r.range_names.join(', ')],
        ]));
        panelBody.appendChild(about);
        if (edges.length) fit(edges.union(edges.connectedNodes()));
    }

    /* ---- type tree ---------------------------------------------------- */

    var tree = document.querySelector('[data-onto-tree]');
    var collapsed = {};

    function chip(attr, inherited) {
        var c = el('span', 'b-attr-chip' + (inherited ? ' is-inherited' : ''));
        c.appendChild(document.createTextNode(attr.name));
        c.appendChild(el('span', 'b-attr-type', ':' + (attr.datatype || '?')));
        if (inherited) c.title = 'geerbt von ' + attr.from;
        return c;
    }

    function renderTree(visible) {
        tree.textContent = '';
        var common = (data.nodes.find(function (n) { return n.source === 'model'; }) || { inherited: [] })
            .inherited.filter(function (a) { return a.from === COMMON; });
        if (common.length) {
            var head = el('li', 'b-tree-row');
            head.appendChild(el('span'));
            var main = el('div', 'b-tree-main');
            main.appendChild(el('span', 'b-small b-muted', 'Attribute, die jeder Typ hat'));
            var chips = el('div', 'b-tree-attrs');
            common.forEach(function (a) { chips.appendChild(chip(a, true)); });
            main.appendChild(chips);
            head.appendChild(main);
            tree.appendChild(head);
        }
        var hiddenUnder = null;
        data.nodes.forEach(function (node) {
            if (hiddenUnder !== null && node.depth > hiddenUnder) return;
            hiddenUnder = null;
            // A row stays when it or anything below it passes the filters: the tree keeps its shape.
            if (!visible[node.id] && !hasVisibleBelow(node, visible)) return;
            var li = el('li', 'b-tree-row' + (node.source !== 'model' ? ' is-reference' : ''));
            li.setAttribute('role', 'treeitem');
            li.setAttribute('aria-level', String(node.depth + 1));
            li.style.setProperty('--depth', String(node.depth));
            li.dataset.node = node.id;
            if (node.id === selectedId) li.setAttribute('aria-selected', 'true');
            if (editor) treeDrag(li, node);

            var toggleCell = el('span');
            if (node.children) {
                var toggle = el('button', 'b-button b-button--quiet b-button--icon b-tree-toggle');
                toggle.type = 'button';
                toggle.setAttribute('aria-expanded', String(!collapsed[node.id]));
                toggle.setAttribute('aria-label', (collapsed[node.id] ? 'Aufklappen: ' : 'Zuklappen: ') + node.name);
                toggle.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
                toggle.addEventListener('click', function () {
                    collapsed[node.id] = !collapsed[node.id];
                    renderTree(currentVisible);
                    var again = tree.querySelector('[data-node="' + CSS.escape(node.id) + '"] .b-tree-toggle');
                    if (again) again.focus();
                });
                toggleCell.appendChild(toggle);
                li.setAttribute('aria-expanded', String(!collapsed[node.id]));
            }
            li.appendChild(toggleCell);

            var main = el('div', 'b-tree-main');
            var line = el('div', 'b-tree-line');
            var name = el('button', 'b-tree-name', node.name);
            name.type = 'button';
            name.addEventListener('click', function () { select(node.id, { from: 'tree' }); });
            line.appendChild(name);
            if (node.label.de && node.label.de !== node.name) line.appendChild(el('span', 'b-muted', node.label.de));
            line.appendChild(tag(LEVELS[node.level], node.level === 'core' ? null : 'accent'));
            if (node.meta) line.appendChild(tag('Meta'));
            if (node.level === 'pack' && node.pack) line.appendChild(tag(packName(node.pack)));
            if (node.retired) line.appendChild(tag('Stillgelegt', 'warning'));
            var serious = seriousProblems(node);
            if (serious.length) line.appendChild(tag(serious.length + (serious.length === 1 ? ' Problem' : ' Probleme'), 'critical'));
            main.appendChild(line);
            if (node.source === 'model') {
                // The attributes every type shares are listed once, above the
                // tree; here they would bury what is the type's own.
                var fromAbove = node.inherited.filter(function (a) { return a.from !== COMMON; });
                var shared = node.inherited.length - fromAbove.length;
                var attrs = el('div', 'b-tree-attrs');
                node.attributes.forEach(function (a) { attrs.appendChild(chip(a, false)); });
                fromAbove.forEach(function (a) { attrs.appendChild(chip(a, true)); });
                if (shared) attrs.appendChild(el('span', 'b-attr-chip is-inherited', '+ ' + shared + ' gemeinsame'));
                main.appendChild(attrs);
            }
            li.appendChild(main);
            tree.appendChild(li);
            if (collapsed[node.id]) hiddenUnder = node.depth;
        });
        if (!tree.querySelector('[data-node]')) tree.appendChild(el('li', 'b-tree-row b-muted', 'Kein Typ passt zu den Filtern.'));
    }

    /* Drag a company type onto another row to make that its parent. */
    var dragged = null;
    function treeDrag(li, node) {
        if (node.editable) {
            li.draggable = true;
            li.addEventListener('dragstart', function (event) {
                dragged = node;
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', node.key);
                li.classList.add('is-dragged');
            });
            li.addEventListener('dragend', function () { dragged = null; li.classList.remove('is-dragged'); });
        }
        li.addEventListener('dragover', function (event) {
            if (!dragged || dragged.id === node.id || isBelow(node.id, dragged.id)) return;
            event.preventDefault();
            li.classList.add('is-drop-target');
        });
        li.addEventListener('dragleave', function () { li.classList.remove('is-drop-target'); });
        li.addEventListener('drop', function (event) {
            event.preventDefault();
            li.classList.remove('is-drop-target');
            if (dragged && dragged.id !== node.id) editor.setParent(dragged, node);
        });
    }

    /* Is ``id`` the node ``ancestor`` or somewhere below it? */
    function isBelow(id, ancestor) {
        var current = id;
        while (current) {
            if (current === ancestor) return true;
            current = nodesById[current] && nodesById[current].parent;
        }
        return false;
    }

    function hasVisibleBelow(node, visible) {
        if (!below) {
            below = {};
            data.nodes.forEach(function (n) {
                var p = n.parent;
                while (p) { (below[p] = below[p] || []).push(n.id); p = nodesById[p] && nodesById[p].parent; }
            });
        }
        return (below[node.id] || []).some(function (id) { return visible[id]; });
    }

    function markTree(nodeId) {
        Array.prototype.forEach.call(tree.querySelectorAll('[aria-selected="true"]'), function (row) { row.removeAttribute('aria-selected'); });
        if (nodeId) {
            var row = tree.querySelector('[data-node="' + CSS.escape(nodeId) + '"]');
            if (row) row.setAttribute('aria-selected', 'true');
        }
    }

    /* ---- problems ----------------------------------------------------- */

    var KINDS = {
        parent_unknown: 'Unbekannter Elterntyp',
        parent_cycle: 'Elterntypen im Kreis',
        relation_no_bounds: 'Beziehungen ohne Definitions- oder Wertebereich',
        relation_unknown_type: 'Beziehungen mit unbekanntem Typ',
        type_no_description: 'Typen ohne Beschreibung',
        attribute_not_held: 'Attribute, die das Brain heute nicht als Wert hält',
        name_collision: 'Doppelte Namen',
        datatype_unknown: 'Unbekannte Datentypen',
        enum_no_values: 'Auswahlen ohne Werte',
        entity_ref_no_target: 'Verweise ohne Zieltyp',
        entity_ref_unknown_target: 'Verweise auf unbekannte Typen',
        retired_in_use: 'Stillgelegte Typen, die noch verwendet werden',
        retired_parent: 'Typen unter einem stillgelegten Elterntyp',
        retired_in_relation: 'Beziehungen mit stillgelegten Typen',
        description_incomplete: 'Unvollständige Beschreibungen (Regel 8)',
        attribute_description_incomplete: 'Attribute ohne vollständige Beschreibung (Regel 8)',
    };
    var SEVERITY = { critical: ['Problem', 'critical'], warning: ['Warnung', 'warning'], info: ['Hinweis', null] };

    function renderProblems() {
        var box = document.querySelector('[data-onto-problems]');
        box.textContent = '';
        if (!data.problems.length) {
            var ok = el('div', 'b-callout b-callout--good');
            ok.appendChild(el('p', 'b-callout-title', 'Keine Probleme gefunden.'));
            box.appendChild(ok);
            return;
        }
        var serious = data.problems.filter(function (p) { return p.severity !== 'info'; }).length;
        if (!serious) {
            var calm = el('div', 'b-callout b-callout--good');
            calm.appendChild(el('p', 'b-callout-title', 'Keine Probleme.'));
            calm.appendChild(el('p', null, 'Die Hinweise unten zeigen, was das Brain heute noch nicht abbilden kann. Das Modell behält es trotzdem.'));
            calm.style.marginBottom = 'var(--space-3)';
            box.appendChild(calm);
        }
        var order = ['name_collision', 'parent_unknown', 'parent_cycle', 'retired_in_use', 'datatype_unknown', 'enum_no_values',
                     'entity_ref_no_target', 'entity_ref_unknown_target', 'relation_no_bounds', 'relation_unknown_type',
                     'retired_parent', 'retired_in_relation', 'type_no_description', 'description_incomplete',
                     'attribute_description_incomplete', 'attribute_not_held'];
        var groups = order.filter(function (kind) { return data.problems.some(function (p) { return p.kind === kind; }); });
        groups.forEach(function (kind) {
            var items = data.problems.filter(function (p) { return p.kind === kind; });
            var group = el('details', 'b-problem-group');
            group.open = items[0].severity !== 'info' || groups.length === 1;
            var summary = el('summary');
            summary.appendChild(document.createTextNode(KINDS[kind] || kind));
            var sev = SEVERITY[items[0].severity] || SEVERITY.info;
            summary.appendChild(tag(items.length + ' ' + sev[0] + (items.length === 1 ? '' : (sev[0] === 'Problem' ? 'e' : sev[0] === 'Hinweis' ? 'e' : 'en')), sev[1]));
            group.appendChild(summary);
            var list = el('ul', 'b-problem-list');
            items.forEach(function (p) {
                var li = el('li', 'b-problem');
                li.appendChild(el('p', null, p.message));
                if (p.target_kind === 'type' || p.target_kind === 'relation') {
                    var show = el('button', 'b-button b-button--quiet', 'Auf der Fläche zeigen');
                    show.type = 'button';
                    show.addEventListener('click', function () { showOnCanvas(p); });
                    li.appendChild(show);
                }
                list.appendChild(li);
            });
            group.appendChild(list);
            box.appendChild(group);
        });
    }

    /* ---- candidates --------------------------------------------------- */

    function renderCandidates() {
        var box = document.querySelector('[data-onto-candidates]');
        if (!box) return;
        box.textContent = '';
        var count = document.querySelector('#b-onto-tab-candidates .b-tab-count');
        if (count) count.textContent = String(data.candidates.length);
        if (!data.candidates.length) {
            box.appendChild(el('p', 'b-muted', 'Kein eigener Typ ist als Kandidat für ein Paket markiert. Markieren lässt er sich in seiner Seitenleiste, unter «Paket».'));
            return;
        }
        box.appendChild(el('p', 'b-muted', 'Eigene Typen der Firma, die in ein Branchenpaket gehören könnten. Knovas prüft sie, bevor ein Paket sie aufnimmt.'));
        var list = el('ul', 'b-problem-list');
        data.candidates.forEach(function (c) {
            var li = el('li', 'b-problem');
            var head = el('p');
            head.appendChild(el('strong', null, c.name));
            if (c.label.de) head.appendChild(document.createTextNode(' – ' + c.label.de));
            head.appendChild(document.createTextNode(' '));
            head.appendChild(tag(c.pack ? 'für ' + packName(c.pack) : 'Paket offen', 'accent'));
            li.appendChild(head);
            if (c.reason) li.appendChild(el('p', 'b-small b-muted', c.reason));
            var show = el('button', 'b-button b-button--quiet', 'Auf der Fläche zeigen');
            show.type = 'button';
            show.addEventListener('click', function () { showOnCanvas({ target_kind: 'type', target: c.id }); });
            li.appendChild(show);
            list.appendChild(li);
        });
        box.appendChild(list);
    }

    function showOnCanvas(problem) {
        document.getElementById('b-onto-tab-canvas').click();
        if (problem.target_kind === 'relation') { selectRelation(problem.target); return; }
        var node = nodesById[problem.target];
        if (node && !matches(node)) {
            // The filters hide it: clear them rather than point at nothing.
            document.querySelector('[data-onto-filters]').reset();
            refresh();
        }
        select(problem.target, { center: true });
    }

    /* ---- wiring ------------------------------------------------------- */

    function refresh() {
        readFilters();
        currentVisible = applyFiltersToCanvas();
        renderTree(currentVisible);
        // The tree closes up around what the filters leave.
        if (layoutMode === 'tree') applyLayout('tree');
    }

    var filterForm = document.querySelector('[data-onto-filters]');

    Array.prototype.forEach.call(document.querySelectorAll('[data-onto-view]'), function (tab) {
        tab.addEventListener('click', function () {
            var view = tab.getAttribute('data-onto-view');
            // The filters narrow the canvas and the tree; the problems list is always whole.
            filterForm.hidden = view === 'problems';
            if (view === 'canvas' && cy) { cy.resize(); }
        });
    });
    filterForm.addEventListener('input', refresh);
    filterForm.addEventListener('change', refresh);
    filterForm.addEventListener('submit', function (event) { event.preventDefault(); });
    filterForm.addEventListener('reset', function () { setTimeout(refresh, 0); });

    Array.prototype.forEach.call(document.querySelectorAll('[data-onto-layout]'), function (button) {
        button.addEventListener('click', function () { applyLayout(button.getAttribute('data-onto-layout')); });
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-onto-zoom]'), function (button) {
        button.addEventListener('click', function () {
            var how = button.getAttribute('data-onto-zoom');
            if (how === 'fit') { fit(); return; }
            var center = { x: graphEl.clientWidth / 2, y: graphEl.clientHeight / 2 };
            cy.zoom({ level: cy.zoom() * (how === 'in' ? 1.25 : 0.8), renderedPosition: center });
        });
    });

    panel.querySelector('[data-panel-close]').addEventListener('click', function () {
        cy.elements().unselect();
        focusOn(null);
        markTree(null);
        selectedId = null;
    });

    function start() {
        buildCanvas();
        refresh();
        renderProblems();
        renderCandidates();
        var ready = Math.round(performance.now() - started);
        graphEl.setAttribute('aria-label', 'Fläche mit ' + data.counts.types + ' Typen und ' + data.counts.relations + ' Beziehungen');
        // For the acceptance test: how long from script start to a drawn canvas.
        window.__builderOntology = {
            readyMs: ready,
            types: cy.nodes('.type').filter(function (n) { return nodesById[n.id()].source === 'model'; }).length,
            relations: Object.keys(cy.edges('.relation').reduce(function (seen, e) { seen[e.data('relation')] = 1; return seen; }, {})).length,
            cy: cy,
            select: select,
            selectRelation: selectRelation,
            load: load,
            data: function () { return data; },
            packName: packName,
            setConnectMode: setConnectMode,
        };
        if (editor) editor.start(window.__builderOntology, { el: el, tag: tag, section: section, LEVELS: LEVELS, COMMON: COMMON, isBelow: isBelow });
    }

    /* A new view from the server after an edit: redraw, keep the viewport,
     * and select ``options.select`` (or what was selected, if it still exists). */
    function load(next, options) {
        var keep = selectedId;
        var pan = Object.assign({}, cy.pan()), zoom = cy.zoom();
        var positions = {};
        cy.nodes('.type').forEach(function (n) { positions[n.id()] = Object.assign({}, n.position()); });
        index(next);
        cy.batch(function () {
            cy.elements().remove();
            cy.add(elements());
        });
        if (layoutMode === 'force') {
            var center = { x: (graphEl.clientWidth / 2 - pan.x) / zoom, y: (graphEl.clientHeight / 2 - pan.y) / zoom };
            cy.nodes('.type').positions(function (n) { return positions[n.id()] || center; });
            applyLayoutParents('force');
        }
        readFilters();
        currentVisible = applyFiltersToCanvas();
        if (layoutMode !== 'force') applyLayout(layoutMode, { keepView: true });
        cy.viewport({ zoom: zoom, pan: pan });
        renderTree(currentVisible);
        renderProblems();
        renderCandidates();
        var headTypes = document.querySelector('[data-onto-head-types]');
        if (headTypes) headTypes.textContent = data.counts.types + ' Typen';
        var headRelations = document.querySelector('[data-onto-head-relations]');
        if (headRelations) headRelations.textContent = data.counts.relations + ' Beziehungen';
        var tabCount = document.querySelector('#b-onto-tab-problems .b-tab-count');
        if (tabCount) tabCount.textContent = String(data.problems.length);
        var wanted = (options && options.select) || keep;
        // The same element again: the panel keeps where it was scrolled to.
        var scroll = wanted === keep ? panelBody.scrollTop : 0;
        if (wanted && nodesById[wanted]) select(wanted);
        else if (wanted && relationsById[wanted]) selectRelation(wanted);
        else if (wanted) clearSelection();
        panelBody.scrollTop = scroll;
    }

    // Load the brand fonts first: Cytoscape draws once and does not redraw
    // when a web font arrives later (font-display: swap).
    var fonts = document.fonts && document.fonts.load
        ? Promise.race([
            Promise.all(["500 12px 'IBM Plex Sans'", "600 16px 'IBM Plex Sans'"].map(function (f) { return document.fonts.load(f); })),
            new Promise(function (resolve) { setTimeout(resolve, 1000); }),
        ]).catch(function () {})
        : Promise.resolve();
    fonts.then(start);
})();
