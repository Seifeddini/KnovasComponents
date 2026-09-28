"""What is in the configured OneDrive/SharePoint folder, kept current with delta.

No file content is stored here -- only each item's id, name, parent, size,
modification time and web address. A sync cycle asks Graph for what changed
since the last cycle (one request when nothing did) and downloads a file only
when it is new or modified, straight into a temporary directory that is
deleted once the file is indexed.

Items are tracked **by id**, never by path, because that is the only thing
delta guarantees: its items carry no ``parentReference.path``, and renaming or
moving a folder reports the folder alone, not its descendants. Paths are
therefore derived from the parent chain every time they are needed, which
makes a folder rename move its whole subtree for free. Delta only runs on a
drive's root in OneDrive for Business and SharePoint, so the whole drive is
tracked and the configured folder is a filter over it; an item moved into the
folder from elsewhere is thereby already known.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional, Protocol

from onedrive_mirror.graph import DeltaTokenInvalid, GraphRequestError

logger = logging.getLogger(__name__)

STATE_VERSION = 1

#: Everything the inventory reads from a delta item, and nothing more.
DELTA_SELECT = (
    "id,name,parentReference,file,folder,package,root,size,"
    "lastModifiedDateTime,webUrl,deleted"
)

_KIND_ROOT = "r"
_KIND_FOLDER = "d"
_KIND_FILE = "f"
_KIND_OTHER = "o"

#: Deeper than any real folder tree; stops a corrupt parent chain from looping.
_MAX_DEPTH = 256


class DeltaSource(Protocol):
    def delta_pages(
        self, drive_id: str, delta_url: Optional[str] = None, *, select: Optional[str] = None
    ) -> Iterator[tuple[list[dict], Optional[str]]]: ...


class InventoryError(RuntimeError):
    """The inventory cannot give a trustworthy view of the folder right now."""


class FolderMissingError(InventoryError):
    """The configured folder's id is gone from the library."""


@dataclass(frozen=True)
class RemoteFile:
    """A file in the configured folder, addressed by Graph ids."""

    drive_id: str
    item_id: str
    #: Path below the configured folder, "/"-separated.
    rel_path: str
    name: str
    size: int
    modified_iso: str
    web_url: str


@dataclass(frozen=True)
class RemoteFolder:
    item_id: str
    rel_path: str
    name: str
    modified_iso: str


def _safe_name(name: str) -> bool:
    return bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name and "\x00" not in name


def _iso_z(value: str) -> str:
    value = (value or "").strip()
    return value.replace("+00:00", "Z") if value else ""


def _record_for(item: dict[str, Any], previous: Optional[list]) -> list:
    parent = str((item.get("parentReference") or {}).get("id") or "")
    name = item.get("name")
    if not isinstance(name, str) or not name:
        name = previous[0] if previous else ""
    if "root" in item:
        kind = _KIND_ROOT
    elif "folder" in item:
        kind = _KIND_FOLDER
    elif "file" in item:
        kind = _KIND_FILE
    else:
        kind = _KIND_OTHER  # OneNote packages, shortcuts: not files we can read
    try:
        size = int(item.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    return [
        name,
        parent,
        kind,
        size,
        _iso_z(str(item.get("lastModifiedDateTime") or "")),
        str(item.get("webUrl") or ""),
    ]


class DriveInventory:
    """Id-tracked view of one drive, filtered to one folder.

    Thread-safe without making readers wait: a refresh builds the next view on
    a copy and swaps one reference at the end, so ``/discover`` keeps reading
    the last complete view while the scheduler's worker spends minutes on the
    first pass over a large library. Refreshes themselves are serialised.
    """

    def __init__(
        self,
        client: DeltaSource,
        *,
        drive_id: str,
        folder_id: str,
        folder_is_root: bool,
        state_path: Path,
    ) -> None:
        self._client = client
        self._drive_id = drive_id
        self._folder_id = folder_id
        self._folder_is_root = folder_is_root
        self._state_path = Path(state_path)
        self._refresh_lock = threading.Lock()
        self._items: dict[str, list] = {}
        self._delta_link: Optional[str] = None
        self._complete = False
        self._load()

    # ----------------------------------------------------------------- state
    def _load(self) -> None:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning("Microsoft 365 inventory unreadable (%s); starting over", exc)
            return
        if (
            not isinstance(data, dict)
            or data.get("version") != STATE_VERSION
            or data.get("drive_id") != self._drive_id
        ):
            # Another library, or another format: nothing in it describes this drive.
            return
        items = data.get("items")
        link = data.get("delta_link")
        if isinstance(items, dict) and isinstance(link, str) and link:
            self._items = {str(k): v for k, v in items.items() if isinstance(v, list) and len(v) == 6}
            self._delta_link = link
            self._complete = True

    def _save(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "drive_id": self._drive_id,
            "delta_link": self._delta_link,
            "items": self._items,
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._state_path.parent, prefix=".inventory-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._state_path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # --------------------------------------------------------------- refresh
    def refresh(self) -> dict[str, int]:
        """Apply every change since the last refresh. Raises on any failure.

        Changes are staged on a copy and only swapped in once Graph has handed
        over a new delta link, so a refresh that dies half way leaves the last
        good view (and its link) in place and the next one simply repeats.
        """
        with self._refresh_lock:
            try:
                return self._refresh_from(self._delta_link)
            except DeltaTokenInvalid:
                # Graph forgot our position (410). Enumerate again from scratch;
                # anything that vanished meanwhile drops out with the old view.
                logger.warning("Microsoft 365 delta position expired; re-reading the library")
                return self._refresh_from(None)

    def _refresh_from(self, link: Optional[str]) -> dict[str, int]:
        staged: dict[str, list] = dict(self._items) if link else {}
        changed = 0
        new_link: Optional[str] = None
        try:
            for page, delta_link in self._client.delta_pages(
                self._drive_id, link, select=DELTA_SELECT
            ):
                for item in page:
                    if not isinstance(item, dict):
                        continue
                    item_id = str(item.get("id") or "")
                    if not item_id:
                        continue
                    changed += 1
                    if "deleted" in item:
                        staged.pop(item_id, None)
                    else:
                        staged[item_id] = _record_for(item, staged.get(item_id))
                if delta_link:
                    new_link = delta_link
        except GraphRequestError as exc:
            raise InventoryError(f"Microsoft 365 change feed failed: {exc}") from exc
        if not new_link:
            raise InventoryError("Microsoft 365 change feed ended without a position to resume from")
        if not self._folder_is_root and self._folder_id not in staged:
            raise FolderMissingError(
                "The configured OneDrive/SharePoint folder no longer exists (deleted, or "
                "the app lost access). Nothing is removed from Knovas until it is back "
                "or the address is changed."
            )
        # Link first, then the view readers pick up; both before the save, so
        # a failed write costs a repeated delta next time, never a lost change.
        first = link is None
        self._delta_link = new_link
        self._items = staged
        self._complete = True
        if changed or first:
            # An unchanged cycle keeps the saved position: resuming from it
            # after a restart only replays nothing. Rewriting the whole
            # inventory every minute for no change was pure disk churn.
            self._save()
        return {"changes": changed, "items": len(staged)}

    @property
    def complete(self) -> bool:
        return self._complete

    # ------------------------------------------------------------------ views
    def _path_resolver(self, items: dict[str, list]):
        cache: dict[str, Optional[str]] = {self._folder_id: ""}

        def path_of(item_id: str, depth: int = 0) -> Optional[str]:
            if item_id in cache:
                return cache[item_id]
            record = items.get(item_id)
            result: Optional[str] = None
            if record is not None and depth < _MAX_DEPTH and record[2] != _KIND_ROOT:
                name, parent = record[0], record[1]
                if parent and _safe_name(name):
                    parent_path = path_of(parent, depth + 1)
                    if parent_path is not None:
                        result = f"{parent_path}/{name}" if parent_path else name
            cache[item_id] = result
            return result

        return path_of

    def files(self) -> dict[str, RemoteFile]:
        """Every file below the configured folder, keyed by its relative path."""
        items = self._items  # one consistent view; a refresh swaps, never mutates it
        if not self._complete:
            raise InventoryError("The Microsoft 365 folder has not been read yet")
        path_of = self._path_resolver(items)
        out: dict[str, RemoteFile] = {}
        for item_id, record in items.items():
            if record[2] != _KIND_FILE:
                continue
            rel = path_of(item_id)
            if not rel:
                continue
            out[rel] = RemoteFile(
                drive_id=self._drive_id,
                item_id=item_id,
                rel_path=rel,
                name=record[0],
                size=int(record[3] or 0),
                modified_iso=record[4] or "1970-01-01T00:00:00Z",
                web_url=record[5],
            )
        return out

    def folders(self) -> dict[str, RemoteFolder]:
        """Every folder below the configured folder, keyed by its relative path."""
        items = self._items
        if not self._complete:
            raise InventoryError("The Microsoft 365 folder has not been read yet")
        path_of = self._path_resolver(items)
        out: dict[str, RemoteFolder] = {}
        for item_id, record in items.items():
            if record[2] != _KIND_FOLDER:
                continue
            rel = path_of(item_id)
            if not rel:
                continue
            out[rel] = RemoteFolder(
                item_id=item_id, rel_path=rel, name=record[0], modified_iso=record[4]
            )
        return out
