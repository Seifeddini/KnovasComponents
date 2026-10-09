// Decorative galaxy study for Cortex. It is deliberately independent from
// ontology data and never captures pointer input.
'use strict';

(function () {
    const canvas = document.getElementById('pointCloudMockup');
    if (!canvas) return;

    const context = canvas.getContext('2d', { alpha: true });
    if (!context) return;

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const stars = [];
    const systems = [];
    const sunTargets = [...document.querySelectorAll('[data-galaxy-system]')];
    const breadcrumb = document.getElementById('galaxyBreadcrumb');
    const breadcrumbCurrent = document.getElementById('galaxyBreadcrumbCurrent');
    const galaxyUp = document.getElementById('galaxyUp');
    const galaxySystemUp = document.getElementById('galaxySystemUp');
    const entitySeparator = document.getElementById('galaxyEntitySeparator');
    const entityPanel = document.getElementById('galaxyEntityPanel');
    const entityType = document.getElementById('galaxyEntityType');
    const entityTitle = document.getElementById('galaxyEntityTitle');
    const entityFields = document.getElementById('galaxyEntityFields');
    const documentsPanel = document.getElementById('galaxyDocumentsPanel');
    const entityDocuments = document.getElementById('galaxyEntityDocuments');
    const historyPanel = document.getElementById('galaxyHistoryPanel');
    const entityHistory = document.getElementById('galaxyEntityHistory');
    const entityTabs = document.getElementById('galaxyEntityTabs');
    const fieldsTab = document.getElementById('galaxyFieldsTab');
    const documentsTab = document.getElementById('galaxyDocumentsTab');
    const historyTab = document.getElementById('galaxyHistoryTab');
    let width = 0;
    let height = 0;
    let pixelRatio = 1;
    let frame = 0;
    let targetTiltX = 0.58;
    let targetTiltY = 0.14;
    let tiltX = targetTiltX;
    let tiltY = targetTiltY;
    let zoom = 1.32;
    let targetZoom = zoom;
    let cameraX = 0;
    let cameraY = 0;
    let focusMix = 0;
    let targetFocusMix = 0;
    let focusedSystem = null;
    let highlightedEntity = null;
    let selectedEntity = null;
    let entityMix = 0;
    let targetEntityMix = 0;
    let historyMix = 0;
    let targetHistoryMix = 0;
    let detailView = 'fields';
    let travelTimer = 0;

    // Seeded randomness keeps the galaxy recognisable across reloads.
    let seed = 0x4b4e4f56;
    const random = () => {
        seed = (1664525 * seed + 1013904223) >>> 0;
        return seed / 4294967296;
    };
    const jitter = () => random() + random() + random() - 1.5;

    // Five curved arms with a dense central bulge.
    for (let i = 0; i < 1180; i += 1) {
        const arm = i % 5;
        const radius = 0.08 + 0.94 * Math.pow(random(), 0.62);
        const angle = arm * (Math.PI * 2 / 5) + radius * 5.5
            + jitter() * (0.16 + 0.24 * radius);
        stars.push({
            x: Math.cos(angle) * radius * 1.28,
            y: jitter() * 0.12 * (1 - radius * 0.48),
            z: Math.sin(angle) * radius,
            size: 0.4 + random() * 1.2,
            brightness: 0.34 + random() * 0.66,
            warmth: random(),
        });
    }
    for (let i = 0; i < 260; i += 1) {
        const radius = Math.pow(random(), 1.85) * 0.43;
        const angle = random() * Math.PI * 2;
        stars.push({
            x: Math.cos(angle) * radius * 1.2,
            y: jitter() * 0.16 * (0.5 - radius),
            z: Math.sin(angle) * radius,
            size: 0.55 + random() * 1.45,
            brightness: 0.5 + random() * 0.5,
            warmth: random(),
        });
    }

    const systemDefinitions = [
        {
            x: -0.46, y: -0.02, z: 0.08, label: 'Mandant', primary: true, warm: true,
            entities: [
                'Müller Bau AG', 'Meier Immobilien', 'Keller Holding',
                'NovaTech AG', 'Familie Baumann', 'Stadtwerke Nord',
            ],
        },
        {
            x: 0.44, y: 0.015, z: -0.12, label: 'Gericht', primary: true, warm: false,
            entities: [
                'Bezirksgericht Zürich', 'Obergericht Zürich', 'Handelsgericht',
                'Bundesgericht', 'Arbeitsgericht', 'Schlichtungsstelle',
            ],
        },
        {
            x: -0.08, y: 0.025, z: 0.5, label: 'Personen', primary: true, warm: false,
            entities: [
                'Dr. Anna Keller', 'Markus Meier', 'Lisa Baumann',
                'Thomas Frei', 'Sofia Rossi', 'Daniel Weber',
            ],
        },
        { x: 0.14, y: -0.03, z: -0.62 },
        { x: 0.76, y: 0.01, z: 0.28 },
        { x: -0.82, y: -0.02, z: -0.27 },
        { x: 0.22, y: 0.02, z: 0.18 },
    ];

    const connections = {
        Mandant: {
            1: { system: 'Gericht', entity: 'Bezirksgericht Zürich' },
            4: { system: 'Personen', entity: 'Lisa Baumann' },
        },
        Gericht: {
            1: { system: 'Mandant', entity: 'Müller Bau AG' },
            4: { system: 'Personen', entity: 'Dr. Anna Keller' },
        },
        Personen: {
            1: { system: 'Mandant', entity: 'Meier Immobilien' },
            4: { system: 'Gericht', entity: 'Arbeitsgericht' },
        },
    };

    systemDefinitions.forEach((definition, systemIndex) => {
        const system = {
            ...definition,
            size: definition.primary ? 8.5 : 3.2 + random() * 1.8,
            orbit: [],
            entityNodes: [],
        };
        const orbitCount = definition.primary ? 42 : 18 + Math.floor(random() * 15);
        const orbitRadius = definition.primary ? 0.12 : 0.055 + random() * 0.04;
        for (let i = 0; i < orbitCount; i += 1) {
            const angle = (i / orbitCount) * Math.PI * 2 + systemIndex * 0.7;
            const ring = orbitRadius * (0.72 + random() * 0.42);
            system.orbit.push({
                x: definition.x + Math.cos(angle) * ring,
                y: definition.y + jitter() * 0.012,
                z: definition.z + Math.sin(angle) * ring * 0.72,
                size: 0.42 + random() * 0.72,
                brightness: 0.52 + random() * 0.48,
                warmth: definition.warm ? 0.98 : random(),
            });
        }
        (definition.entities || []).forEach((label, index, entities) => {
            const angle = (index / entities.length) * Math.PI * 2 + systemIndex * 0.36;
            const ring = 0.2 + (index % 2) * 0.075;
            system.entityNodes.push({
                x: definition.x + Math.cos(angle) * ring,
                y: definition.y + ((index % 3) - 1) * 0.012,
                z: definition.z + Math.sin(angle) * ring * 0.74,
                label,
                size: 4.1 + (index % 3) * 0.45,
                connection: connections[definition.label]
                    ? connections[definition.label][index] || null
                    : null,
            });
        });
        systems.push(system);
        stars.push(...system.orbit);
    });

    const connectionArrows = Array.from({ length: 2 }, (_, index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'galaxy-connection-arrow';
        button.dataset.arrowIndex = String(index);
        button.hidden = true;
        button.innerHTML =
            '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
            '<path d="M5 12h13M14 7l5 5-5 5"/></svg>';
        canvas.parentElement.appendChild(button);
        return button;
    });
    const entityTargets = Array.from({ length: 6 }, (_, index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'galaxy-entity-target';
        button.dataset.entityIndex = String(index);
        button.hidden = true;
        canvas.parentElement.appendChild(button);
        return button;
    });
    const connectionPopover = document.createElement('aside');
    connectionPopover.className = 'galaxy-connection-popover';
    connectionPopover.hidden = true;
    connectionPopover.setAttribute('aria-live', 'polite');
    const connectionTitle = document.createElement('strong');
    const connectionDetail = document.createElement('span');
    const connectionClose = document.createElement('button');
    connectionClose.type = 'button';
    connectionClose.className = 'galaxy-connection-close';
    connectionClose.setAttribute('aria-label', 'Verbindungsinfo schliessen');
    connectionClose.textContent = '×';
    connectionPopover.append(connectionClose, connectionTitle, connectionDetail);
    canvas.parentElement.appendChild(connectionPopover);
    connectionClose.addEventListener('click', () => {
        connectionPopover.hidden = true;
    });

    function detailGroups(system, entity) {
        if (system.label === 'Personen') {
            return [
                ['Stammdaten', [
                    ['Name', entity.label],
                    ['Geburtsdatum', '14. März 1982'],
                    ['Adresse', 'Bahnhofstrasse 24, Zürich'],
                    ['E-Mail', 'kontakt@beispiel.ch'],
                ]],
                ['Zuordnung', [
                    ['Rolle', 'Ansprechperson'],
                    ['Organisation', 'Müller Bau AG'],
                    ['Status', 'Aktiv'],
                ]],
            ];
        }
        if (system.label === 'Gericht') {
            return [
                ['Stammdaten', [
                    ['Bezeichnung', entity.label],
                    ['Gerichtsebene', 'Kantonal'],
                    ['Ort', 'Zürich'],
                    ['Zuständigkeit', 'Zivilrecht'],
                ]],
                ['Verfahren', [
                    ['Aktenzeichen', 'HG-2026-184'],
                    ['Status', 'Laufend'],
                    ['Letzte Aktivität', '8. Oktober 2026'],
                ]],
            ];
        }
        return [
            ['Stammdaten', [
                ['Firmierung', entity.label],
                ['Mandatsnummer', 'M-2026-041'],
                ['Rechtsform', 'Aktiengesellschaft'],
                ['Sitz', 'Zürich'],
            ]],
            ['Betreuung', [
                ['Ansprechperson', 'Dr. Anna Keller'],
                ['Fachgebiet', 'Vertragsrecht'],
                ['Status', 'Aktiv'],
            ]],
        ];
    }

    function documentsFor(system, entity) {
        if (system.label === 'Personen') {
            return [
                [`Vollmacht ${entity.label}.pdf`, 'Vollmacht · 8. Oktober 2026'],
                ['Mandatsvertrag Müller Bau AG.pdf', 'Vertrag · 26. September 2026'],
                ['Korrespondenz zur Sache.msg', 'E-Mail · 19. September 2026'],
                ['Besprechungsnotiz.pdf', 'Notiz · 4. September 2026'],
            ];
        }
        if (system.label === 'Gericht') {
            return [
                ['Verfügung HG-2026-184.pdf', 'Verfügung · 7. Oktober 2026'],
                ['Klageantwort.pdf', 'Rechtsschrift · 28. September 2026'],
                ['Vorladung Hauptverhandlung.pdf', 'Vorladung · 12. September 2026'],
            ];
        }
        return [
            [`Mandatsvertrag ${entity.label}.pdf`, 'Vertrag · 26. September 2026'],
            ['Handelsregisterauszug.pdf', 'Register · 18. September 2026'],
            ['Vollmacht.pdf', 'Vollmacht · 4. September 2026'],
            ['Korrespondenz.msg', 'E-Mail · 29. August 2026'],
        ];
    }

    function renderDocuments(system, entity) {
        if (!entityDocuments) return;
        entityDocuments.replaceChildren();
        const list = document.createElement('ul');
        list.className = 'galaxy-document-list';
        documentsFor(system, entity).forEach(([title, meta]) => {
            const item = document.createElement('li');
            item.className = 'galaxy-document-item';
            const name = document.createElement('span');
            name.className = 'galaxy-document-title';
            name.textContent = title;
            const details = document.createElement('span');
            details.className = 'galaxy-document-meta';
            details.textContent = meta;
            item.append(name, details);
            list.appendChild(item);
        });
        entityDocuments.appendChild(list);
    }

    function renderHistory(entity) {
        if (!entityHistory) return;
        entityHistory.replaceChildren();
        const list = document.createElement('ol');
        list.className = 'galaxy-history-list';
        [
            ['Heute, 09:42', `${entity.label} im Wissensnetz geöffnet`],
            ['8. Okt.', 'Neue Verbindung bestätigt'],
            ['26. Sept.', 'Stammdaten aus Dokument ergänzt'],
            ['4. Sept.', 'Entität erstmals erkannt'],
        ].forEach(([time, event]) => {
            const item = document.createElement('li');
            item.className = 'galaxy-history-item';
            const date = document.createElement('span');
            date.className = 'galaxy-history-time';
            date.textContent = time;
            const description = document.createElement('span');
            description.textContent = event;
            item.append(date, description);
            list.appendChild(item);
        });
        entityHistory.appendChild(list);
    }

    function setDetailTab(tab) {
        const showFields = tab === 'fields';
        const showDocuments = tab === 'documents';
        const showHistory = tab === 'history';
        detailView = tab;
        targetHistoryMix = showHistory ? 1 : 0;
        if (reducedMotion.matches) historyMix = targetHistoryMix;
        entityPanel.classList.toggle('active', showFields);
        entityPanel.classList.toggle('view-hidden', showHistory);
        documentsPanel.classList.toggle('active', showDocuments);
        documentsPanel.classList.toggle('view-hidden', showHistory);
        historyPanel.classList.remove('active');
        historyPanel.classList.toggle('view-hidden', showHistory);
        fieldsTab.classList.toggle('active', showFields);
        documentsTab.classList.toggle('active', showDocuments);
        historyTab.classList.toggle('active', showHistory);
        fieldsTab.setAttribute('aria-selected', String(showFields));
        documentsTab.setAttribute('aria-selected', String(showDocuments));
        historyTab.setAttribute('aria-selected', String(showHistory));
        restart();
    }

    function renderEntityPanel(system, entity) {
        if (!entityPanel || !documentsPanel || !historyPanel || !entityTabs
                || !entityType || !entityTitle || !entityFields) return;
        entityType.textContent = system.label;
        entityTitle.textContent = entity.label;
        entityFields.replaceChildren();
        detailGroups(system, entity).forEach(([heading, rows]) => {
            const section = document.createElement('section');
            section.className = 'galaxy-field-group';
            const title = document.createElement('h3');
            title.textContent = heading;
            const table = document.createElement('table');
            table.className = 'galaxy-field-table';
            const body = document.createElement('tbody');
            rows.forEach(([field, value]) => {
                const row = document.createElement('tr');
                const label = document.createElement('th');
                label.scope = 'row';
                label.textContent = field;
                const content = document.createElement('td');
                content.textContent = value;
                row.append(label, content);
                body.appendChild(row);
            });
            table.appendChild(body);
            section.append(title, table);
            entityFields.appendChild(section);
        });
        renderDocuments(system, entity);
        renderHistory(entity);
        entityPanel.hidden = false;
        documentsPanel.hidden = false;
        historyPanel.hidden = false;
        entityTabs.hidden = false;
        setDetailTab('fields');
        window.requestAnimationFrame(() => {
            entityPanel.classList.add('open');
            documentsPanel.classList.add('open');
            historyPanel.classList.add('open');
        });
    }

    function hideEntityPanel() {
        if (!entityPanel || !documentsPanel || !historyPanel || !entityTabs) return;
        detailView = 'fields';
        targetHistoryMix = 0;
        entityPanel.classList.remove('open');
        documentsPanel.classList.remove('open');
        historyPanel.classList.remove('open');
        entityPanel.hidden = true;
        documentsPanel.hidden = true;
        historyPanel.hidden = true;
        entityTabs.hidden = true;
    }

    function resize() {
        const bounds = canvas.getBoundingClientRect();
        pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
        width = Math.max(1, bounds.width);
        height = Math.max(1, bounds.height);
        canvas.width = Math.round(width * pixelRatio);
        canvas.height = Math.round(height * pixelRatio);
        context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    }

    function rotatePoint(point, rotationX, rotationY) {
        const cosY = Math.cos(rotationY);
        const sinY = Math.sin(rotationY);
        const xzX = point.x * cosY - point.z * sinY;
        const xzZ = point.x * sinY + point.z * cosY;
        const cosX = Math.cos(rotationX);
        const sinX = Math.sin(rotationX);
        return {
            ...point,
            x: xzX,
            y: point.y * cosX - xzZ * sinX,
            z: point.y * sinX + xzZ * cosX,
        };
    }

    function project(point, rotation, scale, centerX, centerY) {
        const rotated = rotatePoint(point, tiltX, rotation + tiltY);
        const perspective = 3.4;
        const depthScale = perspective / (perspective - rotated.z);
        return {
            ...rotated,
            x: centerX + rotated.x * scale * depthScale,
            y: centerY + rotated.y * scale * depthScale,
            depthScale,
        };
    }

    function drawSun(system, projected) {
        const radius = system.size * projected.depthScale;
        const warm = system.warm;
        const glow = context.createRadialGradient(
            projected.x, projected.y, 0,
            projected.x, projected.y, radius * 4.8,
        );
        glow.addColorStop(0, warm ? 'rgba(255, 244, 184, 1)' : 'rgba(225, 240, 255, 1)');
        glow.addColorStop(0.18, warm ? 'rgba(246, 181, 66, .95)' : 'rgba(78, 139, 255, .95)');
        glow.addColorStop(0.52, warm ? 'rgba(238, 146, 40, .32)' : 'rgba(40, 89, 205, .3)');
        glow.addColorStop(1, 'rgba(40, 89, 205, 0)');
        context.beginPath();
        context.arc(projected.x, projected.y, radius * 4.8, 0, Math.PI * 2);
        context.fillStyle = glow;
        context.fill();

        context.beginPath();
        context.arc(projected.x, projected.y, radius, 0, Math.PI * 2);
        context.fillStyle = warm ? '#ffd46a' : '#dceaff';
        context.fill();

        if (!system.label) return;
        context.font = '600 12px "IBM Plex Mono", ui-monospace, monospace';
        context.textAlign = 'center';
        context.textBaseline = 'top';
        context.fillStyle = '#283647';
        context.fillText(system.label, projected.x, projected.y + radius + 9);
    }

    function positionSunTarget(system, projected) {
        if (!system.label) return;
        const target = sunTargets.find(
            (button) => button.dataset.galaxySystem === system.label);
        if (!target) return;
        const size = Math.max(58, system.size * projected.depthScale * 6.5);
        target.style.width = `${size}px`;
        target.style.height = `${size}px`;
        target.style.transform =
            `translate3d(${projected.x - size / 2}px, ${projected.y - size / 2}px, 0)`;
        const enabled = !focusedSystem || focusedSystem === system;
        target.style.pointerEvents = enabled ? 'auto' : 'none';
        target.tabIndex = enabled ? 0 : -1;
    }

    function positionEntityTarget(index, node, system) {
        const button = entityTargets[index];
        if (!button) return;
        button.hidden = false;
        button.style.left = `${node.x}px`;
        button.style.top = `${node.y}px`;
        const size = Math.max(34, node.size * node.depthScale * 5.2);
        button.style.width = `${size}px`;
        button.style.height = `${size}px`;
        button.setAttribute('aria-label', `${node.label} öffnen`);
        button.title = node.label;
        if (button.dataset.label !== node.label
                || button.dataset.system !== system.label) {
            button.dataset.label = node.label;
            button.dataset.system = system.label;
            button.onclick = () => openEntityDetail(
                button.dataset.system, button.dataset.label);
        }
    }

    function fadingLine(from, to, color) {
        const control = {
            x: (from.x + to.x) / 2,
            y: (from.y + to.y) / 2 - 32,
        };
        const gradient = context.createLinearGradient(from.x, from.y, to.x, to.y);
        gradient.addColorStop(0, color);
        gradient.addColorStop(0.45, color.replace(/[\d.]+\)$/, '0.12)'));
        gradient.addColorStop(1, color.replace(/[\d.]+\)$/, '0)'));
        context.beginPath();
        context.moveTo(from.x, from.y);
        context.quadraticCurveTo(control.x, control.y, to.x, to.y);
        context.strokeStyle = gradient;
        context.lineWidth = 1.2;
        context.setLineDash([4, 6]);
        context.stroke();
        context.setLineDash([]);

        const t = 0.34;
        const oneMinusT = 1 - t;
        const point = {
            x: oneMinusT * oneMinusT * from.x
                + 2 * oneMinusT * t * control.x + t * t * to.x,
            y: oneMinusT * oneMinusT * from.y
                + 2 * oneMinusT * t * control.y + t * t * to.y,
        };
        const tangent = {
            x: 2 * oneMinusT * (control.x - from.x) + 2 * t * (to.x - control.x),
            y: 2 * oneMinusT * (control.y - from.y) + 2 * t * (to.y - control.y),
        };
        return { ...point, angle: Math.atan2(tangent.y, tangent.x) };
    }

    function showConnectionInfo(button) {
        connectionTitle.textContent = button.dataset.source;
        connectionDetail.textContent =
            `Verbindung zu ${button.dataset.targetEntity} · ${button.dataset.targetSystem}`;
        const left = Number.parseFloat(button.style.left);
        const top = Number.parseFloat(button.style.top);
        connectionPopover.style.left =
            `${Math.max(16, Math.min(width - 250, left + 20))}px`;
        connectionPopover.style.top =
            `${Math.max(82, Math.min(height - 130, top - 18))}px`;
        connectionPopover.hidden = false;
    }

    function positionConnectionArrow(index, position, source, target, warm) {
        const button = connectionArrows[index];
        if (!button) return;
        button.hidden = false;
        button.style.left = `${position.x}px`;
        button.style.top = `${position.y}px`;
        button.style.opacity = String(focusMix);
        button.style.setProperty('--arrow-angle', `${position.angle}rad`);
        button.style.setProperty('--arrow-color', warm ? '#c77b18' : '#2859cd');
        if (button.dataset.source !== source
                || button.dataset.targetSystem !== target.system
                || button.dataset.targetEntity !== target.entity) {
            button.dataset.source = source;
            button.dataset.targetSystem = target.system;
            button.dataset.targetEntity = target.entity;
            button.setAttribute(
                'aria-label',
                `Von ${source} zu ${target.entity} in ${target.system} wechseln`);
            button.title = `${source} → ${target.entity}`;
            button.onmouseenter = () => showConnectionInfo(button);
            button.onfocus = () => showConnectionInfo(button);
            button.onmouseleave = () => { connectionPopover.hidden = true; };
            button.onblur = () => { connectionPopover.hidden = true; };
            button.onclick = () => travelTo(
                button.dataset.targetSystem, button.dataset.targetEntity);
        }
    }

    function drawFocusedSystem(system, projectedSun, projectedSystems,
                               rotation, scale, centerX, centerY, time) {
        if (!system || focusMix < 0.015) return [];
        const nodes = system.entityNodes.map((entity, index, entities) => {
            const natural = project(entity, rotation, scale, centerX, centerY);
            const angle = -Math.PI / 2 + (index / entities.length) * Math.PI * 2;
            const orbitWidth = Math.min(width * 0.19, 190);
            const orbitHeight = Math.min(height * 0.062, 52);
            const orbitTilt = -0.24;
            const orbitX = Math.cos(angle) * orbitWidth;
            const orbitY = Math.sin(angle) * orbitHeight;
            const depth = Math.sin(angle);
            const arranged = {
                x: width * 0.47
                    + orbitX * Math.cos(orbitTilt) - orbitY * Math.sin(orbitTilt),
                y: height * 0.44
                    + orbitX * Math.sin(orbitTilt) + orbitY * Math.cos(orbitTilt),
                depthScale: 0.68 + (depth + 1) * 0.17,
            };
            return {
                ...natural,
                x: natural.x + (arranged.x - natural.x) * entityMix,
                y: natural.y + (arranged.y - natural.y) * entityMix,
                depthScale: natural.depthScale
                    + (arranged.depthScale - natural.depthScale) * entityMix,
                label: entity.label,
                connection: entity.connection,
            };
        });
        const systemColor = system.warm ? 'rgba(214, 132, 30, .34)'
                                        : 'rgba(40, 89, 205, .34)';

        context.save();
        context.globalAlpha = focusMix * (1 - entityMix * 0.58)
            * (1 - historyMix);

        // An orbit is suggested as a broken path through the aggregated nodes.
        context.beginPath();
        let arrowIndex = 0;
        nodes.forEach((node, index) => {
            if (index === 0) context.moveTo(node.x, node.y);
            else context.lineTo(node.x, node.y);
        });
        if (nodes.length) context.closePath();
        context.strokeStyle = system.warm
            ? 'rgba(214, 132, 30, .16)'
            : 'rgba(40, 89, 205, .16)';
        context.lineWidth = 1;
        context.setLineDash([2, 7]);
        context.stroke();
        context.setLineDash([]);

        nodes.forEach((node, index) => {
            if (selectedEntity && node.label === selectedEntity.label) return;
            context.beginPath();
            context.moveTo(projectedSun.x, projectedSun.y);
            context.lineTo(node.x, node.y);
            context.strokeStyle = systemColor;
            context.lineWidth = 0.9;
            context.stroke();

            if (node.connection && !selectedEntity) {
                const destination = projectedSystems.find(
                    (entry) => entry.system.label === node.connection.system);
                const lineTarget = destination
                    ? { x: destination.projected.x, y: destination.projected.y }
                    : { x: node.x + width * 0.43, y: node.y - height * 0.24 };
                const arrowPosition = fadingLine(node, lineTarget, systemColor);
                positionConnectionArrow(
                    arrowIndex,
                    arrowPosition,
                    node.label,
                    node.connection,
                    system.warm,
                );
                arrowIndex += 1;
            }
        });

        nodes.forEach((node, index) => {
            if (selectedEntity && node.label === selectedEntity.label) return;
            const radius = node.size * node.depthScale;
            const isHighlighted = node.label === highlightedEntity;
            if (isHighlighted) {
                const pulse = reducedMotion.matches ? 0.5
                    : (Math.sin(time * 0.006) + 1) / 2;
                context.beginPath();
                context.arc(
                    node.x,
                    node.y,
                    radius * (2.3 + pulse * 1.35),
                    0,
                    Math.PI * 2,
                );
                context.strokeStyle = system.warm
                    ? `rgba(199, 123, 24, ${0.58 - pulse * 0.28})`
                    : `rgba(40, 89, 205, ${0.58 - pulse * 0.28})`;
                context.lineWidth = 2;
                context.stroke();
            }
            const glow = context.createRadialGradient(
                node.x, node.y, 0, node.x, node.y, radius * 3.4);
            glow.addColorStop(0, system.warm
                ? 'rgba(255, 223, 143, .95)' : 'rgba(222, 236, 255, .98)');
            glow.addColorStop(0.28, system.warm
                ? 'rgba(222, 143, 43, .78)' : 'rgba(59, 121, 242, .82)');
            glow.addColorStop(1, 'rgba(40, 89, 205, 0)');
            context.beginPath();
            context.arc(node.x, node.y, radius * 3.4, 0, Math.PI * 2);
            context.fillStyle = glow;
            context.fill();
            context.beginPath();
            context.arc(node.x, node.y, radius, 0, Math.PI * 2);
            context.fillStyle = system.warm ? '#e6a137' : '#4d83e9';
            context.fill();

            context.font = `${isHighlighted ? 700 : 500} 11px "IBM Plex Sans", ui-sans-serif, sans-serif`;
            context.textAlign = node.x < projectedSun.x ? 'right' : 'left';
            context.textBaseline = 'middle';
            context.fillStyle = '#283647';
            context.fillText(
                node.label,
                node.x + (node.x < projectedSun.x ? -radius - 7 : radius + 7),
                node.y,
            );
            if (!selectedEntity) positionEntityTarget(index, node, system);
        });
        context.restore();
        return nodes;
    }

    function drawHistoryCylinder(system, entity, x, y, radius, time) {
        if (historyMix < 0.015) return;
        const entries = [
            ['Heute · 09:42', 'Im Wissensnetz geöffnet'],
            ['08. Okt.', 'Verbindung bestätigt'],
            ['26. Sept.', 'Stammdaten ergänzt'],
            ['04. Sept.', 'Entität erkannt'],
        ];
        const color = system.warm ? '199, 123, 24' : '40, 89, 205';
        const available = Math.min(width - x - 92, width * 0.46);
        const spacing = Math.max(82, available / (entries.length - 1));
        const discHeight = radius * 0.86;
        const discWidth = Math.max(7, radius * 0.16);
        const discTilt = -0.24;
        const topXOffset = discHeight * Math.sin(discTilt);
        const topYOffset = -discHeight * Math.cos(discTilt);
        const bottomXOffset = -topXOffset;
        const bottomYOffset = -topYOffset;
        const eased = 1 - Math.pow(1 - historyMix, 3);
        const lastX = x + spacing * (entries.length - 1) * eased;

        context.save();
        context.globalAlpha = entityMix * historyMix;

        const body = context.createLinearGradient(x, y, lastX, y);
        body.addColorStop(0, `rgba(${color}, .28)`);
        body.addColorStop(0.55, `rgba(${color}, .09)`);
        body.addColorStop(1, `rgba(${color}, .2)`);
        context.beginPath();
        context.moveTo(x + topXOffset, y + topYOffset);
        context.lineTo(lastX + topXOffset, y + topYOffset);
        context.lineTo(lastX + bottomXOffset, y + bottomYOffset);
        context.lineTo(x + bottomXOffset, y + bottomYOffset);
        context.closePath();
        context.fillStyle = body;
        context.fill();

        context.beginPath();
        context.moveTo(x + topXOffset, y + topYOffset);
        context.lineTo(lastX + topXOffset, y + topYOffset);
        context.moveTo(x + bottomXOffset, y + bottomYOffset);
        context.lineTo(lastX + bottomXOffset, y + bottomYOffset);
        context.strokeStyle = `rgba(${color}, .32)`;
        context.lineWidth = 1;
        context.stroke();

        entries.forEach(([timestamp, note], index) => {
            const local = Math.max(
                0,
                Math.min(1, historyMix * 1.35 - index * 0.11),
            );
            if (local <= 0) return;
            const movement = 1 - Math.pow(1 - local, 3);
            const discX = x + spacing * index * movement;
            const pulse = reducedMotion.matches
                ? 0.5
                : (Math.sin(time * 0.0035 + index * 0.9) + 1) / 2;

            context.save();
            context.globalAlpha = entityMix * local;
            const disc = context.createLinearGradient(
                discX - discWidth, y, discX + discWidth, y);
            disc.addColorStop(0, `rgba(${color}, .3)`);
            disc.addColorStop(0.45, `rgba(${color}, .92)`);
            disc.addColorStop(1, `rgba(${color}, .4)`);
            context.beginPath();
            context.ellipse(
                discX,
                y,
                discWidth + pulse * 1.2,
                discHeight,
                discTilt,
                0,
                Math.PI * 2,
            );
            context.fillStyle = disc;
            context.fill();
            context.strokeStyle = `rgba(${color}, .8)`;
            context.lineWidth = index === 0 ? 2 : 1.3;
            context.stroke();

            context.textAlign = 'center';
            context.textBaseline = 'top';
            context.fillStyle = '#283647';
            context.font = '700 10px "IBM Plex Mono", ui-monospace, monospace';
            context.fillText(timestamp, discX, y + discHeight + 20);
            context.fillStyle = '#6f788d';
            context.font = '500 10px "IBM Plex Sans", ui-sans-serif, sans-serif';
            context.fillText(note, discX, y + discHeight + 38);
            context.restore();
        });

        context.globalAlpha = entityMix * historyMix;
        context.textAlign = 'left';
        context.textBaseline = 'bottom';
        context.fillStyle = '#283647';
        context.font = '700 13px "IBM Plex Mono", ui-monospace, monospace';
        context.fillText(entity.label, x - discWidth, y - discHeight - 50);
        context.restore();
    }

    function drawEntityDetail(system, entity, rotation, scale, centerX, centerY,
                              time, projectedOrigin, projectedSystemCenter) {
        if (!system || !entity || entityMix < 0.015) return;
        const origin = projectedOrigin
            || project(entity, rotation, scale, centerX, centerY);
        const beamOrigin = projectedSystemCenter || origin;
        const target = {
            x: width < 720 ? width * 0.5 : width * 0.3,
            y: width < 720 ? height * 0.32 : height * 0.51,
        };
        const x = origin.x + (target.x - origin.x) * entityMix;
        const y = origin.y + (target.y - origin.y) * entityMix;
        const targetRadius = Math.min(width, height) * (width < 720 ? 0.065 : 0.078);
        const radius = origin.size * origin.depthScale
            + (targetRadius - origin.size * origin.depthScale) * entityMix;
        const pulse = reducedMotion.matches ? 0.5 : (Math.sin(time * 0.0045) + 1) / 2;

        context.save();
        context.globalAlpha = entityMix * (1 - historyMix);
        const color = system.warm ? '199, 123, 24' : '40, 89, 205';
        const distance = Math.max(
            1,
            Math.hypot(x - beamOrigin.x, y - beamOrigin.y),
        );
        const directionX = (x - beamOrigin.x) / distance;
        const directionY = (y - beamOrigin.y) / distance;
        const perpendicularX = -directionY;
        const perpendicularY = directionX;
        const endX = x - directionX * radius * 0.88;
        const endY = y - directionY * radius * 0.88;
        const tether = context.createLinearGradient(
            beamOrigin.x, beamOrigin.y, endX, endY);
        tether.addColorStop(0, `rgba(${color}, .08)`);
        tether.addColorStop(0.46, `rgba(${color}, .3)`);
        tether.addColorStop(1, `rgba(${color}, .72)`);

        // The widening beam makes the selected node read as moving from the
        // distant, tilted system into the foreground.
        context.beginPath();
        context.moveTo(
            beamOrigin.x + perpendicularX * 0.35,
            beamOrigin.y + perpendicularY * 0.35,
        );
        context.lineTo(
            endX + perpendicularX * 2.8,
            endY + perpendicularY * 2.8,
        );
        context.lineTo(
            endX - perpendicularX * 2.8,
            endY - perpendicularY * 2.8,
        );
        context.lineTo(
            beamOrigin.x - perpendicularX * 0.35,
            beamOrigin.y - perpendicularY * 0.35,
        );
        context.closePath();
        context.fillStyle = tether;
        context.fill();

        const outerGlow = context.createRadialGradient(x, y, radius * 0.25, x, y, radius * 1.8);
        outerGlow.addColorStop(0, system.warm
            ? 'rgba(255, 210, 106, .58)' : 'rgba(91, 142, 244, .55)');
        outerGlow.addColorStop(0.62, system.warm
            ? 'rgba(214, 132, 30, .13)' : 'rgba(40, 89, 205, .13)');
        outerGlow.addColorStop(1, 'rgba(40, 89, 205, 0)');
        context.globalAlpha = entityMix;
        const sphereWidth = radius * (1 - historyMix * 0.82);
        const sphereTilt = -0.24 * historyMix;
        context.beginPath();
        context.ellipse(
            x, y, sphereWidth * 1.8, radius * 1.8,
            sphereTilt, 0, Math.PI * 2);
        context.fillStyle = outerGlow;
        context.fill();

        const sphere = context.createRadialGradient(
            x - radius * 0.3, y - radius * 0.35, radius * 0.05,
            x, y, radius,
        );
        if (system.warm) {
            sphere.addColorStop(0, '#fff3c4');
            sphere.addColorStop(0.38, '#f3bb54');
            sphere.addColorStop(1, '#bc6915');
        } else {
            sphere.addColorStop(0, '#f7fbff');
            sphere.addColorStop(0.38, '#6fa0f5');
            sphere.addColorStop(1, '#244eaf');
        }
        context.beginPath();
        context.ellipse(
            x, y, sphereWidth, radius, sphereTilt, 0, Math.PI * 2);
        context.fillStyle = sphere;
        context.fill();

        context.beginPath();
        context.ellipse(
            x,
            y,
            sphereWidth * (1.22 + pulse * 0.12),
            radius * (1.22 + pulse * 0.12),
            sphereTilt,
            0,
            Math.PI * 2,
        );
        context.strokeStyle = system.warm
            ? `rgba(199, 123, 24, ${0.35 - pulse * 0.15})`
            : `rgba(40, 89, 205, ${0.35 - pulse * 0.15})`;
        context.lineWidth = 1.5;
        context.stroke();

        context.font = '700 14px "IBM Plex Mono", ui-monospace, monospace';
        context.textAlign = 'center';
        context.textBaseline = 'top';
        context.fillStyle = '#283647';
        context.globalAlpha = entityMix * (1 - historyMix);
        context.fillText(entity.label, x, y + radius + 18);
        context.globalAlpha = entityMix;
        drawHistoryCylinder(system, entity, x, y, radius, time);
        context.restore();
    }

    function draw(time = 0) {
        context.clearRect(0, 0, width, height);
        connectionArrows.forEach((button) => { button.hidden = true; });
        entityTargets.forEach((button) => { button.hidden = true; });
        const motionStep = reducedMotion.matches ? 1 : 0.075;
        tiltX += (targetTiltX - tiltX) * Math.min(motionStep, 0.035);
        tiltY += (targetTiltY - tiltY) * Math.min(motionStep, 0.035);
        zoom += (targetZoom - zoom) * motionStep;
        focusMix += (targetFocusMix - focusMix) * motionStep;
        entityMix += (targetEntityMix - entityMix) * motionStep;
        historyMix += (targetHistoryMix - historyMix) * motionStep;

        const rotation = reducedMotion.matches ? 0.2 : time * 0.000022;
        const scale = Math.min(width, height) * (width < 640 ? 0.32 : 0.3) * zoom;
        const baseCenterX = width * 0.5;
        const baseCenterY = height * 0.46;
        let wantedCameraX = 0;
        let wantedCameraY = 0;
        if (focusedSystem) {
            const focusAtRest = project(
                focusedSystem, rotation, scale, baseCenterX, baseCenterY);
            wantedCameraX = baseCenterX - focusAtRest.x;
            wantedCameraY = baseCenterY - focusAtRest.y;
        }
        cameraX += (wantedCameraX - cameraX) * motionStep;
        cameraY += (wantedCameraY - cameraY) * motionStep;
        const centerX = baseCenterX + cameraX;
        const centerY = baseCenterY + cameraY;
        const halo = context.createRadialGradient(
            centerX, centerY, 0,
            centerX, centerY, scale * 1.15,
        );
        halo.addColorStop(0, 'rgba(36, 65, 151, .095)');
        halo.addColorStop(0.52, 'rgba(48, 95, 210, .045)');
        halo.addColorStop(1, 'rgba(59, 121, 242, 0)');
        context.save();
        context.translate(centerX, centerY);
        context.scale(1.38, 0.7);
        context.beginPath();
        context.arc(0, 0, scale * 1.15, 0, Math.PI * 2);
        context.fillStyle = halo;
        context.fill();
        context.restore();

        const projected = stars.map((star) =>
            project(star, rotation, scale, centerX, centerY))
            .sort((a, b) => a.z - b.z);

        for (const star of projected) {
            const depth = Math.max(0, Math.min(1, (star.z + 1.25) / 2.5));
            const alpha = (0.27 + depth * 0.73) * star.brightness
                * (1 - focusMix * 0.68)
                * (1 - entityMix * 0.72);
            const radius = Math.max(0.35, star.size * star.depthScale);
            context.beginPath();
            context.arc(star.x, star.y, radius, 0, Math.PI * 2);
            context.fillStyle = star.warmth > 0.93
                ? `rgba(222, 143, 43, ${alpha * 0.92})`
                : `rgba(26, 69, 199, ${alpha})`;
            context.fill();
        }

        const projectedSystems = systems.map((system) => ({
            system,
            projected: project(system, rotation, scale, centerX, centerY),
        }));
        projectedSystems.forEach(({ system, projected: projectedSystem }) => {
            context.save();
            context.globalAlpha = focusedSystem && focusedSystem !== system
                ? 1 - focusMix * 0.88
                : 1;
            context.globalAlpha *= 1 - entityMix * 0.88;
            drawSun(system, projectedSystem);
            context.restore();
            positionSunTarget(system, projectedSystem);
        });
        const focusedProjection = projectedSystems.find(
            (entry) => entry.system === focusedSystem);
        if (focusedProjection) {
            const focusedNodes = drawFocusedSystem(
                focusedSystem,
                focusedProjection.projected,
                projectedSystems,
                rotation,
                scale,
                centerX,
                centerY,
                time,
            );
            const selectedProjection = selectedEntity
                ? focusedNodes.find((node) => node.label === selectedEntity.label)
                : null;
            drawEntityDetail(
                focusedSystem,
                selectedEntity,
                rotation,
                scale,
                centerX,
                centerY,
                time,
                selectedProjection,
                focusedProjection.projected,
            );
        }

        if (!reducedMotion.matches) frame = window.requestAnimationFrame(draw);
    }

    function restart() {
        window.cancelAnimationFrame(frame);
        resize();
        draw(performance.now());
    }

    function setZoom(nextZoom) {
        targetZoom = Math.max(0.68, Math.min(5.4, nextZoom));
        if (reducedMotion.matches) zoom = targetZoom;
        restart();
    }

    function showBreadcrumb(system) {
        if (!breadcrumb || !breadcrumbCurrent) return;
        if (galaxySystemUp) galaxySystemUp.hidden = true;
        if (entitySeparator) entitySeparator.hidden = true;
        breadcrumbCurrent.textContent = system.label;
        breadcrumb.hidden = false;
    }

    function showEntityBreadcrumb(system, entity) {
        if (!breadcrumb || !breadcrumbCurrent || !galaxySystemUp) return;
        galaxySystemUp.textContent = system.label;
        galaxySystemUp.dataset.system = system.label;
        galaxySystemUp.hidden = false;
        if (entitySeparator) entitySeparator.hidden = false;
        breadcrumbCurrent.textContent = entity.label;
        breadcrumb.hidden = false;
    }

    function openSystem(label) {
        window.clearTimeout(travelTimer);
        const system = systems.find((candidate) => candidate.label === label);
        if (!system) return;
        focusedSystem = system;
        highlightedEntity = null;
        selectedEntity = null;
        targetEntityMix = 0;
        targetFocusMix = 1;
        connectionPopover.hidden = true;
        hideEntityPanel();
        showBreadcrumb(system);
        setZoom(2.65);
    }

    function openEntityDetail(systemLabel, entityLabel) {
        const system = systems.find((candidate) => candidate.label === systemLabel);
        const entity = system && system.entityNodes.find(
            (candidate) => candidate.label === entityLabel);
        if (!system || !entity) return;
        focusedSystem = system;
        selectedEntity = entity;
        highlightedEntity = entity.label;
        targetEntityMix = 1;
        targetFocusMix = 1;
        connectionPopover.hidden = true;
        showEntityBreadcrumb(system, entity);
        renderEntityPanel(system, entity);
        setZoom(1.7);
    }

    function travelTo(systemLabel, entityLabel) {
        window.clearTimeout(travelTimer);
        const destination = systems.find((system) => system.label === systemLabel);
        if (!destination) return;
        connectionPopover.hidden = true;
        targetFocusMix = 0.42;
        setZoom(1.18);

        const arrive = () => {
            focusedSystem = destination;
            highlightedEntity = entityLabel;
            selectedEntity = null;
            targetEntityMix = 0;
            targetFocusMix = 1;
            hideEntityPanel();
            showBreadcrumb(destination);
            setZoom(3.05);
        };
        if (reducedMotion.matches) arrive();
        else travelTimer = window.setTimeout(arrive, 380);
    }

    function fitGalaxy() {
        window.clearTimeout(travelTimer);
        focusedSystem = null;
        highlightedEntity = null;
        selectedEntity = null;
        targetEntityMix = 0;
        targetFocusMix = 0;
        connectionPopover.hidden = true;
        hideEntityPanel();
        if (breadcrumb) breadcrumb.hidden = true;
        setZoom(1);
    }

    window.cortexGalaxy = {
        zoomBy(factor) {
            setZoom(targetZoom * factor);
        },
        fit: fitGalaxy,
        open: openSystem,
    };

    sunTargets.forEach((target) => {
        target.addEventListener('click', () => openSystem(target.dataset.galaxySystem));
    });
    if (fieldsTab) fieldsTab.addEventListener('click', () => setDetailTab('fields'));
    if (documentsTab) {
        documentsTab.addEventListener('click', () => setDetailTab('documents'));
    }
    if (historyTab) historyTab.addEventListener('click', () => setDetailTab('history'));
    if (galaxyUp) galaxyUp.addEventListener('click', fitGalaxy);
    if (galaxySystemUp) {
        galaxySystemUp.addEventListener(
            'click', () => openSystem(galaxySystemUp.dataset.system));
    }
    window.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && focusedSystem) fitGalaxy();
    });
    window.addEventListener('resize', restart);
    window.addEventListener('pointermove', (event) => {
        if (reducedMotion.matches) return;
        targetTiltY = 0.14
            + ((event.clientX / Math.max(window.innerWidth, 1)) - 0.5) * 0.42;
        targetTiltX = 0.58
            + ((event.clientY / Math.max(window.innerHeight, 1)) - 0.5) * -0.24;
    }, { passive: true });
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) window.cancelAnimationFrame(frame);
        else restart();
    });
    reducedMotion.addEventListener('change', restart);
    restart();
}());
