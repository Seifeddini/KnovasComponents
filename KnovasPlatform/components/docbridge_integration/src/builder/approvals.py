"""«Freigaben»: write proposals as the Builder shows them (connection layer MVP-27, KC-7b).

A proposal is the tenant's (GET /secured/source-writes, MVP-8): an agent or a person proposes new values for facts
of a node, and a person here accepts or rejects it. Accepting queues a write job; the component turns it into a
change the source reviews (for GitHub a pull request), and the proposal follows the outcome. The Brain's own fact
changes only with the next sync.

The rows are read when the page opens and shown, never stored or logged by the Platform (names of nodes, files and
people stay on the page). Who proposed for whom is shown as declared: the Brain records it, nothing verifies it yet
(section 5 of the plan: accepting is not bound to a person until I2).

**Change sets (write back v2, FEAT-2026-11-10-write-back C-2; behind BUILDER_PROTOCOL_V2, off).** A change set holds
named actions of the tenant's ontology (``set_status``, …), each with the preview the connector at the site computed
without writing: per field the value before and after, at the source version it read, and whether it is exact. The
page shows them; «Freigeben» approves with the staging of the sources the actions write (``direct`` unless the
source says otherwise, plan C-Q1), «Ablehnen» rejects with a reason. **An action whose preview is missing or not
exact makes the change set not approvable** (GI-CONN-10; Alloy ``write_back_actions.als::approvalNeedsExactPreview``):
the page offers no «Freigeben» and the route refuses before it calls the Brain, which refuses again. Minimal: no
filters and no history view (plan §5.L, L-8).
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from builder import codes
from builder import protocol_v2

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



# ------------------------------------------------------------------ change sets (write back v2, C-2)

#: A change set's state, in German (the Brain's states, Change Sets API).
CHANGE_SET_STATES = {
    "proposed": "Offen",
    "approved": "Freigegeben: wird geschrieben",
    "applied": "Geschrieben",
    "partly_applied": "Teilweise geschrieben",
    "differing": "Abweichung: die Quelle hält etwas anderes als freigegeben",
    "conflict": "Konflikt: die Quelle hat sich geändert, nichts geschrieben",
    "refused": "Abgelehnt",
    "failed": "Fehlgeschlagen",
    "withdrawn": "Zurückgezogen",
    "reversed": "Rückgängig gemacht",
}
CHANGE_SET_TONES = {"proposed": "warning", "approved": "", "applied": "good", "partly_applied": "critical",
                    "differing": "critical", "conflict": "critical", "refused": "", "failed": "critical",
                    "withdrawn": "", "reversed": ""}
#: A receipt's outcome, in German.
OUTCOME_TEXTS = {"written": "geschrieben", "partly_written": "teilweise geschrieben", "not_written": "nicht geschrieben"}


def change_set_state_text(state: Any) -> str:
    return CHANGE_SET_STATES.get(str(state), f"Unbekannt ({state})")


def _preview_code(action: Mapping[str, Any], brain_code: Optional[str]) -> Optional[str]:
    """Why the action's preview does not allow an approval, or None: there is one, the connector marked it exact, it
    read a version, and the Brain names no reason (it also checks that it was computed for these parameters)."""
    preview = action.get("preview") if isinstance(action.get("preview"), Mapping) else None
    if preview is None:
        return "preview_missing"
    if preview.get("exact") is not True or not preview.get("read_version"):
        return str(preview.get("inexact_code") or "preview_not_exact")
    return brain_code


def _change_lines(changes: Any) -> list:
    """Before and after per field, as the connector computed them (``field`` null: the item's whole content, with its
    diff), in the order of their step where a change names it (protocol 2.0's ``step``, the founder's D-14: two actions
    on one item are told apart by it)."""
    out = []
    for c in changes if isinstance(changes, list) else []:
        if not isinstance(c, Mapping):
            continue
        step = c.get("step") if isinstance(c.get("step"), int) and not isinstance(c.get("step"), bool) else None
        out.append({"field": c.get("field"), "whole": c.get("field") is None, "before": c.get("before"),
                    "after": c.get("after"), "diff": c.get("diff") if isinstance(c.get("diff"), str) else None,
                    "step": step})
    return sorted(out, key=lambda c: -1 if c["step"] is None else c["step"])


def change_set_action(action: Mapping[str, Any], brain_code: Optional[str] = None) -> dict:
    """One action as the page shows it: what it is, where it writes, and its preview."""
    atype = action.get("action_type") if isinstance(action.get("action_type"), Mapping) else {}
    preview = action.get("preview") if isinstance(action.get("preview"), Mapping) else None
    receipt = action.get("receipt") if isinstance(action.get("receipt"), Mapping) else None
    code = _preview_code(action, brain_code)
    params = action.get("params") if isinstance(action.get("params"), Mapping) else {}
    return {
        "id": str(action.get("id")), "action": str(atype.get("key") or ""), "risk_class": atype.get("risk_class"),
        "node_id": action.get("node_id"), "source": action.get("source"), "item": action.get("external_id"),
        "params": [{"name": k, "value": v} for k, v in params.items()],
        "preview": None if preview is None else {
            "exact": preview.get("exact") is True and bool(preview.get("read_version")),
            "read_version": preview.get("read_version"), "changes": _change_lines(preview.get("changes")),
            "computed": [str(f) for f in preview.get("computed") or []],
            "stagings": preview.get("stagings") if isinstance(preview.get("stagings"), list) else None},
        "exact": code is None, "code": code, "code_text": codes.describe(code) if code else None,
        "receipt": None if receipt is None else {
            "outcome": receipt.get("outcome"), "outcome_text": OUTCOME_TEXTS.get(str(receipt.get("outcome")), ""),
            "new_version": receipt.get("new_version"), "code": receipt.get("code"),
            "differences": len(receipt.get("differences") or []) if isinstance(receipt.get("differences"), list) else 0},
    }


def change_set_staging(actions: Iterable[Mapping[str, Any]], staging_by_source: Mapping[str, str]) -> tuple:
    """(staging, None) the approval sends: the published staging of the sources the actions write (``direct``
    unless set, C-Q1); or (None, why in German) when the sources differ or a connector did not declare the mode."""
    actions = list(actions)
    modes = {staging_by_source.get(str(a.get("source")), protocol_v2.DEFAULT_STAGING) for a in actions}
    if len(modes) != 1:
        return None, ("Die Quellen dieses Änderungssatzes schreiben auf verschiedene Weise; ein Änderungssatz wird "
                      "auf eine Weise freigegeben.")
    mode = modes.pop()
    if mode not in protocol_v2.STAGINGS:
        return None, ("Arbeitsbereiche geben einer Quelle dieses Änderungssatzes verschiedene Schreibweisen; "
                      "zuerst eine veröffentlichen.")
    for a in actions:
        declared = (a.get("preview") or {}).get("stagings")
        if declared is not None and mode not in declared:
            return None, (f"Die Komponente der Quelle {a.get('source')} schreibt nicht "
                          f"«{protocol_v2.STAGING_LABELS[mode]}»: in «Quellen» die Schreibweise der Quelle ändern.")
    return mode, None


def change_set_row(cs: Mapping[str, Any], staging_by_source: Mapping[str, str]) -> dict:
    """One change set as the page shows it, and whether «Freigeben» is offered. Approvable only when the Brain says
    so, it is open, no action type is held, the staging is clear, and **every action has an exact preview**
    (GI-CONN-10): the Builder checks the last itself, so an inexact preview is never offered for approval."""
    brain_codes = {str(n.get("action_id")): str(n.get("code")) for n in cs.get("not_approvable") or []
                   if isinstance(n, Mapping) and n.get("code")}
    raw = [a for a in cs.get("actions") or [] if isinstance(a, Mapping)]
    actions = [change_set_action(a, brain_codes.get(str(a.get("id")))) for a in raw]
    state = str(cs.get("state") or "")
    staging, staging_why = change_set_staging(raw, staging_by_source) if raw else (None, None)
    why = []
    if state == "proposed":
        if not actions:
            why.append("Der Änderungssatz hat keine Aktion.")
        if any(not a["exact"] for a in actions):
            why.append("Nicht jede Aktion hat eine genaue Vorschau der Komponente; freigegeben wird nur, was genau "
                       "berechnet ist.")
        if cs.get("held"):
            why.append("Eine Aktionsart dieses Änderungssatzes ist auf ihrer Quelle angehalten, bis eine Person den "
                       "Halt aufhebt.")
        if staging_why:
            why.append(staging_why)
        if not why and cs.get("approvable") is not True:
            why.append("Das Brain gibt diesen Änderungssatz noch nicht frei.")
    return {
        "id": str(cs.get("id")), "state": state, "state_text": change_set_state_text(state),
        "tone": CHANGE_SET_TONES.get(state, ""), "state_code": cs.get("state_code"),
        "state_code_text": codes.describe(cs.get("state_code")) if cs.get("state_code") else None,
        "actor": actor_text(cs.get("proposer")), "at": cs.get("created_at"), "reason": cs.get("reason") or "",
        "evidence_missing": cs.get("evidence_missing") is True, "irreversible": cs.get("irreversible") is True,
        "writes_individually": cs.get("writes_individually") is True,
        "actions": actions, "staging": staging if state == "proposed" else cs.get("staging"),
        "staging_text": protocol_v2.STAGING_LABELS.get(str(staging if state == "proposed" else cs.get("staging")), ""),
        "approvable": state == "proposed" and not why, "not_approvable": why,
    }


def change_set_rows(open_sets: Iterable[Mapping[str, Any]], every: Iterable[Mapping[str, Any]],
                    staging_by_source: Mapping[str, str]) -> dict:
    """The open change sets, oldest first, and the decided ones, newest first."""
    opened = sorted((change_set_row(c, staging_by_source) for c in open_sets),
                    key=lambda r: str(r["at"] or ""))
    decided = sorted((change_set_row(c, staging_by_source) for c in every if c.get("state") != "proposed"),
                     key=lambda r: str(r["at"] or ""), reverse=True)[:DECIDED_SHOWN]
    return {"open": opened, "decided": decided}


def staging_by_source(models: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """{source key: its published staging} over the published models (C-Q1). A key two workspaces give different
    modes is left out, so its change sets are not approvable until one mode is published."""
    found: dict[str, set] = {}
    for model in models:
        for source in (model or {}).get("sources") or []:
            if isinstance(source, Mapping) and source.get("key"):
                found.setdefault(str(source["key"]), set()).add(protocol_v2.staging_of(source))
    return {key: next(iter(modes)) if len(modes) == 1 else "ambiguous" for key, modes in found.items()}
