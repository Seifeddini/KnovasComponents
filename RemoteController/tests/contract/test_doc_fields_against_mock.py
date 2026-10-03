"""The RemoteController's document-fields uploads against the mock Knovas API.

The mock (``KnovasPlatform/mock_knovas_api``) plays every server state:
``off`` (an old server, or the feature off for the tenant), ``values``,
``filters`` and ``filters`` without calibration, plus a forced init refusal
and a BROKERED tenant. ``requests.request`` -- what the uploader calls -- is
answered in-process by the mock (``testing.wsgi_request``).

Acceptance (spec 7, WP-RC2): against the mock in ``off`` mode the init body
of a source without fields is byte-identical to the body before fields.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from util.schema import validate

MOCK_DIR = Path(__file__).resolve().parents[3] / "KnovasPlatform" / "mock_knovas_api"
TESTING = MOCK_DIR / "testing.py"

if not TESTING.is_file():  # pragma: no cover - a checkout without the mock
    pytest.skip("KnovasPlatform/mock_knovas_api/testing.py is not in this checkout",
                allow_module_level=True)

_spec = importlib.util.spec_from_file_location("knovas_mock_testing", TESTING)
testing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(testing)

INIT = "/secured/init_document_transmission"
FIXED_MTIME = 1_700_000_000
REL = "Muster AG/GJ 2024/Rechnung_17.txt"
POINTER = f"rc-sync/{REL}"
SOURCE_FIELDS = {"fields": {"doc_type": "Rechnung", "doctype": "invoice"},
                 "field_templates": ["{party}/{period}/**"]}


def _today_body(rel: str, *, title: str, access_groups=None) -> bytes:
    """The init body exactly as the RemoteController sent it before document
    fields existed, as `requests` encodes `json=`."""
    body: dict[str, Any] = {"identifier": f"rc-sync/{rel}", "part_count": 1, "title": title,
                            "path": rel}
    if access_groups:
        body["access_groups"] = list(access_groups)
    return json.dumps(body).encode("utf-8")


class Rig:
    def __init__(self, root: Path, state_path: Path, monkeypatch):
        self.root = root
        self.state_path = state_path
        self.monkeypatch = monkeypatch
        self.app = None

    def mock(self, **kw: Any):
        self.app = testing.load_mock_app(**kw)
        self.monkeypatch.setattr("sync.knovas_uploader.requests.request", testing.wsgi_request(self.app))
        return testing.mock_state(self.app)

    @property
    def state(self):
        return testing.mock_state(self.app)

    def init_requests(self) -> list[dict]:
        return [r for r in self.state.requests if r["path"] == INIT]

    def write(self, rel: str, text: str = "Rechnung fuer Beratung") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.utime(path, (FIXED_MTIME, FIXED_MTIME))
        return path

    def body(self, **source: Any) -> dict:
        return {
            "mode": "incremental",
            "sources": [{"path": str(self.root), "recursive": True, **source}],
            "filters": {"include_globs": ["*.txt", "*.eml"]},
            "ingestion": {"identifier_prefix": "rc-sync"},
        }

    def run(self, body: dict):
        from sync.knovas_uploader import SemantixUploader
        from sync.sync_executor import run_sync_work

        return run_sync_work(body, SemantixUploader())

    def fields(self, rel: str):
        from sync.sync_state import SyncStateStore

        store = SyncStateStore(str(self.state_path))
        try:
            return store.fields_state(rel)
        finally:
            store.close()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from config import load_config, reset_config
    from sync.ingest_rate_limit import configure

    root = tmp_path / "share"
    root.mkdir()
    state_path = tmp_path / "state" / ".rc-sync-state.json"
    monkeypatch.setenv("RC_WATCH_ROOTS", str(root))
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("SEMANTIX_CERT_AUTO_RENEW_ENABLED", "false")
    for key in ("M365_FOLDER_URL", "RC_DOC_FIELDS", "RC_FIELDS_REUPLOAD_PER_CYCLE",
                "RC_FIELDS_REUPLOAD_MAX_ATTEMPTS"):
        monkeypatch.delenv(key, raising=False)
    reset_config()
    load_config(validate=False, force_reload=True)
    configure(60000, 1000)
    monkeypatch.setattr("sync.knovas_uploader.write_context_sidecar", lambda *a, **k: None)
    monkeypatch.setattr("sync.knovas_uploader.time.sleep", lambda s: None)
    yield Rig(root, state_path, monkeypatch)
    reset_config()


class TestOffMode:
    def test_init_body_without_fields_is_byte_identical(self, rig):
        rig.mock(doc_fields="off")
        rig.write(REL)
        result = rig.run(rig.body(access_groups=["Mandate"]))
        assert result.files_uploaded == 1 and result.errors == []
        (init,) = rig.init_requests()
        assert init["body"] == _today_body(REL, title="Rechnung_17.txt", access_groups=["Mandate"])
        assert "fields" not in result.transmissions[0]

    def test_fields_are_ignored_and_reported_not_accepted(self, rig):
        rig.mock(doc_fields="off")
        rig.write(REL)
        result = rig.run(rig.body(**SOURCE_FIELDS))
        (init,) = rig.init_requests()
        assert init["json"]["fields"] == {"doc_type": "Rechnung", "doctype": "invoice",
                                          "party": "Muster AG", "period": "GJ 2024"}
        today = json.loads(_today_body(REL, title="Rechnung_17.txt"))
        assert {k: v for k, v in init["json"].items() if k != "fields"} == today
        assert result.transmissions[0]["fields"]["outcome"] == "not_accepted"
        assert rig.fields(REL).outcome == "not_accepted"
        assert rig.run(rig.body(**SOURCE_FIELDS)).files_uploaded == 0, "no loop on an old server"


@pytest.mark.parametrize("mode,calibrated", [("values", True), ("filters", True), ("filters", False)])
def test_fields_are_staged_with_the_echo(rig, mode, calibrated):
    rig.mock(doc_fields=mode, calibrated=calibrated)
    rig.write(REL)
    result = rig.run(rig.body(**SOURCE_FIELDS))
    tx = result.transmissions[0]
    assert tx["fields"]["outcome"] == "staged"
    assert tx["fields"]["staged"] == 3  # doc_type, party, period; "doctype" is unknown
    assert tx["fields"]["warning_codes"] == ["unresolved_entity"]
    assert result.doc_fields.unknown_keys == ["doctype"]
    assert result.doc_fields.suggest == {"doctype": ["doc_type"]}
    upload = rig.state.anchors[POINTER]["upload"]
    assert upload["doc_type"]["values"] == ["invoice"]
    # Knovas stores the period it parsed from the folder name.
    assert upload["period"]["values"] == [{"lo": "2024-01-01", "hi": "2024-12-31", "label": "2024"}]
    assert rig.fields(REL).outcome == "staged" and rig.fields(REL).sent
    assert validate(_sync_response(result), "sync_response.schema.json") == []
    assert rig.run(rig.body(**SOURCE_FIELDS)).files_uploaded == 0


def test_clearing_the_configuration_clears_the_upload_layer(rig):
    rig.mock(doc_fields="values")
    rig.write(REL)
    rig.run(rig.body(**SOURCE_FIELDS))
    assert rig.state.anchors[POINTER]["upload"]
    result = rig.run(rig.body())
    assert result.document_sync.fields_changed == 1
    assert rig.init_requests()[-1]["json"]["fields"] == {}
    assert rig.state.anchors[POINTER]["upload"] == {}
    assert rig.fields(REL).outcome == "cleared"


@pytest.mark.parametrize("refusal,expected,transient", [
    ("422:unknown_field", "refused:unknown_field", False),
    ("400:invalid_fields", "refused:invalid_fields", False),
    ("503:doc_fields_unavailable", "refused:doc_fields_unavailable", True),
    ("503:doc_fields_ingest_unavailable", "refused:doc_fields_ingest_unavailable", True),
])
def test_a_forced_refusal_indexes_the_document_without_fields(rig, refusal, expected, transient):
    rig.mock(doc_fields="values")
    rig.write(REL)
    rig.run(rig.body(fields={"doc_type": "contract"}))
    before = dict(rig.state.anchors[POINTER]["upload"])
    rig.state.refuse = tuple((int(refusal.split(":")[0]), refusal.split(":")[1]))
    result = rig.run(rig.body(fields={"doc_type": "invoice"}))
    inits = rig.init_requests()[-2:]
    assert "fields" in inits[0]["json"] and "fields" not in inits[1]["json"]
    assert result.files_uploaded == 1
    assert result.transmissions[0]["fields"]["outcome"] == expected
    assert rig.state.anchors[POINTER]["upload"] == before, "previous upload values survive"
    assert validate(_sync_response(result), "sync_response.schema.json") == []
    again = rig.run(rig.body(fields={"doc_type": "invoice"}))
    assert again.files_uploaded == (1 if transient else 0)


class TestBrokered:
    def test_entity_values_without_assertion_stay_unlinked(self, rig):
        """Server S1: the RemoteController sends no assertion, so Knovas keeps
        its entity names unlinked instead of refusing the upload."""
        rig.mock(doc_fields="values", brokered=True)
        rig.write(REL)
        result = rig.run(rig.body(**SOURCE_FIELDS))
        assert result.files_uploaded == 1
        tx = result.transmissions[0]
        assert tx["fields"]["outcome"] == "staged"
        assert tx["fields"]["warning_codes"] == ["unresolved_entity"]
        assert rig.state.anchors[POINTER]["upload"]["party"]["values"] == [{"name": "Muster AG"}]
        assert len(rig.init_requests()) == 1

    def test_entity_values_without_assertion_fall_back_before_s1(self, rig):
        state = rig.mock(doc_fields="values", brokered=True)
        state.s1 = False
        rig.write(REL)
        result = rig.run(rig.body(**SOURCE_FIELDS))
        assert result.files_uploaded == 1
        assert result.transmissions[0]["fields"]["outcome"] == "refused:assertion_rejected"
        assert rig.fields(REL).outcome == "refused:assertion_rejected"

    def test_access_groups_401_is_an_init_failure_not_a_refusal(self, rig):
        rig.mock(doc_fields="values", brokered=True)
        rig.write(REL)
        result = rig.run(rig.body(access_groups=["Mandate"], **SOURCE_FIELDS))
        assert result.files_uploaded == 0 and result.files_retry == 1
        assert result.errors == [{"path": REL, "error": "init failed: 401"}]
        assert len(rig.init_requests()) == 2
        assert rig.fields(REL) is None, "no digest stored"


def test_the_server_starting_to_accept_requeues_not_accepted(rig):
    state = rig.mock(doc_fields="off")
    rig.write(REL)
    rig.run(rig.body(**SOURCE_FIELDS))
    assert rig.fields(REL).outcome == "not_accepted"
    state.mode = "values"
    rig.write("Beispiel GmbH/GJ 2025/Neu.txt")
    first = rig.run(rig.body(**SOURCE_FIELDS))
    assert first.doc_fields.requeued == 1
    second = rig.run(rig.body(**SOURCE_FIELDS))
    assert second.document_sync.fields_changed == 1
    assert rig.fields(REL).outcome == "staged"
    assert rig.state.anchors[POINTER]["upload"]["period"]["values"] == [{"lo": "2024-01-01", "hi": "2024-12-31", "label": "2024"}]


def test_mail_metadata_reaches_the_server(rig):
    rig.mock(doc_fields="values")
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "sample.eml"
    target = rig.root / "Postfach" / "sample.eml"
    target.parent.mkdir()
    target.write_bytes(fixture.read_bytes())
    rig.run(rig.body(metadata_fields=["email_date", "email_doc_type", "email_author"]))
    (init,) = rig.init_requests()
    assert init["json"]["fields"]["doc_type"] == "correspondence.email"
    assert init["json"]["fields"]["author"] == "sender@example.com"
    assert "document_date" in init["json"]["fields"]


def _sync_response(result) -> dict:
    from routes.sync import _build_sync_response

    return _build_sync_response("completed", result)


# --- the HTTP routes ----------------------------------------------------------------


@pytest.fixture
def employee(rc_client, rig, monkeypatch):
    from config import load_config, reset_config

    monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "false")
    monkeypatch.setenv("RC_WATCH_ROOTS", str(rig.root))
    reset_config()
    load_config(validate=False, force_reload=True)
    import sync.sync_scheduler as scheduler

    for name, value in (("_last_doc_fields", None), ("_last_fields_changed", None),
                        ("_fields_requeued_since_scan", 0)):
        monkeypatch.setattr(scheduler, name, value)
    one_time = {"enabled": True, "mode": "one_time",
                "window": {"start_local": "00:00", "end_local": "23:59"},
                "rate_limit": {"max_ingestion_requests_per_minute": 6000, "burst": 1000}}
    monkeypatch.setattr("routes.sync.load_sync_config", lambda: one_time)
    monkeypatch.setattr("sync.sync_scheduler.is_in_window", lambda *a, **k: True)
    with patch("auth.knovas_verify_client.get_verify_client") as client:
        client.return_value.verify_operator.return_value = (True, "c", None)
        yield rc_client


class TestRoutes:
    def test_one_time_sync_with_fields_changed_validates(self, rig, employee, auth_headers):
        rig.mock(doc_fields="values")
        rig.write(REL)
        first = employee.post("/sync", json=rig.body(**SOURCE_FIELDS), headers=auth_headers)
        assert first.status_code == 200, first.get_json()
        assert first.get_json()["transmissions"][0]["fields"]["outcome"] == "staged"
        changed = rig.body(fields={"doc_type": "contract"}, field_templates=["{party}/**"])
        resp = employee.post("/sync", json=changed, headers=auth_headers)
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()
        assert data["document_sync"]["fields_changed"] == 1
        assert data["transmissions"][0]["fields"] == {"outcome": "staged", "staged": 2,
                                                      "warning_codes": ["unresolved_entity"]}
        assert data["doc_fields"]["last_cycle"]["staged"] == 1
        assert validate(data, "sync_response.schema.json") == []

    def test_status_advertises_capabilities_and_the_doc_fields_block(self, rig, employee, auth_headers):
        rig.mock(doc_fields="values")
        rig.write(REL)
        employee.post("/sync", json=rig.body(**SOURCE_FIELDS), headers=auth_headers)
        status = employee.get("/sync/status", headers=auth_headers).get_json()
        assert status["capabilities"] == ["source_fields_v1", "field_templates_v1",
                                          "metadata_fields_v1", "fields_requeue_v1"]
        block = status["doc_fields"]
        assert block["enabled"] is True and block["server"] == "accepted"
        assert block["per_cycle"] == 100
        assert block["documents"]["with_fields"] == 1
        assert block["last_cycle"]["staged"] == 1
        assert block["warnings"] == [{"code": "unresolved_entity", "key": "party", "count": 1}]
        assert block["unknown_keys"] == ["doctype"]
        assert block["suggest"] == {"doctype": ["doc_type"]}
        assert block["template_errors"] == {"field_template_invalid": 0}
        assert "Muster AG" not in json.dumps(status) and "GJ 2024" not in json.dumps(status)

    def test_status_says_not_accepted_on_an_old_server(self, rig, employee, auth_headers):
        rig.mock(doc_fields="off")
        rig.write(REL)
        employee.post("/sync", json=rig.body(**SOURCE_FIELDS), headers=auth_headers)
        block = employee.get("/sync/status", headers=auth_headers).get_json()["doc_fields"]
        assert block["server"] == "not_accepted"
        assert block["documents"]["not_accepted"] == 1 and block["documents"]["with_fields"] == 0

    def test_requeue_endpoint(self, rig, employee, auth_headers):
        state = rig.mock(doc_fields="off")
        rig.write(REL)
        employee.post("/sync", json=rig.body(**SOURCE_FIELDS), headers=auth_headers)
        bad = employee.post("/sync/doc-fields/requeue", json={"outcome": "staged"}, headers=auth_headers)
        assert bad.status_code == 400
        assert employee.post("/sync/doc-fields/requeue", json={}, headers=auth_headers).status_code == 400
        resp = employee.post("/sync/doc-fields/requeue", json={"outcome": "not_accepted"},
                             headers=auth_headers)
        assert resp.status_code == 200 and resp.get_json() == {"requeued": 1}
        block = employee.get("/sync/status", headers=auth_headers).get_json()["doc_fields"]
        assert block["documents"]["pending_reupload"] == 1
        state.mode = "values"
        again = employee.post("/sync", json=rig.body(**SOURCE_FIELDS), headers=auth_headers).get_json()
        assert again["document_sync"]["fields_changed"] == 1
        assert again["transmissions"][0]["fields"]["outcome"] == "staged"

    def test_requeue_is_gated_like_start(self, rc_client):
        resp = rc_client.post("/sync/doc-fields/requeue", json={"outcome": "all"})
        assert resp.status_code in (401, 403, 429)

    def test_schema_errors_under_the_new_keys_carry_no_values(self, rig, employee, auth_headers):
        sentinel = "Muster AG Geheim " + "x" * 300
        cases = [
            {"fields": {"mandant": sentinel}},
            {"fields": {sentinel[:40].replace(" ", "_"): "x"}},
            {"field_templates": [sentinel + "/**", "{a}/" + "y" * 600]},
            {"metadata_fields": [sentinel]},
        ]
        for source_extra in cases:
            resp = employee.post("/sync/body", json=rig.body(**source_extra), headers=auth_headers)
            assert resp.status_code == 400
            error = resp.get_json()["error"]
            assert "Muster" not in error and "Geheim" not in error and "xxxx" not in error, error
            assert error.startswith("$.sources[0].")
