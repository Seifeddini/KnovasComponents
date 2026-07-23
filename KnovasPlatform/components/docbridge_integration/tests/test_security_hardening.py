"""
Security hardening regression tests (TDD).

Covers confirmed issues in the DocBridge web app:
  B1  symlink escape / path-metadata oracle in autodoc path confinement
  B2  session cookie missing Secure attribute
  C2  one-time open tokens not single-use across workers
  C1  no brute-force protection on shared login
  C10 internal exception text leaked via str(e)
  plus: unauth stats/version path leak, CORS wildcard, weak SECRET_KEY,
        DOCX decompression bomb.

Conventions follow tests/test_web_login.py / test_open_tokens.py.
"""

import io
import os
import sys
import zipfile
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


def _std_config(
    ad_str,
    *,
    extra_web="",
    extra_open="",
    secret='"${WEB_SECRET_KEY}"',
    login_enabled='"true"',
    companion="false",
):
    return (
        "web:\n"
        f"  secret_key: {secret}\n"
        "  session_lifetime: 3600\n"
        f"{extra_web}"
        "  login:\n"
        f"    enabled: {login_enabled}\n"
        '    company_name: "Test Company"\n'
        '    username: "${COMPANY_LOGIN_NAME}"\n'
        '    password: "${COMPANY_LOGIN_PASSWORD}"\n'
        "  search:\n"
        "    results_per_page: 20\n"
        "api:\n"
        '  base_url: "http://example.test"\n'
        "open:\n"
        f"  companion_enabled: {companion}\n"
        f"{extra_open}"
    )


def _build_app(tmp_path, monkeypatch, *, config_yaml, autodoc_root, set_login_env=True):
    if set_login_env:
        monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-hardening")
        monkeypatch.setenv("COMPANY_LOGIN_ENABLED", "true")
        monkeypatch.setenv("COMPANY_DISPLAY_NAME", "Test Company")
        monkeypatch.setenv("COMPANY_LOGIN_NAME", "office")
        monkeypatch.setenv("COMPANY_LOGIN_PASSWORD", "s3cret")
    monkeypatch.delenv("AUTODOC_IDENTIFIER_PREFIX", raising=False)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_yaml, encoding="utf-8")

    import web_interface.app as web_app

    monkeypatch.setattr(web_app, "KnovasAPIClient", DummyKnovasClient)
    monkeypatch.setattr(web_app, "AutoDocFileHandler", lambda: TmpAutodocHandler(autodoc_root))

    flask_app = web_app.create_app(str(config_path))
    flask_app.config.update(TESTING=True)
    return flask_app


def _csrf(client):
    client.get("/login")
    with client.session_transaction() as sess:
        return sess["csrf_token"]


def _login(client, environ=None):
    kwargs = {}
    if environ:
        kwargs["environ_base"] = environ
    return client.post(
        "/login",
        data={"login_name": "office", "password": "s3cret", "csrf_token": _csrf(client)},
        **kwargs,
    )


@pytest.fixture()
def autodoc_app(tmp_path, monkeypatch):
    ad = tmp_path / "autodoc"
    (ad / "sub").mkdir(parents=True)
    (ad / "sub" / "hello.pdf").write_bytes(b"%PDF-1.4 minimal content")
    app = _build_app(
        tmp_path,
        monkeypatch,
        config_yaml=_std_config(str(ad).replace("\\", "/")),
        autodoc_root=ad,
    )
    return app, ad, tmp_path


# ---------------------------------------------------------------------------
# B1 -- symlink escape / path confinement
# ---------------------------------------------------------------------------
def test_symlink_escape_is_rejected(autodoc_app):
    app, ad, tmp_path = autodoc_app
    outside = tmp_path / "outside"
    outside.mkdir()
    secret_file = outside / "secret.pdf"
    secret_file.write_bytes(b"TOP SECRET HOST FILE")

    link = ad / "evil.pdf"
    try:
        os.symlink(str(secret_file), str(link))
    except OSError:
        pytest.skip("symlink creation not permitted (unprivileged Windows)")

    client = app.test_client()
    _login(client)
    resp = client.get("/api/document/x/download?path=evil.pdf")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Document path not allowed"


def test_legitimate_in_root_download_succeeds(autodoc_app):
    app, ad, tmp_path = autodoc_app
    client = app.test_client()
    _login(client)
    resp = client.get("/api/document/x/download?path=sub/hello.pdf")
    assert resp.status_code == 200
    assert b"%PDF-1.4" in resp.data


def test_enhance_search_results_no_stat_for_traversal(tmp_path, monkeypatch):
    import web_interface.app as web_app

    web_app._search_enrichment_cache = {}
    web_app._search_enrichment_unique = []
    web_app._search_enrichment_by_basename = {}
    web_app._search_enrichment_inferred_prefixes = []
    web_app._search_enrichment_mtime = 0.0
    monkeypatch.setenv("SEARCH_ENRICHMENT_PATH", "")
    monkeypatch.delenv("AUTODOC_IDENTIFIER_PREFIX", raising=False)

    ad = tmp_path / "autodoc"
    ad.mkdir()
    # A real host file OUTSIDE the autodoc root; a "../" pointer would otherwise
    # let an attacker probe its existence/size via the search metadata oracle.
    host_secret = tmp_path / "secret.txt"
    host_secret.write_bytes(b"host secret contents")

    class _Cfg:
        def get_bool(self, k, d=False):
            return d

        def get_int(self, k, d=0):
            return d

        def get(self, k, d=None):
            return d

        def get_float(self, k, d=0.0):
            return d

    results = {"results": [{"doc_id": "x", "path": "../secret.txt"}]}
    enhanced = web_app._enhance_search_results(results, TmpAutodocHandler(ad), _Cfg())
    row = enhanced["results"][0]
    assert row["file_exists"] is False
    assert row.get("can_open") is False
    assert "file_size" not in row


# ---------------------------------------------------------------------------
# B2 -- session cookie Secure attribute
# ---------------------------------------------------------------------------
def _session_set_cookies(resp):
    return [c for c in resp.headers.getlist("Set-Cookie") if c.startswith("session=")]


def test_session_cookie_secure_by_default(tmp_path, monkeypatch):
    ad = tmp_path / "autodoc"
    ad.mkdir()
    app = _build_app(
        tmp_path, monkeypatch, config_yaml=_std_config(str(ad).replace("\\", "/")), autodoc_root=ad
    )
    resp = app.test_client().get("/login")
    cookies = _session_set_cookies(resp)
    assert cookies, "expected a session cookie to be set"
    assert any("Secure" in c for c in cookies)


def test_session_cookie_secure_dev_override(tmp_path, monkeypatch):
    ad = tmp_path / "autodoc"
    ad.mkdir()
    app = _build_app(
        tmp_path,
        monkeypatch,
        config_yaml=_std_config(
            str(ad).replace("\\", "/"), extra_web="  session_cookie_secure: false\n"
        ),
        autodoc_root=ad,
    )
    resp = app.test_client().get("/login")
    cookies = _session_set_cookies(resp)
    assert cookies, "expected a session cookie to be set"
    assert all("Secure" not in c for c in cookies)


# ---------------------------------------------------------------------------
# C2 -- open tokens single-use across workers (shared store)
# ---------------------------------------------------------------------------
def test_open_token_single_use_across_worker_instances(tmp_path):
    from open_tokens import OpenTokenManager

    store = str(tmp_path / "open_tokens.sqlite3")
    secret = "shared-worker-secret"
    worker_a = OpenTokenManager(secret, max_age_seconds=120, store_path=store)
    worker_b = OpenTokenManager(secret, max_age_seconds=120, store_path=store)

    token = worker_a.mint("sub/hello.pdf", "doc-1")

    first = worker_b.verify_and_consume(token, consume=True)
    assert first is not None
    assert first["rel"] == "sub/hello.pdf"

    # A different worker instance sharing the same store must reject the replay.
    second = worker_a.verify_and_consume(token, consume=True)
    assert second is None


# ---------------------------------------------------------------------------
# C1 -- brute force protection on shared login
# ---------------------------------------------------------------------------
def test_login_lockout_after_repeated_failures(tmp_path, monkeypatch):
    ad = tmp_path / "autodoc"
    ad.mkdir()
    app = _build_app(
        tmp_path, monkeypatch, config_yaml=_std_config(str(ad).replace("\\", "/")), autodoc_root=ad
    )
    client = app.test_client()
    attacker = {"REMOTE_ADDR": "10.20.30.40"}

    for _ in range(5):
        token = _csrf(client)
        r = client.post(
            "/login",
            data={"login_name": "office", "password": "wrong", "csrf_token": token},
            environ_base=attacker,
        )
        assert r.status_code == 200

    # Even with correct-looking credentials, the locked IP is rejected.
    token = _csrf(client)
    r = client.post(
        "/login",
        data={"login_name": "office", "password": "s3cret", "csrf_token": token},
        environ_base=attacker,
    )
    assert r.status_code == 429
    with client.session_transaction() as sess:
        assert sess.get("company_login_ok") is not True

    # A different IP is unaffected and can still log in.
    other = app.test_client()
    token2 = _csrf(other)
    r2 = other.post(
        "/login",
        data={"login_name": "office", "password": "s3cret", "csrf_token": token2},
        environ_base={"REMOTE_ADDR": "10.99.99.99"},
        follow_redirects=False,
    )
    assert r2.status_code == 302


# ---------------------------------------------------------------------------
# C10 -- internal exception text must not leak to the client
# ---------------------------------------------------------------------------
def test_search_error_does_not_leak_internal_detail(tmp_path, monkeypatch):
    ad = tmp_path / "autodoc"
    ad.mkdir()
    app = _build_app(
        tmp_path, monkeypatch, config_yaml=_std_config(str(ad).replace("\\", "/")), autodoc_root=ad
    )
    import web_interface.app as web_app

    marker = "SEKRET_/srv/private/credentials.pem_boom"

    def _boom(*args, **kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(web_app, "_enhance_search_results", _boom)

    client = app.test_client()
    _login(client)
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    resp = client.post(
        "/api/search", json={"query": "vertrag"}, headers={"X-CSRF-Token": token}
    )
    assert resp.status_code == 500
    body = resp.get_data(as_text=True)
    assert marker not in body
    assert "credentials.pem" not in body
    assert resp.get_json()["error"] == web_app._GENERIC_ERROR_MESSAGE


# ---------------------------------------------------------------------------
# Unauthenticated /api/stats & /api/version must not leak server paths
# ---------------------------------------------------------------------------
def test_stats_hides_server_path_when_unauthenticated(tmp_path, monkeypatch):
    ad = tmp_path / "autodoc"
    ad.mkdir()
    app = _build_app(
        tmp_path, monkeypatch, config_yaml=_std_config(str(ad).replace("\\", "/")), autodoc_root=ad
    )
    data = app.test_client().get("/api/stats").get_json()
    assert "path" not in data.get("enrichment", {})


def test_version_hides_server_path_when_unauthenticated(tmp_path, monkeypatch):
    ad = tmp_path / "autodoc"
    ad.mkdir()
    app = _build_app(
        tmp_path, monkeypatch, config_yaml=_std_config(str(ad).replace("\\", "/")), autodoc_root=ad
    )
    data = app.test_client().get("/api/version").get_json()
    assert "path" not in data.get("enrichment", {})


# ---------------------------------------------------------------------------
# CORS wildcard must be gone
# ---------------------------------------------------------------------------
def test_no_wildcard_cors_header(tmp_path, monkeypatch):
    ad = tmp_path / "autodoc"
    ad.mkdir()
    app = _build_app(
        tmp_path, monkeypatch, config_yaml=_std_config(str(ad).replace("\\", "/")), autodoc_root=ad
    )
    resp = app.test_client().get("/api/health", headers={"Origin": "https://evil.example"})
    acao = resp.headers.get("Access-Control-Allow-Origin")
    assert acao != "*"
    assert acao != "https://evil.example"


# ---------------------------------------------------------------------------
# Weak SECRET_KEY must be rejected unconditionally (it also signs open tokens)
# ---------------------------------------------------------------------------
def test_weak_secret_rejected_even_when_login_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_LOGIN_NAME", "office")
    monkeypatch.setenv("COMPANY_LOGIN_PASSWORD", "s3cret")
    monkeypatch.delenv("AUTODOC_IDENTIFIER_PREFIX", raising=False)

    ad = tmp_path / "autodoc"
    ad.mkdir()
    config_yaml = _std_config(
        str(ad).replace("\\", "/"),
        secret='""',
        login_enabled='"false"',
        companion="true",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_yaml, encoding="utf-8")

    import web_interface.app as web_app

    monkeypatch.setattr(web_app, "KnovasAPIClient", DummyKnovasClient)
    monkeypatch.setattr(web_app, "AutoDocFileHandler", lambda: TmpAutodocHandler(ad))

    with pytest.raises(RuntimeError):
        web_app.create_app(str(config_path))
