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
            "Neue Bereiche starten mit dem Typ \u00abAllgemeine Hypothese\u00bb und den allgemeinen ", "Pakete",
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


# -- page scripts under Node (regressions of the review findings) ---------------

# A DOM just rich enough for the page scripts on top of _VM_PRELUDE: parents
# and removal, ids, values, fake timers, a routed fetch and simple selectors
# (tag, .class, #id, [attr], [attr="v"], :checked, descendant, comma lists).
_PAGE_PRELUDE = r"""
process.on('unhandledRejection', (e) => { process.stderr.write('UNHANDLED ' + (e && e.stack || e)); process.exitCode = 1; });
const P = FakeEl.prototype;
P.appendChild = function (c) {
  if (c && typeof c === 'object') { if (c.parentNode && c.parentNode.removeChild) c.parentNode.removeChild(c); c.parentNode = this; }
  this.children.push(c); return c;
};
P.removeChild = function (c) { const i = this.children.indexOf(c); if (i !== -1) this.children.splice(i, 1); if (c && typeof c === 'object') c.parentNode = null; return c; };
P.remove = function () { if (this.parentNode) this.parentNode.removeChild(this); };
P.replaceChild = function (n, old) {
  if (this.children.indexOf(old) === -1) return old;
  if (n.parentNode) n.parentNode.removeChild(n);
  this.children[this.children.indexOf(old)] = n; n.parentNode = this; old.parentNode = null; return old;
};
P.replaceWith = function (n) { if (this.parentNode) this.parentNode.replaceChild(n, this); };
P.insertBefore = function (n, ref) {
  if (n.parentNode) n.parentNode.removeChild(n);
  const i = ref ? this.children.indexOf(ref) : -1;
  if (i === -1) this.children.push(n); else this.children.splice(i, 0, n);
  n.parentNode = this; return n;
};
Object.defineProperty(P, 'firstChild', { get() { return this.children[0] || null; } });
Object.defineProperty(P, 'isConnected', { get() { let n = this; while (n.parentNode) n = n.parentNode; return n.__root === true; } });
Object.defineProperty(P, 'value', {
  get() { return this._value !== undefined ? this._value : (this.attrs.value !== undefined ? this.attrs.value : ''); },
  set(v) { this._value = String(v); }, configurable: true,
});
P.focus = function () {}; P.scrollIntoView = function () {}; P.select = function () {};
P.showModal = function () { this.open = true; }; P.close = function () { this.open = false; };
P.closest = function () { return null; };
P.getBoundingClientRect = function () { return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; };
P.contains = function (n) { while (n) { if (n === this) return true; n = n.parentNode; } return false; };
P.dispatch = function (type, extra) {
  const ev = Object.assign({ type, target: this, currentTarget: this, preventDefault() {} }, extra || {});
  return Promise.all((this.listeners[type] || []).map((fn) => fn(ev)));
};
function splitTop(sel, ch) {
  const out = []; let depth = 0; let cur = '';
  for (const c of sel) {
    if (c === '[' || c === '(') depth += 1;
    if (c === ']' || c === ')') depth -= 1;
    if (c === ch && depth === 0) { out.push(cur); cur = ''; } else cur += c;
  }
  out.push(cur); return out.map((s) => s.trim()).filter(Boolean);
}
function parseCompound(s) {
  const c = { tag: null, id: null, classes: [], attrs: [], checked: false };
  const re = /^([a-zA-Z][\w-]*|\*)|\.([\w-]+)|#([\w-]+)|\[([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]|:([\w-]+)(\((?:[^()]|\([^)]*\))*\))?/g;
  let m;
  while ((m = re.exec(s)) && m[0]) {
    if (m[1]) c.tag = m[1] === '*' ? null : m[1].toLowerCase();
    else if (m[2]) c.classes.push(m[2]);
    else if (m[3]) c.id = m[3];
    else if (m[4]) c.attrs.push([m[4], m[5] !== undefined ? m[5] : m[6] !== undefined ? m[6] : m[7]]);
    else if (m[8] === 'checked') c.checked = true;
  }
  return c;
}
function attrOf(el, name) {
  if (name.indexOf('data-') === 0) {
    const k = name.slice(5).replace(/-([a-z])/g, (_, x) => x.toUpperCase());
    return el.dataset[k] !== undefined ? String(el.dataset[k]) : null;
  }
  if (name === 'class') return el.className;
  if (name === 'id') return el.id !== undefined ? String(el.id) : (el.attrs.id !== undefined ? el.attrs.id : null);
  if (name in el.attrs) return el.attrs[name];
  if (['open', 'disabled', 'checked', 'hidden', 'required'].indexOf(name) !== -1) return el[name] ? '' : null;
  return null;
}
function matchCompound(el, c) {
  if (!(el instanceof FakeEl)) return false;
  if (c.tag && el.tagName.toLowerCase() !== c.tag) return false;
  if (c.id && attrOf(el, 'id') !== c.id) return false;
  if (c.classes.some((k) => !el.classList.contains(k))) return false;
  for (const [name, value] of c.attrs) {
    const v = attrOf(el, name);
    if (v === null || (value !== undefined && v !== value)) return false;
  }
  if (c.checked && !el.checked) return false;
  return true;
}
function matchComplex(el, parts) {
  if (!matchCompound(el, parts[parts.length - 1])) return false;
  let i = parts.length - 2; let n = el.parentNode;
  while (i >= 0 && n) { if (matchCompound(n, parts[i])) i -= 1; n = n.parentNode; }
  return i < 0;
}
function compile(sel) { return splitTop(sel, ',').map((g) => splitTop(g.replace(/>/g, ' '), ' ').map(parseCompound)); }
function descendants(root, out) {
  for (const c of root.children) if (c instanceof FakeEl) { out.push(c); descendants(c, out); }
  return out;
}
P.querySelectorAll = function (sel) { const groups = compile(sel); return descendants(this, []).filter((e) => groups.some((g) => matchComplex(e, g))); };
P.querySelector = function (sel) { return this.querySelectorAll(sel)[0] || null; };
P.matches = function (sel) { return compile(sel).some((g) => matchComplex(this, g)); };
const roots = [];
document.body.__root = true; roots.push(document.body);
document.getElementById = (id) => {
  if (id === 'kxPageData') return { textContent: JSON.stringify(context.pageData || {}) };
  for (const r of roots) {
    if (attrOf(r, 'id') === id) return r;
    const hit = r.querySelector('#' + id);
    if (hit) return hit;
  }
  const node = new FakeEl('div'); node.id = id; node.__root = true; roots.push(node); return node;
};
document.querySelectorAll = (sel) => roots.reduce((acc, r) => acc.concat(r.matches(sel) ? [r] : [], r.querySelectorAll(sel)), []);
document.querySelector = (sel) => document.querySelectorAll(sel)[0] || null;
document.contains = (n) => Boolean(n && n.isConnected);
document.visibilityState = 'visible';
document.title = '';
context.find = (root, tag, text) => root.querySelectorAll(tag).filter((n) => n.textContent.trim() === text)[0] || null;
context.addEventListener = () => {};
context.history = { replaceState() {} };
context.location.hash = '';
context.navigator = { clipboard: { writeText: async () => {} } };
context.isSecureContext = true;
context.CSS = { escape: (s) => String(s) };
context.AbortController = AbortController;
context.getSelection = () => '';
context.Date = Date;
context.timers = [];
let timerSeq = 0;
context.setTimeout = (fn, ms) => { timerSeq += 1; context.timers.push({ id: timerSeq, fn, ms }); return timerSeq; };
context.clearTimeout = (id) => { const i = context.timers.findIndex((t) => t.id === id); if (i !== -1) context.timers.splice(i, 1); };
context.runTimers = (ms) => {
  const due = context.timers.filter((t) => ms === undefined || t.ms === ms);
  due.forEach((t) => { context.timers.splice(context.timers.indexOf(t), 1); t.fn(); });
  return due.length;
};
context.tick = async (n) => { for (let i = 0; i < (n || 8); i += 1) await new Promise((r) => setImmediate(r)); };
context.requests = [];
context.routes = [];
context.fetch = async (url, init) => {
  const method = (init && init.method) || 'GET';
  const body = init && typeof init.body === 'string' ? JSON.parse(init.body) : null;
  context.requests.push({ method, url, body });
  let res = null;
  for (const route of context.routes) { res = await route(method, url, body); if (res) break; }
  if (!res) res = { status: 404, body: { success: false, error: 'Nicht gefunden.' } };
  const status = res.status || 200;
  const payload = res.body === undefined ? { success: true } : res.body;
  return { status, ok: status >= 200 && status < 300, headers: { get: () => 'application/json' }, json: async () => payload };
};
"""

# After experiments_common.js: record toasts and dialogs instead of drawing them.
_UI_STUBS = r"""
window.toasts = [];
KX.toast = (m, k) => { toasts.push([k || 'info', String(m)]); return new FakeEl('div'); };
window.dialogs = [];
KX.dialog = (opts) => new Promise((resolve) => { dialogs.push({ opts, resolve }); });
KX.confirm = async () => true;
"""

# A detail snapshot like GET /api/experiments/<KEY> (store.load_snapshot_with_definition).
_DETAIL_SETUP = r"""
window.pageData = window.pageData || { experimentKey: 'MKT-1', canManage: false };
window.mkExp = (over) => Object.assign({
  id: 'e1', key: 'MKT-1', title: 'Titel', hypothesis: 'Server-Hypothese', description: '',
  status: 'running', status_label: 'L\u00e4uft', status_phase: 'running', archived: false, row_version: 5,
  started_at: '2026-09-01T00:00:00+00:00', created_at: '2026-09-01T00:00:00+00:00', updated_at: '2026-09-02T00:00:00+00:00',
  domain: { key: 'marketing', name: 'Marketing', color: '#eb6834' }, type: { key: 'ab_test', name: 'A/B-Test', version: 1 },
  fields: [], field_values: {}, tags: [], owner: null,
  variants: [{ key: 'A', name: 'Alt', is_control: true, allocation: 0.5 }, { key: 'B', name: 'Neu', allocation: 0.5 }],
  metrics: [], evaluations: [], decisions: [], notes: [], runs: [], run_count: 0,
  measurement_count: 0, batch_count: 0, index: { state: 'indexed', state_label: 'aktuell' },
  definition: { states: [
      { key: 'draft', label: 'Entwurf' }, { key: 'running', label: 'L\u00e4uft', phase: 'running' },
      { key: 'analysis', label: 'Auswertung' }, { key: 'decided', label: 'Entschieden', phase: 'decided' },
      { key: 'stopped', label: 'Abgebrochen', phase: 'stopped' }],
    fields: [], variants: { min: 2, max: 5 } },
  transitions: [{ to: 'analysis', label: 'Zur Auswertung', allowed: true },
                { to: 'stopped', label: 'Abbrechen', allowed: true, needs_comment: true }],
  evaluators: [],
}, over || {});
window.current = mkExp(window.expOverrides || {});
routes.push((m, u) => (m === 'GET' && u === '/api/experiments/MKT-1'
  ? { body: { success: true, experiment: window.current } } : null));
routes.push((m, u) => (u === '/api/experiments/meta'
  ? { body: { success: true, meta: Object.assign({ kinds: [], index_enabled: true }, window.metaOverrides || {}) } } : null));
routes.push((m, u) => (u === '/api/experiments/MKT-1/activity' ? { body: { success: true, activity: [] } } : null));
routes.push((m, u) => (u.indexOf('/api/experiments/metrics?') === 0 ? { body: { success: true, metrics: window.catalog || [] } } : null));
"""

DETAIL_EXPORTS = (
    "state", "load", "applySnapshot", "renderHeader", "renderEvaluations", "intervalsFor",
    "transitionDialogText", "runTransition", "decisionForm", "describeActivity", "reportError",
    "undoBatch", "reindex", "loadMoreRuns", "renderRuns", "runErrorForForm", "showConflict",
    "openTextEditor", "openMetricsEditor", "renderMeasurements", "scheduleIndexPoll",
    "openVariantsEditor", "openCsvDialog",
)
LIST_EXPORTS = ("state", "mergeByKey", "openNewExperiment", "loadDomains")
MANAGE_EXPORTS = ("countsText", "experimentCount", "definitionPreview", "renderPreferences", "openTypeEditor")


def _run_page(page, body, *, setup="", exports=(), stubs=True):
    """Run ``body`` (async, writes its result with out()) after loading
    experiments_common.js and, if given, a page script whose internals
    ``exports`` are made reachable as ``__t.<name>`` by closing its IIFE with
    an extra assignment (test-side only; the file is not changed)."""
    script = _VM_PRELUDE + _PAGE_PRELUDE
    script += "const out = (v) => process.stdout.write(JSON.stringify(v));\ncontext.out = out;\n"
    script += "vm.runInContext(" + json.dumps(setup) + ", context);\n"
    script += f"load({json.dumps(str(JS / 'experiments_common.js'))});\n"
    if stubs:
        script += "vm.runInContext(" + json.dumps(_UI_STUBS) + ", context);\n"
    if page:
        inject = "window.__t = { " + ", ".join(exports) + " };\n})();\n"
        script += (f"const pageSrc = fs.readFileSync({json.dumps(str(JS / page))}, 'utf8')"
                   f".replace(/\\}}\\)\\(\\);\\s*$/, {json.dumps(inject)});\n")
        script += "if (pageSrc.indexOf('window.__t =') === -1) throw new Error('page IIFE end not found');\n"
        script += f"vm.runInContext(pageSrc, context, {{ filename: {json.dumps(page)} }});\n"
    wrapped = ("(async function(){ try { await tick(); " + body
               + " } catch (e) { out({ __error: String((e && e.stack) || e) }); } })()")
    script += "vm.runInContext(" + json.dumps(wrapped) + ", context);\n"
    proc = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (proc.stderr or proc.stdout)[-3000:]
    assert proc.stdout, proc.stderr[-3000:]
    result = json.loads(proc.stdout)
    if isinstance(result, dict) and "__error" in result:
        pytest.fail(result["__error"])
    return result


def _detail(body, *, setup="", exports=DETAIL_EXPORTS):
    return _run_page("experiments_detail.js", body, setup=setup + _DETAIL_SETUP, exports=exports)


_EVALS = r"""
const ev = (id, key, created, over) => Object.assign({ id, evaluator_key: key, evaluator_name: key,
  metric_key: 'ctr', params: {}, scope: {}, status: 'done', verdict: 'n/a', trigger: 'pipeline',
  created_at: created, output: null, headline: id }, over || {});
window.evs = [
  ev('e4', 'builtin.two_proportion', '2026-09-29T02:44:19.300000+00:00', { verdict: 'better' }),
  ev('e3', 'builtin.describe', '2026-09-29T02:44:19.200000+00:00'),
  ev('e2', 'builtin.two_proportion', '2026-09-29T02:40:27+00:00', { verdict: 'worse' }),
  ev('e1', 'builtin.two_proportion', '2026-09-29T02:39:35+00:00', { params: { alpha: 0.1 } }),
  ev('e0', 'builtin.describe', '2026-09-29T02:39:35+00:00'),
];
"""


@needs_node
class TestCommonRegressions:
    def test_field_errors_land_on_the_most_specific_row(self):
        """review-contract-frontend-8: params.<name> and metrics.<key>.<field>
        keys reach their rows; the sub-path stays in the message."""
        result = _run_page(None, r"""
        const box = new FakeEl('div'); box.__root = true;
        const params = KX.textarea({ name: 'params' });
        box.appendChild(KX.field({ label: 'Parameter', input: params, name: 'params' }));
        const value = KX.input();
        box.appendChild(KX.field({ label: 'Erfolge', input: value, name: 'metrics.ctr', aliases: ['ctr'] }));
        const count = KX.input();
        box.appendChild(KX.field({ label: 'Versuche', input: count, name: 'metrics.ctr.count' }));
        const unmatched = KX.showFieldErrors(box, { 'params.alpha': 'Darf h\u00f6chstens 0,2 sein.',
          'metrics.ctr.count': 'Zu klein.', 'metrics.ctr': 'Keine Zahl.', 'rows.9.zzz': 'Weg.' });
        const slot = (input) => box.querySelectorAll('.kx-field').find((r) => r.contains(input)).querySelector('.kx-field-error').textContent;
        out({ unmatched, params: slot(params), value: slot(value), count: slot(count),
              invalid: [params, value, count].map((n) => n.getAttribute('aria-invalid')) });
        """, stubs=False)
        assert result["params"] == "alpha: Darf h\u00f6chstens 0,2 sein."
        assert result["value"] == "Keine Zahl."
        assert result["count"] == "Zu klein."
        assert result["invalid"] == ["true", "true", "true"]
        assert result["unmatched"] == ["rows.9.zzz: Weg."]

    def test_error_lines_drop_field_messages_the_message_repeats(self):
        """e2e-ui-13 (a): the variants error was shown twice, the second time
        behind the internal key 'variants:'."""
        result = _run_page(None, r"""
        const err = new KX.ApiError('\u00abVarianten\u00bb: Die Zuteilungen ergeben zusammen mehr als 100 %.', 400);
        out(KX.errorLines(err, ['variants: Die Zuteilungen ergeben zusammen mehr als 100 %.', 'x.y: Anderes.']));
        """, stubs=False)
        assert result == ["\u00abVarianten\u00bb: Die Zuteilungen ergeben zusammen mehr als 100 %.", "x.y: Anderes."]

    def test_dialog_does_not_repeat_the_message_of_its_only_field(self):
        """e2e-ui-13 (f): the CSV errors stood under the file field and again
        in the banner. A different field text ('Siehe Fehlerliste.') keeps the
        banner."""
        result = _run_page(None, r"""
        const run = async (fieldText) => {
          KX.dialog({ title: 'CSV importieren', body: KX.field({ label: 'Datei', input: KX.input({ name: 'file' }), name: 'file' }),
            actions: [{ label: 'Abbrechen', value: null }, { label: 'Importieren', primary: true,
              onClick: async () => { throw new KX.ApiError('Zeile 2: kaputt; Zeile 3: auch.', 400, { file: fieldText }); } }] });
          const dlg = document.body.children[document.body.children.length - 1];
          await dlg.querySelector('form').dispatch('submit');
          await tick();
          const box = dlg.querySelector('.kx-dialog-error');
          const res = { hidden: box.hidden, banner: box.textContent, field: dlg.querySelector('.kx-field-error').textContent };
          dlg.remove();
          return res;
        };
        out([await run('Zeile 2: kaputt; Zeile 3: auch.'), await run('Siehe Fehlerliste.')]);
        """, stubs=False)
        same, pointer = result
        assert same["hidden"] is True and same["field"] == "Zeile 2: kaputt; Zeile 3: auch."
        assert pointer["hidden"] is False and pointer["banner"] == "Zeile 2: kaputt; Zeile 3: auch."
        assert pointer["field"] == "Siehe Fehlerliste."

    def test_split_evaluations_marks_the_older_of_each_group(self):
        """e2e-ui-3: without the server flag the page groups by evaluator,
        metric, params and scope (key order does not matter), newest first."""
        result = _run_page(None, _EVALS + r"""
        evs[3].params = { alpha: 0.1, correction: 'holm' };
        evs.push({ id: 'e9', evaluator_key: 'builtin.two_proportion', metric_key: 'ctr', status: 'done',
                   params: { correction: 'holm', alpha: 0.1 }, scope: {}, created_at: '2026-09-28T00:00:00+00:00' });
        const client = KX.splitEvaluations(evs);
        const flagged = KX.splitEvaluations(evs.map((e) => Object.assign({}, e, { superseded: e.id === 'e3' })));
        out({ current: client.current.map((e) => e.id), earlier: client.earlier.map((e) => e.id),
              serverCurrent: flagged.current.map((e) => e.id), serverEarlier: flagged.earlier.map((e) => e.id) });
        """, stubs=False)
        assert result["current"] == ["e4", "e3", "e1"]
        assert result["earlier"] == ["e2", "e0", "e9"]
        # The server's flag wins when every item carries it.
        assert result["serverEarlier"] == ["e3"]

    def test_line_chart_is_drawn_at_the_width_it_has(self):
        """e2e-ui-10: a fixed 640 viewBox scaled the 11px ticks to ~5px on a
        phone. The chart now redraws at the frame's width (1 unit = 1px)."""
        result = _run_page(None, r"""
        window.ResizeObserver = class { constructor(cb) { window.ro = this; this.cb = cb; this.off = false; }
          observe(el) { this.el = el; } disconnect() { this.off = true; } };
        const series = [];
        for (let w = 0; w < 8; w += 1) {
          const d = new Date(Date.UTC(2026, 6, 6 + 7 * w)).toISOString();
          series.push({ bucket_start: d, variant: 'A', estimate: 0.01 + w / 1000, n: 100 });
          series.push({ bucket_start: d, variant: 'B', estimate: 0.012 + w / 1000, n: 100 });
        }
        const wrap = KX.lineChart(series, { kind: 'proportion', bucket: 'week', variants: [{ key: 'A' }, { key: 'B' }] });
        document.body.appendChild(wrap);
        const frame = wrap.querySelector('.kx-chart-frame');
        const info = () => { const svg = frame.querySelector('svg');
          return { viewBox: svg.getAttribute('viewBox'), width: svg.getAttribute('width'),
                   xLabels: svg.querySelectorAll('.kx-chart-xaxis text').length }; };
        const first = info();
        ro.cb([{ contentRect: { width: 330 } }]);
        const phone = info();
        ro.cb([{ contentRect: { width: 2000 } }]);
        const wide = info();
        wrap.remove();
        ro.cb([{ contentRect: { width: 0 } }]);
        out({ first, phone, wide, off: ro.off, svgs: frame.querySelectorAll('svg').length });
        """, stubs=False)
        assert result["first"]["viewBox"] == "0 0 640 220"
        assert result["phone"] == {"viewBox": "0 0 330 200", "width": "330", "xLabels": 3}
        assert result["wide"]["viewBox"] == "0 0 820 282"
        assert result["first"]["xLabels"] > result["phone"]["xLabels"]
        assert result["svgs"] == 1 and result["off"] is True


@needs_node
class TestDetailPageRegressions:
    def test_abort_dialog_has_distinct_buttons(self):
        """e2e-ui-1: the stop dialog showed two buttons 'Abbrechen' (close and
        abort) with the destructive one as the plain default."""
        result = _detail(r"""
        const S = __t.state;
        __t.runTransition(S.exp.transitions.find((t) => t.to === 'stopped'));
        __t.runTransition(S.exp.transitions.find((t) => t.to === 'analysis'));
        await tick();
        out(dialogs.map((d) => ({ title: d.opts.title, labels: d.opts.actions.map((a) => a.label),
                                  danger: d.opts.actions.map((a) => Boolean(a.danger)), body: d.opts.body.textContent })));
        """)
        stop, go = result
        assert stop["title"] == "Experiment abbrechen"
        assert stop["labels"] == ["Zur\u00fcck", "Experiment abbrechen"]
        assert stop["danger"] == [False, True]
        assert "nicht fortsetzen" in stop["body"]
        assert go["labels"] == ["Zur\u00fcck", "Zur Auswertung"] and go["danger"] == [False, False]
        for d in result:
            assert len(set(d["labels"])) == 2

    def test_transition_conflict_keeps_the_dialog_and_the_reason(self):
        """e2e-ui-8 (2): a 409 closed the dialog and lost the typed reason."""
        result = _detail(r"""
        routes.unshift((m, u) => (m === 'POST' && u === '/api/experiments/MKT-1/transition'
          ? { status: 409, body: { success: false, error: 'Das Experiment wurde inzwischen ge\u00e4ndert. Bitte neu laden.' } } : null));
        __t.runTransition(__t.state.exp.transitions.find((t) => t.to === 'stopped'));
        await tick();
        const d = dialogs[0].opts;
        const area = d.body.querySelector('textarea');
        area.value = 'Budget gestrichen';
        window.current = mkExp({ row_version: 6 });
        const gets = requests.filter((r) => r.method === 'GET' && r.url === '/api/experiments/MKT-1').length;
        let error = null;
        try { await d.actions[1].onClick({}); } catch (e) { error = { status: e.status, message: e.message }; }
        out({ error, reason: area.value, reloaded: requests.filter((r) => r.method === 'GET' && r.url === '/api/experiments/MKT-1').length - gets,
              rv: __t.state.exp.row_version, conflictShown: document.getElementById('kxConflict').hidden === false });
        """)
        assert result["error"]["status"] == 409
        assert "erneut best\u00e4tigen" in result["error"]["message"]
        assert result["reason"] == "Budget gestrichen"
        assert result["reloaded"] == 1 and result["rv"] == 6
        assert result["conflictShown"] is False

    def test_superseded_evaluations_are_collapsed_and_marked(self):
        """e2e-ui-3: outdated results stood next to current ones, unmarked."""
        result = _detail(_EVALS + r"""
        __t.state.exp.evaluations = evs;
        __t.renderEvaluations();
        const box = document.getElementById('kxEvaluations');
        const top = box.children.filter((c) => c.tagName === 'ARTICLE');
        const details = box.querySelector('details');
        const chips = (card) => card.querySelectorAll('.kx-chip').map((c) => c.textContent);
        out({ top: top.map((c) => c.dataset.evaluationId), summary: details.querySelector('summary').textContent,
              open: Boolean(details.open), earlier: details.querySelectorAll('article').map((c) => c.dataset.evaluationId),
              topChips: top.map(chips), earlierChips: details.querySelectorAll('article').map(chips) });
        """)
        assert result["top"] == ["e4", "e3", "e1"]
        assert result["summary"] == "Fr\u00fchere Auswertungen (2)"
        assert result["open"] is False
        assert result["earlier"] == ["e2", "e0"]
        assert all("\u00fcberholt" not in c for c in result["topChips"])
        assert all("\u00fcberholt" in c for c in result["earlierChips"])

    def test_metric_card_intervals_come_from_all_data_at_95_percent(self):
        """review-contract-frontend-1: the card paired all-data estimates with
        a scoped, 90 % or stale interval."""
        result = _detail(r"""
        const v = (variant, n, lo, hi) => ({ variant, n, ci_low: lo, ci_high: hi });
        const ev = (id, over) => Object.assign({ id, evaluator_key: 'builtin.describe', metric_key: 'ndcg', params: {},
          scope: {}, status: 'done', created_at: '2026-09-29T02:00:00+00:00' }, over);
        const aggs = [{ variant: 'baseline', n: 60 }, { variant: 'candidate', n: 60 }];
        __t.state.exp.evaluations = [
          ev('scoped', { evaluator_key: 'builtin.paired_t', scope: { runs: 'latest' }, created_at: '2026-09-29T02:00:03+00:00',
                         output: { variants: [v('baseline', 30, 0.48, 0.51), v('candidate', 30, 0.54, 0.56)] } }),
          ev('alpha', { evaluator_key: 'builtin.welch', params: { alpha: 0.1 }, created_at: '2026-09-29T02:00:02+00:00',
                        output: { variants: [v('baseline', 60, 0.49, 0.5), v('candidate', 60, 0.4, 0.45)] } }),
          ev('all', { created_at: '2026-09-29T02:00:01+00:00',
                      output: { variants: [v('baseline', 59, 0.48, 0.5), v('candidate', 60, 0.39, 0.46)] } }),
        ];
        const picked = __t.intervalsFor('ndcg', aggs);
        // A newer, still running describe supersedes the done one: no interval.
        __t.state.exp.evaluations.unshift(ev('rerun', { status: 'running', created_at: '2026-09-29T02:00:04+00:00' }));
        out({ picked, rerun: __t.intervalsFor('ndcg', aggs) });
        """)
        assert result["picked"] == {"candidate": [0.39, 0.46]}
        assert result["rerun"] == {}

    def test_decision_form_names_the_target_state(self):
        """e2e-ui-5: it said the status changes to 'Entscheiden' (the verb)."""
        result = _detail(r"""
        __t.state.exp.transitions = [{ to: 'decided', label: 'Entscheiden', decides: true, allowed: true }];
        out(__t.decisionForm().querySelector('.kx-help').textContent);
        """)
        assert result == "Mit der Entscheidung wechselt der Status zu \u00abEntschieden\u00bb."

    def test_activity_shows_the_details(self):
        """e2e-ui-7: every edit read 'Angaben geaendert'; transitions had no states."""
        result = _detail(r"""
        __t.state.exp.metrics = [{ key: 'ctr', name: 'Klickrate', kind: 'proportion' }];
        const a = (action, label, detail) => __t.describeActivity({ action, label, detail });
        out([
          a('experiments.experiment.update', 'Angaben ge\u00e4ndert', { changed: ['tags', 'title'] }),
          a('experiments.experiment.update', 'Angaben ge\u00e4ndert', { changed: ['archived'], archived: true }),
          a('experiments.experiment.update', 'Angaben ge\u00e4ndert', { changed: ['archived'] }),
          a('experiments.experiment.transition', 'Status gewechselt', { from: 'draft', to: 'running' }),
          a('experiments.experiment.decide', 'Entscheidung festgehalten', { from: 'analysis', to: 'decided', verdict: 'ship' }),
          a('experiments.measurements.import', 'CSV-Datei importiert', { rows: 12, metrics: ['ctr'] }),
          a('experiments.experiment.variants', 'Varianten ge\u00e4ndert', { added: [], removed: [], variants: 2 }),
          a('experiments.pipeline.run', 'Auswertungen des Typs ausgef\u00fchrt', { created: 0, evaluations: 0 }),
          a('experiments.note.add', 'Notiz erfasst', { kind: 'feedback' }),
          a('experiments.unknown', 'Etwas', {}),
        ]);
        """)
        assert result == [
            "Ge\u00e4ndert: Schlagw\u00f6rter, Titel",
            "Archiviert",
            "Archiviert oder wiederhergestellt",
            "Status \u00abEntwurf\u00bb \u2192 \u00abL\u00e4uft\u00bb",
            "Entscheidung festgehalten: \u00dcbernehmen; Status \u00abAuswertung\u00bb \u2192 \u00abEntschieden\u00bb",
            "CSV-Datei importiert: 12 Messwerte (Klickrate)",
            "Varianten gespeichert (ohne neue oder entfernte)",
            "Auswertungen des Typs ausgef\u00fchrt (0 neu)",
            "Notiz erfasst (R\u00fcckmeldung)",
            "Etwas",
        ]

    def test_a_missing_batch_is_not_a_missing_experiment(self):
        """review-contract-frontend-3: any 404 'Nicht gefunden.' toasted
        'Das Experiment gibt es nicht mehr.'."""
        result = _detail(r"""
        routes.unshift((m, u) => (m === 'DELETE' && u.indexOf('/batches/') !== -1
          ? { status: 404, body: { success: false, error: 'Nicht gefunden.' } } : null));
        const before = requests.length;
        await __t.undoBatch({ batch_id: 'b1', rows: 3, source: 'csv', created_at: '2026-09-29T02:00:00+00:00' });
        await tick();
        const child = toasts.slice();
        const reloaded = requests.slice(before).some((r) => r.method === 'GET' && r.url === '/api/experiments/MKT-1');
        toasts.length = 0;
        __t.reportError(new KX.ApiError('Nicht gefunden.', 404));
        out({ child, reloaded, experiment: toasts });
        """)
        assert result["child"] == [["error", "Diese Erfassung gibt es nicht mehr."]]
        assert result["reloaded"] is True
        assert result["experiment"] == [["error", "Das Experiment gibt es nicht mehr."]]

    def test_reindex_reports_when_nothing_was_queued(self):
        """review-contract-frontend-4: 'eingeplant' was toasted for {queued: false}."""
        result = _detail(r"""
        let queued = false;
        routes.unshift((m, u) => (m === 'POST' && u === '/api/experiments/MKT-1/reindex'
          ? { body: { success: true, result: { queued } } } : null));
        await __t.reindex(new FakeEl('button'));
        queued = true;
        await __t.reindex(new FakeEl('button'));
        __t.state.meta = { index_enabled: false };
        __t.renderHeader();
        const button = find(document.getElementById('kxHeader'), 'button', 'Neu indexieren');
        out({ toasts, disabled: Boolean(button.disabled), title: button.getAttribute('title') });
        """)
        assert result["toasts"] == [
            ["info", "Die \u00dcbertragung nach Knovas ist ausgeschaltet; es wurde nichts eingeplant."],
            ["success", "Die \u00dcbertragung nach Knovas ist eingeplant."],
        ]
        assert result["disabled"] is True
        assert result["title"] == "Die \u00dcbertragung nach Knovas ist ausgeschaltet."

    def test_more_runs_never_shrinks_the_table(self):
        """review-contract-frontend-2: the first click replaced the 100
        snapshot runs with the newest 50."""
        setup = r"""
        const run = (i) => ({ id: 'r' + String(i).padStart(3, '0'), name: 'run-' + i, status: 'finished', source: 'api',
                              created_at: '2026-09-29T00:00:00+00:00', params: {}, metrics: {} });
        window.allRuns = []; for (let i = 119; i >= 0; i -= 1) allRuns.push(run(i));
        window.expOverrides = { runs: allRuns.slice(0, 100), run_count: 120 };
        routes.push((m, u) => {
          if (m !== 'GET' || u.indexOf('/api/experiments/MKT-1/runs?') !== 0) return null;
          const q = new URLSearchParams(u.split('?')[1]);
          const start = q.get('after') ? 100 : 0;
          const items = allRuns.slice(start, start + Number(q.get('limit') || 50));
          return { body: { success: true, result: { items, next_after: start + items.length < 120 ? 'CUR' : null } } };
        });
        """
        result = _detail(r"""
        const btn = new FakeEl('button');
        await __t.loadMoreRuns(btn);
        const plain = { url: requests[requests.length - 1].url, rows: __t.state.runs.length,
                        unique: new Set(__t.state.runs.map((r) => r.id)).size,
                        shown: document.getElementById('kxRuns').querySelectorAll('tbody tr').length };
        __t.state.runs = null;
        __t.state.exp.runs_next_after = 'CUR';
        await __t.loadMoreRuns(btn);
        out({ plain, cursorUrl: requests[requests.length - 1].url, cursorRows: __t.state.runs.length });
        """, setup=setup)
        assert result["plain"] == {"url": "/api/experiments/MKT-1/runs?limit=200", "rows": 120,
                                   "unique": 120, "shown": 120}
        assert result["cursorUrl"] == "/api/experiments/MKT-1/runs?limit=100&after=CUR"
        assert result["cursorRows"] == 120

    def test_sample_size_counts_the_current_variants(self):
        """review-contract-frontend-6 and e2e-ui-14 (1): the result used the
        variant count of the first render; the mde label wrapped with '(Pp.)'."""
        setup = r"""
        window.expOverrides = { status: 'draft', status_phase: null, started_at: null };
        routes.push((m, u) => (u.indexOf('/api/experiments/sample-size?') === 0
          ? { body: { success: true, result: { per_variant: 1000 } } } : null));
        """
        result = _detail(r"""
        const box = document.getElementById('kxSampleSize');
        const label = () => box.querySelector('[data-field="mde"] .kx-label').textContent;
        const help = () => box.querySelector('[data-field="mde"] .kx-help').textContent;
        const labels = [[label(), help()]];
        const kind = box.querySelector('[name="kind"]');
        kind.value = 'mean'; await kind.dispatch('change');
        labels.push([label(), help()]);
        kind.value = 'proportion'; await kind.dispatch('change');
        __t.state.exp = mkExp({ status: 'draft', status_phase: null, started_at: null,
          variants: [{ key: 'A' }, { key: 'B' }, { key: 'C' }] });
        box.querySelector('[name="base"]').value = '1,2';
        box.querySelector('[name="mde"]').value = '0,2';
        await box.querySelector('form').dispatch('submit');
        await tick();
        out({ labels, text: box.querySelector('.kx-headline').textContent });
        """, setup=setup)
        assert result["labels"] == [["Kleinster relevanter Unterschied", "In Prozentpunkten, z. B. 0,3."],
                                    ["Kleinster relevanter Unterschied", "In der Einheit der Metrik."]]
        assert result["text"] == "Rund 1'000 je Variante, bei 3 Varianten 3'000 insgesamt."

    def test_own_save_is_not_hidden_by_an_older_get(self):
        """review-contract-frontend-9: a GET read before the PATCH committed
        was rendered after it, with the old row_version."""
        result = _detail(r"""
        let release;
        const gate = new Promise((r) => { release = r; });
        let gets = 0;
        routes.unshift((m, u) => {
          if (m !== 'GET' || u !== '/api/experiments/MKT-1') return null;
          gets += 1;
          return gets === 1 ? gate.then(() => ({ body: { success: true, experiment: mkExp({ row_version: 5, title: 'Alt' }) } }))
            : { body: { success: true, experiment: mkExp({ row_version: 6, title: 'Neu' }) } };
        });
        __t.state.editors.add('description');
        __t.state.bases.set('description', 5);
        const first = __t.load();
        const saved = __t.applySnapshot({ key: 'MKT-1', row_version: 6, title: 'Neu' }, 5);
        release();
        await first; await saved; await tick();
        out({ gets, rv: __t.state.exp.row_version, h1: document.getElementById('kxHeader').querySelector('h1').textContent,
              base: __t.state.bases.get('description') });
        """)
        assert result == {"gets": 2, "rv": 6, "h1": "Neu", "base": 6}

    def test_reload_after_a_conflict_keeps_every_draft(self):
        """e2e-ui-8: 'Neu laden' closed all editors and dropped the typed text
        (and the unrelated note draft)."""
        result = _detail(r"""
        const box = document.getElementById('kxHypothesis');
        __t.openTextEditor('hypothesis', box, 'Hypothese', 20000, false);
        box.querySelector('textarea').value = 'Mein Entwurf';
        const note = document.getElementById('kxNotes').querySelector('textarea');
        note.value = 'Notizentwurf';
        await note.dispatch('input');
        window.current = mkExp({ row_version: 6, hypothesis: 'Fremde Fassung' });
        __t.showConflict('Das Experiment wurde inzwischen ge\u00e4ndert. Bitte neu laden.');
        await find(document.getElementById('kxConflict'), 'button', 'Neu laden').dispatch('click');
        await tick();
        const kept = { draft: box.querySelector('textarea').value, base: __t.state.bases.get('hypothesis'),
                       note: document.getElementById('kxNotes').querySelector('textarea').value,
                       notice: box.querySelector('.kx-draft-note').textContent };
        await find(box, 'button', 'Entwurf verwerfen').dispatch('click');
        out({ kept, after: box.textContent, editing: __t.state.editors.has('hypothesis'),
              textareas: box.querySelectorAll('textarea').length });
        """)
        kept = result["kept"]
        assert kept["draft"] == "Mein Entwurf" and kept["base"] == 6
        assert kept["note"] == "Notizentwurf"
        assert "\u00e4lteren Fassung" in kept["notice"] and "Fremde Fassung" in kept["notice"]
        assert result["editing"] is False and result["textareas"] == 0
        assert "Fremde Fassung" in result["after"]

    def test_pending_index_state_is_polled_until_it_settles(self):
        """e2e-ui-11: 'In Knovas: ausstehend' stayed until a manual reload."""
        setup = "window.expOverrides = { index: { state: 'pending', state_label: 'ausstehend' } };"
        result = _detail(r"""
        const header = () => document.getElementById('kxHeader').textContent;
        const before = { polls: timers.filter((t) => t.ms === 4000).length, text: header().indexOf('ausstehend') !== -1 };
        window.current = mkExp({ index: { state: 'indexed', state_label: 'aktuell' } });
        runTimers(4000);
        await tick();
        const after = { polls: timers.filter((t) => t.ms === 4000).length, text: header().indexOf('In Knovas: aktuell') !== -1 };
        // Past the five minutes it stops asking.
        __t.state.exp.index = { state: 'pending' };
        __t.state.indexPollStarted = Date.now() - 6 * 60 * 1000;
        __t.scheduleIndexPoll(false);
        out({ before, after, expired: timers.filter((t) => t.ms === 4000).length });
        """, setup=setup)
        assert result["before"] == {"polls": 1, "text": True}
        assert result["after"] == {"polls": 0, "text": True}
        assert result["expired"] == 0

    def test_empty_metric_catalog_says_who_can_help(self):
        """e2e-ui-2: the editor offered an empty select and endless rows."""
        setup = "window.pageData = { experimentKey: 'MKT-1', canManage: true }; window.catalog = [];"
        result = _detail(r"""
        await __t.openMetricsEditor();
        const box = document.getElementById('kxMetrics');
        const link = box.querySelector('a');
        await tick();
        out({ text: box.textContent, selects: box.querySelectorAll('select').length, href: link && link.getAttribute('href'),
              editing: __t.state.editors.has('metrics'), measurements: document.getElementById('kxMeasurements').textContent });
        """, setup=setup)
        assert "Bereich \u00abMarketing\u00bb gibt es noch keine Metriken" in result["text"]
        assert result["selects"] == 0 and result["editing"] is False
        assert result["href"] == "/experiments/verwaltung#metriken"
        assert "noch keine Metriken" in result["measurements"]

    def test_runs_show_their_id_for_the_csv_run_column(self):
        """e2e-ui-12: the CSV 'run' column needs an ID the page never showed."""
        setup = r"""window.expOverrides = { run_count: 1, runs: [{ id: '3e04d797-bbee-4225-b4ee-9b125be57fa5',
          name: 'nightly', status: 'finished', source: 'manual', created_at: '2026-09-29T00:00:00+00:00', params: {}, metrics: {} }] };"""
        result = _detail(r"""
        const row = document.getElementById('kxRuns').querySelector('tbody tr');
        const id = row.querySelector('.kx-run-id .kx-mono');
        __t.state.exp.metrics = [{ key: 'ctr', name: 'Klickrate', kind: 'proportion' }];
        __t.openCsvDialog();
        await tick();
        out({ shown: id.textContent, title: id.getAttribute('title'),
              button: Boolean(find(row, 'button', 'ID kopieren')), help: dialogs[0].opts.body.textContent });
        """, setup=setup)
        assert result["shown"] == "ID 3e04d797\u2026"
        assert result["title"] == "3e04d797-bbee-4225-b4ee-9b125be57fa5"
        assert result["button"] is True
        assert "run (Lauf-ID aus der Tabelle \u00abL\u00e4ufe\u00bb" in result["help"]

    def test_run_errors_are_keyed_like_the_form(self):
        """review-contract-frontend-8: rows.<i>.<field> from an older server
        become metrics.<key>[.<field>] and 'Messwert i' the metric's name."""
        result = _detail(r"""
        __t.state.exp.metrics = [{ key: 'ctr', name: 'Klickrate', kind: 'proportion' }];
        const e = __t.runErrorForForm(new KX.ApiError('Messwert 1: Es kann nicht mehr Erfolge als Versuche geben.', 400,
          { 'rows.0.value': 'x', 'rows.1.count': 'y' }), ['ctr', 'cpc']);
        const already = new KX.ApiError('\u00abKlickrate\u00bb: x', 400, { 'metrics.ctr': 'x' });
        out({ message: e.message, fields: e.fields, same: __t.runErrorForForm(already, ['ctr']) === already });
        """)
        assert result["message"] == "\u00abKlickrate\u00bb: Es kann nicht mehr Erfolge als Versuche geben."
        assert result["fields"] == {"metrics.ctr": "x", "metrics.cpc.count": "y"}
        assert result["same"] is True

    def test_variant_allocations_over_100_percent_are_caught_once(self):
        """e2e-ui-13 (a): the total is checked in percent before sending, and a
        server error is not repeated behind its internal key."""
        result = _detail(r"""
        const box = document.getElementById('kxVariants');
        __t.openVariantsEditor();
        const alloc = (a, b) => { const rows = box.querySelectorAll('tbody tr');
          rows[0].querySelectorAll('input')[4].value = a; rows[1].querySelectorAll('input')[4].value = b; };
        const save = () => find(box, 'button', 'Varianten speichern').dispatch('click');
        alloc('70', '50');
        await save();
        const local = { error: box.querySelector('.kx-dialog-error').textContent,
                        puts: requests.filter((r) => r.method === 'PUT').length };
        routes.unshift((m, u) => (m === 'PUT' ? { status: 400, body: { success: false,
          error: '\u00abVarianten\u00bb: Die Zuteilungen ergeben zusammen mehr als 100 %.',
          fields: { variants: 'Die Zuteilungen ergeben zusammen mehr als 100 %.' } } } : null));
        alloc('50', '50');
        await save(); await tick();
        const lines = box.querySelector('.kx-dialog-error').children.map((p) => p.textContent);
        out({ local, lines });
        """)
        assert "mehr als 100 %" in result["local"]["error"] and result["local"]["puts"] == 0
        assert result["lines"] == ["\u00abVarianten\u00bb: Die Zuteilungen ergeben zusammen mehr als 100 %."]


_LIST_SETUP = r"""
window.pageData = { canManage: false };
window.domainFails = window.domainFails || 0;
routes.push((m, u) => {
  if (u.indexOf('/api/experiments/domains') !== 0) return null;
  if (window.domainFails > 0) { window.domainFails -= 1; return { status: 503, body: { success: false, error: 'Der Server ist gerade nicht erreichbar.' } }; }
  const all = [{ key: 'marketing', name: 'Marketing', color: '#eb6834', experiment_count: 3 },
               { key: 'alt', name: 'Alter Bereich', color: '#123456', experiment_count: 1, archived: true }];
  return { body: { success: true, domains: u.indexOf('archived=1') !== -1 ? all : all.slice(0, 1) } };
});
routes.push((m, u) => (u.indexOf('/api/experiments/types') === 0 ? { body: { success: true, types: [] } } : null));
routes.push((m, u) => (u.indexOf('/api/experiments?') === 0 ? { body: { success: true, result: { items: [], next_after: null, total: 0 } } } : null));
routes.push((m, u) => (u === '/api/experiments/meta' ? { body: { success: true, meta: {} } } : null));
"""


@needs_node
class TestListPageRegressions:
    def _run(self, body, setup=""):
        return _run_page("experiments_list.js", body, setup=setup + _LIST_SETUP, exports=LIST_EXPORTS)

    def test_archived_domain_link_keeps_its_filter_and_chip(self):
        """review-contract-frontend-10: archived domains were never loaded, so
        ?domain=<archived> was dropped and 'Archivierte zeigen' had no chip."""
        result = self._run(r"""
        out({ domain: __t.state.filters.domain, asked: requests.filter((r) => r.url.indexOf('/domains') !== -1).map((r) => r.url),
              chips: document.getElementById('kxDomainChips').querySelectorAll('button').map((b) => b.textContent) });
        """, setup="location.search = '?domain=alt&archived=1';")
        assert result["domain"] == "alt"
        assert result["asked"] == ["/api/experiments/domains?archived=1"]
        assert any(c.startswith("Alter Bereich") for c in result["chips"])

    def test_unknown_domain_filter_is_dropped_and_the_list_reloaded(self):
        result = self._run(r"""
        await tick();
        const lists = requests.filter((r) => r.url.indexOf('/api/experiments?') === 0).map((r) => r.url);
        out({ domain: __t.state.filters.domain, last: lists[lists.length - 1], count: lists.length });
        """, setup="location.search = '?domain=gibtsnicht';")
        assert result["domain"] == ""
        assert result["count"] == 2 and "domain=" not in result["last"]

    def test_new_experiment_retries_failed_domains(self):
        """review-contract-frontend-12: after a failed domains request it said
        'Es gibt noch keinen Bereich' and never asked again."""
        result = self._run(r"""
        const failedFirst = __t.state.domainsFailed;
        // Not awaited: the stubbed dialog stays open.
        document.getElementById('kxNewExperiment').dispatch('click');
        await tick();
        const retried = { dialogs: dialogs.length, toasts: toasts.map((t) => t[1]) };
        window.domainFails = 1;
        __t.state.domainsFailed = true;
        toasts.length = 0;
        await __t.openNewExperiment('');
        out({ failedFirst, retried, again: { dialogs: dialogs.length, toasts: toasts.map((t) => t[1]) } });
        """, setup="window.domainFails = 1;")
        assert result["failedFirst"] is True
        assert result["retried"]["dialogs"] == 1
        assert not any("noch keinen Bereich" in t for t in result["retried"]["toasts"])
        assert result["again"]["dialogs"] == 1
        assert result["again"]["toasts"] == ["Der Server ist gerade nicht erreichbar."]

    def test_moved_items_are_merged_by_key(self):
        """The list API now appends experiments that moved above the cursor
        (moved: true); they may already be listed."""
        result = self._run(r"""
        const it = (key, at, over) => Object.assign({ key, updated_at: at }, over || {});
        const merged = __t.mergeByKey(
          [it('MKT-3', '2026-09-29T03:00:00+00:00'), it('MKT-2', '2026-09-29T02:00:00+00:00')],
          [it('MKT-1', '2026-09-29T01:00:00+00:00'), it('MKT-2', '2026-09-29T04:00:00+00:00', { moved: true })]);
        out(merged.map((i) => [i.key, Boolean(i.moved)]));
        """)
        assert result == [["MKT-2", True], ["MKT-3", False], ["MKT-1", False]]


@needs_node
class TestManagePageRegressions:
    def _run(self, body, setup=""):
        base = r"""
        window.pageData = { canManage: true };
        routes.push((m, u) => (u === '/api/experiments/meta' ? { body: { success: true, meta: {} } } : null));
        """
        return _run_page("experiments_manage.js", body, setup=setup + base, exports=MANAGE_EXPORTS)

    def test_copy_fixes(self):
        """e2e-ui-13 (b) '1 Experimente', (c) raw phase keys, (e) zero counts."""
        result = self._run(r"""
        out({
          none: __t.countsText({ domain: false, types: 0, metrics: 0, evaluators: 0 }),
          some: __t.countsText({ domain: true, types: 2, metrics: 1, evaluators: 0 }),
          counts: [__t.experimentCount(1), __t.experimentCount(2), __t.experimentCount(0)],
          preview: __t.definitionPreview({ states: [{ key: 'running', label: 'L\u00e4uft', phase: 'running' },
                                                     { key: 'stopped', label: 'Abgebrochen', phase: 'stopped' }] }).textContent,
        });
        """)
        assert result["none"] == "nichts zu erg\u00e4nzen"
        assert result["some"] == "1 Bereich, 2 Typen, 1 Metrik neu"
        assert result["counts"] == ["1 Experiment", "2 Experimente", "0 Experimente"]
        assert "L\u00e4uft \u00b7 Phase l\u00e4uft" in result["preview"]
        assert "Abgebrochen \u00b7 Phase abgebrochen" in result["preview"]
        assert "\u00b7 running" not in result["preview"]

    def test_type_check_errors_are_not_repeated_in_the_title(self):
        """e2e-ui-13 (g): the banner title repeated the first listed error."""
        result = self._run(r"""
        routes.unshift((m, u) => (u === '/api/experiments/types/t1'
          ? { body: { success: true, type: { id: 't1', name: 'T', current_version: 1, definition: { states: [] } } } } : null));
        routes.unshift((m, u) => (u === '/api/experiments/types/validate' ? { status: 400, body: { success: false,
          error: '\u00abstates.0.key\u00bb: Pflichtfeld.', fields: { 'states.0.key': 'Pflichtfeld.', initial: 'Fehlt.' } } } : null));
        const box = new FakeEl('div'); box.__root = true;
        await __t.openTypeEditor('t1', box, new FakeEl('div'));
        const check = find(box, 'button', 'Pr\u00fcfen');
        await check.dispatch('click', { currentTarget: check });
        await tick();
        const banner = box.querySelector('.kx-banner--error');
        out({ title: banner.querySelector('p').textContent, items: banner.querySelectorAll('li').map((li) => li.textContent) });
        """)
        assert result["title"] == "Die Definition ist ung\u00fcltig (2 Fehler)."
        assert result["items"] == ["states.0.key: Pflichtfeld.", "initial: Fehlt."]

    def test_personal_search_preference_explains_the_global_switch(self):
        """e2e-ui-6: the personal checkbox looked effective while the manager
        had switched experiments off in the search for everyone."""
        result = self._run(r"""
        const run = async (global) => {
          routes.unshift((m, u) => (u === '/api/experiments/settings' ? { body: { success: true, settings: { show_in_search: global } } } : null));
          routes.unshift((m, u) => (u === '/api/experiments/preferences' ? { body: { success: true, preferences: { show_in_search: true } } } : null));
          const box = new FakeEl('div'); box.__root = true;
          await __t.renderPreferences(box);
          const input = box.querySelector('input');
          return { text: box.textContent, checked: Boolean(input.checked), disabled: Boolean(input.disabled) };
        };
        out([await run(false), await run(true)]);
        """)
        off, on = result
        assert "Die Verwaltung hat Experimente in der normalen Suche f\u00fcr alle ausgeschaltet" in off["text"]
        assert off["checked"] is True and off["disabled"] is False
        assert "Die Verwaltung hat" not in on["text"]


class TestLayoutRegressions:
    def test_list_titles_never_break_mid_word(self):
        """e2e-ui-14 (2): overflow-wrap: anywhere squeezed the title column to
        one character on a phone ('Produktbil d')."""
        css = re.sub(r"/\*.*?\*/", "", (CSS / "experiments.css").read_text(encoding="utf-8"), flags=re.S)
        rules = dict((sel.strip(), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css))
        assert "min-width: 12rem" in rules[".kx-title-cell"]
        assert "overflow-wrap: break-word" in rules[".kx-title-cell strong"]
        assert "anywhere" not in rules[".kx-title-cell strong"]

    def test_mobile_nav_scrolls_inside_on_every_page(self):
        """e2e-ui-14 (3): below 900px the nav pushed '/' and '/settings' to
        802px wide; only the experiments pages made it scroll in itself."""
        css = re.sub(r"/\*.*?\*/", "", (CSS / "style.css").read_text(encoding="utf-8"), flags=re.S)
        start = css.rindex("@media (max-width: 900px)")
        block = css[start:css.index("\n}", start)]
        nav = re.search(r"\.app-nav \{([^}]*)\}", block).group(1)
        assert "overflow-x: auto" in nav and "min-width: 0" in nav
        assert re.search(r"\.app-nav-item \{[^}]*flex: 0 0 auto", block)
        sidebar = (TEMPLATES / "_sidebar.html").read_text(encoding="utf-8")
        script = re.search(r"<script>(.*?)</script>", sidebar, re.S).group(1)
        assert ".app-nav-item.active" in script and "scrollLeft" in script

    @needs_node
    def test_sidebar_script_parses(self, tmp_path):
        sidebar = (TEMPLATES / "_sidebar.html").read_text(encoding="utf-8")
        script = re.search(r"<script>(.*?)</script>", sidebar, re.S).group(1)
        path = tmp_path / "sidebar.js"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr


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
