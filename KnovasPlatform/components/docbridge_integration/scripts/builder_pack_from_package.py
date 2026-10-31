"""Write a pack's terms from the terms of a configuration package.

A pack (src/builder/packs/<id>/pack.json) is what a workspace can start from:
types and relations in the workspace schema, read-only in the workspace. This
script takes them from a package whose terms already carry a level for them
(for example ``level: pack``): every type at that level, and every relation
between two of those types, cut to them.

    python scripts/builder_pack_from_package.py <package folder> --id software \\
        --level pack --version 1.0 --name-de Software --name-en Software

The output is data and is committed; review it before you do.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "src"))

from builder import package  # noqa: E402

OUT = HERE / "src" / "builder" / "packs"
#: What a pack term keeps of a package's type; the rest (reasons, technical notes,
#: extra keys) belongs to the company the package came from.
TYPE_FIELDS = ("key", "parent", "iri", "meta", "state_attribute", "id_prefixes", "label", "description", "example",
               "not_to_confuse", "attributes")
RELATION_FIELDS = ("key", "family", "iri", "inverse", "label", "sentence", "example", "not_to_confuse")
ATTRIBUTE_FIELDS = ("name", "datatype", "required", "enum_values", "enum_labels", "target", "label",
                    "description")


def pack_from_model(model: dict, *, pack_id: str, level: str, version: str, name: dict, description: dict) -> dict:
    types = []
    for item in model["ontology"]["types"]:
        if item.get("level") != level:
            continue
        term = {k: copy.deepcopy(item[k]) for k in TYPE_FIELDS if k in item and item[k] not in (None, [], {}, "")}
        term["attributes"] = [
            {**{k: copy.deepcopy(a[k]) for k in ATTRIBUTE_FIELDS if k in a}, "level": "pack"}
            for a in item.get("attributes") or []
        ]
        types.append({**term, "level": "pack", "level_label": "pack", "pack": pack_id})
    keys = {t["key"] for t in types}
    relations = []
    for item in model["ontology"]["relations"]:
        domain = [d for d in item.get("domain") or [] if d in keys]
        range_ = [r for r in item.get("range") or [] if r in keys]
        if not domain or not range_:
            continue
        term = {k: copy.deepcopy(item[k]) for k in RELATION_FIELDS if k in item and item[k] not in (None, [], {}, "")}
        relations.append({**term, "domain": domain, "range": range_, "level": "pack", "pack": pack_id})
    return {"id": pack_id, "version": version, "name": name, "description": description,
            "types": types, "relations": relations, "process_templates": []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("package", type=Path, help="Folder with brain-config.yaml")
    parser.add_argument("--id", required=True)
    parser.add_argument("--level", default="pack", help="The model level the pack's types have in the package")
    parser.add_argument("--version", required=True)
    parser.add_argument("--name-de", required=True)
    parser.add_argument("--name-en", required=True)
    parser.add_argument("--description-de", default="")
    parser.add_argument("--description-en", default="")
    args = parser.parse_args()
    model = package.read_package(package.files_from_directory(args.package)).model
    pack = pack_from_model(model, pack_id=args.id, level=args.level, version=args.version,
                           name={"de": args.name_de, "en": args.name_en},
                           description={"de": args.description_de, "en": args.description_en})
    target = OUT / args.id / "pack.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(pack, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{target.relative_to(HERE)}: {len(pack['types'])} types, {len(pack['relations'])} relations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
