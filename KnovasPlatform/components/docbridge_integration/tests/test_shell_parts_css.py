"""The shared page parts (PMVP-12, plan FEAT-2026-11-19-platform-mvp F-7): static/css/parts.css, read as a file.

No database and no browser. Held here:

1. the file names no colour and loads nothing: colours, type, radii and spacing come from the brand tokens only,
   and every token it asks for is in the brand's file;
2. every rule hangs on a class that starts with k-, so no rule of another stylesheet changes, and the file brings
   no names of its own;
3. the base stylesheet loads it right after the tokens, the way it loads the tokens;
4. readable and usable: each part's text keeps 4.5 to 1 on the backgrounds it stands on, every link and button
   of the parts has a focus ring, and a tab row that does not fit scrolls inside itself.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("flask")

from web_interface import shell_routes  # noqa: E402

P = Path(__file__).resolve().parents[1]
CSS = P / "src" / "web_interface" / "static" / "css"
PARTS = CSS / "parts.css"

# Words that name a colour. currentColor follows the text and names none.
COLOUR_WORDS = (
    "transparent|black|white|silver|gray|grey|red|maroon|yellow|olive|lime|green|aqua|cyan|teal|blue|navy|fuchsia|"
    "magenta|purple|orange|pink|brown|gold|indigo|violet|beige|ivory|khaki|coral|crimson|salmon|tan|turquoise|"
    "whitesmoke|gainsboro|lightgray|lightgrey|darkgray|darkgrey|dimgray|dimgrey|slategray|slategrey|royalblue|"
    "steelblue|skyblue|dodgerblue|midnightblue|aliceblue|ghostwhite|snow|lavender|tomato|firebrick|darkred|"
    "darkblue|darkgreen|seagreen|forestgreen|goldenrod|orangered|rebeccapurple"
)
# Where a length has to be a token: type, radii and spacing.
TOKEN_ONLY = ("font-size", "font-weight", "letter-spacing", "border-radius", "padding", "padding-top",
              "padding-right", "padding-bottom", "padding-left", "margin", "margin-top", "margin-right",
              "margin-bottom", "margin-left", "gap", "row-gap", "column-gap")
BACKGROUNDS = ("--k-color-page", "--k-color-surface", "--k-color-surface-sunken")
# Text that stands on whatever is behind the part: (the rule that sets the colour).
TEXT_ON_THE_PAGE = (".k-eyebrow", ".k-section-title", ".k-title", ".k-lede", ".k-tab", ".k-tab--on", ".k-tab-count",
                    ".k-link", ".k-muted")
# Text on a fill of its own: (the rule that sets both).
TEXT_ON_ITS_FILL = (".k-tag", ".k-tag--warn", ".k-tag--bad", ".k-tag--acc", ".k-ref", ".k-ref--on")
LINKS_AND_BUTTONS = (".k-tab", ".k-ref", ".k-link")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _css() -> str:
    return re.sub(r"/\*.*?\*/", "", _text(PARTS), flags=re.S)


def _rules() -> list[tuple[list[str], dict[str, str]]]:
    """Every rule of parts.css: its selectors and its declarations. The file has no nested block."""
    rules = []
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", _css()):
        declarations = dict(re.findall(r"([a-z-]+)\s*:\s*([^;]+);", body))
        rules.append(([" ".join(s.split()) for s in selectors.split(",")], declarations))
    return rules


def _declared(selector: str) -> dict[str, str]:
    """What the rules for exactly this selector declare, a later rule winning."""
    found: dict[str, str] = {}
    for selectors, declarations in _rules():
        if selector in selectors:
            found.update(declarations)
    assert found, f"parts.css has no rule for {selector}"
    return found


def _tokens() -> dict[str, str]:
    return shell_routes.root_declarations(_text(CSS / "knovas-tokens.css"))


def _one_token(value: str) -> str:
    match = re.fullmatch(r"var\(\s*(--k-[a-z0-9-]+)\s*\)", value.strip())
    assert match, f"{value!r} is not exactly one token"
    return match.group(1)


def _rgba(colour: str) -> tuple[float, float, float, float]:
    colour = colour.strip()
    hexed = re.fullmatch(r"#([0-9a-fA-F]{6})", colour)
    if hexed:
        digits = hexed.group(1)
        return (*(int(digits[i:i + 2], 16) for i in (0, 2, 4)), 1.0)
    function = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)", colour)
    assert function, f"{colour!r} is neither a hex colour nor rgb()/rgba()"
    return (float(function.group(1)), float(function.group(2)), float(function.group(3)),
            float(function.group(4) or 1))


def _over(top: tuple, under: tuple) -> tuple[float, float, float, float]:
    """A colour laid over an opaque one."""
    alpha = top[3]
    return (*(top[i] * alpha + under[i] * (1 - alpha) for i in range(3)), 1.0)


def _luminance(colour: tuple) -> float:
    def channel(value: float) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(colour[0]) + 0.7152 * channel(colour[1]) + 0.0722 * channel(colour[2])


def _contrast(text: tuple, background: tuple) -> float:
    lighter, darker = sorted((_luminance(text), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _colour(token: str) -> tuple[float, float, float, float]:
    return _rgba(shell_routes.resolve(token, _tokens()))


# --- 1: no colour of its own, nothing loaded, tokens only -------------------------------------------------


def test_parts_css_names_no_colour():
    css = _css()
    assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", css), "a hex colour"
    assert not re.search(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix)\(", css), "a colour function"
    for selectors, declarations in _rules():
        for name, value in declarations.items():
            rest = re.sub(r"var\([^)]*\)", "", value)
            word = re.search(rf"(?<![\w-])(?:{COLOUR_WORDS})(?![\w-])", rest, re.I)
            assert not word, f"{selectors[0]} {{ {name}: {value} }} names the colour {word.group(0)!r}"


def test_parts_css_loads_nothing():
    css = _css()
    assert "url(" not in css and "@import" not in css and "@font-face" not in css
    assert "//" not in css and "http" not in css.lower(), "an address"


def test_every_token_parts_css_asks_for_is_in_the_brands_file():
    asked = set(re.findall(r"var\(\s*(--[A-Za-z0-9_-]+)", _css()))
    assert len(asked) >= 20
    assert all(name.startswith("--k-") for name in asked), sorted(n for n in asked if not n.startswith("--k-"))
    missing = sorted(asked - set(_tokens()))
    assert not missing, f"not in knovas-tokens.css: {missing}"
    assert not re.search(r"var\([^)]*,", _css()), "a var() with a fallback value of its own"


def test_parts_css_declares_no_name_of_its_own():
    """The tokens are the brand's; a part takes them and adds none."""
    for selectors, declarations in _rules():
        own = [name for name in declarations if name.startswith("--")]
        assert not own, f"{selectors[0]} declares {own}"
    assert not re.search(r"(?:^|[{;\s])--[A-Za-z0-9_-]+\s*:", _css())


@pytest.mark.parametrize("name", TOKEN_ONLY)
def test_type_radii_and_spacing_are_tokens(name):
    """A size of type, a weight, a radius, a padding, a margin or a gap is a token, or nothing at all."""
    for selectors, declarations in _rules():
        if name not in declarations:
            continue
        rest = re.sub(r"var\(\s*--k-[a-z0-9-]+\s*\)", "", declarations[name])
        rest = re.sub(r"\bcalc\(|\)|\bauto\b|\binherit\b|[+\s]", "", rest)
        assert rest.strip("0") == "", f"{selectors[0]} {{ {name}: {declarations[name]} }} has a value of its own"


def test_every_face_is_the_brands_or_the_one_around_it():
    for selectors, declarations in _rules():
        for name in ("font-family", "font"):
            if name in declarations:
                assert declarations[name] in ("var(--k-font-sans)", "var(--k-font-mono)", "inherit"), selectors[0]


# --- 2: k- classes only -------------------------------------------------------------------------------------


def test_every_class_starts_with_k():
    classes = set()
    for selectors, _ in _rules():
        for selector in selectors:
            classes |= set(re.findall(r"\.([A-Za-z_][A-Za-z0-9_-]*)", selector))
    assert len(classes) >= 25
    assert all(name.startswith("k-") for name in classes), sorted(n for n in classes if not n.startswith("k-"))


def test_every_rule_hangs_on_a_k_class():
    """No rule can reach an element another stylesheet draws: each selector starts at a k- class."""
    for selectors, _ in _rules():
        for selector in selectors:
            assert selector.startswith(".k-"), f"{selector!r} does not start at a k- class"
            assert not re.search(r"(?:^|[\s>+~])(?:\*|[a-z][a-z0-9]*)(?![\w-])(?=[\s>+~.:\[]|$)", selector), (
                f"{selector!r} names an element")


@pytest.mark.parametrize("sheet", ["style.css", "admin.css", "views.css", "experiments.css", "custom_views.css",
                                   "ontology.css"])
def test_no_other_stylesheet_has_a_k_class(sheet):
    """The k- names are the parts' own; nothing of the other stylesheets can meet them."""
    css = re.sub(r"/\*.*?\*/", "", _text(CSS / sheet), flags=re.S)
    selectors = "".join(re.findall(r"([^{}]+)\{", css))
    assert not re.findall(r"\.k-[A-Za-z0-9_-]+", selectors)


def test_parts_css_has_no_block_inside_a_block_and_no_important():
    css = _css()
    assert "@" not in css, "an at-rule (the readers of this file take plain rules only)"
    assert "!important" not in css
    assert css.count("{") == css.count("}") == len(_rules())


# --- 3: how it is loaded ----------------------------------------------------------------------------------


def test_the_base_stylesheet_loads_the_parts_right_after_the_tokens():
    style = re.sub(r"/\*.*?\*/", "", _text(CSS / "style.css"), flags=re.S)
    imports = re.findall(r"@import\s+url\(\s*['\"]?([^'\")]+)['\"]?\s*\)\s*;", style)
    assert imports == ["knovas-tokens.css", "parts.css"]
    # an @import counts only before every other rule
    first_rule = re.search(r"@font-face|[^{};@]+\{", style)
    assert style.index("parts.css") < first_rule.start()


def test_a_part_sets_its_own_margins_and_its_own_box():
    """So a part looks the same where the base styles are not style.css (the Builder has its own)."""
    for selector in (".k-eyebrow", ".k-section-title", ".k-title", ".k-lede"):
        assert "margin" in _declared(selector), f"{selector} leaves its margins to the page"
    for selector in (".k-page", ".k-card", ".k-tabs", ".k-tag", ".k-ref"):
        assert _declared(selector).get("box-sizing") == "border-box", selector
    chip = _declared(".k-ref")
    assert chip["border"] == "0" and chip["font-family"] == "inherit" and "padding" in chip


# --- 4: readable and usable -------------------------------------------------------------------------------


@pytest.mark.parametrize("background", BACKGROUNDS)
@pytest.mark.parametrize("selector", TEXT_ON_THE_PAGE)
def test_text_of_a_part_keeps_4_5_to_1_on_the_three_backgrounds(selector, background):
    text = _colour(_one_token(_declared(selector)["color"]))
    ratio = _contrast(text, _colour(background))
    assert ratio >= 4.5, f"{selector} on {background}: {ratio:.2f} to 1"


@pytest.mark.parametrize("background", BACKGROUNDS)
@pytest.mark.parametrize("selector", TEXT_ON_ITS_FILL)
def test_text_of_a_part_keeps_4_5_to_1_on_its_own_fill(selector, background):
    """A tag or a reference brings its fill; a soft fill lets the surface behind it through."""
    declared = _declared(selector)
    fill = _over(_colour(_one_token(declared["background"])), _colour(background))
    text = _over(_colour(_one_token(declared["color"])), fill)
    ratio = _contrast(text, fill)
    assert ratio >= 4.5, f"{selector} over {background}: {ratio:.2f} to 1"


def test_every_colour_a_part_sets_is_held_by_a_contrast_test():
    """A new text colour in parts.css has to be added to one of the two lists above."""
    setting = {s for selectors, declarations in _rules() if "color" in declarations for s in selectors}
    held = set(TEXT_ON_THE_PAGE) | set(TEXT_ON_ITS_FILL)
    # a hover state changes the colour of text that is already held, to the accent's own darker step
    hover = {s for s in setting if s.endswith(":hover")}
    for selector in hover:
        assert _declared(selector)["color"] in ("var(--k-color-accent-hover)", "var(--k-color-text-strong)",
                                                "var(--k-color-accent-contrast)"), selector
    assert setting - hover <= held, sorted(setting - hover - held)


@pytest.mark.parametrize("selector", LINKS_AND_BUTTONS)
def test_a_link_or_button_of_the_parts_has_a_focus_ring(selector):
    ring = _declared(f"{selector}:focus-visible")
    width, style, colour = ring["outline"].split(None, 2)
    assert style == "solid" and int(width.removesuffix("px")) >= 2
    # the ring is the accent: 7 to 1 on white, so it is seen on every background
    assert _one_token(colour) == "--k-color-accent"
    assert min(_contrast(_colour("--k-color-accent"), _colour(b)) for b in BACKGROUNDS) >= 3


def test_no_part_takes_the_focus_ring_away():
    for selectors, declarations in _rules():
        for name in ("outline", "outline-style", "outline-width"):
            assert declarations.get(name, "").split(" ")[0] not in ("none", "0", "0px"), selectors[0]


def test_a_tab_row_that_does_not_fit_scrolls_inside_itself():
    row, tab = _declared(".k-tabs"), _declared(".k-tab")
    assert row["display"] == "flex" and row["overflow-x"] == "auto"
    # a tab keeps its width and its one line, so the row grows past its box and the box scrolls
    assert tab["flex"] == "none" and tab["white-space"] == "nowrap"
    # and the row never widens what it stands in: no width of its own, the whole line at least
    assert row["width"] == "0" and row["min-width"] == "100%"
    # the ring of a focused tab lies inside it, where the scrolling box cannot cut it
    assert _declared(".k-tab:focus-visible")["outline-offset"].startswith("-")


def test_the_column_is_640_pixels_and_the_wide_one_720():
    column = _declared(".k-page")
    assert column["max-width"] == "640px" and column["width"] == "100%" and column["margin"] == "0 auto"
    assert _declared(".k-page--wide") == {"max-width": "720px"}


def test_long_text_wraps_instead_of_pushing_the_page_sideways():
    for selector in (".k-title", ".k-lede", ".k-eyebrow", ".k-section-title", ".k-link"):
        assert _declared(selector).get("overflow-wrap") == "anywhere", selector
    tag = _declared(".k-tag")
    assert tag["max-width"] == "100%" and tag["overflow"] == "hidden" and tag["text-overflow"] == "ellipsis"
    assert _declared(".k-rowline > :first-child")["min-width"] == "0"
    # a row whose end leaves the content too little room puts the end below it
    assert _declared(".k-rowline")["flex-wrap"] == "wrap" and _declared(".k-rowline > :last-child")["flex"] == "none"
