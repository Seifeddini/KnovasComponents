/* Brain Builder -- the import dialog (stage 1).
 *
 * Two steps in one native <dialog>: choose a zip and let the server check it
 * (POST data-preview-url), then name the workspace and create it (POST
 * data-import-url with the same file). The file stays in the browser between
 * the steps; nothing is stored until the second POST.
 */
(function () {
    'use strict';

    var dialog = document.getElementById('b-import');
    if (!dialog) return;

    var choose = dialog.querySelector('[data-import-step="choose"]');
    var confirm = dialog.querySelector('[data-import-step="confirm"]');
    var stepLabel = dialog.querySelector('[data-import-step-label]');
    var fileInput = dialog.querySelector('#b-import-file');
    var nameInput = dialog.querySelector('#b-import-name');
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    var lastOpener = null;

    var COUNTS = [
        ['types', 'Typen'], ['relations', 'Beziehungen'], ['attributes', 'Attribute'],
        ['processes', 'Prozesse'], ['mappings', 'Zuordnungen'], ['profiles', 'Profile'],
        ['spines', 'Hierarchien'], ['pages', 'Seiten']
    ];

    function showStep(step) {
        choose.hidden = step !== 'choose';
        confirm.hidden = step !== 'confirm';
        stepLabel.textContent = step === 'choose' ? 'Schritt 1 von 2: Paket wählen' : 'Schritt 2 von 2: Prüfen und anlegen';
        var first = (step === 'choose' ? fileInput : nameInput);
        if (first) first.focus();
    }

    function setError(form, payload) {
        var box = form.querySelector('[data-import-error]');
        box.textContent = '';
        if (!payload) { box.hidden = true; return; }
        var title = document.createElement('p');
        title.className = 'b-callout-title';
        title.textContent = payload.error || 'Das hat nicht geklappt.';
        box.appendChild(title);
        if (payload.details && payload.details.length) {
            var list = document.createElement('ul');
            payload.details.forEach(function (detail) {
                var item = document.createElement('li');
                item.className = 'b-mono';
                item.textContent = detail;
                list.appendChild(item);
            });
            box.appendChild(list);
        }
        box.hidden = false;
    }

    function busy(form, on) {
        Array.prototype.forEach.call(form.querySelectorAll('button'), function (b) { b.disabled = on; });
        form.setAttribute('aria-busy', String(on));
    }

    function post(url, extra) {
        var body = new FormData();
        body.append('package', fileInput.files[0]);
        Object.keys(extra || {}).forEach(function (key) { body.append(key, extra[key]); });
        return fetch(url, {
            method: 'POST', body: body, credentials: 'same-origin',
            headers: { 'X-CSRF-Token': csrf, 'Accept': 'application/json' }
        }).then(function (response) {
            return response.json().catch(function () {
                return { success: false, error: response.status === 413 ? 'Das Archiv ist zu groß.' : 'Der Server hat nicht verständlich geantwortet.' };
            });
        }, function () {
            return { success: false, error: 'Der Server ist nicht erreichbar.' };
        });
    }

    function stat(label, value, detail) {
        var tile = document.createElement('div');
        tile.className = 'b-stat b-stat--small';
        [['b-stat-label', label], ['b-stat-value', String(value)], ['b-stat-detail', detail]].forEach(function (part) {
            if (part[1] === undefined || part[1] === '') return;
            var span = document.createElement('span');
            span.className = part[0];
            span.textContent = part[1];
            tile.appendChild(span);
        });
        return tile;
    }

    function showSummary(summary, suggestedName) {
        var grid = dialog.querySelector('[data-import-summary]');
        grid.textContent = '';
        COUNTS.forEach(function (pair) {
            grid.appendChild(stat(pair[1], summary[pair[0]]));
        });
        var levels = dialog.querySelector('[data-import-levels]');
        levels.hidden = !summary.levels;
        levels.textContent = summary.levels
            ? 'Typen nach Ebene: ' + summary.levels.core + ' Kern, ' + summary.levels.pack + ' Paket, ' + summary.levels.company + ' Firma.'
            : '';

        var company = dialog.querySelector('[data-import-company]');
        company.textContent = summary.company ? 'Gefunden: das Paket von ' + summary.company + '.' : 'Gefunden:';

        var warnings = dialog.querySelector('[data-import-warnings]');
        var list = warnings.querySelector('ul');
        list.textContent = '';
        (summary.warnings || []).forEach(function (text) {
            var item = document.createElement('li');
            item.textContent = text;
            list.appendChild(item);
        });
        warnings.hidden = !(summary.warnings || []).length;

        var ignored = dialog.querySelector('[data-import-ignored]');
        ignored.hidden = !summary.ignored_files;
        ignored.textContent = summary.ignored_files
            ? summary.ignored_files + ' weitere YAML-Dateien im Archiv gehören nicht zur Konfiguration und werden nicht übernommen.'
            : '';

        if (!nameInput.value) nameInput.value = suggestedName || '';
    }

    Array.prototype.forEach.call(document.querySelectorAll('[data-import-open]'), function (button) {
        button.addEventListener('click', function () {
            if (button.getAttribute('aria-disabled') === 'true') return;
            lastOpener = button;
            choose.reset();
            confirm.reset();
            setError(choose, null);
            setError(confirm, null);
            dialog.showModal();
            showStep('choose');
        });
    });

    Array.prototype.forEach.call(dialog.querySelectorAll('[data-import-close]'), function (button) {
        button.addEventListener('click', function () { dialog.close(); });
    });
    dialog.addEventListener('close', function () { if (lastOpener) lastOpener.focus(); });
    dialog.querySelector('[data-import-back]').addEventListener('click', function () { showStep('choose'); });

    choose.addEventListener('submit', function (event) {
        event.preventDefault();
        if (!fileInput.files.length) {
            setError(choose, { error: 'Bitte ein ZIP-Archiv wählen.' });
            fileInput.focus();
            return;
        }
        setError(choose, null);
        busy(choose, true);
        post(dialog.getAttribute('data-preview-url')).then(function (payload) {
            busy(choose, false);
            if (!payload.success) { setError(choose, payload); return; }
            nameInput.value = '';
            showSummary(payload.summary, payload.suggested_name);
            showStep('confirm');
        });
    });

    confirm.addEventListener('submit', function (event) {
        event.preventDefault();
        var name = nameInput.value.trim();
        if (!name) {
            setError(confirm, { error: 'Bitte einen Namen eingeben.' });
            nameInput.focus();
            return;
        }
        setError(confirm, null);
        busy(confirm, true);
        post(dialog.getAttribute('data-import-url'), { name: name }).then(function (payload) {
            if (!payload.success) { busy(confirm, false); setError(confirm, payload); return; }
            window.location.assign(payload.url);
        });
    });
})();
