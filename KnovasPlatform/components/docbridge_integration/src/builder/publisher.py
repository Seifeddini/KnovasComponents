"""Compile, dry-run and apply a workspace version (build plan step B5).

The only module that knows brain-runner (KnowledgeBase scripts/brain_runner,
the release pinned in the image). It works on what brain-runner works on: a
configuration package on disk. The draft's model is written out as the
package (builder.package.write_package) into a temporary folder, and
brain-runner reads it from there, so the bundle is brain-runner's own:

  bundle   brain_runner.bundle.build -> {"header", "sections"}, format
           brain-definitions-bundle, format_version 1, serialised with
           brain_runner.bundle.dumps.
  plan     brain_runner.ontology.plan(cfg, api): what the tenant's ontology
           needs -- types and attributes to create or update, parents to set,
           and what the engine cannot hold yet (not_configured). Planned from
           the model as it stands; only an alias type brain-runner cannot
           place on the tenant is planned under another parent, and its
           description stays the stated one (alias_parents).
  apply    brain_runner.ontology.apply(api, plan): written, refused, skipped.

The API client is brain_runner.api.BrainApi on the Platform's certificate,
built per call by ``api_factory`` (the app passes it in). Nothing here holds
a tenant: the caller passes the one the workspace is linked to.

``Engine`` is the seam the routes use; tests put a stand-in in its place.
"""
from __future__ import annotations

import copy
import hashlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol

from builder import package
from builder import packs as shipped_packs

BUNDLE_FILE = "bundle.json"


def without_engine_types(model: Mapping[str, Any]) -> Mapping[str, Any]:
    """The model the tenant's ontology is planned from: without the types the engine installs in every tenant
    itself (packs.engine_types). The tenant lists them beside the company's types, but they have no schema to
    read (its schema route answers 404) and nothing may be written to them. A workspace started from the tenant
    before the builder left them out holds a copy."""
    engine = shipped_packs.engine_types()
    types = (model.get("ontology") or {}).get("types") or []
    if not any(t.get("key") in engine for t in types if isinstance(t, Mapping)):
        return model
    out = copy.deepcopy(dict(model))
    out["ontology"]["types"] = [t for t in types if not (isinstance(t, Mapping) and t.get("key") in engine)]
    return out


def _is_alias(t: Any) -> bool:
    return isinstance(t, Mapping) and bool(t.get("parent")) and t.get("parent") == t.get("iri")


def alias_parents(model: Mapping[str, Any], unresolved: Iterable[str] = (),
                  core: Optional[Mapping[str, bool]] = None) -> Mapping[str, Any]:
    """The model with the alias types brain-runner cannot place on the tenant under their class's ancestor.

    An alias stands for a catalog class and names that same class as its parent (parent == its own iri: a
    glossary term that *is* the core's Designation, a code symbol that *is* a CodeArtifact). brain-runner places
    such a type under the class when the tenant's store lets tenant types specialise it, or under another
    configured type that stands for the class; otherwise it leaves the type without a parent ("unresolved
    parent"). Such a type takes the class's place: its parent becomes the class's nearest ancestor in the catalogs
    (builder/packs) that another configured type stands for (that type) or that the store lets a tenant type
    specialise (the class itself).

    ``unresolved`` names the types brain-runner leaves without a parent on the tenant (BrainRunnerEngine.plan asks
    brain-runner, over the tenant's core types); only the aliases among them move, every other type keeps the
    parent the model states. ``core`` is the tenant's core types, {CURIE: specializable}, as
    ``GET node-types?include=core`` lists them: the parents a moved alias may take; None: the core catalog's
    classes.

    Only the parent changes. A type's description is planned from the model as it stands (``stated_descriptions``):
    where the type sits must not rewrite what it says."""
    names = set(unresolved)
    types = [t for t in ((model.get("ontology") or {}).get("types") or []) if isinstance(t, Mapping)]
    if not any(_is_alias(t) and t.get("key") in names for t in types):
        return model
    catalog = {c["id"]: c for p in shipped_packs.load() for c in p.get("types") or []}
    if core is None:
        core = {c["id"]: True for p in shipped_packs.load() if p.get("pack") == "core" for c in p.get("types") or []}
    out = copy.deepcopy(dict(model))
    for t in out["ontology"]["types"]:
        if not _is_alias(t) or t.get("key") not in names:
            continue
        others = {o.get("iri"): o.get("key") for o in out["ontology"]["types"]
                  if isinstance(o, Mapping) and o.get("iri") and o.get("key") != t.get("key")}
        queue, seen, parent = list((catalog.get(t["parent"]) or {}).get("parents") or []), set(), None
        while queue and parent is None:
            at = queue.pop(0)
            if at in seen:
                continue
            seen.add(at)
            if at in others:
                parent = others[at]
            elif core.get(at):
                parent = at
            else:
                queue += list((catalog.get(at) or {}).get("parents") or [])
        if parent:
            t["parent"] = parent
        else:
            t.pop("parent", None)
    return out


def for_engine(model: Mapping[str, Any], unresolved: Iterable[str] = (),
               core: Optional[Mapping[str, bool]] = None) -> Mapping[str, Any]:
    """The model as the tenant's ontology is planned from (without_engine_types, alias_parents). Without
    ``unresolved`` no type moves: which alias brain-runner cannot place is the tenant's answer, and
    BrainRunnerEngine.plan asks for it."""
    return alias_parents(without_engine_types(model), unresolved, core)


def tenant_listing(api: Any) -> Optional[tuple[dict[str, bool], dict[str, dict]]]:
    """({core CURIE: specializable}, {name: the tenant's own type}) from ``GET node-types?include=core``, read the
    way brain-runner reads it; None on a store without a type hierarchy (409), where brain-runner sends no parents."""
    try:
        rows = api.get("secured/graph/node-types?include=core")["node_types"]
    except Exception as exc:  # noqa: BLE001 - only a 409 means "no hierarchy"; anything else is the plan's failure
        if getattr(exc, "status", None) == 409:
            return None
        raise
    core = {str(t["id"]): bool(t.get("specializable", True)) for t in rows if t.get("managed_by") == "bundle:core"}
    return core, {str(t["name"]): t for t in rows if t.get("managed_by") != "bundle:core"}


def stated_descriptions(plan: Mapping[str, Any], descriptions: Mapping[str, str],
                        tenant_types: Mapping[str, Mapping[str, Any]]) -> dict:
    """``plan`` with the descriptions of the moved types (``descriptions``: type -> the description brain-runner
    plans from the model as it stands) in place of the ones it planned from their new parent.

    brain-runner prints a type's parent into its description (brain-config.yaml
    ``runner.ontology.type_description``), so a plan over the moved model would rewrite what a moved type says.
    A new type is created with the stated description; a type the tenant has is updated only when its description
    differs from the stated one, brain-runner's own rule. Reparents stay last, as brain-runner orders them."""
    items, reparents = [], []
    for item in plan.get("items") or []:
        name, op = item.get("type"), item.get("op")
        if name in descriptions and op == "update_type":
            continue
        if name in descriptions and op == "create_type":
            item = {**item, "description": descriptions[name]}
        (reparents if op == "reparent_type" else items).append(item)
    for name, text in descriptions.items():
        held = tenant_types.get(name)
        if held is not None and (held.get("description") or "") != text:
            items.append({"op": "update_type", "type": name, "type_id": held["id"],
                          "from": held.get("description") or "", "to": text})
    return {**plan, "items": items + reparents}


class EngineUnavailable(RuntimeError):
    """brain-runner is missing, or too old for what was asked."""


@dataclass
class Compiled:
    files: dict[str, bytes]
    bundle: dict
    bundle_bytes: bytes
    content_hash: str          # the bundle's own: "sha256:..." over its sections
    bundle_hash: str = field(default="")  # sha256 of the bundle's bytes

    def __post_init__(self):
        self.bundle_hash = self.bundle_hash or hashlib.sha256(self.bundle_bytes).hexdigest()


class Engine(Protocol):
    def version(self) -> Optional[str]: ...
    def missing(self) -> Optional[str]: ...
    def compile(self, model: Mapping[str, Any]) -> Compiled: ...
    def plan(self, model: Mapping[str, Any], tenant: str) -> dict: ...
    def apply(self, model: Mapping[str, Any], tenant: str, plan: dict) -> dict: ...
    def run_event(self, model: Mapping[str, Any], **facts: Any) -> dict: ...
    def mapping_plan(self, model: Mapping[str, Any], mapping_id: str, run: Any) -> dict: ...
    def read(self, model: Mapping[str, Any], types: set[str]) -> dict: ...


def _rows(plan: Mapping[str, Any]) -> list[dict]:
    """brain-runner's ontology plan as table rows: element, what happens, details."""
    rows = []
    for item in plan.get("items") or []:
        op = item.get("op", "")
        status = {"create_type": "create", "create_attribute": "create", "update_type": "update",
                  "update_attribute": "update", "reparent_type": "update"}.get(op, op)
        element = item.get("type") or item.get("name") or ""
        if item.get("attribute"):
            element = f"{element}.{item['attribute']}"
        detail = {"create_type": "Typ anlegen", "create_attribute": "Attribut anlegen",
                  "update_type": "Typ ändern", "update_attribute": "Attribut ändern",
                  "reparent_type": "Elterntyp setzen"}.get(op, op)
        rows.append({"element": element, "status": status, "detail": detail})
    for item in plan.get("not_configured") or []:
        element = item.get("type", "")
        if item.get("attribute"):
            element = f"{element}.{item['attribute']}"
        rows.append({"element": element, "status": "not_configured",
                     "detail": "Kann das Brain heute nicht halten" + (" (Werte)" if item.get("enum_values") else "")})
    for item in plan.get("unresolved_parents") or []:
        if isinstance(item, Mapping):
            parent = str(item.get("parent") or "")
            reserved = "reserves" in str(item.get("reason") or "")
            rows.append({"element": str(item.get("type") or ""), "status": "refused",
                         "detail": (f"Elterntyp {parent} ist beim Mandanten für eigene Typen gesperrt" if reserved
                                    else f"Elterntyp {parent} kennt der Mandant nicht")
                                   + "; der Typ wird ohne Elterntyp angelegt"})
        else:
            rows.append({"element": str(item), "status": "refused", "detail": "Elterntyp ist beim Mandanten unbekannt"})
    return rows


def summarise_plan(plan: Mapping[str, Any]) -> dict:
    rows = _rows(plan)
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("create", "update", "refused", "not_configured")}
    return {"rows": rows, "counts": counts}


class BrainRunnerEngine:
    """brain-runner, as installed in the image."""

    def __init__(self, api_factory: Callable[[], Any]):
        self._api_factory = api_factory

    @staticmethod
    def _module(name: str):
        import importlib

        try:
            return importlib.import_module(f"brain_runner.{name}")
        except ImportError as exc:
            raise EngineUnavailable(f"brain-runner ohne «{name}»: {exc}") from exc

    def version(self) -> Optional[str]:
        try:
            import brain_runner
        except ImportError:
            return None
        return getattr(brain_runner, "__version__", None)

    def missing(self) -> Optional[str]:
        """Why a dry run cannot run here, or None."""
        if self.version() is None:
            return "brain-runner ist in diesem Image nicht installiert."
        try:
            self._module("ontology")
        except EngineUnavailable:
            return (f"brain-runner {self.version()} kann die Ontologie noch nicht planen; "
                    "das braucht Version 0.1.0 oder neuer.")
        return None

    def _write(self, model: Mapping[str, Any], root: Path) -> tuple[dict[str, bytes], Path]:
        # The configuration brain-runner reads: the workspace's own parts (package.WORKSPACE_FILE) stay out.
        files = package.write_package(model, workspace_parts=False)
        for path, data in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return files, root / package.PACKAGE_FILE

    def compile(self, model: Mapping[str, Any]) -> Compiled:
        config, bundle_mod = self._module("config"), self._module("bundle")
        with tempfile.TemporaryDirectory(prefix="builder-") as tmp:
            files, manifest = self._write(model, Path(tmp))
            cfg = config.load(manifest)
            built = bundle_mod.build(cfg, Path(tmp) / BUNDLE_FILE)
            text = bundle_mod.dumps(built)
        data = text.encode("utf-8")
        return Compiled(files=files, bundle=built, bundle_bytes=data,
                        content_hash=str(built.get("header", {}).get("content_hash", "")))

    def plan(self, model: Mapping[str, Any], tenant: str) -> dict:
        """brain-runner's plan of the model as it stands. An alias type brain-runner cannot place on the tenant
        (``alias_parents``) is planned under its class's ancestor; its description stays the one the model states."""
        config, ontology = self._module("config"), self._module("ontology")
        api = self._api_factory()
        stated = without_engine_types(model)
        with tempfile.TemporaryDirectory(prefix="builder-") as tmp:
            _, manifest = self._write(stated, Path(tmp) / "stated")
            cfg = config.load(manifest)
            moved = self._placement(stated, cfg, ontology, api)
            if moved is None:
                plan = ontology.plan(cfg, api)
            else:
                placed, descriptions, tenant_types = moved
                _, manifest = self._write(placed, Path(tmp) / "placed")
                plan = stated_descriptions(ontology.plan(config.load(manifest), api), descriptions, tenant_types)
        # The tenant's engine types are not the configuration's to name: not "kann das Brain nicht halten".
        engine = shipped_packs.engine_types()
        plan["not_configured"] = [i for i in plan.get("not_configured") or []
                                  if not (i.get("type") in engine and not i.get("attribute"))]
        return plan

    @staticmethod
    def _placement(stated: Mapping[str, Any], cfg: Any, ontology: Any, api: Any):
        """(the model with the aliases brain-runner cannot resolve moved, the stated descriptions of the moved
        types, the tenant's own types by name), or None when nothing moves: no alias, a brain-runner without type
        parents, a store without a hierarchy, or every alias resolved as the model states it.

        Which parent brain-runner resolves is brain-runner's own answer (``ontology.resolve_parents``) over the
        tenant's core types (``tenant_listing``): a configured type's name, a core type tenant types may
        specialise, or another configured type carrying the CURIE as its iri."""
        types = (stated.get("ontology") or {}).get("types") or []
        if not any(_is_alias(t) for t in types):
            return None
        resolve, wanted = getattr(ontology, "resolve_parents", None), getattr(ontology, "wanted", None)
        if resolve is None or wanted is None:
            return None
        listing = tenant_listing(api)
        if listing is None:
            return None
        core, tenant_types = listing
        want = wanted(cfg)
        unresolved = {name for name, (kind, _) in resolve(want, core).items() if kind == "unresolved"}
        placed = alias_parents(stated, unresolved, core)
        before = {t.get("key"): t.get("parent") for t in types if isinstance(t, Mapping)}
        moved = sorted(str(t["key"]) for t in placed["ontology"]["types"]
                       if isinstance(t, Mapping) and before.get(t.get("key")) != t.get("parent"))
        if not moved:
            return None
        return placed, {k: want[k]["description"] for k in moved if k in want}, tenant_types

    def apply(self, model: Mapping[str, Any], tenant: str, plan: dict) -> dict:
        ontology = self._module("ontology")
        return ontology.apply(self._api_factory(), plan)

    def run_event(self, model: Mapping[str, Any], **facts: Any) -> dict:
        """The publish's RunEvent on the tenant, through the Brain API (builder.run_event)."""
        from builder import run_event

        return run_event.write(self._api_factory(), model, **facts)

    def mapping_plan(self, model: Mapping[str, Any], mapping_id: str, run: Any) -> dict:
        """brain-runner's plan of one mapping's run against the tenant (step B8): diff.plan over a snapshot of
        the types it touches. Nothing is written. The package holds only this mapping, so a mapping still
        being built elsewhere in the workspace does not stop the configuration from loading."""
        import copy

        config, diff = self._module("config"), self._module("diff")
        snapshot, state = self._module("snapshot"), self._module("state")
        only = copy.deepcopy(dict(model))
        only["mappings"] = [m for m in model.get("mappings") or [] if (m.get("definition") or {}).get("id") == mapping_id]
        with tempfile.TemporaryDirectory(prefix="builder-") as tmp:
            _, manifest = self._write(only, Path(tmp))
            cfg = config.load(manifest)
            node_types, schema_types = diff.snapshot_types(cfg, [mapping_id])
            snap = snapshot.read(self._api_factory(), node_types, schema_types)
            return diff.plan(cfg, {mapping_id: run}, snap, state.State(None))

    def read(self, model: Mapping[str, Any], types: set[str]) -> dict:
        """What the tenant holds of ``types`` (step B10): brain-runner's snapshot as plain data -- nodes with
        their facts valid now, and every edge between them. Read only."""
        snapshot = self._module("snapshot")
        snap = snapshot.read(self._api_factory(), set(types))
        return {"taken_at": snap.taken_at,
                "nodes": [{"id": n.id, "type": n.type, "name": n.name, "description": n.description,
                           "facts": {k: {"value": r.get("value"), "valid_from": r.get("valid_from"),
                                         "created_at": r.get("created_at")} for k, r in n.facts.items()}}
                          for n in snap.nodes.values()],
                "edges": [e for e in snap.edges if e["lo"] in snap.nodes and e["hi"] in snap.nodes]}
