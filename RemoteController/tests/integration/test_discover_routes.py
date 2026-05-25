from unittest.mock import patch


def test_discover_auth_required(rc_client):
    resp = rc_client.get("/discover")
    assert resp.status_code in (401, 403, 429)


def test_discover_valid_200(rc_client, auth_headers):
    with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
        mock_client.return_value.verify_operator.return_value = (True, "client", None)
        resp = rc_client.get("/discover", headers=auth_headers)
        assert resp.status_code == 200
