"""No field value, capture or metadata value in logs or metric labels (spec 3.10, 6).

Sentinel values travel through every document-fields path the RC takes --
staged, not accepted, refused and re-posted, a transient refusal that ends
in ``reupload_failed``, a template that does not compile, a requeue -- and
must never appear in a log record or in a ``rc_doc_fields_*`` metric label.
Only keys, codes and counts may leave the data path.

Pre-existing logs that name a relative path on a failure (``Upload failed
path=...``) are outside this work (spec 8), so path captures are checked on
the success and fallback paths and static values everywhere.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import pytest
import requests

INIT = "/secured/init_document_transmission"
PART = "/secured/transmit_document_part"
FIXED_MTIME = 1_700_000_000

STATIC = "Treuhand Sentinel Zahl 4711"
CAPTURE = "Kunde Sentinel Muster"
PERIOD = "Periode Sentinel 2031"
AUTHOR = "Absender Sentinel Name"
TEMPLATE_LITERAL = "Vorlage Sentinel"
SENTINELS = (STATIC, CAPTURE, PERIOD, AUTHOR, TEMPLATE_LITERAL, "Sentinel")

EML = (
    f"From: {AUTHOR} <absender@example.com>\r\n"
    "To: empfaenger@example.com\r\n"
    "Subject: Rechnung\r\n"
    "Date: Mon, 1 Jan 2024 12:00:00 +0000\r\n"
    "Content-Language: de-CH\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n\r\n"
    "Guten Tag, anbei die Rechnung.\r\n"
)


def _response(status: int, body: Any) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = json.dumps(body).encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    return resp


class Server:
    def __init__(self) -> None:
        self.mode = "values"
        self.refuse: Optional[requests.Response] = None
        self.fail_all: Optional[requests.Response] = None

    def __call__(self, method: str, url: str, json: Optional[dict] = None, **_: Any):
        path = "/" + url.split("/", 3)[3]
        body = json or {}
        if path == INIT:
            if self.fail_all is not None:
                return self.fail_all
            if "fields" in body and self.refuse is not None:
                return self.refuse
            out: dict[str, Any] = {"status": "success", "transmission_key_id": "tk"}
            if self.mode == "values" and "fields" in body:
                fields = body["fields"]
                out["fields"] = {
                    "staged": len(fields),
                    "mapped_keys": {k: k for k in fields},
                    "unknown_keys": ["mandat"],
                    "warnings": [{"key": k, "path": f"fields.{k}", "code": "unresolved_entity"}
                                 for k in fields] + [{"key": "x", "path": "fields.x", "code": STATIC}],
                    "suggest": {"mandat": ["mandant", STATIC]},
                }
            return _response(201, out)
        if path == PART:
            return _response(200, {"status": "success"})
        return _response(200, {"status": "success"})


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from config import load_config, reset_config
    from sync.ingest_rate_limit import configure

    root = tmp_path / "share"
    root.mkdir()
    monkeypatch.setenv("RC_WATCH_ROOTS", str(root))
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(tmp_path / "state" / ".rc-sync-state.json"))
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("SEMANTIX_CERT_AUTO_RENEW_ENABLED", "false")
    for key in ("M365_FOLDER_URL", "RC_DOC_FIELDS", "RC_FIELDS_REUPLOAD_MAX_ATTEMPTS"):
        monkeypatch.delenv(key, raising=False)
    reset_config()
    load_config(validate=False, force_reload=True)
    configure(60000, 1000)
    monkeypatch.setattr("sync.knovas_uploader.write_context_sidecar", lambda *a, **k: None)
    monkeypatch.setattr("sync.knovas_uploader.time.sleep", lambda s: None)
    server = Server()
    monkeypatch.setattr("sync.knovas_uploader.requests.request", server)
    yield root, server
    reset_config()


def _write(path, text="Rechnung fuer Beratung"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (FIXED_MTIME, FIXED_MTIME))


def _body(root, **source):
    return {
        "mode": "incremental",
        "sources": [{"path": str(root), "recursive": True, **source}],
        "filters": {"include_globs": ["*.txt", "*.eml"]},
        "ingestion": {"identifier_prefix": "rc-sync"},
    }


def _run(body):
    from sync.knovas_uploader import SemantixUploader
    from sync.sync_executor import run_sync_work

    return run_sync_work(body, SemantixUploader())


def _assert_clean(caplog, *sentinels):
    text = "\n".join(
        f"{r.name} {r.getMessage()} {r.exc_text or ''}" for r in caplog.records
    )
    for sentinel in sentinels:
        assert sentinel not in text, f"a value reached the log: {sentinel!r}"


def _assert_metric_labels_closed(*sentinels):
    prometheus_client = pytest.importorskip("prometheus_client")
    from sync import doc_fields_metrics

    allowed = doc_fields_metrics.label_sets()
    seen = 0
    for metric in prometheus_client.REGISTRY.collect():
        if not metric.name.startswith("rc_doc_fields_"):
            continue
        family = metric.name if metric.name.endswith("_total") else metric.name + "_total"
        for sample in metric.samples:
            for value in sample.labels.values():
                seen += 1
                assert value in allowed[family], (family, value)
                for sentinel in sentinels:
                    assert sentinel not in value
    assert seen, "the counters were exercised"


def test_success_and_fallback_paths_log_codes_and_counts_only(rig, caplog):
    root, server = rig
    caplog.set_level(logging.DEBUG)
    _write(root / CAPTURE / PERIOD / "Rechnung.txt")
    _write(root / "Postfach" / "mail.eml", EML)
    source = {
        "fields": {"doc_type": STATIC, "keywords": [STATIC, "x" * 300]},
        "field_templates": ["{party}/{period}/**"],
        "metadata_fields": ["email_author", "email_date", "language"],
    }
    first = _run(_body(root, **source))
    assert first.files_uploaded == 2
    assert first.doc_fields.outcomes["staged"] == 2

    # The server is off: not accepted.
    server.mode = "off"
    _write(root / CAPTURE / PERIOD / "Zweite.txt")
    _run(_body(root, **source))

    # A refusal the RC falls back from.
    server.refuse = _response(422, {"status": "error", "error_code": "unknown_field",
                                    "path": "fields.doc_type", "error": "Unknown field"})
    _write(root / CAPTURE / PERIOD / "Dritte.txt")
    _run(_body(root, **source))

    # A requeue, and a template that does not compile.
    from sync.sync_scheduler import requeue_doc_fields

    requeue_doc_fields("all")
    _run(_body(root, field_templates=[f"{TEMPLATE_LITERAL}/{{a}}/{{a}}"]))

    _assert_clean(caplog, *SENTINELS)
    assert any("doc_fields outcome=staged" in r.getMessage() for r in caplog.records)
    assert any("field_template_invalid (duplicate_key)" in r.getMessage() for r in caplog.records)
    _assert_metric_labels_closed(*SENTINELS)


def test_failure_paths_never_log_static_values(rig, caplog):
    root, server = rig
    caplog.set_level(logging.DEBUG)
    _write(root / "a.txt")
    body = _body(root, fields={"doc_type": STATIC})
    _run(body)
    changed = _body(root, fields={"doc_type": STATIC + " neu"})
    server.fail_all = _response(403, {"status": "error", "error_code": "group_not_dominated",
                                      "error": STATIC})
    for _ in range(3):
        _run(changed)
    server.fail_all = None
    server.refuse = _response(503, {"status": "error", "error_code": "doc_fields_unavailable"})
    _write(root / "b.txt")
    for _ in range(4):
        _run(_body(root, fields={"doc_type": STATIC + " drei"}))
    assert any("reupload_failed:init_403" in r.getMessage() for r in caplog.records)
    assert any("reupload_failed:fields_unavailable" in r.getMessage() for r in caplog.records)
    _assert_clean(caplog, STATIC, "Sentinel")


def test_labels_outside_the_closed_sets_become_other():
    from sync import doc_fields_metrics as m

    assert m.outcome_family("refused:unknown_field") == "refused"
    assert m.outcome_family("reupload_failed:init_403") == "reupload_failed"
    assert m.outcome_family("staged") == "staged"
    assert m.outcome_family(STATIC) == "other"
    assert m.closed(STATIC, m.WARNING_CODES) == "other"
    assert m.closed("unresolved_entity", m.WARNING_CODES) == "unresolved_entity"
    assert m.closed(None, m.REFUSAL_CODES) == "other"
    assert m.closed("too_large", m.DROP_REASON_LABELS) == "too_large"
    for labels in m.label_sets().values():
        assert "other" in labels
        assert all(isinstance(v, str) and v.replace("_", "").isalnum() for v in labels)
