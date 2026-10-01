/* Verwaltung -> Wissenstypen, Karte gestalten, Verzeichnisse.
 *
 * The console does not ask anyone to guess a schema in advance: which fields a
 * matter needs is known after forty matters, not before. So the pages show
 * what the inventory says -- fill rates, used values, gaps -- and turn it into
 * SUGGESTIONS. The system proposes; a person decides. Nothing here changes by
 * itself, and a dismissed suggestion never comes back.
 */
(function () {
  'use strict';

  const KG = window.KG;
  const root = document.getElementById('kg-root');
  if (!KG || !root || root.querySelector('[data-kg-unavailable]')) { return; }
  const esc = KG.esc;
  const page = root.dataset.kgPage;
  const typeId = root.dataset.type;
  const setTitle = (title, lede) => {
    document.getElementById('kg-title').textContent = title;
    if (lede !== undefined) { document.getElementById('kg-lede').textContent = lede; }
    document.title = title + ' · ' + document.title.split(' · ').pop();
  };
  let flash = KG.takeFlash();
  let flashKind = 'ok';
  const say = (text, kind) => { flash = text; flashKind = kind || 'ok'; };
  const takeSaid = () => { const html = KG.msgHTML(flash, flashKind); flash = ''; flashKind = 'ok'; return html; };

  function fail(error) {
    root.innerHTML = KG.msgHTML(error.message, 'err');
  }

  // ── Vorschläge ────────────────────────────────────────────────────────

  const BULB = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 ' +
    '12.7V17h8v-2.3A7 7 0 0 0 12 2z"/></svg>';

  /* No suggestions, no strip: an empty "Vorschläge" heading would promise
     work there is none of. */
  function suggestHTML(items) {
    if (!items || !items.length) { return ''; }
    return '<section class="kg-suggest" aria-label="Vorschläge aus dem Bestand"><header>' + BULB +
      '<h3>Vorschläge aus dem Bestand</h3><span class="kg-sub-inline">' + items.length + '</span></header>' +
      '<p class="kg-suggest-note">Gerechnet aus den vorhandenen Einträgen — Füllgrade, benutzte Auswahlwerte, ' +
      'Lücken. Nichts davon passiert von selbst: jeder Vorschlag wartet auf einen Klick.</p>' +
      items.map((s) => '<article class="kg-sg kg-sg--' + esc(s.tone) + '"><div class="kg-sg-body"><strong>' +
        esc(s.title) + '</strong><span class="kg-sub">' + esc(s.why) + '</span></div><div class="kg-sg-acts">' +
        (s.cta ? '<button type="button" class="kg-btn kg-btn--sm" data-apply="' + esc(s.id) + '">' + esc(s.cta) + '</button>' : '') +
        (s.dismissable === false ? '' : '<button type="button" class="kg-btn kg-btn--ghost kg-btn--sm" data-dismiss="' +
          esc(s.id) + '">Verwerfen</button>') + '</div></article>').join('') + '</section>';
  }

  function wireSuggest(scope, items, reload) {
    root.querySelectorAll('[data-apply]').forEach((b) => b.addEventListener('click', async () => {
      const item = items.find((s) => s.id === b.dataset.apply);
      if (item && item.action && item.action.kind === 'show_gaps') { location.href = item.action.href; return; }
      b.disabled = true;
      try {
        const body = await KG.api('/api/graph/suggestions/apply', { method: 'POST',
          body: { scope: scope, type_id: typeId, id: b.dataset.apply } });
        if (scope === 'dirs') { KG.carryFlash(body.message); location.reload(); return; }
        say(body.message);
      } catch (error) { say(error.message, 'err'); }
      reload();
    }));
    root.querySelectorAll('[data-dismiss]').forEach((b) => b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        const body = await KG.api('/api/graph/suggestions/dismiss', { method: 'POST',
          body: { scope: scope, type_id: typeId, id: b.dataset.dismiss } });
        say(body.message);
      } catch (error) { say(error.message, 'err'); }
      reload();
    }));
  }

  // ═══ Wissenstypen ═══════════════════════════════════════════════════════

  async function typesPage() {
    let data;
    try { data = await KG.api('/api/graph/admin/types'); } catch (error) { fail(error); return; }
    const rows = data.types.map((t) => '<tr data-href="/admin/wissenstypen/' + esc(encodeURIComponent(t.id)) + '">' +
      '<td><a href="/admin/wissenstypen/' + esc(encodeURIComponent(t.id)) + '"><strong>' + esc(t.name) + '</strong></a>' +
        (t.description ? '<span class="kg-sub">' + esc(t.description) + '</span>' : '') + '</td>' +
      '<td class="kg-num">' + t.fields + '<span class="kg-sub">davon ' + t.required + ' Pflicht</span></td>' +
      '<td class="kg-num">' + t.entries + '</td>' +
      '<td>' + (t.view && t.view.active
        ? '<span class="kg-pill kg-pill--on">aktiv</span><span class="kg-sub"><code>/verzeichnis/' + esc(t.view.slug) + '</code></span>'
        : t.view ? '<span class="kg-pill kg-pill--off">abgeschaltet</span><span class="kg-sub">Titel, Spalten und Karte sind gespeichert.</span>'
          : '<span class="kg-sub">keine</span>') + '</td>' +
      '<td><a class="kg-btn kg-btn--ghost kg-btn--sm" href="/admin/wissenstypen/' + esc(encodeURIComponent(t.id)) +
        '">Öffnen</a></td></tr>').join('');
    root.innerHTML = takeSaid() +
      '<div class="kg-table-wrap"><table><thead><tr><th>Typ</th><th>Felder</th><th>Einträge</th><th>Liste</th>' +
      '<th><span class="kg-sr">Aktion</span></th></tr></thead><tbody>' +
      (rows || '<tr><td class="kg-empty" colspan="5">Noch kein Wissenstyp. Der erste entsteht unten.</td></tr>') +
      '</tbody></table></div>' +
      '<div class="kg-panel"><h2>Neuen Wissenstyp anlegen</h2><p class="kg-hint">Nur der Name. Felder, Karte und ' +
      'Liste beschreiben Sie danach auf der Seite des Typs.</p><label class="kg-label" for="kg-newtype">Name</label>' +
      '<input type="text" id="kg-newtype" placeholder="Vertrag" autocomplete="off" maxlength="200">' +
      '<div id="kg-newtype-err"></div><div class="kg-actions"><button type="button" class="kg-btn" id="kg-addtype">' +
      'Typ anlegen</button></div></div>';
    root.querySelectorAll('tr[data-href]').forEach((tr) => tr.addEventListener('click', (e) => {
      if (!e.target.closest('a')) { location.href = tr.dataset.href; }
    }));
    const add = async () => {
      const name = document.getElementById('kg-newtype').value.trim();
      if (!name) { return; }
      try {
        const body = await KG.api('/api/graph/node-types', { method: 'POST', body: { name: name } });
        KG.carryFlash('Typ „' + name + '“ angelegt. Jetzt seine Felder beschreiben — ein Verzeichnis dafür ' +
          'legen Sie danach unter Verzeichnisse an.');
        location.href = '/admin/wissenstypen/' + encodeURIComponent(body.node_type.id);
      } catch (error) { document.getElementById('kg-newtype-err').innerHTML = KG.msgHTML(error.message, 'err'); }
    };
    document.getElementById('kg-addtype').addEventListener('click', add);
    document.getElementById('kg-newtype').addEventListener('keydown', (e) => { if (e.key === 'Enter') { add(); } });
  }

  // ═══ Typ-Werkstatt ══════════════════════════════════════════════════════

  const shop = { data: null, open: '' };

  async function typePage() {
    try { shop.data = await KG.api('/api/graph/admin/types/' + encodeURIComponent(typeId)); }
    catch (error) {
      setTitle('Wissenstyp');
      fail(error);
      return;
    }
    renderShop();
  }

  function fillHTML(attribute, basis) {
    const fill = attribute.fill;
    if (!basis.total) { return '<span class="kg-fill"><span class="kg-fill-num kg-gap">keine Einträge</span></span>'; }
    if (!fill || !fill.total) { return '<span class="kg-fill"><span class="kg-fill-num kg-gap">—</span></span>'; }
    const share = Math.round((fill.filled / fill.total) * 100);
    return '<span class="kg-fill" title="In ' + fill.filled + ' von ' + fill.total + ' ' +
      (basis.complete ? '' : 'gelesenen ') + 'Einträgen ausgefüllt"><span class="kg-bar-track"><i style="width:' +
      share + '%"></i></span><span class="kg-fill-num">' + fill.filled + '/' + fill.total + '</span></span>';
  }

  function targetName(id) {
    const type = (shop.data.types || []).find((t) => t.id === id);
    return type ? type.name : 'beliebiger Typ';
  }

  function fieldRow(a) {
    const open = shop.open === a.id;
    const kind = KG.kindLabel(a.datatype);
    return '<div class="kg-fl-row' + (open ? ' is-open' : '') + '" data-field="' + esc(a.id) + '" draggable="' +
      (open ? 'false' : 'true') + '"><div class="kg-fl-head"><span class="kg-grip" aria-hidden="true">⠿</span>' +
      '<span class="kg-fl-name">' + esc(a.name) + '</span><span class="kg-kind">' + esc(kind) +
      (a.datatype === 'entity_ref' ? ' → ' + esc(targetName(a.target_node_type_id)) : '') + '</span>' +
      (a.required ? '<span class="kg-must">Pflicht</span>' : '') + fillHTML(a, shop.data.basis) +
      '<button type="button" class="kg-btn kg-btn--ghost kg-btn--sm" data-open="' + esc(a.id) + '">' +
      (open ? 'Schliessen' : 'Bearbeiten') + '</button></div>' + (open ? '<div class="kg-fl-edit">' +
        '<div class="kg-fieldrow"><label class="kg-fc kg-f-name"><span>Name</span><input type="text" id="kg-fe-name" value="' +
        esc(a.name) + '" maxlength="200"></label><span class="kg-fc"><span>Datentyp</span><strong class="kg-f-type">' +
        esc((KG.DATATYPES[a.datatype] || { label: a.datatype }).label) + '</strong></span>' +
        '<label class="kg-fc kg-f-narrow kg-f-check"><span>Pflicht</span><input type="checkbox" id="kg-fe-req"' +
        (a.required ? ' checked' : '') + '></label>' + (a.datatype === 'enum'
          ? '<label class="kg-fc kg-f-grow"><span>Auswahlwerte — einer je Zeile</span><textarea id="kg-fe-enum" rows="3">' +
            esc((a.enum_values || []).join('\n')) + '</textarea></label>'
          : '<label class="kg-fc kg-f-grow"><span>Beschreibung</span><input type="text" id="kg-fe-desc" value="' +
            esc(a.description || '') + '" placeholder="optional — erscheint als Hinweis am Feld"></label>') +
        '</div><p class="kg-hint" style="margin:10px 0 0">Der <strong>Datentyp</strong> steht fest: bestehende Werte ' +
        'sind in seiner Form gespeichert, und die API kennt kein Umschreiben. Die <strong>Reihenfolge</strong> ergibt ' +
        'sich aus dieser Liste — Zeile anfassen und verschieben.</p><div id="kg-fe-err"></div><div class="kg-actions">' +
        '<button type="button" class="kg-btn" data-save="' + esc(a.id) + '">Speichern</button>' +
        '<button type="button" class="kg-btn kg-btn--danger" data-retire="' + esc(a.id) + '">Stilllegen</button>' +
        '<span class="kg-sub-inline">Stilllegen behält bestehende Werte; neue Einträge kennen das Feld nicht mehr.</span>' +
        '</div></div>' : '') + '</div>';
  }

  function renderShop() {
    const d = shop.data;
    const t = d.type;
    setTitle(t.name, t.description || 'Die Felder dieses Typs, der Aufbau seiner Karte und seine Liste.');
    const basis = d.basis;
    const retired = d.retired.map((a) => '<div class="kg-fl-row is-retired"><div class="kg-fl-head"><span class="kg-grip"></span>' +
      '<span class="kg-fl-name">' + esc(a.name) + '</span><span class="kg-kind">' + esc(KG.kindLabel(a.datatype)) + '</span>' +
      '<span class="kg-pill kg-pill--off">stillgelegt</span><span class="kg-fill"><span class="kg-fill-num kg-gap">' +
      'Werte bleiben lesbar</span></span><button type="button" class="kg-btn kg-btn--ghost kg-btn--sm" data-revive="' +
      esc(a.id) + '">Wieder aufnehmen</button></div></div>').join('');
    const layout = d.layout;
    const names = {};
    d.attributes.forEach((a) => { names[a.id] = a.name; });
    const chips = (ids) => ids.length ? ids.map((id) => '<span class="kg-chip">' + esc(names[id]) + '</span>').join('')
      : '<span class="kg-gap">leer</span>';
    const view = d.view;
    root.innerHTML = '<p class="kg-hint" style="margin:0 0 6px"><a href="/admin/wissenstypen">← Alle Wissenstypen</a></p>' +
      takeSaid() + suggestHTML(d.suggestions) +
      '<h2 style="margin-top:26px">Felder</h2><p class="kg-hint">Die Zahl rechts sagt, in wie vielen Einträgen ein ' +
      'Feld tatsächlich ausgefüllt ist — daran sieht man, ob es seinen Platz verdient. Die Reihenfolge gilt für ' +
      'Formular und Karte; zum Ändern eine Zeile anfassen und verschieben. Ein <strong>Pflichtfeld</strong> hält das ' +
      'Speichern nicht auf: es macht eine fehlende Angabe sichtbar, statt sie zu verhindern.</p>' +
      (!basis.complete && basis.total ? '<p class="kg-note">Füllgrade aus einer Stichprobe von ' + basis.loaded + ' der ' +
        basis.total + ' Einträge: die Knovas-API bietet die Sammelabfrage der Werte noch nicht an. Vorschläge, die ' +
        'den ganzen Bestand brauchen, erscheinen deshalb nicht.</p>' : '') +
      '<div class="kg-flist" id="kg-fields">' + (d.attributes.map(fieldRow).join('') ||
        '<p class="kg-empty">Noch kein Feld. Ohne Felder trägt ein Eintrag nur seinen Namen — erlaubt, aber die ' +
        'Liste hätte dann nichts anzuzeigen.</p>') + retired + '</div>' +
      '<div class="kg-panel"><h2>Feld hinzufügen</h2><div class="kg-fieldrow" style="margin-top:10px">' +
        '<label class="kg-fc kg-f-name"><span>Name</span><input type="text" id="kg-nf-name" placeholder="Gegenanwalt" maxlength="200"></label>' +
        '<label class="kg-fc"><span>Datentyp</span><select id="kg-nf-type"><option value="">— wählen —</option>' +
        Object.keys(KG.DATATYPES).map((k) => '<option value="' + k + '">' + esc(KG.DATATYPES[k].label) + '</option>').join('') +
        '</select></label><label class="kg-fc kg-f-narrow kg-f-check"><span>Pflicht</span><input type="checkbox" id="kg-nf-req"></label>' +
        '<label class="kg-fc kg-f-grow"><span>Beschreibung</span><input type="text" id="kg-nf-desc" placeholder="optional"></label>' +
      '</div><p class="kg-hint" id="kg-nf-hint" style="margin:10px 0 0"></p>' +
      '<div id="kg-nf-enumwrap" hidden><label class="kg-label" for="kg-nf-enum">Auswahlwerte — einer je Zeile</label>' +
        '<textarea id="kg-nf-enum" rows="3" placeholder="offen&#10;abgeschlossen&#10;ruhend"></textarea></div>' +
      '<div id="kg-nf-targetwrap" hidden><label class="kg-label" for="kg-nf-target">Verbindet mit</label>' +
        '<select id="kg-nf-target"><option value="">beliebigem Eintrag</option>' + d.types.map((ty) =>
          '<option value="' + esc(ty.id) + '">' + esc(ty.name) + '</option>').join('') + '</select></div>' +
      '<div id="kg-nf-err"></div><div class="kg-actions"><button type="button" class="kg-btn" id="kg-nf-add">Feld anlegen</button></div></div>' +
      '<h2 style="margin-top:34px">Aufbau der Karte</h2><p class="kg-hint">Wie ein einzelner Eintrag dieses Typs ' +
      'aussieht: was in der <strong>Kopfzeile</strong> steht, was in der <strong>Seitenleiste</strong>, und in welchen ' +
      '<strong>Abschnitten</strong> der Rest gruppiert ist.</p><div class="kg-panel" style="margin-top:14px">' +
        '<div class="kg-cardsummary">' + [['Kopfzeile', layout.head], ['Seitenleiste', layout.rail]].concat(
          layout.sections.map((s) => [s.name, s.fields])).map(([label, ids]) => '<div class="kg-csrow"><span class="kg-cslabel">' +
          esc(label) + '</span><span class="kg-csvals">' + chips(ids) + '</span></div>').join('') +
          (layout.loose.length ? '<div class="kg-csrow"><span class="kg-cslabel">Nicht zugeordnet</span><span class="kg-csvals">' +
            chips(layout.loose) + '</span></div>' : '') + '</div>' +
        '<div class="kg-actions"><a class="kg-btn" href="/admin/wissenstypen/' + esc(encodeURIComponent(t.id)) +
        '/karte">Karte gestalten →</a></div></div>' +
      '<h2 style="margin-top:34px">Verzeichnis</h2><p class="kg-hint">Die Seite in der Navigation, auf der alle ' +
      'Einträge dieses Typs stehen. Angelegt und eingestellt wird sie unter <a href="/admin/verzeichnisse">Verzeichnisse</a>.</p>' +
      '<div class="kg-panel">' + (view ? '<div class="kg-cardsummary">' +
          '<div class="kg-csrow"><span class="kg-cslabel">Titel</span><span class="kg-csvals">' + esc(view.title) + '</span></div>' +
          '<div class="kg-csrow"><span class="kg-cslabel">Adresse</span><span class="kg-csvals"><code>/verzeichnis/' + esc(view.slug) + '</code></span></div>' +
          '<div class="kg-csrow"><span class="kg-cslabel">Spalten</span><span class="kg-csvals">' + (view.columns.filter((c) => names[c]).length
            ? chips(view.columns.filter((c) => names[c]))
            : '<span class="kg-gap">keine gewählt — die ersten ' + Math.min(4, d.attributes.length) + ' Felder</span>') + '</span></div>' +
          '<div class="kg-csrow"><span class="kg-cslabel">Status</span><span class="kg-csvals">' + (view.active
            ? '<span class="kg-pill kg-pill--on">aktiv</span>' : '<span class="kg-pill kg-pill--off">abgeschaltet</span>') + '</span></div></div>' +
          '<div class="kg-actions"><a class="kg-btn kg-btn--ghost" href="/admin/verzeichnisse">Verzeichnis verwalten →</a>' +
          (view.active ? '<a class="kg-btn kg-btn--ghost" href="/verzeichnis/' + esc(view.slug) + '">Verzeichnis öffnen →</a>' : '') + '</div>'
        : '<p class="kg-hint" style="margin:0">Für diesen Typ gibt es noch kein Verzeichnis — seine Einträge sind damit ' +
          'nur über die Suche und den Cortex erreichbar.</p><div class="kg-actions"><a class="kg-btn" href="/admin/verzeichnisse?typ=' +
          esc(encodeURIComponent(t.id)) + '">Verzeichnis anlegen →</a></div>') + '</div>';
    wireShop();
  }

  const schemaPath = (id) => '/api/graph/node-types/' + encodeURIComponent(typeId) + '/schema' +
    (id ? '/' + encodeURIComponent(id) : '');

  function wireShop() {
    wireSuggest('fields', shop.data.suggestions, typePage);
    const list = document.getElementById('kg-fields');
    list.addEventListener('click', async (e) => {
      const open = e.target.closest('[data-open]');
      if (open) { shop.open = shop.open === open.dataset.open ? '' : open.dataset.open; renderShop(); return; }
      const save = e.target.closest('[data-save]');
      if (save) {
        const body = { name: document.getElementById('kg-fe-name').value, required: document.getElementById('kg-fe-req').checked };
        const box = document.getElementById('kg-fe-enum');
        if (box) { body.enum_values = box.value.split('\n').map((v) => v.trim()).filter(Boolean); }
        else { body.description = document.getElementById('kg-fe-desc').value; }
        try {
          await KG.api(schemaPath(save.dataset.save), { method: 'PATCH', body: body });
          say('Feld „' + body.name.trim() + '“ gespeichert.');
          shop.open = '';
          typePage();
        } catch (error) { document.getElementById('kg-fe-err').innerHTML = KG.msgHTML(error.message, 'err'); }
        return;
      }
      const retire = e.target.closest('[data-retire]');
      if (retire) {
        const attribute = shop.data.attributes.find((a) => a.id === retire.dataset.retire);
        if (!window.confirm('Feld „' + attribute.name + '“ stilllegen? Bestehende Werte bleiben erhalten, neue ' +
          'Einträge kennen das Feld nicht mehr.')) { return; }
        try {
          await KG.api(schemaPath(attribute.id), { method: 'DELETE' });
          say('„' + attribute.name + '“ stillgelegt. Bestehende Werte bleiben erhalten.');
          shop.open = '';
        } catch (error) { say(error.message, 'err'); }
        typePage();
        return;
      }
      const revive = e.target.closest('[data-revive]');
      if (revive) {
        try {
          await KG.api(schemaPath(revive.dataset.revive), { method: 'PATCH', body: { deprecated: false } });
          say('Das Feld ist wieder in Gebrauch — am Ende der Liste.');
        } catch (error) { say(error.message, 'err'); }
        typePage();
      }
    });

    /* Reorder by dragging. The server moves one field with one write when
       there is room between its new neighbours, and renumbers in tens only
       when there is not. */
    let held = null;
    list.querySelectorAll('.kg-fl-row[draggable="true"]').forEach((row) => {
      row.addEventListener('dragstart', (e) => {
        held = row.dataset.field;
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', held);
        row.classList.add('is-dragging');
      });
      row.addEventListener('dragend', () => {
        row.classList.remove('is-dragging');
        list.querySelectorAll('.is-drop').forEach((r) => r.classList.remove('is-drop'));
      });
      row.addEventListener('dragover', (e) => {
        if (!held || row.dataset.field === held) { return; }
        e.preventDefault();
        list.querySelectorAll('.is-drop').forEach((r) => r.classList.remove('is-drop'));
        row.classList.add('is-drop');
      });
      row.addEventListener('drop', async (e) => {
        e.preventDefault();
        const moved = e.dataTransfer.getData('text/plain') || held;
        if (!moved || moved === row.dataset.field) { return; }
        try {
          await KG.api(schemaPath(moved) + '/move', { method: 'POST', body: { before: row.dataset.field } });
          say('Reihenfolge geändert.');
        } catch (error) { say(error.message, 'err'); }
        typePage();
      });
    });

    const type = document.getElementById('kg-nf-type');
    const hint = document.getElementById('kg-nf-hint');
    type.addEventListener('change', () => {
      hint.textContent = type.value ? KG.DATATYPES[type.value].hint : '';
      document.getElementById('kg-nf-enumwrap').hidden = type.value !== 'enum';
      document.getElementById('kg-nf-targetwrap').hidden = type.value !== 'entity_ref';
    });
    document.getElementById('kg-nf-add').addEventListener('click', async () => {
      const name = document.getElementById('kg-nf-name').value.trim();
      const err = document.getElementById('kg-nf-err');
      if (!name || !type.value) { err.innerHTML = KG.msgHTML('Name und Datentyp fehlen.', 'err'); return; }
      const body = { name: name, datatype: type.value, required: document.getElementById('kg-nf-req').checked,
        description: document.getElementById('kg-nf-desc').value };
      if (type.value === 'enum') {
        body.enum_values = document.getElementById('kg-nf-enum').value.split('\n').map((v) => v.trim()).filter(Boolean);
      }
      if (type.value === 'entity_ref' && document.getElementById('kg-nf-target').value) {
        body.target_node_type_id = document.getElementById('kg-nf-target').value;
      }
      try {
        await KG.api(schemaPath(''), { method: 'POST', body: body });
        say('Feld „' + name + '“ angelegt. Es steht im Formular und auf der Karte unter „Nicht zugeordnet“.');
        typePage();
      } catch (error) { err.innerHTML = KG.msgHTML(error.message, 'err'); }
    });
  }

  // ═══ Karte gestalten ════════════════════════════════════════════════════

  const design = { data: null, layout: null, sample: '', samples: {}, busy: false };

  async function cardPage() {
    try { design.data = await KG.api('/api/graph/admin/types/' + encodeURIComponent(typeId) + '/card'); }
    catch (error) { setTitle('Karte gestalten'); fail(error); return; }
    design.layout = design.data.layout;
    if (!design.sample && design.data.samples.length) { design.sample = design.data.samples[0].id; }
    renderDesigner();
  }

  /* Which zone a field stands in: 'head', 'rail', 's<index>' or '' (loose). */
  function zoneOf(id) {
    const l = design.layout;
    if (l.head.indexOf(id) >= 0) { return 'head'; }
    if (l.rail.indexOf(id) >= 0) { return 'rail'; }
    const i = l.sections.findIndex((s) => s.fields.indexOf(id) >= 0);
    return i < 0 ? '' : 's' + i;
  }

  function place(id, zone, index) {
    const l = design.layout;
    l.head = l.head.filter((x) => x !== id);
    l.rail = l.rail.filter((x) => x !== id);
    l.sections.forEach((s) => { s.fields = s.fields.filter((x) => x !== id); });
    const into = (list) => {
      const at = index == null || index < 0 || index > list.length ? list.length : index;
      list.splice(at, 0, id);
    };
    if (zone === 'head') { into(l.head); }
    else if (zone === 'rail') { into(l.rail); }
    else if (zone.charAt(0) === 's') { const s = l.sections[Number(zone.slice(1))]; if (s) { into(s.fields); } }
    const placed = new Set(l.head.concat(l.rail, ...l.sections.map((s) => s.fields)));
    l.loose = design.data.attributes.map((a) => a.id).filter((x) => !placed.has(x));
  }

  function zoneFields(zone) {
    const l = design.layout;
    const ids = zone === 'head' ? l.head : zone === 'rail' ? l.rail
      : zone.charAt(0) === 's' ? (l.sections[Number(zone.slice(1))] || { fields: [] }).fields : l.loose;
    const byId = {};
    design.data.attributes.forEach((a) => { byId[a.id] = a; });
    return ids.map((id) => byId[id]).filter(Boolean);
  }

  async function saveLayout(message) {
    const l = design.layout;
    try {
      const body = await KG.api('/api/graph/cards/' + encodeURIComponent(typeId), { method: 'PUT',
        body: { layout: { head: l.head, rail: l.rail, sections: l.sections } } });
      design.layout = body.layout;
      say(message || 'Kartenaufbau gespeichert.');
    } catch (error) {
      say(error.message, 'err');
      return cardPage();
    }
    renderDesigner();
  }

  async function previewPayload() {
    const attributes = design.data.attributes;
    if (!design.sample) {
      // No entry yet: the preview shows the field names and a gap everywhere,
      // which is more honest than invented sample values in legal software.
      const fields = {};
      attributes.forEach((a) => { fields[a.id] = { missing: true }; });
      return { real: false, node: { name: 'Beispieleintrag' }, attributes: attributes, fields: fields, extra: [] };
    }
    if (!design.samples[design.sample]) {
      design.samples[design.sample] = await KG.api('/api/graph/nodes/' + encodeURIComponent(design.sample));
    }
    const p = design.samples[design.sample];
    return { real: true, node: p.node, attributes: attributes, fields: p.fields, extra: p.extra };
  }

  async function renderDesigner() {
    const d = design.data;
    const t = d.type;
    setTitle('Karte gestalten · ' + t.name, 'Ziehen Sie die Felder in die Zone, in der sie erscheinen sollen — oder ' +
      'wählen Sie die Zone am Feld selbst. Rechts steht dieselbe Karte, die die Kanzlei danach sieht, mit echten Werten.');
    const zones = [['head', 'Kopfzeile', 'Neben dem Titel, als Marke. Für das, was man auf einen Blick braucht.']]
      .concat(design.layout.sections.map((s, i) => ['s' + i, s.name, '']))
      .concat([['rail', 'Seitenleiste', 'Rechts als Kurzübersicht — stille Angaben, die selten geändert werden.'],
        ['', 'Nicht zugeordnet', 'Erscheint auf der Karte als eigener Abschnitt, damit kein Feld unbemerkt verschwindet.']]);
    const zoneHTML = ([key, title, why]) => {
      const fields = zoneFields(key);
      const isSection = key.charAt(0) === 's';
      return '<section class="kg-zone' + (key === '' ? ' is-pool' : '') + '" data-zone="' + key + '"><header><h3>' +
        esc(title) + '</h3>' + (isSection ? '<span class="kg-zacts"><button type="button" class="kg-btn kg-btn--ghost kg-btn--sm" ' +
          'data-rename="' + key + '">Umbenennen</button><button type="button" class="kg-btn kg-btn--ghost kg-btn--sm" data-dropsect="' +
          key + '">Entfernen</button></span>' : why ? '<span class="kg-why">' + esc(why) + '</span>' : '') + '</header>' +
        '<div class="kg-bin' + (fields.length ? '' : ' is-blank') + '">' + fields.map((f) =>
          '<span class="kg-fchip" draggable="true" data-field="' + esc(f.id) + '"><span class="kg-grip" aria-hidden="true">⠿</span>' +
          (f.required ? '<span class="kg-dot" title="Pflichtfeld"></span>' : '') + '<span>' + esc(f.name) + '</span>' +
          '<span class="kg-kind">' + esc(KG.kindLabel(f.datatype)) + '</span><select data-move="' + esc(f.id) +
          '" title="Zone wechseln" aria-label="Zone von ' + esc(f.name) + '">' + zones.map(([k, label]) =>
            '<option value="' + k + '"' + (k === key ? ' selected' : '') + '>' + esc(label) + '</option>').join('') +
          '</select></span>').join('') + '</div></section>';
    };
    let preview;
    try { preview = await previewPayload(); } catch (error) { preview = null; }
    const card = preview ? '<div class="kg-cardhead"><div class="kg-titlebox">' + KG.typemark(t) + '<h1>' +
      esc(preview.node.name) + '</h1><div class="kg-chips">' + (KG.card.headChipsHTML(preview, design.layout) ||
      '<span class="kg-sub">Kopfzeile leer — ein Feld hierher ziehen.</span>') + '</div></div></div>' +
      '<div class="kg-previewgrid"><div>' + KG.card.sectionsHTML(preview, design.layout, { actions: false }) + '</div>' +
      '<div class="kg-rail">' + KG.card.summaryRailHTML(preview, design.layout, 'Noch kein Feld in der Seitenleiste.') + '</div></div>'
      : KG.msgHTML('Die Vorschau konnte den Eintrag nicht lesen.', 'err');
    root.innerHTML = '<nav class="kg-crumb" aria-label="Pfad"><a href="/admin/wissenstypen">Wissenstypen</a> › <a href="/admin/wissenstypen/' +
      esc(encodeURIComponent(t.id)) + '">' + esc(t.name) + '</a> › <span>Karte gestalten</span></nav>' +
      takeSaid() + suggestHTML(d.suggestions) +
      '<div class="kg-designer"><div id="kg-zones">' + zones.map(zoneHTML).join('') +
        '<div class="kg-actions"><input type="text" id="kg-newsect" placeholder="Neuer Abschnitt, z. B. Beteiligte" ' +
        'style="max-width:280px" maxlength="80"><button type="button" class="kg-btn kg-btn--ghost" id="kg-addsect">' +
        'Abschnitt hinzufügen</button></div></div>' +
      '<div class="kg-previewbox"><div class="kg-previewtag"><span class="kg-live"><svg width="9" height="9" viewBox="0 0 10 10" ' +
        'aria-hidden="true"><circle cx="5" cy="5" r="4" fill="currentColor"/></svg>Vorschau</span><span>' +
        (preview && preview.real ? 'echter Eintrag' : 'kein Eintrag vorhanden — Felder ohne Werte') + '</span>' +
        (d.samples.length > 1 ? '<select id="kg-prevpick" aria-label="Eintrag für die Vorschau">' + d.samples.map((s) =>
          '<option value="' + esc(s.id) + '"' + (s.id === design.sample ? ' selected' : '') + '>' + esc(s.name) + '</option>').join('') +
          '</select>' : '') + '</div><div class="kg-previewframe">' + card + '</div>' +
        '<div class="kg-callout" style="margin-top:14px"><b>Verbindungen, Zugriff, Systemangaben und die Reiter</b> ' +
        '(Wissensnetz, Dokumente, Verlauf) stehen auf jeder Karte und sind nicht einstellbar — sie beschreiben den ' +
        'Eintrag selbst, nicht diesen Typ.</div>' + (d.view && d.view.active && design.sample
          ? '<div class="kg-actions"><a class="kg-btn" href="' + esc(KG.entryHref(design.sample, d.view.slug)) +
            '">Echte Karte öffnen →</a></div>' : '') + '</div></div>';
    wireDesigner();
  }

  function wireDesigner() {
    wireSuggest('card', design.data.suggestions, () => { design.samples = {}; cardPage(); });
    // The select on each field: the way that needs no mouse. A layout that
    // only a mouse can reach is no layout for part of the staff.
    root.querySelectorAll('[data-move]').forEach((select) => select.addEventListener('change', () => {
      place(select.dataset.move, select.value);
      saveLayout('Kartenaufbau geändert.');
    }));

    /* Dragging: the chip dropped ON decides the insertion point, otherwise
       every field lands at the end and order could only be set by removing
       and re-adding. */
    let dragged = null;
    root.querySelectorAll('.kg-fchip').forEach((chip) => {
      chip.addEventListener('dragstart', (e) => {
        dragged = chip.dataset.field;
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', dragged);
        chip.classList.add('is-dragging');
      });
      chip.addEventListener('dragend', () => {
        chip.classList.remove('is-dragging');
        root.querySelectorAll('.is-before').forEach((c) => c.classList.remove('is-before'));
      });
      chip.addEventListener('dragover', (e) => {
        if (!dragged || chip.dataset.field === dragged) { return; }
        e.preventDefault();
        root.querySelectorAll('.is-before').forEach((c) => c.classList.remove('is-before'));
        chip.classList.add('is-before');
      });
    });
    root.querySelectorAll('.kg-zone').forEach((zone) => {
      const key = zone.dataset.zone;
      zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('is-over'); });
      zone.addEventListener('dragleave', (e) => { if (!zone.contains(e.relatedTarget)) { zone.classList.remove('is-over'); } });
      zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('is-over');
        const id = e.dataTransfer.getData('text/plain') || dragged;
        if (!id) { return; }
        const before = e.target.closest('.kg-fchip');
        let index = null;
        if (before && before.dataset.field !== id) {
          const at = zoneFields(key).map((f) => f.id).indexOf(before.dataset.field);
          if (at >= 0) { index = at; }
        }
        place(id, key, index);
        saveLayout('Kartenaufbau geändert.');
      });
    });
    root.querySelectorAll('[data-rename]').forEach((b) => b.addEventListener('click', () => {
      const section = design.layout.sections[Number(b.dataset.rename.slice(1))];
      const name = window.prompt('Wie soll der Abschnitt heissen?', section.name);
      if (name && name.trim()) { section.name = name.trim(); saveLayout('Abschnitt umbenannt.'); }
    }));
    root.querySelectorAll('[data-dropsect]').forEach((b) => b.addEventListener('click', () => {
      const l = design.layout;
      const index = Number(b.dataset.dropsect.slice(1));
      if (l.sections.length === 1) { window.alert('Die Karte braucht mindestens einen Abschnitt.'); return; }
      // The fields go back to the pool, not away: removing a section is a
      // statement about the structure, not about the fields.
      const section = l.sections.splice(index, 1)[0];
      l.loose = l.loose.concat(section.fields);
      saveLayout(section.fields.length ? 'Abschnitt „' + section.name + '“ entfernt. ' + section.fields.length +
        ' Feld(er) stehen wieder unter „Nicht zugeordnet“.' : 'Abschnitt „' + section.name + '“ entfernt.');
    }));
    const add = () => {
      const name = document.getElementById('kg-newsect').value.trim();
      if (!name) { return; }
      design.layout.sections.push({ name: name, fields: [] });
      saveLayout('Abschnitt „' + name + '“ angelegt. Jetzt Felder hineinziehen.');
    };
    document.getElementById('kg-addsect').addEventListener('click', add);
    document.getElementById('kg-newsect').addEventListener('keydown', (e) => { if (e.key === 'Enter') { add(); } });
    const pick = document.getElementById('kg-prevpick');
    if (pick) { pick.addEventListener('change', () => { design.sample = pick.value; renderDesigner(); }); }
  }

  // ═══ Verzeichnisse ══════════════════════════════════════════════════════

  const dirs = { data: null, editing: '', newType: new URLSearchParams(location.search).get('typ') || '' };

  async function directoriesPage() {
    try { dirs.data = await KG.api('/api/graph/admin/directories'); } catch (error) { fail(error); return; }
    renderDirectories();
  }

  function slugify(text) {
    const map = { 'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss' };
    let out = '';
    for (const ch of String(text || '').toLowerCase()) { out += map[ch] || ch; }
    return out.normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '').slice(0, 64).replace(/-+$/, '') || 'liste';
  }

  function renderDirectories() {
    const d = dirs.data;
    const types = {};
    d.types.forEach((t) => { types[t.id] = t; });
    const views = d.views;
    const taken = new Set(views.map((v) => v.node_type_id));
    const rows = views.length ? views.map((v, i) => {
      const type = types[v.node_type_id];
      const live = type ? type.attributes : [];
      const cols = v.columns.filter((c) => live.some((a) => a.id === c));
      return '<tr><td class="kg-num"><span class="kg-order"><button type="button" class="kg-btn kg-btn--ghost kg-btn--sm" ' +
        'data-up="' + esc(v.node_type_id) + '"' + (i === 0 ? ' disabled' : '') + ' aria-label="Nach oben">▲</button>' +
        '<button type="button" class="kg-btn kg-btn--ghost kg-btn--sm" data-down="' + esc(v.node_type_id) + '"' +
        (i === views.length - 1 ? ' disabled' : '') + ' aria-label="Nach unten">▼</button></span></td>' +
        '<td><strong>' + esc(v.title) + '</strong><span class="kg-sub"><code>/verzeichnis/' + esc(v.slug) + '</code></span>' +
          (v.subtitle ? '<span class="kg-sub">' + esc(v.subtitle) + '</span>' : '') + '</td>' +
        '<td>' + (type ? KG.typemark(type) : '<span class="kg-gap">Typ nicht mehr vorhanden</span>') + '</td>' +
        '<td class="kg-num">' + (cols.length || '<span class="kg-gap">erste ' + Math.min(4, live.length) + '</span>') + '</td>' +
        '<td class="kg-num">' + v.entries + '</td>' +
        '<td>' + (v.active ? '<span class="kg-pill kg-pill--on">aktiv</span>' : '<span class="kg-pill kg-pill--off">abgeschaltet</span>') + '</td>' +
        '<td><span class="kg-actions" style="margin:0;gap:5px">' +
          '<button type="button" class="kg-btn kg-btn--ghost kg-btn--sm" data-editdir="' + esc(v.node_type_id) + '">Bearbeiten</button>' +
          '<button type="button" class="kg-btn kg-btn--ghost kg-btn--sm" data-toggledir="' + esc(v.node_type_id) + '">' +
            (v.active ? 'Abschalten' : 'Aktivieren') + '</button>' +
          (v.active ? '<a class="kg-btn kg-btn--ghost kg-btn--sm" href="/verzeichnis/' + esc(v.slug) + '">Öffnen</a>' : '') +
          '<button type="button" class="kg-btn kg-btn--danger kg-btn--sm" data-deldir="' + esc(v.node_type_id) + '">Entfernen</button>' +
        '</span></td></tr>';
    }).join('') : '<tr><td class="kg-empty" colspan="7">Noch kein Verzeichnis. Unten steht das erste — dafür braucht ' +
      'es einen Wissenstyp.</td></tr>';

    const editing = dirs.editing && views.find((v) => v.node_type_id === dirs.editing) ? dirs.editing : '';
    const free = d.types.filter((t) => !taken.has(t.id));
    const target = editing || (free.some((t) => t.id === dirs.newType) ? dirs.newType : (free[0] || {}).id || '');
    const view = editing ? views.find((v) => v.node_type_id === editing) : null;
    const live = target && types[target] ? types[target].attributes : [];
    const chosen = new Set(view ? (view.columns.length ? view.columns : live.slice(0, 4).map((a) => a.id))
      : live.slice(0, 5).map((a) => a.id));

    /* No free type and nothing being edited: a form without a type to choose
       would look broken. Say why there is nothing to add instead. */
    const form = !editing && !free.length
      ? '<div class="kg-panel" id="kg-dirform"><h2>Neues Verzeichnis anlegen</h2><p class="kg-hint" style="margin:0">' +
        (d.types.length ? 'Jeder vorhandene Wissenstyp hat bereits ein Verzeichnis — ein Typ trägt höchstens eines, weil ' +
          'zwei Seiten über denselben Bestand zwei Wahrheiten über dieselbe Sache wären. Legen Sie unter ' +
          '<a href="/admin/wissenstypen">Wissenstypen</a> einen neuen Typ an; hier bekommt er dann sein Verzeichnis.'
          : 'Es gibt noch keinen Wissenstyp. Ein Verzeichnis listet die Einträge <em>eines</em> Typs auf — ohne Typ gibt ' +
            'es nichts aufzulisten. Der erste entsteht unter <a href="/admin/wissenstypen">Wissenstypen</a>.') + '</p></div>'
      : '<div class="kg-panel" id="kg-dirform"><h2>' + (editing ? '„' + esc(view.title) + '“ bearbeiten' : 'Neues Verzeichnis anlegen') +
        '</h2><p class="kg-hint">' + (editing ? 'Der Wissenstyp eines bestehenden Verzeichnisses lässt sich nicht tauschen — ' +
          'die Spalten und der Kartenaufbau gehören zu seinen Feldern.' : 'Wählen Sie den Wissenstyp, dessen Einträge die ' +
          'Seite auflisten soll. Alles andere ist Darstellung und jederzeit änderbar.') + '</p>' +
        '<label class="kg-label" for="kg-d-type">Wissenstyp</label>' + (editing
          ? '<p style="margin:0 0 4px">' + KG.typemark(types[editing]) + '</p>'
          : '<select id="kg-d-type">' + d.types.map((t) => '<option value="' + esc(t.id) + '"' + (t.id === target ? ' selected' : '') +
            (taken.has(t.id) ? ' disabled' : '') + '>' + esc(t.name) + ' · ' + t.attributes.length + ' Feld(er)' +
            (taken.has(t.id) ? ' — hat bereits ein Verzeichnis' : '') + '</option>').join('') + '</select>') +
        '<label class="kg-label" for="kg-d-title">Titel</label><input type="text" id="kg-d-title" maxlength="200" autocomplete="off" value="' +
          esc(view ? view.title : (types[target] || {}).name || '') + '">' +
        '<label class="kg-label" for="kg-d-sub">Untertitel</label><input type="text" id="kg-d-sub" maxlength="200" autocomplete="off" value="' +
          esc(view ? view.subtitle : '') + '" placeholder="optional — eine Zeile über der Tabelle">' +
        '<label class="kg-label" for="kg-d-slug">Adresse</label><p class="kg-hint" style="margin:0 0 6px"><code>/verzeichnis/<span id="kg-d-slugview">' +
          esc(view ? view.slug : slugify((types[target] || {}).name)) + '</span></code> ' +
          (editing ? '— leer lassen, um die bestehende Adresse zu behalten. Eine Änderung macht bestehende Lesezeichen ungültig.'
            : '— leer lassen, um sie aus dem Titel zu bilden.') + '</p>' +
        '<input type="text" id="kg-d-slug" maxlength="64" autocomplete="off" spellcheck="false" placeholder="' +
          esc(view ? view.slug : slugify((types[target] || {}).name)) + '">' +
        '<fieldset><legend>Spalten der Tabelle</legend><p class="kg-hint" style="margin-bottom:10px">Ohne Auswahl zeigt die ' +
          'Liste die ersten vier Felder — eine Tabelle nur aus Namen wäre beim ersten Öffnen nutzlos.</p>' +
          '<div class="kg-cols" id="kg-d-cols">' + colsHTML(live, chosen) + '</div></fieldset>' +
        '<div id="kg-d-err"></div><div class="kg-actions"><button type="button" class="kg-btn" id="kg-d-save"' + (target ? '' : ' disabled') +
          '>' + (editing ? 'Änderungen speichern' : 'Verzeichnis anlegen') + '</button>' +
          (editing ? '<button type="button" class="kg-btn kg-btn--ghost" id="kg-d-cancel">Abbrechen</button>'
            : '<span class="kg-sub-inline">Danach erscheint es links in der Navigation.</span>') + '</div></div>';

    root.innerHTML = takeSaid() + suggestHTML(d.suggestions) +
      '<div class="kg-table-wrap"><table><thead><tr><th style="width:96px">Reihenfolge</th><th>Verzeichnis</th>' +
      '<th>Wissenstyp</th><th>Spalten</th><th>Einträge</th><th>Status</th><th><span class="kg-sr">Aktionen</span></th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table></div>' +
      '<p class="kg-note">Die Reihenfolge ist die der Navigation. <strong>Abschalten</strong> nimmt die Seite aus der ' +
      'Navigation und behält Titel, Adresse und Spalten; <strong>Entfernen</strong> löscht diese Einstellungen. Die ' +
      'Einträge selbst und der Aufbau ihrer Karte sind von beidem nicht betroffen — ein Verzeichnis ist nur eine Sicht auf sie.</p>' +
      form;
    wireDirectories(editing, types);
  }

  function colsHTML(live, chosen) {
    return live.length ? live.map((a) => '<label><input type="checkbox" name="kg-dcol" value="' + esc(a.id) + '"' +
      (chosen.has(a.id) ? ' checked' : '') + '> ' + esc(a.name) + ' <span class="kg-sub-inline">' +
      esc(KG.kindLabel(a.datatype)) + '</span></label>').join('')
      : '<span class="kg-sub">Dieser Wissenstyp hat noch keine Felder — die Liste zeigt dann nur die Namen der Einträge.</span>';
  }

  function wireDirectories(editing, types) {
    wireSuggest('dirs', dirs.data.suggestions, directoriesPage);
    // Every change here also changes the navigation on the left, so the page
    // reloads, carrying its message across.
    const act = async (fn, message) => {
      try {
        await fn();
        if (message) { KG.carryFlash(message); }
        location.reload();
      } catch (error) { say(error.message, 'err'); directoriesPage(); }
    };
    const viewPath = (id) => '/api/graph/views/' + encodeURIComponent(id);
    root.querySelectorAll('[data-up]').forEach((b) => b.addEventListener('click', () =>
      act(() => KG.api(viewPath(b.dataset.up) + '/move', { method: 'POST', body: { delta: -1 } }), '')));
    root.querySelectorAll('[data-down]').forEach((b) => b.addEventListener('click', () =>
      act(() => KG.api(viewPath(b.dataset.down) + '/move', { method: 'POST', body: { delta: 1 } }), '')));
    root.querySelectorAll('[data-editdir]').forEach((b) => b.addEventListener('click', () => {
      dirs.editing = b.dataset.editdir;
      renderDirectories();
      document.getElementById('kg-dirform').scrollIntoView({ block: 'start' });
    }));
    root.querySelectorAll('[data-toggledir]').forEach((b) => b.addEventListener('click', () => {
      const view = dirs.data.views.find((v) => v.node_type_id === b.dataset.toggledir);
      act(() => KG.api(viewPath(view.node_type_id), { method: 'PATCH', body: { active: !view.active } }),
        view.active ? '„' + view.title + '“ abgeschaltet. Titel, Adresse und Spalten bleiben gespeichert.'
          : '„' + view.title + '“ ist aktiv: /verzeichnis/' + view.slug);
    }));
    root.querySelectorAll('[data-deldir]').forEach((b) => b.addEventListener('click', () => {
      const view = dirs.data.views.find((v) => v.node_type_id === b.dataset.deldir);
      if (!window.confirm('Verzeichnis „' + view.title + '“ entfernen? Titel, Adresse und Spalten gehen verloren. ' +
        'Die Einträge dieses Typs und der Aufbau ihrer Karte bleiben bestehen.')) { return; }
      if (dirs.editing === view.node_type_id) { dirs.editing = ''; }
      act(() => KG.api(viewPath(view.node_type_id), { method: 'DELETE' }), 'Verzeichnis „' + view.title + '“ entfernt.');
    }));

    const form = document.getElementById('kg-d-save');
    if (!form) { return; }
    const typeSelect = document.getElementById('kg-d-type');
    const title = document.getElementById('kg-d-title');
    const slug = document.getElementById('kg-d-slug');
    const slugView = document.getElementById('kg-d-slugview');
    const current = () => editing || (typeSelect ? typeSelect.value : '');
    const syncSlug = () => {
      const typed = slug.value.trim();
      if (typed) { slugView.textContent = slugify(typed); return; }
      const view = dirs.data.views.find((v) => v.node_type_id === editing);
      slugView.textContent = editing ? view.slug : slugify(title.value || 'liste');
    };
    title.addEventListener('input', syncSlug);
    slug.addEventListener('input', syncSlug);
    // A type change rebuilds only the columns: redrawing the page would lose
    // the title someone just typed.
    if (typeSelect) {
      typeSelect.addEventListener('change', () => {
        const type = types[typeSelect.value];
        dirs.newType = typeSelect.value;
        document.getElementById('kg-d-cols').innerHTML = colsHTML(type.attributes,
          new Set(type.attributes.slice(0, 5).map((a) => a.id)));
        if (!title.value.trim() || dirs.data.types.some((t) => t.name === title.value.trim())) { title.value = type.name; }
        syncSlug();
      });
    }
    const cancel = document.getElementById('kg-d-cancel');
    if (cancel) { cancel.addEventListener('click', () => { dirs.editing = ''; renderDirectories(); }); }
    form.addEventListener('click', async () => {
      const typeId = current();
      if (!typeId) { return; }
      const body = { title: title.value.trim(), subtitle: document.getElementById('kg-d-sub').value.trim(),
        columns: Array.from(root.querySelectorAll('input[name="kg-dcol"]:checked')).map((c) => c.value) };
      if (slug.value.trim()) { body.slug = slug.value.trim(); }
      try {
        if (editing) {
          const saved = await KG.api('/api/graph/views/' + encodeURIComponent(typeId), { method: 'PATCH', body: body });
          KG.carryFlash('„' + saved.view.title + '“ gespeichert: /verzeichnis/' + saved.view.slug);
          dirs.editing = '';
        } else {
          body.node_type_id = typeId;
          const saved = await KG.api('/api/graph/views', { method: 'POST', body: body });
          KG.carryFlash('Verzeichnis „' + saved.view.title + '“ angelegt: /verzeichnis/' + saved.view.slug + '. Der ' +
            'Aufbau seiner Karte steht unter Wissenstypen → ' + types[typeId].name + ' → Karte gestalten.');
          dirs.newType = '';
        }
        // The navigation lists directories; a new one appears on reload.
        location.href = '/admin/verzeichnisse';
      } catch (error) {
        document.getElementById('kg-d-err').innerHTML = KG.msgHTML(error.message, 'err');
      }
    });
  }

  if (page === 'types') { typesPage(); }
  if (page === 'type') { typePage(); }
  if (page === 'card') { cardPage(); }
  if (page === 'dirs') { directoriesPage(); }
})();
