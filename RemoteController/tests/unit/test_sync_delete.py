from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sync.sync_executor import _prune_removed_documents, _pointer_for_relative
from sync.sync_state import SyncStateStore
from sync.sync_executor import SyncRunResult
from sync.knovas_uploader import SemantixUploader


def test_pointer_for_relative():
    assert _pointer_for_relative("corpus", "akten/brief.pdf") == "corpus/akten/brief.pdf"


def test_prune_removed_documents(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
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
