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

    onTypeSelect(typeId, label) {
        console.debug('Typ gewählt:', typeId, label);  // Task 5 ersetzt dies
    }
}

document.addEventListener('DOMContentLoaded', () => { new WissensnetzApp(); });
