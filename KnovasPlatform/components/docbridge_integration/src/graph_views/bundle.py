"""Each tenant's definitions bundle (KnowledgeBase KB-7), from ``views.config_source``.

One JSON file holds everything the views read of a company's configuration: its types, relations,
descriptions, processes, spines, pages, edit map, owners, profiles and health
(KnowledgeBase ``docs/ModernDocs/client-zero/generated/README.md``). The computed reports
(``header.reports``) are separate files, read from the same source relative to the bundle's folder.

The source has two forms:

* ``file:<path>`` — a local file (development and tests);
* ``github:<owner>/<repo>@<ref>:<path to bundle.json>`` — read through the GitHub client of the change
  requests (``github_repo.py``) with its token;
* ``sync`` — the bundle of this tenant's last successful "Sync von GitHub" (``brain_sync``, KC-0.2), kept in
  the Platform's storage with its evaluation reports.

A bundle is refused, with the reason on the page, when its ``format`` is not
``brain-definitions-bundle``, its ``format_version`` is one this reader does not know, or its
``content_hash`` is not the hash of its sections. The tenant never comes from the bundle (DEC-0039).

A request works with one bundle: ``ViewService.context`` sets it (``use``) and the readers
(definitions, descriptions, keys, edit map, completeness, documentation pages) take it from
``current()``.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import posixpath
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

FORMAT = "brain-definitions-bundle"
FORMAT_VERSIONS = (1,)
SECTIONS = ("types", "relations", "descriptions", "processes", "spines", "pages", "edit_map", "owners",
            "profiles", "health")
#: The empty form of a section a company does not have (KB-7 README, "A company without some parts").
EMPTY = {"processes": {"version": 1, "rules": [], "report_rules": [], "processes": []},
         "spines": {"version": 1, "spines": []}, "pages": {"version": 1, "pages": []},
         "edit_map": {"version": 1, "items": []}}

_GITHUB = re.compile(r"^github:(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@(?P<ref>[^:\s]+):(?P<path>\S+)$")


class BundleError(Exception):
    """The bundle is not configured, not readable or refused; the message is for the page."""


def content_hash(sections: Dict[str, Any]) -> str:
    """``sha256:`` + the hex SHA-256 of the canonical JSON of ``sections`` (KB-7)."""
    canon = json.dumps(sections, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(canon).hexdigest()


# -- sources ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Source:
    spec: str
    kind: str                      # "file" | "github"
    path: str
    repo: str = ""
    ref: str = ""

    def report_path(self, rel: str) -> str:
        """Where a report named in ``header.reports`` lives: relative to the bundle's folder."""
        if self.kind == "file":
            p = Path(rel)
            return str(p if p.is_absolute() else Path(self.path).parent / p)
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(self.path), rel))
        if joined.startswith("../") or joined == ".." or posixpath.isabs(rel):
            raise BundleError(f"Bericht {rel!r} liegt ausserhalb des Repositorys {self.repo}.")
        return joined


def parse_source(spec: str) -> Source:
    spec = str(spec or "").strip()
    if not spec:
        raise BundleError("Kein Definitionsbündel konfiguriert (views.config_source).")
    if spec.startswith("file:") and spec[len("file:"):].strip():
        return Source(spec, "file", spec[len("file:"):].strip())
    if spec in ("sync", "sync:"):
        return Source(spec, "sync", "")
    m = _GITHUB.match(spec)
    if m:
        return Source(spec, "github", m.group("path").lstrip("/"), repo=m.group("repo"), ref=m.group("ref"))
    raise BundleError(f"views.config_source {spec!r} ist ungültig: erwartet file:<Pfad>, "
                      "github:<owner>/<repo>@<ref>:<Pfad zu bundle.json> oder sync.")


def _read_text(source: Source, path: str, github: Optional[Callable[[str], Any]]) -> Optional[str]:
    """A file's text from the source, or None when it does not exist."""
    if source.kind == "file":
        try:
            return Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise BundleError(f"{path} nicht lesbar ({type(exc).__name__}).") from None
    from graph_views.github_repo import GitHubError

    repo = github(source.repo) if github else None
    if repo is None:
        raise BundleError(f"Kein Zugang zu GitHub für {source.repo} konfiguriert.")
    try:
        return repo.get_raw(path, source.ref)
    except GitHubError as exc:
        hint = " Token gesetzt (KB_GITHUB_TOKEN / views.change_requests.github_token_file)?" if exc.status in (401, 403) else ""
        raise BundleError(f"{source.repo}@{source.ref}:{path} nicht lesbar: {exc}.{hint}") from None


# -- the bundle -----------------------------------------------------------------------------------

class Bundle:
    """One verified bundle and the reports it names."""

    def __init__(self, header: Dict[str, Any], sections: Dict[str, Any], source: str,
                 reports: Optional[Dict[str, Dict[str, Any]]] = None, report_errors: Optional[List[str]] = None):
        self.header = header
        self.sections = sections
        self.source = source
        self.reports = reports or {}
        self.report_errors = report_errors or []
        self._memo: Dict[str, Any] = {}
        self._lock = threading.Lock()

    @property
    def label(self) -> str:
        return str(self.header.get("label") or "")

    @property
    def content_hash(self) -> str:
        return str(self.header.get("content_hash") or "")

    @property
    def source_commit(self) -> Optional[str]:
        return self.header.get("source_commit")

    def section(self, name: str) -> Any:
        """A section as the bundle holds it; a missing one is its empty form (KB-7, reader rule 3)."""
        value = self.sections.get(name)
        if value is None:
            return EMPTY.get(name, {})
        return value

    @property
    def types(self) -> Dict[str, Any]:
        return self.section("types")

    @property
    def relations(self) -> Dict[str, Any]:
        return self.section("relations")

    @property
    def descriptions(self) -> Dict[str, Any]:
        return self.section("descriptions")

    @property
    def processes(self) -> Dict[str, Any]:
        return self.section("processes")

    @property
    def spines(self) -> Dict[str, Any]:
        return self.section("spines")

    @property
    def pages(self) -> Dict[str, Any]:
        return self.section("pages")

    @property
    def edit_map(self) -> Dict[str, Any]:
        return self.section("edit_map")

    @property
    def owners(self) -> Dict[str, Any]:
        return self.section("owners")

    @property
    def profiles(self) -> Dict[str, Any]:
        return self.section("profiles")

    @property
    def health(self) -> Dict[str, Any]:
        return self.section("health")

    @property
    def report_paths(self) -> Dict[str, str]:
        return dict(self.header.get("reports") or {})

    def report(self, name: str) -> Dict[str, Any]:
        """A computed report by its name in ``header.reports``; empty when the bundle names none."""
        return self.reports.get(name) or {}

    def memo(self, name: str, build: Callable[[], Any]) -> Any:
        """A value derived from the sections, built once per bundle (the sections never change)."""
        with self._lock:
            if name not in self._memo:
                self._memo[name] = build()
            return self._memo[name]

    def pin(self) -> Dict[str, Any]:
        """What the page names as the definitions' origin."""
        return {"source": self.source, "label": self.label, "content_hash": self.content_hash,
                "source_commit": self.source_commit, "format_version": self.header.get("format_version")}

    def with_reports(self, reports: Dict[str, Dict[str, Any]], errors: List[str]) -> "Bundle":
        """The same sections (and what was derived from them) with freshly read reports."""
        fresh = Bundle(self.header, self.sections, self.source, reports, errors)
        fresh._memo, fresh._lock = self._memo, self._lock
        return fresh


def check(doc: Any, where: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(header, sections) of a bundle, or BundleError naming what is wrong (KB-7, reader rules 1 and 2)."""
    if not isinstance(doc, dict) or not isinstance(doc.get("header"), dict) or not isinstance(doc.get("sections"), dict):
        raise BundleError(f"{where}: kein Definitionsbündel (header und sections fehlen).")
    header, sections = doc["header"], doc["sections"]
    if header.get("format") != FORMAT:
        raise BundleError(f"{where}: Format {header.get('format')!r} ist kein {FORMAT}.")
    if header.get("format_version") not in FORMAT_VERSIONS:
        known = ", ".join(str(v) for v in FORMAT_VERSIONS)
        raise BundleError(f"{where}: Formatversion {header.get('format_version')!r} wird nicht unterstützt "
                          f"(diese Plattform liest Version {known}).")
    actual = content_hash(sections)
    if header.get("content_hash") != actual:
        raise BundleError(f"{where}: Prüfsumme stimmt nicht (Kopf {header.get('content_hash')!r}, Inhalt {actual}). "
                          "Die Datei ist beschädigt oder von Hand geändert; neu erzeugen mit python -m brain_runner bundle.")
    return header, sections


def _where(source: Source) -> str:
    return f"{source.repo}@{source.ref}:{source.path}" if source.kind == "github" else source.path


def _read_reports(source: Source, header: Dict[str, Any],
                  github: Optional[Callable[[str], Any]]) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """The reports the header names. A missing or broken report is empty and listed; the bundle still serves."""
    reports, errors = {}, []
    for name, rel in sorted((header.get("reports") or {}).items()):
        try:
            path = source.report_path(str(rel))
            text = _read_text(source, path, github)
            if text is None:
                errors.append(f"Bericht {name} fehlt ({rel}).")
                continue
            data = json.loads(text)
            reports[name] = data if isinstance(data, dict) else {}
        except (BundleError, ValueError) as exc:
            errors.append(f"Bericht {name} nicht lesbar: {exc}")
    for e in errors:
        logger.warning("views bundle: %s", e)
    return reports, errors


def read(spec: str, github: Optional[Callable[[str], Any]] = None, *,
         known: Optional[Callable[[str], Optional[Bundle]]] = None,
         sync_path: Optional[Callable[[], Optional[str]]] = None) -> Bundle:
    """Read, check and return the bundle at ``spec`` with its reports.

    ``known`` maps a content hash to a bundle already read: then that bundle's sections and derived
    values are reused and only the reports are read again. ``sync_path`` names the file of this tenant's
    last successful sync (source ``sync``).
    """
    source = parse_source(spec)
    if source.kind == "sync":
        path = sync_path() if sync_path is not None else None
        if not path:
            raise BundleError("Noch kein erfolgreicher Sync von GitHub: die Quelle sync hat kein Definitionsbündel.")
        source = Source(spec, "file", str(path))
    text = _read_text(source, source.path, github)
    if text is None:
        raise BundleError(f"Definitionsbündel {_where(source)} nicht gefunden.")
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise BundleError(f"{_where(source)}: kein gültiges JSON ({exc}).") from None
    header, sections = check(doc, _where(source))
    reports, errors = _read_reports(source, header, github)
    seen = known(header["content_hash"]) if known else None
    if seen is not None and seen.source == spec:
        return seen.with_reports(reports, errors)
    return Bundle(header, sections, spec, reports, errors)


class BundleStore:
    """Bundles per (tenant, source), read again after ``ttl_seconds`` and kept by content hash.

    A refused or unreadable bundle is remembered for a short while too, so a broken source does not
    cost a read per request; ``refresh`` reads again at once.
    """

    ERROR_TTL_SECONDS = 30

    def __init__(self, ttl_seconds: float, *, github: Optional[Callable[[str], Any]] = None,
                 now: Optional[Callable[[], float]] = None,
                 sync_path: Optional[Callable[[str], Optional[str]]] = None):
        self._ttl = ttl_seconds
        self._github = github
        self._sync_path = sync_path          # tenant -> its last successful sync's bundle file, or None
        self._now = now or time.time
        self._items: Dict[Tuple[str, str], Tuple[float, Any]] = {}
        self._by_hash: Dict[str, Bundle] = {}
        self._lock = threading.Lock()

    def get(self, tenant: str, spec: str, *, refresh: bool = False) -> Bundle:
        key = (str(tenant or ""), str(spec or "").strip())
        now = self._now()
        with self._lock:
            hit = self._items.get(key)
        if hit is not None and not refresh:
            at, value = hit
            if now - at < (self.ERROR_TTL_SECONDS if isinstance(value, BundleError) else self._ttl):
                if isinstance(value, BundleError):
                    raise value
                return value
        try:
            sync_path = (lambda: self._sync_path(key[0])) if self._sync_path is not None else None
            bundle = read(key[1], self._github, known=self._by_hash.get, sync_path=sync_path)
        except BundleError as exc:
            with self._lock:
                self._items[key] = (now, exc)
            raise
        with self._lock:
            self._items[key] = (now, bundle)
            self._by_hash[bundle.content_hash] = bundle
        return bundle

    def forget(self, tenant: str) -> None:
        """Read the tenant's bundles again on next use (a sync replaced its ``sync`` bundle)."""
        with self._lock:
            for key in [k for k in self._items if k[0] == str(tenant or "")]:
                self._items.pop(key, None)


# -- the bundle of this request -------------------------------------------------------------------

_current: "contextvars.ContextVar[Optional[Bundle]]" = contextvars.ContextVar("views_bundle", default=None)


def use(bundle: Optional[Bundle]) -> contextvars.Token:
    return _current.set(bundle)


def reset(token: contextvars.Token) -> None:
    _current.reset(token)


def current() -> Bundle:
    bundle = _current.get()
    if bundle is None:
        raise BundleError("Kein Definitionsbündel geladen (views.config_source).")
    return bundle


def load_file(path: Any) -> Bundle:
    """A bundle from a local file (tests, scripts)."""
    return read(f"file:{path}")
