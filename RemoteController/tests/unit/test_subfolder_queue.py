from sync.subfolder_queue import SubfolderQueue


def test_sequential_subfolder_advance(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    (root / "alpha").mkdir(parents=True)
    (root / "beta").mkdir()
    (root / "alpha" / "a.md").write_text("a", encoding="utf-8")
    (root / "beta" / "b.md").write_text("b", encoding="utf-8")

    state_path = tmp_path / "state.json"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)

    queue = SubfolderQueue.from_config()
    try:
        prog = queue.progress(root)
        assert prog.total_subfolders == 2
        assert prog.current_subfolder == "alpha"
        assert queue.current_path(root) == root / "alpha"

        queue.maybe_advance(root, pending=0, modified=0, scan_truncated=False, paused_reason=None)
        prog2 = queue.progress(root)
        assert prog2.current_subfolder == "beta"
    finally:
        queue.close()


def test_sequential_subfolder_stays_on_scan_truncated(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    (root / "alpha").mkdir(parents=True)
    (root / "beta").mkdir()

    state_path = tmp_path / "state.json"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)

    queue = SubfolderQueue.from_config()
    try:
        queue.refresh(root)
        queue.maybe_advance(
            root, pending=1, modified=0, scan_truncated=True, paused_reason="scan_limit_reached"
        )
        assert queue.progress(root).current_subfolder == "alpha"
    finally:
        queue.close()
