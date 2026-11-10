"""A rich-text field as plain text: Atlassian Document Format (ADF) to text (FEAT-2026-11-10-write-back J-4;
BET-2026-11-write-back WB-07).

Jira Cloud's REST API v3 gives a rich-text field (the description, the environment, a paragraph custom field) as an ADF
document, ``{"type": "doc", "version": 1, "content": [...]}``. A record carries scalars only, so ``records.scalar``
turns such a document into plain text with :func:`to_text`. Plain means no HTML and no markup beyond the line
structure:

- **Blocks** (a paragraph, a heading, a code block, a quote, a panel, a list, a table) are separated by a blank line. A
  heading is its text alone; a code block is its text as it is, line breaks kept, without a fence.
- **Lists:** ``- `` before each item of a bullet list, ``1. `` (from the list's own start) in an ordered one, ``[ ] ``
  or ``[x] `` after the dash for a task, two spaces more per level of nesting.
- **Tables:** one line per row, the cells' text separated by `` | ``.
- **Inline:** text as it is, every mark dropped (bold, italic, code, colour; a link is its text, never its address); a
  hard break is a line break; a smart link card, which has no text of its own, is its URL; a mention is ``@`` and the
  person's account id, never their name (as every person in a record); an emoji its text or short name; a date
  ``YYYY-MM-DD``; a status its text.
- **Left out:** attachments and other media, extensions' parameters, placeholders, rules; a node of a kind not named
  here gives the text of its content.
- **Bounded:** at most :data:`MAX_TEXT_CHARS` characters (32,768, half of the protocol's 64 KiB for one value); longer
  text is cut and ends with ``…``. The walk reads at most :data:`MAX_NODES` nodes and :data:`MAX_DEPTH` levels; beyond
  either it stops and the text read so far ends with ``…`` the same way. Control characters other than the line break
  and the tab are dropped, a line's trailing spaces too, and there is never more than one blank line in a row.

The same document always gives the same text. Nothing here names a site, a project or an issue.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

MAX_TEXT_CHARS = 32768
MAX_NODES = 20000
MAX_DEPTH = 64
CUT = "…"

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_TRAILING = re.compile(r"[ \t]+\n")
_BLANKS = re.compile(r"\n{3,}")
_LISTS = ("bulletList", "orderedList", "taskList", "decisionList")
_INLINE = frozenset({"text", "hardBreak", "mention", "emoji", "inlineCard", "date", "status", "mediaInline",
                     "inlineExtension", "placeholder"})
_LEFT_OUT = frozenset({"media", "mediaGroup", "mediaSingle", "mediaInline", "extension", "inlineExtension",
                       "placeholder", "rule"})


def is_adf(value: Any) -> bool:
    """Whether ``value`` is an ADF document (``type`` ``doc`` with a ``content`` list)."""
    return isinstance(value, Mapping) and value.get("type") == "doc" and isinstance(value.get("content"), list)


def to_text(doc: Mapping[str, Any]) -> Optional[str]:
    """The document as plain text (module docstring), None when it holds no text."""
    walk = _Walk()
    text = _tidy("\n\n".join(walk.blocks(doc.get("content"), 1)))
    if walk.cut or len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS - len(CUT)].rstrip() + CUT
    return text or None


def _tidy(text: str) -> str:
    text = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    return _BLANKS.sub("\n\n", _TRAILING.sub("\n", text)).strip()


def _attrs(node: Mapping[str, Any]) -> Mapping[str, Any]:
    attrs = node.get("attrs")
    return attrs if isinstance(attrs, Mapping) else {}


def _content(node: Mapping[str, Any]) -> list:
    content = node.get("content")
    return content if isinstance(content, list) else []


def _text_attr(node: Mapping[str, Any], *names: str) -> str:
    attrs = _attrs(node)
    for name in names:
        if isinstance(attrs.get(name), str) and attrs[name]:
            return attrs[name]
    return ""


def _date(node: Mapping[str, Any]) -> str:
    raw = _attrs(node).get("timestamp")
    try:
        millis = int(raw)
        return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


class _Walk:
    """One walk over a document, counting nodes, depth and the text made so far."""

    def __init__(self) -> None:
        self.nodes = 0
        self.size = 0
        self.cut = False

    def _enter(self, depth: int) -> bool:
        self.nodes += 1
        if self.nodes > MAX_NODES or depth > MAX_DEPTH or self.size > MAX_TEXT_CHARS:
            self.cut = True
        return not self.cut

    def _made(self, text: str) -> str:
        self.size += len(text)
        return text

    # -- blocks -------------------------------------------------------------------------------------------------------

    def blocks(self, nodes: Any, depth: int) -> list[str]:
        out = []
        for node in nodes if isinstance(nodes, list) else ():
            if not isinstance(node, Mapping):
                continue
            if not self._enter(depth):
                break
            text = self.block(node, depth)
            if text and text.strip():
                out.append(text)
        return out

    def block(self, node: Mapping[str, Any], depth: int) -> str:
        kind = node.get("type")
        if kind in _LEFT_OUT:
            return ""
        if kind in ("paragraph", "heading"):
            return self.inline(_content(node), depth + 1)
        if kind == "codeBlock":
            return self._made("".join(c["text"] for c in _content(node)
                                      if isinstance(c, Mapping) and isinstance(c.get("text"), str)))
        if kind in _LISTS:
            return "\n".join(self.list_lines(node, depth, 0))
        if kind == "table":
            return "\n".join(self.table_rows(node, depth))
        if kind in ("blockCard", "embedCard"):
            return self._made(_text_attr(node, "url"))
        if kind in ("expand", "nestedExpand"):
            title = self._made(_text_attr(node, "title"))
            return "\n\n".join(([title] if title else []) + self.blocks(_content(node), depth + 1))
        if kind in _INLINE:
            return self.inline([node], depth)
        # blockquote, panel, bodiedExtension, layoutSection, layoutColumn, and any kind not named here: its content
        content = _content(node)
        if content and all(isinstance(c, Mapping) and c.get("type") in _INLINE for c in content):
            return self.inline(content, depth + 1)
        return "\n\n".join(self.blocks(content, depth + 1))

    def list_lines(self, node: Mapping[str, Any], depth: int, level: int) -> list[str]:
        kind = node.get("type")
        order = _attrs(node).get("order", 1)
        start = order if isinstance(order, int) and not isinstance(order, bool) and order >= 0 else 1
        lines, number = [], start
        pad = "  " * level
        for item in _content(node):
            if not isinstance(item, Mapping):
                continue
            if not self._enter(depth + 1):
                break
            if item.get("type") in _LISTS:                      # a task list holds its sub-lists directly
                lines.extend(self.list_lines(item, depth + 1, level + 1))
                continue
            if kind == "orderedList":
                marker = f"{number}. "
                number += 1
            elif kind == "taskList":
                marker = "- [x] " if _attrs(item).get("state") == "DONE" else "- [ ] "
            else:
                marker = "- "
            body, nested = [], []
            content = _content(item)
            if item.get("type") in ("taskItem", "decisionItem") or (
                    content and all(isinstance(c, Mapping) and c.get("type") in _INLINE for c in content)):
                body = [self.inline(content, depth + 2)]
            else:
                for child in content:
                    if not isinstance(child, Mapping):
                        continue
                    if not self._enter(depth + 2):
                        break
                    if child.get("type") in _LISTS:
                        nested.extend(self.list_lines(child, depth + 2, level + 1))
                    else:
                        text = self.block(child, depth + 2)
                        if text and text.strip():
                            body.append(text)
            text_lines = "\n".join(body).split("\n")
            lines.append(pad + marker + text_lines[0])
            lines.extend(pad + " " * len(marker) + more for more in text_lines[1:])
            lines.extend(nested)
        return lines

    def table_rows(self, node: Mapping[str, Any], depth: int) -> list[str]:
        rows = []
        for row in _content(node):
            if not isinstance(row, Mapping):
                continue
            if not self._enter(depth + 1):
                break
            cells = []
            for cell in _content(row):
                if not isinstance(cell, Mapping):
                    continue
                if not self._enter(depth + 2):
                    break
                cells.append(" ".join(" ".join(self.blocks(_content(cell), depth + 3)).split()))
            if any(cells):
                rows.append(" | ".join(cells))
        return rows

    # -- inline -------------------------------------------------------------------------------------------------------

    def inline(self, nodes: Any, depth: int) -> str:
        parts = []
        for node in nodes if isinstance(nodes, list) else ():
            if not isinstance(node, Mapping):
                continue
            if not self._enter(depth):
                break
            kind = node.get("type")
            if kind == "text":
                parts.append(self._made(node["text"] if isinstance(node.get("text"), str) else ""))
            elif kind == "hardBreak":
                parts.append("\n")
            elif kind == "mention":
                account = _text_attr(node, "id")
                parts.append(self._made("@" + account) if account else "")
            elif kind == "emoji":
                parts.append(self._made(_text_attr(node, "text", "shortName")))
            elif kind in ("inlineCard", "blockCard", "embedCard"):
                parts.append(self._made(_text_attr(node, "url")))
            elif kind == "date":
                parts.append(self._made(_date(node)))
            elif kind == "status":
                parts.append(self._made(_text_attr(node, "text")))
            elif kind in _LEFT_OUT:
                continue
            else:
                parts.append(self.inline(_content(node), depth + 1))
        return "".join(parts)
