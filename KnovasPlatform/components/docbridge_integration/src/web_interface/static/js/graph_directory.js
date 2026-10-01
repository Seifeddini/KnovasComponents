/* Verzeichnis -> Liste -> Karte, and the Wissensnetz of an entry.
 *
 * Two pages share this file: /verzeichnis/<slug> (every entry of one type,
 * searchable) and the card of one entry (/verzeichnis/<slug>/<id> or
 * /eintrag/<id>). Both are generated from the type's fields; no type name
 * appears here.
 */
(function () {
  'use strict';

  const KG = window.KG;
  const root = document.getElementById('kg-root');
  if (!KG || !root || root.querySelector('[data-kg-unavailable]')) { return; }
  const esc = KG.esc;
  const page = root.dataset.kgPage;
  const SEARCH_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/>' +
    '<path d="m21 21-4.3-4.3"/></svg>';
  const NET_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" aria-hidden="true" style="vertical-align:-2px">' +
    '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>' +
    '<line x1="8.6" y1="13.5" x2="15.4" y2="17.5"/><line x1="15.4" y1="6.5" x2="8.6" y2="10.5"/></svg>';

  /* Every entry this person may see, for pickers and the network search.
     Loaded once, on first need. */
  let everyone = null;
  async function candidates() {
    if (!everyone) {
      const [nodes, types] = await Promise.all([KG.api('/api/graph/nodes'), KG.api('/api/graph/node-types')]);
      const byId = {};
      (types.node_types || []).forEach((t) => { byId[t.id] = t; });
      everyone = { nodes: nodes.nodes || [], types: byId };
    }
    return everyone;
  }

  // ═══ Verzeichnis ════════════════════════════════════════════════════════

  const list = { slug: root.dataset.slug, data: null, query: '', onlyGaps: false, flash: '', loading: 0 };

  async function openDirectory() {
    list.onlyGaps = new URLSearchParams(location.search).get('luecken') === '1';
    list.flash = KG.takeFlash();
    try {
      list.data = await KG.api('/api/graph/directories/' + encodeURIComponent(list.slug));
    } catch (error) {
      root.innerHTML = '<h1>Verzeichnis</h1>' + KG.msgHTML(error.message, 'err');
      return;
    }
    if (list.data.inactive) {
      root.innerHTML = '<h1>' + esc(list.data.view.title) + '</h1><div class="kg-panel"><h2>Diese Liste ' +
        'ist derzeit abgeschaltet</h2><p class="kg-hint" style="margin:0">Ein Administrator hat sie in der ' +
        'Verwaltung unter <em>Verzeichnisse</em> abgeschaltet. Die Einträge selbst sind unverändert — nur ' +
        'diese Sicht auf sie ist aus.</p></div>';
      return;
    }
    renderDirectory();
    loadPendingRows();
  }

  const gapRows = () => list.data.rows.filter((r) => r.gaps && r.gaps.length);

  function visibleRows() {
    const q = list.query.toLowerCase();
    return list.data.rows.filter((r) => (!q || r.name.toLowerCase().indexOf(q) >= 0) &&
      (!list.onlyGaps || (r.gaps && r.gaps.length)));
  }

  function cellHTML(column, row) {
    if (!row.loaded) { return '<td><span class="kg-shimmer" aria-label="wird geladen"></span></td>'; }
    const cell = (row.cells || {})[column.id];
    const cls = column.datatype === 'money' ? ' class="kg-num"' : '';
    if (!cell || cell.missing) {
      return '<td>' + (column.required
        ? '<span class="kg-pill kg-pill--gap" title="Pflichtfeld ohne Wert">fehlt</span>'
        : '<span class="kg-gap">—</span>') + '</td>';
    }
    return '<td' + cls + '>' + esc(cell.display) + '</td>';
  }

  function rowsHTML() {
    const rows = visibleRows();
    const cols = list.data.columns;
    if (!rows.length) {
      const text = !list.data.rows.length
        ? 'Noch kein Eintrag. „Neuer Eintrag“ legt den ersten an.'
        : list.query ? 'Kein Eintrag, dessen Name „' + list.query + '“ enthält.'
          : 'Kein Eintrag mit einer Lücke in einem Pflichtfeld.';
      return '<tr><td class="kg-empty" colspan="' + (cols.length + 2) + '">' + esc(text) + '</td></tr>';
    }
    return rows.map((r) => '<tr data-id="' + esc(r.id) + '" tabindex="0">' +
      '<td><strong>' + esc(r.name) + '</strong></td>' + cols.map((c) => cellHTML(c, r)).join('') +
      '<td class="kg-num"><span class="kg-sub-inline">' + (r.connections == null ? '—' : r.connections + ' Verb.') +
      '</span></td></tr>').join('');
  }

  function countText() {
    const shown = visibleRows().length, all = list.data.rows.length;
    return shown + ' von ' + all + ' ' + (all === 1 ? 'Eintrag' : 'Einträgen');
  }

  function pendingNote() {
    const pending = list.data.rows.filter((r) => !r.loaded).length;
    if (!pending) { return ''; }
    return 'Feldwerte werden nachgeladen — noch ' + pending + ' von ' + list.data.rows.length +
      '. Die Knovas-API bietet die Sammelabfrage der Werte noch nicht an; die Plattform liest die ' +
      'Einträge deshalb einzeln.';
  }

  function renderDirectory() {
    const d = list.data;
    const gaps = gapRows().length;
    root.innerHTML =
      '<h1>' + esc(d.view.title) + '</h1>' +
      '<p class="kg-hint">' + esc(d.view.subtitle || 'Alle Einträge dieses Wissenstyps.') + '</p>' +
      KG.msgHTML(list.flash) +
      '<div class="kg-bar">' +
        '<label class="kg-search"><span class="kg-sr">Einträge durchsuchen</span>' + SEARCH_ICON +
          '<input type="search" id="kg-q" placeholder="Nach Name suchen …" autocomplete="off" value="' +
          esc(list.query) + '"></label>' +
        '<button type="button" class="kg-btn" id="kg-new">Neuer Eintrag</button>' +
        '<label class="kg-onlygaps" id="kg-gapwrap"' + (gaps ? '' : ' hidden') + '><input type="checkbox" id="kg-gaps"' +
          (list.onlyGaps ? ' checked' : '') + '> nur mit Lücke <span class="kg-pill kg-pill--gap" id="kg-gapn">' +
          gaps + '</span></label>' +
        '<span class="kg-count" id="kg-count">' + esc(countText()) + '</span>' +
      '</div>' +
      '<p class="kg-note">Die Suche liest den <strong>Namen</strong> eines Eintrags, nicht den Inhalt seiner Felder.</p>' +
      '<p class="kg-note" id="kg-pending">' + esc(pendingNote()) + '</p>' +
      '<div class="kg-table-wrap"><table><thead><tr><th>Name</th>' +
        d.columns.map((c) => '<th>' + esc(c.name) + '</th>').join('') + '<th>Netz</th></tr></thead>' +
        '<tbody id="kg-rows">' + rowsHTML() + '</tbody></table></div>';

    const q = document.getElementById('kg-q');
    q.addEventListener('input', () => { list.query = q.value.trim(); refreshRows(); });
    document.getElementById('kg-gaps').addEventListener('change', (e) => {
      list.onlyGaps = e.target.checked; refreshRows();
    });
    document.getElementById('kg-new').addEventListener('click', openNewEntry);
    const body = document.getElementById('kg-rows');
    const open = (tr) => { location.href = KG.entryHref(tr.dataset.id, list.slug); };
    body.addEventListener('click', (e) => { const tr = e.target.closest('tr[data-id]'); if (tr) { open(tr); } });
    body.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') { return; }
      const tr = e.target.closest('tr[data-id]');
      if (tr) { e.preventDefault(); open(tr); }
    });
  }

  /* Only the rows and the counters: rebuilding the page on every keystroke
     would take the focus out of the search field. */
  function refreshRows() {
    document.getElementById('kg-rows').innerHTML = rowsHTML();
    document.getElementById('kg-count').textContent = countText();
    const gaps = gapRows().length;
    document.getElementById('kg-gapn').textContent = gaps;
    document.getElementById('kg-gapwrap').hidden = !gaps && !list.onlyGaps;
    document.getElementById('kg-pending').textContent = pendingNote();
  }

  async function loadPendingRows() {
    const pending = (list.data.pending || []).slice();
    while (pending.length) {
      const batch = pending.splice(0, 6);
      try {
        const body = await KG.api('/api/graph/directories/' + encodeURIComponent(list.slug) +
          '/rows?ids=' + batch.map(encodeURIComponent).join(','));
        const fresh = {};
        (body.rows || []).forEach((r) => { fresh[r.id] = r; });
        list.data.rows = list.data.rows.map((r) => fresh[r.id] || r);
      } catch (error) {
        document.getElementById('kg-pending').textContent =
          'Weitere Feldwerte konnten nicht geladen werden: ' + error.message;
        return;
      }
      refreshRows();
    }
  }

  // ── Neuer Eintrag ─────────────────────────────────────────────────────

  async function openNewEntry() {
    const d = list.data;
    const dialog = document.createElement('dialog');
    dialog.className = 'kg kg-dialog';
    dialog.setAttribute('aria-labelledby', 'kg-dlg-title');
    dialog.innerHTML = '<h2 id="kg-dlg-title">Neuer Eintrag · ' + esc(d.view.title) + '</h2>' +
      '<p class="kg-hint" style="margin-bottom:10px">Die Felder unten sind die dieses Wissenstyps. Ein ' +
      'Pflichtfeld ohne Wert hält das Speichern nicht auf — die Lücke ist danach sichtbar und kann ' +
      'jederzeit gefüllt werden.</p><div id="kg-dlg-err"></div>' +
      '<label class="kg-label" for="kg-dlg-name">Name</label><input type="text" id="kg-dlg-name" autocomplete="off">' +
      '<div id="kg-dlg-fields"><p class="kg-loading">Felder werden geladen …</p></div>' +
      '<div class="kg-dialog-actions"><button type="button" class="kg-btn kg-btn--ghost" id="kg-dlg-cancel">' +
      'Abbrechen</button><button type="button" class="kg-btn" id="kg-dlg-save">Anlegen</button></div>';
    document.body.appendChild(dialog);
    dialog.addEventListener('close', () => dialog.remove());
    dialog.querySelector('#kg-dlg-cancel').addEventListener('click', () => dialog.close());
    dialog.showModal();
    dialog.querySelector('#kg-dlg-name').focus();

    const ctx = d.attributes.some((a) => a.datatype === 'entity_ref')
      ? await candidates().then((c) => ({ candidates: c.nodes, types: c.types })).catch(() => ({}))
      : {};
    const host = dialog.querySelector('#kg-dlg-fields');
    host.innerHTML = '';
    const readers = d.attributes.map((attribute) => {
      const wrap = document.createElement('div');
      wrap.className = 'kg-dlgfield';
      const built = KG.control(attribute, null, ctx);
      wrap.innerHTML = '<label class="kg-cap" for="' + esc(built.focus.id) + '">' + esc(attribute.name) +
        (attribute.required ? ' <span class="kg-req">Pflicht</span>' : '') + '</label>' +
        (attribute.description ? '<div class="kg-sub" style="margin-bottom:4px">' + esc(attribute.description) + '</div>' : '');
      wrap.appendChild(built.node);
      host.appendChild(wrap);
      return () => [attribute, built.read()];
    });

    dialog.querySelector('#kg-dlg-save').addEventListener('click', async (event) => {
      const err = dialog.querySelector('#kg-dlg-err');
      const name = dialog.querySelector('#kg-dlg-name').value.trim();
      if (!name) { err.innerHTML = KG.msgHTML('Der Eintrag braucht einen Namen.', 'err'); return; }
      const facts = {};
      try {
        readers.forEach((read) => { const [a, v] = read(); if (v !== null) { facts[a.id] = v; } });
      } catch (error) { err.innerHTML = KG.msgHTML(error.message, 'err'); return; }
      event.target.disabled = true;
      try {
        const body = await KG.api('/api/graph/nodes', { method: 'POST',
          body: { name: name, node_type_id: d.type.id, facts: facts } });
        const missing = d.attributes.filter((a) => a.required && !(a.id in facts)).map((a) => a.name);
        KG.carryFlash('„' + name + '“ angelegt.' +
          (missing.length ? ' Ohne Angabe: ' + missing.join(', ') + '.' : '') +
          (body.problems && body.problems.length ? ' Nicht übernommen — ' + body.problems.join(' · ') : ''));
        location.href = KG.entryHref(body.node.id, list.slug);
      } catch (error) {
        err.innerHTML = KG.msgHTML(error.message, 'err');
        event.target.disabled = false;
      }
    });
  }

  // ═══ Karte eines Eintrags ═══════════════════════════════════════════════

  const TABS = ['felder', 'netz', 'dokumente', 'verlauf'];
  const entry = { id: root.dataset.node, data: null, tab: 'felder', flash: '', net: null, depth: 1,
                  history: null, busy: false };

  async function openEntry() {
    const hash = location.hash.slice(1);
    entry.tab = TABS.indexOf(hash) >= 0 ? hash : 'felder';
    entry.flash = KG.takeFlash();
    try {
      entry.data = await KG.api('/api/graph/nodes/' + encodeURIComponent(entry.id));
    } catch (error) {
      root.innerHTML = error.status === 404
        ? '<h1>Eintrag nicht gefunden</h1><p class="kg-hint">Diesen Eintrag gibt es nicht, oder Ihre ' +
          'Freigaben zeigen ihn nicht.</p>'
        : '<h1>Eintrag</h1>' + KG.msgHTML(error.message, 'err');
      return;
    }
    const view = entry.data.view;
    if (view && view.active) {
      // One address per card: the directory's, so a link names its list.
      const canonical = KG.entryHref(entry.id, view.slug);
      if (location.pathname !== canonical) { history.replaceState(null, '', canonical + location.hash); }
    }
    document.title = entry.data.node.name + ' · ' + document.title.split(' · ').pop();
    render();
    if (entry.tab === 'netz') { showNetwork(entry.depth); }
    if (entry.tab === 'verlauf') { showHistory(); }
  }

  const attrs = () => {
    const map = {};
    (entry.data.attributes || []).forEach((a) => { map[a.id] = a; });
    return map;
  };

  function render() {
    const d = entry.data;
    const view = d.view && d.view.active ? d.view : null;
    const tabs = [['felder', 'Felder', d.attributes.length],
      ['netz', 'Wissensnetz', d.connections.length],
      ['dokumente', 'Dokumente', d.documents.length],
      ['verlauf', 'Verlauf', entry.history ? entry.history.events.length : '']];
    root.innerHTML =
      '<nav class="kg-crumb" aria-label="Pfad">' + (view
        ? '<a href="/verzeichnis/' + esc(view.slug) + '">' + esc(view.title) + '</a> › '
        : '<span>' + esc(d.type ? d.type.name : 'Eintrag') + '</span> › ') +
        '<span>' + esc(d.node.name) + '</span></nav>' +
      KG.msgHTML(entry.flash) +
      '<div class="kg-cardhead"><div class="kg-titlebox">' + KG.typemark(d.type) +
        '<h1>' + esc(d.node.name) + '</h1>' +
        '<div class="kg-chips">' + (KG.card.headChipsHTML(d, d.layout) ||
          '<span class="kg-sub">Für die Kopfzeile ist noch kein Feld gewählt.</span>') + '</div></div>' +
        '<div class="kg-acts">' +
          '<button type="button" class="kg-btn kg-btn--ghost" data-tab="netz">' + NET_ICON + ' Wissensnetz</button>' +
          (d.may_write ? '<button type="button" class="kg-btn kg-btn--ghost" id="kg-rename">Umbenennen</button>' +
            '<button type="button" class="kg-btn" id="kg-edit-all">Felder bearbeiten</button>' : '') +
        '</div></div>' +
      '<div class="kg-tabs" role="tablist">' + tabs.map(([k, label, n]) =>
        '<button type="button" role="tab" aria-selected="' + (k === entry.tab) + '" class="' +
        (k === entry.tab ? 'is-on' : '') + '" data-tab="' + k + '">' + label +
        (n === '' ? '' : '<span class="kg-tally">' + n + '</span>') + '</button>').join('') + '</div>' +
      (entry.tab === 'netz' ? '<div id="kg-net">' + (entry.net ? networkHTML() : '<p class="kg-loading">Das Wissensnetz wird geladen …</p>') + '</div>'
        : '<div class="kg-cardgrid"><div id="kg-pane">' + paneHTML() + '</div><div class="kg-rail">' + railHTML() + '</div></div>');
    wire();
    entry.flash = '';
  }

  function paneHTML() {
    const d = entry.data;
    if (entry.tab === 'dokumente') {
      return '<section class="kg-sect"><header><h3>Zugeordnete Dokumente</h3><span class="kg-n">' +
        d.documents.length + '</span></header>' + (d.documents.length
        ? d.documents.map((doc) => '<div class="kg-docrow"><span class="kg-nm"><strong>' + esc(doc.title) +
            '</strong><span class="kg-sub">' + esc(doc.pointer) + '</span></span><span class="kg-mt">' +
            esc(doc.meta) + '</span></div>').join('')
        : '<div class="kg-frow"><span class="kg-fv kg-gap">Diesem Eintrag ist noch kein Dokument ' +
          'zugeordnet. Zuordnungen entstehen bei der Ingestion oder von Hand im Cortex.</span></div>') + '</section>';
    }
    if (entry.tab === 'verlauf') {
      const h = entry.history;
      if (!h) { return '<p class="kg-loading">Der Verlauf wird geladen …</p>'; }
      if (!h.available) {
        return '<section class="kg-sect"><header><h3>Verlauf</h3></header><div class="kg-frow"><span ' +
          'class="kg-fv kg-gap">Die Knovas-API liefert für diesen Eintrag noch keinen Verlauf.</span></div></section>';
      }
      return '<section class="kg-sect"><header><h3>Verlauf</h3></header>' + (h.events.length
        ? '<div class="kg-timeline">' + h.events.map((e) => '<div class="kg-tl"><span class="kg-mark"></span>' +
            '<span><span class="kg-when">' + esc(e.when || '—') + ' · ' + esc(e.who) + '</span><br>' +
            (e.field ? '<b>' + esc(e.field) + '</b> ' : '') + esc(e.what) + '</span></div>').join('') + '</div>' +
          (h.truncated ? '<p class="kg-note" style="padding:0 16px 12px">Gezeigt ist der Verlauf der ersten 25 Felder.</p>' : '')
        : '<div class="kg-frow"><span class="kg-fv kg-gap">Für diesen Eintrag ist noch nichts aufgezeichnet.</span></div>') +
        '</section>';
    }
    return KG.card.sectionsHTML(d, d.layout, { actions: d.may_write });
  }

  function personHTML(person, role, removable) {
    return '<div class="kg-person"><span>' + esc(person.display_name) + '</span><span class="kg-role">' +
      role + '</span>' + (removable ? '<button type="button" class="kg-x" data-revoke="' + esc(person.id) +
      '" aria-label="' + esc(person.display_name) + ' die Bearbeitung entziehen" title="Entziehen">×</button>' : '') + '</div>';
  }

  function railHTML() {
    const d = entry.data;
    const groups = d.visibility.access_group_ids || [];
    const shown = d.connections.slice(0, 5);
    const owner = d.grants.owner;
    return KG.card.summaryRailHTML(d, d.layout) +
      '<section class="kg-sect"><header><h3>Verbindungen</h3><span class="kg-n">' + d.connections.length +
        '</span></header>' + (shown.length ? shown.map((c) => '<div class="kg-frow"><span class="kg-fl">' +
          esc(c.label || 'verbunden') + '</span><span class="kg-fv"><a class="kg-link" href="' +
          esc(KG.entryHref(c.node.id)) + '">' + esc(c.node.name) + '</a></span></div>').join('')
        : '<div class="kg-frow"><span class="kg-fv kg-gap">Noch keine Verbindung.</span></div>') +
        '<div class="kg-frow"><span class="kg-fv"><button type="button" class="kg-link" data-tab="netz">' +
        'Im Wissensnetz ansehen →</button></span></div></section>' +
      '<section class="kg-sect"><header><h3>Zugriff</h3></header>' +
        '<div class="kg-frow"><span class="kg-fl">Sichtbar für</span><span class="kg-fv">' + (groups.length
          ? esc(groups.join(', ')) : '<span class="kg-gap">offen — keine Zugriffsgruppe gesetzt</span>') + '</span></div>' +
        '<div class="kg-frow"><span class="kg-fl">Entschieden von</span><span class="kg-fv">Knovas-ACL</span></div>' +
        '<div class="kg-frow"><span class="kg-fl">Bearbeiten dürfen</span><span class="kg-fv">' +
          (owner ? personHTML(owner, 'Eigentümer', false)
            : '<span class="kg-gap">Kein Eigentümer eingetragen — bearbeiten können Administratoren.</span>') +
          d.grants.editors.map((p) => personHTML(p, 'Bearbeitung', d.may_grant)).join('') +
          (d.may_grant ? '<div class="kg-picker" style="margin-top:8px"><label class="kg-sr" for="kg-grant-q">' +
            'Person als Bearbeiter hinzufügen</label><input type="search" id="kg-grant-q" autocomplete="off" ' +
            'placeholder="Person hinzufügen …"><div class="kg-picker-list" id="kg-grant-list" hidden></div></div>' +
            '<span class="kg-desc" id="kg-grant-err"></span>' : '') +
        '</span></div></section>' +
      '<section class="kg-sect"><header><h3>Systemangaben</h3></header>' +
        '<div class="kg-frow"><span class="kg-fl">Angelegt</span><span class="kg-fv">' + esc(d.node.created || '—') + '</span></div>' +
        '<div class="kg-frow"><span class="kg-fl">Zuletzt geändert</span><span class="kg-fv">' + esc(d.node.updated || '—') + '</span></div>' +
        '<div class="kg-frow"><span class="kg-fl">Kennung</span><span class="kg-fv"><code>' + esc(d.node.id) + '</code></span></div>' +
      '</section>';
  }

  function switchTab(tab) {
    entry.tab = tab;
    history.replaceState(null, '', location.pathname + (tab === 'felder' ? '' : '#' + tab));
    render();
    if (tab === 'netz' && !entry.net) { showNetwork(entry.depth); }
    if (tab === 'verlauf' && !entry.history) { showHistory(); }
  }

  function wire() {
    root.querySelectorAll('[data-tab]').forEach((el) =>
      el.addEventListener('click', () => switchTab(el.dataset.tab)));
    root.querySelectorAll('[data-edit]').forEach((el) =>
      el.addEventListener('click', () => editField(el.dataset.edit)));
    const all = document.getElementById('kg-edit-all');
    if (all) {
      all.addEventListener('click', () => {
        if (entry.tab !== 'felder') { switchTab('felder'); }
        const first = root.querySelector('[data-edit]');
        if (first) { first.focus(); first.scrollIntoView({ block: 'center' }); }
      });
    }
    const rename = document.getElementById('kg-rename');
    if (rename) { rename.addEventListener('click', renameEntry); }
    wireEditors();
    if (entry.tab === 'netz' && entry.net) { wireNetwork(); }
  }

  async function renameEntry() {
    const name = window.prompt('Neuer Name des Eintrags', entry.data.node.name);
    if (!name || !name.trim() || name.trim() === entry.data.node.name) { return; }
    try {
      await KG.api('/api/graph/nodes/' + encodeURIComponent(entry.id), { method: 'PATCH', body: { name: name.trim() } });
      entry.data.node.name = name.trim();
      entry.flash = 'Umbenannt.';
    } catch (error) { window.alert(error.message); }
    render();
  }

  /* A field is changed in place, like on a settings page -- not in a form
     that replaces the whole card. */
  async function editField(attributeId) {
    const attribute = attrs()[attributeId];
    const row = root.querySelector('#kg-pane .kg-frow[data-field="' + CSS.escape(attributeId) + '"]');
    if (!attribute || !row || row.classList.contains('is-editing')) { return; }
    row.classList.add('is-editing');
    const cell = entry.data.fields[attributeId];
    let ctx = {};
    if (attribute.datatype === 'entity_ref') {
      try { const c = await candidates(); ctx = { candidates: c.nodes, types: c.types }; } catch (e) { ctx = {}; }
    }
    const built = KG.control(attribute, cell && !cell.missing ? cell.value : null, ctx);
    const value = row.querySelector('.kg-fv');
    value.innerHTML = '';
    built.node.setAttribute('aria-label', attribute.name);
    value.appendChild(built.node);
    const err = document.createElement('span');
    err.className = 'kg-desc kg-desc--err';
    value.appendChild(err);
    const acts = row.querySelector('.kg-fa');
    acts.innerHTML = '<button type="button" class="kg-btn kg-btn--ghost kg-btn--sm" data-act="cancel">Abbrechen</button>' +
      '<button type="button" class="kg-btn kg-btn--sm" data-act="save">Speichern</button>';
    const cancel = () => render();
    const save = async () => {
      let raw;
      try { raw = built.read(); } catch (error) { err.textContent = error.message; return; }
      acts.querySelectorAll('button').forEach((b) => { b.disabled = true; });
      try {
        const body = await KG.api('/api/graph/nodes/' + encodeURIComponent(entry.id) + '/fields/' +
          encodeURIComponent(attributeId), { method: 'PUT', body: { value: raw } });
        entry.data.fields[attributeId] = body.field;
        entry.data.gaps = body.gaps;
        entry.flash = raw === null ? '„' + attribute.name + '“ geleert.' : '„' + attribute.name + '“ gespeichert.';
        render();
      } catch (error) {
        err.textContent = error.message;
        acts.querySelectorAll('button').forEach((b) => { b.disabled = false; });
      }
    };
    acts.querySelector('[data-act="cancel"]').addEventListener('click', cancel);
    acts.querySelector('[data-act="save"]').addEventListener('click', save);
    row.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); cancel(); }
      if (e.key === 'Enter' && e.target.tagName === 'INPUT') { e.preventDefault(); save(); }
    });
    built.focus.focus();
  }

  // ── Bearbeiter ────────────────────────────────────────────────────────

  function wireEditors() {
    root.querySelectorAll('[data-revoke]').forEach((button) => button.addEventListener('click', async () => {
      try {
        const body = await KG.api('/api/graph/nodes/' + encodeURIComponent(entry.id) + '/grants/' +
          encodeURIComponent(button.dataset.revoke), { method: 'DELETE' });
        entry.data.grants = { owner: body.owner, editors: body.editors };
        entry.flash = 'Bearbeitungsrecht entzogen.';
        render();
      } catch (error) {
        const err = document.getElementById('kg-grant-err');
        if (err) { err.textContent = error.message; } else { window.alert(error.message); }
      }
    }));
    const input = document.getElementById('kg-grant-q');
    const box = document.getElementById('kg-grant-list');
    if (!input || !box) { return; }
    const search = KG.debounce(async () => {
      const q = input.value.trim();
      if (q.length < 2) { box.hidden = true; return; }
      try {
        const body = await KG.api('/api/graph/people?q=' + encodeURIComponent(q));
        const taken = new Set([(entry.data.grants.owner || {}).id].concat(entry.data.grants.editors.map((p) => p.id)));
        const people = (body.people || []).filter((p) => !taken.has(p.id));
        box.innerHTML = people.length ? people.map((p) => '<button type="button" data-grant="' + esc(p.id) +
          '">' + esc(p.display_name) + ' <span class="kg-sub-inline">' + esc(p.email) + '</span></button>').join('')
          : '<p class="kg-note" style="padding:6px 9px">Niemand gefunden.</p>';
        box.hidden = false;
        box.querySelectorAll('[data-grant]').forEach((b) => b.addEventListener('click', () => grant(b.dataset.grant)));
      } catch (error) { box.hidden = true; }
    }, 250);
    input.addEventListener('input', search);
    input.addEventListener('keydown', (e) => { if (e.key === 'Escape') { box.hidden = true; } });
  }

  async function grant(userId) {
    try {
      const body = await KG.api('/api/graph/nodes/' + encodeURIComponent(entry.id) + '/grants',
        { method: 'POST', body: { user_id: userId } });
      entry.data.grants = { owner: body.owner, editors: body.editors };
      entry.flash = body.editor.display_name + ' darf diesen Eintrag jetzt bearbeiten.';
      render();
    } catch (error) {
      document.getElementById('kg-grant-err').textContent = error.message;
    }
  }

  async function showHistory() {
    try {
      entry.history = await KG.api('/api/graph/nodes/' + encodeURIComponent(entry.id) + '/history');
    } catch (error) {
      entry.history = { available: true, events: [] };
      entry.flash = '';
      if (entry.tab === 'verlauf') {
        document.getElementById('kg-pane').innerHTML = KG.msgHTML(error.message, 'err');
        return;
      }
    }
    if (entry.tab === 'verlauf') { render(); }
  }

  // ── Wissensnetz ───────────────────────────────────────────────────────

  async function showNetwork(depth) {
    entry.depth = depth;
    try {
      entry.net = await KG.api('/api/graph/nodes/' + encodeURIComponent(entry.id) + '/network?depth=' + depth);
    } catch (error) {
      entry.net = null;
      const host = document.getElementById('kg-net');
      if (host) { host.innerHTML = KG.msgHTML(error.message, 'err'); }
      return;
    }
    if (entry.tab === 'netz') { render(); }
  }

  const trim = (s) => (s.length > 26 ? s.slice(0, 25) + '…' : s);
  const toneOf = (typeId) => ((entry.net.types || {})[typeId] || {}).tone || 't4';

  /* Rings around the entry. The first ring is spread evenly; every further
     ring hangs off the neighbour it was reached through, so a node does not
     wander across the picture and claim a nearness it does not have. */
  function layout(net) {
    const byHop = {};
    net.nodes.forEach((n) => { (byHop[n.hop] = byHop[n.hop] || []).push(n); });
    const ring1 = byHop[1] || [];
    const scale = Math.max(1, ring1.length / 9);
    // A crowded first ring grows the whole picture rather than overlapping.
    const rx = (hop) => (232 + 203 * (hop - 1)) * scale;
    const ry = (hop) => (150 + 58 * (hop - 1)) * scale;
    const maxHop = Math.max(1, ...net.nodes.map((n) => n.hop));
    const W = Math.max(940, 2 * (rx(maxHop) + 120)), H = Math.max(470, 2 * (ry(maxHop) + 34));
    const cx = W / 2, cy = H / 2;
    const pos = {}, angle = {};
    net.nodes.filter((n) => n.hop === 0).forEach((n) => { pos[n.id] = { x: cx, y: cy }; });
    ring1.forEach((n, i) => {
      const a = -Math.PI / 2 + (i * 2 * Math.PI) / Math.max(ring1.length, 1);
      angle[n.id] = a;
      pos[n.id] = { x: cx + Math.cos(a) * rx(1), y: cy + Math.sin(a) * ry(1) };
    });
    for (let hop = 2; hop <= maxHop; hop++) {
      const ring = byHop[hop] || [];
      const wanted = ring.map((n, i) => {
        const edge = net.edges.find((e) => (e.from === n.id && angle[e.to] !== undefined && e.to !== n.id) ||
          (e.to === n.id && angle[e.from] !== undefined && e.from !== n.id));
        const parent = edge ? (edge.from === n.id ? edge.to : edge.from) : null;
        const base = parent !== null ? angle[parent] : -Math.PI / 2 + (i * 2 * Math.PI) / Math.max(ring.length, 1);
        return { n: n, a: base + ((i % 3) - 1) * 0.3 };
      }).sort((p, q) => p.a - q.a);
      // Keep neighbours on the same ring from sitting on top of each other.
      const gap = Math.min(0.42, (2 * Math.PI) / Math.max(ring.length, 1));
      for (let i = 1; i < wanted.length; i++) {
        if (wanted[i].a - wanted[i - 1].a < gap) { wanted[i].a = wanted[i - 1].a + gap; }
      }
      wanted.forEach(({ n, a }) => {
        angle[n.id] = a;
        pos[n.id] = { x: cx + Math.cos(a) * rx(hop), y: cy + Math.sin(a) * ry(hop) };
      });
    }
    return { W: W, H: H, pos: pos };
  }

  function netSVG(net) {
    const L = layout(net);
    const hop = {};
    net.nodes.forEach((n) => { hop[n.id] = n.hop; });
    const chipW = (name) => Math.min(230, Math.max(96, trim(name).length * 7.4 + 26));
    const lines = net.edges.map((e) => {
      const a = L.pos[e.from], b = L.pos[e.to];
      if (!a || !b) { return ''; }
      const far = hop[e.from] >= 2 || hop[e.to] >= 2;
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      const label = far || !e.label ? '' :
        '<rect class="kg-elabel-bg" x="' + (mx - (e.label.length * 3.1 + 7)) + '" y="' + (my - 8) +
        '" width="' + (e.label.length * 6.2 + 14) + '" height="15" rx="7.5"></rect>' +
        '<text class="kg-elabel" x="' + mx + '" y="' + (my + 3) + '" text-anchor="middle">' + esc(e.label) + '</text>';
      return '<line class="kg-edge' + (far ? ' is-far' : '') + (e.manual ? ' is-manual' : '') + '" x1="' + a.x +
        '" y1="' + a.y + '" x2="' + b.x + '" y2="' + b.y + '"></line>' + label;
    }).join('');
    const nodes = net.nodes.map((n) => {
      const p = L.pos[n.id];
      if (!p) { return ''; }
      const w = chipW(n.name), centre = n.hop === 0;
      const type = (net.types || {})[n.node_type_id];
      return '<g class="kg-node tone-' + toneOf(n.node_type_id) + (centre ? ' is-centre' : '') + '" data-id="' +
        esc(n.id) + '" data-slug="' + esc(n.slug || '') + '"' + (centre ? '' : ' tabindex="0" role="link"') +
        ' aria-label="' + esc(n.name + (type ? ' · ' + type.name : '')) + '"><title>' +
        esc(n.name + (type ? ' · ' + type.name : '')) + '</title><rect x="' + (p.x - w / 2) + '" y="' + (p.y - 16) +
        '" width="' + w + '" height="32" rx="16"></rect><text x="' + p.x + '" y="' + (p.y + 4) +
        '" text-anchor="middle">' + esc(trim(n.name)) + '</text></g>';
    }).join('');
    return '<svg class="kg-net kg-netfade" viewBox="0 0 ' + L.W + ' ' + L.H + '" role="group" aria-label="' +
      esc('Wissensnetz um ' + entry.data.node.name) + '">' + lines + nodes + '</svg>';
  }

  function networkHTML() {
    const net = entry.net;
    const neighbours = net.nodes.length - 1;
    const kinds = [];
    net.nodes.forEach((n) => { if (kinds.indexOf(n.node_type_id) < 0) { kinds.push(n.node_type_id); } });
    return '<div class="kg-graphwrap"><div class="kg-graphbar">' +
      '<span style="font-size:.83rem"><strong>Umgebung</strong> von „' + esc(entry.data.node.name) + '“</span>' +
      '<span class="kg-seg" role="group" aria-label="Schritte">' + [1, 2, 3].map((d) =>
        '<button type="button" class="' + (d === entry.depth ? 'is-on' : '') + '" data-depth="' + d + '">' + d +
        ' Schritt' + (d > 1 ? 'e' : '') + '</button>').join('') + '</span>' +
      '<label class="kg-netfind"><span class="kg-sr">Eintrag suchen und öffnen</span>' + SEARCH_ICON.replace(/16/g, '15') +
        '<input type="search" id="kg-netq" autocomplete="off" placeholder="Eintrag suchen …" role="combobox" ' +
        'aria-expanded="false" aria-controls="kg-netresults"><div class="kg-netresults" id="kg-netresults" ' +
        'role="listbox" hidden></div></label>' +
      '<span class="kg-legend">' + kinds.map((k) => '<span class="tone-' + toneOf(k) + '"><i></i><span ' +
        'class="kg-legend-name">' + esc(((net.types || {})[k] || {}).name || 'Ohne Typ') + '</span></span>').join('') + '</span>' +
      '</div><div class="kg-graphstage">' + netSVG(net) + '</div>' +
      '<div class="kg-graphfoot">' + neighbours + ' Nachbar' + (neighbours === 1 ? '' : 'n') + ' · ' +
        net.edges.length + ' Verbindung' + (net.edges.length === 1 ? '' : 'en') + ' · Kanten entstehen aus ' +
        '<strong>Verbindungsfeldern</strong> und tragen deren Namen. Ein Klick auf einen Knoten öffnet ihn und ' +
        'rückt ihn in die Mitte. Die Suche oben findet jeden Eintrag, den Sie sehen dürfen — auch einen, der ' +
        'hier gerade nicht gezeichnet ist. Die API begrenzt den Lauf auf 3 Schritte.' +
        (net.available ? '' : ' <strong>Die Nachbarschaft war gerade nicht lesbar; gezeigt ist nur der Eintrag selbst.</strong>') +
      '</div></div>' +
      '<div class="kg-callout"><b>Nur, was Sie sehen dürfen.</b> Kanten werden auf der <em>gefilterten</em> ' +
      'Knotenmenge gebildet, nicht auf dem rohen Lauf durch den Graphen. Sonst benennte eine Linie einen ' +
      'Knoten, den die Freigaben verbergen — und verriete damit, dass es ihn gibt.</div>';
  }

  function openNode(id, slug) { location.href = KG.entryHref(id, slug) + '#netz'; }

  function wireNetwork() {
    root.querySelectorAll('[data-depth]').forEach((b) => b.addEventListener('click', () => {
      const depth = Number(b.dataset.depth);
      if (depth !== entry.depth) { entry.net = null; render(); showNetwork(depth); }
    }));
    const chips = Array.from(root.querySelectorAll('svg.kg-net .kg-node'));
    chips.forEach((g) => {
      if (g.classList.contains('is-centre')) { return; }
      const go = () => openNode(g.dataset.id, g.dataset.slug);
      g.addEventListener('click', go);
      g.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    });

    /* The search covers EVERY entry this person may see, not only the drawn
       ones: someone looking for a node two steps away should find it without
       turning the depth up first. What is drawn is still marked -- the
       drawing dims what does not match instead of removing it; a
       neighbourhood with holes would be a false statement about it. */
    const input = document.getElementById('kg-netq');
    const box = document.getElementById('kg-netresults');
    if (!input || !box) { return; }
    const drawn = new Set(chips.map((g) => g.dataset.id));
    const close = () => { box.hidden = true; input.setAttribute('aria-expanded', 'false'); };
    const paint = (q) => chips.forEach((g) => {
      const node = entry.net.nodes.find((n) => n.id === g.dataset.id) || { name: '' };
      const hit = q && node.name.toLowerCase().indexOf(q) >= 0;
      g.classList.toggle('is-hit', !!hit);
      // The centre never fades: it is the anchor of the view.
      g.classList.toggle('is-dim', !!q && !hit && !g.classList.contains('is-centre'));
    });
    const show = async (q) => {
      if (!q) { close(); paint(''); return; }
      let all;
      try { all = await candidates(); } catch (error) { return; }
      const hits = all.nodes.filter((n) => n.name.toLowerCase().indexOf(q) >= 0).slice(0, 8);
      box.innerHTML = hits.length ? hits.map((n) => {
        const type = all.types[n.node_type_id] || {};
        return '<button type="button" role="option" data-open="' + esc(n.id) + '"><span>' + esc(n.name) + '</span>' +
          (drawn.has(n.id) ? '<span class="kg-here">im Bild</span>' : '') +
          '<span class="kg-k tone-' + esc(type.tone || 't4') + '">' + esc(type.name || '') + '</span></button>';
      }).join('') : '<p class="kg-none">Kein Eintrag, dessen Name „' + esc(q) + '“ enthält.</p>';
      box.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      box.querySelectorAll('[data-open]').forEach((b) => b.addEventListener('click', () => openNode(b.dataset.open, '')));
      paint(q);
    };
    input.addEventListener('input', () => show(input.value.trim().toLowerCase()));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { input.value = ''; close(); paint(''); return; }
      const first = box.querySelector('[data-open]');
      if (e.key === 'ArrowDown' && first) { e.preventDefault(); first.focus(); }
      if (e.key === 'Enter' && first) { e.preventDefault(); openNode(first.dataset.open, ''); }
    });
    box.addEventListener('keydown', (e) => {
      const options = Array.from(box.querySelectorAll('[data-open]'));
      const at = options.indexOf(document.activeElement);
      if (e.key === 'ArrowDown' && at < options.length - 1) { e.preventDefault(); options[at + 1].focus(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); (at > 0 ? options[at - 1] : input).focus(); }
      if (e.key === 'Escape') { close(); input.focus(); }
    });
  }

  // A click beside the result list closes it; a click in it does not -- or the
  // hit would vanish under the pointer before the click lands. Bound once: the
  // network is redrawn on every depth change.
  document.addEventListener('click', (e) => {
    if (e.target.closest('.kg-netfind')) { return; }
    const box = document.getElementById('kg-netresults');
    const input = document.getElementById('kg-netq');
    if (box) { box.hidden = true; }
    if (input) { input.setAttribute('aria-expanded', 'false'); }
  });

  if (page === 'directory') { openDirectory(); }
  if (page === 'entry') {
    openEntry();
    // A link to #netz on the card itself switches the tab, like a click would.
    window.addEventListener('hashchange', () => {
      const tab = location.hash.slice(1) || 'felder';
      if (entry.data && TABS.indexOf(tab) >= 0 && tab !== entry.tab) { switchTab(tab); }
    });
  }
})();
