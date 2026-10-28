"""Display names and descriptions from KnowledgeBase's descriptions.yaml (rule 8).

The vendored copy under definitions/ is pinned in SOURCE.json. A type or
relation the file does not describe shows its technical name and no text —
nothing is made up here.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

DEFINITIONS_DIR = Path(__file__).resolve().parent / "definitions"


@functools.lru_cache(maxsize=4)
def load(path: Optional[str] = None) -> Dict[str, Any]:
    target = Path(path) if path else DEFINITIONS_DIR / "descriptions.yaml"
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


@functools.lru_cache(maxsize=1)
def relations() -> Dict[str, Dict[str, Any]]:
    """relations.yaml (pinned copy): name -> {domain, range}."""
    try:
        data = yaml.safe_load((DEFINITIONS_DIR / "relations.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return {r["name"]: r for r in data.get("relations") or [] if isinstance(r, dict) and r.get("name")}


def hop_fits(type_name: str, hop: Dict[str, str]) -> bool:
    """May a hop start at a node of ``type_name``, by relations.yaml?

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
