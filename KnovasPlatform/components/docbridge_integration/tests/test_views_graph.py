"""Ansichten, Step 1: the decluttered ego graph and what it stands on.

Topology index and its per-subject cache (D1), meta hiding (D2), fact reads
only for drawn nodes (D3), fan-out grouping, the dossier, the routes, and the
paced bulk read in the client. Plan:
docs/superpowers/plans/2026-10-27-process-and-spine-views.md
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest
from flask import Flask

from graph_views import grouping, service as views
from graph_views.pacing import TokenBucket
from graph_views.settings import ViewSettings
from graph_views.topology import PartialIndex, TopologyCache, TopologyIndex, subject_key
from views_fixture import FakeViewsClient, client_zero_graph
from web_interface.views_routes import create_views_blueprint

DEFINITIONS = Path(__file__).resolve().parents[1] / "src" / "graph_views" / "definitions"


class _NoWait:
    def acquire(self):
        return None


def _service(client, **settings):
    return views.ViewService(lambda: client, ViewSettings(**settings), bucket=_NoWait(),
                             markers=lambda: set())


@pytest.fixture
def graph():
    return client_zero_graph()


@pytest.fixture
def client(graph):
    return FakeViewsClient(graph)


# -- topology index ---------------------------------------------------------------


class TestTopologyIndex:
    def test_neighbours_follow_direction_and_relation(self, graph):
        index = TopologyIndex(
            [{"id": tid, "name": n} for n, tid in graph.types.items()],
            list(graph.nodes.values()), graph.edges)
        comp = graph.by_key["L2-KNOWLEDGE-GRAPH"]
        plan = graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"]
        assert ("affects", "in", plan) in index.neighbours(comp, "affects", "in")
        assert index.neighbours(comp, "affects", "out") == []
        assert len(index.neighbours(comp, "verifies", "in")) == 22
        assert index.type_of(plan) == "FeaturePlan"

    def test_an_edge_without_a_relation_name_is_dropped_not_guessed(self, graph):
        index = TopologyIndex([], [], [{"node_lo": "a", "node_hi": "b", "relation": ""}])
        assert index.edge_count == 0


class TestTopologyCache:
    def test_one_read_per_subject_within_the_ttl(self, client):
        svc = _service(client)
        svc.context()
        svc.context()
        assert client.calls.count("/edges") == 1

    def test_refresh_bypasses_the_cache(self, client):
        svc = _service(client)
        svc.context()
        svc.context(refresh=True)
        assert client.calls.count("/edges") == 2

    def test_ttl_expiry_reads_again(self, client):
        clock = [1000.0]
        svc = views.ViewService(lambda: client, ViewSettings(ttl_seconds=60), bucket=_NoWait(),
                                now=lambda: clock[0], markers=lambda: set())
        svc.context()
        clock[0] += 61
        svc.context()
        assert client.calls.count("/edges") == 2

    def test_two_subjects_never_share_a_slot(self, graph):
        class Broker:
            def __init__(self):
                self.user = None

            def current_user(self):
                return self.user

        a, b = type("U", (), {"id": uuid.uuid4()})(), type("U", (), {"id": uuid.uuid4()})()
        client = FakeViewsClient(graph)
        client._principal_broker = Broker()
        svc = _service(client)
        client._principal_broker.user = a
        svc.context()
        client._principal_broker.user = b
        svc.context()
        assert client.calls.count("/edges") == 2
        client._principal_broker.user = a
        svc.context()
        assert client.calls.count("/edges") == 2   # a's slot is still warm

    def test_a_broker_without_a_user_caches_nothing(self, graph):
        client = FakeViewsClient(graph)
        client._principal_broker = type("B", (), {"current_user": lambda self: None})()
        assert subject_key(client) is None

    def test_logout_drops_the_subject(self, graph):
        user = type("U", (), {"id": uuid.uuid4()})()
        client = FakeViewsClient(graph)
        client._principal_broker = type("B", (), {"current_user": lambda self: user})()
        svc = _service(client)
        svc.context()
        svc.forget_subject(str(user.id))
        svc.context()
        assert client.calls.count("/edges") == 2

    def test_a_renewed_certificate_starts_a_new_slot(self, graph, tmp_path):
        cert = tmp_path / "cert.pem"
        cert.write_text("one")
        client = FakeViewsClient(graph)
        client.cert_path = str(cert)
        first = subject_key(client)
        import os
        os.utime(cert, ns=(1, 1))
        assert subject_key(client) != first

    def test_the_scale_guard_walks_per_node(self, client, graph):
        svc = _service(client, edge_limit=10)
        ctx = svc.context()
        assert isinstance(ctx.index, PartialIndex)
        assert "zu gross" in ctx.state.fallback
        comp = graph.by_key["L2-KNOWLEDGE-GRAPH"]
        body = views.ego_graph(ctx, comp)
        assert body["fallback"]
        assert any(c["type"] == "AlloyCheck" and c["count"] == 22 for c in body["chips"])
        assert client.calls.count(f"/nodes/{comp}/neighbors") == 1

    def test_the_rbac_filter_of_the_brain_is_what_the_index_holds(self, graph):
        secret = graph.by_key["GI-GRAPH-01"]
        client = FakeViewsClient(graph, hidden={secret})
        ctx = _service(client).context()
        assert secret not in ctx.index.nodes
        comp = graph.by_key["L2-KNOWLEDGE-GRAPH"]
        assert secret not in [m for _, _, m in ctx.index.neighbours(comp)]


# -- meta hiding ------------------------------------------------------------------


class TestMeta:
    def test_meta_is_off_by_default_and_hides_meta_neighbours(self, client, graph):
        ctx = _service(client).context()
        body = views.ego_graph(ctx, graph.by_key["L2-KNOWLEDGE-GRAPH"])
        names = {n["name"] for n in body["nodes"]}
        assert "Loader run 2026-10-27" not in names        # meta = yes, small type: by fact
        assert "Plan review" in names                       # an Event that is not meta
        assert not any(c["type"] == "GlossaryTerm" for c in body["chips"])
        assert "GlossaryTerm" in body["hidden"]["by_type"]  # 60 > 50: hidden by type
        assert body["hidden"]["meta"] == 61

    def test_meta_on_shows_them_marked(self, client, graph):
        ctx = _service(client).context()
        body = views.ego_graph(ctx, graph.by_key["L2-KNOWLEDGE-GRAPH"], show_meta=True)
        run = next(n for n in body["nodes"] if n["name"] == "Loader run 2026-10-27")
        assert run["meta"] is True
        assert any(c["type"] == "GlossaryTerm" for c in body["chips"])

    def test_a_type_level_marker_wins_and_reads_no_fact(self, client, graph):
        svc = views.ViewService(lambda: client, ViewSettings(), bucket=_NoWait(),
                                markers=lambda: {"Event"})
        ctx = svc.context()
        views.ego_graph(ctx, graph.by_key["L2-KNOWLEDGE-GRAPH"])
        for key in ("EVT-run-1", "EVT-review-1"):
            assert f"/nodes/{graph.by_key[key]}/facts" not in client.calls

    def test_only_types_whose_schema_declares_meta_can_be_meta(self, client, graph):
        ctx = _service(client).context()
        ctx.meta.classify(list(graph.nodes))
        assert "Component" not in ctx.meta.capable
        assert {"Process", "Event", "GlossaryTerm"} <= ctx.meta.capable

    def test_the_threshold_is_configurable(self, client, graph):
        ctx = _service(client, meta_exact_max=100).context()
        ctx.meta.classify(list(graph.nodes))
        assert "GlossaryTerm" not in ctx.meta.hidden_types()
        assert "GlossaryTerm" in ctx.meta.exact

    def test_decisions_are_cached_with_the_index(self, client, graph):
        svc = _service(client)
        comp = graph.by_key["L2-KNOWLEDGE-GRAPH"]
        views.ego_graph(svc.context(), comp)
        before = len(client.calls)
        views.ego_graph(svc.context(), comp)
        assert len(client.calls) == before


# -- fan-out grouping ---------------------------------------------------------------


class TestGrouping:
    def test_at_most_n_same_type_neighbours_are_drawn(self):
        nbs = [("verifies", "in", f"c{i}") for i in range(6)] + [("keeps", "out", "i1")]
        drawn, chips = grouping.group(nbs, lambda n: "AlloyCheck" if n.startswith("c") else "Invariant", 5)
        assert drawn == [("keeps", "out", "i1")]
        assert chips[0]["count"] == 6 and chips[0]["type"] == "AlloyCheck"

    def test_exactly_n_stay_nodes(self):
        nbs = [("verifies", "in", f"c{i}") for i in range(5)]
        drawn, chips = grouping.group(nbs, lambda n: "AlloyCheck", 5)
        assert len(drawn) == 5 and chips == []

    def test_relation_and_direction_split_groups(self):
        nbs = [("verifies", "in", f"c{i}") for i in range(4)] + [("verifies", "out", f"d{i}") for i in range(4)]
        drawn, chips = grouping.group(nbs, lambda n: "AlloyCheck", 5)
        assert len(drawn) == 8 and chips == []

    def test_breakdown_counts_statuses(self):
        assert grouping.breakdown(["passed"] * 20 + ["failed"] * 2) == [
            {"status": "passed", "count": 20}, {"status": "failed", "count": 2}]

    def test_paging(self):
        members, pages = grouping.page([str(i) for i in range(120)], 2, 50)
        assert pages == 3 and members == [str(i) for i in range(100, 120)]

    def test_ego_graph_draws_no_more_than_n_of_a_kind(self, client, graph):
        ctx = _service(client).context()
        body = views.ego_graph(ctx, graph.by_key["L2-KNOWLEDGE-GRAPH"], show_meta=True)
        kinds = {}
        for n in body["nodes"]:
            kinds[(n["relation"], n["direction"], n["type"])] = kinds.get((n["relation"], n["direction"], n["type"]), 0) + 1
        assert max(kinds.values()) <= 5
        chip = next(c for c in body["chips"] if c["type"] == "AlloyCheck")
        assert chip["breakdown"] == [{"status": "passed", "count": 20}, {"status": "failed", "count": 2}]
        assert "members" not in chip

    def test_a_large_chip_reads_no_member_facts_until_opened(self, client, graph):
        ctx = _service(client, chip_status_max=10).context()
        comp = graph.by_key["L2-KNOWLEDGE-GRAPH"]
        body = views.ego_graph(ctx, comp)
        chip = next(c for c in body["chips"] if c["type"] == "AlloyCheck")
        assert chip["breakdown"] is None
        checks = [n for n, x in graph.nodes.items() if x["node_type_id"] == graph.types["AlloyCheck"]]
        assert not any(f"/nodes/{c}/facts" in client.calls for c in checks)
        page = views.group_members(ctx, comp, "verifies", "in", "AlloyCheck", 0)
        assert page["count"] == 22 and page["pages"] == 1
        assert {m["status"] for m in page["members"]} == {"passed", "failed"}

    def test_members_load_a_page_at_a_time(self, client, graph):
        ctx = _service(client, page_size=10).context()
        comp = graph.by_key["L2-KNOWLEDGE-GRAPH"]
        page = views.group_members(ctx, comp, "verifies", "in", "AlloyCheck", 1)
        assert page["pages"] == 3 and len(page["members"]) == 10
        read = [c for c in client.calls if c.endswith("/facts")]
        assert len(read) <= 10 + 2   # the page, plus the meta decisions of small types


# -- dossier --------------------------------------------------------------------------


class TestSchemasAreReadLazily:
    def test_only_the_types_a_view_meets(self, client, graph):
        ctx = _service(client).context()
        views.ego_graph(ctx, graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"])
        read = {c.split("/")[2] for c in client.calls if c.endswith("/schema")}
        assert graph.types["Component"] in read
        assert graph.types["AlloyCheck"] not in read      # not a neighbour of the plan

    def test_a_type_without_a_state_attribute_costs_no_fact_reads(self, client, graph):
        comp = graph.by_key["L2-KNOWLEDGE-GRAPH"]
        for i in range(6):
            graph.edge(comp, "part_of", graph.node("BoundedContext", f"Area {i}"))
        ctx = _service(client).context()
        body = views.ego_graph(ctx, comp)
        chip = next(c for c in body["chips"] if c["type"] == "BoundedContext")
        assert chip["breakdown"] is None and chip["status_attribute"] is None
        areas = [n for n, x in graph.nodes.items() if x["node_type_id"] == graph.types["BoundedContext"]]
        assert not any(f"/nodes/{a}/facts" in client.calls for a in areas)


class TestDossier:
    def test_dossier_holds_what_the_node_holds(self, client, graph):
        ctx = _service(client).context()
        plan = graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"]
        body = views.dossier(ctx, plan)
        node = body["node"]
        assert node["key"] == "FEAT-2026-09-08-adaptive-rate-limits"
        assert node["owner"] == "ROLE-ENGINE-OWNER"
        assert node["status"] == "implementing"
        assert node["last_confirmed"] is None
        relations = {(g["relation"], g["direction"]) for g in body["neighbours"]}
        assert ("affects", "out") in relations
        assert ("instance_of", "out") not in relations   # Process is meta, hidden by default

    def test_neighbour_lists_are_capped(self, client, graph):
        ctx = _service(client, page_size=5).context()
        body = views.dossier(ctx, graph.by_key["L2-KNOWLEDGE-GRAPH"])
        checks = next(g for g in body["neighbours"] if g["type"] == "AlloyCheck")
        assert len(checks["items"]) == 5 and checks["more"] == 17

    def test_unknown_node(self, client):
        ctx = _service(client).context()
        assert views.dossier(ctx, "nope")["success"] is False


# -- routes ------------------------------------------------------------------------------


class _Gate:
    def __init__(self, user):
        self.user = user

    def current_user(self):
        return self.user


def _app(client, *, user=True, graph_mode=True):
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"))
    gate = _Gate(type("U", (), {"id": uuid.uuid4(), "roles": frozenset()})() if user else None)
    app.register_blueprint(create_views_blueprint(gate, _service(client), graph_mode=lambda: graph_mode))
    return app.test_client()


class TestRoutes:
    def test_graph_route(self, client, graph):
        res = _app(client).get(f"/api/views/graph?node={graph.by_key['L2-KNOWLEDGE-GRAPH']}")
        assert res.status_code == 200
        body = res.get_json()
        assert body["as_of"] and "timings_ms" in body and body["legend"]

    def test_signed_out_is_401(self, client, graph):
        assert _app(client, user=False).get("/api/views/graph?node=x").status_code == 401

    def test_fixture_mode_is_409(self, client):
        assert _app(client, graph_mode=False).get("/api/views/graph?node=x").status_code == 409

    def test_group_needs_a_direction(self, client, graph):
        res = _app(client).get(f"/api/views/graph/group?node={graph.by_key['L2-KNOWLEDGE-GRAPH']}"
                               "&relation=verifies&direction=sideways&type=AlloyCheck")
        assert res.status_code == 400

    def test_search_by_name(self, client):
        body = _app(client).get("/api/views/search?q=adaptive").get_json()
        assert [r["name"] for r in body["results"]] == ["Adaptive Rate Limits"]

    def test_search_hides_meta_unless_asked(self, client):
        assert _app(client).get("/api/views/search?q=Term 1").get_json()["results"] == []
        assert _app(client).get("/api/views/search?q=Term 1&meta=1").get_json()["results"]

    def test_no_route_writes(self, client):
        app = _app(client)
        for path in ("/api/views/graph", "/api/views/search", "/api/views/node/x"):
            assert app.post(path).status_code == 405


# -- paced bulk read in the client ---------------------------------------------------


class TestGraphGetMany:
    def _client(self, responses):
        from knovas_client import KnovasAPIClient

        api = KnovasAPIClient.__new__(KnovasAPIClient)
        api.base_url = "https://brain"
        api.http_read_timeout = 5
        api._principal_broker = None
        api.api_key = ""
        api.auth_type = "bearer"
        api._ensure_certificate_freshness = lambda: None
        sent = []

        class Resp:
            def __init__(self, status, body, headers=None):
                self.status_code, self._body, self.headers = status, body, headers or {}

            def json(self):
                return self._body

        class Session:
            def request(self, **kw):
                sent.append(kw)
                status, body, headers = responses.pop(0)
                return Resp(status, body, headers)

        api._session = Session()
        return api, sent

    def test_a_429_waits_and_retries(self, monkeypatch):
        import knovas_client
        monkeypatch.setattr(knovas_client.time, "sleep", lambda s: None)
        api, sent = self._client([(429, {}, {"Retry-After": "1"}), (200, {"facts": []}, None)])
        assert api.graph_get_many([("/nodes/a/facts", None)]) == [(200, {"facts": []})]
        assert len(sent) == 2

    def test_each_call_carries_its_own_assertion(self):
        api, sent = self._client([(200, {}, None), (200, {}, None)])
        minted = iter(["tok-1", "tok-2"])
        api._principal_broker = type("B", (), {"current_user": lambda self: object(),
                                               "assertion_for": lambda self, u: next(minted)})()
        api.graph_get_many([("/a", None), ("/b", None)], workers=1)
        from knovas_client import ASSERTION_FIELD
        assert sorted(kw["json"][ASSERTION_FIELD] for kw in sent) == ["tok-1", "tok-2"]

    def test_no_user_means_no_call(self):
        api, sent = self._client([])
        api._principal_broker = type("B", (), {"current_user": lambda self: None})()
        with pytest.raises(PermissionError):
            api.graph_get_many([("/a", None)])
        assert sent == []

    def test_only_get_is_sent(self):
        api, sent = self._client([(200, {}, None)])
        api.graph_get_many([("/edges", None)])
        assert sent[0]["method"] == "GET"


def test_token_bucket_paces_after_the_burst():
    t = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s

    bucket = TokenBucket(60, 2, clock=lambda: t[0], sleep=sleep)
    for _ in range(3):
        bucket.acquire()
    assert slept and abs(sum(slept) - 1.0) < 1e-6   # third call waits one second at 1/s


# -- vendored files and their pin -------------------------------------------------------


def test_vendored_copies_match_their_pin():
    """A copy edited by hand, or re-vendored without SOURCE.json, fails here."""
    source = json.loads((DEFINITIONS / "SOURCE.json").read_text(encoding="utf-8"))
    assert source["files"], "SOURCE.json lists no file"
    for name, pin in source["files"].items():
        data = (DEFINITIONS / name).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(data).hexdigest() == pin["sha256"], f"{name} diverged from {pin['commit']}"
        assert len(pin["commit"]) == 40


def test_every_vendored_file_is_pinned():
    source = json.loads((DEFINITIONS / "SOURCE.json").read_text(encoding="utf-8"))
    files = {p.name for p in DEFINITIONS.iterdir() if p.is_file() and p.name != "SOURCE.json"}
    assert files == set(source["files"])


def test_settings_come_from_config():
    class Config:
        def get_int(self, key, default):
            return {"views.fanout_threshold": 7, "views.edge_limit": 5}.get(key, default)

        def get_float(self, key, default):
            return {"views.edge_budget_seconds": 1.5}.get(key, default)

    s = ViewSettings.from_config(Config())
    assert (s.fanout_threshold, s.edge_limit, s.edge_budget_seconds) == (7, 5, 1.5)
