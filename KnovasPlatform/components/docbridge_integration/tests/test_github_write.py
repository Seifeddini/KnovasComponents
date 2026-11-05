"""MVP-22: a write job becomes a pull request in the source's GitHub repository, never a commit on its branch.

The real component (link mode) against an in-process Knovas Link and an in-process GitHub, with the real brain-runner
``compile-write`` over a made-up company's configuration (tests/sync_link_world.py).
"""
from __future__ import annotations

import hashlib
import logging

import pytest

from fake_github_rest import blob_sha
from sync_link_world import AGENT, COMPONENT, PERSON, REMOTE, TOKEN, TRAVEL, build, run_job, write_job

from sync_component import github_jobs  # noqa: E402 - on the path through sync_link_world

NEW_SUMMARY = "Book trains for trips under six hours."


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = build(tmp_path, monkeypatch, credential="write-token")
    yield w
    w.close()


def _writes(world) -> list:
    return [c for c in world.github.calls if c[0] in ("POST", "PUT", "PATCH", "DELETE")]


class TestAProposalBecomesAPullRequest:
    def test_the_pull_request_carries_only_the_changed_values(self, world):
        job = write_job({"summary": NEW_SUMMARY, "reviewed_on": "2026-11-05"}, world.travel_version)
        result = run_job(world, job)
        assert result["status"] == "ok" and "code" not in result and isinstance(result["duration_ms"], int)
        pull = world.github.pulls[0]
        assert result["source_ref"] == pull["html_url"] and isinstance(result["source_ref"], str)
        branch = "knovas/write-" + job["id"]
        assert (pull["head"], pull["base"]) == (branch, "main")
        assert world.github.file_at(branch, "policies/travel.md") == TRAVEL.replace(
            b"Book the cheapest fare.", NEW_SUMMARY.encode()).replace(b"2026-01-10", b"2026-11-05")
        assert world.github.file_at("main", "policies/travel.md") == TRAVEL, "the branch itself is never written"
        assert world.github.file_at(branch, "policies/remote.md") == REMOTE

    def test_the_commit_names_the_agent_and_only_a_hash_of_the_person(self, world):
        job = write_job({"summary": NEW_SUMMARY}, world.travel_version)
        run_job(world, job)
        commit = world.github.commits[world.github.branches["knovas/write-" + job["id"]]]
        person = hashlib.sha256(PERSON.encode()).hexdigest()[:12]
        assert AGENT in commit["message"] and person in commit["message"] and job["id"] in commit["message"]
        assert PERSON not in commit["message"]
        pull = world.github.pulls[0]
        assert job["id"] in pull["body"] and AGENT in pull["body"] and PERSON not in pull["body"] + pull["title"]

    def test_a_job_claimed_again_ends_with_the_same_pull_request(self, world):
        job = write_job({"summary": NEW_SUMMARY}, world.travel_version)
        first = github_jobs.write_proposal(job, world.worker.source("handbook"), "write-token",
                                           compile_change=world.worker.compile_change(world.worker.source("handbook")))
        again = github_jobs.write_proposal(job, world.worker.source("handbook"), "write-token",
                                           compile_change=world.worker.compile_change(world.worker.source("handbook")))
        assert first["status"] == again["status"] == "ok" and first["source_ref"] == again["source_ref"]
        assert len(world.github.pulls) == 1

    def test_without_a_ref_the_default_branch_takes_the_pull_request(self, world):
        world.worker.source("handbook")["settings"].pop("ref")
        world.github.default_branch = "main"
        result = run_job(world, write_job({"summary": NEW_SUMMARY}, world.travel_version))
        assert result["status"] == "ok" and world.github.pulls[0]["base"] == "main"


class TestNothingIsWritten:
    def test_a_file_changed_since_the_version_is_a_conflict(self, world):
        newer = TRAVEL.replace(b"cheapest", b"most sensible")
        world.github.commit({"policies/travel.md": newer})
        result = run_job(world, write_job({"summary": NEW_SUMMARY}, world.travel_version))
        assert (result["status"], result["code"], result["version"]) == ("conflict", "conflict", blob_sha(newer))
        assert _writes(world) == [] and world.github.pulls == []

    def test_what_the_inverse_mapping_refuses_is_the_jobs_refusal(self, world):
        result = run_job(world, write_job({"state": "retired"}, world.travel_version))       # computed in the ontology
        assert (result["status"], result["code"]) == ("refused", "computed_attribute")
        result = run_job(world, write_job({"summary": NEW_SUMMARY}, world.travel_version, mapping="no-such-mapping"))
        assert (result["status"], result["code"]) == ("refused", "unknown_mapping")
        assert world.github.calls == [], "a refused job never reaches the source"

    def test_a_mode_other_than_proposal_is_refused(self, world):
        job = write_job({"summary": NEW_SUMMARY}, world.travel_version)
        job["args"]["mode"] = "standing"
        assert world.worker.answer(job) == {"status": "refused", "code": "mode_unsupported"}
        assert world.github.calls == []

    def test_a_token_that_cannot_write_is_refused(self, tmp_path, monkeypatch):
        w = build(tmp_path, monkeypatch, credential="read-token")
        try:
            result = run_job(w, write_job({"summary": NEW_SUMMARY}, w.travel_version))
            assert (result["status"], result["code"]) == ("refused", "token_cannot_write")
            assert w.github.pulls == [] and w.github.file_at("main", "policies/travel.md") == TRAVEL
        finally:
            w.close()

    def test_a_rate_limit_is_not_taken_for_a_token_that_cannot_write(self, world):
        world.github.rate_limited = True
        result = run_job(world, write_job({"summary": NEW_SUMMARY}, world.travel_version))
        assert (result["status"], result["code"]) == ("failed", "github.rate_limited")

    def test_a_source_switched_to_read_is_refused(self, tmp_path, monkeypatch):
        w = build(tmp_path, monkeypatch, github_access="read")
        try:
            result = run_job(w, write_job({"summary": NEW_SUMMARY}, w.travel_version))
            assert (result["status"], result["code"]) == ("refused", "write_disabled")
            assert w.github.calls == []
        finally:
            w.close()

    def test_a_file_gone_from_the_branch_is_not_found(self, world):
        world.github.commit({"policies/travel.md": None})
        result = run_job(world, write_job({"summary": NEW_SUMMARY}, world.travel_version))
        assert (result["status"], result["code"]) == ("refused", "not_found")
        assert _writes(world) == []

    def test_a_front_matter_the_editor_cannot_change_safely_is_refused(self, world):
        odd = b"---\nid: POLICY-travel\nsummary:\n  - a list\n---\nbody\n"
        world.github.commit({"policies/travel.md": odd})
        result = run_job(world, write_job({"summary": NEW_SUMMARY}, blob_sha(odd)))
        assert (result["status"], result["code"]) == ("refused", "edit_unsupported")
        assert _writes(world) == []

    def test_the_same_values_change_nothing(self, world):
        result = run_job(world, write_job({"summary": "Book the cheapest fare."}, world.travel_version))
        assert (result["status"], result["code"]) == ("refused", "no_change")
        assert _writes(world) == []


class TestTheCompiledChange:
    @pytest.mark.parametrize("edits", [
        [{"path": "data.summary", "value": "x"}],                    # a YAML or JSON file's value: not edited yet
        [{"path": "front_matter.meta.summary", "value": "x"}],       # below the front matter's top level
    ])
    def test_a_path_the_editor_does_not_take_is_refused(self, edits):
        args = {"external_id": "policies/travel.md", "expected_version": "a" * 40}
        with pytest.raises(github_jobs.Outcome) as out:
            github_jobs._edits({**args, "edits": edits}, args)
        assert out.value.result == {"status": "refused", "code": "edit_unsupported"}

    def test_a_change_for_another_file_is_not_applied(self):
        args = {"external_id": "policies/travel.md", "expected_version": "a" * 40}
        change = {"external_id": "policies/other.md", "expected_version": "a" * 40,
                  "edits": [{"path": "front_matter.summary", "value": "x"}]}
        with pytest.raises(github_jobs.Outcome) as out:
            github_jobs._edits(change, args)
        assert out.value.result["code"] == "runner.bad_change"


def test_logs_carry_codes_counts_and_durations_only(world, caplog):
    caplog.set_level(logging.DEBUG)
    run_job(world, write_job({"summary": NEW_SUMMARY}, world.travel_version))
    run_job(world, write_job({"state": "retired"}, world.travel_version))
    own = [r for r in caplog.records if r.name.split(".")[0] in ("sync_component", "urllib3", "requests")]
    text = "\n".join(r.getMessage() for r in own)
    for secret in ("policies", "travel", "handbook", "example-org", NEW_SUMMARY, "cheapest", TOKEN, "write-token",
                   AGENT, PERSON, "knovas/write-"):
        assert secret not in text, secret
    assert "job.done write ok" in text and "computed_attribute" in text
    assert COMPONENT not in text
