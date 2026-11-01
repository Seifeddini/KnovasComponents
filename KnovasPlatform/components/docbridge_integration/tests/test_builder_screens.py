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
    assert "Noch keine eigenen Typen" in page.inner_text("main")
    page.wait_for_function("window.__builderOntology && window.__builderOntology.data().counts.reference === 80")
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


# ---------------------------------------------------------------------------
# Step B6: processes as flows, published through B5.
# ---------------------------------------------------------------------------

_PROC = "window.__builderProcesses"
_LIFECYCLE = f"{_PROC}.data().processes.find(p => p.key === 'PROC-feature-lifecycle')"


def _proc(page, expression):
    return page.evaluate(f"() => {{ const p = {_LIFECYCLE}; return {expression}; }}")


def _drag_transition(page, source, target):
    """Shift-drag on the canvas from one step's box onto another's."""
    box = page.locator("#b-proc-graph").bounding_box()
    points = page.evaluate("([a, b]) => [a, b].map(id => window.__builderProcesses.cy().getElementById('s:' + id).renderedPosition())",
                           [source, target])
    page.keyboard.down("Shift")
    page.mouse.move(box["x"] + points[0]["x"], box["y"] + points[0]["y"])
    page.mouse.down()
    page.mouse.move(box["x"] + (points[0]["x"] + points[1]["x"]) / 2, box["y"] + points[0]["y"] + 40, steps=6)
    page.mouse.move(box["x"] + points[1]["x"], box["y"] + points[1]["y"], steps=6)
    page.mouse.up()
    page.keyboard.up("Shift")


def _check_text(page):
    return page.inner_text("[data-proc-check-list]")


def test_processes_of_client_zero(browser, publishing_server, shots, import_zip):
    s = publishing_server
    base, store = s["base"], s["store"]
    page = _page(browser, "1440", "light")
    _import_through_the_dialog(page, base, import_zip)
    [workspace] = store.list()
    if not any(p["key"] == "PROC-feature-lifecycle" for p in (store.model(workspace.id)["processes"] or {}).get("processes", [])):
        pytest.skip("needs the Client Zero package in $BUILDER_IMPORT_ZIP")

    # The Client Zero processes from the import, PROC-feature-lifecycle opened.
    page.goto(f"{base}/builder/prozesse#PROC-feature-lifecycle")
    page.wait_for_selector("html[data-proc-ready='true']")
    assert page.locator("[data-proc-list] li").count() == 7
    assert _proc(page, "p.steps.length") == 11
    assert "Keine Probleme" in _check_text(page)
    page.screenshot(path=shots / "b6-01-feature-lifecycle.png")

    # A new step after "verified".
    page.locator("[data-proc-new-step]").click()
    page.fill("#b-ns-id", "security-review")
    page.fill("#b-ns-name", "Sicherheitsprüfung")
    page.select_option("#b-ns-after", "verified")
    page.screenshot(path=shots / "b6-02-new-step.png")
    page.locator("#b-proc-new-step").get_by_role("button", name="Schritt anlegen").click()
    _wait_for(page, f"() => {_LIFECYCLE}.steps.some(s => s.id === 'security-review')")
    assert _proc(page, "p.steps.map(s => s.id).indexOf('security-review')") == 8

    # The check sees what the new step lacks: no way in, no way out.
    check = _check_text(page)
    assert "Kein Übergang führt vom ersten Schritt idea zu security-review" in check
    assert "Aus security-review führt kein Übergang hinaus" in check
    page.wait_for_timeout(400)  # the panel has slid in
    page.screenshot(path=shots / "b6-03-validation.png")

    # One exit criterion, written as text: a new process rule.
    panel = page.locator("#b-proc-panel")
    panel.locator("summary", has_text="Neues Kriterium als Text").click()
    criterion = panel.locator(".b-proc-new-criterion")
    criterion.get_by_label("Kriterium", exact=True).fill("Sicherheitsprüfung abgeschlossen")
    criterion.get_by_label("Attribut der Instanz").select_option("owner")
    criterion.get_by_role("button", name="Kriterium anlegen").click()
    _wait_for(page, f"() => {_LIFECYCLE}.steps.find(s => s.id === 'security-review').exit_criteria.length === 1")
    assert _proc(page, "p.steps.find(s => s.id === 'security-review').exit_criteria") == ["P-sicherheitspruefung-abgeschlossen"]

    # An artefact through the path picker: a passing test run that verifies the plan.
    panel.locator(".b-panel-section", has_text="Liefert").get_by_role("button", name="Artefakt hinzufügen").click()
    form = panel.locator(".b-proc-artefact-form")
    hop = form.get_by_label("Pfad, Schritt 1")
    offered = hop.locator("option").all_inner_texts()
    assert "← verifies · TestRun" in offered
    # Only relations whose domain fits a FeaturePlan: "serves" goes out of a plan, never into one.
    assert "serves → Capability" in offered and "← serves · FeaturePlan" not in offered
    hop.select_option(label="← verifies · TestRun")
    form.get_by_label("Nur wenn").select_option("outcome")
    form.get_by_label("gleich").fill("passed")
    form.scroll_into_view_if_needed()
    page.screenshot(path=shots / "b6-04-artefact.png")
    form.get_by_role("button", name="Artefakt hinzufügen").click()
    _wait_for(page, f"() => {_LIFECYCLE}.steps.find(s => s.id === 'security-review').produces.length === 1")

    # Transitions drawn on the canvas: in, out, and a rework loop back to implementing.
    for source, target in (("verified", "security-review"), ("security-review", "implemented"),
                           ("security-review", "implementing")):
        before = _proc(page, "p.transitions.length")
        _drag_transition(page, source, target)
        _wait_for(page, f"() => {_LIFECYCLE}.transitions.length === {before + 1}")
    transitions = _proc(page, "p.transitions.filter(t => t.from === 'security-review' || t.to === 'security-review')")
    assert {(t["from"], t["to"], t["rework"]) for t in transitions} == {
        ("verified", "security-review", False), ("security-review", "implemented", False),
        ("security-review", "implementing", True)}
    assert page.evaluate(f"() => {_PROC}.cy().getElementById('t:security-review>implementing').hasClass('rework')")
    assert "Keine Probleme" in _check_text(page)
    page.locator("#b-proc-panel [data-panel-close]").click()
    page.screenshot(path=shots / "b6-05-rework-loop.png")

    # A rework marked as forward closes a cycle: the check names it.
    page.evaluate(f"() => {_PROC}.cy().getElementById('t:security-review>implementing').emit('tap')")
    rework = panel.get_by_label("Nacharbeit: führt zu einem früheren Schritt zurück")
    rework.uncheck()
    _wait_for(page, "() => document.querySelector('[data-proc-check-list]').innerText.includes('bilden einen Kreis')")
    page.wait_for_timeout(400)
    page.screenshot(path=shots / "b6-06-cycle-found.png")
    panel.get_by_label("Nacharbeit: führt zu einem früheren Schritt zurück").check()
    _wait_for(page, "() => document.querySelector('[data-proc-check-list]').innerText.includes('Keine Probleme')")

    # Published through B5.
    page.goto(f"{base}/builder/pruefen")
    page.get_by_role("button", name="Mit diesem Mandanten verbinden").click()
    page.wait_for_selector("[data-publish-dry-run]")
    main = page.inner_text("main")
    assert "PROC-feature-lifecycle" in main
    assert "Schritt security-review" in main and "Nacharbeit security-review → implementing" in main
    page.screenshot(path=shots / "b6-07-change-set.png", full_page=True)
    page.get_by_role("button", name="Probelauf starten").click()
    page.wait_for_selector(".b-rv-counts")
    s["gate"].user = s["reviewer"]
    page.reload()
    page.get_by_role("button", name="Diesen Stand freigeben").click()
    page.wait_for_selector(".b-rv-approvals")
    s["gate"].user = s["author"]
    page.reload()
    page.get_by_role("button", name="Veröffentlichen").click()
    page.wait_for_selector("text=Nichts zu veröffentlichen")
    published = store.published_bundle(workspace.id)["bundle"]["sections"]["processes"]
    lifecycle = next(p for p in published["processes"] if p["key"] == "PROC-feature-lifecycle")
    assert ["security-review", "implementing", "rework"] in lifecycle["transitions"]
    assert any(r["id"] == "P-sicherheitspruefung-abgeschlossen" for r in published["rules"])
    page.screenshot(path=shots / "b6-08-published.png", full_page=True)
    page.context.close()


# ---------------------------------------------------------------------------
# Step B7: sources, each through its registered component. The GitHub runner
# reads the KnowledgeBase checkout ($KNOWLEDGEBASE_CLIENT_ZERO's repository)
# with brain-runner as it reads the repository on GitHub; the KnovasConnector
# is a folder answered as its /discover answers.
# ---------------------------------------------------------------------------


@pytest.fixture
def sources_server(tmp_path):
    from builder_harness import FakeConnector, MemorySecrets, MemoryWorkspaceStore, SAMPLE_PACKAGE, stand_in_readers

    pytest.importorskip("brain_runner.extractors")
    given = os.environ.get("KNOWLEDGEBASE_CLIENT_ZERO")
    checkout = Path(given).parents[2] if given else SAMPLE_PACKAGE
    share = tmp_path / "ablage"
    for path in ("Mandate/Muster AG/2026-03 Vollmacht.pdf", "Mandate/Muster AG/2026-04 Honorarvereinbarung.docx",
                 "Mandate/Muster AG/Korrespondenz/2026-05 Schreiben Gegenseite.pdf", "Mandate/Beispiel GmbH/Klage.pdf",
                 "Mandate/Beispiel GmbH/Beilagen/Vertrag.pdf", "Mandate/Beispiel GmbH/Notiz.txt",
                 "Vorlagen/Briefvorlage.docx"):
        (share / path).parent.mkdir(parents=True, exist_ok=True)
        (share / path).write_bytes(b"x" * (len(path) * 97))
    user = StandInUser({"admin"})
    store = MemoryWorkspaceStore({str(user.id): user.display_name})
    readers = stand_in_readers(github_root=checkout, connector=FakeConnector(share))
    app = make_app(user, store, tenant="test-tenant-0001", secrets=MemorySecrets(), readers=readers)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"base": f"http://127.0.0.1:{server.server_port}", "store": store, "readers": readers}
    server.shutdown()


def _register_component(page, base, kind, name, site, owner, shots=None, shot=None):
    page.goto(f"{base}/builder/verbinden")
    page.locator("[data-comp-new]").click()
    dialog = page.locator("#b-comp-new")
    dialog.locator(f"input[name='kind'][value='{kind}']").check()
    page.fill("#b-cn-name", name)
    page.fill("#b-cn-site", site)
    page.fill("#b-cn-owner", owner)
    if shots and shot:
        page.screenshot(path=shots / shot)
    dialog.get_by_role("button", name="Registrieren").click()
    page.wait_for_selector(f"[data-comp-table] >> text={name}")


def _add_source(page, kind, name, owner, settings, component):
    page.locator("[data-src-new]").click()
    dialog = page.locator("#b-src-new")
    dialog.locator(f"input[name='kind'][value='{kind}']").check()
    dialog.locator("#b-sn-component").select_option(label=component)
    page.fill("#b-sn-name", name)
    page.fill("#b-sn-owner", owner)
    for field, value in settings.items():
        page.fill(f"#b-sn-{field}", value)
    return dialog


def test_sources_of_client_zero(browser, sources_server, shots, import_zip):
    s = sources_server
    base = s["base"]
    page = _page(browser, "1440", "light")
    _import_through_the_dialog(page, base, import_zip)

    # Both components, registered for the tenant.
    _register_component(page, base, "github_runner", "GitHub-Runner", "Plattform", "Engine-Owner", shots, "b7-01-register.png")
    _register_component(page, base, "knovas_connector", "Dateiablage Zürich", "Büro Zürich", "IT")
    assert page.locator("[data-comp-table] tbody tr").count() == 2
    page.screenshot(path=shots / "b7-02-components.png")

    # The KnowledgeBase repository through the GitHub runner.
    page.goto(f"{base}/builder/quellen")
    page.wait_for_selector("html[data-src-ready='true']")
    dialog = _add_source(page, "github", "KnowledgeBase", "Engine-Owner",
                         {"repository": "Seifeddini/KnowledgeBase", "ref": "master"}, "GitHub-Runner (Plattform)")
    page.screenshot(path=shots / "b7-03-new-source.png")
    dialog.get_by_role("button", name="Quelle anlegen").click()
    page.wait_for_selector(".b-src-head >> text=KnowledgeBase")
    page.get_by_label("Neuer Zugang für KnowledgeBase").fill("ghp_read_only_for_the_acceptance")
    page.locator(".b-src-credential").get_by_role("button", name="Speichern").click()
    page.wait_for_selector(".b-src-credential >> text=Gesetzt")
    page.locator(".b-toast-close").first.click()
    assert "ghp_read_only_for_the_acceptance" not in page.content()
    page.get_by_role("button", name="Testen").click()
    page.wait_for_selector(".b-src-ok")
    assert "Erreicht" in page.inner_text("[data-src-result]")
    page.screenshot(path=shots / "b7-04-repository-tested.png")
    yaml_collection = page.locator("[data-src-result] tbody tr", has_text="*.yaml").first
    yaml_collection.get_by_role("button", name="Stichprobe").click()
    page.wait_for_selector(".b-src-profile")
    assert page.locator(".b-src-profile tbody tr").count() > 2
    page.locator("[data-src-sample]").scroll_into_view_if_needed()
    page.screenshot(path=shots / "b7-05-repository-sample.png")
    assert s["readers"].secrets[-1] == "ghp_read_only_for_the_acceptance"

    # A fileshare folder through the KnovasConnector.
    dialog = _add_source(page, "knovas_connector", "Ablage Mandate", "Sekretariat", {"folder": "Mandate"},
                         "Dateiablage Zürich (Büro Zürich)")
    dialog.get_by_role("button", name="Quelle anlegen").click()
    page.wait_for_selector(".b-src-head >> text=Ablage Mandate")
    page.get_by_role("button", name="Testen").click()
    page.wait_for_selector(".b-src-ok")
    counts = page.inner_text(".b-src-stats")
    assert "6" in counts and "Dateien" in counts
    page.screenshot(path=shots / "b7-06-folder-tested.png")
    page.locator("[data-src-result] tbody tr", has_text="Beispiel GmbH").get_by_role("button", name="Stichprobe").click()
    page.wait_for_selector(".b-src-profile")
    page.locator(".b-src-rows summary").click()
    assert "Klage.pdf" in page.inner_text("[data-src-sample]")
    page.locator("[data-src-sample]").scroll_into_view_if_needed()
    page.screenshot(path=shots / "b7-07-folder-sample.png")

    # Both are part of the draft, for the review.
    [workspace] = s["store"].list()
    assert {c["path"] for c in s["store"].draft_changes(workspace.id)} >= {"sources/knowledgebase", "sources/ablage-mandate"}
    page.context.close()


# ---------------------------------------------------------------------------
# Step B8: a source mapped to the ontology, dry-run and published through B5.
# The GitHub runner reads a KnowledgeBase checkout ($BUILDER_KB_CHECKOUT, a
# `git archive` of the repository) the way it reads GitHub.
# ---------------------------------------------------------------------------


@pytest.fixture
def mapping_server(tmp_path):
    from builder_harness import FakeConnector, FakeEngine, MemorySecrets, MemoryWorkspaceStore, stand_in_readers

    pytest.importorskip("brain_runner.extractors")
    checkout = os.environ.get("BUILDER_KB_CHECKOUT")
    if not checkout:
        pytest.skip("needs a KnowledgeBase checkout in $BUILDER_KB_CHECKOUT")
    share = tmp_path / "ablage"
    for path in ("Mandate/Muster AG/2026-03 Vollmacht.pdf", "Mandate/Muster AG/2026-04 Honorarvereinbarung.docx",
                 "Mandate/Beispiel GmbH/Klage.pdf", "Mandate/Beispiel GmbH/Notiz.txt", "Mandate/Nord AG/Vertrag.pdf"):
        (share / path).parent.mkdir(parents=True, exist_ok=True)
        (share / path).write_bytes(b"x" * (len(path) * 97))
    author, reviewer = StandInUser({"admin"}, "Alex Beispiel"), StandInUser({"admin"}, "Robin Muster")
    store = MemoryWorkspaceStore({str(author.id): author.display_name, str(reviewer.id): reviewer.display_name})
    engine = FakeEngine()
    secrets = MemorySecrets()
    app = make_app(author, store, tenant="test-tenant-0001", engine=engine, secrets=secrets,
                   readers=stand_in_readers(github_root=checkout, connector=FakeConnector(share)))
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"base": f"http://127.0.0.1:{server.server_port}", "store": store, "engine": engine, "secrets": secrets,
           "gate": app.extensions["builder_gate"], "author": author, "reviewer": reviewer}
    server.shutdown()


def _map_wait(page):
    page.wait_for_function("() => !document.documentElement.dataset.mapBusy", timeout=60_000)


def test_mapping_client_zero(browser, mapping_server, shots, import_zip):
    from builder import editing

    s = mapping_server
    base, store = s["base"], s["store"]
    page = _page(browser, "1440", "light")
    _import_through_the_dialog(page, base, import_zip)
    [workspace] = store.list()
    if not any(m["definition"]["id"] == "component-manifest" for m in store.model(workspace.id)["mappings"]):
        pytest.skip("needs the Client Zero package in $BUILDER_IMPORT_ZIP")
    tenant = "test-tenant-0001"
    runner = store.register_component(tenant, kind="github_runner", name="GitHub-Runner", site="Plattform", owner="IT",
                                      settings={}, actor_id=s["author"].id)
    connector = store.register_component(tenant, kind="knovas_connector", name="Dateiablage Zürich", site="Büro Zürich",
                                         owner="IT", settings={}, actor_id=s["author"].id)
    for op in ({"op": "source.create", "kind": "github", "name": "KnowledgeBase", "component": runner,
                "settings": {"repository": "Seifeddini/KnowledgeBase", "ref": "master"}},
               {"op": "source.credential", "key": "knowledgebase", "set": True},
               {"op": "source.create", "kind": "knovas_connector", "name": "Ablage Mandate", "component": connector,
                "settings": {"folder": "Mandate"}},
               # The test types: a file of the share and the mandate it is filed under.
               {"op": "type.create", "key": "Akte", "parent": "euo:Document", "label": {"de": "Akte", "en": "File"}},
               {"op": "attribute.add", "key": "Akte", "attribute": {"name": "dateiart"}},
               {"op": "attribute.add", "key": "Akte", "attribute": {"name": "geaendert_am", "datatype": "date"}},
               {"op": "type.create", "key": "Mandat", "parent": "euo:Agreement", "label": {"de": "Mandat", "en": "Mandate"}},
               {"op": "relation.create", "key": "abgelegt_unter", "domain": ["Akte"], "range": ["Mandat"], "family": "Structure"}):
        store.edit(workspace.id, s["author"].id, lambda m, op=op: editing.apply(m, op))
    s["secrets"].put(f"builder.sources.{workspace.id}.knowledgebase.credential", "ghp_read_only", s["author"])

    # Client Zero's component-manifest, rendered: read through the KnowledgeBase repository.
    page.goto(f"{base}/builder/quellen")
    assert page.locator("[data-src-mappings] tbody tr").count() == 23
    page.screenshot(path=shots / "b8-01-mappings.png")
    page.locator("[data-src-mappings] a", has_text="component-manifest").click()
    page.wait_for_selector("html[data-map-ready='true']")
    page.select_option("[data-map-reads]", "knowledgebase")
    _map_wait(page)
    assert page.locator(".b-map-field", has_text="c.value.name").count() == 1
    assert "c.key" in page.inner_text("[data-target='0:key']")
    assert "part_of → BoundedContext" in page.inner_text("[data-map-emits]")
    assert "L2-" in page.inner_text("[data-map-preview-body]")
    page.screenshot(path=shots / "b8-02-component-manifest.png")
    page.locator("[data-map-preview-body]").scroll_into_view_if_needed()
    page.screenshot(path=shots / "b8-03-component-manifest-preview.png")

    # A new mapping from the fileshare source to the test type.
    page.goto(f"{base}/builder/quellen")
    page.wait_for_selector("html[data-src-ready='true']")
    page.locator(".b-proc-item", has_text="Ablage Mandate").click()
    page.get_by_role("button", name="Testen").click()
    page.wait_for_selector(".b-src-ok")
    page.locator("[data-src-result] tbody tr", has_text="Alle Dateien").get_by_role("button", name="Zuordnen").click()
    page.fill("#b-mn-id", "ablage-akten")
    page.select_option("#b-mn-type", "Akte")
    page.locator("#b-map-new").get_by_role("button", name="Zuordnung anlegen").click()
    page.wait_for_url(f"{base}/builder/zuordnung/ablage-akten")
    page.wait_for_selector("html[data-map-ready='true']")
    _map_wait(page)

    def bind(field, target):
        page.locator(".b-map-field", has_text=field).first.drag_to(page.locator(f"[data-target='{target}']"))
        page.wait_for_function("(t) => window.__builderMapping.data().emits[0].targets.concat(window.__builderMapping.data().emits[0].relations)"
                               ".some(x => x.target === t && x.expression)", arg=target.split(":", 1)[1])
        _map_wait(page)

    bind("f.path", "0:key")
    bind("f.name", "0:name")
    bind("f.suffix", "0:fact:dateiart")
    bind("f.modified", "0:fact:geaendert_am")
    # Steps: the extension without its dot, the date without its time.
    row = page.locator(".b-map-row", has=page.locator("[data-target='0:fact:dateiart']"))
    row.get_by_role("button", name="Schritte").click()
    steps = row.locator(".b-map-steps")
    steps.get_by_label("Schritt", exact=True).select_option("replace")
    steps.get_by_label("Alt").fill(".")
    steps.get_by_label("Neu").fill("")
    steps.get_by_role("button", name="Schritt hinzufügen").click()
    page.screenshot(path=shots / "b8-04-steps.png")
    steps.get_by_role("button", name="Schritte übernehmen").click()
    page.wait_for_function("() => window.__builderMapping.data().emits[0].targets.find(t => t.target === 'fact:dateiart').steps.length === 1")
    _map_wait(page)
    row = page.locator(".b-map-row", has=page.locator("[data-target='0:fact:geaendert_am']"))
    row.get_by_role("button", name="Schritte").click()
    row.locator(".b-map-steps").get_by_label("Schritt", exact=True).select_option("date")
    row.locator(".b-map-steps").get_by_role("button", name="Schritt hinzufügen").click()
    row.locator(".b-map-steps").get_by_role("button", name="Schritte übernehmen").click()
    page.wait_for_function("() => window.__builderMapping.data().emits[0].targets.find(t => t.target === 'fact:geaendert_am').steps.length === 1")
    _map_wait(page)
    # A relation: the folder names the mandate (looked up by its key).
    page.get_by_label("Beziehung für Akte").select_option(label="abgelegt_unter → Mandat")
    page.get_by_label("Feld mit dem Schlüssel des anderen Knotens").select_option("f.path")
    page.get_by_role("button", name="Beziehung hinzufügen").click()
    page.wait_for_function("() => window.__builderMapping.data().emits[0].relations.length === 1")
    _map_wait(page)
    # The mandate is the folder below Mandate/: the path split at "/", its second part.
    row = page.locator(".b-map-row", has=page.locator("[data-target='0:relation:0']"))
    row.get_by_role("button", name="Schritte").click()
    row.locator(".b-map-steps").get_by_label("Schritt", exact=True).select_option("split")
    row.locator(".b-map-steps").get_by_label("Teil (0 = erster)").fill("1")
    row.locator(".b-map-steps").get_by_role("button", name="Schritt hinzufügen").click()
    row.locator(".b-map-steps").get_by_role("button", name="Schritte übernehmen").click()
    page.wait_for_function("() => window.__builderMapping.data().emits[0].relations[0].steps.length === 1")
    _map_wait(page)
    assert page.locator("[data-map-problems]").is_hidden()
    preview = page.inner_text("[data-map-preview-body]")
    assert "dateiart = pdf" in preview and "geaendert_am = 2026-10-30" in preview
    assert "Mandat Muster AG" in preview
    page.screenshot(path=shots / "b8-05-new-mapping.png")
    page.locator("[data-map-preview-body]").scroll_into_view_if_needed()
    page.screenshot(path=shots / "b8-06-preview.png")
    page.locator("[data-map-expert]").check()
    expressions = page.inner_text("[data-map-emits]")
    page.locator("[data-map-emits]").scroll_into_view_if_needed()
    page.screenshot(path=shots / "b8-07-expressions.png")
    del expressions
    assert "replace(f.suffix, '.', '')" in page.locator("[data-map-yaml]").text_content()

    # Dry run against the tenant: one file the tenant knows with another date, one it holds that is gone.
    store.link_tenant(workspace.id, tenant)
    s["engine"].nodes = {"Akte": {"Mandate/Muster AG/2026-03 Vollmacht.pdf": {"dateiart": "pdf", "geaendert_am": "2026-01-01"},
                                  "Mandate/Alt AG/Brief.pdf": {"dateiart": "pdf"}}}
    page.get_by_role("button", name="Probelauf").click()
    page.wait_for_selector("html[data-map-dry-run='done']")
    dry = page.inner_text("[data-map-dry-body]")
    assert "Hinzugefügt" in dry and "Löschschutz: frei" in dry
    page.locator("[data-map-dry-body]").scroll_into_view_if_needed()
    page.screenshot(path=shots / "b8-08-dry-run.png")

    # Published through B5: the change set names the mapping; the published version holds it.
    page.goto(f"{base}/builder/pruefen")
    page.wait_for_selector("[data-publish-dry-run]")
    assert "ablage-akten" in page.inner_text("main")
    page.get_by_role("button", name="Probelauf starten").click()
    page.wait_for_selector(".b-rv-counts")
    s["gate"].user = s["reviewer"]
    page.reload()
    page.get_by_role("button", name="Diesen Stand freigeben").click()
    page.wait_for_selector(".b-rv-approvals")
    s["gate"].user = s["author"]
    page.reload()
    page.get_by_role("button", name="Veröffentlichen").click()
    page.wait_for_selector("text=Nichts zu veröffentlichen")
    published = store.model(workspace.id)
    assert any(m["definition"]["id"] == "ablage-akten" for m in published["mappings"])
    page.screenshot(path=shots / "b8-09-published.png")
    page.context.close()


# ---------------------------------------------------------------------------
# Step B9: how the company sees its Brain -- the CTO profile of Client Zero,
# a new view, a page's slots, a role; previewed and published through B5.
# ---------------------------------------------------------------------------


@pytest.fixture
def views_server():
    from builder_harness import FakeEngine, MemoryWorkspaceStore

    author, reviewer = StandInUser({"admin"}, "Alex Beispiel"), StandInUser({"admin"}, "Robin Muster")
    store = MemoryWorkspaceStore({str(author.id): author.display_name, str(reviewer.id): reviewer.display_name})
    engine = FakeEngine(tenant_types={})
    written = []
    app = make_app(author, store, tenant="test-tenant-0001", engine=engine,
                   roles=["admin", "approver", "ingestion_manager", "member"],
                   roles_sink=lambda mapping, who: written.append(mapping))
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"base": f"http://127.0.0.1:{server.server_port}", "store": store, "engine": engine, "written": written,
           "gate": app.extensions["builder_gate"], "author": author, "reviewer": reviewer}
    server.shutdown()


_VIEWS = "window.__builderViews"


def _views_idle(page):
    page.wait_for_function("() => !document.documentElement.dataset.viewsBusy")
    # The toasts that confirmed the edits would cover the screenshot.
    page.evaluate("() => document.querySelectorAll('#b-toasts .b-toast').forEach(t => t.remove())")


def test_views_of_client_zero(browser, views_server, shots, import_zip):
    s = views_server
    base, store = s["base"], s["store"]
    page = _page(browser, "1440", "light")
    page.set_viewport_size({"width": 1600, "height": 1000})
    _import_through_the_dialog(page, base, import_zip)
    [workspace] = store.list()
    if not any(e["definition"].get("id") == "cto" for e in store.model(workspace.id)["profiles"]):
        pytest.skip("needs the Client Zero package in $BUILDER_IMPORT_ZIP")

    # The CTO profile, its start view (the system map) and its first page from sample data.
    page.goto(f"{base}/builder/ansichten")
    page.locator("[data-views-item='cto']").click()
    _views_idle(page)
    page.wait_for_selector("[data-views-preview-view='system-map']")
    assert "SYS-KNOVAS-SEMANTIX" in page.inner_text("[data-views-preview]")
    assert page.locator("[data-views-preview-page='PAGE-system']").count() == 1
    page.screenshot(path=shots / "b9-01-cto-profile.png")

    # A new view: open risks as a list, with the component each affects.
    page.get_by_role("button", name="Ansicht hinzufügen").click()
    dialog = page.locator("#b-vw-dialog")
    dialog.get_by_label("Kennung").fill("open-risks")
    dialog.get_by_label("Beschriftung", exact=True).first.fill("Offene Risiken")
    dialog.get_by_label("Darstellung").select_option("list")
    dialog.get_by_label("Wurzeltypen").fill("Risk")
    dialog.get_by_label("Spalten").fill("status, owner")
    dialog.get_by_role("button", name="Pfad hinzufügen").click()
    path = dialog.locator(".b-vw-path").last
    path.get_by_label("Beschriftung").fill("Komponente")
    hop = path.get_by_label("Nächster Schritt des Pfads")
    offered = hop.locator("option").all_inner_texts()
    assert "affects → Component" in offered
    hop.select_option(label="affects → Component")
    page.screenshot(path=shots / "b9-02-new-view.png")
    dialog.get_by_role("button", name="Ansicht hinzufügen").click()
    page.wait_for_function(f"() => {_VIEWS}.data().profiles.find(p => p.id === 'cto').definition.views.some(v => v.id === 'open-risks')")
    _views_idle(page)
    page.wait_for_selector("[data-views-preview-view='open-risks']")
    assert "Komponente" in page.inner_text("[data-views-preview-view='open-risks']")
    page.screenshot(path=shots / "b9-03-view-preview.png")

    # A page's slots: the component page shows its calls first and gets an "Einordnung" slot of facts.
    page.get_by_role("tab", name="Seiten").click()
    page.locator("[data-views-item='PAGE-component']").click()
    _views_idle(page)
    page.get_by_role("button", name="Ruft auf und wird aufgerufen von nach oben").click()
    page.wait_for_function(f"() => {_VIEWS}.data().pages.find(p => p.key === 'PAGE-component').sections[5].id === 'calls'")
    page.get_by_role("button", name="Abschnitt hinzufügen").click()
    dialog.get_by_label("Kennung").fill("classification")
    dialog.get_by_label("Titel (de)").fill("Einordnung")
    dialog.get_by_label("Titel (en)").fill("Classification")
    dialog.get_by_label("Darstellung").select_option("table")
    dialog.get_by_label("Inhalt").select_option("facts")
    dialog.get_by_label("Attribute").fill("alloy_status, design_pattern")
    dialog.get_by_role("button", name="Abschnitt hinzufügen").click()
    page.wait_for_function(f"() => {_VIEWS}.data().pages.find(p => p.key === 'PAGE-component').sections.some(s => s.id === 'classification')")
    _views_idle(page)
    page.wait_for_selector("[data-views-preview-slot='classification']")
    assert "alloy_status" in page.inner_text("[data-views-preview-slot='classification']")
    page.screenshot(path=shots / "b9-04-page-slots.png")

    # A spine as a tree of levels, one level open.
    page.get_by_role("tab", name="Hierarchien").click()
    page.locator("[data-views-level='L5']").first.click()
    page.wait_for_selector("[data-views-level-form='L5']")
    _views_idle(page)
    page.locator("[data-views-level-form='L5']").scroll_into_view_if_needed()
    page.screenshot(path=shots / "b9-05-spine-tree.png")

    # A role: approvers see the CTO profile.
    page.get_by_role("tab", name="Rollen").click()
    page.get_by_label("approver sieht CTO").check()
    page.wait_for_function(f"() => ({_VIEWS}.data().role_map.approver || []).includes('cto')")
    _views_idle(page)
    page.screenshot(path=shots / "b9-06-roles.png")

    # Published through B5: the change set names the view, the slots and the role; the bundle holds them.
    page.goto(f"{base}/builder/pruefen")
    page.get_by_role("button", name="Mit diesem Mandanten verbinden").click()
    page.wait_for_selector("[data-publish-dry-run]")
    main = page.inner_text("main")
    assert "Ansicht Offene Risiken" in main and "Abschnitt Einordnung" in main and "sieht cto" in main
    page.screenshot(path=shots / "b9-07-change-set.png", full_page=True)
    page.get_by_role("button", name="Probelauf starten").click()
    page.wait_for_selector(".b-rv-counts")
    s["gate"].user = s["reviewer"]
    page.reload()
    page.get_by_role("button", name="Diesen Stand freigeben").click()
    page.wait_for_selector(".b-rv-approvals")
    s["gate"].user = s["author"]
    page.reload()
    page.get_by_role("button", name="Veröffentlichen").click()
    page.wait_for_selector("text=Nichts zu veröffentlichen")
    assert store.published_bundle(workspace.id)["number"] == 1
    # The published model compiled by brain-runner itself: the bundle the views read.
    from builder.publisher import BrainRunnerEngine
    sections = BrainRunnerEngine(lambda: None).compile(store.model(workspace.id)).bundle["sections"]
    component = next(p for p in sections["pages"]["pages"] if p["key"] == "PAGE-component")
    assert [x["id"] for x in component["sections"]][-1] == "classification"
    assert any(v["id"] == "open-risks" for v in sections["profiles"]["cto"]["views"])
    assert s["written"] == [{"approver": ["cto"]}]
    page.screenshot(path=shots / "b9-08-published.png", full_page=True)
    page.context.close()


# ---------------------------------------------------------------------------
# Step B10: operating Client Zero once published. The stand-in tenant holds
# Client Zero's seed and sample nodes and what its mappings make of the
# KnowledgeBase checkout ($BUILDER_KB_CHECKOUT), read as brain-runner's
# snapshot reads a tenant; the sync's state and runs stand in for the
# Platform's sync. A few items are seeded so every tidiness kind shows.
# ---------------------------------------------------------------------------


def _client_zero_graph(model, checkout):
    from brain_runner.mapping import run_mapping
    from brain_runner.sources.local import LocalSource

    from builder import mappings as mapping_rules
    from builder import package as pkg
    from builder_harness import seed_from_runs, seed_graph

    root = Path(os.environ["KNOWLEDGEBASE_CLIENT_ZERO"])
    seed = pkg.load_yaml((root / "ontology/seed.yaml").read_bytes())
    sample = pkg.load_yaml((root / "ontology/sample.yaml").read_bytes())
    source = LocalSource(Path(checkout))
    runs = []
    for entry in model["mappings"]:
        try:
            runs.append(run_mapping(mapping_rules._written(entry["definition"]), source))
        except Exception:  # noqa: BLE001 - a mapping that reads git history: not in an archive
            continue
    decision = next(n for n in sample["nodes"] if n["type"] == "Decision")
    seeded = {"nodes": [
        # Near-identical names: the same decision entered twice.
        {"type": "Decision", "key": decision["key"] + "-copy", "name": decision["name"] + ".", "facts": {"status": "proposed"}},
    ], "edges": []}
    plan = next(n for n in sample["nodes"] if n["type"] == "FeaturePlan")
    return seed_graph(seed, sample, seed_from_runs(*runs), seeded, at="2026-10-30T09:00:00+00:00",
                      ages={plan["key"]: "2026-08-01T09:00:00+00:00"})


@pytest.fixture
def operate_server():
    from builder_harness import FakeEngine, MemoryWorkspaceStore

    author = StandInUser({"admin"}, "Alex Beispiel")
    store = MemoryWorkspaceStore({str(author.id): author.display_name})
    engine = FakeEngine(tenant_types={})
    sync = {}
    app = make_app(author, store, tenant="test-tenant-0001", engine=engine, sync=sync)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"base": f"http://127.0.0.1:{server.server_port}", "store": store, "engine": engine, "sync": sync,
           "author": author}
    server.shutdown()


def test_operate_client_zero(browser, operate_server, shots, import_zip):
    from builder import editing

    s = operate_server
    base, store = s["base"], s["store"]
    checkout = os.environ.get("BUILDER_KB_CHECKOUT")
    if not checkout or not os.environ.get("KNOWLEDGEBASE_CLIENT_ZERO"):
        pytest.skip("needs $BUILDER_KB_CHECKOUT and $KNOWLEDGEBASE_CLIENT_ZERO")
    page = _page(browser, "1440", "light")
    page.set_viewport_size({"width": 1440, "height": 1000})
    _import_through_the_dialog(page, base, import_zip)
    [workspace] = store.list()
    if not (store.model(workspace.id)["health"] or {}).get("types"):
        pytest.skip("needs the Client Zero package in $BUILDER_IMPORT_ZIP")

    # A type and an attribute added without a description: the tidiness list names them.
    for op in ({"op": "type.create", "key": "Workaround", "parent": "euo:Document", "label": {"de": "Umgehung", "en": "Workaround"}},
               {"op": "attribute.add", "key": "Workaround", "attribute": {"name": "expires_on", "datatype": "date"}}):
        store.edit(workspace.id, s["author"].id, lambda m, op=op: editing.apply(m, op))
    model = store.model(workspace.id)
    s["engine"].graph = _client_zero_graph(model, checkout)

    # The sync's state: one mapping ready, one stale, one held by the deletion guard.
    s["sync"].update({
        "state": {"mappings": {
            "component-manifest": {"conditions": {"Ready": {"status": "True", "message": "151 source items"},
                                                  "Stale": {"status": "False"}, "Degraded": {"status": "False"}},
                                   "observed": {"added": 3, "modified": 12, "retired": 0, "drift": 0, "nodes_in_brain": 151},
                                   "last_run_at": "2026-10-31T06:00:00+00:00", "last_success_at": "2026-10-31T06:00:00+00:00"},
            "spec-design-decisions": {"conditions": {"Ready": {"status": "False"}, "Degraded": {"status": "False"},
                                             "Stale": {"status": "True", "message": "window 7 d passed"}},
                              "last_run_at": "2026-10-20T06:00:00+00:00", "last_success_at": "2026-10-20T06:00:00+00:00"},
            "golden-invariants": {"conditions": {"Ready": {"status": "False"}, "Stale": {"status": "False"},
                                                 "Degraded": {"status": "True", "reason": "DeletionGuard",
                                                              "message": "would retire 40 of 166 (24 %)"}},
                                  "last_run_at": "2026-10-31T06:00:00+00:00"}}},
        "runs": [{"id": "r2", "kind": "probe", "status": "ok", "started_by": "siran@knovas.example",
                  "started_at": "2026-10-31T06:00:00+00:00", "finished_at": "2026-10-31T06:02:00+00:00",
                  "counts": {"golden-invariants": {"add": 0, "change": 2, "retire": 40, "blocked": True, "guard_tripped": True},
                             "component-manifest": {"add": 3, "change": 12, "retire": 0}},
                  "result": "Probelauf: 2 Zuordnungen geplant"},
                 {"id": "r1", "kind": "apply", "status": "ok", "started_by": None, "started_at": "2026-10-30T06:00:00+00:00",
                  "finished_at": "2026-10-30T06:05:00+00:00", "counts": {"component-manifest": {"add": 148}},
                  "result": "angewendet: 148 Schreibvorgänge"}],
    })
    store.link_tenant(workspace.id, "test-tenant-0001")
    draft = store.draft(workspace.id)
    store.record_run(workspace.id, kind="apply", actor_id=s["author"].id, tenant="test-tenant-0001",
                     content_hash=draft["content_hash"], bundle_hash="h", status="ok",
                     summary={"published_number": 1, "written": 412, "refused": []})
    store.publish(workspace.id, s["author"].id, draft["content_hash"], {"sections": {}}, "h")

    page.goto(f"{base}/builder/betrieb")
    page.wait_for_selector("[data-op-type='FeaturePlan']")
    health = page.inner_text("#b-op-health")
    assert "Component" in health and "ROLE-ENGINE-OWNER" in health
    nodes = int(page.inner_text("[data-op-type='Component'] td.is-num"))
    assert nodes > 100
    page.screenshot(path=shots / "b10-01-health.png")

    # Every tidiness kind has at least one item, each with where it is fixed.
    for kind in ("type_undescribed", "attribute_undescribed", "slot_empty", "orphan", "duplicate", "wrong_level", "stale_claim"):
        group = page.locator(f"[data-op-lint='{kind}']")
        count = int(group.locator("summary .b-tag").inner_text())
        assert count >= 1, kind
        assert group.locator("a", has_text="Beheben").count() >= 1, kind

    page.locator("#b-op-status").scroll_into_view_if_needed()
    status = page.inner_text("#b-op-status")
    assert "Löschschutz: 40 stilllegen" in status and "Gestört" in status and "Veraltet" in status
    page.screenshot(path=shots / "b10-02-mappings.png")
    page.locator("#b-op-history").scroll_into_view_if_needed()
    assert "Version 1 veröffentlicht" in page.inner_text("#b-op-history")
    page.screenshot(path=shots / "b10-03-history.png")
    page.locator("#b-op-lint").scroll_into_view_if_needed()
    page.screenshot(path=shots / "b10-04-tidiness.png")
    page.locator("#b-op-coverage").scroll_into_view_if_needed()
    assert page.locator("[data-op-coverage='SPINE-product'] td.is-filled").count() >= 1
    assert page.locator("[data-op-coverage='SPINE-product'] td.is-missing").count() >= 1
    page.screenshot(path=shots / "b10-05-coverage.png")

    # A health review moves the next one.
    page.locator("[data-op-review='FeaturePlan']").click()
    page.wait_for_selector("[data-op-type='FeaturePlan'] >> text=zuletzt Alex Beispiel")

    # An item links to the editor where it is fixed: the undescribed type opens in the ontology.
    page.locator("[data-op-lint='type_undescribed'] a", has_text="Beheben").first.click()
    page.wait_for_function("() => window.__builderOntology && document.querySelector('[data-panel]:not([hidden])')")
    assert "Workaround" in page.inner_text("#b-onto-panel-title, [data-panel]")
    page.screenshot(path=shots / "b10-06-fix-in-ontology.png")
    page.context.close()


# ---------------------------------------------------------------------------
# Step B11: the builder as a team tool. An outside consultant, invited to one
# workspace, edits a draft and cannot publish; a reviewer comments and
# approves; a publisher applies; the consultant's access ends on its day.
# ---------------------------------------------------------------------------


@pytest.fixture
def team_server():
    from builder_harness import FakeEngine, MemoryDirectory, MemoryWorkspaceStore

    admin = StandInUser({"admin"}, "Ada Admin", "ada@knovas.example")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    directory = MemoryDirectory(store, [admin])
    reviewer = directory.add(StandInUser({"builder_reviewer"}, "Rolf Prüfer", "rolf@knovas.example"))
    publisher = directory.add(StandInUser({"builder_publisher"}, "Paula Publiziert", "paula@knovas.example"))
    app = make_app(admin, store, tenant="test-tenant-0001", engine=FakeEngine(), directory=directory)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"base": f"http://127.0.0.1:{server.server_port}", "store": store, "directory": directory,
           "gate": app.extensions["builder_gate"], "admin": admin, "reviewer": reviewer, "publisher": publisher}
    server.shutdown()


def _api(page, url, body=None):
    """A request from the page itself: the signed-in person's session and the CSRF header, as the scripts send it."""
    return page.evaluate("""async ([url, body]) => {
        const r = await fetch(url, {method: body === null ? 'GET' : 'POST', credentials: 'same-origin',
            headers: {'Content-Type': 'application/json', 'X-CSRF-Token': 'harness'},
            body: body === null ? undefined : JSON.stringify(body)});
        return {status: r.status, body: await r.json()};
    }""", [url, body])


def test_a_team_with_an_outside_consultant(browser, team_server, shots, import_zip):
    import datetime as _dt

    s = team_server
    base, store, gate = s["base"], s["store"], s["gate"]
    page = _page(browser, "1440", "light")
    page.set_viewport_size({"width": 1440, "height": 960})
    _import_through_the_dialog(page, base, import_zip)
    [workspace] = store.list()
    wid = workspace.id
    store.link_tenant(wid, "test-tenant-0001")

    # The administrator invites a consultant to this workspace only: author, for two weeks.
    page.goto(f"{base}/builder/w/{wid}/team")
    page.fill("#b-tm-email", "bea.berater@extern.example")
    page.fill("#b-tm-name", "Bea Berater")
    ends = (_dt.date.today() + _dt.timedelta(days=14)).isoformat()
    page.fill("#b-tm-end", ends)
    page.get_by_role("button", name="Einladen").click()
    page.wait_for_selector("[data-team-made]:not([hidden])")
    assert page.inner_text("[data-team-password-value]")
    page.screenshot(path=shots / "b11-01-invite.png")
    consultant = s["directory"].get_by_email("bea.berater@extern.example")

    # The consultant: this one workspace, an author's tools, and an edit of the draft.
    gate.user = consultant
    page.goto(f"{base}/builder/w/{wid}")
    assert "Autor · bis " + ends in page.inner_text("header")
    made = _api(page, f"/builder/api/w/{wid}/ontology",
                {"op": "type.create", "key": "Workaround", "parent": "euo:Document", "label": {"de": "Umgehung", "en": "Workaround"}})
    assert made["status"] == 200
    _api(page, f"/builder/api/w/{wid}/ontology",
         {"op": "type.update", "key": "Workaround", "set": {"description": "A temporary way around a known defect."}})
    page.goto(f"{base}/builder/pruefen")
    page.get_by_role("button", name="Probelauf starten").click()
    page.wait_for_selector(".b-rv-counts")
    review = page.inner_text("main")
    assert "Veröffentlichen darf, wer die Rolle Veröffentlicher hat." in review
    assert "Freigeben darf, wer die Rolle Prüfer hat." in review
    assert _api(page, f"/builder/api/w/{wid}/publish", {"content_hash": store.draft(wid)["content_hash"]})["status"] == 403
    page.screenshot(path=shots / "b11-02-consultant-cannot-publish.png", full_page=True)

    # The reviewer comments on the change; the comment holds the approval back until it is resolved.
    gate.user = s["reviewer"]
    page.goto(f"{base}/builder/w/{wid}")
    page.goto(f"{base}/builder/pruefen")
    details = page.locator("details[data-comment-target='ontology.types/Workaround']")
    details.locator("summary").click()
    details.get_by_placeholder("Kommentar schreiben …").fill("Bitte ein Beispiel ergänzen.")
    details.get_by_role("button", name="Kommentieren").click()
    page.wait_for_selector("details[data-comment-target='ontology.types/Workaround'] .b-cm")
    page.reload()
    assert "1 offener Kommentar" in page.inner_text("main")
    assert page.locator("[data-publish-approve]").count() == 0
    page.locator("details[data-comment-target='ontology.types/Workaround']").scroll_into_view_if_needed()
    page.screenshot(path=shots / "b11-03-review-comment.png")

    # The consultant addresses it on the canvas: the example, and the comment resolved there.
    gate.user = consultant
    _api(page, f"/builder/api/w/{wid}/ontology", {"op": "type.update", "key": "Workaround",
                                                  "set": {"example": "WA-17, export over the old share until the API is fixed."}})
    page.goto(f"{base}/builder/ontologie#type:Workaround")
    page.wait_for_selector("#b-onto-panel [data-comment-thread='ontology.types/Workaround'] .b-cm")
    thread = page.locator("#b-onto-panel [data-comment-thread='ontology.types/Workaround']")
    assert "seither geändert" in thread.inner_text()
    thread.scroll_into_view_if_needed()
    page.screenshot(path=shots / "b11-04-comment-on-the-canvas.png")
    thread.get_by_role("button", name="Erledigt").click()
    page.wait_for_selector("#b-onto-panel .b-cm.is-resolved")

    # A dry run of the state as it is now, then the reviewer approves and the publisher applies.
    page.goto(f"{base}/builder/pruefen")
    page.get_by_role("button", name="Probelauf starten").click()
    page.wait_for_selector(".b-rv-counts")
    gate.user = s["reviewer"]
    page.reload()
    page.get_by_role("button", name="Diesen Stand freigeben").click()
    page.wait_for_selector(".b-rv-approvals")
    assert "Freigegeben von Rolf Prüfer" in page.inner_text("main")
    assert page.locator("[data-publish-apply]").count() == 0
    page.screenshot(path=shots / "b11-05-reviewer-approves.png", full_page=True)
    gate.user = s["publisher"]
    page.reload()
    page.get_by_role("button", name="Veröffentlichen").click()
    page.wait_for_selector("text=Nichts zu veröffentlichen")
    assert store.published_bundle(wid)["number"] == 1
    page.screenshot(path=shots / "b11-06-publisher-applies.png")

    # The activity feed: who did what, when.
    page.goto(f"{base}/builder/w/{wid}/aktivitaet")
    feed = page.inner_text("[data-activity]")
    assert "Bea Berater" in feed and "änderte Typ Workaround" in feed and "Rolf Prüfer" in feed
    assert "gab den Entwurf frei" in feed and "Paula Publiziert" in feed and "veröffentlichte Version 1" in feed
    page.screenshot(path=shots / "b11-07-activity.png", full_page=True)

    # The access ends by itself on its day (set to yesterday here).
    store.grant_access(wid, user_id=consultant.id, email=consultant.email, roles=["builder_author"],
                       ends_on=(_dt.date.today() - _dt.timedelta(days=1)).isoformat(), actor_id=s["admin"].id)
    gate.user = consultant
    page.goto(f"{base}/builder/w/{wid}")
    assert "abgelaufen" in page.inner_text("[data-refused]")
    page.screenshot(path=shots / "b11-08-access-expired.png")

    # The team page: the grant now expired, and the access log of every request the consultant made.
    gate.user = s["admin"]
    page.goto(f"{base}/builder/w/{wid}/team")
    assert page.locator("[data-grant-state='expired']").count() == 1
    log = page.inner_text("[data-access-log]")
    assert "POST publish" in log and "403" in log and "POST edit_ontology" in log
    page.screenshot(path=shots / "b11-09-team-and-log.png", full_page=True)
    page.context.close()


# ---------------------------------------------------------------------------
# Step B13: enrolling and monitoring sync components. A file-share connector
# and the GitHub runner enrol through the wizard and speak protocol v1
# (played by builder_harness.ProtocolClient); a dry run, a held plan and its
# approval, an apply, pause and resume; one goes offline and the alert shows.
# ---------------------------------------------------------------------------


@pytest.fixture
def components_server():
    from builder_harness import FakeEngine, MemorySecrets, MemoryWorkspaceStore

    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    app = make_app(admin, store, tenant="test-tenant-0001", engine=FakeEngine(), component_secrets=MemorySecrets())
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"base": f"http://127.0.0.1:{server.server_port}", "store": store, "app": app, "admin": admin}
    server.shutdown()


def _enrol_through_the_wizard(page, kind_label, name, site, owner, shots=None, prefix=""):
    page.get_by_role("button", name="Komponente anmelden").click()
    wizard = page.locator("#b-enrol")
    wizard.get_by_text(kind_label, exact=True).click()
    if shots:
        page.screenshot(path=shots / f"{prefix}-1-kind.png")
    wizard.get_by_role("button", name="Weiter").click()
    wizard.get_by_label("Name").fill(name)
    wizard.get_by_label("Standort").fill(site)
    wizard.get_by_label("Verantwortlich").fill(owner)
    wizard.get_by_role("button", name="Token erzeugen").click()
    token = wizard.locator("[data-enrol-token]")
    token.wait_for(state="visible")
    if shots:
        page.screenshot(path=shots / f"{prefix}-2-token.png")
    value = token.input_value()
    wizard.get_by_role("button", name="Abbrechen").click()
    page.wait_for_load_state("load")
    return value


def test_components_enrol_run_and_alert(browser, components_server, shots):
    import datetime as _dt

    from builder_harness import ProtocolClient

    s = components_server
    base, store = s["base"], s["store"]
    http = s["app"].test_client()
    page = _page(browser, "1440", "light")
    page.set_viewport_size({"width": 1440, "height": 960})

    # The wizard: a file-share connector, its one-time token shown once; it enrols and appears online.
    page.goto(f"{base}/builder/verbinden")
    token = _enrol_through_the_wizard(page, "Dateiablage / DMS (KnovasConnector)", "Dateiablage Zürich", "Büro Zürich",
                                      "IT Kanzlei", shots, "b13-01-wizard")
    share = ProtocolClient(http)
    assert share.enrol(token, version="2.4.0").status_code == 201
    assert share.heartbeat(lag_seconds=420).status_code == 200
    token = _enrol_through_the_wizard(page, "GitHub-Runner", "GitHub-Runner", "Plattform", "Engineering")
    runner = ProtocolClient(http)
    assert runner.enrol(token, version="brain-runner 0.1.1").status_code == 201
    runner.heartbeat(lag_seconds=3600, version="brain-runner 0.1.1")
    page.goto(f"{base}/builder/verbinden")
    assert page.locator("[data-component-state='online']").count() == 2

    # A dry run from the detail page: the connector takes it with its next heartbeat and reports a held plan.
    page.locator("a", has_text="Dateiablage Zürich").click()
    page.get_by_role("button", name="Probelauf jetzt").click()
    page.wait_for_selector("[data-cp-result]:not([hidden])")
    [command] = share.heartbeat().get_json()["commands"]
    t0 = _dt.datetime(2026, 11, 1, 6, tzinfo=_dt.timezone.utc)
    for n, (kind, status, added, changed, retired, errors, seconds) in enumerate((
            ("apply", "ok", 214, 0, 0, 0, 95), ("dry_run", "ok", 3, 11, 0, 0, 12), ("apply", "ok", 3, 11, 0, 0, 20),
            ("apply", "failed", 0, 0, 0, 2, 4), ("dry_run", "ok", 1, 4, 0, 0, 10))):
        start = t0 - _dt.timedelta(days=5 - n)
        share.report_run(kind=kind, status=status, started_at=start.isoformat(),
                         finished_at=(start + _dt.timedelta(seconds=seconds)).isoformat(), duration_ms=seconds * 1000,
                         added=added, changed=changed, retired=retired, errors=errors)
    share.report_run(kind="dry_run", status="held", started_at=t0.isoformat(),
                     finished_at=(t0 + _dt.timedelta(seconds=14)).isoformat(), duration_ms=14000, added=2, changed=6,
                     retired=58, errors=0, held=[{"mapping": "ablage-akten", "retire": {"Akte": 58}, "share": 0.27}],
                     command=command["id"])
    share.events({"code": "scan.finished", "counts": {"files": 1873, "folders": 212}})
    share.heartbeat()

    # The held plan, approved with a reason; the decision goes to the connector.
    page.reload()
    plan = page.locator("[data-cp-plan='ablage-akten']")
    plan.get_by_placeholder("Grund der Freigabe").fill("Alte Akten 2019 sind ins Archiv gewandert.")
    plan.get_by_role("button", name="Freigeben").click()
    page.wait_for_selector("[data-cp-plan='ablage-akten'].is-approved")
    [decision] = share.heartbeat().get_json()["commands"]
    assert decision["command"] == "approve_plan"
    share.done(decision["id"])

    # An apply after confirming; pause and resume.
    page.get_by_role("button", name="Jetzt anwenden").click()
    page.locator("#b-cp-confirm").get_by_role("button", name="Anwenden").click()
    page.wait_for_selector("[data-cp-result]:not([hidden])")
    [apply_cmd] = share.heartbeat().get_json()["commands"]
    assert apply_cmd["args"] == {"kind": "apply"}
    finish = t0 + _dt.timedelta(minutes=5)
    share.report_run(kind="apply", status="ok", started_at=finish.isoformat(),
                     finished_at=(finish + _dt.timedelta(seconds=41)).isoformat(), duration_ms=41000, added=2, changed=6,
                     retired=58, errors=0, command=apply_cmd["id"])
    page.reload()
    page.get_by_role("button", name="Pausieren").click()
    page.wait_for_selector("[data-cp-state='paused']")
    assert share.heartbeat().get_json()["paused"]
    page.get_by_role("button", name="Fortsetzen").click()
    page.wait_for_selector("[data-cp-state='online']")
    share.heartbeat()
    page.reload()
    assert "ablage-akten" in page.inner_text("main") and "scan.finished" in page.inner_text("[data-cp-events]")
    page.screenshot(path=shots / "b13-03-detail.png", full_page=True)

    # The GitHub runner stops reporting: offline beyond the limit, first in the registry, an alert everywhere.
    store.update_component(runner.component, last_heartbeat_at=(_dt.datetime.now(_dt.timezone.utc)
                                                                - _dt.timedelta(hours=2)).isoformat())
    page.goto(f"{base}/builder/verbinden")
    first = page.locator("[data-comp-table] tbody tr").first
    assert first.get_attribute("data-component") == "GitHub-Runner" and first.get_attribute("data-component-state") == "offline"
    assert "GitHub-Runner ist offline seit 2 Stunden." in page.inner_text("[data-component-alerts]")
    page.screenshot(path=shots / "b13-04-registry.png", full_page=True)
    page.goto(f"{base}/builder/arbeitsbereich")
    assert page.locator("[data-alert='offline']").count() == 1
    page.screenshot(path=shots / "b13-05-alert-start-page.png")
    page.context.close()
