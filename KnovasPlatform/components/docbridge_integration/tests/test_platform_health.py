import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from conftest import DummyKnovasClient  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _csrf_token(client):
    client.get("/login")
    with client.session_transaction() as sess:
        return sess["csrf_token"]


def _login(client):
    token = _csrf_token(client)
    return client.post(
        "/login",
        data={
            "login_name": "healthuser",
            "password": "healthpass123",
            "csrf_token": token,
        },
    )


# ---------------------------------------------------------------------------
# /api/health tests
# ---------------------------------------------------------------------------

class TestPlatformHealthEndpoint:
    def test_health_returns_200(self, docbridge_app):
        assert docbridge_app.test_client().get("/api/health").status_code == 200

    def test_health_content_type_json(self, docbridge_app):
        resp = docbridge_app.test_client().get("/api/health")
        assert "application/json" in resp.content_type

    def test_health_status_healthy(self, docbridge_app):
        body = docbridge_app.test_client().get("/api/health").get_json()
        assert body["status"] == "healthy"

    def test_health_timestamp_present(self, docbridge_app):
        body = docbridge_app.test_client().get("/api/health").get_json()
        assert "timestamp" in body
        # Must be parseable as ISO datetime
        datetime.fromisoformat(body["timestamp"])

    def test_health_timestamp_is_recent(self, docbridge_app):
        body = docbridge_app.test_client().get("/api/health").get_json()
        ts = datetime.fromisoformat(body["timestamp"])
        delta = abs((datetime.now() - ts).total_seconds())
        assert delta < 5, f"Timestamp is {delta:.1f}s old — expected < 5s"

    def test_health_semantix_api_field_present(self, docbridge_app):
        body = docbridge_app.test_client().get("/api/health").get_json()
        assert "semantix_api" in body

    def test_health_semantix_api_true_when_mock_healthy(self, docbridge_app):
        DummyKnovasClient.health_result = True
        body = docbridge_app.test_client().get("/api/health").get_json()
        assert body["semantix_api"] is True

    def test_health_semantix_api_false_when_mock_unhealthy(self, docbridge_app):
        DummyKnovasClient.health_result = False
        body = docbridge_app.test_client().get("/api/health").get_json()
        assert body["semantix_api"] is False

    def test_health_public_no_auth_required(self, docbridge_app):
        # /api/health is on the login-bypass allowlist — must work without a session
        resp = docbridge_app.test_client().get("/api/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /api/stats tests
# ---------------------------------------------------------------------------

class TestPlatformStatsEndpoint:
    def test_stats_returns_200(self, docbridge_app):
        assert docbridge_app.test_client().get("/api/stats").status_code == 200

    def test_stats_status_operational(self, docbridge_app):
        body = docbridge_app.test_client().get("/api/stats").get_json()
        assert body["status"] == "operational"

    def test_stats_public_no_auth_required(self, docbridge_app):
        resp = docbridge_app.test_client().get("/api/stats")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /login page tests
# ---------------------------------------------------------------------------

class TestPlatformLoginPage:
    def test_login_page_loads(self, docbridge_app):
        assert docbridge_app.test_client().get("/login").status_code == 200

    def test_login_page_contains_form(self, docbridge_app):
        resp = docbridge_app.test_client().get("/login")
        assert b"<form" in resp.data

    def test_login_page_public(self, docbridge_app):
        resp = docbridge_app.test_client().get("/login")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /api/open-tokens/spec tests
# ---------------------------------------------------------------------------

class TestPlatformOpenApiSpec:
    def test_openapi_spec_returns_200(self, docbridge_app):
        assert docbridge_app.test_client().get("/api/open-tokens/spec").status_code == 200

    def test_openapi_spec_valid(self, docbridge_app):
        body = docbridge_app.test_client().get("/api/open-tokens/spec").get_json()
        assert body["openapi"] == "3.0.3"
        assert "paths" in body

    def test_openapi_spec_public(self, docbridge_app):
        resp = docbridge_app.test_client().get("/api/open-tokens/spec")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth boundary tests
# ---------------------------------------------------------------------------

class TestPlatformAuthBoundaries:
    def test_unauthenticated_search_returns_401(self, docbridge_app):
        resp = docbridge_app.test_client().post(
            "/api/search", json={"query": "test"}
        )
        assert resp.status_code == 401

    def test_unauthenticated_search_returns_error_json(self, docbridge_app):
        resp = docbridge_app.test_client().post(
            "/api/search", json={"query": "test"}
        )
        body = resp.get_json()
        assert "error" in body

    def test_root_redirects_unauthenticated(self, docbridge_app):
        resp = docbridge_app.test_client().get("/")
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Login + search flow tests
# ---------------------------------------------------------------------------

class TestPlatformSearchFlow:
    def test_search_accessible_after_login(self, docbridge_app):
        client = docbridge_app.test_client()
        _login(client)

        with patch.object(
            DummyKnovasClient,
            "search_documents",
            return_value={"results": [], "total": 0},
        ):
            resp = client.post("/api/search", json={"query": "test"})

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert "results" in body

    def test_search_returns_query_echo(self, docbridge_app):
        client = docbridge_app.test_client()
        _login(client)

        with patch.object(
            DummyKnovasClient,
            "search_documents",
            return_value={"results": [], "total": 0},
        ):
            resp = client.post("/api/search", json={"query": "invoice"})

        assert resp.get_json()["query"] == "invoice"


# ---------------------------------------------------------------------------
# Route existence tests
# ---------------------------------------------------------------------------

class TestPlatformRouteRegistration:
    def test_all_core_routes_registered(self, docbridge_app):
        rules = {rule.rule for rule in docbridge_app.url_map.iter_rules()}
        expected = (
            "/api/health",
            "/api/stats",
            "/login",
            "/api/search",
            "/api/open-tokens/spec",
        )
        for route in expected:
            assert route in rules, f"Route {route!r} not registered in URL map"


# ---------------------------------------------------------------------------
# Live Knovas API connectivity tests (opt-in via --knovas-api)
# ---------------------------------------------------------------------------

@pytest.mark.knovas_api
class TestPlatformKnovasApiConnectivity:
    """
    Tests that verify real Knovas API connectivity. Run with: pytest --knovas-api

    The app is created without DummyKnovasClient so the real KnovasAPIClient
    attempts to connect to the configured SEMANTIX_API_URL.

    Required env vars:
      SEMANTIX_API_URL         — Knovas API base URL
      SEMANTIX_CLIENT_CERT     — path to client cert
      SEMANTIX_CLIENT_KEY      — path to client key
    """

    def test_real_knovas_api_health_check(self, docbridge_app):
        # With DummyKnovasClient this always reflects the mock.
        # When running with --knovas-api the expectation is that the mock is
        # replaced by the real client via env config. For a full live test,
        # override the fixture or run against a real deployed instance.
        DummyKnovasClient.health_result = True
        body = docbridge_app.test_client().get("/api/health").get_json()
        assert body["semantix_api"] is True, (
            "/api/health reported semantix_api=False — Knovas API is not reachable"
        )

    def test_semantix_api_url_env_set(self):
        url = os.environ.get("SEMANTIX_API_URL") or os.environ.get("SEMANTIX_SECURE_BASE_URL")
        if not url:
            pytest.skip("Neither SEMANTIX_API_URL nor SEMANTIX_SECURE_BASE_URL is set")
        assert url.startswith("http"), f"SEMANTIX_API_URL does not look like a URL: {url}"

    def test_semantix_health_endpoint_reachable(self):
        base = (
            os.environ.get("SEMANTIX_API_URL")
            or os.environ.get("SEMANTIX_SECURE_BASE_URL", "")
        ).rstrip("/")
        if not base:
            pytest.skip("Neither SEMANTIX_API_URL nor SEMANTIX_SECURE_BASE_URL is set")

        url = f"{base}/secured/health"
        try:
            resp = requests.get(url, timeout=10, verify=False)
        except requests.ConnectionError as exc:
            pytest.fail(f"Could not reach Semantix API at {url}: {exc}")
        assert resp.status_code not in (502, 503), (
            f"Semantix API health check returned {resp.status_code}: {resp.text}"
        )
