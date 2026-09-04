import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


class TestGrantRead:
    def test_grants_resolve_to_people_not_uuids(self, member_client, grants,
                                                node_owned_by_alice, alice):
        body = member_client.get(
            f"/api/graph/nodes/{node_owned_by_alice}/grants").get_json()
        assert body["owner"]["email"] == alice.email

    def test_anyone_may_see_who_the_editors_are(self, member_client,
                                                node_owned_by_alice):
        assert member_client.get(
            f"/api/graph/nodes/{node_owned_by_alice}/grants").status_code == 200


class TestGrantWrite:
    def test_the_owner_may_grant_an_editor(self, alice_client, grants,
                                           node_owned_by_alice, bob):
        response = alice_client.post(f"/api/graph/nodes/{node_owned_by_alice}/grants",
                                     json={"user_id": str(bob.id)})
        assert response.status_code == 201
        assert str(bob.id) in grants.for_node(node_owned_by_alice)["editors"]

    def test_an_editor_may_not_grant_further_editors(self, bob_client, grants,
                                                     node_owned_by_alice, bob, carol):
        grants.grant_editor(node_owned_by_alice, bob.id, granted_by=None)
        response = bob_client.post(f"/api/graph/nodes/{node_owned_by_alice}/grants",
                                   json={"user_id": str(carol.id)})
        assert response.status_code == 403

    def test_an_admin_may_grant_on_any_node(self, admin_client, node_owned_by_alice,
                                            bob):
        assert admin_client.post(f"/api/graph/nodes/{node_owned_by_alice}/grants",
                                 json={"user_id": str(bob.id)}).status_code == 201

    def test_revoking_the_owner_is_409_not_500(self, alice_client,
                                               node_owned_by_alice, alice):
        response = alice_client.delete(
            f"/api/graph/nodes/{node_owned_by_alice}/grants/{alice.id}")
        assert response.status_code == 409

    def test_granting_to_an_unknown_user_is_404(self, alice_client,
                                                node_owned_by_alice):
        import uuid
        response = alice_client.post(f"/api/graph/nodes/{node_owned_by_alice}/grants",
                                     json={"user_id": str(uuid.uuid4())})
        assert response.status_code == 404
