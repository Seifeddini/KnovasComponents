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
    """Whether the identity tests can run. Skipping is a developer convenience.

    In CI it is not: a database that failed to come up would turn roughly 180
    identity and admin tests into skips and leave the run green, which is the
    one outcome worse than a red build. PLATFORM_DB_REQUIRED (set by the
    workflow) turns an unreachable database into a loud collection error.
    """
    required = os.environ.get("PLATFORM_DB_REQUIRED", "").strip().lower() in {
        "1", "true", "yes",
    }
    try:
        import psycopg
    except ImportError:
        if required:
            raise RuntimeError(
                "PLATFORM_DB_REQUIRED is set but psycopg is not installed, so the "
                "identity and admin tests would silently skip. Install the identity "
                "extras from requirements.txt."
            )
        return False
    try:
        with psycopg.connect(PLATFORM_DB_TEST_DSN, connect_timeout=3):
            return True
    except Exception as exc:
        if required:
            raise RuntimeError(
                f"PLATFORM_DB_REQUIRED is set but {PLATFORM_DB_TEST_DSN} is "
                f"unreachable ({exc}). Refusing to skip the identity and admin "
                "tests into a green run."
            ) from exc
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

    # create_app() boots the identity schema and the first administrator, the
    # way gunicorn does in production, so the fixture has to supply the same
    # bootstrap values a deployment does. The address is deliberately not one
    # the per-test `people` fixtures use, so a test that lists accounts sees
    # its own cast plus this one rather than a surprising collision.
    monkeypatch.setenv("PLATFORM_ADMIN_EMAIL", "bootstrap@kanzlei.ch")
    monkeypatch.setenv("PLATFORM_ADMIN_PASSWORD", "bootstrap-korrektes-pferd")
    monkeypatch.setenv(
        "PLATFORM_ADMIN_BOOTSTRAP_PATH", (tmp_path / "admin-bootstrap").as_posix()
    )

    broker_dir = (tmp_path / "broker").as_posix()
    (tmp_path / "broker").mkdir(exist_ok=True)
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
        f'  broker_key_dir: "{broker_dir}"\n'
        'api:\n'
        '  base_url: "http://example.test"\n'
        '  customer_id: "tenant-a"\n'
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


class DummyKnovasClient:
    """Controllable mock. Set DummyKnovasClient.health_result before creating the app."""

    health_result = True
    last_instance = None
    customer_id = "tenant-a"

    def __init__(self, config):
        self.config = config
        self.principal_broker = None
        self.acl_calls: list[tuple] = []
        self.fail_next = False
        # Pointers the content gate must refuse. Empty means "everything is
        # readable", which keeps every test that predates the wall unchanged.
        self.denied_pointers: set[str] = set()
        self.readable_calls: list[str] = []
        DummyKnovasClient.last_instance = self

    def document_readable(self, pointer):
        self.readable_calls.append(str(pointer))
        return str(pointer) not in self.denied_pointers

    def attach_principal_broker(self, broker):
        self.principal_broker = broker

    def health_check(self):
        return DummyKnovasClient.health_result

    def search_documents(self, query, limit=20, filters=None):
        return {"results": [], "total": 0}

    # -- what the console's Dokumente / Zugriffsgruppen tabs call --------
    def documents(self, **kw):
        return {"documents": [], "next_after": None, "total_count": 0}

    def access_groups(self):
        return [{"group_id": "g-lit", "name": "Litigation", "parent_id": None}]

    def folder_rules(self):
        return []

    def set_document_access(self, pointer, access_groups, acting_as=None):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated backend failure")
        self.acl_calls.append(("set_document_access", pointer, list(access_groups)))
        return {"pointer": pointer, "access_groups": list(access_groups)}

    def create_folder_rule(self, pointer_prefix, access_groups, acting_as=None):
        self.acl_calls.append(("create_folder_rule", pointer_prefix, list(access_groups)))
        return {"rule_id": "r-new", "pointer_prefix": pointer_prefix}

    def update_folder_rule(self, rule_id, access_groups, acting_as=None):
        self.acl_calls.append(("update_folder_rule", rule_id, list(access_groups)))
        return {"rule_id": rule_id}

    def delete_folder_rule(self, rule_id):
        self.acl_calls.append(("delete_folder_rule", rule_id, []))
        return True


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


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Under CI a PostgreSQL skip is a failure, not a pass.

    151 identity tests skipped silently for weeks because an unreachable
    database looked exactly like a green run. In CI we would rather be
    loudly broken than quietly untested. Locally the skip stays a skip.
    """
    outcome = yield
    report = outcome.get_result()
    if os.environ.get("CI") != "true":
        return
    if report.skipped and "No PostgreSQL" in str(report.longrepr):
        report.outcome = "failed"
        report.longrepr = (
            "PostgreSQL was unreachable in CI. Identity tests must execute, "
            "not skip -- a skipped security test is a test that does not exist."
        )
