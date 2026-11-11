"""The RunEvent a publish leaves in the tenant's graph (build plan step B5).

brain-runner leaves one after each mapping run (its apply's run record);
publishing a workspace version leaves one the same way, written here through
the Brain API on the Platform's certificate: a node of the run-record type
with the facts key, occurred_at, condition, reason, message, observed and
actor, as KnowledgeBase's RunEvent type holds them.

The configuration says which type and key prefix (brain-config.yaml
``runner.run_record``). A workspace without that setting uses the type
``RunEvent`` and the prefix ``EVT-builder-`` when the tenant has that type;
a tenant without it gets no RunEvent, and the publish says so. A RunEvent
that cannot be written does not undo the publish: the ontology is written by
then, and the run is in the builder's own history either way.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Mapping, Optional

DEFAULT_TYPE = "RunEvent"
DEFAULT_PREFIX = "EVT-builder-"


def _rows(payload: Any, *keys: str) -> list:
    if isinstance(payload, list):
        return payload
    for key in keys:
        if isinstance(payload, Mapping) and isinstance(payload.get(key), list):
            return payload[key]
    return []


def write(api, model: Mapping[str, Any], *, version: int, condition: str, reason: str, message: str,
          observed: str, now: Optional[dt.datetime] = None) -> dict:
    """Write the RunEvent; ``{"written": bool, "key", "errors", "why"}``."""
    now = now or dt.datetime.now(dt.timezone.utc)
    record = ((model.get("package") or {}).get("manifest") or {}).get("runner", {}) or {}
    record = record.get("run_record") or {}
    type_name = record.get("type") or DEFAULT_TYPE
    prefix = record.get("key_prefix") or DEFAULT_PREFIX
    types = {t.get("name"): t.get("id") for t in _rows(api.get("secured/graph/node-types"), "node_types")}
    type_id = types.get(type_name)
    if type_id is None:
        return {"written": False, "key": None, "errors": [],
                "why": f"Der Mandant kennt den Typ {type_name} nicht."}
    attributes = {a.get("name"): a.get("id")
                  for a in _rows(api.get(f"secured/graph/node-types/{type_id}/schema"), "attributes")}
    key = f"{prefix}publish-v{version}-{now:%Y-%m-%d-%H%M%S}"
    status, data = api.post("secured/graph/nodes", {
        "name": f"Publish version {version} {now:%Y-%m-%d %H:%M} UTC", "node_type_id": type_id,
        "description": f"Publish of configuration version {version}: {condition} ({reason}).",
    })
    if status not in (200, 201):
        return {"written": False, "key": key, "errors": [f"node: HTTP {status}"], "why": None}
    node_id = ((data or {}).get("node") or {}).get("id")
    facts = {"key": key, "occurred_at": now.date().isoformat(), "condition": condition, "reason": reason,
             "message": message, "observed": observed}
    actor = record.get("actor") or {}
    if actor.get("key"):
        facts["actor"] = actor["key"]
    errors = []
    for fact, value in facts.items():
        if not value:
            continue
        body = {"value": value, "label": fact, "valid_from": now.date().isoformat()}
        if attributes.get(fact):
            body["attribute_id"] = attributes[fact]
        status, _ = api.post(f"secured/graph/nodes/{node_id}/facts", body)
        if status not in (200, 201):
            errors.append(f"fact {fact}: HTTP {status}")
    return {"written": True, "key": key, "errors": errors, "why": None}
