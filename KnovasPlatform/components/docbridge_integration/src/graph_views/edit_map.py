"""Where a person changes what the definitions use (KnowledgeBase DEC-0022), and what to add (DEC-0025).

``edit-map.json`` names, for every type, relation (``<From> <relation> <To>``, the edge as stored) and
fact (``<Type>.<fact>``) a definition uses, whether a person edits it, the owning source and the key
path in it. A non-editable entry says why (``from git``, ``from CI``, ``derived by loader``,
``written by script``) and where to fix it at the source (``fix_at``).

``suggestions.json`` lists, per missing required artefact of an instance, ranked candidates with
the rule and a reason a person can check. Both are KnowledgeBase's; the Platform only reads them
from the tenant's definitions bundle (section ``edit_map``, report ``suggestions``; ``bundle.py``).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from graph_views import bundle, repo_layout


def _edit_map() -> Dict[str, Any]:
    return bundle.current().edit_map


def _suggestions() -> Dict[str, Any]:
    return bundle.current().report("suggestions")


def items() -> Dict[str, Dict[str, Any]]:
    return bundle.current().memo("edit_map_items", lambda: {i["id"]: i for i in _edit_map().get("items") or []})


def entry(edit_id: str) -> Optional[Dict[str, Any]]:
    return items().get(edit_id)


def relation_id(instance_type: str, hop: Dict[str, str]) -> str:
    """The edit-map id of one hop from an instance: the edge as stored (from = the tail)."""
    if hop["direction"] == "out":
        return f"{instance_type} {hop['relation']} {hop['to_type']}"
    return f"{hop['to_type']} {hop['relation']} {instance_type}"


def sources(edit_id: str) -> List[str]:
    """The entry's source as repository paths. An edit map may write a source relative to a documentation
    folder; a source that is the tail of a configured file (``repo_layout``) becomes that file's path, any
    other stays as written (the product guesses no folder)."""
    e = entry(edit_id)
    if not e:
        return []
    known = list(repo_layout.current().paths().values())
    out = []
    for part in str(e.get("source") or "").split(";"):
        part = part.strip().strip("/")
        if not part:
            continue
        out.append(next((p for p in known if repo_layout.names_file(part, p)), part))
    return out


def describe(edit_id: str) -> Dict[str, Any]:
    """What the page needs to know about one edit-map id."""
    e = entry(edit_id)
    if e is None:
        return {"id": edit_id, "known": False, "editable": False,
                "why": "Kein Eintrag in der Edit-Map (KnowledgeBase)."}
    out = {"id": edit_id, "known": True, "editable": bool(e.get("editable")),
           "source": e.get("source"), "key_path": e.get("key_path"), "format": e.get("format")}
    if not e.get("editable"):
        out["reason"] = e.get("reason")
        out["fix_at"] = e.get("fix_at")
    if e.get("allowed_values"):
        out["allowed_values"] = e["allowed_values"]
    return out


# -- suggestions (DEC-0025) -----------------------------------------------------------------

def suggestions_generated_at() -> Optional[str]:
    return _suggestions().get("generated_at")


def suggestions_for(instance_key: str, path: str) -> Optional[Dict[str, Any]]:
    """The suggestions item for one missing artefact (instance key + path text), or None."""
    for item in _suggestions().get("items") or []:
        if item.get("instance") == instance_key and item.get("path") == path:
            return item
    return None


def suggestion_items() -> List[Dict[str, Any]]:
    return list(_suggestions().get("items") or [])
