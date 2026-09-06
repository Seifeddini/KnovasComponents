import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


class TestFactCreate:
    def test_a_date_fact_is_encoded_before_it_leaves(self, alice_client, fake_graph,
                                                     node_owned_by_alice):
        fake_graph.schema["t1"] = [{"id": "a1", "name": "Frist", "datatype": "date",
                                    "sort_order": 0}]
        alice_client.post(f"/api/graph/nodes/{node_owned_by_alice}/facts",
                          json={"attribute_id": "a1",
                                "value": {"value": "2026-03-04", "precision": "month"}})
        assert fake_graph.last_fact["value"] == {"value": "2026-03-04",
                                                 "precision": "month"}

    def test_a_malformed_value_is_400_with_a_usable_message(self, alice_client,
                                                            fake_graph,
                                                            node_owned_by_alice):
        fake_graph.schema["t1"] = [{"id": "a1", "name": "Frist", "datatype": "date",
                                    "sort_order": 0}]
        response = alice_client.post(
            f"/api/graph/nodes/{node_owned_by_alice}/facts",
            json={"attribute_id": "a1",
                  "value": {"value": "04.03.2026"}})
        assert response.status_code == 400
        assert "JJJJ-MM-TT" in response.get_json()["error"]

    def test_an_enum_value_is_checked_against_the_attribute(self, alice_client,
                                                            fake_graph,
                                                            node_owned_by_alice):
        fake_graph.schema["t1"] = [{"id": "a1", "name": "Status", "datatype": "enum",
                                    "enum_values": ["offen", "erledigt"],
                                    "sort_order": 0}]
        response = alice_client.post(
            f"/api/graph/nodes/{node_owned_by_alice}/facts",
            json={"attribute_id": "a1", "value": "schwebend"})
        assert response.status_code == 400

    def test_a_non_editor_may_not_write_a_fact(self, member_client,
                                               node_owned_by_alice):
        response = member_client.post(
            f"/api/graph/nodes/{node_owned_by_alice}/facts",
            json={"attribute_id": "a1", "value": "x"})
        assert response.status_code == 403

    def test_a_free_form_fact_needs_a_label(self, alice_client, node_owned_by_alice):
        response = alice_client.post(
            f"/api/graph/nodes/{node_owned_by_alice}/facts", json={"value": "x"})
        assert response.status_code == 400


class TestFactMutation:
    def test_patching_a_fact_without_a_node_id_is_403(self, alice_client):
        """The write gate is per node; without the node there is nothing to
        authorise against, and defaulting to allow would be the bug."""
        assert alice_client.patch("/api/graph/facts/f1",
                                  json={"value": "x"}).status_code == 403

    def test_the_owner_may_patch_a_fact_on_their_node(self, alice_client,
                                                      node_owned_by_alice, fake_graph):
        fake_graph.schema["t1"] = [{"id": "a1", "name": "Titel", "datatype": "text",
                                    "sort_order": 0}]
        response = alice_client.patch(
            "/api/graph/facts/f1",
            json={"node_id": node_owned_by_alice, "attribute_id": "a1",
                  "value": "Neu"})
        assert response.status_code == 200
