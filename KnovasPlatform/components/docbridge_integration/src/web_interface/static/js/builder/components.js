/* Brain Builder -- the sync components (build plan steps B7 and B13).
 *
 * The enrolment wizard: kind, then name, site and owner; the server issues a
 * one-time enrolment token (24 h), shown here once; the component enrols with
 * it over connector protocol v1. Next to it, the B7 registration of a
 * component that does not speak v1 yet (read through an adapter), and the
 * registry's kind filter.
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

    /* ---- the registry's kind filter ---------------------------------------- */

    var kindFilter = document.querySelector('[data-reg-kind]');
    if (kindFilter) kindFilter.addEventListener('change', function () { kindFilter.form.submit(); });

    /* ---- the enrolment wizard ------------------------------------------------ */

    var wizard = document.getElementById('b-enrol');
    var wizardOpen = document.querySelector('[data-enrol-open]');
    if (wizard && wizardOpen) {
        var form = wizard.querySelector('[data-enrol-form]');
        var errorBox = wizard.querySelector('[data-enrol-error]');
        var next = wizard.querySelector('[data-enrol-next]');
        var back = wizard.querySelector('[data-enrol-back]');
        var step = 1;
        var made = null;

        function show(n) {
            step = n;
            Array.prototype.forEach.call(wizard.querySelectorAll('[data-enrol-step]'), function (el) {
                el.hidden = Number(el.dataset.enrolStep) !== n;
            });
            Array.prototype.forEach.call(wizard.querySelectorAll('[data-enrol-step-label]'), function (el) {
                var at = Number(el.dataset.enrolStepLabel);
                if (at === n) el.setAttribute('aria-current', 'step');
                else el.removeAttribute('aria-current');
                el.classList.toggle('is-current', at === n);
                el.classList.toggle('is-done', at < n);
            });
            back.hidden = n !== 2;
            next.textContent = n === 1 ? 'Weiter' : n === 2 ? 'Token erzeugen' : 'Fertig';
            errorBox.hidden = true;
            var first = wizard.querySelector('[data-enrol-step="' + n + '"] input:not([disabled])');
            if (first && n !== 3) first.focus();
        }

        wizardOpen.addEventListener('click', function () {
            form.reset();
            made = null;
            show(1);
            wizard.showModal();
        });
        Array.prototype.forEach.call(wizard.querySelectorAll('[data-enrol-close]'), function (b) {
            b.addEventListener('click', function () {
                wizard.close();
                if (made) window.location.reload();
            });
        });
        back.addEventListener('click', function () { show(1); });

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            if (step === 1) { show(2); return; }
            if (step === 3) { wizard.close(); window.location.href = made.url; return; }
            var kind = form.querySelector('input[name="kind"]:checked');
            if (!form.elements.name.value.trim()) {
                errorBox.textContent = 'Wie heißt die Komponente?';
                errorBox.hidden = false;
                return;
            }
            next.disabled = true;
            post(wizardOpen.dataset.url, { kind: kind ? kind.value : '', name: form.elements.name.value,
                                           site: form.elements.site.value, owner: form.elements.owner.value })
                .then(function (r) {
                    next.disabled = false;
                    if (!r.success) { errorBox.textContent = r.error || 'Das hat nicht geklappt.'; errorBox.hidden = false; return; }
                    made = r;
                    wizard.querySelector('[data-enrol-token]').value = r.token;
                    wizard.querySelector('[data-enrol-name]').textContent = form.elements.name.value;
                    var expires = new Date(r.expires_at);
                    wizard.querySelector('[data-enrol-expires]').textContent = isNaN(expires.getTime()) ? r.expires_at
                        : expires.toLocaleString('de-CH', { dateStyle: 'medium', timeStyle: 'short' });
                    show(3);
                    wizard.querySelector('[data-enrol-token]').select();
                });
        });
    }

    /* ---- the B7 registration (adapter) ------------------------------------- */

    var dialog = document.getElementById('b-comp-new');
    var opener = document.querySelector('[data-comp-new]');
    if (!dialog || !opener) return;
    var regForm = dialog.querySelector('form');
    var error = dialog.querySelector('[data-ed-error]');

    function kind() {
        var checked = regForm.querySelector('input[name="kind"]:checked');
        return checked ? checked.value : '';
    }
    function syncBase() {
        dialog.querySelector('[data-cn-base]').hidden = kind() !== 'knovas_connector';
    }

    opener.addEventListener('click', function () {
        regForm.reset();
        error.hidden = true;
        syncBase();
        dialog.showModal();
        regForm.elements.name.focus();
    });
    Array.prototype.forEach.call(regForm.querySelectorAll('input[name="kind"]'), function (r) { r.addEventListener('change', syncBase); });
    Array.prototype.forEach.call(dialog.querySelectorAll('[data-ed-close]'), function (b) {
        b.addEventListener('click', function () { dialog.close(); opener.focus(); });
    });
    regForm.addEventListener('submit', function (e) {
        e.preventDefault();
        var body = { kind: kind(), name: regForm.elements.name.value, site: regForm.elements.site.value,
                     owner: regForm.elements.owner.value, settings: {} };
        if (body.kind === 'knovas_connector' && regForm.elements.base_url.value.trim()) body.settings.base_url = regForm.elements.base_url.value.trim();
        post(opener.dataset.url || '/builder/api/components', body).then(function (payload) {
            if (!payload.success) { error.textContent = payload.error; error.hidden = false; return; }
            window.location.reload();
        });
    });
})();
