"""
Map Word paragraph styles to Markdown headings (real document structure only).

Keep in sync with KnovasPlatform/components/docbridge_integration/src/docx_markdown.py
when changing heading rules.
"""
from __future__ import annotations

import io
import logging
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

_HEADING_STYLE_PATTERNS = (
    re.compile(r"^heading\s*(\d+)\s*$", re.I),
    re.compile(r"^überschrift\s*(\d+)\s*$", re.I),
    re.compile(r"^titre\s*(\d+)\s*$", re.I),
    re.compile(r"^rubrik\s*(\d+)\s*$", re.I),
    re.compile(r"^encabezado\s*(\d+)\s*$", re.I),
)


def _heading_level_from_style_name(style_name: str) -> Optional[int]:
    name = (style_name or "").strip()
    if not name:
        return None
    for pat in _HEADING_STYLE_PATTERNS:
        m = pat.match(name)
        if m:
            return max(1, min(int(m.group(1)), 6))
    if re.match(r"^title\s*$", name, re.I):
        return 1
    return None


def _escape_table_cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _docx_table_to_markdown(table) -> str:
    rows: List[List[str]] = []
    for row in table.rows:
        cells = [_escape_table_cell(c.text) for c in row.cells]
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(norm[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    for r in norm[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _iter_docx_block_items(document):
    from docx.oxml.ns import qn  # type: ignore[import]
    from docx.table import Table  # type: ignore[import]
    from docx.text.paragraph import Paragraph  # type: ignore[import]

    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def docx_bytes_to_markdown(raw_bytes: bytes) -> str:
    """Convert DOCX body to markdown: headings, tables, plain paragraphs."""
    try:
        import docx  # type: ignore[import]
    except ImportError as exc:
        raise ImportError("python-docx is required for DOCX conversion") from exc

    document = docx.Document(io.BytesIO(raw_bytes))
    blocks: List[str] = []

    for block in _iter_docx_block_items(document):
        if hasattr(block, "rows"):
            md = _docx_table_to_markdown(block)
            if md:
                blocks.append(md)
            continue
        text = (block.text or "").strip()
        style = block.style
        style_name = style.name if style is not None else ""
        level = _heading_level_from_style_name(style_name)
        if level is not None and text:
            blocks.append(f"{'#' * level} {text}")
        elif text:
            blocks.append(text)

    return "\n\n".join(blocks).strip()
