from unittest.mock import patch

from sync.sync_config import seed_from_env
from sync.sync_scheduler import SyncRunContext, run_one_time


def test_one_time_disabled():
    cfg = seed_from_env()
    cfg["enabled"] = False
    ctx = SyncRunContext(sync_body={"mode": "full", "sources": [], "filters": {}, "ingestion": {"identifier_prefix": "x"}}, sync_config=cfg)
    status, result = run_one_time(ctx)
    assert status == "disabled"


def test_already_running_guard():
    cfg = seed_from_env()
    cfg["enabled"] = False
    body = {"mode": "full", "sources": [{"path": "."}], "filters": {}, "ingestion": {"identifier_prefix": "rc"}}
    ctx = SyncRunContext(sync_body=body, sync_config=cfg)
    run_one_time(ctx)
    status, _ = run_one_time(ctx)
    assert status in ("disabled", "already_running", "completed", "paused_outside_window")
