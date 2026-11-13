"""Task 6 (SS-627): facts in batches over POST /secured/graph/facts/batch."""
from __future__ import annotations

import pytest
import requests

from graph_views.reader import GraphReader
from identity.broker_key import load_or_create_signer
from knovas_client import ASSERTION_FIELD, KnovasAPIClient
from test_knovas_client_assertion import TENANT, _StubBroker, _StubConfig, _User
from views_fixture import FakeViewsClient, FixtureGraph


def _graph(n=3):
    g = FixtureGraph()
    ids = [g.node("Thing", f"n{i}", key=f"K{i}", color=f"c{i}") for i in range(n)]
    return g, ids


# ---- the reader against the fake ----------------------------------------

def test_batch_answers_the_same_facts_as_facts_many():
    g, ids = _graph()
    r = GraphReader(FakeViewsClient(g))
    assert r.facts_batch(ids) == r.facts_many(ids)
    assert r.batch_unavailable is False


def test_a_hidden_or_unknown_id_is_none_not_empty():
    g, ids = _graph()
    r = GraphReader(FakeViewsClient(g, hidden={ids[1]}))
    out = r.facts_batch([ids[0], ids[1], "nope"])
    assert out[ids[0]] and out[ids[1]] is None and out["nope"] is None


def test_501_ids_make_two_calls_and_the_body_names_the_ids():
    g, ids = _graph(1)
    fake = FakeViewsClient(g)
    many = [f"x{i}" for i in range(500)] + [ids[0]]
    out = GraphReader(fake).facts_batch(many)
    assert [len(b["node_ids"]) for b in fake.batch_bodies] == [500, 1]
    assert out[ids[0]] and len(out) == 501


def test_without_the_route_it_falls_back_and_says_so():
    g, ids = _graph()
    fake = FakeViewsClient(g, batch=False)
    r = GraphReader(fake)
    assert r.facts_batch(ids) == GraphReader(FakeViewsClient(g)).facts_many(ids)
    assert r.batch_unavailable is True
    assert "/nodes/%s/facts" % ids[0] in fake.calls


def test_a_503_gives_none_for_the_whole_chunk():
    g, ids = _graph()
    fake = FakeViewsClient(g)
    fake.graph_facts_batch = lambda node_ids: (503, None)
    out = GraphReader(fake).facts_batch(ids)
    assert out == {i: None for i in ids}


def test_a_top_level_facts_by_node_is_accepted():
    g, ids = _graph(1)
    fake = FakeViewsClient(g)
    fake.graph_facts_batch = lambda node_ids: (200, {"facts_by_node": {ids[0]: g.facts[ids[0]]}})
    assert GraphReader(fake).facts_batch(ids)[ids[0]] == g.facts[ids[0]]


# ---- the client, no network ---------------------------------------------

@pytest.fixture
def client(tmp_path):
    return KnovasAPIClient(config_loader=_StubConfig({
        "api.base_url": "http://example.test", "api.customer_id": TENANT}))


class _Resp:
    status_code = 200

    def json(self):
        return {"success": True, "data": {"facts_by_node": {}}}


def test_client_posts_the_ids_with_the_principal(client, tmp_path, monkeypatch):
    (tmp_path / "b").mkdir()
    client.attach_principal_broker(_StubBroker(load_or_create_signer(tmp_path / "b"), _User()))
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return _Resp()

    monkeypatch.setattr(client._session, "request", fake)
    monkeypatch.setattr(client, "_ensure_certificate_freshness", lambda: None)
    status, payload = client.graph_facts_batch(["a", "b"])
    assert status == 200 and payload["success"] is True
    assert seen["method"] == "POST"
    assert seen["url"] == "http://example.test/secured/graph/facts/batch"
    assert seen["json"]["node_ids"] == ["a", "b"] and seen["json"][ASSERTION_FIELD]
    assert seen["allow_redirects"] is False and seen["timeout"] == client.http_read_timeout


def test_client_maps_transport_errors_to_599(client, monkeypatch):
    def boom(**kw):
        raise requests.exceptions.ConnectionError("x")

    monkeypatch.setattr(client._session, "request", boom)
    monkeypatch.setattr(client, "_ensure_certificate_freshness", lambda: None)
    assert client.graph_facts_batch(["a"]) == (599, None)


def test_client_refuses_more_than_500_ids(client):
    with pytest.raises(ValueError):
        client.graph_facts_batch([str(i) for i in range(501)])
