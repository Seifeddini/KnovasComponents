/* Arbeitsplatz: list -> neighbourhood -> fields.
 *
 * Nothing here knows a node type. Every column, control and label comes from
 * GET /api/graph/node-types/<id>/schema at runtime, so a new type is data
 * entry rather than a deploy.
 */
(function () {
  'use strict';

  const csrf = document.querySelector('meta[name="csrf-token"]');
  const CSRF = csrf ? csrf.getAttribute('content') : '';

  const state = { types: [], nodes: [], typeFilter: null, query: '', selected: null };

  async function api(path, options) {
    const response = await fetch(path, Object.assign({
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
      credentials: 'same-origin'
    }, options || {}));
    const body = await response.json().catch(() => ({}));
    if (!response.ok) { throw Object.assign(new Error(body.error || 'Fehler'), { status: response.status, body }); }
    return body;
  }

  function debounce(fn, ms) {
    let handle;
    return function () {
      const args = arguments;
      clearTimeout(handle);
      handle = setTimeout(() => fn.apply(null, args), ms);
    };
  }

  async function loadTypes() {
    const body = await api('/api/graph/node-types');
    state.types = body.node_types || [];
    const container = document.getElementById('typeFilter');
    container.innerHTML = '';
    container.appendChild(chip('Alle', null));
    state.types.forEach((type) => container.appendChild(chip(type.name, type.id)));
  }

  function chip(label, typeId) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'workbench-chip' + (state.typeFilter === typeId ? ' is-active' : '');
    button.textContent = label;
    button.addEventListener('click', () => {
      state.typeFilter = typeId;
      loadTypes();
      loadNodes();
    });
    return button;
  }

  async function loadNodes() {
    const params = new URLSearchParams();
    if (state.typeFilter) { params.set('type', state.typeFilter); }
    if (state.query) { params.set('q', state.query); }
    const body = await api('/api/graph/nodes?' + params.toString());
    state.nodes = body.nodes || [];
    renderNodes();
  }

  function renderNodes() {
    const nodes = state.nodes;
    const list = document.getElementById('nodeList');
    const empty = document.getElementById('nodeListEmpty');
    list.innerHTML = '';
    if (!nodes.length) {
      empty.hidden = false;
      // Say what was searched. The server matches the NAME only; implying it
      // searched field contents would send people looking for a bug.
      empty.textContent = state.query
        ? 'Keine Objekte, deren Name "' + state.query + '" enthaelt.'
        : 'Noch keine Objekte vorhanden.';
      return;
    }
    empty.hidden = true;
    nodes.forEach((node) => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'workbench-node' + (state.selected === node.id ? ' is-selected' : '');
      button.textContent = node.name;
      button.addEventListener('click', () => select(node.id));
      item.appendChild(button);
      list.appendChild(item);
    });
  }

  async function select(nodeId) {
    state.selected = nodeId;
    history.replaceState(null, '', '?node=' + encodeURIComponent(nodeId));
    renderNodes();                       // repaint so the selection marker moves
    const payload = await api('/api/graph/nodes/' + encodeURIComponent(nodeId));
    window.Workbench.renderNeighbourhood(payload);   // E2
    window.Workbench.renderFields(payload);          // E3
    window.Workbench.renderGrants(payload);          // E4
  }

  let cy = null;

  function renderNeighbourhood(payload) {
    const container = document.getElementById('neighbourhoodGraph');
    const anchor = payload.node;
    const neighbours = (payload.neighbourhood && payload.neighbourhood.nodes) || [];
    const edges = (payload.neighbourhood && payload.neighbourhood.edges) || [];

    if (cy) { cy.destroy(); cy = null; }
    container.innerHTML = '';
    if (!neighbours.length && !edges.length) {
      container.setAttribute('aria-hidden', 'true');
      container.innerHTML = '<p class="workbench-empty">Keine direkten Verbindungen.</p>';
      return;
    }

    const elements = [{ data: { id: anchor.id, label: anchor.name }, classes: 'anchor' }];
    neighbours.forEach((n) => elements.push({ data: { id: n.id, label: n.name } }));

    // Only edges whose BOTH endpoints are drawn. The server already guarantees
    // this, but an edge to a node we did not render would draw to nowhere.
    const drawn = new Set(elements.map((e) => e.data.id));
    edges.forEach((edge) => {
      if (drawn.has(edge.node_lo) && drawn.has(edge.node_hi)) {
        elements.push({ data: { id: edge.id, source: edge.node_lo,
                                target: edge.node_hi, label: edge.relation } });
      }
    });

    cy = cytoscape({
      container: container,
      elements: elements,
      style: [
        { selector: 'node', style: {
            'label': 'data(label)', 'font-size': 11, 'text-valign': 'center',
            'background-color': '#9aa7b8', 'color': '#fff',
            'text-outline-width': 2, 'text-outline-color': '#5a6675' } },
        { selector: 'node.anchor', style: { 'background-color': '#2f6fb0', 'width': 44, 'height': 44 } },
        { selector: 'edge', style: {
            'label': 'data(label)', 'font-size': 9, 'curve-style': 'bezier',
            'width': 1.5, 'line-color': '#c3ccd8', 'target-arrow-shape': 'none',
            'text-rotation': 'autorotate' } }
      ],
      layout: { name: 'concentric', concentric: (n) => (n.hasClass('anchor') ? 2 : 1),
                minNodeSpacing: 40 }
    });

    // Walking the graph IS the navigation: clicking a neighbour selects it.
    cy.on('tap', 'node', (event) => {
      const id = event.target.id();
      if (id !== anchor.id) { select(id); }
    });

    container.setAttribute('aria-hidden', neighbours.length ? 'false' : 'true');
  }

  function renderFields(payload) {
    document.getElementById('fieldReaderTitle').textContent = payload.node.name;
    const host = document.getElementById('fieldReader');
    host.innerHTML = '';

    const fields = payload.fields || [];
    if (!fields.length) {
      host.innerHTML = '<p class="workbench-empty">Fuer diesen Typ sind noch keine Felder definiert.</p>';
    } else {
      const list = document.createElement('dl');
      list.className = 'workbench-fieldlist';
      fields.forEach((field) => {
        const term = document.createElement('dt');
        term.textContent = field.name;
        if (field.required) { term.classList.add('is-required'); }

        const value = document.createElement('dd');
        if (field.missing) {
          // A gap, not an error. The completeness report exists to count these,
          // which requires the node to have been creatable without them.
          value.className = 'is-missing';
          value.textContent = field.required ? 'Fehlt' : '\u2014';
        } else if (field.datatype === 'entity_ref' && field.value && field.value.node_id) {
          const link = document.createElement('button');
          link.type = 'button';
          link.className = 'workbench-ref';
          link.textContent = field.display || field.value.node_id;
          link.addEventListener('click', () => select(field.value.node_id));
          value.appendChild(link);
        } else {
          value.textContent = field.display;
        }
        list.appendChild(term);
        list.appendChild(value);
      });
      host.appendChild(list);
    }

    const groups = (payload.visibility && payload.visibility.access_group_ids) || [];
    const visibility = document.createElement('p');
    visibility.className = 'workbench-visibility';
    visibility.textContent = groups.length
      ? 'Sichtbarkeit: ' + groups.join(', ')
      : 'Sichtbarkeit: keine Einschraenkung';
    host.appendChild(visibility);
  }

  function showFormErrors(problems, hostId) {
    const target = document.getElementById(hostId || 'nodeCreateErrors');
    if (!target) {
      window.alert(problems.join('\n'));
      return;
    }
    target.hidden = !problems.length;
    target.textContent = problems.join(' ');
  }

  // One control per datatype. This map IS the reason no node type appears in
  // code: adding a type adds rows here, never a branch.
  function controlFor(attribute) {
    const wrap = document.createElement('div');
    wrap.className = 'workbench-field';
    const label = document.createElement('label');
    label.textContent = attribute.name + (attribute.required ? ' *' : '');
    label.htmlFor = 'attr-' + attribute.id;
    wrap.appendChild(label);

    let control;
    switch (attribute.datatype) {
      case 'date': {
        control = document.createElement('div');
        const day = document.createElement('input');
        day.type = 'date'; day.id = 'attr-' + attribute.id;
        const precision = document.createElement('select');
        [['day', 'Tag genau'], ['month', 'Monat genau'], ['year', 'Jahr genau']]
          .forEach(([v, t]) => precision.add(new Option(t, v)));
        precision.className = 'workbench-precision';
        control.appendChild(day); control.appendChild(precision);
        break;
      }
      case 'money': {
        control = document.createElement('div');
        const amount = document.createElement('input');
        amount.type = 'text'; amount.inputMode = 'decimal';
        amount.id = 'attr-' + attribute.id; amount.placeholder = '0.00';
        const currency = document.createElement('input');
        currency.type = 'text'; currency.value = 'CHF'; currency.maxLength = 3;
        currency.className = 'workbench-currency';
        control.appendChild(amount); control.appendChild(currency);
        break;
      }
      case 'enum': {
        control = document.createElement('select');
        control.id = 'attr-' + attribute.id;
        control.add(new Option('\u2014', ''));
        (attribute.enum_values || []).forEach((v) => control.add(new Option(v, v)));
        break;
      }
      case 'entity_ref': {
        control = document.createElement('select');
        control.id = 'attr-' + attribute.id;
        control.add(new Option('\u2014', ''));
        // target_node_type_id filters the picker to the declared type. Null on
        // attributes created before that field existed: then offer everything.
        const params = new URLSearchParams();
        if (attribute.target_node_type_id) { params.set('type', attribute.target_node_type_id); }
        api('/api/graph/nodes?' + params.toString()).then((body) => {
          (body.nodes || []).forEach((n) => control.add(new Option(n.name, n.id)));
        });
        break;
      }
      default:
        control = document.createElement('input');
        control.type = 'text';
        control.id = 'attr-' + attribute.id;
    }
    control.dataset.attributeId = attribute.id;
    control.dataset.datatype = attribute.datatype;
    wrap.appendChild(control);
    return wrap;
  }

  function readControl(wrap) {
    const control = wrap.querySelector('[data-attribute-id]');
    const datatype = control.dataset.datatype;
    if (datatype === 'date') {
      const day = wrap.querySelector('input[type="date"]').value;
      if (!day) { return null; }
      return { value: day, precision: wrap.querySelector('.workbench-precision').value };
    }
    if (datatype === 'money') {
      const amount = wrap.querySelector('input[inputmode="decimal"]').value.trim();
      if (!amount) { return null; }
      return { amount: amount, currency: wrap.querySelector('.workbench-currency').value.trim() };
    }
    const raw = control.value.trim();
    if (!raw) { return null; }
    return datatype === 'entity_ref' ? { node_id: raw } : raw;
  }

  async function rebuildCreateFields(typeId) {
    const host = document.getElementById('nodeCreateFields');
    host.innerHTML = '';
    if (!typeId) { return; }
    const body = await api('/api/graph/node-types/' + encodeURIComponent(typeId) + '/schema');
    (body.attributes || []).forEach((attribute) => host.appendChild(controlFor(attribute)));
  }

  async function submitNewNode(typeId, name, fieldWraps) {
    const created = await api('/api/graph/nodes', {
      method: 'POST',
      body: JSON.stringify({ name: name, node_type_id: typeId || null })
    });
    const nodeId = created.node.id;

    // Facts are written one at a time and a rejected value never blocks the
    // node: the node exists, the field stays empty and visible as a gap.
    // Schemas are overlays, not write gates.
    const problems = [];
    for (const wrap of fieldWraps) {
      const control = wrap.querySelector('[data-attribute-id]');
      const value = readControl(wrap);
      if (value === null) { continue; }
      try {
        await api('/api/graph/nodes/' + encodeURIComponent(nodeId) + '/facts', {
          method: 'POST',
          body: JSON.stringify({ attribute_id: control.dataset.attributeId, value: value })
        });
      } catch (error) {
        problems.push(wrap.querySelector('label').textContent + ': ' + error.message);
      }
    }
    if (problems.length) { showFormErrors(problems); }
    await loadNodes();
    await select(nodeId);
  }

  function fillTypeSelect(select, includeBlank) {
    select.innerHTML = '';
    if (includeBlank) { select.add(new Option('\u2014', '')); }
    state.types.forEach((type) => select.add(new Option(type.name, type.id)));
  }

  function openCreateDialog() {
    const dialog = document.getElementById('nodeCreateDialog');
    const typeSelect = document.getElementById('nodeCreateType');
    showFormErrors([]);
    document.getElementById('nodeCreateName').value = '';
    fillTypeSelect(typeSelect, true);
    if (state.typeFilter) { typeSelect.value = state.typeFilter; }
    rebuildCreateFields(typeSelect.value);
    dialog.showModal();
  }

  const DATATYPE_LABELS = {
    text: 'Text', date: 'Datum', money: 'Betrag',
    enum: 'Auswahl', entity_ref: 'Verknuepfung'
  };

  async function openTypeWorkshop(typeId) {
    const dialog = document.getElementById('typeWorkshopDialog');
    if (!dialog) { return; }
    showFormErrors([], 'typeWorkshopErrors');
    const typeSelect = document.getElementById('typeWorkshopType');
    fillTypeSelect(typeSelect, false);
    if (typeId) { typeSelect.value = typeId; }
    const current = typeSelect.value;
    const table = document.getElementById('typeWorkshopAttributes');
    table.innerHTML = '';
    if (current) {
      const body = await api('/api/graph/node-types/' + encodeURIComponent(current) + '/schema');
      (body.attributes || []).forEach((attribute) => {
        const row = document.createElement('tr');

        const name = document.createElement('td');
        name.textContent = attribute.name;
        const datatype = document.createElement('td');
        datatype.textContent = DATATYPE_LABELS[attribute.datatype] || attribute.datatype;
        const required = document.createElement('td');
        required.textContent = attribute.required ? 'Pflicht' : 'Optional';

        const actions = document.createElement('td');
        const retire = document.createElement('button');
        retire.type = 'button';
        retire.className = 'btn btn-outline';
        // "Stilllegen", never "Loeschen": the server soft-deprecates and the
        // facts survive. Offering a delete would promise something untrue.
        retire.textContent = 'Stilllegen';
        retire.addEventListener('click', async () => {
          if (!confirm('Attribut wird stillgelegt -- bestehende Fakten bleiben erhalten.')) {
            return;
          }
          await api('/api/graph/node-types/' + encodeURIComponent(current) +
                    '/schema/' + encodeURIComponent(attribute.id), { method: 'DELETE' });
          openTypeWorkshop(current);
        });
        actions.appendChild(retire);

        [name, datatype, required, actions].forEach((cell) => row.appendChild(cell));
        table.appendChild(row);
      });
    }
    const target = document.getElementById('newAttrTargetType');
    fillTypeSelect(target, true);
    dialog.showModal();
  }

  function syncNewAttrExtras() {
    const datatype = document.getElementById('newAttrDatatype').value;
    const enumOn = datatype === 'enum';
    const refOn = datatype === 'entity_ref';
    document.getElementById('newAttrEnumLabel').hidden = !enumOn;
    document.getElementById('newAttrEnumValues').hidden = !enumOn;
    document.getElementById('newAttrTargetLabel').hidden = !refOn;
    document.getElementById('newAttrTargetType').hidden = !refOn;
  }

  async function addAttribute(typeId) {
    const datatype = document.getElementById('newAttrDatatype').value;
    const payload = {
      name: document.getElementById('newAttrName').value.trim(),
      datatype: datatype,
      required: document.getElementById('newAttrRequired').checked,
      sort_order: Number(document.getElementById('newAttrSortOrder').value || 0)
    };
    if (datatype === 'enum') {
      payload.enum_values = document.getElementById('newAttrEnumValues').value
        .split(',').map((v) => v.trim()).filter(Boolean);
    }
    if (datatype === 'entity_ref') {
      const target = document.getElementById('newAttrTargetType').value;
      if (target) { payload.target_node_type_id = target; }
    }
    try {
      await api('/api/graph/node-types/' + encodeURIComponent(typeId) + '/schema', {
        method: 'POST', body: JSON.stringify(payload)
      });
    } catch (error) {
      showFormErrors([error.message], 'typeWorkshopErrors');
      return;
    }
    document.getElementById('newAttrName').value = '';
    openTypeWorkshop(typeId);
  }

  function person(entry, role, mayRevoke, payload) {
    const item = document.createElement('li');
    const label = document.createElement('span');
    label.textContent = (entry.display_name || entry.email || entry.id) + ' \u00b7 ' + role;
    item.appendChild(label);
    if (mayRevoke) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn btn-outline';
      button.textContent = 'Entziehen';
      button.addEventListener('click', async () => {
        try {
          await api('/api/graph/nodes/' + encodeURIComponent(payload.node.id) +
                    '/grants/' + encodeURIComponent(entry.id), { method: 'DELETE' });
          renderGrants(payload);
        } catch (error) {
          // 409 is the owner-revoke refusal; show the server's words.
          showFormErrors([error.message], null);
          const host = document.getElementById('grantsPanel');
          const note = document.createElement('p');
          note.className = 'workbench-form-errors';
          note.textContent = error.message;
          host.appendChild(note);
        }
      });
      item.appendChild(button);
    }
    return item;
  }

  function addEditorControl(payload) {
    const form = document.createElement('form');
    form.className = 'workbench-add-editor';
    const input = document.createElement('input');
    input.type = 'email';
    input.required = true;
    input.placeholder = 'E-Mail des Bearbeiters';
    const button = document.createElement('button');
    button.type = 'submit';
    button.className = 'btn btn-outline';
    button.textContent = 'Freigeben';
    form.appendChild(input);
    form.appendChild(button);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        await api('/api/graph/nodes/' + encodeURIComponent(payload.node.id) + '/grants', {
          method: 'POST',
          body: JSON.stringify({ email: input.value.trim() })
        });
        renderGrants(payload);
      } catch (error) {
        const note = document.createElement('p');
        note.className = 'workbench-form-errors';
        note.textContent = error.message;
        form.appendChild(note);
      }
    });
    return form;
  }

  function renderGrants(payload) {
    const host = document.getElementById('grantsPanel');
    host.innerHTML = '';
    const heading = document.createElement('h3');
    heading.textContent = 'Bearbeitung';
    host.appendChild(heading);

    api('/api/graph/nodes/' + encodeURIComponent(payload.node.id) + '/grants')
      .then((body) => {
        const list = document.createElement('ul');
        list.className = 'workbench-grants';
        if (body.owner) {
          list.appendChild(person(body.owner, 'Eigentuemer', false, payload));
        }
        (body.editors || []).forEach((editor) => {
          list.appendChild(person(editor, 'Bearbeiter', payload.may_write, payload));
        });
        host.appendChild(list);
        if (payload.may_write) { host.appendChild(addEditorControl(payload)); }
      });
  }

  window.Workbench = { api, select, state,
    renderNeighbourhood: renderNeighbourhood, renderFields: renderFields,
    renderGrants: renderGrants };

  document.addEventListener('DOMContentLoaded', async () => {
    if (!document.getElementById('nodeList')) { return; }   // fixture mode
    document.getElementById('nodeSearch').addEventListener('input', debounce((event) => {
      state.query = event.target.value.trim();
      loadNodes();
    }, 250));
    const zoomFit = document.getElementById('zoomFit');
    if (zoomFit) {
      zoomFit.addEventListener('click', () => { if (cy) { cy.fit(); } });
    }
    document.getElementById('nodeCreate').addEventListener('click', openCreateDialog);
    document.getElementById('nodeCreateType').addEventListener('change', (event) => {
      rebuildCreateFields(event.target.value);
    });
    document.getElementById('nodeCreateCancel').addEventListener('click', () => {
      document.getElementById('nodeCreateDialog').close();
    });
    document.getElementById('nodeCreateForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const name = document.getElementById('nodeCreateName').value.trim();
      if (!name) {
        showFormErrors(['Name fehlt.']);
        return;
      }
      const typeId = document.getElementById('nodeCreateType').value;
      const wraps = Array.from(document.getElementById('nodeCreateFields').children);
      try {
        await submitNewNode(typeId, name, wraps);
        document.getElementById('nodeCreateDialog').close();
      } catch (error) {
        showFormErrors([error.message]);
      }
    });
    const workshopBtn = document.getElementById('typeWorkshop');
    if (workshopBtn) {
      workshopBtn.addEventListener('click', () => openTypeWorkshop(state.typeFilter));
      document.getElementById('typeWorkshopType').addEventListener('change', (event) => {
        openTypeWorkshop(event.target.value);
      });
      document.getElementById('newAttrDatatype').addEventListener('change', syncNewAttrExtras);
      document.getElementById('newAttrAdd').addEventListener('click', () => {
        const typeId = document.getElementById('typeWorkshopType').value;
        if (typeId) { addAttribute(typeId); }
      });
      document.getElementById('newTypeCreate').addEventListener('click', async () => {
        const name = document.getElementById('newTypeName').value.trim();
        if (!name) {
          showFormErrors(['Name fehlt.'], 'typeWorkshopErrors');
          return;
        }
        try {
          const created = await api('/api/graph/node-types', {
            method: 'POST', body: JSON.stringify({ name: name })
          });
          document.getElementById('newTypeName').value = '';
          await loadTypes();
          openTypeWorkshop(created.node_type && created.node_type.id);
        } catch (error) {
          showFormErrors([error.message], 'typeWorkshopErrors');
        }
      });
      document.getElementById('typeWorkshopClose').addEventListener('click', () => {
        document.getElementById('typeWorkshopDialog').close();
      });
      syncNewAttrExtras();
    }
    await loadTypes();
    await loadNodes();
    const initial = new URLSearchParams(location.search).get('node');
    if (initial) { select(initial); }
  });
})();
