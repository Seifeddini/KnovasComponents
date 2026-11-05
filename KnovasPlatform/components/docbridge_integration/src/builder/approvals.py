"""«Freigaben»: write proposals as the Builder shows them (connection layer MVP-27, KC-7b).

A proposal is the tenant's (GET /secured/source-writes, MVP-8): an agent or a person proposes new values for facts
of a node, and a person here accepts or rejects it. Accepting queues a write job; the component turns it into a
change the source reviews (for GitHub a pull request), and the proposal follows the outcome. The Brain's own fact
changes only with the next sync.

The rows are read when the page opens and shown, never stored or logged by the Platform (names of nodes, files and
people stay on the page). Who proposed for whom is shown as declared: the Brain records it, nothing verifies it yet
(section 5 of the plan: accepting is not bound to a person until I2).
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from builder import codes

#: A proposal's status, in German.
STATUS_TEXTS = {
    "proposed": "Offen",
    "accepted": "Angenommen",
    "queued": "In Arbeit: an Knovas Link übergeben",
    "applied": "Pull Request erstellt",
    "conflict": "Konflikt: die Datei hat sich seit dem Vorschlag geändert",
    "refused": "Von der Quelle abgelehnt",
    "failed": "Fehlgeschlagen",
    "rejected": "Abgelehnt",
}
STATUS_TONES = {"proposed": "warning", "accepted": "", "queued": "", "applied": "good", "conflict": "critical",
                "refused": "critical", "failed": "critical", "rejected": ""}
#: How many decided proposals the page shows below the open ones.
DECIDED_SHOWN = 50
ACTOR_KINDS = {"user": "Person", "agent": "Agent", "component": "Komponente", "system": "System"}


def status_text(status: Any) -> str:
    return STATUS_TEXTS.get(str(status), f"Unbekannt ({status})")


def actor_text(actor: Any) -> str:
    """Who proposed, and for whom, as declared."""
    if not isinstance(actor, Mapping) or not actor.get("id"):
        return "unbekannt"
    out = f"{ACTOR_KINDS.get(str(actor.get('kind')), 'Unbekannt')} {actor.get('id')}"
    behalf = actor.get("on_behalf_of")
    if isinstance(behalf, Mapping) and behalf.get("id"):
        out += f" für {ACTOR_KINDS.get(str(behalf.get('kind')), 'Unbekannt')} {behalf.get('id')}"
    return out + " (angegeben)"


def mapping_writes(models: Iterable[Mapping[str, Any]]) -> dict[str, set]:
    """{mapping id: the facts its writes allow, of any type}, over the workspaces' models."""
    out: dict[str, set] = {}
    for model in models:
        for entry in (model or {}).get("mappings") or []:
            defn = entry.get("definition") or {}
            facts = {f for per_type in (defn.get("writes") or {}).values() for f in (per_type or {})}
            out.setdefault(str(defn.get("id")), set()).update(facts)
    return out


def row(p: Mapping[str, Any], allowed_by: Mapping[str, set]) -> dict:
    """One proposal as the page shows it."""
    facts = p.get("facts") if isinstance(p.get("facts"), Mapping) else {}
    current = p.get("current") if isinstance(p.get("current"), Mapping) else {}
    mapping = str(p.get("mapping") or "")
    allowed: Optional[bool] = None
    if mapping in allowed_by:
        allowed = bool(facts) and all(f in allowed_by[mapping] for f in facts)
    code = p.get("code")
    ref = str(p.get("result_ref") or "")
    return {
        "id": str(p.get("id")), "node": p.get("node_name") or p.get("node_id"), "node_id": p.get("node_id"),
        "mapping": mapping, "source": p.get("source"), "file": p.get("external_id"),
        "changes": [{"fact": k, "current": current.get(k), "proposed": v} for k, v in facts.items()],
        "actor": actor_text(p.get("actor")), "at": p.get("created_at"), "reason": p.get("reason") or "",
        "allowed": allowed, "status": p.get("status"), "status_text": status_text(p.get("status")),
        "tone": STATUS_TONES.get(str(p.get("status")), ""),
        "pull_request": ref if ref.startswith("https://") else None,
        "code": code, "code_text": codes.describe(code) if code else None,
    }


def rows(open_rows: Iterable[Mapping[str, Any]], every: Iterable[Mapping[str, Any]], allowed_by: Mapping[str, set]) -> dict:
    """The open proposals, oldest first, and the decided ones, newest first."""
    opened = sorted((row(p, allowed_by) for p in open_rows), key=lambda r: str(r["at"] or ""))
    decided = sorted((row(p, allowed_by) for p in every if p.get("status") != "proposed"),
                     key=lambda r: str(r["at"] or ""), reverse=True)[:DECIDED_SHOWN]
    return {"open": opened, "decided": decided}
