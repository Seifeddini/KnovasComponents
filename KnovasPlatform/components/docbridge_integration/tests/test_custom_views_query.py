import itertools
from types import MappingProxyType

import pytest

from src.custom_views.manifest import Manifest
from src.custom_views.query import Forbidden, LinkSpec, Query, QueryError, authorize, parse_query

MESSAGE = "Diese Ansicht darf das nicht lesen."


def _manifest():
    return Manifest(
        id="v", title={"de": "V"}, description=None, order=None, roles=("*",), entry="index.html",
        scripts=(), styles=(), reads=MappingProxyType({"A": ("f1", "f2")}), link_hosts=(),
    )


@pytest.mark.parametrize(
    "type_,facts,where",
    list(itertools.product(["A", "B"], [("f1",), ("zz",)], [None, "f2", "zz"])),
)
def test_authorize_every_combination(type_, facts, where):
    body = {"type": type_, "facts": list(facts)}
    if where:
        body["where"] = {where: "x"}
    query = parse_query(body, max_nodes=200)
    outside = type_ != "A" or facts == ("zz",) or where == "zz"
    if outside:
        with pytest.raises(Forbidden) as exc:
            authorize(query, _manifest())
        assert str(exc.value) == MESSAGE
    else:
        authorize(query, _manifest())


def test_parse_full():
    q = parse_query(
        {
            "type": "A", "ids": ["1"], "linked": {"to": ["2", "3"], "relation": "r", "direction": "in"},
            "where": {"f1": "x", "f2": ["a", "b"]}, "facts": ["f1"],
            "links": [{"relation": "r", "direction": "out"}], "limit": 5,
        },
        max_nodes=200,
    )
    assert q == Query("A", ("1",), ("2", "3"), LinkSpec("r", "in"), {"f1": ("x",), "f2": ("a", "b")},
                      ("f1",), (LinkSpec("r", "out"),), 5)


def test_defaults():
    q = parse_query({"type": "A"}, max_nodes=200)
    assert (q.ids, q.linked_to, q.linked, dict(q.where), q.facts, q.links, q.limit) == (
        None, None, None, {}, (), (), 200)


@pytest.mark.parametrize(
    "body",
    [
        [], "x", None, {}, {"type": ""}, {"type": 3}, {"type": "A", "bogus": 1},
        {"type": "A", "ids": [str(i) for i in range(201)]},
        {"type": "A", "ids": "1"}, {"type": "A", "ids": [1]},
        {"type": "A", "limit": 0}, {"type": "A", "limit": 201}, {"type": "A", "limit": True},
        {"type": "A", "limit": "5"},
        {"type": "A", "linked": {"to": ["1"], "relation": "r", "direction": "both"}},
        {"type": "A", "linked": {"to": [], "relation": "r", "direction": "out"}},
        {"type": "A", "linked": {"to": ["1"], "relation": "r"}},
        {"type": "A", "linked": {"to": ["1"], "relation": "r", "direction": "out", "x": 1}},
        {"type": "A", "where": {"f": [str(i) for i in range(21)]}},
        {"type": "A", "where": {"f": 1}}, {"type": "A", "where": []},
        {"type": "A", "facts": [str(i) for i in range(41)]},
        {"type": "A", "links": [{"relation": "r", "direction": "out"}] * 11},
        {"type": "A", "links": [{"relation": "r", "direction": "x"}]},
        {"type": "A", "type2": 1}, {"type": "x" * 201},
    ],
)
def test_parse_refusals(body):
    with pytest.raises(QueryError) as exc:
        parse_query(body, max_nodes=200)
    assert "bogus" not in str(exc.value)


def test_limit_bounds_use_max_nodes():
    assert parse_query({"type": "A", "limit": 7}, max_nodes=7).limit == 7
    with pytest.raises(QueryError):
        parse_query({"type": "A", "limit": 8}, max_nodes=7)
