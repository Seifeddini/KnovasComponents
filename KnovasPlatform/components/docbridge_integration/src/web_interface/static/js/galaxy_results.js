// Structured Cortex search overlay. Version 1 stays with the heatmap in
// point_cloud_mockup.js; versions 2–4 draw orbit, constellation and dossier
// geometries over the same galaxy, including every document as a node.
'use strict';

(function () {
    const Search = window.CortexGalaxySearch;
    if (!Search) return;

    const version = Search.versionFromSearch(window.location.search);
    const resultsRoot = document.getElementById('galaxyResults');
    const rail = document.getElementById('galaxyResultsRail');
    const card = document.getElementById('galaxyDocumentCard');
    const versionsNav = document.getElementById('galaxyVersions');
    const examples = document.getElementById('galaxyExamples');
    const searchInput = document.getElementById('galaxySearchInput');
    const searchForm = document.getElementById('galaxySearch');

    let last = {
        version,
        query: '',
        mode: 'idle',
        hits: [],
        layout: { positions: {}, hidden: 0, groups: [] },
    };

    function stageBox() {
        const rect = resultsRoot && resultsRoot.getBoundingClientRect
            ? resultsRoot.getBoundingClientRect()
            : { left: 0, top: 0, width: 1280, height: 800 };
        const width = (resultsRoot && resultsRoot.clientWidth) || rect.width || 1280;
        const height = (resultsRoot && resultsRoot.clientHeight) || rect.height || 800;
        const railWidth = (rail && !rail.hidden && rail.offsetWidth) || 0;
        const left = Math.max(28, railWidth ? railWidth + 16 : 28);
        const gutter = 150;
        const rx = Math.max(100, (width - left - gutter - 36) / 2);
        return {
            left,
            top: 72,
            width: Math.max(240, width - left - 28),
            height: Math.max(180, height - 56),
            cx: left + gutter + rx,
            cy: height * 0.48,
            rx,
            ry: Math.max(90, height * 0.28),
            maxNodes: 40,
        };
    }

    function indexNow() {
        const galaxy = window.cortexGalaxy || { systems: [], documents: [] };
        return Search.buildIndex(galaxy.systems || [], galaxy.documents || []);
    }

    function layoutFor(result) {
        const box = stageBox();
        if (version === 2) {
            const layout = Search.orbitLayout(result.hits, box);
            return Object.assign({ groups: layout.sectors }, layout);
        }
        if (version === 4) {
            const layout = Search.dossierLayout(indexNow(), result.hits, box);
            return Object.assign({ groups: layout.columns }, layout);
        }
        const layout = Search.constellationLayout(result.hits, box);
        return layout;
    }

    function setActive(on) {
        if (document.body && document.body.classList) {
            document.body.classList.toggle('galaxy-results-active', Boolean(on));
            document.body.classList.toggle('galaxy-searching', Boolean(on) && version === 1);
            document.body.classList.remove('galaxy-view-2', 'galaxy-view-3', 'galaxy-view-4');
            if (on && version > 1) document.body.classList.add(`galaxy-view-${version}`);
        }
        if (resultsRoot) resultsRoot.hidden = !on;
        if (rail) rail.hidden = !on || version === 4;
        if (on && window.cortexGalaxy && window.cortexGalaxy.redraw) {
            window.cortexGalaxy.redraw();
        }
    }

    function closeCard() {
        if (!card) return;
        card.hidden = true;
        card.replaceChildren();
    }

    function showDocument(hit) {
        if (!card) return;
        const node = hit.node;
        card.hidden = false;
        card.replaceChildren();
        const kicker = document.createElement('span');
        kicker.className = 'galaxy-doc-kicker';
        kicker.textContent = node.kind === 'document' ? 'Dokument' : node.type;
        const title = document.createElement('strong');
        title.textContent = node.label;
        const meta = document.createElement('p');
        meta.textContent = node.meta || (node.matters || []).join(' · ') || node.type;
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'galaxy-doc-close';
        close.setAttribute('aria-label', 'Dokument schliessen');
        close.textContent = '×';
        close.addEventListener('click', closeCard);
        card.append(close, kicker, title, meta);
        if ((node.matters || []).length) {
            const matters = document.createElement('p');
            matters.className = 'galaxy-doc-matters';
            matters.textContent = node.matters.join(' · ');
            card.appendChild(matters);
        }
    }

    function syncUrl(query) {
        if (!window.history || !window.history.replaceState) return;
        const params = new URLSearchParams();
        if (version !== 1) params.set('version', String(version));
        if (query) params.set('q', query);
        const next = params.toString();
        window.history.replaceState(
            null,
            '',
            next ? `${window.location.pathname}?${next}` : window.location.pathname,
        );
    }

    function screenMap() {
        return (window.cortexGalaxy && window.cortexGalaxy.screenPositions) || {};
    }

    function systemAnchorKey(hit) {
        if (hit.node.kind === 'entity') return `system:${hit.node.type}`;
        const link = (hit.node.links && hit.node.links[0]) || '';
        return `system:${link.split('::')[0]}`;
    }

    function pointFor(hit, layout) {
        if (version === 3) {
            const live = screenMap()[hit.key];
            if (live) return live;
        }
        return layout.positions[hit.key];
    }

    function markFor(hit) {
        const mark = document.createElement('span');
        mark.className = hit.node.kind === 'document' ? 'galaxy-sheet' : 'galaxy-orb';
        mark.setAttribute('aria-hidden', 'true');
        return mark;
    }

    function placeCaption(caption, point, originX, originY) {
        const dx = point.x - originX;
        const dy = point.y - originY;
        const len = Math.hypot(dx, dy) || 1;
        const ux = dx / len;
        const uy = dy / len;
        const clearOfRail = !rail || rail.hidden ? 16 : rail.offsetWidth + 12;
        const wouldCrossRail = ux < 0 && point.x - 150 < clearOfRail;
        const roomRight = ((resultsRoot && resultsRoot.clientWidth) || 1280) - point.x;
        if (wouldCrossRail || (ux > 0 && roomRight < 150)) {
            caption.style.left = '0';
            caption.style.top = '-16px';
            caption.style.transform = 'translate(-50%, -100%)';
            return;
        }
        caption.style.left = `${ux * 18}px`;
        caption.style.top = `${uy * 18}px`;
        if (Math.abs(ux) > Math.abs(uy)) {
            caption.style.transform = ux < 0
                ? 'translate(-100%, -50%)'
                : 'translate(0, -50%)';
        } else {
            caption.style.transform = uy < 0
                ? 'translate(-50%, -100%)'
                : 'translate(-50%, 0)';
        }
    }

    function placeNode(hit, point, originX, originY) {
        const button = document.createElement('button');
        button.type = 'button';
        const mandate = hit.node.type === 'Mandate' ? ' is-mandate' : '';
        button.className = `galaxy-result-node is-${hit.node.kind}${mandate} tier-${hit.tier}`;
        button.style.left = `${point.x}px`;
        button.style.top = `${point.y}px`;
        button.dataset.key = hit.key;
        button.dataset.system = systemAnchorKey(hit).slice('system:'.length);
        button.setAttribute('aria-label', `${hit.node.label}, ${hit.node.type}`);
        const caption = document.createElement('span');
        caption.className = 'galaxy-result-caption';
        caption.textContent = hit.node.label;
        placeCaption(caption, point, originX, originY);
        button.append(markFor(hit), caption);
        button.addEventListener('click', () => showDocument(hit));
        return button;
    }

    function paintDossiers(layout) {
        const board = document.createElement('div');
        board.className = 'galaxy-dossier-board';
        (layout.columns || []).forEach((column) => {
            const sheet = document.createElement('section');
            sheet.className = 'galaxy-dossier';
            const heading = document.createElement('h3');
            heading.textContent = column.matter || 'Ohne Mandat';
            sheet.appendChild(heading);
            column.hits.forEach((hit) => {
                const button = document.createElement('button');
                button.type = 'button';
                const mandate = hit.node.type === 'Mandate' ? ' is-mandate' : '';
                button.className = `is-${hit.node.kind}${mandate}`;
                button.setAttribute('aria-label', `${hit.node.label}, ${hit.node.type}`);
                const name = document.createElement('span');
                name.textContent = hit.node.label;
                const kind = document.createElement('em');
                kind.textContent = hit.node.kind === 'document' ? 'Dokument' : hit.node.type;
                button.append(markFor(hit), name, kind);
                button.addEventListener('click', () => showDocument(hit));
                sheet.appendChild(button);
            });
            board.appendChild(sheet);
        });
        resultsRoot.appendChild(board);
    }

    function paintOverlay(result, layout) {
        if (!resultsRoot) return;
        resultsRoot.replaceChildren();
        if (result.mode === 'idle' || !result.hits.length) return;
        if (version === 4) {
            paintDossiers(layout);
            return;
        }

        const box = stageBox();
        if (version === 2) {
            const svgNS = 'http://www.w3.org/2000/svg';
            const make = (tag) => (typeof document.createElementNS === 'function'
                ? document.createElementNS(svgNS, tag)
                : document.createElement(tag));
            const svg = make('svg');
            const svgWidth = (resultsRoot && resultsRoot.clientWidth) || 1280;
            const svgHeight = (resultsRoot && resultsRoot.clientHeight) || 800;
            svg.setAttribute('class', 'galaxy-results-lines');
            svg.setAttribute('aria-hidden', 'true');
            svg.setAttribute('width', String(svgWidth));
            svg.setAttribute('height', String(svgHeight));
            svg.setAttribute('viewBox', `0 0 ${svgWidth} ${svgHeight}`);
            [0.7, 0.82, 0.94].forEach((ring) => {
                const ellipse = make('ellipse');
                ellipse.setAttribute('cx', String(box.cx));
                ellipse.setAttribute('cy', String(box.cy));
                ellipse.setAttribute('rx', String(box.rx * ring));
                ellipse.setAttribute('ry', String(box.ry * ring));
                ellipse.setAttribute('class', 'galaxy-orbit-ring');
                svg.appendChild(ellipse);
            });
            resultsRoot.appendChild(svg);
        }

        const screen = screenMap();
        result.hits.forEach((hit) => {
            const point = pointFor(hit, layout);
            if (!point) return;
            const anchor = screen[systemAnchorKey(hit)];
            const originX = version === 3 && anchor ? anchor.x : box.cx;
            const originY = version === 3 && anchor ? anchor.y : box.cy;
            resultsRoot.appendChild(placeNode(hit, point, originX, originY));
        });
    }

    function syncPositions() {
        if (version !== 3 || !resultsRoot) return;
        const screen = screenMap();
        resultsRoot.querySelectorAll('.galaxy-result-node').forEach((button) => {
            const point = screen[button.dataset.key];
            if (!point) return;
            button.style.left = `${point.x}px`;
            button.style.top = `${point.y}px`;
            const anchor = screen[`system:${button.dataset.system}`];
            const caption = button.querySelector('.galaxy-result-caption');
            if (anchor && caption) placeCaption(caption, point, anchor.x, anchor.y);
        });
    }

    function paintRail(result, layout) {
        if (!rail) return;
        rail.replaceChildren();
        if (result.mode === 'idle') return;
        const heading = document.createElement('h2');
        heading.textContent = result.mode === 'none'
            ? 'Keine Treffer im Graphen'
            : `${result.hits.length} Treffer`;
        rail.appendChild(heading);
        if (layout.hidden) {
            const note = document.createElement('p');
            note.className = 'galaxy-results-hidden';
            note.textContent = `${layout.hidden} weitere ausserhalb der Ansicht`;
            rail.appendChild(note);
        }
        const list = document.createElement('ol');
        result.hits.slice(0, 18).forEach((hit) => {
            const item = document.createElement('li');
            const button = document.createElement('button');
            button.type = 'button';
            button.className = hit.node.kind === 'document'
                ? 'galaxy-rail-doc'
                : 'galaxy-rail-entity';
            const type = document.createElement('span');
            type.textContent = hit.node.type;
            const label = document.createElement('strong');
            label.textContent = hit.node.label;
            button.append(type, label);
            button.addEventListener('click', () => showDocument(hit));
            item.appendChild(button);
            list.appendChild(item);
        });
        rail.appendChild(list);
    }

    function snapshot() {
        if (version === 1) return { version: 1 };
        const positions = last.layout.positions || {};
        return {
            version,
            query: last.query,
            mode: last.mode,
            hits: last.hits.length,
            positioned: Object.keys(positions).length,
            groups: last.layout.groups || last.layout.sectors || last.layout.columns,
        };
    }

    function run(query) {
        const value = String(query || '');
        closeCard();
        if (version === 1) {
            last = { version, query: value, mode: value.trim() ? 'all' : 'idle',
                hits: [], layout: { positions: {}, hidden: 0, groups: [] } };
            return snapshot();
        }
        const result = Search.search(indexNow(), value);
        const show = result.mode !== 'idle' && result.hits.length > 0;
        setActive(show || result.mode === 'none');
        const layout = result.mode === 'idle' || result.mode === 'none'
            ? { positions: {}, hidden: 0, groups: [] }
            : layoutFor(result);
        last = {
            version,
            query: result.query,
            mode: result.mode,
            hits: result.hits,
            layout,
        };
        if (result.mode === 'idle' || !result.hits.length) {
            if (resultsRoot) resultsRoot.replaceChildren();
            if (rail) {
                rail.replaceChildren();
                if (result.mode === 'none') {
                    setActive(true);
                    paintRail(result, layout);
                }
            }
        } else {
            paintOverlay(result, layout);
            paintRail(result, layout);
        }
        return snapshot();
    }

    function currentQuery() {
        return searchInput ? searchInput.value : last.query;
    }

    function onQuery(query, writeUrl) {
        run(query);
        if (writeUrl !== false) syncUrl(String(query || '').trim());
    }

    function decorateVersions() {
        if (!versionsNav) return;
        const q = currentQuery().trim();
        [...versionsNav.querySelectorAll('[data-version]')].forEach((link) => {
            const target = link.getAttribute('data-version');
            const params = new URLSearchParams();
            if (target && target !== '1') params.set('version', target);
            if (q) params.set('q', q);
            const href = params.toString()
                ? `${window.location.pathname}?${params}`
                : window.location.pathname;
            link.setAttribute('href', href);
            link.classList.toggle('active', Number(target) === version);
        });
    }

    function decorateExamples() {
        if (!examples) return;
        [...examples.querySelectorAll('[data-demo]')].forEach((link) => {
            const query = link.getAttribute('data-demo') || '';
            const view = link.getAttribute('data-demo-version') || String(version);
            const params = new URLSearchParams();
            if (view !== '1') params.set('version', view);
            if (query) params.set('q', query);
            link.setAttribute('href', `${window.location.pathname}?${params}`);
        });
    }

    if (searchInput) {
        searchInput.addEventListener('input', () => {
            if (version === 1) return;
            onQuery(searchInput.value);
            decorateVersions();
        });
    }
    if (searchForm) {
        searchForm.addEventListener('submit', (event) => {
            if (version === 1) return;
            event.preventDefault();
            onQuery(searchInput ? searchInput.value : '', true);
        });
    }
    window.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || version === 1) return;
        if (card && !card.hidden) {
            closeCard();
            return;
        }
        if (searchInput && searchInput.value) {
            searchInput.value = '';
            onQuery('');
        }
    });
    window.addEventListener('resize', () => {
        if (version !== 1 && last.mode !== 'idle') run(last.query);
    });

    decorateVersions();
    decorateExamples();

    const initial = Search.queryFromSearch(window.location.search);
    if (searchInput && initial && !searchInput.value) searchInput.value = initial;
    if (version !== 1) onQuery(searchInput ? searchInput.value : initial, false);
    else if (initial && searchInput) searchInput.value = initial;

    window.cortexGalaxyResults = { snapshot, run, version, syncPositions };
}());
