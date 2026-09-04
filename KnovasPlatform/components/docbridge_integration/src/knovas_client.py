"""
Knovas API client for document synchronization.
Handles communication with Knovas knowledge base API.
"""

import requests
import logging
import json
from typing import Iterator, List, Dict, Any, Optional, Tuple, Union
from urllib.parse import quote
from datetime import datetime, timezone
import time
import os
import threading
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from tempfile import NamedTemporaryFile
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from config_loader import get_config
from part_metadata import enrich_transmit_parts_with_location


logger = logging.getLogger(__name__)


# Only transient transport failures are safe to retry. HTTPError (raised by
# raise_for_status on 4xx/5xx) is a RequestException subclass but MUST NOT be
# retried: retrying a 4xx/5xx on a state-changing POST duplicates ingestion.
_RETRYABLE_REQUEST_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
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
            from knovas_extract_upload import parts_from_base64
        except ImportError:
            parts_from_base64 = None  # type: ignore[misc,assignment]
        else:
            try:
                use_ocr: bool | str = "auto"
                ocr_language = "deu+eng"
                try:
                    cfg = get_config()
                    use_ocr = "auto" if cfg.get_bool("advanced.extraction.use_ocr", True) else False
                    ocr_language = str(cfg.get("advanced.extraction.ocr_language") or ocr_language)
                except Exception:
                    pass
                parts = parts_from_base64(
                    str(b64),
                    ext,
                    pointer=identifier,
                    path=str(init_fields.get("path") or identifier),
                    use_ocr=use_ocr,
                    ocr_language=ocr_language,
                )
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
    parts = enrich_transmit_parts_with_location([{"snippet": snippet}], full_text=snippet)
    return parts, init_fields


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
    """

    def __init__(self, status: int, error_code: Optional[str], message: str):
        super().__init__(f"{status} {error_code or ''}: {message}".strip())
        self.status = status
        self.error_code = error_code
        self.message = message


def _graph_payload_list(payload: Any, *candidate_keys: str) -> List[Dict[str, Any]]:
    """Liste aus einer flachen Envelope ziehen.

    Die Graph-Spezifikation nennt die Schluesselnamen der Listen-Antworten
    nicht. Deshalb erst die plausiblen Namen probieren, dann auf die erste
    Liste im Objekt zurueckfallen - tolerant statt zu raten und zu brechen.
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in candidate_keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
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
        self.use_secured_api = self.config.get_bool('api.use_secured_api', True)
        self.allow_legacy_api_fallback = self.config.get_bool('api.allow_legacy_api_fallback', False)
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
        self.cert_renew_method = (
            (self.config.get('api.cert_renew_method', '') or '').strip().lower()
            or (os.getenv('SEMANTIX_CERT_RENEW_METHOD') or 'csr').strip().lower()
        )
        if self.cert_renew_method not in ('csr', 'legacy'):
            self.cert_renew_method = 'csr'
        self._last_cert_check_at = 0.0
        # Serialize the certificate freshness check / renewal / session swap so
        # concurrent request threads can't race on _last_cert_check_at or _session.
        self._cert_lock = threading.Lock()

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

    def _secured_query_request_body(
        self,
        query: Union[str, List[str]],
        limit: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
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
            body['limit'] = int(limit)
            body['top_k'] = int(limit)
        if filters:
            # Forward case/matter scoping to the server instead of silently
            # dropping it. Server-side filtering depends on tenant support, so
            # surface it loudly rather than over-returning without a trace.
            body['filters'] = filters
            logger.warning(
                "Secured query: forwarding %d filter(s) to /secured/query "
                "(server-side scoping depends on tenant support): %s",
                len(filters),
                sorted(filters.keys()),
            )
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

    def _attempt_certificate_renewal(self) -> bool:
        if self.cert_renew_method == 'legacy':
            return self._attempt_certificate_renewal_legacy()
        return self._attempt_certificate_renewal_csr()

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

    def _attempt_certificate_renewal_csr(self) -> bool:
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

    def _attempt_certificate_renewal_legacy(self) -> bool:
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
                allow_redirects=False,
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
        return self._install_renewed_certificate_pair(
            str(certificate_pem).strip() + "\n",
            str(private_key).strip() + "\n",
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
    
    def attach_principal_broker(self, broker) -> None:
        """Bind the broker that signs the current user into every outbound body.

        ``broker`` offers ``current_user()`` and ``assertion_for(user)``. It is
        attached after construction because create_app() builds the identity
        gate after the client; the client itself stays ignorant of Flask.
        """
        self._principal_broker = broker

    def _with_principal(self, data):
        """Attach the caller's assertion to an outgoing body.

        Fail closed when there is no authenticated user. An unsigned call
        resolves to asserted=False at the Secure API -- "unrestricted
        documents only" -- and returns *more* than a correctly scoped one.
        A wall that widens under failure is not a wall.
        """
        if self._principal_broker is None:
            return data
        user = self._principal_broker.current_user()
        if user is None:
            raise PermissionError(
                "No authenticated user for this request; refusing to call "
                "Knovas without a principal assertion."
            )
        assertion = self._principal_broker.assertion_for(user)
        if data is None:
            return {ASSERTION_FIELD: assertion}
        if not isinstance(data, dict):
            # Every secured endpoint takes a JSON object. A non-dict body is a
            # caller we have not accounted for; guessing how to attach the
            # assertion is how one route would quietly lose it.
            raise TypeError(
                f"Cannot attach a principal assertion to a {type(data).__name__} body."
            )
        return {**data, ASSERTION_FIELD: assertion}

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
        
        logger.debug(f"Making {method} request to {url}")
        data = self._with_principal(data)

        response = self._session.request(
            method=method,
            url=url,
            json=data,
            params=params,
            headers=headers,
            timeout=self.http_read_timeout,
            allow_redirects=False,
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
        data = self._with_principal(data)
        response = self._session.request(
            method=method,
            url=url,
            json=data,
            params=params,
            headers=self._get_headers(),
            timeout=self.http_read_timeout,
            allow_redirects=False,
        )
        response.raise_for_status()
        return response

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

    def sign_certificate(
        self,
        csr_pem: str,
        validity_days: Optional[int] = None,
        organisation: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /secured/sign_certificate — sign a tenant CSR (recommended renewal path)."""
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
        response = self._request_no_retry('POST', endpoint, data=body)
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
        query: Union[str, List[str]],
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
            return self._search_documents_secured(query=query, limit=limit, filters=filters)

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
            try:
                body = response.json() or {}
            except ValueError:
                body = {}
            if response.status_code == 404:
                if body.get('error_code') == 'knowledge_graph_disabled':
                    raise KnowledgeGraphDisabled(
                        'Der Wissensgraph ist fuer dieses Deployment nicht aktiviert'
                    ) from exc
                logger.info("Graph 404 (unbekannte oder fremde Id): %s %s", method, endpoint)
                return None
            raise GraphError(
                response.status_code,
                body.get("error_code"),
                body.get("message") or getattr(response, "reason", None) or "",
            ) from exc
        try:
            return response.json() or {}
        except ValueError:
            logger.warning("Graph-Antwort ohne JSON-Body: %s %s", method, endpoint)
            return {}

    def graph_export(self) -> Dict[str, Any]:
        """GET /secured/graph - vollstaendiger Topologie-Export."""
        return self._graph_request('GET', '') or {}

    def graph_node_types(self) -> List[Dict[str, Any]]:
        """GET /secured/graph/node-types - Typ-Vokabular."""
        return _graph_payload_list(self._graph_request('GET', '/node-types'),
                                   'node_types', 'nodeTypes', 'types')

    def graph_nodes(self) -> List[Dict[str, Any]]:
        """GET /secured/graph/nodes - alle Knoten des Mandanten."""
        return _graph_payload_list(self._graph_request('GET', '/nodes'), 'nodes')

    def graph_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """GET /secured/graph/nodes/<id> - Detail inkl. Zuordnungen und Fakten."""
        return self._graph_request('GET', f'/nodes/{quote(str(node_id), safe="")}')

    def graph_edges(self) -> List[Dict[str, Any]]:
        """GET /secured/graph/edges - typisierte Relationen."""
        return _graph_payload_list(self._graph_request('GET', '/edges'), 'edges')

    def graph_neighbors(self, node_id: str, depth: int = 1) -> List[Dict[str, Any]]:
        """GET /secured/graph/nodes/<id>/neighbors - Traversal, max. 3 Hops."""
        depth = max(0, min(3, int(depth)))
        payload = self._graph_request(
            'GET', f'/nodes/{quote(str(node_id), safe="")}/neighbors',
            params={'depth': depth})
        return _graph_payload_list(payload, 'neighbors', 'nodes')

    # -- Kuratieren (der Graph wird vom Client gepflegt, nicht abgeleitet) --

    def graph_create_node_type(self, name: str) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/node-types - Typ-Vokabular erweitern."""
        return self._graph_request('POST', '/node-types', data={'name': name})

    def graph_create_node(self, name: str,
                          node_type_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/nodes - Entitaet anlegen.

        Achtung: Die Spezifikation zeigt als Body nur name (plus die
        Zugriffsfelder); wie ein Knoten seinen Typ bekommt, ist dort nicht
        beschrieben. Wir senden node_type_id - beim ersten Lauf gegen eine
        echte Instanz pruefen, ob der Typ wirklich gesetzt wird.
        """
        payload: Dict[str, Any] = {'name': name}
        if node_type_id:
            payload['node_type_id'] = node_type_id
        return self._graph_request('POST', '/nodes', data=payload)

    def graph_create_edge(self, node_lo: str, node_hi: str,
                          relation: str) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/edges - typisierte Relation zwischen Knoten."""
        return self._graph_request('POST', '/edges', data={
            'node_lo': node_lo, 'node_hi': node_hi, 'relation': relation})

    def graph_create_schema_attribute(self, type_id: str, name: str,
                                      datatype: str = 'entity_ref'
                                      ) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/node-types/<id>/schema - Attributdefinition.

        Fuer Vorgaben auf Typebene nutzen wir datatype entity_ref; laut
        Datentyp-Tabelle materialisiert der eine typisierte Kante. Der Body
        des Endpunkts ist in der Spezifikation nicht gezeigt, deshalb beim
        ersten Lauf gegen eine echte Instanz pruefen (Task 17).
        """
        return self._graph_request(
            'POST', f'/node-types/{quote(str(type_id), safe="")}/schema',
            data={'name': name, 'datatype': datatype})

    def graph_delete_schema_attribute(self, type_id: str,
                                      attribute_id: str) -> Optional[Dict[str, Any]]:
        """DELETE /secured/graph/node-types/<id>/schema/<aid>."""
        return self._graph_request(
            'DELETE',
            f'/node-types/{quote(str(type_id), safe="")}'
            f'/schema/{quote(str(attribute_id), safe="")}')

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

    def graph_delete_filter(self, node_id: str, filter_id: str) -> Optional[Dict[str, Any]]:
        """DELETE /secured/graph/nodes/<id>/filters/<fid>."""
        return self._graph_request(
            'DELETE',
            f'/nodes/{quote(str(node_id), safe="")}/filters/{quote(str(filter_id), safe="")}')

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

    def graph_restore_placement(self, placement_id: str) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/placements/<pid>/restore - expliziter Override."""
        return self._graph_request(
            'POST', f'/placements/{quote(str(placement_id), safe="")}/restore')

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
            logger.info("RBAC 404 (unbekannt oder fremd): %s %s", method, path)
            return None
        if response.status_code == 204:
            return {}
        try:
            return response.json() or {}
        except ValueError:
            logger.warning("RBAC-Antwort ohne JSON-Body: %s %s", method, path)
            return {}

    def access_groups(self) -> List[Dict[str, Any]]:
        """GET /secured/access_groups - der Gruppenbaum des Mandanten."""
        payload = self._rbac_request('GET', '/secured/access_groups') or {}
        return list(payload.get('groups') or [])

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

    def delete_access_group(self, identifier: str) -> bool:
        """DELETE /secured/access_groups/<id>. True, wenn geloescht."""
        result = self._rbac_request(
            'DELETE', f'/secured/access_groups/{quote(str(identifier), safe="")}')
        return result is not None

    def document_access(self, pointer: str) -> Optional[Dict[str, Any]]:
        """GET /secured/document_access - die ACL genau eines Dokuments."""
        return self._rbac_request(
            'GET', '/secured/document_access', params={'pointer': str(pointer)})

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
        return self._rbac_request('GET', '/secured/documents', params=params) or {}

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

    def _search_documents_secured(
        self,
        query: Union[str, List[str]],
        limit: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        endpoint = self.endpoints.get('query', '/secured/query')
        response = self._make_request(
            method='POST',
            endpoint=endpoint,
            data=self._secured_query_request_body(query, limit=limit, filters=filters),
        )
        result = _unwrap_secured_query_response(response.json())
        # Coerce to [] so a null/absent "results" never crashes None[:limit].
        raw_hits = (result.get("results") or [])[:limit]

        normalized_results = []
        for raw in raw_hits:
            if not isinstance(raw, dict):
                continue
            normalized_results.append(_secured_query_hit_to_row(raw))

        if normalized_results and all(
            r.get("page_number") is None
            and r.get("sentence_number") is None
            and not (r.get("top_chunks") or [])
            for r in normalized_results[:3]
        ):
            raw0 = raw_hits[0] if raw_hits and isinstance(raw_hits[0], dict) else {}
            logger.info(
                "Secured query: no page/sentence in first hits; raw[0] keys=%s "
                "page_number=%r sentence_number=%r top_chunks=%r",
                sorted(raw0.keys()) if raw0 else [],
                raw0.get("page_number"),
                raw0.get("sentence_number"),
                raw0.get("top_chunks") if isinstance(raw0.get("top_chunks"), list) else raw0.get("top_chunks"),
            )

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

        # Non-idempotent: never retry init/transmit — a retried 4xx/5xx (or a
        # retried connection error after the server already committed) would
        # create duplicate document parts. Use the no-retry request path.
        init_resp = self._request_no_retry(
            method='POST',
            endpoint=init_endpoint,
            data=init_body,
        ).json()

        transmission_key_id = init_resp.get('transmission_key_id')
        if not transmission_key_id:
            raise RuntimeError('init_document_transmission returned no transmission_key_id')

        part_endpoint = self.endpoints.get('transmit_part', '/secured/transmit_document_part')
        for idx, part in enumerate(parts):
            payload = _secured_transmit_part_payload(transmission_key_id, idx, part)
            if idx == 0 and ('page_number' in payload or 'sentence_number' in payload):
                logger.info(
                    "Secured transmit part 0 location page=%s sentence=%s doc=%s",
                    payload.get('page_number'),
                    payload.get('sentence_number'),
                    identifier,
                )
            self._request_no_retry(method='POST', endpoint=part_endpoint, data=payload)

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
