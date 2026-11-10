/* Brain Builder -- one sync component's actions (build plan step B13): run now (a dry run, or an apply
 * after confirming), pause and resume, a new enrolment token, revoke, the schedule (also «bei neuen Commits»), its
 * details, and the held plans (approve with a reason, or reject). The «automatisch anwenden» switches are site.js's. The server decides who may; the page reloads to show it. */
(function () {
    'use strict';

    var root = document.querySelector('[data-cp]');
    if (!root) return;
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    var result = document.querySelector('[data-cp-result]');

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
        result.textContent = text;
        result.className = 'b-callout b-callout--' + (tone || 'good');
        result.hidden = false;
    }
    function act(url, body, done) {
        return post(url, body).then(function (r) {
            if (!r.success) { say(r.error || 'Das hat nicht geklappt.', 'critical'); return r; }
            if (done) done(r); else window.location.reload();
            return r;
        });
    }
    /* The address as the browser reached it, port included: behind nginx the server does not know its port. */
    Array.prototype.forEach.call(document.querySelectorAll('[data-protocol-address]'), function (el) {
        el.textContent = window.location.origin + el.dataset.protocolPath;
    });
    function on(selector, fn) {
        Array.prototype.forEach.call(document.querySelectorAll(selector), function (el) { el.addEventListener('click', function () { fn(el); }); });
    }

    var confirmBox = document.getElementById('b-cp-confirm');
    on('[data-cp-check]', function () {
        act(root.dataset.checkUrl, {});
    });
    on('[data-cp-run]', function (b) {
        if (b.dataset.cpRun === 'apply') { confirmBox.showModal(); return; }
        act(root.dataset.runUrl, { kind: 'dry_run' }, function (r) {
            say(r.queued ? 'Probelauf angefordert: die Komponente holt ihn beim nächsten Herzschlag ab.' : 'Probelauf gestartet.');
        });
    });
    on('[data-cp-confirm-cancel]', function () { confirmBox.close(); });
    on('[data-cp-confirm-go]', function () {
        confirmBox.close();
        act(root.dataset.runUrl, { kind: 'apply' }, function (r) {
            say(r.queued ? 'Anwenden angefordert: die Komponente holt es beim nächsten Herzschlag ab.' : 'Anwenden gestartet.');
        });
    });
    on('[data-cp-pause]', function () { act(root.dataset.pauseUrl); });
    on('[data-cp-resume]', function () { act(root.dataset.resumeUrl); });
    on('[data-cp-revoke]', function () { act(root.dataset.revokeUrl); });
    on('[data-cp-delete]', function () {
        if (!window.confirm('Diese Komponente und ihre Läufe löschen? Das lässt sich nicht rückgängig machen.')) return;
        act(root.dataset.deleteUrl, {}, function (r) { window.location.href = r.url; });
    });
    on('[data-cp-rotate]', function () {
        act(root.dataset.rotateUrl, {}, function (r) {
            var box = document.querySelector('[data-cp-token-box]');
            box.querySelector('[data-cp-token]').value = r.token;
            box.hidden = false;
            box.querySelector('[data-cp-token]').select();
        });
    });
    on('[data-cp-approve]', function (b) {
        var reason = b.parentNode.querySelector('[data-cp-reason]').value;
        // The GitHub runner releases every mapping its dry run held: the page names them, the person confirms all.
        var together = (b.dataset.cpTogether || '').split(',').filter(Boolean);
        act(root.dataset.planUrl.replace('PLAN', encodeURIComponent(b.dataset.cpApprove)),
            { approve: true, reason: reason, confirm_mappings: together });
    });
    on('[data-cp-reject]', function (b) {
        var reason = b.parentNode.querySelector('[data-cp-reason]').value;
        act(root.dataset.planUrl.replace('PLAN', encodeURIComponent(b.dataset.cpReject)), { approve: false, reason: reason });
    });

    var schedule = document.querySelector('[data-cp-schedule]');
    if (schedule) {
        function mode() { var m = schedule.querySelector('input[name="mode"]:checked'); return m ? m.value : 'manual'; }
        function sync() {
            Array.prototype.forEach.call(schedule.querySelectorAll('[data-cp-window]'), function (el) { el.hidden = mode() !== 'window'; });
            schedule.elements.every_minutes.closest('.b-field').hidden = mode() === 'manual' || mode() === 'commits';
            Array.prototype.forEach.call(schedule.querySelectorAll('[data-cp-commits]'), function (el) { el.hidden = mode() !== 'commits'; });
        }
        Array.prototype.forEach.call(schedule.querySelectorAll('input[name="mode"]'), function (r) { r.addEventListener('change', sync); });
        sync();
        schedule.addEventListener('submit', function (event) {
            event.preventDefault();
            var days = Array.prototype.filter.call(schedule.querySelectorAll('input[name="days"]'), function (c) { return c.checked; })
                .map(function (c) { return c.value; });
            act(root.dataset.scheduleUrl, { schedule: { mode: mode(), every_minutes: Number(schedule.elements.every_minutes.value),
                                                        poll_seconds: Number(schedule.elements.poll_seconds.value),
                                                        days: days, from: schedule.elements.from.value, to: schedule.elements.to.value } },
                function (r) {
                    document.querySelector('[data-cp-schedule-text]').textContent = r.text;
                    say('Zeitplan übernommen: ' + r.text + '.');
                });
        });
    }
    var settings = document.querySelector('[data-cp-settings]');
    if (settings) {
        settings.addEventListener('submit', function (event) {
            event.preventDefault();
            act(root.dataset.settingsUrl, { name: settings.elements.name.value, site: settings.elements.site.value,
                                            owner: settings.elements.owner.value });
        });
    }
})();
