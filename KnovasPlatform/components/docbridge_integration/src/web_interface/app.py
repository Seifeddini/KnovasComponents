"""
Web Interface for DocBridge Document Search

Flask-based web application for lawyers to search and access DocBridge documents
through Knovas.
"""

import sys
import io
import os
import json
import hmac
import secrets
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for, g
from flask_cors import CORS
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import subprocess
import platform
import re
from urllib.parse import quote

from config_loader import get_config
from context_store import enrich_result_with_context
from identity.principal import ClientAssertedGroupsError, PrincipalBroker
from knovas_client import KnovasAPIClient
from file_utils import AutoDocFileHandler
from open_tokens import OpenTokenManager
from ontology_filters import get_filter_engine
from ontology_store import get_ontology
from web_interface.preview import (
    PreviewFailed,
    PreviewUnsupported,
    extract_markdown,
    preview_kind,
    render_first_page_png,
)
from unc_path import (
    filesystem_path_to_client_local,
    map_path_with_roots,
    normalize_client_local_root,
    normalize_local_root,
    normalize_unc_root,
    parse_unc_roots_list,
)

logger = logging.getLogger(__name__)


class _RequestScopedBroker:
    """Binds PrincipalBroker to whoever is signed in on *this* request.

    The broker reads user_access_groups at mint time with no caching, so an
    administrator's revocation takes effect on the user's next request rather
    than when their session happens to expire.
    """

    def __init__(self, gate, signer, tenant_id):
        self._gate = gate
        self._signer = signer
        self._tenant_id = tenant_id

    def current_user(self):
        return self._gate.current_user()

    def assertion_for(self, user):
        from identity.principal import PrincipalBroker

        return PrincipalBroker(
            user_repo=self._gate.users(),
            signer=self._signer,
            tenant_id=self._tenant_id,
        ).assertion_for(user)


def _configure_logging_for_wsgi(config) -> None:
    """
    Gunicorn imports wsgi:app without running main(), so logging.basicConfig never runs.
    Ensure INFO logs (e.g. search similarity debug) reach docker logs (stderr).
    """
    level_name = (os.getenv('LOG_LEVEL') or config.get('logging.level') or 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(
            logging.Formatter('%(asctime)s %(levelname)s [%(name)s] %(message)s')
        )
        root.addHandler(h)
    for name in ('web_interface', 'web_interface.app', 'knovas_client'):
        logging.getLogger(name).setLevel(level)


_search_enrichment_cache: Dict[str, dict] = {}
_search_enrichment_unique: List[dict] = []
_search_enrichment_by_basename: Dict[str, List[dict]] = {}
_search_enrichment_inferred_prefixes: List[str] = []
_search_enrichment_mtime: float = 0.0


def _build_location_summary(results: List[Dict[str, Any]], *, limit: int = 8) -> List[Dict[str, Any]]:
    """Lightweight per-hit location fields for browser DevTools / support."""
    rows: List[Dict[str, Any]] = []
    for r in results[:limit]:
        rows.append({
            'doc_id': r.get('doc_id'),
            'page_number': r.get('page_number'),
            'sentence_number': r.get('sentence_number'),
            'top_chunks': len(r.get('top_chunks') or []),
        })
    return rows


def _build_similarity_debug(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Structured payload for API / browser DevTools (after enrichment + refinement)."""
    rows = []
    scores: List[float] = []
    for r in results:
        s = float(r.get('score') or 0)
        scores.append(s)
        rows.append({
            'doc_id': r.get('doc_id'),
            'score': s,
            'title': r.get('title'),
            'page_number': r.get('page_number'),
            'sentence_number': r.get('sentence_number'),
            'cosine_similarity': r.get('cosine_similarity'),
            'cosine_distance': r.get('cosine_distance'),
        })
    return {
        'count': len(results),
        'max_score': max(scores) if scores else None,
        'min_score': min(scores) if scores else None,
        'results': rows,
    }


def _autodoc_identifier_prefixes() -> List[str]:
    """RC/Knovas pointer prefixes to strip (comma- or semicolon-separated)."""
    raw = (os.getenv("AUTODOC_IDENTIFIER_PREFIX") or "").strip()
    if not raw:
        return []
    parts = raw.replace(";", ",").split(",")
    return [p.strip().strip("/") for p in parts if p.strip().strip("/")]


def _effective_identifier_prefixes() -> List[str]:
    """Env prefixes plus prefixes inferred from enrichment JSONL (e.g. corpus)."""
    seen: set[str] = set()
    out: List[str] = []
    for raw in _autodoc_identifier_prefixes() + _search_enrichment_inferred_prefixes:
        key = raw.strip().strip("/").lower()
        if key and key not in seen:
            seen.add(key)
            out.append(raw.strip().strip("/"))
    return out


def _rel_path_for_autodoc(pointer: str) -> str:
    """
    Map Knovas pointer to a path under the autodoc mount.

    RemoteController sync uses identifier_prefix (e.g. ``corpus/rel/path.txt``).
    Set AUTODOC_IDENTIFIER_PREFIX=corpus when the mount root is the corpus folder itself.
    Multiple prefixes (corpus,winjur) support mixed tenants during RC prefix migrations.
    """
    rel = (pointer or "").strip().replace("\\", "/")
    for prefix in _effective_identifier_prefixes():
        needle = prefix + "/"
        if rel.lower().startswith(needle.lower()):
            rel = rel[len(needle) :]
            break
    return rel


def _log_search_similarity_debug(query: str, results: List[Dict[str, Any]]) -> None:
    """Docker logs: see whether Knovas scores survived into each hit."""
    if not results:
        logger.info("Search similarity debug query=%r: 0 results", query)
        return
    scores = [float(r.get('score') or 0) for r in results]
    brief = [
        {
            'doc_id': r.get('doc_id'),
            'score': float(r.get('score') or 0),
            'page': r.get('page_number'),
            'sentence': r.get('sentence_number'),
            'top_chunks': len(r.get('top_chunks') or []),
            'cos_sim': r.get('cosine_similarity'),
            'cos_dist': r.get('cosine_distance'),
        }
        for r in results
    ]
    logger.info(
        "Search similarity debug query=%r count=%s max_score=%.6f min_score=%.6f detail=%s",
        query,
        len(results),
        max(scores),
        min(scores),
        brief,
    )


def _demo_hit_locations(count: int = 15) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Build primary + top_chunks for local multihit UI demo (distinct page/sentence pairs)."""
    locations: List[Dict[str, Any]] = []
    for i in range(count):
        locations.append({
            'page_number': (i // 5) + 1,
            'sentence_number': (i % 5) + 1,
            'cosine_similarity': round(max(0.55, 0.93 - i * 0.025), 2),
        })
    return locations[0], locations


_demo_primary, _demo_top_chunks = _demo_hit_locations(15)


def _demo_context_snippet(
    before: str,
    match: str,
    after: str,
) -> Dict[str, str]:
    return {
        'before': before.strip(),
        'match': match.strip(),
        'after': after.strip(),
    }


_TEST_SEARCH_FIXTURES: List[Dict[str, Any]] = [
    {
        'doc_id': 'corpus/demo/Mietrecht_Kommentar.pdf',
        'path': 'corpus/demo/Mietrecht_Kommentar.pdf',
        'title': 'Mietrecht Kommentar (Demo: 15 Trefferstellen)',
        'akten_id': '2024-050',
        'type': 'Kommentar',
        'score': _demo_primary['cosine_similarity'],
        'cosine_similarity': _demo_primary['cosine_similarity'],
        'cosine_distance': round(1.0 - float(_demo_primary['cosine_similarity']), 2),
        'page_number': _demo_primary['page_number'],
        'sentence_number': _demo_primary['sentence_number'],
        'document_date': '2024-08-10T09:00:00',
        'top_chunks': _demo_top_chunks,
        'first_page_preview': (
            'Kommentar zum österreichischen Mietrecht (MRG). Dieses Werk erläutert die '
            'Hauptmietzinsregelung, Kündigungsgründe, Mietzinsanpassung und die '
            'Rechte des Mieters bei Mängeln. Gegenstand sind Wohn- und Geschäftsraummieten '
            'sowie die Abgrenzung zu Werkverträgen und Leasing.'
        ),
        'context_snippet': _demo_context_snippet(
            '',
            'Das Mietrecht regelt das entgeltliche Überlassen von Räumen zur Nutzung durch den Mieter.',
            'Für Wohnungen gilt das MRG mit besonderen Kündigungsschutzbestimmungen. '
            'Der Hauptmietzins ist die periodisch zu entrichtende Gegenleistung. '
            'Mietzinsanpassungen bedürfen einer gesetzlichen oder vertraglichen Grundlage. '
            'Bei wesentlichen Mängeln kann der Mieter eine angemessene Minderung verlangen.',
        ),
        'ingested_summary': (
            'Demo-Dokument mit 15 Trefferstellen in einem Suchergebnis. '
            'Suche lokal mit „Mietrecht“ oder „Multitreffer“.'
        ),
        'file_size': 1048576,
        'client_open_unc': r'\\fileserver\AutoDoc\corpus\demo\Mietrecht_Kommentar.pdf',
    },
    {
        'doc_id': 'corpus/2024-001/Mustervertrag.pdf',
        'path': 'corpus/2024-001/Mustervertrag.pdf',
        'title': 'Mustervertrag Kaufvertrag Immobilie',
        'akten_id': '2024-001',
        'type': 'Vertrag',
        'score': 0.91,
        'cosine_similarity': 0.91,
        'cosine_distance': 0.09,
        'page_number': 3,
        'sentence_number': 12,
        'document_date': '2024-03-15T10:00:00',
        'top_chunks': [
            {'page_number': 3, 'sentence_number': 12, 'cosine_similarity': 0.91},
            {'page_number': 2, 'sentence_number': 8, 'cosine_similarity': 0.78},
        ],
        'first_page_preview': (
            'KAUFVERTRAG über eine Wohnimmobilie. Verkäufer: Immobilien GmbH, Käufer: Max Mustermann. '
            'Gegenstand ist das Eigentumsrecht an der Liegenschaft EZ 1234 KG Musterstadt. '
            'Der Vertrag wird im beiderseitigen Einvernehmen geschlossen.'
        ),
        'context_snippet': _demo_context_snippet(
            'Die Vertragsparteien haben die Liegenschaft gemeinsam besichtigt und den Zustand protokolliert. '
            'Der Verkäufer sichert zu, dass keine über die im Grundbuch eingetragenen hinausgehenden Lasten bestehen. '
            'Die Übergabe der Liegenschaft erfolgt nach vollständiger Zahlung des Kaufpreises. '
            'Lastenfreistellung und Gewährleistung richten sich nach den gesetzlichen Bestimmungen. '
            'Ein Treuhandkonto wird bei der beurkundenden Notarin eingerichtet.',
            'Der Kaufpreis in Höhe von EUR 485.000,00 ist spätestens bis zum vereinbarten Übergabetermin zu bezahlen.',
            'Mit Übergabe gehen Nutzen und Lasten auf den Käufer über. Mängelansprüche verjähren nach den '
            'gesetzlichen Fristen. Die Parteien vereinbaren einen Rücktrittsvorbehalt bei Finanzierungsausfall.',
        ),
        'ingested_summary': (
            'Kaufvertrag über eine Wohnimmobilie mit Standardklauseln zu '
            'Kaufpreis, Übergabe und Gewährleistung.'
        ),
        'file_size': 245760,
        'client_open_unc': r'\\fileserver\AutoDoc\corpus\2024-001\Mustervertrag.pdf',
    },
    {
        'doc_id': 'corpus/2024-001/Aktennotiz.txt',
        'path': 'corpus/2024-001/Aktennotiz.txt',
        'title': 'Aktennotiz Übergabetermin',
        'akten_id': '2024-001',
        'type': 'Aktennotiz',
        'score': 0.88,
        'cosine_similarity': 0.88,
        'cosine_distance': 0.12,
        'page_number': 1,
        'sentence_number': 3,
        'document_date': '2024-03-20T11:30:00',
        'top_chunks': [
            {'page_number': 1, 'sentence_number': 3, 'cosine_similarity': 0.88},
        ],
        'first_page_preview': (
            'Aktennotiz vom 20.03.2024. Übergabetermin für die Liegenschaft laut '
            'Kaufvertrag 2024-001 vereinbart für den 05.04.2024, 10:00 Uhr vor Ort.'
        ),
        'context_snippet': _demo_context_snippet(
            'Der Käufer wurde telefonisch über den vorgeschlagenen Termin informiert.',
            'Übergabe der Liegenschaft ist für den 05.04.2024 vorgesehen, vorbehaltlich '
            'vollständiger Kaufpreiszahlung.',
            'Die Schlüsselübergabe erfolgt direkt vor Ort. Ein Übergabeprotokoll wird von '
            'beiden Seiten unterzeichnet.',
        ),
        'ingested_summary': (
            'Aktennotiz zur Vereinbarung des Übergabetermins im Zusammenhang mit dem '
            'Kaufvertrag 2024-001.'
        ),
        'file_size': 247,
        'client_open_unc': r'\\fileserver\AutoDoc\corpus\2024-001\Aktennotiz.txt',
    },
    {
        'doc_id': 'corpus/2024-001/Kaufvertrag.docx',
        'path': 'corpus/2024-001/Kaufvertrag.docx',
        'title': 'Kaufvertrag Immobilie (Entwurf)',
        'akten_id': '2024-001',
        'type': 'Vertrag',
        'score': 0.93,
        'cosine_similarity': 0.93,
        'cosine_distance': 0.07,
        'page_number': 1,
        'sentence_number': 5,
        'document_date': '2024-03-10T09:00:00',
        'top_chunks': [
            {'page_number': 1, 'sentence_number': 5, 'cosine_similarity': 0.93},
            {'page_number': 2, 'sentence_number': 2, 'cosine_similarity': 0.81},
        ],
        'first_page_preview': (
            'KAUFVERTRAG (Entwurf) über eine Wohnimmobilie. Verkäufer: Immobilien GmbH, '
            'Käufer: Max Mustermann. Dieser Entwurf dient der Abstimmung vor Unterzeichnung.'
        ),
        'context_snippet': _demo_context_snippet(
            'Die Vertragsparteien haben sich auf den nachstehenden Kaufpreis geeinigt.',
            'Der Kaufpreis beträgt EUR 485.000,00 und ist bis zum Übergabetermin zu entrichten.',
            'Änderungen an diesem Entwurf bedürfen der Schriftform und der Zustimmung beider Parteien.',
        ),
        'ingested_summary': (
            'Entwurfsfassung des Kaufvertrags über die Wohnimmobilie EZ 1234 KG Musterstadt, '
            'zur Abstimmung vor der Unterzeichnung.'
        ),
        'file_size': 36822,
        'client_open_unc': r'\\fileserver\AutoDoc\corpus\2024-001\Kaufvertrag.docx',
    },
    {
        'doc_id': 'corpus/2024-001/Rueckfrage.msg',
        'path': 'corpus/2024-001/Rueckfrage.msg',
        'title': 'Rückfrage zum Kaufvertrag',
        'akten_id': '2024-001',
        'type': 'E-Mail',
        'score': 0.86,
        'cosine_similarity': 0.86,
        'cosine_distance': 0.14,
        'page_number': 1,
        'sentence_number': 2,
        'document_date': '2024-03-18T15:20:00',
        'top_chunks': [
            {'page_number': 1, 'sentence_number': 2, 'cosine_similarity': 0.86},
        ],
        'first_page_preview': (
            'E-Mail-Rückfrage des Käufers zu einzelnen Klauseln des Kaufvertrags, '
            'insbesondere zur Fälligkeit des Kaufpreises und zum Übergabetermin.'
        ),
        'context_snippet': _demo_context_snippet(
            'Der Käufer bedankt sich für die Zusendung des Entwurfs.',
            'Er bittet um Klarstellung, ob der Übergabetermin verschoben werden kann, '
            'falls sich die Finanzierung verzögert.',
            'Eine Antwort wird bis Ende der Woche erbeten.',
        ),
        'ingested_summary': (
            'Rückfrage des Käufers zu Fälligkeit und Übergabetermin im laufenden Kaufvertrag.'
        ),
        'file_size': 5120,
        'client_open_unc': r'\\fileserver\AutoDoc\corpus\2024-001\Rueckfrage.msg',
    },
    {
        'doc_id': 'corpus/2024-001/Schriftsatz_Klage.docx',
        'path': 'corpus/2024-001/Schriftsatz_Klage.docx',
        'title': 'Schriftsatz Klage',
        'akten_id': '2024-001',
        'type': 'Schriftsatz',
        'score': 0.84,
        'cosine_similarity': 0.84,
        'cosine_distance': 0.16,
        'page_number': 1,
        'sentence_number': 4,
        'document_date': '2024-05-02T14:30:00',
        'top_chunks': [
            {'page_number': 1, 'sentence_number': 4, 'cosine_similarity': 0.84},
        ],
        'first_page_preview': (
            'An das Landesgericht Wien. In der Rechtssache Mustermann ./. Muster GmbH '
            'erhebe ich namens und im Auftrag des Klägers Klage. Der Beklagte schuldet '
            'Zahlung aus einem Werkvertrag über die Lieferung und Montage von Anlagen.'
        ),
        'context_snippet': _demo_context_snippet(
            'Der Kläger ist Unternehmer im Sinne des UGB. Der Beklagte bestellte im Jänner 2024 '
            'die Lieferung und Installation einer Lüftungsanlage.',
            'Die Klage wird wegen Nichterfüllung der vertraglichen Leistungspflicht erhoben.',
            'Der Beklagte verweigert die Restzahlung unter Berufung auf angebliche Mängel. '
            'Diese Mängel sind nicht binnen der gesetzlichen Rügefrist angezeigt worden.',
        ),
        'file_size': 98304,
        'client_open_unc': r'\\fileserver\AutoDoc\corpus\2024-001\Schriftsatz_Klage.docx',
    },
    {
        'doc_id': 'corpus/2023-088/Gutachten_Baumfaellung.pdf',
        'path': 'corpus/2023-088/Gutachten_Baumfaellung.pdf',
        'title': 'Gutachten Baumfällung Nachbargrundstück',
        'akten_id': '2023-088',
        'type': 'Gutachten',
        'score': 0.77,
        'cosine_similarity': 0.77,
        'cosine_distance': 0.23,
        'page_number': 7,
        'sentence_number': 2,
        'document_date': '2023-11-20T09:15:00',
        'top_chunks': [
            {'page_number': 7, 'sentence_number': 2, 'cosine_similarity': 0.77},
        ],
        'first_page_preview': (
            'Sachverständigengutachten. Auftraggeber: Hausverwaltung Musterstraße 12. '
            'Gegenstand: Beurteilung der Verkehrssicherungspflicht hinsichtlich einer '
            'Grenzlinde auf dem Nachbargrundstück. Ort der Besichtigung: 5020 Salzburg.'
        ),
        'context_snippet': _demo_context_snippet(
            'Im Rahmen der Ortsbesichtigung wurde festgestellt, dass mehrere Äste über die '
            'Grundstücksgrenze ragen und bei Sturm Schaden verursachen könnten.',
            'Die Verkehrssicherungspflicht des Grundeigentümers erfordert regelmäßige Kontrolle und fachgerechten Rückschnitt.',
            'Eine sofortige Fällung wurde nicht als zwingend erachtet, wohl aber ein Rückschnitt '
            'innerhalb von sechs Wochen. Die Kosten sind nach den Regeln der Nachbarschaftsrechte zu tragen.',
        ),
        'ingested_summary': 'Sachverständigengutachten zur Verkehrssicherungspflicht bei einem Grenzbaum.',
        'file_size': 512000,
        'client_open_unc': r'\\fileserver\AutoDoc\corpus\2023-088\Gutachten_Baumfaellung.pdf',
    },
    {
        'doc_id': 'corpus/2024-010/Protokoll_Besprechung.docx',
        'path': 'corpus/2024-010/Protokoll_Besprechung.docx',
        'title': 'Besprechungsprotokoll Mandant',
        'akten_id': '2024-010',
        'type': 'Protokoll',
        'score': 0.72,
        'cosine_similarity': 0.72,
        'cosine_distance': 0.28,
        'page_number': 2,
        'sentence_number': 8,
        'document_date': '2024-06-01T16:45:00',
        'top_chunks': [
            {'page_number': 2, 'sentence_number': 8, 'cosine_similarity': 0.72},
        ],
        'first_page_preview': (
            'Besprechungsprotokoll vom 01.06.2024. Teilnehmer: RA Dr. Huber, Mandantin Frau Berger, '
            'Stellvertretung Kanzlei. Gegenstand: Vorbereitung der außergerichtlichen Einigung im '
            'Schadenersatzverfahren. Nächster Termin mit der Gegenseite wird für KW 24 angestrebt.'
        ),
        'context_snippet': _demo_context_snippet(
            'Die Mandantin berichtet über den aktuellen Gesundheitszustand und die fortbestehenden '
            'Einschränkungen im Alltag. Unterlagen der Krankenanstalt liegen der Kanzlei vor.',
            'Ein Vergleichsangebot in Höhe von EUR 18.500,00 wurde von der gegnerischen Versicherung unterbreitet.',
            'Die Mandantin wünscht eine Stellungnahme innerhalb von zwei Wochen. '
            'RA Dr. Huber wird eine Gegendarstellung und ein Gegenangebot vorbereiten.',
        ),
        'external_url': 'https://contoso.sharepoint.com/sites/legal/Shared%20Documents/Protokoll.docx',
        'file_size': 45056,
    },
]


def _search_use_test_results() -> bool:
    """Sample hits only when SEARCH_USE_TEST_RESULTS is explicitly enabled (local UI dev)."""
    flag = (os.getenv('SEARCH_USE_TEST_RESULTS') or '').strip().lower()
    return flag in ('1', 'true', 'yes', 'on')


def _test_result_haystack(row: Dict[str, Any]) -> str:
    parts = [
        row.get('title'),
        row.get('ingested_summary'),
        row.get('path'),
        row.get('doc_id'),
        row.get('akten_id'),
        row.get('type'),
    ]
    return ' '.join(str(p) for p in parts if p).lower()


def _build_test_search_results(query: str, limit: int) -> Dict[str, Any]:
    """Deterministic sample search payload for local development."""
    q = (query or '').strip().lower()
    terms = [t for t in re.split(r'\s+', q) if len(t) >= 2]
    rows: List[Dict[str, Any]] = []
    for fixture in _TEST_SEARCH_FIXTURES:
        hay = _test_result_haystack(fixture)
        if not q or not terms or all(term in hay for term in terms):
            rows.append({k: v for k, v in fixture.items() if k != 'client_open_unc'})
    if not rows and q:
        rows = [{k: v for k, v in fixture.items() if k != 'client_open_unc'}
                for fixture in _TEST_SEARCH_FIXTURES[:2]]
    rows = rows[: max(1, limit)]
    pointers = [str(r.get('doc_id') or '') for r in rows if r.get('doc_id')]
    return {
        'results': rows,
        'total': len(rows),
        'semantix': {
            'status': 'test_data',
            'message': 'Beispieltreffer für lokale Entwicklung (Knovas API nicht verwendet)',
            'result_count': len(rows),
            'pointers': pointers,
            'query_session_id': 'local-test-session-0001',
        },
    }


def _apply_test_open_hints(
    results: List[Dict[str, Any]],
    browser_client_open_enabled: bool,
    companion_enabled: bool,
    companion_uri_scheme: str,
) -> None:
    """Mark sample hits as openable for UI development without a real AutoDoc mount."""
    by_id = {str(f.get('doc_id') or ''): f for f in _TEST_SEARCH_FIXTURES}
    for result in results:
        if result.get('external_url'):
            result['file_exists'] = True
            result['can_open'] = True
            result['open_mode'] = 'external'
            continue
        fixture = by_id.get(str(result.get('doc_id') or ''))
        result['can_open'] = True
        result['file_exists'] = None
        unc = fixture.get('client_open_unc') if fixture else None
        if browser_client_open_enabled and unc:
            result['open_via_browser'] = True
            result['client_open_unc'] = unc
        if companion_enabled and unc:
            result['open_via_companion'] = True
            result['companion_scheme'] = companion_uri_scheme


# Bump when search UI / open behaviour changes — visible in footer and GET /api/version
DOCBRIDGE_BUILD_ID = 'onedrive-locations-v4'

# Default OneDrive enrichment JSONL location (OneDrive mirror / RC sync on AutoDoc mount)
_DEFAULT_ENRICHMENT_PATH = "/mnt/autodoc/.search_enrichment.jsonl"
_DEFAULT_CONTEXT_STORE_PATH = "/mnt/autodoc/.search_context"

# Generic client-facing error message. Internal exception detail is logged
# server-side (exc_info=True) and never returned to the browser (avoids leaking
# filesystem paths / stack context to unauthenticated or malicious callers).
_GENERIC_ERROR_MESSAGE = 'Interner Serverfehler'


def _confine_to_autodoc(autodoc_path: str, file_path: str) -> Optional[str]:
    """
    Resolve a Knovas pointer to an absolute path guaranteed to live inside the
    AutoDoc root, or return None.

    Two-stage confinement:
      1. Lexical check on abspath -> rejects ``..`` traversal and absolute paths.
      2. realpath check -> rejects escapes via symlinks (abspath does NOT resolve
         symlinks, so a symlink inside the root pointing outside would otherwise
         pass step 1 and be handed to send_file, which follows symlinks).

    Returns the abspath (NOT the realpath) so downstream UNC / client-local
    mapping behaviour for legitimate files is unchanged.
    """
    rel = _rel_path_for_autodoc(file_path)
    if os.path.isabs(rel):
        return None
    base_abs = os.path.abspath(autodoc_path)
    candidate_abs = os.path.abspath(os.path.join(base_abs, rel))
    try:
        if os.path.commonpath([base_abs, candidate_abs]) != base_abs:
            return None
    except ValueError:
        return None
    base_real = os.path.realpath(base_abs)
    candidate_real = os.path.realpath(candidate_abs)
    try:
        if os.path.commonpath([base_real, candidate_real]) != base_real:
            return None
    except ValueError:
        return None
    return candidate_abs


def _open_autodoc_fileobj(full_path: str):
    """
    Open a confined file for send_file, refusing to follow a symlink on the final
    path segment (closes the check-then-open TOCTOU window). O_NOFOLLOW is a no-op
    where unavailable (e.g. Windows); O_BINARY is a no-op on POSIX.
    """
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    fd = os.open(full_path, flags)
    return os.fdopen(fd, 'rb')


def _static_asset_version() -> str:
    """Cache-bust static JS/CSS after deploy (neueste Aenderung aller Assets).

    Frueher zaehlte nur app.js. Aenderungen an ontology.js oder ontology.css
    liessen die Versionsnummer damit unberuehrt, und Browser lieferten
    tagelang die alte Datei aus dem Zwischenspeicher aus.
    """
    neueste = 0
    try:
        static_root = os.path.join(os.path.dirname(__file__), 'static')
        for ordner in ('js', 'css'):
            pfad = os.path.join(static_root, ordner)
            if not os.path.isdir(pfad):
                continue
            for name in os.listdir(pfad):
                if not name.endswith(('.js', '.css')):
                    continue
                datei = os.path.join(pfad, name)
                if os.path.isfile(datei):
                    neueste = max(neueste, int(os.path.getmtime(datei)))
    except OSError:
        pass
    return str(neueste) if neueste else '1'


def create_app(config_path: Optional[str] = None):
    """
    Create and configure Flask application.
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        Configured Flask app
    """
    app = Flask(__name__)
    
    if config_path:
        from config_loader import set_config
        config = set_config(config_path)
    else:
        config = get_config()

    _configure_logging_for_wsgi(config)
    
    web_secret_key = str(config.get('web.secret_key', '') or '')
    app.config['SECRET_KEY'] = web_secret_key or 'change-me-in-production'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    # Secure by default (session cookie only over HTTPS). Must be paired with TLS
    # termination in front of the app. Override to False (web.session_cookie_secure)
    # only for plain-HTTP LAN development.
    app.config['SESSION_COOKIE_SECURE'] = config.get_bool('web.session_cookie_secure', True)
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(
        seconds=config.get_int('web.session_lifetime', 3600)
    )

    # Same-origin UI: no CORS by default. Only enable (scoped) when explicit
    # origins are configured; never emit a wildcard Access-Control-Allow-Origin.
    cors_origins = [
        str(o).strip()
        for o in (config.get_list('web.cors_origins', []) or [])
        if str(o).strip() and str(o).strip() != '*'
    ]
    if cors_origins:
        CORS(app, origins=cors_origins)

    @app.after_request
    def _prevent_stale_ui_assets(response):
        """Avoid browsers serving cached HTML/JS after docker rebuild."""
        path = request.path or ''
        if (
            path in ('/', '/login', '/ontology')
            or path.endswith('.js')
            or path.endswith('.css')
            or path.startswith('/static/')
        ):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
        return response

    login_enabled = config.get_bool('web.login.enabled', True)
    web_app_title = str(config.get('web.app_title', 'Knovas Document Search') or 'Knovas Document Search')
    # Kurzform der Marke fuer die Titelzeile. Die Titel liefen auseinander
    # ("Login - Knovas Document Search" gegen "Cortex . Knovas Document
    # Search"); einheitlich ist "Knovas Cortex", "Knovas Suche" und so fort.
    web_brand = (str(config.get('web.brand', '') or '').strip()
                 or (web_app_title.split() or ['Knovas'])[0])
    login_company_name = config.get('web.login.company_name', 'Knovas')
    login_username = str(config.get('web.login.username', '') or '')
    login_password = str(config.get('web.login.password', '') or '')
    login_configured = bool(login_username and login_password)

    # Per-user accounts (Pflichtenheft B1). When on, the shared company
    # credential is not merely unused — the app refuses to start with one
    # configured, so an upgrade cannot leave both doors open.
    identity_enabled = config.get_bool('identity.enabled', False)
    identity_gate = None
    principal_broker = None
    if identity_enabled:
        from identity.broker_key import load_or_create_signer
        from identity.webauth import IdentityGate

        if login_configured:
            raise RuntimeError(
                'identity.enabled is true, but COMPANY_LOGIN_NAME/COMPANY_LOGIN_PASSWORD '
                'are still set. The shared company login is superseded by per-user '
                'accounts; remove both values before enabling identity.'
            )
        identity_gate = IdentityGate()
        app.teardown_request(identity_gate.close)

        key_dir = config.get("identity.broker_key_dir")
        if not key_dir:
            raise RuntimeError(
                "identity.enabled is true, but identity.broker_key_dir is not set. "
                "The Platform must have a dedicated directory for the broker signing key."
            )
        tenant_id = config.get("api.client_id")
        if not tenant_id:
            raise RuntimeError(
                "identity.enabled is true, but api.client_id is not set. "
                "Principal assertions must name the Knovas tenant."
            )
        signer = load_or_create_signer(Path(key_dir))
        principal_broker = _RequestScopedBroker(identity_gate, signer, tenant_id)
        api_client = KnovasAPIClient(config, principal_broker=principal_broker)
    else:
        api_client = KnovasAPIClient(config)
    file_handler = AutoDocFileHandler()
    weak_secret_values = {
        '',
        'change-me',
        'change-me-in-production',
        'replace-with-random-hex',
    }
    weak_password_values = {
        '',
        'change-me',
        'change-me-company-password',
        'replace-with-strong-company-password',
    }

    # Per-IP brute-force throttling for the shared company login (in-process; the
    # shared login is a single credential so N failures -> temporary IP lockout).
    login_max_failed = max(1, config.get_int('web.login.max_failed_attempts', 5))
    login_lockout_seconds = max(1, config.get_int('web.login.lockout_seconds', 300))
    login_failed_window = max(1, config.get_int('web.login.failed_window_seconds', 300))
    login_attempts: Dict[str, Dict[str, float]] = {}

    if login_enabled and not identity_enabled and not login_configured:
        logger.warning(
            "Company login is enabled but COMPANY_LOGIN_NAME or COMPANY_LOGIN_PASSWORD is missing."
        )
    # Unconditional: SECRET_KEY signs both the session cookie AND the single-use
    # open tokens, so a weak/default secret is exploitable even with login disabled.
    if web_secret_key in weak_secret_values:
        raise RuntimeError('WEB_SECRET_KEY must be set to a strong random value.')
    # Only meaningful for the legacy shared credential. With per-user accounts
    # there is no COMPANY_LOGIN_PASSWORD to be weak — the app refuses to start
    # if one is set at all (see the identity block above).
    if login_enabled and not identity_enabled and login_password in weak_password_values:
        raise RuntimeError('COMPANY_LOGIN_PASSWORD must be changed before login can be enabled.')

    open_section = config.get_dict('open', {}) or {}
    browser_client_open_enabled = config.get_bool('open.browser_client_path', True)
    companion_enabled = config.get_bool('open.companion_enabled', False)
    open_token_ttl = max(30, config.get_int('open.token_ttl_seconds', 120))
    open_token_store_path = str(open_section.get('token_store_path') or '').strip()
    if not open_token_store_path:
        # Shared cross-worker store under the app data dir (default 2 gunicorn
        # workers each get their own process, so an in-process cache is not enough).
        data_dir = os.path.dirname(
            str(config.get('sync.last_sync_file', '/app/data/last_sync.txt')) or ''
        ) or '/app/data'
        open_token_store_path = os.path.join(data_dir, 'open_tokens.sqlite3')
    open_token_manager = OpenTokenManager(
        str(app.config['SECRET_KEY']),
        max_age_seconds=open_token_ttl,
        store_path=open_token_store_path,
    )
    pdf_inline_in_browser = config.get_bool('open.pdf_inline_in_browser', True)
    allow_server_side_startfile = config.get_bool('open.allow_server_side_startfile', False)
    allow_degraded_download_open = config.get_bool('open.allow_degraded_download_open', False)
    companion_uri_scheme = str(open_section.get('companion_uri_scheme') or 'semantix-doc').strip()
    public_base_url_config = str(open_section.get('public_base_url') or '').strip().rstrip('/')
    if not public_base_url_config:
        public_base_url_config = (os.getenv('KNOVAS_PLATFORM_URL') or '').strip().rstrip('/')

    client_local_root = normalize_client_local_root(str(open_section.get('client_local_root') or ''))

    def _open_unc_root_pairs() -> List[Tuple[str, str]]:
        roots = parse_unc_roots_list(open_section.get('unc_roots'))
        if not roots:
            loc = str(open_section.get('local_root') or '').strip()
            unc = normalize_unc_root(str(open_section.get('unc_root') or ''))
            if loc and unc:
                roots = [(loc, unc)]
        if not roots:
            unc_only = normalize_unc_root(str(open_section.get('unc_root') or ''))
            if unc_only:
                roots = [(os.path.abspath(file_handler.autodoc_path), unc_only)]
        return roots

    def _open_server_local_roots() -> List[str]:
        """Server/container paths used as the left side of UNC or client-local mapping."""
        seen: set[str] = set()
        out: List[str] = []
        for loc, _unc in _open_unc_root_pairs():
            norm = normalize_local_root(loc)
            if norm and norm not in seen:
                seen.add(norm)
                out.append(norm)
        explicit = normalize_local_root(str(open_section.get('local_root') or ''))
        if explicit and explicit not in seen:
            out.append(explicit)
        autodoc = normalize_local_root(str(file_handler.autodoc_path))
        if autodoc and autodoc not in seen:
            out.append(autodoc)
        return out

    def _open_mapping_configured() -> bool:
        if _open_unc_root_pairs():
            return True
        return bool(client_local_root and _open_server_local_roots())

    def _unc_for_resolved_path(full_path: str) -> Optional[str]:
        roots = _open_unc_root_pairs()
        if not roots:
            return None
        return map_path_with_roots(full_path, roots)

    def _client_path_for_resolved_path(full_path: str) -> Optional[str]:
        if not client_local_root:
            return None
        for loc in _open_server_local_roots():
            p = filesystem_path_to_client_local(full_path, loc, client_local_root)
            if p:
                return p
        return None

    def _can_open_via_companion(full_path: str) -> bool:
        return bool(_unc_for_resolved_path(full_path) or _client_path_for_resolved_path(full_path))

    def _client_open_targets(full_path: str) -> Dict[str, str]:
        """Client-visible paths the user's OS can open (UNC on Windows, mount path on Linux)."""
        out: Dict[str, str] = {}
        unc = _unc_for_resolved_path(full_path)
        if unc:
            out['unc'] = unc
        client_path = _client_path_for_resolved_path(full_path)
        if client_path:
            out['path'] = client_path
        return out

    def _is_safe_next(target: Optional[str]) -> bool:
        """Allow only local redirects after login."""
        return bool(
            target
            and target.startswith('/')
            and not target.startswith('//')
            and not target.startswith('/\\')
            and '\\' not in target
        )

    def _ensure_csrf_token() -> str:
        token = session.get('csrf_token')
        if not token:
            token = secrets.token_urlsafe(32)
            session['csrf_token'] = token
        return token

    def _csrf_token_is_valid(submitted_token: str) -> bool:
        token = session.get('csrf_token')
        if not token or not submitted_token:
            return False
        return hmac.compare_digest(str(token).encode('utf-8'), submitted_token.encode('utf-8'))

    def _login_redirect():
        next_url = request.full_path if request.query_string else request.path
        return redirect(url_for('login', next=next_url))

    def _client_ip() -> str:
        return request.remote_addr or 'unknown'

    def _login_is_locked(ip: str) -> bool:
        rec = login_attempts.get(ip)
        return bool(rec and time.time() < rec.get('locked_until', 0.0))

    def _record_login_failure(ip: str) -> None:
        now = time.time()
        rec = login_attempts.get(ip)
        if not rec or (now - rec.get('window_start', now)) > login_failed_window:
            rec = {'fails': 0.0, 'window_start': now, 'locked_until': 0.0}
        rec['fails'] += 1
        if rec['fails'] >= login_max_failed:
            rec['locked_until'] = now + login_lockout_seconds
        login_attempts[ip] = rec

    def _reset_login_failures(ip: str) -> None:
        login_attempts.pop(ip, None)

    def _credentials_match(submitted_name: str, submitted_password: str) -> bool:
        expected_name = login_username.encode('utf-8')
        expected_password = login_password.encode('utf-8')
        actual_name = submitted_name.encode('utf-8')
        actual_password = submitted_password.encode('utf-8')
        name_ok = hmac.compare_digest(actual_name, expected_name)
        password_ok = hmac.compare_digest(actual_password, expected_password)
        return name_ok and password_ok

    def _resolve_autodoc_path(file_path: str) -> Optional[str]:
        return _confine_to_autodoc(file_handler.autodoc_path, file_path)

    @app.before_request
    def require_company_login():
        """Require a login before serving the search UI and APIs.

        With ``identity.enabled`` the gate is per-user and lives in
        ``identity.webauth``; otherwise the legacy shared-credential check
        below still applies, so an existing deployment keeps working until it
        migrates.
        """
        if identity_gate is not None:
            return identity_gate.guard()
        if not login_enabled:
            return None
        if request.endpoint in {
            'static',
            'favicon',
            'login',
            'logout',
            'stats',
            'api_version',
            'health',
            'open_token_redeem',
            'open_tokens_spec',
        }:
            return None
        if session.get('company_login_ok') is True:
            return None
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': 'Login erforderlich'}), 401
        return _login_redirect()

    # Endpoints exempt from the uniform X-CSRF-Token gate below:
    #   login / logout   -> form-based csrf_token, validated in the handler
    #   open_token_mint  -> in-handler X-CSRF-Token check (returns 400), kept as-is
    #   open_token_redeem-> companion Bearer token, no browser session/CSRF
    #   static           -> asset serving
    _CSRF_EXEMPT_ENDPOINTS = frozenset({
        'static',
        'login',
        'logout',
        # Browser form post, not XHR: it carries a hidden csrf_token field and
        # validates it in the handler, exactly as login/logout do. Leaving it
        # in the header gate would reject the form before it was read.
        'account_password',
        'open_token_mint',
        'open_token_redeem',
    })

    @app.before_request
    def require_csrf_for_state_changing_requests():
        """
        Uniform CSRF gate: every state-changing (non-safe method) request must carry a
        valid X-CSRF-Token header matching the session token. Registered after the login
        gate so unauthenticated callers still get 401 first. Safe methods (GET/HEAD/
        OPTIONS), unknown routes, and the exempt endpoints above are not gated — the
        exempt endpoints perform their own token validation (login/logout/mint) or use a
        separate auth scheme (redeem).
        """
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return None
        if request.endpoint is None or request.endpoint in _CSRF_EXEMPT_ENDPOINTS:
            return None
        # The admin console is server-rendered HTML forms, not XHR: each POST
        # carries a hidden csrf_token field and every handler checks it before
        # doing anything. This gate expects a header and would reject them all.
        if request.endpoint.startswith('admin.'):
            return None
        csrf_header = str(request.headers.get('X-CSRF-Token', '') or '')
        if not _csrf_token_is_valid(csrf_header):
            return jsonify({'success': False, 'error': 'CSRF token invalid or missing'}), 403
        return None

    @app.before_request
    def reject_client_asserted_groups():
        """Refuse browser JSON that tries to choose its own access groups."""
        if not request.is_json:
            return None
        try:
            PrincipalBroker.reject_client_assertion(request.get_json(silent=True))
        except ClientAssertedGroupsError as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400
        return None

    @app.route('/favicon.ico')
    def favicon():
        """Browser fragen /favicon.ico an der Wurzel an, unabhaengig vom <link>-Tag.

        Ohne diese Route laeuft jeder Seitenaufruf in ein 404 im Log.
        """
        return redirect(url_for('static', filename='img/favicon.svg'), code=301)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        """Company login page."""
        if not login_enabled:
            return redirect(url_for('index'))

        next_url = request.args.get('next') or url_for('index')
        if not _is_safe_next(next_url):
            next_url = url_for('index')

        if identity_gate is not None:
            return _identity_login(next_url)

        if session.get('company_login_ok') is True:
            return redirect(next_url)

        error = None
        status_code = 200
        csrf_token = _ensure_csrf_token()
        if request.method == 'POST':
            submitted_name = str(request.form.get('login_name', '') or '')
            submitted_password = str(request.form.get('password', '') or '')
            submitted_csrf = str(request.form.get('csrf_token', '') or '')
            next_url = request.form.get('next') or next_url
            if not _is_safe_next(next_url):
                next_url = url_for('index')

            client_ip = _client_ip()
            if _login_is_locked(client_ip):
                # Reject (do not even check credentials) while the IP is locked out.
                logger.warning('Login locked out for %s (too many failed attempts)', client_ip)
                error = 'Zu viele fehlgeschlagene Anmeldeversuche. Bitte später erneut versuchen.'
                status_code = 429
            elif not _csrf_token_is_valid(submitted_csrf):
                error = 'Login-Formular ist abgelaufen. Bitte erneut versuchen.'
                csrf_token = _ensure_csrf_token()
            elif not login_configured:
                error = 'Login ist noch nicht konfiguriert. Bitte .env prüfen.'
            elif _credentials_match(submitted_name, submitted_password):
                _reset_login_failures(client_ip)
                session.clear()
                session.permanent = True
                session['company_login_ok'] = True
                session['company_login_name'] = submitted_name
                session['csrf_token'] = secrets.token_urlsafe(32)
                return redirect(next_url)
            else:
                _record_login_failure(client_ip)
                error = 'Login-Name oder Passwort ist falsch.'

        return render_template(
            'login.html',
            app_title=web_app_title,
            brand=web_brand,
            company_name=login_company_name,
            error=error,
            next_url=next_url,
            csrf_token=csrf_token,
        ), status_code

    def _identity_login(next_url: str):
        """Per-user email + password sign-in.

        One message for every refusal — unknown address, wrong password,
        disabled, locked. Distinguishing them would turn the form into a way to
        discover who works at the firm.
        """
        if identity_gate.current_user() is not None:
            return redirect(next_url)

        error = None
        status_code = 200
        csrf_token = _ensure_csrf_token()
        if request.method == 'POST':
            email = str(request.form.get('login_name', '') or '').strip()
            password = str(request.form.get('password', '') or '')
            submitted_csrf = str(request.form.get('csrf_token', '') or '')
            next_url = request.form.get('next') or next_url
            if not _is_safe_next(next_url):
                next_url = url_for('index')

            client_ip = _client_ip()
            if _login_is_locked(client_ip):
                logger.warning('Login locked out for %s (too many failed attempts)', client_ip)
                error = 'Zu viele fehlgeschlagene Anmeldeversuche. Bitte später erneut versuchen.'
                status_code = 429
            elif not _csrf_token_is_valid(submitted_csrf):
                error = 'Login-Formular ist abgelaufen. Bitte erneut versuchen.'
                csrf_token = _ensure_csrf_token()
            else:
                user = identity_gate.users().authenticate(email, password)
                if user is not None:
                    _reset_login_failures(client_ip)
                    identity_gate.sign_in(user)
                    session['csrf_token'] = secrets.token_urlsafe(32)
                    return redirect(next_url)
                _record_login_failure(client_ip)
                error = 'E-Mail-Adresse oder Passwort ist falsch.'

        return render_template(
            'login.html',
            app_title=web_app_title,
            brand=web_brand,
            company_name=login_company_name,
            error=error,
            next_url=next_url,
            csrf_token=csrf_token,
            identity_enabled=True,
        ), status_code

    @app.route('/logout', methods=['POST'])
    def logout():
        """End the session."""
        submitted_csrf = str(request.form.get('csrf_token', '') or '')
        if not _csrf_token_is_valid(submitted_csrf):
            return jsonify({'success': False, 'error': 'CSRF token ungültig'}), 400
        if identity_gate is not None:
            identity_gate.sign_out()
        else:
            session.clear()
        return redirect(url_for('login'))

    @app.route('/account/password', methods=['GET', 'POST'])
    def account_password():
        """Change your own password. Also the gate a forced rotation lands on."""
        if identity_gate is None:
            return redirect(url_for('settings_page'))
        user = identity_gate.current_user()
        if user is None:
            return redirect(url_for('login'))

        error = None
        done = False
        if request.method == 'POST':
            if not _csrf_token_is_valid(str(request.form.get('csrf_token', '') or '')):
                error = 'Formular ist abgelaufen. Bitte erneut versuchen.'
            else:
                current = str(request.form.get('current_password', '') or '')
                new_password = str(request.form.get('new_password', '') or '')
                repo = identity_gate.users()
                if repo.authenticate(user.email, current) is None:
                    error = 'Das aktuelle Passwort ist falsch.'
                else:
                    from identity.passwords import WeakPasswordError
                    try:
                        repo.set_password(user.id, new_password)
                    except WeakPasswordError as exc:
                        error = '; '.join(exc.reasons)
                    else:
                        done = True
                        g.identity_session = None

        return render_template(
            'account_password.html',
            app_title=web_app_title,
            brand=web_brand,
            user=user,
            must_change=user.must_change_password and not done,
            error=error,
            done=done,
            csrf_token=_ensure_csrf_token(),
        ), (200 if not error else 400)

    # Feedback-Ziel: bisher im Fuss der Suchseite, jetzt eigener
    # Navigationspunkt. Ueber die Umgebung abschaltbar (leer = kein Punkt).
    feedback_url = os.getenv('FEEDBACK_URL', 'https://knovas.atlassian.net/jira/software/form/b05bdd7b-936a-4d3a-b92b-15b89773e6cf?atlOrigin=eyJpIjoiNGJlM2Y4YTMzNTE5NDFmZjg5M2RhMDQ5ZGRhNzM3NTQiLCJwIjoiaiJ9')

    def _sidebar_context() -> Dict[str, Any]:
        """Gemeinsame Werte der Plattform-Leiste."""
        return {
            'company_name': login_company_name,
            'feedback_url': feedback_url,
        }

    @app.route('/')
    def index():
        """Main search page."""
        _load_search_enrichment(config)
        return render_template(
            'index.html',
            active_nav='suche',
            **_sidebar_context(),
            app_title=web_app_title,
            brand=web_brand,
            csrf_token=_ensure_csrf_token(),
            companion_enabled=companion_enabled,
            browser_client_open_enabled=browser_client_open_enabled,
            allow_degraded_download_open=allow_degraded_download_open,
            pdf_inline_in_browser=pdf_inline_in_browser,
            onedrive_enrichment_loaded=bool(_unique_enrichment_records()),
            results_per_page=config.get_int('web.search.results_per_page', 20),
            asset_version=_static_asset_version(),
            build_id=DOCBRIDGE_BUILD_ID,
        )

    @app.route('/ontology')
    def ontology_page():
        """Cortex: Ontologie-Explorer (Typ-Graph -> Entitaeten -> Belege -> PDF)."""
        return render_template(
            'ontology.html',
            active_nav='cortex',
            **_sidebar_context(),
            app_title=web_app_title,
            brand=web_brand,
            csrf_token=_ensure_csrf_token(),
            asset_version=_static_asset_version(),
        )

    @app.route('/settings')
    def settings_page():
        """Konto und System. Zeigt nur echte Werte, keine Attrappen."""
        if identity_gate is not None:
            signed_in_as = identity_gate.current_user()
            display_name = f'{signed_in_as.display_name} ({signed_in_as.email})'
        else:
            display_name = config.get('web.login.username', '') or ''
        return render_template(
            'settings.html',
            active_nav='einstellungen',
            **_sidebar_context(),
            app_title=web_app_title,
            brand=web_brand,
            identity_enabled=identity_gate is not None,
            login_name=display_name,
            csrf_token=_ensure_csrf_token(),
            asset_version=_static_asset_version(),
            build_id=DOCBRIDGE_BUILD_ID,
        )

    if identity_gate is not None:
        from web_interface.admin import create_admin_blueprint

        app.register_blueprint(create_admin_blueprint(
            identity_gate,
            csrf_valid=_csrf_token_is_valid,
            csrf_token=_ensure_csrf_token,
            page_context=lambda: {
                **_sidebar_context(),
                'app_title': web_app_title,
                'brand': web_brand,
                'asset_version': _static_asset_version(),
            },
        ))

    @app.route('/api/search', methods=['POST'])
    def search():
        """
        Search documents via Knovas API.
        
        Request JSON:
            {
                "query": "search query",
                "limit": 20,
                "filters": {}
            }
        
        Returns:
            JSON with search results
        """
        try:
            data = request.get_json()
            
            if not data or 'query' not in data:
                return jsonify({'error': 'Query parameter required'}), 400
            
            query = data['query']
            limit = data.get('limit', config.get_int('web.search.results_per_page', 20))
            filters = data.get('filters', {})
            
            logger.info(f"Search request: query='{query}', limit={limit}")

            min_qlen = config.get_int('web.search.min_query_length', 2)
            qstrip = (query or '').strip()
            if min_qlen > 1 and len(qstrip) < min_qlen:
                return jsonify({
                    'success': False,
                    'error': f'Suchbegriff muss mindestens {min_qlen} Zeichen haben.',
                }), 400

            use_test_results = _search_use_test_results()
            if use_test_results:
                logger.info("Search using local test fixtures (SEARCH_USE_TEST_RESULTS=true)")
                results = _build_test_search_results(query=query, limit=limit)
            else:
                results = api_client.search_documents(query=query, limit=limit, filters=filters)

            is_test_data = use_test_results or (
                isinstance(results.get('semantix'), dict)
                and results['semantix'].get('status') == 'test_data'
            )

            enhanced_results = _enhance_search_results(results, file_handler, config)
            enrichment_loaded = bool(_unique_enrichment_records())
            for result in enhanced_results.get('results') or []:
                fp = (result.get('path') or '').strip()
                if (
                    fp
                    and result.get('can_open')
                    and not result.get('external_url')
                    and not enrichment_loaded
                ):
                    full = _resolve_autodoc_path(fp)
                    if full:
                        targets = _client_open_targets(full)
                        if browser_client_open_enabled and _open_mapping_configured():
                            result['open_via_browser'] = True
                            if targets.get('unc'):
                                result['client_open_unc'] = targets['unc']
                            if targets.get('path'):
                                result['client_open_path'] = targets['path']
                        if companion_enabled and _open_mapping_configured():
                            result['open_via_companion'] = True
                            result['companion_scheme'] = companion_uri_scheme
            if is_test_data:
                _apply_test_open_hints(
                    enhanced_results.get('results') or [],
                    browser_client_open_enabled,
                    companion_enabled,
                    companion_uri_scheme,
                )
            refined = _apply_search_refinement(enhanced_results, query, filters, config)

            final_results = refined.get('results', [])
            final_results = _supplement_results_from_enrichment_filenames(
                query, final_results, filters, config, limit=limit
            )
            if config.get_bool('web.search.log_similarity_scores', True):
                _log_search_similarity_debug(query, final_results)

            if enrichment_loaded:
                for result in final_results:
                    result['onedrive_open_available'] = True
                    if not result.get('external_url'):
                        url = _resolve_onedrive_url(
                            str(result.get('doc_id') or ''),
                            str(result.get('path') or result.get('doc_id') or ''),
                            config,
                        )
                        if url:
                            _apply_external_open_mode(result, url)

            payload: Dict[str, Any] = {
                'success': True,
                'query': query,
                'results': final_results,
                'total': len(final_results),
                'timestamp': datetime.now().isoformat(),
                'onedrive_enrichment_loaded': enrichment_loaded,
                'location_summary': _build_location_summary(final_results),
            }
            if 'semantix' in refined and isinstance(refined.get('semantix'), dict):
                payload['semantix'] = refined['semantix']
            if config.get_bool('web.search.expose_similarity_scores_in_json', False):
                payload['similarity_debug'] = _build_similarity_debug(final_results)

            return jsonify(payload)
            
        except Exception as e:
            logger.error(f"Search error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': _GENERIC_ERROR_MESSAGE
            }), 500
    
    @app.route('/api/document/<path:doc_id>', methods=['GET'])
    def get_document(doc_id: str):
        """
        Get document metadata.
        
        Args:
            doc_id: Document ID
            
        Returns:
            JSON with document metadata
        """
        try:
            logger.info(f"Document request: doc_id={doc_id}")
            
            return jsonify({
                'success': True,
                'doc_id': doc_id,
                'message': 'Document metadata endpoint (to be implemented)'
            })
            
        except Exception as e:
            logger.error(f"Error retrieving document: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': _GENERIC_ERROR_MESSAGE
            }), 500
    
    @app.route('/api/document/<path:doc_id>/open', methods=['POST'])
    def open_document(doc_id: str):
        """
        Legacy: open on server host (os.startfile). Disabled when companion open is mandatory.

        Prefer POST /api/open-tokens/mint + Semantix Open Companion (UNC, no temp copy).
        """
        try:
            data = request.get_json() or {}
            file_path = data.get('path')

            if not file_path:
                return jsonify({
                    'success': False,
                    'error': 'Document path required'
                }), 400

            full_path = _resolve_autodoc_path(file_path)
            if not full_path:
                return jsonify({
                    'success': False,
                    'error': 'Document path not allowed'
                }), 400

            if not allow_server_side_startfile:
                hint = (
                    'Server-seitiges Öffnen ist deaktiviert. Dateien werden auf dem Client-PC geöffnet.'
                )
                if browser_client_open_enabled:
                    hint += ' Bitte „Öffnen“ in der Suchoberfläche verwenden (Browser-Client-Pfad).'
                elif companion_enabled:
                    hint += ' Bitte Companion-Open (POST /api/open-tokens/mint).'
                return jsonify({
                    'success': False,
                    'error': hint,
                    'use_browser_client_path': browser_client_open_enabled,
                    'use_companion': companion_enabled,
                }), 410

            if not os.path.exists(full_path):
                return jsonify({
                    'success': False,
                    'error': 'Document file not found'
                }), 404

            logger.info(f"Opening document (server-side): {full_path}")

            success = _open_file_external(full_path, config)

            if success:
                return jsonify({
                    'success': True,
                    'message': f'Document opened: {doc_id}'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Failed to open document'
                }), 500

        except Exception as e:
            logger.error(f"Error opening document: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': _GENERIC_ERROR_MESSAGE
            }), 500
    
    @app.route('/api/document/<path:doc_id>/download', methods=['GET'])
    def download_document(doc_id: str):
        """
        Download document file.
        
        Args:
            doc_id: Document ID
            
        Returns:
            File download response
        """
        try:
            file_path = request.args.get('path')
            
            if not file_path:
                return jsonify({'error': 'Document path required'}), 400
            
            full_path = _resolve_autodoc_path(file_path)
            if not full_path:
                return jsonify({'error': 'Document path not allowed'}), 400
            
            if not os.path.exists(full_path):
                return jsonify({'error': 'Document file not found'}), 404

            logger.info(f"Downloading document: {full_path}")

            try:
                file_obj = _open_autodoc_fileobj(full_path)
            except OSError:
                return jsonify({'error': 'Document file not found'}), 404
            return send_file(
                file_obj,
                as_attachment=True,
                download_name=os.path.basename(full_path)
            )

        except Exception as e:
            logger.error(f"Error downloading document: {e}", exc_info=True)
            return jsonify({'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/document/<path:doc_id>/preview', methods=['GET'])
    def preview_document(doc_id: str):
        """Inline PDF preview in browser (Option A — no persisted duplicate on disk)."""
        if not pdf_inline_in_browser:
            return jsonify({'error': 'PDF inline preview disabled'}), 404
        try:
            file_path = request.args.get('path')
            if not file_path:
                return jsonify({'error': 'Document path required'}), 400
            full_path = _resolve_autodoc_path(file_path)
            if not full_path:
                return jsonify({'error': 'Document path not allowed'}), 400
            if not os.path.exists(full_path):
                return jsonify({'error': 'Document file not found'}), 404
            if not str(full_path).lower().endswith('.pdf'):
                return jsonify({'error': 'Preview only supported for PDF'}), 415
            try:
                file_obj = _open_autodoc_fileobj(full_path)
            except OSError:
                return jsonify({'error': 'Document file not found'}), 404
            return send_file(
                file_obj,
                mimetype='application/pdf',
                as_attachment=False,
                download_name=os.path.basename(full_path),
            )
        except Exception as e:
            logger.error(f"Error previewing document: {e}", exc_info=True)
            return jsonify({'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/document/<path:doc_id>/thumbnail', methods=['GET'])
    def document_thumbnail(doc_id: str):
        """Seite 1 als PNG fuer die Trefferkarte. Nur PDF -- siehe preview.py."""
        file_path = str(request.args.get('path') or '').strip()
        if not file_path:
            return jsonify({'success': False, 'error': 'Document path required'}), 400

        full_path = _resolve_autodoc_path(file_path)
        if not full_path:
            return jsonify({'success': False, 'error': 'Document path not allowed'}), 400

        if preview_kind(file_path) != 'pdf':
            return jsonify({'success': False, 'error': 'Thumbnail only supported for PDF'}), 415

        if not os.path.exists(full_path):
            return jsonify({'success': False, 'error': 'Document file not found'}), 404

        try:
            png = render_first_page_png(full_path)
        except PreviewUnsupported:
            return jsonify({'success': False, 'error': 'Thumbnail only supported for PDF'}), 415
        except PreviewFailed as exc:
            logger.warning("Thumbnail failed for %s: %s", file_path, exc)
            return jsonify({'success': False, 'error': 'Vorschaubild konnte nicht erzeugt werden'}), 422
        except Exception:
            logger.error("Thumbnail error for %s", file_path, exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

        response = send_file(io.BytesIO(png), mimetype='image/png')
        # Das Bild aendert sich nur, wenn die Datei sich aendert; ohne diesen
        # Header laedt jede Suche jedes Vorschaubild neu.
        try:
            stat = os.stat(full_path)
            response.set_etag(f"{stat.st_mtime_ns}-{stat.st_size}")
        except OSError:
            pass
        response.headers['Cache-Control'] = 'private, max-age=300'
        return response.make_conditional(request)

    @app.route('/api/document/<path:doc_id>/preview-content', methods=['GET'])
    def preview_content(doc_id: str):
        """Sanitisiertes Markdown fuer DOCX, TXT und MSG.

        PDF laeuft bewusst nicht hierueber: der Client bettet /preview ein und
        laesst den Browser rendern. Antwort enthaelt niemals HTML -- der Client
        escaped zuerst und formatiert danach (siehe static/js/markdown.js).
        """
        file_path = str(request.args.get('path') or '').strip()
        if not file_path:
            return jsonify({'success': False, 'error': 'Document path required'}), 400

        full_path = _resolve_autodoc_path(file_path)
        if not full_path:
            return jsonify({'success': False, 'error': 'Document path not allowed'}), 400

        kind = preview_kind(file_path)
        if kind is None or kind == 'pdf':
            return jsonify({'success': False, 'error': 'Preview not supported for this format'}), 415

        if not os.path.exists(full_path):
            return jsonify({'success': False, 'error': 'Document file not found'}), 404

        try:
            extracted = extract_markdown(full_path)
        except PreviewUnsupported:
            return jsonify({'success': False, 'error': 'Preview not supported for this format'}), 415
        except PreviewFailed as exc:
            logger.warning("Preview extraction failed for %s: %s", file_path, exc)
            return jsonify({'success': False, 'error': 'Vorschau konnte nicht erzeugt werden'}), 422
        except Exception:
            logger.error("Preview error for %s", file_path, exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

        return jsonify({
            'success': True,
            'doc_id': doc_id,
            'kind': extracted['kind'],
            'markdown': extracted['markdown'],
            'meta': extracted['meta'],
            'warnings': extracted['warnings'],
        })

    @app.route('/api/document/<path:doc_id>/client-path', methods=['GET'])
    def document_client_path(doc_id: str):
        """
        Return UNC and/or POSIX path for opening on the user's machine (session required).
        The browser uses this to launch the OS default app — no companion install.
        """
        if not browser_client_open_enabled:
            return jsonify({'success': False, 'error': 'Browser client-path open disabled'}), 503
        if not _open_mapping_configured():
            return jsonify({
                'success': False,
                'error': 'Open mapping not configured (OPEN_UNC_ROOT / OPEN_CLIENT_LOCAL_ROOT)',
            }), 503
        file_path = str(request.args.get('path') or '').strip()
        if not file_path:
            return jsonify({'success': False, 'error': 'path query parameter required'}), 400
        full_path = _resolve_autodoc_path(file_path)
        if not full_path or not os.path.exists(full_path):
            return jsonify({'success': False, 'error': 'Document path not allowed or missing'}), 404
        targets = _client_open_targets(full_path)
        if not targets:
            return jsonify({'success': False, 'error': 'No client path mapping for this file'}), 503
        body: Dict[str, Any] = {'success': True, 'doc_id': doc_id}
        body.update(targets)
        return jsonify(body)

    @app.route('/api/document/<path:doc_id>/external-open', methods=['GET'])
    def document_external_open(doc_id: str):
        """
        Redirect to OneDrive / SharePoint webUrl for this document (session required).
        Resolves enrichment at click time so links stay fresh and hrefs stay same-origin.
        """
        file_path = str(request.args.get('path') or doc_id).strip()
        url = _resolve_onedrive_url(doc_id, file_path, config)
        if not url:
            logger.info(
                "OneDrive open miss doc_id=%r path=%r enrichment_path=%r",
                doc_id,
                file_path,
                _enrichment_path_from_config(config),
            )
            return jsonify({
                'success': False,
                'error': 'Kein OneDrive-Link für dieses Dokument.',
            }), 404
        return redirect(url, code=302)

    @app.route('/api/open-tokens/mint', methods=['POST'])
    def open_token_mint():
        """Mint a short-lived signed token for companion redeem (browser must send CSRF)."""
        if not companion_enabled:
            return jsonify({'success': False, 'error': 'Companion open disabled'}), 503
        if not _open_mapping_configured():
            return jsonify({
                'success': False,
                'error': (
                    'Open mapping not configured (open.unc_root / open.unc_roots '
                    'and/or open.client_local_root + open.local_root)'
                ),
            }), 503
        csrf_header = str(request.headers.get('X-CSRF-Token', '') or '')
        if not _csrf_token_is_valid(csrf_header):
            return jsonify({'success': False, 'error': 'CSRF token invalid or missing'}), 400
        try:
            data = request.get_json() or {}
            doc_id = str(data.get('doc_id') or '').strip()
            file_path = data.get('path')
            if not doc_id or not file_path:
                return jsonify({'success': False, 'error': 'doc_id and path required'}), 400
            full_path = _resolve_autodoc_path(str(file_path).strip())
            if not full_path or not os.path.exists(full_path):
                return jsonify({'success': False, 'error': 'Document path not allowed or missing'}), 400
            if not _can_open_via_companion(full_path):
                return jsonify({'success': False, 'error': 'No open mapping for this file'}), 503
            rel = str(file_path).strip()
            token = open_token_manager.mint(rel, doc_id)
            api_base = public_base_url_config or request.url_root.rstrip('/')
            redeem_url = f"{api_base}/api/open-tokens/redeem"
            companion_href = (
                f"{companion_uri_scheme}:open?token={quote(token, safe='')}"
                f"&apiBase={quote(api_base, safe='')}"
            )
            return jsonify({
                'success': True,
                'token': token,
                'redeem_url': redeem_url,
                'companion_href': companion_href,
                'doc_id': doc_id,
            })
        except Exception as e:
            logger.error(f"open_token_mint: {e}", exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/open-tokens/redeem', methods=['POST'])
    def open_token_redeem():
        """
        Redeem token → UNC and/or client-local path. No login (companion calls with Bearer).
        """
        if not companion_enabled:
            return jsonify({'success': False, 'error': 'Companion open disabled'}), 503
        try:
            auth = str(request.headers.get('Authorization', '') or '')
            token = ''
            if auth.lower().startswith('bearer '):
                token = auth[7:].strip()
            if not token:
                body = request.get_json(silent=True) or {}
                token = str(body.get('token') or '').strip()
            if not token:
                return jsonify({'success': False, 'error': 'Bearer token required'}), 400
            payload = open_token_manager.verify_and_consume(token, consume=True)
            if not payload:
                return jsonify({'success': False, 'error': 'Invalid or expired token'}), 401
            full_path = _resolve_autodoc_path(payload['rel'])
            if not full_path or not os.path.exists(full_path):
                return jsonify({'success': False, 'error': 'File no longer available'}), 410
            unc = _unc_for_resolved_path(full_path)
            client_path = _client_path_for_resolved_path(full_path)
            if not unc and not client_path:
                return jsonify({'success': False, 'error': 'No open mapping'}), 503
            body: Dict[str, Any] = {'success': True}
            if unc:
                body['unc'] = unc
            if client_path:
                body['path'] = client_path
            return jsonify(body)
        except Exception as e:
            logger.error(f"open_token_redeem: {e}", exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/open-tokens/spec', methods=['GET'])
    def open_tokens_spec():
        """Minimal OpenAPI 3 description for mint/redeem (unauthenticated read)."""
        spec = {
            'openapi': '3.0.3',
            'info': {
                'title': 'DocBridge open tokens',
                'version': '1.0.0',
            },
            'paths': {
                '/api/open-tokens/mint': {
                    'post': {
                        'summary': 'Mint signed open token (session + X-CSRF-Token)',
                        'requestBody': {
                            'required': True,
                            'content': {
                                'application/json': {
                                    'schema': {
                                        'type': 'object',
                                        'required': ['doc_id', 'path'],
                                        'properties': {
                                            'doc_id': {'type': 'string'},
                                            'path': {
                                                'type': 'string',
                                                'description': 'Relative path under AutoDoc root',
                                            },
                                        },
                                    }
                                }
                            },
                        },
                        'responses': {'200': {'description': 'token payload'}},
                    }
                },
                '/api/open-tokens/redeem': {
                    'post': {
                        'summary': 'Redeem token for UNC (Authorization: Bearer)',
                        'responses': {'200': {'description': '{success, unc}'}},
                    }
                },
            },
        }
        return jsonify(spec)

    @app.route('/api/health', methods=['GET'])
    def health():
        """Health check endpoint."""
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'semantix_api': api_client.health_check()
        })

    @app.route('/api/version', methods=['GET'])
    def api_version():
        """Public build marker — use after deploy to confirm the running image."""
        _load_search_enrichment(config)
        unique = _unique_enrichment_records()
        js_path = os.path.join(os.path.dirname(__file__), 'static', 'js', 'app.js')
        js_flags = {}
        try:
            with open(js_path, encoding='utf-8') as fh:
                js_text = fh.read()
            js_flags = {
                'onedrive_external_open': 'externalOpenHref' in js_text,
                'match_locations': '_buildMatchLocationsHtml' in js_text,
                'open_document_legacy_shim': 'pathOrBrowserFlag' in js_text,
            }
        except OSError:
            js_flags = {'readable': False}
        enrichment: Dict[str, Any] = {
            'loaded': bool(unique),
            'records': len(unique),
        }
        # Only expose the server-side filesystem path to an authenticated session;
        # this endpoint is unauthenticated (public build marker).
        if session.get('company_login_ok') is True:
            enrichment['path'] = _enrichment_path_from_config(config)
        return jsonify({
            'build_id': DOCBRIDGE_BUILD_ID,
            'asset_version': _static_asset_version(),
            'enrichment': enrichment,
            'js': js_flags,
        })

    @app.route('/api/stats', methods=['GET'])
    def stats():
        """Get usage statistics."""
        _load_search_enrichment(config)
        unique = _unique_enrichment_records()
        enrichment: Dict[str, Any] = {
            'loaded': bool(unique),
            'records': len(unique),
            'lookup_keys': len(_search_enrichment_cache),
        }
        # Only expose the server-side filesystem path to an authenticated session.
        if session.get('company_login_ok') is True:
            enrichment['path'] = _enrichment_path_from_config(config)
        return jsonify({
            'status': 'operational',
            'timestamp': datetime.now().isoformat(),
            'enrichment': enrichment,
            'asset_version': _static_asset_version(),
            'build_id': DOCBRIDGE_BUILD_ID,
        })

    # --- Cortex (Ontology Explorer) -----------------------------------
    # Datenvertrag siehe docs/superpowers/specs/2026-08-04-wissensnetz-ontology-mvp-design.md
    # Mock hinter stabilem Vertrag: get_ontology() liest die Fixture; der
    # spaetere echte Knovas-Endpunkt ersetzt nur das Innere des Stores.

    def _ontology_path_exists(rel_path: str) -> bool:
        full = _resolve_autodoc_path(rel_path)
        return bool(full) and os.path.exists(full)

    # Quelle des Cortex: 'fixture' (Standard, lokale JSON) oder 'graph'
    # (Knovas Knowledge Graph API). Der Vertrag ist identisch, deshalb ist
    # der Wechsel ein Schalter und kein Umbau.
    # Laufzeit-Zustand des Cortex: Textaufloeser, Quelle und Filter-Engine
    # werden einmal erzeugt und wiederverwendet (siehe _ontology_source).
    _cortex_text_resolver: Dict[str, Any] = {}

    def _ontology_source_is_graph() -> bool:
        return (os.getenv('ONTOLOGY_SOURCE') or 'fixture').strip().lower() == 'graph'

    def _document_text_resolver():
        """Wortlaut zu einem Pointer - die API liefert keinen Passagentext."""
        if 'resolver' not in _cortex_text_resolver:
            from ontology_text import DocumentTextResolver
            _cortex_text_resolver['resolver'] = DocumentTextResolver(
                resolve_path=_resolve_autodoc_path)
        return _cortex_text_resolver['resolver']

    def _ontology_source():
        # Eine Instanz ueber alle Anfragen: sonst waere der Topologie-Cache
        # wirkungslos und jede Route holte den Export erneut - bei einem
        # Limit von rund einer Anfrage pro Sekunde ein echtes Problem.
        if not _ontology_source_is_graph():
            return get_ontology(path_exists=_ontology_path_exists)
        if 'source' not in _cortex_text_resolver:
            from ontology_graph import GraphOntologySource
            _cortex_text_resolver['source'] = GraphOntologySource(
                api_client, text_resolver=_document_text_resolver())
        return _cortex_text_resolver['source']

    def _ontology_filter_engine():
        if not _ontology_source_is_graph():
            return get_filter_engine(resolve_path=_resolve_autodoc_path)
        if 'filters' not in _cortex_text_resolver:
            from ontology_graph_filters import GraphFilterEngine
            _cortex_text_resolver['filters'] = GraphFilterEngine(
                api_client,
                state_path=(os.getenv('ONTOLOGY_FILTER_STATE_PATH') or '').strip() or None,
                text_resolver=_document_text_resolver())
        return _cortex_text_resolver['filters']

    @app.route('/api/ontology/summary', methods=['GET'])
    def ontology_summary():
        try:
            store = _ontology_source()
            payload = store.summary()
            return jsonify({'success': True,
                            'types': payload['types'],
                            'relations': payload['relations']})
        except Exception:
            logger.error("Ontology summary error", exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/ontology/entities', methods=['GET'])
    def ontology_entities():
        try:
            type_id = str(request.args.get('type') or '').strip()
            store = _ontology_source()
            return jsonify({'success': True, **store.entities_for_type(type_id)})
        except Exception:
            logger.error("Ontology entities error", exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/ontology/entities/<entity_id>', methods=['GET'])
    def ontology_entity_detail(entity_id: str):
        try:
            store = _ontology_source()
            detail = store.entity_detail(entity_id)
            if detail is None:
                return jsonify({'success': False, 'error': 'Entität nicht gefunden'}), 404
            engine = _ontology_filter_engine()
            detail['filters'] = engine.filters_for_entity(store, entity_id)
            return jsonify({'success': True, **detail})
        except Exception:
            logger.error("Ontology entity detail error for %s", entity_id, exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    # Kuratieren: Der Wissensgraph wird vom Anwender gepflegt, Knovas leitet
    # ihn nicht ab. Beide Quellen koennen schreiben - die Fixture in ihre
    # Datei, die Graph-Quelle ueber POST /secured/graph/nodes bzw. /edges.

    @app.route('/api/ontology/types', methods=['POST'])
    def ontology_type_create():
        try:
            payload = request.get_json(silent=True) or {}
            label = str(payload.get('label') or '').strip()
            created = _ontology_source().create_type(label)
            if created is None:
                return jsonify({'success': False, 'error': 'Name fehlt'}), 400
            return jsonify({'success': True, 'type': created}), 201
        except Exception:
            logger.error("Ontology type create error", exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/ontology/entities', methods=['POST'])
    def ontology_entity_create():
        try:
            payload = request.get_json(silent=True) or {}
            label = str(payload.get('label') or '').strip()
            type_id = str(payload.get('type') or '').strip()
            store = _ontology_source()
            created = store.create_entity(label, type_id)
            if created is None:
                return jsonify({'success': False,
                                'error': 'Name oder Typ fehlt'}), 400
            return jsonify({'success': True, 'entity': created}), 201
        except Exception:
            logger.error("Ontology entity create error", exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/ontology/relations', methods=['POST'])
    def ontology_relation_create():
        try:
            payload = request.get_json(silent=True) or {}
            src = str(payload.get('src') or '').strip()
            dst = str(payload.get('dst') or '').strip()
            predicate = str(payload.get('predicate') or '').strip()
            store = _ontology_source()
            created = store.create_relation(src, predicate, dst)
            if created is None:
                return jsonify({'success': False,
                                'error': 'Verbindung nicht anlegbar'}), 400
            return jsonify({'success': True, 'relation': created}), 201
        except Exception:
            logger.error("Ontology relation create error", exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/ontology/type-relations', methods=['POST'])
    def ontology_type_relation_create():
        """Vorgabe auf Typebene. Die API kennt keine Kante zwischen Typen,
        deshalb wird daraus ein Schema-Attribut (siehe Design 2026-08-08)."""
        try:
            payload = request.get_json(silent=True) or {}
            created = _ontology_source().create_type_relation(
                str(payload.get('src') or '').strip(),
                str(payload.get('predicate') or '').strip(),
                str(payload.get('dst') or '').strip())
            if created is None:
                return jsonify({'success': False,
                                'error': 'Vorgabe nicht anlegbar'}), 400
            return jsonify({'success': True, 'relation': created}), 201
        except Exception:
            logger.error("Ontology type relation create error", exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/ontology/type-relations', methods=['DELETE'])
    def ontology_type_relation_delete():
        try:
            payload = request.get_json(silent=True) or {}
            entfernt = _ontology_source().delete_type_relation(
                str(payload.get('src') or '').strip(),
                str(payload.get('predicate') or '').strip(),
                str(payload.get('dst') or '').strip())
            if not entfernt:
                return jsonify({'success': False, 'error': 'Vorgabe nicht gefunden'}), 404
            return jsonify({'success': True})
        except Exception:
            logger.error("Ontology type relation delete error", exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/ontology/relations', methods=['DELETE'])
    def ontology_relation_delete():
        try:
            payload = request.get_json(silent=True) or {}
            entfernt = _ontology_source().delete_relation(
                str(payload.get('src') or '').strip(),
                str(payload.get('predicate') or '').strip(),
                str(payload.get('dst') or '').strip())
            if not entfernt:
                return jsonify({'success': False,
                                'error': 'Verbindung nicht gefunden'}), 404
            return jsonify({'success': True})
        except Exception:
            logger.error("Ontology relation delete error", exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/ontology/types/<type_id>', methods=['DELETE'])
    def ontology_type_delete(type_id: str):
        try:
            if not _ontology_source().delete_type(type_id):
                return jsonify({'success': False, 'error': 'Typ nicht gefunden'}), 404
            return jsonify({'success': True})
        except Exception:
            logger.error("Ontology type delete error for %s", type_id, exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/ontology/entities/<entity_id>', methods=['DELETE'])
    def ontology_entity_delete(entity_id: str):
        try:
            if not _ontology_source().delete_entity(entity_id):
                return jsonify({'success': False, 'error': 'Entität nicht gefunden'}), 404
            return jsonify({'success': True})
        except Exception:
            logger.error("Ontology entity delete error for %s", entity_id, exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    # Filter (Req 2.2): echtes Passagen-Matching + permanente Rejection-
    # Memory, Logik in ontology_filters.py. POSTs laufen durch das
    # bestehende CSRF-Header-Gate; Routen stehen in KEINEM Exempt-Set.

    @app.route('/api/ontology/filters', methods=['POST'])
    def ontology_filter_create():
        try:
            payload = request.get_json(silent=True) or {}
            entity_id = str(payload.get('entity_id') or '').strip()
            label = str(payload.get('label') or '').strip()
            store = _ontology_source()
            if store.entity_detail(entity_id) is None:
                return jsonify({'success': False, 'error': 'Entität nicht gefunden'}), 404
            engine = _ontology_filter_engine()
            created = engine.create_filter(entity_id, label)
            if created is None:
                return jsonify({'success': False,
                                'error': 'Filterbeschreibung fehlt'}), 400
            detail = engine.filter_detail(store, created['id'])
            return jsonify({'success': True, **detail}), 201
        except Exception:
            logger.error("Ontology filter create error", exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/ontology/filters/<filter_id>', methods=['GET'])
    def ontology_filter_detail(filter_id: str):
        try:
            store = _ontology_source()
            engine = _ontology_filter_engine()
            detail = engine.filter_detail(store, filter_id)
            if detail is None:
                return jsonify({'success': False, 'error': 'Filter nicht gefunden'}), 404
            return jsonify({'success': True, **detail})
        except Exception:
            logger.error("Ontology filter detail error for %s", filter_id, exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    @app.route('/api/ontology/filters/<filter_id>/decision', methods=['POST'])
    def ontology_filter_decision(filter_id: str):
        try:
            payload = request.get_json(silent=True) or {}
            proposal_id = str(payload.get('proposal_id') or '').strip()
            action = str(payload.get('action') or '').strip()
            store = _ontology_source()
            engine = _ontology_filter_engine()
            proposal, error = engine.decide(store, filter_id, proposal_id, action)
            if error == 'bad_action':
                return jsonify({'success': False, 'error': 'Ungültige Aktion'}), 400
            if error == 'not_found':
                return jsonify({'success': False, 'error': 'Vorschlag nicht gefunden'}), 404
            return jsonify({'success': True, 'proposal': proposal})
        except Exception:
            logger.error("Ontology filter decision error for %s", filter_id, exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    return app


def _effective_cosine_distance(result: Dict[str, Any]) -> Optional[float]:
    """
    Knovas-style distance: 0 = best, higher = worse.
    Prefer API cosine_distance; else derive from cosine_similarity (1 - sim).
    """
    cd = result.get("cosine_distance")
    if cd is not None and str(cd).strip() != "":
        try:
            return float(cd)
        except (TypeError, ValueError):
            pass
    cs = result.get("cosine_similarity")
    if cs is not None and str(cs).strip() != "":
        try:
            return max(0.0, min(1.0, 1.0 - float(cs)))
        except (TypeError, ValueError):
            pass
    return None


def _search_result_haystack(result: Dict[str, Any]) -> str:
    """Lowercased text used for strict / exact-style matching."""
    parts = [
        result.get('title'),
        result.get('snippet'),
        result.get('content'),
        result.get('ingested_summary'),
        result.get('description'),
        result.get('path'),
        result.get('doc_id'),
        result.get('page_number'),
        result.get('sentence_number'),
    ]
    return ' '.join(str(p) for p in parts if p is not None and str(p).strip()).lower()


def _apply_search_refinement(
    enhanced: Dict[str, Any],
    query: str,
    filters: Dict[str, Any],
    config,
) -> Dict[str, Any]:
    """
    Tighten results after Knovas returns (semantic search is loose by default).

    - exact_match (UI checkbox): every significant token must appear as a substring
      in title/snippet/content/description/path (after enrichment).
    - min_similarity_score: minimum internal *similarity* (higher = better; same scale as
      cosine_similarity, or 1 - cosine_distance). NOT the same as max distance.
    - max_cosine_distance: drop hits whose cosine *distance* exceeds this (0 = best).
      If the API sends no scores, see enforce_similarity_threshold_when_set.
    """
    out: List[Dict[str, Any]] = list(enhanced.get('results', []))

    min_score = config.get_float('web.search.min_similarity_score', 0.0)
    if min_score > 0.0 and out:
        scores = [float(r.get('score') or 0) for r in out]
        max_s = max(scores) if scores else 0.0
        if max_s > 1e-9:
            out = [r for r in out if float(r.get('score') or 0) >= min_score]
        else:
            enforce = config.get_bool(
                'web.search.enforce_similarity_threshold_when_set', True
            )
            if enforce:
                logger.warning(
                    "min_similarity_score=%s is set but search results have no similarity "
                    "values (Knovas may use a different JSON field). Returning no results. "
                    "Set web.search.enforce_similarity_threshold_when_set: false to show "
                    "unfiltered API results until the API exposes scores.",
                    min_score,
                )
                out = []
            else:
                logger.warning(
                    "min_similarity_score=%s ignored: all result scores are zero/missing.",
                    min_score,
                )

    max_dist = config.get_float("web.search.max_cosine_distance", -1.0)
    if max_dist >= 0.0 and out:
        kept: List[Dict[str, Any]] = []
        for r in out:
            d = _effective_cosine_distance(r)
            if d is None:
                kept.append(r)
            elif d <= max_dist + 1e-9:
                kept.append(r)
        out = kept

    if filters.get('exact_match'):
        min_term = config.get_int('web.search.strict_match_min_term_length', 2)
        qlow = (query or '').strip().lower()
        terms = [t for t in re.split(r'\s+', qlow) if len(t) >= min_term]

        def matches(r: Dict[str, Any]) -> bool:
            hay = _search_result_haystack(r)
            if terms:
                return all(t in hay for t in terms)
            return qlow in hay if qlow else True

        out = [r for r in out if matches(r)]

    # Highest similarity first; tie-break by doc_id for stable ordering.
    out.sort(
        key=lambda r: (-float(r.get('score') or 0), str(r.get('doc_id') or '')),
    )

    refined = enhanced.copy()
    refined['results'] = out
    refined['total'] = len(out)
    return refined


def _enrichment_title_matches_query(
    title: str,
    query: str,
    exact_match: bool,
    min_term: int,
) -> bool:
    """Whether filename/title should count as a hit for this query (case-insensitive)."""
    t = (title or "").lower()
    qlow = (query or "").strip().lower()
    if not t or not qlow:
        return False
    if exact_match:
        terms = [x for x in re.split(r"\s+", qlow) if len(x) >= min_term]
        if terms:
            return all(term in t for term in terms)
        return qlow in t
    if qlow in t:
        return True
    for tok in re.split(r"\W+", qlow):
        if len(tok) >= 3 and tok in t:
            return True
    return False


def _supplement_results_from_enrichment_filenames(
    query: str,
    results: List[Dict[str, Any]],
    filters: Dict[str, Any],
    config,
    *,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """
    Add OneDrive (JSONL) rows whose *title* matches the query even when Knovas
    vector search does not return them (e.g. match in filename only).
    """
    if not config.get_bool("web.search.supplement_filename_matches", False):
        return results
    min_sq = config.get_int("web.search.supplement_min_query_length", 3)
    qstrip = (query or "").strip()
    if len(qstrip) < min_sq:
        return results

    enrichment = _load_search_enrichment(config)
    unique_records = _unique_enrichment_records()
    if not enrichment or not unique_records:
        return results

    existing = {str(r.get("doc_id")) for r in results if r.get("doc_id") is not None}
    min_term = config.get_int("web.search.strict_match_min_term_length", 2)
    exact = bool(filters.get("exact_match"))
    max_scan = config.get_int("web.search.supplement_max_enrichment_scan", 5000)
    max_extra = max(0, limit)

    extra: List[Dict[str, Any]] = []
    scanned = 0
    for meta in unique_records:
        did = meta.get("doc_id")
        if did is None:
            continue
        did = str(did)
        scanned += 1
        if scanned > max_scan:
            break
        if did in existing:
            continue
        title = meta.get("title") or ""
        if not _enrichment_title_matches_query(title, query, exact, min_term):
            continue
        row: Dict[str, Any] = {
            "doc_id": did,
            "path": did,
            "score": 0.01,
            "title": title,
            "source": "semantix",
            "match_supplement": "filename",
        }
        if meta.get("doc_type"):
            row["type"] = meta["doc_type"]
            row["doc_type"] = meta["doc_type"]
        if meta.get("description"):
            row["description"] = meta["description"]
        if meta.get("akten_id"):
            row["akten_id"] = meta["akten_id"]
        wu = meta.get("web_url") or meta.get("webUrl")
        if wu and _is_safe_http_url(wu):
            _apply_external_open_mode(row, str(wu).strip())
        else:
            row.setdefault("file_exists", False)
            row.setdefault("can_open", False)
        extra.append(row)
        if len(extra) >= max_extra:
            break

    if not extra:
        return results

    merged = list(results) + extra
    merged.sort(
        key=lambda r: (0 if r.get("match_supplement") == "filename" else 1, -float(r.get("score") or 0), str(r.get("doc_id") or "")),
    )
    return merged


def _is_safe_http_url(url: Optional[str]) -> bool:
    if not url or not isinstance(url, str):
        return False
    u = url.strip().lower()
    return u.startswith("https://") or u.startswith("http://")


def _normalize_doc_key(raw: Optional[str]) -> str:
    return str(raw or "").strip().replace("\\", "/")


def _canonical_lookup_key(raw: Optional[str]) -> str:
    return _normalize_doc_key(raw).strip("/").lower()


def _infer_identifier_prefixes_from_enrichment(unique: List[dict]) -> List[str]:
    """Detect shared first path segment (e.g. corpus) from OneDrive JSONL doc_ids."""
    if not unique:
        return []
    counts: Dict[str, int] = {}
    sample = unique[: min(1000, len(unique))]
    for rec in sample:
        did = _normalize_doc_key(rec.get("doc_id")).strip("/")
        if "/" not in did:
            continue
        first = did.split("/", 1)[0].lower()
        if first:
            counts[first] = counts.get(first, 0) + 1
    if not counts:
        return []
    top_seg, top_n = max(counts.items(), key=lambda kv: kv[1])
    if top_n >= max(5, int(len(sample) * 0.6)):
        return [top_seg]
    return []


def _enrichment_lookup_keys(result: Dict[str, Any]) -> List[str]:
    """Candidate doc_id keys for OneDrive enrichment JSONL (pointer prefix variants)."""
    seen: set[str] = set()
    keys: List[str] = []

    def add(key: str) -> None:
        normalized = _canonical_lookup_key(key)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        keys.append(normalized)

    for raw in (result.get("doc_id"), result.get("path"), result.get("pointer")):
        if not raw:
            continue
        s = _normalize_doc_key(raw)
        add(s)
        rel = _rel_path_for_autodoc(s)
        add(rel)
        for prefix in _effective_identifier_prefixes():
            if rel:
                add(f"{prefix}/{rel}")
    return keys


def _web_url_from_enrichment(meta: dict) -> Optional[str]:
    for key in ("web_url", "webUrl", "onedrive_url", "sharepoint_url", "external_url"):
        val = meta.get(key)
        if val and _is_safe_http_url(str(val)):
            return str(val).strip()
    return None


def _lookup_enrichment_meta(enrichment: Dict[str, dict], result: Dict[str, Any]) -> Optional[dict]:
    for key in _enrichment_lookup_keys(result):
        meta = enrichment.get(key)
        if meta:
            return meta
    for field in ("path", "doc_id", "pointer"):
        raw = result.get(field)
        if not raw:
            continue
        base = _canonical_lookup_key(str(raw)).rsplit("/", 1)[-1]
        if not base:
            continue
        candidates = _search_enrichment_by_basename.get(base, [])
        if len(candidates) == 1:
            return candidates[0]
    for field in ("path", "doc_id", "pointer"):
        raw = result.get(field)
        if not raw:
            continue
        candidate = _canonical_lookup_key(str(raw))
        suffix_hits = [
            meta
            for key, meta in enrichment.items()
            if key == candidate or key.endswith("/" + candidate)
        ]
        if len(suffix_hits) == 1:
            return suffix_hits[0]
    return None


def _enrichment_index_keys(doc_id: str) -> List[str]:
    """All lookup keys to register for one enrichment JSONL row."""
    return _enrichment_lookup_keys({
        "doc_id": doc_id,
        "path": doc_id,
        "pointer": doc_id,
    })


def _resolve_onedrive_url(doc_id: str, path: str, config=None) -> Optional[str]:
    enrichment = _load_search_enrichment(config)
    if not enrichment:
        return None
    meta = _lookup_enrichment_meta(enrichment, {
        "doc_id": doc_id,
        "path": path,
        "pointer": path or doc_id,
    })
    return _web_url_from_enrichment(meta) if meta else None


def _unique_enrichment_records() -> List[dict]:
    """One dict per JSONL line (not per alias key)."""
    return list(_search_enrichment_unique)


def _apply_external_open_mode(result: Dict[str, Any], url: str) -> None:
    """OneDrive / SharePoint link — preferred over local UNC open."""
    result["external_url"] = url.strip()
    result["file_exists"] = True
    result["can_open"] = True
    result["open_mode"] = "external"
    result.pop("open_via_browser", None)
    result.pop("open_via_companion", None)
    result.pop("client_open_unc", None)
    result.pop("client_open_path", None)


def _enrichment_path_from_config(config=None) -> str:
    path = os.getenv("SEARCH_ENRICHMENT_PATH", "").strip()
    if not path and config is not None:
        path = str(config.get("web.search.enrichment_path", "") or "").strip()
    if path and not os.path.isfile(path) and os.path.isfile(_DEFAULT_ENRICHMENT_PATH):
        logger.warning(
            "Search enrichment not found at %s; using %s",
            path,
            _DEFAULT_ENRICHMENT_PATH,
        )
        return _DEFAULT_ENRICHMENT_PATH
    if not path and os.path.isfile(_DEFAULT_ENRICHMENT_PATH):
        path = _DEFAULT_ENRICHMENT_PATH
    return path


def _context_store_path_from_config(config=None) -> str:
    path = os.getenv("SEARCH_CONTEXT_STORE_PATH", "").strip()
    if not path and config is not None:
        path = str(config.get("web.search.context_store_path", "") or "").strip()
    if path and not os.path.isdir(path) and os.path.isdir(_DEFAULT_CONTEXT_STORE_PATH):
        logger.warning(
            "Context store not found at %s; using %s",
            path,
            _DEFAULT_CONTEXT_STORE_PATH,
        )
        return _DEFAULT_CONTEXT_STORE_PATH
    if not path and os.path.isdir(_DEFAULT_CONTEXT_STORE_PATH):
        path = _DEFAULT_CONTEXT_STORE_PATH
    return path


def _load_search_enrichment(config=None) -> Dict[str, dict]:
    """
    Load JSONL written by docbridge-sync (OneDrive webUrl, title, etc.).
    Last line per doc_id wins. Cached by file mtime.
    Indexed by doc_id and pointer-prefix aliases for reliable lookup.
    """
    global _search_enrichment_cache, _search_enrichment_mtime, _search_enrichment_unique
    global _search_enrichment_by_basename, _search_enrichment_inferred_prefixes
    path = _enrichment_path_from_config(config)
    if not path:
        _search_enrichment_unique = []
        _search_enrichment_by_basename = {}
        _search_enrichment_inferred_prefixes = []
        return {}
    max_bytes = 0
    if config is not None:
        max_bytes = config.get_int("web.search.enrichment_max_bytes", 52_428_800)
    try:
        if not os.path.isfile(path):
            _search_enrichment_unique = []
            _search_enrichment_by_basename = {}
            _search_enrichment_inferred_prefixes = []
            return {}
        if max_bytes > 0:
            file_size = os.path.getsize(path)
            if file_size > max_bytes:
                logger.warning(
                    "Search enrichment skipped: %s is %d bytes (max %d)",
                    path,
                    file_size,
                    max_bytes,
                )
                _search_enrichment_unique = []
                _search_enrichment_by_basename = {}
                _search_enrichment_inferred_prefixes = []
                return {}
        mtime = os.path.getmtime(path)
        if mtime == _search_enrichment_mtime and _search_enrichment_cache:
            return _search_enrichment_cache
    except OSError:
        _search_enrichment_unique = []
        _search_enrichment_by_basename = {}
        _search_enrichment_inferred_prefixes = []
        return {}

    by_id: Dict[str, dict] = {}
    unique: List[dict] = []
    basename_index: Dict[str, List[dict]] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                did = rec.get("doc_id")
                if did is None:
                    continue
                did_s = str(did)
                unique.append(rec)
                base = _canonical_lookup_key(did_s).rsplit("/", 1)[-1]
                if base:
                    basename_index.setdefault(base, []).append(rec)
        _search_enrichment_inferred_prefixes = _infer_identifier_prefixes_from_enrichment(unique)
        for rec in unique:
            did_s = str(rec.get("doc_id"))
            for key in _enrichment_index_keys(did_s):
                by_id[_canonical_lookup_key(key)] = rec
        _search_enrichment_cache = by_id
        _search_enrichment_unique = unique
        _search_enrichment_by_basename = basename_index
        _search_enrichment_mtime = mtime
        logger.info(
            "Loaded search enrichment: %d records, %d lookup keys, prefixes=%s from %s",
            len(unique),
            len(by_id),
            _search_enrichment_inferred_prefixes or _autodoc_identifier_prefixes(),
            path,
        )
    except Exception as e:
        logger.warning("Could not load search enrichment from %s: %s", path, e)
        _search_enrichment_unique = []
        _search_enrichment_by_basename = {}
        _search_enrichment_inferred_prefixes = []

    return _search_enrichment_cache


def _apply_autodoc_disk_metadata(result: Dict[str, Any], full_path: str) -> None:
    """Populate file_exists / size / mtime from disk (open path or explicit verify)."""
    result["file_exists"] = os.path.exists(full_path)
    result["can_open"] = result["file_exists"]
    if result["file_exists"]:
        try:
            stat = os.stat(full_path)
            result["file_size"] = stat.st_size
            result["modified_at"] = datetime.fromtimestamp(stat.st_mtime).isoformat()
        except OSError:
            pass


def _enhance_search_results(
    results: Dict[str, Any],
    file_handler: AutoDocFileHandler,
    config=None,
) -> Dict[str, Any]:
    """
    Enhance search results with additional metadata.
    
    Args:
        results: Raw search results from API
        file_handler: AutoDocFileHandler instance
        config: App config (optional); controls verify_files_on_disk
        
    Returns:
        Enhanced results
    """
    verify_disk = True
    if config is not None:
        verify_disk = config.get_bool("web.search.verify_files_on_disk", True)
    enrichment = _load_search_enrichment(config)
    context_store_path = _context_store_path_from_config(config)
    context_radius = 10
    if config is not None:
        context_radius = config.get_int("web.search.context_sentences", 10)
    enhanced_results = results.copy()
    
    if 'results' not in enhanced_results:
        return enhanced_results

    for result in enhanced_results['results']:
        doc_id = str(result.get("doc_id") or result.get("pointer") or "")
        meta = _lookup_enrichment_meta(enrichment, result) if enrichment else None
        if meta:
            if meta.get("title"):
                result["title"] = meta["title"]
            if meta.get("doc_type"):
                result["type"] = meta["doc_type"]
                result["doc_type"] = meta["doc_type"]
            if meta.get("description"):
                result["description"] = meta["description"]
            if meta.get("akten_id"):
                result["akten_id"] = meta["akten_id"]
            meta_date = (
                meta.get("date")
                or meta.get("document_date")
                or meta.get("timestamp")
                or meta.get("modified_at")
            )
            if meta_date and not result.get("document_date") and not result.get("date"):
                result["document_date"] = meta_date
                result["date"] = meta_date
            wu = _web_url_from_enrichment(meta)
            if wu:
                _apply_external_open_mode(result, wu)

        for key in ("web_url", "webUrl", "external_url"):
            direct = result.get(key)
            if direct and _is_safe_http_url(str(direct)):
                _apply_external_open_mode(result, str(direct))
                break

        fp = (result.get("path") or "").strip()
        if _is_safe_http_url(fp):
            _apply_external_open_mode(result, (result.get("external_url") or fp).strip())

        if result.get("external_url") and _is_safe_http_url(result["external_url"]):
            continue

        file_path = result.get("path")
        if file_path:
            rel = _rel_path_for_autodoc(str(file_path))
            result["autodoc_rel_path"] = rel
            # Confine to the AutoDoc root before touching disk. Without this,
            # a ``../`` pointer leaked file existence/size/mtime of host files
            # (path-metadata oracle) even though the download endpoint blocks it.
            full_path = _confine_to_autodoc(file_handler.autodoc_path, str(file_path))
            if not full_path:
                result["file_exists"] = False
                result["can_open"] = False
            elif verify_disk:
                _apply_autodoc_disk_metadata(result, full_path)
            else:
                result["can_open"] = bool(rel)
                result["file_exists"] = None
        else:
            result.setdefault("file_exists", False)
            result.setdefault("can_open", False)

        pointer_keys: List[str] = []
        seen_ptr: set[str] = set()
        for field in ("doc_id", "path", "pointer"):
            raw = result.get(field)
            if not raw:
                continue
            s = str(raw).strip()
            if s and s not in seen_ptr:
                seen_ptr.add(s)
                pointer_keys.append(s)
        if context_store_path and pointer_keys:
            enrich_result_with_context(
                result,
                context_store_path,
                pointer_keys,
                context_radius=context_radius,
            )

    return enhanced_results


def _open_file_external(file_path: str, config) -> bool:
    """
    Open file with external application.
    
    Args:
        file_path: Path to file
        config: Configuration object
        
    Returns:
        True if successful, False otherwise
    """
    try:
        system = platform.system()
        
        external_app = config.get('web.document_handler.external_app')
        
        if external_app and os.path.exists(external_app):
            subprocess.Popen([external_app, file_path])
            return True
        
        if system == 'Windows':
            os.startfile(file_path)
        elif system == 'Darwin':
            subprocess.Popen(['open', file_path])
        else:
            subprocess.Popen(['xdg-open', file_path])
        
        return True
        
    except Exception as e:
        logger.error(f"Error opening file externally: {e}")
        return False


def main():
    """Main entry point for web interface."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='DocBridge Document Search Web Interface'
    )
    parser.add_argument(
        '--config',
        type=str,
        help='Path to configuration file'
    )
    parser.add_argument(
        '--host',
        type=str,
        help='Host to bind to'
    )
    parser.add_argument(
        '--port',
        type=int,
        help='Port to bind to'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug mode'
    )
    parser.add_argument(
        '--log-level',
        type=str,
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Logging level'
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('docbridge_web.log')
        ]
    )
    
    app = create_app(config_path=args.config)
    
    config = get_config()
    
    host = args.host or config.get('web.host', '0.0.0.0')
    port = args.port or config.get_int('web.port', 8080)
    debug = args.debug or config.get_bool('web.debug', False)
    
    logger.info(f"Starting DocBridge Web Interface on {host}:{port}")
    logger.info(f"Debug mode: {debug}")
    
    app.run(
        host=host,
        port=port,
        debug=debug,
        threaded=True
    )


if __name__ == '__main__':
    main()
