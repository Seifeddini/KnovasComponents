"""Mapping a source to the ontology (build plan step B8).

A mapping is brain-runner's: the YAML of a company package's mappings/*.yaml
(schema brain_runner/schemas/mapping.schema.json) -- a source (extractor, path
or glob, ``select``, ``as``), freshness, deletion guard, edit policy, and what
it emits per item: a type, the identify rule and key, name, facts and
relations, each an expression (JMESPath with brain-runner's functions). The
workspace keeps that definition as it is written; beside it, under
``builder``, what only the canvas needs: the workspace source it reads, the
collection, and the steps each expression was built from.

Steps are small named transformations of one field -- trim, split, replace,
map values, date, join -- that ``compile_steps`` turns into the expression;
an expert edits the expression itself, and the steps are then gone for that
target.

A mapping's source is read through the workspace source's component: the
GitHub runner runs brain-runner's own ``run_mapping`` over the repository; a
KnovasConnector or HTTP/JSON source hands over its items as documents, and
the same ``_emit`` of brain-runner makes the candidates. ``preview`` shows the
first of them; ``plan_summary`` reads brain-runner's plan (``diff.plan``)
for counts, drift and the deletion guard.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from functools import lru_cache
from typing import Any, Iterable, Mapping, Optional

from builder.processes import Ontology

VAR = "f"
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAPPING_ID = re.compile(r"^[a-z][a-z0-9-]{1,59}$")
EDIT_POLICIES = {"source_owned": "Die Quelle bestimmt", "brain_owned": "Im Brain bearbeitbar",
                 "newest_wins": "Die neueste Änderung gilt"}
STEPS = {"trim": "Leerraum entfernen", "split": "Teilen", "replace": "Ersetzen", "map": "Werte umsetzen",
         "date": "Datum", "join": "Felder verbinden", "left": "Anfang"}
PREVIEW_LIMIT = 20
#: A KnovasConnector source is files under the connector's root: brain-runner's file_glob, as it runs on the
#: folder on site. An HTTP/JSON source is the response, read as brain-runner's json_file of the fetched document.
CONNECTOR_EXTRACTOR = "file_glob"
HTTP_EXTRACTOR = "json_file"
HTTP_DOCUMENT = "response.json"


class MappingError(ValueError):
    """A step or expression the mapping cannot take; the message is for the page."""


# ------------------------------------------------------------------ expressions

def _quote(segment: str) -> str:
    return segment if IDENT.match(segment) else json.dumps(segment, ensure_ascii=False)


def field_expr(field: str, var: str = VAR) -> str:
    """A field as an expression: dotted from the item (``f.data.status``) or its document (``doc.path``),
    as ``items`` names them; a name without either is read on the item."""
    parts = [p for p in str(field).split(".") if p != ""]
    if not parts:
        raise MappingError("Feld: bitte wählen.")
    if parts[0] not in (var, "doc"):
        parts = [var] + parts
    return ".".join([parts[0]] + [_quote(p) for p in parts[1:]])


def _lit(text: Any) -> str:
    return "'" + str(text).replace("\\", "\\\\").replace("'", "\\'") + "'"


def compile_steps(field: str, steps: Iterable[Mapping[str, Any]] = (), var: str = VAR) -> str:
    """The expression for ``field`` after ``steps``, in brain-runner's expression language."""
    expr = field_expr(field, var)
    for step in steps or []:
        kind = step.get("step")
        if kind == "trim":
            expr = f"trim({expr})"
        elif kind == "left":
            expr = f"left({expr}, `{int(step.get('n') or 1)}`)"
        elif kind == "split":
            sep, part = str(step.get("separator") or ""), int(step.get("part") or 0)
            if not sep:
                raise MappingError("Teilen: ein Trennzeichen angeben.")
            expr = f"split({expr}, {_lit(sep)})[{part}]"
        elif kind == "replace":
            expr = f"replace({expr}, {_lit(step.get('old') or '')}, {_lit(step.get('new') or '')})"
        elif kind == "map":
            values = step.get("values") or {}
            if not isinstance(values, Mapping) or not values:
                raise MappingError("Werte umsetzen: mindestens eine Zuordnung.")
            branches = " || ".join(f"({expr} == {_lit(k)} && {_lit(v)})" for k, v in values.items())
            expr = f"({branches} || {expr})" if step.get("keep", True) else f"({branches})"
        elif kind == "date":
            expr = f"left(str({expr}), `{4 if step.get('format') == 'year' else 10}`)"
        elif kind == "join":
            others = [field_expr(f, var) for f in step.get("fields") or []]
            if not others:
                raise MappingError("Felder verbinden: mindestens ein weiteres Feld.")
            expr = f"join({_lit(step.get('separator', ' '))}, [{', '.join(f'str({e})' for e in [expr] + others)}])"
        else:
            raise MappingError(f"Unbekannter Schritt «{kind}».")
    return expr


def expression_problem(expr: Any) -> Optional[str]:
    """Why brain-runner cannot read ``expr``, or None."""
    if not isinstance(expr, str) or not expr.strip():
        return "Der Ausdruck ist leer."
    try:
        from brain_runner.mapping import evaluate
    except ImportError:
        return None
    try:
        evaluate(expr, {})
    except Exception as exc:  # noqa: BLE001 - jmespath's parse and type errors
        name = type(exc).__name__
        if name in ("ParseError", "LexerError", "IncompleteExpressionError", "UnknownFunctionError", "ArityError"):
            return f"Der Ausdruck ist nicht lesbar ({name})."
    return None


def simple_field(expr: Any, var: str) -> Optional[str]:
    """The field a plain ``var.a.b`` (or ``doc.a``) expression reads, or None: how an imported expression
    shows as a chip."""
    if not isinstance(expr, str):
        return None
    for head in (var, "doc"):
        if expr.startswith(head + "."):
            rest = expr[len(head) + 1:]
            parts = re.findall(r'"(?:[^"\\]|\\.)*"|[A-Za-z_][A-Za-z0-9_]*', rest)
            if ".".join(parts) != rest:
                return None
            return ".".join([head] + [json.loads(p) if p.startswith('"') else p for p in parts])
    return None


# ------------------------------------------------------------------ definitions

def source_spec(source: Mapping[str, Any], collection: str) -> dict:
    """The mapping's ``source`` for a workspace source (B7) and one of its collections."""
    kind, settings = source.get("kind"), source.get("settings") or {}
    if kind == "github":
        from builder.sources import ReaderError, github_selection

        try:
            extractor, pattern = github_selection(collection)
        except ReaderError:
            raise MappingError("Sammlung: eine Auswahl aus dem Test der Quelle, oder ein Dateimuster im Repository.") from None
        return {"extractor": extractor, "glob": pattern, "select": "@", "as": VAR}
    if kind == "knovas_connector":
        folder = str(settings.get("folder") or "").strip("/")
        sub = str(collection).partition(":")[2] if str(collection).startswith("folder:") else ""
        base = "/".join(p for p in (folder, sub) if p)
        return {"extractor": CONNECTOR_EXTRACTOR, "glob": f"{base}/**/*" if base else "**/*", "select": "@", "as": VAR}
    if kind == "http_json":
        path = str(collection)
        select = "data" if path == "$" else "data." + path.removeprefix("$.")
        return {"extractor": HTTP_EXTRACTOR, "path": HTTP_DOCUMENT, "select": select, "as": VAR,
                "options": {"url": settings.get("url")}}
    raise MappingError("Diese Quelle kann noch nicht zugeordnet werden.")


def new_definition(mapping_id: str, spec: Mapping[str, Any], type_key: str, description: str = "") -> dict:
    return {"id": mapping_id, "version": 1, "description": description or f"{type_key} aus {spec.get('glob') or spec.get('path')}",
            "source": dict(spec), "freshness": {"window": "7 d"}, "deletion_guard": {"max_retire_share": 0.2},
            "edit_policy": "source_owned",
            "emits": [{"type": type_key, "identify": ["key"], "key": "", "name": "", "facts": {}, "relations": []}]}


def _written(defn: Mapping[str, Any]) -> dict:
    """The definition as the package writes it: empty parts left out."""
    out = json.loads(json.dumps(defn))
    for em in out.get("emits") or []:
        for k in ("facts", "relations", "description"):
            if k in em and not em[k]:
                em.pop(k)
    return out


@lru_cache(maxsize=1)
def _schema_validator():
    try:
        from importlib.resources import files

        text = files("brain_runner").joinpath("schemas/mapping.schema.json").read_text(encoding="utf-8")
    except (ImportError, FileNotFoundError, ModuleNotFoundError):
        return None
    from jsonschema import Draft202012Validator

    return Draft202012Validator(json.loads(text))


def problems(model: Mapping[str, Any], entry: Mapping[str, Any]) -> list[dict]:
    """What keeps a mapping from running: brain-runner's schema, then the ontology (validate_definitions' rules)."""
    found: list[dict] = []
    defn = entry.get("definition") or {}

    def add(target: str, message: str, severity: str = "critical") -> None:
        found.append({"target": target, "message": message, "severity": severity})

    validator = _schema_validator()
    if validator is not None:
        # «Zugriff» and «Zurückschreiben» (MVP-26) need a brain-runner whose schema knows them (MVP-12); with an older
        # pin the mapping is refused, and the problem says why instead of the schema's generic sentence.
        unknown = [k for k in ("access", "writes") if k in defn and k not in (validator.schema.get("properties") or {})]
        if unknown:
            add("mapping", "Die installierte brain-runner-Version kennt «Zugriff» und «Zurückschreiben» noch nicht "
                           f"({', '.join(unknown)}); erst mit der Version, die sie einführt, läuft diese Zuordnung.")
        for err in sorted(validator.iter_errors(_written(defn)), key=lambda e: list(map(str, e.absolute_path))):
            path = list(err.absolute_path)
            if unknown and not path and err.validator == "additionalProperties":
                continue
            target = "mapping"
            if len(path) >= 3 and path[0] == "emits":
                target = f"{path[1]}:fact:{path[3]}" if path[2] == "facts" and len(path) > 3 else f"{path[1]}:{path[2]}"
            add(target, f"{'/'.join(map(str, path)) or 'Zuordnung'}: {err.message}")
    onto = Ontology(model)
    for i, em in enumerate(defn.get("emits") or []):
        t = em.get("type")
        if not onto.live_type(str(t)):
            add(f"{i}:type", f"Den Typ {t} gibt es in der Ontologie nicht.")
            continue
        names = {a.get("name") for a in onto.attributes(t)}
        for a in em.get("identify") or []:
            if a not in names:
                add(f"{i}:identify", f"Erkannt wird an {a}, das ist kein Attribut von {t}.")
        for target, expr in (("key", em.get("key")), ("name", em.get("name"))):
            if expr:
                why = expression_problem(expr)
                if why:
                    add(f"{i}:{target}", why)
        for fname, spec in (em.get("facts") or {}).items():
            if fname not in names:
                add(f"{i}:fact:{fname}", f"{fname} ist kein Attribut von {t}.")
            why = expression_problem((spec or {}).get("value"))
            if why:
                add(f"{i}:fact:{fname}", why)
        for j, r in enumerate(em.get("relations") or []):
            rel = onto.live_relation(str(r.get("relation")))
            if rel is None:
                add(f"{i}:relation:{j}", f"Die Beziehung {r.get('relation')} gibt es nicht.")
            elif not onto.live_type(str(r.get("to_type"))):
                add(f"{i}:relation:{j}", f"Den Typ {r.get('to_type')} gibt es nicht.")
            elif not onto.fits(rel, t, r["to_type"], "in" if r.get("direction") == "in" else "out"):
                add(f"{i}:relation:{j}", f"{t} {r['relation']} {r['to_type']} passt nicht zu den Beziehungen der Ontologie.")
            if r.get("to_key"):
                why = expression_problem(r["to_key"])
                if why:
                    add(f"{i}:relation:{j}", why)
    return found


# ------------------------------------------------------------------ running

def run_documents(defn: Mapping[str, Any], documents: Iterable[Mapping[str, Any]], today: Optional[dt.date] = None):
    """brain-runner's MappingRun over documents a component handed over (no extractor of brain-runner reads them)."""
    from brain_runner.mapping import MappingRun, _emit, _selected

    today_s = (today or dt.date.today()).isoformat()
    src = defn["source"]
    out = MappingRun(defn["id"])
    for doc in documents:
        for item in _selected(doc, src["select"]):
            ctx = {src["as"]: item, "doc": doc, "lookup": {}}
            out.source_items += 1
            for em in defn["emits"]:
                cand, why = _emit(defn, em, ctx, doc, today_s)
                if cand is None:
                    out.skipped.append({"reason": why, "path": doc.get("path"), "type": em["type"]})
                else:
                    out.candidates.append(cand)
    return out


def run(defn: Mapping[str, Any], reader) -> Any:
    """The mapping's run through the reader of its workspace source."""
    defn = _written(defn)
    src = getattr(reader, "src", None)
    if src is not None:
        from brain_runner.mapping import run_mapping

        return run_mapping(defn, src)
    return run_documents(defn, reader.documents(defn["source"]))


def items(defn: Mapping[str, Any], reader, limit: int = 50) -> list[dict]:
    """Up to ``limit`` items the mapping's source selects, each with its document's path: the fields a target
    can take, named as an expression names them (``<as>.…``, ``doc.path``)."""
    from brain_runner.mapping import _selected

    spec = _written(defn)["source"]
    out: list[dict] = []
    for doc in reader.documents(spec):
        for item in _selected(doc, spec["select"]):
            out.append({spec["as"]: item, "doc": {"path": doc.get("path") if isinstance(doc, Mapping) else None}})
            if len(out) >= limit:
                return out
    return out


def preview(run_result, limit: int = PREVIEW_LIMIT) -> dict:
    """The first nodes, facts and edges a run would create, and what it skipped."""
    nodes, edges = [], []
    for cand in run_result.candidates[:limit]:
        nodes.append({"type": cand.type, "key": cand.key, "name": cand.name,
                      "facts": {k: v.value for k, v in cand.facts.items()}})
        for r in cand.relations:
            edges.append({"from": f"{cand.type} {cand.key}", "relation": r["relation"],
                          "to": f"{r['to_type']} {r.get('to_key') or '?'}", "direction": r.get("direction", "out")})
    reasons: dict[str, int] = {}
    for s in run_result.skipped:
        reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
    return {"items": run_result.source_items, "candidates": len(run_result.candidates), "nodes": nodes,
            "edges": edges[: limit * 3], "skipped": [{"reason": k, "count": v} for k, v in reasons.items()],
            "unmatched": len(run_result.unmatched)}


def plan_summary(plan: Mapping[str, Any], mapping_id: str) -> dict:
    """Counts of brain-runner's plan for one mapping: added, modified, retired, unchanged, drift, the guard."""
    s = (plan.get("summary") or {}).get(mapping_id) or {}
    guard = (plan.get("guard") or {}).get(mapping_id) or {}
    observed = (plan.get("observed") or {}).get(mapping_id) or {}
    return {"added": s.get("add", 0), "modified": s.get("change", 0), "retired": s.get("retire", 0),
            "unchanged": s.get("unchanged", 0), "drift": s.get("drift", 0),
            "items": observed.get("source_items", 0), "candidates": observed.get("candidates", 0),
            "guard": {"tripped": bool(guard.get("tripped")), "max_share": guard.get("max_share"),
                      "min_retire": guard.get("min_retire"), "types": guard.get("types") or {}},
            "blocked": mapping_id in (plan.get("blocked") or [])}


# ------------------------------------------------------------------ access and writing back (connection layer MVP-26)

#: The access a mapping's documents get (brain-runner's ``access``, MVP-12). ``fixed: [keys]`` names groups by the
#: group tree's stable keys as the access window (B14) names them, never live group ids; the runner checks at plan
#: time that they exist. ``from_source`` waits for the permissions from the source (I5) and is refused until then.
ACCESS_CHOICES = {"inherit": "Wie die Ordnerregel", "all": "Alle im Mandanten", "groups": "Gruppen",
                  "from_source": "aus der Quelle"}
#: Why a fact cannot be written back, as builder.codes names it.
WRITE_REASONS = ("not_invertible", "computed_attribute", "not_writable")


def is_computed(attribute: Optional[Mapping[str, Any]]) -> bool:
    """Whether the ontology configuration marks the attribute as computed: an explicit ``computed: true`` on it (the
    package's terms file; the model keeps the key under ``extra``). Nothing else counts -- a description that says
    «computed» is text, not a mark; each company package marks its own computed attributes."""
    if not attribute:
        return False
    return attribute.get("computed") is True or (attribute.get("extra") or {}).get("computed") is True


def _plain_path(expr: Any, var: str) -> Optional[str]:
    """The path a fact's expression reads from the item, when it is nothing but that path; else None."""
    found = simple_field(expr, var)
    if not found or not found.startswith(var + "."):
        return None             # a document's field (doc.…) is not the item's
    return found[len(var) + 1:]


def _local_invertible(defn: Mapping[str, Any]) -> dict:
    """MVP-13's rule, here until brain-runner ships it: brain-runner as pinned by this Platform has no
    ``brain_runner.inverse`` yet, so the Builder applies the same rule itself -- a fact can be written back only when
    its expression is a plain path into the item, never a computed value. Remove once the pin carries MVP-13."""
    var = str((defn.get("source") or {}).get("as") or VAR)
    out: dict[str, dict] = {}
    for em in defn.get("emits") or []:
        facts = out.setdefault(str(em.get("type")), {})
        for name, spec in (em.get("facts") or {}).items():
            facts[name] = (True, None) if _plain_path((spec or {}).get("value"), var) else (False, "not_invertible")
    return out


def invertible(defn: Mapping[str, Any], model: Optional[Mapping[str, Any]] = None) -> dict:
    """{Type: {fact: (writable, reason code or None)}} for the mapping's facts. brain-runner's own rule
    (``brain_runner.inverse.invertible``) when it is installed, else the same rule here (MVP-13). An attribute the
    ontology defines as computed is never listed."""
    try:
        from brain_runner.inverse import invertible as runner_invertible
    except ImportError:
        runner_invertible = None
    raw = runner_invertible(dict(defn)) if runner_invertible is not None else _local_invertible(defn)
    onto = Ontology(model or {})
    out: dict[str, dict] = {}
    for type_key, facts in (raw or {}).items():
        kept = {}
        for name, verdict in (facts or {}).items():
            ok, reason = (list(verdict) + [None, None])[:2] if isinstance(verdict, (list, tuple)) else (bool(verdict), None)
            if reason == "computed_attribute" or is_computed(onto.attribute(str(type_key), str(name))):
                continue
            kept[name] = (bool(ok), None if ok else (reason or "not_invertible"))
        out[str(type_key)] = kept
    return out


def write_field(defn: Mapping[str, Any], type_key: str, fact: str) -> Optional[str]:
    """The plain path a fact is written back to (the path its expression reads), or None."""
    var = str((defn.get("source") or {}).get("as") or VAR)
    for em in defn.get("emits") or []:
        if em.get("type") == type_key and fact in (em.get("facts") or {}):
            return _plain_path(((em.get("facts") or {}).get(fact) or {}).get("value"), var)
    return None


def access_text(access: Any) -> str:
    if not isinstance(access, Mapping) or not access:
        return "nicht gewählt"
    if access.get("fixed") == "all":
        return "alle im Mandanten (überschreibt Ordnerregeln)"
    if isinstance(access.get("fixed"), list):
        return "die Gruppen " + ", ".join(str(g) for g in access["fixed"])
    if access.get("inherit") == "folder_rule":
        return "wie die Ordnerregel"
    return "nicht gewählt"


def describe(model: Mapping[str, Any], entry: Mapping[str, Any]) -> str:
    """«Was diese Zuordnung tut»: one sentence (and the access and write-back) from the definition."""
    defn = entry.get("definition") or {}
    spec = defn.get("source") or {}
    what = spec.get("glob") or spec.get("path") or "Einträge"
    source_key = (entry.get("builder") or {}).get("source")
    source = next((s for s in model.get("sources") or [] if s.get("key") == source_key), None)
    where = f" aus der Quelle «{source.get('name')}»" if source else ""
    parts = []
    for em in defn.get("emits") or []:
        facts = sorted((em.get("facts") or {}).keys())
        relations = len(em.get("relations") or [])
        bits = [f"erkannt an {', '.join(em.get('identify') or ['key'])}"]
        if facts:
            bits.append(f"mit {len(facts)} Fakt{'' if len(facts) == 1 else 'en'} ({', '.join(facts)})")
        if relations:
            bits.append(f"{relations} Beziehung{'' if relations == 1 else 'en'}")
        parts.append(f"einen Knoten vom Typ {em.get('type')} ({'; '.join(bits)})")
    made = " und ".join(parts) if parts else "noch nichts"
    writes = [f"{t}.{f}" for t, facts in sorted((defn.get("writes") or {}).items()) for f in sorted(facts or {})]
    return (f"Liest {what}{where} und macht aus jedem Eintrag {made}. Zugriff: {access_text(defn.get('access'))}. "
            + (f"Zurückgeschrieben werden darf: {', '.join(writes)}." if writes else "Zurückgeschrieben wird nichts."))
