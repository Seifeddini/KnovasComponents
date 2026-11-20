"""PMVP-11 acceptance tests: one navigation with three areas; the links to Verwaltung and Builder follow the
person's role, and the server refuses the pages whatever the links show (FEAT-2026-11-19-platform-mvp, contract
L2-PLATFORM-SHELL POST-01, plan section 5.1 rule 3, Jira SS-730).

FROZEN. Written by the coordinator before the build; a builder never edits this file. Its git blob id is in
docs/platform-mvp/frozen-tests.txt.

What is pinned:

1. The navigation is the element `<aside class="app-sidebar" data-area="work|admin|builder">`; its `<nav>` holds
   the entries of the area the page belongs to and nothing else (rule 3: "the navigation shows only the area you
   are in").
2. Workspace (`work`): the one entry is the search. Founder, 2026-11-20: Cortex, Experimente, Eigene Ansichten,
   Einstellungen and Feedback get no entry until a later item places them; their addresses answer as before.
3. SHELL POST-01: the links to Verwaltung and to the Builder are shown only to a person who may open a page
   there, and the pages answer 403 to everybody else, link or no link.
4. Verwaltung (`admin`): a way back to the Workspace, then the Admin pages the person may open; the row of tabs
   inside the Admin pages is gone (one navigation).
5. The Builder keeps its own pages in this item and offers a way back to the Workspace.
"""
from __future__ import annotations

import re

import pytest
from flask import url_for

import conftest

HIDDEN_WORDS = ["Cortex", "Experimente", "Eigene Ansichten", "Einstellungen", "Feedback"]


@pytest.fixture
def approver_client(workbench_app, identity_repo):
    person = conftest._person(identity_repo, "pruefer@kanzlei.ch", "Petra Pruefer", "approver")
    return conftest._signed_in(workbench_app, person.email)


@pytest.fixture
def urls(workbench_app):
    names = ["index", "settings_page", "ontology_page", "builder.home", "admin.people", "admin.documents",
             "admin.access_groups", "admin.system", "admin.approvals"]
    with workbench_app.test_request_context():
        return {name: url_for(name) for name in names}


def _page(client, url):
    response = client.get(url)
    assert response.status_code == 200, f"{url} answered {response.status_code}"
    return response.data.decode("utf-8")


def _sidebar(html):
    match = re.search(r"<aside\b[^>]*\bapp-sidebar\b[^>]*>.*?</aside>", html, flags=re.S)
    assert match, "the page has no navigation (<aside class=\"app-sidebar\">)"
    return match.group(0)


def _area(html):
    tag = re.match(r"<aside\b[^>]*>", _sidebar(html)).group(0)
    found = re.search(r'data-area="([^"]*)"', tag)
    assert found, "the navigation does not say which area it shows (data-area)"
    return found.group(1)


def _nav(html):
    match = re.search(r"<nav\b[^>]*>.*?</nav>", _sidebar(html), flags=re.S)
    assert match, "the navigation has no <nav>"
    return match.group(0)


def _hrefs(fragment):
    return [href.replace("&amp;", "&") for href in re.findall(r'href="([^"]+)"', fragment)]


def _text(fragment):
    return " ".join(re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style|svg)\b.*?</\1>", " ", fragment, flags=re.S)).split())


def _current(fragment):
    """The addresses of the links marked as the current page."""
    return _hrefs(" ".join(re.findall(r'<a\b[^>]*aria-current="page"[^>]*>', fragment)))


# --- the Workspace shows the search and nothing else ----------------------------------------------------


@pytest.mark.parametrize("who", ["member_client", "approver_client", "admin_client"])
def test_the_workspace_menu_holds_only_the_search(who, urls, request):
    html = _page(request.getfixturevalue(who), urls["index"])
    assert _area(html) == "work"
    assert _hrefs(_nav(html)) == [urls["index"]]
    assert "Suche" in _text(_nav(html))
    for word in HIDDEN_WORDS:
        assert word not in _text(_nav(html)), f"{word} has an entry"


@pytest.mark.parametrize("who", ["member_client", "admin_client"])
def test_the_navigation_links_to_no_other_site(who, urls, request):
    html = _page(request.getfixturevalue(who), urls["index"])
    assert not [href for href in _hrefs(_sidebar(html)) if re.match(r"(?:https?:)?//", href)]


def test_the_search_is_marked_as_the_current_page_only_on_the_search(member_client, urls):
    assert _current(_sidebar(_page(member_client, urls["index"]))) == [urls["index"]]
    assert _current(_sidebar(_page(member_client, urls["settings_page"]))) == []


@pytest.mark.parametrize("address", ["settings_page", "ontology_page"])
def test_a_page_without_an_entry_still_opens(address, member_client, urls):
    _page(member_client, urls[address])


def test_a_page_without_an_entry_shows_the_workspace_menu(member_client, urls):
    html = _page(member_client, urls["settings_page"])
    assert _area(html) == "work"
    assert _hrefs(_nav(html)) == [urls["index"]]


# --- SHELL POST-01: the links follow the role ---------------------------------------------------------------


def _into(hrefs, prefix):
    return [href for href in hrefs if href == prefix.rstrip("/") or href.startswith(prefix.rstrip("/") + "/")]


def test_a_member_sees_no_link_to_verwaltung_or_builder(member_client, urls):
    sidebar = _sidebar(_page(member_client, urls["index"]))
    assert _into(_hrefs(sidebar), "/admin") == []
    assert _into(_hrefs(sidebar), "/builder") == []
    assert "Verwaltung" not in _text(sidebar)
    assert "Builder" not in _text(sidebar)


def test_an_administrator_sees_both_links(admin_client, urls):
    sidebar = _sidebar(_page(admin_client, urls["index"]))
    assert len(_into(_hrefs(sidebar), "/admin")) == 1
    assert _into(_hrefs(sidebar), "/builder") == [urls["builder.home"]]
    assert "Verwaltung" in _text(sidebar) and "Builder" in _text(sidebar)


def test_an_approver_sees_verwaltung_leading_to_the_page_they_may_open_and_no_builder(approver_client, urls):
    sidebar = _sidebar(_page(approver_client, urls["index"]))
    assert _into(_hrefs(sidebar), "/admin") == [urls["admin.approvals"]]
    assert _into(_hrefs(sidebar), "/builder") == []
    assert "Verwaltung" in _text(sidebar)
    assert "Builder" not in _text(sidebar)


# --- SHELL POST-01: the link is not the check ---------------------------------------------------------------

ADMIN_AND_BUILDER = ["admin.people", "admin.documents", "admin.access_groups", "admin.system", "admin.approvals",
                     "builder.home"]


@pytest.mark.parametrize("address", ADMIN_AND_BUILDER)
def test_the_server_refuses_a_member_whatever_the_menu_shows(address, member_client, urls):
    assert member_client.get(urls[address]).status_code == 403


@pytest.mark.parametrize("address", [a for a in ADMIN_AND_BUILDER if a != "admin.approvals"])
def test_the_server_refuses_an_approver_outside_the_approvals(address, approver_client, urls):
    assert approver_client.get(urls[address]).status_code == 403


@pytest.mark.parametrize("address", ["index", "settings_page"] + ADMIN_AND_BUILDER)
def test_a_signed_out_visitor_is_sent_to_sign_in(address, anon_client, urls):
    response = anon_client.get(urls[address])
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


# --- Verwaltung: a way back, the pages the person may open, no second row of tabs --------------------------


@pytest.mark.parametrize("address", ["admin.people", "admin.system", "admin.approvals"])
def test_an_admin_page_shows_the_admin_menu(address, admin_client, urls):
    html = _page(admin_client, urls[address])
    assert _area(html) == "admin"
    entries = _hrefs(_nav(html))
    assert all(href.startswith("/admin/") for href in entries), entries
    for name in ("admin.people", "admin.documents", "admin.access_groups", "admin.system", "admin.approvals"):
        assert urls[name] in entries, f"{name} has no entry"
    assert len(entries) == len(set(entries))
    sidebar = _sidebar(html)
    assert urls["index"] in _hrefs(sidebar) and "Arbeitsbereich" in _text(sidebar)   # the way back
    assert _into(_hrefs(sidebar), "/builder") == []                                  # only the area you are in
    assert "Suche" not in _text(_nav(html))


@pytest.mark.parametrize("address", ["admin.people", "admin.system", "admin.approvals"])
def test_exactly_one_link_on_an_admin_page_is_the_current_one(address, admin_client, urls):
    html = _page(admin_client, urls[address])
    assert _current(html) == [urls[address]]                 # the old row of tabs marked a second one
    assert _current(_nav(html)) == [urls[address]]


def test_the_row_of_tabs_inside_the_admin_pages_is_gone(admin_client, urls):
    html = _page(admin_client, urls["admin.people"])
    assert _hrefs(html).count(urls["admin.system"]) == 1     # once, in the navigation


def test_an_approver_sees_only_the_approvals_in_the_admin_menu(approver_client, urls):
    html = _page(approver_client, urls["admin.approvals"])
    assert _area(html) == "admin"
    assert _hrefs(_nav(html)) == [urls["admin.approvals"]]
    assert urls["index"] in _hrefs(_sidebar(html))


# --- the Builder keeps its pages and offers a way back ------------------------------------------------------


def test_the_builder_offers_a_way_back_to_the_workspace(admin_client, urls):
    response = admin_client.get(urls["builder.home"], follow_redirects=True)
    assert response.status_code == 200
    assert urls["index"] in _hrefs(response.data.decode("utf-8"))
