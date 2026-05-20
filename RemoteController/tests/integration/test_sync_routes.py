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


def test_sync_config_api_disabled(rc_client):
    with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
        mock_client.return_value.verify_operator.return_value = (True, "c", None)
        resp = rc_client.get("/sync/config", headers={"Authorization": "Bearer jwt"})
        assert resp.status_code == 404


def test_sync_status(rc_client):
    with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
        mock_client.return_value.verify_operator.return_value = (True, "c", None)
        resp = rc_client.get("/sync/status", headers={"Authorization": "Bearer jwt"})
        assert resp.status_code == 200
