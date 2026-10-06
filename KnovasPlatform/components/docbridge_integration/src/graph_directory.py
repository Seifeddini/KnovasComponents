"""Pure projections used by the Cortex directory and context views."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

from graph_model import decode, format_date

HIDDEN_REF = "Nicht sichtbarer Eintrag"


def first(mapping: Any, *keys: str, default: Any = "") -> Any:
    if not isinstance(mapping, dict):
        return default
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return default


def node_id(node: Any) -> str:
    return str(first(node, "id", "node_id", "uuid"))


def node_name(node: Any) -> str:
    return str(first(node, "name", "label", "title"))


def node_type_id(node: Any) -> str:
    value = first(node, "node_type_id", "type_id", "node_type", "type")
    if isinstance(value, dict):
        return str(first(value, "id", "node_type_id", "uuid"))
    return str(value)


def attribute_id(attribute: Any) -> str:
    return str(first(attribute, "id", "attribute_id", "uuid"))


def edge_parts(edge: Any) -> Optional[tuple[str, str, str]]:
    source = str(first(edge, "node_lo", "source", "src", "from_node_id"))
    target = str(first(edge, "node_hi", "target", "dst", "to_node_id"))
    relation = str(first(edge, "relation", "predicate", "label", "type"))
    return (source, target, relation) if source and target else None


def live_attributes(attributes: Optional[Iterable[dict]]) -> list[dict]:
    def sort_key(attribute: dict) -> tuple[int, str]:
        try:
            order = int(attribute.get("sort_order") or 0)
        except (TypeError, ValueError):
            order = 0
        return order, str(attribute.get("name") or "")

    return sorted(
        (
            attribute
            for attribute in attributes or []
            if not attribute.get("deprecated")
            and not attribute.get("deprecated_at")
        ),
        key=sort_key,
    )


def public_attribute(attribute: dict) -> dict:
    return {
        "id": attribute_id(attribute),
        "name": str(attribute.get("name") or ""),
        "datatype": str(attribute.get("datatype") or "text"),
        "required": bool(attribute.get("required")),
        "description": str(attribute.get("description") or ""),
        "enum_values": [str(item) for item in (attribute.get("enum_values") or [])],
        "target_node_type_id": attribute.get("target_node_type_id") or None,
    }


def format_money(value: Any) -> str:
    if not isinstance(value, dict) or value.get("amount") in (None, ""):
        return ""
    try:
        amount = Decimal(str(value["amount"]).replace("'", "").replace("’", ""))
    except (InvalidOperation, ValueError):
        return str(value.get("amount") or "")
    grouped = f"{abs(amount):,.2f}".replace(",", "’")
    sign = "-" if amount < 0 else ""
    return f"{sign}{grouped} {value.get('currency') or ''}".strip()


def ref_target(value: Any) -> Optional[str]:
    decoded = decode("entity_ref", value)
    if isinstance(decoded, dict) and decoded.get("node_id"):
        return str(decoded["node_id"])
    return None


def display(datatype: str, value: Any, names: Optional[dict[str, str]] = None) -> str:
    if value in (None, ""):
        return ""
    if datatype == "date":
        return format_date(value)
    if datatype == "money":
        return format_money(value)
    if datatype == "entity_ref":
        target = ref_target(value)
        return (names or {}).get(target, HIDDEN_REF if target else "")
    if isinstance(value, (dict, list)):
        return ""
    return str(value)


def facts_by_attribute(facts: Iterable[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for fact in facts or []:
        key = str(fact.get("attribute_id") or "")
        if not key:
            continue
        timestamp = str(first(fact, "updated_at", "created_at"))
        old_timestamp = str(first(result.get(key, {}), "updated_at", "created_at"))
        if key not in result or timestamp >= old_timestamp:
            result[key] = fact
    return result


def field_cell(attribute: dict, fact: Optional[dict], names: dict[str, str]) -> dict:
    datatype = str(attribute.get("datatype") or "text")
    value = None if fact is None else fact.get("value")
    target = ref_target(value) if datatype == "entity_ref" else None
    return {
        "attribute": public_attribute(attribute),
        "fact_id": str(fact.get("id")) if fact and fact.get("id") else None,
        "value": decode(datatype, value),
        "display": display(datatype, value, names),
        "missing": value in (None, ""),
        "ref": (
            {"id": target, "name": names[target]}
            if target and target in names
            else None
        ),
    }


def documents(detail: dict) -> list[dict]:
    result: list[dict] = []
    seen: set[str] = set()
    for key in ("assignments", "knowledge", "documents", "pointers"):
        rows = detail.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, str):
                pointer, title = row, row.rsplit("/", 1)[-1]
                metadata = {}
            elif isinstance(row, dict):
                pointer = str(first(row, "pointer", "identifier", "document_id", "id"))
                title = str(first(row, "title", "name", "filename", default=pointer))
                metadata = {
                    "page": first(row, "page", "page_number", default=None),
                    "quote": first(row, "quote", "text", "snippet", default=""),
                }
            else:
                continue
            if pointer and pointer not in seen:
                seen.add(pointer)
                result.append({"pointer": pointer, "title": title or pointer, **metadata})
        if result:
            break
    return result


def neighbourhood(
    centre: dict,
    response: Optional[dict],
    *,
    names: dict[str, str],
    types: dict[str, str],
    fallback_edges: Iterable[dict] = (),
) -> dict:
    centre_id = node_id(centre)
    raw_nodes = (response or {}).get("neighbors") or []
    nodes = [{
        "id": centre_id,
        "name": node_name(centre) or names.get(centre_id, centre_id),
        "node_type_id": node_type_id(centre) or types.get(centre_id, ""),
        "hop": 0,
    }]
    visible = {centre_id}
    for raw in raw_nodes:
        nid = node_id(raw)
        if not nid or nid in visible:
            continue
        visible.add(nid)
        try:
            hop = int(first(raw, "depth", "hop", "distance", default=1))
        except (TypeError, ValueError):
            hop = 1
        nodes.append({
            "id": nid,
            "name": node_name(raw) or names.get(nid, nid),
            "node_type_id": node_type_id(raw) or types.get(nid, ""),
            "hop": max(1, min(3, hop)),
        })
    raw_edges = (response or {}).get("edges") or fallback_edges
    edges = []
    for index, raw in enumerate(raw_edges):
        parts = edge_parts(raw)
        if not parts or parts[0] not in visible or parts[1] not in visible:
            continue
        edges.append({
            "id": str(first(raw, "id", "edge_id", default=f"edge-{index}")),
            "source": parts[0],
            "target": parts[1],
            "label": parts[2],
            "source_kind": str(first(raw, "edge_source", "source_kind", default="manual")),
        })
    return {"nodes": nodes, "edges": edges}


def history_events(
    facts: Iterable[dict],
    histories: dict[str, Optional[list]],
    attribute_names: dict[str, str],
) -> list[dict]:
    events: list[dict] = []
    for fact in facts:
        fact_id = str(fact.get("id") or "")
        rows = histories.get(fact_id)
        if rows is None:
            timestamp = first(fact, "updated_at", "created_at")
            if timestamp:
                rows = [{
                    "id": f"current-{fact_id}",
                    "created_at": timestamp,
                    "new_value": fact.get("value"),
                    "action": "current",
                }]
        for index, row in enumerate(rows or []):
            attribute = str(fact.get("attribute_id") or "")
            events.append({
                "id": str(first(row, "id", "event_id", default=f"{fact_id}-{index}")),
                "timestamp": str(first(row, "created_at", "timestamp", "changed_at")),
                "action": str(first(row, "action", "operation", default="changed")),
                "field": attribute_names.get(
                    attribute, str(fact.get("attribute_name") or fact.get("label") or "Feld")
                ),
                "old_value": first(row, "old_value", "before", default=None),
                "new_value": first(row, "new_value", "after", "value", default=fact.get("value")),
                "actor": str(first(row, "actor_name", "actor_ref", "actor", default="")),
            })
    return sorted(events, key=lambda event: event["timestamp"], reverse=True)


def format_timestamp(value: Any) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.strftime("%d.%m.%Y, %H:%M")
    except ValueError:
        return str(value)
