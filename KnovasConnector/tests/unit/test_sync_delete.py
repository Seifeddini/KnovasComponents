import os
from unittest.mock import MagicMock, patch

import pytest

from sync.sync_executor import (
    _pointer_for_relative,
    _prune_removed_documents,
    run_sync_work,
)
from sync.sync_state import SyncStateStore
from sync.sync_executor import SyncRunResult
from sync.knovas_uploader import SemantixUploader


def test_pointer_for_relative():
    assert _pointer_for_relative("corpus", "akten/brief.pdf") == "corpus/akten/brief.pdf"


def test_prune_removed_documents(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    # Reload the config, or SyncStateStore() keeps using the path it was
    # loaded with and this test writes into the repository's state file.
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    state = SyncStateStore()
    state.record_upload("gone.txt", "2026-01-01T00:00:00Z", 10, "key-1")
    state.record_upload("stay.txt", "2026-01-01T00:00:00Z", 10, "key-2")

    uploader = MagicMock(spec=SemantixUploader)
    uploader.delete_by_pointer.return_value = (True, None)

    result = SyncRunResult()
    sync_body = {"ingestion": {"identifier_prefix": "rc-sync", "delete_on_remove": True}}
    _prune_removed_documents(sync_body, uploader, state, {"stay.txt"}, result)

    uploader.delete_by_pointer.assert_called_once_with("rc-sync/gone.txt")
    assert "gone.txt" not in state.list_tracked_paths()
    assert "stay.txt" in state.list_tracked_paths()
    state.close()


# --- A scan that could not read something proves nothing about what is below it.


class _Listing(list):
    """What ``with os.scandir(...) as it`` needs."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _BrokenEntry:
    """A DirEntry whose ``broken`` method fails, as on a share that drops mid-listing."""

    def __init__(self, entry, broken):
        self._entry = entry
        self._broken = broken

    def __getattr__(self, name):
        if name == self._broken:

            def fail(*args, **kwargs):
                raise OSError(5, "Input/output error")

            return fail
        return getattr(self._entry, name)


def _deny_listing(monkeypatch, *denied):
    real = os.scandir
    names = {str(p) for p in denied}

    def scandir(path="."):
        if str(path) in names:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)


def _break_entry(monkeypatch, folder, entry_name, broken):
    real = os.scandir

    def scandir(path="."):
        with real(path) as it:
            entries = list(it)
        if str(path) == str(folder):
            entries = [
                _BrokenEntry(e, broken) if e.name == entry_name else e for e in entries
            ]
        return _Listing(entries)

    monkeypatch.setattr(os, "scandir", scandir)


def _run_cycle(tmp_path, monkeypatch, sources, tracked):
    """One real cycle over real folders. Returns (result, deleted pointers, tracked paths)."""
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(tmp_path / "state.json"))
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    state = SyncStateStore()
    for rel in tracked:
        state.record_upload(rel, "2026-01-01T00:00:00Z", 10, f"key-{rel}")
    state.close()

    body = {
        "mode": "incremental",
        "sources": [{"path": str(s), "recursive": True} for s in sources],
        "filters": {"include_globs": ["**/*.md", "*.md"]},
        "ingestion": {"identifier_prefix": "rc"},
    }
    uploader = SemantixUploader()
    with patch.object(uploader, "_request") as req:
        ok = MagicMock()
        ok.status_code = 200
        ok.content = b'{"key": "tx-1"}'
        ok.json.return_value = {"key": "tx-1"}
        req.return_value = ok
        result = run_sync_work(body, uploader)
    deleted = [
        c.kwargs["json_body"]["pointer"] for c in req.call_args_list if c.args[0] == "DELETE"
    ]
    state = SyncStateStore()
    remaining = set(state.list_tracked_paths())
    state.close()
    return result, deleted, remaining


def _kept_errors(result):
    return [e for e in result.errors if "could not be read" in e["error"]]


def test_unreadable_folder_keeps_its_documents_and_the_rest_is_pruned(
    tmp_watch_root, tmp_path, monkeypatch
):
    locked = tmp_watch_root / "locked"
    locked.mkdir()
    _deny_listing(monkeypatch, locked)

    result, deleted, remaining = _run_cycle(
        tmp_path, monkeypatch, [tmp_watch_root], ["locked/deep/inside.md", "removed.md"]
    )

    assert deleted == ["rc/removed.md"]
    assert "locked/deep/inside.md" in remaining
    assert "removed.md" not in remaining
    assert len(_kept_errors(result)) == 1
    assert _kept_errors(result)[0]["error"].startswith("1 document")


def test_a_folder_with_a_similar_name_is_not_protected(tmp_watch_root, tmp_path, monkeypatch):
    locked = tmp_watch_root / "locked"
    locked.mkdir()
    _deny_listing(monkeypatch, locked)

    _, deleted, _ = _run_cycle(tmp_path, monkeypatch, [tmp_watch_root], ["locked2/gone.md"])

    assert deleted == ["rc/locked2/gone.md"]


def test_unreadable_folder_without_documents_changes_nothing(
    tmp_watch_root, tmp_path, monkeypatch
):
    # The usual case on a file server: "System Volume Information" and folders
    # another department locked. They must not stop deletes for the whole share.
    locked = tmp_watch_root / "System Volume Information"
    locked.mkdir()
    _deny_listing(monkeypatch, locked)

    result, deleted, _ = _run_cycle(tmp_path, monkeypatch, [tmp_watch_root], ["removed.md"])

    assert deleted == ["rc/removed.md"]
    assert _kept_errors(result) == []


def test_unreadable_source_keeps_every_document(tmp_watch_root, tmp_path, monkeypatch):
    # Paths are relative to their source, so a document cannot be told apart by
    # its path: one source that cannot be read leaves all of them unproven.
    good = tmp_watch_root / "good"
    good.mkdir()
    (good / "still.md").write_text("x", encoding="utf-8")
    bad = tmp_watch_root / "bad"
    bad.mkdir()
    _deny_listing(monkeypatch, bad)

    result, deleted, remaining = _run_cycle(
        tmp_path, monkeypatch, [good, bad], ["from-the-bad-source.md"]
    )

    assert deleted == []
    assert "from-the-bad-source.md" in remaining
    assert len(_kept_errors(result)) == 1


@pytest.mark.parametrize("broken", ["is_dir", "is_file", "stat"])
def test_a_file_the_scan_cannot_inspect_stays(tmp_watch_root, tmp_path, monkeypatch, broken):
    (tmp_watch_root / "flaky.md").write_text("x", encoding="utf-8")
    _break_entry(monkeypatch, tmp_watch_root, "flaky.md", broken)

    result, deleted, remaining = _run_cycle(
        tmp_path, monkeypatch, [tmp_watch_root], ["flaky.md"]
    )

    assert deleted == []
    assert "flaky.md" in remaining
    assert len(_kept_errors(result)) == 1


def test_a_folder_the_scan_cannot_inspect_keeps_what_is_below(
    tmp_watch_root, tmp_path, monkeypatch
):
    sub = tmp_watch_root / "sub"
    sub.mkdir()
    _break_entry(monkeypatch, tmp_watch_root, "sub", "is_dir")

    _, deleted, remaining = _run_cycle(tmp_path, monkeypatch, [tmp_watch_root], ["sub/inner.md"])

    assert deleted == []
    assert "sub/inner.md" in remaining
