"""Task 7 (SS-627): the read — what a custom view's code gets back from the Brain.

Every path must keep the manifest's limits: the type and facts it names, link
ends of the types it names only, ``linked.to`` ids of those types only, the
budget charged before anything is read, never silently partial. D-4: no formal
model; instead the combinations of the Platform's own checks (link end type x
``reads`` x direction; ``linked.to`` id type x ``reads`` x direction), each on
the complete index and on the per-node fallback.
"""
from __future__ import annotations

import itertools
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from custom_views.budget import RefusingBudget
from custom_views.manifest import parse_manifest
from custom_views.query import Forbidden, parse_query
from custom_views.read import BudgetExceeded, TooBroad, run_read
from graph_views.reader import GraphReader
from graph_views.settings import ViewSettings
from graph_views.topology import PartialIndex, TopologyCache
from views_fixture import FakeViewsClient, FixtureGraph, client_zero_graph

MAX = 200


def _manifest(reads):
    return parse_manifest("test-view", json.dumps({
        "format": 1, "title": {"de": "Test", "en": "Test"}, "roles": ["*"],
        "entry": "index.html", "reads": reads}).encode())


class World:
    """A graph behind the fake client, the reader and one subject's state."""

    def __init__(self, g, *, partial=False):
        self.g = g
        self.client = FakeViewsClient(g)
        self.reader = GraphReader(self.client, acquire=None)
        settings = ViewSettings(edge_limit=0) if partial else ViewSettings()
        self.state = TopologyCache(settings).get(None, self.reader)
        self.budget = RefusingBudget(60, capacity=MAX, clock=lambda: 0.0)

    def read(self, body, reads, *, budget=None, max_nodes=MAX):
        query = parse_query(body, max_nodes=max_nodes)
        return run_read(query, _manifest(reads), self.state, self.reader,
                        budget if budget is not None else self.budget, max_nodes=max_nodes)

    def batch_ids(self):
        return [i for body in self.client.batch_bodies for i in body["node_ids"]]


@pytest.fixture
def cz():
    return World(client_zero_graph())


def _names(out):
    return [n["name"] for n in out["nodes"]]


def _key(g, key):
    return g.by_key[key]


# -- by type, ids, the answer's shape ----------------------------------------------


def test_by_type_answers_every_node_by_name_and_reads_no_fact(cz):
    out = cz.read({"type": "Invariant"}, {"Invariant": ["key"]})
    assert _names(out) == ["GI-GRAPH-00", "GI-GRAPH-01", "GI-GRAPH-02"]
    assert out["total"] == 3 and out["links_hidden"] == 0
    assert out["complete"] is True and out["incomplete_why"] == []
    node = out["nodes"][0]
    assert node == {"id": _key(cz.g, "GI-GRAPH-00"), "name": "GI-GRAPH-00", "type": "Invariant",
                    "description": "", "facts": {}, "links": []}
    assert cz.client.batch_bodies == []          # nothing asked for facts, nothing read
    read_at = datetime.fromisoformat(out["read_at"])
    assert read_at.utcoffset() == timedelta(0)
    assert abs(datetime.now(timezone.utc) - read_at) < timedelta(minutes=1)
    json.dumps(out)                               # the route sends it as JSON


def test_order_is_case_insensitive_name_then_id():
    g = FixtureGraph()
    ids = [g.node("Thing", name) for name in ("beta", "Alpha", "alpha", "Gamma")]
    out = World(g).read({"type": "Thing"}, {"Thing": []})
    alphas = sorted([ids[1], ids[2]])
    assert [n["id"] for n in out["nodes"]] == alphas + [ids[0], ids[3]]


def test_ids_narrow_and_an_id_of_another_type_matches_nothing(cz):
    g = cz.g
    body = {"type": "Invariant", "ids": [_key(g, "GI-GRAPH-02"), _key(g, "GI-GRAPH-00"),
                                         _key(g, "L2-KNOWLEDGE-GRAPH"), "no-such-id"]}
    out = cz.read(body, {"Invariant": [], "Component": []})
    assert _names(out) == ["GI-GRAPH-00", "GI-GRAPH-02"]
    assert out["total"] == 2 and out["complete"] is True


def test_a_type_the_tenant_lacks_answers_no_nodes_and_complete(cz):
    body = {"type": "NoSuchType", "facts": ["x"], "where": {"x": "y"},
            "links": [{"relation": "r", "direction": "out"}]}
    out = cz.read(body, {"NoSuchType": ["x"]})
    assert out["nodes"] == [] and out["total"] == 0
    assert out["complete"] is True and out["incomplete_why"] == []
    assert cz.client.batch_bodies == []


def test_a_query_outside_the_manifest_is_refused_by_the_read_itself(cz):
    with pytest.raises(Forbidden):
        cz.read({"type": "Invariant"}, {"Component": []})
    with pytest.raises(Forbidden):
        cz.read({"type": "AlloyCheck", "facts": ["file"]}, {"AlloyCheck": ["outcome"]})
    with pytest.raises(Forbidden):
        cz.read({"type": "AlloyCheck", "where": {"file": "x"}}, {"AlloyCheck": ["outcome"]})
    assert cz.client.batch_bodies == []


# -- linked --------------------------------------------------------------------------


def test_linked_out(cz):
    comp = _key(cz.g, "L2-KNOWLEDGE-GRAPH")
    body = {"type": "AlloyCheck", "linked": {"to": [comp], "relation": "verifies", "direction": "out"}}
    out = cz.read(body, {"AlloyCheck": [], "Component": []})
    assert out["total"] == 22 and len(out["nodes"]) == 22
    assert _names(out)[:2] == ["ALLOY-check-00", "ALLOY-check-01"]


def test_linked_in(cz):
    plan = _key(cz.g, "FEAT-2026-09-08-adaptive-rate-limits")
    body = {"type": "Component", "linked": {"to": [plan], "relation": "affects", "direction": "in"}}
    out = cz.read(body, {"Component": [], "FeaturePlan": []})
    assert _names(out) == ["L2-KNOWLEDGE-GRAPH — Knowledge Graph"]
    wrong_way = dict(body, linked=dict(body["linked"], direction="out"))
    assert cz.read(wrong_way, {"Component": [], "FeaturePlan": []})["nodes"] == []


def test_linked_and_ids_narrow_together(cz):
    g = cz.g
    plan = _key(g, "FEAT-2026-09-08-adaptive-rate-limits")
    body = {"type": "Invariant", "ids": [_key(g, "GI-GRAPH-01")],
            "linked": {"to": [plan], "relation": "keeps", "direction": "in"}}
    out = cz.read(body, {"Invariant": [], "FeaturePlan": []})
    assert _names(out) == ["GI-GRAPH-01"]


# -- where ----------------------------------------------------------------------------


def test_where_single_value_trimmed_and_case_ignored(cz):
    out = cz.read({"type": "AlloyCheck", "where": {"outcome": "  FAILED "}, "facts": ["outcome"]},
                  {"AlloyCheck": ["outcome"]})
    assert _names(out) == ["ALLOY-check-03", "ALLOY-check-07"]
    assert all(n["facts"] == {"outcome": "failed"} for n in out["nodes"])
    assert out["total"] == 2 and out["complete"] is True


def test_where_trims_and_ignores_case_on_the_stored_value_too():
    g = FixtureGraph()
    hit = g.node("Thing", "a", state="  In Progress ")
    g.node("Thing", "b", state="in progress, blocked")
    out = World(g).read({"type": "Thing", "where": {"state": "in progress"}, "facts": ["state"]},
                        {"Thing": ["state"]})
    assert [n["id"] for n in out["nodes"]] == [hit]
    assert out["nodes"][0]["facts"] == {"state": "  In Progress "}     # returned as stored


def test_where_a_list_matches_any_of_its_values(cz):
    out = cz.read({"type": "AlloyCheck", "where": {"outcome": ["Failed", "passed"]}},
                  {"AlloyCheck": ["outcome"]})
    assert out["total"] == 22


def test_where_reads_the_facts_of_every_candidate_once(cz):
    cz.read({"type": "AlloyCheck", "where": {"outcome": "failed"}}, {"AlloyCheck": ["outcome"]})
    assert sorted(cz.batch_ids()) == sorted(cz.state.index.by_type["AlloyCheck"])


def test_where_on_a_fact_no_node_has_answers_no_nodes(cz):
    out = cz.read({"type": "Invariant", "where": {"status": "open"}}, {"Invariant": ["status"]})
    assert out["nodes"] == [] and out["total"] == 0 and out["complete"] is True


def test_where_over_max_nodes_candidates_is_too_broad_and_reads_nothing():
    g = FixtureGraph()
    for i in range(MAX + 1):
        g.node("Ticket", f"t{i:03d}", state="open")
    w = World(g)
    with pytest.raises(TooBroad):
        w.read({"type": "Ticket", "where": {"state": "open"}}, {"Ticket": ["state"]})
    assert w.client.batch_bodies == []
    # the same set without where is fine: capped at the limit and said so
    out = w.read({"type": "Ticket"}, {"Ticket": ["state"]})
    assert len(out["nodes"]) == MAX and out["total"] == MAX + 1
    assert out["incomplete_why"] == ["node_cap"]
    # narrowed to max_nodes by ids, where is allowed
    ids = w.state.index.by_type["Ticket"][:MAX]
    out = w.read({"type": "Ticket", "ids": ids, "where": {"state": "open"}}, {"Ticket": ["state"]})
    assert out["total"] == MAX and out["complete"] is True


# -- facts ----------------------------------------------------------------------------


def test_facts_are_only_the_requested_ones(cz):
    out = cz.read({"type": "AlloyCheck", "ids": [_key(cz.g, "ALLOY-03")], "facts": ["outcome"]},
                  {"AlloyCheck": ["outcome", "file", "key"]})
    assert out["nodes"][0]["facts"] == {"outcome": "failed"}


def test_a_fact_a_node_lacks_is_simply_missing(cz):
    out = cz.read({"type": "Invariant", "facts": ["key", "status"]}, {"Invariant": ["key", "status"]})
    assert [n["facts"] for n in out["nodes"]] == [
        {"key": "GI-GRAPH-00"}, {"key": "GI-GRAPH-01"}, {"key": "GI-GRAPH-02"}]
    assert out["complete"] is True


def test_a_closed_fact_is_not_returned_the_newest_open_one_is():
    g = FixtureGraph()
    nid = g.node("Thing", "n")
    g.add_fact(nid, "state", "old", valid_from="2026-01-01", valid_to="2026-02-01")
    g.add_fact(nid, "state", "new", valid_from="2026-02-01")
    out = World(g).read({"type": "Thing", "facts": ["state"]}, {"Thing": ["state"]})
    assert out["nodes"][0]["facts"] == {"state": "new"}


def test_without_where_only_the_returned_nodes_facts_are_read(cz):
    ids = [_key(cz.g, f"ALLOY-{i:02d}") for i in range(5)]
    out = cz.read({"type": "AlloyCheck", "ids": ids, "facts": ["outcome"], "limit": 2},
                  {"AlloyCheck": ["outcome"]})
    assert _names(out) == ["ALLOY-check-00", "ALLOY-check-01"]
    assert sorted(cz.batch_ids()) == sorted(ids[:2])


def test_limit_2_of_5_is_node_cap(cz):
    ids = [_key(cz.g, f"ALLOY-{i:02d}") for i in range(5)]
    out = cz.read({"type": "AlloyCheck", "ids": ids, "limit": 2}, {"AlloyCheck": []})
    assert len(out["nodes"]) == 2 and out["total"] == 5
    assert out["complete"] is False and out["incomplete_why"] == ["node_cap"]


def test_a_node_whose_facts_read_fails_is_dropped_and_said(cz):
    inv = [_key(cz.g, f"GI-GRAPH-{i:02d}") for i in range(3)]
    cz.client.hidden.add(inv[1])                 # visible in the topology, its facts not
    out = cz.read({"type": "Invariant", "facts": ["key"]}, {"Invariant": ["key"]})
    assert _names(out) == ["GI-GRAPH-00", "GI-GRAPH-02"]
    assert out["total"] == 2
    assert out["complete"] is False and out["incomplete_why"] == ["facts_unreadable"]


def test_a_node_whose_facts_read_fails_never_matches_where(cz):
    failed = _key(cz.g, "ALLOY-03")
    cz.client.hidden.add(failed)
    out = cz.read({"type": "AlloyCheck", "where": {"outcome": "failed"}}, {"AlloyCheck": ["outcome"]})
    assert _names(out) == ["ALLOY-check-07"]
    assert out["incomplete_why"] == ["facts_unreadable"]


def test_a_failed_batch_drops_every_node_never_answers_partial_data(cz):
    cz.client.graph_facts_batch = lambda node_ids: (503, None)
    out = cz.read({"type": "Invariant", "facts": ["key"]}, {"Invariant": ["key"]})
    assert out["nodes"] == [] and out["total"] == 0
    assert out["incomplete_why"] == ["facts_unreadable"]


def test_a_node_that_was_not_asked_for_facts_is_not_dropped(cz):
    cz.client.hidden.add(_key(cz.g, "GI-GRAPH-01"))
    out = cz.read({"type": "Invariant"}, {"Invariant": ["key"]})
    assert out["total"] == 3 and out["complete"] is True


def test_facts_land_in_the_cache_shared_with_the_views(cz):
    nid = _key(cz.g, "GI-GRAPH-00")
    cz.read({"type": "Invariant", "ids": [nid], "facts": ["key"]}, {"Invariant": ["key"]})
    assert cz.state.facts[nid] == cz.g.facts[nid]


# -- budget ---------------------------------------------------------------------------


def test_an_empty_budget_refuses_before_any_fact_is_read(cz):
    assert cz.budget.take(MAX) is None           # drained
    with pytest.raises(BudgetExceeded) as exc:
        cz.read({"type": "Invariant", "facts": ["key"]}, {"Invariant": ["key"]})
    assert exc.value.retry_after >= 1
    assert cz.client.batch_bodies == []
    assert cz.state.facts == {}


def test_the_budget_is_charged_one_unit_per_node_whose_facts_are_read(cz):
    ids = [_key(cz.g, f"ALLOY-{i:02d}") for i in range(5)]
    cz.read({"type": "AlloyCheck", "ids": ids, "facts": ["outcome"], "limit": 2}, {"AlloyCheck": ["outcome"]})
    assert cz.budget.take(MAX - 2) is None
    assert cz.budget.take(1) is not None


def test_cached_facts_are_never_charged(cz):
    body = {"type": "Invariant", "facts": ["key"]}
    first = cz.read(body, {"Invariant": ["key"]})
    assert cz.budget.take(MAX - 3) is None       # nothing left
    again = cz.read(body, {"Invariant": ["key"]})
    assert again["nodes"] == first["nodes"]
    assert len(cz.client.batch_bodies) == 1


def test_only_the_uncached_part_is_charged_and_read(cz):
    inv = [_key(cz.g, f"GI-GRAPH-{i:02d}") for i in range(3)]
    cz.read({"type": "Invariant", "ids": inv[:1], "facts": ["key"]}, {"Invariant": ["key"]})
    assert cz.budget.take(MAX - 3) is None       # two units left
    out = cz.read({"type": "Invariant", "facts": ["key"]}, {"Invariant": ["key"]})
    assert out["total"] == 3
    assert cz.client.batch_bodies[-1]["node_ids"] == inv[1:]
    assert cz.budget.take(1) is not None


def test_a_read_that_needs_no_facts_takes_nothing_from_an_empty_budget(cz):
    assert cz.budget.take(MAX) is None
    plan = _key(cz.g, "FEAT-2026-09-08-adaptive-rate-limits")
    out = cz.read({"type": "FeaturePlan", "links": [{"relation": "affects", "direction": "out"}]},
                  {"FeaturePlan": ["status"], "Component": []})
    assert out["nodes"][0]["id"] == plan


# -- links ----------------------------------------------------------------------------


def test_a_link_to_a_type_outside_reads_is_hidden_and_counted(cz):
    g = cz.g
    plan = _key(g, "FEAT-2026-09-08-adaptive-rate-limits")
    comp = _key(g, "L2-KNOWLEDGE-GRAPH")
    body = {"type": "FeaturePlan", "facts": ["status"],
            "links": [{"relation": "affects", "direction": "out"}, {"relation": "keeps", "direction": "out"}]}
    out = cz.read(body, {"FeaturePlan": ["status"], "Component": []})
    node = out["nodes"][0]
    assert node["facts"] == {"status": "implementing"}
    assert node["links"] == [{"relation": "affects", "direction": "out", "id": comp,
                              "name": "L2-KNOWLEDGE-GRAPH — Knowledge Graph", "type": "Component"}]
    assert out["links_hidden"] == 3              # the three invariants
    assert out["complete"] is True               # hidden links are said, not partial
    assert cz.batch_ids() == [plan]              # a link end's facts are never read


def test_links_are_ordered_by_spec_then_name_and_never_doubled(cz):
    comp = _key(cz.g, "L2-KNOWLEDGE-GRAPH")
    spec = {"relation": "keeps", "direction": "out"}
    out = cz.read({"type": "Component", "links": [spec, spec]}, {"Component": [], "Invariant": []})
    links = out["nodes"][0]["links"]
    assert [l["name"] for l in links] == ["GI-GRAPH-00", "GI-GRAPH-01", "GI-GRAPH-02"]
    assert all(set(l) == {"relation", "direction", "id", "name", "type"} for l in links)
    assert out["nodes"][0]["id"] == comp


def test_two_edges_of_one_relation_to_one_node_are_one_link_and_one_hidden_count():
    g = FixtureGraph()
    hub = g.node("Hub", "hub")
    seen, unseen = g.node("Allowed", "seen"), g.node("Outside", "unseen")
    for _ in range(2):
        g.edge(hub, "rel", seen)
        g.edge(hub, "rel", unseen)
    out = World(g).read({"type": "Hub", "links": [{"relation": "rel", "direction": "out"}]},
                        {"Hub": [], "Allowed": []})
    assert [l["id"] for l in out["nodes"][0]["links"]] == [seen]
    assert out["links_hidden"] == 1


def test_the_partial_index_reads_links_and_linked_and_says_partial():
    g = client_zero_graph()
    w = World(g, partial=True)
    assert isinstance(w.state.index, PartialIndex)
    plan = _key(g, "FEAT-2026-09-08-adaptive-rate-limits")
    out = w.read({"type": "Component", "linked": {"to": [plan], "relation": "affects", "direction": "in"},
                  "links": [{"relation": "keeps", "direction": "out"}]},
                 {"Component": [], "FeaturePlan": [], "Invariant": []})
    assert _names(out) == ["L2-KNOWLEDGE-GRAPH — Knowledge Graph"]
    assert [l["name"] for l in out["nodes"][0]["links"]] == ["GI-GRAPH-00", "GI-GRAPH-01", "GI-GRAPH-02"]
    assert out["complete"] is False and out["incomplete_why"] == ["topology_partial"]


# -- D-4: every combination of the read's own checks ------------------------------------

#: end kinds: a type named in reads, the query's own type, a type reads does not name,
#: and a node whose type the index cannot name
END_KINDS = ("allowed", "same", "outside", "untyped")
END_TYPE = {"allowed": "Allowed", "same": "Hub", "outside": "Outside"}
READS = {"Hub": [], "Allowed": []}


def _untyped(g, name):
    """A node whose type is not among the node types the caller can see."""
    nid = str(uuid.uuid4())
    g.nodes[nid] = {"id": nid, "name": name, "node_type_id": "t-unlisted", "description": ""}
    g.facts[nid] = []
    return nid


def _pair(kind, edge_dir):
    g = FixtureGraph()
    for t in ("Hub", "Allowed", "Outside"):
        g.type(t)
    hub = g.node("Hub", "hub")
    other = _untyped(g, "other") if kind == "untyped" else g.node(END_TYPE[kind], "other")
    lo, hi = (hub, other) if edge_dir == "out" else (other, hub)
    g.edge(lo, "rel", hi)
    return g, hub, other


@pytest.mark.parametrize("kind,edge_dir,spec_dir,partial",
                         list(itertools.product(END_KINDS, ("out", "in"), ("out", "in"), (False, True))))
def test_link_end_type_x_reads_x_direction(kind, edge_dir, spec_dir, partial):
    g, hub, other = _pair(kind, edge_dir)
    w = World(g, partial=partial)
    out = w.read({"type": "Hub", "ids": [hub], "links": [{"relation": "rel", "direction": spec_dir}]}, READS)
    (node,) = out["nodes"]
    if spec_dir != edge_dir:
        assert node["links"] == [] and out["links_hidden"] == 0
    elif kind in ("allowed", "same"):
        assert node["links"] == [{"relation": "rel", "direction": spec_dir, "id": other,
                                  "name": "other", "type": END_TYPE[kind]}]
        assert out["links_hidden"] == 0
    else:
        assert node["links"] == [] and out["links_hidden"] == 1
    assert out["incomplete_why"] == (["topology_partial"] if partial else [])


@pytest.mark.parametrize("kind,edge_dir,spec_dir,partial",
                         list(itertools.product(END_KINDS, ("out", "in"), ("out", "in"), (False, True))))
def test_linked_to_id_type_x_reads_x_direction(kind, edge_dir, spec_dir, partial):
    g, hub, other = _pair(kind, edge_dir)
    w = World(g, partial=partial)
    out = w.read({"type": "Hub", "linked": {"to": [other], "relation": "rel", "direction": spec_dir}}, READS)
    # with kind "same" both ends are Hubs; ``other`` itself never qualifies (its link goes to the hub)
    qualifies = spec_dir == edge_dir and kind in ("allowed", "same")
    assert [n["id"] for n in out["nodes"]] == ([hub] if qualifies else [])
    assert out["incomplete_why"] == (["topology_partial"] if partial else [])


def test_a_link_with_another_relation_is_neither_returned_nor_counted():
    g, hub, _ = _pair("outside", "out")
    out = World(g).read({"type": "Hub", "links": [{"relation": "other_rel", "direction": "out"}]}, READS)
    assert out["nodes"][0]["links"] == [] and out["links_hidden"] == 0


def test_linked_with_another_relation_matches_nothing():
    g, hub, other = _pair("allowed", "out")
    out = World(g).read({"type": "Hub", "linked": {"to": [other], "relation": "other_rel", "direction": "out"}},
                        READS)
    assert out["nodes"] == []
