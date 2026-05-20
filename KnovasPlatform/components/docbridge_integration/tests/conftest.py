import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--knovas-api",
        action="store_true",
        default=False,
        help="Also run tests that require real Knovas API connectivity",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--knovas-api"):
        skip = pytest.mark.skip(reason="Pass --knovas-api to run live Knovas API tests")
        for item in items:
            if "knovas_api" in item.keywords:
                item.add_marker(skip)


class DummyKnovasClient:
    """Controllable mock. Set DummyKnovasClient.health_result before creating the app."""

    health_result = True

    def __init__(self, config):
        self.config = config

    def health_check(self):
        return DummyKnovasClient.health_result

    def search_documents(self, query, limit=20, filters=None):
        return {"results": [], "total": 0}


class DummyFileHandler:
    autodoc_path = "/tmp"


@pytest.fixture
def docbridge_app(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-key-for-health-checks")
    monkeypatch.setenv("COMPANY_LOGIN_ENABLED", "true")
    monkeypatch.setenv("COMPANY_DISPLAY_NAME", "TestCo")
    monkeypatch.setenv("COMPANY_LOGIN_NAME", "healthuser")
    monkeypatch.setenv("COMPANY_LOGIN_PASSWORD", "healthpass123")

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

    DummyKnovasClient.health_result = True  # reset to healthy for each test

    from web_interface import app as web_app

    monkeypatch.setattr(web_app, "KnovasAPIClient", DummyKnovasClient)
    monkeypatch.setattr(web_app, "AutoDocFileHandler", DummyFileHandler)

    flask_app = web_app.create_app(str(config_path))
    flask_app.config.update(TESTING=True)
    return flask_app
