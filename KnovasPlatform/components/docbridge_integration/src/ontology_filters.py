"""Cortex-Filter: echtes Passagen-Matching mit permanenter Rejection-Memory.

Req 2.2 ("junior associate that never sleeps"): Der Kunde beschreibt in
Alltagssprache einen Filter auf einer Entität; passende Passagen aus deren
Dokumenten werden als prüfbare Vorschläge geroutet. Eine Ablehnung ist
endgültig — sie überlebt Neustarts und erneute Auswertungen.

Flask-frei (Muster: ontology_store.py). Das Matching ist bewusst
lexikalisch (Token-Folding, Präfix-Matching für Komposita); der spätere
echte Knovas-Endpunkt ersetzt nur diese Funktion, der Vertrag bleibt.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ENV_STATE_PATH = "ONTOLOGY_FILTER_STATE_PATH"

MAX_PROPOSALS = 12
SCORE_THRESHOLD = 0.3
MAX_FILTER_LABEL = 120

_STOPWORDS = {
    "und", "oder", "der", "die", "das", "den", "dem", "des", "ein", "eine",
    "einer", "eines", "einem", "einen", "zur", "zum", "im", "in", "am", "an",
    "auf", "aus", "bei", "mit", "von", "vom", "fur", "fuer", "ist", "sind",
    "wird", "werden", "per", "als", "zu", "nach", "uber", "alle", "aller",
}


def _fold(word: str) -> str:
    w = (word.lower()
         .replace("ä", "a").replace("ö", "o").replace("ü", "u")
         .replace("ß", "ss"))
    return re.sub(r"[^a-z0-9]", "", w)


def _stem(w: str) -> str:
    """Sehr grobes deutsches Stemming — reicht für Plural/Flexion."""
    for suffix in ("ungen", "en", "ern", "er", "es", "e", "n", "s"):
        if len(w) - len(suffix) >= 4 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def _tokens(text: str) -> List[str]:
    out = []
    for raw in re.findall(r"[A-Za-zÄÖÜäöüß0-9]+", text):
        t = _stem(_fold(raw))
        if len(t) >= 3 and t not in _STOPWORDS:
            out.append(t)
    return out


def _token_match(qt: str, st: str) -> float:
    """Wie gut trifft ein Filter-Token ein Segment-Token?"""
    if qt == st:
        return 1.0
    if len(qt) >= 5 and len(st) >= 5 and (qt in st or st in qt):
        return 0.8          # Kompositum-Bestandteil: "frist" in "kundigungsfrist"
    common = 0
    for a, b in zip(qt, st):
        if a != b:
            break
        common += 1
    if common >= 6:
        return 0.7          # gemeinsamer Stamm: "kundigungsklausel"/"kundigungsfrist"
    return 0.0


def _score_segment(query_tokens: List[str], seg_tokens: List[str],
                   heading_tokens: List[str]) -> float:
    """Anteil getroffener Filter-Tokens; Treffer nur im Seitenkontext
    (Überschrift) zählen abgeschwächt."""
    if not query_tokens:
        return 0.0
    total = 0.0
    for qt in query_tokens:
        direct = max((_token_match(qt, st) for st in seg_tokens), default=0.0)
        via_heading = 0.6 * max((_token_match(qt, ht) for ht in heading_tokens),
                                default=0.0)
        total += max(direct, via_heading)
    return total / len(query_tokens)


def _segment_pages(abs_path: str) -> List[Tuple[int, str, List[str]]]:
    """[(seite, satz, überschrift-tokens)] aus einem PDF."""
    import fitz  # pymupdf; bewusst lazy — Tests ohne PDF brauchen es nicht

    segments: List[Tuple[int, str, List[str]]] = []
    with fitz.open(abs_path) as doc:
        for page_no, page in enumerate(doc, start=1):
            text = page.get_text().strip()
            if not text:
                continue
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if not lines:
                continue
            # Erste Zeile ist die Überschrift: zählt als Kontext fürs
            # Scoring, gehört aber nicht ins Zitat.
            heading_tokens = _tokens(lines[0])
            flat = " ".join(lines[1:]) if len(lines) > 1 else lines[0]
            for sentence in _split_sentences(flat):
                if len(sentence) >= 20:
                    segments.append((page_no, sentence, heading_tokens))
    return segments


# Ordinalzahlen sind 1-2-stellig ("1. Mai", "30. September"); 4-stellige
# Jahreszahlen ("... 2024.") sind echte Satzenden und bleiben getrennt.
_ABBREV_END = re.compile(
    r"(?:\b(?:Nr|Art|Abs|Ziff|Dr|bzw|ca|inkl|vgl)|(?<![\d])\b\d{1,2})\.$")


def _split_sentences(flat: str) -> List[str]:
    """Satz-Split, der Ordinalzahlen ("1. Mai") und juristische
    Abkürzungen ("Art.", "Ziff.") nicht als Satzende missversteht."""
    pieces = re.split(r"(?<=[.!?])\s+", flat)
    merged: List[str] = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if merged and _ABBREV_END.search(merged[-1]):
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    return merged


def _fingerprint(doc_path: str, page: int, quote: str) -> str:
    basis = f"{doc_path}|{page}|{_fold(quote)}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


class FilterEngine:
    """Filter + Entscheidungen, persistiert als JSON (atomarer Write)."""

    def __init__(self, state_path: Optional[str],
                 resolve_path: Optional[Callable[[str], Optional[str]]] = None):
        self._state_path = state_path
        self._resolve_path = resolve_path
        self._lock = threading.Lock()
        self._segments_cache: Dict[Tuple[str, float], List[Tuple[int, str, List[str]]]] = {}
        self._state = self._load_state()

    # -- Persistenz ---------------------------------------------------------

    def _load_state(self) -> Dict[str, Any]:
        empty = {"filters": [], "decisions": {}}
        if not self._state_path:
            logger.warning("%s nicht gesetzt — Filter nur im Speicher, "
                           "Rejection-Memory überlebt keinen Neustart", ENV_STATE_PATH)
            return empty
        try:
            with open(self._state_path, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return empty
        except (OSError, ValueError) as exc:
            logger.warning("Filter-State nicht ladbar (%s): %s", self._state_path, exc)
            return empty
        if not isinstance(raw, dict):
            return empty
        return {"filters": list(raw.get("filters") or []),
                "decisions": dict(raw.get("decisions") or {})}

    def _save_state(self) -> None:
        if not self._state_path:
            return
        tmp = f"{self._state_path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._state_path)
        except OSError as exc:
            logger.error("Filter-State nicht speicherbar (%s): %s",
                         self._state_path, exc)

    # -- Filter-Verwaltung --------------------------------------------------

    def create_filter(self, entity_id: str, label: str) -> Optional[Dict[str, Any]]:
        label = " ".join(str(label or "").split())[:MAX_FILTER_LABEL]
        entity_id = str(entity_id or "").strip()
        if not label or not entity_id:
            return None
        with self._lock:
            for flt in self._state["filters"]:
                if (flt["entity_id"] == entity_id
                        and _fold(flt["label"]) == _fold(label)):
                    return dict(flt)
            fid = "f-" + hashlib.sha1(
                f"{entity_id}|{_fold(label)}".encode("utf-8")).hexdigest()[:10]
            flt = {"id": fid, "entity_id": entity_id, "label": label,
                   "created": time.time()}
            self._state["filters"].append(flt)
            self._save_state()
            return dict(flt)

    def get_filter(self, filter_id: str) -> Optional[Dict[str, Any]]:
        for flt in self._state["filters"]:
            if flt["id"] == filter_id:
                return dict(flt)
        return None

    def filters_for_entity(self, store: Any, entity_id: str) -> List[Dict[str, Any]]:
        out = []
        for flt in self._state["filters"]:
            if flt["entity_id"] != entity_id:
                continue
            proposals = self._proposals(store, flt)
            out.append(self._filter_summary(flt, proposals))
        return out

    # -- Matching -----------------------------------------------------------

    def _documents_for_entity(self, store: Any, entity_id: str) -> List[Dict[str, str]]:
        """Dokumente der Entität und ihrer direkten Nachbarn (1 Hop) —
        das ist die ehrliche Übersetzung von "the matter's documents"."""
        detail = store.entity_detail(entity_id)
        if detail is None:
            return []
        seen: Dict[str, Dict[str, str]] = {}
        for ev in detail["evidence"]:
            seen[ev["document"]["path"]] = dict(ev["document"])
        for rel in detail["relations"]:
            neighbor = store.entity_detail(rel["target"]["id"])
            if neighbor is None:
                continue
            for ev in neighbor["evidence"]:
                seen.setdefault(ev["document"]["path"], dict(ev["document"]))
        return list(seen.values())

    def _segments_for(self, abs_path: str) -> List[Tuple[int, str, List[str]]]:
        try:
            mtime = os.stat(abs_path).st_mtime
        except OSError:
            return []
        key = (abs_path, mtime)
        if key not in self._segments_cache:
            try:
                self._segments_cache[key] = _segment_pages(abs_path)
            except Exception as exc:  # kaputtes PDF darf nie eskalieren
                logger.warning("Filter: PDF nicht lesbar (%s): %s", abs_path, exc)
                self._segments_cache[key] = []
        return self._segments_cache[key]

    def _proposals(self, store: Any, flt: Dict[str, Any]) -> List[Dict[str, Any]]:
        query_tokens = _tokens(flt["label"])
        matches: List[Dict[str, Any]] = []
        for doc in self._documents_for_entity(store, flt["entity_id"]):
            abs_path = self._resolve_path(doc["path"]) if self._resolve_path else None
            if not abs_path:
                continue
            for page, sentence, heading_tokens in self._segments_for(abs_path):
                score = _score_segment(query_tokens, _tokens(sentence), heading_tokens)
                if score < SCORE_THRESHOLD:
                    continue
                fp = _fingerprint(doc["path"], page, sentence)
                matches.append({
                    "id": fp,
                    "quote": sentence,
                    "page": page,
                    "score": round(score, 2),
                    "document": {"path": doc["path"], "title": doc["title"]},
                })
        matches.sort(key=lambda m: (-m["score"], m["document"]["path"], m["page"]))
        matches = matches[:MAX_PROPOSALS]
        decisions = self._state["decisions"].get(flt["id"], {})
        for m in matches:
            decided = decisions.get(m["id"])
            m["state"] = decided["state"] if decided else "pending"
        return matches

    @staticmethod
    def _filter_summary(flt: Dict[str, Any],
                        proposals: List[Dict[str, Any]]) -> Dict[str, Any]:
        counts = {"pending": 0, "accepted": 0, "rejected": 0}
        for p in proposals:
            counts[p["state"]] += 1
        return {"id": flt["id"], "label": flt["label"],
                "entity_id": flt["entity_id"],
                "status": "active" if proposals else "collecting",
                "counts": counts}

    def filter_detail(self, store: Any, filter_id: str) -> Optional[Dict[str, Any]]:
        flt = self.get_filter(filter_id)
        if flt is None:
            return None
        proposals = self._proposals(store, flt)
        return {"filter": self._filter_summary(flt, proposals),
                "proposals": proposals}

    # -- Entscheidungen -----------------------------------------------------

    def decide(self, store: Any, filter_id: str, proposal_id: str,
               action: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """(aktualisierter Vorschlag, '') oder (None, fehlercode).

        Abgelehnt ist endgültig: eine bestehende Ablehnung kann nicht
        zurückgenommen werden — das ist das Produktversprechen.
        """
        if action not in ("accept", "reject"):
            return None, "bad_action"
        flt = self.get_filter(filter_id)
        if flt is None:
            return None, "not_found"
        proposals = {p["id"]: p for p in self._proposals(store, flt)}
        proposal = proposals.get(str(proposal_id))
        if proposal is None:
            return None, "not_found"
        with self._lock:
            decisions = self._state["decisions"].setdefault(filter_id, {})
            existing = decisions.get(proposal["id"])
            if existing and existing["state"] == "rejected":
                proposal["state"] = "rejected"
                return proposal, ""      # endgültig — idempotent
            decisions[proposal["id"]] = {
                "state": "accepted" if action == "accept" else "rejected",
                "decided": time.time(),
            }
            self._save_state()
        proposal["state"] = decisions[proposal["id"]]["state"]
        return proposal, ""


_engine_cache: Optional[Tuple[Optional[str], FilterEngine]] = None


def get_filter_engine(resolve_path: Optional[Callable[[str], Optional[str]]] = None
                      ) -> FilterEngine:
    """Env-konfigurierte Engine, gecacht per State-Pfad."""
    global _engine_cache
    state_path = (os.environ.get(ENV_STATE_PATH) or "").strip() or None
    if _engine_cache is not None and _engine_cache[0] == state_path:
        return _engine_cache[1]
    engine = FilterEngine(state_path, resolve_path)
    _engine_cache = (state_path, engine)
    return engine
