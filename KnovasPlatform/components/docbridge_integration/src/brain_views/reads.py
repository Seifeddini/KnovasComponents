"""What every Brain view reads the same way (moved from brain_views/dossier.py by PMVP-32a without a change of
behaviour, when the process line became the second view to read a node): which ids may go into an address, the one
per-thing node read as the person, and the two answers a view gives when it cannot show a thing.

``node_payload`` is ``GET /secured/graph/nodes/<id>`` (``graph_node``) through the Platform's one Knovas client,
whose broker signs every call as the person signed in. An unknown node and a node this person may not see both
answer 404 there; the client gives None for both, and this module raises the same NotFound for both (GI-GRAPH-11).
Nothing here logs.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Mapping, Tuple

from knovas_client import KnowledgeGraphDisabled

# A node id as it may go into an address: the Core's are uuids; a few test and core ids are shorter words.
_NODE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class NotFound(Exception):
    """No such node, or one this person may not see: the same answer for both (GI-GRAPH-11)."""


class Unavailable(Exception):
    """The Core did not answer the node read; the message is the error's type name only."""


def valid_node_id(node_id: Any) -> bool:
    return isinstance(node_id, str) and bool(_NODE_ID.match(node_id))


def valid_type_id(type_id: Any) -> bool:
    """A node type id that may go into an address: a uuid, as the Core's tenant types are."""
    if not isinstance(type_id, str) or len(type_id) != 36:
        return False
    try:
        return str(uuid.UUID(type_id)) == type_id.lower()
    except ValueError:
        return False


def node_payload(client: Any, node_id: str) -> Tuple[Mapping[str, Any], Mapping[str, Any]]:
    """``GET /nodes/<id>`` as the person: (the answer, its node). NotFound for an id that is malformed, unknown, not
    visible to the person, or when the graph is switched off; Unavailable when the read itself fails."""
    if not valid_node_id(node_id):
        raise NotFound()
    try:
        payload = client.graph_node(node_id)
    except KnowledgeGraphDisabled:
        raise NotFound() from None
    except Exception as exc:  # noqa: BLE001 - every other failure is one plain message on the page
        raise Unavailable(type(exc).__name__) from None
    if payload is None:
        raise NotFound()
    node = payload.get("node") if isinstance(payload, Mapping) else None
    if not isinstance(node, Mapping):
        raise Unavailable("no node in the answer")
    return payload, node
