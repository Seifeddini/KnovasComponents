/* Brain Builder -- how the company sees its Brain (build plan step B9).
 *
 * #b-views-data (builder.views.build) holds the spines, pages, profiles, the
 * role mapping, their problems and the ontology they may name. Four tabs:
 * profiles with their ordered views and pages, pages with their ordered slots
 * (sections), spines as trees of levels, and the roles that see each profile.
 * Every change is one operation (builder.view_edits) on the edit route; the
 * server answers with the whole view again. The preview on the right draws a
 * profile's start view and one page from sample data the server makes from
 * the workspace (builder.views.preview).
 */
(function () {
    'use strict';

    var dataEl = document.getElementById('b-views-data');
    var root = document.querySelector('[data-views-root]');
    if (!dataEl || !root) return;
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    var data = JSON.parse(dataEl.textContent);
    var history = { undo: Number(root.dataset.undo || 0), redo: Number(root.dataset.redo || 0) };
    var state = { tab: 'profiles', profile: null, page: null, spine: null, level: null, branch: null,
                  previewView: null, previewPage: null };
    var pending = false;

    var listEl = document.querySelector('[data-views-list]');
    var listEmpty = document.querySelector('[data-views-list-empty]');
    var checkEl = document.querySelector('[data-views-check]');
    var editor = document.querySelector('[data-views-editor]');
    var previewPane = document.querySelector('[data-views-preview-pane]');
    var previewEl = document.querySelector('[data-views-preview]');
    var layout = document.querySelector('[data-views-layout]');
    var newButton = document.querySelector('[data-views-new]');
    var newLabel = document.querySelector('[data-views-new-label]');

    /* ---- small builders ------------------------------------------------- */

    function el(tagName, className, text) {
        var node = document.createElement(tagName);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }
    function tag(text, tone) { return el('span', 'b-tag' + (tone ? ' b-tag--' + tone : ''), text); }
    function button(text, kind, onClick, label) {
        var b = el('button', 'b-button ' + (kind || 'b-button--secondary'), text);
        b.type = 'button';
        if (label) b.setAttribute('aria-label', label);
        if (onClick) b.addEventListener('click', onClick);
        return b;
    }
    function option(value, label, selected) {
        var o = el('option', null, label);
        o.value = value;
        if (selected) o.selected = true;
        return o;
    }
    var uid = 0;
    function field(labelText, control, hint) {
        var wrap = el('div', 'b-field');
        var id = control.id || ('b-vw-f' + (++uid));
        control.id = id;
        var label = el('label', 'b-label', labelText);
        label.htmlFor = id;
        wrap.appendChild(label);
        wrap.appendChild(control);
        if (hint) wrap.appendChild(el('span', 'b-hint', hint));
        return wrap;
    }
    function input(name, value, cls) {
        var i = el('input', 'b-input' + (cls ? ' ' + cls : ''));
        i.name = name;
        i.value = value || '';
        i.autocomplete = 'off';
        return i;
    }
    function textarea(name, value) {
        var t = el('textarea', 'b-textarea');
        t.name = name;
        t.rows = 2;
        t.value = value || '';
        return t;
    }
    function select(name, options, value) {
        var s = el('select', 'b-select');
        s.name = name;
        options.forEach(function (o) { s.appendChild(option(o[0], o[1], String(o[0]) === String(value || ''))); });
        return s;
    }
    function checkbox(labelText, checked, name) {
        var wrap = el('label', 'b-check');
        var c = el('input');
        c.type = 'checkbox';
        c.checked = !!checked;
        if (name) c.name = name;
        wrap.appendChild(c);
        wrap.appendChild(el('span', null, labelText));
        return { wrap: wrap, input: c };
    }
    function list(text) {
        return String(text || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
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
    function typeName(key) {
        var t = data.types.filter(function (x) { return x.key === key; })[0];
        return t ? t.name : key;
    }
    function attributesOf(types) {
        var out = [];
        data.types.forEach(function (t) {
            if (types.indexOf(t.key) >= 0) t.attributes.forEach(function (a) { if (out.indexOf(a.name) < 0) out.push(a.name); });
        });
        return out;
    }

    /* ---- the server ----------------------------------------------------- */

    /* While an edit or a preview runs, <html data-views-busy>: what the browser tests wait for. */
    var busy = 0;
    function working(delta) {
        busy += delta;
        if (busy) document.documentElement.dataset.viewsBusy = 'true';
        else delete document.documentElement.dataset.viewsBusy;
    }

    function edit(op) {
        if (pending) return Promise.resolve({ success: false, error: 'Einen Moment, die letzte Änderung läuft noch.' });
        pending = true;
        working(1);
        return post(root.dataset.editUrl, op).then(function (payload) {
            pending = false;
            working(-1);
            if (!payload.success) return payload;
            history = payload.history || history;
            data = payload.views;
            render();
            return payload;
        });
    }
    function editOrToast(op) {
        return edit(op).then(function (payload) {
            if (!payload.success && payload.error) toast(payload.error, (payload.details || []).join(', '), 'critical');
            return payload;
        });
    }
    function step(url) {
        if (pending) return;
        pending = true;
        working(1);
        post(url).then(function (payload) {
            pending = false;
            working(-1);
            if (!payload.success) { toast(payload.error || 'Das hat nicht geklappt.', '', 'critical'); return; }
            history = payload.history || history;
            data = payload.views;
            render();
        });
    }
    document.querySelector('[data-views-undo]').addEventListener('click', function () { step(root.dataset.undoUrl); });
    document.querySelector('[data-views-redo]').addEventListener('click', function () { step(root.dataset.redoUrl); });

    /* ---- the dialog ----------------------------------------------------- */

    var dialog = document.getElementById('b-vw-dialog');
    var dialogForm = dialog.querySelector('form');
    var dialogBody = dialog.querySelector('[data-vw-dialog-body]');
    var dialogError = dialog.querySelector('[data-vw-dialog-error]');
    var dialogSubmit = null;
    Array.prototype.forEach.call(dialog.querySelectorAll('[data-vw-dialog-close]'), function (b) {
        b.addEventListener('click', function () { dialog.close(); });
    });
    /* open a dialog: build(body) fills it and returns a function that gives the operation (or throws a message) */
    function openDialog(title, text, submitText, build) {
        dialog.querySelector('[data-vw-dialog-title]').textContent = title;
        dialog.querySelector('[data-vw-dialog-text]').textContent = text || '';
        dialog.querySelector('[data-vw-dialog-submit]').textContent = submitText || 'Übernehmen';
        dialogBody.textContent = '';
        dialogError.hidden = true;
        dialogSubmit = build(dialogBody);
        dialog.showModal();
        var first = dialogBody.querySelector('input, select, textarea');
        if (first) first.focus();
    }
    dialogForm.addEventListener('submit', function (event) {
        event.preventDefault();
        var op;
        try { op = dialogSubmit(); } catch (message) { dialogError.textContent = String(message); dialogError.hidden = false; return; }
        edit(op).then(function (payload) {
            if (!payload.success) {
                dialogError.textContent = (payload.error || 'Das hat nicht geklappt.') + (payload.details && payload.details.length ? ' ' + payload.details.join(', ') : '');
                dialogError.hidden = false;
                return;
            }
            dialog.close();
            if (op.after) op.after();
        });
    });

    /* ---- the path picker -------------------------------------------------- */

    /* Offers, from the types a path stands at, only the relations whose Von (out) or Nach (in) fits. */
    function hopOptions(fromTypes) {
        var out = [];
        data.relations.forEach(function (r) {
            var fromOut = r.domain.indexOf('*') >= 0 || fromTypes.some(function (t) { return r.domain.indexOf(t) >= 0; });
            var fromIn = r.range.indexOf('*') >= 0 || fromTypes.some(function (t) { return r.range.indexOf(t) >= 0; });
            if (fromOut) r.range.forEach(function (t) { if (t !== '*') out.push({ relation: r.key, direction: 'out', to_type: t }); });
            if (fromIn) r.domain.forEach(function (t) { if (t !== '*') out.push({ relation: r.key, direction: 'in', to_type: t }); });
        });
        return out.slice(0, 400);
    }
    function hopText(h) { return h.relation + ' ' + (h.direction === 'out' ? '→' : '←') + ' ' + h.to_type; }
    function hopValue(h) { return h.relation + '|' + h.direction + '|' + h.to_type; }

    /* A labelled path editor; get() gives {label, path, where?} or null when it has no hop. */
    function pathEditor(startTypes, initial, removable, onRemove) {
        var box = el('div', 'b-vw-path');
        var hops = (initial && initial.path ? initial.path.slice() : []);
        var label = input('label', initial && initial.label);
        label.placeholder = 'Beschriftung, z. B. Komponenten';
        var row = el('div', 'b-vw-path-row');
        box.appendChild(field('Beschriftung', label));
        box.appendChild(row);
        function draw() {
            row.textContent = '';
            var at = startTypes.slice();
            row.appendChild(el('span', 'b-vw-hop-from', at.join(' / ') || '–'));
            hops.forEach(function (h, i) {
                var options = hopOptions(at);
                var s = el('select', 'b-select');
                s.setAttribute('aria-label', 'Schritt ' + (i + 1) + ' des Pfads');
                var known = false;
                options.forEach(function (o) {
                    var selected = hopValue(o) === hopValue(h);
                    known = known || selected;
                    s.appendChild(option(hopValue(o), hopText(o), selected));
                });
                if (!known) s.insertBefore(option(hopValue(h), hopText(h) + ' (passt nicht)', true), s.firstChild);
                s.addEventListener('change', function () {
                    var p = s.value.split('|');
                    hops[i] = { relation: p[0], direction: p[1], to_type: p[2] };
                    hops = hops.slice(0, i + 1);
                    draw();
                });
                row.appendChild(s);
                at = [h.to_type];
            });
            var next = hopOptions(at);
            if (next.length) {
                var add = el('select', 'b-select');
                add.setAttribute('aria-label', 'Nächster Schritt des Pfads');
                add.appendChild(option('', hops.length ? '+ weiter …' : 'Beziehung wählen …', true));
                next.forEach(function (o) { add.appendChild(option(hopValue(o), hopText(o))); });
                add.addEventListener('change', function () {
                    if (!add.value) return;
                    var p = add.value.split('|');
                    hops.push({ relation: p[0], direction: p[1], to_type: p[2] });
                    draw();
                });
                row.appendChild(add);
            }
            if (hops.length) row.appendChild(button('Letzten Schritt entfernen', 'b-button--quiet b-button--small', function () { hops.pop(); draw(); }));
            if (removable) row.appendChild(button('Pfad entfernen', 'b-button--quiet b-button--small', function () { box.remove(); if (onRemove) onRemove(); }));
        }
        draw();
        box.get = function () {
            if (!hops.length) return null;
            var out = { path: hops.slice() };
            if (label.value.trim()) out.label = label.value.trim();
            if (initial && initial.where) out.where = initial.where;
            return out;
        };
        return box;
    }

    /* A list of path editors with "Pfad hinzufügen"; get() gives the paths. */
    function pathList(startTypes, initial) {
        var box = el('div', 'b-vw-paths');
        var editors = [];
        var holder = el('div', 'b-vw-paths-list');
        box.appendChild(holder);
        function add(p) {
            var ed = pathEditor(startTypes(), p, true, function () { editors = editors.filter(function (x) { return x !== ed; }); });
            editors.push(ed);
            holder.appendChild(ed);
        }
        (initial || []).forEach(add);
        box.appendChild(button('Pfad hinzufügen', 'b-button--secondary b-button--small', function () { add(null); }));
        box.get = function () { return editors.map(function (e) { return e.get(); }).filter(Boolean); };
        return box;
    }

    /* ---- the rail: list and check ----------------------------------------- */

    var NEW_LABELS = { profiles: 'Neues Profil', pages: 'Neue Seite', spines: 'Neue Hierarchie', roles: '' };

    function counts(p) {
        var meta = el('span', 'b-proc-item-meta');
        if (p.critical) meta.appendChild(tag(p.critical + ' ' + (p.critical === 1 ? 'Problem' : 'Probleme'), 'critical'));
        if (p.warning) meta.appendChild(tag(p.warning + ' ' + (p.warning === 1 ? 'Warnung' : 'Warnungen'), 'warning'));
        return meta;
    }

    function renderList() {
        listEl.textContent = '';
        var items = [];
        if (state.tab === 'profiles') {
            items = data.profiles.map(function (p) {
                return { key: p.id, name: p.definition.audience || p.id, sub: p.id + (p.platform ? '' : ' · nicht über die Plattform'),
                         problems: p.problems, current: p.id === state.profile,
                         pick: function () { state.profile = p.id; state.previewView = null; state.previewPage = null; render(); } };
            });
        } else if (state.tab === 'pages') {
            items = data.pages.map(function (p) {
                return { key: p.key, name: (p.name || {}).de || p.key, sub: p.key + ' · ' + p.type, problems: p.problems,
                         current: p.key === state.page, pick: function () { state.page = p.key; render(); } };
            });
        } else if (state.tab === 'spines') {
            items = data.spines.map(function (s) {
                return { key: s.key, name: s.name || s.key, sub: s.key + ' · ' + (s.levels || []).length + ' Ebenen', problems: s.problems,
                         current: s.key === state.spine,
                         pick: function () { state.spine = s.key; state.level = null; state.branch = null; render(); } };
            });
        }
        items.forEach(function (it) {
            var li = el('li');
            var b = el('button', 'b-proc-item' + (it.current ? ' is-current' : ''));
            b.type = 'button';
            b.dataset.viewsItem = it.key;
            if (it.current) b.setAttribute('aria-current', 'true');
            b.appendChild(el('span', 'b-proc-item-name', it.name));
            var meta = counts(it.problems || {});
            meta.insertBefore(el('span', 'b-mono', it.sub), meta.firstChild);
            b.appendChild(meta);
            b.addEventListener('click', it.pick);
            li.appendChild(b);
            listEl.appendChild(li);
        });
        listEmpty.hidden = items.length > 0 || state.tab === 'roles';
        listEmpty.textContent = { profiles: 'Noch keine Profile.', pages: 'Noch keine Seiten.', spines: 'Noch keine Hierarchien.' }[state.tab] || '';
        if (state.tab === 'pages' && data.unpaged.length) {
            var head = el('li', 'b-vw-list-head');
            head.appendChild(el('span', 'b-small b-muted', 'Gelesen, aber ohne Seite'));
            listEl.appendChild(head);
            data.unpaged.forEach(function (t) {
                var li = el('li', 'b-vw-unpaged');
                li.appendChild(el('span', 'b-mono', t));
                li.appendChild(button('Seite anlegen', 'b-button--quiet b-button--small', function () {
                    editOrToast({ op: 'page.create', type: t }).then(function (payload) {
                        if (payload.success) {
                            var made = data.pages.filter(function (p) { return p.type === t; })[0];
                            if (made) { state.page = made.key; render(); }
                            toast('Seite angelegt', 'Für ' + t + ' gibt es jetzt eine Seite mit einer Beschreibung.');
                        }
                    });
                }, 'Seite für ' + t + ' anlegen'));
                listEl.appendChild(li);
            });
        }
        newButton.hidden = state.tab === 'roles';
        newLabel.textContent = NEW_LABELS[state.tab];
    }

    function renderCheck() {
        checkEl.textContent = '';
        var area = { profiles: 'profile', pages: 'page', spines: 'spine', roles: 'roles' }[state.tab];
        var mine = data.problems.filter(function (f) { return f.area === area; });
        if (!mine.length) {
            checkEl.appendChild(el('li', 'b-proc-check-ok', 'Keine Probleme in diesem Teil.'));
            return;
        }
        mine.slice(0, 60).forEach(function (f) {
            var li = el('li', 'b-proc-check-item');
            li.appendChild(tag(f.severity === 'critical' ? 'Problem' : 'Warnung', f.severity === 'critical' ? 'critical' : 'warning'));
            var b = el('button', 'b-link-strong b-vw-check-link', (f.item ? f.item + ': ' : '') + f.message);
            b.type = 'button';
            b.addEventListener('click', function () {
                if (area === 'profile' && f.item) state.profile = f.item;
                if (area === 'page' && f.item) state.page = f.item;
                if (area === 'spine' && f.item) { state.spine = f.item; state.level = f.part || null; }
                render();
            });
            li.appendChild(b);
            checkEl.appendChild(li);
        });
        if (mine.length > 60) checkEl.appendChild(el('li', 'b-muted b-small', 'und ' + (mine.length - 60) + ' weitere'));
    }

    /* ---- profiles --------------------------------------------------------- */

    function currentProfile() {
        return data.profiles.filter(function (p) { return p.id === state.profile; })[0] || null;
    }

    function viewForm(profile, view) {
        return function (body) {
            var d = (view && view.definition) || {};
            var id = input('id', view && view.id, 'b-mono');
            id.placeholder = 'z. B. akten-liste';
            var label = input('label', view && view.label);
            var renderSel = select('render', Object.keys(data.renders).map(function (k) { return [k, data.renders[k]]; }), (view && view.render) || 'list');
            var description = textarea('description', d.description);
            var roots = input('root_types', (d.root_types || []).join(', '), 'b-mono');
            roots.setAttribute('list', 'b-vw-types');
            var procs = select('process', [['', '—']].concat(data.processes.map(function (p) { return [p.key, p.key + ' · ' + p.name]; })), d.process);
            var spines = select('spine', [['', '—']].concat(data.spines.map(function (s) { return [s.key, s.key + ' · ' + (s.name || '')]; })), d.spine);
            var columns = input('columns', (d.columns || []).join(', '), 'b-mono');
            var group = input('group_by', d.group_by, 'b-mono');
            group.setAttribute('list', 'b-vw-types');
            var rules = input('rules', (d.rules || []).join(', '), 'b-mono');
            var paths = pathList(function () { return list(roots.value); }, d.paths || []);
            var grid = el('div', 'b-vw-grid');
            grid.appendChild(field('Kennung', id, 'Klein geschrieben, Wörter mit Bindestrich.'));
            grid.appendChild(field('Beschriftung', label));
            grid.appendChild(field('Darstellung', renderSel));
            grid.appendChild(field('Wurzeltypen', roots, 'Die Typen, von denen die Ansicht ausgeht, mit Komma getrennt.'));
            grid.appendChild(field('Prozess (Tafel, Spur)', procs));
            grid.appendChild(field('Hierarchie (Karte, Matrix)', spines));
            grid.appendChild(field('Spalten', columns, 'Attribute der Wurzeltypen, mit Komma getrennt.'));
            grid.appendChild(field('Gruppieren nach', group));
            grid.appendChild(field('Regeln', rules, 'Etwa R1, R7, P-owner.'));
            body.appendChild(grid);
            body.appendChild(field('Beschreibung', description));
            var pathsField = el('div', 'b-field');
            pathsField.appendChild(el('span', 'b-label', 'Pfade von den Wurzeltypen'));
            pathsField.appendChild(paths);
            body.appendChild(pathsField);
            return function () {
                if (!id.value.trim()) throw 'Die Kennung fehlt.';
                var set = { id: id.value.trim(), label: label.value.trim(), render: renderSel.value,
                            definition: { description: description.value, root_types: list(roots.value), paths: paths.get(),
                                          process: procs.value, spine: spines.value, columns: list(columns.value),
                                          group_by: group.value.trim(), rules: list(rules.value) } };
                if (view) return { op: 'view.update', profile: profile.id, view: view.id, set: set,
                                   after: function () { toast('Ansicht übernommen', set.label || set.id); } };
                return { op: 'view.add', profile: profile.id, view: set,
                         after: function () { state.previewView = set.id; render(); toast('Ansicht hinzugefügt', set.label || set.id); } };
            };
        };
    }

    function renderProfile() {
        var p = currentProfile();
        editor.textContent = '';
        if (!p) {
            editor.appendChild(el('p', 'b-muted', data.profiles.length ? 'Wählen Sie links ein Profil.' : 'Legen Sie das erste Profil an: für wen, und was diese Leute sehen.'));
            return;
        }
        var d = p.definition;
        var head = el('div', 'b-src-head');
        var titles = el('div');
        titles.appendChild(el('h2', 'b-h2', d.audience || p.id));
        titles.appendChild(el('span', 'b-mono b-muted', p.file));
        head.appendChild(titles);
        head.appendChild(button('Profil entfernen', 'b-button--quiet b-button--small', function () {
            editOrToast({ op: 'profile.remove', id: p.id }).then(function (r) { if (r.success) { state.profile = null; render(); } });
        }));
        editor.appendChild(head);

        var form = el('form', 'b-vw-section');
        form.noValidate = true;
        form.appendChild(el('h3', 'b-h3', 'Profil'));
        var grid = el('div', 'b-vw-grid');
        var audience = input('audience', d.audience);
        var delivered = input('delivered_through', d.delivered_through);
        var role = input('role', d.role, 'b-mono');
        role.placeholder = 'z. B. ROLE-PARTNER';
        grid.appendChild(field('Zielgruppe', audience));
        grid.appendChild(field('Ausgeliefert über', delivered, 'Platform, secured API, CI job …'));
        grid.appendChild(field('Rolle im Brain', role));
        form.appendChild(grid);
        var description = textarea('description', d.description);
        form.appendChild(field('Beschreibung', description));
        var lens = d.lens || {};
        var lensTypes = textarea('lens_types', (lens.types || []).join(', '));
        lensTypes.classList.add('b-mono');
        form.appendChild(field('Typen im Blickfeld', lensTypes, 'Mit Komma getrennt; * für alle.'));
        var spineBox = el('fieldset', 'b-vw-choices');
        spineBox.appendChild(el('legend', 'b-label', 'Hierarchien im Blickfeld'));
        var spineChecks = data.spines.map(function (s) {
            var c = checkbox(s.name || s.key, (lens.spines || []).indexOf(s.key) >= 0);
            c.input.value = s.key;
            spineBox.appendChild(c.wrap);
            return c.input;
        });
        if (!data.spines.length) spineBox.appendChild(el('span', 'b-muted b-small', 'Noch keine Hierarchien.'));
        form.appendChild(spineBox);
        var pageBox = el('fieldset', 'b-vw-choices');
        pageBox.appendChild(el('legend', 'b-label', 'Seiten'));
        var pageChecks = data.pages.map(function (pg) {
            var c = checkbox((pg.name || {}).de + ' · ' + pg.key, (d.pages || []).indexOf(pg.key) >= 0);
            c.input.value = pg.key;
            pageBox.appendChild(c.wrap);
            return c.input;
        });
        if (!data.pages.length) pageBox.appendChild(el('span', 'b-muted b-small', 'Noch keine Seiten.'));
        form.appendChild(pageBox);
        var save = el('button', 'b-button b-button--primary', 'Profil übernehmen');
        save.type = 'submit';
        var row = el('div', 'b-button-row');
        row.appendChild(save);
        form.appendChild(row);
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            editOrToast({ op: 'profile.update', id: p.id, set: {
                audience: audience.value, delivered_through: delivered.value, role: role.value.trim(), description: description.value,
                lens_types: list(lensTypes.value),
                lens_spines: spineChecks.filter(function (c) { return c.checked; }).map(function (c) { return c.value; }),
                pages: pageChecks.filter(function (c) { return c.checked; }).map(function (c) { return c.value; }) } })
                .then(function (r) { if (r.success) toast('Profil übernommen', d.audience || p.id); });
        });
        editor.appendChild(form);

        var views = el('section', 'b-vw-section');
        var vh = el('div', 'b-src-head');
        vh.appendChild(el('h3', 'b-h3', 'Ansichten'));
        vh.appendChild(button('Ansicht hinzufügen', 'b-button--secondary b-button--small', function () {
            openDialog('Neue Ansicht', 'Eine Ansicht des Profils ' + (d.audience || p.id) + ': Karte, Spur, Tafel, Liste, Matrix oder Seite.', 'Ansicht hinzufügen', viewForm(p, null));
        }));
        views.appendChild(vh);
        views.appendChild(el('p', 'b-muted b-small', 'Die erste Ansicht ist die Startansicht des Profils.'));
        var ol = el('ol', 'b-vw-items');
        (d.views || []).forEach(function (v, i) {
            var li = el('li', 'b-vw-item' + (state.previewView === v.id ? ' is-current' : ''));
            li.dataset.viewsView = v.id;
            var main = el('div', 'b-vw-item-main');
            var title = el('span', 'b-proc-item-name', v.label || v.id);
            main.appendChild(title);
            var meta = el('span', 'b-proc-item-meta');
            meta.appendChild(tag(data.renders[v.render] || v.render, 'accent'));
            meta.appendChild(el('span', 'b-mono', v.id));
            if (i === 0) meta.appendChild(tag('Startansicht'));
            var vp = data.problems.filter(function (f) { return f.area === 'profile' && f.item === p.id && f.part === v.id; });
            if (vp.length) meta.appendChild(tag(vp.length + ' Hinweis' + (vp.length === 1 ? '' : 'e'), vp.some(function (f) { return f.severity === 'critical'; }) ? 'critical' : 'warning'));
            main.appendChild(meta);
            if ((v.definition || {}).description) main.appendChild(el('span', 'b-small b-muted', v.definition.description));
            li.appendChild(main);
            var tools = el('div', 'b-vw-item-tools');
            tools.appendChild(button('Vorschau', 'b-button--quiet b-button--small', function () { state.previewView = v.id; renderProfile(); preview(); }, 'Vorschau von ' + (v.label || v.id)));
            tools.appendChild(button('↑', 'b-button--quiet b-button--small', function () { editOrToast({ op: 'view.move', profile: p.id, view: v.id, to: i - 1 }); }, (v.label || v.id) + ' nach oben')).disabled = i === 0;
            tools.appendChild(button('↓', 'b-button--quiet b-button--small', function () { editOrToast({ op: 'view.move', profile: p.id, view: v.id, to: i + 1 }); }, (v.label || v.id) + ' nach unten')).disabled = i === d.views.length - 1;
            tools.appendChild(button('Bearbeiten', 'b-button--quiet b-button--small', function () {
                openDialog('Ansicht bearbeiten', v.label || v.id, 'Übernehmen', viewForm(p, v));
            }, (v.label || v.id) + ' bearbeiten'));
            tools.appendChild(button('Entfernen', 'b-button--quiet b-button--small', function () { editOrToast({ op: 'view.remove', profile: p.id, view: v.id }); }, (v.label || v.id) + ' entfernen'));
            li.appendChild(tools);
            ol.appendChild(li);
        });
        if (!(d.views || []).length) views.appendChild(el('p', 'b-muted', 'Noch keine Ansichten.'));
        views.appendChild(ol);
        editor.appendChild(views);
    }

    /* ---- pages ------------------------------------------------------------ */

    function currentPage() { return data.pages.filter(function (p) { return p.key === state.page; })[0] || null; }

    function contentText(s) {
        if (s.field) return 'Feld ' + s.field;
        if (s.facts) return 'Attribute ' + s.facts.join(', ');
        if (s.paths) return s.paths.map(function (sp) { return (sp.label ? sp.label + ': ' : '') + sp.path.map(hopText).join(' / '); }).join(' · ');
        return '–';
    }

    function slotForm(page, slot) {
        return function (body) {
            var id = input('id', slot && slot.id, 'b-mono');
            id.placeholder = 'z. B. verantwortliche';
            var de = input('title_de', slot && slot.title && slot.title.de);
            var en = input('title_en', slot && slot.title && slot.title.en);
            var renderSel = select('render', Object.keys(data.section_renders).map(function (k) { return [k, data.section_renders[k]]; }), (slot && slot.render) || 'list');
            var required = checkbox('Pflichtabschnitt: leer zählt als Lücke', slot ? slot.required : false);
            var kind = select('kind', [['paths', 'Pfade vom Seitenknoten'], ['facts', 'Attribute des Knotens'], ['field', 'Feld des Knotens']],
                              slot ? (slot.field ? 'field' : slot.facts ? 'facts' : 'paths') : 'paths');
            var fieldSel = select('field', [['description', 'Beschreibung'], ['name', 'Name']], slot && slot.field);
            var attrs = attributesOf([page.type]);
            var facts = input('facts', slot && slot.facts ? slot.facts.join(', ') : '', 'b-mono');
            var columns = input('columns', slot && slot.columns ? slot.columns.join(', ') : '', 'b-mono');
            var orderFact = input('order_fact', slot && slot.order_by ? slot.order_by.fact : '', 'b-mono');
            var orderDir = select('order_dir', [['asc', 'aufsteigend'], ['desc', 'absteigend']], slot && slot.order_by ? slot.order_by.direction : 'asc');
            var limit = input('limit', slot && slot.limit ? String(slot.limit) : '');
            limit.inputMode = 'numeric';
            var paths = pathList(function () { return [page.type]; }, (slot && slot.paths) || []);
            var grid = el('div', 'b-vw-grid');
            grid.appendChild(field('Kennung', id, 'Klein geschrieben, Wörter mit Unterstrich.'));
            grid.appendChild(field('Titel (de)', de));
            grid.appendChild(field('Titel (en)', en, 'Leer: wie der deutsche.'));
            grid.appendChild(field('Darstellung', renderSel));
            grid.appendChild(field('Inhalt', kind));
            body.appendChild(grid);
            body.appendChild(required.wrap);
            var fieldBox = field('Feld', fieldSel);
            var factsBox = field('Attribute', facts, attrs.length ? 'Von ' + page.type + ': ' + attrs.slice(0, 20).join(', ') : '');
            var pathsBox = el('div', 'b-field');
            pathsBox.appendChild(el('span', 'b-label', 'Pfade von ' + page.type));
            pathsBox.appendChild(paths);
            var extra = el('div', 'b-vw-grid');
            extra.appendChild(field('Spalten (Tabelle)', columns, 'Attribute der Ziele, mit Komma getrennt.'));
            extra.appendChild(field('Sortieren nach', orderFact));
            extra.appendChild(field('Richtung', orderDir));
            extra.appendChild(field('Höchstens', limit));
            pathsBox.appendChild(extra);
            body.appendChild(fieldBox);
            body.appendChild(factsBox);
            body.appendChild(pathsBox);
            function show() {
                fieldBox.hidden = kind.value !== 'field';
                factsBox.hidden = kind.value !== 'facts';
                pathsBox.hidden = kind.value !== 'paths';
            }
            kind.addEventListener('change', show);
            show();
            return function () {
                if (!id.value.trim()) throw 'Die Kennung fehlt.';
                if (!de.value.trim()) throw 'Der Titel fehlt.';
                var content = {};
                if (kind.value === 'field') content.field = fieldSel.value;
                if (kind.value === 'facts') content.facts = list(facts.value);
                if (kind.value === 'paths') content.paths = paths.get();
                var set = { id: id.value.trim(), title: { de: de.value.trim(), en: en.value.trim() }, render: renderSel.value,
                            required: required.input.checked, content: content };
                if (kind.value === 'paths') {
                    set.columns = list(columns.value);
                    set.order_by = orderFact.value.trim() ? { fact: orderFact.value.trim(), direction: orderDir.value } : null;
                    set.limit = limit.value.trim() ? Number(limit.value) : null;
                }
                if (slot) return { op: 'slot.update', page: page.key, slot: slot.id, set: set,
                                   after: function () { toast('Abschnitt übernommen', set.title.de); } };
                return { op: 'slot.add', page: page.key, slot: set, after: function () { toast('Abschnitt hinzugefügt', set.title.de); } };
            };
        };
    }

    function renderPage() {
        var p = currentPage();
        editor.textContent = '';
        if (!p) {
            editor.appendChild(el('p', 'b-muted', data.pages.length ? 'Wählen Sie links eine Seite.' : 'Legen Sie die erste Seite an: was ein Knoten eines Typs zeigt.'));
            return;
        }
        var head = el('div', 'b-src-head');
        var titles = el('div');
        titles.appendChild(el('h2', 'b-h2', (p.name || {}).de || p.key));
        titles.appendChild(el('span', 'b-mono b-muted', p.key + ' · ' + p.type));
        head.appendChild(titles);
        head.appendChild(button('Seite entfernen', 'b-button--quiet b-button--small', function () {
            editOrToast({ op: 'page.remove', key: p.key }).then(function (r) { if (r.success) { state.page = null; render(); } });
        }));
        editor.appendChild(head);

        var form = el('form', 'b-vw-section');
        form.noValidate = true;
        var grid = el('div', 'b-vw-grid');
        var de = input('name_de', (p.name || {}).de);
        var en = input('name_en', (p.name || {}).en);
        var type = input('type', p.type, 'b-mono');
        type.setAttribute('list', 'b-vw-types');
        var prefix = input('key_prefix', (p.filter || {}).key_prefix, 'b-mono');
        grid.appendChild(field('Name (de)', de));
        grid.appendChild(field('Name (en)', en));
        grid.appendChild(field('Typ', type));
        grid.appendChild(field('Nur Schlüssel mit Anfang', prefix, 'Etwa L2- für eine Ebene eines Typs.'));
        form.appendChild(grid);
        var save = el('button', 'b-button b-button--secondary', 'Seitenangaben übernehmen');
        save.type = 'submit';
        form.appendChild(save);
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            editOrToast({ op: 'page.update', key: p.key, set: { name: { de: de.value, en: en.value }, type: type.value.trim(), key_prefix: prefix.value } })
                .then(function (r) { if (r.success) toast('Seite übernommen', p.key); });
        });
        editor.appendChild(form);

        var slots = el('section', 'b-vw-section');
        var sh = el('div', 'b-src-head');
        sh.appendChild(el('h3', 'b-h3', 'Abschnitte'));
        sh.appendChild(button('Abschnitt hinzufügen', 'b-button--secondary b-button--small', function () {
            openDialog('Neuer Abschnitt', 'Was die Seite ' + ((p.name || {}).de || p.key) + ' zeigt, und woher es kommt.', 'Abschnitt hinzufügen', slotForm(p, null));
        }));
        slots.appendChild(sh);
        var ol = el('ol', 'b-vw-items');
        (p.sections || []).forEach(function (s, i) {
            var li = el('li', 'b-vw-item');
            li.dataset.viewsSlot = s.id;
            var main = el('div', 'b-vw-item-main');
            main.appendChild(el('span', 'b-proc-item-name', (s.title || {}).de || s.id));
            var meta = el('span', 'b-proc-item-meta');
            meta.appendChild(tag(data.section_renders[s.render] || s.render, 'accent'));
            if (s.required) meta.appendChild(tag('Pflicht'));
            meta.appendChild(el('span', 'b-mono', s.id));
            var sp = data.problems.filter(function (f) { return f.area === 'page' && f.item === p.key && f.part === s.id; });
            if (sp.length) meta.appendChild(tag(sp.length + ' Hinweis' + (sp.length === 1 ? '' : 'e'), sp.some(function (f) { return f.severity === 'critical'; }) ? 'critical' : 'warning'));
            main.appendChild(meta);
            main.appendChild(el('span', 'b-small b-muted b-vw-content', contentText(s)));
            li.appendChild(main);
            var tools = el('div', 'b-vw-item-tools');
            var name = (s.title || {}).de || s.id;
            tools.appendChild(button('↑', 'b-button--quiet b-button--small', function () { editOrToast({ op: 'slot.move', page: p.key, slot: s.id, to: i - 1 }); }, name + ' nach oben')).disabled = i === 0;
            tools.appendChild(button('↓', 'b-button--quiet b-button--small', function () { editOrToast({ op: 'slot.move', page: p.key, slot: s.id, to: i + 1 }); }, name + ' nach unten')).disabled = i === p.sections.length - 1;
            tools.appendChild(button('Bearbeiten', 'b-button--quiet b-button--small', function () { openDialog('Abschnitt bearbeiten', name, 'Übernehmen', slotForm(p, s)); }, name + ' bearbeiten'));
            tools.appendChild(button('Entfernen', 'b-button--quiet b-button--small', function () { editOrToast({ op: 'slot.remove', page: p.key, slot: s.id }); }, name + ' entfernen'));
            li.appendChild(tools);
            ol.appendChild(li);
        });
        slots.appendChild(ol);
        editor.appendChild(slots);
    }

    /* ---- spines ----------------------------------------------------------- */

    function currentSpine() { return data.spines.filter(function (s) { return s.key === state.spine; })[0] || null; }

    function linkOptions(childType, parentTypes) {
        var out = [];
        data.relations.forEach(function (r) {
            parentTypes.forEach(function (pt) {
                var fitsOut = (r.domain.indexOf('*') >= 0 || r.domain.indexOf(childType) >= 0) && (r.range.indexOf('*') >= 0 || r.range.indexOf(pt) >= 0);
                var fitsIn = (r.domain.indexOf('*') >= 0 || r.domain.indexOf(pt) >= 0) && (r.range.indexOf('*') >= 0 || r.range.indexOf(childType) >= 0);
                if (fitsOut && !out.some(function (o) { return o.relation === r.key && o.direction === 'out'; })) out.push({ relation: r.key, direction: 'out' });
                if (fitsIn && !out.some(function (o) { return o.relation === r.key && o.direction === 'in'; })) out.push({ relation: r.key, direction: 'in' });
            });
        });
        return out;
    }
    function linkOf(level, t) {
        return (level.links_to_parent || {})[t] || level.link_to_parent || null;
    }
    function linkText(link) {
        if (!link) return 'keine Verbindung';
        return link.direction === 'out' ? link.relation + ' → zum Elternknoten' : link.relation + ' ← vom Elternknoten';
    }

    function levelForm(spine, branch, levels, level, index) {
        var form = el('form', 'b-vw-level-form');
        form.noValidate = true;
        form.dataset.viewsLevelForm = level.id;
        var grid = el('div', 'b-vw-grid');
        var name = input('name', level.name);
        var types = input('types', (level.types || []).join(', '), 'b-mono');
        types.setAttribute('list', 'b-vw-types');
        var prefix = input('key_prefix', (level.filter || {}).key_prefix, 'b-mono');
        grid.appendChild(field('Name der Ebene', name));
        grid.appendChild(field('Typen', types, 'Mit Komma getrennt.'));
        grid.appendChild(field('Nur Schlüssel mit Anfang', prefix));
        form.appendChild(grid);
        var linkSelects = {};
        if (index > 0) {
            var parents = levels[index - 1].types || [];
            var box = el('fieldset', 'b-vw-links');
            box.appendChild(el('legend', 'b-label', 'Verbindung zur Ebene darüber (' + (levels[index - 1].name || '') + ')'));
            (level.types || []).forEach(function (t) {
                var options = linkOptions(t, parents);
                var current = linkOf(level, t);
                var s = el('select', 'b-select');
                s.appendChild(option('', '— keine —', !current));
                var known = false;
                options.forEach(function (o) {
                    var on = !!current && current.relation === o.relation && current.direction === o.direction;
                    known = known || on;
                    s.appendChild(option(o.relation + '|' + o.direction, linkText(o), on));
                });
                if (current && !known) s.insertBefore(option(current.relation + '|' + current.direction, linkText(current) + ' (passt nicht)', true), s.children[1]);
                linkSelects[t] = s;
                box.appendChild(field(t + ' hängt an ' + parents.join(' / '), s));
            });
            if (!(level.types || []).length) box.appendChild(el('span', 'b-muted b-small', 'Zuerst Typen wählen.'));
            form.appendChild(box);
        }
        var verified = checkbox('Bestätigt: der Mandant hat solche Verbindungen', level.verified !== false);
        form.appendChild(verified.wrap);
        var row = el('div', 'b-button-row');
        var save = el('button', 'b-button b-button--primary b-button--small', 'Ebene übernehmen');
        save.type = 'submit';
        row.appendChild(save);
        row.appendChild(button('Ebene darunter', 'b-button--secondary b-button--small', function () {
            editOrToast({ op: 'level.add', spine: spine.key, branch: branch, at: index + 1, level: { name: 'Neue Ebene', types: [] } })
                .then(function (r) {
                    if (!r.success) return;
                    var s = currentSpine();
                    var chain = branch ? (s.branches || []).filter(function (b) { return b.key === branch; })[0].levels : s.levels;
                    state.level = chain[index + 1].id;
                    render();
                });
        }));
        row.appendChild(button('Entfernen', 'b-button--quiet b-button--small', function () {
            editOrToast({ op: 'level.remove', spine: spine.key, branch: branch, level: level.id }).then(function (r) { if (r.success) { state.level = null; render(); } });
        }, 'Ebene ' + level.name + ' entfernen'));
        form.appendChild(row);
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            var set = { name: name.value, types: list(types.value), key_prefix: prefix.value, verified: verified.input.checked };
            if (index > 0) {
                var links = {};
                Object.keys(linkSelects).forEach(function (t) {
                    if (linkSelects[t].value) { var p = linkSelects[t].value.split('|'); links[t] = { relation: p[0], direction: p[1] }; }
                });
                set.links = links;
            }
            editOrToast({ op: 'level.update', spine: spine.key, branch: branch, level: level.id, set: set })
                .then(function (r) { if (r.success) toast('Ebene übernommen', set.name); });
        });
        return form;
    }

    function chainTree(spine, branch, levels, startDepth) {
        var ol = el('ol', 'b-vw-tree');
        levels.forEach(function (lv, i) {
            var li = el('li', 'b-vw-level');
            li.style.setProperty('--depth', String(startDepth + i));
            var selected = state.level === lv.id && (state.branch || null) === (branch || null);
            var row = el('div', 'b-vw-level-row' + (selected ? ' is-current' : ''));
            var pick = el('button', 'b-vw-level-pick');
            pick.type = 'button';
            pick.dataset.viewsLevel = lv.id;
            pick.setAttribute('aria-expanded', String(selected));
            pick.appendChild(el('span', 'b-mono b-muted', lv.id));
            pick.appendChild(el('span', 'b-proc-item-name', lv.name || lv.id));
            var chips = el('span', 'b-vw-chips');
            (lv.types || []).forEach(function (t) { chips.appendChild(tag(t)); });
            if (!(lv.types || []).length) chips.appendChild(tag('ohne Typ', 'critical'));
            pick.appendChild(chips);
            if (i > 0) {
                var links = (lv.types || []).map(function (t) { return linkOf(lv, t); });
                var first = links[0];
                var same = links.every(function (l) { return JSON.stringify(l) === JSON.stringify(first); });
                pick.appendChild(el('span', 'b-small b-muted', same ? linkText(first) : 'je Typ eine eigene Verbindung'));
            }
            if (lv.verified === false) pick.appendChild(tag('unbestätigt', 'warning'));
            pick.addEventListener('click', function () {
                state.level = selected ? null : lv.id;
                state.branch = branch || null;
                render();
            });
            row.appendChild(pick);
            var tools = el('span', 'b-vw-item-tools');
            tools.appendChild(button('↑', 'b-button--quiet b-button--small', function () { editOrToast({ op: 'level.move', spine: spine.key, branch: branch, level: lv.id, to: i - 1 }); }, (lv.name || lv.id) + ' nach oben')).disabled = i === 0;
            tools.appendChild(button('↓', 'b-button--quiet b-button--small', function () { editOrToast({ op: 'level.move', spine: spine.key, branch: branch, level: lv.id, to: i + 1 }); }, (lv.name || lv.id) + ' nach unten')).disabled = i === levels.length - 1;
            row.appendChild(tools);
            li.appendChild(row);
            if (selected) li.appendChild(levelForm(spine, branch, levels, lv, i));
            ol.appendChild(li);
        });
        return ol;
    }

    function renderSpine() {
        var s = currentSpine();
        editor.textContent = '';
        if (!s) {
            editor.appendChild(el('p', 'b-muted', data.spines.length ? 'Wählen Sie links eine Hierarchie.' : 'Legen Sie die erste Hierarchie an: Ebenen von oben nach unten, jede mit ihren Typen.'));
            return;
        }
        var head = el('div', 'b-src-head');
        var titles = el('div');
        titles.appendChild(el('h2', 'b-h2', s.name || s.key));
        titles.appendChild(el('span', 'b-mono b-muted', s.key));
        head.appendChild(titles);
        head.appendChild(button('Hierarchie entfernen', 'b-button--quiet b-button--small', function () {
            editOrToast({ op: 'spine.remove', key: s.key }).then(function (r) { if (r.success) { state.spine = null; render(); } });
        }));
        editor.appendChild(head);
        var form = el('form', 'b-vw-section');
        form.noValidate = true;
        var name = input('name', s.name);
        var description = textarea('description', s.description);
        form.appendChild(field('Name', name));
        form.appendChild(field('Beschreibung', description));
        var save = el('button', 'b-button b-button--secondary', 'Angaben übernehmen');
        save.type = 'submit';
        form.appendChild(save);
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            editOrToast({ op: 'spine.update', key: s.key, set: { name: name.value, description: description.value } });
        });
        editor.appendChild(form);

        var tree = el('section', 'b-vw-section');
        var th = el('div', 'b-src-head');
        th.appendChild(el('h3', 'b-h3', 'Ebenen'));
        th.appendChild(button('Ebene hinzufügen', 'b-button--secondary b-button--small', function () {
            editOrToast({ op: 'level.add', spine: s.key, level: { name: 'Neue Ebene', types: [] } }).then(function (r) {
                if (!r.success) return;
                var levels = currentSpine().levels;
                state.level = levels[levels.length - 1].id;
                state.branch = null;
                render();
            });
        }));
        tree.appendChild(th);
        tree.appendChild(el('p', 'b-muted b-small', 'Von oben nach unten: jede Ebene hängt über eine Beziehung an der Ebene darüber.'));
        tree.appendChild(chainTree(s, null, s.levels || [], 0));
        (s.branches || []).forEach(function (b) {
            var bh = el('div', 'b-src-head b-vw-branch-head');
            bh.appendChild(el('h4', 'b-h3', 'Zweig: ' + (b.name || b.key)));
            var bt = el('span', 'b-vw-item-tools');
            bt.appendChild(button('Ebene im Zweig', 'b-button--secondary b-button--small', function () {
                editOrToast({ op: 'level.add', spine: s.key, branch: b.key, level: { name: 'Neue Ebene', types: [] } });
            }));
            bt.appendChild(button('Zweig entfernen', 'b-button--quiet b-button--small', function () { editOrToast({ op: 'branch.remove', spine: s.key, branch: b.key }); }));
            bh.appendChild(bt);
            tree.appendChild(bh);
            if (b.description) tree.appendChild(el('p', 'b-muted b-small', b.description));
            tree.appendChild(chainTree(s, b.key, b.levels || [], 1));
        });
        tree.appendChild(button('Zweig hinzufügen', 'b-button--quiet b-button--small', function () {
            openDialog('Neuer Zweig', 'Eine zweite Kette, die an der Hierarchie ' + (s.name || s.key) + ' hängt.', 'Zweig anlegen', function (body) {
                var key = input('key', '', 'b-mono');
                var nm = input('name', '');
                body.appendChild(field('Schlüssel', key, 'Klein geschrieben, etwa sales.'));
                body.appendChild(field('Name', nm));
                return function () { return { op: 'branch.create', spine: s.key, key: key.value.trim(), name: nm.value.trim() }; };
            });
        }));
        editor.appendChild(tree);
    }

    /* ---- roles ------------------------------------------------------------ */

    function renderRoles() {
        editor.textContent = '';
        var section = el('section', 'b-vw-section');
        section.appendChild(el('h2', 'b-h2', 'Rollen und Profile'));
        section.appendChild(el('p', 'b-muted', 'Welche Profile die Rollen der Plattform sehen. Beim Veröffentlichen wird das die Einstellung «Rollen und Profile». Eine Rolle ohne Haken sieht keine Profilansichten, solange irgendeine Rolle eines hat.'));
        if (!data.profiles.length) {
            section.appendChild(el('p', 'b-muted', 'Noch keine Profile.'));
            editor.appendChild(section);
            return;
        }
        var wrap = el('div', 'b-table-wrap');
        var table = el('table', 'b-table b-table--compact b-vw-roles');
        var thead = el('thead');
        var hr = el('tr');
        hr.appendChild(el('th', null, 'Rolle'));
        data.profiles.forEach(function (p) {
            var th = el('th', null, p.definition.audience || p.id);
            th.scope = 'col';
            hr.appendChild(th);
        });
        thead.appendChild(hr);
        table.appendChild(thead);
        var tbody = el('tbody');
        data.roles.forEach(function (r) {
            var tr = el('tr');
            tr.dataset.viewsRole = r.role;
            var th = el('th', 'b-mono', r.role);
            th.scope = 'row';
            tr.appendChild(th);
            data.profiles.forEach(function (p) {
                var td = el('td');
                var c = el('input');
                c.type = 'checkbox';
                c.checked = r.profiles.indexOf(p.id) >= 0;
                c.setAttribute('aria-label', r.role + ' sieht ' + (p.definition.audience || p.id));
                c.addEventListener('change', function () {
                    var names = data.profiles.map(function (x) { return x.id; }).filter(function (id) {
                        return id === p.id ? c.checked : r.profiles.indexOf(id) >= 0;
                    });
                    editOrToast({ op: 'roles.set', role: r.role, profiles: names }).then(function (res) {
                        if (res.success) toast('Rolle zugeordnet', r.role + (c.checked ? ' sieht ' : ' sieht nicht mehr ') + (p.definition.audience || p.id));
                    });
                });
                td.appendChild(c);
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        wrap.appendChild(table);
        section.appendChild(wrap);
        editor.appendChild(section);
    }

    /* ---- the preview ------------------------------------------------------ */

    function card(c) {
        var box = el('div', 'b-vw-card');
        box.appendChild(el('span', 'b-vw-card-name', c.name));
        var meta = el('span', 'b-vw-card-meta');
        meta.appendChild(el('span', 'b-mono', c.key));
        meta.appendChild(el('span', null, c.type_name || c.type));
        box.appendChild(meta);
        (c.facts || []).forEach(function (f) { box.appendChild(el('span', 'b-small', f.name + ': ' + f.value)); });
        return box;
    }
    function tree(nodes) {
        var ul = el('ul', 'b-vw-ptree');
        nodes.forEach(function (n) {
            var li = el('li');
            if (n.via) li.appendChild(el('span', 'b-vw-via', n.via));
            li.appendChild(card(n));
            if (n.children && n.children.length) li.appendChild(tree(n.children));
            ul.appendChild(li);
        });
        return ul;
    }

    function drawView(v) {
        var box = el('section', 'b-vw-pview');
        box.dataset.viewsPreviewView = v.id;
        var head = el('div', 'b-vw-pview-head');
        head.appendChild(el('h3', 'b-h3', v.label));
        head.appendChild(tag(v.render_label, 'accent'));
        box.appendChild(head);
        if (v.description) box.appendChild(el('p', 'b-small b-muted', v.description));
        if (v.kind === 'unsupported') {
            box.appendChild(el('div', 'b-callout b-callout--warning', 'Die Plattform zeichnet diese Ansicht so nicht: ' + v.why));
            return box;
        }
        if (v.kind === 'map') {
            box.appendChild(tree(v.roots || []));
            if ((v.skipped || []).length) box.appendChild(el('p', 'b-small b-muted', 'Nicht gezeichnet (Pfad passt nicht): ' + v.skipped.join(', ')));
        } else if (v.kind === 'trace') {
            if ((v.steps || []).length) {
                var steps = el('ol', 'b-vw-steps');
                v.steps.forEach(function (s) { steps.appendChild(el('li', s.current ? 'is-current' : '', s.id)); });
                box.appendChild(steps);
            }
            var cols = el('div', 'b-vw-trace');
            if (v.root) { var c0 = el('div', 'b-vw-trace-col'); c0.appendChild(el('span', 'b-small b-muted', 'Ausgang')); c0.appendChild(card(v.root)); cols.appendChild(c0); }
            v.columns.forEach(function (c) {
                var col = el('div', 'b-vw-trace-col');
                col.appendChild(el('span', 'b-small b-muted', c.label));
                if (c.invalid) col.appendChild(tag('Pfad passt nicht', 'critical'));
                c.items.forEach(function (it) { col.appendChild(card(it)); });
                cols.appendChild(col);
            });
            box.appendChild(cols);
        } else if (v.kind === 'board') {
            var lanes = el('div', 'b-vw-board');
            v.lanes.forEach(function (l) {
                var lane = el('div', 'b-vw-lane' + (l.resting ? ' is-resting' : ''));
                lane.appendChild(el('span', 'b-vw-lane-name', l.name));
                l.cards.forEach(function (c) { lane.appendChild(card(c)); });
                lanes.appendChild(lane);
            });
            box.appendChild(lanes);
        } else if (v.kind === 'list') {
            var wrap = el('div', 'b-table-wrap');
            var table = el('table', 'b-table b-table--compact');
            var hr = el('tr');
            ['Name', 'Schlüssel'].concat(v.group_by ? [v.group_by] : [], v.columns, v.path_columns).forEach(function (h) { hr.appendChild(el('th', null, h)); });
            var thead = el('thead');
            thead.appendChild(hr);
            table.appendChild(thead);
            var tbody = el('tbody');
            v.rows.forEach(function (r) {
                var tr = el('tr');
                [r.name, r.key].concat(v.group_by ? [r.group] : [], r.facts.map(function (f) { return f.value; }), r.paths.map(function (p) { return p.value; }))
                    .forEach(function (x) { tr.appendChild(el('td', null, x)); });
                tbody.appendChild(tr);
            });
            table.appendChild(tbody);
            wrap.appendChild(table);
            box.appendChild(wrap);
        } else if (v.kind === 'matrix') {
            var mw = el('div', 'b-table-wrap');
            var mt = el('table', 'b-table b-table--compact b-vw-matrix');
            var mh = el('tr');
            mh.appendChild(el('th'));
            v.columns.forEach(function (c) { mh.appendChild(el('th', null, c)); });
            var mhead = el('thead');
            mhead.appendChild(mh);
            mt.appendChild(mhead);
            var mb = el('tbody');
            v.rows.forEach(function (r, i) {
                var tr = el('tr');
                tr.appendChild(el('th', null, r));
                v.cells[i].forEach(function (cell) {
                    var td = el('td', 'b-vw-cell b-vw-cell--' + cell);
                    td.appendChild(el('span', 'b-visually-hidden', { filled: 'gefüllt', missing: 'fehlt', stale: 'veraltet', empty: 'leer' }[cell]));
                    tr.appendChild(td);
                });
                mb.appendChild(tr);
            });
            mt.appendChild(mb);
            mw.appendChild(mt);
            box.appendChild(mw);
        } else {
            box.appendChild(el('p', 'b-muted', 'Diese Ansicht zeigt die Seite unten.'));
        }
        return box;
    }

    function drawPage(pg) {
        var box = el('section', 'b-vw-ppage');
        box.dataset.viewsPreviewPage = pg.key;
        var head = el('div', 'b-vw-pview-head');
        head.appendChild(el('h3', 'b-h3', 'Seite ' + pg.name));
        head.appendChild(el('span', 'b-mono b-muted', pg.key));
        box.appendChild(head);
        if (pg.missing_type) { box.appendChild(el('div', 'b-callout b-callout--critical', 'Den Typ ' + pg.type + ' gibt es nicht.')); return box; }
        var title = el('div', 'b-vw-page-title');
        title.appendChild(el('span', 'b-h2', pg.node.name));
        title.appendChild(el('span', 'b-mono b-muted', pg.node.key + ' · ' + pg.node.type_name));
        box.appendChild(title);
        pg.sections.forEach(function (s) {
            var sec = el('div', 'b-vw-psec' + (s.empty && s.required ? ' is-missing' : ''));
            sec.dataset.viewsPreviewSlot = s.id;
            var sh = el('div', 'b-vw-psec-head');
            sh.appendChild(el('h4', 'b-vw-psec-title', s.title));
            if (s.empty) sh.appendChild(tag(s.required ? 'fehlt' : 'leer', s.required ? 'critical' : null));
            sec.appendChild(sh);
            if (s.text) sec.appendChild(el('p', 'b-small', s.text));
            if (s.pairs.length) {
                var dl = el('dl', 'b-dl');
                s.pairs.forEach(function (p) { dl.appendChild(el('dt', null, p.name)); dl.appendChild(el('dd', null, p.value)); });
                sec.appendChild(dl);
            }
            if (s.render === 'diagram' && s.edges.length) {
                var ul = el('ul', 'b-vw-edges');
                s.edges.forEach(function (e) { ul.appendChild(el('li', 'b-small', e.from + ' → ' + e.to + (e.label ? ' (' + e.label + ')' : ''))); });
                sec.appendChild(ul);
            } else if (s.render === 'table' && s.items.length) {
                var t = el('table', 'b-table b-table--compact');
                var hr = el('tr');
                hr.appendChild(el('th', null, 'Name'));
                (s.items[0].facts || []).forEach(function (f) { hr.appendChild(el('th', null, f.name)); });
                t.appendChild(hr);
                s.items.forEach(function (it) {
                    var tr = el('tr');
                    tr.appendChild(el('td', null, it.name));
                    it.facts.forEach(function (f) { tr.appendChild(el('td', null, f.value)); });
                    t.appendChild(tr);
                });
                sec.appendChild(t);
            } else if (s.items.length) {
                var items = el('ul', 'b-vw-pitems');
                s.items.forEach(function (it) {
                    var li = el('li', 'b-small');
                    li.appendChild(el('span', null, it.name));
                    if (it.status) li.appendChild(tag(it.status, it.status === 'offen' ? 'warning' : 'good'));
                    if (it.via) li.appendChild(el('span', 'b-muted', ' · ' + it.via));
                    items.appendChild(li);
                });
                sec.appendChild(items);
            }
            s.problems.forEach(function (p) { sec.appendChild(el('p', 'b-small b-error', p)); });
            box.appendChild(sec);
        });
        return box;
    }

    var previewToken = 0;
    function preview() {
        var token = ++previewToken;
        previewEl.textContent = '';
        var body = null;
        if (state.tab === 'profiles' && state.profile) body = { profile: state.profile, view: state.previewView, page: state.previewPage };
        if (state.tab === 'pages' && state.page) {
            var owner = data.profiles.filter(function (p) { return (p.definition.pages || []).indexOf(state.page) >= 0; })[0] || data.profiles[0];
            if (owner) body = { profile: owner.id, page: state.page, view: '-' };
        }
        if (!body) {
            previewEl.appendChild(el('p', 'b-muted', state.tab === 'pages' ? 'Wählen Sie eine Seite. Ohne Profil gibt es keine Vorschau.' : 'Wählen Sie ein Profil.'));
            return;
        }
        working(1);
        post(root.dataset.previewUrl, body).then(function (payload) {
            working(-1);
            if (token !== previewToken) return;
            if (!payload.success) { previewEl.appendChild(el('div', 'b-callout b-callout--critical', payload.error || 'Keine Vorschau.')); return; }
            var pv = payload.preview;
            if (state.tab === 'profiles') {
                var pick = el('div', 'b-vw-grid');
                if (pv.views.length) {
                    var vs = select('preview_view', pv.views.map(function (v, i) { return [v.id, (i === 0 ? 'Start: ' : '') + v.label]; }), pv.view && pv.view.id);
                    vs.addEventListener('change', function () { state.previewView = vs.value; preview(); });
                    pick.appendChild(field('Ansicht', vs));
                }
                if (pv.pages.length) {
                    var ps = select('preview_page', pv.pages.map(function (k) { return [k, k]; }), pv.page && pv.page.key);
                    ps.addEventListener('change', function () { state.previewPage = ps.value; preview(); });
                    pick.appendChild(field('Seite', ps));
                }
                previewEl.appendChild(pick);
                if (pv.view) previewEl.appendChild(drawView(pv.view));
                else previewEl.appendChild(el('p', 'b-muted', 'Das Profil hat noch keine Ansicht.'));
            }
            if (pv.page) previewEl.appendChild(drawPage(pv.page));
            else if (state.tab === 'profiles') previewEl.appendChild(el('p', 'b-muted', 'Das Profil zeigt noch keine Seite.'));
        });
    }

    /* ---- new ------------------------------------------------------------- */

    newButton.addEventListener('click', function () {
        if (state.tab === 'profiles') {
            openDialog('Neues Profil', 'Für wen: eine Zielgruppe mit ihren Ansichten und Seiten.', 'Profil anlegen', function (body) {
                var id = input('id', '', 'b-mono');
                id.placeholder = 'z. B. partner';
                var audience = input('audience', '');
                body.appendChild(field('Kennung', id, 'Klein geschrieben, Wörter mit Bindestrich.'));
                body.appendChild(field('Zielgruppe', audience));
                return function () {
                    return { op: 'profile.create', id: id.value.trim(), audience: audience.value.trim(),
                             after: function () { state.profile = id.value.trim(); render(); } };
                };
            });
        } else if (state.tab === 'pages') {
            openDialog('Neue Seite', 'Was ein Knoten eines Typs zeigt; sie beginnt mit seiner Beschreibung.', 'Seite anlegen', function (body) {
                var type = input('type', '', 'b-mono');
                type.setAttribute('list', 'b-vw-types');
                body.appendChild(field('Typ', type));
                return function () {
                    var t = type.value.trim();
                    return { op: 'page.create', type: t, after: function () {
                        var made = data.pages.filter(function (p) { return p.type === t; }).pop();
                        if (made) { state.page = made.key; render(); }
                    } };
                };
            });
        } else if (state.tab === 'spines') {
            openDialog('Neue Hierarchie', 'Ebenen von oben nach unten, etwa Firma → Bereich → Team.', 'Hierarchie anlegen', function (body) {
                var key = input('key', 'SPINE-', 'b-mono');
                var name = input('name', '');
                body.appendChild(field('Schlüssel', key, 'SPINE- und Kleinbuchstaben mit Bindestrich.'));
                body.appendChild(field('Name', name));
                return function () {
                    return { op: 'spine.create', key: key.value.trim(), name: name.value.trim(),
                             after: function () { state.spine = key.value.trim(); render(); } };
                };
            });
        }
    });

    /* ---- tabs and render ------------------------------------------------- */

    Array.prototype.forEach.call(document.querySelectorAll('[data-views-tab]'), function (tab) {
        tab.addEventListener('click', function () { state.tab = tab.dataset.viewsTab; render(); });
    });

    function render() {
        if (!state.profile && data.profiles.length) state.profile = data.profiles[0].id;
        if (state.profile && !currentProfile()) state.profile = data.profiles.length ? data.profiles[0].id : null;
        if (!state.page && data.pages.length) state.page = data.pages[0].key;
        if (state.page && !currentPage()) state.page = data.pages.length ? data.pages[0].key : null;
        if (!state.spine && data.spines.length) state.spine = data.spines[0].key;
        if (state.spine && !currentSpine()) state.spine = data.spines.length ? data.spines[0].key : null;
        Array.prototype.forEach.call(document.querySelectorAll('[data-views-tab]'), function (tab) {
            tab.setAttribute('aria-selected', String(tab.dataset.viewsTab === state.tab));
        });
        var withPreview = state.tab === 'profiles' || state.tab === 'pages';
        previewPane.hidden = !withPreview;
        layout.classList.toggle('b-vw-layout--wide', !withPreview);
        renderList();
        renderCheck();
        if (state.tab === 'profiles') renderProfile();
        else if (state.tab === 'pages') renderPage();
        else if (state.tab === 'spines') renderSpine();
        else renderRoles();
        document.querySelector('[data-views-undo]').disabled = !history.undo;
        document.querySelector('[data-views-redo]').disabled = !history.redo;
        if (withPreview) preview();
    }

    // A link from elsewhere (the Betrieb stage's tidiness list) opens one element: #page:PAGE-x, #spine:SPINE-x, #profile:id.
    var wanted = decodeURIComponent((window.location.hash || '').slice(1)).split(':');
    if (wanted.length === 2) {
        var tabOf = { page: 'pages', spine: 'spines', profile: 'profiles', roles: 'roles' };
        if (tabOf[wanted[0]]) state.tab = tabOf[wanted[0]];
        if (wanted[0] === 'page') state.page = wanted[1];
        if (wanted[0] === 'spine') state.spine = wanted[1];
        if (wanted[0] === 'profile') state.profile = wanted[1];
    }

    window.__builderViews = { data: function () { return data; }, state: state, render: render };
    render();
})();
