"""Acceptance for build plan step B0, in a real browser (Playwright).

Takes the step's screenshots -- the shell and the style guide at 1440 and
390 px, light and dark -- and checks what a screenshot cannot: keyboard focus
is visible, nothing scrolls sideways on a phone, the drawer, tabs, side panel
and toasts work from the keyboard.

Screenshots land in $BUILDER_SCREENSHOTS when it is set, else in pytest's tmp
dir. Skipped when Playwright or its Chromium is not installed.
"""
import os
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from builder_harness import StandInUser, make_app

sync_api = pytest.importorskip("playwright.sync_api")

WIDTHS = {"1440": (1440, 900), "390": (390, 844)}
SCHEMES = ("light", "dark")


@pytest.fixture(scope="module")
def base_url():
    server = make_server("127.0.0.1", 0, make_app(StandInUser({"admin"})), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - no browser installed here
            pytest.skip(f"Chromium for Playwright is not installed: {exc}")
        yield chromium
        chromium.close()


@pytest.fixture(scope="module")
def shots(tmp_path_factory):
    target = os.environ.get("BUILDER_SCREENSHOTS")
    path = Path(target) if target else tmp_path_factory.mktemp("builder-screens")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _page(browser, width, scheme):
    w, h = WIDTHS[width]
    context = browser.new_context(viewport={"width": w, "height": h}, color_scheme=scheme, device_scale_factor=1)
    return context.new_page()


def _no_sideways_scroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("scheme", SCHEMES)
def test_shell_screenshots(browser, base_url, shots, width, scheme):
    page = _page(browser, width, scheme)
    page.goto(f"{base_url}/builder/ontologie")
    page.evaluate("document.fonts.ready")
    assert _no_sideways_scroll(page)
    page.screenshot(path=shots / f"b0-shell-{width}-{scheme}.png", full_page=True)

    if width == "390":
        # The rail is a drawer on a phone: closed at first, opened by the menu.
        assert not page.locator("#b-rail").is_visible()
        page.click("[data-drawer-toggle]")
        page.wait_for_timeout(300)
        assert page.locator("#b-rail").is_visible()
        page.screenshot(path=shots / f"b0-shell-{width}-{scheme}-stufen.png")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        assert not page.locator("#b-rail").is_visible()
    else:
        page.click("[data-popover-toggle]")
        assert page.locator("#b-workspace-menu").is_visible()
        page.screenshot(path=shots / f"b0-shell-{width}-{scheme}-arbeitsbereich.png")
        page.keyboard.press("Escape")
        assert not page.locator("#b-workspace-menu").is_visible()
    page.context.close()


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("scheme", SCHEMES)
def test_styleguide_screenshots(browser, base_url, shots, width, scheme):
    page = _page(browser, width, scheme)
    view = "hell" if scheme == "light" else "dunkel"
    page.goto(f"{base_url}/builder/_styleguide?ansicht={view}")
    page.evaluate("document.fonts.ready")
    assert _no_sideways_scroll(page)
    page.screenshot(path=shots / f"b0-styleguide-{width}-{scheme}.png", full_page=True)
    page.context.close()


def test_keyboard_focus_is_visible(browser, base_url, shots):
    page = _page(browser, "1440", "light")
    page.goto(f"{base_url}/builder/arbeitsbereich")
    seen = []
    for _ in range(6):
        page.keyboard.press("Tab")
        seen.append(page.evaluate("""() => {
            const el = document.activeElement;
            const s = getComputedStyle(el);
            return {tag: el.tagName, text: (el.textContent || '').trim().slice(0, 30),
                    outline: s.outlineStyle, width: parseFloat(s.outlineWidth)};
        }"""))
    # The first stop is the skip link; every stop draws a real outline.
    assert seen[0]["text"] == "Zum Inhalt springen"
    for stop in seen:
        assert stop["outline"] != "none" and stop["width"] >= 2, stop
    page.keyboard.press("Tab")
    page.screenshot(path=shots / "b0-focus-1440-light.png")
    page.context.close()


def test_tabs_panel_and_toasts_work_from_the_keyboard(browser, base_url):
    page = _page(browser, "1440", "light")
    page.goto(f"{base_url}/builder/_styleguide?ansicht=hell")

    first_tab = page.locator("#sg-tab1-light")
    first_tab.focus()
    page.keyboard.press("ArrowRight")
    assert page.locator("#sg-tab2-light").get_attribute("aria-selected") == "true"
    assert page.locator("#sg-tp2-light").is_visible()
    assert not page.locator("#sg-tp1-light").is_visible()

    panel = page.locator("#sg-panel-light")
    handle = panel.locator("[data-panel-resize]")
    before = panel.bounding_box()["width"]
    handle.focus()
    for _ in range(4):
        page.keyboard.press("ArrowLeft")
    assert panel.bounding_box()["width"] == pytest.approx(before + 64, abs=1)
    assert handle.get_attribute("aria-valuenow") == str(round(before + 64))

    panel.locator(".b-panel-head [data-panel-close]").click()
    assert not panel.is_visible()
    page.locator('[data-panel-open="sg-panel-light"]').nth(1).click()
    assert panel.is_visible()
    page.keyboard.press("Escape")
    assert not panel.is_visible()

    page.locator('[data-toast-demo="critical"]').first.click()
    toast = page.locator("#b-toasts .b-toast--critical")
    assert toast.get_attribute("role") == "alert"
    toast.locator("button").click()
    assert page.locator("#b-toasts .b-toast").count() == 0
    page.context.close()


def test_the_theme_choice_is_remembered(browser, base_url):
    page = _page(browser, "1440", "light")
    page.goto(f"{base_url}/builder/arbeitsbereich")
    page.locator('label:has(input[value="dark"])').click()
    page.reload()
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark"
    assert page.locator('input[value="dark"]').is_checked()
    page.context.close()
