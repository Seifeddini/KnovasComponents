/* Brain Builder -- the workspace's team (build plan step B11): invite by e-mail, revoke.
 * A new account's one-time password is shown once, here, and nowhere else. */
(function () {
    'use strict';

    var root = document.querySelector('[data-team]');
    if (!root) return;
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

    var form = root.querySelector('[data-team-invite]');
    if (form) {
        var error = root.querySelector('[data-team-error]');
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            error.hidden = true;
            var roles = Array.prototype.filter.call(form.querySelectorAll('input[name="roles"]'), function (c) { return c.checked; })
                .map(function (c) { return c.value; });
            var button = form.querySelector('button[type="submit"]');
            button.disabled = true;
            post(root.dataset.grantUrl, { email: form.email.value, name: form.name.value, ends_on: form.ends_on.value, roles: roles })
                .then(function (r) {
                    button.disabled = false;
                    if (!r.success) { error.textContent = r.error || 'Das hat nicht geklappt.'; error.hidden = false; return; }
                    var made = root.querySelector('[data-team-made]');
                    made.querySelector('[data-team-made-text]').textContent = r.email + ' hat Zugang bis ' + r.ends_on + '.';
                    var pw = made.querySelector('[data-team-password]');
                    pw.hidden = !r.password;
                    made.querySelector('[data-team-password-value]').textContent = r.password || '';
                    made.hidden = false;
                    form.reset();
                });
        });
    }

    Array.prototype.forEach.call(document.querySelectorAll('[data-team-revoke]'), function (button) {
        button.addEventListener('click', function () {
            button.disabled = true;
            post(root.dataset.revokeUrl.replace('GRANT', encodeURIComponent(button.dataset.teamRevoke))).then(function (r) {
                if (r.success) window.location.reload();
                else button.disabled = false;
            });
        });
    });
})();
