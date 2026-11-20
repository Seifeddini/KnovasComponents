"""PMVP-03: one browser walkthrough per area, as evidence on a pull request (plan section 11).

An administrator signs in and opens every page of the area's table at 1440 and at 390 pixels wide. On each page
and width: no response of 500 or more, no console error, and at 390 nothing scrolls sideways. A page item of the
Platform MVP adds its pages to ``PAGES``.

With ``PMVP_WALK_OUT=<folder>`` one screenshot per page and width is saved there (``<area>-<page>-<width>.png``).
The folder is never inside the repository; the walkthrough refuses one that is.

The app is the real one, served from a thread, on the tests' own invented data (``fixture_mode_app``), so a
console error printed into a failure holds nothing of a customer. Playwright is optional, as for
``test_builder_screens.py``: without it, or without its Chromium, the walkthrough is skipped. The table itself is
checked without a browser, so an address that no longer opens is noticed everywhere.

Seven of today's pages scroll sideways at 390 pixels. They are listed in ``SCROLLS_SIDEWAYS_TODAY`` rather than left
out of the walk.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from conftest import PASSWORD, PLATFORM_DB_TEST_DSN, platform_db_reachable
from pmvp_harness.files import inside_the_repository

needs_database = pytest.mark.skipif(
    not platform_db_reachable(),
    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}",
)

#: Width and height of the two windows every page is opened in.
WIDTHS = {1440: 900, 390: 844}
PHONE = 390

#: The pages to walk, per area. Later items add theirs.
PAGES = {
    "workspace": ["/", "/views", "/ontology", "/settings"],
    "admin": ["/admin/people", "/admin/documents", "/admin/ingestion", "/admin/approvals", "/admin/system"],
    "builder": ["/builder/arbeitsbereich", "/builder/verbinden", "/builder/ontologie", "/builder/prozesse",
                "/builder/quellen", "/builder/ansichten", "/builder/pruefen", "/builder/betrieb", "/builder/zugriff"],
}

#: Pages that scroll sideways at 390 pixels today (found 2026-11-20: they were built before a phone width was
#: asked of them). Written down so that the walkthrough is green on what is known and red on anything new. A
#: page that no longer scrolls sideways fails until it is taken off this list, so the list cannot go stale.
SCROLLS_SIDEWAYS_TODAY = {
    "/views", "/settings",
    "/admin/people", "/admin/documents", "/admin/ingestion", "/admin/approvals", "/admin/system",
}

OUT_VARIABLE = "PMVP_WALK_OUT"


def screenshot_folder(value=None):
    """The folder ``PMVP_WALK_OUT`` names, made when missing; None when it is not set."""
    value = os.environ.get(OUT_VARIABLE, "") if value is None else value
    if not value.strip():
        return None
    folder = Path(value)
    if inside_the_repository(folder):
        raise ValueError(f"{OUT_VARIABLE} is inside this repository; screenshots are evidence for a pull request "
                         "and stay outside it")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def screenshot_name(area: str, path: str, width: int) -> str:
    page = path.strip("/").replace("/", "-") or "start"
    return f"{area}-{page}-{width}.png"


# --- the table, without a browser ---------------------------------------------------------------------


def test_the_table_has_the_three_areas_and_each_page_once():
    assert list(PAGES) == ["workspace", "admin", "builder"]
    listed = [path for pages in PAGES.values() for path in pages]
    assert all(path.startswith("/") for path in listed) and len(listed) == len(set(listed))
    assert all(PAGES[area] for area in PAGES)
    assert SCROLLS_SIDEWAYS_TODAY <= set(listed)


@needs_database
@pytest.mark.parametrize("area", PAGES)
def test_every_page_of_the_table_opens_for_an_administrator(area, admin_client):
    not_open = {path: status for path in PAGES[area] if (status := admin_client.get(path).status_code) != 200}
    assert not not_open, f"pages of the {area} table that do not open (address: status): {not_open}"


def test_screenshots_are_refused_inside_the_repository(tmp_path):
    inside = Path(__file__).resolve().parent / "walk-out-of-a-test-run"
    with pytest.raises(ValueError, match="inside this repository"):
        screenshot_folder(str(inside))
    assert not inside.exists()
    assert screenshot_folder("") is None
    assert screenshot_folder(str(tmp_path / "walk" / "run-1")) == tmp_path / "walk" / "run-1"
    assert (tmp_path / "walk" / "run-1").is_dir()


def test_a_screenshot_is_named_by_area_page_and_width():
    assert screenshot_name("workspace", "/", 1440) == "workspace-start-1440.png"
    assert screenshot_name("admin", "/admin/people", 390) == "admin-admin-people-390.png"


# --- the walkthrough ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed (it is optional)")
    with sync_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - no browser installed here
            pytest.skip(f"Chromium for Playwright is not installed: {type(exc).__name__}")
        yield chromium
        chromium.close()


@pytest.fixture
def connector_stand_in(monkeypatch):
    """No test environment has a Knovas Connector. The pages handle that, but the folder picker of the ingestion
    page then answers 502 after waiting for a name that does not resolve. This one offers a folder to pick and
    is otherwise as unreachable as before."""
    import knovas_connector_client

    class ConnectorStandIn(knovas_connector_client.KnovasConnectorClient):
        def _call(self, method, path, **_):
            if (method, path) == ("GET", "/discover"):
                return {"root": "/srv/kanzlei", "truncated": False,
                        "entries": [{"type": "directory", "path": "Mandate", "name": "Mandate"}]}
            raise knovas_connector_client.KnovasConnectorError("Der Knovas Connector ist nicht erreichbar.")

    monkeypatch.setattr(knovas_connector_client, "KnovasConnectorClient", ConnectorStandIn)


@pytest.fixture
def served(connector_stand_in, fixture_mode_app, platform_admin):
    """The real app on a port of its own, and the administrator who walks it.

    ``fixture_mode_app``: the Cortex page reads the shipped example ontology. In graph mode it would ask the
    tests' fake Knowledge Graph for an export the fake does not have. The stand-in comes first in the arguments
    so that it is in place when the app is built.
    """
    server = make_server("127.0.0.1", 0, fixture_mode_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", platform_admin
    server.shutdown()
    thread.join(timeout=10)


def _sign_in(page, base_url, person):
    page.goto(f"{base_url}/login")
    page.fill('input[name="login_name"]', person.email)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("load")
    assert "/login" not in page.url, "the administrator could not sign in"


def _walk(page, base_url, area, shots):
    """Open every page of ``area`` at every width; the problems found, as lines for the failure message."""
    seen = {"responses": [], "console": []}
    page.on("response", lambda response: seen["responses"].append((response.status, response.url)))
    page.on("console", lambda message: seen["console"].append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: seen["console"].append(str(error)))
    problems = []
    for path in PAGES[area]:
        for width, height in WIDTHS.items():
            where = f"{path} at {width}px"
            seen["responses"].clear()
            seen["console"].clear()
            page.set_viewport_size({"width": width, "height": height})
            opened = page.goto(f"{base_url}{path}", wait_until="load")
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:  # noqa: BLE001 - a page that keeps polling is still a page
                pass
            page.evaluate("document.fonts.ready")
            if opened is None or opened.status != 200:
                problems.append(f"{where}: did not open (status {getattr(opened, 'status', None)})")
            for status, url in seen["responses"]:
                if status >= 500:
                    problems.append(f"{where}: response {status} from {url.removeprefix(base_url)}")
            for text in seen["console"]:
                first_line = (text.splitlines() or [""])[0].replace(base_url, "")
                problems.append(f"{where}: console error: {first_line[:200]}")
            if width == PHONE:
                wide = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                if wide > 0 and path not in SCROLLS_SIDEWAYS_TODAY:
                    problems.append(f"{where}: scrolls sideways ({wide}px wider than the window)")
                if wide <= 0 and path in SCROLLS_SIDEWAYS_TODAY:
                    problems.append(f"{where}: no longer scrolls sideways; take it off SCROLLS_SIDEWAYS_TODAY")
            if shots is not None:
                page.screenshot(path=shots / screenshot_name(area, path, width), full_page=True)
    return problems


@needs_database
@pytest.mark.parametrize("area", PAGES)
def test_walkthrough(area, browser, served):
    base_url, administrator = served
    shots = screenshot_folder()
    context = browser.new_context(viewport={"width": 1440, "height": WIDTHS[1440]}, device_scale_factor=1,
                                  locale="de-CH")
    try:
        page = context.new_page()
        _sign_in(page, base_url, administrator)
        problems = _walk(page, base_url, area, shots)
    finally:
        context.close()
    assert not problems, f"{len(problems)} problem(s) on the {area} pages:\n" + "\n".join(problems)
