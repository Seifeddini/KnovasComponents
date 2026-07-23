"""Build Knovas transmission payloads from knovas-extract ExtractionResult."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

from knovas_extract.result import Page, Section, Sentence

from sync.chunking import build_transmission_parts
from sync.table_payload import map_extractor_tables


@dataclass(frozen=True)
class ExtractionPayload:
    """Normalized extractor output for the RC upload pipeline."""

    text: str
    sentences: Optional[list[Sentence]]
    sections: Optional[list[Section]]
    pages: Optional[list[Page]]
    title: Optional[str]
    description: Optional[str]
    tables: Optional[list[dict[str, Any]]]


def description_from_metadata(metadata: Any) -> Optional[str]:
    if metadata is None:
        return None
    extra = getattr(metadata, "extra", None) or {}
    for key in ("docx:subject", "subject", "description"):
        val = extra.get(key) if isinstance(extra, dict) else None
        if val is not None:
            s = str(val).strip()
            if s:
                return s[:2000]
    return None


def payload_from_extraction_result(result: Any) -> ExtractionPayload:
    content = result.content
    metadata = result.metadata
    text = str(content.text or "")
    tables_raw = getattr(content, "tables", None)
    tables = map_extractor_tables(tables_raw) if tables_raw else None
    return ExtractionPayload(
        text=text,
        sentences=content.sentences,
        sections=content.sections,
        pages=content.pages,
        title=getattr(metadata, "title", None),
        description=description_from_metadata(metadata),
        tables=tables,
    )


def build_parts_from_payload(
    payload: ExtractionPayload,
    part_max_chars: int,
) -> list[dict[str, Any]]:
    """Chunk payload.text with sentence/page/section/table metadata for transmit."""
    return build_transmission_parts(
        payload.text,
        part_max_chars,
        sentences=payload.sentences,
        sections=payload.sections,
        pages=payload.pages,
        tables=payload.tables,
    )
