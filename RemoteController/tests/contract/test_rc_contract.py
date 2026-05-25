from unittest.mock import MagicMock, patch

from tests.helpers import TEST_EMPLOYEE_ID, make_test_jwt


class TestRcContract:
    def test_health_unauthenticated(self, rc_client):
        resp = rc_client.get("/health")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

    def test_discover_anonymous_never_200(self, rc_client):
        resp = rc_client.get("/discover")
        assert resp.status_code in (401, 403, 429)

    def test_discover_returns_200_when_authorized(self, rc_client, auth_headers):
        with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
            mock_client.return_value.verify_operator.return_value = (
                True,
                "22222222-2222-2222-2222-222222222222",
                None,
            )
            resp = rc_client.get("/discover", headers=auth_headers)
            assert resp.status_code == 200
            body = resp.get_json()
            assert body["status"] == "success"
            assert "entries" in body

    def test_discover_rejects_unauthorized_verify(self, rc_client, auth_headers):
        with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
            mock_client.return_value.verify_operator.return_value = (
                False,
                None,
                ({"error": "Operator not authorized", "status": "error"}, 403),
            )
            resp = rc_client.get("/discover", headers=auth_headers)
            assert resp.status_code == 403

    def test_discover_ip_rate_limit_burst_429(self, rc_client, auth_headers):
        with patch("auth.knovas_verify_client.get_verify_client") as mock_verify:
            mock_verify.return_value.verify_operator.return_value = (True, "c", None)
            with patch("auth.rc_rate_limit._get_ip_limiter") as mock_lim:
                limiter = MagicMock()
                limiter.is_allowed.side_effect = [True] * 5 + [False]
                mock_lim.return_value = limiter
                last_status = None
                for _ in range(6):
                    last_status = rc_client.get("/discover", headers=auth_headers).status_code
                assert last_status == 429

    def test_sync_requires_json(self, rc_client, auth_headers):
        with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
            mock_client.return_value.verify_operator.return_value = (True, "c", None)
            resp = rc_client.post(
                "/sync",
                data="not-json",
                headers=auth_headers,
                content_type="text/plain",
            )
            assert resp.status_code == 400

    def test_discover_requires_bearer(self, rc_client):
        resp = rc_client.get("/discover")
        assert resp.status_code == 401

    def test_discover_rejects_invalid_jwt_shape(self, rc_client):
        resp = rc_client.get("/discover", headers={"Authorization": "Bearer notavalid"})
        assert resp.status_code == 401

    def test_discover_verify_unreachable_503(self, rc_client, auth_headers):
        with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
            mock_client.return_value.verify_operator.return_value = (
                False,
                None,
                (
                    {"error": "Remote operator verification unavailable", "status": "error"},
                    503,
                ),
            )
            resp = rc_client.get("/discover", headers=auth_headers)
            assert resp.status_code == 503

    def test_discover_passes_jwt_identity_to_verify(self, rc_client, auth_headers):
        with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
            mock_client.return_value.verify_operator.return_value = (
                True,
                "22222222-2222-2222-2222-222222222222",
                None,
            )
            rc_client.get("/discover", headers=auth_headers)
            mock_client.return_value.verify_operator.assert_called_once()
            jwt_token, employee_id = mock_client.return_value.verify_operator.call_args[0]
            assert employee_id == TEST_EMPLOYEE_ID
            assert jwt_token == make_test_jwt()

    def test_sync_anonymous_never_200(self, rc_client):
        resp = rc_client.post("/sync", json={"sources": []})
        assert resp.status_code in (401, 403, 429)
