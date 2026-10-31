/* Brain Builder -- editing the ontology (build plan step B3).
 *
 * Registers window.BuilderOntologyEditor, which ontology.js calls: for the
 * panel of a type or relation the company owns, when a relation is drawn
 * from card to card, and when a type is dropped on another in the type tree.
 * Every edit is one POST of an operation (builder.editing) to data-edit-url;
 * the server checks it, writes it to the draft with who and before/after, and
 * answers with the whole view again, which ontology.js redraws. Undo and redo
 * are POSTs too: the server keeps the edits, the session the order.
 *
 * What this file checks itself is only what helps while typing (the parts of
 * rule 8 still missing). Everything else the server decides.
 */
(function () {
    'use strict';

    var root = document.querySelector('[data-onto-edit]');
    if (!root) return;

    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    var api = null;       // window.__builderOntology, once the canvas is drawn
    var ui = null;        // helpers from ontology.js: el, tag, section, LEVELS, isBelow
    var history = { undo: Number(root.dataset.undo || 0), redo: Number(root.dataset.redo || 0) };
    var pending = false;

    var DATATYPES = { text: 'Text', date: 'Datum', money: 'Betrag', enum: 'Auswahl', entity_ref: 'Verweis' };
    var RULE8 = [['label_de', 'Name (de)', true], ['label_en', 'Name (en)', true], ['description', 'Beschreibung', true],
                 ['example', 'Beispiel', true], ['not_to_confuse', 'Nicht verwechseln mit', false]];

    /* ---- the server --------------------------------------------------- */

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
        var details = (payload.details || []).join(', ');
        toast(payload.error || 'Das hat nicht geklappt.', details, 'critical');
    }

    function landed(payload, options) {
        history = payload.history || history;
        showHistory();
        if (!api) { window.location.reload(); return; }
        api.load(payload.ontology, { select: options && options.select });
        // The canvas and the panel show an edit; only undo and redo, which
        // change something out of sight, say what they did.
        if (options && options.announce) toast(options.announce, null, 'good');
    }

    /* One operation; resolves true when the server took it. */
    function edit(op, options) {
        if (pending) return Promise.resolve(false);
        pending = true;
        return post(root.dataset.editUrl, op).then(function (payload) {
            pending = false;
            if (!payload.success) { refused(payload); return false; }
            landed(payload, options);
            return true;
        });
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
        var undo = document.querySelector('[data-onto-undo]');
        var redo = document.querySelector('[data-onto-redo]');
        if (undo) { undo.disabled = !history.undo; undo.title = history.undo ? 'Rückgängig (Strg+Z)' : 'Nichts rückgängig zu machen'; }
        if (redo) { redo.disabled = !history.redo; redo.title = history.redo ? 'Wiederherstellen (Strg+Umschalt+Z)' : 'Nichts wiederherzustellen'; }
    }

    /* ---- small builders ----------------------------------------------- */

    function el(tagName, className, text) {
        var node = document.createElement(tagName);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    var uid = 0;
    function field(labelText, control, hint) {
        var box = el('div', 'b-field');
        control.id = control.id || 'b-ed-' + (++uid);
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

    function button(text, kind, onClick) {
        var b = el('button', 'b-button ' + (kind || 'b-button--secondary'), text);
        b.type = 'button';
        if (onClick) b.addEventListener('click', onClick);
        return b;
    }

    function iconButton(label, path, onClick) {
        var b = el('button', 'b-button b-button--quiet b-button--icon');
        b.type = 'button';
        b.setAttribute('aria-label', label);
        b.title = label;
        b.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + path + '</svg>';
        b.addEventListener('click', onClick);
        return b;
    }

    var UP = '<path d="m6 15 6-6 6 6"/>', DOWN = '<path d="m6 9 6 6 6-6"/>', REMOVE = '<path d="M5 7h14M10 11v6M14 11v6M7 7l1 12h8l1-12M9 7V4h6v3"/>';

    function select(options, value, attrs) {
        var s = el('select', 'b-select');
        options.forEach(function (o) {
            if (o.group) {
                var g = el('optgroup');
                g.label = o.group;
                o.options.forEach(function (x) { g.appendChild(option(x, value)); });
                s.appendChild(g);
            } else {
                s.appendChild(option(o, value));
            }
        });
        Object.keys(attrs || {}).forEach(function (k) { s.setAttribute(k, attrs[k]); });
        return s;
    }

    function option(o, value) {
        var opt = el('option', null, o.label);
        opt.value = o.value;
        if (o.value === value) opt.selected = true;
        return opt;
    }

    function form(onSubmit) {
        var f = el('form', 'b-ed-form');
        f.noValidate = true;
        f.addEventListener('submit', function (event) { event.preventDefault(); onSubmit(f); });
        return f;
    }

    function actions(f, label) {
        var row = el('div', 'b-button-row b-ed-actions');
        var save = el('button', 'b-button b-button--primary', label || 'Übernehmen');
        save.type = 'submit';
        row.appendChild(save);
        f.appendChild(row);
        return row;
    }

    /* ---- what a type can point to ------------------------------------- */

    /* The view; before the canvas exists (a workspace without types) only the catalog. */
    var bare = null;
    function data() {
        if (api) return api.data();
        if (!bare) {
            var raw = document.getElementById('b-onto-catalog');
            bare = { nodes: [], relations: [], catalog: raw ? JSON.parse(raw.textContent) : [] };
        }
        return bare;
    }

    /* The value a type is referred to by: a model type by its key, a class by its id. */
    function refOf(node) { return node.source === 'model' ? node.key : node.id; }

    function typeOptions(exclude) {
        var groups = { core: [], pack: [], company: [] };
        var seen = {};
        data().nodes.forEach(function (n) {
            if (exclude && exclude(n)) return;
            var ref = refOf(n);
            if (seen[ref]) return;
            seen[ref] = true;
            groups[n.level].push({ value: ref, label: n.name + (n.label.de && n.label.de !== n.name ? ' – ' + n.label.de : '') });
        });
        (data().catalog || []).forEach(function (c) {
            if (seen[c.id]) return;
            seen[c.id] = true;
            groups[c.level].push({ value: c.id, label: c.name + (c.label.de && c.label.de !== c.name ? ' – ' + c.label.de : '') + ' (' + c.id + ')' });
        });
        var out = [];
        [['core', 'Kern'], ['pack', 'Paket'], ['company', 'Firma']].forEach(function (pair) {
            var items = groups[pair[0]].sort(function (a, b) { return a.label.localeCompare(b.label, 'de'); });
            if (items.length) out.push({ group: pair[1], options: items });
        });
        return out;
    }

    function modelTypes() {
        return data().nodes.filter(function (n) { return n.source === 'model'; })
            .map(function (n) { return { value: n.key, label: n.key + (n.label.de && n.label.de !== n.key ? ' – ' + n.label.de : '') + (n.retired ? ' (stillgelegt)' : '') }; })
            .sort(function (a, b) { return a.label.localeCompare(b.label, 'de'); });
    }

    function multiSelect(values, extraAny) {
        var opts = modelTypes();
        if (extraAny) opts.unshift({ value: '*', label: '* – alle Typen' });
        var s = select(opts, null, { multiple: 'multiple', size: '7' });
        Array.prototype.forEach.call(s.options, function (o) { o.selected = values.indexOf(o.value) >= 0; });
        return s;
    }

    function chosen(s) {
        return Array.prototype.filter.call(s.options, function (o) { return o.selected; }).map(function (o) { return o.value; });
    }

    /* ---- rule 8 ------------------------------------------------------- */

    function sentences(text) {
        var parts = (text || '').trim().split(/[.!?](\s|$)/).filter(function (s) { return s && s.trim(); });
        return parts.length;
    }

    function checklist(values) {
        var list = el('ul', 'b-checklist');
        RULE8.forEach(function (part) {
            var done = !!(values[part[0]] || '').trim();
            var li = el('li', done ? 'is-done' : (part[2] ? 'is-missing' : 'is-optional'));
            li.appendChild(el('span', 'b-checklist-mark', done ? '✓' : (part[2] ? '!' : '–')));
            li.appendChild(document.createTextNode(part[1] + (done ? '' : part[2] ? ' fehlt' : ' fehlt (empfohlen)')));
            list.appendChild(li);
        });
        var count = sentences(values.description);
        if (count > 2) {
            var long = el('li', 'is-optional');
            long.appendChild(el('span', 'b-checklist-mark', '–'));
            long.appendChild(document.createTextNode('Beschreibung hat ' + count + ' Sätze; Regel 8 empfiehlt einen oder zwei.'));
            list.appendChild(long);
        }
        return list;
    }

    /* ---- the type panel ----------------------------------------------- */

    function typePanel(node, body) {
        var ctx = ui;

        // Names and key.
        var names = ctx.section('Benennung');
        var nf = form(function () {
            var tasks = [];
            var label = { de: deInput.value, en: enInput.value };
            if (label.de !== (node.label.de || '') || label.en !== (node.label.en || '')) {
                tasks.push({ op: 'type.update', key: node.key, set: { label: label } });
            }
            var newKey = keyInput.value.trim();
            var chain = Promise.resolve(true);
            tasks.forEach(function (op) { chain = chain.then(function (ok) { return ok && edit(op, { done: 'Gespeichert' }); }); });
            if (newKey && newKey !== node.key) {
                chain = chain.then(function (ok) {
                    return ok && edit({ op: 'type.rename', key: node.key, new_key: newKey }, { select: 'type:' + newKey, done: 'Umbenannt in ' + newKey });
                });
            }
        });
        var keyInput = input(node.key, { autocomplete: 'off', spellcheck: 'false' });
        keyInput.classList.add('b-mono');
        nf.appendChild(field('Schlüssel', keyInput, 'Großbuchstabe am Anfang, nur Buchstaben und Ziffern. Umbenennen ändert auch Elterntypen und Beziehungen, die ihn nennen.'));
        var deInput = input(node.label.de, { autocomplete: 'off' });
        var enInput = input(node.label.en, { autocomplete: 'off' });
        var pair = el('div', 'b-ed-pair');
        pair.appendChild(field('Name (de)', deInput));
        pair.appendChild(field('Name (en)', enInput));
        nf.appendChild(pair);
        actions(nf);
        names.appendChild(nf);
        body.appendChild(names);

        // Description, rule 8.
        var about = ctx.section('Beschreibung');
        var df = form(function () {
            edit({ op: 'type.update', key: node.key, set: {
                description: descInput.value, example: exampleInput.value, not_to_confuse: confuseInput.value,
            } }, { done: 'Beschreibung gespeichert' });
        });
        var descInput = textarea(node.description_text, 3);
        var exampleInput = textarea(node.example, 2);
        var confuseInput = textarea(node.not_to_confuse, 2);
        var checks = el('div', 'b-ed-checks');
        function liveChecks() {
            checks.textContent = '';
            checks.appendChild(checklist({
                label_de: deInput.value, label_en: enInput.value, description: descInput.value,
                example: exampleInput.value, not_to_confuse: confuseInput.value,
            }));
        }
        [deInput, enInput, descInput, exampleInput, confuseInput].forEach(function (i) { i.addEventListener('input', liveChecks); });
        liveChecks();
        df.appendChild(checks);
        df.appendChild(field('Beschreibung (en)', descInput, 'Ein oder zwei Sätze: was der Typ ist, in einfacher Sprache.'));
        df.appendChild(field('Beispiel', exampleInput));
        df.appendChild(field('Nicht verwechseln mit', confuseInput));
        actions(df);
        about.appendChild(df);
        body.appendChild(about);

        // Parent.
        var parentBox = ctx.section('Elterntyp');
        var current = node.parent ? refOf(api.data().nodes.find(function (n) { return n.id === node.parent; }) || { source: '', id: node.parent }) : '';
        var options = [{ value: '', label: '– kein Elterntyp –' }].concat(typeOptions(function (n) { return ctx.isBelow(n.id, node.id); }));
        var parentSelect = select(options, current || node.parent_ref || '');
        parentSelect.addEventListener('change', function () {
            edit({ op: 'type.update', key: node.key, set: { parent: parentSelect.value } }, { done: 'Elterntyp gesetzt' });
        });
        parentBox.appendChild(field('Untertyp von', parentSelect, 'Auch im Typbaum: Typ auf den neuen Elterntyp ziehen. Geerbte Attribute und Beziehungen werden angezeigt, nicht kopiert.'));
        body.appendChild(parentBox);

        // Attributes.
        var attrs = ctx.section('Attribute');
        attrs.appendChild(attributeEditor(node));
        body.appendChild(attrs);

        // Relations.
        var rels = ctx.section('Beziehungen');
        rels.appendChild(relationLinks(node));
        rels.appendChild(button('Beziehung hinzufügen', 'b-button--secondary', function () { openRelationDialog({ domain: [node.key], range: [] }); }));
        body.appendChild(rels);

        // Candidate for a pack.
        var packBox = ctx.section('Paket');
        var packOptions = [{ value: '', label: 'Kein Kandidat' }, { value: '*', label: 'Kandidat für ein Paket (offen)' }]
            .concat((data().term_packs || []).map(function (p) { return { value: p.id, label: 'Kandidat für ' + (p.name.de || p.id) }; }));
        var candidate = node.candidate === null || node.candidate === undefined ? '' : (node.candidate || '*');
        var candidateSelect = select(packOptions, candidate);
        candidateSelect.addEventListener('change', function () {
            edit({ op: 'type.update', key: node.key, set: { candidate: candidateSelect.value } });
        });
        packBox.appendChild(field('Gehört in ein Branchenpaket?', candidateSelect, 'Kandidaten erscheinen im Reiter «Kandidaten»; Knovas prüft sie für das Paket.'));
        body.appendChild(packBox);

        // Retire.
        var end = ctx.section(node.retired ? 'Stillgelegt' : 'Stilllegen');
        end.appendChild(el('p', 'b-small b-muted', node.retired
            ? 'Der Typ bleibt mit seiner Markierung im Modell, bis die Veröffentlichung entscheidet.'
            : 'Stilllegen löscht nichts: der Typ bleibt markiert im Modell, bis die Veröffentlichung entscheidet.'));
        end.appendChild(button(node.retired ? 'Wieder aktivieren' : 'Typ stilllegen', node.retired ? 'b-button--secondary' : 'b-button--danger', function () {
            edit({ op: 'type.retire', key: node.key, retired: !node.retired }, { done: node.retired ? node.key + ' ist wieder aktiv' : node.key + ' stillgelegt' });
        }));
        body.appendChild(end);
    }

    function attributeEditor(node) {
        var box = el('div', 'b-ed-attrs');
        var list = el('ol', 'b-ed-attr-list');
        // On a pack's type the pack's attributes are read-only; the company's own are edited here.
        var own = node.extendable ? node.attributes.filter(function (a) { return a.level === 'company'; }) : node.attributes;
        var count = own.length;
        own.forEach(function (attr, index) {
            list.appendChild(attributeRow(node, attr, index, count));
        });
        if (!count) list.appendChild(el('li', 'b-muted b-small', 'Noch keine eigenen Attribute.'));
        box.appendChild(list);

        // Add one.
        var add = form(function () {
            var name = nameInput.value.trim();
            if (!name) { nameInput.focus(); return; }
            edit({ op: 'attribute.add', key: node.key, attribute: { name: name, datatype: typeSelect.value } }, { done: 'Attribut ' + name + ' hinzugefügt' })
                .then(function (ok) {
                    if (!ok) return;
                    var again = document.querySelector('[data-ed-attr-new]');
                    if (again) again.focus();
                });
        });
        add.classList.add('b-ed-attr-add');
        var nameInput = input('', { autocomplete: 'off', spellcheck: 'false', placeholder: 'z. B. due_date', 'data-ed-attr-new': '' });
        nameInput.classList.add('b-mono');
        var typeSelect = select(Object.keys(DATATYPES).map(function (k) { return { value: k, label: DATATYPES[k] + ' (' + k + ')' }; }), 'text');
        add.appendChild(field('Neues Attribut', nameInput));
        add.appendChild(field('Datentyp', typeSelect));
        var addButton = el('button', 'b-button b-button--secondary', 'Hinzufügen');
        addButton.type = 'submit';
        add.appendChild(addButton);
        box.appendChild(add);

        if (node.extendable) return box;
        var inherited = node.inherited.filter(function (a) { return a.from !== ui.COMMON; });
        var common = node.inherited.length - inherited.length;
        if (inherited.length || common) {
            var inh = el('p', 'b-small b-muted b-ed-inherited');
            inh.textContent = 'Geerbt (angezeigt, nicht kopiert): ' +
                inherited.map(function (a) { return a.name + ' von ' + a.from; }).join(', ') +
                (common ? (inherited.length ? '; ' : '') + common + ' gemeinsame Attribute aller Typen' : '') + '.';
            box.appendChild(inh);
        }
        return box;
    }

    function attributeRow(node, attr, index, count) {
        var li = el('li', 'b-ed-attr');
        li.dataset.attr = attr.name;
        function update(set, done) {
            return edit({ op: 'attribute.update', key: node.key, name: attr.name, set: set }, { done: done || 'Attribut gespeichert' });
        }
        var head = el('div', 'b-ed-attr-head');
        var name = input(attr.name, { autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Name des Attributs' });
        name.classList.add('b-mono');
        name.addEventListener('change', function () { if (name.value.trim() && name.value.trim() !== attr.name) update({ name: name.value.trim() }, 'Attribut umbenannt'); });
        head.appendChild(name);
        var typeSelect = select(Object.keys(DATATYPES).map(function (k) { return { value: k, label: DATATYPES[k] }; }).concat(
            DATATYPES[attr.datatype] ? [] : [{ value: attr.datatype, label: attr.datatype || '–' }]), attr.datatype, { 'aria-label': 'Datentyp von ' + attr.name });
        typeSelect.addEventListener('change', function () { update({ datatype: typeSelect.value }); });
        head.appendChild(typeSelect);
        var req = el('label', 'b-check');
        var box = el('input');
        box.type = 'checkbox';
        box.checked = attr.required;
        box.addEventListener('change', function () { update({ required: box.checked }); });
        req.appendChild(box);
        req.appendChild(document.createTextNode('Pflicht'));
        head.appendChild(req);
        var tools = el('span', 'b-ed-attr-tools');
        if (!node.extendable) {
        var up = iconButton('Nach oben: ' + attr.name, UP, function () { edit({ op: 'attribute.move', key: node.key, name: attr.name, to: index - 1 }); });
        up.disabled = index === 0;
        var down = iconButton('Nach unten: ' + attr.name, DOWN, function () { edit({ op: 'attribute.move', key: node.key, name: attr.name, to: index + 1 }); });
        down.disabled = index === count - 1;
        tools.appendChild(up);
        tools.appendChild(down);
        }
        tools.appendChild(iconButton('Entfernen: ' + attr.name, REMOVE, function () {
            edit({ op: 'attribute.remove', key: node.key, name: attr.name }, { done: 'Attribut ' + attr.name + ' entfernt' });
        }));
        head.appendChild(tools);
        li.appendChild(head);

        var badges = el('div', 'b-tag-row');
        if (!attr.held) badges.appendChild(ui.tag('Brain: nur Beschriftung', 'warning')).title = 'Der Graph-Speicher hält diesen Datentyp heute nicht als Wert.';
        if (attr.datatype === 'enum' && !attr.enum_values.length) badges.appendChild(ui.tag('Keine Werte', 'critical'));
        if (attr.datatype === 'entity_ref' && !attr.target) badges.appendChild(ui.tag('Kein Zieltyp', 'critical'));
        if ((attr.rule8_missing || []).length) badges.appendChild(ui.tag('Regel 8: ' + attr.rule8_missing.join(', ') + ' fehlt', null));
        if (badges.childNodes.length) li.appendChild(badges);

        if (attr.datatype === 'enum') {
            var values = input(attr.enum_values.join(', '), { autocomplete: 'off', spellcheck: 'false' });
            values.addEventListener('change', function () {
                update({ enum_values: values.value.split(',').map(function (v) { return v.trim(); }).filter(Boolean) }, 'Werte gespeichert');
            });
            li.appendChild(field('Werte, durch Komma getrennt', values));
        }
        if (attr.datatype === 'entity_ref') {
            var target = select([{ value: '', label: '– Zieltyp wählen –' }].concat(typeOptions()), attr.target);
            target.addEventListener('change', function () { update({ target: target.value }, 'Zieltyp gesetzt'); });
            li.appendChild(field('Zieltyp', target));
        }
        var details = el('details', 'b-ed-attr-more');
        details.appendChild(el('summary', null, 'Namen und Beschreibung'));
        var df = form(function () {
            update({ label: { de: de.value, en: en.value }, description: desc.value }, 'Beschreibung gespeichert');
        });
        var de = input(attr.label.de), en = input(attr.label.en), desc = textarea(attr.description, 2);
        var pair = el('div', 'b-ed-pair');
        pair.appendChild(field('Name (de)', de));
        pair.appendChild(field('Name (en)', en));
        df.appendChild(pair);
        df.appendChild(field('Beschreibung (en)', desc));
        actions(df);
        details.appendChild(df);
        li.appendChild(details);
        return li;
    }

    function relationLinks(node) {
        var list = el('ul', 'b-rel-list');
        var rels = data().relations;
        function add(relationId, side) {
            var r = rels.find(function (x) { return x.id === relationId; });
            if (!r) return;
            var li = el('li');
            var b = el('button', null, r.key);
            b.type = 'button';
            b.addEventListener('click', function () { api.selectRelation(r.id); });
            li.appendChild(b);
            var other = side === 'out' ? r.range_names : r.domain_names;
            li.appendChild(document.createTextNode((side === 'out' ? ' → ' : ' ← ') + (other.join(', ') || '–')));
            if (r.retired) li.appendChild(document.createTextNode(' (stillgelegt)'));
            list.appendChild(li);
        }
        node.outgoing.forEach(function (id) { add(id, 'out'); });
        node.incoming.forEach(function (id) { if (node.outgoing.indexOf(id) < 0) add(id, 'in'); });
        if (!list.childNodes.length) list.appendChild(el('li', 'b-muted', 'Keine'));
        return list;
    }

    function readonlyNote(node, body) {
        if (node.extendable) {
            var ext = ui.section('Eigene Attribute der Firma');
            ext.appendChild(el('p', 'b-small b-muted', 'Der Typ gehört zum Paket ' + api.packName(node.pack) +
                ' und ist schreibgeschützt. Eigene Attribute, Untertypen und Beziehungen erweitern ihn; ein Paket-Update behält sie.'));
            ext.appendChild(attributeEditor(node));
            body.appendChild(ext);
        } else {
            body.appendChild(el('p', 'b-small b-muted', 'Kerntypen sind schreibgeschützt. Erweitern Sie sie mit einem eigenen Untertyp.'));
        }
        var row = el('div', 'b-button-row');
        row.appendChild(button('Untertyp anlegen', 'b-button--secondary', function () { openTypeDialog(refOf(node)); }));
        if (node.source === 'model') row.appendChild(button('Beziehung hinzufügen', 'b-button--secondary', function () { openRelationDialog({ domain: [node.key], range: [] }); }));
        body.appendChild(row);
    }

    /* ---- the relation panel ------------------------------------------- */

    function relationPanel(r, body) {
        var own = r.editable;
        var box = ui.section(own ? 'Beziehung' : 'Beziehung (Kern)');
        var f = form(function () {
            var set = { domain: chosen(domain), range: chosen(range) };
            if (own) {
                set.label = { de: de.value, en: en.value };
                set.family = family.value;
                set.inverse = inverse.value;
                set.sentence = sentence.value;
                set.reason = reason.value;
            }
            edit({ op: 'relation.update', key: r.key, set: set }, { done: 'Beziehung gespeichert' });
        });
        var de = input(r.label.de), en = input(r.label.en);
        var family = input(r.family, { list: 'b-ed-families', autocomplete: 'off' });
        var inverse = input(r.inverse || '', { autocomplete: 'off', spellcheck: 'false' });
        var sentence = textarea(r.sentence, 2), reason = textarea(r.reason, 2);
        if (own) {
            var pair = el('div', 'b-ed-pair');
            pair.appendChild(field('Name (de)', de));
            pair.appendChild(field('Name (en)', en));
            f.appendChild(pair);
            var pair2 = el('div', 'b-ed-pair');
            pair2.appendChild(field('Familie', family));
            pair2.appendChild(field('Umkehrung', inverse, 'Wie die Beziehung rückwärts liest, z. B. contains.'));
            f.appendChild(pair2);
            f.appendChild(field('Lesart', sentence, 'Ein Satz: «Rechnung billed_to Kunde — …»'));
            f.appendChild(field('Begründung', reason));
        } else {
            f.appendChild(el('p', 'b-small b-muted', 'Kernbeziehung: Name, Familie und Lesart sind fest. Von und Nach lassen sich um eigene Typen erweitern.'));
        }
        var domain = multiSelect(r.domain_names, true), range = multiSelect(r.range_names, true);
        var sides = el('div', 'b-ed-pair');
        sides.appendChild(field('Von (Definitionsbereich)', domain, 'Mehrfachauswahl mit Strg oder Umschalt.'));
        sides.appendChild(field('Nach (Wertebereich)', range));
        f.appendChild(sides);
        actions(f);
        box.appendChild(f);
        body.appendChild(box);
        if (own) {
            var end = ui.section(r.retired ? 'Stillgelegt' : 'Stilllegen');
            end.appendChild(button(r.retired ? 'Wieder aktivieren' : 'Beziehung stilllegen', r.retired ? 'b-button--secondary' : 'b-button--danger', function () {
                edit({ op: 'relation.retire', key: r.key, retired: !r.retired }, { done: r.retired ? r.key + ' ist wieder aktiv' : r.key + ' stillgelegt' });
            }));
            body.appendChild(end);
        }
    }

    /* ---- dialogs ------------------------------------------------------ */

    var typeDialog = document.getElementById('b-onto-new-type');
    var relationDialog = document.getElementById('b-onto-new-relation');
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

    [typeDialog, relationDialog].forEach(function (dialog) {
        if (!dialog) return;
        Array.prototype.forEach.call(dialog.querySelectorAll('[data-ed-close]'), function (b) {
            b.addEventListener('click', function () { dialog.close(); });
        });
        dialog.addEventListener('close', function () { if (lastOpener && lastOpener.focus) lastOpener.focus(); });
    });

    function fillParents(selectEl, value) {
        selectEl.textContent = '';
        var options = [{ value: '', label: '– kein Elterntyp –' }].concat(typeOptions());
        options.forEach(function (o) {
            if (o.group) {
                var g = el('optgroup'); g.label = o.group;
                o.options.forEach(function (x) { g.appendChild(option(x, value)); });
                selectEl.appendChild(g);
            } else selectEl.appendChild(option(o, value));
        });
    }

    function openTypeDialog(parent) {
        if (!typeDialog) return;
        var f = typeDialog.querySelector('form');
        f.reset();
        fillParents(f.elements.parent, parent || '');
        openDialog(typeDialog);
        f.elements.key.focus();
    }

    if (typeDialog) {
        typeDialog.querySelector('form').addEventListener('submit', function (event) {
            event.preventDefault();
            var f = event.target;
            var key = f.elements.key.value.trim();
            if (!key) { f.elements.key.focus(); return; }
            post(root.dataset.editUrl, {
                op: 'type.create', key: key, parent: f.elements.parent.value,
                label: { de: f.elements.label_de.value, en: f.elements.label_en.value },
            }).then(function (payload) {
                if (!payload.success) { dialogError(typeDialog, payload); return; }
                typeDialog.close();
                landed(payload, { select: 'type:' + key, done: 'Typ ' + key + ' angelegt' });
            });
        });
    }

    function openRelationDialog(prefill) {
        if (!relationDialog) return;
        var f = relationDialog.querySelector('form');
        f.reset();
        var lists = { domain: f.querySelector('[data-ed-side="domain"]'), range: f.querySelector('[data-ed-side="range"]') };
        ['domain', 'range'].forEach(function (side) {
            var fresh = multiSelect(prefill[side] || [], true);
            fresh.name = side;
            fresh.id = lists[side].querySelector('select').id;
            lists[side].querySelector('select').replaceWith(fresh);
        });
        relationMode(f);
        openDialog(relationDialog);
        f.elements.key.focus();
    }

    /* A name from the controlled list extends that relation; a new name makes one. */
    function relationMode(f) {
        var key = f.elements.key.value.trim();
        var existing = data().relations.find(function (r) { return r.key === key; });
        f.querySelector('[data-ed-new-only]').hidden = !!existing;
        var note = f.querySelector('[data-ed-existing]');
        note.hidden = !existing;
        if (existing) note.textContent = 'Die Beziehung ' + key + ' gibt es schon' + (existing.family ? ' (Familie ' + existing.family + ')' : '') +
            '. Die gewählten Typen werden ihrem Von und Nach hinzugefügt.';
        f.querySelector('[type="submit"]').textContent = existing ? 'Beziehung erweitern' : 'Beziehung anlegen';
        return existing;
    }

    if (relationDialog) {
        var rf = relationDialog.querySelector('form');
        rf.elements.key.addEventListener('input', function () { relationMode(rf); });
        rf.addEventListener('submit', function (event) {
            event.preventDefault();
            var key = rf.elements.key.value.trim();
            if (!key) { rf.elements.key.focus(); return; }
            var domain = chosen(rf.querySelector('[data-ed-side="domain"] select'));
            var range = chosen(rf.querySelector('[data-ed-side="range"] select'));
            var existing = relationMode(rf);
            var op = existing
                ? { op: 'relation.update', key: key, set: {
                    domain: existing.domain_names.concat(domain.filter(function (d) { return existing.domain_names.indexOf(d) < 0; })),
                    range: existing.range_names.concat(range.filter(function (d) { return existing.range_names.indexOf(d) < 0; })) } }
                : { op: 'relation.create', key: key, domain: domain, range: range, family: rf.elements.family.value,
                    inverse: rf.elements.inverse.value, reason: rf.elements.reason.value, sentence: rf.elements.sentence.value,
                    label: { de: rf.elements.label_de.value, en: rf.elements.label_en.value } };
            post(root.dataset.editUrl, op).then(function (payload) {
                if (!payload.success) { dialogError(relationDialog, payload); return; }
                relationDialog.close();
                landed(payload, { select: 'relation:' + key, done: existing ? 'Beziehung ' + key + ' erweitert' : 'Beziehung ' + key + ' angelegt' });
            });
        });
    }

    /* ---- hooks from ontology.js --------------------------------------- */

    function drawRelation(source, target) {
        if (!source || !target) return;
        if (source.source !== 'model' || target.source !== 'model') {
            toast('Beziehungen verbinden Typen des Modells.', 'Bezugsklassen ohne Typ im Modell lassen sich nicht verbinden.', 'warning');
            return;
        }
        openRelationDialog({ domain: [source.key], range: [target.key] });
    }

    function setParent(child, parent) {
        edit({ op: 'type.update', key: child.key, set: { parent: refOf(parent) } }, { done: child.key + ' ist jetzt Untertyp von ' + parent.name });
    }

    function start(ontologyApi, helpers) {
        api = ontologyApi;
        ui = helpers;
        showHistory();
    }

    /* ---- page controls ------------------------------------------------ */

    Array.prototype.forEach.call(document.querySelectorAll('[data-onto-new-type]'), function (b) {
        b.addEventListener('click', function () { openTypeDialog(''); });
    });
    var connectButton = document.querySelector('[data-onto-connect]');
    if (connectButton) connectButton.addEventListener('click', function () {
        if (api) api.setConnectMode(connectButton.getAttribute('aria-pressed') !== 'true');
    });
    var undoButton = document.querySelector('[data-onto-undo]');
    var redoButton = document.querySelector('[data-onto-redo]');
    if (undoButton) undoButton.addEventListener('click', function () { step('undo'); });
    if (redoButton) redoButton.addEventListener('click', function () { step('redo'); });
    document.addEventListener('keydown', function (event) {
        if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
        var target = event.target;
        if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
        if (document.querySelector('dialog[open]')) return;
        var key = event.key.toLowerCase();
        if (key === 'z' && !event.shiftKey) { event.preventDefault(); step('undo'); }
        else if (key === 'y' || (key === 'z' && event.shiftKey)) { event.preventDefault(); step('redo'); }
    });
    showHistory();

    window.BuilderOntologyEditor = {
        start: start, typePanel: typePanel, relationPanel: relationPanel, readonlyNote: readonlyNote,
        drawRelation: drawRelation, setParent: setParent, openTypeDialog: openTypeDialog,
    };
})();
