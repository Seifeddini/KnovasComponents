"""The styleguide page, GET /styleguide (PMVP-10), beside the frozen acceptance tests.

test_pmvp10_brand_acceptance.py pins who gets the page (signed out, a member, an administrator) and the twelve
token names on it. Held here, against the real app and the identity database:

1. the gate is the role admin, not "a console role": an approver is refused, and the route exists only with
   accounts on;
2. what the page shows: its sections in German, every token by name with the value the tokens file gives it, the
   Platform's own names, the plain parts that exist today, and one empty section for the parts of PMVP-12;
3. the navigation is the one every page has, the page loads nothing from outside the installation, and the route
   writes no log line of its own.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

pytest.importorskip("flask")

from conftest import _person, _signed_in, platform_db_reachable  # noqa: E402
from web_interface import shell_routes  # noqa: E402

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

CSS = Path(__file__).resolve().parents[1] / "src" / "web_interface" / "static" / "css"
FEEDBACK = "https://feedback.example.org/form"


@pytest.fixture(autouse=True)
def _feedback_entry_on(monkeypatch):
    """The installation names a Feedback address that points outside, as a default installation does, whatever
    this machine's environment says (until PMVP-11 the sidebar drew an entry for it). Autouse, so it is set before
    the app is built."""
    monkeypatch.setenv("FEEDBACK_URL", FEEDBACK)


def _page(client) -> str:
    response = client.get("/styleguide")
    assert response.status_code == 200
    return response.data.decode("utf-8")


# --- 1: who may open it ---------------------------------------------------------------------------------


def test_an_approver_is_refused(workbench_app, identity_repo):
    """An approver enters the console (Freigaben) but is no administrator."""
    approver = _person(identity_repo, "anna@kanzlei.ch", "Anna", "approver")
    assert _signed_in(workbench_app, approver.email).get("/styleguide").status_code == 403


def test_a_signed_out_visitor_comes_back_to_the_page_after_sign_in(anon_client):
    response = anon_client.get("/styleguide")
    assert response.status_code == 302
    assert "next=" in response.headers["Location"] and "styleguide" in response.headers["Location"]


def test_the_page_takes_no_post(admin_client):
    assert admin_client.post("/styleguide").status_code == 405


def test_the_route_exists_only_with_accounts_on(workbench_app, docbridge_app):
    assert "shell.styleguide" in workbench_app.view_functions
    # one shared login knows no administrator, so there is nobody the page could be for
    assert "shell.styleguide" not in docbridge_app.view_functions


# --- 2: what it shows -----------------------------------------------------------------------------------


def test_the_page_is_in_german_with_its_sections(admin_client):
    page = _page(admin_client)
    assert '<html lang="de">' in page
    for heading in ("Farben", "Markenfarben", "Statusfarben", "Textfarben auf den drei Flächen", "Schrift",
                    "Schriftgrade", "Abstände", "Radien", "Schatten", "Bestehende Teile", "Seitenteile",
                    "Die Namen der Plattform"):
        assert f">{heading}</h" in page, f"no section {heading!r}"


def test_every_token_on_the_page_is_in_the_tokens_file_with_its_value(admin_client):
    page = _page(admin_client)
    tokens = shell_routes.root_declarations((CSS / "knovas-tokens.css").read_text(encoding="utf-8"))
    named = set(re.findall(r"--k-[a-z0-9-]+", page))
    assert named, "the page names no token"
    assert named <= set(tokens), f"not in the tokens file: {sorted(named - set(tokens))}"
    for name in shell_routes.BRAND_COLOURS + shell_routes.STATUS_COLOURS + shell_routes.NEUTRALS:
        # a plain colour: its name and, right after it, the value the file writes
        assert re.search(rf"{re.escape(name)}</span>\s*<span class=\"sg-value\">{re.escape(tokens[name])}", page), name


def test_every_step_of_the_scales_is_shown(admin_client):
    page = _page(admin_client)
    for name in (shell_routes.TYPE_SCALE + shell_routes.SPACING + shell_routes.RADII + shell_routes.SHADOWS
                 + shell_routes.FACES):
        assert name in page, f"{name} is not shown"


def test_the_platforms_own_names_are_shown_with_the_token_each_takes(admin_client):
    page = _page(admin_client)
    platform = shell_routes.root_declarations((CSS / "style.css").read_text(encoding="utf-8"))
    assert len(platform) >= 22
    for name, written in platform.items():
        assert f">{name}</span>" in page, f"{name} is not shown"
    assert "var(--k-color-accent)" in page and "var(--k-gray-600)" in page


def test_the_plain_parts_are_drawn_with_the_classes_the_pages_use(admin_client):
    page = _page(admin_client)
    assert 'class="btn btn-primary"' in page and 'class="btn btn-outline"' in page
    assert 'id="searchInput"' in page and 'class="login-form"' in page
    assert 'class="btn-text"' in page


def test_the_section_for_the_parts_of_pmvp_12_is_there_and_empty(admin_client):
    page = _page(admin_client)
    section = re.search(r'<section class="sg-section" aria-labelledby="sg-seitenteile">(.*?)</section>', page, re.S)
    assert section, "no section for the page parts"
    body = section.group(1)
    assert "PMVP-12" in body
    # a heading and one line saying they follow; no part is drawn
    assert re.sub(r"<h2[^>]*>.*?</h2>|<p class=\"sg-empty\">.*?</p>", "", body, flags=re.S).strip() == ""


def test_the_page_loads_the_base_styles_and_names_no_colour_of_its_own(admin_client):
    page = _page(admin_client)
    assert "/static/css/style.css?v=" in page
    style = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    assert not re.search(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(|hsla?\(", style), "the page's own rules name a colour"
    assert set(re.findall(r"var\((--[A-Za-z0-9_-]+)\)", style)) <= set(
        shell_routes.root_declarations((CSS / "knovas-tokens.css").read_text(encoding="utf-8")))


# --- 3: nothing outside, nothing logged -------------------------------------------------------------------


def test_the_navigation_is_the_one_every_page_has(admin_client):
    """The sidebar partial, with the same entries as on the search page. PMVP-11: that is the Workspace's one
    entry, the search; Feedback has no entry until a later item places it (founder, 2026-11-20), although the
    installation names a Feedback address."""
    def entries(html: str) -> list[str]:
        nav = re.search(r'<nav class="app-nav"[^>]*>(.*?)</nav>', html, re.S).group(1)
        return re.findall(r'<a href="([^"]+)" class="app-nav-item', nav)

    page, search_page = _page(admin_client), admin_client.get("/").data.decode("utf-8")
    assert entries(page) == entries(search_page) == ["/"]
    assert FEEDBACK not in page and FEEDBACK not in search_page


def test_the_page_loads_nothing_from_outside_the_installation(admin_client):
    """A plain link to another site loads nothing; a stylesheet, a script, an image or a frame from there would."""
    page = _page(admin_client)
    outside = r"(?:https?:)?//"
    loading = re.findall(r"<(?:link|script|img|iframe|source|embed|object|video|audio|form)\b[^>]*>", page, re.I)
    assert loading, "the page has no stylesheet link at all"
    for tag in loading:
        assert not re.search(rf"(?:src|href|action|srcset|poster|data)=[\"']{outside}", tag, re.I), tag
    assert not re.search(rf"url\(\s*[\"']?{outside}|@import\s+[\"']{outside}", page), "a style loads from outside"


def test_the_route_writes_no_log_line_of_its_own(admin_client, member_client, caplog):
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    assert admin_client.get("/styleguide").status_code == 200
    assert member_client.get("/styleguide").status_code == 403
    assert [r for r in caplog.records if r.name.startswith("web_interface.shell_routes")] == []
    # and no line of anybody's carries a person's address or name
    assert "chef@kanzlei.ch" not in caplog.text and "mia@kanzlei.ch" not in caplog.text
