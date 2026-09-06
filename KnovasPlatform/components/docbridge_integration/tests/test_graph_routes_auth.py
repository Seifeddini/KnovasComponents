"""Authorisation on /api/graph/*, asserted on the route rather than the link.

Hiding a control is presentation; refusing the request is the control. Every
test here calls the endpoint directly for that reason.

Alloy: models/alloy/node_grants.als (WriteGateMechanism, ReadGateMechanism).
"""
import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


class TestAuthentication:
    def test_an_anonymous_caller_gets_401(self, anon_client):
        assert anon_client.get("/api/graph/node-types").status_code == 401

    def test_a_member_may_read_the_type_list(self, member_client):
        assert member_client.get("/api/graph/node-types").status_code == 200


class TestAdminGate:
    def test_a_member_may_not_create_a_node_type(self, member_client):
        response = member_client.post("/api/graph/node-types", json={"name": "Mandat"})
        assert response.status_code == 403

    def test_an_admin_may_create_a_node_type(self, admin_client):
        response = admin_client.post("/api/graph/node-types", json={"name": "Mandat"})
        assert response.status_code == 201


class TestNodeWriteGate:
    def test_a_non_editor_may_not_patch_a_node(self, member_client, node_owned_by_alice):
        response = member_client.patch(f"/api/graph/nodes/{node_owned_by_alice}",
                                       json={"name": "Neu"})
        assert response.status_code == 403

    def test_the_owner_may_patch_their_node(self, alice_client, node_owned_by_alice):
        response = alice_client.patch(f"/api/graph/nodes/{node_owned_by_alice}",
                                      json={"name": "Neu"})
        assert response.status_code == 200

    def test_a_non_editor_may_still_read_it(self, member_client, node_owned_by_alice):
        """Read is the backend ACL's decision, never node_grants'."""
        assert member_client.get(
            f"/api/graph/nodes/{node_owned_by_alice}").status_code == 200


class TestCsrf:
    def test_a_state_changing_request_without_the_header_is_refused(
            self, admin_client_no_csrf):
        response = admin_client_no_csrf.post("/api/graph/node-types",
                                             json={"name": "Mandat"})
        assert response.status_code == 403


class TestFixtureMode:
    def test_every_graph_route_refuses_in_fixture_mode(self, fixture_mode_client):
        response = fixture_mode_client.get("/api/graph/node-types")
        assert response.status_code == 409
        assert response.get_json()["error"] == "Wissensnetz-Modus erforderlich"
