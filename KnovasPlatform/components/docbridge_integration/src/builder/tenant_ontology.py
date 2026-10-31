"""A workspace from the ontology a tenant already has.

A tenant that has been running carries node types in its graph: the ones its
configuration was published with, and the ones made by hand. To model it in
the builder without starting from nothing, the builder reads them through the
Platform's own certificate (GET secured/graph/node-types and each type's
schema) and turns them into the workspace's types.

What a node type says about itself sits in its description, which the loader
writes as ``[level=...; parent=...; iri=...] <en> / <de> — <text>``
(KnowledgeBase NodeType.description_header and type_description). Level,
parent, iri, the names and the text come from there; a type without that
header is the company's own with only its name. The engine's core types are
not listed here (they come with every tenant and the builder shows them from
its own core catalog).

Relations are not read: the graph keeps relation names on edges, not domain
and range. They are added in the builder, or come with an imported package.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Mapping, Optional

from builder import ontology_view, package

_LEVELS = {"euo": "core", "core": "core", "pack": "pack"}
_NAMES = re.compile(r"^(?P<en>[^/—]*?)\s*/\s*(?P<de>[^—]*?)\s*(?:—\s*(?P<text>.*))?$", re.S)


def _text(description: Any) -> dict:
    """Header fields, names and text of a node type's description."""
    raw = description if isinstance(description, str) else ""
    fields = ontology_view.header_fields(raw)
    body = ontology_view.description_text(raw)
    out: dict[str, Any] = {"fields": fields, "label": {}, "text": ""}
    match = _NAMES.match(body) if fields else None
    if match:
        for lang in ("en", "de"):
            if (match.group(lang) or "").strip():
                out["label"][lang] = match.group(lang).strip()
        out["text"] = (match.group("text") or "").strip()
    else:
        out["text"] = body
    return out


def _attribute(raw: Mapping[str, Any], level: str, names: Mapping[str, str]) -> Optional[dict]:
    name = str(raw.get("name") or "").strip()
    if not name or raw.get("deprecated"):
        return None
    attr: dict[str, Any] = {"name": name, "level": level, "datatype": str(raw.get("datatype") or "text")}
    if raw.get("required"):
        attr["required"] = True
    if raw.get("enum_values"):
        attr["enum_values"] = [str(v) for v in raw["enum_values"]]
    target = raw.get("target_node_type_id")
    if target and str(target) in names:
        attr["target"] = names[str(target)]
    if raw.get("description"):
        attr["description"] = {"en": str(raw["description"])}
    return attr


def model_from_tenant(name: str, node_types: Iterable[Mapping[str, Any]],
                      schema_of: Callable[[str], Optional[list]]) -> tuple[dict, dict]:
    """A blank workspace model for ``name`` with the tenant's node types; and counts."""
    model = package.read_package(package.blank_package(name)).model
    rows = [t for t in node_types if isinstance(t, Mapping) and t.get("name")
            and str(t.get("managed_by") or "") != "bundle:core"]
    names = {str(t.get("id")): str(t["name"]) for t in rows if t.get("id") is not None}
    types, attributes, unreadable = [], 0, 0
    for row in sorted(rows, key=lambda t: str(t["name"]).lower()):
        about = _text(row.get("description"))
        level_label = about["fields"].get("level")
        level = _LEVELS.get(str(level_label).lower(), "company") if level_label else "company"
        item: dict[str, Any] = {"key": str(row["name"]), "level": level, "attributes": []}
        # The level as the package names it: a company's own types carry the company's key.
        item["level_label"] = level_label or str(model["company"].get("key") or "company").lower()
        for field, key in (("parent", "parent"), ("iri", "iri")):
            if about["fields"].get(field):
                item[key] = about["fields"][field]
        if about["label"]:
            item["label"] = about["label"]
        if about["text"]:
            item["description"] = {"en": about["text"]}
        schema = schema_of(str(row["id"])) if row.get("id") is not None else None
        if schema is None:
            unreadable += 1
        for raw in schema or []:
            attr = _attribute(raw, level, names) if isinstance(raw, Mapping) else None
            if attr:
                item["attributes"].append(attr)
                attributes += 1
        types.append(item)
    model["ontology"]["types"] = types
    return model, {"types": len(types), "attributes": attributes, "unreadable": unreadable}
