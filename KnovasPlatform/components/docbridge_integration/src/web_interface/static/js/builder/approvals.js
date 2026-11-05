/* Brain Builder -- «Freigaben» (connection layer MVP-27).
 *
 * «Annehmen» posts once; «Ablehnen» asks for the reason first. The row then shows the outcome the Brain answered
 * with, in German, and the page reloads to follow it.
 */
(function () {
    'use strict';

    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

    function post(url, body) {
        return fetch(url, {
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

    function decide(button, url, body) {
        var row = button.closest('tr');
        Array.prototype.forEach.call(row.querySelectorAll('button'), function (b) { b.disabled = true; });
        post(url, body).then(function (payload) {
            if (!payload.success) {
                toast('Nicht entschieden', payload.error, 'critical');
                Array.prototype.forEach.call(row.querySelectorAll('button'), function (b) { b.disabled = false; });
                return;
            }
            var status = row.querySelector('[data-proposal-status]');
            if (status) status.textContent = payload.status_text;
            toast(payload.status_text);
            setTimeout(function () { window.location.reload(); }, 1500);
        });
    }

    Array.prototype.forEach.call(document.querySelectorAll('[data-accept-url]'), function (button) {
        button.addEventListener('click', function () { decide(button, button.dataset.acceptUrl, {}); });
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-reject-url]'), function (button) {
        button.addEventListener('click', function () {
            var reason = window.prompt('Warum wird dieser Vorschlag abgelehnt?');
            if (reason === null) return;
            decide(button, button.dataset.rejectUrl, { reason: reason });
        });
    });
})();
