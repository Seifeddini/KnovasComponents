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

/** Font-Token für Cytoscape: dessen Validierung lehnt Anführungszeichen im
    Font-Stack ab ("invalid" → Fallback-Schrift), Canvas braucht sie nicht. */
function cssFontToken(name) {
    return cssToken(name).replace(/['"]/g, '');
}

/** Lade-Skeleton: schimmernde Platzhalterzeilen statt Text-Blitzer. */
function skeleton(rows = 3) {
    return Array.from({ length: rows }, () => '<div class="skeleton-row"></div>').join('');
}

/** Cytoscape rendert ins Canvas und zeichnet nicht neu, wenn Webfonts erst
    nach dem ersten Frame eintreffen (font-display: swap) — deshalb die
    CI-Schriften explizit laden, bevor der Graph entsteht. */
async function loadBrandFonts() {
    if (!document.fonts || !document.fonts.load) return;
    const wanted = ["600 13px 'IBM Plex Mono'", "400 13px 'IBM Plex Sans'"];
    try {
        await Promise.race([
            Promise.all(wanted.map((f) => document.fonts.load(f))),
            new Promise((resolve) => setTimeout(resolve, 1500)),
        ]);
    } catch (_) { /* Fallback-Schrift ist besser als gar kein Graph. */ }
}

/* Typ-Symbole im Stil der bestehenden UI-Icons (Feather-Strichstärke 2).
   Farben kommen zur Laufzeit aus den Tokens, keine Hexwerte hier. */
const TYPE_ICONS = {
    mandant: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    gegenpartei: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    dossier: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    vertrag: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
    gericht: '<line x1="3" y1="22" x2="21" y2="22"/><line x1="6" y1="18" x2="6" y2="11"/><line x1="10" y1="18" x2="10" y2="11"/><line x1="14" y1="18" x2="14" y2="11"/><line x1="18" y1="18" x2="18" y2="11"/><polygon points="12 2 20 7 4 7"/>',
    frist: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    honorar: '<rect x="1" y="4" width="22" height="16" rx="2" ry="2"/><line x1="1" y1="10" x2="23" y2="10"/>',
    dokumenttyp: '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
};
const FALLBACK_ICON =
    '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.83z"/><line x1="7" y1="7" x2="7.01" y2="7"/>';

/** SVG-Icon als Data-URI für Cytoscape-Knoten, Strichfarbe aus dem Token.
    Rand fürs Icon über translate, NICHT über negativen ViewBox-Ursprung —
    Chrome rastert SVGs mit negativem Ursprung im Canvas falsch (verschoben,
    abgeschnitten). Explizite width/height für die intrinsische Grösse. */
function iconDataUri(typeId, color) {
    const inner = TYPE_ICONS[typeId] || FALLBACK_ICON;
    const svg =
        `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 28 28">` +
        `<g transform="translate(2 2)" fill="none" stroke="${color}" stroke-width="2" ` +
        `stroke-linecap="round" stroke-linejoin="round">` + inner + '</g></svg>';
    return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
}

class WissensnetzApp {
    constructor() {
        this.cy = null;
        this.selectedType = null;
        this.selectedEntity = null;
        this.entityAbort = null;
        this.expandedTypes = new Map();   // typeId -> Cytoscape-Collection der Satelliten
        this.init();
    }

    async init() {
        try {
            const [resp] = await Promise.all([fetch('/api/ontology/summary'), loadBrandFonts()]);
            if (resp.status === 401) { window.location.assign('/login'); return; }
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            if (!data.types.length) {
                document.getElementById('graphContainer').hidden = true;
                document.getElementById('graphEmpty').hidden = false;
                document.querySelector('.graph-toolbar').hidden = true;
                return;
            }
            this.renderGraph(data);
            this.bindZoomControls();
            this.bindDrawerControls();
        } catch (err) {
            console.error('Wissensnetz: Summary nicht ladbar', err);
            const empty = document.getElementById('graphEmpty');
            empty.textContent = 'Wissensnetz konnte nicht geladen werden. Seite neu laden.';
            empty.hidden = false;
            document.querySelector('.graph-toolbar').hidden = true;
        }
    }

    renderGraph(data) {
        const iconColor = cssToken('--primary-color');
        const maxCount = Math.max(...data.types.map((t) => t.count), 1);
        const n = data.types.length;
        // Deterministischer Kreis-Seed (Spec Regel 4): das anschliessende
        // cose-Layout mit randomize:false liefert damit stets dasselbe Bild.
        const seedRadius = 300;
        const nodes = data.types.map((t, i) => ({
            data: { id: t.id, label: t.label, count: t.count,
                    icon: iconDataUri(t.id, iconColor),
                    size: 54 + Math.round(40 * (t.count / maxCount)) },
            position: {
                x: seedRadius * Math.cos((2 * Math.PI * i) / n - Math.PI / 2),
                y: seedRadius * Math.sin((2 * Math.PI * i) / n - Math.PI / 2),
            },
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
                    'background-color': cssToken('--card-bg'),
                    'border-width': 2,
                    'border-color': cssToken('--accent'),
                    'width': 'data(size)',
                    'height': 'data(size)',
                    'label': 'data(label)',
                    'font-family': cssFontToken('--font-heading') || 'IBM Plex Mono, monospace',
                    'font-weight': 600,
                    'font-size': 13,
                    'color': cssToken('--text-primary'),
                    'text-valign': 'bottom',
                    'text-margin-y': 8,
                } },
                { selector: 'node[icon]', style: {
                    'background-image': 'data(icon)',
                    'background-width': '62%',
                    'background-height': '62%',
                } },
                { selector: 'node:selected', style: {
                    'background-color': cssToken('--highlight'),
                    'border-color': cssToken('--primary-color'),
                    'border-width': 3,
                } },
                { selector: 'node.hovered', style: {
                    'border-color': cssToken('--primary-color'),
                } },
                { selector: 'node.expanded', style: {
                    'border-color': cssToken('--primary-color'),
                } },
                // Entitäten-Satelliten: kleine Instanz-Knoten am Typ-Knoten.
                { selector: 'node.entity', style: {
                    'background-color': cssToken('--surface-sunken'),
                    'border-width': 1.5,
                    'border-color': cssToken('--callout'),
                    'font-family': cssFontToken('--font-body') || 'IBM Plex Sans, sans-serif',
                    'font-weight': 400,
                    'font-size': 11,
                    'text-margin-y': 5,
                    'text-wrap': 'wrap',
                    'text-max-width': 150,
                } },
                { selector: 'edge.entity-edge', style: {
                    'width': 1,
                    'line-style': 'dashed',
                    'target-arrow-shape': 'none',
                    'label': '',
                } },
                { selector: 'edge', style: {
                    'line-color': cssToken('--border-color'),
                    'target-arrow-shape': 'triangle',
                    'target-arrow-color': cssToken('--border-color'),
                    'curve-style': 'bezier',
                    'width': 'data(width)',
                    'label': 'data(label)',
                    'font-family': cssFontToken('--font-body') || 'IBM Plex Sans, sans-serif',
                    'font-size': 11,
                    'color': cssToken('--text-secondary'),
                    'text-rotation': 'autorotate',
                    'text-background-color': cssToken('--surface-sunken'),
                    'text-background-opacity': 0.9,
                    'text-background-padding': 2,
                } },
                { selector: 'edge:selected', style: {
                    'line-color': cssToken('--accent'),
                    'target-arrow-color': cssToken('--accent'),
                } },
            ],
            layout: { name: 'preset' },
        });

        // Kräftelayout ab dem Kreis-Seed: zieht verbundene Typen zusammen,
        // drückt unverbundene auseinander — ohne Zufall (randomize:false).
        this.cy.layout({
            name: 'cose',
            animate: false,
            randomize: false,
            fit: true,
            padding: 60,
            // Feste Layout-Fläche statt Container-Pixelmasse: sonst hängt das
            // Ergebnis von der Fenstergrösse beim Laden ab (Spec: deterministisch).
            boundingBox: { x1: 0, y1: 0, w: 1200, h: 800 },
            nodeDimensionsIncludeLabels: true,
            idealEdgeLength: () => 130,
            nodeRepulsion: () => 150000,
            edgeElasticity: () => 150,
            gravity: 2.2,
            numIter: 3000,
        }).run();

        this.cy.on('tap', 'node', (evt) => {
            const node = evt.target;
            if (node.hasClass('entity')) {
                this.openEntityDrawer();
                this.onEntitySelect(node.data('entityId'));
            } else {
                this.onTypeTap(node);
            }
        });
        this.cy.on('tap', (evt) => {
            if (evt.target === this.cy) this.closeDrawers();
        });
        this.cy.on('dbltap', (evt) => {
            if (evt.target === this.cy) this.cy.fit(undefined, 70);
        });
        this.cy.on('mouseover', 'node', (evt) => evt.target.addClass('hovered'));
        this.cy.on('mouseout', 'node', (evt) => evt.target.removeClass('hovered'));
        this.cy.on('mouseover', 'node', () => {
            document.getElementById('graphContainer').style.cursor = 'pointer';
        });
        this.cy.on('mouseout', 'node', () => {
            document.getElementById('graphContainer').style.cursor = '';
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
        document.getElementById('zoomFit').addEventListener('click', () => this.cy.fit(undefined, 70));
    }

    /** Typ-Knoten-Klick: aufklappen (Satelliten + Drawer) bzw. wieder einklappen. */
    onTypeTap(node) {
        const typeId = node.id();
        if (this.expandedTypes.has(typeId)) {
            this.collapseType(typeId);
            this.closeDrawers();
            // Der Tap selektiert den Knoten nativ erst nach diesem Handler —
            // beim Einklappen soll aber nichts ausgewählt zurückbleiben.
            setTimeout(() => node.unselect(), 0);
            return;
        }
        this.collapseAllTypes(typeId);   // nur ein Typ gleichzeitig aufgeklappt
        this.onTypeSelect(typeId, node.data('label'));
    }

    /** Entitäten eines Typs als Satelliten-Knoten am Typ-Knoten auffächern. */
    renderEntityNodes(typeId, entities) {
        if (this.expandedTypes.has(typeId) || !entities.length) return;
        const parent = this.cy.getElementById(typeId);
        if (parent.empty()) return;
        const p = parent.position();
        // Auffächern in Richtung "vom Netz weg", damit freie Fläche genutzt wird.
        const bb = this.cy.nodes().boundingBox();
        let base = Math.atan2(p.y - (bb.y1 + bb.y2) / 2, p.x - (bb.x1 + bb.x2) / 2);
        if (!Number.isFinite(base)) base = -Math.PI / 2;
        const k = entities.length;
        const spread = Math.min(Math.PI * 0.85, (Math.PI / 4) * k);
        const radius = parent.data('size') / 2 + 90;
        const eles = [];
        entities.forEach((e) => {
            // Reste einer noch laufenden Einfahr-Animation räumen (ID-Kollision).
            const stale = this.cy.getElementById(`ent:${e.id}`);
            if (stale.nonempty()) stale.remove();
            eles.push({ group: 'nodes', classes: 'entity',
                        data: { id: `ent:${e.id}`, entityId: e.id, label: e.label, size: 26 },
                        position: { x: p.x, y: p.y } });
            eles.push({ group: 'edges', classes: 'entity-edge',
                        data: { id: `ee:${typeId}:${e.id}`, source: typeId,
                                target: `ent:${e.id}`, label: '', width: 1 } });
        });
        const added = this.cy.add(eles);
        const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        added.nodes().forEach((satellite, i) => {
            const a = k === 1 ? base : base - spread / 2 + (spread * i) / (k - 1);
            const target = { x: p.x + radius * Math.cos(a), y: p.y + radius * Math.sin(a) };
            if (reduceMotion) satellite.position(target);
            else satellite.animate({ position: target }, { duration: 260, easing: 'ease-out' });
        });
        parent.addClass('expanded');
        this.expandedTypes.set(typeId, added);
    }

    collapseType(typeId) {
        const satellites = this.expandedTypes.get(typeId);
        this.expandedTypes.delete(typeId);
        const parent = this.cy.getElementById(typeId);
        parent.removeClass('expanded');
        if (!satellites) return;
        const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (reduceMotion || parent.empty()) { satellites.remove(); return; }
        // Zurück in den Typ-Knoten gleiten, dann entfernen.
        satellites.nodes().animate({ position: parent.position() },
                                   { duration: 200, easing: 'ease-in' });
        setTimeout(() => satellites.remove(), 210);
    }

    collapseAllTypes(exceptTypeId = null) {
        for (const typeId of [...this.expandedTypes.keys()]) {
            if (typeId !== exceptTypeId) this.collapseType(typeId);
        }
    }

    bindDrawerControls() {
        document.getElementById('entityClose').addEventListener('click', () => this.closeDrawers());
        document.getElementById('docClose').addEventListener('click', () => this.closeDocDrawer());
    }

    openEntityDrawer() {
        document.getElementById('entityPane').classList.add('open');
    }

    openDocDrawer() {
        document.getElementById('docPane').classList.add('open');
    }

    closeDocDrawer() {
        document.getElementById('docPane').classList.remove('open');
    }

    closeDrawers() {
        this.closeDocDrawer();
        document.getElementById('entityPane').classList.remove('open');
        if (this.cy) {
            this.cy.elements(':selected').unselect();
            this.collapseAllTypes();    // Wegklicken fährt die Satelliten ein
        }
    }

    /** Escaping vor jeder Interpolation — Fixture-/Backend-Text ist Fremdtext. */
    static esc(s) {
        const d = document.createElement('span');
        d.textContent = String(s ?? '');
        return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    async fetchJson(url) {
        if (this.entityAbort) this.entityAbort.abort();     // Spec Regel 5
        this.entityAbort = new AbortController();
        const resp = await fetch(url, { signal: this.entityAbort.signal });
        if (resp.status === 401) { window.location.assign('/login'); return null; }
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json();
    }

    async onTypeSelect(typeId, label) {
        this.selectedType = typeId;
        this.closeDocDrawer();          // Belege des vorherigen Kontexts sind veraltet
        this.openEntityDrawer();
        const body = document.getElementById('entityPaneBody');
        document.getElementById('entityPaneTitle').textContent = label;
        body.innerHTML = skeleton(4);
        try {
            const data = await this.fetchJson(
                `/api/ontology/entities?type=${encodeURIComponent(typeId)}`);
            if (!data.entities.length) {
                body.innerHTML = '<p class="ontology-empty">Keine Entitäten dieses Typs im Korpus.</p>';
                return;
            }
            this.renderEntityNodes(typeId, data.entities);
            const esc = WissensnetzApp.esc;
            body.innerHTML = `
                <p class="entity-hint">Auswahl der wichtigsten Entitäten</p>
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
        this.closeDocDrawer();          // Beleg gehört zur vorherigen Entität
        const satellite = this.cy.getElementById(`ent:${entityId}`);
        if (satellite.nonempty() && !satellite.selected()) {
            this.cy.elements(':selected').unselect();
            satellite.select();
        }
        const body = document.getElementById('entityPaneBody');
        body.innerHTML = skeleton(5);
        try {
            const data = await this.fetchJson(
                `/api/ontology/entities/${encodeURIComponent(entityId)}`);
            const esc = WissensnetzApp.esc;
            const relations = data.relations.length
                ? `<ul class="entity-relations">${data.relations.map((r) => `
                     <li><span class="predicate">${r.direction === 'in' ? '← ' : ''}${esc(r.predicate)}</span>
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
                         <span class="evidence-source">${esc(ev.document.title)}, Seite ${ev.page}</span>
                     </button></li>`).join('')}
                   </ol>`
                : '<p class="ontology-empty">Keine Belege zu dieser Entität erfasst.</p>';
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
            `${evidence.title} – Seite ${evidence.page}`;
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
        this.openDocDrawer();
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.wissensnetzApp = new WissensnetzApp();
});
