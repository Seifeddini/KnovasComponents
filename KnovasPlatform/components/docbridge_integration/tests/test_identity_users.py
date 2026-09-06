"""Accounts, roles and the leaver path (KC-B1-1, KC-B1-2, KC-B1-5)."""

import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}",
)

from identity import users  # noqa: E402
from identity.passwords import WeakPasswordError  # noqa: E402


@pytest.fixture
def repo(platform_db):
    return users.UserRepository(platform_db)


class TestCreatingAccounts:
    def test_a_created_user_can_be_found_by_email(self, repo):
        created = repo.create(email="anna@kanzlei.ch", display_name="Anna Meier",
                              password="korrektes-pferd-batterie")
        assert repo.get_by_email("anna@kanzlei.ch").id == created.id

    def test_lookup_by_email_ignores_case(self, repo):
        repo.create(email="anna@kanzlei.ch", display_name="Anna", password="korrektes-pferd-batterie")
        assert repo.get_by_email("Anna@Kanzlei.CH") is not None

    def test_an_unknown_email_returns_none_not_an_error(self, repo):
        assert repo.get_by_email("niemand@kanzlei.ch") is None

    def test_a_duplicate_email_is_a_named_error(self, repo):
        repo.create(email="anna@kanzlei.ch", display_name="Anna", password="korrektes-pferd-batterie")
        with pytest.raises(users.EmailTakenError):
            repo.create(email="ANNA@kanzlei.ch", display_name="Other",
                        password="korrektes-pferd-batterie")

    def test_a_weak_password_is_refused_and_no_user_is_created(self, repo):
        with pytest.raises(WeakPasswordError):
            repo.create(email="weak@kanzlei.ch", display_name="W", password="kurz")
        assert repo.get_by_email("weak@kanzlei.ch") is None

    def test_a_federated_account_needs_no_password(self, repo):
        created = repo.create(email="sso@kanzlei.ch", display_name="SSO",
                              idp_subject="entra|abc")
        assert created.id is not None

    def test_the_stored_password_is_not_the_plaintext(self, repo):
        repo.create(email="anna@kanzlei.ch", display_name="Anna", password="korrektes-pferd-batterie")
        stored = repo._row_by_email("anna@kanzlei.ch")["password_hash"]
        assert "korrektes-pferd-batterie" not in stored
        assert stored.startswith("$argon2id$")


class TestAuthentication:
    def test_the_right_password_authenticates(self, repo):
        repo.create(email="anna@kanzlei.ch", display_name="Anna", password="korrektes-pferd-batterie")
        assert repo.authenticate("anna@kanzlei.ch", "korrektes-pferd-batterie") is not None

    def test_the_wrong_password_does_not(self, repo):
        repo.create(email="anna@kanzlei.ch", display_name="Anna", password="korrektes-pferd-batterie")
        assert repo.authenticate("anna@kanzlei.ch", "falsch-falsch-falsch") is None

    def test_an_unknown_account_does_not_authenticate(self, repo):
        assert repo.authenticate("niemand@kanzlei.ch", "korrektes-pferd-batterie") is None

    def test_a_federated_account_cannot_authenticate_with_a_password(self, repo):
        """password_hash is NULL. Nothing may verify against it."""
        repo.create(email="sso@kanzlei.ch", display_name="SSO", idp_subject="entra|abc")
        assert repo.authenticate("sso@kanzlei.ch", "korrektes-pferd-batterie") is None

    def test_a_disabled_account_does_not_authenticate(self, repo):
        """The leaver rule at the front door."""
        created = repo.create(email="weg@kanzlei.ch", display_name="Weg",
                              password="korrektes-pferd-batterie")
        repo.disable(created.id)
        assert repo.authenticate("weg@kanzlei.ch", "korrektes-pferd-batterie") is None


class TestLockout:
    def test_repeated_failures_lock_the_account(self, repo):
        repo.create(email="anna@kanzlei.ch", display_name="Anna", password="korrektes-pferd-batterie")
        for _ in range(users.MAX_FAILED_ATTEMPTS):
            repo.authenticate("anna@kanzlei.ch", "falsch-falsch-falsch")
        assert repo.get_by_email("anna@kanzlei.ch").is_locked

    def test_the_right_password_is_refused_while_locked(self, repo):
        """Per-user lockout, beside the existing per-IP throttle — an attacker
        with many addresses must not get unlimited guesses at one account."""
        repo.create(email="anna@kanzlei.ch", display_name="Anna", password="korrektes-pferd-batterie")
        for _ in range(users.MAX_FAILED_ATTEMPTS):
            repo.authenticate("anna@kanzlei.ch", "falsch-falsch-falsch")
        assert repo.authenticate("anna@kanzlei.ch", "korrektes-pferd-batterie") is None

    def test_a_success_before_the_limit_clears_the_counter(self, repo):
        repo.create(email="anna@kanzlei.ch", display_name="Anna", password="korrektes-pferd-batterie")
        repo.authenticate("anna@kanzlei.ch", "falsch-falsch-falsch")
        repo.authenticate("anna@kanzlei.ch", "korrektes-pferd-batterie")
        assert repo._row_by_email("anna@kanzlei.ch")["failed_attempts"] == 0

    def test_an_admin_can_unlock(self, repo):
        repo.create(email="anna@kanzlei.ch", display_name="Anna", password="korrektes-pferd-batterie")
        for _ in range(users.MAX_FAILED_ATTEMPTS):
            repo.authenticate("anna@kanzlei.ch", "falsch-falsch-falsch")
        repo.unlock(repo.get_by_email("anna@kanzlei.ch").id)
        assert repo.authenticate("anna@kanzlei.ch", "korrektes-pferd-batterie") is not None


class TestRoles:
    def test_a_new_user_has_no_roles(self, repo):
        created = repo.create(email="anna@kanzlei.ch", display_name="Anna",
                              password="korrektes-pferd-batterie")
        assert repo.roles_of(created.id) == frozenset()

    def test_granting_a_role_shows_up(self, repo):
        created = repo.create(email="anna@kanzlei.ch", display_name="Anna",
                              password="korrektes-pferd-batterie")
        repo.grant_role(created.id, "admin")
        assert "admin" in repo.roles_of(created.id)

    def test_granting_twice_is_not_an_error(self, repo):
        created = repo.create(email="anna@kanzlei.ch", display_name="Anna",
                              password="korrektes-pferd-batterie")
        repo.grant_role(created.id, "admin")
        repo.grant_role(created.id, "admin")
        assert repo.roles_of(created.id) == {"admin"}

    def test_revoking_a_role_removes_it(self, repo):
        created = repo.create(email="anna@kanzlei.ch", display_name="Anna",
                              password="korrektes-pferd-batterie")
        repo.grant_role(created.id, "admin")
        repo.revoke_role(created.id, "admin")
        assert repo.roles_of(created.id) == frozenset()

    def test_an_unknown_role_is_a_named_error(self, repo):
        created = repo.create(email="anna@kanzlei.ch", display_name="Anna",
                              password="korrektes-pferd-batterie")
        with pytest.raises(users.UnknownRoleError):
            repo.grant_role(created.id, "wizard")

    def test_the_authenticated_user_carries_their_roles(self, repo):
        created = repo.create(email="anna@kanzlei.ch", display_name="Anna",
                              password="korrektes-pferd-batterie")
        repo.grant_role(created.id, "approver")
        assert "approver" in repo.authenticate("anna@kanzlei.ch", "korrektes-pferd-batterie").roles


class TestAccessGroupGrants:
    def test_granted_groups_come_back(self, repo):
        created = repo.create(email="anna@kanzlei.ch", display_name="Anna",
                              password="korrektes-pferd-batterie")
        repo.set_access_groups(created.id, ["litigation", "ip"])
        assert repo.access_groups_of(created.id) == ("ip", "litigation")

    def test_setting_replaces_rather_than_adds(self, repo):
        created = repo.create(email="anna@kanzlei.ch", display_name="Anna",
                              password="korrektes-pferd-batterie")
        repo.set_access_groups(created.id, ["litigation"])
        repo.set_access_groups(created.id, ["ip"])
        assert repo.access_groups_of(created.id) == ("ip",)

    def test_setting_an_empty_list_removes_every_grant(self, repo):
        created = repo.create(email="anna@kanzlei.ch", display_name="Anna",
                              password="korrektes-pferd-batterie")
        repo.set_access_groups(created.id, ["litigation"])
        repo.set_access_groups(created.id, [])
        assert repo.access_groups_of(created.id) == ()

    def test_groups_are_returned_sorted_and_deduplicated(self, repo):
        """The assertion signs this list; a stable order keeps it comparable."""
        created = repo.create(email="anna@kanzlei.ch", display_name="Anna",
                              password="korrektes-pferd-batterie")
        repo.set_access_groups(created.id, ["ip", "litigation", "ip"])
        assert repo.access_groups_of(created.id) == ("ip", "litigation")


class TestPasswordChange:
    def test_changing_the_password_lets_the_new_one_in(self, repo):
        created = repo.create(email="anna@kanzlei.ch", display_name="Anna",
                              password="korrektes-pferd-batterie")
        repo.set_password(created.id, "ein-ganz-anderes-passwort")
        assert repo.authenticate("anna@kanzlei.ch", "ein-ganz-anderes-passwort") is not None

    def test_the_old_password_stops_working(self, repo):
        created = repo.create(email="anna@kanzlei.ch", display_name="Anna",
                              password="korrektes-pferd-batterie")
        repo.set_password(created.id, "ein-ganz-anderes-passwort")
        assert repo.authenticate("anna@kanzlei.ch", "korrektes-pferd-batterie") is None

    def test_setting_a_password_clears_the_must_change_flag(self, repo):
        created = repo.create(email="anna@kanzlei.ch", display_name="Anna",
                              password="korrektes-pferd-batterie",
                              must_change_password=True)
        repo.set_password(created.id, "ein-ganz-anderes-passwort")
        assert repo.get_by_email("anna@kanzlei.ch").must_change_password is False
