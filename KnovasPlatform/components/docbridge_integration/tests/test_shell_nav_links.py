"""The links between the areas follow the routes' own checks (PMVP-11), beside the frozen acceptance tests.

test_pmvp11_navigation_acceptance.py pins the menu for a member, an approver and an administrator. Held here,
against the real app and the identity database:

1. for every role a person can be given, the link «Verwaltung», the entries of the Admin menu and the link
   «Builder» are exactly what the server lets that person open (the link is not the check, and the two cannot
   drift apart);
2. "may open" is read from the routes' own gates, not from a second list of roles: change a route's gate and the
   menu follows; a route that is not there, or a gate that cannot be read, draws no link;
3. the area follows the address: every page under /admin shows Verwaltung, also when a POST draws the page again;
   every other page shows the Workspace;
4. deciding the links writes no person's address or name into a log line.
"""
from __future__ import annotations

import logging
import re

import pytest

pytest.importorskip("flask")

from flask import url_for  # noqa: E402

import conftest  # noqa: E402
from conftest import platform_db_reachable  # noqa: E402

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

# the Admin pages in the menu's order; Dokumentfelder is drawn only while Knovas offers document fields
ADMIN_PAGES = ["admin.people", "admin.documents", "admin.access_groups", "admin.doc_fields", "admin.ingestion",
               "admin.system", "admin.approvals"]
NAMES = {"admin.people": "Personen", "admin.documents": "Dokumente", "admin.access_groups": "Zugriffsgruppen",
         "admin.doc_fields": "Dokumentfelder", "admin.ingestion": "Ingestion", "admin.system": "System",
         "admin.approvals": "Freigaben"}
# every role the console can give a person (admin.ASSIGNABLE_ROLES), alone, and two that are held together
ROLE_SETS = [("admin",), ("approver",), ("ingestion_manager",), ("member",), ("experimenter",),
             ("experiments_manager",), ("approver", "ingestion_manager"), ("member", "approver")]
EMAIL, NAME = "pat.muster@kanzlei.ch", "Pat Muster"


@pytest.fixture
def app(platform_db, tmp_path, monkeypatch):
    """The real app with accounts on. Knovas Connector is a recording fake (as in test_web_admin_ingestion.py),
    so the Ingestion page answers without a network; it is put in before the app is built."""
    import knovas_connector_client
    from test_web_admin_ingestion import FakeKnovasConnectorClient

    monkeypatch.setattr(knovas_connector_client, "KnovasConnectorClient", FakeKnovasConnectorClient)
    monkeypatch.setenv("ONTOLOGY_SOURCE", "graph")
    return conftest._identity_app(platform_db, tmp_path, monkeypatch, client_cls=conftest.FakeGraphApi)


@pytest.fixture
def urls(app):
    with app.test_request_context():
        return {name: url_for(name) for name in ADMIN_PAGES + ["index", "settings_page", "builder.home"]}


def _someone(identity_repo, roles, email=EMAIL, name=NAME):
    user = identity_repo.create(email=email, display_name=name, password=conftest.PASSWORD)
    for role in roles:
        identity_repo.grant_role(user.id, role)
    return identity_repo.get(user.id)


def _client(app, identity_repo, roles):
    return conftest._signed_in(app, _someone(identity_repo, roles).email)


def _html(client, url):
    response = client.get(url)
    assert response.status_code == 200, f"{url} answered {response.status_code}"
    return response.data.decode("utf-8")


def _sidebar(html):
    return re.search(r"<aside\b[^>]*\bapp-sidebar\b[^>]*>.*?</aside>", html, flags=re.S).group(0)


def _area(html):
    return re.search(r'<aside\b[^>]*data-area="([^"]*)"', html).group(1)


def _nav(html):
    return re.search(r"<nav\b[^>]*>.*?</nav>", _sidebar(html), flags=re.S).group(0)


def _hrefs(fragment):
    return [href.replace("&amp;", "&") for href in re.findall(r'href="([^"]+)"', fragment)]


def _text(fragment):
    fragment = re.sub(r"<(script|style|svg)\b.*?</\1>", " ", fragment, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())


def _into(hrefs, prefix):
    return [href for href in hrefs if href == prefix or href.startswith(prefix + "/")]


def _entries(html):
    """The Admin menu's entries as (address, name, marked as the current page)."""
    return [(href, name.strip(), 'aria-current="page"' in tag)
            for tag, href, name in re.findall(r'(<a href="([^"]+)" class="app-nav-item[^>]*>)([^<]+)</a>', _nav(html))]


# --- 1: the links are what the server lets the person open ------------------------------------------------


@pytest.mark.parametrize("roles", ROLE_SETS, ids=["+".join(r) for r in ROLE_SETS])
def test_verwaltung_and_its_menu_are_exactly_the_admin_pages_the_server_opens_for_the_person(
        app, identity_repo, urls, roles):
    client = _client(app, identity_repo, roles)
    answers = {name: client.get(urls[name]).status_code for name in ADMIN_PAGES}
    assert set(answers.values()) <= {200, 403}, answers
    opens = [name for name in ADMIN_PAGES if answers[name] == 200]
    # the fake Knovas of this app offers no document fields, so that entry is not drawn for anybody
    offered = [name for name in opens if name != "admin.doc_fields"]

    sidebar = _sidebar(_html(client, urls["index"]))
    link = _into(_hrefs(sidebar), "/admin")
    if not offered:
        assert link == [] and "Verwaltung" not in _text(sidebar)
        return
    assert link == [urls[offered[0]]], "«Verwaltung» leads to the first Admin page the person may open"
    assert "Verwaltung" in _text(sidebar)
    for name in offered:                                       # the same menu on every page of the area
        page = _html(client, urls[name])
        assert _area(page) == "admin"
        assert _entries(page) == [(urls[n], NAMES[n], n == name) for n in offered]


@pytest.mark.parametrize("roles", ROLE_SETS + [("builder_viewer",), ("member", "builder_author")],
                         ids=["+".join(r) for r in ROLE_SETS] + ["builder_viewer", "member+builder_author"])
def test_builder_is_offered_exactly_to_a_person_the_builder_lets_in(app, identity_repo, urls, roles):
    client = _client(app, identity_repo, roles)
    lets_in = client.get(urls["builder.home"], follow_redirects=True).status_code
    assert lets_in in (200, 403)
    for page in ("index", "settings_page"):
        sidebar = _sidebar(_html(client, urls[page]))
        assert _into(_hrefs(sidebar), "/builder") == ([urls["builder.home"]] if lets_in == 200 else [])
        assert ("Builder" in _text(sidebar)) == (lets_in == 200)


def test_the_area_links_stand_at_the_foot_above_the_person(app, identity_repo, urls):
    sidebar = _sidebar(_html(_client(app, identity_repo, ("admin",)), urls["index"]))
    foot = sidebar[sidebar.index('class="app-sidebar-foot"'):]
    assert re.findall(r'<a href="([^"]+)" class="app-nav-item">([^<]+)</a>', foot) == [
        (urls["admin.people"], "Verwaltung"), (urls["builder.home"], "Builder")]
    assert foot.index("Builder") < foot.index('class="app-user"')
    assert urls["settings_page"] in _hrefs(foot)               # the person's block still leads to the settings


def test_a_signed_out_visitor_is_shown_no_menu_at_all(app, urls):
    for name in ["index", "settings_page", "builder.home"] + ADMIN_PAGES:
        response = app.test_client().get(urls[name])
        assert response.status_code == 302 and "app-sidebar" not in response.data.decode("utf-8")


# --- 2: "may open" is the route's own gate ------------------------------------------------------------------


def _gated(allowed):
    """A view behind a gate of the console's shape: it carries the roles it lets in as ``allowed_roles``."""
    def wrapped():
        return "x"

    wrapped.allowed_roles = allowed
    return wrapped


def test_every_console_route_carries_the_roles_its_gate_lets_in(app):
    """The menu reads ``allowed_roles`` (admin._require_roles sets it). A console route without it, or with
    anything other than a non-empty set of roles a person can be given, would silently drop out of the menu."""
    from web_interface.admin import ASSIGNABLE_ROLES

    console = {name: view for name, view in app.view_functions.items() if name.startswith("admin.")}
    assert set(ADMIN_PAGES) <= set(console)
    for name, view in sorted(console.items()):
        roles = getattr(view, "allowed_roles", None)
        assert isinstance(roles, frozenset), f"{name} has no allowed_roles"
        assert roles and roles <= set(ASSIGNABLE_ROLES), (name, roles)


def test_the_menu_follows_a_routes_gate_when_the_gate_changes(app, identity_repo, urls):
    """No second list of roles: give Personen a gate that lets members in, and a member's menu offers it."""
    client = _client(app, identity_repo, ("member",))
    assert _into(_hrefs(_sidebar(_html(client, urls["index"]))), "/admin") == []

    app.view_functions["admin.people"] = _gated(frozenset({"member"}))
    assert _into(_hrefs(_sidebar(_html(client, urls["index"]))), "/admin") == [urls["admin.people"]]

    app.view_functions["admin.people"] = _gated(frozenset({"approver"}))
    assert _into(_hrefs(_sidebar(_html(client, urls["index"]))), "/admin") == []


def test_a_gate_that_lets_administrators_out_takes_the_entry_out_of_their_menu(app, identity_repo, urls):
    client = _client(app, identity_repo, ("admin",))
    app.view_functions["admin.people"] = _gated(frozenset({"member"}))
    index = _sidebar(_html(client, urls["index"]))
    assert _into(_hrefs(index), "/admin") == [urls["admin.documents"]]      # the next page they may open
    assert urls["admin.people"] not in _hrefs(_nav(_html(client, urls["admin.system"])))


@pytest.mark.parametrize("broken", ["missing", "no gate", "not a set of roles", "roles in a closure only"])
def test_a_route_that_is_not_there_or_a_gate_that_cannot_be_read_draws_no_link(app, identity_repo, urls, broken):
    """Closed, not open: an approver whose one page cannot be checked gets no link, and the search still opens."""
    client = _client(app, identity_repo, ("approver",))
    assert _into(_hrefs(_sidebar(_html(client, urls["index"]))), "/admin") == [urls["admin.approvals"]]
    if broken == "missing":
        del app.view_functions["admin.approvals"]
    elif broken == "no gate":
        app.view_functions["admin.approvals"] = lambda: "x"
    elif broken == "not a set of roles":
        app.view_functions["admin.approvals"] = _gated("approver")  # a word, not a set: "a" in "approver" must not pass
    else:
        allowed = frozenset({"approver"})                      # the old way of reading a gate is not read any more
        app.view_functions["admin.approvals"] = lambda: allowed
    sidebar = _sidebar(_html(client, urls["index"]))
    assert _into(_hrefs(sidebar), "/admin") == [] and "Verwaltung" not in _text(sidebar)


def test_the_web_app_names_no_role_where_it_decides_the_admin_links():
    from pathlib import Path

    app_py = Path(conftest.__file__).resolve().parents[1] / "src" / "web_interface" / "app.py"
    source = app_py.read_text(encoding="utf-8")
    decided = source[source.index("    admin_menu = ("):source.index("    def _experiments_nav(")]
    code = "\n".join(line for line in re.sub(r'""".*?"""', "", decided, flags=re.S).splitlines()
                     if not line.strip().startswith("#"))
    for role in ("admin", "approver", "ingestion_manager", "member", "experimenter", "experiments_manager"):
        assert f"'{role}'" not in code and f'"{role}"' not in code, f"app.py names the role {role} for the menu"
    assert "_gate_roles(endpoint)" in code


# --- 3: the area follows the address ---------------------------------------------------------------------------


def test_every_page_under_admin_shows_verwaltung_and_every_other_page_the_workspace(app, identity_repo, urls):
    client = _client(app, identity_repo, ("admin",))
    for name in ADMIN_PAGES:
        page = _html(client, urls[name])
        assert _area(page) == "admin", name
        marked = [href for href, _, current in _entries(page) if current]
        # Dokumentfelder has no entry while Knovas does not offer the fields; its page then marks none
        assert marked == ([] if name == "admin.doc_fields" else [urls[name]]), name
        assert page.count('aria-current="page"') == len(marked), name
    for address in (urls["index"], urls["settings_page"], "/styleguide"):
        page = _html(client, address)
        assert _area(page) == "work", address
        assert _hrefs(_nav(page)) == [urls["index"]]


def test_an_admin_page_drawn_again_after_a_post_keeps_its_menu_and_its_mark(app, identity_repo, urls):
    """The page of a refused POST is drawn at the POST's address (/admin/people/create); the menu must not depend
    on the address being the page's own."""
    client = _client(app, identity_repo, ("admin",))
    response = client.post("/admin/people/create", data={"email": "neu@kanzlei.ch", "csrf_token": "falsch"})
    assert response.status_code == 400
    page = response.data.decode("utf-8")
    assert _area(page) == "admin"
    assert [href for href, _, current in _entries(page) if current] == [urls["admin.people"]]
    assert [href for href, _, _ in _entries(page)] == [urls[n] for n in ADMIN_PAGES if n != "admin.doc_fields"]


# --- 4: nothing about a person in a log line ------------------------------------------------------------------


def test_deciding_the_links_logs_no_address_and_no_name(app, identity_repo, urls, caplog):
    member = _someone(identity_repo, ("member",))
    chef = _someone(identity_repo, ("admin",), email="carla.chef@kanzlei.ch", name="Carla Chef")
    caplog.set_level(logging.DEBUG)
    for person in (member, chef):
        client = conftest._signed_in(app, person.email)
        caplog.clear()
        assert client.get(urls["index"]).status_code == 200
        assert client.get(urls["settings_page"]).status_code == 200
        for word in (member.email, chef.email, NAME, "Carla Chef", "pat.muster", "carla.chef"):
            assert word not in caplog.text, f"a log line carries {word!r}"
    # a hidden link still says whose roles were read, by id, so a missing link can be diagnosed
    client = conftest._signed_in(app, member.email)
    caplog.clear()
    client.get(urls["index"])
    hidden = [r.getMessage() for r in caplog.records if "Verwaltungslink ausgeblendet" in r.getMessage()]
    assert hidden and str(member.id) in hidden[0] and "member" in hidden[0]
