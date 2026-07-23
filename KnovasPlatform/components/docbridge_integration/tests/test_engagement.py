"""Tests for engagement analytics client + web proxy."""

import sys
from pathlib import Path

import pytest
import requests

SRC = Path(__file__).resolve().parents[1] / "src"
WEB_SRC = SRC / "web_interface"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(WEB_SRC) not in sys.path:
    sys.path.insert(0, str(WEB_SRC))

from knovas_client import KnovasAPIClient, _validate_and_normalize_tables
from test_knovas_client_hardening import FakeResponse, FakeSession, make_client, make_secured_client


def test_post_engagement_events_success():
    client = make_secured_client()

    def responder(method, url, **kw):
        assert method == "POST"
        assert "/secured/analytics/engagement" in url
        return FakeResponse(202, {"status": "success", "accepted": 1})

    client._session = FakeSession(responder)
    out = client.post_engagement_events(
        "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        [{"action": "view", "pointer": "doc-1", "position": 2}],
    )
    assert out["accepted"] == 1


def test_post_engagement_rejects_invalid_action():
    client = make_secured_client()
    with pytest.raises(ValueError, match="action"):
        client.post_engagement_events("session-id", [{"action": "hover", "pointer": "x"}])


def test_delete_information_object():
    client = make_secured_client()

    def responder(method, url, **kw):
        assert method == "DELETE"
        return FakeResponse(200, {"status": "success", "deleted_sentences": 3})

    client._session = FakeSession(responder)
    out = client.delete_information_object("doc-pointer")
    assert out["deleted_sentences"] == 3


def test_secured_query_multi_input_body():
    client = make_secured_client()
    body = client._secured_query_request_body(["Q3 revenue", "third quarter"])
    assert body["Input"] == ["Q3 revenue", "third quarter"]


def test_csr_renewal_installs_certificate(tmp_path, monkeypatch):
    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    ca = tmp_path / "ca.crt"
    cert.write_text("ORIGINAL CERT\n", encoding="utf-8")
    key.write_text("ORIGINAL KEY\n", encoding="utf-8")
    ca.write_text("CA\n", encoding="utf-8")

    client = make_client(
        base_url="https://knovas.test",
        cert_path=str(cert),
        key_path=str(key),
        ca_cert_path=str(ca),
        cert_renew_method="csr",
    )
    original_session = FakeSession(lambda *a, **k: FakeResponse(200))
    client._session = original_session

    monkeypatch.setattr(
        client,
        "_generate_csr_key_pair",
        lambda: ("-----BEGIN CERTIFICATE REQUEST-----\nMOCK\n-----END CERTIFICATE REQUEST-----\n", "NEW KEY"),
    )
    monkeypatch.setattr(
        client,
        "sign_certificate",
        lambda csr, **kw: {"certificate": "NEW CERT"},
    )
    monkeypatch.setattr(client, "_validate_renewed_certificate", lambda c, k: True)

    assert client._attempt_certificate_renewal() is True
    assert cert.read_text(encoding="utf-8").strip() == "NEW CERT"
    assert key.read_text(encoding="utf-8").strip() == "NEW KEY"
    assert client._session is not original_session

    tables = _validate_and_normalize_tables(
        [
            {
                "client_table_hint": "revenue",
                "headers": ["Region", "Amt"],
                "rows": [["EMEA", "12M"]],
            }
        ]
    )
    assert tables[0]["client_table_hint"] == "revenue"


def test_transmit_payload_includes_tables():
    from knovas_client import _secured_transmit_part_payload

    payload = _secured_transmit_part_payload(
        "key-1",
        0,
        {
            "snippet": "see table",
            "tables": [
                {
                    "client_table_hint": "t1",
                    "headers": ["A"],
                    "rows": [["1"]],
                }
            ],
        },
    )
    assert payload["tables"][0]["headers"] == ["A"]
