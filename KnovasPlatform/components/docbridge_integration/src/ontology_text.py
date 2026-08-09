"""Wortlaut zu einem Dokument-Pointer aufloesen.

Die Knovas-API liefert bewusst keinen Passagentext: /secured/query gibt
Pointer, Seite und Scores zurueck, aber keinen Fliesstext, und es gibt
keinen Endpunkt, um Chunk-Text nachzuladen (Secure_API.md). Der Cortex
lebt aber vom woertlichen Beleg.

Diese Schicht schliesst die Luecke lokal: Sie bekommt einen Pointer, findet
die zugehoerige Datei unter dem AutoDoc-Wurzelverzeichnis und liest den
Wortlaut aus dem PDF. Damit kommt die Semantik vom Backend und der Wortlaut
aus der Quelle. Wird das spaeter ein eigener Dienst, tauscht nur diese
Datei ihr Inneres - die Aufrufer bleiben unveraendert.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MIN_QUOTE_CHARS = 20


def _fold(text: str) -> str:
    return (text.lower()
            .replace("ä", "a").replace("ö", "o").replace("ü", "u")
            .replace("ß", "ss"))


def _split_sentences(flat: str) -> List[str]:
    """Grober Satz-Split; Ordinalzahlen und Abkuerzungen bleiben zusammen."""
    abbrev = re.compile(r"(?:\b(?:Nr|Art|Abs|Ziff|Dr|bzw|ca|inkl|vgl)|(?<![\d])\b\d{1,2})\.$")
    merged: List[str] = []
    for piece in re.split(r"(?<=[.!?])\s+", flat):
        piece = piece.strip()
        if not piece:
            continue
        if merged and abbrev.search(merged[-1]):
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    return merged


class DocumentTextResolver:
    """Pointer -> Seitentext bzw. belegte Fundstelle. Cached per (Pfad, mtime)."""

    def __init__(self, resolve_path: Optional[Callable[[str], Optional[str]]] = None):
        self._resolve_path = resolve_path
        self._cache: Dict[Tuple[str, float], Dict[int, str]] = {}

    def _pages(self, pointer: str) -> Dict[int, str]:
        """Seitentexte eines Dokuments; leer, wenn nicht lesbar."""
        abs_path = self._resolve_path(pointer) if self._resolve_path else None
        if not abs_path or not os.path.isfile(abs_path):
            return {}
        try:
            mtime = os.stat(abs_path).st_mtime
        except OSError:
            return {}
        key = (abs_path, mtime)
        if key in self._cache:
            return self._cache[key]
        pages: Dict[int, str] = {}
        try:
            import fitz  # pymupdf, lazy: nicht jeder Aufrufer braucht PDFs

            with fitz.open(abs_path) as doc:
                for page_no, page in enumerate(doc, start=1):
                    pages[page_no] = page.get_text().strip()
        except Exception as exc:      # kaputte Datei darf nie eskalieren
            logger.warning("Text nicht lesbar (%s): %s", abs_path, exc)
            pages = {}
        self._cache[key] = pages
        return pages

    def page_count(self, pointer: str) -> int:
        return len(self._pages(pointer))

    def quote_on_page(self, pointer: str, page: int,
                      needle: Optional[str] = None) -> str:
        """Satz von einer bekannten Seite. Mit needle den treffenden Satz,
        sonst den ersten brauchbaren - so bleibt der Beleg woertlich."""
        text = self._pages(pointer).get(int(page or 0), "")
        if not text:
            return ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return ""
        body = " ".join(lines[1:]) if len(lines) > 1 else lines[0]
        sentences = [s for s in _split_sentences(body) if len(s) >= MIN_QUOTE_CHARS]
        if not sentences:
            return ""
        if needle:
            folded = _fold(needle)
            for sentence in sentences:
                if folded in _fold(sentence):
                    return sentence
        return sentences[0]

    def find_mention(self, pointer: str, needle: str) -> Optional[Tuple[int, str]]:
        """Erste woertliche Erwaehnung: (Seite, Satz). None, wenn keine da ist -
        dann wird kein Zitat erfunden."""
        needle = str(needle or "").strip()
        if not needle:
            return None
        folded = _fold(needle)
        for page_no, text in sorted(self._pages(pointer).items()):
            if folded not in _fold(text):
                continue
            quote = self.quote_on_page(pointer, page_no, needle)
            if quote:
                return page_no, quote
        return None
