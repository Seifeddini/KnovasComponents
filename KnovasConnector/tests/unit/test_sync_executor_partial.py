"""Obligation tests: how the RC records an extraction outcome (GI-EXTRACT-02).

Alloy: KnowledgeBase ``models/alloy/mechanisms/client_pipeline.als::
RcRecordingMechanism`` (model ``data_plane/ocr_budget_failsoft.als``; mutants
``ocr__skip_uncounted`` = skipped OCR pages silently recorded as synced,
``ocr__timeout_parks`` = the behaviour shipped today: the wall-clock kill in
``extract_document_guarded`` raises "resource limit exceeded: extraction
timeout …", ``is_unconvertible_error`` matches that prefix, and one hung
Tesseract page parks the whole file as skip:unconvertible forever).

Recording rules mirrored here (planned ``sync_executor.record_upload_outcome``):

    library returned, skipped OCR pages > 0  -> "partial"  (backfill picks it up)
    library returned, nothing skipped        -> "synced"
    library flagged the input unconvertible  -> "skipped"  (skip:unconvertible)
    transient failure / wall-clock kill      -> "retry"    (capped; after the
                                                cap the file is recorded
                                                partial so the OCR-disabled
                                                escalation pass lands its
                                                text pages instead of looping)

RED until the feature lands.
"""

from __future__ import annotations

import pytest

from sync.document_text import is_unconvertible_error
from sync.knovas_uploader import UploadResult
from sync.sync_state import SyncStateStore


def _upload(status: str = "ok", *, error: str | None = None, partial: dict | None = None) -> UploadResult:
    kwargs = dict(relative_path="Mandanten/Mueller AG/2023/Jahresrechnung.pdf",
                  transmission_key_id="tk-1" if status == "ok" else None,
                  parts=3 if status == "ok" else 0, status=status,
                  ingestion_requests=4 if status == "ok" else 0, error=error)
    if partial is not None:
        kwargs["partial"] = partial
    return UploadResult(**kwargs)


@pytest.fixture
def state(tmp_path) -> SyncStateStore:
    return SyncStateStore(str(tmp_path / "state.json"))


class TestRcRecordingMechanism:
    """Alloy obligation: mechanisms/client_pipeline.als::RcRecordingMechanism."""

    def test_skipped_ocr_pages_record_partial_not_synced(self, state):
        from sync.sync_executor import record_upload_outcome

        up = _upload("ok", partial={"ocr_pages_skipped": 12, "ocr_pages": 40})
        outcome = record_upload_outcome(state, up.relative_path, "2026-10-01T00:00:00Z", 1234, up, "incremental")
        assert outcome == "partial"
        assert up.relative_path in state.partial_paths()
        # the file is not re-uploaded by the next incremental cycle …
        assert state.status_for(up.relative_path, "2026-10-01T00:00:00Z", 1234) == "synced"
        # … but it is never confused with a clean upload
        assert state.partial_note(up.relative_path) == {"ocr_pages_skipped": 12, "ocr_pages": 40}

    def test_clean_upload_is_synced(self, state):
        from sync.sync_executor import record_upload_outcome

        up = _upload("ok")
        assert record_upload_outcome(state, up.relative_path, "2026-10-01T00:00:00Z", 1234, up, "incremental") == "synced"
        assert up.relative_path not in state.partial_paths()

    def test_wall_clock_kill_is_retryable_never_unconvertible(self, state):
        from sync.document_text import EXTRACT_TIMEOUT_ERROR_PREFIX
        from sync.sync_executor import record_upload_outcome

        msg = f"{EXTRACT_TIMEOUT_ERROR_PREFIX}: extraction timeout after 300s"
        assert not msg.lower().startswith("resource limit exceeded")
        assert is_unconvertible_error(msg) is False
        up = _upload("error", error=msg)
        assert record_upload_outcome(state, up.relative_path, "2026-10-01T00:00:00Z", 1234, up, "incremental") == "retry"
        assert state.status_for(up.relative_path, "2026-10-01T00:00:00Z", 1234) in ("pending", "modified")

    def test_killed_child_is_retryable_with_backoff_not_every_cycle(self, state):
        """'extractor died (exit -9)' (OOM) used to be retried every cycle,
        each burning the full timeout: it is retryable but counted."""
        from sync.sync_executor import record_upload_outcome

        up = _upload("error", error="extractor died (exit -9)")
        assert record_upload_outcome(state, up.relative_path, "2026-10-01T00:00:00Z", 1234, up, "incremental") == "retry"
        assert state.retry_count(up.relative_path) == 1

    def test_retry_cap_escalates_to_partial_instead_of_looping(self, state):
        from sync.sync_executor import MAX_EXTRACT_RETRIES, record_upload_outcome

        up = _upload("error", error="extractor died (exit -9)")
        outcomes = [record_upload_outcome(state, up.relative_path, "2026-10-01T00:00:00Z", 1234, up, "incremental")
                    for _ in range(MAX_EXTRACT_RETRIES + 1)]
        assert outcomes[:-1] == ["retry"] * MAX_EXTRACT_RETRIES
        assert outcomes[-1] == "partial", "after the cap the file waits for the OCR-disabled pass / backfill"
        assert up.relative_path in state.partial_paths()

    def test_library_flagged_unconvertible_is_still_parked(self, state):
        from sync.sync_executor import record_upload_outcome

        up = _upload("error", error="corrupt pdf: cannot open broken xref")
        assert is_unconvertible_error(up.error) is True
        assert record_upload_outcome(state, up.relative_path, "2026-10-01T00:00:00Z", 1234, up, "incremental") == "skipped"
        assert state.status_for(up.relative_path, "2026-10-01T00:00:00Z", 1234) == "synced"
        assert up.relative_path not in state.partial_paths()

    def test_full_mode_never_parks_anything(self, state):
        from sync.sync_executor import record_upload_outcome

        up = _upload("error", error="corrupt pdf: cannot open broken xref")
        assert record_upload_outcome(state, up.relative_path, "2026-10-01T00:00:00Z", 1234, up, "full") == "retry"

    def test_a_refused_ocr_setting_never_uses_up_the_retries(self, state):
        """Spec E5: the library refused the Connector's OCR settings -- no
        PDF is at fault. Counting it would record every PDF partial after
        three cycles and backfill it without OCR."""
        from sync.document_text import CONFIG_INVALID_PREFIX
        from sync.sync_executor import MAX_EXTRACT_RETRIES, record_upload_outcome

        up = _upload("error", error=f"{CONFIG_INVALID_PREFIX}: RC_TESSERACT_LANG")
        outcomes = [record_upload_outcome(state, up.relative_path, "2026-10-01T00:00:00Z", 1234, up, "incremental")
                    for _ in range(MAX_EXTRACT_RETRIES + 2)]
        assert outcomes == ["retry"] * (MAX_EXTRACT_RETRIES + 2)
        assert state.retry_count(up.relative_path) == 0
        assert up.relative_path not in state.partial_paths()


class TestOcrBudgetMessagesAreNotUnconvertible:
    """A budget trip inside the library never surfaces as an error at all;
    should one leak (older library), its wording must not park the file."""

    @pytest.mark.parametrize("msg", [
        "ocr budget exceeded: partial 12/40 pages",
        "extraction timeout after 300s (child killed)",
        "extractor died (exit -9)",
        "extraction configuration invalid: RC_TESSERACT_LANG",
    ])
    def test_not_unconvertible(self, msg):
        assert is_unconvertible_error(msg) is False

    @pytest.mark.parametrize("msg", [
        "corrupt pdf: xref", "encrypted pdf", "unsupported extension .xyz",
        "resource limit exceeded: page_count 20000 > 10000",
    ])
    def test_library_limits_and_corrupt_inputs_still_park(self, msg):
        assert is_unconvertible_error(msg) is True
