/* Knovas Assistent, <<Wissensgraph>> (PMVP-20 brain-graph): the block "Im Wissensgraphen" under an answer.

   ask.html puts the answer's graph (assistant_routes.shown_graph: nodes with label, type, cited, passage, kind, href;
   edges with from, to, relation) into the data-graph attribute of .ask-graph-canvas, and a plain list of the nodes
   under it. Here the graph is drawn as a small inline SVG and the list is hidden; without this script (or when the
   data does not parse) the list stays.

   The layout is deterministic: the cited node with the most edges in the middle, the others on rings by their
   distance from it (a ring's nodes ordered by the angle of the node they hang on), then a few hundred steps of a
   force layout in plain JavaScript, with no random start. The same graph in the same width is the same picture.
   Colours by type (the palette and the hash of the views page, static/js/views_core.js, so a type has the colour it
   has there); a cited node is filled and carries the number of its passage; a count is one box ("97 x Akte"); a
   connecting node is a ring. The rest is CSS (ask.css, the brand tokens: right in the light and the dark theme).

   Each node is focusable with an accessible name. A node with an address (its dossier, built by the route with
   url_for while the Brain views are on) is a link that opens it in a new tab; any other address is refused. Hover
   or focus on a node shows its edges' relation names; with few edges they always show.

   No markup is written from text: every element is created, every text is a text node. */
(function () {
    'use strict';

    var SVG_NS = 'http://www.w3.org/2000/svg';
    var PALETTE = [
        '#1A45C7', '#1a6b4a', '#9a3412', '#6d28d9', '#0f766e', '#b45309',
        '#be123c', '#4d7c0f', '#0369a1', '#7c2d12', '#a21caf', '#334155'
    ];
    var LABEL_CHARS = 26;
    var FEW_EDGES = 8;
    var STEPS = 300;
    var drawn = 0;

    var hash = function (text) {
        var h = 2166136261;
        for (var i = 0; i < text.length; i += 1) {
            h ^= text.charCodeAt(i);
            h = Math.imul(h, 16777619);
        }
        return h >>> 0;
    };
    var colour = function (type) { return PALETTE[hash(String(type || '')) % PALETTE.length]; };
    var cut = function (text, most) {
        var value = String(text || '');
        return value.length <= most ? value : value.slice(0, most - 1) + '\u2026';
    };
    // Only an address of the app itself: one path, never another site or a script.
    var ownAddress = function (href) {
        return typeof href === 'string' && href.charAt(0) === '/' && href.charAt(1) !== '/' &&
            href.indexOf('\\') === -1;
    };

    var make = function (tag, attrs, parent) {
        var element = document.createElementNS(SVG_NS, tag);
        Object.keys(attrs || {}).forEach(function (key) { element.setAttribute(key, String(attrs[key])); });
        if (parent) parent.appendChild(element);
        return element;
    };
    var say = function (parent, text) {
        parent.appendChild(document.createTextNode(String(text)));
        return parent;
    };

    var radius = function (node) {
        if (node.kind === 'count') return 15;
        return node.cited ? 13 : 7;
    };

    /* Positions for the nodes in a box of width x height: rings around the centre, then the force steps. */
    var layout = function (nodes, edges, width, height) {
        var count = nodes.length;
        var index = {};
        nodes.forEach(function (node, i) { index[node.id] = i; });
        var near = nodes.map(function () { return []; });
        var links = [];
        edges.forEach(function (edge) {
            var a = index[edge.from], b = index[edge.to];
            if (a === undefined || b === undefined || a === b) return;
            near[a].push(b);
            near[b].push(a);
            links.push([a, b]);
        });
        var centre = 0, best = -1;
        nodes.forEach(function (node, i) {
            if (node.cited && near[i].length > best) { best = near[i].length; centre = i; }
        });
        var depth = nodes.map(function () { return -1; });
        var parent = nodes.map(function () { return -1; });
        depth[centre] = 0;
        var queue = [centre];
        while (queue.length) {
            var at = queue.shift();
            near[at].forEach(function (next) {
                if (depth[next] === -1) { depth[next] = depth[at] + 1; parent[next] = at; queue.push(next); }
            });
        }
        var deepest = Math.max.apply(null, depth.concat([1]));
        depth = depth.map(function (d) { return d === -1 ? deepest + 1 : d; });
        deepest = Math.max.apply(null, depth);
        var padX = 70, padY = 34;
        var cx = width / 2, cy = height / 2;
        var rx = Math.max(cx - padX, 40), ry = Math.max(cy - padY, 40);
        var x = nodes.map(function () { return cx; });
        var y = nodes.map(function () { return cy; });
        var angle = nodes.map(function () { return 0; });
        for (var ring = 1; ring <= deepest; ring += 1) {
            var members = [];
            for (var m = 0; m < count; m += 1) if (depth[m] === ring) members.push(m);
            members.sort(function (a, b) {
                var pa = parent[a] >= 0 ? angle[parent[a]] : 0, pb = parent[b] >= 0 ? angle[parent[b]] : 0;
                return pa - pb || a - b;
            });
            var share = ring / deepest;
            members.forEach(function (member, k) {
                var turn = -Math.PI / 2 + (ring - 1) * 0.35 + (2 * Math.PI * k) / members.length;
                angle[member] = turn;
                x[member] = cx + rx * share * Math.cos(turn);
                y[member] = cy + ry * share * Math.sin(turn);
            });
        }
        // The force steps (Fruchterman-Reingold, cooling), the centre held, every node kept inside the box.
        var ideal = Math.sqrt((width * height) / Math.max(count, 1)) * 0.6;
        for (var step = 0; step < STEPS; step += 1) {
            var heat = (width / 12) * (1 - step / STEPS) + 0.5;
            var dx = nodes.map(function () { return 0; });
            var dy = nodes.map(function () { return 0; });
            for (var i = 0; i < count; i += 1) {
                for (var j = i + 1; j < count; j += 1) {
                    var ex = x[i] - x[j], ey = y[i] - y[j];
                    var d = Math.sqrt(ex * ex + ey * ey) || 0.01;
                    var push = (ideal * ideal) / d;
                    dx[i] += (ex / d) * push; dy[i] += (ey / d) * push;
                    dx[j] -= (ex / d) * push; dy[j] -= (ey / d) * push;
                }
            }
            links.forEach(function (link) {
                var a = link[0], b = link[1];
                var lx = x[a] - x[b], ly = y[a] - y[b];
                var length = Math.sqrt(lx * lx + ly * ly) || 0.01;
                var pull = (length * length) / ideal;
                dx[a] -= (lx / length) * pull; dy[a] -= (ly / length) * pull;
                dx[b] += (lx / length) * pull; dy[b] += (ly / length) * pull;
            });
            for (var n = 0; n < count; n += 1) {
                if (n === centre) continue;
                dx[n] += (cx - x[n]) * 0.05 * ideal / 40;
                dy[n] += (cy - y[n]) * 0.08 * ideal / 40;
                var move = Math.sqrt(dx[n] * dx[n] + dy[n] * dy[n]) || 0.01;
                x[n] += (dx[n] / move) * Math.min(move, heat);
                y[n] += (dy[n] / move) * Math.min(move, heat);
                x[n] = Math.min(width - padX, Math.max(padX, x[n]));
                y[n] = Math.min(height - padY, Math.max(padY - 14, y[n]));
            }
        }
        // Fit: the drawing's middle in the box's middle, grown (at most 2.5 times) or shrunk to fill it.
        var left = Math.min.apply(null, x), right = Math.max.apply(null, x);
        var top = Math.min.apply(null, y), bottom = Math.max.apply(null, y);
        var top0 = 26, bottom0 = height - 30;
        var scale = Math.min((width - 2 * padX) / Math.max(right - left, 1),
            (bottom0 - top0) / Math.max(bottom - top, 1), 2.5);
        var midX = (left + right) / 2, midY = (top + bottom) / 2;
        return nodes.map(function (node, i) {
            return { x: Math.round(cx + (x[i] - midX) * scale),
                y: Math.round((top0 + bottom0) / 2 + (y[i] - midY) * scale) };
        });
    };

    var nameOf = function (node) {
        if (node.kind === 'count') {
            return 'Abfrage: ' + node.label + (node.query ? ' (' + node.query + ')' : '') +
                (node.passage ? ', Quelle ' + node.passage : '');
        }
        var words = (node.type ? node.type + ': ' : '') + node.label;
        if (node.type && node.label === node.type) words = node.type + ' (verbindet)';
        if (node.passage) words += ', Quelle ' + node.passage;
        if (ownAddress(node.href)) words += ', Dossier \u00f6ffnen';
        return words;
    };

    var draw = function (canvas, graph) {
        var nodes = graph.nodes, edges = graph.edges || [];
        drawn += 1;
        var width = Math.max(canvas.clientWidth || 0, 280);
        if (width < 300) width = 640;
        var height = nodes.length <= 6 ? 320 : (nodes.length <= 15 ? 380 : 420);
        var points = layout(nodes, edges, width, height);
        var at = {};
        nodes.forEach(function (node, i) { at[node.id] = i; });

        var svg = make('svg', {
            'class': 'ask-graph-svg' + (edges.length <= FEW_EDGES ? ' is-few' : ''),
            viewBox: '0 0 ' + width + ' ' + height, width: '100%', height: height,
            preserveAspectRatio: 'xMidYMid meet', role: 'group',
            'aria-label': 'Ausschnitt aus dem Wissensgraphen: ' + nodes.length + ' Knoten, ' + edges.length +
                ' Verbindungen'
        });
        var arrow = 'ask-graph-arrow-' + drawn;
        var defs = make('defs', {}, svg);
        var marker = make('marker', { id: arrow, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7,
            markerHeight: 7, orient: 'auto-start-reverse' }, defs);
        make('path', { d: 'M0,0 L10,5 L0,10 z', 'class': 'ask-graph-arrow' }, marker);

        var edgeLayer = make('g', { 'class': 'ask-graph-edges' }, svg);
        var nodeLayer = make('g', { 'class': 'ask-graph-nodes' }, svg);
        var edgeEls = [];
        // Boxes already taken (each node with its label, then each edge label shown): an edge label that would
        // cover one waits for hover or focus (is-crowded), so no text is drawn over another.
        var taken = nodes.map(function (node, i) {
            var r = node.kind === 'count' ? Math.round(cut(node.label, 24).length * 3.5 + 15) : radius(node);
            var half = Math.max(r, (node.kind === 'count' ? 0 : cut(node.label, LABEL_CHARS).length * 2.9));
            return { x1: points[i].x - half, x2: points[i].x + half, y1: points[i].y - radius(node) - 2,
                y2: points[i].y + radius(node) + (node.kind === 'count' ? 2 : 18) };
        });
        var free = function (box) {
            return taken.every(function (t) {
                return box.x2 < t.x1 || box.x1 > t.x2 || box.y2 < t.y1 || box.y1 > t.y2;
            });
        };
        edges.forEach(function (edge) {
            var a = at[edge.from], b = at[edge.to];
            if (a === undefined || b === undefined || a === b) return;
            var p = points[a], q = points[b];
            var lx = q.x - p.x, ly = q.y - p.y;
            var length = Math.sqrt(lx * lx + ly * ly) || 1;
            var ra = radius(nodes[a]) + 2, rb = radius(nodes[b]) + 4;
            if (length <= ra + rb) return;
            var g = make('g', { 'class': 'ask-graph-edge' }, edgeLayer);
            say(make('title', {}, g), edge.relation + ': ' + nodes[a].label + ' \u2192 ' + nodes[b].label);
            var ends = { x1: p.x + (lx / length) * ra, y1: p.y + (ly / length) * ra,
                x2: q.x - (lx / length) * rb, y2: q.y - (ly / length) * rb };
            make('line', Object.assign({ 'class': 'ask-graph-hit' }, ends), g);
            make('line', Object.assign({ 'class': 'ask-graph-line', 'marker-end': 'url(#' + arrow + ')' }, ends), g);
            if (edge.relation) {
                var words = cut(edge.relation, 22);
                var mx = Math.round((p.x + q.x) / 2), my = Math.round((p.y + q.y) / 2) - 4;
                var box = { x1: mx - words.length * 2.8, x2: mx + words.length * 2.8, y1: my - 10, y2: my + 3 };
                var room = free(box);
                if (room) taken.push(box);
                say(make('text', { 'class': 'ask-graph-edge-label' + (room ? '' : ' is-crowded'), x: mx, y: my,
                    'text-anchor': 'middle' }, g), words);
            }
            edgeEls.push({ el: g, a: a, b: b });
        });

        var nodeEls = [];
        var focus = function (i, on) {
            svg.classList.toggle('is-focus', on);
            nodeEls.forEach(function (el, k) { el.classList.toggle('is-me', on && k === i); });
            edgeEls.forEach(function (edge) {
                var touches = on && (edge.a === i || edge.b === i);
                edge.el.classList.toggle('is-on', touches);
                if (touches) nodeEls[edge.a === i ? edge.b : edge.a].classList.add('is-near');
            });
            if (!on) nodeEls.forEach(function (el) { el.classList.remove('is-near'); });
        };
        nodes.forEach(function (node, i) {
            var point = points[i];
            var kind = node.kind === 'count' ? 'is-count' : (node.cited ? 'is-cited' : 'is-read');
            var item;
            if (ownAddress(node.href)) {
                item = make('a', { 'class': 'ask-graph-node is-link ' + kind, target: '_blank', rel: 'noopener' },
                    nodeLayer);
                item.setAttribute('href', node.href);
            } else {
                item = make('g', { 'class': 'ask-graph-node ' + kind, tabindex: 0, role: 'img' }, nodeLayer);
            }
            item.setAttribute('aria-label', nameOf(node));
            item.setAttribute('transform', 'translate(' + point.x + ',' + point.y + ')');
            say(make('title', {}, item), nameOf(node));
            var tint = colour(node.type);
            if (node.kind === 'count') {
                var text = cut(node.label, 24);
                var boxWidth = Math.round(text.length * 7 + 26);
                make('rect', { 'class': 'ask-graph-box', x: -boxWidth / 2, y: -14, width: boxWidth, height: 28,
                    rx: 14 }, item);
                say(make('text', { 'class': 'ask-graph-box-text', y: 0, 'text-anchor': 'middle',
                    'dominant-baseline': 'central' }, item), text);
                if (node.passage) {
                    make('circle', { 'class': 'ask-graph-badge', cx: boxWidth / 2 - 2, cy: -13, r: 9 }, item);
                    say(make('text', { 'class': 'ask-graph-num', x: boxWidth / 2 - 2, y: -13, 'text-anchor': 'middle',
                        'dominant-baseline': 'central' }, item), node.passage);
                }
            } else if (node.cited) {
                var dot = make('circle', { 'class': 'ask-graph-dot', r: radius(node) }, item);
                dot.style.fill = tint;
                if (node.passage) {
                    say(make('text', { 'class': 'ask-graph-num', 'text-anchor': 'middle',
                        'dominant-baseline': 'central' }, item), node.passage);
                }
            } else {
                var ring = make('circle', { 'class': 'ask-graph-ring', r: radius(node) }, item);
                ring.style.stroke = tint;
            }
            if (node.kind !== 'count') {
                var unnamed = node.type && node.label === node.type;
                say(make('text', { 'class': 'ask-graph-label' + (unnamed ? ' is-unnamed' : ''),
                    y: radius(node) + 14, 'text-anchor': 'middle' }, item), cut(node.label, LABEL_CHARS));
            }
            ['mouseenter', 'focus'].forEach(function (name) {
                item.addEventListener(name, function () { focus(i, true); });
            });
            ['mouseleave', 'blur'].forEach(function (name) {
                item.addEventListener(name, function () { focus(i, false); });
            });
            nodeEls.push(item);
        });
        canvas.appendChild(svg);
        canvas.appendChild(legend(nodes));
    };

    var legend = function (nodes) {
        var list = document.createElement('ul');
        list.className = 'ask-graph-legend';
        var entry = function (swatchClass, tint, words) {
            var item = document.createElement('li');
            var swatch = document.createElement('span');
            swatch.className = 'ask-graph-swatch ' + swatchClass;
            swatch.setAttribute('aria-hidden', 'true');
            if (tint) swatch.style.backgroundColor = tint;
            item.appendChild(swatch);
            say(item, words);
            list.appendChild(item);
        };
        var seen = {};
        nodes.forEach(function (node) {
            if (node.kind === 'count' || !node.type || seen[node.type]) return;
            seen[node.type] = true;
            entry('', colour(node.type), node.type);
        });
        entry('is-cited-key', '', 'zitiert (Nummer der Quelle)');
        if (nodes.some(function (node) { return !node.cited; })) entry('is-read-key', '', 'verbindet');
        if (nodes.some(function (node) { return node.kind === 'count'; })) entry('is-count-key', '', 'Abfrage (Anzahl)');
        return list;
    };

    var start = function (block) {
        var canvas = block.querySelector('.ask-graph-canvas[data-graph]');
        var list = block.querySelector('.ask-graph-list');
        if (!canvas || canvas.getAttribute('data-drawn') === 'yes') return;
        try {
            var graph = JSON.parse(canvas.getAttribute('data-graph'));
            if (!graph || !Array.isArray(graph.nodes) || graph.nodes.length < 2) return;
            draw(canvas, graph);
            canvas.setAttribute('data-drawn', 'yes');
            if (list) list.hidden = true;
        } catch (err) {
            while (canvas.firstChild) canvas.removeChild(canvas.firstChild);   // the list stays
        }
    };

    Array.prototype.forEach.call(document.querySelectorAll('.ask-graph'), function (block) {
        if (block.open) start(block);
        block.addEventListener('toggle', function () { if (block.open) start(block); });
    });
})();
