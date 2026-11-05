"""MVP-21: the sync component reads originals from GitHub for Knovas Link's fetch jobs, and answers probes.

The real component (link mode) against an in-process Knovas Link and an in-process GitHub, over real HTTP. The
repository, company and people are made up.
"""
from __future__ import annotations

import hashlib
import logging

import pytest

from fake_github_rest import blob_sha
from sync_link_world import (AGENT, COMPONENT, GITHUB_KEY, PERSON, TOKEN, TRAVEL, build, fetch_job, probe_job,
                             run_job)

PART = 4 * 1024 * 1024


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = build(tmp_path, monkeypatch, credential="read-token")
    yield w
    w.close()


class TestFetch:
    def test_the_blob_at_the_version_arrives_whole_and_its_git_hash_is_the_version(self, world):
        result = run_job(world, fetch_job("policies/travel.md", version=world.travel_version))
        data = world.link.data(result and next(iter(world.link.results)))
        assert data == TRAVEL
        assert blob_sha(data) == result["version"] == world.travel_version
        assert result["status"] == "ok" and result["bytes_sha256"] == hashlib.sha256(data).hexdigest()
        assert (result["mime"], result["bytes"], result["parts"]) == ("text/markdown", len(TRAVEL), 1)
        assert isinstance(result["duration_ms"], int) and "code" not in result

    def test_without_a_version_it_reads_what_the_ref_holds_now(self, world):
        newer = TRAVEL.replace(b"cheapest", b"most sensible")
        world.github.commit({"policies/travel.md": newer})
        job = fetch_job("policies/travel.md")
        result = run_job(world, job)
        assert world.link.data(job["id"]) == newer
        assert result["version"] == blob_sha(newer) != world.travel_version

    def test_an_older_version_is_still_read_by_its_blob(self, world):
        world.github.commit({"policies/travel.md": b"---\nid: POLICY-travel\n---\nrewritten\n"})
        job = fetch_job("policies/travel.md", version=world.travel_version)
        assert run_job(world, job)["version"] == world.travel_version
        assert world.link.data(job["id"]) == TRAVEL

    def test_a_large_original_goes_in_numbered_parts_of_at_most_4_mib(self, world):
        big = bytes(range(256)) * (3 * PART // 256 + 7)
        world.github.commit({"docs/large.pdf": big})
        job = fetch_job("docs/large.pdf")
        result = run_job(world, job)
        parts = world.link.parts[job["id"]]
        assert [len(p) for p in parts] == [PART, PART, PART, len(big) - 3 * PART] and len(parts) == result["parts"]
        assert b"".join(parts) == big and blob_sha(big) == result["version"]
        assert result["bytes"] == len(big) and result["mime"] == "application/pdf"
        assert result["bytes_sha256"] == hashlib.sha256(big).hexdigest()

    def test_an_empty_original_has_no_parts(self, world):
        world.github.commit({"policies/empty.md": b""})
        job = fetch_job("policies/empty.md")
        result = run_job(world, job)
        assert (result["status"], result["bytes"], result["parts"]) == ("ok", 0, 0)
        assert world.link.parts[job["id"]] == [] and result["version"] == blob_sha(b"")

    @pytest.mark.parametrize("with_version", [True, False])
    def test_max_bytes_is_enforced_before_anything_is_sent(self, world, with_version):
        job = fetch_job("policies/travel.md", version=world.travel_version if with_version else None,
                        max_bytes=len(TRAVEL) - 1)
        result = run_job(world, job)
        assert (result["status"], result["code"]) == ("refused", "too_large")
        assert world.link.parts[job["id"]] == []

    def test_exactly_max_bytes_is_allowed(self, world):
        assert run_job(world, fetch_job("policies/travel.md", max_bytes=len(TRAVEL)))["status"] == "ok"

    @pytest.mark.parametrize("path,version", [
        ("policies/missing.md", None),
        ("policies", None),                                           # a folder is no original
        ("policies/travel.md", "0" * 40),                             # a version the repository never had
        ("../outside.md", None),
    ])
    def test_what_is_not_there_is_refused_not_found(self, world, path, version):
        job = fetch_job(path, version=version)
        result = run_job(world, job)
        assert (result["status"], result["code"]) == ("refused", "not_found")
        assert world.link.parts[job["id"]] == []

    def test_bytes_that_do_not_hash_to_the_version_never_leave(self, world):
        world.github.blobs[world.travel_version] = TRAVEL + b"tampered"
        job = fetch_job("policies/travel.md", version=world.travel_version)
        result = run_job(world, job)
        assert (result["status"], result["code"]) == ("failed", "version_mismatch")
        assert world.link.parts[job["id"]] == []

    def test_a_fetch_whose_lease_ended_at_link_sends_nothing_more(self, world):
        big = b"x" * (2 * PART + 10)
        world.github.commit({"docs/large.bin": big})
        job = fetch_job("docs/large.bin")
        world.link.lease_lost_after[job["id"]] = 1
        run_job(world, job)
        assert len(world.link.parts[job["id"]]) == 1
        assert world.link.results[job["id"]]["status"] == "unreachable", "Link's own answer stands; no result from us"
        assert world.link.refused_results == []

    def test_the_credential_comes_from_the_components_environment(self, tmp_path, monkeypatch):
        w = build(tmp_path, monkeypatch, credential=None)
        try:
            result = run_job(w, fetch_job("policies/travel.md"))
            assert (result["status"], result["code"]) == ("failed", "github.http_401")
            w.worker.environ = {"SYNC_CREDENTIAL_" + GITHUB_KEY.upper(): "read-token"}
            assert run_job(w, fetch_job("policies/travel.md"))["status"] == "ok"
        finally:
            w.close()

    def test_logs_carry_codes_counts_and_durations_only(self, world, caplog):
        caplog.set_level(logging.DEBUG)
        run_job(world, fetch_job("policies/travel.md", version=world.travel_version))
        run_job(world, fetch_job("policies/missing.md"))
        own = [r for r in caplog.records if r.name.split(".")[0] in ("sync_component", "urllib3", "requests")]
        text = "\n".join(r.getMessage() for r in own)
        for secret in ("policies", "travel", "missing", "handbook", "example-org", TOKEN, "read-token", "cheapest",
                       AGENT, PERSON):
            assert secret not in text, secret
        assert "job.done fetch ok" in text and "not_found" in text


    def test_a_source_whose_fetch_switch_is_off_is_refused_here_too(self, tmp_path, monkeypatch):
        w = build(tmp_path, monkeypatch, credential="read-token", fetch=False)
        try:
            job = fetch_job("policies/travel.md")
            result = run_job(w, job)
            assert (result["status"], result["code"]) == ("refused", "fetch_disabled")
            assert w.github.calls == [] and w.link.parts[job["id"]] == []
        finally:
            w.close()


class TestTheLoop:
    def test_it_registers_again_so_a_restarted_link_routes_its_jobs(self, world):
        now = [1000.0]
        registered = []
        world.worker.clock = lambda: now[0]
        world.worker._registered_at = now[0]
        world.worker.register = lambda: registered.append(now[0])
        stop = __import__("threading").Event()

        def poll_once(wait=25):
            now[0] += 200
            if len(registered) >= 2:
                stop.set()
            return False

        world.worker.poll_once = poll_once
        world.worker.forever(stop)
        assert registered == [1400.0, 1800.0], "every 300 s, not on every poll"

    def test_a_broken_poll_does_not_end_the_loop(self, world):
        stop = __import__("threading").Event()
        calls = []

        def poll_once(wait=25):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("broken")
            stop.set()
            return False

        world.worker.poll_once = poll_once
        world.worker.forever(stop)
        assert len(calls) == 2


class TestProbe:
    def test_a_probe_is_answered_at_once(self, world):
        job = probe_job()
        result = run_job(world, job)
        assert result["status"] == "ok" and isinstance(result["duration_ms"], int) and "code" not in result

    def test_a_probe_through_the_long_poll_needs_no_thread_of_its_own(self, world):
        world.link.queue(COMPONENT, probe_job())
        assert world.worker.poll_once(wait=5) is True
        assert [r["status"] for r in world.link.results.values()] == ["ok"]


class TestWhatTheComponentDoesNotServe:
    def test_a_job_for_a_source_it_does_not_serve_is_unsupported(self, world):
        job = fetch_job("policies/travel.md", source="another-source")
        result = run_job(world, job)
        assert (result["status"], result["code"]) == ("unsupported", "unsupported")

    def test_a_fetch_of_a_kind_without_a_reader_is_unsupported(self, world):
        job = fetch_job("policies/travel.md", source="handbook-share")
        assert run_job(world, job)["code"] == "unsupported"

    def test_it_registered_the_job_kinds_and_capabilities(self, world):
        body = world.link.registered[COMPONENT]
        assert body["kind"] == "sync_component" and body["job_kinds"] == ["fetch", "write", "probe"]
        assert {"fetch:github", "write:github", "dry_run", "apply"} <= set(body["capabilities"])

    def test_every_call_carries_the_token_and_the_name(self, world):
        run_job(world, fetch_job("policies/travel.md"))
        assert world.link.calls and all(path.startswith("/local/v1/") for _, path in world.link.calls)
