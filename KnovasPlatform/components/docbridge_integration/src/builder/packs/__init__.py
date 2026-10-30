"""Reference packs: the Brain's core types and the industry packs' classes.

Data, not code: the JSON files beside this module are written by
scripts/builder_packs_from_sedm.py from a KnowledgeBase checkout. The
builder shows a company's types under these, read-only.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PACK_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def load() -> tuple[dict, ...]:
    """Every pack, the core first."""
    packs = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(PACK_DIR.glob("*.json"))]
    return tuple(sorted(packs, key=lambda p: (p["pack"] != "core", p["pack"])))
