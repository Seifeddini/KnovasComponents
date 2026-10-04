"""Source-relative path templates, as Knovas Connector applies them (spec 3.4).

The Platform's copy of ``KnovasConnector/src/sync/field_templates.py``. The
Ingestion tab compiles templates when a profile is saved and previews their
captures over the paths Knovas Connector reports, so it has to read a
template exactly as Knovas Connector will: a preview that disagrees with the
sync would show a person values their documents never get. Both
implementations are checked against the same golden vectors, byte-copied to
``rc_contracts/vectors/field_templates.json`` beside this package (a test
keeps the copy identical to the Knovas Connector checkout).

The grammar::

    template := segment ("/" segment)* ["/**"]
    segment  := "*" | "{" key "}" | literal
    key      := [a-z][a-z0-9_]{0,63}, not a system key, at most once
    literal  := 1..255 chars, none of "/" "{" "}" "*" "\\"

Matching runs over the DIRECTORY segments of the source-relative path (the
file name is excluded; ``\\`` is a separator). Literals compare after NFC
and casefold (then NFC again), ``*`` matches exactly one segment, and
without a trailing ``/**`` the directory depth must be equal. A capture is
the whole segment, NFC-normalised and stripped; an empty segment gives no
value. Nothing is type-converted. The first template that matches wins, even
when it captured nothing.

Compile errors are checked in a fixed order so both implementations report
the same code: the overall length first (512), then each segment left to
right -- an empty segment is ``syntax``; a key segment checks its characters
(``syntax``), its length (64, ``too_long``), system keys (``system_key``),
then repeats (``duplicate_key``); a literal checks forbidden characters
(``syntax``) before its length (255, ``too_long``).

Captures are customer data: nothing in this module logs, and an error never
repeats the template.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

#: Keys of the anchor's own fields: set by top-level init keys or read-only,
#: never sent in ``fields``.
SYSTEM_KEYS = frozenset({"title", "description", "path", "ingested_at", "pointer"})

#: A registry key as the sync contract allows it.
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_KEY_CHARS_RE = re.compile(r"^[a-z][a-z0-9_]*$")

MAX_TEMPLATE_CHARS = 512
MAX_LITERAL_CHARS = 255
MAX_KEY_CHARS = 64

DEEP_SUFFIX = "/**"
_LITERAL_FORBIDDEN = frozenset("/{}*\\")

TEMPLATE_ERROR_CODES = frozenset({"syntax", "duplicate_key", "system_key", "too_long"})

#: The golden vectors shipped with the Platform (a byte copy of
#: ``KnovasConnector/contracts/vectors/field_templates.json``).
VECTORS_PATH = Path(__file__).resolve().parent / "rc_contracts" / "vectors" / "field_templates.json"


class TemplateError(ValueError):
    """A template that does not compile. ``code`` is one of
    ``TEMPLATE_ERROR_CODES``; the message never repeats the template."""

    def __init__(self, code: str) -> None:
        super().__init__(f"field template invalid: {code}")
        self.code = code


@dataclass(frozen=True)
class _Segment:
    kind: str  # "any" | "key" | "literal"
    value: str = ""  # the key, or the folded literal


@dataclass(frozen=True)
class CompiledTemplate:
    """A compiled template; ``source`` is the text as configured."""

    source: str
    segments: tuple[_Segment, ...]
    deep: bool

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(s.value for s in self.segments if s.kind == "key")

    def match(self, rel: str) -> Optional[dict[str, str]]:
        """The captures for ``rel``, or None when the template does not match.

        A match whose captured segments are all empty returns ``{}``.
        """
        dirs = directory_segments(rel)
        if self.deep:
            if len(dirs) < len(self.segments):
                return None
        elif len(dirs) != len(self.segments):
            return None
        out: dict[str, str] = {}
        for segment, part in zip(self.segments, dirs):
            if segment.kind == "literal":
                if _fold(part) != segment.value:
                    return None
            elif segment.kind == "key":
                value = unicodedata.normalize("NFC", part).strip()
                if value:
                    out[segment.value] = value
        return out


def _fold(text: str) -> str:
    """NFC, casefold, NFC: casefold may leave a decomposed sequence."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def directory_segments(rel: str) -> tuple[str, ...]:
    """The directory segments of a source-relative path (``\\`` is a
    separator; the last segment, the file name, is dropped). Empty segments
    keep their position."""
    parts = str(rel or "").replace("\\", "/").split("/")
    return tuple(parts[:-1])


def _compile_segment(raw: str, seen: set[str]) -> _Segment:
    if raw == "":
        raise TemplateError("syntax")
    if raw == "*":
        return _Segment("any")
    if raw.startswith("{") and raw.endswith("}") and len(raw) >= 2:
        key = raw[1:-1]
        if not _KEY_CHARS_RE.match(key):
            raise TemplateError("syntax")
        if len(key) > MAX_KEY_CHARS:
            raise TemplateError("too_long")
        if key in SYSTEM_KEYS:
            raise TemplateError("system_key")
        if key in seen:
            raise TemplateError("duplicate_key")
        seen.add(key)
        return _Segment("key", key)
    if any(ch in _LITERAL_FORBIDDEN for ch in raw):
        raise TemplateError("syntax")
    if len(raw) > MAX_LITERAL_CHARS:
        raise TemplateError("too_long")
    return _Segment("literal", _fold(raw))


def compile_template(template: str) -> CompiledTemplate:
    """Compile one template or raise ``TemplateError`` (order: see the
    module docstring)."""
    if not isinstance(template, str):
        raise TemplateError("syntax")
    if len(template) > MAX_TEMPLATE_CHARS:
        raise TemplateError("too_long")
    body = template
    deep = False
    if body.endswith(DEEP_SUFFIX):
        deep = True
        body = body[: -len(DEEP_SUFFIX)]
    if body == "":
        raise TemplateError("syntax")
    seen: set[str] = set()
    segments = tuple(_compile_segment(raw, seen) for raw in body.split("/"))
    return CompiledTemplate(source=template, segments=segments, deep=deep)


def compile_templates(templates: Iterable[str]) -> tuple[CompiledTemplate, ...]:
    """Compile a source's templates in order; the first bad one raises."""
    return tuple(compile_template(t) for t in templates or ())


def match_template(template: str, rel: str) -> Optional[dict[str, str]]:
    """Compile and match in one call (the golden-vector semantics)."""
    return compile_template(template).match(rel)


def first_match(rel: str, templates: Sequence[CompiledTemplate]) -> tuple[Optional[int], dict[str, str]]:
    """``(index, captures)`` of the first template that matches ``rel``
    (0-based), or ``(None, {})``. ``captures`` with the template that won,
    for a preview that names it."""
    for index, template in enumerate(templates or ()):
        found = template.match(rel)
        if found is not None:
            return index, found
    return None, {}


def captures(rel: str, templates: Sequence[CompiledTemplate]) -> dict[str, str]:
    """The captures of the first template that matches ``rel``; ``{}`` when
    none matches. A later template is never consulted once one matched,
    even if the first one captured nothing."""
    return first_match(rel, templates)[1]
