/* Brain Builder -- one mapping of a source to the ontology (build plan step B8).
 *
 * #b-map-data (builder_routes._mapping_view) is the mapping as brain-runner
 * runs it, with the targets of each emitted type: key, name, description,
 * every attribute of the type, the relations. The fields on the left come
 * from the preview route: the items the mapping's source selects, read
 * through the workspace source's component. A field dragged onto a target
 * (or chosen, then "Hier einsetzen") binds it; "Schritte" adds named
 * transformations; "Ausdrücke zeigen" shows and edits the expression itself.
 * Every change is one operation (builder.mapping_edits); the server answers
 * with the mapping again, and the preview runs anew.
 */
(function () {
    'use strict';

    var dataEl = document.getElementById('b-map-data');
    var root = document.querySelector('[data-map-root]');
    if (!dataEl || !root) return;
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    var data = JSON.parse(dataEl.textContent);
    var fields = [];          // the profile's fields: {name, type, examples, ...}
    var chosen = null;        // a field chosen with the keyboard or a click, waiting for its target
    var expert = false;
    var history = { undo: Number(root.dataset.undo || 0), redo: Number(root.dataset.redo || 0) };
    var pending = false;

    /* ---- small builders ------------------------------------------------- */

    function el(tagName, className, text) {
        var node = document.createElement(tagName);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }
    function tag(text, tone) { return el('span', 'b-tag' + (tone ? ' b-tag--' + tone : ''), text); }
    function button(text, kind, onClick) {
        var b = el('button', 'b-button ' + (kind || 'b-button--secondary'), text);
        b.type = 'button';
        if (onClick) b.addEventListener('click', onClick);
        return b;
    }
    function option(value, label, selected) {
        var o = el('option', null, label);
        o.value = value;
        if (selected) o.selected = true;
        return o;
    }
    function toast(title, text, tone) {
        if (window.BuilderUI && window.BuilderUI.toast) window.BuilderUI.toast({ title: title, text: text, tone: tone || 'good' });
    }
    function post(url, body) {
        return fetch(url, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'X-CSRF-Token': csrf, 'Accept': 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        }).then(function (r) {
            return r.json().catch(function () { return { success: false, error: 'Der Server hat nicht verständlich geantwortet.' }; });
        }, function () { return { success: false, error: 'Der Server ist nicht erreichbar.' }; });
    }

    /* ---- the server ----------------------------------------------------- */

    /* While an edit or a read runs, <html data-map-busy>: what the browser tests wait for. */
    var busy = 0;
    function working(delta) {
        busy += delta;
        if (busy) document.documentElement.dataset.mapBusy = 'true';
        else delete document.documentElement.dataset.mapBusy;
    }

    function edit(op) {
        if (pending) return Promise.resolve({ success: false });
        pending = true;
        working(1);
        op.id = data.id;
        return post(root.dataset.editUrl, op).then(function (payload) {
            pending = false;
            working(-1);
            if (!payload.success) {
                toast(payload.error || 'Das hat nicht geklappt.', (payload.details || []).join(', '), 'critical');
                return payload;
            }
            history = payload.history || history;
            data = payload.mapping;
            chosen = null;
            render();
            preview();
            return payload;
        });
    }

    function step(which) {
        if (pending || !history[which]) return;
        pending = true;
        post(root.dataset[which + 'Url']).then(function (payload) {
            pending = false;
            if (!payload.success) { toast(payload.error, null, 'critical'); return; }
            history = payload.history || history;
            data = payload.mapping;
            render();
            preview();
        });
    }

    /* ---- source side ---------------------------------------------------- */

    function renderSource() {
        var reads = document.querySelector('[data-map-reads]');
        reads.textContent = '';
        reads.appendChild(option('', '– Quelle wählen –', !data.source));
        data.sources.forEach(function (s) { reads.appendChild(option(s.key, s.name, data.source && data.source.key === s.key)); });
        var spec = document.querySelector('[data-map-spec]');
        spec.textContent = '';
        [['Leser', data.source_spec.extractor], ['Dateien', data.source_spec.glob || data.source_spec.path],
         ['Auswahl', data.source_spec.select], ['Eintrag heißt', data.source_spec.as]].forEach(function (r) {
            if (!r[1]) return;
            spec.appendChild(el('dt', null, r[0]));
            spec.appendChild(el('dd', 'b-mono b-small', r[1]));
        });
        renderFields();
    }

    function renderFields() {
        var list = document.querySelector('[data-map-fields]');
        list.textContent = '';
        if (!fields.length) {
            list.appendChild(el('li', 'b-muted b-small', data.source ? 'Felder werden gelesen …' : 'Wählen Sie die Quelle, über die gelesen wird.'));
            return;
        }
        fields.forEach(function (f) {
            var li = el('li');
            var chip = el('button', 'b-map-field' + (chosen === f.name ? ' is-chosen' : ''));
            chip.type = 'button';
            chip.draggable = true;
            chip.setAttribute('aria-pressed', String(chosen === f.name));
            chip.appendChild(el('span', 'b-mono', f.name));
            chip.appendChild(el('span', 'b-small b-muted', f.type + (f.examples.length ? ' · ' + f.examples[0] : '')));
            chip.addEventListener('dragstart', function (e) { e.dataTransfer.setData('text/plain', f.name); e.dataTransfer.effectAllowed = 'copy'; });
            chip.addEventListener('click', function () { chosen = chosen === f.name ? null : f.name; renderFields(); renderEmits(); });
            li.appendChild(chip);
            list.appendChild(li);
        });
    }

    /* ---- target side ---------------------------------------------------- */

    function stepText(s) {
        switch (s.step) {
            case 'trim': return 'Leerraum entfernen';
            case 'left': return 'Anfang (' + s.n + ')';
            case 'split': return 'Teilen an «' + s.separator + '», Teil ' + s.part;
            case 'replace': return 'Ersetzen «' + s.old + '» → «' + s['new'] + '»';
            case 'map': return 'Werte umsetzen (' + Object.keys(s.values || {}).length + ')';
            case 'date': return s.format === 'year' ? 'Jahr' : 'Datum';
            case 'join': return 'Verbinden mit ' + (s.fields || []).join(', ');
        }
        return s.step;
    }

    function problemsFor(emitIndex, target) {
        return data.problems.filter(function (p) { return p.target === emitIndex + ':' + target; });
    }

    function dropZone(cell, emitIndex, t) {
        cell.addEventListener('dragover', function (e) { e.preventDefault(); cell.classList.add('is-over'); });
        cell.addEventListener('dragleave', function () { cell.classList.remove('is-over'); });
        cell.addEventListener('drop', function (e) {
            e.preventDefault();
            cell.classList.remove('is-over');
            var name = e.dataTransfer.getData('text/plain');
            if (name) edit({ op: 'mapping.bind', emit: emitIndex, target: t.target, field: name, steps: t.field === name ? t.steps : [] });
        });
    }

    function bindingCell(emitIndex, t) {
        var cell = el('div', 'b-map-binding');
        cell.setAttribute('data-target', emitIndex + ':' + t.target);
        dropZone(cell, emitIndex, t);
        if (t.expression) {
            var chip = el('span', 'b-map-bound');
            chip.appendChild(el('span', 'b-mono', t.custom ? 'Ausdruck' : (t.field || 'Ausdruck')));
            t.steps.forEach(function (s) { chip.appendChild(tag(stepText(s))); });
            cell.appendChild(chip);
        } else {
            cell.appendChild(el('span', 'b-map-empty', t.kind === 'key' || t.kind === 'name' ? 'Pflicht: Feld hierher ziehen' : 'Feld hierher ziehen'));
        }
        var tools = el('span', 'b-map-tools');
        if (chosen) tools.appendChild(button('Hier einsetzen', 'b-button--quiet b-button--small', function () {
            edit({ op: 'mapping.bind', emit: emitIndex, target: t.target, field: chosen, steps: [] });
        }));
        if (t.expression && t.field && !t.custom) tools.appendChild(button('Schritte', 'b-button--quiet b-button--small', function () {
            var row = cell.closest('.b-map-row');
            var open = row.querySelector('.b-map-steps');
            if (open) { open.remove(); return; }
            row.appendChild(stepsEditor(emitIndex, t));
        }));
        if (t.expression) {
            var x = button('×', 'b-button--quiet b-button--icon b-button--small', function () {
                edit({ op: 'mapping.unbind', emit: emitIndex, target: t.target });
            });
            x.setAttribute('aria-label', 'Zuordnung von ' + t.label + ' entfernen');
            tools.appendChild(x);
        }
        cell.appendChild(tools);
        if (expert) {
            var input = el('input', 'b-input b-mono b-map-expr');
            input.value = t.expression;
            input.setAttribute('aria-label', 'Ausdruck für ' + t.label);
            input.addEventListener('change', function () {
                edit({ op: 'mapping.expression', emit: emitIndex, target: t.target, expression: input.value });
            });
            cell.appendChild(input);
        } else if (t.expression) {
            cell.appendChild(el('code', 'b-map-expr-text', t.expression));
        }
        problemsFor(emitIndex, t.target).forEach(function (p) { cell.appendChild(el('p', 'b-map-problem', p.message)); });
        return cell;
    }

    function stepsEditor(emitIndex, t) {
        var box = el('div', 'b-map-steps');
        var steps = t.steps.map(function (s) { return Object.assign({}, s); });
        var list = el('ol', 'b-map-step-list');
        function draw() {
            list.textContent = '';
            steps.forEach(function (s, i) {
                var li = el('li', null, stepText(s) + ' ');
                var x = button('×', 'b-button--quiet b-button--icon b-button--small', function () { steps.splice(i, 1); draw(); });
                x.setAttribute('aria-label', 'Schritt entfernen');
                li.appendChild(x);
                list.appendChild(li);
            });
            if (!steps.length) list.appendChild(el('li', 'b-muted', 'Noch kein Schritt: der Wert wird übernommen, wie er ist.'));
        }
        draw();
        box.appendChild(list);
        var kind = el('select', 'b-select');
        kind.setAttribute('aria-label', 'Schritt');
        Object.keys(data.steps).forEach(function (k) { kind.appendChild(option(k, data.steps[k])); });
        var params = el('div', 'b-map-step-params');
        function paramInputs() {
            params.textContent = '';
            function field(name, label, value) {
                var i = el('input', 'b-input');
                i.name = name; i.value = value || ''; i.setAttribute('aria-label', label); i.placeholder = label;
                params.appendChild(i);
            }
            if (kind.value === 'split') { field('separator', 'Trennzeichen', '/'); field('part', 'Teil (0 = erster)', '0'); }
            if (kind.value === 'left') field('n', 'Zeichen', '10');
            if (kind.value === 'replace') { field('old', 'Alt'); field('new', 'Neu'); }
            if (kind.value === 'map') {
                var ta = el('textarea', 'b-textarea');
                ta.name = 'values'; ta.rows = 3; ta.placeholder = 'alt=neu, eine Zeile je Wert';
                ta.setAttribute('aria-label', 'Werte umsetzen');
                params.appendChild(ta);
            }
            if (kind.value === 'date') {
                var f = el('select', 'b-select'); f.name = 'format'; f.setAttribute('aria-label', 'Format');
                f.appendChild(option('date', 'Datum (JJJJ-MM-TT)')); f.appendChild(option('year', 'Jahr'));
                params.appendChild(f);
            }
            if (kind.value === 'join') {
                var others = el('select', 'b-select'); others.name = 'fields'; others.multiple = true; others.size = 4;
                others.setAttribute('aria-label', 'Weitere Felder');
                fields.forEach(function (fl) { others.appendChild(option(fl.name, fl.name)); });
                params.appendChild(others);
                field('separator', 'Trennzeichen', ' — ');
            }
        }
        kind.addEventListener('change', paramInputs);
        paramInputs();
        var addRow = el('div', 'b-proc-inline');
        addRow.appendChild(kind);
        addRow.appendChild(button('Schritt hinzufügen', 'b-button--quiet', function () {
            var s = { step: kind.value };
            Array.prototype.forEach.call(params.querySelectorAll('input, select, textarea'), function (i) {
                if (i.name === 'values') {
                    s.values = {};
                    i.value.split('\n').forEach(function (line) {
                        var at = line.indexOf('=');
                        if (at > 0) s.values[line.slice(0, at).trim()] = line.slice(at + 1).trim();
                    });
                } else if (i.name === 'fields') {
                    s.fields = Array.prototype.filter.call(i.options, function (o) { return o.selected; }).map(function (o) { return o.value; });
                } else if (i.name === 'part' || i.name === 'n') {
                    s[i.name] = Number(i.value) || 0;
                } else {
                    s[i.name] = i.value;
                }
            });
            steps.push(s);
            draw();
        }));
        box.appendChild(addRow);
        box.appendChild(params);
        var actions = el('div', 'b-button-row b-ed-actions');
        actions.appendChild(button('Schritte übernehmen', 'b-button--primary', function () {
            edit({ op: 'mapping.bind', emit: emitIndex, target: t.target, field: t.field, steps: steps });
        }));
        box.appendChild(actions);
        return box;
    }

    function renderEmits() {
        var box = document.querySelector('[data-map-emits]');
        box.textContent = '';
        data.emits.forEach(function (em) {
            var card = el('section', 'b-card b-map-emit');
            var head = el('div', 'b-map-emit-head');
            head.appendChild(el('h3', 'b-h3', em.type));
            var identify = el('label', 'b-small b-muted b-map-identify', 'Erkannt an ');
            var idSelect = el('select', 'b-select');
            idSelect.setAttribute('aria-label', 'Erkannt an');
            (em.attributes.length ? em.attributes : em.identify).forEach(function (a) { idSelect.appendChild(option(a, a, em.identify[0] === a)); });
            idSelect.addEventListener('change', function () { edit({ op: 'mapping.identify', emit: em.index, attributes: [idSelect.value] }); });
            identify.appendChild(idSelect);
            head.appendChild(identify);
            if (data.emits.length > 1) head.appendChild(button('Zieltyp entfernen', 'b-button--quiet b-button--small', function () {
                edit({ op: 'mapping.emit.remove', emit: em.index });
            }));
            card.appendChild(head);
            problemsFor(em.index, 'type').concat(problemsFor(em.index, 'identify')).forEach(function (p) { card.appendChild(el('p', 'b-map-problem', p.message)); });

            var rows = el('div', 'b-map-rows');
            em.targets.forEach(function (t) {
                var row = el('div', 'b-map-row' + (t.expression ? ' is-bound' : ''));
                var label = el('div', 'b-map-label');
                label.appendChild(el('span', t.kind === 'fact' ? 'b-mono' : null, t.label));
                if (t.kind === 'key') label.appendChild(el('span', 'b-small b-muted', 'erkennt den Knoten'));
                if (t.kind === 'fact' && t.datatype) label.appendChild(el('span', 'b-small b-muted', t.datatype + (t.priority != null && t.expression ? ' · Priorität ' + t.priority : '')));
                if (t.unknown) label.appendChild(tag('kein Attribut', 'critical'));
                row.appendChild(label);
                row.appendChild(bindingCell(em.index, t));
                rows.appendChild(row);
            });
            card.appendChild(rows);

            var rel = el('div', 'b-map-relations');
            rel.appendChild(el('h4', 'b-map-subhead', 'Beziehungen'));
            em.relations.forEach(function (r, j) {
                var row = el('div', 'b-map-row is-bound');
                var label = el('div', 'b-map-label');
                label.appendChild(el('span', 'b-mono', (r.direction === 'in' ? '← ' : '') + r.relation + ' → ' + r.to_type));
                label.appendChild(el('span', 'b-small b-muted', r.to_match ? 'gefunden über Fakten' : 'Schlüssel des anderen Knotens'));
                row.appendChild(label);
                var cell = r.to_match ? el('div', 'b-map-binding', 'über Fakten (nur im Ausdruck)') : bindingCell(em.index, r);
                var x = button('Beziehung entfernen', 'b-button--quiet b-button--small', function () {
                    edit({ op: 'mapping.relation', emit: em.index, index: j, remove: true });
                });
                cell.appendChild(x);
                row.appendChild(cell);
                rel.appendChild(row);
            });
            var add = el('div', 'b-proc-inline b-map-rel-add');
            var which = el('select', 'b-select');
            which.setAttribute('aria-label', 'Beziehung für ' + em.type);
            which.appendChild(option('', '– Beziehung wählen –'));
            em.offered.forEach(function (o, n) {
                which.appendChild(option(String(n), (o.direction === 'in' ? '← ' : '') + o.relation + ' → ' + o.to_type));
            });
            var from = el('select', 'b-select');
            from.setAttribute('aria-label', 'Feld mit dem Schlüssel des anderen Knotens');
            from.appendChild(option('', '– Feld mit dem Schlüssel –'));
            fields.forEach(function (f) { from.appendChild(option(f.name, f.name)); });
            add.appendChild(which);
            add.appendChild(from);
            add.appendChild(button('Beziehung hinzufügen', 'b-button--secondary', function () {
                if (!which.value || !from.value) { toast('Beziehung und Feld wählen.', null, 'critical'); return; }
                var o = em.offered[Number(which.value)];
                edit({ op: 'mapping.relation', emit: em.index, relation: o.relation, to_type: o.to_type, direction: o.direction,
                       field: from.value, steps: [] });
            }));
            rel.appendChild(add);
            card.appendChild(rel);
            box.appendChild(card);
        });
    }

    /* ---- settings ------------------------------------------------------- */

    function renderSettings() {
        var form = document.querySelector('[data-map-settings]');
        form.textContent = '';
        function field(label, control) {
            var f = el('div', 'b-field');
            control.id = control.id || 'b-map-set-' + label.replace(/\W+/g, '-').toLowerCase();
            var l = el('label', 'b-label', label); l.htmlFor = control.id;
            f.appendChild(l); f.appendChild(control);
            form.appendChild(f);
            return control;
        }
        var priority = field('Priorität', el('input', 'b-input'));
        priority.type = 'number'; priority.min = 0; priority.max = 100; priority.value = data.priority;
        var policy = field('Bearbeitung', el('select', 'b-select'));
        Object.keys(data.policies).forEach(function (k) { policy.appendChild(option(k, data.policies[k], data.edit_policy === k)); });
        var share = field('Verschwindet ein Eintrag: stilllegen, höchstens', el('select', 'b-select'));
        [0.05, 0.1, 0.2, 0.3, 0.5, 1].forEach(function (v) {
            share.appendChild(option(String(v), Math.round(v * 100) + ' % je Lauf', Number(data.deletion_guard.max_retire_share || 0.2) === v));
        });
        var least = field('Löschschutz ab', el('input', 'b-input'));
        least.type = 'number'; least.min = 1; least.value = data.deletion_guard.min_retire || 3;
        var fresh = field('Frisch für', el('input', 'b-input'));
        fresh.value = data.freshness || '7 d';
        var row = el('div', 'b-button-row');
        row.appendChild(button('Einstellungen speichern', 'b-button--secondary', function () {
            edit({ op: 'mapping.settings', fields: { priority: Number(priority.value), edit_policy: policy.value,
                   max_retire_share: Number(share.value), min_retire: Number(least.value), freshness: fresh.value } });
        }));
        form.appendChild(row);
        form.appendChild(el('p', 'b-small b-muted',
            'Verschwindet ein Eintrag aus der Quelle, legt der Lauf seinen Knoten still. Würde ein Lauf mehr stilllegen als erlaubt, hält der Löschschutz ihn an, bis jemand zustimmt.'));
    }

    /* ---- preview and dry run -------------------------------------------- */

    function renderProblems() {
        var box = document.querySelector('[data-map-problems]');
        var general = data.problems.filter(function (p) { return p.target === 'mapping'; });
        box.textContent = '';
        box.hidden = !data.problems.length;
        if (!data.problems.length) return;
        box.appendChild(el('p', 'b-callout-title', data.problems.length === 1 ? '1 Problem' : data.problems.length + ' Probleme'));
        if (general.length) {
            var ul = el('ul');
            general.forEach(function (p) { ul.appendChild(el('li', null, p.message)); });
            box.appendChild(ul);
        } else {
            box.appendChild(el('p', null, 'Die Probleme stehen bei den Zielen.'));
        }
    }

    function table(headers, rows) {
        var wrap = el('div', 'b-table-wrap');
        var t = el('table', 'b-table b-table--compact');
        var head = el('tr');
        headers.forEach(function (h) { var th = el('th', null, h); th.scope = 'col'; head.appendChild(th); });
        var thead = el('thead'); thead.appendChild(head); t.appendChild(thead);
        var body = el('tbody');
        rows.forEach(function (r) {
            var tr = el('tr');
            r.forEach(function (c) { var td = el('td'); if (c instanceof Node) td.appendChild(c); else td.textContent = c; tr.appendChild(td); });
            body.appendChild(tr);
        });
        t.appendChild(body);
        wrap.appendChild(t);
        return wrap;
    }

    function preview() {
        var body = document.querySelector('[data-map-preview-body]');
        if (!data.source) { fields = []; renderFields(); return; }
        body.textContent = '';
        body.appendChild(el('p', 'b-muted', 'Die Quelle wird gelesen …'));
        working(1);
        post(root.dataset.previewUrl).then(function (payload) {
            working(-1);
            body.textContent = '';
            if (!payload.success) {
                var err = el('div', 'b-callout b-callout--critical');
                err.appendChild(el('p', null, payload.error));
                body.appendChild(err);
                return;
            }
            fields = payload.fields.fields;
            renderFields();
            renderEmits();
            var p = payload.preview;
            if (!p) {
                body.appendChild(el('p', 'b-map-problem', 'Erst wenn die Probleme behoben sind, zeigt die Vorschau Knoten.'));
                return;
            }
            body.appendChild(el('p', 'b-small b-muted', p.items + ' Einträge gelesen · ' + p.candidates + ' Knoten · die ersten ' + p.nodes.length + ' gezeigt'));
            body.appendChild(table(['Typ', 'Schlüssel', 'Name', 'Fakten'], p.nodes.map(function (n) {
                var facts = el('span', 'b-small');
                facts.textContent = Object.keys(n.facts).map(function (k) { return k + ' = ' + n.facts[k]; }).join(' · ');
                return [n.type, n.key, n.name, facts];
            })));
            if (p.edges.length) {
                body.appendChild(el('h3', 'b-h3', 'Kanten'));
                body.appendChild(table(['Von', 'Beziehung', 'Nach'], p.edges.map(function (e) { return [e.from, e.relation, e.to]; })));
            }
            p.skipped.forEach(function (s) { body.appendChild(el('p', 'b-map-problem', s.count + ' Einträge übersprungen: ' + s.reason)); });
            if (p.unmatched) body.appendChild(el('p', 'b-map-problem', p.unmatched + ' Knoten ohne eine verlangte Beziehung.'));
        });
    }

    function dryRun() {
        var body = document.querySelector('[data-map-dry-body]');
        body.textContent = '';
        body.appendChild(el('p', 'b-muted', 'Der Plan wird gegen den Mandanten gerechnet …'));
        post(root.dataset.dryRunUrl).then(function (payload) {
            body.textContent = '';
            if (!payload.success) {
                var err = el('div', 'b-callout b-callout--critical');
                err.appendChild(el('p', null, payload.error));
                (payload.details || []).forEach(function (d) { err.appendChild(el('p', 'b-small b-mono', d)); });
                if (payload.url) {
                    var there = el('a', 'b-button b-button--secondary', 'Zur Komponente');
                    there.href = payload.url;
                    err.appendChild(there);
                }
                body.appendChild(err);
                return;
            }
            var s = payload.summary;
            var stats = el('div', 'b-src-stats');
            [['Hinzugefügt', s.added], ['Geändert', s.modified], ['Stillgelegt', s.retired], ['Unverändert', s.unchanged], ['Abweichungen', s.drift]]
                .forEach(function (pair) {
                    var stat = el('div', 'b-src-stat');
                    stat.appendChild(el('span', 'b-src-stat-value', String(pair[1])));
                    stat.appendChild(el('span', 'b-src-stat-label', pair[0]));
                    stats.appendChild(stat);
                });
            body.appendChild(stats);
            var guard = el('p', 'b-map-guard');
            guard.appendChild(s.guard.tripped ? tag('Löschschutz hält an', 'critical') : tag('Löschschutz: frei', 'good'));
            guard.appendChild(document.createTextNode(' höchstens ' + Math.round((s.guard.max_share || 0) * 100) + ' % stilllegen, ab ' + s.guard.min_retire + ' Einträgen.'));
            body.appendChild(guard);
            body.appendChild(el('p', 'b-small b-muted', s.items + ' Einträge gelesen, ' + s.candidates + ' Knoten geplant. Nichts wurde geschrieben; veröffentlicht wird über «Prüfen & Veröffentlichen».'));
            document.documentElement.dataset.mapDryRun = 'done';
        });
    }

    /* ---- wiring --------------------------------------------------------- */

    function render() {
        document.querySelector('[data-map-description]').textContent = data.description;
        renderProblems();
        renderSource();
        renderEmits();
        renderSettings();
        document.querySelector('[data-map-yaml]').textContent = data.yaml;
        var undo = document.querySelector('[data-map-undo]'), redo = document.querySelector('[data-map-redo]');
        undo.disabled = !history.undo; redo.disabled = !history.redo;
        var addType = document.querySelector('[data-map-add-type]');
        addType.textContent = '';
        data.types.forEach(function (t) { addType.appendChild(option(t.key, t.name === t.key ? t.key : t.name + ' (' + t.key + ')')); });
    }

    document.querySelector('[data-map-reads]').addEventListener('change', function (e) {
        edit({ op: 'mapping.source', source: e.target.value || null });
    });
    document.querySelector('[data-map-expert]').addEventListener('change', function (e) { expert = e.target.checked; renderEmits(); });
    document.querySelector('[data-map-add-type-button]').addEventListener('click', function () {
        edit({ op: 'mapping.emit.add', type: document.querySelector('[data-map-add-type]').value });
    });
    document.querySelector('[data-map-preview]').addEventListener('click', preview);
    document.querySelector('[data-map-dry-run]').addEventListener('click', dryRun);
    document.querySelector('[data-map-undo]').addEventListener('click', function () { step('undo'); });
    document.querySelector('[data-map-redo]').addEventListener('click', function () { step('redo'); });

    render();
    preview();
    document.documentElement.dataset.mapReady = 'true';
    window.__builderMapping = { data: function () { return data; }, fields: function () { return fields; } };
})();
