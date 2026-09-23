/* Client-side controls for the People master/detail view.
 *
 * The server still owns every write. This module only switches the visible
 * detail form and filters the already-rendered rows, so roles, CSRF and audit
 * behaviour stay exactly where they belong.
 */
(function () {
    'use strict';

    var table = document.getElementById('people-table');
    if (!table) { return; }

    var rows = Array.prototype.slice.call(
        table.querySelectorAll('[data-person-row]')
    );
    var details = Array.prototype.slice.call(
        document.querySelectorAll('[data-person-detail]')
    );
    var search = document.getElementById('people-search');
    var roleFilter = document.getElementById('people-role-filter');
    var statusFilter = document.getElementById('people-status-filter');
    var visibleCount = document.getElementById('people-visible-count');

    function selectPerson(id, focusPanel) {
        var selected = false;
        rows.forEach(function (row) {
            var active = !row.hidden && row.getAttribute('data-person-id') === id;
            row.classList.toggle('is-selected', active);
            if (active) {
                row.setAttribute('aria-selected', 'true');
                selected = true;
            } else {
                row.removeAttribute('aria-selected');
            }
        });
        details.forEach(function (detail) {
            var active = selected && detail.getAttribute('data-person-detail') === id;
            detail.hidden = !active;
            if (active && focusPanel) {
                var heading = detail.querySelector('h2');
                if (heading) {
                    heading.setAttribute('tabindex', '-1');
                    heading.focus();
                }
            }
        });
    }

    function selectedId() {
        var selected = table.querySelector('[data-person-row].is-selected:not([hidden])');
        return selected ? selected.getAttribute('data-person-id') : '';
    }

    function applyFilters() {
        var term = String(search && search.value || '').trim().toLocaleLowerCase('de');
        var role = String(roleFilter && roleFilter.value || '');
        var status = String(statusFilter && statusFilter.value || '');
        var firstVisible = null;
        var count = 0;

        rows.forEach(function (row) {
            var haystack = String(row.getAttribute('data-search') || '')
                .toLocaleLowerCase('de');
            var roles = String(row.getAttribute('data-role') || '').split(/\s+/);
            var matches = (!term || haystack.indexOf(term) !== -1)
                && (!role || roles.indexOf(role) !== -1)
                && (!status || row.getAttribute('data-status') === status);
            row.hidden = !matches;
            if (matches) {
                count += 1;
                if (!firstVisible) { firstVisible = row; }
            }
        });

        if (visibleCount) { visibleCount.textContent = String(count); }
        if (!selectedId()) {
            selectPerson(firstVisible ? firstVisible.getAttribute('data-person-id') : '', false);
        }
    }

    rows.forEach(function (row) {
        var button = row.querySelector('[data-person-select]');
        if (!button) { return; }
        button.addEventListener('click', function () {
            selectPerson(button.getAttribute('data-person-select') || '', true);
        });
    });

    [search, roleFilter, statusFilter].forEach(function (control) {
        if (!control) { return; }
        control.addEventListener(control === search ? 'input' : 'change', applyFilters);
    });

    var dialog = document.getElementById('person-create-dialog');
    document.querySelectorAll('[data-person-create-open]').forEach(function (button) {
        button.addEventListener('click', function () {
            if (!dialog) { return; }
            if (typeof dialog.showModal === 'function') {
                dialog.showModal();
            } else {
                dialog.setAttribute('open', '');
            }
        });
    });
    document.querySelectorAll('[data-person-create-close]').forEach(function (button) {
        button.addEventListener('click', function () {
            if (!dialog) { return; }
            if (typeof dialog.close === 'function') {
                dialog.close();
            } else {
                dialog.removeAttribute('open');
            }
        });
    });

    applyFilters();
}());
