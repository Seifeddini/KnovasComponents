"""Experimente frontend: templates, sidebar item, page scripts, search-hit cards.

No Flask app and no database for most of this: the templates render in a bare
Jinja environment under StrictUndefined (a missing context key fails loudly
instead of rendering empty), and the JavaScript runs in Node's vm module with
just enough of a DOM stub for the functions under test. The Node tests skip
when node is not installed; everything else always runs.

The number formatting in experiments_common.js must match kinds.format_* to
the character -- the same estimate is shown on the page, in API answers and in
the Markdown Knovas indexes -- so those tests compare the two implementations
over the same inputs instead of pinning a few strings.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
from collections import Counter

import pytest

jinja2 = pytest.importorskip("jinja2")

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "web_interface"
TEMPLATES = WEB / "templates"
JS = WEB / "static" / "js"
CSS = WEB / "static" / "css"

PAGE_TEMPLATES = ("experiments_list.html", "experiments_detail.html", "experiments_manage.html")
PAGE_SCRIPTS = {
    "experiments_list.html": "experiments_list.js",
    "experiments_detail.html": "experiments_detail.js",
    "experiments_manage.html": "experiments_manage.js",
}
EXPERIMENT_JS = ("experiments_common.js", "experiments_list.js",
                 "experiments_detail.js", "experiments_manage.js")
FLASK_PATH = "M9 3h6M10 3v6L4.5 18.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3M7 15h10"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _env():
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        autoescape=True,
        undefined=jinja2.StrictUndefined,
    )
    endpoints = {
        "index": "/", "ontology_page": "/ontology", "settings_page": "/settings",
        "experiments.list_page": "/experiments",
        "experiments.manage_page": "/experiments/verwaltung",
    }

    def url_for(endpoint, **kw):
        if endpoint == "static":
            return "/static/" + kw["filename"]
        return endpoints.get(endpoint, "/" + endpoint.replace(".", "/"))

    env.globals["url_for"] = url_for
    return env


def _context(**extra):
    """The admin-test base context plus the keys the experiments pages get
    (plan section 10: active_nav, page_context(), app_title, brand,
    csrf_token, asset_version, experiments_can_manage, pointer_prefix)."""
    base = {
        "app_title": "Knovas", "company_name": "Kanzlei",
        "feedback_url": None, "console_url": "/admin/people",
        "active_nav": "experiments", "csrf_token": "t0k-\u00e4<&>", "error": None,
        "notice": None, "me": None, "asset_version": "a1b2",
        "brand": "Knovas", "cortex_enabled": True, "experiments_nav": True,
        "experiments_can_manage": False, "pointer_prefix": "experiments",
    }
    base.update(extra)
    return base


def _render(name, **extra):
    ctx = _context(**extra)
    if name == "experiments_detail.html":
        ctx.setdefault("experiment_key", "MKT-1")
    return _env().get_template(name).render(ctx)


def _page_data(page_html):
    match = re.search(r'<script type="application/json" id="kxPageData">(.*?)</script>',
                      page_html, re.S)
    assert match, "the page data island is missing"
    return json.loads(match.group(1))


def _nav_item(page_html):
    """The Experimente <a> in the sidebar, or None."""
    match = re.search(r'<a href="[^"]*"[^>]*class="app-nav-item[^"]*"[^>]*>(?:(?!</a>).)*?'
                      r'Experimente\s*</a>', page_html, re.S)
    return match.group(0) if match else None


# -- templates ------------------------------------------------------------------


class TestTemplatesRender:
    @pytest.mark.parametrize("name", PAGE_TEMPLATES)
    @pytest.mark.parametrize("can_manage", [True, False])
    def test_every_page_renders_under_strict_undefined(self, name, can_manage):
        page = _render(name, experiments_can_manage=can_manage)
        assert page.lstrip().startswith("<!DOCTYPE html>")
        assert 'lang="de"' in page

    @pytest.mark.parametrize("name", PAGE_TEMPLATES)
    def test_every_page_carries_the_csrf_meta_escaped(self, name):
        page = _render(name)
        assert '<meta name="csrf-token" content="t0k-\u00e4&lt;&amp;&gt;">' in page

    @pytest.mark.parametrize("name", PAGE_TEMPLATES)
    def test_scripts_load_in_order_with_the_asset_version(self, name):
        page = _render(name)
        wanted = ["js/markdown.js", "js/experiments_common.js", "js/" + PAGE_SCRIPTS[name]]
        positions = []
        for script in wanted:
            tag = f'<script src="/static/{script}?v=a1b2"></script>'
            assert tag in page, f"{name} must load {script} with ?v=asset_version"
            positions.append(page.index(tag))
        assert positions == sorted(positions), "markdown.js, then experiments_common.js, then the page script"

    @pytest.mark.parametrize("name", PAGE_TEMPLATES)
    def test_styles_are_the_product_stylesheet_plus_the_module_one(self, name):
        page = _render(name)
        assert '/static/css/style.css?v=a1b2' in page
        assert '/static/css/experiments.css?v=a1b2' in page
        # The console stylesheet would shrink the sidebar; this is a main module.
        assert "admin.css" not in page

    @pytest.mark.parametrize("name", PAGE_TEMPLATES)
    def test_pages_include_the_sidebar_with_the_item_active(self, name):
        page = _render(name)
        item = _nav_item(page)
        assert item is not None
        assert "active" in item and 'aria-current="page"' in item

    @pytest.mark.parametrize("name", PAGE_TEMPLATES)
    def test_title_names_the_brand_and_module(self, name):
        page = _render(name, brand="Kanzlei&Co")
        title = re.search(r"<title>(.*?)</title>", page, re.S).group(1)
        assert "Kanzlei&amp;Co" in title and "Experimente" in title

    @pytest.mark.parametrize("name", PAGE_TEMPLATES)
    def test_page_data_island_is_valid_json_with_booleans(self, name):
        data = _page_data(_render(name, experiments_can_manage=True))
        assert data["canManage"] is True
        assert data["pointerPrefix"] == "experiments"
        assert _page_data(_render(name, experiments_can_manage=False))["canManage"] is False

    def test_optional_keys_may_be_missing_without_breaking_the_page(self):
        """experiments_can_manage and pointer_prefix default safely: a route
        that forgets them must not 500 (and must not grant anything)."""
        ctx = _context()
        del ctx["experiments_can_manage"]
        del ctx["pointer_prefix"]
        for name in PAGE_TEMPLATES:
            if name == "experiments_detail.html":
                ctx["experiment_key"] = "MKT-1"
            data = _page_data(_env().get_template(name).render(ctx))
            assert data["canManage"] is False
            assert data["pointerPrefix"] == "experiments"

    def test_detail_emits_the_key_with_tojson(self):
        data = _page_data(_render("experiments_detail.html", experiment_key="ENG-42"))
        assert data["experimentKey"] == "ENG-42"

    def test_detail_key_cannot_break_out_of_the_script_island(self):
        """The route only renders valid keys, but the template must be safe on
        its own: tojson escapes <, >, & and ' inside the data island."""
        hostile = "</script><script>alert(1)</script>'\"&"
        page = _render("experiments_detail.html", experiment_key=hostile)
        island = re.search(r'id="kxPageData">(.*?)</script>', page, re.S).group(1)
        assert "<" not in island and ">" not in island
        assert json.loads(island)["experimentKey"] == hostile
        assert page.count("<script>alert(1)") == 0
        # Everywhere else (breadcrumb, title) it is plain autoescaped text.
        assert "&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in page

    def test_list_page_has_the_contract_controls(self):
        page = _render("experiments_list.html")
        assert "Hypothesen, Messwerte und Entscheidungen \u2013 \u00fcber alle Bereiche." in page
        assert 'id="kxNewExperiment"' in page and "Neues Experiment" in page
        assert 'href="/experiments/verwaltung"' in page
        assert 'placeholder="In Knovas suchen \u2026"' in page
        assert "Archivierte zeigen" in page
        assert 'id="kxLoadMore"' in page and "Mehr laden" in page
        # Every form control has a label.
        for control in ("kxSearchInput", "kxFilterText", "kxFilterStatus", "kxFilterTag"):
            assert f'for="{control}"' in page

    def test_detail_page_has_every_section(self):
        page = _render("experiments_detail.html")
        for anchor in ("kx-ueberblick", "kx-varianten", "kx-messwerte", "kx-laeufe",
                       "kx-auswertungen", "kx-notizen", "kx-entscheidung", "kx-aktivitaet"):
            assert f'id="{anchor}"' in page
            assert f'href="#{anchor}"' in page
        assert 'href="/experiments"' in page  # breadcrumb back to the list

    def test_manage_tabs_for_managers(self):
        page = _render("experiments_manage.html", experiments_can_manage=True)
        tabs = re.findall(r'role="tab" id="kxTab-([a-z]+)"', page)
        assert tabs == ["bereiche", "typen", "metriken", "auswerter", "zugangsschluessel", "index"]
        for tab in tabs:
            assert f'id="kxPanel-{tab}"' in page
            assert f'aria-controls="kxPanel-{tab}"' in page
        labels = re.findall(r'role="tab"[^>]*>([^<]+)</button>', page)
        assert labels == ["Bereiche", "Typen", "Metriken", "Auswerter", "Zugangsschl\u00fcssel", "Index"]

    def test_manage_hides_manager_tabs_from_everyone_else(self):
        page = _render("experiments_manage.html", experiments_can_manage=False)
        assert re.findall(r'role="tab" id="kxTab-([a-z]+)"', page) == ["zugangsschluessel"]
        for tab in ("bereiche", "typen", "metriken", "auswerter", "index"):
            assert f"kxPanel-{tab}" not in page


class TestSidebar:
    def _sidebar(self, **extra):
        ctx = _context(**extra)
        return _env().get_template("_sidebar.html").render(ctx)

    def test_no_item_without_experiments_nav(self):
        ctx = _context(active_nav="suche")
        del ctx["experiments_nav"]
        page = _env().get_template("_sidebar.html").render(ctx)
        assert _nav_item(page) is None
        assert "Experimente" not in page
        assert FLASK_PATH not in page

    @pytest.mark.parametrize("value", [False, None, 0, ""])
    def test_no_item_when_experiments_nav_is_falsy(self, value):
        page = self._sidebar(experiments_nav=value, active_nav="suche")
        assert _nav_item(page) is None

    def test_item_with_experiments_nav(self):
        page = self._sidebar(experiments_nav=True, active_nav="suche")
        item = _nav_item(page)
        assert item is not None
        assert 'href="/experiments"' in item
        assert FLASK_PATH in item
        assert "active" not in item.split(">")[0].replace("app-nav-item", "")
        assert "aria-current" not in item

    def test_item_is_active_on_the_module_pages(self):
        item = _nav_item(self._sidebar(experiments_nav=True, active_nav="experiments"))
        assert 'class="app-nav-item active"' in item
        assert 'aria-current="page"' in item

    def test_item_follows_cortex_and_precedes_settings(self):
        page = self._sidebar(experiments_nav=True, active_nav="suche")
        assert page.index("Cortex") < page.index("Experimente") < page.index("Einstellungen")

    def test_item_stays_when_cortex_is_off(self):
        page = self._sidebar(experiments_nav=True, cortex_enabled=False, active_nav="suche")
        assert "Cortex" not in page
        assert _nav_item(page) is not None

    def test_icon_is_decorative(self):
        item = _nav_item(self._sidebar(experiments_nav=True))
        svg = re.search(r"<svg[^>]*>", item).group(0)
        assert 'aria-hidden="true"' in svg and 'focusable="false"' in svg

    def test_other_pages_still_render_with_and_without_the_item(self):
        """index/ontology/settings include the same partial; the new block
        must not require experiments_nav from them."""
        env = _env()
        common = _context(active_nav="suche")
        del common["experiments_nav"]
        index_ctx = dict(common, browser_client_open_enabled=False, companion_enabled=False,
                         allow_degraded_download_open=False, open_mapping_configured=False,
                         pdf_inline_in_browser=False, onedrive_enrichment_loaded=False,
                         results_per_page=20, build_id="b")
        assert _nav_item(env.get_template("index.html").render(index_ctx)) is None
        assert _nav_item(env.get_template("ontology.html").render(common)) is None
        settings_ctx = dict(common, login_name="x", identity_enabled=True, build_id="b")
        assert _nav_item(env.get_template("settings.html").render(settings_ctx)) is None
        assert _nav_item(env.get_template("index.html").render(
            dict(index_ctx, experiments_nav=True))) is not None


# -- static checks on the scripts ------------------------------------------------


def _source(name):
    return (JS / name).read_text(encoding="utf-8")


class TestScriptsStatic:
    @pytest.mark.parametrize("name", EXPERIMENT_JS)
    def test_no_dynamic_code_or_html_sinks(self, name):
        source = _source(name)
        for sink in ("eval(", "new Function", "document.write", "insertAdjacentHTML",
                     "outerHTML =", "setTimeout('", 'setTimeout("'):
            assert sink not in source, f"{name} uses {sink}"

    def test_inner_html_only_for_the_markdown_renderer(self):
        """Server text goes in through textContent; the one innerHTML is the
        output of KnovasMarkdown.render, which escapes before formatting."""
        for name in EXPERIMENT_JS:
            code_lines = [line for line in _source(name).splitlines()
                          if "innerHTML" in line and not line.strip().startswith("//")]
            if name == "experiments_common.js":
                assert code_lines == ["            box.innerHTML = renderer.render(text);"]
            else:
                assert code_lines == [], f"{name} writes innerHTML"

    def test_only_the_common_module_talks_to_the_server(self):
        """Every request goes through KX.api / KX.upload, which attach the
        X-CSRF-Token and handle 401. A stray fetch would do neither."""
        assert "fetch(" in _source("experiments_common.js")
        for name in EXPERIMENT_JS[1:]:
            assert "fetch(" not in _source(name), f"{name} calls fetch directly"
            assert "XMLHttpRequest" not in _source(name)

    def test_request_bodies_never_carry_access_groups(self):
        """Plan section 10: bodies never use the key access_groups (the web
        layer refuses them). The index tab may only read it."""
        for name in EXPERIMENT_JS:
            # An object key, not the ternary in `index.access_groups ? ... : []`.
            assert not re.search(r"[{,]\s*['\"]?access_groups['\"]?\s*:", _source(name)), name

    def test_scripts_are_strict_and_scoped(self):
        for name in EXPERIMENT_JS:
            source = _source(name)
            assert "'use strict';" in source
            assert source.lstrip().startswith("//")
            assert source.rstrip().endswith("})();"), f"{name} must stay inside its IIFE"

    def test_common_module_exposes_the_contract_api(self):
        source = _source("experiments_common.js")
        exported = re.search(r"window\.KX = \{(.*?)\};", source, re.S).group(1)
        names = set(re.findall(r"[A-Za-z_]+", exported))
        for name in ("csrfToken", "api", "upload", "esc", "el", "fmtDate", "fmtDateTime",
                     "fmtNumber", "fmtEstimate", "fmtDiff", "toast", "dialog", "verdictChip",
                     "statusChip", "domainDot", "intervalBar", "lineChart", "renderMarkdown",
                     "renderFieldInput", "readFieldInput", "meta"):
            assert name in names, f"KX.{name} missing"

    def test_charts_are_inline_svg_without_libraries(self):
        source = _source("experiments_common.js")
        assert "createElementNS" in source and "http://www.w3.org/2000/svg" in source
        for name in EXPERIMENT_JS:
            source = _source(name)
            assert not re.search(r"^\s*import\s|\bimport\(|\brequire\(", source, re.M), name
        for name in PAGE_TEMPLATES:
            srcs = re.findall(r'<script[^>]*src="([^"]+)"', (TEMPLATES / name).read_text(encoding="utf-8"))
            assert all("url_for('static'" in s for s in srcs), srcs

    def test_contract_copy_is_present(self):
        """German strings the plan (section 14) prescribes, verbatim."""
        text = "\n".join(_source(n) for n in EXPERIMENT_JS)
        text += "\n".join((TEMPLATES / t).read_text(encoding="utf-8") for t in PAGE_TEMPLATES)
        # Long messages are split over source lines: 'a ' + 'b'.
        text = re.sub(r"'\s*\+\s*'", "", text)
        for phrase in (
            "Gefunden mit Knovas", "Knovas und Datenbank", "Datenbanksuche",
            "Noch keine Bereiche.", "Bitten Sie eine verantwortliche Person, Bereiche einzurichten.",
            "Noch keine Experimente in diesem Bereich.", "Leitplanke verletzt",
            "Noch keine Messwerte. Erfassen Sie Werte von Hand, laden Sie eine CSV-Datei hoch "
            "oder senden Sie sie aus CI (Zugangsschl\u00fcssel unter Verwaltung).",
            "Python- und Julia-Auswerter brauchen die Rechenumgebung (Profil experiments).",
            "Noch keine Auswertung. \u00abAlle Auswertungen des Typs ausf\u00fchren\u00bb startet die vorgesehenen.",
            "Alle Auswertungen des Typs ausf\u00fchren", "Auswertung starten", "Alle Daten",
            "Neuester Lauf je Variante", "Zeitraum", "Protokoll", "Messwert erfassen",
            "CSV importieren", "R\u00fcckg\u00e4ngig", "Lauf erfassen", "Neu indexieren",
            "In Knovas: ", "variant;observed_at;ctr;ctr.count;cost_per_click;cost_per_click.denominator",
            "Neue Bereiche starten mit dem Typ \u00abAllgemeine Hypothese\u00bb.", "Pakete",
            "Pr\u00fcfen", "Als neue Version speichern", "Kopieren nach \u2026", "Testen",
            "Experimente in meiner normalen Suche zeigen", "Alles neu indexieren",
            "Experimente in der normalen Suche zeigen", "Grund", "Begr\u00fcndung", "Erkenntnis",
            "Neu laden",
        ):
            assert phrase in text, phrase

    def test_no_eszett_in_ui_copy(self):
        """Repository rule: write ss for the sharp s. (The transliteration
        table that turns it into ss in suggested keys is the exception.)"""
        for name in EXPERIMENT_JS:
            lines = [l for l in _source(name).splitlines() if "\u00df" in l and "replace(/\u00df/g" not in l]
            assert lines == [], f"{name}: {lines[:2]}"
        for name in PAGE_TEMPLATES + ("_sidebar.html",):
            assert "\u00df" not in (TEMPLATES / name).read_text(encoding="utf-8")

    def test_stylesheet_uses_the_product_tokens(self):
        css = (CSS / "experiments.css").read_text(encoding="utf-8")
        for token in ("var(--primary-color)", "var(--border-color)", "var(--radius-lg)",
                      "var(--font-heading)", "var(--surface-sunken)", "var(--card-bg)"):
            assert token in css
        assert "@import" not in css and "url(" not in css  # self-hosted, no fonts or images pulled in

    def test_hidden_wins_over_display_classes_in_pages_and_dialogs(self):
        """.kx-field / .kx-form-row set display, which beats the browser's
        [hidden]; dialogs live under <body>, outside .kx-page. Without both
        rules the date range of an evaluation and the levels of a metric
        showed even when they did not apply."""
        css = re.sub(r"/\*.*?\*/", "", (CSS / "experiments.css").read_text(encoding="utf-8"), flags=re.S)
        selectors = {sel.strip() for m in re.finditer(r"([^{}]*)\{\s*display:\s*none\s*!important;\s*\}", css)
                     for sel in m.group(1).split(",")}
        assert ".kx-page [hidden]" in selectors and ".kx-dialog [hidden]" in selectors

    def test_app_js_methods_stay_unique_and_the_new_ones_exist(self):
        from test_frontend_static import _method_names

        counts = Counter(_method_names())
        for name in ("_experimentHitUrl", "_openExperimentHit", "_previewNeighbour",
                     "_experimentCardHtml"):
            assert counts[name] == 1, name
        assert not [n for n, c in counts.items() if c > 1]


# -- JavaScript under Node --------------------------------------------------------

_VM_PRELUDE = r"""
const vm = require('vm');
const fs = require('fs');
class FakeClassList {
  constructor(el) { this.el = el; }
  add(...c) { const s = new Set(this.el.className.split(' ').filter(Boolean)); c.forEach((x) => s.add(x)); this.el.className = [...s].join(' '); }
  remove(...c) { this.el.className = this.el.className.split(' ').filter((x) => !c.includes(x)).join(' '); }
  contains(c) { return this.el.className.split(' ').includes(c); }
  toggle(c, on) { if (on === undefined ? !this.contains(c) : on) this.add(c); else this.remove(c); }
}
class FakeEl {
  constructor(tag) { this.tagName = String(tag).toUpperCase(); this.attrs = {}; this.children = []; this._html = null; this._text = '';
    this.className = ''; this.classList = new FakeClassList(this); this.dataset = {}; this.listeners = {};
    this.style = { props: {}, setProperty(k, v) { this.props[k] = v; } }; this.nodeType = 1; }
  setAttribute(k, v) { if (k === 'class') this.className = String(v); else this.attrs[k] = String(v); }
  getAttribute(k) { return k === 'class' ? this.className : (k in this.attrs ? this.attrs[k] : null); }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  appendChild(c) { this.children.push(c); return c; }
  set textContent(t) { this._text = String(t); this.children = []; this._html = null; }
  get textContent() { return this._text + this.children.map((c) => c.textContent || '').join(''); }
  set innerHTML(h) { this._html = String(h); }
  get innerHTML() { return this._html !== null ? this._html
    : this._text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
}
const document = {
  createElement: (t) => new FakeEl(t),
  createElementNS: (ns, t) => new FakeEl(t),
  createTextNode: (t) => ({ nodeType: 3, textContent: String(t) }),
  getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
  addEventListener: () => {}, readyState: 'complete', body: new FakeEl('body'),
};
const assigned = [];
const context = { document, console, JSON, Math, Number, String, Object, Array, Set, Map, Date,
  BigInt, RegExp, Promise, Error, URLSearchParams, setTimeout, clearTimeout,
  location: { assign: (u) => assigned.push(u), pathname: '/', search: '' } };
context.window = context;
context.assigned = assigned;
context.FakeEl = FakeEl;
vm.createContext(context);
const load = (file, suffix) => vm.runInContext(fs.readFileSync(file, 'utf8') + (suffix || ''), context, { filename: file });
"""


def _run_node(body, files, *, suffix=""):
    """Load files into one vm context (see _VM_PRELUDE) and run body there;
    body writes its result with out(value)."""
    script = _VM_PRELUDE
    for i, f in enumerate(files):
        script += f"load({json.dumps(str(f))}, {json.dumps(suffix if i == len(files) - 1 else '')});\n"
    script += "const out = (v) => process.stdout.write(JSON.stringify(v));\n"
    script += "vm.runInContext(" + json.dumps("(function(){" + body + "})()") + ", Object.assign(context, { out }));\n"
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


@needs_node
@pytest.mark.parametrize("name", EXPERIMENT_JS + ("app.js",))
def test_every_script_parses(name):
    proc = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def _kinds():
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from experiments import kinds
    except ImportError:  # pragma: no cover - part A not in this tree
        pytest.skip("experiments.kinds is not available")
    return kinds


_NUMBERS = [0, -0.0, 0.5, 0.125, 1.005, 2.5, -2.5, 12, 999.999, -0.001, 10714.5, -1234.567,
            1234567.891, 0.016334, 0.0042, -0.0042, 1e-9, 123456789012.345, 1e21, -1.5e22,
            7.25e15, 0.1 + 0.2, 1 / 3, "12", True, None, "NaN", "Infinity"]
_DECIMALS = [None, 0, 1, 2, 3, 6, -3, 99, True, 2.7]


def _js_value(x):
    return {"special": x} if x in ("NaN", "Infinity") else x


def _py_value(x):
    return float("nan") if x == "NaN" else float("inf") if x == "Infinity" else x


@needs_node
class TestNumberFormattingParity:
    """KX.fmt* and kinds.format_* over the same grid of inputs."""

    def _js(self, cases):
        body = r"""
        const decode = (v) => (v && typeof v === 'object' && 'special' in v) ? Number(v.special) : v;
        const cases = """ + json.dumps(cases) + r""";
        out(cases.map(([fn, args]) => KX[fn](...args.map(decode))));
        """
        return _run_node(body, [JS / "experiments_common.js"])

    def test_format_number(self):
        kinds = _kinds()
        grid = [(x, d) for x in _NUMBERS for d in _DECIMALS]
        js = self._js([["fmtNumber", [_js_value(x), d]] for x, d in grid])
        py = [kinds.format_number(_py_value(x), d) for x, d in grid]
        mismatches = [(g, a, b) for g, a, b in zip(grid, js, py) if a != b]
        assert not mismatches, mismatches[:10]

    def test_format_value_and_diff(self):
        kinds = _kinds()
        units = ["", "%", "  CHF ", "ms", None]
        grid = [(k, x, u, d) for k in ("proportion", "mean", "ratio", "count")
                for x in _NUMBERS for u in units for d in (None, 0, 1, 3)]
        js_value = self._js([["fmtEstimate", [k, _js_value(x), u, d]] for k, x, u, d in grid])
        js_diff = self._js([["fmtDiff", [k, _js_value(x), u, d]] for k, x, u, d in grid])
        py_value = [kinds.format_value(k, _py_value(x), u or "", d) for k, x, u, d in grid]
        py_diff = [kinds.format_diff(k, _py_value(x), u or "", d) for k, x, u, d in grid]
        bad = [(g, a, b) for g, a, b in zip(grid, js_value, py_value) if a != b]
        assert not bad, bad[:10]
        bad = [(g, a, b) for g, a, b in zip(grid, js_diff, py_diff) if a != b]
        assert not bad, bad[:10]

    def test_format_plain(self):
        kinds = _kinds()
        values = [x for x in _NUMBERS] + [1234.5678901, 0.30000000000000004, -0.5, 1e15, 2e15 + 0.5]
        js = self._js([["fmtPlain", [_js_value(x)]] for x in values])
        py = [kinds.format_plain(_py_value(x)) for x in values]
        assert [(v, a, b) for v, a, b in zip(values, js, py) if a != b] == []

    def test_known_strings(self):
        js = self._js([
            ["fmtNumber", [10714.5, 2]], ["fmtEstimate", ["proportion", 0.016334, "%", None]],
            ["fmtDiff", ["proportion", 0.0042, "%", None]], ["fmtDiff", ["duration", 12.5, "ms", 1]],
            ["fmtNumber", [None, 2]], ["fmtPValue", [0.0004]], ["fmtPValue", [0.0213]],
            ["fmtPercent", [0.996]],
        ])
        assert js == ["10'714,50", "1,63 %", "+0,42 Pp.", "+12,5 ms", "\u2013", "< 0,001", "0,021", "99,6 %"]

    def test_parse_number_reads_what_the_server_reads(self):
        js = self._js([["parseNumber", [s]] for s in
                       ["1'250,5", " 12 ", "0,5", "1.5", "1,234.5", "1,2,3", "abc", "", "1e3", "\u00a01 000"]])
        assert js == [1250.5, 12, 0.5, 1.5, None, None, None, None, 1000, 1000]


@needs_node
class TestCommonHelpers:
    def _run(self, body):
        return _run_node(body, [JS / "markdown.js", JS / "experiments_common.js"])

    def test_keys_and_app_urls(self):
        result = self._run(r"""
        out({
          keys: ['MKT-1', 'ENG-123456789', 'mkt-1', 'M-1', 'MKT-', 'MKT-1 ', 'ABCDEFGHI-1', '<b>-1'].map(KX.isKey),
          urls: ['MKT-1', 'bad', null].map(KX.experimentUrl),
          app: ['/experiments/MKT-1', '/experiments/../admin', '//evil/experiments/MKT-1',
                'https://evil/experiments/MKT-1', '/experiments/MKT-1?x=1', '/experiments/verwaltung',
                'javascript:alert(1)', 42].map(KX.safeAppUrl),
        });
        """)
        assert result["keys"] == [True, True, False, False, False, False, False, False]
        assert result["urls"] == ["/experiments/MKT-1", None, None]
        assert result["app"] == ["/experiments/MKT-1"] + [None] * 7

    def test_el_never_builds_markup_from_strings(self):
        result = self._run(r"""
        const a = KX.el('a', { href: 'javascript:alert(1)', onclick: 'alert(1)', text: '<b>x</b>' });
        const b = KX.el('a', { href: '//evil.example/x' });
        const c = KX.el('a', { href: '/experiments/MKT-1', class: ['x', null, 'y'] }, 'A', ['<i>', null], 7);
        let clicked = 0;
        const d = KX.el('button', { onClick: () => { clicked += 1; } });
        d.listeners.click[0]();
        out({ aHref: a.getAttribute('href'), aOn: a.getAttribute('onclick'), aHtml: a.innerHTML,
              bHref: b.getAttribute('href'), cHref: c.getAttribute('href'), cClass: c.className,
              cText: c.textContent, clicked });
        """)
        assert result["aHref"] is None and result["aOn"] is None
        assert result["aHtml"] == "&lt;b&gt;x&lt;/b&gt;"
        assert result["bHref"] is None
        assert result["cHref"] == "/experiments/MKT-1" and result["cClass"] == "x y"
        assert result["cText"] == "A<i>7"
        assert result["clicked"] == 1

    def test_esc(self):
        assert self._run("out(KX.esc(`<a href=\"x\">'&`));") == "&lt;a href=&quot;x&quot;&gt;&#39;&amp;"

    def test_domain_dot_accepts_only_hex_colours(self):
        result = self._run(r"""
        out(['#eb6834', '#EB6834', 'red', '#12345g', 'url(x)', '#fff', null]
            .map((c) => KX.domainDot(c).style.backgroundColor || null));
        """)
        assert result == ["#eb6834", "#EB6834", None, None, None, None, None]

    def test_markdown_goes_through_the_escaping_renderer(self):
        result = self._run(r"""
        const box = KX.renderMarkdown('**ok** <img src=x onerror=alert(1)> [x](javascript:alert(1))');
        out(box.innerHTML);
        """)
        assert "<strong>ok</strong>" in result
        assert "<img" not in result and "&lt;img" in result
        assert 'href="javascript' not in result

    def test_markdown_without_renderer_falls_back_to_text(self):
        result = _run_node(r"""
        const box = KX.renderMarkdown('<b>x</b>');
        out({ tag: box.children[0].tagName, html: box.innerHTML, text: box.textContent });
        """, [JS / "experiments_common.js"])
        assert result == {"tag": "PRE", "html": "", "text": "<b>x</b>"}

    def test_field_values_round_trip(self):
        result = self._run(r"""
        const row = (type, value) => { const r = new FakeEl('div'); const c = new FakeEl('input'); c.value = value;
          r.querySelector = () => c; r.querySelectorAll = () => []; return KX.readFieldInput({ type }, r); };
        const multi = new FakeEl('div');
        multi.querySelectorAll = () => [{ checked: true, value: 'A' }, { checked: false, value: 'B' }, { checked: true, value: 'C' }];
        const none = new FakeEl('div'); none.querySelectorAll = () => [{ checked: false, value: 'A' }];
        out({
          number: row('number', "1'250,5"), integer: row('integer', '12'), bad: row('number', '12abc'),
          empty: row('text', '   '), text: row('text', '  hallo '), yes: row('boolean', 'true'),
          no: row('boolean', 'false'), unset: row('boolean', ''), date: row('date', '2026-09-28'),
          multi: KX.readFieldInput({ type: 'multi_enum' }, multi), none: KX.readFieldInput({ type: 'multi_enum' }, none),
        });
        """)
        assert result == {"number": 1250.5, "integer": 12, "bad": "12abc", "empty": None,
                          "text": "hallo", "yes": True, "no": False, "unset": None,
                          "date": "2026-09-28", "multi": ["A", "C"], "none": None}

    def test_dates_are_swiss(self):
        result = self._run(r"""
        out([KX.fmtDate('2026-09-28'), KX.fmtDate(null), KX.fmtDate('kein Datum'),
             KX.fmtDateUTC('2026-09-14T00:00:00+00:00'), KX.fmtDateUTC('2026-09-01T00:00:00+00:00', true)]);
        """)
        assert result == ["28.09.2026", "\u2013", "\u2013", "14.09.2026", "09.2026"]


@needs_node
class TestSearchHitCards:
    """app.js: experiment rows in the normal search open the experiment page,
    never the preview, and render from escaped server text."""

    def _run(self, body):
        return _run_node(body, [JS / "app.js"], suffix="\n;globalThis.__App = DocumentSearchApp;")

    def test_hit_url_is_only_ever_an_experiment_page(self):
        result = self._run(r"""
        const url = (doc) => __App.prototype._experimentHitUrl.call({}, doc);
        out([
          url({ result_kind: 'experiment', app_url: '/experiments/MKT-1' }),
          url({ result_kind: 'experiment', app_url: '//evil.example/experiments/MKT-1' }),
          url({ result_kind: 'experiment', app_url: '/experiments/../admin' }),
          url({ result_kind: 'experiment', app_url: 'javascript:alert(1)' }),
          url({ result_kind: 'experiment', app_url: '/experiments/MKT-1?next=//evil' }),
          url({ result_kind: 'experiment' }),
          url({ app_url: '/experiments/MKT-1' }),
          url(null),
        ]);
        """)
        assert result == ["/experiments/MKT-1"] + [None] * 7

    def test_click_navigates_and_never_previews(self):
        result = self._run(r"""
        const previews = [];
        const app = { currentResults: [
            { doc_id: 'a' },
            { result_kind: 'experiment', app_url: '/experiments/MKT-1' },
            { result_kind: 'experiment', app_url: 'https://evil.example/' },
          ],
          openPreview: (i) => previews.push(i) };
        Object.setPrototypeOf(app, __App.prototype);
        const r = [app._openExperimentHit(0), app._openExperimentHit(1), app._openExperimentHit(2), app._openExperimentHit(9)];
        out({ r, assigned, previews });
        """)
        assert result == {"r": [False, True, True, False], "assigned": ["/experiments/MKT-1"],
                          "previews": []}

    def test_preview_stepping_skips_experiment_rows(self):
        result = self._run(r"""
        const app = { currentResults: [{ doc_id: 'a' }, { result_kind: 'experiment' }, { doc_id: 'b' },
                                       { result_kind: 'experiment' }] };
        Object.setPrototypeOf(app, __App.prototype);
        out([app._previewNeighbour(0, 1), app._previewNeighbour(2, -1), app._previewNeighbour(2, 1),
             app._previewNeighbour(0, -1)]);
        """)
        assert result == [2, 0, None, None]

    def test_preview_position_counts_documents_only(self):
        result = self._run(r"""
        const pos = { textContent: '' }, prev = { disabled: null }, next = { disabled: null };
        const app = { currentResults: [{ doc_id: 'a' }, { result_kind: 'experiment' }, { doc_id: 'b' }],
                      previewPosition: pos, previewPrev: prev, previewNext: next };
        Object.setPrototypeOf(app, __App.prototype);
        app._updatePreviewPosition(2);
        out([pos.textContent, prev.disabled, next.disabled]);
        """)
        assert result == ["2 von 2", False, True]

    def test_open_preview_refuses_experiment_rows(self):
        result = self._run(r"""
        let touched = false;
        const app = { currentResults: [{ result_kind: 'experiment', app_url: '/experiments/MKT-1' }],
                      get _previewAbort() { touched = true; return null; }, set _previewAbort(v) { touched = true; } };
        Object.setPrototypeOf(app, __App.prototype);
        app.openPreview(0);
        out(touched);
        """)
        assert result is False

    def test_card_renders_escaped_with_flask_and_without_file_badge(self):
        result = self._run(r"""
        const app = Object.create(__App.prototype);
        const xss = '<img src=x onerror=alert(1)>';
        const card = app.createDocumentCard({
          result_kind: 'experiment', doc_id: 'experiments/marketing/MKT-1', path: '/Experimente/x',
          title: 'MKT-1 \u00b7 Titel ' + xss, app_url: '/experiments/MKT-1', file_exists: false,
          experiment: { key: 'MKT-1', domain_name: 'Marketing ' + xss, status_label: 'L\u00e4uft' },
          context_snippet: 'Treffer ' + xss, snippet: 'Treffer ' + xss }, 3);
        out({ html: card.innerHTML, cls: card.className, role: card.getAttribute('role'),
              index: card.getAttribute('data-index'), tab: card.getAttribute('tabindex') });
        """)
        page = result["html"]
        assert "<img" not in page
        assert page.count("&lt;img src=x onerror=alert(1)&gt;") == 3
        assert "Experiment \u00b7 Marketing &lt;img" in page and "\u00b7 L\u00e4uft" in page
        assert "MKT-1 \u00b7 Titel" in page  # doc.title as is, not displayTitle()
        assert FLASK_PATH in page
        assert "badge" not in page and "Datei nicht verf" not in page
        assert "document-card--experiment" in result["cls"]
        assert result["role"] == "link" and result["index"] == "3" and result["tab"] == "0"

    def test_document_cards_are_unchanged(self):
        result = self._run(r"""
        const app = Object.create(__App.prototype);
        const card = app.createDocumentCard({ doc_id: 'x', path: 'Akten/brief.docx', title: 'Brief',
                                               file_exists: false }, 0);
        out({ html: card.innerHTML, role: card.getAttribute('role') });
        """)
        assert "Datei nicht verf\u00fcgbar" in result["html"]
        assert result["role"] is None


# -- the real app (needs part E's routes and PostgreSQL) ----------------------------


def _routes_available():
    return (WEB / "experiments_routes.py").exists()


try:
    from conftest import platform_db_reachable
except ImportError:  # pragma: no cover
    def platform_db_reachable():
        return False


@pytest.mark.skipif(not _routes_available(), reason="experiments_routes.py is not in this tree yet")
@pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")
class TestInTheApp:
    def test_list_page_renders_for_an_experimenter(self, experimenter_client):
        resp = experimenter_client.get("/experiments")
        assert resp.status_code == 200
        page = resp.get_data(as_text=True)
        assert _nav_item(page) is not None
        assert 'name="csrf-token"' in page
        assert _page_data(page)["canManage"] is False

    def test_manager_sees_manager_tabs(self, exp_manager_client):
        page = exp_manager_client.get("/experiments/verwaltung").get_data(as_text=True)
        assert 'id="kxTab-bereiche"' in page
        assert _page_data(page)["canManage"] is True

    def test_member_gets_no_nav_item(self, exp_member_client):
        page = exp_member_client.get("/").get_data(as_text=True)
        assert _nav_item(page) is None
        assert exp_member_client.get("/experiments").status_code == 404
