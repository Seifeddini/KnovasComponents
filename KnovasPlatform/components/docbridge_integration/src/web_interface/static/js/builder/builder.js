/* Brain Builder -- shell and component behaviour.
 *
 * No framework, no build step. Every behaviour hangs off a data-* attribute,
 * so markup opts in and a page without the attribute is untouched:
 *
 *   data-theme-choice      radio: system | light | dark, remembered per browser
 *   data-drawer-toggle     opens the stage rail on narrow screens
 *   data-popover-toggle    button that shows/hides the element in aria-controls
 *   [role=tablist]         tabs with arrow keys, Home and End
 *   data-panel             side panel; data-panel-close, data-panel-open=<id>,
 *                          data-panel-resize (drag or arrow keys)
 *   data-toggle-group      buttons with aria-pressed, one pressed at a time
 *   data-indeterminate     checkbox shown as partly checked
 *   data-toast-demo        style guide: shows a toast of that tone
 *   data-sg-view           style guide: which themes to show
 *
 * window.BuilderUI.toast({title, text, tone}) shows a toast from any script.
 * Show and hide always use the hidden attribute (builder.css makes it win).
 */
(function () {
    'use strict';

    var THEME_KEY = 'knovas.builder.theme';
    var root = document.documentElement;

    function each(selector, fn, scope) {
        Array.prototype.forEach.call((scope || document).querySelectorAll(selector), fn);
    }

    /* ---- Theme -------------------------------------------------------- */

    function storedTheme() {
        try { return localStorage.getItem(THEME_KEY) || 'system'; } catch (e) { return 'system'; }
    }

    function applyTheme(choice) {
        if (choice === 'light' || choice === 'dark') root.setAttribute('data-theme', choice);
        else root.removeAttribute('data-theme');
        try {
            if (choice === 'system') localStorage.removeItem(THEME_KEY);
            else localStorage.setItem(THEME_KEY, choice);
        } catch (e) { /* storage blocked: the choice lasts until reload */ }
    }

    var currentTheme = storedTheme();
    each('[data-theme-choice]', function (input) {
        input.checked = input.value === currentTheme;
        input.addEventListener('change', function () { if (input.checked) applyTheme(input.value); });
    });

    /* ---- Drawer (stage rail on narrow screens) ------------------------ */

    var shell = document.querySelector('[data-shell]');
    var drawerToggle = document.querySelector('[data-drawer-toggle]');

    function setDrawer(open) {
        if (!shell || !drawerToggle) return;
        if (open) shell.setAttribute('data-drawer', 'open');
        else shell.removeAttribute('data-drawer');
        drawerToggle.setAttribute('aria-expanded', String(open));
        if (open) {
            var first = document.querySelector('#b-rail a, #b-rail button, #b-rail input');
            if (first) first.focus();
        } else {
            drawerToggle.focus();
        }
    }

    if (drawerToggle) {
        drawerToggle.addEventListener('click', function () {
            setDrawer(shell.getAttribute('data-drawer') !== 'open');
        });
    }
    each('[data-drawer-close]', function (el) {
        el.addEventListener('click', function () { setDrawer(false); });
    });

    /* ---- Popovers ----------------------------------------------------- */

    var openPopover = null;

    function closePopover(returnFocus) {
        if (!openPopover) return;
        openPopover.panel.hidden = true;
        openPopover.button.setAttribute('aria-expanded', 'false');
        if (returnFocus) openPopover.button.focus();
        openPopover = null;
    }

    each('[data-popover-toggle]', function (button) {
        var panel = document.getElementById(button.getAttribute('aria-controls'));
        if (!panel) return;
        button.addEventListener('click', function (event) {
            event.stopPropagation();
            var wasOpen = openPopover && openPopover.button === button;
            closePopover(false);
            if (wasOpen) return;
            panel.hidden = false;
            button.setAttribute('aria-expanded', 'true');
            openPopover = { button: button, panel: panel };
            var first = panel.querySelector('a, button');
            if (first) first.focus();
        });
    });

    document.addEventListener('click', function (event) {
        if (openPopover && !openPopover.panel.contains(event.target)) closePopover(false);
    });

    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') return;
        if (openPopover) { closePopover(true); return; }
        if (shell && shell.getAttribute('data-drawer') === 'open') setDrawer(false);
    });

    /* ---- Tabs --------------------------------------------------------- */

    each('[role="tablist"]', function (list) {
        var tabs = Array.prototype.filter.call(list.querySelectorAll('[role="tab"]'), function (tab) {
            return !tab.disabled;
        });

        function select(tab, focus) {
            tabs.forEach(function (other) {
                var on = other === tab;
                other.setAttribute('aria-selected', String(on));
                other.tabIndex = on ? 0 : -1;
                var panel = document.getElementById(other.getAttribute('aria-controls'));
                if (panel) panel.hidden = !on;
            });
            if (focus) tab.focus();
        }

        tabs.forEach(function (tab, index) {
            tab.addEventListener('click', function () { select(tab, false); });
            tab.addEventListener('keydown', function (event) {
                var next = null;
                if (event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
                else if (event.key === 'ArrowLeft') next = tabs[(index - 1 + tabs.length) % tabs.length];
                else if (event.key === 'Home') next = tabs[0];
                else if (event.key === 'End') next = tabs[tabs.length - 1];
                if (next) { event.preventDefault(); select(next, true); }
            });
        });
    });

    /* ---- Side panel --------------------------------------------------- */

    var PANEL_STEP = 16;

    function cssPx(el, name) {
        return parseFloat(getComputedStyle(el).getPropertyValue(name)) || 0;
    }

    each('[data-panel]', function (panel) {
        var lastOpener = null;

        function setWidth(px) {
            var min = cssPx(panel, '--panel-width-min');
            var max = Math.min(cssPx(panel, '--panel-width-max'), panel.parentElement.clientWidth);
            var width = Math.round(Math.max(min, Math.min(max, px)));
            panel.style.setProperty('--panel-width', width + 'px');
            var handle = panel.querySelector('[data-panel-resize]');
            if (handle) handle.setAttribute('aria-valuenow', String(width));
        }

        each('[data-panel-close]', function (button) {
            button.addEventListener('click', function () {
                panel.hidden = true;
                var back = lastOpener || document.querySelector('[data-panel-open="' + panel.id + '"]');
                if (back) back.focus();
            });
        }, panel);

        each('[data-panel-open="' + panel.id + '"]', function (opener) {
            opener.addEventListener('click', function () {
                lastOpener = opener;
                each('[data-panel-open="' + panel.id + '"]', function (other) {
                    other.setAttribute('aria-pressed', String(other === opener));
                });
                panel.hidden = false;
                var close = panel.querySelector('[data-panel-close]');
                if (close) close.focus();
            });
        });

        panel.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && !panel.hidden) {
                event.stopPropagation();
                var close = panel.querySelector('[data-panel-close]');
                if (close) close.click();
            }
        });

        var handle = panel.querySelector('[data-panel-resize]');
        if (!handle) return;

        handle.addEventListener('keydown', function (event) {
            var width = panel.getBoundingClientRect().width;
            if (event.key === 'ArrowLeft') setWidth(width + PANEL_STEP);
            else if (event.key === 'ArrowRight') setWidth(width - PANEL_STEP);
            else if (event.key === 'Home') setWidth(cssPx(panel, '--panel-width-min'));
            else if (event.key === 'End') setWidth(cssPx(panel, '--panel-width-max'));
            else return;
            event.preventDefault();
        });

        handle.addEventListener('pointerdown', function (event) {
            event.preventDefault();
            handle.setPointerCapture(event.pointerId);
            panel.classList.add('is-resizing');
            var right = panel.getBoundingClientRect().right;
            function move(e) { setWidth(right - e.clientX); }
            function up(e) {
                handle.releasePointerCapture(e.pointerId);
                panel.classList.remove('is-resizing');
                handle.removeEventListener('pointermove', move);
                handle.removeEventListener('pointerup', up);
            }
            handle.addEventListener('pointermove', move);
            handle.addEventListener('pointerup', up);
        });
    });

    /* ---- Toggle groups (toolbar) -------------------------------------- */

    each('[data-toggle-group]', function (group) {
        var buttons = group.querySelectorAll('[aria-pressed]');
        Array.prototype.forEach.call(buttons, function (button) {
            button.addEventListener('click', function () {
                Array.prototype.forEach.call(buttons, function (other) {
                    other.setAttribute('aria-pressed', String(other === button));
                });
            });
        });
    });

    each('input[type="checkbox"][data-indeterminate]', function (box) { box.indeterminate = true; });

    /* ---- Toasts ------------------------------------------------------- */

    var region = document.getElementById('b-toasts');
    var TOAST_MS = 6000;

    function toast(options) {
        if (!region) return null;
        var tone = options.tone || 'info';
        var item = document.createElement('div');
        item.className = 'b-toast b-toast--' + tone + ' is-entering';
        // Errors interrupt; everything else waits its turn.
        item.setAttribute('role', tone === 'critical' ? 'alert' : 'status');

        var body = document.createElement('div');
        var title = document.createElement('p');
        title.className = 'b-toast-title';
        title.textContent = options.title || '';
        body.appendChild(title);
        if (options.text) {
            var text = document.createElement('p');
            text.className = 'b-muted';
            text.textContent = options.text;
            body.appendChild(text);
        }

        var close = document.createElement('button');
        close.type = 'button';
        close.className = 'b-button b-button--quiet b-button--icon b-toast-close';
        close.setAttribute('aria-label', 'Meldung schließen');
        close.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
        close.addEventListener('click', function () { item.remove(); });

        item.appendChild(body);
        item.appendChild(close);
        region.appendChild(item);
        // A failure stays until someone reads it.
        if (tone !== 'critical') setTimeout(function () { item.remove(); }, TOAST_MS);
        return item;
    }

    window.BuilderUI = { toast: toast };

    var DEMO = {
        good: { title: 'Entwurf gespeichert', text: 'Die Änderungen liegen im Entwurf.', tone: 'good' },
        critical: { title: 'Veröffentlichen fehlgeschlagen', text: 'Der Mandant hat nicht geantwortet. Erneut versuchen.', tone: 'critical' }
    };
    each('[data-toast-demo]', function (button) {
        button.addEventListener('click', function () { toast(DEMO[button.getAttribute('data-toast-demo')] || DEMO.good); });
    });

    /* ---- Style guide: which themes ------------------------------------ */

    var themes = document.querySelector('[data-sg-themes]');
    each('[data-sg-view]', function (input) {
        input.addEventListener('change', function () {
            if (input.checked && themes) themes.setAttribute('data-view', input.value);
        });
    });
})();
