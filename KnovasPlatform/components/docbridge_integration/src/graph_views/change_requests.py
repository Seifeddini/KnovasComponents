"""Change requests for process instances, starting with feature plans (Step 7).

The KnowledgeBase repository is the source of truth for feature plans, so an
edit never writes the brain: it becomes a pull request on the plan file (and
its .ai.yaml, and ontology/plan-links.yaml for capability and goal), opened
with a least-privilege token. After the merge the client-zero loader updates
the tenant; until then the TRACE says "Änderung beantragt: PR #n".

Change classes come from the process definition when it carries them, else
from change_classes.yaml beside this module. A substantive change also sends
the plan back to the rework step and adds a decision record (DEC-…) in the
repository's format. Permissions reuse node_grants: substantive changes need
the plan node's owner or an admin, minor ones any writer (owner, editor,
admin). A plan file that moved on master since the form was opened stops the
submit with a diff.
"""
from __future__ import annotations

import datetime as dt
import difflib
import functools
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from graph_views import plan_file
from graph_views.facts import fact_values, read_facts
from graph_views.keys import key_type
from graph_views.process_views import current_step
from graph_views.traverse import follow

CONFIG = Path(__file__).resolve().parent / "change_classes.yaml"
DECISIONS_DIR = "docs/ModernDocs/client-zero/ontology/decisions"

#: The form's fields, in order: label, input kind, where the value lives.
FIELDS: List[Dict[str, str]] = [
    {"id": "title", "label": "Titel", "kind": "text", "where": "plan"},
    {"id": "description", "label": "Beschreibung (Problem)", "kind": "textarea", "where": "plan"},
    {"id": "owner", "label": "Verantwortlich (Author)", "kind": "text", "where": "plan"},
    {"id": "target_date", "label": "Zieldatum", "kind": "date", "where": "none"},
    {"id": "capability", "label": "Fähigkeit (serves)", "kind": "keys", "where": "plan-links"},
    {"id": "goal", "label": "Ziel (motivated_by)", "kind": "keys", "where": "plan-links"},
    {"id": "affected_components", "label": "Betroffene Komponenten (Affected L2)", "kind": "keys", "where": "plan"},
    {"id": "risks", "label": "Risiken (RISK-Zeilen)", "kind": "risks", "where": "plan"},
    {"id": "requirements", "label": "Anforderungen / Verträge (Component contract deltas)", "kind": "textarea", "where": "plan"},
    {"id": "sources", "label": "Quellen (References)", "kind": "textarea", "where": "plan"},
]
FIELD_IDS = [f["id"] for f in FIELDS]


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


@functools.lru_cache(maxsize=1)
def _config() -> Dict[str, Any]:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}


def change_classes(defs: Any, proc_key: str) -> Optional[Dict[str, Any]]:
    """The process definition's change_classes, else the Platform's stand-in."""
    proc = defs.process(proc_key) if defs is not None else None
    if proc and isinstance(proc.get("change_classes"), dict):
        return {**proc["change_classes"], "source": "definition"}
    cfg = _config().get(proc_key)
    return {**cfg, "source": "platform-config"} if cfg else None


# -- keys (terms.yaml id_prefixes, graph_views.keys) ---------------------------------

#: Which types a key field accepts, by terms.yaml.
KEY_FIELDS = {"capability": ("Capability",), "goal": ("Goal", "Hypothesis"), "affected_components": ("Component",)}


def check_keys(proposed: Dict[str, Any]) -> None:
    for fid, types in KEY_FIELDS.items():
        bad = [k for k in proposed.get(fid) or [] if key_type(str(k), *types) is None]
        if bad:
            raise ChangeError(f"{', '.join(bad)} ist kein Schlüssel vom Typ {' oder '.join(types)} (terms.yaml).")


# -- comparing and classifying ------------------------------------------------------

def _norm(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split()) or None
    if isinstance(value, list):
        return [_norm(v) for v in value] or None
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in sorted(value.items())}
    return value


def changed_fields(current: Dict[str, Any], proposed: Dict[str, Any]) -> List[str]:
    """Fields whose proposed value differs from the file (whitespace does not count)."""
    out = []
    for fid in FIELD_IDS:
        if fid in proposed and _norm(proposed[fid]) != _norm(current.get(fid)):
            out.append(fid)
    return out


def classify(fields: List[str], classes: Dict[str, Any]) -> str:
    unmapped = set(classes.get("unmapped") or {})
    bad = [f for f in fields if f in unmapped]
    if bad:
        raise ChangeError(f"Nicht im Plan-Dokument abbildbar: {', '.join(bad)}.")
    unknown = [f for f in fields if f not in classes.get("minor", []) and f not in classes.get("substantive", [])]
    if unknown:
        raise ChangeError(f"Keine Änderungsklasse für: {', '.join(unknown)}.")
    if not fields:
        raise ChangeError("Keine Änderung gegenüber dem Plan.")
    return "substantive" if any(f in classes.get("substantive", []) for f in fields) else "minor"


def rework_status(classes: Dict[str, Any], change_class: str, step: Optional[str]) -> Optional[str]:
    """The plan status a substantive change sets, or None when the step stays."""
    rw = classes.get("rework") or {}
    if change_class != "substantive" or step not in (rw.get("from_steps") or []):
        return None
    return rw.get("status")


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

def impact(ctx: Any, node_id: str, *, step: Optional[str], change_class: str, classes: Dict[str, Any],
           proposed_components: Optional[List[str]], facts: Dict[str, str]) -> Dict[str, Any]:
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
    new_status = rework_status(classes, change_class, step)
    rules = list(classes.get("rules_after_substantive") or []) if change_class == "substantive" else []
    large = classes.get("large_plan_rule") or {}
    size_note = None
    if change_class == "substantive" and large:
        m = re.match(r"^(\d+(?:\.\d+)?)\s*sp$", facts.get(large.get("fact", ""), "").strip())
        if m and float(m.group(1)) >= float(large.get("threshold_sp", 0)):
            rules.append(large["rule"])
        elif not m:
            size_note = f"Größe unbekannt ({large.get('fact')} fehlt): {large['rule']} gilt ab {large.get('threshold_sp')} sp."
    return {"components": [dict(header(c), added=c not in current, removed=bool(proposed_components is not None) and c not in proposed)
                           for c in components],
            "unknown_components": [k for k, v in by_key.items() if not v],
            "invariants": [header(i) for i in invariants], "risks": [header(r) for r in risks],
            "decisions": [header(d) for d in decisions],
            "returns_to": (classes.get("rework") or {}).get("to_step") if new_status else None,
            "new_status": new_status, "rules": rules, "size_note": size_note}


# -- decision record ------------------------------------------------------------------

def next_decision_number(names: List[str]) -> int:
    nums = [int(m.group(1)) for n in names if (m := re.match(r"^DEC-(\d{4})", n))]
    return (max(nums) if nums else 0) + 1


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "change"


def decision_record(number: int, *, plan_key: str, plan_title: str, actor: str, why: str,
                    fields: List[str], components: List[str], new_status: Optional[str],
                    today: dt.date) -> Tuple[str, str]:
    """(file name, text) in the client-zero decision-record format (DEC-0014 … DEC-0017)."""
    dec = f"DEC-{number:04d}"
    title = f"Change to {plan_key}: {', '.join(fields)}"
    affects = ", ".join([plan_key] + components)
    rework = (f" The plan goes back to status `{new_status}`, so its brief artefacts and rules are checked again."
              if new_status else "")
    text = (f"---\nid: {dec}\ntitle: {title}\nstatus: proposed\ndate: {today.isoformat()}\n"
            f"decided_by: {actor} (proposed through the Knovas Platform)\naffects: [{affects}]\n---\n\n"
            f"# {dec} — {title}\n\n"
            f"**Context.** {plan_title} ({plan_key}) is changed in fields that alter its scope: {', '.join(fields)}.\n\n"
            f"**Decision.** {why.strip()}\n\n"
            f"**Consequences.** The pull request carries the plan change and this record.{rework} "
            f"Affected: {affects}.\n")
    return f"{dec}-{_slug(plan_key.removeprefix('FEAT-'))}-change.md", text


# -- repositories ---------------------------------------------------------------------

class LocalRepo:
    """Read-only view of a KnowledgeBase checkout (dry runs, previews, demos)."""

    def __init__(self, path: str):
        self.path = path
        self.repo = f"local:{path}"

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", self.path, *args], capture_output=True)

    def branch_sha(self, branch: str) -> str:
        r = self._git("rev-parse", f"origin/{branch}")
        if r.returncode != 0:
            r = self._git("rev-parse", branch)
        return r.stdout.decode().strip()

    def get_file(self, path: str, ref: str) -> Optional[Tuple[str, str]]:
        r = self._git("show", f"{ref}:{path}")
        if r.returncode != 0:
            return None
        blob = self._git("rev-parse", f"{ref}:{path}").stdout.decode().strip()
        return r.stdout.decode("utf-8"), blob

    def list_dir(self, path: str, ref: str) -> List[str]:
        r = self._git("ls-tree", "--name-only", f"{ref}:{path}")
        return r.stdout.decode().split() if r.returncode == 0 else []


@dataclass
class Snapshot:
    """The files a change touches, as read at one commit of the base branch."""
    commit: str
    md: Tuple[str, str]
    ai: Optional[Tuple[str, str]]
    links: Tuple[str, str]

    def shas(self) -> Dict[str, Optional[str]]:
        return {"md": self.md[1], "ai": self.ai[1] if self.ai else None, "links": self.links[1]}


def read_snapshot(repo: Any, base: str, md_path: str) -> Snapshot:
    commit = repo.branch_sha(base)
    md = repo.get_file(md_path, commit)
    if md is None:
        raise ChangeError(f"Plan-Datei {md_path} gibt es auf {base} nicht.")
    ai = repo.get_file(md_path[:-3] + ".ai.yaml", commit) if md_path.endswith(".md") else None
    links = repo.get_file(plan_file.PLAN_LINKS_PATH, commit) or ("", "")
    return Snapshot(commit, md, ai, links)


def plan_of(ctx: Any, node_id: str) -> Dict[str, Any]:
    """Key, title and file path of a plan node, from what the node holds (source fact)."""
    facts = fact_values(read_facts(ctx.state, ctx.reader, [node_id]).get(node_id))
    source = facts.get("source", "")
    if not source.endswith(".md") or "docs/ModernDocs/plans/" not in source:
        raise ChangeError("Der Knoten nennt keine Plan-Datei (Fakt source).")
    return {"key": facts.get("key") or "", "path": source, "title": ctx.index.nodes[node_id]["name"],
            "facts": facts}


def form(ctx: Any, defs: Any, proc_key: str, node_id: str, repo: Any, base: str) -> Dict[str, Any]:
    classes = change_classes(defs, proc_key)
    if classes is None:
        raise ChangeNotConfigured(f"Für {proc_key} sind keine Änderungsklassen festgelegt.")
    if ctx.index.type_of(node_id) != classes.get("instance_type"):
        raise ChangeError(f"Änderungen gibt es nur für {classes.get('instance_type')}.")
    plan = plan_of(ctx, node_id)
    snap = read_snapshot(repo, base, plan["path"])
    fields = plan_file.read(snap.md[0], plan["key"], snap.links[0]).as_dict()
    values = {fid: fields.get(fid) for fid in FIELD_IDS if fid != "target_date"}
    values["goal"] = (fields.get("goal") or []) + (fields.get("hypothesis") or [])
    absent = [fid for fid in ("title", "owner", "affected_components", "description", "sources", "requirements")
              if fields.get(fid) is None]
    return {"plan": {"key": plan["key"], "path": plan["path"], "title": plan["title"],
                     "ai_path": plan["path"][:-3] + ".ai.yaml" if snap.ai else None,
                     "status": fields.get("status"), "step": current_step(ctx, defs, proc_key, node_id)},
            "fields": FIELDS, "values": values, "absent": absent,
            "unmapped": classes.get("unmapped") or {},
            "classes": {"minor": classes.get("minor", []), "substantive": classes.get("substantive", []),
                        "source": classes["source"]},
            "base": {"branch": base, "commit": snap.commit, **snap.shas()}}


def _apply_all(snap: Snapshot, plan: Dict[str, str], changes: Dict[str, Any], new_status: Optional[str],
               note: str) -> Dict[str, Tuple[str, Optional[str], Dict[str, str]]]:
    """path -> (new text, blob sha it replaces, notes)."""
    out: Dict[str, Tuple[str, Optional[str], Dict[str, str]]] = {}
    plan_changes = {k: v for k, v in changes.items() if k not in ("capability", "goal")}
    if new_status:
        plan_changes["status"] = new_status
    if plan_changes:
        e = plan_file.apply(snap.md[0], plan_changes)
        out[plan["path"]] = (e.text, snap.md[1], e.notes)
        if snap.ai:
            ea = plan_file.apply_ai_yaml(snap.ai[0], plan_changes)
            if ea.text != snap.ai[0]:
                out[plan["path"][:-3] + ".ai.yaml"] = (ea.text, snap.ai[1], ea.notes)
    if "capability" in changes or "goal" in changes:
        text = snap.links[0]
        if "capability" in changes:
            text = plan_file.set_links(text, plan["key"], "serves", list(changes["capability"]), note)
        if "goal" in changes:
            goals = [g for g in changes["goal"] if key_type(str(g), "Goal")]
            hyps = [g for g in changes["goal"] if key_type(str(g), "Hypothesis")]
            text = plan_file.set_links(text, plan["key"], "motivated_by", goals, note)
            text = plan_file.set_links(text, plan["key"], "justified_by", hyps, note)
        out[plan_file.PLAN_LINKS_PATH] = (text, snap.links[1] or None, {"plan-links": "Kanten serves/motivated_by"})
    return out


def _diff(before: str, after: str, path: str) -> str:
    return "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                        fromfile=f"a/{path}", tofile=f"b/{path}", n=2))


def prepare(ctx: Any, defs: Any, proc_key: str, node_id: str, repo: Any, base: str,
            proposed: Dict[str, Any], *, base_shas: Dict[str, Optional[str]], why: str,
            actor: str, today: Optional[dt.date] = None) -> Dict[str, Any]:
    """Everything a submit needs, without writing anything: classify, check, build."""
    today = today or dt.date.today()
    classes = change_classes(defs, proc_key)
    if classes is None:
        raise ChangeNotConfigured(f"Für {proc_key} sind keine Änderungsklassen festgelegt.")
    plan = plan_of(ctx, node_id)
    snap = read_snapshot(repo, base, plan["path"])
    current = {k: v for k, v in snap.shas().items()}
    moved = [k for k in ("md", "ai", "links") if base_shas.get(k) != current.get(k)]
    if moved:
        old_md = repo.get_file(plan["path"], base_shas["md"]) if base_shas.get("md") else None
        diff = _diff(old_md[0] if old_md else "", snap.md[0], plan["path"]) if "md" in moved else ""
        fresh = form(ctx, defs, proc_key, node_id, repo, base)
        raise ChangeConflict("Die Plan-Datei hat sich auf master geändert, seit das Formular geöffnet wurde.",
                             diff, fresh, current)
    check_keys(proposed)
    file_fields = plan_file.read(snap.md[0], plan["key"], snap.links[0]).as_dict()
    file_fields["goal"] = (file_fields.get("goal") or []) + (file_fields.get("hypothesis") or [])
    fields = changed_fields(file_fields, proposed)
    change_class = classify(fields, classes)
    if change_class == "substantive" and len(" ".join(str(why or "").split())) < 20:
        raise ChangeError("Eine inhaltliche Änderung braucht eine Begründung (mindestens 20 Zeichen) für den Entscheidungseintrag.")
    step = current_step(ctx, defs, proc_key, node_id)
    new_status = rework_status(classes, change_class, step)
    changes = {f: proposed[f] for f in fields}
    note = f"change request {today.isoformat()} via the Knovas Platform ({actor})"
    files = _apply_all(snap, plan, changes, new_status, note)
    comps = list(changes.get("affected_components") or file_fields.get("affected_components") or [])
    if change_class == "substantive":
        number = next_decision_number(repo.list_dir(DECISIONS_DIR, snap.commit))
        name, text = decision_record(number, plan_key=plan["key"], plan_title=plan["title"], actor=actor, why=why,
                                     fields=fields, components=comps, new_status=new_status, today=today)
        files[f"{DECISIONS_DIR}/{name}"] = (text, None, {"decision": "neuer Entscheidungseintrag"})
    originals = {plan["path"]: snap.md[0], plan_file.PLAN_LINKS_PATH: snap.links[0]}
    if snap.ai:
        originals[plan["path"][:-3] + ".ai.yaml"] = snap.ai[0]
    diffs = {p: _diff(originals.get(p, ""), t, p) for p, (t, _, _) in files.items()}
    imp = impact(ctx, node_id, step=step, change_class=change_class, classes=classes,
                 proposed_components=changes.get("affected_components"), facts=plan["facts"])
    expected = {"status": new_status} if new_status else {}
    if "owner" in changes:
        expected["owner"] = str(changes["owner"]).strip()
    return {"plan": plan["key"], "path": plan["path"], "commit": snap.commit, "class": change_class,
            "fields": fields, "new_status": new_status, "impact": imp, "files": files, "diffs": diffs,
            "notes": {p: n for p, (_, _, n) in files.items()}, "expected": expected,
            "classes_source": classes["source"], "title": plan["title"]}


def submit(prepared: Dict[str, Any], repo: Any, base: str, *, actor: str, why: str,
           now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Branch, commit each file, open the PR. Only a GitHubRepo can write."""
    if not hasattr(repo, "create_pull"):
        raise ChangeNotConfigured("Kein GitHub-Zugang konfiguriert: nur Vorschau möglich.")
    now = now or dt.datetime.now(dt.timezone.utc)
    branch = f"platform/change-{_slug(prepared['plan'].removeprefix('FEAT-'))}-{now:%Y%m%d%H%M%S}"
    repo.create_branch(branch, prepared["commit"])
    for path, (text, sha, _) in prepared["files"].items():
        repo.put_file(path, text, f"{prepared['plan']}: {prepared['class']} change ({', '.join(prepared['fields'])})",
                      branch, sha=sha)
    imp = prepared["impact"]
    body = "\n".join([
        f"Change request for **{prepared['plan']}** ({prepared['title']}), proposed by {actor} through the Knovas Platform (Ansichten → Ablauf).",
        "",
        f"- Class: **{prepared['class']}** (change classes from the {prepared['classes_source']})",
        f"- Fields: {', '.join(prepared['fields'])}",
        f"- Status: back to `{prepared['new_status']}` (step {imp['returns_to']})" if prepared["new_status"] else "- Status: unchanged",
        f"- Rules to pass again: {', '.join(imp['rules']) or 'none'}" + (f" — {imp['size_note']}" if imp["size_note"] else ""),
        f"- Components: {', '.join(c['name'] for c in imp['components']) or '—'}",
        f"- Invariants touched: {len(imp['invariants'])}, risks: {len(imp['risks'])}, decisions: {len(imp['decisions'])}",
        "", "**Why**", "", why.strip() or "_(minor change)_", "",
        "After the merge the client-zero loader updates the tenant; the Platform shows the change as pending until then.",
    ])
    pr = repo.create_pull(f"{prepared['plan']}: {prepared['class']} change — {', '.join(prepared['fields'])}",
                          body, branch, base)
    return {"branch": branch, **pr}
