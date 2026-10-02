/* Cursor-fed document list.
 *
 * The inventory pages by keyset: the server hands back `next_after`, we hand
 * it straight back on the next request. No page numbers, because a page
 * number implies an offset, and an offset walk fails on a large tenant.
 *
 * Rows are built with DOM methods and textContent only. Pointers and titles
 * come from the corpus, which is user-supplied data; nothing here is ever
 * parsed as HTML.
 *
 * Document fields (second block below), only when the page renders them:
 * the fields drawer and the Feldfilter. Both POST JSON with the pointer in
 * the body and the X-CSRF-Token header; every decision -- what is shown as
 * filtered, which notice, who may edit -- comes from the server's answer.
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
  var table = document.getElementById('doc-table');

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
    if (table && table.getAttribute('data-doc-fields') === '1') {
      tr.appendChild(fieldsCell(doc));
    }
    return tr;
  }

  function fieldsCell(doc) {
    var td = cell('row-action');
    var open = document.createElement('button');
    open.type = 'button';
    open.className = 'secondary compact';
    open.setAttribute('data-df-open', '');
    open.setAttribute('data-pointer', String(doc.pointer == null ? '' : doc.pointer));
    var pointers = (doc.pointers && doc.pointers.length) ? doc.pointers : [doc.pointer];
    open.setAttribute('data-pointers', JSON.stringify(pointers.map(String)));
    open.textContent = 'Felder';
    td.appendChild(open);
    return td;
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

/* Document fields: the drawer and the Feldfilter (spec 4.7). */
(function () {
  'use strict';

  var drawer = document.getElementById('doc-fields-drawer');
  var filter = document.getElementById('doc-fields-filter');
  if (!drawer && !filter) { return; }

  var csrf = drawer ? drawer.getAttribute('data-csrf') : '';
  if (!csrf) {
    var tokenInput = document.querySelector('input[name="csrf_token"]');
    csrf = tokenInput ? tokenInput.value : '';
  }

  function postJson(url, body) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json',
                 'X-CSRF-Token': csrf },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        return { status: r.status, data: data || {} };
      });
    });
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text != null) { node.textContent = String(text); }
    return node;
  }

  function day(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    return m ? (m[3] + '.' + m[2] + '.' + m[1]) : '';
  }

  // -- Drawer ---------------------------------------------------------------
  if (drawer) {
    var readUrl = drawer.getAttribute('data-read');
    var editUrl = drawer.getAttribute('data-edit');
    var body = drawer.querySelector('[data-df-body]');
    var message = drawer.querySelector('[data-df-message]');
    var pointerLine = drawer.querySelector('[data-df-pointer]');
    var picker = drawer.querySelector('[data-df-pointer-picker]');
    var pickerSelect = drawer.querySelector('[data-df-pointer-select]');
    var current = null;     // the view on screen
    var pointer = '';

    var say = function (text, kind) {
      message.hidden = !text;
      message.className = 'msg' + (kind ? ' ' + kind : '');
      message.textContent = text || '';
    };

    var inputFor = function (spec) {
      var control;
      if (spec.kind === 'select' || spec.kind === 'bool') {
        control = document.createElement('select');
        var options = spec.kind === 'bool'
          ? [{ code: 'true', label: 'Ja' }, { code: 'false', label: 'Nein' }]
          : (spec.options || []);
        var blank = el('option', null, '—');
        blank.value = '';
        control.appendChild(blank);
        options.forEach(function (o) {
          var opt = el('option', null, o.label || o.code);
          opt.value = String(o.code);
          control.appendChild(opt);
        });
        control.value = String(spec.value || '');
      } else {
        control = document.createElement('input');
        control.type = 'text';
        control.value = String(spec.value || '');
        if (spec.cardinality === 'many') { control.placeholder = 'mehrere mit ; trennen'; }
      }
      control.setAttribute('data-df-key', spec.key);
      control.setAttribute('data-df-original', control.value);
      control.setAttribute('data-df-kind', spec.kind || 'text');
      control.setAttribute('data-df-many', spec.cardinality === 'many' ? '1' : '');
      if (spec.locked || !current.can_edit) { control.disabled = true; }
      return control;
    };

    var noteFor = function (key, warnings) {
      return (warnings || []).filter(function (w) { return w.key === key; })
        .map(function (w) { return w.text; }).join('; ');
    };

    var render = function (view, warnings) {
      current = view;
      body.textContent = '';
      pointerLine.textContent = view.pointer || pointer;
      if (view.held) {
        say(view.message, 'warn');
        return;
      }
      var inputs = {};
      (view.inputs || []).forEach(function (spec) { inputs[spec.key] = spec; });
      var editable = (view.editable_keys || []);

      // Title and description.
      var head = el('section', 'doc-fields-drawer-section');
      head.appendChild(el('h3', null, 'Titel'));
      if (editable.indexOf('title') !== -1) {
        var title = el('input');
        title.type = 'text';
        title.maxLength = 500;
        title.value = view.title || '';
        title.setAttribute('data-df-key', 'title');
        title.setAttribute('data-df-original', title.value);
        title.setAttribute('data-df-kind', 'title');
        head.appendChild(title);
      } else {
        head.appendChild(el('p', null, view.title || '—'));
      }
      head.appendChild(el('p', 'hint', view.title_hint || ''));
      head.appendChild(el('h3', null, 'Beschreibung'));
      if (editable.indexOf('description') !== -1) {
        var desc = el('textarea');
        desc.rows = 3;
        desc.maxLength = 2000;
        desc.value = view.description || '';
        desc.setAttribute('data-df-key', 'description');
        desc.setAttribute('data-df-original', desc.value);
        desc.setAttribute('data-df-kind', 'description');
        head.appendChild(desc);
      } else {
        head.appendChild(el('p', null, view.description || '—'));
      }
      body.appendChild(head);

      // Values, each with its layers.
      var list = el('dl', 'doc-fields-values');
      var shown = {};
      (view.fields || []).forEach(function (f) {
        shown[f.key] = true;
        list.appendChild(el('dt', null, f.label));
        var dd = el('dd');
        var spec = inputs[f.key];
        if (spec) { dd.appendChild(inputFor(spec)); }
        else { dd.appendChild(el('span', null, f.text || '—')); }
        if (f.layer_label) { dd.appendChild(el('span', 'field-badge layer-' + f.layer, f.layer_label)); }
        if (f.changed_at) {
          dd.appendChild(el('span', 'sub', 'zuletzt manuell geändert am ' + day(f.changed_at)));
        }
        if (spec && spec.hint) { dd.appendChild(el('span', 'sub', spec.hint)); }
        var note = noteFor(f.key, warnings);
        if (note) { dd.appendChild(el('span', 'sub warn-text', note)); }
        if (f.has_manual && spec && view.can_edit) {
          var revert = el('button', 'secondary compact', 'zurück zum Upload-/Ordnerwert');
          revert.type = 'button';
          revert.addEventListener('click', function () {
            var change = {}; change[f.key] = null;
            save({ set: change });
          });
          dd.appendChild(revert);
        }
        if ((f.layers || []).length > 1) {
          var details = el('details', 'doc-fields-layers');
          details.appendChild(el('summary', null, 'Ebenen'));
          var ul = el('ul');
          f.layers.forEach(function (layer) {
            var li = el('li', layer.effective ? 'is-effective' : '');
            li.appendChild(el('span', 'field-badge layer-' + layer.layer, layer.layer_label));
            li.appendChild(document.createTextNode(' ' + (layer.text || '—')));
            if (layer.changed_at) { li.appendChild(el('span', 'sub', ' ' + day(layer.changed_at))); }
            ul.appendChild(li);
          });
          details.appendChild(ul);
          dd.appendChild(details);
        }
        list.appendChild(dd);
      });
      (view.inputs || []).forEach(function (spec) {
        if (shown[spec.key]) { return; }
        list.appendChild(el('dt', null, spec.label));
        var dd = el('dd');
        dd.appendChild(inputFor(spec));
        if (spec.hint) { dd.appendChild(el('span', 'sub', spec.hint)); }
        var note = noteFor(spec.key, warnings);
        if (note) { dd.appendChild(el('span', 'sub warn-text', note)); }
        list.appendChild(dd);
      });
      body.appendChild(list);

      if (view.can_edit) {
        var saveButton = el('button', null, 'Speichern');
        saveButton.type = 'button';
        saveButton.addEventListener('click', function () { save(collect()); });
        body.appendChild(saveButton);
      } else {
        body.appendChild(el('p', 'hint', 'Nur lesen: Ihre Rolle darf diese Werte nicht ändern.'));
      }
    };

    var collect = function () {
      var change = { set: {}, unset: [] };
      body.querySelectorAll('[data-df-key]').forEach(function (input) {
        if (input.disabled) { return; }
        var key = input.getAttribute('data-df-key');
        var value = String(input.value || '');
        if (value === input.getAttribute('data-df-original')) { return; }
        var kind = input.getAttribute('data-df-kind');
        if (kind === 'title') { change.set.title = value.trim() ? value : null; return; }
        if (kind === 'description') { change.set.description = value; return; }
        if (!value.trim()) { change.unset.push(key); return; }
        if (kind === 'bool') { change.set[key] = value === 'true'; return; }
        if (input.getAttribute('data-df-many')) {
          change.set[key] = value.split(';').map(function (v) { return v.trim(); })
            .filter(function (v) { return v; });
          return;
        }
        change.set[key] = value;
      });
      if (!Object.keys(change.set).length) { delete change.set; }
      if (!change.unset.length) { delete change.unset; }
      return change;
    };

    var save = function (change) {
      if (!change.set && !change.unset) { say('Keine Änderung.', ''); return; }
      var request = { pointer: pointer, if_version: current ? current.version : 0 };
      if (change.set) { request.set = change.set; }
      if (change.unset) { request.unset = change.unset; }
      say('Wird gespeichert …', '');
      postJson(editUrl, request).then(function (res) {
        var data = res.data;
        if (data.success) {
          if (data.view) { render(data.view, data.warnings); }
          say((data.warnings || []).length ? 'Gespeichert, mit Hinweisen.' : 'Gespeichert.', 'ok');
          return;
        }
        if (data.view) { render(data.view, []); }
        if (data.error === 'version_conflict') {
          say('Die Werte wurden inzwischen geändert. Die aktuelle Fassung ist geladen – bitte prüfen und erneut speichern.', 'warn');
        } else if (data.read_only) {
          if (current) { current.can_edit = false; render(current, []); }
          say(data.message || 'Nur lesen.', 'warn');
        } else {
          say(data.message || 'Nicht gespeichert.', 'error');
        }
      }).catch(function () { say('Nicht gespeichert: keine Verbindung.', 'error'); });
    };

    var read = function (p) {
      pointer = p;
      body.textContent = '';
      pointerLine.textContent = p;
      say('Wird geladen …', '');
      postJson(readUrl, { pointer: p }).then(function (res) {
        if (res.data.success) { say('', ''); render(res.data, []); }
        else { say(res.data.message || 'Nicht abrufbar.', 'error'); }
      }).catch(function () { say('Nicht abrufbar: keine Verbindung.', 'error'); });
    };

    document.addEventListener('click', function (event) {
      var open = event.target.closest('[data-df-open]');
      if (!open) { return; }
      var pointers = [];
      try { pointers = JSON.parse(open.getAttribute('data-pointers') || '[]') || []; } catch (e) { pointers = []; }
      var first = open.getAttribute('data-pointer') || pointers[0] || '';
      pickerSelect.textContent = '';
      pointers.forEach(function (p) {
        var opt = el('option', null, p);
        opt.value = p;
        pickerSelect.appendChild(opt);
      });
      picker.hidden = pointers.length < 2;
      pickerSelect.value = first;
      drawer.hidden = false;
      read(first);
    });
    pickerSelect.addEventListener('change', function () { read(pickerSelect.value); });
    drawer.querySelector('[data-df-close]').addEventListener('click', function () {
      drawer.hidden = true;
      body.textContent = '';
      current = null;
    });
  }

  // -- Feldfilter -------------------------------------------------------------
  if (filter) {
    var endpoint = filter.getAttribute('data-endpoint');
    var formEl = filter.querySelector('[data-df-filter-form]');
    var rowsEl = filter.querySelector('[data-df-filter-rows]');
    var results = filter.querySelector('[data-df-filter-results]');
    var more = filter.querySelector('[data-df-filter-more]');
    var statusEl = filter.querySelector('[data-df-filter-status]');
    var banner = filter.querySelector('[data-df-filter-banner]');
    var chips = filter.querySelector('[data-df-filter-chips]');
    var fields = [];
    try {
      fields = JSON.parse((document.getElementById('doc-fields-filter-fields') || {}).textContent || '[]') || [];
    } catch (e) { fields = []; }
    var byKey = {};
    fields.forEach(function (f) { byKey[f.key] = f; });
    var lastRequest = null;
    var withDrawer = !!drawer;

    filter.querySelectorAll('[data-df-filter-row]').forEach(function (row) {
      var select = row.querySelector('[data-df-filter-field]');
      select.addEventListener('change', function () {
        var old = row.querySelector('[data-df-filter-value]');
        var spec = byKey[select.value];
        var control;
        if (spec && (spec.datatype === 'enum' || spec.datatype === 'bool')) {
          control = document.createElement('select');
          var options = spec.datatype === 'bool'
            ? [{ code: 'true', label: 'Ja' }, { code: 'false', label: 'Nein' }] : (spec.enum || []);
          options.forEach(function (o) {
            var opt = el('option', null, o.label || o.code);
            opt.value = String(o.code);
            control.appendChild(opt);
          });
        } else {
          control = document.createElement('input');
          control.type = 'text';
          control.autocomplete = 'off';
          if (spec && spec.datatype === 'entity_ref') { control.placeholder = 'Name, z. B. Muster AG'; }
          if (spec && (spec.datatype === 'date' || spec.datatype === 'period')) {
            control.placeholder = 'z. B. 2024, Q1 2024, GJ 2024';
          }
        }
        control.setAttribute('data-df-filter-value', '');
        old.parentNode.replaceChild(control, old);
      });
    });

    var renderRows = function (documents) {
      documents.forEach(function (doc) {
        var tr = el('tr');
        var titleCell = el('td');
        titleCell.appendChild(el('strong', null, doc.title || '—'));
        tr.appendChild(titleCell);
        tr.appendChild(el('td', 'ptr', doc.pointer));
        var values = el('td');
        (doc.fields_display || []).forEach(function (f) {
          var chip = el('span', 'field-chip');
          chip.appendChild(el('span', 'field-chip-label', f.label));
          chip.appendChild(document.createTextNode(' ' + f.text));
          values.appendChild(chip);
        });
        tr.appendChild(values);
        var action = el('td', 'row-action');
        if (withDrawer) {
          var open = el('button', 'secondary compact', 'Felder');
          open.type = 'button';
          open.setAttribute('data-df-open', '');
          open.setAttribute('data-pointer', String(doc.pointer || ''));
          open.setAttribute('data-pointers', JSON.stringify([String(doc.pointer || '')]));
          action.appendChild(open);
        }
        tr.appendChild(action);
        rowsEl.appendChild(tr);
      });
    };

    var load = function (request, emptyRun) {
      more.disabled = true;
      statusEl.textContent = 'Wird geladen …';
      postJson(endpoint, request).then(function (res) {
        var data = res.data;
        if (!data.success) {
          rowsEl.textContent = '';
          results.hidden = true;
          more.hidden = true;
          statusEl.textContent = data.message || 'Nicht abrufbar.';
          return;
        }
        if (!request.after) {
          rowsEl.textContent = '';
          chips.textContent = (data.resolved || []).length
            ? 'Verstanden als: ' + data.resolved.map(function (c) {
                return c.label + ': ' + c.text + (c.linked_text ? ' (' + c.linked_text + ')' : '');
              }).join(' · ')
            : '';
          banner.hidden = !data.deadline_banner;
          banner.textContent = data.deadline_banner || '';
        }
        results.hidden = false;
        renderRows(data.documents || []);
        var notice = data.notice || {};
        var parts = [];
        if (typeof notice.total_count === 'number') { parts.push(notice.total_count + ' Dokument(e)'); }
        if (notice.text) { parts.push(notice.text); }
        lastRequest = request;
        if (data.next_after) {
          lastRequest = Object.assign({}, request, { after: data.next_after });
          // A page may be empty while the walk goes on; keep walking a
          // little before asking the person.
          if (!(data.documents || []).length && (emptyRun || 0) < 5) {
            load(lastRequest, (emptyRun || 0) + 1);
            return;
          }
          more.hidden = false;
        } else {
          more.hidden = true;
          if (!rowsEl.children.length) {
            // The server words the empty state: "no document" only when
            // Knovas called the walk complete (H8, H9).
            if (data.empty_text) {
              parts = parts.filter(function (part) { return part !== notice.text; });
            }
            parts.push(data.empty_text || 'Kein Dokument angezeigt.');
          }
        }
        statusEl.textContent = parts.join(' – ');
      }).catch(function () {
        statusEl.textContent = 'Nicht abrufbar: keine Verbindung.';
      }).finally(function () { more.disabled = false; });
    };

    formEl.addEventListener('submit', function (event) {
      event.preventDefault();
      var pairs = [];
      filter.querySelectorAll('[data-df-filter-row]').forEach(function (row) {
        var key = row.querySelector('[data-df-filter-field]').value;
        var value = row.querySelector('[data-df-filter-value]').value;
        if (key && String(value || '').trim()) { pairs.push({ field: key, value: String(value) }); }
      });
      if (!pairs.length) { statusEl.textContent = 'Bitte mindestens ein Feld mit einem Wert angeben.'; return; }
      var request = { pairs: pairs };
      var sortField = filter.querySelector('[data-df-filter-sort]').value;
      if (sortField) {
        request.sort = { field: sortField, order: filter.querySelector('[data-df-filter-order]').value };
      }
      load(request, 0);
    });

    more.addEventListener('click', function () { if (lastRequest) { load(lastRequest, 0); } });
  }
}());
