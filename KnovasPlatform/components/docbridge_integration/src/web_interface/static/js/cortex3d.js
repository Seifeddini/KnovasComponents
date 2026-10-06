// Knovas Cortex — guided 3D graph and directory mind maps.
'use strict';

(function () {
    function supportsWebGL() {
        try {
            const canvas = document.createElement('canvas');
            return Boolean(
                window.WebGLRenderingContext &&
                (canvas.getContext('webgl') || canvas.getContext('experimental-webgl'))
            );
        } catch (_) {
            return false;
        }
    }

    if (typeof window.ForceGraph3D !== 'function' || !supportsWebGL()) return;

    const CONTEXTS = new Set(['fields', 'network', 'documents', 'history']);
    const esc = (value) => String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');

    function token(name, fallback) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
    }

    function apiError(payload, status) {
        return new Error(payload && payload.error ? payload.error : `HTTP ${status}`);
    }

    class Cortex3DApp {
        constructor() {
            this.graph = null;
            this.summary = null;
            this.views = [];
            this.view = 'all';
            this.context = null;
            this.selectedNode = null;
            this.selectedDetail = null;
            this.baseScene = { nodes: [], links: [] };
            this.request = null;
            this.contextGeneration = 0;
            this.labelNodes = new Map();
            this.reducedMotion = window.matchMedia &&
                window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            this.init();
        }

        async init() {
            try {
                const summaryRequest = this.fetchJson('/api/ontology/summary');
                const viewsRequest = this.fetchJson('/api/graph/views').catch(() => ({
                    views: [],
                    node_types: [],
                }));
                [this.summary, this.viewPayload] = await Promise.all([
                    summaryRequest,
                    viewsRequest,
                ]);
                this.views = this.viewPayload.views || [];
                this.buildRenderer();
                this.bindControls();
                this.renderViewButtons();
                const params = new URLSearchParams(location.search);
                const requestedView = params.get('view') || 'all';
                const requestedNode = params.get('node');
                const requestedContext = params.get('context');
                document.getElementById('cortexDepth').value =
                    ['1', '2', '3'].includes(params.get('depth')) ? params.get('depth') : '1';
                await this.setView(
                    requestedView === 'all' || this.views.some((view) => view.slug === requestedView)
                        ? requestedView : 'all',
                    { updateUrl: false },
                );
                if (requestedNode) {
                    await this.selectEntity(requestedNode, {
                        context: CONTEXTS.has(requestedContext) ? requestedContext : 'fields',
                        updateUrl: false,
                    });
                }
            } catch (error) {
                console.error('Cortex 3D konnte nicht geladen werden', error);
                this.showError('Cortex konnte nicht geladen werden. Seite neu laden.');
            }
        }

        async fetchJson(url, options = {}) {
            const response = await fetch(url, {
                credentials: 'same-origin',
                ...options,
                headers: {
                    Accept: 'application/json',
                    ...(options.body ? {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': this.csrfToken(),
                    } : {}),
                    ...(options.headers || {}),
                },
            });
            const payload = await response.json().catch(() => ({}));
            if (response.status === 401) {
                window.location.assign('/login');
                throw apiError(payload, response.status);
            }
            if (!response.ok) throw apiError(payload, response.status);
            return payload;
        }

        csrfToken() {
            const element = document.querySelector('meta[name="csrf-token"]');
            return element ? element.content : '';
        }

        buildRenderer() {
            const container = document.getElementById('graphContainer');
            this.labelLayer = document.createElement('div');
            this.labelLayer.className = 'cortex-label-layer';
            container.insertAdjacentElement('afterend', this.labelLayer);
            const primary = token('--primary-color', '#1A45C7');
            const accent = token('--accent', '#3B79F2');
            const secondary = token('--text-secondary', '#5A6B80');
            this.graph = ForceGraph3D()(container)
                .backgroundColor('rgba(0,0,0,0)')
                .showNavInfo(false)
                .numDimensions(3)
                .nodeId('id')
                .nodeLabel((node) => this.nodeTooltip(node))
                .nodeVal((node) => node.size || (node.kind === 'entity' ? 5 : 7))
                .nodeColor((node) => {
                    if (node.id === this.selectedNode) return accent;
                    if (node.kind === 'context') return secondary;
                    if (node.kind === 'document') return token('--callout', accent);
                    if (node.kind === 'history') return token('--text-muted', secondary);
                    if (node.kind === 'field' && node.missing) {
                        return token('--error-color', secondary);
                    }
                    return primary;
                })
                .nodeOpacity(0.92)
                .linkSource('source')
                .linkTarget('target')
                .linkLabel((link) => link.label || '')
                .linkColor(() => token('--border-color', '#D1D6DF'))
                .linkOpacity(0.58)
                .linkWidth((link) => link.width || 1)
                .linkDirectionalArrowLength((link) => link.directed === false ? 0 : 3)
                .linkDirectionalArrowRelPos(0.86)
                .onNodeClick((node) => this.onNodeClick(node))
                .onBackgroundClick(() => this.closeDrawer());

            const resize = () => {
                const width = Math.max(1, container.clientWidth);
                const height = Math.max(1, container.clientHeight);
                if (this.graph.width() !== width) this.graph.width(width);
                if (this.graph.height() !== height) this.graph.height(height);
            };
            resize();
            if (window.ResizeObserver) {
                this.resizeObserver = new ResizeObserver(resize);
                this.resizeObserver.observe(container);
            } else {
                window.addEventListener('resize', resize);
            }

            const charge = this.graph.d3Force('charge');
            if (charge && charge.strength) charge.strength(-150);
            this.graph.d3VelocityDecay(0.28);
            this.graph.cooldownTicks(this.reducedMotion ? 45 : 100);
            const controls = this.graph.controls();
            if (controls) {
                controls.enableDamping = true;
                controls.dampingFactor = 0.12;
                controls.autoRotate = false;
                controls.minDistance = 90;
                controls.maxDistance = 2200;
            }
            this.syncLabels();
        }

        bindControls() {
            document.getElementById('zoomIn').addEventListener('click', () => this.zoomBy(0.72));
            document.getElementById('zoomOut').addEventListener('click', () => this.zoomBy(1.35));
            document.getElementById('zoomFit').addEventListener('click', () => {
                this.graph.zoomToFit(this.reducedMotion ? 0 : 700, 72);
            });
            document.getElementById('entityClose').addEventListener('click', () => this.closeDrawer());
            document.getElementById('docClose').addEventListener('click', () => this.closeDocDrawer());
            document.getElementById('typeCreate').hidden = true;
            document.getElementById('cortexDepth').addEventListener('change', () => {
                if (this.context === 'network') this.setContext('network');
            });
            document.getElementById('cortexContextSwitch').addEventListener('click', (event) => {
                const button = event.target.closest('[data-context]');
                if (button && !button.disabled) this.setContext(button.dataset.context);
            });
            window.addEventListener('popstate', () => this.restoreUrl());
        }

        renderViewButtons() {
            const host = document.getElementById('cortexViewSwitch');
            host.querySelectorAll('[data-view]:not([data-view="all"])').forEach((item) => item.remove());
            this.views.forEach((view) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.dataset.view = view.slug;
                button.textContent = view.title;
                button.setAttribute('aria-pressed', 'false');
                host.appendChild(button);
            });
            host.addEventListener('click', (event) => {
                const button = event.target.closest('[data-view]');
                if (button) this.setView(button.dataset.view);
            });
        }

        generalScene() {
            const types = this.summary.types || [];
            const max = Math.max(...types.map((type) => Number(type.count) || 0), 1);
            return {
                nodes: types.map((type, index) => {
                    const angle = (index / Math.max(types.length, 1)) * Math.PI * 2;
                    const elevation = ((index % 3) - 1) * 75;
                    return {
                        id: type.id,
                        label: type.label,
                        count: type.count,
                        kind: 'type',
                        size: 180 + (Number(type.count) || 0) / max * 220,
                        x: Math.cos(angle) * 250,
                        y: Math.sin(angle) * 250,
                        z: elevation,
                    };
                }),
                links: (this.summary.relations || []).map((relation, index) => ({
                    id: `type-edge-${index}`,
                    source: relation.src,
                    target: relation.dst,
                    label: relation.predicate,
                    width: relation.count ? Math.min(4, 1 + relation.count / 10) : 1,
                })),
            };
        }

        async setView(slug, { updateUrl = true } = {}) {
            this.abortRequest();
            this.view = slug;
            this.selectedNode = null;
            this.selectedDetail = null;
            this.context = null;
            this.closeDrawer();
            this.setContextControls(false);
            this.updatePressedStates();
            this.setStatus(slug === 'all' ? 'Cortex allgemein' : 'Verzeichnis wird geladen');
            if (slug === 'all') {
                this.baseScene = this.generalScene();
            } else {
                const data = await this.fetchJson(
                    `/api/graph/views/${encodeURIComponent(slug)}/nodes?limit=250`,
                );
                const rootId = `view:${slug}`;
                this.baseScene = {
                    nodes: [{
                        id: rootId,
                        label: data.view.title,
                        count: data.total,
                        kind: 'view',
                        size: 300,
                        fx: 0, fy: 0, fz: 0,
                    }].concat((data.nodes || []).map((node, index) => {
                        const point = this.fibonacciPoint(index, data.nodes.length, 250);
                        return {
                            ...node,
                            label: node.name,
                            kind: 'entity',
                            x: point.x,
                            y: point.y,
                            z: point.z,
                            size: 90,
                        };
                    })),
                    links: (data.nodes || []).map((node, index) => ({
                        id: `view-link-${index}`,
                        source: rootId,
                        target: node.id,
                        directed: false,
                    })),
                };
                this.setStatus(`${data.view.title}: ${data.total} Einträge`);
            }
            this.setScene(this.baseScene);
            if (updateUrl) this.updateUrl();
        }

        async onNodeClick(node) {
            if (node.kind === 'type') {
                await this.expandType(node);
                return;
            }
            if (node.kind === 'document') {
                this.openDocument(node.document);
                return;
            }
            if (node.kind === 'field') {
                this.openDetailDrawer(node.attributeId);
                return;
            }
            if (node.kind === 'history') {
                this.openDetailDrawer();
                return;
            }
            if (node.kind === 'entity' || node.kind === 'centre') {
                await this.selectEntity(node.entityId || node.id);
            }
        }

        async expandType(typeNode) {
            const data = await this.fetchJson(
                `/api/ontology/entities?type=${encodeURIComponent(typeNode.id)}`,
            );
            const entities = data.entities || [];
            const centre = {
                ...typeNode,
                fx: 0, fy: 0, fz: 0,
            };
            this.baseScene = {
                nodes: [centre].concat(entities.map((entity, index) => {
                    const point = this.fibonacciPoint(index, entities.length, 230);
                    return {
                        ...entity,
                        id: entity.id,
                        entityId: entity.id,
                        label: entity.label,
                        kind: 'entity',
                        x: point.x,
                        y: point.y,
                        z: point.z,
                    };
                })),
                links: entities.map((entity, index) => ({
                    id: `type-instance-${index}`,
                    source: typeNode.id,
                    target: entity.id,
                    directed: false,
                })),
            };
            this.setScene(this.baseScene, { fit: false });
            this.focusNode(centre, 430);
            this.setStatus(`${typeNode.label}: ${entities.length} Einträge`);
        }

        async selectEntity(nodeId, { context = 'fields', updateUrl = true } = {}) {
            this.abortRequest();
            const controller = new AbortController();
            this.request = controller;
            const detail = await this.fetchJson(
                `/api/graph/nodes/${encodeURIComponent(nodeId)}`,
                { signal: controller.signal },
            );
            if (controller.signal.aborted) return;
            this.selectedNode = nodeId;
            this.selectedDetail = detail;
            this.setContextControls(true);
            await this.setContext(context, { updateUrl });
        }

        async setContext(context, { updateUrl = true } = {}) {
            if (!this.selectedDetail || !CONTEXTS.has(context)) return;
            const generation = ++this.contextGeneration;
            this.context = context;
            this.updatePressedStates();
            const node = this.selectedDetail.node;
            let scene;
            if (context === 'fields') {
                scene = this.fieldsScene(node, this.selectedDetail.fields || []);
            } else if (context === 'documents') {
                scene = this.documentsScene(node, this.selectedDetail.documents || []);
            } else if (context === 'network') {
                const depth = document.getElementById('cortexDepth').value;
                const data = await this.fetchJson(
                    `/api/graph/nodes/${encodeURIComponent(node.id)}/network?depth=${depth}`,
                );
                if (generation !== this.contextGeneration) return;
                scene = {
                    nodes: (data.nodes || []).map((item) => ({
                        ...item,
                        label: item.name,
                        kind: item.id === node.id ? 'centre' : 'entity',
                        entityId: item.id,
                        size: item.id === node.id ? 250 : Math.max(55, 100 - (item.hop || 1) * 18),
                        ...(item.id === node.id ? { fx: 0, fy: 0, fz: 0 } : {}),
                    })),
                    links: (data.edges || []).map((edge) => ({
                        ...edge,
                        directed: true,
                    })),
                };
            } else {
                const data = await this.fetchJson(
                    `/api/graph/nodes/${encodeURIComponent(node.id)}/history`,
                );
                if (generation !== this.contextGeneration) return;
                scene = this.historyScene(node, data.events || []);
            }
            this.setScene(scene, { fit: false });
            const centre = scene.nodes.find((item) => item.id === node.id);
            if (centre) this.focusNode(centre, context === 'network' ? 500 : 390);
            this.setStatus(`${node.name}: ${this.contextLabel(context)}`);
            if (updateUrl) this.updateUrl();
        }

        centreNode(node) {
            return {
                ...node,
                id: node.id,
                entityId: node.id,
                label: node.name,
                kind: 'centre',
                size: 250,
                fx: 0, fy: 0, fz: 0,
            };
        }

        fieldsScene(node, fields) {
            const centre = this.centreNode(node);
            const nodes = [centre];
            const links = [];
            fields.forEach((field, index) => {
                const point = this.ringPoint(index, fields.length, 190, index % 2 ? 55 : -55);
                const id = `field:${field.attribute.id}`;
                nodes.push({
                    id,
                    label: `${field.attribute.name}\n${field.display || 'Nicht ausgefüllt'}`,
                    kind: 'field',
                    attributeId: field.attribute.id,
                    missing: field.missing,
                    size: field.missing ? 48 : 72,
                    x: point.x, y: point.y, z: point.z,
                    fx: point.x, fy: point.y, fz: point.z,
                });
                links.push({ id: `field-link:${index}`, source: node.id, target: id, directed: false });
            });
            return { nodes, links };
        }

        documentsScene(node, documents) {
            const centre = this.centreNode(node);
            const nodes = [centre];
            const links = [];
            documents.forEach((document, index) => {
                const point = this.ringPoint(index, documents.length, 205, (index % 3 - 1) * 45);
                const id = `document:${index}`;
                nodes.push({
                    id,
                    label: document.title,
                    kind: 'document',
                    document,
                    size: 82,
                    x: point.x, y: point.y, z: point.z,
                    fx: point.x, fy: point.y, fz: point.z,
                });
                links.push({ id: `document-link:${index}`, source: node.id, target: id, directed: false });
            });
            return { nodes, links };
        }

        historyScene(node, events) {
            const centre = this.centreNode(node);
            const nodes = [centre];
            const links = [];
            events.slice(0, 40).forEach((event, index) => {
                const angle = index * 0.72;
                const id = `history:${event.id}`;
                const previous = index === 0 ? node.id : `history:${events[index - 1].id}`;
                nodes.push({
                    id,
                    label: `${event.field}\n${event.timestamp || 'Zeitpunkt unbekannt'}`,
                    kind: 'history',
                    event,
                    size: 58,
                    x: Math.cos(angle) * 145,
                    y: 55 + index * 28,
                    z: Math.sin(angle) * 145,
                });
                links.push({ id: `history-link:${index}`, source: previous, target: id, directed: false });
            });
            return { nodes, links };
        }

        setScene(scene, { fit = true } = {}) {
            const data = {
                nodes: (scene.nodes || []).map((node) => ({ ...node })),
                links: (scene.links || []).map((link) => ({
                    ...link,
                    source: typeof link.source === 'object' ? link.source.id : link.source,
                    target: typeof link.target === 'object' ? link.target.id : link.target,
                })),
            };
            this.graph.graphData(data);
            this.renderLabels(data.nodes);
            if (fit) {
                window.setTimeout(() => {
                    if (this.graph) this.graph.zoomToFit(this.reducedMotion ? 0 : 650, 72);
                }, this.reducedMotion ? 0 : 180);
            }
        }

        renderLabels(nodes) {
            this.labelLayer.innerHTML = '';
            this.labelNodes.clear();
            nodes.forEach((node) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = `cortex-node-label cortex-node-label-${node.kind || 'node'}`;
                button.textContent = node.label || node.name || '';
                button.setAttribute('aria-label', String(node.label || node.name || '').replace(/\n/g, ': '));
                button.addEventListener('click', () => this.onNodeClick(node));
                this.labelLayer.appendChild(button);
                this.labelNodes.set(node.id, { node, button });
            });
        }

        syncLabels() {
            if (this.graph) {
                this.labelNodes.forEach(({ node, button }) => {
                    if (![node.x, node.y, node.z].every(Number.isFinite)) {
                        button.hidden = true;
                        return;
                    }
                    const point = this.graph.graph2ScreenCoords(node.x, node.y, node.z);
                    button.hidden = false;
                    button.style.transform =
                        `translate(-50%, -50%) translate(${point.x}px, ${point.y}px)`;
                });
            }
            window.requestAnimationFrame(() => this.syncLabels());
        }

        focusNode(node, distance) {
            const x = Number(node.x) || 0;
            const y = Number(node.y) || 0;
            const z = Number(node.z) || 0;
            const length = Math.hypot(x, y, z) || 1;
            const ratio = 1 + distance / length;
            this.graph.cameraPosition(
                { x: x * ratio + 40, y: y * ratio + 25, z: z * ratio + distance },
                { x, y, z },
                this.reducedMotion ? 0 : 900,
            );
        }

        zoomBy(factor) {
            const camera = this.graph.cameraPosition();
            this.graph.cameraPosition({
                x: camera.x * factor,
                y: camera.y * factor,
                z: camera.z * factor,
            }, undefined, this.reducedMotion ? 0 : 300);
        }

        ringPoint(index, count, radius, z) {
            const angle = (index / Math.max(count, 1)) * Math.PI * 2 - Math.PI / 2;
            return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, z };
        }

        fibonacciPoint(index, count, radius) {
            if (count <= 1) return { x: 0, y: 0, z: 0 };
            const y = 1 - (index / (count - 1)) * 2;
            const ring = Math.sqrt(Math.max(0, 1 - y * y));
            const theta = Math.PI * (3 - Math.sqrt(5)) * index;
            return {
                x: Math.cos(theta) * ring * radius,
                y: y * radius,
                z: Math.sin(theta) * ring * radius,
            };
        }

        nodeTooltip(node) {
            const count = node.count == null ? '' : `<small>${esc(node.count)} Einträge</small>`;
            return `<div class="cortex-tooltip"><strong>${esc(node.label || node.name || '')}</strong>${count}</div>`;
        }

        setContextControls(enabled) {
            document.querySelectorAll('#cortexContextSwitch [data-context]').forEach((button) => {
                button.disabled = !enabled;
            });
        }

        updatePressedStates() {
            document.querySelectorAll('#cortexViewSwitch [data-view]').forEach((button) => {
                const active = button.dataset.view === this.view;
                button.classList.toggle('active', active);
                button.setAttribute('aria-pressed', String(active));
            });
            document.querySelectorAll('#cortexContextSwitch [data-context]').forEach((button) => {
                const active = button.dataset.context === this.context;
                button.classList.toggle('active', active);
                button.setAttribute('aria-pressed', String(active));
            });
            document.getElementById('cortexDepthWrap').hidden = this.context !== 'network';
        }

        updateUrl() {
            const params = new URLSearchParams();
            if (this.view !== 'all') params.set('view', this.view);
            if (this.selectedNode) params.set('node', this.selectedNode);
            if (this.context) params.set('context', this.context);
            if (this.context === 'network') {
                params.set('depth', document.getElementById('cortexDepth').value);
            }
            const query = params.toString();
            history.pushState(null, '', `${location.pathname}${query ? `?${query}` : ''}`);
        }

        async restoreUrl() {
            const params = new URLSearchParams(location.search);
            document.getElementById('cortexDepth').value = params.get('depth') || '1';
            await this.setView(params.get('view') || 'all', { updateUrl: false });
            if (params.get('node')) {
                await this.selectEntity(params.get('node'), {
                    context: params.get('context') || 'fields',
                    updateUrl: false,
                });
            }
        }

        openDetailDrawer(focusAttribute = null) {
            const detail = this.selectedDetail;
            if (!detail) return;
            const pane = document.getElementById('entityPane');
            document.getElementById('entityPaneTitle').textContent = detail.node.name;
            const body = document.getElementById('entityPaneBody');
            const fields = (detail.fields || []).map((field) => `
                <div class="cortex-field${field.missing ? ' missing' : ''}"
                     data-attribute="${esc(field.attribute.id)}">
                    <span class="cortex-field-name">${esc(field.attribute.name)}</span>
                    <span class="cortex-field-value">${esc(field.display || 'Nicht ausgefüllt')}</span>
                    ${detail.may_write ? '<button type="button" class="btn-text cortex-field-edit">Bearbeiten</button>' : ''}
                </div>`).join('');
            body.innerHTML = `<div class="entity-detail">
                <p class="entity-hint">${esc(detail.view ? detail.view.title : 'Cortex')}</p>
                <div class="cortex-field-list">${fields || '<p>Keine Felder definiert.</p>'}</div>
            </div>`;
            body.querySelectorAll('.cortex-field-edit').forEach((button) => {
                button.addEventListener('click', () => this.editField(button.closest('.cortex-field')));
            });
            pane.classList.add('open');
            if (focusAttribute) {
                const target = body.querySelector(`[data-attribute="${CSS.escape(focusAttribute)}"]`);
                if (target) target.scrollIntoView({ block: 'center' });
            }
        }

        editField(row) {
            const id = row.dataset.attribute;
            const field = this.selectedDetail.fields.find((item) => item.attribute.id === id);
            if (!field) return;
            const attribute = field.attribute;
            let control;
            if (attribute.datatype === 'enum') {
                control = `<select class="cortex-field-input">
                    <option value="">Nicht ausgefüllt</option>
                    ${(attribute.enum_values || []).map((value) =>
                        `<option value="${esc(value)}"${field.value === value ? ' selected' : ''}>${esc(value)}</option>`
                    ).join('')}
                </select>`;
            } else if (attribute.datatype === 'date') {
                control = `<input class="cortex-field-input" type="date"
                    value="${esc(field.value && field.value.value || '')}">`;
            } else if (attribute.datatype === 'money') {
                control = `<div class="cortex-money-input">
                    <input class="cortex-field-input" inputmode="decimal"
                        value="${esc(field.value && field.value.amount || '')}" aria-label="Betrag">
                    <input class="cortex-currency-input" maxlength="3"
                        value="${esc(field.value && field.value.currency || 'CHF')}" aria-label="Währung">
                </div>`;
            } else if (attribute.datatype === 'entity_ref') {
                control = `<input class="cortex-field-input" value="${esc(field.value && field.value.node_id || '')}"
                    placeholder="Knoten-ID">`;
            } else {
                control = `<textarea class="cortex-field-input" rows="3">${esc(field.value || '')}</textarea>`;
            }
            row.innerHTML = `<label class="cortex-field-name">${esc(attribute.name)}</label>
                ${control}<div class="cortex-field-actions">
                <button type="button" class="btn btn-primary cortex-field-save">Speichern</button>
                <button type="button" class="btn-text cortex-field-cancel">Abbrechen</button></div>`;
            row.querySelector('.cortex-field-cancel').addEventListener('click', () => this.openDetailDrawer(id));
            row.querySelector('.cortex-field-save').addEventListener('click', () => this.saveField(row, field));
            row.querySelector('.cortex-field-input').focus();
        }

        async saveField(row, field) {
            const input = row.querySelector('.cortex-field-input');
            let value = input.value;
            if (field.attribute.datatype === 'date' && value) {
                value = { value, precision: 'day' };
            } else if (field.attribute.datatype === 'money' && value) {
                value = {
                    amount: value,
                    currency: row.querySelector('.cortex-currency-input').value,
                };
            } else if (field.attribute.datatype === 'entity_ref' && value) {
                value = { node_id: value };
            }
            try {
                await this.fetchJson(
                    `/api/graph/nodes/${encodeURIComponent(this.selectedNode)}/fields/${encodeURIComponent(field.attribute.id)}`,
                    { method: 'PUT', body: JSON.stringify({ value }) },
                );
                await this.selectEntity(this.selectedNode, { context: this.context });
            } catch (error) {
                row.insertAdjacentHTML('beforeend', `<p class="form-error">${esc(error.message)}</p>`);
            }
        }

        openDocument(document) {
            const pane = document.getElementById('docPane');
            const body = document.getElementById('docPaneBody');
            document.getElementById('docPaneTitle').textContent = document.title || 'Dokument';
            body.innerHTML = '';
            const frame = document.createElement('iframe');
            frame.className = 'doc-frame';
            frame.title = `Vorschau: ${document.title || 'Dokument'}`;
            frame.src = `/api/document/${encodeURIComponent(document.title || document.pointer)}/preview` +
                `?path=${encodeURIComponent(document.pointer)}` +
                (document.page ? `#page=${Number(document.page)}` : '');
            body.appendChild(frame);
            pane.classList.add('open');
        }

        closeDrawer() {
            document.getElementById('entityPane').classList.remove('open');
        }

        closeDocDrawer() {
            const pane = document.getElementById('docPane');
            pane.classList.remove('open');
            document.getElementById('docPaneBody').innerHTML = '';
        }

        abortRequest() {
            if (this.request) this.request.abort();
            this.request = null;
            this.contextGeneration += 1;
        }

        setStatus(text) {
            document.getElementById('cortexStatus').textContent = text;
        }

        showError(text) {
            const empty = document.getElementById('graphEmpty');
            empty.textContent = text;
            empty.hidden = false;
        }

        contextLabel(context) {
            return {
                fields: 'Felder',
                network: 'Wissensnetz',
                documents: 'Dokumente',
                history: 'Verlauf',
            }[context] || context;
        }
    }

    window.KnovasCortex3D = Cortex3DApp;
    document.addEventListener('DOMContentLoaded', () => {
        window.cortexApp = new Cortex3DApp();
    });
}());
