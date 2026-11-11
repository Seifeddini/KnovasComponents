"""Display names and descriptions from KnowledgeBase's descriptions.yaml (rule 8).

Both come from the tenant's definitions bundle (sections ``descriptions`` and
``relations``, ``bundle.py``). A type or relation the bundle does not describe
shows its technical name and no text — nothing is made up here.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from graph_views import bundle


def load() -> Dict[str, Any]:
    data = bundle.current().descriptions
    return data if isinstance(data, dict) else {}


def relations() -> Dict[str, Dict[str, Any]]:
    """The bundle's relations: name -> {domain, range}."""
    def build() -> Dict[str, Dict[str, Any]]:
        data = bundle.current().relations
        rels = data.get("relations") if isinstance(data, dict) else None
        return {r["name"]: r for r in rels or [] if isinstance(r, dict) and r.get("name")}

    return bundle.current().memo("relations_by_name", build)


def hop_fits(type_name: str, hop: Dict[str, str]) -> bool:
    """May a hop start at a node of ``type_name``, by the bundle's relations?

    ``out``: the start node is the edge's node_lo, so its type must be in the
    relation's domain; ``in``: it is node_hi, so in the range. Unknown
    relations fit nothing.
    """
    rel = relations().get(hop.get("relation", ""))
    if not rel:
        return False
    side = rel.get("domain") if hop.get("direction") == "out" else rel.get("range")
    return type_name in (side or [])


def type_entry(name: str, doc: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    entry = ((doc if doc is not None else load()).get("types") or {}).get(name) or {}
    return {"name": name,
            "label": str(entry.get("de") or name),
            "label_en": str(entry.get("en") or name),
            "description": str(entry.get("text") or ""),
            "example": str(entry.get("example") or ""),
            "confuse": str(entry.get("confuse") or "")}


def attribute_label(type_name: str, attribute: str, doc: Optional[Dict[str, Any]] = None) -> str:
    """The German display name of an attribute (attributes.<Type>.<attr>, else attributes._common)."""
    attrs = (doc if doc is not None else load()).get("attributes") or {}
    entry = (attrs.get(type_name) or {}).get(attribute) or (attrs.get("_common") or {}).get(attribute) or {}
    return str(entry.get("de") or attribute) if isinstance(entry, dict) else attribute


def relation_entry(name: str, doc: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    entry = ((doc if doc is not None else load()).get("relations") or {}).get(name) or {}
    return {"name": name,
            "label": str(entry.get("de") or name),
            "label_en": str(entry.get("en") or name),
            "sentence": str(entry.get("sentence") or "")}
