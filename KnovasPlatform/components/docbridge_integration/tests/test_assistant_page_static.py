"""PMVP-22a: static checks on the ask page's own files (template, stylesheet, script).

ask.html marks nothing safe and takes every href from the route (url_for, FU-34); ask.css takes its values from the
brand tokens only; ask.js writes no markup from text and parses. This file is ASCII-only.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "src" / "web_interface"
TEMPLATE = WEB / "templates" / "ask.html"
INDEX = WEB / "templates" / "index.html"
CSS = WEB / "static" / "css" / "ask.css"
TOKENS = WEB / "static" / "css" / "knovas-tokens.css"
JS = WEB / "static" / "js" / "ask.js"


def _read(path):
    return path.read_text(encoding="utf-8")


def _css_without_comments():
    return re.sub(r"/\*.*?\*/", "", _read(CSS), flags=re.S)


def test_the_template_marks_nothing_safe():
    for template in (TEMPLATE, INDEX):
        text = _read(template)
        assert "|safe" not in text.replace(" ", "") and "Markup" not in text, template.name
        assert "autoescape false" not in text, template.name


def test_every_href_of_the_page_is_built_by_the_app():
    hrefs = re.findall(r'href="([^"]*)"', _read(TEMPLATE))
    assert hrefs
    for href in hrefs:
        assert re.fullmatch(r"\{\{ (url_for\([^}]*\)|p\.href|s\.passage\.href) \}\}(\?v=\{\{ asset_version \}\})?",
                            href), href


def test_the_form_actions_are_built_by_the_app():
    for template in (TEMPLATE, INDEX):
        for action in re.findall(r'action="([^"]*)"', _read(template)):
            assert action == "{{ url_for('assistant.ask') }}", (template.name, action)


def test_the_stylesheet_names_no_colour():
    css = _css_without_comments()
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.search(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(", css)
    named = r"\b(?:white|black|red|green|blue|gray|grey|yellow|orange|navy|silver|purple)\b"
    for value in re.findall(r":\s*([^;{}]+);", css):
        assert not re.search(named, re.sub(r"var\([^)]*\)", "", value)), value


def test_the_stylesheet_takes_only_tokens_the_brand_declares():
    declared = set(re.findall(r"(--k-[a-z0-9-]+)\s*:", _read(TOKENS)))
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", _css_without_comments()))
    assert used and used <= declared, sorted(used - declared)


def test_the_stylesheet_writes_out_only_own_sizes():
    """Lengths are tokens; written out are only lines (1, 2, 3 px) and the number box of a passage (18 px)."""
    css = _css_without_comments()
    lengths = set(re.findall(r"(?<![\w.-])(\d+(?:\.\d+)?)(px|rem|em)\b", css))
    assert lengths <= {("1", "px"), ("2", "px"), ("3", "px"), ("18", "px")}, sorted(lengths)


def test_the_reference_and_the_stop_do_not_break_apart():
    css = _css_without_comments()
    rule = re.search(r"\.ask-tail\s*\{([^}]*)\}", css)
    assert rule and "white-space: nowrap" in rule.group(1)
    # The tail holds the chip and the stop only: no text of the statement is kept from wrapping.
    tail = re.search(r'<span class="ask-tail">(.*?)</span></p>', _read(TEMPLATE))
    assert tail and tail.group(1) == ("{{ parts.ref_chip(s.number, active=True, controls=s.quote_id) }}"
                                      "{{ s.stop }}")


@pytest.mark.parametrize("selector", [".ask-statement", ".ask-quote-text", ".ask-quote-doc", ".ask-source-name"])
def test_long_words_wrap_anywhere(selector):
    css = _css_without_comments()
    rule = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert rule and "overflow-wrap: anywhere" in rule.group(1), selector


def test_nothing_but_the_tail_is_kept_from_wrapping():
    css = _css_without_comments()
    held = [m.group(1).strip() for m in re.finditer(r"([^{}]+)\{[^}]*white-space:\s*nowrap", css)]
    # .ask-sr is the screen readers' text, clipped to one pixel: it never takes room.
    assert sorted(held) == [".ask-sr", ".ask-tail"]


def test_titles_and_quotes_are_isolated():
    text = _read(TEMPLATE)
    assert '<span class="ask-quote-doc" dir="auto">{{ s.passage.title }}</span>' in text
    assert '\u00ab<span dir="auto">{{ s.quote }}</span>\u00bb' in text
    assert re.search(r'<a class="k-link" href="\{\{ p\.href \}\}"[^>]*\bdir="auto"[^>]*>\{\{ p\.title \}\}</a>', text)
    assert re.search(r'\.ask-main \[dir="auto"\]\s*\{\s*unicode-bidi: isolate;\s*\}', _css_without_comments())


def test_the_script_writes_no_markup_from_text():
    source = _read(JS)
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                 "setAttribute('href'", 'setAttribute("href"', ".href ="):
        assert sink not in source, sink


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_script_parses():
    result = subprocess.run(["node", "--check", str(JS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
