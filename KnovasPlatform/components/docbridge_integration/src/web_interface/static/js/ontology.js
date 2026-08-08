// Knovas Cortex — Ontologie-Explorer (Vertrag: /api/ontology/*)
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

/* Benannte Symbole im Stil der bestehenden UI-Icons (Feather, Strichstärke 2).
   Schlüssel sind Icon-NAMEN, nicht Typ-IDs: der Datenvertrag darf pro Typ ein
   optionales `icon: "<name>"` mitliefern, das immer gewinnt. Farben kommen
   zur Laufzeit aus den Tokens, keine Hexwerte hier. */
const ICONS = {
    person: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    people: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    document: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
    court: '<line x1="3" y1="22" x2="21" y2="22"/><line x1="6" y1="18" x2="6" y2="11"/><line x1="10" y1="18" x2="10" y2="11"/><line x1="14" y1="18" x2="14" y2="11"/><line x1="18" y1="18" x2="18" y2="11"/><polygon points="12 2 20 7 4 7"/>',
    clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    card: '<rect x="1" y="4" width="22" height="16" rx="2" ry="2"/><line x1="1" y1="10" x2="23" y2="10"/>',
    layers: '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
    briefcase: '<rect x="2" y="7" width="20" height="14" rx="2" ry="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
    book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
    mail: '<path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22 6 12 13 2 6"/>',
    place: '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    tag: '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.83z"/><line x1="7" y1="7" x2="7.01" y2="7"/>',
    filter: '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
    plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
};


/* Default-Pack "Recht": Schlüsselwort -> Icon-Name, erster Treffer gewinnt
   (spezifisch vor generisch). Bewusst nur eine VERMUTUNG für den Fall, dass
   die Daten kein `icon` mitbringen — bei fremden Domänen/Sprachen greift
   stattdessen das Monogramm, statt dass alles gleich aussieht. Ein eigenes
   Pack pro Branche gehört später in die Daten, nicht in diese Datei. */
const KEYWORD_PACK = [
    [/dokumenttyp|dokumentart|kategorie|rubrik/, 'layers'],
    [/gegenpartei|gegner|beklagt/, 'people'],
    [/mandant|klient|partei|person|anwalt|klager|zeuge|kontakt/, 'person'],
    [/dossier|akte|fall|mandat|verfahren|projekt/, 'folder'],
    [/gericht|instanz|kammer|behorde|amt/, 'court'],
    [/frist|termin|datum|fallig|stichtag|laufzeit/, 'clock'],
    [/honorar|kosten|rechnung|betrag|gebuhr|streitwert|zahlung|preis/, 'card'],
    [/firma|gesellschaft|unternehmen|gmbh|\bag\b|konzern|arbeitgeber/, 'briefcase'],
    [/gesetz|artikel|paragraf|norm|verordnung|richtlinie/, 'book'],
    [/schreiben|brief|korrespondenz|mail|mitteilung/, 'mail'],
    [/ort|adresse|standort|liegenschaft|grundstuck|objekt/, 'place'],
    [/versicherung|police|schaden|risiko|haftung|garantie/, 'shield'],
    [/vertrag|klausel|urkunde|vereinbarung|nachtrag|dokument/, 'document'],
];

/** Anteil erkannter Typen, ab dem Symbole statt Monogrammen gezeigt werden.
    Darunter wirkt das Bild als Flickenteppich — dann bekommen ALLE ein
    Monogramm, was als System gewollt aussieht statt kaputt. */
const ICON_COVERAGE_MIN = 0.6;

/** Umlaut-Faltung für die Schlüsselwortsuche (identisch zum Backend-Muster). */
function foldText(s) {
    return String(s || '').toLowerCase()
        .replace(/ä/g, 'a').replace(/ö/g, 'o').replace(/ü/g, 'u').replace(/ß/g, 'ss');
}

/** SVG-Icon als Data-URI für Cytoscape-Knoten, Strichfarbe aus dem Token.
    Rand fürs Icon über translate, NICHT über negativen ViewBox-Ursprung —
    Chrome rastert SVGs mit negativem Ursprung im Canvas falsch (verschoben,
    abgeschnitten). Explizite width/height für die intrinsische Grösse. */
function svgDataUri(inner, color) {
    const svg =
        `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 28 28">` +
        `<g transform="translate(2 2)" fill="none" stroke="${color}" stroke-width="2" ` +
        `stroke-linecap="round" stroke-linejoin="round">` + inner + '</g></svg>';
    return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
}

/** Monogramm-Fallback: Anfangsbuchstabe des Labels im Knoten (wie
    Avatar-Initialen). Sprach- und branchenunabhängig, trägt Information und
    kann nie „falsch" sein — das Sicherheitsnetz für unbekannte Vokabulare.
    Webfonts stehen im SVG-Bildkontext nicht zur Verfügung, daher System-Mono. */
function monogramDataUri(label, color) {
    const first = Array.from(String(label || '').trim())[0] || '?';
    const glyph = first.toLocaleUpperCase()
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return svgDataUri(
        `<text x="12" y="12" text-anchor="middle" dominant-baseline="central" ` +
        `stroke="none" fill="${color}" font-size="15" font-weight="600" ` +
        `font-family="ui-monospace, SFMono-Regular, Menlo, monospace">${glyph}</text>`,
        color);
}

/** Icon-Name eines Typs: explizites Feld aus dem Vertrag schlägt alles. */
function explicitIconName(type) {
    const name = String(type.icon || '').trim();
    return ICONS[name] ? name : null;
}

/** Vermutung aus dem Default-Pack über Label und ID. */
function guessedIconName(type) {
    const hay = foldText(`${type.label || ''} ${type.id || ''}`);
    for (const [pattern, name] of KEYWORD_PACK) {
        if (pattern.test(hay)) return name;
    }
    return null;
}

/** Symbol je Typ, als Data-URI in der Reihenfolge:
    explizites `icon` -> Default-Pack (nur bei genügend Abdeckung) -> Monogramm.
    Die Abdeckung entscheidet für den GANZEN Graphen, damit das Bild
    einheitlich bleibt. */
function iconsForTypes(types, color) {
    const explicit = types.map(explicitIconName);
    const guessed = types.map(guessedIconName);
    const known = types.filter((_, i) => explicit[i] || guessed[i]).length;
    const useGuesses = types.length > 0 && known / types.length >= ICON_COVERAGE_MIN;
    return types.map((t, i) => {
        const name = explicit[i] || (useGuesses ? guessed[i] : null);
        return name ? svgDataUri(ICONS[name], color) : monogramDataUri(t.label, color);
    });
}

function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
}

class CortexApp {
    constructor() {
        this.cy = null;
        this.selectedType = null;
        this.selectedEntity = null;
        this.entityAbort = null;
        this.expandedTypes = new Map();   // typeId -> Cytoscape-Collection der Satelliten
        this.zoomAnim = null;             // laufende Kamerafahrt (Promise)
        this.init();
    }

    async init() {
        try {
            const [resp] = await Promise.all([fetch('/api/ontology/summary'), loadBrandFonts()]);
            if (resp.status === 401) { window.location.assign('/login'); return; }
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            this.bindZoomControls();
            this.bindDrawerControls();
            document.getElementById('typeCreate')
                .addEventListener('click', () => this.openTypeCreateForm());
            // Auch ohne Typen wird der Graph gezeigt: dann steht dort nur der
            // Plus-Knoten - ein Anfang statt einer Sackgasse.
            this.renderGraph(data);
        } catch (err) {
            console.error('Cortex: Summary nicht ladbar', err);
            const empty = document.getElementById('graphEmpty');
            empty.textContent = 'Cortex konnte nicht geladen werden. Seite neu laden.';
            empty.hidden = false;
            ['zoomIn', 'zoomOut', 'zoomFit'].forEach((id) => {
                document.getElementById(id).hidden = true;
            });
        }
    }

    renderGraph(data) {
        const iconColor = cssToken('--primary-color');
        const maxCount = Math.max(...data.types.map((t) => t.count), 1);
        const n = data.types.length;
        // Deterministischer Kreis-Seed (Spec Regel 4): das anschliessende
        // cose-Layout mit randomize:false liefert damit stets dasselbe Bild.
        const seedRadius = 300;
        const typeIcons = iconsForTypes(data.types, iconColor);
        const nodes = data.types.map((t, i) => ({
            data: { id: t.id, label: t.label, count: t.count,
                    icon: typeIcons[i],
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
                    'transition-property': 'opacity, text-opacity',
                    'transition-duration': '0.2s',
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
                // Keine Label-Platte — der Fokus-Modus räumt den Hintergrund frei.
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
                // Auswahl/Hover für Satelliten: die Klassen-Styles oben stehen
                // später im Stylesheet als node:selected und würden das
                // Feedback sonst überschreiben (spätere Regel gewinnt).
                { selector: 'node.entity:selected', style: {
                    'background-color': cssToken('--highlight'),
                    'border-color': cssToken('--primary-color'),
                    'border-width': 2.5,
                } },
                { selector: 'node.entity.hovered', style: {
                    'border-color': cssToken('--primary-color'),
                } },
                // Fokus-Modus: während ein Typ aufgeklappt ist, treten die
                // unbeteiligten Teile des Netzes zurück (Kantenlabels ganz weg —
                // sie sind es, die mit den Satelliten-Texten kollidieren).
                { selector: 'node.faded', style: {
                    'opacity': 0.35,
                    'text-opacity': 0.35,
                } },
                { selector: 'edge.faded', style: {
                    'opacity': 0.18,
                    'text-opacity': 0,
                } },
                // Filter-Unter-Knoten: Trichter am Entitäts-Satelliten.
                { selector: 'node.filter-node', style: {
                    'background-color': cssToken('--card-bg'),
                    'border-width': 1.5,
                    'border-style': 'dashed',
                    'border-color': cssToken('--accent'),
                    'font-family': cssFontToken('--font-body') || 'IBM Plex Sans, sans-serif',
                    'font-weight': 400,
                    'font-size': 10,
                    'color': cssToken('--text-secondary'),
                    'text-margin-y': 4,
                    'text-wrap': 'wrap',
                    'text-max-width': 140,
                } },
                { selector: 'node.filter-node:selected', style: {
                    'background-color': cssToken('--highlight'),
                    'border-color': cssToken('--primary-color'),
                } },
                { selector: 'node.filter-node.hovered', style: {
                    'border-color': cssToken('--primary-color'),
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
                    'transition-property': 'opacity, text-opacity',
                    'transition-duration': '0.2s',
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
        if (nodes.length) this.runLayout();

        this.cy.on('tap', 'node', (evt) => {
            const node = evt.target;
            if (node.hasClass('filter-node')) {
                this.openEntityDrawer();
                this.renderFilterPanel(node.data('filterId'));
            } else if (node.hasClass('entity')) {
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
            if (evt.target !== this.cy) return;
            if (CortexApp.reducedMotion()) { this.cy.fit(undefined, 60); return; }
            this.cy.stop();
            this.cy.animate({ fit: { padding: 60 } },
                            { duration: 350, easing: 'ease-out-quart' });
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

    /** Kraeftelayout ab dem Kreis-Seed, deterministisch. */
    runLayout() {
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
    }

    bindZoomControls() {
        // Buttons gleiten statt zu springen; cy.stop() bricht eine laufende
        // Fahrt ab, damit schnelle Klickfolgen sich nicht stapeln.
        const zoomBy = (factor) => {
            const level = this.cy.zoom() * factor;
            const center = { x: this.cy.width() / 2, y: this.cy.height() / 2 };
            if (CortexApp.reducedMotion()) {
                this.cy.zoom({ level, renderedPosition: center });
                return;
            }
            this.cy.stop();
            this.cy.animate({ zoom: { level, renderedPosition: center } },
                            { duration: 220, easing: 'ease-out-quart' });
        };
        const fitAll = () => {
            if (CortexApp.reducedMotion()) { this.cy.fit(undefined, 60); return; }
            this.cy.stop();
            this.cy.animate({ fit: { padding: 60 } },
                            { duration: 350, easing: 'ease-out-quart' });
        };
        document.getElementById('zoomIn').addEventListener('click', () => zoomBy(1.25));
        document.getElementById('zoomOut').addEventListener('click', () => zoomBy(0.8));
        document.getElementById('zoomFit').addEventListener('click', fitAll);
    }

    /** Typ-Knoten-Klick: aufklappen (Kamerafahrt + Satelliten + Drawer)
        bzw. wieder einklappen (inkl. Rückfahrt zur Übersicht). */
    onTypeTap(node) {
        const typeId = node.id();
        if (this.expandedTypes.has(typeId)) {
            this.closeDrawers();         // klappt ein und zoomt zurück
            // Der Tap selektiert den Knoten nativ erst nach diesem Handler —
            // beim Einklappen soll aber nichts ausgewählt zurückbleiben.
            setTimeout(() => node.unselect(), 0);
            return;
        }
        this.collapseAllTypes(typeId);   // nur ein Typ gleichzeitig aufgeklappt
        this.zoomAnim = this.zoomToNode(node);
        this.onTypeSelect(typeId, node.data('label'));
    }

    static reducedMotion() {
        return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    /** Kamerafahrt auf einen Knoten; zentriert in der Fläche links vom
        Entitäten-Drawer. Liefert ein Promise für die Sequenz "erst Zoom,
        dann Satelliten". */
    zoomToNode(node) {
        const level = Math.max(this.cy.zoom(), 1.35);
        const p = node.position();
        // Sichtbares Zentrum: der Drawer (400px + Ränder) verdeckt rechts.
        const visibleW = Math.max(this.cy.width() - 432, this.cy.width() * 0.45);
        const pan = { x: visibleW / 2 - p.x * level,
                      y: this.cy.height() / 2 - p.y * level };
        if (CortexApp.reducedMotion()) {
            this.cy.viewport({ zoom: level, pan });
            return Promise.resolve();
        }
        // Schnell losfahren, weich ausgleiten (ease-out statt ease-in-out) —
        // und das Promise löst schon im Ausgleiten auf, damit die Satelliten
        // in die letzte Phase der Fahrt hineinpoppen statt danach.
        this.cy.animate({ zoom: level, pan },
                        { duration: 480, easing: 'ease-out-quart' });
        return new Promise((resolve) => setTimeout(resolve, 300));
    }

    /** Zurück zur Gesamtansicht (nach dem Einklappen). */
    animateFit() {
        if (CortexApp.reducedMotion()) { this.cy.fit(undefined, 60); return; }
        this.cy.animate({ fit: { padding: 60 } },
                        { duration: 480, easing: 'ease-out-quart' });
    }

    /** Entitäten eines Typs als Satelliten-Knoten am Typ-Knoten auffächern.
        Wartet eine laufende Kamerafahrt ab: erst reinzoomen, dann aufpoppen. */
    async renderEntityNodes(typeId, entities) {
        if (this.expandedTypes.has(typeId) || !entities.length) return;
        const parent = this.cy.getElementById(typeId);
        if (parent.empty()) return;
        // Slot sofort reservieren, damit Doppel-Klicks nicht doppelt auffächern.
        this.expandedTypes.set(typeId, null);
        parent.addClass('expanded');
        if (this.zoomAnim) { await this.zoomAnim; this.zoomAnim = null; }
        // Während der Kamerafahrt wieder eingeklappt? Dann nichts auffächern.
        if (!this.expandedTypes.has(typeId)) return;
        // Fade erst NACH der Kamerafahrt: Style-Transitions während der
        // Viewport-Animation erzwingen sonst pro Frame einen Voll-Redraw.
        this.applyFocus(typeId);
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
                        data: { id: `ent:${e.id}`, entityId: e.id, typeId,
                                label: e.label, size: 26 },
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
            else satellite.animate({ position: target }, { duration: 320, easing: 'ease-out-quart' });
        });
        this.expandedTypes.set(typeId, added);
    }

    /** Fokus-Modus: unbeteiligte Kanten und Typ-Knoten zurücktreten lassen,
        damit die Satelliten-Texte nicht mit Kantenlabels kollidieren. */
    applyFocus(typeId) {
        const parent = this.cy.getElementById(typeId);
        this.cy.edges().not('.entity-edge').addClass('faded');
        this.cy.nodes().not(parent).not('.entity').not('.filter-node').addClass('faded');
    }

    clearFocus() {
        this.cy.elements('.faded').removeClass('faded');
    }

    collapseType(typeId) {
        const satellites = this.expandedTypes.get(typeId);
        this.expandedTypes.delete(typeId);
        const parent = this.cy.getElementById(typeId);
        parent.removeClass('expanded');
        this.clearFocus();
        if (!satellites) return;
        const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (reduceMotion || parent.empty()) { satellites.remove(); return; }
        // Zurück in den Typ-Knoten gleiten, dann entfernen.
        satellites.nodes().animate({ position: parent.position() },
                                   { duration: 180, easing: 'ease-in-quad' });
        setTimeout(() => satellites.remove(), 190);
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
            const hadExpansion = this.expandedTypes.size > 0;
            this.collapseAllTypes();    // Wegklicken fährt die Satelliten ein
            if (hadExpansion) {
                // Rückfahrt beginnt, während die Satelliten gerade landen —
                // fühlt sich nach einer Bewegung an statt nach zwei Schritten.
                if (CortexApp.reducedMotion()) this.animateFit();
                else setTimeout(() => this.animateFit(), 150);
            }
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

    async deleteJson(url) {
        const resp = await fetch(url, {
            method: 'DELETE',
            headers: { 'X-CSRF-Token': csrfToken() },
        });
        if (resp.status === 401) { window.location.assign('/login'); return null; }
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json();
    }

    /** Loeschknopf im Drawer-Kopf: erscheint nur, wo etwas zu loeschen ist. */
    setDrawerDelete(handler) {
        const button = document.getElementById('entityDelete');
        if (!button) return;
        const fresh = button.cloneNode(true);      // alte Bindung mit entfernen
        button.replaceWith(fresh);
        if (!handler) { fresh.hidden = true; return; }
        fresh.hidden = false;
        fresh.addEventListener('click', handler);
    }

    /** Bestaetigungsblatt statt Browser-Dialog: nennt die Folgen genau,
        bevor etwas verschwindet. */
    askDelete({ title, detail, onConfirm, onCancel }) {
        const esc = CortexApp.esc;
        const body = document.getElementById('entityPaneBody');
        this.setDrawerDelete(null);
        body.innerHTML = `
            <div class="entity-detail">
                <h3>${esc(title)}</h3>
                <p class="confirm-detail">${esc(detail)}</p>
                <div class="confirm-actions">
                    <button type="button" id="confirmDelete" class="btn btn-danger">Endgültig löschen</button>
                    <button type="button" id="cancelDelete" class="btn-text">Abbrechen</button>
                </div>
            </div>`;
        document.getElementById('confirmDelete').addEventListener('click', onConfirm);
        document.getElementById('cancelDelete').addEventListener('click', onCancel);
    }

    confirmTypeDelete(typeId, typeLabel, entityCount) {
        const folgen = entityCount
            ? `Der Typ und ${entityCount} ${entityCount === 1 ? 'Entität' : 'Entitäten'} `
              + 'werden entfernt, samt deren Verbindungen und Belegen.'
            : 'Der Typ enthält keine Entitäten.';
        this.askDelete({
            title: `${typeLabel} löschen?`,
            detail: folgen,
            onConfirm: () => this.onTypeDelete(typeId),
            onCancel: () => this.onTypeSelect(typeId, typeLabel),
        });
    }

    confirmEntityDelete(entityId, entityLabel, typeId) {
        this.askDelete({
            title: `${entityLabel} löschen?`,
            detail: 'Die Entität wird entfernt, samt ihrer Verbindungen und Belege.',
            onConfirm: () => this.onEntityDelete(entityId, typeId),
            onCancel: () => this.onEntitySelect(entityId),
        });
    }

    async postJson(url, payload) {
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json',
                       'X-CSRF-Token': csrfToken() },
            body: JSON.stringify(payload),
        });
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
            // Auch der leere Typ muss loeschbar sein - gerade der vertippte.
            this.setDrawerDelete(() => this.confirmTypeDelete(typeId, label,
                                                             data.entities.length));
            if (!data.entities.length) {
                body.innerHTML = `
                    <p class="ontology-empty">Noch keine Entitäten in diesem Typ.</p>
                    <div class="create-row">
                        <input type="text" id="entityInput" maxlength="120"
                               placeholder="Erste Entität, z. B. Müller Bau AG"
                               aria-label="Name der neuen Entität">
                        <button type="button" id="entityCreateBtn" class="btn btn-primary">Anlegen</button>
                    </div>`;
                const leerInput = document.getElementById('entityInput');
                const leerCreate = () => this.onEntityCreate(typeId, label, leerInput.value);
                document.getElementById('entityCreateBtn').addEventListener('click', leerCreate);
                leerInput.addEventListener('keydown', (evt) => {
                    if (evt.key === 'Enter') { evt.preventDefault(); leerCreate(); }
                });
                leerInput.focus();
                return;
            }
            this.renderEntityNodes(typeId, data.entities);
            const esc = CortexApp.esc;
            // Kartenform analog zu den Suchtreffern (document-card):
            // Metazeile + Titel, Karte selbst ist die Geste.
            body.innerHTML = `
                <p class="entity-hint">Auswahl der wichtigsten Entitäten</p>
                <ul class="entity-cards">${data.entities.map((e) => `
                    <li><button type="button" class="entity-card" data-id="${esc(e.id)}">
                        <span class="entity-card-metaline">${formatCount(e.doc_count)}
                            ${e.doc_count === 1 ? 'Dokument' : 'Dokumente'}</span>
                        <span class="entity-card-title">${esc(e.label)}</span>
                    </button></li>`).join('')}
                </ul>`;
            body.insertAdjacentHTML('beforeend', `
                <div class="create-row">
                    <input type="text" id="entityInput" maxlength="120"
                           placeholder="Neue Entität, z. B. Meier Immobilien AG"
                           aria-label="Name der neuen Entität">
                    <button type="button" id="entityCreateBtn" class="btn btn-outline">Anlegen</button>
                </div>`);
            this.setDrawerDelete(() => this.confirmTypeDelete(typeId, label,
                                                             data.entities.length));
            const entityInput = document.getElementById('entityInput');
            const createEntity = () => this.onEntityCreate(typeId, label, entityInput.value);
            document.getElementById('entityCreateBtn').addEventListener('click', createEntity);
            entityInput.addEventListener('keydown', (evt) => {
                if (evt.key === 'Enter') { evt.preventDefault(); createEntity(); }
            });
            body.querySelectorAll('.entity-card').forEach((btn) =>
                btn.addEventListener('click', () => this.onEntitySelect(btn.dataset.id)));
        } catch (err) {
            if (err.name === 'AbortError') return;
            body.innerHTML = '<p class="ontology-empty">Entitäten konnten nicht geladen werden.</p>';
        }
    }

    selectSatellite(node) {
        if (node.empty() || node.selected()) return;
        this.cy.elements(':selected').unselect();
        node.select();
    }

    /** Der Graph folgt dem Drilldown: Eine verbundene Entität gehört meist zu
        einem anderen Typ, dessen Satelliten noch gar nicht ausgefächert sind.
        Dann Typ aufklappen, hinfahren und die Entität auswählen. */
    async focusEntityInGraph(entity) {
        const existing = this.cy.getElementById(`ent:${entity.id}`);
        if (existing.nonempty()) { this.selectSatellite(existing); return; }
        const typeNode = this.cy.getElementById(entity.type);
        if (typeNode.empty()) return;          // Typ nicht im Graphen
        this.collapseAllTypes(entity.type);
        this.selectedType = entity.type;       // "Zurück zur Liste" zeigt den neuen Typ
        this.zoomAnim = this.zoomToNode(typeNode);
        let entities = [];
        try {
            const data = await this.fetchJson(
                `/api/ontology/entities?type=${encodeURIComponent(entity.type)}`);
            entities = (data && data.entities) || [];
        } catch (err) {
            if (err.name === 'AbortError') return;
        }
        // Die Liste ist eine kuratierte Auswahl — die angesteuerte Entität
        // muss sichtbar sein, auch wenn sie nicht darin vorkommt.
        if (!entities.some((e) => e.id === entity.id)) {
            entities = [{ id: entity.id, label: entity.label, doc_count: 0 }, ...entities];
        }
        await this.renderEntityNodes(entity.type, entities);
        this.selectSatellite(this.cy.getElementById(`ent:${entity.id}`));
    }

    async onEntitySelect(entityId) {
        this.selectedEntity = entityId;
        this.closeDocDrawer();          // Beleg gehört zur vorherigen Entität
        const body = document.getElementById('entityPaneBody');
        body.innerHTML = skeleton(5);
        try {
            const data = await this.fetchJson(
                `/api/ontology/entities/${encodeURIComponent(entityId)}`);
            if (!data) return;
            this.focusEntityInGraph(data.entity);   // Graph nachziehen, ohne den Drawer zu blockieren
            const esc = CortexApp.esc;
            // Verbundene Entitäten als klickbare Mini-Karten: Prädikat lesbar
            // (ohne Unterstriche), Chevron als Klick-Signal. Die Richtung
            // steht bewusst nicht hier — die zeigt der Graph.
            const relations = data.relations.length
                ? `<ul class="relation-cards">${data.relations.map((r) => `
                     <li><button type="button" class="relation-card entity-link"
                                 data-id="${esc(r.target.id)}">
                         <span class="relation-card-body">
                             <span class="relation-card-metaline">${esc(r.predicate.replace(/[_-]+/g, ' '))}</span>
                             <span class="relation-card-title">${esc(r.target.label)}</span>
                         </span>
                         <span class="relation-card-chevron" aria-hidden="true">›</span>
                     </button></li>`).join('')}
                   </ul>`
                : '<p class="ontology-empty">Keine verbundenen Entitäten erfasst.</p>';
            const evidence = data.evidence.length
                ? `<ol class="evidence-list">${data.evidence.map((ev, i) => `
                     <li><button type="button" class="evidence-item" data-index="${i}"
                                 data-path="${esc(ev.document.path)}" data-page="${ev.page}"
                                 data-title="${esc(ev.document.title)}">
                         ${ev.quote ? `<span class="evidence-quote">«${esc(ev.quote)}»</span>` : ''}
                         <span class="evidence-source">${esc(ev.document.title)}, Seite ${ev.page}</span>
                     </button></li>`).join('')}
                   </ol>`
                : '<p class="ontology-empty">Keine Belege zu dieser Entität erfasst.</p>';
            const filters = data.filters || [];
            // Label als eigener Block: sonst erzeugt der Zeilenumbruch im
            // Template ein führendes Leerzeichen, das die erste Zeile
            // gegenüber der Statuszeile darunter eingerückt aussehen lässt.
            const filterRows = filters.map((f) => `
                <li><button type="button" class="btn-text filter-chip" data-id="${esc(f.id)}"
                    ><span class="filter-chip-label">${esc(f.label)}</span
                    ><span class="filter-chip-state">${CortexApp.filterStateText(f)}</span
                ></button></li>`).join('');
            // Filter-Karte direkt unter dem Titel: das Feature bekommt die
            // Bühne und erklärt seinen Zweck in einem Satz selbst.
            const filterCard = `
                <section class="filter-card" aria-label="Cortex Filter">
                    <h4 class="filter-card-title">
                        <svg viewBox="0 0 24 24" width="13" height="13" fill="none"
                             stroke="currentColor" stroke-width="2" stroke-linecap="round"
                             stroke-linejoin="round" aria-hidden="true" focusable="false">
                            <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>
                        </svg>
                        Cortex Filter
                    </h4>
                    <p class="filter-card-purpose">Knovas sammelt laufend Fundstellen
                        zu Ihrem Thema. Sie prüfen und entscheiden.</p>
                    ${filters.length ? `<ul class="filter-list">${filterRows}</ul>` : ''}
                    <div class="filter-create">
                        <input type="text" id="filterInput" maxlength="120"
                               placeholder="Thema, z. B. Kündigungsklauseln und Fristen"
                               aria-label="Filterthema">
                        <button type="button" id="filterCreateBtn" class="btn btn-primary">Anlegen</button>
                    </div>
                </section>`;
            body.innerHTML = `
                <div class="entity-detail">
                    <button type="button" class="btn-text" id="entityBack">← Zurück zur Liste</button>
                    <h3>${esc(data.entity.label)}</h3>
                    ${filterCard}
                    <h4>Belege</h4>${evidence}
                    <h4>Verbundene Entitäten</h4>${relations}
                    <div class="connect-form">
                        <input type="text" id="relationInput" maxlength="80"
                               placeholder="Beziehung, z. B. hat Dossier"
                               aria-label="Art der Beziehung">
                        <select id="relationTarget" aria-label="Zielentität">
                            <option value="">Zielentität wählen …</option>
                        </select>
                        <button type="button" id="relationCreateBtn" class="btn btn-outline">Verbinden</button>
                    </div>
                </div>`;
            document.getElementById('entityBack').addEventListener('click', () => {
                const node = this.cy.getElementById(this.selectedType);
                this.onTypeSelect(this.selectedType, node.data('label'));
            });
            this.syncFilterNodes(entityId, filters);
            document.getElementById('relationCreateBtn').addEventListener(
                'click', () => this.onRelationCreate(entityId));
            this.setDrawerDelete(() => this.confirmEntityDelete(
                entityId, data.entity.label, data.entity.type));
            this.fillRelationTargets(entityId);
            body.querySelectorAll('.filter-chip').forEach((btn) =>
                btn.addEventListener('click', () => this.renderFilterPanel(btn.dataset.id)));
            const filterInput = document.getElementById('filterInput');
            const createFilter = () => this.onFilterCreate(entityId, filterInput.value);
            document.getElementById('filterCreateBtn').addEventListener('click', createFilter);
            filterInput.addEventListener('keydown', (evt) => {
                if (evt.key === 'Enter') { evt.preventDefault(); createFilter(); }
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

    /** Entität anlegen: der Graph ist kuratiert, Knovas leitet ihn nicht ab. */
    async onEntityCreate(typeId, typeLabel, label) {
        const input = document.getElementById('entityInput');
        label = String(label || '').trim();
        if (!label) { if (input) input.focus(); return; }
        try {
            const data = await this.postJson('/api/ontology/entities',
                                             { type: typeId, label });
            if (!data) return;
            // Satelliten zuerst abräumen, dann den Typ frisch aufklappen —
            // sonst überschneidet sich die Einfahr-Animation mit dem Neuaufbau.
            this.collapseType(typeId);
            await this.onTypeSelect(typeId, typeLabel);
        } catch (err) {
            console.error('Cortex: Entität nicht anlegbar', err);
        }
    }

    /** Typ anlegen. Ohne Typen gibt es keinen Einstieg in den Graphen —
        deshalb ist der Knopf immer erreichbar, auch im leeren Zustand. */
    /** Formular fuer einen neuen Typ (aufgerufen vom Plus-Knoten im Graphen). */
    openTypeCreateForm() {
        this.openEntityDrawer();
        this.setDrawerDelete(null);
        document.getElementById('entityPaneTitle').textContent = 'Neuer Typ';
        const body = document.getElementById('entityPaneBody');
        body.innerHTML = `
            <div class="entity-detail">
                <p class="entity-hint">Typen sind die Struktur Ihres Netzes,
                   zum Beispiel Mandant, Dossier oder Vertrag.</p>
                <div class="create-row">
                    <input type="text" id="typeInput" maxlength="80"
                           placeholder="Name des Typs, z. B. Mandant"
                           aria-label="Name des neuen Typs">
                    <button type="button" id="typeCreateSubmit" class="btn btn-primary">Anlegen</button>
                </div>
            </div>`;
        const input = document.getElementById('typeInput');
        const submit = () => this.onTypeCreate(input.value);
        document.getElementById('typeCreateSubmit').addEventListener('click', submit);
        input.addEventListener('keydown', (evt) => {
            if (evt.key === 'Enter') { evt.preventDefault(); submit(); }
        });
        input.focus();
    }

    async onTypeCreate(label) {
        label = String(label || '').trim();
        if (!label) { document.getElementById('typeInput').focus(); return; }
        try {
            const data = await this.postJson('/api/ontology/types', { label });
            if (!data) return;
            await this.reloadGraph();
            // Direkt weiterarbeiten: der neue Typ ist offen, Entitäten folgen.
            const node = this.cy.getElementById(data.type.id);
            if (node.nonempty()) this.onTypeTap(node);
        } catch (err) {
            console.error('Cortex: Typ nicht anlegbar', err);
        }
    }

    async onEntityDelete(entityId, typeId) {
        try {
            if (!await this.deleteJson(`/api/ontology/entities/${encodeURIComponent(entityId)}`)) return;
            this.collapseType(typeId);
            const node = this.cy.getElementById(typeId);
            if (node.nonempty()) await this.onTypeSelect(typeId, node.data('label'));
            else this.closeDrawers();
        } catch (err) {
            console.error('Cortex: Entität nicht löschbar', err);
        }
    }

    async onTypeDelete(typeId) {
        try {
            if (!await this.deleteJson(`/api/ontology/types/${encodeURIComponent(typeId)}`)) return;
            this.closeDrawers();
            await this.reloadGraph();
        } catch (err) {
            console.error('Cortex: Typ nicht löschbar', err);
        }
    }

    /** Graph neu aufbauen, nachdem sich die Typ-Ebene geändert hat. */
    async reloadGraph() {
        const data = await this.fetchJson('/api/ontology/summary');
        if (!data || !data.types) return;
        this.expandedTypes.clear();
        if (this.cy) { this.cy.destroy(); this.cy = null; }
        const container = document.getElementById('graphContainer');
        const empty = document.getElementById('graphEmpty');
        container.hidden = false;
        empty.hidden = true;
        this.renderGraph(data);   // Zoom-Knöpfe lesen this.cy erst beim Klick
    }

    /** Auswahlliste für das Verbinden: alle Entitäten ausser dieser. */
    async fillRelationTargets(entityId) {
        const select = document.getElementById('relationTarget');
        if (!select) return;
        try {
            const data = await this.fetchJson('/api/ontology/entities');
            if (!data) return;
            const esc = CortexApp.esc;
            select.insertAdjacentHTML('beforeend', data.entities
                .filter((e) => e.id !== entityId)
                .map((e) => `<option value="${esc(e.id)}">${esc(e.label)}</option>`)
                .join(''));
        } catch (err) {
            if (err.name !== 'AbortError') console.error('Cortex: Ziele nicht ladbar', err);
        }
    }

    async onRelationCreate(entityId) {
        const predicate = String(document.getElementById('relationInput').value || '').trim();
        const target = document.getElementById('relationTarget').value;
        if (!predicate || !target) return;
        try {
            const data = await this.postJson('/api/ontology/relations',
                                             { src: entityId, predicate, dst: target });
            if (!data) return;
            await this.onEntitySelect(entityId);          // Detail neu laden
        } catch (err) {
            console.error('Cortex: Verbindung nicht anlegbar', err);
        }
    }

    static filterStateText(f) {
        if (f.status === 'collecting') return 'liest den Aktenbestand …';
        const parts = [];
        if (f.counts.pending) parts.push(`${f.counts.pending} zur Prüfung`);
        if (f.counts.accepted) parts.push(`${f.counts.accepted} übernommen`);
        if (f.counts.rejected) parts.push(`${f.counts.rejected} abgelehnt`);
        return parts.join(' · ') || 'keine Fundstellen';
    }

    static filterNodeLabel(f) {
        if (f.status === 'collecting') return `${f.label}\nliest …`;
        if (f.counts.pending) return `${f.label}\n${f.counts.pending} zur Prüfung`;
        return f.label;
    }

    /** Filter-Unter-Knoten am Entitäts-Satelliten anlegen/aktualisieren. */
    syncFilterNodes(entityId, filters) {
        const satellite = this.cy.getElementById(`ent:${entityId}`);
        if (satellite.empty()) return;      // Entität ist nicht aufgefächert
        const typeId = satellite.data('typeId');
        const typeNode = typeId ? this.cy.getElementById(typeId) : null;
        const sp = satellite.position();
        // Weiter nach aussen, in Verlängerung Typ -> Satellit.
        let base = -Math.PI / 2;
        if (typeNode && typeNode.nonempty()) {
            const tp = typeNode.position();
            base = Math.atan2(sp.y - tp.y, sp.x - tp.x);
        }
        const reduceMotion = CortexApp.reducedMotion();
        filters.forEach((f, i) => {
            const existing = this.cy.getElementById(`flt:${f.id}`);
            if (existing.nonempty()) {
                existing.data('label', CortexApp.filterNodeLabel(f));
                return;
            }
            const a = base + (i - (filters.length - 1) / 2) * 0.55;
            const target = { x: sp.x + 85 * Math.cos(a), y: sp.y + 85 * Math.sin(a) };
            const added = this.cy.add([
                { group: 'nodes', classes: 'filter-node',
                  data: { id: `flt:${f.id}`, filterId: f.id, entityId,
                          label: CortexApp.filterNodeLabel(f), size: 30,
                          icon: svgDataUri(ICONS.filter, cssToken('--accent')) },
                  position: { x: sp.x, y: sp.y } },
                { group: 'edges', classes: 'entity-edge',
                  data: { id: `fe:${f.id}`, source: `ent:${entityId}`,
                          target: `flt:${f.id}`, label: '', width: 1 } },
            ]);
            const node = added.nodes();
            if (reduceMotion) node.position(target);
            else node.animate({ position: target }, { duration: 320, easing: 'ease-out-quart' });
            // Mit der Satelliten-Collection einklappen/aufräumen.
            if (typeId && this.expandedTypes.has(typeId)) {
                const current = this.expandedTypes.get(typeId);
                if (current) this.expandedTypes.set(typeId, current.union(added));
            }
        });
    }

    async onFilterCreate(entityId, label) {
        const input = document.getElementById('filterInput');
        label = String(label || '').trim();
        if (!label) { if (input) input.focus(); return; }
        try {
            const data = await this.postJson('/api/ontology/filters',
                                             { entity_id: entityId, label });
            if (!data) return;
            this.syncFilterNodes(entityId, [data.filter]);
            this.renderFilterPanel(data.filter.id, data);
        } catch (err) {
            console.error('Cortex: Filter nicht anlegbar', err);
            if (input) input.setCustomValidity('');
        }
    }

    /** Prüf-Panel: Vorschläge eines Filters mit Übernehmen/Ablehnen. */
    async renderFilterPanel(filterId, preloaded = null) {
        const body = document.getElementById('entityPaneBody');
        if (!preloaded) body.innerHTML = skeleton(4);
        try {
            const data = preloaded ||
                await this.fetchJson(`/api/ontology/filters/${encodeURIComponent(filterId)}`);
            if (!data) return;
            this.filterView = { id: filterId, filter: data.filter,
                                proposals: data.proposals, tab: 'pending' };
            document.getElementById('entityPaneTitle').textContent = 'Filter';
            this.setDrawerDelete(null);
            const esc = CortexApp.esc;
            body.innerHTML = `
                <div class="entity-detail">
                    <button type="button" class="btn-text" id="filterBack">← Zurück zur Entität</button>
                    <h3>${esc(data.filter.label)}</h3>
                    <p class="filter-status">${data.filter.status === 'collecting'
                        ? 'Knovas liest den Aktenbestand laufend. Noch keine Fundstellen.'
                        : 'Läuft laufend im Hintergrund. Knovas schlägt vor, Sie entscheiden.'}</p>
                    <div class="tab-chips" role="tablist" id="filterTabs"></div>
                    <div id="proposalList"></div>
                </div>`;
            document.getElementById('filterBack').addEventListener('click', () =>
                this.onEntitySelect(this.filterView.filter.entity_id));
            this.renderProposalTabs();
            this.renderProposalList();
        } catch (err) {
            if (err.name === 'AbortError') return;
            body.innerHTML = '<p class="ontology-empty">Filter konnte nicht geladen werden.</p>';
        }
    }

    renderProposalTabs() {
        const counts = { pending: 0, accepted: 0, rejected: 0 };
        this.filterView.proposals.forEach((p) => { counts[p.state] += 1; });
        const tabs = [['pending', 'Zur Prüfung'], ['accepted', 'Übernommen'],
                      ['rejected', 'Abgelehnt']];
        const holder = document.getElementById('filterTabs');
        holder.innerHTML = tabs.map(([key, label]) => `
            <button type="button" role="tab" class="tab-chip${this.filterView.tab === key ? ' active' : ''}"
                    data-tab="${key}">${label} (${counts[key]})</button>`).join('');
        holder.querySelectorAll('.tab-chip').forEach((btn) =>
            btn.addEventListener('click', () => {
                this.filterView.tab = btn.dataset.tab;
                this.renderProposalTabs();
                this.renderProposalList();
            }));
    }

    renderProposalList() {
        const esc = CortexApp.esc;
        const view = this.filterView;
        const list = document.getElementById('proposalList');
        const shown = view.proposals.filter((p) => p.state === view.tab);
        if (!shown.length) {
            const empty = { pending: 'Keine offenen Vorschläge.',
                            accepted: 'Noch nichts übernommen.',
                            rejected: 'Noch nichts abgelehnt.' };
            list.innerHTML = `<p class="ontology-empty">${empty[view.tab]}</p>`;
            return;
        }
        list.innerHTML = shown.map((p) => `
            <div class="proposal-card" data-id="${esc(p.id)}">
                <span class="score-chip">Zuversicht ${Math.round(p.score * 100)} %</span>
                <button type="button" class="evidence-item proposal-quote"
                        data-path="${esc(p.document.path)}" data-page="${p.page}"
                        data-title="${esc(p.document.title)}">
                    ${p.quote ? `<span class="evidence-quote">«${esc(p.quote)}»</span>` : ''}
                    <span class="evidence-source">${esc(p.document.title)}, Seite ${p.page}</span>
                </button>
                ${p.state === 'pending' ? `
                <div class="proposal-actions">
                    <button type="button" class="btn btn-outline act-accept">Übernehmen</button>
                    <button type="button" class="btn-text act-reject">Ablehnen</button>
                </div>` : ''}
                ${p.state === 'rejected' ? `
                <p class="proposal-note">Dauerhaft gemerkt. Wird nie wieder vorgeschlagen,
                   auch bei erneutem Upload.</p>` : ''}
            </div>`).join('');
        list.querySelectorAll('.proposal-quote').forEach((btn) =>
            btn.addEventListener('click', () => {
                this.onEvidenceSelect({ path: btn.dataset.path,
                                        page: Number(btn.dataset.page),
                                        title: btn.dataset.title });
            }));
        list.querySelectorAll('.act-accept').forEach((btn) =>
            btn.addEventListener('click', () =>
                this.onProposalDecide(btn.closest('.proposal-card'), 'accept')));
        list.querySelectorAll('.act-reject').forEach((btn) =>
            btn.addEventListener('click', () =>
                this.onProposalDecide(btn.closest('.proposal-card'), 'reject')));
    }

    async onProposalDecide(card, action) {
        const proposalId = card.dataset.id;
        const view = this.filterView;
        try {
            const data = await this.postJson(
                `/api/ontology/filters/${encodeURIComponent(view.id)}/decision`,
                { proposal_id: proposalId, action });
            if (!data) return;
            const proposal = view.proposals.find((p) => p.id === proposalId);
            if (proposal) proposal.state = data.proposal.state;
            this.updateFilterNodeCounts();
            if (action === 'reject') {
                // Das Produktversprechen im Moment der Korrektur zeigen.
                card.innerHTML = '<p class="proposal-note proposal-confirm">' +
                    'Verstanden. Wird nie wieder vorgeschlagen.</p>';
                setTimeout(() => { this.renderProposalTabs(); this.renderProposalList(); }, 1400);
            } else {
                this.renderProposalTabs();
                this.renderProposalList();
            }
        } catch (err) {
            console.error('Cortex: Entscheidung fehlgeschlagen', err);
        }
    }

    updateFilterNodeCounts() {
        const view = this.filterView;
        if (!view) return;
        const counts = { pending: 0, accepted: 0, rejected: 0 };
        view.proposals.forEach((p) => { counts[p.state] += 1; });
        const f = { ...view.filter, counts,
                    status: view.proposals.length ? 'active' : 'collecting' };
        const node = this.cy.getElementById(`flt:${view.id}`);
        if (node.nonempty()) node.data('label', CortexApp.filterNodeLabel(f));
    }

    onEvidenceSelect(evidence) {
        const body = document.getElementById('docPaneBody');
        document.getElementById('docPaneTitle').textContent =
            `${evidence.title}, Seite ${evidence.page}`;
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
    window.cortexApp = new CortexApp();
});
