"""Cortex galaxy search: the four result views behind /ontology?version=N.

galaxy_search.js is pure -- the documents, the ranking and the geometry of
the structured views -- and is tested on its own. The boot tests load it with
point_cloud_mockup.js and galaxy_results.js against a stub DOM in the order
the page loads them, so every version has to start with a query in the URL.
The Node tests skip when node is not installed.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from test_experiments_frontend import JS, NODE, TEMPLATES, needs_node

SEARCH_JS = JS / "galaxy_search.js"
GALAXY_JS = JS / "point_cloud_mockup.js"
RESULTS_JS = JS / "galaxy_results.js"
PAGE = (SEARCH_JS, GALAXY_JS, RESULTS_JS)

# Just enough DOM for the galaxy to start: every id resolves to an element,
# canvases paint into a context that accepts any call, and reduced motion is
# on, so each restart() draws exactly one frame and no animation loop runs.
_PRELUDE = r"""
const vm = require('vm');
const fs = require('fs');
const paint = () => new Proxy({}, {
  get(target, prop) {
    if (prop in target) return target[prop];
    if (prop === 'createLinearGradient' || prop === 'createRadialGradient') {
      return () => ({ addColorStop() {} });
    }
    if (prop === 'measureText') return (text) => ({ width: String(text).length * 6 });
    return () => {};
  },
  set(target, prop, value) { target[prop] = value; return true; },
});
class ClassList {
  constructor() { this.names = new Set(); }
  add(...names) { names.forEach((n) => this.names.add(n)); }
  remove(...names) { names.forEach((n) => this.names.delete(n)); }
  contains(name) { return this.names.has(name); }
  toggle(name, force) {
    const on = force === undefined ? !this.names.has(name) : Boolean(force);
    if (on) this.names.add(name); else this.names.delete(name);
    return on;
  }
}
class Event {
  constructor(type, init) { this.type = type; this.defaultPrevented = false; Object.assign(this, init || {}); }
  preventDefault() { this.defaultPrevented = true; }
}
class El {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase(); this.children = []; this.parentElement = null;
    this.dataset = {}; this.attributes = {}; this.listeners = {}; this.hidden = false; this.value = '';
    this.style = { setProperty() {} }; this.classList = new ClassList(); this.className = '';
    this.textContent = ''; this.innerHTML = ''; this.tabIndex = 0; this.title = '';
  }
  getContext() { return paint(); }
  getBoundingClientRect() { return { left: 0, top: 0, right: 1280, bottom: 800, width: 1280, height: 800 }; }
  appendChild(child) { this.children.push(child); child.parentElement = this; return child; }
  append(...children) { children.forEach((c) => this.appendChild(typeof c === 'string' ? new El('#text') : c)); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
  removeAttribute(k) { delete this.attributes[k]; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  removeEventListener() {}
  dispatchEvent(event) { (this.listeners[event.type] || []).forEach((fn) => fn(event)); return true; }
  requestSubmit() { this.dispatchEvent(new Event('submit', { cancelable: true })); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  scrollIntoView() {}
  focus() {}
  blur() {}
}
const stage = new El('div');
const elements = new Map();
const suns = ['Mandate', 'Mandanten', 'Personen', 'Gerichte', 'Gegenparteien', 'Beh\u00f6rden']
  .map((label) => { const el = new El('button'); el.dataset.galaxySystem = label; return el; });
const document = {
  body: new El('body'),
  getElementById(id) {
    if (!elements.has(id)) { const el = new El('div'); el.id = id; stage.appendChild(el); elements.set(id, el); }
    return elements.get(id);
  },
  querySelectorAll(selector) { return selector === '[data-galaxy-system]' ? suns : []; },
  querySelector() { return null; },
  createElement: (tag) => new El(tag),
  createTextNode: (text) => { const el = new El('#text'); el.textContent = String(text); return el; },
  addEventListener() {},
};
const context = {
  document, console, Math, JSON, Number, String, Object, Array, Set, Map, Date, RegExp,
  Promise, Error, Proxy, URLSearchParams, Event,
  location: { search: __SEARCH__, pathname: '/ontology' },
  history: { replaceState(state, title, url) { context.lastUrl = url; } },
  performance: { now: () => 1000 },
  matchMedia: () => ({ matches: true, addEventListener() {} }),
  requestAnimationFrame: () => 0, cancelAnimationFrame() {},
  setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
  devicePixelRatio: 1, innerWidth: 1280, innerHeight: 800,
  addEventListener() {},
};
context.window = context;
vm.createContext(context);
const load = (file) => vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
"""


def _boot(body, *, search="", files=PAGE):
    """Load files into one vm context and run body there; body reports with out(value)."""
    script = _PRELUDE.replace("__SEARCH__", json.dumps(search))
    for f in files:
        script += f"load({json.dumps(str(f))});\n"
    script += "const out = (v) => process.stdout.write(JSON.stringify(v));\n"
    script += ("vm.runInContext(" + json.dumps("(function(){" + body + "})()")
               + ", Object.assign(context, { out }));\n")
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout)


_INDEX = """
const G = window.cortexGalaxy;
const S = window.CortexGalaxySearch;
const index = S.buildIndex(G.systems, G.documents);
const run = (q) => S.search(index, q);
"""


@needs_node
@pytest.mark.parametrize("name", ["galaxy_search.js", "galaxy_results.js", "point_cloud_mockup.js"])
def test_scripts_parse(name):
    proc = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def test_page_loads_search_before_the_galaxy_and_the_results_after_it():
    html = (TEMPLATES / "ontology.html").read_text(encoding="utf-8")
    order = [html.index(f"js/{name}") for name in
             ("galaxy_search.js", "point_cloud_mockup.js", "galaxy_results.js", "ontology.js")]
    assert order == sorted(order)
    for element_id in ("galaxyResults", "galaxyResultsRail", "galaxyDocumentCard",
                       "galaxyVersions", "galaxyExamples"):
        assert f'id="{element_id}"' in html


@needs_node
def test_version_comes_from_the_url():
    versions = _boot("""
      const S = window.CortexGalaxySearch;
      out(['', '?version=1', '?version=2', '?q=Keller&version=3', '?version=4',
           '?version=5', '?version=zwei'].map(S.versionFromSearch));
    """, files=(SEARCH_JS,))
    assert versions == [1, 1, 2, 3, 4, 1, 1]


@needs_node
def test_version_one_keeps_the_heatmap_and_has_no_document_nodes():
    state = _boot("""
      out({ documents: window.cortexGalaxy.documents.length,
            snapshot: window.cortexGalaxyResults.snapshot(),
            searching: document.body.classList.contains('galaxy-searching'),
            query: document.getElementById('galaxySearchInput').value });
    """, search="?q=Keller")
    assert state == {"documents": 0, "snapshot": {"version": 1}, "searching": True, "query": "Keller"}


@needs_node
def test_every_document_is_a_node_filed_next_to_its_entity():
    report = _boot(_INDEX + """
      const docs = index.nodes.filter((n) => n.kind === 'document');
      const links = S.DOCUMENTS.flatMap((d) => d.links.map(([s, e]) => `${s}::${e}`));
      out({
        corpus: S.DOCUMENTS.length,
        nodes: docs.length,
        ids: new Set(docs.map((n) => n.ref.id)).size,
        finite: docs.every((n) => [n.home.x, n.home.y, n.home.z].every(Number.isFinite)),
        orphanLinks: links.filter((key) => !index.byKey.has(key)),
        unknownMatters: docs.flatMap((n) => n.matters).filter((m) => !index.matters.has(m)),
        farFromHome: docs.filter((n) => n.links.length).filter((n) => {
          const home = index.byKey.get(n.links[0]).home;
          return Math.hypot(home.x - n.home.x, home.z - n.home.z) > 0.09;
        }).map((n) => n.label),
        unfiled: docs.filter((n) => !n.links.length).length,
      });
    """, search="?version=2")
    assert report["corpus"] >= 40
    assert report["nodes"] == report["ids"] == report["corpus"]
    assert report["finite"]
    assert report["orphanLinks"] == []
    assert report["unknownMatters"] == []
    assert report["farFromHome"] == []
    assert report["unfiled"] >= 1


@needs_node
def test_search_reaches_across_the_galaxy():
    found = _boot(_INDEX + """
      const types = (q) => [...new Set(run(q).hits.map((h) => h.node.type))].sort();
      const vollmacht = run('Vollmacht').hits;
      out({
        caseTypes: types('M-2026-041'),
        caseTop: run('M-2026-041').hits[0].node.label,
        vollmachtKinds: [...new Set(vollmacht.map((h) => h.node.kind))],
        vollmachtCount: vollmacht.length,
        vollmachtMatters: new Set(vollmacht.flatMap((h) => h.node.matters)).size,
        mueller: run('Mueller').hits.slice(0, 2).map((h) => h.node.label).sort(),
        court: run('HG260184-O').hits[0].node.label,
        none: run('Kryptowährung'),
        idle: run('   ').mode,
        partial: run('Keller Kryptowährung').mode,
      });
    """)
    assert len(found["caseTypes"]) >= 6 and "Dokumente" in found["caseTypes"]
    assert found["caseTop"] == "Müller Bau AG · Werklohnstreit"
    assert found["vollmachtKinds"] == ["document"]
    assert found["vollmachtCount"] >= 5 and found["vollmachtMatters"] >= 4
    assert found["mueller"] == ["Müller Bau AG", "Müller Bau AG · Werklohnstreit"]
    assert found["court"] == "Handelsgericht Zürich"
    assert found["none"]["mode"] == "none" and found["none"]["hits"] == []
    assert found["idle"] == "idle"
    assert found["partial"] == "partial"


@needs_node
def test_relations_only_join_hits():
    report = _boot(_INDEX + """
      const hits = run('M-2026-041').hits;
      const keys = new Set(hits.map((h) => h.key));
      const edges = S.relations(index, hits);
      out({ edges: edges.length, outside: edges.flat().filter((k) => !keys.has(k)).length,
            unique: new Set(edges.map((e) => [...e].sort().join('|'))).size });
    """)
    assert report["edges"] >= 8
    assert report["outside"] == 0
    assert report["unique"] == report["edges"]


@needs_node
def test_orbit_places_each_hit_once_with_the_strong_ones_inside():
    report = _boot(_INDEX + """
      const box = { cx: 600, cy: 400, rx: 300, ry: 220 };
      const hits = run('M-2026-041').hits;
      const layout = S.orbitLayout(hits, { ...box, maxNodes: 40 });
      const reach = (p) => Math.hypot((p.x - box.cx) / box.rx, (p.y - box.cy) / box.ry);
      const byTier = [0, 1, 2].map((t) => hits.filter((h) => layout.positions[h.key]
        && h.tier === t).map((h) => reach(layout.positions[h.key])));
      const capped = S.orbitLayout(run('Keller').hits, { ...box, maxNodes: 12 });
      out({
        hits: hits.length,
        placed: Object.keys(layout.positions).length,
        hidden: layout.hidden,
        innerMax: Math.max(...byTier[0]),
        outerMin: Math.min(...byTier[2]),
        outside: Object.values(layout.positions).filter((p) => reach(p) > 1.05).length,
        sectorTotal: layout.sectors.reduce((sum, s) => sum + s.count, 0),
        cappedPlaced: Object.keys(capped.positions).length,
        cappedHidden: capped.hidden,
        kellerHits: run('Keller').hits.length,
      });
    """)
    assert report["placed"] + report["hidden"] == report["hits"]
    assert report["innerMax"] < report["outerMin"]
    assert report["outside"] == 0
    assert report["sectorTotal"] == report["placed"]
    assert report["cappedPlaced"] == 12
    assert report["cappedHidden"] == report["kellerHits"] - 12


@needs_node
def test_dossiers_bundle_hits_by_matter():
    report = _boot(_INDEX + """
      const box = { left: 360, top: 96, width: 1200, height: 560 };
      const baumann = S.dossierLayout(index, run('Baumann').hits, box);
      const vollmacht = S.dossierLayout(index, run('Vollmacht').hits, box);
      const keller = run('Keller').hits;
      const kellerLayout = S.dossierLayout(index, keller, box);
      const placedTwice = Object.keys(kellerLayout.positions).length
        !== new Set(Object.keys(kellerLayout.positions)).size;
      out({
        baumannFirst: baumann.columns[0].matter,
        baumannHeader: baumann.columns[0].headerHit,
        vollmachtLast: vollmacht.columns[vollmacht.columns.length - 1].matter,
        kellerAccounted: Object.keys(kellerLayout.positions).length + kellerLayout.hidden === keller.length,
        placedTwice,
        bridgesResolve: kellerLayout.bridges.every((b) => kellerLayout.positions[b.key]
          && kellerLayout.columns[b.column]),
        inside: kellerLayout.columns.every((c) => c.x >= box.left - 0.5
          && c.x + c.width <= box.left + box.width + 0.5),
      });
    """)
    assert report["baumannFirst"] == "M-2025-119"
    assert report["baumannHeader"] == "Mandate::Familie Baumann · Nachlass"
    assert report["vollmachtLast"] is None
    assert report["kellerAccounted"]
    assert not report["placedTwice"]
    assert report["bridgesResolve"]
    assert report["inside"]


@needs_node
def test_example_queries_show_what_they_promise():
    report = _boot(_INDEX + """
      out(S.DEMO_QUERIES.map((example) => ({
        query: example.query, empty: Boolean(example.empty), hits: run(example.query).hits.length })));
    """)
    assert len(report) >= 6
    for example in report:
        assert (example["hits"] == 0) == example["empty"], example


@needs_node
@pytest.mark.parametrize("version", [2, 3, 4])
def test_each_structured_view_starts_from_the_url(version):
    snapshot = _boot("out(window.cortexGalaxyResults.snapshot());",
                     search=f"?version={version}&q=Keller")
    assert snapshot["version"] == version
    assert snapshot["query"] == "Keller"
    assert snapshot["mode"] == "all"
    assert snapshot["hits"] > 0
    assert 0 < snapshot["positioned"] <= snapshot["hits"]
    assert snapshot["groups"]


@needs_node
def test_structured_view_clears_with_the_query():
    snapshot = _boot("""
      window.cortexGalaxyResults.run('');
      out({ snapshot: window.cortexGalaxyResults.snapshot(),
            active: document.body.classList.contains('galaxy-results-active') });
    """, search="?version=3&q=Baumann")
    assert snapshot["snapshot"]["mode"] == "idle"
    assert snapshot["snapshot"]["positioned"] == 0
    assert not snapshot["active"]
