"""Shared pieces of Step 7: errors, KnowledgeBase repositories, permissions, impact and the plan form.

KnowledgeBase is the source of truth for feature plans, so a change never writes the brain: it is
collected in a change set (graph_views.change_set), routed by the edit map (graph_views.writers) and
submitted as one pull request. Permissions reuse node_grants: substantive changes need the
instance's owner or an admin, minor ones any writer (owner, editor, admin).
"""
from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from graph_views import plan_file
from graph_views.facts import fact_values, read_facts
from graph_views.keys import key_type
from graph_views.process_views import current_step
from graph_views.traverse import follow

PLANS_DIR = "docs/ModernDocs/plans/"
DECISIONS_DIR = "docs/ModernDocs/client-zero/ontology/decisions"


class ChangeError(Exception):
    status = 400


class ChangeForbidden(ChangeError):
    status = 403


class ChangeConflict(ChangeError):
    status = 409

    def __init__(self, message: str, diff: str, current: Dict[str, Any], base: Dict[str, Any]):
        super().__init__(message)
        self.diff, self.current, self.base = diff, current, base


class ChangeNotConfigured(ChangeError):
    status = 409


# -- permissions (reusing node_grants) -----------------------------------------------

def may_propose(change_class: str, user: Any, node_id: str, grants: Any) -> bool:
    if user is None:
        return False
    if "admin" in (getattr(user, "roles", ()) or ()):
        return True
    if change_class == "substantive":
        return grants.for_node(node_id).get("owner") == str(user.id)
    return grants.may_write(node_id, user)


# -- impact preview (edge index) -------------------------------------------------------

def impact(ctx: Any, node_id: str, *, proposed_components: Optional[List[str]]) -> Dict[str, Any]:
    """Components (with the proposed ones), the invariants they keep, risks and decisions of the instance."""
    index = ctx.index
    hop = lambda rel, d, t: {"relation": rel, "direction": d, "to_type": t}  # noqa: E731
    walk = lambda start, path: follow(index, start, path).nodes  # noqa: E731
    current = walk([node_id], [hop("affects", "out", "Component")])
    by_key: Dict[str, Optional[str]] = {}
    if proposed_components is not None:
        # Candidates by name, confirmed by the key fact: a name is not a key.
        candidates = {k: [nid for nid, n in index.nodes.items()
                          if n["type"] == "Component" and n["name"].startswith(k)] for k in proposed_components}
        facts_of = read_facts(ctx.state, ctx.reader, [n for c in candidates.values() for n in c[:3]])
        for k, cands in candidates.items():
            by_key[k] = next((n for n in cands[:3] if fact_values(facts_of.get(n)).get("key") == k), None)
    proposed = [v for v in by_key.values() if v]
    components = list(dict.fromkeys(current + proposed))
    header = lambda n: {"id": n, "name": index.nodes.get(n, {}).get("name", n), "type": index.type_of(n)}  # noqa: E731
    invariants = walk(components, [hop("keeps", "out", "Invariant")])
    risks = list(dict.fromkeys(walk([node_id], [hop("affects", "in", "Risk")]) + walk([node_id], [hop("mitigated_by", "in", "Risk")])))
    decisions = walk([node_id], [hop("affects", "in", "Decision")])
    return {"components": [dict(header(c), added=c not in current,
                                removed=proposed_components is not None and c not in proposed) for c in components],
            "unknown_components": [k for k, v in by_key.items() if not v],
            "invariants": [header(i) for i in invariants], "risks": [header(r) for r in risks],
            "decisions": [header(d) for d in decisions]}


def next_decision_number(names: List[str]) -> int:
    nums = [int(m.group(1)) for n in names if (m := re.match(r"^DEC-(\d{4})", n))]
    return (max(nums) if nums else 0) + 1


# -- repositories ---------------------------------------------------------------------

class LocalRepo:
    """Read-only view of a KnowledgeBase checkout (dry runs, previews, demos)."""

    def __init__(self, path: str):
        self.path = path
        self.repo = f"local:{path}"

    def _git(self, command: str, *options: str, rev: str) -> subprocess.CompletedProcess:
        """One git command on one revision. The revision follows --end-of-options and may not
        start with '-', so a value from a request can never be read as an option (--output=…)."""
        if rev.startswith("-"):
            raise ChangeError("Ungültige Revision.")
        return subprocess.run(["git", "-C", self.path, command, *options, "--end-of-options", rev],
                              capture_output=True)

    def branch_sha(self, branch: str) -> str:
        r = self._git("rev-parse", "--verify", rev=f"origin/{branch}")
        if r.returncode != 0:
            r = self._git("rev-parse", "--verify", rev=branch)
        return r.stdout.decode().strip()

    def get_file(self, path: str, ref: str) -> Optional[Tuple[str, str]]:
        r = self._git("show", rev=f"{ref}:{path}")
        if r.returncode != 0:
            return None
        blob = self._git("rev-parse", "--verify", rev=f"{ref}:{path}").stdout.decode().strip()
        return r.stdout.decode("utf-8"), blob

    def list_dir(self, path: str, ref: str) -> List[str]:
        r = self._git("ls-tree", "--name-only", rev=f"{ref}:{path}")
        return r.stdout.decode().split() if r.returncode == 0 else []


def plan_of(ctx: Any, node_id: str) -> Dict[str, Any]:
    """Key, title and file path of a plan node, from what the node holds (source fact)."""
    facts = fact_values(read_facts(ctx.state, ctx.reader, [node_id]).get(node_id))
    source = facts.get("source", "")
    if (not source.endswith(".md") or not source.startswith(PLANS_DIR)
            or ".." in source.split("/") or "\\" in source):
        raise ChangeError("Der Knoten nennt keine Plan-Datei (Fakt source).")
    return {"key": facts.get("key") or "", "path": source, "title": ctx.index.nodes[node_id]["name"],
            "facts": facts}


# -- the plan form ("Bearbeiten") -------------------------------------------------------

#: The template's fields, in order: label, input kind.
FIELDS: List[Dict[str, str]] = [
    {"id": "title", "label": "Titel", "kind": "text"},
    {"id": "description", "label": "Beschreibung (Problem)", "kind": "textarea"},
    {"id": "owner", "label": "Verantwortlich (Author)", "kind": "text"},
    {"id": "target_date", "label": "Zieldatum", "kind": "date"},
    {"id": "capability", "label": "Fähigkeit (serves)", "kind": "keys"},
    {"id": "goal", "label": "Ziel oder Hypothese (motivated_by, justified_by)", "kind": "keys"},
    {"id": "affected_components", "label": "Betroffene Komponenten (Affected L2)", "kind": "keys"},
    {"id": "risks", "label": "Risiken (RISK-Zeilen)", "kind": "risks"},
    {"id": "requirements", "label": "Anforderungen / Verträge (Component contract deltas)", "kind": "textarea"},
    {"id": "sources", "label": "Quellen (References)", "kind": "textarea"},
]
FIELD_IDS = [f["id"] for f in FIELDS]


def _norm(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split()) or None
    if isinstance(value, list):
        return [_norm(v) for v in value] or None
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in sorted(value.items())}
    return value


def changed_fields(current: Dict[str, Any], proposed: Dict[str, Any]) -> List[str]:
    """Fields whose proposed value differs from the file (whitespace and empty-vs-absent do not count)."""
    return [fid for fid in FIELD_IDS if fid in proposed and _norm(proposed[fid]) != _norm(current.get(fid))]


def read_plan(repo: Any, commit: str, plan: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    """(fields as the files hold them at ``commit``, sidecar text)."""
    md = repo.get_file(plan["path"], commit)
    if md is None:
        raise ChangeError(f"Plan-Datei {plan['path']} gibt es auf dem Basisstand nicht.")
    links = repo.get_file(plan_file.PLAN_LINKS_PATH, commit)
    side = repo.get_file(plan["path"][:-3] + ".ai.yaml", commit)
    fields = plan_file.read(md[0], plan["key"], links[0] if links else "").as_dict()
    fields["goal"] = (fields.get("goal") or []) + (fields.get("hypothesis") or [])
    return fields, side[0] if side else None


def form(ctx: Any, defs: Any, proc_key: str, node_id: str, repo: Any, commit: str) -> Dict[str, Any]:
    """The plan's fields at the change set's base commit, with each field's class and route."""
    from graph_views import change_set, writers

    plan = plan_of(ctx, node_id)
    fields, _ = read_plan(repo, commit, plan)
    values = {fid: fields.get(fid) for fid in FIELD_IDS if fid != "target_date"}
    absent = [fid for fid in ("title", "owner", "affected_components", "description", "sources", "requirements")
              if fields.get(fid) is None]
    routes: Dict[str, Dict[str, Any]] = {}
    for fid in FIELD_IDS:
        if fid in writers.FIELD_UNROUTED:
            routes[fid] = {"locked": writers.FIELD_UNROUTED[fid]}
            continue
        ids = list(writers.LINK_FIELDS.get(fid, ())) or [change_set.FIELD_EDIT_IDS[fid]]
        try:
            classes = sorted({change_set.classify(defs, proc_key, i) for i in ids})
            routes[fid] = {"edit_ids": ids, "class": "substantive" if "substantive" in classes else "minor"}
        except ChangeError as exc:
            routes[fid] = {"locked": str(exc)}
    return {"plan": {"key": plan["key"], "path": plan["path"], "title": plan["title"],
                     "status": fields.get("status"), "step": current_step(ctx, defs, proc_key, node_id)},
            "fields": FIELDS, "values": values, "absent": absent, "routes": routes}


def field_ops(current: Dict[str, Any], proposed: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The form's edits as change-set operations: text fields as one ``field`` operation each, key fields
    as one ``add`` or ``remove`` per key, so the change set stays a list of single changes."""
    from graph_views import writers

    out: List[Dict[str, Any]] = []
    for fid in changed_fields(current, proposed):
        if fid in writers.LINK_FIELDS:
            before = set(current.get(fid) or [])
            after = [str(k).strip() for k in proposed.get(fid) or [] if str(k).strip()]
            for key in after:
                if key not in before:
                    out.append({"kind": "add", "edit_id": _link_id(fid, key), "target": key})
            for key in sorted(before - set(after)):
                out.append({"kind": "remove", "edit_id": _link_id(fid, key), "target": key,
                            "reason": "im Plan-Formular entfernt"})
        else:
            out.append({"kind": "field", "field": fid, "value": proposed[fid]})
    return out


def _link_id(fid: str, key: str) -> str:
    if fid == "goal":
        return "FeaturePlan justified_by Hypothesis" if key_type(key, "Hypothesis") else "FeaturePlan motivated_by Goal"
    from graph_views import writers

    return writers.LINK_FIELDS[fid][0]
