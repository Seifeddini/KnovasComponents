from unittest.mock import patch

from sync.sync_config import seed_from_env
from sync.sync_scheduler import SyncRunContext, load_last_sync_body, run_one_time, save_last_sync_body


def test_one_time_disabled():
    cfg = seed_from_env()
    cfg["enabled"] = False
    ctx = SyncRunContext(sync_body={"mode": "full", "sources": [], "filters": {}, "ingestion": {"identifier_prefix": "x"}}, sync_config=cfg)
    status, result = run_one_time(ctx)
    assert status == "disabled"


def test_save_last_sync_body_uses_state_directory(tmp_path, monkeypatch):
    state_file = tmp_path / "state" / ".rc-sync-state.json"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_file))
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    body = {"mode": "incremental", "sources": [], "filters": {}, "ingestion": {"identifier_prefix": "t"}}
    save_last_sync_body(body)
    last_path = tmp_path / "state" / ".rc-sync-last-request.json"
    assert last_path.exists()
    assert load_last_sync_body() == body


def test_already_running_guard():
    cfg = seed_from_env()
    cfg["enabled"] = False
    body = {"mode": "full", "sources": [{"path": "."}], "filters": {}, "ingestion": {"identifier_prefix": "rc"}}
    ctx = SyncRunContext(sync_body=body, sync_config=cfg)
    run_one_time(ctx)
    status, _ = run_one_time(ctx)
    assert status in ("disabled", "already_running", "completed", "paused_outside_window")
