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


def type_entry(name: str, doc: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    entry = ((doc if doc is not None else load()).get("types") or {}).get(name) or {}
    return {"name": name,
            "label": str(entry.get("de") or name),
            "label_en": str(entry.get("en") or name),
            "description": str(entry.get("text") or ""),
            "example": str(entry.get("example") or ""),
            "confuse": str(entry.get("confuse") or "")}


def relation_entry(name: str, doc: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    entry = ((doc if doc is not None else load()).get("relations") or {}).get(name) or {}
    return {"name": name,
            "label": str(entry.get("de") or name),
            "label_en": str(entry.get("en") or name),
            "sentence": str(entry.get("sentence") or "")}
