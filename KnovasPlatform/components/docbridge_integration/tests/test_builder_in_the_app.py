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


def test_an_import_needs_the_csrf_header_and_lands_in_the_database(identity_client, identity_repo, platform_db):
    import io

    from builder import package
    from builder_harness import SAMPLE_PACKAGE

    data = package.zip_files(package.files_from_directory(SAMPLE_PACKAGE))
    _person(identity_repo, "admin@example.org", "admin")
    _sign_in(identity_client, "admin@example.org")

    def upload():
        return {"package": (io.BytesIO(data), "paket.zip"), "name": "Beispiel AG"}

    refused = identity_client.post("/builder/api/import", data=upload(), content_type="multipart/form-data")
    assert refused.status_code == 403

    page = identity_client.get("/builder/arbeitsbereich").get_data(as_text=True)
    token = page.split('name="csrf-token" content="', 1)[1].split('"', 1)[0]
    created = identity_client.post("/builder/api/import", data=upload(), content_type="multipart/form-data",
                                   headers={"X-CSRF-Token": token})
    assert created.status_code == 201
    workspace_url = created.get_json()["url"]
    assert platform_db.execute("SELECT count(*) FROM builder_workspace").fetchone()[0] == 1
    assert "Beispiel AG" in identity_client.get(workspace_url).get_data(as_text=True)
    exported = identity_client.get(workspace_url + "/export")
    assert exported.status_code == 200 and exported.mimetype == "application/zip"


def test_creating_a_workspace_needs_the_csrf_header_and_lands_in_the_database(identity_client, identity_repo, platform_db):
    _person(identity_repo, "admin@example.org", "admin")
    _sign_in(identity_client, "admin@example.org")
    refused = identity_client.post("/builder/api/workspaces", data={"name": "Muster AG"})
    assert refused.status_code == 403

    page = identity_client.get("/builder/arbeitsbereich").get_data(as_text=True)
    token = page.split('name="csrf-token" content="', 1)[1].split('"', 1)[0]
    created = identity_client.post("/builder/api/workspaces", data={"name": "Muster AG"},
                                   headers={"X-CSRF-Token": token})
    assert created.status_code == 201
    assert platform_db.execute("SELECT count(*) FROM builder_workspace").fetchone()[0] == 1
    assert "Muster AG" in identity_client.get(created.get_json()["url"]).get_data(as_text=True)


def test_an_admin_opens_every_stage_and_is_offered_the_builder(identity_client, identity_repo):
    _person(identity_repo, "admin@example.org", "admin")
    _sign_in(identity_client, "admin@example.org")
    assert 'href="/builder/"' in identity_client.get("/settings").get_data(as_text=True)
    for slug in ("arbeitsbereich", "verbinden", "ontologie", "prozesse", "quellen",
                 "ansichten", "pruefen", "betrieb", "_styleguide"):
        assert identity_client.get(f"/builder/{slug}").status_code == 200, slug


def test_an_edit_needs_the_csrf_header_and_is_written_with_its_actor(identity_client, identity_repo, platform_db):
    _person(identity_repo, "admin@example.org", "admin")
    _sign_in(identity_client, "admin@example.org")
    page = identity_client.get("/builder/arbeitsbereich").get_data(as_text=True)
    token = page.split('name="csrf-token" content="', 1)[1].split('"', 1)[0]
    created = identity_client.post("/builder/api/workspaces", data={"name": "Muster AG"}, headers={"X-CSRF-Token": token})
    workspace_id = created.get_json()["url"].rsplit("/", 1)[1]
    op = {"op": "type.create", "key": "Invoice", "parent": "euo:Agreement"}
    assert identity_client.post(f"/builder/api/w/{workspace_id}/ontology", json=op).status_code == 403
    edited = identity_client.post(f"/builder/api/w/{workspace_id}/ontology", json=op, headers={"X-CSRF-Token": token})
    assert edited.status_code == 200
    row = platform_db.execute(
        "SELECT c.path, c.before, c.after, c.batch, u.email FROM builder_change c JOIN users u ON u.id = c.actor"
        " WHERE c.workspace = %s AND c.batch IS NOT NULL", (workspace_id,)).fetchone()
    assert row[0] == "ontology.types/Invoice" and row[1] is None and row[2]["key"] == "Invoice" and row[4] == "admin@example.org"
    undone = identity_client.post(f"/builder/api/w/{workspace_id}/undo", headers={"X-CSRF-Token": token})
    assert undone.status_code == 200 and undone.get_json()["history"] == {"undo": 0, "redo": 1}
    assert "Noch keine Typen" in identity_client.get("/builder/ontologie").get_data(as_text=True)
