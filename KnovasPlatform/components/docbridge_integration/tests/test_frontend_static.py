"""Statische Prüfungen an app.js.

Die Datei wird von keinem Python-Test ausgeführt, und der Browser meldet
nichts: eine zweite Methode gleichen Namens im selben Klassenkörper ist
gültiges JavaScript. Die spätere gewinnt, die frühere ist tot. Genau so ist
die satzweise Markierung der Trefferstellen einmal unbemerkt ausgefallen --
die neue Fassung stand oben, eine vergessene alte weiter unten, und im
Dokument wurde wieder der ganze Absatz markiert.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "src/web_interface/static/js/app.js"

# Methodenkopf auf Klassenebene: genau vier Leerzeichen Einrückung, Name,
# Klammer. Schließt `if (...)`/`for (...)` aus (Schlüsselwörter) und alles
# tiefer Eingerückte (verschachtelte Funktionen, Objektliterale).
_METHOD = re.compile(r"^    (?:static\s+)?(?:async\s+)?([A-Za-z_$][\w$]*)\s*\(", re.MULTILINE)
_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "function", "constructor"}


def _method_names() -> list[str]:
    source = APP_JS.read_text(encoding="utf-8")
    return [name for name in _METHOD.findall(source) if name not in _KEYWORDS]


def test_app_js_defines_every_method_once():
    duplicates = sorted(n for n, count in Counter(_method_names()).items() if count > 1)
    assert not duplicates, (
        "Diese Methoden sind in app.js mehrfach definiert; die letzte Fassung "
        f"überschreibt die vorherigen stillschweigend: {duplicates}"
    )


# ---------------------------------------------------------------------------
# Dokumentfelder (doc_fields.js, und was app.js dafuer tut). Spec 4.5, 6.
# ---------------------------------------------------------------------------

import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

STATIC_JS = APP_JS.parent
DOC_FIELDS_JS = STATIC_JS / "doc_fields.js"
INDEX_HTML = APP_JS.parents[2] / "templates" / "index.html"

# Writing server data as markup. doc_fields.js uses textContent only.
_MARKUP_SINKS = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write")


def _source(path):
    return path.read_text(encoding="utf-8")


def _code(text):
    """``text`` without comments: what the browser executes. Comments may
    name what the code avoids."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"^\s*//[^\n]*", "", text, flags=re.MULTILINE)


def _method_body(source, name):
    """The text of one class method, from its head to the next method head."""
    heads = list(_METHOD.finditer(source))
    for i, head in enumerate(heads):
        if head.group(1) == name:
            end = heads[i + 1].start() if i + 1 < len(heads) else len(source)
            return source[head.start():end]
    raise AssertionError(f"method {name} not found")


def test_doc_fields_js_defines_every_method_once():
    names = [n for n in _METHOD.findall(_source(DOC_FIELDS_JS)) if n not in _KEYWORDS]
    duplicates = sorted(n for n, count in Counter(names).items() if count > 1)
    assert not duplicates, duplicates


def test_doc_fields_js_never_writes_markup():
    source = _code(_source(DOC_FIELDS_JS))
    for sink in _MARKUP_SINKS:
        assert sink not in source, f"doc_fields.js uses {sink}"


def test_app_js_renders_field_data_without_markup():
    """The new card chips, the reasoned empty state, the listing and the
    refusal box put server data in with textContent only."""
    source = _source(APP_JS)
    for name in ("_appendFieldChips", "_showReasonedEmptyState", "displayListing",
                 "displayRefusal", "_renderSearchNotices"):
        body = _code(_method_body(source, name))
        for sink in _MARKUP_SINKS:
            assert sink not in body, f"{name} uses {sink}"
    # The card title is set through textContent after the template.
    card = _method_body(source, "createDocumentCard")
    assert "titleEl.textContent = title" in card


def test_doc_fields_js_builds_no_url_from_data():
    """Names, values and pointers travel in JSON bodies only (D6): every
    request goes to a fixed path."""
    source = _code(_source(DOC_FIELDS_JS))
    calls = re.findall(r"fetch\(\s*([^,)]+)", source)
    assert calls, "doc_fields.js makes requests"
    allowed = {"'/api/doc-fields'", "'/api/doc-fields/entities'", "'/api/documents/find'",
               "'/api/document-fields/read'", "'/api/document-fields/edit'"}
    assert set(call.strip() for call in calls) <= allowed, calls
    # No query string assembled anywhere, and the Cortex handoff is not a URL.
    assert "encodeURIComponent" not in source and "URLSearchParams" not in source
    assert "?" + "list" not in source


def test_doc_fields_posts_carry_the_csrf_header():
    source = _source(DOC_FIELDS_JS)
    posts = source.count("method: 'POST'")
    assert posts == source.count("headers: this.app._jsonHeadersWithCsrf()") == 4


def test_search_limit_never_exceeds_fifty():
    source = _source(APP_JS)
    assert "static SEARCH_LIMIT_MAX = 50;" in source
    assert "Math.min(100" not in source and "< 100" not in source


def _js_object(source, name):
    start = source.index(f"static {name} = {{")
    body = source[source.index("{", start):source.index("};", start) + 1]
    pairs = re.findall(r"(\w+):\s*'([^']*)'", body)
    return dict(pairs)


def test_empty_state_texts_match_the_python_view():
    """H8: the browser says exactly what doc_fields_view says."""
    import doc_fields_view as dfv

    source = _source(APP_JS)
    assert _js_object(source, "NO_RESULTS_TEXTS") == dfv._NO_RESULTS
    assert f"static NO_RESULTS_GENERIC = '{dfv.NO_RESULTS_GENERIC}';" in source
    assert "eingeschr\u00e4nkte Suchqualit\u00e4t" in source


def test_no_listing_notice_is_computed_in_the_browser():
    """H5: the notice text comes from the server response only."""
    import doc_fields_view as dfv

    for path in (APP_JS, DOC_FIELDS_JS):
        assert dfv.INCOMPLETE_LISTING not in _code(_source(path))
        assert "Liste unvollst" not in _code(_source(path))
    assert "notice.incomplete ? notice.text" in _source(DOC_FIELDS_JS)


def test_where_goes_out_only_under_filters():
    """H1 in the browser: searchWhere answers only for the capability
    'filters'; the server enforces it again."""
    source = _source(DOC_FIELDS_JS)
    body = _method_body(source, "searchWhere")
    assert "this.showsFilters ? this.collectWhere() : null" in body
    assert "static FILTER_CAPS = ['filters'];" in source
    assert "where: state.where" in source


def test_index_loads_doc_fields_after_app_and_has_its_containers():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert html.index("js/app.js") < html.index("js/doc_fields.js")
    for element_id in ("docFieldsRail", "docFieldsUnderstood", "docFieldsBanner",
                       "docFieldsNotice", "previewFieldsSection", "previewFields",
                       "showListButton", "resultsHeading", "searchNotices"):
        assert f'id="{element_id}"' in html, element_id
    # Gated UI ships hidden; doc_fields.js shows it only for its capability.
    assert 'id="docFieldsRail" hidden' in html
    assert 'id="previewFieldsSection" hidden' in html
    assert 'id="searchNotices" hidden' in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("path", [APP_JS, DOC_FIELDS_JS], ids=lambda p: p.name)
def test_javascript_parses(path):
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_python_texts_the_page_relies_on_are_ascii_escaped_in_python():
    """The Python modules keep umlauts as escapes (scripts/check_ascii_py.py);
    the JSON the browser gets carries the real characters."""
    import doc_fields_view as dfv

    assert json.loads(json.dumps(dfv.TITLE_NOT_SEARCHABLE)) == "Titel wird angezeigt, nicht durchsucht"


ONTOLOGY_JS = STATIC_JS / "ontology.js"


def test_cortex_link_hands_over_without_a_url():
    """The name goes through sessionStorage; the page opens /?list=1 only."""
    body = _code(_method_body(_source(ONTOLOGY_JS), "renderDocFieldLinks"))
    for sink in _MARKUP_SINKS:
        assert sink not in body
    assert "sessionStorage.setItem('knovas.docFieldsHandoff'" in body
    assert "window.location.assign('/?list=1')" in body
    assert "encodeURIComponent" not in body and "URLSearchParams" not in body
    handoff = _code(_method_body(_source(DOC_FIELDS_JS), "_applyHandoff"))
    assert "removeItem(DocFieldsUI.HANDOFF_KEY)" in handoff
    assert "static HANDOFF_KEY = 'knovas.docFieldsHandoff';" in _source(DOC_FIELDS_JS)


def test_an_empty_listing_is_worded_by_the_server():
    """platform-search-1: the browser never says "no document" on its own; an
    empty listing shows the server's ``empty_text`` (H8, H9)."""
    body = _code(_method_body(_source(APP_JS), "displayListing"))
    assert "NO_RESULTS_TEXTS.empty_where" not in body
    assert "emptyText ||" in body
    pages = _code(_method_body(_source(DOC_FIELDS_JS), "_fetchPages"))
    assert "emptyText: String(data.empty_text || '')" in pages
    admin = _code(_source(STATIC_JS / "admin_documents.js"))
    assert "Kein f\u00fcr Sie sichtbares Dokument" not in admin
    assert "data.empty_text" in admin


def test_the_rail_clear_never_leaves_filtered_results_unmarked():
    """platform-search-2: "Filter entfernen" re-runs a filtered search
    without its filters, and removes a listing (always filtered)."""
    source = _source(DOC_FIELDS_JS)
    assert "addEventListener('click', () => this.clearAndRefresh())" in source
    body = _code(_method_body(source, "clearAndRefresh"))
    assert "this.app.performSearch(this.app.currentQuery)" in body
    assert "this.app.clearResults()" in body
    assert "this.clearListingState()" in body
    clear = _code(_method_body(_source(APP_JS), "clearResults"))
    assert "this.resultsContainer.replaceChildren()" in clear
    assert "this.resultsSection.style.display = 'none'" in clear


def test_a_listing_only_search_says_the_rail_was_not_applied():
    """platform-search-3: under listing_only the rail's values never go with
    a search; when it holds any, the server's notice says so."""
    body = _code(_method_body(_source(DOC_FIELDS_JS), "renderSearchState"))
    assert "!this.showsFilters && this.showsListing && this.collectWhere()" in body
    assert "df.rail_not_applied" in body


def test_a_refusal_clears_the_listing_texts():
    """platform-search-5: a refusal replaces the rows; the incomplete notice
    and the deadline banner of an earlier listing go with them."""
    refusal = _code(_method_body(_source(APP_JS), "displayRefusal"))
    assert "this.docFields.clearListingState()" in refusal
    clear = _code(_method_body(_source(DOC_FIELDS_JS), "clearListingState"))
    assert "this._listing = null" in clear
    assert "this._setText(this.listNotice, '')" in clear
    assert "this._setText(this.banner, '')" in clear


# ---------------------------------------------------------------------------
# F2: the filter rail's operators (doc_fields.js under Node)
# ---------------------------------------------------------------------------

from test_experiments_frontend import _run_node, needs_node  # noqa: E402

_DF_EXPORT = "\n;globalThis.__DF = DocFieldsUI;"
_UNDEF = "__undefined__"
_ALL_TYPES = ("enum", "code", "text", "money", "number", "date", "period", "bool",
              "entity_ref")


def _rail(body):
    """``body`` with DocFieldsUI as ``__DF``, in the vm harness of
    test_experiments_frontend."""
    return _run_node(body, [DOC_FIELDS_JS], suffix=_DF_EXPORT)


@needs_node
class TestRailOperands:
    """The ``where`` value per datatype and operator (spec F2's table). The
    rail's controls read and restore through these two functions only, for
    the search and for "Liste anzeigen" alike."""

    TABLE = [
        ("enum", {"op": "in", "choices": ["invoice"]}, "invoice"),
        ("enum", {"op": "in", "choices": ["invoice", "contract"]}, ["invoice", "contract"]),
        ("enum", {"op": "in", "choices": []}, _UNDEF),
        ("enum", {"op": "prefix", "text": " correspondence "}, {"prefix": "correspondence"}),
        ("code", {"op": "eq", "text": "4A_123/2024"}, "4A_123/2024"),
        ("code", {"op": "prefix", "text": "E11"}, {"prefix": "E11"}),
        ("code", {"op": "in", "text": "E11.90, E10.1, , E11.90"}, ["E11.90", "E10.1"]),
        ("code", {"op": "in", "text": "E11.90"}, "E11.90"),
        ("text", {"op": "eq", "text": "Telefon"}, "Telefon"),
        ("text", {"op": "prefix", "text": "Tel"}, {"prefix": "Tel"}),
        ("text", {"op": "eq", "text": "   "}, _UNDEF),
        ("money", {"op": "eq", "text": "CHF 1'000"}, "CHF 1'000"),
        ("money", {"op": "range", "lo": "CHF 1'000"}, {"gte": "CHF 1'000"}),
        ("money", {"op": "range", "hi": "CHF 5'000"}, {"lte": "CHF 5'000"}),
        ("money", {"op": "range", "lo": "CHF 1'000", "hi": "CHF 5'000"},
         {"between": ["CHF 1'000", "CHF 5'000"]}),
        ("number", {"op": "range", "lo": "10", "hi": "20"}, {"between": ["10", "20"]}),
        ("number", {"op": "range"}, _UNDEF),
        ("date", {"op": "overlaps", "text": "2024"}, "2024"),
        ("date", {"op": "range", "lo": "01.01.2024", "hi": "30.06.2024"},
         {"gte": "01.01.2024", "lte": "30.06.2024"}),
        ("date", {"op": "range", "lo": "01.01.2024", "hi": "30.06.2024", "partly": True},
         {"gte": "01.01.2024", "lte": "30.06.2024", "match": "possible"}),
        ("date", {"op": "range", "hi": "30.06.2024", "partly": True},
         {"lte": "30.06.2024", "match": "possible"}),
        ("date", {"op": "within", "text": "2024"}, {"within": "2024"}),
        ("period", {"op": "overlaps", "text": "GJ 2024"}, "GJ 2024"),
        ("period", {"op": "within", "text": "GJ 2024"}, {"within": "GJ 2024"}),
        ("bool", {"op": "true"}, True),
        ("bool", {"op": "false"}, False),
        ("bool", {"op": ""}, _UNDEF),
        ("entity_ref", {"op": "in", "text": "Muster AG"}, {"name": "Muster AG"}),
        ("entity_ref", {"op": "in", "text": "Muster AG; Beispiel GmbH;"},
         [{"name": "Muster AG"}, {"name": "Beispiel GmbH"}]),
        ("entity_ref", {"op": "in", "text": ""}, _UNDEF),
    ] + [(t, {"op": "exists"}, {"exists": True}) for t in _ALL_TYPES]

    def test_the_operator_table(self):
        cases = [[t, s] for t, s, _ in self.TABLE]
        result = _rail("const cases = " + json.dumps(cases) + ";\n"
                       "out(cases.map(([type, state]) => {"
                       " const v = __DF.buildOperand(type, state);"
                       " return v === undefined ? '" + _UNDEF + "' : v; }));")
        assert result == [v for _, _, v in self.TABLE]

    def test_a_value_survives_a_rail_rebuild(self):
        """load() reads the rail before it rebuilds it and writes the values
        back; the Cortex handoff writes {name}: parseOperand must give back
        what buildOperand made."""
        values = [[t, v] for t, _, v in self.TABLE if v != _UNDEF]
        result = _rail("const cases = " + json.dumps(values) + ";\n"
                       "out(cases.map(([type, value]) =>"
                       " __DF.buildOperand(type, __DF.parseOperand(type, value))));")
        assert result == [v for _, v in values]

    def test_an_empty_rail_starts_at_each_type_s_first_operator(self):
        result = _rail("out(['enum', 'code', 'money', 'date', 'bool', 'entity_ref', 'weird']"
                       ".map((t) => __DF.parseOperand(t, undefined).op));")
        assert result == ["in", "eq", "eq", "overlaps", "", "in", "eq"]

    def test_every_type_offers_hat_einen_wert(self):
        result = _rail("out(" + json.dumps(list(_ALL_TYPES)) + ".map((t) =>"
                       " __DF.OPERATORS[t].map(([op]) => op)));")
        assert all("exists" in ops for ops in result)


def test_the_rail_controls_use_the_one_builder():
    """F2: every facet control reads and restores through buildOperand and
    parseOperand, so search and "Liste anzeigen" send the same values."""
    source = _source(DOC_FIELDS_JS)
    body = _code(_method_body(source, "_controlFor"))
    assert "DocFieldsUI.buildOperand(" in body and "DocFieldsUI.parseOperand(" in body
    assert "_labelled" not in source
    suggestions = _code(_method_body(source, "_suggestions"))
    assert "this.suggest(field.key, last)" in suggestions


def test_the_listing_sorts_by_path_both_ways():
    body = _code(_method_body(_source(DOC_FIELDS_JS), "_renderSort"))
    assert "this._option('pointer:asc', 'Dokumentpfad aufsteigend')" in body
    assert "this._option('pointer:desc', 'Dokumentpfad absteigend')" in body


@needs_node
def test_collect_sort_reads_pointer_descending():
    result = _rail("out(__DF.prototype.collectSort.call({ _sortControl: { value: 'pointer:desc' } }));")
    assert result == {"field": "pointer", "order": "desc"}


def test_search_notices_come_from_the_answer():
    """F3: the page shows what the server put in ``notices``; listings,
    refusals and a cleared list carry none."""
    source = _source(APP_JS)
    assert "this._renderSearchNotices(data.notices" in _code(_method_body(source, "performSearch"))
    for name in ("displayListing", "displayRefusal", "clearResults"):
        assert "this._renderSearchNotices([], 0)" in _code(_method_body(source, name)), name


_APP_EXPORT = "\n;globalThis.__App = DocumentSearchApp;"


@needs_node
class TestSearchNotices:
    def _run(self, body):
        return _run_node(body, [APP_JS], suffix=_APP_EXPORT)

    CASES = [
        ({"kind": "return_fields_unavailable"},
         "Feldwerte konnten nicht gelesen werden; die Treffer werden ohne Werte angezeigt."),
        ({"kind": "degraded_to_bm25"},
         "Eingeschr\u00e4nkte Suchqualit\u00e4t: diese Treffer wurden nur \u00fcber genaue "
         "W\u00f6rter gefunden. F\u00fcr die volle Qualit\u00e4t sp\u00e4ter erneut suchen."),
        ({"kind": "auto_scope_applied", "names": ["Muster AG"], "hidden_count": 0},
         "Suche automatisch auf Muster AG eingegrenzt (in der Frage erkannt)."),
        ({"kind": "auto_scope_applied", "names": ["Muster AG", "Beispiel GmbH"],
          "hidden_count": 0},
         "Suche automatisch auf Muster AG und Beispiel GmbH eingegrenzt (in der Frage erkannt)."),
        ({"kind": "auto_scope_applied", "names": ["Muster AG"], "hidden_count": 1},
         "Suche automatisch auf Muster AG und 1 weiteren Eintrag eingegrenzt "
         "(in der Frage erkannt)."),
        ({"kind": "auto_scope_applied", "names": ["Muster AG", "Beispiel GmbH"],
          "hidden_count": 2},
         "Suche automatisch auf Muster AG, Beispiel GmbH und 2 weitere Eintr\u00e4ge "
         "eingegrenzt (in der Frage erkannt)."),
        ({"kind": "auto_scope_applied", "names": [], "hidden_count": 2},
         "Suche automatisch auf 2 Eintr\u00e4ge eingegrenzt (in der Frage erkannt)."),
        ({"kind": "auto_scope_applied", "names": [], "hidden_count": 1},
         "Suche automatisch auf 1 Eintrag eingegrenzt (in der Frage erkannt)."),
        ({"kind": "auto_scope_fallback", "names": ["Muster AG"], "hidden_count": 0},
         "In Muster AG nichts gefunden \u2013 alle Dokumente durchsucht."),
        ({"kind": "auto_scope_fallback", "names": ["Muster AG"], "hidden_count": 2},
         "In Muster AG und 2 weiteren Eintr\u00e4gen nichts gefunden \u2013 "
         "alle Dokumente durchsucht."),
        ({"kind": "auto_scope_fallback", "names": [], "hidden_count": 1},
         "In 1 erkannten Eintrag nichts gefunden \u2013 alle Dokumente durchsucht."),
        ({"kind": "something_new"}, ""),
    ]

    def test_each_kind_reads_in_german(self):
        notices = [n for n, _ in self.CASES]
        result = self._run("const cases = " + json.dumps(notices) + ";\n"
                           "out(cases.map((n) => __App.prototype._searchNoticeText"
                           ".call(__App.prototype, n)));")
        assert result == [text for _, text in self.CASES]

    def test_notices_go_above_the_results_as_text(self):
        result = self._run(r"""
        const box = new FakeEl('div');
        const app = Object.create(__App.prototype);
        app.searchNotices = box;
        app._renderSearchNotices([
          { kind: 'auto_scope_applied', names: ['<b>Muster AG</b>'], hidden_count: 0 },
          { kind: 'degraded_to_bm25', names: [], hidden_count: 0 },
          { kind: 'something_new' },
        ], 3);
        const shown = { hidden: box.hidden, kinds: box.children.map((c) => c.dataset.kind),
                        first: box.children[0].textContent };
        app._renderSearchNotices([{ kind: 'degraded_to_bm25' },
          { kind: 'return_fields_unavailable' },
          { kind: 'auto_scope_fallback', names: ['Muster AG'], hidden_count: 0 }], 0);
        const empty = box.children.map((c) => c.dataset.kind);
        app._renderSearchNotices([], 0);
        out({ shown, empty, hiddenAfter: box.hidden });
        """)
        assert result["shown"] == {
            "hidden": False, "kinds": ["auto_scope_applied", "degraded_to_bm25"],
            "first": "Suche automatisch auf <b>Muster AG</b> eingegrenzt (in der Frage erkannt)."}
        assert result["empty"] == ["auto_scope_fallback"]
        assert result["hiddenAfter"] is True
