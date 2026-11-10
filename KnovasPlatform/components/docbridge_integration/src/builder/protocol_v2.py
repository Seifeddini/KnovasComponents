"""Connector protocol 2.0 (draft) on the Platform's side: a connector's declaration, as Knovas Link reports it in its
heartbeat, and what the Builder offers from it (FEAT-2026-11-10-write-back C-1; SPEC-write-back -12, -14; GI-CONN-08).

Behind ``BUILDER_PROTOCOL_V2`` (off by default). With it on, the Builder offers writing back to a source when the
component serving it declared, for the source's kind, at least one action and passed every write property of the
conformance suite; it shows the declared actions with the ways each can write (``direct``: after the approval in
Knovas, ``native_review``: as a change for the source's own review). The declaration decides; the Builder reads no
source kind's value (GI-CONN-08). A Link or Platform without protocol 2.0 neither sends nor keeps a declaration.

A declaration is kept only in the checked form below: codes, kinds and short values, never free text. A field this
Platform does not know is dropped, so a newer Link still beats.

**The staging per source** (plan C-Q1, decided A by the founder on 2026-11-10): the Builder keeps how a source's
writes reach it as the source's ``staging`` in the workspace model, ``direct`` unless set (D-3), changed with the
switch edit ``source.switches`` (``staging``) and, like every switch, effective once published. «Freigaben» sends the
published value with each approval (C-2); the Brain refuses a mode the connector did not declare.
"""
from __future__ import annotations

import os
import re
from typing import Any, Mapping, Optional

TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")
VERSION = re.compile(r"^2\.[0-9]{1,3}$")
PARAM_KINDS = ("string", "integer", "boolean", "date", "datetime", "enum", "reference")
STAGINGS = ("direct", "native_review")
PROPERTIES = ("replay_changes_nothing", "deletion_guard", "preview_equals_receipt", "exactly_once", "permit_refusals",
              "no_credential_leaves_site", "review_outcome")
WRITE_PROPERTIES = ("preview_equals_receipt", "exactly_once", "permit_refusals", "no_credential_leaves_site")
STREAMS = ("document", "record", "tombstone", "generation", "state", "principal", "group", "membership", "permission")
STAGING_LABELS = {"direct": "direkt nach der Freigabe in Knovas",
                  "native_review": "als Vorschlag zur Prüfung in der Quelle"}
#: A source's staging when none is set (D-3: ``direct`` for the Knovas tenant's own repositories).
DEFAULT_STAGING = "direct"
_VALUE = re.compile(r"^[^\x00-\x1f\x7f]{1,200}$")


def enabled(environ: Mapping[str, str] = os.environ) -> bool:
    return str(environ.get("BUILDER_PROTOCOL_V2") or "").strip().lower() in ("1", "true", "yes", "on")


def check_declaration(raw: Any) -> dict:
    """The declaration as the Platform keeps it; ValueError naming the field otherwise."""
    if not isinstance(raw, Mapping):
        raise ValueError("declaration")
    if not VERSION.match(str(raw.get("protocol") or "")):
        raise ValueError("declaration.protocol")
    kind = raw.get("source_kind")
    if not isinstance(kind, str) or not TOKEN.match(kind):
        raise ValueError("declaration.source_kind")
    streams = raw.get("streams") or []
    if not isinstance(streams, list) or not all(s in STREAMS for s in streams):
        raise ValueError("declaration.streams")
    actions = raw.get("actions") or []
    if not isinstance(actions, list) or len(actions) > 30:
        raise ValueError("declaration.actions")
    kept = []
    for a in actions:
        if not isinstance(a, Mapping) or not TOKEN.match(str(a.get("name") or "")):
            raise ValueError("declaration.actions.name")
        staging = a.get("staging") or []
        if not isinstance(staging, list) or not staging or not all(s in STAGINGS for s in staging):
            raise ValueError("declaration.actions.staging")
        if a.get("preview") not in ("exact", "none") or a.get("grouping") not in ("atomic", "none"):
            raise ValueError("declaration.actions.preview")
        params = a.get("params") or []
        if not isinstance(params, list) or len(params) > 50:
            raise ValueError("declaration.actions.params")
        kept_params = []
        for p in params:
            if not isinstance(p, Mapping) or not TOKEN.match(str(p.get("name") or "")) or p.get("kind") not in PARAM_KINDS:
                raise ValueError("declaration.actions.params")
            values = p.get("values")
            if values is not None and (not isinstance(values, list) or len(values) > 100
                                       or not all(isinstance(v, str) and _VALUE.match(v) for v in values)):
                raise ValueError("declaration.actions.params.values")
            kept_params.append({"name": p["name"], "kind": p["kind"], "required": p.get("required") is True,
                                **({"values": list(values)} if values else {})})
        inverse = a.get("inverse")
        if inverse is not None and not TOKEN.match(str(inverse)):
            raise ValueError("declaration.actions.inverse")
        kept.append({"name": a["name"], "target_kind": str(a.get("target_kind") or "")[:63], "params": kept_params,
                     "preview": a["preview"], "staging": list(staging), "grouping": a["grouping"], "inverse": inverse})
    passed = ((raw.get("conformance") or {}).get("passed") or []) if isinstance(raw.get("conformance"), Mapping) else []
    if not isinstance(passed, list) or not all(p in PROPERTIES for p in passed):
        raise ValueError("declaration.conformance")
    return {"protocol": raw["protocol"], "source_kind": kind, "streams": list(streams), "fetch": raw.get("fetch") is True,
            "actions": kept, "conformance": {"passed": list(passed)}}


def declaration_of(component: Optional[Mapping[str, Any]]) -> Optional[dict]:
    if not component:
        return None
    found = (component.get("telemetry") or {}).get("declaration")
    return found if isinstance(found, dict) else None


def write_reason(kind: Any, component: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Why writing back is not offered through protocol 2.0 for a source of ``kind``, or None when it is."""
    found = declaration_of(component)
    if found is None:
        return "Die Komponente dieser Quelle hat sich nicht mit Protokoll 2 angemeldet."
    if found.get("source_kind") != kind or not found.get("actions"):
        return "Die Komponente dieser Quelle bietet für diese Art von Quelle keine Aktion an."
    missing = [p for p in WRITE_PROPERTIES if p not in (found.get("conformance") or {}).get("passed", [])]
    if missing:
        return ("Die Komponente hat die Prüfungen fürs Schreiben nicht bestanden (" + ", ".join(missing) + "): "
                "eine Version installieren, die sie besteht.")
    return None


def actions_of(kind: Any, component: Optional[Mapping[str, Any]]) -> list:
    """The declared actions for the page: name, how each can write (in German), its parameters, whether an
    approval is possible (an exact preview)."""
    found = declaration_of(component)
    if found is None or found.get("source_kind") != kind:
        return []
    return [{"name": a["name"], "staging": list(a["staging"]),
             "staging_labels": [STAGING_LABELS[s] for s in a["staging"]],
             "params": [p["name"] for p in a["params"]], "approvable": a["preview"] == "exact",
             "undo": a["inverse"] is not None} for a in found.get("actions") or []]


def staging_of(source: Optional[Mapping[str, Any]]) -> str:
    """How a source's approved writes reach it: its ``staging``, ``direct`` unless set (C-Q1, D-3)."""
    found = (source or {}).get("staging")
    return found if found in STAGINGS else DEFAULT_STAGING


def staging_reason(kind: Any, component: Optional[Mapping[str, Any]], staging: Any) -> Optional[str]:
    """Why ``staging`` cannot be chosen for a source of ``kind`` served by ``component``, or None: the component
    declared it for at least one of its actions for the kind."""
    if staging not in STAGINGS:
        return "Schreibweise: direkt (direct) oder als Vorschlag zur Prüfung in der Quelle (native_review)."
    found = declaration_of(component)
    if found is None or found.get("source_kind") != kind:
        return "Die Komponente dieser Quelle hat sich nicht mit Protokoll 2 für diese Art von Quelle angemeldet."
    if not any(staging in a.get("staging", []) for a in found.get("actions") or []):
        return f"Die Komponente dieser Quelle bietet die Schreibweise «{STAGING_LABELS[staging]}» nicht an."
    return None
