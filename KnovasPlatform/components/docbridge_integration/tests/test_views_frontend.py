"""Ansichten: the page's scripts, run in Node with the stub DOM of the
experiments tests. No markup sinks, chips drawn as one element, stable styles.
"""
from __future__ import annotations

import subprocess
from urllib.parse import unquote

import pytest

from test_experiments_frontend import JS, NODE, TEMPLATES, _run_node, needs_node

VIEW_JS = ("views_core.js", "views_graph.js", "views_definitions.js", "views_change.js", "views_process.js", "views_spine.js", "views_discover.js", "views_saved.js", "views.js")
_MARKUP_SINKS = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write")


@needs_node
@pytest.mark.parametrize("name", VIEW_JS)
def test_scripts_parse(name):
    proc = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("name", VIEW_JS)
def test_no_markup_sinks(name):
    source = (JS / name).read_text(encoding="utf-8")
    for sink in _MARKUP_SINKS:
        assert sink not in source, f"{name} uses {sink}"


@pytest.mark.parametrize("name", VIEW_JS)
def test_nothing_from_the_brain_is_stored_in_the_browser(name):
    source = (JS / name).read_text(encoding="utf-8")
    for store in ("localStorage", "sessionStorage", "indexedDB"):
        assert store not in source, f"{name} uses {store}"


def test_page_loads_core_before_the_kinds_and_the_controller_last():
    page = (TEMPLATES / "views.html").read_text(encoding="utf-8")
    order = [page.index(f"js/{name}") for name in ("vendor/cytoscape.min.js",) + VIEW_JS]
    assert order == sorted(order)


def test_cortex_dock_links_the_views():
    assert 'href="/views"' in (TEMPLATES / "ontology.html").read_text(encoding="utf-8")


ANSWER = {
    "root": {"id": "c", "name": "Knowledge Graph", "type": "Component", "meta": False},
    "nodes": [{"id": "p", "name": "Plan", "type": "FeaturePlan", "relation": "affects",
               "direction": "in", "meta": False},
              {"id": "i", "name": "GI-1", "type": "Invariant", "relation": "keeps",
               "direction": "out", "meta": False}],
    "chips": [{"key": "in:verifies:AlloyCheck", "relation": "verifies", "relation_label": "verifiziert",
               "direction": "in", "type": "AlloyCheck", "count": 22,
               "breakdown": [{"status": "passed", "count": 20}, {"status": "failed", "count": 2}]}],
    "legend": [{"name": "AlloyCheck", "label": "Alloy-Prüfung"}],
}


@needs_node
def test_elements_draw_a_chip_as_one_node_with_its_breakdown():
    out = _run_node(f"""
        const els = window.ViewsGraph.elements({__import__('json').dumps(ANSWER)});
        out(els.map((e) => ({{group: e.group, kind: e.data.kind || null, label: encodeURIComponent(e.data.label),
                              source: e.data.source || null, target: e.data.target || null}})));
    """, [JS / "views_core.js", JS / "views_graph.js"])
    nodes = [e for e in out if e["group"] == "nodes"]
    assert [n["kind"] for n in nodes] == ["root", "node", "node", "chip"]
    assert unquote(nodes[-1]["label"]) == "22 × Alloy-Prüfung: 20 passed, 2 failed"
    edges = [e for e in out if e["group"] == "edges"]
    # in: the edge points at the root; out: away from it, as stored in the brain
    affects = next(e for e in edges if unquote(e["label"]) == "affects")
    assert affects["target"] == "c"
    keeps = next(e for e in edges if unquote(e["label"]) == "keeps")
    assert keeps["source"] == "c"


@needs_node
def test_type_style_is_stable_and_overridable():
    out = _run_node("""
        const a = window.ViewsCore.typeStyle('Component');
        const b = window.ViewsCore.typeStyle('Component');
        const c = window.ViewsCore.typeStyle('Component', {Component: {color: '#000000'}});
        out([a.color === b.color && a.shape === b.shape, c.color, c.shape === a.shape]);
    """, [JS / "views_core.js"])
    assert out == [True, "#000000", True]


@needs_node
def test_chip_label_names_nodes_without_a_status():
    out = _run_node("""
        out(encodeURIComponent(window.ViewsCore.chipLabel({count: 8, type: 'Commit',
            breakdown: [{status: null, count: 8}]}, 'Commit')));
    """, [JS / "views_core.js"])
    assert unquote(out) == "8 × Commit"


@needs_node
def test_definitions_render_without_messages_appends_no_empty_node():
    """An empty error list once appended null, which a real DOM refuses."""
    out = _run_node("""
        const box = document.createElement('div');
        window.ViewsDefinitions.render(box, {source: 'tenant', errors: [], gaps: [], differences: [],
            processes: [{key: 'PROC-x', name: 'X', instance_types: ['T'], context: [],
                         steps: [{id: 'a', name: 'a', order: 1, requires: [], produces: [], exit_criteria: []}]}],
            spines: [{key: 'SPINE-x', name: 'S', levels: [{id: 'L1', name: 'L', types: ['T']}]}]});
        const nulls = [];
        const walk = (n) => (n.children || []).forEach((c) => { if (!c) nulls.push(1); else walk(c); });
        walk(box);
        out([nulls.length, box.children[0].children.length]);
    """, [JS / "views_core.js", JS / "views_definitions.js"])
    assert out[0] == 0 and out[1] > 0


def test_definitions_path_text_follows_direction():
    source = (JS / "views_definitions.js").read_text(encoding="utf-8")
    assert "h.direction === 'out'" in source


@needs_node
def test_trace_flags_only_the_current_lane():
    lane = lambda state: {"id": state, "name": state, "order": 1, "state": state, "entered": None,
                          "description": "", "exit_criteria": [],
                          "artefacts": [{"type": "Commit", "path": "←implements– Commit", "required": True,
                                         "min_count": 1, "count": 0, "missing": True, "items": [], "chip": None}]}
    out = _run_node("""
        const box = document.createElement('div');
        const page = {typeLabel: (t) => t, overrides: {}, isHidden: () => false, openDossier() {}, openTraceItems() {}, center() {}};
        window.ViewsProcess.renderTrace(box, {instance: {id: 'x', name: 'X', key: 'K'}, process: {name: 'P'},
            current: {step: 'b', since: '2026-10-20', days: 7}, missing: [{}], unmet: [], context: [],
            lanes: [LANE_A, LANE_B]}, page);
        const flagged = [];
        const walk = (n) => (n.children || []).forEach((c) => { if (c && c.className) {
            if (String(c.className).includes('views-art-missing')) flagged.push(1); walk(c); } });
        walk(box);
        out(flagged.length);
    """.replace("LANE_A", __import__("json").dumps(lane("done"))).replace("LANE_B", __import__("json").dumps(lane("current"))),
        [JS / "views_core.js", JS / "views_process.js"])
    assert out == 1


@needs_node
def test_matrix_draws_one_cell_per_pair_and_marks_links():
    out = _run_node("""
        const box = document.createElement('div');
        const page = {overrides: {}, openDossier() {}};
        window.ViewsSpine.renderMatrix(box, {spine: {name: 'S'}, rows_level: {name: 'R'}, cols_level: {name: 'C'},
            rows: [{id: 'r1', name: 'R1', key: 'CAP-01', status: 'live'}, {id: 'r2', name: 'R2', key: 'CAP-02', status: null}],
            cols: [{id: 'c1', name: 'C1', key: 'L2-A'}, {id: 'c2', name: 'C2', key: 'L2-B'}, {id: 'c3', name: 'C3', key: 'L2-C'}],
            cells: [[{linked: true, status: 'live'}, {linked: false}, {linked: true, status: 'live'}],
                    [{linked: false}, {linked: false}, {linked: false}]],
            unlinked_rows: ['r2']}, page);
        const cells = [];
        const walk = (n) => (n.children || []).forEach((c) => { if (!c) return;
            if (c.tagName === 'TD') cells.push(String(c.className)); walk(c); });
        walk(box);
        out(cells);
    """, [JS / "views_core.js", JS / "views_spine.js"])
    assert len(out) == 6 and out.count("views-matrix-on") == 2


def _walk_js():
    return """
        const found = [];
        const walk = (n) => (n.children || []).forEach((c) => { if (!c) { found.push('NULL'); return; }
            if (c.tagName) found.push(c.tagName + ':' + encodeURIComponent(c.textContent || '')); walk(c); });
    """


@needs_node
def test_list_rows_offer_the_actions_that_apply():
    out = _run_node(_walk_js() + """
        const box = document.createElement('div');
        const page = {state: {listFilters: {owner: '', status: '', step: ''}, node: 'b'}, overrides: {},
                      select() {}, openDossier() {}, reload() {}};
        window.ViewsDiscover.renderList(box, {type: {label: 'Feature-Plan', description: ''}, total: 2, page: 0, pages: 1,
            complete_facts: true, in_spine: false, filters: {owners: ['Siran'], statuses: [], steps: []},
            rows: [{id: 'a', key: 'FEAT-1', name: 'Eins', status: 'approved', owner: 'Siran', process: 'PROC-x', step: 'approved', degree: 3},
                   {id: 'b', key: 'FEAT-2', name: 'Zwei', status: null, owner: null, process: null, step: null, degree: 0}]}, page);
        walk(box);
        out(found);
    """, [JS / "views_core.js", JS / "views_discover.js"])
    from urllib.parse import unquote
    buttons = [unquote(x.split(":", 1)[1]) for x in out if x.startswith("BUTTON:")]
    assert "NULL" not in out
    assert buttons.count("Ablauf") == 1 and buttons.count("Graph") == 2 and "Baum" not in buttons


@needs_node
def test_start_panel_draws_one_tile_per_type():
    out = _run_node(_walk_js() + """
        const box = document.createElement('div');
        const page = {overrides: {}, openList() {}, select() {}};
        window.ViewsDiscover.renderStart(box, {groups: [{spine: 'SPINE-product', name: 'Product spine',
            tiles: [{name: 'Product', label: 'Produkt', count: 2, description: 'x'}, {name: 'Capability', label: 'Fähigkeit', count: 13}]}]},
            {groups: [{id: 'mine', label: 'Meine offenen Pläne', items: []}]}, page);
        walk(box);
        out(found.filter((x) => x.startsWith('BUTTON:')).length);
    """, [JS / "views_core.js", JS / "views_discover.js"])
    assert out == 2


def test_every_method_the_controller_calls_is_defined():
    """A removed block once took bindSpineControls with it and the page never initialised."""
    import re

    source = (JS / "views.js").read_text(encoding="utf-8")
    called = set(re.findall(r"\bthis\.([a-zA-Z_]\w*)\(", source))
    defined = set(re.findall(r"^\s{8}(?:async\s+)?([a-zA-Z_]\w*)\s*\([^)]*\)\s*\{", source, re.M))
    builtins = {"$"}
    assert called - defined - builtins == set()


@needs_node
def test_change_form_round_trips_keys_and_risks():
    out = _run_node("""
        const C = window.ViewsChange;
        const keys = {kind: 'keys'}, risks = {kind: 'risks'};
        out([C.fromText(keys, C.toText(keys, ['CAP-05', 'GOAL-2026-Q4-x'])),
             C.fromText(risks, C.toText(risks, [{id: 'RISK-01', risk: 'Edges lost', mitigation: 'dual write'}])),
             C.fromText(keys, 'L2-A,  L2-B L2-C')]);
    """, [JS / "views_core.js", JS / "views_change.js"])
    assert out == [["CAP-05", "GOAL-2026-Q4-x"], [{"id": "RISK-01", "risk": "Edges lost", "mitigation": "dual write"}],
                   ["L2-A", "L2-B", "L2-C"]]
