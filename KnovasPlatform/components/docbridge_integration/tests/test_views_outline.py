"""Ansichten: the outline, one page with everything a profile's list view names.

Works on a tenant's bundle with one added view; the view, its types and its facts are test data here, nothing of
them is written in the Platform's code (test_views_profiles.py checks that).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from flask import Flask

from graph_views import bundle, outline, profiles, service as views
from graph_views.settings import ViewSettings
from views_fixture import KNOVAS_BUNDLE, FakeViewsClient, client_zero_graph
from web_interface.views_routes import create_views_blueprint

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"
PROC = "PROC-feature-lifecycle"
KEY = "cto/lifecycle"
VIEW = {
    "id": "lifecycle", "label": "Lebenszyklus", "render": "list",
    "definition": {
        "layout": "outline", "description": "Was als Nächstes kommt.",
        "root_types": ["Bet"], "where": {"fact": "status", "not_in": ["closed"]},
        "order_by": {"fact": "priority_rank"}, "section_by": "priority",
        "emphasis": {"fact": "state", "equals": "next-up"},
        "columns": ["status", "next_up"],
        "labels": {"priority": {"now": "Jetzt", "next": "Danach"}, "state": {"next-up": "Als Nächstes"}},
        "paths": [
            {"label": "Pläne", "path": [{"relation": "part_of", "direction": "in", "to_type": "FeaturePlan"}],
             "process": PROC, "columns": ["status"]},
            {"label": "Offen", "path": [{"relation": "part_of", "direction": "in", "to_type": "WorkItem"}],
             "where": {"fact": "status", "equals": "open"}, "order_by": {"fact": "rank"},
             "columns": ["state", "waits_for"]},
        ],
        "also": [{"label": "Eingang", "type": "WorkItem", "where": {"fact": "status", "equals": "new"},
                  "order_by": {"fact": "added_on"}, "columns": ["why"]}],
    },
}


class _NoWait:
    def acquire(self):
        return None


def _source(tmp_path: Path, view: dict = VIEW) -> str:
    """The Knovas fixture bundle with the view added to the CTO's profile."""
    doc = json.loads(KNOVAS_BUNDLE.read_text(encoding="utf-8"))
    doc["sections"]["profiles"]["cto"]["views"].append(view)
    doc["header"]["content_hash"] = bundle.content_hash(doc["sections"])
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    for name in doc["header"].get("reports") or {}:
        rel = doc["header"]["reports"][name]
        target = (tmp_path / rel).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        original = (KNOVAS_BUNDLE.parent / rel).resolve()
        if original.exists():
            target.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
    return f"file:{path}"


def _graph():
    g = client_zero_graph()
    second = g.node("Bet", "Second bet", key="BET-second", status="approved", priority="next", priority_rank="02")
    first = g.node("Bet", "First bet", key="BET-first", status="approved", priority="now", priority_rank="01",
                   next_up="W-02 the next thing")
    g.node("Bet", "Closed bet", key="BET-closed", status="closed")
    plan = g.node("FeaturePlan", "A brief", key="FEAT-a", status="draft", current_step=("brief", "2026-10-20"))
    g.edge(plan, "instance_of", g.by_key[PROC])
    g.edge(plan, "in_state", g.by_key["STEP-feature-lifecycle-brief"])
    g.edge(plan, "part_of", first)
    for key, rank, status, state, waits in (("W-03", "03", "open", "open", None), ("W-01", "01", "open", "waiting", "an answer"),
                                            ("W-02", "02", "open", "next-up", None), ("W-00", "00", "done", "done", None)):
        item = g.node("WorkItem", f"{key}: work", key=key, status=status, state=state, rank=rank,
                      **({"waits_for": waits} if waits else {}))
        g.edge(item, "part_of", first)
    g.node("WorkItem", "IN-1: new work", key="IN-1", status="new", state="new", added_on="2026-11-02", why="it matters")
    g.node("WorkItem", "IN-0: placed work", key="IN-0", status="placed", state="placed", added_on="2026-11-01")
    return g, {"first": first, "second": second, "plan": plan}


def _svc(g, source, **settings):
    client = FakeViewsClient(g)
    return views.ViewService(lambda: client, ViewSettings(config_source=source, **settings), bucket=_NoWait(),
                             markers=lambda: set())


def _draw(tmp_path, **settings):
    g, n = _graph()
    svc = _svc(g, _source(tmp_path), **settings)
    ctx = svc.context()
    return outline.draw(ctx, svc.definitions(ctx), KEY), n


def test_a_list_view_with_the_layout_opens_as_an_outline_and_another_stays_a_list(tmp_path):
    view = {**VIEW, "definition": {k: v for k, v in VIEW["definition"].items() if k != "layout"}}
    assert profiles.open_spec({"id": "cto"}, VIEW, bundle.read(_source(tmp_path))) == {"kind": "outline", "profile_view": KEY}
    assert profiles.open_spec({"id": "cto"}, view, bundle.read(_source(tmp_path)))["kind"] == "list"


def test_the_roots_follow_their_order_under_their_section_without_what_where_excludes(tmp_path):
    body, _ = _draw(tmp_path)
    assert [r["name"] for r in body["rows"]] == ["First bet", "Second bet"]
    assert [r["section"] for r in body["rows"]] == ["Jetzt", "Danach"]                # the view's labels
    assert body["rows"][0]["cells"] == {"status": "approved", "next_up": "W-02 the next thing"}
    assert body["rows"][0]["key"] == "BET-first" and body["unread"] == 0


def test_a_path_keeps_what_its_where_says_in_its_order_and_marks_the_emphasis(tmp_path):
    body, _ = _draw(tmp_path)
    plans, work = body["rows"][0]["sections"]
    assert (plans["label"], work["label"]) == ("Pläne", "Offen")
    assert [r["key"] for r in work["rows"]] == ["W-01", "W-02", "W-03"]               # W-00 is done; ordered by rank
    assert [r["emphasis"] for r in work["rows"]] == [False, True, False]
    assert work["rows"][0]["cells"] == {"state": "waiting", "waits_for": "an answer"}
    assert work["rows"][1]["cells"]["state"] == "Als Nächstes"
    assert [c["id"] for c in work["columns"]] == ["state", "waits_for"]
    assert [s["rows"] for s in body["rows"][1]["sections"]] == [[], []]               # the second bet has nothing yet


def test_a_plan_shows_its_place_in_the_process_and_what_it_misses(tmp_path):
    body, _ = _draw(tmp_path)
    life = body["rows"][0]["sections"][0]["rows"][0]["lifecycle"]
    states = {s["id"]: s["state"] for s in life["steps"]}
    assert life["current"] == "brief" and states["idea"] == "done" and states["brief"] == "current"
    assert states["approved"] == "todo" and states["released"] == "todo"
    assert "killed" not in states                                                     # shown only for a plan that ended there
    assert life["missing"]                                                            # a brief with nothing behind it


def test_a_plan_stays_placed_when_the_scale_guard_walks_per_node(tmp_path):
    g, _ = _graph()
    svc = _svc(g, _source(tmp_path), edge_limit=1)
    ctx = svc.context()
    assert ctx.state.fallback                                                         # the guard fired
    body = outline.draw(ctx, svc.definitions(ctx), KEY)
    assert body["rows"][0]["sections"][0]["rows"][0]["lifecycle"]["current"] == "brief"
    assert [r["key"] for r in body["rows"][0]["sections"][1]["rows"]] == ["W-01", "W-02", "W-03"]


def test_the_also_part_lists_nodes_tied_to_no_root(tmp_path):
    body, _ = _draw(tmp_path)
    assert [(a["label"], [r["key"] for r in a["rows"]]) for a in body["also"]] == [("Eingang", ["IN-1"])]
    assert body["also"][0]["rows"][0]["cells"] == {"why": "it matters"}


def test_the_fact_budget_is_counted_on_the_page(tmp_path):
    body, _ = _draw(tmp_path, outline_fact_budget=3)
    assert body["unread"] > 0


def test_a_node_whose_facts_could_not_be_read_is_counted_too(tmp_path):
    g, _ = _graph()
    client = FakeViewsClient(g)
    answer = client._one
    broken = f"/nodes/{g.by_key['W-02']}/facts"
    client._one = lambda path, params: (500, None) if path == broken else answer(path, params)
    svc = views.ViewService(lambda: client, ViewSettings(config_source=_source(tmp_path)), bucket=_NoWait(),
                            markers=lambda: set())
    ctx = svc.context()
    body = outline.draw(ctx, svc.definitions(ctx), KEY)
    assert body["unread"] == 1


def test_a_view_that_is_no_outline_is_not_drawn(tmp_path):
    g, _ = _graph()
    svc = _svc(g, _source(tmp_path))
    ctx = svc.context()
    with pytest.raises(profiles.NotAProfileView, match="keine Übersicht"):
        outline.draw(ctx, svc.definitions(ctx), "cto/decision-log")


def _app(svc, gate=None):
    app = Flask(__name__, template_folder=str(TEMPLATES))
    app.register_blueprint(create_views_blueprint(gate, svc, graph_mode=lambda: True))
    return app.test_client()


def test_the_route_draws_the_view_and_names_what_is_wrong(tmp_path):
    g, _ = _graph()
    c = _app(_svc(g, _source(tmp_path)))
    body = c.get(f"/api/views/outline?view={KEY}").get_json()
    assert body["success"] and body["name"] == "Lebenszyklus" and len(body["rows"]) == 2
    assert c.get("/api/views/outline").status_code == 400
    missing = c.get("/api/views/outline?view=cto/no-such-view")
    assert missing.status_code == 404 and "steht nicht im Definitionsbündel" in missing.get_json()["error"]


def test_the_route_answers_another_profiles_user_like_a_missing_view(tmp_path):
    g, _ = _graph()
    svc = _svc(g, _source(tmp_path), profile_roles="admin=CTO; member=Engineer")
    gate = type("G", (), {"user": None, "current_user": lambda self: self.user})()
    c = _app(svc, gate)
    gate.user = type("U", (), {"id": uuid.uuid4(), "roles": frozenset({"member"})})()
    r = c.get(f"/api/views/outline?view={KEY}")
    assert r.status_code == 404 and r.get_json()["error"] == "Ansicht nicht gefunden."
    gate.user = type("U", (), {"id": uuid.uuid4(), "roles": frozenset({"admin"})})()
    assert c.get(f"/api/views/outline?view={KEY}").get_json()["success"]
