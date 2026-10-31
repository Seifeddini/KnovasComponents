"""Packs: the Brain's core types and the industry packs (build plan steps B2, B4).

Data, not code. Each pack is a folder beside this module, ``<id>/``, with

  classes.json  the classes the pack is built on, as SeDM names them (id,
                name, labels, parents), written by
                scripts/builder_packs_from_sedm.py from a KnowledgeBase
                checkout. The builder shows a company's types under them,
                read-only. ``core`` has only this.
  pack.json     the terms the pack brings into a workspace, in the workspace
                schema: id, version, name, description, types, relations,
                process templates. Pack terms are read-only in a workspace;
                the company extends them.

A pack with only terms has no classes, and one with only classes (a domain
SeDM knows that no pack ships terms for yet) has no terms.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

PACK_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def load() -> tuple[dict, ...]:
    """Every class catalog, the core first."""
    catalogs = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(PACK_DIR.glob("*/classes.json"))]
    return tuple(sorted(catalogs, key=lambda p: (p["pack"] != "core", p["pack"])))


@lru_cache(maxsize=1)
def term_packs() -> tuple[dict, ...]:
    """Every pack that brings terms into a workspace, by id."""
    packs = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(PACK_DIR.glob("*/pack.json"))]
    return tuple(sorted(packs, key=lambda p: p["id"]))


def term_pack(pack_id: str) -> Optional[dict]:
    return next((p for p in term_packs() if p["id"] == pack_id), None)


def names() -> dict[str, dict]:
    """Display names of every pack, catalog or terms, by id."""
    out = {c["pack"]: dict(c.get("name") or {}) for c in load()}
    out.update({p["id"]: dict(p.get("name") or {}) for p in term_packs()})
    return out
