"""The knowledge screens end to end: routes, pages, and the rules behind them.

The real Flask app in identity mode with ONTOLOGY_SOURCE=graph, the in-memory
FakeGraphApi, real users and the real platform DB. Every authorisation is
asserted on the route, not on whether a button is drawn.
"""
import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


def _seed(fake):
    """Two types, a matter referencing a person, and the edge the reference makes."""
    fake.node_types = [{"id": "t1", "name": "Mandat", "created_at": "2025-01-01"},
                       {"id": "t2", "name": "Person", "created_at": "2025-01-02"}]
    fake.schema = {
        "t1": [{"id": "a1", "name": "Aktenzeichen", "datatype": "text", "required": True,
                "sort_order": 10},
               {"id": "a2", "name": "Status", "datatype": "enum", "required": True,
                "sort_order": 20, "enum_values": ["offen", "ruhend", "abgeschlossen"]},
               {"id": "a3", "name": "Mandant", "datatype": "entity_ref", "sort_order": 30,
                "target_node_type_id": "t2"},
               {"id": "a4", "name": "Streitwert", "datatype": "money", "sort_order": 40},
               {"id": "a5", "name": "Eröffnet", "datatype": "date", "sort_order": 50}],
        "t2": [{"id": "b1", "name": "Ort", "datatype": "text", "sort_order": 10}],
    }
    fake.nodes = {
        "n1": {"id": "n1", "name": "Bühlmann ./. Regazzoni", "node_type_id": "t1",
               "created_at": "2026-01-14T09:00:00"},
        "q1": {"id": "q1", "name": "Elsbeth Bühlmann", "node_type_id": "t2"},
        "q2": {"id": "q2", "name": "Bezirksgericht Luzern", "node_type_id": "t2"},
    }
    fake.facts = {"n1": [], "q1": [], "q2": []}
    fake.graph_create_fact("n1", "2026-0114", attribute_id="a1")
    fake.graph_create_fact("n1", {"node_id": "q1"}, attribute_id="a3")
    fake.graph_create_fact("n1", {"amount": "84500", "currency": "CHF"}, attribute_id="a4")
    fake.edges.append({"id": "e-m", "node_lo": "q1", "node_hi": "q2",
                       "relation": "gleiche Verwaltung", "edge_source": "manual"})
    return fake


@pytest.fixture
def seeded(fake_graph):
    return _seed(fake_graph)


@pytest.fixture
def directory(seeded, admin_client):
    body = admin_client.post("/api/graph/views", json={
        "node_type_id": "t1", "title": "Mandate", "columns": ["a1", "a3", "a4", "a2", "zz"]})
    assert body.status_code == 201, body.get_json()
    return body.get_json()["view"]


def _alices_matter(fake, grants, alice):
    node = fake.graph_create_node("Alices Akte", node_type_id="t1")["node"]
    grants.set_owner(node["id"], alice.id)
    return node["id"]


class TestTypesAndFields:
    def test_members_read_types_with_a_tone_each(self, member_client, seeded):
        types = member_client.get("/api/graph/node-types").get_json()["node_types"]
        assert [(t["name"], t["tone"]) for t in types] == [("Mandat", "t1"), ("Person", "t2")]

    def test_only_admins_define_types_and_names_are_unique(self, member_client, admin_client,
                                                           seeded):
        assert member_client.post("/api/graph/node-types", json={"name": "Frist"}).status_code == 403
        assert admin_client.post("/api/graph/node-types", json={"name": "Frist"}).status_code == 201
        assert admin_client.post("/api/graph/node-types", json={"name": "mandat"}).status_code == 409

    @pytest.mark.parametrize("body, status", [
        ({"name": "X", "datatype": "timestamp"}, 400),
        ({"name": "Art", "datatype": "enum", "enum_values": ["", " "]}, 400),
        ({"name": "Status", "datatype": "text"}, 409),
        ({"name": "Gericht", "datatype": "text", "target_node_type_id": "t2"}, 400),
        ({"name": "Gericht", "datatype": "entity_ref", "target_node_type_id": "t9"}, 400),
    ])
    def test_field_creation_is_validated(self, admin_client, seeded, body, status):
        assert admin_client.post("/api/graph/node-types/t1/schema", json=body).status_code == status

    def test_a_new_field_goes_to_the_end_with_its_target_type(self, admin_client, seeded):
        response = admin_client.post("/api/graph/node-types/t1/schema", json={
            "name": "Gericht", "datatype": "entity_ref", "target_node_type_id": "t2"})
        assert response.status_code == 201
        assert seeded.last_attribute["sort_order"] == 60
        assert seeded.last_attribute["target_node_type_id"] == "t2"

    def test_retiring_a_field_says_deprecated_and_drops_its_column(self, admin_client, directory,
                                                                    seeded):
        body = admin_client.delete("/api/graph/node-types/t1/schema/a4").get_json()
        assert body["deprecated"] is True
        assert seeded.deprecated == [("t1", "a4")]
        views = admin_client.get("/api/graph/admin/directories").get_json()["views"]
        assert "a4" not in views[0]["columns"]

    def test_a_retired_field_can_be_taken_back(self, admin_client, seeded):
        admin_client.delete("/api/graph/node-types/t1/schema/a5")
        response = admin_client.patch("/api/graph/node-types/t1/schema/a5",
                                      json={"deprecated": False})
        assert response.status_code == 200
        assert seeded.schema["t1"][-1]["deprecated_at"] is None

    def test_moving_a_field_writes_once_when_there_is_room(self, admin_client, seeded):
        body = admin_client.post("/api/graph/node-types/t1/schema/a5/move",
                                 json={"before": "a2"}).get_json()
        assert body["order"] == ["a1", "a5", "a2", "a3", "a4"]
        assert body["writes"] == 1
        assert seeded._attribute("t1", "a5")["sort_order"] == 15

    def test_moving_without_room_renumbers_in_tens(self, admin_client, seeded):
        for order, attribute in zip((10, 11, 12, 13, 14), seeded.schema["t1"]):
            attribute["sort_order"] = order
        body = admin_client.post("/api/graph/node-types/t1/schema/a5/move",
                                 json={"before": "a2"}).get_json()
        assert body["order"] == ["a1", "a5", "a2", "a3", "a4"]
        assert body["writes"] == 4          # a1 already stands at 10
        assert {a["id"]: a["sort_order"] for a in seeded.schema["t1"]} == {
            "a1": 10, "a5": 20, "a2": 30, "a3": 40, "a4": 50}


class TestEntries:
    def test_creating_an_entry_writes_its_fields_and_its_owner(self, alice_client, grants, seeded,
                                                               alice):
        response = alice_client.post("/api/graph/nodes", json={
            "name": "Kündigung Steiner", "node_type_id": "t1",
            "facts": {"a1": "2026-0031", "a2": "offen", "a5": {"value": "04.03.2026"},
                      "a4": {"amount": "", "currency": "CHF"}}})
        body = response.get_json()
        # The malformed date is reported; the required-but-empty fields are
        # not: a schema makes absence visible, it never blocks the save.
        assert response.status_code == 201
        assert len(body["problems"]) == 1 and "Eröffnet" in body["problems"][0]
        assert grants.for_node(body["node"]["id"])["owner"] == str(alice.id)
        written = {f["attribute_id"]: f["value"] for f in seeded.facts[body["node"]["id"]]}
        assert written == {"a1": "2026-0031", "a2": "offen"}

    def test_the_card_payload_joins_fields_layout_network_and_rights(self, member_client, seeded):
        body = member_client.get("/api/graph/nodes/n1").get_json()
        assert body["type"]["name"] == "Mandat"
        assert body["fields"]["a4"]["display"] == "84’500.00 CHF"
        assert body["fields"]["a3"]["ref"] == {"id": "q1", "name": "Elsbeth Bühlmann"}
        assert body["fields"]["a2"]["missing"] is True
        assert body["gaps"] == ["a2"]
        assert body["layout"]["loose"] == ["a1", "a2", "a3", "a4", "a5"]
        assert [c["label"] for c in body["connections"]] == ["Mandant"]
        assert body["may_write"] is False and body["may_grant"] is False

    def test_an_unknown_entry_is_404(self, member_client, seeded):
        assert member_client.get("/api/graph/nodes/nope").status_code == 404

    def test_the_list_is_not_narrowed_by_grants(self, member_client, seeded, grants, alice):
        node_id = _alices_matter(seeded, grants, alice)
        names = [n["id"] for n in member_client.get("/api/graph/nodes?type=t1").get_json()["nodes"]]
        assert node_id in names and "q1" not in names

    def test_only_the_name_of_an_entry_can_be_patched(self, alice_client, seeded, grants, alice):
        node_id = _alices_matter(seeded, grants, alice)
        response = alice_client.patch(f"/api/graph/nodes/{node_id}",
                                      json={"name": "Neu", "required_groups": ["g-all"]})
        assert response.status_code == 200
        assert "required_groups" not in seeded.nodes[node_id]


class TestFieldWrites:
    def test_the_owner_sets_changes_and_clears_a_field(self, alice_client, seeded, grants, alice):
        node_id = _alices_matter(seeded, grants, alice)
        url = f"/api/graph/nodes/{node_id}/fields/a5"
        set_ = alice_client.put(url, json={"value": {"value": "2026-03-01", "precision": "month"}})
        assert set_.get_json()["field"]["display"] == "März 2026"
        alice_client.put(url, json={"value": {"value": "2026-03-04", "precision": "day"}})
        assert [f["value"]["value"] for f in seeded.facts[node_id]] == ["2026-03-04"]
        cleared = alice_client.put(url, json={"value": None}).get_json()
        assert cleared["field"]["missing"] is True and seeded.facts[node_id] == []

    def test_a_reference_materialises_an_edge(self, alice_client, seeded, grants, alice):
        node_id = _alices_matter(seeded, grants, alice)
        alice_client.put(f"/api/graph/nodes/{node_id}/fields/a3", json={"value": {"node_id": "q2"}})
        network = alice_client.get(f"/api/graph/nodes/{node_id}/network").get_json()
        assert {(e["from"], e["to"], e["label"]) for e in network["edges"]} == {
            (node_id, "q2", "Mandant")}

    def test_a_malformed_value_is_400_with_the_codec_message(self, alice_client, seeded, grants,
                                                             alice):
        node_id = _alices_matter(seeded, grants, alice)
        response = alice_client.put(f"/api/graph/nodes/{node_id}/fields/a2",
                                    json={"value": "schwebend"})
        assert response.status_code == 400
        assert "nicht zugelassen" in response.get_json()["error"]

    def test_someone_else_s_entry_is_refused(self, alice_client, member_client, seeded, grants,
                                             alice):
        _alices_matter(seeded, grants, alice)
        assert alice_client.put("/api/graph/nodes/n1/fields/a1", json={"value": "x"}).status_code == 403
        assert member_client.put("/api/graph/nodes/n1/fields/a1", json={"value": "x"}).status_code == 403

    def test_an_admin_may_repair_an_entry_nobody_owns(self, admin_client, seeded):
        response = admin_client.put("/api/graph/nodes/n1/fields/a2", json={"value": "offen"})
        assert response.status_code == 200 and response.get_json()["gaps"] == []


class TestEditors:
    def test_the_owner_grants_and_revokes_an_editor(self, alice_client, seeded, grants, alice, bob):
        node_id = _alices_matter(seeded, grants, alice)
        granted = alice_client.post(f"/api/graph/nodes/{node_id}/grants",
                                    json={"user_id": str(bob.id)})
        assert granted.status_code == 201
        assert [e["display_name"] for e in granted.get_json()["editors"]] == ["Bob"]
        assert alice_client.delete(f"/api/graph/nodes/{node_id}/grants/{bob.id}").status_code == 200
        assert grants.for_node(node_id)["editors"] == []

    def test_an_editor_edits_but_does_not_delegate(self, bob_client, seeded, grants, alice, bob,
                                                   carol):
        node_id = _alices_matter(seeded, grants, alice)
        grants.grant_editor(node_id, bob.id)
        assert bob_client.put(f"/api/graph/nodes/{node_id}/fields/a1",
                              json={"value": "1"}).status_code == 200
        assert bob_client.post(f"/api/graph/nodes/{node_id}/grants",
                               json={"user_id": str(carol.id)}).status_code == 403

    def test_the_owner_cannot_be_revoked(self, alice_client, seeded, grants, alice):
        node_id = _alices_matter(seeded, grants, alice)
        assert alice_client.delete(f"/api/graph/nodes/{node_id}/grants/{alice.id}").status_code == 409

    def test_people_are_found_by_name(self, alice_client, seeded, bob):
        people = alice_client.get("/api/graph/people?q=bob").get_json()["people"]
        assert [p["display_name"] for p in people] == ["Bob"]


class TestDirectories:
    def test_members_read_rows_with_the_chosen_columns(self, member_client, directory, seeded):
        body = member_client.get("/api/graph/directories/mandate").get_json()
        assert [c["name"] for c in body["columns"]] == ["Aktenzeichen", "Mandant", "Streitwert",
                                                        "Status"]
        row = body["rows"][0]
        assert row["cells"]["a3"]["display"] == "Elsbeth Bühlmann"
        assert row["gaps"] == ["a2"] and row["connections"] == 1
        assert body["complete"] is True and body["pending"] == []

    def test_without_the_bulk_route_rows_load_in_batches(self, member_client, directory, seeded):
        seeded.bulk_facts = False
        body = member_client.get("/api/graph/directories/mandate").get_json()
        assert body["pending"] == ["n1"] and "cells" not in body["rows"][0]
        rows = member_client.get("/api/graph/directories/mandate/rows?ids=n1,q1").get_json()["rows"]
        assert [r["id"] for r in rows] == ["n1"]
        assert rows[0]["cells"]["a4"]["display"] == "84’500.00 CHF"

    def test_an_address_belongs_to_one_directory(self, admin_client, directory):
        response = admin_client.post("/api/graph/views", json={"node_type_id": "t2",
                                                                "slug": "Mandate"})
        assert response.status_code == 409

    def test_a_switched_off_directory_says_so(self, admin_client, member_client, directory):
        admin_client.patch("/api/graph/views/t1", json={"active": False})
        assert member_client.get("/api/graph/directories/mandate").get_json()["inactive"] is True

    def test_only_admins_manage_directories(self, member_client, seeded):
        assert member_client.post("/api/graph/views",
                                  json={"node_type_id": "t1"}).status_code == 403

    def test_directories_are_reordered_by_swapping(self, admin_client, directory):
        admin_client.post("/api/graph/views", json={"node_type_id": "t2", "title": "Personen"})
        assert admin_client.post("/api/graph/views/t2/move", json={"delta": -1}).status_code == 200
        views = admin_client.get("/api/graph/admin/directories").get_json()["views"]
        assert [v["title"] for v in views] == ["Personen", "Mandate"]


class TestCards:
    def test_a_layout_is_saved_against_the_live_fields(self, admin_client, member_client, seeded):
        response = admin_client.put("/api/graph/cards/t1", json={"layout": {
            "head": ["a2", "zz"], "rail": ["a1"],
            "sections": [{"name": "Beteiligte", "fields": ["a3"]}]}})
        assert response.status_code == 200
        layout = member_client.get("/api/graph/nodes/n1").get_json()["layout"]
        assert layout["head"] == ["a2"] and layout["loose"] == ["a4", "a5"]

    def test_a_card_without_sections_is_refused(self, admin_client, seeded):
        response = admin_client.put("/api/graph/cards/t1",
                                    json={"layout": {"head": [], "sections": []}})
        assert response.status_code == 400


class TestSuggestions:
    def _inventory(self, fake):
        for i in range(2, 5):
            fake.nodes[f"n{i}"] = {"id": f"n{i}", "name": f"Akte {i}", "node_type_id": "t1",
                                   "created_at": "2026-01-0%d" % i}
            fake.facts[f"n{i}"] = []
            fake.graph_create_fact(f"n{i}", str(i), attribute_id="a1")

    def test_an_unfilled_field_can_be_retired_from_the_suggestion(self, admin_client, seeded):
        self._inventory(seeded)
        found = admin_client.get("/api/graph/admin/types/t1").get_json()["suggestions"]
        assert "leer:t1:a5" in [s["id"] for s in found]
        applied = admin_client.post("/api/graph/suggestions/apply", json={
            "scope": "fields", "type_id": "t1", "id": "leer:t1:a5"})
        assert applied.status_code == 200
        assert ("t1", "a5") in seeded.deprecated

    def test_a_dismissed_suggestion_does_not_return(self, admin_client, seeded):
        self._inventory(seeded)
        admin_client.post("/api/graph/suggestions/dismiss", json={
            "scope": "fields", "type_id": "t1", "id": "leer:t1:a5"})
        found = admin_client.get("/api/graph/admin/types/t1").get_json()["suggestions"]
        assert "leer:t1:a5" not in [s["id"] for s in found]

    def test_the_gap_count_cannot_be_dismissed(self, admin_client, seeded):
        self._inventory(seeded)
        gap = next(s for s in admin_client.get("/api/graph/admin/types/t1").get_json()["suggestions"]
                   if s["id"].startswith("luecken:"))
        response = admin_client.post("/api/graph/suggestions/dismiss", json={
            "scope": "fields", "type_id": "t1", "id": gap["id"]})
        assert response.status_code == 400

    def test_a_forged_suggestion_is_not_applied(self, admin_client, seeded):
        response = admin_client.post("/api/graph/suggestions/apply", json={
            "scope": "fields", "type_id": "t1", "id": "leer:t1:a1"})
        assert response.status_code == 404
        assert seeded.deprecated == []

    def test_loose_fields_are_placed_by_datatype(self, admin_client, seeded):
        card = admin_client.get("/api/graph/admin/types/t1/card").get_json()
        suggestion = next(s for s in card["suggestions"] if s["id"].startswith("karte:"))
        admin_client.post("/api/graph/suggestions/apply", json={
            "scope": "card", "type_id": "t1", "id": suggestion["id"]})
        layout = admin_client.get("/api/graph/admin/types/t1/card").get_json()["layout"]
        assert layout["head"] == ["a2"] and layout["loose"] == []
        assert {s["name"] for s in layout["sections"]} >= {"Beteiligte", "Termine",
                                                           "Wirtschaftliches"}


class TestNetworkAndHistory:
    def test_two_steps_reach_the_neighbour_s_neighbour(self, member_client, directory, seeded):
        body = member_client.get("/api/graph/nodes/n1/network?depth=2").get_json()
        assert {(n["id"], n["hop"]) for n in body["nodes"]} == {("n1", 0), ("q1", 1), ("q2", 2)}
        assert {e["label"] for e in body["edges"]} == {"Mandant", "gleiche Verwaltung"}
        assert next(n for n in body["nodes"] if n["id"] == "n1")["slug"] == "mandate"
        assert body["types"]["t2"]["tone"] == "t2"

    def test_history_names_fields_and_people(self, member_client, seeded, alice):
        fact_id = seeded.facts["n1"][2]["id"]
        seeded.histories[fact_id] = [{"event_type": "updated", "actor_ref": str(alice.id),
                                      "occurred_at": "2026-02-01T10:00:00"}]
        body = member_client.get("/api/graph/nodes/n1/history").get_json()
        assert body["available"] is True
        assert (body["events"][0]["field"], body["events"][0]["who"]) == ("Streitwert", "Alice")

    def test_an_api_without_history_says_so(self, member_client, seeded):
        assert member_client.get("/api/graph/nodes/n1/history").get_json()["available"] is False


class TestPages:
    def test_a_directory_page_and_its_place_in_the_sidebar(self, member_client, directory):
        html = member_client.get("/verzeichnis/mandate").get_data(as_text=True)
        assert "<h1>Mandate</h1>" in html
        assert 'href="/verzeichnis/mandate"' in html and 'aria-current="page"' in html
        assert 'data-kg-page="directory"' in html and "js/graph_directory.js" in html

    def test_an_unknown_directory_is_404(self, member_client, seeded):
        assert member_client.get("/verzeichnis/gibtsnicht").status_code == 404

    def test_an_entry_card_page(self, member_client, seeded):
        html = member_client.get("/eintrag/n1").get_data(as_text=True)
        assert 'data-node="n1"' in html and 'data-kg-page="entry"' in html

    def test_the_console_pages_are_for_admins(self, admin_client, member_client, seeded):
        for path in ("/admin/wissenstypen", "/admin/wissenstypen/t1",
                     "/admin/wissenstypen/t1/karte", "/admin/verzeichnisse"):
            assert admin_client.get(path).status_code == 200, path
            assert member_client.get(path).status_code == 403, path
        html = admin_client.get("/admin/wissenstypen").get_data(as_text=True)
        assert ">Wissenstypen</a>" in html and ">Verzeichnisse</a>" in html

    def test_every_asset_the_pages_load_is_served(self, admin_client, directory):
        import re

        for path in ("/verzeichnis/mandate", "/eintrag/n1", "/admin/wissenstypen/t1/karte"):
            html = admin_client.get(path).get_data(as_text=True)
            for asset in re.findall(r'(?:src|href)="(/static/[^"?]+)', html):
                assert admin_client.get(asset).status_code == 200, (path, asset)

    def test_pages_require_a_session(self, anon_client, seeded):
        assert anon_client.get("/verzeichnis/mandate").status_code == 302

    def test_fixture_mode_says_what_is_needed(self, fixture_mode_client):
        html = fixture_mode_client.get("/eintrag/n1").get_data(as_text=True)
        assert "Wissensnetz-Modus erforderlich" in html
        assert "graph_directory.js" not in html
        response = fixture_mode_client.get("/api/graph/nodes/n1")
        assert response.status_code == 409


@pytest.mark.parametrize("method, path", [
    ("POST", "/api/graph/node-types"),
    ("POST", "/api/graph/node-types/t1/schema"),
    ("PATCH", "/api/graph/node-types/t1/schema/a1"),
    ("DELETE", "/api/graph/node-types/t1/schema/a1"),
    ("POST", "/api/graph/node-types/t1/schema/a1/move"),
    ("POST", "/api/graph/nodes"),
    ("PATCH", "/api/graph/nodes/n1"),
    ("PUT", "/api/graph/nodes/n1/fields/a1"),
    ("POST", "/api/graph/nodes/n1/grants"),
    ("DELETE", "/api/graph/nodes/n1/grants/u1"),
    ("POST", "/api/graph/views"),
    ("PATCH", "/api/graph/views/t1"),
    ("DELETE", "/api/graph/views/t1"),
    ("POST", "/api/graph/views/t1/move"),
    ("PUT", "/api/graph/cards/t1"),
    ("POST", "/api/graph/suggestions/apply"),
    ("POST", "/api/graph/suggestions/dismiss"),
])
def test_every_write_needs_the_csrf_header(admin_client_no_csrf, seeded, method, path):
    response = admin_client_no_csrf.open(path, method=method, json={})
    assert response.status_code == 403
    assert "CSRF" in response.get_json()["error"]
