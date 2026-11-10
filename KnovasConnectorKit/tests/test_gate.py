"""The reference of Knovas Link's checks: the Alloy model's order (LinkVerifyMechanism), the first result for a
repeat also after expiry, and the write log (WB-21, WB-06, WB-25; SPEC-write-back -10, -21, -22, -23, -26)."""
from __future__ import annotations

from knovas_connector_kit.conformance.suite import NOW, SERIAL, Brain
from knovas_connector_kit.example import make_harness
from knovas_connector_kit.permits import Signer


def _setup():
    h = make_harness()
    brain = Brain(h.source)
    step = h.sample_steps()[0]
    prev = h.connector.handle(brain.preview([step]))
    return h, brain, step, prev["preview"]["read_versions"][0]["version"]


def test_refusals_follow_the_models_order():
    h, brain, step, v = _setup()
    gate = brain.gate(h.connector)
    # bad signature beats wrong site; wrong site beats mismatch; mismatch beats expiry
    job = brain.act([step], [v], signer=Signer(), site="1")
    assert gate.deliver(job)["code"] == "permit_bad_signature"
    job = brain.act([step], [v], site="1", iat=NOW - 7200)
    job["args"]["steps"][0]["params"] = {"owner": "x"}
    assert gate.deliver(job)["code"] == "permit_wrong_site"
    job = brain.act([step], [v], iat=NOW - 7200)
    job["args"]["steps"][0]["params"] = {"owner": "x"}
    assert gate.deliver(job)["code"] == "permit_mismatch"
    assert gate.deliver(brain.act([step], [v], iat=NOW - 7200))["code"] == "permit_expired"
    assert h.writes(step["target"]) == 0


def test_a_repeat_after_expiry_gets_the_first_result():
    h, brain, step, v = _setup()
    job = brain.act([step], [v])
    gate = brain.gate(h.connector)
    first = gate.deliver(job)
    gate.clock = lambda: NOW + 10_000                            # long after the permit's expiry
    repeat = gate.deliver(job)
    assert first["status"] == "ok"
    assert repeat["repeat"] is True and repeat["code"] == "permit_used"
    assert repeat["receipts"] == first["receipts"]
    assert h.writes(step["target"]) == 1


def test_every_delivery_with_a_permit_is_in_the_write_log():
    h, brain, step, v = _setup()
    gate = brain.gate(h.connector)
    job = brain.act([step], [v])
    gate.deliver(job)
    gate.deliver(job)
    gate.deliver(brain.act([step], [v], site="7"))
    outcomes = [e["outcome"] for e in gate.log]
    assert outcomes == ["accepted", "executed", "repeated", "refused"]
    assert all(set(e) == {"permit_id", "job_id", "outcome", "code", "at"} for e in gate.log)
    assert SERIAL == "424242"
