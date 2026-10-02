"""Folder paths on the RemoteController host -> Knovas pointer prefixes.

Why this exists
---------------
A document-field folder rule (``PUT /secured/graph/doc-field-rules``) is
keyed by a *pointer prefix* and matched with a raw ``startswith``
(KnowledgeBase doc_fields/rules.py:93). The administrator thinks in folders
on the file server; Knovas only ever sees pointers. RemoteController builds
every pointer the same way::

    identifier_prefix + "/" + <path relative to the source folder, with "/">

(RemoteController src/sync/sync_executor.py ``_pointer_for_relative``,
src/sync/knovas_uploader.py ``upload_file``). So the prefix of a sub-folder
is that same expression over the folder's relative path, and it must end in
``/``: without it, ``kanzlei/Mandate/2024`` would also match
``kanzlei/Mandate/2024-alt/``.

The source folder itself is not part of the pointer. Two sources with the
same sub-folder share one prefix; the console asks for a confirmation in
that case (spec 4.6) rather than pretending a rule could tell them apart.

Pure functions, no I/O. A path is never logged here (spec D6).
"""
from __future__ import annotations

import posixpath
import re
from typing import Iterable, Optional, Tuple

#: The server's limit for ``pointer_prefix`` (rules.py MAX_PREFIX_CHARS).
MAX_PREFIX_CHARS = 2000

_DRIVE_RE = re.compile(r"^[A-Za-z]:/")


class FolderOutsideSources(ValueError):
    """The folder lies in none of the ingestion profile's source folders."""


def _norm(path: object) -> str:
    """Forward slashes, ``.`` and ``..`` resolved, no trailing slash.

    A UNC root (``\\\\server\\share``) keeps its two leading slashes; that is
    what ``posixpath.normpath`` does with exactly two, and it is what tells
    ``//server/share`` from ``/server/share``.

    Whitespace is kept: a folder name may end in a space, and RemoteController
    keeps it in the pointer (``relative_to(...).as_posix()``). Only an
    all-blank path is no path.
    """
    text = str(path or "").replace("\\", "/")
    if not text.strip():
        return ""
    normed = posixpath.normpath(text)
    return "" if normed == "." else normed.rstrip("/") or "/"


def _is_windows(path: str) -> bool:
    return bool(_DRIVE_RE.match(path)) or path.startswith("//")


def relative_folder(source_path: object, folder_path: object) -> Optional[str]:
    """``folder_path`` relative to ``source_path`` ("" for the source root),
    or None when the folder is not inside the source.

    Windows paths (a drive letter or UNC) are compared without case, as the
    file system does; the relative part keeps the folder's own spelling,
    because RemoteController takes it from the walk.
    """
    source, folder = _norm(source_path), _norm(folder_path)
    if not source or not folder:
        return None
    fold = _is_windows(source) or _is_windows(folder)
    s_cmp, f_cmp = (source.casefold(), folder.casefold()) if fold else (source, folder)
    if f_cmp == s_cmp:
        return ""
    head = s_cmp if s_cmp.endswith("/") else s_cmp + "/"
    if not f_cmp.startswith(head):
        return None
    rel = folder[len(head):].strip("/")
    if not rel or any(part in ("", ".", "..") for part in rel.split("/")):
        return None
    return rel


def rc_pointer_prefix(identifier_prefix: object, source_path: object,
                      folder_path: object) -> str:
    """The pointer prefix RemoteController gives documents under ``folder_path``.

    ``identifier_prefix.strip() + "/" + <relative folder> + "/"``; the source
    root gives ``identifier_prefix + "/"``. Raises FolderOutsideSources when
    the folder is not inside ``source_path``, and ValueError for an empty
    identifier prefix (there would be no pointer to match).
    """
    prefix = str(identifier_prefix or "").strip()
    if not prefix:
        raise ValueError("the ingestion profile has no identifier prefix")
    rel = relative_folder(source_path, folder_path)
    if rel is None:
        raise FolderOutsideSources("the folder is outside the source folder")
    out = f"{prefix}/{rel}/" if rel else f"{prefix}/"
    if len(out) > MAX_PREFIX_CHARS:
        raise ValueError("the folder path is too long for a rule")
    return out


def prefix_for_folder(identifier_prefix: object, source_paths: Iterable[object],
                      folder_path: object) -> Tuple[str, int]:
    """``(prefix, matching_sources)`` for a folder picked from the RC tree.

    When several sources contain the folder (nested sources), the most
    specific one -- the longest source path -- decides the relative part.
    Raises FolderOutsideSources when no source contains it.
    """
    matches = []
    for source in source_paths or ():
        if relative_folder(source, folder_path) is not None:
            matches.append(_norm(source))
    if not matches:
        raise FolderOutsideSources("the folder is outside every source folder")
    best = max(matches, key=len)
    return rc_pointer_prefix(identifier_prefix, best, folder_path), len(matches)


def normalize_pointer_prefix(text: object) -> str:
    """A pointer prefix typed by hand, validated like a computed one.

    Backslashes become ``/``, surrounding blanks go, and the trailing ``/``
    is added when missing (the server matches with a raw ``startswith``).
    Raises ValueError for an empty prefix, a bare ``/``, a NUL, a ``.`` or
    ``..`` segment, or more than 2000 characters.
    """
    raw = str(text or "")
    if "\x00" in raw:
        raise ValueError("the folder prefix contains a NUL character")
    prefix = raw.strip().replace("\\", "/")
    if not prefix.strip("/"):
        raise ValueError("the folder prefix is empty")
    if not prefix.endswith("/"):
        prefix += "/"
    if any(part in (".", "..") for part in prefix.split("/")):
        raise ValueError("the folder prefix contains . or ..")
    if len(prefix) > MAX_PREFIX_CHARS:
        raise ValueError("the folder prefix is too long")
    return prefix


def prefix_depth(prefix: object) -> int:
    """Number of path segments of a prefix; what the audit records instead
    of the prefix itself (spec 4.6)."""
    return len([part for part in str(prefix or "").split("/") if part])
