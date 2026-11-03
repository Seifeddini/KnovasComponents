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
                    var deploy = wizard.querySelector('[data-enrol-deploy]');
                    var box = wizard.querySelector('[data-enrol-command]');
                    var slug = form.elements.name.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'verbindung';
                    var line = kind ? deployLine(kind.value, slug, window.location.origin + box.dataset.protocolPath, r.token) : null;
                    deploy.hidden = !line;
                    if (line) {
                        box.value = line.lines.join(' \\\n');
                        box.rows = line.lines.length + 1;
                        wizard.querySelector('[data-enrol-hint]').textContent = line.hint;
                    }
                    show(3);
                    wizard.querySelector('[data-enrol-token]').select();
                });
        });
    }

    /* One line per kind of component: the sync component reads records of any source, the KnovasConnector transfers
       a folder's documents. Each starts, enrols itself and takes its configuration from here. */
    function deployLine(kind, slug, url, token) {
        function syncLine(extra) {
            return ['docker run -d --name sync-' + slug + ' --restart unless-stopped', '  -v sync-' + slug + ':/data']
                .concat(extra)
                .concat(['  -e SYNC_NAME=' + slug, '  -e SYNC_PLATFORM_URL=' + url, '  -e SYNC_ENROL_TOKEN=' + token,
                         '  sync-component:0.1.0']);
        }
        if (kind === 'sync_component') {
            return { lines: syncLine([]),
                     hint: 'Für eine Dateiablage ergänzen: -v <Ordner bei der Firma>:/sources:ro. Für einen Zugang: -e SYNC_CREDENTIAL_<SCHLÜSSEL DER QUELLE>=<Token>. Danach die Quelle hier auf dieser Komponente anlegen.' };
        }
        if (kind === 'github_runner') {
            return { lines: syncLine(['  -e SYNC_CREDENTIAL_GITHUB=LESE_TOKEN_HIER_EINSETZEN']),
                     hint: 'LESE_TOKEN_HIER_EINSETZEN durch ein Token mit «Contents: read» ersetzen; bei einem öffentlichen Repository die Zeile weglassen. Das Token bleibt bei der Komponente. Danach die Quelle «GitHub-Repository» hier auf dieser Komponente anlegen.' };
        }
        if (kind === 'knovas_connector') {
            return { lines: ['docker run -d --name connector-' + slug + ' --restart unless-stopped',
                             '  -v connector-' + slug + ':/var/rc-state', '  -v /PFAD/ZUR/ABLAGE:/sources:ro',
                             '  -e RC_PLATFORM_URL=' + url, '  -e RC_ENROL_TOKEN=' + token, '  -e RC_ENROL_NAME=' + slug,
                             '  knovas-connector:0.1.1'],
                     hint: '/PFAD/ZUR/ABLAGE durch den Ordner bei der Firma ersetzen. Kein Port wird geöffnet: Der Connector ruft nur hinaus. Danach die Quelle «Dateiablage / DMS» hier auf dieser Komponente anlegen und die Zugriffsgruppen wählen.' };
        }
        return null;
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
