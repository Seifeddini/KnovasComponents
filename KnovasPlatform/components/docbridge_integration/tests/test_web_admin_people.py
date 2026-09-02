"""The admin console's People tab (KC-B1-5).

Without this there is no way to create a second account, so the login built in
KC-B1-6 is usable only by whoever ran the bootstrap.
"""

import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}",
)


def _csrf_from(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start:html.index('"', start)]


def _sign_in(client, email, password="korrektes-pferd-batterie"):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"login_name": email, "password": password,
              "csrf_token": _csrf_from(page.data.decode("utf-8"))},
    )


@pytest.fixture
def admin(identity_repo):
    user = identity_repo.create(email="chef@kanzlei.ch", display_name="Chef",
                                password="korrektes-pferd-batterie")
    identity_repo.grant_role(user.id, "admin")
    return identity_repo.get(user.id)


@pytest.fixture
def member(identity_repo):
    user = identity_repo.create(email="anwalt@kanzlei.ch", display_name="Anwalt",
                                password="korrektes-pferd-batterie")
    identity_repo.grant_role(user.id, "member")
    return identity_repo.get(user.id)


@pytest.fixture
def as_admin(identity_client, admin):
    _sign_in(identity_client, "chef@kanzlei.ch")
    return identity_client


def _post(client, path, **fields):
    page = client.get("/admin/people")
    fields["csrf_token"] = _csrf_from(page.data.decode("utf-8"))
    return client.post(path, data=fields, follow_redirects=False)


class TestWhoMayOpenIt:
    def test_an_anonymous_visitor_is_sent_to_login(self, identity_client):
        response = identity_client.get("/admin/people")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_an_ordinary_member_is_refused(self, identity_client, member):
        _sign_in(identity_client, "anwalt@kanzlei.ch")
        assert identity_client.get("/admin/people").status_code == 403

    def test_an_administrator_gets_the_page(self, as_admin):
        assert as_admin.get("/admin/people").status_code == 200

    def test_a_member_cannot_create_a_user_through_the_post_route_either(
        self, identity_client, member
    ):
        """The page being hidden is not the control; the route is."""
        _sign_in(identity_client, "anwalt@kanzlei.ch")
        page = identity_client.get("/settings")
        response = identity_client.post(
            "/admin/people/create",
            data={"email": "neu@kanzlei.ch", "display_name": "Neu",
                  "password": "korrektes-pferd-batterie",
                  "csrf_token": _csrf_from(page.data.decode("utf-8"))},
        )
        assert response.status_code == 403


class TestListingPeople:
    def test_the_page_lists_existing_accounts(self, as_admin, member):
        assert "anwalt@kanzlei.ch" in as_admin.get("/admin/people").data.decode("utf-8")

    def test_the_page_never_shows_a_password_hash(self, as_admin, member):
        assert "$argon2id$" not in as_admin.get("/admin/people").data.decode("utf-8")


class TestCreating:
    def test_creating_an_account_adds_it(self, as_admin, identity_repo):
        _post(as_admin, "/admin/people/create", email="neu@kanzlei.ch",
              display_name="Neu", password="korrektes-pferd-batterie")
        assert identity_repo.get_by_email("neu@kanzlei.ch") is not None

    def test_a_created_account_must_change_its_password(self, as_admin, identity_repo):
        """An administrator picking someone else's password means two people
        know it. The first sign-in has to replace it."""
        _post(as_admin, "/admin/people/create", email="neu@kanzlei.ch",
              display_name="Neu", password="korrektes-pferd-batterie")
        assert identity_repo.get_by_email("neu@kanzlei.ch").must_change_password is True

    def test_a_weak_password_is_reported_not_swallowed(self, as_admin, identity_repo):
        response = _post(as_admin, "/admin/people/create", email="neu@kanzlei.ch",
                         display_name="Neu", password="kurz")
        assert response.status_code in (200, 400)
        assert identity_repo.get_by_email("neu@kanzlei.ch") is None

    def test_a_duplicate_address_is_reported(self, as_admin, member, identity_repo):
        _post(as_admin, "/admin/people/create", email="anwalt@kanzlei.ch",
              display_name="Zweiter", password="korrektes-pferd-batterie")
        assert identity_repo.get_by_email("anwalt@kanzlei.ch").display_name == "Anwalt"

    def test_creating_is_audited(self, as_admin, platform_db):
        _post(as_admin, "/admin/people/create", email="neu@kanzlei.ch",
              display_name="Neu", password="korrektes-pferd-batterie")
        rows = platform_db.execute(
            "SELECT actor_email_snapshot FROM audit_log WHERE action = 'user.created'"
        ).fetchall()
        assert len(rows) == 1
        assert str(rows[0][0]) == "chef@kanzlei.ch"


class TestDisabling:
    def test_disabling_an_account_stops_it_signing_in(self, as_admin, member, identity_repo):
        _post(as_admin, "/admin/people/disable", user_id=str(member.id))
        assert identity_repo.authenticate("anwalt@kanzlei.ch", "korrektes-pferd-batterie") is None

    def test_disabling_revokes_live_sessions(self, as_admin, member, platform_db, identity_app):
        """Leaver, end to end: the colleague is out on their next request."""
        other = identity_app.test_client()
        _sign_in(other, "anwalt@kanzlei.ch")
        assert other.get("/").status_code == 200
        _post(as_admin, "/admin/people/disable", user_id=str(member.id))
        assert other.get("/").status_code == 302

    def test_an_administrator_cannot_disable_themselves(self, as_admin, admin, identity_repo):
        """Locking the last administrator out of their own system is a support
        call, not a security control."""
        _post(as_admin, "/admin/people/disable", user_id=str(admin.id))
        assert identity_repo.get(admin.id).is_active

    def test_re_enabling_works(self, as_admin, member, identity_repo):
        _post(as_admin, "/admin/people/disable", user_id=str(member.id))
        _post(as_admin, "/admin/people/enable", user_id=str(member.id))
        assert identity_repo.authenticate("anwalt@kanzlei.ch", "korrektes-pferd-batterie")

    def test_disabling_is_audited(self, as_admin, member, platform_db):
        _post(as_admin, "/admin/people/disable", user_id=str(member.id))
        rows = platform_db.execute(
            "SELECT 1 FROM audit_log WHERE action = 'user.disabled'"
        ).fetchall()
        assert len(rows) == 1


class TestRolesAndGroups:
    def test_granting_a_role_takes_effect(self, as_admin, member, identity_repo):
        _post(as_admin, "/admin/people/roles", user_id=str(member.id),
              roles=["approver", "member"])
        assert "approver" in identity_repo.roles_of(member.id)

    def test_roles_are_replaced_not_merged(self, as_admin, member, identity_repo):
        _post(as_admin, "/admin/people/roles", user_id=str(member.id), roles=["approver"])
        assert identity_repo.roles_of(member.id) == {"approver"}

    def test_assigning_access_groups_takes_effect(self, as_admin, member, identity_repo):
        _post(as_admin, "/admin/people/groups", user_id=str(member.id),
              access_groups="litigation, ip")
        assert identity_repo.access_groups_of(member.id) == ("ip", "litigation")

    def test_clearing_access_groups_removes_them(self, as_admin, member, identity_repo):
        _post(as_admin, "/admin/people/groups", user_id=str(member.id),
              access_groups="litigation")
        _post(as_admin, "/admin/people/groups", user_id=str(member.id), access_groups="")
        assert identity_repo.access_groups_of(member.id) == ()

    def test_a_group_change_is_audited(self, as_admin, member, platform_db):
        _post(as_admin, "/admin/people/groups", user_id=str(member.id),
              access_groups="litigation")
        rows = platform_db.execute(
            "SELECT detail FROM audit_log WHERE action = 'user.access_groups_changed'"
        ).fetchall()
        assert rows[0][0]["access_groups"] == ["litigation"]


class TestResettingAPassword:
    def test_the_new_password_works_and_must_be_changed(self, as_admin, member, identity_repo):
        _post(as_admin, "/admin/people/reset-password", user_id=str(member.id),
              password="ein-ganz-neues-passwort")
        assert identity_repo.authenticate("anwalt@kanzlei.ch", "ein-ganz-neues-passwort")
        assert identity_repo.get(member.id).must_change_password is True

    def test_resetting_signs_the_person_out_everywhere(
        self, as_admin, member, identity_app
    ):
        """A reset is what you do when a credential may be compromised; leaving
        the old session alive would defeat it."""
        other = identity_app.test_client()
        _sign_in(other, "anwalt@kanzlei.ch")
        _post(as_admin, "/admin/people/reset-password", user_id=str(member.id),
              password="ein-ganz-neues-passwort")
        assert other.get("/").status_code == 302
