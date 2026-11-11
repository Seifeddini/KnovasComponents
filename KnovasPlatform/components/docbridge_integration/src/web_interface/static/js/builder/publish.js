/* Brain Builder -- review and publish (stage 7, build plan step B5).
 *
 * Every button is one POST; the server checks everything again (the draft's
 * content hash, who may approve, the dry run, the approval, the explicit
 * confirmation of retirements) and the page reloads to show the result.
 */
(function () {
    'use strict';

    var root = document.querySelector('[data-publish]');
    if (!root) return;
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    var hash = root.dataset.contentHash;
    var result = root.querySelector('[data-publish-result]');
    var confirmDialog = document.getElementById('b-rv-confirm');

    function post(url, body) {
        return fetch(url, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'X-CSRF-Token': csrf, 'Accept': 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        }).then(function (response) {
            return response.json().catch(function () { return { success: false, error: 'Der Server hat nicht verständlich geantwortet.' }; });
        }, function () {
            return { success: false, error: 'Der Server ist nicht erreichbar.' };
        });
    }

    function failed(payload) {
        var text = (payload.error || 'Das hat nicht geklappt.') + ((payload.details || []).length ? ' (' + payload.details.join(', ') + ')' : '');
        if (window.BuilderUI && window.BuilderUI.toast) window.BuilderUI.toast({ title: text, tone: 'critical' });
        if (result) { result.className = 'b-callout b-callout--critical'; result.textContent = text; result.hidden = false; }
    }

    function act(button, url, body) {
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        return post(url, body).then(function (payload) {
            button.removeAttribute('aria-busy');
            if (!payload.success) { button.disabled = false; failed(payload); return false; }
            window.location.reload();
            return true;
        });
    }

    function on(selector, fn) {
        var b = root.querySelector(selector);
        if (b) b.addEventListener('click', function () { fn(b); });
    }

    var author = root.querySelector('[data-publish-author]');
    if (author) author.addEventListener('change', function () { author.form.submit(); });

    on('[data-publish-link]', function (b) { act(b, root.dataset.tenantUrl); });
    on('[data-publish-dry-run]', function (b) { act(b, root.dataset.dryRunUrl); });
    /* The approval holds the digest of the dry run this page shows; the server refuses one it no longer matches. */
    var reviewed = root.dataset.reviewHash || undefined;
    on('[data-publish-approve]', function (b) { act(b, root.dataset.approveUrl, { content_hash: hash, review_hash: reviewed }); });

    /* An administrator approving their own changes: a reason and a tick first; the server checks both again. */
    var self = root.querySelector('[data-publish-self]');
    if (self) {
        var reason = self.querySelector('[data-publish-self-reason]');
        var tick = self.querySelector('[data-publish-self-confirm]');
        var go = self.querySelector('[data-publish-self-approve]');
        var ready = function () { go.disabled = !(tick.checked && reason.value.trim().length >= 3); };
        reason.addEventListener('input', ready);
        tick.addEventListener('change', ready);
        go.addEventListener('click', function () {
            act(go, root.dataset.approveUrl, { content_hash: hash, review_hash: reviewed, self_approve: true, reason: reason.value.trim() });
        });
    }

    var paths = confirmDialog ? Array.prototype.map.call(confirmDialog.querySelectorAll('[data-path]'), function (li) { return li.dataset.path; }) : [];
    on('[data-publish-apply]', function (b) {
        if (!paths.length) { act(b, root.dataset.publishUrl, { content_hash: hash }); return; }
        var box = confirmDialog.querySelector('[data-publish-confirm-box]');
        var go = confirmDialog.querySelector('[data-publish-confirm]');
        box.checked = false;
        go.disabled = true;
        confirmDialog.showModal();
        box.onchange = function () { go.disabled = !box.checked; };
        confirmDialog.querySelector('[data-publish-cancel]').onclick = function () { confirmDialog.close(); };
        go.onclick = function () {
            confirmDialog.close();
            act(b, root.dataset.publishUrl, { content_hash: hash, confirm_destructive: paths });
        };
    });
})();
