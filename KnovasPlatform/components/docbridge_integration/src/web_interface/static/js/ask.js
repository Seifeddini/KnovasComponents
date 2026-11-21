/* Knovas Assistent (PMVP-22a): the switch in the search page's box, and the references on the ask page.

   The box (index.html, #askForm): the switch "Fragen / Suchen" is shown. It stands on "Fragen" and follows what is
   typed -- one to three words without a question mark switch it to "Suchen", anything else back to "Fragen" --
   until the person chooses; then the person's choice holds. "Fragen" posts the form to /fragen; "Suchen" leaves
   the box to app.js exactly as before (the form is not sent, app.js searches).

   The ask page (ask.html): without this script every quote stands open under its statement. Here each closes,
   and its reference (a button with aria-controls) opens and closes its own quote, in place.

   No markup is written from text: only attributes, classes and the hidden property change. */
(function () {
    'use strict';

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
        form.addEventListener('submit', function (event) {
            if (mode() !== 'fragen' || !input.value.trim()) {
                event.preventDefault();
                return;
            }
            askButton.disabled = true;
        });
    }

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
})();
