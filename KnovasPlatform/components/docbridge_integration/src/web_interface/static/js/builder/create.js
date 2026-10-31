/* Brain Builder -- the new-workspace dialog (stage 1).
 *
 * One field in a native <dialog>: the company's name. POST data-create-url
 * creates the workspace with an empty model and answers with its URL.
 */
(function () {
    'use strict';

    var dialog = document.getElementById('b-create');
    if (!dialog) return;

    var form = dialog.querySelector('[data-create-form]');
    var nameInput = dialog.querySelector('#b-create-name');
    var errorBox = dialog.querySelector('[data-create-error]');
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    var lastOpener = null;

    function setError(message) {
        errorBox.textContent = message || '';
        errorBox.hidden = !message;
    }

    function busy(on) {
        Array.prototype.forEach.call(form.querySelectorAll('button'), function (b) { b.disabled = on; });
        form.setAttribute('aria-busy', String(on));
    }

    Array.prototype.forEach.call(document.querySelectorAll('[data-create-open]'), function (button) {
        button.addEventListener('click', function () {
            if (button.getAttribute('aria-disabled') === 'true') return;
            lastOpener = button;
            form.reset();
            setError(null);
            busy(false);
            dialog.showModal();
            nameInput.focus();
        });
    });

    Array.prototype.forEach.call(dialog.querySelectorAll('[data-create-close]'), function (button) {
        button.addEventListener('click', function () { dialog.close(); });
    });
    dialog.addEventListener('close', function () { if (lastOpener) lastOpener.focus(); });
    nameInput.addEventListener('input', function () { setError(null); });

    form.addEventListener('submit', function (event) {
        event.preventDefault();
        var name = nameInput.value.trim();
        if (!name) {
            setError('Bitte einen Namen eingeben.');
            nameInput.focus();
            return;
        }
        setError(null);
        busy(true);
        var body = new FormData();
        body.append('name', name);
        fetch(dialog.getAttribute('data-create-url'), {
            method: 'POST', body: body, credentials: 'same-origin',
            headers: { 'X-CSRF-Token': csrf, 'Accept': 'application/json' }
        }).then(function (response) {
            return response.json().catch(function () {
                return { success: false, error: 'Der Server hat nicht verständlich geantwortet.' };
            });
        }, function () {
            return { success: false, error: 'Der Server ist nicht erreichbar.' };
        }).then(function (payload) {
            if (!payload.success) { busy(false); setError(payload.error || 'Das hat nicht geklappt.'); return; }
            window.location.assign(payload.url);
        });
    });
})();
