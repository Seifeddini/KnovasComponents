"""Operating a published Brain (build plan step B10).

Everything here reads; nothing writes to the tenant. The inputs:

- the workspace model (the health section, pages, spines, mappings, sources);
- what the tenant holds for the types the screen looks at, read by the engine
  with brain-runner's snapshot (``Engine.read``): nodes with their facts valid
  now, and the edges between them;
- the sync's state, runs and evaluation (``brain_sync``), when the Platform
  syncs: each mapping's conditions (Ready, Stale, Degraded), observed counts
  and the plans the deletion guard held;
- the builder's own runs (dry runs and publishes) and the health reviews.

Health is three numbers per type of the health section (DC-34):
correctness -- the share of nodes confirmed within ``stale_after``;
completeness -- the share carrying every ``required`` field; compliance --
the share of the evaluable rule checks of ``compliance`` that pass. Each with
the owner role and the next review (the last review, else the last publish,
plus ``review_every``).

The tidiness list names what to tidy and where it is fixed: types and
attributes without a description, empty required page slots, orphan nodes,
duplicates (the same key, or near-identical names), knowledge on the wrong
level of a spine, stale claims. The coverage matrix puts a spine's levels down
and the page slots of each level's type across.

Logs carry counts only (GI-DATA-02/04); node names and values go to the page.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections import defaultdict
from typing import Any, Iterable, Mapping, Optional

from builder import processes as P
from builder import views as V

#: Lint kinds, in the order the screen lists them.
LINT_KINDS = {
    "type_undescribed": "Typ ohne Beschreibung",
    "attribute_undescribed": "Attribut ohne Beschreibung",
    "slot_empty": "Leerer Pflichtabschnitt",
    "orphan": "Knoten ohne Verbindung",
    "duplicate": "Doppelt",
    "wrong_level": "Wissen auf der falschen Ebene",
    "stale_claim": "Veraltete Angabe",
}
#: How many items of one kind the screen lists (the count says how many there are).
LINT_LIMIT = 50
DURATION = re.compile(r"^\s*(\d+)\s*(h|d|w)\s*$")


# ------------------------------------------------------------------ the tenant's graph

def duration(text: Any) -> Optional[dt.timedelta]:
    """"14 d" -> 14 days (brain-runner's freshness format); None for anything else."""
    m = DURATION.match(str(text or ""))
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return {"h": dt.timedelta(hours=n), "d": dt.timedelta(days=n), "w": dt.timedelta(weeks=n)}[unit]


def moment(text: Any) -> Optional[dt.datetime]:
    if not text:
        return None
    try:
        t = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        try:
            t = dt.datetime.fromisoformat(str(text)[:10])
        except ValueError:
            return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


class Graph:
    """The tenant's nodes and edges as ``Engine.read`` gives them, indexed."""

    def __init__(self, snapshot: Mapping[str, Any]):
        self.taken_at = snapshot.get("taken_at")
        self.nodes = {n["id"]: n for n in snapshot.get("nodes") or []}
        self.edges = list(snapshot.get("edges") or [])
        self.by_type: dict[str, list[dict]] = defaultdict(list)
        for n in self.nodes.values():
            self.by_type[n["type"]].append(n)
        self.out: dict[str, list[dict]] = defaultdict(list)
        self.into: dict[str, list[dict]] = defaultdict(list)
        for e in self.edges:
            self.out[e["lo"]].append(e)
            self.into[e["hi"]].append(e)

    @staticmethod
    def value(node: Mapping[str, Any], fact: str) -> Optional[str]:
        row = (node.get("facts") or {}).get(fact)
        if row is None:
            return None
        value = row.get("value") if isinstance(row, Mapping) else row
        return None if value in (None, "") else str(value)

    @staticmethod
    def key(node: Mapping[str, Any]) -> str:
        return Graph.value(node, "key") or node.get("name") or node["id"]

    @staticmethod
    def seen(node: Mapping[str, Any]) -> Optional[dt.datetime]:
        """When the node was last confirmed: its newest fact."""
        times = [moment(r.get("valid_from") or r.get("created_at")) for r in (node.get("facts") or {}).values()
                 if isinstance(r, Mapping)]
        times = [t for t in times if t is not None]
        return max(times) if times else moment(node.get("updated_at") or node.get("created_at"))

    def has(self, node: Mapping[str, Any], field: str) -> bool:
        """Whether the node carries ``field``: its name or description, a fact, or an edge of that name."""
        if field == "name":
            return bool(str(node.get("name") or "").strip())
        if field == "description":
            return bool(str(node.get("description") or "").strip()) or self.value(node, "description") is not None
        if self.value(node, field) is not None:
            return True
        relations = {e["relation"] for e in self.out.get(node["id"], [])}
        return field in relations or (field == "owner" and "owned_by" in relations)

    def follow(self, start: str, path: list[dict]) -> list[str]:
        frontier = [start]
        for hop in path:
            nxt = []
            for n in frontier:
                edges = self.out.get(n, []) if hop["direction"] == "out" else self.into.get(n, [])
                for e in edges:
                    other = e["hi"] if hop["direction"] == "out" else e["lo"]
                    if e["relation"] == hop["relation"] and (self.nodes.get(other) or {}).get("type") == hop["to_type"]:
                        nxt.append(other)
            frontier = list(dict.fromkeys(nxt))
        return frontier


def read_types(model: Mapping[str, Any]) -> set[str]:
    """The types the screen reads from the tenant: health, pages, spine levels, and the run records."""
    types = set(((model.get("health") or {}).get("types") or {}))
    types |= {str(p.get("type")) for p in V.pages_of(model)}
    for s in V.spines_of(model):
        for _, levels in V.chains(s):
            for lv in levels:
                types |= {str(t) for t in lv.get("types") or []}
    types.add("RunEvent")
    return types


# ------------------------------------------------------------------ health

def _rule_checks(model: Mapping[str, Any]) -> dict[str, dict]:
    """The process rules a node can be checked against here (fact_present, facts_present, date_not_past)."""
    doc = P.document(model)
    return {str(r.get("id")): dict(r) for r in doc.get("rules") or []
            if isinstance(r, Mapping) and r.get("check") in P.CHECKS}


def _passes(graph: Graph, node: Mapping[str, Any], rule: Mapping[str, Any], today: dt.date) -> bool:
    check = rule.get("check")
    if check == "fact_present":
        return graph.has(node, str(rule.get("fact")))
    if check == "facts_present":
        return all(graph.has(node, str(f)) for f in rule.get("facts") or [])
    if check == "date_not_past":
        when = moment(graph.value(node, str(rule.get("fact"))))
        return when is None or when.date() >= today
    return False


def _evaluated(evaluation: Optional[Mapping[str, Any]]) -> dict[tuple[str, str], bool]:
    """(node key, rule) -> passed, from the sync's evaluation (brain-runner evaluate: rule_results per instance)."""
    out: dict[tuple[str, str], bool] = {}
    for inst in (evaluation or {}).get("instances") or []:
        if not isinstance(inst, Mapping):
            continue
        key = str(inst.get("key") or inst.get("instance") or "")
        for rule, result in (inst.get("rule_results") or {}).items():
            passed = result.get("passed") if isinstance(result, Mapping) else result
            if isinstance(passed, bool):
                out[(key, str(rule))] = passed
    return out


def _share(part: int, whole: int) -> Optional[int]:
    return round(100 * part / whole) if whole else None


def health(model: Mapping[str, Any], graph: Graph, *, now: dt.datetime, reviews: Mapping[str, Mapping[str, Any]],
           published_at: Optional[str], evaluation: Optional[Mapping[str, Any]] = None) -> list[dict]:
    """Per type of the health section: the three numbers, the owner role and the next review."""
    checks = _rule_checks(model)
    evaluated = _evaluated(evaluation)
    out = []
    for type_key, spec in ((model.get("health") or {}).get("types") or {}).items():
        spec = spec or {}
        nodes = graph.by_type.get(type_key, [])
        required = [str(f) for f in spec.get("required") or []]
        window = duration(spec.get("stale_after"))
        complete = sum(1 for n in nodes if all(graph.has(n, f) for f in required))
        fresh = sum(1 for n in nodes if window is None or ((graph.seen(n) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
                                                             >= now - window))
        passed = checked = 0
        rules = [str(r) for r in spec.get("compliance") or []]
        unevaluable = set()
        for n in nodes:
            for r in rules:
                if r in checks:
                    checked += 1
                    passed += _passes(graph, n, checks[r], now.date())
                elif (graph.key(n), r) in evaluated:
                    checked += 1
                    passed += evaluated[(graph.key(n), r)]
                else:
                    unevaluable.add(r)
        reviewed = reviews.get(type_key) or {}
        base = moment(reviewed.get("at")) or moment(spec.get("last_review")) or moment(published_at)
        every = duration(spec.get("review_every"))
        due = (base + every) if base and every else None
        out.append({
            "type": type_key, "nodes": len(nodes), "owner_role": spec.get("owner_role"),
            "review_every": spec.get("review_every"), "reviewed_at": reviewed.get("at"),
            "reviewed_by": reviewed.get("actor_name"), "due": due.isoformat() if due else None,
            "overdue": bool(due and due < now),
            "correctness": _share(fresh, len(nodes)), "completeness": _share(complete, len(nodes)),
            "compliance": _share(passed, checked),
            "detail": {"fresh": fresh, "complete": complete, "required": required, "stale_after": spec.get("stale_after"),
                       "checks": checked, "passed": passed, "rules": rules, "unevaluable": sorted(unevaluable)},
        })
    return out


# ------------------------------------------------------------------ mappings and sources

_ORDER = {"Degraded": 0, "Stale": 1, "Ready": 2, "Unbekannt": 3}


def condition_of(mstate: Optional[Mapping[str, Any]]) -> tuple[str, str]:
    """(Ready | Stale | Degraded | Unbekannt, the reason) from a mapping's state."""
    conds = (mstate or {}).get("conditions") or {}
    for name in ("Degraded", "Stale"):
        c = conds.get(name) or {}
        if c.get("status") == "True":
            return name, str(c.get("message") or c.get("reason") or "")
    ready = conds.get("Ready") or {}
    if ready.get("status") == "True":
        return "Ready", str(ready.get("message") or "")
    return "Unbekannt", "Noch kein Lauf." if not conds else str(ready.get("message") or "")


def mappings_status(model: Mapping[str, Any], *, state: Optional[Mapping[str, Any]], sync_runs: Iterable[Mapping[str, Any]],
                    builder_runs: Iterable[Mapping[str, Any]]) -> dict:
    """Each mapping's condition, last run, counts, errors and held plan; each source's worst condition."""
    states = (state or {}).get("mappings") or {}
    runs = list(sync_runs)
    # The newest probe or apply that planned a mapping (runs come newest first): its counts, and whether the
    # guard held it. A held probe stays held until a newer run plans the mapping again.
    planned: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for run in runs:
        for mid, counts in ((run.get("counts") or {}) if isinstance(run.get("counts"), Mapping) else {}).items():
            planned.setdefault(mid, (run, counts))
    builder_dry = {}
    for run in builder_runs:
        mid = (run.get("summary") or {}).get("mapping")
        if run.get("kind") == "dry_run" and mid and mid not in builder_dry:
            builder_dry[mid] = run
    rows = []
    for entry in model.get("mappings") or []:
        d = entry.get("definition") or {}
        mid = str(d.get("id"))
        ms = states.get(mid) or {}
        cond, reason = condition_of(ms)
        observed = ms.get("observed") or {}
        run, counts = planned.get(mid, ({}, {}))
        dry = builder_dry.get(mid)
        held = None
        if counts.get("blocked") and run.get("kind") == "probe":
            held = {"at": run.get("finished_at") or run.get("started_at"), "by": run.get("started_by_name") or run.get("started_by"),
                    "why": "Löschschutz" if counts.get("guard_tripped") else "Quelle nicht lesbar",
                    "retire": counts.get("retire", 0), "source": "sync"}
        elif dry is not None and (dry.get("summary") or {}).get("guard_tripped"):
            held = {"at": dry.get("at"), "by": dry.get("actor_name"), "why": "Löschschutz",
                    "retire": (dry.get("summary") or {}).get("retired", 0), "source": "builder"}
        errors = []
        degraded = (ms.get("conditions") or {}).get("Degraded") or {}
        if degraded.get("status") == "True":
            errors.append(str(degraded.get("message") or degraded.get("reason") or "Degraded"))
        if run.get("status") == "failed":
            errors.append(str(run.get("result") or "Lauf fehlgeschlagen")[:200])
        rows.append({"id": mid, "types": [str(e.get("type")) for e in d.get("emits") or []],
                     "source": (entry.get("builder") or {}).get("source"),
                     "condition": cond, "reason": reason,
                     "last_run_at": ms.get("last_run_at") or run.get("finished_at") or (dry or {}).get("at"),
                     "last_success_at": ms.get("last_success_at"),
                     "counts": {"added": observed.get("added", counts.get("add", 0)),
                                "modified": observed.get("modified", counts.get("change", 0)),
                                "retired": observed.get("retired", counts.get("retire", 0)),
                                "drift": observed.get("drift", counts.get("drift", 0)),
                                "in_brain": observed.get("nodes_in_brain"), "source_items": observed.get("source_items",
                                                                                                         counts.get("source_items"))},
                     "errors": errors, "held": held, "approvals": len(ms.get("guard_approvals") or [])})
    rows.sort(key=lambda r: (_ORDER.get(r["condition"], 9), r["id"]))
    sources = []
    for s in model.get("sources") or []:
        mine = [r for r in rows if r["source"] == s.get("key")]
        worst = min((r["condition"] for r in mine), key=lambda c: _ORDER.get(c, 9), default="Unbekannt")
        sources.append({"key": s.get("key"), "name": s.get("name"), "kind": s.get("kind"), "condition": worst,
                        "mappings": [r["id"] for r in mine], "held": sum(1 for r in mine if r["held"])})
    counts = {c: sum(1 for r in rows if r["condition"] == c) for c in ("Ready", "Stale", "Degraded", "Unbekannt")}
    return {"mappings": rows, "sources": sources, "counts": counts, "held": [r for r in rows if r["held"]]}


# ------------------------------------------------------------------ run history

def history(builder_runs: Iterable[Mapping[str, Any]], sync_runs: Iterable[Mapping[str, Any]], graph: Graph,
            limit: int = 60) -> list[dict]:
    """Every publish and every sync, newest first: who, when, what changed. Run records on the tenant join in."""
    out = []
    for r in builder_runs:
        s = r.get("summary") or {}
        if r.get("kind") == "apply":
            what = (f"Version {s['published_number']} veröffentlicht" if s.get("published_number") else "Veröffentlichen")
            changed = f"{s.get('written', 0)} geschrieben, {len(s.get('refused') or [])} abgelehnt"
        else:
            what = f"Probelauf der Zuordnung {s['mapping']}" if s.get("mapping") else "Probelauf"
            counts = s.get("counts") or {k: s.get(k) for k in ("added", "modified", "retired") if k in s}
            changed = ", ".join(f"{k} {v}" for k, v in counts.items() if v not in (None, 0)) or "keine Änderung"
        out.append({"at": r.get("at"), "who": r.get("actor_name") or "–", "kind": "publish" if r.get("kind") == "apply" else "dry_run",
                    "what": what, "changed": changed, "status": r.get("status")})
    for r in sync_runs:
        counts = r.get("counts") if isinstance(r.get("counts"), Mapping) else {}
        totals = defaultdict(int)
        for c in counts.values():
            if isinstance(c, Mapping):
                for k in ("add", "change", "retire"):
                    totals[k] += int(c.get(k) or 0)
        changed = f"+{totals['add']} ~{totals['change']} −{totals['retire']} in {len(counts)} Zuordnungen" if counts else "–"
        out.append({"at": r.get("finished_at") or r.get("started_at"), "who": r.get("started_by_name") or r.get("started_by") or "Zeitplan",
                    "kind": "sync_" + str(r.get("kind")), "what": "Synchronisation: " + ("Probelauf" if r.get("kind") == "probe" else "angewendet"),
                    "changed": changed, "status": r.get("status"), "detail": str(r.get("result") or "")[:200]})
    for n in graph.by_type.get("RunEvent", []):
        out.append({"at": Graph.value(n, "occurred_at") or (Graph.seen(n).isoformat() if Graph.seen(n) else None),
                    "who": Graph.value(n, "actor") or "–", "kind": "run_event",
                    "what": f"Laufbericht {Graph.key(n)}", "changed": Graph.value(n, "observed") or "–",
                    "status": {"Ready": "ok", "Degraded": "failed"}.get(Graph.value(n, "condition") or "", Graph.value(n, "condition"))})
    out.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    return out[:limit]


# ------------------------------------------------------------------ tidiness

def _normal(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _near(a: str, b: str) -> bool:
    """Names that are one and the same once spelling is set aside: equal after normalising case, accents and
    punctuation, or one typo apart (Meier, Meyer) -- never a series that differs in a number (Release 1, 2)."""
    if a == b:
        return True
    if min(len(a), len(b)) < 6 or abs(len(a) - len(b)) > 1:
        return False
    if re.sub(r"\d+", "#", a) == re.sub(r"\d+", "#", b):
        return False
    if len(a) == len(b):
        diffs = [(x, y) for x, y in zip(a, b) if x != y]
        return len(diffs) == 1 and not (diffs[0][0].isdigit() or diffs[0][1].isdigit())
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _described(item: Mapping[str, Any]) -> bool:
    d = item.get("description") or {}
    text = " ".join(str(v) for v in d.values()) if isinstance(d, Mapping) else str(d)
    return bool(re.sub(r"^\s*\[[^\]]*\]\s*", "", text).strip())


def lint(model: Mapping[str, Any], graph: Graph, *, now: dt.datetime, links: Mapping[str, Any]) -> dict:
    """The tidiness list: {kind: {label, count, items}}; each item says where it is fixed (``links``).

    ``links`` gives the URL of an editor: ``type(key)``, ``page(key)``, ``spine(key)``, ``mapping(id)`` (the
    mapping that emits a type, None when none does) -- the screen links each item there.
    """
    found: dict[str, list[dict]] = {k: [] for k in LINT_KINDS}

    def add(kind: str, target: str, message: str, url: Optional[str], where: str) -> None:
        found[kind].append({"target": target, "message": message, "url": url, "where": where})

    def data_fix(type_key: str) -> tuple[Optional[str], str]:
        url = links["mapping"](type_key)
        return (url, "Zuordnung") if url else (links["type"](type_key), "Ontologie")

    onto = model.get("ontology") or {}
    for t in onto.get("types") or []:
        if t.get("retired") or t.get("level") == "core":
            continue
        if not _described(t):
            add("type_undescribed", t["key"], f"{t['key']} hat keine Beschreibung.", links["type"](t["key"]), "Ontologie")
        for a in t.get("attributes") or []:
            if a.get("level", t.get("level")) == "company" and not _described(a):
                add("attribute_undescribed", f"{t['key']}.{a['name']}", f"{t['key']}.{a['name']} hat keine Beschreibung.",
                    links["type"](t["key"]), "Ontologie")

    # Empty required slots: per page, the nodes of its type whose required slot has nothing.
    for page in V.pages_of(model):
        t = str(page.get("type"))
        prefix = str((page.get("filter") or {}).get("key_prefix") or "")
        nodes = [n for n in graph.by_type.get(t, []) if not prefix or Graph.key(n).startswith(prefix)]
        for s in page.get("sections") or []:
            if not s.get("required"):
                continue
            empty = [n for n in nodes if not _slot_filled(graph, n, s)]
            for n in empty:
                add("slot_empty", Graph.key(n), f"{Graph.key(n)}: «{(s.get('title') or {}).get('de') or s.get('id')}» "
                    f"auf der Seite {page.get('key')} ist leer.", links["page"](page.get("key")), "Seite")

    # Orphans: nodes of the read types with no edge at all (the run records excepted).
    for t, nodes in sorted(graph.by_type.items()):
        if t == "RunEvent":
            continue
        for n in nodes:
            if not graph.out.get(n["id"]) and not graph.into.get(n["id"]):
                url, where = data_fix(t)
                add("orphan", Graph.key(n), f"{Graph.key(n)} ({t}) hängt an nichts.", url, where)

    # Duplicates: the same key twice in a type, or names that differ only in spelling.
    for t, nodes in sorted(graph.by_type.items()):
        if t == "RunEvent":
            continue
        url, where = data_fix(t)
        keys = defaultdict(list)
        for n in nodes:
            keys[Graph.key(n)].append(n)
        for key, same in keys.items():
            if len(same) > 1:
                add("duplicate", key, f"{t} {key} gibt es {len(same)}-mal.", url, where)
        names = sorted(((_normal(n.get("name") or ""), Graph.key(n)) for n in nodes if n.get("name")), key=lambda x: x[0])
        for (a, ka), (b, kb) in zip(names, names[1:]):
            if ka != kb and a and _near(a, b):
                add("duplicate", f"{ka} / {kb}", f"{t} {ka} und {kb} heißen fast gleich.", url, where)

    # Knowledge on the wrong level: a node of a spine level linked upward to a node not of the level above.
    for s in V.spines_of(model):
        for branch, levels in V.chains(s):
            for i, lv in enumerate(levels[1:], start=1):
                parents = {str(x) for x in levels[i - 1].get("types") or []}
                prefix = str((lv.get("filter") or {}).get("key_prefix") or "")
                parent_prefix = str((levels[i - 1].get("filter") or {}).get("key_prefix") or "")
                for t in lv.get("types") or []:
                    link = V.link_for(lv, str(t))
                    if not link:
                        continue
                    for n in graph.by_type.get(str(t), []):
                        if prefix and not Graph.key(n).startswith(prefix):
                            continue        # a node of another level of the same type (the key prefix says which)
                        edges = graph.out.get(n["id"], []) if link.get("direction") == "out" else graph.into.get(n["id"], [])
                        ends = [e["hi"] if link.get("direction") == "out" else e["lo"] for e in edges
                                if e["relation"] == link.get("relation")]
                        wrong = [graph.nodes[x] for x in ends if x in graph.nodes
                                 and (graph.nodes[x]["type"] not in parents
                                      or (parent_prefix and not Graph.key(graph.nodes[x]).startswith(parent_prefix)))]
                        for w in wrong:
                            add("wrong_level", Graph.key(n),
                                f"{Graph.key(n)} ({t}, Ebene {lv.get('name')}) hängt über {link.get('relation')} an "
                                f"{Graph.key(w)} ({w['type']}); darüber erwartet: {', '.join(sorted(parents))}.",
                                links["spine"](s.get("key")), "Hierarchie")

    # Stale claims: facts older than the type's stale_after.
    for type_key, spec in ((model.get("health") or {}).get("types") or {}).items():
        window = duration((spec or {}).get("stale_after"))
        if window is None:
            continue
        for n in graph.by_type.get(type_key, []):
            old = sorted(name for name, row in (n.get("facts") or {}).items()
                         if isinstance(row, Mapping) and name != "key"
                         and (moment(row.get("valid_from") or row.get("created_at")) or now) < now - window)
            if old:
                url, where = data_fix(type_key)
                add("stale_claim", Graph.key(n), f"{Graph.key(n)}: {', '.join(old[:4])}{' …' if len(old) > 4 else ''} "
                    f"älter als {spec.get('stale_after')}.", url, where)

    return {kind: {"label": label, "count": len(found[kind]), "items": found[kind][:LINT_LIMIT]}
            for kind, label in LINT_KINDS.items()}


def _slot_filled(graph: Graph, node: Mapping[str, Any], section: Mapping[str, Any]) -> bool:
    if section.get("field"):
        return graph.has(node, str(section["field"]))
    if section.get("facts"):
        return any(graph.value(node, str(f)) is not None for f in section["facts"])
    for sp in section.get("paths") or []:
        hops = [{"relation": str(h.get("relation")), "direction": str(h.get("direction")), "to_type": str(h.get("to_type"))}
                for h in sp.get("path") or []]
        if graph.follow(node["id"], hops):
            return True
    return False


# ------------------------------------------------------------------ coverage

def coverage(model: Mapping[str, Any], graph: Graph, spine_key: Optional[str], *, now: dt.datetime) -> Optional[dict]:
    """A spine's levels down, the page slots of each level's type across: filled, missing or stale per cell."""
    spines = V.spines_of(model)
    spine = next((s for s in spines if s.get("key") == spine_key), spines[0] if spines else None)
    if spine is None:
        return None
    health_spec = (model.get("health") or {}).get("types") or {}
    pages = V.pages_of(model)
    columns: list[str] = []
    rows = []
    for lv in spine.get("levels") or []:
        types = [str(t) for t in lv.get("types") or []]
        prefix = str((lv.get("filter") or {}).get("key_prefix") or "")
        nodes = [n for t in types for n in graph.by_type.get(t, []) if not prefix or Graph.key(n).startswith(prefix)]
        page = next((p for p in pages if str(p.get("type")) in types
                     and (not (p.get("filter") or {}).get("key_prefix") or (p.get("filter") or {}).get("key_prefix") == prefix)), None)
        cells = {}
        for s in (page or {}).get("sections") or []:
            title = (s.get("title") or {}).get("de") or s.get("id")
            if title not in columns:
                columns.append(title)
            filled = [n for n in nodes if _slot_filled(graph, n, s)]
            stale = 0
            for n in filled:
                window = duration((health_spec.get(n["type"]) or {}).get("stale_after"))
                seen = graph.seen(n)
                if window and seen and seen < now - window:
                    stale += 1
            if not nodes:
                state = "empty"
            elif len(filled) < len(nodes):
                state = "missing"
            elif stale:
                state = "stale"
            else:
                state = "filled"
            cells[title] = {"state": state, "filled": len(filled), "nodes": len(nodes), "stale": stale,
                            "required": bool(s.get("required"))}
        rows.append({"level": lv.get("id"), "name": lv.get("name"), "types": types, "nodes": len(nodes),
                     "page": (page or {}).get("key"), "cells": cells})
    return {"spine": spine.get("key"), "name": spine.get("name"), "spines": [{"key": s.get("key"), "name": s.get("name")} for s in spines],
            "columns": columns, "rows": rows}
