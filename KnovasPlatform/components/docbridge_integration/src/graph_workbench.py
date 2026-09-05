"""Compose one workbench screen from several Knowledge Graph calls.

Three backend calls per selection, not one per pane: the Secure API is rate
limited at roughly one request a second, and a screen that fans out per widget
becomes unusable at exactly the moment someone opens it in front of a client.

The join between facts and attribute definitions lives here because the field
reader must render an attribute that has NO fact -- the visible gap -- and a
fact-only response cannot express that.

No Flask, no SQL. Takes a client and a grant store, returns a dict.

Plan: docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md (D2)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from graph_model import decode, format_date

logger = logging.getLogger(__name__)


def compose_node(client: Any, grants: Any, node_id: str) -> Optional[dict]:
    detail = client.graph_node(node_id)
    if not detail:
        return None
    node = detail.get("node", detail)
    facts = detail.get("facts")
    if facts is None:
        facts = client.graph_facts(node_id)

    type_id = node.get("node_type_id")
    attributes = client.graph_schema(type_id) if type_id else []
    neighbourhood = client.graph_neighbors(node_id, depth=1, include_edges=True)
    neighbor_nodes = neighbourhood.get("neighbors", [])
    names = {str(n.get("id")): n.get("name") or str(n.get("id"))
             for n in neighbor_nodes if n.get("id")}
    if node.get("id"):
        names[str(node["id"])] = node.get("name") or names.get(str(node["id"]), "")

    return {
        "node": node,
        "fields": _fields(attributes, facts, names),
        "neighbourhood": {"nodes": neighbor_nodes,
                          "edges": neighbourhood.get("edges", [])},
        "grants": grants.for_node(node_id),
        "visibility": {"access_group_ids": list(node.get("access_group_ids") or [])},
    }


def _fields(attributes: list, facts: list, names: Optional[dict] = None) -> list:
    """Schema fields first, in sort order; unschematised facts after.

    A fact whose attribute_id is not in the schema is kept, not dropped. It is
    either free-form (attribute_id NULL) or belongs to a deprecated attribute,
    and both are real content the node genuinely has.
    """
    by_attribute: dict = {}
    for fact in facts:
        key = fact.get("attribute_id")
        if key is not None:
            by_attribute.setdefault(str(key), fact)

    out, claimed = [], set()
    for attribute in sorted(attributes,
                            key=lambda a: (a.get("sort_order", 0), a.get("name", ""))):
        attribute_id = str(attribute.get("id"))
        fact = by_attribute.get(attribute_id)
        if fact is not None:
            claimed.add(str(fact.get("id")))
        out.append(_field(attribute.get("name", ""), attribute.get("datatype", "text"),
                          attribute_id, bool(attribute.get("required", False)),
                          int(attribute.get("sort_order", 0)), fact, names))

    for fact in facts:
        if str(fact.get("id")) in claimed:
            continue
        out.append(_field(fact.get("label") or "Ohne Bezeichnung", "text",
                          fact.get("attribute_id"), False, 9999, fact, names))
    return out


def _field(name, datatype, attribute_id, required, sort_order, fact,
           names: Optional[dict] = None) -> dict:
    value = fact.get("value") if fact else None
    return {
        "attribute_id": attribute_id,
        "name": name,
        "datatype": datatype,
        "required": required,
        "sort_order": sort_order,
        "fact_id": str(fact["id"]) if fact and fact.get("id") else None,
        "value": decode(datatype, value) if fact else None,
        "display": _display(datatype, value, names) if fact else "",
        "missing": fact is None,
    }


def _display(datatype: str, value: Any, names: Optional[dict] = None) -> str:
    if datatype == "date":
        return format_date(value)
    if datatype == "money" and isinstance(value, dict):
        return f"{value.get('currency', '')} {value.get('amount', '')}".strip()
    if datatype == "entity_ref" and isinstance(value, dict):
        node_id = str(value.get("node_id") or "")
        if names and node_id in names and names[node_id]:
            return str(names[node_id])
        return node_id
    return "" if value is None else str(value)
