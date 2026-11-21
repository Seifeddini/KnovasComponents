"""fileshare: list the folders below each root and read file sizes and dates. No file is ever opened.

Works on a mounted path (Linux, macOS) and on a Windows UNC path (\\\\server\\share$\\folder). One result per root,
key `<share key>:<root>`. Names of files and folders go to the detail only.
"""

from __future__ import annotations

import datetime as _dt
import heapq
import os
import re
import time
from collections import Counter
from pathlib import PurePosixPath

from ..util import ProbeError, error_code, result
from . import Context, Output

# What the Knovas Connector reads (KnovasConnector/README.md; contracts/sync_request.schema.json include_globs).
CONNECTOR_READABLE = (".md", ".txt", ".docx", ".pdf", ".eml", ".msg")
# The WinJur example configuration of the Connector uploads 4 documents per minute.
UPLOADS_PER_MINUTE = 4
LONG_PATH = 240
LARGEST = 20

_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
AUTODOC_STEM = re.compile(r"^\{?" + _GUID + r"\}?_[^_]+_.+$")
RANGE_FOLDER = re.compile(r"^\d{6}-\d{6}$")
SCANNER_NAME = re.compile(r"^(scan|scn|img)[_-]?\d", re.IGNORECASE)
SCANNER_EXT = (".pdf", ".tif", ".tiff", ".jpg")
DICTATION_EXT = (".dss", ".ds2", ".dvf")
OUTLOOK_EXT = (".pst", ".ost")
AGE_BANDS = ("<1y", "1-5y", "5-10y", ">10y")

FACTS = {
    "files": "files the account can see below the root",
    "bytes": "their total size in bytes",
    "folders": "folders below the root, including those that refuse the account",
    "max_depth": "deepest folder level below the root (a folder directly in the root is level 1)",
    "extensions": "files per extension, lower case with the dot; \"\" for files without one",
    "connector_readable_files": "files with an extension the Knovas Connector reads (.md .txt .docx .pdf .eml .msg)",
    "connector_readable_share": "connector_readable_files / files (null when there are no files)",
    "age.newest_days": "days since the newest file was changed (by modification time, at scan time)",
    "age.oldest_days": "days since the oldest file was changed",
    "age.bands": "files per age band by modification time: <1y, 1-5y, 5-10y, >10y",
    "refused_folders": "folders the account may not list (permission denied), the root included; their paths are in the detail",
    "patterns.autodoc_names_share": "share of files whose name stem is <GUID>_<something>_<something> (AutoDoc); null when there are no files",
    "patterns.range_folders": "folders named as a number range: six digits, a dash, six digits",
    "patterns.office_lock_files": "files whose name starts with ~$ (Office lock files)",
    "patterns.outlook_archives": "Outlook archive files (.pst, .ost)",
    "patterns.dictation_files": "dictation files (.dss, .ds2, .dvf)",
    "patterns.scanner_files": "scanner output: name starts with scan/scn/img and a number; .pdf .tif .tiff .jpg",
    "patterns.no_extension_files": "files without an extension",
    "patterns.long_paths": "files whose path below the share is longer than 240 characters",
    "patterns.empty_files": "files of size 0",
    "largest_file_bytes": "size of the largest file in bytes",
    "listing.seconds": "seconds the listing took",
    "listing.entries_per_second": "files and folders listed per second (null when nothing could be listed)",
    "first_read_forecast.files_to_upload": "files the Connector would upload on its first read (connector_readable_files)",
    "first_read_forecast.hours_at_4_per_minute": "hours the first read takes at 4 uploads per minute",
}


# --------------------------------------------------------------------------- name patterns (unit-tested)
def extension(name: str) -> str:
    """Lower-case extension with the dot; "" when the name has none."""
    return os.path.splitext(name)[1].lower()


def is_autodoc_name(name: str) -> bool:
    return bool(AUTODOC_STEM.match(os.path.splitext(name)[0]))


def is_range_folder(name: str) -> bool:
    return bool(RANGE_FOLDER.match(name))


def is_lock_file(name: str) -> bool:
    return name.startswith("~$")


def is_outlook_archive(name: str) -> bool:
    return extension(name) in OUTLOOK_EXT


def is_dictation(name: str) -> bool:
    return extension(name) in DICTATION_EXT


def is_scanner_file(name: str) -> bool:
    return bool(SCANNER_NAME.match(name)) and extension(name) in SCANNER_EXT


def is_connector_readable(name: str) -> bool:
    return extension(name) in CONNECTOR_READABLE


def age_band(days: int) -> str:
    if days < 365:
        return "<1y"
    if days < 5 * 365:
        return "1-5y"
    if days < 10 * 365:
        return "5-10y"
    return ">10y"


# --------------------------------------------------------------------------- the walk
def _join(base: str, name: str) -> str:
    return os.path.join(base, name)


def _rel(share_rel_root: str, rel: str) -> str:
    return str(PurePosixPath(share_rel_root, rel)) if rel else share_rel_root


def scan_root(share_path: str, root: str, now: _dt.datetime, clock=time.monotonic) -> tuple[dict, dict, str, str | None]:
    """Walk one root. Returns (facts, detail, status, error)."""
    root_norm = root.replace("\\", "/").strip("/")
    start = os.path.join(share_path, *root_norm.split("/")) if root_norm else share_path
    if not os.path.isdir(start):
        if os.path.exists(start):
            raise ProbeError("root_not_a_folder")
        raise ProbeError("root_not_found")

    now_ts = now.timestamp()
    t0 = clock()
    files = folders = total_bytes = max_depth = 0
    readable = autodoc = ranges = locks = outlook = dictation = scanner = noext = long_paths = empty = 0
    largest = 0
    newest = oldest = None
    bands = Counter({b: 0 for b in AGE_BANDS})
    extensions: Counter[str] = Counter()
    top_counts: Counter[str] = Counter()
    refused: list[str] = []
    other_errors: list[str] = []
    links: list[str] = []
    biggest: list[tuple[int, str]] = []  # min-heap of (size, path)

    stack: list[tuple[str, str, int]] = [(start, "", 0)]  # (absolute path, path below the root, depth)
    first = True
    while stack:
        abs_dir, rel_dir, depth = stack.pop()
        try:
            with os.scandir(abs_dir) as it:
                entries = list(it)
        except PermissionError:
            # A folder that refuses the account is a finding, not a failure; the root itself included.
            first = False
            refused.append(_rel(root_norm, rel_dir))
            continue
        except OSError as exc:
            if first:
                raise ProbeError("root_unreadable:" + error_code(exc)) from None
            other_errors.append(_rel(root_norm, rel_dir))
            continue
        first = False
        for entry in entries:
            name = entry.name
            rel = f"{rel_dir}/{name}" if rel_dir else name
            try:
                # Links are not followed: a junction (Windows) or symlink can point back up and loop.
                if getattr(entry, "is_junction", lambda: False)() or entry.is_symlink():
                    links.append(_rel(root_norm, rel))
                    continue
                if entry.is_dir(follow_symlinks=False):
                    folders += 1
                    max_depth = max(max_depth, depth + 1)
                    if is_range_folder(name):
                        ranges += 1
                    stack.append((entry.path, rel, depth + 1))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                other_errors.append(_rel(root_norm, rel))
                continue
            files += 1
            size = st.st_size
            total_bytes += size
            ext = extension(name)
            extensions[ext] += 1
            top_counts[rel.split("/", 1)[0] if "/" in rel else "(files in the root)"] += 1
            if ext in CONNECTOR_READABLE:
                readable += 1
            if is_autodoc_name(name):
                autodoc += 1
            if is_lock_file(name):
                locks += 1
            if ext in OUTLOOK_EXT:
                outlook += 1
            if ext in DICTATION_EXT:
                dictation += 1
            if is_scanner_file(name):
                scanner += 1
            if ext == "":
                noext += 1
            if size == 0:
                empty += 1
            if len(_rel(root_norm, rel)) > LONG_PATH:
                long_paths += 1
            largest = max(largest, size)
            if len(biggest) < LARGEST:
                heapq.heappush(biggest, (size, rel))
            elif (size, rel) > biggest[0]:
                heapq.heapreplace(biggest, (size, rel))
            days = max(0, int((now_ts - st.st_mtime) // 86400))
            newest = days if newest is None else min(newest, days)
            oldest = days if oldest is None else max(oldest, days)
            bands[age_band(days)] += 1

    seconds = max(clock() - t0, 1e-6)
    # A share of nothing, or a speed of nothing, is unknown (null), not 0: no check fires on a root that refused.
    readable_share = round(readable / files, 4) if files else None
    speed = round((files + folders) / seconds, 1) if files + folders else None
    facts = {
        "files": files,
        "bytes": total_bytes,
        "folders": folders,
        "max_depth": max_depth,
        "extensions": dict(sorted(extensions.items())),
        "connector_readable_files": readable,
        "connector_readable_share": readable_share,
        "age": {"newest_days": newest, "oldest_days": oldest, "bands": {b: bands[b] for b in AGE_BANDS}},
        "refused_folders": len(refused),
        "patterns": {
            "autodoc_names_share": round(autodoc / files, 4) if files else None,
            "range_folders": ranges,
            "office_lock_files": locks,
            "outlook_archives": outlook,
            "dictation_files": dictation,
            "scanner_files": scanner,
            "no_extension_files": noext,
            "long_paths": long_paths,
            "empty_files": empty,
        },
        "largest_file_bytes": largest,
        "listing": {"seconds": round(seconds, 1), "entries_per_second": speed},
        "first_read_forecast": {
            "files_to_upload": readable,
            "hours_at_4_per_minute": round(readable / UPLOADS_PER_MINUTE / 60, 1),
        },
    }
    detail = {
        "root": root_norm,
        "refused_folders": sorted(refused),
        "unreadable_entries": sorted(other_errors),
        "links_not_followed": sorted(links),
        "top_folders": dict(sorted(top_counts.items())),
        "largest_files": [{"path": _rel(root_norm, p), "bytes": s} for s, p in sorted(biggest, key=lambda x: (-x[0], x[1]))],
    }
    status, error = "ok", None
    if other_errors:
        status, error = "partial", f"listing_errors:{len(other_errors)}"
    return facts, detail, status, error


def run(target: dict, ctx: Context) -> list[Output]:
    share_key = target["key"]
    path = target.get("path")
    roots = [str(r) for r in (target.get("roots") or [])] or [""]
    outputs = []
    for root in roots:
        root_norm = root.replace("\\", "/").strip("/")
        key = f"{share_key}:{root_norm}" if root_norm else share_key
        if not path:
            outputs.append(Output(result(key, "failed", "no_path_in_intake"), {}))
            continue
        try:
            facts, detail, status, error = scan_root(str(path), root_norm, ctx.now)
            detail["path"] = str(path)
            ctx.log(f"fileshare {key}: {status}, {facts['files']:,} files, {facts['folders']:,} folders, "
                    f"{facts['refused_folders']} refused, {facts['listing']['seconds']} s")
            outputs.append(Output(result(key, status, error, facts), detail))
        except Exception as exc:  # a failing root never stops the scan
            ctx.log(f"fileshare {key}: failed ({error_code(exc)})")
            outputs.append(Output(result(key, "failed", error_code(exc)),
                                  {"root": root_norm, "message": ctx.secrets.scrub(f"{type(exc).__name__}: {exc}")}))
    return outputs
