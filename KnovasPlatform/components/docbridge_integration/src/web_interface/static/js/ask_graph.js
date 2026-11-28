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

   Moving the nodes (the founder, 2026-11-28): a node is dragged with a mouse, a pen or a finger (Pointer Events, the
   pointer captured by the node; touch-action none on the SVG, so the page does not scroll while dragging); its
   edges and their labels follow, and it stays inside the box. A press that moves less than DRAG_PX pixels is a
   click and opens the dossier as before; a drag does not. With the keyboard, the arrow keys move a focused node
   (KEY_STEP, with Shift BIG_STEP); Enter still opens it. "Anordnung zuruecksetzen" puts every node back where the
   layout placed it. Positions are kept for this page only, never stored.

   No markup is written from text: every element is created, every text is a text node. */
(function () {
    'use strict';

    var SVG_NS = 'http://www.w3.org/2000/svg';
    var PALETTE = [
        '#1A45C7', '#1a6b4a', '#9a3412', '#6d28d9', '#0f766e', '#b45309',
        '#be123c', '#4d7c0f', '#0369a1', '#7c2d12', '#a21caf', '#334155'
    ];
    var LABEL_CHARS = 26;
    // The longest edge label of the assistant's selection (answer_graph.MAX_LABEL_CHARS).
    var LABEL_EDGE_CHARS = 30;
    var FEW_EDGES = 8;
    var STEPS = 300;
    // A press that moves less than this (CSS pixels) is a click, not a drag.
    var DRAG_PX = 4;
    // The arrow keys move a focused node this far (SVG units); with Shift, BIG_STEP.
    var KEY_STEP = 8;
    var BIG_STEP = 32;
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

    // Whether a press moved far enough to be a drag (dx, dy in CSS pixels).
    var isDrag = function (dx, dy) { return Math.sqrt(dx * dx + dy * dy) >= DRAG_PX; };
    // The step an arrow key moves a node by, or null for any other key.
    var keyStep = function (key, shift) {
        var size = shift ? BIG_STEP : KEY_STEP;
        if (key === 'ArrowLeft') return [-size, 0];
        if (key === 'ArrowRight') return [size, 0];
        if (key === 'ArrowUp') return [0, -size];
        if (key === 'ArrowDown') return [0, size];
        return null;
    };
    // A point kept inside the box: margin left and right of it, top above it, bottom below it.
    var keepInside = function (x, y, box) {
        var left = box.side, right = Math.max(box.width - box.side, left);
        var top = box.top, bottom = Math.max(box.height - box.bottom, top);
        return { x: Math.round(Math.min(right, Math.max(left, x))), y: Math.round(Math.min(bottom, Math.max(top, y))) };
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
    var layout = function (nodes, edges, width, height, centreId) {
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
        if (centreId !== undefined && centreId !== null && index[centreId] !== undefined) centre = index[centreId];
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

    // Half the width of a count's box (its text decides it).
    var boxHalf = function (node) { return Math.round(cut(node.label, 24).length * 7 + 26) / 2; };

    // What an edge shows: the assistant's label when it gave one, else the relation's name.
    var edgeWords = function (edge) { return edge.label ? cut(edge.label, LABEL_EDGE_CHARS) : cut(edge.relation, 22); };
    // The edge's tooltip and accessible name: the label with the real relation, or the relation.
    var edgeName = function (edge, from, to) {
        var words = edge.label ? edge.label + ' (Relation: ' + edge.relation + ')' : edge.relation;
        return words + ': ' + from.label + ' \u2192 ' + to.label;
    };

    /* One drawing of a set of nodes and edges (the whole graph, or the assistant's selection): its own layout, its own
       positions. ``options``: width, centre (a node id for the middle), labelled (every edge label shown where it
       fits). Returns the SVG, reset() and hide(types, relations). */
    var scene = function (nodes, edges, options) {
        drawn += 1;
        var width = options.width;
        var height = nodes.length <= 6 ? 320 : (nodes.length <= 15 ? 380 : 420);
        var home = layout(nodes, edges, width, height, options.centre);
        var points = home.map(function (p) { return { x: p.x, y: p.y }; });
        var at = {};
        nodes.forEach(function (node, i) { at[node.id] = i; });

        var svg = make('svg', {
            'class': 'ask-graph-svg' + (options.labelled || edges.length <= FEW_EDGES ? ' is-few' : ''),
            viewBox: '0 0 ' + width + ' ' + height, width: '100%', height: height,
            preserveAspectRatio: 'xMidYMid meet', role: 'group',
            'aria-label': 'Ausschnitt aus dem Wissensgraphen: ' + nodes.length + ' Knoten, ' + edges.length +
                ' Verbindungen. Knoten lassen sich verschieben, mit der Maus oder den Pfeiltasten.'
        });
        var arrow = 'ask-graph-arrow-' + drawn;
        var defs = make('defs', {}, svg);
        var marker = make('marker', { id: arrow, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7,
            markerHeight: 7, orient: 'auto-start-reverse' }, defs);
        make('path', { d: 'M0,0 L10,5 L0,10 z', 'class': 'ask-graph-arrow' }, marker);

        var edgeLayer = make('g', { 'class': 'ask-graph-edges' }, svg);
        var nodeLayer = make('g', { 'class': 'ask-graph-nodes' }, svg);
        var edgeEls = [];
        var nodeEls = [];
        // Where each node may go: inside the box, its label too.
        var bounds = nodes.map(function (node) {
            var side = node.kind === 'count' ? boxHalf(node) + 2 : radius(node) + 2;
            return { width: width, height: height, side: side, top: radius(node) + 2,
                bottom: radius(node) + (node.kind === 'count' ? 2 : 18) };
        });

        // An edge between its two nodes' points: the line from rim to rim and its label in the middle; an edge
        // shorter than the two rims is not shown.
        var placeEdge = function (edge) {
            var p = points[edge.a], q = points[edge.b];
            var lx = q.x - p.x, ly = q.y - p.y;
            var length = Math.sqrt(lx * lx + ly * ly) || 1;
            var ra = radius(nodes[edge.a]) + 2, rb = radius(nodes[edge.b]) + 4;
            var short = length <= ra + rb;
            edge.el.classList.toggle('is-short', short);
            var ends = { x1: p.x + (lx / length) * ra, y1: p.y + (ly / length) * ra,
                x2: q.x - (lx / length) * rb, y2: q.y - (ly / length) * rb };
            [edge.hit, edge.line].forEach(function (line) {
                Object.keys(ends).forEach(function (key) { line.setAttribute(key, String(ends[key])); });
            });
            if (edge.label) {
                edge.label.setAttribute('x', String(Math.round((p.x + q.x) / 2)));
                edge.label.setAttribute('y', String(Math.round((p.y + q.y) / 2) - 4));
            }
            return !short;
        };
        var placeNode = function (i) {
            nodeEls[i].setAttribute('transform', 'translate(' + points[i].x + ',' + points[i].y + ')');
        };
        var moveTo = function (i, x, y) {
            points[i] = keepInside(x, y, bounds[i]);
            placeNode(i);
            edgeEls.forEach(function (edge) { if (edge.a === i || edge.b === i) placeEdge(edge); });
        };

        // Boxes already taken (each node with its label, then each edge label shown): an edge label that would
        // cover one waits for hover or focus (is-crowded), so no text is drawn over another. Decided once, for the
        // layout's picture.
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
            var g = make('g', { 'class': 'ask-graph-edge' }, edgeLayer);
            g.setAttribute('aria-label', edgeName(edge, nodes[a], nodes[b]));
            say(make('title', {}, g), edgeName(edge, nodes[a], nodes[b]));
            var drawnEdge = { el: g, a: a, b: b, relation: edge.relation || '',
                hit: make('line', { 'class': 'ask-graph-hit' }, g),
                line: make('line', { 'class': 'ask-graph-line', 'marker-end': 'url(#' + arrow + ')' }, g),
                label: null };
            var words = edgeWords(edge);
            if (words) {
                drawnEdge.label = say(make('text', { 'class': 'ask-graph-edge-label' + (edge.label ? ' is-named' : ''),
                    'text-anchor': 'middle' }, g), words);
            }
            var shown = placeEdge(drawnEdge);
            if (drawnEdge.label && shown) {
                var mx = Math.round((points[a].x + points[b].x) / 2), my = Math.round((points[a].y + points[b].y) / 2) - 4;
                var box = { x1: mx - words.length * 2.8, x2: mx + words.length * 2.8, y1: my - 10, y2: my + 3 };
                var room = free(box);
                if (room) taken.push(box);
                else drawnEdge.label.classList.add('is-crowded');
            }
            edgeEls.push(drawnEdge);
        });

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
        // SVG units per CSS pixel: the viewBox is scaled to the element's width.
        var unitsPerPixel = function () {
            var matrix = svg.getScreenCTM && svg.getScreenCTM();
            return matrix && matrix.a ? 1 / matrix.a : 1;
        };
        nodes.forEach(function (node, i) {
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
            nodeEls.push(item);
            placeNode(i);
            say(make('title', {}, item), nameOf(node));
            var tint = colour(node.type);
            if (node.kind === 'count') {
                var text = cut(node.label, 24);
                var boxWidth = boxHalf(node) * 2;
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

            // Dragging: the node follows the pointer once it moved DRAG_PX; a shorter press stays a click.
            var press = null;
            var dragged = false;
            item.addEventListener('pointerdown', function (event) {
                if (event.button !== undefined && event.button !== 0) return;
                press = { id: event.pointerId, sx: event.clientX, sy: event.clientY, x: points[i].x, y: points[i].y,
                    scale: unitsPerPixel(), moving: false };
                dragged = false;
                if (item.setPointerCapture) {
                    try { item.setPointerCapture(event.pointerId); } catch (err) { /* the press still counts */ }
                }
            });
            item.addEventListener('pointermove', function (event) {
                if (!press || press.id !== event.pointerId) return;
                var dx = event.clientX - press.sx, dy = event.clientY - press.sy;
                if (!press.moving && !isDrag(dx, dy)) return;
                press.moving = true;
                svg.classList.add('is-dragging');
                event.preventDefault();
                moveTo(i, press.x + dx * press.scale, press.y + dy * press.scale);
            });
            var release = function (event) {
                if (!press || press.id !== event.pointerId) return;
                dragged = press.moving;
                press = null;
                svg.classList.remove('is-dragging');
            };
            item.addEventListener('pointerup', release);
            item.addEventListener('pointercancel', release);
            // A drag is no click: the dossier opens only for a press that stayed in place.
            item.addEventListener('click', function (event) {
                if (dragged) { event.preventDefault(); event.stopPropagation(); }
                dragged = false;
            });
            item.addEventListener('keydown', function (event) {
                if (event.key === 'Enter') dragged = false;          // Enter opens, whatever a pointer did before
                var step = keyStep(event.key, event.shiftKey);
                if (!step) return;                                   // Enter and the rest as before
                event.preventDefault();
                moveTo(i, points[i].x + step[0], points[i].y + step[1]);
            });
        });
        // A link's own drag (of its address) would start instead of moving the node.
        svg.addEventListener('dragstart', function (event) { event.preventDefault(); });

        return {
            svg: svg,
            // Every node back where the layout placed it.
            reset: function () {
                nodes.forEach(function (node, i) { points[i] = { x: home[i].x, y: home[i].y }; placeNode(i); });
                edgeEls.forEach(placeEdge);
            },
            // Hide the nodes of the hidden types (COUNT_KEY: the counts) and the edges of the hidden relations;
            // a hidden node hides its edges too.
            hide: function (types, relations) {
                var gone = nodes.map(function (node) { return hiddenNode(node, types); });
                nodeEls.forEach(function (el, i) { el.classList.toggle('is-hidden', gone[i]); });
                edgeEls.forEach(function (edge) {
                    edge.el.classList.toggle('is-hidden', !!relations[edge.relation] || gone[edge.a] || gone[edge.b]);
                });
            }
        };
    };

    // The key of the counts among the type toggles (no type is called so: it holds a NUL).
    var COUNT_KEY = '\u0000count';
    var hiddenNode = function (node, types) {
        return node.kind === 'count' ? !!types[COUNT_KEY] : !!types[node.type];
    };

    /* The legend: the types (and the counts) and the relations are toggle buttons (aria-pressed: shown) that hide and
       show those nodes and edges; the other keys are plain. ``state.types`` / ``state.relations`` hold what is
       hidden; ``changed()`` is called after each toggle. */
    var legend = function (nodes, edges, state, changed) {
        var box = document.createElement('div');
        box.className = 'ask-graph-keys';
        var list = document.createElement('ul');
        list.className = 'ask-graph-legend';
        box.appendChild(list);
        var swatchOf = function (swatchClass, tint) {
            var swatch = document.createElement('span');
            swatch.className = 'ask-graph-swatch ' + swatchClass;
            swatch.setAttribute('aria-hidden', 'true');
            if (tint) swatch.style.backgroundColor = tint;
            return swatch;
        };
        var toggle = function (target, hidden, key, swatch, words, what) {
            var item = document.createElement('li');
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'ask-graph-key';
            var sync = function () {
                button.setAttribute('aria-pressed', hidden[key] ? 'false' : 'true');
                button.title = (hidden[key] ? what + ' einblenden' : what + ' ausblenden');
            };
            if (swatch) button.appendChild(swatch);
            say(button, words);
            sync();
            button.addEventListener('click', function () {
                hidden[key] = !hidden[key];
                sync();
                changed();
            });
            item.appendChild(button);
            target.appendChild(item);
        };
        var plain = function (swatchClass, words) {
            var item = document.createElement('li');
            item.appendChild(swatchOf(swatchClass, ''));
            say(item, words);
            list.appendChild(item);
        };
        var seen = {};
        nodes.forEach(function (node) {
            if (node.kind === 'count' || !node.type || seen[node.type]) return;
            seen[node.type] = true;
            toggle(list, state.types, node.type, swatchOf('', colour(node.type)), node.type, node.type);
        });
        if (nodes.some(function (node) { return node.kind === 'count'; })) {
            toggle(list, state.types, COUNT_KEY, swatchOf('is-count-key', ''), 'Abfrage (Anzahl)', 'Abfragen');
        }
        plain('is-cited-key', 'zitiert (Nummer der Quelle)');
        if (nodes.some(function (node) { return !node.cited; })) plain('is-read-key', 'verbindet');
        var relations = [];
        edges.forEach(function (edge) {
            if (edge.relation && relations.indexOf(edge.relation) === -1) relations.push(edge.relation);
        });
        if (relations.length) {
            var row = document.createElement('ul');
            row.className = 'ask-graph-legend is-relations';
            var head = document.createElement('li');
            head.className = 'ask-graph-legend-head';
            say(head, 'Verbindungen:');
            row.appendChild(head);
            relations.forEach(function (relation) {
                var named = edges.filter(function (edge) { return edge.relation === relation && edge.label; });
                var words = named.length ? cut(named[0].label, LABEL_EDGE_CHARS) + ' (' + relation + ')' : relation;
                toggle(row, state.relations, relation, null, words, 'Verbindung ' + relation);
            });
            box.appendChild(row);
        }
        return box;
    };

    var button = function (className, words) {
        var element = document.createElement('button');
        element.type = 'button';
        element.className = className;
        say(element, words);
        return element;
    };

    /* The block's drawing: the assistant's selection (graph.view) by default when there is one, else the whole graph;
       \u00abAlles zeigen\u00bb / \u00abAuswahl zeigen\u00bb switches; each has its own layout, positions and hidden keys. \u00abAnordnung
       zuruecksetzen\u00bb resets the drawing shown. */
    var draw = function (canvas, graph) {
        var width = Math.max(canvas.clientWidth || 0, 280);
        if (width < 300) width = 640;
        var byId = {};
        graph.nodes.forEach(function (node) { byId[node.id] = node; });
        var states = {
            all: { nodes: graph.nodes, edges: (graph.edges || []).map(function (edge) {
                return { from: edge.from, to: edge.to, relation: edge.relation, label: '' };
            }), centre: null, labelled: false }
        };
        var view = graph.view;
        if (view && Array.isArray(view.nodes)) {
            var picked = view.nodes.map(function (id) { return byId[id]; }).filter(Boolean);
            if (picked.length >= 2) {
                states.view = { nodes: picked, edges: (view.edges || []).filter(function (edge) {
                    return byId[edge.from] && byId[edge.to];
                }), centre: view.focus, labelled: true };
            }
        }
        var block = canvas.parentNode;
        var caption = block && block.querySelector ? block.querySelector('.ask-graph-caption') : null;
        var stage = document.createElement('div');
        stage.className = 'ask-graph-stage';
        canvas.appendChild(stage);
        var under = document.createElement('div');
        under.className = 'ask-graph-under';
        canvas.appendChild(under);
        var keys = null;
        var buttons = document.createElement('div');
        buttons.className = 'ask-graph-buttons';
        under.appendChild(buttons);
        var switcher = states.view ? button('ask-graph-switch', 'Alles zeigen') : null;
        var reset = button('ask-graph-reset', 'Anordnung zur\u00fccksetzen');
        if (switcher) buttons.appendChild(switcher);
        buttons.appendChild(reset);
        var current = null;
        var show = function (name) {
            var state = states[name];
            if (!state.scene) {
                state.types = {};
                state.relations = {};
                state.scene = scene(state.nodes, state.edges, { width: width, centre: state.centre,
                    labelled: state.labelled });
            }
            current = state;
            while (stage.firstChild) stage.removeChild(stage.firstChild);
            stage.appendChild(state.scene.svg);
            if (keys) under.removeChild(keys);
            keys = legend(state.nodes, state.edges, state, function () {
                state.scene.hide(state.types, state.relations);
            });
            under.insertBefore(keys, buttons);
            state.scene.hide(state.types, state.relations);
            if (caption) caption.hidden = name !== 'view';
            if (switcher) {
                switcher.textContent = name === 'view' ? 'Alles zeigen' : 'Auswahl zeigen';
                switcher.setAttribute('aria-pressed', name === 'all' ? 'true' : 'false');
            }
            canvas.setAttribute('data-state', name);
        };
        if (switcher) {
            switcher.addEventListener('click', function () { show(current === states.view ? 'all' : 'view'); });
        }
        reset.addEventListener('click', function () { if (current) current.scene.reset(); });
        show(states.view ? 'view' : 'all');
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

    // The pure parts, for the tests (node runs this file with a stub document).
    globalThis.knovasAskGraph = { isDrag: isDrag, keyStep: keyStep, keepInside: keepInside, layout: layout,
        DRAG_PX: DRAG_PX };

    Array.prototype.forEach.call(document.querySelectorAll('.ask-graph'), function (block) {
        if (block.open) start(block);
        block.addEventListener('toggle', function () { if (block.open) start(block); });
    });
})();
