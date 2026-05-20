import os

import pytest
import requests


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_health(client):
    return client.get("/health")


# ---------------------------------------------------------------------------
# Default health check tests (no Knovas API required)
# ---------------------------------------------------------------------------

class TestRcHealthEndpoint:
    def test_health_returns_200(self, rc_client):
        assert _get_health(rc_client).status_code == 200

    def test_health_content_type_json(self, rc_client):
        resp = _get_health(rc_client)
        assert "application/json" in resp.content_type

    def test_health_status_ok(self, rc_client):
        body = _get_health(rc_client).get_json()
        assert body["status"] == "ok"

    def test_health_service_field(self, rc_client):
        body = _get_health(rc_client).get_json()
        assert body["service"] == "remote-controller"

    def test_health_checks_key_present(self, rc_client):
        body = _get_health(rc_client).get_json()
        assert "checks" in body

    def test_health_config_check_ok(self, rc_client):
        body = _get_health(rc_client).get_json()
        assert body["checks"]["config"] == "ok"

    def test_health_watch_roots_check_ok(self, rc_client):
        # rc_client depends on tmp_watch_root which creates a real directory
        body = _get_health(rc_client).get_json()
        assert body["checks"]["watch_roots"] == "ok"

    def test_health_watch_roots_detail_is_list(self, rc_client):
        body = _get_health(rc_client).get_json()
        detail = body["checks"]["watch_roots_detail"]
        assert isinstance(detail, list)
        assert len(detail) > 0

    def test_health_watch_root_exists_true(self, rc_client):
        body = _get_health(rc_client).get_json()
        assert body["checks"]["watch_roots_detail"][0]["exists"] is True

    def test_health_watch_root_readable_true(self, rc_client):
        body = _get_health(rc_client).get_json()
        assert body["checks"]["watch_roots_detail"][0]["readable"] is True

    def test_health_scheduler_check_present(self, rc_client):
        body = _get_health(rc_client).get_json()
        assert "scheduler" in body["checks"]

    def test_health_scheduler_state_present(self, rc_client):
        body = _get_health(rc_client).get_json()
        assert "scheduler_state" in body["checks"]

    def test_health_no_auth_required(self, rc_client):
        # Must be reachable without any Authorization header (used by liveness probes)
        resp = rc_client.get("/health")
        assert resp.status_code == 200

    def test_health_watch_roots_degraded_when_missing(self, rc_client):
        from config import reset_config, load_config

        original = os.environ.get("RC_WATCH_ROOTS")
        try:
            os.environ["RC_WATCH_ROOTS"] = "/nonexistent/__health_check_test__"
            reset_config()
            load_config(validate=False, force_reload=True)

            resp = rc_client.get("/health")
            body = resp.get_json()
            assert resp.status_code == 503
            assert body["status"] == "degraded"
            assert body["checks"]["watch_roots"] == "degraded"
        finally:
            if original is not None:
                os.environ["RC_WATCH_ROOTS"] = original
            else:
                os.environ.pop("RC_WATCH_ROOTS", None)
            reset_config()
            load_config(validate=False, force_reload=True)


# ---------------------------------------------------------------------------
# Metrics endpoint
# ---------------------------------------------------------------------------

class TestRcMetricsEndpoint:
    def test_metrics_returns_200(self, rc_client):
        assert rc_client.get("/metrics").status_code == 200

    def test_metrics_content_type_prometheus(self, rc_client):
        resp = rc_client.get("/metrics")
        assert "text/plain" in resp.content_type


# ---------------------------------------------------------------------------
# Route existence — authenticated routes must return auth errors, not 404/500
# ---------------------------------------------------------------------------

class TestRcRouteExistence:
    def test_discover_route_exists_not_404(self, rc_client):
        resp = rc_client.get("/discover")
        assert resp.status_code in (401, 403, 429)
        assert resp.status_code not in (404, 500)

    def test_sync_route_exists_not_404(self, rc_client):
        resp = rc_client.post("/sync", json={})
        assert resp.status_code in (400, 401, 403, 429)
        assert resp.status_code not in (404, 500)

    def test_all_blueprints_registered(self, rc_client):
        rules = {rule.rule for rule in rc_client.application.url_map.iter_rules()}
        for expected in ("/health", "/metrics", "/discover", "/sync"):
            assert expected in rules, f"Route {expected!r} not registered"


# ---------------------------------------------------------------------------
# Live Knovas API connectivity tests (opt-in via --knovas-api)
# ---------------------------------------------------------------------------

@pytest.mark.knovas_api
class TestRcKnovasApiConnectivity:
    """
    These tests make real HTTP calls to the Knovas APIs configured in the
    environment. Run with: pytest --knovas-api

    Required env vars:
      KNOVAS_INTERNAL_API_URL  — base URL of the Knovas internal API
      RC_INSTANCE_TOKEN        — RC instance token for authorization
      SEMANTIX_SECURE_BASE_URL — base URL of the Semantix secure API
    """

    def test_knovas_internal_api_verify_endpoint_reachable(self):
        base = os.environ.get("KNOVAS_INTERNAL_API_URL", "").rstrip("/")
        token = os.environ.get("RC_INSTANCE_TOKEN", "")
        if not base:
            pytest.skip("KNOVAS_INTERNAL_API_URL not set")

        url = f"{base}/remote_controller/verify_operator"
        headers = {
            "Authorization": "Bearer test-live-probe",
            "X-RC-Instance-Token": token,
            "Content-Type": "application/json",
        }
        payload = {"employee_id": "probe", "certificate_serial": "probe"}
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
        except requests.ConnectionError as exc:
            pytest.fail(f"Could not reach Knovas internal API at {url}: {exc}")
        # 200/403/401 all prove the endpoint responded; 503 or connection error means not reachable
        assert resp.status_code != 503, (
            f"Knovas internal API returned 503 — service may be down: {resp.text}"
        )

    def test_semantix_api_health_reachable(self):
        base = os.environ.get("SEMANTIX_SECURE_BASE_URL", "").rstrip("/")
        if not base:
            pytest.skip("SEMANTIX_SECURE_BASE_URL not set")

        url = f"{base}/secured/health"
        try:
            resp = requests.get(url, timeout=10, verify=False)
        except requests.ConnectionError as exc:
            pytest.fail(f"Could not reach Semantix API at {url}: {exc}")
        assert resp.status_code not in (503, 502), (
            f"Semantix API health check failed with {resp.status_code}: {resp.text}"
        )
