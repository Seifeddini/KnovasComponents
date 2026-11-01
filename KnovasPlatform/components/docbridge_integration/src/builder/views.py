"""How a company sees its Brain: spines, pages and profiles (build plan step B9).

The workspace keeps the three as the package writes them: the spines document
(``ontology/spines.yaml``: spines of ordered levels, each with its node types
and the relation that links a node to the level above, and branches), the page
definitions (``ontology/page-definitions.yaml``: per node type the ordered
sections -- slots -- a page shows and what fills each: a field, facts or
relation paths) and one profile per file (``profiles/*.yaml``: an audience, its
lens, views, pages, actions and rights). The role-to-profile mapping the
Platform reads from ``views.profile_roles`` is part of the workspace too
(``profile_roles``) and is written to that setting when a version is published.

The checks are KnowledgeBase's: the spine, page and profile schemas (copied
from KnowledgeBase, ``schema/``) over the form the bundle takes, then every
type, relation, path, process, spine and page a definition names against the
model. One more the builder adds: a type people read (a profile delivered
through the Platform shows it) without a page.

The preview draws a profile's start view and one page from sample data made
from the workspace itself -- the types' examples and labels, their attributes'
values -- so it shows what the definitions do before anything is published.
Nothing here knows a company.
"""
from __future__ import annotations

import copy
import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from builder import package
from builder import processes as P

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"

#: Render kinds of a profile view, as the builder names them.
RENDERS = {"map": "Karte", "trace": "Spur", "board": "Tafel", "list": "Liste", "matrix": "Matrix", "doc": "Seite",
           "tree": "Baum"}
#: How a page section shows its content.
SECTION_RENDERS = {"text": "Text", "list": "Liste", "table": "Tabelle", "status-list": "Statusliste",
                   "diagram": "Diagramm"}
SECTION_FIELDS = ("description", "name")
#: ``delivered_through`` values that mean the Platform (graph_views.profiles.PLATFORM).
PLATFORM = "platform"


# ------------------------------------------------------------------ the documents

def _document(model: Mapping[str, Any], part: str) -> dict:
    doc = model.get(part)
    return doc if isinstance(doc, dict) else {}


def spines_of(model: Mapping[str, Any]) -> list[dict]:
    items = _document(model, "spines").get("spines")
    return [s for s in items if isinstance(s, dict)] if isinstance(items, list) else []


def pages_of(model: Mapping[str, Any]) -> list[dict]:
    items = _document(model, "pages").get("pages")
    return [p for p in items if isinstance(p, dict)] if isinstance(items, list) else []


def profiles_of(model: Mapping[str, Any]) -> list[dict]:
    """The profiles' definitions (each from its own file)."""
    return [e["definition"] for e in model.get("profiles") or []
            if isinstance(e, Mapping) and isinstance(e.get("definition"), dict)]


def profile_roles(model: Mapping[str, Any]) -> dict[str, list[str]]:
    """{Platform role: [profile ids]}, as ``views.profile_roles`` takes it."""
    return {str(e["key"]): [str(p) for p in e.get("profiles") or []]
            for e in model.get("profile_roles") or [] if isinstance(e, Mapping) and e.get("key")}


def delivered_by_platform(profile: Mapping[str, Any]) -> bool:
    return str(profile.get("delivered_through") or "").strip().lower().startswith(PLATFORM)


def chains(spine: Mapping[str, Any]) -> list[tuple[Optional[str], list[dict]]]:
    """The spine's own levels and each branch's: [(None | branch key, levels)]."""
    out: list[tuple[Optional[str], list[dict]]] = [(None, [lv for lv in spine.get("levels") or [] if isinstance(lv, dict)])]
    for b in spine.get("branches") or []:
        if isinstance(b, dict):
            out.append((str(b.get("key")), [lv for lv in b.get("levels") or [] if isinstance(lv, dict)]))
    return out


def link_for(level: Mapping[str, Any], type_key: str) -> Optional[dict]:
    """The link of ``type_key`` at ``level`` to the level above: its own, else the level's."""
    own = (level.get("links_to_parent") or {}).get(type_key)
    if isinstance(own, Mapping):
        return dict(own)
    common = level.get("link_to_parent")
    return dict(common) if isinstance(common, Mapping) else None


def _verified(levels: Iterable[Any]) -> list:
    out = []
    for lv in levels:
        if isinstance(lv, Mapping):
            lv = {**copy.deepcopy(dict(lv)), "verified": bool(lv.get("verified", True))}
        out.append(lv)
    return out


def normalise_spines(doc: Mapping[str, Any]) -> dict:
    """The spines in the generated form (generated/spine-definitions.json): every level says whether it is verified."""
    spines = []
    for s in doc.get("spines") or []:
        if isinstance(s, Mapping):
            s = copy.deepcopy(dict(s))
            s["levels"] = _verified(s.get("levels") or [])
            if "branches" in s:
                s["branches"] = [{**b, "levels": _verified(b.get("levels") or [])} if isinstance(b, Mapping) else b
                                 for b in s.get("branches") or []]
        spines.append(s)
    return {"version": int(doc.get("version", 1) or 1), "generated_at": "", "spines": spines}


def normalise_pages(doc: Mapping[str, Any]) -> dict:
    """The pages in the generated form (generated/page-definitions.json): as written."""
    return {"version": int(doc.get("version", 1) or 1), "generated_at": "",
            "pages": copy.deepcopy(list(doc.get("pages") or []))}


@lru_cache(maxsize=4)
def _validator(name: str):
    from jsonschema import Draft202012Validator

    schema = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


# ------------------------------------------------------------------ checks

def _problem(found: list, severity: str, area: str, item: Any, kind: str, message: str, part: Any = None) -> None:
    found.append({"id": f"{area}:{item}:{kind}:{part or ''}:{len(found)}", "severity": severity, "area": area,
                  "item": item, "part": part, "kind": kind, "message": message})


#: What an empty list a definition needs means, said as what to do (the schema says "[] is too short").
_EMPTY = {"levels": "Die Hierarchie hat noch keine Ebene. Legen Sie die oberste Ebene an.",
          "types": "Die Ebene nennt noch keinen Typ. Wählen Sie, welche Knoten auf dieser Ebene stehen.",
          "sections": "Die Seite hat noch keinen Abschnitt.",
          "views": "Das Profil hat noch keine Ansicht.",
          "path": "Ein Pfad braucht mindestens einen Schritt."}


def _message(error) -> str:
    if error.validator == "minItems" and error.instance == [] and error.absolute_path:
        plain = _EMPTY.get(str(list(error.absolute_path)[-1]))
        if plain:
            return plain
    return error.message


def _schema(found: list, area: str, name: str, doc: Any, list_key: Optional[str], key_field: str) -> bool:
    """Schema errors of ``doc``, each named after the element it is in; whether there were none."""
    clean = True
    for error in sorted(_validator(name).iter_errors(doc), key=lambda e: list(map(str, e.absolute_path))):
        parts = list(error.absolute_path)
        item = None
        if list_key and len(parts) >= 2 and parts[0] == list_key and isinstance(parts[1], int):
            entry = doc[list_key][parts[1]]
            item = entry.get(key_field) if isinstance(entry, Mapping) else None
        elif not list_key:
            item = doc.get(key_field) if isinstance(doc, Mapping) else None
        where = "/".join(str(p) for p in parts) or area
        message = _message(error)
        _problem(found, "critical", area, item, "schema", message if message != error.message else f"{where}: {message}")
        clean = False
    return clean


def _hops(path: Any) -> list[dict]:
    return [{"relation": str(h.get("relation")), "direction": str(h.get("direction")), "to_type": str(h.get("to_type"))}
            for h in path or [] if isinstance(h, Mapping)]


def path_text(path: list[dict]) -> str:
    return " / ".join(f"{h['relation']} {'→' if h['direction'] == 'out' else '←'} {h['to_type']}" for h in path)


def _end_types(onto: P.Ontology, path: list[dict]) -> list[str]:
    return [path[-1]["to_type"]] if path else []


def _check_where(found: list, onto: P.Ontology, area: str, item: Any, part: Any, where: Any, types: list[str]) -> None:
    if isinstance(where, Mapping) and where.get("fact"):
        if not any(onto.attribute(t, str(where["fact"])) for t in types):
            _problem(found, "warning", area, item, "where_fact_unknown",
                     f"Die Bedingung prüft {where['fact']}, das {', '.join(types)} nicht hat.", part)


def _check_spines(model: Mapping[str, Any], onto: P.Ontology, found: list) -> None:
    doc = _document(model, "spines")
    spines = spines_of(model)
    if not spines:
        return
    if not _schema(found, "spine", "spine-definition", normalise_spines(doc), "spines", "key"):
        return
    keys = [s["key"] for s in spines]
    for dup in sorted({k for k in keys if keys.count(k) > 1}):
        _problem(found, "critical", "spine", dup, "spine_duplicate", f"Die Hierarchie {dup} gibt es mehrfach.")
    for s in spines:
        k = s["key"]
        for branch, levels in chains(s):
            where = f"Zweig {branch}: " if branch else ""
            ids = [str(lv.get("id")) for lv in levels]
            for dup in sorted({i for i in ids if ids.count(i) > 1}):
                _problem(found, "critical", "spine", k, "level_duplicate", f"{where}die Ebene {dup} gibt es mehrfach.", dup)
            for n, lv in enumerate(levels):
                lid = str(lv.get("id"))
                types = [str(t) for t in lv.get("types") or []]
                for t in types:
                    if not onto.live_type(t):
                        _problem(found, "critical", "spine", k, "type_unknown",
                                 f"{where}Ebene {lv.get('name') or lid}: den Typ {t} gibt es nicht.", lid)
                for t in (lv.get("links_to_parent") or {}):
                    if t not in types:
                        _problem(found, "warning", "spine", k, "link_type_unused",
                                 f"{where}Ebene {lv.get('name') or lid}: die Verbindung für {t} gilt keinem Typ der Ebene.", lid)
                if n == 0:
                    if lv.get("link_to_parent") or lv.get("links_to_parent"):
                        _problem(found, "warning", "spine", k, "link_on_root",
                                 f"{where}die oberste Ebene {lv.get('name') or lid} hängt an nichts; ihre Verbindung "
                                 "nach oben wird nicht gelesen.", lid)
                    continue
                parents = [str(t) for t in levels[n - 1].get("types") or []]
                for t in types:
                    link = link_for(lv, t)
                    if link is None:
                        _problem(found, "critical", "spine", k, "link_missing",
                                 f"{where}Ebene {lv.get('name') or lid}: {t} hat keine Beziehung zur Ebene darüber.", lid)
                        continue
                    rel = onto.live_relation(str(link.get("relation")))
                    if rel is None:
                        _problem(found, "critical", "spine", k, "relation_unknown",
                                 f"{where}Ebene {lv.get('name') or lid}: die Beziehung {link.get('relation')} gibt es nicht.", lid)
                    elif onto.live_type(t) and not any(onto.fits(rel, t, p, str(link.get("direction"))) for p in parents):
                        arrow = "→" if link.get("direction") == "out" else "←"
                        _problem(found, "critical", "spine", k, "link_mismatch",
                                 f"{where}Ebene {lv.get('name') or lid}: {t} {arrow} {link.get('relation')} {arrow} "
                                 f"{' / '.join(parents) or '–'} passt nicht zu Von und Nach der Beziehung.", lid)


def _check_pages(model: Mapping[str, Any], onto: P.Ontology, found: list) -> None:
    doc = _document(model, "pages")
    pages = pages_of(model)
    if not pages:
        return
    if not _schema(found, "page", "page-definition", normalise_pages(doc), "pages", "key"):
        return
    keys = [p["key"] for p in pages]
    for dup in sorted({k for k in keys if keys.count(k) > 1}):
        _problem(found, "critical", "page", dup, "page_duplicate", f"Die Seite {dup} gibt es mehrfach.")
    seen: dict[tuple[str, str], str] = {}
    for p in pages:
        k, t = p["key"], str(p["type"])
        prefix = str((p.get("filter") or {}).get("key_prefix") or "")
        if (t, prefix) in seen:
            _problem(found, "warning", "page", k, "page_twice",
                     f"{t}{' mit ' + prefix + '…' if prefix else ''} hat schon die Seite {seen[(t, prefix)]}.")
        seen.setdefault((t, prefix), k)
        if not onto.live_type(t):
            _problem(found, "critical", "page", k, "type_unknown", f"Den Typ {t} gibt es nicht.")
            continue
        ids = [s["id"] for s in p["sections"]]
        for dup in sorted({i for i in ids if ids.count(i) > 1}):
            _problem(found, "critical", "page", k, "section_duplicate", f"Den Abschnitt {dup} gibt es mehrfach.", dup)
        for s in p["sections"]:
            sid, title = s["id"], (s.get("title") or {}).get("de") or s["id"]
            for f in s.get("facts") or []:
                if not onto.attribute(t, f):
                    _problem(found, "warning", "page", k, "fact_unknown", f"Abschnitt {title}: {t} hat kein Attribut {f}.", sid)
            ends: list[str] = []
            for sp in s.get("paths") or []:
                path = _hops(sp.get("path"))
                why = P.path_problem(onto, [t], path)
                if why:
                    _problem(found, "critical", "page", k, "path_invalid", f"Abschnitt {title}: {why}", sid)
                    continue
                ends += _end_types(onto, path)
                _check_where(found, onto, "page", k, sid, sp.get("where"), _end_types(onto, path))
            for c in s.get("columns") or []:
                if ends and not any(onto.attribute(e, c) for e in ends):
                    _problem(found, "warning", "page", k, "column_unknown",
                             f"Abschnitt {title}: die Spalte {c} ist kein Attribut von {', '.join(sorted(set(ends)))}.", sid)
            status = s.get("status")
            if isinstance(status, Mapping) and ends:
                why = P.path_problem(onto, ends, _hops(status.get("path")))
                if why:
                    _problem(found, "critical", "page", k, "status_path_invalid", f"Abschnitt {title}, Status: {why}", sid)


def _rule_ids(model: Mapping[str, Any]) -> set[str]:
    doc = P.document(model)
    return set(P.R_RULES) | {str(r.get("id")) for r in doc.get("rules") or [] if isinstance(r, Mapping)}


def _check_profiles(model: Mapping[str, Any], onto: P.Ontology, found: list) -> None:
    spine_keys = {s.get("key") for s in spines_of(model)}
    page_keys = {p.get("key") for p in pages_of(model)}
    procs = {p.get("key"): p for p in P.processes_of(model)}
    rules = _rule_ids(model)
    profiles = profiles_of(model)
    ids = [str(p.get("id")) for p in profiles]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        _problem(found, "critical", "profile", dup, "profile_duplicate", f"Das Profil {dup} gibt es mehrfach.")
    for prof in profiles:
        if not _schema(found, "profile", "profile", prof, None, "id"):
            continue
        k = prof["id"]
        lens = prof.get("lens") or {}
        for s in lens.get("spines") or []:
            if s not in spine_keys:
                _problem(found, "critical", "profile", k, "spine_unknown", f"Die Hierarchie {s} gibt es nicht.")
        for t in lens.get("types") or []:
            if t != P.WILDCARD and not onto.live_type(t):
                _problem(found, "warning", "profile", k, "type_unknown", f"Den Typ {t} im Blickfeld gibt es nicht.")
        for r in lens.get("relations") or []:
            if r != P.WILDCARD and onto.live_relation(r) is None:
                _problem(found, "warning", "profile", k, "relation_unknown", f"Die Beziehung {r} im Blickfeld gibt es nicht.")
        for pg in prof.get("pages") or []:
            if pg not in page_keys:
                _problem(found, "critical", "profile", k, "page_unknown", f"Die Seite {pg} gibt es nicht.")
        vids = [str(v.get("id")) for v in prof.get("views") or []]
        for dup in sorted({i for i in vids if vids.count(i) > 1}):
            _problem(found, "critical", "profile", k, "view_duplicate", f"Die Ansicht {dup} gibt es mehrfach.", dup)
        for v in prof.get("views") or []:
            vid, label, render = v["id"], v.get("label") or v["id"], v["render"]
            d = v.get("definition") or {}
            roots = [str(t) for t in d.get("root_types") or []]
            for t in roots:
                if not onto.live_type(t):
                    _problem(found, "critical", "profile", k, "root_unknown", f"Ansicht {label}: den Typ {t} gibt es nicht.", vid)
            live_roots = [t for t in roots if onto.live_type(t)]
            ends: list[str] = []
            for i, lp in enumerate(d.get("paths") or [], start=1):
                path = _hops(lp.get("path"))
                why = P.path_problem(onto, live_roots, path) if live_roots else None
                if why:
                    _problem(found, "critical", "profile", k, "path_invalid",
                             f"Ansicht {label}, Pfad {lp.get('label') or i}: {why}", vid)
                    continue
                ends += _end_types(onto, path)
                _check_where(found, onto, "profile", k, vid, lp.get("where"), _end_types(onto, path))
            if d.get("process") and d["process"] not in procs:
                _problem(found, "critical", "profile", k, "process_unknown",
                         f"Ansicht {label}: den Prozess {d['process']} gibt es nicht.", vid)
            if d.get("spine") and d["spine"] not in spine_keys:
                _problem(found, "critical", "profile", k, "spine_unknown",
                         f"Ansicht {label}: die Hierarchie {d['spine']} gibt es nicht.", vid)
            for r in d.get("rules") or []:
                if r not in rules:
                    _problem(found, "warning", "profile", k, "rule_unknown", f"Ansicht {label}: die Regel {r} gibt es nicht.", vid)
            for c in d.get("columns") or []:
                if live_roots and not any(onto.attribute(t, c) for t in live_roots) and c not in ("owner", "current_step"):
                    _problem(found, "warning", "profile", k, "column_unknown",
                             f"Ansicht {label}: die Spalte {c} ist kein Attribut von {', '.join(live_roots)}.", vid)
            if d.get("group_by") and not onto.live_type(str(d["group_by"])):
                _problem(found, "warning", "profile", k, "group_unknown",
                         f"Ansicht {label}: nach {d['group_by']} gruppieren – den Typ gibt es nicht.", vid)
            why = drawable(v, procs)
            if why:
                _problem(found, "warning", "profile", k, "not_drawable", f"Ansicht {label}: {why}", vid)


def drawable(view: Mapping[str, Any], procs: Mapping[str, Any]) -> Optional[str]:
    """Why the Platform cannot draw ``view`` (graph_views.profiles.open_spec), or None."""
    d = view.get("definition") or {}
    roots = list(d.get("root_types") or [])
    render = view.get("render")
    if render in ("map", "tree") and not d.get("spine") and not d.get("paths"):
        return "eine Karte ohne Hierarchie braucht Pfade."
    if render in ("board", "trace") and not d.get("process"):
        if not any(roots and roots[0] in (p.get("instance_types") or []) for p in procs.values()):
            return f"kein Prozess hat {roots[0] if roots else 'die Wurzeltypen'} als Instanztyp."
    if render == "list" and not roots:
        return "eine Liste braucht einen Wurzeltyp."
    if render == "matrix" and not d.get("spine") and not roots:
        return "eine Matrix braucht eine Hierarchie oder Wurzeltypen."
    return None


def read_types(model: Mapping[str, Any]) -> dict[str, list[str]]:
    """The types people read -- a profile delivered through the Platform shows them -- and which profiles do."""
    onto = P.Ontology(model)
    out: dict[str, list[str]] = defaultdict(list)
    for prof in profiles_of(model):
        if not delivered_by_platform(prof):
            continue
        named = [t for t in (prof.get("lens") or {}).get("types") or [] if t != P.WILDCARD]
        named += [t for v in prof.get("views") or [] for t in (v.get("definition") or {}).get("root_types") or []]
        for t in named:
            if onto.live_type(str(t)) and not onto.types[str(t)].get("meta") and prof.get("id") not in out[str(t)]:
                out[str(t)].append(str(prof.get("id")))
    return dict(out)


def _check_coverage(model: Mapping[str, Any], found: list) -> None:
    paged = {str(p.get("type")) for p in pages_of(model)}
    for t, profiles in sorted(read_types(model).items()):
        if t not in paged:
            _problem(found, "warning", "page", None, "page_missing",
                     f"{t} wird gelesen (Profil {', '.join(profiles)}), hat aber keine Seite.", t)


def _check_roles(model: Mapping[str, Any], roles: Optional[Iterable[str]], found: list) -> None:
    ids = {str(p.get("id")) for p in profiles_of(model)}
    known = {str(r) for r in roles} if roles is not None else None
    for role, names in profile_roles(model).items():
        if known is not None and role not in known:
            _problem(found, "critical", "roles", role, "role_unknown", f"Die Rolle {role} gibt es auf der Plattform nicht.")
        for n in names:
            if n not in ids:
                _problem(found, "critical", "roles", role, "profile_unknown", f"{role} sieht das Profil {n}, das es nicht gibt.")


def validate(model: Mapping[str, Any], *, roles: Optional[Iterable[str]] = None) -> list[dict]:
    """Every problem of the spines, pages, profiles and the role mapping, critical ones first."""
    onto = P.Ontology(model)
    found: list[dict] = []
    _check_spines(model, onto, found)
    _check_pages(model, onto, found)
    _check_profiles(model, onto, found)
    _check_coverage(model, found)
    _check_roles(model, roles, found)
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(found, key=lambda f: order[f["severity"]])


# ------------------------------------------------------------------ the view

def _label(t: Mapping[str, Any]) -> str:
    return (t.get("label") or {}).get("de") or (t.get("label") or {}).get("en") or str(t.get("key"))


def build(model: Mapping[str, Any], *, roles: Optional[Iterable[str]] = None) -> dict:
    """What the views stage draws: spines, pages, profiles, the role mapping, problems and the ontology they name."""
    onto = P.Ontology(model)
    roles = list(roles) if roles is not None else None
    problems = validate(model, roles=roles)

    def counts(area: str, key: Any) -> dict:
        mine = [f for f in problems if f["area"] == area and f["item"] == key]
        return {s: sum(1 for f in mine if f["severity"] == s) for s in ("critical", "warning")}

    profiles = []
    for e in model.get("profiles") or []:
        d = e.get("definition") or {}
        profiles.append({"file": e.get("file"), "definition": d, "id": d.get("id"),
                         "platform": delivered_by_platform(d), "problems": counts("profile", d.get("id"))})
    types = [{"key": k, "name": _label(t), "meta": bool(t.get("meta")), "parent": t.get("parent"),
              "attributes": [{"name": a["name"], "datatype": a.get("datatype") or "text"} for a in onto.attributes(k)]}
             for k, t in sorted(onto.types.items()) if not t.get("retired")]
    relations = [{"key": r["key"], "domain": list(r.get("domain") or []), "range": list(r.get("range") or []),
                  "label": (r.get("label") or {}).get("de") or ""}
                 for r in sorted(onto.relations.values(), key=lambda r: r["key"]) if not r.get("retired")]
    procs = [{"key": p.get("key"), "name": p.get("name") or p.get("key"),
              "instance_types": [str(t) for t in p.get("instance_types") or []]} for p in P.processes_of(model)]
    read = read_types(model)
    paged = {str(p.get("type")) for p in pages_of(model)}
    return {
        "available": {part: package.part_available(model, part) for part in ("spines", "pages", "profiles")},
        "spines": [{**s, "problems": counts("spine", s.get("key"))} for s in spines_of(model)],
        "pages": [{**p, "problems": counts("page", p.get("key"))} for p in pages_of(model)],
        "profiles": profiles,
        "roles": [{"role": r, "profiles": profile_roles(model).get(r, [])}
                  for r in (roles if roles is not None else sorted(profile_roles(model)))],
        "role_map": profile_roles(model),
        "problems": problems,
        "types": types,
        "relations": relations,
        "processes": procs,
        "rules": sorted(_rule_ids(model)),
        "renders": RENDERS,
        "section_renders": SECTION_RENDERS,
        "unpaged": sorted(t for t in read if t not in paged),
        "counts": {"spines": len(spines_of(model)), "pages": len(pages_of(model)), "profiles": len(profiles),
                   "problems": {s: sum(1 for f in problems if f["severity"] == s) for s in ("critical", "warning")}},
    }


# ------------------------------------------------------------------ the preview's sample data

_HEADER = re.compile(r"^\s*\[[^\]]*\]\s*")
_DATES = ("2026-10-12", "2026-10-26", "2026-11-09", "2026-09-28")


def _plain(text: Any) -> str:
    return _HEADER.sub("", str(text or "")).strip()


class Fixtures:
    """A small graph of sample nodes made from the workspace's own types, for the preview.

    A node's key and name come from its type's example ("KEY, what it is.") or its label;
    its facts from the type's attributes (an enum's values in turn, a date, a figure). Following
    a path from a node makes its targets once, so two paths with a common start share them.
    """

    def __init__(self, model: Mapping[str, Any], *, fan: int = 2):
        self.onto = P.Ontology(model)
        self.fan = fan
        self.nodes: dict[str, dict] = {}
        self.edges: list[tuple[str, str, str]] = []
        self._made: dict[tuple[str, str, str, str], list[str]] = {}
        self._count: dict[str, int] = defaultdict(int)

    def _sample(self, type_key: str) -> tuple[str, str]:
        """(key, name) from the type's example ("KEY, what it is."), else from its label."""
        t = self.onto.types.get(type_key) or {}
        label = _label(t) if t else type_key
        example = _plain(t.get("example")).rstrip(".")
        head, _, rest = example.partition(", ")
        if head and " " not in head and len(head) <= 60:
            return head, (rest[:1].upper() + rest[1:])[:80] if rest else label
        return re.sub(r"[^A-Z]", "", type_key)[:4] or "N", example[:80] or label

    def _facts(self, type_key: str, n: int) -> dict[str, str]:
        facts = {}
        for a in self.onto.attributes(type_key)[:12]:
            name, datatype = a["name"], a.get("datatype") or "text"
            values = [str(v) for v in a.get("enum_values") or []]
            if values:
                facts[name] = values[n % len(values)]
            elif datatype == "date" or name.endswith(("_on", "_date", "_at")) or name == "date":
                facts[name] = _DATES[n % len(_DATES)]
            elif datatype == "money":
                facts[name] = f"{(n + 1) * 1200} CHF"
            elif datatype == "entity_ref" and a.get("target"):
                facts[name] = self._sample(str(a["target"]))[1]
            elif name in ("owner", "owner_role"):
                facts[name] = ("Leitung", "Team A", "Team B")[n % 3]
        return facts

    def node(self, type_key: str) -> str:
        n = self._count[type_key]
        self._count[type_key] += 1
        key, name = self._sample(type_key)
        node_id = f"{type_key}:{n}"
        t = self.onto.types.get(type_key) or {}
        self.nodes[node_id] = {"id": node_id, "type": type_key, "type_name": _label(t) if t else type_key,
                               "key": key if n == 0 else f"{key}-{n + 1}", "name": name if n == 0 else f"{name} ({n + 1})",
                               "facts": self._facts(type_key, n),
                               "description": _plain((t.get("description") or {}).get("de")
                                                     or (t.get("description") or {}).get("en"))}
        return node_id

    def children(self, parent: str, hop: Mapping[str, str], count: Optional[int] = None, label: str = "") -> list[str]:
        key = (parent, hop["relation"], hop["direction"], hop["to_type"])
        if key not in self._made:
            made = [self.node(hop["to_type"]) for _ in range(count if count is not None else self.fan)]
            self._made[key] = made
            for m in made:
                self.edges.append((parent, m, label or hop["relation"]))
        return self._made[key]

    def follow(self, start: str, path: list[dict], *, label: str = "") -> list[str]:
        frontier = [start]
        for i, hop in enumerate(path):
            fan = self.fan if i < 2 else 1
            frontier = [c for n in frontier for c in self.children(n, hop, fan, label if i == len(path) - 1 else "")]
        return frontier

    def card(self, node_id: str, columns: Iterable[str] = ()) -> dict:
        n = self.nodes[node_id]
        return {"name": n["name"], "key": n["key"], "type": n["type"], "type_name": n["type_name"],
                "facts": [{"name": c, "value": n["facts"].get(c, "–")} for c in columns]}


# ------------------------------------------------------------------ the preview

def _tree(fx: Fixtures, node_id: str, children: Mapping[str, list[tuple[str, str]]], depth: int = 0) -> dict:
    out = fx.card(node_id)
    if depth < 6:
        out["children"] = [{**_tree(fx, c, children, depth + 1), "via": label} for c, label in children.get(node_id, [])]
    return out


def _map_from_paths(fx: Fixtures, d: Mapping[str, Any]) -> dict:
    roots = [fx.node(t) for t in d.get("root_types") or [] if fx.onto.live_type(str(t))][:2]
    children: dict[str, list[tuple[str, str]]] = defaultdict(list)
    skipped = []
    for i, lp in enumerate(d.get("paths") or [], start=1):
        path = _hops(lp.get("path"))
        label = str(lp.get("label") or f"Pfad {i}")
        if P.path_problem(fx.onto, [fx.nodes[r]["type"] for r in roots], path):
            skipped.append(label)
            continue
        for r in roots:
            parents = fx.follow(r, path[:-1]) if len(path) > 1 else [r]
            for p in parents:
                for c in fx.children(p, path[-1], fx.fan if len(path) <= 2 else 1, label):
                    if (c, label) not in children[p]:
                        children[p].append((c, label))
    return {"kind": "map", "roots": [_tree(fx, r, children) for r in roots], "skipped": skipped}


def _spine_levels(model: Mapping[str, Any], key: Any) -> list[dict]:
    spine = next((s for s in spines_of(model) if s.get("key") == key), None)
    return [lv for lv in (spine or {}).get("levels") or [] if isinstance(lv, dict)]


def _spine_nodes(fx: Fixtures, levels: list[dict]) -> tuple[list[str], dict[str, list[tuple[str, str]]], list[list[str]]]:
    if not levels:
        return [], {}, []
    first = [t for t in levels[0].get("types") or [] if fx.onto.live_type(str(t))]
    roots = [fx.node(first[0])] if first else []
    children: dict[str, list[tuple[str, str]]] = defaultdict(list)
    by_level = [roots]
    for lv in levels[1:]:
        frontier = []
        for p in by_level[-1]:
            for t in lv.get("types") or []:
                link = link_for(lv, str(t)) or {}
                if not fx.onto.live_type(str(t)):
                    continue
                # The link goes from the child to the parent ("out") or back; from the parent it is the other way.
                hop = {"relation": str(link.get("relation") or "?"), "direction": "in" if link.get("direction") == "out" else "out",
                       "to_type": str(t)}
                for c in fx.children(p, hop, 2 if len(by_level) < 3 else 1, lv.get("name") or ""):
                    children[p].append((c, str(lv.get("name") or "")))
                    frontier.append(c)
        by_level.append(frontier)
    return roots, children, by_level


def start_view(model: Mapping[str, Any], view: Mapping[str, Any], fx: Optional[Fixtures] = None) -> dict:
    """A profile view drawn from sample data: what the person of the profile sees first."""
    fx = fx or Fixtures(model)
    d = view.get("definition") or {}
    render = view.get("render")
    roots = [str(t) for t in d.get("root_types") or [] if fx.onto.live_type(str(t))]
    base = {"id": view.get("id"), "label": view.get("label") or view.get("id"), "render": render,
            "render_label": RENDERS.get(str(render), render), "description": d.get("description") or ""}
    procs = {p.get("key"): p for p in P.processes_of(model)}
    why = drawable(view, procs)
    if why:
        return {**base, "kind": "unsupported", "why": why}
    if render in ("map", "tree"):
        if d.get("spine"):
            levels = _spine_levels(model, d["spine"])
            top, children, _ = _spine_nodes(fx, levels)
            return {**base, "kind": "map", "roots": [_tree(fx, r, children) for r in top], "skipped": []}
        return {**base, **_map_from_paths(fx, d)}
    if render == "matrix":
        if d.get("spine"):
            levels = _spine_levels(model, d["spine"])
            _, children, by_level = _spine_nodes(fx, levels)
            rows = by_level[1] if len(by_level) > 2 else by_level[0] if by_level else []
            cols = by_level[2] if len(by_level) > 2 else by_level[1] if len(by_level) > 1 else []
            under = {r: {c for c, _ in children.get(r, [])} for r in rows}
            return {**base, "kind": "matrix", "rows": [fx.nodes[r]["name"] for r in rows],
                    "columns": [fx.nodes[c]["name"] for c in cols],
                    "cells": [["filled" if c in under[r] else "empty" for c in cols] for r in rows]}
        rules = list(d.get("rules") or [])[:9] or ["–"]
        return {**base, "kind": "matrix", "rows": roots, "columns": rules,
                "cells": [[("filled", "missing", "filled", "stale")[(i + j) % 4] for j, _ in enumerate(rules)]
                          for i, _ in enumerate(roots)]}
    if render in ("board", "trace"):
        proc = procs.get(d.get("process")) or next(
            (p for p in procs.values() if roots and roots[0] in (p.get("instance_types") or [])), None)
        steps = [P.step_of(s) for s in (proc or {}).get("steps") or []]
        step_ids = [str(s.get("id")) for s in steps]
        if render == "board":
            columns = list(d.get("columns") or [])
            cards = [fx.node(roots[0]) for _ in range(min(6, max(3, len(step_ids))))] if roots else []
            lanes = [{"step": sid, "name": str(s.get("name") or sid).replace("-", " "),
                      "resting": sid in [str(x) for x in (proc or {}).get("final") or []],
                      "cards": [fx.card(c, [x for x in columns if x != "current_step"])
                                for n, c in enumerate(cards) if n % max(1, len(step_ids)) == i]}
                     for i, (sid, s) in enumerate(zip(step_ids, steps))]
            return {**base, "kind": "board", "process": (proc or {}).get("key"), "lanes": lanes}
        root = fx.node(roots[0]) if roots else None
        columns = []
        for i, lp in enumerate(d.get("paths") or [], start=1):
            path = _hops(lp.get("path"))
            label = str(lp.get("label") or f"Pfad {i}")
            if root is None or P.path_problem(fx.onto, roots, path):
                columns.append({"label": label, "items": [], "invalid": True})
                continue
            columns.append({"label": label, "items": [fx.card(n) for n in fx.follow(root, path, label=label)]})
        current = step_ids[len(step_ids) // 2] if step_ids else None
        return {**base, "kind": "trace", "process": (proc or {}).get("key"), "root": fx.card(root) if root else None,
                "steps": [{"id": s, "current": s == current} for s in step_ids], "columns": columns}
    if render == "list":
        columns = list(d.get("columns") or [])
        rows = [fx.node(roots[0]) for _ in range(5)]
        path_cols = []
        for i, lp in enumerate(d.get("paths") or [], start=1):
            path = _hops(lp.get("path"))
            if not P.path_problem(fx.onto, roots, path):
                path_cols.append((str(lp.get("label") or f"Pfad {i}"), path))
        group_by = d.get("group_by")
        out_rows = []
        for n, r in enumerate(rows):
            row = fx.card(r, columns)
            row["paths"] = []
            for label, path in path_cols:
                found = fx.follow(r, path)
                more = f" (+{len(found) - 1})" if len(found) > 1 else ""
                row["paths"].append({"label": label, "value": (fx.nodes[found[0]]["key"] + more) if found else "–"})
            if group_by:
                groups = [x for label, path in path_cols if path[-1]["to_type"] == group_by for x in fx.follow(r, path)[:1]]
                row["group"] = fx.nodes[groups[0]]["name"] if groups else "ohne " + str(group_by)
            out_rows.append(row)
        return {**base, "kind": "list", "columns": columns, "path_columns": [label for label, _ in path_cols],
                "group_by": group_by, "rows": out_rows}
    return {**base, "kind": "doc"}


def page_preview(model: Mapping[str, Any], page: Mapping[str, Any], fx: Optional[Fixtures] = None) -> dict:
    """A page of sample data: every slot filled as the Platform fills it, an empty required one marked."""
    fx = fx or Fixtures(model)
    t = str(page.get("type"))
    out = {"key": page.get("key"), "type": t, "name": (page.get("name") or {}).get("de") or page.get("key"),
           "sections": []}
    if not fx.onto.live_type(t):
        out["missing_type"] = True
        return out
    node = fx.node(t)
    out["node"] = fx.card(node)
    for s in page.get("sections") or []:
        sec = {"id": s.get("id"), "title": (s.get("title") or {}).get("de") or s.get("id"), "render": s.get("render"),
               "required": bool(s.get("required")), "items": [], "pairs": [], "text": "", "edges": [], "problems": []}
        if s.get("field") == "description":
            sec["text"] = fx.nodes[node]["description"] or f"Beschreibung von {fx.nodes[node]['name']}."
        elif s.get("field") == "name":
            sec["text"] = fx.nodes[node]["name"]
        for f in s.get("facts") or []:
            sec["pairs"].append({"name": f, "value": fx.nodes[node]["facts"].get(f, "–")})
        columns = list(s.get("columns") or [])
        status = s.get("status") if isinstance(s.get("status"), Mapping) else None
        for i, sp in enumerate(s.get("paths") or [], start=1):
            path = _hops(sp.get("path"))
            why = P.path_problem(fx.onto, [t], path)
            if why:
                sec["problems"].append(why)
                continue
            for n, target in enumerate(fx.follow(node, path, label=str(sp.get("label") or ""))):
                card = fx.card(target, columns)
                card["via"] = sp.get("label") or ""
                if status:
                    card["status"] = status.get("passing") if n % 3 != 2 else "offen"
                if card["name"] not in [x["name"] for x in sec["items"]]:
                    sec["items"].append(card)
                if s.get("render") == "diagram":
                    sec["edges"].append({"from": fx.nodes[node]["name"], "to": card["name"],
                                         "label": sp.get("label") or path[-1]["relation"]})
        sec["empty"] = not (sec["text"] or sec["pairs"] or sec["items"])
        out["sections"].append(sec)
    return out


def preview(model: Mapping[str, Any], profile_id: str, *, view_id: Optional[str] = None,
            page_key: Optional[str] = None) -> Optional[dict]:
    """A profile's start view (its first view, or ``view_id``) and one of its pages, from sample data."""
    prof = next((p for p in profiles_of(model) if p.get("id") == profile_id), None)
    if prof is None:
        return None
    fx = Fixtures(model)
    views = [v for v in prof.get("views") or [] if isinstance(v, Mapping)]
    view = next((v for v in views if v.get("id") == view_id), views[0] if views else None)
    pages = {p.get("key"): p for p in pages_of(model)}
    keys = [k for k in prof.get("pages") or [] if k in pages]
    chosen = page_key if page_key in pages else (keys[0] if keys else None)
    return {"profile": prof.get("id"), "audience": prof.get("audience"),
            "view": start_view(model, view, fx) if view else None,
            "views": [{"id": v.get("id"), "label": v.get("label") or v.get("id")} for v in views],
            "page": page_preview(model, pages[chosen], fx) if chosen else None,
            "pages": keys}
