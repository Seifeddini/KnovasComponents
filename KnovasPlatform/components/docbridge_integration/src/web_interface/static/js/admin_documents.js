/* Cursor-fed document list.
 *
 * The inventory pages by keyset: the server hands back `next_after`, we hand
 * it straight back on the next request. No page numbers, because a page
 * number implies an offset, and an offset walk fails on a large tenant.
 *
 * Rows are built with DOM methods and textContent only. Pointers and titles
 * come from the corpus, which is user-supplied data; nothing here is ever
 * parsed as HTML.
 */
(function () {
  'use strict';

  var button = document.getElementById('load-more');
  if (!button) { return; }
  var rows = document.getElementById('doc-rows');
  var status = document.getElementById('load-status');
  var selectAll = document.getElementById('document-select-all');
  var bulkBar = document.getElementById('document-bulk-bar');
  var selectedCount = document.getElementById('document-selected-count');
  var clearSelection = document.getElementById('document-clear-selection');

  function cell(className) {
    var td = document.createElement('td');
    if (className) { td.className = className; }
    return td;
  }

  function textCell(text, className) {
    var td = cell(className);
    td.textContent = (text == null || text === '') ? '—' : String(text);
    return td;
  }

  function badge(text, open) {
    var span = document.createElement('span');
    span.className = open ? 'access-chip open' : 'access-chip';
    span.textContent = text;
    return span;
  }

  function groupsCell(groups) {
    var td = cell();
    if (!groups || !groups.length) {
      td.appendChild(badge('offen', true));
      return td;
    }
    groups.forEach(function (g) { td.appendChild(badge(String(g), false)); });
    return td;
  }

  function checkboxCell(pointer, label) {
    var td = cell('select-column');
    var box = document.createElement('input');
    box.type = 'checkbox';
    box.name = 'pointer';
    box.value = String(pointer == null ? '' : pointer);
    box.setAttribute('aria-label', String(label || pointer || 'Dokument') + ' auswählen');
    td.appendChild(box);
    return td;
  }

  function renderRow(doc) {
    var tr = document.createElement('tr');
    tr.appendChild(checkboxCell(doc.pointer, doc.title));
    var titleCell = cell();
    var title = document.createElement('strong');
    title.textContent = (doc.title == null || doc.title === '') ? '—' : String(doc.title);
    titleCell.appendChild(title);
    tr.appendChild(titleCell);
    tr.appendChild(textCell(doc.pointer, 'ptr'));
    tr.appendChild(groupsCell(doc.access_groups));
    var statusCell = cell();
    var statusBadge = document.createElement('span');
    statusBadge.className = 'document-status';
    statusBadge.textContent = (doc.status == null || doc.status === '') ? '—' : String(doc.status);
    statusCell.appendChild(statusBadge);
    tr.appendChild(statusCell);
    return tr;
  }

  function pointerBoxes() {
    return Array.prototype.slice.call(
      rows.querySelectorAll('input[type="checkbox"][name="pointer"]')
    );
  }

  function syncSelection() {
    var boxes = pointerBoxes();
    var checked = boxes.filter(function (box) { return box.checked; }).length;
    if (selectedCount) { selectedCount.textContent = String(checked); }
    if (bulkBar) { bulkBar.hidden = checked === 0; }
    if (selectAll) {
      selectAll.checked = boxes.length > 0 && checked === boxes.length;
      selectAll.indeterminate = checked > 0 && checked < boxes.length;
    }
  }

  rows.addEventListener('change', function (event) {
    if (event.target.matches('input[type="checkbox"][name="pointer"]')) {
      syncSelection();
    }
  });

  if (selectAll) {
    selectAll.addEventListener('change', function () {
      pointerBoxes().forEach(function (box) { box.checked = selectAll.checked; });
      syncSelection();
    });
  }

  if (clearSelection) {
    clearSelection.addEventListener('click', function () {
      pointerBoxes().forEach(function (box) { box.checked = false; });
      syncSelection();
    });
  }

  button.addEventListener('click', function () {
    var after = button.getAttribute('data-next-after');
    if (!after) { return; }
    var url = new URL(button.getAttribute('data-endpoint'), window.location.origin);
    url.searchParams.set('after', after);
    // Carry the active filters so paging stays inside the same result set.
    new URLSearchParams(window.location.search).forEach(function (v, k) {
      if (k !== 'after') { url.searchParams.set(k, v); }
    });

    button.disabled = true;
    status.textContent = 'Wird geladen …';

    fetch(url.toString(), { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (page) {
        (page.documents || []).forEach(function (doc) {
          rows.appendChild(renderRow(doc));
        });
        syncSelection();
        if (page.next_after) {
          button.setAttribute('data-next-after', page.next_after);
          status.textContent = '';
        } else {
          button.hidden = true;
          status.textContent = 'Alle Dokumente geladen.';
        }
      })
      .catch(function () {
        status.textContent = 'Nachladen fehlgeschlagen. Bitte erneut versuchen.';
      })
      .finally(function () { button.disabled = false; });
  });

  syncSelection();
}());
