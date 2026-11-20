"""PMVP-10 acceptance tests: the Platform's base styles take the Knovas brand tokens, and a styleguide page shows
them (FEAT-2026-11-19-platform-mvp requirement F-7, plan section 5.1, Jira SS-729).

FROZEN. Written by the coordinator before the build; a builder never edits this file. Its git blob id is in
docs/platform-mvp/frozen-tests.txt.

What is pinned:

1. `static/css/knovas-tokens.css` is the brand's own file, unchanged (the copy the six reference screens use, in
   docs/platform-mvp/reference-screens/assets/; it equals KnowledgeBase brand/knovas-brand/css/knovas-tokens.css).
2. Every page that loads the base stylesheet gets the tokens first.
3. The base stylesheet keeps its own variable names (the other stylesheets use them) and gives them the brand's
   values: each resolves, through var() only, to the same colour as the brand token beside it below.
4. Text stays readable: 4.5 to 1 or better (WCAG AA) for the text colours on the three backgrounds.
5. Body text and headings are set in the brand's sans face; nothing is loaded from outside the installation, and
   every font or image a stylesheet names is there.
6. `/styleguide` shows the tokens to an administrator and to nobody else.

Changed by the coordinator on 2026-11-20, after the first build: the last test rejected every outside address on
the page, also a plain link in the navigation; it now looks only at what the page loads (stylesheets, scripts,
images, frames).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

P = Path(__file__).resolve().parents[1]                     # .../components/docbridge_integration
REPO = Path(__file__).resolve().parents[4]
CSS = P / "src" / "web_interface" / "static" / "css"
TEMPLATES = P / "src" / "web_interface" / "templates"
TOKENS = CSS / "knovas-tokens.css"
STYLE = CSS / "style.css"
REFERENCE = REPO / "docs" / "platform-mvp" / "reference-screens" / "assets" / "knovas-tokens.css"

# the Platform's variable -> the brand token it takes its value from
BRAND_OF = {
    "--primary-color": "--k-color-accent",
    "--primary-hover": "--k-color-accent-hover",
    "--bg-color": "--k-color-page",
    "--card-bg": "--k-color-surface",
    "--surface-sunken": "--k-color-surface-sunken",
    "--text-primary": "--k-color-text",
    "--text-muted": "--k-color-text-muted",
    "--border-color": "--k-color-border",
    "--success-color": "--k-color-success-fg",
    "--error-color": "--k-color-danger-fg",
}
TEXT = ["--text-primary", "--text-secondary", "--text-muted", "--primary-color"]
BACKGROUNDS = ["--bg-color", "--card-bg", "--surface-sunken"]
SHOWN_ON_THE_STYLEGUIDE = [
    "--k-navy", "--k-blue", "--k-color-accent", "--k-color-text", "--k-color-text-muted", "--k-color-surface-sunken",
    "--k-color-border", "--k-success", "--k-warning", "--k-danger", "--k-font-sans", "--k-font-mono",
]


# --- reading CSS -------------------------------------------------------------------------------------


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _without_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _without_at_blocks(css: str) -> str:
    """Drop @media, @supports and the like with their whole block, so only the default theme is read."""
    out, i = [], 0
    for match in re.finditer(r"@(?:media|supports|container|layer)\b[^{;]*\{", css):
        if match.start() < i:
            continue
        out.append(css[i:match.start()])
        depth, j = 1, match.end()
        while depth and j < len(css):
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        i = j
    out.append(css[i:])
    return "".join(out)


def _root_declarations(css: str) -> dict[str, str]:
    """The custom properties of the :root blocks of the default (light) theme; a later one wins."""
    declared: dict[str, str] = {}
    plain = _without_at_blocks(_without_comments(css))
    for block in re.findall(r"(?:^|[}\s,]):root\s*\{([^{}]*)\}", plain):
        for name, value in re.findall(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;]+);", block):
            declared[name] = " ".join(value.split())
    return declared


def _declared() -> dict[str, str]:
    return {**_root_declarations(_text(TOKENS)), **_root_declarations(_text(STYLE))}


def _resolve(name: str, declared: dict[str, str], seen: tuple[str, ...] = ()) -> str:
    assert name in declared, f"{name} is not declared in a :root block"
    assert name not in seen, f"{name} refers to itself"
    value = declared[name]
    reference = re.fullmatch(r"var\(\s*(--[A-Za-z0-9_-]+)\s*(?:,\s*(.+?)\s*)?\)", value)
    if not reference:
        return value
    if reference.group(1) in declared:
        return _resolve(reference.group(1), declared, seen + (name,))
    assert reference.group(2), f"{name} points to {reference.group(1)}, which is not declared"
    return reference.group(2)


def _hex(name: str, declared: dict[str, str]) -> str:
    value = _resolve(name, declared)
    match = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value)
    assert match, f"{name} must resolve to a plain hex colour through var() only, got {value!r}"
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(d * 2 for d in digits)
    return digits.lower()


def _rgb(hex_colour: str) -> tuple[int, ...]:
    return tuple(int(hex_colour[i:i + 2], 16) for i in (0, 2, 4))


def _luminance(hex_colour: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in _rgb(hex_colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    lighter, darker = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# --- 1 and 2: the tokens are the brand's, and every page gets them -----------------------------------


def test_the_tokens_file_is_the_brands_own_unchanged():
    assert TOKENS.is_file(), "static/css/knovas-tokens.css is missing"
    assert _text(TOKENS) == _text(REFERENCE)


def test_every_page_that_loads_the_base_styles_gets_the_tokens_first():
    style = _without_comments(_text(STYLE))
    if re.match(r"\s*(?:@charset\s+[^;]+;\s*)?@import\s+(?:url\(\s*)?[\"']?(?:\./)?knovas-tokens\.css", style):
        return                                              # the base stylesheet pulls them in itself
    pages = [t for t in sorted(TEMPLATES.rglob("*.html")) if "css/style.css" in _text(t)]
    assert pages, "no template loads css/style.css"
    for template in pages:
        html = _text(template)
        assert "css/knovas-tokens.css" in html, f"{template.name} loads the base styles without the tokens"
        assert html.index("css/knovas-tokens.css") < html.index("css/style.css"), (
            f"{template.name} loads the tokens after the base styles")


# --- 3: the Platform's own variables carry the brand's values -----------------------------------------


@pytest.mark.parametrize("ours, brand", sorted(BRAND_OF.items()))
def test_the_platforms_variable_has_the_brands_value(ours, brand):
    declared = _declared()
    assert ours in _root_declarations(_text(STYLE)), f"{ours} must stay declared in style.css (other stylesheets use it)"
    assert _hex(ours, declared) == _hex(brand, declared)


@pytest.mark.parametrize("triple, colour", [("--text-primary-rgb", "--text-primary"), ("--accent-rgb", "--accent")])
def test_an_rgb_triple_follows_its_colour(triple, colour):
    declared = _declared()
    numbers = tuple(int(n) for n in re.findall(r"\d+", _resolve(triple, declared)))
    assert numbers == _rgb(_hex(colour, declared))


# --- 4: readable ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", TEXT)
@pytest.mark.parametrize("background", BACKGROUNDS)
def test_text_keeps_a_contrast_of_4_5_to_1(text, background):
    declared = _declared()
    ratio = _contrast(_hex(text, declared), _hex(background, declared))
    assert ratio >= 4.5, f"{text} on {background}: {ratio:.2f} to 1"


# --- 5: type, and nothing from outside ---------------------------------------------------------------


@pytest.mark.parametrize("font", ["--font-body", "--font-heading"])
def test_body_text_and_headings_are_set_in_the_brands_sans(font):
    assert _resolve(font, _declared()).lstrip("'\" ").startswith("IBM Plex Sans")


def _stylesheets():
    return sorted(CSS.rglob("*.css"))


def test_no_stylesheet_loads_anything_from_outside_the_installation():
    for sheet in _stylesheets():
        css = _without_comments(_text(sheet))
        assert not re.search(r"url\(\s*[\"']?(?:https?:)?//", css), sheet.name
        assert not re.search(r"@import\s+(?:url\(\s*)?[\"']?(?:https?:)?//", css), sheet.name


def test_every_file_a_stylesheet_names_is_there():
    for sheet in _stylesheets():
        css = _without_comments(_text(sheet))
        for target in re.findall(r"url\(\s*[\"']?([^\"')]+)[\"']?\s*\)", css):
            if target.startswith(("data:", "#")):
                continue
            path = target.split("?")[0].split("#")[0]
            static_root = CSS.parent
            found = (static_root / path.lstrip("/").removeprefix("static/")) if path.startswith("/") \
                else (sheet.parent / path)
            assert found.resolve().is_file(), f"{sheet.name} names {target}, which is not in static/"


# --- 6: the styleguide page ---------------------------------------------------------------------------


def test_a_signed_out_visitor_is_sent_to_sign_in(anon_client):
    response = anon_client.get("/styleguide")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_a_member_is_refused(member_client):
    assert member_client.get("/styleguide").status_code == 403


def test_an_administrator_sees_the_tokens_by_name(admin_client):
    response = admin_client.get("/styleguide")
    assert response.status_code == 200
    page = response.data.decode("utf-8")
    assert "Styleguide" in page
    for name in SHOWN_ON_THE_STYLEGUIDE:
        assert name in page, f"{name} is not shown"
    assert "IBM Plex Sans" in page and "IBM Plex Mono" in page
    loaded = re.findall(r"<(?:link|script|img|iframe|source|embed|object|video|audio)[^>]*>", page, flags=re.I)
    for tag in loaded:                                      # a plain link to another site (<a href>) loads nothing
        assert not re.search(r"(?:src|href|data)=[\"'](?:https?:)?//", tag, flags=re.I), (
            "the page loads something from outside the installation")
