/* Brain Builder -- a company's sources (build plan step B7).
 *
 * #b-src-data (builder_routes._sources_view) holds the workspace's sources,
 * the tenant's registered components and the kinds. A source is created and
 * changed through the edit route (builder.source_edits), so it is part of the
 * draft and its review; its credential goes to the settings registry through
 * its own route and never comes back. "Testen" reaches the source through its
 * component and lists what it holds; "Stichprobe" shows up to 50 items of one
 * collection with the profile of their fields.
 */
(function () {
    'use strict';

    var dataEl = document.getElementById('b-src-data');
    var root = document.querySelector('[data-src-root]');
    if (!dataEl || !root) return;
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    var data = JSON.parse(dataEl.textContent);
    var current = null;
    var tested = {};     // source key -> the last test's result, while the page lives

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

    function url(key, action) {
        return root.dataset.baseUrl.replace('__KEY__/test', encodeURIComponent(key) + '/' + action);
    }

    function post(target, body) {
        return fetch(target, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'X-CSRF-Token': csrf, 'Accept': 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        }).then(function (r) {
            return r.json().catch(function () { return { success: false, error: 'Der Server hat nicht verständlich geantwortet.' }; });
        }, function () { return { success: false, error: 'Der Server ist nicht erreichbar.' }; });
    }

    function toast(title, text, tone) {
        if (window.BuilderUI && window.BuilderUI.toast) window.BuilderUI.toast({ title: title, text: text, tone: tone || 'good' });
    }

    function sourceByKey(key) {
        return data.sources.find(function (s) { return s.key === key; }) || null;
    }

    function load(next, select) {
        data = next;
        if (select) current = select;
        if (current && !sourceByKey(current)) current = null;
        if (!current && data.sources.length) current = data.sources[0].key;
        renderList();
        renderDetail();
    }

    /* ---- list ----------------------------------------------------------- */

    function renderList() {
        var list = document.querySelector('[data-src-list]');
        list.textContent = '';
        data.sources.forEach(function (s) {
            var li = el('li');
            var b = el('button', 'b-proc-item' + (s.key === current ? ' is-current' : ''));
            b.type = 'button';
            if (s.key === current) b.setAttribute('aria-current', 'true');
            b.appendChild(el('span', 'b-proc-item-name', s.name));
            var meta = el('span', 'b-proc-item-meta');
            meta.appendChild(el('span', null, s.kind_label));
            meta.appendChild(el('span', null, s.component_name ? 'über ' + s.component_name : 'Komponente fehlt'));
            b.appendChild(meta);
            if (s.credential_required && !s.credential_set) b.appendChild(tag('Zugang fehlt', 'warning'));
            b.addEventListener('click', function () { current = s.key; renderList(); renderDetail(); });
            li.appendChild(b);
            list.appendChild(li);
        });
        document.querySelector('[data-src-list-empty]').hidden = data.sources.length > 0;
    }

    /* ---- detail --------------------------------------------------------- */

    function dl(rows) {
        var list = el('dl', 'b-dl');
        rows.forEach(function (r) {
            list.appendChild(el('dt', null, r[0]));
            var dd = el('dd');
            if (r[1] instanceof Node) dd.appendChild(r[1]); else dd.textContent = r[1] || '–';
            list.appendChild(dd);
        });
        return list;
    }

    function renderDetail() {
        var box = document.querySelector('[data-src-detail]');
        box.textContent = '';
        var s = sourceByKey(current);
        if (!s) {
            var empty = el('section', 'b-empty');
            var text = el('div', 'b-empty-text');
            text.appendChild(el('h2', 'b-h3', 'Noch keine Quelle'));
            text.appendChild(el('p', 'b-muted', 'Fügen Sie eine Quelle hinzu: ein GitHub-Repository, einen Ordner der Dateiablage oder eine JSON-Schnittstelle.'));
            empty.appendChild(text);
            box.appendChild(empty);
            return;
        }
        var head = el('div', 'b-src-head');
        var title = el('div');
        title.appendChild(el('h2', 'b-h2', s.name));
        title.appendChild(el('span', 'b-mono b-muted b-small', s.key));
        head.appendChild(title);
        var tools = el('div', 'b-button-row');
        tools.appendChild(button('Testen', 'b-button--primary', function () { test(s); }));
        head.appendChild(tools);
        box.appendChild(head);

        var kind = data.kinds[s.kind] || { settings: [] };
        var rows = [['Art', s.kind_label], ['Komponente', s.component_name || 'nicht registriert'],
                    ['Verantwortlich', s.owner], ['Synchronisation', 'manuell']];
        kind.settings.forEach(function (f) { rows.push([f.label, (s.settings || {})[f.name]]); });
        box.appendChild(dl(rows));

        if (kind.credential) box.appendChild(credentialBox(s, kind));
        var result = el('div', 'b-src-result');
        result.setAttribute('data-src-result', '');
        box.appendChild(result);
        if (tested[s.key]) showTest(s, tested[s.key]);
    }

    function credentialBox(s, kind) {
        var box = el('section', 'b-src-credential');
        var h = el('h3', 'b-h3', 'Zugang');
        box.appendChild(h);
        var state = el('p', 'b-small');
        state.appendChild(s.credential_set ? tag('Gesetzt', 'good') : tag(kind.credential_required ? 'Fehlt' : 'Nicht gesetzt', kind.credential_required ? 'warning' : ''));
        state.appendChild(document.createTextNode(' ' + kind.credential + '. Wird nur geschrieben, nie angezeigt.'));
        box.appendChild(state);
        if (!data.secrets.available) { box.appendChild(el('p', 'b-small b-muted', data.secrets.reason)); return box; }
        var row = el('div', 'b-proc-inline');
        var input = el('input', 'b-input b-mono');
        input.type = 'password';
        input.autocomplete = 'off';
        input.setAttribute('aria-label', 'Neuer Zugang für ' + s.name);
        input.placeholder = s.credential_set ? 'Neuen Wert setzen' : 'Wert eingeben';
        row.appendChild(input);
        row.appendChild(button('Speichern', 'b-button--secondary', function () {
            if (!input.value.trim()) return;
            post(url(s.key, 'credential'), { value: input.value }).then(function (payload) {
                input.value = '';
                if (!payload.success) { toast(payload.error, null, 'critical'); return; }
                toast('Zugang gespeichert');
                load(payload.sources, s.key);
            });
        }));
        if (s.credential_set) {
            row.appendChild(button('Entfernen', 'b-button--quiet', function () {
                post(url(s.key, 'credential'), { value: '' }).then(function (payload) {
                    if (!payload.success) { toast(payload.error, null, 'critical'); return; }
                    load(payload.sources, s.key);
                });
            }));
        }
        box.appendChild(row);
        return box;
    }

    /* ---- test ----------------------------------------------------------- */

    function test(s) {
        var result = document.querySelector('[data-src-result]');
        result.textContent = '';
        result.appendChild(el('p', 'b-muted', 'Die Komponente liest die Quelle …'));
        post(url(s.key, 'test')).then(function (payload) {
            if (!payload.success) {
                result.textContent = '';
                var box = el('div', 'b-callout b-callout--critical');
                box.setAttribute('role', 'alert');
                box.appendChild(el('p', 'b-callout-title', 'Nicht erreicht'));
                box.appendChild(el('p', null, payload.error));
                result.appendChild(box);
                return;
            }
            tested[s.key] = payload.result;
            showTest(s, payload.result);
        });
    }

    function showTest(s, r) {
        var result = document.querySelector('[data-src-result]');
        result.textContent = '';
        var ok = el('p', 'b-src-ok');
        ok.appendChild(tag('Erreicht', 'good'));
        var detail = [];
        if (r.detail && r.detail.head) detail.push('Stand ' + r.detail.head);
        if (r.detail && r.detail.listing) detail.push('gelistet: ' + ({ cache: 'aus dem Zwischenspeicher', compare: 'nur die Änderungen', tree: 'vollständig' }[r.detail.listing] || r.detail.listing));
        if (r.detail && r.detail.truncated) detail.push('gekürzt');
        ok.appendChild(document.createTextNode(' über ' + (s.component_name || 'die Komponente') + (detail.length ? ' · ' + detail.join(' · ') : '')));
        result.appendChild(ok);
        var stats = el('div', 'b-src-stats');
        r.counts.forEach(function (pair) {
            var stat = el('div', 'b-src-stat');
            stat.appendChild(el('span', 'b-src-stat-value', String(pair[1])));
            stat.appendChild(el('span', 'b-src-stat-label', pair[0]));
            stats.appendChild(stat);
        });
        result.appendChild(stats);

        var wrap = el('div', 'b-table-wrap');
        var table = el('table', 'b-table b-table--compact');
        var thead = el('thead');
        var hr = el('tr');
        ['Sammlung', 'Art', 'Anzahl', ''].forEach(function (h, i) { var th = el('th', i === 2 ? 'is-num' : null, h); th.scope = 'col'; hr.appendChild(th); });
        thead.appendChild(hr);
        table.appendChild(thead);
        var body = el('tbody');
        r.collections.forEach(function (c) {
            var tr = el('tr');
            var name = el('td');
            name.appendChild(el('span', null, c.label));
            if (c.id !== c.label && c.id !== '*') { name.appendChild(el('br')); name.appendChild(el('span', 'b-mono b-small b-muted', c.id)); }
            tr.appendChild(name);
            tr.appendChild(el('td', null, c.kind));
            tr.appendChild(el('td', 'is-num', String(c.count)));
            var act = el('td');
            act.appendChild(button('Stichprobe', 'b-button--quiet b-button--small', function () { sample(s, c.id, c.label); }));
            act.appendChild(button('Zuordnen', 'b-button--quiet b-button--small', function () { newMapping(s, c); }));
            tr.appendChild(act);
            body.appendChild(tr);
        });
        table.appendChild(body);
        wrap.appendChild(table);
        result.appendChild(el('h3', 'b-h3', 'Was die Quelle hält'));
        result.appendChild(wrap);
        if (r.more) result.appendChild(el('p', 'b-small b-muted', r.more + ' weitere kleinere Sammlungen.'));
        if (s.kind === 'github') result.appendChild(ownSelection(s));
        var browser = el('section', 'b-src-sample');
        browser.setAttribute('data-src-sample', '');
        result.appendChild(browser);
    }

    function ownSelection(s) {
        var box = el('details', 'b-proc-new-criterion');
        box.appendChild(el('summary', null, 'Eigene Auswahl (Muster und Leser)'));
        var row = el('div', 'b-proc-inline');
        var pattern = el('input', 'b-input b-mono');
        pattern.placeholder = 'z. B. docs/**/*.yaml';
        pattern.setAttribute('aria-label', 'Dateimuster');
        var reader = el('select', 'b-select');
        reader.setAttribute('aria-label', 'Leser');
        Object.keys(data.extractors).forEach(function (k) { var o = el('option', null, data.extractors[k]); o.value = k; reader.appendChild(o); });
        row.appendChild(pattern);
        row.appendChild(reader);
        row.appendChild(button('Stichprobe', 'b-button--secondary', function () {
            if (pattern.value.trim()) sample(s, reader.value + ':' + pattern.value.trim(), pattern.value.trim());
        }));
        box.appendChild(row);
        return box;
    }

    /* ---- sample browser ------------------------------------------------- */

    function sample(s, collection, label) {
        var box = document.querySelector('[data-src-sample]');
        box.textContent = '';
        box.appendChild(el('p', 'b-muted', 'Stichprobe wird gelesen …'));
        post(url(s.key, 'sample'), { collection: collection }).then(function (payload) {
            box.textContent = '';
            if (!payload.success) {
                var err = el('div', 'b-callout b-callout--critical');
                err.appendChild(el('p', null, payload.error));
                box.appendChild(err);
                return;
            }
            showSample(box, label, payload.profile);
            box.scrollIntoView({ block: 'start', behavior: 'smooth' });
        });
    }

    function showSample(box, label, p) {
        box.appendChild(el('h3', 'b-h3', 'Stichprobe: ' + label));
        box.appendChild(el('p', 'b-small b-muted', p.count + (p.count === 1 ? ' Eintrag' : ' Einträge') + ' (höchstens 50) · ' +
            (p.fields.length + p.more_fields) + ' Felder' + (p.more_fields ? ', die ' + p.fields.length + ' am meisten gefüllten gezeigt' : '')));
        var wrap = el('div', 'b-table-wrap');
        var table = el('table', 'b-table b-table--compact b-src-profile');
        var thead = el('thead');
        var hr = el('tr');
        ['Feld', 'Typ', 'Leer', 'Verschieden', 'Beispiele'].forEach(function (h, i) {
            var th = el('th', i === 2 || i === 3 ? 'is-num' : null, h); th.scope = 'col'; hr.appendChild(th);
        });
        thead.appendChild(hr);
        table.appendChild(thead);
        var body = el('tbody');
        p.fields.forEach(function (f) {
            var tr = el('tr');
            tr.appendChild(el('td', 'b-mono', f.name));
            tr.appendChild(el('td', null, f.type));
            var empty = el('td', 'is-num');
            var bar = el('span', 'b-src-bar');
            var fill = el('span', 'b-src-bar-fill');
            fill.style.width = Math.round(f.empty_share * 100) + '%';
            bar.appendChild(fill);
            empty.appendChild(bar);
            empty.appendChild(document.createTextNode(' ' + Math.round(f.empty_share * 100) + ' %'));
            tr.appendChild(empty);
            tr.appendChild(el('td', 'is-num', String(f.distinct)));
            tr.appendChild(el('td', 'b-small', f.examples.join(' · ')));
            body.appendChild(tr);
        });
        table.appendChild(body);
        wrap.appendChild(table);
        box.appendChild(wrap);

        var details = el('details', 'b-src-rows');
        details.appendChild(el('summary', null, 'Einträge zeigen'));
        var rowsWrap = el('div', 'b-table-wrap');
        var rows = el('table', 'b-table b-table--compact');
        var names = p.fields.map(function (f) { return f.name; });
        var rh = el('tr');
        names.forEach(function (n) { var th = el('th', 'b-mono', n); th.scope = 'col'; rh.appendChild(th); });
        var rhead = el('thead'); rhead.appendChild(rh); rows.appendChild(rhead);
        var rbody = el('tbody');
        p.rows.forEach(function (row) {
            var tr = el('tr');
            names.forEach(function (n) { tr.appendChild(el('td', 'b-small', row[n])); });
            rbody.appendChild(tr);
        });
        rows.appendChild(rbody);
        rowsWrap.appendChild(rows);
        details.appendChild(rowsWrap);
        box.appendChild(details);
    }

    /* ---- new mapping (step B8) ----------------------------------------- */

    var mapDialog = document.getElementById('b-map-new');
    var mapFor = null;
    function newMapping(s, c) {
        mapFor = { source: s.key, collection: c.id };
        var f = mapDialog.querySelector('form');
        f.reset();
        mapDialog.querySelector('[data-ed-error]').hidden = true;
        mapDialog.querySelector('[data-mn-what]').textContent = s.name + ': ' + c.label;
        mapDialog.showModal();
        f.elements.id.focus();
    }
    Array.prototype.forEach.call(mapDialog.querySelectorAll('[data-ed-close]'), function (b) {
        b.addEventListener('click', function () { mapDialog.close(); });
    });
    mapDialog.querySelector('form').addEventListener('submit', function (e) {
        e.preventDefault();
        var f = e.target;
        var id = f.elements.id.value.trim();
        post(root.dataset.editUrl, { op: 'mapping.create', id: id, type: f.elements.type.value,
                                     source: mapFor.source, collection: mapFor.collection })
            .then(function (payload) {
                var box = mapDialog.querySelector('[data-ed-error]');
                if (!payload.success) { box.textContent = payload.error; box.hidden = false; return; }
                var made = payload.sources.mappings.find(function (m) { return m.id === id; });
                window.location.href = made.url;
            });
    });

    /* ---- new source ----------------------------------------------------- */

    var dialog = document.getElementById('b-src-new');
    var form = dialog.querySelector('form');
    var error = dialog.querySelector('[data-ed-error]');

    function chosenKind() {
        var r = form.querySelector('input[name="kind"]:checked');
        return r ? r.value : '';
    }

    function syncKind() {
        var kind = data.kinds[chosenKind()];
        var select = form.elements.component;
        select.textContent = '';
        var fitting = data.components.filter(function (c) { return kind && (kind.components || [kind.component]).indexOf(c.kind) !== -1; });
        fitting.forEach(function (c) { var o = el('option', null, c.name + (c.site ? ' (' + c.site + ')' : '')); o.value = c.id; select.appendChild(o); });
        dialog.querySelector('[data-sn-component-hint]').textContent = fitting.length ? ''
            : 'Für diese Art ist keine Komponente registriert (Stufe Verbinden).';
        var box = dialog.querySelector('[data-sn-settings]');
        box.textContent = '';
        (kind ? kind.settings : []).forEach(function (f) {
            var field = el('div', 'b-field');
            var input = el('input', 'b-input b-mono');
            input.name = 'setting_' + f.name;
            input.id = 'b-sn-' + f.name;
            input.placeholder = f.hint;
            input.autocomplete = 'off';
            input.spellcheck = false;
            var label = el('label', 'b-label', f.label);
            label.htmlFor = input.id;
            field.appendChild(label);
            field.appendChild(input);
            box.appendChild(field);
        });
    }

    document.querySelector('[data-src-new]').addEventListener('click', function () {
        form.reset();
        error.hidden = true;
        syncKind();
        dialog.showModal();
        form.elements.name.focus();
    });
    Array.prototype.forEach.call(form.querySelectorAll('input[name="kind"]'), function (r) { r.addEventListener('change', syncKind); });
    Array.prototype.forEach.call(dialog.querySelectorAll('[data-ed-close]'), function (b) {
        b.addEventListener('click', function () { dialog.close(); });
    });
    form.addEventListener('submit', function (e) {
        e.preventDefault();
        var kind = chosenKind();
        var settings = {};
        (data.kinds[kind] ? data.kinds[kind].settings : []).forEach(function (f) {
            settings[f.name] = form.elements['setting_' + f.name].value;
        });
        post(root.dataset.editUrl, { op: 'source.create', kind: kind, name: form.elements.name.value,
                                     owner: form.elements.owner.value, component: form.elements.component.value,
                                     settings: settings })
            .then(function (payload) {
                if (!payload.success) { error.textContent = payload.error; error.hidden = false; return; }
                dialog.close();
                var made = payload.sources.sources[payload.sources.sources.length - 1];
                load(payload.sources, made ? made.key : null);
            });
    });

    load(data);
    document.documentElement.dataset.srcReady = 'true';
})();
