"""The navigation partial on its own (PMVP-11): what the frozen acceptance file leaves open.

test_pmvp11_navigation_acceptance.py pins the three areas against the real app. Held here on the template itself,
with a bare Jinja environment that refuses every undefined name (no database, no Flask app):

1. the partial needs only the title and the firm's name; everything else is optional and draws nothing when it is
   missing, so a page that forgets a value loses a link and never breaks;
2. the Workspace: the search and nothing else, whatever the old menu's switches say; the two area links stand
   outside the <nav> and only when the web app says the person may open a page there;
3. Verwaltung: the entries are the pages the web app hands in, in its order, exactly one marked; the entry for
   Dokumentfelder follows what Knovas offers and is not asked for a person whose menu does not hold it;
4. the partial names no role, the row of tabs is gone, and every Admin template says which page it is before it
   includes the navigation.
"""
from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"
ADMIN_TEMPLATES = {
    "admin_people.html": "people", "admin_documents.html": "documents",
    "admin_access_groups.html": "access_groups", "admin_doc_fields.html": "doc_fields",
    "admin_ingestion.html": "ingestion", "admin_system.html": "system", "admin_approvals.html": "approvals",
    "admin_roles.html": "roles",                                                     # RB-3
}
HIDDEN_WORDS = ("Cortex", "Experimente", "Eigene Ansichten", "Einstellungen", "Feedback")
ROLES = ("admin", "approver", "ingestion_manager", "member", "experimenter", "experiments_manager")

URLS = {"index": "/", "settings_page": "/settings", "builder.home": "/builder/"}


def _env() -> jinja2.Environment:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True,
                             undefined=jinja2.StrictUndefined)

    def url_for(endpoint, **kw):
        if endpoint == "static":
            return "/static/" + kw["filename"]
        return URLS.get(endpoint, "/" + endpoint.replace(".", "/"))

    env.globals["url_for"] = url_for
    return env


def _sidebar(**context) -> str:
    return _env().get_template("_sidebar.html").render(app_title="Knovas", company_name="Kanzlei", **context)


def _page(key, label, url, if_doc_fields=False) -> dict:
    return {"key": key, "label": label, "url": url, "if_doc_fields": if_doc_fields}


PAGES = [
    _page("people", "Personen", "/admin/people"),
    _page("documents", "Dokumente", "/admin/documents"),
    _page("access_groups", "Zugriffsgruppen", "/admin/access-groups"),
    _page("doc_fields", "Dokumentfelder", "/admin/doc-fields", if_doc_fields=True),
    _page("ingestion", "Ingestion", "/admin/ingestion"),
    _page("system", "System", "/admin/system"),
    _page("approvals", "Freigaben", "/admin/approvals"),
]


class Capability:
    def __init__(self, on: bool):
        self.shows_values = on


def _area(html: str) -> str:
    return re.search(r'<aside class="app-sidebar" data-area="([^"]*)">', html).group(1)


def _navs(html: str) -> list[str]:
    return re.findall(r"<nav\b[^>]*>.*?</nav>", html, flags=re.S)


def _nav(html: str) -> str:
    navs = _navs(html)
    assert len(navs) == 1, "the navigation has exactly one <nav>"
    return navs[0]


def _hrefs(fragment: str) -> list[str]:
    return re.findall(r'href="([^"]+)"', fragment)


def _text(fragment: str) -> str:
    fragment = re.sub(r"<(script|style|svg)\b.*?</\1>", " ", fragment, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())


def _current(fragment: str) -> list[str]:
    return _hrefs(" ".join(re.findall(r'<a\b[^>]*aria-current="page"[^>]*>', fragment)))


# --- 1: what the partial needs ---------------------------------------------------------------------------


def test_the_partial_draws_the_workspace_from_the_title_and_the_firms_name_alone():
    html = _sidebar()
    assert _area(html) == "work"
    assert _hrefs(_nav(html)) == ["/"] and _text(_nav(html)) == "Suche"
    assert _current(html) == []                                  # no page said it is the search
    assert _hrefs(html) == ["/", "/", "/settings"]               # the logo, the search, the person


@pytest.mark.parametrize("given, admin_tab, area", [
    ("work", None, "work"), ("admin", None, "admin"), ("work", "people", "work"), (None, "people", "admin"),
    (None, None, "work"),
])
def test_the_area_is_the_one_the_web_app_names_and_else_follows_the_page(given, admin_tab, area):
    """The web app names the area from the address. A bare render (no web app) of a page that says which Admin
    page it is shows Verwaltung."""
    context = {}
    if given is not None:
        context["nav_area"] = given
    if admin_tab is not None:
        context["admin_tab"] = admin_tab
    assert _area(_sidebar(**context)) == area


def test_the_person_at_the_foot_leads_to_the_settings_in_both_areas():
    for context in ({}, {"nav_area": "admin", "admin_pages": PAGES, "admin_tab": "people"}):
        html = _sidebar(**context)
        foot = html[html.index('<div class="app-sidebar-foot">'):]
        assert 'class="app-user" href="/settings"' in foot
        assert "aria-current" not in foot


# --- 2: the Workspace ------------------------------------------------------------------------------------


@pytest.mark.parametrize("active_nav, marked", [("suche", ["/"]), ("einstellungen", []), ("cortex", []),
                                                ("experiments", []), ("custom_views", []), ("styleguide", []),
                                                ("admin", [])])
def test_the_search_is_the_current_page_only_when_the_page_says_so(active_nav, marked):
    html = _sidebar(active_nav=active_nav)
    assert _current(html) == marked
    assert ('class="app-nav-item active"' in html) == bool(marked)


def test_the_old_menus_switches_draw_no_entry():
    """Cortex, Experimente, Eigene Ansichten, Einstellungen and Feedback have no entry until a later item places
    them (founder, 2026-11-20). The web app still hands their switches in; none of them draws anything."""
    html = _sidebar(active_nav="suche", cortex_enabled=True, experiments_nav=True, custom_views_nav=True,
                    feedback_url="https://feedback.example.org/form", console_url="/admin/people", builder_nav=True)
    assert _hrefs(_nav(html)) == ["/"]
    for word in HIDDEN_WORDS:
        assert word not in _text(html), f"{word} is drawn"
    assert not [href for href in _hrefs(html) if re.match(r"(?:https?:)?//", href)]
    assert "target=" not in html


@pytest.mark.parametrize("console_url, builder_nav, links", [
    (None, False, []),
    ("/admin/people", False, [("/admin/people", "Verwaltung")]),
    ("/admin/approvals", False, [("/admin/approvals", "Verwaltung")]),
    (None, True, [("/builder/", "Builder")]),
    ("/admin/people", True, [("/admin/people", "Verwaltung"), ("/builder/", "Builder")]),
    ("", 0, []),
])
def test_the_area_links_are_drawn_only_when_the_web_app_says_so(console_url, builder_nav, links):
    html = _sidebar(active_nav="suche", console_url=console_url, builder_nav=builder_nav)
    foot = html[html.index('<div class="app-sidebar-foot">'):]
    drawn = re.findall(r'<a href="([^"]+)" class="app-nav-item">([^<]+)</a>', foot)
    assert drawn == links
    assert ("app-areas" in html) == bool(links)                  # no empty box for a member
    assert _hrefs(_nav(html)) == ["/"]                           # the links stand outside the <nav>
    for word in ("Verwaltung", "Builder"):
        assert (word in _text(html)) == (word in [label for _, label in links])


def test_the_area_links_are_not_drawn_inside_verwaltung():
    html = _sidebar(nav_area="admin", admin_pages=PAGES, admin_tab="people", console_url="/admin/people",
                    builder_nav=True)
    assert "app-areas" not in html
    assert "/builder/" not in _hrefs(html)
    assert _hrefs(html).count("/admin/people") == 1              # the entry, not a second link to the area


# --- 3: Verwaltung ---------------------------------------------------------------------------------------


def test_verwaltung_draws_the_pages_it_is_handed_in_their_order():
    html = _sidebar(nav_area="admin", admin_pages=PAGES, admin_tab="system", doc_fields_capability=Capability(True))
    nav = _nav(html)
    assert re.findall(r'<a href="([^"]+)" class="app-nav-item[^"]*"[^>]*>([^<]+)</a>', nav) == [
        (page["url"], page["label"]) for page in PAGES]
    assert _text(nav).startswith("Verwaltung")                   # the area's name, above its entries
    assert "Suche" not in _text(nav)


@pytest.mark.parametrize("handed", [
    [], [PAGES[6]], [PAGES[4], PAGES[6]], PAGES[:3] + PAGES[4:],
])
def test_verwaltung_draws_nothing_it_was_not_handed(handed):
    """The partial decides nothing: a page the web app left out (the person may not open it) has no entry."""
    html = _sidebar(nav_area="admin", admin_pages=handed, admin_tab="approvals", me=None)
    assert _hrefs(_nav(html)) == [page["url"] for page in handed]


def test_verwaltung_without_pages_still_offers_the_way_back():
    html = _sidebar(nav_area="admin")
    assert _hrefs(_nav(html)) == []
    back = re.search(r'<a href="([^"]+)" class="app-nav-item app-nav-back">(.*?)</a>', html, re.S)
    assert back and back.group(1) == "/" and _text(back.group(2)).endswith("Arbeitsbereich")
    assert html.index("app-nav-back") < html.index("<nav")       # above the area's entries, outside the <nav>


@pytest.mark.parametrize("admin_tab", [page["key"] for page in PAGES])
def test_exactly_the_current_admin_page_is_marked(admin_tab):
    html = _sidebar(nav_area="admin", admin_pages=PAGES, admin_tab=admin_tab,
                    doc_fields_capability=Capability(True))
    wanted = [page["url"] for page in PAGES if page["key"] == admin_tab]
    assert _current(html) == wanted
    assert html.count('aria-current="page"') == 1
    assert re.findall(r'<a href="([^"]+)" class="app-nav-item active"', html) == wanted


def test_no_admin_page_is_marked_when_the_page_does_not_say_which_it_is():
    for context in ({}, {"admin_tab": "walls"}):
        html = _sidebar(nav_area="admin", admin_pages=PAGES[:3], **context)
        assert _current(html) == [] and 'class="app-nav-item active"' not in html


def test_dokumentfelder_is_drawn_only_while_knovas_offers_document_fields():
    """H6: no entry for a feature the server does not have. The capability reaches the partial through the
    console blueprint's context processor; outside it (no capability handed in) there is no entry."""
    def entries(**context):
        return _hrefs(_nav(_sidebar(nav_area="admin", admin_pages=PAGES, admin_tab="people", **context)))

    without = [page["url"] for page in PAGES if not page["if_doc_fields"]]
    assert entries() == without
    assert entries(doc_fields_capability=Capability(False)) == without
    assert entries(doc_fields_capability=Capability(True)) == [page["url"] for page in PAGES]


def test_the_capability_is_not_asked_for_a_person_whose_menu_has_no_dokumentfelder():
    """Reading the capability asks Knovas. An approver's menu (Freigaben alone) must not, and neither must any
    page of the Workspace."""
    class Boom:
        @property
        def shows_values(self):
            raise AssertionError("the capability was asked")

    html = _sidebar(nav_area="admin", admin_pages=[PAGES[6]], admin_tab="approvals", doc_fields_capability=Boom())
    assert _hrefs(_nav(html)) == ["/admin/approvals"]
    _sidebar(active_nav="suche", admin_pages=PAGES, console_url="/admin/people", doc_fields_capability=Boom())


# --- 4: no role, no tabs, and the Admin templates ---------------------------------------------------------


def test_the_partial_names_no_role():
    """Who may open a page is read from the page's route (app.py); the partial draws what it is handed. ('admin'
    is also the name of the area: the partial compares it with the area and with nothing a person holds.)"""
    source = (TEMPLATES / "_sidebar.html").read_text(encoding="utf-8")
    code = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
    for role in ROLES:
        if role != "admin":
            assert role not in code, f"the partial names the role {role}"
    assert ".roles" not in code and not re.search(r"\bme\b", code)
    assert re.findall(r"(\w+) (?:==|!=) 'admin'", code) == ["shell_area"]
    assert not re.search(r"'admin' (?:not )?in\b", code)


def test_the_row_of_tabs_is_gone():
    assert not (TEMPLATES / "_admin_tabs.html").exists()
    for template in sorted(TEMPLATES.rglob("*.html")):
        assert "_admin_tabs" not in template.read_text(encoding="utf-8"), template.name


@pytest.mark.parametrize("name, key", sorted(ADMIN_TEMPLATES.items()))
def test_an_admin_template_says_which_page_it_is_before_it_includes_the_navigation(name, key):
    source = (TEMPLATES / name).read_text(encoding="utf-8")
    said = "{% set admin_tab = '" + key + "' %}"
    assert source.count(said) == 1 and source.count("admin_tab") == 1
    assert source.count("{% include '_sidebar.html' %}") == 1
    assert source.index(said) < source.index("{% include '_sidebar.html' %}")


def test_every_admin_template_is_in_the_list_above():
    assert sorted(t.name for t in TEMPLATES.glob("admin_*.html")) == sorted(ADMIN_TEMPLATES)


def test_the_partial_has_the_one_script_it_had_and_no_event_attribute():
    source = (TEMPLATES / "_sidebar.html").read_text(encoding="utf-8")
    assert source.count("<script>") == 1 and "<script src" not in source
    assert not re.search(r"\son[a-z]+=", re.sub(r"<script>.*?</script>", "", source, flags=re.S))
    # the script reads the element before it: the <nav> of either area
    rendered = _sidebar(nav_area="admin", admin_pages=PAGES, admin_tab="people")
    for html in (rendered, _sidebar(active_nav="suche")):
        assert re.search(r"</nav>\s*<script>", html)
