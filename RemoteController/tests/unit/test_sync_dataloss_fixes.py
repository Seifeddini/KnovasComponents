"""Regression tests for confirmed data-loss bugs in the sequential sync engine.

Each test reproduces a specific bug (A2/A1/A4/A3/A8) that could silently drop or
endlessly re-upload documents. See the accompanying fixes in
sync/sync_executor.py, sync/subfolder_queue.py and sync/knovas_uploader.py.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from sync.knovas_uploader import SemantixUploader, UploadResult
from sync.subfolder_queue import SubfolderQueue
from sync.sync_executor import (
    _should_skip_failed_upload,
    plan_sync_cycle,
    run_sync_work,
)
from sync.sync_state import SyncStateStore


def _reload_config(monkeypatch, *, watch_root, state_path):
    monkeypatch.setenv("RC_WATCH_ROOTS", str(watch_root))
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)


def _sequential_body(root):
    return {
        "mode": "incremental",
        "sources": [{"path": str(root), "recursive": True}],
        "ingestion": {"identifier_prefix": "rc"},
    }


# --------------------------------------------------------------------------- #
# A1 - File-yield cap permanently stalls large subfolders                     #
# --------------------------------------------------------------------------- #
def test_a1_file_cap_checkpoints_and_makes_forward_progress(tmp_path, monkeypatch):
    """A subfolder with more than max_scan_entries files must not re-scan the
    same prefix forever: the resume checkpoint must advance past what was
    already scanned so files beyond the cap are eventually discovered."""
    CAP = 5
    root = tmp_path / "winjur"
    bucket = root / "bucket"  # the sequential subfolder (cursor index 0)
    bucket.mkdir(parents=True)
    for i in range(CAP + 2):  # 7 files directly in bucket
        (bucket / f"f{i:03d}.md").write_text(f"doc {i}", encoding="utf-8")
    zsub = bucket / "zsub"  # files "beyond the cap" live one directory deeper
    zsub.mkdir()
    for i in range(3):
        (zsub / f"g{i:03d}.md").write_text(f"sub {i}", encoding="utf-8")

    _reload_config(monkeypatch, watch_root=root, state_path=tmp_path / "state.json")

    body = _sequential_body(root)
    body["ingestion"]["identifier_prefix"] = "winjur"
    sync_cfg = {"sequential_subfolders": True, "max_scan_entries_per_cycle": CAP}

    store = SyncStateStore(str(tmp_path / "state.json"))
    queue = SubfolderQueue.from_config()
    try:
        plan1 = plan_sync_cycle(
            body,
            store,
            sync_config=sync_cfg,
            include_documents=True,
            max_scan_entries=CAP,
            queue=queue,
        )
        assert plan1.scan_truncated is True
        stack = queue.load_scan_stack(root)
        assert stack, "a truncated scan must persist a resume checkpoint"
        # The infinite-loop bug persists [bucket] (== walk root) every cycle.
        assert stack != [bucket]
        # A whole directory is scanned atomically, so the cap never truncates
        # mid-directory and silently drops the remaining files in it.
        assert plan1.summary.total >= CAP + 2

        discovered = {d.relative_path for d in plan1.summary.documents}
        for _ in range(6):
            plan = plan_sync_cycle(
                body,
                store,
                sync_config=sync_cfg,
                include_documents=True,
                max_scan_entries=CAP,
                queue=queue,
            )
            discovered.update(d.relative_path for d in plan.summary.documents)
            if not plan.scan_truncated:
                break
        # Files beyond the cap (inside zsub) must eventually be reached.
        assert any("zsub" in p for p in discovered)
    finally:
        queue.close()
        store.close()


# --------------------------------------------------------------------------- #
# A8 - Empty transmission key must be an error, not a silent re-upload         #
# --------------------------------------------------------------------------- #
def _no_key_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b"{}"
    resp.json.return_value = {}
    return resp


def test_a8_empty_transmission_key_is_error(tmp_path):
    """A 200 init response with no key must yield status='error', never 'ok'
    with an empty key (which would never be recorded and re-uploads forever)."""
    f = tmp_path / "doc.txt"
    f.write_text("hello world", encoding="utf-8")

    uploader = SemantixUploader()
    with patch.object(uploader, "_request") as req:
        req.return_value = _no_key_response()
        result = uploader.upload_file(f, "doc.txt", {"ingestion": {"identifier_prefix": "rc"}})

    assert result.status == "error"
    assert not result.transmission_key_id


def test_a8_empty_key_document_is_not_recorded(tmp_path, monkeypatch):
    """run_sync_work must not record an empty-key upload as done, so it retries
    next cycle instead of being treated as a successful upload."""
    root = tmp_path / "archive"
    root.mkdir()
    (root / "doc.txt").write_text("hello world", encoding="utf-8")

    _reload_config(monkeypatch, watch_root=root, state_path=tmp_path / "state.json")

    body = {
        "mode": "incremental",
        "sources": [{"path": str(root), "recursive": True}],
        "filters": {"include_globs": ["doc.txt"]},
        "ingestion": {"identifier_prefix": "rc"},
    }

    uploader = SemantixUploader()
    with patch.object(uploader, "_request") as req:
        req.return_value = _no_key_response()
        result = run_sync_work(body, uploader)

    assert result.files_uploaded == 0
    assert any(e["path"] == "doc.txt" for e in result.errors)

    stat = (root / "doc.txt").stat()
    mtime_iso = (
        datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    store = SyncStateStore(str(tmp_path / "state.json"))
    try:
        # Not recorded -> incremental sync will retry it (not skipped/synced).
        assert not store.should_skip("doc.txt", mtime_iso, stat.st_size)
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# A3 - Transient read error must not permanently skip a valid document        #
# --------------------------------------------------------------------------- #
def test_a3_transient_oserror_is_not_skipped():
    """A momentary OS error (locked SMB file) must stay retryable, never be
    recorded as permanently synced/skipped."""
    upload = UploadResult(
        relative_path="akten/brief.pdf",
        transmission_key_id=None,
        parts=0,
        status="error",
        ingestion_requests=0,
        error="[Errno 13] Permission denied",
    )
    assert _should_skip_failed_upload(upload, "incremental") is False


def test_a3_generic_being_used_error_is_not_skipped():
    upload = UploadResult(
        relative_path="akten/brief.docx",
        transmission_key_id=None,
        parts=0,
        status="error",
        ingestion_requests=0,
        error="The process cannot access the file because it is being used by another process",
    )
    assert _should_skip_failed_upload(upload, "incremental") is False


def test_a3_genuinely_unconvertible_error_is_skipped():
    upload = UploadResult(
        relative_path="akten/scan.pdf",
        transmission_key_id=None,
        parts=0,
        status="error",
        ingestion_requests=1,
        error="no extractable text from .pdf file",
    )
    assert _should_skip_failed_upload(upload, "incremental") is True


# --------------------------------------------------------------------------- #
# A4 - Stop/deadline during the scan must not advance the cursor              #
# --------------------------------------------------------------------------- #
def test_a4_stop_during_scan_does_not_advance_cursor(tmp_path, monkeypatch):
    """When should_stop fires mid-scan (e.g. the max_sync_duration deadline)
    with an empty upload queue, the cursor must stay put and the resume stack
    must be preserved - the unscanned tail has not been proven empty."""
    root = tmp_path / "archive"
    current = root / "current"  # cursor index 0
    current.mkdir(parents=True)
    (current / "doc1.md").write_text("a", encoding="utf-8")
    (current / "doc2.md").write_text("b", encoding="utf-8")
    znext = root / "znext"  # index 1 - must NOT be advanced to
    znext.mkdir()
    (znext / "z.md").write_text("z", encoding="utf-8")

    _reload_config(monkeypatch, watch_root=root, state_path=tmp_path / "state.json")

    # should_stop fires on its 2nd invocation, which is the first while-loop
    # iteration inside _walk_text_files (before "current" is scanned).
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] >= 2

    uploader = MagicMock()
    result = run_sync_work(
        _sequential_body(root),
        uploader,
        should_stop=should_stop,
        sync_config={"sequential_subfolders": True},
    )

    uploader.upload_file.assert_not_called()  # empty upload queue

    queue = SubfolderQueue.from_config()
    try:
        prog = queue.progress(root)
        assert prog.current_subfolder == "current"
        assert prog.current_index == 0
        assert not prog.completed
        assert queue.load_scan_stack(root), "resume checkpoint must be preserved"
    finally:
        queue.close()


# --------------------------------------------------------------------------- #
# A2 - Empty-subfolder double-advance                                         #
# --------------------------------------------------------------------------- #
def test_a2_empty_subfolder_advances_exactly_one(tmp_path, monkeypatch):
    """Processing an empty subfolder must land the cursor on the very next
    subfolder, not skip over it."""
    root = tmp_path / "archive"
    (root / "A_empty").mkdir(parents=True)
    (root / "B_hasdocs").mkdir()
    (root / "C").mkdir()
    (root / "B_hasdocs" / "b.md").write_text("b", encoding="utf-8")
    (root / "C" / "c.md").write_text("c", encoding="utf-8")

    _reload_config(monkeypatch, watch_root=root, state_path=tmp_path / "state.json")

    uploader = MagicMock()
    run_sync_work(_sequential_body(root), uploader, sync_config={"sequential_subfolders": True})

    queue = SubfolderQueue.from_config()
    try:
        prog = queue.progress(root)
        # subfolders sorted: A_empty(0), B_hasdocs(1), C(2)
        assert prog.current_index == 1
        assert prog.current_subfolder == "B_hasdocs"
    finally:
        queue.close()


def test_a2_empty_second_to_last_does_not_falsely_complete(tmp_path, monkeypatch):
    """An empty subfolder that is second-to-last must advance to the final
    subfolder without marking the whole queue complete."""
    root = tmp_path / "archive"
    (root / "A_empty").mkdir(parents=True)
    (root / "B_last").mkdir()
    (root / "B_last" / "b.md").write_text("b", encoding="utf-8")

    _reload_config(monkeypatch, watch_root=root, state_path=tmp_path / "state.json")

    uploader = MagicMock()
    run_sync_work(_sequential_body(root), uploader, sync_config={"sequential_subfolders": True})

    queue = SubfolderQueue.from_config()
    try:
        prog = queue.progress(root)
        assert not prog.completed
        assert prog.current_subfolder == "B_last"
        assert prog.current_index == 1
    finally:
        queue.close()
