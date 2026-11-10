"""A fake source of records and an example connector over it: the kit's reference for "what a conformant connector
does", and the subject of the conformance suite's own tests. Nothing here names a real system.

``FakeRecordSource`` keeps items as fields with a version that moves on every change, one computed field (``updated``,
set by the source on every write: a side effect the preview does not show), a record of which permit wrote what (as a
real source keeps a marker with its change, so a repeat finds its earlier write), a review queue for staged changes,
and switches the suite uses: a cut listing, a crash after the next write, a failure that quotes the credential, and a
source that alters the next written value.

``ExampleRecordsConnector`` declares two actions: ``set_fields`` (atomic, both stagings, inverse itself) and
``transition`` (one step per job, both stagings, no inverse).
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from knovas_connector_kit import PROTOCOL, SUITE_VERSION
from knovas_connector_kit.connector import Connector, outcome

log = logging.getLogger("knovas_connector_kit.example")

STATES = ["open", "waiting", "closed"]


class SourceCrashed(Exception):
    pass


class FakeRecordSource:
    def __init__(self, credential: str = "canary-credential-0000"):
        self.credential = credential
        self.items: dict[str, dict] = {}
        self.deleted: set[str] = set()
        self.clock = 0
        self.writes: dict[str, int] = {}
        self.marks: dict[str, dict] = {}          # permit id -> {target, before, version_before, version_after}
        self.staged: dict[str, dict] = {}         # stage ref -> {steps, permit_ids, state, before}
        self.cut_listing = False
        self.crash_after_next_write = False
        self.fail_with_secret = False
        self.alter_next_write = False

    def _tick(self) -> str:
        self.clock += 1
        return dt.datetime(2026, 11, 10, 12, 0, 0, tzinfo=dt.timezone.utc).replace(second=self.clock % 60,
                                                                                  minute=self.clock // 60 % 60) \
            .strftime("%Y-%m-%dT%H:%M:%SZ")

    def _check(self) -> None:
        if self.fail_with_secret:
            self.fail_with_secret = False
            raise RuntimeError(f"source refused the request made with {self.credential}")

    def add(self, target: str, fields: dict) -> None:
        self.items[target] = {"fields": dict(fields, updated=self._tick()), "version": 1}

    def get(self, target: str) -> Optional[dict]:
        self._check()
        item = self.items.get(target)
        return None if item is None else {"fields": dict(item["fields"]), "version": f"v-{item['version']}"}

    def edit(self, target: str, changes: dict) -> None:
        """Someone else edits the item (not through Knovas)."""
        item = self.items[target]
        item["fields"].update(changes, updated=self._tick())
        item["version"] += 1

    def delete(self, target: str) -> None:
        self.items.pop(target)
        self.deleted.add(target)

    def write(self, target: str, changes: dict, permit_id: str) -> None:
        self._check()
        item = self.items[target]
        before = {k: item["fields"].get(k) for k in changes}
        version_before = item["version"]
        if self.alter_next_write:
            self.alter_next_write = False
            changes = {k: (v + " (altered)" if isinstance(v, str) else v) for k, v in changes.items()}
        item["fields"].update(changes, updated=self._tick())
        item["version"] += 1
        self.writes[target] = self.writes.get(target, 0) + 1
        self.marks[permit_id] = {"target": target, "before": before, "version_before": f"v-{version_before}",
                                 "version_after": f"v-{item['version']}"}
        if self.crash_after_next_write:
            self.crash_after_next_write = False
            raise SourceCrashed()

    def stage(self, steps: list[dict], permit_ids: list[str], befores: list[dict]) -> str:
        self._check()
        ref = f"stage-{len(self.staged) + 1}"
        self.staged[ref] = {"steps": steps, "permit_ids": permit_ids, "state": "pending", "before": befores}
        return ref

    def review(self, ref: str, accept: bool) -> None:
        """The source's own reviewer decides; on acceptance the source applies the staged change."""
        staged = self.staged[ref]
        if accept:
            for step, pid in zip(staged["steps"], staged["permit_ids"]):
                self.write(step["target"], step["changes"], pid)
        staged["state"] = "accepted" if accept else "declined"


class ExampleRecordsConnector(Connector):
    COMPUTED = ("updated",)

    def __init__(self, source: FakeRecordSource, package_sha256: str = "0" * 64,
                 passed: Optional[list[str]] = None):
        self.src = source
        self.package_sha256 = package_sha256
        self.passed = passed if passed is not None else []

    def declaration(self) -> dict:
        return {
            "protocol": PROTOCOL,
            "connector": {"name": "example-records", "version": "0.1.0", "package_sha256": self.package_sha256},
            "source_kind": "example-records",
            "streams": ["record", "tombstone", "generation", "state"],
            "fetch": False,
            "actions": [
                {"name": "set_fields", "target_kind": "matter",
                 "params": [{"name": "owner", "kind": "reference", "required": False},
                            {"name": "due_date", "kind": "date", "required": False},
                            {"name": "updated", "kind": "datetime", "required": False}],
                 "preview": "exact", "staging": ["direct", "native_review"], "grouping": "atomic",
                 "inverse": "set_fields"},
                {"name": "transition", "target_kind": "matter",
                 "params": [{"name": "state", "kind": "enum", "required": True, "values": STATES}],
                 "preview": "exact", "staging": ["direct", "native_review"], "grouping": "none", "inverse": None},
            ],
            "conformance": {"suite": SUITE_VERSION, "passed": self.passed, "report_sha256": "0" * 64},
        }

    # ---------------------------------------------------------------- reading
    def read(self, source: str, state: Optional[dict]) -> list[dict]:
        seen = dict((state or {}).get("seen") or {})
        out = []
        for target in sorted(self.src.items):
            item = self.src.get(target)
            out.append({"type": "record", "source": source, "external_id": target, "version": item["version"],
                        "payload": {"record_kind": "matter", "fields": item["fields"],
                                    "computed": list(self.COMPUTED)}})
        now = {m["external_id"]: m["version"] for m in out}
        if not self.src.cut_listing:
            for gone in sorted(set(seen) - set(now)):
                out.append({"type": "tombstone", "source": source, "external_id": gone})
            out.append({"type": "generation", "source": source,
                        "payload": {"id": f"g-{len(now)}-{sum(int(v[2:]) for v in now.values())}", "complete": True,
                                    "counts": {"record": len(now)}}})
            out.append({"type": "state", "source": source, "state": {"seen": now}})
        return out

    # ---------------------------------------------------------------- the write path
    def _changes(self, step: dict) -> dict:
        if step["action"] == "transition":
            return {"state": step["params"]["state"]}
        return dict(step["params"])

    def _invalid(self, step: dict) -> Optional[str]:
        decl = self.declared_action(step["action"])
        names = {p["name"] for p in decl["params"]}
        if set(step["params"]) - names or not step["params"]:
            return "params_invalid"
        for p in decl["params"]:
            if p["required"] and p["name"] not in step["params"]:
                return "params_invalid"
            if p["kind"] == "enum" and p["name"] in step["params"] and step["params"][p["name"]] not in p["values"]:
                return "params_invalid"
        return None

    def preview(self, source, steps, staging, at_version):
        changes, versions, exact, code = [], [], True, None
        for step in steps:
            bad = self._invalid(step)
            if bad:
                return outcome("refused", bad)
            item = self.src.get(step["target"])
            if item is None:
                return outcome("refused", "target_not_found")
            if at_version is not None and item["version"] != at_version:
                return outcome("conflict", "version_changed",
                               conflict={"target": step["target"], "expected_version": at_version,
                                         "found_version": item["version"]})
            versions.append({"target": step["target"], "version": item["version"]})
            for field, after in self._changes(step).items():
                if field in self.COMPUTED:
                    exact, code = False, "computed_field"
                changes.append({"target": step["target"], "field": field, "before": item["fields"].get(field),
                                "after": after})
        grouping = "atomic" if all(self.declared_action(s["action"])["grouping"] == "atomic" for s in steps) else "none"
        preview = {"exact": exact, "read_versions": versions, "changes": [] if not exact else changes,
                   "grouping": grouping}
        if not exact:
            preview["inexact_code"] = code
        return outcome("ok", None if exact else "preview_inexact", preview=preview)

    def _receipt(self, permit: dict, target: str, before: dict, version_before: str, previewed: dict,
                 outcome_name: str = "written", source_ref: Optional[str] = None) -> dict:
        item = self.src.get(target)
        values = [{"target": target, "field": f, "before": before.get(f), "after": item["fields"].get(f)}
                  for f in previewed]
        differences = [{"target": target, "field": f, "previewed": previewed[f], "found": item["fields"].get(f)}
                       for f in previewed if item["fields"].get(f) != previewed[f]]
        return {"permit_id": permit["permit_id"], "outcome": outcome_name, "expected_version": version_before,
                "version_after": item["version"], "reread_at": "2026-11-10T12:00:00Z", "values": values,
                "differences": differences,
                "side_effects": [{"target": target, "field": f} for f in self.COMPUTED],
                "source_ref": source_ref}

    def act(self, source, steps, permits, staging):
        for step in steps:
            bad = self._invalid(step)
            if bad:
                return outcome("refused", bad)
        # A repeat finds its earlier write by the permit id the source keeps with the change: no second write.
        if all(p["permit_id"] in self.src.marks for p in permits):
            receipts = []
            for step, p in zip(steps, permits):
                mark = self.src.marks[p["permit_id"]]
                receipts.append(self._receipt(p, step["target"], mark["before"], mark["version_before"],
                                              self._changes(step)))
            return outcome("ok", receipts=receipts)
        for ref, staged in self.src.staged.items():
            if any(p["permit_id"] in staged["permit_ids"] for p in permits):
                return outcome("ok", receipts=[
                    {"permit_id": p["permit_id"], "outcome": "staged", "expected_version": p["expected_version"],
                     "version_after": None, "reread_at": "2026-11-10T12:00:00Z", "values": [], "differences": [],
                     "side_effects": [], "source_ref": ref} for p in permits])
        items = [(step, p, self.src.get(step["target"])) for step, p in zip(steps, permits)]
        for step, p, item in items:
            if item is None:
                return outcome("refused", "target_not_found")
            if item["version"] != p["expected_version"]:
                return outcome("conflict", "version_changed",
                               conflict={"target": step["target"], "expected_version": p["expected_version"],
                                         "found_version": item["version"]})
            if set(self._changes(step)) & set(self.COMPUTED):
                return outcome("refused", "computed_field")
        if staging == "native_review":
            befores = [{f: item["fields"].get(f) for f in self._changes(step)} for step, _p, item in items]
            ref = self.src.stage([{"target": s["target"], "changes": self._changes(s)} for s, _p, _i in items],
                                 [p["permit_id"] for _s, p, _i in items], befores)
            receipts = [{"permit_id": p["permit_id"], "outcome": "staged", "expected_version": p["expected_version"],
                         "version_after": None, "reread_at": "2026-11-10T12:00:00Z", "values": [],
                         "differences": [], "side_effects": [], "source_ref": ref} for _s, p, _i in items]
            return outcome("ok", receipts=receipts)
        receipts = []
        for step, p, item in items:
            changes = self._changes(step)
            before = {f: item["fields"].get(f) for f in changes}
            self.src.write(step["target"], changes, p["permit_id"])
            receipts.append(self._receipt(p, step["target"], before, item["version"], changes))
        return outcome("ok", receipts=receipts)

    def reread(self, source, targets, fields, permit_ids):
        read = []
        for target in targets:
            item = self.src.get(target)
            if item is None:
                read.append({"target": target, "version": None})
                continue
            values = {k: v for k, v in item["fields"].items() if not fields or k in fields}
            read.append({"target": target, "version": item["version"], "values": values})
        receipts = []
        for pid in permit_ids:
            mark = self.src.marks.get(pid)
            if mark is not None:
                receipts.append(self._receipt({"permit_id": pid}, mark["target"], mark["before"],
                                              mark["version_before"], {k: self.src.get(mark["target"])["fields"].get(k)
                                                                       for k in mark["before"]}))
        return outcome("ok", read=read, **({"receipts": receipts} if receipts else {}))

    def review_outcome(self, source, stage_ref, permit_ids):
        staged = self.src.staged.get(stage_ref)
        if staged is None:
            return outcome("refused", "not_staged")
        review = {"state": staged["state"], "stage_ref": stage_ref}
        if staged["state"] == "accepted":
            review["receipts"] = []
            for step, pid, before in zip(staged["steps"], staged["permit_ids"], staged["before"]):
                mark = self.src.marks[pid]
                review["receipts"].append(self._receipt({"permit_id": pid}, step["target"], before,
                                                        mark["version_before"], step["changes"],
                                                        source_ref=stage_ref))
        return outcome("ok", review=review)

    def unstage(self, source, stage_ref, permit_ids):
        staged = self.src.staged.get(stage_ref)
        if staged is None:
            return outcome("refused", "not_staged")
        if staged["state"] == "pending":
            staged["state"] = "withdrawn"
        return outcome("ok", review={"state": staged["state"], "stage_ref": stage_ref})


class ExampleHarness:
    """The conformance harness of the example connector (``conformance/harness.py``)."""

    source = "records-main"

    def __init__(self, connector_class=ExampleRecordsConnector):
        self.src = FakeRecordSource()
        for n in range(1, 7):
            self.src.add(f"items/matter-{n}", {"owner": f"person-{n}", "state": "open", "due_date": None})
        self.connector = connector_class(self.src)
        self.credential = self.src.credential

    def sample_steps(self) -> list[dict]:
        return [{"action": "set_fields", "target": "items/matter-1",
                 "params": {"owner": "person-7", "due_date": "2026-12-01"}},
                {"action": "transition", "target": "items/matter-2", "params": {"state": "closed"}},
                {"action": "set_fields", "target": "items/matter-3", "params": {"owner": "person-8"}}]

    def computed_step(self) -> dict:
        return {"action": "set_fields", "target": "items/matter-4", "params": {"updated": "2026-11-11T00:00:00Z"}}

    def change_behind(self, target: str) -> None:
        self.src.edit(target, {"owner": "someone-else"})

    def delete_behind(self, target: str) -> None:
        self.src.delete(target)

    def set_cut_listing(self, on: bool) -> None:
        self.src.cut_listing = on

    def writes(self, target: str) -> int:
        return self.src.writes.get(target, 0)

    def crash_after_next_write(self) -> None:
        self.src.crash_after_next_write = True

    def fail_next_call_with_secret(self) -> None:
        self.src.fail_with_secret = True

    def alter_next_write(self) -> None:
        self.src.alter_next_write = True

    def review(self, stage_ref: str, accept: bool) -> None:
        self.src.review(stage_ref, accept)


def make_harness() -> ExampleHarness:
    return ExampleHarness()
