"""Connector protocol 2.0 (draft) on the Platform's side: a connector's declaration, as Knovas Link reports it in its
heartbeat, and what the Builder offers from it (FEAT-2026-11-10-write-back C-1; SPEC-write-back -12, -14; GI-CONN-08).

Behind ``BUILDER_PROTOCOL_V2`` (off by default). With it on, the Builder offers writing back to a source when the
component serving it declared, for the source's kind, at least one action and passed every write property of the
conformance suite; it shows the declared actions with the ways each can write (``direct``: after the approval in
Knovas, ``native_review``: as a change for the source's own review). The declaration decides; the Builder reads no
source kind's value (GI-CONN-08). A Link or Platform without protocol 2.0 neither sends nor keeps a declaration.

A declaration is checked against the protocol's own schema (``KnovasLink/contracts/v2/declaration.schema.json``,
copied byte for byte to ``builder/contracts/declaration-v2.schema.json`` because the Platform's image is built from its
own folder; a test fails on any drift): every declaration the schema allows is taken, with the schema's limits and no
other, and kept in the checked form below (the connector's identity is not kept). One the schema does not allow offers
nothing through protocol 2.0 and is named with its field (``declaration_refused``); the heartbeat it came with is kept
(Phase A verification L3).

**Staging needs its conformance** (Phase A verification M4): ``native_review`` is offered only where the connector
passed ``review_outcome``, as the protocol requires for that staging; ``direct`` needs the write properties alone.

**The staging per source** (plan C-Q1, decided A by the founder on 2026-11-10): the Builder keeps how a source's
writes reach it as the source's ``staging`` in the workspace model, ``direct`` unless set (D-3), changed with the
switch edit ``source.switches`` (``staging``) and, like every switch, effective once published. «Freigaben» sends the
published value with each approval (C-2); the Brain refuses a mode the connector did not declare.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional

SCHEMA = Path(__file__).resolve().parent / "contracts" / "declaration-v2.schema.json"
TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")
STAGINGS = ("direct", "native_review")
PROPERTIES = ("replay_changes_nothing", "deletion_guard", "preview_equals_receipt", "exactly_once", "permit_refusals",
              "no_credential_leaves_site", "review_outcome")
WRITE_PROPERTIES = ("preview_equals_receipt", "exactly_once", "permit_refusals", "no_credential_leaves_site")
#: What a staging needs besides the write properties (the declaration schema's ``conformance.passed``).
STAGING_PROPERTIES = {"direct": (), "native_review": ("review_outcome",)}
STREAMS = ("document", "record", "tombstone", "generation", "state", "principal", "group", "membership", "permission")
STAGING_LABELS = {"direct": "direkt nach der Freigabe in Knovas",
                  "native_review": "als Vorschlag zur Prüfung in der Quelle"}
#: A source's staging when none is set (D-3: ``direct`` for the Knovas tenant's own repositories).
DEFAULT_STAGING = "direct"


def enabled(environ: Mapping[str, str] = os.environ) -> bool:
    return str(environ.get("BUILDER_PROTOCOL_V2") or "").strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def _validator():
    from jsonschema import Draft202012Validator

    return Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))


def check_declaration(raw: Any) -> dict:
    """The declaration as the Platform keeps it, when the protocol's schema allows it; ValueError naming the first
    field it does not allow (``declaration.<path>``, names and indices only, never a value) otherwise."""
    errors = sorted(_validator().iter_errors(raw), key=lambda e: list(e.absolute_path))
    if errors:
        where = ".".join(str(p) for p in errors[0].absolute_path)
        raise ValueError(("declaration." + where if where else "declaration")[:120])
    kept = [{"name": a["name"], "target_kind": a["target_kind"],
             "params": [{"name": p["name"], "kind": p["kind"], "required": p["required"] is True,
                         **({"values": list(p["values"])} if p.get("values") else {})} for p in a["params"]],
             "preview": a["preview"], "staging": list(a["staging"]), "grouping": a["grouping"], "inverse": a["inverse"]}
            for a in raw["actions"]]
    return {"protocol": raw["protocol"], "source_kind": raw["source_kind"], "streams": list(raw["streams"]),
            "fetch": raw["fetch"] is True, "actions": kept, "conformance": {"passed": list(raw["conformance"]["passed"])}}


def offered_stagings(action: Mapping[str, Any], passed: Any) -> list:
    """The stagings of an action the Builder offers: those declared whose conformance the connector passed."""
    have = set(passed or [])
    return [s for s in action.get("staging") or [] if s in STAGINGS and set(STAGING_PROPERTIES[s]) <= have]


def declaration_of(component: Optional[Mapping[str, Any]]) -> Optional[dict]:
    if not component:
        return None
    found = (component.get("telemetry") or {}).get("declaration")
    return found if isinstance(found, dict) else None


def write_reason(kind: Any, component: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Why writing back is not offered through protocol 2.0 for a source of ``kind``, or None when it is."""
    found = declaration_of(component)
    if found is None:
        refused = ((component or {}).get("telemetry") or {}).get("declaration_refused")
        if refused:
            return (f"Die Anmeldung der Komponente mit Protokoll 2 entspricht dem Protokoll nicht ({refused}): eine "
                    "Version der Komponente installieren, die zu dieser Plattform passt.")
        return "Die Komponente dieser Quelle hat sich nicht mit Protokoll 2 angemeldet."
    if found.get("source_kind") != kind or not found.get("actions"):
        return "Die Komponente dieser Quelle bietet für diese Art von Quelle keine Aktion an."
    passed = (found.get("conformance") or {}).get("passed", [])
    missing = [p for p in WRITE_PROPERTIES if p not in passed]
    if missing:
        return ("Die Komponente hat die Prüfungen fürs Schreiben nicht bestanden (" + ", ".join(missing) + "): "
                "eine Version installieren, die sie besteht.")
    if not any(offered_stagings(a, passed) for a in found.get("actions") or []):
        return ("Die Komponente bietet nur das Schreiben als Vorschlag zur Prüfung in der Quelle an und hat dessen "
                "Prüfung (review_outcome) nicht bestanden.")
    return None


def actions_of(kind: Any, component: Optional[Mapping[str, Any]]) -> list:
    """The declared actions for the page: name, how each can write (in German), its parameters, whether an
    approval is possible (an exact preview)."""
    found = declaration_of(component)
    if found is None or found.get("source_kind") != kind:
        return []
    passed = (found.get("conformance") or {}).get("passed", [])
    out = []
    for a in found.get("actions") or []:
        offered = offered_stagings(a, passed)
        out.append({"name": a["name"], "staging": offered, "staging_labels": [STAGING_LABELS[s] for s in offered],
                    "staging_withheld": [s for s in a["staging"] if s not in offered],
                    "params": [p["name"] for p in a["params"]], "approvable": a["preview"] == "exact",
                    "undo": a["inverse"] is not None})
    return out


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
    passed = (found.get("conformance") or {}).get("passed", [])
    if not any(staging in a.get("staging", []) for a in found.get("actions") or []):
        return f"Die Komponente dieser Quelle bietet die Schreibweise «{STAGING_LABELS[staging]}» nicht an."
    if not any(staging in offered_stagings(a, passed) for a in found.get("actions") or []):
        missing = ", ".join(STAGING_PROPERTIES[staging])
        return (f"Die Komponente hat die Prüfung für «{STAGING_LABELS[staging]}» nicht bestanden ({missing}): eine "
                "Version installieren, die sie besteht.")
    return None
