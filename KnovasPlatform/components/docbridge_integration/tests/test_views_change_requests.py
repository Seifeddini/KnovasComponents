"""Ansichten, Step 7: change requests for feature plans, as pull requests.

Field mapping onto the plan file (template-shaped and deviating plans), change
classification, status rollback, the decision record, permissions through
node_grants, conflict detection, the impact preview, and the submit path
against an in-memory repository (no network).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import subprocess
import uuid
from pathlib import Path

import pytest
from flask import Flask

from graph_views import change_requests as cr, plan_file, service as views
from graph_views.settings import ViewSettings
from views_fixture import FakeViewsClient, client_zero_graph
from web_interface.views_routes import create_views_blueprint

TODAY = dt.date(2026, 10, 28)
PLAN_PATH = "docs/ModernDocs/plans/2026-10-05-graph-native-brain.md"
KEY = "FEAT-2026-10-05-graph-native-brain"

PLAN_MD = f"""---
doc_type: feature_plan
canonical: false
status: approved
product: semantix
---

# Feature: Graph-native company brain

| Field | Value |
|-------|-------|
| **Author** | Platform |
| **Date** | 2026-10-05 |
| **Status** | approved |
| **L1 context** | L1-BRAIN |
| **Affected L2** | L2-BRAIN-GRAPH-STORE, L2-KNOWLEDGE-GRAPH |

## Problem

The brain stores its graph in PostgreSQL tables; walks are slow.

## Component contract deltas

### L2-BRAIN-GRAPH-STORE

- RESP-01: owns the graph.

## Risks

| ID | Risk | Mitigation |
|----|------|------------|
| RISK-01 | Migration loses edges | dual write |

## References

- docs/ModernDocs/plans/2026-10-05-graph-native-brain-roadmap.md
"""

PLAN_AI = f"""# Machine-readable feature plan
schema_version: 1

feature:
  id: {KEY}
  title: "Graph-native company brain"
  status: approved
  author: Platform
  date: 2026-10-05

hierarchy:
  L1: L1-BRAIN
  affected_L2:
    - L2-BRAIN-GRAPH-STORE
    - L2-KNOWLEDGE-GRAPH

problem:
  statement: "The brain stores its graph in PostgreSQL tables; walks are slow."
"""

LINKS = f"""version: 1
nodes: []
edges:
  - {{from: {KEY}, relation: serves, to: CAP-11}}
  - {{from: {KEY}, relation: motivated_by, to: GOAL-2026-Q4-brain-on-graph-store}}
  - {{from: FEAT-other, relation: serves, to: CAP-01}}
"""


def _sha(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class FakeRepo:
    """Commits as dicts of files; one branch 'master'; records writes and PRs."""

    def __init__(self, files):
        self.commits = {_sha("c1"): dict(files)}
        self.head = _sha("c1")
        self.created, self.puts, self.prs = [], [], []
        self.pulls = {}

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


def _repo(md=PLAN_MD):
    return FakeRepo({PLAN_PATH: md, PLAN_PATH[:-3] + ".ai.yaml": PLAN_AI, plan_file.PLAN_LINKS_PATH: LINKS,
                     f"{cr.DECISIONS_DIR}/DEC-0016-definitions-on-the-tenant.md": "x",
                     f"{cr.DECISIONS_DIR}/DEC-0017-definition-validation-rules.md": "y"})


class _NoWait:
    def acquire(self):
        return None


@pytest.fixture
def graph():
    g = client_zero_graph()
    proc = g.by_key["PROC-feature-lifecycle"]
    plan = g.node("FeaturePlan", "Feature: Graph-native company brain", key=KEY, status="approved",
                  owner="Platform", source=PLAN_PATH, size_points="80 sp")
    g.edge(plan, "instance_of", proc)
    g.edge(plan, "in_state", g.by_key["STEP-feature-lifecycle-approved"])
    g.edge(plan, "affects", g.by_key["L2-KNOWLEDGE-GRAPH"])
    dec = g.node("Decision", "DEC-0099 — keep the store", key="DEC-0099", status="decided")
    g.edge(dec, "affects", plan)
    store = g.node("Component", "L2-BRAIN-GRAPH-STORE — store", key="L2-BRAIN-GRAPH-STORE")
    g.edge(plan, "affects", store)
    g.node("Component", "L2-USAGE-METER — meter", key="L2-USAGE-METER")
    return g


def _ctx(graph):
    client = FakeViewsClient(graph)
    svc = views.ViewService(lambda: client, ViewSettings(), bucket=_NoWait(), markers=lambda: set())
    ctx = svc.context()
    return ctx, svc.definitions(ctx), svc


# -- the plan file ---------------------------------------------------------------------


class TestFieldMapping:
    def test_reads_every_template_field(self):
        f = plan_file.read(PLAN_MD, KEY, LINKS)
        assert (f.title, f.status, f.owner) == ("Graph-native company brain", "approved", "Platform")
        assert f.affected_components == ["L2-BRAIN-GRAPH-STORE", "L2-KNOWLEDGE-GRAPH"]
        assert f.description.startswith("The brain stores") and "RESP-01" in f.requirements
        assert f.risks == [{"id": "RISK-01", "risk": "Migration loses edges", "mitigation": "dual write"}]
        assert f.capability == ["CAP-11"] and f.goal == ["GOAL-2026-Q4-brain-on-graph-store"]
        assert f.sources.strip().startswith("- docs/ModernDocs/plans/")

    def test_a_deviating_plan_reports_absent_fields(self):
        md = "# Company Brain — epics\n\nSome text.\n"
        f = plan_file.read(md, "FEAT-x", "")
        assert (f.owner, f.affected_components, f.description, f.status) == (None, None, None, None)

    def test_edits_change_only_their_field(self):
        e = plan_file.apply(PLAN_MD, {"owner": "Siran", "description": "New problem statement."})
        assert "| **Author** | Siran |" in e.text and "New problem statement." in e.text
        assert e.text.count("## Problem") == 1 and "| **Date** | 2026-10-05 |" in e.text
        assert "RESP-01" in e.text and "dual write" in e.text

    def test_status_sets_front_matter_and_row(self):
        e = plan_file.apply(PLAN_MD, {"status": "review"})
        assert "\nstatus: review\n" in e.text and "| **Status** | review |" in e.text

    def test_absent_fields_are_inserted_where_the_template_puts_them(self):
        md = "# Company Brain — epics\n\n| Field | Value |\n|-------|-------|\n| **Date** | 2026-08-02 |\n\nBody.\n"
        e = plan_file.apply(md, {"owner": "Siran", "description": "Why."})
        assert "| **Author** | Siran |" in e.text and "## Problem\n\nWhy." in e.text
        assert "eingefügt" in e.notes["owner"] and "neu angelegt" in e.notes["description"]

    def test_risks_rows(self):
        e = plan_file.apply(PLAN_MD, {"risks": [{"id": "RISK-01", "risk": "Edges lost", "mitigation": "dual write"},
                                               {"id": "RISK-02", "risk": "Slow cutover", "mitigation": ""}]})
        assert "| RISK-01 | Edges lost | dual write |" in e.text and "| RISK-02 | Slow cutover |" in e.text

    def test_ai_yaml_mirrors_and_keeps_the_rest(self):
        e = plan_file.apply_ai_yaml(PLAN_AI, {"status": "review", "owner": "Siran",
                                              "affected_components": ["L2-BRAIN-GRAPH-STORE"]})
        assert "  status: review\n" in e.text and "  author: Siran\n" in e.text
        assert "    - L2-BRAIN-GRAPH-STORE\n\nproblem:" in e.text and "L2-KNOWLEDGE-GRAPH" not in e.text
        assert "schema_version: 1" in e.text

    def test_plan_links_keep_unchanged_lines_in_place(self):
        text = plan_file.set_links(LINKS, KEY, "serves", ["CAP-11", "CAP-02"], "change request")
        assert text.index("to: CAP-11") < text.index("relation: motivated_by")        # untouched line, same place
        assert text.endswith(f"  # change request\n  - {{from: {KEY}, relation: serves, to: CAP-02}}\n")

    def test_plan_links_change_this_plan_only(self):
        text = plan_file.set_links(LINKS, KEY, "serves", ["CAP-05"], "change request")
        assert f"{{from: {KEY}, relation: serves, to: CAP-05}}" in text
        assert f"{{from: {KEY}, relation: serves, to: CAP-11}}" not in text
        assert "{from: FEAT-other, relation: serves, to: CAP-01}" in text
        assert f"{{from: {KEY}, relation: motivated_by, to: GOAL-2026-Q4-brain-on-graph-store}}" in text


# -- classes, rework, decision record -------------------------------------------------------


CLASSES = cr._config()["PROC-feature-lifecycle"]


class TestClassification:
    def test_minor_and_substantive(self):
        assert cr.classify(["owner", "description"], CLASSES) == "minor"
        assert cr.classify(["owner", "capability"], CLASSES) == "substantive"

    def test_unmapped_and_empty(self):
        with pytest.raises(cr.ChangeError, match="Zieldatum|target_date|abbildbar"):
            cr.classify(["target_date"], CLASSES)
        with pytest.raises(cr.ChangeError, match="Keine Änderung"):
            cr.classify([], CLASSES)

    def test_whitespace_is_not_a_change(self):
        assert cr.changed_fields({"description": "a  b\n"}, {"description": "a b"}) == []

    def test_an_empty_field_absent_from_the_file_is_not_a_change(self):
        """The form sends "" or [] for a section the plan lacks; that once made a capability edit list three more fields."""
        current = {"description": None, "requirements": None, "sources": None, "capability": ["CAP-11"]}
        proposed = {"description": "", "requirements": [], "sources": "  ", "capability": ["CAP-11", "CAP-02"]}
        assert cr.changed_fields(current, proposed) == ["capability"]

    def test_rework_only_for_substantive_from_late_steps(self):
        assert cr.rework_status(CLASSES, "substantive", "approved") == "review"
        assert cr.rework_status(CLASSES, "substantive", "brief") is None
        assert cr.rework_status(CLASSES, "minor", "approved") is None

    def test_classes_from_the_definition_win(self):
        defs = type("D", (), {"process": lambda self, k: {"change_classes": {"minor": ["title"], "substantive": [], "rework": {}}}})()
        assert cr.change_classes(defs, "PROC-feature-lifecycle")["source"] == "definition"

    def test_decision_record_format_and_number(self):
        n = cr.next_decision_number(["DEC-0016-x.md", "DEC-0017-y.md", "README.md"])
        name, text = cr.decision_record(n, plan_key=KEY, plan_title="Graph-native", actor="Siran",
                                        why="Scope narrowed to the store.", fields=["affected_components"],
                                        components=["L2-BRAIN-GRAPH-STORE"], new_status="review", today=TODAY)
        assert name == "DEC-0018-2026-10-05-graph-native-brain-change.md"
        assert text.startswith("---\nid: DEC-0018\n") and "status: proposed\n" in text
        assert f"affects: [{KEY}, L2-BRAIN-GRAPH-STORE]" in text
        assert "**Context.**" in text and "**Decision.** Scope narrowed to the store." in text and "**Consequences.**" in text
        assert "`review`" in text

    def test_keys_must_have_their_declared_form(self):
        with pytest.raises(cr.ChangeError, match="Capability"):
            cr.check_keys({"capability": ["Ask the brain"]})
        cr.check_keys({"capability": ["CAP-05"], "goal": ["GOAL-2026-Q4-x", "P-02.H01"],
                       "affected_components": ["L2-USAGE-METER"]})


class _Grants:
    def __init__(self, owner=None, editors=()):
        self.owner, self.editors = owner, set(editors)

    def for_node(self, node_id):
        return {"owner": self.owner, "editors": sorted(self.editors)}

    def may_write(self, node_id, user):
        return "admin" in user.roles or str(user.id) in ({self.owner} | self.editors)


def _user(*roles):
    return type("U", (), {"id": uuid.uuid4(), "roles": frozenset(roles), "display_name": "U", "email": "u@x"})()


class TestPermissions:
    def test_substantive_needs_the_owner_or_an_admin(self):
        owner, editor, admin, other = _user(), _user(), _user("admin"), _user()
        grants = _Grants(owner=str(owner.id), editors=[str(editor.id)])
        assert cr.may_propose("substantive", owner, "n", grants)
        assert cr.may_propose("substantive", admin, "n", grants)
        assert not cr.may_propose("substantive", editor, "n", grants)
        assert cr.may_propose("minor", editor, "n", grants)
        assert not cr.may_propose("minor", other, "n", grants)
        assert not cr.may_propose("minor", None, "n", grants)


# -- prepare: impact, rollback, conflicts --------------------------------------------------


class TestPrepare:
    def _form(self, graph, repo):
        ctx, d, _ = _ctx(graph)
        return ctx, d, cr.form(ctx, d, "PROC-feature-lifecycle", graph.by_key[KEY], repo, "master")

    def test_form_holds_the_file_values(self, graph):
        _, _, f = self._form(graph, _repo())
        assert f["values"]["owner"] == "Platform" and f["values"]["capability"] == ["CAP-11"]
        assert f["plan"]["step"] == "approved" and f["absent"] == [] and "target_date" in f["unmapped"]

    def test_minor_change_keeps_the_step(self, graph):
        repo = _repo()
        ctx, d, f = self._form(graph, repo)
        p = cr.prepare(ctx, d, "PROC-feature-lifecycle", graph.by_key[KEY], repo, "master",
                       {**f["values"], "owner": "Siran"}, base_shas=f["base"], why="", actor="Siran", today=TODAY)
        assert p["class"] == "minor" and p["new_status"] is None and p["fields"] == ["owner"]
        assert sorted(p["files"]) == sorted([PLAN_PATH, PLAN_PATH[:-3] + ".ai.yaml"])
        assert "+| **Author** | Siran |" in p["diffs"][PLAN_PATH]

    def test_substantive_change_rolls_back_and_adds_a_decision(self, graph):
        repo = _repo()
        ctx, d, f = self._form(graph, repo)
        p = cr.prepare(ctx, d, "PROC-feature-lifecycle", graph.by_key[KEY], repo, "master",
                       {**f["values"], "capability": ["CAP-05"], "affected_components": ["L2-BRAIN-GRAPH-STORE", "L2-USAGE-METER"]},
                       base_shas=f["base"], why="The store also meters graph reads now.", actor="Siran", today=TODAY)
        assert p["class"] == "substantive" and p["new_status"] == "review"
        assert "+status: review" in p["diffs"][PLAN_PATH]
        assert plan_file.PLAN_LINKS_PATH in p["files"]
        dec = [x for x in p["files"] if x.startswith(cr.DECISIONS_DIR)]
        assert dec == [f"{cr.DECISIONS_DIR}/DEC-0018-2026-10-05-graph-native-brain-change.md"]
        imp = p["impact"]
        assert imp["returns_to"] == "brief" and imp["rules"] == ["R1", "R7"]          # 80 sp >= 50
        names = {c["name"]: c for c in imp["components"]}
        assert names["L2-USAGE-METER — meter"]["added"] and names["L2-KNOWLEDGE-GRAPH — Knowledge Graph"]["removed"]
        assert [x["name"] for x in imp["decisions"]] == ["DEC-0099 — keep the store"]
        assert imp["invariants"]                                                     # via keeps

    def test_substantive_needs_a_reason(self, graph):
        repo = _repo()
        ctx, d, f = self._form(graph, repo)
        with pytest.raises(cr.ChangeError, match="Begründung"):
            cr.prepare(ctx, d, "PROC-feature-lifecycle", graph.by_key[KEY], repo, "master",
                       {**f["values"], "capability": ["CAP-05"]}, base_shas=f["base"], why="", actor="x", today=TODAY)

    def test_a_moved_plan_file_is_a_conflict_with_a_diff(self, graph):
        repo = _repo()
        ctx, d, f = self._form(graph, repo)
        repo.advance(PLAN_PATH, PLAN_MD.replace("walks are slow", "walks are slow and costly"))
        with pytest.raises(cr.ChangeConflict) as exc:
            cr.prepare(ctx, d, "PROC-feature-lifecycle", graph.by_key[KEY], repo, "master",
                       {**f["values"], "owner": "Siran"}, base_shas=f["base"], why="", actor="x", today=TODAY)
        assert "-The brain stores its graph in PostgreSQL tables; walks are slow." in exc.value.diff
        assert "+The brain stores its graph in PostgreSQL tables; walks are slow and costly." in exc.value.diff
        assert exc.value.current["base"]["commit"] == repo.head

    @pytest.mark.parametrize("bad", ["--output=/tmp/x", "HEAD", "-c1", "../x", 7])
    def test_a_base_that_is_not_an_object_id_is_refused(self, graph, bad):
        repo = _repo()
        ctx, d, f = self._form(graph, repo)
        with pytest.raises(cr.ChangeError, match="Ungültige Basis"):
            cr.prepare(ctx, d, "PROC-feature-lifecycle", graph.by_key[KEY], repo, "master",
                       {**f["values"], "owner": "Siran"}, base_shas={**f["base"], "commit": bad},
                       why="", actor="x", today=TODAY)


def test_local_repo_never_passes_a_revision_as_an_option(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    repo = cr.LocalRepo(str(tmp_path))
    with pytest.raises(cr.ChangeError, match="Revision"):
        repo.get_file("x.md", "--output=" + str(tmp_path / "pwned"))
    assert not (tmp_path / "pwned").exists()
    # and a revision that is not one is a missing file, not an error
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

    def test_r7_unknown_size_is_said(self, graph):
        ctx, _, _ = _ctx(graph)
        imp = cr.impact(ctx, graph.by_key[KEY], step="approved", change_class="substantive", classes=CLASSES,
                        proposed_components=None, facts={})
        assert imp["rules"] == ["R1"] and "size_points" in imp["size_note"]

    def test_submit_branches_commits_and_opens_the_pr(self, graph):
        repo = _repo()
        ctx, d, f = self._form(graph, repo)
        p = cr.prepare(ctx, d, "PROC-feature-lifecycle", graph.by_key[KEY], repo, "master",
                       {**f["values"], "goal": ["GOAL-2026-Q4-brain-on-graph-store", "P-02.H01"]},
                       base_shas=f["base"], why="A hypothesis justifies it too.", actor="Siran", today=TODAY)
        pr = cr.submit(p, repo, "master", actor="Siran", why="A hypothesis justifies it too.",
                       now=dt.datetime(2026, 10, 28, 9, 0, 0))
        assert repo.created == [("platform/change-2026-10-05-graph-native-brain-20261028090000", _sha("c1"))]
        assert {x["path"] for x in repo.puts} == set(p["files"])
        links = next(x["text"] for x in repo.puts if x["path"] == plan_file.PLAN_LINKS_PATH)
        assert f"relation: justified_by, to: P-02.H01" in links
        assert pr["number"] == 300 and "Rules to pass again: R1, R7" in repo.prs[0]["body"]

    def test_a_local_checkout_previews_but_cannot_submit(self, graph):
        with pytest.raises(cr.ChangeNotConfigured):
            cr.submit({"plan": KEY}, cr.LocalRepo("."), "master", actor="x", why="")


# -- routes, store, audit (database) ----------------------------------------------------------

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable  # noqa: E402


@pytest.mark.skipif(not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")
class TestRoutes:
    def _app(self, graph, platform_db, repo):
        from identity.change_requests import ChangeRequestStore
        from identity.node_grants import NodeGrantStore

        client = FakeViewsClient(graph)
        svc = views.ViewService(lambda: client, ViewSettings(), bucket=_NoWait(), markers=lambda: set())
        gate = type("G", (), {"user": None, "current_user": lambda self: self.user, "connection": lambda self: platform_db})()
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"))
        app.register_blueprint(create_views_blueprint(
            gate, svc, graph_mode=lambda: True, change_repo=lambda: repo,
            change_store=lambda: ChangeRequestStore(platform_db), node_grants=lambda: NodeGrantStore(platform_db)))
        return app.test_client(), gate

    def test_owner_submits_editor_is_refused_audit_and_pending(self, graph, platform_db, identity_repo):
        from identity import audit
        from identity.node_grants import NodeGrantStore

        mk = lambda n: identity_repo.get(identity_repo.create(email=f"{n}@kanzlei.ch", display_name=n.title(),  # noqa: E731
                                                              password="korrektes-pferd-batterie").id)
        owner, editor = mk("olga"), mk("edda")
        node = graph.by_key[KEY]
        NodeGrantStore(platform_db).set_owner(node, owner.id)
        NodeGrantStore(platform_db).grant_editor(node, editor.id, granted_by=owner.id)
        repo = _repo()
        client, gate = self._app(graph, platform_db, repo)

        gate.user = editor
        form = client.get(f"/api/views/change?process=PROC-feature-lifecycle&node={node}").get_json()
        assert form["permissions"] == {"minor": True, "substantive": False}
        body = {"process": "PROC-feature-lifecycle", "node": node, "base": form["base"],
                "values": {**form["values"], "capability": ["CAP-05"]}, "why": "Belongs to filing, not to the brain."}
        preview = client.post("/api/views/change/preview", json=body).get_json()
        assert preview["class"] == "substantive" and preview["permitted"] is False
        assert client.post("/api/views/change", json=body).status_code == 403

        gate.user = owner
        res = client.post("/api/views/change", json=body)
        assert res.status_code == 201 and res.get_json()["pr"]["number"] == 300
        entry = audit.recent(platform_db, action="plan.change.proposed")[0]
        assert entry["actor_email"] == "olga@kanzlei.ch" and entry["target_id"] == KEY
        assert entry["detail"]["class"] == "substantive" and entry["detail"]["pr_url"].endswith("/300")
        pending = client.get(f"/api/views/change/pending?node={node}").get_json()["pending"]
        assert [p["pr_number"] for p in pending] == [300]

        repo.pulls[300].update(state="closed", merged=True)
        assert client.get(f"/api/views/change/pending?node={node}").get_json()["pending"][0]["state"] == "merged"

    def test_conflict_answers_409_with_the_diff(self, graph, platform_db, identity_repo):
        admin = identity_repo.get(identity_repo.create(email="ad@kanzlei.ch", display_name="Ad",
                                                       password="korrektes-pferd-batterie").id)
        identity_repo.grant_role(admin.id, "admin")
        admin = identity_repo.get(admin.id)
        repo = _repo()
        client, gate = self._app(graph, platform_db, repo)
        gate.user = admin
        node = graph.by_key[KEY]
        form = client.get(f"/api/views/change?process=PROC-feature-lifecycle&node={node}").get_json()
        repo.advance(PLAN_PATH, PLAN_MD.replace("Platform", "Plattform-Team", 1))
        res = client.post("/api/views/change/preview", json={"process": "PROC-feature-lifecycle", "node": node,
                                                             "base": form["base"], "values": {**form["values"], "description": "x"}})
        assert res.status_code == 409 and res.get_json()["conflict"] and "+| **Author** | Plattform-Team |" in res.get_json()["diff"]

    def test_without_identity_there_are_no_change_requests(self, graph):
        client = FakeViewsClient(graph)
        svc = views.ViewService(lambda: client, ViewSettings(), bucket=_NoWait(), markers=lambda: set())
        app = Flask(__name__)
        app.register_blueprint(create_views_blueprint(None, svc, graph_mode=lambda: True))
        assert app.test_client().get(f"/api/views/change?node={graph.by_key['L2-KNOWLEDGE-GRAPH']}").status_code == 409
