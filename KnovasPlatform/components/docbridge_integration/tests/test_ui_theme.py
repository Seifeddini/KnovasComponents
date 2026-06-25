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


def _make_app(tmp_path, monkeypatch, *, theme_env: str = ""):
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret")
    monkeypatch.setenv("COMPANY_LOGIN_ENABLED", "true")
    monkeypatch.setenv("COMPANY_DISPLAY_NAME", "Test Company")
    monkeypatch.setenv("COMPANY_LOGIN_NAME", "office")
    monkeypatch.setenv("COMPANY_LOGIN_PASSWORD", "s3cret")
    monkeypatch.setenv("WEB_UI_THEME", theme_env)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
web:
  secret_key: "${WEB_SECRET_KEY}"
  theme: "${WEB_UI_THEME:-}"
  login:
    enabled: "${COMPANY_LOGIN_ENABLED:-true}"
    company_name: "${COMPANY_DISPLAY_NAME:-Knovas}"
    username: "${COMPANY_LOGIN_NAME}"
    password: "${COMPANY_LOGIN_PASSWORD}"
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


@pytest.fixture()
def app_no_theme(tmp_path, monkeypatch):
    return _make_app(tmp_path, monkeypatch)


@pytest.fixture()
def app_helvetia(tmp_path, monkeypatch):
    return _make_app(tmp_path, monkeypatch, theme_env="helvetia")


def test_no_theme_without_config_or_query(app_no_theme):
    client = app_no_theme.test_client()
    with client.session_transaction() as sess:
        sess["company_login_ok"] = True
        sess["csrf_token"] = "tok"

    response = client.get("/")
    assert response.status_code == 200
    assert b"theme-draft-" not in response.data
    assert b"css/drafts/" not in response.data


def test_configured_theme_applied(app_helvetia):
    client = app_helvetia.test_client()
    with client.session_transaction() as sess:
        sess["company_login_ok"] = True
        sess["csrf_token"] = "tok"

    response = client.get("/")
    assert response.status_code == 200
    assert b'theme-draft-helvetia' in response.data
    assert b"css/drafts/helvetia.css" in response.data


def test_query_theme_overrides_config(app_helvetia):
    client = app_helvetia.test_client()
    with client.session_transaction() as sess:
        sess["company_login_ok"] = True
        sess["csrf_token"] = "tok"

    response = client.get("/?theme=ledger")
    assert response.status_code == 200
    assert b'theme-draft-ledger' in response.data
    assert b"css/drafts/ledger.css" in response.data
