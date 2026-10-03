"""Upload files to Semantix Secure API via tenant mTLS."""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from config import doc_fields_enabled, get_config
from sync import doc_fields_metrics, extract_metrics, ocr_metrics
from sync.ingest_rate_limit import acquire_chars, acquire_request
from sync.rate_metrics import IngestRateMetrics
from sync.chunking import PART_MAX_CHARS, build_transmission_parts
from sync.context_sidecar import context_store_dir_from_env, write_context_sidecar
from sync.document_text import (
    ConversionError,
    ExtractedDocument,
    _env_flag,
    extract_document_guarded,
    partial_note_for,
    pdf_ocr_enabled,
)
from sync.doc_fields_payload import (
    FieldsOutcome,
    SourceSpec,
    assemble,
    classify_init_refusal,
    config_digest,
    fields_to_send,
    is_doc_fields_unavailable,
    outcome_after_init,
    parse_init_echo,
    refused_outcome,
)

logger = logging.getLogger(__name__)

RETRY_STATUS = {429, 503, 504}
MAX_BACKOFF = 30.0
INIT_PATH = "/secured/init_document_transmission"
#: The Secure API refuses longer titles (secure_api.py init validation); an
#: uncapped one made such a file fail its init every cycle.
MAX_TITLE_CHARS = 500


def _json_or_none(resp: requests.Response) -> Any:
    try:
        return resp.json() if resp.content else None
    except ValueError:
        return None


def _retry_unless_doc_fields_unavailable(resp: requests.Response) -> bool:
    """``retry_status`` of an init that carries ``fields``: a 503 whose
    ``error_code`` starts with ``doc_fields_`` comes back at once (the RC
    re-posts without fields) instead of after five backoff rounds."""
    if resp.status_code not in RETRY_STATUS:
        return False
    return not is_doc_fields_unavailable(resp.status_code, _json_or_none(resp))


def page_break_markers_enabled() -> bool:
    """`RC_PAGE_BREAK_MARKERS` (default true): form feeds before every text
    page start inside a part, so the server serves hits on their own page
    (GI-INGEST-17)."""
    return _env_flag("RC_PAGE_BREAK_MARKERS", True)


def send_pdf_tables_enabled() -> bool:
    """`RC_SEND_PDF_TABLES` (default false): the server drops `tables` at
    the Redis buffer — only snippet, page and sentence survive — so for PDFs
    the rows have to live in the text and the payload is wasted bytes."""
    return _env_flag("RC_SEND_PDF_TABLES", False)


@dataclass
class UploadResult:
    relative_path: str
    transmission_key_id: Optional[str]
    parts: int
    status: str
    ingestion_requests: int
    error: Optional[str] = None
    #: Counts and reasons when the text landed only in part (OCR pages
    #: skipped on a budget trip, no OCR backend although one was configured);
    #: None for a complete document. Recorded by the executor (GI-EXTRACT-02).
    partial: Optional[dict[str, Any]] = None
    #: What happened to the init ``fields`` (spec 3.6); None when the upload
    #: was not given a ``source`` or the init did not succeed.
    fields: Optional[FieldsOutcome] = None
    #: Values left out of ``fields`` before sending, by reason (counts).
    fields_dropped: dict[str, int] = field(default_factory=dict)


def _record_cache_metrics(doc: ExtractedDocument) -> None:
    extra = doc.extra or {}
    hits = extra.get("rc:ocr_cache_hits")
    misses = extra.get("rc:ocr_cache_misses")
    if isinstance(hits, int) and hits > 0:
        ocr_metrics.OCR_CACHE_HITS.inc(hits)
    if isinstance(misses, int) and misses > 0:
        ocr_metrics.OCR_CACHE_MISSES.inc(misses)


def _transmit_part_body(
    key: str,
    part_number: int,
    part: dict[str, Any],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "key": key,
        "part_number": part_number,
        "snippet": part["snippet"],
    }
    page_number = part.get("page_number")
    sentence_number = part.get("sentence_number")
    if page_number is not None and int(page_number) >= 1:
        body["page_number"] = int(page_number)
    if sentence_number is not None and int(sentence_number) >= 1:
        body["sentence_number"] = int(sentence_number)
    tables = part.get("tables")
    if tables:
        body["tables"] = tables
    return body


class SemantixUploader:
    def __init__(
        self,
        on_ingest_request: Optional[Callable[[], None]] = None,
        rate_metrics: Optional[IngestRateMetrics] = None,
    ):
        cfg = get_config()
        self._base = cfg.semantix_secure_base_url
        self._cert = (
            cfg.semantix_client_cert_path,
            cfg.semantix_client_key_path,
        )
        self._verify = cfg.semantix_ca_cert_path
        self._on_ingest = on_ingest_request or (lambda: None)
        self._rate_metrics = rate_metrics

    def _ingest_char_cost(self, json_body: Optional[dict]) -> int:
        if not json_body:
            return 1
        snippet = json_body.get("snippet")
        if isinstance(snippet, str) and snippet:
            return max(len(snippet), 1)
        return 1

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        max_retries: int = 5,
        retry_status: Optional[Callable[[requests.Response], bool]] = None,
    ) -> requests.Response:
        """One API call with backoff. ``retry_status`` decides whether an
        answer is retried; default: its status is in ``RETRY_STATUS``."""
        should_retry = retry_status or (lambda resp: resp.status_code in RETRY_STATUS)
        url = f"{self._base}{path}"
        backoff = 1.0
        last_exc: Optional[Exception] = None
        for attempt in range(max_retries):
            cost = self._ingest_char_cost(json_body)
            if not acquire_chars(cost) or not acquire_request():
                raise requests.RequestException("ingest rate limit exceeded")
            self._on_ingest()
            started = time.monotonic()
            try:
                resp = requests.request(
                    method,
                    url,
                    json=json_body,
                    cert=self._cert,
                    verify=self._verify,
                    timeout=120,
                )
            except requests.RequestException as exc:
                last_exc = exc
                if self._rate_metrics is not None:
                    self._rate_metrics.record_request(
                        chars=cost,
                        latency_seconds=time.monotonic() - started,
                        http_status=0,
                        success=False,
                    )
                if attempt >= max_retries - 1:
                    raise
                time.sleep(backoff + random.uniform(-0.1, 0.1) * backoff)
                backoff = min(MAX_BACKOFF, backoff * 2)
                continue

            if self._rate_metrics is not None:
                self._rate_metrics.record_request(
                    chars=cost,
                    latency_seconds=time.monotonic() - started,
                    http_status=resp.status_code,
                    success=200 <= resp.status_code < 300,
                )

            if not should_retry(resp):
                return resp
            if attempt >= max_retries - 1:
                return resp
            jitter = random.uniform(-0.1, 0.1) * backoff
            time.sleep(backoff + jitter)
            backoff = min(MAX_BACKOFF, backoff * 2)
        raise last_exc or RuntimeError("request failed")

    def upload_file(
        self,
        file_path: Path,
        relative_path: str,
        sync_body: dict[str, Any],
        access_groups: tuple[str, ...] = (),
        *,
        source: Optional[SourceSpec] = None,
        previous_fields_sent: bool = False,
    ) -> UploadResult:
        """Extract, init and transmit one file.

        ``source`` is the governing source's ``SourceSpec``: its access
        groups win over ``access_groups``, and its fields configuration
        becomes the init ``fields`` (spec 3.6). ``previous_fields_sent``
        (from the state row) makes an empty payload a ``{}`` clear. Without
        ``source`` the init body is exactly what it was before fields.
        """
        if source is not None:
            access_groups = source.access_groups
        ingestion = sync_body.get("ingestion") or {}
        prefix = ingestion.get("identifier_prefix", "rc-sync")
        part_max = min(int(ingestion.get("part_max_chars", PART_MAX_CHARS)), PART_MAX_CHARS)
        identifier = f"{prefix}/{relative_path.replace(chr(92), '/')}"

        ext = file_path.suffix.lower()
        try:
            doc = extract_document_guarded(file_path, document_key=relative_path)
            # Counted here, in the process that serves /metrics: the
            # extraction child's registry died with it (spec L5).
            extract_metrics.record_extraction(doc)
            text, sentences = doc.text, doc.sentences
            extracted_title = doc.title
            tables = doc.tables
            if ext == ".pdf" and not send_pdf_tables_enabled():
                tables = None
            parts = build_transmission_parts(
                text,
                part_max,
                sentences=sentences,
                sections=doc.sections,
                pages=doc.pages,
                tables=tables,
                page_markers=page_break_markers_enabled(),
            )
            # Always from the UNMARKED text: the sidecar's offsets are the
            # extractor's, the markers exist only on the wire.
            write_context_sidecar(
                context_store_dir_from_env(),
                identifier,
                relative_path,
                text,
                sentences,
            )
            part_count = len(parts)
            partial = partial_note_for(doc, expect_ocr=(ext == ".pdf" and bool(pdf_ocr_enabled())))
            _record_cache_metrics(doc)
            if partial and partial.get("reason") == "ocr_backend_none":
                ocr_metrics.OCR_BACKEND_DEGRADED.inc()
        except Exception as exc:
            return UploadResult(
                relative_path=relative_path,
                transmission_key_id=None,
                parts=0,
                status="error",
                ingestion_requests=0,
                error=str(exc),
            )

        # Prefer the extractor-supplied title (email subject, PDF /Title, DOCX
        # core.xml title) so email search on the subject line still works after
        # migrating off the legacy '# Subject' body-prefix shape. Falls back to
        # filename when no title was extracted.
        title = (extracted_title or file_path.name)[:MAX_TITLE_CHARS]

        init_body: dict[str, Any] = {
            "identifier": identifier,
            "part_count": part_count,
            "title": title,
            "path": relative_path,
        }
        description = (sync_body.get("ingestion") or {}).get("description") or doc.description
        if description:
            init_body["description"] = str(description).strip()[:2000]
        if access_groups:
            # Only when set. An absent key lets the Secure API apply the
            # folder rule for this pointer; an explicit [] would mean
            # "deliberately unrestricted" and would override it.
            init_body["access_groups"] = list(access_groups)

        # Knovas document fields (spec 3.6): only with a source, only while
        # RC_DOC_FIELDS is on, and only when there is something to send --
        # values, or {} to clear values an earlier upload staged. Every other
        # init body stays byte-identical to the one before fields existed.
        fields_on = source is not None and doc_fields_enabled()
        fields_value: Optional[dict[str, Any]] = None
        fields_digest = ""
        dropped: dict[str, int] = {}
        if fields_on:
            payload = assemble(relative_path, source, doc.source_metadata, ext)
            dropped = {k: int(v) for k, v in payload.dropped.items() if v}
            doc_fields_metrics.record_dropped(dropped)
            fields_value = fields_to_send(payload, previous_fields_sent)
            fields_digest = config_digest(relative_path, source)
            if fields_value is not None:
                init_body["fields"] = fields_value

        fields_outcome: Optional[FieldsOutcome] = None
        if fields_value is None:
            init_resp = self._request("POST", INIT_PATH, json_body=init_body)
        else:
            init_resp = self._request(
                "POST",
                INIT_PATH,
                json_body=init_body,
                retry_status=_retry_unless_doc_fields_unavailable,
            )
        ingestion_count = 1
        if init_resp.status_code not in (200, 201) and fields_value is not None:
            code = classify_init_refusal(init_resp.status_code, _json_or_none(init_resp))
            if code is not None:
                # D5: fields never block indexing. Re-post once without them;
                # only if that succeeds were the fields the cause.
                retry_body = {k: v for k, v in init_body.items() if k != "fields"}
                init_resp = self._request("POST", INIT_PATH, json_body=retry_body)
                ingestion_count += 1
                if init_resp.status_code in (200, 201):
                    fields_outcome = refused_outcome(code, fields_digest)
        if init_resp.status_code not in (200, 201):
            return UploadResult(
                relative_path=relative_path,
                transmission_key_id=None,
                parts=part_count,
                status="error",
                ingestion_requests=ingestion_count,
                error=f"init failed: {init_resp.status_code}",
                fields_dropped=dropped,
            )

        init_data = init_resp.json() if init_resp.content else {}
        if fields_on and fields_outcome is None:
            echo = parse_init_echo(init_data) if fields_value is not None else None
            fields_outcome = outcome_after_init(fields_value, echo, fields_digest)
        if fields_outcome is not None and fields_outcome.fields_sent:
            logger.info(fields_outcome.log_line())
        key = init_data.get("key") or init_data.get("transmission_key_id") or ""
        if not key:
            # A 200 with no key means the server never opened a transmission.
            # Returning "ok" here would drop the document (it is never recorded
            # locally, so it re-uploads every cycle). Treat it as a retryable
            # error instead - not skippable, so it is retried next cycle.
            return UploadResult(
                relative_path=relative_path,
                transmission_key_id=None,
                parts=part_count,
                status="error",
                ingestion_requests=ingestion_count,
                error="init failed: missing transmission key",
                fields_dropped=dropped,
            )

        try:
            for idx, part in enumerate(parts):
                if idx == 0 and (
                    part.get("page_number") is not None or part.get("sentence_number") is not None
                ):
                    logger.info(
                        "Transmit part 0 location page=%s sentence=%s file=%s",
                        part.get("page_number"),
                        part.get("sentence_number"),
                        file_path.name,
                    )
                part_resp = self._request(
                    "POST",
                    "/secured/transmit_document_part",
                    json_body=_transmit_part_body(key, idx, part),
                )
                ingestion_count += 1
                if part_resp.status_code != 200:
                    return UploadResult(
                        relative_path=relative_path,
                        transmission_key_id=key,
                        parts=part_count,
                        status="error",
                        ingestion_requests=ingestion_count,
                        error=f"part {idx} failed: {part_resp.status_code}",
                        fields_dropped=dropped,
                    )
        except (OSError, UnicodeDecodeError, ConversionError) as exc:
            return UploadResult(
                relative_path=relative_path,
                transmission_key_id=key,
                parts=part_count,
                status="error",
                ingestion_requests=ingestion_count,
                error=str(exc),
                fields_dropped=dropped,
            )

        logger.info(
            "Uploaded file basename=%s parts=%d status=ok%s",
            file_path.name,
            part_count,
            f" partial={partial}" if partial else "",
        )
        return UploadResult(
            relative_path=relative_path,
            transmission_key_id=key,
            parts=part_count,
            status="ok",
            ingestion_requests=ingestion_count,
            partial=partial,
            fields=fields_outcome,
            fields_dropped=dropped,
        )

    def delete_by_pointer(self, pointer: str) -> tuple[bool, Optional[str]]:
        """DELETE /secured/delete_information_object. 404 is treated as success."""
        pointer = str(pointer or "").strip()
        if not pointer:
            return False, "pointer is required"
        resp = self._request(
            "DELETE",
            "/secured/delete_information_object",
            json_body={"pointer": pointer},
        )
        if resp.status_code in (200, 404):
            return True, None
        return False, f"delete failed: {resp.status_code}"
