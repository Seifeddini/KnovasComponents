"""Cortex: Fixture-JSON laden und validieren.

Traegt bewusst kein Flask-Wissen (Muster: web_interface/preview.py).
Der Store ist der spaetere Andockpunkt fuer den echten Knovas-Endpunkt:
Vertrag bleibt, nur die Datenquelle wird getauscht.

Validierungsposition: kaputte Referenzen werden gefiltert und geloggt,
niemals eskaliert -- die Seite zeigt dann weniger, aber nie einen 500.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ENV_FIXTURE_PATH = "ONTOLOGY_FIXTURE_PATH"

# Erlaubter Zeichensatz fuer optionale Symbolnamen (types[].icon).
_ICON_NAME_RE = re.compile(r"[a-z0-9_-]{1,32}")

_EMPTY: Dict[str, Any] = {
    "types": [], "relations": [], "entities": [],
    "entity_relations": [], "evidence": [], "corpus": {},
}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class OntologyStore:
    def __init__(self, data: Dict[str, Any], warnings: List[str],
                 path: Optional[str] = None):
        self._path = path
        self._types: List[Dict[str, Any]] = data["types"]
        self._relations: List[Dict[str, Any]] = data["relations"]
        self._entities: List[Dict[str, Any]] = data["entities"]
        self._entity_relations: List[Dict[str, Any]] = data["entity_relations"]
        self._evidence: List[Dict[str, Any]] = data["evidence"]
        self._corpus: Dict[str, Any] = data.get("corpus") or {}
        self._entity_by_id = {e["id"]: e for e in self._entities}
        self.warnings = warnings

    def corpus(self) -> Dict[str, Any]:
        """Korpus-Kennzahlen fuer die Plattform-Leiste (optional in der Fixture)."""
        return dict(self._corpus)

    # -- Kuratieren ------------------------------------------------------
    # Der Wissensgraph wird vom Anwender gepflegt, nicht abgeleitet. Damit
    # derselbe Ablauf lokal und gegen die API funktioniert, schreibt die
    # Fixture-Quelle in ihre JSON-Datei zurueck.

    def create_entity(self, label: str, type_id: str) -> Optional[Dict[str, Any]]:
        label = " ".join(str(label or "").split())
        type_id = str(type_id or "").strip()
        if not label or not any(t["id"] == type_id for t in self._types):
            return None
        for existing in self._entities:
            if existing["type"] == type_id and existing["label"].lower() == label.lower():
                return dict(existing)
        entity = {"id": self._next_entity_id(), "label": label,
                  "type": type_id, "doc_count": 0}
        self._entities.append(entity)
        self._entity_by_id[entity["id"]] = entity
        self._persist(lambda raw: raw.setdefault("entities", []).append(dict(entity)))
        return dict(entity)

    def create_relation(self, src: str, predicate: str,
                        dst: str) -> Optional[Dict[str, Any]]:
        predicate = " ".join(str(predicate or "").split())
        src, dst = str(src or "").strip(), str(dst or "").strip()
        if not predicate or src == dst:
            return None
        if src not in self._entity_by_id or dst not in self._entity_by_id:
            return None
        relation = {"src": src, "predicate": predicate, "dst": dst}
        if relation in self._entity_relations:
            return dict(relation)
        self._entity_relations.append(relation)
        self._persist(lambda raw: raw.setdefault("entity_relations", []).append(dict(relation)))
        return dict(relation)

    def _next_entity_id(self) -> str:
        used = {e["id"] for e in self._entities}
        index = len(used) + 1
        while f"e-{index:03d}" in used:
            index += 1
        return f"e-{index:03d}"

    def _persist(self, mutate: Callable[[Dict[str, Any]], None]) -> None:
        """Aenderung in die Fixture schreiben (atomar). Ohne Pfad bleibt sie
        im Speicher - dann ist der Store aus einem Test heraus gebaut."""
        if not self._path:
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("Fixture ist kein JSON-Objekt")
            mutate(raw)
            tmp = f"{self._path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path)
        except (OSError, ValueError) as exc:
            logger.error("Fixture nicht schreibbar (%s): %s", self._path, exc)
            return
        global _cache
        _cache = None            # naechster Zugriff liest neu ein

    def summary(self) -> Dict[str, Any]:
        return {
            "types": [dict(t) for t in self._types],
            "relations": [dict(r) for r in self._relations],
        }

    def entities_for_type(self, type_id: str) -> Dict[str, Any]:
        """Ohne Typ alle Entitaeten - das braucht die Auswahl beim Verbinden."""
        if not str(type_id or "").strip():
            return {"entities": [dict(e) for e in self._entities]}
        return {"entities": [dict(e) for e in self._entities if e["type"] == type_id]}

    def entity_detail(self, entity_id: str) -> Optional[Dict[str, Any]]:
        entity = self._entity_by_id.get(entity_id)
        if entity is None:
            return None
        relations = [
            {"predicate": r["predicate"], "direction": "out",
             "target": dict(self._entity_by_id[r["dst"]])}
            for r in self._entity_relations
            if r["src"] == entity_id
        ] + [
            {"predicate": r["predicate"], "direction": "in",
             "target": dict(self._entity_by_id[r["src"]])}
            for r in self._entity_relations
            if r["dst"] == entity_id
        ]
        evidence = [
            {"document": dict(ev["document"]), "page": ev["page"], "quote": ev["quote"]}
            for ev in self._evidence
            if ev["entity_id"] == entity_id
        ]
        return {"entity": dict(entity), "relations": relations, "evidence": evidence}


def _validate(raw: Any, path_exists: Optional[Callable[[str], bool]]) -> Tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []
    if not isinstance(raw, dict):
        return dict(_EMPTY), ["Fixture ist kein JSON-Objekt"]

    types = []
    seen_type_ids = set()
    for t in raw.get("types") or []:
        tid = str(t.get("id") or "").strip()
        label = str(t.get("label") or "").strip()
        if not tid or not label or tid in seen_type_ids:
            warnings.append(f"Typ verworfen: {t!r}")
            continue
        seen_type_ids.add(tid)
        entry = {"id": tid, "label": label, "count": _as_int(t.get("count"))}
        # Optionaler Symbolname aus dem Vertrag. Zeichensatz eng gefasst; das
        # Frontend schlaegt ohnehin nur bekannte Namen nach und faellt sonst
        # auf ein Monogramm zurueck.
        icon = str(t.get("icon") or "").strip().lower()
        if icon and _ICON_NAME_RE.fullmatch(icon):
            entry["icon"] = icon
        types.append(entry)

    relations = []
    for r in raw.get("relations") or []:
        src, dst = str(r.get("src") or ""), str(r.get("dst") or "")
        pred = str(r.get("predicate") or "").strip()
        if not pred or src not in seen_type_ids or dst not in seen_type_ids:
            warnings.append(f"Typ-Relation verworfen (unbekannter Typ): {r!r}")
            continue
        relations.append({"src": src, "predicate": pred, "dst": dst, "count": _as_int(r.get("count"))})

    entities = []
    seen_entity_ids = set()
    for e in raw.get("entities") or []:
        eid = str(e.get("id") or "").strip()
        label = str(e.get("label") or "").strip()
        etype = str(e.get("type") or "")
        if not eid or not label or eid in seen_entity_ids or etype not in seen_type_ids:
            warnings.append(f"Entität verworfen: {e!r}")
            continue
        seen_entity_ids.add(eid)
        entities.append({"id": eid, "label": label, "type": etype,
                         "doc_count": _as_int(e.get("doc_count"))})

    entity_relations = []
    for r in raw.get("entity_relations") or []:
        src, dst = str(r.get("src") or ""), str(r.get("dst") or "")
        pred = str(r.get("predicate") or "").strip()
        if not pred or src not in seen_entity_ids or dst not in seen_entity_ids:
            warnings.append(f"Entitäts-Relation verworfen: {r!r}")
            continue
        entity_relations.append({"src": src, "predicate": pred, "dst": dst})

    evidence = []
    for ev in raw.get("evidence") or []:
        doc = ev.get("document") or {}
        doc_path = str(doc.get("path") or "").strip()
        eid = str(ev.get("entity_id") or "")
        page = _as_int(ev.get("page"), default=0)
        if not doc_path or eid not in seen_entity_ids or page < 1:
            warnings.append(f"Beleg verworfen (Pflichtfeld fehlt): {ev!r}")
            continue
        if path_exists is not None and not path_exists(doc_path):
            warnings.append(f"Beleg verworfen (Datei nicht gefunden): {doc_path}")
            continue
        evidence.append({
            "entity_id": eid,
            "document": {"path": doc_path, "title": str(doc.get("title") or doc_path)},
            "page": page,
            "quote": str(ev.get("quote") or ""),
        })

    # Optionale Korpus-Kennzahlen; fehlend oder unbrauchbar -> leer, damit die
    # Oberflaeche den Block weglaesst statt eine Zahl zu erfinden.
    corpus: Dict[str, Any] = {}
    raw_corpus = raw.get("corpus")
    if isinstance(raw_corpus, dict):
        documents = _as_int(raw_corpus.get("documents"), default=0)
        if documents > 0:
            corpus["documents"] = documents

    return (
        {"types": types, "relations": relations, "entities": entities,
         "entity_relations": entity_relations, "evidence": evidence,
         "corpus": corpus},
        warnings,
    )


def load_ontology(path: str,
                  path_exists: Optional[Callable[[str], bool]] = None) -> OntologyStore:
    """Fixture laden; fehlende/kaputte Datei ⇒ leerer Store + Warnung, nie Exception."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning("Ontology-Fixture nicht ladbar (%s): %s", path, exc)
        return OntologyStore(dict(_EMPTY), [f"Fixture nicht ladbar: {exc}"])
    data, warnings = _validate(raw, path_exists)
    for w in warnings:
        logger.warning("Ontology-Fixture: %s", w)
    return OntologyStore(data, warnings, path=path)


_cache: Optional[Tuple[str, float, OntologyStore]] = None


def get_ontology(path_exists: Optional[Callable[[str], bool]] = None) -> OntologyStore:
    """Env-konfigurierter Store, gecacht per (Pfad, mtime)."""
    global _cache
    path = (os.environ.get(ENV_FIXTURE_PATH) or "").strip()
    if not path:
        return OntologyStore(dict(_EMPTY), [f"{ENV_FIXTURE_PATH} nicht gesetzt"])
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        mtime = -1.0
    if _cache is not None and _cache[0] == path and _cache[1] == mtime:
        return _cache[2]
    store = load_ontology(path, path_exists)
    _cache = (path, mtime, store)
    return store
