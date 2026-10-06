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
import threading
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for, g
from flask_cors import CORS
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
import subprocess
import platform
import re

import requests
from urllib.parse import quote

from config_loader import get_config
import german_text
from context_store import (
    enrich_result_with_context,
    indexed_text,
    load_context,
    sentences_by_number_map,
)
from context_store import query_terms as context_query_terms
import doc_fields_view
from knovas_client import KnovasAPIClient, QueryRejected
from file_utils import AutoDocFileHandler
from document_grants import DEFAULT_TTL_SECONDS as GRANT_TTL_DEFAULT
from document_grants import DocumentGrantStore
from open_tokens import OpenTokenManager
from ontology_filters import get_filter_engine
from ontology_store import get_ontology
from web_interface import doc_fields_routes
from web_interface.preview import (
    PreviewFailed,
    PreviewUnsupported,
    extract_markdown,
    highlight_pdf,
    passage_anchors,
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

    Knovas Connector sync uses identifier_prefix (e.g. ``corpus/rel/path.txt``).
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
    """Docker logs: see whether Knovas scores survived into the hits.

    Counts and scores only. The query text and the pointers used to be in
    this line; both carry client names, and a log line is the one place a
    value must never reach (spec D6). ``query`` stays in the signature so
    callers need not change; only its length is logged.
    """
    if not results:
        logger.info("Search similarity debug query_len=%d: 0 results", len(query or ''))
        return
    scores = [float(r.get('score') or 0) for r in results]
    with_cosine = sum(
        1 for r in results
        if r.get('cosine_similarity') is not None or r.get('cosine_distance') is not None
    )
    logger.info(
        "Search similarity debug query_len=%d count=%s max_score=%.6f min_score=%.6f "
        "with_cosine=%d",
        len(query or ''),
        len(results),
        max(scores),
        min(scores),
        with_cosine,
    )


def _demo_hit_locations(count: int = 15) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Build primary + top_chunks for local multihit UI demo (distinct page/sentence pairs)."""
    locations: List[Dict[str, Any]] = []
    for i in range(count):
        locations.append({
            'page_number': (i // 5) + 1,
            # Durchlaufend ueber das ganze Dokument, so wie Knovas sie meldet --
            # nicht je Seite von vorne. Sonst fallen die Stellen auf Seite 2 mit
            # denen auf Seite 1 zusammen, und die Attrappe zeigt etwas, das es
            # im Betrieb nicht gibt.
            'sentence_number': i + 1,
            'cosine_similarity': round(max(0.55, 0.93 - i * 0.025), 2),
        })
    return locations[0], locations


_demo_primary, _demo_top_chunks = _demo_hit_locations(15)


def _demo_card_snippet(locations: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """Der Kartenausschnitt: die beste Fundstelle, ohne Vorlauf.

    So wie ``context_store.enrich_result_with_context`` ihn im Betrieb baut.
    Vorher trug jede Attrappe ihren eigenen, festen Ausschnitt -- und dann
    zeigte die Karte lokal einen anderen Satz als der Dialog, also genau den
    Zustand, den die Aenderung beseitigt.
    """
    if not locations:
        return None
    best = locations[0]
    return {
        'before': '',
        'match': str(best.get('match') or '').strip(),
        'after': str(best.get('after') or '').strip(),
    }


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


# Ein Demo-Dokument, Satz fuer Satz, so wie ein Kontext-Sidecar es haelt.
# Wortgleich mit dem Text, den die lokale Vorschau anzeigt: nur dann findet die
# Oberflaeche die Fundstelle im dargestellten Text wieder, und nur dann zeigt
# das lokale Entwickeln, was der Betrieb zeigt. Ueberschrift und Aktenzeichen
# stehen absichtlich mit drin -- an ihnen sieht man, dass die Liste sie weglaesst.
_DEMO_SENTENCE_TEXTS = [
    'MANDATSVEREINBARUNG',
    'Aktenzeichen: 2019-021',
    'In Sachen: Alpenblick Gastro GmbH ./. Paechterkollektiv',
    'Der Hauptmietzins ist die periodisch zu entrichtende Gegenleistung.',
    'Die Reaktionszeit bei Stoerungen der Prioritaetsstufe 1 betraegt vier Stunden.',
    'Sie wird ab Eingang der Meldung gemessen, nicht ab Eintritt des Fehlers.',
    'Fuer Wohnungen gilt das MRG mit besonderen Kuendigungsschutzbestimmungen.',
    'Wird die Reaktionszeit ueberschritten, eskaliert der Auftragnehmer selbsttaetig.',
    'Die Ansprechperson des Auftraggebers ist im Anhang benannt.',
    'Mietzinsanpassungen beduerfen einer gesetzlichen Grundlage.',
    'Die Messung der Reaktionszeit erfolgt ueber das Ticketsystem des Auftragnehmers.',
    'Abweichende Fristen sind in Anlage 4 abschliessend geregelt.',
    'Die Mandantin Alpenblick Gastro GmbH wird nach Zeitaufwand abgerechnet.',
]

_DEMO_SENTENCES = [
    {'i': n + 1, 'p': (n // 5) + 1, 't': text}
    for n, text in enumerate(_DEMO_SENTENCE_TEXTS)
]


def _demo_match_locations(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fundstellen fuer die lokale UI-Entwicklung.

    Ueber denselben Code wie im Betrieb: ``build_match_locations`` entscheidet,
    welche der gemeldeten Stellen eine Fundstelle wird. Vorher baute diese
    Attrappe die Liste selbst, und dann zeigte die lokale Oberflaeche acht
    Eintraege, wo die echte zwei je Seite zeigt -- eine Aenderung an der Auswahl
    war lokal gar nicht zu sehen.
    """
    from context_store import build_match_locations

    return build_match_locations(_DEMO_SENTENCES, chunks)


_TEST_SEARCH_FIXTURES: List[Dict[str, Any]] = [
    {
        'doc_id': 'corpus/demo/Mietrecht_Kommentar.pdf',
        'path': 'corpus/demo/Mietrecht_Kommentar.pdf',
        'title': 'Mietrecht Kommentar (Demo: 15 Trefferorte, 2 je Seite)',
        'akten_id': '2024-050',
        'type': 'Kommentar',
        'score': _demo_primary['cosine_similarity'],
        'cosine_similarity': _demo_primary['cosine_similarity'],
        'cosine_distance': round(1.0 - float(_demo_primary['cosine_similarity']), 2),
        'page_number': _demo_primary['page_number'],
        'sentence_number': _demo_primary['sentence_number'],
        'document_date': '2024-08-10T09:00:00',
        'top_chunks': _demo_top_chunks,
        'match_locations': _demo_match_locations(_demo_top_chunks),
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
            'Demo-Dokument, zu dem Knovas 15 Trefferorte meldet. Die Liste zeigt '
            'davon zwei je Seite; Briefkopf und Ueberschriften fallen weg. '
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
        # Ueber das Dokument verteilt, damit die Fundstellenliste lokal mehr als
        # einen Eintrag zeigt. Satz 1 und 2 sind Ueberschrift und Aktenzeichen --
        # sie werden gemeldet und fallen weg, genau darum stehen sie hier.
        # Mit sentence_number_end, so wie Knovas meldet: ein Chunk deckt mehrere
        # Saetze ab, und die Fundstelle wird darin gesucht.
        'match_locations': _demo_match_locations([
            {'page_number': 1, 'sentence_number': n, 'sentence_number_end': n + 2,
             'cosine_similarity': c}
            for n, c in ((1, 0.88), (3, 0.84), (5, 0.82), (8, 0.80),
                         (11, 0.78), (13, 0.76))
        ]),
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

# Karte und Dialog zeigen dieselbe Stelle -- im Betrieb, weil beide aus der
# besten Fundstelle kommen, hier, weil die Attrappe es nachzieht. Sonst wuerde
# lokal wieder auseinanderlaufen, was die Aenderung zusammengebracht hat.
for _fixture in _TEST_SEARCH_FIXTURES:
    _card = _demo_card_snippet(_fixture.get('match_locations') or [])
    if _card:
        _fixture['context_snippet'] = _card
del _fixture, _card


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

    When the pointer carries a prefix this deployment was not told about, the
    first candidate lands nowhere and a second is tried with one leading segment
    removed. That prefix is free text an administrator types into the Übernahme
    profile ("Mandanten Sync"), while the Platform only strips what
    AUTODOC_IDENTIFIER_PREFIX names -- and when the two disagree, nothing says
    so: search works, the snippets work (their sidecars are keyed by the pointer
    itself, not by a path), and every preview, thumbnail and Öffnen answers 404.
    A whole deployment can sit in that state looking healthy.

    The fallback is safe because it only ever *shortens* the path, so both
    confinement checks still apply to what is returned, and because the caller
    has already had to hold a grant for this exact pointer. It logs what would
    settle it properly, so the guess does not quietly become the configuration.
    """
    rel = _rel_path_for_autodoc(file_path)
    candidate = _autodoc_candidate(autodoc_path, rel)
    if candidate is None or os.path.exists(candidate):
        return candidate
    for found, note in _autodoc_alternatives(autodoc_path, rel):
        if note:
            logger.warning("%s", note)
        return found
    return candidate


# The subdirectory of the mount that pointers are relative to, once we have
# worked it out. One lookup settles it for the whole corpus, so this is learned
# once and then costs a single stat per request.
_autodoc_offset: Dict[str, str] = {}
_autodoc_offset_lock = threading.Lock()

# How far to look for that subdirectory, and how many directories to visit
# doing it. Bounded because this runs on a request thread against a share.
_AUTODOC_OFFSET_MAX_DEPTH = 4
_AUTODOC_OFFSET_MAX_DIRS = 400


def _autodoc_alternatives(autodoc_path: str, rel: str):
    """Yield (resolved_path, log_note) for a pointer that did not resolve directly.

    A pointer's path is relative to the **source folder of the Übernahme
    profile** (sync_executor sets rel_root to that folder), while this side
    mounts KNOVAS_DOCUMENTS_PATH whole. Point a profile at a subdirectory --
    ``/mnt/documents/kanzlei/Mandanten`` -- and every pointer is missing
    ``kanzlei/Mandanten`` from the front. Nothing says so: the search works, the
    snippets work, and only the routes that need the file itself fail. Download
    and Öffnen have no fallback, so they are where it shows.

    Two shapes are tried. A leading segment too many (a pointer prefix this
    deployment was not told about) and a leading segment too few (the profile
    is rooted below the mount). The second is discovered by looking for the
    document under the mount and is then remembered, so the walk happens once.
    """
    base = os.path.abspath(autodoc_path)

    learned = _autodoc_offset.get(base)
    if learned:
        found = _autodoc_candidate(autodoc_path, f"{learned}/{rel}")
        if found and os.path.exists(found):
            yield found, ""
            return

    head, _, tail = rel.partition("/")
    if head and tail:
        found = _autodoc_candidate(autodoc_path, tail)
        if found and os.path.exists(found):
            # The prefix is configuration; the rest of the pointer is not
            # logged (it names clients and matters).
            yield found, (
                f"A pointer resolved only without its leading {head!r}. "
                f"Set KNOVAS_IDENTIFIER_PREFIX={head} in knovas.env (or match the "
                f"Kennung on the Übernahme profile) to stop guessing."
            )
            return

    offset = _discover_autodoc_offset(base, rel)
    if offset is None:
        return
    with _autodoc_offset_lock:
        _autodoc_offset[base] = offset
    found = _autodoc_candidate(autodoc_path, f"{offset}/{rel}")
    if found and os.path.exists(found):
        yield found, (
            f"Documents live under {offset!r} inside the mount, but pointers do not "
            f"carry it: the Übernahme profile is rooted at a subdirectory of "
            f"KNOVAS_DOCUMENTS_PATH. Resolving there from now on. Point the profile "
            f"at the mount root, or set KNOVAS_DOCUMENTS_PATH to that subdirectory, "
            f"to make it exact."
        )


def _discover_autodoc_offset(base: str, rel: str) -> Optional[str]:
    """Find the subdirectory under the mount that ``rel`` hangs off, if any."""
    visited = 0
    queue: List[Tuple[str, int]] = [(base, 0)]
    while queue and visited < _AUTODOC_OFFSET_MAX_DIRS:
        current, depth = queue.pop(0)
        if depth >= _AUTODOC_OFFSET_MAX_DEPTH:
            continue
        try:
            with os.scandir(current) as entries:
                children = [e for e in entries if e.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        for entry in children:
            visited += 1
            if visited > _AUTODOC_OFFSET_MAX_DIRS:
                break
            if os.path.exists(os.path.join(entry.path, rel)):
                return os.path.relpath(entry.path, base).replace("\\", "/")
            queue.append((entry.path, depth + 1))
    return None


def _autodoc_candidate(autodoc_path: str, rel: str) -> Optional[str]:
    """One confined candidate path, or None if it escapes the AutoDoc root."""
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
    # Upper bound for any request body (JSON, CSV import). Without it a
    # client could stream an unbounded body into a worker's memory.
    app.config['MAX_CONTENT_LENGTH'] = config.get_int('web.max_request_bytes', 32 * 1024 * 1024)

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
            or path == '/experiments'
            or path.startswith('/experiments/')
            or path.endswith('.js')
            or path.endswith('.css')
            or path.startswith('/static/')
        ):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
        return response

    api_client = KnovasAPIClient(config)
    file_handler = AutoDocFileHandler()
    login_enabled = config.get_bool('web.login.enabled', True)
    web_app_title = str(config.get('web.app_title', 'Knovas Document Search') or 'Knovas Document Search')
    # Kurzform der Marke fuer die Titelzeile. Die Titel liefen auseinander
    # ("Login - Knovas Document Search" gegen "Cortex . Knovas Document
    # Search"); einheitlich ist "Knovas Cortex", "Knovas Suche" und so fort.
    web_brand = (str(config.get('web.brand', '') or '').strip()
                 or (web_app_title.split() or ['Knovas'])[0])
    cortex_enabled = config.get_bool('web.cortex_enabled', True)
    login_company_name = config.get('web.login.company_name', 'Knovas')
    login_username = str(config.get('web.login.username', '') or '')
    login_password = str(config.get('web.login.password', '') or '')
    login_configured = bool(login_username and login_password)

    # Per-user accounts (Pflichtenheft B1). When on, the shared company
    # credential is not merely unused — the app refuses to start with one
    # configured, so an upgrade cannot leave both doors open.
    # Default true, matching config/config.yaml: per-user accounts are the
    # product, and a deployment should not have to opt in to knowing who its
    # users are. A config that omits the key entirely still gets identity, so
    # the default is stated once rather than differing between the shipped
    # configuration and the code that reads it. The legacy shared-login path
    # is now an explicit `identity.enabled: false`.
    identity_enabled = config.get_bool('identity.enabled', True)
    identity_gate = None
    rc_client = None
    if identity_enabled:
        from identity.webauth import IdentityGate

        if login_configured:
            raise RuntimeError(
                'identity.enabled is true, but COMPANY_LOGIN_NAME/COMPANY_LOGIN_PASSWORD '
                'are still set. The shared company login is superseded by per-user '
                'accounts; remove both values before enabling identity.'
            )
        identity_gate = IdentityGate()
        app.teardown_request(identity_gate.close)

        # Schema and first administrator, before anything serves a request.
        # Gunicorn loads this module in every worker, so prepare_identity takes
        # an advisory lock and is idempotent; a restart is a no-op. Without it
        # a fresh deployment comes up with no tables and nobody who can sign
        # in, which looks like a broken login rather than a missing step.
        from identity import db as identity_db
        from identity.startup import DEFAULT_SECRET_PATH, prepare_identity

        boot_conn = identity_db.connect()
        try:
            prepare_identity(
                boot_conn,
                email=os.environ.get('PLATFORM_ADMIN_EMAIL', ''),
                password=os.environ.get('PLATFORM_ADMIN_PASSWORD') or None,
                secret_path=os.environ.get(
                    'PLATFORM_ADMIN_BOOTSTRAP_PATH', DEFAULT_SECRET_PATH
                ),
            )
        finally:
            boot_conn.close()

        # The broker signs the signed-in person into every Knovas call, through
        # the one client the search path already uses. Both preconditions fail
        # closed at startup: an unsigned call returns MORE than a signed one.
        from pathlib import Path as _Path

        from identity.broker_key import BrokerKeyUnavailableError, load_or_create_signer
        from identity.principal import PrincipalBroker

        broker_key_dir = str(config.get('identity.broker_key_dir', '') or '').strip()
        if not broker_key_dir:
            raise RuntimeError(
                'identity.enabled is true but identity.broker_key_dir is not set. '
                'The Platform signs each user into its Knovas calls with an Ed25519 '
                'key kept in that directory; see docs/certificates.md.'
            )
        if not getattr(api_client, 'customer_id', ''):
            raise RuntimeError(
                'identity.enabled is true but api.customer_id (SEMANTIX_CUSTOMER_ID) '
                'is empty. A principal assertion is bound to the tenant.'
            )
        try:
            broker_signer = load_or_create_signer(_Path(broker_key_dir))
        except BrokerKeyUnavailableError as exc:
            raise RuntimeError(f'Broker signing key unavailable: {exc}') from exc

        class _RequestScopedBroker:
            """PrincipalBroker bound to whoever is signed in on *this* request.

            gate.users() is a repository on the request's own connection and
            the broker reads user_access_groups at mint time, uncached -- so a
            revocation lands on the user's next request, not at session expiry.
            """

            def __init__(self, gate, signer, tenant_id):
                self._gate, self._signer, self._tenant_id = gate, signer, tenant_id

            def current_user(self):
                return self._gate.current_user()

            def assertion_for(self, user):
                return PrincipalBroker(
                    user_repo=self._gate.users(), signer=self._signer,
                    tenant_id=self._tenant_id,
                ).assertion_for(user)

        principal_broker = _RequestScopedBroker(identity_gate, broker_signer, str(api_client.customer_id))
        api_client.attach_principal_broker(principal_broker)

        from knovas_connector_client import KnovasConnectorClient

        rc_client = KnovasConnectorClient(
            str(config.get('knovas_connector.base_url', 'http://knovas-connector:5001')),
            principal_broker=principal_broker,
        )

    # Experimente (docs/superpowers/plans/2026-09-28-experiments-module.md).
    # Off unless EXPERIMENTS_ENABLED, and never without per-user identity: the
    # module is built on roles. The search integration exists either way,
    # because experiment hits are stripped from every search -- switching the
    # module off must not put the documents it wrote to Knovas on everyone's
    # result list.
    from experiments.search import SearchIntegration
    from experiments.settings import load_settings as load_experiments_settings

    experiments_settings = load_experiments_settings(config, identity_enabled=identity_enabled)
    if not identity_enabled and load_experiments_settings(config).enabled:
        logger.warning(
            'EXPERIMENTS_ENABLED is on but identity.enabled is false; Experimente needs '
            'per-user accounts and stays off.'
        )
    experiments_search = SearchIntegration(
        settings=experiments_settings,
        connection=identity_gate.connection if identity_gate is not None else (lambda: None),
        current_user=identity_gate.current_user if identity_gate is not None else (lambda: None),
        enabled=experiments_settings.enabled,
    )
    app.extensions['experiments'] = {
        'settings': experiments_settings,
        'search': experiments_search,
        'index_client': None,
        'runner': None,
        'workers': [],
    }

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
    # Documents in OneDrive/SharePoint: nothing on this server to open,
    # download or render. Every hit opens and previews in Microsoft 365.
    m365_mode = str(config.get('documents.source', 'files') or 'files').strip().lower() == 'm365'
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
    # Same shape and the same reason as the open-token store above: several
    # gunicorn workers, and a grant written while serving the search has to be
    # visible to the worker that serves the thumbnail.
    document_grant_store_path = str(open_section.get('grant_store_path') or '').strip()
    if not document_grant_store_path:
        document_grant_store_path = os.path.join(
            os.path.dirname(open_token_store_path) or '/app/data',
            'document_grants.sqlite3',
        )
    document_grants = DocumentGrantStore(
        document_grant_store_path,
        ttl_seconds=config.get_int('open.grant_ttl_seconds', GRANT_TTL_DEFAULT),
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

    def _download_open_enabled() -> bool:
        """Whether the browser may be offered the file itself.

        ``Öffnen`` starts the document on the *user's* PC, so it needs a path
        that PC can reach. A deployment whose documents live only on this
        server has none, and then the download is not a degraded extra -- it is
        the only route to the document, and without it every Öffnen ends in an
        error message.

        So: on when the operator asked for it, and on when there is no client
        path to offer instead -- unless they set the variable to false
        themselves, which is a decision and is respected. A bare default false
        is not, because nobody chose it for this deployment. Reading the
        environment rather than the config value is what tells those apart:
        config.yaml always carries the key with a default interpolated in.
        """
        if m365_mode:
            # The file is in OneDrive/SharePoint, not here: a download button
            # would only ever answer 404. OneDrive's own viewer downloads.
            return False
        if allow_degraded_download_open:
            return True
        chosen = os.getenv('OPEN_ALLOW_DEGRADED_DOWNLOAD_OPEN')
        return chosen is None and not _open_mapping_configured()

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
    def reject_client_asserted_groups():
        """The group list has exactly one source: user_access_groups, read
        server-side for the signed-in user. A body that supplies its own is
        refused with 400, not quietly overruled -- silently dropping it would
        let a caller believe a scope applied, and would hide a merging bug."""
        if not request.is_json:
            return None
        from identity.principal import ClientAssertedGroupsError, PrincipalBroker

        try:
            body = request.get_json(silent=True)
        except RecursionError:
            # silent=True swallows ValueError only. A body nested deeper than
            # the parser's recursion limit is just as unreadable, and it is
            # the caller's fault, not a 500 with a traceback in the log.
            logger.info("Refused a JSON body nested too deeply: %s %s",
                        request.method, request.path)
            return jsonify({'success': False,
                            'error': 'Die Anfrage ist kein g\u00fcltiges JSON.'}), 400
        try:
            PrincipalBroker.reject_client_assertion(body)
        except ClientAssertedGroupsError as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400
        return None

    @app.before_request
    def refuse_experiments_when_switched_off():
        """One gate over every Experimente path while the module is off.

        Registered before the login gate, so the answer is the same for
        everyone: the pages go back to the search, the API (the machine API
        for CI included) says the module is off rather than asking a CI job
        to sign in. The blueprints are not even registered then; this gate
        makes a kept bookmark land somewhere sensible instead of on a 404.
        """
        if experiments_settings.enabled:
            return None
        path = request.path or ''
        if path == '/api/experiments' or path.startswith('/api/experiments/'):
            return jsonify({'success': False,
                            'error': 'Experimente sind nicht eingeschaltet.'}), 404
        if path == '/experiments' or path.startswith('/experiments/'):
            return redirect(url_for('index'))
        return None

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
        # The Experimente machine API (CI) authenticates every request with a
        # bearer token and never reads the session cookie, so there is no
        # ambient credential for a forged request to ride on.
        if request.endpoint.startswith('experiments_api.'):
            return None
        csrf_header = str(request.headers.get('X-CSRF-Token', '') or '')
        if not _csrf_token_is_valid(csrf_header):
            return jsonify({'success': False, 'error': 'CSRF token invalid or missing'}), 403
        return None

    # Cache of (subject, pointer) -> readable, so a results page of thumbnails
    # does not become one Knovas round trip per tile. The TTL is deliberately
    # shorter than the 120 s principal-assertion lifetime, so it cannot widen
    # the staleness bound the assertion already sets: a person whose access is
    # revoked loses these documents within one assertion lifetime, as before.
    @app.before_request
    def require_readable_document():
        """The wall, on the routes that hand over a file rather than search it.

        Retrieval is filtered by Knovas, so search never lists a document the
        signed-in person is walled out of. These routes are the other way in:
        they take a pointer and a path and read the file off the Platform's own
        disk, which is why they have to ask.

        They ask ``document_grants`` — "did this person's own retrieval return
        this pointer?" — and not the Secure API. The API route this used to
        call, ``GET /secured/document_readable``, does not exist in any
        deployed version, so every call 404'd, this guard failed closed exactly
        as written, and every document answered "Not found" for every user
        while search went on working. See ``document_grants`` for what the
        capability does and does not promise; the short version is that it
        never grants more than retrieval did, so it tracks the backend instead
        of second-guessing it.

        Written as one gate over ``doc_id`` rather than a check inside each
        handler, so a content route added later is covered by default instead
        of by remembering. Denial is **404**, never 403: a 403 would confirm
        that the matter exists, which is the trace an ethical wall forbids.

        With ``identity.enabled`` off there is no authenticated subject to hold
        a grant, so the gate stands aside and the legacy shared-login
        deployment behaves exactly as before.
        """
        if identity_gate is None:
            return None
        doc_id = (request.view_args or {}).get('doc_id')
        if not doc_id:
            return None
        # Experiment documents are Knovas copies of database rows, not files:
        # they are never served from disk, whatever a grant says.
        if str(doc_id).strip().lstrip('/').startswith(experiments_settings.pointer_prefix + '/'):
            return jsonify({'success': False, 'error': 'Not found'}), 404
        user = identity_gate.current_user()
        if user is None:
            return None  # the login gate above already refused this request

        # The path is supplied separately from the pointer, so a caller could
        # otherwise name a document they may read and ask for the bytes of one
        # they may not. Serve a path only when it is the one this pointer names.
        supplied = request.args.get('path')
        if supplied is None and request.method == 'POST':
            supplied = (request.get_json(silent=True) or {}).get('path')
        if supplied:
            # Compare the *resolved* files, not the strings. Callers legitimately
            # spell a path either way -- the raw Knovas pointer or the mapped
            # relative path -- and both reach the same file through
            # _resolve_autodoc_path. Resolving both sides accepts every spelling
            # of the authorised document and no spelling of a different one.
            given = _resolve_autodoc_path(str(supplied))
            wanted = _resolve_autodoc_path(_rel_path_for_autodoc(str(doc_id)))
            if given is None or wanted is None or given != wanted:
                # The route's endpoint name, never request.path: the path is
                # the pointer, and pointers name clients and matters.
                logger.warning(
                    "Refusing %s: the supplied path does not belong to the pointer",
                    request.endpoint,
                )
                return jsonify({'success': False, 'error': 'Not found'}), 404

        if not _readable_for_current_user(str(doc_id)):
            logger.info(
                "Refusing %s: no live grant for this document. It was not in "
                "this person's search or listing results, or the grant has aged out.",
                request.endpoint,
            )
            return jsonify({'success': False, 'error': 'Not found'}), 404
        return None

    def _readable_for_current_user(doc_id: str) -> bool:
        """Whether the signed-in person's own search or listing returned this
        document, within the grant TTL (``document_grants``).

        The check the file routes run before handing over bytes, and the one
        the document-fields routes run before asking Knovas about a pointer.
        Both spellings count: search and listing grant the raw Knovas pointer
        and the path under the mount, and a caller may name either.

        With ``identity.enabled`` off there is no subject to hold a grant, so
        this stands aside (True), exactly as the file-route gate always has.
        Nobody signed in is False. Logs nothing: the callers decide what a
        refusal is worth saying, and a pointer never goes into a new log line.
        """
        if identity_gate is None:
            return True
        user = identity_gate.current_user()
        if user is None:
            return False
        return document_grants.granted(
            str(user.id), str(doc_id), _rel_path_for_autodoc(str(doc_id))
        )

    def _grant_for_current_user(rows: List[Dict[str, Any]]) -> None:
        """Record what retrieval -- a search or a listing -- handed this
        person, so the file routes serve those documents and only those.

        Every spelling a row carries is granted: the raw Knovas pointer and
        its path under the mount. Without identity there is nobody to hold a
        grant, and nothing is recorded.
        """
        if identity_gate is None:
            return
        searcher = identity_gate.current_user()
        if searcher is None:
            return
        spellings: List[str] = []
        for result in rows or ():
            for field in ('doc_id', 'path', 'pointer'):
                raw = result.get(field)
                if raw:
                    spellings.append(str(raw))
                    spellings.append(_rel_path_for_autodoc(str(raw)))
        document_grants.grant(str(searcher.id), spellings)

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

    def _console_url():
        """Link zur Verwaltung -- je nach Rolle, sonst None.

        Der Link ist Darstellung; ``require_admin``/``require_approver`` auf
        der jeweiligen Route bleibt die Kontrolle (REQ-A1/REQ-A2). Ein
        Administrator landet auf Personen, ein reiner Freigeber (Rolle
        'approver' ohne 'admin') auf Freigaben -- sonst gibt es keinen Link.
        Faellt die Identitaetsdatenbank aus, verschwindet der Link, statt dass
        die Suchseite bricht.
        """
        if identity_gate is None:
            return None
        try:
            user = identity_gate.current_user()
        except Exception as exc:  # noqa: BLE001 - die Leiste darf nie 500en
            logger.warning('Verwaltungslink nicht ermittelbar: %s', exc)
            return None
        if user is None:
            logger.info('Verwaltungslink: keine angemeldete Person in dieser Anfrage')
            return None
        roles = getattr(user, 'roles', None) or ()
        if 'admin' in roles:
            return url_for('admin.people')
        if 'approver' in roles:
            return url_for('admin.approvals')
        # Not drawing the link is indistinguishable from a broken menu from the
        # outside, so say whose roles were read and what they were.
        logger.info('Verwaltungslink ausgeblendet: %s hat die Rollen %s',
                    getattr(user, 'email', '?'), sorted(roles) or ['-'])
        return None

    def _experiments_nav() -> bool:
        """Whether the sidebar offers Experimente: module on and the signed-in
        person holds a viewing role. Presentation only -- the routes refuse on
        their own -- so a failure hides the item instead of breaking the page."""
        if not experiments_settings.enabled or identity_gate is None:
            return False
        try:
            from experiments.permissions import can_view

            return can_view(identity_gate.current_user())
        except Exception as exc:  # noqa: BLE001 - die Leiste darf nie 500en
            logger.warning('Experimente-Eintrag nicht ermittelbar: %s', exc)
            return False

    def _sidebar_context() -> Dict[str, Any]:
        """Gemeinsame Werte der Plattform-Leiste."""
        return {
            'company_name': login_company_name,
            'feedback_url': feedback_url,
            'console_url': _console_url(),
            'cortex_enabled': cortex_enabled,
            'experiments_nav': _experiments_nav(),
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
            allow_degraded_download_open=_download_open_enabled(),
            open_mapping_configured=_open_mapping_configured(),
            pdf_inline_in_browser=pdf_inline_in_browser,
            onedrive_enrichment_loaded=bool(_unique_enrichment_records()),
            m365_mode=m365_mode,
            results_per_page=config.get_int('web.search.results_per_page', 20),
            asset_version=_static_asset_version(),
            build_id=DOCBRIDGE_BUILD_ID,
        )

    @app.before_request
    def refuse_cortex_when_switched_off():
        """One gate over every Cortex route, page and API alike.

        Hiding the navigation link is not switching a feature off: the page is
        still there for anyone who kept the URL, and its API still answers. So
        the switch is enforced here, once, rather than remembered at each of
        the eleven /api/ontology routes.
        """
        if cortex_enabled:
            return None
        path = request.path or ''
        if (path == '/ontology' or path.startswith('/api/ontology')
                or path.startswith('/api/graph')):
            if path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Cortex ist deaktiviert.'}), 404
            return redirect(url_for('index'))
        return None

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
            graph_mode=_ontology_source_is_graph() and identity_gate is not None,
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
            # The console talks to Knovas through the same client the search
            # path uses, so mTLS material, retries and rate limiting are
            # configured in exactly one place.
            client_factory=lambda: api_client,
            rc_client_factory=(lambda: rc_client) if rc_client is not None else None,
            page_context=lambda: {
                **_sidebar_context(),
                'app_title': web_app_title,
                'brand': web_brand,
                'asset_version': _static_asset_version(),
                'ingestion_enabled': rc_client is not None,
            },
        ))

        from identity.directories import DirectoryStore
        from identity.node_grants import NodeGrantStore
        from web_interface.graph_routes import create_graph_blueprint

        # The grant store is built per request: identity_gate.connection() is
        # request-scoped and teardown_request closes it, so a store made once
        # here would hold a connection the first request already closed.
        app.register_blueprint(create_graph_blueprint(
            identity_gate,
            lambda: NodeGrantStore(identity_gate.connection()),
            lambda: api_client,
            # _ontology_source_is_graph is defined further down create_app;
            # naming it here rather than calling it through a lambda would read
            # it before it is bound.
            graph_mode=lambda: _ontology_source_is_graph(),
            topology=lambda: _ontology_source(),
            directories=lambda: DirectoryStore(identity_gate.connection()),
        ))

        if experiments_settings.enabled:
            from web_interface.experiments_routes import install_experiments

            app.extensions['experiments'].update(install_experiments(
                app,
                config=config,
                settings=experiments_settings,
                gate=identity_gate,
                # The experiments search asks Knovas as the signed-in person,
                # through the same principal-signed client the search uses.
                api_client=api_client,
                csrf_token=_ensure_csrf_token,
                page_context=lambda: {
                    **_sidebar_context(),
                    'app_title': web_app_title,
                    'brand': web_brand,
                    'asset_version': _static_asset_version(),
                },
            ))

    def _apply_open_hints(rows: List[Dict[str, Any]]) -> None:
        """How the browser may open each row's file: client path, UNC, companion.

        Not "and not enrichment_loaded": a mirrored OneDrive corpus has an
        enrichment file and still holds documents with no webUrl, which are
        opened locally like any other. Rows that do resolve a URL have these
        hints removed again by _apply_onedrive_links.
        """
        for result in rows:
            fp = (result.get('path') or '').strip()
            if fp and result.get('can_open') and not result.get('external_url'):
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

    def _apply_onedrive_links(rows: List[Dict[str, Any]]) -> None:
        """Only a document that actually resolved to a webUrl can be opened in
        OneDrive. Marking every hit available because the deployment *has* an
        enrichment file put an "In OneDrive oeffnen" on mirrored-but-unlinked
        documents, where it 404s, and hid the local Oeffnen that would have
        worked."""
        for result in rows:
            if not result.get('external_url'):
                url = _resolve_onedrive_url(
                    str(result.get('doc_id') or ''),
                    str(result.get('path') or result.get('doc_id') or ''),
                    config,
                )
                if url:
                    _apply_external_open_mode(result, url)
            result['onedrive_open_available'] = bool(result.get('external_url'))

    def _enhance_listing_rows(payload: Dict[str, Any]) -> Dict[str, Any]:
        """A listing page's rows, enriched exactly like search rows (disk
        metadata, context sidecar, open hints, OneDrive links), so a listed
        document previews and opens the way a found one does."""
        enhanced = _enhance_search_results(payload, file_handler, config, query='')
        rows = enhanced.get('results') or []
        _apply_open_hints(rows)
        if _unique_enrichment_records():
            _apply_onedrive_links(rows)
        return enhanced

    doc_fields_routes.attach(
        app,
        config=config,
        client_factory=lambda: api_client,
        identity_gate=identity_gate,
        grant=_grant_for_current_user,
        grant_check=_readable_for_current_user,
        enhance=_enhance_listing_rows,
        split=experiments_search.split,
    )

    @app.route('/api/search', methods=['POST'])
    def search():
        """
        Search documents via Knovas API.
        
        Request JSON:
            {
                "query": "search query",
                "limit": 20,
                "filters": {},
                "where": {"doc_type": "invoice"}     (optional; document fields)
            }
        
        Returns:
            JSON with search results, plus ``document_fields`` (capability,
            filter_state, fields_unavailable, resolved) and ``honesty``
            (no_strong_matches, no_results_reason, relevance_gate_applied,
            degraded_to_bm25; null where Knovas does not say) and ``notices``
            (spec F3: kinds, visible names and counts, never node ids).

        Document fields (spec 4.3, H1-H3): ``where`` goes to Knovas only when
        the tenant's capability is ``filters``, and its results are shown only
        when Knovas echoed ``where.applied``; otherwise the answer is a 409
        without results. A filtered search is never retried unfiltered, and
        the client-side score thresholds and the filename supplement are
        skipped for it -- they would add or drop rows the filter did not
        decide. ``return_fields`` (typed values on the cards) goes out under
        ``filters`` and ``listing_only``.
        """
        plan = None
        try:
            data = request.get_json()
            
            if not data or 'query' not in data:
                return jsonify({'error': 'Query parameter required'}), 400
            
            query = data['query']
            # main's tolerant parser (None/bool/inf -> default), capped at 50:
            # /secured/query answers 422 above that (doc-fields).
            limit = min(_SEARCH_LIMIT_MAX, _search_page_limit(
                data.get('limit'), config.get_int('web.search.results_per_page', 20)))
            filters = data.get('filters', {}) or {}
            where = None
            if data.get('where') is not None:
                try:
                    where = doc_fields_view.validate_where(data.get('where'))
                except ValueError:
                    return jsonify({
                        'success': False, 'error_code': 'filter_invalid', 'code': 'where_invalid',
                        'error': doc_fields_routes.FILTER_SHAPE_INVALID,
                    }), 400

            # Lengths and counts only: the question and the filter values
            # carry client names (spec D6).
            logger.info("Search request: query_len=%d, limit=%d, where_keys_count=%d",
                        len(str(query or '')), limit, len(where or {}))

            min_qlen = config.get_int('web.search.min_query_length', 2)
            qstrip = (query or '').strip()
            if min_qlen > 1 and len(qstrip) < min_qlen:
                return jsonify({
                    'success': False,
                    'error': f'Suchbegriff muss mindestens {min_qlen} Zeichen haben.',
                }), 400

            use_test_results = _search_use_test_results()
            plan = doc_fields_routes.search_plan(
                api_client, where, doc_fields_routes.user_key_for(identity_gate),
                enabled=not use_test_results,
            )
            if plan.refusal is not None:
                body, status = plan.refusal
                return jsonify(body), status
            if use_test_results:
                logger.info("Search using local test fixtures (SEARCH_USE_TEST_RESULTS=true)")

                def ask(n: int) -> Dict[str, Any]:
                    return _build_test_search_results(query=query, limit=n)
            else:
                # exact_match is decided here, after Knovas answers. /secured/query
                # reads no filters at all (the client leaves them out, spec F7);
                # only the legacy GET forwards them, as query parameters, so a
                # local-only key stays here.
                knovas_filters = {k: v for k, v in (filters or {}).items()
                                  if k not in _LOCAL_ONLY_FILTERS}

                def ask(n: int) -> Dict[str, Any]:
                    # Document fields: the plan's where/return_fields go out
                    # with every question, the wider second one included
                    # (a filtered search is never asked unfiltered, H3).
                    return doc_fields_routes.run_search(
                        api_client, plan, query=query, limit=n, filters=knovas_filters)

            # Experiment documents (Experimente) are taken out for everyone,
            # with the module on or off, pointers in the semantix block
            # included. They come back further down as experiment rows, and
            # only for people allowed to see them. Knovas is asked for more
            # than the page, so the places they held go to the documents
            # ranked below them rather than being lost; the documents are cut
            # back to the page here, before anything is granted. The local
            # fixtures hold no experiment documents and are asked as they are.
            results, experiment_hits, has_more = _fetch_search_page(
                ask, experiments_search.split, limit, over_fetch=not use_test_results)

            semantix_meta = results.get('semantix')
            if not isinstance(semantix_meta, dict):
                semantix_meta = {}
            filter_state = doc_fields_view.filter_state(plan.where, semantix_meta)
            if filter_state == 'not_applied':
                # H2: Knovas answered, but did not say the filter applied.
                # Showing these rows as filtered would be the one lie this
                # feature must not tell, so none are shown.
                body, status = doc_fields_routes.filter_not_applied()
                return jsonify(body), status
            filtered = plan.where is not None

            is_test_data = use_test_results or (
                isinstance(results.get('semantix'), dict)
                and results['semantix'].get('status') == 'test_data'
            )

            enhanced_results = _enhance_search_results(results, file_handler, config, query)
            enrichment_loaded = bool(_unique_enrichment_records())
            _apply_open_hints(enhanced_results.get('results') or [])
            if is_test_data:
                _apply_test_open_hints(
                    enhanced_results.get('results') or [],
                    browser_client_open_enabled,
                    companion_enabled,
                    companion_uri_scheme,
                )
            # H3: under a filter, the client score thresholds and the filename
            # supplement stay out -- the rows are the ones the filter decided.
            # Without one, both run as always, even when Knovas reports its
            # own relevance gate (that flag is independent of document fields).
            refined = _apply_search_refinement(
                enhanced_results, query, filters, config, thresholds=not filtered)

            final_results = refined.get('results', [])
            if not filtered:
                final_results = _supplement_results_from_enrichment_filenames(
                    query, final_results, filters, config, limit=limit
                )
            if config.get_bool('web.search.log_similarity_scores', True):
                _log_search_similarity_debug(query, final_results)

            if enrichment_loaded:
                _apply_onedrive_links(final_results)
            doc_fields_routes.decorate_rows(final_results, plan.registry)

            # Retrieval has decided; record what it handed this person so the
            # file routes can serve those documents and only those. Search and
            # the listing are the only routes that give the browser document
            # pointers, so they are the only places a grant is created.
            _grant_for_current_user(final_results)

            # Experiment rows, rendered from the Platform database. Merged
            # after the grants loop, so an experiment pointer never becomes a
            # file grant, and after the open hints: they link to the
            # experiment page, never to a file.
            experiment_rows = experiments_search.rows(experiment_hits)
            if experiment_rows:
                experiment_rows = _apply_search_refinement(
                    {'results': experiment_rows}, query, filters, config)['results']
                if len(final_results) + len(experiment_rows) > limit:
                    has_more = True
                final_results = _merge_experiment_rows(final_results, experiment_rows, limit)

            # Whether the words the person typed actually occur in anything we
            # are about to show them. A vector search answers "related to", and
            # for a person's name that is often nothing of the sort -- the
            # honest thing is to say so rather than let the list look like a
            # failure of the product.
            literal_hits = sum(
                1 for r in final_results
                if any(loc.get('literal') for loc in (r.get('match_locations') or []))
                or (
                    context_query_terms(query, config.get_int(
                        'web.search.strict_match_min_term_length', 2))
                    and all(
                        term in _search_result_haystack(r)
                        for term in context_query_terms(query, config.get_int(
                            'web.search.strict_match_min_term_length', 2))
                    )
                )
            )

            payload: Dict[str, Any] = {
                'success': True,
                'query': query,
                'results': final_results,
                'literal_query_matches': literal_hits,
                # Womit ein Wort im Dokument beginnen muss, um markiert zu
                # werden. Die Oberflaeche kann nicht stemmen: ohne diese Liste
                # bliebe "Abrechnung" bei der Suche nach "abgerechnet"
                # unmarkiert, obwohl der Satz als Fundstelle gilt.
                'highlight_prefixes': german_text.highlight_prefixes(
                    context_query_terms(query, config.get_int(
                        'web.search.strict_match_min_term_length', 2))
                ),
                'total': len(final_results),
                # Whether a larger limit could show more. Not the same as
                # "the page is full": experiment hits and the refinement take
                # rows out after Knovas answered.
                'has_more': bool(has_more),
                'timestamp': datetime.now().isoformat(),
                'onedrive_enrichment_loaded': enrichment_loaded,
                'location_summary': _build_location_summary(final_results),
                # Additive only: a server or a tenant without document fields
                # gets capability "off", filter_state "none", and null honesty
                # values -- nothing else in this answer changes.
                'document_fields': doc_fields_routes.document_fields_block(
                    plan, filter_state, semantix_meta.get('where'),
                    doc_fields_routes.current_capability(plan.capability)),
                'honesty': doc_fields_routes.honesty_block(semantix_meta),
                # What Knovas said about this answer, shown above the results
                # (spec F3): kinds, names the person may see, counts.
                'notices': doc_fields_routes.search_notices(
                    api_client, plan, semantix_meta,
                    doc_fields_routes.user_key_for(identity_gate)),
            }
            if 'semantix' in refined and isinstance(refined.get('semantix'), dict):
                # Knovas's auto_scope stays on the server: its node ids may
                # name nodes this person may not see (spec F3).
                payload['semantix'] = {k: v for k, v in refined['semantix'].items()
                                       if k != 'auto_scope'}
            if config.get_bool('web.search.expose_similarity_scores_in_json', False):
                payload['similarity_debug'] = _build_similarity_debug(final_results)

            return jsonify(payload)

        except QueryRejected as e:
            # Only a request that carried ``where`` gets here: one with just
            # return_fields was retried without them in run_search. Never
            # retried unfiltered (H3); mapped to what the browser can show.
            logger.info("Knovas refused the filtered search (%s %s)", e.status, e.error_code)
            body, status = doc_fields_routes.filter_refusal(
                e, plan.registry if plan is not None else None)
            return jsonify(body), status
        except requests.exceptions.HTTPError as e:
            # A failure at api.knovas.ch is not a failure of this deployment,
            # and saying "Interner Serverfehler" for it sends an operator to
            # debug their own stack for someone else's outage. 502, because
            # this app is a gateway here and the upstream is what broke.
            status = getattr(getattr(e, 'response', None), 'status_code', None)
            logger.error("Knovas API refused the search (HTTP %s): %s", status, e, exc_info=True)
            return jsonify({
                'success': False,
                'error': (
                    f'Die Knovas-API hat die Suche mit HTTP {status} abgelehnt. '
                    'Das ist ein Fehler der API, nicht dieser Installation. '
                    'Details stehen im Log von docbridge-web.'
                ),
                'upstream_status': status,
            }), 502
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

    @app.route('/api/document/<path:doc_id>/preview-anchors', methods=['GET'])
    def preview_anchors(doc_id: str):
        """Wo die Trefferstellen im PDF stehen: Seite und Hoehe je Satznummer.

        Der browsereigene Viewer springt ueber ``#page=N&view=FitH,<top>``. Ohne
        die Hoehe bleibt nur die Seite, und mehrere Fundstellen auf einer Seite
        -- der Normalfall -- fuehren dann alle auf dieselbe Adresse: ein Klick
        bewirkt sichtbar nichts.
        """
        try:
            file_path = request.args.get('path')
            if not file_path:
                return jsonify({'error': 'Document path required'}), 400
            full_path = _resolve_autodoc_path(file_path)
            if not full_path:
                return jsonify({'anchors': {}}), 200
            numbers: List[int] = []
            for raw in str(request.args.get('s') or '').split(','):
                raw = raw.strip()
                if raw.lstrip('-').isdigit():
                    numbers.append(int(raw))
            if not numbers:
                return jsonify({'anchors': {}}), 200
            passage_map = sentences_by_number_map(
                load_context(
                    _context_store_path_from_config(config), [str(doc_id), file_path]
                ),
                numbers[:8],
            )
            return jsonify({'anchors': passage_anchors(full_path, passage_map)}), 200
        except Exception as exc:  # noqa: BLE001
            # Springen ist Beiwerk. Ein Fehler hier darf die Vorschau nicht
            # kosten -- ohne Anker bleibt es beim Sprung auf die Seite.
            logger.warning("Preview anchors unavailable for %s: %s", doc_id, exc)
            return jsonify({'anchors': {}}), 200

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
            # Die Fundstellen im Dokument selbst markieren. Der browsereigene
            # Viewer hebt nichts hervor, was man ihm sagt -- aber er zeigt
            # Anmerkungen an, die im Dokument stehen. Nur der ausgelieferte
            # Datenstrom traegt sie; /download bleibt das Original.
            terms = context_query_terms(
                request.args.get('q') or '',
                config.get_int('web.search.strict_match_min_term_length', 2),
            )
            # s= sind die Satznummern der Trefferstellen. Ihr Text steht im
            # Sidecar, nicht in der Adresszeile: ein rein semantischer Treffer
            # enthält die gesuchten Wörter gar nicht, und ohne die Stelle selbst
            # wäre an ihm nichts markiert.
            numbers: List[int] = []
            for raw in str(request.args.get('s') or '').split(','):
                raw = raw.strip()
                if raw.lstrip('-').isdigit():
                    numbers.append(int(raw))
            passage_map = sentences_by_number_map(
                load_context(
                    _context_store_path_from_config(config), [str(doc_id), file_path]
                ),
                numbers[:8],
            ) if numbers else {}
            passages = list(passage_map.values())
            # a= ist die SATZNUMMER der angeklickten Stelle, nicht ihre Position
            # in s=: der Sidecar kennt nicht jeden Satz, die Liste hat dann
            # Luecken, und ueber die Position markierte ein Klick auf die dritte
            # Fundstelle die zweite.
            active = ''
            raw_active = str(request.args.get('a') or '').strip()
            if raw_active.lstrip('-').isdigit():
                active = passage_map.get(int(raw_active), '')
            if terms or passages:
                marked = highlight_pdf(
                    full_path, terms, passages=passages, active=active
                )
                if marked:
                    return send_file(
                        io.BytesIO(marked),
                        mimetype='application/pdf',
                        as_attachment=False,
                        download_name=os.path.basename(full_path),
                    )
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

        if not os.path.exists(full_path):
            # Die Datei ist weg -- der Text nicht. Der Kontext-Sidecar hält jeden
            # Satz, der beim Indexieren gelesen wurde, und das ist immer noch das
            # Dokument. Ein Treffer, den man nur anschauen und nicht lesen kann,
            # ist für die Anwältin keiner; das passiert bei jedem umbenannten
            # oder neu erzeugten Korpus, dessen alte Einträge noch im Index
            # stehen. Gilt auch für PDF: dort gibt es keine Seiten mehr zu
            # rendern, aber lesen kann man es.
            indexed = indexed_text(
                load_context(_context_store_path_from_config(config), [str(doc_id), file_path])
            )
            if indexed:
                logger.info(
                    "Serving %s from the search index: the file is not on the mount",
                    file_path,
                )
                return jsonify({
                    'success': True,
                    'doc_id': doc_id,
                    'kind': kind or 'txt',
                    'markdown': indexed,
                    'meta': {},
                    'warnings': [],
                    'from_index': True,
                })
            return jsonify({'success': False, 'error': 'Document file not found'}), 404

        if kind is None or kind == 'pdf':
            return jsonify({'success': False, 'error': 'Preview not supported for this format'}), 415

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
            # No share means there is no path this PC could open -- but the
            # bytes are right here, so refusing outright leaves the person with
            # no way to the document at all. Name the way out; the browser
            # downloads instead. Deployments that want the refusal to stand can
            # set OPEN_ALLOW_DEGRADED_DOWNLOAD_OPEN=false.
            return jsonify({
                'success': False,
                'error': (
                    'Für dieses Dokument ist kein Pfad auf Ihrem PC hinterlegt '
                    '(keine Freigabe konfiguriert).'
                ),
                'fallback': 'download' if _download_open_enabled() else None,
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

    def _rc_m365_preview(doc_id: str, page: Optional[int]) -> Dict[str, Any]:
        """Ask Knovas Connector -- which holds the Microsoft 365 credentials,
        this internet-facing app does not -- for Microsoft's viewer URL.

        Signed as the person looking when per-user identity is on; with the
        shared login there is nobody to sign as, and Knovas Connector accepts
        the call from the stack's own network, as it does the console's.
        """
        if rc_client is not None:
            return rc_client.m365_preview(doc_id, page)
        base = str(config.get('knovas_connector.base_url', 'http://knovas-connector:5001')).rstrip('/')
        body: Dict[str, Any] = {'doc_id': doc_id}
        if page:
            body['page'] = page
        from knovas_connector_client import KnovasConnectorError

        resp = requests.post(f'{base}/m365/preview', json=body, timeout=8)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise KnovasConnectorError(
                str((data or {}).get('error') or f'HTTP {resp.status_code}'), status=resp.status_code,
            )
        return data

    @app.route('/api/document/<path:doc_id>/m365-preview', methods=['GET'])
    def document_m365_preview(doc_id: str):
        """Microsoft 365's own viewer for this document, embeddable in the dialog.

        Guarded like every document route (``require_readable_document`` sees
        ``doc_id``): only what this person's search returned. The URL is
        short-lived and fetched per opening, never cached or shared.
        """
        if not m365_mode:
            return jsonify({'success': False, 'error': 'Not found'}), 404
        try:
            page = int(request.args.get('page') or 0) or None
        except (TypeError, ValueError):
            page = None
        try:
            # Exactly the identifier the gate above granted -- no lookup that
            # could land on another file of the same name. Knovas Connector
            # answers only for identifiers it published itself.
            data = _rc_m365_preview(str(doc_id), page)
        except Exception as exc:  # noqa: BLE001 - every failure means "show the indexed text"
            if getattr(exc, 'status', None) == 404:
                return jsonify({'success': False, 'error': 'Kein Microsoft-365-Dokument.'}), 404
            logger.warning('Microsoft 365 preview for %r failed: %s', doc_id, exc)
            return jsonify({'success': False, 'error': 'Vorschau von Microsoft 365 nicht erreichbar.'}), 502
        body: Dict[str, Any] = {'success': True}
        for src, dst in (('getUrl', 'embed_url'), ('postUrl', 'post_url'), ('postParameters', 'post_params')):
            value = str((data or {}).get(src) or '')
            if value:
                body[dst] = value
        if body.get('embed_url') and not _is_safe_http_url(body['embed_url']):
            body.pop('embed_url')
        if body.get('post_url') and not _is_safe_http_url(body['post_url']):
            body.pop('post_url')
            body.pop('post_params', None)
        if not (body.get('embed_url') or body.get('post_url')):
            return jsonify({'success': False, 'error': 'Keine Vorschau f\u00fcr dieses Dokument.'}), 502
        return jsonify(body)

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
            # The uniform content gate keys on a doc_id in the URL; this route
            # carries it in the body, so the wall is applied here explicitly.
            # Minting is the moment a document leaves the session's protection.
            subject = ''
            if identity_gate is not None:
                minter = identity_gate.current_user()
                if minter is None:
                    return jsonify({'success': False, 'error': 'Not found'}), 404
                subject = str(minter.id)
                if not document_grants.granted(
                    subject, doc_id, _rel_path_for_autodoc(str(doc_id))
                ):
                    return jsonify({'success': False, 'error': 'Not found'}), 404
            token = open_token_manager.mint(rel, doc_id, subject=subject)
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
            # This route is exempt from the session and CSRF gates by necessity
            # -- the companion has no browser session -- which is exactly why it
            # cannot also be exempt from the wall. Re-check the minting subject
            # rather than trusting the token alone, so a person whose access was
            # withdrawn cannot spend a token they were holding.
            if identity_gate is not None:
                subject = payload.get('sub') or ''
                if not subject:
                    logger.warning("Refusing an open token minted without a subject")
                    return jsonify({'success': False, 'error': 'Invalid or expired token'}), 401
                if not document_grants.granted(
                    subject, payload['doc'], _rel_path_for_autodoc(str(payload['doc']))
                ):
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
            from context_store import indexed_pages

            def _pages_from_index(pointer: str) -> Dict[int, str]:
                return indexed_pages(load_context(
                    _context_store_path_from_config(config),
                    [pointer, _rel_path_for_autodoc(pointer)],
                ))

            _cortex_text_resolver['resolver'] = DocumentTextResolver(
                resolve_path=_resolve_autodoc_path, indexed_pages=_pages_from_index)
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
            if _ontology_source_is_graph():
                # "Dokumente mit <Feld> = <Name>": only where Knovas offers
                # the listing; otherwise the answer is what it always was.
                links = doc_fields_routes.doc_field_links(
                    api_client, doc_fields_routes.user_key_for(identity_gate),
                    (detail.get('entity') or {}).get('type'))
                if links is not None:
                    detail['doc_field_links'] = links
            return jsonify({'success': True, **detail})
        except Exception:
            logger.error("Ontology entity detail error for %s", entity_id, exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

    # Kuratieren: Der Wissensgraph wird vom Anwender gepflegt, Knovas leitet
    # ihn nicht ab. Beide Quellen koennen schreiben - die Fixture in ihre
    # Datei, die Graph-Quelle ueber POST /secured/graph/nodes bzw. /edges.

    def _ontology_write_refused_message(what: str) -> str:
        """Why a curation write did not stick, in terms of the source in use."""
        if _ontology_source_is_graph():
            return (
                f'Die Knovas-API hat den {what} nicht angelegt. Cortex schreibt in '
                'den Wissensgraphen des Mandanten (ONTOLOGY_SOURCE=graph); das Log '
                'von docbridge-web nennt die Antwort der API.'
            )
        return (
            f'Der {what} konnte nicht gespeichert werden. Die Fixture unter '
            'ONTOLOGY_FIXTURE_PATH ist nicht beschreibbar - siehe Log von docbridge-web.'
        )

    @app.route('/api/ontology/types', methods=['POST'])
    def ontology_type_create():
        try:
            payload = request.get_json(silent=True) or {}
            label = str(payload.get('label') or '').strip()
            if not label:
                return jsonify({'success': False, 'error': 'Name fehlt'}), 400
            created = _ontology_source().create_type(label)
            if created is None:
                # The label was fine, so this is the source refusing the write.
                # In graph mode a 404 from /secured/graph is read as "unknown
                # id" and becomes None here; answering "Name fehlt" blamed the
                # operator's input for an API that declined, and the type then
                # vanished on the next reload with no explanation anywhere.
                return jsonify({
                    'success': False,
                    'error': _ontology_write_refused_message('Typ'),
                }), 502
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
            if not label or not type_id:
                return jsonify({'success': False,
                                'error': 'Name oder Typ fehlt'}), 400
            store = _ontology_source()
            created = store.create_entity(label, type_id)
            if created is None:
                # Same distinction as the type route: the input was complete, so
                # this is the source declining, not the operator's mistake.
                return jsonify({
                    'success': False,
                    'error': _ontology_write_refused_message('Eintrag'),
                }), 502
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


# Filters the Platform applies to what came back, rather than asking the API
# for. /secured/query reads Input, query_prompt, scope and limit and nothing
# else, so anything here would only be noise in the request body.
_LOCAL_ONLY_FILTERS = frozenset({'exact_match'})

# /secured/query answers 422 above 50 (query_pipeline.py); "Mehr laden"
# doubled past it and every second page failed.
_SEARCH_LIMIT_MAX = 50


def _clamp_search_limit(raw: Any, default: int) -> int:
    """The browser's ``limit`` as an int in 1..50; the default when it is
    not a number."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        try:
            value = int(default)
        except (TypeError, ValueError):
            value = 20
    return max(1, min(_SEARCH_LIMIT_MAX, value))


def _search_result_haystack(result: Dict[str, Any]) -> str:
    """Lowercased text used for strict / exact-style matching.

    Includes the text from the context sidecar -- the matched sentences and the
    first page -- and not only the title and the path. Without it "exact match"
    asked whether both words appear in a *filename*, which for a corpus of
    Aktenzeichen is almost never, so the option looked broken rather than
    strict. The sidecar is already loaded for the snippet at this point, so this
    costs nothing.
    """
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
        result.get('first_page_preview'),
    ]
    snippet = result.get('context_snippet')
    if isinstance(snippet, dict):
        parts.extend([snippet.get('before'), snippet.get('match'), snippet.get('after')])
    for location in (result.get('match_locations') or []):
        if isinstance(location, dict):
            parts.extend([location.get('before'), location.get('match'), location.get('after')])
    return ' '.join(str(p) for p in parts if p is not None and str(p).strip()).lower()


def _keep_server_order_enabled() -> bool:
    """``PLATFORM_KEEP_SERVER_ORDER`` (default true): keep the ranking the
    server returned.

    The server reranks with ColBERT and boosts name-prefilter hits; what it
    returns is its final order. ``score`` on a row is the stage-1 cosine of
    the best chunk, and re-sorting by it throws the rerank away (diagnosis
    P1). ``false`` restores the old re-sort by score.
    """
    raw = (os.environ.get("PLATFORM_KEEP_SERVER_ORDER") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def _server_rank(r: Dict[str, Any]) -> float:
    """The row's position in the server's response, or +inf for a row the
    server did not return (a filename supplement, a demo row)."""
    try:
        rank = int(r.get("server_rank"))
    except (TypeError, ValueError):
        return float("inf")
    return float(rank) if rank >= 1 else float("inf")


def _result_sort_key(r: Dict[str, Any]) -> Tuple[Any, ...]:
    """Server order first when it is kept; score and doc_id break ties and
    order rows without a rank."""
    score = -float(r.get('score') or 0)
    doc_id = str(r.get('doc_id') or '')
    if _keep_server_order_enabled():
        return (_server_rank(r), score, doc_id)
    return (score, doc_id)


def _apply_search_refinement(
    enhanced: Dict[str, Any],
    query: str,
    filters: Dict[str, Any],
    config,
    *,
    thresholds: bool = True,
) -> Dict[str, Any]:
    """
    Tighten results after Knovas returns (semantic search is loose by default).

    - exact_match (UI checkbox): every significant token must appear as a substring
      in title/snippet/content/description/path (after enrichment).
    - min_similarity_score: minimum internal *similarity* (higher = better; same scale as
      cosine_similarity, or 1 - cosine_distance). NOT the same as max distance.
    - max_cosine_distance: drop hits whose cosine *distance* exceeds this (0 = best).
      If the API sends no scores, see enforce_similarity_threshold_when_set.

    ``thresholds=False`` skips the two score thresholds: a search filtered by
    document fields is relevance-gated at Knovas, and dropping its rows here
    would turn "these documents match" into an empty list that reads as "no
    document matches the filter" (spec H3). The person's own exact-match
    option and the ordering still apply.
    """
    out: List[Dict[str, Any]] = list(enhanced.get('results', []))

    min_score = config.get_float('web.search.min_similarity_score', 0.0) if thresholds else 0.0
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

    max_dist = config.get_float("web.search.max_cosine_distance", -1.0) if thresholds else -1.0
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

    # The server's ranking first (PLATFORM_KEEP_SERVER_ORDER); otherwise the
    # highest similarity first. Tie-break by doc_id for stable ordering.
    out.sort(key=_result_sort_key)

    refined = enhanced.copy()
    refined['results'] = out
    refined['total'] = len(out)
    return refined


#: /api/search asks Knovas for this many hits more than the page shows (at
#: most as many again as the page itself). Experiment hits are taken out of
#: every answer and come back only for people allowed to see them; without
#: the margin every experiment Knovas ranked into the page would leave the
#: page one document short, and "Mehr laden" (offered for a full page only)
#: would disappear with it.
SEARCH_FETCH_MARGIN = 20
#: The most /api/search asks Knovas for to make room: /secured/query answers
#: 422 above 50 (_SEARCH_LIMIT_MAX; knovas_client clamps to the same), so a
#: larger question would only be cut down there and make has_more read
#: "no more" when Knovas could not say. A page larger than this is asked
#: for as it is.
SEARCH_FETCH_CEILING = _SEARCH_LIMIT_MAX


def _search_page_limit(raw: Any, default: Any) -> int:
    """The page size a search asked for: a whole number of at least 1, the
    configured default when it is missing or not a number."""
    try:
        fallback = max(1, int(default))
    except (TypeError, ValueError, OverflowError):
        fallback = 20
    if raw is None or isinstance(raw, bool):
        return fallback
    try:
        return max(1, int(raw))
    except (TypeError, ValueError, OverflowError):
        return fallback


def _search_fetch_size(page_limit: int) -> int:
    """How many hits to ask Knovas for, for a page of ``page_limit``."""
    margin = min(page_limit, SEARCH_FETCH_MARGIN)
    return max(page_limit, min(SEARCH_FETCH_CEILING, page_limit + margin))


def _search_hit_count(answer: Any) -> int:
    if not isinstance(answer, dict):
        return 0
    rows = answer.get('results')
    return len(rows) if isinstance(rows, list) else 0


def _search_pointer_name(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    name = value.strip().lstrip('/')
    return name or None


def _search_row_pointer_names(row: Any) -> set:
    if not isinstance(row, dict):
        return set()
    names = (_search_pointer_name(row.get(f)) for f in ('doc_id', 'path', 'pointer', 'identifier'))
    return {name for name in names if name}


def _search_semantix_entry_names(entry: Any) -> set:
    if isinstance(entry, dict):
        names = (_search_pointer_name(entry.get(f)) for f in ('pointer', 'identifier', 'doc_id'))
        return {name for name in names if name}
    name = _search_pointer_name(entry)
    return {name} if name else set()


def _cut_search_page(answer: Any, page_limit: int) -> Tuple[Any, bool]:
    """``answer`` with at most ``page_limit`` result rows, and whether any
    were cut. The semantix block loses the pointers of the rows cut, and its
    result_count as many, the way SearchIntegration.split treats the
    experiment hits it takes out. The input is not changed."""
    if not isinstance(answer, dict):
        return answer, False
    rows = answer.get('results')
    if not isinstance(rows, list) or len(rows) <= page_limit:
        return answer, False
    kept, cut = rows[:page_limit], rows[page_limit:]
    out = dict(answer)
    out['results'] = kept
    if 'total' in answer:
        out['total'] = len(kept)
    semantix = answer.get('semantix')
    if isinstance(semantix, dict):
        meta = dict(semantix)
        removed = len(cut)
        pointers = semantix.get('pointers')
        if isinstance(pointers, list):
            kept_names: set = set()
            for row in kept:
                kept_names |= _search_row_pointer_names(row)
            cut_names: set = set()
            for row in cut:
                cut_names |= _search_row_pointer_names(row)
            cut_names -= kept_names
            remaining = [p for p in pointers if not (_search_semantix_entry_names(p) & cut_names)]
            removed = len(pointers) - len(remaining)
            meta['pointers'] = remaining
        count = semantix.get('result_count')
        if isinstance(count, int) and not isinstance(count, bool):
            meta['result_count'] = max(0, count - removed)
        out['semantix'] = meta
    return out, True


def _fetch_search_page(
    ask: Callable[[int], Any],
    split: Callable[[Any], Tuple[Any, List[Dict[str, Any]]]],
    page_limit: int,
    *,
    over_fetch: bool = True,
) -> Tuple[Any, List[Dict[str, Any]], bool]:
    """One page of document hits for /api/search, experiment hits taken out.

    ``ask(n)`` is the search answer for ``n`` hits; ``split`` is
    SearchIntegration.split. Asking for exactly the page would leave it short
    by every experiment hit ``split`` takes out, for everyone who does not
    see experiments. So Knovas is asked for a margin more, and once more for
    twice as many when that was not enough and Knovas had that many; the
    document hits are then cut back to the page. A failed second question
    keeps the first answer.

    Returns ``(answer, experiment_hits, has_more)``: the answer holds at most
    ``page_limit`` document hits, the experiment hits are all that came back,
    and ``has_more`` says whether Knovas may hold more than the page shows
    (it filled the question, or documents were cut).
    """
    fetch = _search_fetch_size(page_limit) if over_fetch else page_limit
    answer, hits = split(ask(fetch))
    returned = _search_hit_count(answer) + len(hits)
    if (over_fetch and hits and _search_hit_count(answer) < page_limit
            and returned >= fetch and fetch < SEARCH_FETCH_CEILING):
        wider = min(SEARCH_FETCH_CEILING, fetch * 2)
        try:
            wider_answer, wider_hits = split(ask(wider))
        except Exception as exc:  # noqa: BLE001 - the first answer still stands
            logger.warning("Second search question (%d hits) failed, keeping the first: %s",
                           wider, exc)
        else:
            answer, hits, fetch = wider_answer, wider_hits, wider
            returned = _search_hit_count(answer) + len(hits)
    answer, cut = _cut_search_page(answer, page_limit)
    return answer, hits, bool(cut or returned >= fetch)


def _merge_experiment_rows(
    results: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
    limit: Any,
) -> List[Dict[str, Any]]:
    """Document hits and experiment rows in one list: best score first, cut to
    the requested page size so experiments take places rather than add them.

    The sort is stable, so documents with equal scores keep the order the
    refinement gave them.
    """

    def score(row: Dict[str, Any]) -> float:
        try:
            return float(row.get('score') or 0)
        except (TypeError, ValueError):
            return 0.0

    merged = sorted(list(results) + list(rows), key=lambda r: -score(r))
    try:
        cap = max(1, int(limit))
    except (TypeError, ValueError):
        return merged
    return merged[:cap]


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
        key=lambda r: (0 if r.get("match_supplement") == "filename" else 1, *_result_sort_key(r)),
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


def _m365_documents(config=None) -> bool:
    """documents.source == m365: every document is identified exactly."""
    if config is None:
        return False
    try:
        return str(config.get('documents.source', 'files') or 'files').strip().lower() == 'm365'
    except Exception:  # noqa: BLE001 - a config stub without the key is "files"
        return False


def _lookup_enrichment_meta(
    enrichment: Dict[str, dict], result: Dict[str, Any], *, exact_only: bool = False,
) -> Optional[dict]:
    """Enrichment row for a result.

    ``exact_only`` drops the file-name and path-suffix fallbacks. They exist to
    bridge a mirror whose identifiers were spelt differently from the index;
    with Microsoft 365 as the source Knovas Connector publishes the index's own
    identifiers, and a fallback can only ever find a DIFFERENT document -- one
    with the same file name elsewhere, possibly behind a wall this person may
    not pass, opened with an app-only viewer that ignores SharePoint's rights.
    """
    for key in _enrichment_lookup_keys(result):
        meta = enrichment.get(key)
        if meta:
            return meta
    if exact_only:
        return None
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
    }, exact_only=_m365_documents(config))
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
    if config is not None and not _m365_documents(config):
        # In Microsoft 365 mode this file is how every hit opens and previews;
        # skipping it past a size cap would silently take both from all of them.
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
    query: str = '',
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
    min_term = 2
    if config is not None:
        min_term = config.get_int('web.search.strict_match_min_term_length', 2)
    terms = context_query_terms(query, min_term)
    context_radius = 10
    if config is not None:
        context_radius = config.get_int("web.search.context_sentences", 10)
    enhanced_results = results.copy()
    
    if 'results' not in enhanced_results:
        return enhanced_results

    for result in enhanced_results['results']:
        meta = (
            _lookup_enrichment_meta(enrichment, result, exact_only=_m365_documents(config))
            if enrichment else None
        )
        if meta:
            # A title from the document's own values (a real title, not a
            # file name: doc_fields_view.title_from_values) is what the firm
            # wrote down; the enrichment file does not overrule it.
            if meta.get("title") and not result.get("title_from_values"):
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

        external = bool(result.get("external_url") and _is_safe_http_url(result["external_url"]))

        file_path = result.get("path")
        if external:
            # Opened in OneDrive/SharePoint, so there is no file here to stat --
            # but the snippets and the "Fundstellen" come from the indexed text,
            # which Knovas Connector wrote for this document like any other.
            pass
        elif file_path:
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
                terms=terms,
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
