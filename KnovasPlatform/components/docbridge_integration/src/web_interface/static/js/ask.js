/* Knovas Assistent (PMVP-22a): the switch in the search page's box, and the references on the ask page.

   The box (index.html, #askForm): the switch "Fragen / Suchen" is shown. It stands on "Fragen" and follows what is
   typed -- one to three words without a question mark switch it to "Suchen", anything else back to "Fragen" --
   until the person chooses; then the person's choice holds. "Fragen" posts the form to /fragen; "Suchen" leaves
   the box to app.js exactly as before (the form is not sent, app.js searches).

   The ask page (ask.html): without this script every quote stands open under its statement. Here each closes,
   and its reference (a button with aria-controls) opens and closes its own quote, in place.

   A chat's page (PMVP-23): "Gespraech loeschen" asks first, with the form's own data-confirm text; without this
   script the form deletes at once.

   The waiting line (PMVP-24), for the search page's box (Fragen) and every box of the ask page: when a question is
   sent, the button is disabled, the box keeps the question and the status line under it says the first text; after
   data-wait-slow-ms (20 s) the second; after data-wait-giveup-ms (170 s, past the server's deadline of 150 s) the
   third, and the button works again. A second Enter or click while waiting sends nothing. On pageshow (back and
   forward) the box is reset. The line's texts are the three fixed ones below, set as text; nothing else is written.

   No markup is written from text: only attributes, classes and the hidden property change. */
(function () {
    'use strict';

    // --- the waiting line ----------------------------------------------------------------------------------
    var WAIT_TEXTS = [
        '',
        'Der Knovas Assistent sucht in Ihren Quellen und pr\u00fcft die Antwort \u2026',
        'Das dauert l\u00e4nger als sonst. Die Antwort kommt, sobald sie gepr\u00fcft ist.',
        'Keine Antwort erhalten. Bitte versuchen Sie es noch einmal.'
    ];
    var SLOW_MS = 20000;
    var GIVE_UP_MS = 170000;

    // The waiting of one box: its status line is inside the form (the search page) or right after it (the ask page).
    var waiting = function (form, button) {
        var line = form.querySelector('.ask-wait');
        var next = form.nextElementSibling;
        if (!line && next && next.classList.contains('ask-wait')) line = next;
        var timers = [];
        var sending = false;
        var after = function (name, fallback) {
            var ms = line ? parseInt(line.getAttribute(name), 10) : NaN;
            return ms > 0 ? ms : fallback;
        };
        var say = function (step) {
            if (!line) return;
            line.textContent = WAIT_TEXTS[step];
            line.setAttribute('data-wait-step', String(step));
        };
        var stop = function () {
            timers.forEach(clearTimeout);
            timers = [];
        };
        var reset = function () {
            stop();
            sending = false;
            if (button) button.disabled = false;
            say(0);
        };
        window.addEventListener('pageshow', reset);
        return {
            sending: function () { return sending; },
            start: function () {
                stop();
                sending = true;
                if (button) button.disabled = true;
                say(1);
                timers.push(setTimeout(function () { say(2); }, after('data-wait-slow-ms', SLOW_MS)));
                timers.push(setTimeout(function () {
                    say(3);
                    sending = false;
                    if (button) button.disabled = false;
                }, after('data-wait-giveup-ms', GIVE_UP_MS)));
            }
        };
    };

    // --- the switch in the box -------------------------------------------------------------------------------
    var form = document.getElementById('askForm');
    if (form) {
        var input = document.getElementById('searchInput');
        var askButton = document.getElementById('askButton');
        var searchButton = document.getElementById('searchButton');
        var switchBox = document.getElementById('askSwitch');
        var askMode = document.getElementById('askModeAsk');
        var searchMode = document.getElementById('askModeSearch');
        var chosen = false;

        var mode = function () { return searchMode.checked ? 'suchen' : 'fragen'; };
        var show = function () {
            var asking = mode() === 'fragen';
            askButton.hidden = !asking;
            searchButton.hidden = asking;
        };
        // One to three words and no question mark read as search terms.
        var looksLikeTerms = function (text) {
            var words = text.trim().split(/\s+/).filter(Boolean);
            return words.length >= 1 && words.length <= 3 && text.indexOf('?') === -1;
        };

        switchBox.hidden = false;
        show();

        input.addEventListener('input', function () {
            if (chosen) return;
            var terms = looksLikeTerms(input.value);
            askMode.checked = !terms;
            searchMode.checked = terms;
            show();
        });
        [askMode, searchMode].forEach(function (radio) {
            radio.addEventListener('change', function () {
                chosen = true;
                show();
                input.focus();
            });
        });
        // Enter in the field: app.js searches on it. While asking, the key stops here (capture, before it reaches
        // the field), and the browser sends the form.
        form.addEventListener('keypress', function (event) {
            if (event.key === 'Enter' && event.target === input && mode() === 'fragen') {
                event.stopPropagation();
            }
        }, true);
        var boxWait = waiting(form, askButton);
        form.addEventListener('submit', function (event) {
            if (mode() !== 'fragen' || !input.value.trim() || boxWait.sending()) {
                event.preventDefault();
                return;
            }
            boxWait.start();
        });
    }

    // --- the boxes of the ask page: a new question or a follow-up ------------------------------------------
    Array.prototype.forEach.call(document.querySelectorAll('form.ask-again'), function (again) {
        var againWait = waiting(again, again.querySelector('button[type="submit"]'));
        again.addEventListener('submit', function (event) {
            if (againWait.sending()) {
                event.preventDefault();
                return;
            }
            againWait.start();
        });
    });

    // --- the references on the ask page --------------------------------------------------------------------
    var chips = document.querySelectorAll('.ask-statement [aria-controls]');
    Array.prototype.forEach.call(chips, function (chip) {
        var quote = document.getElementById(chip.getAttribute('aria-controls'));
        if (!quote) return;
        var set = function (open) {
            quote.hidden = !open;
            chip.setAttribute('aria-expanded', open ? 'true' : 'false');
            chip.classList.toggle('k-ref--on', open);
        };
        set(false);
        chip.addEventListener('click', function () {
            set(quote.hidden);
        });
    });

    // --- a chat's page: deleting asks first ------------------------------------------------------------------
    var deleteForm = document.querySelector('form.ask-delete[data-confirm]');
    if (deleteForm) {
        deleteForm.addEventListener('submit', function (event) {
            if (!window.confirm(deleteForm.getAttribute('data-confirm'))) {
                event.preventDefault();
            }
        });
    }
})();
