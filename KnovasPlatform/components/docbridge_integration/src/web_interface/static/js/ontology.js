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
        // Task 4: Graph laden + rendern
    }
}

document.addEventListener('DOMContentLoaded', () => { new WissensnetzApp(); });
