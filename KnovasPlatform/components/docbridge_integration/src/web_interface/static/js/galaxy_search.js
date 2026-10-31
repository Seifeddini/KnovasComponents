// Pure Cortex search: the document corpus, ranking, and the geometry of
// the three structured result views. No DOM. The galaxy and the overlay
// consume this object; the tests run it in Node.
'use strict';

(function (root) {
    const DOCUMENT_TYPE = 'Dokumente';

    const DEMO_QUERIES = [
        { query: 'M-2026-041', empty: false,
          blurb: 'Eine Akte, Treffer in sechs Wissensräumen' },
        { query: 'Vollmacht', empty: false,
          blurb: 'Dasselbe Dokumentwort, über viele Mandate verstreut' },
        { query: 'Keller', empty: false,
          blurb: 'Person, Mandant und Mandat gleichzeitig' },
        { query: 'Baumann', empty: false,
          blurb: 'Ein Nachlass, als Dossier gebündelt' },
        { query: 'HG260184-O', empty: false,
          blurb: 'Die Gerichtsnummer findet das Verfahren' },
        { query: 'Müller', empty: false,
          blurb: 'Firma, Mandat und Person im selben Graphen' },
        { query: 'Kryptowährung', empty: true,
          blurb: 'Kein Treffer — die Galaxie bleibt ungewichtet' },
    ];

    // Each document is a node. links file it next to an entity; an empty
    // links list leaves it unfiled in the graph (still searchable).
    const DOCUMENTS = [
        { id: 'doc-m041-mandat', label: 'Mandatsvereinbarung M-2026-041.pdf',
          meta: 'Mandatsakte · unterzeichnet', matters: ['M-2026-041'],
          links: [['Mandate', 'Müller Bau AG · Werklohnstreit']] },
        { id: 'doc-m041-klage', label: 'Klage Werklohnstreit.pdf',
          meta: 'Rechtsschrift · Handelsgericht', matters: ['M-2026-041'],
          links: [['Mandate', 'Müller Bau AG · Werklohnstreit']] },
        { id: 'doc-m041-vollmacht', label: 'Vollmacht Müller Bau AG.pdf',
          meta: 'Vollmacht · qualifiziert signiert', matters: ['M-2026-041'],
          links: [['Mandate', 'Müller Bau AG · Werklohnstreit']] },
        { id: 'doc-m041-gutachten', label: 'Gutachten Haller Baumängel.pdf',
          meta: 'Privatgutachten · 8. Oktober 2026', matters: ['M-2026-041'],
          links: [['Personen', 'Prof. Dr. Reto Haller']] },
        { id: 'doc-m041-chronik', label: 'Sachverhaltschronologie Müller Bau.pdf',
          meta: 'Arbeitsprodukt · aktualisiert heute', matters: ['M-2026-041'],
          links: [['Mandate', 'Müller Bau AG · Werklohnstreit']] },
        { id: 'doc-m041-beilagen', label: 'Beilagenverzeichnis Werklohnstreit.xlsx',
          meta: 'Beweismittel · 42 Positionen', matters: ['M-2026-041'],
          links: [['Mandate', 'Müller Bau AG · Werklohnstreit']] },
        { id: 'doc-m041-korrespondenz', label: 'Korrespondenz Helvetia Projektentwicklung.msg',
          meta: 'E-Mail · 15. Oktober 2026', matters: ['M-2026-041'],
          links: [['Gegenparteien', 'Helvetia Projektentwicklung AG']] },
        { id: 'doc-m041-verfuegung', label: 'Verfügung HG260184-O.pdf',
          meta: 'Verfügung · Schriftenwechsel', matters: ['M-2026-041'],
          links: [['Gerichte', 'Handelsgericht Zürich']] },
        { id: 'doc-m041-kyc', label: 'Mandantenstammblatt K-10041.pdf',
          meta: 'KYC · geprüft', matters: ['M-2026-041'],
          links: [['Mandanten', 'Müller Bau AG']] },
        { id: 'doc-m041-markus', label: 'Vollmacht Markus Müller.pdf',
          meta: 'Vollmacht · 8. Oktober 2026', matters: ['M-2026-041'],
          links: [['Personen', 'Markus Müller']] },

        { id: 'doc-m038-mandat', label: 'Mandatsvereinbarung M-2026-038.pdf',
          meta: 'Mandatsakte · unterzeichnet', matters: ['M-2026-038'],
          links: [['Mandate', 'Meier Immobilien · Mietzinsverfahren']] },
        { id: 'doc-m038-schlichtung', label: 'Schlichtungsgesuch Mietgericht.pdf',
          meta: 'Eingabe · Friedensrichteramt', matters: ['M-2026-038'],
          links: [['Gerichte', 'Friedensrichteramt Kreis 7']] },
        { id: 'doc-m038-vollmacht', label: 'Vollmacht Meier Immobilien.pdf',
          meta: 'Vollmacht · unterzeichnet', matters: ['M-2026-038'],
          links: [['Mandanten', 'Meier Immobilien AG']] },

        { id: 'doc-m057-term', label: 'Term Sheet NovaTech Apex.pdf',
          meta: 'Transaktionsdokument · vertraulich', matters: ['M-2026-057'],
          links: [['Mandate', 'NovaTech AG · Beteiligungsrunde']] },
        { id: 'doc-m057-dd', label: 'Due-Diligence-Bericht NovaTech.pdf',
          meta: 'Arbeitsprodukt · 18. März 2026', matters: ['M-2026-057'],
          links: [['Mandate', 'NovaTech AG · Beteiligungsrunde']] },
        { id: 'doc-m057-vollmacht', label: 'Vollmacht NovaTech AG.pdf',
          meta: 'Vollmacht · unterzeichnet', matters: ['M-2026-057'],
          links: [['Mandanten', 'NovaTech AG']] },
        { id: 'doc-m057-hr', label: 'Eingabe Handelsregisteramt Zürich.pdf',
          meta: 'Behördeneingabe · Voranfrage', matters: ['M-2026-057'],
          links: [['Behörden', 'Handelsregisteramt Zürich']] },

        { id: 'doc-m119-mandat', label: 'Mandatsvereinbarung M-2025-119.pdf',
          meta: 'Mandatsakte · unterzeichnet', matters: ['M-2025-119'],
          links: [['Mandate', 'Familie Baumann · Nachlass']] },
        { id: 'doc-m119-inventar', label: 'Nachlassinventar Baumann.xlsx',
          meta: 'Inventar · in Teilung', matters: ['M-2025-119'],
          links: [['Mandate', 'Familie Baumann · Nachlass']] },
        { id: 'doc-m119-vertrag', label: 'Erbteilungsvertrag Entwurf.pdf',
          meta: 'Vertragsentwurf · 12. Dezember 2026', matters: ['M-2025-119'],
          links: [['Mandate', 'Familie Baumann · Nachlass']] },
        { id: 'doc-m119-urkunden', label: 'Zivilstandsurkunden Küsnacht.pdf',
          meta: 'Urkunden · vollständig', matters: ['M-2025-119'],
          links: [['Behörden', 'Zivilstandsamt Küsnacht']] },
        { id: 'doc-m119-kyc', label: 'Mandantenstammblatt Erbengemeinschaft.pdf',
          meta: 'KYC · geprüft', matters: ['M-2025-119'],
          links: [['Mandanten', 'Erbengemeinschaft Baumann']] },

        { id: 'doc-m063-beschwerde', label: 'Beschwerdeschrift Vergabe Stadtwerke.pdf',
          meta: 'Rechtsschrift · Verwaltungsgericht', matters: ['M-2026-063'],
          links: [['Mandate', 'Stadtwerke Nord · Vergabebeschwerde']] },
        { id: 'doc-m063-vollmacht', label: 'Vollmacht Stadtwerke Nord.pdf',
          meta: 'Vollmacht · unterzeichnet', matters: ['M-2026-063'],
          links: [['Mandanten', 'Stadtwerke Nord']] },
        { id: 'doc-m063-esti', label: 'Aktennotiz ESTI-Prüfung.pdf',
          meta: 'Notiz · technische Prüfung', matters: ['M-2026-063'],
          links: [['Behörden', 'Eidgenössisches Starkstrominspektorat']] },

        { id: 'doc-m071-klage', label: 'Kündigungsschutzklage Marti.pdf',
          meta: 'Rechtsschrift · Arbeitsgericht', matters: ['M-2026-071'],
          links: [['Mandate', 'Alpenblick Klinik · Arbeitsstreit']] },
        { id: 'doc-m071-vollmacht', label: 'Vollmacht Alpenblick Klinik.pdf',
          meta: 'Vollmacht · unterzeichnet', matters: ['M-2026-071'],
          links: [['Mandanten', 'Alpenblick Klinik AG']] },
        { id: 'doc-m071-vergleich', label: 'Vergleichsvorschlag Arbeitsstreit.pdf',
          meta: 'Entwurf · 27. Oktober 2026', matters: ['M-2026-071'],
          links: [['Mandate', 'Alpenblick Klinik · Arbeitsstreit']] },

        { id: 'doc-m029-ruling', label: 'Ruling-Antrag Keller Holding.pdf',
          meta: 'Eingabe · Steueramt', matters: ['M-2026-029'],
          links: [['Mandate', 'Keller Holding · Steuerreorganisation']] },
        { id: 'doc-m029-gutachten', label: 'Umstrukturierungsgutachten Keller.pdf',
          meta: 'Memo · Steuer- und Gesellschaftsrecht', matters: ['M-2026-029'],
          links: [['Mandate', 'Keller Holding · Steuerreorganisation']] },
        { id: 'doc-m029-kyc', label: 'KYC Keller Holding.pdf',
          meta: 'Compliance · freigegeben', matters: ['M-2026-029'],
          links: [['Mandanten', 'Keller Holding AG']] },

        { id: 'doc-m084-lizenz', label: 'Lizenzvertrag Redline GreenGrid.docx',
          meta: 'Memo · IT- und Immaterialgüterrecht', matters: ['M-2026-084'],
          links: [['Mandate', 'GreenGrid GmbH · Lizenzvertrag']] },
        { id: 'doc-m084-vollmacht', label: 'Vollmacht GreenGrid GmbH.pdf',
          meta: 'Vollmacht · unterzeichnet', matters: ['M-2026-084'],
          links: [['Mandanten', 'GreenGrid GmbH']] },
        { id: 'doc-m084-marke', label: 'Markenanmeldung TM-824771.pdf',
          meta: 'Register · Widerspruchsfrist', matters: ['M-2026-084'],
          links: [['Behörden', 'Eidgenössisches Institut für Geistiges Eigentum']] },
        { id: 'doc-m084-gridware', label: 'Stellungnahme Gridware Europe.pdf',
          meta: 'Rechtsschrift · Lizenzgeberin', matters: ['M-2026-084'],
          links: [['Gegenparteien', 'Gridware Europe S.A.']] },

        { id: 'doc-anna-kontakt', label: 'Kontaktakte Dr. Anna Keller.pdf',
          meta: 'CRM · Kanzlei Keller & Partner', matters: ['M-2026-041', 'M-2026-057'],
          links: [['Personen', 'Dr. Anna Keller']] },
        { id: 'doc-eva-notiz', label: 'Besprechungsnotiz Eva Schmid.pdf',
          meta: 'Aktennotiz · 8. Oktober 2026', matters: ['M-2026-041'],
          links: [['Personen', 'lic. iur. Eva Schmid']] },
        { id: 'doc-hg-eingang', label: 'Eingangsbestätigung Rechtsschrift HG.pdf',
          meta: 'Gerichtspost · 14. Oktober 2026', matters: ['M-2026-041'],
          links: [['Gerichte', 'Handelsgericht Zürich']] },
        { id: 'doc-fristen', label: 'Fristenkontrollblatt HG260184.pdf',
          meta: 'Frist · Klageantwort 30. Oktober 2026', matters: ['M-2026-041'],
          links: [['Gerichte', 'Handelsgericht Zürich']] },
        { id: 'doc-parteidossier', label: 'Parteidossier Helvetia.pdf',
          meta: 'Parteienakte · vertraulich', matters: ['M-2026-041'],
          links: [['Gegenparteien', 'Helvetia Projektentwicklung AG']] },
        { id: 'doc-weko', label: 'Empfangsbestätigung WEKO.pdf',
          meta: 'eGov-Zustellung · 14. Oktober 2026', matters: ['M-2026-057'],
          links: [['Behörden', 'WEKO']] },
        { id: 'doc-thomas-mail', label: 'Korrespondenz Thomas Frei GreenGrid.msg',
          meta: 'E-Mail · 15. Oktober 2026', matters: ['M-2026-084'],
          links: [['Personen', 'Thomas Frei']] },
        { id: 'doc-hr-mueller', label: 'Handelsregisterauszug Müller Bau.pdf',
          meta: 'Registerauszug · aktuell', matters: ['M-2026-041'],
          links: [['Mandanten', 'Müller Bau AG']] },
        { id: 'doc-konflikt-green', label: 'Konfliktprüfung GreenGrid.pdf',
          meta: 'Compliance · dokumentiert', matters: ['M-2026-084'],
          links: [['Mandanten', 'GreenGrid GmbH']] },

        { id: 'doc-vollmacht-vorlage', label: 'Vollmacht Muster Vorlage.pdf',
          meta: 'Vorlage · nicht zugeordnet', matters: [],
          links: [] },
        { id: 'doc-kanzlei-handbuch', label: 'Kanzlei-Vorlage Fristenblatt.docx',
          meta: 'Interne Vorlage · unfiled', matters: [],
          links: [] },
    ];

    function versionFromSearch(search) {
        const raw = String(search || '');
        const query = raw.charAt(0) === '?' ? raw.slice(1) : raw;
        const value = Number.parseInt(new URLSearchParams(query).get('version') || '1', 10);
        return value >= 1 && value <= 4 ? value : 1;
    }

    function queryFromSearch(search) {
        const raw = String(search || '');
        const query = raw.charAt(0) === '?' ? raw.slice(1) : raw;
        return String(new URLSearchParams(query).get('q') || '');
    }

    function normalize(value) {
        return String(value || '')
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .replace(/ß/g, 'ss')
            .toLocaleLowerCase('de-CH')
            .replace(/ae/g, 'a')
            .replace(/oe/g, 'o')
            .replace(/ue/g, 'u')
            .replace(/[^\p{L}\p{N}]+/gu, ' ')
            .trim();
    }

    function compact(value) {
        return normalize(value).replace(/\s+/g, '');
    }

    function tokensOf(query) {
        const raw = String(query || '').trim();
        const tokens = [];
        const seen = new Set();
        const add = (token) => {
            if (!token || seen.has(token)) return;
            seen.add(token);
            tokens.push(token);
        };
        const identifier = /[A-Za-zÄÖÜäöü]{0,8}-?\d[\w./-]*/g;
        let match;
        while ((match = identifier.exec(raw))) {
            add(compact(match[0]));
        }
        normalize(raw.replace(/[A-Za-zÄÖÜäöü]{0,8}-?\d[\w./-]*/g, ' '))
            .split(/\s+/)
            .filter(Boolean)
            .forEach(add);
        return tokens;
    }

    function hash01(text) {
        let hash = 2166136261;
        const raw = String(text || '');
        for (let i = 0; i < raw.length; i += 1) {
            hash ^= raw.charCodeAt(i);
            hash = Math.imul(hash, 16777619);
        }
        return (hash >>> 0) / 4294967296;
    }

    function collectMatters(record) {
        const found = [];
        const push = (value) => {
            String(value || '').split(/[,;]/).forEach((part) => {
                const match = part.trim().match(/M-\d{4}-\d+/);
                if (match && !found.includes(match[0])) found.push(match[0]);
            });
        };
        if (!record || typeof record !== 'object') return found;
        Object.keys(record).forEach((key) => {
            const value = record[key];
            if (typeof value === 'string') push(value);
        });
        if (Array.isArray(record.matters)) record.matters.forEach(push);
        return found;
    }

    function searchTextOf(record) {
        if (!record || typeof record !== 'object') return '';
        return Object.keys(record)
            .filter((key) => key !== 'x' && key !== 'y' && key !== 'z'
                && key !== 'size' && key !== 'connection')
            .map((key) => record[key])
            .filter((value) => typeof value === 'string' || typeof value === 'number')
            .join(' ');
    }

    function entityHome(entity) {
        if (entity && [entity.x, entity.y, entity.z].every(Number.isFinite)) {
            return { x: entity.x, y: entity.y, z: entity.z };
        }
        return { x: 0, y: 0, z: 0 };
    }

    function placeDocument(doc, entity, index) {
        if (doc && [doc.x, doc.y, doc.z].every(Number.isFinite)) {
            return { x: doc.x, y: doc.y, z: doc.z };
        }
        if (!entity) {
            const slot = index % 5;
            return {
                x: 0.018 * (slot - 2),
                y: 0.03,
                z: -0.018 - index * 0.0015,
            };
        }
        const home = entityHome(entity);
        const angle = hash01(doc.id) * Math.PI * 2;
        const radius = 0.03 + hash01(`${doc.id}:r`) * 0.05;
        return {
            x: home.x + Math.cos(angle) * radius,
            y: home.y + 0.008,
            z: home.z + Math.sin(angle) * radius,
        };
    }

    function placeDocuments(systems, documents) {
        const byKey = new Map();
        (systems || []).forEach((system) => {
            (system.entityNodes || []).forEach((entity) => {
                byKey.set(`${system.label}::${entity.label}`, entity);
            });
        });
        return (documents || DOCUMENTS).map((doc, index) => {
            const link = (doc.links && doc.links[0]) || null;
            const entity = link ? byKey.get(`${link[0]}::${link[1]}`) : null;
            return Object.assign({}, doc, placeDocument(doc, entity, index));
        });
    }

    function buildIndex(systems, documents) {
        const nodes = [];
        const byKey = new Map();
        const matters = new Set();

        (systems || []).forEach((system) => {
            if (!system || !system.label) return;
            (system.entityNodes || []).forEach((entity) => {
                const key = `${system.label}::${entity.label}`;
                const node = {
                    key,
                    kind: 'entity',
                    type: system.label,
                    label: entity.label,
                    home: entityHome(entity),
                    links: entity.connection
                        ? [`${entity.connection.system}::${entity.connection.entity}`]
                        : [],
                    matters: collectMatters(entity),
                    searchText: searchTextOf(entity),
                    ref: { id: key },
                    source: entity,
                };
                nodes.push(node);
                byKey.set(key, node);
                node.matters.forEach((matter) => matters.add(matter));
            });
        });

        nodes.forEach((node) => {
            node.links.forEach((otherKey) => {
                const other = byKey.get(otherKey);
                if (!other) return;
                if (!other.links.includes(node.key)) other.links.push(node.key);
            });
        });
        nodes.forEach((node) => {
            node.links.forEach((otherKey) => {
                const other = byKey.get(otherKey);
                if (!other) return;
                const source = other.type === 'Mandate' ? other
                    : node.type === 'Mandate' ? node : null;
                if (!source) return;
                const target = source === node ? other : node;
                source.matters.forEach((matter) => {
                    if (!target.matters.includes(matter)) target.matters.push(matter);
                    matters.add(matter);
                });
            });
        });

        const corpus = (documents && documents.length) ? documents : DOCUMENTS;
        corpus.forEach((doc, index) => {
            const linkKeys = (doc.links || []).map(([system, entity]) => `${system}::${entity}`);
            const host = linkKeys.length ? byKey.get(linkKeys[0]) : null;
            const key = `${DOCUMENT_TYPE}::${doc.label}`;
            const node = {
                key,
                kind: 'document',
                type: DOCUMENT_TYPE,
                label: doc.label,
                meta: doc.meta || '',
                home: placeDocument(doc, host && host.source || host, index),
                links: linkKeys.slice(),
                matters: (doc.matters || []).slice(),
                searchText: `${doc.meta || ''} ${(doc.matters || []).join(' ')}`,
                ref: { id: doc.id },
                source: doc,
            };
            nodes.push(node);
            byKey.set(key, node);
            node.matters.forEach((matter) => matters.add(matter));
            linkKeys.forEach((otherKey) => {
                const other = byKey.get(otherKey);
                if (!other) return;
                if (!other.links.includes(key)) other.links.push(key);
            });
        });

        return { nodes, byKey, matters };
    }

    function haystack(node) {
        return normalize([
            node.label,
            node.type,
            node.meta || '',
            node.searchText || '',
            (node.matters || []).join(' '),
        ].join(' '));
    }

    function scoreNode(node, tokens, normalizedQuery) {
        const label = normalize(node.label);
        const hay = haystack(node);
        const compactLabel = compact(node.label);
        const compactHay = compact(hay);
        const compactMatters = compact((node.matters || []).join(' '));
        const compactIds = compact([
            node.source && node.source.reference,
            node.source && node.source.number,
        ].filter(Boolean).join(' '));
        let score = 0;
        let matchedTokens = 0;
        tokens.forEach((token) => {
            const packed = compact(token);
            let tokenScore = 0;
            if (label.includes(token)) tokenScore += 5;
            if (label.startsWith(token)) tokenScore += 2;
            if (hay.includes(token)) tokenScore += 2;
            if (packed.length > 4 && compactIds && compactIds.includes(packed)) {
                tokenScore += node.kind === 'entity' ? 16 : 4;
            }
            if (packed.length > 4 && compactLabel.includes(packed)) tokenScore += 4;
            if (packed.length > 4 && compactHay.includes(packed)) tokenScore += 2;
            if (packed && compactMatters && (compactMatters === packed
                    || compactMatters.includes(packed))) {
                tokenScore += node.type === 'Mandate' ? 18
                    : node.kind === 'entity' ? 8 : 3;
            }
            if (tokenScore > 0) matchedTokens += 1;
            score += tokenScore;
        });
        if (normalizedQuery && label.includes(normalizedQuery)) score += 5;
        return { score, matchedTokens };
    }

    function assignTiers(hits) {
        if (!hits.length) return hits;
        hits.forEach((hit, index) => {
            const span = hits.length === 1 ? 0 : index / (hits.length - 1);
            if (span < 0.34) hit.tier = 0;
            else if (span < 0.67) hit.tier = 1;
            else hit.tier = 2;
        });
        return hits;
    }

    function search(index, query) {
        const trimmed = String(query || '').trim();
        const normalizedQuery = normalize(trimmed);
        if (!normalizedQuery) return { mode: 'idle', hits: [], query: '' };
        const tokens = tokensOf(trimmed);
        const hits = [];
        (index.nodes || []).forEach((node) => {
            const ranked = scoreNode(node, tokens, normalizedQuery);
            if (ranked.score > 0) {
                hits.push({
                    node,
                    key: node.key,
                    score: ranked.score,
                    matchedTokens: ranked.matchedTokens,
                    tier: 0,
                });
            }
        });
        hits.sort((left, right) =>
            right.score - left.score
            || (left.node.kind === 'entity' ? 0 : 1) - (right.node.kind === 'entity' ? 0 : 1)
            || left.node.label.localeCompare(right.node.label, 'de'));
        assignTiers(hits);

        if (!hits.length) return { mode: 'none', hits, query: trimmed };

        const covered = new Set();
        hits.forEach((hit) => {
            const hay = haystack(hit.node);
            const label = normalize(hit.node.label);
            const packedHay = compact(hay + ' ' + hit.node.label);
            tokens.forEach((token) => {
                if (hay.includes(token) || label.includes(token)
                        || packedHay.includes(compact(token))) {
                    covered.add(token);
                }
            });
        });
        return {
            mode: covered.size < tokens.length ? 'partial' : 'all',
            hits,
            query: trimmed,
        };
    }

    function relations(index, hits) {
        const keys = new Set((hits || []).map((hit) => hit.key));
        const seen = new Set();
        const edges = [];
        const add = (left, right) => {
            if (!keys.has(left) || !keys.has(right) || left === right) return;
            const id = left < right ? `${left}|${right}` : `${right}|${left}`;
            if (seen.has(id)) return;
            seen.add(id);
            edges.push([left, right]);
        };
        (hits || []).forEach((hit) => {
            (hit.node.links || []).forEach((other) => add(hit.key, other));
        });
        const mandateByMatter = {};
        (hits || []).forEach((hit) => {
            if (hit.node.type === 'Mandate' && hit.node.matters && hit.node.matters[0]) {
                mandateByMatter[hit.node.matters[0]] = hit.key;
            }
        });
        (hits || []).forEach((hit) => {
            (hit.node.matters || []).forEach((matter) => {
                if (mandateByMatter[matter]) add(hit.key, mandateByMatter[matter]);
            });
        });
        return edges;
    }

    function orbitLayout(hits, box) {
        const maxNodes = box.maxNodes == null ? hits.length : box.maxNodes;
        const visible = hits.slice(0, maxNodes);
        const hidden = Math.max(0, hits.length - visible.length);
        const positions = {};
        const typeOrder = [];
        visible.forEach((hit) => {
            if (!typeOrder.includes(hit.node.type)) typeOrder.push(hit.node.type);
        });
        const sectors = typeOrder.map((type) => ({
            type,
            count: visible.filter((hit) => hit.node.type === type).length,
        }));
        const ringForTier = { 0: 0.7, 1: 0.82, 2: 0.94 };
        visible.forEach((hit, index) => {
            const span = visible.length === 1 ? 0 : index / (visible.length - 1);
            hit.tier = span < 0.34 ? 0 : span < 0.67 ? 1 : 2;
        });
        const tierCount = { 0: 0, 1: 0, 2: 0 };
        visible.forEach((hit) => { tierCount[hit.tier] += 1; });
        const tierUsed = { 0: 0, 1: 0, 2: 0 };
        visible.forEach((hit) => {
            const count = Math.max(1, tierCount[hit.tier]);
            const index = tierUsed[hit.tier];
            tierUsed[hit.tier] = index + 1;
            const angle = -Math.PI / 2 + ((index + 0.5) / count) * Math.PI * 2;
            const ring = ringForTier[hit.tier] || 0.82;
            positions[hit.key] = {
                x: box.cx + Math.cos(angle) * box.rx * ring,
                y: box.cy + Math.sin(angle) * box.ry * ring,
            };
        });
        return { positions, hidden, sectors };
    }

    function dossierLayout(index, hits, box) {
        const groups = new Map();
        const unfiled = [];
        (hits || []).forEach((hit) => {
            const matter = (hit.node.matters && hit.node.matters[0]) || null;
            if (!matter) {
                unfiled.push(hit);
                return;
            }
            if (!groups.has(matter)) groups.set(matter, []);
            groups.get(matter).push(hit);
        });
        const matterKeys = [...groups.keys()].sort((left, right) => {
            const leftHits = groups.get(left);
            const rightHits = groups.get(right);
            const leftScore = Math.max(...leftHits.map((hit) => hit.score));
            const rightScore = Math.max(...rightHits.map((hit) => hit.score));
            return rightScore - leftScore || rightHits.length - leftHits.length;
        });
        const columnMatters = unfiled.length ? matterKeys.concat([null]) : matterKeys;
        const count = Math.max(columnMatters.length, 1);
        const gap = 18;
        const columnWidth = Math.min(
            280,
            (box.width - gap * (count - 1)) / count,
        );
        const totalWidth = count * columnWidth + gap * (count - 1);
        const startX = box.left + Math.max(0, (box.width - totalWidth) / 2);
        const columns = columnMatters.map((matter, index) => {
            const columnHits = matter == null ? unfiled : groups.get(matter);
            const mandate = columnHits.find((hit) => hit.node.type === 'Mandate');
            return {
                matter,
                headerHit: mandate ? mandate.key
                    : (columnHits[0] ? columnHits[0].key : null),
                x: startX + index * (columnWidth + gap),
                width: columnWidth,
                hits: columnHits,
            };
        });
        const positions = {};
        let hidden = 0;
        const rowHeight = 38;
        columns.forEach((column) => {
            column.hits.forEach((hit, row) => {
                const y = box.top + 56 + row * rowHeight;
                if (y > box.top + box.height - 24) {
                    hidden += 1;
                    return;
                }
                positions[hit.key] = {
                    x: column.x + column.width / 2,
                    y,
                };
            });
        });
        const bridges = [];
        (hits || []).forEach((hit) => {
            const homeMatter = (hit.node.matters && hit.node.matters[0]) || null;
            if (!positions[hit.key]) return;
            (hit.node.matters || []).slice(1).forEach((matter) => {
                const column = columns.findIndex((entry) => entry.matter === matter);
                if (column >= 0) bridges.push({ key: hit.key, column });
            });
            (hit.node.links || []).forEach((otherKey) => {
                const other = (hits || []).find((candidate) => candidate.key === otherKey);
                if (!other) return;
                const otherMatter = (other.node.matters && other.node.matters[0]) || null;
                if (otherMatter === homeMatter) return;
                const column = columns.findIndex((entry) => entry.matter === otherMatter);
                if (column >= 0) bridges.push({ key: hit.key, column });
            });
        });
        return { columns, positions, hidden, bridges };
    }

    function constellationLayout(hits, box) {
        const positions = {};
        (hits || []).forEach((hit) => {
            positions[hit.key] = {
                x: box.cx + (hit.node.home ? hit.node.home.x : 0) * (box.rx || 280),
                y: box.cy + (hit.node.home ? hit.node.home.z : 0) * (box.ry || 180),
            };
        });
        const types = [];
        (hits || []).forEach((hit) => {
            if (!types.includes(hit.node.type)) types.push(hit.node.type);
        });
        return { positions, hidden: 0, groups: types };
    }

    root.CortexGalaxySearch = {
        DOCUMENT_TYPE,
        DEMO_QUERIES,
        DOCUMENTS,
        versionFromSearch,
        queryFromSearch,
        normalize,
        placeDocuments,
        buildIndex,
        search,
        relations,
        orbitLayout,
        dossierLayout,
        constellationLayout,
    };
}(typeof window !== 'undefined' ? window : globalThis));
