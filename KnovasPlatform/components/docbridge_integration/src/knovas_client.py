"""
Knovas API client for document synchronization.
Handles communication with Knovas knowledge base API.
"""

import contextlib
import re
import requests
import logging
from typing import Iterator, List, Dict, Any, Optional, Tuple, Union
from urllib.parse import quote, urlsplit
from datetime import datetime, timezone
import time
import os
import threading
import uuid as _uuid
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from tempfile import NamedTemporaryFile
from tenacity import (
    RetryError,
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from assistant.errors import CoreBusy, CoreFailed
from config_loader import get_config
from doc_fields_view import display_title as _doc_fields_display_title


logger = logging.getLogger(__name__)


# Only transient transport failures are safe to retry. HTTPError (raised by
# raise_for_status on 4xx/5xx) is a RequestException subclass but MUST NOT be
# retried: retrying a 4xx/5xx on a state-changing POST duplicates ingestion.
_RETRYABLE_REQUEST_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)


# -- What a log line may say about a call to the Core (FU-43) ------------------------------------------------------
# A path can hold an id a customer chose (a group's id can be speaking, so can a node's, a field's or a pack's), a
# query string a document's pointer, and an answer's body can quote a group's name, which since RB-4 is a role's
# name. A log line therefore names the route, never the path, and says of an answer only its status, the Core's
# error code when it is a short identifier, the Core's request id (``X-Request-ID``, to find the Core's own lines)
# and the body's size: never body text, at any level, DEBUG included.

_ROUTE_ID = '{id}'
#: The Core's routes this client calls, with ``{id}`` where the path carries an id.
_ROUTE_TEMPLATES = (
    '/secured/health', '/secured/query', '/secured/llm/generate', '/secured/sign_certificate',
    '/secured/init_document_transmission', '/secured/transmit_document_part',
    '/secured/delete_information_object', '/secured/delete_all_documents',
    '/secured/agents/{id}/claims',
    '/secured/access_groups', '/secured/access_groups/{id}',
    '/secured/document_access', '/secured/document_readable', '/secured/documents',
    '/secured/folder_rules', '/secured/folder_rules/{id}', '/secured/document/{id}/text',
    '/secured/graph', '/secured/graph/node-types', '/secured/graph/node-types/{id}',
    '/secured/graph/node-types/{id}/schema', '/secured/graph/node-types/{id}/schema/{id}',
    '/secured/graph/nodes', '/secured/graph/nodes/{id}', '/secured/graph/nodes/{id}/neighbors',
    '/secured/graph/nodes/{id}/facts', '/secured/graph/nodes/{id}/knowledge', '/secured/graph/nodes/{id}/filters',
    '/secured/graph/nodes/{id}/placements', '/secured/graph/placements/{id}/reject',
    '/secured/graph/edges', '/secured/graph/edges/{id}', '/secured/graph/facts/batch',
    '/secured/graph/doc-values', '/secured/graph/doc-values/find',
    '/secured/graph/doc-fields', '/secured/graph/doc-fields/{id}', '/secured/graph/doc-fields/{id}/deprecate',
    '/secured/graph/doc-fields/packs', '/secured/graph/doc-fields/packs/{id}/install',
    '/secured/graph/doc-fields/settings', '/secured/graph/doc-field-rules',
)
# Fewest ids first, so a fixed word wins over an id in the same place (``doc-fields/packs`` before ``doc-fields/{id}``).
_ROUTES = tuple(sorted((tuple(t.strip('/').split('/')) for t in _ROUTE_TEMPLATES),
                       key=lambda route: route.count(_ROUTE_ID)))
_ROUTE_WORDS = frozenset(word for route in _ROUTES for word in route if word != _ROUTE_ID)
_SHORT_CODE = re.compile(r'[A-Za-z][A-Za-z0-9_.:-]{0,63}')
_SHORT_REQUEST_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}')


def _route_name(endpoint: Any) -> str:
    """The route of ``endpoint`` as a log line may name it: ``/secured/access_groups/{id}``, never the id itself.

    The query string and a base URL are dropped. A path no template knows keeps only the route's own words; every
    other segment becomes ``{id}``.
    """
    segments = tuple(s for s in urlsplit(str(endpoint or '')).path.split('/') if s)
    for route in _ROUTES:
        if len(route) == len(segments) and all(r in (_ROUTE_ID, s) for r, s in zip(route, segments)):
            return '/' + '/'.join(route)
    return '/' + '/'.join(s if s in _ROUTE_WORDS else _ROUTE_ID for s in segments)


def _short_code(value: Any) -> str:
    """An error code fit for a log line (``NOT_FOUND``, ``knowledge_graph_disabled``), else ``-``."""
    return value if isinstance(value, str) and _SHORT_CODE.fullmatch(value) else '-'


def _code_or_none(value: Any) -> Optional[str]:
    """An error code the client's own errors may carry: a short identifier, else None (RB-FIX-2)."""
    return value if isinstance(value, str) and _SHORT_CODE.fullmatch(value) else None


#: Inside ``KnovasAPIClient.within_time`` a retry is made only when at least this many seconds remain after its wait
#: (PMVP-24): an attempt with less would only use up the answer's deadline.
MIN_ATTEMPT_SECONDS = 5.0


def _upcoming_wait(retry_state: Any) -> float:
    """The wait tenacity makes before the next attempt, computed by the retry's own ``wait`` strategy.

    tenacity 8 (the pinned 8.2.3) asks ``stop`` before it computes the wait and has no ``upcoming_sleep``; 9 computes
    it first. Both call the same strategy with the same state (``wait_exponential`` reads the attempt number), so
    asking it here gives the wait either version is about to make. Anything unreadable counts as no wait.
    """
    wait = getattr(getattr(retry_state, "retry_object", None), "wait", None)
    try:
        return max(0.0, float(wait(retry_state))) if callable(wait) else 0.0
    except Exception:  # noqa: BLE001 - a strategy that cannot say counts as no wait
        return 0.0


def _out_of_time(retry_state: Any) -> bool:
    """tenacity's second stop for ``_make_request``: inside ``within_time``, no retry without ``MIN_ATTEMPT_SECONDS``
    after its wait. Outside it (no bound on the thread) it never stops anything: three tries, as before."""
    client = retry_state.args[0] if getattr(retry_state, "args", None) else None
    left_of = getattr(client, "_time_left", None)
    left = left_of() if callable(left_of) else None
    return left is not None and left - _upcoming_wait(retry_state) < MIN_ATTEMPT_SECONDS


def _read_part(timeout: Any) -> float:
    """The read part of a request's timeout (a number or a (connect, read) pair); none is no limit."""
    if isinstance(timeout, tuple):
        timeout = timeout[-1]
    return float("inf") if timeout is None else float(timeout)


def _read_body(response: requests.Response, time_left: Any) -> None:
    """Read a streamed answer's body in the pieces the peer sends, and stop once no time is left (PMVP-24).

    ``read1`` returns whatever one read of the socket brought, so a peer that sends one byte at a time is looked at
    after every byte. The body ends up where ``requests`` keeps it (``content``, ``json()``). urllib3's errors
    become the ``requests`` errors ``iter_content`` would raise, a read that times out a ``ReadTimeout``. A body
    already in hand (a test's prepared answer) is left as it is.
    """
    import urllib3.exceptions as u3

    raw = getattr(response, "raw", None)
    if raw is None or getattr(response, "_content", False) is not False:
        return
    read1 = getattr(raw, "read1", None)
    pieces: List[bytes] = []
    try:
        while True:
            if time_left() <= 0:
                raise requests.exceptions.ReadTimeout("no time left while the answer was read")
            piece = read1(65536, decode_content=True) if callable(read1) else raw.read(1, decode_content=True)
            if not piece:
                break
            pieces.append(piece)
    except u3.ReadTimeoutError as exc:
        raise requests.exceptions.ReadTimeout(str(exc)) from None
    except u3.ProtocolError as exc:
        raise requests.exceptions.ChunkedEncodingError(str(exc)) from None
    except u3.DecodeError as exc:
        raise requests.exceptions.ContentDecodingError(str(exc)) from None
    except u3.SSLError as exc:
        raise requests.exceptions.SSLError(str(exc)) from None
    response._content = b"".join(pieces)
    response._content_consumed = True
    release = getattr(raw, "release_conn", None)
    if callable(release):
        release()


def _capped(timeout: Any, left: float) -> Any:
    """``timeout`` (a number, a (connect, read) pair or None) no longer than ``left`` seconds."""
    if isinstance(timeout, tuple):
        return tuple(left if part is None else min(float(part), left) for part in timeout)
    return left if timeout is None else min(float(timeout), left)


def _transport_error(exc: requests.exceptions.RequestException, method: str,
                     endpoint: str) -> requests.exceptions.RequestException:
    """The same kind of error ``requests`` raised for no answer, saying only the route and the kind (RB-FIX-2).

    The original's text quotes the whole URL -- the path's ids and the query string (``?q=<a name>``) -- and its
    ``request`` holds the URL and the body; a caller that logs the exception, or a route that logs its traceback,
    would write them. The new error has neither, keeps the class (so retries and ``except Timeout`` stay as they
    were) and is raised outside the handler, so nothing of the original is chained.
    """
    kind = type(exc)
    text = f"Knovas API {method} {_route_name(endpoint)}: no answer ({kind.__name__})"
    try:
        clean = kind(text)
        if type(clean) is kind and str(clean) == text:
            return clean
    except Exception:  # noqa: BLE001 - a class that takes other arguments: its nearest plain base below
        pass
    for base in (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout,
                 requests.exceptions.Timeout, requests.exceptions.SSLError,
                 requests.exceptions.ConnectionError):
        if isinstance(exc, base):
            return base(text)
    return requests.exceptions.RequestException(text)


def _error_code(response: Any) -> str:
    """The Core's ``error_code`` of an answer when it is a short identifier, else ``-``. Never raises."""
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - never fail while reporting a failure
        return '-'
    return _short_code(body.get('error_code') if isinstance(body, dict) else None)


def _request_id(response: Any) -> str:
    """The Core's request id of an answer (``X-Request-ID``), or ``-``. Never raises."""
    try:
        value = response.headers.get('X-Request-ID')
    except Exception:  # noqa: BLE001 - a response double without headers
        return '-'
    return value if isinstance(value, str) and _SHORT_REQUEST_ID.fullmatch(value) else '-'


def _body_size(response: Any) -> int:
    """How many characters the answer's body has (-1: unreadable): whether the Core said anything, not what."""
    try:
        return len(response.text or '')
    except Exception:  # noqa: BLE001 - never fail while reporting a failure
        return -1


def _raise_for_status(response: Any, method: str, endpoint: str) -> None:
    """``response.raise_for_status()``, with an error that names the route instead of the URL.

    The error ``requests`` raises quotes the whole URL (the path's ids, the query's pointer), and a caller that logs
    the exception writes it. The new error keeps the ``response``; nothing else of the old one is chained.
    """
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        answer = exc.response if exc.response is not None else response
    else:
        return
    # Raised outside the handler: the original is not even the hidden context (RB-FIX-2).
    raise requests.exceptions.HTTPError(
        f"Knovas API {method} {_route_name(endpoint)} -> HTTP {getattr(answer, 'status_code', '?')} "
        f"(request_id={_request_id(answer)})",
        response=answer,
    )


def _normalize_semantix_similarity_value(raw: Any) -> float:
    """Map API number to [0, 1] (handles percentages 0–100)."""
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if f > 1.0:
        if f <= 100.0:
            f = f / 100.0
        else:
            f = 1.0
    return max(0.0, min(1.0, f))


def _similarity_from_cosine_distance(item: Dict[str, Any]) -> Optional[float]:
    """Knovas: low distance = good match → internal score = 1 - distance (higher better)."""
    for k in ('cosine_distance', 'cosineDistance', 'CosineDistance'):
        if k not in item or item[k] is None:
            continue
        d = _normalize_semantix_similarity_value(item[k])
        return max(0.0, min(1.0, 1.0 - d))
    return None


def _extract_semantix_query_similarity(item: Dict[str, Any]) -> float:
    """
    Read match quality from Knovas /secured/query items.

    Prefer cosine_similarity (higher = better), then final_score (common secured-query
    field). If only cosine_distance is present, use 1 - distance as the internal score
    for sorting and min_similarity_score.
    """
    for k in ('cosine_similarity', 'cosineSimilarity', 'CosineSimilarity'):
        if k not in item or item[k] is None:
            continue
        return _normalize_semantix_similarity_value(item[k])

    for k in ('final_score', 'FinalScore'):
        if k not in item or item[k] is None:
            continue
        return _normalize_semantix_similarity_value(item[k])

    dist_score = _similarity_from_cosine_distance(item)
    if dist_score is not None:
        return dist_score

    keys = (
        'similarity',
        'Similarity',
        'score',
        'Score',
        'match_score',
        'MatchScore',
        'relevanceScore',
        'RelevanceScore',
        'relevance',
        'Relevance',
    )
    for k in keys:
        if k not in item or item[k] is None:
            continue
        return _normalize_semantix_similarity_value(item[k])
    for nest_key in ('metadata', 'meta', 'Meta'):
        nest = item.get(nest_key)
        if not isinstance(nest, dict):
            continue
        for k in ('cosine_similarity', 'cosineSimilarity', 'CosineSimilarity'):
            if k not in nest or nest[k] is None:
                continue
            return _normalize_semantix_similarity_value(nest[k])
        for k in ('final_score', 'FinalScore'):
            if k not in nest or nest[k] is None:
                continue
            return _normalize_semantix_similarity_value(nest[k])
        sd = _similarity_from_cosine_distance(nest)
        if sd is not None:
            return sd
        for k in keys:
            if k not in nest or nest[k] is None:
                continue
            return _normalize_semantix_similarity_value(nest[k])
    for k, v in item.items():
        if not isinstance(k, str) or v is None:
            continue
        lk = k.lower()
        if 'similarity' in lk or lk == 'relevance':
            return _normalize_semantix_similarity_value(v)
    return 0.0


def _secured_query_field_empty(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, str) and not val.strip():
        return True
    if isinstance(val, list) and len(val) == 0:
        return True
    return False


def _merge_secured_query_result_rows(
    primary: List[Any],
    secondary: List[Any],
) -> List[Dict[str, Any]]:
    """
    Merge top-level secured/query rows with nested data.results rows.

    Some API builds return pointers/scores at the top level and page_number,
    sentence_number, top_chunks only under data.results for the same index.
    """
    if not secondary:
        return [r for r in primary if isinstance(r, dict)]
    if not primary:
        return [r for r in secondary if isinstance(r, dict)]

    fill_keys = (
        "pointer",
        "Pointer",
        "identifier",
        "Identifier",
        "document_uuid",
        "documentUuid",
        "DocumentUuid",
        "page_number",
        "pageNumber",
        "PageNumber",
        "page",
        "Page",
        "sentence_number",
        "sentenceNumber",
        "SentenceNumber",
        "sentence",
        "Sentence",
        "top_chunks",
        "topChunks",
        "TopChunks",
        "ingested_summary",
        "ingestedSummary",
        "IngestedSummary",
        "cosine_similarity",
        "cosineSimilarity",
        "CosineSimilarity",
        "cosine_distance",
        "cosineDistance",
        "CosineDistance",
        "final_score",
        "FinalScore",
    )

    def _pointer(row: Dict[str, Any]) -> str:
        return str(row.get("pointer") or row.get("Pointer") or row.get("identifier") or "")

    secondary_by_ptr = {
        _pointer(s): s for s in secondary if isinstance(s, dict) and _pointer(s)
    }

    merged: List[Dict[str, Any]] = []
    for i, row in enumerate(primary):
        if not isinstance(row, dict):
            continue
        fill: Dict[str, Any] = {}
        if i < len(secondary) and isinstance(secondary[i], dict):
            fill = secondary[i]
        else:
            ptr = _pointer(row)
            if ptr and ptr in secondary_by_ptr:
                fill = secondary_by_ptr[ptr]

        combined = dict(fill)
        combined.update(row)
        for key in fill_keys:
            if _secured_query_field_empty(combined.get(key)) and not _secured_query_field_empty(
                fill.get(key)
            ):
                combined[key] = fill.get(key)
        merged.append(combined)

    seen = {_pointer(m) for m in merged if _pointer(m)}
    for s in secondary:
        if not isinstance(s, dict):
            continue
        ptr = _pointer(s)
        if ptr and ptr not in seen:
            merged.append(dict(s))
            seen.add(ptr)
    return merged


def _unwrap_secured_query_response(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize /secured/query JSON whether the API returns a flat body or nests
    fields under `data` (see Analytics Integration Guide vs Client guide).
    """
    data = result.get("data")
    if not isinstance(data, dict):
        return result

    out = dict(result)
    inner_results = data.get("results")
    if inner_results is None:
        inner_results = data.get("hits")
    top_results = out.get("results")
    if top_results is None:
        top_results = out.get("hits")

    if isinstance(inner_results, list):
        if isinstance(top_results, list) and top_results:
            out["results"] = _merge_secured_query_result_rows(top_results, inner_results)
        elif not top_results:
            out["results"] = inner_results

    if out.get("pointers") is None:
        out["pointers"] = data.get("pointers")
    if out.get("result_count") is None:
        out["result_count"] = data.get("result_count")
    if out.get("query_session_id") is None:
        out["query_session_id"] = data.get("query_session_id")
    if out.get("status") is None:
        out["status"] = data.get("status")
    if out.get("message") is None:
        out["message"] = data.get("message")
    # The honesty keys (no_strong_matches, the where echo, ...) travel at the
    # top level today; a nested build must not lose them, because a missing
    # `where` echo reads as "not filtered" and withholds the results.
    for key in ("no_strong_matches", "no_results_reason", "relevance_gate_applied",
                "meta", "where", "return_fields", "auto_scope"):
        if key not in out and key in data:
            out[key] = data[key]
    return out


_INGESTED_SUMMARY_MAX_LEN = 4000


def _soft_truncate_summary(text: str) -> str:
    if len(text) <= _INGESTED_SUMMARY_MAX_LEN:
        return text
    return text[: _INGESTED_SUMMARY_MAX_LEN - 1].rstrip() + "…"


def _is_http_url(url: Any) -> bool:
    if not url or not isinstance(url, str):
        return False
    u = url.strip().lower()
    return u.startswith("https://") or u.startswith("http://")


def _display_title_for_hit(pointer: str, raw_title: Optional[Any]) -> str:
    """
    Prefer filename stem for RC/corpus pointers; Knovas title fields are often run-on text.
    """
    p = (pointer or "").strip().replace("\\", "/")
    base = p.rsplit("/", 1)[-1] if p else ""
    stem = Path(base).stem if base else ""
    title = str(raw_title or "").strip()
    if stem and (
        not title
        or len(title) > 100
        or title == p
        or title.lower().startswith(stem.lower() + " ")
    ):
        return stem[:500]
    return (title or stem or p or "Unbenanntes Dokument")[:500]


def _ingested_summary_text(value: Any) -> Optional[str]:
    """
    Normalize ingested_summary from Knovas /secured/query.

    API shape: {"present": bool, "text": str} (see Secure_API.md); older payloads may be plain strings.
    """
    if isinstance(value, str) and value.strip():
        return _soft_truncate_summary(value.strip())
    if isinstance(value, dict):
        if value.get("present") is False:
            return None
        for key in ("text", "summary", "content"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return _soft_truncate_summary(text.strip())
    return None


def _ingested_summary_from_hit(item: Dict[str, Any]) -> Optional[str]:
    """Document-level summary from Knovas query hit (top-level, metadata, or top_chunks[0])."""
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    for src in (item, meta):
        for k in ("ingested_summary", "ingestedSummary"):
            v = src.get(k) if isinstance(src, dict) else None
            text = _ingested_summary_text(v)
            if text:
                return text
    tc = _first_top_chunk(item)
    if isinstance(tc, dict):
        for k in ("ingested_summary", "ingestedSummary"):
            text = _ingested_summary_text(tc.get(k))
            if text:
                return text
    return None


def _snippet_from_hit(item: Dict[str, Any]) -> Optional[str]:
    """The passage text of one /secured/query hit, or None (``_snippet_with_location``).

    Knovas attaches its verbatim text (``QUERY_RETURN_CHUNK_SNIPPETS``, on by
    default since 2026-10-02; KnowledgeBase ``query_two_stage.attach_chunk_snippets``)
    as ``snippet`` on each of the first three served ``top_chunks`` entries,
    and the first of those as the hit's own ``snippet``: a window of at most
    240 characters cut from the chunk's ``original_text`` (whole rows for a
    table chunk, none for an auto summary). Only ``original_text`` and
    ``snippet`` are read -- not ``text``, ``content`` or other chunk keys
    a hit may carry: GI-QUERY-04 promises that served text
    is ``original_text``, and the BM25 ``text`` field has its digits
    stripped.
    """
    found = _snippet_with_location(item)
    return found[0] if found else None


#: The most characters one part of ``GET /secured/document/<uuid>/text`` holds (KnowledgeBase ``secure_api.py``,
#: ``DOCUMENT_TEXT_MAX_PART_CHARS``); a larger ``max_chars`` is the Core's ``422 invalid_paging``.
DOCUMENT_TEXT_MAX_PART_CHARS = 20_000
#: The most bytes of one document-text answer that are read: a part of 20,000 characters is at most about 240 KB of
#: JSON (every character escaped); a larger answer is not a part, and reading it is stopped.
DOCUMENT_TEXT_MAX_BODY_BYTES = 1_000_000


class _StopReading(Exception):
    """The worker of ``_get_by_deadline`` stops: the deadline passed, the body is too large, or it was abandoned."""


def _whole_number(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _document_text_part(payload: Any) -> Optional[Dict[str, Any]]:
    """``{"text", "offset", "next_offset", "total_chars", "truncated"}`` of a document-text answer, or None.

    The Core merges the part into its success envelope (``text``, ``offset``, ``next_offset``, ``total_chars``,
    ``page_from``, ``page_to``, ``source``, ``truncated``, with ``document_uuid``, ``pointer`` and ``title``); an
    envelope that nests it under ``data`` is read too. Only the paging and the text are kept: the pointer and the
    title are not needed, the search row has them.
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    text, offset, next_offset, total = (data.get("text"), data.get("offset"), data.get("next_offset"),
                                        data.get("total_chars"))
    if not isinstance(text, str) or not _whole_number(offset) or not _whole_number(total):
        return None
    if next_offset is not None and not (_whole_number(next_offset) and next_offset > offset):
        return None
    return {"text": text, "offset": offset, "next_offset": next_offset, "total_chars": total,
            "truncated": data.get("truncated") is True}


#: The most characters of the passage one hit gives the Knovas Assistent
#: (PMVP-20 passages). The setting ``ASSISTANT_PASSAGE_CHARS`` replaces it: a
#: whole number from ``MIN_PASSAGE_CHARS`` to ``MAX_PASSAGE_CHARS``; a larger
#: one is cut to ``MAX_PASSAGE_CHARS`` (PMVP-20 context's verification), and
#: anything else is the default; either with one warning per search that
#: names the setting and not its value.
DEFAULT_PASSAGE_CHARS = 1_200
MIN_PASSAGE_CHARS = 100
MAX_PASSAGE_CHARS = 2_000
#: The least words of a passage: the reference check's least quote
#: (``assistant.check.MIN_QUOTE_WORDS``; a test keeps the two equal).
MIN_PASSAGE_WORDS = 3
#: Between two snippets of one chunk; and the mark of a passage that was cut
#: (the reference check reads it as a closing mark).
_PASSAGE_JOIN = " \u2026 "
_PASSAGE_CUT = "\u2026"


def _passage_chars() -> int:
    """``ASSISTANT_PASSAGE_CHARS``, read per search (inside ``keeping_snippets()`` only)."""
    raw = (os.environ.get("ASSISTANT_PASSAGE_CHARS") or "").strip()
    if not raw:
        return DEFAULT_PASSAGE_CHARS
    if raw.isascii() and raw.isdigit() and int(raw) >= MIN_PASSAGE_CHARS:
        if int(raw) <= MAX_PASSAGE_CHARS:
            return int(raw)
        logger.warning("ASSISTANT_PASSAGE_CHARS is above %d; using %d", MAX_PASSAGE_CHARS, MAX_PASSAGE_CHARS)
        return MAX_PASSAGE_CHARS
    logger.warning("Invalid ASSISTANT_PASSAGE_CHARS (a whole number from %d to %d); using %d",
                   MIN_PASSAGE_CHARS, MAX_PASSAGE_CHARS, DEFAULT_PASSAGE_CHARS)
    return DEFAULT_PASSAGE_CHARS


def _passage_words(text: str) -> int:
    """Words as the reference check counts them: runs between white space
    with a letter or a digit in them."""
    return sum(1 for word in text.split() if any(char.isalnum() for char in word))


def _cut_passage(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` characters at the end of a word,
    with ``_PASSAGE_CUT``; inside a word only when the first word is longer."""
    if len(text) <= limit:
        return text
    room = max(1, limit - len(_PASSAGE_CUT))
    end = room
    while end > 0 and not text[end].isspace():
        end -= 1
    head = text[:end].rstrip() if end > 0 else text[:room]
    return head + _PASSAGE_CUT


def _chunk_text(entries: List[Dict[str, Any]]) -> Optional[str]:
    """The longest text the answer carries for one chunk: its
    ``original_text`` when Knovas sends it, else its snippets in order,
    joined with ``_PASSAGE_JOIN``."""
    for entry in entries:
        full = entry.get("original_text")
        if isinstance(full, str) and full.strip():
            return full.strip()
    snippets: List[str] = []
    for entry in entries:
        value = entry.get("snippet")
        if isinstance(value, str) and value.strip() and value.strip() not in snippets:
            snippets.append(value.strip())
    return _PASSAGE_JOIN.join(snippets) or None


def _chunk_rank(entries: List[Tuple[int, Dict[str, Any]]]) -> Tuple[int, float]:
    """Best first: the reranker's score where Knovas has one, then the rest
    in the order it sent them -- its own citation order (``query_two_stage``).
    Not the stage-1 cosine: the reranker disagrees with it on purpose."""
    ranks: List[Tuple[int, float]] = []
    for index, entry in entries:
        score = entry.get("rerank_score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            ranks.append((0, -float(score)))
        else:
            ranks.append((1, float(index)))
    return min(ranks)


def _snippet_with_location(
    item: Dict[str, Any], limit: int = DEFAULT_PASSAGE_CHARS,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """``(passage, location of the chunk it is from)`` of one hit, or None.

    PMVP-20 passages: the passage is the longest text the answer carries for
    ONE chunk (``_chunk_text``; entries with the same ``chunk_uuid`` are one
    chunk), cut at a word to ``limit`` characters. Of the hit's chunks the
    best ranked (``_chunk_rank``) whose passage has at least
    ``MIN_PASSAGE_WORDS`` words is taken -- the reference check needs that
    many for a quote. Text and location come from the same chunk, never
    from two (rework 1, item 5: the row's ``page_number`` is
    ``top_chunks[0]``'s, which may carry no text). A row-level ``snippet`` is
    the last resort, with an unknown location (``{}``). A hit with nothing
    of that many words has no passage.
    """
    chunks = item.get("top_chunks")
    if not isinstance(chunks, list):
        chunks = item.get("topChunks")
    by_chunk: Dict[str, List[Tuple[int, Dict[str, Any]]]] = {}
    for index, chunk in enumerate(chunks if isinstance(chunks, list) else ()):
        if isinstance(chunk, dict):
            cid = chunk.get("chunk_uuid") or chunk.get("uuid")
            by_chunk.setdefault(str(cid) if cid else f"#{index}", []).append((index, chunk))
    candidates: List[Tuple[Tuple[int, float], str, Dict[str, Any]]] = []
    for entries in by_chunk.values():
        text = _chunk_text([entry for _, entry in entries])
        if text is not None:
            candidates.append((_chunk_rank(entries), text, entries[0][1]))
    candidates.sort(key=lambda candidate: candidate[0])
    for _rank, text, chunk in candidates:
        passage = _cut_passage(text, limit)
        if _passage_words(passage) >= MIN_PASSAGE_WORDS:
            return passage, _location_from_mapping(_coalesce_secured_query_keys(chunk))
    value = item.get("snippet")
    if isinstance(value, str) and value.strip():
        passage = _cut_passage(value.strip(), limit)
        if _passage_words(passage) >= MIN_PASSAGE_WORDS:
            return passage, {}
    return None


def _llm_generate_result(payload: Any) -> Optional[Dict[str, Any]]:
    """``{"content", "model", "prompt_tokens", "completion_tokens"}`` from a
    /secured/llm/generate answer, or None when it is not a success envelope
    with text content.

    The Core answers flat (``{"status": "success", "message": ..., "content":
    ..., ...}``: ``APIResponseService.create_success_response`` merges the
    data into the envelope); an envelope that nests them under ``data`` is
    read as well.
    """
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return None
    data = payload.get("data")
    source = data if isinstance(data, dict) and "content" in data else payload
    content = source.get("content")
    if not isinstance(content, str):
        return None

    def _count(value: Any) -> Optional[int]:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return None

    model = source.get("model")
    return {
        "content": content,
        "model": model if isinstance(model, str) else None,
        "prompt_tokens": _count(source.get("prompt_tokens")),
        "completion_tokens": _count(source.get("completion_tokens")),
    }


def _document_date_from_hit(item: Dict[str, Any]) -> Any:
    """Best-effort document date from Knovas query hit (top-level and metadata)."""
    raw_meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    for src in (item, raw_meta):
        for k in (
            "date",
            "document_date",
            "timestamp",
            "created_at",
            "modified_at",
            "doc_date",
            "DocumentDate",
        ):
            v = src.get(k)
            if v is None:
                continue
            if isinstance(v, str) and not v.strip():
                continue
            return v
    return None


def _normalize_semantix_path_for_init(raw: Optional[Any]) -> Optional[str]:
    """Path for init_document_transmission (max 2000, forward slashes)."""
    if raw is None:
        return None
    s = str(raw).strip().replace("\\", "/")
    if not s:
        return None
    low = s.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        if not s.startswith("/"):
            s = "/" + s.lstrip("/")
    if len(s) > 2000:
        s = s[:1999] + "…"
    return s


_MAX_TABLES_PER_PART = 50
_MAX_TABLE_COLUMNS = 64
_MAX_TABLE_ROWS = 5000
_MAX_CELL_CHARS = 1024
_MAX_HEADER_CHARS = 512
_MAX_TABLE_HINT_CHARS = 128
_MAX_TABLE_TITLE_CHARS = 512


def _validate_and_normalize_tables(tables: Any) -> List[Dict[str, Any]]:
    """Normalize structured tables for POST /secured/transmit_document_part."""
    if not tables:
        return []
    if not isinstance(tables, list):
        raise ValueError("tables must be a list")
    if len(tables) > _MAX_TABLES_PER_PART:
        raise ValueError(f"tables exceeds max {_MAX_TABLES_PER_PART} per part")

    normalized: List[Dict[str, Any]] = []
    allowed_keys = frozenset(
        {"client_table_hint", "headers", "rows", "title", "page", "bbox"}
    )
    for idx, raw in enumerate(tables):
        if not isinstance(raw, dict):
            raise ValueError(f"tables[{idx}] must be an object")
        unknown = set(raw.keys()) - allowed_keys
        if unknown:
            raise ValueError(f"tables[{idx}] has unknown keys: {sorted(unknown)}")

        hint = str(raw.get("client_table_hint") or "").strip()
        if not hint or len(hint) > _MAX_TABLE_HINT_CHARS:
            raise ValueError(f"tables[{idx}].client_table_hint must be 1–{_MAX_TABLE_HINT_CHARS} chars")

        headers_raw = raw.get("headers")
        if not isinstance(headers_raw, list) or not headers_raw:
            raise ValueError(f"tables[{idx}].headers must be a non-empty array")
        if len(headers_raw) > _MAX_TABLE_COLUMNS:
            raise ValueError(f"tables[{idx}].headers exceeds {_MAX_TABLE_COLUMNS} columns")
        headers = []
        for col_i, h in enumerate(headers_raw):
            hs = str(h)
            if len(hs) > _MAX_HEADER_CHARS:
                raise ValueError(f"tables[{idx}].headers[{col_i}] exceeds {_MAX_HEADER_CHARS} chars")
            headers.append(hs)

        rows_raw = raw.get("rows")
        if not isinstance(rows_raw, list):
            raise ValueError(f"tables[{idx}].rows must be an array")
        if len(rows_raw) > _MAX_TABLE_ROWS:
            raise ValueError(f"tables[{idx}].rows exceeds {_MAX_TABLE_ROWS} rows")
        rows: List[List[str]] = []
        col_count = len(headers)
        for row_i, row in enumerate(rows_raw):
            if not isinstance(row, list):
                raise ValueError(f"tables[{idx}].rows[{row_i}] must be an array")
            if len(row) != col_count:
                raise ValueError(
                    f"tables[{idx}].rows[{row_i}] must have {col_count} cells (got {len(row)})"
                )
            cells = []
            for cell_i, cell in enumerate(row):
                cs = str(cell)
                if len(cs) > _MAX_CELL_CHARS:
                    raise ValueError(
                        f"tables[{idx}].rows[{row_i}][{cell_i}] exceeds {_MAX_CELL_CHARS} chars"
                    )
                cells.append(cs)
            rows.append(cells)

        table: Dict[str, Any] = {
            "client_table_hint": hint,
            "headers": headers,
            "rows": rows,
        }
        title = raw.get("title")
        if title is not None:
            ts = str(title).strip()
            if ts:
                if len(ts) > _MAX_TABLE_TITLE_CHARS:
                    raise ValueError(f"tables[{idx}].title exceeds {_MAX_TABLE_TITLE_CHARS} chars")
                table["title"] = ts
        page = raw.get("page")
        if page is not None:
            page_i = int(page)
            if page_i < 1 or page_i > 100000:
                raise ValueError(f"tables[{idx}].page must be 1–100000")
            table["page"] = page_i
        bbox = raw.get("bbox")
        if bbox is not None:
            if not isinstance(bbox, list) or len(bbox) != 4:
                raise ValueError(f"tables[{idx}].bbox must be [x0, y0, x1, y1]")
            table["bbox"] = [float(bbox[i]) for i in range(4)]
        normalized.append(table)
    return normalized


def _secured_transmit_part_payload(
    transmission_key_id: str,
    part_number: int,
    part: Dict[str, Any],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "key": transmission_key_id,
        "snippet": part["snippet"],
        "part_number": part_number,
    }
    for field in ("page_number", "sentence_number"):
        val = part.get(field)
        if val is None:
            continue
        try:
            num = int(val)
            if num >= 1:
                payload[field] = num
        except (TypeError, ValueError):
            pass
    if part.get("tables"):
        payload["tables"] = _validate_and_normalize_tables(part["tables"])
    return payload


def _coerce_location_int(val: Any) -> Optional[int]:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    if isinstance(val, str):
        s = val.strip()
        if s.isdigit():
            return int(s)
        try:
            as_float = float(s)
            if as_float == int(as_float):
                return int(as_float)
        except ValueError:
            pass
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _location_from_mapping(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract page_number / sentence_number from assorted Knovas API field names."""
    if not isinstance(data, dict):
        return {}
    sources: List[Dict[str, Any]] = [data]
    for nest_key in ("location", "metadata", "meta", "properties", "best_chunk", "chunk"):
        nested = data.get(nest_key)
        if isinstance(nested, dict):
            sources.append(nested)
    page_keys = (
        "page_number",
        "pageNumber",
        "PageNumber",
        "page",
        "Page",
        "page_num",
        "pageNum",
    )
    sent_keys = (
        "sentence_number",
        "sentenceNumber",
        "SentenceNumber",
        "sentence",
        "Sentence",
        "sent_num",
        "sentenceNum",
        "line_number",
        "lineNumber",
    )
    # Das Ende des Chunks, den die Suche bewertet hat. Knovas schickt es mit und
    # garantiert es: fehlt es an einem alt geschriebenen Chunk, setzt der Server
    # den Startsatz ein. Damit ist bekannt, ueber welche Saetze der Treffer
    # ueberhaupt geht -- die Fundstelle steht irgendwo darin, nicht
    # zwangslaeufig im ersten Satz.
    sent_end_keys = (
        "sentence_number_end",
        "sentenceNumberEnd",
        "SentenceNumberEnd",
        "sentence_end",
        "sentenceEnd",
    )
    out: Dict[str, Any] = {}
    for src in sources:
        if out.get("page_number") is None:
            for key in page_keys:
                page = _coerce_location_int(src.get(key))
                if page is not None:
                    out["page_number"] = page
                    break
        if out.get("sentence_number") is None:
            for key in sent_keys:
                sent = _coerce_location_int(src.get(key))
                if sent is not None:
                    out["sentence_number"] = sent
                    break
        if out.get("sentence_number_end") is None:
            for key in sent_end_keys:
                end = _coerce_location_int(src.get(key))
                if end is not None:
                    out["sentence_number_end"] = end
                    break
    return out


def _score_fields_from_mapping(data: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in ("cosine_similarity", "cosine_distance"):
        val = data.get(key)
        if val is None and key.startswith("cosine"):
            camel = "cosineSimilarity" if key == "cosine_similarity" else "cosineDistance"
            val = data.get(camel)
        if val is not None:
            out[key] = val
    return out


def _coalesce_secured_query_keys(item: Dict[str, Any]) -> Dict[str, Any]:
    """Map PascalCase / camelCase Knovas API keys to snake_case fields we parse."""
    merged = dict(item)
    for canonical, aliases in (
        ("pointer", ("pointer", "Pointer", "identifier", "Identifier")),
        ("top_chunks", ("top_chunks", "topChunks", "TopChunks")),
        ("page_number", ("page_number", "pageNumber", "PageNumber", "page", "Page")),
        ("sentence_number", ("sentence_number", "sentenceNumber", "SentenceNumber", "sentence", "Sentence")),
        ("cosine_similarity", ("cosine_similarity", "cosineSimilarity", "CosineSimilarity")),
        ("cosine_distance", ("cosine_distance", "cosineDistance", "CosineDistance")),
        ("document_uuid", ("document_uuid", "documentUuid", "DocumentUuid")),
        ("ingested_summary", ("ingested_summary", "ingestedSummary", "IngestedSummary")),
    ):
        if merged.get(canonical) is not None:
            continue
        for alias in aliases:
            if alias in merged and merged[alias] is not None:
                merged[canonical] = merged[alias]
                break
    return merged


def _prepare_secured_query_hit(item: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize secured-query hit keys before merge (camelCase, nested location)."""
    merged = _coalesce_secured_query_keys(item)
    if not isinstance(merged.get("top_chunks"), list) and isinstance(merged.get("topChunks"), list):
        merged["top_chunks"] = merged["topChunks"]
    loc = _location_from_mapping(merged)
    for key, val in loc.items():
        if merged.get(key) is None:
            merged[key] = val
    return merged


def _first_top_chunk(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Secured query hits often nest match location and scores under top_chunks[0]."""
    chunks = item.get("top_chunks")
    if not isinstance(chunks, list):
        chunks = item.get("topChunks")
    if not isinstance(chunks, list) or not chunks:
        return None
    first = chunks[0]
    return first if isinstance(first, dict) else None


def _normalize_top_chunks(chunks: Any) -> List[Dict[str, Any]]:
    """Knovas /secured/query: top_chunks holds extra match locations (no chunk text)."""
    if not isinstance(chunks, list):
        return []
    out: List[Dict[str, Any]] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        normalized = _coalesce_secured_query_keys(chunk)
        row = _location_from_mapping(normalized)
        row.update(_score_fields_from_mapping(normalized))
        if row:
            out.append(row)
    return out


def _merge_secured_query_hit(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Flatten Knovas /secured/query result rows: copy top_chunks[0] score/location
    fields when top-level values are missing. Chunk text is never in the API payload.
    """
    merged = dict(item)
    tc = _first_top_chunk(merged)
    if not tc:
        return merged

    def _is_empty(val: Any) -> bool:
        if val is None:
            return True
        if isinstance(val, str) and not val.strip():
            return True
        return False

    for key, val in _location_from_mapping(tc).items():
        if _is_empty(merged.get(key)):
            merged[key] = val

    for key in (
        "cosine_distance",
        "cosineSimilarity",
        "cosineDistance",
        "final_score",
        "FinalScore",
        "ingested_summary",
        "ingestedSummary",
    ):
        if not _is_empty(merged.get(key)):
            continue
        val = tc.get(key)
        if not _is_empty(val):
            merged[key] = val
    return merged


def _primary_location_from_hit(
    item: Dict[str, Any],
    top_chunks: List[Dict[str, Any]],
) -> Tuple[Optional[int], Optional[int]]:
    loc = _location_from_mapping(item)
    page_number = loc.get("page_number")
    if page_number is None:
        page_number = _coerce_location_int(item.get("page"))
    sentence_number = loc.get("sentence_number")
    if top_chunks:
        primary = top_chunks[0]
        if page_number is None:
            page_number = primary.get("page_number")
        if sentence_number is None:
            sentence_number = primary.get("sentence_number")
    return page_number, sentence_number


def _secured_query_hit_to_row(item: Dict[str, Any]) -> Dict[str, Any]:
    """Map one /secured/query results[] entry to a docbridge search result row."""
    item = _merge_secured_query_hit(_prepare_secured_query_hit(item))
    pointer = item.get("pointer") or item.get("identifier") or ""
    top_chunks = _normalize_top_chunks(
        item.get("top_chunks") if isinstance(item.get("top_chunks"), list) else item.get("topChunks")
    )
    page_number, sentence_number = _primary_location_from_hit(item, top_chunks)
    document_date = _document_date_from_hit(item)
    cos_sim = item.get("cosine_similarity")
    if cos_sim is None:
        cos_sim = item.get("cosineSimilarity")
    cos_dist = item.get("cosine_distance")
    if cos_dist is None:
        cos_dist = item.get("cosineDistance")
    row: Dict[str, Any] = {
        "doc_id": pointer,
        "path": pointer,
        "score": _extract_semantix_query_similarity(item),
        "title": _display_title_for_hit(pointer, item.get("title")),
        "source": "semantix",
        "page_number": page_number,
        "page": page_number,
        "sentence_number": sentence_number,
        "cosine_similarity": cos_sim,
        "cosine_distance": cos_dist,
        "document_date": document_date,
        "date": document_date,
    }
    fs = item.get("final_score")
    if fs is None:
        fs = item.get("FinalScore")
    if fs is not None:
        row["final_score"] = fs
    summary = _ingested_summary_from_hit(item)
    if summary:
        row["ingested_summary"] = summary
    doc_uuid = item.get("document_uuid")
    if doc_uuid:
        row["document_uuid"] = str(doc_uuid)
    for url_key in ("web_url", "webUrl", "external_url"):
        url_val = item.get(url_key)
        if url_val and _is_http_url(str(url_val)):
            row["external_url"] = str(url_val).strip()
            break
    if top_chunks:
        row["top_chunks"] = top_chunks
    # Document fields (only on a request that asked for them). Copied as
    # Knovas sent them: a result shows fields only when the server returned
    # them (H4), and a values title replaces the file-name stem only when it
    # is a real title (doc_fields_view.title_from_values).
    fields = item.get("fields")
    if isinstance(fields, dict):
        row["fields"] = fields
        row["title"], row["title_from_values"] = _doc_fields_display_title(
            pointer, item.get("title"), fields.get("title"))
    for key in ("relevance_tier", "score_mode"):
        if isinstance(item.get(key), str):
            row[key] = item[key]
    return row


class KnowledgeGraphDisabled(RuntimeError):
    """Der Wissensgraph ist fuer dieses Deployment nicht aktiviert.

    Die API antwortet auf jede /secured/graph/*-Route mit 404 und
    error_code 'knowledge_graph_disabled' (siehe Knowledge_Graph_API.md).
    """


class GraphError(Exception):
    """A Knowledge Graph API call failed in a way the caller can act on.

    404 is deliberately NOT raised: an unknown or foreign id is the API's
    documented answer for "not yours", and every caller already treats None as
    that. What callers cannot currently distinguish is a 422 they should show
    the user from a 503 that means "retry once the operator finishes", which is
    what error_code carries.

    It carries the status, the Core's error code when it is a short
    identifier, and the Core's request id (RB-FIX-2) -- never the Core's
    text. ``message`` is accepted for the callers that pass one and not
    kept: the Core's text can quote what was sent (a name, a value), and
    this error reaches logs and tracebacks. A page says what happened in its
    own words, from the code; an operator finds the Core's full message in
    the Core's own logs by the request id.
    """

    def __init__(self, status: int, error_code: Optional[str], message: str = "",
                 *, details: Optional[Dict[str, Any]] = None, request_id: Optional[str] = None):
        self.status = status
        self.error_code = _code_or_none(error_code)
        self.request_id = request_id if request_id and request_id != '-' else None
        self.message = ""
        super().__init__(f"Knovas graph call failed: HTTP {status}, error_code {self.error_code or '-'}, "
                         f"request_id {self.request_id or '-'}")
        # Only what says WHERE the request went wrong (a JSON path, field
        # keys, a version) -- never anything that could carry a value.
        self.details = _error_details(details)


def _error_details(body: Any) -> Dict[str, Any]:
    """The parts of a Knovas error body a caller may act on.

    Document-fields errors name a JSON path (``set.doc_type[0]``), registry
    keys (``suggest``, ``candidates``), the current version, or per-key
    ``errors[{path, code}]``. The server never echoes a value (DF/errors.py);
    this whitelist keeps it that way on this side too, whatever arrives.
    """
    if not isinstance(body, dict):
        return {}
    out: Dict[str, Any] = {}
    path = body.get("path")
    if isinstance(path, str) and path:
        out["path"] = path[:200]
    for key in ("suggest", "candidates"):
        value = body.get(key)
        if isinstance(value, list):
            kept = [v for v in value if isinstance(v, str)]
            if kept:
                out[key] = kept
        elif key == "suggest" and isinstance(value, dict):
            # The init echo's form: {sent key: [registry keys]}.
            out[key] = {
                str(k): [v for v in vs if isinstance(v, str)]
                for k, vs in value.items() if isinstance(vs, list)
            }
    version = body.get("current_version")
    if isinstance(version, int) and not isinstance(version, bool):
        out["current_version"] = version
    errors = body.get("errors")
    if isinstance(errors, list):
        kept_errors = [
            {k: e[k] for k in ("path", "code") if isinstance(e.get(k), str)}
            for e in errors if isinstance(e, dict)
        ]
        kept_errors = [e for e in kept_errors if e]
        if kept_errors:
            out["errors"] = kept_errors
    return out


class DocFieldsUnavailable(RuntimeError):
    """Document fields are off for this tenant, or Knovas predates them.

    Every /secured/graph/doc-* route then answers 404 ``HTTP_404``, the same
    bytes as an unknown route (doc_fields_api.py:317-349). Distinct from
    DocFieldsError because the caller's answer is different: hide the
    feature (and tell the capability cache), rather than show an error.
    """

    def __init__(self, message: str = "Document fields are not enabled at Knovas",
                 status: int = 404, error_code: Optional[str] = "HTTP_404"):
        super().__init__(message)
        self.status = status
        self.error_code = _code_or_none(error_code)   # a short code only (RB-FIX-2)


class DocFieldsError(GraphError):
    """A document-fields call failed with an answer the caller can map.

    ``error_code`` is the server's code (``version_conflict``,
    ``unknown_field``, ...). Two codes are made on this side, so a caller
    never has to catch transport exceptions as well: ``transport_error``
    (status 503, no answer at all) and ``invalid_response`` (status 502, a
    2xx without a JSON object).
    """


class QueryRejected(Exception):
    """/secured/query refused a request that carried ``where`` or
    ``return_fields`` with a document-fields error code.

    Raised only for such requests. A query without either key fails exactly
    as it always has (HTTPError), so unfiltered search keeps today's errors.
    """

    def __init__(self, status: int, error_code: Optional[str],
                 details: Optional[Dict[str, Any]] = None, *, request_id: Optional[str] = None):
        self.status = status
        self.error_code = _code_or_none(error_code)   # a short code only (RB-FIX-2)
        self.request_id = request_id if request_id and request_id != '-' else None
        super().__init__(f"{status} {self.error_code or ''}".strip())
        self.details = _error_details(details)


# Error codes of the document-fields feature (DF/errors.py, the contract's
# error catalogue). A query error with one of these is about ``where`` or
# ``return_fields``; anything else is an ordinary query failure.
DOC_FIELDS_ERROR_CODES = frozenset({
    "invalid_fields", "fields_too_large", "ambiguous_field", "unknown_field",
    "invalid_value", "type_mismatch", "checksum_failed", "restricted_identifier",
    "key_looks_personal", "ambiguous_date", "ambiguous_number", "unresolved_entity",
    "ambiguous_entity",
    "if_version_required", "invalid_field_definition", "where_unsupported",
    "where_too_complex", "invalid_cursor", "change_not_authorized",
    "registry_write_requires_full_clearance", "version_conflict",
    "anchor_quarantined", "field_key_exists", "field_cap_reached",
    "field_type_locked", "doc_fields_unavailable", "doc_fields_ingest_unavailable",
    "where_unavailable", "where_requires_calibration", "pack_not_found",
})


def _is_doc_fields_code(code: Any) -> bool:
    return isinstance(code, str) and (
        code in DOC_FIELDS_ERROR_CODES or code.startswith("doc_fields_")
    )


def _query_rejection(exc: requests.exceptions.HTTPError, *,
                     where_sent: bool) -> Optional[QueryRejected]:
    """The QueryRejected an HTTPError on a doc-fields query stands for."""
    response = getattr(exc, "response", None)
    if response is None:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    code = body.get("error_code")
    # A query with ``where`` is always relevance-gated (QP:1085-1096), so a
    # missing relevance calibration is part of what ``where`` asked for.
    if _is_doc_fields_code(code) or (where_sent and code == "relevance_mode_calibration_missing"):
        return QueryRejected(response.status_code, code, body, request_id=_request_id(response))
    return None


# The most auto_scope node ids kept: the server's own bound on a scope
# (query_scope.MAX_SCOPE_IDS).
_AUTO_SCOPE_IDS_MAX = 200


def _auto_scope_echo(block: Dict[str, Any]) -> Dict[str, Any]:
    """``auto_scope`` as the Platform keeps it (spec F3): whether Knovas ran
    the search inside the nodes it recognised in the question (``applied``)
    or found nothing there and searched everything (``fallback``), and the
    node ids -- the detected ones first, then the rest of the scope, each
    once. Identifier ids, channels and scores stay behind: nothing on the
    page uses them. ``detections`` and ``node_ids`` count only as lists: a
    string is not one id per character, nor an object its keys."""
    detections, node_ids = block.get("detections"), block.get("node_ids")
    ids: List[str] = []
    for detection in detections if isinstance(detections, list) else ():
        if isinstance(detection, dict) and isinstance(detection.get("node_id"), str):
            ids.append(detection["node_id"])
    if isinstance(node_ids, list):
        ids.extend(i for i in node_ids if isinstance(i, str))
    unique = [i for i in dict.fromkeys(ids) if i][:_AUTO_SCOPE_IDS_MAX]
    return {"applied": block.get("applied") is True, "fallback": block.get("fallback") is True,
            "node_ids": unique}


def _secured_query_honesty(result: Dict[str, Any]) -> Dict[str, Any]:
    """What /secured/query says about how far its answer can be trusted.

    The scalars are always present (None from a server that predates them),
    so a caller reads one shape. ``where`` and ``return_fields`` appear only
    when Knovas echoed them: their absence means "not filtered" (H2);
    ``auto_scope`` (node ids only) when Knovas narrowed the search by a name.
    """
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}

    def _flag(value: Any) -> Optional[bool]:
        return value if isinstance(value, bool) else None

    reason = result.get("no_results_reason")
    out: Dict[str, Any] = {
        "no_strong_matches": _flag(result.get("no_strong_matches")),
        "no_results_reason": reason if isinstance(reason, str) else None,
        "relevance_gate_applied": _flag(result.get("relevance_gate_applied")),
        "degraded_to_bm25": _flag(meta.get("degraded_to_bm25")),
    }
    where = result.get("where")
    if isinstance(where, dict):
        out["where"] = {k: where[k] for k in ("applied", "clauses", "resolved", "may_be_partial")
                        if k in where}
    return_fields = result.get("return_fields")
    if isinstance(return_fields, dict):
        out["return_fields"] = {"applied": return_fields.get("applied")}
    auto_scope = result.get("auto_scope")
    if isinstance(auto_scope, dict):
        out["auto_scope"] = _auto_scope_echo(auto_scope)
    return out


def _doc_fields_body(payload: Any) -> Dict[str, Any]:
    """A doc-fields answer without the envelope's ``status`` / ``message``."""
    if not isinstance(payload, dict):
        return {}
    return {k: v for k, v in payload.items() if k not in ("status", "message")}


def _classify_doc_fields_probe(response: Any) -> str:
    """off / values / filters / unknown from the probe's answer (spec 2.2)."""
    status = getattr(response, "status_code", 0)
    try:
        body = response.json()
    except ValueError:
        body = None
    code = body.get("error_code") if isinstance(body, dict) else None
    if status == 404:
        # NOT_FOUND / pack_not_found are answers of a LIVE route; every other
        # 404 -- HTTP_404, knowledge_graph_disabled, a non-JSON page from an
        # old server or a proxy -- means the feature is not there.
        return "unknown" if code in ("NOT_FOUND", "pack_not_found") else "off"
    if status == 400 and code == "where_unsupported":
        return "values"
    if status == 400 and code == "invalid_value" and body.get("path") == "where":
        return "filters"
    return "unknown"


def _graph_payload_list(payload: Any, *candidate_keys: str,
                        strict: bool = False) -> List[Dict[str, Any]]:
    """Liste aus einer flachen Envelope ziehen.

    Die Graph-Spezifikation nennt die Schluesselnamen der Listen-Antworten
    nicht. Deshalb erst die plausiblen Namen probieren, dann auf die erste
    Liste im Objekt zurueckfallen - tolerant statt zu raten und zu brechen.

    strict=True schaltet den Rueckfall ab. Fuer eine Antwort, die mehr als eine
    Liste enthalten kann, ist Raten kein Entgegenkommen: gaebe eine Antwort mit
    nur "edges" ihre Kanten als Nachbarn aus, waeren das erfundene Knoten.
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in candidate_keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    if strict:
        return []
    for key, value in payload.items():
        if key in ('status', 'message') or not isinstance(value, list):
            continue
        return [item for item in value if isinstance(item, dict)]
    return []


# The field that carries a person across the mTLS boundary. KnowledgeBase reads
# this exact name from the JSON body (services/rbac/assertion.py, ASSERTION_FIELD).
# A cross-repo wire contract with no shared import: renaming either side is a
# silent break that surfaces as "search returns nothing" on a BROKERED tenant.
ASSERTION_FIELD = "principal_assertion"

# The declared actor (KnowledgeBase services/request_actor.py): the body's
# ``actor`` on a POST, ``?actor=<base64url JSON>`` on a GET -- never both, the
# Core refuses that. Declared, not verified: the Core records it and decides
# nothing on it. Sent only while an agent acts (RB-6: the Knovas Assistent
# answering a person, ``asserting_for(AgentPrincipal)``).
ACTOR_FIELD = "actor"


def _encode_actor(actor: Dict[str, Any]) -> str:
    """The ``?actor=`` form of an actor: base64url of compact UTF-8 JSON, no padding."""
    import base64
    import json as _json

    raw = _json.dumps(actor, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

# /secured/query refuses a limit above 50 with 422 (QP:91-103).
_SECURED_QUERY_MAX_LIMIT = 50
# POST /secured/graph/doc-values/find caps its page at 200
# (DOC_FIELDS_FIND_MAX_LIMIT); a larger limit would only be cut down there.
_DOC_FIELDS_FIND_MAX_LIMIT = 200


def flatten_access_groups(nodes: List[Any]) -> List[Dict[str, Any]]:
    """Lift nested ``children`` into a flat list the console can iterate.

    GET /secured/access_groups returns the tenant group *tree*. Creating a
    group with a parent stores it under that node. Every screen that does
    ``for g in groups`` would otherwise show only the roots.
    """

    def walk(items: List[Any], parent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for node in items or []:
            if not isinstance(node, dict):
                continue
            item = dict(node)
            children = item.pop("children", None) or []
            gid = item.get("group_id")
            if parent_id and not item.get("parent_id"):
                item["parent_id"] = parent_id
            out.append(item)
            out.extend(walk(children, str(gid) if gid else parent_id))
        return out

    # One entry per group. The walk appends every node it reaches, so a group
    # reachable at more than one place in the returned tree came back two or
    # three times -- as two or three identical checkboxes in the console, and
    # then as a repeated id in the posted form, which the sync-request schema
    # rejects outright ("access_groups ... has non-unique elements"). The first
    # occurrence is the shallowest, which is where it reads best in a flat list.
    seen: set = set()
    flat = []
    for item in walk(nodes):
        gid = str(item.get("group_id") or "")
        if gid and gid in seen:
            continue
        if gid:
            seen.add(gid)
        flat.append(item)
    names = {
        str(g.get("group_id")): g.get("name")
        for g in flat if g.get("group_id")
    }
    for group in flat:
        pid = group.get("parent_id")
        if pid and names.get(str(pid)):
            group["parent_name"] = names[str(pid)]
    return flat


class KnovasAPIClient:
    """Client for Knovas API operations."""
    
    def __init__(self, config_loader=None):
        """
        Initialize Knovas API client.
        
        Args:
            config_loader: ConfigLoader instance. If None, uses global config.
        """
        self.config = config_loader or get_config()
        # Set by attach_principal_broker() once the identity gate exists. None
        # means a legacy shared-login deployment: bodies go out unsigned.
        self._principal_broker = None
        
        self.base_url = self.config.get('api.base_url', 'http://localhost:5000')
        self.auth_type = self.config.get('api.auth_type', 'bearer')
        self.api_key = self.config.get('api.api_key', '')
        self.cert_path = self.config.get('api.cert_path', '')
        self.key_path = self.config.get('api.key_path', '')
        self.ca_cert_path = self.config.get('api.ca_cert_path', '')
        self.mtls_enabled = bool(self.cert_path and self.key_path and self.ca_cert_path)

        # Refuse to present client certificates / run the secured API over a
        # non-TLS connection. If mTLS is configured the base_url MUST be https.
        if self.mtls_enabled and not str(self.base_url).strip().lower().startswith('https://'):
            raise ValueError(
                "mTLS is enabled (client cert/key/CA configured) but base_url is "
                f"not https://: {self.base_url!r}. Refusing to use client "
                "certificates over a non-TLS connection."
            )
        self.customer_id = self.config.get('api.customer_id', '') or os.getenv('SEMANTIX_CUSTOMER_ID', '')
        self.cert_auto_renew_enabled = self.config.get_bool('api.cert_auto_renew_enabled', True)
        self.cert_renew_threshold_days = self.config.get_int('api.cert_renew_threshold_days', 30)
        self.cert_check_interval_seconds = self.config.get_int('api.cert_check_interval_seconds', 3600)
        self._last_cert_check_at = 0.0
        # Serialize the certificate freshness check / renewal / session swap so
        # concurrent request threads can't race on _last_cert_check_at or _session.
        # Reentrant by necessity, not preference: _ensure_certificate_freshness
        # holds this lock while renewing, and the renewal posts a CSR through
        # _request_no_retry, which calls _ensure_certificate_freshness again. On
        # a plain Lock that nested acquire never returns and the worker thread
        # is gone for good -- at renewal time, months after deployment.
        self._cert_lock = threading.RLock()

        self.endpoints = {
            'health': self.config.get('api.endpoints.health', '/secured/health'),
            'init_transmission': self.config.get('api.endpoints.init_transmission', '/secured/init_document_transmission'),
            'transmit_part': self.config.get('api.endpoints.transmit_part', '/secured/transmit_document_part'),
            'query': self.config.get('api.endpoints.query', '/secured/query'),
            'sign_certificate': self.config.get(
                'api.endpoints.sign_certificate', '/secured/sign_certificate'
            ),
            'delete_information_object': self.config.get(
                'api.endpoints.delete_information_object', '/secured/delete_information_object'
            ),
        }
        
        self.retry_attempts = self.config.get_int('api.rate_limit.retry_attempts', 3)
        self.retry_backoff = self.config.get_int('api.rate_limit.retry_backoff', 2)
        self.requests_per_second = self.config.get_int('api.rate_limit.requests_per_second', 5)
        # Secured search/query can exceed 30s over WAN or on heavy corpora; was hard-coded 30.
        env_timeout = (os.getenv("SEMANTIX_HTTP_READ_TIMEOUT") or "").strip()
        if env_timeout.isdigit():
            self.http_read_timeout = max(5, int(env_timeout))
        else:
            self.http_read_timeout = max(5, self.config.get_int("api.http_read_timeout", 30))
        
        self._last_request_time = 0
        self._request_interval = 1.0 / self.requests_per_second if self.requests_per_second > 0 else 0
        self._session = self._build_session()

    def _secured_query_request_body(
        self,
        query: Union[str, List[str]],
        limit: Optional[int] = None,
        where: Optional[Dict[str, Any]] = None,
        return_fields: Optional[Union[bool, List[str]]] = None,
    ) -> Dict[str, Any]:
        """The /secured/query body: the keys Knovas reads (1.5.0).

        ``Input`` and ``limit``; ``where`` and ``return_fields`` only when
        given, so a query without them is the body it has always been (D8).
        ``top_k``, ``filters`` and ``encryption_matrix`` are not sent: the
        server reads none of them (spec F7). ``limit`` is clamped to 50: the
        server answers 422 above it (QP:91-103), which made every "Mehr
        laden" past 50 a failed search.
        """
        if isinstance(query, list):
            inputs = [str(q).strip() for q in query if str(q).strip()]
            if not inputs:
                raise ValueError('Input must be a non-empty string or non-empty list of strings')
            body_input: Union[str, List[str]] = inputs if len(inputs) > 1 else inputs[0]
        else:
            q = str(query).strip()
            if not q:
                raise ValueError('Input must be a non-empty string or non-empty list of strings')
            body_input = q
        body: Dict[str, Any] = {'Input': body_input}
        if limit is not None and limit > 0:
            body['limit'] = min(int(limit), _SECURED_QUERY_MAX_LIMIT)
        if where is not None:
            body['where'] = where
        if return_fields is not None:
            body['return_fields'] = return_fields
        return body

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        if self.mtls_enabled:
            session.cert = (self.cert_path, self.key_path)
            session.verify = self.ca_cert_path
        return session

    def _parse_certificate_validity(self) -> Optional[datetime]:
        try:
            with open(self.cert_path, "rb") as f:
                cert = x509.load_pem_x509_certificate(f.read(), default_backend())
            return cert.not_valid_after_utc
        except Exception as exc:
            logger.warning("Could not parse cert expiry: %s", exc)
            return None

    def _atomic_write(self, path: str, content: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=str(target.parent), delete=False) as tmp:
            tmp.write(content)
            tmp_name = tmp.name
        os.replace(tmp_name, str(target))

    def _write_temp_pem(self, target_path: str, content: str) -> str:
        """Stage PEM content to a temp file in the target's directory; return its path."""
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(target.parent),
            delete=False,
            suffix=".pem.tmp",
        ) as tmp:
            tmp.write(content)
            return tmp.name

    def _validate_renewed_certificate(self, cert_path: str, key_path: str) -> bool:
        """
        Handshake/health check for a candidate cert/key pair BEFORE installing it.

        Builds a throwaway session using the candidate pair and calls the health
        endpoint. Injectable: tests monkeypatch this to force success/failure.
        """
        session = requests.Session()
        session.cert = (cert_path, key_path)
        if self.ca_cert_path:
            session.verify = self.ca_cert_path
        try:
            endpoint = self.endpoints.get('health', '/secured/health')
            resp = session.request(
                method='GET',
                url=f"{self.base_url}{endpoint}",
                headers=self._get_headers(),
                timeout=self.http_read_timeout,
                allow_redirects=False,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            logger.error("Renewed certificate validation failed: %s", exc)
            return False
        finally:
            session.close()

    def _csr_subject_from_current_cert(self) -> x509.Name:
        with open(self.cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read(), default_backend())
        return cert.subject

    def _generate_csr_key_pair(self) -> Tuple[str, str]:
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )
        subject = self._csr_subject_from_current_cert()
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(subject)
            .sign(private_key, hashes.SHA256(), default_backend())
        )
        csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("utf-8")
        key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        return csr_pem, key_pem

    def _attempt_certificate_renewal(self) -> bool:
        """Renew by CSR: a new key pair here, signed by /secured/sign_certificate."""
        try:
            csr_pem, private_key_pem = self._generate_csr_key_pair()
            payload = self.sign_certificate(csr_pem, validity_days=365)
        except Exception as exc:
            logger.error("CSR auto-renew request failed: %s", exc)
            return False

        certificate_pem = payload.get("certificate")
        if not certificate_pem:
            logger.error("CSR auto-renew response missing certificate")
            return False
        chain = payload.get("certificate_chain")
        if chain:
            certificate_pem = f"{str(certificate_pem).strip()}\n{str(chain).strip()}\n"
        return self._install_renewed_certificate_pair(
            str(certificate_pem).strip() + "\n",
            str(private_key_pem).strip() + "\n",
        )

    def _install_renewed_certificate_pair(self, certificate_pem: str, private_key_pem: str) -> bool:
        cert_tmp: Optional[str] = None
        key_tmp: Optional[str] = None
        try:
            cert_tmp = self._write_temp_pem(self.cert_path, certificate_pem)
            key_tmp = self._write_temp_pem(self.key_path, private_key_pem)

            if not self._validate_renewed_certificate(cert_tmp, key_tmp):
                logger.error(
                    "Auto-renew: candidate certificate failed validation; "
                    "keeping existing cert/key and session"
                )
                return False

            orig_cert = self._read_text_or_none(self.cert_path)
            orig_key = self._read_text_or_none(self.key_path)
            try:
                os.replace(cert_tmp, self.cert_path)
                cert_tmp = None
                os.replace(key_tmp, self.key_path)
                key_tmp = None
            except Exception as exc:
                logger.error("Auto-renew: install failed (%s); rolling back", exc)
                if orig_cert is not None:
                    self._atomic_write(self.cert_path, orig_cert)
                if orig_key is not None:
                    self._atomic_write(self.key_path, orig_key)
                return False

            self._session.close()
            self._session = self._build_session()
            logger.info("Certificate auto-renewed and rotated successfully")
            return True
        finally:
            for tmp in (cert_tmp, key_tmp):
                if tmp and os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass

    @staticmethod
    def _read_text_or_none(path: str) -> Optional[str]:
        try:
            if os.path.exists(path):
                return Path(path).read_text(encoding="utf-8")
        except OSError:
            return None
        return None

    def _ensure_certificate_freshness(self) -> None:
        if not self.mtls_enabled or not self.cert_auto_renew_enabled:
            return
        # Guard the check + renew + session swap so concurrent request threads
        # don't race on _last_cert_check_at / _session.
        with self._cert_lock:
            now_ts = time.time()
            if now_ts - self._last_cert_check_at < self.cert_check_interval_seconds:
                return
            self._last_cert_check_at = now_ts

            not_after = self._parse_certificate_validity()
            if not not_after:
                return
            remaining_days = (not_after - datetime.now(timezone.utc)).total_seconds() / 86400
            if remaining_days <= self.cert_renew_threshold_days:
                logger.info(
                    "Certificate expires in %.2f days (threshold=%s), attempting auto-renew",
                    remaining_days,
                    self.cert_renew_threshold_days
                )
                self._attempt_certificate_renewal()
    
    @contextlib.contextmanager
    def asserting_for(self, user):
        """Requests in this block carry ``user``'s principal assertion instead of the signed-in person's.

        For the access window's "explain access" (Brain Builder B14): an administrator asks the Brain
        whether *that* person may read a document, and the Brain answers with its own policy. And for the
        Knovas Assistent (RB-6): ``user`` is an ``identity.agents.AgentPrincipal``, every call of the block
        carries the assistant's claim narrowed to the person and declares the agent as its actor
        (``_with_principal``). Per thread, so a concurrent request of someone else keeps its own principal.
        """
        import threading

        local = getattr(self, "_principal_local", None)
        if local is None:
            local = self._principal_local = threading.local()
        previous = getattr(local, "user", None)
        local.user = user
        try:
            yield
        finally:
            local.user = previous

    @contextlib.contextmanager
    def within_time(self, time_left):
        """Every request in this block ends in time (PMVP-24, FU-08): ``time_left()`` gives the seconds that remain.

        For the Knovas Assistent, whose search with its retries and generation together have a deadline under the
        web server's (``assistant.core.ANSWER_DEADLINE_SECONDS``). Each request's timeout is the smaller of its own
        and the time left; with no time left nothing is sent and the request times out at once
        (``requests.exceptions.ReadTimeout``). ``_make_request`` (the search) retries only when, after its wait, at
        least ``MIN_ATTEMPT_SECONDS`` remain. Per thread, like ``keeping_snippets``. Off by default: outside the
        block a request waits ``http_read_timeout`` and a search tries three times, exactly as before.
        """
        local = getattr(self, "_time_local", None)
        if local is None:
            local = self._time_local = threading.local()
        previous = getattr(local, "left", None)
        local.left = time_left
        try:
            yield
        finally:
            local.left = previous

    def _time_left(self) -> Optional[float]:
        """The seconds ``within_time`` leaves this thread, or None outside it."""
        left = getattr(getattr(self, "_time_local", None), "left", None)
        return None if left is None else float(left())

    @contextlib.contextmanager
    def keeping_snippets(self):
        """Searches in this block keep each hit's passage text in its row as ``"snippet"``.

        For the Knovas Assistent (PMVP-20), which cites only what it can show
        and searches through the search page's own path (``run_search``). The
        request Knovas receives is the same; only the row keeps ``snippet``
        -- the longest text the answer carries for one chunk, at most
        ``ASSISTANT_PASSAGE_CHARS`` (default 1,200) characters, at least three
        words -- and the location of that chunk, ``snippet_page_number``
        and ``snippet_sentence_number`` (``_snippet_with_location``). Outside
        the block rows have none of them:
        the search page reads that key when it matches words
        (``_search_result_haystack``) and must behave as before. Per
        thread, like ``asserting_for``, so a concurrent search page request
        keeps its rows unchanged.
        """
        local = getattr(self, "_snippets_local", None)
        if local is None:
            local = self._snippets_local = threading.local()
        previous = getattr(local, "on", False)
        local.on = True
        try:
            yield
        finally:
            local.on = previous

    def attach_principal_broker(self, broker) -> None:
        """Bind the broker that signs the current user into every outbound body.

        ``broker`` offers ``current_user()`` and ``assertion_for(user)``. It is
        attached after construction because create_app() builds the identity
        gate after the client; the client itself stays ignorant of Flask.
        """
        self._principal_broker = broker

    def _declared_actor(self) -> Optional[Dict[str, Any]]:
        """The actor the principal in force declares, or None.

        Only an agent declares one (RB-6): the principal ``asserting_for``
        names offers ``declared_actor`` as a dict (``AgentPrincipal``). A
        person, the signed-in user included, declares none, as before.
        """
        principal = getattr(getattr(self, "_principal_local", None), "user", None)
        actor = getattr(principal, "declared_actor", None) if principal is not None else None
        return dict(actor) if isinstance(actor, dict) else None

    def _actor_params(self, method: Optional[str], params: Optional[Dict] = None) -> Optional[Dict]:
        """``params`` with ``?actor=`` on a GET while an agent acts (its body carries none), else unchanged."""
        actor = self._declared_actor()
        if actor is None or str(method or '').upper() != 'GET':
            return params
        return {**(params or {}), ACTOR_FIELD: _encode_actor(actor)}

    def _with_principal(self, data, method=None):
        """Attach the caller's assertion to an outgoing body.

        Fail closed when there is no authenticated user. An unsigned call
        resolves to asserted=False at the Secure API -- "unrestricted
        documents only" -- and returns *more* than a correctly scoped one.
        A wall that widens under failure is not a wall.

        While an agent acts (``asserting_for(AgentPrincipal)``, RB-6) the body
        also declares it as ``actor``, except on a GET (``method``), which
        declares it in the query string (``_actor_params``).
        """
        if self._principal_broker is None:
            return data
        # The access window asks "may this person read it?" as that person (asserting_for); otherwise the signed-in user.
        user = getattr(getattr(self, "_principal_local", None), "user", None) or self._principal_broker.current_user()
        if user is None:
            raise PermissionError(
                "No authenticated user for this request; refusing to call "
                "Knovas without a principal assertion."
            )
        assertion = self._principal_broker.assertion_for(user)
        if data is None:
            body = {ASSERTION_FIELD: assertion}
        elif not isinstance(data, dict):
            # Every secured endpoint takes a JSON object. A non-dict body is a
            # caller we have not accounted for; guessing how to attach the
            # assertion is how one route would quietly lose it.
            raise TypeError(
                f"Cannot attach a principal assertion to a {type(data).__name__} body."
            )
        else:
            body = {**data, ASSERTION_FIELD: assertion}
        actor = self._declared_actor()
        if actor is not None and str(method or 'POST').upper() != 'GET':
            body[ACTOR_FIELD] = actor
        return body

    def _get_headers(self) -> Dict[str, str]:
        """Get HTTP headers for API requests."""
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'DocBridge-Integration/1.0'
        }
        
        if self.api_key:
            if self.auth_type == 'bearer':
                headers['Authorization'] = f'Bearer {self.api_key}'
            elif self.auth_type == 'api_key':
                headers['X-API-Key'] = self.api_key
        
        return headers
    
    def _rate_limit(self):
        """Apply rate limiting between requests."""
        if self._request_interval > 0:
            elapsed = time.time() - self._last_request_time
            if elapsed < self._request_interval:
                sleep_time = self._request_interval - elapsed
                time.sleep(sleep_time)
        
        self._last_request_time = time.time()
    
    @retry(
        # PMVP-24: inside within_time a retry also needs MIN_ATTEMPT_SECONDS left after its wait.
        stop=(stop_after_attempt(3) | _out_of_time),
        wait=wait_exponential(multiplier=2, min=1, max=30),
        retry=retry_if_exception_type(_RETRYABLE_REQUEST_EXCEPTIONS)
    )
    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> requests.Response:
        """
        Make HTTP request with retry logic.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            data: Request body data
            params: Query parameters
            
        Returns:
            Response object
        """
        self._rate_limit()
        self._ensure_certificate_freshness()
        
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers()
        
        logger.debug("Knovas API %s %s", method, _route_name(endpoint))
        data = self._with_principal(data, method)
        params = self._actor_params(method, params)

        response = self._send(
            method, endpoint,
            url=url,
            json=data,
            params=params,
            headers=headers,
            timeout=self.http_read_timeout,
            allow_redirects=False,
        )

        if response.status_code >= 400:
            # Says which call failed and how, so an upstream 500 is not taken for
            # a fault in this deployment: the route, the status, the Core's error
            # code and its request id (the Core's own log line has the rest). Not
            # the body: it can quote a group's name, a question or a document (FU-43).
            logger.error(
                "Knovas API %s %s -> %s (error_code=%s, request_id=%s; body %d chars, not logged)",
                method, _route_name(endpoint), response.status_code,
                _error_code(response), _request_id(response), _body_size(response),
            )
        _raise_for_status(response, method, endpoint)
        return response

    def _request_no_retry(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        timeout: Optional[float] = None,
        as_person: bool = True,
    ) -> requests.Response:
        """
        Single HTTP request without tenacity retries (used for analytics feedback;
        avoids duplicate submissions on transient failures). ``timeout``
        replaces ``http_read_timeout`` for this one request. ``as_person=False``
        sends no assertion: only for a call that reads nothing a person's access
        could narrow.
        """
        self._rate_limit()
        self._ensure_certificate_freshness()
        url = f"{self.base_url}{endpoint}"
        if as_person:
            data = self._with_principal(data, method)
            params = self._actor_params(method, params)
        response = self._send(
            method, endpoint,
            url=url,
            json=data,
            params=params,
            headers=self._get_headers(),
            timeout=self.http_read_timeout if timeout is None else timeout,
            allow_redirects=False,
        )
        status = getattr(response, 'status_code', None)
        if isinstance(status, int) and status >= 400:
            # The caller logs what the failure means; this line keeps the Core's code and request id, by which an
            # operator finds the Core's own message in the Core's logs (RB-FIX-2). INFO: the caller's line is the
            # warning, and some of these answers are expected (a feature off, a route the Core lacks yet).
            logger.info(
                "Knovas API %s %s -> %s (error_code=%s, request_id=%s; body %d chars, not logged)",
                method, _route_name(endpoint), status,
                _error_code(response), _request_id(response), _body_size(response),
            )
        _raise_for_status(response, method, endpoint)
        return response

    def _request_quiet(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        timeout: Optional[float] = None,
    ) -> requests.Response:
        """One request, the assertion attached, and nothing else.

        No tenacity, no raise_for_status and no ERROR line with the body:
        the answer is returned whatever its status. Used by the
        document-fields probe, whose expected answers are a 404 or a 400 --
        through _make_request every worker would log an ERROR per probe on a
        server with the feature off -- and by graph_node_name, whose 404 is
        an ordinary answer too. Transport errors still raise. ``timeout``
        replaces ``http_read_timeout`` for this one request.
        """
        # The assertion first: with nobody signed in nothing is sent, and a
        # refused call should not cost a rate-limit slot either.
        data = self._with_principal(data, method)
        # ?actor= only while an agent acts on a GET: otherwise the call is exactly as before.
        extra = {}
        params = self._actor_params(method)
        if params:
            extra['params'] = params
        self._rate_limit()
        self._ensure_certificate_freshness()
        return self._send(
            method, endpoint,
            url=f"{self.base_url}{endpoint}",
            json=data,
            headers=self._get_headers(),
            timeout=self.http_read_timeout if timeout is None else timeout,
            allow_redirects=False,
            **extra,
        )

    def _send(self, method: str, endpoint: str, **kwargs: Any) -> requests.Response:
        """``self._session.request(method=method, **kwargs)``; no answer raises ``_transport_error`` (RB-FIX-2).

        Every request path of the client goes through here, so an error for no answer (no connection, a timeout, a
        TLS failure) never carries the URL -- the path's ids and the query string -- into a caller's log line or a
        route's traceback. ``endpoint`` is the path the route's name is read from.
        """
        left = self._time_left()
        if left is not None:  # within_time (PMVP-24): no longer than the time left, and nothing sent without any
            if left <= 0:
                raise _transport_error(requests.exceptions.ReadTimeout("no time left"), method, endpoint)
            kwargs["timeout"] = _capped(kwargs.get("timeout"), left)
            return self._send_in_time(method, endpoint, left, kwargs)
        try:
            return self._session.request(method=method, **kwargs)
        except requests.exceptions.RequestException as exc:
            failure = _transport_error(exc, method, endpoint)
        raise failure

    def _send_in_time(self, method: str, endpoint: str, left: float, kwargs: Dict[str, Any]) -> requests.Response:
        """A request inside ``within_time``: it ends by the wall clock, however slowly the peer answers (PMVP-24).

        A read timeout bounds each read only: a peer that sends its headers and then a byte every few seconds would
        keep the request, and the person, waiting past the deadline. So the request runs in a worker thread
        (``assistant.core.call_within``) with its body streamed and read in the pieces the peer sends
        (``_read_body``), and the caller waits no longer than the request's own read timeout or the time left,
        whichever is smaller. Past it the caller gets a ``ReadTimeout`` (the assistant reads it as busy, and the
        search makes no retry without time for it); the worker stops at the peer's next byte and closes the answer.
        A peer that trickles even its headers keeps only that worker, never the caller.
        """
        from assistant.core import OutOfTime, call_within

        time_left = self._time_local.left
        session = self._session
        kwargs = {k: v for k, v in kwargs.items() if k != "stream"}

        def request() -> requests.Response:
            response = session.request(method=method, stream=True, **kwargs)
            try:
                _read_body(response, time_left)
            except BaseException:
                response.close()
                raise
            return response

        try:
            return call_within(min(left, _read_part(kwargs.get("timeout"))), request, name="request")
        except OutOfTime:
            failure: requests.exceptions.RequestException = requests.exceptions.ReadTimeout("past the deadline")
        except requests.exceptions.RequestException as exc:
            failure = exc
        raise _transport_error(failure, method, endpoint)

    def secured_mode(self) -> bool:
        """Whether this client speaks the secured API over mTLS (client
        certificate, key and CA configured). Without them it searches nothing.
        """
        return bool(self.mtls_enabled)

    # -- External agents' claims for the Core's MCP gateway (RB-7, CR-R5) ----------------------------------------
    # The Platform's own calls: no person's principal_assertion, no access_groups and no actor
    # (as_person=False). The claims carry the authority; the Core verifies each with the tenant's broker key.
    # Sent once, never retried (the next push replaces the pool anyway), and no body is logged: it holds
    # bearer claims.

    def put_agent_claims(self, agent_id: str, claims: List[str], *, timeout: Optional[float] = None) -> None:
        """``PUT /secured/agents/<agent id>/claims`` with exactly ``{"claims": [...]}``: the agent's whole pool.

        Replaces the pool the Core holds for the agent (CR-R5). Raises the
        ``requests`` error of a refusal (404 without CR-R5, 409
        ``not_brokered``, 400 ``invalid_claims``, 5xx) or of no connection;
        the caller logs its class and status, never its text.
        """
        self._request_no_retry('PUT', f"/secured/agents/{quote(str(agent_id), safe='')}/claims",
                               data={'claims': list(claims)}, timeout=timeout, as_person=False)

    def delete_agent_claims(self, agent_id: str, *, timeout: Optional[float] = None) -> None:
        """``DELETE /secured/agents/<agent id>/claims``: the Core drops the agent's pool at once (CR-R5).

        No body. Raises as ``put_agent_claims``.
        """
        self._request_no_retry('DELETE', f"/secured/agents/{quote(str(agent_id), safe='')}/claims",
                               timeout=timeout, as_person=False)

    def delete_all_documents(self, confirm_client_id: str) -> Dict[str, Any]:
        """DELETE /secured/delete_all_documents — erase the whole corpus.

        There is no undo. Knovas cannot restore the documents afterwards and
        nobody else can without uploading them again.

        ``confirm_client_id`` must equal this tenant's own id. Per Secure_API.md
        it is a typo guard rather than a second credential — the server takes the
        tenant from the certificate either way and rejects a body naming anyone
        else, so there is no way to point this at another tenant. It is compared
        locally as well, so a mismatched confirmation never leaves the process.

        Uses ``_request_no_retry``: tenacity would repeat the call on a timeout,
        and a destructive request is the one kind that must not be replayed
        because the caller could not read the answer.

        Raises:
            ValueError: the confirmation is missing or does not match. Nothing
                was sent.
        """
        confirm = str(confirm_client_id or '').strip()
        own = str(getattr(self, 'customer_id', '') or '').strip()
        if not confirm:
            raise ValueError('Ohne Bestaetigung der Mandanten-Id wird nichts geloescht.')
        if not own:
            raise ValueError(
                'Die eigene Mandanten-Id ist nicht bekannt (SEMANTIX_CUSTOMER_ID); '
                'ohne sie laesst sich die Bestaetigung nicht pruefen.'
            )
        if confirm != own:
            raise ValueError(
                'Die Bestaetigung entspricht nicht der Mandanten-Id dieses Zertifikats.'
            )
        logger.warning(
            'Loesche den gesamten Dokumentbestand des Mandanten %s. Nicht umkehrbar.',
            own,
        )
        response = self._request_no_retry(
            'DELETE',
            '/secured/delete_all_documents',
            data={'confirm_client_id': own},
        )
        return response.json() if response.content else {}

    def delete_information_object(self, pointer: str) -> Dict[str, Any]:
        """DELETE /secured/delete_information_object — remove document by pointer."""
        if not pointer or not str(pointer).strip():
            raise ValueError('pointer is required')
        endpoint = self.endpoints.get(
            'delete_information_object', '/secured/delete_information_object'
        )
        response = self._request_no_retry(
            'DELETE',
            endpoint,
            data={'pointer': str(pointer).strip()},
        )
        return response.json() if response.content else {}

    def upload_text_document(
        self,
        identifier: str,
        *,
        title: str,
        description: str,
        path: str,
        parts: List[Dict[str, Any]],
        access_groups: Optional[Union[List[str], Tuple[str, ...]]] = None,
    ) -> Dict[str, Any]:
        """Upload a document the Platform wrote itself (Markdown parts).

        Same wire protocol as a file sync -- init, then one transmit per part --
        but the text is already there, so no extraction runs. Used by the
        experiments indexer, whose documents must reach Knovas with their
        access groups: ``access_groups`` goes into the init body whenever it
        is non-empty (an empty list would read as "no restriction").

        Uses ``_request_no_retry`` throughout: a retried init or transmit
        after the server committed would duplicate document parts.

        Raises:
            ValueError: no identifier, or no part with text. Nothing was sent.
            requests.HTTPError / ConnectionError / Timeout: from the transport;
                the caller decides whether to try again.
            RuntimeError: the init answer carried no transmission key.
        """
        ident = str(identifier or '').strip()
        if not ident:
            raise ValueError('identifier is required')
        clean_parts: List[Dict[str, Any]] = []
        for part in parts or []:
            if not isinstance(part, dict):
                continue
            snippet = str(part.get('snippet') or '')
            if not snippet.strip():
                continue
            clean_parts.append({**part, 'snippet': snippet})
        if not clean_parts:
            raise ValueError('at least one part with text is required')

        init_body: Dict[str, Any] = {'identifier': ident, 'part_count': len(clean_parts)}
        title_text = str(title or '').strip()[:500]
        if title_text:
            init_body['title'] = title_text
        description_text = str(description or '').strip()[:2000]
        if description_text:
            init_body['description'] = description_text
        norm_path = _normalize_semantix_path_for_init(path)
        if norm_path:
            init_body['path'] = norm_path
        groups = [
            g for g in dict.fromkeys(str(x).strip() for x in (access_groups or ()))
            if g
        ]
        if groups:
            init_body['access_groups'] = groups

        init_endpoint = self.endpoints.get('init_transmission', '/secured/init_document_transmission')
        init_resp = self._request_no_retry(method='POST', endpoint=init_endpoint, data=init_body)
        init_json = init_resp.json() if init_resp.content else {}
        transmission_key_id = (init_json or {}).get('transmission_key_id')
        if not transmission_key_id:
            raise RuntimeError('init_document_transmission returned no transmission_key_id')

        part_endpoint = self.endpoints.get('transmit_part', '/secured/transmit_document_part')
        for idx, part in enumerate(clean_parts):
            payload = _secured_transmit_part_payload(transmission_key_id, idx, part)
            self._request_no_retry(method='POST', endpoint=part_endpoint, data=payload)

        # Not the identifier: it names the document (FU-43). The init's request id finds it at the Core.
        logger.info("Text document uploaded: %d part(s), request_id=%s", len(clean_parts), _request_id(init_resp))
        return {'status': 'success', 'identifier': ident, 'part_count': len(clean_parts)}

    def sign_certificate(
        self,
        csr_pem: str,
        validity_days: Optional[int] = None,
        organisation: Optional[str] = None,
        as_person: bool = True,
    ) -> Dict[str, Any]:
        """POST /secured/sign_certificate — sign a tenant CSR (recommended renewal path).

        ``as_person=False``: a sync component enrolling has no signed-in person; its one-time token authorised this
        (builder.protocol.enrol). The route reads nothing, so the assertion's fail-closed rule has nothing to protect.
        """
        csr = str(csr_pem or '').strip()
        if not csr or 'BEGIN CERTIFICATE REQUEST' not in csr:
            raise ValueError('csr must be a PEM certificate signing request')
        body: Dict[str, Any] = {'csr': csr}
        if validity_days is not None:
            days = int(validity_days)
            if days < 1 or days > 1095:
                raise ValueError('validity_days must be 1–1095')
            body['validity_days'] = days
        if organisation is not None:
            org = str(organisation).strip()
            if org:
                body['organisation'] = org
        endpoint = self.endpoints.get('sign_certificate', '/secured/sign_certificate')
        response = self._request_no_retry('POST', endpoint, data=body, as_person=as_person)
        return response.json() if response.content else {}
    
    def search_documents(
        self,
        query: Union[str, List[str]],
        limit: int = 20,
        filters: Optional[Dict[str, Any]] = None,
        *,
        where: Optional[Dict[str, Any]] = None,
        return_fields: Optional[Union[bool, List[str]]] = None,
    ) -> Dict[str, Any]:
        """
        Search documents in Knovas.
        
        Args:
            query: Search query string
            limit: Maximum number of results
            filters: Not sent: /secured/query reads no filters (spec F7); the
                Platform applies its own filters to the answer.
            where: Document-fields filter; sent only when given.
            return_fields: Document-field keys to return per hit; sent only
                when given.

        Inside ``keeping_snippets()`` the secured rows also carry the Core's
        passage text as ``"snippet"``.
            
        Returns:
            Search results

        Raises:
            RuntimeError: no mTLS certificate paths are configured; nothing
                was sent.
            QueryRejected: Knovas refused a request that carried ``where``
                or ``return_fields`` with a document-fields error code.
        """
        if not self.mtls_enabled:
            raise RuntimeError(
                "mTLS cert paths are not configured. "
                "Set SEMANTIX_CLIENT_CERT, SEMANTIX_CLIENT_KEY and SEMANTIX_CA_CERT."
            )
        extra: Dict[str, Any] = {}
        if where is not None:
            extra['where'] = where
        if return_fields is not None:
            extra['return_fields'] = return_fields
        return self._search_documents_secured(query=query, limit=limit, **extra)

    def llm_generate(
        self,
        messages: List[Dict[str, str]],
        *,
        system_instruction: Optional[str] = None,
        max_tokens: int = 512,
        temperature: Optional[float] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """POST /secured/llm/generate: one generation, as the signed-in person.

        Body: ``messages`` (``role`` and ``content`` of each), ``max_tokens``,
        and ``system_instruction`` and ``temperature`` when given; the
        principal assertion is attached as on every secured call. Keeping the
        Core's limits (32 messages, roles user/assistant, 50,000 bytes per
        message, 4,000 bytes of system instruction, max_tokens <= 2,048) is
        the caller's job: a 400 is a CoreFailed like any other.

        One request, never retried (``_request_no_retry``): a generation
        repeated behind the caller's back is billed twice and may answer
        differently. No response body is logged -- an error body of this route
        could hold generated text -- only the status, the duration and the
        token counts. ``timeout`` defaults to ``api.llm_generate_timeout``
        (120 s), at least ``http_read_timeout``: an answer of a thousand
        tokens takes longer than a search.

        Returns ``{"content", "model", "prompt_tokens", "completion_tokens"}``
        (see ``_llm_generate_result``; the model name is for operators, never
        for the person asking).

        Raises:
            CoreBusy: HTTP 429 or 503.
            CoreFailed: a timeout, a connection error, any other HTTP error,
                or an answer that is not a success envelope with text content.
            PermissionError: nobody is signed in (``_with_principal``).
        """
        endpoint = self.config.get('api.endpoints.llm_generate', '/secured/llm/generate')
        body: Dict[str, Any] = {
            'messages': [{'role': m['role'], 'content': m['content']} for m in messages],
            'max_tokens': int(max_tokens),
        }
        if system_instruction is not None:
            body['system_instruction'] = system_instruction
        if temperature is not None:
            body['temperature'] = float(temperature)
        if timeout is None:
            timeout = max(self.http_read_timeout,
                          self.config.get_int('api.llm_generate_timeout', 120))

        started = time.monotonic()

        def elapsed_ms() -> int:
            return int((time.monotonic() - started) * 1000)

        try:
            response = self._request_no_retry('POST', endpoint, data=body, timeout=timeout)
        except requests.exceptions.HTTPError as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            logger.warning("Knovas generation refused: HTTP %s after %d ms", status, elapsed_ms())
            if status in (429, 503):
                raise CoreBusy(status=status) from None
            raise CoreFailed('http_error', status=status) from None
        except requests.exceptions.Timeout:
            logger.warning("Knovas generation timed out after %d ms", elapsed_ms())
            raise CoreFailed('timeout') from None
        except requests.exceptions.RequestException as exc:
            logger.warning("Knovas generation failed (%s) after %d ms",
                           type(exc).__name__, elapsed_ms())
            raise CoreFailed('transport') from None

        try:
            payload = response.json()
        except ValueError:
            payload = None
        result = _llm_generate_result(payload)
        if result is None:
            logger.warning("Knovas generation: HTTP %s but no success envelope with text",
                           response.status_code)
            raise CoreFailed('malformed', status=response.status_code)
        logger.info("Knovas generation: HTTP %s, %d ms, prompt_tokens=%s completion_tokens=%s",
                    response.status_code, elapsed_ms(),
                    result['prompt_tokens'], result['completion_tokens'])
        return result

    def document_text(
        self,
        document_uuid: Any,
        *,
        offset: int = 0,
        max_chars: int = DOCUMENT_TEXT_MAX_PART_CHARS,
        timeout: Optional[float] = None,
        deadline: Optional[float] = None,
    ) -> Dict[str, Any]:
        """GET /secured/document/<uuid>/text: one part of a document's indexed text (PMVP-20 context).

        For the Knovas Assistent only (``assistant.core.make_read_text``), under the founder's limits of 2026-11-22
        (``assistant/context.py``): it reads the documents the person's own search just returned, to give the model
        the text around each hit. The Core answers the text in reading order, at most 20,000 characters a part
        (``offset``, ``max_chars``); the tenant is the certificate's, and a document the principal may not see
        answers the 404 of a missing one.

        As the person: the principal in force is attached as on every call (``_with_principal``; inside
        ``asserting_for`` the assistant's claim, and ``?actor=``), through the same client as the search. The Core
        takes no claim on this GET yet (Core request CR-1c). One request, never retried: the caller has a time
        budget, and every read is billed.

        By the wall clock (``_get_by_deadline``): the answer is in by ``deadline`` (a ``time.monotonic()`` value;
        else ``timeout`` seconds from now, else ``http_read_timeout``) or the read is given up there with a
        ``ReadTimeout``. A socket timeout alone restarts with every packet, so a Core that trickles its answer held one
        question for 47.6 s against a budget of 8 (PMVP-20 context's verification).

        ``document_uuid`` must be a UUID; the path holds its canonical form only, so no other value reaches the
        address. A failure is logged as every call's is (RB-FIX-1, RB-FIX-2): the route as a template, the status,
        the Core's short code, its request id and the body's size -- never the document's id, the text or the body.

        Returns ``{"text", "offset", "next_offset", "total_chars", "truncated"}`` (``next_offset`` None on the last
        part).

        Raises:
            ValueError: ``document_uuid`` is not a UUID or the paging is out of range (nothing was sent); or the
                answer is not a document-text part.
            RuntimeError: the secured API is not configured (nothing was sent).
            PermissionError: nobody is signed in (nothing was sent).
            requests.exceptions.HTTPError: any status of 400 or more (the error names the route, not the id).
            requests.exceptions.RequestException: no answer (a timeout, no connection, not complete by the
                deadline), without the address.
        """
        if deadline is None:
            deadline = time.monotonic() + (self.http_read_timeout if timeout is None else timeout)
        try:
            document = str(_uuid.UUID(document_uuid)) if isinstance(document_uuid, str) else None
        except ValueError:
            document = None
        if document is None:
            raise ValueError("document_uuid is not a UUID")
        if not _whole_number(offset) or not (_whole_number(max_chars)
                                             and 1 <= max_chars <= DOCUMENT_TEXT_MAX_PART_CHARS):
            raise ValueError("offset must be a whole number and max_chars from 1 to 20000")
        if not self.mtls_enabled:
            raise RuntimeError("mTLS cert paths are not configured; the document text is read on the secured API only")
        endpoint = f"/secured/document/{document}/text"
        response = self._get_by_deadline(endpoint, {'offset': offset, 'max_chars': max_chars}, deadline)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        part = _document_text_part(payload)
        if part is None:
            logger.info(
                "Knovas API GET %s -> %s but no document text part (request_id=%s; body %d chars, not logged)",
                _route_name(endpoint), response.status_code, _request_id(response), _body_size(response),
            )
            raise ValueError(f"Knovas API GET {_route_name(endpoint)}: the answer is not a document text part")
        return part

    def _get_by_deadline(self, endpoint: str, params: Dict[str, Any], deadline: float) -> requests.Response:
        """One GET as the person in force, complete by ``deadline`` (``time.monotonic()``) or given up there.

        ``_request_no_retry``'s steps, with the waiting bounded by the wall clock, for ``document_text``. The rate
        limit, the certificate check, the principal and the actor run on the caller's thread (``asserting_for`` is
        per thread). The request and the reading of its body run on a worker thread, and the caller waits for it only
        until ``deadline``: a socket timeout restarts with every packet, so neither the headers nor the body of a
        trickling answer can hold the caller longer. The worker streams the body and stops itself at its next piece
        after the deadline, past ``DOCUMENT_TEXT_MAX_BODY_BYTES``, or at its socket timeout (the time left when the
        read began), and closes an answer it did not read to the end (its connection is not reused). Nothing it reads
        after the deadline is used or logged.

        Raises ``ReadTimeout`` naming the route when nothing complete came by the deadline, ``ValueError`` for an
        answer larger than ``DOCUMENT_TEXT_MAX_BODY_BYTES``, what ``_send`` raises for no answer (without the
        address), and ``HTTPError`` naming the route for a status of 400 or more (after the line every failed call
        gets: route, status, short code, request id, body size).
        """
        route = _route_name(endpoint)
        late = f"Knovas API GET {route}: no complete answer by the deadline (ReadTimeout)"
        self._rate_limit()
        self._ensure_certificate_freshness()
        data = self._with_principal(None, 'GET')
        params = self._actor_params('GET', params)
        left = deadline - time.monotonic()
        if left <= 0:
            raise requests.exceptions.ReadTimeout(late)
        box: Dict[str, Any] = {}

        def fetch() -> None:
            try:
                response = self._send(
                    'GET', endpoint, url=f"{self.base_url}{endpoint}", json=data, params=params,
                    headers=self._get_headers(), timeout=(left, left), allow_redirects=False, stream=True)
                box['response'] = response
                if isinstance(getattr(response, '_content', False), (bytes, bytearray)):
                    body = bytes(response._content)            # already read (a test double, or a cached answer)
                else:
                    pieces = bytearray()
                    for piece in response.iter_content(chunk_size=1024):
                        pieces += piece
                        if box.get('abandoned') or time.monotonic() >= deadline:
                            raise _StopReading('late')
                        if len(pieces) > DOCUMENT_TEXT_MAX_BODY_BYTES:
                            raise _StopReading('large')
                    body = bytes(pieces)
                if len(body) > DOCUMENT_TEXT_MAX_BODY_BYTES:
                    raise _StopReading('large')
                response._content = body
                box['done'] = True
            except _StopReading as stop:
                box['stop'] = str(stop)
            except requests.exceptions.RequestException as exc:
                box['error'] = _transport_error(exc, 'GET', endpoint)
            except Exception as exc:  # noqa: BLE001 - handed to the caller as it is
                box['error'] = exc
            finally:
                # Not read to its end: close it here, on the worker (its connection is not reused). The caller never
                # closes it: that would wait on the reader the worker is blocked in.
                if not box.get('done') and box.get('response') is not None:
                    try:
                        box['response'].close()
                    except Exception:  # noqa: BLE001 - a connection that is already gone
                        pass

        worker = threading.Thread(target=fetch, name="knovas-document-text", daemon=True)
        worker.start()
        worker.join(max(0.0, deadline - time.monotonic()))
        if worker.is_alive():
            box['abandoned'] = True      # the worker stops at its next piece, or at its socket timeout
            raise requests.exceptions.ReadTimeout(late)
        if box.get('stop') == 'late':
            raise requests.exceptions.ReadTimeout(late)
        if box.get('stop') == 'large':
            raise ValueError(f"Knovas API GET {route}: the answer is larger than a document text part")
        if 'error' in box:
            raise box['error']
        response = box['response']
        status = getattr(response, 'status_code', None)
        if isinstance(status, int) and status >= 400:
            logger.info(
                "Knovas API GET %s -> %s (error_code=%s, request_id=%s; body %d chars, not logged)",
                route, status, _error_code(response), _request_id(response), _body_size(response),
            )
        _raise_for_status(response, 'GET', endpoint)
        return response

    def health_check(self) -> bool:
        """
        Check if Knovas API is healthy.
        
        Returns:
            True if API is healthy, False otherwise
        """
        try:
            endpoint = self.endpoints.get('health', '/secured/health')
            response = self._make_request(method='GET', endpoint=endpoint)
            
            is_healthy = response.status_code == 200
            logger.info(f"Health check: {'OK' if is_healthy else 'FAILED'}")
            return is_healthy
            
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Knowledge Graph (Cortex)
    # Spezifikation: KnowledgeBase docs/Knovas_Developer_Kit/api/
    #                Knowledge_Graph_API.md
    # Alle Routen liegen unter /secured/graph und antworten in derselben
    # flachen Envelope wie der Rest der Secure-API.
    # ------------------------------------------------------------------

    def _graph_request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Graph-Aufruf. Gibt den geparsten Body zurueck.

        404 bedeutet laut Spezifikation entweder 'Feature aus' (error_code
        knowledge_graph_disabled) oder 'Id unbekannt bzw. nicht deine' -
        letzteres ist ein normaler Zustand und liefert None, kein Fehler.
        """
        endpoint = f"/secured/graph{path}"
        try:
            response = self._make_request(method=method, endpoint=endpoint,
                                          data=data, params=params)
        except requests.exceptions.HTTPError as exc:
            response = exc.response
            if response is None:
                raise
            if response.status_code != 404:
                try:
                    failure = response.json() or {}
                except ValueError:
                    failure = {}
                if not isinstance(failure, dict):
                    failure = {}
                # The status, a short code and the request id; not the Core's
                # text, which can quote what was sent (RB-FIX-2). _make_request
                # logged the route; the Core's own log has the text by the id.
                raise GraphError(
                    response.status_code,
                    failure.get('error_code'),
                    details=failure,
                    request_id=_request_id(response),
                ) from exc
            body: Dict[str, Any] = {}
            try:
                body = response.json() or {}
            except ValueError:
                body = {}
            if body.get('error_code') == 'knowledge_graph_disabled':
                raise KnowledgeGraphDisabled(
                    'Der Wissensgraph ist fuer dieses Deployment nicht aktiviert'
                ) from exc
            if method.upper() != 'GET':
                # For a GET, "unknown or not yours" is ordinary. For a write it
                # means the write did not happen, and the caller can only see
                # None -- which is why a refused curation looked like a missing
                # name in the console.
                logger.error(
                    "Knovas API %s %s -> 404, so nothing was written "
                    "(error_code=%s, request_id=%s; body %d chars, not logged)",
                    method, _route_name(endpoint), _error_code(response),
                    _request_id(response), _body_size(response),
                )
            else:
                logger.info("Graph 404 (unbekannte oder fremde Id): %s %s", method, _route_name(endpoint))
            return None
        if response.status_code in (204, 205):
            # Documented "done, nothing to say". Parsing it fails, and the {}
            # that came back was read by every caller as a failed write -- a
            # successful delete reported as an error.
            return {'status': 'success'}
        try:
            payload = response.json()
        except ValueError:
            logger.warning(
                "Graph-Antwort ohne JSON-Body: %s %s -> %s (request_id=%s; body %d chars, not logged)",
                method, _route_name(endpoint), response.status_code,
                _request_id(response), _body_size(response),
            )
            return {}
        if method.upper() != 'GET' and not payload:
            # A 2xx with an empty body. The status says the call was accepted and
            # the body says nothing was created, and the caller reads the falsy
            # payload as failure. Neither _make_request (no error status) nor the
            # branch above (valid JSON) says a word, so without this the write
            # fails in complete silence.
            logger.error(
                "Knovas API %s %s -> %s with an empty body; treating "
                "the write as failed (request_id=%s; body %d chars, not logged)",
                method, _route_name(endpoint), response.status_code,
                _request_id(response), _body_size(response),
            )
        return payload or {}

    def graph_export(self) -> Dict[str, Any]:
        """GET /secured/graph - vollstaendiger Topologie-Export."""
        return self._graph_request('GET', '') or {}

    def graph_node_types(self) -> List[Dict[str, Any]]:
        """GET /secured/graph/node-types - Typ-Vokabular."""
        return _graph_payload_list(self._graph_request('GET', '/node-types'),
                                   'node_types', 'nodeTypes', 'types')

    def graph_nodes(self, node_type_id: Optional[str] = None,
                    q: Optional[str] = None) -> List[Dict[str, Any]]:
        """GET /secured/graph/nodes - serverseitig gefiltert.

        Der Endpunkt kennt node_type_id und q (ILIKE auf name). Die ganze
        Topologie zu ziehen und in Python zu filtern kostet eine Anfrage, die
        mit dem Mandanten waechst, fuer eine Antwort, die die Datenbank
        bereits hat.
        """
        params: Dict[str, Any] = {}
        if node_type_id:
            params['node_type_id'] = node_type_id
        if q:
            params['q'] = q
        return _graph_payload_list(
            self._graph_request('GET', '/nodes', params=params), 'nodes')

    def graph_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """GET /secured/graph/nodes/<id> - Detail inkl. Zuordnungen und Fakten."""
        return self._graph_request('GET', f'/nodes/{quote(str(node_id), safe="")}')

    def graph_node_name(self, node_id: str, timeout: float) -> Optional[str]:
        """The name of one node as the signed-in person sees it: one
        ``GET /secured/graph/nodes/<id>``, for the auto-scope notice above
        search results (spec F3).

        None when Knovas answers 404 -- an unknown id, a node this person
        may not see (the graph answers 404, never 403) or the graph switched
        off -- or when the node has no name. The name is optional and a
        search must not wait for it: one request with the caller's
        ``timeout``, no retries, nothing logged (_request_quiet). Any other
        status raises GraphError (code only), a network error or timeout
        raises as requests raises it, and PermissionError when nobody is
        signed in (nothing is sent then).
        """
        response = self._request_quiet(
            'GET', f'/secured/graph/nodes/{quote(str(node_id), safe="")}', timeout=timeout)
        if response.status_code == 404:
            return None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code >= 400:
            code = payload.get('error_code') if isinstance(payload, dict) else None
            raise GraphError(response.status_code, code if isinstance(code, str) else None,
                             request_id=_request_id(response))
        node = payload.get('node') if isinstance(payload, dict) else None
        if not isinstance(node, dict):
            node = payload if isinstance(payload, dict) else {}
        name = node.get('name')
        return name.strip() or None if isinstance(name, str) else None

    def graph_edges(self) -> List[Dict[str, Any]]:
        """GET /secured/graph/edges - typisierte Relationen."""
        return _graph_payload_list(self._graph_request('GET', '/edges'), 'edges')

    def graph_neighbors(self, node_id: str, depth: int = 1,
                        include_edges: bool = False) -> Dict[str, Any]:
        """GET /secured/graph/nodes/<id>/neighbors - Traversal, max. 3 Hops.

        Liefert {"neighbors": [...], "edges": [...]}. Der Endpunkt laesst den
        edges-Schluessel weg, wenn include_edges nicht verlangt wird; hier auf
        eine leere Liste normalisiert, damit Aufrufer nie auf einen fehlenden
        Schluessel verzweigen muessen.

        include_edges ist Backend-Task A2 und noch nicht ausgerollt. Ein Server
        ohne diesen Parameter antwortet weiterhin nur mit den Nachbarn: dann
        bleibt edges leer, statt die Knotenliste als Kanten auszugeben - erfundene
        Relationen waeren schlimmer als gar keine.
        """
        depth = max(1, min(3, int(depth)))
        params: Dict[str, Any] = {'depth': depth}
        if include_edges:
            params['include_edges'] = 'true'
        payload = self._graph_request(
            'GET', f'/nodes/{quote(str(node_id), safe="")}/neighbors',
            params=params) or {}
        edges = payload.get('edges') if isinstance(payload, dict) else None
        return {
            'neighbors': _graph_payload_list(payload, 'neighbors', 'nodes',
                                             strict=True),
            'edges': ([item for item in edges if isinstance(item, dict)]
                      if isinstance(edges, list) else []),
        }

    def graph_get_many(
        self,
        calls: List[Tuple[str, Optional[Dict[str, Any]]]],
        *,
        workers: int = 4,
        acquire: Optional[Any] = None,
        max_retries: int = 3,
    ) -> List[Tuple[int, Any]]:
        """Several read-only ``GET /secured/graph<path>`` calls, in parallel.

        For the views (docs/superpowers/plans/2026-10-27-process-and-spine-views.md,
        D3): a board needs the facts of a few dozen nodes, and the 200 ms
        spacing of ``_rate_limit`` alone would put that over its budget. Pacing
        here is the caller's ``acquire()``, a token bucket kept under the
        tenant's limit; a 429 waits for its ``Retry-After`` and is tried again.

        Every call gets its own principal assertion, minted here on the
        request thread: an assertion is single-use (``jti``), and the worker
        threads have no request context to mint one from.

        Returns ``(status, payload)`` per call, in order; status 599 when no
        response came back. Nothing is raised for a single failed call, so one
        unreadable node does not blank a whole view.
        """
        from concurrent.futures import ThreadPoolExecutor

        if not calls:
            return []
        self._ensure_certificate_freshness()
        bodies = [self._with_principal(None, 'GET') for _ in calls]
        actor_params = self._actor_params('GET', {}) or {}
        headers = self._get_headers()

        def one(index: int) -> Tuple[int, Any]:
            path, params = calls[index]
            if actor_params:
                params = {**(params or {}), **actor_params}
            url = f"{self.base_url}/secured/graph{path}"
            status, payload = 599, None
            for attempt in range(max_retries + 1):
                if acquire is not None:
                    acquire()
                try:
                    response = self._send(
                        'GET', f"/secured/graph{path}", url=url, json=bodies[index], params=params,
                        headers=headers, timeout=self.http_read_timeout,
                        allow_redirects=False,
                    )
                except _RETRYABLE_REQUEST_EXCEPTIONS as exc:
                    logger.warning("Graph read %s failed: %s",
                                   _route_name(f"/secured/graph{path}"), type(exc).__name__)
                    continue
                status = response.status_code
                if status in (429, 503) and attempt < max_retries:
                    try:
                        wait = float(response.headers.get('Retry-After') or 1)
                    except ValueError:
                        wait = 1.0
                    time.sleep(min(max(wait, 0.1), 10.0))
                    continue
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                break
            return status, payload

        with ThreadPoolExecutor(max_workers=max(1, min(int(workers), len(calls)))) as pool:
            return list(pool.map(one, range(len(calls))))

    def graph_facts_batch(self, node_ids: List[str]) -> Tuple[int, Any]:
        """``POST /secured/graph/facts/batch``: the facts of up to 500 nodes in one read.

        Read-only despite the verb (the ids travel in a body). Nodes the caller
        may not see are left out of the answer. No retries: the caller reads
        the status. Returns ``(status, payload)``; 599 when no response came back.
        """
        ids = [str(n) for n in node_ids]
        if len(ids) > 500:
            raise ValueError("graph_facts_batch takes at most 500 node ids")
        self._ensure_certificate_freshness()
        body = self._with_principal({"node_ids": ids}, 'POST')
        try:
            response = self._send(
                'POST', '/secured/graph/facts/batch', url=f"{self.base_url}/secured/graph/facts/batch",
                json=body, headers=self._get_headers(),
                timeout=self.http_read_timeout, allow_redirects=False,
            )
        except _RETRYABLE_REQUEST_EXCEPTIONS as exc:
            logger.warning("Graph facts batch failed: %s", type(exc).__name__)
            return 599, None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return response.status_code, payload

    # -- Kuratieren (der Graph wird vom Client gepflegt, nicht abgeleitet) --

    def graph_create_node_type(self, name: str) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/node-types - Typ-Vokabular erweitern."""
        return self._graph_request('POST', '/node-types', data={'name': name})

    def graph_create_node(self, name: str,
                          node_type_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/nodes - Entitaet anlegen."""
        payload: Dict[str, Any] = {'name': name}
        if node_type_id:
            payload['node_type_id'] = node_type_id
        return self._graph_request('POST', '/nodes', data=payload)

    def graph_create_edge(self, node_lo: str, node_hi: str,
                          relation: str) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/edges - typisierte Relation zwischen Knoten."""
        return self._graph_request('POST', '/edges', data={
            'node_lo': node_lo, 'node_hi': node_hi, 'relation': relation})

    def graph_schema(self, type_id: str,
                     include_deprecated: bool = False
                     ) -> Optional[List[Dict[str, Any]]]:
        """GET /secured/graph/node-types/<id>/schema - die Felddefinitionen.

        None bei 404, [] nur fuer einen bekannten Typ ohne Felder. Die ganze
        Oberflaeche wird zur Laufzeit aus dieser Antwort erzeugt; kollabierte
        man beides auf [], zeigte ein veralteter, geloeschter oder fremder
        type_id ein leeres Formular und behauptete damit, der Typ habe keine
        Felder. Genau dieses 404-als-leer hat schon dreimal in diesem Repo
        einen fehlgeschlagenen Aufruf als Erfolg gemeldet.
        """
        params = {'include_deprecated': 'true'} if include_deprecated else {}
        payload = self._graph_request(
            'GET', f'/node-types/{quote(str(type_id), safe="")}/schema',
            params=params)
        if payload is None:
            return None
        return _graph_payload_list(payload, 'attributes')

    def graph_create_schema_attribute(
            self, type_id: str, name: str, datatype: str = 'entity_ref',
            required: bool = False, description: Optional[str] = None,
            sort_order: int = 0, enum_values: Optional[List[str]] = None,
            target_node_type_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/node-types/<id>/schema - eine Felddefinition.

        target_node_type_id gehoert zu entity_ref und wird nur mitgeschickt,
        wenn ein Zieltyp gesetzt ist: die Serverseite dafuer ist noch nicht
        ausgerollt, und ein leeres Feld waere fuer eine aeltere API ein
        unbekannter Schluessel.
        """
        data: Dict[str, Any] = {
            'name': name, 'datatype': datatype,
            'required': bool(required), 'sort_order': int(sort_order),
        }
        if description:
            data['description'] = description
        if enum_values is not None:
            data['enum_values'] = list(enum_values)
        if target_node_type_id:
            data['target_node_type_id'] = target_node_type_id
        return self._graph_request(
            'POST', f'/node-types/{quote(str(type_id), safe="")}/schema', data=data)

    def graph_update_schema_attribute(self, type_id: str, attribute_id: str,
                                      **fields: Any) -> Optional[Dict[str, Any]]:
        """PATCH /secured/graph/node-types/<id>/schema/<aid>."""
        return self._graph_request(
            'PATCH',
            f'/node-types/{quote(str(type_id), safe="")}'
            f'/schema/{quote(str(attribute_id), safe="")}',
            data=dict(fields))

    def graph_deprecate_schema_attribute(self, type_id: str,
                                         attribute_id: str) -> Optional[Dict[str, Any]]:
        """DELETE /secured/graph/node-types/<id>/schema/<aid>.

        Benannt nach dem, was der Server tut: er setzt das Attribut nur auf
        deprecated, vorhandene Fakten behalten ihre attribute_id. Eine Methode
        namens "delete" beschriebe eine Operation, die die API nicht ausfuehrt,
        und eine darauf gebaute Oberflaeche verspraeche dem Nutzer Unwahres.
        """
        return self._graph_request(
            'DELETE',
            f'/node-types/{quote(str(type_id), safe="")}'
            f'/schema/{quote(str(attribute_id), safe="")}')

    def graph_update_node_type(self, type_id: str,
                               **fields: Any) -> Optional[Dict[str, Any]]:
        """PATCH /secured/graph/node-types/<id>."""
        return self._graph_request(
            'PATCH', f'/node-types/{quote(str(type_id), safe="")}', data=dict(fields))

    def graph_update_node(self, node_id: str,
                          **fields: Any) -> Optional[Dict[str, Any]]:
        """PATCH /secured/graph/nodes/<id> - name, description, node_type_id
        und required_groups (die ACL des Backends).

        Ohne Felder wird nichts geschickt. Ein GET an dieser Stelle laege den
        Knoten-Envelope zurueck, und der Aufrufer liest den wahrheitswidrig als
        "gespeichert" - derselbe Fehler, den dieses Repo schon zweimal
        behoben hat. Was ein unveraendert abgeschicktes Formular bedeutet,
        entscheidet der Aufrufer, nicht der Client.
        """
        if not fields:
            raise ValueError(
                "graph_update_node braucht mindestens ein Feld: ein leerer "
                "PATCH waere ein Schreibvorgang, der nicht stattfindet.")
        return self._graph_request(
            'PATCH', f'/nodes/{quote(str(node_id), safe="")}', data=dict(fields))

    # -- Fakten (typisierte Werte an einem Knoten) -------------------------

    def graph_facts(self, node_id: str) -> Optional[List[Dict[str, Any]]]:
        """GET /secured/graph/nodes/<id>/facts - typisierte Werte am Knoten.

        None bei 404 (Knoten unbekannt oder fremd), [] fuer einen Knoten ohne
        Fakten. Wie bei graph_schema: ein Feldleser, der beides gleich
        behandelt, zeigt fuer einen geloeschten Knoten eine leere Akte statt zu
        sagen, dass es sie nicht gibt.
        """
        payload = self._graph_request(
            'GET', f'/nodes/{quote(str(node_id), safe="")}/facts')
        if payload is None:
            return None
        return _graph_payload_list(payload, 'facts')

    def graph_delete_edge(self, edge_id: str) -> Optional[Dict[str, Any]]:
        """DELETE /secured/graph/edges/<id> - nur manuelle Kanten."""
        return self._graph_request(
            'DELETE', f'/edges/{quote(str(edge_id), safe="")}')

    def graph_assign_knowledge(self, node_id: str,
                               pointer: str) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/nodes/<id>/knowledge - Dokument zuordnen."""
        return self._graph_request(
            'POST', f'/nodes/{quote(str(node_id), safe="")}/knowledge',
            data={'pointer': pointer})

    def graph_delete_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """DELETE /secured/graph/nodes/<id> - Kaskade laut Spezifikation."""
        return self._graph_request('DELETE', f'/nodes/{quote(str(node_id), safe="")}')

    def graph_delete_node_type(self, type_id: str) -> Optional[Dict[str, Any]]:
        """DELETE /secured/graph/node-types/<id>."""
        return self._graph_request('DELETE', f'/node-types/{quote(str(type_id), safe="")}')

    def graph_filters(self, node_id: str) -> List[Dict[str, Any]]:
        """GET /secured/graph/nodes/<id>/filters - Filter eines Knotens."""
        payload = self._graph_request(
            'GET', f'/nodes/{quote(str(node_id), safe="")}/filters')
        return _graph_payload_list(payload, 'filters')

    def graph_create_filter(self, node_id: str, query_text: str,
                            child_node_name: str) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/nodes/<id>/filters - Filter anlegen.

        Erzeugt bzw. bindet einen Kind-Knoten; passende Chunks der Dokumente
        des Eltern-Knotens erscheinen danach als Placements. Max. 16 pro
        Knoten, keine Filter auf filter-erzeugten Kindern (Tiefe 1).
        """
        return self._graph_request(
            'POST', f'/nodes/{quote(str(node_id), safe="")}/filters',
            data={'query_text': query_text, 'child_node_name': child_node_name})

    def graph_placements(self, node_id: str,
                         status: str = 'active') -> List[Dict[str, Any]]:
        """GET /secured/graph/nodes/<id>/placements?status=active|rejected."""
        status = status if status in ('active', 'rejected') else 'active'
        payload = self._graph_request(
            'GET', f'/nodes/{quote(str(node_id), safe="")}/placements',
            params={'status': status})
        return _graph_payload_list(payload, 'placements')

    def graph_reject_placement(self, placement_id: str) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/placements/<pid>/reject - dauerhaft."""
        return self._graph_request(
            'POST', f'/placements/{quote(str(placement_id), safe="")}/reject')

    # -- Document fields (typed values per document) -------------------------
    # Contract: KnowledgeBase docs/Knovas_Developer_Kit/api/Knowledge_Graph_API.md
    # and the integration spec (4.1). Three rules hold for every method here:
    # the assertion rides in every body, GETs included (_with_principal);
    # pointers and names travel in the JSON body, never in the URL, because
    # the gateway logs request lines; and writes are sent exactly once
    # (_request_no_retry) -- a replayed PATCH that already committed would
    # come back 409 and read as a conflict the person never caused.

    def _doc_fields_call(
        self,
        method: str,
        path: str,
        *,
        data: Optional[Dict[str, Any]] = None,
        write: bool = False,
        timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """One /secured/graph doc-fields call; the body without its envelope.

        404 ``NOT_FOUND`` (an unknown or invisible pointer, field or rule) is
        None, the documented "not yours". 404 ``pack_not_found`` is a
        DocFieldsError. Any other 404 -- ``HTTP_404`` for a tenant with the
        feature off -- is DocFieldsUnavailable. Every other status of 400 or
        more is a DocFieldsError carrying the code and the whitelisted
        details; no answer at all is DocFieldsError ``transport_error``.
        With ``timeout`` a read is one attempt within that many seconds, as
        a write always is: no tenacity retries.
        """
        endpoint = f"/secured/graph{path}"
        try:
            if write or timeout is not None:
                response = self._request_no_retry(
                    method=method, endpoint=endpoint, data=data, timeout=timeout)
            else:
                response = self._make_request(method=method, endpoint=endpoint, data=data)
        except requests.exceptions.HTTPError as exc:
            response = exc.response
            if response is None:
                raise DocFieldsError(503, 'transport_error', 'no response') from exc
            try:
                body = response.json()
            except ValueError:
                body = None
            body = body if isinstance(body, dict) else {}
            code = body.get('error_code')
            status = response.status_code
            if status == 404:
                if code == 'NOT_FOUND':
                    return None
                if code != 'pack_not_found':
                    logger.info("Document fields off at Knovas: %s %s -> 404 %s (request_id=%s)",
                                method, _route_name(endpoint), _short_code(code),
                                _request_id(response))
                    raise DocFieldsUnavailable(status=404, error_code=code) from exc
            if code is None and status == 429:
                code = 'too_many_requests'  # the rate-limit body has no error_code
            # No Core text (RB-FIX-2): the page says it in its own words from
            # the code; the operator finds the text by the request id.
            raise DocFieldsError(status, code, details=body,
                                 request_id=_request_id(response)) from exc
        except (requests.exceptions.RequestException, RetryError) as exc:
            raise DocFieldsError(
                503, 'transport_error', type(exc).__name__) from exc
        if response.status_code in (204, 205):
            return {}
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise DocFieldsError(502, 'invalid_response',
                                 'Knovas answered without a JSON object')
        return _doc_fields_body(payload)

    def _doc_fields_listing(self, method: str, path: str, *,
                            data: Optional[Dict[str, Any]] = None,
                            timeout: Optional[float] = None) -> Dict[str, Any]:
        """A read whose route always exists while the feature is on. A
        NOT_FOUND there is not "not yours", so it is not folded into an
        empty answer -- the same 404-as-empty that has reported failure as
        success in this client before."""
        payload = self._doc_fields_call(method, path, data=data, timeout=timeout)
        if payload is None:
            raise DocFieldsUnavailable(status=404, error_code='NOT_FOUND')
        return payload

    def doc_fields_probe(self) -> str:
        """Which document-fields capability Knovas offers this tenant.

        ``POST /secured/graph/doc-values/find`` with ``{}`` (plus the
        assertion): never billed (only success is), no side effects, one
        METADATA admission. Answers ``off``, ``values``, ``filters`` or
        ``unknown`` (spec 2.2). Sent through _request_quiet, so a feature-off
        answer leaves no ERROR line. Raises PermissionError when a broker is
        attached and nobody is signed in -- nothing was sent then.
        """
        try:
            response = self._request_quiet('POST', '/secured/graph/doc-values/find', {})
        except PermissionError:
            raise
        except requests.exceptions.RequestException as exc:
            logger.info("Document fields probe: no answer (%s)", type(exc).__name__)
            return 'unknown'
        answer = _classify_doc_fields_probe(response)
        logger.info("Document fields probe: HTTP %s -> %s",
                    getattr(response, 'status_code', '?'), answer)
        return answer

    def doc_fields(self) -> List[Dict[str, Any]]:
        """GET /secured/graph/doc-fields - the field registry, sorted by key.

        Deprecated fields are included. The first read of an empty registry
        installs ``core`` at Knovas, which is why the probe never uses this.
        """
        return _graph_payload_list(
            self._doc_fields_listing('GET', '/doc-fields'), 'fields', strict=True)

    def doc_fields_quick(self, timeout: float) -> List[Dict[str, Any]]:
        """``doc_fields()`` in one attempt within ``timeout`` seconds, for
        request threads: doc_fields_capability serves the last registry when
        Knovas does not answer, so a hung route must not hold a search for
        the client's whole retry cycle (three reads of http_read_timeout)."""
        return _graph_payload_list(
            self._doc_fields_listing('GET', '/doc-fields', timeout=timeout),
            'fields', strict=True)

    def create_doc_field(self, defn: Dict[str, Any]) -> Dict[str, Any]:
        """POST /secured/graph/doc-fields - a new field; returns the field."""
        if not isinstance(defn, dict) or not defn.get('key'):
            raise ValueError('a field definition needs at least a key')
        payload = self._doc_fields_call('POST', '/doc-fields', data=dict(defn), write=True)
        return dict((payload or {}).get('field') or {})

    def update_doc_field(self, field_id: str,
                         changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """PATCH /secured/graph/doc-fields/<id>; the field, None if unknown."""
        if not isinstance(changes, dict) or not changes:
            raise ValueError(
                'update_doc_field needs at least one change: an empty PATCH '
                'would be a write that does not happen.')
        payload = self._doc_fields_call(
            'PATCH', f'/doc-fields/{quote(str(field_id), safe="")}',
            data=dict(changes), write=True)
        return None if payload is None else dict(payload.get('field') or {})

    def deprecate_doc_field(self, field_id: str) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/doc-fields/<id>/deprecate; None if unknown.

        The field keeps its values and still matches ``where`` by its exact
        key; uploads naming it are no longer taken.
        """
        payload = self._doc_fields_call(
            'POST', f'/doc-fields/{quote(str(field_id), safe="")}/deprecate', write=True)
        return None if payload is None else dict(payload.get('field') or {})

    def doc_field_packs(self) -> List[Dict[str, Any]]:
        """GET /secured/graph/doc-fields/packs -
        ``[{key, version, installed, installed_version}]`` (no labels)."""
        return _graph_payload_list(
            self._doc_fields_listing('GET', '/doc-fields/packs'), 'packs', strict=True)

    def install_doc_field_pack(self, pack: str) -> Dict[str, Any]:
        """POST /secured/graph/doc-fields/packs/<pack>/install -
        ``{installed, skipped, warnings}``. An unknown pack is DocFieldsError
        ``pack_not_found``."""
        payload = self._doc_fields_call(
            'POST', f'/doc-fields/packs/{quote(str(pack), safe="")}/install', write=True)
        if payload is None:
            raise DocFieldsError(404, 'pack_not_found', 'Field pack not found')
        return payload

    def doc_field_settings(self) -> Dict[str, Any]:
        """GET /secured/graph/doc-fields/settings - ``{unknown_keys, date_order}``."""
        return self._doc_fields_listing('GET', '/doc-fields/settings')

    def set_doc_field_settings(self, unknown_keys: Optional[str] = None,
                               date_order: Optional[str] = None) -> Dict[str, Any]:
        """PUT /secured/graph/doc-fields/settings with the keys given."""
        body: Dict[str, Any] = {}
        if unknown_keys is not None:
            body['unknown_keys'] = unknown_keys
        if date_order is not None:
            body['date_order'] = date_order
        if not body:
            raise ValueError('set_doc_field_settings needs unknown_keys or date_order')
        payload = self._doc_fields_call('PUT', '/doc-fields/settings', data=body, write=True)
        if payload is None:
            raise DocFieldsUnavailable(status=404, error_code='NOT_FOUND')
        return payload

    def doc_field_rules(self) -> List[Dict[str, Any]]:
        """GET /secured/graph/doc-field-rules - the live folder rules.

        Needs the tenant-admin group or full clearance at Knovas, the GET
        included (doc_fields_api.py:802-814): a 403 is a DocFieldsError
        ``registry_write_requires_full_clearance`` the caller explains.
        """
        return _graph_payload_list(
            self._doc_fields_listing('GET', '/doc-field-rules'), 'rules', strict=True)

    def put_doc_field_rule(self, prefix: str, values: Dict[str, Any]) -> Dict[str, Any]:
        """PUT /secured/graph/doc-field-rules - set a folder default.

        ``prefix`` must end in ``/``: the server matches with a raw
        ``startswith`` (rules.py:93). Returns ``{rule, reapply_job_id,
        warnings?}``; existing documents are updated by a background job.
        """
        prefix = str(prefix or '')
        if not prefix.strip() or not prefix.endswith('/'):
            raise ValueError("a rule prefix must be non-empty and end with '/'")
        if not isinstance(values, dict) or not values:
            raise ValueError('a rule needs at least one field value')
        payload = self._doc_fields_call(
            'PUT', '/doc-field-rules',
            data={'pointer_prefix': prefix, 'set': dict(values)}, write=True)
        if payload is None:
            raise DocFieldsUnavailable(status=404, error_code='NOT_FOUND')
        return payload

    def retire_doc_field_rule(self, prefix: str) -> Optional[Dict[str, Any]]:
        """DELETE /secured/graph/doc-field-rules with the prefix in the body.

        ``{retired, reapply_job_id}``; None when no live rule has the prefix.
        """
        if not str(prefix or '').strip():
            raise ValueError('a rule prefix is required')
        return self._doc_fields_call(
            'DELETE', '/doc-field-rules', data={'pointer_prefix': str(prefix)}, write=True)

    def doc_values(self, pointer: str) -> Optional[Dict[str, Any]]:
        """GET /secured/graph/doc-values with ``{"pointer"}`` in the body.

        The values view (version, title, fields, layers); None when the
        document is unknown or not visible to the person. A server that
        reads the pointer only from the query string answers 400
        ``invalid_value`` with path ``pointer``: there is deliberately no
        fallback to the query string (spec 5, S2).
        """
        pointer = str(pointer or '').strip()
        if not pointer:
            raise ValueError('pointer is required')
        return self._doc_fields_call('GET', '/doc-values', data={'pointer': pointer})

    def patch_doc_values(
        self,
        pointer: str,
        if_version: int,
        *,
        set: Optional[Dict[str, Any]] = None,  # noqa: A002 - the wire name
        unset: Optional[List[str]] = None,
        add: Optional[Dict[str, Any]] = None,
        remove: Optional[Dict[str, Any]] = None,
        fields_strict: bool = False,
        actor_ref: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """PATCH /secured/graph/doc-values - manual values, sent once.

        ``fields_strict`` defaults to False: Knovas keeps every value it can
        normalise and returns ``warnings`` per key (``unresolved_entity``,
        ``ambiguous_date``) instead of refusing a name typed into a field
        without a target type (D12). The answer's ``fields`` is ``{}`` when
        no typed key was named; read again for the effective values.
        None when the document is unknown or not visible.
        """
        pointer = str(pointer or '').strip()
        if not pointer:
            raise ValueError('pointer is required')
        if isinstance(if_version, bool) or not isinstance(if_version, int) or if_version < 0:
            raise ValueError('if_version must be the non-negative version that was read')
        if actor_ref is not None and (not str(actor_ref).strip() or '@' in str(actor_ref)):
            # Knovas stores it on every value event; it is a pseudonymous id,
            # never an address.
            raise ValueError('actor_ref must be an opaque id, not an e-mail address')
        body: Dict[str, Any] = {'pointer': pointer, 'if_version': if_version}
        for name, value in (('set', set), ('unset', unset), ('add', add), ('remove', remove)):
            if value:
                body[name] = value
        if len(body) == 2:
            raise ValueError(
                'patch_doc_values needs set, unset, add or remove: an empty '
                'PATCH would be a write that does not happen.')
        body['fields_strict'] = bool(fields_strict)
        if actor_ref is not None:
            body['actor_ref'] = str(actor_ref)
        return self._doc_fields_call('PATCH', '/doc-values', data=body, write=True)

    def find_doc_values(
        self,
        where: Dict[str, Any],
        *,
        sort: Optional[Dict[str, Any]] = None,
        limit: int = 50,
        after: Optional[str] = None,
        return_fields: Optional[Union[bool, List[str]]] = None,
    ) -> Dict[str, Any]:
        """POST /secured/graph/doc-values/find - one page of the listing.

        ``{documents, next_after, complete, total_count?, where,
        return_fields?}``. Pages may be short or empty while ``next_after``
        is set; the walk is over when it is null. The cursor does not bind
        ``where``: send the same ``where`` for every page.
        """
        if not isinstance(where, dict) or not where:
            raise ValueError('find_doc_values needs a non-empty where')
        body: Dict[str, Any] = {
            'where': where,
            'limit': max(1, min(_DOC_FIELDS_FIND_MAX_LIMIT, int(limit))),
        }
        if sort is not None:
            body['sort'] = sort
        if after:
            body['after'] = str(after)
        if return_fields is not None:
            body['return_fields'] = return_fields
        return self._doc_fields_listing('POST', '/doc-values/find', data=body)

    # -- RBAC: Zugriffsgruppen, Dokument-ACL, Ordnerregeln ------------------

    def _rbac_request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """RBAC-Aufruf. Gibt den geparsten Body zurueck.

        404 heisst 'unbekannt oder nicht deins' und liefert None - ein
        normaler Zustand, kein Fehler. Das entspricht der 404-statt-403-Regel
        der Secure API: keine Route verraet, dass eine Id existiert.
        """
        try:
            response = self._make_request(method=method, endpoint=path,
                                          data=data, params=params)
        except requests.exceptions.HTTPError as exc:
            response = exc.response
            if response is None or response.status_code != 404:
                raise
            if method.upper() != 'GET':
                # Fuer ein GET ist "unbekannt oder nicht deins" eine normale
                # Antwort. Fuer einen Schreibzugriff heisst dasselbe 404, dass
                # nichts geschrieben wurde -- und der Aufrufer sieht nur None.
                # Genau so meldete die Konsole 'Gruppe wurde angelegt' fuer eine
                # Gruppe, die es nie gab.
                # Ohne Antworttext und ohne die Id im Pfad: beide koennen den Namen
                # einer Gruppe tragen, seit RB-4 den einer Rolle (FU-43).
                logger.error(
                    "Knovas API %s %s -> 404, es wurde nichts geschrieben "
                    "(error_code=%s, request_id=%s; Antwort %d Zeichen, nicht protokolliert)",
                    method, _route_name(path), _error_code(response),
                    _request_id(response), _body_size(response),
                )
            else:
                logger.info("RBAC 404 (unbekannt oder fremd): %s %s", method, _route_name(path))
            return None
        if response.status_code == 204:
            return {}
        try:
            return response.json() or {}
        except ValueError:
            logger.warning(
                "RBAC-Antwort ohne JSON-Body: %s %s -> %s (request_id=%s; Antwort %d Zeichen, nicht protokolliert)",
                method, _route_name(path), response.status_code,
                _request_id(response), _body_size(response),
            )
            return {}

    def access_groups(self) -> List[Dict[str, Any]]:
        """GET /secured/access_groups - der Gruppenbaum des Mandanten, flach."""
        payload = self._rbac_request('GET', '/secured/access_groups') or {}
        return flatten_access_groups(list(payload.get('groups') or []))

    def create_access_group(
        self, name: str, parent: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """POST /secured/access_groups - neue Gruppe, optional unter parent."""
        return self._rbac_request(
            'POST', '/secured/access_groups', data={'name': name, 'parent': parent}
        )

    def rename_access_group(
        self, identifier: str, name: str
    ) -> Optional[Dict[str, Any]]:
        """PATCH /secured/access_groups/<id> - Anzeigename aendern.

        Die group_id bleibt stabil, deshalb kostet ein Rename nichts: in
        acl_reader_ids stehen Ids, keine Namen.
        """
        return self._rbac_request(
            'PATCH', f'/secured/access_groups/{quote(str(identifier), safe="")}',
            data={'name': name})

    def move_access_group(self, identifier: str, parent: str) -> Optional[Dict[str, Any]]:
        """PATCH /secured/access_groups/<id> with ``parent`` - hang the group under another (Brain Builder B14).

        A Brain that does not move groups yet ignores the field; the caller reads the tree back to know.
        """
        return self._rbac_request(
            'PATCH', f'/secured/access_groups/{quote(str(identifier), safe="")}',
            data={'parent': str(parent)})

    def delete_access_group(self, identifier: str) -> bool:
        """DELETE /secured/access_groups/<id>. True, wenn geloescht."""
        result = self._rbac_request(
            'DELETE', f'/secured/access_groups/{quote(str(identifier), safe="")}')
        return result is not None

    def document_access(self, pointer: str) -> Optional[Dict[str, Any]]:
        """GET /secured/document_access - die ACL genau eines Dokuments."""
        return self._rbac_request(
            'GET', '/secured/document_access', params={'pointer': str(pointer)})

    def document_readable(self, pointer: str) -> bool:
        """Whether the signed-in person may read this document. Never raises.

        Knovas decides, not the Platform. The alternative -- fetching the ACL
        and evaluating it here -- would be a second copy of a policy whose own
        reference implementation warns that walking the tree on both sides
        inverts the model, and a wall that disagrees with the backend about
        who may read what is worse than no wall.

        Any failure answers False. This gates files the Platform serves off its
        own disk, so an unreachable backend must close the door, not open it.
        """
        try:
            payload = self._rbac_request(
                'GET', '/secured/document_readable',
                params={'pointer': str(pointer)},
            )
        except Exception as exc:  # noqa: BLE001 - transport, TLS, HTTP, JSON
            # Neither the pointer (it names the document) nor the error's text (it
            # quoted the URL with the pointer): the class, and the status of an
            # HTTP error, whose route and request id _make_request logged (FU-43).
            logger.warning(
                "document_readable failed (%s, HTTP %s); refusing the document",
                type(exc).__name__,
                getattr(getattr(exc, 'response', None), 'status_code', '-'),
            )
            return False
        if not isinstance(payload, dict):
            # A 404 from _rbac_request lands here as None. Unknown is refused.
            return False
        return payload.get('readable') is True

    def set_document_access(
        self,
        pointer: str,
        access_groups: List[str],
        acting_as: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """PUT /secured/document_access - ersetzt die ACL vollstaendig.

        Ersetzen, nicht ergaenzen: der Client schickt die vollstaendige
        Zielmenge, damit 'eine Gruppe entfernen' kein zweites Verb braucht.

        `access_groups` ist die Zuweisung, `acting_as` die eigene Freigabe
        des Aufrufers - zwei verschiedene Dinge. Der Server prueft damit,
        dass niemand in eine Gruppe einordnet, die er nicht dominiert.

        Ein Dokument, das so gesetzt wird, verlaesst seine Ordnerregel: ab
        dann entscheidet nur noch die eigene Zuweisung (genau ein Governor).
        """
        body: Dict[str, Any] = {
            'pointer': str(pointer),
            'access_groups': list(access_groups),
        }
        if acting_as is not None:
            body['acting_as'] = list(acting_as)
        return self._rbac_request('PUT', '/secured/document_access', data=body)

    def documents(
        self,
        after: Optional[str] = None,
        limit: int = 100,
        prefix: Optional[str] = None,
        group: Optional[str] = None,
        unrestricted: bool = False,
        conflicts: bool = False,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /secured/documents - eine Keyset-Seite des Dokumentbestands.

        `after` ist der `next_after` der vorigen Antwort. Die Seite wird
        NICHT ueber einen Offset geblaettert: bei grossen Mandanten waere das
        oberhalb von QUERY_MAXIMUM_RESULTS schlicht ein Fehler.

        Gefiltert wird mit der eigenen Freigabe des Aufrufers. Wer aus einem
        Mandat ausgeschlossen ist, sieht es auch hier nicht.
        """
        params: Dict[str, Any] = {'limit': int(limit)}
        if after:
            params['after'] = str(after)
        if prefix:
            params['prefix'] = str(prefix)
        if group:
            params['group'] = str(group)
        if status:
            params['status'] = str(status)
        if unrestricted:
            params['unrestricted'] = 'true'
        if conflicts:
            # Wire-Vertrag aus der Design-Spec (5.4). Das Backend wertet den
            # Parameter noch nicht aus - er wird durchgereicht, nicht erfunden.
            params['conflicts'] = 'true'
        payload = self._rbac_request('GET', '/secured/documents', params=params)
        if payload is None:
            # 404 on a *collection* is not "no documents" — it is the endpoint
            # not being there. Flattening it to {} produced an inventory page
            # reading "0 Dokument(e)", which is what a correctly empty tenant
            # looks like, with the difference recorded only at INFO.
            logger.error(
                "Knovas API GET /secured/documents -> 404. The document inventory "
                "endpoint is not available for this tenant; the console cannot "
                "distinguish that from an empty result without this."
            )
            return {'documents': [], 'total_count': 0, 'unavailable': True}
        return payload

    def iter_documents(
        self, max_pages: int = 10_000, **kwargs: Any
    ) -> Iterator[Dict[str, Any]]:
        """Laeuft den Cursor bis zum Ende ab und liefert einzelne Dokumente.

        `max_pages` ist eine Schleifenbremse, keine Fachgrenze: ein Server,
        der denselben Cursor wiederholt, darf uns nicht endlos drehen.
        """
        after = kwargs.pop('after', None)
        seen: set = set()
        for _ in range(max(1, int(max_pages))):
            page = self.documents(after=after, **kwargs)
            for row in page.get('documents') or []:
                yield row
            after = page.get('next_after')
            if not after or after in seen:
                return
            seen.add(after)

    def folder_rules(self) -> List[Dict[str, Any]]:
        """GET /secured/folder_rules - alle Ordnerregeln des Mandanten."""
        payload = self._rbac_request('GET', '/secured/folder_rules') or {}
        return list(payload.get('rules') or [])

    def create_folder_rule(
        self,
        pointer_prefix: str,
        access_groups: List[str],
        acting_as: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """POST /secured/folder_rules - Ordner einer Gruppe zuordnen.

        Dokumente, die spaeter unter diesen Pfad eingelesen werden, erben die
        Regel beim Ingest. Genau das verhindert, dass ein erneuter Abgleich
        eine geschlossene Wand wieder oeffnet.
        """
        body: Dict[str, Any] = {
            'pointer_prefix': str(pointer_prefix),
            'access_groups': list(access_groups),
        }
        if acting_as is not None:
            body['acting_as'] = list(acting_as)
        return self._rbac_request('POST', '/secured/folder_rules', data=body)

    def update_folder_rule(
        self,
        rule_id: str,
        access_groups: List[str],
        acting_as: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """PATCH /secured/folder_rules/<id> - Gruppen einer Ordnerregel aendern.

        Das ist ein einziger Datenbankschreibvorgang, egal wie viele
        Dokumente unter dem Ordner liegen: auf den Chunks steht die Regel-Id,
        nicht die aufgeloeste Gruppenmenge.
        """
        body: Dict[str, Any] = {'access_groups': list(access_groups)}
        if acting_as is not None:
            body['acting_as'] = list(acting_as)
        return self._rbac_request(
            'PATCH', f'/secured/folder_rules/{quote(str(rule_id), safe="")}',
            data=body)

    def delete_folder_rule(self, rule_id: str) -> bool:
        """DELETE /secured/folder_rules/<id>. True, wenn geloescht."""
        result = self._rbac_request(
            'DELETE', f'/secured/folder_rules/{quote(str(rule_id), safe="")}')
        return result is not None

    def _search_documents_secured(
        self,
        query: Union[str, List[str]],
        limit: int,
        where: Optional[Dict[str, Any]] = None,
        return_fields: Optional[Union[bool, List[str]]] = None,
    ) -> Dict[str, Any]:
        endpoint = self.endpoints.get('query', '/secured/query')
        snippets = bool(getattr(getattr(self, "_snippets_local", None), "on", False))
        # The new keys reach the body builder only when given, so a query
        # without them takes exactly the path it always took.
        extra: Dict[str, Any] = {}
        if where is not None:
            extra['where'] = where
        if return_fields is not None:
            extra['return_fields'] = return_fields
        try:
            response = self._make_request(
                method='POST',
                endpoint=endpoint,
                data=self._secured_query_request_body(query, limit=limit, **extra),
            )
        except requests.exceptions.HTTPError as exc:
            rejected = _query_rejection(exc, where_sent=where is not None) if extra else None
            if rejected is not None:
                raise rejected from exc
            raise
        result = _unwrap_secured_query_response(response.json())
        # Coerce to [] so a null/absent "results" never crashes None[:limit].
        raw_hits = (result.get("results") or [])[:limit]

        normalized_results = []
        passage_chars = _passage_chars() if snippets else DEFAULT_PASSAGE_CHARS
        for raw in raw_hits:
            if not isinstance(raw, dict):
                continue
            row = _secured_query_hit_to_row(raw)
            if snippets:
                found = _snippet_with_location(raw, passage_chars)
                if found:
                    # The passage's own page and sentence, from its chunk (None
                    # when unknown); page_number stays the row's (top_chunks[0]).
                    row["snippet"] = found[0]
                    row["snippet_page_number"] = found[1].get("page_number")
                    row["snippet_sentence_number"] = found[1].get("sentence_number")
            # The server's own ranking (ColBERT rerank, name-prefilter boost).
            # `score` is the stage-1 cosine of the best chunk and sorts
            # differently; the UI keeps this order (PLATFORM_KEEP_SERVER_ORDER).
            row["server_rank"] = len(normalized_results) + 1
            normalized_results.append(row)

        if normalized_results and all(
            r.get("page_number") is None
            and r.get("sentence_number") is None
            and not (r.get("top_chunks") or [])
            for r in normalized_results[:3]
        ):
            raw0 = raw_hits[0] if raw_hits and isinstance(raw_hits[0], dict) else {}
            chunks = raw0.get("top_chunks")
            # The chunks' count, not the chunks: a chunk carries its snippet, the
            # document's own text (FU-09, FU-43).
            logger.info(
                "Secured query: no page/sentence in first hits; raw[0] keys=%s "
                "page_number=%r sentence_number=%r top_chunks=%d item(s), not logged",
                sorted(str(k) for k in raw0.keys()) if raw0 else [],
                raw0.get("page_number"),
                raw0.get("sentence_number"),
                len(chunks) if isinstance(chunks, list) else 0,
            )

        semantix_meta = {
            'status': result.get('status'),
            'message': result.get('message'),
            'result_count': result.get('result_count'),
            'pointers': result.get('pointers'),
            'query_session_id': result.get('query_session_id'),
        }
        semantix_meta.update(_secured_query_honesty(result))

        return {
            'results': normalized_results,
            'total': len(normalized_results),
            'semantix': semantix_meta,
        }
