/* Brain Builder -- registering a sync component (build plan step B7; B13 adds enrolment).
 *
 * One POST to /builder/api/components with kind, name, site, owner and, for a
 * further KnovasConnector, its address; the page reloads with the register.
 */
(function () {
    'use strict';

    var dialog = document.getElementById('b-comp-new');
    var opener = document.querySelector('[data-comp-new]');
    if (!dialog || !opener) return;
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    var form = dialog.querySelector('form');
    var error = dialog.querySelector('[data-ed-error]');

    function kind() {
        var checked = form.querySelector('input[name="kind"]:checked');
        return checked ? checked.value : '';
    }

    function syncBase() {
        dialog.querySelector('[data-cn-base]').hidden = kind() !== 'knovas_connector';
    }

    opener.addEventListener('click', function () {
        form.reset();
        error.hidden = true;
        syncBase();
        dialog.showModal();
        form.elements.name.focus();
    });
    Array.prototype.forEach.call(form.querySelectorAll('input[name="kind"]'), function (r) { r.addEventListener('change', syncBase); });
    Array.prototype.forEach.call(dialog.querySelectorAll('[data-ed-close]'), function (b) {
        b.addEventListener('click', function () { dialog.close(); opener.focus(); });
    });

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        var body = { kind: kind(), name: form.elements.name.value, site: form.elements.site.value,
                     owner: form.elements.owner.value, settings: {} };
        if (body.kind === 'knovas_connector' && form.elements.base_url.value.trim()) body.settings.base_url = form.elements.base_url.value.trim();
        fetch(opener.dataset.url || '/builder/api/components', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'X-CSRF-Token': csrf, 'Accept': 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        }).then(function (r) { return r.json(); }, function () { return { success: false, error: 'Der Server ist nicht erreichbar.' }; })
          .then(function (payload) {
              if (!payload.success) { error.textContent = payload.error; error.hidden = false; return; }
              window.location.reload();
          });
    });
})();
