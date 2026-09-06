"""The search UI behind per-user email + password login (KC-B1-6).

This is the step that makes the identity package visible: until it lands, the
Platform still authenticates the whole firm with one shared credential.

MFA and OIDC are deliberately out — email and password only.
"""

import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}",
)

from identity import users  # noqa: E402


@pytest.fixture
def anna(identity_repo):
    return identity_repo.create(
        email="anna@kanzlei.ch", display_name="Anna Meier",
        password="korrektes-pferd-batterie",
    )


def _login(client, email, password):
    page = client.get("/login")
    token = _csrf_from(page.data.decode("utf-8"))
    return client.post(
        "/login",
        data={"login_name": email, "password": password, "csrf_token": token},
        follow_redirects=False,
    )


def _csrf_from(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start:html.index('"', start)]


class TestSigningIn:
    def test_the_search_page_redirects_an_anonymous_visitor_to_login(self, identity_client):
        response = identity_client.get("/")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_an_api_call_from_an_anonymous_visitor_is_401_not_a_redirect(self, identity_client):
        assert identity_client.post("/api/search", json={"query": "x"}).status_code == 401

    def test_the_right_email_and_password_sign_in(self, identity_client, anna):
        response = _login(identity_client, "anna@kanzlei.ch", "korrektes-pferd-batterie")
        assert response.status_code == 302

    def test_after_signing_in_the_search_page_is_served(self, identity_client, anna):
        _login(identity_client, "anna@kanzlei.ch", "korrektes-pferd-batterie")
        assert identity_client.get("/").status_code == 200

    def test_the_email_is_matched_case_insensitively(self, identity_client, anna):
        assert _login(identity_client, "Anna@Kanzlei.CH",
                      "korrektes-pferd-batterie").status_code == 302

    def test_a_wrong_password_does_not_sign_in(self, identity_client, anna):
        response = _login(identity_client, "anna@kanzlei.ch", "falsch-falsch-falsch")
        assert response.status_code == 200  # the form again, not a redirect

    def test_an_unknown_address_gives_the_same_message_as_a_wrong_password(
        self, identity_client, anna
    ):
        """The login form must not reveal who has an account."""
        wrong_password = _login(identity_client, "anna@kanzlei.ch", "falsch-falsch-falsch")
        no_account = _login(identity_client, "niemand@kanzlei.ch", "falsch-falsch-falsch")
        assert wrong_password.data == no_account.data

    def test_a_disabled_account_cannot_sign_in(self, identity_client, identity_repo, anna):
        identity_repo.disable(anna.id)
        assert _login(identity_client, "anna@kanzlei.ch",
                      "korrektes-pferd-batterie").status_code == 200


class TestTheSessionIsServerSide:
    def test_disabling_the_account_ends_the_live_session(
        self, identity_client, identity_repo, anna
    ):
        """The whole point of B1's leaver rule: not at cookie expiry, now."""
        _login(identity_client, "anna@kanzlei.ch", "korrektes-pferd-batterie")
        assert identity_client.get("/").status_code == 200
        identity_repo.disable(anna.id)
        assert identity_client.get("/").status_code == 302

    def test_signing_out_ends_the_session(self, identity_client, anna):
        _login(identity_client, "anna@kanzlei.ch", "korrektes-pferd-batterie")
        # settings.html carries the logout form and its hidden token.
        page = identity_client.get("/settings")
        identity_client.post("/logout", data={"csrf_token": _csrf_from(page.data.decode())})
        assert identity_client.get("/").status_code == 302

    def test_a_forged_session_cookie_does_not_authenticate(self, identity_client, anna):
        identity_client.set_cookie("session", "not-a-real-session")
        assert identity_client.get("/").status_code == 302


class TestTheSharedLoginIsGone:
    def test_the_old_shared_credential_no_longer_works(self, identity_client):
        assert _login(identity_client, "healthuser", "healthpass123").status_code == 200

    def test_starting_with_both_identity_and_a_shared_login_is_refused(self, tmp_path, monkeypatch):
        """A deployment must not silently run both auth paths after an upgrade."""
        from web_interface import app as web_app
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            'web:\n'
            '  secret_key: "a-strong-secret-for-tests-0123456789"\n'
            '  login:\n'
            '    enabled: true\n'
            '    username: "company"\n'
            '    password: "a-real-shared-password"\n'
            'identity:\n'
            '  enabled: true\n'
            'api:\n'
            '  base_url: "http://example.test"\n',
            encoding="utf-8",
        )
        with pytest.raises(RuntimeError, match="(?i)shared"):
            web_app.create_app(str(config_path))


class TestWhoIsSignedIn:
    def test_the_page_shows_the_signed_in_person(self, identity_client, anna):
        _login(identity_client, "anna@kanzlei.ch", "korrektes-pferd-batterie")
        assert "Anna Meier" in identity_client.get("/settings").data.decode("utf-8")

    def test_a_forced_password_change_redirects_to_the_change_form(
        self, identity_client, identity_repo
    ):
        identity_repo.create(email="chef@kanzlei.ch", display_name="Chef",
                             password="korrektes-pferd-batterie",
                             must_change_password=True)
        _login(identity_client, "chef@kanzlei.ch", "korrektes-pferd-batterie")
        response = identity_client.get("/")
        assert response.status_code == 302
        assert "/account/password" in response.headers["Location"]

    def test_changing_the_password_clears_the_block(self, identity_client, identity_repo):
        identity_repo.create(email="chef@kanzlei.ch", display_name="Chef",
                             password="korrektes-pferd-batterie",
                             must_change_password=True)
        _login(identity_client, "chef@kanzlei.ch", "korrektes-pferd-batterie")
        page = identity_client.get("/account/password")
        identity_client.post(
            "/account/password",
            data={
                "current_password": "korrektes-pferd-batterie",
                "new_password": "ein-ganz-neues-passwort",
                "csrf_token": _csrf_from(page.data.decode("utf-8")),
            },
        )
        assert identity_client.get("/").status_code == 200
