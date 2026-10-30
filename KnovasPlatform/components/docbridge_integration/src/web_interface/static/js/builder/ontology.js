/* Brain Builder -- the ontology, read-only (build plan step B2).
 *
 * Three views of one JSON document (#b-onto-data, builder.ontology_view):
 * a Cytoscape canvas (types as cards, subtype links as thin lines, relations
 * as labelled arrows from domain to range), the type tree, and the problems
 * list. Filters and the side panel are shared by all three.
 *
 * The Cytoscape setup follows Cortex (static/js/ontology.js), which this file
 * does not touch: colours come from the design tokens at runtime, the brand
 * fonts are loaded before the first frame, the force layout is deterministic
 * on a fixed area, and a selection fades what it does not touch.
 */
(function () {
    'use strict';

    var dataEl = document.getElementById('b-onto-data');
    var graphEl = document.getElementById('b-onto-graph');
    if (!dataEl || !graphEl || typeof cytoscape !== 'function') return;

    var started = performance.now();
    var data = JSON.parse(dataEl.textContent);
    var nodesById = {};
    data.nodes.forEach(function (n) { nodesById[n.id] = n; });
    var relationsById = {};
    data.relations.forEach(function (r) { relationsById[r.id] = r; });
    var problemsById = {};
    data.problems.forEach(function (p) { problemsById[p.id] = p; });

    var LEVELS = { core: 'Kern', pack: 'Paket', company: 'Firma' };
    /* builder.ontology_view's name for the attributes every type carries. */
    var COMMON = 'Alle Typen';
    var CARD_W = 168, CARD_H = 56, GAP = 24, GROUP_GAP = 96;

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

    function cardLabel(node) {
        var parts = [LEVELS[node.level] || node.level];
        if (node.source === 'model') parts.push(node.attribute_count + (node.attribute_count === 1 ? ' Attribut' : ' Attribute'));
        if (node.meta) parts.push('◆ Meta');
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
                                 classes: 'relation' + (d === t ? ' loop' : '') });
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
            { selector: 'edge.loop', style: { 'loop-direction': '0deg', 'loop-sweep': '-40deg', 'control-point-step-size': 48 } },
            { selector: 'edge.relation.focus', style: { 'opacity': 1, 'width': 2.5, 'z-index': 10 } },
            { selector: '.faded', style: { 'opacity': 0.15, 'text-opacity': 0 } },
            { selector: 'node.type.faded', style: { 'opacity': 0.3, 'text-opacity': 0.3 } },
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

    var cy = null;
    var layoutMode = 'grouped';

    function applyLayout(mode) {
        layoutMode = mode;
        cy.batch(function () {
            cy.nodes('.type').forEach(function (node) {
                var wanted = mode === 'grouped' ? 'group:' + node.data('level') : null;
                var current = node.parent().length ? node.parent().id() : null;
                if (current !== wanted) node.move({ parent: wanted });
            });
        });
        if (mode === 'grouped') {
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
        fit();
    }

    function fit(eles) {
        cy.fit(eles && eles.length ? eles : cy.elements(':visible'), 40);
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
        applyLayout('grouped');

        cy.on('tap', 'node.type', function (evt) { select(evt.target.id(), { from: 'canvas' }); });
        cy.on('tap', 'edge.relation', function (evt) { selectRelation(evt.target.data('relation')); });
        cy.on('tap', function (evt) { if (evt.target === cy) clearSelection(); });
        cy.on('mouseover', 'node.type', function (evt) { evt.target.addClass('hovered'); graphEl.style.cursor = 'pointer'; });
        cy.on('mouseout', 'node.type', function (evt) { evt.target.removeClass('hovered'); graphEl.style.cursor = ''; });
        cy.on('dbltap', function (evt) { if (evt.target === cy) fit(); });

        // Theme switches re-read the tokens; the canvas draws with colours, not CSS.
        var restyle = function () { cy.style(style()); };
        new MutationObserver(restyle).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
        if (window.matchMedia) {
            var media = window.matchMedia('(prefers-color-scheme: dark)');
            if (media.addEventListener) media.addEventListener('change', restyle);
        }
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
        if (node.pack) tags.appendChild(tag('Paket ' + node.pack));
        if (node.meta) tags.appendChild(tag('Meta'));
        if (node.readonly) tags.appendChild(tag('Schreibgeschützt'));
        if (node.source !== 'model') tags.appendChild(tag('Bezugsklasse, nicht im Modell'));
        panelBody.appendChild(tags);

        var serious = seriousProblems(node);
        if (serious.length) panelBody.appendChild(problemList(serious));

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
        var serious = (r.problems || []).filter(function (id) { return problemsById[id].severity !== 'info'; });
        if (serious.length) panelBody.appendChild(problemList(serious));
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

    var below = null;
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
        var order = ['parent_unknown', 'parent_cycle', 'relation_no_bounds', 'relation_unknown_type', 'type_no_description', 'attribute_not_held'];
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

    var currentVisible = {};

    function refresh() {
        readFilters();
        currentVisible = applyFiltersToCanvas();
        renderTree(currentVisible);
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
        var ready = Math.round(performance.now() - started);
        graphEl.setAttribute('aria-label', 'Fläche mit ' + data.counts.types + ' Typen und ' + data.counts.relations + ' Beziehungen');
        // For the acceptance test: how long from script start to a drawn canvas.
        window.__builderOntology = {
            readyMs: ready,
            types: cy.nodes('.type').filter(function (n) { return nodesById[n.id()].source === 'model'; }).length,
            relations: Object.keys(cy.edges('.relation').reduce(function (seen, e) { seen[e.data('relation')] = 1; return seen; }, {})).length,
            cy: cy,
            select: select,
        };
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
