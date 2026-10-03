"""Concurrent writes through the Connector's routes (spec E6).

The image runs gunicorn's gthread worker with four request threads, so
POST /sync/config, /sync/body, /sync/start and /sync/stop can run at the
same time. What they write must come out as one complete document, and a
GET that seeds a missing config file must never undo a POST.
"""
from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest

SYNC_BODY = {
    "mode": "incremental",
    "sources": [{"path": ".", "recursive": True}],
    "filters": {"include_globs": ["**/*.md"], "exclude_globs": []},
    "ingestion": {"identifier_prefix": "rc-sync", "part_max_chars": 50000},
}


def _config(interval: int) -> dict:
    return {
        "schema_version": 1,
        "enabled": True,
        "mode": "continuous",
        "window": {"start_local": "00:00", "end_local": "23:59"},
        "rate_limit": {"max_ingestion_requests_per_minute": 30, "burst": 5},
        "scan_interval_seconds": interval,
        "pause_policy": "finish_current_unit_then_pause",
    }


@pytest.fixture
def app_and_config_path(tmp_watch_root, tmp_path, monkeypatch):
    config_path = tmp_path / "config" / "sync.json"
    monkeypatch.setenv("RC_SYNC_CONFIG_API_ENABLED", "true")
    monkeypatch.setenv("RC_SYNC_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(tmp_path / "state" / ".rc-sync-state.json"))
    monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "false")
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    from app import create_app

    application = create_app(skip_validation=True)
    application.config["TESTING"] = True
    with patch("auth.knovas_verify_client.get_verify_client") as verify:
        verify.return_value.verify_operator.return_value = (True, "c", None)
        yield application, config_path


def _run_together(*targets) -> None:
    threads = [threading.Thread(target=target) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)


def test_a_get_that_seeds_the_config_never_overwrites_a_concurrent_post(
    app_and_config_path, auth_headers, monkeypatch
):
    """GET /sync/config writes a default file when there is none. It checked,
    then wrote: a POST landing in between was overwritten by the default and
    the console's "Speichern und uebertragen" was lost."""
    import sync.sync_config as sync_config

    application, config_path = app_and_config_path
    seeding, posted = threading.Event(), threading.Event()
    real_seed = sync_config.seed_from_env

    def slow_seed():
        seeding.set()
        posted.wait(timeout=2)  # with the lock the POST cannot finish in between
        return real_seed()

    monkeypatch.setattr(sync_config, "seed_from_env", slow_seed)
    stored = _config(600)
    statuses: dict = {}

    def get_config_doc():
        with application.test_client() as client:
            statuses["get"] = client.get("/sync/config", headers=auth_headers).status_code

    def post_config_doc():
        seeding.wait(timeout=5)
        with application.test_client() as client:
            statuses["post"] = client.post("/sync/config", json=stored, headers=auth_headers).status_code
        posted.set()

    _run_together(get_config_doc, post_config_doc)
    assert statuses == {"get": 200, "post": 200}
    assert json.loads(config_path.read_text(encoding="utf-8")) == stored, "the posted config survives"


def test_two_concurrent_config_writes_leave_one_complete_document(app_and_config_path, auth_headers):
    application, config_path = app_and_config_path
    bodies = [_config(60), _config(900)]
    barrier = threading.Barrier(len(bodies))
    statuses: list = []

    def post(body):
        def run():
            with application.test_client() as client:
                barrier.wait(timeout=5)
                statuses.append(client.post("/sync/config", json=body, headers=auth_headers).status_code)
        return run

    _run_together(*(post(body) for body in bodies))
    assert statuses == [200, 200]
    assert json.loads(config_path.read_text(encoding="utf-8")) in bodies, "one complete document, never a mix"
    assert not list(config_path.parent.glob("*.tmp")), "no temporary file is left behind"


def test_two_concurrent_body_writes_leave_one_complete_body(app_and_config_path, auth_headers):
    from sync.sync_scheduler import load_last_sync_body

    application, _config_path = app_and_config_path
    bodies = [
        {**SYNC_BODY, "ingestion": {**SYNC_BODY["ingestion"], "identifier_prefix": prefix}}
        for prefix in ("alpha", "beta")
    ]
    barrier = threading.Barrier(len(bodies))
    statuses: list = []

    def post(body):
        def run():
            with application.test_client() as client:
                barrier.wait(timeout=5)
                statuses.append(client.post("/sync/body", json=body, headers=auth_headers).status_code)
        return run

    _run_together(*(post(body) for body in bodies))
    assert statuses == [200, 200]
    assert load_last_sync_body() in bodies
