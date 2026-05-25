from unittest.mock import patch

import pytest

SYNC_BODY = {
    "mode": "incremental",
    "sources": [{"path": ".", "recursive": True}],
    "filters": {"include_globs": ["**/*.md"], "exclude_globs": []},
    "ingestion": {"identifier_prefix": "rc-sync", "part_max_chars": 50000},
}


def test_sync_auth_required(rc_client):
    resp = rc_client.post("/sync", json=SYNC_BODY)
    assert resp.status_code in (401, 403, 429)


def test_sync_internal_bypass_no_auth(rc_client, monkeypatch):
    monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "true")
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    with patch("sync.sync_scheduler.run_one_time", return_value=("completed", object())):
        resp = rc_client.post("/sync", json=SYNC_BODY)
    assert resp.status_code != 401


def test_sync_config_api_disabled(rc_client, auth_headers):
    with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
        mock_client.return_value.verify_operator.return_value = (True, "c", None)
        resp = rc_client.get("/sync/config", headers=auth_headers)
        assert resp.status_code == 404


def test_sync_status(rc_client, auth_headers):
    with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
        mock_client.return_value.verify_operator.return_value = (True, "c", None)
        resp = rc_client.get("/sync/status", headers=auth_headers)
        assert resp.status_code == 200
