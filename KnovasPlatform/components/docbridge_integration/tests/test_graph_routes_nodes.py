import pytest

from conftest import PLATFORM_DB_TEST_DSN, SEEDED_NODE_ID, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


class TestNodeList:
    def test_the_list_passes_the_filters_through(self, member_client, fake_graph):
        member_client.get("/api/graph/nodes?type=t1&q=M%C3%BCller")
        assert fake_graph.last_node_filters == {"node_type_id": "t1", "q": "Müller"}

    def test_the_list_is_not_narrowed_by_grants(self, member_client, fake_graph,
                                                node_owned_by_alice):
        """Read is the backend ACL's decision. Filtering by node_grants here
        would be a second read model."""
        body = member_client.get("/api/graph/nodes").get_json()
        assert node_owned_by_alice in [n["id"] for n in body["nodes"]]


class TestNodeCreate:
    def test_creating_a_node_makes_the_creator_the_owner(self, alice_client,
                                                         grants, fake_graph):
        body = alice_client.post("/api/graph/nodes",
                                 json={"name": "Mueller AG",
                                       "node_type_id": "t1"}).get_json()
        assert grants.for_node(body["node"]["id"])["owner"] is not None

    def test_creating_a_node_without_a_name_is_400(self, alice_client):
        assert alice_client.post("/api/graph/nodes",
                                 json={"node_type_id": "t1"}).status_code == 400

    def test_any_member_may_create(self, member_client):
        assert member_client.post("/api/graph/nodes",
                                  json={"name": "X"}).status_code == 201

    def test_a_required_field_left_empty_does_not_block_the_save(
            self, alice_client, fake_graph):
        """Schemas are overlays that make absence visible; they never gate a
        write. Blocking would empty the completeness report of its purpose."""
        fake_graph.schema["t1"] = [{"id": "a1", "name": "Frist", "datatype": "date",
                                    "required": True, "sort_order": 0}]
        response = alice_client.post("/api/graph/nodes",
                                     json={"name": "Ohne Frist", "node_type_id": "t1"})
        assert response.status_code == 201


class TestNodeDetail:
    def test_detail_returns_the_composed_payload(self, member_client):
        body = member_client.get(f"/api/graph/nodes/{SEEDED_NODE_ID}").get_json()
        assert set(body) >= {"node", "fields", "neighbourhood", "grants", "visibility"}

    def test_an_unknown_node_is_404(self, member_client):
        assert member_client.get("/api/graph/nodes/nope").status_code == 404
