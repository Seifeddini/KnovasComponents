"""Write the builder's reference packs from a KnowledgeBase checkout.

The builder shows a company's types under the Brain's own core types and the
classes of the industry packs. It does not invent them: this script reads

  - the engine's core binding (the 80 core types the graph store knows,
    with their parents and labels):
      knovas-software/app/src/services/brain_store/ontology/core_binding.json
  - the SeDM domain packs (classes, labels, parents):
      DataModels/Semantic Data Model (SeDM)/ontology/domains/<pack>/<pack>.ttl

and writes one class catalog per pack to src/builder/packs/<pack>/classes.json.
Run it again when the core or a pack changes; the files are data and are
committed. The terms a pack brings into a workspace are beside them, in
pack.json (see builder_pack_from_package.py).

    python scripts/builder_packs_from_sedm.py ../../../../KnowledgeBase
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
OUT = HERE / "src" / "builder" / "packs"
CORE_BINDING = Path("knovas-software/app/src/services/brain_store/ontology/core_binding.json")
DOMAINS = Path("DataModels/Semantic Data Model (SeDM)/ontology/domains")

# One class block of the domain Turtle files: "prefix:Name a owl:Class ; ... ."
_CLASS = re.compile(r"^(?P<id>[a-z]+:[A-Za-z0-9]+) a owl:Class\s*;(?P<body>.*?)\s\.\s*$", re.S | re.M)
_LABELS = re.compile(r'rdfs:label\s+((?:"[^"]*"@[a-z]+\s*,?\s*)+)')
_LABEL = re.compile(r'"([^"]*)"@(de|en)\b')
_PARENT = re.compile(r"rdfs:subClassOf\s+([a-z]+:[A-Za-z0-9]+)")


def core_pack(root: Path) -> dict:
    binding = json.loads((root / CORE_BINDING).read_text(encoding="utf-8-sig"))
    types = [
        {
            "id": t["id"],
            "name": t["name"],
            "parents": list(t.get("parents") or []),
            "label": {lang: t["labels"][lang] for lang in ("de", "en") if lang in (t.get("labels") or {})},
            "abstract": bool(t.get("abstract")),
        }
        for t in sorted(binding["node_types"], key=lambda t: t["id"])
    ]
    return {"pack": "core", "name": {"de": "Kern", "en": "Core"}, "source": CORE_BINDING.as_posix(),
            "profile_hash": binding.get("profile_hash"), "types": types}


def domain_pack(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    types = []
    for match in _CLASS.finditer(text):
        body = match.group("body")
        statement = _LABELS.search(body)
        labels = {lang: value for value, lang in _LABEL.findall(statement.group(1))} if statement else {}
        types.append({
            "id": match.group("id"),
            "name": match.group("id").split(":", 1)[1],
            "parents": _PARENT.findall(body),
            "label": labels,
            "abstract": False,
        })
    name = path.stem
    return {"pack": name, "name": {"de": name.capitalize(), "en": name.capitalize()},
            "source": (DOMAINS / path.parent.name / path.name).as_posix(),
            "types": sorted(types, key=lambda t: t["id"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("knowledgebase", type=Path, help="Path of a KnowledgeBase checkout")
    args = parser.parse_args()
    root = args.knowledgebase.resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    packs = [core_pack(root)]
    for folder in sorted((root / DOMAINS).iterdir()):
        main_file = folder / f"{folder.name}.ttl"
        if main_file.is_file():
            packs.append(domain_pack(main_file))
    for pack in packs:
        target = OUT / pack["pack"] / "classes.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(pack, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{target.relative_to(HERE)}: {len(pack['types'])} types")
    return 0


if __name__ == "__main__":
    sys.exit(main())
