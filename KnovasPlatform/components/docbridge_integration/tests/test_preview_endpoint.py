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


# --- EML und MD ------------------------------------------------------------
#
# RemoteController nimmt beide standardmaessig auf (_DEFAULT_INCLUDE_GLOBS), die
# Suche findet sie also -- und die Vorschau antwortete 415, weil sie nicht in
# PREVIEW_KIND_BY_SUFFIX standen. Bei einer Kanzlei sind E-Mails der groesste
# Teil des Bestands, das war damit der haeufigste Fehlerfall ueberhaupt.

SAMPLE_EML = (
    "From: meierhans@example.ch\r\n"
    "To: kanzlei@example.ch\r\n"
    "Subject: Schaffhauserstrasse 12\r\n"
    "Date: Fri, 15 Mar 2024 09:00:00 +0100\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "\r\n"
    "Sehr geehrte Damen und Herren, betreffend Akte 2024-017.\r\n"
)


def test_preview_content_returns_eml_markdown(logged_in_client, tmp_path):
    (tmp_path / "anfrage.eml").write_text(SAMPLE_EML, encoding="utf-8")
    response = logged_in_client.get("/api/document/x/preview-content?path=anfrage.eml")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["kind"] == "eml"
    assert "Akte 2024-017" in payload["markdown"]


def test_preview_content_carries_eml_mail_headers(logged_in_client, tmp_path):
    """Ohne diese Felder ist eine E-Mail in der Vorschau blosser Fliesstext:
    der Dialog zeigt Von und An nur, wenn sie in den Metadaten stehen."""
    (tmp_path / "anfrage.eml").write_text(SAMPLE_EML, encoding="utf-8")
    response = logged_in_client.get("/api/document/x/preview-content?path=anfrage.eml")
    payload = response.get_json()
    assert payload["meta"]["eml:from"] == "meierhans@example.ch"
    assert payload["meta"]["eml:to"] == "kanzlei@example.ch"


def test_preview_content_returns_md_markdown(logged_in_client, tmp_path):
    (tmp_path / "notiz.md").write_text(
        "# Aktennotiz\n\nBesprechung mit **Meierhans**.\n", encoding="utf-8"
    )
    response = logged_in_client.get("/api/document/x/preview-content?path=notiz.md")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["kind"] == "md"
    assert "Aktennotiz" in payload["markdown"]
