"""``POST /sync/reextract/requeue`` and the ``extraction`` counts of
``GET /sync/status`` (spec L6): the gate of the doc-fields requeue, counts
only -- never a path -- and a worker that does not idle while
re-extractions wait."""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from sync.extraction_stamp import current_extraction_stamp
from sync.sync_state import SyncStateStore

TS = "2026-10-01T00:00:00Z"
REL = "Muster AG/GJ 2024/Rechnung_17.pdf"


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    from config import load_config, reset_config

    path = tmp_path / "state" / ".rc-sync-state.json"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(path))
    monkeypatch.delenv("RC_REEXTRACT_PER_CYCLE", raising=False)
    reset_config()
    load_config(validate=False, force_reload=True)
    return path


@pytest.fixture
def employee(rc_client, state_path, monkeypatch):
    from config import load_config, reset_config

    monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "false")
    reset_config()
    load_config(validate=False, force_reload=True)
    with patch("auth.knovas_verify_client.get_verify_client") as client:
        client.return_value.verify_operator.return_value = (True, "c", None)
        yield rc_client


def _seed(state_path, *rels: str) -> None:
    store = SyncStateStore(str(state_path))
    try:
        for rel in rels:
            store.record_upload(rel, TS, 1, "tk")
    finally:
        store.close()


def _extraction(client, headers) -> dict:
    return client.get("/sync/status", headers=headers).get_json()["extraction"]


def test_the_route_is_gated_like_the_doc_fields_requeue(rc_client):
    assert rc_client.post("/sync/reextract/requeue", json={}).status_code in (401, 403, 429)


def test_requeue_queues_every_outdated_document_once_and_wakes_the_worker(
    employee, auth_headers, state_path, caplog
):
    caplog.set_level(logging.INFO)
    _seed(state_path, REL, "b.docx")
    with patch("routes.sync_control.request_cycle_now") as wake:
        first = employee.post("/sync/reextract/requeue", json={}, headers=auth_headers)
        again = employee.post("/sync/reextract/requeue", json={}, headers=auth_headers)
    assert first.status_code == 200 and first.get_json() == {"requeued": 2}
    assert again.status_code == 200 and again.get_json() == {"requeued": 0}, \
        "already queued documents are not counted twice"
    assert wake.call_count == 1, "only a request that queued something wakes the worker"
    assert "reextract requeued=2" in caplog.text
    assert "Muster" not in caplog.text and "b.docx" not in caplog.text


def test_the_status_counts_outdated_and_queued_documents_and_the_bound(
    employee, auth_headers, state_path
):
    _seed(state_path, REL, "b.docx", "c.txt")
    store = SyncStateStore(str(state_path))
    try:
        store.set_extraction("c.txt", current_extraction_stamp(), "0" * 64)
    finally:
        store.close()
    block = _extraction(employee, auth_headers)
    assert (block["outdated"], block["queued"], block["per_cycle"]) == (2, 0, 100)
    employee.post("/sync/reextract/requeue", json={}, headers=auth_headers)
    block = _extraction(employee, auth_headers)
    assert (block["outdated"], block["queued"]) == (2, 2)
    text = employee.get("/sync/status", headers=auth_headers).get_data(as_text=True)
    assert "Muster" not in text and "b.docx" not in text


def test_the_status_counts_documents_whose_re_extraction_was_kept(
    employee, auth_headers, state_path
):
    """Not sent because it would have missed more OCR pages than the text
    Knovas holds: no longer outdated, but counted -- never named."""
    _seed(state_path, REL, "b.docx")
    store = SyncStateStore(str(state_path))
    try:
        store.requeue_reextract(current_extraction_stamp())
        store.record_reextract_kept(REL, current_extraction_stamp())
    finally:
        store.close()
    block = _extraction(employee, auth_headers)
    assert (block["outdated"], block["queued"], block["kept"]) == (1, 1, 1)
    text = employee.get("/sync/status", headers=auth_headers).get_data(as_text=True)
    assert "Muster" not in text


def test_the_bound_is_the_connectors_setting(employee, auth_headers, monkeypatch):
    from config import load_config, reset_config

    monkeypatch.setenv("RC_REEXTRACT_PER_CYCLE", "250")
    reset_config()
    load_config(validate=False, force_reload=True)
    assert _extraction(employee, auth_headers)["per_cycle"] == 250


def test_the_platform_principal_of_an_admin_may_requeue_and_a_member_may_not(
    rc_client, state_path, tmp_path, monkeypatch
):
    """The Platform reaches the route as the signed-in person, exactly as
    it reaches /sync/doc-fields/requeue."""
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from tests.test_platform_principal import TENANT, mint

    private = ed25519.Ed25519PrivateKey.generate()
    pub = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pem = tmp_path / "broker_ed25519.pub"
    pem.write_bytes(pub)
    monkeypatch.setenv("RC_PLATFORM_BROKER_PUBKEY_PATH", str(pem))
    monkeypatch.setenv("RC_CLIENT_ID", TENANT)
    monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "false")
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    resp = rc_client.post("/sync/reextract/requeue", json={},
                          headers={"X-Platform-Principal": mint(private, pub)})
    assert resp.status_code == 200 and resp.get_json() == {"requeued": 0}
    resp = rc_client.post("/sync/reextract/requeue", json={},
                          headers={"X-Platform-Principal": mint(private, pub, rol=["member"])})
    assert resp.status_code == 403


def test_queued_re_extractions_keep_the_worker_from_idling(monkeypatch):
    import sync.sync_scheduler as scheduler
    from sync.sync_executor import SyncRunResult
    from sync.sync_state import DocumentSyncSummary

    monkeypatch.setattr(scheduler, "_idle_scan_multiplier", 1)
    idle = SyncRunResult(files_scanned=10, document_sync=DocumentSyncSummary(total=10, synced=10))
    busy = SyncRunResult(files_scanned=10, document_sync=DocumentSyncSummary(total=10, synced=10),
                         reextract_reached=3)
    assert scheduler._pending_work(idle) == 0 and scheduler._pending_work(busy) == 3
    cfg = {"scan_interval_seconds": 60, "scan_interval_idle_max_seconds": 3600}
    assert scheduler._effective_scan_interval_seconds(cfg, idle) == 120, "idle: backs off"
    assert scheduler._effective_scan_interval_seconds(cfg, busy) == 60, "re-extracting: no backoff"
