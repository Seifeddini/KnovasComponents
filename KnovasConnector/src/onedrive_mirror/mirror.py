"""Mirror a OneDrive drive/folder to a local directory tree.

Idempotent and incremental: a file is downloaded only when its remote
``lastModifiedDateTime`` is newer than the local file's mtime, or when the
local file is missing. Files deleted on OneDrive are removed locally on the
next pass. The on-disk mtime is set to the remote ``lastModifiedDateTime`` so
RC's filesystem fingerprinting sees stable times across mirror runs.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from onedrive_mirror.graph import DeltaTokenInvalid, GraphClient, GraphRequestError

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
    skipped_control: int = 0
    deleted_locally: int = 0
    enrichment_entries: int = 0
    download_failures: int = 0
    mode: str = "walk"  # "delta", "walk", or "delta-then-walk"
    errors: list[str] = field(default_factory=list)


DELTA_TOKEN_FILENAME = ".onedrive_delta.json"


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
        identifier_prefix: str = "",
        enrichment_path: Optional[Path] = None,
        use_delta: bool = True,
    ) -> None:
        if not drive_id:
            raise ValueError("drive_id is required")
        self._client = client
        self._drive_id = drive_id
        self._root_path = (root_path or "").strip().strip("/")
        self._local_root = Path(local_root).resolve()
        self._max_size = max_file_size_bytes if max_file_size_bytes and max_file_size_bytes > 0 else None
        self._allowed_extensions = self._normalise_extensions(allowed_extensions)
        self._identifier_prefix = (identifier_prefix or "").strip().strip("/")
        self._enrichment_path = Path(enrichment_path).resolve() if enrichment_path else None
        self._use_delta = bool(use_delta)
        # Set True whenever a listing fails part-way through a full walk so the
        # prune step is skipped (a partial remote view must never drive mass
        # local deletion).
        self._enumeration_incomplete = False
        # Path enrichment rows are aggregated across passes when running in
        # delta mode, since each delta only describes the diff. The full
        # writer reconstructs the snapshot from this dict on every pass.
        self._enrichment_state: dict[str, dict] = {}

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

        if self._use_delta:
            try:
                self._run_delta(stats)
                # Always rewrite the snapshot when enrichment is enabled — even
                # when the state ends up empty after deletions, the file must
                # be overwritten so docbridge-web does not keep serving stale
                # open-links.
                if self._enrichment_path is not None:
                    self._write_enrichment(list(self._enrichment_state.values()), stats)
                return stats
            except DeltaTokenInvalid as exc:
                logger.warning(
                    "OneDrive mirror: delta token rejected, falling back to full walk (%s)",
                    exc,
                )
                self._discard_delta_token()
                stats.mode = "delta-then-walk"
            except GraphRequestError as exc:
                logger.warning(
                    "OneDrive mirror: delta path failed, falling back to full walk (%s)",
                    exc,
                )
                stats.mode = "delta-then-walk"

        # Full-walk fallback (also used when ONEDRIVE_MIRROR_USE_DELTA=false).
        remote_rel_paths: set[str] = set()
        enrichment_rows: list[dict] = []
        self._enumeration_incomplete = False
        try:
            self._walk_root(
                rel_dir=Path("."),
                children_iter=self._client.list_root_children(self._drive_id, self._root_path),
                stats=stats,
                remote_rel_paths=remote_rel_paths,
                enrichment_rows=enrichment_rows,
            )
        except GraphRequestError as exc:
            self._enumeration_incomplete = True
            stats.errors.append(f"listing failed: {exc}")
            logger.error("OneDrive mirror: %s", stats.errors[-1])

        # Only prune when we have a COMPLETE remote view. A partial enumeration
        # (top-level or any sub-folder listing failure) would otherwise delete
        # every local file that simply was not seen yet — mass data loss.
        if self._enumeration_incomplete:
            logger.warning(
                "OneDrive mirror: skipping prune — remote enumeration incomplete "
                "(%d error(s)); not deleting local files this pass.",
                len(stats.errors),
            )
        else:
            self._prune_local_only(remote_rel_paths, stats)

        # Rebuild full enrichment snapshot from this pass.
        if self._enrichment_path is not None and enrichment_rows:
            self._enrichment_state = {row["doc_id"]: row for row in enrichment_rows}
            self._write_enrichment(enrichment_rows, stats)

        return stats

    # ------------------------------------------------------------------ delta
    def _delta_token_path(self) -> Path:
        return self._local_root / DELTA_TOKEN_FILENAME

    def _load_delta_token(self) -> Optional[str]:
        path = self._delta_token_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("OneDrive mirror: cannot read delta token (%s) — restarting", exc)
            return None
        link = data.get("delta_link")
        if isinstance(link, str) and link:
            # Also rehydrate the enrichment snapshot so files unchanged since
            # last pass retain their open-link mapping.
            snap = data.get("enrichment") or {}
            if isinstance(snap, dict):
                self._enrichment_state = {str(k): v for k, v in snap.items() if isinstance(v, dict)}
            return link
        return None

    def _save_delta_token(self, link: str) -> None:
        path = self._delta_token_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "delta_link": link,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "enrichment": self._enrichment_state,
        }
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _discard_delta_token(self) -> None:
        try:
            self._delta_token_path().unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("OneDrive mirror: could not delete stale delta token (%s)", exc)

    def _is_control_dest(self, dest: Path) -> bool:
        """True if ``dest`` collides with one of the module's own control files.

        A mirrored remote item must never be written over the delta-token file
        or the enrichment snapshot — otherwise a maliciously named OneDrive item
        could corrupt/poison the mirror's own state.
        """
        try:
            resolved = dest.resolve()
        except (OSError, ValueError):
            return False
        control_paths = [self._delta_token_path().resolve()]
        if self._enrichment_path is not None:
            control_paths.append(self._enrichment_path.resolve())
        return resolved in control_paths

    def _rel_path_for_delta_item(self, item: dict) -> Optional[str]:
        """Reconstruct the path within the mirrored subtree, or None if outside."""
        name = (item.get("name") or "").strip()
        # Reject empty, separator-bearing, and "." / ".." names — a ".." name
        # (or a component that reduces to it) would resolve back to / above the
        # mirror root and let a delete wipe the whole tree.
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            return None
        parent = item.get("parentReference") or {}
        parent_path = parent.get("path") or ""
        # Strip the "/drive/root:" prefix.
        if ":" in parent_path:
            after = parent_path.split(":", 1)[1].strip("/")
        else:
            after = ""

        rel: Optional[str]
        if self._root_path:
            if after == self._root_path:
                rel = name
            else:
                prefix = self._root_path + "/"
                if after.startswith(prefix):
                    sub = after[len(prefix):]
                    rel = f"{sub}/{name}" if sub else name
                elif name == self._root_path and after == "":
                    # The root folder of our subtree — not a file we mirror, but valid.
                    return ""
                else:
                    # Item lives outside the mirrored subtree.
                    return None
        else:
            rel = f"{after}/{name}" if after else name

        # Defence in depth: reject any path that traverses via empty / "." / ".."
        # components (e.g. a parentReference of "<root>/foo" with name "..").
        if rel:
            parts = rel.replace("\\", "/").split("/")
            if any(p in ("", ".", "..") for p in parts):
                return None
        return rel

    def _run_delta(self, stats: MirrorStats) -> None:
        """Process Graph delta pages. Raises DeltaTokenInvalid/GraphRequestError on failure."""
        saved_link = self._load_delta_token()
        is_initial = saved_link is None
        stats.mode = "delta-initial" if is_initial else "delta"

        new_delta_link: Optional[str] = None
        for items, delta_link in self._client.delta_pages(self._drive_id, saved_link):
            for item in items:
                self._process_delta_item(item, stats)
            if delta_link:
                new_delta_link = delta_link

        if new_delta_link and stats.download_failures:
            # At least one item could not be downloaded this pass (throttled /
            # transient error). Persisting the new cursor would skip those items
            # forever, so keep the last good cursor and let the next pass retry.
            logger.warning(
                "OneDrive mirror: %d download failure(s) this pass — NOT advancing "
                "delta cursor so failed items are reprocessed next pass.",
                stats.download_failures,
            )
        elif new_delta_link:
            try:
                self._save_delta_token(new_delta_link)
            except OSError as exc:
                # Persist failure is non-fatal — next pass just redoes a full delta.
                stats.errors.append(f"save delta token: {exc}")
                logger.error("OneDrive mirror: %s", stats.errors[-1])

    def _process_delta_item(self, item: dict, stats: MirrorStats) -> None:
        stats.items_seen += 1
        rel = self._rel_path_for_delta_item(item)
        if rel is None:
            return  # outside mirrored subtree

        is_deleted = "deleted" in item
        is_folder = "folder" in item
        is_file = "file" in item

        if is_deleted:
            self._apply_delta_delete(rel, stats)
            return

        if is_folder:
            stats.folders_seen += 1
            if rel == "":
                # The mirrored subtree's own root — nothing to do.
                return
            dest = self._local_root / rel
            if not _within(self._local_root, dest):
                logger.warning("OneDrive mirror: refusing path escape via %r", rel)
                return
            dest.mkdir(parents=True, exist_ok=True)
            return

        if not is_file:
            return  # shortcut / package / unknown

        if rel == "":
            return  # not a file we expected — be defensive

        name = item.get("name") or ""
        ext = Path(name).suffix.lower()
        if ext not in self._allowed_extensions:
            stats.skipped_extension += 1
            return
        size = int(item.get("size") or 0)
        if self._max_size is not None and size > self._max_size:
            stats.skipped_oversize += 1
            return

        dest = self._local_root / rel
        if not _within(self._local_root, dest):
            logger.warning("OneDrive mirror: refusing path escape via %r", rel)
            return
        if self._is_control_dest(dest):
            stats.skipped_control += 1
            logger.warning(
                "OneDrive mirror: refusing to overwrite control file via item %r", rel
            )
            return
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Maintain enrichment snapshot for this file (added / modified).
        web_url = (item.get("webUrl") or "").strip()
        if self._enrichment_path is not None and web_url:
            doc_id = self._doc_id_for(rel)
            self._enrichment_state[doc_id] = {
                "doc_id": doc_id,
                "web_url": web_url,
                "title": name,
                "modified_at": (item.get("lastModifiedDateTime") or "").strip() or None,
            }

        remote_mtime = _parse_iso(item.get("lastModifiedDateTime") or "")
        if self._is_local_current(dest, remote_mtime, size):
            stats.skipped_unchanged += 1
            return

        try:
            bytes_written = self._client.download_to(self._drive_id, item["id"], dest)
            if remote_mtime is not None:
                ts = remote_mtime.timestamp()
                os.utime(dest, (ts, ts))
            stats.downloaded += 1
            logger.info("OneDrive mirror: wrote %s (%d bytes)", rel, bytes_written)
        except (GraphRequestError, OSError) as exc:
            stats.download_failures += 1
            stats.errors.append(f"download {rel}: {exc}")
            logger.error("OneDrive mirror: %s", stats.errors[-1])

    def _apply_delta_delete(self, rel: str, stats: MirrorStats) -> None:
        if not rel:
            return
        dest = self._local_root / rel
        if not _within(self._local_root, dest):
            return
        # Never delete the mirror root itself, even if a crafted rel resolves to
        # it — that would rmtree the entire mirror.
        try:
            if dest.resolve() == self._local_root.resolve():
                logger.warning(
                    "OneDrive mirror: refusing to delete the mirror root via rel %r", rel
                )
                return
        except (OSError, ValueError):
            return
        try:
            if dest.is_file() or dest.is_symlink():
                dest.unlink()
                stats.deleted_locally += 1
            elif dest.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
        except OSError as exc:
            stats.errors.append(f"delete {rel}: {exc}")
            logger.error("OneDrive mirror: %s", stats.errors[-1])

        # Drop any enrichment entry that points at this path.
        doc_id = self._doc_id_for(rel)
        self._enrichment_state.pop(doc_id, None)

    # -------------------------------------------------------------- enrichment
    def _doc_id_for(self, rel_path: str) -> str:
        rel = rel_path.replace("\\", "/").strip("/")
        if self._identifier_prefix:
            return f"{self._identifier_prefix}/{rel}"
        return rel

    def _write_enrichment(self, rows: list[dict], stats: MirrorStats) -> None:
        """Atomically write JSONL — KnovasPlatform's docbridge-web reads this."""
        path = self._enrichment_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            os.replace(tmp, path)
            try:
                os.chmod(path, 0o644)
            except OSError:
                pass
            stats.enrichment_entries = len(rows)
            logger.info(
                "OneDrive mirror: wrote %d enrichment entries to %s",
                len(rows),
                path,
            )
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            stats.errors.append(f"enrichment write: {exc}")
            logger.error("OneDrive mirror: %s", stats.errors[-1])

    # ---------------------------------------------------------------- walk
    def _walk_root(
        self,
        *,
        rel_dir: Path,
        children_iter,
        stats: MirrorStats,
        remote_rel_paths: set[str],
        enrichment_rows: list[dict],
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
                    self._enumeration_incomplete = True
                    stats.errors.append(f"list {rel}: {exc}")
                    logger.error("OneDrive mirror: %s", stats.errors[-1])
                    continue
                self._walk_root(
                    rel_dir=rel,
                    children_iter=next_iter,
                    stats=stats,
                    remote_rel_paths=remote_rel_paths,
                    enrichment_rows=enrichment_rows,
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
            if self._is_control_dest(dest):
                stats.skipped_control += 1
                logger.warning(
                    "OneDrive mirror: refusing to overwrite control file via item %r",
                    str(rel),
                )
                continue
            remote_rel_paths.add(str(rel))
            dest.parent.mkdir(parents=True, exist_ok=True)

            # Capture enrichment metadata regardless of whether we (re)downloaded
            web_url = (item.get("webUrl") or "").strip()
            if self._enrichment_path is not None and web_url:
                last_modified = (item.get("lastModifiedDateTime") or "").strip()
                rel_posix = str(rel).replace("\\", "/")
                enrichment_rows.append(
                    {
                        "doc_id": self._doc_id_for(rel_posix),
                        "web_url": web_url,
                        "title": name,
                        "modified_at": last_modified or None,
                    }
                )

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
                stats.download_failures += 1
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
