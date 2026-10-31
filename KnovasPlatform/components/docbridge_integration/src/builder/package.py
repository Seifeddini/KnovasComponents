"""Configuration package <-> workspace model, without loss.

A configuration package is ``brain-config.yaml`` and the files it lists:
the ontology (terms, relations, descriptions, node descriptions), processes,
spines, pages, owners, edit sources, health, and one file per mapping and per
profile. This module turns a package into the workspace model and the model
back into the package.

What "without loss" means here: importing a package and exporting the model
gives files that load to the same data -- the same keys, values and list
order. Comments and the original key order are not kept; the export writes
keys sorted, so the same model always gives the same bytes.

The model normalises what the editors work on -- types, their attributes and
relations, each with level, labels in German and English, description,
example, not-to-confuse and meta -- by joining the terms file with the
descriptions file. Every key the model does not name is kept beside the item
(``extra``, ``description_extra``) and written back where it came from. The
other parts are kept as the documents they are; the steps that build their
editors normalise them.

Nothing here knows a company: level names, paths and keys all come from the
package itself.
"""
from __future__ import annotations

import copy
import fnmatch
import hashlib
import io
import json
import posixpath
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

MODEL_VERSION = 1
PACKAGE_FILE = "brain-config.yaml"

#: The ontology's parts inside ``ontology:`` of brain-config.yaml.
ONTOLOGY_PARTS = ("terms", "relations", "descriptions", "node_descriptions")
#: Top-level parts that are one YAML document each, kept as such in the model.
DOCUMENT_PARTS = ("processes", "spines", "pages", "owners", "edit_sources", "health")
#: Top-level parts that are a glob of YAML files, one definition each.
COLLECTION_PARTS = ("mappings", "profiles")

#: Level names in the package -> the builder's three levels. Any other name is
#: the company's own level (the package names it after the company).
_LEVELS = {"euo": "core", "core": "core", "pack": "pack"}
_LEVEL_LABELS = {"core": "euo", "pack": "pack"}

# Limits for an uploaded zip. A configuration package is a few hundred
# kilobytes of YAML; these leave room for a repository folder exported whole.
MAX_ZIP_ENTRIES = 20_000
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_YAML_BYTES = 8 * 1024 * 1024

# A fixed timestamp for every zip entry, so an export is byte-stable.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


class PackageError(ValueError):
    """The package cannot be imported. ``message`` is German UI text."""

    def __init__(self, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.details = list(details or [])


@dataclass
class ImportResult:
    model: dict
    summary: dict
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- YAML


def _without_timestamps(resolvers):
    """A copy of a resolver table without the timestamp rule.

    A copy, never an edit: PyYAML's loaders and dumpers share one table, and
    changing it in place would change YAML for every caller in the process.
    """
    return {
        first: [(tag, regexp) for tag, regexp in rules if tag != "tag:yaml.org,2002:timestamp"]
        for first, rules in resolvers.items()
    }


class _Loader(yaml.SafeLoader):
    """SafeLoader that keeps dates as the text they are, so the model stays JSON."""

    yaml_implicit_resolvers = _without_timestamps(yaml.SafeLoader.yaml_implicit_resolvers)


class _Dumper(yaml.SafeDumper):
    """SafeDumper without anchors, writing date-like text unquoted.

    Unquoted, because that is how a date was written before it was read: a
    date in the package comes back as a date for every other YAML reader.
    The one case this changes is text that looked like a date and was quoted;
    configuration packages write dates and durations unquoted.
    """

    yaml_implicit_resolvers = _without_timestamps(yaml.SafeDumper.yaml_implicit_resolvers)

    def ignore_aliases(self, data):  # noqa: D401 - yaml hook
        return True


def load_yaml(data: bytes | str, name: str = "") -> Any:
    try:
        return yaml.load(data, Loader=_Loader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (Zeile {mark.line + 1})" if mark is not None else ""
        raise PackageError(f"{name or 'Eine Datei'} ist kein gültiges YAML{where}.") from exc


def dump_yaml(data: Any) -> bytes:
    text = yaml.dump(
        data, Dumper=_Dumper, sort_keys=True, allow_unicode=True,
        default_flow_style=False, width=4096,
    )
    return text.encode("utf-8")


def canonical_json(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def content_hash(model: Mapping[str, Any]) -> str:
    """sha256 of the model in canonical JSON: equal models, equal hashes."""
    return hashlib.sha256(canonical_json(model)).hexdigest()


# ---------------------------------------------------------------- reading


def files_from_zip(data: bytes) -> dict[str, bytes]:
    """The YAML files of a zip, keyed by their path relative to brain-config.yaml.

    The zip may hold the package at its root or inside folders (a repository
    exported whole); the one brain-config.yaml decides where the package is.
    Only YAML files under it are read.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PackageError("Die Datei ist kein ZIP-Archiv.") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise PackageError(f"Das Archiv hat mehr als {MAX_ZIP_ENTRIES} Einträge.")
        if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
            raise PackageError("Das Archiv ist entpackt zu groß.")
        configs = [
            info for info in infos
            if not info.is_dir() and posixpath.basename(info.filename) == PACKAGE_FILE
        ]
        if not configs:
            raise PackageError(f"Im Archiv fehlt die Datei {PACKAGE_FILE}.")
        if len(configs) > 1:
            raise PackageError(
                f"Das Archiv enthält mehrere {PACKAGE_FILE}. Bitte nur ein Paket hochladen.",
                [info.filename for info in configs],
            )
        root = posixpath.dirname(configs[0].filename)
        prefix = f"{root}/" if root else ""
        files: dict[str, bytes] = {}
        for info in infos:
            name = info.filename
            if info.is_dir() or not name.startswith(prefix):
                continue
            relative = name[len(prefix):]
            if not relative.endswith((".yaml", ".yml")):
                continue
            if _unsafe(relative):
                raise PackageError("Das Archiv enthält einen unzulässigen Pfad.", [name])
            if info.file_size > MAX_YAML_BYTES:
                raise PackageError(f"{relative} ist größer als {MAX_YAML_BYTES // (1024 * 1024)} MB.")
            try:
                # Read no more than the header claims: a lying header cannot
                # make one entry larger than the limit.
                with archive.open(info) as entry:
                    data = entry.read(MAX_YAML_BYTES + 1)
            except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, EOFError) as exc:
                # RuntimeError: encrypted; NotImplementedError: unknown compression.
                raise PackageError(f"{relative} lässt sich nicht entpacken.") from exc
            if len(data) > MAX_YAML_BYTES:
                raise PackageError(f"{relative} ist größer als {MAX_YAML_BYTES // (1024 * 1024)} MB.")
            files[relative] = data
        return files


def files_from_directory(root: Path) -> dict[str, bytes]:
    """The YAML files under ``root`` (a checked-out package), keyed by relative path."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix in (".yaml", ".yml")
    }


def _unsafe(path: str) -> bool:
    parts = path.split("/")
    return path.startswith("/") or "\\" in path or ".." in parts or (len(parts[0]) == 2 and parts[0][1] == ":")


def _glob(files: Mapping[str, bytes], pattern: str) -> list[str]:
    """Paths matching a brain-config glob, sorted. ``*`` stays inside one folder."""
    folder, _, name = pattern.rpartition("/")
    return sorted(
        path for path in files
        if posixpath.dirname(path) == folder and fnmatch.fnmatchcase(posixpath.basename(path), name)
    )


def read_package(files: Mapping[str, bytes]) -> ImportResult:
    """Build the workspace model from a package's files."""
    if PACKAGE_FILE not in files:
        raise PackageError(f"Im Paket fehlt die Datei {PACKAGE_FILE}.")
    manifest = load_yaml(files[PACKAGE_FILE], PACKAGE_FILE)
    if not isinstance(manifest, dict):
        raise PackageError(f"{PACKAGE_FILE} ist keine Zuordnung von Schlüsseln.")
    warnings: list[str] = []

    def document(path: Any, part: str) -> Any:
        if not isinstance(path, str):
            raise PackageError(f"{PACKAGE_FILE}: «{part}» nennt keinen Dateipfad.")
        if path not in files:
            raise PackageError(f"Die Datei {path} fehlt im Paket ({part} in {PACKAGE_FILE}).")
        return load_yaml(files[path], path)

    ontology_paths = manifest.get("ontology") or {}
    if not isinstance(ontology_paths, dict):
        raise PackageError(f"{PACKAGE_FILE}: «ontology» ist keine Zuordnung.")
    ontology_docs = {
        part: document(path, f"ontology.{part}") for part, path in ontology_paths.items()
    }

    model: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "company": copy.deepcopy(manifest.get("company") or {}),
        "package": {"manifest": {k: copy.deepcopy(v) for k, v in manifest.items() if k != "company"}},
        "ontology": _read_ontology(ontology_docs, warnings),
        "sources": [],
    }
    for part in DOCUMENT_PARTS:
        model[part] = document(manifest[part], part) if part in manifest else None
    for part in COLLECTION_PARTS:
        pattern = manifest.get(part)
        if pattern is None:
            model[part] = []
            continue
        if not isinstance(pattern, str):
            raise PackageError(f"{PACKAGE_FILE}: «{part}» nennt kein Dateimuster.")
        paths = _glob(files, pattern)
        if not paths:
            warnings.append(f"Das Muster {pattern} ({part}) findet keine Datei.")
        model[part] = [{"file": path, "definition": load_yaml(files[path], path)} for path in paths]

    used = {PACKAGE_FILE, *(p for p in ontology_paths.values())}
    used |= {manifest[p] for p in DOCUMENT_PARTS if p in manifest}
    used |= {item["file"] for part in COLLECTION_PARTS for item in model[part]}
    ignored = sorted(set(files) - used)

    from builder import schema  # late: schema loads a file

    problems = schema.problems(model)
    if problems:
        raise PackageError("Das Paket passt nicht zum Arbeitsbereich-Modell.", problems[:20])

    return ImportResult(model=model, summary=summarise(model, warnings, ignored=len(ignored)), warnings=warnings)


# ---------------------------------------------------------------- ontology in


def _level(label: Any) -> str:
    return _LEVELS.get(str(label).lower(), "company") if label is not None else "company"


def _take(source: dict, target: dict, pairs) -> None:
    for src, dst in pairs:
        if src in source:
            target[dst] = source.pop(src)


def _read_description(raw: Any, entry: dict, *, text_key: str = "text") -> None:
    """Move a descriptions-file entry onto a model item."""
    if not isinstance(raw, dict):
        if raw is not None:
            entry["description_extra"] = {"_value": raw}
        return
    raw = dict(raw)
    label = {lang: raw.pop(lang) for lang in ("de", "en") if lang in raw}
    if label:
        entry["label"] = label
    if text_key in raw:
        entry["description"] = {"en": raw.pop(text_key)}
    _take(raw, entry, (("example", "example"), ("confuse", "not_to_confuse")))
    if "meta" in raw and raw["meta"] == entry.get("meta"):
        raw.pop("meta")  # the type's own flag; written back from there
    if raw:
        entry["description_extra"] = raw


def _read_attribute(raw: dict, owner_level: str, desc: dict, enums: dict) -> dict:
    raw = dict(raw)
    entry: dict[str, Any] = {"name": raw.pop("name", None)}
    if "level" in raw:
        entry["level_label"] = raw.pop("level")
        entry["level"] = _level(entry["level_label"])
    else:
        entry["level"] = owner_level
    _take(raw, entry, (("datatype", "datatype"), ("required", "required"),
                       ("enum_values", "enum_values"), ("target", "target"), ("description", "note")))
    if raw:
        entry["extra"] = raw
    name = entry["name"]
    if name in desc:
        _read_description(desc.pop(name), entry)
    if name in enums:
        entry["enum_labels"] = enums.pop(name)
    return entry


def _read_ontology(docs: dict[str, Any], warnings: list[str]) -> dict:
    terms = copy.deepcopy(docs.get("terms") or {})
    relations_doc = copy.deepcopy(docs.get("relations") or {})
    rest = copy.deepcopy(docs.get("descriptions") or {})
    if not all(isinstance(d, dict) for d in (terms, relations_doc, rest)):
        raise PackageError("Eine Ontologie-Datei ist keine Zuordnung von Schlüsseln.")

    desc_types = rest.get("types") or {}
    desc_attrs = rest.get("attributes") or {}
    desc_enums = rest.get("enums") or {}
    desc_relations = rest.get("relations") or {}

    types = []
    for raw in terms.pop("node_types", None) or []:
        raw = dict(raw)
        key = raw.pop("name", None)
        entry: dict[str, Any] = {"key": key}
        if "level" in raw:
            entry["level_label"] = raw.pop("level")
        entry["level"] = _level(entry.get("level_label"))
        _take(raw, entry, (("parent", "parent"), ("sedm_iri", "iri"), ("meta", "meta"),
                           ("state_attribute", "state_attribute"), ("id_prefixes", "id_prefixes"),
                           ("aligns_with", "aligns_with"), ("reason", "reason"), ("retired", "retired")))
        attributes = raw.pop("attributes", None)
        if raw:
            entry["extra"] = raw
        if key in desc_types:
            _read_description(desc_types.pop(key), entry)
        type_desc = desc_attrs.get(key) or {}
        type_enums = desc_enums.get(key) or {}
        entry["attributes"] = [
            _read_attribute(a, entry["level"], type_desc, type_enums) for a in attributes or []
        ]
        types.append(entry)

    common = None
    if "common_attributes" in terms:
        common_desc = desc_attrs.get("_common") or {}
        common_enums = desc_enums.get("_common") or {}
        common = [_read_attribute(a, "core", common_desc, common_enums)
                  for a in terms.pop("common_attributes") or []]

    relations = []
    for raw in relations_doc.pop("relations", None) or []:
        raw = dict(raw)
        entry = {"key": raw.pop("name", None)}
        _take(raw, entry, (("family", "family"), ("sedm_iri", "iri"), ("domain", "domain"),
                           ("range", "range"), ("inverse", "inverse"), ("reason", "reason"),
                           ("retired", "retired")))
        if "level" in raw:
            entry["level_label"] = raw.pop("level")
            entry["level"] = _level(entry["level_label"])
        else:
            # The package gives relations no level: one that maps to a core
            # relation (an iri) is core, the rest are the company's own.
            entry["level"] = "core" if entry.get("iri") else "company"
        if raw:
            entry["extra"] = raw
        if entry["key"] in desc_relations:
            _read_description(desc_relations.pop(entry["key"]), entry, text_key="sentence")
            if "description" in entry:
                entry["sentence"] = entry.pop("description")["en"]
        relations.append(entry)

    _drop_empty(rest)
    if rest.get("types"):
        warnings.append(f"Beschreibungen für {len(rest['types'])} unbekannte Typen bleiben unverändert erhalten.")

    undescribed = [t["key"] for t in types if not (t.get("description") or {}).get("en")]
    if undescribed:
        warnings.append(f"{len(undescribed)} Typen haben keine Beschreibung.")
    unbounded = [r["key"] for r in relations if not r.get("domain") or not r.get("range")]
    if unbounded:
        warnings.append(f"{len(unbounded)} Beziehungen fehlt der Definitions- oder Wertebereich.")

    return {
        "terms_header": terms,
        "relations_header": relations_doc,
        "descriptions_rest": rest,
        "common_attributes": common,
        "types": types,
        "relations": relations,
        "documents": {
            part: copy.deepcopy(doc) for part, doc in docs.items()
            if part not in ("terms", "relations", "descriptions")
        },
    }


def _drop_empty(node: dict) -> None:
    for key in list(node):
        value = node[key]
        if isinstance(value, dict):
            _drop_empty(value)
            if not value:
                del node[key]


# ---------------------------------------------------------------- writing


def _level_label(item: Mapping[str, Any], company: Mapping[str, Any]) -> str:
    if item.get("level_label") is not None:
        return item["level_label"]
    if item.get("level") in _LEVEL_LABELS:
        return _LEVEL_LABELS[item["level"]]
    return str(company.get("key") or "company").lower()


def _put(target: dict, source: Mapping[str, Any], pairs) -> None:
    for src, dst in pairs:
        if src in source:
            target[dst] = copy.deepcopy(source[src])


def _description_out(item: Mapping[str, Any], *, text_key: str = "text", text: Any = None) -> dict:
    out: dict[str, Any] = {}
    out.update(copy.deepcopy(item.get("label") or {}))
    if text is not None:
        out[text_key] = text
    elif "description" in item and "en" in item["description"]:
        out[text_key] = item["description"]["en"]
    _put(out, item, (("example", "example"), ("not_to_confuse", "confuse")))
    return out


def _attribute_out(attr: Mapping[str, Any]) -> dict:
    out: dict[str, Any] = {"name": attr["name"]}
    if "level_label" in attr:
        out["level"] = attr["level_label"]
    _put(out, attr, (("datatype", "datatype"), ("required", "required"),
                     ("enum_values", "enum_values"), ("target", "target"), ("note", "description")))
    out.update(copy.deepcopy(attr.get("extra") or {}))
    return out


def _merge(target: dict, extra: Mapping[str, Any]) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def _write_ontology(ontology: Mapping[str, Any], company: Mapping[str, Any]) -> dict[str, Any]:
    terms = copy.deepcopy(ontology.get("terms_header") or {})
    desc_types: dict[str, Any] = {}
    desc_attrs: dict[str, Any] = {}
    desc_enums: dict[str, Any] = {}
    desc_relations: dict[str, Any] = {}

    def describe_attributes(owner: str, attributes) -> None:
        for attr in attributes:
            text = _description_out(attr)
            if text or "description_extra" in attr:
                desc_attrs.setdefault(owner, {})[attr["name"]] = _merge(text, attr.get("description_extra") or {})
            if "enum_labels" in attr:
                desc_enums.setdefault(owner, {})[attr["name"]] = copy.deepcopy(attr["enum_labels"])

    if ontology.get("common_attributes") is not None:
        terms["common_attributes"] = [_attribute_out(a) for a in ontology["common_attributes"]]
        describe_attributes("_common", ontology["common_attributes"])

    node_types = []
    for item in ontology.get("types") or []:
        out: dict[str, Any] = {"name": item["key"], "level": _level_label(item, company)}
        _put(out, item, (("parent", "parent"), ("iri", "sedm_iri"), ("meta", "meta"),
                         ("state_attribute", "state_attribute"), ("id_prefixes", "id_prefixes"),
                         ("aligns_with", "aligns_with"), ("reason", "reason"), ("retired", "retired")))
        out["attributes"] = [_attribute_out(a) for a in item.get("attributes") or []]
        out.update(copy.deepcopy(item.get("extra") or {}))
        node_types.append(out)

        text = _description_out(item)
        if text or "description_extra" in item:
            if "meta" in item:
                text["meta"] = item["meta"]
            desc_types[item["key"]] = _merge(text, item.get("description_extra") or {})
        describe_attributes(item["key"], item.get("attributes") or [])
    terms["node_types"] = node_types

    relations_doc = copy.deepcopy(ontology.get("relations_header") or {})
    relation_list = []
    for item in ontology.get("relations") or []:
        out = {"name": item["key"]}
        _put(out, item, (("family", "family"), ("iri", "sedm_iri"), ("domain", "domain"),
                         ("range", "range"), ("inverse", "inverse"), ("reason", "reason"),
                         ("retired", "retired")))
        if "level_label" in item:
            out["level"] = item["level_label"]
        out.update(copy.deepcopy(item.get("extra") or {}))
        relation_list.append(out)
        text = _description_out(item, text_key="sentence", text=item.get("sentence"))
        if text or "description_extra" in item:
            desc_relations[item["key"]] = _merge(text, item.get("description_extra") or {})
    relations_doc["relations"] = relation_list

    descriptions = copy.deepcopy(ontology.get("descriptions_rest") or {})
    for name, part in (("types", desc_types), ("attributes", desc_attrs),
                       ("enums", desc_enums), ("relations", desc_relations)):
        if part:
            _merge(descriptions.setdefault(name, {}), part)

    docs = {"terms": terms, "relations": relations_doc, "descriptions": descriptions}
    docs.update(copy.deepcopy(ontology.get("documents") or {}))
    return docs


def write_package(model: Mapping[str, Any]) -> dict[str, bytes]:
    """The package's files from the model, as bytes keyed by relative path."""
    manifest = copy.deepcopy(model["package"]["manifest"])
    if model.get("company"):
        manifest["company"] = copy.deepcopy(model["company"])
    files: dict[str, bytes] = {PACKAGE_FILE: dump_yaml(manifest)}

    ontology_docs = _write_ontology(model["ontology"], model.get("company") or {})
    for part, path in (manifest.get("ontology") or {}).items():
        if part in ontology_docs:
            files[path] = dump_yaml(ontology_docs[part])
    for part in DOCUMENT_PARTS:
        if part in manifest and model.get(part) is not None:
            files[manifest[part]] = dump_yaml(model[part])
    for part in COLLECTION_PARTS:
        for item in model.get(part) or []:
            files[item["file"]] = dump_yaml(item["definition"])
    return files


def company_key(name: str) -> str:
    """A key for a company from its name: ASCII capitals, digits and hyphens."""
    folded = unicodedata.normalize("NFKD", name.replace("ß", "ss")).encode("ascii", "ignore").decode("ascii")
    words = "".join(c if c.isalnum() else " " for c in folded.upper()).split()
    return "-".join(words)[:40].strip("-") or "COMPANY"


def blank_package(name: str) -> dict[str, bytes]:
    """The files of a package that holds only the company: where a workspace
    created in the builder, rather than imported, starts. Read with
    ``read_package`` like any other, so it has the model's exact shape and
    exports to a package the Brain's tools read."""
    paths = {part: f"ontology/{part}.yaml" for part in ("terms", "relations", "descriptions")}
    manifest = {
        "version": 1,
        "company": {"key": company_key(name), "name": name.strip()},
        "ontology": paths,
    }
    return {
        PACKAGE_FILE: dump_yaml(manifest),
        paths["terms"]: dump_yaml({"node_types": []}),
        paths["relations"]: dump_yaml({"relations": []}),
        paths["descriptions"]: dump_yaml({}),
    }


def zip_files(files: Mapping[str, bytes]) -> bytes:
    """A zip of ``files`` that is the same bytes for the same files."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            archive.writestr(info, files[path])
    return buffer.getvalue()


# ---------------------------------------------------------------- summary


def summarise(model: Mapping[str, Any], warnings: list[str] | None = None, *, ignored: int = 0) -> dict:
    """What a model holds, counted per section, for the import dialog and the overview."""
    ontology = model.get("ontology") or {}
    types = ontology.get("types") or []
    processes = (model.get("processes") or {}).get("processes") or []
    spines = (model.get("spines") or {}).get("spines") or []
    pages = (model.get("pages") or {}).get("pages") or []
    return {
        "company": (model.get("company") or {}).get("name") or "",
        "types": len(types),
        "attributes": sum(len(t.get("attributes") or []) for t in types),
        "relations": len(ontology.get("relations") or []),
        "processes": len(processes) if isinstance(processes, list) else 0,
        "spines": len(spines) if isinstance(spines, list) else 0,
        "pages": len(pages) if isinstance(pages, list) else 0,
        "mappings": len(model.get("mappings") or []),
        "profiles": len(model.get("profiles") or []),
        "sources": len(model.get("sources") or []),
        "health": len((model.get("health") or {}).get("types") or {}),
        "levels": {level: sum(1 for t in types if t.get("level") == level) for level in ("core", "pack", "company")},
        "ignored_files": ignored,
        "warnings": list(warnings or []),
    }
