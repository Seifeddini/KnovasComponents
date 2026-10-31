"""A company's processes: the model, the checks and the view (build plan step B6).

The workspace keeps the processes document as the package writes it
(``ontology/processes.yaml``, KnowledgeBase's format): a process has steps in
order, transitions ``[from, to]`` or ``[from, to, rework]``, the resting steps
under ``final``, the instance types, the governing fact and the status map
(per instance type: governing-fact value -> step id). A step has what it
``requires`` and ``produces`` -- artefacts reached from the instance along a
path of relation hops -- and its ``exit_criteria`` (R-rule or P-rule ids).
Edits (builder.editing) change that document in place, so compiling writes the
processes in the package's own format and brain-runner bundles them as they are.

The checks are the ones KnowledgeBase's validator (validate_definitions.py)
applies to a package: the process-definition schema over the normalised form
(``normalise``, the shape of generated/process-definitions.json), then the
rules -- instance types, steps, transitions (rework goes back, the forward ones
form no cycle), the status map against the governing fact's values, change
classes, exit criteria and every path against the relations' domain and range.
Two more the builder adds because a canvas makes them easy to get wrong: a step
no transition reaches from the first one, and a status value that two
processes map for the same type and fact.
"""
from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "process-definition.schema.json"

#: The rules report's rules (R0-R8) an exit criterion may name besides the document's own P-rules.
R_RULES = tuple(f"R{n}" for n in range(9))
CHECKS = {"fact_present": "Attribut vorhanden", "facts_present": "Attribute vorhanden",
          "date_not_past": "Datum nicht überschritten"}
DIRECTIONS = ("out", "in")
WILDCARD = "*"


# ------------------------------------------------------------------ the document

def document(model: Mapping[str, Any]) -> dict:
    doc = model.get("processes")
    return doc if isinstance(doc, dict) else {}


def processes_of(model: Mapping[str, Any]) -> list[dict]:
    items = document(model).get("processes")
    return [p for p in items if isinstance(p, dict)] if isinstance(items, list) else []


def step_of(step: Any) -> dict:
    """A step as a definition; version 1 wrote a plain name."""
    return step if isinstance(step, dict) else {"id": step}


def step_ids(process: Mapping[str, Any]) -> list[str]:
    return [str(step_of(s).get("id")) for s in process.get("steps") or []]


def transitions_of(process: Mapping[str, Any]) -> list[tuple[str, str, bool]]:
    out = []
    for t in process.get("transitions") or []:
        if isinstance(t, (list, tuple)) and len(t) >= 2:
            out.append((str(t[0]), str(t[1]), len(t) > 2 and str(t[2]) == "rework"))
        elif isinstance(t, Mapping):
            out.append((str(t.get("from")), str(t.get("to")), bool(t.get("rework"))))
    return out


def transition_entry(source: str, target: str, rework: bool) -> list:
    """A transition as the package writes it."""
    return [source, target, "rework"] if rework else [source, target]


def _hops(path: Any) -> list[dict]:
    return [{"relation": str(h.get("relation")), "direction": str(h.get("direction")), "to_type": str(h.get("to_type"))}
            for h in path or [] if isinstance(h, Mapping)]


def _artefact(a: Mapping[str, Any]) -> dict:
    out = {"type": str(a.get("type")), "required": bool(a.get("required", False)),
           "min_count": int(a.get("min_count", 1) or 1), "path": _hops(a.get("path")),
           "verified": bool(a.get("verified", True))}
    alt = a.get("alternative")
    if isinstance(alt, Mapping):
        out["alternative"] = {"type": str(alt.get("type")), "path": _hops(alt.get("path"))}
    where = a.get("where")
    if isinstance(where, Mapping):
        out["where"] = {"fact": str(where.get("fact")), "equals": str(where.get("equals"))}
    for key in ("note", "label"):
        if a.get(key):
            out[key] = str(a[key])
    return out


def normalise(doc: Mapping[str, Any]) -> dict:
    """The processes document in the schema's form (KnowledgeBase terms.load_process_definitions)."""
    procs = []
    for p in doc.get("processes") or []:
        if not isinstance(p, Mapping):
            procs.append(p)
            continue
        slug = str(p.get("key", "")).removeprefix("PROC-")
        final = {str(x) for x in p.get("final") or []}
        steps = []
        for i, raw in enumerate(p.get("steps") or [], start=1):
            st = step_of(raw)
            step = {"id": str(st.get("id")), "key": f"STEP-{slug}-{st.get('id')}", "order": int(st.get("order", i)),
                    "name": str(st.get("name") or st.get("id")).replace("-", " ")}
            for k in ("owner_role", "description"):
                if st.get(k):
                    step[k] = str(st[k])
            step["resting"] = bool(st.get("resting", str(st.get("id")) in final))
            for k in ("requires", "produces"):
                step[k] = [_artefact(a) for a in st.get(k) or [] if isinstance(a, Mapping)]
            step["exit_criteria"] = [str(x) for x in st.get("exit_criteria") or []]
            steps.append(step)
        cc = p.get("change_classes") or {}
        trig = cc.get("on_substantive") if isinstance(cc, Mapping) else None
        proc = {"key": p.get("key"), "name": p.get("name"), "description": str(p.get("description") or ""),
                "instance_types": [str(x) for x in p.get("instance_types") or []],
                "governing_fact": str(p.get("governing_fact") or "status"),
                "status_map": {str(t): {str(k): (None if v is None else str(v)) for k, v in (m or {}).items()}
                               for t, m in (p.get("status_map") or {}).items()},
                "owner_role": p.get("owner_role"), "exists_today": str(p.get("exists_today") or ""),
                "context": [_artefact(a) for a in p.get("context") or [] if isinstance(a, Mapping)], "steps": steps,
                "transitions": [{"from": a, "to": b, "rework": r} for a, b, r in transitions_of(p)],
                "final": [str(x) for x in p.get("final") or []],
                "change_classes": {"minor": [str(x) for x in (cc or {}).get("minor") or []],
                                   "substantive": [str(x) for x in (cc or {}).get("substantive") or []],
                                   "on_substantive": ({"from": str(trig["from"]), "to": str(trig["to"])}
                                                      if isinstance(trig, Mapping) else None)}}
        for k in ("pack", "value_stream"):
            if p.get(k):
                proc[k] = str(p[k])
        procs.append(proc)
    return {"version": int(doc.get("version", 1) or 1), "generated_at": "",
            "rules": [dict(r) for r in doc.get("rules") or [] if isinstance(r, Mapping)],
            # As KnowledgeBase normalises them: who computes a report rule is filled in when the file leaves it out.
            "report_rules": [{**dict(r), "applies_to": [str(x) for x in r.get("applies_to") or []],
                              "computed_by": str(r.get("computed_by") or "rules report")}
                             for r in doc.get("report_rules") or [] if isinstance(r, Mapping)],
            "processes": procs}


@lru_cache(maxsize=1)
def _validator():
    from jsonschema import Draft202012Validator

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


# ------------------------------------------------------------------ the ontology the processes read

class Ontology:
    """What a process refers to: types (with their attributes, inherited ones too) and relations."""

    def __init__(self, model: Mapping[str, Any]):
        onto = model.get("ontology") or {}
        self.types = {t["key"]: t for t in onto.get("types") or [] if t.get("key")}
        self.relations = {r["key"]: r for r in onto.get("relations") or [] if r.get("key")}
        self.common = list(onto.get("common_attributes") or [])

    def live_type(self, key: str) -> bool:
        return key in self.types and not self.types[key].get("retired")

    def live_relation(self, key: str) -> Optional[dict]:
        rel = self.relations.get(key)
        return rel if rel is not None and not rel.get("retired") else None

    def attributes(self, key: str) -> list[dict]:
        """The type's attributes, its ancestors' and the common ones; the nearest wins a name."""
        seen: dict[str, dict] = {}
        current, guard = key, set()
        while current in self.types and current not in guard:
            guard.add(current)
            for a in self.types[current].get("attributes") or []:
                seen.setdefault(a.get("name"), a)
            current = self.types[current].get("parent")
        for a in self.common:
            seen.setdefault(a.get("name"), a)
        return [a for name, a in seen.items() if name]

    def attribute(self, key: str, name: str) -> Optional[dict]:
        return next((a for a in self.attributes(key) if a.get("name") == name), None)

    def fits(self, relation: Mapping[str, Any], frm: str, to: str, direction: str) -> bool:
        dom, rng = relation.get("domain") or [], relation.get("range") or []
        if direction != "out":
            dom, rng = rng, dom
        return (WILDCARD in dom or frm in dom) and (WILDCARD in rng or to in rng)


def path_problem(onto: Ontology, start_types: Iterable[str], path: list[dict]) -> Optional[str]:
    """Why ``path`` is not a path from one of ``start_types``, or None (validate_definitions.check_path)."""
    if not path:
        return "Der Pfad hat keinen Schritt."
    starts = list(start_types)
    for i, hop in enumerate(path, start=1):
        if onto.live_relation(hop["relation"]) is None:
            return f"Schritt {i} des Pfads: die Beziehung {hop['relation']} gibt es nicht."
        if hop["direction"] not in DIRECTIONS:
            return f"Schritt {i} des Pfads: die Richtung {hop['direction']} gibt es nicht."
        if not onto.live_type(hop["to_type"]):
            return f"Schritt {i} des Pfads: den Typ {hop['to_type']} gibt es nicht."
    for start in starts:
        current = start
        for hop in path:
            if not onto.fits(onto.relations[hop["relation"]], current, hop["to_type"], hop["direction"]):
                break
            current = hop["to_type"]
        else:
            return None
    shown = " / ".join(f"{h['relation']} {'→' if h['direction'] == 'out' else '←'} {h['to_type']}" for h in path)
    return f"Der Pfad {shown} passt für {', '.join(starts) or 'keinen Instanztyp'} nicht zu den Beziehungen."


# ------------------------------------------------------------------ checks

def _problem(found: list, severity: str, kind: str, process: Any, message: str, *,
             step: Optional[str] = None, transition: Optional[str] = None) -> None:
    pid = f"{process}:{kind}:{step or transition or ''}:{len(found)}"
    found.append({"id": pid, "severity": severity, "kind": kind, "process": process, "step": step,
                  "transition": transition, "message": message})


def _schema(doc: Mapping[str, Any], found: list) -> None:
    normal = normalise(doc)
    for error in sorted(_validator().iter_errors(normal), key=lambda e: list(map(str, e.absolute_path))):
        parts = list(error.absolute_path)
        process, step = None, None
        if len(parts) >= 2 and parts[0] == "processes" and isinstance(parts[1], int):
            entry = normal["processes"][parts[1]]
            process = entry.get("key") if isinstance(entry, Mapping) else None
            if len(parts) >= 4 and parts[2] == "steps" and isinstance(parts[3], int):
                step = entry["steps"][parts[3]]["id"]
        where = "/".join(str(p) for p in parts) or "Prozesse"
        _problem(found, "critical", "schema", process, f"{where}: {error.message}", step=step)


def _cycle(steps: list[str], forward: list[tuple[str, str]]) -> Optional[list[str]]:
    graph = defaultdict(list)
    for a, b in forward:
        graph[a].append(b)
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(n: str) -> Optional[list[str]]:
        state[n] = 1
        stack.append(n)
        for m in graph[n]:
            if state.get(m) == 1:
                return stack[stack.index(m):] + [m]
            if state.get(m) is None:
                found = visit(m)
                if found:
                    return found
        state[n] = 2
        stack.pop()
        return None

    for s in steps:
        if state.get(s) is None:
            found = visit(s)
            if found:
                return found
    return None


def _check_process(p: Mapping[str, Any], onto: Ontology, rule_ids: set[str], found: list) -> None:
    k = p["key"]
    for t in p["instance_types"]:
        if not onto.live_type(t):
            _problem(found, "critical", "instance_type_unknown", k, f"Den Instanztyp {t} gibt es in der Ontologie nicht.")
    ids = [s["id"] for s in p["steps"]]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        _problem(found, "critical", "step_duplicate", k, f"Die Schritt-ID {dup} kommt mehrfach vor.", step=dup)
    order = {s["id"]: s["order"] for s in p["steps"]}
    for f in p["final"]:
        if f not in order:
            _problem(found, "critical", "final_unknown", k, f"Der Ruheschritt {f} ist kein Schritt.")
    forward, edges = [], defaultdict(list)
    for t in p["transitions"]:
        tid = f"{t['from']}>{t['to']}"
        if t["from"] not in order or t["to"] not in order:
            _problem(found, "critical", "transition_unknown", k,
                     f"Der Übergang {t['from']} → {t['to']} nennt einen unbekannten Schritt.", transition=tid)
            continue
        edges[t["from"]].append(t["to"])
        if t["rework"]:
            if order[t["to"]] >= order[t["from"]]:
                _problem(found, "critical", "rework_forward", k,
                         f"Die Nacharbeit {t['from']} → {t['to']} führt nicht zu einem früheren Schritt zurück.",
                         transition=tid)
        else:
            forward.append((t["from"], t["to"]))
    for a, b in forward:
        if (b, a) in forward and order[a] < order[b]:
            _problem(found, "critical", "transition_both_ways", k,
                     f"{a} → {b} und {b} → {a} gehen beide vorwärts; die Brain-Engine hält nur eine Kante je Paar. "
                     f"Markieren Sie den Weg zurück als Nacharbeit.", transition=f"{b}>{a}")
    cycle = _cycle(ids, forward)
    if cycle:
        _problem(found, "critical", "forward_cycle", k,
                 f"Die Vorwärts-Übergänge bilden einen Kreis: {' → '.join(cycle)}. Markieren Sie den Weg zurück als Nacharbeit.")
    if ids:
        first = min(p["steps"], key=lambda s: s["order"])["id"]
        reached, todo = {first}, [first]
        while todo:
            for nxt in edges[todo.pop()]:
                if nxt not in reached:
                    reached.add(nxt)
                    todo.append(nxt)
        for s in ids:
            if s not in reached:
                _problem(found, "critical", "step_unreachable", k,
                         f"Kein Übergang führt vom ersten Schritt {first} zu {s}.", step=s)
    resting = {s["id"] for s in p["steps"] if s["resting"]}
    for s in p["steps"]:
        if s["id"] not in resting and not edges[s["id"]] and len(ids) > 1:
            _problem(found, "warning", "step_dead_end", k,
                     f"Aus {s['id']} führt kein Übergang hinaus, und der Schritt ist kein Ruheschritt.", step=s["id"])
    gf = p["governing_fact"]
    for t in p["instance_types"]:
        if not onto.live_type(t):
            continue
        m = p["status_map"].get(t)
        if m is None:
            _problem(found, "critical", "status_map_missing", k,
                     f"Für {t} fehlt die Statuszuordnung (Wert von {gf} → Schritt).")
            continue
        attr = onto.attribute(t, gf)
        enum = (attr or {}).get("enum_values") if (attr or {}).get("datatype") == "enum" else None
        if not enum:
            _problem(found, "critical", "governing_fact_not_enum", k,
                     f"{t} hat kein Auswahl-Attribut {gf}; der Status braucht feste Werte.")
        else:
            missing = [v for v in enum if str(v) not in m]
            extra = [v for v in m if v not in {str(e) for e in enum}]
            if missing:
                _problem(found, "critical", "status_unmapped", k,
                         f"{t}.{gf}: {', '.join(map(str, missing))} ist keinem Schritt zugeordnet.")
            if extra:
                _problem(found, "critical", "status_unknown_value", k,
                         f"{t}.{gf}: {', '.join(extra)} ist kein Wert von {gf}.")
        for v, step in m.items():
            if step is not None and step not in order:
                _problem(found, "critical", "status_unknown_step", k, f"{t}.{gf} = {v} nennt den unbekannten Schritt {step}.")
    cc = p["change_classes"]
    for f in sorted(set(cc["minor"]) & set(cc["substantive"])):
        _problem(found, "critical", "change_class_both", k, f"Die Änderungsklasse {f} ist zugleich klein und wesentlich.")
    trig = cc["on_substantive"]
    if trig and not any(t["rework"] and t["from"] == trig["from"] and t["to"] == trig["to"] for t in p["transitions"]):
        _problem(found, "critical", "on_substantive_unknown", k,
                 f"Eine wesentliche Änderung führt {trig['from']} → {trig['to']} zurück, aber das ist keine Nacharbeit des Prozesses.")
    for s in p["steps"]:
        for c in s["exit_criteria"]:
            if c not in R_RULES and c not in rule_ids:
                _problem(found, "critical", "criterion_unknown", k,
                         f"Das Abschlusskriterium {c} ist weder R0–R8 noch eine Prozessregel.", step=s["id"])
    starts = [t for t in p["instance_types"] if onto.live_type(t)]
    artefacts = [(None, "Kontext", a) for a in p["context"]] + [
        (s["id"], "braucht" if kind == "requires" else "liefert", a)
        for s in p["steps"] for kind in ("requires", "produces") for a in s[kind]]
    for step, kind, a in artefacts:
        paths = [("", a["path"])]
        if a.get("alternative"):
            paths.append((" (Alternative)", a["alternative"]["path"]))
        for label, path in paths:
            why = path_problem(onto, starts, path) if starts else None
            if why:
                _problem(found, "critical", "path_invalid", k, f"{kind} {a['type']}{label}: {why}", step=step)
        if a.get("where") and a["path"]:
            end = a["path"][-1]["to_type"]
            if onto.live_type(end) and onto.attribute(end, a["where"]["fact"]) is None:
                _problem(found, "critical", "where_unknown", k,
                         f"{kind} {a['type']}: {end} hat kein Attribut {a['where']['fact']}.", step=step)


def validate(model: Mapping[str, Any]) -> list[dict]:
    """Every problem of the workspace's processes, critical ones first."""
    doc = document(model)
    found: list[dict] = []
    if not doc:
        return found
    _schema(doc, found)
    if found:   # the further checks read the structure the schema guarantees
        return found
    normal = normalise(doc)
    onto = Ontology(model)
    rule_ids = {r["id"] for r in normal["rules"]}
    keys = [p["key"] for p in normal["processes"]]
    for dup in sorted({k for k in keys if keys.count(k) > 1}):
        _problem(found, "critical", "process_duplicate", dup, f"Den Prozess {dup} gibt es mehrfach.")
    for p in normal["processes"]:
        _check_process(p, onto, rule_ids, found)
    # A status value two processes map for the same type and fact: an instance would sit in both.
    claimed: dict[tuple[str, str, str], list[tuple[str, str]]] = defaultdict(list)
    for p in normal["processes"]:
        for t, m in p["status_map"].items():
            for v, step in m.items():
                if step is not None:
                    claimed[(t, p["governing_fact"], v)].append((p["key"], step))
    for (t, gf, v), owners in sorted(claimed.items()):
        if len({o for o, _ in owners}) > 1:
            text = " und ".join(f"in {o} den Schritt {s}" for o, s in owners)
            for o, s in owners:
                _problem(found, "critical", "status_mapped_twice", o,
                         f"{t}.{gf} = {v} ist doppelt zugeordnet: {text}.", step=s)
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(found, key=lambda f: order[f["severity"]])


# ------------------------------------------------------------------ the view

def build(model: Mapping[str, Any]) -> dict:
    """What the process canvas draws: the processes, their problems and the ontology they may name."""
    doc = document(model)
    normal = normalise(doc) if doc else {"rules": [], "report_rules": [], "processes": []}
    problems = validate(model)
    onto = Ontology(model)
    processes = []
    for p in normal["processes"]:
        if not isinstance(p, Mapping):
            continue
        mine = [f for f in problems if f["process"] == p.get("key")]
        p = dict(p)
        p["problems"] = {s: sum(1 for f in mine if f["severity"] == s) for s in ("critical", "warning")}
        processes.append(p)
    report = {r.get("id"): r for r in normal["report_rules"]}
    rules = [{"id": r, "name": (report.get(r) or {}).get("name") or f"Regel {r}",
              "description": (report.get(r) or {}).get("description") or "", "kind": "report"} for r in R_RULES]
    rules += [{"id": r.get("id"), "name": r.get("name") or r.get("id"), "description": r.get("description") or "",
               "check": r.get("check"), "fact": r.get("fact"), "facts": r.get("facts"), "kind": "process"}
              for r in normal["rules"]]
    types = []
    for key in sorted(onto.types):
        t = onto.types[key]
        types.append({"key": key, "name": (t.get("label") or {}).get("de") or key, "retired": bool(t.get("retired")),
                      "attributes": [{"name": a["name"], "datatype": a.get("datatype") or "text",
                                      "enum_values": [str(v) for v in a.get("enum_values") or []]}
                                     for a in onto.attributes(key)]})
    relations = [{"key": r["key"], "domain": list(r.get("domain") or []), "range": list(r.get("range") or []),
                  "label": (r.get("label") or {}).get("de") or ""}
                 for r in sorted(onto.relations.values(), key=lambda r: r["key"]) if not r.get("retired")]
    roles = sorted({str(x) for p in processes for x in [p.get("owner_role")] + [s.get("owner_role") for s in p["steps"]] if x})
    return {
        "available": "processes" in ((model.get("package") or {}).get("manifest") or {}),
        "processes": processes,
        "problems": problems,
        "rules": rules,
        "checks": CHECKS,
        "types": types,
        "relations": relations,
        "roles": roles,
        "counts": {"processes": len(processes), "steps": sum(len(p["steps"]) for p in processes),
                   "problems": {s: sum(1 for f in problems if f["severity"] == s) for s in ("critical", "warning")}},
    }
