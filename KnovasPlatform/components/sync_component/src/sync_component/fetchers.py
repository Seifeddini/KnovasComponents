"""How a component reads a source, one function per kind: a new kind of connection is one function and one entry.

A fetcher gets the source's settings from the Platform and its credential from this component's own environment
(``SYNC_CREDENTIAL_<SOURCE KEY>``, or ``SYNC_CREDENTIAL_<KIND>`` for every source of a kind: a credential never travels
through the Platform). It either writes the source's data where the mappings read it (HTTP/JSON), or tells the runner
where to read for itself (a GitHub repository, a mounted folder), and answers with a ``Reading``.

``CHECKS`` holds what a kind can answer without reading everything: is the source reachable, with this credential? A
kind without an entry is checked by fetching it into a temporary folder. A check answers with a code and counts, shown
on the component's page in the Platform; it never names a file, an address or a value (GI-DATA-02/04).

It runs on site, so a company's internal address is allowed here; the Platform's own readers stay public-only.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import quote

import requests

#: Where an HTTP/JSON source's mappings read the fetched document (the builder's compiled mappings name this file).
HTTP_DOCUMENT = "response.json"
MAX_BYTES = int(os.environ.get("SYNC_FETCH_MAX_BYTES") or 50_000_000)
#: Counting the files of a share stops here: a check says whether it can read, not how big the share is.
COUNT_LIMIT = 200_000
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REF = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


class FetchError(RuntimeError):
    """The source could not be read; the message is a code for the run's events."""


@dataclass
class Reading:
    """How the runner reads this source for one plan."""
    args: list[str] = field(default_factory=list)   # added to the runner's plan command
    env: dict[str, str] = field(default_factory=dict)   # for that one command; a credential goes here, never in args
    share_root: Optional[str] = None                # a mounted folder the mappings' globs are relative to
    counts: dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------------ HTTP/JSON

def fetch_http_json(settings: Mapping[str, Any], credential: Optional[str], root: Path, *,
                    get: Callable[..., Any] = requests.get) -> Reading:
    url = str(settings.get("url") or "")
    if not url.startswith("https://") and os.environ.get("SYNC_ALLOW_HTTP") != "true":
        raise FetchError("fetch.not_https")
    headers = {"Accept": "application/json"}
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    try:
        answer = get(url, headers=headers, timeout=120, stream=True, allow_redirects=False)
    except requests.RequestException as exc:
        raise FetchError(f"fetch.{type(exc).__name__.lower()}") from None
    if answer.status_code != 200:
        raise FetchError(f"fetch.http_{answer.status_code}")
    body = b""
    for chunk in answer.iter_content(1 << 16):
        body += chunk
        if len(body) > MAX_BYTES:
            raise FetchError("fetch.too_large")
    try:
        json.loads(body)
    except ValueError:
        raise FetchError("fetch.not_json") from None
    (Path(root) / HTTP_DOCUMENT).write_bytes(body)
    return Reading(counts={"bytes": len(body)})


# ------------------------------------------------------------------ a GitHub repository

def check_github(settings: Mapping[str, Any], credential: Optional[str], *, get: Callable[..., Any] = requests.get) -> dict[str, int]:
    """That the repository and its branch or tag can be read with this credential: one call, no file."""
    repo, ref = str(settings.get("repository") or ""), str(settings.get("ref") or "HEAD")
    if not _REPOSITORY.match(repo) or not _REF.match(ref) or ".." in ref or ref.startswith(("-", "/")) or repo.startswith("-"):
        raise FetchError("fetch.bad_repository")
    base = (os.environ.get("BRAIN_GITHUB_API") or "https://api.github.com").rstrip("/")
    headers = {"Accept": "application/vnd.github.sha", "X-GitHub-Api-Version": "2022-11-28"}
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    try:
        answer = get(f"{base}/repos/{repo}/commits/{quote(ref, safe='/')}", headers=headers, timeout=30, allow_redirects=False)
    except requests.RequestException as exc:
        raise FetchError(f"fetch.{type(exc).__name__.lower()}") from None
    if answer.status_code != 200:
        raise FetchError(f"fetch.http_{answer.status_code}")
    return {}


def fetch_github(settings: Mapping[str, Any], credential: Optional[str], root: Path, *,
                 get: Callable[..., Any] = requests.get) -> Reading:
    """The runner reads the repository itself, through the GitHub API, with its cache beside the plan: a second plan
    fetches only what changed. Checked first, so a wrong token is a clear code and not a failed plan."""
    check_github(settings, credential, get=get)
    return Reading(args=["--source", "github", "--repo", str(settings["repository"]), "--ref", str(settings.get("ref") or "HEAD"),
                         "--cache-dir", str(Path(root) / "source-cache")],
                   env={"BRAIN_GITHUB_TOKEN": credential} if credential else {})


# ------------------------------------------------------------------ a file share

def share_root(environ: Optional[Mapping[str, str]] = None) -> Path:
    """Where the company's share is mounted, read only: ``-v <folder>:/sources:ro``."""
    return Path((environ if environ is not None else os.environ).get("SYNC_FILES_ROOT") or "/sources")


def _share_folder(settings: Mapping[str, Any], environ: Optional[Mapping[str, str]]) -> Path:
    root = share_root(environ)
    folder = str(settings.get("folder") or "").replace("\\", "/").strip("/")
    if ".." in folder.split("/"):
        raise FetchError("fetch.bad_folder")
    return root / folder if folder else root


def check_files(settings: Mapping[str, Any], credential: Optional[str] = None, *,
                environ: Optional[Mapping[str, str]] = None) -> dict[str, int]:
    """That the folder is mounted, readable and not empty. An empty folder is refused, not read: a share that did not
    mount looks exactly like one whose documents were all deleted, and the plan would retire them."""
    folder = _share_folder(settings, environ)
    if not folder.is_dir():
        raise FetchError("fetch.folder_missing")
    if not os.access(folder, os.R_OK | os.X_OK):
        raise FetchError("fetch.folder_unreadable")
    files = 0
    for _, _, names in os.walk(folder):
        files += len(names)
        if files >= COUNT_LIMIT:
            return {"files": COUNT_LIMIT, "capped": 1}
    if files == 0:
        raise FetchError("fetch.folder_empty")
    return {"files": files}


def fetch_files(settings: Mapping[str, Any], credential: Optional[str], root: Path, *,
                environ: Optional[Mapping[str, str]] = None) -> Reading:
    """The runner reads the mounted folder; the mappings' globs are relative to the share's root (builder.mappings)."""
    return Reading(share_root=str(share_root(environ)), counts=check_files(settings, environ=environ))


# ------------------------------------------------------------------ the kinds

FETCHERS: dict[str, Callable[..., Reading]] = {"http_json": fetch_http_json, "github": fetch_github,
                                               "knovas_connector": fetch_files}
CHECKS: dict[str, Callable[..., dict]] = {"github": check_github, "knovas_connector": check_files}


def check(kind: str, settings: Mapping[str, Any], credential: Optional[str], *, fetchers: Optional[Mapping[str, Callable]] = None,
          checks: Optional[Mapping[str, Callable]] = None) -> tuple[str, dict[str, int]]:
    """(code, counts) of one source: ``source.ok`` or the error's code. Nothing is kept."""
    fetchers = FETCHERS if fetchers is None else fetchers
    checks = CHECKS if checks is None else checks
    if kind not in fetchers:
        return "source.kind_unknown", {}
    try:
        if kind in checks:
            return "source.ok", checks[kind](settings, credential)
        with tempfile.TemporaryDirectory() as folder:
            found = fetchers[kind](settings, credential, Path(folder))
        return "source.ok", dict(getattr(found, "counts", {}) or {})
    except FetchError as exc:
        return str(exc), {}


def credential_for(source_key: str, kind: str = "", environ: Mapping[str, str] = os.environ) -> Optional[str]:
    """A source's own credential, else the one for every source of its kind."""
    for name in (source_key, kind):
        if name:
            found = environ.get("SYNC_CREDENTIAL_" + "".join(c if c.isalnum() else "_" for c in name).upper())
            if found:
                return found
    return None
