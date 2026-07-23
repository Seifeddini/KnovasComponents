"""Map knovas-extract tables to Knovas Secure API transmit payload shape."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

_MAX_TABLES_PER_PART = 50
_MAX_TABLE_COLUMNS = 64
_MAX_TABLE_ROWS = 5000
_MAX_CELL_CHARS = 1024
_MAX_HEADER_CHARS = 512
_MAX_TABLE_HINT_CHARS = 128
_MAX_TABLE_TITLE_CHARS = 512

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _table_attr(table: Any, *keys: str) -> Any:
    if isinstance(table, dict):
        for key in keys:
            if key in table:
                return table[key]
        return None
    for key in keys:
        val = getattr(table, key, None)
        if val is not None:
            return val
    return None


def _slug_hint(value: str, fallback: str) -> str:
    base = (value or fallback or "table").strip().lower()
    slug = _SLUG_RE.sub("-", base).strip("-")
    if not slug:
        slug = "table"
    return slug[:_MAX_TABLE_HINT_CHARS]


def map_extractor_tables(
    raw_tables: Optional[Sequence[Any]],
    *,
    default_hint_prefix: str = "table",
) -> List[Dict[str, Any]]:
    if not raw_tables:
        return []
    mapped: List[Dict[str, Any]] = []
    for idx, raw in enumerate(raw_tables):
        headers_raw = _table_attr(raw, "headers")
        rows_raw = _table_attr(raw, "rows")
        if not isinstance(headers_raw, list) or not headers_raw:
            continue
        if not isinstance(rows_raw, list):
            continue

        hint = _table_attr(raw, "client_table_hint", "hint", "id")
        hint_s = str(hint).strip() if hint is not None else ""
        if not hint_s:
            title_for_hint = _table_attr(raw, "title")
            hint_s = _slug_hint(
                str(title_for_hint) if title_for_hint else "",
                f"{default_hint_prefix}-{idx + 1}",
            )

        headers = [str(h)[:_MAX_HEADER_CHARS] for h in headers_raw[:_MAX_TABLE_COLUMNS]]
        if not headers:
            continue
        col_count = len(headers)

        rows: List[List[str]] = []
        for row in rows_raw[:_MAX_TABLE_ROWS]:
            if not isinstance(row, (list, tuple)):
                continue
            if len(row) != col_count:
                continue
            rows.append([str(cell)[:_MAX_CELL_CHARS] for cell in row])
        if not rows:
            continue

        item: Dict[str, Any] = {
            "client_table_hint": hint_s[:_MAX_TABLE_HINT_CHARS],
            "headers": headers,
            "rows": rows,
        }
        title = _table_attr(raw, "title")
        if title is not None:
            ts = str(title).strip()
            if ts:
                item["title"] = ts[:_MAX_TABLE_TITLE_CHARS]
        page = _table_attr(raw, "page", "page_number")
        if page is not None:
            try:
                page_i = int(page)
                if 1 <= page_i <= 100000:
                    item["page"] = page_i
            except (TypeError, ValueError):
                pass
        bbox = _table_attr(raw, "bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            try:
                item["bbox"] = [float(bbox[i]) for i in range(4)]
            except (TypeError, ValueError):
                pass
        mapped.append(item)
    return mapped


def assign_tables_to_parts(
    parts: List[Dict[str, Any]],
    tables: List[Dict[str, Any]],
    *,
    text: str,
    part_max_chars: int,
) -> None:
    if not tables or not parts:
        return

    boundaries: List[tuple[int, int]] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + part_max_chars, length)
        boundaries.append((start, end))
        start = end
    if not boundaries:
        boundaries = [(0, 0)]

    buckets: List[List[Dict[str, Any]]] = [[] for _ in parts]
    for table in tables:
        part_idx = 0
        page = table.get("page")
        if page is not None:
            for i, part in enumerate(parts):
                if part.get("page_number") == page:
                    part_idx = i
                    break
        if part_idx >= len(buckets):
            part_idx = len(buckets) - 1
        if len(buckets[part_idx]) < _MAX_TABLES_PER_PART:
            buckets[part_idx].append(table)

    for i, part in enumerate(parts):
        if buckets[i]:
            part["tables"] = buckets[i]
