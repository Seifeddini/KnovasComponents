"""Background thread launcher for the OneDrive mirror.

A single daemon thread loops forever:
  - run one mirror pass
  - log stats
  - sleep ``ONEDRIVE_MIRROR_INTERVAL_SECONDS`` (default 300)

Failures within a single pass are caught and logged so the loop keeps running.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from config import get_config
from onedrive_mirror.graph import GraphAuthError, GraphClient, GraphRequestError
from onedrive_mirror.mirror import OneDriveMirror

logger = logging.getLogger(__name__)

_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def _split_csv(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


def start_mirror_thread_if_configured() -> Optional[threading.Thread]:
    """Spawn the mirror daemon if all required OneDrive env vars are set."""
    global _thread

    if _thread is not None and _thread.is_alive():
        return _thread

    cfg = get_config()
    drive_id = (os.environ.get("ONEDRIVE_DRIVE_ID") or "").strip()
    tenant_id = (os.environ.get("ONEDRIVE_TENANT_ID") or "").strip()
    client_id = (os.environ.get("ONEDRIVE_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("ONEDRIVE_CLIENT_SECRET") or "").strip()

    if not (drive_id and tenant_id and client_id and client_secret):
        logger.info(
            "OneDrive mirror: not configured (missing one of "
            "ONEDRIVE_DRIVE_ID/TENANT_ID/CLIENT_ID/CLIENT_SECRET) — skipping."
        )
        return None

    mirror_path = _resolve_mirror_path(
        os.environ.get("ONEDRIVE_MIRROR_PATH", ""), cfg.rc_watch_roots
    )

    if cfg.rc_watch_roots:
        allowed_roots = [Path(p).resolve() for p in cfg.rc_watch_roots]
        if not any(_is_within(allowed, mirror_path) for allowed in allowed_roots):
            logger.warning(
                "OneDrive mirror: ONEDRIVE_MIRROR_PATH=%s is not within any "
                "RC_WATCH_ROOTS entry (%s). RC discover/sync won't pick up the "
                "mirrored files until you fix this.",
                mirror_path,
                ", ".join(cfg.rc_watch_roots),
            )

    interval = _safe_int(os.environ.get("ONEDRIVE_MIRROR_INTERVAL_SECONDS"), 300)
    max_mb = _safe_int(os.environ.get("ONEDRIVE_MAX_FILE_SIZE_MB"), 0)
    max_bytes = max_mb * 1024 * 1024 if max_mb > 0 else None
    allowed_ext = _split_csv(os.environ.get("ONEDRIVE_ALLOWED_EXTENSIONS", "")) or None
    root_path = os.environ.get("ONEDRIVE_ROOT_PATH", "")
    identifier_prefix = (os.environ.get("ONEDRIVE_IDENTIFIER_PREFIX") or "").strip()
    enrichment_path_raw = (os.environ.get("ONEDRIVE_SEARCH_ENRICHMENT_PATH") or "").strip()
    enrichment_path = Path(enrichment_path_raw).resolve() if enrichment_path_raw else None
    use_delta = _safe_bool(os.environ.get("ONEDRIVE_MIRROR_USE_DELTA"), default=True)

    client = GraphClient(
        tenant_id=tenant_id, client_id=client_id, client_secret=client_secret
    )
    mirror = OneDriveMirror(
        client=client,
        drive_id=drive_id,
        root_path=root_path,
        local_root=mirror_path,
        allowed_extensions=allowed_ext,
        max_file_size_bytes=max_bytes,
        identifier_prefix=identifier_prefix,
        enrichment_path=enrichment_path,
        use_delta=use_delta,
    )

    _stop_event.clear()
    t = threading.Thread(
        target=_loop,
        args=(mirror, interval),
        name="onedrive-mirror",
        daemon=True,
    )
    t.start()
    _thread = t
    logger.info(
        "OneDrive mirror: started — drive=%s root=%r → %s (every %ss)",
        drive_id[:16] + "...",
        root_path or "/",
        mirror_path,
        interval,
    )
    return t


def stop_mirror_thread(timeout: float = 5.0) -> None:
    """Signal the mirror thread to stop after its current pass and join it."""
    global _thread
    if _thread is None:
        return
    _stop_event.set()
    _thread.join(timeout=timeout)
    _thread = None


def _loop(mirror: OneDriveMirror, interval_seconds: int) -> None:
    while not _stop_event.is_set():
        started = time.monotonic()
        try:
            stats = mirror.run_once()
            logger.info(
                "OneDrive mirror pass: mode=%s items=%d folders=%d downloaded=%d "
                "unchanged=%d skipped_size=%d skipped_ext=%d deleted=%d "
                "enrichment=%d errors=%d duration=%.1fs",
                stats.mode,
                stats.items_seen,
                stats.folders_seen,
                stats.downloaded,
                stats.skipped_unchanged,
                stats.skipped_oversize,
                stats.skipped_extension,
                stats.deleted_locally,
                stats.enrichment_entries,
                len(stats.errors),
                time.monotonic() - started,
            )
        except GraphAuthError as exc:
            logger.error(
                "OneDrive mirror: authentication failed — check tenant/client "
                "secret and that the app has Files.Read.All application "
                "permission with admin consent. (%s)",
                exc,
            )
        except Exception:
            logger.exception("OneDrive mirror: unexpected error in pass")

        _stop_event.wait(timeout=interval_seconds)


def _safe_int(value: Optional[str], default: int) -> int:
    if value is None or not str(value).strip():
        return default
    try:
        return int(str(value).strip())
    except ValueError:
        return default


def _safe_bool(value: Optional[str], *, default: bool) -> bool:
    if value is None or not str(value).strip():
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _resolve_mirror_path(mirror_path_env: str, watch_roots) -> Path:
    """Resolve the local mirror root, never defaulting to a bare watch root.

    When ``ONEDRIVE_MIRROR_PATH`` is set it is honoured verbatim. Otherwise we
    default to a *dedicated* ``onedrive_mirror`` subdirectory of the first watch
    root — never the watch root itself, whose files would be eligible for
    deletion by the mirror's prune step.
    """
    raw = (mirror_path_env or "").strip()
    if raw:
        return Path(raw).resolve()
    roots = list(watch_roots or [])
    if roots:
        return Path(roots[0]).resolve() / "onedrive_mirror"
    return Path("/data/onedrive_mirror").resolve()


def _is_within(parent: Path, child: Path) -> bool:
    try:
        return child.resolve().is_relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
