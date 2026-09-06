import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


class TestNodeTypes:
    def test_listing_types(self, member_client, fake_graph):
        fake_graph.node_types = [{"id": "t1", "name": "Mandat"}]
        body = member_client.get("/api/graph/node-types").get_json()
        assert body["node_types"][0]["name"] == "Mandat"

    def test_creating_a_type_without_a_name_is_400(self, admin_client):
        assert admin_client.post("/api/graph/node-types", json={}).status_code == 400


class TestSchema:
    def test_reading_a_schema_returns_attributes_in_sort_order(self, member_client,
                                                               fake_graph):
        fake_graph.schema["t1"] = [
            {"id": "a2", "name": "Frist", "datatype": "date", "sort_order": 1},
            {"id": "a1", "name": "Titel", "datatype": "text", "sort_order": 0},
        ]
        body = member_client.get("/api/graph/node-types/t1/schema").get_json()
        assert [a["name"] for a in body["attributes"]] == ["Titel", "Frist"]

    def test_creating_an_attribute_with_an_unknown_datatype_is_400(self, admin_client):
        response = admin_client.post("/api/graph/node-types/t1/schema",
                                     json={"name": "X", "datatype": "timestamp"})
        assert response.status_code == 400

    def test_an_enum_without_values_is_400(self, admin_client):
        response = admin_client.post("/api/graph/node-types/t1/schema",
                                     json={"name": "Status", "datatype": "enum"})
        assert response.status_code == 400

    def test_an_entity_ref_may_carry_a_target_type(self, admin_client, fake_graph):
        response = admin_client.post(
            "/api/graph/node-types/t1/schema",
            json={"name": "Zustaendig", "datatype": "entity_ref",
                  "target_node_type_id": "t2"})
        assert response.status_code == 201
        assert fake_graph.last_attribute["target_node_type_id"] == "t2"

    def test_delete_deprecates_and_says_so(self, admin_client, fake_graph):
        body = admin_client.delete(
            "/api/graph/node-types/t1/schema/a1").get_json()
        assert body["deprecated"] is True
        assert fake_graph.deprecated == [("t1", "a1")]

    def test_a_member_may_not_deprecate_an_attribute(self, member_client):
        assert member_client.delete(
            "/api/graph/node-types/t1/schema/a1").status_code == 403
