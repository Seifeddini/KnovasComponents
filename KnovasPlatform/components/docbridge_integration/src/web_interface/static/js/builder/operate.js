/* Brain Builder -- operating a published Brain (build plan step B10).
 *
 * The Betrieb stage is drawn on the server; this records that the owner has
 * reviewed a type's health ("Geprüft"), and reloads so the next review shows.
 */
(function () {
    'use strict';

    var root = document.querySelector('[data-op-root]');
    if (!root) return;
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

    function toast(title, text, tone) {
        if (window.BuilderUI && window.BuilderUI.toast) window.BuilderUI.toast({ title: title, text: text, tone: tone || 'good' });
    }

    Array.prototype.forEach.call(document.querySelectorAll('[data-op-review]'), function (button) {
        button.addEventListener('click', function () {
            button.disabled = true;
            fetch(root.dataset.reviewUrl, {
                method: 'POST', credentials: 'same-origin',
                headers: { 'X-CSRF-Token': csrf, 'Accept': 'application/json', 'Content-Type': 'application/json' },
                body: JSON.stringify({ type: button.dataset.opReview }),
            }).then(function (r) { return r.json(); }).then(function (payload) {
                if (!payload.success) {
                    button.disabled = false;
                    toast(payload.error || 'Das hat nicht geklappt.', '', 'critical');
                    return;
                }
                window.location.reload();
            }, function () {
                button.disabled = false;
                toast('Der Server ist nicht erreichbar.', '', 'critical');
            });
        });
    });
})();
