"""The seven properties (connector protocol 2.0, "The conformance suite").

Each check takes a harness factory (``harness.py``), builds fresh harnesses as it needs them, and returns ``None``
when the property holds or a short code saying what failed (never a value: a value may be customer data). The suite
plays the Brain (it signs permits with a key of its own, valid for the protocol's longest lifetime) and, for the permit
refusals, Knovas Link (``gate.LinkGate``, the reference of Link's checks).

Every change, value, difference and conflict names its step, the index into the job's ``args.steps`` (v2.0 as amended
2026-11-10, D-14); ``preview_equals_receipt`` checks that a step's changes are attributed to it, also for two steps on
one item.

The write properties check each staging the connector declared (verifier, Phase A, M3), so a connector whose actions
only stage (``native_review``) can pass them: ``preview_equals_receipt`` takes a staged change's receipts from the
accepted review, ``exactly_once`` repeats a staging ``act`` (the repeat answers the same stage, and the source's
review holds one change), and ``permit_refusals`` checks the connector's own binding of each staging and of the site.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Callable, Optional

from knovas_connector_kit import SUITE_VERSION, contracts
from knovas_connector_kit.canonical import digest, params_hash
from knovas_connector_kit.gate import LinkGate
from knovas_connector_kit.permits import MAX_LIFETIME_SECONDS, KeyRing, Signer

READ_PROPERTIES = ["replay_changes_nothing", "deletion_guard", "no_credential_leaves_site"]
WRITE_PROPERTIES = ["preview_equals_receipt", "exactly_once", "permit_refusals", "no_credential_leaves_site"]
PROPERTIES = ["replay_changes_nothing", "deletion_guard", "preview_equals_receipt", "exactly_once", "permit_refusals",
              "no_credential_leaves_site", "review_outcome"]

TENANT = "00000000-0000-4000-8000-000000000001"
SERIAL = "424242"
NOW = 1794405600
LIFETIME = MAX_LIFETIME_SECONDS          # the Brain signs for the protocol's longest lifetime, 3 hours (D-15)
EXPIRED_IAT = NOW - 2 * LIFETIME         # signed so long ago that the permit expired before NOW
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
                   "staging": staging, "iat": iat, "exp": iat + LIFETIME if exp is None else exp}
        return (signer or self.signer).sign(payload)

    def act(self, steps: list[dict], expected: list[str], staging: str = "direct", **permit_kw) -> dict:
        full = [dict(s, expected_version=v) for s, v in zip(steps, expected)]
        tokens = [self.permit(s, v, staging, **permit_kw) for s, v in zip(steps, expected)]
        return self.job("act", {"steps": full, "staging": staging}, permits=tokens)

    def gate(self, connector, now: int = NOW) -> LinkGate:
        return LinkGate(connector, self.ring, tenant=TENANT, serial=SERIAL, clock=lambda: now)


STAGINGS = ("direct", "native_review")


def _staging_steps(h, staging: str) -> list[dict]:
    return [s for s in h.sample_steps()
            if h.connector.declared_action(s["action"]) is not None
            and staging in h.connector.declared_action(s["action"])["staging"]]


def _stagings(h) -> list[str]:
    """The stagings the harness has sample steps for, ``direct`` first."""
    return [staging for staging in STAGINGS if _staging_steps(h, staging)]


def _other(staging: str) -> str:
    return "native_review" if staging == "direct" else "direct"


def _accepted_receipts(h, brain: "Brain", res: dict) -> Optional[list]:
    """A staged ``act``'s receipts as the source's review gives them: the reviewer accepts, ``review_outcome`` reports
    the receipts of the re-read at acceptance. None when the act did not stage or the review does not say accepted."""
    receipts = res.get("receipts") or []
    if res.get("status") != "ok" or not receipts or any(r["outcome"] != "staged" for r in receipts):
        return None
    ref = receipts[0]["source_ref"]
    h.review(ref, True)
    accepted = h.connector.handle(brain.job("review_outcome", {"stage_ref": ref,
                                                               "permit_ids": [r["permit_id"] for r in receipts]}))
    review = accepted.get("review") or {}
    if accepted.get("status") != "ok" or review.get("state") != "accepted":
        return None
    return review.get("receipts") or None


def _version(result: dict, target: str) -> Optional[str]:
    for rv in (result.get("preview") or {}).get("read_versions", []):
        if rv["target"] == target:
            return rv["version"]
    return None


def _shown(entries: list, step: int) -> set:
    """What the entries named with ``step`` change: (target, field, after) of its field changes."""
    return {(c["target"], c["field"], json.dumps(c["after"], sort_keys=True)) for c in entries
            if c.get("step") == step and c["field"] is not None}


def _attributed(entries: list, steps: list) -> bool:
    """Every entry names a step of the job (D-14), on that step's target."""
    return all(isinstance(e.get("step"), int) and 0 <= e["step"] < len(steps)
               and e["target"] == steps[e["step"]]["target"] for e in entries)


def _same_item_steps(h, staging: str) -> Optional[list[dict]]:
    """Two steps on one item that both declare ``staging``: the harness's, or else its first step of that staging
    twice."""
    offered = getattr(h, "same_item_steps", None)
    steps = offered() if callable(offered) else None
    if steps is not None and all(staging in (h.connector.declared_action(s["action"]) or {}).get("staging", [])
                                 for s in steps):
        return steps
    own = _staging_steps(h, staging)
    return [own[0], own[0]] if own else None


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
    stagings = _stagings(factory())
    if not stagings:
        return "no_exact_step"
    for staging in stagings:
        reason = _preview_equals_receipt(factory, staging)
        if reason is not None:
            return reason
    return _changes_named_by_their_step(factory, stagings[0])


def _receipt_of(h, brain: "Brain", res: dict, staging: str) -> Optional[dict]:
    """The one receipt of an ``act`` of one step: the act's own (direct), or the accepted review's (native_review)."""
    if staging == "direct":
        return res["receipts"][0] if res.get("status") == "ok" and len(res.get("receipts", [])) == 1 else None
    receipts = _accepted_receipts(h, brain, res)
    return receipts[0] if receipts and len(receipts) == 1 else None


def _preview_equals_receipt(factory: Callable, staging: str) -> Optional[str]:
    h = factory()
    brain = Brain(h.source)
    for step in _staging_steps(h, staging):
        prev = h.connector.handle(brain.preview([step], staging))
        if prev["status"] != "ok" or not prev["preview"]["exact"]:
            return "preview_not_exact"
        if not _attributed(prev["preview"]["changes"], [step]):
            return "change_not_attributed_to_its_step"
        expected = _version(prev, step["target"])
        res = h.connector.handle(brain.act([step], [expected], staging))
        if staging == "native_review" and h.writes(step["target"]) != 0:
            return "staged_change_written_before_review"
        r = _receipt_of(h, brain, res, staging)
        if r is None:
            return "act_failed"
        if not _attributed(r["values"] + r["differences"], [step]):
            return "change_not_attributed_to_its_step"
        shown = {(c["target"], c["field"]): c["after"] for c in prev["preview"]["changes"] if c["field"] is not None}
        found = {(v["target"], v["field"]): v["after"] for v in r["values"]}
        if r["outcome"] != "written" or r["differences"] or found != shown or r["version_after"] == expected:
            return "receipt_differs_from_preview"
        if h.writes(step["target"]) != 1:
            return "wrote_other_than_once"
    # A conflict writes (or stages) nothing, and names the step whose item moved.
    h = factory()
    step = _staging_steps(h, staging)[0]
    prev = h.connector.handle(brain.preview([step], staging))
    h.change_behind(step["target"])
    before = h.writes(step["target"])
    res = h.connector.handle(brain.act([step], [_version(prev, step["target"])], staging))
    if res["status"] != "conflict" or h.writes(step["target"]) != before:
        return "wrote_past_the_preview_version" if staging == "direct" else "staged_past_the_preview_version"
    if not _attributed([res["conflict"]] + res["conflict"].get("changes", []), [step]):
        return "change_not_attributed_to_its_step"
    h = factory()
    grouped = [s for s in _staging_steps(h, staging)
               if h.connector.declared_action(s["action"])["grouping"] == "atomic"][:2]
    if len(grouped) == 2 and grouped[0]["target"] != grouped[1]["target"]:
        prev = h.connector.handle(brain.preview(grouped, staging))
        h.change_behind(grouped[1]["target"])
        res = h.connector.handle(brain.act(grouped, [_version(prev, s["target"]) for s in grouped], staging))
        if res["status"] != "conflict" or res["conflict"]["step"] != 1 \
                or not _attributed([res["conflict"]] + res["conflict"].get("changes", []), grouped):
            return "change_not_attributed_to_its_step"
    # The receipt is a re-read: a source that stored something else shows a difference.
    h = factory()
    step = _staging_steps(h, staging)[0]
    prev = h.connector.handle(brain.preview([step], staging))
    h.alter_next_write()
    r = _receipt_of(h, brain, h.connector.handle(brain.act([step], [_version(prev, step["target"])], staging)),
                    staging)
    if r is None or not r["differences"]:
        return "receipt_not_a_reread"
    return None


def _changes_named_by_their_step(factory: Callable, staging: str) -> Optional[str]:
    """Several steps in one preview: each change names the step it belongs to (D-14), and each step shows what its own
    preview shows, also when two steps change one item; a field the source computes is never previewed as exact."""
    h = factory()
    brain = Brain(h.source)
    steps = _staging_steps(h, staging)
    alone = []
    for step in steps:
        single = h.connector.handle(brain.preview([step], staging))
        if single["status"] != "ok":
            return "preview_not_exact"
        alone.append(_shown(single["preview"]["changes"], 0))
    prev = h.connector.handle(brain.preview(steps, staging))
    if prev["status"] != "ok" or not _attributed(prev["preview"]["changes"], steps):
        return "change_not_attributed_to_its_step"
    if any(_shown(prev["preview"]["changes"], i) != alone[i] for i in range(len(steps))):
        return "change_not_attributed_to_its_step"
    pair = _same_item_steps(h, staging)
    if pair is not None:
        own = []
        for step in pair:
            single = h.connector.handle(brain.preview([step], staging))
            if single["status"] != "ok":
                return "preview_not_exact"
            own.append(_shown(single["preview"]["changes"], 0))
        both = h.connector.handle(brain.preview(pair, staging))
        if both["status"] != "ok" or not _attributed(both["preview"]["changes"], pair) \
                or [_shown(both["preview"]["changes"], i) for i in range(len(pair))] != own:
            return "change_not_attributed_to_its_step"
        declared = [h.connector.declared_action(s["action"]) for s in pair]
        if all(d["grouping"] == "atomic" and "direct" in d["staging"] for d in declared):
            # Written together, each receipt's values name the step of its own permit.
            res = h.connector.handle(brain.act(pair, [_version(both, s["target"]) for s in pair]))
            if res["status"] != "ok" or len(res.get("receipts", [])) != len(pair):
                return "act_failed"
            for i, r in enumerate(res["receipts"]):
                if not _attributed(r["values"] + r["differences"], pair) \
                        or {v["step"] for v in r["values"]} != {i} or _shown(r["values"], i) != own[i]:
                    return "change_not_attributed_to_its_step"
    # A field the source computes is never previewed as exact.
    h = factory()
    computed = h.computed_step()
    if computed is not None:
        prev = h.connector.handle(brain.preview([computed], staging))
        if prev["status"] == "ok" and prev["preview"]["exact"]:
            return "computed_field_previewed_exact"
    return None


def exactly_once(factory: Callable) -> Optional[str]:
    stagings = _stagings(factory())
    if not stagings:
        return "no_exact_step"
    if "direct" in stagings:
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
    if "native_review" in stagings:
        # A staging act delivered again (its result lost on the way): the repeat answers the stage it made, and the
        # source's review holds that one change; accepted, it is written once.
        h = factory()
        brain = Brain(h.source)
        step = _staging_steps(h, "native_review")[0]
        prev = h.connector.handle(brain.preview([step], "native_review"))
        job = brain.act([step], [_version(prev, step["target"])], "native_review")
        first, again = h.connector.handle(job), h.connector.handle(job)
        staged = [r for res in (first, again) for r in (res.get("receipts") or []) if res["status"] == "ok"]
        if len(staged) != 2 or any(r["outcome"] != "staged" for r in staged) or h.writes(step["target"]) != 0:
            return "repeat_not_answered_with_its_stage"
        if staged[1]["source_ref"] != staged[0]["source_ref"] or staged[1]["permit_id"] != staged[0]["permit_id"]:
            return "repeat_staged_again"
        h.review(staged[0]["source_ref"], True)
        if h.writes(step["target"]) != 1:
            return "repeat_staged_again"
    return None


def permit_refusals(factory: Callable) -> Optional[str]:
    h = factory()
    stagings = _stagings(h)
    if not stagings:
        return "no_exact_step"
    staging = stagings[0]
    cases = []
    # Through the reference of Link's checks: each refusal with its code, nothing written.
    brain = Brain(h.source)
    step = _staging_steps(h, staging)[0]
    expected = _version(h.connector.handle(brain.preview([step], staging)), step["target"])
    gate = brain.gate(h.connector)
    cases.append((gate.deliver(brain.act([step], [expected], staging, signer=Signer())), "permit_bad_signature"))
    cases.append((gate.deliver(brain.act([step], [expected], staging, site="1")), "permit_wrong_site"))
    changed = brain.act([step], [expected], staging)
    changed["args"]["steps"][0]["params"] = {k: (v + "-changed" if isinstance(v, str) else v)
                                             for k, v in step["params"].items()}
    cases.append((gate.deliver(changed), "permit_mismatch"))
    cases.append((gate.deliver(brain.act([step], [expected], staging, iat=EXPIRED_IAT)), "permit_expired"))
    cases.append((gate.deliver(brain.act([step], [expected], staging, exp=NOW + LIFETIME + 1)), "permit_bad_signature"))
    for res, code in cases:
        if res["status"] != "refused" or res["code"] != code:
            return f"link_reference_{code}"
    if h.writes(step["target"]) != 0:
        return "wrote_under_a_refused_permit"
    ok = brain.act([step], [expected], staging)
    first = gate.deliver(ok)
    repeat = gate.deliver(ok)
    if first["status"] != "ok" or not repeat.get("repeat") or repeat.get("receipts") != first.get("receipts") \
            or h.writes(step["target"]) != (1 if staging == "direct" else 0):
        return "used_permit_not_answered_with_first_result"
    # The connector's own check (defence in depth), for each staging it declared: a permit that does not bind its step,
    # its staging or this site writes and stages nothing, even when a job reaches it without Link.
    for staging in stagings:
        h = factory()
        step = _staging_steps(h, staging)[0]
        expected = _version(h.connector.handle(brain.preview([step], staging)), step["target"])
        unbound = []
        job = brain.act([step], [expected], staging)
        job["args"]["steps"][0]["params"] = {k: (v + "-changed" if isinstance(v, str) else v)
                                             for k, v in step["params"].items()}
        unbound.append(job)
        job = brain.act([step], [expected], staging)
        job["args"]["steps"][0]["expected_version"] = "not-the-permits"
        unbound.append(job)
        job = brain.act([step], [expected], staging)                        # approved for the other staging
        job["permits"] = [brain.permit(step, expected, _other(staging))]
        unbound.append(job)
        unbound.append(brain.act([step], [expected], staging, site="1"))    # approved for another site
        for job in unbound:
            res = h.connector.handle(job)
            if res["status"] == "ok" or h.writes(step["target"]) != 0:
                return "connector_acted_under_an_unbound_permit"
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

    staged_ids: list = []

    def stage(hh):
        step = _staging_steps(hh, "native_review")[0]
        prev = hh.connector.handle(brain.preview([step], "native_review"))
        res = hh.connector.handle(brain.act([step], [_version(prev, step["target"])], "native_review"))
        if res["status"] != "ok" or res["receipts"][0]["outcome"] != "staged" or hh.writes(step["target"]) != 0:
            return None, step, prev
        staged_ids[:] = [res["receipts"][0]["permit_id"]]
        return res["receipts"][0]["source_ref"], step, prev

    ref, step, prev = stage(h)
    if ref is None:
        return "not_staged_or_written_early"

    def ask(hh, kind="review_outcome"):
        return hh.connector.handle(brain.job(kind, {"stage_ref": ref, "permit_ids": list(staged_ids)}))

    if ask(h)["review"]["state"] != "pending":
        return "pending_not_reported"
    h.review(ref, True)
    accepted = ask(h)
    if accepted["review"]["state"] != "accepted" or not accepted["review"].get("receipts") \
            or accepted["review"]["receipts"][0]["differences"] or h.writes(step["target"]) != 1 \
            or not _attributed(accepted["review"]["receipts"][0]["values"], [step]):
        return "acceptance_not_reported_with_receipt"
    h = factory()
    ref, step, prev = stage(h)
    h.review(ref, False)
    if ask(h)["review"]["state"] != "declined" or h.writes(step["target"]) != 0:
        return "decline_not_reported_or_written"
    # unstage is optional (not required in V1, the founder's decision of 2026-11-10): checked only when answered.
    h = factory()
    ref, step, prev = stage(h)
    withdrawn = ask(h, "unstage")
    if withdrawn["status"] == "ok" and (withdrawn["review"]["state"] != "withdrawn"
                                        or ask(h)["review"]["state"] != "withdrawn"):
        return "unstage_not_withdrawn"
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
        if name in ("replay_changes_nothing", "deletion_guard") and not declaration["streams"]:
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
