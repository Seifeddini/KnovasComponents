"""Views from the tenant's profiles (WP KC-2, KnowledgeBase KB-2), read from the definitions bundle.

A profile (section ``profiles`` of the bundle) says who it is for (``audience``), how it is
delivered (``delivered_through``) and which views it has: each with an id, a label, a render kind
and a definition (root types, labelled relation paths, a process or spine, columns, colour
signals, rules). The Platform serves the profiles delivered through it.

* **Seeding** turns every view of those profiles into a shared saved view: name = the view's
  label, description = its definition's description, in the language the profile is written in;
  source ``{profile: "<profile id>/<view id>"}``; render kind = the profile's. It is idempotent by
  that source and audit-logged like every shared view (``identity.saved_views``). Nothing here
  names a view or a definition key: they all come from the bundle.
* **Who sees which:** ``views.profile_roles`` maps the Platform's roles (admin, approver,
  ingestion_manager, member) to profiles, by profile id or audience. A user sees the profile views
  of the profiles their roles map to. Without a mapping every user sees every profile's views.
* **Opening a view** (``open_spec``) says which existing render kind draws it, with what.
* **The map** (``levels``) is a tree over the definition's paths, drawn by ``spine_views.tree``.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from graph_views import bundle

#: The value of ``delivered_through`` that means "this Platform" (profile schema: "Platform, secured API, CI job, …").
PLATFORM = "platform"


class NotAProfileView(Exception):
    """The bundle has no such profile view."""


def platform_profiles(b: Optional[bundle.Bundle] = None) -> Dict[str, Dict[str, Any]]:
    """The bundle's profiles delivered through the Platform, by id, in id order."""
    profiles = (b or bundle.current()).profiles or {}
    return {pid: p for pid, p in sorted(profiles.items())
            if isinstance(p, dict) and str(p.get("delivered_through") or "").strip().lower().startswith(PLATFORM)}


def source_key(profile_id: str, view_id: str) -> str:
    return f"{profile_id}/{view_id}"


def find(key: str, b: Optional[bundle.Bundle] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(profile, view) for ``<profile id>/<view id>``; NotAProfileView when the bundle has neither."""
    pid, _, vid = str(key or "").partition("/")
    profile = platform_profiles(b).get(pid)
    view = next((v for v in (profile or {}).get("views") or [] if v.get("id") == vid), None)
    if profile is None or view is None:
        raise NotAProfileView(f"Die Profilansicht {key} steht nicht im Definitionsbündel.")
    return profile, view


def saved_view(profile: Dict[str, Any], view: Dict[str, Any]) -> Dict[str, Any]:
    """The shared saved view a profile view becomes (presentation only; the definition stays in the bundle)."""
    definition = view.get("definition") or {}
    return {"name": str(view.get("label") or view["id"])[:80],
            "description": " ".join(str(definition.get("description") or "").split())[:500],
            "source": {"profile": source_key(profile["id"], view["id"])},
            "render_kind": view["render"], "settings": {}}


def seed(store: Any, admin: Any, b: Optional[bundle.Bundle] = None) -> Dict[str, List[str]]:
    """Create the missing profile views, bring changed ones up to date. Returns source keys."""
    existing = store.profile_views()
    created, updated, kept = [], [], []
    for pid, profile in platform_profiles(b).items():
        for view in profile.get("views") or []:
            if not isinstance(view, dict) or not view.get("id") or not view.get("render"):
                continue
            body = saved_view({**profile, "id": pid}, view)
            key = body["source"]["profile"]
            have = existing.get(key)
            if have is None:
                store.create(admin, body, audience="shared")
                created.append(key)
            elif any(have[k] != body[k] for k in ("name", "description", "render_kind")):
                store.update(have["id"], admin, {k: body[k] for k in ("name", "description", "render_kind")})
                updated.append(key)
            else:
                kept.append(key)
    return {"created": created, "updated": updated, "kept": kept}


# -- who sees which -------------------------------------------------------------------------------

def parse_roles(text: str) -> Dict[str, List[str]]:
    """``"admin=CTO,Engineer; member=Engineer"`` -> {"admin": ["cto", "engineer"], "member": ["engineer"]}."""
    out: Dict[str, List[str]] = {}
    for part in re.split(r"[;\n]", str(text or "")):
        role, sep, names = part.partition("=")
        if not sep or not role.strip():
            continue
        out.setdefault(role.strip().lower(), []).extend(
            n.strip().lower() for n in names.split(",") if n.strip())
    return out


def profiles_for(roles: Iterable[str], mapping: Dict[str, List[str]],
                 b: Optional[bundle.Bundle] = None) -> Optional[Set[str]]:
    """The ids of the profiles a user with ``roles`` sees; None when no mapping is configured (all)."""
    if not mapping:
        return None
    names = {n for r in roles or () for n in mapping.get(str(r).lower(), [])}
    return {pid for pid, p in platform_profiles(b).items()
            if pid.lower() in names or str(p.get("audience") or "").strip().lower() in names}


# -- opening a view -------------------------------------------------------------------------------

def _process_for(root_types: List[str], b: bundle.Bundle) -> Optional[str]:
    """The process whose instances are of the view's first root type."""
    for p in (b.processes or {}).get("processes") or []:
        if root_types and root_types[0] in (p.get("instance_types") or []):
            return p.get("key")
    return None


def open_spec(profile: Dict[str, Any], view: Dict[str, Any], b: Optional[bundle.Bundle] = None) -> Dict[str, Any]:
    """Which render kind of the page draws a profile view, and with what.

    ``kind`` None: no render kind of the Platform draws this view yet (``unsupported`` says why).
    ``note``: what of the definition the drawing leaves out.
    """
    b = b or bundle.current()
    d = view.get("definition") or {}
    roots = list(d.get("root_types") or [])
    render = view.get("render")
    key = source_key(profile["id"], view["id"])
    if render in ("map", "tree") and not d.get("spine"):
        if not d.get("paths"):
            return {"kind": None, "unsupported": f"{key}: ein Baum ohne Achse braucht Pfade."}
        return {"kind": "tree", "profile_view": key}
    if render in ("tree", "matrix") and d.get("spine"):
        return {"kind": render, "spine": d["spine"]}
    if render in ("board", "trace"):
        process = d.get("process") or _process_for(roots, b)
        if not process:
            return {"kind": None, "unsupported": f"{key}: kein Prozess für {', '.join(roots) or 'die Ansicht'}."}
        out: Dict[str, Any] = {"kind": render, "process": process}
        if render == "trace" and d.get("paths"):
            out["note"] = (f"Der Ablauf zeigt die Schritte von {process}; die {len(d['paths'])} Pfade der "
                           "Profilansicht zeichnet er noch nicht.")
        return out
    if render == "list" and roots:
        out = {"kind": "list", "type": roots[0]}
        if d.get("paths") or d.get("group_by"):
            out["note"] = f"Die Liste zeigt {roots[0]}; Pfade und Gruppierung der Profilansicht zeichnet sie noch nicht."
        return out
    if render == "doc":
        return {"kind": "doc"}
    return {"kind": None, "unsupported": f"{key}: die Darstellung {render!r} über "
                                         f"{', '.join(roots) or 'diese Typen'} gibt es auf der Plattform noch nicht."}


# -- the map: a tree over the definition's paths ------------------------------------------------------

def _slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return s or fallback


def levels(view: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Spine-like levels from a view's root types and labelled paths, and what could not be placed.

    Each path extends an earlier one by one hop (``areas``, then ``areas + components``, …). The
    hop from the parent becomes the level's link to its parent (``in`` from the parent is ``out``
    from the child). Paths that end at the same depth from the same parent share a level, one link
    per type. A path that extends no earlier one, or has a ``where``, is listed and left out.
    """
    d = view.get("definition") or {}
    roots = list(d.get("root_types") or [])
    out = [{"id": "root", "name": " / ".join(roots), "types": roots}]
    chains: List[List[Dict[str, str]]] = [[]]
    skipped: List[str] = []
    for i, p in enumerate(d.get("paths") or []):
        hops = [{"relation": h["relation"], "direction": h["direction"], "to_type": h["to_type"]} for h in p.get("path") or []]
        label = str(p.get("label") or f"Pfad {i + 1}")
        if not hops or p.get("where"):
            skipped.append(f"{label}: {'mit Bedingung (where)' if p.get('where') else 'leer'}")
            continue
        parent = next((j for j, c in enumerate(chains) if c == hops[:-1]), None)
        hop = hops[-1]
        link = {"relation": hop["relation"], "direction": "out" if hop["direction"] == "in" else "in"}
        if parent == len(out) - 1:                          # a new, deeper level
            out.append({"id": _slug(label, f"level-{len(out)}"), "name": label, "types": [hop["to_type"]],
                        "links_to_parent": {hop["to_type"]: link}})
            chains.append(hops)
        elif parent == len(out) - 2 and hop["to_type"] not in out[-1]["types"]:   # a sibling of the last level
            out[-1]["types"].append(hop["to_type"])
            out[-1]["links_to_parent"][hop["to_type"]] = link
            out[-1]["name"] += f" · {label}"
        else:
            skipped.append(f"{label}: setzt keinen Pfad der Ebene darüber fort")
    return out, skipped
