"""Groups come from the session, never from the browser (KC-B2-1)."""

import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}",
)

from identity import assertion, principal  # noqa: E402


@pytest.fixture
def keypair():
    return assertion.generate_keypair()


@pytest.fixture
def broker(platform_db, identity_repo, keypair):
    return principal.PrincipalBroker(
        user_repo=identity_repo,
        signer=assertion.AssertionSigner(keypair.private_pem, key_id="k1"),
        tenant_id="tenant-a",
    )


@pytest.fixture
def verifier(keypair):
    return assertion.AssertionVerifier({"k1": keypair.public_pem})


@pytest.fixture
def anna(identity_repo):
    user = identity_repo.create(email="anna@kanzlei.ch", display_name="Anna",
                                password="korrektes-pferd-batterie")
    identity_repo.set_access_groups(user.id, ["litigation", "ip"])
    identity_repo.grant_role(user.id, "member")
    return identity_repo.get(user.id)


class TestGroupsComeFromTheDatabase:
    def test_the_assertion_carries_the_users_granted_groups(self, broker, verifier, anna):
        claims = verifier.verify(broker.assertion_for(anna), tenant="tenant-a")
        assert claims.groups == ("ip", "litigation")

    def test_a_user_with_no_grants_asserts_no_groups(self, broker, verifier, identity_repo):
        user = identity_repo.create(email="neu@kanzlei.ch", display_name="Neu",
                                    password="korrektes-pferd-batterie")
        claims = verifier.verify(broker.assertion_for(user), tenant="tenant-a")
        assert claims.groups == ()

    def test_the_subject_is_the_opaque_id_not_the_address(self, broker, verifier, anna):
        claims = verifier.verify(broker.assertion_for(anna), tenant="tenant-a")
        assert claims.subject == str(anna.id)
        assert "@" not in claims.subject

    def test_platform_roles_ride_along_for_the_remote_controller(self, broker, verifier, anna):
        claims = verifier.verify(broker.assertion_for(anna), tenant="tenant-a")
        assert "member" in claims.roles

    def test_revoking_a_group_changes_the_next_assertion(
        self, broker, verifier, identity_repo, anna
    ):
        """No caching: the grant table is read per assertion, so an admin's
        change takes effect on the user's next request."""
        identity_repo.set_access_groups(anna.id, ["ip"])
        claims = verifier.verify(broker.assertion_for(anna), tenant="tenant-a")
        assert claims.groups == ("ip",)


class TestTheBrowserCannotAssertAnything:
    def test_a_body_supplied_group_list_is_rejected_outright(self, broker):
        """Not ignored — rejected. Silently dropping it would let a caller
        believe a scope applied that never did."""
        with pytest.raises(principal.ClientAssertedGroupsError):
            broker.reject_client_assertion({"Input": "x", "access_groups": ["everything"]})

    def test_an_empty_body_supplied_list_is_still_rejected(self, broker):
        with pytest.raises(principal.ClientAssertedGroupsError):
            broker.reject_client_assertion({"access_groups": []})

    def test_a_body_without_the_field_is_fine(self, broker):
        broker.reject_client_assertion({"Input": "x"})

    def test_no_body_at_all_is_fine(self, broker):
        broker.reject_client_assertion(None)


class TestDualControlTokens:
    def test_the_broker_mints_one_for_an_approved_request(self, broker, verifier, anna,
                                                          identity_repo):
        approver = identity_repo.create(email="chef@kanzlei.ch", display_name="Chef",
                                        password="korrektes-pferd-batterie")
        token = broker.dual_control_token(
            action="matter_delete", target="node:1",
            requester=anna, approver=identity_repo.get(approver.id),
        )
        claims = verifier.verify_dual_control(
            token, tenant="tenant-a", action="matter_delete", target="node:1"
        )
        assert claims.requester == str(anna.id)

    def test_it_refuses_to_mint_one_for_self_approval(self, broker, anna):
        with pytest.raises(ValueError):
            broker.dual_control_token(
                action="matter_delete", target="node:1", requester=anna, approver=anna
            )
