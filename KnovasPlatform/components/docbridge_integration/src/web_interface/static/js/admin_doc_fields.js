/* Dokumentfelder tab: small conveniences over server-rendered forms.
 *
 * - "Neues Feld": show the inputs that belong to the chosen type only.
 * - Auswahlwerte: "Weitere Zeile" adds an empty choice row.
 * - Folder rules: each value input follows its field's type (a select for a
 *   single choice or yes/no, text otherwise).
 * - Folder picker: one level of folders per expand from the Knovas Connector
 *   (/admin/ingestion/folders, the same route the Ingestion tab uses).
 *
 * Every decision that matters is made on the server; without this script
 * the forms still work. Names, labels and paths come from Knovas and the
 * file server, so everything is built with DOM methods and textContent,
 * never parsed as HTML.
 */
(function () {
    'use strict';

    // -- "Neues Feld": inputs per type --------------------------------------
    var datatype = document.querySelector('[data-df-datatype]');
    if (datatype) {
        var form = datatype.form;
        var syncTypeInputs = function () {
            var chosen = datatype.value;
            form.querySelectorAll('[data-df-only]').forEach(function (el) {
                var types = String(el.getAttribute('data-df-only') || '').split(' ');
                el.hidden = types.indexOf(chosen) === -1;
            });
        };
        datatype.addEventListener('change', syncTypeInputs);
        syncTypeInputs();
    }

    // -- Auswahlwerte: eine Zeile je Auswahl; "Weitere Zeile" hängt eine an --
    // Die neue Zeile ist eine geleerte Kopie der letzten, mit der nächsten
    // Nummer in den Namen (choice_code_<n>, ...). Ohne dieses Skript kommen
    // weitere leere Zeilen nach dem Speichern.
    document.querySelectorAll('[data-df-choices]').forEach(function (box) {
        var add = box.querySelector('[data-df-choice-add]');
        if (!add) { return; }
        add.addEventListener('click', function () {
            var rows = box.querySelectorAll('[data-df-choice-row]');
            var last = rows[rows.length - 1];
            if (!last) { return; }
            var next = 0;
            box.querySelectorAll('input[name^="choice_code_"]').forEach(function (input) {
                var n = parseInt(String(input.name).slice('choice_code_'.length), 10);
                if (!isNaN(n) && n >= next) { next = n + 1; }
            });
            var row = last.cloneNode(true);
            row.querySelectorAll('input').forEach(function (input) {
                input.value = '';
                input.name = String(input.name).replace(/_\d+$/, '_' + next);
            });
            last.parentNode.insertBefore(row, last.nextSibling);
            var first = row.querySelector('input');
            if (first) { first.focus(); }
        });
    });

    // -- Folder rules: typed value inputs ------------------------------------
    var registry = [];
    var registryEl = document.getElementById('doc-fields-registry');
    if (registryEl) {
        try { registry = JSON.parse(registryEl.textContent || '[]') || []; } catch (e) { registry = []; }
    }
    var byKey = {};
    registry.forEach(function (spec) { if (spec && spec.key) { byKey[spec.key] = spec; } });

    var PLACEHOLDERS = {
        date: 'z. B. 15.03.2024, März 2024, Q1 2024',
        period: 'z. B. GJ 2024, 2024',
        money: "z. B. CHF 1'234.50",
        number: "z. B. 1'234.5",
        entity_ref: 'Name, z. B. Muster AG',
        code: 'Kennung',
        text: 'Text'
    };

    function option(value, label, selected) {
        var opt = document.createElement('option');
        opt.value = value;
        opt.textContent = label;
        if (selected) { opt.selected = true; }
        return opt;
    }

    function valueControl(spec, name, current) {
        var one = !spec || spec.cardinality !== 'many';
        if (spec && one && spec.datatype === 'enum') {
            var select = document.createElement('select');
            select.name = name;
            select.setAttribute('data-df-rule-value', '');
            select.appendChild(option('', '—', !current));
            (spec.enum || []).forEach(function (item) {
                select.appendChild(option(String(item.code), String(item.label || item.code),
                    current === String(item.code)));
            });
            return select;
        }
        if (spec && one && spec.datatype === 'bool') {
            var yesNo = document.createElement('select');
            yesNo.name = name;
            yesNo.setAttribute('data-df-rule-value', '');
            yesNo.appendChild(option('', '—', !current));
            yesNo.appendChild(option('true', 'Ja', current === 'true'));
            yesNo.appendChild(option('false', 'Nein', current === 'false'));
            return yesNo;
        }
        var input = document.createElement('input');
        input.type = 'text';
        input.name = name;
        input.autocomplete = 'off';
        input.setAttribute('data-df-rule-value', '');
        input.value = current || '';
        var hint = spec ? (PLACEHOLDERS[spec.datatype] || '') : '';
        if (spec && !one) { hint = (hint ? hint + ' — ' : '') + 'mehrere mit ; trennen'; }
        input.placeholder = hint;
        return input;
    }

    document.querySelectorAll('[data-df-rule-row]').forEach(function (row) {
        var keySelect = row.querySelector('[data-df-rule-key]');
        if (!keySelect) { return; }
        var sync = function (keepValue) {
            var old = row.querySelector('[data-df-rule-value]');
            if (!old) { return; }
            var replacement = valueControl(byKey[keySelect.value], old.name,
                keepValue ? String(old.value || '') : '');
            old.parentNode.replaceChild(replacement, old);
        };
        keySelect.addEventListener('change', function () { sync(false); });
        sync(true);
    });

    // -- Folder picker ---------------------------------------------------------
    var picker = document.querySelector('.doc-fields-folder-picker');
    if (!picker) { return; }
    var endpoint = picker.getAttribute('data-endpoint');
    var hidden = picker.querySelector('[data-df-folder-path]');
    var shown = picker.querySelector('[data-df-folder-shown]');
    var ruleForm = picker.closest('form');
    var manual = ruleForm ? ruleForm.querySelector('input[name="pointer_prefix"]') : null;

    function node(path, name) {
        var li = document.createElement('li');
        li.className = 'folder-node';
        li.setAttribute('data-path', path);
        var toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'folder-toggle';
        toggle.setAttribute('aria-expanded', 'false');
        toggle.setAttribute('data-df-expand', '');
        toggle.textContent = '+';
        var label = document.createElement('span');
        label.className = 'folder-name';
        label.textContent = name;
        var pick = document.createElement('button');
        pick.type = 'button';
        pick.className = 'folder-add';
        pick.setAttribute('data-df-pick', '');
        pick.textContent = 'wählen';
        li.appendChild(toggle);
        li.appendChild(label);
        li.appendChild(pick);
        return li;
    }

    function expand(li, toggle) {
        if (toggle.getAttribute('aria-expanded') === 'true') {
            var open = li.querySelector('ul');
            if (open) { li.removeChild(open); }
            toggle.setAttribute('aria-expanded', 'false');
            toggle.textContent = '+';
            return;
        }
        var url = new URL(endpoint, window.location.origin);
        url.searchParams.set('root', li.getAttribute('data-path'));
        toggle.disabled = true;
        fetch(url.toString(), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json().catch(function () { return {}; }); })
            .then(function (body) {
                var list = document.createElement('ul');
                (body.folders || []).forEach(function (f) {
                    list.appendChild(node(String(f.path || ''), String(f.name || f.path || '')));
                });
                if (!(body.folders || []).length) {
                    var empty = document.createElement('li');
                    empty.className = 'hint';
                    empty.textContent = body.error ? 'Ordner nicht abrufbar.' : 'Keine Unterordner.';
                    list.appendChild(empty);
                }
                li.appendChild(list);
                toggle.setAttribute('aria-expanded', 'true');
                toggle.textContent = '–';
            })
            .catch(function () {
                if (shown) { shown.textContent = 'Ordner nicht abrufbar.'; }
            })
            .finally(function () { toggle.disabled = false; });
    }

    picker.addEventListener('click', function (event) {
        var target = event.target;
        var li = target.closest('.folder-node');
        if (!li) { return; }
        if (target.hasAttribute('data-df-expand')) {
            expand(li, target);
        } else if (target.hasAttribute('data-df-pick')) {
            var path = li.getAttribute('data-path') || '';
            if (hidden) { hidden.value = path; }
            if (shown) { shown.textContent = path || '—'; }
            if (manual) { manual.value = ''; }
        }
    });

    if (manual) {
        manual.addEventListener('input', function () {
            // A typed prefix replaces a picked folder: one source of truth.
            if (manual.value && hidden) {
                hidden.value = '';
                if (shown) { shown.textContent = '—'; }
            }
        });
    }
}());
