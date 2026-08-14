"""Server-side sessions — the half of B1's leaver rule that acts on live users.

KC-B1-2. The shipped Platform put one boolean in a signed cookie
(`session['company_login_ok']`, app.py:978), which cannot be revoked: it stays
valid until it lapses. These tests pin the property that replaces it — a
disabled account loses its live session on the very next request.
"""

import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}",
)

from identity import sessions, users  # noqa: E402


@pytest.fixture
def repo(platform_db):
    return users.UserRepository(platform_db)


@pytest.fixture
def store(platform_db, repo):
    return sessions.SessionStore(platform_db, repo)


@pytest.fixture
def anna(repo):
    return repo.create(email="anna@kanzlei.ch", display_name="Anna",
                       password="korrektes-pferd-batterie")


class TestOpeningAndResolving:
    def test_a_new_session_resolves_to_its_user(self, store, anna):
        opened = store.open(anna, ip="10.0.0.5", user_agent="Firefox")
        assert store.resolve(opened.id).user.id == anna.id

    def test_an_unknown_session_id_resolves_to_nothing(self, store):
        import uuid
        assert store.resolve(uuid.uuid4()) is None

    def test_a_malformed_session_id_resolves_to_nothing_rather_than_raising(self, store):
        """The id comes from a cookie, so it is attacker-controlled."""
        assert store.resolve("'; DROP TABLE users; --") is None

    def test_the_session_records_where_it_came_from(self, store, anna, platform_db):
        store.open(anna, ip="10.0.0.5", user_agent="Firefox")
        row = platform_db.execute("SELECT ip, user_agent FROM sessions").fetchone()
        assert str(row[0]) == "10.0.0.5"
        assert row[1] == "Firefox"


class TestTheLeaverRule:
    def test_disabling_the_account_kills_the_live_session(self, store, repo, anna):
        opened = store.open(anna)
        repo.disable(anna.id)
        assert store.resolve(opened.id) is None

    def test_locking_the_account_kills_the_live_session(self, store, repo, anna):
        opened = store.open(anna)
        for _ in range(users.MAX_FAILED_ATTEMPTS):
            repo.authenticate("anna@kanzlei.ch", "falsch-falsch-falsch")
        assert store.resolve(opened.id) is None

    def test_re_enabling_does_not_resurrect_the_old_session(self, store, repo, anna):
        """A returning colleague logs in again; a revoked session stays revoked."""
        opened = store.open(anna)
        repo.disable(anna.id)
        store.resolve(opened.id)
        repo.enable(anna.id)
        assert store.resolve(opened.id) is None

    def test_revoke_all_ends_every_session_for_that_user(self, store, anna):
        first, second = store.open(anna), store.open(anna)
        store.revoke_all_for_user(anna.id)
        assert store.resolve(first.id) is None
        assert store.resolve(second.id) is None

    def test_revoke_all_leaves_other_users_alone(self, store, repo, anna):
        other = repo.create(email="bea@kanzlei.ch", display_name="Bea",
                            password="korrektes-pferd-batterie")
        mine, theirs = store.open(anna), store.open(other)
        store.revoke_all_for_user(anna.id)
        assert store.resolve(mine.id) is None
        assert store.resolve(theirs.id) is not None


class TestExpiryAndRevocation:
    def test_an_expired_session_does_not_resolve(self, store, anna, platform_db):
        opened = store.open(anna)
        platform_db.execute(
            "UPDATE sessions SET expires_at = now() - interval '1 second' WHERE id = %s",
            (str(opened.id),),
        )
        assert store.resolve(opened.id) is None

    def test_an_explicitly_revoked_session_does_not_resolve(self, store, anna):
        opened = store.open(anna)
        store.revoke(opened.id)
        assert store.resolve(opened.id) is None

    def test_resolving_refreshes_last_seen(self, store, anna, platform_db):
        opened = store.open(anna)
        platform_db.execute(
            "UPDATE sessions SET last_seen_at = now() - interval '1 hour' WHERE id = %s",
            (str(opened.id),),
        )
        before = platform_db.execute("SELECT last_seen_at FROM sessions").fetchone()[0]
        store.resolve(opened.id)
        after = platform_db.execute("SELECT last_seen_at FROM sessions").fetchone()[0]
        assert after > before

    def test_purge_removes_expired_rows_and_reports_how_many(self, store, anna, platform_db):
        opened = store.open(anna)
        platform_db.execute(
            "UPDATE sessions SET expires_at = now() - interval '1 day' WHERE id = %s",
            (str(opened.id),),
        )
        assert store.purge_expired() == 1
        assert platform_db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


class TestMultiFactorGate:
    def test_a_new_session_has_not_passed_mfa(self, store, anna):
        assert store.resolve(store.open(anna).id).mfa_passed is False

    def test_marking_mfa_passed_sticks(self, store, anna):
        opened = store.open(anna)
        store.mark_mfa_passed(opened.id)
        assert store.resolve(opened.id).mfa_passed is True
