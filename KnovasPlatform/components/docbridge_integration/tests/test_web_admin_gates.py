"""The role gate of every route of Personen, Zugriffsgruppen and Dokumente, asked live (PMVP-40, FU-17).

Each route the cards list for these three pages (L2-PLATFORM-ADMIN, L2-PLATFORM-ACCESS), the console's entry
(GET /admin/, which leads to Personen) and System (FU-17 names its gate with the people routes). Every one of
them is require_admin (each route's N-1 on the cards):

- the gate itself: the view the app registered for the route lets in the role admin and no other (the set
  _require_roles puts on it, allowed_roles), so a widened gate fails here by name;
- every role the console can give except admin (admin.ASSIGNABLE_ROLES: approver, ingestion_manager, member,
  experimenter, experiments_manager), and an account with no role at all: 403, with a valid token and a complete
  form, and nothing run. This is what catches a missing or widened gate;
- an administrator: 200, with the smallest form the route accepts;
- signed out: 302 to the sign-in, nothing run. This alone cannot catch a missing route gate: the app's own
  sign-in check sends a signed-out visitor away before any route runs.

The guarded actions (a document's groups, a folder rule, erasing the corpus) run with the administrator's bypass
switched off, so they are queued for a second person and never carried out. The three POST routes of a document's
fields drawer answer JSON; an administrator's 200 there is shown in test_web_admin_documents.py (TestDocFieldsLive).

RB-3, RB-4: the Rollen page and its forms are here too (FU-17: every console route has a gate test); ``{role}`` in a
path is a role of the tenant's own without a Core group, ``{mitglied}`` the role Mitglied. The people page's roles
form sends role ids (``role_id``). Their behaviour is pinned in test_rb34_roles_page_acceptance.py.
"""

from __future__ import annotations

import pytest

from conftest import PLATFORM_DB_TEST_DSN, DummyKnovasClient, platform_db_reachable
from web_interface.admin import ASSIGNABLE_ROLES

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
    "/admin/roles",
    "/admin/roles/{role}",
]

#: The forms: the route and the smallest form it accepts ({person} is the id of the account the form acts on).
FORMS = {
    "/admin/people/create": {"email": "neu@kanzlei.ch", "display_name": "Neu", "password": PASSWORD},
    "/admin/people/disable": {"user_id": "{person}"},
    "/admin/people/enable": {"user_id": "{person}"},
    "/admin/people/roles": {"user_id": "{person}", "role_id": "{mitglied}"},
    "/admin/people/groups": {"user_id": "{person}", "access_groups": "litigation"},
    "/admin/people/reset-password": {"user_id": "{person}", "password": "ein-ganz-neues-passwort"},
    "/admin/people/sign-out": {"user_id": "{person}"},
    "/admin/documents/acl": {"pointer": "rc-sync/a.docx", "access_group": "g-lit"},
    "/admin/documents/purge": {"confirm_client_id": "tenant-a"},
    "/admin/access-groups/create": {"name": "personal"},
    "/admin/folder-rules/save": {"pointer_prefix": "rc-sync/mandate/", "access_group": "g-lit"},
    "/admin/folder-rules/delete": {"rule_id": "r1"},
    "/admin/roles/create": {"name": "Partner", "function": ["approver"]},
    "/admin/roles/connect": {},
    "/admin/roles/{role}/rename": {"name": "Sekretariat Zürich"},
    "/admin/roles/{role}/functions": {"function": ["approver", "ingestion_manager"]},
    "/admin/roles/{role}/members/add": {"user_id": "{person}"},
    "/admin/roles/{role}/members/remove": {"user_id": "{person}"},
    "/admin/roles/{role}/delete": {},
}

#: A role id for the routes of one role where no role of the tenant's own is needed (the gate answers first).
NO_ROLE = "00000000-0000-0000-0000-000000000000"


def _fill(value, **ids):
    """A path or a form value with its {placeholders} filled; anything that is not text as it is."""
    return value.format(**ids) if isinstance(value, str) else value

#: The fields drawer and the Feldfilter: JSON, pointer in the body.
JSON_ROUTES = [
    "/admin/documents/fields/read",
    "/admin/documents/fields/edit",
    "/admin/documents/fields/find",
]

#: Who the gate of every route here lets in (require_admin, each route's N-1 on the cards).
ADMITTED = frozenset({"admin"})
#: Who it must refuse: every other role the console can give, and an account with no role (None).
REFUSED = [role for role in ASSIGNABLE_ROLES if role not in ADMITTED] + [None]
ROUTES = [(path, "GET") for path in PAGES] + [(path, "POST") for path in sorted(FORMS)] + [
    (path, "POST") for path in JSON_ROUTES]


def _role_id(role):
    return role or "no-role"


class _Client(DummyKnovasClient):
    """The conftest's fake Knovas, which can also create a group (the fake has no such call)."""

    def create_access_group(self, name, parent=None):
        return {"group_id": "g-new", "name": name, "parent_id": parent}

    def access_groups(self):
        """The fake's groups and the tenant's administrator group (RB-4's «Mit Knovas verbinden» reads it)."""
        return super().access_groups() + [
            {"group_id": "g-admins", "name": "Administratoren", "parent_id": None, "is_admin": True}]


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


@pytest.fixture
def role(identity_repo):
    """A role of the tenant's own, without a Core group: its forms make no Core call."""
    from identity.roles import RoleStore

    return RoleStore(identity_repo._conn).create_role(name="Sekretariat", functions={"approver"})


def _ids(identity_repo, role=None, person=None):
    from identity.roles import RoleStore

    return {"role": str(role.id) if role is not None else NO_ROLE,
            "mitglied": str(RoleStore(identity_repo._conn).mitglied().id),
            "person": str(person.id) if person is not None else ""}


def _roles_state(identity_repo):
    from identity.roles import RoleStore

    return [(r.id, r.name, sorted(r.functions), r.core_group_id, sorted(r.members))
            for r in RoleStore(identity_repo._conn).list_roles()]


def _sent_to_sign_in(response) -> bool:
    return response.status_code == 302 and "/login" in response.headers.get("Location", "")


class TestSignedOut:
    @pytest.mark.parametrize("path", PAGES)
    def test_a_page_sends_a_signed_out_visitor_to_sign_in(self, app, path):
        assert _sent_to_sign_in(app.test_client().get(_fill(path, role=NO_ROLE)))

    @pytest.mark.parametrize("path", sorted(FORMS))
    def test_a_form_sends_a_signed_out_visitor_to_sign_in_and_runs_nothing(self, app, path, member,
                                                                          identity_repo):
        before = (identity_repo.get(member.id).is_active, set(identity_repo.roles_of(member.id)),
                  _roles_state(identity_repo))
        response = app.test_client().post(_fill(path, role=NO_ROLE), data={"user_id": str(member.id)})
        assert _sent_to_sign_in(response)
        assert (identity_repo.get(member.id).is_active, set(identity_repo.roles_of(member.id)),
                _roles_state(identity_repo)) == before
        assert DummyKnovasClient.last_instance.acl_calls == []

    @pytest.mark.parametrize("path", JSON_ROUTES)
    def test_the_fields_routes_send_a_signed_out_visitor_to_sign_in(self, app, path):
        assert _sent_to_sign_in(app.test_client().post(path, json={"pointer": "k/a.pdf"}))


class TestTheGate:
    def test_the_refused_are_every_role_the_console_gives_but_admin_and_no_role(self):
        assert set(REFUSED) == (set(ASSIGNABLE_ROLES) - ADMITTED) | {None}
        assert {"approver", "ingestion_manager", "member", "experimenter", "experiments_manager"} <= set(REFUSED)

    @pytest.mark.parametrize("path, method", ROUTES)
    def test_the_route_lets_in_the_administrator_alone(self, app, path, method):
        """The roles the app's registered view checks (allowed_roles, put there by _require_roles)."""
        endpoint, _ = app.url_map.bind("localhost").match(_fill(path, role=NO_ROLE), method=method)
        assert getattr(app.view_functions[endpoint], "allowed_roles", None) == ADMITTED, endpoint


def _store(identity_repo):
    from identity.roles import RoleStore

    return RoleStore(identity_repo._conn)


def _person_with(identity_repo, role):
    """A signed-in account holding exactly ``role`` (None: no role at all)."""
    email = f"{(role or 'ohne-rolle').replace('_', '-')}@kanzlei.ch"
    user = identity_repo.create(email=email, display_name="Gast", password=PASSWORD)
    if role:
        identity_repo.grant_role(user.id, role)
    return identity_repo.get(user.id)


def _token(client):
    with client.session_transaction() as sess:
        return sess["csrf_token"]


class TestRefused:
    """403 for every role the gate does not name, and for an account without a role; nothing runs."""

    @pytest.mark.parametrize("role", REFUSED, ids=_role_id)
    @pytest.mark.parametrize("path", PAGES)
    def test_every_role_but_the_administrator_is_refused_a_page(self, app, identity_repo, path, role):
        person = _person_with(identity_repo, role)
        client = _sign_in(app.test_client(), person.email)
        assert client.get(_fill(path, role=NO_ROLE)).status_code == 403   # signed out it would be 302

    @pytest.mark.parametrize("role", REFUSED, ids=_role_id)
    @pytest.mark.parametrize("path", sorted(FORMS))
    def test_every_role_but_the_administrator_is_refused_a_form_and_nothing_runs(self, app, admin, identity_repo,
                                                                                 path, role):
        """The gate refuses before anything else: the token is valid, the form complete."""
        person = _person_with(identity_repo, role)
        client = _sign_in(app.test_client(), person.email)
        sekretariat = _store(identity_repo).create_role(name="Sekretariat", functions={"approver"})
        ids = _ids(identity_repo, sekretariat, admin)
        fields = {k: _fill(v, **ids) for k, v in FORMS[path].items()}
        before = (identity_repo.get(admin.id).is_active, set(identity_repo.roles_of(admin.id)),
                  _roles_state(identity_repo))
        response = client.post(_fill(path, **ids), data=dict(fields, csrf_token=_token(client)))
        assert response.status_code == 403
        assert (identity_repo.get(admin.id).is_active, set(identity_repo.roles_of(admin.id)),
                _roles_state(identity_repo)) == before
        assert identity_repo.get_by_email("neu@kanzlei.ch") is None
        assert DummyKnovasClient.last_instance.acl_calls == []

    @pytest.mark.parametrize("role", REFUSED, ids=_role_id)
    @pytest.mark.parametrize("path", JSON_ROUTES)
    def test_every_role_but_the_administrator_is_refused_the_fields_routes(self, app, identity_repo, path, role):
        person = _person_with(identity_repo, role)
        client = _sign_in(app.test_client(), person.email)
        response = client.post(path, json={"pointer": "k/a.pdf", "set": {"title": "x"}},
                               headers={"X-CSRF-Token": _token(client)})
        assert response.status_code == 403


class TestAdministrator:
    @pytest.fixture
    def as_admin(self, app, admin, platform_db, identity_repo):
        from identity.approvals import ApprovalService

        # Guarded actions are queued, never carried out, in these tests.
        ApprovalService(platform_db, identity_repo).set_admin_bypass(False, by=admin)
        return _sign_in(app.test_client(), admin.email)

    @pytest.mark.parametrize("path", PAGES)
    def test_an_administrator_gets_the_page(self, as_admin, identity_repo, role, path):
        response = as_admin.get(_fill(path, **_ids(identity_repo, role)), follow_redirects=True)
        assert response.status_code == 200
        if path == "/admin/":
            assert response.request.path == "/admin/people"

    @pytest.mark.parametrize("path", sorted(FORMS))
    def test_an_administrator_gets_through_the_form(self, as_admin, member, identity_repo, role, path):
        token = _csrf_from(as_admin.get("/admin/people").data.decode("utf-8"))
        ids = _ids(identity_repo, role, member)
        fields = {k: _fill(v, **ids) for k, v in FORMS[path].items()}
        response = as_admin.post(_fill(path, **ids), data=dict(fields, csrf_token=token))
        assert response.status_code == 200, response.data.decode("utf-8")[:400]
        assert DummyKnovasClient.last_instance.acl_calls == [], "a guarded action is queued, not run"
