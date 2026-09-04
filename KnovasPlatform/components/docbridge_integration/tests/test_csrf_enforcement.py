"""
CSRF enforcement regression tests (TDD).

Coordinated hardening: every state-changing POST the browser issues must carry a
valid ``X-CSRF-Token`` header (mirrors static/js/app.js). Endpoints enforced here:

  POST /api/search
  POST /api/document/<id>/open

Exempt / unchanged: login + logout (form ``csrf_token``, validated in-handler),
/api/open-tokens/mint (in-handler X-CSRF-Token check -> 400), /api/open-tokens/redeem
(companion Bearer token, no browser session). GET requests are never gated.

Conventions follow tests/test_web_login.py / test_security_hardening.py.
"""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
WEB_SRC = SRC / "web_interface"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(WEB_SRC) not in sys.path:
    sys.path.insert(0, str(WEB_SRC))


class DummyKnovasClient:
    def __init__(self, config):
        self.config = config

    def health_check(self):
        return True

    def search_documents(self, query, limit=20, filters=None):
        return {"results": [], "total": 0}


class TmpAutodocHandler:
    def __init__(self, root):
        self.autodoc_path = str(root)


def _build_app(tmp_path, monkeypatch, autodoc_root):
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-csrf")
    monkeypatch.setenv("COMPANY_LOGIN_ENABLED", "true")
    monkeypatch.setenv("COMPANY_DISPLAY_NAME", "Test Company")
    monkeypatch.setenv("COMPANY_LOGIN_NAME", "office")
    monkeypatch.setenv("COMPANY_LOGIN_PASSWORD", "s3cret")
    monkeypatch.delenv("AUTODOC_IDENTIFIER_PREFIX", raising=False)

    ad_str = str(autodoc_root).replace("\\", "/")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
web:
  secret_key: "${{WEB_SECRET_KEY}}"
  session_lifetime: 3600
  login:
    enabled: "${{COMPANY_LOGIN_ENABLED:-true}}"
    company_name: "${{COMPANY_DISPLAY_NAME:-Knovas}}"
    username: "${{COMPANY_LOGIN_NAME}}"
    password: "${{COMPANY_LOGIN_PASSWORD}}"
  search:
    results_per_page: 20
api:
  base_url: "http://example.test"
open:
  companion_enabled: false
  local_root: "{ad_str}"
""",
        encoding="utf-8",
    )

    import web_interface.app as web_app

    monkeypatch.setattr(web_app, "KnovasAPIClient", DummyKnovasClient)
    monkeypatch.setattr(web_app, "AutoDocFileHandler", lambda: TmpAutodocHandler(autodoc_root))

    flask_app = web_app.create_app(str(config_path))
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture()
def csrf_app(tmp_path, monkeypatch):
    ad = tmp_path / "autodoc"
    (ad / "sub").mkdir(parents=True)
    (ad / "sub" / "hello.pdf").write_bytes(b"%PDF-1.4 minimal")
    return _build_app(tmp_path, monkeypatch, ad)


def _login_and_token(client):
    """Log in and return the post-login session CSRF token (as the browser holds it)."""
    client.get("/login")
    with client.session_transaction() as sess:
        login_csrf = sess["csrf_token"]
    client.post(
        "/login",
        data={"login_name": "office", "password": "s3cret", "csrf_token": login_csrf},
    )
    with client.session_transaction() as sess:
        return sess["csrf_token"]


# ---------------------------------------------------------------------------
# Negative: a logged-in POST WITHOUT a valid X-CSRF-Token must be rejected (403)
# ---------------------------------------------------------------------------
def test_search_post_without_csrf_is_forbidden(csrf_app):
    client = csrf_app.test_client()
    _login_and_token(client)
    resp = client.post("/api/search", json={"query": "vertrag"})
    assert resp.status_code == 403


def test_open_document_post_without_csrf_is_forbidden(csrf_app):
    client = csrf_app.test_client()
    _login_and_token(client)
    resp = client.post(
        "/api/document/doc-1/open",
        json={"path": "sub/hello.pdf"},
    )
    assert resp.status_code == 403


def test_search_post_with_invalid_csrf_is_forbidden(csrf_app):
    client = csrf_app.test_client()
    _login_and_token(client)
    resp = client.post(
        "/api/search",
        json={"query": "vertrag"},
        headers={"X-CSRF-Token": "not-the-real-token"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Positive: the same POSTs WITH a valid X-CSRF-Token reach the handler (not 403)
# ---------------------------------------------------------------------------
def test_search_post_with_valid_csrf_allowed(csrf_app):
    client = csrf_app.test_client()
    token = _login_and_token(client)
    resp = client.post(
        "/api/search",
        json={"query": "vertrag"},
        headers={"X-CSRF-Token": token},
    )
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True


def test_open_document_post_with_valid_csrf_not_csrf_blocked(csrf_app):
    client = csrf_app.test_client()
    token = _login_and_token(client)
    resp = client.post(
        "/api/document/doc-1/open",
        json={"path": "sub/hello.pdf"},
        headers={"X-CSRF-Token": token},
    )
    # Server-side open is disabled by default -> 410; the point is the CSRF gate
    # let the request through to the handler (i.e. it is NOT a 403).
    assert resp.status_code != 403
    assert resp.status_code == 410


# ---------------------------------------------------------------------------
# Regression: GET, login precedence, and exempt endpoints stay unchanged
# ---------------------------------------------------------------------------
def test_unauthenticated_search_is_401_not_403(csrf_app):
    # Login check must run before the CSRF gate: no session -> 401 (Login erforderlich).
    client = csrf_app.test_client()
    resp = client.post("/api/search", json={"query": "vertrag"})
    assert resp.status_code == 401


def test_mint_without_csrf_still_returns_400_not_403(csrf_app):
    # /api/open-tokens/mint keeps its own in-handler CSRF check (400), not the gate.
    client = csrf_app.test_client()
    _login_and_token(client)
    resp = client.post(
        "/api/open-tokens/mint",
        json={"doc_id": "doc-1", "path": "sub/hello.pdf"},
    )
    assert resp.status_code != 403
    # companion disabled in this config -> 503 (still proves the gate did not fire)
    assert resp.status_code in (400, 503)


# ---------------------------------------------------------------------------
# Graph blueprint (SS-315): mutating /api/graph/* routes inherit the global hook
# ---------------------------------------------------------------------------
from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable  # noqa: E402

_GRAPH_MUTATIONS = [
    ("POST",   "/api/graph/node-types"),
    ("POST",   "/api/graph/node-types/t1/schema"),
    ("PATCH",  "/api/graph/node-types/t1/schema/a1"),
    ("DELETE", "/api/graph/node-types/t1/schema/a1"),
    ("POST",   "/api/graph/nodes"),
    ("PATCH",  "/api/graph/nodes/n1"),
    ("POST",   "/api/graph/nodes/n1/facts"),
    ("PATCH",  "/api/graph/facts/f1"),
    ("DELETE", "/api/graph/facts/f1"),
    ("POST",   "/api/graph/nodes/n1/grants"),
    ("DELETE", "/api/graph/nodes/n1/grants/u1"),
]


@pytest.mark.skipif(not platform_db_reachable(),
                    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")
@pytest.mark.parametrize("method, path", _GRAPH_MUTATIONS)
def test_graph_mutating_route_without_csrf_is_forbidden(admin_client_no_csrf,
                                                        method, path):
    """The graph JSON blueprint must not be CSRF-exempt. A 404 here would mean
    the route is missing; a 401 would mean the session did not attach."""
    response = admin_client_no_csrf.open(path, method=method, json={})
    assert response.status_code == 403
