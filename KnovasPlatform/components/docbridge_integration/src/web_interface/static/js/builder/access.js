/* Brain Builder -- the access window (build plan step B14).
 *
 * Every structural change -- a new group, a move (drag a group onto another, or "Hängt unter"), a deletion --
 * is first previewed: the server checks the engine's limits and says who gains or loses sight of how many
 * items. Only after "Bestätigen" is it sent, with confirmed: true. Members, folder rules, "explain access" and
 * the enforcement request go straight to the server, which decides who may.
 */
(function () {
    'use strict';

    var root = document.querySelector('[data-ax]');
    if (!root) return;
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    var result = document.querySelector('[data-ax-result]');
    var detail = document.querySelector('[data-ax-detail]');
    var current = detail ? detail.dataset.axDetail : null;

    function post(url, body) {
        return fetch(url, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'X-CSRF-Token': csrf, 'Accept': 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        }).then(function (r) {
            return r.json().catch(function () { return { success: false, error: 'Der Server hat nicht verständlich geantwortet.' }; });
        }, function () { return { success: false, error: 'Der Server ist nicht erreichbar.' }; });
    }
    function say(text, tone) {
        if (!result) return;
        result.textContent = text;
        result.className = 'b-callout b-callout--' + (tone || 'good');
        result.hidden = false;
    }
    function el(tagName, className, text) {
        var node = document.createElement(tagName);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }
    function reload(group) {
        var url = new URL(window.location.href);
        if (group) url.searchParams.set('gruppe', group);
        window.location.href = url.toString();
    }

    /* ---- preview, then confirm ------------------------------------------------ */

    var dialog = document.getElementById('b-ax-impact');
    var pending = null;

    function preview(change) {
        post(root.dataset.previewUrl, change).then(function (r) {
            if (!r.success) { say(r.error || 'Das geht so nicht.', 'critical'); return; }
            var imp = r.impact;
            pending = change;
            dialog.querySelector('[data-ax-impact-what]').textContent = imp.what + '.';
            var body = dialog.querySelector('[data-ax-impact-body]');
            body.textContent = '';
            if (imp.nobody) body.appendChild(el('p', null, 'Niemand gewinnt oder verliert Sicht.'));
            [['gains', 'Sehen künftig mehr', 'good'], ['losses', 'Sehen künftig weniger', 'critical']].forEach(function (k) {
                if (!imp[k[0]].length) return;
                body.appendChild(el('h3', 'b-h3', k[1]));
                var ul = el('ul', 'b-ax-impact b-ax-impact--' + k[2]);
                ul.dataset.axImpact = k[0];
                imp[k[0]].forEach(function (x) { ul.appendChild(el('li', null, x.name + ': ' + x.items + (x.items === 1 ? ' Eintrag' : ' Einträge'))); });
                body.appendChild(ul);
            });
            if (imp.items) body.appendChild(el('p', 'b-small b-muted', imp.items + ' zugewiesene Einträge hängen an dieser Gruppe und ihren Untergruppen.'));
            dialog.querySelector('[data-ax-impact-error]').hidden = true;
            dialog.showModal();
        });
    }
    if (dialog) {
        dialog.querySelector('[data-ax-impact-cancel]').addEventListener('click', function () { dialog.close(); pending = null; });
        dialog.querySelector('[data-ax-impact-go]').addEventListener('click', function () {
            if (!pending) return;
            var change = Object.assign({ confirmed: true }, pending);
            post(root.dataset.applyUrl, change).then(function (r) {
                if (!r.success) {
                    var box = dialog.querySelector('[data-ax-impact-error]');
                    box.textContent = r.error || 'Das hat nicht geklappt.';
                    box.hidden = false;
                    return;
                }
                dialog.close();
                reload(change.op === 'create' ? r.group : (change.op === 'delete' ? null : change.group));
            });
        });
    }

    /* ---- the tree: drag a group onto its new parent ---------------------------- */

    var dragged = null;
    Array.prototype.forEach.call(document.querySelectorAll('[data-ax-group]'), function (row) {
        row.addEventListener('dragstart', function (e) {
            dragged = row.dataset.axGroup;
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', dragged);
            row.classList.add('is-dragged');
        });
        row.addEventListener('dragend', function () { row.classList.remove('is-dragged'); });
        row.addEventListener('dragover', function (e) {
            if (dragged && dragged !== row.dataset.axGroup) { e.preventDefault(); row.classList.add('is-over'); }
        });
        row.addEventListener('dragleave', function () { row.classList.remove('is-over'); });
        row.addEventListener('drop', function (e) {
            e.preventDefault();
            row.classList.remove('is-over');
            var group = dragged || e.dataTransfer.getData('text/plain');
            dragged = null;
            if (group && group !== row.dataset.axGroup) preview({ op: 'move', group: group, parent: row.dataset.axGroup });
        });
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-ax-toggle]'), function (b) {
        b.addEventListener('click', function () {
            var li = b.closest('li');
            var open = li.getAttribute('aria-expanded') !== 'false';
            li.setAttribute('aria-expanded', String(!open));
            b.textContent = open ? '▸' : '▾';
        });
    });

    /* ---- the chosen group ------------------------------------------------------- */

    function onSubmit(selector, fn) {
        var form = document.querySelector(selector);
        if (form) form.addEventListener('submit', function (e) { e.preventDefault(); fn(form); });
    }
    onSubmit('[data-ax-create]', function (f) { preview({ op: 'create', parent: current, name: f.elements.name.value }); });
    onSubmit('[data-ax-move]', function (f) { preview({ op: 'move', group: current, parent: f.elements.parent.value }); });
    onSubmit('[data-ax-rename]', function (f) {
        post(root.dataset.applyUrl, { op: 'rename', group: current, name: f.elements.name.value, confirmed: true })
            .then(function (r) { if (r.success) reload(current); else say(r.error, 'critical'); });
    });
    var del = document.querySelector('[data-ax-delete]');
    if (del) del.addEventListener('click', function () { preview({ op: 'delete', group: current }); });

    onSubmit('[data-ax-member-add]', function (f) {
        post(root.dataset.membersUrl, { group: current, user: f.elements.user.value, ends_on: f.elements.ends_on.value })
            .then(function (r) { if (r.success) reload(current); else say(r.error, 'critical'); });
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-ax-member-remove]'), function (b) {
        b.addEventListener('click', function () {
            post(root.dataset.membersUrl, { group: current, user: b.dataset.axMemberRemove, remove: true })
                .then(function (r) { if (r.success) reload(current); else say(r.error, 'critical'); });
        });
    });
    onSubmit('[data-ax-rule-add]', function (f) {
        post(root.dataset.rulesUrl, { group: current, prefix: f.elements.prefix.value })
            .then(function (r) { if (r.success) reload(current); else say(r.error, 'critical'); });
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-ax-rule-remove]'), function (b) {
        b.addEventListener('click', function () {
            post(root.dataset.ruleRemoveUrl, { rule: b.dataset.axRuleRemove })
                .then(function (r) { if (r.success) reload(current); else say(r.error, 'critical'); });
        });
    });

    /* ---- explain access, the enforcement posture -------------------------------- */

    onSubmit('[data-ax-explain]', function (f) {
        var box = document.querySelector('[data-ax-explain-result]');
        post(root.dataset.explainUrl, { user: f.elements.user.value, pointer: f.elements.pointer.value }).then(function (r) {
            box.hidden = false;
            if (!r.success) { box.className = 'b-callout b-callout--critical'; box.textContent = r.error; return; }
            var a = r.answer;
            box.className = 'b-callout b-callout--' + (a.readable === false ? 'warning' : a.readable ? 'good' : '');
            box.textContent = '';
            box.appendChild(el('p', 'b-callout-title', a.readable === false ? 'Sieht es nicht' : a.readable ? 'Sieht es' : 'Unbekannt'));
            box.appendChild(el('p', null, a.text));
            if (a.path.length) box.appendChild(el('p', 'b-small b-muted', 'Weg: ' + a.path.join(' → ')));
        });
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-ax-posture]'), function (b) {
        b.addEventListener('click', function () {
            post(root.dataset.postureUrl, { mode: b.dataset.axPosture }).then(function (r) {
                if (r.success) reload(current); else say(r.error, 'critical');
            });
        });
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-ax-posture-approve]'), function (b) {
        b.addEventListener('click', function () {
            post(root.dataset.postureApproveUrl.replace('REQ', encodeURIComponent(b.dataset.axPostureApprove))).then(function (r) {
                if (r.success) reload(current); else say(r.error, 'critical');
            });
        });
    });
})();
