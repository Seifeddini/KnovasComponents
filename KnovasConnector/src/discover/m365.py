"""``/discover`` for a Microsoft 365 folder: the same answer a share would give.

The Platform's folder picker and its profile preview call ``/discover`` with
paths under the watch root. With ``M365_FOLDER_URL`` set, the watch root
stands for the OneDrive/SharePoint folder, so this lists its subfolders and
files from the change-tracked inventory -- no download, no filesystem.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import get_config
from discover.filesystem import ABSOLUTE_MAX_DEPTH, DEFAULT_MAX_DEPTH, ENTRY_CAP, _matches_globs
from m365.source import BROWSE_MAX_AGE_SECONDS, M365Source, watch_root_subpath
from sync.document_text import DEFAULT_INCLUDE_GLOBS, is_syncable_extension


def _relative(full_rel: str, sub: str) -> Optional[str]:
    if not sub:
        return full_rel
    if full_rel.startswith(sub + "/"):
        return full_rel[len(sub) + 1:]
    return None


def _valid_iso(value: str) -> Optional[str]:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return value


def discover_m365(
    source: M365Source,
    root_param: Optional[str] = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    include_globs: Optional[list[str]] = None,
    exclude_globs: Optional[list[str]] = None,
) -> dict[str, Any]:
    roots = get_config().rc_watch_roots
    watch_root = (roots[0] if roots else "/mnt/documents").rstrip("/") or "/"
    sub = watch_root_subpath(root_param) if root_param else ""
    if sub is None:
        raise PermissionError("Root path is outside allowed watch roots")

    max_depth = min(max(1, max_depth), ABSOLUTE_MAX_DEPTH)
    include = include_globs or list(DEFAULT_INCLUDE_GLOBS)
    exclude = exclude_globs or []

    source.refresh(max_age_seconds=BROWSE_MAX_AGE_SECONDS)
    folders = source.folders()
    files = source.files()

    entries: list[dict[str, Any]] = []
    truncated = False
    if not sub or sub in folders:
        for full_rel in sorted(folders):
            rel = _relative(full_rel, sub)
            if not rel or len(rel.split("/")) > max_depth:
                continue
            entry: dict[str, Any] = {"path": rel, "name": folders[full_rel].name, "type": "directory"}
            modified = _valid_iso(folders[full_rel].modified_iso)
            if modified:
                entry["modified_at"] = modified
            entries.append(entry)
            if len(entries) >= ENTRY_CAP:
                truncated = True
                break
        if not truncated:
            for full_rel in sorted(files):
                rel = _relative(full_rel, sub)
                if not rel or len(rel.split("/")) > max_depth:
                    continue
                remote = files[full_rel]
                if not is_syncable_extension(Path(remote.name).suffix):
                    continue
                if not _matches_globs(rel, include, exclude):
                    continue
                entry = {"path": rel, "name": remote.name, "type": "file", "size_bytes": remote.size}
                modified = _valid_iso(remote.modified_iso)
                if modified:
                    entry["modified_at"] = modified
                entries.append(entry)
                if len(entries) >= ENTRY_CAP:
                    truncated = True
                    break

    return {
        "status": "success",
        "scanned_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "root": watch_root if not sub else f"{watch_root}/{sub}",
        "truncated": truncated,
        "entries": entries,
    }
