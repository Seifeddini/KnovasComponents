"""
Map resolved AutoDoc filesystem paths to client-facing UNC paths for companion open.
"""

from __future__ import annotations

import os
import re
from typing import Optional, Tuple


def normalize_unc_root(unc_root: str) -> str:
    """Normalize UNC root to use backslashes, no trailing slash; ensure leading \\\\server."""
    s = (unc_root or "").strip()
    if not s:
        return ""
    s = s.replace("/", "\\")
    if s.startswith("\\\\"):
        rest = s[2:]
        rest = re.sub(r"\\+", r"\\", rest)
        s = "\\\\" + rest
    else:
        s = re.sub(r"\\+", r"\\", s)
        if not s.startswith("\\"):
            s = "\\\\" + s
        else:
            s = "\\" + s
    while s.endswith("\\") and len(s) > 2:
        s = s[:-1]
    return s


def normalize_local_root(local_root: str) -> str:
    """Absolute local path with OS-specific separators, no trailing slash."""
    s = (local_root or "").strip()
    if not s:
        return ""
    s = os.path.abspath(os.path.normpath(s))
    if s.endswith(os.sep):
        s = s.rstrip(os.sep)
    return s


def filesystem_path_to_unc(
    filesystem_abs: str,
    local_root: str,
    unc_root: str,
) -> Optional[str]:
    """
    If filesystem_abs is under local_root, return UNC = unc_root + relative remainder.

    Returns None if not under root or roots misconfigured.
    """
    unc_r = normalize_unc_root(unc_root)
    loc_r = normalize_local_root(local_root)
    if not unc_r or not loc_r:
        return None

    try:
        fs_abs = os.path.abspath(os.path.normpath(filesystem_abs))
    except (OSError, ValueError):
        return None

    try:
        common = os.path.commonpath([loc_r, fs_abs])
    except ValueError:
        return None

    if common != loc_r:
        return None

    rel = os.path.relpath(fs_abs, loc_r)
    if rel.startswith(".."):
        return None

    rel_unc = rel.replace(os.sep, "\\")
    if rel_unc in (".", ""):
        return unc_r
    return unc_r + "\\" + rel_unc


def parse_unc_roots_list(value) -> list[Tuple[str, str]]:
    """YAML may provide open.unc_roots as list of {local, unc} dicts."""
    out: list[Tuple[str, str]] = []
    if not value:
        return out
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                loc = str(item.get("local") or item.get("local_root") or "").strip()
                unc = str(item.get("unc") or item.get("unc_root") or "").strip()
                if loc and unc:
                    out.append((os.path.abspath(os.path.normpath(loc)), normalize_unc_root(unc)))
    return out


def map_path_with_roots(
    filesystem_abs: str,
    roots: list[Tuple[str, str]],
) -> Optional[str]:
    """Try each (local_root, unc_root) pair; first match wins."""
    for loc, unc in roots:
        u = filesystem_path_to_unc(filesystem_abs, loc, unc)
        if u:
            return u
    return None
