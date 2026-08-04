"""Wissensnetz: Fixture-JSON laden und validieren.

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
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ENV_FIXTURE_PATH = "ONTOLOGY_FIXTURE_PATH"

_EMPTY: Dict[str, Any] = {
    "types": [], "relations": [], "entities": [],
    "entity_relations": [], "evidence": [],
}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class OntologyStore:
    def __init__(self, data: Dict[str, Any], warnings: List[str]):
        self._types: List[Dict[str, Any]] = data["types"]
        self._relations: List[Dict[str, Any]] = data["relations"]
        self._entities: List[Dict[str, Any]] = data["entities"]
        self._entity_relations: List[Dict[str, Any]] = data["entity_relations"]
        self._evidence: List[Dict[str, Any]] = data["evidence"]
        self._entity_by_id = {e["id"]: e for e in self._entities}
        self.warnings = warnings

    def summary(self) -> Dict[str, Any]:
        return {
            "types": [dict(t) for t in self._types],
            "relations": [dict(r) for r in self._relations],
        }

    def entities_for_type(self, type_id: str) -> Dict[str, Any]:
        return {"entities": [dict(e) for e in self._entities if e["type"] == type_id]}

    def entity_detail(self, entity_id: str) -> Optional[Dict[str, Any]]:
        entity = self._entity_by_id.get(entity_id)
        if entity is None:
            return None
        relations = [
            {"predicate": r["predicate"], "target": dict(self._entity_by_id[r["dst"]])}
            for r in self._entity_relations
            if r["src"] == entity_id
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
        types.append({"id": tid, "label": label, "count": _as_int(t.get("count"))})

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

    return (
        {"types": types, "relations": relations, "entities": entities,
         "entity_relations": entity_relations, "evidence": evidence},
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
    return OntologyStore(data, warnings)


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
