"""The sync component's automatic GitHub → Brain syncing (FEAT-2026-11-05-knovas-brain-v1, KG-2; protocol 1.2).

- «bei neuen Commits»: every ``poll_seconds`` the component asks each GitHub source for its head (one request). The
  head it last synced or planned: nothing more, no plan and no Brain call. A new head: brain-runner's ``affected``
  names the mappings the commits concern, only those are planned (each whole) and reported as today; a new package,
  or a change ``affected`` cannot narrow, plans every mapping. The synced commit is written only after an apply
  without errors.
- Automatic apply, for a source a person switched it on for: a plan the schedule made is applied right after, only
  when it holds nothing a person must decide (``engine.attention``). A plan a person asked for never is.
- The heartbeat names per source the synced commit, the last apply's counts and the next check.

brain-runner stands in (``Runner`` answers as its command line does); the Platform or Knovas Link is ``Client``, as
the protocol's answers look. Made-up company and repository.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SYNC_SRC = Path(__file__).resolve().parents[2] / "sync_component" / "src"
if str(SYNC_SRC) not in sys.path:
    sys.path.insert(0, str(SYNC_SRC))

from sync_component import engine as E  # noqa: E402
from sync_component.agent import Agent, poll_seconds, schedule_due  # noqa: E402
from sync_component.engine import RunnerEngine, attention  # noqa: E402
from sync_component.fetchers import FetchError, Head, Reading, head_github  # noqa: E402

WORKSPACE = "0b8f8f8e-1111-4222-8333-944444444444"
KEY = "handbuch"
SYNCED, NEWER, NEWEST = "1" * 40, "2" * 40, "3" * 40
MAPPINGS = ["seiten", "personen"]
FILES = {"brain-config.yaml": base64.b64encode(b"version: 1\n").decode()}


class Runner:
    """brain-runner's command line: ``affected`` prints one JSON line, ``plan`` writes a plan, ``apply`` its counts."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.concerned = ["seiten"]
        self.affected_exit = 0
        self.plan_commit = NEWER
        self.plan_extra: dict = {}
        self.blocked: list = []
        self.apply_errors = 0

    def __call__(self, argv, **kw):
        args = argv[3:]
        self.calls.append(args)
        if args[0] == "affected":
            return SimpleNamespace(returncode=self.affected_exit, stderr="", stdout=json.dumps(
                {"head": self.plan_commit, "since": args[args.index("--since") + 1], "mappings": self.concerned,
                 "new_commits": True, "changed": 1}) + "\n")
        if args[0] == "plan":
            mappings = [args[i + 1] for i, a in enumerate(args) if a == "--mapping"]
            plan = {"version": 1, "summary": {m: {"add": 2, "change": 1, "retire": 0} for m in mappings},
                    "blocked": list(self.blocked), "guard": {m: {"tripped": True, "types": {}} for m in self.blocked},
                    "drift": [], "unresolved": [], "released_mappings": [], "items": [],
                    "source": {"kind": "github", "commit": self.plan_commit}, **self.plan_extra}
            Path(args[args.index("--out") + 1]).write_text(json.dumps(plan), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[0] == "apply":
            blocked = [] if "--approve-guard" in args else self.blocked
            return SimpleNamespace(returncode=1 if self.apply_errors else 0, stderr="", stdout=json.dumps(
                {"writes": 3, "errors": self.apply_errors, "done": {"seiten.added": 2, "seiten.modified": 1},
                 "blocked": blocked}))
        raise AssertionError(args)

    def kinds(self) -> list[str]:
        return [c[0] for c in self.calls]

    def planned(self) -> list[str]:
        plan = [c for c in self.calls if c[0] == "plan"][-1]
        return [plan[i + 1] for i, a in enumerate(plan) if a == "--mapping"]


class Client:
    """The Platform's protocol (or Knovas Link's local API) as the component sees it."""

    def __init__(self, schedule=None, auto_apply=False, kind="github"):
        self.schedule = schedule or {"mode": "commits", "poll_seconds": 30}
        self.paused = False
        self.source = {"workspace": WORKSPACE, "key": KEY, "kind": kind, "access": "read", "fetch": False,
                       "settings": {"repository": "beispiel-firma/handbuch", "ref": "main"}, "auto_apply": auto_apply}
        self.files = dict(FILES)
        self.beats, self.runs, self.codes, self.done, self.commands = [], [], [], [], []
        self.config_reads = 0

    def heartbeat(self, **body):
        self.beats.append(body)
        commands, self.commands = self.commands, []
        return {"paused": self.paused, "schedule": self.schedule, "commands": commands}

    def config(self):
        self.config_reads += 1
        return {"sources": [dict(self.source)], "schedule": self.schedule,
                "mappings": [{"workspace": WORKSPACE, "id": m, "source": KEY} for m in MAPPINGS]}

    def package(self):
        return {"workspaces": [{"workspace": WORKSPACE, "files": dict(self.files)}]}

    def report_run(self, **report):
        self.runs.append(report)
        return "run"

    def events(self, events):
        self.codes.extend(e["code"] for e in events)

    def command_done(self, cid, result):
        self.done.append((cid, result))

    def report_checks(self, checks):
        pass


class Clock:
    def __init__(self):
        self.t = dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.timezone.utc)

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t += dt.timedelta(seconds=seconds)


@pytest.fixture
def w(tmp_path):
    runner, client, clock = Runner(), Client(), Clock()
    heads = {"commit": SYNCED, "asked": 0}

    def head(settings, credential, etag=None):
        heads["asked"] += 1
        return Head(heads["commit"], "etag-" + heads["commit"][:4])

    engine = RunnerEngine(tmp_path / "sources", {}, run=runner)
    agent = Agent(client, engine, fetchers={"github": lambda settings, credential, root: Reading(args=["--source", "github"])},
                  checks={"github": lambda settings, credential: {}}, now=clock, heads={"github": head})
    root = engine.source_root(WORKSPACE, KEY)
    return SimpleNamespace(runner=runner, client=client, clock=clock, heads=heads, engine=engine, agent=agent, root=root,
                           tmp=tmp_path)


def _synced(w, commit=SYNCED):
    """The state a first full sync leaves: commit synced under the current package."""
    w.engine.update_sync_state(w.root, synced_commit=commit, synced_at="2026-11-10T08:00:00+00:00",
                               package=E.package_hash(w.client.files))


def _poll(w, seconds=30):
    w.clock.tick(seconds)
    w.agent.tick()


# ------------------------------------------------------------------ diff polling

class TestDiffPolling:
    def test_the_head_last_synced_plans_nothing_and_calls_no_brain(self, w):
        _synced(w)
        for _ in range(3):
            _poll(w)
        assert w.heads["asked"] == 3 and w.runner.calls == [] and w.client.runs == []
        assert w.engine.sync_state(w.root)["checked_at"] == w.clock.t.isoformat()

    def test_a_new_commit_plans_only_the_mappings_it_concerns_and_reports_as_today(self, w):
        _synced(w)
        w.heads["commit"] = NEWER
        _poll(w)
        affected = w.runner.calls[0]
        assert affected[0] == "affected" and affected[affected.index("--since") + 1] == SYNCED
        assert affected[affected.index("--repo") + 1] == "beispiel-firma/handbuch" and "--json" in affected
        assert w.runner.kinds() == ["affected", "plan"] and w.runner.planned() == ["seiten"]
        assert [(r["kind"], r["status"], r["added"]) for r in w.client.runs] == [("dry_run", "ok", 2)]
        assert w.engine.sync_state(w.root)["planned_commit"] == NEWER
        assert w.engine.sync_state(w.root)["synced_commit"] == SYNCED, "a dry run syncs nothing"
        _poll(w)
        assert w.runner.kinds() == ["affected", "plan"], "the planned head is not planned again while it waits"

    def test_a_change_affected_cannot_narrow_plans_every_mapping(self, w):
        _synced(w)
        w.runner.concerned = None                     # the configuration or ontology changed, or compare could not say
        w.heads["commit"] = NEWER
        _poll(w)
        assert sorted(w.runner.planned()) == sorted(MAPPINGS)

    def test_a_new_package_plans_every_mapping_without_asking(self, w):
        _synced(w)
        w.client.files = {**FILES, "mappings/seiten.yaml": base64.b64encode(b"id: seiten\n").decode()}
        w.heads["commit"] = NEWER
        _poll(w)
        assert w.runner.kinds() == ["plan"] and sorted(w.runner.planned()) == sorted(MAPPINGS)

    def test_without_a_synced_commit_the_first_poll_plans_everything(self, w):
        w.heads["commit"] = NEWER
        _poll(w)
        assert w.runner.kinds() == ["plan"] and sorted(w.runner.planned()) == sorted(MAPPINGS)

    def test_commits_that_concern_no_mapping_plan_nothing_and_report_nothing(self, w):
        _synced(w)
        w.runner.concerned = []
        w.heads["commit"] = NEWER
        _poll(w)
        assert w.runner.kinds() == ["affected"] and w.client.runs == []
        state = w.engine.sync_state(w.root)
        assert state["planned_commit"] == NEWER and state["synced_commit"] == SYNCED
        _poll(w)
        assert w.runner.kinds() == ["affected"]

    def test_when_affected_fails_every_mapping_is_planned(self, w):
        _synced(w)
        w.runner.affected_exit = 4
        w.heads["commit"] = NEWER
        _poll(w)
        assert sorted(w.runner.planned()) == sorted(MAPPINGS) and "runner.affected_exit_4" in w.client.codes

    def test_it_asks_only_every_poll_seconds(self, w):
        _synced(w)
        w.client.schedule = {"mode": "commits", "poll_seconds": 120}
        _poll(w, 30)
        _poll(w, 30)
        _poll(w, 30)
        assert w.heads["asked"] == 1
        _poll(w, 90)
        assert w.heads["asked"] == 2
        assert poll_seconds({"poll_seconds": 5}) == 15 and poll_seconds({"poll_seconds": 99999}) == 3600
        assert not schedule_due({"mode": "commits", "poll_seconds": 30}, None, w.clock.t)

    def test_a_paused_component_asks_nothing(self, w):
        w.client.paused = True
        w.heads["commit"] = NEWER
        _poll(w)
        assert w.heads["asked"] == 0 and w.runner.calls == []

    def test_only_sources_with_commits_are_polled(self, w):
        w.client.source["kind"] = "http_json"
        w.agent.fetchers["http_json"] = lambda settings, credential, root: Reading()
        _poll(w)
        assert w.heads["asked"] == 0 and w.runner.calls == []

    def test_a_failed_plan_is_tried_again_after_a_while_not_on_every_poll(self, w):
        _synced(w)
        w.heads["commit"] = NEWER
        w.agent.fetchers["github"] = lambda *a: (_ for _ in ()).throw(FetchError("fetch.http_502"))
        _poll(w)
        assert w.client.runs[-1]["status"] == "failed"
        _poll(w)
        assert len(w.client.runs) == 1
        w.clock.tick(300)
        _poll(w)
        assert len(w.client.runs) == 2

    def test_the_synced_commit_is_written_only_after_an_apply_without_errors(self, w):
        _synced(w)
        w.heads["commit"] = NEWER
        _poll(w)
        w.runner.apply_errors = 1
        assert w.agent.run("apply").status == "failed"
        state = w.engine.sync_state(w.root)
        assert state["synced_commit"] == SYNCED and state["last_apply"]["status"] == "failed"
        w.runner.apply_errors = 0
        w.heads["commit"] = NEWEST
        w.runner.plan_commit = NEWEST
        _poll(w)
        assert w.agent.run("apply").status == "ok"
        state = w.engine.sync_state(w.root)
        assert state["synced_commit"] == NEWEST and state["last_apply"] == {
            "at": state["last_apply"]["at"], "status": "ok", "added": 2, "changed": 1, "retired": 0, "errors": 0,
            "auto": False}

    def test_a_full_plan_syncs_the_commit_it_read_and_a_narrowed_one_the_commit_affected_compared_to(self, w):
        w.runner.plan_commit = NEWER
        w.agent.run("dry_run")                                # a person's dry run: every mapping, at the head it read
        assert w.engine.pending(w.root)["commit"] == NEWER
        assert w.agent.run("apply").status == "ok"
        assert w.engine.sync_state(w.root)["synced_commit"] == NEWER


# ------------------------------------------------------------------ automatic apply

def _auto(w):
    w.client.source["auto_apply"] = True
    _synced(w)
    w.heads["commit"] = NEWER


class TestAutomaticApply:
    def test_a_clean_plan_the_schedule_made_is_applied_right_after(self, w):
        _auto(w)
        _poll(w)
        assert w.runner.kinds() == ["affected", "plan", "apply"]
        apply = w.runner.calls[-1]
        assert "--approve-guard" not in apply and "--overwrite-drift" not in apply and "--approve-retire" not in apply
        assert [(r["kind"], r["status"], r["command"]) for r in w.client.runs] == [("dry_run", "ok", None),
                                                                                ("apply", "ok", None)]
        assert "auto_apply.applied" in w.client.codes
        state = w.engine.sync_state(w.root)
        assert state["synced_commit"] == NEWER and state["last_apply"]["auto"] is True

    def test_off_by_default(self, w):
        _synced(w)
        w.heads["commit"] = NEWER
        _poll(w)
        assert "apply" not in w.runner.kinds()

    @pytest.mark.parametrize("extra,code", [
        ({"drift": [{"mapping": "seiten", "field": "titel"}]}, "plan.drift"),
        ({"computed": {"drift": [{"attribute": "zustand"}]}}, "plan.drift"),
        ({"derived": {"drift": [{"relation": "gehört_zu"}], "blocked": []}}, "plan.drift"),
        ({"unresolved": [{"kind": "unknown_claim"}]}, "plan.unresolved"),
        ({"released_mappings": ["alt"]}, "plan.release"),
        ({"items": [{"op": "release", "mapping": "seiten"}]}, "plan.release"),
        ({"retire": {"entries": [], "items": [{"entry": "alte-namen", "op": "close"}], "held": True}}, "plan.retire_held"),
        ({"documents": {"held": [{"mapping": "seiten"}], "stopped": [], "reported": [], "guard": {}}}, "plan.documents_held"),
        ({"documents": {"held": [], "stopped": [{"mapping": "seiten"}], "reported": [], "guard": {}}}, "plan.documents_stopped"),
        ({"documents": {"held": [], "stopped": [], "reported": [{"mapping": "seiten"}], "guard": {}}}, "plan.documents_reported"),
        ({"documents": {"guard": {"seiten": {"tripped": True}}}}, "plan.guard_held"),
        ({"unguarded": {"items": []}}, "plan.guard_held"),
        ({"unreadable": [{"mapping": "seiten"}]}, "plan.unreadable"),
        ({"version": 2}, "plan.unknown_version"),
    ])
    def test_nothing_a_person_must_decide_is_ever_applied_on_its_own(self, w, extra, code):
        _auto(w)
        w.runner.plan_extra = extra
        _poll(w)
        assert "apply" not in w.runner.kinds()
        assert "auto_apply.withheld" in w.client.codes and code in w.client.codes
        assert w.engine.sync_state(w.root)["held_back"] == [code]
        assert [r["kind"] for r in w.client.runs] == ["dry_run"], "the plan is reported and waits as today"

    def test_a_plan_the_deletion_guard_held_goes_to_the_platform_and_is_not_applied(self, w):
        _auto(w)
        w.runner.blocked = ["seiten"]
        _poll(w)
        assert "apply" not in w.runner.kinds()
        assert w.client.runs[-1]["status"] == "held" and w.client.runs[-1]["held"][0]["mapping"] == "seiten"
        assert "plan.not_clean" in w.client.codes

    def test_a_dry_run_a_person_asked_for_is_never_applied_on_its_own(self, w):
        _auto(w)
        w.client.commands = [{"id": "c-1", "command": "run", "args": {"kind": "dry_run"}}]
        w.client.schedule = {"mode": "manual"}
        _poll(w)
        assert w.runner.kinds() == ["plan"] and [r["command"] for r in w.client.runs] == ["c-1"]

    def test_an_interval_schedule_s_clean_plan_is_applied_too(self, w):
        w.client.source["auto_apply"] = True
        w.client.schedule = {"mode": "interval", "every_minutes": 1}
        _poll(w)
        assert w.runner.kinds() == ["plan", "apply"]

    def test_the_switch_is_read_fresh_before_a_plan(self, w):
        _auto(w)
        _poll(w)                                             # applied with the switch on
        w.client.source["auto_apply"] = False                # a person switches it off
        w.heads["commit"] = NEWEST
        w.runner.plan_commit = NEWEST
        _poll(w)
        assert w.runner.kinds()[-1] == "plan", "the next plan waits for a person"

    def test_a_clean_plan_is_one_whose_attention_is_empty(self):
        assert attention({"version": 1, "items": [{"op": "add"}, {"op": "retire"}], "drift": [], "blocked": [],
                          "documents": {"items": [{"op": "ingest"}], "held": [], "stopped": [], "reported": [],
                                        "guard": {"seiten": {"tripped": False}}}}) == []
        assert attention({}) == ["plan.unknown_version"] and attention("kein Plan") == ["plan.unknown_version"]


# ------------------------------------------------------------------ the heartbeat's sync status

class TestTheSyncStatus:
    def test_the_heartbeat_names_the_synced_commit_the_last_apply_and_the_next_check(self, w):
        _auto(w)
        _poll(w)
        _poll(w)
        [entry] = w.client.beats[-1]["sources"]
        assert (entry["workspace"], entry["key"], entry["synced_commit"]) == (WORKSPACE, KEY, NEWER)
        assert entry["last_apply"]["auto"] is True and entry["last_apply"]["added"] == 2
        checked = dt.datetime.fromisoformat(entry["checked_at"])
        assert dt.datetime.fromisoformat(entry["next_check_at"]) == checked + dt.timedelta(seconds=30)
        assert set(entry) == {"workspace", "key", "synced_commit", "synced_at", "checked_at", "next_check_at",
                              "planned_commit", "held_back", "last_apply"}

    def test_it_carries_commits_counts_codes_and_times_only(self, w):
        _auto(w)
        w.runner.plan_extra = {"drift": [{"mapping": "seiten", "brain": "Muster AG", "source": "Beispiel AG"}]}
        _poll(w)
        _poll(w)
        text = json.dumps(w.client.beats[-1])
        for name in ("Muster", "Beispiel", "beispiel-firma", "handbuch/", "titel"):
            assert name not in text, name

    def test_nothing_is_sent_before_a_source_was_ever_checked(self, w):
        w.client.schedule = {"mode": "manual"}
        w.agent.tick()
        assert "sources" not in w.client.beats[-1] or w.client.beats[-1]["sources"] == []


# ------------------------------------------------------------------ the head of a GitHub source

class _Answer:
    def __init__(self, status, text="", etag=None):
        self.status_code, self.text = status, text
        self.headers = {"ETag": etag} if etag else {}


class TestTheHead:
    SETTINGS = {"repository": "beispiel-firma/handbuch", "ref": "main"}

    def test_one_request_with_the_token_and_the_etag(self):
        seen = {}

        def get(url, **kw):
            seen.update(kw, url=url)
            return _Answer(200, NEWER + "\n", 'W/"abc"')

        head = head_github(self.SETTINGS, "lese-token", etag='W/"old"', get=get)
        assert head == Head(NEWER, 'W/"abc"')
        assert seen["url"].endswith("/repos/beispiel-firma/handbuch/commits/main")
        assert seen["headers"]["Authorization"] == "Bearer lese-token"
        assert seen["headers"]["If-None-Match"] == 'W/"old"' and seen["headers"]["Accept"] == "application/vnd.github.sha"
        assert seen["allow_redirects"] is False

    def test_unchanged_is_a_304_and_names_no_commit(self):
        assert head_github(self.SETTINGS, None, etag='W/"abc"', get=lambda url, **kw: _Answer(304)) == Head(None, 'W/"abc"')

    @pytest.mark.parametrize("answer,code", [(_Answer(404), "fetch.http_404"), (_Answer(200, "<html>"), "fetch.not_a_commit")])
    def test_what_it_refuses(self, answer, code):
        with pytest.raises(FetchError, match=code):
            head_github(self.SETTINGS, None, get=lambda url, **kw: answer)
        with pytest.raises(FetchError, match="fetch.bad_repository"):
            head_github({"repository": "../x", "ref": "main"}, None, get=lambda url, **kw: answer)


def test_the_engine_reads_affected_s_json_line(tmp_path):
    seen = {}

    def run(argv, **kw):
        seen["args"], seen["env"] = argv[3:], kw["env"]
        return SimpleNamespace(returncode=0, stderr="", stdout='INFO log\n{"head": "%s", "mappings": null}\n' % NEWER)

    engine = RunnerEngine(tmp_path, {}, run=run)
    assert engine.affected(tmp_path, SYNCED, "beispiel-firma/handbuch", "main", {"BRAIN_GITHUB_TOKEN": "t"}) == \
        {"head": NEWER, "mappings": None}
    assert seen["env"]["BRAIN_GITHUB_TOKEN"] == "t" and "t" not in seen["args"]
    engine._run = lambda argv, **kw: SimpleNamespace(returncode=4, stdout="", stderr="")
    assert engine.affected(tmp_path, SYNCED, "beispiel-firma/handbuch", "main") == "runner.affected_exit_4"



# ------------------------------------------------------------------ with the real brain-runner (0.3.2 or later)

class TestTheRealRunner:
    """``affected`` over GitHub's API as the image's brain-runner answers it, against an in-process GitHub."""

    @pytest.fixture
    def repo(self, tmp_path, monkeypatch):
        affected = pytest.importorskip("brain_runner.affected")
        if not hasattr(affected, "github_changes"):
            pytest.skip("brain-runner before 0.3.2: no affected --source github")
        import sync_link_world as LW
        from fake_github_rest import FakeGitHub
        from fake_knovas_link import stop

        github = FakeGitHub()
        first = github.commit(dict(LW.POLICIES))
        served = github.serve()
        monkeypatch.setenv("BRAIN_GITHUB_API", served.url)
        package = tmp_path / "package"
        LW.write_config(package)
        engine = RunnerEngine(tmp_path / "sources", LW.runner_env())
        yield SimpleNamespace(github=github, first=first, package=package, engine=engine, LW=LW)
        stop(served)

    def _affected(self, r, since):
        return r.engine.affected(r.package, since, "example-org/handbook", "main", {"BRAIN_GITHUB_TOKEN": "read-token"})

    def test_a_commit_to_a_file_a_mapping_reads_names_that_mapping(self, repo):
        head = repo.github.commit({"policies/travel.md": repo.LW.TRAVEL.replace(b"cheapest", b"greenest")})
        assert self._affected(repo, repo.first) == {"head": head, "mappings": [repo.LW.MAPPING]}

    def test_a_commit_no_mapping_reads_names_none_and_nothing_new_is_one_request(self, repo):
        head = repo.github.commit({"README.md": b"# Handbook\n"})
        assert self._affected(repo, repo.first) == {"head": head, "mappings": []}
        before = len(repo.github.calls)
        assert self._affected(repo, head) == {"head": head, "mappings": []}
        assert [path for _, path in repo.github.calls[before:]] == ["/repos/example-org/handbook/commits/main"]
