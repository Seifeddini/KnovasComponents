"""Sync scheduler state machine with window and ingest gating."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from config import get_config
from sync.ingest_rate_limit import acquire as acquire_ingest
from sync.ingest_rate_limit import configure as configure_ingest
from sync.sync_config import load_sync_config
from sync.sync_executor import SyncRunResult, run_sync_work
from sync.knovas_uploader import SemantixUploader
from sync.window import is_in_window

logger = logging.getLogger(__name__)

_scheduler_lock = threading.Lock()
_stop_event = threading.Event()
_worker_thread: Optional[threading.Thread] = None
_current_status = "awaiting_initial_sync_body"
_last_run_at: Optional[str] = None
_files_processed = 0
_last_document_sync: Optional[dict[str, Any]] = None
_idle_scan_multiplier: int = 1


@dataclass
class SyncRunContext:
    sync_body: dict[str, Any]
    sync_config: dict[str, Any]


def _timezone() -> ZoneInfo:
    cfg = get_config()
    if cfg.rc_timezone:
        return ZoneInfo(cfg.rc_timezone)
    return datetime.now().astimezone().tzinfo or ZoneInfo("UTC")


def _last_sync_body_path() -> Path:
    """Writable path next to RC_SYNC_STATE_PATH (e.g. /var/rc-state/)."""
    state_path = Path(get_config().rc_sync_state_path)
    parent = state_path.parent if state_path.name else Path(".")
    return parent / ".rc-sync-last-request.json"


def save_last_sync_body(body: dict[str, Any]) -> None:
    p = _last_sync_body_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(body, f)
        os.replace(tmp, p)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_last_sync_body() -> Optional[dict[str, Any]]:
    p = _last_sync_body_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _synced_paths_in_state() -> int:
    """Paths recorded after successful upload (persists across restarts)."""
    from sync.sync_state import SyncStateStore

    store = SyncStateStore()
    try:
        return store.count_tracked_paths()
    finally:
        store.close()


def get_scheduler_status() -> dict[str, Any]:
    from sync.sync_config import config_snapshot_hash

    cfg = load_sync_config()
    synced_local = _synced_paths_in_state()
    return {
        "scheduler_state": _current_status,
        "last_run_at": _last_run_at,
        "current_status": _current_status,
        # Incremented only when a full scheduler cycle (_run_once) finishes.
        "files_processed": _files_processed,
        # Live count from .rc-sync-state.json — use this while a long cycle is running.
        "files_synced_local": synced_local,
        "document_sync": _last_document_sync,
        "config_snapshot_hash": config_snapshot_hash(cfg),
        "worker_alive": _worker_thread is not None and _worker_thread.is_alive(),
    }


def _set_status(status: str) -> None:
    global _current_status
    _current_status = status


def _run_once(ctx: SyncRunContext) -> SyncRunResult:
    global _last_run_at, _files_processed, _last_document_sync
    cfg_doc = ctx.sync_config
    if not cfg_doc.get("enabled", True):
        _set_status("disabled")
        return SyncRunResult()

    tz = _timezone()
    window = cfg_doc.get("window") or {}
    rl = cfg_doc.get("rate_limit") or {}
    configure_ingest(
        int(rl.get("max_ingestion_requests_per_minute", 30)),
        int(rl.get("burst", 5)),
    )

    now = datetime.now(timezone.utc)
    if not is_in_window(now, window.get("start_local", "08:00"), window.get("end_local", "20:00"), tz):
        _set_status("paused_outside_window")
        return SyncRunResult(paused_reason="outside_window")

    _set_status("running")
    logger.info("Sync cycle starting (mode=%s)", ctx.sync_body.get("mode", "incremental"))
    max_duration = cfg_doc.get("max_sync_duration_minutes")
    deadline = (
        time.monotonic() + float(max_duration) * 60 if max_duration else None
    )

    def should_stop() -> bool:
        if _stop_event.is_set():
            return True
        if deadline and time.monotonic() >= deadline:
            return True
        return False

    def in_window() -> bool:
        return is_in_window(
            datetime.now(timezone.utc),
            window.get("start_local", "08:00"),
            window.get("end_local", "20:00"),
            tz,
        )

    def ingest_token() -> bool:
        if not acquire_ingest():
            _set_status("rate_limited_wait")
            return False
        return True

    uploader = SemantixUploader()
    logger.info("Sync cycle scanning corpus and uploading (rate_limit=%s/min)", rl.get("max_ingestion_requests_per_minute", 30))
    result = run_sync_work(
        ctx.sync_body,
        uploader,
        should_stop=should_stop,
        is_in_sync_window=in_window,
        acquire_ingest_token=ingest_token,
        sync_config=ctx.sync_config,
    )
    _last_run_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _files_processed += result.files_uploaded
    logger.info(
        "Sync cycle finished uploaded=%d scanned=%d errors=%d paused=%s",
        result.files_uploaded,
        result.files_scanned,
        len(result.errors),
        result.paused_reason,
    )
    if result.document_sync is not None:
        _last_document_sync = result.document_sync.as_dict()

    if result.paused_reason:
        _set_status(result.paused_reason if result.paused_reason != "outside_window" else "paused_outside_window")
    else:
        _set_status("completed")
    return result


def run_one_time(ctx: SyncRunContext) -> tuple[str, SyncRunResult]:
    if not _scheduler_lock.acquire(blocking=False):
        return "already_running", SyncRunResult()
    try:
        _stop_event.clear()
        result = _run_once(ctx)
        return _current_status, result
    finally:
        _scheduler_lock.release()


def _effective_scan_interval_seconds(cfg_doc: dict[str, Any], result: SyncRunResult) -> int:
    global _idle_scan_multiplier
    base = max(5, int(cfg_doc.get("scan_interval_seconds", 60)))
    idle_max = int(cfg_doc.get("scan_interval_idle_max_seconds", 3600))
    idle_max = max(base, idle_max)
    pending_work = 0
    if result.document_sync is not None:
        pending_work = result.document_sync.pending + result.document_sync.modified
    if result.files_uploaded == 0 and pending_work == 0 and result.files_scanned > 0:
        cap = max(1, idle_max // base)
        _idle_scan_multiplier = min(_idle_scan_multiplier * 2, cap)
    else:
        _idle_scan_multiplier = 1
    return min(base * _idle_scan_multiplier, idle_max)


def _continuous_worker(ctx: SyncRunContext) -> None:
    cfg_doc = ctx.sync_config
    while not _stop_event.is_set():
        result = _run_once(ctx)
        interval = _effective_scan_interval_seconds(cfg_doc, result)
        for _ in range(interval):
            if _stop_event.is_set():
                break
            time.sleep(1)
    _set_status("not_running")


def start_continuous(ctx: SyncRunContext) -> str:
    global _worker_thread
    if not _scheduler_lock.acquire(blocking=False):
        return "already_running"

    def _worker_wrapper() -> None:
        try:
            _continuous_worker(ctx)
        finally:
            try:
                _scheduler_lock.release()
            except RuntimeError:
                pass

    _stop_event.clear()
    _worker_thread = threading.Thread(target=_worker_wrapper, daemon=True)
    _worker_thread.start()
    _set_status("running")
    return "running"


def stop_continuous() -> str:
    _stop_event.set()
    if _worker_thread and _worker_thread.is_alive():
        _worker_thread.join(timeout=120)
    _set_status("not_running")
    return "not_running"


def maybe_auto_start() -> None:
    cfg = get_config()
    if not cfg.rc_sync_auto_start_continuous:
        return
    sync_cfg = load_sync_config()
    if not sync_cfg.get("enabled") or sync_cfg.get("mode") != "continuous":
        return
    body = load_last_sync_body()
    if cfg.rc_sync_auto_start_requires_saved_body and not body:
        _set_status("awaiting_initial_sync_body")
        return
    if body:
        start_continuous(SyncRunContext(sync_body=body, sync_config=sync_cfg))
