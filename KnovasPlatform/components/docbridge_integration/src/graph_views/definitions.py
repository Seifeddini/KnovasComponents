"""Process and spine definitions (decision D5): from the tenant, else the pinned copy.

The tenant's definition nodes carry most of a definition as facts (Process:
key, instance_types, owner_role, context; ProcessStep: key, order, resting,
requires/produces, exit_criteria; Spine: levels, branches) and the rest as
edges (has_step, transitions_to, part_of a ValueStream). This module puts them
back into the shape of process-definitions.json / spine-definitions.json and
validates the result against KnowledgeBase's schemas.

What the tenant does not hold (on 2026-10-27: the P-rules, governing_fact,
step owner_role and descriptions, transition rework flags) is taken from the
pinned copy and listed as a gap, for KnowledgeBase to close — never invented.
When the tenant cannot be read, or its definitions do not validate, the pinned
copy is used whole and the page says so.
"""
from __future__ import annotations

import copy
import functools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jsonschema import Draft202012Validator

from graph_views.facts import fact_values, read_facts

DEFINITIONS_DIR = Path(__file__).resolve().parent / "definitions"


@dataclass
class Definitions:
    processes: List[Dict[str, Any]]
    spines: List[Dict[str, Any]]
    rules: List[Dict[str, Any]]
    source: str                                   # "tenant" | "vendored"
    errors: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    differences: List[str] = field(default_factory=list)
    pinned: Dict[str, Any] = field(default_factory=dict)
    #: tenant node id -> process key, and -> (process key, step id); empty
    #: when the tenant could not be read (instances cannot be placed then)
    process_nodes: Dict[str, str] = field(default_factory=dict)
    step_nodes: Dict[str, Tuple[str, str]] = field(default_factory=dict)

    def process(self, key: str) -> Optional[Dict[str, Any]]:
        return next((p for p in self.processes if p.get("key") == key), None)

    def spine(self, key: str) -> Optional[Dict[str, Any]]:
        return next((s for s in self.spines if s.get("key") == key), None)


@functools.lru_cache(maxsize=8)
def _read_json(name: str) -> Any:
    return json.loads((DEFINITIONS_DIR / name).read_text(encoding="utf-8"))


def vendored() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    return copy.deepcopy(_read_json("process-definitions.json")), copy.deepcopy(_read_json("spine-definitions.json"))


def pin() -> Dict[str, Any]:
    return _read_json("SOURCE.json")


def _where(path) -> str:
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "(Wurzel)"


def _name_at(doc: Dict[str, Any], path) -> str:
    """The key or id of the process, step, spine or level an error sits in."""
    names, node = [], doc
    for part in path:
        try:
            node = node[part]
        except (KeyError, IndexError, TypeError):
            break
        if isinstance(node, dict):
            label = node.get("key") or node.get("id")
            if label:
                names.append(str(label))
    return " / ".join(names)


def validate(doc: Dict[str, Any], schema_name: str) -> List[str]:
    """Readable schema errors: where (by key), what, and the JSON path."""
    validator = Draft202012Validator(_read_json(schema_name))
    out = []
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        path = list(err.absolute_path)
        where = _name_at(doc, path)
        out.append(f"{where + ': ' if where else ''}{err.message} (bei {_where(path)})")
    return out


def _split(value: Optional[str]) -> List[str]:
    return [p.strip() for p in str(value or "").split(",") if p.strip()]


def _json(value: Optional[str], what: str, errors: List[str]) -> Any:
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        errors.append(f"{what}: kein gültiges JSON ({exc.msg}, Zeichen {exc.pos})")
        return None


def from_tenant(ctx: Any) -> Tuple[Dict[str, Any], Dict[str, Any], List[str], List[str], Dict[str, Dict]]:
    """(process doc, spine doc, gaps, parse errors, node maps) from definition nodes."""
    index = ctx.index
    proc_ids = list(index.by_type.get("Process", ()))
    step_ids = list(index.by_type.get("ProcessStep", ()))
    spine_ids = list(index.by_type.get("Spine", ()))
    stream_ids = list(index.by_type.get("ValueStream", ()))
    facts = read_facts(ctx.state, ctx.reader, proc_ids + step_ids + spine_ids + stream_ids)
    values = {n: fact_values(facts.get(n)) for n in facts}
    errors: List[str] = []
    gaps: List[Tuple[str, str]] = []
    ref_proc, ref_spine = vendored()
    ref_by_key = {p["key"]: p for p in ref_proc["processes"]}
    nodes: Dict[str, Dict[str, Any]] = {"process": {}, "step": {}}

    processes = []
    for pid in proc_ids:
        v = values.get(pid) or {}
        key = v.get("key")
        if not key:
            errors.append(f"Prozessknoten {index.nodes[pid]['name']!r}: Fakt key fehlt")
            continue
        slug = key[len("PROC-"):] if key.startswith("PROC-") else key
        nodes["process"][pid] = key
        ref = ref_by_key.get(key, {})
        proc: Dict[str, Any] = {"key": key, "name": index.nodes[pid]["name"] or key,
                                "description": index.nodes[pid]["description"],
                                "instance_types": _split(v.get("instance_types")),
                                "owner_role": v.get("owner_role", "")}
        for opt in ("exists_today", "pack"):
            if v.get(opt):
                proc[opt] = v[opt]
        proc["context"] = _json(v.get("context"), f"{key}.context", errors) or []
        streams = [m for r, d, m in index.neighbours(pid, "part_of", "out") if index.type_of(m) == "ValueStream"]
        if streams:
            proc["value_stream"] = (values.get(streams[0]) or {}).get("key") or index.nodes[streams[0]]["name"]
        if "governing_fact" in ref:
            proc["governing_fact"] = ref["governing_fact"]
            gaps.append(("governing_fact", key))

        ref_steps = {s["id"]: s for s in ref.get("steps", [])}
        steps, step_key_to_id = [], {}
        for sid in [m for r, d, m in index.neighbours(pid, "has_step", "out") if m in values]:
            sv = values[sid]
            skey = sv.get("key", "")
            prefix = f"STEP-{slug}-"
            if not skey.startswith(prefix):
                errors.append(f"{key}: Schrittknoten {skey or index.nodes[sid]['name']!r} passt nicht zu {prefix}<id>")
                continue
            step_id = skey[len(prefix):]
            step_key_to_id[sid] = step_id
            nodes["step"][sid] = (key, step_id)
            try:
                order = int(sv.get("order", ""))
            except ValueError:
                errors.append(f"{skey}: order {sv.get('order')!r} ist keine Zahl")
                order = 0
            name = index.nodes[sid]["name"].split(": ", 1)[-1] or step_id
            step = {"id": step_id, "key": skey, "order": order, "name": name,
                    "resting": (sv.get("resting", "no").lower() == "yes"),
                    "requires": _json(sv.get("requires"), f"{skey}.requires", errors) or [],
                    "produces": _json(sv.get("produces"), f"{skey}.produces", errors) or [],
                    "exit_criteria": _split(sv.get("exit_criteria"))}
            rs = ref_steps.get(step_id, {})
            for opt in ("owner_role", "description"):
                if rs.get(opt):
                    step[opt] = rs[opt]
            steps.append(step)
        steps.sort(key=lambda s: s["order"])
        if steps and ref_steps and any(rs.get("description") for rs in ref_steps.values()):
            gaps.append(("Schrittbeschreibungen und owner_role der Schritte", key))
        proc["steps"] = steps

        ref_rework = {(t["from"], t["to"]): t.get("rework") for t in ref.get("transitions", [])}
        transitions = []
        for sid, step_id in step_key_to_id.items():
            for r, d, m in index.neighbours(sid, "transitions_to", "out"):
                if m in step_key_to_id:
                    t = {"from": step_id, "to": step_key_to_id[m]}
                    if ref_rework.get((t["from"], t["to"])):
                        t["rework"] = True
                    transitions.append(t)
        transitions.sort(key=lambda t: (t["from"], t["to"]))
        if any(ref_rework.values()):
            gaps.append(("rework-Kennzeichen der Übergänge", key))
        proc["transitions"] = transitions
        proc["final"] = [s["id"] for s in steps if s["resting"]]
        processes.append(proc)

    rules = ref_proc.get("rules", [])
    if rules:
        gaps.append(("P-Regeln", ""))
    proc_doc = {"version": ref_proc.get("version", 2), "generated_at": "Mandant",
                "source": "tenant definition nodes", "rules": rules,
                "processes": sorted(processes, key=lambda p: p["key"])}

    spines = []
    for spid in spine_ids:
        v = values.get(spid) or {}
        key = v.get("key")
        if not key:
            errors.append(f"Achsenknoten {index.nodes[spid]['name']!r}: Fakt key fehlt")
            continue
        spine = {"key": key, "name": index.nodes[spid]["name"] or key,
                 "description": index.nodes[spid]["description"],
                 "levels": _json(v.get("levels"), f"{key}.levels", errors) or []}
        branches = _json(v.get("branches"), f"{key}.branches", errors)
        if branches:
            spine["branches"] = branches
        spines.append(spine)
    spine_doc = {"version": ref_spine.get("version", 1), "generated_at": "Mandant",
                 "source": "tenant definition nodes", "spines": sorted(spines, key=lambda s: s["key"])}
    return proc_doc, spine_doc, _gap_lines(gaps), errors, nodes


def _gap_lines(gaps: List[Tuple[str, str]]) -> List[str]:
    """One line per missing field, naming the definitions it is missing on."""
    by_field: Dict[str, List[str]] = {}
    for fld, key in gaps:
        by_field.setdefault(fld, [])
        if key and key not in by_field[fld]:
            by_field[fld].append(key)
    return [f"{fld} nicht auf dem Mandanten (aus der Kopie)" + (f": {', '.join(sorted(keys))}" if keys else "")
            for fld, keys in by_field.items()]


def differences(tenant: Dict[str, Any], copy_doc: Dict[str, Any], list_key: str) -> List[str]:
    """Where the tenant and the pinned copy disagree, per definition and field."""
    out = []
    a = {d["key"]: d for d in tenant.get(list_key, [])}
    b = {d["key"]: d for d in copy_doc.get(list_key, [])}
    for key in sorted(set(a) | set(b)):
        if key not in a:
            out.append(f"{key}: nur in der Kopie, nicht auf dem Mandanten")
            continue
        if key not in b:
            out.append(f"{key}: nur auf dem Mandanten, nicht in der Kopie")
            continue
        for fld in ("instance_types", "owner_role", "context", "levels", "branches", "final"):
            if fld in a[key] or fld in b[key]:
                if a[key].get(fld) != b[key].get(fld):
                    out.append(f"{key}: {fld} weicht ab")
        if list_key == "processes":
            sa = {s["id"]: s for s in a[key].get("steps", [])}
            sb = {s["id"]: s for s in b[key].get("steps", [])}
            if set(sa) != set(sb):
                out.append(f"{key}: Schritte weichen ab ({sorted(set(sa) ^ set(sb))})")
            for sid in sorted(set(sa) & set(sb)):
                for fld in ("order", "resting", "requires", "produces", "exit_criteria"):
                    if sa[sid].get(fld) != sb[sid].get(fld):
                        out.append(f"{key}/{sid}: {fld} weicht ab")
            ta = {(t["from"], t["to"]) for t in a[key].get("transitions", [])}
            tb = {(t["from"], t["to"]) for t in b[key].get("transitions", [])}
            if ta != tb:
                out.append(f"{key}: Übergänge weichen ab ({sorted(ta ^ tb)})")
    return out


def load(ctx: Any) -> Definitions:
    """Tenant first; the pinned copy when the tenant is unreadable or invalid."""
    ref_proc, ref_spine = vendored()
    copy_errors = validate(ref_proc, "process-definition.schema.json") + \
        validate(ref_spine, "spine-definition.schema.json")
    try:
        proc_doc, spine_doc, gaps, parse_errors, nodes = from_tenant(ctx)
    except Exception as exc:  # noqa: BLE001 - any failure here means "use the copy"
        return Definitions(ref_proc["processes"], ref_spine["spines"], ref_proc.get("rules", []),
                           "vendored", errors=[f"Mandant nicht lesbar ({type(exc).__name__}); Kopie verwendet"] + copy_errors,
                           pinned=pin())
    errors = parse_errors + validate(proc_doc, "process-definition.schema.json") + \
        validate(spine_doc, "spine-definition.schema.json")
    diffs = differences(proc_doc, ref_proc, "processes") + differences(spine_doc, ref_spine, "spines")
    if errors or not proc_doc["processes"]:
        why = errors or ["keine Prozessdefinitionen auf dem Mandanten"]
        return Definitions(ref_proc["processes"], ref_spine["spines"], ref_proc.get("rules", []),
                           "vendored", errors=[f"Mandant: {e}" for e in why] + copy_errors,
                           gaps=gaps, differences=diffs, pinned=pin(),
                           process_nodes=nodes["process"], step_nodes=nodes["step"])
    return Definitions(proc_doc["processes"], spine_doc["spines"], proc_doc["rules"], "tenant",
                       errors=copy_errors, gaps=gaps, differences=diffs, pinned=pin(),
                       process_nodes=nodes["process"], step_nodes=nodes["step"])
