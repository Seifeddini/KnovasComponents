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

  window.Workbench = { api, select, state,
    renderNeighbourhood() {}, renderFields() {}, renderGrants() {} };

  document.addEventListener('DOMContentLoaded', async () => {
    if (!document.getElementById('nodeList')) { return; }   // fixture mode
    document.getElementById('nodeSearch').addEventListener('input', debounce((event) => {
      state.query = event.target.value.trim();
      loadNodes();
    }, 250));
    await loadTypes();
    await loadNodes();
    const initial = new URLSearchParams(location.search).get('node');
    if (initial) { select(initial); }
  });
})();
