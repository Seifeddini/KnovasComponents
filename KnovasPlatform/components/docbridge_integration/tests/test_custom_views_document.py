"""Custom views: the document and the bridge client (SS-626).

The entry checks are tested over full combinations (decision D-4: no formal model): every refused construct in
lower/upper/mixed case, with quoted, unquoted and missing attribute values and with whitespace variants.
"""

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from types import MappingProxyType

import pytest

from custom_views import document
from custom_views.document import build_document, check_entry_markup, check_view, new_nonce
from custom_views.manifest import ManifestError, parse_manifest
from custom_views.registry import View, ViewFile

POLICY = (
    "default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; img-src data:; font-src data:; "
    "base-uri 'none'; form-action 'none'; require-trusted-types-for 'script'"
)
BRIDGE = Path(document.__file__).with_name("bridge_client.js")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def make_view(entry="<p>Hallo</p>", scripts=(), styles=()):
    """A view snapshot as the registry hands it out; ``scripts``/``styles`` are (name, text) pairs in order."""
    manifest = parse_manifest("board", json.dumps({
        "format": 1,
        "title": {"de": "Tafel", "en": "Board"},
        "roles": ["*"],
        "entry": "index.html",
        "scripts": [name for name, _ in scripts],
        "styles": [name for name, _ in styles],
        "reads": {"Project": ["status"]},
    }).encode("utf-8"))
    files = {}
    for name, content in (("index.html", entry), *scripts, *styles):
        data = content if isinstance(content, bytes) else content.encode("utf-8")
        files[name] = ViewFile(name=name, data=data, sha256="0" * 64, mtime=0.0)
    return View(manifest=manifest, files=MappingProxyType(files))


def bodies(doc, tag, nonce):
    """The text of each ``<tag nonce>`` element where an HTML parser ends it: the first ``</tag`` + [ws / >]."""
    start = f'<{tag} nonce="{nonce}">'
    end = re.compile(rf"</{tag}[\t\n\f\r />]", re.I)
    found, pos = [], 0
    while (i := doc.find(start, pos)) >= 0:
        match = end.search(doc, i + len(start))
        found.append(doc[i + len(start):match.start()])
        pos = match.end()
    return found


class StartTags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def start_tags(doc):
    parser = StartTags()
    parser.feed(doc)
    parser.close()
    return parser.tags


# ── the document ────────────────────────────────────────────────────────────


def test_the_document_has_its_exact_shape_and_order():
    nonce = new_nonce()
    view = make_view(
        entry='<main id="app"></main>',
        scripts=[("app.js", "knovas.ready(start);"), ("more.js", "var b = 2;")],
        styles=[("a.css", "p{color:red}"), ("b.css", "h1{margin:0}")],
    )
    bridge = BRIDGE.read_text(encoding="utf-8")

    assert build_document(view, nonce) == (
        '<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{POLICY.format(nonce=nonce)}">'
        f'<style nonce="{nonce}">p{{color:red}}</style><style nonce="{nonce}">h1{{margin:0}}</style>'
        '</head><body><main id="app"></main>'
        f'<script nonce="{nonce}">{bridge}</script>'
        f'<script nonce="{nonce}">knovas.ready(start);</script><script nonce="{nonce}">var b = 2;</script>'
        "</body></html>"
    )


def test_the_policy_tag_is_the_first_element_in_head_after_the_charset():
    nonce = new_nonce()
    tags = start_tags(build_document(make_view(styles=[("a.css", "p{}")]), nonce))

    assert [tag for tag, _ in tags[:4]] == ["html", "head", "meta", "meta"]
    assert tags[2][1] == {"charset": "utf-8"}
    assert tags[3][1] == {"http-equiv": "Content-Security-Policy", "content": POLICY.format(nonce=nonce)}


def test_every_script_and_style_carries_the_same_nonce():
    nonce = new_nonce()
    view = make_view(scripts=[("a.js", "1;"), ("b.js", "2;")], styles=[("a.css", "p{}"), ("b.css", "h1{}")])
    doc = build_document(view, nonce)

    elements = re.findall(r"<(script|style)\b([^>]*)>", doc, re.I)
    assert [name for name, _ in elements] == ["style", "style", "script", "script", "script"]
    assert {attrs for _, attrs in elements} == {f' nonce="{nonce}"'}
    assert f"'nonce-{nonce}'" in doc


def test_two_builds_give_two_nonces():
    view = make_view(scripts=[("a.js", "1;")])
    first, second = new_nonce(), new_nonce()
    assert first != second
    assert re.fullmatch(r"[A-Za-z0-9_-]{24}", first) and re.fullmatch(r"[A-Za-z0-9_-]{24}", second)

    one, two = build_document(view, first), build_document(view, second)
    assert set(re.findall(r'nonce="([^"]*)"', one)) == {first}
    assert set(re.findall(r'nonce="([^"]*)"', two)) == {second}
    assert one.replace(first, "N") == two.replace(second, "N")


@pytest.mark.parametrize(
    "nonce", ["", "short", 'abcdefghijklmnop" onload="x', "abcdefghijklmnop'x", "abc def ghijklmnopq"]
)
def test_an_unsafe_nonce_is_refused(nonce):
    with pytest.raises(ValueError):
        build_document(make_view(), nonce)


def test_a_script_file_cannot_close_its_element():
    nonce = new_nonce()
    hostile = (
        'var a = "</script><script>alert(1)</script>";\n'
        "var b = '</SCRIPT >', c = `</ScRiPt\n>`, d = \"</script/\";\n"
        'var e = "<!--<script>", f = "<!-- -->";\n'
    )
    doc = build_document(make_view(scripts=[("app.js", hostile), ("next.js", "var z = 1;")]), nonce)

    found = bodies(doc, "script", nonce)
    assert len(found) == 3  # the bridge, then both files: none of them ends early
    assert found[1] == (
        'var a = "<\\/script><script>alert(1)<\\/script>";\n'
        "var b = '<\\/SCRIPT >', c = `<\\/ScRiPt\n>`, d = \"<\\/script/\";\n"
        'var e = "<\\!--<script>", f = "<\\!-- -->";\n'
    )
    assert found[2] == "var z = 1;"
    assert all("<!--" not in body for body in found)  # no escaped script state, so `</script` alone ends it


def test_a_style_file_cannot_close_its_element():
    nonce = new_nonce()
    hostile = 'p::after { content: "</style><script>alert(1)</script>"; } /* </STYLE > */'
    doc = build_document(make_view(styles=[("app.css", hostile), ("next.css", "h1{}")]), nonce)

    assert bodies(doc, "style", nonce) == [
        'p::after { content: "<\\/style><script>alert(1)<\\/script>"; } /* <\\/STYLE > */',
        "h1{}",
    ]


def test_the_document_holds_no_address_unless_a_view_file_does():
    plain = build_document(
        make_view(scripts=[("app.js", "knovas.ready(function () {});")], styles=[("app.css", "p { color: red }")]),
        new_nonce(),
    )
    assert not re.search(r"https?:", plain, re.I)

    script = 'var link = "https://example.atlassian.net/browse/X-1";'
    with_link = build_document(make_view(scripts=[("app.js", script)]), new_nonce())
    assert script in with_link
    assert not re.search(r"https?:", with_link.replace(script, ""), re.I)


def test_a_byte_order_mark_is_dropped():
    nonce = new_nonce()
    view = make_view(entry="\ufeff<p>x</p>", scripts=[("a.js", "\ufeffvar a;")], styles=[("a.css", "\ufeffp{}")])
    doc = build_document(view, nonce)
    assert "\ufeff" not in doc
    assert "<body><p>x</p><script" in doc
    check_view(view)


@pytest.mark.parametrize("bad", ["index.html", "app.js", "app.css"])
def test_check_view_refuses_a_file_that_is_not_utf8(bad):
    files = {"index.html": "<p>Grüsse</p>", "app.js": "var s = 'ä';", "app.css": "p::after{content:'ü'}"}
    files[bad] = files[bad].encode("latin-1")
    view = make_view(entry=files["index.html"], scripts=[("app.js", files["app.js"])],
                     styles=[("app.css", files["app.css"])])
    with pytest.raises(ManifestError) as caught:
        check_view(view)
    assert str(caught.value) == f"Datei „{bad}“ ist nicht UTF-8-kodiert."


def test_check_view_checks_the_entry_markup():
    with pytest.raises(ManifestError):
        check_view(make_view(entry="<p onclick=f()>x</p>"))
    check_view(make_view(entry="<p>x</p>", scripts=[("a.js", "</script>")]))  # scripts are escaped, not checked


# ── the bridge client file ─────────────────────────────────────────────────


def test_the_bridge_client_holds_no_end_tag_comment_opener_or_address():
    text = BRIDGE.read_text(encoding="utf-8")
    assert "window.knovas" in text or '"knovas"' in text
    assert not re.search(r"</script", text, re.I)
    assert "<!--" not in text
    assert not re.search(r"https?:", text, re.I)
    assert document.BRIDGE_CLIENT == text


# ── the entry check: refused elements ──────────────────────────────────────

ELEMENT_MESSAGE = "Die Einstiegsdatei (entry) darf kein Element „{}“ enthalten."
REFUSED_ELEMENTS = [
    "script", "link", "base", "meta", "iframe", "frame", "frameset", "object", "embed", "applet", "form",
    "html", "head", "body", "style", "textarea", "title", "xmp", "noembed", "noframes", "noscript", "plaintext",
]
OPENINGS = ["<", "< ", "<\n", "<\t", "<\r\n ", "</", "< / ", "</\n"]
CLOSINGS = [">", "/>", " >", "\n>", " a>", " a=1>", ' a="1">', " a='1'>", "\ta = '1' >", '\na\n=\n"1"\n>']
CONTEXTS = ["{}", "<p>vor</p>{}<p>nach</p>", "<!-- {} -->", '<p title="{}">x</p>', "<svg>{}</svg>", "<div>{}"]


def case_variants(name):
    return {name.lower(), name.upper(), name.capitalize(),
            "".join(c.upper() if i % 2 else c for i, c in enumerate(name))}


def messages(cases):
    """Each case mapped to its refusal message, or None where it passed."""
    result = {}
    for case in cases:
        try:
            check_entry_markup(case)
        except ManifestError as error:
            result[case] = str(error)
        else:
            result[case] = None
    return result


@pytest.mark.parametrize("name", REFUSED_ELEMENTS)
def test_every_refused_element_in_every_spelling_is_refused(name):
    cases = [
        context.format(opening + spelled + closing)
        for spelled in case_variants(name)
        for opening in OPENINGS
        for closing in CLOSINGS
        for context in CONTEXTS
    ]
    cases += [opening + spelled for spelled in case_variants(name) for opening in OPENINGS]  # cut off at the end

    wrong = {case: got for case, got in messages(cases).items() if got != ELEMENT_MESSAGE.format(name)}
    assert wrong == {}


def test_a_doctype_is_refused_in_every_spelling():
    cases = ["<!DOCTYPE html>", "<!doctype html>", "<!DocType>", "< ! DOCTYPE html>", "<!\ndoctype\nhtml>",
             "<p>x</p><!DOCTYPE html>"]
    assert messages(cases) == {case: ELEMENT_MESSAGE.format("!DOCTYPE") for case in cases}


def test_names_that_only_start_like_a_refused_element_are_allowed():
    cases = ["<meter value=1></meter>", "<header></header>", "<basefont>", "<formula-x></formula-x>",
             "<script-like></script-like>", "<bodyx></bodyx>", "<p>Metadaten</p>", "<p>Scripts &amp; Links</p>"]
    assert messages(cases) == {case: None for case in cases}


# ── the entry check: refused attributes ────────────────────────────────────

EVENT_MESSAGE = "Die Einstiegsdatei (entry) darf keine Ereignis-Attribute („on…“) enthalten."
STYLE_MESSAGE = "Die Einstiegsdatei (entry) darf kein Attribut „style“ enthalten."
ADDRESS_MESSAGE = (
    "In der Einstiegsdatei (entry) darf das Attribut „{}“ nur auf „#…“ verweisen (bei img auch auf „data:“)."
)
ATTRIBUTE_NAME_MESSAGE = (
    "In der Einstiegsdatei (entry) darf „attributeName“ kein Adress-, Ereignis- oder Stil-Attribut nennen."
)
UNREADABLE_MESSAGE = "Die Einstiegsdatei (entry) enthält Markup, das nicht gelesen werden kann."
UNCLOSED_MESSAGE = "In der Einstiegsdatei (entry) ist das Element „{}“ nicht geschlossen."

HOSTS = ["p", "img", "a", "custom-el", "IMG", "svg", "details"]
SEPARATORS = [" ", "\n", "\t", "\r\n", "/", ' x="1"', " x='1'"]  # the last two: right after a quoted value
VALUES = ['="f()"', "='f()'", "=f()", "", ' = "f()"', "\n=\n'f()'", '=""']
ENDINGS = [">", "/>", " >"]


def attribute_cases(names, values=VALUES, hosts=HOSTS):
    return [
        f"<{host}{separator}{name}{value}{ending}x</{host}>"
        for host in hosts
        for separator in SEPARATORS
        for name in names
        for value in values
        for ending in ENDINGS
    ]


def test_every_event_attribute_is_refused():
    names = ["onclick", "ONCLICK", "OnError", "onload", "onfocusin", "on", "onx", "xlink:onclick"]
    cases = attribute_cases(names)
    assert {case: got for case, got in messages(cases).items() if got != EVENT_MESSAGE} == {}


def test_every_style_attribute_is_refused():
    cases = attribute_cases(["style", "STYLE", "Style"])
    assert {case: got for case, got in messages(cases).items() if got != STYLE_MESSAGE} == {}


ADDRESS_ATTRIBUTES = ["src", "href", "action", "formaction", "srcset", "poster", "data", "background", "ping", "cite",
                      "longdesc", "lowsrc", "dynsrc", "codebase", "classid", "archive", "manifest", "icon"]
BAD_TARGETS = ["https://example.org/x", "HTTP://example.org", "//example.org/x", "javascript:alert(1)",
               "JavaScript:alert(1)", "x.png", "/x", "?x", "", " #x", "&#106;avascript:alert(1)", "=#x"]
DATA_TARGETS = ["data:image/png;base64,AAAA", "DATA:image/gif,x", "Data:text/html,x"]


def quoted(target):
    forms = [f'="{target}"', f"='{target}'", f' = "{target}"', f"\n=\n'{target}'"]
    if target and not re.search(r"[\s\"'>]", target) and not target.startswith("="):  # href==#x is unreadable
        forms.append(f"={target}")
    return forms


def address_cases(attribute, targets, hosts):
    names = {attribute, attribute.upper()} | ({"xlink:href", "XLINK:HREF"} if attribute == "href" else set())
    return [
        f"<{host} {name}{form}{ending}x</{host}>"
        for host in hosts
        for name in names
        for target in targets
        for form in quoted(target)
        for ending in (">", "/>")
    ]


@pytest.mark.parametrize("attribute", ADDRESS_ATTRIBUTES)
def test_every_address_attribute_may_only_point_into_the_document(attribute):
    hosts = ["a", "img", "video", "custom-el", "IMG", "svg"]
    refused = address_cases(attribute, BAD_TARGETS, hosts)
    refused += [f"<{host} {name}{ending}x</{host}>" for host in hosts for name in (attribute, attribute.upper())
                for ending in (">", "/>", " >")]  # no value at all
    data_hosts = [h for h in hosts if h.lower() != "img" or attribute not in ("src", "href")]
    refused += address_cases(attribute, DATA_TARGETS, data_hosts)
    message = ADDRESS_MESSAGE.format(attribute)
    assert {case: got for case, got in messages(refused).items() if got != message} == {}

    allowed = address_cases(attribute, ["#x", "#", "&#35;top"], hosts)
    assert {case: got for case, got in messages(allowed).items() if got is not None} == {}


def test_an_img_may_hold_a_data_address():
    allowed = address_cases("src", DATA_TARGETS, ["img", "IMG"]) + address_cases("href", DATA_TARGETS, ["img"])
    assert {case: got for case, got in messages(allowed).items() if got is not None} == {}


def test_svg_addresses_are_checked_like_html_ones():
    refused = {
        '<svg><image href="https://example.org/x.png"/></svg>': ADDRESS_MESSAGE.format("href"),
        '<svg><image xlink:href="data:image/png,x"/></svg>': ADDRESS_MESSAGE.format("href"),
        "<svg><a xlink:href=javascript:f()><text>x</text></a></svg>": ADDRESS_MESSAGE.format("href"),
        '<svg><use href="//example.org/s.svg#i"/></svg>': ADDRESS_MESSAGE.format("href"),
        "<svg><feImage href='x.png'/></svg>": ADDRESS_MESSAGE.format("href"),
    }
    assert messages(refused) == refused
    allowed = ['<svg><use href="#icon"/></svg>', "<svg><a xlink:href='#top'><text>x</text></a></svg>"]
    assert messages(allowed) == {case: None for case in allowed}


def test_an_animation_may_not_target_an_address_event_or_style_attribute():
    targets = ["href", "HREF", "xlink:href", " href ", "src", "onclick", "OnLoad", "style"]
    cases = [
        template.format(target)
        for target in targets
        for template in ('<svg><animate attributeName="{}" to="x"/></svg>',
                         "<svg><set attributename='{}' to='x'></set></svg>",
                         "<svg><a><animate ATTRIBUTENAME={} values=x /></a></svg>")
        if not (" " in target and "={}" in template)
    ]
    assert {case: got for case, got in messages(cases).items() if got != ATTRIBUTE_NAME_MESSAGE} == {}
    allowed = ['<svg><animate attributeName="opacity" to="0"/></svg>', "<svg><set attributeName=fill to=red /></svg>"]
    assert messages(allowed) == {case: None for case in allowed}


def test_values_every_reader_reads_alike_are_allowed():
    allowed = [
        "<img src=data:image/png;base64,AA== alt=x>",  # "=" inside an unquoted value
        '<p title="a==b" data-x="Q&A, AT&T, &amp;, &notin;, &copy; 2026, &lt">x</p>',
        "<p title='x\xa0y'>x</p>",  # any space inside quotes belongs to the value
    ]
    assert messages(allowed) == {case: None for case in allowed}


# ── the entry check: unreadable and unclosed markup ────────────────────────


@pytest.mark.parametrize("markup", [
    '<p class="x',
    "<p>x</p><p",
    "<p>x<",
    "<a href='#x'",
    "<p>x</p><!-- offen",
    "<![CDATA[<img src=x onerror=f()>]]>",
    "<?xml version='1.0'?>",
    "<!--><img src=x onerror=f()>-->",
    "<!---><img src=x onerror=f()>-->",
    "<!-- a -- ><p>x</p>",
    "<p>\x00</p>",
    # Comment ends and declarations that the parser versions read differently from each other or from a browser.
    "<!-- a --!><img src=x onerror=f()>-->",
    "<!-- a --!> b -->",
    "<!-->x",
    "<!--->x",
    "<![x>",
    # Found by fuzzing against an HTML5 parser: a browser ends these tags at the first ">"; the older parser read on
    # and would not have seen the <img>.
    '<p a=\xa0"x><img src=x onerror=f()>">',
    '<p a=="x><img src=x onerror=f()>">',
    '<p a=x\xa0b="y>z">x</p>',
    # Unquoted values that start with "=" (the older parser drops it) or hold a quote, "<", "`" or non-ASCII
    # whitespace; attribute names with non-ASCII whitespace.
    "<p title==x>x</p>",
    "<a href==#x>x</a>",
    "<a href = = '#x'>x</a>",
    "<img src==data:image/png,x>",
    "<svg a=x\xa0b/>",
    "<p a=x\N{IDEOGRAPHIC SPACE}b>x</p>",
    "<p a=x\x85b>x</p>",
    "<p a=x\x0bb>x</p>",
    "<p a=x\"y>x</p>",
    "<p a=x'y>x</p>",
    "<p a=x<y>x</p>",
    "<p a=x`y>x</p>",
    '<p a="1"\xa0b="2">x</p>',
    "<p a\xa0b=1>x</p>",
    # Named references without ";" that a browser keeps in a value and html.unescape decodes.
    '<p title="&notx">x</p>',
    "<p title='&amp=1'>x</p>",
    '<a href="#a&copyx">x</a>',
    "<p title=&notin>x</p>",
    # End tags: the older parser ends them at the first ">", a browser skips quoted values first.
    "</p title='>'>",
    "<p>x</p x>",
    "</td<use src=x>",
    "<p>x</p/>",
    # The older parser reads "\s" as any Unicode space: it closes <math> at these, a browser does not (a name
    # "math\xa0", or a bogus comment), and the bridge client would become a MathML element.
    "<math></math\xa0>",
    "<math></math\x0b>",
    "<math></ math>",
    "<svg></\xa0svg>",
    "<template></template\u3000>",
])
def test_markup_the_parser_cannot_read_is_refused(markup):
    """Refused on every Python version; only the message may differ (3.11.15+ has the rewritten parser)."""
    with pytest.raises(ManifestError):
        check_entry_markup(markup)


def test_a_tag_the_two_readings_disagree_on_is_refused(monkeypatch):
    # A guard: should the parser and the browser reading ever differ on an attribute, the tag is refused.
    monkeypatch.setattr(document, "_browser_tag", lambda tag: ([("title", "other")], False))
    with pytest.raises(ManifestError) as caught:
        check_entry_markup('<p title="x">x</p>')
    assert str(caught.value) == UNREADABLE_MESSAGE


def test_an_end_tag_may_hold_whitespace_after_its_name():
    allowed = ["<p>x</p >", "<p>x</P\n>", "<b>x</b\t>"]
    assert messages(allowed) == {case: None for case in allowed}


@pytest.mark.parametrize("markup", [
    "<svg/viewBox='0 0 1 1'><p>x</p>",  # a "/" between attributes does not close
    "<svg/ >",
])
def test_whether_a_tag_closes_itself_is_read_as_a_browser_reads_it(markup):
    with pytest.raises(ManifestError) as caught:
        check_entry_markup(markup)
    assert str(caught.value) == UNCLOSED_MESSAGE.format("svg")


@pytest.mark.parametrize("tag", [
    "<p title==x>",  # an unquoted value that starts with "="
    "<p a=x\x85b>",  # ... or holds non-ASCII whitespace
    "<p a=x\xa0b>",
    '<p a=x"y>',  # ... or a quote, "<" or "`"
    "<p a=x'y>",
    "<p a=x<y>",
    "<p a=x`y>",
    "<p a\xa0b=1>",  # a name with non-ASCII whitespace
    '<p title="&notx">',  # a named reference a browser keeps and html.unescape decodes
    "<p title='&amp=1'>",
    "<p title=&notin>",
    '<p a=x>y">',  # a browser ends the tag before the parser's end
    '<p a=">',  # ... or after it
])
def test_the_browser_reading_refuses_what_readers_could_read_differently(tag):
    # Each rule on its own: in the whole check another layer (the comparison of both readings) may refuse first.
    with pytest.raises(document._Refused):
        document._browser_tag(tag)


def test_the_browser_reading_of_a_plain_tag():
    assert document._browser_tag("<svg a='>' b/>") == ([("a", ">"), ("b", None)], True)
    assert document._browser_tag('<p t="&amp;&notin;&copy; Q&A" u=a==b>') == (
        [("t", "&∉© Q&A"), ("u", "a==b")], False)


def test_a_comment_a_browser_ends_earlier_is_refused_by_the_parser_guard():
    # The raw scan refuses these first; the guard stays for a parser that reads comments otherwise.
    parser = document._EntryParser()
    for data in (">x", "->x", " a --!> b "):
        with pytest.raises(document._Refused):
            parser.handle_comment(data)
    parser.handle_comment(" Kopf -> Inhalt ")


def test_a_tag_the_parser_passes_on_as_text_is_refused():
    # A guard for other Python versions: on 3.13.5 no input reaches handle_data with a tag (the checks before
    # refuse it), but a parser that hands a malformed tag over as text must not get it past the check.
    parser = document._EntryParser()
    with pytest.raises(document._Refused):
        parser.handle_data("<img src=x onerror=f()>")
    parser.handle_data("<")  # a lone "<" is text


@pytest.mark.parametrize("name", ["svg", "math", "template"])
def test_an_element_that_changes_how_the_rest_is_read_must_be_closed(name):
    for spelled in case_variants(name):
        with pytest.raises(ManifestError) as caught:
            check_entry_markup(f"<div><{spelled}><p>x</p></div>")
        assert str(caught.value) == UNCLOSED_MESSAGE.format(name)
    check_entry_markup(f"<{name}><p>x</p></{name.upper()}>")


def test_self_closing_closes_svg_and_math_but_not_a_template():
    check_entry_markup("<svg/><MATH />")
    with pytest.raises(ManifestError) as caught:
        check_entry_markup("<template/>")  # a browser ignores "/>" on HTML elements
    assert str(caught.value) == UNCLOSED_MESSAGE.format("template")


def test_a_realistic_entry_passes():
    check_entry_markup(
        "<!-- Kopf -> Inhalt => Fuss -->\n"
        '<header><h1>Übersicht</h1><a href="#inhalt" class="skip">zum Inhalt</a></header>\n'
        '<main id="inhalt" data-on="x" data-src="y" aria-label="Projekte">\n'
        '  <p class=hint>Online seit 2026 &amp; one=1, a < b, 3 > 2, &lt;script&gt; als Text</p>\n'
        '  <img src="data:image/png;base64,iVBORw0KGgo=" alt="">\n'
        '  <svg viewBox="0 0 10 10" aria-hidden="true"><use href="#icon"/>'
        '<animate attributeName="opacity" from="0" to="1" dur="1s"/></svg>\n'
        '  <template id="row"><tr><td></td></tr></template>\n'
        '  <table><tbody id="rows"></tbody></table>\n'
        '  <label for="q">Suche</label><input id="q" type="search" placeholder="Suche">\n'
        '  <button type="button" disabled>Los</button><meter value="0.5"></meter>\n'
        "</main>\n"
    )


# ── the bridge client, run in node ─────────────────────────────────────────

BRIDGE_SCENARIO = r"""
const vm = require("vm");
const fs = require("fs");
const source = fs.readFileSync(process.argv[2], "utf8");
const listeners = [];
const parent = {name: "parent"};
const stranger = {name: "stranger"};
const window = {
  parent,
  addEventListener(type, fn) { if (type === "message") listeners.push(fn); },
  removeEventListener(type, fn) { const i = listeners.indexOf(fn); if (i >= 0) listeners.splice(i, 1); },
};
const context = vm.createContext({window});
const globalsBefore = new Set(Object.getOwnPropertyNames(context));
vm.runInContext(source, context);
const result = {events: []};
result.newGlobals = Object.getOwnPropertyNames(context).filter((n) => !globalsBefore.has(n));
const own = ["parent", "addEventListener", "removeEventListener"];
result.windowKeys = Object.keys(window).filter((k) => !own.includes(k));
const k = window.knovas;
result.apiKeys = Object.keys(k).sort();
result.frozen = Object.isFrozen(k);
const d = Object.getOwnPropertyDescriptor(window, "knovas");
result.replaceable = d.writable || d.configurable;
result.listenersBefore = listeners.length;

function dispatch(source, data, ports) { for (const fn of listeners.slice()) fn({source, data, ports: ports || []}); }
function settle(promise) {
  return promise.then((value) => ({ok: true, value: value === undefined ? "undefined" : value}),
                      (error) => ({ok: false, message: error.message, status: error.status,
                                   isError: Object.prototype.toString.call(error) === "[object Error]"}));
}

(async () => {
  const received = [];
  k.ready(() => result.events.push("ready-early"));
  const early = settle(k.read({type: "A"}));

  const spare = new MessageChannel();
  spare.port1.onmessage = (e) => result.events.push("spare-port-used");
  dispatch(stranger, {type: "knovas.port"}, [spare.port2]);   // not from the parent
  dispatch(parent, {type: "other"}, [spare.port2]);           // not the port message
  dispatch(parent, {type: "knovas.port"}, []);                // no port

  const main = new MessageChannel();
  const page = main.port1;
  page.onmessage = (e) => {
    const m = e.data;
    received.push(m);
    if (m.kind === "read" && m.query.type === "A") {
      page.postMessage({id: 999, ok: true, data: "wrong"});   // unknown id: ignored
      page.postMessage("garbage");
      page.postMessage({id: m.id, ok: true, data: {rows: [1, 2]}});
    } else if (m.kind === "read") {
      page.postMessage({id: m.id, ok: false, error: "Zu viele Abfragen.", status: 429});
    } else if (m.kind === "open" && m.url === "#gut") {
      page.postMessage({id: m.id, ok: true});
    } else if (m.kind === "open") {
      page.postMessage({id: m.id, ok: false, error: "Adresse nicht erlaubt.", status: 403});
    } else if (m.kind === "resize") {
      page.postMessage({id: m.id, ok: true});                 // an answer nobody waits for
    }
  };
  dispatch(parent, {type: "knovas.port"}, [main.port2]);      // accepted
  result.listenersAfter = listeners.length;
  const late = new MessageChannel();
  late.port1.onmessage = (e) => result.events.push("late-port-used");
  dispatch(parent, {type: "knovas.port"}, [late.port2]);      // only once

  k.ready(() => result.events.push("ready-late"));
  const opened = settle(k.open("#gut"));
  const refused = settle(k.open("https-less"));
  const limited = settle(k.read({type: "B"}));
  result.resizeReturns = k.resize(320) === undefined ? "undefined" : "value";
  result.results = {early: await early, opened: await opened, refused: await refused, limited: await limited};
  await new Promise((resolve) => setTimeout(resolve, 100));
  result.received = received;
  for (const port of [page, main.port2, spare.port1, spare.port2, late.port1, late.port2]) port.close();
  process.stdout.write(JSON.stringify(result));
})().catch((error) => { process.stdout.write(JSON.stringify({crash: String(error && error.stack)})); });
"""


def run_node(tmp_path, script, *args):
    path = tmp_path / "scenario.js"
    path.write_text(script, encoding="utf-8")
    completed = subprocess.run([NODE, str(path), *map(str, args)], capture_output=True, text=True,
                               encoding="utf-8", timeout=60)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@needs_node
def test_the_bridge_client_speaks_the_protocol(tmp_path):
    result = run_node(tmp_path, BRIDGE_SCENARIO, BRIDGE)

    assert "crash" not in result, result.get("crash")
    assert result["newGlobals"] == []
    assert result["windowKeys"] == ["knovas"]
    assert result["apiKeys"] == ["open", "read", "ready", "resize"]
    assert result["frozen"] is True
    assert result["replaceable"] is False
    assert result["listenersBefore"] == 1 and result["listenersAfter"] == 0
    assert result["received"] == [
        {"id": 1, "kind": "read", "query": {"type": "A"}},
        {"id": 2, "kind": "open", "url": "#gut"},
        {"id": 3, "kind": "open", "url": "https-less"},
        {"id": 4, "kind": "read", "query": {"type": "B"}},
        {"id": 5, "kind": "resize", "height": 320},
    ]
    assert result["results"] == {
        "early": {"ok": True, "value": {"rows": [1, 2]}},
        "opened": {"ok": True, "value": "undefined"},
        "refused": {"ok": False, "message": "Adresse nicht erlaubt.", "status": 403, "isError": True},
        "limited": {"ok": False, "message": "Zu viele Abfragen.", "status": 429, "isError": True},
    }
    assert result["resizeReturns"] == "undefined"
    assert result["events"] == ["ready-early", "ready-late"]


ESCAPE_SCENARIO = r"""
const vm = require("vm");
const fs = require("fs");
const [original, escaped] = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
function run(code) { const c = vm.createContext({}); vm.runInContext(code, c); return JSON.stringify(c.out); }
process.stdout.write(JSON.stringify({original: run(original), escaped: run(escaped)}));
"""


@needs_node
def test_escaped_scripts_keep_their_meaning(tmp_path):
    original = (
        'var out = ["</script>", \'</SCRIPT >\', `</ScRiPt\n>`, "<!--x-->", "a<!--b", /<\\/script>/i.source,\n'
        '  /<!--/.test("<!--"), "</script" + "/"];\n'
        "// a comment with </script> and <!-- inside\n"
        "/* </script> <!-- */\n"
    )
    nonce = new_nonce()
    [escaped] = bodies(build_document(make_view(scripts=[("app.js", original)]), nonce), "script", nonce)[1:]
    assert escaped != original
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps([original, escaped]), encoding="utf-8")

    result = run_node(tmp_path, ESCAPE_SCENARIO, inputs)
    assert result["escaped"] == result["original"]
