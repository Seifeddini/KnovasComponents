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


def _continuous_config(interval):
    return {
        "schema_version": 1,
        "enabled": True,
        "mode": "continuous",
        "window": {"start_local": "00:00", "end_local": "23:59"},
        "rate_limit": {"max_ingestion_requests_per_minute": 30, "burst": 5},
        "scan_interval_seconds": interval,
        "pause_policy": "finish_current_unit_then_pause",
    }


def _body(prefix):
    return {"mode": "incremental", "sources": [{"path": "."}], "filters": {},
            "ingestion": {"identifier_prefix": prefix}}


def test_the_running_worker_picks_up_a_body_and_config_saved_while_it_runs(tmp_path, monkeypatch):
    """C2: the Platform writes a new folder list and schedule into a
    RemoteController whose continuous worker is already running. Before this,
    the worker looped on the context it was started with for ever, so the
    console reported "uebertragen" while RC kept indexing the old folders
    behind the old walls."""
    import time

    from config import load_config, reset_config
    from sync import sync_scheduler
    from sync.sync_config import save_sync_config

    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(tmp_path / "state" / ".rc-sync-state.json"))
    monkeypatch.setenv("RC_SYNC_CONFIG_PATH", str(tmp_path / "config" / "sync.json"))
    reset_config()
    load_config(validate=False, force_reload=True)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    save_sync_config(_continuous_config(60))
    save_last_sync_body(_body("alpha"))
    started_with = SyncRunContext(sync_body=_body("alpha"), sync_config=_continuous_config(60))

    seen = []

    def _fake_run_once(ctx):
        from sync.sync_executor import SyncRunResult

        seen.append(ctx)
        if len(seen) == 1:
            save_sync_config(_continuous_config(600))
            save_last_sync_body(_body("beta"))
        else:
            sync_scheduler._stop_event.set()
        return SyncRunResult()

    intervals = []
    real_interval = sync_scheduler._effective_scan_interval_seconds

    def _record_interval(cfg_doc, result):
        value = real_interval(cfg_doc, result)
        intervals.append(value)
        return value

    monkeypatch.setattr(sync_scheduler, "_run_once", _fake_run_once)
    monkeypatch.setattr(sync_scheduler, "_effective_scan_interval_seconds", _record_interval)
    sync_scheduler._stop_event.clear()
    try:
        sync_scheduler._continuous_worker(started_with)
    finally:
        sync_scheduler._stop_event.clear()

    assert len(seen) == 2
    assert seen[0].sync_body["ingestion"]["identifier_prefix"] == "alpha"
    assert seen[1].sync_body["ingestion"]["identifier_prefix"] == "beta", \
        "the second cycle must run the body saved while the worker ran"
    assert seen[1].sync_config["scan_interval_seconds"] == 600
    assert intervals == [60, 600], (
        "cycle 1 still runs on the old interval; the reloaded config drives cycle 2")


def test_the_worker_keeps_its_own_context_when_nothing_is_saved(tmp_path, monkeypatch):
    """A reload that finds nothing must not blank the running context."""
    import time

    from config import load_config, reset_config
    from sync import sync_scheduler

    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(tmp_path / "state" / ".rc-sync-state.json"))
    monkeypatch.setenv("RC_SYNC_CONFIG_PATH", str(tmp_path / "config" / "sync.json"))
    reset_config()
    load_config(validate=False, force_reload=True)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    started_with = SyncRunContext(sync_body=_body("alpha"), sync_config=_continuous_config(60))
    seen = []

    def _fake_run_once(ctx):
        from sync.sync_executor import SyncRunResult

        seen.append(ctx)
        sync_scheduler._stop_event.set()
        return SyncRunResult()

    monkeypatch.setattr(sync_scheduler, "_run_once", _fake_run_once)
    monkeypatch.setattr(sync_scheduler, "load_last_sync_body", lambda: None)
    sync_scheduler._stop_event.clear()
    try:
        sync_scheduler._continuous_worker(started_with)
    finally:
        sync_scheduler._stop_event.clear()

    assert seen == [started_with]


def test_the_worker_stops_after_a_cycle_when_the_reloaded_config_is_one_time(tmp_path, monkeypatch):
    """D2: a one_time config (the console's "manual" preset) saved while a
    continuous worker runs must end the loop after the cycle that ran it;
    otherwise RC keeps indexing while the console says the run is started by hand."""
    import time

    from config import load_config, reset_config
    from sync import sync_scheduler
    from sync.sync_config import save_sync_config

    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(tmp_path / "state" / ".rc-sync-state.json"))
    monkeypatch.setenv("RC_SYNC_CONFIG_PATH", str(tmp_path / "config" / "sync.json"))
    reset_config()
    load_config(validate=False, force_reload=True)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    save_sync_config(_continuous_config(60))
    save_last_sync_body(_body("alpha"))
    started_with = SyncRunContext(sync_body=_body("alpha"), sync_config=_continuous_config(60))

    seen = []

    def _fake_run_once(ctx):
        from sync.sync_executor import SyncRunResult

        seen.append(ctx)
        if len(seen) == 1:
            one_time = dict(_continuous_config(60), mode="one_time")
            save_sync_config(one_time)
            save_last_sync_body(_body("beta"))
        elif len(seen) >= 3:
            sync_scheduler._stop_event.set()  # safety net: the loop should have ended already
        return SyncRunResult()

    monkeypatch.setattr(sync_scheduler, "_run_once", _fake_run_once)
    sync_scheduler._stop_event.clear()
    try:
        sync_scheduler._continuous_worker(started_with)
    finally:
        sync_scheduler._stop_event.clear()

    assert len(seen) == 2, "cycle 2 ran the one_time config and the worker then stopped on its own"
    assert seen[1].sync_config["mode"] == "one_time"
    assert seen[1].sync_body["ingestion"]["identifier_prefix"] == "beta"
