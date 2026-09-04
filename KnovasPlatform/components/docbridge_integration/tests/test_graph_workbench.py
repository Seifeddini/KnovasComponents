"""One screen, one payload.

The join between facts and attribute definitions happens here rather than in
the browser: the field reader must show an attribute that has NO fact (the
visible gap), which a fact-only response cannot express.
"""
import pytest

from conftest import (FakeGraphApi, PLATFORM_DB_TEST_DSN, SEEDED_NODE_ID,
                      platform_db_reachable)
from graph_workbench import compose_node

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


@pytest.fixture
def client():
    """The composer takes any object with the client's graph_* methods."""
    return FakeGraphApi(config=None)


class TestFieldJoin:
    def test_a_filled_attribute_carries_its_fact(self, client, grants):
        client.schema["t1"] = [{"id": "a1", "name": "Frist", "datatype": "date",
                                "required": True, "sort_order": 0}]
        client.facts[SEEDED_NODE_ID] = [{"id": "f1", "attribute_id": "a1",
                               "value": {"value": "2026-03-04", "precision": "day"}}]
        payload = compose_node(client, grants, SEEDED_NODE_ID)
        field = payload["fields"][0]
        assert field["fact_id"] == "f1"
        assert field["display"] == "04.03.2026"
        assert field["missing"] is False

    def test_a_required_attribute_with_no_fact_is_a_visible_gap(self, client, grants):
        """The completeness report exists to count these. They are shown, never
        treated as an error."""
        client.schema["t1"] = [{"id": "a1", "name": "Frist", "datatype": "date",
                                "required": True, "sort_order": 0}]
        client.facts[SEEDED_NODE_ID] = []
        field = compose_node(client, grants, SEEDED_NODE_ID)["fields"][0]
        assert field["missing"] is True and field["required"] is True
        assert field["value"] is None

    def test_fields_follow_sort_order(self, client, grants):
        client.schema["t1"] = [
            {"id": "a2", "name": "Frist", "datatype": "date", "sort_order": 1},
            {"id": "a1", "name": "Titel", "datatype": "text", "sort_order": 0}]
        names = [f["name"] for f in compose_node(client, grants, SEEDED_NODE_ID)["fields"]]
        assert names == ["Titel", "Frist"]

    def test_a_free_form_fact_appears_after_the_schema_fields(self, client, grants):
        """attribute_id is NULL for a fact typed in without a definition. It is
        real content and must not vanish because the schema does not name it."""
        client.schema["t1"] = [{"id": "a1", "name": "Titel", "datatype": "text",
                                "sort_order": 0}]
        client.facts[SEEDED_NODE_ID] = [{"id": "f9", "attribute_id": None,
                               "label": "Notiz", "value": "frei"}]
        fields = compose_node(client, grants, SEEDED_NODE_ID)["fields"]
        assert fields[-1]["name"] == "Notiz" and fields[-1]["attribute_id"] is None

    def test_a_fact_for_a_deprecated_attribute_still_renders(self, client, grants):
        """Deprecation keeps facts. Dropping them here would make the UI lie
        about what the node contains."""
        client.schema["t1"] = []
        client.facts[SEEDED_NODE_ID] = [{"id": "f1", "attribute_id": "a-old",
                               "label": "Altfeld", "value": "Wert"}]
        assert len(compose_node(client, grants, SEEDED_NODE_ID)["fields"]) == 1

    def test_a_node_without_a_type_has_no_schema_fields(self, client, grants):
        client.nodes[SEEDED_NODE_ID] = {"id": SEEDED_NODE_ID, "name": "Lose",
                                        "node_type_id": None}
        assert compose_node(client, grants, SEEDED_NODE_ID)["fields"] == []


class TestNeighbourhood:
    def test_the_neighbourhood_is_depth_one_with_edges(self, client, grants):
        compose_node(client, grants, SEEDED_NODE_ID)
        assert client.last_neighbours == {"node_id": SEEDED_NODE_ID, "depth": 1,
                                          "include_edges": True}


class TestVisibilityAndGrants:
    def test_the_payload_carries_the_backend_acl(self, client, grants):
        client.nodes[SEEDED_NODE_ID]["access_group_ids"] = ["g-legal"]
        payload = compose_node(client, grants, SEEDED_NODE_ID)
        assert payload["visibility"]["access_group_ids"] == ["g-legal"]

    def test_the_payload_carries_the_platform_grants(self, client, grants, alice):
        grants.set_owner(SEEDED_NODE_ID, alice.id)
        assert compose_node(client, grants, SEEDED_NODE_ID)["grants"]["owner"] == str(alice.id)


class TestMissingNode:
    def test_an_unknown_node_composes_to_none(self, client, grants):
        """The client maps 404 to None; the composer must not build a page
        around a node that is not there."""
        assert compose_node(client, grants, "nope") is None
