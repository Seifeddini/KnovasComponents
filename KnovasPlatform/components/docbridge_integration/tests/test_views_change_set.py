"""Ansichten, Step 7: fix and change from the Platform, as ONE pull request on KnowledgeBase.

Classification from the process definitions (DEC-0024), status rollback and the decision record,
permissions through node_grants, conflict detection and rebase, the PR title and body, the
TRACE's fix actions and locks, the picker's suggestions (DEC-0025), the gaps worklist, and the
routes with the change-set store and the audit log. The repository is in memory (no network).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import subprocess
from pathlib import Path

import pytest
from flask import Flask

from graph_views import change_requests as cr, change_set as cs, fixes, plan_file, service as views
from graph_views.settings import ViewSettings
from views_fixture import FakeViewsClient, client_zero_graph
from web_interface.views_routes import create_views_blueprint

TODAY = dt.date(2026, 10, 28)
PROC = "PROC-feature-lifecycle"
KEY = "FEAT-2026-10-05-graph-native-brain"
PLAN_PATH = "docs/ModernDocs/plans/2026-10-05-graph-native-brain.md"
ROADMAP = "FEAT-2026-10-05-graph-native-brain-roadmap"           # in suggestions.json: Goal candidates
ROADMAP_PATH = "docs/ModernDocs/plans/2026-10-05-graph-native-brain-roadmap.md"

PLAN_MD = """---
doc_type: feature_plan
status: approved
---

# Feature: Graph-native company brain

| Field | Value |
|-------|-------|
| **Author** | Platform |
| **Date** | 2026-10-05 |
| **Status** | approved |
| **Affected L2** | L2-BRAIN-GRAPH-STORE, L2-KNOWLEDGE-GRAPH |

## Problem

The brain stores its graph in PostgreSQL tables; walks are slow.

## Component contract deltas

### L2-BRAIN-GRAPH-STORE

- RESP-01: owns the graph.

## Golden Invariants

| GI ID | Impact |
|-------|--------|
| GI-GRAPH-00 | preserved |

## Alloy

| Model path | Action | Checks |
|------------|--------|--------|
| `data_plane/rate_limits.als` | extend | |

## Risks

| ID | Risk | Mitigation |
|----|------|------------|
| RISK-01 | Migration loses edges | dual write |

## References

- docs/ModernDocs/plans/2026-10-05-graph-native-brain-roadmap.md
"""

PLAN_AI = f"""schema_version: 1
feature:
  id: {KEY}
  title: "Graph-native company brain"
  status: approved
  author: Platform
hierarchy:
  affected_L2:
    - L2-BRAIN-GRAPH-STORE
    - L2-KNOWLEDGE-GRAPH
problem:
  statement: "The brain stores its graph in PostgreSQL tables; walks are slow."
"""

ROADMAP_MD = """---
status: draft
---

# Feature Plan: Graph-native brain roadmap

| Field | Value |
|-------|-------|
| **Date** | 2026-10-05 |
"""

LINKS = f"""version: 1
nodes: []
edges:
  - {{from: {KEY}, relation: serves, to: CAP-11}}
  - {{from: {KEY}, relation: motivated_by, to: GOAL-2026-Q4-brain-on-graph-store}}
  - {{from: FEAT-other, relation: serves, to: CAP-01}}
"""

DECS = "docs/ModernDocs/client-zero/ontology/decisions"


def _sha(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class FakeRepo:
    """Commits as dicts of files; one branch 'master'; records writes and PRs."""

    def __init__(self, files):
        self.commits = {_sha("c1"): dict(files)}
        self.head = _sha("c1")
        self.created, self.puts, self.prs, self.pulls = [], [], [], {}

    def advance(self, path, text):
        new = _sha(f"c{len(self.commits) + 1}")
        self.commits[new] = {**self.commits[self.head], path: text}
        self.head = new

    def branch_sha(self, branch):
        return self.head

    def get_file(self, path, ref):
        files = self.commits.get(ref)       # refs are commits only, as on GitHub
        if files is None:
            return None
        text = files.get(path)
        return None if text is None else (text, _sha(text))

    def list_dir(self, path, ref):
        return sorted(p.rsplit("/", 1)[1] for p in self.commits[ref] if p.startswith(path + "/"))

    def create_branch(self, name, sha):
        self.created.append((name, sha))

    def put_file(self, path, text, message, branch, sha=None):
        self.puts.append({"path": path, "text": text, "branch": branch, "sha": sha})

    def create_pull(self, title, body, head, base):
        n = 300 + len(self.prs)
        self.prs.append({"title": title, "body": body, "head": head, "base": base})
        self.pulls[n] = {"number": n, "url": f"https://github.com/x/pull/{n}", "state": "open", "merged": False}
        return {"number": n, "url": f"https://github.com/x/pull/{n}"}

    def get_pull(self, number):
        return self.pulls.get(number)


def _repo():
    return FakeRepo({PLAN_PATH: PLAN_MD, PLAN_PATH[:-3] + ".ai.yaml": PLAN_AI, ROADMAP_PATH: ROADMAP_MD,
                     plan_file.PLAN_LINKS_PATH: LINKS,
                     f"{DECS}/DEC-0016-definitions-on-the-tenant.md": "x",
                     f"{DECS}/DEC-0017-definition-validation-rules.md": "y"})


class _NoWait:
    def acquire(self):
        return None


@pytest.fixture
def graph():
    g = client_zero_graph()
    proc = g.by_key["PROC-feature-lifecycle"]
    plan = g.node("FeaturePlan", "Feature: Graph-native company brain", key=KEY, status="approved",
                  owner="ROLE-ENGINE-OWNER", source=PLAN_PATH)
    g.edge(plan, "instance_of", proc)
    g.edge(plan, "in_state", g.by_key["STEP-feature-lifecycle-approved"])
    g.edge(plan, "affects", g.by_key["L2-KNOWLEDGE-GRAPH"])
    store = g.node("Component", "L2-BRAIN-GRAPH-STORE — store", key="L2-BRAIN-GRAPH-STORE")
    g.edge(plan, "affects", store)
    cap = g.node("Capability", "CAP-11 — a brain that improves itself", key="CAP-11")
    g.edge(plan, "serves", cap)
    g.node("Capability", "CAP-02 — search with citations", key="CAP-02")
    goal = g.node("Goal", "GOAL-2026-Q4-brain-on-graph-store — the brain on a graph store", key="GOAL-2026-Q4-brain-on-graph-store")
    g.edge(plan, "motivated_by", goal)
    dec = g.node("Decision", "DEC-0099 — keep the store", key="DEC-0099", status="decided")
    g.edge(dec, "affects", plan)
    roadmap = g.node("FeaturePlan", "Feature Plan: Graph-native brain roadmap", key=ROADMAP, status="draft",
                     source=ROADMAP_PATH)
    g.edge(roadmap, "instance_of", proc)
    g.edge(roadmap, "in_state", g.by_key["STEP-feature-lifecycle-brief"])
    return g


def _ctx(graph):
    client = FakeViewsClient(graph)
    svc = views.ViewService(lambda: client, ViewSettings(), bucket=_NoWait(), markers=lambda: set())
    ctx = svc.context()
    return ctx, svc.definitions(ctx), svc


class Admin:
    id = "00000000-0000-0000-0000-00000000a0a0"
    roles = frozenset({"admin"})


class Nobody:
    id = "00000000-0000-0000-0000-00000000b0b0"
    roles = frozenset()


class Grants:
    def __init__(self, owner=None, editors=()):
        self.owner, self.editors = owner, set(editors)

    def for_node(self, node):
        return {"owner": self.owner, "editors": list(self.editors)}

    def may_write(self, node, user):
        return str(user.id) == self.owner or str(user.id) in self.editors


def _op(graph, ctx, d, user=Admin(), grants=Grants(), **kw):
    kw.setdefault("process", PROC)
    kw.setdefault("node", graph.by_key[KEY])
    return cs.new_op(ctx, d, user, grants, **kw)


def _set(repo, ops):
    return {"id": "s", "base": {"branch": "master", "commit": repo.head}, "ops": ops, "reasons": {}}


# -- the plan file (unchanged readers and writers) ---------------------------------------------


class TestPlanFile:
    def test_reads_every_template_field(self):
        f = plan_file.read(PLAN_MD, KEY, LINKS)
        assert (f.title, f.status, f.owner) == ("Graph-native company brain", "approved", "Platform")
        assert f.affected_components == ["L2-BRAIN-GRAPH-STORE", "L2-KNOWLEDGE-GRAPH"]
        assert f.capability == ["CAP-11"] and f.goal == ["GOAL-2026-Q4-brain-on-graph-store"]

    def test_absent_fields_are_inserted_where_the_template_puts_them(self):
        md = "# Company Brain — epics\n\n| Field | Value |\n|-------|-------|\n| **Date** | 2026-08-02 |\n\nBody.\n"
        e = plan_file.apply(md, {"owner": "Siran", "description": "Why."})
        assert "| **Author** | Siran |" in e.text and "## Problem\n\nWhy." in e.text

    def test_an_empty_field_absent_from_the_file_is_not_a_change(self):
        current = {"description": None, "requirements": None, "sources": None, "capability": ["CAP-11"]}
        proposed = {"description": "", "requirements": [], "sources": "  ", "capability": ["CAP-11", "CAP-02"]}
        assert cr.changed_fields(current, proposed) == ["capability"]

    def test_form_edits_become_single_operations(self):
        current = {"owner": "Platform", "capability": ["CAP-11"], "goal": ["GOAL-2026-Q4-x"]}
        ops = cr.field_ops(current, {"owner": "PERS-siran", "capability": ["CAP-02"],
                                     "goal": ["GOAL-2026-Q4-x", "P-02.H01"]})
        assert {"kind": "field", "field": "owner", "value": "PERS-siran"} in ops
        assert {"kind": "add", "edit_id": "FeaturePlan serves Capability", "target": "CAP-02"} in ops
        assert any(o["kind"] == "remove" and o["target"] == "CAP-11" and o["reason"] for o in ops)
        assert {"kind": "add", "edit_id": "FeaturePlan justified_by Hypothesis", "target": "P-02.H01"} in ops


def test_local_repo_never_passes_a_revision_as_an_option(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    repo = cr.LocalRepo(str(tmp_path))
    with pytest.raises(cr.ChangeError, match="Revision"):
        repo.get_file("x.md", "--output=" + str(tmp_path / "pwned"))
    assert not (tmp_path / "pwned").exists()
    assert repo.get_file("x.md", "deadbeef") is None


@pytest.mark.parametrize("source", ["docs/ModernDocs/plans/../../secrets.md", "x/docs/ModernDocs/plans/a.md",
                                    "-docs/ModernDocs/plans/a.md", "docs\\ModernDocs\\plans\\a.md"])
def test_a_plan_path_outside_the_plans_folder_is_refused(source, monkeypatch):
    class Ctx:
        class index:
            nodes = {"n": {"name": "N"}}
        state = reader = None
    monkeypatch.setattr(cr, "read_facts", lambda state, reader, ids: {"n": None})
    monkeypatch.setattr(cr, "fact_values", lambda facts: {"source": source})
    with pytest.raises(cr.ChangeError, match="Plan-Datei"):
        cr.plan_of(Ctx, "n")


# -- classes and rollback (DEC-0024) ------------------------------------------------------------


class TestClasses:
    def test_classes_come_from_the_definition(self, graph):
        _, d, _ = _ctx(graph)
        assert cs.classify(d, PROC, "FeaturePlan serves Capability") == "substantive"
        assert cs.classify(d, PROC, "FeaturePlan.owner") == "minor"
        assert cs.classify(d, PROC, "name") == "minor"
        with pytest.raises(cr.ChangeError, match="keine Änderungsklasse"):
            cs.classify(d, PROC, "PreMortem examines FeaturePlan")

    def test_rollback_from_approved_or_later_to_the_draft_status(self, graph):
        _, d, _ = _ctx(graph)
        assert cs.rollback_of(d, PROC, "approved") == {"from_step": "approved", "to_step": "brief", "status": "draft"}
        assert cs.rollback_of(d, PROC, "implementing")["status"] == "draft"
        assert cs.rollback_of(d, PROC, "brief") is None
        assert cs.rollback_of(d, PROC, "released") is None             # final steps stay

    def test_rules_again_are_the_exit_criteria_back_to_the_rework_step(self, graph):
        _, d, _ = _ctx(graph)
        rules = cs.rules_again(d, PROC, {"from_step": "approved", "to_step": "brief", "status": "draft"})
        brief = next(s for s in d.process(PROC)["steps"] if s["id"] == "brief")
        assert rules[:len(brief["exit_criteria"])] == brief["exit_criteria"]


# -- operations ------------------------------------------------------------------------------------


class TestOperations:
    def test_a_fix_is_routed_classified_and_permitted(self, graph):
        ctx, d, _ = _ctx(graph)
        op = _op(graph, ctx, d, kind="add", edit_id="FeaturePlan serves Capability", target="CAP-02")
        assert (op["class"], op["instance_key"], op["plan_path"]) == ("substantive", KEY, PLAN_PATH)

    def test_a_locked_relation_names_where_to_fix_it(self, graph):
        ctx, d, _ = _ctx(graph)
        with pytest.raises(cr.ChangeError, match="from git"):
            _op(graph, ctx, d, kind="add", edit_id="Commit implements FeaturePlan", target="COMMIT-0123456789ab")

    def test_remove_and_replace_need_a_reason(self, graph):
        ctx, d, _ = _ctx(graph)
        with pytest.raises(cr.ChangeError, match="Begründung"):
            _op(graph, ctx, d, kind="remove", edit_id="FeaturePlan serves Capability", target="CAP-11")
        op = _op(graph, ctx, d, kind="replace", edit_id="FeaturePlan serves Capability", target="CAP-11",
                 new_target="CAP-02", reason="CAP-11 is the learning loop, not this")
        assert op["new_target"] == "CAP-02" and op["reason"]

    def test_a_target_of_the_wrong_type_is_refused(self, graph):
        ctx, d, _ = _ctx(graph)
        with pytest.raises(cr.ChangeError, match="Capability"):
            _op(graph, ctx, d, kind="add", edit_id="FeaturePlan serves Capability", target="GOAL-2026-Q4-x")

    def test_substantive_needs_the_owner_minor_any_writer(self, graph):
        ctx, d, _ = _ctx(graph)
        editor = Nobody()
        grants = Grants(owner="someone-else", editors={editor.id})
        _op(graph, ctx, d, user=editor, grants=grants, kind="field", field="owner", value="PERS-siran")
        with pytest.raises(cr.ChangeForbidden, match="Verantwortlichen"):
            _op(graph, ctx, d, user=editor, grants=grants, kind="add", edit_id="FeaturePlan serves Capability", target="CAP-02")
        with pytest.raises(cr.ChangeForbidden):
            _op(graph, ctx, d, user=Nobody(), grants=Grants(), kind="field", field="owner", value="x")
        owner = Nobody()
        _op(graph, ctx, d, user=owner, grants=Grants(owner=owner.id), kind="add",
            edit_id="FeaturePlan serves Capability", target="CAP-02")

    def test_the_process_is_the_instances_own_not_the_requests(self, graph):
        """The class decides the permission, and the class depends on the process: a request naming
        another process (where the same id might be minor) is refused."""
        ctx, d, _ = _ctx(graph)
        with pytest.raises(cr.ChangeError, match="kein Instanztyp"):
            _op(graph, ctx, d, process="PROC-decision", kind="add", edit_id="FeaturePlan serves Capability", target="CAP-02")
        stray = graph.node("FeaturePlan", "A plan of no process", key="FEAT-2026-01-01-stray", source="docs/ModernDocs/plans/x.md")
        ctx, d, _ = _ctx(graph)
        with pytest.raises(cr.ChangeError, match="nicht zu"):
            _op(graph, ctx, d, node=stray, kind="field", field="owner", value="x")

    def test_a_decision_row_of_a_plan_is_not_a_plan(self, graph):
        """Plan-table decisions carry the plan file as their source; they must not reach the plan writers."""
        dec = graph.node("Decision", "D-10: keep it", key=f"{KEY}/D-10", source=PLAN_PATH, status="decided")
        graph.edge(dec, "instance_of", graph.by_key["PROC-feature-lifecycle"])
        ctx, d, _ = _ctx(graph)
        with pytest.raises(cr.ChangeError, match="kein Instanztyp"):
            _op(graph, ctx, d, node=dec, kind="field", field="owner", value="x")

    @pytest.mark.parametrize("fld", ["sources", "target_date"])
    def test_fields_without_a_route_are_refused(self, graph, fld):
        ctx, d, _ = _ctx(graph)
        with pytest.raises(cr.ChangeError):
            _op(graph, ctx, d, kind="field", field=fld, value="x")


# -- review: files, rollback, decision record, impact -----------------------------------------------


class TestReview:
    def test_a_minor_change_touches_its_files_only(self, graph):
        ctx, d, _ = _ctx(graph)
        repo = _repo()
        op = _op(graph, ctx, d, kind="field", field="owner", value="PERS-siran")
        r = cs.build(ctx, d, repo, _set(repo, [op]), actor="Siran", today=TODAY)
        assert [f["path"] for f in r["files"]] == [PLAN_PATH[:-3] + ".ai.yaml", PLAN_PATH]
        assert r["instances"][0]["rollback"] is None and r["ready"] and not r["conflicts"]

    def test_substantive_on_an_approved_plan_rolls_back_and_adds_a_decision(self, graph):
        ctx, d, _ = _ctx(graph)
        repo = _repo()
        ops = [_op(graph, ctx, d, kind="add", edit_id="FeaturePlan serves Capability", target="CAP-02",
                   suggestion={"rule": "realised-by-affected", "score": 3, "reason": "realised by L2-KNOWLEDGE-GRAPH"}),
               _op(graph, ctx, d, kind="remove", edit_id="AlloyCheck verifies FeaturePlan",
                   target="ALLOY-data_plane/rate_limits.als#check_01", reason="model belongs to rate limits")]
        r = cs.build(ctx, d, repo, _set(repo, ops), actor="Siran", today=TODAY)
        inst = r["instances"][0]
        assert inst["class"] == "substantive" and inst["rollback"]["status"] == "draft"
        assert r["missing_reasons"] == [KEY] and not r["ready"]          # a decision needs the reason
        r = cs.build(ctx, d, repo, _set(repo, ops), actor="Siran", today=TODAY,
                     reasons={KEY: "Search scoping depends on the graph store as well."})
        paths = [f["path"] for f in r["files"]]
        assert paths == sorted(paths) and r["ready"]
        dec = next(f for f in r["files"] if f["path"].startswith(DECS))
        assert dec["new"] and dec["path"].endswith("DEC-0018-2026-10-05-graph-native-brain-change.md")
        text = r["_texts"][dec["path"]]
        assert "status: proposed" in text and "Search scoping depends" in text and "`draft`" in text
        plan = next(f for f in r["files"] if f["path"] == PLAN_PATH)
        assert "+status: draft" in plan["diff"] and "-| `data_plane/rate_limits.als`" in plan["diff"]
        assert f"rollback:{KEY}" in plan["ops"] and ops[1]["id"] in plan["ops"]
        links = next(f for f in r["files"] if f["path"] == plan_file.PLAN_LINKS_PATH)
        assert links["ops"] == [ops[0]["id"]]
        assert inst["impact"]["components"] and isinstance(inst["rules"], list)

    def test_substantive_before_the_rework_point_needs_no_decision(self, graph):
        ctx, d, _ = _ctx(graph)
        repo = _repo()
        op = _op(graph, ctx, d, node=graph.by_key[ROADMAP], kind="add", edit_id="FeaturePlan motivated_by Goal",
                 target="GOAL-2026-Q4-brain-on-graph-store")
        r = cs.build(ctx, d, repo, _set(repo, [op]), actor="Siran", today=TODAY)
        assert r["instances"][0]["rollback"] is None and r["ready"]
        assert not any(f["path"].startswith(DECS) for f in r["files"])

    def test_an_operation_that_no_longer_applies_is_named(self, graph):
        ctx, d, _ = _ctx(graph)
        repo = _repo()
        op = _op(graph, ctx, d, kind="add", edit_id="FeaturePlan serves Capability", target="CAP-02")
        repo.advance(plan_file.PLAN_LINKS_PATH, LINKS + f"  - {{from: {KEY}, relation: serves, to: CAP-02}}\n")
        r = cs.build(ctx, d, repo, cs.rebase(repo, _set(repo, [op])), actor="x", today=TODAY,
                     reasons={KEY: "x" * 30})
        assert r["ops"][0]["ok"] is False and "bereits" in r["ops"][0]["error"] and not r["ready"]


class TestConflicts:
    def test_a_moved_owning_file_is_a_conflict_until_rebased(self, graph):
        ctx, d, _ = _ctx(graph)
        repo = _repo()
        change_set = _set(repo, [_op(graph, ctx, d, kind="field", field="owner", value="PERS-siran")])
        repo.advance(PLAN_PATH, PLAN_MD.replace("walks are slow", "walks are slow and costly"))
        r = cs.build(ctx, d, repo, change_set, actor="x", today=TODAY)
        assert r["behind"] and [c["path"] for c in r["conflicts"]] == [PLAN_PATH] and not r["ready"]
        assert "+The brain stores its graph in PostgreSQL tables; walks are slow and costly." in r["conflicts"][0]["diff"]
        with pytest.raises(cr.ChangeConflict):
            cs.submit(r, repo, actor="x", reasons={})
        r = cs.build(ctx, d, repo, cs.rebase(repo, change_set), actor="x", today=TODAY)
        assert not r["conflicts"] and r["ready"]
        assert "walks are slow and costly" in r["_texts"][PLAN_PATH] and "| **Author** | PERS-siran |" in r["_texts"][PLAN_PATH]

    def test_a_file_the_set_does_not_touch_is_no_conflict(self, graph):
        ctx, d, _ = _ctx(graph)
        repo = _repo()
        change_set = _set(repo, [_op(graph, ctx, d, kind="field", field="owner", value="PERS-siran")])
        repo.advance(plan_file.PLAN_LINKS_PATH, LINKS + "  - {from: FEAT-z, relation: serves, to: CAP-01}\n")
        r = cs.build(ctx, d, repo, change_set, actor="x", today=TODAY)
        assert r["behind"] and not r["conflicts"] and r["ready"]

    @pytest.mark.parametrize("bad", ["--output=/tmp/x", "HEAD", "../x", 7])
    def test_a_base_that_is_not_an_object_id_is_refused(self, graph, bad):
        ctx, d, _ = _ctx(graph)
        repo = _repo()
        with pytest.raises(cr.ChangeError, match="Basisstand"):
            cs.build(ctx, d, repo, {"base": {"branch": "master", "commit": bad}, "ops": []}, actor="x")


# -- the pull request ----------------------------------------------------------------------------------


class TestPullRequest:
    def _review(self, graph):
        ctx, d, _ = _ctx(graph)
        repo = _repo()
        ops = [_op(graph, ctx, d, kind="add", edit_id="FeaturePlan serves Capability", target="CAP-02",
                   suggestion={"rule": "realised-by-affected", "score": 3, "reason": "realised by L2-KNOWLEDGE-GRAPH"}),
               _op(graph, ctx, d, node=graph.by_key[ROADMAP], kind="add", edit_id="FeaturePlan motivated_by Goal",
                   target="GOAL-2026-Q4-brain-on-graph-store")]
        reasons = {KEY: "Search scoping depends on the graph store as well."}
        return repo, cs.build(ctx, d, repo, _set(repo, ops), actor="Siran", today=TODAY, reasons=reasons), reasons

    def test_title_and_body_come_from_the_change_set(self, graph):
        _, r, reasons = self._review(graph)
        assert cs.pr_title(r) == f"Platform change set: 2 changes to {KEY}, {ROADMAP} (substantive)"
        body = cs.pr_body(r, actor="Siran", reasons=reasons)
        assert f"### {KEY}" in body and f"### {ROADMAP}" in body
        assert "**substantive** FEAT-2026-10-05-graph-native-brain: Fähigkeit + CAP-02" in body
        assert "suggestion realised-by-affected (score 3): realised by L2-KNOWLEDGE-GRAPH" in body
        assert "Status back to `draft`" in body and "DEC-0018" in body and "> Search scoping" in body
        assert "never writes the brain" in body

    def test_submit_branches_at_the_base_and_opens_one_pr(self, graph):
        repo, r, reasons = self._review(graph)
        pr = cs.submit(r, repo, actor="Siran Seifeddini", reasons=reasons, now=dt.datetime(2026, 10, 28, 9, 0, 0))
        assert repo.created == [("platform/changes-siran-seifeddini-20261028090000", repo.head)]
        assert {p["path"] for p in repo.puts} == {f["path"] for f in r["files"]}
        put = next(p for p in repo.puts if p["path"] == PLAN_PATH)
        assert put["sha"] == _sha(PLAN_MD)                          # GitHub refuses a write over a moved file
        assert next(p for p in repo.puts if p["path"].startswith(DECS))["sha"] is None
        assert len(repo.prs) == 1 and pr["number"] == 300 and repo.prs[0]["base"] == "master"

    def test_a_local_checkout_previews_but_cannot_submit(self, graph):
        _, r, reasons = self._review(graph)
        with pytest.raises(cr.ChangeNotConfigured):
            cs.submit(r, cr.LocalRepo("."), actor="x", reasons=reasons)


# -- TRACE fixes, picker, gaps -------------------------------------------------------------------------


class TestFixes:
    def test_trace_artefacts_say_what_can_be_changed_here(self, graph):
        from graph_views import process_views as pv

        ctx, d, _ = _ctx(graph)
        body = fixes.annotate_trace(d, PROC, pv.trace(ctx, d, PROC, graph.by_key[KEY], today=TODAY), [])
        brief = {a["type"]: a["edit"] for a in next(l for l in body["lanes"] if l["id"] == "brief")["artefacts"]}
        assert brief["Capability"]["routable"] and brief["Capability"]["can_add"]
        assert brief["Record"]["routable"] is False and brief["Record"]["reason"] == "derived by loader"
        impl = {a["type"]: a["edit"] for a in next(l for l in body["lanes"] if l["id"] == "implementing")["artefacts"]}
        assert impl["Commit"]["editable"] is False and "commit message" in impl["Commit"]["fix_at"]
        assert impl["SourceFile"]["indirect"] is True
        ctxs = {(a["type"], a["path"]): a["edit"] for a in body["context"]}
        assert ctxs[("Risk", "←affects– Risk")]["can_add"] is False         # a new risk needs text

    def test_the_picker_puts_suggestions_with_their_reason_on_top(self, graph):
        ctx, d, svc = _ctx(graph)
        brief = next(s for s in d.process(PROC)["steps"] if s["id"] == "brief")
        pos = [a["type"] for a in brief["requires"] + brief["produces"]].index("Goal")
        got = fixes.picker(ctx, d, svc.schemas, process=PROC, node=graph.by_key[ROADMAP], where="brief", position=pos,
                           q="brain")
        top = got["suggestions"][0]
        assert top["target"] == "GOAL-2026-Q4-brain-on-graph-store" and top["reason"].startswith("goal of the current quarter")
        assert top["id"] == graph.by_key["GOAL-2026-Q4-brain-on-graph-store"] and top["name"].startswith("GOAL-2026-Q4")
        assert [r["key"] for r in got["results"]] == ["GOAL-2026-Q4-brain-on-graph-store"]
        assert got["edit"]["routable"] and got["suggestions_as_of"]

    def test_gaps_list_missing_artefacts_with_suggestions_and_flagged_rules(self, graph):
        ctx, d, _ = _ctx(graph)
        g = fixes.gaps(ctx, d, process=PROC)
        roadmap = [r for r in g["rows"] if r["key"] == ROADMAP]
        goal = next(r for r in roadmap if r["type"] == "Goal")
        assert goal["kind"] == "missing" and goal["candidates"][0]["target"] == "GOAL-2026-Q4-brain-on-graph-store"
        assert goal["edit"]["routable"] and goal["where"] == "brief" and goal["position"] is not None
        record = next(r for r in roadmap if r["type"] == "Record")
        assert record["edit"]["routable"] is False
        rule = next(r for r in g["rows"] if r["key"] == KEY and r["kind"] == "rule")
        assert rule["rule"] == "change-class" and "approved" in rule["why"] and rule["source"] == "kb-report"
        only = fixes.gaps(ctx, d, process=PROC, type_name="Goal")
        assert {r["type"] for r in only["rows"]} == {"Goal"} and "Goal" in g["filters"]["types"]

    def test_a_merged_link_counts_as_applied_once_the_tenant_shows_it(self, graph):
        ctx, d, _ = _ctx(graph)
        op = _op(graph, ctx, d, kind="add", edit_id="FeaturePlan serves Capability", target="CAP-02")
        assert fixes.op_applied(ctx, op) is False
        graph.edge(graph.by_key[KEY], "serves", graph.by_key["CAP-02"])
        ctx, _, _ = _ctx(graph)
        assert fixes.op_applied(ctx, op) is True


# -- routes, store, audit (database) ----------------------------------------------------------------

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable  # noqa: E402


@pytest.mark.skipif(not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")
class TestRoutes:
    def _app(self, graph, platform_db, repo):
        from identity.change_sets import ChangeSetStore
        from identity.node_grants import NodeGrantStore

        client = FakeViewsClient(graph)
        svc = views.ViewService(lambda: client, ViewSettings(), bucket=_NoWait(), markers=lambda: set())
        gate = type("G", (), {"user": None, "current_user": lambda self: self.user, "connection": lambda self: platform_db})()
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"))
        app.register_blueprint(create_views_blueprint(
            gate, svc, graph_mode=lambda: True, change_repo=lambda: repo,
            change_store=lambda: ChangeSetStore(platform_db), node_grants=lambda: NodeGrantStore(platform_db)))
        return app.test_client(), gate

    def _users(self, identity_repo, platform_db, graph):
        from identity.node_grants import NodeGrantStore

        mk = lambda n: identity_repo.get(identity_repo.create(email=f"{n}@kanzlei.ch", display_name=n.title(),  # noqa: E731
                                                              password="korrektes-pferd-batterie").id)
        owner, editor = mk("olga"), mk("edda")
        node = graph.by_key[KEY]
        NodeGrantStore(platform_db).set_owner(node, owner.id)
        NodeGrantStore(platform_db).grant_editor(node, editor.id, granted_by=owner.id)
        return owner, editor

    def test_fix_review_submit_audit_and_pending(self, graph, platform_db, identity_repo):
        from identity import audit

        owner, editor = self._users(identity_repo, platform_db, graph)
        repo = _repo()
        client, gate = self._app(graph, platform_db, repo)
        node = graph.by_key[KEY]
        _, d, _ = _ctx(graph)
        brief = next(s for s in d.process(PROC)["steps"] if s["id"] == "brief")
        cap = [a["type"] for a in brief["requires"] + brief["produces"]].index("Capability")

        gate.user = editor                     # minor: yes; substantive: no
        res = client.post("/api/views/changeset/ops", json={"process": PROC, "node": node, "kind": "add",
                                                            "where": "brief", "position": cap, "target": "CAP-02"})
        assert res.status_code == 403
        res = client.post("/api/views/changeset/fields", json={"process": PROC, "node": node,
                                                               "values": {"owner": "PERS-siran"}})
        assert res.status_code == 201 and res.get_json()["count"] == 1

        gate.user = owner
        trace = client.get(f"/api/views/trace?process={PROC}&instance={node}").get_json()
        assert trace["changeable"] and all("edit" in a for l in trace["lanes"] for a in l["artefacts"])
        res = client.post("/api/views/changeset/ops", json={"ops": [
            {"process": PROC, "node": node, "kind": "add", "where": "brief", "position": cap, "target": "CAP-02"},
            {"process": PROC, "node": node, "kind": "add", "edit_id": "Commit implements FeaturePlan", "target": "COMMIT-0123456789ab"},
        ]}).get_json()
        assert len(res["added"]) == 1 and "from git" in res["refused"][0]["error"] and res["count"] == 1
        review = client.post("/api/views/changeset/review", json={"reasons": {KEY: "Search scoping needs the store."}}).get_json()
        assert review["ready"] and review["instances"][0]["rollback"]["status"] == "draft"
        assert any(f["path"].startswith(DECS) for f in review["files"]) and "_texts" not in review

        res = client.post("/api/views/changeset/submit", json={})
        assert res.status_code == 201 and res.get_json()["pr"]["number"] == 300
        entry = audit.recent(platform_db, action="plan.changeset.submitted")[0]
        assert entry["actor_email"] == "olga@kanzlei.ch" and entry["detail"]["pr_url"].endswith("/300")
        assert entry["detail"]["classes"] == ["substantive"] and PLAN_PATH in entry["detail"]["files"]
        assert client.get("/api/views/changeset").get_json()["count"] == 0          # a fresh set starts empty

        trace = client.get(f"/api/views/trace?process={PROC}&instance={node}").get_json()
        assert [p["pr_number"] for p in trace["pending"]] == [300]
        assert trace["pending"][0]["ops"][0]["edit_id"] == "FeaturePlan serves Capability"
        repo.pulls[300].update(state="closed", merged=True)
        assert client.get(f"/api/views/changeset/pending?node={node}").get_json()["pending"][0]["state"] == "merged"

        gate.user = editor                     # the editor's set is still their own
        assert client.get("/api/views/changeset").get_json()["count"] == 1

    def test_a_conflict_is_shown_and_rebase_clears_it(self, graph, platform_db, identity_repo):
        owner, _ = self._users(identity_repo, platform_db, graph)
        repo = _repo()
        client, gate = self._app(graph, platform_db, repo)
        gate.user = owner
        node = graph.by_key[KEY]
        client.post("/api/views/changeset/fields", json={"process": PROC, "node": node, "values": {"owner": "PERS-siran"}})
        repo.advance(PLAN_PATH, PLAN_MD.replace("Platform", "Plattform-Team", 1))
        review = client.post("/api/views/changeset/review", json={}).get_json()
        assert review["conflicts"] and "+| **Author** | Plattform-Team |" in review["conflicts"][0]["diff"]
        assert client.post("/api/views/changeset/submit", json={}).status_code == 409
        review = client.post("/api/views/changeset/rebase", json={}).get_json()
        assert not review["conflicts"] and review["ready"]

    def test_drop_and_discard(self, graph, platform_db, identity_repo):
        owner, _ = self._users(identity_repo, platform_db, graph)
        repo = _repo()
        client, gate = self._app(graph, platform_db, repo)
        gate.user = owner
        node = graph.by_key[KEY]
        body = client.post("/api/views/changeset/fields", json={"process": PROC, "node": node,
                                                                "values": {"owner": "PERS-siran", "title": "Brain"}}).get_json()
        assert body["count"] == 2
        op = body["change_set"]["ops"][0]["id"]
        assert client.delete(f"/api/views/changeset/ops/{op}").get_json()["count"] == 1
        assert client.post("/api/views/changeset/discard", json={}).get_json()["count"] == 0

    def test_without_identity_there_are_no_change_sets(self, graph):
        client = FakeViewsClient(graph)
        svc = views.ViewService(lambda: client, ViewSettings(), bucket=_NoWait(), markers=lambda: set())
        app = Flask(__name__)
        app.register_blueprint(create_views_blueprint(None, svc, graph_mode=lambda: True))
        assert app.test_client().get("/api/views/changeset").status_code == 409
        trace = app.test_client().get(f"/api/views/trace?process={PROC}&instance={graph.by_key[KEY]}").get_json()
        assert trace["changeable"] is False and trace["pending"] == []
