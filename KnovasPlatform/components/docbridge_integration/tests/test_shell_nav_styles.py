"""The navigation's style rules (PMVP-11): static/css/shell.css and what is left in style.css. No database.

1. style.css loads shell.css with an @import after the brand tokens, and holds no rule for the navigation or for
   the removed row of tabs any more;
2. shell.css takes every colour, radius, shadow and duration from a brand token that exists, and styles exactly
   the parts the partial draws;
3. the look the work order names: a sunken column, the area's name in small capitals, the current entry on a
   white tile; in a narrow window the entries stand in a row that scrolls in itself;
4. admin.css still restyles the old navigation on the console's pages and is loaded later; every such rule is
   outranked by a rule of shell.css that sets the same properties, so the navigation looks the same everywhere.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "src" / "web_interface"
CSS = WEB / "static" / "css"
SIDEBAR = WEB / "templates" / "_sidebar.html"
NARROW = "(max-width: 900px)"
# the classes of the navigation's parts, as the partial writes them
PARTS = {"app-sidebar", "app-brand", "app-nav", "app-nav-back", "app-nav-group", "app-nav-item", "app-sidebar-foot",
         "app-areas", "app-user", "app-avatar", "app-user-text", "app-user-name", "app-user-role"}
# a longhand a shorthand of shell.css sets as well
SHORTHAND = {"flex-basis": "flex", "flex-grow": "flex", "flex-shrink": "flex", "border-right-color": "border-right",
             "background-color": "background", "overflow-x": "overflow", "overflow-y": "overflow"}


def _css(name: str) -> str:
    text = (CSS / name).read_text(encoding="utf-8").replace("\r\n", "\n")
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _rules(name: str) -> list[tuple[str, str, dict[str, str]]]:
    """Every style rule of a stylesheet as (media, selector, declarations); media is '' outside an @media block."""
    css, out = _css(name), []

    def plain(block: str, media: str) -> None:
        for selectors, body in re.findall(r"([^{}@;]+)\{([^{}]*)\}", block):
            declared = {prop.strip(): " ".join(value.split())
                        for prop, value in re.findall(r"([\w-]+)\s*:\s*([^;]+)", body)}
            for selector in selectors.split(","):
                out.append((media, " ".join(selector.split()), declared))

    position, outside = 0, []
    for match in re.finditer(r"@media\s*([^{]+)\{", css):
        if match.start() < position:
            continue
        outside.append(css[position:match.start()])
        depth, end = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[end], 0)
            end += 1
        plain(css[match.end():end - 1], " ".join(match.group(1).split()))
        position = end
    outside.append(css[position:])
    plain(re.sub(r"@(?:import|charset)[^;]+;", "", re.sub(r"@font-face\s*\{[^{}]*\}", "", "".join(outside))), "")
    return out


def _specificity(selector: str) -> tuple[int, int, int]:
    ids = len(re.findall(r"#[\w-]+", selector))
    classes = len(re.findall(r"\.[\w-]+|\[[^\]]*\]|(?<!:):(?!:)[\w-]+", selector))
    elements = len(re.findall(r"(?:^|[\s>+~])[a-zA-Z][\w-]*", selector)) + len(re.findall(r"::[\w-]+", selector))
    return ids, classes, elements


def _part(selector: str) -> str:
    """The navigation part a selector styles, without what it starts from: '.app-brand img', '.app-sidebar'."""
    found = re.search(r"\.app-(?!shell)[\w-]+.*$", selector)
    assert found, selector
    part = found.group(0)
    if part.startswith(".app-sidebar ") and part != ".app-sidebar":
        part = part[len(".app-sidebar "):]
    return part


def _navigation(name: str) -> list[tuple[str, str, dict[str, str]]]:
    return [rule for rule in _rules(name) if re.search(r"\.app-(?:" + "|".join(p[4:] for p in PARTS) + r")\b", rule[1])]


# --- 1: style.css -----------------------------------------------------------------------------------------


def test_style_css_loads_the_tokens_first_and_then_the_navigation():
    imports = re.findall(r"@import\s+url\(\s*['\"]?([^'\")]+)['\"]?\s*\)\s*;", _css("style.css"))
    assert imports == ["knovas-tokens.css", "parts.css", "shell.css"]  # parts.css: PMVP-12, merged beside this item
    # an @import counts only before every other rule
    before = _css("style.css")[:_css("style.css").index("shell.css")]
    assert "{" not in before
    assert (CSS / "shell.css").is_file()


def test_shell_css_imports_nothing_and_loads_nothing():
    assert "@import" not in _css("shell.css") and "url(" not in _css("shell.css")


def test_style_css_holds_no_rule_for_the_navigation_any_more():
    assert [(media, selector) for media, selector, _ in _navigation("style.css")] == []
    # the frame around it stays: the two columns, and one column in a narrow window
    frame = {(media, selector): declared for media, selector, declared in _rules("style.css")
             if selector in (".app-shell", ".app-content")}
    assert frame[("", ".app-shell")]["display"] == "flex"
    assert frame[(NARROW, ".app-shell")] == {"flex-direction": "column"}
    assert ("", ".app-content") in frame


def test_no_rule_for_the_row_of_tabs_is_left_in_style_css():
    assert not [selector for _, selector, _ in _rules("style.css") if ".tabs" in selector.split(" ")
                or selector.startswith(".admin .tabs")]


# --- 2: tokens, and the parts ----------------------------------------------------------------------------------


def test_shell_css_names_no_colour_of_its_own():
    css = _css("shell.css")
    assert not re.search(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(|hsla?\(", css)
    for _, selector, declared in _rules("shell.css"):
        for prop, value in declared.items():
            if prop in ("color", "background", "background-color", "border-top", "border-right", "border-bottom",
                        "box-shadow", "outline") and value not in ("none", "0"):
                assert "var(--k-" in value, f"{selector}: {prop}: {value}"


def test_every_token_shell_css_uses_is_in_the_brands_file():
    tokens = set(re.findall(r"(--k-[a-z0-9-]+)\s*:", _css("knovas-tokens.css")))
    used = set(re.findall(r"var\(\s*(--[A-Za-z0-9_-]+)\s*\)", _css("shell.css")))
    assert used and used <= tokens, sorted(used - tokens)
    assert "var(--" not in re.sub(r"var\(\s*--[A-Za-z0-9_-]+\s*\)", "", _css("shell.css"))   # no fallback values


def test_shell_css_styles_exactly_the_parts_the_partial_draws():
    drawn = set()
    for classes in re.findall(r'class="([^"{]*)', SIDEBAR.read_text(encoding="utf-8")):
        drawn |= set(classes.split())
    assert drawn == PARTS
    styled = set(re.findall(r"\.(app-[\w-]+)", " ".join(selector for _, selector, _ in _rules("shell.css"))))
    assert styled - {"app-shell"} == PARTS
    states = set(re.findall(r"\.(?!app-)([\w-]+)", " ".join(selector for _, selector, _ in _rules("shell.css"))))
    assert states == {"active"}                                 # the one state the partial sets


def test_every_rule_of_shell_css_starts_at_the_frame_and_the_navigation():
    for _, selector, _ in _rules("shell.css"):
        assert re.match(r"\.app-shell (?:aside\.app-sidebar$|\.app-sidebar \S)", selector), selector


# --- 3: the look ---------------------------------------------------------------------------------------------


def _declared(part: str, media: str = "") -> dict[str, str]:
    found: dict[str, str] = {}
    for rule_media, selector, declared in _rules("shell.css"):
        if rule_media == media and _part(selector) == part:
            found.update(declared)
    assert found, f"shell.css has no rule for {part} in {media or 'the base'}"
    return found


def test_the_navigation_is_a_quiet_sunken_column():
    column = _declared(".app-sidebar")
    assert column["background"] == "var(--k-color-surface-sunken)"
    assert column["border-right"] == "1px solid var(--k-color-border)"
    assert column["flex"] == "0 0 212px" and column["flex-direction"] == "column"
    assert column["position"] == "sticky" and column["height"] == "100vh"


def test_the_areas_name_is_set_in_small_capitals():
    group = _declared(".app-nav-group")
    assert group["text-transform"] == "uppercase"
    assert group["letter-spacing"] == "var(--k-tracking-label)"
    assert float(group["font-size"].removesuffix("rem")) < 0.75
    assert group["color"] == "var(--k-color-text-muted)"        # 4.5 to 1 on the sunken surface; gray-400 is not


def test_the_current_entry_stands_on_a_white_tile():
    entry, current = _declared(".app-nav-item"), _declared(".app-nav-item.active")
    assert entry["color"] == "var(--k-gray-600)" and "background" not in entry
    assert current["background"] == "var(--k-color-surface)"
    assert current["box-shadow"] == "var(--k-shadow-sm)"
    assert current["color"] == "var(--k-color-text-strong)"
    assert current["font-weight"] == "var(--k-weight-semibold)"
    # hovering over the current entry keeps the tile
    assert _declared(".app-nav-item.active:hover") == current


def test_a_link_shows_where_the_keyboard_is():
    for part in (".app-brand:focus-visible", ".app-nav-item:focus-visible", ".app-user:focus-visible"):
        assert _declared(part)["outline"] == "2px solid var(--k-color-accent)"


def test_the_foot_is_pushed_to_the_end_and_the_person_stands_under_a_line():
    assert _declared(".app-sidebar-foot")["margin-top"] == "auto"
    assert _declared(".app-user")["border-top"] == "1px solid var(--k-color-border)"


def test_in_a_narrow_window_the_entries_stand_in_a_row_that_scrolls_in_itself():
    """At 390 pixels the page must not scroll sideways because of the menu: the head wraps, the row of entries
    takes a line of its own and scrolls inside it, and no entry is squeezed."""
    head = _declared(".app-sidebar", NARROW)
    assert head["flex-wrap"] == "wrap" and head["width"] == "100%" and head["position"] == "static"
    row = _declared(".app-nav", NARROW)
    assert row["flex"] == "1 1 100%" and row["min-width"] == "0"
    assert row["overflow-x"] == "auto" and row["flex-direction"] == "row" and row["flex-wrap"] == "nowrap"
    assert _declared(".app-nav-item", NARROW)["flex"] == "0 0 auto"
    # the person stays reachable (admin.css hides the foot on its pages; see the next section)
    assert _declared(".app-sidebar-foot", NARROW)["display"] == "flex"
    assert _declared(".app-user-text", NARROW)["display"] == "none"


def test_the_narrow_window_is_the_one_where_style_css_stacks_the_frame():
    assert {media for media, _, _ in _rules("shell.css")} == {"", NARROW}
    assert (NARROW, ".app-shell", {"flex-direction": "column"}) in _rules("style.css")


# --- 4: admin.css does not win --------------------------------------------------------------------------------


def _admin_rules():
    return [(media, selector, declared) for media, selector, declared in _navigation("admin.css")]


def test_admin_css_still_restyles_the_navigation():
    """If this fails because admin.css dropped those rules, the selectors of shell.css can lose their first step
    and this section can go."""
    assert len(_admin_rules()) >= 10
    assert all(selector.startswith(".admin-page ") for _, selector, _ in _admin_rules())


@pytest.mark.parametrize("media, selector, declared", _admin_rules(),
                         ids=[f"{media or 'base'} {selector}" for media, selector, _ in _admin_rules()])
def test_a_rule_of_admin_css_for_the_navigation_is_outranked(media, selector, declared):
    part, theirs = _part(selector), _specificity(selector)
    for prop in declared:
        winners = [ours for ours_media, ours, ours_declared in _rules("shell.css")
                   if ours_media in ("", media) and _part(ours) == part and _specificity(ours) > theirs
                   and (prop in ours_declared or SHORTHAND.get(prop) in ours_declared)]
        assert winners, f"admin.css sets {prop} on {selector} ({media or 'base'}) and nothing in shell.css outranks it"
        if media:                                               # a narrow rule is answered in the narrow block
            assert any(ours_media == media for ours_media, ours, ours_declared in _rules("shell.css")
                       if _part(ours) == part and (prop in ours_declared or SHORTHAND.get(prop) in ours_declared))


def test_specificity_is_counted_as_css_counts_it():
    assert _specificity(".admin-page .app-nav-item.active") == (0, 3, 0)
    assert _specificity(".app-shell aside.app-sidebar") == (0, 2, 1)
    assert _specificity(".app-shell .app-sidebar .app-nav-item.active:hover") == (0, 5, 0)
    assert _specificity(".admin-page .app-brand img") == (0, 2, 1)
    assert _specificity("#a .b c::before") == (1, 1, 2)
