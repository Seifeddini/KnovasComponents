"""Experimente frontend: leftovers of the fix round (frontend group).

The page scripts run in Node's vm with the fake DOM of
test_experiments_frontend (see _run_page there); the tests skip without node.
Each test names the item it pins: the server's has_more for "Mehr laden", the
missing level distribution, the sample size with several comparisons, the
evaluator template's relative difference, the type definition as YAML, the
singular copy of the management page, the orphan count of the index tab and
the metric catalog with the global generic metrics.
"""

from __future__ import annotations

import pytest

from test_experiments_frontend import (DETAIL_EXPORTS, JS, MANAGE_EXPORTS, _detail, _run_node,
                                       _run_page, needs_node)

pytestmark = needs_node


# -- item 1: "Mehr laden" follows the server's has_more ---------------------------


def _search(body):
    return _run_node(body, [JS / "app.js"], suffix="\n;globalThis.__App = DocumentSearchApp;")


_SEARCH_APP = r"""
const mkApp = (limit) => {
  const app = Object.create(__App.prototype);
  Object.assign(app, {
    _searchLimit: limit, _searchLimitBase: limit, _limitQuery: null, currentQuery: 'vertrag',
    searchInput: { value: 'vertrag' }, resultsSection: { style: {} }, resultsContainer: new FakeEl('div'),
    resultsQuery: { textContent: '' }, resultsCount: { textContent: '' }, resultsNotice: null,
    loadMoreButton: { hidden: null }, closePreview() {}, showLoading() {}, hideLoading() {},
    showError(m) { this.error = m; }, createDocumentCard: () => new FakeEl('div'),
  });
  return app;
};
const docs = (n) => Array.from({ length: n }, (_, i) => ({ doc_id: 'd' + i, path: 'd' + i + '.pdf' }));
"""


class TestSearchHasMore:
    def test_display_results_believes_the_server(self):
        """A full page is no sign of more when experiment hits and the
        refinement took rows out, and a short page can still have more."""
        result = _search(_SEARCH_APP + r"""
        const shown = (n, limit, hasMore) => {
          const app = mkApp(limit);
          app.displayResults(docs(n), n, null, hasMore);
          return !app.loadMoreButton.hidden;
        };
        out({
          fullButNoMore: shown(20, 20, false),
          shortButMore: shown(12, 20, true),
          atTheCap: shown(90, 100, true),
          olderServerFull: shown(20, 20, undefined),
          olderServerShort: shown(12, 20, undefined),
        });
        """)
        assert result == {"fullButNoMore": False, "shortButMore": True, "atTheCap": False,
                          "olderServerFull": True, "olderServerShort": False}

    def test_perform_search_hands_has_more_on(self):
        result = _search(_SEARCH_APP + r"""
        const run = async (payload) => {
          globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => payload });
          const app = mkApp(20);
          await app.performSearch();
          return { more: !app.loadMoreButton.hidden, error: app.error || null };
        };
        (async () => {
          out([
            await run({ success: true, results: docs(12), total: 12, has_more: true }),
            await run({ success: true, results: docs(20), total: 20, has_more: false }),
          ]);
        })();
        """)
        assert result == [{"more": True, "error": None}, {"more": False, "error": None}]


# -- item 2: an ordinal or categorical variant without a distribution ------------------


class TestLevelsWithoutDistribution:
    def test_null_levels_say_why_instead_of_a_dash(self):
        result = _detail(r"""
        const m = { key: 'rating', name: 'Bewertung', kind: 'categorical', role: 'primary', direction: 'none',
                    definition: {}, aggregates: [
                      { variant: 'A', n: 60, estimate: null, levels: null },
                      { variant: 'B', n: 4, estimate: null, levels: { gut: 3, schlecht: 1 } }] };
        const rows = __t.metricCard(m).querySelectorAll('tbody tr').map((r) => r.textContent);
        out({ none: __t.levelBars(null, null, 60).textContent,
              noneClass: __t.levelBars(null, null, 60).className,
              empty: __t.levelBars({}, null, 0).textContent,
              missing: __t.levelBars(undefined, null, 0).textContent, rows });
        """, exports=DETAIL_EXPORTS + ("metricCard", "levelBars"))
        assert result["none"] == "Zu viele verschiedene Werte f\u00fcr eine Verteilung"
        assert "kx-muted" in result["noneClass"]
        # No data stays a dash.
        assert result["empty"] == "\u2013" and result["missing"] == "\u2013"
        assert "Zu viele verschiedene Werte" in result["rows"][0]
        assert "Stufe gut" in result["rows"][1] and "Zu viele" not in result["rows"][1]


# -- item 3: sample size with more than two variants ---------------------------------


_SAMPLE_SIZE_SETUP = r"""
window.expOverrides = { status: 'draft', status_phase: null, started_at: null };
window.sampleSizeRoute = (m, u) => {
  if (u.indexOf('/api/experiments/sample-size?') !== 0) return null;
  const q = new URLSearchParams(u.split('?')[1]);
  if (window.refuseMde) {
    return { status: 400, body: { success: false,
      error: '\u00abKleinster relevanter Unterschied\u00bb: Die n\u00f6tige Stichprobe w\u00e4re unrealistisch gross.',
      fields: { mde: 'Die n\u00f6tige Stichprobe w\u00e4re unrealistisch gross.' } } };
  }
  const comparisons = Number(q.get('comparisons') || 1);
  return { body: { success: true, result: { per_variant: 1000, comparisons,
    alpha_used: Number(q.get('alpha') || 0.05) / comparisons } } };
};
routes.push((m, u) => window.sampleSizeRoute(m, u));
"""

_SAMPLE_SIZE_RUN = r"""
const box = document.getElementById('kxSampleSize');
const note = () => box.querySelectorAll('p.kx-help').filter((p) => p.getAttribute('aria-live') === 'polite')[0];
const submit = async (variants) => {
  __t.state.exp = mkExp({ status: 'draft', status_phase: null, started_at: null,
    variants: variants.map((k) => ({ key: k })) });
  box.querySelector('[name="base"]').value = '1,2';
  box.querySelector('[name="mde"]').value = '0,2';
  await box.querySelector('form').dispatch('submit');
  await tick();
  const url = requests.filter((r) => r.url.indexOf('/api/experiments/sample-size?') === 0).pop().url;
  const q = new URLSearchParams(url.split('?')[1]);
  const n = note();
  return { comparisons: q.get('comparisons'), headline: box.querySelector('.kx-headline').textContent,
           note: n ? n.textContent : null, noteHidden: n ? Boolean(n.hidden) : null,
           mdeError: box.querySelector('[data-field="mde"] .kx-field-error').textContent };
};
"""


class TestSampleSizeComparisons:
    def test_each_extra_variant_is_a_comparison_and_the_level_is_shown(self):
        result = _detail(_SAMPLE_SIZE_RUN + r"""
        out([await submit(['A', 'B']), await submit(['A', 'B', 'C']), await submit(['A', 'B', 'C', 'D'])]);
        """, setup=_SAMPLE_SIZE_SETUP, exports=DETAIL_EXPORTS + ("renderSampleSize",))
        two, three, four = result
        assert two["comparisons"] is None and two["noteHidden"] is True and two["note"] == ""
        assert three["comparisons"] == "2"
        assert three["headline"] == "Rund 1'000 je Variante, bei 3 Varianten 3'000 insgesamt."
        assert three["noteHidden"] is False
        assert three["note"] == "Signifikanzniveau je Vergleich: 0,025 (Bonferroni, 2 Vergleiche)"
        assert four["comparisons"] == "3"
        assert four["note"] == "Signifikanzniveau je Vergleich: 0,0167 (Bonferroni, 3 Vergleiche)"

    def test_an_mde_refusal_lands_at_its_field_and_clears_the_level(self):
        result = _detail(_SAMPLE_SIZE_RUN + r"""
        const ok = await submit(['A', 'B', 'C']);
        window.refuseMde = true;
        const refused = await submit(['A', 'B', 'C']);
        out({ ok, refused });
        """, setup=_SAMPLE_SIZE_SETUP, exports=DETAIL_EXPORTS + ("renderSampleSize",))
        assert result["ok"]["noteHidden"] is False
        refused = result["refused"]
        assert refused["mdeError"] == "Die n\u00f6tige Stichprobe w\u00e4re unrealistisch gross."
        assert refused["headline"].startswith("\u00abKleinster relevanter Unterschied\u00bb: ")
        assert refused["noteHidden"] is True and refused["note"] == ""


# -- management page -----------------------------------------------------------------


def _manage(body, setup="", exports=MANAGE_EXPORTS):
    base = r"""
    window.pageData = { canManage: true };
    routes.push((m, u) => (u === '/api/experiments/meta' ? { body: { success: true, meta: {} } } : null));
    """
    return _run_page("experiments_manage.js", body, setup=setup + base, exports=exports)


# -- item 4: the Python evaluator template ---------------------------------------------


def _python_template():
    source = _manage("out(__t.PYTHON_TEMPLATE);", exports=MANAGE_EXPORTS + ("PYTHON_TEMPLATE",))
    namespace = {}
    exec(compile(source, "<PYTHON_TEMPLATE>", "exec"), namespace)  # noqa: S102 - our own template
    return namespace["evaluate"]


def _template_input(control, variant):
    return {
        "experiment": {"key": "MKT-1"},
        "metric": {"key": "m", "name": "Messwert", "kind": "mean", "unit": "Punkte"},
        "variants": [{"key": "A", "is_control": True}, {"key": "B"}],
        "aggregates": [
            {"variant": "A", "n": 10, "estimate": control, "value_sum": control * 10},
            {"variant": "B", "n": 10, "estimate": variant, "value_sum": variant * 10},
        ],
        "rows": [], "rows_truncated": False, "scope": {}, "params": {},
    }


class TestPythonTemplate:
    @pytest.mark.parametrize("control, variant, relative", [
        (2.0, 3.0, 0.5),
        (0.0, 1.0, None),    # no division by zero
        (-2.0, -1.0, None),  # against a negative mean the ratio has the wrong sign
    ])
    def test_relative_only_against_a_positive_baseline(self, control, variant, relative):
        output = _python_template()(_template_input(control, variant))
        (comparison,) = output["comparisons"]
        assert comparison["estimate"] == pytest.approx(variant - control)
        assert comparison["relative"] == (pytest.approx(relative) if relative is not None else None)


# -- item 5: the type definition as YAML ----------------------------------------------


_TYPE_SETUP = r"""
window.typeDef = { states: [{ key: 'draft', label: 'Entwurf' }], initial: 'draft', fields: [] };
window.typeOut = { id: 't1', key: 'hypothesis', name: 'Hypothese', description: '', domain_key: null,
  current_version: 3, definition: typeDef, versions: [],
  definition_yaml: 'fields: []\nstates:\n  - key: draft\n    label: Entwurf\ninitial: draft\n' };
routes.push((m, u) => (m === 'GET' && u === '/api/experiments/types/t1'
  ? { body: { success: true, type: window.typeOut } } : null));
routes.push((m, u) => (m === 'POST' && u === '/api/experiments/types/t1/versions'
  ? { body: { success: true, type: Object.assign({}, window.typeOut, { current_version: window.nextVersion || 3 }) } } : null));
routes.push((m, u) => (m === 'GET' && u.indexOf('/api/experiments/types?') === 0
  ? { body: { success: true, types: [] } } : null));
"""

_TYPE_RUN = r"""
const open = async () => {
  const box = new FakeEl('div'); box.__root = true;
  await __t.openTypeEditor('t1', box, new FakeEl('div'));
  return { box, text: box.querySelector('textarea[name="definition_text"]'),
           name: box.querySelector('input[name="name"]') };
};
const save = async (box) => {
  const button = find(box, 'button', 'Als neue Version speichern');
  await button.dispatch('click', { currentTarget: button });
  await tick();
  return requests.filter((r) => r.method === 'POST').pop().body;
};
"""


class TestTypeDefinitionYaml:
    def test_editor_shows_the_yaml_and_falls_back_to_json(self):
        result = _manage(_TYPE_RUN + r"""
        const yaml = (await open()).text.value;
        delete window.typeOut.definition_yaml;
        const json = (await open()).text.value;
        out({ yaml, json });
        """, setup=_TYPE_SETUP)
        assert result["yaml"] == "fields: []\nstates:\n  - key: draft\n    label: Entwurf\ninitial: draft\n"
        assert result["json"].startswith("{\n  \"states\": [")

    def test_only_a_changed_definition_is_sent(self):
        """A rename must not send the definition again: the server would check
        it anew (and refuse, e.g., a metric archived since)."""
        result = _manage(_TYPE_RUN + r"""
        const bodies = [];
        let e = await open();
        e.name.value = 'Neuer Name';
        bodies.push(await save(e.box));
        e = await open();
        bodies.push(await save(e.box));
        e = await open();
        e.text.value = e.text.value.replace('Entwurf', 'Idee');
        window.nextVersion = 4;
        bodies.push(await save(e.box));
        out({ bodies, toasts });
        """, setup=_TYPE_SETUP)
        renamed, unchanged, edited = result["bodies"]
        assert renamed == {"name": "Neuer Name"}
        assert unchanged == {}
        assert edited == {"definition_text": "fields: []\nstates:\n  - key: draft\n    label: Idee\ninitial: draft\n"}
        assert [t[1] for t in result["toasts"]] == [
            "Gespeichert. Die Definition ist unver\u00e4ndert, es gibt keine neue Version.",
            "Gespeichert. Die Definition ist unver\u00e4ndert, es gibt keine neue Version.",
            "Version 4 gespeichert.",
        ]


# -- items 6 and 7: index tab copy and orphans ---------------------------------------


_INDEX_SETUP = r"""
window.indexOut = { enabled: true, unrestricted: false, access_groups: ['experimente'],
  counts: { indexed: 3 }, jobs: {}, failures: [], access_warnings: [],
  runner: { configured: true, ok: true, busy: 1 } };
routes.push((m, u) => (m === 'GET' && u === '/api/experiments/index'
  ? { body: { success: true, index: window.indexOut } } : null));
routes.push((m, u) => (m === 'POST' && u === '/api/experiments/index/reindex'
  ? { body: { success: true, result: { queued: window.queued } } } : null));
routes.push((m, u) => (u === '/api/experiments/settings'
  ? { body: { success: true, settings: { show_in_search: true } } } : null));
"""

_INDEX_RUN = r"""
const box = document.getElementById('kxPanel-index');
const render = async (over) => {
  Object.assign(window.indexOut, over || {});
  await __t.renderIndexTab();
  await tick();
  return box.textContent;
};
"""


class TestIndexTab:
    def test_singular_forms(self):
        result = _manage(_INDEX_RUN + r"""
        const texts = [await render({ runner: { configured: true, ok: true, busy: 1 } }),
                       await render({ runner: { configured: true, ok: true, busy: 2 } })];
        const reindex = async (n) => {
          window.queued = n;
          const button = find(box, 'button', 'Alles neu indexieren');
          await button.dispatch('click', { currentTarget: button });
          await tick();
          return toasts.pop()[1];
        };
        out({ texts, toasts: [await reindex(1), await reindex(2)] });
        """, setup=_INDEX_SETUP, exports=MANAGE_EXPORTS + ("renderIndexTab",))
        one, two = result["texts"]
        assert "Erreichbar, 1 Auftrag l\u00e4uft." in one
        assert "Erreichbar, 2 Auftr\u00e4ge laufen." in two
        assert result["toasts"] == ["1 Experiment zur \u00dcbertragung eingeplant.",
                                    "2 Experimente zur \u00dcbertragung eingeplant."]

    def test_orphans_are_named_only_when_there_are_some(self):
        result = _manage(_INDEX_RUN + r"""
        out([await render({ orphans: 1234 }), await render({ orphans: 0 }), await render({ orphans: undefined })]);
        """, setup=_INDEX_SETUP, exports=MANAGE_EXPORTS + ("renderIndexTab",))
        some, none, absent = result
        assert "Gel\u00f6schte Experimente noch in Knovas: 1'234 \u2013 die Wartung l\u00f6scht sie erneut." in some
        assert "Gel\u00f6schte Experimente" not in none
        assert "Gel\u00f6schte Experimente" not in absent


# -- item 8: the global generic metrics fill the catalog of a new domain ------------------


class TestGenericMetricsCatalog:
    def test_global_metrics_open_the_editor_without_the_empty_hint(self):
        """Guard: a new domain has no metrics of its own but sees the five
        global ones of the core pack, so the editor offers them and the hint
        for an empty catalog stays away."""
        setup = r"""
        window.pageData = { experimentKey: 'MKT-1', canManage: false };
        const g = (key, name, kind) => ({ key, name, kind, domain_key: null, unit: '', archived: false });
        window.catalog = [g('generic_success_rate', 'Erfolgsquote', 'proportion'),
                          g('generic_events_per_period', 'Ereignisse je Zeitraum', 'count'),
                          g('generic_duration_s', 'Dauer in Sekunden', 'duration'),
                          g('generic_score', 'Messwert', 'mean'), g('generic_rating', 'Bewertung 1\u20135', 'ordinal')];
        """
        result = _detail(r"""
        await __t.openMetricsEditor();
        const box = document.getElementById('kxMetrics');
        find(box, 'button', 'Metrik hinzuf\u00fcgen').dispatch('click');
        await tick();
        const select = box.querySelector('tbody select');
        out({ text: box.textContent, options: select ? select.querySelectorAll('option').map((o) => o.value) : null,
              editing: __t.state.editors.has('metrics') });
        """, setup=setup)
        assert "noch keine Metriken" not in result["text"]
        assert result["editing"] is True
        assert result["options"] == ["", "generic_rating", "generic_duration_s", "generic_events_per_period",
                                     "generic_success_rate", "generic_score"]
