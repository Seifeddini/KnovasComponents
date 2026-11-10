"""One connector per conformance property that breaks exactly that property (the suite must catch each), after the
Alloy model's mutant style: each weakens one rule of the example connector."""
from __future__ import annotations

import logging

from knovas_connector_kit.connector import outcome
from knovas_connector_kit.example import ExampleHarness, ExampleRecordsConnector

log = logging.getLogger("mutant")


class ReplayChanges(ExampleRecordsConnector):
    """Every read stamps a counter into each record: a replay is not the same stream."""
    reads = 0

    def read(self, source, state):
        ReplayChanges.reads += 1
        out = super().read(source, state)
        for m in out:
            if m["type"] == "record":
                m["payload"]["fields"]["read_no"] = ReplayChanges.reads
        return out


class CutListingDeletes(ExampleRecordsConnector):
    """Reports a complete listing although the source cut it: absence would retire items."""

    def read(self, source, state):
        cut, self.src.cut_listing = self.src.cut_listing, False
        try:
            return super().read(source, state)
        finally:
            self.src.cut_listing = cut


class ReceiptCopiesPreview(ExampleRecordsConnector):
    """Builds the receipt from what it meant to write, not from reading the source again."""

    def _receipt(self, permit, target, before, version_before, previewed, outcome_name="written", source_ref=None):
        r = super()._receipt(permit, target, before, version_before, previewed, outcome_name, source_ref)
        r["values"] = [{"target": target, "field": f, "before": before.get(f), "after": v} for f, v in previewed.items()]
        r["differences"] = []
        return r


class IgnoresVersion(ExampleRecordsConnector):
    """Writes although the item moved since the preview."""

    def act(self, source, steps, permits, staging):
        permits = [dict(p, expected_version=self.src.get(s["target"])["version"]) for s, p in zip(steps, permits)]
        return super().act(source, steps, permits, staging)


class WritesTwice(ExampleRecordsConnector):
    """Does not look for its earlier write: a repeat or a lost result writes again."""

    def act(self, source, steps, permits, staging):
        marks, self.src.marks = self.src.marks, {}
        try:
            return super().act(source, steps, permits, staging)
        finally:
            for k, v in marks.items():
                self.src.marks.setdefault(k, v)


class SkipsPermitBinding(ExampleRecordsConnector):
    """Trusts that Link checked the permits and writes whatever the steps say."""

    def _bound_permits(self, job):
        from knovas_connector_kit.permits import decode
        return [decode(t).payload for t in job["permits"]]


class LeaksCredential(ExampleRecordsConnector):
    """Logs the source's error text, which quotes the credential."""

    def preview(self, source, steps, staging, at_version):
        try:
            return super().preview(source, steps, staging, at_version)
        except Exception as exc:  # noqa: BLE001
            log.warning("preview failed: %s", exc)
            return outcome("failed", "internal")


class IgnoresDecline(ExampleRecordsConnector):
    """Never reports that the source's reviewer declined: the change set would wait forever."""

    def review_outcome(self, source, stage_ref, permit_ids):
        res = super().review_outcome(source, stage_ref, permit_ids)
        if res.get("review", {}).get("state") == "declined":
            res["review"]["state"] = "pending"
        return res


MUTANTS = {
    "replay_changes_nothing": ReplayChanges,
    "deletion_guard": CutListingDeletes,
    "preview_equals_receipt": ReceiptCopiesPreview,
    "exactly_once": WritesTwice,
    "permit_refusals": SkipsPermitBinding,
    "no_credential_leaves_site": LeaksCredential,
    "review_outcome": IgnoresDecline,
}
EXTRA = {"preview_equals_receipt": IgnoresVersion}


def factory(cls):
    return lambda: ExampleHarness(cls)
