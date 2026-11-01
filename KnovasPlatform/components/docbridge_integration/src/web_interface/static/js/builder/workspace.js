/* Brain Builder -- a workspace's overview: deleting it, confirmed by typing its name.
 * The server checks the name and the permission again; this only guides. */
(function () {
    'use strict';

    var dialog = document.getElementById('b-ws-delete');
    var opener = document.querySelector('[data-ws-delete-open]');
    if (!dialog || !opener) return;
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    var form = dialog.querySelector('form');
    var name = dialog.querySelector('p.b-muted').textContent.trim();
    var confirm = dialog.querySelector('[data-ws-delete-confirm]');
    var error = dialog.querySelector('[data-ws-delete-error]');

    opener.addEventListener('click', function () {
        form.reset();
        confirm.disabled = true;
        error.hidden = true;
        dialog.showModal();
        form.elements.name.focus();
    });
    Array.prototype.forEach.call(dialog.querySelectorAll('[data-ws-delete-close]'), function (b) {
        b.addEventListener('click', function () { dialog.close(); });
    });
    dialog.addEventListener('close', function () { opener.focus(); });
    form.elements.name.addEventListener('input', function () {
        confirm.disabled = form.elements.name.value.trim() !== name;
    });
    form.addEventListener('submit', function (event) {
        event.preventDefault();
        if (confirm.disabled) return;
        confirm.disabled = true;
        error.hidden = true;
        fetch(dialog.dataset.deleteUrl, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'X-CSRF-Token': csrf, 'Accept': 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: form.elements.name.value }),
        }).then(function (r) {
            return r.json().catch(function () { return { success: false, error: 'Der Server hat nicht verständlich geantwortet.' }; });
        }, function () { return { success: false, error: 'Der Server ist nicht erreichbar.' }; }).then(function (r) {
            if (r.success) { window.location.assign(r.url); return; }
            error.textContent = r.error || 'Das hat nicht geklappt.';
            error.hidden = false;
            confirm.disabled = false;
        });
    });
})();
