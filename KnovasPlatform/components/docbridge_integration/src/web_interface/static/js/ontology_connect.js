// Zieh-Geste fuer Verbindungen im Cortex-Graphen.
// Kennt nur Cytoscape und meldet einen fertigen Zug per Rueckruf; was daraus
// entsteht, entscheidet CortexApp. Bewusst eigene Datei, weil ontology.js
// bereits gross ist.
'use strict';

/** Ebene eines Knotens: Typen tragen ein Symbol, Entitaeten die Klasse entity. */
function nodeEbene(node) {
    if (node.hasClass('entity')) return 'entitaet';
    if (node.hasClass('filter-node')) return null;      // Filter verbinden nicht
    return node.data('icon') ? 'typ' : null;
}

class ConnectGesture {
    constructor(cy, { onConnect }) {
        this.cy = cy;
        this.onConnect = onConnect;
        this.quelle = null;
        this.griff = null;
        this._bind();
    }

    _bind() {
        this._onOver = (evt) => this._zeigeGriff(evt.target);
        this._onOut = (evt) => {
            if (evt.originalEvent && evt.originalEvent.relatedTarget === this.griff) {
                return;
            }
            if (evt.originalEvent && this.griff && this.griff.contains(evt.originalEvent.relatedTarget)) {
                return;
            }
            this._versteckeGriff();
        };
        this._onPan = () => this._versteckeGriff();
        this.cy.on('mouseover', 'node', this._onOver);
        this.cy.on('mouseout', 'node', this._onOut);
        this.cy.on('pan zoom', this._onPan);
    }

    _griffElement() {
        if (!this.griff) {
            this.griff = document.createElement('button');
            this.griff.type = 'button';
            this.griff.className = 'connect-handle';
            this.griff.setAttribute('aria-label', 'Verbindung ziehen');
            this._onGriffMouseDown = (e) => this._start(e);
            this._onGriffMouseLeave = () => {
                if (!this.quelle) this._versteckeGriff();
            };
            this.griff.addEventListener('mousedown', this._onGriffMouseDown);
            this.griff.addEventListener('mouseleave', this._onGriffMouseLeave);
            this.cy.container().parentElement.appendChild(this.griff);
        }
        return this.griff;
    }

    _zeigeGriff(node) {
        if (this.quelle) return;                    // waehrend eines Zuges nicht
        if (!nodeEbene(node)) { this._versteckeGriff(); return; }
        const p = node.renderedPosition();
        const radius = (node.renderedWidth() / 2) - 2;
        const griff = this._griffElement();
        griff.dataset.nodeId = node.id();
        griff.style.left = `${p.x + radius}px`;
        griff.style.top = `${p.y}px`;
        griff.hidden = false;
    }

    _versteckeGriff() {
        if (this.griff && !this.quelle) this.griff.hidden = true;
    }

    _start(event) {
        event.preventDefault();
        event.stopPropagation();
        const node = this.cy.getElementById(this.griff.dataset.nodeId);
        if (node.empty()) return;
        this.quelle = node;
        this.ebene = nodeEbene(node);
        this.cy.userPanningEnabled(false);
        this.cy.boxSelectionEnabled(false);

        // Vorschaulinie ueber einen unsichtbaren Zielknoten
        const p = node.position();
        this.zeiger = this.cy.add({
            group: 'nodes', classes: 'connect-pointer',
            data: { id: '__connect_pointer__' }, position: { x: p.x, y: p.y },
        });
        this.zeiger.ungrabify();
        this.vorschau = this.cy.add({
            group: 'edges', classes: 'connect-preview',
            data: { id: '__connect_preview__', source: node.id(),
                    target: '__connect_pointer__' },
        });

        this._onMove = (e) => this._bewege(e);
        this._onUp = (e) => this._beende(e);
        window.addEventListener('mousemove', this._onMove);
        window.addEventListener('mouseup', this._onUp, { once: true });
    }

    _modellPunkt(event) {
        const box = this.cy.container().getBoundingClientRect();
        const zoom = this.cy.zoom();
        const pan = this.cy.pan();
        return { x: (event.clientX - box.left - pan.x) / zoom,
                 y: (event.clientY - box.top - pan.y) / zoom };
    }

    _zielUnter(event) {
        const box = this.cy.container().getBoundingClientRect();
        const punkt = { x: event.clientX - box.left, y: event.clientY - box.top };
        let treffer = null;
        this.cy.nodes().forEach((n) => {
            if (n.id() === this.quelle.id() || n.id() === '__connect_pointer__') return;
            if (nodeEbene(n) !== this.ebene) return;
            const r = n.renderedPosition();
            const radius = n.renderedWidth() / 2;
            const d = Math.hypot(r.x - punkt.x, r.y - punkt.y);
            if (d <= radius) treffer = n;
        });
        return treffer;
    }

    _bewege(event) {
        if (!this.quelle) return;
        this.zeiger.position(this._modellPunkt(event));
        const ziel = this._zielUnter(event);
        this.cy.nodes('.connect-target').removeClass('connect-target');
        if (ziel) ziel.addClass('connect-target');
    }

    _beende(event) {
        const ziel = this._zielUnter(event);
        const quelle = this.quelle;
        const ebene = this.ebene;
        this._aufraeumen();
        if (ziel && this.onConnect) {
            this.onConnect({ srcId: quelle.id(), dstId: ziel.id(), ebene });
        }
    }

    _aufraeumen() {
        this.cy.nodes('.connect-target').removeClass('connect-target');
        if (this.vorschau) { this.vorschau.remove(); this.vorschau = null; }
        if (this.zeiger) { this.zeiger.remove(); this.zeiger = null; }
        if (this._onMove) {
            window.removeEventListener('mousemove', this._onMove);
            this._onMove = null;
        }
        if (this._onUp) {
            window.removeEventListener('mouseup', this._onUp);
            this._onUp = null;
        }
        this.quelle = null;
        this.ebene = null;
        this.cy.userPanningEnabled(true);
        this.cy.boxSelectionEnabled(true);
        this._versteckeGriff();
    }

    destroy() {
        this.cy.removeListener('mouseover', 'node', this._onOver);
        this.cy.removeListener('mouseout', 'node', this._onOut);
        this.cy.removeListener('pan zoom', this._onPan);
        this._aufraeumen();
        if (this.griff) {
            if (this._onGriffMouseDown) {
                this.griff.removeEventListener('mousedown', this._onGriffMouseDown);
            }
            if (this._onGriffMouseLeave) {
                this.griff.removeEventListener('mouseleave', this._onGriffMouseLeave);
            }
            this.griff.remove();
            this.griff = null;
        }
    }
}
