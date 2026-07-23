"""Upload files to Semantix Secure API via tenant mTLS."""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Tuple

import requests

from config import get_config
from sync.chunking import iter_text_chunks_with_location
from sync.document_text import ConversionError, extract_document

logger = logging.getLogger(__name__)

RETRY_STATUS = {429, 503, 504}
MAX_BACKOFF = 30.0


@dataclass
class UploadResult:
    relative_path: str
    transmission_key_id: Optional[str]
    parts: int
    status: str
    ingestion_requests: int
    error: Optional[str] = None


def _transmit_part_body(
    key: str,
    part_number: int,
    snippet: str,
    *,
    page_number: Optional[int] = None,
    sentence_number: Optional[int] = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "key": key,
        "part_number": part_number,
        "snippet": snippet,
    }
    if page_number is not None and page_number >= 1:
        body["page_number"] = int(page_number)
    if sentence_number is not None and sentence_number >= 1:
        body["sentence_number"] = int(sentence_number)
    return body


class SemantixUploader:
    def __init__(self, on_ingest_request: Optional[Callable[[], None]] = None):
        cfg = get_config()
        self._base = cfg.semantix_secure_base_url
        self._cert = (
            cfg.semantix_client_cert_path,
            cfg.semantix_client_key_path,
        )
        self._verify = cfg.semantix_ca_cert_path
        self._on_ingest = on_ingest_request or (lambda: None)

    def _request(
        self, method: str, path: str, *, json_body: Optional[dict] = None, max_retries: int = 5
    ) -> requests.Response:
        url = f"{self._base}{path}"
        backoff = 1.0
        last_exc: Optional[Exception] = None
        for attempt in range(max_retries):
            self._on_ingest()
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
                if attempt >= max_retries - 1:
                    raise
                time.sleep(backoff + random.uniform(-0.1, 0.1) * backoff)
                backoff = min(MAX_BACKOFF, backoff * 2)
                continue

            if resp.status_code not in RETRY_STATUS:
                return resp
            if attempt >= max_retries - 1:
                return resp
            jitter = random.uniform(-0.1, 0.1) * backoff
            time.sleep(backoff + jitter)
            backoff = min(MAX_BACKOFF, backoff * 2)
        raise last_exc or RuntimeError("request failed")

    def upload_file(
        self, file_path: Path, relative_path: str, sync_body: dict[str, Any]
    ) -> UploadResult:
        ingestion = sync_body.get("ingestion") or {}
        prefix = ingestion.get("identifier_prefix", "rc-sync")
        part_max = min(int(ingestion.get("part_max_chars", 50000)), 50000)
        identifier = f"{prefix}/{relative_path.replace(chr(92), '/')}"

        try:
            doc = extract_document(file_path)
            text, sentences = doc.text, doc.sentences
            extracted_title = doc.title
            parts_iter = iter_text_chunks_with_location(text, part_max, sentences=sentences)
            part_count = sum(
                1 for _ in iter_text_chunks_with_location(text, part_max, sentences=sentences)
            )
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
        title = extracted_title or file_path.name

        init_resp = self._request(
            "POST",
            "/secured/init_document_transmission",
            json_body={
                "identifier": identifier,
                "part_count": part_count,
                "title": title,
                "path": relative_path,
            },
        )
        ingestion_count = 1
        if init_resp.status_code not in (200, 201):
            return UploadResult(
                relative_path=relative_path,
                transmission_key_id=None,
                parts=part_count,
                status="error",
                ingestion_requests=ingestion_count,
                error=f"init failed: {init_resp.status_code}",
            )

        init_data = init_resp.json() if init_resp.content else {}
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
            )

        try:
            for idx, (snippet, page_number, sentence_number) in enumerate(parts_iter):
                if idx == 0 and (page_number is not None or sentence_number is not None):
                    logger.info(
                        "Transmit part 0 location page=%s sentence=%s file=%s",
                        page_number,
                        sentence_number,
                        file_path.name,
                    )
                part_resp = self._request(
                    "POST",
                    "/secured/transmit_document_part",
                    json_body=_transmit_part_body(
                        key,
                        idx,
                        snippet,
                        page_number=page_number,
                        sentence_number=sentence_number,
                    ),
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
                    )
        except (OSError, UnicodeDecodeError, ConversionError) as exc:
            return UploadResult(
                relative_path=relative_path,
                transmission_key_id=key,
                parts=part_count,
                status="error",
                ingestion_requests=ingestion_count,
                error=str(exc),
            )

        logger.info("Uploaded file basename=%s parts=%d status=ok", file_path.name, part_count)
        return UploadResult(
            relative_path=relative_path,
            transmission_key_id=key,
            parts=part_count,
            status="ok",
            ingestion_requests=ingestion_count,
        )
