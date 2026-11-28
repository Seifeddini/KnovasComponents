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
   or focus on a node brings out its edges and their labels.

   Labels (the founder, 2026-11-28: "the texts and edges overlap"): the drawing is taller for more nodes (boxHeight),
   the layout pushes harder and keeps edges as long as the labels need; then placeLabels puts every label where it
   covers nothing: a node's below, above, right or left of it, an edge's at a spot along its curve, a little off it,
   never outside the drawing. Two or more edges between the same nodes are slight arcs, each label on its arc. An
   edge label has a halo in the card's colour, so a crossing line never cuts through it. In the selection every
   label shows; in the whole graph a label with no free spot waits for hover or focus. After a drag, a key move, a
   reset and a toggle the labels are placed again; during a drag they move with their node and edge.

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

    /* Placing the labels (2026-11-28: "the texts and edges overlap"). Plain geometry, no library: boxes in SVG units,
       a label's width measured on the page or estimated from its letters (CHAR_W a letter, TEXT_H a line). */
    var CHAR_W = 6.2;
    var TEXT_H = 15;
    var LABEL_GAP = 3;
    var LABEL_ROOM = 5;
    // How strongly the nodes push one another apart (the force layout's repulsion; 1 is Fruchterman-Reingold's).
    var REPULSION = 1.35;
    // The longest relation name shown on an edge of the whole graph (the full words in its tooltip).
    var RELATION_CHARS = 22;
    // How far apart two edges of the same pair arc, and how far an edge label may stand off its line.
    var ARC_STEP = 22;
    var EDGE_OFFSET = 10;
    // The spots tried for an edge label, in order: (place along the curve, side of it: 0 on the line).
    var EDGE_SPOTS = [[0.5, 0], [0.5, 1], [0.5, -1], [0.4, 0], [0.6, 0], [0.4, 1], [0.6, -1], [0.4, -1], [0.6, 1],
        [0.3, 0], [0.7, 0], [0.3, 1], [0.7, -1], [0.3, -1], [0.7, 1]];
    // The sides tried for a node's label, in order.
    var NODE_SIDES = ['below', 'above', 'right', 'left'];

    var textWidth = function (words) { return String(words || '').length * CHAR_W + 6; };
    // The drawing's height: taller for more nodes.
    var boxHeight = function (count) { return Math.min(760, Math.max(320, 180 + count * 30)); };
    var round1 = function (value) { return Math.round(value * 10) / 10; };
    // Half the width of a count's box (its text decides it).
    var boxHalf = function (node) { return Math.round(cut(node.label, 24).length * 7 + 26) / 2; };
    // The point ``distance`` from ``p`` towards ``q``.
    var towards = function (p, q, distance) {
        var lx = q.x - p.x, ly = q.y - p.y;
        var length = Math.sqrt(lx * lx + ly * ly) || 1;
        return { x: p.x + (lx / length) * distance, y: p.y + (ly / length) * distance };
    };
    // The control point of an edge's curve between ``p`` and ``q`` (the pair's own order): its middle ``bend`` units
    // off the straight line (0: straight).
    var curveControl = function (p, q, bend) {
        var lx = q.x - p.x, ly = q.y - p.y;
        var length = Math.sqrt(lx * lx + ly * ly) || 1;
        return { x: (p.x + q.x) / 2 - (ly / length) * bend * 2, y: (p.y + q.y) / 2 + (lx / length) * bend * 2 };
    };
    var quadPoint = function (p, c, q, t) {
        var u = 1 - t;
        return { x: u * u * p.x + 2 * u * t * c.x + t * t * q.x, y: u * u * p.y + 2 * u * t * c.y + t * t * q.y };
    };
    // The bend of the k-th of ``total`` edges between the same two nodes: 0 for one, else spread around the line.
    var arcBend = function (k, total) { return total <= 1 ? 0 : (k - (total - 1) / 2) * ARC_STEP; };
    var overlapArea = function (a, b) {
        var w = Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1), h = Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1);
        return w > 0 && h > 0 ? w * h : 0;
    };
    // Whether the segment (x1, y1)-(x2, y2) crosses or touches the box (Liang-Barsky clipping).
    var segmentHitsBox = function (x1, y1, x2, y2, box) {
        var t0 = 0, t1 = 1, dx = x2 - x1, dy = y2 - y1;
        var p = [-dx, dx, -dy, dy], q = [x1 - box.x1, box.x2 - x1, y1 - box.y1, box.y2 - y1];
        for (var k = 0; k < 4; k += 1) {
            if (p[k] === 0) {
                if (q[k] < 0) return false;
            } else {
                var r = q[k] / p[k];
                if (p[k] < 0) {
                    if (r > t1) return false;
                    if (r > t0) t0 = r;
                } else {
                    if (r < t0) return false;
                    if (r < t1) t1 = r;
                }
            }
        }
        return true;
    };
    var insideBox = function (box, width, height) {
        return box.x1 >= 1 && box.y1 >= 1 && box.x2 <= width - 1 && box.y2 <= height - 1;
    };
    // The box moved (horizontally, then vertically) as little as needed to stand inside the drawing.
    var shiftInside = function (box, width, height) {
        var dx = box.x1 < 1 ? 1 - box.x1 : (box.x2 > width - 1 ? width - 1 - box.x2 : 0);
        var dy = box.y1 < 1 ? 1 - box.y1 : (box.y2 > height - 1 ? height - 1 - box.y2 : 0);
        return { x1: box.x1 + dx, x2: box.x2 + dx, y1: box.y1 + dy, y2: box.y2 + dy };
    };
    // An edge label at a spot of the curve p-c-q: ``t`` along it, ``off`` (-1, 0, 1) to a side of it; its box and
    // its text's position (``x`` the middle, ``baseline``), kept inside the drawing.
    var edgeSpot = function (p, c, q, t, off, w, width, height) {
        var at = quadPoint(p, c, q, t);
        var tx = 2 * (1 - t) * (c.x - p.x) + 2 * t * (q.x - c.x), ty = 2 * (1 - t) * (c.y - p.y) + 2 * t * (q.y - c.y);
        var length = Math.sqrt(tx * tx + ty * ty) || 1;
        var mx = at.x - (ty / length) * off * EDGE_OFFSET, my = at.y + (tx / length) * off * EDGE_OFFSET;
        var box = shiftInside({ x1: mx - w / 2, x2: mx + w / 2, y1: my - TEXT_H / 2, y2: my + TEXT_H / 2 },
            width, height || Infinity);
        return { box: box, x: (box.x1 + box.x2) / 2, baseline: box.y2 - 4 };
    };
    // A node label on one side of its node: its box (in the drawing) and its text's place in the node's frame.
    var nodeLabelSpot = function (node, side, width, height) {
        var w = node.w, r = node.r, box, anchor = 'middle';
        if (side === 'below') {
            box = { x1: node.x - w / 2, x2: node.x + w / 2, y1: node.y + r + LABEL_GAP, y2: node.y + r + LABEL_GAP + TEXT_H };
        } else if (side === 'above') {
            box = { x1: node.x - w / 2, x2: node.x + w / 2, y1: node.y - r - LABEL_GAP - TEXT_H, y2: node.y - r - LABEL_GAP };
        } else if (side === 'right') {
            box = { x1: node.x + r + LABEL_GAP, x2: node.x + r + LABEL_GAP + w, y1: node.y - TEXT_H / 2,
                y2: node.y + TEXT_H / 2 };
            anchor = 'start';
        } else {
            box = { x1: node.x - r - LABEL_GAP - w, x2: node.x - r - LABEL_GAP, y1: node.y - TEXT_H / 2,
                y2: node.y + TEXT_H / 2 };
            anchor = 'end';
        }
        if (anchor === 'middle') box = shiftInside(box, width, height);   // never outside the drawing
        var x = anchor === 'middle' ? (box.x1 + box.x2) / 2 - node.x : (anchor === 'start' ? box.x1 : box.x2) - node.x;
        return { box: box, x: x, y: box.y2 - 4 - node.y, anchor: anchor, side: side };
    };

    /* Every label where it covers nothing, deterministically. ``geo``: width, height, order (the nodes' order),
       nodes [{x, y, r, half, w (0: no label), hidden}], edges [{p, c, q, w, keep, hidden}]. A node label tries
       NODE_SIDES, an edge label EDGE_SPOTS; the first spot that covers no label, no node, no edge line and stays
       inside wins, else the one that covers least (crossing an edge line counts far less than covering a label or a
       node: the halo keeps the line out of the text). An edge label that covers a label or a node wherever it goes
       is ``crowded`` unless ``keep`` (it then shows where it covers least). Returns {nodes: [{x, y, anchor, side, box}], edges: [{t, off,
       crowded, box}]} (null where there is no label). */
    var placeLabels = function (geo) {
        var width = geo.width, height = geo.height;
        var discs = geo.nodes.map(function (n) {
            return n.hidden ? null : { x1: n.x - n.half - 2, x2: n.x + n.half + 2, y1: n.y - n.r - 2, y2: n.y + n.r + 2 };
        });
        var segments = [];
        geo.edges.forEach(function (e, k) {
            if (e.hidden) return;
            var previous = e.p;
            for (var s = 1; s <= 4; s += 1) {
                var point = quadPoint(e.p, e.c, e.q, s / 4);
                segments.push([previous.x, previous.y, point.x, point.y, k]);
                previous = point;
            }
        });
        // Labels keep a little room between them (LABEL_ROOM across, 1 up and down).
        var taken = [];
        var keep = function (box) {
            taken.push({ x1: box.x1 - LABEL_ROOM, x2: box.x2 + LABEL_ROOM, y1: box.y1 - 1, y2: box.y2 + 1 });
        };
        // What a box covers: ``hard`` (outside the drawing, another label, a node), ``soft`` (edge lines it crosses:
        // allowed, the label's halo keeps the line out of the text, but a free spot is better).
        var cost = function (box, ownNode, ownEdge) {
            var hard = insideBox(box, width, height) ? 0 : 1e6, soft = 0;
            taken.forEach(function (other) { hard += overlapArea(box, other); });
            discs.forEach(function (disc, i) { if (disc && i !== ownNode) hard += overlapArea(box, disc); });
            segments.forEach(function (segment) {
                if (segment[4] !== ownEdge && segmentHitsBox(segment[0], segment[1], segment[2], segment[3], box)) {
                    soft += 1;
                }
            });
            return { hard: hard, soft: soft, total: hard * 1000 + soft };
        };
        var nodesOut = geo.nodes.map(function () { return null; });
        (geo.order || geo.nodes.map(function (n, i) { return i; })).forEach(function (i) {
            var node = geo.nodes[i];
            if (!node || node.hidden || !node.w) return;
            var best = null;
            NODE_SIDES.forEach(function (side) {
                var spot = nodeLabelSpot(node, side, width, height);
                spot.cost = cost(spot.box, i, -1).total;
                if (best === null || spot.cost < best.cost) best = spot;
            });
            keep(best.box);
            nodesOut[i] = best;
        });
        var edgesOut = geo.edges.map(function (e, k) {
            if (e.hidden || !e.w) return null;
            var best = null;
            EDGE_SPOTS.forEach(function (pair) {
                var spot = edgeSpot(e.p, e.c, e.q, pair[0], pair[1], e.w, width, height);
                var spotCost = cost(spot.box, -1, k);
                if (best === null || spotCost.total < best.cost) {
                    best = { t: pair[0], off: pair[1], box: spot.box, cost: spotCost.total, hard: spotCost.hard };
                }
            });
            best.crowded = best.hard > 0 && !e.keep;
            if (!best.crowded) keep(best.box);
            return best;
        });
        return { nodes: nodesOut, edges: edgesOut };
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
        // The force steps (Fruchterman-Reingold, cooling), the centre held, every node kept inside the box. The ideal
        // edge length grows with the labels (2026-11-28: texts and edges overlapped), the repulsion is stronger.
        var widths = nodes.map(function (node) {
            return node.kind === 'count' ? boxHalf(node) * 2 : textWidth(cut(node.label, LABEL_CHARS));
        });
        var meanWidth = widths.reduce(function (sum, w) { return sum + w; }, 0) / Math.max(count, 1);
        var ideal = Math.max(Math.sqrt((width * height) / Math.max(count, 1)) * 0.75, meanWidth * 0.6 + 36);
        for (var step = 0; step < STEPS; step += 1) {
            var heat = (width / 12) * (1 - step / STEPS) + 0.5;
            var dx = nodes.map(function () { return 0; });
            var dy = nodes.map(function () { return 0; });
            for (var i = 0; i < count; i += 1) {
                for (var j = i + 1; j < count; j += 1) {
                    var ex = x[i] - x[j], ey = y[i] - y[j];
                    var d = Math.sqrt(ex * ex + ey * ey) || 0.01;
                    var push = (REPULSION * ideal * ideal) / d;
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
        var top0 = 30, bottom0 = height - 34;
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

    // What an edge shows: the assistant's label when it gave one, else the relation's name, cut to ``most``; the
    // full words stand in its tooltip.
    var edgeWords = function (edge, most) { return cut(edge.label || edge.relation, most); };
    // The edge's tooltip and accessible name: the label with the real relation, or the relation.
    var edgeName = function (edge, from, to) {
        var words = edge.label ? edge.label + ' (Relation: ' + edge.relation + ')' : edge.relation;
        return words + ': ' + from.label + ' \u2192 ' + to.label;
    };

    /* One drawing of a set of nodes and edges (the whole graph, or the assistant's selection): its own layout, its own
       positions. ``options``: width, centre (a node id for the middle), labelled (every edge label shown: the
       selection; else a label that finds no free place waits for hover or focus). Returns the SVG, reset(),
       hide(types, relations) and relabel(). */
    var scene = function (nodes, edges, options) {
        drawn += 1;
        var width = options.width;
        var height = boxHeight(nodes.length);
        var home = layout(nodes, edges, width, height, options.centre);
        var points = home.map(function (p) { return { x: p.x, y: p.y }; });
        var at = {};
        nodes.forEach(function (node, i) { at[node.id] = i; });
        var edgeChars = options.labelled ? LABEL_EDGE_CHARS : RELATION_CHARS;

        var svg = make('svg', {
            'class': 'ask-graph-svg is-few',
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
        var labelEls = nodes.map(function () { return null; });
        // Where each node may go: inside the box, its label too.
        var bounds = nodes.map(function (node) {
            var side = node.kind === 'count' ? boxHalf(node) + 2 : radius(node) + 2;
            return { width: width, height: height, side: side, top: radius(node) + 2,
                bottom: radius(node) + (node.kind === 'count' ? 2 : 18) };
        });

        // An edge's curve: straight, or a slight arc when two or more edges join the same two nodes (``bend``,
        // taken across the pair in one direction, so opposite edges part too).
        var curveOf = function (edge) {
            var lo = Math.min(edge.a, edge.b), hi = Math.max(edge.a, edge.b);
            return { a: points[edge.a], b: points[edge.b], c: curveControl(points[lo], points[hi], edge.bend) };
        };
        // An edge between its two nodes: the curve from rim to rim and its label at its place (``t``, ``off``); an
        // edge shorter than the two rims is not shown.
        var placeEdge = function (edge) {
            var g = curveOf(edge), p = g.a, q = g.b, c = g.c;
            var length = Math.sqrt((q.x - p.x) * (q.x - p.x) + (q.y - p.y) * (q.y - p.y)) || 1;
            var ra = radius(nodes[edge.a]) + 2, rb = radius(nodes[edge.b]) + 4;
            var short = length <= ra + rb;
            edge.el.classList.toggle('is-short', short);
            var start = towards(p, c, ra), end = towards(q, c, rb);
            var d = 'M' + round1(start.x) + ',' + round1(start.y) + ' Q' + round1(c.x) + ',' + round1(c.y) + ' ' +
                round1(end.x) + ',' + round1(end.y);
            edge.hit.setAttribute('d', d);
            edge.line.setAttribute('d', d);
            if (edge.label) {
                var spot = edgeSpot(p, c, q, edge.t, edge.off, edge.width, width);
                edge.label.setAttribute('x', String(round1(spot.x)));
                edge.label.setAttribute('y', String(round1(spot.baseline)));
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
        // The width of a drawn text: measured once the drawing is on the page, else estimated.
        var widthOf = function (el, words) {
            var measured = 0;
            try { measured = el.getComputedTextLength ? el.getComputedTextLength() : 0; } catch (err) { measured = 0; }
            return measured > 0 ? measured + 4 : textWidth(words);
        };

        // The edges, with a bend for each edge that shares its pair of nodes with another.
        var pairs = {};
        edges.forEach(function (edge) {
            var a = at[edge.from], b = at[edge.to];
            if (a === undefined || b === undefined || a === b) return;
            var key = Math.min(a, b) + ':' + Math.max(a, b);
            (pairs[key] = pairs[key] || []).push(edge);
        });
        edges.forEach(function (edge) {
            var a = at[edge.from], b = at[edge.to];
            if (a === undefined || b === undefined || a === b) return;
            var group = pairs[Math.min(a, b) + ':' + Math.max(a, b)];
            var g = make('g', { 'class': 'ask-graph-edge' }, edgeLayer);
            g.setAttribute('aria-label', edgeName(edge, nodes[a], nodes[b]));
            say(make('title', {}, g), edgeName(edge, nodes[a], nodes[b]));
            var words = edgeWords(edge, edgeChars);
            var drawnEdge = { el: g, a: a, b: b, relation: edge.relation || '',
                bend: arcBend(group.indexOf(edge), group.length),
                hit: make('path', { 'class': 'ask-graph-hit' }, g),
                line: make('path', { 'class': 'ask-graph-line', 'marker-end': 'url(#' + arrow + ')' }, g),
                label: null, words: words, t: 0.5, off: 0, width: textWidth(words) };
            if (words) {
                drawnEdge.label = say(make('text', { 'class': 'ask-graph-edge-label' + (edge.label ? ' is-named' : ''),
                    'text-anchor': 'middle' }, g), words);
            }
            placeEdge(drawnEdge);
            edgeEls.push(drawnEdge);
        });

        var degree = nodes.map(function () { return 0; });
        edgeEls.forEach(function (edge) { degree[edge.a] += 1; degree[edge.b] += 1; });
        // Labels are placed in this order: cited nodes first, then by their number of edges.
        var order = nodes.map(function (node, i) { return i; }).sort(function (a, b) {
            return (nodes[b].cited ? 1 : 0) - (nodes[a].cited ? 1 : 0) || degree[b] - degree[a] || a - b;
        });
        // Every label where it covers nothing (placeLabels): a node's below, above, right or left of it, an edge's at
        // a spot of its curve; with no free spot the selection shows it where it covers least, the whole graph
        // keeps it for hover or focus (is-crowded).
        var relabel = function () {
            var geo = {
                width: width, height: height, order: order,
                nodes: nodes.map(function (node, i) {
                    return { x: points[i].x, y: points[i].y, r: radius(node),
                        half: node.kind === 'count' ? boxHalf(node) : radius(node),
                        w: labelEls[i] ? widthOf(labelEls[i], cut(node.label, LABEL_CHARS)) : 0,
                        hidden: nodeEls[i].classList.contains('is-hidden') };
                }),
                edges: edgeEls.map(function (edge) {
                    var g = curveOf(edge);
                    edge.width = edge.label ? widthOf(edge.label, edge.words) : 0;
                    return { p: g.a, c: g.c, q: g.b, w: edge.width, keep: !!options.labelled,
                        hidden: edge.el.classList.contains('is-hidden') || edge.el.classList.contains('is-short') };
                })
            };
            var placed = placeLabels(geo);
            placed.nodes.forEach(function (spot, i) {
                if (!spot || !labelEls[i]) return;
                labelEls[i].setAttribute('x', String(round1(spot.x)));
                labelEls[i].setAttribute('y', String(round1(spot.y)));
                labelEls[i].setAttribute('text-anchor', spot.anchor);
            });
            placed.edges.forEach(function (spot, k) {
                var edge = edgeEls[k];
                if (!spot || !edge.label) return;
                edge.t = spot.t;
                edge.off = spot.off;
                edge.label.classList.toggle('is-crowded', spot.crowded);
                placeEdge(edge);
            });
        };

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
                labelEls[i] = say(make('text', { 'class': 'ask-graph-label' + (unnamed ? ' is-unnamed' : ''),
                    y: radius(node) + LABEL_GAP + TEXT_H - 4, 'text-anchor': 'middle' }, item),
                    cut(node.label, LABEL_CHARS));
            }
            ['mouseenter', 'focus'].forEach(function (name) {
                item.addEventListener(name, function () { focus(i, true); });
            });
            ['mouseleave', 'blur'].forEach(function (name) {
                item.addEventListener(name, function () { focus(i, false); });
            });

            // Dragging: the node follows the pointer once it moved DRAG_PX; a shorter press stays a click. Its label
            // moves with it, the edge labels with their edges; after the drag every label is placed again.
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
                if (dragged) relabel();
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
                relabel();
            });
        });
        // A link's own drag (of its address) would start instead of moving the node.
        svg.addEventListener('dragstart', function (event) { event.preventDefault(); });
        relabel();

        return {
            svg: svg,
            relabel: relabel,
            // Every node back where the layout placed it.
            reset: function () {
                nodes.forEach(function (node, i) { points[i] = { x: home[i].x, y: home[i].y }; placeNode(i); });
                edgeEls.forEach(placeEdge);
                relabel();
            },
            // Hide the nodes of the hidden types (COUNT_KEY: the counts) and the edges of the hidden relations;
            // a hidden node hides its edges too. The labels are placed again for what is left.
            hide: function (types, relations) {
                var gone = nodes.map(function (node) { return hiddenNode(node, types); });
                nodeEls.forEach(function (el, i) { el.classList.toggle('is-hidden', gone[i]); });
                edgeEls.forEach(function (edge) {
                    edge.el.classList.toggle('is-hidden', !!relations[edge.relation] || gone[edge.a] || gone[edge.b]);
                });
                relabel();
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
            state.scene.relabel();                   // on the page now: the labels' real widths
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
        DRAG_PX: DRAG_PX, placeLabels: placeLabels, segmentHitsBox: segmentHitsBox, overlapArea: overlapArea,
        quadPoint: quadPoint, curveControl: curveControl, arcBend: arcBend, boxHeight: boxHeight, edgeSpot: edgeSpot,
        nodeLabelSpot: nodeLabelSpot, textWidth: textWidth, TEXT_H: TEXT_H };

    Array.prototype.forEach.call(document.querySelectorAll('.ask-graph'), function (block) {
        if (block.open) start(block);
        block.addEventListener('toggle', function () { if (block.open) start(block); });
    });
})();
