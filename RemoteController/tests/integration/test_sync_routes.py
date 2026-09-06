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
    with patch("routes.sync.run_one_time", return_value=("completed", object())), \
         patch("routes.sync.start_continuous", return_value="running"):
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


class TestSyncBody:
    """C2: POST /sync/body stores the folder list without running anything.

    POST /sync could not be used for a push: with a continuous worker already
    running it answers "already_running" and changes nothing, and with the
    scheduler idle and mode one_time it runs a full scan inside the request.
    The Platform stores the body here and decides separately whether to start.
    """

    @pytest.fixture
    def as_employee(self, monkeypatch):
        monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "false")
        from config import load_config, reset_config

        reset_config()
        load_config(validate=False, force_reload=True)
        with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
            mock_client.return_value.verify_operator.return_value = (True, "c", None)
            yield

    def test_it_stores_the_body_and_starts_nothing(self, rc_client, auth_headers, as_employee,
                                                   tmp_path, monkeypatch):
        monkeypatch.setenv("RC_SYNC_STATE_PATH", str(tmp_path / "state" / ".rc-sync-state.json"))
        from config import load_config, reset_config

        reset_config()
        load_config(validate=False, force_reload=True)
        from sync.sync_scheduler import load_last_sync_body

        with patch("routes.sync.run_one_time") as run_once, \
             patch("routes.sync.start_continuous") as start:
            resp = rc_client.post("/sync/body", json=SYNC_BODY, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "stored"}
        assert run_once.call_count == 0 and start.call_count == 0
        assert load_last_sync_body() == SYNC_BODY

    def test_a_body_the_schema_refuses_is_a_400(self, rc_client, auth_headers, as_employee):
        resp = rc_client.post("/sync/body", json={"mode": "incremental"}, headers=auth_headers)
        assert resp.status_code == 400

    def test_a_non_object_body_is_a_400(self, rc_client, auth_headers, as_employee):
        resp = rc_client.post("/sync/body", json=["not", "an", "object"], headers=auth_headers)
        assert resp.status_code == 400

    def test_it_is_gated_exactly_like_sync(self, rc_client):
        assert rc_client.post("/sync/body", json=SYNC_BODY).status_code in (401, 403, 429)

    def test_start_with_an_empty_body_uses_the_stored_one(self, rc_client, auth_headers,
                                                          as_employee, tmp_path, monkeypatch):
        """D1: the Platform's start() posts {} after storing the body via
        /sync/body. An empty object is "no body given", not a body."""
        monkeypatch.setenv("RC_SYNC_STATE_PATH", str(tmp_path / "state" / ".rc-sync-state.json"))
        from config import load_config, reset_config

        reset_config()
        load_config(validate=False, force_reload=True)
        assert rc_client.post("/sync/body", json=SYNC_BODY, headers=auth_headers).status_code == 200
        with patch("routes.sync_control.start_continuous", return_value="running") as start:
            resp = rc_client.post("/sync/start", json={}, headers=auth_headers)
        assert resp.status_code == 200, resp.get_json()
        assert start.call_count == 1
        assert start.call_args[0][0].sync_body == SYNC_BODY

    def test_start_without_any_stored_body_is_still_a_400(self, rc_client, auth_headers,
                                                          as_employee, tmp_path, monkeypatch):
        monkeypatch.setenv("RC_SYNC_STATE_PATH", str(tmp_path / "state" / ".rc-sync-state.json"))
        from config import load_config, reset_config

        reset_config()
        load_config(validate=False, force_reload=True)
        with patch("routes.sync_control.start_continuous") as start:
            resp = rc_client.post("/sync/start", json={}, headers=auth_headers)
        assert resp.status_code == 400
        assert start.call_count == 0


def test_sync_body_accepts_the_platform_principal_like_sync(rc_client, tmp_path, monkeypatch):
    """The seventh route the Platform console reaches; same decorator as /sync."""
    pytest.importorskip("cryptography")
    from tests.test_platform_principal import TENANT, mint
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private = ed25519.Ed25519PrivateKey.generate()
    pub = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pem = tmp_path / "broker_ed25519.pub"
    pem.write_bytes(pub)
    monkeypatch.setenv("RC_PLATFORM_BROKER_PUBKEY_PATH", str(pem))
    monkeypatch.setenv("RC_CLIENT_ID", TENANT)
    monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "false")
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(tmp_path / "state" / ".rc-sync-state.json"))
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)

    resp = rc_client.post("/sync/body", json=SYNC_BODY,
                          headers={"X-Platform-Principal": mint(private, pub)})
    assert resp.status_code == 200
    resp = rc_client.post("/sync/body", json=SYNC_BODY,
                          headers={"X-Platform-Principal": mint(private, pub, rol=["member"])})
    assert resp.status_code == 403
