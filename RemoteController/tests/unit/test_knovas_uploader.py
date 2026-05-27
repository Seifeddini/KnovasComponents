from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sync.knovas_uploader import SemantixUploader


@pytest.fixture
def mock_config(monkeypatch):
    cfg = MagicMock()
    cfg.semantix_secure_base_url = "https://api.example:8443"
    cfg.semantix_client_cert_path = "/c/cert.pem"
    cfg.semantix_client_key_path = "/c/key.pem"
    cfg.semantix_ca_cert_path = "/c/ca.pem"
    monkeypatch.setattr("sync.knovas_uploader.get_config", lambda: cfg)
    return cfg


def _ok_response(key: str = "tx-key-1"):
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b'{"key": "' + key.encode() + b'"}'
    resp.json.return_value = {"key": key}
    return resp


def test_upload_uses_original_identifier_and_converted_text(mock_config, tmp_path):
    md_file = tmp_path / "brief.txt"
    md_file.write_text("Ingested markdown body", encoding="utf-8")

    uploader = SemantixUploader()
    sync_body = {"ingestion": {"identifier_prefix": "corpus", "part_max_chars": 50000}}

    with patch.object(uploader, "_request") as req:
        req.side_effect = [_ok_response(), _ok_response()]
        result = uploader.upload_file(md_file, "akten/brief.txt", sync_body)

    assert result.status == "ok"
    assert result.transmission_key_id == "tx-key-1"
    init_call = req.call_args_list[0]
    assert init_call[0] == ("POST", "/secured/init_document_transmission")
    init_json = init_call.kwargs["json_body"]
    assert init_json["identifier"] == "corpus/akten/brief.txt"
    assert init_json["path"] == "akten/brief.txt"
    assert init_json["title"] == "brief.txt"

    part_call = req.call_args_list[1]
    part_json = part_call.kwargs["json_body"]
    assert "Ingested markdown body" in part_json["snippet"]


def test_upload_conversion_error(mock_config, tmp_path):
    bad = tmp_path / "empty.pdf"
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    doc.new_page()
    bad.write_bytes(doc.tobytes())
    doc.close()

    uploader = SemantixUploader()
    sync_body = {"ingestion": {"identifier_prefix": "rc"}}

    with patch.object(uploader, "_request") as req:
        result = uploader.upload_file(bad, "empty.pdf", sync_body)

    assert result.status == "error"
    assert result.parts == 0
    req.assert_not_called()
