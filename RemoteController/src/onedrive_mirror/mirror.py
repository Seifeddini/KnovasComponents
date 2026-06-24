"""Mirror a OneDrive drive/folder to a local directory tree.

Idempotent and incremental: a file is downloaded only when its remote
``lastModifiedDateTime`` is newer than the local file's mtime, or when the
local file is missing. Files deleted on OneDrive are removed locally on the
next pass. The on-disk mtime is set to the remote ``lastModifiedDateTime`` so
RC's filesystem fingerprinting sees stable times across mirror runs.
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from onedrive_mirror.graph import GraphClient, GraphRequestError

logger = logging.getLogger(__name__)


DEFAULT_ALLOWED_EXTENSIONS = frozenset(
    {".md", ".txt", ".docx", ".pdf", ".eml", ".msg"}
)


@dataclass
class MirrorStats:
    items_seen: int = 0
    folders_seen: int = 0
    downloaded: int = 0
    skipped_unchanged: int = 0
    skipped_oversize: int = 0
    skipped_extension: int = 0
    deleted_locally: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _safe_name(name: str) -> str:
    """Refuse names that would escape the destination directory."""
    if not name or name in (".", ".."):
        return ""
    if "/" in name or "\\" in name or "\x00" in name:
        return ""
    return name


def _within(parent: Path, child: Path) -> bool:
    try:
        return child.resolve().is_relative_to(parent.resolve())
    except (OSError, ValueError):
        return False


class OneDriveMirror:
    """Recursive mirror of a Graph drive/folder into ``local_root``."""

    def __init__(
        self,
        *,
        client: GraphClient,
        drive_id: str,
        root_path: str,
        local_root: Path,
        allowed_extensions: Optional[Iterable[str]] = None,
        max_file_size_bytes: Optional[int] = None,
    ) -> None:
        if not drive_id:
            raise ValueError("drive_id is required")
        self._client = client
        self._drive_id = drive_id
        self._root_path = (root_path or "").strip().strip("/")
        self._local_root = Path(local_root).resolve()
        self._max_size = max_file_size_bytes if max_file_size_bytes and max_file_size_bytes > 0 else None
        self._allowed_extensions = self._normalise_extensions(allowed_extensions)

    @staticmethod
    def _normalise_extensions(raw: Optional[Iterable[str]]) -> frozenset[str]:
        if not raw:
            return frozenset(DEFAULT_ALLOWED_EXTENSIONS)
        out: set[str] = set()
        for ext in raw:
            if not isinstance(ext, str) or not ext.strip():
                continue
            e = ext.strip().lower()
            if not e.startswith("."):
                e = "." + e.lstrip(".")
            out.add(e)
        return frozenset(out) if out else frozenset(DEFAULT_ALLOWED_EXTENSIONS)

    # ---------------------------------------------------------------- public
    def run_once(self) -> MirrorStats:
        stats = MirrorStats()
        self._local_root.mkdir(parents=True, exist_ok=True)

        # Probe drive once — surface auth/permission errors early with a clear log.
        try:
            self._client.test_drive(self._drive_id)
        except GraphRequestError as exc:
            stats.errors.append(f"drive probe failed: {exc}")
            logger.error("OneDrive mirror: %s", stats.errors[-1])
            return stats

        # Collect remote tree first so we can prune local-only files at the end.
        remote_rel_paths: set[str] = set()
        try:
            self._walk_root(
                rel_dir=Path("."),
                children_iter=self._client.list_root_children(self._drive_id, self._root_path),
                stats=stats,
                remote_rel_paths=remote_rel_paths,
            )
        except GraphRequestError as exc:
            stats.errors.append(f"listing failed: {exc}")
            logger.error("OneDrive mirror: %s", stats.errors[-1])

        self._prune_local_only(remote_rel_paths, stats)
        return stats

    # ---------------------------------------------------------------- walk
    def _walk_root(
        self,
        *,
        rel_dir: Path,
        children_iter,
        stats: MirrorStats,
        remote_rel_paths: set[str],
    ) -> None:
        for item in children_iter:
            stats.items_seen += 1
            name = _safe_name(item.get("name") or "")
            if not name:
                continue
            rel = (rel_dir / name) if str(rel_dir) != "." else Path(name)

            if "folder" in item:
                stats.folders_seen += 1
                child_dir = self._local_root / rel
                if not _within(self._local_root, child_dir):
                    logger.warning("OneDrive mirror: refusing path escape via %r", str(rel))
                    continue
                child_dir.mkdir(parents=True, exist_ok=True)
                remote_rel_paths.add(str(rel))
                try:
                    next_iter = self._client.list_children_by_id(self._drive_id, item["id"])
                except GraphRequestError as exc:
                    stats.errors.append(f"list {rel}: {exc}")
                    logger.error("OneDrive mirror: %s", stats.errors[-1])
                    continue
                self._walk_root(
                    rel_dir=rel,
                    children_iter=next_iter,
                    stats=stats,
                    remote_rel_paths=remote_rel_paths,
                )
                continue

            if "file" not in item:
                continue  # shortcut / package / unknown — skip

            ext = Path(name).suffix.lower()
            if ext not in self._allowed_extensions:
                stats.skipped_extension += 1
                continue
            size = int(item.get("size") or 0)
            if self._max_size is not None and size > self._max_size:
                stats.skipped_oversize += 1
                logger.info("OneDrive mirror: skipping oversize file %s (%d bytes)", rel, size)
                continue

            dest = self._local_root / rel
            if not _within(self._local_root, dest):
                logger.warning("OneDrive mirror: refusing path escape via %r", str(rel))
                continue
            remote_rel_paths.add(str(rel))
            dest.parent.mkdir(parents=True, exist_ok=True)

            remote_mtime = _parse_iso(item.get("lastModifiedDateTime") or "")
            if self._is_local_current(dest, remote_mtime, size):
                stats.skipped_unchanged += 1
                continue

            try:
                bytes_written = self._client.download_to(
                    self._drive_id, item["id"], dest
                )
                if remote_mtime is not None:
                    ts = remote_mtime.timestamp()
                    os.utime(dest, (ts, ts))
                stats.downloaded += 1
                logger.info(
                    "OneDrive mirror: wrote %s (%d bytes)", rel, bytes_written
                )
            except (GraphRequestError, OSError) as exc:
                stats.errors.append(f"download {rel}: {exc}")
                logger.error("OneDrive mirror: %s", stats.errors[-1])

    @staticmethod
    def _is_local_current(
        dest: Path, remote_mtime: Optional[datetime], remote_size: int
    ) -> bool:
        if not dest.exists():
            return False
        try:
            st = dest.stat()
        except OSError:
            return False
        if remote_size and st.st_size != remote_size:
            return False
        if remote_mtime is None:
            return True
        local_mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        # Treat as current if local is at or newer than remote, ignoring sub-second drift.
        return local_mtime >= remote_mtime.replace(microsecond=0)

    # ---------------------------------------------------------------- prune
    def _prune_local_only(self, remote_rel_paths: set[str], stats: MirrorStats) -> None:
        if not self._local_root.exists():
            return
        # Collect local files (not directories) under root.
        for path in sorted(self._local_root.rglob("*"), reverse=True):
            try:
                rel_str = str(path.relative_to(self._local_root))
            except ValueError:
                continue
            if rel_str in remote_rel_paths:
                continue
            if path.is_file() or path.is_symlink():
                try:
                    path.unlink()
                    stats.deleted_locally += 1
                    logger.info("OneDrive mirror: deleted local-only file %s", rel_str)
                except OSError as exc:
                    stats.errors.append(f"unlink {rel_str}: {exc}")
            elif path.is_dir():
                try:
                    # Only remove empty directories during prune
                    path.rmdir()
                except OSError:
                    # Directory still contains kept files — fine
                    pass
