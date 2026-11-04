"""A workspace from the definitions the tenant's views read.

A tenant whose Brain was published from a configuration package -- Client
Zero's from KnowledgeBase, or one the builder published -- carries that
configuration in the definitions bundle the views read (graph_views.bundle,
KB-7: types, relations, descriptions, processes, spines, pages, owners,
profiles and health). The graph itself holds node types and edges, not
processes or hierarchies, so a workspace that starts from the tenant takes
its definitions from here (builder.tenant_ontology).

The bundle keeps processes, spines and pages in brain-runner's generated form
and the rest as the package's own YAML documents. This module writes a package
from it -- the generated parts turned back into the form the package writes
(processes.from_generated, views.spines_from_generated,
views.pages_from_generated) -- and reads that package as it reads an upload,
so the model has the exact shape every other workspace has. What the bundle
does not carry stays absent: mappings, edit sources and node descriptions.
The workspace's name is its own; the company is the bundle's.
"""
from __future__ import annotations

from typing import Any, Mapping

from builder import package, processes, views

ONTOLOGY_PATHS = {part: f"ontology/{part.replace('_', '-')}.yaml"
                  for part in ("terms", "relations", "descriptions", "node_descriptions")}
#: The bundle's document sections, each one package file (package.PART_PATHS).
PARTS = ("processes", "spines", "pages", "owners", "health")


def _section(bundle: Any, name: str) -> Any:
    value = bundle.section(name)
    return value if isinstance(value, (Mapping, list)) else {}


def company_of(name: str, bundle: Any) -> dict:
    """The company as the bundle's header names it; the workspace name where the header does not.

    brain-runner 0.1.0 and later put the manifest's whole company under ``label``, earlier releases
    only its key beside ``name`` and ``kind``.
    """
    header = getattr(bundle, "header", None) or {}
    label = header.get("label")
    found = dict(label) if isinstance(label, Mapping) else {"key": label}
    company = {"key": str(found.get("key") or package.company_key(name)),
               "name": str(found.get("name") or header.get("name") or name).strip()}
    kind = found.get("kind") or header.get("kind")
    if kind:
        company["kind"] = str(kind)
    return company


def files_from_bundle(name: str, bundle: Any) -> dict[str, bytes]:
    """The bundle as a package's files, as ``package.read_package`` reads them."""
    manifest: dict[str, Any] = {
        "version": 1,
        "company": company_of(name, bundle),
        "ontology": dict(ONTOLOGY_PATHS),
        **{part: package.PART_PATHS[part] for part in PARTS},
        "profiles": package.PART_PATHS["profiles"],
    }
    health = _section(bundle, "health")
    documents = {
        "processes": processes.from_generated(_section(bundle, "processes")),
        "spines": views.spines_from_generated(_section(bundle, "spines")),
        "pages": views.pages_from_generated(_section(bundle, "pages")),
        "owners": dict(_section(bundle, "owners")),
        "health": dict(health) if isinstance(health, Mapping) and health.get("types") is not None else {"types": {}},
    }
    files = {
        package.PACKAGE_FILE: package.dump_yaml(manifest),
        ONTOLOGY_PATHS["terms"]: package.dump_yaml(dict(_section(bundle, "types")) or {"node_types": []}),
        ONTOLOGY_PATHS["relations"]: package.dump_yaml(dict(_section(bundle, "relations")) or {"relations": []}),
        ONTOLOGY_PATHS["descriptions"]: package.dump_yaml(dict(_section(bundle, "descriptions"))),
        ONTOLOGY_PATHS["node_descriptions"]: package.dump_yaml({}),
    }
    files.update({package.PART_PATHS[part]: package.dump_yaml(documents[part]) for part in PARTS})
    profiles = _section(bundle, "profiles")
    items = (profiles.items() if isinstance(profiles, Mapping)
             else ((str(p.get("id")), p) for p in profiles if isinstance(p, Mapping)))
    for pid, definition in sorted(items):
        if not isinstance(definition, Mapping):
            continue
        files[f"profiles/{pid}.yaml"] = package.dump_yaml(
            dict(definition) if "id" in definition else {"id": pid, **definition})
    return files


def model_from_bundle(name: str, bundle: Any) -> package.ImportResult:
    """The workspace model of ``bundle``, read as an uploaded package is."""
    return package.read_package(files_from_bundle(name, bundle))
