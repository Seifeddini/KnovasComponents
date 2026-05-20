"""Bounded filesystem discovery under RC_WATCH_ROOTS."""
from __future__ import annotations

import fnmatch
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import get_config

logger = logging.getLogger(__name__)

ENTRY_CAP = 10_000
DEFAULT_MAX_DEPTH = 3
ABSOLUTE_MAX_DEPTH = 10


def _allowed_roots() -> list[Path]:
    return [Path(r).resolve() for r in get_config().rc_watch_roots]


def resolve_root(root_param: Optional[str]) -> tuple[Optional[Path], Optional[str]]:
    """Return (resolved_root, error_message)."""
    roots = _allowed_roots()
    if not roots:
        return None, "No watch roots configured"

    if root_param:
        candidate = Path(root_param)
        if not candidate.is_absolute():
            candidate = roots[0] / candidate
        resolved = candidate.resolve()
    else:
        resolved = roots[0]

    for allowed in roots:
        try:
            if resolved.is_relative_to(allowed):
                return resolved, None
        except ValueError:
            continue
    return None, "Root path is outside allowed watch roots"


def _is_under_root(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except ValueError:
        return False


def _matches_globs(rel_posix: str, include: list[str], exclude: list[str]) -> bool:
    path = Path(rel_posix)
    if include:
        if not any(path.match(g) or fnmatch.fnmatch(rel_posix, g) for g in include):
            return False
    for g in exclude:
        if path.match(g) or fnmatch.fnmatch(rel_posix, g):
            return False
    return True


def discover_filesystem(
    root_param: Optional[str] = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    include_globs: Optional[list[str]] = None,
    exclude_globs: Optional[list[str]] = None,
) -> dict[str, Any]:
    root, err = resolve_root(root_param)
    if err or root is None:
        raise PermissionError(err or "Invalid root")

    max_depth = min(max(1, max_depth), ABSOLUTE_MAX_DEPTH)
    include = include_globs or ["**/*", "*"]
    exclude = exclude_globs or []

    entries: list[dict[str, Any]] = []
    truncated = False
    scanned_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def walk_dir(directory: Path, depth: int) -> None:
        nonlocal truncated
        if truncated:
            return
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            return

        for child in children:
            if truncated:
                return
            resolved = child.resolve()
            if not _is_under_root(resolved, root):
                logger.warning("Skipping path outside watch root: %s", child.name)
                continue

            rel = resolved.relative_to(root)
            rel_posix = rel.as_posix()
            depth_rel = len(rel.parts)

            entry_type = "directory" if resolved.is_dir() else "file"
            meta: dict[str, Any] = {
                "path": rel_posix,
                "name": resolved.name,
                "type": entry_type,
            }
            if resolved.is_file():
                try:
                    stat = resolved.stat()
                    meta["size_bytes"] = stat.st_size
                    meta["modified_at"] = (
                        datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z")
                    )
                except OSError:
                    pass

            if _matches_globs(rel_posix, include, exclude) or entry_type == "directory":
                if entry_type == "file" and _matches_globs(rel_posix, include, exclude):
                    entries.append(meta)
                    if len(entries) >= ENTRY_CAP:
                        truncated = True
                        return
                elif entry_type == "directory" and depth_rel <= max_depth:
                    entries.append(meta)
                    if len(entries) >= ENTRY_CAP:
                        truncated = True
                        return

            if resolved.is_dir() and depth_rel < max_depth:
                walk_dir(resolved, depth + 1)

    walk_dir(root, 0)

    return {
        "status": "success",
        "scanned_at": scanned_at,
        "root": str(root),
        "truncated": truncated,
        "entries": entries,
    }
