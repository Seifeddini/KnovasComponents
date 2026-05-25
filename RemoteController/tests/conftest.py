import os
from pathlib import Path

import pytest

from tests.helpers import TEST_EMPLOYEE_ID, make_test_jwt


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


os.environ.setdefault("RC_SKIP_CONFIG_VALIDATION", "true")
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("RC_RATE_LIMIT_ENABLED", "true")
os.environ.setdefault("KNOVAS_INTERNAL_API_URL", "http://internal-api:5000")
os.environ.setdefault("RC_INSTANCE_TOKEN", "test-instance-token")
os.environ.setdefault("RC_CLIENT_ID", "22222222-2222-2222-2222-222222222222")
os.environ.setdefault("SEMANTIX_SECURE_BASE_URL", "https://semantix:8443")
os.environ.setdefault("SEMANTIX_CLIENT_CERT_PATH", "/certs/client.pem")
os.environ.setdefault("SEMANTIX_CLIENT_KEY_PATH", "/certs/client.key")
os.environ.setdefault("SEMANTIX_CA_CERT_PATH", "/certs/ca.pem")

from config import load_config  # noqa: E402

load_config(validate=False)


@pytest.fixture
def test_jwt():
    return make_test_jwt()


@pytest.fixture
def auth_headers(test_jwt):
    return {"Authorization": f"Bearer {test_jwt}"}


@pytest.fixture
def tmp_watch_root(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    (root / "sample.md").write_text("# Hello\nWorld", encoding="utf-8")
    os.environ["RC_WATCH_ROOTS"] = str(root)
    from config import reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    yield root


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    import auth.rc_rate_limit as rl

    rl._ip_limiter = None
    rl._handled_limiter = None
    yield
    rl._ip_limiter = None
    rl._handled_limiter = None


@pytest.fixture
def rc_client(tmp_watch_root):
    from app import create_app

    application = create_app(skip_validation=True)
    application.config["TESTING"] = True
    with application.test_client() as client:
        yield client
