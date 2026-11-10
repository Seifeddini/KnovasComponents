"""How a component reads a source, one function per kind: a new kind of connection is one function and one entry.

A fetcher gets the source's settings from the Platform and its credential from this component's own environment
(``SYNC_CREDENTIAL_<SOURCE KEY>``, or ``SYNC_CREDENTIAL_<KIND>`` for every source of a kind: a credential never travels
through the Platform). It either writes the source's data where the mappings read it (HTTP/JSON), or tells the runner
where to read for itself (a GitHub repository, a mounted folder), and answers with a ``Reading``.

Which credential goes where (SPEC-write-back-20; verifier KC-3 N5). The Platform names a source's key, kind and, for
HTTP/JSON, the address the credential is sent to, so whoever controls the Platform's configuration could name a key
whose line belongs to something else. ``credential_for`` therefore never hands out:

- a credential the site bound to one host (a ``SYNC_CREDENTIAL_<NAME>_HOST`` line beside it): that is a connector's
  of its own (the Jira connector reads its file, ``knovas-jira.env``, which this component never receives), and this
  component does not use it at all, for any source, kind or address, nor another line instead;
- a host line itself;
- another kind's line: a source keyed like a kind it is not of (an HTTP/JSON source keyed ``github``) does not get
  ``SYNC_CREDENTIAL_GITHUB``, which goes to the GitHub API only.

A GitHub source's credential goes only to the GitHub API (``BRAIN_GITHUB_API``, default api.github.com: the site's own
setting): every address is that base, the repository (``owner/name``) and a ref, both checked so that they cannot name
another host, and no redirect is followed. An HTTP/JSON source's address is its own setting, so its credential (one
without a host line) goes there, as in protocol 1.x.

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

def _settings_ok(repo: str, ref: str) -> bool:
    """owner/name and a branch, tag or commit that stay inside the repository's address (no «.» or «..» part)."""
    return (bool(_REPOSITORY.match(repo)) and bool(_REF.match(ref)) and ".." not in ref and not ref.startswith(("-", "/"))
            and not repo.startswith("-") and not any(part in (".", "..") for part in repo.split("/")))


def check_github(settings: Mapping[str, Any], credential: Optional[str], *, get: Callable[..., Any] = requests.get) -> dict[str, int]:
    """That the repository and its branch or tag can be read with this credential: one call, no file."""
    repo, ref = str(settings.get("repository") or ""), str(settings.get("ref") or "HEAD")
    if not _settings_ok(repo, ref):
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


#: A commit as GitHub names it: 40 hex digits (64 for a SHA-256 repository).
COMMIT = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


@dataclass
class Head:
    """The commit a source names now. ``commit`` is None when the source answered «unchanged» (304) to ``etag``."""
    commit: Optional[str]
    etag: Optional[str] = None


def head_github(settings: Mapping[str, Any], credential: Optional[str], *, etag: Optional[str] = None,
                get: Callable[..., Any] = requests.get) -> Head:
    """The commit the source's branch or tag names now: one request, nothing downloaded. With the ``etag`` of the last
    answer GitHub may answer 304, which its rate limit does not count (diff polling, KG-2)."""
    repo, ref = str(settings.get("repository") or ""), str(settings.get("ref") or "HEAD")
    if not _settings_ok(repo, ref):
        raise FetchError("fetch.bad_repository")
    base = (os.environ.get("BRAIN_GITHUB_API") or "https://api.github.com").rstrip("/")
    headers = {"Accept": "application/vnd.github.sha", "X-GitHub-Api-Version": "2022-11-28"}
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    if etag:
        headers["If-None-Match"] = etag
    try:
        answer = get(f"{base}/repos/{repo}/commits/{quote(ref, safe='/')}", headers=headers, timeout=30, allow_redirects=False)
    except requests.RequestException as exc:
        raise FetchError(f"fetch.{type(exc).__name__.lower()}") from None
    found = (getattr(answer, "headers", None) or {}).get("ETag")
    if answer.status_code == 304 and etag:
        return Head(None, etag)
    if answer.status_code != 200:
        raise FetchError(f"fetch.http_{answer.status_code}")
    commit = (answer.text or "").strip()
    if not COMMIT.match(commit):
        raise FetchError("fetch.not_a_commit")
    return Head(commit, found if isinstance(found, str) and len(found) <= 200 else None)


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
#: The kinds of source this component reads (and answers jobs for): a source of one never takes another's line.
KINDS = frozenset(FETCHERS)
CHECKS: dict[str, Callable[..., dict]] = {"github": check_github, "knovas_connector": check_files}
#: The kinds whose head commit can be asked for: a schedule «bei neuen Commits» polls only these (KG-2).
HEADS: dict[str, Callable[..., Head]] = {"github": head_github}


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


#: The line beside a credential that binds it to one host (``SYNC_CREDENTIAL_<NAME>_HOST``, the Jira connector's rule).
HOST_LINE = "_HOST"


def credential_line(name: str) -> str:
    """The variable a source's credential is read from: ``SYNC_CREDENTIAL_`` and the key or kind in upper case, every
    other character an underscore (the Builder shows the same line, builder/sites.py)."""
    return "SYNC_CREDENTIAL_" + "".join(c if c.isalnum() else "_" for c in str(name)).upper()


def credential_for(source_key: str, kind: str = "", environ: Mapping[str, str] = os.environ) -> Optional[str]:
    """A source's own credential, else the one for every source of its kind; None when there is none, or when the line
    it would take is not this component's to send (the module's docstring). A source keyed like another kind skips
    that kind's line, which is not its own. A line with a host line beside it, or a host line, ends the search: no
    other line is taken instead."""
    own = credential_line(source_key) if source_key else None
    if own is not None and own in {credential_line(k) for k in KINDS if k != kind}:
        own = None                                      # keyed like another kind: that kind's line is not this source's
    for line in (own, credential_line(kind) if kind else None):
        if line is None:
            continue
        if line + HOST_LINE in environ:
            return None                                 # bound to a host for a connector: never sent from here
        if line.endswith(HOST_LINE) and line[:-len(HOST_LINE)] in environ:
            return None                                 # another credential's host line, not a credential
        found = environ.get(line)
        if found:
            return found
    return None
