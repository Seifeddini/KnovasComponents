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


# --- PMVP-40: two roles on the page (the founder, 2026-11-21) -------------------------------------------------

import re  # noqa: E402


def _details(html: str, person_id) -> str:
    """The details section of one person (up to the next one)."""
    start = html.index(f'data-person-detail="{person_id}"')
    end = html.find("data-person-detail=", start + 1)
    return html[start:end if end != -1 else len(html)]


def _roles_form(html: str, person_id) -> str:
    section = _details(html, person_id)
    start = section.index('action="/admin/people/roles"')
    return section[start:section.index("</form>", start)]


def _as_the_browser_sends_it(form: str, uncheck=()) -> list[str]:
    """The roles the form sends: its ticked boxes (less those unticked here) and its hidden fields."""
    ticked = re.findall(r'<input type="checkbox" name="roles" value="([^"]+)"\s*checked>', form)
    hidden = re.findall(r'<input type="hidden" name="roles" value="([^"]+)">', form)
    return [r for r in ticked if r not in uncheck] + hidden


class TestTwoRoles:
    """The page offers Administration (admin) and Mitglied (member) only; the console's other roles stay in the
    data, are shown read only, and survive a save. No gate changes."""

    def test_only_the_two_role_boxes_are_drawn(self, as_admin, member, identity_repo):
        identity_repo.grant_role(member.id, "approver")
        html = as_admin.get("/admin/people").data.decode("utf-8")
        boxes = re.findall(r'<input type="checkbox" name="roles" value="([^"]+)"', html)
        assert set(boxes) == {"admin", "member"}
        assert set(re.findall(r'<span class="role-choice-name">([^<]*)</span>', html)) == {"Administration", "Mitglied"}
        assert "Einlesen" not in html and "Experimente verwalten" not in html

    def test_the_filter_offers_the_two_and_the_search_matches_their_words(self, as_admin, admin, member):
        html = as_admin.get("/admin/people").data.decode("utf-8")
        select = html[html.index('<select id="people-role-filter">'):]
        select = select[:select.index("</select>")]
        assert re.findall(r'<option value="([^"]*)">([^<]*)</option>', select) == [
            ("", "Alle Rollen"), ("admin", "Administration"), ("member", "Mitglied")]
        rows = dict(re.findall(r'data-person-id="([^"]+)"\s*data-search="([^"]*)"', html))
        assert "Mitglied" in rows[str(member.id)]
        assert "Administration" in rows[str(admin.id)]

    def test_saving_keeps_the_roles_the_page_does_not_offer(self, as_admin, member, identity_repo):
        for role in ("approver", "ingestion_manager", "experimenter", "experiments_manager"):
            identity_repo.grant_role(member.id, role)
        html = as_admin.get("/admin/people").data.decode("utf-8")
        form = _roles_form(html, member.id)
        # The administrator makes the member an administrator too, through the page's own form.
        sent = _as_the_browser_sends_it(form) + ["admin"]
        _post(as_admin, "/admin/people/roles", user_id=str(member.id), roles=sent)
        assert identity_repo.roles_of(member.id) == {"admin", "member", "approver", "ingestion_manager",
                                                     "experimenter", "experiments_manager"}
        # ... and takes both offered roles away again: the others stay.
        form = _roles_form(as_admin.get("/admin/people").data.decode("utf-8"), member.id)
        _post(as_admin, "/admin/people/roles", user_id=str(member.id),
              roles=_as_the_browser_sends_it(form, uncheck=("admin", "member")))
        assert identity_repo.roles_of(member.id) == {"approver", "ingestion_manager", "experimenter",
                                                     "experiments_manager"}

    def test_a_person_with_only_a_role_the_page_does_not_offer_shows_it_read_only(self, as_admin, identity_repo):
        from conftest import _person

        approver = _person(identity_repo, "pruefer@kanzlei.ch", "Pruefer", "approver")
        html = as_admin.get("/admin/people").data.decode("utf-8")
        details = _details(html, approver.id)
        shown = re.search(r"<p[^>]*data-other-roles>(.*?)</p>", details, flags=re.S)
        assert shown and "weitere Rollen" in shown.group(1) and "approver" in shown.group(1)
        assert 'type="checkbox" name="roles" value="approver"' not in details
        assert re.findall(r'<input type="checkbox" name="roles" value="([^"]+)"\s*checked>',
                          _roles_form(html, approver.id)) == []
        # In the list the person holds no role the page offers.
        row = html[html.index(f'data-person-id="{approver.id}"'):]
        row = row[:row.index("Verwalten")]
        assert re.search(r"Rollen:</span>\s*—", row)

    def test_saving_keeps_a_builder_role_and_a_hidden_console_role(self, as_admin, member, identity_repo):
        """A role outside ASSIGNABLE_ROLES (the Builder's) is never this route's to take away: saving through the page's
        two boxes keeps it, and so it does for the console's approver, which the page sends back."""
        identity_repo.grant_role(member.id, "approver")
        identity_repo.grant_role(member.id, "builder_author")
        form = _roles_form(as_admin.get("/admin/people").data.decode("utf-8"), member.id)
        _post(as_admin, "/admin/people/roles", user_id=str(member.id),
              roles=_as_the_browser_sends_it(form) + ["admin"])
        assert identity_repo.roles_of(member.id) == {"admin", "member", "approver", "builder_author"}
        form = _roles_form(as_admin.get("/admin/people").data.decode("utf-8"), member.id)
        _post(as_admin, "/admin/people/roles", user_id=str(member.id),
              roles=_as_the_browser_sends_it(form, uncheck=("admin",)))
        assert identity_repo.roles_of(member.id) == {"member", "approver", "builder_author"}
        # The route alone, sent nothing but member: the Builder role stays; approver, one it may give, goes.
        _post(as_admin, "/admin/people/roles", user_id=str(member.id), roles=["member"])
        assert identity_repo.roles_of(member.id) == {"member", "builder_author"}
