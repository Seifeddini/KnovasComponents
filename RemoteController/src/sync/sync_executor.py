"""Orchestrate discover → read → upload with incremental state."""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from discover.filesystem import resolve_root
from sync.knovas_uploader import SemantixUploader, UploadResult
from sync.sync_config import effective_filters
from sync.sync_state import (
    DocumentSyncRecord,
    DocumentSyncStatus,
    DocumentSyncSummary,
    SyncStateStore,
)

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {".md", ".txt"}


def _matches_globs(rel_posix: str, patterns: list[str]) -> bool:
    path = Path(rel_posix)
    return any(path.match(g) or fnmatch.fnmatch(rel_posix, g) for g in patterns)


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


def is_within_max_document_age(
    mtime_iso: str,
    max_age_seconds: int,
    *,
    now: datetime | None = None,
) -> bool:
    """True if file mtime is at most max_age_seconds old (relative to now)."""
    if max_age_seconds < 1:
        return True
    reference = now or datetime.now(timezone.utc)
    mtime = datetime.fromisoformat(mtime_iso.replace("Z", "+00:00"))
    if mtime.tzinfo is None:
        mtime = mtime.replace(tzinfo=timezone.utc)
    age_seconds = (reference - mtime).total_seconds()
    return age_seconds <= max_age_seconds


def _document_status_for_inventory(
    state: SyncStateStore,
    rel: str,
    mtime_iso: str,
    size_bytes: int,
    *,
    max_age_seconds: int | None,
    now: datetime | None = None,
) -> DocumentSyncStatus:
    status = state.document_status(rel, mtime_iso, size_bytes)
    if status == "synced":
        return status
    if max_age_seconds is not None and not is_within_max_document_age(
        mtime_iso, max_age_seconds, now=now
    ):
        return "excluded_max_age"
    return status


def _iter_candidate_files(
    sync_body: dict[str, Any],
    *,
    should_stop: Callable[[], bool],
    filters: dict[str, Any] | None = None,
) -> list[tuple[Path, str, str, int]]:
    """Return all in-scope text files: (absolute_path, relative_path, mtime_iso, size_bytes)."""
    sources = sync_body.get("sources") or []
    filters = filters if filters is not None else (sync_body.get("filters") or {})
    include = filters.get("include_globs") or [
        "**/*.md",
        "**/*.txt",
        "*.md",
        "*.txt",
    ]
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
            if not _matches_globs(rel, include):
                continue
            if exclude and _matches_globs(rel, exclude):
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


def _build_document_inventory(
    state: SyncStateStore,
    scanned: list[tuple[str, str, int]],
    *,
    max_age_seconds: int | None,
    include_documents: bool,
    now: datetime | None = None,
) -> DocumentSyncSummary:
    summary = DocumentSyncSummary()
    for rel, mtime_iso, size_bytes in scanned:
        status = _document_status_for_inventory(
            state, rel, mtime_iso, size_bytes, max_age_seconds=max_age_seconds, now=now
        )
        summary.total += 1
        if status == "synced":
            summary.synced += 1
        elif status == "pending":
            summary.pending += 1
        elif status == "modified":
            summary.modified += 1
        else:
            summary.excluded_max_age += 1
        if include_documents:
            summary.documents.append(
                DocumentSyncRecord(
                    relative_path=rel,
                    mtime_iso=mtime_iso,
                    size_bytes=size_bytes,
                    status=status,
                )
            )
    return summary


def scan_document_inventory(
    sync_body: dict[str, Any],
    *,
    should_stop: Callable[[], bool] = lambda: False,
    include_documents: bool = False,
    sync_config: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> DocumentSyncSummary:
    """Scan all in-scope documents and classify sync status against local state."""
    filters = effective_filters(sync_body, sync_config)
    state = SyncStateStore()
    scanned = [
        (rel, mtime, size)
        for _, rel, mtime, size in _iter_candidate_files(
            sync_body, should_stop=should_stop, filters=filters
        )
    ]
    max_age = filters.get("max_document_age_seconds")
    max_age_seconds = int(max_age) if max_age is not None else None
    return _build_document_inventory(
        state,
        scanned,
        max_age_seconds=max_age_seconds,
        include_documents=include_documents,
        now=now,
    )


def _collect_files(
    sync_body: dict[str, Any],
    *,
    should_stop: Callable[[], bool],
    sync_config: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> list[tuple[Path, str, str, int]]:
    """Return files that need upload (pending or modified; all files in full mode)."""
    mode = sync_body.get("mode", "incremental")
    filters = effective_filters(sync_body, sync_config)
    candidates = _iter_candidate_files(sync_body, should_stop=should_stop, filters=filters)
    if mode != "incremental":
        max_age = filters.get("max_document_age_seconds")
        max_age_seconds = int(max_age) if max_age is not None else None
        if max_age_seconds is None:
            return candidates
        state = SyncStateStore()
        return [
            item
            for item in candidates
            if _document_status_for_inventory(
                state, item[1], item[2], item[3], max_age_seconds=max_age_seconds, now=now
            )
            != "excluded_max_age"
        ]

    state = SyncStateStore()
    max_age = filters.get("max_document_age_seconds")
    max_age_seconds = int(max_age) if max_age is not None else None
    return [
        item
        for item in candidates
        if _document_status_for_inventory(
            state, item[1], item[2], item[3], max_age_seconds=max_age_seconds, now=now
        )
        not in ("synced", "excluded_max_age")
    ]


def run_sync_work(
    sync_body: dict[str, Any],
    uploader: SemantixUploader,
    *,
    should_stop: Callable[[], bool] = lambda: False,
    is_in_sync_window: Callable[[], bool] = lambda: True,
    acquire_ingest_token: Callable[[], bool] = lambda: True,
    sync_config: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> SyncRunResult:
    result = SyncRunResult()
    state = SyncStateStore()
    inventory = scan_document_inventory(
        sync_body,
        should_stop=should_stop,
        include_documents=True,
        sync_config=sync_config,
        now=now,
    )
    result.document_sync = inventory
    result.files_scanned = inventory.total
    result.files_skipped = inventory.synced

    files = _collect_files(sync_body, should_stop=should_stop, sync_config=sync_config, now=now)

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
