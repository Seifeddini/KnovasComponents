"""Browser requests may not choose their own Knovas access groups."""

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


def _login(client, email: str, password: str):
    page = client.get("/login")
    return client.post(
        "/login",
        data={
            "login_name": email,
            "password": password,
            "csrf_token": _csrf_from(page.data.decode("utf-8")),
        },
        follow_redirects=False,
    )


@pytest.fixture
def client_with_broker(identity_app, identity_repo):
    """A signed-in Flask client whose JSON posts satisfy the CSRF gate."""
    password = "korrektes-pferd-batterie"
    user = identity_repo.create(
        email="gruppen@testco.example",
        display_name="Gruppen Test",
        password=password,
    )
    client = identity_app.test_client()
    assert _login(client, user.email, password).status_code == 302
    with client.session_transaction() as signed_in_session:
        client.environ_base["HTTP_X_CSRF_TOKEN"] = signed_in_session["csrf_token"]
    return client


def test_body_supplied_access_groups_are_rejected(client_with_broker):
    """A supplied group field is rejected rather than silently ignored."""
    response = client_with_broker.post(
        "/api/search",
        json={"query": "xx", "access_groups": ["litigation"]},
    )
    assert response.status_code == 400


def test_empty_access_groups_list_is_also_rejected(client_with_broker):
    response = client_with_broker.post(
        "/api/search",
        json={"query": "xx", "access_groups": []},
    )
    assert response.status_code == 400


def test_a_normal_request_is_unaffected(client_with_broker):
    response = client_with_broker.post("/api/search", json={"query": "xx"})
    assert response.status_code == 200
