"""Ansichten: the page's scripts, run in Node with the stub DOM of the
experiments tests. No markup sinks, chips drawn as one element, stable styles.
"""
from __future__ import annotations

import subprocess
from urllib.parse import unquote

import pytest

from test_experiments_frontend import JS, NODE, TEMPLATES, _run_node, needs_node

VIEW_JS = ("views_core.js", "views_graph.js", "views_definitions.js", "views_change.js", "views_process.js", "views_spine.js", "views_discover.js", "views_gaps.js", "views_docs.js", "views_saved.js", "views.js")
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


# -- Step 7: fix in place, gaps, review ----------------------------------------------------------

_EDIT_OPEN = {"id": "FeaturePlan serves Capability", "known": True, "editable": True, "routable": True, "can_add": True}
_EDIT_LOCK = {"id": "Commit implements FeaturePlan", "known": True, "editable": False, "routable": False,
              "reason": "from git", "fix_at": "name the plan in the commit message"}


def _art(type_, edit, items=()):
    return {"kind": "requires", "type": type_, "path": "→serves→ " + type_, "required": True, "min_count": 1,
            "count": len(items), "missing": not items, "verified": True, "items": list(items), "chip": None, "edit": edit}


def _texts(out):
    return [unquote(x.split(":", 1)[1]) for x in out if ":" in x]


def _buttons(out):
    return [unquote(x.split(":", 1)[1]) for x in out if x.startswith("BUTTON:")]


@needs_node
def test_trace_offers_fixes_where_the_edit_map_allows_and_a_lock_elsewhere():
    import json

    lane = {"id": "brief", "name": "brief", "order": 1, "state": "current", "entered": None, "description": "",
            "exit_criteria": [], "artefacts": [_art("Capability", _EDIT_OPEN, [{"id": "c", "name": "CAP-11", "type": "Capability"}]),
                                               _art("Commit", _EDIT_LOCK)]}
    trace = {"instance": {"id": "x", "name": "X", "key": "K"}, "process": {"key": "P", "name": "P"},
             "current": {"step": "brief", "since": "2026-10-20", "days": 7}, "missing": [], "unmet": [], "context": [],
             "changeable": True, "lanes": [lane],
             "pending": [{"pr_number": 301, "pr_url": "https://x/301", "state": "submitted",
                          "ops": [{"edit_id": "FeaturePlan serves Capability"}]}]}
    out = _run_node(_walk_js() + """
        const box = document.createElement('div');
        const page = {typeLabel: (t) => t, overrides: {}, isHidden: () => false, openDossier() {}, openTraceItems() {}, center() {},
                      changes: {pendingIn: () => [{}]}};
        window.ViewsProcess.renderTrace(box, TRACE, page);
        walk(box);
        out(found);
    """.replace("TRACE", json.dumps(trace)), [JS / "views_core.js", JS / "views_change.js", JS / "views_process.js"])
    texts, buttons = _texts(out), _buttons(out)
    assert "Bearbeiten" in buttons and buttons.count("+ hinzufügen") == 1     # not on the locked Commit
    assert "falsch / entfernen" in buttons and "ersetzen" in buttons
    assert any("from git" in t and "commit message" in t for t in texts)
    assert any(t.startswith("Änderung ausstehend: PR #301") for t in texts)
    assert "im Änderungssatz (1)" in texts


@needs_node
def test_a_trace_without_accounts_offers_no_fixes():
    import json

    lane = {"id": "brief", "name": "brief", "order": 1, "state": "current", "entered": None, "description": "",
            "exit_criteria": [], "artefacts": [_art("Capability", _EDIT_OPEN)]}
    trace = {"instance": {"id": "x", "name": "X", "key": "K"}, "process": {"key": "P", "name": "P"},
             "current": {"step": "brief"}, "missing": [], "unmet": [], "context": [], "changeable": False, "lanes": [lane]}
    out = _run_node(_walk_js() + """
        const box = document.createElement('div');
        const page = {typeLabel: (t) => t, overrides: {}, isHidden: () => false, openDossier() {}, openTraceItems() {}, center() {}};
        window.ViewsProcess.renderTrace(box, TRACE, page);
        walk(box);
        out(found);
    """.replace("TRACE", json.dumps(trace)), [JS / "views_core.js", JS / "views_change.js", JS / "views_process.js"])
    assert _buttons(out) == ["Im Graph zeigen"]


@needs_node
def test_gaps_rows_preselect_the_top_suggestion_and_lock_what_cannot_be_fixed_here():
    import json

    rows = [
        {"instance": "a", "key": "FEAT-1", "name": "Eins", "process": "P", "step": "brief", "owner": None, "kind": "missing",
         "type": "Goal", "path": "→motivated_by→ Goal", "why": "Fehlt: 0 von 1", "where": "brief", "position": 3,
         "edit": _EDIT_OPEN, "candidates": [{"target": "GOAL-1", "score": 3, "rule": "r", "reason": "motivates a sibling"}],
         "no_rule": False},
        {"instance": "a", "key": "FEAT-1", "name": "Eins", "process": "P", "step": "brief", "owner": None, "kind": "missing",
         "type": "Commit", "path": "←implements– Commit", "why": "Fehlt", "where": "brief", "position": 4,
         "edit": _EDIT_LOCK, "candidates": [], "no_rule": True},
        {"instance": "b", "key": "FEAT-2", "name": "Zwei", "process": "P", "step": "approved", "owner": "ROLE-X", "kind": "rule",
         "rule": "change-class", "type": None, "path": None, "why": "substantive change", "edit": None, "candidates": [],
         "no_rule": True}]
    body = {"rows": rows, "total": 3, "suggestions_as_of": "2026-10-28", "kb_report_as_of": "2026-10-28",
            "filters": {"processes": [{"key": "P", "name": "P"}], "steps": ["brief"], "types": ["Goal"], "owners": []}}
    out = _run_node(_walk_js() + """
        const box = document.createElement('div');
        const page = {state: {gapProcess: 'P', gapStep: '', gapType: '', gapOwner: ''}, typeLabel: (t) => t, changes: {}};
        window.ViewsGaps.render(box, BODY, page);
        walk(box);
        out(found);
    """.replace("BODY", json.dumps(body)), [JS / "views_core.js", JS / "views_change.js", JS / "views_gaps.js"])
    texts, buttons = _texts(out), _buttons(out)
    assert buttons.count("Übernehmen") == 1 and "Vorschläge übernehmen (ausgewählte)" in buttons
    assert "GOAL-1 (Punkte 3)" in texts and "motivates a sibling" in texts
    assert any("from git" in t for t in texts) and any("KB-Regelbericht" in t for t in texts)
    assert "NULL" not in out


@needs_node
def test_the_review_names_conflicts_rollback_and_why_submit_waits():
    import json

    review = {"success": True, "base": {"branch": "master", "commit": "a" * 40}, "head": "b" * 40, "behind": True,
              "ops": [{"id": "o1", "instance": "x", "kind": "add", "edit_id": "FeaturePlan serves Capability",
                       "target": "CAP-02", "class": "substantive", "ok": True, "permitted": True,
                       "suggestion": {"rule": "realised-by-affected", "score": 3, "reason": "realised by L2-KG"}}],
              "files": [{"path": "docs/x.md", "diff": "+status: draft", "new": False, "ops": ["o1", "rollback:FEAT-1"], "blob": "c"}],
              "instances": [{"node": "x", "key": "FEAT-1", "name": "Eins", "class": "substantive", "rules": ["R1"],
                             "rollback": {"from_step": "approved", "to_step": "brief", "status": "draft"},
                             "decision": "docs/decisions/DEC-0018-x.md",
                             "impact": {"components": [1], "invariants": [], "risks": [], "decisions": []}}],
              "conflicts": [{"path": "docs/x.md", "diff": "-old"}], "missing_reasons": ["FEAT-1"],
              "ready": False, "permitted": True, "can_write": False}
    out = _run_node(_walk_js() + """
        window.fetch = async () => ({status: 200, ok: true, json: async () => (REVIEW)});
        globalThis.fetch = window.fetch;
        const box = document.createElement('div');
        const page = {changes: {data: null, badge() {}, drop() {}, discard() {}}, reload() {}};
        window.ViewsChange.renderReview(box, {success: true, count: 1, change_set: {reasons: {}, ops: []}}, page).then(() => {
            walk(box);
            out(found);
        });
    """.replace("REVIEW", json.dumps(review)), [JS / "views_core.js", JS / "views_change.js"])
    texts, buttons = _texts(out), _buttons(out)
    assert any(t.startswith("Konflikt") for t in texts) and "Neu aufsetzen (Rebase)" in buttons
    assert any("Status zurück auf „draft“" in t for t in texts)
    assert any(t.startswith("Vorschlag realised-by-affected (Punkte 3)") for t in texts)
    assert any("Begründung fehlt für FEAT-1" in t and "kein GitHub-Zugang" in t for t in texts)
    assert "Pull Request erstellen" in buttons


# -- Step 8: documentation pages --------------------------------------------------------------------

_DIAGRAM = {"kind": "level", "width": 550, "height": 230, "box_w": 230, "box_h": 72, "note": "n",
            "boxes": [{"id": "a", "key": "L2-A", "name": "Alpha", "type": "Component", "description": "Does alpha things.",
                       "x": 0, "y": 0, "incomplete": ["responsibilities"], "stale": []},
                      {"id": "b", "key": "L2-B", "name": "Beta", "type": "Component", "description": "", "x": 320, "y": 150,
                       "incomplete": [], "stale": ["explanation"]}],
            "edges": [{"from": "a", "to": "b", "relation": "calls", "label": "calls"},
                      {"from": "b", "to": "a", "relation": "calls_back", "label": "calls_back"}]}


@needs_node
def test_the_diagram_draws_boxes_arrows_and_labels():
    import json

    out = _run_node("""
        const box = window.ViewsDocs.drawDiagram(DIAGRAM, () => {});
        const svg = box.children[0];
        const kinds = svg.children.map((c) => c.tagName + ':' + (c.className || ''));
        const texts = [];
        const walk = (n) => (n.children || []).forEach((c) => { if (String(c.tagName).toLowerCase() === "text") texts.push(c.textContent); walk(c); });
        walk(svg);
        out({kinds, texts});
    """.replace("DIAGRAM", json.dumps(_DIAGRAM)), [JS / "views_core.js", JS / "views_docs.js"])
    kinds = out["kinds"]
    assert kinds.count("PATH:views-docs-edge") == 2
    assert "G:views-docs-box views-docs-box-incomplete" in kinds and "G:views-docs-box views-docs-box-stale" in kinds
    assert {"L2-A", "Alpha", "Does alpha things.", "calls", "calls_back"} <= set(out["texts"])


@needs_node
def test_the_wrap_never_doubles_an_ellipsis():
    out = _run_node("""
        out(window.ViewsDocs.wrap('Data: PostgreSQL, Weaviate, the knowledge graph store and their schemas…', 30));
    """, [JS / "views_core.js", JS / "views_docs.js"])
    assert len(out) == 2 and out[1].endswith("…") and not out[1].endswith("……")


@needs_node
def test_a_section_shows_missing_stale_deferred_and_source_links():
    import json

    sections = [
        {"id": "responsibilities", "title": "Verantwortlichkeiten", "render": "list", "required": True, "columns": [],
         "column_labels": [], "source": "spec.ai.yaml", "as_of": "2026-10-28T21:00:00", "empty": True, "count": 0,
         "missing": "Eintragen in spec.ai.yaml · responsibilities[]", "items": []},
        {"id": "explanation", "title": "Erläuterung", "render": "list", "required": True, "columns": ["kind"],
         "column_labels": ["Art"], "source": "the document", "as_of": "2026-10-28T21:00:00", "empty": False, "count": 1,
         "stale": {"source": "KB-Bericht docs-completeness", "as_of": "2026-10-28T13:25:11Z"}, "deferred": False,
         "items": [{"id": "r", "name": "README.md", "type": "Record", "cells": {"kind": "explanation"},
                    "url": "https://github.com/x/blob/master/README.md", "path": "README.md"}]},
        {"id": "commits", "title": "Letzte Commits", "render": "table", "required": False, "columns": ["committed_on"],
         "column_labels": ["Datum"], "source": "git", "as_of": "2026-10-28T21:00:00", "empty": False, "count": 10, "total": 48,
         "deferred": True, "deferred_reads": 48, "items": [{"id": "c", "name": "abc", "type": "Commit", "cells": {}}]},
    ]
    out = _run_node(_walk_js() + """
        const docs = new window.ViewsDocs.Docs({openDossier() {}, center() {}});
        const box = document.createElement('div');
        SECTIONS.forEach((s) => box.appendChild(docs.drawSection(s, {node: {id: 'n'}})));
        walk(box);
        const links = [];
        const findA = (n) => (n.children || []).forEach((c) => { if (c.tagName === 'A' || c.tagName === 'a') links.push([c.attrs.href, c.attrs.target]); findA(c); });
        findA(box);
        out({found, links});
    """.replace("SECTIONS", json.dumps(sections)), [JS / "views_core.js", JS / "views_docs.js"])
    texts = _texts(out["found"])
    assert any(t.startswith("fehlt — Eintragen in spec.ai.yaml") for t in texts)
    assert "veraltet" in texts and any("veraltet laut KB-Bericht vom 2026-10-28T13:25:11Z" in t for t in texts)
    assert "Laden (48 Lesezugriffe)" in _buttons(out["found"])
    assert ["https://github.com/x/blob/master/README.md", "_blank"] in out["links"]
