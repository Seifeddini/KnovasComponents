"""Cortex-Filter gegen die Knovas Knowledge Graph API.

Erfuellt denselben Vertrag wie ontology_filters.FilterEngine, verlagert die
Arbeit aber dorthin, wo sie hingehoert: Das Routing macht der Server
(POST /secured/graph/nodes/<id>/filters legt einen Kind-Knoten an, passende
Chunks erscheinen als Placements), und die Ablehnung ist dort dauerhaft
verankert (POST /secured/graph/placements/<pid>/reject).

Hybrid-Modell (Entscheid 2026-08-08): Die API kennt nur aktiv und
abgelehnt. "Uebernommen" ist eine reine Pruefmarkierung und bleibt lokal -
so sieht der Anwender weiter, was er bereits gesichtet hat, ohne dass wir
dem Backend einen Zustand andichten, den es nicht fuehrt.

Offen bis zum ersten Lauf gegen eine echte Instanz: Die Spezifikation zeigt
die Antwortform von /placements nicht. Alle Felder werden deshalb tolerant
gelesen; fehlt eine Angabe, faellt der Eintrag nicht aus, er bleibt nur
unvollstaendig.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ENV_STATE_PATH = "ONTOLOGY_FILTER_STATE_PATH"
MAX_FILTER_LABEL = 120
MAX_FILTERS_PER_NODE = 16          # Grenze der API


def _first(mapping: Dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if isinstance(mapping, dict) and mapping.get(key) not in (None, ""):
            return mapping[key]
    return default


def _child_node_id(filter_obj: Dict[str, Any]) -> str:
    value = _first(filter_obj, "child_node_id", "child_node", "child", "node_id")
    if isinstance(value, dict):
        return str(_first(value, "id", "node_id", "uuid"))
    return str(value)


def _filter_id(filter_obj: Dict[str, Any]) -> str:
    return str(_first(filter_obj, "id", "filter_id", "uuid"))


def _filter_label(filter_obj: Dict[str, Any]) -> str:
    return str(_first(filter_obj, "query_text", "query", "label", "name"))


class GraphFilterEngine:
    """Filter und Placements ueber die Graph-API, Pruefmarkierung lokal."""

    def __init__(self, client: Any, state_path: Optional[str] = None,
                 text_resolver: Any = None):
        self._client = client
        self._text = text_resolver
        self._state_path = state_path
        self._lock = threading.Lock()
        self._state = self._load_state()

    # -- lokale Pruefmarkierung ----------------------------------------

    def _load_state(self) -> Dict[str, Any]:
        empty: Dict[str, Any] = {"reviewed": {}, "parents": {}}
        if not self._state_path:
            return empty
        try:
            with open(self._state_path, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return empty
        except (OSError, ValueError) as exc:
            logger.warning("Pruefmarkierungen nicht ladbar (%s): %s",
                           self._state_path, exc)
            return empty
        if not isinstance(raw, dict):
            return empty
        return {"reviewed": dict(raw.get("reviewed") or {}),
                "parents": dict(raw.get("parents") or {})}

    def _save_state(self) -> None:
        if not self._state_path:
            return
        tmp = f"{self._state_path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._state_path)
        except OSError as exc:
            logger.error("Pruefmarkierungen nicht speicherbar (%s): %s",
                         self._state_path, exc)

    def _reviewed(self, filter_id: str) -> Dict[str, Any]:
        return self._state["reviewed"].get(filter_id, {})

    # -- Filter ---------------------------------------------------------

    def _filters_of_node(self, entity_id: str) -> List[Dict[str, Any]]:
        try:
            return self._client.graph_filters(entity_id) or []
        except Exception:
            logger.error("Filter nicht ladbar fuer %s", entity_id, exc_info=True)
            return []

    def create_filter(self, entity_id: str, label: str) -> Optional[Dict[str, Any]]:
        label = " ".join(str(label or "").split())[:MAX_FILTER_LABEL]
        entity_id = str(entity_id or "").strip()
        if not label or not entity_id:
            return None
        for existing in self._filters_of_node(entity_id):
            if _filter_label(existing).strip().lower() == label.lower():
                self._merke_eltern(_filter_id(existing), entity_id)
                return {"id": _filter_id(existing), "entity_id": entity_id,
                        "label": label}
        created = self._client.graph_create_filter(
            entity_id, query_text=label, child_node_name=label)
        if not created:
            return None
        payload = created.get("filter") if isinstance(created.get("filter"), dict) else created
        self._merke_eltern(_filter_id(payload), entity_id)
        return {"id": _filter_id(payload), "entity_id": entity_id, "label": label}

    def _merke_eltern(self, filter_id: str, entity_id: str) -> None:
        """Elternknoten eines Filters festhalten.

        Die API kennt Filter nur ueber ihren Elternknoten; ohne diese Notiz
        muss _locate den ganzen Graphen absuchen. Bei 72 Knoten waren das
        rund zehn Sekunden je Detailabfrage, und es waechst linear mit dem
        Bestand - nach dem Ingest des Korpus waeren es Minuten.
        """
        filter_id, entity_id = str(filter_id or ""), str(entity_id or "")
        if not filter_id or not entity_id:
            return
        with self._lock:
            if self._state["parents"].get(filter_id) == entity_id:
                return
            self._state["parents"][filter_id] = entity_id
            self._save_state()

    def _locate(self, filter_id: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """(entity_id, filter_obj) - die API kennt Filter nur ueber ihren
        Eltern-Knoten.

        Zuerst die gemerkte Zuordnung: das ist ein Aufruf statt einer Suche
        ueber alle Knoten. Die Suche bleibt als Rueckfall, fuer Filter die
        anderswo entstanden sind oder deren Notiz verlorenging.
        """
        gemerkt = self._state["parents"].get(str(filter_id))
        if gemerkt:
            for flt in self._filters_of_node(gemerkt):
                if _filter_id(flt) == str(filter_id):
                    return gemerkt, flt
        for entity_id in self._known_parents():
            for flt in self._filters_of_node(entity_id):
                if _filter_id(flt) == str(filter_id):
                    self._merke_eltern(filter_id, entity_id)
                    return entity_id, flt
        return None

    def _known_parents(self) -> List[str]:
        try:
            from ontology_graph import _node_id
            return [_node_id(n) for n in (self._client.graph_nodes() or [])]
        except Exception:
            logger.error("Knotenliste nicht ladbar", exc_info=True)
            return []

    def filters_for_entity(self, store: Any, entity_id: str) -> List[Dict[str, Any]]:
        out = []
        for flt in self._filters_of_node(entity_id):
            fid = _filter_id(flt)
            proposals = self._proposals(flt, fid)
            out.append(self._summary(fid, _filter_label(flt), entity_id, proposals))
        return out

    # -- Placements -----------------------------------------------------

    def _proposals(self, filter_obj: Dict[str, Any],
                   filter_id: str) -> List[Dict[str, Any]]:
        node_id = _child_node_id(filter_obj)
        if not node_id:
            return []
        reviewed = self._reviewed(filter_id)
        proposals: List[Dict[str, Any]] = []
        for status, mapped in (("active", "pending"), ("rejected", "rejected")):
            try:
                placements = self._client.graph_placements(node_id, status=status) or []
            except Exception:
                logger.error("Placements nicht ladbar (%s, %s)", node_id, status,
                             exc_info=True)
                continue
            for placement in placements:
                item = self._map_placement(placement, mapped)
                if item is None:
                    continue
                if item["state"] == "pending" and reviewed.get(item["id"]) == "accepted":
                    item["state"] = "accepted"
                proposals.append(item)
        proposals.sort(key=lambda p: (-p["score"], p["document"]["path"], p["page"]))
        return proposals

    def _map_placement(self, placement: Dict[str, Any],
                       state: str) -> Optional[Dict[str, Any]]:
        pid = str(_first(placement, "id", "placement_id", "uuid"))
        if not pid:
            return None
        pointer = str(_first(placement, "pointer", "identifier", "document_id"))
        page = _first(placement, "page_number", "page", default=0)
        try:
            page = int(page) or 1
        except (TypeError, ValueError):
            page = 1
        score = _first(placement, "score", "cosine_similarity", "final_score",
                       default=0.0)
        try:
            score = round(float(score), 2)
        except (TypeError, ValueError):
            score = 0.0
        # Die API liefert keinen Passagentext - Wortlaut lokal nachschlagen.
        quote = str(_first(placement, "text", "snippet", "chunk_text"))
        if not quote and self._text is not None and pointer:
            quote = self._text.quote_on_page(pointer, page)
        from ontology_graph import _title_for
        return {
            "id": pid,
            "quote": quote,
            "page": page,
            "score": score,
            "state": state,
            "document": {"path": pointer, "title": _title_for(pointer)},
        }

    @staticmethod
    def _summary(filter_id: str, label: str, entity_id: str,
                 proposals: List[Dict[str, Any]]) -> Dict[str, Any]:
        counts = {"pending": 0, "accepted": 0, "rejected": 0}
        for p in proposals:
            counts[p["state"]] = counts.get(p["state"], 0) + 1
        return {"id": filter_id, "label": label, "entity_id": entity_id,
                "status": "active" if proposals else "collecting",
                "counts": counts}

    def filter_detail(self, store: Any, filter_id: str) -> Optional[Dict[str, Any]]:
        located = self._locate(filter_id)
        if located is None:
            return None
        entity_id, flt = located
        proposals = self._proposals(flt, filter_id)
        return {"filter": self._summary(filter_id, _filter_label(flt), entity_id,
                                        proposals),
                "proposals": proposals}

    # -- Entscheidungen -------------------------------------------------

    def decide(self, store: Any, filter_id: str, proposal_id: str,
               action: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """Ablehnung geht an die API (dort dauerhaft), Uebernahme bleibt lokal."""
        if action not in ("accept", "reject"):
            return None, "bad_action"
        located = self._locate(filter_id)
        if located is None:
            return None, "not_found"
        _, flt = located
        proposals = {p["id"]: p for p in self._proposals(flt, filter_id)}
        proposal = proposals.get(str(proposal_id))
        if proposal is None:
            return None, "not_found"
        if proposal["state"] == "rejected":
            return proposal, ""                 # endgueltig, idempotent
        if action == "reject":
            if self._client.graph_reject_placement(proposal_id) is None:
                return None, "not_found"
            proposal["state"] = "rejected"
            return proposal, ""
        with self._lock:
            self._state["reviewed"].setdefault(filter_id, {})[str(proposal_id)] = "accepted"
            self._save_state()
        proposal["state"] = "accepted"
        return proposal, ""
