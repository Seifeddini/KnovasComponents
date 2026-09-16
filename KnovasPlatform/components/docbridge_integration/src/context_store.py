"""Read/write per-document context sidecars for search-result previews."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SIDECAR_VERSION = 1
MAX_SENTENCE_CHARS = 2000
MAX_FIRST_PAGE_CHARS = 8000
MAX_SIDEcar_SENTENCES = 50_000
FIRST_PAGE_FALLBACK_SENTENCES = 15
DEFAULT_CONTEXT_RADIUS = 10


def _truncate(text: str, max_chars: int) -> str:
    raw = str(text or "").strip()
    if len(raw) <= max_chars:
        return raw
    return raw[: max_chars - 1].rstrip() + "…"


def sidecar_path_for_pointer(store_dir: Path, pointer: str) -> Path:
    digest = hashlib.sha256(str(pointer or "").encode("utf-8")).hexdigest()
    return store_dir / f"{digest}.json"


def build_sentence_records_from_dicts(
    text: str,
    sentences: Sequence[Any],
) -> List[Dict[str, Any]]:
    """Build compact sentence records from knovas-extract Sentence objects."""
    if not text or not sentences:
        return []
    ordered = sorted(sentences, key=lambda s: s.char_start)
    records: List[Dict[str, Any]] = []
    length = len(text)
    for idx, sent in enumerate(ordered):
        if len(records) >= MAX_SIDEcar_SENTENCES:
            break
        start = max(0, min(sent.char_start, length))
        end = ordered[idx + 1].char_start if idx + 1 < len(ordered) else length
        end = max(start, min(end, length))
        chunk = text[start:end].strip()
        if not chunk:
            continue
        entry: Dict[str, Any] = {
            "i": int(sent.index) + 1,
            "t": _truncate(chunk, MAX_SENTENCE_CHARS),
        }
        page = getattr(sent, "page_number", None)
        if page is not None and int(page) >= 1:
            entry["p"] = int(page)
        records.append(entry)
    return records


def _sentence_text(sent: Any) -> str:
    """The text of one sentence record, whatever shape it is stored in.

    Sidecars are written by whichever version of the ingest was current, so a
    sentence may be {"t": ...}, {"text": ...} or a bare string. This is
    decoration on a search result: an unfamiliar shape must degrade to no
    snippet, never raise. It used to raise -- `sent.get("t")` on a plain string
    is an AttributeError, which surfaced as "Fehler bei der Suche: Interner
    Serverfehler" for a query that was otherwise perfectly good.
    """
    if isinstance(sent, str):
        return sent.strip()
    if isinstance(sent, dict):
        for key in ("t", "text", "sentence", "s"):
            value = sent.get(key)
            if value:
                return str(value).strip()
    return ""


def build_first_page_payload(sentences: Sequence[Any]) -> Dict[str, Any]:
    """First-page text from sentence records, whatever shape they are in.

    Reads through _sentence_text for the same reason context_window does: this
    also runs over sidecars written by an older ingest, where `s["t"]` is a
    KeyError or an AttributeError rather than a sentence.
    """
    page_one = [
        _sentence_text(s)
        for s in sentences
        if isinstance(s, dict) and s.get("p") == 1
    ]
    page_one = [text for text in page_one if text]
    if page_one:
        body = " ".join(page_one)
    elif sentences:
        body = " ".join(
            text
            for text in (
                _sentence_text(s) for s in sentences[:FIRST_PAGE_FALLBACK_SENTENCES]
            )
            if text
        )
    else:
        body = ""
    return {"page": 1, "text": _truncate(body, MAX_FIRST_PAGE_CHARS)}


def build_sidecar_payload(
    pointer: str,
    path: str,
    text: str,
    sentences: Optional[Sequence[Any]],
) -> Dict[str, Any]:
    sentence_records = build_sentence_records_from_dicts(text, sentences or [])
    return {
        "version": SIDECAR_VERSION,
        "pointer": str(pointer or "").strip(),
        "path": str(path or "").strip(),
        "sentences": sentence_records,
        "first_page": build_first_page_payload(sentence_records),
    }


def write_context_sidecar(
    store_dir: Optional[str],
    pointer: str,
    path: str,
    text: str,
    sentences: Optional[Sequence[Any]],
) -> bool:
    if not store_dir or not str(store_dir).strip():
        return False
    pointer_s = str(pointer or "").strip()
    if not pointer_s:
        return False
    root = Path(store_dir).resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
        payload = build_sidecar_payload(pointer_s, path, text, sentences)
        target = sidecar_path_for_pointer(root, pointer_s)
        fd, tmp = tempfile.mkstemp(dir=root, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except Exception as exc:
        logger.warning("Failed to write context sidecar for %s: %s", pointer_s, exc)
        return False


@lru_cache(maxsize=4096)
def _load_sidecar_file(path: str, mtime: float) -> Optional[Dict[str, Any]]:
    del mtime
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as exc:
        logger.debug("Could not load context sidecar %s: %s", path, exc)
        return None


def load_context(store_dir: Optional[str], pointer_candidates: Sequence[str]) -> Optional[Dict[str, Any]]:
    if not store_dir or not str(store_dir).strip():
        return None
    root = Path(store_dir)
    if not root.is_dir():
        return None
    seen: set[str] = set()
    for raw in pointer_candidates:
        pointer = str(raw or "").strip()
        if not pointer or pointer in seen:
            continue
        seen.add(pointer)
        path = sidecar_path_for_pointer(root, pointer)
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        data = _load_sidecar_file(str(path), mtime)
        if data:
            return data
    return None


def first_page_text(entry: Optional[Dict[str, Any]], max_chars: int = MAX_FIRST_PAGE_CHARS) -> str:
    if not entry:
        return ""
    first = entry.get("first_page")
    if isinstance(first, dict):
        text = first.get("text")
        if isinstance(text, str) and text.strip():
            return _truncate(text.strip(), max_chars)
    sentences = entry.get("sentences")
    if isinstance(sentences, list):
        return _truncate(build_first_page_payload(sentences).get("text", ""), max_chars)
    return ""


def _anchor_sentence_index(
    sentences: List[Dict[str, Any]],
    sentence_number: Optional[int],
) -> int:
    if not sentences:
        return 0
    by_i: Dict[int, int] = {}
    for idx, sent in enumerate(sentences):
        if not isinstance(sent, dict) or "i" not in sent:
            continue
        try:
            by_i[int(sent["i"])] = idx
        except (TypeError, ValueError):
            continue
    if sentence_number is not None:
        try:
            target = int(sentence_number)
        except (TypeError, ValueError):
            target = None
        if target is not None and target in by_i:
            return by_i[target]
    return 0


def context_window(
    sentences: List[Dict[str, Any]],
    sentence_number: Optional[int],
    radius: int = DEFAULT_CONTEXT_RADIUS,
) -> Optional[Dict[str, str]]:
    if not sentences or radius < 0:
        return None
    anchor_idx = _anchor_sentence_index(sentences, sentence_number)
    start = max(0, anchor_idx - radius)
    end = min(len(sentences), anchor_idx + radius + 1)
    window = sentences[start:end]
    if not window:
        return None
    before_parts: List[str] = []
    match_text = ""
    after_parts: List[str] = []
    for idx, sent in enumerate(window):
        global_idx = start + idx
        text = _sentence_text(sent)
        if not text:
            continue
        if global_idx < anchor_idx:
            before_parts.append(text)
        elif global_idx == anchor_idx:
            match_text = text
        else:
            after_parts.append(text)
    if not match_text and anchor_idx < len(sentences):
        match_text = _sentence_text(sentences[anchor_idx])
    return {
        "before": " ".join(before_parts).strip(),
        "match": match_text,
        "after": " ".join(after_parts).strip(),
    }


def resolve_sentence_number(result: Dict[str, Any]) -> Optional[int]:
    raw = result.get("sentence_number")
    if raw is not None and str(raw).strip() != "":
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    chunks = result.get("top_chunks")
    if isinstance(chunks, list) and chunks:
        first = chunks[0]
        if isinstance(first, dict):
            raw = first.get("sentence_number")
            if raw is not None and str(raw).strip() != "":
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    pass
    return None


# Wie viele Fundstellen der Dialog auflistet. Knovas liefert oft mehr, aber eine
# Liste, durch die man scrollen muss, hilft beim Lesen nicht mehr.
MAX_MATCH_LOCATIONS = 8

# Enger als das Kartensnippet: in der Fundstellenliste steht eine Zeile pro
# Treffer, kein Absatz.
MATCH_LOCATION_RADIUS = 1


def _location_page(chunk: Dict[str, Any]) -> Optional[int]:
    raw = chunk.get("page_number")
    if raw is None:
        raw = chunk.get("page")
    try:
        page = int(raw)
    except (TypeError, ValueError):
        return None
    return page if page >= 1 else None


# Woerter, die in jedem deutschen Satz stehen. Sie als Treffer zu markieren
# faerbt das halbe Dokument ein und sagt nichts: wer "Die Mandantin Alpenblick
# Gastro GmbH beauftragt die Kanzlei" sucht, meint Alpenblick und Gastro.
STOPWORDS: frozenset = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
    "einer", "eines", "und", "oder", "aber", "auch", "als", "am", "an", "auf",
    "aus", "bei", "bis", "durch", "für", "fuer", "gegen", "im", "in", "ist",
    "mit", "nach", "nicht", "noch", "von", "vom", "vor", "zu", "zum", "zur",
    "über", "ueber", "unter", "wird", "werden", "wurde", "war", "sind", "sein",
    "hat", "haben", "kann", "können", "koennen", "soll", "sollen", "muss",
    "müssen", "muessen", "es", "er", "sie", "wir", "ich", "man", "sich", "dass",
    "wenn", "weil", "dies", "diese", "dieser", "dieses", "the", "and", "for",
    "with", "that", "this", "from", "are", "was", "has", "have",
})


def query_terms(query: str, min_length: int = 2) -> List[str]:
    """The words a reader expects to find again, lowercased.

    Stopwords are dropped. They match everywhere, so highlighting them marks
    half the document and buries the words that were actually searched for --
    and a location containing only "die" is not a literal hit in any sense a
    reader would recognise.
    """
    import re as _re

    return [
        term for term in _re.split(r"\W+", str(query or "").lower())
        if len(term) >= min_length and term not in STOPWORDS
    ]


def build_match_locations(
    sentences: List[Dict[str, Any]],
    top_chunks: Any,
    *,
    radius: int = MATCH_LOCATION_RADIUS,
    limit: int = MAX_MATCH_LOCATIONS,
    terms: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """Every place in this document the query matched, with its page and text.

    Knovas reports the locations (``top_chunks`` carries page and sentence
    numbers); the sidecar holds the sentences. Neither alone can answer "where
    in this document, and what does it say there" -- which is the question a
    lawyer opens a 60-page contract with. Until now the two were combined for
    the first hit only, to make one snippet on the card, and the rest were
    dropped on the floor.

    Duplicate sentence numbers collapse: several chunks of one long sentence are
    one place to look, not three.

    ``terms`` are the words of the query. A location carries ``literal: True``
    when one of them actually appears in it, and those sort first. Without this
    the list is simply the highest-scoring passages of a document the vector
    search liked -- which, when someone searched for a person's name that occurs
    nowhere, is eight arbitrary sentences presented as "Fundstellen". The panel
    has to be able to tell the caller which it is holding.
    """
    if not sentences or not isinstance(top_chunks, list):
        return []
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for chunk in top_chunks:
        if len(out) >= limit:
            break
        if not isinstance(chunk, dict):
            continue
        raw = chunk.get("sentence_number")
        try:
            sentence_number = int(raw)
        except (TypeError, ValueError):
            continue
        if sentence_number in seen:
            continue
        seen.add(sentence_number)
        window = context_window(sentences, sentence_number, radius=radius)
        if not window or not (window.get("match") or "").strip():
            continue
        entry: Dict[str, Any] = {
            "sentence_number": sentence_number,
            "before": window.get("before", ""),
            "match": window.get("match", ""),
            "after": window.get("after", ""),
        }
        page = _location_page(chunk)
        if page is not None:
            entry["page"] = page
        if terms:
            # Nur der Trefferatz selbst entscheidet, nicht sein Umfeld: mit
            # Radius 1 steht der Nachbarsatz mit im Fenster, und dann waere
            # jeder Satz neben einem Treffer selbst einer.
            anchor = str(entry["match"]).lower()
            entry["literal"] = any(term in anchor for term in terms)
        out.append(entry)
    # Stable: the backend's ordering is kept inside each group, so the best
    # scoring literal hit still comes before a weaker one.
    if terms:
        out.sort(key=lambda e: not e.get("literal"))
    return out


# Obergrenze für den Text, den die Vorschau aus dem Suchindex zeigt. Grosszügig:
# hier ist es das ganze Dokument oder nichts, die Datei gibt es ja nicht mehr.
MAX_INDEX_TEXT_CHARS = 200_000


def indexed_text(entry: Optional[Dict[str, Any]], max_chars: int = MAX_INDEX_TEXT_CHARS) -> str:
    """Der ganze Text eines Dokuments, so wie er beim Indexieren gelesen wurde.

    Der Sidecar hält jeden Satz -- er ist geschrieben worden, damit die Treffer
    Kontext haben. Ist die Datei selbst nicht mehr auf dem Dokumentenspeicher
    (umbenannt, verschoben, ein neu erzeugtes Korpus mit neuen Namen), dann ist
    das hier das Einzige, was von ihr übrig ist, und immer noch lesbar. Ein
    Treffer, den man nur anschauen und nicht lesen kann, ist für die Anwältin
    keiner.

    Absätze statt einer Textwand: ein Satz je Zeile liest sich in der Vorschau
    wie ein Dokument und nicht wie ein Log.
    """
    if not entry:
        return ""
    sentences = entry.get("sentences")
    if not isinstance(sentences, list):
        return ""
    parts: List[str] = []
    size = 0
    for sent in sentences:
        text = _sentence_text(sent)
        if not text:
            continue
        size += len(text) + 1
        if size > max_chars:
            parts.append("…")
            break
        parts.append(text)
    return "\n\n".join(parts).strip()


def sentences_by_number(
    entry: Optional[Dict[str, Any]],
    numbers: Sequence[int],
) -> List[str]:
    """Der Text der genannten Saetze, in der Reihenfolge der Anfrage.

    Der Aufrufer kennt die Satznummern einer Trefferstelle (sie stehen in
    ``match_locations``), aber nicht ihren Text -- der steht im Sidecar. So
    kann die Vorschau die Stelle markieren, ohne dass ganze Saetze durch die
    Adresszeile geschickt werden.
    """
    if not entry or not numbers:
        return []
    sentences = entry.get("sentences")
    if not isinstance(sentences, list):
        return []
    by_number: Dict[int, str] = {}
    for index, sent in enumerate(sentences):
        if isinstance(sent, dict):
            raw = sent.get("i")
        else:
            raw = index + 1
        try:
            by_number[int(raw)] = _sentence_text(sent)
        except (TypeError, ValueError):
            continue
    out: List[str] = []
    for number in numbers:
        text = by_number.get(int(number), "").strip()
        if text:
            out.append(text)
    return out


def enrich_result_with_context(
    result: Dict[str, Any],
    store_dir: Optional[str],
    pointer_candidates: Sequence[str],
    *,
    context_radius: int = DEFAULT_CONTEXT_RADIUS,
    terms: Sequence[str] = (),
) -> bool:
    """Attach first_page_preview and context_snippet when a sidecar exists."""
    if result.get("match_locations") or (
        result.get("first_page_preview") and result.get("context_snippet")
    ):
        return True
    # A sidecar written by another version can be shaped in ways this code does
    # not expect. That is a reason to show no snippet, never a reason to fail
    # the search: the results are correct and complete without it, and a 500
    # here reads to the user as "search is broken".
    try:
        entry = load_context(store_dir, pointer_candidates)
        if not entry:
            return False
        first = first_page_text(entry)
        sentences = entry.get("sentences")
        snippet = None
        locations: List[Dict[str, Any]] = []
        if isinstance(sentences, list):
            snippet = context_window(
                sentences, resolve_sentence_number(result), radius=context_radius
            )
            locations = build_match_locations(
                sentences, result.get("top_chunks"), terms=terms
            )
    except Exception as exc:  # noqa: BLE001 - decoration must not break the result
        logger.warning(
            "Context enrichment skipped for %s: %s",
            (list(pointer_candidates) or ["<no pointer>"])[0], exc,
        )
        return False
    if first:
        result["first_page_preview"] = first
    if snippet:
        result["context_snippet"] = snippet
    if locations:
        result["match_locations"] = locations
    return bool(first or snippet)
