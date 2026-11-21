"""GET /api/document/<id>/client-path: the UNC or mount path a client PC opens."""
import sys
from pathlib import Path

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


def test_client_path_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-client-path")
    monkeypatch.setenv("COMPANY_LOGIN_ENABLED", "true")
    monkeypatch.setenv("COMPANY_DISPLAY_NAME", "Test Company")
    monkeypatch.setenv("COMPANY_LOGIN_NAME", "office")
    monkeypatch.setenv("COMPANY_LOGIN_PASSWORD", "s3cret")

    ad = tmp_path / "autodoc"
    (ad / "sub").mkdir(parents=True)
    (ad / "sub" / "hello.pdf").write_bytes(b"%PDF-1.4 minimal")

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
identity:
  enabled: false
api:
  base_url: "http://example.test"
open:
  browser_client_path: true
  local_root: "{ad_str}"
  unc_root: "\\\\\\\\fileserver\\\\AutoDocShare"
""",
        encoding="utf-8",
    )

    from web_interface import app as web_app

    monkeypatch.setattr(web_app, "KnovasAPIClient", DummyKnovasClient)
    monkeypatch.setattr(web_app, "AutoDocFileHandler", lambda: TmpAutodocHandler(ad))

    flask_app = web_app.create_app(str(config_path))
    client = flask_app.test_client()
    _login(client)

    resp = client.get("/api/document/doc-1/client-path?path=sub/hello.pdf")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "hello.pdf" in data["unc"]
