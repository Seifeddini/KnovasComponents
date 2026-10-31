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


# ---------------------------------------------------------------------------
# Step B1: workspaces. The package imported is $BUILDER_IMPORT_ZIP when set
# (the acceptance uses Client Zero's), else the fictitious sample package.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def import_zip(tmp_path_factory):
    given = os.environ.get("BUILDER_IMPORT_ZIP")
    if given:
        return Path(given)
    from builder import package
    from builder_harness import SAMPLE_PACKAGE

    path = tmp_path_factory.mktemp("builder-import") / "beispiel.zip"
    path.write_bytes(package.zip_files(package.files_from_directory(SAMPLE_PACKAGE)))
    return path


@pytest.fixture
def workspace_url():
    from builder_harness import MemoryWorkspaceStore

    user = StandInUser({"admin"})
    store = MemoryWorkspaceStore({str(user.id): user.display_name})
    server = make_server("127.0.0.1", 0, make_app(user, store), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _import_through_the_dialog(page, base, import_zip, shots=None, suffix=""):
    page.goto(f"{base}/builder/arbeitsbereich")
    page.locator("[data-import-open]").first.click()
    dialog = page.locator("#b-import")
    assert dialog.is_visible()
    if shots:
        page.screenshot(path=shots / f"b1-import-choose{suffix}.png")
    page.set_input_files("#b-import-file", str(import_zip))
    dialog.get_by_role("button", name="Paket prüfen").click()
    page.wait_for_selector("[data-import-step='confirm']:not([hidden])")
    if shots:
        page.screenshot(path=shots / f"b1-import-summary{suffix}.png")
    summary = {
        label: int(value)
        for label, value in zip(
            dialog.locator("[data-import-summary] .b-stat-label").all_inner_texts(),
            dialog.locator("[data-import-summary] .b-stat-value").all_inner_texts(),
        )
    }
    dialog.get_by_role("button", name="Arbeitsbereich anlegen").click()
    page.wait_for_url(f"{base}/builder/w/*")
    return summary


@pytest.mark.parametrize("width,scheme", [("1440", "light"), ("1440", "dark"), ("390", "light")])
def test_workspace_screenshots(browser, workspace_url, shots, import_zip, width, scheme):
    page = _page(browser, width, scheme)
    suffix = f"-{width}-{scheme}"
    page.goto(f"{workspace_url}/builder/arbeitsbereich")
    if width == "1440" and scheme == "light":
        page.screenshot(path=shots / "b1-list-empty-1440-light.png")
    summary = _import_through_the_dialog(page, workspace_url, import_zip, shots, suffix)
    assert summary["Typen"] > 0
    assert _no_sideways_scroll(page)
    page.evaluate("document.fonts.ready")
    page.screenshot(path=shots / f"b1-overview{suffix}.png", full_page=True)
    page.goto(f"{workspace_url}/builder/arbeitsbereich")
    assert _no_sideways_scroll(page)
    page.screenshot(path=shots / f"b1-list{suffix}.png", full_page=True)
    page.context.close()


@pytest.mark.parametrize("width,scheme", [("1440", "light"), ("1440", "dark"), ("390", "light")])
def test_a_new_workspace_from_the_dialog(browser, workspace_url, shots, width, scheme):
    page = _page(browser, width, scheme)
    suffix = f"-{width}-{scheme}"
    page.goto(f"{workspace_url}/builder/arbeitsbereich")
    page.locator("[data-create-open]").first.click()
    dialog = page.locator("#b-create")
    assert dialog.is_visible()
    assert page.evaluate("document.activeElement.id") == "b-create-name"
    dialog.get_by_role("button", name="Weiter").click()
    assert "Bitte einen Namen" in dialog.locator("[data-create-error]").inner_text()
    page.fill("#b-create-name", "Muster Treuhand AG")
    page.screenshot(path=shots / f"b1-create{suffix}.png")
    dialog.get_by_role("button", name="Weiter").click()
    dialog.get_by_role("button", name="Weiter").click()
    dialog.get_by_role("button", name="Arbeitsbereich anlegen").click()
    page.wait_for_url(f"{workspace_url}/builder/w/*")
    assert "Muster Treuhand AG" in page.locator("h1").inner_text()
    assert _no_sideways_scroll(page)
    page.screenshot(path=shots / f"b1-created{suffix}.png", full_page=True)
    page.goto(f"{workspace_url}/builder/ontologie")
    assert "Noch keine Typen" in page.inner_text("main")
    page.screenshot(path=shots / f"b2-ontology-empty{suffix}.png")
    page.context.close()


def test_the_dialog_shows_a_refusal_and_keeps_focus_inside(browser, workspace_url, tmp_path):
    page = _page(browser, "1440", "light")
    page.goto(f"{workspace_url}/builder/arbeitsbereich")
    page.locator("[data-import-open]").first.click()
    bogus = tmp_path / "kaputt.zip"
    bogus.write_bytes(b"not a zip")
    page.set_input_files("#b-import-file", str(bogus))
    page.get_by_role("button", name="Paket prüfen").click()
    error = page.locator("[data-import-step='choose'] [data-import-error]")
    error.wait_for(state="visible")
    assert "kein ZIP-Archiv" in error.inner_text()
    page.keyboard.press("Escape")
    assert not page.locator("#b-import").is_visible()
    page.context.close()


# ---------------------------------------------------------------------------
# Step B2: the ontology, read-only. Same package as above.
# ---------------------------------------------------------------------------


def _open_ontology(page, base):
    page.goto(f"{base}/builder/ontologie")
    page.wait_for_function("window.__builderOntology && window.__builderOntology.readyMs >= 0", timeout=10_000)
    return page.evaluate("""() => ({
        sinceNavigation: Math.round(performance.now()),
        readyMs: window.__builderOntology.readyMs,
        types: window.__builderOntology.types,
        relations: window.__builderOntology.relations,
        expectedRelations: JSON.parse(document.getElementById('b-onto-data').textContent)
            .relations.filter(r => r.domain.length && r.range.length).length,
        expectedTypes: JSON.parse(document.getElementById('b-onto-data').textContent).counts.types,
    })""")


@pytest.mark.parametrize("width,scheme", [("1440", "light"), ("1440", "dark"), ("390", "light")])
def test_ontology_screens(browser, workspace_url, shots, import_zip, width, scheme):
    page = _page(browser, width, scheme)
    _import_through_the_dialog(page, workspace_url, import_zip)
    timing = _open_ontology(page, workspace_url)
    suffix = f"-{width}-{scheme}"

    # Acceptance: every type and every relation drawn, in under two seconds.
    assert timing["types"] == timing["expectedTypes"]
    assert timing["relations"] == timing["expectedRelations"]
    assert timing["readyMs"] < 2000, timing
    assert _no_sideways_scroll(page)
    page.screenshot(path=shots / f"b2-canvas{suffix}.png")

    # The canvas opens as a tree: every subtype card to the right of its parent.
    misplaced = page.evaluate("""() => window.__builderOntology.cy.edges('.subtype:visible')
        .filter(e => e.source().position('x') <= e.target().position('x')).map(e => e.id())""")
    assert misplaced == []

    if width == "1440":
        page.locator("[data-onto-layout='grouped']").click()
        page.screenshot(path=shots / f"b2-canvas-grouped{suffix}.png")
        page.locator("[data-onto-layout='force']").click()
        page.screenshot(path=shots / f"b2-canvas-force{suffix}.png")
        page.locator("[data-onto-layout='tree']").click()

    # A selected type: pick the model type with the most attributes.
    chosen = page.evaluate("""() => {
        const data = JSON.parse(document.getElementById('b-onto-data').textContent);
        const node = data.nodes.filter(n => n.source === 'model').sort((a, b) => b.attribute_count - a.attribute_count)[0];
        window.__builderOntology.select(node.id, {center: true});
        return node.name;
    }""")
    page.wait_for_timeout(300)
    panel = page.locator("#b-onto-panel")
    assert panel.is_visible()
    assert "Attribute" in panel.inner_text() and "Ausgehende Beziehungen" in panel.inner_text()
    page.screenshot(path=shots / f"b2-selected{suffix}.png")
    panel.locator(".b-panel-head [data-panel-close]").click()

    page.locator("#b-onto-tab-tree").click()
    page.screenshot(path=shots / f"b2-tree{suffix}.png")
    page.locator("#b-onto-tab-problems").click()
    page.screenshot(path=shots / f"b2-problems{suffix}.png", full_page=True)
    assert chosen
    page.context.close()


def test_ontology_problems_of_the_sample_package(browser, workspace_url, shots, tmp_path):
    """The sample package has the problems Client Zero has fixed: show them."""
    from builder import package
    from builder_harness import SAMPLE_PACKAGE

    sample = tmp_path / "beispiel.zip"
    sample.write_bytes(package.zip_files(package.files_from_directory(SAMPLE_PACKAGE)))
    page = _page(browser, "1440", "light")
    _import_through_the_dialog(page, workspace_url, sample)
    _open_ontology(page, workspace_url)
    page.locator("#b-onto-tab-problems").click()
    assert not page.locator("[data-onto-filters]").is_visible()
    problems = page.locator("[data-onto-problems]").inner_text()
    assert "relates_to" in problems
    page.screenshot(path=shots / "b2-problems-sample-1440-light.png", full_page=True)
    page.locator("[data-onto-problems] .b-problem button").first.click()
    assert page.locator("#b-onto-panel").is_visible()
    assert "relates_to" in page.locator("#b-onto-panel").inner_text()
    faded = page.evaluate("window.__builderOntology.cy.nodes('.type.faded').length")
    assert faded == 0  # a relation with nothing to draw fades nothing
    page.wait_for_timeout(400)  # the panel's entrance
    page.screenshot(path=shots / "b2-problem-on-canvas-1440-light.png")
    page.context.close()


def test_ontology_filters_search_and_problem_links(browser, workspace_url, import_zip):
    page = _page(browser, "1440", "light")
    _import_through_the_dialog(page, workspace_url, import_zip)
    _open_ontology(page, workspace_url)
    visible = "() => window.__builderOntology.cy.nodes('.type').filter(n => n.style('display') !== 'none').length"
    everything = page.evaluate(visible)

    page.fill("#b-onto-search", "zzzz-nothing")
    assert page.evaluate(visible) == 0
    assert page.locator("[data-onto-canvas-empty]").is_visible()
    page.fill("#b-onto-search", "")
    assert page.evaluate(visible) == everything

    page.locator("label:has([data-filter='level'][value='core'])").click()
    without_core = page.evaluate(visible)
    assert 0 < without_core < everything
    page.locator("label:has([data-filter='level'][value='core'])").click()

    # The tree collapses and expands.
    page.locator("#b-onto-tab-tree").click()
    rows = page.locator("[data-onto-tree] > li").count()
    page.locator("[data-onto-tree] .b-tree-toggle").first.click()
    assert page.locator("[data-onto-tree] > li").count() < rows
    page.locator("[data-onto-tree] .b-tree-toggle").first.click()
    assert page.locator("[data-onto-tree] > li").count() == rows

    # A problem's link leads to its element on the canvas.
    page.locator("#b-onto-tab-problems").click()
    page.locator("[data-onto-problems] details").first.evaluate("d => d.open = true")
    page.locator("[data-onto-problems] .b-problem button").first.click()
    assert page.locator("#b-onto-view-canvas").is_visible()
    assert page.locator("#b-onto-panel").is_visible()
    page.context.close()


def test_the_theme_choice_is_remembered(browser, base_url):
    page = _page(browser, "1440", "light")
    page.goto(f"{base_url}/builder/arbeitsbereich")
    page.locator('label:has(input[value="dark"])').click()
    page.reload()
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark"
    assert page.locator('input[value="dark"]').is_checked()
    page.context.close()


# ---------------------------------------------------------------------------
# Step B3: editing the ontology. Needs Client Zero ($BUILDER_IMPORT_ZIP).
# ---------------------------------------------------------------------------


@pytest.fixture
def editing_server():
    from builder_harness import MemoryWorkspaceStore

    user = StandInUser({"admin"})
    store = MemoryWorkspaceStore({str(user.id): user.display_name})
    server = make_server("127.0.0.1", 0, make_app(user, store), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", store
    server.shutdown()


def _problems_of(page, target):
    return page.evaluate("""(target) => window.__builderOntology.data().problems
        .filter(p => p.target === target).map(p => p.kind).sort()""", target)


def _wait_for(page, expression, arg=None):
    page.wait_for_function(expression, arg=arg, timeout=10_000)


def _has_type(key):
    return f"() => window.__builderOntology.data().nodes.some(n => n.key === '{key}')"


_INVOICE = "window.__builderOntology.data().nodes.find(n => n.key === 'Invoice')"


def test_editing_client_zero(browser, editing_server, shots, import_zip):
    base, store = editing_server
    page = _page(browser, "1440", "light")
    _import_through_the_dialog(page, base, import_zip)
    _open_ontology(page, base)
    if not page.evaluate(_has_type("RunEvent")):
        pytest.skip("needs the Client Zero package in $BUILDER_IMPORT_ZIP")
    [workspace] = store.list()
    page.screenshot(path=shots / "b3-01-before.png")

    # A new type under a core class.
    page.locator(".b-onto-edit-bar [data-onto-new-type]").click()
    dialog = page.locator("#b-onto-new-type")
    page.fill("#b-nt-key", "Invoice")
    page.fill("#b-nt-de", "Rechnung")
    page.fill("#b-nt-en", "Invoice")
    page.select_option("#b-nt-parent", "euo:Transaction")
    page.screenshot(path=shots / "b3-02-new-type.png")
    dialog.get_by_role("button", name="Typ anlegen").click()
    _wait_for(page, _has_type("Invoice"))
    panel = page.locator("#b-onto-panel")
    assert panel.is_visible() and "Rechnung" in panel.locator("[data-onto-panel-title]").inner_text()
    assert _problems_of(page, "type:Invoice") == ["description_incomplete", "type_no_description"]

    # Three attributes; the enum without values is a problem until it has some.
    for name, datatype in (("invoice_number", "text"), ("status", "enum"), ("due_date", "date")):
        page.fill("[data-ed-attr-new]", name)
        page.locator(".b-ed-attr-add select").select_option(datatype)
        page.locator(".b-ed-attr-add button[type=submit]").click()
        _wait_for(page, f"() => {_INVOICE}.attributes.some(a => a.name === '{name}')")
    assert "enum_no_values" in _problems_of(page, "type:Invoice")
    page.screenshot(path=shots / "b3-03-attributes-enum-problem.png")
    values = page.locator("[data-attr='status'] > .b-field input")
    values.fill("open, paid, overdue")
    values.press("Tab")
    _wait_for(page, "() => !window.__builderOntology.data().problems.some(p => p.kind === 'enum_no_values')")
    assert page.evaluate(f"() => {_INVOICE}.attributes.map(a => a.name)") == ["invoice_number", "status", "due_date"]
    page.locator("[data-attr='due_date'] button[aria-label^='Nach oben']").click()
    _wait_for(page, f"() => {_INVOICE}.attributes[1].name === 'due_date'")

    # Rule 8: the description and the example clear the description problems.
    about = page.locator("#b-onto-panel .b-ed-form").nth(1)
    about.locator("textarea").nth(0).fill("A bill the company sends for work delivered.")
    about.locator("textarea").nth(1).fill("Invoice 2026-114 for the pilot with a fiduciary firm.")
    assert not any("Beispiel" in t for t in about.locator(".b-checklist .is-missing").all_inner_texts())
    about.get_by_role("button", name="Übernehmen").click()
    _wait_for(page, """() => !window.__builderOntology.data().problems.some(p => p.target === 'type:Invoice'
        && (p.kind === 'type_no_description' || p.kind === 'description_incomplete'))""")
    page.screenshot(path=shots / "b3-04-description.png")

    # Relation 1: drawn from the Invoice card to the Company card.
    page.locator("[data-onto-connect]").click()
    points = page.evaluate("""() => {
        const cy = window.__builderOntology.cy;
        const company = window.__builderOntology.data().nodes.find(n => n.key === 'Company');
        cy.fit(cy.collection([cy.getElementById('type:Invoice'), cy.getElementById(company.id)]), 120);
        const box = document.getElementById('b-onto-graph').getBoundingClientRect();
        const at = (id) => { const p = cy.getElementById(id).renderedPosition(); return [box.left + p.x, box.top + p.y]; };
        return [at('type:Invoice'), at(company.id)];
    }""")
    page.mouse.move(*points[0])
    page.mouse.down()
    page.mouse.move((points[0][0] + points[1][0]) / 2, (points[0][1] + points[1][1]) / 2, steps=5)
    page.mouse.move(*points[1], steps=5)
    page.mouse.up()
    relation = page.locator("#b-onto-new-relation")
    relation.wait_for(state="visible")
    assert page.evaluate("() => Array.from(document.querySelectorAll('#b-nr-domain option:checked')).map(o => o.value)") == ["Invoice"]
    page.fill("#b-nr-key", "billed_to")
    page.fill("#b-nr-family", "Market")
    page.fill("#b-nr-de", "ist gestellt an")
    page.fill("#b-nr-en", "is billed to")
    page.fill("#b-nr-sentence", "Invoice billed_to Company: the company the invoice is addressed to.")
    page.screenshot(path=shots / "b3-05-draw-relation.png")
    relation.get_by_role("button", name="Beziehung anlegen").click()
    _wait_for(page, "() => window.__builderOntology.data().relations.some(r => r.key === 'billed_to')")
    page.locator("[data-onto-connect]").click()

    # Relation 2: a name from the controlled list extends that relation.
    page.evaluate("() => window.__builderOntology.select('type:Invoice')")
    page.locator("#b-onto-panel").get_by_role("button", name="Beziehung hinzufügen").click()
    page.fill("#b-nr-key", "about")
    assert relation.locator("[data-ed-existing]").is_visible()
    page.select_option("#b-nr-range", ["Opportunity"])
    relation.get_by_role("button", name="Beziehung erweitern").click()
    _wait_for(page, """() => { const r = window.__builderOntology.data().relations.find(r => r.key === 'about');
        return r.domain_names.includes('Invoice') && r.range_names.includes('Opportunity'); }""")
    node = page.evaluate(f"() => {_INVOICE}")
    assert len(node["attributes"]) == 3 and len(node["outgoing"]) == 2
    page.evaluate("() => window.__builderOntology.select('type:Invoice', {center: true})")
    page.wait_for_timeout(300)
    page.screenshot(path=shots / "b3-06-type-with-relations.png")

    # Retire a type a process uses: a problem, until undo takes it back.
    run_event = page.evaluate("() => window.__builderOntology.data().nodes.find(n => n.key === 'RunEvent').id")
    page.evaluate("(id) => window.__builderOntology.select(id, {center: true})", run_event)
    page.locator("#b-onto-panel").get_by_role("button", name="Typ stilllegen").click()
    _wait_for(page, "(id) => window.__builderOntology.data().problems.some(p => p.kind === 'retired_in_use' && p.target === id)", run_event)
    page.wait_for_timeout(300)
    page.screenshot(path=shots / "b3-07-retired-problem.png")
    page.locator("[data-onto-undo]").click()
    _wait_for(page, "() => !window.__builderOntology.data().problems.some(p => p.kind === 'retired_in_use')")
    assert not page.evaluate("(id) => window.__builderOntology.data().nodes.find(n => n.id === id).retired", run_event)
    assert page.locator("[data-onto-redo]").is_enabled()
    page.wait_for_timeout(300)
    page.screenshot(path=shots / "b3-08-after-undo.png")

    # Every change is in the log, with actor and before/after.
    edits = [c for c in store.rows[workspace.id]["changes"] if c.get("batch")]
    assert edits and all(c["actor"] for c in edits)
    created = [c for c in edits if c["path"] == "ontology.types/Invoice" and c["before"] is None]
    assert created and created[0]["after"]["parent"] == "euo:Transaction"
    retired = [c for c in edits if c["path"] == "ontology.types/RunEvent"]
    assert [bool((c["after"] or {}).get("retired")) for c in reversed(retired)] == [True, False]

    # The tree: Invoice under Transaction; the problems list has no problem left.
    page.locator("#b-onto-tab-tree").click()
    page.locator("[data-node='type:Invoice']").scroll_into_view_if_needed()
    page.screenshot(path=shots / "b3-09-tree.png")
    page.locator("#b-onto-tab-problems").click()
    assert page.evaluate("() => window.__builderOntology.data().counts.problems.critical") == 0
    page.screenshot(path=shots / "b3-10-problems.png")
    page.context.close()


# ---------------------------------------------------------------------------
# Step B4: a workspace from industry packs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width,scheme", [("1440", "light"), ("1440", "dark"), ("390", "light")])
def test_a_law_firm_from_the_legal_pack(browser, editing_server, shots, width, scheme):
    base, store = editing_server
    page = _page(browser, width, scheme)
    suffix = f"-{width}-{scheme}"
    page.goto(f"{base}/builder/arbeitsbereich")
    page.locator("[data-create-open]").first.click()
    dialog = page.locator("#b-create")
    page.fill("#b-create-name", "Kanzlei Muster")
    page.screenshot(path=shots / f"b4-wizard-1-name{suffix}.png")
    dialog.get_by_role("button", name="Weiter").click()
    page.screenshot(path=shots / f"b4-wizard-2-tenant{suffix}.png")
    dialog.get_by_role("button", name="Weiter").click()
    dialog.locator(".b-pack-card", has_text="Recht").locator("input").check()
    assert _no_sideways_scroll(page)
    page.screenshot(path=shots / f"b4-wizard-3-packs{suffix}.png")
    dialog.get_by_role("button", name="Arbeitsbereich anlegen").click()
    page.wait_for_url(f"{base}/builder/w/*")
    [workspace] = store.list()
    assert store.model(workspace.id)["packs"] == [{"key": "legal", "version": "1.0"}]

    # Two company types that extend the pack's.
    _open_ontology(page, base)
    assert page.evaluate("() => window.__builderOntology.data().nodes.filter(n => n.level === 'pack').length") == 8
    for key, de, parent in (("ConflictCheck", "Kollisionsprüfung", "Matter"), ("TimeEntry", "Leistungserfassung", "Matter")):
        page.locator(".b-onto-edit-bar [data-onto-new-type]").click()
        page.fill("#b-nt-key", key)
        page.fill("#b-nt-de", de)
        page.select_option("#b-nt-parent", parent)
        page.locator("#b-onto-new-type").get_by_role("button", name="Typ anlegen").click()
        _wait_for(page, _has_type(key))
    # The company's own attribute on the pack's Matter.
    page.evaluate("() => window.__builderOntology.select('type:Matter')")
    page.fill("[data-ed-attr-new]", "billing_partner")
    page.locator(".b-ed-attr-add button[type=submit]").click()
    _wait_for(page, "() => window.__builderOntology.data().nodes.find(n => n.key === 'Matter')"
                    ".attributes.some(a => a.name === 'billing_partner' && a.level === 'company')")
    # ConflictCheck is a candidate for the legal pack.
    page.evaluate("() => window.__builderOntology.select('type:ConflictCheck')")
    page.locator("#b-onto-panel select").filter(has_text="Kandidat für Recht").select_option("legal")
    _wait_for(page, "() => window.__builderOntology.data().candidates.length === 1")
    assert "10 Typen" in page.locator("[data-onto-head-types]").inner_text()
    page.locator("#b-onto-panel [data-panel-close]").first.click()
    page.evaluate("() => window.__builderOntology.cy.fit(window.__builderOntology.cy.nodes('.type'), 24)")
    page.wait_for_timeout(300)
    assert _no_sideways_scroll(page)
    page.screenshot(path=shots / f"b4-canvas-pack-and-company{suffix}.png")
    page.evaluate("() => window.__builderOntology.select('type:Matter', {center: true})")
    page.wait_for_timeout(300)
    page.screenshot(path=shots / f"b4-pack-type-extended{suffix}.png")
    page.locator("#b-onto-tab-candidates").click()
    page.screenshot(path=shots / f"b4-candidates{suffix}.png")

    # A newer pack than the workspace took over: the overview offers the update, the diff shows it.
    row = store.rows[workspace.id]
    row["model"]["packs"] = [{"key": "legal", "version": "0.9"}]
    matter = next(t for t in row["model"]["ontology"]["types"] if t["key"] == "Matter")
    matter["attributes"] = [a for a in matter["attributes"] if a["name"] != "opened_on"]
    row["model"]["ontology"]["types"] = [t for t in row["model"]["ontology"]["types"] if t["key"] != "Claim"]
    row["model"]["ontology"]["types"].append({"key": "Hearing", "level": "pack", "level_label": "pack", "pack": "legal",
                                             "parent": "euo:Event", "attributes": [], "label": {"de": "Verhandlung"}})
    page.goto(f"{base}/builder/w/{workspace.id}")
    page.get_by_role("link", name="Änderungen prüfen").click()
    page.wait_for_url(f"{base}/builder/w/{workspace.id}/pakete/legal")
    text = page.inner_text("main")
    assert "Attribut opened_on" in text and "Claim" in text and "Hearing" in text
    assert "billing_partner" not in text  # the company's own attribute stays and is no change
    page.screenshot(path=shots / f"b4-pack-diff{suffix}.png", full_page=True)
    page.get_by_role("button", name="Version 1.0 übernehmen").click()
    page.wait_for_url(f"{base}/builder/ontologie")
    model = store.model(workspace.id)
    assert model["packs"] == [{"key": "legal", "version": "1.0"}]
    names = [a["name"] for a in next(t for t in model["ontology"]["types"] if t["key"] == "Matter")["attributes"]]
    assert "opened_on" in names and names[-1] == "billing_partner"
    assert next(t for t in model["ontology"]["types"] if t["key"] == "Hearing")["retired"] is True
    page.context.close()


# ---------------------------------------------------------------------------
# Step B5: review and publish, with a stand-in engine (a tenant in memory).
# ---------------------------------------------------------------------------


@pytest.fixture
def publishing_server():
    from builder_harness import FakeEngine, MemoryWorkspaceStore

    author, reviewer = StandInUser({"admin"}, "Alex Beispiel"), StandInUser({"admin"}, "Robin Muster")
    store = MemoryWorkspaceStore({str(author.id): author.display_name, str(reviewer.id): reviewer.display_name})
    engine = FakeEngine(tenant_types={"Opportunity": {"stage"}}, refuse={"AlloyCheck"})
    app = make_app(author, store, tenant="test-tenant-0001", engine=engine)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"base": f"http://127.0.0.1:{server.server_port}", "store": store, "engine": engine,
           "gate": app.extensions["builder_gate"], "author": author, "reviewer": reviewer}
    server.shutdown()


def test_publishing_client_zero(browser, publishing_server, shots, import_zip):
    from builder import editing

    s = publishing_server
    base, store = s["base"], s["store"]
    page = _page(browser, "1440", "light")
    _import_through_the_dialog(page, base, import_zip)
    [workspace] = store.list()
    if not any(t["key"] == "RunEvent" for t in store.model(workspace.id)["ontology"]["types"]):
        pytest.skip("needs the Client Zero package in $BUILDER_IMPORT_ZIP")

    # The B3 changes, made by the author.
    for op in ({"op": "type.create", "key": "Invoice", "parent": "euo:Transaction", "label": {"de": "Rechnung", "en": "Invoice"}},
               {"op": "attribute.add", "key": "Invoice", "attribute": {"name": "invoice_number"}},
               {"op": "attribute.add", "key": "Invoice", "attribute": {"name": "status", "datatype": "enum"}},
               {"op": "attribute.update", "key": "Invoice", "name": "status", "set": {"enum_values": ["open", "paid"]}},
               {"op": "attribute.add", "key": "Invoice", "attribute": {"name": "due_date", "datatype": "date"}},
               {"op": "relation.create", "key": "billed_to", "domain": ["Invoice"], "range": ["Company"], "family": "Market"},
               {"op": "type.retire", "key": "AlloyCheck"}):
        store.edit(workspace.id, s["author"].id, lambda m, op=op: editing.apply(m, op))

    page.goto(f"{base}/builder/pruefen")
    page.get_by_role("button", name="Mit diesem Mandanten verbinden").click()
    page.wait_for_selector("[data-publish-dry-run]")
    assert "Invoice" in page.inner_text("main") and "AlloyCheck" in page.inner_text("main")
    page.screenshot(path=shots / "b5-1-change-set.png", full_page=True)

    page.get_by_role("button", name="Probelauf starten").click()
    page.wait_for_selector(".b-rv-counts")
    assert "Invoice" not in s["engine"].tenant
    page.locator("#b-rv-dry").scroll_into_view_if_needed()
    page.screenshot(path=shots / "b5-2-dry-run.png", full_page=True)
    assert page.locator("[data-publish-approve]").count() == 0  # the author cannot approve

    s["gate"].user = s["reviewer"]
    page.reload()
    page.get_by_role("button", name="Diesen Stand freigeben").click()
    page.wait_for_selector(".b-rv-approvals")
    assert "Freigegeben von Robin Muster" in page.inner_text("main")
    page.locator("#b-rv-approve").scroll_into_view_if_needed()
    page.screenshot(path=shots / "b5-3-approval.png")

    s["gate"].user = s["author"]
    page.reload()
    page.get_by_role("button", name="Veröffentlichen").click()
    confirm = page.locator("#b-rv-confirm")
    confirm.wait_for(state="visible")
    assert "AlloyCheck" in confirm.inner_text()
    page.screenshot(path=shots / "b5-4-confirm-retirement.png")
    confirm.locator("[data-publish-confirm-box]").check()
    confirm.get_by_role("button", name="Stilllegen und veröffentlichen").click()
    page.wait_for_selector("text=Nichts zu veröffentlichen")
    assert s["engine"].tenant["Invoice"] == {"status"}
    assert store.published_bundle(workspace.id)["number"] == 1
    page.screenshot(path=shots / "b5-5-result.png", full_page=True)
    page.context.close()
