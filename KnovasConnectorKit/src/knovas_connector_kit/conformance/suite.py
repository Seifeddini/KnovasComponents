"""The seven properties (connector protocol 2.0, "The conformance suite").

Each check takes a harness factory (``harness.py``), builds fresh harnesses as it needs them, and returns ``None``
when the property holds or a short code saying what failed (never a value: a value may be customer data). The suite
plays the Brain (it signs permits with a key of its own) and, for the permit refusals, Knovas Link (``gate.LinkGate``,
the reference of Link's checks).
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Callable, Optional

from knovas_connector_kit import SUITE_VERSION, contracts
from knovas_connector_kit.canonical import digest, params_hash
from knovas_connector_kit.gate import LinkGate
from knovas_connector_kit.permits import KeyRing, Signer

READ_PROPERTIES = ["replay_changes_nothing", "deletion_guard", "no_credential_leaves_site"]
WRITE_PROPERTIES = ["preview_equals_receipt", "exactly_once", "permit_refusals", "no_credential_leaves_site"]
PROPERTIES = ["replay_changes_nothing", "deletion_guard", "preview_equals_receipt", "exactly_once", "permit_refusals",
              "no_credential_leaves_site", "review_outcome"]

TENANT = "00000000-0000-4000-8000-000000000001"
SERIAL = "424242"
NOW = 1794405600
ACTOR = {"kind": "user", "id": "conformance-approver"}


class Brain:
    """The suite's stand-in for the Brain: builds jobs and signs one permit per step."""

    def __init__(self, source: str):
        self.source = source
        self.signer = Signer()
        self.ring = KeyRing.from_document(self.signer.keys_document())

    def job(self, kind: str, args: dict, **extra) -> dict:
        job = {"id": str(uuid.uuid4()), "kind": kind, "protocol": "2.0", "source": self.source,
               "target_serial": SERIAL, "deadline": "2100-01-01T00:00:00Z", "actor": ACTOR, "args": args}
        job.update(extra)
        return job

    def preview(self, steps: list[dict], staging: str = "direct", at_version: Optional[str] = None) -> dict:
        args = {"steps": [dict(s) for s in steps], "staging": staging}
        if at_version:
            args["at_version"] = at_version
        return self.job("preview", args)

    def permit(self, step: dict, expected: str, staging: str, *, site: str = SERIAL, iat: int = NOW,
               exp: Optional[int] = None, signer: Optional[Signer] = None) -> str:
        payload = {"v": 1, "permit_id": str(uuid.uuid4()), "tenant": TENANT, "site": site, "source": self.source,
                   "action_id": str(uuid.uuid4()), "change_set_id": str(uuid.uuid4()), "action": step["action"],
                   "params_hash": params_hash(step["target"], step["params"]), "expected_version": expected,
                   "staging": staging, "iat": iat, "exp": iat + 3600 if exp is None else exp}
        return (signer or self.signer).sign(payload)

    def act(self, steps: list[dict], expected: list[str], staging: str = "direct", **permit_kw) -> dict:
        full = [dict(s, expected_version=v) for s, v in zip(steps, expected)]
        tokens = [self.permit(s, v, staging, **permit_kw) for s, v in zip(steps, expected)]
        return self.job("act", {"steps": full, "staging": staging}, permits=tokens)

    def gate(self, connector, now: int = NOW) -> LinkGate:
        return LinkGate(connector, self.ring, tenant=TENANT, serial=SERIAL, clock=lambda: now)


def _staging_steps(h, staging: str) -> list[dict]:
    return [s for s in h.sample_steps()
            if h.connector.declared_action(s["action"]) is not None
            and staging in h.connector.declared_action(s["action"])["staging"]]


def _version(result: dict, target: str) -> Optional[str]:
    for rv in (result.get("preview") or {}).get("read_versions", []):
        if rv["target"] == target:
            return rv["version"]
    return None


# ------------------------------------------------------------------ read properties

def replay_changes_nothing(factory: Callable) -> Optional[str]:
    h = factory()
    first, again = h.connector.read(h.source, None), h.connector.read(h.source, None)
    if first != again:
        return "stream_differs_on_replay"
    for m in first:
        if not contracts.valid("stream-message.schema.json", m):
            return "stream_message_invalid"
    items = [m["external_id"] for m in first if m["type"] in ("document", "record")]
    if len(items) != len(set(items)) or any("version" not in m for m in first if m["type"] in ("document", "record")):
        return "item_without_version_or_twice"
    states = [m["state"] for m in first if m["type"] == "state"]
    if states and h.connector.read(h.source, states[-1]) != h.connector.read(h.source, states[-1]):
        return "stream_differs_on_replay_from_state"
    return None


def deletion_guard(factory: Callable) -> Optional[str]:
    h = factory()
    full = h.connector.read(h.source, None)
    states = [m["state"] for m in full if m["type"] == "state"]
    target = h.sample_steps()[0]["target"]
    h.delete_behind(target)
    h.set_cut_listing(True)
    cut = h.connector.read(h.source, states[-1] if states else None)
    if any(m["type"] == "tombstone" for m in cut) or any(
            m["type"] == "generation" and m["payload"].get("complete") for m in cut):
        return "cut_listing_reports_absence"
    h.set_cut_listing(False)
    whole = h.connector.read(h.source, states[-1] if states else None)
    listed = {m["external_id"] for m in whole if m["type"] in ("document", "record")}
    tomb = any(m["type"] == "tombstone" and m["external_id"] == target for m in whole)
    complete = any(m["type"] == "generation" and m["payload"].get("complete") for m in whole)
    if target in listed or not (tomb or complete):
        return "deletion_not_reported"
    return None


# ------------------------------------------------------------------ write properties

def preview_equals_receipt(factory: Callable) -> Optional[str]:
    h = factory()
    brain = Brain(h.source)
    steps = _staging_steps(h, "direct")
    if not steps:
        return "no_direct_step"
    for step in steps:
        prev = h.connector.handle(brain.preview([step]))
        if prev["status"] != "ok" or not prev["preview"]["exact"]:
            return "preview_not_exact"
        expected = _version(prev, step["target"])
        res = h.connector.handle(brain.act([step], [expected]))
        if res["status"] != "ok" or len(res.get("receipts", [])) != 1:
            return "act_failed"
        r = res["receipts"][0]
        shown = {(c["target"], c["field"]): c["after"] for c in prev["preview"]["changes"]}
        found = {(v["target"], v["field"]): v["after"] for v in r["values"]}
        if r["outcome"] != "written" or r["differences"] or found != shown or r["version_after"] == expected:
            return "receipt_differs_from_preview"
        if h.writes(step["target"]) != 1:
            return "wrote_other_than_once"
    # A conflict writes nothing.
    h = factory()
    step = _staging_steps(h, "direct")[0]
    prev = h.connector.handle(brain.preview([step]))
    h.change_behind(step["target"])
    before = h.writes(step["target"])
    res = h.connector.handle(brain.act([step], [_version(prev, step["target"])]))
    if res["status"] != "conflict" or h.writes(step["target"]) != before:
        return "wrote_past_the_preview_version"
    # The receipt is a re-read: a source that stored something else shows a difference.
    h = factory()
    step = _staging_steps(h, "direct")[0]
    prev = h.connector.handle(brain.preview([step]))
    h.alter_next_write()
    res = h.connector.handle(brain.act([step], [_version(prev, step["target"])]))
    if res["status"] != "ok" or not res["receipts"][0]["differences"]:
        return "receipt_not_a_reread"
    # A field the source computes is never previewed as exact.
    h = factory()
    computed = h.computed_step()
    if computed is not None:
        prev = h.connector.handle(brain.preview([computed]))
        if prev["status"] == "ok" and prev["preview"]["exact"]:
            return "computed_field_previewed_exact"
    return None


def exactly_once(factory: Callable) -> Optional[str]:
    h = factory()
    brain = Brain(h.source)
    step = _staging_steps(h, "direct")[0]
    prev = h.connector.handle(brain.preview([step]))
    job = brain.act([step], [_version(prev, step["target"])])
    first = h.connector.handle(job)
    again = h.connector.handle(job)
    if first["status"] != "ok" or again["status"] != "ok" or h.writes(step["target"]) != 1:
        return "repeat_wrote_again"
    if again["receipts"][0]["values"] != first["receipts"][0]["values"]:
        return "repeat_answered_another_receipt"
    # The result is lost between the source and Link: the next delivery finds the earlier write.
    h = factory()
    step = _staging_steps(h, "direct")[0]
    prev = h.connector.handle(brain.preview([step]))
    job = brain.act([step], [_version(prev, step["target"])])
    h.crash_after_next_write()
    h.connector.handle(job)
    again = h.connector.handle(job)
    if h.writes(step["target"]) != 1 or again["status"] != "ok" or again["receipts"][0]["outcome"] != "written":
        return "lost_result_wrote_again"
    return None


def permit_refusals(factory: Callable) -> Optional[str]:
    cases = []
    # Through the reference of Link's checks: each refusal with its code, nothing written.
    h = factory()
    brain = Brain(h.source)
    step = _staging_steps(h, "direct")[0]
    expected = _version(h.connector.handle(brain.preview([step])), step["target"])
    gate = brain.gate(h.connector)
    cases.append((gate.deliver(brain.act([step], [expected], signer=Signer())), "permit_bad_signature"))
    cases.append((gate.deliver(brain.act([step], [expected], site="1")), "permit_wrong_site"))
    changed = brain.act([step], [expected])
    changed["args"]["steps"][0]["params"] = {k: (v + "-changed" if isinstance(v, str) else v)
                                             for k, v in step["params"].items()}
    cases.append((gate.deliver(changed), "permit_mismatch"))
    cases.append((gate.deliver(brain.act([step], [expected], iat=NOW - 7200)), "permit_expired"))
    for res, code in cases:
        if res["status"] != "refused" or res["code"] != code:
            return f"link_reference_{code}"
    if h.writes(step["target"]) != 0:
        return "wrote_under_a_refused_permit"
    ok = brain.act([step], [expected])
    first = gate.deliver(ok)
    repeat = gate.deliver(ok)
    if first["status"] != "ok" or not repeat.get("repeat") or h.writes(step["target"]) != 1:
        return "used_permit_not_answered_with_first_result"
    # The connector's own check (defence in depth): a permit that does not bind its step writes nothing, even when
    # a job reaches it without Link.
    h = factory()
    step = _staging_steps(h, "direct")[0]
    expected = _version(h.connector.handle(brain.preview([step])), step["target"])
    job = brain.act([step], [expected])
    job["args"]["steps"][0]["params"] = {k: (v + "-changed" if isinstance(v, str) else v)
                                         for k, v in step["params"].items()}
    res = h.connector.handle(job)
    if res["status"] == "ok" or h.writes(step["target"]) != 0:
        return "connector_wrote_under_an_unbound_permit"
    job = brain.act([step], [expected])
    job["args"]["steps"][0]["expected_version"] = "not-the-permits"
    res = h.connector.handle(job)
    if res["status"] == "ok" or h.writes(step["target"]) != 0:
        return "connector_wrote_under_an_unbound_permit"
    return None


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage() + json.dumps({k: str(v) for k, v in record.__dict__.items()}, sort_keys=True)
        if record.exc_info:
            text += logging.Formatter().formatException(record.exc_info)
        self.lines.append(text)


def no_credential_leaves_site(factory: Callable) -> Optional[str]:
    root = logging.getLogger()
    capture, level = _Capture(), root.level
    root.addHandler(capture)
    root.setLevel(logging.DEBUG)
    out: list = []
    try:
        h = factory()
        brain = Brain(h.source)
        out.append(h.connector.read(h.source, None))
        h.fail_next_call_with_secret()
        try:
            out.append(h.connector.read(h.source, None))
        except Exception:  # noqa: BLE001 - a read may fail; its text must not carry the credential either
            out.append("read_failed")
        steps = _staging_steps(h, "direct") or _staging_steps(h, "native_review")
        if steps:
            step = steps[0]
            staging = "direct" if _staging_steps(h, "direct") else "native_review"
            h.fail_next_call_with_secret()
            out.append(h.connector.handle(brain.preview([step], staging)))
            prev = h.connector.handle(brain.preview([step], staging))
            out.append(prev)
            h.fail_next_call_with_secret()
            out.append(h.connector.handle(brain.act([step], [_version(prev, step["target"])], staging)))
            out.append(h.connector.handle(brain.job("reread", {"targets": [step["target"]]})))
        out.append(h.connector.declaration())
    finally:
        root.removeHandler(capture)
        root.setLevel(level)
    text = json.dumps(out, sort_keys=True, default=str) + "\n".join(capture.lines)
    return "credential_left_the_connector" if h.credential in text else None


def review_outcome(factory: Callable) -> Optional[str]:
    h = factory()
    if not _staging_steps(h, "native_review"):
        return "not_applicable"
    brain = Brain(h.source)

    def stage(hh):
        step = _staging_steps(hh, "native_review")[0]
        prev = hh.connector.handle(brain.preview([step], "native_review"))
        res = hh.connector.handle(brain.act([step], [_version(prev, step["target"])], "native_review"))
        if res["status"] != "ok" or res["receipts"][0]["outcome"] != "staged" or hh.writes(step["target"]) != 0:
            return None, step, prev
        return res["receipts"][0]["source_ref"], step, prev

    ref, step, prev = stage(h)
    if ref is None:
        return "not_staged_or_written_early"
    ids = []

    def ask(hh, kind="review_outcome"):
        return hh.connector.handle(brain.job(kind, {"stage_ref": ref, "permit_ids": [str(uuid.uuid4())]}))

    if ask(h)["review"]["state"] != "pending":
        return "pending_not_reported"
    h.review(ref, True)
    accepted = ask(h)
    if accepted["review"]["state"] != "accepted" or not accepted["review"].get("receipts") \
            or accepted["review"]["receipts"][0]["differences"] or h.writes(step["target"]) != 1:
        return "acceptance_not_reported_with_receipt"
    h = factory()
    ref, step, prev = stage(h)
    h.review(ref, False)
    if ask(h)["review"]["state"] != "declined" or h.writes(step["target"]) != 0:
        return "decline_not_reported_or_written"
    h = factory()
    ref, step, prev = stage(h)
    if ask(h, "unstage")["review"]["state"] != "withdrawn" or ask(h)["review"]["state"] != "withdrawn":
        return "unstage_not_withdrawn"
    del ids
    return None


CHECKS = {"replay_changes_nothing": replay_changes_nothing, "deletion_guard": deletion_guard,
          "preview_equals_receipt": preview_equals_receipt, "exactly_once": exactly_once,
          "permit_refusals": permit_refusals, "no_credential_leaves_site": no_credential_leaves_site,
          "review_outcome": review_outcome}


def run(factory: Callable) -> dict:
    """Run every property on fresh harnesses; the report a connector's declaration names by its SHA-256."""
    passed, failed, not_applicable = [], {}, []
    declaration = factory().connector.declaration()
    for name in PROPERTIES:
        if name in WRITE_PROPERTIES[:3] and not declaration["actions"]:
            not_applicable.append(name)
            continue
        try:
            reason = CHECKS[name](factory)
        except Exception as exc:  # noqa: BLE001 - a crash is a failure of the property, named by its type
            reason = "raised_" + type(exc).__name__
        if reason == "not_applicable":
            not_applicable.append(name)
        elif reason is None:
            passed.append(name)
        else:
            failed[name] = reason
    report = {"suite": SUITE_VERSION, "connector": declaration["connector"], "source_kind": declaration["source_kind"],
              "passed": passed, "failed": failed, "not_applicable": not_applicable}
    report["report_sha256"] = digest(report)
    return report
