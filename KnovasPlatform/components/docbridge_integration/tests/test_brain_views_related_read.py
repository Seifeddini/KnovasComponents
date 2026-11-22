"""PMVP-30b: the read of a thing's related things (``brain_views.related``), without the web app.

One ``graph_neighbors(node, depth=1, include_edges=True)`` and one round of kind reads, both on the client handed in;
the starting thing, rows not one hop away and rows without a usable id are left out; kinds by count with their
plurals; at most 20 named kinds, the rest «Weitere Einträge»; relations from the edges between the start and the row;
the failures. And the lists' helpers: sort, filter, the sort key from the address.
"""
from __future__ import annotations

import pytest

from brain_views import related
from brain_views.related import (MAX_NAMED_KINDS, OTHER_KIND, OTHER_KIND_NAME, SORT_NAME, SORT_NAME_DESC,
                                 SORT_RELATION, Unavailable, arrange, filter_text, matches, plural, read_related,
                                 sort_choice, sort_key)
from knovas_client import GraphError, KnowledgeGraphDisabled

START = "6f1c2c1e-0000-4000-8000-0000000b0001"


def _id(n):
    return f"6f1c2c1e-0000-4000-8000-{n:012d}"


def _kind(n):
    return f"7a2b0000-0000-4000-8000-{n:012d}"


class Client:
    """The two reads read_related makes, recorded; nothing else answers."""

    def __init__(self, neighbours=(), edges=(), names=None, error=None):
        self.neighbours, self.edges, self.names, self.error = list(neighbours), list(edges), dict(names or {}), error
        self.calls = []

    def graph_neighbors(self, node_id, depth=1, include_edges=False):
        self.calls.append(("graph_neighbors", node_id, depth, include_edges))
        if self.error:
            raise self.error
        return {"neighbors": list(self.neighbours), "edges": list(self.edges) if include_edges else []}

    def graph_get_many(self, calls, *, workers=4, acquire=None, max_retries=3):
        self.calls.append(("graph_get_many", tuple(path for path, _ in calls)))
        out = []
        for path, _params in calls:
            type_id = path.rsplit("/", 1)[-1]
            if type_id in self.names:
                out.append((200, {"status": "success", "node_type": {"id": type_id, "name": self.names[type_id]}}))
            else:
                out.append((404, {"status": "error"}))
        return out

    def __getattr__(self, name):
        raise AssertionError(f"read_related made another read: {name}")


def _row(n, kind=None, name=None, hop=1):
    return {"id": _id(n), "name": name if name is not None else f"Eintrag {n}", "node_type_id": kind, "hop": hop}


def _edge(a, b, relation):
    lo, hi = sorted((a, b))
    return {"id": f"e-{a}-{b}-{relation}", "node_lo": lo, "node_hi": hi, "relation": relation}


def test_one_neighbours_read_and_one_round_of_kind_reads():
    client = Client([_row(1, _kind(1)), _row(2, _kind(2)), _row(3, _kind(1))],
                    names={_kind(1): "Mandat", _kind(2): "Vertrag"})
    found = read_related(client, START)
    assert client.calls[0] == ("graph_neighbors", START, 1, True)
    assert [c[0] for c in client.calls] == ["graph_neighbors", "graph_get_many"]
    assert sorted(client.calls[1][1]) == sorted([f"/node-types/{_kind(1)}", f"/node-types/{_kind(2)}"])
    assert [(k.name, k.count) for k in found.kinds] == [("Mandate", 2), ("Verträge", 1)]
    assert found.total == 3


def test_the_start_rows_not_one_hop_away_and_unusable_ids_are_left_out():
    rows = [{"id": START, "name": "Ich", "node_type_id": _kind(1), "hop": 0},
            {"id": START.upper(), "name": "Ich", "node_type_id": _kind(1), "hop": 1},
            _row(1, _kind(1)), _row(2, _kind(1), hop=2), _row(3, _kind(1), hop=True),
            {"id": "../x", "name": "Weg", "node_type_id": _kind(1), "hop": 1},
            {"name": "ohne Id", "node_type_id": _kind(1), "hop": 1},
            _row(1, _kind(1)),                     # the same thing twice
            "kein Eintrag"]
    found = read_related(Client(rows, names={_kind(1): "Mandat"}), START)
    assert [t.id for k in found.kinds for t in k.things] == [_id(1)]


def test_kinds_by_count_then_by_name_and_the_others_last():
    rows = ([_row(n, _kind(1)) for n in range(2)] + [_row(10 + n, _kind(2)) for n in range(3)]
            + [_row(20 + n, _kind(3)) for n in range(2)] + [_row(30 + n, None) for n in range(5)])
    found = read_related(Client(rows, names={_kind(1): "Vertrag", _kind(2): "Sonderfall", _kind(3): "Mandat"}),
                         START)
    assert [(k.name, k.count) for k in found.kinds] == [("Sonderfall", 3), ("Mandate", 2), ("Verträge", 2),
                                                         (OTHER_KIND_NAME, 5)]
    assert found.kinds[-1].key == OTHER_KIND
    assert found.kinds[0].key == _kind(2)


def test_a_kind_in_the_table_in_its_plural_and_one_outside_with_its_own_name():
    assert plural("Mandat") == "Mandate" and plural("  matter ") == "Mandate"
    assert plural("FeaturePlan") == "Feature-Pläne" and plural("ProcessStep") == "Prozessschritte"
    assert plural("Sonderfall") == "Sonderfall"


def test_at_most_twenty_kinds_are_named_and_the_rest_fall_under_the_others():
    kinds = [_kind(100 + n) for n in range(MAX_NAMED_KINDS + 3)]
    rows = []
    for n, kind in enumerate(kinds):
        rows += [_row(1000 * (n + 1) + m, kind) for m in range(50 - n)]   # each kind fewer than the one before
    client = Client(rows, names={k: f"Art {i:02d}" for i, k in enumerate(kinds)})
    found = read_related(client, START)
    assert len(client.calls[1][1]) == MAX_NAMED_KINDS
    named = [k for k in found.kinds if k.key != OTHER_KIND]
    assert [k.key for k in named] == kinds[:MAX_NAMED_KINDS]
    other = found.kind(OTHER_KIND)
    assert other.name == OTHER_KIND_NAME and other.count == sum(50 - n for n in range(MAX_NAMED_KINDS, len(kinds)))
    assert found.total == len(rows)


def test_a_kind_whose_name_cannot_be_read_falls_under_the_others():
    found = read_related(Client([_row(1, _kind(1)), _row(2, _kind(2))], names={_kind(1): "Mandat"}), START)
    assert [(k.key, k.count) for k in found.kinds] == [(_kind(1), 1), (OTHER_KIND, 1)]


@pytest.mark.parametrize("type_id", ["euo:Person", "../node-types", "x" * 36, 7])
def test_a_kind_that_is_not_a_uuid_is_not_read_and_falls_under_the_others(type_id):
    client = Client([_row(1, type_id)])
    found = read_related(client, START)
    assert [c[0] for c in client.calls] == ["graph_neighbors"]
    assert [(k.key, k.count) for k in found.kinds] == [(OTHER_KIND, 1)]


def test_relations_come_from_the_edges_between_the_start_and_the_thing():
    rows = [_row(1, _kind(1)), _row(2, _kind(1)), _row(3, _kind(1))]
    edges = [_edge(START, _id(1), "Vermieterin"), _edge(_id(1), START, "Partei"), _edge(START, _id(1), "Partei"),
             _edge(_id(2), _id(3), "Mieterin"),                     # between two neighbours: not the start's
             _edge(START, _id(3), "  "), {"node_lo": START, "node_hi": None, "relation": "kaputt"}]
    found = read_related(Client(rows, edges, names={_kind(1): "Mandat"}), START)
    relations = {t.id: t.relation for t in found.kinds[0].things}
    assert relations == {_id(1): "Partei, Vermieterin", _id(2): None, _id(3): None}


def test_without_edges_the_rows_have_no_relation():
    found = read_related(Client([_row(1, _kind(1))], names={_kind(1): "Mandat"}), START)
    assert found.kinds[0].things[0].relation is None


def test_a_graph_switched_off_and_a_hidden_thing_are_nothing_related():
    assert read_related(Client(error=KnowledgeGraphDisabled("aus")), START).kinds == ()
    empty = Client()                                       # the client's answer to a 404: no neighbours, no edges
    assert read_related(empty, START).total == 0
    assert [c[0] for c in empty.calls] == ["graph_neighbors"]


@pytest.mark.parametrize("error", [GraphError(500, "internal"), ConnectionError("weg"), RuntimeError("x")])
def test_any_other_failure_is_unavailable_with_the_error_type_only(error):
    with pytest.raises(Unavailable) as raised:
        read_related(Client(error=error), START)
    assert str(raised.value) == type(error).__name__


def test_a_malformed_start_is_not_sent():
    client = Client([_row(1, _kind(1))])
    assert read_related(client, "../x").total == 0
    assert client.calls == []


def test_a_thing_without_a_name_is_called_so():
    found = read_related(Client([_row(1, _kind(1), name="   ")], names={_kind(1): "Mandat"}), START)
    assert found.kinds[0].things[0].name == "Ohne Namen"


def test_a_failed_round_of_kind_reads_leaves_every_kind_unnamed():
    class Broken(Client):
        def graph_get_many(self, calls, **kwargs):
            raise RuntimeError("weg")

    found = read_related(Broken([_row(1, _kind(1)), _row(2, _kind(2))]), START)
    assert [(k.key, k.count) for k in found.kinds] == [(OTHER_KIND, 2)]


# --- the lists' helpers ------------------------------------------------------------------------------------------


def test_the_sort_key_ignores_case_accents_and_spacing():
    names = ["Zürich", "ärzte", "Arzt", "  Bern ", "Straße"]
    assert sorted(names, key=sort_key) == ["Arzt", "ärzte", "  Bern ", "Straße", "Zürich"]


def test_the_filter_is_cleaned_and_cut_at_100_characters():
    assert filter_text("  Seefeld   strasse ") == "Seefeld strasse"
    assert filter_text("x" * 150) == "x" * 100
    assert filter_text("a\x00b​c") == "abc"
    assert filter_text(None) == "" and filter_text(["x"]) == ""


def test_the_filter_ignores_case_and_spacing():
    assert matches("Mietvertrag  Seefeldstrasse 48", "seefeldstrasse  48")
    assert not matches("Mietvertrag Hardturm", "seefeld")
    assert matches("anything", "")


def test_only_a_known_sort_is_taken_from_the_address():
    assert sort_choice("name-ab", relations=False) == SORT_NAME_DESC
    assert sort_choice("beziehung", relations=True) == SORT_RELATION
    assert sort_choice("beziehung", relations=False) == SORT_NAME
    assert sort_choice("<script>", relations=True) == SORT_NAME
    assert sort_choice(None, relations=True) == SORT_NAME


def test_arrange_sorts_and_filters():
    things = [related.Thing(_id(n), name, relation) for n, (name, relation) in
              enumerate([("Cäsar", "Partei"), ("anna", None), ("Bruno", "Mieterin"), ("Bea", "Partei")])]
    name, relation = (lambda t: t.name), (lambda t: t.relation)
    assert [t.name for t in arrange(things, name=name, needle="", sort=SORT_NAME)] == ["anna", "Bea", "Bruno", "Cäsar"]
    assert [t.name for t in arrange(things, name=name, needle="", sort=SORT_NAME_DESC)] == ["Cäsar", "Bruno", "Bea",
                                                                                              "anna"]
    assert [t.name for t in arrange(things, name=name, needle="b", sort=SORT_NAME)] == ["Bea", "Bruno"]
    assert [t.name for t in arrange(things, name=name, needle="", sort=SORT_RELATION, relation=relation)] == [
        "Bruno", "Bea", "Cäsar", "anna"]
