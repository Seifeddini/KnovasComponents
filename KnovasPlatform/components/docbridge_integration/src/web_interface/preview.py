"""Dokument-Extraktion fuer die Vorschau.

Traegt bewusst kein Flask-Wissen, damit die Extraktion isoliert testbar bleibt.

Sicherheitsposition: dieses Modul gibt **Markdown** heraus, niemals HTML.
``knovas_extract._markdown`` ist die Trust Boundary gegenueber feindlichen
Dokumenten -- es entfernt Markup, Event-Handler und gefaehrliche URL-Schemata.
Es escaped jedoch keinen *Textinhalt*: ein Dokument, das woertlich
``<script>`` enthaelt, liefert diese Zeichen unveraendert zurueck. Der Client
muss deshalb zuerst escapen und erst danach formatieren.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

PREVIEW_KIND_BY_SUFFIX: Dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "txt",
    ".msg": "msg",
}

# Obergrenzen fuer die Extraktion. Bewusst konservativ: die Vorschau ist eine
# Entscheidungshilfe in der Trefferliste, kein vollwertiger Dokumentbetrachter.
MAX_INPUT_BYTES = 25 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024


class PreviewUnsupported(Exception):
    """Das Format hat keinen Vorschaupfad."""


class PreviewFailed(Exception):
    """Die Datei ist beschaedigt, verschluesselt oder zu gross."""


def preview_kind(path: str) -> Optional[str]:
    """Vorschau-Art anhand der Dateiendung, oder None."""
    _, suffix = os.path.splitext(path or "")
    return PREVIEW_KIND_BY_SUFFIX.get(suffix.lower())


def extract_markdown(path: str) -> Dict[str, Any]:
    """Extrahiert ``path`` nach sanitisiertem Markdown.

    PDF gehoert nicht hierher -- es wird im Browser nativ dargestellt.
    """
    kind = preview_kind(path)
    if kind is None or kind == "pdf":
        raise PreviewUnsupported(path)

    import knovas_extract
    from knovas_extract.errors import ExtractError
    from knovas_extract.result import Limits

    limits = Limits(max_input_bytes=MAX_INPUT_BYTES, max_text_bytes=MAX_TEXT_BYTES)
    try:
        result = knovas_extract.extract(path, limits=limits, emit_markdown=True)
    except (ExtractError, OSError, ValueError) as exc:
        # ExtractError covers the library's own typed hierarchy. ValueError
        # comes from its path validation (NUL bytes, control chars, etc.);
        # OSError covers filesystem races (missing file, permission denied)
        # from its internal, unguarded ``open()`` call. Widening the catch
        # keeps PreviewFailed a reliable contract for callers.
        raise PreviewFailed(str(exc)) from exc

    markdown = result.content.markdown or ""
    metadata = result.metadata
    meta: Dict[str, Any] = {
        "title": metadata.title,
        "page_count": metadata.page_count,
        "word_count": metadata.word_count,
        "created": metadata.created,
        "modified": metadata.modified,
    }
    # MSG legt Absender, Empfaenger und Body-Quelle unter msg:* in extra ab.
    for key, value in (metadata.extra or {}).items():
        if key.startswith("msg:"):
            meta[key] = value

    return {
        "kind": kind,
        "markdown": markdown,
        "meta": meta,
        "warnings": list(result.warnings),
    }


# Breite der Seitenvorschau in Pixeln. Bewusst klein: sie sitzt in einer
# Trefferkarte, nicht im Viewer, und wird pro Treffer einmal geladen.
THUMBNAIL_WIDTH = 480


def render_first_page_png(path: str) -> bytes:
    """Rendert Seite 1 eines PDFs als PNG.

    Nur PDF: fuer DOCX/TXT/MSG gaebe es keine Seite, ohne sie vorher zu
    konvertieren -- und ein Konverter im Serving-Pfad ist bewusst nicht Teil
    dieser Anwendung.
    """
    if preview_kind(path) != "pdf":
        raise PreviewUnsupported(path)

    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - pymupdf ist Pflichtdependency
        raise PreviewFailed(f"pymupdf missing: {exc}") from exc

    try:
        with pymupdf.open(path) as doc:
            if doc.page_count < 1:
                raise PreviewFailed("PDF has no pages")
            page = doc.load_page(0)
            scale = THUMBNAIL_WIDTH / max(page.rect.width, 1)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
            return pix.tobytes("png")
    except PreviewFailed:
        raise
    except Exception as exc:
        raise PreviewFailed(str(exc)) from exc
