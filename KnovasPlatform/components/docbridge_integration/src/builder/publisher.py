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
           and what the engine cannot hold yet (not_configured).
  apply    brain_runner.ontology.apply(api, plan): written, refused, skipped.

The API client is brain_runner.api.BrainApi on the Platform's certificate,
built per call by ``api_factory`` (the app passes it in). Nothing here holds
a tenant: the caller passes the one the workspace is linked to.

``Engine`` is the seam the routes use; tests put a stand-in in its place.
"""
from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol

from builder import package

BUNDLE_FILE = "bundle.json"


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
    for name in plan.get("unresolved_parents") or []:
        rows.append({"element": str(name), "status": "refused", "detail": "Elterntyp ist beim Mandanten unbekannt"})
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
        files = package.write_package(model)
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
        config, ontology = self._module("config"), self._module("ontology")
        with tempfile.TemporaryDirectory(prefix="builder-") as tmp:
            _, manifest = self._write(model, Path(tmp))
            return ontology.plan(config.load(manifest), self._api_factory())

    def apply(self, model: Mapping[str, Any], tenant: str, plan: dict) -> dict:
        ontology = self._module("ontology")
        return ontology.apply(self._api_factory(), plan)
