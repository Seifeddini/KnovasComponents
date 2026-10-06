from types import SimpleNamespace

from flask import Flask

import graph_directory as gd
from graph_workbench import Workbench
from web_interface.graph_routes import create_graph_blueprint


class Store:
    def active_views(self):
        return []

    def card(self, _type_id):
        return None


class Source:
    def topology(self):
        return {
            "node_types": [
                {"id": "mandate", "name": "Mandat"},
                {"id": "people", "name": "Person"},
            ],
            "nodes": [
                {"id": "m1", "name": "Steiner", "node_type_id": "mandate"},
                {"id": "p1", "name": "Marco Steiner", "node_type_id": "people"},
            ],
            "edges": [
                {"id": "e1", "node_lo": "m1", "node_hi": "p1", "relation": "Mandant"},
            ],
        }

    def invalidate(self):
        pass


class Client:
    def __init__(self):
        self.fact = {
            "id": "f1",
            "attribute_id": "a1",
            "value": {"amount": "22000", "currency": "CHF"},
            "updated_at": "2026-02-03T10:00:00Z",
        }

    def graph_node(self, node_id):
        nodes = {row["id"]: row for row in Source().topology()["nodes"]}
        node = nodes.get(node_id)
        return None if node is None else {
            "node": node,
            "facts": [self.fact] if node_id == "m1" else [],
            "assignments": [{"pointer": "akte/klage.pdf", "title": "Klage", "page": 2}],
        }

    def graph_facts(self, node_id):
        return [self.fact] if node_id == "m1" else []

    def graph_schema(self, type_id, include_deprecated=False):
        if type_id != "mandate":
            return []
        return [{
            "id": "a1",
            "name": "Streitwert",
            "datatype": "money",
            "required": True,
            "sort_order": 10,
        }]

    def graph_neighbors(self, node_id, depth=1, include_edges=False):
        assert include_edges is True
        return {
            "neighbors": [
                {"id": "p1", "name": "Marco Steiner", "node_type_id": "people", "depth": 1},
            ],
            "edges": Source().topology()["edges"],
        }

    def graph_fact_history(self, fact_id):
        return [{
            "id": "h1",
            "created_at": "2026-02-03T10:00:00Z",
            "action": "changed",
            "old_value": {"amount": "20000", "currency": "CHF"},
            "new_value": {"amount": "22000", "currency": "CHF"},
        }]

    def graph_update_node(self, node_id, **fields):
        return {"node": {"id": node_id, **fields}}

    def graph_update_fact(self, fact_id, **fields):
        self.fact.update(fields)
        return {"fact": dict(self.fact)}

    def graph_create_fact(self, node_id, value, attribute_id=None, label=None):
        self.fact = {"id": "f2", "attribute_id": attribute_id, "value": value}
        return {"fact": dict(self.fact)}

    def graph_delete_fact(self, fact_id):
        return {"success": True}


def test_directory_projection_formats_money_and_documents():
    workbench = Workbench(Client(), Source(), Store())
    payload = workbench.node_page("m1")
    assert payload["fields"][0]["display"] == "22’000.00 CHF"
    assert payload["documents"] == [{
        "pointer": "akte/klage.pdf",
        "title": "Klage",
        "page": 2,
        "quote": "",
    }]


def test_views_are_inferred_from_graph_types_and_paginated():
    workbench = Workbench(Client(), Source(), Store())
    assert [view["title"] for view in workbench.views()] == ["Mandate", "Personen"]
    page = workbench.nodes_for_view("mandat", limit=1)
    assert page["nodes"][0]["id"] == "m1"
    assert page["total"] == 1


def test_network_contains_real_visible_edge_only():
    payload = Workbench(Client(), Source(), Store()).network("m1", 2)
    assert payload["depth"] == 2
    assert {node["id"] for node in payload["nodes"]} == {"m1", "p1"}
    assert payload["edges"][0]["label"] == "Mandant"


def test_history_is_selection_scoped_and_newest_first():
    payload = Workbench(Client(), Source(), Store()).history("m1")
    assert payload["available"] is True
    assert payload["events"][0]["field"] == "Streitwert"


class Gate:
    user = SimpleNamespace(id="u1", roles=frozenset({"admin"}))

    def current_user(self):
        return self.user


class Grants:
    def may_write(self, _node_id, _user):
        return True


def test_graph_routes_expose_views_and_contexts():
    client = Client()
    bp = create_graph_blueprint(
        Gate(),
        lambda: Grants(),
        lambda: client,
        graph_mode=lambda: True,
        topology=lambda: Source(),
        directories=lambda: Store(),
    )
    app = Flask(__name__)
    app.register_blueprint(bp)
    browser = app.test_client()

    views = browser.get("/api/graph/views")
    assert views.status_code == 200
    assert len(views.get_json()["views"]) == 2

    detail = browser.get("/api/graph/nodes/m1")
    assert detail.status_code == 200
    assert detail.get_json()["node"]["name"] == "Steiner"

    network = browser.get("/api/graph/nodes/m1/network?depth=2")
    assert network.status_code == 200
    assert network.get_json()["depth"] == 2

    history = browser.get("/api/graph/nodes/m1/history")
    assert history.status_code == 200
    assert history.get_json()["events"][0]["field"] == "Streitwert"


def test_field_projection_hides_unreadable_entity_reference():
    attribute = {"id": "owner", "name": "Person", "datatype": "entity_ref"}
    field = gd.field_cell(
        attribute,
        {"id": "f", "value": {"node_id": "hidden"}},
        {},
    )
    assert field["display"] == gd.HIDDEN_REF
    assert field["ref"] is None
