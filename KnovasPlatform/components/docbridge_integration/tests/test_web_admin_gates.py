"""The role gate of every route of Personen, Zugriffsgruppen and Dokumente, asked live (PMVP-40, FU-17).

Each route the Admin card lists for these three pages, the console's entry (GET /admin/, which leads to
Personen) and System (FU-17 names its gate with the people routes) is asked three times through the real app
and the real sign-in:

- signed out: 302 to the sign-in, nothing run;
- as a member (the role every account gets): 403;
- as an administrator, the role the gate names (require_admin): 200.

The administrator's requests are real requests with a valid CSRF token and the smallest form the route
accepts. The guarded actions (a document's groups, a folder rule, erasing the corpus) run with the
administrator's bypass switched off, so they are queued for a second person and never carried out.

The three POST routes of a document's fields drawer answer JSON; a member's 403 and an administrator's 200
are shown in test_web_admin_documents.py (TestDocFieldsLive), the signed-out visitor here.
"""

from __future__ import annotations

import pytest

from conftest import PLATFORM_DB_TEST_DSN, DummyKnovasClient, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}",
)

PASSWORD = "korrektes-pferd-batterie"

#: The pages: GET, nothing to send.
PAGES = [
    "/admin/",
    "/admin/people",
    "/admin/system",
    "/admin/documents",
    "/admin/documents/page",
    "/admin/access-groups",
]

#: The forms: the route and the smallest form it accepts ({person} is the id of the account the form acts on).
FORMS = {
    "/admin/people/create": {"email": "neu@kanzlei.ch", "display_name": "Neu", "password": PASSWORD},
    "/admin/people/disable": {"user_id": "{person}"},
    "/admin/people/enable": {"user_id": "{person}"},
    "/admin/people/roles": {"user_id": "{person}", "roles": ["member", "approver"]},
    "/admin/people/groups": {"user_id": "{person}", "access_groups": "litigation"},
    "/admin/people/reset-password": {"user_id": "{person}", "password": "ein-ganz-neues-passwort"},
    "/admin/people/sign-out": {"user_id": "{person}"},
    "/admin/documents/acl": {"pointer": "rc-sync/a.docx", "access_group": "g-lit"},
    "/admin/documents/purge": {"confirm_client_id": "tenant-a"},
    "/admin/access-groups/create": {"name": "personal"},
    "/admin/folder-rules/save": {"pointer_prefix": "rc-sync/mandate/", "access_group": "g-lit"},
    "/admin/folder-rules/delete": {"rule_id": "r1"},
}

#: The fields drawer and the Feldfilter: JSON, pointer in the body.
JSON_ROUTES = [
    "/admin/documents/fields/read",
    "/admin/documents/fields/edit",
    "/admin/documents/fields/find",
]


class _Client(DummyKnovasClient):
    """The conftest's fake Knovas, which can also create a group (the fake has no such call)."""

    def create_access_group(self, name, parent=None):
        return {"group_id": "g-new", "name": name, "parent_id": parent}


def _csrf_from(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start:html.index('"', start)]


def _sign_in(client, email):
    page = client.get("/login")
    client.post("/login", data={"login_name": email, "password": PASSWORD,
                                "csrf_token": _csrf_from(page.data.decode("utf-8"))})
    return client


@pytest.fixture
def app(platform_db, tmp_path, monkeypatch):
    from conftest import _identity_app

    return _identity_app(platform_db, tmp_path, monkeypatch, client_cls=_Client)


@pytest.fixture
def admin(identity_repo):
    from conftest import _person

    return _person(identity_repo, "chef@kanzlei.ch", "Chef", "admin")


@pytest.fixture
def member(identity_repo):
    from conftest import _person

    return _person(identity_repo, "anwalt@kanzlei.ch", "Anwalt", "member")


def _sent_to_sign_in(response) -> bool:
    return response.status_code == 302 and "/login" in response.headers.get("Location", "")


class TestSignedOut:
    @pytest.mark.parametrize("path", PAGES)
    def test_a_page_sends_a_signed_out_visitor_to_sign_in(self, app, path):
        assert _sent_to_sign_in(app.test_client().get(path))

    @pytest.mark.parametrize("path", sorted(FORMS))
    def test_a_form_sends_a_signed_out_visitor_to_sign_in_and_runs_nothing(self, app, path, member,
                                                                          identity_repo):
        before = (identity_repo.get(member.id).is_active, set(identity_repo.roles_of(member.id)))
        response = app.test_client().post(path, data={"user_id": str(member.id)})
        assert _sent_to_sign_in(response)
        assert (identity_repo.get(member.id).is_active, set(identity_repo.roles_of(member.id))) == before
        assert DummyKnovasClient.last_instance.acl_calls == []

    @pytest.mark.parametrize("path", JSON_ROUTES)
    def test_the_fields_routes_send_a_signed_out_visitor_to_sign_in(self, app, path):
        assert _sent_to_sign_in(app.test_client().post(path, json={"pointer": "k/a.pdf"}))


class TestMember:
    @pytest.mark.parametrize("path", PAGES)
    def test_a_member_is_refused_a_page(self, app, member, path):
        client = _sign_in(app.test_client(), member.email)
        assert client.get("/").status_code == 200, "the member is signed in"
        assert client.get(path).status_code == 403

    @pytest.mark.parametrize("path", sorted(FORMS))
    def test_a_member_is_refused_a_form_and_nothing_runs(self, app, member, admin, path, identity_repo):
        """The gate refuses before anything else: the member's token is valid, the form complete."""
        client = _sign_in(app.test_client(), member.email)
        token = _csrf_from(client.get("/settings").data.decode("utf-8"))
        fields = {k: (v.format(person=admin.id) if isinstance(v, str) else v)
                  for k, v in FORMS[path].items()}
        before = (identity_repo.get(admin.id).is_active, set(identity_repo.roles_of(admin.id)))
        response = client.post(path, data=dict(fields, csrf_token=token))
        assert response.status_code == 403
        assert (identity_repo.get(admin.id).is_active, set(identity_repo.roles_of(admin.id))) == before
        assert identity_repo.get_by_email("neu@kanzlei.ch") is None
        assert DummyKnovasClient.last_instance.acl_calls == []


class TestAdministrator:
    @pytest.fixture
    def as_admin(self, app, admin, platform_db, identity_repo):
        from identity.approvals import ApprovalService

        # Guarded actions are queued, never carried out, in these tests.
        ApprovalService(platform_db, identity_repo).set_admin_bypass(False, by=admin)
        return _sign_in(app.test_client(), admin.email)

    @pytest.mark.parametrize("path", PAGES)
    def test_an_administrator_gets_the_page(self, as_admin, path):
        response = as_admin.get(path, follow_redirects=True)
        assert response.status_code == 200
        if path == "/admin/":
            assert response.request.path == "/admin/people"

    @pytest.mark.parametrize("path", sorted(FORMS))
    def test_an_administrator_gets_through_the_form(self, as_admin, member, path):
        token = _csrf_from(as_admin.get("/admin/people").data.decode("utf-8"))
        fields = {k: (v.format(person=member.id) if isinstance(v, str) else v)
                  for k, v in FORMS[path].items()}
        response = as_admin.post(path, data=dict(fields, csrf_token=token))
        assert response.status_code == 200, response.data.decode("utf-8")[:400]
        assert DummyKnovasClient.last_instance.acl_calls == [], "a guarded action is queued, not run"
