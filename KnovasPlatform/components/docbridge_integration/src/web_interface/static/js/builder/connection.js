/* Brain Builder -- «Betrieb» › «Verbindung» (connection layer MVP-28).
 *
 * Reads the cards again every data-refresh-seconds (30) and on «Aktualisieren», and says how old the shown state is.
 */
(function () {
    'use strict';

    var box = document.querySelector('[data-connection]');
    if (!box) return;
    var at = document.querySelector('[data-conn-at]');
    var shownAt = Date.now();

    function age() {
        var s = Math.round((Date.now() - shownAt) / 1000);
        if (at) at.textContent = s < 5 ? 'gerade eben' : 'vor ' + s + ' s';
    }

    function refresh() {
        fetch(box.dataset.url, { credentials: 'same-origin', headers: { 'Accept': 'text/html' } })
            .then(function (r) { return r.ok ? r.text() : null; })
            .then(function (html) {
                if (!html) return;
                box.innerHTML = html;
                shownAt = Date.now();
                age();
            })
            .catch(function () { /* keep what is shown; its age says how old it is */ });
    }

    var every = Math.max(10, Number(box.dataset.refreshSeconds) || 30) * 1000;
    setInterval(refresh, every);
    setInterval(age, 5000);
    var button = document.querySelector('[data-conn-refresh]');
    if (button) button.addEventListener('click', refresh);
})();
