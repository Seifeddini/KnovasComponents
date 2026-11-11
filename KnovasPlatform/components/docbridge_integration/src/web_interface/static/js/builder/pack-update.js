/* Brain Builder -- applying a reviewed pack update (step B4).
 *
 * One POST of {"op": "pack.apply"} to the ontology's edit URL: the same edit
 * as every other, logged and undoable. Then on to the ontology.
 */
(function () {
    'use strict';

    var box = document.querySelector('[data-pack-apply]');
    if (!box) return;
    var button = box.querySelector('[data-pack-apply-button]');
    var error = document.querySelector('[data-pack-error]');
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

    button.addEventListener('click', function () {
        button.disabled = true;
        error.hidden = true;
        fetch(box.dataset.editUrl, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'X-CSRF-Token': csrf, 'Accept': 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify({ op: 'pack.apply', pack: box.dataset.pack }),
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
