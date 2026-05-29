"""Orchestrate discover → read → upload with incremental state."""
from __future__ import annotations

import fnmatch
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from discover.filesystem import resolve_root
from sync.document_text import DEFAULT_INCLUDE_GLOBS, is_syncable_extension
from sync.knovas_uploader import SemantixUploader, UploadResult
from sync.subfolder_queue import SubfolderProgress, SubfolderQueue
from sync.sync_config import effective_filters
from sync.sync_state import (
    DocumentSyncRecord,
    DocumentSyncStatus,
    DocumentSyncSummary,
    SyncStateStore,
    status_from_fingerprint,
)

logger = logging.getLogger(__name__)


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
    transmissions_truncated: bool = False
    errors: list[dict[str, str]] = field(default_factory=list)
    paused_reason: Optional[str] = None
    document_sync: Optional[DocumentSyncSummary] = None
    scan_truncated: bool = False
    subfolder_progress: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class _WalkTarget:
    walk_root: Path
    rel_root: Path
    recursive: bool


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


def _classify_status(
    stored: Optional[tuple[str, int]],
    mtime_iso: str,
    size_bytes: int,
    *,
    max_age_seconds: int | None,
    now: datetime | None,
) -> DocumentSyncStatus:
    status = status_from_fingerprint(stored, mtime_iso, size_bytes)
    if status == "synced":
        return status
    if max_age_seconds is not None and not is_within_max_document_age(
        mtime_iso, max_age_seconds, now=now
    ):
        return "excluded_max_age"
    return status


def _relative_posix(path: Path, rel_root: Path) -> Optional[str]:
    try:
        return path.relative_to(rel_root).as_posix()
    except ValueError:
        return None


@dataclass
class _WalkBudget:
    """Caps directory visits so archives with few/no syncable files cannot walk forever."""

    max_dir_visits: int = 0
    dir_visits: int = 0
    truncated: bool = False

    def consume_dir(self) -> bool:
        """Record one directory visit. Returns False when the budget is exhausted."""
        self.dir_visits += 1
        if self.max_dir_visits > 0 and self.dir_visits > self.max_dir_visits:
            self.truncated = True
            return False
        return True


def _walk_text_files(
    walk_root: Path,
    rel_root: Path,
    *,
    recursive: bool,
    include: list[str],
    exclude: list[str],
    max_bytes: int,
    should_stop: Callable[[], bool],
    budget: Optional[_WalkBudget] = None,
) -> Iterator[tuple[Path, str, str, int]]:
    """Yield (absolute_path, relative_path, mtime_iso, size_bytes) using os.scandir."""
    stack: list[Path] = [walk_root]
    while stack:
        if should_stop():
            return
        current = stack.pop()
        if budget is not None and not budget.consume_dir():
            return
        if budget is not None and budget.dir_visits % 5000 == 0:
            logger.info("Scan progress: %d directories visited under %s", budget.dir_visits, walk_root.name)
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            if should_stop():
                return
            if budget is not None and budget.truncated:
                return
            try:
                if entry.is_dir(follow_symlinks=False):
                    if recursive:
                        stack.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            path = Path(entry.path)
            if not is_syncable_extension(path.suffix):
                continue
            rel = _relative_posix(path, rel_root)
            if rel is None:
                continue
            if not _matches_globs(rel, include):
                continue
            if exclude and _matches_globs(rel, exclude):
                continue
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.st_size > max_bytes:
                continue
            mtime_iso = (
                datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            yield path, rel, mtime_iso, stat.st_size


def _iter_candidate_files(
    walk_targets: list[_WalkTarget],
    *,
    should_stop: Callable[[], bool],
    filters: dict[str, Any],
    max_scan_entries: int = 0,
    budget: Optional[_WalkBudget] = None,
) -> Iterator[tuple[Path, str, str, int]]:
    """Yield in-scope files; honour directory visit budget and optional file cap."""
    include = filters.get("include_globs") or list(DEFAULT_INCLUDE_GLOBS)
    exclude = filters.get("exclude_globs") or ["**/.git/**"]
    max_bytes = int(filters.get("max_file_bytes", 10_485_760))
    files_yielded = 0

    for target in walk_targets:
        if should_stop() or (budget is not None and budget.truncated):
            break
        for item in _walk_text_files(
            target.walk_root,
            target.rel_root,
            recursive=target.recursive,
            include=include,
            exclude=exclude,
            max_bytes=max_bytes,
            should_stop=should_stop,
            budget=budget,
        ):
            yield item
            files_yielded += 1
            if max_scan_entries > 0 and files_yielded >= max_scan_entries:
                if budget is not None:
                    budget.truncated = True
                return
        if budget is not None and budget.truncated:
            break


def _needs_upload(status: DocumentSyncStatus, mode: str) -> bool:
    if mode != "incremental":
        return status != "excluded_max_age"
    return status in ("pending", "modified")


@dataclass
class _ScanPlan:
    summary: DocumentSyncSummary
    upload_queue: list[tuple[Path, str, str, int]]
    scan_truncated: bool = False


def _sequential_subfolders_enabled(sync_config: dict[str, Any] | None) -> bool:
    return bool(sync_config and sync_config.get("sequential_subfolders"))


def build_walk_targets(
    sync_body: dict[str, Any],
    sync_config: dict[str, Any] | None,
    queue: SubfolderQueue | None,
) -> tuple[list[_WalkTarget], Optional[SubfolderProgress]]:
    """Resolve filesystem walk targets for one scheduler cycle."""
    sources = sync_body.get("sources") or []
    if not sources:
        return [], None

    if not _sequential_subfolders_enabled(sync_config):
        targets: list[_WalkTarget] = []
        for source in sources:
            root, err = resolve_root(source.get("path"))
            if err or root is None:
                continue
            targets.append(
                _WalkTarget(
                    walk_root=root,
                    rel_root=root,
                    recursive=bool(source.get("recursive", True)),
                )
            )
        return targets, None

    if len(sources) != 1:
        logger.warning("sequential_subfolders requires exactly one source; using first only")
    source = sources[0]
    root, err = resolve_root(source.get("path"))
    if err or root is None or queue is None:
        return [], None

    progress = queue.progress(root)
    if progress.completed:
        return [], progress

    sub_path = queue.current_path(root)
    if sub_path is None:
        return [], progress

    return [
        _WalkTarget(walk_root=sub_path, rel_root=root, recursive=True),
    ], progress


def plan_sync_cycle(
    sync_body: dict[str, Any],
    state: SyncStateStore,
    *,
    should_stop: Callable[[], bool] = lambda: False,
    include_documents: bool = False,
    sync_config: dict[str, Any] | None = None,
    now: datetime | None = None,
    max_upload_files: int = 0,
    max_scan_entries: int = 0,
    queue: SubfolderQueue | None = None,
) -> _ScanPlan:
    """Single filesystem pass: inventory counts + upload queue."""
    filters = effective_filters(sync_body, sync_config)
    mode = sync_body.get("mode", "incremental")
    max_age = filters.get("max_document_age_seconds")
    max_age_seconds = int(max_age) if max_age is not None else None
    fingerprints = state.load_fingerprints()
    summary = DocumentSyncSummary()
    upload_queue: list[tuple[Path, str, str, int]] = []
    walk_targets, _ = build_walk_targets(sync_body, sync_config, queue)
    visit_cap = max_scan_entries if max_scan_entries > 0 else 0
    budget = _WalkBudget(max_dir_visits=visit_cap) if visit_cap else _WalkBudget()

    scanned = 0
    for abs_path, rel, mtime_iso, size_bytes in _iter_candidate_files(
        walk_targets,
        should_stop=should_stop,
        filters=filters,
        max_scan_entries=max_scan_entries,
        budget=budget,
    ):
        scanned += 1
        stored = state.lookup_stored(rel, fingerprints)
        status = _classify_status(
            stored, mtime_iso, size_bytes, max_age_seconds=max_age_seconds, now=now
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
        if _needs_upload(status, mode):
            if max_upload_files <= 0 or len(upload_queue) < max_upload_files:
                upload_queue.append((abs_path, rel, mtime_iso, size_bytes))

    scan_truncated = budget.truncated

    return _ScanPlan(summary=summary, upload_queue=upload_queue, scan_truncated=scan_truncated)


def scan_document_inventory(
    sync_body: dict[str, Any],
    *,
    should_stop: Callable[[], bool] = lambda: False,
    include_documents: bool = False,
    sync_config: dict[str, Any] | None = None,
    now: datetime | None = None,
    max_scan_entries: int = 0,
) -> DocumentSyncSummary:
    """Scan in-scope documents and classify sync status against local state."""
    state = SyncStateStore()
    queue: SubfolderQueue | None = None
    try:
        if _sequential_subfolders_enabled(sync_config):
            queue = SubfolderQueue.from_config()
        return plan_sync_cycle(
            sync_body,
            state,
            should_stop=should_stop,
            include_documents=include_documents,
            sync_config=sync_config,
            now=now,
            max_scan_entries=max_scan_entries,
            queue=queue,
        ).summary
    finally:
        if queue is not None:
            queue.close()
        state.close()


def _collect_files(
    sync_body: dict[str, Any],
    *,
    should_stop: Callable[[], bool],
    sync_config: dict[str, Any] | None = None,
    now: datetime | None = None,
    max_upload_files: int = 0,
) -> list[tuple[Path, str, str, int]]:
    """Return files that need upload (pending or modified; all in-scope in full mode)."""
    state = SyncStateStore()
    try:
        return plan_sync_cycle(
            sync_body,
            state,
            should_stop=should_stop,
            sync_config=sync_config,
            now=now,
            max_upload_files=max_upload_files,
        ).upload_queue
    finally:
        state.close()


def _max_files_per_cycle(sync_config: dict[str, Any] | None) -> int:
    if not sync_config:
        return 0
    raw = sync_config.get("max_files_per_cycle")
    if raw is None:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _max_scan_entries_per_cycle(sync_config: dict[str, Any] | None) -> int:
    if not sync_config:
        return 10000
    raw = sync_config.get("max_scan_entries_per_cycle")
    if raw is None:
        return 10000 if sync_config.get("sequential_subfolders") else 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _default_max_sync_duration_minutes(sync_config: dict[str, Any] | None) -> Optional[int]:
    if not sync_config:
        return None
    raw = sync_config.get("max_sync_duration_minutes")
    if raw is not None:
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            pass
    if sync_config.get("sequential_subfolders"):
        return 120
    return None


def run_sync_work(
    sync_body: dict[str, Any],
    uploader: SemantixUploader,
    *,
    should_stop: Callable[[], bool] = lambda: False,
    is_in_sync_window: Callable[[], bool] = lambda: True,
    acquire_ingest_token: Callable[[], bool] = lambda: True,
    sync_config: dict[str, Any] | None = None,
    now: datetime | None = None,
    max_transmissions_in_response: int = 100,
) -> SyncRunResult:
    result = SyncRunResult()
    state = SyncStateStore()
    queue: SubfolderQueue | None = None
    sequential = _sequential_subfolders_enabled(sync_config)
    source_root: Path | None = None

    try:
        if sequential:
            queue = SubfolderQueue.from_config()
            sources = sync_body.get("sources") or []
            if sources:
                source_root, _ = resolve_root(sources[0].get("path"))

        max_scan = _max_scan_entries_per_cycle(sync_config)
        plan = plan_sync_cycle(
            sync_body,
            state,
            should_stop=should_stop,
            include_documents=False,
            sync_config=sync_config,
            now=now,
            max_upload_files=_max_files_per_cycle(sync_config),
            max_scan_entries=max_scan,
            queue=queue,
        )
        _, progress = build_walk_targets(sync_body, sync_config, queue)
        if progress is not None:
            result.subfolder_progress = progress.as_dict()

        result.document_sync = plan.summary
        result.files_scanned = plan.summary.total
        result.files_skipped = plan.summary.synced
        result.scan_truncated = plan.scan_truncated
        if plan.scan_truncated:
            result.paused_reason = "scan_limit_reached"

        for abs_path, rel, mtime_iso, size_bytes in plan.upload_queue:
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

            tx_entry: dict[str, Any]
            if upload.status == "ok":
                result.files_uploaded += 1
                if sync_body.get("mode") == "incremental" and upload.transmission_key_id:
                    state.record_upload(rel, mtime_iso, size_bytes, upload.transmission_key_id)
                tx_entry = {
                    "path": rel,
                    "transmission_key_id": upload.transmission_key_id,
                    "parts": upload.parts,
                    "status": "ok",
                }
            else:
                result.errors.append({"path": rel, "error": upload.error or "upload failed"})
                tx_entry = {"path": rel, "status": "error", "parts": upload.parts}

            if max_transmissions_in_response > 0 and len(result.transmissions) >= max_transmissions_in_response:
                result.transmissions_truncated = True
            else:
                result.transmissions.append(tx_entry)

        if sequential and queue is not None and source_root is not None and result.document_sync is not None:
            ds = result.document_sync
            if result.scan_truncated and ds.total == 0:
                # No syncable files in this slice (typical for .doc-only WinJur buckets) — move on.
                logger.info(
                    "Subfolder scan cap hit with no syncable files; advancing to next folder"
                )
                queue.maybe_advance(
                    source_root,
                    pending=0,
                    modified=0,
                    scan_truncated=False,
                    paused_reason=None,
                )
            else:
                queue.maybe_advance(
                    source_root,
                    pending=ds.pending,
                    modified=ds.modified,
                    scan_truncated=result.scan_truncated,
                    paused_reason=result.paused_reason,
                )
            if (
                not result.scan_truncated
                and ds.total == 0
                and ds.pending == 0
                and ds.modified == 0
                and result.paused_reason not in ("stop_requested", "outside_window", "rate_limited")
            ):
                queue.maybe_advance(
                    source_root,
                    pending=0,
                    modified=0,
                    scan_truncated=False,
                    paused_reason=None,
                )
            updated = queue.progress(source_root)
            result.subfolder_progress = updated.as_dict()
    finally:
        if queue is not None:
            queue.close()
        state.close()

    return result
