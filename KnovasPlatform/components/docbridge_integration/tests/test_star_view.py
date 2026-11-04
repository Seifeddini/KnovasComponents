"""The star view over a tenant's own types: it shows them, and a node can be added and seen.

A real browser over the real page, with the ontology API answered here, so what is tested is what the page does with
each shape of tenant: one whose types have other names than the demo's, and one whose types have no nodes yet.
"""
from __future__ import annotations

import json
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
pytest.importorskip("flask")


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
def base(tmp_path_factory):
    import os

    from conftest import DummyFileHandler, DummyKnovasClient
    from web_interface import app as web_app
    from werkzeug.serving import make_server

    saved = {k: os.environ.get(k) for k in ("WEB_SECRET_KEY", "CORTEX_ENABLED")}
    os.environ.update(WEB_SECRET_KEY="test-secret-key-for-the-star-view", CORTEX_ENABLED="true")
    config = tmp_path_factory.mktemp("star") / "config.yaml"
    config.write_text('web:\n  secret_key: "${WEB_SECRET_KEY}"\n  cortex_enabled: "true"\n  login:\n    enabled: false\n'
                      'identity:\n  enabled: false\napi:\n  base_url: "http://example.test"\n', encoding="utf-8")
    client, handler = web_app.KnovasAPIClient, web_app.AutoDocFileHandler
    web_app.KnovasAPIClient, web_app.AutoDocFileHandler = DummyKnovasClient, DummyFileHandler
    app = web_app.create_app(str(config))
    web_app.KnovasAPIClient, web_app.AutoDocFileHandler = client, handler
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class Tenant:
    """The ontology API of one tenant, answered through the browser's routing."""

    def __init__(self, counts: dict[str, int], *, refuse: bool = False):
        self.nodes = {label.lower(): [{"id": f"{label.lower()}-{n}", "label": f"{label} {n}"} for n in range(count)]
                      for label, count in counts.items()}
        self.labels = {label.lower(): label for label in counts}
        self.refuse = refuse
        self.created: list[dict] = []

    def answer(self, route):
        request = route.request
        if request.method == "POST":
            body = json.loads(request.post_data)
            if self.refuse:
                route.fulfill(status=502, json={"success": False, "error": "Die Knovas-API hat den Eintrag nicht angelegt."})
                return
            self.created.append(body)
            node = {"id": f"new-{len(self.created)}", "label": body["label"]}
            self.nodes[body["type"]].insert(0, node)
            route.fulfill(status=201, json={"success": True, "entity": {**node, "type": body["type"], "doc_count": 0}})
            return
        wanted = request.url.split("type=")[-1]
        route.fulfill(json={"entities": self.nodes.get(wanted, [])})

    def summary(self, route):
        route.fulfill(json={"relations": [], "types": [{"id": key, "label": self.labels[key], "count": len(nodes)}
                                                       for key, nodes in self.nodes.items()]})


def _open(browser, base, tenant, saved=None):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    if saved is not None:
        context.add_init_script(f"localStorage.setItem('cortex-galaxy-top', {json.dumps(json.dumps(saved))})")
    page = context.new_page()
    page.route("**/api/ontology/summary", tenant.summary)
    page.route("**/api/ontology/entities**", tenant.answer)
    page.goto(f"{base}/ontology")
    page.wait_for_function("window.cortexGalaxy && document.body.classList.contains('galaxy-live')")
    return page


def _on_screen(page):
    return page.evaluate("window.cortexGalaxy.systems.map((s) => s.label)")


def _add(page, type_label, name):
    bar = page.locator("#galaxyLayers")
    if not bar.locator("form").count():
        bar.get_by_role("button", name="Knoten anlegen", exact=True).click()
    form = bar.locator("form")
    form.get_by_label("Typ des neuen Knotens").select_option(label=type_label)
    form.get_by_label("Name des Knotens").fill(name)
    form.get_by_role("button", name="Knoten anlegen").click()


def _nodes_of(page, label):
    return page.evaluate("(label) => (window.cortexGalaxy.systems.find((s) => s.label === label) || {entityNodes: []})"
                         ".entityNodes.map((n) => n.label)", label)


class TestWhatIsShown:
    def test_a_tenant_without_a_client_type_shows_its_largest_type(self, browser, base):
        page = _open(browser, base, Tenant({"Service": 2, "Decision": 3}))
        assert _on_screen(page) == ["Decision"]

    def test_the_client_type_stays_in_front(self, browser, base):
        page = _open(browser, base, Tenant({"Decision": 3, "Client": 1}))
        assert _on_screen(page) == ["Client"]

    def test_a_saved_choice_stays(self, browser, base):
        page = _open(browser, base, Tenant({"Service": 2, "Decision": 3}), saved=["service"])
        assert _on_screen(page) == ["Service"]

    def test_a_saved_choice_of_types_this_tenant_does_not_have_is_not_an_empty_sky(self, browser, base):
        page = _open(browser, base, Tenant({"Service": 2, "Decision": 3}), saved=["von-einem-anderen-mandanten"])
        assert _on_screen(page) == ["Decision"]

    def test_types_without_nodes_are_not_replaced_by_the_demo(self, browser, base):
        page = _open(browser, base, Tenant({"Decision": 0, "Service": 0}))
        assert _on_screen(page) == []
        assert page.locator("#galaxyLayers form").is_visible(), "an empty sky offers the way to fill it"

    def test_an_empty_type_can_be_chosen_for_the_front(self, browser, base):
        page = _open(browser, base, Tenant({"Decision": 3, "Service": 0}))
        page.locator("#galaxyLayers").get_by_role("button", name="Vordergrund").click()
        page.locator("#galaxyLayers").get_by_label("Service").check()
        assert "Service" in _on_screen(page)


class TestAddingANode:
    def test_the_first_node_of_a_tenant_appears_and_stays_after_a_reload(self, browser, base):
        tenant = Tenant({"Decision": 0, "Service": 0})
        page = _open(browser, base, tenant)
        _add(page, "Service", "Erster Dienst")
        page.wait_for_function("window.cortexGalaxy.systems.length === 1")
        assert tenant.created == [{"type": "service", "label": "Erster Dienst"}]
        assert _on_screen(page) == ["Service"] and _nodes_of(page, "Service") == ["Erster Dienst"]
        assert page.locator("#galaxyEntityTitle").inner_text() == "Service"
        assert not page.locator("#galaxyLayers form").count(), "the form has done its work"

        page.reload()
        page.wait_for_function("window.cortexGalaxy && window.cortexGalaxy.systems.length === 1")
        assert _nodes_of(page, "Service") == ["Erster Dienst"]

    def test_a_node_of_a_type_that_is_not_on_screen_brings_it_there(self, browser, base):
        tenant = Tenant({"Decision": 3, "Service": 0})
        page = _open(browser, base, tenant)
        assert _on_screen(page) == ["Decision"]
        _add(page, "Service", "Neuer Dienst")
        page.wait_for_function("window.cortexGalaxy.systems.some((s) => s.label === 'Service')")
        assert set(_on_screen(page)) == {"Decision", "Service"}
        assert _nodes_of(page, "Service") == ["Neuer Dienst"]

    def test_a_node_of_a_type_on_screen_joins_it(self, browser, base):
        page = _open(browser, base, Tenant({"Decision": 2}))
        _add(page, "Decision", "Neuer Entscheid")
        page.wait_for_function("window.cortexGalaxy.systems[0].entityNodes.some((n) => n.label === 'Neuer Entscheid')")
        assert page.evaluate("window.cortexGalaxy.systems[0].count") == 3
        assert page.locator("#galaxyEntityTitle").inner_text() == "Decision"

    def test_a_refusal_says_so_and_adds_nothing(self, browser, base):
        page = _open(browser, base, Tenant({"Decision": 2}, refuse=True))
        _add(page, "Decision", "Abgelehnt")
        page.wait_for_selector("#galaxyLayers form >> text=nicht angelegt")
        assert "Abgelehnt" not in _nodes_of(page, "Decision")
