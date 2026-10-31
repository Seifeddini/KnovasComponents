/* Brain Builder -- the new-workspace wizard (stage 1, steps B1 and B4).
 *
 * Three steps in one native <dialog>: the company's name, whether the
 * workspace is linked to this Platform's tenant (the server takes the tenant
 * from its own certificate; the form only says yes or later), and the packs it
 * starts from. POST data-create-url creates it and answers with its URL.
 */
(function () {
    'use strict';

    var dialog = document.getElementById('b-create');
    if (!dialog) return;

    var STEPS = [['name', 'Name'], ['tenant', 'Mandant'], ['packs', 'Pakete']];
    var form = dialog.querySelector('[data-create-form]');
    var nameInput = dialog.querySelector('#b-create-name');
    var errorBox = dialog.querySelector('[data-create-error]');
    var label = dialog.querySelector('[data-create-step-label]');
    var next = dialog.querySelector('[data-create-next]');
    var back = dialog.querySelector('[data-create-back]');
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    var lastOpener = null;
    var step = 0;

    function setError(message) {
        errorBox.textContent = message || '';
        errorBox.hidden = !message;
    }

    function busy(on) {
        Array.prototype.forEach.call(form.querySelectorAll('button'), function (b) { b.disabled = on; });
        form.setAttribute('aria-busy', String(on));
    }

    function show(index) {
        step = index;
        STEPS.forEach(function (pair, i) {
            dialog.querySelector('[data-create-step="' + pair[0] + '"]').hidden = i !== index;
            var dot = dialog.querySelector('[data-create-dot="' + pair[0] + '"]');
            dot.className = i < index ? 'is-done' : (i === index ? 'is-current' : '');
        });
        label.textContent = 'Schritt ' + (index + 1) + ' von ' + STEPS.length + ': ' + STEPS[index][1];
        next.textContent = index === STEPS.length - 1 ? 'Arbeitsbereich anlegen' : 'Weiter';
        back.hidden = index === 0;
        setError(null);
        var first = dialog.querySelector('[data-create-step="' + STEPS[index][0] + '"] input');
        if (first) first.focus();
    }

    Array.prototype.forEach.call(document.querySelectorAll('[data-create-open]'), function (button) {
        button.addEventListener('click', function () {
            if (button.getAttribute('aria-disabled') === 'true') return;
            lastOpener = button;
            form.reset();
            busy(false);
            dialog.showModal();
            show(0);
        });
    });

    Array.prototype.forEach.call(dialog.querySelectorAll('[data-create-close]'), function (button) {
        button.addEventListener('click', function () { dialog.close(); });
    });
    back.addEventListener('click', function () { show(Math.max(0, step - 1)); });
    dialog.addEventListener('close', function () { if (lastOpener) lastOpener.focus(); });
    nameInput.addEventListener('input', function () { setError(null); });

    form.addEventListener('submit', function (event) {
        event.preventDefault();
        var name = nameInput.value.trim();
        if (!name) {
            show(0);
            setError('Bitte einen Namen eingeben.');
            nameInput.focus();
            return;
        }
        if (step < STEPS.length - 1) { show(step + 1); return; }
        setError(null);
        busy(true);
        var body = new FormData(form);
        body.set('name', name);
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
            if (!payload.success) {
                busy(false);
                setError((payload.error || 'Das hat nicht geklappt.') + ((payload.details || []).length ? ' (' + payload.details.join(', ') + ')' : ''));
                return;
            }
            window.location.assign(payload.url);
        });
    });
})();
