"""Resolve a OneDrive/SharePoint folder address to a Graph drive and folder id.

Only read calls, and only ones ``Sites.Read.All`` (application) covers:
``GET /sites/{host}:/{path}``, ``GET /sites/{id}/drives``, ``GET /sites/{id}/drive``
and ``GET /drives/{id}/root:/{path}``. Graph's ``/shares`` endpoint would
resolve any link in one call, but for an app it requires
``Files.ReadWrite.All`` -- write access to every file in the tenant, to read
one folder. That is not a trade worth making, so the path is walked instead.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Protocol
from urllib.parse import quote, unquote, urlsplit

from m365.location import FolderLocation, LocationError

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"

#: Site collections live under these managed paths; everything else is the
#: tenant's root site.
_SITE_PREFIXES = ("sites", "teams", "personal")

PERMISSION_HINT = (
    "Grant the app the Microsoft Graph *application* permission Sites.Read.All "
    "and give admin consent (Entra admin center → App registrations → API permissions)."
)


class GraphLike(Protocol):
    def get_json(self, url: str) -> tuple[int, dict[str, Any]]: ...


class ResolveError(RuntimeError):
    """Graph answered, but not with the folder the address names."""


@dataclass(frozen=True)
class ResolvedFolder:
    site_id: str
    drive_id: str
    drive_web_url: str
    folder_id: str
    folder_web_url: str
    #: The folder's path inside its drive, "" for the drive root.
    folder_path: str

    def as_dict(self) -> dict[str, str]:
        return {
            "site_id": self.site_id,
            "drive_id": self.drive_id,
            "drive_web_url": self.drive_web_url,
            "folder_id": self.folder_id,
            "folder_web_url": self.folder_web_url,
            "folder_path": self.folder_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Optional["ResolvedFolder"]:
        try:
            return cls(**{k: str(data[k]) for k in (
                "site_id", "drive_id", "drive_web_url", "folder_id", "folder_web_url", "folder_path",
            )})
        except (KeyError, TypeError):
            return None


def _raise_for(status: int, body: dict[str, Any], what: str) -> None:
    message = ((body or {}).get("error") or {}).get("message") or ""
    if status in (401, 403):
        raise ResolveError(f"Microsoft 365 refused access to {what} (HTTP {status}). {PERMISSION_HINT}")
    raise ResolveError(f"Could not read {what} (HTTP {status}{': ' + message if message else ''}).")


def _segments_of(web_url: str) -> list[str]:
    return [unquote(p) for p in urlsplit(web_url).path.split("/") if p]


def _starts_with(segments: list[str], prefix: list[str]) -> bool:
    if len(prefix) > len(segments):
        return False
    return all(a.lower() == b.lower() for a, b in zip(segments, prefix))


def _quote_path(segments: Iterable[str]) -> str:
    return "/".join(quote(s, safe="") for s in segments)


def _site_candidates(location: FolderLocation) -> list[list[str]]:
    """Server-relative site paths to try, shortest first.

    ``/sites/Kanzlei`` is the site for nearly every address; a legacy subsite
    (``/sites/Kanzlei/Archiv``) only shows up when no library of the shorter
    site matches, which is why the loop in ``resolve_folder`` keeps going.
    """
    segs = list(location.segments)
    if segs and segs[0].lower() in _SITE_PREFIXES and len(segs) >= 2:
        return [segs[:k] for k in range(2, len(segs) + 1)]
    return [[]] + [segs[:k] for k in range(1, len(segs) + 1)]


def _get_site(client: GraphLike, hostname: str, site_path: list[str]) -> Optional[dict[str, Any]]:
    if site_path:
        url = f"{GRAPH}/sites/{hostname}:/{_quote_path(site_path)}"
    else:
        url = f"{GRAPH}/sites/{hostname}"
    status, body = client.get_json(url + ("?" if "?" not in url else "&") + "$select=id,webUrl")
    if status == 200 and body.get("id"):
        return body
    if status in (400, 404):
        return None
    _raise_for(status, body, f"the site {'/' + '/'.join(site_path) if site_path else hostname}")
    return None


def _list_drives(client: GraphLike, site_id: str) -> list[dict[str, Any]]:
    drives: list[dict[str, Any]] = []
    url: Optional[str] = f"{GRAPH}/sites/{site_id}/drives?$select=id,name,webUrl,driveType"
    pages = 0
    while url and pages < 50:
        pages += 1
        status, body = client.get_json(url)
        if status != 200:
            _raise_for(status, body, "the site's document libraries")
        drives.extend(d for d in body.get("value") or [] if isinstance(d, dict))
        url = body.get("@odata.nextLink")
    return drives


def _default_drive(client: GraphLike, site_id: str) -> dict[str, Any]:
    status, body = client.get_json(f"{GRAPH}/sites/{site_id}/drive?$select=id,name,webUrl,driveType")
    if status != 200:
        _raise_for(status, body, "the site's default document library")
    return body


def _get_folder(client: GraphLike, drive_id: str, path_segments: list[str]) -> dict[str, Any]:
    select = "$select=id,name,webUrl,folder,file,root"
    if path_segments:
        url = f"{GRAPH}/drives/{drive_id}/root:/{_quote_path(path_segments)}?{select}"
    else:
        url = f"{GRAPH}/drives/{drive_id}/root?{select}"
    status, body = client.get_json(url)
    if status == 404:
        raise ResolveError(
            f"The folder '{'/'.join(path_segments)}' does not exist in that library "
            "(or the app cannot see it)."
        )
    if status != 200:
        _raise_for(status, body, "the folder")
    if "folder" not in body and "root" not in body:
        raise ResolveError(
            f"'{body.get('name') or '/'.join(path_segments)}' is a file. Configure the "
            "folder that contains it."
        )
    return body


def resolve_folder(client: GraphLike, location: FolderLocation) -> ResolvedFolder:
    """Find the site, the document library and the folder the address names."""
    target = list(location.segments)
    for site_path in _site_candidates(location):
        site = _get_site(client, location.hostname, site_path)
        if site is None:
            continue
        site_id = str(site["id"])
        best: Optional[dict[str, Any]] = None
        best_len = -1
        for drive in _list_drives(client, site_id):
            drive_segments = _segments_of(str(drive.get("webUrl") or ""))
            if drive_segments and _starts_with(target, drive_segments) and len(drive_segments) > best_len:
                best, best_len = drive, len(drive_segments)
        if best is None and len(target) == len(site_path):
            # The address is the site itself (a OneDrive root, or a site home):
            # use the site's default library.
            best = _default_drive(client, site_id)
            best_len = len(_segments_of(str(best.get("webUrl") or ""))) or len(site_path)
        if best is None:
            continue
        drive_id = str(best["id"])
        folder_segments = target[best_len:] if best_len >= 0 else []
        folder = _get_folder(client, drive_id, folder_segments)
        resolved = ResolvedFolder(
            site_id=site_id,
            drive_id=drive_id,
            drive_web_url=str(best.get("webUrl") or ""),
            folder_id=str(folder["id"]),
            folder_web_url=str(folder.get("webUrl") or best.get("webUrl") or ""),
            folder_path="/".join(folder_segments),
        )
        logger.info(
            "Microsoft 365 folder resolved: library=%s folder=%r",
            resolved.drive_web_url,
            resolved.folder_path or "/",
        )
        return resolved
    raise ResolveError(
        f"No OneDrive or SharePoint document library matches {location.url!r}. Check "
        f"the address, and that the app may read the site. {PERMISSION_HINT}"
    )


__all__ = [
    "LocationError",
    "PERMISSION_HINT",
    "ResolveError",
    "ResolvedFolder",
    "resolve_folder",
]
