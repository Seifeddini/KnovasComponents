"""The shared page parts (PMVP-12, plan FEAT-2026-11-19-platform-mvp F-7): the macros of templates/_parts.html.

Every macro is drawn by the app's own template engine (the settings a page gets, autoescape among them), with no
database. Held here:

1. each macro draws its classes and its states, and nothing else changes with a state;
2. `tabs` marks exactly the active item, `status_dot` carries its label as its accessible name and shows no text,
   `ref_chip` is a link with an address and a button without, and says what it opens when it is told;
3. whatever a macro is given comes out as text: a title, a tag, a tab label, a link text, a label or an address
   that holds mark-up cannot become mark-up;
4. every class a macro draws has a rule in parts.css.

The names and arguments used below are the ones the work order fixes; the screens rely on them.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

pytest.importorskip("flask")

P = Path(__file__).resolve().parents[1]
PARTS_HTML = P / "src" / "web_interface" / "templates" / "_parts.html"
PARTS_CSS = P / "src" / "web_interface" / "static" / "css" / "parts.css"

SCRIPT = "<script>alert(1)</script>"
ESCAPED = "&lt;script&gt;alert(1)&lt;/script&gt;"
MARKDOWN = "**fett** und [ein Link](javascript:alert(1)) und `code`"
TABS = [
    {"key": "ueberblick", "label": "Überblick", "href": "/dossier/7"},
    {"key": "mandate", "label": "Mandate", "href": "/dossier/7/mandate", "count": 7},
    {"key": "vertraege", "label": "Verträge", "href": "/dossier/7/vertraege", "count": 0},
]


@pytest.fixture
def draw(docbridge_app):
    """Draws a piece of template that uses the parts, with the app's template engine."""
    env = docbridge_app.jinja_env

    def _draw(body: str, **values) -> str:
        return env.from_string('{% import "_parts.html" as parts %}' + body).render(**values)

    return _draw


class _Elements(HTMLParser):
    """Every element of a piece of mark-up, in order: its tag, its attributes and the text directly inside it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found: list[dict] = []
        self._open: list[dict] = []

    def handle_starttag(self, tag, attrs):
        element = {"tag": tag, "attrs": dict(attrs), "text": "", "classes": set((dict(attrs).get("class") or "").split())}
        self.found.append(element)
        self._open.append(element)

    def handle_endtag(self, tag):
        while self._open and self._open.pop()["tag"] != tag:
            pass

    def handle_data(self, data):
        if self._open:
            self._open[-1]["text"] += data


def _elements(html: str) -> list[dict]:
    parser = _Elements()
    parser.feed(html)
    parser.close()
    return parser.found


def _with_class(html: str, name: str) -> list[dict]:
    return [e for e in _elements(html) if name in e["classes"]]


def _only(html: str, name: str) -> dict:
    found = _with_class(html, name)
    assert len(found) == 1, f"expected one .{name}, found {len(found)} in {html!r}"
    return found[0]


# --- 1: classes and states ----------------------------------------------------------------------------


def test_page_is_the_one_column_and_keeps_what_the_caller_puts_in(draw):
    html = draw('{% call parts.page() %}<p id="inhalt">Inhalt</p>{% endcall %}')
    column = _only(html, "k-page")
    assert column["tag"] == "div" and column["classes"] == {"k-page"}
    assert '<p id="inhalt">Inhalt</p>' in html


def test_page_wide_is_the_same_column_marked_wide(draw):
    html = draw("{% call parts.page(wide=True) %}x{% endcall %}")
    assert _only(html, "k-page")["classes"] == {"k-page", "k-page--wide"}
    assert _only(draw("{% call parts.page(wide=False) %}x{% endcall %}"), "k-page")["classes"] == {"k-page"}


def test_page_head_with_a_title_only_draws_the_h1_alone(draw):
    html = draw('{{ parts.page_head("Quellen") }}')
    title = _only(html, "k-title")
    assert title["tag"] == "h1" and title["text"] == "Quellen"
    assert not _with_class(html, "k-eyebrow") and not _with_class(html, "k-lede")
    assert [e["tag"] for e in _elements(html)].count("h1") == 1


def test_page_head_draws_eyebrow_title_and_lede_in_that_order(draw):
    html = draw('{{ parts.page_head("Limmat Immobilien AG", eyebrow="Mandant", lede="Zürich · Mandant seit 2018") }}')
    assert _only(html, "k-eyebrow")["text"] == "Mandant"
    assert _only(html, "k-title")["text"] == "Limmat Immobilien AG"
    assert _only(html, "k-lede")["text"] == "Zürich · Mandant seit 2018"
    order = [next(iter(e["classes"])) for e in _elements(html) if e["classes"] & {"k-eyebrow", "k-title", "k-lede"}]
    assert order == ["k-eyebrow", "k-title", "k-lede"]
    assert _elements(html)[0]["classes"] == {"k-page-head"}


def test_page_head_takes_its_arguments_by_position_too(draw):
    html = draw('{{ parts.page_head("Titel", "Darüber", "Darunter") }}')
    assert _only(html, "k-eyebrow")["text"] == "Darüber" and _only(html, "k-lede")["text"] == "Darunter"


def test_section_is_a_small_h2_and_its_content(draw):
    html = draw('{% call parts.section("Angaben") %}<p>Branche</p>{% endcall %}')
    assert _only(html, "k-section")["tag"] == "section"
    heading = _only(html, "k-section-title")
    assert heading["tag"] == "h2" and heading["text"] == "Angaben"
    assert html.index("k-section-title") < html.index("<p>Branche</p>")


def test_card_is_plain_unless_it_is_the_current_one(draw):
    plain = _only(draw("{% call parts.card() %}<p>x</p>{% endcall %}"), "k-card")
    assert plain["tag"] == "div" and plain["classes"] == {"k-card"} and "aria-current" not in plain["attrs"]
    now = _only(draw("{% call parts.card(now=True) %}<p>x</p>{% endcall %}"), "k-card")
    assert now["classes"] == {"k-card", "k-card--now"}
    # the mark is not colour alone: the card says it is the current one
    assert now["attrs"].get("aria-current") == "true"


def test_rowline_holds_the_two_children_the_caller_puts_in(draw):
    html = draw('{% call parts.rowline() %}<div id="links">Inhalt</div><span id="rechts">→</span>{% endcall %}')
    assert _only(html, "k-rowline")["classes"] == {"k-rowline"}
    assert '<div id="links">Inhalt</div><span id="rechts">→</span>' in html


def test_tabs_are_links_in_the_order_given(draw):
    html = draw('{{ parts.tabs(items, "mandate") }}', items=TABS)
    row = _only(html, "k-tabs")
    assert row["tag"] == "nav" and row["attrs"].get("aria-label")
    tabs = _with_class(html, "k-tab")
    assert [t["tag"] for t in tabs] == ["a", "a", "a"]
    assert [t["attrs"]["href"] for t in tabs] == [item["href"] for item in TABS]
    assert [t["text"].strip() for t in tabs] == ["Überblick", "Mandate", "Verträge"]


@pytest.mark.parametrize("active", ["ueberblick", "mandate", "vertraege"])
def test_tabs_mark_exactly_the_active_item(draw, active):
    html = draw("{{ parts.tabs(items, active) }}", items=TABS, active=active)
    current = [t for t in _with_class(html, "k-tab") if "aria-current" in t["attrs"]]
    assert [t["attrs"]["aria-current"] for t in current] == ["page"]
    assert current[0]["attrs"]["href"] == next(i["href"] for i in TABS if i["key"] == active)
    assert [t for t in _with_class(html, "k-tab") if "k-tab--on" in t["classes"]] == current


def test_tabs_mark_nothing_when_no_item_has_the_active_key(draw):
    html = draw('{{ parts.tabs(items, "gibt-es-nicht") }}', items=TABS)
    assert "aria-current" not in html and "k-tab--on" not in html
    assert len(_with_class(html, "k-tab")) == 3


def test_a_tab_shows_its_count_apart_from_its_label_and_none_when_it_has_none(draw):
    html = draw('{{ parts.tabs(items, "ueberblick") }}', items=TABS)
    counts = _with_class(html, "k-tab-count")
    # a count of nothing is still a count; the first item has none
    assert [c["text"] for c in counts] == ["7", "0"]
    assert re.search(r'>Mandate <span class="k-tab-count">7</span></a>', html), "label, a space, then the count"
    assert re.search(r'href="/dossier/7"[^>]*>Überblick</a>', html)


def test_tabs_take_a_list_written_in_the_template(draw):
    html = draw('{{ parts.tabs([{"key": "a", "label": "Eins", "href": "#a"}, {"key": "b", "label": "Zwei", '
                '"href": "#b", "count": 2}], "b") }}')
    assert [t["text"].strip() for t in _with_class(html, "k-tab")] == ["Eins", "Zwei"]
    assert _only(html, "k-tab--on")["attrs"]["href"] == "#b"


@pytest.mark.parametrize("state", ["ok", "warn", "bad"])
def test_status_dot_draws_its_state_and_carries_its_label_as_its_name(draw, state):
    html = draw("{{ parts.status_dot(state, label) }}", state=state, label="Anmeldung abgelaufen")
    dot = _only(html, "k-dot")
    assert dot["classes"] == {"k-dot", f"k-dot--{state}"}
    # the accessible name: an image by role, named by the label
    assert dot["attrs"] == {"class": f"k-dot k-dot--{state}", "role": "img", "aria-label": "Anmeldung abgelaufen"}
    # the label is not shown
    assert dot["text"] == "" and re.sub(r"<[^>]+>", "", html).strip() == ""


def test_status_dot_with_a_state_it_does_not_know_draws_the_plain_dot(draw):
    dot = _only(draw('{{ parts.status_dot("lila", "Unbekannt") }}'), "k-dot")
    assert dot["classes"] == {"k-dot"} and dot["attrs"]["aria-label"] == "Unbekannt"


@pytest.mark.parametrize("kind, classes", [
    ("", {"k-tag"}), ("warn", {"k-tag", "k-tag--warn"}), ("bad", {"k-tag", "k-tag--bad"}),
    ("acc", {"k-tag", "k-tag--acc"}), ("gibt-es-nicht", {"k-tag"}),
])
def test_tag_is_a_pill_of_its_kind(draw, kind, classes):
    tag = _only(draw("{{ parts.tag(text, kind=kind) }}", text="fehlt noch", kind=kind), "k-tag")
    assert tag["tag"] == "span" and tag["classes"] == classes and tag["text"] == "fehlt noch"


def test_tag_without_a_kind_is_the_plain_pill(draw):
    assert _only(draw('{{ parts.tag("nur Testgruppe") }}'), "k-tag")["classes"] == {"k-tag"}


def test_ref_chip_is_a_button_without_an_address(draw):
    chip = _only(draw("{{ parts.ref_chip(1) }}"), "k-ref")
    assert chip["tag"] == "button" and chip["text"] == "1"
    assert chip["attrs"] == {"type": "button", "class": "k-ref"}


def test_ref_chip_is_a_link_with_an_address(draw):
    chip = _only(draw('{{ parts.ref_chip(3, href="/dokument/12#seite-4") }}'), "k-ref")
    assert chip["tag"] == "a" and chip["text"] == "3"
    assert chip["attrs"] == {"class": "k-ref", "href": "/dokument/12#seite-4"}


@pytest.mark.parametrize("call, tag", [("parts.ref_chip(2, active=True)", "button"),
                                       ('parts.ref_chip(2, active=True, href="#stelle")', "a")])
def test_ref_chip_active_is_the_same_chip_marked_on(draw, call, tag):
    chip = _only(draw("{{ " + call + " }}"), "k-ref")
    assert chip["tag"] == tag and chip["classes"] == {"k-ref", "k-ref--on"}
    assert "aria-expanded" not in chip["attrs"] and "aria-controls" not in chip["attrs"]


@pytest.mark.parametrize("href", [None, "#stelle"])
@pytest.mark.parametrize("active, expanded", [(True, "true"), (False, "false")])
def test_ref_chip_says_what_it_opens_and_whether_it_is_open(draw, href, active, expanded):
    chip = _only(draw("{{ parts.ref_chip(2, active=active, href=href, controls='stelle-2') }}", active=active,
                      href=href), "k-ref")
    assert chip["tag"] == ("a" if href else "button")
    assert chip["attrs"]["aria-controls"] == "stelle-2" and chip["attrs"]["aria-expanded"] == expanded
    assert ("k-ref--on" in chip["classes"]) is active


def test_ref_chip_takes_its_arguments_by_position_too(draw):
    chip = _only(draw('{{ parts.ref_chip(4, True, "#stelle", "stelle-4") }}'), "k-ref")
    assert chip["tag"] == "a" and chip["classes"] == {"k-ref", "k-ref--on"}
    assert chip["attrs"]["href"] == "#stelle" and chip["attrs"]["aria-controls"] == "stelle-4"


def test_link_is_a_link_and_dotted_for_a_name_that_opens_a_dossier(draw):
    plain = _only(draw('{{ parts.link("Im Dokument zeigen", "/dokument/12") }}'), "k-link")
    assert plain["tag"] == "a" and plain["text"] == "Im Dokument zeigen"
    assert plain["attrs"] == {"class": "k-link", "href": "/dokument/12"}
    dotted = _only(draw('{{ parts.link("Limmat Immobilien AG", "/dossier/7", dotted=True) }}'), "k-link")
    assert dotted["classes"] == {"k-link", "k-link--dotted"} and dotted["attrs"]["href"] == "/dossier/7"


def test_a_part_in_a_sentence_brings_no_space_of_its_own(draw):
    """A reference stands right behind its sentence, a tag or a link exactly where the caller writes it."""
    html = draw('verlängert{{ parts.ref_chip(2) }}. {{ parts.link("Ansehen", "#a") }}, '
                '{{ parts.tag("neu") }}!{{ parts.status_dot("ok", "Aktuell") }}Quelle')
    assert re.fullmatch(r'verlängert<button[^>]*>2</button>\. <a[^>]*>Ansehen</a>, <span[^>]*>neu</span>!'
                        r'<span[^>]*></span>Quelle', html), html


# --- 3: whatever a macro is given comes out as text -----------------------------------------------------


def test_a_title_an_eyebrow_and_a_lede_with_mark_up_come_out_escaped(draw):
    html = draw("{{ parts.page_head(text, eyebrow=text, lede=text) }}", text=SCRIPT)
    assert "<script" not in html and html.count(ESCAPED) == 3
    assert _only(html, "k-title")["text"] == SCRIPT


def test_a_section_title_with_mark_up_comes_out_escaped(draw):
    html = draw("{% call parts.section(text) %}x{% endcall %}", text=SCRIPT)
    assert "<script" not in html and ESCAPED in html


def test_a_tag_text_with_mark_up_comes_out_escaped(draw):
    html = draw("{{ parts.tag(text, kind='warn') }}", text=SCRIPT)
    assert "<script" not in html and ESCAPED in html
    assert _only(html, "k-tag")["text"] == SCRIPT


def test_a_tab_label_a_count_and_an_address_with_mark_up_come_out_escaped(draw):
    items = [{"key": "a", "label": SCRIPT, "href": '"><script>alert(2)</script>', "count": SCRIPT}]
    html = draw('{{ parts.tabs(items, "a") }}', items=items)
    assert "<script" not in html and html.count(ESCAPED) == 2
    tab = _only(html, "k-tab")
    assert tab["attrs"]["href"] == '"><script>alert(2)</script>', "the address stays inside its attribute"
    assert set(tab["attrs"]) == {"class", "href", "aria-current"}


def test_a_link_text_and_its_address_with_mark_up_come_out_escaped(draw):
    html = draw("{{ parts.link(text, href) }}", text=SCRIPT, href='" onclick="alert(3)')
    assert "<script" not in html and ESCAPED in html
    link = _only(html, "k-link")
    assert link["text"] == SCRIPT and set(link["attrs"]) == {"class", "href"}
    assert link["attrs"]["href"] == '" onclick="alert(3)'


def test_a_status_label_cannot_leave_its_attribute(draw):
    html = draw("{{ parts.status_dot('bad', label) }}", label='x" onmouseover="alert(4)"><script>alert(5)</script>')
    assert "<script" not in html
    dot = _only(html, "k-dot")
    assert set(dot["attrs"]) == {"class", "role", "aria-label"}
    assert dot["attrs"]["aria-label"] == 'x" onmouseover="alert(4)"><script>alert(5)</script>'


def test_a_reference_number_an_address_and_a_target_with_mark_up_come_out_escaped(draw):
    html = draw("{{ parts.ref_chip(number, href=href, controls=controls) }}", number=SCRIPT,
                href='"><script>alert(6)</script>', controls='" onfocus="alert(7)')
    assert "<script" not in html and ESCAPED in html
    chip = _only(html, "k-ref")
    assert chip["tag"] == "a" and set(chip["attrs"]) == {"class", "href", "aria-expanded", "aria-controls"}
    assert chip["attrs"]["aria-controls"] == '" onfocus="alert(7)'


def test_a_state_or_a_kind_never_becomes_a_class_of_its_own(draw):
    """Only the known states and kinds reach the class attribute; anything else is dropped, not written out."""
    html = draw("{{ parts.status_dot(evil, 'x') }}{{ parts.tag('x', kind=evil) }}", evil='ok" onclick="alert(8)')
    assert "onclick" not in html and "alert(8)" not in html
    assert _only(html, "k-dot")["classes"] == {"k-dot"} and _only(html, "k-tag")["classes"] == {"k-tag"}


def test_markdown_comes_out_as_the_text_it_is(draw):
    html = draw("{{ parts.page_head(text) }}{{ parts.tag(text) }}{{ parts.link(text, '#a') }}", text=MARKDOWN)
    for name in ("k-title", "k-tag", "k-link"):
        assert _only(html, name)["text"] == MARKDOWN
    assert "<strong" not in html and "<em" not in html and "<code" not in html
    assert 'href="javascript' not in html, "a link written in Markdown is text, not a link"


def test_what_the_caller_puts_in_a_block_is_the_callers_and_is_not_escaped_twice(draw):
    html = draw("{% call parts.card() %}<p>{{ text }}</p>{% endcall %}", text="Müller & Söhne <AG>")
    assert "<p>Müller &amp; Söhne &lt;AG&gt;</p>" in html


def test_the_parts_file_marks_nothing_safe_and_brings_no_script():
    source = re.sub(r"\{#.*?#\}", "", PARTS_HTML.read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"\|\s*safe\b", source), "a value is marked safe"
    assert "Markup(" not in source and "autoescape" not in source
    assert "<script" not in source.lower() and not re.search(r"\son[a-z]+\s*=", source), "the parts bring no script"
    assert "<style" not in source.lower() and "style=" not in source, "the look belongs in parts.css"


def test_the_ten_macros_are_there_under_their_names(docbridge_app):
    parts = docbridge_app.jinja_env.get_template("_parts.html").module
    expected = {
        "page": ("wide",), "page_head": ("title", "eyebrow", "lede"), "section": ("title",), "card": ("now",),
        "rowline": (), "tabs": ("items", "active"), "status_dot": ("state", "label"), "tag": ("text", "kind"),
        "ref_chip": ("number", "active", "href", "controls"), "link": ("text", "href", "dotted"),
    }
    for name, arguments in expected.items():
        macro = getattr(parts, name, None)
        assert macro is not None, f"parts.{name} is missing"
        assert tuple(macro.arguments) == arguments, f"parts.{name} takes {macro.arguments}"
    assert {n for n in dir(parts) if not n.startswith("_")} >= set(expected)


# --- 4: every class a macro draws has a rule ------------------------------------------------------------


def test_every_class_a_macro_draws_has_a_rule_in_parts_css(draw):
    html = draw(
        "{% call parts.page(wide=True) %}{{ parts.page_head('t', eyebrow='e', lede='l') }}"
        "{% call parts.section('s') %}{% call parts.card(now=True) %}{% call parts.rowline() %}a{% endcall %}"
        "{% endcall %}{% endcall %}{{ parts.tabs(items, 'mandate') }}"
        "{% for s in ['ok', 'warn', 'bad'] %}{{ parts.status_dot(s, 'l') }}{% endfor %}"
        "{% for k in ['', 'warn', 'bad', 'acc'] %}{{ parts.tag('t', kind=k) }}{% endfor %}"
        "{{ parts.ref_chip(1) }}{{ parts.ref_chip(2, active=True, href='#a') }}"
        "{{ parts.link('l', '#a') }}{{ parts.link('l', '#a', dotted=True) }}{% endcall %}", items=TABS)
    drawn = set().union(*(e["classes"] for e in _elements(html)))
    assert len(drawn) >= 25 and all(name.startswith("k-") for name in drawn), sorted(drawn)
    css = re.sub(r"/\*.*?\*/", "", PARTS_CSS.read_text(encoding="utf-8"), flags=re.S)
    ruled = set(re.findall(r"\.(k-[A-Za-z0-9_-]+)", "".join(re.findall(r"([^{}]+)\{", css))))
    assert drawn <= ruled, f"no rule for {sorted(drawn - ruled)}"
