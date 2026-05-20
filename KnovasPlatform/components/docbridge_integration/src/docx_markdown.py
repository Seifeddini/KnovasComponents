"""
Map Word paragraph styles to Markdown headings (real document structure only).

Duplicate of components/docbridge_sync/src/utils/docx_markdown.py — keep in sync
when changing heading rules (integration image build does not include docbridge_sync).
"""
from __future__ import annotations

import io
import re
import logging
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
    """Return 1–6 for Word built-in heading styles, or None."""
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
    """Build a minimal GitHub-flavored markdown table from a python-docx table."""
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
    """Yield paragraphs and tables in document order (body child sequence)."""
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
    """
    Convert DOCX body to markdown: heading styles -> # / ## / …; tables -> pipe tables.

    Body paragraphs are emitted as plain text blocks separated by blank lines.
    """
    try:
        import docx  # type: ignore[import]
    except ImportError:
        logger.warning("python-docx not installed; DOCX markdown unavailable")
        return ""

    document = docx.Document(io.BytesIO(raw_bytes))
    blocks: List[str] = []

    for block in _iter_docx_block_items(document):
        if hasattr(block, "rows"):  # Table
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
