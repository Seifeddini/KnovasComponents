"""First boot: exactly one administrator, never a shipped default (KC-F3)."""

import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}",
)

from identity import bootstrap, users  # noqa: E402


@pytest.fixture
def repo(platform_db):
    return users.UserRepository(platform_db)


class TestFirstBoot:
    def test_it_creates_the_administrator(self, platform_db, repo, tmp_path):
        bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch",
                               secret_path=tmp_path / "pw")
        assert repo.get_by_email("chef@kanzlei.ch") is not None

    def test_the_administrator_has_the_admin_role(self, platform_db, repo, tmp_path):
        bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch",
                               secret_path=tmp_path / "pw")
        assert "admin" in repo.get_by_email("chef@kanzlei.ch").roles

    def test_the_one_time_password_is_written_where_told(self, platform_db, tmp_path):
        secret = tmp_path / "pw"
        bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch", secret_path=secret)
        assert secret.read_text(encoding="utf-8").strip()

    def test_the_written_password_actually_works(self, platform_db, repo, tmp_path):
        secret = tmp_path / "pw"
        bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch", secret_path=secret)
        password = secret.read_text(encoding="utf-8").strip()
        assert repo.authenticate("chef@kanzlei.ch", password) is not None

    def test_the_administrator_must_change_it(self, platform_db, repo, tmp_path):
        bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch",
                               secret_path=tmp_path / "pw")
        assert repo.get_by_email("chef@kanzlei.ch").must_change_password is True

    def test_the_generated_password_satisfies_the_policy(self, platform_db, tmp_path):
        secret = tmp_path / "pw"
        bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch", secret_path=secret)
        from identity import passwords
        assert passwords.check_policy(secret.read_text(encoding="utf-8").strip()) == []

    def test_generated_passwords_are_never_the_same_twice(self):
        """No fixed seed, no shipped default. Tested on the generator rather
        than on two bootstrap runs, because a second run is correctly a no-op
        and would generate nothing at all."""
        generated = {bootstrap._generate_password() for _ in range(50)}
        assert len(generated) == 50

    def test_the_creation_is_audited(self, platform_db, tmp_path):
        bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch",
                               secret_path=tmp_path / "pw")
        rows = platform_db.execute(
            "SELECT action FROM audit_log WHERE action = 'bootstrap.admin_created'"
        ).fetchall()
        assert len(rows) == 1


class TestItRunsExactlyOnce:
    def test_a_second_run_creates_nobody(self, platform_db, repo, tmp_path):
        bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch",
                               secret_path=tmp_path / "pw")
        bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch",
                               secret_path=tmp_path / "pw2")
        assert len(repo.list_all()) == 1

    def test_a_second_run_does_not_reset_the_password(self, platform_db, repo, tmp_path):
        """Restarting the container must not hand out a new admin credential."""
        first = tmp_path / "pw"
        bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch", secret_path=first)
        original = first.read_text(encoding="utf-8").strip()
        bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch",
                               secret_path=tmp_path / "pw2")
        assert repo.authenticate("chef@kanzlei.ch", original) is not None

    def test_a_second_run_writes_no_new_secret_file(self, platform_db, tmp_path):
        bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch",
                               secret_path=tmp_path / "pw")
        second = tmp_path / "pw2"
        bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch", secret_path=second)
        assert not second.exists()

    def test_it_stays_quiet_when_any_account_exists(self, platform_db, repo, tmp_path):
        """Not just 'an admin exists' — any account means the firm has started."""
        repo.create(email="anwalt@kanzlei.ch", display_name="A",
                    password="korrektes-pferd-batterie")
        bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch",
                               secret_path=tmp_path / "pw")
        assert repo.get_by_email("chef@kanzlei.ch") is None


class TestRefusals:
    def test_no_email_is_a_named_error(self, platform_db, tmp_path):
        with pytest.raises(bootstrap.BootstrapError) as excinfo:
            bootstrap.ensure_admin(platform_db, email="", secret_path=tmp_path / "pw")
        assert "PLATFORM_ADMIN_EMAIL" in str(excinfo.value)

    def test_an_address_without_an_at_sign_is_refused(self, platform_db, tmp_path):
        with pytest.raises(bootstrap.BootstrapError):
            bootstrap.ensure_admin(platform_db, email="chef", secret_path=tmp_path / "pw")

    def test_a_supplied_placeholder_password_is_refused(self, platform_db, tmp_path):
        """The values app.py:701 already refuses must not enter through here."""
        with pytest.raises(bootstrap.BootstrapError):
            bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch",
                                   password="change-me", secret_path=tmp_path / "pw")

    def test_a_supplied_strong_password_is_accepted_and_not_written_to_disk(
        self, platform_db, repo, tmp_path
    ):
        """An operator who brought their own secret does not need it echoed."""
        secret = tmp_path / "pw"
        bootstrap.ensure_admin(platform_db, email="chef@kanzlei.ch",
                               password="ein-sehr-langes-eigenes-passwort",
                               secret_path=secret)
        assert repo.authenticate("chef@kanzlei.ch", "ein-sehr-langes-eigenes-passwort")
        assert not secret.exists()
