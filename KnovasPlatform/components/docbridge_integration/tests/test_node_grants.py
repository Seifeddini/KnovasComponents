"""Per-user write grants on graph nodes (SS-315, plan C1).

The rules in one place: the creator owns; the owner grants and revokes editors;
an admin overrides both; and nothing here decides who may READ, which is the
backend's ACL.

Alloy: models/alloy/node_grants.als (WriteGateMechanism, GrantTableShape) and
models/alloy/node_grants_lifecycle.als (RevokeMechanism, CreateMechanism).
"""
import uuid

import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = [
    pytest.mark.precondition,
    pytest.mark.skipif(not platform_db_reachable(),
                       reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}"),
]

from identity.node_grants import (  # noqa: E402
    NodeGrantStore, NodeOwnerConflict, OwnerRevokeError)

PASSWORD = "korrektes-pferd-batterie"


class FakeUser:
    """A principal as may_write sees it: an id and the platform roles."""

    def __init__(self, roles=frozenset()):
        self.id = uuid.uuid4()
        self.roles = frozenset(roles)


@pytest.fixture
def store(platform_db):
    """platform_db comes from conftest: a migrated per-test schema."""
    return NodeGrantStore(platform_db)


@pytest.fixture
def alice(identity_repo):
    return identity_repo.create(email="alice@kanzlei.ch", display_name="Alice",
                                password=PASSWORD)


@pytest.fixture
def bob(identity_repo):
    return identity_repo.create(email="bob@kanzlei.ch", display_name="Bob",
                                password=PASSWORD)


class TestOwnership:
    def test_the_creator_becomes_the_owner(self, store, alice):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        assert store.for_node(node)["owner"] == str(alice.id)

    def test_setting_the_owner_twice_is_idempotent(self, store, alice):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        store.set_owner(node, alice.id)
        assert store.for_node(node)["editors"] == []

    def test_the_owner_may_write(self, store, alice):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        assert store.may_write(node, alice)

    def test_a_second_different_owner_is_refused(self, store, alice, bob):
        """GrantTableShape: one owner per node. Without this test the partial
        unique index can be deleted from the migration and the suite stays
        green — and "who may grant editors?" quietly gets two answers."""
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        with pytest.raises(NodeOwnerConflict):
            store.set_owner(node, bob.id)
        assert store.for_node(node)["owner"] == str(alice.id)

    def test_the_refusal_is_a_domain_error_not_a_driver_error(self, store, alice, bob):
        """The route layer has to turn this into a 409 with a sentence, which
        it cannot do if it has to catch psycopg by name."""
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        try:
            store.set_owner(node, bob.id)
        except NodeOwnerConflict as conflict:
            assert "Eigentümer" in str(conflict)
        else:
            pytest.fail("a second owner was accepted")

    def test_the_store_survives_the_conflict(self, store, alice, bob):
        """Not a formality: a driver error inside a transaction leaves the
        connection unusable, and the next query would fail for a reason that
        has nothing to do with ownership."""
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        with pytest.raises(NodeOwnerConflict):
            store.set_owner(node, bob.id)
        store.grant_editor(node, bob.id, granted_by=alice.id)
        assert store.may_write(node, bob)


class TestEditors:
    def test_an_editor_may_write(self, store, alice, bob):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        store.grant_editor(node, bob.id, granted_by=alice.id)
        assert store.may_write(node, bob)

    def test_a_stranger_may_not_write(self, store, alice, bob):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        assert not store.may_write(node, bob)

    def test_granting_editor_to_the_owner_does_not_demote_them(self, store, alice):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        store.grant_editor(node, alice.id, granted_by=alice.id)
        assert store.for_node(node)["owner"] == str(alice.id)

    def test_revoking_removes_the_write_right(self, store, alice, bob):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        store.grant_editor(node, bob.id, granted_by=alice.id)
        store.revoke(node, bob.id)
        assert not store.may_write(node, bob)

    def test_the_owner_cannot_be_revoked(self, store, alice):
        """Otherwise a node ends up with nobody who may grant anything."""
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        with pytest.raises(OwnerRevokeError):
            store.revoke(node, alice.id)


class TestAdminOverride:
    def test_an_admin_may_write_any_node(self, store, alice):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        assert store.may_write(node, FakeUser(roles={"admin"}))

    def test_an_admin_may_write_a_node_with_no_grants_at_all(self, store):
        """Nodes created before this feature have no owner. An admin must still
        be able to repair them."""
        assert store.may_write(str(uuid.uuid4()), FakeUser(roles={"admin"}))

    def test_a_member_may_not_write_an_ungranted_node(self, store, bob):
        assert not store.may_write(str(uuid.uuid4()), bob)


class TestDeadData:
    def test_a_grant_for_an_unknown_node_is_simply_inert(self, store, alice):
        """node_id has no FK by design; a grant whose node was deleted must not
        raise on read."""
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        assert store.for_node(str(uuid.uuid4())) == {"owner": None, "editors": []}


class TestAMalformedNodeId:
    """node_grants.node_id is a UUID column and node ids arrive from a URL path
    segment. Before this, `may_write("n1", user)` raised psycopg's
    InvalidTextRepresentation out of the authorisation guard: a 500 an operator
    cannot read, produced by any caller who chooses the path."""

    @pytest.mark.parametrize("node_id", ["n1", "", "../nodes", None, "not-a-uuid"])
    def test_a_member_is_refused_rather_than_crashed(self, store, bob, node_id):
        assert store.may_write(node_id, bob) is False

    def test_reading_the_grants_of_an_impossible_id_is_empty_not_an_error(self, store):
        assert store.for_node("n1") == {"owner": None, "editors": []}

    @pytest.mark.parametrize("call", ["set_owner", "grant_editor", "revoke"])
    def test_a_write_says_so_instead_of_failing_silently(self, store, alice, call):
        with pytest.raises(ValueError):
            getattr(store, call)("n1", alice.id)

    def test_an_admin_still_passes(self, store):
        """The admin branch never touches the database, so its answer does not
        depend on the id being well-formed; the route answers 404 for an id no
        node has. Asserted so the asymmetry is a decision, not an accident."""
        assert store.may_write("n1", FakeUser(roles={"admin"})) is True
