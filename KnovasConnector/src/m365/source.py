"""The Microsoft 365 document source: one folder in OneDrive or SharePoint.

Configured by four settings and nothing else::

    M365_FOLDER_URL      the folder's address, as the browser shows it
    M365_CLIENT_ID       the Entra app that reads it
    M365_CLIENT_SECRET   (application permission Sites.Read.All)
    M365_TENANT_ID       optional; derived from the address when omitted

When ``M365_FOLDER_URL`` is set the folder takes the place of the watch root:
``/mnt/documents`` *is* the configured folder, ``/mnt/documents/Akten`` its
subfolder ``Akten``. Sync bodies, the Platform's folder picker, per-folder
access groups and identifiers therefore work exactly as for a file share,
without a single file being kept on this server.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Optional

import requests

from config import get_config
from m365.inventory import (
    DriveInventory,
    FolderMissingError,
    InventoryError,
    RemoteFile,
    RemoteFolder,
)
from m365.location import FolderLocation, LocationError, default_tenant_for_host, parse_folder_url
from m365.resolver import GRAPH, ResolveError, ResolvedFolder, resolve_folder
from onedrive_mirror.graph import GraphAuthError, GraphClient, GraphRequestError

logger = logging.getLogger(__name__)

#: How stale the inventory may be for a read that is not a sync cycle
#: (folder picker, status page). A sync cycle always asks for changes.
BROWSE_MAX_AGE_SECONDS = 60


class M365Error(RuntimeError):
    """The Microsoft 365 source is configured but cannot be used right now."""


@dataclass(frozen=True)
class M365Settings:
    folder_url: str
    tenant_id: str
    client_id: str
    client_secret: str
    state_dir: Path
    links_path: Path

    @classmethod
    def from_env(cls) -> Optional["M365Settings"]:
        folder_url = (os.environ.get("M365_FOLDER_URL") or "").strip()
        if not folder_url:
            return None
        state_dir = Path(
            (os.environ.get("M365_STATE_DIR") or "").strip()
            or Path(get_config().rc_sync_state_path).resolve().parent / "m365"
        )
        links = (os.environ.get("M365_LINKS_PATH") or "").strip()
        return cls(
            folder_url=folder_url,
            tenant_id=(os.environ.get("M365_TENANT_ID") or "").strip(),
            client_id=(os.environ.get("M365_CLIENT_ID") or "").strip(),
            client_secret=(os.environ.get("M365_CLIENT_SECRET") or "").strip(),
            state_dir=state_dir,
            links_path=Path(links) if links else state_dir / "links.jsonl",
        )

    def problems(self) -> list[str]:
        """What stops this configuration from working, in words an operator can act on."""
        out: list[str] = []
        location: Optional[FolderLocation] = None
        try:
            location = parse_folder_url(self.folder_url)
        except LocationError as exc:
            out.append(str(exc))
        if not self.client_id:
            out.append("M365_CLIENT_ID is not set (the Entra app's Application (client) ID).")
        if not self.client_secret:
            out.append("M365_CLIENT_SECRET is not set (a client secret of that app).")
        if location is not None and not (self.tenant_id or default_tenant_for_host(location.hostname)):
            out.append("M365_TENANT_ID is not set and cannot be derived from the address.")
        return out


#: Well below the 255-byte file name limit of every Linux filesystem, in bytes,
#: because a name of umlauts is twice as long in bytes as in characters.
_MAX_STEM_BYTES = 150


def temp_filename(name: str) -> str:
    """A safe local name that keeps the extension extraction reads by.

    Long Outlook subjects and SharePoint names routinely pass 200 characters;
    cutting them blindly dropped the ``.msg``/``.pdf`` (so the file counted as
    unconvertible and was skipped for good) or overran 255 bytes (so the
    download failed every cycle).
    """
    cleaned = "".join("_" if c in '/\\:\x00' else c for c in (name or "")).strip().strip(".")
    stem, dot, ext = cleaned.rpartition(".")
    if not dot or not ext or len(ext) > 16 or not ext.isalnum() or not stem:
        stem, ext = cleaned, ""
    encoded = stem.encode("utf-8")[:_MAX_STEM_BYTES]
    stem = encoded.decode("utf-8", errors="ignore").strip() or "document"
    return f"{stem}.{ext}" if ext else stem


def remove_stale_temp_copies() -> int:
    """Delete temp copies a killed worker left behind (``finally`` never ran)."""
    removed = 0
    for leftover in Path(tempfile.gettempdir()).glob("knovas-m365-*"):
        shutil.rmtree(leftover, ignore_errors=True)
        removed += 1
    return removed


class M365Source:
    """Resolution, inventory, downloads and previews for the configured folder."""

    def __init__(self, settings: M365Settings, *, client: Any = None) -> None:
        problems = settings.problems()
        if problems:
            raise M365Error("; ".join(problems))
        self.settings = settings
        self.location = parse_folder_url(settings.folder_url)
        tenant = settings.tenant_id or default_tenant_for_host(self.location.hostname)
        self._client = client or GraphClient(
            tenant_id=tenant, client_id=settings.client_id, client_secret=settings.client_secret
        )
        # For calls made inside an HTTP request (the preview): one attempt,
        # short timeout. The sync client's patient retries (minutes, during a
        # Graph throttling episode) would outlast gunicorn's worker timeout and
        # take the running sync down with the request.
        self._request_client = client or GraphClient(
            tenant_id=tenant,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            request_timeout=8.0,
            max_attempts=1,
            adapter_retries=0,
        )
        # _lock guards resolution and the inventory object; _refresh_lock is
        # held for the whole Graph round trip; _links_lock for the link table.
        # Kept apart so a preview never waits for a refresh -- the first pass
        # over a large library takes minutes, and Knovas Connector has a single
        # gunicorn worker whose request timeout would kill the sync with it.
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._links_lock = threading.Lock()
        self._resolved: Optional[ResolvedFolder] = None
        self._inventory: Optional[DriveInventory] = None
        self._last_refresh: float = 0.0
        self._last_error: Optional[str] = None
        self._last_ok_at: Optional[str] = None
        self._links_digest: Optional[str] = None
        self._links_by_doc: Optional[dict[str, dict[str, str]]] = None

    # ------------------------------------------------------------- resolution
    def _resolution_path(self) -> Path:
        return self.settings.state_dir / "resolution.json"

    def _load_resolution(self) -> Optional[ResolvedFolder]:
        try:
            data = json.loads(self._resolution_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("folder_url") != self.settings.folder_url:
            return None
        return ResolvedFolder.from_dict(data.get("resolved") or {})

    def _save_resolution(self, resolved: ResolvedFolder) -> None:
        path = self._resolution_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"folder_url": self.settings.folder_url, "resolved": resolved.as_dict()}),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def resolved(self) -> ResolvedFolder:
        """The drive and folder the address names; resolved once, then cached.

        The cache is keyed by the address, so changing ``M365_FOLDER_URL``
        resolves again, and the inventory of a different library starts fresh.
        """
        if self._resolved is not None:
            return self._resolved
        with self._lock:
            if self._resolved is None:
                cached = self._load_resolution()
                if cached is None:
                    try:
                        cached = resolve_folder(self._client, self.location)
                    except (ResolveError, GraphAuthError, GraphRequestError,
                            requests.RequestException) as exc:
                        raise M365Error(str(exc)) from exc
                    self._save_resolution(cached)
                self._resolved = cached
            return self._resolved

    def _inventory_for(self) -> DriveInventory:
        with self._lock:
            if self._inventory is None:
                resolved = self.resolved()
                self._inventory = DriveInventory(
                    self._client,
                    drive_id=resolved.drive_id,
                    folder_id=resolved.folder_id,
                    folder_is_root=not resolved.folder_path,
                    state_path=self.settings.state_dir / "inventory.json",
                )
            return self._inventory

    # ---------------------------------------------------------------- refresh
    def refresh(self, *, max_age_seconds: float = 0) -> None:
        """Bring the inventory up to date; raises ``M365Error`` if Graph cannot.

        A failure is never papered over with the previous view for a sync
        cycle (``max_age_seconds=0``): pruning from a stale or partial view is
        how documents get deleted from Knovas that still exist.

        A browsing read (``max_age_seconds > 0``) never waits for a refresh
        that is already running: it takes the last complete view, or -- during
        the very first pass -- says the folder is still being read.
        """
        if max_age_seconds > 0:
            # A browsing read (folder picker, status page) never talks to Graph
            # itself: it reads the last complete view and, when that is older
            # than max_age_seconds, has a refresh started in the background.
            inventory = self._inventory
            if inventory is None and self._resolved is None and self._load_resolution() is not None:
                inventory = self._inventory_for()  # cached resolution: no network
            if inventory is None or not inventory.complete:
                self._start_background_refresh()
                raise M365Error(
                    "The OneDrive/SharePoint folder is still being read for the first "
                    "time; try again in a minute."
                )
            if time.monotonic() - self._last_refresh >= max_age_seconds:
                self._start_background_refresh()
            return
        self._refresh_lock.acquire()
        self._refresh_locked()

    def _start_background_refresh(self) -> None:
        if not self._refresh_lock.acquire(blocking=False):
            return  # already under way

        def run() -> None:
            try:
                self._refresh_locked()
            except M365Error as exc:
                logger.warning("Microsoft 365 background read failed: %s", exc)

        threading.Thread(target=run, name="m365-refresh", daemon=True).start()

    def _forget_resolution(self) -> None:
        """Resolve the address again next time (the folder's id changed)."""
        with self._lock:
            self._resolved = None
            self._inventory = None
            try:
                self._resolution_path().unlink()
            except OSError:
                pass

    def _refresh_locked(self) -> None:
        """Refresh while holding ``_refresh_lock``; always releases it, also
        when resolving the address fails before any refresh starts."""
        try:
            try:
                inventory = self._inventory_for()
                stats = inventory.refresh()
            except FolderMissingError as exc:
                # Deleted -- or deleted and re-created under the same address,
                # which gives it a new id. Resolve again next cycle: a folder
                # that is really gone fails there, and nothing is pruned either
                # way; one that is back under a new id is picked up.
                self._forget_resolution()
                self._last_error = str(exc)
                raise M365Error(str(exc)) from exc
            except (M365Error, InventoryError, GraphAuthError, GraphRequestError,
                    requests.RequestException) as exc:
                self._last_error = str(exc)
                raise M365Error(str(exc)) from exc
            self._last_refresh = time.monotonic()
            self._last_error = None
            self._last_ok_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if stats.get("changes"):
                logger.info(
                    "Microsoft 365 folder: %d change(s), %d item(s) tracked",
                    stats["changes"],
                    stats["items"],
                )
        finally:
            self._refresh_lock.release()

    def files(self) -> dict[str, RemoteFile]:
        try:
            return self._inventory_for().files()
        except InventoryError as exc:
            raise M365Error(str(exc)) from exc

    def folders(self) -> dict[str, RemoteFolder]:
        try:
            return self._inventory_for().folders()
        except InventoryError as exc:
            raise M365Error(str(exc)) from exc

    # -------------------------------------------------------------- downloads
    @contextmanager
    def local_copy(self, remote: RemoteFile) -> Iterator[Path]:
        """The file's content in a private temp directory, deleted on exit.

        The file keeps its own name, so extraction picks the right reader by
        extension and falls back to the right title.
        """
        tmp_dir = Path(tempfile.mkdtemp(prefix="knovas-m365-"))
        try:
            os.chmod(tmp_dir, 0o700)
            dest = tmp_dir / temp_filename(remote.name)
            try:
                self._client.download_to(
                    remote.drive_id, remote.item_id, dest, expected_size=remote.size or None
                )
            except (GraphAuthError, GraphRequestError, requests.RequestException, OSError) as exc:
                raise M365Error(f"download of {remote.rel_path} failed: {exc}") from exc
            yield dest
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------ links
    def write_links(self, rows: list[dict[str, str]]) -> None:
        """Publish identifier -> web address for the Platform (open and preview).

        Rewritten only when something changed, so the Platform does not reload
        an identical file after every idle cycle.
        """
        ordered = sorted(rows, key=lambda r: r["doc_id"])
        body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in ordered)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with self._links_lock:
            path = self.settings.links_path
            if digest == self._links_digest and path.exists():
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".links-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(body)
                # The Platform reads it through a read-only mount as another uid.
                os.chmod(tmp, 0o644)
                os.replace(tmp, path)
            except OSError:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            self._links_digest = digest
            self._links_by_doc = {r["doc_id"]: r for r in ordered}

    def _link_for(self, doc_id: str) -> Optional[dict[str, str]]:
        with self._links_lock:
            if self._links_by_doc is None:
                by_doc: dict[str, dict[str, str]] = {}
                try:
                    with open(self.settings.links_path, encoding="utf-8") as fh:
                        for line in fh:
                            try:
                                row = json.loads(line)
                            except ValueError:
                                continue
                            if isinstance(row, dict) and row.get("doc_id"):
                                by_doc[str(row["doc_id"])] = row
                except OSError:
                    return None
                self._links_by_doc = by_doc
            return self._links_by_doc.get(doc_id)

    # ---------------------------------------------------------------- preview
    def preview(self, doc_id: str, *, page: Optional[int] = None) -> dict[str, Any]:
        """A short-lived, embeddable Microsoft 365 viewer URL for an indexed file.

        Only identifiers this source itself published can be previewed, so a
        caller cannot turn this into a way of reading arbitrary drive items.
        """
        row = self._link_for(doc_id)
        if not row or not row.get("item_id"):
            raise KeyError(doc_id)
        body: dict[str, Any] = {}
        if page is not None and page >= 1:
            body["page"] = str(page)
        drive_id = row.get("drive_id") or (self._resolved.drive_id if self._resolved else "")
        if not drive_id:
            raise M365Error("preview unavailable until the next sync cycle publishes links")
        try:
            status, data = self._request_client.post_json(
                f"{GRAPH}/drives/{drive_id}/items/{row['item_id']}/preview", body
            )
        except (GraphAuthError, GraphRequestError, requests.RequestException) as exc:
            raise M365Error(f"preview failed: {exc}") from exc
        if status != 200:
            raise M365Error(f"preview failed: HTTP {status}")
        out: dict[str, Any] = {"web_url": row.get("web_url") or ""}
        for key in ("getUrl", "postUrl", "postParameters"):
            if data.get(key):
                out[key] = data[key]
        if not (out.get("getUrl") or out.get("postUrl")):
            raise M365Error("Microsoft 365 offers no preview for this file")
        return out

    # ----------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        return {
            "configured": True,
            "kind": "onedrive" if self.location.is_onedrive else "sharepoint",
            "resolved": self._resolved is not None,
            "last_ok_at": self._last_ok_at,
            "last_error": self._last_error,
        }


# ------------------------------------------------------------------ module API
_source: Optional[M365Source] = None
_source_error: Optional[str] = None
_source_lock = threading.Lock()


def m365_configured() -> bool:
    return bool((os.environ.get("M365_FOLDER_URL") or "").strip())


def active_m365_source() -> Optional[M365Source]:
    """The configured source, or None when documents come from a file share.

    Raises ``M365Error`` when an address is configured but unusable: falling
    back to the (empty) watch root would make the next cycle prune every
    document from Knovas.
    """
    global _source, _source_error
    if not m365_configured():
        return None
    with _source_lock:
        if _source is None:
            settings = M365Settings.from_env()
            assert settings is not None
            try:
                _source = M365Source(settings)
            except M365Error as exc:
                _source_error = str(exc)
                raise
        return _source


def reset_m365_source(source: Optional[M365Source] = None) -> None:
    """Tests: install a prepared source, or forget the cached one."""
    global _source, _source_error
    with _source_lock:
        _source = source
        _source_error = None


def watch_root_subpath(source_path: str) -> Optional[str]:
    """Map a sync-body source path onto the configured folder.

    ``/mnt/documents`` -> ``""``, ``/mnt/documents/Akten`` -> ``"Akten"``, and a
    relative ``Akten`` means the same. None for anything outside the root.
    """
    roots = [r.rstrip("/") or "/" for r in get_config().rc_watch_roots] or ["/mnt/documents"]
    raw = (source_path or "").strip()
    rel: Optional[str] = None
    if raw.startswith("/"):
        normalized = str(PurePosixPath(raw))
        for root in roots:
            if normalized == root:
                rel = ""
                break
            if normalized.startswith(root.rstrip("/") + "/"):
                rel = normalized[len(root.rstrip("/")) + 1:]
                break
    else:
        rel = raw
    if rel is None:
        return None
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if any(p in (".", "..") for p in parts):
        return None
    return "/".join(parts)
