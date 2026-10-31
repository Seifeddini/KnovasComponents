/* Brain Builder -- a company's processes as flows (build plan step B6).
 *
 * One JSON document (#b-proc-data, builder.processes.build) holds every
 * process in the schema's form, the problems the check found, and the
 * ontology a process may name (types with their attributes, relations).
 *
 * The canvas shows the chosen process: steps as boxes left to right in their
 * order, transitions as arrows above, rework back to an earlier step as a
 * dashed arrow below, resting steps with a double border. A step dragged
 * sideways takes a new place in the order; a transition is drawn by dragging
 * from one step onto another (in the "Übergang ziehen" mode or with Shift).
 * The side panel edits the process, a step or a transition.
 *
 * Every edit is one POST of an operation (builder.editing, the process ones
 * in builder.process_edits) to data-edit-url; the server writes it to the
 * draft with who and before/after, checks the processes again and answers
 * with the whole view, which this file redraws.
 */
(function () {
    'use strict';

    var dataEl = document.getElementById('b-proc-data');
    var graphEl = document.getElementById('b-proc-graph');
    var root = document.querySelector('[data-proc-edit]');
    if (!dataEl || !graphEl || !root || typeof cytoscape !== 'function') return;

    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    var panel = document.getElementById('b-proc-panel');
    var panelBody = panel.querySelector('[data-proc-panel-body]');
    var history = { undo: Number(root.dataset.undo || 0), redo: Number(root.dataset.redo || 0) };
    var pending = false;

    var BOX_W = 176, BOX_H = 64, GAP = 72;
    /* Below this zoom the boxes are not readable: the flow is panned instead. */
    var READABLE = 0.85;
    var data, typesByKey, current = null, selection = null, cy = null;

    /* ---- data ----------------------------------------------------------- */

    function index(next) {
        data = next;
        typesByKey = {};
        data.types.forEach(function (t) { typesByKey[t.key] = t; });
    }

    function processByKey(key) {
        return data.processes.find(function (p) { return p.key === key; }) || null;
    }

    function stepById(p, id) {
        return p.steps.find(function (s) { return s.id === id; }) || null;
    }

    function liveTypes() {
        return data.types.filter(function (t) { return !t.retired; });
    }

    function attributesOf(typeKey) {
        return (typesByKey[typeKey] || { attributes: [] }).attributes;
    }

    function enumValues(typeKey, fact) {
        var attr = attributesOf(typeKey).find(function (a) { return a.name === fact; });
        return attr && attr.datatype === 'enum' ? attr.enum_values : [];
    }

    /* A process's problems, and those of the document as a whole (its rules, its schema). */
    function problemsOf(p) {
        return data.problems.filter(function (f) { return f.process === p.key || !f.process; });
    }

    function ruleName(id) {
        var rule = data.rules.find(function (r) { return r.id === id; });
        return rule ? rule.name : id;
    }

    /* ---- the server ----------------------------------------------------- */

    function post(url, body) {
        return fetch(url, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'X-CSRF-Token': csrf, 'Accept': 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        }).then(function (response) {
            return response.json().catch(function () {
                return { success: false, error: 'Der Server hat nicht verständlich geantwortet.' };
            });
        }, function () {
            return { success: false, error: 'Der Server ist nicht erreichbar.' };
        });
    }

    function toast(title, text, tone) {
        if (window.BuilderUI && window.BuilderUI.toast) window.BuilderUI.toast({ title: title, text: text, tone: tone || 'good' });
    }

    function refused(payload) {
        toast(payload.error || 'Das hat nicht geklappt.', (payload.details || []).join(', '), 'critical');
    }

    /* One operation on the current process; resolves to the server's answer. */
    function edit(op, options) {
        if (pending) return Promise.resolve({ success: false, error: 'Einen Moment: die letzte Änderung läuft noch.' });
        pending = true;
        return post(root.dataset.editUrl, op).then(function (payload) {
            pending = false;
            if (!payload.success) {
                if (!(options && options.quiet)) refused(payload);
                return payload;
            }
            landed(payload, options);
            return payload;
        });
    }

    function landed(payload, options) {
        history = payload.history || history;
        showHistory();
        if (options && options.select !== undefined) selection = options.select;
        if (options && options.process) current = options.process;
        load(payload.processes);
        if (options && options.announce) toast(options.announce, null, 'good');
    }

    function step(which) {
        if (pending || !history[which]) return;
        pending = true;
        post(root.dataset[which + 'Url']).then(function (payload) {
            pending = false;
            if (!payload.success) { refused(payload); history[which] = Math.max(0, history[which] - 1); showHistory(); return; }
            landed(payload, { announce: which === 'undo' ? 'Rückgängig gemacht' : 'Wiederhergestellt' });
        });
    }

    function showHistory() {
        var undo = document.querySelector('[data-proc-undo]');
        var redo = document.querySelector('[data-proc-redo]');
        undo.disabled = !history.undo;
        redo.disabled = !history.redo;
        undo.title = history.undo ? 'Rückgängig (Strg+Z)' : 'Nichts rückgängig zu machen';
        redo.title = history.redo ? 'Wiederherstellen (Strg+Umschalt+Z)' : 'Nichts wiederherzustellen';
    }

    /* ---- small builders ------------------------------------------------- */

    function el(tagName, className, text) {
        var node = document.createElement(tagName);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    function tag(text, tone) {
        return el('span', 'b-tag' + (tone ? ' b-tag--' + tone : ''), text);
    }

    var uid = 0;
    function field(labelText, control, hint) {
        var box = el('div', 'b-field');
        control.id = control.id || 'b-pe-' + (++uid);
        var label = el('label', 'b-label', labelText);
        label.htmlFor = control.id;
        box.appendChild(label);
        box.appendChild(control);
        if (hint) {
            var h = el('span', 'b-hint', hint);
            h.id = control.id + '-hint';
            control.setAttribute('aria-describedby', h.id);
            box.appendChild(h);
        }
        return box;
    }

    function input(value, attrs) {
        var i = el('input', 'b-input');
        i.value = value || '';
        Object.keys(attrs || {}).forEach(function (k) { i.setAttribute(k, attrs[k]); });
        return i;
    }

    function textarea(value, rows) {
        var t = el('textarea', 'b-textarea');
        t.value = value || '';
        t.rows = rows || 3;
        return t;
    }

    function select(options, value, attrs) {
        var s = el('select', 'b-select');
        Object.keys(attrs || {}).forEach(function (k) { s.setAttribute(k, attrs[k]); });
        options.forEach(function (o) {
            var opt = el('option', null, o.label);
            opt.value = o.value;
            if (o.value === value) opt.selected = true;
            s.appendChild(opt);
        });
        return s;
    }

    function checkbox(labelText, checked) {
        var label = el('label', 'b-check');
        var box = el('input');
        box.type = 'checkbox';
        box.checked = !!checked;
        label.appendChild(box);
        label.appendChild(document.createTextNode(labelText));
        return { label: label, box: box };
    }

    function button(text, kind, onClick) {
        var b = el('button', 'b-button ' + (kind || 'b-button--secondary'), text);
        b.type = 'button';
        if (onClick) b.addEventListener('click', onClick);
        return b;
    }

    function section(title) {
        var box = el('section', 'b-panel-section');
        box.appendChild(el('h5', null, title));
        return box;
    }

    function actions() {
        return el('div', 'b-button-row b-ed-actions');
    }

    /* A destructive button that asks once more in place instead of a browser dialog. */
    function confirmButton(text, again, onConfirm) {
        var b = button(text, 'b-button--danger');
        var armed = false;
        b.addEventListener('click', function () {
            if (!armed) { armed = true; b.textContent = again; return; }
            onConfirm();
        });
        b.addEventListener('blur', function () { armed = false; b.textContent = text; });
        return b;
    }

    function problemBox(list) {
        var box = el('div', 'b-callout b-callout--critical');
        box.appendChild(el('p', 'b-callout-title', list.length === 1 ? '1 Problem' : list.length + ' Probleme'));
        var ul = el('ul');
        list.forEach(function (f) { ul.appendChild(el('li', null, f.message)); });
        box.appendChild(ul);
        return box;
    }

    /* ---- paths ---------------------------------------------------------- */

    /* The hops a path may take next from the given types: only relations whose
     * domain (or, inwards, range) fits -- the server's check, builder.processes. */
    function hopOptions(fromTypes) {
        var live = liveTypes().map(function (t) { return t.key; });
        var out = [];
        var seen = {};
        data.relations.forEach(function (r) {
            ['out', 'in'].forEach(function (direction) {
                var here = direction === 'out' ? r.domain : r.range;
                var there = direction === 'out' ? r.range : r.domain;
                var fits = here.indexOf('*') >= 0 || fromTypes.some(function (t) { return here.indexOf(t) >= 0; });
                if (!fits) return;
                var targets = there.indexOf('*') >= 0 ? live : there.filter(function (t) { return live.indexOf(t) >= 0; });
                targets.forEach(function (target) {
                    var id = r.key + '|' + direction + '|' + target;
                    if (seen[id]) return;
                    seen[id] = true;
                    out.push({ relation: r.key, direction: direction, to_type: target });
                });
            });
        });
        return out;
    }

    function hopText(h) {
        return h.direction === 'out' ? h.relation + ' → ' + h.to_type : '← ' + h.relation + ' · ' + h.to_type;
    }

    function pathText(path) {
        return path.map(hopText).join(' / ');
    }

    /* A path picker: one select per hop, each offering what fits after the one
     * before; a last, empty select takes the path one hop further. */
    function pathPicker(startTypes, initial, onChange, name) {
        var box = el('div', 'b-proc-path');
        var hops = (initial || []).slice();
        function render() {
            box.textContent = '';
            var from = startTypes;
            for (var i = 0; i <= hops.length; i++) {
                var options = hopOptions(from);
                var current = hops[i];
                var chosen = current ? options.findIndex(function (o) {
                    return o.relation === current.relation && o.direction === current.direction && o.to_type === current.to_type;
                }) : -1;
                var stale = -1;
                if (current && chosen < 0) {   // a hop that no longer fits: shown, so it can be replaced
                    options.push(current);
                    chosen = stale = options.length - 1;
                }
                var choices = [{ value: '', label: i ? '– weiter über … (optional) –' : '– Beziehung wählen –' }].concat(options.map(function (o, n) {
                    return { value: String(n), label: hopText(o) + (n === stale ? ' (passt nicht)' : '') };
                }));
                var s = select(choices, chosen >= 0 ? String(chosen) : '', { 'aria-label': (name || 'Pfad') + ', Schritt ' + (i + 1) });
                (function (at, opts, control) {
                    control.addEventListener('change', function () {
                        hops = hops.slice(0, at);
                        if (control.value !== '') hops.push(opts[Number(control.value)]);
                        render();
                        onChange(hops.slice());
                        var next = box.querySelectorAll('select')[at + 1];
                        if (next) next.focus();
                    });
                })(i, options, s);
                var row = el('div', 'b-proc-hop');
                row.appendChild(el('span', 'b-proc-hop-from b-mono b-small', i ? hops[i - 1].to_type : startTypes.join(', ')));
                row.appendChild(s);
                box.appendChild(row);
                if (!current) break;
                from = [current.to_type];
            }
        }
        render();
        return box;
    }

    /* ---- the list of processes ------------------------------------------ */

    function renderList() {
        var list = document.querySelector('[data-proc-list]');
        list.textContent = '';
        data.processes.forEach(function (p) {
            var li = el('li');
            var b = el('button', 'b-proc-item' + (p.key === current ? ' is-current' : ''));
            b.type = 'button';
            if (p.key === current) b.setAttribute('aria-current', 'true');
            b.appendChild(el('span', 'b-proc-item-name', p.name || p.key));
            var meta = el('span', 'b-proc-item-meta');
            meta.appendChild(el('span', 'b-mono', p.key));
            meta.appendChild(el('span', null, p.steps.length + ' Schritte'));
            b.appendChild(meta);
            if (p.problems.critical) b.appendChild(tag(p.problems.critical + (p.problems.critical === 1 ? ' Problem' : ' Probleme'), 'critical'));
            else if (p.problems.warning) b.appendChild(tag(p.problems.warning + ' Warnung' + (p.problems.warning === 1 ? '' : 'en'), 'warning'));
            b.addEventListener('click', function () { choose(p.key); });
            li.appendChild(b);
            list.appendChild(li);
        });
        document.querySelector('[data-proc-list-empty]').hidden = data.processes.length > 0;
        document.querySelector('[data-proc-new-step]').hidden = !current;
    }

    function renderHead() {
        var head = document.querySelector('[data-proc-head]');
        var draft = head.querySelector('.b-tag--accent');
        head.textContent = '';
        head.appendChild(tag(data.counts.processes + ' Prozesse'));
        head.appendChild(tag(data.counts.steps + ' Schritte'));
        [['critical', ' Problem', ' Probleme'], ['warning', ' Warnung', ' Warnungen']].forEach(function (words) {
            var n = data.counts.problems[words[0]];
            if (!n) return;
            var t = tag('', words[0]);
            t.appendChild(el('span', 'b-tag-dot'));
            t.appendChild(document.createTextNode(n + (n === 1 ? words[1] : words[2])));
            head.appendChild(t);
        });
        if (draft) head.appendChild(draft);
    }

    function choose(key) {
        current = key;
        selection = null;
        if (window.history && window.history.replaceState) window.history.replaceState(null, '', '#' + key);
        renderList();
        draw(true);
        showSelection();
    }

    /* ---- the canvas ----------------------------------------------------- */

    function token(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    function fontToken(name) { return token(name).replace(/['"]/g, ''); }

    function stepLabel(p, s, n) {
        var role = s.owner_role ? ' · ' + s.owner_role.replace(/^ROLE-/, '').toLowerCase() : '';
        return (s.name || s.id) + '\n' + n + role + (s.resting ? ' · ruht' : '');
    }

    function elements(p) {
        var problems = problemsOf(p);
        var bad = {};
        problems.forEach(function (f) { if (f.step && f.severity === 'critical') bad[f.step] = true; });
        var position = {};
        p.steps.forEach(function (s, i) { position[s.id] = i; });
        var out = p.steps.map(function (s, i) {
            var classes = ['step'];
            if (s.resting) classes.push('resting');
            if (i === 0) classes.push('first');
            if (bad[s.id]) classes.push('problem');
            return { group: 'nodes', data: { id: 's:' + s.id, step: s.id, label: stepLabel(p, s, i + 1) },
                     position: { x: i * (BOX_W + GAP), y: 0 }, classes: classes.join(' ') };
        });
        p.transitions.forEach(function (t) {
            if (!(t.from in position) || !(t.to in position)) return;
            var span = Math.abs(position[t.to] - position[t.from]);
            var bent = t.rework || span > 1;
            var bad2 = problems.some(function (f) { return f.transition === t.from + '>' + t.to; });
            out.push({ group: 'edges', data: {
                id: 't:' + t.from + '>' + t.to, source: 's:' + t.from, target: 's:' + t.to, from: t.from, to: t.to,
                // Forward arrows bend up, rework bends down: the same sign does both, the direction flips the side.
                bend: bent ? -(26 + 16 * Math.min(span, 8)) : 0,
            }, classes: (t.rework ? 'rework' : 'forward') + (bent ? ' bent' : '') + (bad2 ? ' problem' : '') });
        });
        return out;
    }

    function style() {
        var sans = fontToken('--font-sans') || 'IBM Plex Sans, sans-serif';
        return [
            { selector: 'node.step', style: {
                'shape': 'round-rectangle', 'width': BOX_W, 'height': BOX_H,
                'background-color': token('--color-surface'), 'border-width': 2, 'border-color': token('--color-accent'),
                'label': 'data(label)', 'text-wrap': 'wrap', 'text-max-width': BOX_W - 20,
                'text-valign': 'center', 'text-halign': 'center', 'font-family': sans,
                'font-size': 13, 'line-height': 1.45, 'font-weight': 500, 'color': token('--color-text'), 'min-zoomed-font-size': 6,
            } },
            { selector: 'node.first', style: { 'border-width': 3 } },
            { selector: 'node.resting', style: {
                'border-style': 'double', 'border-width': 6, 'background-color': token('--color-accent-soft'),
            } },
            { selector: 'node.problem', style: { 'border-color': token('--color-critical'), 'border-width': 3 } },
            { selector: 'node.resting.problem', style: { 'border-width': 6 } },
            { selector: 'node.step:selected', style: { 'border-color': token('--color-focus'), 'overlay-opacity': 0 } },
            { selector: 'node.connect-source', style: { 'border-color': token('--color-focus'), 'border-width': 4 } },
            { selector: 'node.connect-target', style: { 'border-color': token('--color-accent-hover'), 'border-width': 4 } },
            { selector: 'node.ghost', style: { 'width': 1, 'height': 1, 'opacity': 0, 'events': 'no' } },
            { selector: 'edge', style: {
                'width': 1.75, 'line-color': token('--color-border-strong'), 'target-arrow-color': token('--color-border-strong'),
                'target-arrow-shape': 'triangle', 'arrow-scale': 1.1, 'curve-style': 'straight',
            } },
            { selector: 'edge.bent', style: {
                'curve-style': 'unbundled-bezier', 'control-point-distances': 'data(bend)', 'control-point-weights': 0.5,
            } },
            { selector: 'edge.rework', style: {
                'line-style': 'dashed', 'line-dash-pattern': [7, 5], 'line-color': token('--color-warning'),
                'target-arrow-color': token('--color-warning'),
            } },
            { selector: 'edge.problem', style: { 'line-color': token('--color-critical'), 'target-arrow-color': token('--color-critical') } },
            { selector: 'edge:selected', style: { 'width': 3.5, 'line-color': token('--color-focus'), 'target-arrow-color': token('--color-focus'), 'overlay-opacity': 0 } },
            { selector: 'edge.ghost', style: {
                'width': 2, 'line-color': token('--color-focus'), 'target-arrow-color': token('--color-focus'),
                'line-style': 'dashed', 'curve-style': 'straight', 'events': 'no',
            } },
        ];
    }

    function draw(refit) {
        var p = processByKey(current);
        var empty = document.querySelector('[data-proc-canvas-empty]');
        if (!cy) buildCanvas();
        cy.elements().remove();
        if (!p) {
            empty.hidden = false;
            empty.textContent = data.processes.length ? 'Wählen Sie links einen Prozess.' : 'Noch kein Prozess. Legen Sie mit «Neuer Prozess» den ersten an.';
            renderCheck(null);
            return;
        }
        empty.hidden = p.steps.length > 0;
        empty.textContent = 'Der Prozess hat noch keine Schritte. Legen Sie mit «Neuer Schritt» den ersten an.';
        cy.add(elements(p));
        markSelection();
        if (refit) fit(false);
        else reveal();
        renderCheck(p);
    }

    /* The part of the canvas the side panel leaves free, in rendered pixels. */
    function freeArea() {
        var covered = panel.hidden ? 0 : panel.getBoundingClientRect().width;
        return { x1: 0, x2: Math.max(240, graphEl.clientWidth - covered), y1: 64, y2: graphEl.clientHeight - 40 };
    }

    /* Show the flow: all of it when ``whole`` or when it fits readably, else from its first step. */
    function fit(whole) {
        if (!cy || !cy.elements().length) return;
        var area = freeArea();
        var box = cy.elements().boundingBox();
        var zoom = Math.min(1.1, (area.x2 - area.x1 - 96) / box.w, (area.y2 - area.y1 - 48) / box.h);
        if (!whole) zoom = Math.max(zoom, READABLE);
        cy.zoom(zoom);
        var midY = (area.y1 + area.y2) / 2 - ((box.y1 + box.y2) / 2) * zoom;
        if (box.w * zoom <= area.x2 - area.x1 - 96) {
            cy.pan({ x: (area.x1 + area.x2) / 2 - ((box.x1 + box.x2) / 2) * zoom, y: midY });
        } else {
            cy.pan({ x: area.x1 + 48 - box.x1 * zoom, y: midY });
        }
        reveal();
    }

    /* Pan the selected step into the free part of the canvas when it is outside it. */
    function reveal() {
        if (!cy || !selection || selection.kind !== 'step') return;
        var node = cy.getElementById('s:' + selection.id);
        if (!node.length) return;
        var area = freeArea();
        var at = node.renderedPosition();
        var half = BOX_W * cy.zoom() / 2;
        if (at.x - half >= area.x1 + 16 && at.x + half <= area.x2 - 16) return;
        var pan = cy.pan();
        cy.pan({ x: pan.x + ((area.x1 + area.x2) / 2 - at.x), y: pan.y });
    }

    function buildCanvas() {
        cy = cytoscape({
            container: graphEl, elements: [], style: style(), layout: { name: 'preset' },
            minZoom: 0.15, maxZoom: 2.5, wheelSensitivity: 0.2, boxSelectionEnabled: false,
        });
        cy.on('tap', 'node.step', function (evt) { selection = { kind: 'step', id: evt.target.data('step') }; showSelection(); reveal(); });
        cy.on('tap', 'edge', function (evt) {
            if (evt.target.hasClass('ghost')) return;
            selection = { kind: 'transition', from: evt.target.data('from'), to: evt.target.data('to') };
            showSelection();
        });
        cy.on('tap', function (evt) { if (evt.target === cy) { selection = { kind: 'process' }; showSelection(); } });
        cy.on('dbltap', function (evt) { if (evt.target === cy) fit(true); });
        wireReorder();
        wireConnect();
        var restyle = function () { cy.style(style()); };
        new MutationObserver(restyle).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
        if (window.matchMedia) {
            var media = window.matchMedia('(prefers-color-scheme: dark)');
            if (media.addEventListener) media.addEventListener('change', restyle);
        }
    }

    function markSelection() {
        if (!cy) return;
        cy.elements().unselect();
        if (!selection) return;
        if (selection.kind === 'step') cy.getElementById('s:' + selection.id).select();
        if (selection.kind === 'transition') cy.getElementById('t:' + selection.from + '>' + selection.to).select();
    }

    /* Drag a step sideways: it takes the place its new position falls in. */
    function wireReorder() {
        var row = 0;
        cy.on('grab', 'node.step', function (evt) { row = evt.target.position('y'); });
        cy.on('drag', 'node.step', function (evt) { evt.target.position('y', row); });
        cy.on('dragfree', 'node.step', function (evt) {
            var p = processByKey(current);
            var moved = evt.target.data('step');
            var order = cy.nodes('node.step').sort(function (a, b) { return a.position('x') - b.position('x'); })
                .map(function (n) { return n.data('step'); });
            var to = order.indexOf(moved);
            var from = p.steps.findIndex(function (s) { return s.id === moved; });
            if (to === from) { draw(false); return; }
            edit({ op: 'step.move', key: p.key, step: moved, to: to }, { select: { kind: 'step', id: moved } })
                .then(function (payload) { if (!payload.success) draw(false); });
        });
    }

    var connectMode = false;
    var connect = null;

    function setConnectMode(on) {
        connectMode = !!on;
        cy.autoungrabify(connectMode);
        cy.userPanningEnabled(!connectMode);
        graphEl.classList.toggle('is-connecting', connectMode);
        document.querySelector('[data-proc-connect]').setAttribute('aria-pressed', String(connectMode));
    }

    function stepAt(position) {
        var hit = null;
        cy.nodes('node.step').forEach(function (n) {
            var box = n.boundingBox();
            if (position.x >= box.x1 && position.x <= box.x2 && position.y >= box.y1 && position.y <= box.y2) hit = n;
        });
        return hit;
    }

    function wireConnect() {
        cy.on('tapstart', 'node.step', function (evt) {
            var shift = evt.originalEvent && evt.originalEvent.shiftKey;
            if (!connectMode && !shift) return;
            if (shift && !connectMode) { cy.autoungrabify(true); cy.userPanningEnabled(false); }
            var source = evt.target;
            cy.add([
                { group: 'nodes', data: { id: 'ghost:node' }, position: Object.assign({}, evt.position), classes: 'ghost' },
                { group: 'edges', data: { id: 'ghost:edge', source: source.id(), target: 'ghost:node' }, classes: 'ghost' },
            ]);
            source.addClass('connect-source');
            connect = { source: source };
        });
        cy.on('tapdrag', function (evt) {
            if (!connect) return;
            cy.getElementById('ghost:node').position(evt.position);
            cy.nodes('node.step').removeClass('connect-target');
            var over = stepAt(evt.position);
            if (over && over !== connect.source) over.addClass('connect-target');
        });
        cy.on('tapend', function (evt) {
            if (!connect) return;
            var source = connect.source.data('step');
            var target = stepAt(evt.position);
            cy.remove(cy.getElementById('ghost:node'));
            cy.nodes().removeClass('connect-source connect-target');
            connect = null;
            if (!connectMode) { cy.autoungrabify(false); cy.userPanningEnabled(true); }
            if (!target || target.data('step') === source) return;
            var to = target.data('step');
            edit({ op: 'transition.add', key: current, from: source, to: to },
                 { select: { kind: 'transition', from: source, to: to } });
        });
    }

    /* ---- the check ------------------------------------------------------ */

    function renderCheck(p) {
        var list = document.querySelector('[data-proc-check-list]');
        list.textContent = '';
        if (!p) return;
        var found = problemsOf(p);
        if (!found.length) {
            var ok = el('li', 'b-proc-check-ok');
            ok.appendChild(tag('Keine Probleme', 'good'));
            ok.appendChild(document.createTextNode(' Schema, Übergänge, Statuszuordnung und Pfade passen.'));
            list.appendChild(ok);
            return;
        }
        found.forEach(function (f) {
            var li = el('li', 'b-proc-check-item');
            li.appendChild(tag(f.severity === 'critical' ? 'Problem' : 'Warnung', f.severity === 'critical' ? 'critical' : 'warning'));
            var text = el('span', null, ' ' + f.message);
            li.appendChild(text);
            if (f.step || f.transition) {
                var go = button(f.step ? 'Schritt ' + f.step : 'Übergang', 'b-button--quiet b-button--small', function () {
                    if (f.step) selection = { kind: 'step', id: f.step };
                    else { var parts = f.transition.split('>'); selection = { kind: 'transition', from: parts[0], to: parts[1] }; }
                    showSelection();
                    reveal();
                });
                li.appendChild(go);
            }
            list.appendChild(li);
        });
    }

    /* ---- the panel ------------------------------------------------------ */

    function openPanel(title, key) {
        var opening = panel.hidden;
        panel.querySelector('[data-proc-panel-title]').textContent = title;
        panel.querySelector('[data-proc-panel-key]').textContent = key || '';
        var keep = panelBody.scrollTop;
        panelBody.textContent = '';
        panel.hidden = false;
        if (opening) window.requestAnimationFrame(reveal);
        return keep;
    }

    function showSelection() {
        markSelection();
        var p = processByKey(current);
        if (!p || !selection) { panel.hidden = true; return; }
        if (selection && selection.kind === 'step' && stepById(p, selection.id)) return stepPanel(p, stepById(p, selection.id));
        if (selection && selection.kind === 'transition') {
            var t = p.transitions.find(function (x) { return x.from === selection.from && x.to === selection.to; });
            if (t) return transitionPanel(p, t);
        }
        selection = { kind: 'process' };
        processPanel(p);
    }

    function processPanel(p) {
        var keep = openPanel(p.name || p.key, p.key);
        var mine = problemsOf(p).filter(function (f) { return !f.step && !f.transition; });
        if (mine.length) panelBody.appendChild(problemBox(mine));

        var form = el('form', 'b-ed-form');
        var name = input(p.name);
        var description = textarea(p.description, 3);
        var role = input(p.owner_role, { list: 'b-proc-roles', spellcheck: 'false' });
        role.classList.add('b-mono');
        var types = el('select', 'b-select');
        types.multiple = true;
        types.size = 6;
        liveTypes().forEach(function (t) {
            var o = el('option', null, t.name === t.key ? t.key : t.name + ' (' + t.key + ')');
            o.value = t.key;
            o.selected = p.instance_types.indexOf(t.key) >= 0;
            types.appendChild(o);
        });
        var facts = {};
        p.instance_types.forEach(function (t) {
            attributesOf(t).forEach(function (a) { if (a.datatype === 'enum') facts[a.name] = true; });
        });
        if (p.governing_fact) facts[p.governing_fact] = true;
        var governing = select(Object.keys(facts).sort().map(function (f) { return { value: f, label: f }; }), p.governing_fact);
        var stream = input(p.value_stream, { spellcheck: 'false' });
        form.appendChild(field('Name', name));
        form.appendChild(field('Beschreibung', description));
        form.appendChild(field('Verantwortliche Rolle', role));
        form.appendChild(field('Instanztypen', types, 'Die Typen, deren Vorgänge den Prozess durchlaufen (Strg für mehrere).'));
        form.appendChild(field('Steuerndes Attribut', governing, 'Das Auswahl-Attribut der Instanz, dessen Wert den Schritt nennt.'));
        form.appendChild(field('Wertstrom', stream));
        var row = actions();
        row.appendChild(button('Speichern', 'b-button--primary', function () {
            edit({ op: 'process.update', key: p.key, fields: {
                name: name.value, description: description.value, owner_role: role.value,
                instance_types: Array.prototype.filter.call(types.options, function (o) { return o.selected; }).map(function (o) { return o.value; }),
                governing_fact: governing.value, value_stream: stream.value,
            } }, { select: { kind: 'process' } });
        }));
        form.appendChild(row);
        form.addEventListener('submit', function (e) { e.preventDefault(); });
        panelBody.appendChild(form);

        var map = section('Statuszuordnung');
        map.appendChild(el('p', 'b-small b-muted', 'Welcher Wert von ' + p.governing_fact + ' welchen Schritt bedeutet.'));
        p.instance_types.forEach(function (t) { map.appendChild(statusTable(p, t)); });
        if (!p.instance_types.length) map.appendChild(el('p', 'b-muted', 'Zuerst einen Instanztyp wählen.'));
        panelBody.appendChild(map);

        var context = section('Kontext-Artefakte');
        context.appendChild(el('p', 'b-small b-muted', 'Was in jedem Schritt zum Vorgang gehört.'));
        context.appendChild(artefactList(p, null, 'context', p.context));
        panelBody.appendChild(context);

        var danger = section('Prozess');
        danger.appendChild(confirmButton('Prozess löschen', 'Wirklich löschen?', function () {
            edit({ op: 'process.remove', key: p.key }, { process: null, select: null, announce: 'Prozess gelöscht' });
        }));
        panelBody.appendChild(danger);
        panelBody.scrollTop = keep;
    }

    function statusTable(p, typeKey) {
        var map = p.status_map[typeKey] || {};
        var values = enumValues(typeKey, p.governing_fact).slice();
        Object.keys(map).forEach(function (v) { if (values.indexOf(v) < 0) values.push(v); });
        var wrap = el('div', 'b-table-wrap');
        var table = el('table', 'b-table b-table--compact');
        var caption = el('caption', 'b-small', typeKey + '.' + p.governing_fact);
        table.appendChild(caption);
        var body = el('tbody');
        if (!values.length) {
            var tr0 = el('tr'); var td0 = el('td', 'b-muted', typeKey + ' hat kein Auswahl-Attribut ' + p.governing_fact + '.');
            td0.colSpan = 2; tr0.appendChild(td0); body.appendChild(tr0);
        }
        values.forEach(function (v) {
            var tr = el('tr');
            tr.appendChild(el('td', 'b-mono', v));
            var options = [{ value: '__none', label: '– nicht zugeordnet –' }].concat(p.steps.map(function (s) {
                return { value: s.id, label: s.name || s.id };
            })).concat([{ value: '__null', label: '(nicht aus dem Wert allein)' }]);
            var value = !(v in map) ? '__none' : (map[v] === null ? '__null' : map[v]);
            var s = select(options, value, { 'aria-label': typeKey + '.' + p.governing_fact + ' = ' + v });
            s.addEventListener('change', function () {
                var op = { op: 'status.set', key: p.key, type: typeKey, value: v };
                if (s.value === '__none') op.unmapped = true;
                else op.step = s.value === '__null' ? null : s.value;
                edit(op);
            });
            var td = el('td'); td.appendChild(s); tr.appendChild(td);
            body.appendChild(tr);
        });
        table.appendChild(body);
        wrap.appendChild(table);
        return wrap;
    }

    function stepPanel(p, s) {
        var keep = openPanel(s.name || s.id, s.key);
        var mine = problemsOf(p).filter(function (f) { return f.step === s.id; });
        if (mine.length) panelBody.appendChild(problemBox(mine));

        var form = el('form', 'b-ed-form');
        var name = input(s.name);
        var description = textarea(s.description, 3);
        var role = input(s.owner_role, { list: 'b-proc-roles', spellcheck: 'false' });
        role.classList.add('b-mono');
        var resting = checkbox('Ruheschritt: hier ruht ein Vorgang, ohne als hängend zu gelten', s.resting);
        form.appendChild(field('Name', name));
        form.appendChild(field('Beschreibung', description));
        form.appendChild(field('Verantwortliche Rolle', role));
        form.appendChild(resting.label);
        var row = actions();
        row.appendChild(button('Speichern', 'b-button--primary', function () {
            edit({ op: 'step.update', key: p.key, step: s.id, fields: {
                name: name.value, description: description.value, owner_role: role.value, resting: resting.box.checked,
            } });
        }));
        form.appendChild(row);
        form.addEventListener('submit', function (e) { e.preventDefault(); });
        panelBody.appendChild(form);

        var status = section('Statuswerte');
        status.appendChild(el('p', 'b-small b-muted', 'Die Werte von ' + p.governing_fact + ', die diesen Schritt bedeuten.'));
        p.instance_types.forEach(function (t) {
            var map = p.status_map[t] || {};
            var values = enumValues(t, p.governing_fact);
            if (!values.length) return;
            var group = el('fieldset', 'b-field b-choice b-proc-values');
            group.appendChild(el('legend', 'b-label', t));
            values.forEach(function (v) {
                var elsewhere = map[v] && map[v] !== s.id ? ' (jetzt ' + map[v] + ')' : '';
                var c = checkbox(v + elsewhere, map[v] === s.id);
                c.box.addEventListener('change', function () {
                    var op = { op: 'status.set', key: p.key, type: t, value: v };
                    if (c.box.checked) op.step = s.id; else op.unmapped = true;
                    edit(op);
                });
                group.appendChild(c.label);
            });
            status.appendChild(group);
        });
        panelBody.appendChild(status);

        panelBody.appendChild(criteriaSection(p, s));

        var needs = section('Braucht');
        needs.appendChild(el('p', 'b-small b-muted', 'Was vorliegen muss, bevor der Schritt beginnt.'));
        needs.appendChild(artefactList(p, s, 'requires', s.requires));
        panelBody.appendChild(needs);
        var gives = section('Liefert');
        gives.appendChild(el('p', 'b-small b-muted', 'Was der Schritt hervorbringt.'));
        gives.appendChild(artefactList(p, s, 'produces', s.produces));
        panelBody.appendChild(gives);

        var more = section('Schritt');
        var newId = input(s.id, { spellcheck: 'false', 'aria-label': 'Neue Schritt-ID' });
        newId.classList.add('b-mono');
        var renameRow = el('div', 'b-proc-inline');
        renameRow.appendChild(newId);
        renameRow.appendChild(button('Umbenennen', 'b-button--secondary', function () {
            if (newId.value.trim() === s.id) return;
            var to = newId.value.trim();
            edit({ op: 'step.rename', key: p.key, step: s.id, to: to }, { select: { kind: 'step', id: to } });
        }));
        more.appendChild(field('Schritt-ID', renameRow, 'Übergänge, Ruheschritte und Statuszuordnung ziehen mit.'));
        more.appendChild(confirmButton('Schritt löschen', 'Wirklich löschen?', function () {
            edit({ op: 'step.remove', key: p.key, step: s.id }, { select: { kind: 'process' } });
        }));
        panelBody.appendChild(more);
        panelBody.scrollTop = keep;
    }

    function criteriaSection(p, s) {
        var box = section('Abschlusskriterien');
        var list = el('ul', 'b-proc-criteria');
        s.exit_criteria.forEach(function (id) {
            var li = el('li');
            li.appendChild(el('span', null, ruleName(id)));
            li.appendChild(el('span', 'b-mono b-small b-muted', ' ' + id));
            var remove = el('button', 'b-button b-button--quiet b-button--icon b-button--small', '×');
            remove.type = 'button';
            remove.setAttribute('aria-label', 'Kriterium ' + id + ' entfernen');
            remove.addEventListener('click', function () {
                edit({ op: 'step.update', key: p.key, step: s.id,
                       fields: { exit_criteria: s.exit_criteria.filter(function (x) { return x !== id; }) } });
            });
            li.appendChild(remove);
            list.appendChild(li);
        });
        if (!s.exit_criteria.length) list.appendChild(el('li', 'b-muted', 'Noch kein Kriterium.'));
        box.appendChild(list);

        var free = data.rules.filter(function (r) { return s.exit_criteria.indexOf(r.id) < 0; });
        var pick = select([{ value: '', label: '– Regel wählen –' }].concat(free.map(function (r) {
            return { value: r.id, label: r.id + ' · ' + r.name };
        })), '', { 'aria-label': 'Vorhandene Regel als Kriterium' });
        var pickRow = el('div', 'b-proc-inline');
        pickRow.appendChild(pick);
        pickRow.appendChild(button('Hinzufügen', 'b-button--secondary', function () {
            if (pick.value) edit({ op: 'criterion.add', key: p.key, step: s.id, rule: pick.value });
        }));
        box.appendChild(pickRow);

        var details = el('details', 'b-proc-new-criterion');
        details.appendChild(el('summary', null, 'Neues Kriterium als Text'));
        var text = input('', { placeholder: 'z. B. Sicherheitsprüfung abgeschlossen' });
        var checks = select(Object.keys(data.checks).map(function (k) { return { value: k, label: data.checks[k] }; }), 'fact_present');
        var attrNames = {};
        p.instance_types.forEach(function (t) { attributesOf(t).forEach(function (a) { attrNames[a.name] = true; }); });
        var factSelect = el('select', 'b-select');
        Object.keys(attrNames).sort().forEach(function (n) { var o = el('option', null, n); o.value = n; factSelect.appendChild(o); });
        function syncMultiple() {
            factSelect.multiple = checks.value === 'facts_present';
            factSelect.size = factSelect.multiple ? 5 : 1;
        }
        checks.addEventListener('change', syncMultiple);
        syncMultiple();
        details.appendChild(field('Kriterium', text));
        details.appendChild(field('Geprüft wird', checks));
        details.appendChild(field('Attribut der Instanz', factSelect));
        var row = actions();
        row.appendChild(button('Kriterium anlegen', 'b-button--primary', function () {
            var chosen = Array.prototype.filter.call(factSelect.options, function (o) { return o.selected; }).map(function (o) { return o.value; });
            var rule = { name: text.value, check: checks.value };
            if (checks.value === 'facts_present') rule.facts = chosen; else rule.fact = chosen[0] || '';
            edit({ op: 'criterion.add', key: p.key, step: s.id, rule: rule });
        }));
        details.appendChild(row);
        box.appendChild(details);
        return box;
    }

    function artefactList(p, s, kind, items) {
        var box = el('div', 'b-proc-artefacts');
        var list = el('ul', 'b-proc-artefact-list');
        items.forEach(function (a, i) {
            var li = el('li', 'b-proc-artefact');
            var head = el('div', 'b-proc-artefact-head');
            head.appendChild(el('strong', null, a.type));
            if (a.required) head.appendChild(tag('Pflicht', 'accent'));
            if (a.min_count > 1) head.appendChild(tag('mind. ' + a.min_count));
            if (!a.verified) head.appendChild(tag('ungeprüft', 'warning'));
            li.appendChild(head);
            li.appendChild(el('p', 'b-mono b-small', pathText(a.path)));
            if (a.alternative) li.appendChild(el('p', 'b-small', 'oder ' + a.alternative.type + ': ' + pathText(a.alternative.path)));
            if (a.where) li.appendChild(el('p', 'b-small b-muted', 'nur wenn ' + a.where.fact + ' = ' + a.where.equals));
            if (a.note) li.appendChild(el('p', 'b-small b-muted', a.note));
            var tools = el('div', 'b-button-row');
            tools.appendChild(button('Bearbeiten', 'b-button--quiet b-button--small', function () {
                li.replaceWith(artefactForm(p, s, kind, a, i));
            }));
            tools.appendChild(confirmButton('Entfernen', 'Wirklich entfernen?', function () {
                edit({ op: 'artefact.set', key: p.key, step: s ? s.id : null, kind: kind, index: i, artefact: null });
            }));
            li.appendChild(tools);
            list.appendChild(li);
        });
        if (!items.length) list.appendChild(el('li', 'b-muted', 'Keine.'));
        box.appendChild(list);
        var add = button('Artefakt hinzufügen', 'b-button--secondary', function () {
            add.replaceWith(artefactForm(p, s, kind, null, null));
        });
        box.appendChild(add);
        return box;
    }

    function artefactForm(p, s, kind, a, at) {
        var form = el('div', 'b-proc-artefact-form');
        var state = {
            path: a ? a.path.slice() : [], alternative: a && a.alternative ? a.alternative.path.slice() : [],
        };
        var typeLine = el('p', 'b-small');
        function showType() {
            typeLine.textContent = state.path.length ? 'Typ: ' + state.path[state.path.length - 1].to_type : 'Typ: ergibt sich aus dem Pfad';
        }
        showType();
        var whereBox = el('div', 'b-ed-pair');
        var whereFact = el('select', 'b-select');
        var whereEquals = input(a && a.where ? a.where.equals : '');
        function fillWhere() {
            var end = state.path.length ? state.path[state.path.length - 1].to_type : null;
            whereFact.textContent = '';
            var none = el('option', null, '– keine Bedingung –'); none.value = ''; whereFact.appendChild(none);
            (end ? attributesOf(end) : []).forEach(function (attr) {
                var o = el('option', null, attr.name); o.value = attr.name;
                if (a && a.where && a.where.fact === attr.name) o.selected = true;
                whereFact.appendChild(o);
            });
        }
        fillWhere();
        form.appendChild(field('Pfad von der Instanz', pathPicker(p.instance_types, state.path, function (hops) {
            state.path = hops; showType(); fillWhere();
        }), 'Nur Beziehungen, deren Definitionsbereich zum Typ davor passt.'));
        form.appendChild(typeLine);
        var count = input(String(a ? a.min_count : 1), { type: 'number', min: '1', max: '999' });
        var required = checkbox('Pflicht: ohne das Artefakt ist der Schritt nicht fertig', a ? a.required : true);
        var verified = checkbox('Geprüft: beim Mandanten gibt es Kanten dieser Form', a ? a.verified : true);
        var pair = el('div', 'b-ed-pair');
        pair.appendChild(field('Mindestanzahl', count));
        form.appendChild(pair);
        form.appendChild(required.label);
        form.appendChild(verified.label);
        whereBox.appendChild(field('Nur wenn', whereFact));
        whereBox.appendChild(field('gleich', whereEquals));
        form.appendChild(whereBox);
        var alt = checkbox('Alternative: ein anderes Artefakt genügt auch', !!(a && a.alternative));
        form.appendChild(alt.label);
        var altBox = el('div', 'b-proc-alt');
        altBox.appendChild(field('Pfad der Alternative', pathPicker(p.instance_types, state.alternative, function (hops) {
            state.alternative = hops;
        }, 'Alternative')));
        altBox.hidden = !alt.box.checked;
        alt.box.addEventListener('change', function () { altBox.hidden = !alt.box.checked; });
        form.appendChild(altBox);
        var note = input(a ? a.note : '');
        form.appendChild(field('Notiz', note));
        if (kind !== 'context' && s) {
            var kinds = select([{ value: 'requires', label: 'Braucht (vor dem Schritt)' }, { value: 'produces', label: 'Liefert (im Schritt)' }], kind);
            form.insertBefore(field('Art', kinds), form.firstChild);
            form._kind = kinds;
        }
        var error = el('div', 'b-callout b-callout--critical');
        error.setAttribute('role', 'alert');
        error.hidden = true;
        form.appendChild(error);
        var row = actions();
        row.appendChild(button('Abbrechen', 'b-button--quiet', function () { showSelection(); }));
        row.appendChild(button(a ? 'Artefakt speichern' : 'Artefakt hinzufügen', 'b-button--primary', function () {
            var artefact = {
                path: state.path, required: required.box.checked, min_count: Number(count.value) || 1,
                verified: verified.box.checked, note: note.value,
            };
            if (alt.box.checked && state.alternative.length) artefact.alternative = { path: state.alternative };
            if (whereFact.value && whereEquals.value.trim()) artefact.where = { fact: whereFact.value, equals: whereEquals.value.trim() };
            var target = form._kind ? form._kind.value : kind;
            var op = { op: 'artefact.set', key: p.key, step: s ? s.id : null, kind: target, artefact: artefact };
            if (a && target === kind) op.index = at;
            var run = (a && target !== kind)
                // Moved between braucht and liefert: added there, then taken out here.
                ? edit(op, { quiet: true }).then(function (payload) {
                    if (!payload.success) return payload;
                    return edit({ op: 'artefact.set', key: p.key, step: s.id, kind: kind, index: at, artefact: null });
                })
                : edit(op, { quiet: true });
            run.then(function (payload) {
                if (payload.success) return;
                error.textContent = payload.error + ((payload.details || []).length ? ' (' + payload.details.join(', ') + ')' : '');
                error.hidden = false;
            });
        }));
        form.appendChild(row);
        return form;
    }

    function transitionPanel(p, t) {
        var keep = openPanel((t.rework ? 'Nacharbeit ' : 'Übergang ') + t.from + ' → ' + t.to, p.key);
        var mine = problemsOf(p).filter(function (f) { return f.transition === t.from + '>' + t.to; });
        if (mine.length) panelBody.appendChild(problemBox(mine));
        var from = stepById(p, t.from), to = stepById(p, t.to);
        var dl = el('dl', 'b-dl');
        [['Von', from ? (from.name || from.id) : t.from], ['Nach', to ? (to.name || to.id) : t.to]].forEach(function (r) {
            dl.appendChild(el('dt', null, r[0])); dl.appendChild(el('dd', null, r[1]));
        });
        panelBody.appendChild(dl);
        var rework = checkbox('Nacharbeit: führt zu einem früheren Schritt zurück', t.rework);
        rework.box.addEventListener('change', function () {
            edit({ op: 'transition.update', key: p.key, from: t.from, to: t.to, rework: rework.box.checked });
        });
        panelBody.appendChild(rework.label);
        panelBody.appendChild(el('p', 'b-small b-muted',
            'Vorwärts-Übergänge bilden keinen Kreis; wer zurückführt, ist Nacharbeit und erscheint gestrichelt.'));
        var row = actions();
        row.appendChild(confirmButton('Übergang löschen', 'Wirklich löschen?', function () {
            edit({ op: 'transition.remove', key: p.key, from: t.from, to: t.to }, { select: { kind: 'process' } });
        }));
        panelBody.appendChild(row);
        panelBody.scrollTop = keep;
    }

    /* ---- dialogs -------------------------------------------------------- */

    var lastOpener = null;
    function openDialog(dialog) {
        lastOpener = document.activeElement;
        dialog.querySelector('[data-ed-error]').hidden = true;
        dialog.showModal();
    }
    function dialogError(dialog, payload) {
        var box = dialog.querySelector('[data-ed-error]');
        box.textContent = payload.error + ((payload.details || []).length ? ' (' + payload.details.join(', ') + ')' : '');
        box.hidden = false;
    }
    ['b-proc-new', 'b-proc-new-step'].forEach(function (id) {
        var dialog = document.getElementById(id);
        Array.prototype.forEach.call(dialog.querySelectorAll('[data-ed-close]'), function (b) {
            b.addEventListener('click', function () { dialog.close(); });
        });
        dialog.addEventListener('close', function () { if (lastOpener && lastOpener.focus) lastOpener.focus(); });
    });

    var processDialog = document.getElementById('b-proc-new');
    document.querySelector('[data-proc-new]').addEventListener('click', function () {
        var f = processDialog.querySelector('form');
        f.reset();
        var types = f.elements.instance_type;
        types.textContent = '';
        liveTypes().forEach(function (t) {
            var o = el('option', null, t.name === t.key ? t.key : t.name + ' (' + t.key + ')'); o.value = t.key; types.appendChild(o);
        });
        openDialog(processDialog);
        f.elements.key.focus();
    });
    processDialog.querySelector('form').addEventListener('submit', function (e) {
        e.preventDefault();
        var f = e.target;
        var key = f.elements.key.value.trim();
        edit({ op: 'process.create', key: key, name: f.elements.name.value, owner_role: f.elements.owner_role.value,
               instance_types: f.elements.instance_type.value ? [f.elements.instance_type.value] : [] },
             { quiet: true, process: key, select: { kind: 'process' } })
            .then(function (payload) {
                if (!payload.success) { dialogError(processDialog, payload); return; }
                processDialog.close();
                choose(key);
            });
    });

    var stepDialog = document.getElementById('b-proc-new-step');
    document.querySelector('[data-proc-new-step]').addEventListener('click', function () {
        var p = processByKey(current);
        if (!p) return;
        var f = stepDialog.querySelector('form');
        f.reset();
        stepDialog.querySelector('[data-ns-process]').textContent = p.name + ' (' + p.key + ')';
        var after = f.elements.after;
        after.textContent = '';
        var chosen = selection && selection.kind === 'step' ? selection.id : (p.steps.length ? p.steps[p.steps.length - 1].id : '');
        if (!p.steps.length) { var o0 = el('option', null, '– als erster Schritt –'); o0.value = ''; after.appendChild(o0); }
        p.steps.forEach(function (s) {
            var o = el('option', null, (s.name || s.id) + ' (' + s.id + ')'); o.value = s.id;
            if (s.id === chosen) o.selected = true;
            after.appendChild(o);
        });
        openDialog(stepDialog);
        f.elements.id.focus();
    });
    stepDialog.querySelector('form').addEventListener('submit', function (e) {
        e.preventDefault();
        var f = e.target;
        var id = f.elements.id.value.trim();
        edit({ op: 'step.create', key: current, id: id, name: f.elements.name.value, after: f.elements.after.value || null },
             { quiet: true, select: { kind: 'step', id: id } })
            .then(function (payload) {
                if (!payload.success) { dialogError(stepDialog, payload); return; }
                stepDialog.close();
            });
    });

    /* ---- wiring --------------------------------------------------------- */

    function load(next) {
        index(next);
        if (current && !processByKey(current)) current = null;
        if (!current && data.processes.length) current = data.processes[0].key;
        renderHead();
        renderList();
        draw(false);
        showSelection();
    }

    document.querySelector('[data-proc-undo]').addEventListener('click', function () { step('undo'); });
    document.querySelector('[data-proc-redo]').addEventListener('click', function () { step('redo'); });
    document.addEventListener('keydown', function (e) {
        var typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target || {}).tagName || '') || document.querySelector('dialog[open]');
        if (typing || !(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'z') return;
        e.preventDefault();
        step(e.shiftKey ? 'redo' : 'undo');
    });
    document.querySelector('[data-proc-connect]').addEventListener('click', function () { setConnectMode(!connectMode); });
    document.querySelector('[data-proc-details]').addEventListener('click', function () {
        if (!current) return;
        selection = { kind: 'process' };
        showSelection();
    });
    panel.querySelector('[data-panel-close]').addEventListener('click', function () { selection = null; markSelection(); });
    Array.prototype.forEach.call(document.querySelectorAll('[data-proc-zoom]'), function (b) {
        b.addEventListener('click', function () {
            var how = b.dataset.procZoom;
            if (how === 'fit') return fit(true);
            cy.zoom({ level: cy.zoom() * (how === 'in' ? 1.25 : 0.8), renderedPosition: { x: graphEl.clientWidth / 2, y: graphEl.clientHeight / 2 } });
        });
    });

    index(JSON.parse(dataEl.textContent));
    var wanted = decodeURIComponent((window.location.hash || '').slice(1));
    current = processByKey(wanted) ? wanted : (data.processes[0] ? data.processes[0].key : null);
    selection = null;   // the flow first, whole; the panel opens on a click
    var start = function () {
        renderList();
        draw(true);
        showSelection();
        document.documentElement.dataset.procReady = 'true';
        // For the browser tests: the canvas and the view as drawn.
        window.__builderProcesses = { cy: function () { return cy; }, data: function () { return data; },
                                      current: function () { return current; } };
    };
    // The brand font first, so the boxes are measured with it.
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(start); else start();
})();
