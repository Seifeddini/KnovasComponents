"""Resolving an address walks site -> library -> folder with read-only Graph calls."""
from __future__ import annotations

from urllib.parse import unquote

import pytest

from m365.location import parse_folder_url
from m365.resolver import ResolveError, resolve_folder

G = "https://graph.microsoft.com/v1.0"


class FakeGraph:
    """Answers GETs from a table keyed by the URL without its query string."""

    def __init__(self, table):
        self.table = table
        self.calls: list[str] = []

    def get_json(self, url):
        self.calls.append(url)
        key = unquote(url.split("?", 1)[0])
        return self.table.get(key, (404, {"error": {"message": "itemNotFound"}}))


def _sharepoint_table():
    return {
        f"{G}/sites/contoso.sharepoint.com:/sites/Kanzlei": (
            200,
            {"id": "site-k", "webUrl": "https://contoso.sharepoint.com/sites/Kanzlei"},
        ),
        f"{G}/sites/site-k/drives": (
            200,
            {
                "value": [
                    {"id": "drv-docs", "name": "Dokumente",
                     "webUrl": "https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents"},
                    {"id": "drv-arch", "name": "Archiv",
                     "webUrl": "https://contoso.sharepoint.com/sites/Kanzlei/Archiv"},
                ]
            },
        ),
        f"{G}/drives/drv-docs/root:/Akten/2024": (
            200,
            {"id": "fld-2024", "name": "2024", "folder": {"childCount": 3},
             "webUrl": "https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents/Akten/2024"},
        ),
        f"{G}/drives/drv-docs/root": (
            200,
            {"id": "root-docs", "name": "root", "root": {}, "folder": {},
             "webUrl": "https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents"},
        ),
        f"{G}/drives/drv-docs/root:/Akten/brief.docx": (
            200, {"id": "file-1", "name": "brief.docx", "file": {}},
        ),
    }


def test_sharepoint_folder_resolves_to_library_and_folder():
    graph = FakeGraph(_sharepoint_table())
    loc = parse_folder_url("https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents/Akten/2024")
    r = resolve_folder(graph, loc)
    assert (r.site_id, r.drive_id, r.folder_id, r.folder_path) == ("site-k", "drv-docs", "fld-2024", "Akten/2024")
    # Only reads, and only the three kinds Sites.Read.All covers.
    assert all("/shares/" not in c for c in graph.calls)


def test_library_root_resolves_to_drive_root():
    graph = FakeGraph(_sharepoint_table())
    loc = parse_folder_url(
        "https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents/Forms/AllItems.aspx"
    )
    r = resolve_folder(graph, loc)
    assert (r.drive_id, r.folder_id, r.folder_path) == ("drv-docs", "root-docs", "")


def test_library_name_match_is_case_insensitive():
    graph = FakeGraph(_sharepoint_table())
    loc = parse_folder_url("https://contoso.sharepoint.com/sites/kanzlei/shared%20documents/Akten/2024")
    graph.table[f"{G}/sites/contoso.sharepoint.com:/sites/kanzlei"] = graph.table[
        f"{G}/sites/contoso.sharepoint.com:/sites/Kanzlei"
    ]
    assert resolve_folder(graph, loc).drive_id == "drv-docs"


def test_onedrive_resolves_through_the_personal_site():
    table = {
        f"{G}/sites/contoso-my.sharepoint.com:/personal/anna_contoso_ch": (
            200, {"id": "site-anna", "webUrl": "https://contoso-my.sharepoint.com/personal/anna_contoso_ch"},
        ),
        f"{G}/sites/site-anna/drives": (
            200,
            {"value": [{"id": "drv-anna", "name": "OneDrive",
                        "webUrl": "https://contoso-my.sharepoint.com/personal/anna_contoso_ch/Documents"}]},
        ),
        f"{G}/drives/drv-anna/root:/Akten": (200, {"id": "fld-akten", "name": "Akten", "folder": {}}),
    }
    loc = parse_folder_url(
        "https://contoso-my.sharepoint.com/my?id=%2Fpersonal%2Fanna_contoso_ch%2FDocuments%2FAkten"
    )
    r = resolve_folder(FakeGraph(table), loc)
    assert (r.drive_id, r.folder_id, r.folder_path) == ("drv-anna", "fld-akten", "Akten")


def test_whole_onedrive_uses_the_default_library():
    table = {
        f"{G}/sites/contoso-my.sharepoint.com:/personal/anna_contoso_ch": (200, {"id": "site-anna"}),
        f"{G}/sites/site-anna/drives": (200, {"value": []}),
        f"{G}/sites/site-anna/drive": (
            200,
            {"id": "drv-anna", "webUrl": "https://contoso-my.sharepoint.com/personal/anna_contoso_ch/Documents"},
        ),
        f"{G}/drives/drv-anna/root": (200, {"id": "root-anna", "root": {}, "folder": {}}),
    }
    loc = parse_folder_url("https://contoso-my.sharepoint.com/personal/anna_contoso_ch/_layouts/15/onedrive.aspx")
    r = resolve_folder(FakeGraph(table), loc)
    assert (r.drive_id, r.folder_id, r.folder_path) == ("drv-anna", "root-anna", "")


def test_a_file_address_is_refused():
    graph = FakeGraph(_sharepoint_table())
    loc = parse_folder_url("https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents/Akten/brief.docx")
    with pytest.raises(ResolveError, match="is a file"):
        resolve_folder(graph, loc)


def test_missing_permission_names_the_permission():
    table = {f"{G}/sites/contoso.sharepoint.com:/sites/Kanzlei": (403, {"error": {"message": "Access denied"}})}
    loc = parse_folder_url("https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents")
    with pytest.raises(ResolveError, match="Sites.Read.All"):
        resolve_folder(FakeGraph(table), loc)


def test_unknown_library_is_reported():
    graph = FakeGraph(_sharepoint_table())
    loc = parse_folder_url("https://contoso.sharepoint.com/sites/Kanzlei/Nirgends/Akten")
    with pytest.raises(ResolveError, match="No OneDrive or SharePoint document library"):
        resolve_folder(graph, loc)
