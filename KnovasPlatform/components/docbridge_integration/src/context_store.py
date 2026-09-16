"""Read/write per-document context sidecars for search-result previews."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re as _re_module
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


# Wie viel Text auf der Trefferkarte vor dem Treffersatz stehen darf. Die Karte
# klemmt bei drei Zeilen ab, und was davor steht, verdraengt den Treffer nach
# hinten: bei einer Mandatsvereinbarung waren das zehn Saetze Briefkopf, und
# gelesen hat man "Quarzfels Advokatur, Raemistrasse 14, 8001 Zuerich". Der
# Treffersatz fuehrt die Karte an, der Text danach gibt den Zusammenhang -- man
# liest vorwaerts, nicht rueckwaerts. Im Dialog steht der Vorlauf weiterhin.
CARD_LEADING_CONTEXT_CHARS = 0


def _trim_leading_context(before: str, max_chars: int = CARD_LEADING_CONTEXT_CHARS) -> str:
    """Der Vorlauf, auf sein Ende gekuerzt -- der Teil direkt vor dem Treffer."""
    if max_chars <= 0:
        return ""
    text = " ".join(str(before or "").split())
    if len(text) <= max_chars:
        return text
    tail = text[-max_chars:]
    space = tail.find(" ")
    if space >= 0:
        tail = tail[space + 1:]
    return f"… {tail}"



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

# Wie viele Fundstellen je Seite. Knovas gibt die bestbewerteten Chunks zurueck,
# und auf einer Vertragsseite liegen die oft dicht beieinander -- acht Eintraege,
# die alle auf denselben Absatz zeigen, beantworten die Frage "wo steht das"
# nicht besser als zwei, sie machen sie nur unuebersichtlich.
MAX_MATCH_LOCATIONS_PER_PAGE = 2

# Ab wie vielen Woertern ein Satz als Fundstelle taugt. Darunter sind es
# Briefkopfzeilen, Ueberschriften und Aktenzeichen: "Raemistrasse 14, 8001
# Zuerich", "MANDATSVEREINBARUNG", "Aktenzeichen: 2019-021". Die Vektorsuche
# bewertet solche Zeilen mit, weil sie im Dokument stehen; als Antwort auf eine
# Frage taugen sie nicht.
MIN_LOCATION_CONTENT_WORDS = 4

# Ein Buchstabenwort. Ziffern und Unterstriche zaehlen nicht mit, damit
# "2019-021" oder "+41 44 123 45 67" keine Woerter ergeben.
_CONTENT_WORD = _re_module.compile(r"[^\W\d_]{2,}", _re_module.UNICODE)

# Eine Anschrift: Strasse mit Hausnummer und irgendwo dahinter Postleitzahl und
# Ort. Beide Haelften zusammen, sonst faellt "Der Restwerklohn betraegt 4000
# Franken" darunter -- und die Strassennennung allein steht auch in Saetzen, um
# die es wirklich geht ("Neubau Schaffhauserstrasse 41").
_ADDRESS_LINE = _re_module.compile(
    r"(?:stra(?:ss|\u00df)e|str\.|gasse|weg|platz|allee)\s*\d+.*?\b\d{4,5}\s+[^\W\d_]{3,}",
    _re_module.IGNORECASE | _re_module.DOTALL,
)

# Telefon-, Fax- und Zahlungszeilen eines Briefkopfs.
_CONTACT_LINE = _re_module.compile(
    r"\b(?:tel|telefon|fax|iban|mwst|uid)\b\.?\s*:?\s*\+?\d",
    _re_module.IGNORECASE,
)


# Nur intern: die Zahl der gesuchten Woerter in einem Trefferatz. Wird vor der
# Rueckgabe wieder entfernt, die Oberflaeche sieht nur ``literal``.
_COVERAGE_KEY = "_covered_terms"

# Wie viele Saetze die Vorschau hoechstens selbst beisteuert, wenn Knovas den
# besten Satz nicht gemeldet hat.
MAX_LOCAL_LOCATIONS = 2

# Wie weit dafuer in ein Dokument hineingelesen wird. Der Sidecar darf 50'000
# Saetze halten, und diese Suche laeuft je Treffer einmal -- bei zwanzig
# Treffern waere das Dekorieren des Ergebnisses teurer als die Suche selbst.
# Was in den ersten Saetzen nicht steht, findet die Liste ueber die von Knovas
# gemeldeten Orte.
LOCAL_SCAN_MAX_SENTENCES = 4000


def _term_coverage(text: str, terms: Sequence[str]) -> int:
    """Wie viele verschiedene gesuchte Woerter in diesem Satz stehen."""
    low = str(text or "").lower()
    return sum(1 for term in terms if term and term in low)


def _with_uncovered_sentences(
    candidates: List[Dict[str, Any]],
    sentences: List[Dict[str, Any]],
    terms: Sequence[str],
    *,
    radius: int,
) -> List[Dict[str, Any]]:
    """Ergaenzt den Satz, der die Frage beantwortet, wenn Knovas ihn nicht meldet.

    Knovas meldet die bestbewerteten Chunks, und ein Chunk deckt mehrere Saetze
    ab -- welcher Satz darin die gesuchten Woerter traegt, steht nicht in der
    Antwort. Bei "Womit hat die Alpenblick Gastro beauftragt?" standen so eine
    Kopfzeile und ein Nebensatz in der Liste, waehrend "Die Mandantin Alpenblick
    Gastro GmbH beauftragt die Kanzlei" -- der Satz mit der Antwort -- gar nicht
    vorkam, obwohl er im Dokument steht und seine Woerter markiert waren.

    Der Sidecar haelt jeden Satz. Ist im Dokument einer besser gedeckt als alles
    Gemeldete, kommt er dazu. Ausgewaehlt wird er danach wie jeder andere, mit
    Seitenobergrenze und Dopplungspruefung.
    """
    best_reported = max(
        (int(c.get(_COVERAGE_KEY) or 0) for c in candidates), default=0
    )
    known = {int(c["sentence_number"]) for c in candidates}
    full = len(terms)
    extra: List[Tuple[int, Dict[str, Any]]] = []
    for idx, sent in enumerate(sentences[:LOCAL_SCAN_MAX_SENTENCES]):
        if len(extra) >= MAX_LOCAL_LOCATIONS and extra[0][0] >= full:
            # Besser als "alle gesuchten Woerter" wird es nicht mehr.
            break
        text = _sentence_text(sent)
        if not text:
            continue
        covered = _term_coverage(text, terms)
        if covered <= best_reported:
            continue
        number = sent.get("i") if isinstance(sent, dict) else None
        try:
            number = int(number)
        except (TypeError, ValueError):
            number = idx + 1
        if number in known:
            continue
        window = context_window(sentences, number, radius=radius)
        if not window:
            continue
        entry: Dict[str, Any] = {
            "sentence_number": number,
            "before": window.get("before", ""),
            "match": window.get("match", ""),
            "after": window.get("after", ""),
            "literal": True,
            _COVERAGE_KEY: covered,
        }
        page = sent.get("p") if isinstance(sent, dict) else None
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = None
        if page is not None and page >= 1:
            entry["page"] = page
        extra.append((covered, entry))
        extra.sort(key=lambda pair: -pair[0])
        del extra[MAX_LOCAL_LOCATIONS:]
    if not extra:
        return candidates
    return [entry for _, entry in extra] + candidates


# Wie viele Saetze eines gemeldeten Chunks hoechstens durchgesehen werden. Ein
# Chunk ist serverseitig auf ~1800 Zeichen begrenzt; die Grenze faengt nur einen
# kaputten Bereich ab.
CHUNK_SCAN_MAX_SENTENCES = 60

# Obergrenze fuer den Chunktext, den eine Fundstelle mitschickt. Er geht nur an
# Fundstellen ohne gesuchte Woerter -- bei den anderen zeigt der markierte Satz
# schon, worum es geht, und der Bereich drumherum waere nur Flaeche.
MAX_CHUNK_TEXT_CHARS = 1200


def _chunk_range(chunk: Dict[str, Any], start: int) -> Tuple[int, int]:
    """Von wo bis wo der bewertete Chunk reicht, als Satznummern."""
    raw_end = chunk.get("sentence_number_end")
    try:
        end = int(raw_end)
    except (TypeError, ValueError):
        return start, start
    if end < start:
        return start, start
    return start, min(end, start + CHUNK_SCAN_MAX_SENTENCES)


def _anchor_in_chunk(
    numbers_to_text: Dict[int, str],
    start: int,
    end: int,
    terms: Sequence[str],
) -> int:
    """Der Satz des Chunks, der die gesuchten Woerter traegt.

    Bewertet hat die Suche den ganzen Chunk, gemeldet wird seine erste
    Satznummer. Welcher Satz darin die Frage beantwortet, steht nicht in der
    Antwort -- und der erste ist es oft nicht: bei "Womit hat die Alpenblick
    Gastro beauftragt?" war der erste Satz des Chunks die Kopfzeile und der
    Satz mit "beauftragt" stand drei Saetze weiter.

    Ohne gesuchte Woerter, oder wenn keines vorkommt, bleibt es beim ersten --
    dann gibt es nichts, was einen anderen Satz besser machen wuerde.
    """
    if not terms or end <= start:
        return start
    best_number, best_covered = start, _term_coverage(numbers_to_text.get(start, ""), terms)
    for number in range(start + 1, end + 1):
        covered = _term_coverage(numbers_to_text.get(number, ""), terms)
        if covered > best_covered:
            best_number, best_covered = number, covered
    return best_number


def _is_thin_location(text: str) -> bool:
    """Ob dieser Satz zu wenig Inhalt hat, um als Fundstelle zu zaehlen."""
    if _ADDRESS_LINE.search(text) or _CONTACT_LINE.search(text):
        return True
    return len(_CONTENT_WORD.findall(text)) < MIN_LOCATION_CONTENT_WORDS


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
    "wenn", "weil", "dies", "diese", "dieser", "dieses",
    # Fragewörter: "Wie wird die Alpenblick Mandantin abgerechnet?" fragt nach
    # Alpenblick und abgerechnet, nicht nach "wie".
    "wie", "was", "wer", "wann", "wo", "warum", "wieso", "weshalb",
    "welche", "welcher", "welches", "welchen",
    "womit", "wodurch", "wofür", "wofuer", "woran", "worin", "worum", "wozu",
    "wohin", "woher", "weswegen", "the", "and", "for",
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
    per_page: int = MAX_MATCH_LOCATIONS_PER_PAGE,
    terms: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """Every place in this document the query matched, with its page and text.

    Knovas reports the locations (``top_chunks`` carries page and sentence
    numbers); the sidecar holds the sentences. Neither alone can answer "where
    in this document, and what does it say there" -- which is the question a
    lawyer opens a 60-page contract with. Until now the two were combined for
    the first hit only, to make one snippet on the card, and the rest were
    dropped on the floor.

    Nicht jede gemeldete Stelle wird eine Fundstelle. Welche wegfallen und
    warum, steht bei ``_select_locations``; kurz: was die Leserin zweimal an
    dieselbe Stelle schickt, und Briefkopfzeilen, die die Vektorsuche
    mitbewertet hat, weil sie nun einmal im Dokument stehen.

    ``terms`` are the words of the query. A location carries ``literal: True``
    when one of them actually appears in it, and those sort first. Without this
    the list is simply the highest-scoring passages of a document the vector
    search liked -- which, when someone searched for a person's name that occurs
    nowhere, is eight arbitrary sentences presented as "Fundstellen". The panel
    has to be able to tell the caller which it is holding.
    """
    if not sentences or not isinstance(top_chunks, list):
        return []
    candidates: List[Dict[str, Any]] = []
    seen: set = set()
    numbers_to_text = _sentences_by_number(sentences)
    for chunk in top_chunks:
        if not isinstance(chunk, dict):
            continue
        raw = chunk.get("sentence_number")
        try:
            chunk_start = int(raw)
        except (TypeError, ValueError):
            continue
        chunk_start, chunk_end = _chunk_range(chunk, chunk_start)
        sentence_number = _anchor_in_chunk(
            numbers_to_text, chunk_start, chunk_end, terms
        )
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
        if chunk_end > chunk_start:
            # Der bewertete Bereich, nicht nur der Satz daraus. Die Oberflaeche
            # zeigt ihn bei einer Fundstelle ohne gesuchte Woerter an: dort ist
            # der einzelne Satz eine Auswahl, die niemand getroffen hat.
            entry["chunk_from"] = chunk_start
            entry["chunk_to"] = chunk_end
        if terms:
            # Nur der Trefferatz selbst entscheidet, nicht sein Umfeld: mit
            # Radius 1 steht der Nachbarsatz mit im Fenster, und dann waere
            # jeder Satz neben einem Treffer selbst einer.
            covered = _term_coverage(entry["match"], terms)
            entry["literal"] = covered > 0
            entry[_COVERAGE_KEY] = covered
        if not entry.get("literal") and chunk_end > chunk_start:
            # Steht kein gesuchtes Wort darin, ist der einzelne Satz eine
            # Auswahl, die niemand getroffen hat -- bewertet hat die Suche den
            # ganzen Bereich. Dann faerbt die Vorschau ihn mit ein.
            chunk_text = " ".join(
                text for text in (
                    numbers_to_text.get(n, "") for n in range(chunk_start, chunk_end + 1)
                ) if text
            ).strip()
            if chunk_text:
                entry["chunk_text"] = chunk_text[:MAX_CHUNK_TEXT_CHARS]
        candidates.append(entry)

    if terms:
        # Der Satz, in dem die meisten gesuchten Woerter stehen, zuerst. Ein
        # blosses literal/nicht-literal reicht nicht: "In Sachen: Alpenblick
        # Gastro GmbH" (zwei Woerter, eine Kopfzeile) stand damit gleichauf mit
        # "Die Mandantin Alpenblick Gastro GmbH beauftragt die Kanzlei" (drei) --
        # und bei zwei Plaetzen je Seite gewann die Kopfzeile, weil Knovas sie
        # zuerst meldete. Sortiert wird vor der Auswahl, sonst entscheidet die
        # Meldereihenfolge, wer die Plaetze bekommt.
        candidates = _with_uncovered_sentences(
            candidates, sentences, terms, radius=radius
        )
        candidates.sort(key=lambda e: -int(e.get(_COVERAGE_KEY) or 0))

    chosen = _select_locations(
        candidates, radius=radius, limit=limit, per_page=per_page, drop_thin=True
    )
    if not chosen:
        # Ein Dokument, das nur aus Kurzzeilen besteht -- eine Tabelle, eine
        # Adressliste -- haette sonst gar keine Fundstellen. Dann lieber die
        # duennen zeigen als eine leere Liste unter einem Treffer.
        chosen = _select_locations(
            candidates, radius=radius, limit=limit, per_page=per_page, drop_thin=False
        )
    for entry in chosen:
        entry.pop(_COVERAGE_KEY, None)
    return chosen


def _select_locations(
    candidates: List[Dict[str, Any]],
    *,
    radius: int,
    limit: int,
    per_page: int,
    drop_thin: bool,
) -> List[Dict[str, Any]]:
    """Aus den Kandidaten die Stellen, die einander nicht doppeln.

    Drei Gruende, eine Fundstelle wegzulassen, und alle drei sind dasselbe aus
    Sicht der Leserin: sie schickt sie dorthin, wo sie schon war.

    * Nachbarsaetze. Zwei Anker, die hoechstens ``radius`` auseinanderliegen,
      teilen sich ihr Fenster -- zwei Eintraege, die fast denselben Text zeigen.
    * Gleicher Text. Kopf- und Fusszeilen stehen auf jeder Seite, mit
      verschiedener Satznummer und identischem Wortlaut.
    * Mehr als ``per_page`` auf einer Seite.
    """
    chosen: List[Dict[str, Any]] = []
    anchors: List[int] = []
    texts: set = set()
    per_page_used: Dict[Optional[int], int] = {}
    for entry in candidates:
        if len(chosen) >= limit:
            break
        number = entry["sentence_number"]
        if any(abs(number - taken) <= radius for taken in anchors):
            continue
        key = " ".join(str(entry.get("match") or "").split()).lower()
        if key in texts:
            continue
        # Ein Treffer, in dem ein gesuchtes Wort steht, bleibt immer. Wer nach
        # "Raemistrasse" sucht, meint die Adresszeile.
        if drop_thin and not entry.get("literal") and _is_thin_location(entry["match"]):
            continue
        page = entry.get("page")
        if per_page_used.get(page, 0) >= per_page:
            continue
        chosen.append(entry)
        anchors.append(number)
        texts.add(key)
        per_page_used[page] = per_page_used.get(page, 0) + 1
    return chosen


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


def _sentences_by_number(sentences: Any) -> Dict[int, str]:
    """Satznummer -> Text. Fehlt die Nummer, zaehlt die Position."""
    by_number: Dict[int, str] = {}
    if not isinstance(sentences, list):
        return by_number
    for index, sent in enumerate(sentences):
        raw = sent.get("i") if isinstance(sent, dict) else index + 1
        if raw is None:
            raw = index + 1
        try:
            by_number[int(raw)] = _sentence_text(sent)
        except (TypeError, ValueError):
            continue
    return by_number


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
    by_number = _sentences_by_number(sentences)
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
            locations = build_match_locations(
                sentences, result.get("top_chunks"), terms=terms
            )
            # Der Ausschnitt auf der Trefferkarte ist die beste Fundstelle --
            # dieselbe, die die Vorschau oben anzeigt. Vorher war es das Fenster
            # um die gemeldete Satznummer, und das ist der ANFANG des bewerteten
            # Chunks: bei einer Mandatsvereinbarung der Briefkopf. Auf der Karte
            # stand dann "Quarzfels Advokatur, Raemistrasse 14, 8001 Zuerich",
            # waehrend der Satz mit der Antwort zwei Zeilen weiter unten im
            # abgeschnittenen Text verschwand.
            anchor = (
                locations[0]["sentence_number"] if locations
                else resolve_sentence_number(result)
            )
            snippet = context_window(sentences, anchor, radius=context_radius)
            if snippet:
                snippet["before"] = _trim_leading_context(snippet.get("before", ""))
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
