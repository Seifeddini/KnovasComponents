"""Orchestrate discover → read → upload with incremental state."""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from discover.filesystem import resolve_root
from sync.semantix_uploader import SemantixUploader, UploadResult
from sync.sync_state import DocumentSyncSummary, SyncStateStore

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {".md", ".txt"}


@dataclass
class SyncRunResult:
    files_scanned: int = 0
    files_uploaded: int = 0
    files_skipped: int = 0
    ingestion_requests_sent: int = 0
    transmissions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    paused_reason: Optional[str] = None
    document_sync: Optional[DocumentSyncSummary] = None


def _iter_candidate_files(
    sync_body: dict[str, Any],
    *,
    should_stop: Callable[[], bool],
) -> list[tuple[Path, str, str, int]]:
    """Return all in-scope text files: (absolute_path, relative_path, mtime_iso, size_bytes)."""
    sources = sync_body.get("sources") or []
    filters = sync_body.get("filters") or {}
    include = filters.get("include_globs") or ["**/*.md", "**/*.txt"]
    exclude = filters.get("exclude_globs") or ["**/.git/**"]
    max_bytes = int(filters.get("max_file_bytes", 10_485_760))

    results: list[tuple[Path, str, str, int]] = []

    for source in sources:
        if should_stop():
            break
        root, err = resolve_root(source.get("path"))
        if err or root is None:
            continue
        recursive = source.get("recursive", True)
        iterator = root.rglob("*") if recursive else root.glob("*")
        for path in iterator:
            if should_stop():
                break
            if not path.is_file():
                continue
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            resolved = path.resolve()
            try:
                rel = resolved.relative_to(root).as_posix()
            except ValueError:
                continue
            if not any(fnmatch.fnmatch(rel, g) for g in include):
                continue
            if any(fnmatch.fnmatch(rel, g) for g in exclude):
                continue
            try:
                stat = resolved.stat()
            except OSError:
                continue
            if stat.st_size > max_bytes:
                continue
            mtime_iso = (
                datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            results.append((resolved, rel, mtime_iso, stat.st_size))
    return results


def scan_document_inventory(
    sync_body: dict[str, Any],
    *,
    should_stop: Callable[[], bool] = lambda: False,
    include_documents: bool = False,
) -> DocumentSyncSummary:
    """Scan all in-scope documents and classify sync status against local state."""
    state = SyncStateStore()
    scanned = [(rel, mtime, size) for _, rel, mtime, size in _iter_candidate_files(sync_body, should_stop=should_stop)]
    return state.summarize(scanned, include_documents=include_documents)


def _collect_files(
    sync_body: dict[str, Any],
    *,
    should_stop: Callable[[], bool],
) -> list[tuple[Path, str, str, int]]:
    """Return files that need upload (pending or modified; all files in full mode)."""
    mode = sync_body.get("mode", "incremental")
    candidates = _iter_candidate_files(sync_body, should_stop=should_stop)
    if mode != "incremental":
        return candidates

    state = SyncStateStore()
    return [
        item
        for item in candidates
        if state.document_status(item[1], item[2], item[3]) != "synced"
    ]


def run_sync_work(
    sync_body: dict[str, Any],
    uploader: SemantixUploader,
    *,
    should_stop: Callable[[], bool] = lambda: False,
    is_in_sync_window: Callable[[], bool] = lambda: True,
    acquire_ingest_token: Callable[[], bool] = lambda: True,
) -> SyncRunResult:
    result = SyncRunResult()
    state = SyncStateStore()
    inventory = scan_document_inventory(sync_body, should_stop=should_stop, include_documents=True)
    result.document_sync = inventory
    result.files_scanned = inventory.total
    result.files_skipped = inventory.synced

    files = _collect_files(sync_body, should_stop=should_stop)

    for abs_path, rel, mtime_iso, size_bytes in files:
        if should_stop():
            result.paused_reason = "stop_requested"
            break
        if not is_in_sync_window():
            result.paused_reason = "outside_window"
            break
        if not acquire_ingest_token():
            result.paused_reason = "rate_limited"
            break

        upload: UploadResult = uploader.upload_file(abs_path, rel, sync_body)
        result.ingestion_requests_sent += upload.ingestion_requests

        if upload.status == "ok":
            result.files_uploaded += 1
            if sync_body.get("mode") == "incremental" and upload.transmission_key_id:
                state.record_upload(rel, mtime_iso, size_bytes, upload.transmission_key_id)
            result.transmissions.append(
                {
                    "path": rel,
                    "transmission_key_id": upload.transmission_key_id,
                    "parts": upload.parts,
                    "status": "ok",
                }
            )
        else:
            result.errors.append({"path": rel, "error": upload.error or "upload failed"})
            result.transmissions.append({"path": rel, "status": "error", "parts": upload.parts})

    return result
