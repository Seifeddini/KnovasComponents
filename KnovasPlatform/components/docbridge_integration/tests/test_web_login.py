import sys
from pathlib import Path

import pytest


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


class DummyKnovasClient:
    def __init__(self, config):
        self.config = config

    def health_check(self):
        return True


class DummyFileHandler:
    autodoc_path = "/tmp"


def _csrf_token(client):
    client.get("/login")
    with client.session_transaction() as sess:
        return sess["csrf_token"]


def _login(client):
    token = _csrf_token(client)
    return client.post(
        "/login",
        data={
            "login_name": "office",
            "password": "s3cret",
            "csrf_token": token,
        },
    )


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret")
    monkeypatch.setenv("COMPANY_LOGIN_ENABLED", "true")
    monkeypatch.setenv("COMPANY_DISPLAY_NAME", "Test Company")
    monkeypatch.setenv("COMPANY_LOGIN_NAME", "office")
    monkeypatch.setenv("COMPANY_LOGIN_PASSWORD", "s3cret")

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
web:
  secret_key: "${WEB_SECRET_KEY}"
  session_lifetime: 3600
  login:
    enabled: "${COMPANY_LOGIN_ENABLED:-true}"
    company_name: "${COMPANY_DISPLAY_NAME:-Knovas}"
    username: "${COMPANY_LOGIN_NAME}"
    password: "${COMPANY_LOGIN_PASSWORD}"
  search:
    results_per_page: 20
api:
  base_url: "http://example.test"
open:
  companion_enabled: false
""",
        encoding="utf-8",
    )

    from web_interface import app as web_app

    monkeypatch.setattr(web_app, "KnovasAPIClient", DummyKnovasClient)
    monkeypatch.setattr(web_app, "AutoDocFileHandler", DummyFileHandler)

    flask_app = web_app.create_app(str(config_path))
    flask_app.config.update(TESTING=True)
    return flask_app


def test_root_redirects_to_login_when_not_authenticated(app):
    response = app.test_client().get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?next=/")


def test_api_requires_login(app):
    response = app.test_client().post("/api/search", json={"query": "vertrag"})

    assert response.status_code == 401
    assert response.get_json()["error"] == "Login erforderlich"


def test_login_success_allows_root(app):
    client = app.test_client()
    token = _csrf_token(client)

    response = client.post(
        "/login",
        data={
            "login_name": "office",
            "password": "s3cret",
            "next": "/",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")

    response = client.get("/")
    assert response.status_code == 200
    assert b"Knovas Document Search" in response.data


def test_login_failure_keeps_user_on_login(app):
    client = app.test_client()
    token = _csrf_token(client)

    response = client.post(
        "/login",
        data={
            "login_name": "office",
            "password": "wrong",
            "next": "/",
            "csrf_token": token,
        },
    )

    assert response.status_code == 200
    assert "Login-Name oder Passwort ist falsch.".encode("utf-8") in response.data


def test_logout_clears_session(app):
    client = app.test_client()
    _login(client)
    with client.session_transaction() as sess:
        logout_token = sess["csrf_token"]

    response = client.post(
        "/logout",
        data={"csrf_token": logout_token},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")

    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?next=/")


def test_health_endpoints_remain_public(app):
    client = app.test_client()

    assert client.get("/api/stats").status_code == 200
    assert client.get("/api/health").status_code == 200
    spec = client.get("/api/open-tokens/spec")
    assert spec.status_code == 200
    assert spec.get_json()["openapi"] == "3.0.3"


def test_document_paths_cannot_escape_autodoc_root(app):
    client = app.test_client()
    _login(client)

    response = client.post(
        "/api/document/example/open",
        json={"path": "../secrets/client.key"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "Document path not allowed"

    response = client.get("/api/document/example/download?path=../secrets/client.key")
    assert response.status_code == 400
    assert response.get_json()["error"] == "Document path not allowed"
