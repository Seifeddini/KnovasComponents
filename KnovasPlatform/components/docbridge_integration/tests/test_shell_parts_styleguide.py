"""The shared page parts on the styleguide page (PMVP-12, plan FEAT-2026-11-19-platform-mvp F-7).

GET /styleguide, as an administrator, against the real app and the identity database. Held here:

1. the section PMVP-10 left empty shows every macro of _parts.html with its call beside it, in each state;
2. the examples are drawn by the macros: the call written as text is the call that draws the example, and the
   template types no part's class by hand;
3. the section is usable as it stands: what a reference says it opens is on the page, every link stays on the
   page, and the section brings no script.

The page is fetched once for this file (the fixture ``page``): it is the same for every test here, and each app
costs a schema and every migration.
"""
from __future__ import annotations

import html as html_lib
import re
from pathlib import Path

import pytest

pytest.importorskip("flask")

from conftest import platform_db_reachable  # noqa: E402

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

P = Path(__file__).resolve().parents[1]
TEMPLATE = P / "src" / "web_interface" / "templates" / "styleguide.html"
OPENING = '<section class="sg-section" aria-labelledby="sg-seitenteile">'

MACROS = ("page", "page_head", "section", "card", "rowline", "tabs", "status_dot", "tag", "ref_chip", "link")
BLOCK_MACROS = ("page", "section", "card", "rowline")
# Classes of parts.css that are plain helpers, written by hand where they are needed; no macro draws them.
HELPERS = {"k-head", "k-muted", "k-small"}
# Each part in each state, as the macros write it.
DRAWN = (
    '<div class="k-page">', '<div class="k-page k-page--wide">',
    '<div class="k-page-head"><h1 class="k-title">',                    # a title alone
    '<p class="k-eyebrow">', '<p class="k-lede">',
    '<section class="k-section">', '<h2 class="k-section-title">',
    '<div class="k-card">', '<div class="k-card k-card--now" aria-current="true">',
    '<div class="k-rowline">',
    '<nav class="k-tabs"', 'class="k-tab k-tab--on"', 'aria-current="page"', '<span class="k-tab-count">',
    'class="k-dot k-dot--ok" role="img" aria-label="Aktuell"', 'class="k-dot k-dot--warn" role="img"',
    'class="k-dot k-dot--bad" role="img"',
    '<span class="k-tag">', '<span class="k-tag k-tag--warn">', '<span class="k-tag k-tag--bad">',
    '<span class="k-tag k-tag--acc">',
    '<button type="button" class="k-ref">', '<button type="button" class="k-ref k-ref--on">',
    '<a class="k-ref" href="#', '<a class="k-ref k-ref--on" href="#',
    'class="k-ref k-ref--on" aria-expanded="true" aria-controls="', 'class="k-ref" aria-expanded="false" aria-controls="',
    '<a class="k-link" href="#', '<a class="k-link k-link--dotted" href="#',
)

_FETCHED: dict[str, object] = {}


@pytest.fixture
def page(request) -> str:
    """The styleguide page as an administrator gets it, fetched by the first test that asks."""
    if "page" not in _FETCHED:
        client = request.getfixturevalue("admin_client")
        response = client.get("/styleguide")
        assert response.status_code == 200
        _FETCHED["parts_css"] = client.get("/static/css/parts.css").status_code
        _FETCHED["page"] = response.data.decode("utf-8")
    return _FETCHED["page"]


@pytest.fixture
def section(page) -> str:
    """The parts section of the page: from its opening tag to the next section of the styleguide."""
    start = page.index(OPENING)
    return page[start:page.index('<section class="sg-section"', start + 1)]


def _calls(section: str) -> list[str]:
    """The calls written on the page, as a person reads them."""
    return [html_lib.unescape(c) for c in re.findall(r'<code class="sg-call">(.*?)</code>', section, re.S)]


def _source() -> str:
    source = TEMPLATE.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index(OPENING)
    return source[start:source.index('<section class="sg-section"', start + 1)]


# --- 1: every macro, with its call, in each state -------------------------------------------------------


def test_the_section_is_there_under_its_heading(section):
    assert '<h2 id="sg-seitenteile">Seitenteile</h2>' in section
    assert "_parts.html" in section and "parts.css" in section
    assert "sg-empty" not in section, "the section is no longer the empty one PMVP-10 left"


@pytest.mark.parametrize("macro", MACROS)
def test_the_styleguide_shows_every_macro_with_its_call(section, macro):
    mine = [c for c in _calls(section) if re.search(rf"\bparts\.{macro}\(", c)]
    assert mine, f"no call of parts.{macro} is written on the page"
    if macro in BLOCK_MACROS:
        assert all(c.startswith("{% call parts.") and c.endswith("{% endcall %}") for c in mine), mine


def test_every_call_on_the_page_names_a_macro_of_the_parts(section):
    calls = _calls(section)
    assert len(calls) >= 25
    for call in calls:
        named = re.search(r"\bparts\.([a-z_]+)\(", call)
        assert named and named.group(1) in MACROS, call


@pytest.mark.parametrize("mark_up", DRAWN)
def test_each_state_of_each_part_is_drawn(section, mark_up):
    assert mark_up in section, f"not drawn: {mark_up}"


def test_a_tab_row_marks_one_tab_and_one_row_is_shown_too_narrow_for_its_tabs(section):
    rows = re.findall(r'<nav class="k-tabs".*?</nav>', section, re.S)
    assert len(rows) >= 2
    for row in rows:
        assert row.count('aria-current="page"') == 1 and row.count("k-tab--on") == 1
    assert re.search(r'<div class="sg-narrow">\s*<nav class="k-tabs"', section), "no row that has to scroll"


def test_the_parts_are_shown_together_as_on_a_page(section):
    """One page built from all of them, as the reference screens show a page."""
    together = section[section.index(">Zusammen</h3>"):]
    for name in ("k-page", "k-head", "k-page-head", "k-tabs", "k-card--now", "k-section", "k-rowline", "k-dot--ok",
                 "k-dot--warn", "k-dot--bad", "k-tag", "k-ref", "k-ref--on", "k-link--dotted"):
        assert name in together, f"{name} is not in the page that shows the parts together"
    # one main action on the page
    assert together.count("btn btn-primary") == 1


# --- 2: drawn by the macros ----------------------------------------------------------------------------


def test_a_call_written_on_the_page_is_the_call_that_draws_the_example():
    """In the template a call stands twice on one line: as text for the reader, and for real."""
    source = _source()
    written = re.findall(r"\{% call shown\('(.+?)'\) %\}", source)
    assert len(written) >= 25
    for call in written:
        block = re.fullmatch(r"(\{% call parts\.[a-z_]+\(.*?\) %\}) … \{% endcall %\}", call)
        if block:
            # once inside the text, and at least once more as the block that draws
            assert source.count(block.group(1)) >= 2, f"{call} is written but not used"
        else:
            assert call.startswith("parts."), call
            after = source.split("{% call shown('" + call + "') %}", 1)[1].split("{% endcall %}", 1)[0]
            assert "{{ " + call + " }}" in after, f"{call} is written, but its example is drawn by something else"


def test_the_template_types_no_class_of_a_part_by_hand():
    """Only the helper classes are written out; every part comes from its macro (no copied mark-up)."""
    source = TEMPLATE.read_text(encoding="utf-8")
    typed = set()
    for classes in re.findall(r'class="([^"]*)"', source):
        typed |= {name for name in classes.split() if name.startswith("k-")}
    assert typed <= HELPERS, f"typed by hand: {sorted(typed - HELPERS)}"
    style = re.search(r"<style>(.*?)</style>", source, re.S).group(1)
    assert not re.findall(r"\.k-[a-z-]+", style), "the page's own rules restyle a part"


def test_the_template_takes_the_parts_from_the_one_file():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert source.count('{% import "_parts.html" as parts %}') == 1
    assert not re.search(r"\|\s*safe\b", source)


def test_the_page_draws_the_parts_with_the_stylesheet_every_page_loads(page):
    sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)"', page)
    assert len(sheets) == 1 and "/static/css/style.css" in sheets[0], sheets
    assert _FETCHED["parts_css"] == 200, "the app does not serve static/css/parts.css"


# --- 3: usable as it stands ----------------------------------------------------------------------------


def test_what_a_reference_says_it_opens_is_on_the_page(section):
    targets = re.findall(r'aria-controls="([^"]+)"', section)
    assert len(targets) >= 2
    for target in targets:
        assert f'id="{target}"' in section, f"aria-controls names {target}, which is not there"
    # the open one is shown, the closed one is not
    opened = re.search(r'aria-expanded="true" aria-controls="([^"]+)"', section).group(1)
    closed = re.search(r'aria-expanded="false" aria-controls="([^"]+)"', section).group(1)
    assert "hidden" not in re.search(rf'<p id="{opened}"[^>]*>', section).group(0)
    assert "hidden" in re.search(rf'<p id="{closed}"[^>]*>', section).group(0)


def test_every_link_of_the_section_stays_on_the_page(page, section):
    addresses = re.findall(r'<a [^>]*href="([^"]*)"', section)
    assert len(addresses) >= 12
    for address in addresses:
        assert address.startswith("#"), f"{address} leaves the page"
        assert f'id="{address[1:]}"' in page, f"{address} points at nothing"


def test_the_section_brings_no_script_and_no_style_of_its_own(section):
    assert "<script" not in section.lower()
    assert not re.search(r"\son[a-z]+=", section), "an event handler"
    assert "style=" not in section and "<style" not in section


def test_a_yellow_or_red_dot_has_its_words_beside_it(section):
    """Rule 5 of the design rules: a warn or bad dot never stands alone."""
    dots = list(re.finditer(r'<span class="k-dot k-dot--(?:warn|bad)"[^>]*></span>', section))
    assert len(dots) >= 4
    for dot in dots:
        beside = re.sub(r"<[^>]+>", " ", section[dot.end():dot.end() + 400])
        assert len(beside.split()) >= 3, "a dot with nothing beside it"


def test_a_call_is_shown_as_text_never_as_mark_up(section):
    """The calls hold quotes and braces; they reach the page as characters."""
    assert "{% call parts.card(now=True) %}" in section
    assert "parts.tag(&#34;abgeleitet&#34;, kind=&#34;acc&#34;)" in section
    assert 'parts.tag("abgeleitet", kind="acc")' in _calls(section)
