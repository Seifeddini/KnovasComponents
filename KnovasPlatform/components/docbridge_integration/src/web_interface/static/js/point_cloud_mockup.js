// Decorative galaxy study for Cortex. It is deliberately independent from
// ontology data and never captures pointer input.
'use strict';

(function () {
    const canvas = document.getElementById('pointCloudMockup');
    if (!canvas) return;

    const context = canvas.getContext('2d', { alpha: true });
    if (!context) return;

    const heatmapCanvas = document.getElementById('galaxyHeatmap');
    const heatmapContext = heatmapCanvas
        ? heatmapCanvas.getContext('2d', { alpha: true })
        : null;
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
    const searchForm = document.getElementById('galaxySearch');
    const searchInput = document.getElementById('galaxySearchInput');
    const searchStatus = document.getElementById('galaxySearchStatus');
    const searchApi = window.CortexGalaxySearch;
    const searchVersion = searchApi
        ? searchApi.versionFromSearch(window.location.search)
        : 1;
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
    let viewFrame = null;
    let heldRotation = null;
    let nodeDrag = null;
    const hiddenStoreKey = 'cortex-galaxy-hidden';
    let hiddenItems = { nodes: [], entities: [], edges: [] };
    try {
        const savedHidden = JSON.parse(localStorage.getItem(hiddenStoreKey) || 'null');
        if (savedHidden && Array.isArray(savedHidden.nodes)) hiddenItems = {
            nodes: savedHidden.nodes,
            entities: savedHidden.entities || [],
            edges: savedHidden.edges || [],
        };
    } catch (err) { /* no saved hides */ }

    function saveHidden() {
        localStorage.setItem(hiddenStoreKey, JSON.stringify(hiddenItems));
        renderHiddenTray();
    }

    function hiddenKeyList(kind) {
        return hiddenItems[kind].map((item) => item.key);
    }

    function nodeKey(system) {
        return system && (system.id || `label:${system.label}`);
    }

    function entityKey(entity) {
        return entity && (entity.id || `label:${entity.label}`);
    }

    function isNodeHidden(system) {
        return hiddenKeyList('nodes').includes(nodeKey(system));
    }

    function isEntityHidden(entity) {
        return hiddenKeyList('entities').includes(entityKey(entity));
    }

    function isEdgeHidden(key) {
        return hiddenKeyList('edges').includes(key);
    }

    function rememberHidden(kind, key, label) {
        if (hiddenKeyList(kind).includes(key)) return;
        hiddenItems[kind].push({ key, label });
        saveHidden();
    }

    function forgetHidden(kind, key) {
        hiddenItems[kind] = hiddenItems[kind].filter((item) => item.key !== key);
        saveHidden();
    }
    let springFrames = 0;
    let springsArmed = false;
    let springPin = null;
    let linkRests = new Map();
    let selectedEntity = null;
    let entityMix = 0;
    let targetEntityMix = 0;
    let historyMix = 0;
    let targetHistoryMix = 0;
    let detailView = 'fields';
    let travelTimer = 0;
    let searchState = {
        query: '',
        level: 'idle',
        results: [],
        system: null,
        entity: null,
    };

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
            x: -0.46, y: -0.02, z: 0.08, label: 'Mandate', primary: true, warm: true,
            entities: [
                {
                    label: 'Müller Bau AG · Werklohnstreit',
                    number: 'M-2026-041', area: 'Bau- und Werkvertragsrecht',
                    status: 'Rechtshängig', lead: 'Dr. Anna Keller',
                    value: 'CHF 1’850’000', deadline: 'Klageantwort · 30. Oktober 2026',
                    opened: '12. Februar 2026',
                    connection: { system: 'Gerichte', entity: 'Handelsgericht Zürich' },
                },
                {
                    label: 'Meier Immobilien · Mietzinsverfahren',
                    number: 'M-2026-038', area: 'Mietrecht', status: 'Schlichtung',
                    lead: 'Sofia Rossi', value: 'CHF 186’000',
                    deadline: 'Schlichtungsverhandlung · 4. November 2026',
                    opened: '29. Januar 2026',
                },
                {
                    label: 'NovaTech AG · Beteiligungsrunde',
                    number: 'M-2026-057', area: 'Gesellschaftsrecht', status: 'Due Diligence',
                    lead: 'Dr. Anna Keller', value: 'CHF 8’500’000',
                    deadline: 'Signing · 20. November 2026', opened: '18. März 2026',
                },
                {
                    label: 'Familie Baumann · Nachlass',
                    number: 'M-2025-119', area: 'Erbrecht', status: 'Teilung in Vorbereitung',
                    lead: 'Daniel Weber', value: 'CHF 3’240’000',
                    deadline: 'Erbteilungsvertrag · 12. Dezember 2026',
                    opened: '7. November 2025',
                },
                {
                    label: 'Stadtwerke Nord · Vergabebeschwerde',
                    number: 'M-2026-063', area: 'Öffentliches Beschaffungsrecht',
                    status: 'Beschwerdeverfahren', lead: 'Thomas Frei',
                    value: 'CHF 12’400’000', deadline: 'Replik · 23. Oktober 2026',
                    opened: '6. April 2026',
                },
                {
                    label: 'Alpenblick Klinik · Arbeitsstreit',
                    number: 'M-2026-071', area: 'Arbeitsrecht', status: 'Vergleichsgespräche',
                    lead: 'Sofia Rossi', value: 'CHF 245’000',
                    deadline: 'Vergleichsvorschlag · 27. Oktober 2026',
                    opened: '2. Mai 2026',
                },
                {
                    label: 'Keller Holding · Steuerreorganisation',
                    number: 'M-2026-029', area: 'Steuer- und Gesellschaftsrecht',
                    status: 'In Bearbeitung', lead: 'Daniel Weber',
                    value: 'CHF 24’000’000', deadline: 'Ruling-Antrag · 6. November 2026',
                    opened: '14. Januar 2026',
                },
                {
                    label: 'GreenGrid GmbH · Lizenzvertrag',
                    number: 'M-2026-084', area: 'IT- und Immaterialgüterrecht',
                    status: 'Vertragsverhandlung', lead: 'Thomas Frei',
                    value: 'CHF 960’000', deadline: 'Redline-Fassung · 21. Oktober 2026',
                    opened: '17. Juni 2026',
                    connection: { system: 'Gegenparteien', entity: 'Helvetia Projektentwicklung AG' },
                },
            ],
        },
        {
            x: 0.44, y: 0.015, z: -0.12, label: 'Mandanten', primary: true, warm: false,
            entities: [
                {
                    label: 'Müller Bau AG', number: 'K-10041', legalForm: 'Aktiengesellschaft',
                    location: 'Winterthur ZH', contact: 'Markus Müller, CEO',
                    email: 'markus.mueller@muellerbau.example', activeMatters: '3',
                    since: '2018', industry: 'Bau und Generalunternehmung',
                    connection: { system: 'Mandate', entity: 'Müller Bau AG · Werklohnstreit' },
                },
                {
                    label: 'Meier Immobilien AG', number: 'K-10127',
                    legalForm: 'Aktiengesellschaft', location: 'Zürich',
                    contact: 'Markus Meier, Verwaltungsrat',
                    email: 'markus.meier@meier-immo.example', activeMatters: '5',
                    since: '2020', industry: 'Immobilienverwaltung',
                },
                {
                    label: 'NovaTech AG', number: 'K-10318', legalForm: 'Aktiengesellschaft',
                    location: 'Zug', contact: 'Dr. Lea Hartmann, CFO',
                    email: 'lea.hartmann@novatech.example', activeMatters: '2',
                    since: '2024', industry: 'Software und Medizintechnik',
                },
                {
                    label: 'Erbengemeinschaft Baumann', number: 'K-09984',
                    legalForm: 'Erbengemeinschaft', location: 'Küsnacht ZH',
                    contact: 'Lisa Baumann', email: 'l.baumann@example.ch',
                    activeMatters: '1', since: '2025', industry: 'Privatmandat',
                    matters: 'M-2025-119',
                    connection: { system: 'Mandate', entity: 'Familie Baumann · Nachlass' },
                },
                {
                    label: 'Stadtwerke Nord', number: 'K-10256',
                    legalForm: 'Selbständige Anstalt', location: 'Schaffhausen',
                    contact: 'Claudia Brunner, Leiterin Recht',
                    email: 'claudia.brunner@stadtwerke-nord.example',
                    activeMatters: '4', since: '2022', industry: 'Energieversorgung',
                },
                {
                    label: 'Alpenblick Klinik AG', number: 'K-10344',
                    legalForm: 'Aktiengesellschaft', location: 'Zürich',
                    contact: 'Dr. Miriam Vogt, Direktorin',
                    email: 'miriam.vogt@alpenblick.example', activeMatters: '2',
                    since: '2025', industry: 'Gesundheitswesen',
                },
                {
                    label: 'Keller Holding AG', number: 'K-09762',
                    legalForm: 'Aktiengesellschaft', location: 'Zug',
                    contact: 'Urs Keller, Verwaltungsratspräsident',
                    email: 'urs.keller@keller-holding.example', activeMatters: '7',
                    since: '2016', industry: 'Industriebeteiligungen',
                },
                {
                    label: 'GreenGrid GmbH', number: 'K-10391',
                    legalForm: 'Gesellschaft mit beschränkter Haftung',
                    location: 'Baden AG', contact: 'Nina Graf, Geschäftsführerin',
                    email: 'nina.graf@greengrid.example', activeMatters: '2',
                    since: '2026', industry: 'Energietechnologie',
                    connection: { system: 'Personen', entity: 'Thomas Frei' },
                },
            ],
        },
        {
            x: -0.08, y: 0.025, z: 0.5, label: 'Personen', primary: true, warm: false,
            entities: [
                {
                    label: 'Dr. Anna Keller', role: 'Partnerin · Fallverantwortung',
                    organisation: 'Kanzlei Keller & Partner', phone: '+41 44 555 01 10',
                    email: 'anna.keller@kanzlei.example', matters: 'M-2026-041, M-2026-057',
                    expertise: 'Prozessführung, Gesellschaftsrecht',
                    connection: { system: 'Mandate', entity: 'Müller Bau AG · Werklohnstreit' },
                },
                {
                    label: 'Markus Müller', role: 'CEO · Mandantenvertretung',
                    organisation: 'Müller Bau AG', phone: '+41 52 555 18 20',
                    email: 'markus.mueller@muellerbau.example', matters: 'M-2026-041',
                    expertise: 'Bauleitung und Unternehmensführung',
                },
                {
                    label: 'Sofia Rossi', role: 'Rechtsanwältin · Senior Associate',
                    organisation: 'Kanzlei Keller & Partner', phone: '+41 44 555 01 24',
                    email: 'sofia.rossi@kanzlei.example', matters: 'M-2026-038, M-2026-071',
                    expertise: 'Arbeits- und Mietrecht',
                },
                {
                    label: 'Daniel Weber', role: 'Rechtsanwalt · Counsel',
                    organisation: 'Kanzlei Keller & Partner', phone: '+41 44 555 01 16',
                    email: 'daniel.weber@kanzlei.example', matters: 'M-2025-119, M-2026-029',
                    expertise: 'Erbrecht und Steuerrecht',
                },
                {
                    label: 'Thomas Frei', role: 'Rechtsanwalt · Associate',
                    organisation: 'Kanzlei Keller & Partner', phone: '+41 44 555 01 31',
                    email: 'thomas.frei@kanzlei.example', matters: 'M-2026-063, M-2026-084',
                    expertise: 'Öffentliches Recht und IT-Recht',
                    connection: { system: 'Mandanten', entity: 'GreenGrid GmbH' },
                },
                {
                    label: 'lic. iur. Eva Schmid', role: 'Gegenanwältin',
                    organisation: 'Schmid Legal AG', phone: '+41 44 555 74 12',
                    email: 'eva.schmid@schmid-legal.example', matters: 'M-2026-041',
                    expertise: 'Bau- und Immobilienrecht',
                },
                {
                    label: 'Prof. Dr. Reto Haller', role: 'Privatgutachter',
                    organisation: 'Haller Bauexpertise GmbH', phone: '+41 31 555 22 08',
                    email: 'reto.haller@bauexpertise.example', matters: 'M-2026-041',
                    expertise: 'Baumängel und Kostenplanung',
                },
                {
                    label: 'Nina Graf', role: 'Geschäftsführerin',
                    organisation: 'GreenGrid GmbH', phone: '+41 56 555 08 42',
                    email: 'nina.graf@greengrid.example', matters: 'M-2026-084',
                    expertise: 'Energiesysteme und Lizenzierung',
                },
            ],
        },
        {
            x: 0.14, y: -0.03, z: -0.62, label: 'Gerichte', primary: true, warm: false,
            entities: [
                {
                    label: 'Handelsgericht Zürich', level: 'Kantonales Fachgericht',
                    location: 'Zürich', jurisdiction: 'Handelsrecht',
                    reference: 'HG260184-O', status: 'Schriftenwechsel',
                    judge: 'Handelsrichterin Dr. M. Steiner',
                    nextDate: 'Klageantwort · 30. Oktober 2026',
                    connection: { system: 'Mandate', entity: 'Müller Bau AG · Werklohnstreit' },
                },
                {
                    label: 'Mietgericht Zürich', level: 'Bezirksgericht',
                    location: 'Zürich', jurisdiction: 'Miet- und Pachtrecht',
                    reference: 'MJ260392-L', status: 'Sistiert bis Schlichtung',
                    judge: 'Bezirksrichter P. Egli', nextDate: 'Statusbericht · 6. November 2026',
                },
                {
                    label: 'Obergericht Zürich', level: 'Kantonales Obergericht',
                    location: 'Zürich', jurisdiction: 'Zivil- und Strafrecht',
                    reference: 'LB260071-O/U', status: 'Berufungsverfahren',
                    judge: 'I. Zivilkammer', nextDate: 'Berufungsantwort · 18. November 2026',
                },
                {
                    label: 'Bundesgericht', level: 'Oberstes Gericht',
                    location: 'Lausanne', jurisdiction: 'Bundesrecht',
                    reference: '4A_418/2026', status: 'Instruktion',
                    judge: 'I. zivilrechtliche Abteilung',
                    nextDate: 'Kostenvorschuss · 28. Oktober 2026',
                },
                {
                    label: 'Arbeitsgericht Zürich', level: 'Bezirksgericht',
                    location: 'Zürich', jurisdiction: 'Arbeitsrecht',
                    reference: 'AN260066-L', status: 'Vergleichsverhandlung',
                    judge: 'Bezirksrichterin S. Frei',
                    nextDate: 'Verhandlung · 11. November 2026',
                    connection: { system: 'Mandate', entity: 'Alpenblick Klinik · Arbeitsstreit' },
                },
                {
                    label: 'Verwaltungsgericht Schaffhausen', level: 'Kantonales Gericht',
                    location: 'Schaffhausen', jurisdiction: 'Verwaltungsrecht',
                    reference: 'VGE 2026/83', status: 'Beschwerdeverfahren',
                    judge: '2. Kammer', nextDate: 'Replik · 23. Oktober 2026',
                },
                {
                    label: 'Schiedsgericht Swiss Rules', level: 'Institutionelles Schiedsgericht',
                    location: 'Zürich', jurisdiction: 'Vertragsrecht',
                    reference: 'ASA 600721-2026', status: 'Konstituiert',
                    judge: 'Einzelschiedsrichterin C. Rey',
                    nextDate: 'Case Management Conference · 2. November 2026',
                },
                {
                    label: 'Friedensrichteramt Kreis 7', level: 'Schlichtungsbehörde',
                    location: 'Zürich', jurisdiction: 'Zivilsachen',
                    reference: 'GV.2026.00418', status: 'Verhandlung terminiert',
                    judge: 'Friedensrichter M. Ott', nextDate: 'Verhandlung · 4. November 2026',
                },
            ],
        },
        {
            x: 0.76, y: 0.01, z: 0.28, label: 'Gegenparteien', primary: true, warm: false,
            entities: [
                {
                    label: 'Helvetia Projektentwicklung AG', legalForm: 'Aktiengesellschaft',
                    location: 'Zürich', counsel: 'Schmid Legal AG',
                    contact: 'lic. iur. Eva Schmid', matter: 'M-2026-041',
                    position: 'Bestreitet Mehrvergütung und Fristverlängerung',
                    connection: { system: 'Mandate', entity: 'Müller Bau AG · Werklohnstreit' },
                },
                {
                    label: 'Wohnbaugenossenschaft Limmat', legalForm: 'Genossenschaft',
                    location: 'Zürich', counsel: 'Intern vertreten',
                    contact: 'Andreas Roth, Leiter Bewirtschaftung', matter: 'M-2026-038',
                    position: 'Beruft sich auf umfassende Sanierung',
                },
                {
                    label: 'Apex Ventures II LP', legalForm: 'Limited Partnership',
                    location: 'London, UK', counsel: 'Baker & Rowe LLP',
                    contact: 'James Cole, Partner', matter: 'M-2026-057',
                    position: 'Investorin und federführende Vertragspartei',
                },
                {
                    label: 'Peter Baumann', legalForm: 'Privatperson',
                    location: 'Bern', counsel: 'Advokatur am Bundesplatz',
                    contact: 'RA Dr. Nicole Wenger', matter: 'M-2025-119',
                    position: 'Beantragt Anrechnung lebzeitiger Zuwendungen',
                },
                {
                    label: 'Energia Solutions AG', legalForm: 'Aktiengesellschaft',
                    location: 'Basel', counsel: 'Hofmann Rechtsanwälte',
                    contact: 'RA Lukas Hofmann', matter: 'M-2026-063',
                    position: 'Zuschlagsempfängerin im Vergabeverfahren',
                    connection: { system: 'Gerichte', entity: 'Verwaltungsgericht Schaffhausen' },
                },
                {
                    label: 'Dr. Felix Marti', legalForm: 'Privatperson',
                    location: 'Zollikon ZH', counsel: 'Wenger Arbeitsrecht',
                    contact: 'RA Corinne Wenger', matter: 'M-2026-071',
                    position: 'Macht missbräuchliche Kündigung geltend',
                },
                {
                    label: 'Kantonales Steueramt Zürich', legalForm: 'Behörde',
                    location: 'Zürich', counsel: 'Rechtsdienst',
                    contact: 'Dr. Jürg Ammann', matter: 'M-2026-029',
                    position: 'Prüft steuerneutrale Umstrukturierung',
                },
                {
                    label: 'Gridware Europe S.A.', legalForm: 'Société anonyme',
                    location: 'Lausanne', counsel: 'LexTech Avocats',
                    contact: 'Me Camille Morel', matter: 'M-2026-084',
                    position: 'Lizenzgeberin der Plattformtechnologie',
                },
            ],
        },
        {
            x: -0.82, y: -0.02, z: -0.27, label: 'Behörden', primary: true, warm: false,
            entities: [
                {
                    label: 'Handelsregisteramt Zürich', kind: 'Kantonale Registerbehörde',
                    location: 'Zürich', reference: 'CHE-108.445.219',
                    matter: 'M-2026-057', status: 'Voranfrage eingereicht',
                    nextDate: 'Rückmeldung erwartet · 23. Oktober 2026',
                    connection: { system: 'Mandanten', entity: 'NovaTech AG' },
                },
                {
                    label: 'Kantonales Steueramt Zürich', kind: 'Kantonale Steuerbehörde',
                    location: 'Zürich', reference: 'RUL-2026-8841',
                    matter: 'M-2026-029', status: 'Ruling in Prüfung',
                    nextDate: 'Ergänzende Unterlagen · 6. November 2026',
                },
                {
                    label: 'WEKO', kind: 'Bundesbehörde',
                    location: 'Bern', reference: 'RPW 2026/4-118',
                    matter: 'M-2026-057', status: 'Informelle Vorprüfung',
                    nextDate: 'Marktabgrenzung einreichen · 30. Oktober 2026',
                },
                {
                    label: 'Eidgenössisches Institut für Geistiges Eigentum',
                    kind: 'Bundesinstitut', location: 'Bern', reference: 'TM-824771',
                    matter: 'M-2026-084', status: 'Markenanmeldung publiziert',
                    nextDate: 'Widerspruchsfrist · 14. Dezember 2026',
                },
                {
                    label: 'Baupolizeiamt Winterthur', kind: 'Kommunale Behörde',
                    location: 'Winterthur', reference: 'BAP-2025-1948',
                    matter: 'M-2026-041', status: 'Akteneinsicht gewährt',
                    nextDate: 'Planeinsicht · 22. Oktober 2026',
                    connection: { system: 'Mandate', entity: 'Müller Bau AG · Werklohnstreit' },
                },
                {
                    label: 'Zivilstandsamt Küsnacht', kind: 'Kommunale Behörde',
                    location: 'Küsnacht ZH', reference: 'NZ-2025-441',
                    matter: 'M-2025-119', status: 'Urkunden vollständig',
                    nextDate: 'Keine offene Frist',
                },
                {
                    label: 'Amt für Wirtschaft und Arbeit', kind: 'Kantonale Behörde',
                    location: 'Zürich', reference: 'AWA-AR-2026-319',
                    matter: 'M-2026-071', status: 'Stellungnahme ausstehend',
                    nextDate: 'Stellungnahme · 29. Oktober 2026',
                },
                {
                    label: 'Eidgenössisches Starkstrominspektorat',
                    kind: 'Aufsichtsbehörde', location: 'Fehraltorf ZH',
                    reference: 'ESTI-2026-7714', matter: 'M-2026-063',
                    status: 'Technische Prüfung', nextDate: 'Prüfbericht · 9. November 2026',
                },
            ],
        },
        { x: 0.22, y: 0.02, z: 0.18 },
    ];

    function mountSystems(definitions) {
        for (let index = stars.length - 1; index >= 0; index -= 1) {
            if (stars[index].systemOrbit) stars.splice(index, 1);
        }
        systems.length = 0;
        definitions.forEach((definition, systemIndex) => {
        const system = {
            ...definition,
            size: definition.size || (definition.primary ? 8.5 : 3.2 + random() * 1.8),
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
                systemOrbit: true,
            });
        }
        (definition.entities || []).forEach((entity, index, entities) => {
            const angle = (index / entities.length) * Math.PI * 2 + systemIndex * 0.36;
            const ring = 0.2 + (index % 2) * 0.075;
            system.entityNodes.push({
                ...entity,
                x: definition.x + Math.cos(angle) * ring,
                y: definition.y + ((index % 3) - 1) * 0.012,
                z: definition.z + Math.sin(angle) * ring * 0.74,
                size: 4.1 + (index % 3) * 0.45,
            });
        });
        systems.push(system);
        stars.push(...system.orbit);
        });
        rebuildSunTargets();
        if (focusedSystem && !systems.includes(focusedSystem)) {
            focusedSystem = null;
            selectedEntity = null;
            highlightedEntity = null;
            targetFocusMix = 0;
            targetEntityMix = 0;
            hideEntityPanel();
        }
    }

    const documents = searchVersion === 1 || !searchApi
        ? []
        : searchApi.placeDocuments(systems, searchApi.DOCUMENTS);

    function normalizeSearchValue(value) {
        return String(value || '')
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .replace(/ß/g, 'ss')
            .toLocaleLowerCase('de-CH')
            .replace(/[^\p{L}\p{N}]+/gu, ' ')
            .trim();
    }

    function rankSearch(query) {
        const normalizedQuery = normalizeSearchValue(query);
        const tokens = normalizedQuery.split(/\s+/).filter(Boolean);
        return systems.map((system) => {
            const systemLabel = normalizeSearchValue(system.label);
            const entityResults = system.entityNodes.map((entity) => {
                const label = normalizeSearchValue(entity.label);
                const details = normalizeSearchValue(
                    Object.entries(entity)
                        .filter(([key, value]) =>
                            key !== 'label' && typeof value === 'string')
                        .map(([, value]) => value)
                        .join(' '),
                );
                const connection = entity.connection
                    ? normalizeSearchValue(
                        `${entity.connection.system} ${entity.connection.entity}`)
                    : '';
                let score = 0;
                let directScore = 0;
                let matchedTokens = 0;
                let directMatchedTokens = 0;
                tokens.forEach((token) => {
                    let tokenDirectScore = 0;
                    if (label.includes(token)) tokenDirectScore += 5;
                    if (label.startsWith(token)) tokenDirectScore += 2;
                    if (details.includes(token)) tokenDirectScore += 2;
                    const connectionScore = connection.includes(token) ? 1 : 0;
                    const tokenScore = tokenDirectScore + connectionScore;
                    if (tokenScore > 0) matchedTokens += 1;
                    if (tokenDirectScore > 0) directMatchedTokens += 1;
                    score += tokenScore;
                    directScore += tokenDirectScore;
                });
                if (normalizedQuery && label.includes(normalizedQuery)) {
                    score += 5;
                    directScore += 5;
                }
                return {
                    entity,
                    score,
                    directScore,
                    matchedTokens,
                    directMatchedTokens,
                };
            }).sort((a, b) => b.score - a.score);
            let systemScore = entityResults[0] ? entityResults[0].score : 0;
            let systemDirectScore = Math.max(
                0,
                ...entityResults.map((entry) => entry.directScore),
            );
            if (entityResults[1]) systemScore += entityResults[1].score * 0.16;
            tokens.forEach((token) => {
                if (systemLabel.includes(token)) {
                    systemScore += 7;
                    systemDirectScore += 7;
                }
                if (systemLabel.startsWith(token)) {
                    systemScore += 2;
                    systemDirectScore += 2;
                }
            });
            if (normalizedQuery && systemLabel.includes(normalizedQuery)) {
                systemScore += 6;
                systemDirectScore += 6;
            }
            return {
                system,
                score: systemScore,
                directScore: systemDirectScore,
                entities: entityResults,
                tokenCount: tokens.length,
            };
        }).sort((a, b) => b.score - a.score);
    }

    function setSearchStatus(message) {
        if (searchStatus) searchStatus.textContent = message;
    }

    function applySearch(value, forceDetail = false) {
        if (searchVersion !== 1) return;
        const query = String(value || '').trim();
        const normalizedQuery = normalizeSearchValue(query);
        document.body.classList.toggle('galaxy-searching', Boolean(normalizedQuery));

        if (!normalizedQuery) {
            searchState = {
                query: '',
                level: 'idle',
                results: [],
                system: null,
                entity: null,
            };
            setSearchStatus('Galaxie durchsuchen');
            fitGalaxy();
            return;
        }

        const results = rankSearch(query);
        const positiveResults = results.filter((result) => result.score > 0);
        const directSystemResults = results
            .filter((result) => result.directScore > 0)
            .sort((a, b) => b.directScore - a.directScore);
        const topSystem = directSystemResults[0] || positiveResults[0] || null;
        const secondSystem = directSystemResults[1] || null;
        const globalEntities = results.flatMap((result) =>
            result.entities.map((entry) => ({
                ...entry,
                system: result.system,
                tokenCount: result.tokenCount,
            })))
            .filter((entry) => entry.score > 0)
            .sort((a, b) => b.score - a.score);
        const directEntities = globalEntities
            .filter((entry) => entry.directScore > 0)
            .sort((a, b) => b.directScore - a.directScore);
        const topEntity = directEntities[0] || null;
        const secondEntity = directEntities[1] || null;
        const allEntityTokensMatch = topEntity
            && topEntity.directMatchedTokens === topEntity.tokenCount;
        const entityIsDistinct = topEntity && (
            !secondEntity
            || topEntity.directScore - secondEntity.directScore >= 4
            || topEntity.directScore >= secondEntity.directScore * 1.28
        );
        const systemIsDistinct = topSystem && (
            !secondSystem
            || topSystem.directScore - secondSystem.directScore >= 3
            || topSystem.directScore >= secondSystem.directScore * 1.2
        );
        const systemLabelMatches = topSystem
            && normalizeSearchValue(topSystem.system.label).includes(normalizedQuery);
        const strongEntitySignal = topEntity && allEntityTokensMatch
            && entityIsDistinct && topEntity.directScore >= 8;
        const showEntity = topEntity && allEntityTokensMatch && entityIsDistinct
            && ((normalizedQuery.length >= 6 && topEntity.directScore >= 8)
                || (forceDetail && normalizedQuery.length >= 3));
        const showSystem = topSystem
            && directSystemResults.length
            && (systemLabelMatches
                || (normalizedQuery.length >= 3
                    && topSystem.directScore >= 5
                    && (systemIsDistinct || strongEntitySignal))
                || forceDetail);

        if (showEntity) {
            searchState = {
                query,
                level: 'entity',
                results,
                system: topEntity.system,
                entity: topEntity.entity,
            };
            setSearchStatus(
                `${topEntity.system.label} · Detailtreffer ${topEntity.entity.label}`,
            );
            if (!selectedEntity
                    || selectedEntity.label !== topEntity.entity.label
                    || focusedSystem !== topEntity.system) {
                openEntityDetail(topEntity.system.label, topEntity.entity.label);
            } else {
                restart();
            }
            return;
        }

        if (showSystem) {
            const bestEntity = topSystem.entities.find((entry) => entry.score > 0);
            searchState = {
                query,
                level: 'system',
                results,
                system: topSystem.system,
                entity: bestEntity ? bestEntity.entity : null,
            };
            setSearchStatus(
                `Eingegrenzt auf ${topSystem.system.label} · Heatmap aktiv`,
            );
            if (focusedSystem !== topSystem.system || selectedEntity) {
                openSystem(topSystem.system.label);
            }
            highlightedEntity = bestEntity ? bestEntity.entity.label : null;
            restart();
            return;
        }

        searchState = {
            query,
            level: 'galaxy',
            results,
            system: null,
            entity: null,
        };
        if (positiveResults.length) {
            setSearchStatus(
                `${positiveResults.length} Wissensräume werden gewichtet`,
            );
        } else {
            setSearchStatus('Noch kein eindeutiger Bereich · Galaxie wird geprüft');
        }
        fitGalaxy();
    }

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
    const entityTargets = [];

    function ensureEntityTargets(count) {
        const host = canvas.parentElement;
        if (!host) return;
        while (entityTargets.length < count) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'galaxy-entity-target';
            button.hidden = true;
            bindEntityDrag(button);
            host.appendChild(button);
            entityTargets.push(button);
        }
    }
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
        if (entity.live) {
            return [['Eintrag', [
                ['Name', entity.label],
                ['Typ', system.label],
                ['Einträge im Typ', entity.typeCount || '—'],
            ]]];
        }
        if (system.label === 'Mandate') {
            return [
                ['Akte', [
                    ['Mandatsnummer', entity.number],
                    ['Rechtsgebiet', entity.area],
                    ['Status', entity.status],
                    ['Eröffnet', entity.opened],
                ]],
                ['Verantwortung & Fristen', [
                    ['Fallverantwortung', entity.lead],
                    ['Streit-/Transaktionswert', entity.value],
                    ['Nächste Frist', entity.deadline],
                    ['Konfliktprüfung', 'Abgeschlossen · keine Treffer'],
                ]],
            ];
        }
        if (system.label === 'Mandanten') {
            return [
                ['Stammdaten', [
                    ['Kundennummer', entity.number],
                    ['Rechtsform', entity.legalForm],
                    ['Sitz / Wohnort', entity.location],
                    ['Branche', entity.industry],
                ]],
                ['Mandatsbeziehung', [
                    ['Hauptkontakt', entity.contact],
                    ['E-Mail', entity.email],
                    ['Aktive Mandate', entity.activeMatters],
                    ['Mandant seit', entity.since],
                ]],
            ];
        }
        if (system.label === 'Personen') {
            return [
                ['Kontakt', [
                    ['Name', entity.label],
                    ['Funktion', entity.role],
                    ['Organisation', entity.organisation],
                    ['Telefon', entity.phone],
                    ['E-Mail', entity.email],
                ]],
                ['Zuordnung', [
                    ['Mandate', entity.matters],
                    ['Fachgebiet', entity.expertise],
                    ['Status', 'Aktiv · Identität verifiziert'],
                ]],
            ];
        }
        if (system.label === 'Gerichte') {
            return [
                ['Stammdaten', [
                    ['Bezeichnung', entity.label],
                    ['Gerichtsebene', entity.level],
                    ['Ort', entity.location],
                    ['Zuständigkeit', entity.jurisdiction],
                ]],
                ['Verfahren', [
                    ['Aktenzeichen', entity.reference],
                    ['Status', entity.status],
                    ['Spruchkörper', entity.judge],
                    ['Nächster Termin', entity.nextDate],
                ]],
            ];
        }
        if (system.label === 'Gegenparteien') {
            return [
                ['Stammdaten', [
                    ['Name / Firma', entity.label],
                    ['Rechtsform', entity.legalForm],
                    ['Sitz / Wohnort', entity.location],
                    ['Zugeordnetes Mandat', entity.matter],
                ]],
                ['Vertretung & Position', [
                    ['Rechtsvertretung', entity.counsel],
                    ['Kontakt', entity.contact],
                    ['Verfahrensposition', entity.position],
                    ['Konfliktstatus', 'Geprüft · Gegenpartei'],
                ]],
            ];
        }
        return [
            ['Behörde', [
                ['Bezeichnung', entity.label],
                ['Behördentyp', entity.kind],
                ['Ort', entity.location],
                ['Geschäftsnummer', entity.reference],
            ]],
            ['Vorgang', [
                ['Zugeordnetes Mandat', entity.matter],
                ['Status', entity.status],
                ['Nächster Termin', entity.nextDate],
            ]],
        ];
    }

    function documentsFor(system, entity) {
        if (entity.live) return [];
        if (system.label === 'Mandate') {
            return [
                [`Mandatsvereinbarung ${entity.number}.pdf`, 'Mandatsakte · unterzeichnet'],
                ['Sachverhaltschronologie.pdf', 'Arbeitsprodukt · aktualisiert heute'],
                ['Rechtliche Würdigung v4.docx', `Memo · ${entity.area}`],
                ['Beilagenverzeichnis.xlsx', 'Beweismittel · 42 Positionen'],
                ['Korrespondenz Gegenpartei.msg', 'E-Mail · 15. Oktober 2026'],
                ['Vollmacht.pdf', 'Vollmacht · qualifiziert signiert'],
            ];
        }
        if (system.label === 'Mandanten') {
            return [
                [`Mandantenstammblatt ${entity.number}.pdf`, 'KYC · geprüft'],
                ['Handelsregisterauszug.pdf', 'Registerauszug · aktuell'],
                ['Vollmacht Rahmenmandat.pdf', 'Vollmacht · unterzeichnet'],
                ['Konfliktprüfung.pdf', 'Compliance · freigegeben'],
                ['Honorarvereinbarung.pdf', 'Vertrag · vertraulich'],
            ];
        }
        if (system.label === 'Personen') {
            return [
                [`Vollmacht ${entity.label}.pdf`, 'Vollmacht · 8. Oktober 2026'],
                [`Kontaktakte ${entity.label}.pdf`, `CRM · ${entity.organisation}`],
                ['Korrespondenz zur Sache.msg', `E-Mail · ${entity.matters}`],
                ['Besprechungsnotiz 08-10-2026.pdf', 'Aktennotiz · 8. Oktober 2026'],
            ];
        }
        if (system.label === 'Gerichte') {
            return [
                [`Verfügung ${entity.reference}.pdf`, `Verfügung · ${entity.status}`],
                ['Eingangsbestätigung Rechtsschrift.pdf', 'Gerichtspost · 14. Oktober 2026'],
                ['Zustellnachweis.pdf', 'eGov-Zustellung · verifiziert'],
                ['Fristenkontrollblatt.pdf', `Frist · ${entity.nextDate}`],
                ['Verfahrenskorrespondenz.msg', `Korrespondenz · ${entity.judge}`],
            ];
        }
        if (system.label === 'Gegenparteien') {
            return [
                [`Parteidossier ${entity.label}.pdf`, 'Parteienakte · vertraulich'],
                ['Korrespondenz Rechtsvertretung.msg', `E-Mail · ${entity.counsel}`],
                ['Stellungnahme Gegenpartei.pdf', `Rechtsschrift · ${entity.matter}`],
                ['Konfliktprüfung.pdf', 'Compliance · dokumentiert'],
            ];
        }
        return [
            [`Eingabe ${entity.reference}.pdf`, `Behördeneingabe · ${entity.matter}`],
            ['Empfangsbestätigung.pdf', 'eGov-Zustellung · 14. Oktober 2026'],
            ['Aktennotiz Behördenkontakt.pdf', `Notiz · ${entity.status}`],
            ['Fristennotiz.ics', `Kalender · ${entity.nextDate}`],
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

    function renderHistory(system, entity) {
        if (!entityHistory) return;
        entityHistory.replaceChildren();
        const list = document.createElement('ol');
        list.className = 'galaxy-history-list';
        const contextualEvents = {
            Mandate: [
                `Fristenprüfung für ${entity.number} abgeschlossen`,
                `Neue Korrespondenz durch ${entity.lead} abgelegt`,
                `Status auf „${entity.status}“ aktualisiert`,
            ],
            Mandanten: [
                `KYC-Daten für ${entity.number} bestätigt`,
                `${entity.activeMatters} aktive Mandate abgeglichen`,
                'Handelsregisterdaten automatisch aktualisiert',
            ],
            Personen: [
                `Kontakt ${entity.label} einem Dokument zugeordnet`,
                `Mandatsbezug ${entity.matters} bestätigt`,
                `Kontaktdaten aus Signatur ergänzt`,
            ],
            Gerichte: [
                `Gerichtsfrist zu ${entity.reference} kontrolliert`,
                `Neue Verfügung dem Verfahren ${entity.reference} zugeordnet`,
                `Verfahrensstatus „${entity.status}“ übernommen`,
            ],
            Gegenparteien: [
                `Parteistellung in ${entity.matter} bestätigt`,
                `Korrespondenz von ${entity.counsel} importiert`,
                'Konfliktprüfung dokumentiert',
            ],
            Behörden: [
                `Geschäft ${entity.reference} synchronisiert`,
                `Status „${entity.status}“ übernommen`,
                `Behördenkontakt dem Mandat ${entity.matter} zugeordnet`,
            ],
        };
        const events = entity.live ? [] : (contextualEvents[system.label] || []);
        [
            ['Heute, 09:42', `${entity.label} im Wissensnetz geöffnet`],
            ['15. Okt.', events[0]],
            ['8. Okt.', events[1]],
            ['26. Sept.', events[2]],
            ['4. Sept.', 'Entität erstmals aus Dokumenten erkannt'],
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
        renderHistory(system, entity);
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
        if (heatmapCanvas && heatmapContext) {
            heatmapCanvas.width = Math.round(width * pixelRatio);
            heatmapCanvas.height = Math.round(height * pixelRatio);
            heatmapContext.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
        }
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
        const fill = warm ? '#f9ab00' : '#1a73e8';
        const outline = warm ? '#e37400' : '#0b57d0';

        context.save();
        context.beginPath();
        context.arc(projected.x, projected.y, radius * 2.25, 0, Math.PI * 2);
        context.fillStyle = warm
            ? 'rgba(249, 171, 0, .12)'
            : 'rgba(26, 115, 232, .12)';
        context.fill();

        context.shadowColor = 'rgba(60, 64, 67, .24)';
        context.shadowBlur = 8;
        context.shadowOffsetY = 2;
        context.beginPath();
        context.arc(projected.x, projected.y, radius, 0, Math.PI * 2);
        context.fillStyle = fill;
        context.fill();
        context.shadowColor = 'transparent';
        context.strokeStyle = outline;
        context.lineWidth = 1.25;
        context.stroke();
        context.restore();

        if (!system.label) return;
        context.font = '600 12px "IBM Plex Mono", ui-monospace, monospace';
        context.textAlign = 'center';
        context.textBaseline = 'top';
        context.fillStyle = '#283647';
        context.fillText(shortLabel(system.label, 22), projected.x, projected.y + radius + 9);
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
            button.dataset.entityIndex = String(index);
        }
    }

    function canvasPoint(event) {
        const rect = canvas.getBoundingClientRect();
        return {
            x: event.clientX - rect.left,
            y: event.clientY - rect.top,
        };
    }

    function screenToDisk(sx, sy, depthScale) {
        const view = viewFrame;
        if (!view || !depthScale) return null;
        const rx = (sx - view.centerX) / (view.scale * depthScale);
        const ry = (sy - view.centerY) / (view.scale * depthScale);
        const rotationY = view.rotation + view.tiltY;
        const sinX = Math.sin(view.tiltX);
        const cosY = Math.cos(rotationY);
        const sinY = Math.sin(rotationY);
        const z1 = Math.abs(sinX) > 0.2 ? -ry / sinX : 0;
        const x1 = rx;
        return {
            x: x1 * cosY + z1 * sinY,
            z: -x1 * sinY + z1 * cosY,
        };
    }

    function beginNodeDrag(event, payload) {
        if (event.button != null && event.button !== 0) return;
        const point = canvasPoint(event);
        nodeDrag = {
            ...payload,
            pointerId: event.pointerId,
            button: event.currentTarget && event.currentTarget.classList
                ? event.currentTarget : null,
            startX: event.clientX,
            startY: event.clientY,
            lastX: point.x,
            lastY: point.y,
            moved: false,
        };
        if (nodeDrag.button && nodeDrag.button.setPointerCapture
                && event.pointerId != null) {
            nodeDrag.button.setPointerCapture(event.pointerId);
            nodeDrag.button.classList.add('dragging');
        }
        event.preventDefault();
        event.stopPropagation();
    }

    function moveNodeDrag(event) {
        if (!nodeDrag || event.pointerId !== nodeDrag.pointerId) return;
        const point = canvasPoint(event);
        if (Math.hypot(event.clientX - nodeDrag.startX, event.clientY - nodeDrag.startY) > 3) {
            nodeDrag.moved = true;
        }
        if (!nodeDrag.moved || !viewFrame) return;
        if (heldRotation == null) heldRotation = viewFrame.rotation;
        armSprings(nodeDrag.kind === 'sun' ? 'sun' : 'siblings');
        if (nodeDrag.kind === 'sun') {
            const world = screenToDisk(
                point.x - nodeDrag.offsetX,
                point.y - nodeDrag.offsetY,
                nodeDrag.depthScale,
            );
            if (!world) return;
            nodeDrag.system.x = world.x;
            nodeDrag.system.y = 0;
            nodeDrag.system.z = world.z;
            nodeDrag.system.vx = 0;
            nodeDrag.system.vz = 0;
        } else if (nodeDrag.slot) {
            nodeDrag.slot.x += point.x - nodeDrag.lastX;
            nodeDrag.slot.y += point.y - nodeDrag.lastY;
            nodeDrag.slot.vx = 0;
            nodeDrag.slot.vy = 0;
            nodeDrag.lastX = point.x;
            nodeDrag.lastY = point.y;
        }
        event.preventDefault();
    }

    function armSprings(mode) {
        springFrames = 90;
        if (springsArmed && springPin && springPin.mode === mode) return;
        springsArmed = true;
        springPin = { mode, drag: nodeDrag };
        linkRests = new Map();
        systems.forEach((system) => {
            system.vx = 0;
            system.vz = 0;
            system.restReady = false;
            if (system.focusAnchor) {
                system.focusAnchor.vx = 0;
                system.focusAnchor.vy = 0;
            }
            (system.siblingPoints || []).forEach((point) => {
                point.vx = 0;
                point.vy = 0;
            });
        });
    }

    function band(ax, ay, bx, by, rest, stiffness) {
        let dx = bx - ax;
        let dy = by - ay;
        const dist = Math.hypot(dx, dy) || 0.0001;
        const pull = (dist - rest) * stiffness;
        return { x: (dx / dist) * pull, y: (dy / dist) * pull };
    }

    function stepWorldSprings(pinned) {
        if (!pinned) return;
        const labelled = systems.filter((system) => system.label);
        const seen = new Set();
        const touch = (system) => {
            (system.links || []).forEach((link) => {
                const other = labelled.find((candidate) => candidate.label === link.label);
                if (!other || other === system) return;
                if (system !== pinned && other !== pinned) return;
                const key = [system.label, other.label].sort().join('|');
                if (seen.has(key)) return;
                seen.add(key);
                const dist = Math.hypot(other.x - system.x, other.z - system.z) || 0.0001;
                if (!linkRests.has(key)) linkRests.set(key, dist);
                const force = band(system.x, system.z, other.x, other.z, linkRests.get(key), 0.018);
                if (system !== pinned) { system.vx += force.x; system.vz += force.y; }
                if (other !== pinned) { other.vx -= force.x; other.vz -= force.y; }
            });
        };
        touch(pinned);
        labelled.forEach((system) => {
            if (system !== pinned) touch(system);
        });
        labelled.forEach((system) => {
            if (system === pinned) { system.vx = 0; system.vz = 0; return; }
            system.vx *= 0.62;
            system.vz *= 0.62;
            const speed = Math.hypot(system.vx, system.vz);
            const scale = speed > 0.025 ? 0.025 / speed : 1;
            system.x += system.vx * scale;
            system.z += system.vz * scale;
        });
    }

    function stepSiblingSprings(system, pinnedIndex) {
        const points = system.siblingPoints;
        if (!points || points.length < 2) return;
        if (!system.focusAnchor) system.focusAnchor = { x: 0, y: 0, vx: 0, vy: 0 };
        const anchor = system.focusAnchor;
        if (!system.restReady) {
            points.forEach((point) => {
                point.rest = Math.hypot(point.x - anchor.x, point.y - anchor.y) || 1;
                point.vx = 0;
                point.vy = 0;
            });
            anchor.vx = 0;
            anchor.vy = 0;
            system.restReady = true;
        }
        const fx = points.map(() => 0);
        const fy = points.map(() => 0);
        points.forEach((point, index) => {
            const force = band(point.x, point.y, anchor.x, anchor.y, point.rest, 0.012);
            if (index !== pinnedIndex) { fx[index] += force.x; fy[index] += force.y; }
        });
        const damp = 0.55;
        points.forEach((point, index) => {
            if (index === pinnedIndex) { point.vx = 0; point.vy = 0; return; }
            point.vx = (point.vx + fx[index]) * damp;
            point.vy = (point.vy + fy[index]) * damp;
            const speed = Math.hypot(point.vx, point.vy);
            const scale = speed > 8 ? 8 / speed : 1;
            point.x += point.vx * scale;
            point.y += point.vy * scale;
        });
    }

    function stepSprings() {
        const pin = nodeDrag && nodeDrag.moved ? nodeDrag : springPin;
        if (!pin) return;
        if ((pin.kind || (pin.drag && pin.drag.kind)) === 'sun'
                || (pin.mode === 'sun')) {
            const system = pin.system || (pin.drag && pin.drag.system);
            stepWorldSprings(system);
            return;
        }
        const system = focusedSystem;
        const index = pin.slotIndex != null
            ? pin.slotIndex
            : (pin.drag ? pin.drag.slotIndex : -1);
        if (system) stepSiblingSprings(system, index);
    }

    function endNodeDrag(event) {
        if (!nodeDrag) return;
        const same = event.pointerId == null
            || nodeDrag.pointerId == null
            || event.pointerId === nodeDrag.pointerId;
        if (!same) return;
        const drag = nodeDrag;
        nodeDrag = null;
        if (drag.button) drag.button.classList.remove('dragging');
        if (drag.moved) {
            springPin = {
                mode: drag.kind === 'sun' ? 'sun' : 'siblings',
                drag,
            };
            springFrames = 90;
            return;
        }
        springsArmed = false;
        springPin = null;
        springFrames = 0;
        if (drag.kind === 'sun') openSystem(drag.system.label);
        else if (drag.kind === 'entity') openEntityDetail(drag.systemLabel, drag.entityLabel);
    }

    function bindSunDrag(button) {
        button.addEventListener('pointerdown', (event) => startSunDrag(button, event));
        button.addEventListener('mousedown', (event) => startSunDrag(button, event));
    }

    function startSunDrag(button, event) {
        if (nodeDrag) return;
        const system = systems.find(
            (candidate) => candidate.label === button.dataset.galaxySystem);
        if (!system || !viewFrame) return;
        const point = canvasPoint(event);
        const projected = project(
            system, viewFrame.rotation, viewFrame.scale,
            viewFrame.centerX, viewFrame.centerY);
        beginNodeDrag(event, {
            kind: 'sun',
            system,
            depthScale: projected.depthScale || 1,
            offsetX: point.x - projected.x,
            offsetY: point.y - projected.y,
        });
    }

    function bindEntityDrag(button) {
        button.addEventListener('pointerdown', (event) => startEntityDrag(button, event));
        button.addEventListener('mousedown', (event) => startEntityDrag(button, event));
    }

    function startEntityDrag(button, event) {
        if (nodeDrag) return;
        const system = systems.find(
            (candidate) => candidate.label === button.dataset.system);
        const index = Number(button.dataset.entityIndex);
        if (!system) return;
        if (!system.siblingPoints || !system.siblingPoints[index]) {
            spreadSiblings(system, (system.entityNodes || []).length);
        }
        if (!system.siblingPoints || !system.siblingPoints[index]) return;
        beginNodeDrag(event, {
            kind: 'entity',
            slot: system.siblingPoints[index],
            points: system.siblingPoints,
            slotIndex: index,
            systemLabel: system.label,
            entityLabel: button.dataset.label,
        });
    }

    window.addEventListener('pointermove', moveNodeDrag);
    window.addEventListener('mousemove', moveNodeDrag);
    window.addEventListener('pointerup', endNodeDrag);
    window.addEventListener('mouseup', endNodeDrag);
    window.addEventListener('pointercancel', endNodeDrag);

    function drawTypeLinks(projectedSystems) {
        if (focusMix > 0.25) return;
        context.save();
        context.lineWidth = 1.15;
        context.strokeStyle = 'rgba(26, 69, 199, 0.38)';
        const seen = new Set();
        projectedSystems.forEach(({ system, projected }) => {
            (system.links || []).forEach((link) => {
                const key = [system.label, link.label].sort().join('|');
                const edgeKey = `type|${key}|${link.predicate || ''}`;
                if (seen.has(key) || isEdgeHidden(edgeKey)) return;
                if (isNodeHidden(system)) return;
                seen.add(key);
                const other = projectedSystems.find(
                    (entry) => entry.system.label === link.label);
                if (!other || isNodeHidden(other.system)) return;
                context.beginPath();
                context.moveTo(projected.x, projected.y);
                context.lineTo(other.projected.x, other.projected.y);
                context.stroke();
            });
        });
        context.restore();
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

    function foregroundDepthFactor(x, y) {
        if (!selectedEntity || entityMix < 0.015) return 1;
        const focusX = width < 720 ? width * 0.5 : width * 0.3;
        const focusY = width < 720 ? height * 0.32 : height * 0.51;
        const radius = Math.min(width, height)
            * (width < 720 ? 0.065 : 0.078);
        const distance = Math.hypot(x - focusX, y - focusY);
        const proximity = Math.max(0, 1 - distance / (radius * 3.2));
        return 1 - proximity * entityMix * 0.82;
    }

    function detailSystemCenter(projectedSun) {
        const target = width < 720
            ? { x: width * 0.68, y: height * 0.55 }
            : { x: width * 0.58, y: height * 0.4 };
        return {
            x: projectedSun.x + (target.x - projectedSun.x) * entityMix,
            y: projectedSun.y + (target.y - projectedSun.y) * entityMix,
        };
    }

    function spreadSiblings(system, count) {
        if (!count) return [];
        if (system.siblingPoints && system.siblingPoints.length === count) {
            return system.siblingPoints;
        }
        const minDist = count > 18 ? 176 : 220;
        const points = [];
        let placed = 0;
        let ring = 0;
        while (placed < count) {
            const radius = 220 + ring * minDist;
            const capacity = Math.max(5, Math.floor((2 * Math.PI * radius) / minDist));
            const n = Math.min(capacity, count - placed);
            for (let slot = 0; slot < n; slot += 1) {
                const angle = -Math.PI / 2
                    + ((slot + 0.5) / n) * Math.PI * 2
                    + ring * 0.4;
                points.push({
                    x: Math.cos(angle) * radius,
                    y: Math.sin(angle) * radius * 0.92,
                });
            }
            placed += n;
            ring += 1;
        }
        for (let pass = 0; pass < 14; pass += 1) {
            for (let i = 0; i < count; i += 1) {
                for (let j = i + 1; j < count; j += 1) {
                    let dx = points[i].x - points[j].x;
                    let dy = points[i].y - points[j].y;
                    let dist2 = dx * dx + dy * dy;
                    if (dist2 < 9) { dx = 3; dy = 0; dist2 = 9; }
                    const dist = Math.sqrt(dist2);
                    if (dist >= minDist) continue;
                    const push = (minDist - dist) * 0.5;
                    points[i].x += (dx / dist) * push;
                    points[i].y += (dy / dist) * push;
                    points[j].x -= (dx / dist) * push;
                    points[j].y -= (dy / dist) * push;
                }
            }
        }
        system.siblingPoints = points;
        return points;
    }

    function drawFocusedSystem(system, projectedSun, projectedSystems,
                               rotation, scale, centerX, centerY, time) {
        if (!system || focusMix < 0.015) return [];
        const visualCenter = detailSystemCenter(projectedSun);
        const anchor = system.focusAnchor || { x: 0, y: 0 };
        const hub = {
            x: visualCenter.x + anchor.x,
            y: visualCenter.y + anchor.y,
        };
        const spread = spreadSiblings(system, system.entityNodes.length);
        const nodes = [];
        system.entityNodes.forEach((entity, index) => {
            if (!entity || isEntityHidden(entity)) return;
            const natural = project(entity, rotation, scale, centerX, centerY);
            const slot = spread[index] || { x: 0, y: 0 };
            const depth = Math.sin(
                -Math.PI / 2 + (index / Math.max(system.entityNodes.length, 1)) * Math.PI * 2);
            const arranged = {
                x: visualCenter.x + slot.x,
                y: visualCenter.y + slot.y,
                depthScale: 0.68 + (depth + 1) * 0.17,
            };
            nodes.push({
                ...natural,
                x: natural.x + (arranged.x - natural.x) * Math.max(focusMix, entityMix),
                y: natural.y + (arranged.y - natural.y) * Math.max(focusMix, entityMix),
                depthScale: natural.depthScale
                    + (arranged.depthScale - natural.depthScale) * entityMix,
                label: entity.label,
                connection: entity.connection,
                sourceIndex: index,
            });
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
            context.save();
            context.globalAlpha *= foregroundDepthFactor(node.x, node.y);
            context.beginPath();
            context.moveTo(hub.x, hub.y);
            context.lineTo(node.x, node.y);
            context.strokeStyle = systemColor;
            context.lineWidth = 0.9;
            context.stroke();

            if (node.connections || node.connection) {
                const links = node.connections
                    || (node.connection ? [node.connection] : []);
                links.forEach((connection) => {
                    if (isEdgeHidden(`to|${entityKey(system.entityNodes[node.sourceIndex])}|${connection.system}`)) {
                        return;
                    }
                    const destination = projectedSystems.find(
                        (entry) => entry.system.label === connection.system);
                    if (!destination) return;
                    const lineTarget = {
                        x: destination.projected.x,
                        y: destination.projected.y,
                    };
                    const arrowPosition = fadingLine(node, lineTarget, systemColor);
                    if (connection.button === false) return;
                    positionConnectionArrow(
                        arrowIndex,
                        arrowPosition,
                        node.label,
                        connection,
                        system.warm,
                    );
                    arrowIndex += 1;
                });
            }
            context.restore();
        });

        ensureEntityTargets(nodes.length);
        nodes.forEach((node, index) => {
            if (selectedEntity && node.label === selectedEntity.label) return;
            const depthFactor = foregroundDepthFactor(node.x, node.y);
            const radius = node.size * node.depthScale
                * (0.72 + depthFactor * 0.28);
            context.save();
            context.globalAlpha *= depthFactor;
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
            context.save();
            context.beginPath();
            context.arc(node.x, node.y, radius * 2.2, 0, Math.PI * 2);
            context.fillStyle = system.warm
                ? 'rgba(249, 171, 0, .12)'
                : 'rgba(26, 115, 232, .12)';
            context.fill();
            context.shadowColor = 'rgba(60, 64, 67, .22)';
            context.shadowBlur = 7;
            context.shadowOffsetY = 2;
            context.beginPath();
            context.arc(node.x, node.y, radius, 0, Math.PI * 2);
            context.fillStyle = system.warm ? '#f9ab00' : '#1a73e8';
            context.fill();
            context.shadowColor = 'transparent';
            context.strokeStyle = system.warm ? '#e37400' : '#0b57d0';
            context.lineWidth = 1;
            context.stroke();
            context.restore();

            context.font = `${isHighlighted ? 700 : 500} 11px "IBM Plex Sans", ui-sans-serif, sans-serif`;
            context.textAlign = node.x < visualCenter.x ? 'right' : 'left';
            context.textBaseline = 'middle';
            context.fillStyle = '#283647';
            context.fillText(
                shortLabel(node.label, nodes.length > 16 ? 18 : 32),
                node.x + (node.x < projectedSun.x ? -radius - 7 : radius + 7),
                node.y,
            );
            positionEntityTarget(node.sourceIndex, node, system);
            context.restore();
        });
        context.restore();
        return nodes;
    }

    function drawHistoryCylinder(system, entity, x, y, radius) {
        if (historyMix < 0.015) return;
        const entries = [
            ['Heute · 09:42', 'Im Wissensnetz geöffnet'],
            ['08. Okt.', 'Verbindung bestätigt'],
            ['26. Sept.', 'Stammdaten ergänzt'],
            ['04. Sept.', 'Entität erkannt'],
        ];
        const color = system.warm ? '199, 123, 24' : '40, 89, 205';
        const fillColor = system.warm ? '#f9ab00' : '#1a73e8';
        const outlineColor = system.warm ? '#e37400' : '#0b57d0';
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

        context.beginPath();
        context.moveTo(x + topXOffset, y + topYOffset);
        context.lineTo(lastX + topXOffset, y + topYOffset);
        context.lineTo(lastX + bottomXOffset, y + bottomYOffset);
        context.lineTo(x + bottomXOffset, y + bottomYOffset);
        context.closePath();
        context.fillStyle = system.warm
            ? 'rgba(249, 171, 0, .12)'
            : 'rgba(26, 115, 232, .12)';
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

            context.save();
            context.globalAlpha = entityMix * local;
            context.shadowColor = 'rgba(60, 64, 67, .2)';
            context.shadowBlur = 7;
            context.shadowOffsetY = 2;
            context.beginPath();
            context.ellipse(
                discX,
                y,
                discWidth,
                discHeight,
                discTilt,
                0,
                Math.PI * 2,
            );
            context.fillStyle = fillColor;
            context.fill();
            context.shadowColor = 'transparent';
            context.strokeStyle = outlineColor;
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
        context.fillText(shortLabel(entity.label, 36), x - discWidth, y - discHeight - 50);
        context.restore();
    }

    function drawEntityDetail(system, entity, rotation, scale, centerX, centerY,
                              projectedOrigin, projectedSystemCenter) {
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

        context.globalAlpha = entityMix;
        const sphereWidth = radius * (1 - historyMix * 0.82);
        const sphereTilt = -0.24 * historyMix;
        context.beginPath();
        context.ellipse(
            x, y, sphereWidth * 1.28, radius * 1.28,
            sphereTilt, 0, Math.PI * 2);
        context.fillStyle = system.warm
            ? 'rgba(249, 171, 0, .12)'
            : 'rgba(26, 115, 232, .12)';
        context.fill();

        context.shadowColor = 'rgba(60, 64, 67, .28)';
        context.shadowBlur = 12;
        context.shadowOffsetY = 4;
        context.beginPath();
        context.ellipse(
            x, y, sphereWidth, radius, sphereTilt, 0, Math.PI * 2);
        context.fillStyle = system.warm ? '#f9ab00' : '#1a73e8';
        context.fill();
        context.shadowColor = 'transparent';
        context.strokeStyle = system.warm ? '#e37400' : '#0b57d0';
        context.lineWidth = 1.5;
        context.stroke();

        context.beginPath();
        context.ellipse(
            x,
            y,
            sphereWidth * 1.18,
            radius * 1.18,
            sphereTilt,
            0,
            Math.PI * 2,
        );
        context.strokeStyle = system.warm
            ? 'rgba(227, 116, 0, .28)'
            : 'rgba(11, 87, 208, .28)';
        context.lineWidth = 1.5;
        context.stroke();

        context.font = '700 14px "IBM Plex Mono", ui-monospace, monospace';
        context.textAlign = 'center';
        context.textBaseline = 'top';
        context.fillStyle = '#283647';
        context.globalAlpha = entityMix * (1 - historyMix);
        context.fillText(shortLabel(entity.label, 32), x, y + radius + 18);
        context.globalAlpha = entityMix;
        drawHistoryCylinder(system, entity, x, y, radius);
        context.restore();
    }

    function drawHeatSpot(x, y, strength, radius) {
        if (!heatmapContext || strength <= 0) return;
        const safeStrength = Math.max(0.08, Math.min(1, strength));
        const gradient = heatmapContext.createRadialGradient(
            x, y, 0, x, y, radius);
        gradient.addColorStop(0, `rgba(219, 58, 40, ${0.7 * safeStrength})`);
        gradient.addColorStop(
            0.24,
            `rgba(249, 171, 0, ${0.56 * safeStrength})`,
        );
        gradient.addColorStop(
            0.56,
            `rgba(26, 115, 232, ${0.3 * safeStrength})`,
        );
        gradient.addColorStop(1, 'rgba(26, 115, 232, 0)');
        heatmapContext.beginPath();
        heatmapContext.arc(x, y, radius, 0, Math.PI * 2);
        heatmapContext.fillStyle = gradient;
        heatmapContext.fill();
    }

    function drawSearchHeatmap(projectedSystems, focusedNodes,
                               selectedProjection) {
        if (!heatmapContext) return;
        heatmapContext.clearRect(0, 0, width, height);
        if (!searchState.query) return;

        const maxSystemScore = Math.max(
            1,
            ...searchState.results.map((result) => result.score),
        );
        if (searchState.level === 'galaxy') {
            searchState.results.forEach((result) => {
                const projection = projectedSystems.find(
                    (entry) => entry.system === result.system);
                if (!projection) return;
                const strength = result.score > 0
                    ? 0.28 + 0.72 * (result.score / maxSystemScore)
                    : 0.11;
                const radius = Math.min(width, height)
                    * (0.075 + 0.045 * strength);
                drawHeatSpot(
                    projection.projected.x,
                    projection.projected.y,
                    strength,
                    radius,
                );
            });
            return;
        }

        const focusedResult = searchState.results.find(
            (result) => result.system === focusedSystem);
        const focusedProjection = projectedSystems.find(
            (entry) => entry.system === focusedSystem);
        if (focusedProjection) {
            drawHeatSpot(
                focusedProjection.projected.x,
                focusedProjection.projected.y,
                searchState.level === 'entity' ? 0.3 : 0.62,
                Math.min(width, height) * 0.115,
            );
        }
        if (!focusedResult || !focusedNodes) return;

        const maxEntityScore = Math.max(
            1,
            ...focusedResult.entities.map((entry) => entry.score),
        );
        focusedResult.entities
            .filter((entry) => entry.score > 0)
            .slice(0, 5)
            .forEach((entry) => {
                const node = focusedNodes.find(
                    (candidate) => candidate.label === entry.entity.label);
                if (!node) return;
                const strength = 0.24 + 0.76 * (entry.score / maxEntityScore);
                drawHeatSpot(
                    node.x,
                    node.y,
                    strength,
                    Math.min(width, height) * (0.052 + 0.025 * strength),
                );
            });
        if (searchState.level === 'entity' && selectedProjection) {
            const detailTarget = {
                x: width < 720 ? width * 0.5 : width * 0.3,
                y: width < 720 ? height * 0.32 : height * 0.51,
            };
            drawHeatSpot(
                selectedProjection.x
                    + (detailTarget.x - selectedProjection.x) * entityMix,
                selectedProjection.y
                    + (detailTarget.y - selectedProjection.y) * entityMix,
                1,
                Math.min(width, height) * 0.13,
            );
        }
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

        if (nodeDrag || springFrames > 0) {
            const substeps = 2;
            for (let substep = 0; substep < substeps; substep += 1) stepSprings();
            if (!nodeDrag) springFrames -= 1;
            if (!nodeDrag && springFrames <= 0) {
                springsArmed = false;
                springPin = null;
            }
        }
        const rotation = heldRotation != null
            ? heldRotation
            : (reducedMotion.matches ? 0.2 : time * 0.000022);
        if (nodeDrag) {
            targetTiltX = tiltX;
            targetTiltY = tiltY;
        }
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
        viewFrame = { rotation, scale, centerX, centerY, tiltX, tiltY };
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
            const depthFactor = foregroundDepthFactor(star.x, star.y);
            const alpha = (0.27 + depth * 0.73) * star.brightness
                * (1 - focusMix * 0.68)
                * (1 - entityMix * 0.72)
                * depthFactor;
            const radius = Math.max(
                0.3,
                star.size * star.depthScale * (0.78 + depthFactor * 0.22),
            );
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
        drawTypeLinks(projectedSystems);
        projectedSystems.forEach(({ system, projected: projectedSystem }) => {
            if (isNodeHidden(system)) {
                const target = sunTargets.find(
                    (button) => button.dataset.galaxySystem === system.label);
                if (target) target.hidden = true;
                return;
            }
            const displayCenter = system === focusedSystem
                ? detailSystemCenter(projectedSystem)
                : projectedSystem;
            const anchor = system === focusedSystem ? system.focusAnchor : null;
            const displayedSystem = {
                ...projectedSystem,
                x: displayCenter.x + (anchor ? anchor.x : 0),
                y: displayCenter.y + (anchor ? anchor.y : 0),
            };
            context.save();
            context.globalAlpha = focusedSystem && focusedSystem !== system
                ? 1 - focusMix * 0.88
                : 1;
            context.globalAlpha *= 1 - entityMix * 0.88;
            context.globalAlpha *= foregroundDepthFactor(
                displayedSystem.x,
                displayedSystem.y,
            );
            drawSun(system, displayedSystem);
            context.restore();
            positionSunTarget(system, displayedSystem);
        });
        const focusedProjection = projectedSystems.find(
            (entry) => entry.system === focusedSystem);
        let focusedNodes = null;
        let selectedProjection = null;
        if (focusedProjection) {
            focusedNodes = drawFocusedSystem(
                focusedSystem,
                focusedProjection.projected,
                projectedSystems,
                rotation,
                scale,
                centerX,
                centerY,
                time,
            );
            selectedProjection = selectedEntity
                ? focusedNodes.find((node) => node.label === selectedEntity.label)
                : null;
            drawEntityDetail(
                focusedSystem,
                selectedEntity,
                rotation,
                scale,
                centerX,
                centerY,
                selectedProjection,
                detailSystemCenter(focusedProjection.projected),
            );
        }
        drawSearchHeatmap(projectedSystems, focusedNodes, selectedProjection);
        const screenPositions = {};
        projectedSystems.forEach(({ system, projected: projectedSystem }) => {
            const displayCenter = system === focusedSystem
                ? detailSystemCenter(projectedSystem)
                : projectedSystem;
            screenPositions[`system:${system.label}`] = {
                x: displayCenter.x,
                y: displayCenter.y,
            };
            (system.entityNodes || []).forEach((entity) => {
                const placed = project(entity, rotation, scale, centerX, centerY);
                screenPositions[`${system.label}::${entity.label}`] = {
                    x: placed.x,
                    y: placed.y,
                };
            });
        });
        documents.forEach((doc) => {
            const placed = project(doc, rotation, scale, centerX, centerY);
            screenPositions[`Dokumente::${doc.label}`] = {
                x: placed.x,
                y: placed.y,
            };
        });
        if (window.cortexGalaxy) window.cortexGalaxy.screenPositions = screenPositions;
        if (window.cortexGalaxyResults && window.cortexGalaxyResults.syncPositions) {
            window.cortexGalaxyResults.syncPositions();
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
        const total = Number(system.count) || 0;
        const shown = (system.entityNodes || []).length;
        breadcrumbCurrent.textContent = total > shown && shown > 0
            ? `${system.label} · ${formatGalaxyCount(shown)} von ${formatGalaxyCount(total)}`
            : total > 0
                ? `${system.label} · ${formatGalaxyCount(total)}`
                : system.label;
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

    async function loadChildLinks(system) {
        if (!system || system.linksLoaded) return;
        const nodes = (system.entityNodes || []).filter((entity) => entity && entity.id);
        if (!nodes.length) return;
        system.linksLoaded = true;
        const batch = 6;
        for (let start = 0; start < nodes.length; start += batch) {
            const slice = nodes.slice(start, start + batch);
            const details = await Promise.all(slice.map((entity) =>
                fetch(`/api/ontology/entities/${encodeURIComponent(entity.id)}`)
                    .then((resp) => (resp.ok ? resp.json() : null))
                    .catch(() => null)));
            details.forEach((data, index) => {
                const grouped = new Map();
                ((data && data.relations) || []).forEach((rel) => {
                    if (!rel || !rel.target || !rel.predicate) return;
                    const other = systems.find((candidate) => candidate.id === rel.target.type);
                    if (!other || other === system) return;
                    if (!grouped.has(other.label)) {
                        grouped.set(other.label, { predicates: new Set(), names: [] });
                    }
                    const bucket = grouped.get(other.label);
                    bucket.predicates.add(String(rel.predicate).replace(/[_-]+/g, ' '));
                    if (rel.target.label && bucket.names.length < 3) {
                        bucket.names.push(rel.target.label);
                    }
                });
                slice[index].connections = [...grouped.entries()].map(([label, bucket]) => ({
                    system: label,
                    entity: `${[...bucket.predicates].join(', ')} → ${bucket.names.join(', ')}`,
                    button: false,
                }));
            });
            if (reducedMotion.matches && focusedSystem === system) draw();
        }
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
        showBreadcrumb(system);
        setZoom(2.65);
        loadChildLinks(system);
        showTypeDetails(system);
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
        if (entity.id) showEntityDetails(system, entity);
        else renderEntityPanel(system, entity);
        setZoom(1.7);
    }

    function revealDetailPanel() {
        if (!entityPanel) return;
        entityPanel.hidden = false;
        window.requestAnimationFrame(() => {
            entityPanel.classList.add('open');
            if (typeof setDetailTab === 'function') setDetailTab('fields');
        });
    }

    function detailButton(text, onClick) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn-text galaxy-detail-link';
        button.textContent = text;
        button.addEventListener('click', onClick);
        return button;
    }

    function detailRow(text, onOpen, onHide, onDelete) {
        const item = document.createElement('li');
        item.className = 'galaxy-detail-row';
        item.appendChild(detailButton(text, onOpen));
        if (onHide) item.appendChild(smallAction('Ausblenden', 'galaxy-hide', onHide));
        if (onDelete) {
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'btn btn-danger galaxy-mini';
            remove.textContent = 'Löschen';
            remove.addEventListener('click', (event) => {
                event.stopPropagation();
                onDelete();
            });
            item.appendChild(remove);
        }
        return item;
    }

    function smallAction(text, className, onClick) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `btn-text ${className}`;
        button.textContent = text;
        button.addEventListener('click', (event) => {
            event.stopPropagation();
            onClick();
        });
        return button;
    }

    function galaxyCsrf() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    async function galaxyWrite(url, method, payload) {
        const response = await fetch(url, {
            method,
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': galaxyCsrf(),
            },
            body: payload == null ? undefined : JSON.stringify(payload),
        });
        if (response.status === 401) {
            window.location.assign('/login');
            return null;
        }
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.success === false) {
            throw new Error(data.error || 'Das hat nicht geklappt.');
        }
        return data;
    }

    function findLoadedEntity(label) {
        const want = String(label || '').trim().toLowerCase();
        if (!want) return null;
        for (let s = 0; s < systems.length; s += 1) {
            const hit = (systems[s].entityNodes || []).find(
                (node) => node.label && node.label.toLowerCase() === want);
            if (hit && hit.id) return hit;
        }
        return null;
    }

    function actionForm(fields, submitLabel, onSubmit) {
        const form = document.createElement('form');
        form.className = 'galaxy-action-form';
        const inputs = {};
        fields.forEach((field) => {
            const input = document.createElement('input');
            input.type = 'text';
            input.placeholder = field.placeholder;
            input.setAttribute('aria-label', field.placeholder);
            input.maxLength = field.max || 120;
            inputs[field.name] = input;
            form.appendChild(input);
        });
        const button = document.createElement('button');
        button.type = 'submit';
        button.className = 'btn btn-primary';
        button.textContent = submitLabel;
        form.appendChild(button);
        const error = document.createElement('p');
        error.className = 'entity-hint';
        error.hidden = true;
        form.appendChild(error);
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            error.hidden = true;
            try {
                await onSubmit(inputs);
            } catch (err) {
                error.textContent = err.message || 'Das hat nicht geklappt.';
                error.hidden = false;
            }
        });
        return form;
    }

    function nodeCreateForm(selectedTypeId) {
        const form = document.createElement('form');
        form.className = 'galaxy-action-form';
        const select = document.createElement('select');
        select.className = 'galaxy-type-select';
        select.setAttribute('aria-label', 'Typ des neuen Knotens');
        systems
            .filter((system) => system.id && system.label)
            .slice()
            .sort((a, b) => a.label.localeCompare(b.label, 'de'))
            .forEach((system) => {
                const option = document.createElement('option');
                option.value = system.id;
                option.textContent = system.label;
                if (system.id === selectedTypeId) option.selected = true;
                select.appendChild(option);
            });
        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = 'Name des Knotens';
        input.setAttribute('aria-label', 'Name des Knotens');
        input.maxLength = 120;
        const button = document.createElement('button');
        button.type = 'submit';
        button.className = 'btn btn-primary';
        button.textContent = 'Knoten anlegen';
        const error = document.createElement('p');
        error.className = 'entity-hint';
        error.hidden = true;
        form.append(select, input, button, error);
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            error.hidden = true;
            const host = systems.find((system) => system.id === select.value);
            if (!host) {
                error.textContent = 'Wählen Sie einen Typ.';
                error.hidden = false;
                return;
            }
            try {
                await createEntity(host, input.value);
            } catch (err) {
                error.textContent = err.message || 'Das hat nicht geklappt.';
                error.hidden = false;
            }
        });
        return form;
    }

    function renderHiddenTray() {
        const host = canvas.parentElement;
        if (!host) return;
        let tray = document.getElementById('galaxyHidden');
        const count = hiddenItems.nodes.length
            + hiddenItems.entities.length
            + hiddenItems.edges.length;
        if (!count) {
            if (tray) tray.remove();
            return;
        }
        if (!tray) {
            tray = document.createElement('section');
            tray.id = 'galaxyHidden';
            tray.className = 'galaxy-hidden';
            host.appendChild(tray);
        }
        tray.replaceChildren();
        const title = document.createElement('h2');
        title.textContent = `Ausgeblendet · ${count}`;
        tray.appendChild(title);
        const list = document.createElement('ul');
        const add = (kind, item) => {
            const row = document.createElement('li');
            const name = document.createElement('span');
            name.textContent = item.label;
            const back = document.createElement('button');
            back.type = 'button';
            back.className = 'btn-text';
            back.textContent = 'Einblenden';
            back.addEventListener('click', () => forgetHidden(kind, item.key));
            row.append(name, back);
            list.appendChild(row);
        };
        hiddenItems.nodes.forEach((item) => add('nodes', item));
        hiddenItems.entities.forEach((item) => add('entities', item));
        hiddenItems.edges.forEach((item) => add('edges', item));
        tray.appendChild(list);
    }

    function hideNode(system) {
        rememberHidden('nodes', nodeKey(system), system.label);
        if (focusedSystem === system) fitGalaxy();
        else hideEntityPanel();
    }

    function hideEntityNode(system, entity) {
        rememberHidden('entities', entityKey(entity), `${entity.label} · ${system.label}`);
        if (selectedEntity === entity) selectedEntity = null;
        if (focusedSystem === system) showTypeDetails(system);
    }

    function typeEdgeKey(system, link) {
        return `type|${[system.label, link.label].sort().join('|')}|${link.predicate || ''}`;
    }

    function hideTypeEdge(system, link) {
        const predicate = String(link.predicate || 'Kante').replace(/[_-]+/g, ' ');
        rememberHidden('edges', typeEdgeKey(system, link), `${predicate} · ${system.label} – ${link.label}`);
        if (focusedSystem === system) showTypeDetails(system);
    }

    function entityEdgeKey(entity, typeLabel) {
        return `to|${entityKey(entity)}|${typeLabel}`;
    }

    function hideEntityEdge(system, entity, relation) {
        const target = relation.target || {};
        const host = systems.find((candidate) => candidate.id === target.type);
        const typeLabel = host ? host.label : (target.type || target.label || '');
        const predicate = String(relation.predicate || 'Kante').replace(/[_-]+/g, ' ');
        rememberHidden(
            'edges',
            entityEdgeKey(entity, typeLabel),
            `${predicate} · ${entity.label} – ${target.label || typeLabel}`,
        );
        showEntityDetails(system, entity);
    }

    async function createEntity(system, label) {
        const name = String(label || '').trim();
        if (!name) throw new Error('Name fehlt.');
        if (!system.id) throw new Error('Dieser Typ kann hier nicht ergänzt werden.');
        const data = await galaxyWrite('/api/ontology/entities', 'POST', {
            type: system.id,
            label: name,
        });
        const created = data && data.entity;
        if (!created) return;
        system.entityNodes = system.entityNodes || [];
        system.entityNodes.unshift({
            id: created.id,
            label: created.label || name,
            live: true,
        });
        system.count = (Number(system.count) || 0) + 1;
        system.siblingPoints = null;
        showTypeDetails(system);
    }

    async function removeEntity(system, entity) {
        if (!entity.id) throw new Error('Dieser Eintrag kann hier nicht gelöscht werden.');
        if (!window.confirm(`${entity.label} löschen?`)) return;
        await galaxyWrite(
            `/api/ontology/entities/${encodeURIComponent(entity.id)}`,
            'DELETE',
        );
        system.entityNodes = (system.entityNodes || []).filter((node) => node.id !== entity.id);
        system.count = Math.max(0, (Number(system.count) || 1) - 1);
        system.siblingPoints = null;
        if (selectedEntity && selectedEntity.id === entity.id) selectedEntity = null;
        showTypeDetails(system);
    }

    async function removeType(system) {
        if (!system.id) throw new Error('Dieser Knoten kann hier nicht gelöscht werden.');
        if (!window.confirm(`${system.label} und seine Einträge löschen?`)) return;
        await galaxyWrite(`/api/ontology/types/${encodeURIComponent(system.id)}`, 'DELETE');
        const index = systems.indexOf(system);
        if (index >= 0) systems.splice(index, 1);
        fitGalaxy();
    }

    async function createRelation(system, entity, predicate, targetLabel) {
        const name = String(predicate || '').trim();
        const target = findLoadedEntity(targetLabel);
        if (!name) throw new Error('Art der Verbindung fehlt.');
        if (!entity.id) throw new Error('Dieser Eintrag kann hier nicht verbunden werden.');
        if (!target) throw new Error('Zielknoten nicht gefunden. Öffnen Sie ihn zuerst.');
        await galaxyWrite('/api/ontology/relations', 'POST', {
            src: entity.id,
            predicate: name,
            dst: target.id,
        });
        showEntityDetails(system, entity);
    }

    async function removeRelation(system, entity, relation) {
        const target = relation.target || {};
        if (!entity.id || !target.id || !relation.predicate) {
            throw new Error('Diese Kante kann hier nicht gelöscht werden.');
        }
        const src = relation.direction === 'in' ? target.id : entity.id;
        const dst = relation.direction === 'in' ? entity.id : target.id;
        if (!window.confirm(`${relation.predicate} löschen?`)) return;
        await galaxyWrite('/api/ontology/relations', 'DELETE', {
            src,
            predicate: relation.predicate,
            dst,
        });
        showEntityDetails(system, entity);
    }

    async function showTypeDetails(system) {
        if (!entityPanel || !entityType || !entityTitle || !entityFields) return;
        entityType.textContent = 'Typ';
        entityTitle.textContent = system.label;
        entityFields.replaceChildren();
        if (documentsPanel) documentsPanel.hidden = true;
        if (historyPanel) historyPanel.hidden = true;
        if (entityTabs) entityTabs.hidden = true;
        const total = Number(system.count) || (system.entityNodes || []).length;
        const hint = document.createElement('p');
        hint.className = 'entity-hint';
        hint.textContent = total
            ? `${formatGalaxyCount(total)} ${total === 1 ? 'Eintrag' : 'Einträge'}`
            : 'Keine Einträge';
        entityFields.appendChild(hint);
        const hideThis = document.createElement('button');
        hideThis.type = 'button';
        hideThis.className = 'btn-text galaxy-hide';
        hideThis.textContent = 'Knoten ausblenden';
        hideThis.addEventListener('click', () => hideNode(system));
        entityFields.appendChild(hideThis);
        const removeTypeButton = document.createElement('button');
        removeTypeButton.type = 'button';
        removeTypeButton.className = 'btn btn-danger galaxy-mini';
        removeTypeButton.textContent = 'Löschen';
        removeTypeButton.addEventListener('click', () => {
            removeType(system).catch((err) => {
                hint.textContent = err.message;
            });
        });
        entityFields.appendChild(removeTypeButton);
        entityFields.appendChild(nodeCreateForm(system.id));
        const links = (system.links || []).filter((link) => !isEdgeHidden(typeEdgeKey(system, link)));
        if (links.length) {
            const heading = document.createElement('h3');
            heading.textContent = 'Verbindungen';
            entityFields.appendChild(heading);
            const list = document.createElement('ul');
            list.className = 'galaxy-detail-list';
            links.forEach((link) => {
                const predicate = String(link.predicate || 'verbunden').replace(/[_-]+/g, ' ');
                list.appendChild(detailRow(
                    `${predicate} → ${link.label}`,
                    () => openSystem(link.label),
                    () => hideTypeEdge(system, link),
                ));
            });
            entityFields.appendChild(list);
        }
        const entriesHeading = document.createElement('h3');
        entriesHeading.textContent = 'Einträge';
        entityFields.appendChild(entriesHeading);
        const entries = document.createElement('ul');
        entries.className = 'galaxy-detail-list';
        entityFields.appendChild(entries);
        const paintEntries = (rows) => {
            entries.replaceChildren();
            (rows || []).slice(0, 48).forEach((row) => {
                if (!row || !row.label || isEntityHidden(row)) return;
                entries.appendChild(detailRow(row.label, () => {
                    const known = (system.entityNodes || []).find(
                        (node) => node.id === row.id || node.label === row.label);
                    if (!known) {
                        system.entityNodes = system.entityNodes || [];
                        system.entityNodes.push({
                            id: row.id,
                            label: row.label,
                            live: true,
                            typeCount: formatGalaxyCount(total),
                        });
                    }
                    openEntityDetail(system.label, row.label);
                }, () => hideEntityNode(system, row), () => {
                    removeEntity(system, row).catch((err) => {
                        hint.textContent = err.message;
                    });
                }));
            });
            if (!entries.childElementCount) {
                const empty = document.createElement('li');
                empty.className = 'entity-hint';
                empty.textContent = 'Keine Einträge geladen.';
                entries.appendChild(empty);
            }
        };
        paintEntries(system.entityNodes);
        revealDetailPanel();
        if (!system.id) return;
        try {
            const response = await fetch(
                `/api/ontology/entities?type=${encodeURIComponent(system.id)}`);
            if (!response.ok) return;
            const data = await response.json();
            if (focusedSystem !== system) return;
            paintEntries(data.entities || []);
        } catch (err) { /* die schon gezeigte Liste bleibt */ }
    }

    async function showEntityDetails(system, entity) {
        if (!entityPanel || !entityType || !entityTitle || !entityFields) return;
        entityType.textContent = system.label;
        entityTitle.textContent = entity.label;
        entityFields.replaceChildren();
        const hint = document.createElement('p');
        hint.className = 'entity-hint';
        hint.textContent = 'Wird geladen…';
        entityFields.appendChild(hint);
        if (documentsPanel) documentsPanel.hidden = true;
        if (historyPanel) historyPanel.hidden = true;
        if (entityTabs) entityTabs.hidden = true;
        revealDetailPanel();
        try {
            const response = await fetch(
                `/api/ontology/entities/${encodeURIComponent(entity.id)}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            if (selectedEntity !== entity) return;
            const record = data.entity || {};
            entityTitle.textContent = record.label || entity.label;
            entityFields.replaceChildren();
            if (record.description) {
                const description = document.createElement('p');
                description.textContent = record.description;
                entityFields.appendChild(description);
            }
            const meta = document.createElement('p');
            meta.className = 'entity-hint';
            const docs = Number(record.doc_count) || 0;
            meta.textContent = `${formatGalaxyCount(docs)} ${docs === 1 ? 'Dokument' : 'Dokumente'}`;
            entityFields.appendChild(meta);
            const hideThis = document.createElement('button');
            hideThis.type = 'button';
            hideThis.className = 'btn-text galaxy-hide';
            hideThis.textContent = 'Knoten ausblenden';
        hideThis.addEventListener('click', () => hideEntityNode(system, entity));
        entityFields.appendChild(hideThis);
        const removeNode = document.createElement('button');
        removeNode.type = 'button';
        removeNode.className = 'btn btn-danger galaxy-mini';
        removeNode.textContent = 'Löschen';
        removeNode.addEventListener('click', () => {
            removeEntity(system, entity).catch((err) => {
                hint.textContent = err.message;
            });
        });
        entityFields.appendChild(removeNode);
            const heading = document.createElement('h3');
            heading.textContent = 'Verbindungen';
            entityFields.appendChild(heading);
            const relations = (Array.isArray(data.relations) ? data.relations : [])
                .filter((relation) => {
                    const target = relation.target || {};
                    const host = systems.find((candidate) => candidate.id === target.type);
                    const typeLabel = host ? host.label : (target.type || '');
                    return !isEdgeHidden(entityEdgeKey(entity, typeLabel));
                });
            if (!relations.length) {
                const empty = document.createElement('p');
                empty.className = 'entity-hint';
                empty.textContent = 'Keine Verbindungen erfasst.';
                entityFields.appendChild(empty);
            } else {
                const list = document.createElement('ul');
                list.className = 'galaxy-detail-list';
                relations.forEach((relation) => {
                    const target = relation.target || {};
                    const predicate = String(relation.predicate || '').replace(/[_-]+/g, ' ');
                    const text = relation.direction === 'in'
                        ? `${target.label || 'Eintrag'} · ${predicate}`
                        : `${predicate} → ${target.label || 'Eintrag'}`;
                    list.appendChild(detailRow(text, () => {
                        const host = systems.find((candidate) => candidate.id === target.type);
                        if (!host || !target.label) return;
                        if (!(host.entityNodes || []).some((node) => node.id === target.id)) {
                            host.entityNodes = host.entityNodes || [];
                            host.entityNodes.push({
                                id: target.id,
                                label: target.label,
                                live: true,
                            });
                        }
                        openEntityDetail(host.label, target.label);
                    }, () => hideEntityEdge(system, entity, relation), () => {
                        removeRelation(system, entity, relation).catch((err) => {
                            hint.textContent = err.message;
                        });
                    }));
                });
                entityFields.appendChild(list);
            }
            entityFields.appendChild(actionForm(
                [
                    { name: 'predicate', placeholder: 'Art, z. B. member of' },
                    { name: 'target', placeholder: 'Zielknoten, genauer Name' },
                ],
                'Verbinden',
                (inputs) => createRelation(
                    system,
                    entity,
                    inputs.predicate.value,
                    inputs.target.value,
                ),
            ));
            const evidence = Array.isArray(data.evidence) ? data.evidence : [];
            if (entityDocuments && documentsPanel && entityTabs && evidence.length) {
                entityDocuments.replaceChildren();
                evidence.forEach((item) => {
                    const line = document.createElement('p');
                    const documentInfo = item.document || {};
                    const title = documentInfo.title || documentInfo.path || 'Dokument';
                    line.textContent = item.quote
                        ? `«${item.quote}» — ${title}, Seite ${item.page}`
                        : `${title}, Seite ${item.page || 1}`;
                    entityDocuments.appendChild(line);
                });
                documentsPanel.hidden = false;
                entityTabs.hidden = false;
            }
        } catch (err) {
            hint.textContent = 'Details konnten nicht geladen werden.';
        }
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
            loadChildLinks(destination);
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
        systems,
        documents,
        version: searchVersion,
        screenPositions: {},
        redraw: restart,
    };

    sunTargets.forEach((target) => bindSunDrag(target));
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
    if (searchInput) {
        searchInput.addEventListener('input', () => {
            applySearch(searchInput.value);
        });
        searchInput.addEventListener('search', () => {
            applySearch(searchInput.value);
        });
    }
    if (searchForm) {
        searchForm.addEventListener('submit', (event) => {
            event.preventDefault();
            applySearch(searchInput ? searchInput.value : '', true);
        });
    }
    const initialQuery = searchApi
        ? searchApi.queryFromSearch(window.location.search)
        : '';
    if (searchInput && initialQuery && !searchInput.value) {
        searchInput.value = initialQuery;
    }
    if (searchVersion === 1 && initialQuery) applySearch(initialQuery);
    window.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        if (searchInput && searchInput.value) {
            searchInput.value = '';
            applySearch('');
            searchInput.focus();
        } else if (focusedSystem) {
            fitGalaxy();
        }
    });
    window.addEventListener('resize', restart);
    window.addEventListener('pointermove', (event) => {
        if (reducedMotion.matches || nodeDrag) return;
        targetTiltY = 0.14
            + ((event.clientX / Math.max(window.innerWidth, 1)) - 0.5) * 0.42;
        targetTiltX = 0.58
            + ((event.clientY / Math.max(window.innerHeight, 1)) - 0.5) * -0.24;
    }, { passive: true });
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) window.cancelAnimationFrame(frame);
        else restart();
    });
    function shortLabel(text, max) {
        const value = String(text || '');
        return value.length > max ? `${value.slice(0, max - 1)}…` : value;
    }

    function rebuildSunTargets() {
        const host = canvas.parentElement;
        if (!host) return;
        host.querySelectorAll('[data-galaxy-system]').forEach((button) => button.remove());
        sunTargets.length = 0;
        systems.forEach((system) => {
            if (!system.label) return;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'galaxy-sun-target';
            button.dataset.galaxySystem = system.label;
            button.setAttribute('aria-label', `${system.label} öffnen`);
            button.title = `${system.label} öffnen`;
            bindSunDrag(button);
            host.appendChild(button);
            sunTargets.push(button);
        });
    }

    const galaxyTopKey = 'cortex-galaxy-top';
    let galaxyTopIds = new Set();
    try {
        const savedTop = JSON.parse(localStorage.getItem(galaxyTopKey) || 'null');
        if (Array.isArray(savedTop)) galaxyTopIds = new Set(savedTop.map(String));
    } catch (err) { /* no saved front layer */ }
    let liveTypes = [];
    let liveRelations = [];

    function renderGalaxyLayers() {
        let bar = document.getElementById('galaxyLayers');
        if (!bar) {
            bar = document.createElement('div');
            bar.id = 'galaxyLayers';
            bar.className = 'galaxy-layers';
            canvas.parentElement.appendChild(bar);
        }
        const pickerOpen = bar.dataset.open === 'true';
        bar.replaceChildren();
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'galaxy-layer';
        toggle.setAttribute('aria-expanded', pickerOpen ? 'true' : 'false');
        const chosen = liveTypes.filter((type) => galaxyTopIds.has(type.id) && type.count > 0);
        toggle.textContent = chosen.length
            ? `Vordergrund · ${chosen.length}`
            : 'Vordergrund wählen';
        toggle.addEventListener('click', () => {
            bar.dataset.open = pickerOpen ? 'false' : 'true';
            renderGalaxyLayers();
        });
        bar.appendChild(toggle);
        if (!pickerOpen) return;
        const panel = document.createElement('div');
        panel.className = 'galaxy-top-panel';
        panel.setAttribute('role', 'group');
        panel.setAttribute('aria-label', 'Knoten der vorderen Schicht');
        const note = document.createElement('p');
        note.textContent = 'Die nächste Schicht sind die verbundenen Typen.';
        panel.appendChild(note);
        const populated = liveTypes
            .filter((type) => type.count > 0)
            .slice()
            .sort((a, b) => a.label.localeCompare(b.label, 'de'));
        populated.forEach((type) => {
            const label = document.createElement('label');
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.checked = galaxyTopIds.has(type.id);
            box.addEventListener('change', () => {
                if (box.checked) galaxyTopIds.add(type.id);
                else galaxyTopIds.delete(type.id);
                localStorage.setItem(galaxyTopKey, JSON.stringify([...galaxyTopIds]));
                applyLiveLayers();
            });
            const name = document.createElement('span');
            name.textContent = type.label;
            label.append(box, name);
            panel.appendChild(label);
        });
        bar.appendChild(panel);
    }

    function applyLiveLayers() {
        focusedSystem = null;
        selectedEntity = null;
        highlightedEntity = null;
        targetFocusMix = 0;
        targetEntityMix = 0;
        hideEntityPanel();
        const byId = new Map(liveTypes.map((type) => [type.id, type]));
        const tops = liveTypes.filter((type) => galaxyTopIds.has(type.id) && type.count > 0);
        const neighborIds = new Map();
        liveRelations.forEach((relation) => {
            const srcTop = galaxyTopIds.has(relation.src);
            const dstTop = galaxyTopIds.has(relation.dst);
            if (srcTop === dstTop) return;
            const otherId = srcTop ? relation.dst : relation.src;
            if (!byId.has(otherId) || galaxyTopIds.has(otherId)) return;
            if (!neighborIds.has(otherId)) neighborIds.set(otherId, relation.predicate || '');
        });
        const neighbors = [...neighborIds.keys()].map((id) => byId.get(id)).filter(Boolean);
        const maxCount = Math.max(
            ...tops.map((type) => type.count),
            ...neighbors.map((type) => type.count),
            1,
        );
        const definitions = [];
        const place = (type, index, total, radius, role) => {
            const angle = (index / Math.max(total, 1)) * Math.PI * 2 - Math.PI / 2;
            const span = role === 'top' && total === 1 ? 0 : radius;
            definitions.push({
                id: type.id,
                x: Math.cos(angle) * span,
                y: 0,
                z: Math.sin(angle) * span * 0.84,
                label: type.label,
                count: type.count,
                primary: role === 'top',
                warm: role === 'top',
                size: role === 'top'
                    ? 6.2 + 3.2 * Math.sqrt(type.count / maxCount)
                    : 3.1 + 1.8 * Math.sqrt((type.count || 1) / maxCount),
                entities: (type.entities || []).slice(0, 48).map((entity) => ({
                    id: entity.id || '',
                    label: entity.label || String(entity),
                    live: true,
                    typeCount: formatGalaxyCount(type.count),
                })),
                links: [],
            });
        };
        tops.forEach((type, index) => place(type, index, tops.length, 0.16, 'top'));
        neighbors.forEach((type, index) => place(type, index, neighbors.length, 0.34, 'next'));
        const byDef = new Map(definitions.map((definition) => [definition.id, definition]));
        liveRelations.forEach((relation) => {
            const from = byDef.get(relation.src);
            const to = byDef.get(relation.dst);
            if (!from || !to) return;
            from.links.push({ label: to.label, predicate: relation.predicate || '' });
        });
        relaxDisk(definitions);
        mountSystems(definitions);
        document.body.classList.add('galaxy-live');
        renderGalaxyLayers();
    }

    function relaxDisk(definitions) {
        const minX = 0.12;
        const minZ = 0.16;
        for (let pass = 0; pass < 6; pass += 1) {
            for (let i = 0; i < definitions.length; i += 1) {
                for (let j = i + 1; j < definitions.length; j += 1) {
                    let dx = definitions[i].x - definitions[j].x;
                    let dz = definitions[i].z - definitions[j].z;
                    if (dx === 0 && dz === 0) { dx = 0.02; dz = 0.01; }
                    const nx = dx / minX;
                    const nz = dz / minZ;
                    const ell = Math.hypot(nx, nz);
                    if (ell >= 1 || ell === 0) continue;
                    const push = (1 - ell) * 0.22;
                    const ux = nx / ell;
                    const uz = nz / ell;
                    definitions[i].x += ux * minX * push;
                    definitions[i].z += uz * minZ * push;
                    definitions[j].x -= ux * minX * push;
                    definitions[j].z -= uz * minZ * push;
                }
            }
        }
    }

    function formatGalaxyCount(count) {
        return String(count).replace(/\B(?=(\d{3})+(?!\d))/g, "'");
    }

    async function loadLiveGalaxy() {
        try {
            const response = await fetch('/api/ontology/summary');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            const types = Array.isArray(data.types) ? data.types : [];
            liveRelations = Array.isArray(data.relations) ? data.relations : [];
            const populated = types
                .filter((type) => type && type.count > 0 && type.label)
                .sort((a, b) => b.count - a.count || String(a.label).localeCompare(b.label, 'de'));
            const samples = await Promise.all(populated.map(async (type) => {
                const limit = Math.min(type.count, 48);
                try {
                    const ents = await fetch(`/api/ontology/entities?type=${encodeURIComponent(type.id)}`);
                    if (!ents.ok) return [];
                    const json = await ents.json();
                    return (json.entities || []).slice(0, limit)
                        .filter((entity) => entity && entity.label)
                        .map((entity) => ({ id: entity.id, label: entity.label }));
                } catch (err) {
                    return [];
                }
            }));
            liveTypes = populated.map((type, index) => ({
                id: type.id,
                label: type.label,
                count: type.count,
                entities: samples[index],
            }));
            if (!populated.length) throw new Error('empty');
            applyLiveLayers();
        } catch (err) {
            mountSystems(systemDefinitions);
        }
    }
    reducedMotion.addEventListener('change', restart);
    renderHiddenTray();
    loadLiveGalaxy();

    restart();
}());
