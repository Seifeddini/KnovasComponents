/* Wissensnetz: shared by the directory, entry and console screens.
 *
 * Nothing here knows a node type. Every label, control and column comes from
 * the schema the API returns, so a new type is data entry in the console,
 * never a release.
 *
 * The card renderers below are used by the real entry card AND by the card
 * designer's preview. A preview with its own renderer would be a second
 * truth that one day disagrees with the first and promises an administrator
 * something the firm never sees.
 */
(function () {
  'use strict';

  const meta = document.querySelector('meta[name="csrf-token"]');
  const CSRF = meta ? meta.getAttribute('content') : '';

  async function api(path, options) {
    const opts = options || {};
    const method = opts.method || 'GET';
    const init = { method: method, credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (method !== 'GET') {
      init.headers['Content-Type'] = 'application/json';
      init.headers['X-CSRF-Token'] = CSRF;
      init.body = JSON.stringify(opts.body || {});
    }
    let response;
    try {
      response = await fetch(path, init);
    } catch (networkError) {
      throw Object.assign(new Error('Die Plattform ist gerade nicht erreichbar.'), { status: 0 });
    }
    let body = {};
    try { body = await response.json(); } catch (parseError) { body = {}; }
    if (!response.ok || body.success === false) {
      const message = body.error || ('Die Anfrage ist fehlgeschlagen (' + response.status + ').');
      throw Object.assign(new Error(message), { status: response.status, body: body });
    }
    return body;
  }

  const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ESCAPES[c]);
  }

  function debounce(fn, ms) {
    let handle = null;
    return function () {
      const args = arguments;
      clearTimeout(handle);
      handle = setTimeout(() => fn.apply(null, args), ms);
    };
  }

  const DATATYPES = {
    text: { label: 'Text', short: 'Text', hint: 'Freier Text, eine Zeile oder ein Absatz.' },
    date: { label: 'Datum', short: 'Datum', hint: 'Datum mit Genauigkeit: Tag, Monat oder Jahr.' },
    money: { label: 'Betrag', short: 'Betrag', hint: 'Betrag mit Währung (ISO-4217, z. B. CHF).' },
    enum: { label: 'Auswahl', short: 'Auswahl', hint: 'Auswahl aus einer festen Liste, die Sie hier hinterlegen.' },
    entity_ref: { label: 'Verbindung zu einem anderen Eintrag', short: 'Verbindung',
      hint: 'Zeigt auf einen anderen Eintrag und erzeugt im Wissensnetz eine Kante.' }
  };
  const kindLabel = (datatype) => (DATATYPES[datatype] || { short: datatype }).short;

  function typemark(type) {
    if (!type) { return ''; }
    return '<span class="kg-typemark tone-' + esc(type.tone || 't4') + '"><i></i>' + esc(type.name) + '</span>';
  }

  function entryHref(id, slug) {
    return slug ? '/verzeichnis/' + encodeURIComponent(slug) + '/' + encodeURIComponent(id)
                : '/eintrag/' + encodeURIComponent(id);
  }

  /* A message that survives one navigation: "angelegt" is said on the page
     the person lands on, not on the one they leave. */
  function carryFlash(text) {
    try { sessionStorage.setItem('kg-flash', text); } catch (e) { /* private mode */ }
  }
  function takeFlash() {
    try {
      const text = sessionStorage.getItem('kg-flash');
      sessionStorage.removeItem('kg-flash');
      return text || '';
    } catch (e) { return ''; }
  }
  function msgHTML(text, kind) {
    if (!text) { return ''; }
    return '<p class="kg-msg kg-msg--' + (kind || 'ok') + '" role="' + (kind === 'err' ? 'alert' : 'status') +
      '">' + esc(text) + '</p>';
  }

  // ── Werte eingeben: ein Steuerelement je Datentyp ──────────────────────

  const MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August',
    'September', 'Oktober', 'November', 'Dezember'];

  function dateForInput(value) {
    if (!value || !value.value) { return ''; }
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value.value));
    if (!m) { return String(value.value); }
    if (value.precision === 'year') { return m[1]; }
    if (value.precision === 'month') { return m[2] + '.' + m[1]; }
    return m[3] + '.' + m[2] + '.' + m[1];
  }

  /* TT.MM.JJJJ, MM.JJJJ, JJJJ or ISO. The API stores a full date with its
     precision; a month-precise date is kept as the 1st of the month and is
     never shown as that day. */
  function parseDate(raw, precision, fieldName) {
    const text = raw.trim();
    let y = null, mo = null, d = null, m;
    if ((m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(text))) { y = m[1]; mo = m[2]; d = m[3]; }
    else if ((m = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(text))) { d = m[1]; mo = m[2]; y = m[3]; }
    else if ((m = /^(\d{4})-(\d{1,2})$/.exec(text))) { y = m[1]; mo = m[2]; }
    else if ((m = /^(\d{1,2})\.(\d{4})$/.exec(text))) { mo = m[1]; y = m[2]; }
    else if ((m = /^(\d{4})$/.exec(text))) { y = m[1]; }
    else {
      throw new Error('„' + fieldName + '“: „' + text + '“ ist kein Datum (TT.MM.JJJJ, MM.JJJJ oder JJJJ).');
    }
    if (precision === 'day' && !d) {
      throw new Error('„' + fieldName + '“ ist auf den Tag genau gewählt, „' + text + '“ nennt aber keinen Tag.');
    }
    if (precision === 'month' && !mo) {
      throw new Error('„' + fieldName + '“ ist auf den Monat genau gewählt, es fehlt aber der Monat.');
    }
    const pad = (n) => String(n).padStart(2, '0');
    const month = precision === 'year' ? '01' : pad(mo || 1);
    const day = precision === 'day' ? pad(d) : '01';
    const check = new Date(Date.UTC(Number(y), Number(month) - 1, Number(day)));
    if (check.getUTCMonth() !== Number(month) - 1 || check.getUTCDate() !== Number(day)) {
      throw new Error('„' + fieldName + '“: „' + text + '“ gibt es im Kalender nicht.');
    }
    return { value: y + '-' + month + '-' + day, precision: precision };
  }

  /* candidates: [{id, name, node_type_id}] for entity_ref pickers;
     types: {id: {name}} to group them. */
  function control(attribute, current, context) {
    const ctx = context || {};
    const name = attribute.name;
    const idBase = 'kg-f-' + attribute.id + '-' + Math.random().toString(36).slice(2, 7);

    if (attribute.datatype === 'enum') {
      const select = document.createElement('select');
      select.id = idBase;
      select.add(new Option('— keine Angabe —', ''));
      (attribute.enum_values || []).forEach((v) => select.add(new Option(v, v, false, v === current)));
      if (current && (attribute.enum_values || []).indexOf(current) < 0) {
        select.add(new Option(current + ' (nicht mehr in der Liste)', current, true, true));
      }
      return { node: select, focus: select, read: () => select.value || null };
    }
    if (attribute.datatype === 'date') {
      const box = document.createElement('div');
      box.className = 'kg-pair';
      const input = document.createElement('input');
      input.type = 'text'; input.id = idBase; input.placeholder = 'TT.MM.JJJJ';
      input.value = dateForInput(current);
      const precision = document.createElement('select');
      precision.setAttribute('aria-label', 'Genauigkeit');
      [['day', 'Tag genau'], ['month', 'Monat genau'], ['year', 'Jahr genau']].forEach(([k, label]) =>
        precision.add(new Option(label, k, false, (current && current.precision) === k)));
      box.append(input, precision);
      return { node: box, focus: input, read: () => {
        if (!input.value.trim()) { return null; }
        return parseDate(input.value, precision.value, name);
      } };
    }
    if (attribute.datatype === 'money') {
      const box = document.createElement('div');
      box.className = 'kg-pair';
      const amount = document.createElement('input');
      amount.type = 'text'; amount.id = idBase; amount.inputMode = 'decimal'; amount.placeholder = '0.00';
      amount.value = current && current.amount != null ? String(current.amount) : '';
      const currency = document.createElement('input');
      currency.type = 'text'; currency.className = 'kg-cur'; currency.maxLength = 3;
      currency.setAttribute('aria-label', 'Währung');
      currency.value = (current && current.currency) || 'CHF';
      box.append(amount, currency);
      return { node: box, focus: amount, read: () => {
        const raw = amount.value.trim().replace(/[’'\s]/g, '').replace(',', '.');
        if (!raw) { return null; }
        if (!/^-?\d+(\.\d+)?$/.test(raw)) { throw new Error('„' + name + '“: „' + amount.value + '“ ist kein Betrag.'); }
        const code = currency.value.trim().toUpperCase();
        if (!/^[A-Z]{3}$/.test(code)) { throw new Error('„' + currency.value + '“ ist kein ISO-4217-Code (drei Buchstaben, z. B. CHF).'); }
        return { amount: raw, currency: code };
      } };
    }
    if (attribute.datatype === 'entity_ref') {
      const select = document.createElement('select');
      select.id = idBase;
      select.add(new Option('— keine Angabe —', ''));
      const wanted = current && current.node_id;
      const candidates = (ctx.candidates || []).filter((n) =>
        !attribute.target_node_type_id || n.node_type_id === attribute.target_node_type_id);
      const groups = {};
      candidates.forEach((n) => { (groups[n.node_type_id] = groups[n.node_type_id] || []).push(n); });
      Object.keys(groups).forEach((typeId) => {
        const group = document.createElement('optgroup');
        group.label = ((ctx.types || {})[typeId] || {}).name || 'Ohne Typ';
        groups[typeId].forEach((n) => group.appendChild(new Option(n.name, n.id, false, n.id === wanted)));
        select.appendChild(group);
      });
      if (wanted && !candidates.some((n) => n.id === wanted)) {
        select.add(new Option('Nicht sichtbarer Eintrag', wanted, true, true));
      }
      return { node: select, focus: select, read: () => (select.value ? { node_id: select.value } : null) };
    }
    const input = document.createElement(current && String(current).indexOf('\n') >= 0 ? 'textarea' : 'input');
    if (input.tagName === 'INPUT') { input.type = 'text'; }
    input.id = idBase;
    input.value = current == null ? '' : String(current);
    return { node: input, focus: input, read: () => (input.value.trim() ? input.value : null) };
  }

  // ── Die Karte ──────────────────────────────────────────────────────────

  function attrById(payload) {
    const map = {};
    (payload.attributes || []).forEach((a) => { map[a.id] = a; });
    return map;
  }

  function valueHTML(attribute, cell) {
    if (!cell || cell.missing) {
      return attribute.required
        ? '<span class="kg-pill kg-pill--gap" title="Pflichtfeld ohne Wert">fehlt</span>'
        : '<span class="kg-gap">—</span>';
    }
    if (cell.ref) {
      return '<a class="kg-link" href="' + esc(entryHref(cell.ref.id)) + '">' + esc(cell.ref.name) + '</a>';
    }
    return esc(cell.display);
  }

  function headChipsHTML(payload, layout) {
    const attrs = attrById(payload);
    return (layout.head || []).map((id) => {
      const attribute = attrs[id];
      if (!attribute) { return ''; }
      return '<span class="kg-chip"><b>' + esc(attribute.name) + '</b>' +
        valueHTML(attribute, (payload.fields || {})[id]) + '</span>';
    }).join('');
  }

  function fieldRowHTML(payload, attribute, opts) {
    const o = opts || {};
    const cell = (payload.fields || {})[attribute.id];
    const shown = cell && !cell.missing;
    const actions = o.actions && !o.rail
      ? '<span class="kg-fa"><button type="button" class="kg-btn kg-btn--ghost kg-btn--sm" data-edit="' +
        esc(attribute.id) + '">' + (shown ? 'Ändern' : 'Eintragen') + '</button></span>'
      : (o.rail ? '' : '<span></span>');
    return '<div class="kg-frow" data-field="' + esc(attribute.id) + '">' +
      '<span class="kg-fl">' + esc(attribute.name) + '</span>' +
      '<span class="kg-fv">' + valueHTML(attribute, cell) +
        (!o.rail && attribute.description ? '<span class="kg-desc">' + esc(attribute.description) + '</span>' : '') +
      '</span>' + actions + '</div>';
  }

  /* The sections as the card shows them -- including the fields nobody placed
     yet. Leaving those out would make a newly added field vanish until
     someone remembered to place it. */
  function sectionsHTML(payload, layout, opts) {
    const attrs = attrById(payload);
    const sections = (layout.sections || []).map((s) => ({
      name: s.name, fields: (s.fields || []).map((id) => attrs[id]).filter(Boolean)
    })).filter((s) => s.fields.length);
    const loose = (layout.loose || []).map((id) => attrs[id]).filter(Boolean);
    if (loose.length) { sections.push({ name: 'Nicht zugeordnet', fields: loose, loose: true }); }
    let html = sections.map((s) =>
      '<section class="kg-sect"><header><h3>' + esc(s.name) + '</h3>' +
      (s.loose ? '<span class="kg-n">von der Verwaltung noch nicht einsortiert</span>'
               : '<span class="kg-n">' + s.fields.length + '</span>') + '</header>' +
      s.fields.map((a) => fieldRowHTML(payload, a, opts)).join('') + '</section>').join('');
    if (!sections.length) {
      html = '<section class="kg-sect"><div class="kg-frow"><span class="kg-fv kg-gap">' +
        'Für diesen Typ sind noch keine Felder definiert.</span></div></section>';
    }
    const extra = payload.extra || [];
    if (extra.length) {
      html += '<section class="kg-sect"><header><h3>Weitere Angaben</h3></header>' +
        extra.map((e) => '<div class="kg-frow"><span class="kg-fl">' + esc(e.name) + '</span>' +
          '<span class="kg-fv">' + esc(e.display) + '<span class="kg-desc">' +
          (e.kind === 'retired' ? 'Das Feld wurde stillgelegt; der Wert bleibt erhalten und wird nur noch gelesen.'
            : e.kind === 'free' ? 'Freie Angabe ohne Felddefinition.'
              : 'Zu einem Feld, das dieser Typ nicht (mehr) kennt.') +
          '</span></span><span></span></div>').join('') + '</section>';
    }
    return html;
  }

  function summaryRailHTML(payload, layout, emptyText) {
    const attrs = attrById(payload);
    const fields = (layout.rail || []).map((id) => attrs[id]).filter(Boolean);
    if (!fields.length) {
      return emptyText ? '<section class="kg-sect"><header><h3>Kurzübersicht</h3></header>' +
        '<div class="kg-frow"><span class="kg-fv kg-gap">' + esc(emptyText) + '</span></div></section>' : '';
    }
    return '<section class="kg-sect"><header><h3>Kurzübersicht</h3></header>' +
      fields.map((a) => fieldRowHTML(payload, a, { rail: true })).join('') + '</section>';
  }

  window.KG = {
    api: api, esc: esc, debounce: debounce, DATATYPES: DATATYPES, kindLabel: kindLabel,
    typemark: typemark, entryHref: entryHref, carryFlash: carryFlash, takeFlash: takeFlash,
    msgHTML: msgHTML, control: control, MONTHS: MONTHS,
    card: { valueHTML: valueHTML, headChipsHTML: headChipsHTML, fieldRowHTML: fieldRowHTML,
            sectionsHTML: sectionsHTML, summaryRailHTML: summaryRailHTML }
  };
})();
