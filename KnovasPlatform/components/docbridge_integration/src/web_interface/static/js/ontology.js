// Knovas Wissensnetz — Ontologie-Explorer (Vertrag: /api/ontology/*)
'use strict';

/** Schweizer Tausendertrennung: 1847 -> "1'847" (ASCII-Apostroph). */
function formatCount(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, "'");
}

/** CI-Farben zur Laufzeit aus den Design-Tokens lesen (keine Hexwerte im JS). */
function cssToken(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

class WissensnetzApp {
    constructor() {
        this.cy = null;
        this.selectedType = null;
        this.selectedEntity = null;
        this.entityAbort = null;
        this.init();
    }

    async init() {
        try {
            const resp = await fetch('/api/ontology/summary');
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            if (!data.types.length) {
                document.getElementById('graphContainer').hidden = true;
                document.getElementById('graphEmpty').hidden = false;
                return;
            }
            this.renderGraph(data);
            this.bindZoomControls();
        } catch (err) {
            console.error('Wissensnetz: Summary nicht ladbar', err);
            const empty = document.getElementById('graphEmpty');
            empty.textContent = 'Ontologie konnte nicht geladen werden. Seite neu laden.';
            empty.hidden = false;
        }
    }

    renderGraph(data) {
        const maxCount = Math.max(...data.types.map((t) => t.count), 1);
        const nodes = data.types.map((t) => ({
            data: { id: t.id, label: t.label, count: t.count,
                    size: 32 + Math.round(40 * (t.count / maxCount)) },
        }));
        const maxRel = Math.max(...data.relations.map((r) => r.count), 1);
        const edges = data.relations.map((r, i) => ({
            data: { id: `r-${i}`, source: r.src, target: r.dst,
                    label: `${r.predicate} (${formatCount(r.count)})`,
                    width: 1.5 + 3 * (r.count / maxRel) },
        }));

        this.cy = cytoscape({
            container: document.getElementById('graphContainer'),
            elements: { nodes, edges },
            minZoom: 0.2,
            maxZoom: 4,
            wheelSensitivity: 0.2,
            style: [
                { selector: 'node', style: {
                    'background-color': cssToken('--surface-sunken'),
                    'border-width': 2,
                    'border-color': cssToken('--accent'),
                    'width': 'data(size)',
                    'height': 'data(size)',
                    'label': 'data(label)',
                    'font-family': cssToken('--font-body') || 'IBM Plex Sans, sans-serif',
                    'font-size': 12,
                    'color': cssToken('--text-primary'),
                    'text-valign': 'bottom',
                    'text-margin-y': 6,
                } },
                { selector: 'node:selected', style: {
                    'background-color': cssToken('--highlight'),
                    'border-color': cssToken('--primary-color'),
                    'border-width': 3,
                } },
                { selector: 'edge', style: {
                    'line-color': cssToken('--border-color'),
                    'target-arrow-shape': 'triangle',
                    'target-arrow-color': cssToken('--border-color'),
                    'curve-style': 'bezier',
                    'width': 'data(width)',
                    'label': 'data(label)',
                    'font-size': 10,
                    'color': cssToken('--text-secondary'),
                    'text-rotation': 'autorotate',
                    'text-background-color': cssToken('--bg-color'),
                    'text-background-opacity': 0.85,
                    'text-background-padding': 2,
                } },
                { selector: 'edge:selected', style: {
                    'line-color': cssToken('--accent'),
                    'target-arrow-color': cssToken('--accent'),
                } },
            ],
            // Deterministisch (Spec Regel 4): concentric sortiert stabil nach count.
            layout: { name: 'concentric', fit: true, padding: 40, minNodeSpacing: 60,
                      concentric: (n) => n.data('count'), levelWidth: () => 1 },
        });

        this.cy.on('tap', 'node', (evt) => {
            this.onTypeSelect(evt.target.id(), evt.target.data('label'));
        });
        this.cy.on('dbltap', (evt) => {
            if (evt.target === this.cy) this.cy.fit(undefined, 40);
        });
    }

    bindZoomControls() {
        const zoomBy = (factor) => {
            this.cy.zoom({
                level: this.cy.zoom() * factor,
                renderedPosition: { x: this.cy.width() / 2, y: this.cy.height() / 2 },
            });
        };
        document.getElementById('zoomIn').addEventListener('click', () => zoomBy(1.25));
        document.getElementById('zoomOut').addEventListener('click', () => zoomBy(0.8));
        document.getElementById('zoomFit').addEventListener('click', () => this.cy.fit(undefined, 40));
    }

    /** Escaping vor jeder Interpolation — Fixture-/Backend-Text ist Fremdtext. */
    static esc(s) {
        const d = document.createElement('span');
        d.textContent = String(s ?? '');
        return d.innerHTML;
    }

    async fetchJson(url) {
        if (this.entityAbort) this.entityAbort.abort();     // Spec Regel 5
        this.entityAbort = new AbortController();
        const resp = await fetch(url, { signal: this.entityAbort.signal });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json();
    }

    async onTypeSelect(typeId, label) {
        this.selectedType = typeId;
        const body = document.getElementById('entityPaneBody');
        document.getElementById('entityPaneTitle').textContent = label;
        body.innerHTML = '<p class="ontology-empty">Lädt…</p>';
        try {
            const data = await this.fetchJson(
                `/api/ontology/entities?type=${encodeURIComponent(typeId)}`);
            if (!data.entities.length) {
                body.innerHTML = '<p class="ontology-empty">Keine Entitäten dieses Typs im Korpus.</p>';
                return;
            }
            const esc = WissensnetzApp.esc;
            body.innerHTML = `
                <table class="entity-table">
                  <thead><tr><th>Entität</th><th class="num">Dokumente</th></tr></thead>
                  <tbody>${data.entities.map((e) => `
                    <tr><td><button type="button" class="btn-text entity-link"
                                data-id="${esc(e.id)}">${esc(e.label)}</button></td>
                        <td class="num">${formatCount(e.doc_count)}</td></tr>`).join('')}
                  </tbody>
                </table>`;
            body.querySelectorAll('.entity-link').forEach((btn) =>
                btn.addEventListener('click', () => this.onEntitySelect(btn.dataset.id)));
        } catch (err) {
            if (err.name === 'AbortError') return;
            body.innerHTML = '<p class="ontology-empty">Entitäten konnten nicht geladen werden.</p>';
        }
    }

    async onEntitySelect(entityId) {
        this.selectedEntity = entityId;
        const body = document.getElementById('entityPaneBody');
        try {
            const data = await this.fetchJson(
                `/api/ontology/entities/${encodeURIComponent(entityId)}`);
            const esc = WissensnetzApp.esc;
            const relations = data.relations.length
                ? `<ul class="entity-relations">${data.relations.map((r) => `
                     <li><span class="predicate">${esc(r.predicate)}</span>
                         <button type="button" class="btn-text entity-link"
                                 data-id="${esc(r.target.id)}">${esc(r.target.label)}</button></li>`).join('')}
                   </ul>`
                : '<p class="ontology-empty">Keine Verbindungen erfasst.</p>';
            const evidence = data.evidence.length
                ? `<ol class="evidence-list">${data.evidence.map((ev, i) => `
                     <li><button type="button" class="evidence-item" data-index="${i}"
                                 data-path="${esc(ev.document.path)}" data-page="${ev.page}"
                                 data-title="${esc(ev.document.title)}">
                         <span class="evidence-quote">«${esc(ev.quote)}»</span>
                         <span class="evidence-source">${esc(ev.document.title)}, Seite ${formatCount(ev.page)}</span>
                     </button></li>`).join('')}
                   </ol>`
                : '<p class="ontology-empty">Keine Belege über dem Schwellenwert.</p>';
            body.innerHTML = `
                <div class="entity-detail">
                    <button type="button" class="btn-text" id="entityBack">← Zurück zur Liste</button>
                    <h3>${esc(data.entity.label)}</h3>
                    <h4>Verbindungen</h4>${relations}
                    <h4>Belege</h4>${evidence}
                </div>`;
            document.getElementById('entityBack').addEventListener('click', () => {
                const node = this.cy.getElementById(this.selectedType);
                this.onTypeSelect(this.selectedType, node.data('label'));
            });
            body.querySelectorAll('.entity-link').forEach((btn) =>
                btn.addEventListener('click', () => this.onEntitySelect(btn.dataset.id)));
            body.querySelectorAll('.evidence-item').forEach((btn) =>
                btn.addEventListener('click', () => {
                    body.querySelectorAll('.evidence-item.selected')
                        .forEach((b) => b.classList.remove('selected'));
                    btn.classList.add('selected');
                    this.onEvidenceSelect({ path: btn.dataset.path,
                                            page: Number(btn.dataset.page),
                                            title: btn.dataset.title });
                }));
        } catch (err) {
            if (err.name === 'AbortError') return;
            body.innerHTML = '<p class="ontology-empty">Entität konnte nicht geladen werden.</p>';
        }
    }

    onEvidenceSelect(evidence) {
        const body = document.getElementById('docPaneBody');
        document.getElementById('docPaneTitle').textContent =
            `${evidence.title} – Seite ${formatCount(evidence.page)}`;
        // Query VOR dem Fragment: der browsernative PDF-Viewer liest #page=N.
        const url = `/api/document/${encodeURIComponent(evidence.title)}/preview` +
                    `?path=${encodeURIComponent(evidence.path)}#page=${evidence.page}`;
        body.innerHTML = '';
        const frame = document.createElement('iframe');
        frame.className = 'doc-frame';
        frame.title = `Vorschau: ${evidence.title}`;
        frame.src = url;
        frame.addEventListener('error', () => {
            body.innerHTML =
                '<p class="ontology-empty">Dokument konnte nicht geladen werden.</p>';
        });
        body.appendChild(frame);
    }
}

document.addEventListener('DOMContentLoaded', () => { new WissensnetzApp(); });
