"""A OneDrive/SharePoint folder syncs like a share -- with nothing kept on disk."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

import pytest

from m365.source import M365Error, M365Settings, M365Source, reset_m365_source

G = "https://graph.microsoft.com/v1.0"
URL = "https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents/Akten"
OLD = "2019-03-04T05:06:07Z"
NEW = "2026-09-01T10:00:00Z"


def _folder(item_id, name, parent):
    return {"id": item_id, "name": name, "folder": {}, "parentReference": {"id": parent},
            "lastModifiedDateTime": OLD}


def _file(item_id, name, parent, text, modified=OLD):
    return {"id": item_id, "name": name, "file": {}, "size": len(text.encode()),
            "lastModifiedDateTime": modified, "parentReference": {"id": parent},
            "webUrl": f"https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents/Akten/{name}"}


class FakeGraph:
    def __init__(self):
        self.table = {
            f"{G}/sites/contoso.sharepoint.com:/sites/Kanzlei": (200, {"id": "site"}),
            f"{G}/sites/site/drives": (200, {"value": [
                {"id": "drv", "webUrl": "https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents"}]}),
            f"{G}/drives/drv/root:/Akten": (200, {"id": "akten", "name": "Akten", "folder": {}}),
        }
        self.contents = {"f-a": "Mietvertrag Schaffhauserstrasse", "f-b": "Klage Meierhans",
                         "f-c": "Vollmacht", "f-x": "privat"}
        self.pages = {"__initial__": [([
            {"id": "root", "root": {}, "folder": {}},
            _folder("akten", "Akten", "root"),
            _folder("sub", "Mandanten", "akten"),
            _folder("priv", "Privat", "root"),
            _file("f-a", "a.md", "akten", self.contents["f-a"]),
            _file("f-b", "b.txt", "sub", self.contents["f-b"]),
            _file("f-c", "c.md", "sub", self.contents["f-c"]),
            _file("f-x", "x.md", "priv", self.contents["f-x"]),
            {"id": "f-img", "name": "logo.png", "file": {}, "size": 5, "parentReference": {"id": "akten"},
             "lastModifiedDateTime": OLD, "webUrl": "https://x/logo.png"},
        ], "L1")]}
        self.downloads: list[Path] = []
        self.previews: list[tuple[str, dict]] = []
        self.fail_delta = False

    def get_json(self, url):
        return self.table.get(unquote(url.split("?", 1)[0]), (404, {}))

    def delta_pages(self, drive_id, delta_url=None, *, select=None):
        from onedrive_mirror.graph import GraphRequestError

        if self.fail_delta:
            raise GraphRequestError("Graph unavailable")
        yield from self.pages.get(delta_url or "__initial__", [([], delta_url)])

    def download_to(self, drive_id, item_id, dest, expected_size=None):
        Path(dest).write_text(self.contents[item_id], encoding="utf-8")
        self.downloads.append(Path(dest))
        return len(self.contents[item_id])

    def post_json(self, url, body):
        self.previews.append((url, body))
        return 200, {"getUrl": f"https://contoso.sharepoint.com/embed?item={url.split('/items/')[1]}"}


@pytest.fixture
def m365(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state / ".rc-sync-state.json"))
    monkeypatch.setenv("SEARCH_CONTEXT_STORE_PATH", str(tmp_path / "ctx"))
    monkeypatch.setenv("RC_WATCH_ROOTS", "/mnt/documents")
    monkeypatch.setenv("M365_FOLDER_URL", URL)
    monkeypatch.setenv("M365_CLIENT_ID", "client")
    monkeypatch.setenv("M365_CLIENT_SECRET", "secret")
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    graph = FakeGraph()
    settings = M365Settings.from_env()
    source = M365Source(settings, client=graph)
    reset_m365_source(source)
    yield graph, source, settings
    reset_m365_source(None)
    reset_config()


def _body(**over):
    body = {
        "mode": "incremental",
        "sources": [{"path": "/mnt/documents", "recursive": True}],
        "filters": {},
        "ingestion": {"identifier_prefix": "tenant"},
    }
    body.update(over)
    return body


def _uploader():
    from sync.knovas_uploader import SemantixUploader

    uploader = SemantixUploader()
    ok = MagicMock(status_code=200, content=b'{"key": "tx"}')
    ok.json.return_value = {"key": "tx"}
    return uploader, ok


def _run(body):
    from sync.sync_executor import run_sync_work

    uploader, ok = _uploader()
    with patch.object(uploader, "_request", return_value=ok) as req:
        result = run_sync_work(body, uploader)
    return result, req


def _links(settings):
    return {json.loads(l)["doc_id"]: json.loads(l) for l in settings.links_path.read_text().splitlines()}


def test_first_cycle_indexes_everything_and_keeps_nothing(m365):
    graph, source, settings = m365
    result, req = _run(_body())
    assert result.errors == []
    assert result.files_uploaded == 3
    identifiers = sorted(c.kwargs["json_body"]["identifier"] for c in req.call_args_list
                         if c.args[1] == "/secured/init_document_transmission")
    assert identifiers == ["tenant/Mandanten/b.txt", "tenant/Mandanten/c.md", "tenant/a.md"]
    # Outside the configured folder, and unreadable types, are never fetched.
    assert sorted(p.name for p in graph.downloads) == ["a.md", "b.txt", "c.md"]
    # Every temp copy is gone once indexed.
    assert all(not p.exists() and not p.parent.exists() for p in graph.downloads)
    # The Platform's open/preview links, one per document, keyed like Knovas.
    links = _links(settings)
    assert sorted(links) == identifiers
    assert links["tenant/a.md"]["item_id"] == "f-a"
    assert links["tenant/a.md"]["web_url"].endswith("/Akten/a.md")
    assert oct(settings.links_path.stat().st_mode & 0o777) == "0o644"
    # Search snippets were written from the temp copy.
    assert len(list(Path(os.environ["SEARCH_CONTEXT_STORE_PATH"]).glob("*.json"))) == 3


def test_old_files_are_not_filtered_by_the_default_profile(m365):
    from config import get_config
    from sync.default_sync_body import build_default_sync_body

    body = build_default_sync_body(get_config())
    assert "max_document_age_seconds" not in body["filters"]
    result, _ = _run(body)
    assert result.files_uploaded == 3  # all dated 2019


def test_unchanged_cycle_downloads_nothing(m365):
    graph, _, _ = m365
    _run(_body())
    graph.downloads.clear()
    result, req = _run(_body())
    assert result.files_uploaded == 0 and graph.downloads == []
    assert req.call_count == 0


def test_changed_file_is_fetched_again_deleted_file_is_removed(m365):
    graph, _, settings = m365
    _run(_body())
    graph.downloads.clear()
    graph.contents["f-a"] = "Mietvertrag Schaffhauserstrasse, Nachtrag"
    graph.pages["L1"] = [([
        _file("f-a", "a.md", "akten", graph.contents["f-a"], modified=NEW),
        {"id": "f-c", "deleted": {"state": "deleted"}},
    ], "L2")]
    result, req = _run(_body())
    assert [p.name for p in graph.downloads] == ["a.md"]
    assert result.files_uploaded == 1
    deletes = [c.kwargs["json_body"]["pointer"] for c in req.call_args_list if c.args[0] == "DELETE"]
    assert deletes == ["tenant/Mandanten/c.md"]
    assert "tenant/Mandanten/c.md" not in _links(settings)


def test_graph_failure_stops_the_cycle_before_anything_is_pruned(m365):
    graph, _, _ = m365
    _run(_body())
    graph.fail_delta = True
    from sync.sync_executor import run_sync_work

    uploader, ok = _uploader()
    with patch.object(uploader, "_request", return_value=ok) as req, pytest.raises(M365Error):
        run_sync_work(_body(), uploader)
    assert req.call_count == 0


def test_subfolder_sources_access_groups_and_non_recursive(m365):
    _, _, settings = m365
    body = _body(sources=[
        {"path": "/mnt/documents/Mandanten", "recursive": True, "access_groups": ["partner"]},
        {"path": "/mnt/documents", "recursive": False},
    ])
    result, req = _run(body)
    inits = {c.kwargs["json_body"]["identifier"]: c.kwargs["json_body"].get("access_groups")
             for c in req.call_args_list if c.args[1] == "/secured/init_document_transmission"}
    # Relative to each source folder, exactly as a share walk computes it.
    assert inits == {"tenant/b.txt": ["partner"], "tenant/c.md": ["partner"], "tenant/a.md": None}
    assert result.files_uploaded == 3


def test_source_outside_the_folder_is_skipped(m365):
    result, req = _run(_body(sources=[{"path": "/etc", "recursive": True}]))
    assert result.files_uploaded == 0 and req.call_count == 0


def test_discover_lists_the_folder_like_a_share(m365, monkeypatch):
    monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "true")
    from config import load_config, reset_config
    from app import create_app
    from util.schema import validate

    reset_config()
    load_config(validate=False, force_reload=True)
    client = create_app(skip_validation=True).test_client()

    # Before any sync has read the folder, the picker does not run the first
    # pass inside its request (a single gunicorn worker would time out and take
    # the sync with it): it starts it in the background and says so.
    first = client.get("/discover?max_depth=1")
    assert first.status_code == 503 and "still being read" in first.get_json()["error"]
    _wait_until_read(m365[1])

    resp = client.get("/discover?max_depth=1")
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert validate(data, "discover_response.schema.json") == []
    assert data["root"] == "/mnt/documents"
    assert {(e["path"], e["type"]) for e in data["entries"]} == {("Mandanten", "directory"), ("a.md", "file")}

    sub = client.get("/discover?root=/mnt/documents/Mandanten&max_depth=3").get_json()
    assert sub["root"] == "/mnt/documents/Mandanten"
    assert sorted(e["path"] for e in sub["entries"]) == ["b.txt", "c.md"]

    assert client.get("/discover?root=/etc").status_code == 403


def _wait_until_read(source, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if source._inventory_for().complete and not source._refresh_lock.locked():
            return
        time.sleep(0.01)
    raise AssertionError("background first read did not finish")


def test_browsing_never_waits_for_a_running_refresh(m365):
    graph, source, _ = m365
    source.refresh()  # first pass done
    graph.fail_delta = True  # would raise if browsing refreshed now
    assert source._refresh_lock.acquire(blocking=False)  # a sync cycle is mid-refresh
    try:
        source.refresh(max_age_seconds=0.000001)  # returns the last view at once
        assert "a.md" in source.files()
    finally:
        source._refresh_lock.release()


def test_preview_serves_only_published_documents(m365, monkeypatch):
    graph, _, _ = m365
    _run(_body())
    monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "true")
    from config import load_config, reset_config
    from app import create_app

    reset_config()
    load_config(validate=False, force_reload=True)
    client = create_app(skip_validation=True).test_client()

    resp = client.post("/m365/preview", json={"doc_id": "tenant/Mandanten/b.txt", "page": 2})
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["getUrl"].endswith("item=f-b/preview")
    assert data["web_url"].endswith("/b.txt")
    assert graph.previews[-1] == (f"{G}/drives/drv/items/f-b/preview", {"page": "2"})

    assert client.post("/m365/preview", json={"doc_id": "tenant/nope.pdf"}).status_code == 404
    assert client.post("/m365/preview", json={}).status_code == 400
    # Cross-origin browsers are refused, like every state-changing RC route.
    assert client.post("/m365/preview", json={"doc_id": "tenant/a.md"},
                       headers={"Origin": "https://evil.example"}).status_code == 403


def test_preview_links_survive_a_restart(m365):
    graph, _, settings = m365
    _run(_body())
    fresh = M365Source(settings, client=graph)
    assert fresh.preview("tenant/a.md")["getUrl"].endswith("item=f-a/preview")


def test_health_reports_the_microsoft_365_source(m365, monkeypatch):
    graph, source, _ = m365
    from app import create_app

    client = create_app(skip_validation=True).test_client()
    body = client.get("/health").get_json()
    assert body["checks"]["source"] == "m365"
    assert body["checks"]["watch_roots"] == "ok"  # /mnt/documents need not exist
    assert body["checks"]["m365"] == "ok"

    graph.fail_delta = True
    with pytest.raises(M365Error):
        source.refresh()
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.get_json()["checks"]["m365"] == "degraded"
    assert "Graph" not in json.dumps(resp.get_json())  # no error text on an open endpoint


def test_boot_refuses_an_incomplete_configuration(monkeypatch, capsys):
    from config import load_config, reset_config

    monkeypatch.setenv("M365_FOLDER_URL", "https://contoso.sharepoint.com/:f:/s/Kanzlei/Eabc")
    monkeypatch.delenv("M365_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("M365_CLIENT_ID", "client")
    monkeypatch.delenv("RC_SKIP_CONFIG_VALIDATION", raising=False)
    monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "true")
    monkeypatch.setenv("RC_WATCH_ROOTS", "/mnt/documents")
    reset_config()
    with pytest.raises(SystemExit):
        load_config(validate=True, force_reload=True)
    err = capsys.readouterr().err
    assert "sharing link" in err and "M365_CLIENT_SECRET" in err
    reset_config()
    load_config(validate=False, force_reload=True)


def test_check_command_reports_each_step(m365, capsys, monkeypatch):
    graph, _, _ = m365
    import m365.check as check

    monkeypatch.setattr(check, "M365Source", lambda settings: M365Source(settings, client=graph))
    assert check.main() == 0
    out = capsys.readouterr().out
    assert "SharePoint folder /sites/Kanzlei/Shared Documents/Akten" in out
    assert "1 folder(s), 4 file(s), 3 of a type Knovas indexes" in out


def test_long_file_names_keep_their_extension(m365):
    graph, _, _ = m365
    long_name = "Re " * 80 + "Offerte.md"  # 250 characters, like a long mail subject
    graph.contents["f-long"] = "Offerte Schaffhauserstrasse"
    graph.pages["L1"] = [([_file("f-long", long_name, "akten", graph.contents["f-long"])], "L2")]
    _run(_body())
    graph.downloads.clear()
    result, _ = _run(_body())
    assert result.errors == [] and result.files_uploaded == 1
    local = graph.downloads[-1].name
    assert local.endswith(".md") and len(local.encode()) < 255


def test_preview_needs_no_resolution_and_uses_the_link_drive(m365, tmp_path):
    graph, _, settings = m365
    _run(_body())
    assert _links(settings)["tenant/a.md"]["drive_id"] == "drv"
    graph.table.clear()  # any resolution attempt would now fail
    (settings.state_dir / "resolution.json").unlink()
    fresh = M365Source(settings, client=graph)
    assert fresh.preview("tenant/a.md")["getUrl"].endswith("item=f-a/preview")


def test_a_stale_browse_refreshes_in_the_background_and_answers_at_once(m365):
    graph, source, _ = m365
    source.refresh()
    graph.fail_delta = True
    source._last_refresh = 0  # stale
    source.refresh(max_age_seconds=60)  # no Graph call here, no exception
    assert "a.md" in source.files()
    _wait_until_read(source)
    assert source.status()["last_error"]  # the background refresh reported it


def test_a_failed_first_resolution_does_not_leave_the_sync_locked(m365, tmp_path):
    graph, _, settings = m365
    graph.table.clear()
    fresh = M365Source(settings, client=graph)
    with pytest.raises(M365Error, match="still being read"):
        fresh.refresh(max_age_seconds=60)
    import time

    deadline = time.monotonic() + 5
    while fresh._refresh_lock.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not fresh._refresh_lock.locked()
    with pytest.raises(M365Error):  # a sync cycle gets the error, not a hang
        fresh.refresh()


def test_a_recreated_folder_is_found_again(m365):
    graph, source, settings = m365
    source.refresh()
    graph.pages["L1"] = [([{"id": "akten", "deleted": {"state": "deleted"}}], "L2")]
    with pytest.raises(M365Error, match="no longer exists"):
        source.refresh()
    assert not (settings.state_dir / "resolution.json").exists()
    # Re-created under the same address: a new id. The failed refresh kept the
    # last good position (L1), so the feed from there carries both changes.
    graph.table[f"{G}/drives/drv/root:/Akten"] = (200, {"id": "akten-2", "name": "Akten", "folder": {}})
    graph.pages["L1"] = [([{"id": "akten", "deleted": {"state": "deleted"}},
                           _folder("akten-2", "Akten", "root"),
                           _file("f-n", "neu.md", "akten-2", "neu")], "L3")]
    graph.contents["f-n"] = "neu"
    source.refresh()
    assert sorted(source.files()) == ["neu.md"]


def test_the_stand_in_folder_is_never_synced_as_a_share(tmp_path, monkeypatch):
    root = tmp_path / "no-local-documents"
    root.mkdir()
    (root / ".knovas-no-local-documents").write_text("stand-in", encoding="utf-8")
    monkeypatch.setenv("RC_WATCH_ROOTS", str(root))
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.delenv("M365_FOLDER_URL", raising=False)
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    reset_m365_source(None)
    body = _body(sources=[{"path": str(root), "recursive": True}])
    from sync.sync_executor import run_sync_work

    uploader, ok = _uploader()
    with patch.object(uploader, "_request", return_value=ok) as req, \
            pytest.raises(RuntimeError, match="stand-in"):
        run_sync_work(body, uploader)
    assert req.call_count == 0
    reset_config()


def test_an_empty_source_never_prunes_the_whole_index(tmp_path, monkeypatch):
    root = tmp_path / "share"
    root.mkdir()
    (root / "a.md").write_text("eins", encoding="utf-8")
    monkeypatch.setenv("RC_WATCH_ROOTS", str(root))
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("SEARCH_CONTEXT_STORE_PATH", str(tmp_path / "ctx"))
    monkeypatch.delenv("M365_FOLDER_URL", raising=False)
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    reset_m365_source(None)
    body = _body(sources=[{"path": str(root), "recursive": True}])
    result, _ = _run(body)
    assert result.files_uploaded == 1
    (root / "a.md").unlink()  # the share is suddenly empty (unmounted, emptied)
    result, req = _run(body)
    assert [c for c in req.call_args_list if c.args[0] == "DELETE"] == []
    assert any("nothing removed" in e["error"] for e in result.errors)
    reset_config()
