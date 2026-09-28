"""Turn the folder address a person copies from the browser into a location.

One setting covers OneDrive and SharePoint alike, because to Microsoft Graph
both are the same thing: a site (a OneDrive is the site
``/personal/<user>``), a document library in it (a *drive*), and a folder in
that. What varies is only how the browser spells the address:

- the plain path, ``https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents/Akten``
- a library view, ``.../Shared%20Documents/Forms/AllItems.aspx?id=%2Fsites%2FKanzlei%2F...``
- OneDrive's own page, ``https://contoso-my.sharepoint.com/my?id=%2Fpersonal%2Fanna_contoso_ch%2FDocuments%2FAkten``
  or ``.../personal/anna_contoso_ch/_layouts/15/onedrive.aspx?id=...``
- a "copy link" address that still carries the path, ``https://contoso.sharepoint.com/:f:/r/sites/Kanzlei/...``

What cannot be accepted is a *sharing* link (``/:f:/s/...``, ``/:f:/g/...``):
it hides the path behind a token, and Graph only resolves those tokens for an
app holding write permission. This module names that case instead of
guessing, so the operator is told to paste the address bar instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

#: ``/:f:/r/sites/...`` -- a copied link that still carries the resource path.
_RESOURCE_LINK = re.compile(r"^/:[a-z]:/r(/.*)?$", re.IGNORECASE)
#: ``/:f:/s/...``, ``/:w:/g/...`` and friends -- token links, no path inside.
_TOKEN_LINK = re.compile(r"^/:[a-z]:/[a-z](/|$)", re.IGNORECASE)


class LocationError(ValueError):
    """The configured address is not a OneDrive or SharePoint folder address."""


@dataclass(frozen=True)
class FolderLocation:
    """A OneDrive/SharePoint folder, as host plus decoded path segments."""

    url: str
    hostname: str
    segments: tuple[str, ...]

    @property
    def is_onedrive(self) -> bool:
        return self.hostname.endswith("-my.sharepoint.com") or (
            bool(self.segments) and self.segments[0].lower() == "personal"
        )

    @property
    def server_relative_path(self) -> str:
        return "/" + "/".join(self.segments)


def default_tenant_for_host(hostname: str) -> str:
    """``contoso.sharepoint.com`` / ``contoso-my.sharepoint.com`` -> ``contoso.onmicrosoft.com``.

    Microsoft's token endpoint accepts the initial domain wherever it accepts
    the tenant id, and every Microsoft 365 tenant keeps its initial
    ``<name>.onmicrosoft.com`` domain for life, so the tenant does not have to
    be configured separately for the ordinary case.
    """
    host = (hostname or "").lower()
    suffix = ".sharepoint.com"
    if not host.endswith(suffix):
        return ""
    name = host[: -len(suffix)]
    if name.endswith("-my"):
        name = name[: -len("-my")]
    if not name or "." in name:
        return ""
    return f"{name}.onmicrosoft.com"


def _split_path(raw: str) -> list[str]:
    return [unquote(part) for part in raw.split("/") if part]


def _strip_view_pages(segments: list[str]) -> list[str]:
    """Drop the parts of a browser address that are pages, not folders."""
    lowered = [s.lower() for s in segments]
    if "_layouts" in lowered:
        segments = segments[: lowered.index("_layouts")]
        lowered = lowered[: len(segments)]
    # ".../Shared Documents/Forms/AllItems.aspx" is the library's default view.
    if len(segments) >= 2 and lowered[-2] == "forms" and lowered[-1].endswith(".aspx"):
        segments = segments[:-2]
    return segments


def parse_folder_url(url: str) -> FolderLocation:
    """Parse the configured address, or raise ``LocationError`` saying why not."""
    raw = (url or "").strip()
    if not raw:
        raise LocationError("No OneDrive/SharePoint folder address is configured.")
    parts = urlsplit(raw)
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise LocationError(
            f"{raw!r} is not an https:// address. Open the folder in OneDrive or "
            "SharePoint and copy the address from the browser's address bar."
        )
    hostname = parts.hostname.lower()
    if ".sharepoint." not in hostname:
        raise LocationError(
            f"{hostname} is not a OneDrive or SharePoint host (expected "
            "<firm>.sharepoint.com or <firm>-my.sharepoint.com)."
        )

    path = parts.path or "/"
    if _RESOURCE_LINK.match(path):
        path = _RESOURCE_LINK.match(path).group(1) or "/"
    elif _TOKEN_LINK.match(path):
        raise LocationError(
            "This is a sharing link, which hides the folder's location. Open the "
            "folder in the browser and copy the address from the address bar instead."
        )

    # Library views and OneDrive's own page name the folder in ?id=.
    query = parse_qs(parts.query)
    id_values = [v for v in query.get("id", []) if v.strip()]
    if id_values:
        segments = _split_path(id_values[0])
    else:
        segments = _strip_view_pages(_split_path(path))

    lowered = [s.lower() for s in segments]
    if segments and lowered[-1].endswith(".aspx"):
        raise LocationError(
            f"{raw!r} is a page, not a folder. Open the document library or folder "
            "and copy the address from there."
        )
    if lowered[:1] == ["my"] or (hostname.endswith("-my.sharepoint.com") and not segments):
        raise LocationError(
            "This OneDrive address does not say whose OneDrive it is. Open the folder "
            "in OneDrive and copy the address once the folder itself is showing."
        )
    if any(s in (".", "..") for s in segments):
        raise LocationError(f"{raw!r} contains a relative path segment.")
    return FolderLocation(url=raw, hostname=hostname, segments=tuple(segments))
