"""Edit values in a Markdown file's front matter and nothing else (MVP-22).

A write proposal changes facts; in a Markdown file they are values of its front matter. ``edit(data, values)`` replaces
only the text of each named value and gives the file's bytes back with everything else byte-identical: the BOM, the
line endings (CRLF, LF or mixed), the key order, the comments (on the value's own line too), the other keys, and the
body, which may name the same key again. A key that does not exist yet is added as the last line of the front matter.

The front matter is found as brain-runner reads it: the file starts (after an optional BOM) with a line ``---``, and
the block ends at the next line ``---`` followed by a line end. A value is replaced in its own style where the new
value can be written in it (plain, single or double quoted, literal or folded block); otherwise it is written double
quoted. Each candidate is checked by parsing the result: the edited key must read exactly the new value, as the runner
reads values (text; true/false; a date as YYYY-MM-DD), and every other key exactly what it read before. Anything the
editor cannot change that safely is refused with a code, never guessed:

    front_matter_missing  the file has no front matter block as the runner reads it
    edit_unsupported      the file is not UTF-8; the key's value is not a single scalar (a list, a mapping, an
                          anchor, an alias or a tag), the key stands twice, or no rendering reads back as the value
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

import yaml

BOM = b"\xef\xbb\xbf"
CODES = ("front_matter_missing", "edit_unsupported")
_LINES = re.compile(r"[^\r\n]*(?:\r\n|\n|\r)|[^\r\n]+\Z")
_BREAK = re.compile(r"\r\n|\n|\r")
_INDICATOR = re.compile(r"[|>][-+1-9]{0,2}")
_BARE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
#: Line breaks YAML knows besides \r and \n; a front matter holding one is not edited.
_OTHER_BREAKS = ("\x85", " ", " ")


class EditError(ValueError):
    """The edit cannot be made safely; ``code`` is one of CODES."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def shown(value: Any) -> Any:
    """A value as the runner reads a fact (``brain_runner.mapping.text``): booleans as true/false, everything else
    as its text; None for nothing. A date read from YAML shows as YYYY-MM-DD."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, dict)):
        return ("structured", json.dumps(value, sort_keys=True, default=str))
    return str(value)


def _block(text: str) -> tuple[int, int, str]:
    """(where the front matter's content starts, where its closing line starts, the opening line's line end)."""
    lines = _LINES.findall(text)
    if not lines or _BREAK.sub("", lines[0]) != "---" or lines[0] == "---":
        raise EditError("front_matter_missing")
    eol = lines[0][3:]
    offset = len(lines[0])
    for i, line in enumerate(lines[1:], start=1):
        body = _BREAK.sub("", line)
        # As the runner's pattern reads it: the block holds at least one line, and the closing --- has a line end.
        if i > 1 and body == "---" and line != body:
            return len(lines[0]), offset, eol
        offset += len(line)
    raise EditError("front_matter_missing")


def _load(fm: str) -> dict:
    try:
        data = yaml.safe_load(fm)
    except yaml.YAMLError:
        raise EditError("edit_unsupported") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise EditError("edit_unsupported")
    return data


def _pairs(fm: str) -> list[tuple[Any, Any]]:
    try:
        node = yaml.compose(fm, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        raise EditError("edit_unsupported") from None
    if node is None:
        return []
    if not isinstance(node, yaml.MappingNode):
        raise EditError("edit_unsupported")
    return list(node.value)


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _inline(value: Any, style: str | None) -> list[str]:
    """Renderings of the value on one line, the original style first."""
    s = _text(value)
    plain = [s] if s and "\n" not in s and "\r" not in s else []
    single = ["'" + s.replace("'", "''") + "'"] if "\n" not in s and "\r" not in s else []
    double = [json.dumps(s, ensure_ascii=False)]
    if style == "'":
        return single + plain + double
    if style == '"':
        return double
    return plain + double


def _block_candidates(raw: str, value: Any, style: str) -> list[str]:
    """New texts of the span of a literal or folded block scalar, the block first: the header's comment and the blank
    lines after the content stay as they are."""
    first = _BREAK.search(raw)
    if first is None:
        return []
    header, eol = raw[:first.start()], first.group()
    m = _INDICATOR.match(header)
    if m is None:
        return []
    rest = header[m.end():]                                   # spaces and a comment, kept as they are
    lines = _LINES.findall(raw[first.end():])
    trailing: list[str] = []
    while lines and not lines[-1].strip():
        trailing.insert(0, lines.pop())
    indents = [len(x) - len(x.lstrip(" ")) for x in lines if x.strip()]
    indent = " " * (min(indents) if indents else 2)
    s = _text(value)
    out = []
    chomp = "-" if not s.endswith("\n") else ("" if not s.endswith("\n\n") else None)
    if chomp is not None and s.strip("\n"):
        body = s[:-1] if s.endswith("\n") else s
        parts = body.split("\n")
        kind = style if (style == "|" or len(parts) == 1) else "|"
        width = str(len(indent)) if parts[0].startswith(" ") and 0 < len(indent) < 10 else ""
        text = kind + chomp + width + rest + eol + "".join((indent + p if p else "") + eol for p in parts)
        out.append(text + "".join(trailing))
    for r in _inline(value, '"'):
        out.append(r + rest + eol + "".join(trailing))
    return out


def _render_key(key: str) -> str:
    return key if _BARE_KEY.match(key) else json.dumps(key, ensure_ascii=False)


def _check(fm: str, before: dict, key: str, value: Any) -> bool:
    """The edited key reads the value, and every other key reads what it read before."""
    try:
        after = _load(fm)
    except EditError:
        return False
    if key not in after or shown(after[key]) != shown(value):
        return False
    others = {k: v for k, v in after.items() if k != key}
    return others == {k: v for k, v in before.items() if k != key}


def _edit_one(fm: str, key: str, value: Any, eol: str) -> str:
    before = _load(fm)
    pairs = _pairs(fm)
    matches = [(k, v) for k, v in pairs if isinstance(k, yaml.ScalarNode) and k.value == key]
    if len(matches) > 1:
        raise EditError("edit_unsupported")
    if not matches:
        if key in before:                         # stated some other way (a merge key, a non-string key)
            raise EditError("edit_unsupported")
        for r in _inline(value, None):                         # added as the front matter's last line
            trial = fm + _render_key(key) + ": " + r + eol
            if _check(trial, before, key, value):
                return trial
        raise EditError("edit_unsupported")
    k, v = matches[0]
    if not isinstance(v, yaml.ScalarNode):
        raise EditError("edit_unsupported")
    a, b = v.start_mark.index, v.end_mark.index
    if a < k.end_mark.index or (a < b and fm[a] in "&*!"):    # an alias, an anchor or a tag
        raise EditError("edit_unsupported")
    if a == b:                                                   # no value yet: written right after the colon
        candidates = [" " + r for r in _inline(value, None)]
    elif v.style in ("|", ">"):
        candidates = _block_candidates(fm[a:b], value, v.style)
    else:
        candidates = _inline(value, v.style)
    for r in candidates:
        trial = fm[:a] + r + fm[b:]
        if _check(trial, before, key, value):
            return trial
    raise EditError("edit_unsupported")


def edit(data: bytes, values: Mapping[str, Any]) -> bytes:
    """The file with each key of ``values`` set to its value in the front matter, and every other byte as it was."""
    if not values:
        raise EditError("edit_unsupported")
    bom = data.startswith(BOM)
    try:
        text = data[len(BOM) if bom else 0:].decode("utf-8")
    except UnicodeDecodeError:
        raise EditError("edit_unsupported") from None
    start, end, eol = _block(text)
    fm = text[start:end]
    if any(c in fm for c in _OTHER_BREAKS):
        raise EditError("edit_unsupported")
    original = _load(fm)
    for key, value in values.items():
        if not isinstance(key, str) or not key or _BREAK.search(key) or value is None \
                or not isinstance(value, (str, int, float, bool)) or not _text(value).strip():
            raise EditError("edit_unsupported")
        fm = _edit_one(fm, key, value, eol)
    after = _load(fm)
    if {k: v for k, v in after.items() if k not in values} != {k: v for k, v in original.items() if k not in values} \
            or any(shown(after.get(k)) != shown(v) for k, v in values.items()):
        raise EditError("edit_unsupported")
    out = text[:start] + fm + text[end:]
    return (BOM if bom else b"") + out.encode("utf-8")
