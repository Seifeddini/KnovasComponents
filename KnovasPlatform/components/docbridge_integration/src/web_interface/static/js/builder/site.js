/* Brain Builder -- a site (connection layer MVP-23).
 *
 * «Installationspaket herunterladen» posts the address this browser reached the Platform at (behind nginx the server's
 * own Host has no port) and saves the zip it answers with; «Token erneuern» and «Jetzt prüfen» post and reload. Also
 * «Standort anlegen» on the «Verbinden» page.
 */
(function () {
    'use strict';

    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

    function send(url, body) {
        return fetch(url, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'X-CSRF-Token': csrf, 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
    }

    function json(response) {
        return response.json().catch(function () { return { success: false, error: 'Der Server hat nicht verständlich geantwortet.' }; });
    }

    function fail(box, text) {
        if (box) { box.textContent = text; box.hidden = false; }
        else if (window.BuilderUI) window.BuilderUI.toast({ title: 'Nicht möglich', text: text, tone: 'critical' });
    }

    var root = document.querySelector('[data-site-root]');
    if (root) {
        var errorBox = document.querySelector('[data-site-error]');
        var download = root.querySelector('[data-site-package]');
        download.addEventListener('click', function () {
            download.disabled = true;
            send(root.dataset.packageUrl, { platform_url: window.location.origin + root.dataset.protocolPath })
                .then(function (r) {
                    if (!r.ok) return json(r).then(function (b) { throw new Error(b.error || 'Das Paket ließ sich nicht erzeugen.'); });
                    var name = (/filename="([^"]+)"/.exec(r.headers.get('Content-Disposition') || '') || [])[1] || 'knovas-site.zip';
                    return r.blob().then(function (blob) {
                        var a = document.createElement('a');
                        a.href = URL.createObjectURL(blob);
                        a.download = name;
                        document.body.appendChild(a);
                        a.click();
                        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
                        if (window.BuilderUI) window.BuilderUI.toast({ title: 'Paket heruntergeladen', text: 'Das Token darin gilt 24 Stunden und nur einmal.', tone: 'good' });
                    });
                })
                .catch(function (e) { fail(errorBox, e.message || 'Der Server ist nicht erreichbar.'); })
                .then(function () { download.disabled = false; });
        });
        [['[data-site-rotate]', 'rotateUrl', 'Knovas Link meldet sich danach neu an: jetzt das Paket neu herunterladen und starten. Weiter?'],
         ['[data-site-probe]', 'probeUrl', null]].forEach(function (spec) {
            var button = root.querySelector(spec[0]);
            if (!button) return;
            button.addEventListener('click', function () {
                if (spec[2] && !window.confirm(spec[2])) return;
                send(root.dataset[spec[1]]).then(json).then(function (b) {
                    if (b.success) window.location.reload();
                    else fail(errorBox, b.error || 'Nicht möglich.');
                });
            });
        });
    }

    var dialog = document.getElementById('b-site-new');
    var opener = document.querySelector('[data-site-new]');
    if (dialog && opener) {
        var form = dialog.querySelector('form');
        var box = dialog.querySelector('[data-ed-error]');
        opener.addEventListener('click', function () { box.hidden = true; dialog.showModal(); });
        Array.prototype.forEach.call(dialog.querySelectorAll('[data-ed-close]'), function (b) {
            b.addEventListener('click', function () { dialog.close(); });
        });
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var options = Array.prototype.map.call(form.querySelectorAll('input[name="options"]:checked'), function (i) { return i.value; });
            send(opener.dataset.url, { name: form.elements.name.value, owner: form.elements.owner.value, options: options })
                .then(json).then(function (b) {
                    if (b.url) window.location.href = b.url;
                    else fail(box, b.error || 'Der Standort ließ sich nicht anlegen.');
                });
        });
    }
})();
