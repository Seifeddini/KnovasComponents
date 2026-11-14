"""The custom views folder: which views exist, each read safely into one snapshot per scan.

Every file is read without following links, checked on the open descriptor and read up to its limit plus one
byte. The log names files and fingerprints, never file content.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
import threading
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, List, Mapping, Optional, Tuple

from custom_views.manifest import Manifest, ManifestError, parse_manifest
from custom_views.settings import CustomViewsSettings

logger = logging.getLogger(__name__)

MANIFEST_FILE = "view.json"

# Hidden entries (".…") never match either pattern.
_VIEW_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_FILE_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,79}")

_FOLDER = "Ordner für eigene Ansichten"
_WINDOWS = os.name == "nt"
# On Windows os.access reports every folder as writable, so the check would refuse every folder there.
_CAN_CHECK_WRITE = not _WINDOWS
# O_NOFOLLOW and O_NONBLOCK do not exist on Windows; there _redirected() stands in for O_NOFOLLOW.
_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
# On POSIX the file is opened relative to its folder's descriptor, so a folder swapped for a link in between
# is not followed either.
_DIR_FD = os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW")
_CHUNK = 64 * 1024


@dataclass(frozen=True)
class ViewFile:
    name: str
    data: bytes
    sha256: str
    mtime: float


@dataclass(frozen=True)
class View:
    """A valid view: its manifest and every file it names (plus ``view.json``), all read in one scan."""

    manifest: Manifest
    files: Mapping[str, ViewFile]


@dataclass(frozen=True)
class Invalid:
    id: str
    reason: str


def _size(limit: int) -> str:
    if limit % (1024 * 1024) == 0:
        return f"{limit // (1024 * 1024)} MB"
    if limit % 1024 == 0:
        return f"{limit // 1024} KB"
    return f"{limit} Bytes"


def _redirected(path: str) -> bool:
    """Windows: the path passes a junction or link somewhere (no O_NOFOLLOW there). POSIX relies on lstat/O_NOFOLLOW."""
    if not _WINDOWS:
        return False
    return os.path.normcase(os.path.realpath(path)) != os.path.normcase(os.path.abspath(path))


def _within(path: str, parent: str) -> bool:
    try:
        return os.path.commonpath([parent, path]) == parent
    except ValueError:
        return False


def _confined(folder: str, name: str) -> Optional[str]:
    """One confined candidate path, or None if it escapes ``folder`` (the logic of app._autodoc_candidate)."""
    if os.path.isabs(name):
        return None
    base_abs = os.path.abspath(folder)
    candidate_abs = os.path.abspath(os.path.join(base_abs, name))
    if not _within(candidate_abs, base_abs):
        return None
    base_real = os.path.realpath(base_abs)
    if not _within(os.path.realpath(candidate_abs), base_real):
        return None
    return candidate_abs


def folder_refusal(path: str, documents_root: Optional[str]) -> Optional[str]:
    """Why the views folder must not be used, or None when it may (on Windows its writability is not checked)."""
    if not path:
        return f"{_FOLDER} fehlt."
    path = os.path.abspath(path)  # also drops a trailing separator, which would make lstat follow a link
    try:
        st = os.lstat(path)
    except OSError:
        return f"{_FOLDER} fehlt."
    if stat.S_ISLNK(st.st_mode) or _redirected(path):
        return f"{_FOLDER} ist ein Verweis."
    if not stat.S_ISDIR(st.st_mode):
        return f"{_FOLDER} ist kein Ordner."
    if documents_root:
        folder = os.path.normcase(os.path.realpath(path))
        documents = os.path.normcase(os.path.realpath(documents_root))
        if _within(folder, documents):
            return f"{_FOLDER} liegt im Dokumentenordner."
        if _within(documents, folder):
            return f"{_FOLDER} enthält den Dokumentenordner."
    if _CAN_CHECK_WRITE and os.access(path, os.W_OK):
        return f"{_FOLDER} ist für die Plattform beschreibbar."
    return None


def _open(folder: str, name: str) -> int:
    if not _DIR_FD:
        return os.open(os.path.join(folder, name), _OPEN_FLAGS)
    dir_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return os.open(name, _OPEN_FLAGS, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)


def _read_at_most(fd: int, count: int) -> bytes:
    chunks = []
    while count > 0:
        chunk = os.read(fd, min(count, _CHUNK))
        if not chunk:
            break
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


def read_view_file(folder: str, name: str, limit: int) -> ViewFile:
    """Read the regular file ``name`` directly in ``folder``; ManifestError for a link, anything else, or > limit."""
    if not _FILE_NAME.fullmatch(name):
        raise ManifestError("Ein Dateiname ist ungültig.")  # not echoed: it is not a plain name
    folder = os.path.abspath(folder)
    try:
        folder_st = os.lstat(folder)
    except OSError:
        raise ManifestError("Ordner der Ansicht fehlt.") from None
    if stat.S_ISLNK(folder_st.st_mode) or _redirected(folder):
        raise ManifestError("Ordner der Ansicht ist ein Verweis.")
    if not stat.S_ISDIR(folder_st.st_mode):
        raise ManifestError("Ordner der Ansicht fehlt.")

    path = os.path.join(folder, name)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise ManifestError(f"Datei „{name}“ fehlt.") from None
    except OSError:
        raise ManifestError(f"Datei „{name}“ ist nicht lesbar.") from None
    if stat.S_ISLNK(st.st_mode) or _redirected(path):
        raise ManifestError(f"Datei „{name}“ ist ein Verweis.")
    if not stat.S_ISREG(st.st_mode):
        raise ManifestError(f"Datei „{name}“ ist keine gewöhnliche Datei.")
    if _confined(folder, name) is None:
        raise ManifestError(f"Datei „{name}“ liegt nicht direkt im Ordner der Ansicht.")

    try:
        fd = _open(folder, name)
    except OSError:
        raise ManifestError(f"Datei „{name}“ ist nicht lesbar.") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ManifestError(f"Datei „{name}“ ist keine gewöhnliche Datei.")
        data = _read_at_most(fd, limit + 1)
    except OSError:
        raise ManifestError(f"Datei „{name}“ ist nicht lesbar.") from None
    finally:
        os.close(fd)
    if len(data) > limit:
        raise ManifestError(f"Datei „{name}“ ist grösser als {_size(limit)}.")
    return ViewFile(name=name, data=data, sha256=hashlib.sha256(data).hexdigest(), mtime=st.st_mtime)


def _view_order(view: View):
    order = view.manifest.order
    return (order is None, order if order is not None else 0.0, view.manifest.id)


@dataclass(frozen=True)
class _Scan:
    refusal: Optional[str] = None
    views: Tuple[View, ...] = ()
    by_id: Mapping[str, View] = field(default_factory=lambda: MappingProxyType({}))
    invalid: Tuple[Invalid, ...] = ()


class Registry:
    """The views of one folder, shared by request threads; rescanned at most every ``rescan_seconds``."""

    def __init__(self, settings: CustomViewsSettings, *, documents_root: Optional[str] = None,
                 clock: Callable[[], float] = time.monotonic, log: logging.Logger = logger):
        self._settings = settings
        self._documents_root = documents_root
        self._clock = clock
        self._log = log
        self._lock = threading.Lock()
        self._state: Optional[Tuple[_Scan, float]] = None  # (last complete scan, when it started)
        self._said_write_unchecked = False

    def views(self) -> List[View]:
        return list(self._current().views)

    def invalid(self) -> List[Invalid]:
        return list(self._current().invalid)

    def get(self, view_id: str) -> Optional[View]:
        return self._current().by_id.get(view_id)

    def for_roles(self, roles: frozenset) -> List[View]:
        return [view for view in self._current().views if view.manifest.allows(roles)]

    def refusal(self) -> Optional[str]:
        return self._current().refusal

    def _fresh(self, state: Optional[Tuple[_Scan, float]]) -> bool:
        return state is not None and self._clock() - state[1] < self._settings.rescan_seconds

    def _current(self) -> _Scan:
        state = self._state
        if self._fresh(state):
            return state[0]
        # One scan at a time; while it runs, other threads keep the last complete scan.
        if not self._lock.acquire(blocking=state is None):
            return state[0]
        try:
            state = self._state
            if self._fresh(state):
                return state[0]
            started = self._clock()
            scan = self._scan()
            self._log_changes(state[0] if state else None, scan)
            self._state = (scan, started)
            return scan
        finally:
            self._lock.release()

    def _scan(self) -> _Scan:
        settings = self._settings
        refusal = folder_refusal(settings.dir, self._documents_root)
        if refusal is not None:
            return _Scan(refusal=refusal)
        if not _CAN_CHECK_WRITE and not self._said_write_unchecked:
            self._said_write_unchecked = True  # under the scan lock
            self._log.warning("custom_views: the views folder's write protection cannot be checked on Windows; "
                              "make sure the Platform cannot write it")
        try:
            with os.scandir(settings.dir) as entries:
                candidates = sorted(
                    e.name for e in entries
                    if _VIEW_ID.fullmatch(e.name) and (e.is_dir(follow_symlinks=False) or e.is_symlink())
                )
        except OSError:
            return _Scan(refusal=f"{_FOLDER} ist nicht lesbar.")

        views: List[View] = []
        invalid: List[Invalid] = []
        for position, view_id in enumerate(candidates):
            if len(views) >= settings.max_views:
                self._log.warning("custom_views: more than %d views; skipped %d folder(s) from %s on",
                                  settings.max_views, len(candidates) - position, view_id)
                break
            try:
                views.append(self._load(view_id))
            except ManifestError as exc:
                invalid.append(Invalid(view_id, str(exc)))
            except Exception:  # one broken view never hides the others
                self._log.exception("custom_views: %s could not be read", view_id)
                invalid.append(Invalid(view_id, "Die Ansicht konnte nicht gelesen werden."))
        views.sort(key=_view_order)
        return _Scan(
            views=tuple(views),
            by_id=MappingProxyType({view.manifest.id: view for view in views}),
            invalid=tuple(invalid),
        )

    def _load(self, view_id: str) -> View:
        settings = self._settings
        folder = os.path.join(settings.dir, view_id)
        manifest_file = read_view_file(folder, MANIFEST_FILE, settings.file_max_bytes)
        manifest = parse_manifest(view_id, manifest_file.data)
        files = {MANIFEST_FILE: manifest_file}
        total = len(manifest_file.data)
        for name in (manifest.entry, *manifest.styles, *manifest.scripts):
            if name in files:
                continue
            file = read_view_file(folder, name, settings.file_max_bytes)
            total += len(file.data)
            if total > settings.view_max_bytes:
                raise ManifestError(f"Die Ansicht ist grösser als {_size(settings.view_max_bytes)}.")
            files[name] = file
        view = View(manifest=manifest, files=MappingProxyType(files))
        # Checks on a loaded view's content go here; each raises ManifestError, which makes the view invalid.
        from custom_views.document import check_view  # imported here so the hook stays in this one place

        check_view(view)  # every text file UTF-8, the entry markup safe
        return view

    def _log_changes(self, old: Optional[_Scan], new: _Scan) -> None:
        old = old or _Scan()
        if new.refusal is not None and new.refusal != old.refusal:
            self._log.warning("custom_views: folder refused: %s", new.refusal)
        for view in new.views:
            before = old.by_id.get(view.manifest.id)
            for name, file in view.files.items():
                previous = before.files.get(name) if before else None
                if previous is None or previous.sha256 != file.sha256:
                    self._log.info("custom_views: %s/%s changed sha256=%s", view.manifest.id, name, file.sha256[:12])
            for name in sorted(before.files.keys() - view.files.keys()) if before else ():
                self._log.info("custom_views: %s/%s removed", view.manifest.id, name)
        for view_id in sorted(old.by_id.keys() - new.by_id.keys()):
            self._log.info("custom_views: %s removed", view_id)
        old_reasons = {item.id: item.reason for item in old.invalid}
        for item in new.invalid:
            if old_reasons.get(item.id) != item.reason:
                self._log.warning("custom_views: %s invalid: %s", item.id, item.reason)
