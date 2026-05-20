import os
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


class TmpAutodocHandler:
    autodoc_path = ""

    def __init__(self, root: Path):
        self.autodoc_path = str(root)


@pytest.fixture()
def app_with_open(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-open-tokens")
    monkeypatch.setenv("COMPANY_LOGIN_ENABLED", "true")
    monkeypatch.setenv("COMPANY_DISPLAY_NAME", "Test Company")
    monkeypatch.setenv("COMPANY_LOGIN_NAME", "office")
    monkeypatch.setenv("COMPANY_LOGIN_PASSWORD", "s3cret")

    ad = tmp_path / "autodoc"
    (ad / "sub").mkdir(parents=True)
    f = ad / "sub" / "hello.pdf"
    f.write_bytes(b"%PDF-1.4 minimal")

    config_path = tmp_path / "config.yaml"
    ad_str = str(ad).replace("\\", "/")
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
  companion_enabled: true
  token_ttl_seconds: 120
  unc_root: "\\\\\\\\testfileserver\\\\AutoDocShare"
  local_root: "{ad_str}"
""",
        encoding="utf-8",
    )

    from web_interface import app as web_app

    monkeypatch.setattr(web_app, "KnovasAPIClient", DummyKnovasClient)
    monkeypatch.setattr(web_app, "AutoDocFileHandler", lambda: TmpAutodocHandler(ad))

    flask_app = web_app.create_app(str(config_path))
    flask_app.config.update(TESTING=True)
    return flask_app, ad


def _csrf(client):
    client.get("/login")
    with client.session_transaction() as sess:
        return sess["csrf_token"]


def _login(client):
    return client.post(
        "/login",
        data={
            "login_name": "office",
            "password": "s3cret",
            "csrf_token": _csrf(client),
        },
    )


def test_redeem_without_login(app_with_open):
    app, ad = app_with_open
    client = app.test_client()
    from open_tokens import OpenTokenManager

    mgr = OpenTokenManager("test-secret-open-tokens", max_age_seconds=120)
    tok = mgr.mint("sub/hello.pdf", "doc-1")

    resp = client.post(
        "/api/open-tokens/redeem",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "unc" in data
    assert "hello.pdf" in data["unc"].replace("/", "\\")


def test_mint_requires_csrf_and_login(app_with_open):
    app, ad = app_with_open
    client = app.test_client()
    resp = client.post(
        "/api/open-tokens/mint",
        json={"doc_id": "doc-1", "path": "sub/hello.pdf"},
    )
    assert resp.status_code == 401

    _login(client)
    resp2 = client.post(
        "/api/open-tokens/mint",
        json={"doc_id": "doc-1", "path": "sub/hello.pdf"},
    )
    assert resp2.status_code == 400

    tok = _csrf(client)
    resp3 = client.post(
        "/api/open-tokens/mint",
        headers={"X-CSRF-Token": tok},
        json={"doc_id": "doc-1", "path": "sub/hello.pdf"},
    )
    assert resp3.status_code == 200
    body = resp3.get_json()
    assert body["success"] is True
    assert "companion_href" in body


def test_token_single_use_replay_blocked_same_process(app_with_open):
    app, ad = app_with_open
    client = app.test_client()
    _login(client)
    tok_csrf = _csrf(client)
    minted = client.post(
        "/api/open-tokens/mint",
        headers={"X-CSRF-Token": tok_csrf},
        json={"doc_id": "doc-1", "path": "sub/hello.pdf"},
    ).get_json()
    token = minted["token"]

    r1 = client.post("/api/open-tokens/redeem", headers={"Authorization": f"Bearer {token}"})
    assert r1.status_code == 200
    r2 = client.post("/api/open-tokens/redeem", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 401
