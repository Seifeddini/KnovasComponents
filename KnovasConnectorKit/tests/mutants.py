"""One connector per conformance property that breaks exactly that property (the suite must catch each), after the
Alloy model's mutant style: each weakens one rule of the example connector."""
from __future__ import annotations

import logging

from knovas_connector_kit.canonical import params_hash
from knovas_connector_kit.connector import outcome
from knovas_connector_kit.example import ExampleHarness, ExampleRecordsConnector
from knovas_connector_kit.permits import PermitError, decode

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

    def _receipt(self, permit, target, before, version_before, previewed, outcome_name="written", source_ref=None,
                 step=0):
        r = super()._receipt(permit, target, before, version_before, previewed, outcome_name, source_ref, step)
        r["values"] = [{"step": step, "target": target, "field": f, "before": before.get(f), "after": v}
                       for f, v in previewed.items()]
        r["differences"] = []
        return r


class StepsByTarget(ExampleRecordsConnector):
    """Names each change by its item, as before the amendment (D-14): every change is given the first step of the
    job on its target, so of two actions on one item the second shows nothing."""

    def preview(self, source, steps, staging, at_version):
        res = super().preview(source, steps, staging, at_version)
        targets = [s["target"] for s in steps]
        for change in (res.get("preview") or {}).get("changes", []):
            change["step"] = targets.index(change["target"])
        return res


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


class StagesTwice(ExampleRecordsConnector):
    """Does not look for its earlier staging: a repeat of a staging act stages the change a second time (for GitHub, a
    second pull request)."""

    def act(self, source, steps, permits, staging):
        hidden = {ref: staged["permit_ids"] for ref, staged in self.src.staged.items()}
        for staged in self.src.staged.values():
            staged["permit_ids"] = []
        try:
            return super().act(source, steps, permits, staging)
        finally:
            for ref, ids in hidden.items():
                self.src.staged[ref]["permit_ids"] = ids


class StagesPastTheVersion(ExampleRecordsConnector):
    """Stages a change although the item moved since the preview: the source's reviewer is shown a change approved
    against another version."""

    def act(self, source, steps, permits, staging):
        if staging == "native_review":
            permits = [dict(p, expected_version=self.src.get(s["target"])["version"]) for s, p in zip(steps, permits)]
        return super().act(source, steps, permits, staging)


class SkipsStagingAndSiteBinding(ExampleRecordsConnector):
    """Its own permit check compares the source, the action, the item and parameters and the version, but not the
    staging or the site: a permit approved as a review could be written directly, or one for another site used here."""

    def _bound_permits(self, job):
        out = []
        for step, token in zip(job["args"]["steps"], job.get("permits") or []):
            payload = decode(token).payload
            if (payload["source"] != job["source"] or payload["action"] != step["action"]
                    or payload["params_hash"] != params_hash(step["target"], step["params"])
                    or payload["expected_version"] != step["expected_version"]):
                raise PermitError("permit_mismatch")
            out.append(payload)
        return out


class MixesItems(ExampleRecordsConnector):
    """Its own grouping check takes grouping item for atomic: an act whose steps span items is written item after item,
    as if the source could write them all or none (D-24)."""

    def _bound_permits(self, job):
        self._item_as_atomic = True
        try:
            return super()._bound_permits(job)
        finally:
            self._item_as_atomic = False

    def declared_action(self, name):
        action = super().declared_action(name)
        if action is not None and getattr(self, "_item_as_atomic", False) and action["grouping"] == "item":
            action = dict(action, grouping="atomic")
        return action


class WritesStepByStep(ExampleRecordsConnector):
    """Writes the steps of one act one after the other, each checked against its version just before it is written:
    of two steps on one item the first is written, then the second finds the item moved (by that write) and answers a
    conflict - partly written, where atomic and item promise all or none."""

    def act(self, source, steps, permits, staging):
        if len(steps) < 2 or staging != "direct":
            return super().act(source, steps, permits, staging)
        receipts = []
        for index, (step, permit) in enumerate(zip(steps, permits)):
            res = super().act(source, [step], [permit], staging)
            for entry in [res.get("conflict") or {}] + [e for r in res.get("receipts") or []
                                                        for e in r["values"] + r["differences"]]:
                if entry:
                    entry["step"] = index
            if res["status"] != "ok":
                return res
            receipts += res["receipts"]
        return outcome("ok", receipts=receipts)


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
#: Further mutants, each with the one property it breaks. The last three break only the native_review half of a
#: property (verifier, Phase A, M3): before the suite checked each declared staging, all three passed every property.
EXTRA = [("preview_equals_receipt", IgnoresVersion), ("preview_equals_receipt", StepsByTarget),
         ("exactly_once", StagesTwice), ("preview_equals_receipt", StagesPastTheVersion),
         ("permit_refusals", SkipsStagingAndSiteBinding), ("preview_equals_receipt", WritesStepByStep)]
#: Mutants of a connector whose field action declares grouping item (``grouping_item``), with the property each breaks.
ITEM = [("preview_equals_receipt", MixesItems), ("preview_equals_receipt", WritesStepByStep)]


def factory(cls):
    return lambda: ExampleHarness(cls)


def grouping_item(cls):
    """``cls`` with ``set_fields`` declared grouping item instead of atomic (D-24): several field changes of one item
    in one act, all or none; the Brain sends one act per item."""

    class GroupingItem(cls):
        def declaration(self):
            d = super().declaration()
            for action in d["actions"]:
                if action["grouping"] == "atomic":
                    action["grouping"] = "item"
            return d

    GroupingItem.__name__ = cls.__name__ + "GroupingItem"
    return GroupingItem


def only_native_review(cls):
    """``cls`` with every action declared for ``native_review`` only: a connector whose source has its own review step
    and no direct write (SPEC-write-back-35's document library)."""

    class OnlyNativeReview(cls):
        def declaration(self):
            d = super().declaration()
            for action in d["actions"]:
                action["staging"] = ["native_review"]
            return d

    OnlyNativeReview.__name__ = cls.__name__ + "OnlyNativeReview"
    return OnlyNativeReview
