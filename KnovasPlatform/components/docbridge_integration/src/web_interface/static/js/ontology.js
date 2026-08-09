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
                    src: r.src, dst: r.dst, predicate: r.predicate,
                    label: r.count ? `${r.predicate} (${formatCount(r.count)})`
                                   : r.predicate,
                    width: r.count ? 1.5 + 3 * (r.count / maxRel) : 1.5 },
            classes: r.count ? '' : 'declared',
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
                // Vorgabe: gestrichelt und ohne Zahl. Bleibt sichtbar, solange
                // keine echte Verbindung dieser Art existiert.
                { selector: 'edge.declared', style: {
                    'line-style': 'dashed',
                    'width': 1.5,
                    'line-color': cssToken('--callout'),
                    'target-arrow-color': cssToken('--callout'),
                } },
                // Beobachtete Verbindung zwischen zwei Entitaeten: durchgezogen
                // mit Pfeil und Namen, klar unterschieden von der Stammlinie.
                { selector: 'edge.observed-relation', style: {
                    'line-style': 'solid',
                    'line-color': cssToken('--border-color'),
                    'target-arrow-shape': 'triangle',
                    'target-arrow-color': cssToken('--border-color'),
                    'width': 1.5,
                } },
                { selector: 'edge.connect-preview', style: {
                    'line-style': 'dashed',
                    'line-color': cssToken('--primary-color'),
                    'target-arrow-shape': 'none',
                    'width': 2,
                    'label': '',
                } },
                { selector: 'node.connect-pointer', style: {
                    'width': 1, 'height': 1, 'opacity': 0, 'label': '',
                } },
                { selector: 'node.connect-target', style: {
                    'border-color': cssToken('--primary-color'),
                    'border-width': 4,
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
        this.cy.on('tap', 'edge', (evt) => {
            const kante = evt.target;
            if (kante.hasClass('entity-edge') && !kante.data('predicate')) return;
            this.onEdgeSelect(kante);
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

        if (this.connect) this.connect.destroy();
        this.connect = new ConnectGesture(this.cy, {
            onConnect: (zug) => this.onConnectDrawn(zug),
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
            const center = { x: this.cy.width() / 2, y: this.cy.height() / 2 };
            const level = Math.min(this.cy.maxZoom(),
                                   Math.max(this.cy.minZoom(), this.cy.zoom() * factor));
            if (CortexApp.reducedMotion()) {
                this.cy.zoom({ level, renderedPosition: center });
                return;
            }
            // cy.animate versteht die Objektform {level, renderedPosition}
            // nicht - sie bleibt wirkungslos. Deshalb den Schwenk selbst
            // rechnen: der Modellpunkt unter der Bildmitte bleibt stehen.
            const z0 = this.cy.zoom();
            const p0 = this.cy.pan();
            const modell = { x: (center.x - p0.x) / z0, y: (center.y - p0.y) / z0 };
            const pan = { x: center.x - modell.x * level,
                          y: center.y - modell.y * level };
            this.cy.stop();
            this.cy.animate({ zoom: level, pan },
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
    onTypeTap(node, { fly = true } = {}) {
        const typeId = node.id();
        if (this.expandedTypes.has(typeId)) {
            // Nur DIESEN Typ zuklappen: stehen mehrere offen, sollen die
            // anderen Gruppen stehen bleiben. Erst wenn keiner mehr offen
            // ist, faehrt die Kamera zur Uebersicht zurueck.
            this.collapseType(typeId);
            this.closeDocDrawer();
            document.getElementById('entityPane').classList.remove('open');
            if (!this.expandedTypes.size) {
                if (CortexApp.reducedMotion()) this.animateFit();
                else setTimeout(() => this.animateFit(), 150);
            }
            // Der Tap selektiert den Knoten nativ erst nach diesem Handler —
            // beim Einklappen soll aber nichts ausgewählt zurückbleiben.
            setTimeout(() => node.unselect(), 0);
            return;
        }
        // Mehrere Typen duerfen gleichzeitig offen stehen: eine Verbindung
        // zwischen Entitaeten VERSCHIEDENER Typen ist sonst gar nicht
        // zeichenbar, weil nie zwei Satellitengruppen sichtbar waeren.
        // Sofort als aufgeklappt vermerken: sonst gilt ein Typ OHNE Entitäten
        // nie als offen und liesse sich nicht zuklappen - jeder weitere Klick
        // führe erneut hin.
        this.expandedTypes.set(typeId, null);
        node.addClass('expanded');
        this.applyFocus();
        this.zoomAnim = fly ? this.zoomToNode(node) : null;
        this.onTypeSelect(typeId, node.data('label'));
    }

    static reducedMotion() {
        return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    /** Kamerafahrt auf einen Knoten; zentriert in der Fläche links vom
        Entitäten-Drawer. Liefert ein Promise für die Sequenz "erst Zoom,
        dann Satelliten". */
    zoomToNode(node) {
        // Nur fahren, wenn es etwas bringt: liegt der Knoten schon gut im
        // sichtbaren Bereich links vom Drawer, bleibt die Kamera stehen.
        // Sonst wirkt jeder Klick, als verschöbe sich der ganze Graph.
        const visible = Math.max(this.cy.width() - 432, this.cy.width() * 0.45);
        const r = node.renderedPosition();
        const margin = 80;
        const bereitsGut = this.cy.zoom() >= 0.85
            && r.x > margin && r.x < visible - margin
            && r.y > margin && r.y < this.cy.height() - margin;
        if (bereitsGut) return Promise.resolve();

        const level = Math.max(this.cy.zoom(), 1.15);
        const p = node.position();
        // Sichtbares Zentrum: der Drawer (400px + Ränder) verdeckt rechts.
        const visibleW = visible;
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

    /** Eine Gruppe von Zielpunkten so einrahmen, dass sie links vom Drawer
        vollstaendig sichtbar ist. Passt sie schon, bleibt die Kamera stehen. */
    frameGroup(punkte) {
        if (!punkte.length) return;
        const pad = 40;
        const sichtbar = Math.max(this.cy.width() - 432, this.cy.width() * 0.45);
        const zoom = this.cy.zoom();
        const pan = this.cy.pan();
        const gerendert = punkte.map((q) => ({
            x1: q.x * zoom + pan.x - q.r, x2: q.x * zoom + pan.x + q.r,
            y1: q.y * zoom + pan.y - q.r, y2: q.y * zoom + pan.y + q.r,
        }));
        const passt = gerendert.every((b) => b.x1 > pad && b.x2 < sichtbar - pad
            && b.y1 > pad && b.y2 < this.cy.height() - pad);
        if (passt) return;

        const x1 = Math.min(...punkte.map((q) => q.x - q.r));
        const x2 = Math.max(...punkte.map((q) => q.x + q.r));
        const y1 = Math.min(...punkte.map((q) => q.y - q.r));
        const y2 = Math.max(...punkte.map((q) => q.y + q.r));
        const level = Math.min(this.cy.maxZoom(), 1.4,
                               (sichtbar - 2 * pad) / Math.max(x2 - x1, 1),
                               (this.cy.height() - 2 * pad) / Math.max(y2 - y1, 1));
        const ziel = { x: sichtbar / 2 - ((x1 + x2) / 2) * level,
                       y: this.cy.height() / 2 - ((y1 + y2) / 2) * level };
        if (CortexApp.reducedMotion()) { this.cy.viewport({ zoom: level, pan: ziel }); return; }
        this.cy.stop();
        this.cy.animate({ zoom: level, pan: ziel },
                        { duration: 420, easing: 'ease-out-quart' });
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
        if (this.expandedTypes.get(typeId) || !entities.length) return;
        const parent = this.cy.getElementById(typeId);
        if (parent.empty()) return;
        if (this.zoomAnim) { await this.zoomAnim; this.zoomAnim = null; }
        // Während der Kamerafahrt wieder eingeklappt? Dann nichts auffächern.
        if (!this.expandedTypes.has(typeId)) return;
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
        const ziele = [{ x: p.x, y: p.y, r: parent.data('size') / 2 }];
        added.nodes().forEach((satellite, i) => {
            const a = k === 1 ? base : base - spread / 2 + (spread * i) / (k - 1);
            const target = { x: p.x + radius * Math.cos(a), y: p.y + radius * Math.sin(a) };
            ziele.push({ x: target.x, y: target.y, r: 60 });   // Radius inkl. Beschriftung
            if (reduceMotion) satellite.position(target);
            else satellite.animate({ position: target }, { duration: 320, easing: 'ease-out-quart' });
        });
        this.expandedTypes.set(typeId, added);
        // Erst jetzt rahmen: vorher ist nicht bekannt, wohin die Satelliten
        // fliegen. Bei Randknoten landen sie sonst hinter dem Drawer oder
        // ausserhalb und wirken abgeschnitten.
        this.frameGroup(ziele.concat(this.otherOpenPoints(typeId)));
        // Bestehende Verbindungen nachtragen, ohne den Aufbau aufzuhalten.
        this.restoreEntityRelations(entities).catch(() => { /* nie eskalieren */ });
    }

    /** Punkte aller bereits offenen Gruppen ausser der angegebenen. Beim
        Oeffnen eines zweiten Typs muss der Ausschnitt BEIDE Gruppen fassen:
        sonst waere die erste aus dem Bild gewandert und der Zug zwischen
        zwei Gruppen genau dann unmoeglich, wenn er gebraucht wird. */
    otherOpenPoints(ausser) {
        const punkte = [];
        for (const [typeId, satelliten] of this.expandedTypes) {
            if (typeId === ausser) continue;
            const knoten = this.cy.getElementById(typeId);
            if (knoten.empty()) continue;
            const gruppe = satelliten ? knoten.union(satelliten) : knoten;
            gruppe.forEach((el) => {
                const p = el.position();
                punkte.push({ x: p.x, y: p.y, r: 60 });   // Radius inkl. Beschriftung
            });
        }
        return punkte;
    }

    /** Anfragelimit: hoechstens so viele Detailabfragen je Auffaechern, in
        kleinen Buendeln nacheinander statt alles auf einmal. */
    static get RELATION_FETCH_MAX() { return 24; }

    static get RELATION_FETCH_BATCH() { return 6; }

    /** Bestehende Verbindungen der gerade aufgefaecherten Entitaeten
        nachzeichnen. Ohne das waere eine gezogene Linie nach dem Einklappen
        oder einem Neuladen verschwunden, obwohl sie serverseitig besteht.
        Gezeichnet wird nur, was beidseitig als Knoten im Graphen liegt. */
    async restoreEntityRelations(entities) {
        const ids = entities
            .map((e) => e.id)
            .filter((id) => this.cy.getElementById(`ent:${id}`).nonempty())
            .slice(0, CortexApp.RELATION_FETCH_MAX);
        for (let i = 0; i < ids.length; i += CortexApp.RELATION_FETCH_BATCH) {
            const buendel = ids.slice(i, i + CortexApp.RELATION_FETCH_BATCH);
            // Eigener fetch statt fetchJson: dessen AbortController bricht die
            // jeweils vorige Abfrage ab, hier laufen mehrere nebeneinander.
            const antworten = await Promise.all(buendel.map((id) =>
                fetch(`/api/ontology/entities/${encodeURIComponent(id)}`)
                    .then((resp) => (resp.ok ? resp.json() : null))
                    .catch(() => null)));
            antworten.forEach((data, k) => {
                if (!data || !Array.isArray(data.relations)) return;
                data.relations.forEach((rel) => this.drawKnownRelation(buendel[k], rel));
            });
        }
    }

    /** Eine gemeldete Relation zeichnen, wenn beide Enden im Graphen liegen.
        Auch die eingehende Richtung wird ausgewertet: sonst bliebe eine
        Verbindung von einem laenger offenen Typ zu einem gerade geoeffneten
        unsichtbar, weil nur die neuen Entitaeten abgefragt werden. */
    drawKnownRelation(entityId, rel) {
        if (!rel || !rel.target || !rel.target.id || !rel.predicate) return;
        const hier = `ent:${entityId}`;
        const dort = `ent:${rel.target.id}`;
        if (this.cy.getElementById(dort).empty()) return;
        if (rel.direction === 'out') this.addRelationToGraph(hier, dort, rel.predicate, 'entitaet');
        else if (rel.direction === 'in') this.addRelationToGraph(dort, hier, rel.predicate, 'entitaet');
    }

    /** Fokus-Modus: unbeteiligte Kanten und Typ-Knoten zurücktreten lassen,
        damit die Satelliten-Texte nicht mit Kantenlabels kollidieren.
        Beruecksichtigt ALLE offenen Typen - stehen zwei offen, bleiben beide
        Gruppen sichtbar, sonst waere der Leitfall Mandant zu Dossier auf
        Entitaetsebene nicht herstellbar. */
    applyFocus() {
        this.clearFocus();          // zuerst raeumen: die Menge hat sich geaendert
        let offen = this.cy.collection();
        for (const typeId of this.expandedTypes.keys()) {
            offen = offen.union(this.cy.getElementById(typeId));
        }
        this.cy.edges().not('.entity-edge').not('.observed-relation').addClass('faded');
        this.cy.nodes().not(offen).not('.entity').not('.filter-node').addClass('faded');
    }

    clearFocus() {
        this.cy.elements('.faded').removeClass('faded');
    }

    /** Fokus nachziehen, nachdem sich die Menge der offenen Typen geaendert
        hat: ohne offenen Typ gibt es nichts hervorzuheben. */
    refreshFocus() {
        if (this.expandedTypes.size) this.applyFocus();
        else this.clearFocus();
    }

    collapseType(typeId) {
        const satellites = this.expandedTypes.get(typeId);
        this.expandedTypes.delete(typeId);
        const parent = this.cy.getElementById(typeId);
        parent.removeClass('expanded');
        this.refreshFocus();
        if (!satellites) return;
        const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (reduceMotion || parent.empty()) { satellites.remove(); return; }
        // Zurück in den Typ-Knoten gleiten, dann entfernen.
        satellites.nodes().animate({ position: parent.position() },
                                   { duration: 180, easing: 'ease-in-quad' });
        setTimeout(() => satellites.remove(), 190);
    }

    /** Alles einklappen. Nur beim Wegklicken - einzelne Typen bleiben sonst
        nebeneinander offen. */
    collapseAllTypes() {
        for (const typeId of [...this.expandedTypes.keys()]) {
            this.collapseType(typeId);
        }
    }

    bindDrawerControls() {
        document.getElementById('entityClose').addEventListener('click', () => this.closeDrawers());
        document.getElementById('docClose').addEventListener('click', () => this.closeDocDrawer());
    }

    openEntityDrawer() {
        document.getElementById('entityPane').classList.add('open');
        this.resetStageScroll();
    }

    /** Sicherheitsnetz fuer Browser ohne overflow:clip - ein verrutschter
        Scrollwert wuerde den Graphen versetzt zeichnen. */
    resetStageScroll() {
        const stage = document.querySelector('.ontology-stage');
        if (stage && (stage.scrollLeft || stage.scrollTop)) {
            stage.scrollLeft = 0;
            stage.scrollTop = 0;
        }
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
                leerInput.focus({ preventScroll: true });
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
                    <button type="button" id="entityCreateBtn" class="btn btn-primary">Anlegen</button>
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
        // Die anderen Typen bleiben offen - mehrere Gruppen nebeneinander
        // sind gewollt. Den Zieltyp als offen vermerken, sonst braeche
        // renderEntityNodes ab: es faechert nur fuer offene Typen auf.
        if (!this.expandedTypes.has(entity.type)) {
            this.expandedTypes.set(entity.type, null);
            typeNode.addClass('expanded');
        }
        this.applyFocus();
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
                </div>`;
            document.getElementById('entityBack').addEventListener('click', () => {
                const node = this.cy.getElementById(this.selectedType);
                this.onTypeSelect(this.selectedType, node.data('label'));
            });
            this.syncFilterNodes(entityId, filters);
            this.setDrawerDelete(() => this.confirmEntityDelete(
                entityId, data.entity.label, data.entity.type));
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
        if (!label) { if (input) input.focus({ preventScroll: true }); return; }
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
        input.focus({ preventScroll: true });
    }

    async onTypeCreate(label) {
        label = String(label || '').trim();
        if (!label) { document.getElementById('typeInput').focus({ preventScroll: true }); return; }
        try {
            const data = await this.postJson('/api/ontology/types', { label });
            if (!data) return;
            // Einfügen statt neu aufbauen: ein erneutes Kräftelayout würde
            // alle bestehenden Knoten verschieben.
            const node = this.addTypeToGraph(data.type);
            // Ohne Kamerafahrt: der Knoten liegt bereits im Bild, eine Fahrt
            // dorthin würde den Rest aus dem Blick schieben.
            if (node) this.onTypeTap(node, { fly: false });
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
            this.collapseType(typeId);
            this.closeDrawers();
            this.cy.getElementById(typeId).remove();   // Kanten gehen mit
            this.setDrawerDelete(null);
        } catch (err) {
            console.error('Cortex: Typ nicht löschbar', err);
        }
    }

    /** Neuen Typ in den bestehenden Graphen setzen, ohne das Layout erneut
        laufen zu lassen: freier Platz am Rand, Kamera bleibt stehen. */
    addTypeToGraph(type) {
        if (!this.cy || this.cy.getElementById(type.id).nonempty()) return null;
        const others = this.cy.nodes('[icon]');
        let position = { x: 0, y: 0 };
        if (others.length) {
            const bb = others.boundingBox();
            const radius = Math.max(bb.w, bb.h) / 2 + 60;   // nah am Netz bleiben
            const angle = others.length * 2.39996;      // goldener Winkel: streut
            position = { x: (bb.x1 + bb.x2) / 2 + radius * Math.cos(angle),
                         y: (bb.y1 + bb.y2) / 2 + radius * Math.sin(angle) };
        }
        const node = this.cy.add({
            group: 'nodes',
            data: { id: type.id, label: type.label, count: type.count || 0,
                    icon: iconsForTypes([type], cssToken('--primary-color'))[0],
                    size: 54 },
            position,
        });
        const p = node.renderedPosition();
        const outside = p.x < 60 || p.y < 60
            || p.x > this.cy.width() - 60 || p.y > this.cy.height() - 60;
        if (outside) {
            if (CortexApp.reducedMotion()) this.cy.fit(undefined, 60);
            else this.cy.animate({ fit: { padding: 60 } },
                                 { duration: 350, easing: 'ease-out-quart' });
        }
        return node;
    }

    /** Nach einem Zug den Namen erfragen und die Verbindung anlegen. */
    onConnectDrawn({ srcId, dstId, ebene }) {
        const esc = CortexApp.esc;
        const vorschlaege = [...new Set(
            this.cy.edges().map((e) => e.data('predicate')).filter(Boolean))].sort();
        this.openEntityDrawer();
        this.setDrawerDelete(null);
        document.getElementById('entityPaneTitle').textContent = 'Neue Verbindung';
        const body = document.getElementById('entityPaneBody');
        const label = (id) => esc(this.cy.getElementById(id).data('label') || id);
        // Eigene Auswahl statt datalist: dessen Aufklapp-Dreieck stellt der
        // Browser selbst, es laesst sich weder zentrieren noch der Marke
        // anpassen. Die Chips gibt es im Haus bereits als tab-chip.
        const auswahl = vorschlaege.length ? `
                <p class="entity-hint">Bereits verwendet</p>
                <div class="tab-chips connect-vorschlaege">${vorschlaege
                    .map((v) => `<button type="button" class="tab-chip"
                                 data-wert="${esc(v)}">${esc(v)}</button>`).join('')}</div>` : '';
        body.innerHTML = `
            <div class="entity-detail">
                <p class="entity-hint">${label(srcId)} zu ${label(dstId)}</p>
                <div class="create-row">
                    <input type="text" id="connectInput" maxlength="80" autocomplete="off"
                           placeholder="Beziehung, z. B. hat Dossier"
                           aria-label="Art der Beziehung">
                    <button type="button" id="connectSubmit" class="btn btn-primary">Verbinden</button>
                </div>
                ${auswahl}
                <p class="ontology-empty" id="connectFehler" hidden></p>
            </div>`;
        const eingabe = document.getElementById('connectInput');
        const senden = () => this.onConnectSubmit(srcId, dstId, ebene, eingabe.value);
        document.getElementById('connectSubmit').addEventListener('click', senden);
        const chips = [...body.querySelectorAll('.connect-vorschlaege .tab-chip')];
        const markiere = () => chips.forEach((c) =>
            c.classList.toggle('active', c.dataset.wert === eingabe.value));
        chips.forEach((chip) => chip.addEventListener('click', () => {
            eingabe.value = chip.dataset.wert;
            markiere();
            eingabe.focus({ preventScroll: true });
        }));
        eingabe.addEventListener('input', markiere);
        eingabe.addEventListener('keydown', (evt) => {
            if (evt.key === 'Enter') { evt.preventDefault(); senden(); }
            if (evt.key === 'Escape') this.closeDrawers();
        });
        eingabe.focus({ preventScroll: true });
    }

    async onConnectSubmit(srcId, dstId, ebene, predicate) {
        predicate = String(predicate || '').trim();
        const fehler = document.getElementById('connectFehler');
        if (!predicate) { document.getElementById('connectInput').focus({ preventScroll: true }); return; }
        const url = ebene === 'typ' ? '/api/ontology/type-relations'
                                    : '/api/ontology/relations';
        // Satelliten tragen die Entitaets-Id im Datenfeld, nicht in der Knoten-Id.
        const kennung = (id) => {
            const n = this.cy.getElementById(id);
            return n.data('entityId') || id;
        };
        try {
            const data = await this.postJson(url, {
                src: kennung(srcId), predicate, dst: kennung(dstId) });
            if (!data) return;
            // Der Server gibt bei gleichem Tripel den bestehenden Eintrag
            // zurueck. Traegt der bereits eine Anzahl, ist es keine Vorgabe
            // mehr, sondern eine verdichtete Linie.
            const anzahl = (data.relation && Number(data.relation.count)) || 0;
            this.addRelationToGraph(srcId, dstId, predicate, ebene, anzahl);
            // Bei Entitaeten NICHT die Drawer schliessen: closeDrawers klappt
            // den Typ ein und entfernt dabei die Satelliten samt der gerade
            // gezeichneten Kante. Stattdessen das Detail der Quelle neu
            // zeigen, der Typ bleibt aufgeklappt und die Linie sichtbar.
            if (ebene === 'entitaet') await this.onEntitySelect(kennung(srcId));
            else this.closeDrawers();
        } catch (err) {
            console.error('Cortex: Verbindung nicht anlegbar', err);
            if (fehler) {
                fehler.textContent = 'Verbindung konnte nicht angelegt werden.';
                fehler.hidden = false;
            }
        }
    }

    /** Gibt es schon eine Linie mit denselben Enden und demselben Namen?
        Ueber die Datenfelder, nicht ueber die Id: dieselbe Verbindung kann
        aus dem Aufbau (r-3) oder aus einem Zug (neu:...) stammen. */
    hasRelationEdge(srcId, dstId, predicate) {
        return this.cy.edges().some((e) => e.data('src') === srcId
            && e.data('dst') === dstId && e.data('predicate') === predicate);
    }

    /** Linie einfuegen, ohne den Graphen neu aufzubauen.
        anzahl > 0 heisst: die Typ-Linie fasst bereits echte Verbindungen
        zusammen. Dann ist sie keine Vorgabe, sondern verdichtet - ohne
        Klasse, mit Anzahl in der Beschriftung. */
    addRelationToGraph(srcId, dstId, predicate, ebene, anzahl = 0) {
        if (this.hasRelationEdge(srcId, dstId, predicate)) return;
        const id = `neu:${srcId}:${predicate}:${dstId}`;
        if (this.cy.getElementById(id).nonempty()) return;
        const verdichtet = ebene === 'typ' && anzahl > 0;
        let klasse = 'observed-relation';
        if (ebene === 'typ') klasse = verdichtet ? '' : 'declared';
        this.cy.add({
            group: 'edges',
            classes: klasse,
            data: { id, source: srcId, target: dstId, src: srcId, dst: dstId,
                    predicate,
                    label: verdichtet ? `${predicate} (${formatCount(anzahl)})`
                                      : predicate,
                    width: 1.5 },
        });
    }

    /** Eine Linie zeigen. Drei Arten: Vorgabe und echte Verbindung sind
        einzeln loeschbar; eine verdichtete Typ-Linie fasst mehrere echte
        Verbindungen zusammen und bietet deshalb keinen Loeschknopf an -
        einzelne Verbindungen entfernt man im Detail der jeweiligen Entitaet. */
    onEdgeSelect(edge) {
        const esc = CortexApp.esc;
        const predicate = edge.data('predicate') || '';
        const vorgabe = edge.hasClass('declared');
        const verbindung = edge.hasClass('observed-relation');
        const verdichtet = !vorgabe && !verbindung;
        const quelle = this.cy.getElementById(edge.data('src'));
        const ziel = this.cy.getElementById(edge.data('dst'));
        this.openEntityDrawer();
        this.setDrawerDelete(null);
        document.getElementById('entityPaneTitle').textContent =
            vorgabe ? 'Vorgabe' : (verdichtet ? 'Verbindungen' : 'Verbindung');
        const hinweis = `${esc(quelle.data('label') || '')}
                   zu ${esc(ziel.data('label') || '')}`;
        if (verdichtet) {
            // Die Anzahl steckt nicht in einem eigenen Datenfeld, nur in der
            // Beschriftung ("Praedikat (1'234)"). Laesst sie sich nicht
            // sauber herausloesen, bleibt sie schlicht weg statt geraten.
            const treffer = /\(([\d']+)\)\s*$/.exec(edge.data('label') || '');
            const anzahl = treffer ? treffer[1] : null;
            document.getElementById('entityPaneBody').innerHTML = `
                <div class="entity-detail">
                    <h3>${esc(predicate)}</h3>
                    <p class="entity-hint">${hinweis}</p>
                    <p class="confirm-detail">${anzahl
                        ? `Diese Linie fasst ${anzahl} Verbindungen zwischen einzelnen Entitäten zusammen.`
                        : 'Diese Linie fasst mehrere Verbindungen zwischen einzelnen Entitäten zusammen.'}
                        Einzelne Verbindungen entfernen Sie im Detail der jeweiligen Entität.</p>
                </div>`;
            return;
        }
        document.getElementById('entityPaneBody').innerHTML = `
            <div class="entity-detail">
                <h3>${esc(predicate)}</h3>
                <p class="entity-hint">${hinweis}</p>
                <p class="confirm-detail">${vorgabe
                    ? 'Eine Vorgabe beschreibt, was vorgesehen ist. Sie bleibt sichtbar, solange keine Verbindung dieser Art besteht.'
                    : 'Eine gezogene Verbindung zwischen zwei Entitäten.'}</p>
                <div class="confirm-actions">
                    <button type="button" id="edgeDelete" class="btn btn-danger">Löschen</button>
                </div>
            </div>`;
        document.getElementById('edgeDelete').addEventListener('click', () => {
            this.askDelete({
                title: `${predicate} löschen?`,
                detail: vorgabe
                    ? 'Die Vorgabe wird entfernt. Bestehende Verbindungen bleiben.'
                    : 'Die Verbindung zwischen den beiden Entitäten wird entfernt.',
                onConfirm: () => this.onEdgeDelete(edge, vorgabe),
                onCancel: () => this.onEdgeSelect(edge),
            });
        });
    }

    async onEdgeDelete(edge, vorgabe) {
        const url = vorgabe ? '/api/ontology/type-relations'
                            : '/api/ontology/relations';
        const kennung = (id) => {
            const n = this.cy.getElementById(id);
            return n.data('entityId') || id;
        };
        try {
            const resp = await fetch(url, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json',
                           'X-CSRF-Token': csrfToken() },
                body: JSON.stringify({ src: kennung(edge.data('src')),
                                       predicate: edge.data('predicate'),
                                       dst: kennung(edge.data('dst')) }),
            });
            if (resp.status === 401) { window.location.assign('/login'); return; }
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            edge.remove();
            this.closeDrawers();
        } catch (err) {
            console.error('Cortex: Verbindung nicht löschbar', err);
            // Ohne Rueckmeldung bliebe das Bestaetigungsblatt stehen und
            // nichts geschaehe - im selben Stil wie beim Anlegen.
            this.showDrawerError(vorgabe
                ? 'Vorgabe konnte nicht gelöscht werden.'
                : 'Verbindung konnte nicht gelöscht werden.');
        }
    }

    /** Sichtbarer Hinweis im Drawer, unterhalb des aktuellen Inhalts. */
    showDrawerError(text) {
        const body = document.getElementById('entityPaneBody');
        if (!body) return;
        let hinweis = document.getElementById('drawerFehler');
        if (!hinweis) {
            hinweis = document.createElement('p');
            hinweis.id = 'drawerFehler';
            hinweis.className = 'ontology-empty';
            (body.querySelector('.entity-detail') || body).appendChild(hinweis);
        }
        hinweis.textContent = text;      // Fremdtext bleibt aussen vor
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
        if (!label) { if (input) input.focus({ preventScroll: true }); return; }
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
