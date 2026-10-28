"""A user's change set: link fixes and field edits, reviewed as file diffs, submitted as ONE pull request (Step 7).

KnowledgeBase stays the source of truth. Nothing here writes the brain: every operation is routed
by the edit map (graph_views.writers) to the file that owns it, all operations of a change set are
applied to the files at one base commit, and the result becomes one pull request. After the merge,
the client-zero loader updates the tenant.

An operation is semantic ("add CAP-02 to what FEAT-… serves"), not a text patch, so a change set
survives a moved master: a rebase re-applies the operations on the new head and names any that
no longer apply.

Classes come from the process definition (KnowledgeBase DEC-0024): ``minor`` and ``substantive``
list edit-map ids (and ``name`` / ``description``). A substantive operation on an instance that has
reached ``on_substantive.from`` adds the status rollback (the first status that ``status_map`` maps
to ``on_substantive.to``) and a decision record with the user's reason to the same pull request.
"""
from __future__ import annotations

import datetime as dt
import difflib
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from graph_views import edit_map, writers
from graph_views.change_requests import (DECISIONS_DIR, ChangeConflict, ChangeError, ChangeForbidden, impact,
                                         may_propose, next_decision_number, plan_of)
from graph_views.process_views import current_step

KINDS = ("add", "remove", "replace", "field")
FIELD_EDIT_IDS = {"title": "name", "description": "description", "owner": "FeaturePlan.owner",
                  "risks": "Risk affects FeaturePlan", "requirements": "FeaturePlan keeps ComponentContract"}
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def check_sha(value: Any) -> str:
    """Only git object ids reach a repository (a base comes back from the browser)."""
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ChangeError("Ungültiger Basisstand; bitte den Änderungssatz neu laden.")
    return value


# -- classes and rollback (process definition, DEC-0024) ---------------------------------------

def classes_of(defs: Any, proc_key: str) -> Dict[str, Any]:
    proc = defs.process(proc_key) if defs is not None else None
    cc = (proc or {}).get("change_classes")
    if not isinstance(cc, dict) or "minor" not in cc:
        raise ChangeError(f"Für {proc_key} legt die Prozessdefinition keine Änderungsklassen fest.")
    return cc


def classify(defs: Any, proc_key: str, edit_id: str) -> str:
    cc = classes_of(defs, proc_key)
    if edit_id in (cc.get("substantive") or []):
        return "substantive"
    if edit_id in (cc.get("minor") or []):
        return "minor"
    raise ChangeError(f"„{edit_id}“ hat in {proc_key} keine Änderungsklasse (KnowledgeBase, change_classes).")


def rollback_of(defs: Any, proc_key: str, step: Optional[str]) -> Optional[Dict[str, str]]:
    """{from_step, to_step, status} when a substantive change sends this instance back, else None."""
    proc = defs.process(proc_key)
    on = (classes_of(defs, proc_key).get("on_substantive") or None)
    if not on or step is None or step in (proc.get("final") or []):
        return None
    order = {s["id"]: s["order"] for s in proc["steps"]}
    if step not in order or on["from"] not in order or order[step] < order[on["from"]]:
        return None
    by_type = proc.get("status_map") or {}
    statuses = next(iter(by_type.values()), {}) if by_type else {}
    status = next((s for s, st in statuses.items() if st == on["to"]), None)
    if status is None:
        raise ChangeError(f"status_map von {proc_key} nennt keinen Status für den Schritt {on['to']}.")
    return {"from_step": step, "to_step": on["to"], "status": status}


def rules_again(defs: Any, proc_key: str, rollback: Optional[Dict[str, str]]) -> List[str]:
    """Exit criteria of the steps from the rework step up to where the instance was."""
    if not rollback:
        return []
    proc = defs.process(proc_key)
    order = {s["id"]: s["order"] for s in proc["steps"]}
    lo, hi = order[rollback["to_step"]], order[rollback["from_step"]]
    return list(dict.fromkeys(c for s in sorted(proc["steps"], key=lambda s: s["order"])
                              if lo <= s["order"] < hi for c in s.get("exit_criteria") or []))


# -- operations -------------------------------------------------------------------------------

def _plan(ctx: Any, node_id: str) -> Tuple[writers.Plan, Dict[str, Any]]:
    p = plan_of(ctx, node_id)
    if not p["key"]:
        raise ChangeError("Die Instanz hat keinen Schlüssel (Fakt key).")
    return writers.Plan(p["key"], p["path"]), p


def process_of_instance(ctx: Any, defs: Any, node: str) -> Optional[str]:
    """The process the instance is ``instance_of`` in the brain, by its definition node."""
    for _, _, m in ctx.index.neighbours(node, "instance_of", "out"):
        if m in defs.process_nodes:
            return defs.process_nodes[m]
    return None


def check_instance(ctx: Any, defs: Any, process: str, node: str) -> None:
    """The class, and with it the permission, depends on the process: never take it from the request
    alone. The instance must be ``instance_of`` that process and of one of its instance types."""
    proc = defs.process(process) if defs is not None else None
    if proc is None:
        raise ChangeError(f"Prozess {process} ist nicht definiert.")
    if ctx.index.type_of(node) not in (proc.get("instance_types") or []):
        raise ChangeError(f"{ctx.index.type_of(node)} ist kein Instanztyp von {process}.")
    if process_of_instance(ctx, defs, node) != process:
        raise ChangeError(f"Die Instanz gehört laut Wissensnetz nicht zu {process}.")


def new_op(ctx: Any, defs: Any, user: Any, grants: Any, *, process: str, node: str, kind: str,
           edit_id: str = "", target: str = "", new_target: str = "", field: str = "", value: Any = None,
           reason: str = "", suggestion: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One checked operation: routable, classified, permitted, well-formed. Does not touch a file."""
    if kind not in KINDS:
        raise ChangeError(f"Unbekannte Änderungsart {kind!r}.")
    check_instance(ctx, defs, process, node)
    plan, p = _plan(ctx, node)
    if kind == "field":
        if field in writers.FIELD_UNROUTED:
            raise ChangeError(writers.FIELD_UNROUTED[field])
        if field not in FIELD_EDIT_IDS:
            raise ChangeError(f"Unbekanntes Feld {field!r}.")
        edit_id = FIELD_EDIT_IDS[field]
        if edit_id in writers.FIELD_ROUTES and writers.field_routing_problem(edit_id):
            raise ChangeError(writers.field_routing_problem(edit_id))
    else:
        writer, why = writers.writer_for(edit_id)
        if writer is None:
            raise ChangeError(why)
        if kind in ("add", "replace") and not writer.can_add:
            raise ChangeError("Ein neuer Eintrag dieser Art braucht Text: über „Bearbeiten“ im Plan.")
        target = writer.normalise(target)
        try:
            writer.check_target(target)
            if kind == "replace":
                new_target = writer.normalise(new_target)
                writer.check_target(new_target)
        except writers.WriteError as exc:
            raise ChangeError(str(exc)) from exc
        if kind in ("remove", "replace") and len(" ".join(str(reason or "").split())) < 5:
            raise ChangeError("„Falsch / entfernen“ und „ersetzen“ brauchen eine kurze Begründung (eine Zeile).")
    change_class = classify(defs, process, edit_id)
    if not may_propose(change_class, user, node, grants):
        raise ChangeForbidden("Inhaltliche Änderungen schlagen nur die Verantwortlichen der Instanz (node_grants owner) "
                              "und Administratoren vor." if change_class == "substantive" else
                              "Diese Änderung darf nur vorschlagen, wer die Instanz bearbeiten darf (owner, editor).")
    return {"id": uuid.uuid4().hex[:12], "kind": kind, "process": process, "instance": node,
            "instance_key": plan.key, "instance_name": p["title"], "plan_path": plan.path,
            "edit_id": edit_id, "field": field or None, "target": target or None, "new_target": new_target or None,
            "value": value if kind == "field" else None, "reason": " ".join(str(reason or "").split()) or None,
            "suggestion": suggestion, "class": change_class,
            "added_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def same_change(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    keys = ("kind", "instance", "edit_id", "target", "new_target", "field")
    return all(a.get(k) == b.get(k) for k in keys)


def describe(op: Dict[str, Any]) -> str:
    """One line for the review, the PR body and the decision record."""
    what = writers.WRITERS[op["edit_id"]].label if op["edit_id"] in writers.WRITERS else op["edit_id"]
    if op["kind"] == "add":
        return f"{op['instance_key']}: {what} + {op['target']}"
    if op["kind"] == "remove":
        return f"{op['instance_key']}: {what} − {op['target']}"
    if op["kind"] == "replace":
        return f"{op['instance_key']}: {what} {op['target']} → {op['new_target']}"
    return f"{op['instance_key']}: Feld {op['field']} geändert"


# -- building the files -----------------------------------------------------------------------

class Files:
    """The files an operation set touches, read at one commit, edited in memory."""

    def __init__(self, repo: Any, commit: str):
        self.repo, self.commit = repo, commit
        self.base: Dict[str, Optional[str]] = {}
        self.blob: Dict[str, Optional[str]] = {}
        self.now: Dict[str, Optional[str]] = {}

    def load(self, paths: List[str]) -> Dict[str, Optional[str]]:
        for path in paths:
            if path not in self.base:
                got = self.repo.get_file(path, self.commit)
                self.base[path] = got[0] if got else None
                self.blob[path] = got[1] if got else None
                self.now[path] = self.base[path]
        return {p: self.now[p] for p in paths}

    def changed(self) -> Dict[str, str]:
        return {p: t for p, t in self.now.items() if t is not None and t != self.base.get(p)}


def _files_of(op: Dict[str, Any], plan: writers.Plan) -> List[str]:
    if op["kind"] == "field":
        return [plan.path, plan.sidecar]
    return writers.WRITERS[op["edit_id"]].files(plan)


def _apply_op(files: Files, op: Dict[str, Any], note: str) -> Dict[str, str]:
    plan = writers.Plan(op["instance_key"], op["plan_path"])
    texts = files.load(_files_of(op, plan) + [plan.path])
    if op["kind"] == "field":
        changed = writers.apply_field(texts, plan, op["field"], op["value"])
    else:
        writer = writers.WRITERS[op["edit_id"]]
        if op["kind"] == "replace":
            changed = writers.apply(writer, texts, plan, "remove", op["target"], note)
            changed.update(writers.apply(writer, {**texts, **changed}, plan, "add", op["new_target"], note))
        else:
            changed = writers.apply(writer, texts, plan, op["kind"], op["target"], note)
    files.now.update(changed)
    return changed


def _diff(before: Optional[str], after: str, path: str) -> str:
    return "".join(difflib.unified_diff((before or "").splitlines(keepends=True), after.splitlines(keepends=True),
                                        fromfile=f"a/{path}", tofile=f"b/{path}", n=2))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "change"


def decision_record(number: int, *, instance_key: str, instance_name: str, actor: str, why: str,
                    ops: List[Dict[str, Any]], rollback: Dict[str, str], rules: List[str],
                    components: List[str], today: dt.date) -> Tuple[str, str]:
    """(file name, text) in the client-zero decision-record format (DEC-0014 … DEC-0025)."""
    dec = f"DEC-{number:04d}"
    title = f"Substantive change to {instance_key}"
    changes = "\n".join(f"- {describe(o)}" + (f" — {o['reason']}" if o.get("reason") else "") for o in ops)
    affects = ", ".join([instance_key] + components)
    text = (f"---\nid: {dec}\ntitle: {title}\nstatus: proposed\ndate: {today.isoformat()}\n"
            f"decided_by: {actor} (proposed through the Knovas Platform)\naffects: [{affects}]\n---\n\n"
            f"# {dec} — {title}\n\n"
            f"**Problem.** {instance_name} ({instance_key}) was in step `{rollback['from_step']}`; these changes alter "
            f"what was approved:\n\n{changes}\n\n"
            f"**Decision.** {why.strip()}\n\n"
            f"**Consequences.** The plan goes back to status `{rollback['status']}` (step `{rollback['to_step']}`, "
            f"KnowledgeBase DEC-0024), so these must pass again: {', '.join(rules) or 'the exit criteria of that step'}. "
            f"Affected: {', '.join(components) or instance_key}.\n\nReversibility: easy.\n")
    return f"{dec}-{_slug(instance_key.removeprefix('FEAT-'))}-change.md", text


def build(ctx: Any, defs: Any, repo: Any, change_set: Dict[str, Any], *, actor: str,
          reasons: Optional[Dict[str, str]] = None, today: Optional[dt.date] = None) -> Dict[str, Any]:
    """The review: per-file diffs grouped by owning file, classes, rollbacks, decision records, impact,
    conflicts against the current head. Writes nothing."""
    today = today or dt.date.today()
    reasons = {k: " ".join(str(v or "").split()) for k, v in (reasons or change_set.get("reasons") or {}).items()}
    base = change_set["base"]
    commit = check_sha(base["commit"])
    head = repo.branch_sha(base["branch"])
    files = Files(repo, commit)
    note = f"Knovas Platform change set {today.isoformat()} ({actor})"
    results, routed = [], {}
    for op in change_set["ops"]:
        before = dict(files.now)
        try:
            changed = _apply_op(files, op, note)
            results.append({**op, "ok": True, "files": sorted(changed)})
            for path in changed:
                routed.setdefault(path, []).append(op["id"])
        except (writers.WriteError, ChangeError) as exc:
            # undo this operation only; files it loaded on the way stay loaded, unchanged
            files.now = {**{p: files.base[p] for p in files.base}, **before}
            results.append({**op, "ok": False, "error": str(exc)})

    instances: Dict[str, Dict[str, Any]] = {}
    for r in results:
        inst = instances.setdefault(r["instance"], {"node": r["instance"], "key": r["instance_key"],
                                                    "name": r["instance_name"], "process": r["process"],
                                                    "plan_path": r["plan_path"], "ops": [], "classes": set()})
        inst["ops"].append(r["id"])
        if r["ok"]:
            inst["classes"].add(r["class"])

    decisions_dir = repo.list_dir(DECISIONS_DIR, commit)
    number = next_decision_number(decisions_dir)
    missing_reasons = []
    for node, inst in instances.items():
        ops = [r for r in results if r["instance"] == node and r["ok"]]
        inst["class"] = "substantive" if "substantive" in inst["classes"] else ("minor" if ops else None)
        inst["classes"] = sorted(inst["classes"])
        step = current_step(ctx, defs, inst["process"], node)
        inst["step"] = step
        rollback = rollback_of(defs, inst["process"], step) if inst["class"] == "substantive" else None
        inst["rollback"] = rollback
        inst["rules"] = rules_again(defs, inst["process"], rollback)
        plan = writers.Plan(inst["key"], inst["plan_path"])
        comp_ops = [r for r in ops if r["edit_id"] == "FeaturePlan affects Component"]
        current = writers.WRITERS["FeaturePlan affects Component"].present(files.load([plan.path]), plan)
        inst["impact"] = impact(ctx, node, proposed_components=current if comp_ops else None)
        inst["impact"]["rules"] = inst["rules"]
        if rollback:
            files.now.update(writers.set_status(files.load([plan.path, plan.sidecar]), plan, rollback["status"]))
            why = reasons.get(inst["key"], "")
            if len(why) < 20:
                missing_reasons.append(inst["key"])
            name, text = decision_record(number, instance_key=inst["key"], instance_name=inst["name"], actor=actor,
                                         why=why or "(Begründung fehlt)", ops=ops, rollback=rollback,
                                         rules=inst["rules"], components=current, today=today)
            number += 1
            path = f"{DECISIONS_DIR}/{name}"
            files.base[path], files.blob[path], files.now[path] = None, None, text
            inst["decision"] = path
            routed.setdefault(plan.path, []).append(f"rollback:{inst['key']}")
            routed.setdefault(path, []).append(f"decision:{inst['key']}")

    changed = files.changed()
    conflicts = []
    if head != commit:
        for path in sorted(changed):
            if files.base.get(path) is None and path.startswith(DECISIONS_DIR):
                continue
            now = repo.get_file(path, head)
            if (now[1] if now else None) != files.blob.get(path):
                conflicts.append({"path": path, "diff": _diff(files.base.get(path), now[0] if now else "", path)})
    by_file = [{"path": p, "diff": _diff(files.base.get(p), t, p), "new": files.base.get(p) is None,
                "ops": routed.get(p, []), "blob": files.blob.get(p)} for p, t in sorted(changed.items())]
    return {"base": {"branch": base["branch"], "commit": commit}, "head": head, "behind": head != commit,
            "ops": results, "files": by_file, "instances": list(instances.values()),
            "conflicts": conflicts, "missing_reasons": missing_reasons,
            "ready": bool(by_file) and not conflicts and not missing_reasons and all(r["ok"] for r in results),
            "_texts": changed}


def rebase(repo: Any, change_set: Dict[str, Any]) -> Dict[str, Any]:
    """Move the change set's base to the current head; the operations are re-applied on review."""
    head = repo.branch_sha(change_set["base"]["branch"])
    return {**change_set, "base": {**change_set["base"], "commit": check_sha(head)}}


# -- the pull request ---------------------------------------------------------------------------

def pr_title(review: Dict[str, Any]) -> str:
    keys = [i["key"] for i in review["instances"]]
    n = len([o for o in review["ops"] if o["ok"]])
    head = ", ".join(keys[:2]) + (f" and {len(keys) - 2} more" if len(keys) > 2 else "")
    kind = "substantive" if any(i["class"] == "substantive" for i in review["instances"]) else "minor"
    return f"Platform change set: {n} {'change' if n == 1 else 'changes'} to {head} ({kind})"


def pr_body(review: Dict[str, Any], *, actor: str, reasons: Dict[str, str]) -> str:
    lines = [f"Change set proposed by {actor} through the Knovas Platform (Ansichten). The Platform never writes "
             "the brain: after the merge the client-zero loader updates the tenant.", "",
             f"Base: `{review['base']['branch']}` at `{review['base']['commit'][:12]}`. Change classes and the "
             "rework rule come from the process definitions (DEC-0024); routing from the edit map (DEC-0022).", ""]
    for inst in review["instances"]:
        ops = [o for o in review["ops"] if o["instance"] == inst["node"] and o["ok"]]
        if not ops:
            continue
        lines.append(f"### {inst['key']} — {inst['name']} ({inst['class']})")
        lines.append("")
        for o in ops:
            extra = []
            if o.get("reason"):
                extra.append(f"reason: {o['reason']}")
            if o.get("suggestion"):
                s = o["suggestion"]
                extra.append(f"suggestion {s.get('rule')} (score {s.get('score')}): {s.get('reason')}")
            lines.append(f"- **{o['class']}** {describe(o)}" + (f" — {'; '.join(extra)}" if extra else ""))
        if inst.get("rollback"):
            rb = inst["rollback"]
            lines += ["", f"Status back to `{rb['status']}` (step `{rb['from_step']}` → `{rb['to_step']}`); "
                          f"decision record `{inst['decision']}`. Rules to pass again: {', '.join(inst['rules']) or '—'}.",
                      "", f"> {reasons.get(inst['key'], '')}"]
        imp = inst["impact"]
        lines += ["", f"Impact: {len(imp['components'])} components, {len(imp['invariants'])} invariants, "
                      f"{len(imp['risks'])} risks, {len(imp['decisions'])} decisions.", ""]
    lines.append("Files: " + ", ".join(f"`{f['path']}`" for f in review["files"]))
    return "\n".join(lines)


def submit(review: Dict[str, Any], repo: Any, *, actor: str, reasons: Dict[str, str],
           now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Branch at the base, one commit per file, one pull request. Only a GitHubRepo can write."""
    if not hasattr(repo, "create_pull"):
        from graph_views.change_requests import ChangeNotConfigured

        raise ChangeNotConfigured("Kein GitHub-Zugang konfiguriert: nur Vorschau möglich.")
    if review["conflicts"]:
        raise ChangeConflict("Dateien haben sich auf master geändert; erst neu aufsetzen (Rebase).",
                             "\n".join(c["diff"] for c in review["conflicts"]), {}, {})
    if not review["ready"]:
        raise ChangeError("Der Änderungssatz ist nicht bereit: " + (
            f"Begründung fehlt für {', '.join(review['missing_reasons'])}." if review["missing_reasons"]
            else "nicht anwendbare Änderungen entfernen."))
    now = now or dt.datetime.now(dt.timezone.utc)
    branch = f"platform/changes-{_slug(actor)}-{now:%Y%m%d%H%M%S}"
    repo.create_branch(branch, review["base"]["commit"])
    for f in review["files"]:
        repo.put_file(f["path"], review["_texts"][f["path"]], f"Platform change set: {f['path'].rsplit('/', 1)[-1]}",
                      branch, sha=f["blob"])
    pr = repo.create_pull(pr_title(review), pr_body(review, actor=actor, reasons=reasons), branch, review["base"]["branch"])
    return {"branch": branch, **pr}


def public(review: Dict[str, Any]) -> Dict[str, Any]:
    """The review without the full new texts (the diffs carry what a person reads)."""
    return {k: v for k, v in review.items() if not k.startswith("_")}


def lock_info(edit_id: str) -> Dict[str, Any]:
    """For the page: can this relation be changed here, and if not, why and where."""
    writer, why = writers.writer_for(edit_id)
    info = edit_map.describe(edit_id)
    info["routable"] = writer is not None
    info["can_add"] = bool(writer and writer.can_add)
    if writer is None:
        info["why"] = why
    return info
