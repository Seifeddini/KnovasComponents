"""Tests fuer GET /api/document/<id>/preview-content.

Konventionen folgen tests/test_csrf_enforcement.py und tests/conftest.py.
"""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fixtures.make_msg import build_sample_msg  # noqa: E402


@pytest.fixture
def logged_in_client(docbridge_app, tmp_path, monkeypatch):
    """Angemeldeter Testclient, dessen AutoDoc-Wurzel auf tmp_path zeigt."""
    from web_interface import app as web_app

    monkeypatch.setattr(
        web_app.AutoDocFileHandler, "autodoc_path", str(tmp_path), raising=False
    )
    client = docbridge_app.test_client()
    with client.session_transaction() as session:
        session["company_login_ok"] = True
    return client


def test_preview_content_requires_login(docbridge_app, tmp_path):
    client = docbridge_app.test_client()
    response = client.get("/api/document/x/preview-content?path=a.txt")
    assert response.status_code == 401


def test_preview_content_requires_path(logged_in_client):
    response = logged_in_client.get("/api/document/x/preview-content")
    assert response.status_code == 400


def test_preview_content_rejects_traversal(logged_in_client):
    response = logged_in_client.get(
        "/api/document/x/preview-content?path=../../etc/passwd"
    )
    assert response.status_code == 400


def test_preview_content_missing_file(logged_in_client):
    response = logged_in_client.get("/api/document/x/preview-content?path=weg.txt")
    assert response.status_code == 404


def test_preview_content_unsupported_format(logged_in_client, tmp_path):
    (tmp_path / "bild.png").write_bytes(b"\x89PNG\r\n")
    response = logged_in_client.get("/api/document/x/preview-content?path=bild.png")
    assert response.status_code == 415


def test_preview_content_rejects_pdf(logged_in_client, tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4\n")
    response = logged_in_client.get("/api/document/x/preview-content?path=a.pdf")
    assert response.status_code == 415


def test_preview_content_returns_txt_markdown(logged_in_client, tmp_path):
    (tmp_path / "notiz.txt").write_text("Hallo Welt.\n", encoding="utf-8")
    response = logged_in_client.get("/api/document/x/preview-content?path=notiz.txt")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["kind"] == "txt"
    assert "Hallo Welt." in payload["markdown"]


def test_preview_content_returns_msg_metadata(logged_in_client, tmp_path):
    build_sample_msg(str(tmp_path / "mail.msg"))
    response = logged_in_client.get("/api/document/x/preview-content?path=mail.msg")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["kind"] == "msg"
    assert payload["meta"]["msg:from"] == "Anna Muster"
