from sync.sync_executor import build_walk_targets, plan_sync_cycle
from sync.subfolder_queue import SubfolderQueue
from sync.sync_state import SyncStateStore


def test_build_walk_targets_sequential(tmp_path, monkeypatch):
    root = tmp_path / "winjur"
    (root / "000000-000500").mkdir(parents=True)
    (root / "000501-001000").mkdir()

    monkeypatch.setenv("RC_WATCH_ROOTS", str(root))
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)

    body = {
        "mode": "incremental",
        "sources": [{"path": str(root), "recursive": True}],
        "ingestion": {"identifier_prefix": "winjur"},
    }
    sync_cfg = {"sequential_subfolders": True}
    queue = SubfolderQueue.from_config()
    try:
        targets, progress = build_walk_targets(body, sync_cfg, queue)
        assert progress is not None
        assert progress.current_subfolder == "000000-000500"
        assert len(targets) == 1
        assert targets[0].walk_root == root / "000000-000500"
        assert targets[0].rel_root == root
    finally:
        queue.close()


def test_plan_sync_cycle_respects_dir_visit_limit(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("RC_WATCH_ROOTS", str(root))
    (root / "nested").mkdir()
    (root / "nested" / "inside.md").write_text("x", encoding="utf-8")

    state_path = tmp_path / "state.json"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)

    body = {
        "mode": "incremental",
        "sources": [{"path": str(root), "recursive": True}],
        "ingestion": {"identifier_prefix": "rc"},
    }
    store = SyncStateStore(str(state_path))
    try:
        plan = plan_sync_cycle(body, store, max_scan_entries=1)
        assert plan.summary.total == 0
        assert plan.scan_truncated is True
    finally:
        store.close()
