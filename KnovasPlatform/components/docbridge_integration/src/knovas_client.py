"""
Knovas API client for document synchronization.
Handles communication with Knovas knowledge base API.
"""

import requests
import logging
import json
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
import time
import os
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.x509.oid import NameOID
from tempfile import NamedTemporaryFile
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from config_loader import get_config


logger = logging.getLogger(__name__)


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
    top_results = out.get("results")
    if top_results is None:
        out["results"] = inner_results or []
    elif isinstance(top_results, list) and not top_results and inner_results:
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
    return out


_INGESTED_SUMMARY_MAX_LEN = 2500


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
        return value.strip()
    if isinstance(value, dict):
        if value.get("present") is False:
            return None
        for key in ("text", "summary", "content"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                text = text.strip()
                if len(text) > _INGESTED_SUMMARY_MAX_LEN:
                    return None
                return text
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


def _chunk_text_from_hit(item: Dict[str, Any]) -> Optional[str]:
    """First non-empty chunk/snippet field from a query hit or nested metadata."""
    for k in ("snippet", "text", "chunk", "content", "body", "preview", "matched_text", "excerpt", "highlight"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    nest = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    for k in ("snippet", "text", "chunk", "content", "matched_text", "excerpt"):
        v = nest.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


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


def _cap_path_keep_suffix(path: str, max_chars: int = 300) -> str:
    """Truncate long paths from the beginning so the end (e.g. filename) is kept."""
    if len(path) <= max_chars:
        return path
    return "…" + path[-(max_chars - 1) :]


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


def _secured_init_fields_from_document(document: Dict[str, Any]) -> Dict[str, str]:
    """title, path, description for secured init (Client Integration Guide)."""
    out: Dict[str, str] = {}
    title = document.get("display_name") or document.get("title")
    if title is not None:
        title = str(title).strip()[:500] or None
    if not title:
        path_hint = document.get("path")
        if path_hint:
            ps = str(path_hint).strip()
            if ps.startswith("http://") or ps.startswith("https://"):
                try:
                    from urllib.parse import parse_qs, unquote, urlparse

                    qs = parse_qs(urlparse(ps).query)
                    vals = qs.get("file") or []
                    if vals:
                        raw = unquote(vals[0].split(";")[0]).strip()
                        title = raw[:500] if raw else None
                except Exception:
                    title = None
            if not title:
                stem = ps.replace("\\", "/").rstrip("/").split("/")[-1].split("?")[0]
                title = stem[:500] if stem else None
    if not title:
        did = document.get("doc_id")
        if did is not None:
            title = str(did).strip()[:500] or None
    if title:
        out["title"] = title
    path = _normalize_semantix_path_for_init(document.get("path"))
    if path:
        out["path"] = path
    desc = document.get("description")
    if desc is not None:
        ds = str(desc).strip()
        if ds:
            out["description"] = ds[:2000]
    return out


def _secured_transmit_parts_from_document(document: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """
    Build init fields and transmit parts for secured single-document sync.

    Uses extract_transmission_chunks when docbridge_sync is importable and
    content_base64 is present; otherwise one Markdown fallback part.
    """
    init_fields = _secured_init_fields_from_document(document)
    identifier = str(document.get("doc_id") or document.get("path") or "unknown")
    b64 = document.get("content_base64")
    ext = (document.get("type") or document.get("extension") or "").strip().lower().lstrip(".")

    if b64 and ext:
        try:
            from src.utils.text_extractor import extract_transmission_chunks
        except ImportError:
            extract_transmission_chunks = None  # type: ignore[misc,assignment]
        else:
            try:
                parts = extract_transmission_chunks(str(b64), ext)
                if parts:
                    return parts, init_fields
            except Exception as exc:
                logger.warning("Secured single-doc extract failed for %s: %s", identifier, exc)

    path_line = init_fields.get("path") or identifier
    lines = [path_line]
    if document.get("akten_id"):
        lines.append(f"Akte: {document.get('akten_id')}")
    if document.get("doc_type"):
        lines.append(f"Typ: {document.get('doc_type')}")
    snippet = "\n".join(lines)
    return [{"snippet": snippet}], init_fields


def _first_top_chunk(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Secured query hits often nest match location and scores under top_chunks[0]."""
    chunks = item.get("top_chunks")
    if not isinstance(chunks, list) or not chunks:
        return None
    first = chunks[0]
    return first if isinstance(first, dict) else None


def _merge_secured_query_hit(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Flatten Knovas /secured/query result rows: copy top_chunks[0] fields when
    top-level values are missing (page_number, cosine metrics, final_score, snippet).
    """
    merged = dict(item)
    tc = _first_top_chunk(item)
    if not tc:
        return merged

    def _is_empty(val: Any) -> bool:
        if val is None:
            return True
        if isinstance(val, str) and not val.strip():
            return True
        return False

    for key in (
        "page_number",
        "page",
        "sentence_number",
        "cosine_similarity",
        "cosine_distance",
        "cosineSimilarity",
        "cosineDistance",
        "final_score",
        "FinalScore",
        "snippet",
        "text",
        "chunk",
        "content",
        "body",
        "preview",
        "matched_text",
        "excerpt",
        "highlight",
        "ingested_summary",
        "ingestedSummary",
    ):
        if not _is_empty(merged.get(key)):
            continue
        val = tc.get(key)
        if not _is_empty(val):
            merged[key] = val
    return merged


class KnovasAPIClient:
    """Client for Knovas API operations."""
    
    def __init__(self, config_loader=None):
        """
        Initialize Knovas API client.
        
        Args:
            config_loader: ConfigLoader instance. If None, uses global config.
        """
        self.config = config_loader or get_config()
        
        self.base_url = self.config.get('api.base_url', 'http://localhost:5000')
        self.auth_type = self.config.get('api.auth_type', 'bearer')
        self.api_key = self.config.get('api.api_key', '')
        self.use_secured_api = self.config.get_bool('api.use_secured_api', True)
        self.allow_legacy_api_fallback = self.config.get_bool('api.allow_legacy_api_fallback', False)
        self.cert_path = self.config.get('api.cert_path', '')
        self.key_path = self.config.get('api.key_path', '')
        self.ca_cert_path = self.config.get('api.ca_cert_path', '')
        self.mtls_enabled = bool(self.cert_path and self.key_path and self.ca_cert_path)
        self.customer_id = self.config.get('api.customer_id', '') or os.getenv('SEMANTIX_CUSTOMER_ID', '')
        self.cert_auto_renew_enabled = self.config.get_bool('api.cert_auto_renew_enabled', True)
        self.cert_renew_threshold_days = self.config.get_int('api.cert_renew_threshold_days', 30)
        self.cert_check_interval_seconds = self.config.get_int('api.cert_check_interval_seconds', 3600)
        self._last_cert_check_at = 0.0

        self.encryption_matrix_path = (
            (self.config.get('api.encryption_matrix_path', '') or '').strip()
            or (os.getenv('SEMANTIX_ENCRYPTION_MATRIX_PATH') or '').strip()
        )

        self.endpoints = {
            'full_sync': self.config.get('api.endpoints.full_sync', '/api/docs/full-sync'),
            'new_doc': self.config.get('api.endpoints.new_doc', '/api/docs/new'),
            'search': self.config.get('api.endpoints.search', '/api/search'),
            'health': self.config.get('api.endpoints.health', '/secured/health'),
            'init_transmission': self.config.get('api.endpoints.init_transmission', '/secured/init_document_transmission'),
            'transmit_part': self.config.get('api.endpoints.transmit_part', '/secured/transmit_document_part'),
            'query': self.config.get('api.endpoints.query', '/secured/query'),
            'generate_certificate': self.config.get('api.endpoints.generate_certificate', '/secured/generate_certificate'),
            'relevance_feedback': self.config.get(
                'api.endpoints.relevance_feedback', '/secured/analytics/relevance-feedback'
            ),
            'document_rating': self.config.get(
                'api.endpoints.document_rating', '/secured/document/rating'
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

    def _load_encryption_matrix(self) -> Optional[Any]:
        """Optional orthogonal matrix for POST /secured/query when tenant uses encrypted embeddings."""
        path = self.encryption_matrix_path
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except Exception as exc:
            logger.warning('Could not load encryption matrix from %s: %s', path, exc)
            return None

    def _secured_query_request_body(self, query: str) -> Dict[str, Any]:
        body: Dict[str, Any] = {'Input': query}
        matrix = self._load_encryption_matrix()
        if matrix is not None:
            body['encryption_matrix'] = matrix
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

    def _extract_customer_id_from_cert(self) -> Optional[str]:
        try:
            with open(self.cert_path, "rb") as f:
                cert = x509.load_pem_x509_certificate(f.read(), default_backend())
            for oid in (NameOID.COMMON_NAME, NameOID.ORGANIZATIONAL_UNIT_NAME):
                attrs = cert.subject.get_attributes_for_oid(oid)
                if attrs:
                    return str(attrs[0].value)
        except Exception:
            return None
        return None

    def _atomic_write(self, path: str, content: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=str(target.parent), delete=False) as tmp:
            tmp.write(content)
            tmp_name = tmp.name
        os.replace(tmp_name, str(target))

    def _attempt_certificate_renewal(self) -> bool:
        endpoint = self.endpoints.get('generate_certificate', '/secured/generate_certificate')
        customer_id = self.customer_id or self._extract_customer_id_from_cert()
        if not customer_id:
            logger.warning("Auto-renew skipped: customer_id unavailable")
            return False

        try:
            response = self._session.request(
                method='POST',
                url=f"{self.base_url}{endpoint}",
                json={'certificate_data': {'customer_id': customer_id}},
                headers=self._get_headers(),
                timeout=self.http_read_timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.error("Auto-renew request failed: %s", exc)
            return False

        certificate_pem = payload.get('certificate_pem')
        private_key = payload.get('private_key') or payload.get('private_key_pem')
        if not certificate_pem or not private_key:
            logger.error("Auto-renew response missing certificate/key")
            return False
        if "ENCRYPTED PRIVATE KEY" in str(private_key):
            logger.error("Auto-renew returned encrypted private key; cannot install automatically")
            return False

        self._atomic_write(self.cert_path, str(certificate_pem).strip() + "\n")
        self._atomic_write(self.key_path, str(private_key).strip() + "\n")
        self._session.close()
        self._session = self._build_session()
        logger.info("Certificate auto-renewed and rotated successfully")
        return True

    def _ensure_certificate_freshness(self) -> None:
        if not self.mtls_enabled or not self.cert_auto_renew_enabled:
            return
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
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=1, max=30),
        retry=retry_if_exception_type((requests.exceptions.RequestException,))
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
        
        logger.debug(f"Making {method} request to {url}")
        
        response = self._session.request(
            method=method,
            url=url,
            json=data,
            params=params,
            headers=headers,
            timeout=self.http_read_timeout,
        )
        
        response.raise_for_status()
        return response

    def _request_no_retry(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> requests.Response:
        """
        Single HTTP request without tenacity retries (used for analytics feedback;
        avoids duplicate submissions on transient failures).
        """
        self._rate_limit()
        self._ensure_certificate_freshness()
        url = f"{self.base_url}{endpoint}"
        response = self._session.request(
            method=method,
            url=url,
            json=data,
            params=params,
            headers=self._get_headers(),
            timeout=self.http_read_timeout,
        )
        response.raise_for_status()
        return response

    def post_relevance_feedback(
        self,
        pointer: str,
        relevance_score: int,
        query_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        POST /secured/analytics/relevance-feedback — per-query relevance (1–5).
        """
        if not pointer or not str(pointer).strip():
            raise ValueError('pointer is required')
        score = int(relevance_score)
        if score < 1 or score > 5:
            raise ValueError('relevance_score must be 1–5')
        body: Dict[str, Any] = {
            'pointer': str(pointer).strip(),
            'relevance_score': score,
        }
        if query_session_id and str(query_session_id).strip():
            body['query_session_id'] = str(query_session_id).strip()
        endpoint = self.endpoints.get(
            'relevance_feedback', '/secured/analytics/relevance-feedback'
        )
        response = self._request_no_retry('POST', endpoint, data=body)
        return response.json() if response.content else {}

    def post_document_rating(
        self,
        pointer: str,
        importance_score: Optional[int] = None,
        quality_score: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        POST /secured/document/rating — permanent importance/quality (upsert).
        At least one score must be set.
        """
        if not pointer or not str(pointer).strip():
            raise ValueError('pointer is required')
        body: Dict[str, Any] = {'pointer': str(pointer).strip()}
        if importance_score is not None:
            i = int(importance_score)
            if i < 1 or i > 5:
                raise ValueError('importance_score must be 1–5')
            body['importance_score'] = i
        if quality_score is not None:
            q = int(quality_score)
            if q < 1 or q > 5:
                raise ValueError('quality_score must be 1–5')
            body['quality_score'] = q
        if 'importance_score' not in body and 'quality_score' not in body:
            raise ValueError('At least one of importance_score or quality_score is required')
        endpoint = self.endpoints.get('document_rating', '/secured/document/rating')
        response = self._request_no_retry('POST', endpoint, data=body)
        return response.json() if response.content else {}

    def get_document_rating(self, pointer: str) -> Dict[str, Any]:
        """GET /secured/document/rating?pointer=…"""
        if not pointer or not str(pointer).strip():
            raise ValueError('pointer is required')
        endpoint = self.endpoints.get('document_rating', '/secured/document/rating')
        response = self._request_no_retry(
            'GET',
            endpoint,
            params={'pointer': str(pointer).strip()},
        )
        return response.json() if response.content else {}
    
    def sync_document_batch(
        self, 
        documents: List[Dict[str, Any]],
        endpoint_type: str = 'full_sync'
    ) -> Dict[str, Any]:
        """
        Synchronize a batch of documents to Knovas.
        
        Args:
            documents: List of document dictionaries
            endpoint_type: Type of endpoint ('full_sync' or 'new_doc')
            
        Returns:
            API response data
        """
        endpoint = self.endpoints.get(endpoint_type, self.endpoints['full_sync'])
        
        try:
            response = self._make_request(
                method='POST',
                endpoint=endpoint,
                data={'documents': documents}
            )
            
            result = response.json()
            logger.info(
                f"Batch sync successful: {len(documents)} documents to {endpoint}"
            )
            return result
            
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error during batch sync: {e}")
            if e.response is not None:
                logger.error(f"Response body: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Error during batch sync: {e}")
            raise
    
    def sync_single_document(
        self, 
        document: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Synchronize a single document to Knovas.
        
        Args:
            document: Document dictionary
            
        Returns:
            API response data
        """
        if self.use_secured_api and self.mtls_enabled:
            return self._sync_single_document_secured(document)

        endpoint = self.endpoints['new_doc']
        
        try:
            response = self._make_request(
                method='POST',
                endpoint=endpoint,
                data=document
            )
            
            result = response.json()
            logger.info(f"Single document sync successful: {document.get('doc_id')}")
            return result
            
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error during single document sync: {e}")
            raise
        except Exception as e:
            logger.error(f"Error during single document sync: {e}")
            raise
    
    def search_documents(
        self,
        query: str,
        limit: int = 20,
        filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Search documents in Knovas.
        
        Args:
            query: Search query string
            limit: Maximum number of results
            filters: Additional search filters
            
        Returns:
            Search results
        """
        if self.use_secured_api and self.mtls_enabled:
            return self._search_documents_secured(query=query, limit=limit)

        if self.use_secured_api and not self.allow_legacy_api_fallback:
            raise RuntimeError(
                "Secured API mode is enabled but mTLS cert paths are not configured. "
                "Set SEMANTIX_CLIENT_CERT, SEMANTIX_CLIENT_KEY and SEMANTIX_CA_CERT, "
                "or explicitly enable legacy fallback for mock/dev."
            )

        endpoint = self.endpoints['search']
        params = {'query': query, 'limit': limit}
        if filters:
            params.update(filters)

        try:
            response = self._make_request(method='GET', endpoint=endpoint, params=params)
            result = response.json()
            logger.info(f"Legacy search successful: query='{query}', results={len(result.get('results', []))}")
            return result
        except Exception as e:
            logger.error(f"Error during search: {e}")
            raise
    
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
    
    def format_document_payload(
        self,
        doc_id: str,
        akten_id: Optional[str],
        doc_type: Optional[str],
        file_path: str,
        timestamp: datetime,
        file_size: int,
        file_hash: str,
        additional_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Format document data for API submission.
        
        Args:
            doc_id: Document ID
            akten_id: Akten ID
            doc_type: Document type
            file_path: Relative file path
            timestamp: Document timestamp
            file_size: File size in bytes
            file_hash: File hash (SHA256)
            additional_metadata: Additional metadata fields
            
        Returns:
            Formatted document payload
        """
        payload = {
            'doc_id': str(doc_id),
            'akten_id': str(akten_id) if akten_id else None,
            'type': doc_type,
            'path': file_path,
            'timestamp': timestamp.isoformat() if isinstance(timestamp, datetime) else timestamp,
            'size': file_size,
            'hash': file_hash
        }
        
        if additional_metadata:
            payload.update(additional_metadata)
        
        return payload

    def _search_documents_secured(self, query: str, limit: int) -> Dict[str, Any]:
        endpoint = self.endpoints.get('query', '/secured/query')
        response = self._make_request(
            method='POST',
            endpoint=endpoint,
            data=self._secured_query_request_body(query),
        )
        result = _unwrap_secured_query_response(response.json())

        normalized_results = []
        for raw in result.get('results', [])[:limit]:
            item = _merge_secured_query_hit(raw)
            pointer = item.get('pointer') or item.get('identifier') or ''
            page_number = item.get('page_number')
            if page_number is None:
                page_number = item.get('page')
            sentence_number = item.get('sentence_number')
            document_date = _document_date_from_hit(item)
            cos_sim = item.get('cosine_similarity')
            if cos_sim is None:
                cos_sim = item.get('cosineSimilarity')
            cos_dist = item.get('cosine_distance')
            if cos_dist is None:
                cos_dist = item.get('cosineDistance')
            row: Dict[str, Any] = {
                'doc_id': pointer,
                'path': pointer,
                'score': _extract_semantix_query_similarity(item),
                'title': _display_title_for_hit(pointer, item.get('title')),
                'source': 'semantix',
                'page_number': page_number,
                'page': page_number,
                'sentence_number': sentence_number,
                'cosine_similarity': cos_sim,
                'cosine_distance': cos_dist,
                'document_date': document_date,
                'date': document_date,
            }
            fs = item.get('final_score')
            if fs is None:
                fs = item.get('FinalScore')
            if fs is not None:
                row['final_score'] = fs
            summary = _ingested_summary_from_hit(item)
            if summary:
                row['ingested_summary'] = summary
            chunk = _chunk_text_from_hit(item)
            if chunk:
                row['snippet'] = chunk
                row['content'] = chunk
            normalized_results.append(row)

        semantix_meta = {
            'status': result.get('status'),
            'message': result.get('message'),
            'result_count': result.get('result_count'),
            'pointers': result.get('pointers'),
            'query_session_id': result.get('query_session_id'),
        }

        return {
            'results': normalized_results,
            'total': len(normalized_results),
            'semantix': semantix_meta,
        }

    def _sync_single_document_secured(self, document: Dict[str, Any]) -> Dict[str, Any]:
        identifier = str(document.get('doc_id') or document.get('path') or 'unknown')
        parts, init_fields = _secured_transmit_parts_from_document(document)

        init_endpoint = self.endpoints.get('init_transmission', '/secured/init_document_transmission')
        init_body: Dict[str, Any] = {
            'identifier': identifier,
            'part_count': len(parts),
        }
        init_body.update(init_fields)

        init_resp = self._make_request(
            method='POST',
            endpoint=init_endpoint,
            data=init_body,
        ).json()

        transmission_key_id = init_resp.get('transmission_key_id')
        if not transmission_key_id:
            raise RuntimeError('init_document_transmission returned no transmission_key_id')

        part_endpoint = self.endpoints.get('transmit_part', '/secured/transmit_document_part')
        for idx, part in enumerate(parts):
            payload: Dict[str, Any] = {
                'key': transmission_key_id,
                'snippet': part['snippet'],
                'part_number': idx,
            }
            pn = part.get('page_number')
            if pn is not None:
                try:
                    pni = int(pn)
                    if pni >= 1:
                        payload['page_number'] = pni
                except (TypeError, ValueError):
                    pass
            self._make_request(method='POST', endpoint=part_endpoint, data=payload)

        logger.info(f"Secured single document sync successful: {identifier}")
        return {'status': 'success', 'identifier': identifier, 'mode': 'secured'}


class BatchProcessor:
    """Process documents in batches for API submission."""
    
    def __init__(
        self, 
        api_client: KnovasAPIClient,
        batch_size: Optional[int] = None
    ):
        """
        Initialize batch processor.
        
        Args:
            api_client: KnovasAPIClient instance
            batch_size: Number of documents per batch
        """
        self.api_client = api_client
        
        config = get_config()
        self.batch_size = batch_size or config.get_int('api.rate_limit.batch_size', 100)
    
    def process_documents_in_batches(
        self,
        documents: List[Dict[str, Any]],
        endpoint_type: str = 'full_sync',
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """
        Process list of documents in batches.
        
        Args:
            documents: List of document dictionaries
            endpoint_type: API endpoint type
            progress_callback: Optional callback function(current, total, batch_result)
            
        Returns:
            Summary of processing results
        """
        total_docs = len(documents)
        processed = 0
        failed = 0
        results = []
        
        logger.info(f"Starting batch processing: {total_docs} documents, batch_size={self.batch_size}")
        
        for i in range(0, total_docs, self.batch_size):
            batch = documents[i:i + self.batch_size]
            batch_num = (i // self.batch_size) + 1
            total_batches = (total_docs + self.batch_size - 1) // self.batch_size
            
            try:
                logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} documents)")
                
                result = self.api_client.sync_document_batch(batch, endpoint_type)
                results.append(result)
                
                processed += len(batch)
                
                if progress_callback:
                    progress_callback(processed, total_docs, result)
                
            except Exception as e:
                logger.error(f"Batch {batch_num} failed: {e}")
                failed += len(batch)
        
        summary = {
            'total': total_docs,
            'processed': processed,
            'failed': failed,
            'success_rate': (processed / total_docs * 100) if total_docs > 0 else 0,
            'batches': len(results),
            'results': results
        }
        
        logger.info(
            f"Batch processing complete: {processed}/{total_docs} successful, "
            f"{failed} failed ({summary['success_rate']:.1f}% success rate)"
        )
        
        return summary
