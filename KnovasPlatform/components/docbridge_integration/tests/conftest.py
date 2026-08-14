import os
import uuid

import pytest

# ── identity: a real PostgreSQL per test ───────────────────────────────────
#
# The identity schema uses citext, uuid, jsonb, inet and CHECK constraints, so
# these tests run against the real thing. psycopg is imported lazily inside the
# fixture: an environment without the identity extras must still collect and
# run the rest of the suite.

PLATFORM_DB_TEST_DSN = os.environ.get(
    "PLATFORM_DB_TEST_DSN",
    "postgresql://platform:testpw@127.0.0.1:55433/knovas_platform_test",
)


def platform_db_reachable() -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    try:
        with psycopg.connect(PLATFORM_DB_TEST_DSN, connect_timeout=3):
            return True
    except Exception:
        return False


@pytest.fixture
def platform_db():
    """A migrated identity database in a schema of its own, dropped after.

    Per-test schema rather than a transaction rollback, because the migration
    runner commits DDL — a rollback would not undo it.
    """
    import psycopg

    from identity import migrate

    schema = f"t{uuid.uuid4().hex[:12]}"
    with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as setup:
        setup.execute(f'CREATE SCHEMA "{schema}"')
    conn = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True)
    conn.execute(f'SET search_path TO "{schema}"')
    migrate.apply(conn)
    try:
        yield conn
    finally:
        conn.close()
        with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as cleanup:
            cleanup.execute(f'DROP SCHEMA "{schema}" CASCADE')


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
