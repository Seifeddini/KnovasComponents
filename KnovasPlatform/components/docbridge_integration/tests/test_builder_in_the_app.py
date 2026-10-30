"""The builder inside the real Platform app (build plan step B0).

test_builder_shell.py exercises the blueprint on a bare app; this file checks
that the Platform registers it behind the real login and role check, and that
only builder roles see it in the sidebar.
"""
import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}",
)

PASSWORD = "korrektes-pferd-batterie"


def _csrf_from(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start:html.index('"', start)]


def _sign_in(client, email):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"login_name": email, "password": PASSWORD,
              "csrf_token": _csrf_from(page.data.decode("utf-8"))},
    )


def _person(repo, email, role):
    user = repo.create(email=email, display_name=email.split("@")[0], password=PASSWORD)
    repo.grant_role(user.id, role)
    return user


def test_an_anonymous_visitor_is_sent_to_login(identity_client):
    response = identity_client.get("/builder/ontologie")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_a_member_is_refused_and_not_offered_the_builder(identity_client, identity_repo):
    _person(identity_repo, "mitglied@example.org", "member")
    _sign_in(identity_client, "mitglied@example.org")
    assert identity_client.get("/builder/ontologie").status_code == 403
    assert identity_client.get("/builder/_styleguide").status_code == 403
    assert 'href="/builder/"' not in identity_client.get("/settings").get_data(as_text=True)


def test_an_admin_opens_every_stage_and_is_offered_the_builder(identity_client, identity_repo):
    _person(identity_repo, "admin@example.org", "admin")
    _sign_in(identity_client, "admin@example.org")
    assert 'href="/builder/"' in identity_client.get("/settings").get_data(as_text=True)
    for slug in ("arbeitsbereich", "verbinden", "ontologie", "prozesse", "quellen",
                 "ansichten", "pruefen", "betrieb", "_styleguide"):
        assert identity_client.get(f"/builder/{slug}").status_code == 200, slug
