"""Tests for secured Knovas API client behaviour: object deletion, multi-input
query bodies, certificate renewal, and structured tables in transmit payloads.

These previously lived in test_engagement.py, whose name did not match most of
its contents; they were rescued when the engagement feature was removed.
"""

import pytest

from knovas_client import GraphError, _validate_and_normalize_tables, _secured_transmit_part_payload
from test_knovas_client_hardening import FakeResponse, FakeSession, make_client, make_secured_client


class _Call:
    def __init__(self, params=None, data=None):
        self.params = params or {}
        self.data = data


class _Capture:
    def __init__(self):
        self.last = _Call()


@pytest.fixture
def capture():
    return _Capture()


@pytest.fixture
def client(capture):
    """Secured client whose session records the last call and serves queued responses."""
    knovas = make_secured_client()
    queued = {"resp": FakeResponse(200, {})}

    def responder(method, url, **kw):
        capture.last = _Call(params=kw.get("params") or {}, data=kw.get("json"))
        return queued["resp"]

    knovas._session = FakeSession(responder)
    knovas._queued = queued
    return knovas


@pytest.fixture
def requests_mock(client):
    def _queue(*, status=200, json=None):
        client._queued["resp"] = FakeResponse(status, json or {})
    return _queue


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


class TestGraphError:
    def test_404_still_returns_none(self, client, requests_mock):
        requests_mock(status=404, json={"message": "Node not found"})
        assert client.graph_node("missing") is None

    def test_a_422_raises_with_its_error_code(self, client, requests_mock):
        requests_mock(status=422, json={"error_code": "identifier_limit_exceeded",
                                        "message": "Max 16"})
        with pytest.raises(GraphError) as caught:
            client.graph_node("n1")
        assert caught.value.status == 422
        assert caught.value.error_code == "identifier_limit_exceeded"

    def test_a_503_carries_its_code_so_a_route_can_say_retry(self, client, requests_mock):
        requests_mock(status=503, json={"error_code": "relevance_calibration_missing"})
        with pytest.raises(GraphError) as caught:
            client.graph_node("n1")
        assert caught.value.status == 503
        assert caught.value.error_code == "relevance_calibration_missing"

    def test_a_body_without_an_error_code_still_raises(self, client, requests_mock):
        requests_mock(status=500, json={})
        with pytest.raises(GraphError) as caught:
            client.graph_node("n1")
        assert caught.value.status == 500 and caught.value.error_code is None
