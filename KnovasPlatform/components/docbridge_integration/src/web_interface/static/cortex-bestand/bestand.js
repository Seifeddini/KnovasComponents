/* Layered overview of the Cortex type summary.
   Types with a similar number of entries share a layer. Clicking a type
   focuses it and opens its entries as the next layer. The circle layout
   remains available as the previous view. */
(function () {
    const summary = window.CORTEX_SUMMARY;
    const types = (summary && summary.types) || [];
    const named = window.CORTEX_EXAMPLES || {};
    const populated = types.filter((type) => type.count > 0);
    const empty = types.filter((type) => !type.count);
    const total = populated.reduce((sum, type) => sum + type.count, 0);
    const maxCount = populated.reduce((max, type) => Math.max(max, type.count), 1);
    const byId = new Map(types.map((type) => [type.id, type]));

    const stage = document.getElementById('layerStage');
    const world = document.getElementById('layerWorld');
    const lead = document.getElementById('bestandLead');
    const search = document.getElementById('bestandSearch');
    const back = document.getElementById('layerBack');
    const reading = document.getElementById('bestandReading');
    const oldView = document.getElementById('bestandOld');
    const oldNote = document.getElementById('bestandOldNote');
    const graphHost = document.getElementById('oldGraph');

    const WIDTH = 1180;
    const LEFT = 176;
    const bands = [
        { id: 'thousand', title: "1'000 und mehr", min: 1000, radius: 34 },
        { id: 'hundred', title: '100 bis 999', min: 100, radius: 24 },
        { id: 'ten', title: '10 bis 99', min: 10, radius: 16 },
        { id: 'one', title: '1 bis 9', min: 1, radius: 12 },
        { id: 'none', title: 'Ohne Einträge', min: 0, radius: 5 },
    ];

    const statedPairs = [
        ['FeaturePlan', 'AlloyCheck'],
        ['Persona', 'AlloyCheck'],
        ['Capability', 'AlloyCheck'],
    ];
    const learnedLayers = [
        { title: 'Im Fokus', capacity: 3, radius: 32 },
        { title: 'Danach', capacity: 5, radius: 22 },
        { title: 'Weiter hinten', capacity: 8, radius: 16 },
        { title: 'Rand', capacity: 20, radius: 12 },
    ];

    let focus = null;
    let selectedChild = null;
    let query = '';
    let cameraMode = 'overview';
    let oldGraph = null;
    let objective = 'learned';
    const clickWeight = new Map();
    const viewStoreKey = 'cortex-schicht-views';
    let pickerQuery = '';
    let views = [{ id: 'all', name: 'Alle', ids: null }];
    let activeViewId = 'all';

    function formatCount(n) {
        return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, "'");
    }

    function fold(value) {
        return String(value || '')
            .toLocaleLowerCase('de')
            .replace(/ä/g, 'a')
            .replace(/ö/g, 'o')
            .replace(/ü/g, 'u')
            .replace(/ß/g, 'ss');
    }

    function bandOf(count) {
        return bands.find((band) => count >= band.min) || bands[bands.length - 1];
    }

    function typeByLabel(label) {
        return populated.find((type) => type.label === label) || null;
    }

    /* Prior is log(1 + count): volume, which is why AlloyCheck owns the top.
       A click or a stated pair is one Bradley-Terry observation
       P(i beats j) = sigmoid(theta_i - theta_j). Gradient steps on those
       pairs are the whole learner. Layers are then the greedy packing of
       that score into fixed capacities, which is optimal once the order is
       the score and a layer may hold only so many labels. */
    function priorScores() {
        const score = new Map();
        types.forEach((type) => score.set(type.id, Math.log1p(type.count)));
        return score;
    }

    function learn(base, pairs, steps, rate) {
        const score = new Map(base);
        for (let step = 0; step < steps; step += 1) {
            const delta = new Map();
            const hits = new Map();
            pairs.forEach(([win, lose]) => {
                const winner = typeByLabel(win);
                const loser = typeByLabel(lose);
                if (!winner || !loser) return;
                const gap = score.get(winner.id) - score.get(loser.id);
                const prob = 1 / (1 + Math.exp(-gap));
                const pull = rate * (1 - prob);
                delta.set(winner.id, (delta.get(winner.id) || 0) + pull);
                delta.set(loser.id, (delta.get(loser.id) || 0) - pull);
                hits.set(loser.id, (hits.get(loser.id) || 0) + 1);
            });
            delta.forEach((value, id) => {
                score.set(id, score.get(id) + value / (hits.get(id) || 1));
            });
        }
        return score;
    }

    const learnedBase = learn(priorScores(), statedPairs, 40, 0.8);

    function activeScores() {
        const score = objective === 'volume' ? priorScores() : new Map(learnedBase);
        if (objective === 'learned') {
            clickWeight.forEach((extra, id) => score.set(id, (score.get(id) || 0) + extra));
        }
        return score;
    }

    function linkEdges(nodes) {
        const at = new Map();
        nodes.forEach((node) => {
            if (node.type) at.set(node.type.id, node);
        });
        const lines = [];
        (summary.relations || []).forEach((relation) => {
            const from = at.get(relation.src);
            const to = at.get(relation.dst);
            if (from && to) lines.push([from, to, relation.predicate || '']);
        });
        return lines;
    }

    function entries(type) {
        return named[type.id] || type.examples || [];
    }

    function matches(type) {
        if (!query) return true;
        const hay = fold(`${type.label} ${type.id} ${entries(type).join(' ')}`);
        return hay.includes(query);
    }

    function shownTypes() {
        const view = views.find((item) => item.id === activeViewId);
        if (!view || view.ids == null) return types;
        const allow = new Set(view.ids);
        return types.filter((type) => allow.has(type.id));
    }

    function members(band) {
        return shownTypes()
            .filter((type) => bandOf(type.count).id === band.id)
            .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, 'de'));
    }

    function leadText() {
        if (selectedChild) return selectedChild;
        if (focus && focus.mode === 'type') {
            const type = byId.get(focus.id);
            const noun = type.count === 1 ? 'Eintrag' : 'Einträge';
            return `${type.label}, ${formatCount(type.count)} ${noun}`;
        }
        if (focus && focus.mode === 'empty') {
            const type = byId.get(focus.id);
            return `${type.label}, keine Einträge`;
        }
        if (query) {
            const n = shownTypes().filter(matches).length;
            return n === 1 ? '1 Typ' : `${n} Typen`;
        }
        const view = views.find((item) => item.id === activeViewId);
        const viewNote = view && view.ids
            ? `${view.name}, ${view.ids.length} ${view.ids.length === 1 ? 'Knoten' : 'Knoten'}. `
            : '';
        const edges = (summary.relations || []).length;
        const edgeNote = edges
            ? `${edges} ${edges === 1 ? 'Kante' : 'Kanten'} zwischen Typen.`
            : 'Im Export ist keine Kante zwischen Typen angekommen.';
        if (objective === 'learned') {
            return `${viewNote}Gelernt: FeaturePlan, Persona und Capability vor AlloyCheck. ${edgeNote}`;
        }
        return `${viewNote}Gruppiert nach Anzahl der Einträge. ${edgeNote}`;
    }

    function placeRow(items, y, radiusOf, labeled) {
        const usable = WIDTH - LEFT - 36;
        const n = items.length;
        const cols = n > 14 ? 7 : (n > 8 ? Math.ceil(n / 2) : n);
        const rows = Math.ceil(n / Math.max(cols, 1));
        const step = cols <= 1 ? 0 : Math.min(210, usable / (cols - 1));
        const size = (type) => (typeof radiusOf === 'function' ? radiusOf(type) : radiusOf);
        const maxR = Math.max(...items.map(size));
        const pitch = maxR * 2 + 58;
        const placed = [];
        items.forEach((type, index) => {
            const col = index % cols;
            const row = Math.floor(index / cols);
            const rowCount = Math.min(cols, n - row * cols);
            const rowWidth = step * Math.max(rowCount - 1, 0);
            const x0 = LEFT + (usable - rowWidth) / 2;
            placed.push({
                key: type.id,
                type,
                x: x0 + col * step,
                y: y + row * pitch,
                r: size(type),
                labeled,
                kind: 'type',
            });
        });
        return { placed, height: rows * pitch };
    }

    function layoutOverview() {
        const nodes = [];
        const captions = [];
        const guides = [];
        let y = 78;
        bands.forEach((band) => {
            const group = members(band);
            if (!group.length) return;
            if (band.id === 'none') {
                const cols = 28;
                const gap = 18;
                const gridWidth = (Math.min(cols, group.length) - 1) * gap;
                const x0 = LEFT + ((WIDTH - LEFT - 36) - gridWidth) / 2;
                const rows = Math.ceil(group.length / cols);
                group.forEach((type, index) => {
                    nodes.push({
                        key: type.id,
                        type,
                        x: x0 + (index % cols) * gap,
                        y: y + Math.floor(index / cols) * gap,
                        r: band.radius,
                        labeled: false,
                        kind: 'empty',
                    });
                });
                captions.push({ title: band.title, x: 20, y: y + ((rows - 1) * gap) / 2 });
                guides.push({ y: y + ((rows - 1) * gap) / 2 });
                y += rows * gap + 72;
                return;
            }
            const { placed, height } = placeRow(group, y, band.radius, true);
            nodes.push(...placed);
            captions.push({ title: band.title, x: 20, y });
            guides.push({ y });
            y += height + 36;
        });
        return { nodes, captions, guides, edges: linkEdges(nodes), note: null };
    }

    function layoutLearned() {
        const score = activeScores();
        const visible = shownTypes();
        const ranked = visible.filter((type) => type.count > 0).sort((a, b) => (
            (score.get(b.id) - score.get(a.id))
            || a.label.localeCompare(b.label, 'de')
        ));
        const nodes = [];
        const captions = [];
        const guides = [];
        let y = 78;
        let cursor = 0;
        learnedLayers.forEach((layer) => {
            const group = ranked.slice(cursor, cursor + layer.capacity);
            cursor += group.length;
            if (!group.length) return;
            const vals = group.map((type) => score.get(type.id));
            const lo = Math.min(...vals);
            const hi = Math.max(...vals);
            const radiusOf = (type) => {
                const t = hi === lo ? 1 : (score.get(type.id) - lo) / (hi - lo);
                return layer.radius - 3 + t * 8;
            };
            const { placed, height } = placeRow(group, y, radiusOf, true);
            nodes.push(...placed);
            captions.push({ title: layer.title, x: 20, y });
            guides.push({ y });
            y += height + 36;
        });
        const quiet = visible.filter((type) => !type.count);
        if (quiet.length) {
            const cols = 28;
            const gap = 18;
            const gridWidth = (Math.min(cols, quiet.length) - 1) * gap;
            const x0 = LEFT + ((WIDTH - LEFT - 36) - gridWidth) / 2;
            const rows = Math.ceil(quiet.length / cols);
            quiet.forEach((type, index) => {
                nodes.push({
                    key: type.id,
                    type,
                    x: x0 + (index % cols) * gap,
                    y: y + Math.floor(index / cols) * gap,
                    r: 5,
                    labeled: false,
                    kind: 'empty',
                });
            });
            captions.push({ title: 'Ohne Einträge', x: 20, y: y + ((rows - 1) * gap) / 2 });
            guides.push({ y: y + ((rows - 1) * gap) / 2 });
        }
        return { nodes, captions, guides, edges: linkEdges(nodes), note: null };
    }

    function layoutType(type) {
        const labels = entries(type).slice(0, 24);
        const rest = Math.max(0, type.count - labels.length);
        const parent = {
            key: type.id,
            type,
            x: WIDTH / 2,
            y: 72,
            r: 36,
            labeled: true,
            kind: 'type',
            focus: true,
        };
        const nodes = [parent];
        const edges = [];
        const cols = Math.min(6, Math.max(labels.length, 1));
        const gapX = 168;
        const gapY = 92;
        labels.forEach((label, index) => {
            const row = Math.floor(index / cols);
            const rowCount = Math.min(cols, labels.length - row * cols);
            const col = index % cols;
            const rowWidth = gapX * Math.max(rowCount - 1, 0);
            const node = {
                key: `${type.id}:${index}`,
                label,
                x: parent.x - rowWidth / 2 + col * gapX,
                y: 210 + row * gapY,
                r: 11,
                labeled: true,
                kind: 'child',
            };
            nodes.push(node);
            edges.push([parent, node]);
        });
        const rows = labels.length ? Math.ceil(labels.length / cols) : 0;
        let note = null;
        if (rest > 0) {
            const dots = Math.min(rest, 72);
            const dotCols = Math.min(24, dots);
            const dotGap = 16;
            const startY = 210 + rows * gapY + 28;
            for (let index = 0; index < dots; index += 1) {
                const row = Math.floor(index / dotCols);
                const rowCount = Math.min(dotCols, dots - row * dotCols);
                const col = index % dotCols;
                const rowWidth = dotGap * Math.max(rowCount - 1, 0);
                nodes.push({
                    key: `mass:${index}`,
                    x: parent.x - rowWidth / 2 + col * dotGap,
                    y: startY + 22 + row * dotGap,
                    r: 4,
                    labeled: false,
                    kind: 'mass',
                });
            }
            note = {
                text: rest === 1 ? 'und ein weiterer' : `und ${formatCount(rest)} weitere`,
                x: parent.x,
                y: startY,
            };
        }
        return {
            nodes,
            captions: [],
            guides: [],
            edges,
            note,
        };
    }

    function layoutEmpty(highlightId) {
        const group = members(bands[bands.length - 1]);
        const cols = 7;
        const gapX = 156;
        const gapY = 62;
        const gridWidth = gapX * (Math.min(cols, group.length) - 1);
        const x0 = (WIDTH - gridWidth) / 2;
        const nodes = group.map((type, index) => ({
            key: type.id,
            type,
            x: x0 + (index % cols) * gapX,
            y: 64 + Math.floor(index / cols) * gapY,
            r: 8,
            labeled: true,
            kind: 'empty',
            focus: type.id === highlightId,
        }));
        return {
            nodes,
            captions: [{ title: 'Ohne Einträge', x: 20, y: 64 }],
            guides: [],
            edges: [],
            note: null,
        };
    }

    function scene() {
        if (focus && focus.mode === 'type') return layoutType(byId.get(focus.id));
        if (focus && focus.mode === 'empty') return layoutEmpty(focus.id);
        return objective === 'learned' ? layoutLearned() : layoutOverview();
    }

    function boundsOf(model) {
        const nodes = model.nodes;
        let minX = 0;
        let minY = 0;
        let maxX = WIDTH;
        let maxY = 80;
        nodes.forEach((node) => {
            const label = node.labeled ? 46 : 6;
            minX = Math.min(minX, node.x - node.r - (node.labeled ? 78 : 0));
            maxX = Math.max(maxX, node.x + node.r + (node.labeled ? 78 : 0));
            minY = Math.min(minY, node.y - node.r - 20);
            maxY = Math.max(maxY, node.y + node.r + label);
        });
        return { x: minX, y: minY, w: maxX - minX, h: maxY - minY + 16 };
    }

    function applyCamera(bounds) {
        const pad = 28;
        const width = stage.clientWidth;
        const height = stage.clientHeight;
        if (!width || !height) return;
        const scaleW = (width - pad * 2) / bounds.w;
        const scaleH = (height - pad * 2) / bounds.h;
        const scale = cameraMode === 'empty'
            ? Math.min(scaleW, 1.05)
            : Math.min(scaleW, scaleH, 1.45);
        const safe = Math.max(scale, 0.45);
        const scroll = cameraMode === 'empty' && bounds.h * safe > height - pad;
        stage.classList.toggle('is-scroll', scroll);
        const tx = (width - bounds.w * safe) / 2 - bounds.x * safe;
        const centered = (height - bounds.h * safe) / 2 - bounds.y * safe;
        const pinned = 48 - bounds.y * safe;
        const ty = scroll || cameraMode === 'overview'
            ? (scroll ? pad - bounds.y * safe : centered)
            : Math.min(centered, pinned);
        world.style.transform = `translate(${tx}px, ${ty}px) scale(${safe})`;
    }

    function el(tag, className) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        return node;
    }

    function render() {
        const model = scene();
        const bounds = boundsOf(model);
        world.replaceChildren();
        world.style.width = `${bounds.w}px`;
        world.style.height = `${bounds.h}px`;

        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'layer-edges');
        svg.setAttribute('width', String(bounds.w));
        svg.setAttribute('height', String(bounds.h));
        model.guides.forEach((guide) => {
            const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            line.setAttribute('x1', String(LEFT - 8));
            line.setAttribute('x2', String(WIDTH));
            line.setAttribute('y1', String(guide.y));
            line.setAttribute('y2', String(guide.y));
            line.setAttribute('class', 'layer-guide');
            svg.appendChild(line);
        });
        model.edges.forEach(([from, to, predicate]) => {
            if (predicate) {
                const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                const mx = (from.x + to.x) / 2;
                const my = (from.y + to.y) / 2;
                const dx = to.x - from.x;
                const dy = to.y - from.y;
                const len = Math.hypot(dx, dy) || 1;
                const lift = Math.min(70, len * 0.22);
                path.setAttribute('d', `M ${from.x} ${from.y} Q ${mx - (dy / len) * lift} ${my + (dx / len) * lift} ${to.x} ${to.y}`);
                path.setAttribute('class', 'layer-link');
                svg.appendChild(path);
                return;
            }
            const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            line.setAttribute('x1', String(from.x));
            line.setAttribute('y1', String(from.y));
            line.setAttribute('x2', String(to.x));
            line.setAttribute('y2', String(to.y));
            svg.appendChild(line);
        });
        world.appendChild(svg);

        model.captions.forEach((caption) => {
            const text = el('div', 'layer-caption');
            text.textContent = caption.title;
            text.style.left = `${caption.x}px`;
            text.style.top = `${caption.y}px`;
            world.appendChild(text);
        });

        if (model.note) {
            const note = el('div', 'layer-note');
            note.textContent = model.note.text;
            note.style.left = `${model.note.x}px`;
            note.style.top = `${model.note.y}px`;
            world.appendChild(note);
        }

        model.nodes.forEach((node) => {
            const item = el('div', 'layer-item');
            item.style.left = `${node.x}px`;
            item.style.top = `${node.y}px`;
            const type = node.type;
            const dim = query && type && !matches(type);
            if (dim) item.classList.add('is-dim');
            if (query && type && matches(type)) item.classList.add('is-match');
            if (node.focus) item.classList.add('is-focus');
            if (node.kind === 'child' && selectedChild === node.label) item.classList.add('is-selected');

            if (node.kind === 'mass') {
                const dot = el('span', 'layer-mass');
                dot.style.width = `${node.r * 2}px`;
                dot.style.height = `${node.r * 2}px`;
                item.appendChild(dot);
                world.appendChild(item);
                return;
            }

            const button = el('button', `layer-node layer-node-${node.kind}`);
            button.type = 'button';
            button.style.width = `${node.r * 2}px`;
            button.style.height = `${node.r * 2}px`;
            button.dataset.id = node.key;
            if (type) {
                const noun = type.count === 1 ? 'Eintrag' : 'Einträge';
                button.setAttribute('aria-label', type.count
                    ? `${type.label}, ${formatCount(type.count)} ${noun}`
                    : `${type.label}, keine Einträge`);
                button.addEventListener('click', (event) => {
                    event.stopPropagation();
                    selectedChild = null;
                    if (objective === 'learned' && type.count > 0) {
                        clickWeight.set(type.id, (clickWeight.get(type.id) || 0) + 0.45);
                    }
                    if (type.count > 0) focus = { mode: 'type', id: type.id };
                    else focus = { mode: 'empty', id: type.id };
                    cameraMode = focus.mode === 'empty' ? 'empty' : 'focus';
                    render();
                });
            } else {
                button.setAttribute('aria-label', node.label);
                button.addEventListener('click', (event) => {
                    event.stopPropagation();
                    selectedChild = node.label;
                    render();
                });
            }
            item.appendChild(button);

            if (node.labeled) {
                const label = el('span', 'layer-label');
                const title = type ? type.label : node.label;
                const name = el('span', 'layer-label-name');
                name.textContent = title;
                label.appendChild(name);
                if (type && node.kind === 'type') {
                    const count = el('span', 'layer-label-count');
                    count.textContent = type.count ? formatCount(type.count) : 'leer';
                    label.appendChild(count);
                }
                label.style.transform = node.focus
                    ? `translate(-50%, ${-node.r - 32}px)`
                    : `translate(-50%, ${node.r + 6}px)`;
                button.title = title;
                item.appendChild(label);
            } else if (type) {
                button.title = type.label;
            }
            world.appendChild(item);
        });

        lead.textContent = leadText();
        back.hidden = !focus;
        const emptyNote = document.getElementById('viewEmpty');
        if (emptyNote) emptyNote.hidden = model.nodes.length > 0;
        applyCamera(bounds);
        if (cameraMode === 'empty' && focus) {
            const current = world.querySelector(`[data-id="${CSS.escape(focus.id)}"]`);
            if (current) current.scrollIntoView({ block: 'center', inline: 'nearest' });
        }
    }

    function clearFocus() {
        if (!focus && !selectedChild) return;
        focus = null;
        selectedChild = null;
        cameraMode = 'overview';
        render();
    }

    function monogram(label) {
        const first = Array.from(String(label || '').trim())[0] || '?';
        const glyph = first.toLocaleUpperCase('de')
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const color = getComputedStyle(document.documentElement)
            .getPropertyValue('--primary-color').trim() || '#1A45C7';
        const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 28 28">` +
            `<text x="14" y="15" text-anchor="middle" dominant-baseline="central" fill="${color}" ` +
            `font-size="15" font-weight="600" font-family="IBM Plex Mono, ui-monospace, monospace">${glyph}</text></svg>`;
        return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
    }

    function token(name, fallback) {
        const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return value || fallback;
    }

    function showOldGraph() {
        if (oldGraph) {
            oldGraph.resize();
            oldGraph.fit(undefined, 48);
            return;
        }
        const n = types.length;
        const seedRadius = 300;
        const nodes = types.map((type, index) => ({
            data: {
                id: type.id,
                label: type.label,
                icon: monogram(type.label),
                size: 54 + Math.round(40 * (type.count / maxCount)),
            },
            position: {
                x: seedRadius * Math.cos((2 * Math.PI * index) / n - Math.PI / 2),
                y: seedRadius * Math.sin((2 * Math.PI * index) / n - Math.PI / 2),
            },
        }));
        oldGraph = window.cytoscape({
            container: graphHost,
            elements: nodes,
            minZoom: 0.2,
            maxZoom: 4,
            wheelSensitivity: 0.2,
            style: [{
                selector: 'node',
                style: {
                    'background-color': token('--card-bg', '#FDFDFD'),
                    'border-width': 2,
                    'border-color': token('--accent', '#3B79F2'),
                    width: 'data(size)',
                    height: 'data(size)',
                    label: 'data(label)',
                    'font-family': 'IBM Plex Mono, ui-monospace, monospace',
                    'font-weight': 600,
                    'font-size': 13,
                    color: token('--text-primary', '#283647'),
                    'text-valign': 'bottom',
                    'text-margin-y': 8,
                    'background-image': 'data(icon)',
                    'background-width': '62%',
                    'background-height': '62%',
                    'background-fit': 'none',
                },
            }],
            layout: { name: 'preset' },
        });
        oldGraph.layout({
            name: 'cose',
            animate: false,
            randomize: false,
            fit: false,
            padding: 60,
            boundingBox: { x1: 0, y1: 0, w: 820, h: 560 },
            nodeDimensionsIncludeLabels: true,
            idealEdgeLength: () => 85,
            nodeRepulsion: () => 42000,
            edgeElasticity: () => 190,
            gravity: 4.5,
            numIter: 3000,
        }).run();
        oldGraph.fit(undefined, 48);
    }

    function loadViews() {
        try {
            const raw = JSON.parse(localStorage.getItem(viewStoreKey) || 'null');
            if (!raw || !Array.isArray(raw.views)) return;
            const custom = raw.views.filter((view) => view && view.id && view.id !== 'all' && Array.isArray(view.ids));
            views = [{ id: 'all', name: 'Alle', ids: null }, ...custom.map((view) => ({
                id: String(view.id),
                name: String(view.name || 'Ansicht').slice(0, 40),
                ids: view.ids.map(String),
            }))];
            if (views.some((view) => view.id === raw.active)) activeViewId = raw.active;
        } catch (err) {
            views = [{ id: 'all', name: 'Alle', ids: null }];
            activeViewId = 'all';
        }
    }

    function saveViews() {
        try {
            localStorage.setItem(viewStoreKey, JSON.stringify({ active: activeViewId, views }));
        } catch (err) { /* private mode: the views last for this tab only */ }
    }

    function currentView() {
        return views.find((view) => view.id === activeViewId) || views[0];
    }

    function renderViewBar() {
        const bar = document.getElementById('viewBar');
        bar.replaceChildren();
        views.forEach((view) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'view-chip';
            button.setAttribute('role', 'tab');
            button.dataset.view = view.id;
            button.setAttribute('aria-selected', view.id === activeViewId ? 'true' : 'false');
            button.textContent = view.name;
            button.addEventListener('click', () => selectView(view.id));
            bar.appendChild(button);
        });
        const add = document.createElement('button');
        add.type = 'button';
        add.className = 'view-chip view-chip-add';
        add.textContent = 'Neue Ansicht';
        add.addEventListener('click', createView);
        bar.appendChild(add);
        if (currentView().ids) {
            const edit = document.createElement('button');
            edit.type = 'button';
            edit.className = 'view-chip';
            edit.textContent = 'Knoten wählen';
            edit.setAttribute('aria-expanded', document.getElementById('nodePicker').hidden ? 'false' : 'true');
            edit.addEventListener('click', openPicker);
            bar.appendChild(edit);
        }
    }

    function selectView(id) {
        activeViewId = id;
        focus = null;
        selectedChild = null;
        cameraMode = 'overview';
        if (currentView().ids == null) closePicker();
        saveViews();
        renderViewBar();
        if (!document.getElementById('nodePicker').hidden) renderPicker();
        render();
    }

    function createView() {
        const id = `v-${Date.now().toString(36)}`;
        const count = views.filter((view) => view.ids).length + 1;
        views.push({ id, name: `Ansicht ${count}`, ids: [] });
        activeViewId = id;
        focus = null;
        selectedChild = null;
        cameraMode = 'overview';
        saveViews();
        renderViewBar();
        openPicker();
        render();
    }

    function openPicker() {
        const view = currentView();
        if (!view.ids) return;
        document.getElementById('layerWrap').classList.add('is-picking');
        document.getElementById('nodePicker').hidden = false;
        document.getElementById('viewName').value = view.name;
        pickerQuery = '';
        document.getElementById('pickerSearch').value = '';
        renderPicker();
        renderViewBar();
        render();
    }

    function closePicker() {
        document.getElementById('nodePicker').hidden = true;
        document.getElementById('layerWrap').classList.remove('is-picking');
        renderViewBar();
        render();
    }

    function renderPicker() {
        const view = currentView();
        const list = document.getElementById('pickerList');
        if (!view || !view.ids) {
            list.replaceChildren();
            return;
        }
        const allow = new Set(view.ids);
        const needle = fold(pickerQuery);
        const rows = types
            .filter((type) => !needle || fold(`${type.label} ${type.id}`).includes(needle))
            .slice()
            .sort((a, b) => a.label.localeCompare(b.label, 'de') || b.count - a.count);
        list.replaceChildren();
        rows.forEach((type) => {
            const item = document.createElement('li');
            const label = document.createElement('label');
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.checked = allow.has(type.id);
            box.addEventListener('change', () => {
                const next = new Set(currentView().ids);
                if (box.checked) next.add(type.id);
                else next.delete(type.id);
                currentView().ids = [...next];
                if (focus && !next.has(focus.id)) {
                    focus = null;
                    selectedChild = null;
                    cameraMode = 'overview';
                }
                saveViews();
                renderViewBar();
                render();
            });
            const name = document.createElement('span');
            name.textContent = type.label;
            const count = document.createElement('span');
            count.className = 'picker-count';
            count.textContent = type.count ? formatCount(type.count) : 'leer';
            label.append(box, name, count);
            item.appendChild(label);
            list.appendChild(item);
        });
    }

    function setPickerMembership(ids) {
        const view = currentView();
        if (!view.ids) return;
        view.ids = ids;
        focus = null;
        selectedChild = null;
        cameraMode = 'overview';
        saveViews();
        renderPicker();
        render();
    }

    loadViews();

    function setObjective(next) {
        objective = next;
        focus = null;
        selectedChild = null;
        cameraMode = 'overview';
        document.getElementById('objVolume').setAttribute('aria-checked', next === 'volume' ? 'true' : 'false');
        document.getElementById('objLearned').setAttribute('aria-checked', next === 'learned' ? 'true' : 'false');
        if (!reading.hidden) render();
    }

    function setView(name) {
        const layers = name === 'layers';
        reading.hidden = !layers;
        oldView.hidden = layers;
        document.getElementById('viewLayers').setAttribute('aria-checked', layers ? 'true' : 'false');
        document.getElementById('viewOld').setAttribute('aria-checked', layers ? 'false' : 'true');
        if (layers) render();
        else {
            void oldView.offsetHeight;
            showOldGraph();
        }
    }

    stage.addEventListener('click', (event) => {
        if (event.target.closest('.layer-node')) return;
        clearFocus();
    });
    back.addEventListener('click', clearFocus);
    window.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || reading.hidden) return;
        if (!document.getElementById('nodePicker').hidden) {
            closePicker();
            return;
        }
        if (selectedChild) {
            selectedChild = null;
            render();
            return;
        }
        clearFocus();
    });
    search.addEventListener('input', () => {
        query = fold(search.value.trim());
        focus = null;
        selectedChild = null;
        cameraMode = 'overview';
        render();
    });
    window.addEventListener('resize', () => {
        if (!reading.hidden) render();
    });
    document.getElementById('viewLayers').addEventListener('click', () => setView('layers'));
    document.getElementById('viewOld').addEventListener('click', () => setView('old'));
    document.getElementById('objVolume').addEventListener('click', () => setObjective('volume'));
    document.getElementById('objLearned').addEventListener('click', () => setObjective('learned'));
    document.getElementById('pickerClose').addEventListener('click', closePicker);
    document.getElementById('pickerAll').addEventListener('click', () => setPickerMembership(types.map((type) => type.id)));
    document.getElementById('pickerNone').addEventListener('click', () => setPickerMembership([]));
    document.getElementById('pickerDelete').addEventListener('click', () => {
        const view = currentView();
        if (!view.ids) return;
        views = views.filter((item) => item.id !== view.id);
        activeViewId = 'all';
        closePicker();
        saveViews();
        renderViewBar();
        render();
    });
    document.getElementById('viewName').addEventListener('input', (event) => {
        const view = currentView();
        if (!view.ids) return;
        view.name = event.target.value.replace(/\s+/g, ' ').slice(0, 40);
        saveViews();
        const chip = document.querySelector(`[data-view="${CSS.escape(view.id)}"]`);
        if (chip) chip.textContent = view.name || 'Ansicht';
        lead.textContent = leadText();
    });
    document.getElementById('pickerSearch').addEventListener('input', (event) => {
        pickerQuery = event.target.value.trim();
        renderPicker();
    });
    renderViewBar();
    oldNote.textContent = `Alle ${types.length} Typen als Kreise, auch die ${empty.length} ohne Einträge. ` +
        'Es gibt keine Verbindungen, deshalb liegen die Namen übereinander.';

    requestAnimationFrame(() => {
        render();
        requestAnimationFrame(() => world.classList.add('is-animated'));
    });
}());
