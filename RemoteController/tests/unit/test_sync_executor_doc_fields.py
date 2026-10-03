"""Per-source fields through the sync cycle (spec 3.2 and 3.7).

The executor hands each file its source's ``SourceSpec``, records a
``FieldsRecord`` with the governing digest on every outcome it records, and
re-sends documents whose fields configuration changed -- bounded per cycle,
after new and modified work, never looping. These tests run whole cycles
with the real uploader against a scripted Secure API.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Optional
from unittest.mock import MagicMock

import pytest
import requests

from sync.doc_fields_payload import FieldsRecord
from sync.sync_state import SyncStateStore
from sync.sync_state_db import REQUEUE_DIGEST

INIT = "/secured/init_document_transmission"
PART = "/secured/transmit_document_part"
DELETE = "/secured/delete_information_object"
FIXED_MTIME = 1_700_000_000


def _response(status: int, body: Any = None) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = b"" if body is None else json.dumps(body).encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    return resp


def echo_for(fields: dict) -> dict:
    return {"staged": len(fields), "mapped_keys": {k: k for k in fields}, "unknown_keys": [],
            "warnings": []}


class Server:
    """A scripted Secure API. ``mode``: ``values`` echoes fields, ``off``
    ignores them. ``refuse``: rel suffix -> answer for an init WITH fields;
    ``fail``: rel suffix -> answer for every init of that document."""

    def __init__(self, mode: str = "values"):
        self.mode = mode
        self.refuse: dict[str, requests.Response] = {}
        self.fail: dict[str, Callable[[], requests.Response]] = {}
        self.inits: list[dict] = []
        self.echo_warnings: list[dict] = []

    def rels(self) -> list[str]:
        return [b["path"] for b in self.inits]

    def __call__(self, method: str, url: str, json: Optional[dict] = None, **_: Any):
        path = "/" + url.split("/", 3)[3]
        body = json or {}
        if path == INIT:
            self.inits.append(body)
            rel = body.get("path", "")
            for suffix, answer in self.fail.items():
                if rel.endswith(suffix):
                    return answer()
            if "fields" in body:
                for suffix, answer in self.refuse.items():
                    if rel.endswith(suffix):
                        return answer
            out = {"status": "success", "transmission_key_id": f"tk-{len(self.inits)}"}
            if self.mode == "values" and "fields" in body:
                out["fields"] = dict(echo_for(body["fields"]), warnings=list(self.echo_warnings))
            return _response(201, out)
        if path == PART:
            return _response(200, {"status": "success", "transmission_complete": True})
        if path == DELETE:
            return _response(200, {"status": "success"})
        return _response(404, {"status": "error", "error_code": "HTTP_404"})


class Harness:
    def __init__(self, root: Path, state_path: Path, monkeypatch):
        self.root = root
        self.state_path = state_path
        self.monkeypatch = monkeypatch
        self.server = Server()

    def write(self, rel: str, text: str = "Rechnung fuer Beratung", *, root: Optional[Path] = None) -> Path:
        path = (root or self.root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.utime(path, (FIXED_MTIME, FIXED_MTIME))
        return path

    def source(self, path: Optional[Path] = None, **extra: Any) -> dict:
        return {"path": str(path or self.root), "recursive": True, **extra}

    def body(self, *sources: dict, mode: str = "incremental") -> dict:
        return {
            "mode": mode,
            "sources": list(sources) or [self.source()],
            "filters": {"include_globs": ["*.txt", "*.pdf", "*.md"]},
            "ingestion": {"identifier_prefix": "rc-sync"},
        }

    def run(self, body: dict, *, sync_config: Optional[dict] = None):
        from sync.knovas_uploader import SemantixUploader
        from sync.sync_executor import run_sync_work

        self.monkeypatch.setattr("sync.knovas_uploader.requests.request", self.server)
        self.server.inits.clear()
        return run_sync_work(body, SemantixUploader(), sync_config=sync_config)

    def state(self) -> SyncStateStore:
        return SyncStateStore(str(self.state_path))

    def fields(self, rel: str):
        store = self.state()
        try:
            return store.fields_state(rel)
        finally:
            store.close()

    def env(self, **values: str) -> None:
        from config import load_config, reset_config

        for key, value in values.items():
            self.monkeypatch.setenv(key, value)
        reset_config()
        load_config(validate=False, force_reload=True)


@pytest.fixture
def rc(tmp_path, monkeypatch):
    from config import load_config, reset_config
    from sync.ingest_rate_limit import configure

    root = tmp_path / "share"
    root.mkdir()
    state_path = tmp_path / "state" / ".rc-sync-state.json"
    monkeypatch.setenv("RC_WATCH_ROOTS", str(root))
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("SEMANTIX_CERT_AUTO_RENEW_ENABLED", "false")
    monkeypatch.delenv("M365_FOLDER_URL", raising=False)
    for key in ("RC_DOC_FIELDS", "RC_FIELDS_REUPLOAD_PER_CYCLE", "RC_FIELDS_REUPLOAD_MAX_ATTEMPTS",
                "RC_UPLOAD_ORDER"):
        monkeypatch.delenv(key, raising=False)
    reset_config()
    load_config(validate=False, force_reload=True)
    configure(60000, 1000)
    monkeypatch.setattr("sync.knovas_uploader.write_context_sidecar", lambda *a, **k: None)
    monkeypatch.setattr("sync.knovas_uploader.time.sleep", lambda s: None)
    yield Harness(root, state_path, monkeypatch)
    reset_config()


MANDATE = {"fields": {"doc_type": "invoice"}, "field_templates": ["{mandant}/{period}/**"]}
REL = "Muster AG/GJ 2024/Rechnung_17.txt"


class TestBackwardCompatible:
    def test_no_fields_key_without_configuration(self, rc):
        rc.write("a.txt")
        rc.write("Akten/b.txt")
        result = rc.run(rc.body())
        assert result.files_uploaded == 2
        assert all("fields" not in b for b in rc.server.inits)
        assert all("fields" not in t for t in result.transmissions)
        assert result.doc_fields is not None and not result.doc_fields.active
        # The stored digest of an unconfigured source is "" and stays so.
        assert rc.fields("a.txt").digest == ""
        assert rc.run(rc.body()).files_uploaded == 0

    def test_an_uploader_with_the_old_signature_still_works(self, rc):
        """Without fields the executor calls upload_file exactly as before."""
        from sync.knovas_uploader import SemantixUploader, UploadResult
        from sync.sync_executor import run_sync_work

        rc.write("a.txt")
        uploader = MagicMock(spec=SemantixUploader)

        def old_upload(local_path, rel, sync_body, access_groups=()):
            return UploadResult(rel, "tk", 1, "ok", 1)

        uploader.upload_file.side_effect = old_upload
        uploader._rate_metrics = None
        result = run_sync_work(rc.body(rc.source(access_groups=["g1"])), uploader)
        assert result.files_uploaded == 1
        assert uploader.upload_file.call_args.kwargs == {"access_groups": ("g1",)}

    def test_null_digest_of_old_rows_equals_empty(self, rc):
        rc.write("a.txt")
        store = rc.state()
        try:
            store.record_upload("a.txt", "2023-11-14T22:13:20Z", 22, "tk-old")
        finally:
            store.close()
        result = rc.run(rc.body())
        assert result.document_sync.fields_changed == 0
        assert result.files_uploaded == 0

    def test_upgrading_re_sends_only_sources_with_fields(self, rc):
        rc.write("plain/a.txt")
        rc.write(REL, root=rc.root / "mandate")
        store = rc.state()
        try:
            store.record_upload("a.txt", "2023-11-14T22:13:20Z", 22, "tk-old")
            store.record_upload(REL, "2023-11-14T22:13:20Z", 22, "tk-old")
        finally:
            store.close()
        body = rc.body(rc.source(rc.root / "plain"), rc.source(rc.root / "mandate", **MANDATE))
        result = rc.run(body)
        assert result.document_sync.fields_changed == 1
        assert rc.server.rels() == [REL]
        assert rc.server.inits[0]["fields"]["mandant"] == "Muster AG"
        assert rc.run(body).files_uploaded == 0

    def test_kill_switch_off_is_todays_cycle(self, rc):
        rc.env(RC_DOC_FIELDS="off")
        rc.write(REL)
        body = rc.body(rc.source(**MANDATE), rc.source(rc.root, field_templates=["{x}/{x}"]))
        result = rc.run(body)
        assert result.doc_fields is None
        assert result.errors == [], "templates are not even compiled"
        assert all("fields" not in b for b in rc.server.inits)
        assert rc.fields(REL).outcome is None and rc.fields(REL).digest is None
        rc.env(RC_DOC_FIELDS="on")
        again = rc.run(rc.body(rc.source(**MANDATE)))
        assert again.document_sync.fields_changed == 1, "turning it on re-sends once"


class TestStableOnTheSecondCycle:
    def test_staged_values(self, rc):
        rc.write(REL)
        first = rc.run(rc.body(rc.source(**MANDATE)))
        assert first.files_uploaded == 1
        assert rc.server.inits[0]["fields"] == {"doc_type": "invoice", "mandant": "Muster AG",
                                                "period": "GJ 2024"}
        state = rc.fields(REL)
        assert state.outcome == "staged" and state.sent and state.digest
        tx = first.transmissions[0]
        assert tx["fields"] == {"outcome": "staged", "staged": 3, "warning_codes": []}
        assert rc.run(rc.body(rc.source(**MANDATE))).files_uploaded == 0

    def test_metadata_only_source_with_a_non_mail_file(self, rc):
        rc.write("Scan.txt")
        body = rc.body(rc.source(metadata_fields=["email_date", "email_doc_type"]))
        rc.run(body)
        assert "fields" not in rc.server.inits[0]
        assert rc.fields("Scan.txt").outcome == "none" and rc.fields("Scan.txt").digest
        assert rc.run(body).files_uploaded == 0

    def test_a_partial_upload(self, rc, monkeypatch):
        monkeypatch.setattr("sync.knovas_uploader.partial_note_for",
                            lambda doc, expect_ocr: {"ocr_pages_skipped": 2, "ocr_pages": 5})
        rc.write(REL)
        body = rc.body(rc.source(**MANDATE))
        assert rc.run(body).files_partial == 1
        assert rc.fields(REL).outcome == "staged"
        assert rc.run(body).files_uploaded == 0

    def test_a_skip_unconvertible_row(self, rc):
        rc.write("Muster AG/GJ 2024/leer.txt", text="")
        body = rc.body(rc.source(**MANDATE))
        rc.run(body)
        state = rc.fields("Muster AG/GJ 2024/leer.txt")
        assert state.outcome == "none" and state.digest
        assert rc.run(body).files_uploaded == 0 and rc.server.inits == []

    def test_a_template_that_matches_no_path(self, rc):
        rc.write("Archiv/alt.txt")
        body = rc.body(rc.source(field_templates=["{mandant}/{period}/**"]))
        rc.run(body)
        assert "fields" not in rc.server.inits[0]
        assert rc.fields("Archiv/alt.txt").digest == ""
        assert rc.run(body).files_uploaded == 0

    def test_two_sources_with_the_same_rel_and_different_fields(self, rc):
        first_root, second_root = rc.root / "eins", rc.root / "zwei"
        rc.write("Muster AG/GJ 2024/Brief.txt", root=first_root)
        rc.write("Muster AG/GJ 2024/Brief.txt", root=second_root)
        body = rc.body(
            rc.source(first_root, fields={"doc_type": "invoice"}, access_groups=["a"]),
            rc.source(second_root, fields={"doc_type": "contract"}, access_groups=["b"]),
        )
        result = rc.run(body)
        assert result.doc_fields.rel_collisions == 1
        sent = [(b["access_groups"], b["fields"]) for b in rc.server.inits]
        assert sent == [(["a"], {"doc_type": "invoice"}), (["b"], {"doc_type": "invoice"})], \
            "the first source governs the fields; each copy keeps its own groups"
        again = rc.run(body)
        assert again.files_uploaded == 0
        assert again.doc_fields.rel_collisions == 1


class TestFieldsChanged:
    def _seed(self, rc, count: int, *, per_cycle: Optional[int] = None):
        if per_cycle is not None:
            rc.env(RC_FIELDS_REUPLOAD_PER_CYCLE=str(per_cycle))
        for i in range(count):
            rc.write(f"Muster AG/GJ 2024/R{i}.txt")
        body = rc.body(rc.source(**MANDATE))
        assert rc.run(body).files_uploaded == count
        return rc.body(rc.source(fields={"doc_type": "contract"}, field_templates=MANDATE["field_templates"]))

    def test_a_config_change_re_sends_bounded_per_cycle(self, rc):
        changed = self._seed(rc, 5, per_cycle=2)
        cycles = []
        for _ in range(4):
            result = rc.run(changed)
            cycles.append((result.document_sync.fields_changed, result.files_uploaded))
        assert cycles == [(5, 2), (3, 2), (1, 1), (0, 0)]

    def test_re_uploads_go_after_new_and_modified_work(self, rc):
        changed = self._seed(rc, 2)
        # A new, LARGER file: small_first would put it last if both lists mixed.
        rc.write("Muster AG/GJ 2024/Neu.txt", text="x" * 5000)
        result = rc.run(changed)
        assert rc.server.rels()[0].endswith("Neu.txt")
        assert result.document_sync.pending == 1 and result.document_sync.fields_changed == 2

    def test_re_uploads_take_only_the_room_max_files_leaves(self, rc):
        changed = self._seed(rc, 3)
        rc.write("Muster AG/GJ 2024/Neu1.txt")
        rc.write("Muster AG/GJ 2024/Neu2.txt")
        result = rc.run(changed, sync_config={"max_files_per_cycle": 3})
        assert result.files_uploaded == 3
        assert sorted(r.rsplit("/", 1)[1] for r in rc.server.rels()[:2]) == ["Neu1.txt", "Neu2.txt"]
        full = rc.run(changed, sync_config={"max_files_per_cycle": 1})
        assert full.files_uploaded == 1

    def test_re_uploads_never_truncate_a_scan(self, rc):
        """The walk pops ``b`` before ``a``. Counted as work, b's four
        re-uploads would reach a file cap of 3 and truncate before ``a``."""
        from sync.sync_executor import plan_sync_cycle

        for i in range(4):
            rc.write(f"b/R{i}.txt")
        rc.write("a/R.txt")
        rc.run(rc.body(rc.source(fields={"doc_type": "invoice"})))
        changed = rc.body(rc.source(fields={"doc_type": "contract"}))
        store = rc.state()
        try:
            plan = plan_sync_cycle(changed, store, max_scan_entries=3)
        finally:
            store.close()
        assert plan.scan_truncated is False
        assert plan.summary.fields_changed == 5 and len(plan.fields_queue) == 5

    def test_full_mode_updates_existing_rows_only(self, rc):
        changed = self._seed(rc, 1)
        rc.write("Muster AG/GJ 2024/Neu.txt")
        full = dict(changed, mode="full")
        result = rc.run(full)
        assert result.files_uploaded == 2
        assert rc.fields("Muster AG/GJ 2024/Neu.txt") is None, "full mode creates no rows"
        assert rc.run(changed).document_sync.fields_changed == 0, "digest updated in full mode"

    def test_reupload_failed_after_max_attempts_and_requeue_resets_it(self, rc):
        from sync.sync_scheduler import requeue_doc_fields

        changed = self._seed(rc, 1)
        rel = "Muster AG/GJ 2024/R0.txt"
        rc.server.fail[rel] = lambda: _response(403, {"status": "error", "error_code": "group_not_dominated"})
        attempts = []
        for _ in range(3):
            result = rc.run(changed)
            assert result.files_retry == 1
            attempts.append(rc.fields(rel).attempts)
        assert rc.fields(rel).outcome == "reupload_failed:init_403"
        assert result.doc_fields.reupload_failed == Counter({"init_403": 1})
        assert rc.run(changed).files_uploaded == 0 and rc.server.inits == [], "left the queue"
        rc.server.fail.clear()
        assert requeue_doc_fields("reupload_failed") == 1
        again = rc.run(changed)
        assert again.files_uploaded == 1 and rc.fields(rel).outcome == "staged"

    @staticmethod
    def _fail_extraction(rc) -> None:
        def locked(*_a, **_k):
            raise OSError("[Errno 13] Permission denied")

        rc.monkeypatch.setattr("sync.knovas_uploader.extract_document_guarded", locked)

    @staticmethod
    def _extract_state(rc, rel: str):
        store = rc.state()
        try:
            return store.retry_count(rel), store.partial_paths()
        finally:
            store.close()

    @pytest.mark.parametrize("extract_max", [3, 1])
    def test_a_failing_fields_reupload_never_marks_an_intact_document_partial(
        self, rc, extract_max
    ):
        """rc-1: a ``fields_changed`` re-upload whose extraction fails (a
        locked file, a wall-clock kill) counts fields attempts only. Knovas
        holds the document's full text, so it is never recorded partial for
        exhausted extraction retries -- the backfill would re-send it with
        OCR off -- and it leaves the queue as ``reupload_failed:extract``,
        also after a requeue."""
        from sync.sync_scheduler import requeue_doc_fields

        rc.monkeypatch.setattr("sync.sync_executor.MAX_EXTRACT_RETRIES", extract_max)
        changed = self._seed(rc, 1)
        rel = "Muster AG/GJ 2024/R0.txt"
        self._fail_extraction(rc)
        for _ in range(3):
            assert rc.run(changed).files_retry == 1
            assert self._extract_state(rc, rel) == (0, [])
        assert rc.fields(rel).outcome == "reupload_failed:extract"
        assert rc.run(changed).files_retry == 0, "left the queue"
        assert requeue_doc_fields("reupload_failed") == 1
        for _ in range(3):
            assert rc.run(changed).files_retry == 1
            assert self._extract_state(rc, rel) == (0, [])
            assert rc.fields(rel).outcome == "reupload_failed:extract"
        assert rc.run(changed).files_retry == 0, "left the queue again, not as none"
        # A second configuration change: the same, never partial.
        again = rc.body(rc.source(fields={"doc_type": "letter"}, field_templates=MANDATE["field_templates"]))
        rc.run(again)
        assert self._extract_state(rc, rel) == (0, [])
        assert rc.fields(rel).outcome == "reupload_failed:extract"

    def test_a_content_upload_still_uses_the_extraction_retries(self, rc):
        """The counter stays for new and modified files: past
        MAX_EXTRACT_RETRIES they are recorded partial for the backfill."""
        rc.monkeypatch.setattr("sync.sync_executor.MAX_EXTRACT_RETRIES", 1)
        rc.write(REL)
        self._fail_extraction(rc)
        body = rc.body(rc.source(**MANDATE))
        assert rc.run(body).files_retry == 1
        assert self._extract_state(rc, REL) == (1, [])
        rc.run(body)
        assert self._extract_state(rc, REL) == (0, [REL])
        assert rc.fields(REL).outcome == "none"

    def test_a_requeue_clears_a_stale_extraction_retry_counter(self, rc):
        changed = self._seed(rc, 1)
        rel = "Muster AG/GJ 2024/R0.txt"
        store = rc.state()
        try:
            store.update_fields(rel, FieldsRecord("old", "reupload_failed:extract", sent=True))
            store.increment_retry_count(rel, error="locked")
            assert store.requeue_fields("reupload_failed") == 1
            assert store.retry_count(rel) == 0
        finally:
            store.close()
        assert rc.run(changed).files_uploaded == 1
        assert rc.fields(rel).outcome == "staged"

    def test_a_transient_refusal_comes_back_and_is_bounded(self, rc):
        changed = self._seed(rc, 1)
        rel = "Muster AG/GJ 2024/R0.txt"
        rc.server.refuse[rel] = _response(503, {"status": "error", "error_code": "doc_fields_unavailable"})
        outcomes = []
        for _ in range(4):
            result = rc.run(changed)
            state = rc.fields(rel)
            outcomes.append((result.files_uploaded, state.outcome, state.digest == REQUEUE_DIGEST,
                             state.attempts))
        assert outcomes == [
            (1, "refused:doc_fields_unavailable", True, 1),
            (1, "refused:doc_fields_unavailable", True, 2),
            (1, "reupload_failed:fields_unavailable", False, 0),
            (0, "reupload_failed:fields_unavailable", False, 0),
        ]

    def test_a_permanent_refusal_is_recorded_and_stable(self, rc):
        rc.write(REL)
        rc.server.refuse[REL] = _response(422, {"status": "error", "error_code": "unknown_field",
                                                "path": "fields.mandant"})
        body = rc.body(rc.source(**MANDATE))
        result = rc.run(body)
        assert result.files_uploaded == 1
        assert rc.fields(REL).outcome == "refused:unknown_field"
        assert result.transmissions[0]["fields"]["outcome"] == "refused:unknown_field"
        assert result.doc_fields.refused == Counter({"unknown_field": 1})
        assert rc.run(body).files_uploaded == 0

    def test_removing_the_config_clears_staged_values_once(self, rc):
        rc.write(REL)
        rc.run(rc.body(rc.source(**MANDATE)))
        cleared = rc.run(rc.body(rc.source()))
        assert cleared.document_sync.fields_changed == 1
        assert rc.server.inits[0]["fields"] == {}
        assert rc.fields(REL).outcome == "cleared" and not rc.fields(REL).sent
        assert rc.run(rc.body(rc.source())).files_uploaded == 0


class TestServerStartsAccepting:
    def test_not_accepted_rows_are_requeued_by_the_first_staged_echo(self, rc):
        body = rc.body(rc.source(**MANDATE))
        rc.write(REL)
        rc.server.mode = "off"
        assert rc.run(body).files_uploaded == 1
        assert rc.fields(REL).outcome == "not_accepted"
        assert rc.run(body).files_uploaded == 0, "no loop while the server is off"
        rc.server.mode = "values"
        rc.write("Muster AG/GJ 2024/Neu.txt")
        result = rc.run(body)
        assert result.doc_fields.requeued == 1
        assert rc.server.rels() == ["Muster AG/GJ 2024/Neu.txt"]
        again = rc.run(body)
        assert rc.server.rels() == [REL] and again.document_sync.fields_changed == 1
        assert rc.fields(REL).outcome == "staged"
        assert rc.run(body).files_uploaded == 0

    def test_requeue_endpoint_function(self, rc):
        from sync.sync_scheduler import requeue_doc_fields

        body = rc.body(rc.source(**MANDATE))
        rc.write(REL)
        rc.server.mode = "off"
        rc.run(body)
        rc.server.mode = "values"
        assert requeue_doc_fields("not_accepted") == 1
        assert requeue_doc_fields("not_accepted") == 0
        assert rc.run(body).files_uploaded == 1
        assert rc.fields(REL).outcome == "staged"


class TestRequeueReachesOnlyScannedRows:
    """rc-2: a requeue queues only rows a scan reaches. A row the scan never
    visits again would otherwise be promised a re-send forever."""

    @staticmethod
    def _cycle(rc, body, **kwargs):
        from sync.sync_scheduler import _remember_doc_fields

        result = rc.run(body, **kwargs)
        _remember_doc_fields(result)
        return result

    @staticmethod
    def _status():
        from sync.sync_scheduler import doc_fields_status

        status = doc_fields_status()
        return status["server"], status["documents"]

    def test_a_completed_sequential_subfolder_is_not_requeued(self, rc):
        from sync.sync_scheduler import requeue_doc_fields

        a, b = "A/Muster AG/R.txt", "B/Beispiel GmbH/R.txt"
        rc.write(a)
        rc.write(b)
        cfg = {"sequential_subfolders": True}
        body = rc.body(rc.source(field_templates=["*/{mandant}/**"]))
        rc.server.mode = "off"
        for _ in range(2):
            self._cycle(rc, body, sync_config=cfg)
        assert rc.fields(a).outcome == "not_accepted"
        rc.server.mode = "values"
        cycles = [self._cycle(rc, body, sync_config=cfg) for _ in range(4)]
        assert cycles[-1].subfolder_progress["completed"] is True
        assert sum(c.doc_fields.requeued for c in cycles) == 0, "A lies outside every later scan"
        assert rc.fields(a).outcome == "not_accepted" and rc.fields(a).digest is not None
        server, documents = self._status()
        assert server == "accepted", "the latest answering cycle, not an idle one"
        assert documents["pending_reupload"] == 0, "nothing is promised that no scan re-sends"
        assert documents["not_accepted"] == 1
        assert requeue_doc_fields("not_accepted") == 0, "the endpoint does not queue it either"
        assert rc.fields(a).digest is not None
        assert self._status()[1]["pending_reupload"] == 0

    def test_rows_the_scan_reaches_are_requeued_and_re_sent(self, rc):
        body = rc.body(rc.source(**MANDATE))
        rc.write(REL)
        rc.server.mode = "off"
        self._cycle(rc, body)
        rc.server.mode = "values"
        rc.write("Muster AG/GJ 2024/Neu.txt")
        assert self._cycle(rc, body).doc_fields.requeued == 1
        assert self._status()[1]["pending_reupload"] == 1
        self._cycle(rc, body)
        assert rc.fields(REL).outcome == "staged"
        assert self._status() == ("accepted", {"with_fields": 2, "pending_reupload": 0,
                                               "refused": 0, "not_accepted": 0,
                                               "reupload_failed": 0})

    def test_a_removed_file_kept_tracked_is_not_requeued_by_the_endpoint(self, rc):
        from sync.sync_scheduler import requeue_doc_fields

        other = "Muster AG/GJ 2024/Other.txt"
        rc.write(REL)
        rc.write(other)
        body = rc.body(rc.source(**MANDATE))
        body["ingestion"]["delete_on_remove"] = False
        rc.server.mode = "off"
        self._cycle(rc, body)
        (rc.root / REL).unlink()
        self._cycle(rc, body)
        rc.server.mode = "values"
        assert requeue_doc_fields("not_accepted") == 1, "only the file the scan still reaches"
        self._cycle(rc, body)
        assert rc.server.rels() == [other]
        assert rc.fields(REL).outcome == "not_accepted" and rc.fields(REL).digest is not None
        for _ in range(2):
            self._cycle(rc, body)
            assert self._status() == ("accepted", {"with_fields": 1, "pending_reupload": 0,
                                                   "refused": 0, "not_accepted": 1,
                                                   "reupload_failed": 0})

    def test_before_the_first_cycle_stored_requeued_rows_count_as_pending(self, rc):
        from sync.sync_scheduler import requeue_doc_fields

        body = rc.body(rc.source(**MANDATE))
        rc.write(REL)
        rc.server.mode = "off"
        rc.run(body)  # not remembered: as after a restart
        assert requeue_doc_fields("not_accepted") == 1, "unscoped until a cycle ran"
        assert self._status()[1]["pending_reupload"] == 1

    def test_the_server_state_ignores_requeued_not_accepted_rows(self):
        from sync.sync_scheduler import _server_accepts_fields

        counts = {"accepted": 1, "refused": 0, "not_accepted": 1, "not_accepted_requeued": 1}
        assert _server_accepts_fields(None, counts) == "accepted"
        assert _server_accepts_fields(None, dict(counts, not_accepted_requeued=0)) == "unknown"
        only_requeued = {"accepted": 0, "refused": 0, "not_accepted": 1, "not_accepted_requeued": 1}
        assert _server_accepts_fields(None, only_requeued) == "not_accepted"
        assert _server_accepts_fields(None, counts, "not_accepted") == "not_accepted"


class TestTemplatesAndSources:
    def test_a_bad_template_skips_its_source_and_prunes_nothing(self, rc):
        good, bad = rc.root / "gut", rc.root / "schlecht"
        rc.write("a.txt", root=good)
        rc.write("b.txt", root=bad)
        body_ok = rc.body(rc.source(good), rc.source(bad))
        assert rc.run(body_ok).files_uploaded == 2
        rc.write("c.txt", root=good)
        broken = rc.body(rc.source(good), rc.source(bad, field_templates=["{mandant}/{mandant}"]))
        result = rc.run(broken)
        assert rc.server.rels() == ["c.txt"]
        assert result.doc_fields.template_errors == 1
        assert {"path": "", "error": "field_template_invalid: 1 source(s) skipped this cycle"} in result.errors
        store = rc.state()
        try:
            assert "b.txt" in store.list_tracked_paths(), "never pruned while its source was skipped"
        finally:
            store.close()

    def test_sequential_subfolders_wait_for_fields_changed(self, rc, monkeypatch):
        from sync.subfolder_queue import SubfolderQueue

        calls = []
        original = SubfolderQueue.maybe_advance

        def spy(self, source_root, **kwargs):
            calls.append(kwargs)
            return original(self, source_root, **kwargs)

        monkeypatch.setattr(SubfolderQueue, "maybe_advance", spy)
        rc.write("A/Muster AG/R.txt")
        rc.write("B/Beispiel GmbH/R.txt")
        cfg = {"sequential_subfolders": True}
        first = rc.run(rc.body(rc.source(field_templates=["*/{mandant}/**"])), sync_config=cfg)
        assert first.subfolder_progress["current_subfolder"] == "A"
        changed = rc.body(rc.source(field_templates=["*/{client}/**"]))
        result = rc.run(changed, sync_config=cfg)
        assert result.document_sync.fields_changed == 1
        assert calls[-1]["modified"] == 1 and calls[-1]["pending"] == 0
        assert result.subfolder_progress["current_subfolder"] == "A", "waits for the re-upload"
        done = rc.run(changed, sync_config=cfg)
        assert done.subfolder_progress["current_subfolder"] == "B"

    def test_sequential_source_with_a_bad_template_never_advances(self, rc, monkeypatch):
        from sync.subfolder_queue import SubfolderQueue

        advanced = []
        monkeypatch.setattr(SubfolderQueue, "maybe_advance", lambda self, *a, **k: advanced.append(1))
        rc.write("A/x.txt")
        result = rc.run(rc.body(rc.source(field_templates=["{a}/{a}"])),
                        sync_config={"sequential_subfolders": True})
        assert advanced == [] and result.doc_fields.template_errors == 1


class TestBackfillCarriesTheSpec:
    def test_backfill_sends_fields_and_records_the_digest(self, rc, monkeypatch):
        import importlib.util

        script = Path(__file__).resolve().parents[2] / "scripts" / "backfill_partial_ocr.py"
        spec = importlib.util.spec_from_file_location("backfill_partial_ocr_doc_fields", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        rc.write(REL)
        body = rc.body(rc.source(**MANDATE), rc.source(rc.root, fields={"doc_type": "contract"}))
        store = rc.state()
        try:
            store.record_partial(REL, "2023-11-14T22:13:20Z", 22, "tk", {"ocr_pages_skipped": 1})
        finally:
            store.close()
        monkeypatch.setattr("sync.sync_scheduler.load_last_sync_body", lambda: body)
        monkeypatch.setattr("sync.knovas_uploader.requests.request", rc.server)
        assert module.main([]) == 0
        assert rc.server.inits[0]["fields"]["doc_type"] == "invoice", "the first source governs"
        assert rc.fields(REL).outcome == "staged"


def test_reupload_failure_classes_are_closed():
    from sync.doc_fields_payload import REUPLOAD_FAILURE_CLASSES
    from sync.sync_executor import _reupload_failure_class

    cases = {
        "init failed: 401": "init_401",
        "init failed: 403": "init_403",
        "init failed: 409": "init_4xx",
        "init failed: 502": "init_5xx",
        "init failed: missing transmission key": "other",
        "part 2 failed: 500": "other",
        "ingest rate limit exceeded": "other",
        "extraction timeout after 300s (child killed)": "extract",
    }
    for error, expected in cases.items():
        assert _reupload_failure_class(error) == expected
        assert expected in REUPLOAD_FAILURE_CLASSES


def test_fields_upload_kwargs():
    from sync.doc_fields_payload import SourceSpec
    from sync.sync_executor import fields_upload_kwargs

    plain = SourceSpec(access_groups=("g",))
    configured = SourceSpec(fields={"doc_type": "invoice"})
    assert fields_upload_kwargs(plain, fields_on=True, previous_fields_sent=False) == {"access_groups": ("g",)}
    assert fields_upload_kwargs(configured, fields_on=False, previous_fields_sent=True) == {"access_groups": ()}
    assert fields_upload_kwargs(plain, fields_on=True, previous_fields_sent=True)["source"] is plain
    kw = fields_upload_kwargs(configured, fields_on=True, previous_fields_sent=False)
    assert kw["source"] is configured and kw["previous_fields_sent"] is False


def test_record_upload_outcome_without_digest_never_touches_fields(tmp_path):
    from sync.knovas_uploader import UploadResult
    from sync.sync_executor import record_upload_outcome

    store = SyncStateStore(str(tmp_path / "state.json"))
    try:
        store.record_upload("a.txt", "t0", 1, "tk", fields=FieldsRecord("d", "staged", sent=True))
        up = UploadResult("a.txt", "tk2", 1, "ok", 2)
        assert record_upload_outcome(store, "a.txt", "t1", 2, up, "incremental") == "synced"
        assert store.fields_state("a.txt").digest == "d"
    finally:
        store.close()


class TestMicrosoft365Candidates:
    def _files(self):
        from m365.inventory import RemoteFile

        def remote(rel):
            return RemoteFile("d", rel, rel, rel.rsplit("/", 1)[-1], 10, "2026-01-01T00:00:00Z", "https://x")

        rels = ["Mandate/Muster AG/GJ 2024/R.txt", "Kaputt/a.txt"]
        return {rel: remote(rel) for rel in rels}

    def _iterate(self, sources, template_errors):
        from sync.sync_executor import _WalkBudget, _iter_m365_candidates

        class FakeSource:
            def __init__(self, files):
                self._files = files

            def files(self):
                return self._files

        body = {"sources": sources}
        return list(_iter_m365_candidates(
            FakeSource(self._files()), body, filters={"include_globs": ["*.txt"]},
            should_stop=lambda: False, budget=_WalkBudget(), template_errors=template_errors,
        ))

    def test_each_source_carries_its_spec_and_a_bad_template_skips_it(self, rc):
        rc.env(RC_WATCH_ROOTS="/mnt/documents")
        errors = Counter()
        items = self._iterate([
            {"path": "/mnt/documents/Mandate", "access_groups": ["m"], **MANDATE},
            {"path": "/mnt/documents/Kaputt", "field_templates": ["{a}/{a}"]},
        ], errors)
        assert [(i[1], i[4].access_groups) for i in items] == [("Muster AG/GJ 2024/R.txt", ("m",))]
        assert items[0][4].fields == {"doc_type": "invoice"} and items[0][4].templates
        assert errors == Counter({"field_template_invalid": 1})

    def test_kill_switch_off_compiles_nothing(self, rc):
        rc.env(RC_WATCH_ROOTS="/mnt/documents", RC_DOC_FIELDS="off")
        errors = Counter()
        items = self._iterate([
            {"path": "/mnt/documents/Mandate", "access_groups": ["m"], **MANDATE},
            {"path": "/mnt/documents/Kaputt", "field_templates": ["{a}/{a}"]},
        ], errors)
        assert len(items) == 2 and not errors
        assert all(not i[4].has_fields for i in items)


class TestConfiguration:
    @pytest.fixture(autouse=True)
    def _fresh_config_afterwards(self):
        from config import reset_config

        yield
        # Loaded lazily again from the restored environment.
        reset_config()

    def _load(self, monkeypatch, **env):
        from config import load_config, reset_config

        for key in ("RC_DOC_FIELDS", "RC_FIELDS_REUPLOAD_PER_CYCLE", "RC_FIELDS_REUPLOAD_MAX_ATTEMPTS"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        reset_config()
        return load_config(validate=False, force_reload=True)

    def test_defaults(self, monkeypatch):
        from config import doc_fields_enabled, fields_reupload_max_attempts, fields_reupload_per_cycle

        self._load(monkeypatch)
        assert doc_fields_enabled() is True
        assert fields_reupload_per_cycle() == 100
        assert fields_reupload_max_attempts() == 3

    @pytest.mark.parametrize("raw,expected", [("off", False), ("OFF", False), ("false", False),
                                              ("0", False), ("on", True), ("true", True),
                                              ("enabled", False)])
    def test_the_switch_only_turns_the_feature_off(self, monkeypatch, raw, expected):
        cfg = self._load(monkeypatch, RC_DOC_FIELDS=raw)
        assert cfg.rc_doc_fields is expected

    def test_bounds_are_clamped_at_runtime(self, monkeypatch):
        cfg = self._load(monkeypatch, RC_FIELDS_REUPLOAD_PER_CYCLE="0", RC_FIELDS_REUPLOAD_MAX_ATTEMPTS="x")
        assert cfg.rc_fields_reupload_per_cycle == 1 and cfg.rc_fields_reupload_max_attempts == 3
        cfg = self._load(monkeypatch, RC_FIELDS_REUPLOAD_PER_CYCLE="99999")
        assert cfg.rc_fields_reupload_per_cycle == 10000

    def test_boot_refuses_what_it_would_have_to_guess(self, monkeypatch):
        from config import _doc_fields_config_problems

        self._load(monkeypatch, RC_DOC_FIELDS="enabled", RC_FIELDS_REUPLOAD_PER_CYCLE="0",
                   RC_FIELDS_REUPLOAD_MAX_ATTEMPTS="drei")
        assert _doc_fields_config_problems() == [
            "RC_DOC_FIELDS must be on or off",
            "RC_FIELDS_REUPLOAD_PER_CYCLE must be between 1 and 10000",
            "RC_FIELDS_REUPLOAD_MAX_ATTEMPTS must be an integer",
        ]
        self._load(monkeypatch, RC_DOC_FIELDS="off", RC_FIELDS_REUPLOAD_PER_CYCLE="250")
        assert _doc_fields_config_problems() == []


class TestWarningKeys:
    """Spec F4: Knovas's upload warnings are counted per (code, key) and
    /sync/status lists them; the code counts stay for the POST /sync summary."""

    def test_pairs_per_cycle_and_in_the_status(self, rc):
        from sync.sync_scheduler import _remember_doc_fields, doc_fields_status

        rc.server.echo_warnings = [
            {"key": "mandant", "path": "fields.mandant", "code": "unresolved_entity"},
            {"key": "period", "path": "fields.period", "code": "invalid_value"},
        ]
        for i in range(3):
            rc.write(f"Muster AG/GJ 2024/R{i}.txt")
        result = rc.run(rc.body(rc.source(**MANDATE)))
        assert result.files_uploaded == 3
        assert result.doc_fields.warning_pairs == Counter({
            ("unresolved_entity", "mandant"): 3, ("invalid_value", "period"): 3})
        assert result.doc_fields.as_dict()["warnings"] == {"invalid_value": 3, "unresolved_entity": 3}
        _remember_doc_fields(result)
        assert doc_fields_status()["warnings"] == [
            {"code": "invalid_value", "key": "period", "count": 3},
            {"code": "unresolved_entity", "key": "mandant", "count": 3},
        ]


def test_warning_entries_are_the_most_frequent_first_and_capped():
    from sync.doc_fields_payload import FieldsOutcome
    from sync.sync_executor import MAX_REPORTED_WARNINGS, DocFieldsCycle

    cycle = DocFieldsCycle()
    pairs = tuple(("invalid_value", f"k{i:02d}") for i in range(60))
    cycle.note_outcome("staged", FieldsOutcome("staged", warnings=pairs))
    cycle.note_outcome("staged", FieldsOutcome(
        "staged", warnings=(("ambiguous_date", "document_date"),) * 3))
    entries = cycle.warning_entries()
    assert MAX_REPORTED_WARNINGS == 50 and len(entries) == 50
    assert entries[0] == {"code": "ambiguous_date", "key": "document_date", "count": 3}
    assert entries[1] == {"code": "invalid_value", "key": "k00", "count": 1}
    assert entries[-1] == {"code": "invalid_value", "key": "k48", "count": 1}
    assert DocFieldsCycle().warning_entries() == []
