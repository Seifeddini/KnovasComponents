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
from sync.chunking import PART_MAX_CHARS, build_transmission_parts
from sync.context_sidecar import context_store_dir_from_env, write_context_sidecar
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
        part_max = min(int(ingestion.get("part_max_chars", PART_MAX_CHARS)), PART_MAX_CHARS)
        identifier = f"{prefix}/{relative_path.replace(chr(92), '/')}"

        try:
            doc = extract_document(file_path)
            text, sentences = doc.text, doc.sentences
            extracted_title = doc.title
            parts = build_transmission_parts(
                text,
                part_max,
                sentences=sentences,
                sections=doc.sections,
                pages=doc.pages,
                tables=doc.tables,
            )
            write_context_sidecar(
                context_store_dir_from_env(),
                identifier,
                relative_path,
                text,
                sentences,
            )
            part_count = len(parts)
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

        init_body: dict[str, Any] = {
            "identifier": identifier,
            "part_count": part_count,
            "title": title,
            "path": relative_path,
        }
        description = (sync_body.get("ingestion") or {}).get("description") or doc.description
        if description:
            init_body["description"] = str(description).strip()[:2000]

        init_resp = self._request(
            "POST",
            "/secured/init_document_transmission",
            json_body=init_body,
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
