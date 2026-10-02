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
                 "displayRefusal"):
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
                       "showListButton", "resultsHeading"):
        assert f'id="{element_id}"' in html, element_id
    # Gated UI ships hidden; doc_fields.js shows it only for its capability.
    assert 'id="docFieldsRail" hidden' in html
    assert 'id="previewFieldsSection" hidden' in html


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
