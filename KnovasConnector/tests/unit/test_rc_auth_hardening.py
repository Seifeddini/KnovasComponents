"""Regression tests for confirmed RC auth/hardening issues (B3, B4, C6, C7, L5, L6, INFO).

Each test pins the SECURE behavior; run against the un-fixed code they go red for
the specific vulnerability, and green once the minimal fix lands.
"""
import base64
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, jsonify

from tests.helpers import TEST_EMPLOYEE_ID

_REQUIRED_ENV = (
    "KNOVAS_INTERNAL_API_URL",
    "RC_INSTANCE_TOKEN",
    "RC_CLIENT_ID",
    "RC_WATCH_ROOTS",
    "SEMANTIX_SECURE_BASE_URL",
    "SEMANTIX_CLIENT_CERT_PATH",
    "SEMANTIX_CLIENT_KEY_PATH",
    "SEMANTIX_CA_CERT_PATH",
)


def _jwt_no_jti(employee_id: str, nonce: str) -> str:
    """Build a syntactically valid JWT with a UUID identity claim and NO jti."""
    header = base64.urlsafe_b64encode(b"{}").decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": employee_id, "nonce": nonce}).encode()
    ).decode().rstrip("=")
    return f"{header}.{payload}.sig"


# ---------------------------------------------------------------------------
# B3 — verify-operator cache must key on the full token, not (employee_id, jti)
# ---------------------------------------------------------------------------

def test_verify_cache_not_shared_across_distinct_tokens_without_jti():
    import auth.knovas_verify_client as kvc

    kvc._cache.clear()
    emp = TEST_EMPLOYEE_ID
    token_a = _jwt_no_jti(emp, "aaa")
    token_b = _jwt_no_jti(emp, "bbb")
    assert token_a != token_b

    resp = MagicMock()
    resp.status_code = 200
    resp.content = b'{"authorized": true, "client_id": "cid"}'
    resp.json.return_value = {"authorized": True, "client_id": "cid"}

    with patch("auth.knovas_verify_client.requests.post", return_value=resp) as mock_post:
        client = kvc.KnovasVerifyClient()
        ok_a, cid_a, _ = client.verify_operator(token_a, emp)
        ok_b, cid_b, _ = client.verify_operator(token_b, emp)

    assert ok_a is True and ok_b is True
    # A different token (same employee_id, no jti) must NOT hit A's cache entry.
    assert mock_post.call_count == 2


# ---------------------------------------------------------------------------
# B4 — local bypass must be gated on a loopback remote_addr, in-app
# ---------------------------------------------------------------------------

def _make_bypass_app() -> Flask:
    from auth.knovas_verify_client import require_internal_access

    app = Flask(__name__)

    @app.route("/protected", methods=["GET"])
    @require_internal_access
    def protected():
        return jsonify({"ok": True}), 200

    return app


def test_local_bypass_rejects_non_loopback_remote_addr():
    from config import load_config, reset_config

    os.environ["RC_INTERNAL_LOCAL_BYPASS"] = "true"
    reset_config()
    load_config(validate=False, force_reload=True)
    try:
        client = _make_bypass_app().test_client()

        far = client.get("/protected", environ_base={"REMOTE_ADDR": "10.0.0.5"})
        assert far.status_code == 403

        local = client.get("/protected", environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert local.status_code == 200
        assert local.get_json()["ok"] is True
    finally:
        os.environ.pop("RC_INTERNAL_LOCAL_BYPASS", None)
        reset_config()
        load_config(validate=False, force_reload=True)


def test_local_bypass_allows_configured_trusted_cidr():
    """RC_LOCAL_BYPASS_TRUSTED_CIDRS widens the bypass (e.g. to the Docker
    bridge gateway) while loopback stays allowed and other peers stay rejected."""
    from config import load_config, reset_config

    os.environ["RC_INTERNAL_LOCAL_BYPASS"] = "true"
    os.environ["RC_LOCAL_BYPASS_TRUSTED_CIDRS"] = "172.16.0.0/12"
    reset_config()
    load_config(validate=False, force_reload=True)
    try:
        client = _make_bypass_app().test_client()

        # Docker bridge gateway (inside the trusted CIDR) is allowed.
        gw = client.get("/protected", environ_base={"REMOTE_ADDR": "172.18.0.1"})
        assert gw.status_code == 200

        # Loopback is always allowed, regardless of config.
        local = client.get("/protected", environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert local.status_code == 200

        # Outside loopback and the trusted CIDR is still rejected.
        far = client.get("/protected", environ_base={"REMOTE_ADDR": "10.0.0.5"})
        assert far.status_code == 403
    finally:
        os.environ.pop("RC_INTERNAL_LOCAL_BYPASS", None)
        os.environ.pop("RC_LOCAL_BYPASS_TRUSTED_CIDRS", None)
        reset_config()
        load_config(validate=False, force_reload=True)


# ---------------------------------------------------------------------------
# C6 — rate limiter must ignore spoofable XFF and bound its bucket map
# ---------------------------------------------------------------------------

def test_client_ip_uses_remote_addr_not_forwarded_for():
    from auth.rc_rate_limit import _client_ip

    app = Flask(__name__)
    with app.test_request_context(
        headers={"X-Forwarded-For": "1.2.3.4"},
        environ_base={"REMOTE_ADDR": "10.0.0.9"},
    ):
        ip1 = _client_ip()
    with app.test_request_context(
        headers={"X-Forwarded-For": "9.9.9.9"},
        environ_base={"REMOTE_ADDR": "10.0.0.9"},
    ):
        ip2 = _client_ip()

    # Forged XFF must not let one peer masquerade as many buckets.
    assert ip1 == ip2 == "10.0.0.9"


def test_token_bucket_map_is_bounded():
    from util.rate_limiter import TokenBucketStrategy

    strat = TokenBucketStrategy(max_tokens=100, refill_rate=1.0, max_buckets=5)
    for i in range(500):
        strat.is_allowed(f"id-{i}")

    assert len(strat._buckets) <= 5


# ---------------------------------------------------------------------------
# C7 — state-changing POST routes must reject cross-origin requests
# ---------------------------------------------------------------------------

def test_sync_stop_rejects_foreign_origin(rc_client, auth_headers):
    with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
        mock_client.return_value.verify_operator.return_value = (True, "cid", None)
        headers = dict(auth_headers)
        headers["Origin"] = "http://evil.example"
        resp = rc_client.post("/sync/stop", json={}, headers=headers)
        assert resp.status_code == 403


def test_sync_stop_same_origin_json_passes(rc_client, auth_headers):
    with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
        mock_client.return_value.verify_operator.return_value = (True, "cid", None)
        with patch("routes.sync_control.stop_continuous", return_value="stopped"):
            headers = dict(auth_headers)
            headers["Origin"] = "http://localhost"
            resp = rc_client.post("/sync/stop", json={}, headers=headers)
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# L5 — /discover?max_depth=<non-int> must be a clean 400, not a 500 crash
# ---------------------------------------------------------------------------

def test_discover_non_integer_max_depth_returns_400(rc_client, auth_headers):
    with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
        mock_client.return_value.verify_operator.return_value = (True, "cid", None)
        resp = rc_client.get("/discover?max_depth=abc", headers=auth_headers)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# L6 — RC_SKIP_CONFIG_VALIDATION must not suppress validation outside TESTING
# ---------------------------------------------------------------------------

def test_skip_config_validation_not_honored_without_testing(monkeypatch):
    from config import load_config, reset_config

    monkeypatch.setenv("RC_SKIP_CONFIG_VALIDATION", "true")
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("RC_INTERNAL_LOCAL_BYPASS", raising=False)
    monkeypatch.delenv("RC_DISCOVER_LOCAL_BYPASS", raising=False)
    for key in _REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    reset_config()
    try:
        with pytest.raises(SystemExit) as exc:
            load_config(validate=True, force_reload=True)
        assert exc.value.code == 1
    finally:
        reset_config()


# ---------------------------------------------------------------------------
# INFO — unauthenticated /health must not leak absolute watch-root paths
# ---------------------------------------------------------------------------

def test_health_does_not_leak_watch_root_paths(rc_client):
    from config import get_config

    roots = get_config().rc_watch_roots
    assert roots  # fixture configures at least one root

    resp = rc_client.get("/health")
    assert resp.status_code == 200
    raw = resp.get_data(as_text=True)
    for root in roots:
        assert root not in raw


def test_two_platform_principals_from_one_ip_get_separate_buckets(monkeypatch):
    """M3: the handled limiter keyed on rc_employee_id or the peer address,
    and the platform path sets neither -- so every console user in the firm
    shared one 10-token bucket keyed on the docbridge-web container, and a
    preview of twelve folders spent it."""
    import flask

    import auth.rc_rate_limit as rl
    from auth.platform_principal import PlatformPrincipal

    monkeypatch.setenv("RC_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RC_RATE_LIMIT_HANDLED_MAX_TOKENS", "2")
    monkeypatch.setenv("RC_RATE_LIMIT_HANDLED_REFILL_PER_SEC", "0.0001")
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    rl._handled_limiter = None

    app = flask.Flask(__name__)

    @rl.require_rc_handled_rate_limit
    def handler():
        return "ok"

    def _as(subject):
        with app.test_request_context("/sync/status", environ_base={"REMOTE_ADDR": "10.0.0.9"}):
            flask.g.rc_principal = PlatformPrincipal(
                subject=subject, tenant="t", groups=(), roles=("admin",),
                jti="j", expires_at=0)
            out = handler()
            return out if isinstance(out, str) else out[1]

    assert _as("user-a") == "ok"
    assert _as("user-a") == "ok"
    assert _as("user-a") == 429, "the third call spends user-a's bucket"
    assert _as("user-b") == "ok", "a second person from the same IP has their own"
    rl._handled_limiter = None
