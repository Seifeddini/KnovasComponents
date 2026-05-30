from sync.sync_executor import plan_sync_cycle
from sync.subfolder_queue import SubfolderQueue
from sync.sync_state import SyncStateStore


def test_scan_resumes_after_dir_visit_cap(tmp_path, monkeypatch):
    root = tmp_path / "winjur"
    bucket = root / "000501-001000"
    bucket.mkdir(parents=True)
    deep = bucket
    for i in range(12):
        deep = deep / f"dir{i:02d}"
        deep.mkdir()
    (deep / "late.md").write_text("found", encoding="utf-8")

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
    sync_cfg = {"sequential_subfolders": True, "max_scan_entries_per_cycle": 5}
    store = SyncStateStore(str(state_path))
    queue = SubfolderQueue.from_config()
    try:
        plan1 = plan_sync_cycle(
            body, store, sync_config=sync_cfg, max_scan_entries=5, queue=queue
        )
        assert plan1.scan_truncated is True
        assert plan1.summary.total == 0
        stack = queue.load_scan_stack(root)
        assert stack

        found = False
        for _ in range(5):
            plan = plan_sync_cycle(
                body, store, sync_config=sync_cfg, max_scan_entries=5, queue=queue
            )
            if plan.summary.total >= 1:
                found = True
                assert plan.summary.pending == 1
                break
        assert found
    finally:
        queue.close()
        store.close()
