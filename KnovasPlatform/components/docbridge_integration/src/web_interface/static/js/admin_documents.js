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
    span.className = open ? 'badge open' : 'badge';
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

  function checkboxCell(pointer) {
    var td = cell();
    var box = document.createElement('input');
    box.type = 'checkbox';
    box.name = 'pointer';
    box.value = String(pointer == null ? '' : pointer);
    td.appendChild(box);
    return td;
  }

  function renderRow(doc) {
    var tr = document.createElement('tr');
    tr.appendChild(checkboxCell(doc.pointer));
    tr.appendChild(textCell(doc.title));
    tr.appendChild(textCell(doc.pointer, 'ptr'));
    tr.appendChild(groupsCell(doc.access_groups));
    tr.appendChild(textCell(doc.status));
    return tr;
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
}());
