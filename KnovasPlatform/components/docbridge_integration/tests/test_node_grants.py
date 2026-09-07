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

from identity.node_grants import NodeGrantStore, OwnerRevokeError  # noqa: E402

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
