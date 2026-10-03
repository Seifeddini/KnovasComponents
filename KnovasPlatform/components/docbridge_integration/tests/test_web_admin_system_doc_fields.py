"""System tab: the document-fields line and the RemoteController's capabilities.

The System tab is the one place that names a reduced document-fields state
(H6), so each of the four states is pinned here, with its wording. The
checks run against FakeDocFieldsApi directly; no app, no database.
"""

from __future__ import annotations

import pytest

from conftest import DummyKnovasClient
from doc_fields_fakes import FakeDocFieldsApi


def _doc_fields(checks):
    return next(c for c in checks if c["key"] == "doc_fields")


def _rc(checks):
    return next(c for c in checks if c["key"] == "rc")


def _collect(client, rc=None):
    from web_interface import admin_system

    return admin_system.collect(lambda: client,
                                rc_client_factory=(lambda: rc) if rc is not None else None)


@pytest.fixture(autouse=True)
def _healthy(monkeypatch):
    monkeypatch.setattr(DummyKnovasClient, "health_result", True)


class TestFourStates:
    def test_off(self):
        check = _doc_fields(_collect(FakeDocFieldsApi("off")))
        assert check["detail"] == "aus" and check["state"] == "skip"
        assert "nicht freigeschaltet" in check["hint"]

    def test_values(self):
        check = _doc_fields(_collect(FakeDocFieldsApi("values")))
        assert check["state"] == "ok"
        assert check["detail"].startswith("Werte (ohne Filter); ")
        assert "12 Feld(er)" in check["detail"] and "Pakete: core v1" in check["detail"]

    def test_listing_only_says_filters_are_temporarily_unavailable(self):
        import doc_fields_capability as dfc

        client = FakeDocFieldsApi("listing_only")
        dfc.capability_for(client)
        dfc.observe("needs_calibration")
        check = _doc_fields(_collect(client))
        assert check["state"] == "warn"
        assert check["detail"].startswith(
            "Werte + Liste (Feldfilter bei Knovas vor\u00fcbergehend nicht verf\u00fcgbar)")
        assert "vor\u00fcbergehend nicht verf\u00fcgbar" in check["hint"]
        assert "sp\u00e4ter erneut versuchen" in check["hint"]
        assert "Kalibrierung" not in check["detail"] + check["hint"]

    def test_filters(self):
        check = _doc_fields(_collect(FakeDocFieldsApi("filters")))
        assert check["state"] == "ok" and check["detail"].startswith("Werte + Filter; ")


class TestWhyOff:
    def test_switched_off_in_the_configuration(self):
        class Config:
            def get(self, key, default=None):
                return "off" if key == "web.doc_fields.ui" else default

        client = FakeDocFieldsApi(Config(), "filters")
        check = _doc_fields(_collect(client))
        assert check["detail"] == "aus" and "web.doc_fields.ui" in check["hint"]
        assert client.probe_calls == 0

    def test_legacy_mode(self):
        client = DummyKnovasClient(None)
        check = _doc_fields(_collect(client))
        assert check["detail"] == "aus" and "gesicherten" in check["hint"]

    def test_an_unclear_answer_is_not_called_off(self):
        client = FakeDocFieldsApi("values")
        client.probe_answer = "unknown"
        check = _doc_fields(_collect(client))
        assert check["state"] == "warn" and check["detail"] == "nicht feststellbar"

    def test_the_api_is_down(self, monkeypatch):
        monkeypatch.setattr(DummyKnovasClient, "health_result", False)
        check = _doc_fields(_collect(FakeDocFieldsApi("values")))
        assert check["state"] == "skip"

    def test_a_registry_read_failure_keeps_the_state_line(self):
        from knovas_client import DocFieldsError

        client = FakeDocFieldsApi("values")
        client.fail_call("doc_fields", DocFieldsError(503, "doc_fields_unavailable", "x"))
        check = _doc_fields(_collect(client))
        assert check["detail"].startswith("Werte (ohne Filter); Felder nicht lesbar")


class _RC:
    def __init__(self, caps=None):
        self._caps = caps

    def health(self):
        return {}


class _RCWithCaps(_RC):
    """A RemoteController whose /sync/status (what health() returns) lists
    its capabilities, as the real one does."""

    def health(self):
        return {"capabilities": sorted(self._caps or ())}

    def capabilities(self):
        return frozenset(self._caps or ())


class _BusyRC(_RCWithCaps):
    """The ping answered; a second status request would time out."""

    def capabilities(self):
        raise TimeoutError("RemoteController busy")

    def reachable_capabilities(self):
        return None


class TestRemoteController:
    def test_a_new_remote_controller_reports_its_field_capabilities(self):
        rc = _RCWithCaps({"source_fields_v1", "field_templates_v1", "fields_requeue_v1",
                          "something_else"})
        check = _rc(_collect(FakeDocFieldsApi("values"), rc))
        assert check["state"] == "ok"
        assert check["detail"] == ("antwortet; Dokumentfelder: source_fields_v1, "
                                   "field_templates_v1, fields_requeue_v1")

    def test_an_old_remote_controller_is_called_out_while_fields_are_on(self):
        check = _rc(_collect(FakeDocFieldsApi("values"), _RC()))
        assert check["state"] == "warn"
        assert "nicht unterstuetzt" in check["detail"]
        assert "RemoteController aktualisieren" in check["hint"]

    def test_the_ping_answer_decides_not_a_second_request(self):
        """platform-admin-ingestion-5: a status request that fails after the
        ping succeeded never turns a current RC into one "too old"."""
        rc = _BusyRC({"source_fields_v1", "field_templates_v1"})
        check = _rc(_collect(FakeDocFieldsApi("values"), rc))
        assert check["state"] == "ok"
        assert check["detail"] == "antwortet; Dokumentfelder: source_fields_v1, field_templates_v1"

    def test_with_fields_off_an_old_remote_controller_reads_as_before(self):
        check = _rc(_collect(FakeDocFieldsApi("off"), _RCWithCaps(set())))
        assert check["state"] == "ok" and check["detail"] == "antwortet"
