/* Brain Builder -- adopting the tenant's definitions into a workspace (BB-12).
 *
 * One POST to the adopt URL: the server reads the tenant's bundle itself and
 * writes the additions as one edit, logged and undoable. Then on to the
 * processes.
 */
(function () {
    'use strict';

    var box = document.querySelector('[data-definitions-adopt]');
    if (!box) return;
    var button = box.querySelector('[data-definitions-adopt-button]');
    var error = document.querySelector('[data-definitions-error]');
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

    button.addEventListener('click', function () {
        button.disabled = true;
        error.hidden = true;
        fetch(box.dataset.adoptUrl, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'X-CSRF-Token': csrf, 'Accept': 'application/json', 'Content-Type': 'application/json' },
            body: '{}',
        }).then(function (response) {
            return response.json().catch(function () { return { success: false, error: 'Der Server hat nicht verständlich geantwortet.' }; });
        }, function () {
            return { success: false, error: 'Der Server ist nicht erreichbar.' };
        }).then(function (payload) {
            if (payload.success) { window.location.assign(box.dataset.doneUrl); return; }
            button.disabled = false;
            error.textContent = payload.error + ((payload.details || []).length ? ' (' + payload.details.join(', ') + ')' : '');
            error.hidden = false;
        });
    });
})();
