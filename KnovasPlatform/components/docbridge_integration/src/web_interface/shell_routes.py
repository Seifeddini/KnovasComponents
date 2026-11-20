"""The Platform shell's own routes: the styleguide page (PMVP-10, plan FEAT-2026-11-19-platform-mvp F-7).

GET /styleguide shows what the Platform's look is built from: the Knovas brand tokens (colours, type, spacing,
radii, shadows), each with its name and value, the Platform's own style names with the token each takes, and the
plain parts that exist today. Administrators only, by the console's rule (admin.py, require_admin): a signed-out
caller is sent to sign-in, anyone without the role admin gets 403.

Every name and value on the page is read, per request, from the two stylesheets themselves:
static/css/knovas-tokens.css (the brand's own file) and the :root block of static/css/style.css. So the page
cannot drift from them, and a token the page asks for that the file no longer has is an error, not a blank.

The route logs nothing of its own.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path

from flask import Blueprint, abort, redirect, render_template, request, url_for

CSS_DIR = Path(__file__).resolve().parent / "static" / "css"
TOKENS_FILE = CSS_DIR / "knovas-tokens.css"
STYLE_FILE = CSS_DIR / "style.css"

# What the page shows, by name only; the values come from the files.
BRAND_COLOURS = ("--k-navy", "--k-blue", "--k-blue-deep", "--k-blue-bright", "--k-cyan", "--k-sky")
STATUS_COLOURS = (
    "--k-success", "--k-success-bright", "--k-success-bg",
    "--k-warning", "--k-warning-bright", "--k-warning-bg",
    "--k-danger", "--k-danger-bright", "--k-danger-bg",
)
SURFACE_COLOURS = (
    "--k-color-page", "--k-color-surface", "--k-color-surface-sunken", "--k-color-border",
    "--k-color-border-strong", "--k-color-accent", "--k-color-accent-hover", "--k-color-accent-soft",
)
NEUTRALS = (
    "--k-white", "--k-gray-50", "--k-gray-100", "--k-gray-200", "--k-gray-300", "--k-gray-400", "--k-gray-500",
    "--k-gray-600", "--k-gray-700", "--k-gray-800", "--k-gray-900",
)
# The text colours on the three backgrounds, by the Platform's names (each takes a brand token).
TEXT_COLOURS = ("--text-primary", "--text-secondary", "--text-muted", "--primary-color")
BACKGROUNDS = ("--bg-color", "--card-bg", "--surface-sunken")
FACES = ("--k-font-sans", "--k-font-mono")
TYPE_SCALE = (
    "--k-text-2xs", "--k-text-xs", "--k-text-sm", "--k-text-base", "--k-text-lg", "--k-text-xl", "--k-text-2xl",
    "--k-text-3xl", "--k-text-4xl", "--k-text-5xl",
)
# The weights the installation holds font files for (static/fonts): the sans in four, the mono in three.
SANS_WEIGHTS = ("--k-weight-normal", "--k-weight-medium", "--k-weight-semibold", "--k-weight-bold")
MONO_WEIGHTS = ("--k-weight-normal", "--k-weight-medium", "--k-weight-semibold")
SPACING = (
    "--k-space-3xs", "--k-space-2xs", "--k-space-xs", "--k-space-sm", "--k-space-md", "--k-space-lg",
    "--k-space-xl", "--k-space-2xl", "--k-space-3xl", "--k-space-4xl", "--k-space-5xl",
)
RADII = (
    "--k-radius-xs", "--k-radius-sm", "--k-radius-md", "--k-radius-lg", "--k-radius-xl", "--k-radius-2xl",
    "--k-radius-full",
)
SHADOWS = ("--k-shadow-xs", "--k-shadow-sm", "--k-shadow-md", "--k-shadow-lg", "--k-shadow-xl", "--k-shadow-panel")

_VAR = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)\s*\)")
_HEX = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})")


class TokenMissing(KeyError):
    """The page names a custom property that neither stylesheet declares."""


def root_declarations(css: str) -> dict[str, str]:
    """The custom properties of a stylesheet's :root blocks, default theme only (a later block wins).

    Comments go first; @media and @supports blocks go with everything inside them, so a reduced-motion or
    dark-scheme override is not read as the default. A [data-theme] block is not a :root block and is not read.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    plain, at = [], 0
    for match in re.finditer(r"@(?:media|supports|container|layer)\b[^{;]*\{", css):
        if match.start() < at:
            continue
        plain.append(css[at:match.start()])
        depth, at = 1, match.end()
        while depth and at < len(css):
            depth += {"{": 1, "}": -1}.get(css[at], 0)
            at += 1
    plain.append(css[at:])
    declared: dict[str, str] = {}
    for block in re.findall(r"(?:^|[}\s,]):root\s*\{([^{}]*)\}", "".join(plain)):
        for name, value in re.findall(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;]+);", block):
            declared[name] = " ".join(value.split())
    return declared


def resolve(name: str, declared: dict[str, str], _seen: tuple[str, ...] = ()) -> str:
    """A custom property's value with every var() in it replaced by what it points to."""
    if name not in declared:
        raise TokenMissing(name)
    if name in _seen:
        raise ValueError(f"{name} refers to itself")
    return _VAR.sub(lambda m: resolve(m.group(1), declared, _seen + (name,)), declared[name])


def _luminance(colour: str) -> float:
    digits = colour.lstrip("#")
    if len(digits) == 3:
        digits = "".join(d * 2 for d in digits)

    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(int(digits[i:i + 2], 16)) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float | None:
    """The WCAG contrast of two colours, or None when one of them is not a plain hex colour."""
    if not (_HEX.fullmatch(a) and _HEX.fullmatch(b)):
        return None
    lighter, darker = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _pixels(value: str) -> str | None:
    """A rem or px length in pixels (16px to the rem, the browsers' default), for the reader; else None."""
    match = re.fullmatch(r"(\d*\.?\d+)(rem|px)", value)
    if not match:
        return None
    pixels = float(match.group(1)) * (16 if match.group(2) == "rem" else 1)
    return f"{pixels:g}px"


def styleguide_sections(tokens_css: str | None = None, style_css: str | None = None) -> dict:
    """What the styleguide template draws: every shown name with its written and its resolved value."""
    tokens = root_declarations(TOKENS_FILE.read_text(encoding="utf-8") if tokens_css is None else tokens_css)
    platform = root_declarations(STYLE_FILE.read_text(encoding="utf-8") if style_css is None else style_css)
    declared = {**tokens, **platform}

    def entry(name: str) -> dict:
        if name not in declared:
            raise TokenMissing(name)
        written = declared[name]
        value = resolve(name, declared)
        points_to = _VAR.fullmatch(written)
        return {
            "name": name,
            "value": value,
            # the token a name takes its value from, when it is exactly one
            "token": points_to.group(1) if points_to else None,
            "written": written,
            "pixels": _pixels(value),
        }

    def entries(names) -> list[dict]:
        return [entry(name) for name in names]

    backgrounds = entries(BACKGROUNDS)
    text_rows = []
    for text in entries(TEXT_COLOURS):
        cells = []
        for background in backgrounds:
            ratio = contrast(text["value"], background["value"])
            cells.append({"background": background, "ratio": None if ratio is None else f"{ratio:.1f}"})
        text_rows.append({"text": text, "cells": cells})

    return {
        "sg_brand": entries(BRAND_COLOURS),
        "sg_status": entries(STATUS_COLOURS),
        "sg_surfaces": entries(SURFACE_COLOURS),
        "sg_neutrals": entries(NEUTRALS),
        "sg_backgrounds": backgrounds,
        "sg_text_rows": text_rows,
        "sg_faces": entries(FACES),
        "sg_type_scale": entries(TYPE_SCALE),
        "sg_sans_weights": entries(SANS_WEIGHTS),
        "sg_mono_weights": entries(MONO_WEIGHTS),
        "sg_spacing": entries(SPACING),
        "sg_radii": entries(RADII),
        "sg_shadows": entries(SHADOWS),
        # the Platform's own names, in the order style.css declares them
        "sg_platform": entries(platform),
    }


def create_shell_blueprint(gate, *, page_context) -> Blueprint:
    """The shell's blueprint. ``gate`` is the identity gate, ``page_context`` the values every page's frame needs
    (the sidebar's, the title, the asset version), both handed in so this module does not import ``app.py``."""
    bp = Blueprint("shell", __name__)

    def require_admin(view):
        """The console's rule, as admin.py's require_admin has it: signed in, and holding the role admin."""

        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            user = gate.current_user()
            if user is None:
                return redirect(url_for("login", next=request.full_path or "/styleguide"))
            if "admin" not in set(user.roles):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    @bp.route("/styleguide")
    @require_admin
    def styleguide():
        context = dict(page_context())
        context.update(styleguide_sections())
        return render_template("styleguide.html", active_nav="styleguide", **context)

    return bp
