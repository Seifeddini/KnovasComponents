from unittest.mock import patch

import pytest


def test_discover_auth_required(rc_client):
    resp = rc_client.get("/discover")
    assert resp.status_code in (401, 403, 429)


def test_discover_local_bypass_no_auth(rc_client, monkeypatch):
    monkeypatch.setenv("RC_DISCOVER_LOCAL_BYPASS", "true")
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    resp = rc_client.get("/discover")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "success"


def test_discover_valid_200(rc_client, auth_headers):
    with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
        mock_client.return_value.verify_operator.return_value = (True, "client", None)
        resp = rc_client.get("/discover", headers=auth_headers)
        assert resp.status_code == 200
