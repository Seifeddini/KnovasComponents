"""Knovas Link's work channel and probes (MVP-18, KC-7a), against a fake secure-api that answers as
services/connector_jobs.py does: the job envelope, a lease fresh per claim sent back as X-Knovas-Lease, 409
lease_lost, a non-ok result names its code, an ok fetch names version, bytes_sha256, bytes and parts."""
from __future__ import annotations

import hashlib
import os
import time

import pytest

from conftest import SERIAL, eventually, make_job, register

FOUR_MIB = 4 * 1024 * 1024


def _take(world, client, name="sync-component", wait=3):
    answer = client.get(f"/local/v1/jobs?wait={wait}", headers=world.headers(name))
    assert answer.status_code == 200, answer.status_code
    return answer.json()["job"]


def _part(world, client, jid, n, data, name="sync-component"):
    return client.post(f"/local/v1/jobs/{jid}/part?n={n}", headers={**world.headers(name),
                       "Content-Type": "application/octet-stream"}, content=data)


def _result(world, client, jid, body, name="sync-component"):
    return client.post(f"/local/v1/jobs/{jid}/result", headers=world.headers(name), json=body)


def _fetched(parts):
    raw = b"".join(parts)
    return {"status": "ok", "code": None, "version": hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest(),
            "bytes_sha256": hashlib.sha256(raw).hexdigest(), "mime": "text/markdown", "bytes": len(raw),
            "parts": len(parts), "duration_ms": 41}


def _outcome(world, jid):
    return eventually(lambda: world.secure.outcome(jid))


@pytest.fixture
def channel(world):
    with world.running(loops=("channel",)) as (client, link):
        register(world, client)
        register(world, client, "knovas-connector", kind="knovas_connector", job_kinds=["fetch"], capabilities=["documents"])
        eventually(lambda: link.channel.state == "connected")
        yield client, link


# ------------------------------------------------------------------ jobs reach the right component

def test_a_job_goes_to_the_component_that_serves_its_source_unchanged_and_without_the_lease(world, channel):
    client, link = channel
    job = world.secure.enqueue(make_job("fetch", "handbook"))
    assert client.get("/local/v1/jobs?wait=0", headers=world.headers("knovas-connector")).status_code == 204
    assert _take(world, client) == job, "the job as secure-api gave it, the lease kept by Link"
    assert world.secure.claims[-1]["sources"] == ["archive", "crm", "handbook", "shares"]
    assert world.secure.claims[-1]["wait"] == 25


def test_parts_arrive_in_order_unchanged_and_the_result_after_them(world, channel):
    client, link = channel
    job = world.secure.enqueue(make_job("fetch", "handbook"))
    jid = _take(world, client)["id"]
    parts = [os.urandom(FOUR_MIB), b"# Handbook\r\n\xef\xbb\xbf", os.urandom(1000)]
    for n, data in enumerate(parts):
        answer = _part(world, client, jid, n, data)
        assert (answer.status_code, answer.json()) == (200, {"accepted": True})
    assert world.secure.parts(jid) == parts
    result = _fetched(parts)
    answer = _result(world, client, jid, result)
    assert (answer.status_code, answer.json()) == (200, {"accepted": True})
    assert world.secure.outcome(jid) == result, "passed on unchanged"
    assert world.secure.jobs[jid]["refused"] == [], "every part and the result carried the claim's lease"
    assert link.telemetry.jobs == {"fetch": {"ok": 1}}
    assert link.telemetry.sources[("ws-1", "handbook")]["last_read_ok_at"]
    assert job["id"] == jid


def test_a_part_is_streamed_through_not_held_whole(world, channel, monkeypatch):
    client, link = channel
    world.secure.enqueue(make_job("fetch", "handbook"))
    jid = _take(world, client)["id"]
    seen = {}

    async def post_part(job_id, n, content, length, lease=None):
        seen["type"] = type(content).__name__
        seen["chunks"] = [c async for c in content]
        seen["length"], seen["lease"] = length, lease
        return 202, None

    monkeypatch.setattr(link.brain, "post_part", post_part)
    assert _part(world, client, jid, 0, b"x" * 1000).status_code == 200
    assert seen["type"] == "async_generator" and b"".join(seen["chunks"]) == b"x" * 1000
    assert seen["length"] == 1000 and seen["lease"].startswith("lease-")


@pytest.mark.parametrize("n,data,status", [("1", b"abc", 400), ("", b"abc", 400), ("x", b"abc", 400),
                                           ("0", b"", 400), ("0", b"x" * (FOUR_MIB + 1), 413)])
def test_a_refused_part_ends_the_fetch(world, channel, n, data, status):
    client, _ = channel
    world.secure.enqueue(make_job("fetch", "handbook"))
    jid = _take(world, client)["id"]
    answer = client.post(f"/local/v1/jobs/{jid}/part" + (f"?n={n}" if n else ""), headers=world.headers(), content=data)
    assert answer.status_code == status and answer.json()["error"] == "bad_field"
    assert _outcome(world, jid) == {"status": "failed", "code": "bad_field", "duration_ms": world.secure.outcome(jid)["duration_ms"]}
    assert _part(world, client, jid, 0, b"late").status_code == 409
    late = _result(world, client, jid, _fetched([b"late"]))
    assert late.status_code == 409 and late.json()["error"] == "lease_lost"
    assert world.secure.parts(jid) == []


def test_a_gap_after_a_good_part_ends_the_fetch(world, channel):
    client, _ = channel
    world.secure.enqueue(make_job("fetch", "handbook"))
    jid = _take(world, client)["id"]
    assert _part(world, client, jid, 0, b"one").status_code == 200
    assert _part(world, client, jid, 2, b"three").status_code == 400
    assert _part(world, client, jid, 1, b"two").status_code == 409
    assert _outcome(world, jid)["status"] == "failed" and world.secure.parts(jid) == [b"one"]


def test_a_part_for_a_write_is_refused_and_the_write_goes_on(world, channel):
    client, link = channel
    world.secure.enqueue(make_job("write", "handbook"))
    job = _take(world, client)
    assert job["kind"] == "write" and job["args"]["mode"] == "proposal"
    assert _part(world, client, job["id"], 0, b"x").status_code == 400
    result = {"status": "ok", "source_ref": "https://git.example.test/example-org/handbook/pull/12", "duration_ms": 900}
    assert _result(world, client, job["id"], result).status_code == 200
    assert world.secure.outcome(job["id"]) == result
    assert link.telemetry.jobs == {"write": {"ok": 1}}


def test_a_result_that_breaks_the_contract_is_refused_and_the_job_stays_open(world, channel):
    client, _ = channel
    world.secure.enqueue(make_job("fetch", "handbook"))
    jid = _take(world, client)["id"]
    for bad in ({"status": "ok"}, {"status": "fine", "duration_ms": 1}, {"status": "failed", "code": "Could not read",
                "duration_ms": 1}, {"status": "failed", "code": "x1", "duration_ms": 1, "message": "free text"}):
        answer = _result(world, client, jid, bad)
        assert answer.status_code == 400 and answer.json()["error"] == "bad_field", bad
    answer = _result(world, client, jid, {"status": "refused", "code": "not_found", "duration_ms": 3})
    assert answer.status_code == 200
    assert world.secure.outcome(jid) == {"status": "refused", "code": "not_found", "duration_ms": 3}


def test_a_job_another_component_holds_is_not_the_callers(world, channel):
    client, _ = channel
    world.secure.enqueue(make_job("fetch", "handbook"))
    jid = _take(world, client)["id"]
    answer = _result(world, client, jid, {"status": "ok", "duration_ms": 1}, name="knovas-connector")
    assert answer.status_code == 409 and answer.json()["error"] == "lease_lost"
    unknown = _result(world, client, "00000000-0000-4000-8000-000000000000", {"status": "ok", "duration_ms": 1})
    assert unknown.status_code == 409


# ------------------------------------------------------------------ Link answers for the site

def test_silent_past_the_deadline_is_unreachable_and_the_late_answer_lease_lost(world, channel):
    client, link = channel
    world.secure.enqueue(make_job("fetch", "handbook", seconds=0.6))
    jid = _take(world, client)["id"]
    outcome = _outcome(world, jid)
    assert (outcome["status"], outcome["code"]) == ("unreachable", "unreachable")
    late = _result(world, client, jid, _fetched([b"late"]))
    assert late.status_code == 409 and late.json()["error"] == "lease_lost"
    assert _part(world, client, jid, 0, b"late").status_code == 409
    assert link.telemetry.jobs == {"fetch": {"unreachable": 1}}


def test_a_job_nobody_takes_is_unreachable_at_its_deadline(world, channel):
    client, _ = channel
    jid = world.secure.enqueue(make_job("write", "handbook", seconds=0.4))["id"]
    assert _outcome(world, jid)["status"] == "unreachable"
    assert client.get("/local/v1/jobs?wait=0", headers=world.headers()).status_code == 204


def test_a_job_whose_deadline_passed_before_the_claim_is_unreachable(world, channel):
    client, link = channel
    job = world.secure.claim_now(make_job("fetch", "handbook", seconds=-1))
    client.portal.call(link.channel.accept, job)
    assert world.secure.outcome(job["id"])["code"] == "unreachable"


def test_no_component_for_the_kind_is_unsupported(world, channel):
    client, link = channel
    register(world, client, job_kinds=["write", "probe"])              # sync-component no longer fetches
    fetch = world.secure.enqueue(make_job("fetch", "handbook"))
    crm = world.secure.enqueue(make_job("fetch", "crm"))                 # its component never registered
    for jid in (fetch["id"], crm["id"]):
        outcome = _outcome(world, jid)
        assert (outcome["status"], outcome["code"]) == ("unsupported", "unsupported")
    elsewhere = world.secure.claim_now(make_job("fetch", "not-on-this-site"))
    client.portal.call(link.channel.accept, elsewhere)
    assert world.secure.outcome(elsewhere["id"])["status"] == "unsupported"
    assert client.get("/local/v1/jobs?wait=0", headers=world.headers()).status_code == 204, "never handed out"


def test_fetching_switched_off_is_refused_fetch_disabled(world, channel):
    client, link = channel
    jid = world.secure.enqueue(make_job("fetch", "archive"))["id"]
    outcome = _outcome(world, jid)
    assert (outcome["status"], outcome["code"]) == ("refused", "fetch_disabled")
    assert link.telemetry.jobs == {"fetch": {"refused": 1}}


@pytest.mark.parametrize("source", ["archive", "shares"])
def test_a_write_to_a_source_that_only_reads_is_refused_write_disabled(world, channel, source):
    client, _ = channel
    jid = world.secure.enqueue(make_job("write", source))["id"]
    outcome = _outcome(world, jid)
    assert (outcome["status"], outcome["code"]) == ("refused", "write_disabled")
    assert client.get("/local/v1/jobs?wait=0", headers=world.headers()).status_code == 204


def test_a_job_that_breaks_the_contract_is_answered_failed(world, channel):
    client, link = channel
    job = make_job("fetch", "handbook")
    job["args"]["path"] = "somewhere"                                    # args are closed
    claimed = world.secure.claim_now(job)
    client.portal.call(link.channel.accept, claimed)
    assert world.secure.outcome(job["id"])["status"] == "failed"
    assert world.secure.outcome(job["id"])["code"] == "bad_field"


# ------------------------------------------------------------------ leases

def test_a_late_result_after_another_claim_is_refused_and_the_rest_dropped(world, channel):
    client, _ = channel
    world.secure.enqueue(make_job("fetch", "handbook"))
    jid = _take(world, client)["id"]
    assert _part(world, client, jid, 0, b"one").status_code == 200
    world.secure.lease_runs_out(jid, claimed_by_another=True)
    late = _part(world, client, jid, 1, b"two")
    assert late.status_code == 409 and late.json()["error"] == "lease_lost"
    assert _result(world, client, jid, _fetched([b"one", b"two"])).status_code == 409
    assert world.secure.jobs[jid]["refused"] == ["part"], "after the 409 nothing more is relayed for that claim"
    assert world.secure.outcome(jid) is None and world.secure.parts(jid) == [b"one"]


def test_a_job_claimed_again_after_its_lease_ran_out_refuses_the_first_claims_late_result(world, channel):
    client, link = channel
    world.secure.enqueue(make_job("write", "handbook", seconds=20))
    jid = _take(world, client)["id"]
    world.secure.lease_runs_out(jid)                                     # Link's long-poll claims it again
    eventually(lambda: len(world.secure.jobs[jid]["leases"]) == 2)
    eventually(lambda: link.channel.held[jid].lease == world.secure.jobs[jid]["leases"][1])
    late = _result(world, client, jid, {"status": "ok", "source_ref": "https://git.example.test/pull/1", "duration_ms": 5})
    assert late.status_code == 409 and late.json()["error"] == "lease_lost"
    again = _take(world, client)
    assert again["id"] == jid and "lease" not in again, "handed out afresh under the new claim"
    answer = _result(world, client, jid, {"status": "ok", "source_ref": "https://git.example.test/pull/2", "duration_ms": 5})
    assert answer.status_code == 200
    assert world.secure.outcome(jid)["source_ref"].endswith("/pull/2")
    assert world.secure.jobs[jid]["refused"] == []


# ------------------------------------------------------------------ while waiting for the certificate

def test_waiting_for_the_certificate_claims_nothing(world):
    world.platform.issue_certificate = False
    job = world.secure.enqueue(make_job("fetch", "handbook"))
    with world.running(loops=("channel", "probe")) as (client, link):
        register(world, client)
        assert client.get("/local/v1/jobs?wait=1", headers=world.headers()).status_code == 204
        assert client.portal.call(link.channel.probe_now) is False
        assert link.channel.state == "down"
    assert world.secure.claims == [] and world.secure.queued == [job]
    assert link.channel.probe == {"last_at": None, "ok": None, "round_trip_ms": None}, "unknown, not failed"


# ------------------------------------------------------------------ probes

def test_a_probe_goes_round_the_channel_and_each_component_is_pinged_locally(world, channel):
    client, link = channel
    assert client.portal.call(link.channel.probe_now) is True
    probe = link.channel.probe
    assert probe["ok"] is True and isinstance(probe["round_trip_ms"], int) and probe["last_at"].endswith("Z")
    (answered,) = [e for e in world.secure.jobs.values() if e["job"]["kind"] == "probe"]
    assert answered["job"]["target_serial"] == SERIAL
    assert answered["result"]["status"] == "ok" and answered["refused"] == []
    local = _take(world, client)
    assert local["kind"] == "probe" and local["args"] == {} and "source" not in local and "target_serial" not in local
    assert local["actor"] == {"kind": "component", "id": "knovas-link"}
    assert client.get("/local/v1/jobs?wait=0", headers=world.headers("knovas-connector")).status_code == 204, \
        "a component that did not name probe gets none"
    assert _result(world, client, local["id"], {"status": "ok", "duration_ms": 0}).status_code == 200
    assert isinstance(link.registry.get("sync-component").local_ping_ms, int)
    assert link.registry.get("knovas-connector").local_ping_ms is None
    assert local["id"] not in world.secure.jobs, "a local probe never reaches Knovas"
    assert link.telemetry.jobs == {"probe": {"ok": 1}}


def test_a_probe_that_does_not_come_round_is_failed(world, channel, monkeypatch):
    client, link = channel
    import knovas_link.jobs as jobs
    monkeypatch.setattr(jobs, "PROBE_WAIT_SECONDS", 0.3)
    world.secure.busy = True                                             # the long-poll is refused: nothing comes round
    eventually(lambda: link.channel.state == "down")
    assert client.portal.call(link.channel.probe_now) is False
    assert link.channel.probe["ok"] is False and link.channel.probe["round_trip_ms"] is None


def test_a_silent_local_probe_leaves_the_ping_unknown(world, channel, monkeypatch):
    client, link = channel
    import knovas_link.jobs as jobs
    monkeypatch.setattr(jobs, "LOCAL_PROBE_SECONDS", 0.2)
    link.registry.get("sync-component").local_ping_ms = 7
    client.portal.call(link.channel.local_probes)
    eventually(lambda: link.registry.get("sync-component").local_ping_ms is None)


def test_the_platforms_check_command_probes_and_reports_done(world, channel):
    client, link = channel
    world.platform.commands = [{"id": "cmd-check", "command": "check", "args": {}}]
    client.portal.call(link.beat)
    done = eventually(lambda: [d for d in world.platform.done if d[0] == "cmd-check"])
    assert done == [("cmd-check", {"result": "ok"}, None)]
    assert link.channel.probe["ok"] is True


# ------------------------------------------------------------------ reconnecting

def test_the_channel_goes_down_and_comes_back(world, channel):
    client, link = channel
    world.secure.transport.down = True
    eventually(lambda: link.channel.state == "down")
    assert client.get("/local/v1/ping", headers=world.headers()).json()["work_channel"] == "down"
    world.secure.transport.down = False
    eventually(lambda: link.channel.state == "connected", timeout=5)
    job = world.secure.enqueue(make_job("fetch", "handbook"))
    assert _take(world, client) == job


def test_the_waiter_cap_is_respected_and_the_channel_comes_back(world, channel):
    client, link = channel
    world.secure.busy = True
    eventually(lambda: link.channel.state == "down")
    before = len(world.secure.claims)
    time.sleep(0.5)
    assert len(world.secure.claims) == before, "Retry-After is waited out, not hammered"
    world.secure.busy = False
    eventually(lambda: link.channel.state == "connected", timeout=5)
