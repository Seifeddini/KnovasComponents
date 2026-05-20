"""Upload files to Semantix Secure API via tenant mTLS."""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from config import get_config
from sync.chunking import chunk_text

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
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return UploadResult(
                relative_path=relative_path,
                transmission_key_id=None,
                parts=0,
                status="error",
                ingestion_requests=0,
                error=str(exc),
            )

        parts = chunk_text(text, part_max)
        title = file_path.name

        init_resp = self._request(
            "POST",
            "/secured/init_document_transmission",
            json_body={
                "identifier": identifier,
                "part_count": len(parts),
                "title": title,
            },
        )
        ingestion_count = 1
        if init_resp.status_code not in (200, 201):
            return UploadResult(
                relative_path=relative_path,
                transmission_key_id=None,
                parts=len(parts),
                status="error",
                ingestion_requests=ingestion_count,
                error=f"init failed: {init_resp.status_code}",
            )

        init_data = init_resp.json() if init_resp.content else {}
        key = init_data.get("key") or init_data.get("transmission_key_id") or ""

        for idx, snippet in enumerate(parts):
            part_resp = self._request(
                "POST",
                "/secured/transmit_document_part",
                json_body={
                    "key": key,
                    "part_number": idx,
                    "snippet": snippet,
                },
            )
            ingestion_count += 1
            if part_resp.status_code != 200:
                return UploadResult(
                    relative_path=relative_path,
                    transmission_key_id=key,
                    parts=len(parts),
                    status="error",
                    ingestion_requests=ingestion_count,
                    error=f"part {idx} failed: {part_resp.status_code}",
                )

        logger.info("Uploaded file basename=%s parts=%d status=ok", file_path.name, len(parts))
        return UploadResult(
            relative_path=relative_path,
            transmission_key_id=key,
            parts=len(parts),
            status="ok",
            ingestion_requests=ingestion_count,
        )
