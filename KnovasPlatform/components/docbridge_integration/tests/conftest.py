import json
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


@pytest.fixture
def identity_app(platform_db, tmp_path, monkeypatch):
    """The real Flask app, with per-user identity on and pointed at platform_db.

    The app opens its own connections, so it must reach the same schema the
    fixture migrated — passed through as a search_path in the DSN.
    """
    schema = platform_db.execute("SELECT current_schema()").fetchone()[0]
    monkeypatch.setenv(
        "PLATFORM_DB_DSN", f"{PLATFORM_DB_TEST_DSN}?options=-csearch_path%3D{schema}"
    )
    monkeypatch.delenv("PLATFORM_DB_PASSWORD_FILE", raising=False)

    broker_dir = tmp_path / "broker_keys"
    broker_dir.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        'web:\n'
        '  secret_key: "a-strong-secret-for-tests-0123456789"\n'
        '  session_lifetime: 3600\n'
        '  session_cookie_secure: false\n'
        '  login:\n'
        '    enabled: true\n'
        '    company_name: "TestCo"\n'
        '  search:\n'
        '    results_per_page: 20\n'
        'identity:\n'
        '  enabled: true\n'
        f'  broker_key_dir: {json.dumps(str(broker_dir))}\n'
        'api:\n'
        '  base_url: "http://example.test"\n'
        '  client_id: "tenant-a"\n'
        'open:\n'
        '  companion_enabled: false\n',
        encoding="utf-8",
    )

    DummyKnovasClient.health_result = True
    from web_interface import app as web_app

    monkeypatch.setattr(web_app, "KnovasAPIClient", DummyKnovasClient)
    monkeypatch.setattr(web_app, "AutoDocFileHandler", DummyFileHandler)

    flask_app = web_app.create_app(str(config_path))
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def identity_client(identity_app):
    return identity_app.test_client()


@pytest.fixture
def identity_repo(platform_db):
    from identity import users

    return users.UserRepository(platform_db)


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


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Under CI a PostgreSQL skip is a failure, not a pass.

    151 identity tests skipped silently for weeks because an unreachable
    database looked exactly like a green run. In CI we would rather be
    loudly broken than quietly untested.
    """
    outcome = yield
    report = outcome.get_result()
    if os.environ.get("CI") != "true":
        return
    if report.skipped and "No PostgreSQL" in str(report.longrepr):
        report.outcome = "failed"
        report.longrepr = (
            "PostgreSQL was unreachable in CI. Identity tests must execute, "
            "not skip — a skipped security test is a test that does not exist."
        )


class DummyKnovasClient:
    """Controllable mock. Set DummyKnovasClient.health_result before creating the app."""

    health_result = True

    def __init__(self, config, *, principal_broker=None):
        self.config = config
        self._principal_broker = principal_broker

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


# ── principal assertion on the outbound Knovas client (Task 4) ───────────

_ASSERTION_PASSWORD = "korrektes-pferd-batterie"


class _StubConfig:
    """Minimal ConfigLoader stand-in so assertion tests need no YAML or network."""

    def __init__(self, values):
        self._v = dict(values)

    def get(self, key, default=None):
        return self._v.get(key, default)

    def get_bool(self, key, default=False):
        if key not in self._v:
            return default
        v = self._v[key]
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("true", "yes", "1", "on")
        return bool(v)

    def get_int(self, key, default=0):
        if key not in self._v:
            return default
        try:
            return int(self._v[key])
        except (TypeError, ValueError):
            return default


def _unsigned_client_config():
    return {
        "api.base_url": "http://example.test",
        "api.auth_type": "bearer",
        "api.api_key": "",
        "api.use_secured_api": False,
        "api.allow_legacy_api_fallback": True,
        "api.cert_path": "",
        "api.key_path": "",
        "api.ca_cert_path": "",
        "api.customer_id": "",
        "api.cert_auto_renew_enabled": False,
        "api.cert_renew_threshold_days": 30,
        "api.cert_check_interval_seconds": 3600,
        "api.cert_renew_method": "csr",
        "api.encryption_matrix_path": "",
        "api.rate_limit.requests_per_second": 0,
        "api.rate_limit.retry_attempts": 3,
        "api.rate_limit.retry_backoff": 2,
        "api.http_read_timeout": 5,
    }


class _SearchableClient:
    """KnovasAPIClient with the brief's `.search()` name and the minted subject."""

    def __init__(self, api_client, subject=None):
        self._client = api_client
        self.subject = None if subject is None else str(subject)

    def search(self, query):
        return self._client.search_documents(query)


@pytest.fixture
def broker_keypair(tmp_path):
    from identity.broker_key import load_or_create_signer, public_pem

    key_dir = tmp_path / "assertion_broker_keys"
    key_dir.mkdir()
    signer = load_or_create_signer(key_dir)
    kid = (key_dir / "broker_ed25519.kid").read_text().strip()
    return signer, public_pem(key_dir), kid


@pytest.fixture
def broker_public_pem(broker_keypair):
    return broker_keypair[1]


@pytest.fixture
def broker_kid(broker_keypair):
    return broker_keypair[2]


@pytest.fixture
def captured_requests(monkeypatch):
    """Every outbound call's body, in order, after `_with_principal`.

    The bug this whole task fixes was that minting was well tested and never
    called — so assert on what left the process, not on what mint() returned.
    Production's retrying path is `_make_request` (the brief called it
    `_request`); patch that so `.search()` hits the recorder.
    """
    calls = []

    class _Captured:
        def __init__(self, body, headers):
            self.body = body or {}
            self.headers = headers

    def _record(self, method, endpoint, data=None, params=None, **kwargs):
        body = self._with_principal(data)
        calls.append(_Captured(body, dict(self._get_headers())))

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"results": [], "total": 0}

        return _Resp()

    from knovas_client import KnovasAPIClient

    monkeypatch.setattr(KnovasAPIClient, "_make_request", _record)
    return calls


@pytest.fixture
def client_with_broker(identity_repo, broker_keypair):
    """A KnovasAPIClient whose broker mints for a signed-in user with one group."""
    from identity.principal import PrincipalBroker
    from knovas_client import KnovasAPIClient

    signer, _, _ = broker_keypair
    user = identity_repo.create(
        email="anwalt@testco.example",
        display_name="Anwalt",
        password=_ASSERTION_PASSWORD,
    )
    identity_repo.set_access_groups(user.id, ["litigation"])
    broker = PrincipalBroker(
        user_repo=identity_repo, signer=signer, tenant_id="tenant-a"
    )

    class _FixedUserBroker:
        def current_user(self):
            return user

        def assertion_for(self, signed_in):
            return broker.assertion_for(signed_in)

    api_client = KnovasAPIClient(
        config_loader=_StubConfig(_unsigned_client_config()),
        principal_broker=_FixedUserBroker(),
    )
    return _SearchableClient(api_client, subject=user.id)


@pytest.fixture
def client_with_broker_no_user():
    from knovas_client import KnovasAPIClient

    class _NoUserBroker:
        def current_user(self):
            return None

        def assertion_for(self, user):
            raise AssertionError("must not mint without a user")

    api_client = KnovasAPIClient(
        config_loader=_StubConfig(_unsigned_client_config()),
        principal_broker=_NoUserBroker(),
    )
    return _SearchableClient(api_client)

