"""Node keys as KnowledgeBase declares them: ``id_prefixes`` per type in terms.yaml.

Used to tell which types a typed key can belong to (search) and which type a
key names (change requests). Read from the pinned copy under definitions/.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Dict, List, Optional

import yaml

TERMS = Path(__file__).resolve().parent / "definitions" / "terms.yaml"


@functools.lru_cache(maxsize=1)
def key_patterns() -> Dict[str, List[re.Pattern]]:
    """type -> compiled id_prefixes."""
    data = yaml.safe_load(TERMS.read_text(encoding="utf-8")) or {}
    out: Dict[str, List[re.Pattern]] = {}
    for t in data.get("node_types") or []:
        if isinstance(t, dict) and t.get("name") and t.get("id_prefixes"):
            out[t["name"]] = [re.compile(p) for p in t["id_prefixes"]]
    return out


def key_type(key: str, *types: str) -> Optional[str]:
    """The first of ``types`` whose declared pattern matches ``key``."""
    pats = key_patterns()
    return next((t for t in types if any(p.match(key) for p in pats.get(t, []))), None)


def literal_prefixes(pattern: str) -> List[str]:
    r"""The fixed start of a pattern: ``^FEAT-\d{4}…`` gives FEAT-, ``^(ICP|P)-…`` gives ICP- and P-."""
    body = pattern.lstrip("^")
    m = re.match(r"^\(([A-Za-z0-9|]+)\)(.*)$", body)
    heads, rest = (m.group(1).split("|"), m.group(2)) if m else ([""], body)
    tail = re.match(r"^[A-Za-z0-9_-]*", rest).group(0)
    return [h + tail for h in heads]


def types_for_query(query: str) -> List[str]:
    """Types whose declared keys can start with what was typed (needs a ``-``)."""
    q = query.strip().upper()
    if "-" not in q:
        return []
    out = []
    for t, pats in key_patterns().items():
        if any(q.startswith(lit) or lit.startswith(q)
               for p in pats for lit in literal_prefixes(p.pattern) if lit):
            out.append(t)
    return out
