"""The brand tokens in the Platform's base styles (PMVP-10): what the frozen acceptance file leaves open.

test_pmvp10_brand_acceptance.py pins ten of style.css's names, the contrast of the text colours and the two type
names. Held here, on the files themselves (no database):

1. every name style.css declared before the brand came is still declared, takes its value from brand tokens that
   exist, and names no colour of its own;
2. the two font files the brand added are the brand's own, and each has its face;
3. the reader the styleguide page uses reads the default theme only, follows a token to its value, and refuses a
   name the files do not declare (so a shown token cannot drift from the tokens file).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

pytest.importorskip("flask")

from web_interface import shell_routes  # noqa: E402

P = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[4]
STATIC = P / "src" / "web_interface" / "static"
CSS = STATIC / "css"
FONTS = STATIC / "fonts"
REFERENCE_FONTS = REPO / "docs" / "platform-mvp" / "reference-screens" / "assets" / "fonts"

# The names style.css declared before PMVP-10; the other stylesheets use them.
NAMES_BEFORE = (
    "--primary-color", "--primary-hover", "--accent", "--accent-rgb", "--success-color", "--error-color",
    "--bg-color", "--card-bg", "--surface-sunken", "--callout", "--highlight", "--text-primary",
    "--text-primary-rgb", "--text-secondary", "--text-muted", "--border-color", "--radius", "--radius-lg",
    "--pad-section", "--gutter", "--font-body", "--font-heading",
)
TRIPLES = ("--accent-rgb", "--text-primary-rgb")
ADDED_FONTS = {
    "ibm-plex-sans-latin-700-normal.woff2": ("IBM Plex Sans", "700"),
    "ibm-plex-mono-latin-500-normal.woff2": ("IBM Plex Mono", "500"),
}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _tokens() -> dict[str, str]:
    return shell_routes.root_declarations(_text(CSS / "knovas-tokens.css"))


def _platform() -> dict[str, str]:
    return shell_routes.root_declarations(_text(CSS / "style.css"))


# --- 1: the Platform's names --------------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES_BEFORE)
def test_a_name_the_other_stylesheets_use_is_still_declared(name):
    assert name in _platform(), f"{name} is gone from style.css's :root block"


@pytest.mark.parametrize("name", [n for n in NAMES_BEFORE if n not in TRIPLES])
def test_a_name_takes_its_value_from_brand_tokens_only(name):
    tokens, platform = _tokens(), _platform()
    written = platform[name]
    pointed_at = re.findall(r"var\(\s*(--[A-Za-z0-9_-]+)\s*\)", written)
    assert pointed_at, f"{name}: {written!r} takes no token"
    for target in pointed_at:
        assert target in tokens or target in platform, f"{name} points to {target}, which nothing declares"
    # what is left when the var()s are taken out: calc(), an operator, spaces; never a value of its own
    rest = re.sub(r"var\(\s*--[A-Za-z0-9_-]+\s*\)", "", written)
    assert not re.search(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(|hsla?\(|\d", rest), f"{name}: {written!r} has a value of its own"
    # and it resolves down to the brand's file
    assert shell_routes.resolve(name, {**tokens, **platform})


def test_style_css_names_no_colour_of_its_own_in_the_root_block():
    for name, written in _platform().items():
        assert not re.search(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(|hsla?\(", written), f"{name}: {written!r}"


@pytest.mark.parametrize("sheet", ["admin.css", "views.css", "experiments.css", "custom_views.css"])
def test_every_name_a_page_stylesheet_uses_is_declared(sheet):
    """A var(--x) with no fallback that nothing declares draws nothing. (ontology.css declares its own on its page.)"""
    css = re.sub(r"/\*.*?\*/", "", _text(CSS / sheet), flags=re.S)
    own = set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", css))
    declared = set(_tokens()) | set(_platform()) | own
    used_bare = set(re.findall(r"var\(\s*(--[A-Za-z0-9_-]+)\s*\)", css))
    missing = sorted(used_bare - declared)
    assert not missing, f"{sheet} uses {missing}, which nothing declares"


# --- 2: the fonts --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(ADDED_FONTS))
def test_an_added_font_file_is_the_brands_own(name):
    ours, theirs = FONTS / name, REFERENCE_FONTS / name
    assert ours.is_file(), f"static/fonts/{name} is missing"
    assert hashlib.sha256(ours.read_bytes()).hexdigest() == hashlib.sha256(theirs.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", sorted(ADDED_FONTS))
def test_an_added_font_file_has_its_face(name):
    family, weight = ADDED_FONTS[name]
    css = re.sub(r"/\*.*?\*/", "", _text(CSS / "style.css"), flags=re.S)
    faces = [f for f in re.findall(r"@font-face\s*\{([^}]*)\}", css) if name in f]
    assert len(faces) == 1, f"{name} needs exactly one @font-face rule"
    assert re.search(rf"font-family:\s*'{family}'", faces[0])
    assert re.search(rf"font-weight:\s*{weight}\s*;", faces[0])
    # a subset file says which characters it holds, so the others keep the family's fuller files
    assert "unicode-range" in faces[0]


def test_the_licence_of_the_fonts_is_beside_them():
    assert (FONTS / "OFL.txt").is_file()


# --- 3: the reader the styleguide page uses -------------------------------------------------------------


def test_the_reader_reads_the_default_theme_only():
    css = """
    :root { --k-a: #111111; --k-b: var(--k-a); --k-ms: 150ms; }
    [data-theme='dark'] { --k-a: #EEEEEE; }
    @media (prefers-color-scheme: dark) { [data-theme='auto'] { --k-a: #DDDDDD; } }
    @media (prefers-reduced-motion: reduce) { :root { --k-ms: 0ms; } }
    """
    declared = shell_routes.root_declarations(css)
    assert declared == {"--k-a": "#111111", "--k-b": "var(--k-a)", "--k-ms": "150ms"}


def test_the_reader_leaves_comments_out():
    declared = shell_routes.root_declarations(":root { /* --k-old: #000000; */ --k-a: #111111; /* note */ }")
    assert declared == {"--k-a": "#111111"}


def test_a_value_is_followed_to_what_it_points_to():
    declared = {"--k-a": "#111111", "--k-b": "var(--k-a)", "--k-c": "1px solid var(--k-b)"}
    assert shell_routes.resolve("--k-b", declared) == "#111111"
    assert shell_routes.resolve("--k-c", declared) == "1px solid #111111"


def test_a_name_nothing_declares_is_refused():
    with pytest.raises(shell_routes.TokenMissing):
        shell_routes.resolve("--k-gone", {"--k-a": "#111111"})
    with pytest.raises(shell_routes.TokenMissing):
        shell_routes.resolve("--k-b", {"--k-b": "var(--k-gone)"})


def test_a_name_that_points_to_itself_is_refused():
    with pytest.raises(ValueError):
        shell_routes.resolve("--k-a", {"--k-a": "var(--k-b)", "--k-b": "var(--k-a)"})


def test_the_brands_own_file_reads_as_the_light_theme():
    """The file's dark block and its reduced-motion block re-point names; the page shows the default."""
    tokens = _tokens()
    assert shell_routes.resolve("--k-color-page", tokens) == shell_routes.resolve("--k-white", tokens)
    assert shell_routes.resolve("--k-color-accent", tokens) == shell_routes.resolve("--k-blue", tokens)
    assert tokens["--k-duration-fast"] != "0ms"


def _shown() -> list[dict]:
    sections = shell_routes.styleguide_sections()
    shown = []
    for key, value in sections.items():
        if key == "sg_text_rows":
            shown += [row["text"] for row in value]
        else:
            shown += value
    return shown


def test_every_token_the_page_shows_is_in_the_tokens_file():
    tokens, platform = _tokens(), _platform()
    names = {entry["name"] for entry in _shown()}
    assert {n for n in names if n.startswith("--k-")} <= set(tokens)
    assert {n for n in names if not n.startswith("--k-")} <= set(platform)
    # the acceptance test's list, and both faces
    assert {"--k-navy", "--k-blue", "--k-color-accent", "--k-color-text", "--k-color-text-muted",
            "--k-color-surface-sunken", "--k-color-border", "--k-success", "--k-warning", "--k-danger",
            "--k-font-sans", "--k-font-mono"} <= names | {e["token"] for e in _shown() if e["token"]}


def test_a_shown_colour_has_the_value_the_tokens_file_writes():
    """Read the file a second way: the first plain hex a name is given, which is the light theme's."""
    css = re.sub(r"/\*.*?\*/", "", _text(CSS / "knovas-tokens.css"), flags=re.S)
    written: dict[str, str] = {}
    for name, value in re.findall(r"(--k-[a-z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{6})\s*;", css):
        written.setdefault(name, value)
    checked = 0
    for entry in _shown():
        if entry["name"] in written:
            assert entry["value"] == written[entry["name"]], entry["name"]
            checked += 1
    assert checked >= 25, "the page shows fewer plain colours than expected"


def test_the_page_fails_when_a_shown_token_leaves_the_tokens_file():
    without_navy = re.sub(r"--k-navy:\s*#[0-9A-Fa-f]{6};", "", _text(CSS / "knovas-tokens.css"), count=1)
    with pytest.raises(shell_routes.TokenMissing):
        shell_routes.styleguide_sections(tokens_css=without_navy)


def test_the_text_colours_are_shown_on_the_three_backgrounds_with_their_contrast():
    rows = shell_routes.styleguide_sections()["sg_text_rows"]
    assert [row["text"]["name"] for row in rows] == list(shell_routes.TEXT_COLOURS)
    for row in rows:
        assert [cell["background"]["name"] for cell in row["cells"]] == list(shell_routes.BACKGROUNDS)
        for cell in row["cells"]:
            assert cell["ratio"] is not None and float(cell["ratio"]) >= 4.5, (row["text"]["name"], cell)


def test_contrast_is_the_wcag_ratio():
    assert round(shell_routes.contrast("#000000", "#FFFFFF"), 1) == 21.0
    assert round(shell_routes.contrast("#FFF", "#FFFFFF"), 1) == 1.0
    assert shell_routes.contrast("rgba(0, 0, 0, 0.1)", "#FFFFFF") is None
