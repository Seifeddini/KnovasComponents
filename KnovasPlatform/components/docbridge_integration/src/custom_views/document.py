"""The document of a custom view: one HTML page per request, shown in a sandboxed ``srcdoc`` iframe.

Two lines of defence against script that arrives inside data (a Jira title, say) or inside a view's files:
the policy, a Content-Security-Policy meta tag that lets only this build's nonce'd scripts and styles run, and
the entry check (``check_entry_markup``), which the registry applies to every view it scans, so an entry with
script, outside addresses, event or style attributes makes the view invalid.

The entry check reads the markup twice: a case-insensitive scan of the raw text for refused elements (wherever
they stand, also inside comments and attribute values), then the standard library's HTML parser for attributes,
cross-checked by a reading of each start tag as a browser tokenizes it. Markup the two could read differently is
refused as unreadable.
"""
from __future__ import annotations

import html
import re
import secrets
from html.entities import html5 as html5_entities
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

from custom_views.manifest import ManifestError

if TYPE_CHECKING:
    from custom_views.registry import View

BRIDGE_CLIENT = Path(__file__).with_name("bridge_client.js").read_text(encoding="utf-8")

POLICY = (
    "default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; img-src data:; font-src data:; "
    "base-uri 'none'; form-action 'none'; require-trusted-types-for 'script'"
)
_NONCE = re.compile(r"[A-Za-z0-9_-]{16,}")

# Script and its carriers, what loads or submits, the document frame, and every element whose content a browser
# does not read as markup (raw text, RCDATA, plaintext): without them the parser and a browser agree where a tag is.
REFUSED_ELEMENTS = (
    "script", "link", "base", "meta", "iframe", "frame", "frameset", "object", "embed", "applet", "form",
    "html", "head", "body", "style", "textarea", "title", "xmp", "noembed", "noframes", "noscript", "plaintext",
)
_ELEMENT = re.compile(r"<\s*+(?:/\s*+)?(" + "|".join(REFUSED_ELEMENTS) + r")(?=[\s/>]|\Z)", re.I | re.A)
_DOCTYPE = re.compile(r"<\s*+!\s*+doctype", re.I | re.A)
# Comment ends and declarations that readers take differently: Python 3.13.5 ends a comment at "-- >" (a browser
# does not) and reads past "--!>", "<!-->" and "<!--->" (a browser and the rewritten parser of 3.11.15+ do not);
# "<![" is a declaration for one parser and a comment for the other. Refused outright, whatever the Python version.
_ODD_MARKUP = re.compile(r"--\s++>|--!>|<!---?>|<!\[")
# An end tag must be a plain name, ASCII whitespace and ">". With more after the name the parser ends it at the
# first ">" while a browser skips quoted values (and may run on into the bridge client's script tag); with any other
# Unicode space around the name the parser closes the element and a browser does not ("</ math>" is a comment).
_LOOSE_END_TAG = re.compile(r"</(?:\s|[A-Za-z](?![-.A-Za-z0-9:_]*+[\t\n\f\r ]*+>))")
# Open at the end of the entry, these would turn the bridge client into an SVG/MathML element or inert content.
_MUST_CLOSE = ("svg", "math", "template")
_SELF_CLOSING = ("svg", "math")  # "/>" closes foreign elements only; <template/> stays open
_ADDRESS_ATTRIBUTES = frozenset({
    "src", "href", "action", "formaction", "srcset", "poster", "data",
    "background", "ping", "cite", "longdesc", "lowsrc", "dynsrc", "codebase", "classid", "archive", "manifest",
    "icon",
})
_WHITESPACE = "\t\n\f\r "
_UNQUOTED_REFUSED = "\"'<`"  # parse errors in an unquoted value; a quote there starts a value for some readers
# Named references without ";" that a browser keeps in an attribute value (followed by a letter, digit or "=") while
# html.unescape, which the rewritten parser uses, decodes them; the older parser decodes as a browser does.
_LEGACY_REFERENCES = frozenset(name for name in html5_entities if not name.endswith(";"))
_REFERENCE = re.compile(r"&([A-Za-z][A-Za-z0-9]*+)(;?)")

_ELEMENT_MESSAGE = "Die Einstiegsdatei (entry) darf kein Element „{}“ enthalten."
_EVENT = "Die Einstiegsdatei (entry) darf keine Ereignis-Attribute („on…“) enthalten."
_STYLE = "Die Einstiegsdatei (entry) darf kein Attribut „style“ enthalten."
_ADDRESS = "In der Einstiegsdatei (entry) darf das Attribut „{}“ nur auf „#…“ verweisen (bei img auch auf „data:“)."
_ANIMATED = "In der Einstiegsdatei (entry) darf „attributeName“ kein Adress-, Ereignis- oder Stil-Attribut nennen."
_UNREADABLE = "Die Einstiegsdatei (entry) enthält Markup, das nicht gelesen werden kann."
_UNCLOSED = "In der Einstiegsdatei (entry) ist das Element „{}“ nicht geschlossen."

_SCRIPT_END = re.compile(r"</(script)", re.I | re.A)


def new_nonce() -> str:
    return secrets.token_urlsafe(18)


def _text(view: View, name: str) -> str:
    try:
        text = view.files[name].data.decode("utf-8")
    except UnicodeDecodeError:
        raise ManifestError(f"Datei „{name}“ ist nicht UTF-8-kodiert.") from None
    return text.removeprefix("\ufeff")


def _style_text(text: str) -> str:
    return text.replace("</", "<\\/")


def _script_text(text: str) -> str:
    """No end tag, and no "<!--", whose escaped state would let "<script" hide the real end tag."""
    return _SCRIPT_END.sub(r"<\\/\1", text).replace("<!--", "<\\!--")


def build_document(view: View, nonce: str) -> str:
    """The view as one document. The entry is inserted as it is: the registry checked it when it scanned the view."""
    if not _NONCE.fullmatch(nonce):
        raise ValueError("unsafe nonce")
    manifest = view.manifest
    parts = [
        '<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{POLICY.format(nonce=nonce)}">',
    ]
    parts += [f'<style nonce="{nonce}">{_style_text(_text(view, name))}</style>' for name in manifest.styles]
    parts += ["</head><body>", _text(view, manifest.entry)]
    for script in (BRIDGE_CLIENT, *(_text(view, name) for name in manifest.scripts)):
        parts.append(f'<script nonce="{nonce}">{_script_text(script)}</script>')
    parts.append("</body></html>")
    return "".join(parts)


def check_view(view: View) -> None:
    """The registry's check of a loaded view: every text file is UTF-8 and the entry markup is safe."""
    manifest = view.manifest
    entry = _text(view, manifest.entry)
    for name in (*manifest.styles, *manifest.scripts):
        _text(view, name)
    check_entry_markup(entry)


class _Refused(Exception):
    """Stops the parser at the first refused construct; carries the message."""


def _odd_space(text: str) -> bool:
    """Whitespace other than ASCII's: the older parser splits at it, a browser does not."""
    return any(c.isspace() and c not in _WHITESPACE for c in text)


def _ambiguous_reference(raw: str) -> bool:
    """A named reference in an attribute value that a browser and html.unescape decode differently."""
    for found in _REFERENCE.finditer(raw):
        run, semicolon = found.groups()
        if semicolon and run + ";" in html5_entities:
            continue
        if run in _LEGACY_REFERENCES:
            if not semicolon and raw.startswith("=", found.end()):
                return True  # "&amp=": a browser keeps it
            continue
        if any(run[:k] in _LEGACY_REFERENCES for k in range(1, min(len(run), 7))):
            return True  # "&notx": a browser keeps it, html.unescape reads "¬x"
    return False


def _browser_tag(tag: str) -> Tuple[List[Tuple[str, Optional[str]]], bool]:
    """One start tag as a browser tokenizes it: its attributes (names lowercased, values decoded) and "/>".

    Refused (unreadable) wherever readers could differ: a tag a browser ends elsewhere than the parser did; an
    attribute name with non-ASCII whitespace; an unquoted value that starts with "=" (href==#x is "#x" for the older
    parser and "=#x" for a browser) or holds a quote, "<", "`" or non-ASCII whitespace; a value with an ambiguous
    named reference.
    """
    attrs = []
    self_closing = False
    n = len(tag)
    i = 1
    while i < n and tag[i] not in _WHITESPACE + "/>":
        i += 1
    while i < n:
        if tag[i] in _WHITESPACE:
            i += 1
            continue
        if tag[i] == "/":
            self_closing = tag.startswith("/>", i)
            i += 1
            continue
        if tag[i] == ">":
            if i != n - 1:
                raise _Refused(_UNREADABLE)
            return attrs, self_closing
        start = i
        i += 1  # a name may start with "="
        while i < n and tag[i] not in _WHITESPACE + "/=>":
            i += 1
        name = tag[start:i].lower()
        if _odd_space(name):
            raise _Refused(_UNREADABLE)
        while i < n and tag[i] in _WHITESPACE:
            i += 1
        value = None
        if i < n and tag[i] == "=":
            i += 1
            while i < n and tag[i] in _WHITESPACE:
                i += 1
            if i < n and tag[i] in "\"'":
                end = tag.find(tag[i], i + 1)
                end = n if end < 0 else end
                value, i = tag[i + 1:end], end + 1
            else:
                start = i
                while i < n and tag[i] not in _WHITESPACE + ">":
                    i += 1
                value = tag[start:i]
                if value.startswith("=") or any(c in _UNQUOTED_REFUSED for c in value) or _odd_space(value):
                    raise _Refused(_UNREADABLE)
            if _ambiguous_reference(value):
                raise _Refused(_UNREADABLE)
            value = html.unescape(value)
        attrs.append((name, value))
    raise _Refused(_UNREADABLE)  # the parser's ">" lies inside a quoted value for a browser


def _check_attribute(tag: str, name: str, value: Optional[str]) -> None:
    local = name.rsplit(":", 1)[-1]  # xlink:href is an href
    if name.startswith("on") or local.startswith("on"):
        raise _Refused(_EVENT)
    if local == "style":
        raise _Refused(_STYLE)
    if local in _ADDRESS_ATTRIBUTES:
        if value is not None and value.startswith("#"):
            return
        if value is not None and tag == "img" and local in ("src", "href") and value[:5].lower() == "data:":
            return
        raise _Refused(_ADDRESS.format(local))
    if local == "attributename" and value is not None:  # an SVG animation can set any attribute
        target = value.strip().lower().rsplit(":", 1)[-1]
        if target in _ADDRESS_ATTRIBUTES or target.startswith("on") or target == "style":
            raise _Refused(_ANIMATED)


class _EntryParser(HTMLParser):
    # Never raw text or RCDATA: those elements are refused before the parser runs. (The rewritten parser of
    # Python 3.11.15+ also reads <plaintext> raw whatever these say; the raw scan refuses it as well.)
    CDATA_CONTENT_ELEMENTS = ()
    RCDATA_CONTENT_ELEMENTS = ()

    def __init__(self):
        # Raw text: a tag the parser gave up on reaches handle_data as "<x…", never decoded entities.
        super().__init__(convert_charrefs=False)
        self.open = dict.fromkeys(_MUST_CLOSE, 0)

    def handle_starttag(self, tag, attrs):
        browser_attrs, self_closing = _browser_tag(self.get_starttag_text())
        for name, value in browser_attrs:
            _check_attribute(tag, name, value)
        if attrs != browser_attrs:  # the parser read the tag differently: fail closed
            raise _Refused(_UNREADABLE)
        if tag in self.open and not (self_closing and tag in _SELF_CLOSING):
            self.open[tag] += 1

    handle_startendtag = handle_starttag  # whether "/>" closes is the browser's reading, above

    def handle_endtag(self, tag):
        if self.open.get(tag):
            self.open[tag] -= 1

    def handle_data(self, data):
        if len(data) > 1 and "<" in data:
            raise _Refused(_UNREADABLE)

    def handle_comment(self, data):
        # A browser ends a comment at "<!-->", "<!--->" and "--!>" as well; the older parser reads on. The raw scan
        # refuses these already; this stays for a parser that reads comments otherwise.
        if data.startswith((">", "->")) or "--!>" in data:
            raise _Refused(_UNREADABLE)

    def handle_decl(self, decl):
        raise _Refused(_UNREADABLE)

    def unknown_decl(self, data):
        raise _Refused(_UNREADABLE)

    def handle_pi(self, data):
        raise _Refused(_UNREADABLE)


def check_entry_markup(markup: str) -> None:
    """ManifestError when the entry holds a refused element or attribute or markup that cannot be read safely."""
    if "\x00" in markup:
        raise ManifestError(_UNREADABLE)
    found = _ELEMENT.search(markup)
    if found:
        raise ManifestError(_ELEMENT_MESSAGE.format(found.group(1).lower()))
    if _DOCTYPE.search(markup):
        raise ManifestError(_ELEMENT_MESSAGE.format("!DOCTYPE"))
    if _ODD_MARKUP.search(markup) or _LOOSE_END_TAG.search(markup):
        raise ManifestError(_UNREADABLE)
    parser = _EntryParser()
    try:
        parser.feed(markup)
        if "<" in parser.rawdata:  # a tag, comment or declaration that never ends
            raise _Refused(_UNREADABLE)
        parser.close()
    except _Refused as refused:
        raise ManifestError(str(refused)) from None
    except Exception:  # the parser itself gave up (it asserts on some declarations)
        raise ManifestError(_UNREADABLE) from None
    for name in _MUST_CLOSE:
        if parser.open[name]:
            raise ManifestError(_UNCLOSED.format(name))
