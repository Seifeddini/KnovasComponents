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

  window.Workbench = { api, select, state,
    renderNeighbourhood: renderNeighbourhood, renderFields: renderFields, renderGrants() {} };

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
    await loadTypes();
    await loadNodes();
    const initial = new URLSearchParams(location.search).get('node');
    if (initial) { select(initial); }
  });
})();
