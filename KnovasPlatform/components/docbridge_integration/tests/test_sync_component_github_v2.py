"""The GitHub connector of connector protocol 2.0 (draft; FEAT-2026-11-10-write-back C-3, BET-2026-11-write-back WB-12):
the connector kit's conformance suite against an in-process GitHub, and the direct and native_review paths one by one.

brain-runner's compile-write is replaced by a stand-in that turns each fact into the front-matter key of the same name
and refuses the computed fact ``updated``; the real compile-write is bound by the 1.x write tests. The repository and
everything in it are made up.
"""
from __future__ import annotations

import re
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
import requests

from fake_github_rest import REPO, FakeGitHub, blob_sha

SYNC_SRC = Path(__file__).resolve().parents[2] / "sync_component" / "src"
if str(SYNC_SRC) not in sys.path:
    sys.path.insert(0, str(SYNC_SRC))

from knovas_connector_kit.conformance import run  # noqa: E402
from knovas_connector_kit.conformance.suite import Brain  # noqa: E402
from sync_component import github_v2  # noqa: E402

SOURCE = {"workspace": "ws-1", "key": "handbook", "kind": "github", "access": "read_write", "fetch": True,
          "settings": {"repository": REPO, "ref": "main"}, "component": "sync-component"}
PAGE = "---\ntitle: {title}\nowner: {owner}\nstatus: open\nupdated: 2026-11-01\n---\n\n# {title}\n\nBody text.\n"


def fake_compile(source):
    def run_job(job):
        args = job["args"]
        if "updated" in args["facts"]:
            return "refused", "computed_attribute"
        return "ok", {"external_id": args["external_id"], "expected_version": args["expected_version"],
                      "edits": [{"path": "front_matter." + k, "value": v} for k, v in args["facts"].items()]}
    return run_job


class FlakySession(requests.Session):
    """A session whose next request fails with an error text that quotes the credential."""

    def __init__(self, secret: str):
        super().__init__()
        self.secret, self.fail_next = secret, False

    def request(self, *a, **kw):
        if self.fail_next:
            self.fail_next = False
            raise requests.ConnectionError(f"connection refused while using {self.secret}")
        return super().request(*a, **kw)


class GitHubHarness:
    source = "handbook"

    def __init__(self):
        self.credential = "canary-token-" + uuid.uuid4().hex[:12]
        self.gh = FakeGitHub(tokens={self.credential: "write"})
        self.seed = self.gh.commit({f"pages/{n}.md": PAGE.format(title=f"Page {n}", owner=f"person-{n}").encode()
                                    for n in "abcd"})
        self.server = self.gh.serve()
        self.session = FlakySession(self.credential)
        self.data_dir = Path(tempfile.mkdtemp(prefix="gh-v2-"))
        self.connector = github_v2.GitHubConnector(
            source=lambda key: dict(SOURCE) if key == SOURCE["key"] else None,
            credential=lambda s: self.credential, compile=fake_compile,
            facts=lambda: ["owner", "status", "updated"], data_dir=self.data_dir, session=self.session,
            api_base=self.server.url)

    def close(self):
        self.server.server.shutdown()

    def sample_steps(self):
        return [{"action": "set_front_matter", "target": "pages/a.md", "params": {"mapping": "pages", "owner": "person-7"}},
                {"action": "set_front_matter", "target": "pages/b.md", "params": {"mapping": "pages", "status": "closed"}}]

    def computed_step(self):
        return {"action": "set_front_matter", "target": "pages/c.md",
                "params": {"mapping": "pages", "updated": "2026-11-11"}}

    def change_behind(self, target):
        data = self.gh.file_at("main", target)
        self.gh.commit({target: data.replace(b"Body text.", b"Body text, edited elsewhere.")})

    def delete_behind(self, target):
        self.gh.commit({target: None})

    def set_cut_listing(self, on):
        pass

    def writes(self, target):
        n, at = 0, self.gh.branches["main"]
        while at is not None and at != self.seed:
            c = self.gh.commits[at]
            if c["tree"].get(target) != self.gh.commits[c["parent"]]["tree"].get(target):
                n += 1
            at = c["parent"]
        return n

    def crash_after_next_write(self):
        self.gh.after_put = lambda path, branch: setattr(self.gh, "fail_next_read", True)

    def fail_next_call_with_secret(self):
        self.session.fail_next = True

    def alter_next_write(self):
        def trigger(path, branch):
            data = self.gh.file_at(branch, path)
            self.gh.commit({path: re.sub(rb"owner: (\S+)", rb"owner: \1-altered", data)}, branch=branch)
        self.gh.after_put = trigger

    def review(self, stage_ref, accept):
        self.gh.review(int(stage_ref.rsplit("/", 1)[1]), accept)


harnesses: list = []


def make_harness():
    h = GitHubHarness()
    harnesses.append(h)
    return h


@pytest.fixture(autouse=True)
def _close_servers():
    yield
    while harnesses:
        harnesses.pop().close()


def test_the_github_connector_passes_the_conformance_suite():
    report = run(make_harness)
    assert report["failed"] == {}
    assert report["passed"] == github_v2.CONFORMANCE_PASSED
    assert report["not_applicable"] == ["replay_changes_nothing", "deletion_guard"], "reading stays 1.x's for now"


def test_the_declaration_is_generic_and_names_the_writable_facts():
    h = make_harness()
    d = h.connector.declaration()
    action = d["actions"][0]
    assert (d["source_kind"], action["name"], action["staging"], action["inverse"]) == (
        "github", "set_front_matter", ["direct", "native_review"], None)
    assert [p["name"] for p in action["params"]] == ["mapping", "owner", "status", "updated"]


def _preview_and_act(h, brain, step, staging="direct"):
    prev = h.connector.handle(brain.preview([step], staging))
    version = prev["preview"]["read_versions"][0]["version"]
    job = brain.act([step], [version], staging)
    return prev, job, h.connector.handle(job)


def test_a_direct_write_is_one_commit_on_the_branch_with_the_permit_and_no_pull_request():
    h = make_harness()
    brain = Brain(h.source)
    step = h.sample_steps()[0]
    prev, job, res = _preview_and_act(h, brain, step)
    assert prev["preview"]["exact"] and any(c["field"] is None and c["diff"] for c in prev["preview"]["changes"])
    receipt = res["receipts"][0]
    assert res["status"] == "ok" and receipt["outcome"] == "written" and receipt["differences"] == []
    head = h.gh.commits[h.gh.branches["main"]]
    assert "Knovas-Permit: " + receipt["permit_id"] in head["message"].splitlines()
    assert h.gh.pulls == [] and receipt["version_after"] == blob_sha(h.gh.file_at("main", "pages/a.md"))
    text = h.gh.file_at("main", "pages/a.md").decode()
    assert "owner: person-7" in text and "status: open" in text and "Body text." in text


def test_a_file_changed_after_the_preview_is_a_conflict_and_nothing_is_written():
    h = make_harness()
    brain = Brain(h.source)
    step = h.sample_steps()[0]
    prev = h.connector.handle(brain.preview([step]))
    h.change_behind(step["target"])
    res = h.connector.handle(brain.act([step], [prev["preview"]["read_versions"][0]["version"]]))
    assert (res["status"], res["code"]) == ("conflict", "version_changed")
    assert res["conflict"]["found_version"] == blob_sha(h.gh.file_at("main", "pages/a.md"))
    assert h.writes("pages/a.md") == 1, "only the other edit"


def test_native_review_opens_a_pull_request_and_follows_its_outcome():
    h = make_harness()
    brain = Brain(h.source)
    step = h.sample_steps()[1]
    _prev, job, res = _preview_and_act(h, brain, step, "native_review")
    ref = res["receipts"][0]["source_ref"]
    assert res["receipts"][0]["outcome"] == "staged" and ref == h.gh.pulls[0]["html_url"]
    pid = res["receipts"][0]["permit_id"]
    ask = lambda: h.connector.handle(brain.job("review_outcome", {"stage_ref": ref, "permit_ids": [pid]}))  # noqa: E731
    assert ask()["review"]["state"] == "pending"
    h.review(ref, True)
    accepted = ask()["review"]
    assert accepted["state"] == "accepted" and accepted["receipts"][0]["differences"] == []
    assert "status: closed" in h.gh.file_at("main", "pages/b.md").decode()


def test_unstage_is_not_offered_in_v1():
    h = make_harness()
    res = h.connector.handle(Brain(h.source).job("unstage", {"stage_ref": "x", "permit_ids": [str(uuid.uuid4())]}))
    assert res["status"] == "unsupported"


def test_a_source_that_only_reads_is_never_written():
    h = make_harness()
    brain = Brain(h.source)
    step = h.sample_steps()[0]
    prev = h.connector.handle(brain.preview([step]))
    h.connector._source = lambda key: dict(SOURCE, access="read")
    res = h.connector.handle(brain.act([step], [prev["preview"]["read_versions"][0]["version"]]))
    assert (res["status"], res["code"]) == ("refused", "write_disabled") and h.writes("pages/a.md") == 0


def test_the_vendored_kit_is_the_kit_byte_for_byte():
    """The image's build context is the component's folder, so it carries a copy of the kit; it must be the kit."""
    kit = Path(__file__).resolve().parents[4] / "KnovasConnectorKit" / "src" / "knovas_connector_kit"
    copy = SYNC_SRC / "knovas_connector_kit"
    files = lambda root: sorted(p.relative_to(root) for p in root.rglob("*")  # noqa: E731
                                if p.is_file() and "__pycache__" not in p.parts)
    assert files(copy) == files(kit)
    for rel in files(kit):
        assert (copy / rel).read_bytes() == (kit / rel).read_bytes(), rel
