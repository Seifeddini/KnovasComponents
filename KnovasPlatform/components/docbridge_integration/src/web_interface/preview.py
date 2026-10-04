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

import inspect
import os
import re
from typing import Any, Dict, List, Optional, Tuple

# Jede Endung, die Knovas Connector standardmaessig aufnimmt
# (sync/default_sync_body.py::_DEFAULT_INCLUDE_GLOBS), hat hier einen Eintrag.
# Fehlt einer, antwortet ``preview-content`` 415 und der Dialog zeigt
# "Vorschau nicht verfuegbar (HTTP 415)" -- fuer ein Format, das die Suche
# sehr wohl findet. Bei einer Kanzlei war das die Haelfte des Bestands: EML
# und MD wurden aufgenommen und hatten gar keine Vorschau, obwohl
# ``knovas_extract`` beide laengst kann (Extras ``markdown`` und der
# message/rfc822-Pfad, siehe Dockerfile).
PREVIEW_KIND_BY_SUFFIX: Dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "txt",
    ".msg": "msg",
    ".eml": "eml",
    ".md": "md",
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


#: Warnung, wenn die Vorschau statt des Markdowns den reinen Text zeigt.
MARKDOWN_FALLBACK_WARNING = "preview: markdown limit exceeded; plain text shown"


def _markdown_limit(exc: Exception) -> bool:
    """Ob ``exc`` eine Markdown-Grenze der Bibliothek ist: der
    Expansionswaechter (``markdown expansion ratio``) oder die Groesse des
    Markdowns (``markdown size``). Der Text selbst laesst sich dann zeigen."""
    return str(getattr(exc, "what", "") or "").startswith("markdown")


def _docx_layout(kind: str, extract: Any) -> Dict[str, Any]:
    """``text_mode="layout"`` fuer eine DOCX, sofern ``extract`` es kennt.

    Im Standardmodus ``plain`` stehen die Tabellen einer DOCX nicht im Text,
    nur in ``content.tables``. Der Expansionswaechter verglich das Markdown,
    Tabellen inklusive, deshalb mit den Absaetzen allein und brach bei jeder
    DOCX ab, deren Tabellen ihren Fliesstext ueberwiegen; der reine Text des
    Rueckfalls zeigte dann nur die Absaetze -- von einer Honorarabrechnung die
    Ueberschrift. Im Layoutmodus stehen die Tabellenzeilen im Text (spec L3),
    das Markdown bleibt dasselbe. knovas-extract vor 0.4 kennt ``text_mode``
    nicht und wuerde TypeError werfen; dann bleibt es beim Standard. Ein 0.4
    ohne DOCX-Layoutmodus liefert den Standardtext und eine Warnung.
    """
    if kind != "docx":
        return {}
    try:
        accepted = "text_mode" in inspect.signature(extract).parameters
    except (TypeError, ValueError):
        accepted = False
    return {"text_mode": "layout"} if accepted else {}


def _markdown_or_text(
    extract: Any, path: str, limits: Any, mode: Dict[str, Any]
) -> Tuple[Any, str, List[str]]:
    """Ergebnis, Markdown und eigene Warnungen der Vorschau. Loest das
    Markdown eine Markdown-Grenze der Bibliothek aus, steht der reine Text an
    seiner Stelle."""
    from knovas_extract.errors import ResourceExhaustedError

    try:
        result = extract(path, limits=limits, emit_markdown=True, **mode)
        return result, result.content.markdown or "", []
    except ResourceExhaustedError as exc:
        if not _markdown_limit(exc):
            raise
    result = extract(path, limits=limits, **mode)
    return result, result.content.text or "", [MARKDOWN_FALLBACK_WARNING]


def extract_markdown(path: str) -> Dict[str, Any]:
    """Extrahiert ``path`` nach sanitisiertem Markdown.

    PDF gehoert nicht hierher -- es wird im Browser nativ dargestellt.

    Eine DOCX wird im Layoutmodus gelesen (``_docx_layout``): ihre
    Tabellenzeilen stehen im Text. Loest das Markdown trotzdem eine
    Markdown-Grenze der Bibliothek aus -- eine Tabelle aus fast leeren Zellen
    ergibt ein Vielfaches ihres Textes, und der Expansionswaechter bricht
    ab --, zeigt die Vorschau den reinen Text statt eines Fehlers (spec L7),
    die Tabellenzeilen eingeschlossen. Der Client escapt ihn wie jedes
    Markdown.
    """
    kind = preview_kind(path)
    if kind is None or kind == "pdf":
        raise PreviewUnsupported(path)

    import knovas_extract
    from knovas_extract.errors import ExtractError, ResourceExhaustedError
    from knovas_extract.result import Limits

    limits = Limits(max_input_bytes=MAX_INPUT_BYTES, max_text_bytes=MAX_TEXT_BYTES)
    extract = knovas_extract.extract
    layout = _docx_layout(kind, extract)
    try:
        try:
            result, markdown, warnings = _markdown_or_text(extract, path, limits, layout)
        except ResourceExhaustedError:
            if not layout:
                raise
            # Die Tabellenzeilen sind eine Zugabe: haelt der Layouttext eine
            # Grenze der Bibliothek nicht ein, liest die Vorschau die DOCX im
            # Standardmodus, wie zuvor.
            result, markdown, warnings = _markdown_or_text(extract, path, limits, {})
    except (ExtractError, OSError, ValueError) as exc:
        # ExtractError covers the library's own typed hierarchy. ValueError
        # comes from its path validation (NUL bytes, control chars, etc.);
        # OSError covers filesystem races (missing file, permission denied)
        # from its internal, unguarded ``open()`` call. Widening the catch
        # keeps PreviewFailed a reliable contract for callers.
        raise PreviewFailed(str(exc)) from exc

    metadata = result.metadata
    meta: Dict[str, Any] = {
        "title": metadata.title,
        "page_count": metadata.page_count,
        "word_count": metadata.word_count,
        "created": metadata.created,
        "modified": metadata.modified,
    }
    # MSG legt Absender, Empfaenger und Body-Quelle unter msg:* ab, EML unter
    # eml:* -- gleiche Felder, anderes Praefix. Nur msg:* zu uebernehmen hiess,
    # dass eine E-Mail als .eml ihren Kopf verliert und als blosser Fliesstext
    # erscheint, waehrend dieselbe Mail als .msg Von und An zeigt.
    for key, value in (metadata.extra or {}).items():
        if key.startswith("msg:") or key.startswith("eml:"):
            meta[key] = value

    return {
        "kind": kind,
        "markdown": markdown,
        "meta": meta,
        "warnings": warnings + list(result.warnings),
    }


# Obergrenzen fuer das Markieren. Eine Kanzleiakte kann Hunderte Seiten haben,
# und die Suche laeuft auf dem Request-Thread.
HIGHLIGHT_MAX_PAGES = 200
HIGHLIGHT_MAX_TERMS = 6
HIGHLIGHT_MAX_PER_PAGE = 40
HIGHLIGHT_MAX_PASSAGES = 8
# Wie weit ueber der Trefferzeile der Viewer einsteigt, in PDF-Punkten.
ANCHOR_MARGIN = 60

# Markenfarben statt des PDF-Standardgelbs: --highlight fuer die Trefferstelle,
# --accent abgeschwaecht fuer die gesuchten Woerter darin.
PASSAGE_COLOUR = (0.851, 0.878, 0.969)
# Die angeklickte Trefferstelle. Der browsereigene Viewer kann nichts
# hervorheben, was man ihm zuruft -- also wird die aktive Stelle beim Ausliefern
# kraeftiger eingefaerbt. Ohne das sind alle Stellen gleich bunt, und ein Klick
# auf eine andere Fundstelle aendert im PDF sichtbar gar nichts.
ACTIVE_PASSAGE_COLOUR = (0.996, 0.839, 0.404)
TERM_COLOUR = (0.647, 0.757, 0.976)

# Ab wie vielen Zeichen eine einzelne Tabellenzelle als Suchtext taugt. Kuerzer
# sind Jahreszahlen und Betraege, die auf jeder Bilanzseite stehen.
MIN_CELL_CHARS = 12
# Zellentrenner der markdown-lite-Zeilen (knovas-extract ``text_mode="layout"``).
ROW_CELL_SEPARATOR = " | "
# Ein Fold-Schluessel vor einer Zahl: "2023: 1'234'567.80", "Anhang: 2.1",
# "2022: (12.00)". Er steht im Sidecar, nicht auf der Seite.
_FOLD_KEY = re.compile(r"\b\S{1,16}: (?=[\d(-])")


def search_spans(span: str) -> List[str]:
    """Was auf der Seite gesucht wird, wenn ``span`` eine Fundstelle ist.

    Eine Prosazeile steht so im PDF wie im Sidecar. Eine Tabellenzeile nicht:
    der Sidecar traegt sie als ``Fluessige Mittel | 2023: 1'234'567.80 | 2022:
    987'654.30`` -- Zellentrenner und Fold-Schluessel sind die Zutat des
    Extraktors, auf der digitalen Seite stehen die Zellen durch Leerraum
    getrennt. ``page.search_for`` der ganzen Zeile fand deshalb nichts, und
    ein Klick auf die Fundstelle fuehrte nirgendwohin (Diagnose P6).

    Reihenfolge: die Zeile selbst, dann ohne Trenner und Schluessel, dann
    die laengste Zelle allein (ab ``MIN_CELL_CHARS``) -- eine Bilanzzeile
    kann im PDF ueber zwei Spalten mit grossem Abstand gesetzt sein, die
    Postenbezeichnung steht aber in einem Stueck.
    """
    raw = " ".join(str(span or "").split())
    out: List[str] = []
    if len(raw) >= MIN_CELL_CHARS:
        out.append(raw)
    if ROW_CELL_SEPARATOR not in raw and not _FOLD_KEY.search(raw):
        return out
    flat = " ".join(_FOLD_KEY.sub("", raw.replace(ROW_CELL_SEPARATOR, " ")).split())
    if len(flat) >= MIN_CELL_CHARS and flat not in out:
        out.append(flat)
    cells = [
        " ".join(_FOLD_KEY.sub("", cell).split())
        for cell in raw.split(ROW_CELL_SEPARATOR)
    ]
    longest = max(cells, key=len) if cells else ""
    if len(longest) >= MIN_CELL_CHARS and longest not in out:
        out.append(longest)
    return out


def _search_page(page: Any, span: str) -> list:
    """Die Rechtecke der ersten Fassung von ``span``, die auf der Seite steht."""
    for candidate in search_spans(span):
        rects = page.search_for(candidate)
        if rects:
            return rects
    return []


def passage_anchors(path: str, passages: dict) -> dict:
    """Wo die Trefferstellen im PDF stehen: Seite und Hoehe je Satznummer.

    Der Viewer springt ueber das Fragment ``#page=N&view=FitH,<top>`` an eine
    Stelle IN der Seite. Ohne die Hoehe bleibt nur die Seite, und bei mehreren
    Fundstellen auf derselben Seite -- der Normalfall -- passiert beim Klicken
    schlicht nichts: dieselbe Adresse, dieselbe Ansicht.

    ``top`` zaehlt im PDF von unten, PyMuPDF misst von oben; deshalb die
    Differenz zur Seitenhoehe. Der Zuschlag setzt die Zeile etwas unter den
    oberen Rand, sonst klebt sie daran.
    """
    out: dict = {}
    if not passages or preview_kind(path) != "pdf":
        return out
    try:
        import pymupdf
    except ImportError:
        return out
    try:
        with pymupdf.open(path) as doc:
            for number, text in passages.items():
                span = str(text or "").strip()
                if not search_spans(span):
                    continue
                for index in range(min(doc.page_count, HIGHLIGHT_MAX_PAGES)):
                    page = doc[index]
                    rects = _search_page(page, span)
                    if not rects:
                        continue
                    top = page.rect.height - rects[0].y0 + ANCHOR_MARGIN
                    out[int(number)] = {
                        "page": index + 1,
                        "top": round(max(0.0, min(top, page.rect.height)), 1),
                    }
                    break
    except Exception:  # noqa: BLE001 - Springen ist Beiwerk, nie ein Fehlerfall
        return out
    return out


def highlight_pdf(path: str, terms, passages=None, active: str = "") -> Optional[bytes]:
    """Das PDF mit echten Markierungen auf den gesuchten Woertern.

    Der browsereigene Viewer kann nichts hervorheben, was man ihm sagt -- aber
    er zeigt Anmerkungen an, die im Dokument stehen. Also werden sie
    hineingeschrieben, bevor die Bytes ausgeliefert werden: PyMuPDF ist ohnehin
    Pflichtdependency, und der Viewer bleibt der, in dem sich gut liest.

    Veraendert wird nur der ausgelieferte Datenstrom. Die Datei auf dem
    Dokumentenspeicher wird nicht angefasst, und ``/download`` liefert weiter
    das Original -- wer ein Dokument aus der Akte holt, soll nicht unsere
    gelben Balken darin haben.

    ``passages`` sind die Trefferstellen selbst -- die Saetze, die Knovas
    gefunden hat. Sie werden flaechig markiert, die gesuchten Woerter darin
    kraeftiger. Ohne sie waere bei einem rein semantischen Treffer nichts
    hervorgehoben: die Stelle steht dann in der Liste, und im Dokument sucht
    man sie von Hand. Genau das ist der Fall, fuer den die semantische Suche
    ueberhaupt da ist.

    Gibt None zurueck, wenn nichts zu markieren war oder etwas schiefging; der
    Aufrufer liefert dann die Originaldatei aus.
    """
    wanted = [t for t in (terms or []) if len(t) >= 2][:HIGHLIGHT_MAX_TERMS]
    spans = [p for p in (passages or []) if search_spans(p)][:HIGHLIGHT_MAX_PASSAGES]
    if not (wanted or spans) or preview_kind(path) != "pdf":
        return None
    try:
        import pymupdf
    except ImportError:
        return None

    try:
        with pymupdf.open(path) as doc:
            marked = 0
            for page in doc.pages(0, min(doc.page_count, HIGHLIGHT_MAX_PAGES)):
                on_page = 0
                # Erst die Trefferstelle als Ganzes, dann die Woerter darin --
                # in dieser Reihenfolge, damit die Wortmarkierung obenauf liegt.
                # Die aktive Stelle zuletzt, damit ihre Farbe obenauf liegt.
                # Trefferstellen ueber ``_search_page`` (Tabellenzeilen werden
                # ohne Zellentrenner und Fold-Schluessel gesucht), die
                # gesuchten Woerter woertlich.
                for span, colour, is_passage in (
                    [(s, PASSAGE_COLOUR, True) for s in spans if s != active]
                    + [(w, TERM_COLOUR, False) for w in wanted]
                    + ([(active, ACTIVE_PASSAGE_COLOUR, True)] if active else [])
                ):
                    if on_page >= HIGHLIGHT_MAX_PER_PAGE:
                        break
                    rects = _search_page(page, span) if is_passage else page.search_for(span)
                    for rect in rects or []:
                        annot = page.add_highlight_annot(rect)
                        annot.set_colors(stroke=colour)
                        annot.update()
                        on_page += 1
                        marked += 1
                        if on_page >= HIGHLIGHT_MAX_PER_PAGE:
                            break
            if not marked:
                return None
            return doc.tobytes(garbage=0, deflate=True)
    except Exception:  # noqa: BLE001 - Markieren ist Beiwerk, nie ein Fehlerfall
        return None


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
