"""An in-memory Knovas with document fields, for route tests.

``FakeDocFieldsApi`` is the Knovas client as a Platform route sees it, in one
of the server states of spec 2.1:

    off           every doc-fields call raises DocFieldsUnavailable (404
                  HTTP_404); where / return_fields are ignored on search
    values        registry, packs, settings, rules, GET/PATCH doc-values work;
                  find and any where / return_fields on search answer 400
                  where_unsupported
    listing_only  filters on, relevance calibration missing: find and
                  return_fields work, a search with where answers 503
                  where_requires_calibration (the probe still says filters)
    filters       everything, with the where echo

It extends ``FakeGraphApi`` (node types, nodes) because entity suggestions
come from ``graph_nodes``. Method signatures are the real client's; a test
in test_knovas_client_doc_fields.py pins that, so a route that works against
this fake calls the real client correctly.

Matching is exact equality only -- codes, strings as stored, entity names
casefolded -- like the mock Knovas API. The fake does not normalise dates or
periods; tests that need a formatted value seed the canonical shape.

Use it with the identity app::

    app = _identity_app(platform_db, tmp_path, monkeypatch,
                        client_cls=FakeDocFieldsApi.bind("filters"))
    api = FakeDocFieldsApi.current

Placeholder names only ("Muster AG", "Beispiel GmbH").
"""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from conftest import FakeGraphApi
from doc_fields_view import display_title
from knovas_client import DocFieldsError, DocFieldsUnavailable, QueryRejected

MODES = ("off", "values", "listing_only", "filters")

T_MANDANT = "t-mandant"
T_PATIENT = "t-patient"
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PERSONAL_KEYS = frozenset({"name", "vorname", "nachname", "geburtsdatum", "email", "ahv"})
_SYSTEM = ("title", "description", "path", "ingested_at")
_NOW = "2026-10-01T09:00:00Z"


def field_def(key: str, datatype: str, de: str, *, cardinality: str = "one",
              enum: Optional[List[Tuple[str, str]]] = None, target: Optional[str] = None,
              sensitivity: str = "normal", display: bool = False, facet: bool = False,
              date_role: Optional[str] = None, status: str = "active",
              origin: str = "system", pack: Optional[str] = "core") -> Dict[str, Any]:
    """A registry field in the server's public shape (contract 4.4)."""
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"doc-field:{key}")),
        "key": key, "datatype": datatype, "cardinality": cardinality,
        "labels": {"de": de, "en": key.replace("_", " ")}, "aliases": [],
        "enum_values": ([{"code": c, "labels": {"de": label}, "aliases": []} for c, label in enum]
                        if enum is not None else None),
        "date_role": date_role, "date_order": None, "fy_start_month": None, "fy_label": None,
        "code_scheme": "generic" if datatype == "code" else None,
        "target_node_type_id": target, "link_policy": "resolve",
        "sensitivity": sensitivity, "status": status, "origin": origin,
        "pack_key": pack, "pack_version": 1 if pack else None,
        "display": display, "facet": facet, "warnings": [],
    }


def core_fields() -> List[Dict[str, Any]]:
    """``core`` (the subset routes need) plus two tenant fields: ``mandant``
    with a visible target type, and ``patient``, sensitivity special."""
    return [
        field_def("amount", "money", "Betrag"),
        field_def("author", "text", "Autor", cardinality="many"),
        field_def("doc_type", "enum", "Dokumentart", display=True, facet=True, enum=[
            ("contract", "Vertrag"), ("invoice", "Rechnung"),
            ("correspondence.email", "E-Mail"), ("report", "Bericht"), ("other", "Andere")]),
        field_def("document_date", "date", "Dokumentdatum", display=True, date_role="document"),
        field_def("keywords", "text", "Stichw\u00f6rter", cardinality="many"),
        field_def("language", "code", "Sprache"),
        field_def("mandant", "entity_ref", "Mandant", target=T_MANDANT, display=True,
                  facet=True, origin="tenant", pack=None),
        field_def("party", "entity_ref", "Partei", cardinality="many"),
        field_def("patient", "entity_ref", "Patient", target=T_PATIENT, sensitivity="special",
                  facet=True, origin="tenant", pack=None),
        field_def("period", "period", "Zeitraum", date_role="period"),
        field_def("reference", "code", "Referenz", cardinality="many"),
        field_def("status", "enum", "Status", facet=True, enum=[
            ("draft", "Entwurf"), ("final", "Final"), ("signed", "Unterzeichnet")]),
    ]


def legal_ch_fields() -> List[Tuple[Dict[str, Any], Optional[str]]]:
    """``legal_ch`` as (field, target type NAME): the install resolves names."""
    f = field_def
    return [
        (f("client", "entity_ref", "Klient", display=True, facet=True, pack="legal_ch"), "Mandant"),
        (f("matter", "entity_ref", "Mandat", display=True, pack="legal_ch"), "Mandat"),
        (f("court", "entity_ref", "Gericht", facet=True, pack="legal_ch"), "Gericht"),
        (f("counterparty", "entity_ref", "Gegenpartei", pack="legal_ch"), None),
        (f("case_number", "code", "Gesch\u00e4ftsnummer", cardinality="many", pack="legal_ch"), None),
        (f("legal_class", "enum", "Dokumentklasse", display=True, facet=True, pack="legal_ch",
           enum=[("claim", "Klage"), ("judgment", "Urteil"), ("ruling", "Verf\u00fcgung")]), None),
        (f("filed_on", "date", "Eingereicht am", display=True, date_role="event",
           pack="legal_ch"), None),
        (f("deadline", "date", "Frist", display=True, date_role="due", pack="legal_ch"), None),
        (f("legal_area", "enum", "Rechtsgebiet", cardinality="many", facet=True,
           pack="legal_ch", enum=[("corporate", "Gesellschaftsrecht"),
                                  ("tenancy", "Mietrecht")]), None),
        (f("privileged", "bool", "Anwaltsgeheimnis", pack="legal_ch"), None),
    ]


def _basename(pointer: str) -> str:
    return str(pointer).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _fold(value: Any) -> Any:
    return value.casefold().strip() if isinstance(value, str) else value


class FakeDocFieldsApi(FakeGraphApi):
    """Knovas with document fields, in memory. See the module docstring."""

    current = None
    mode_default = "off"
    secured = True

    def __init__(self, config=None, mode: Optional[str] = None, *,
                 calibrated: Optional[bool] = None, principal_broker=None):
        if isinstance(config, str) and config in MODES and mode is None:
            config, mode = None, config
        super().__init__(config, principal_broker=principal_broker)
        mode = mode or self.mode_default
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        self.mode = "filters" if mode == "listing_only" else mode
        self.calibrated = (mode != "listing_only") if calibrated is None else bool(calibrated)
        self.node_types += [{"id": T_MANDANT, "name": "Mandant"},
                            {"id": T_PATIENT, "name": "Patient"}]
        for node_id, name, type_id in (("m1", "Muster AG", T_MANDANT),
                                       ("m2", "Beispiel GmbH", T_MANDANT),
                                       ("p1", "Beispiel Patient", T_PATIENT)):
            self.nodes[node_id] = {"id": node_id, "name": name, "node_type_id": type_id}
            self.facts[node_id] = []
        self.registry: List[Dict[str, Any]] = core_fields()
        self.installed_packs = {"core": 1}
        self.settings = {"unknown_keys": "ignore", "date_order": "dmy"}
        self.rules: Dict[str, Dict[str, Any]] = {}
        self.docs: Dict[str, Dict[str, Any]] = {}
        # What the routes asked for, in order: (method, arguments).
        self.doc_calls: List[Tuple[str, Dict[str, Any]]] = []
        self.graph_nodes_calls: List[Dict[str, Any]] = []
        self.probe_calls = 0
        # Knobs a test turns.
        self.probe_answer: Optional[str] = None      # override the probe
        self.drop_where_echo = False                  # a 2xx without where.applied
        self.may_be_partial = False
        self.relevance_gate = False                   # relevance_gate_applied w/o where
        self.change_denied: set = set()               # pointers -> 403 change_not_authorized
        self.registry_write_denied = False            # -> 403 registry_write_requires_full_clearance
        self.rules_denied = False                     # the rules GET too
        self.find_budget_exhausted = False            # last page complete:false
        self.total_count_overflow = False             # total_count null
        self.scripted_pages: List[Dict[str, Any]] = []  # find answers, in order
        self.return_fields_unreadable = False         # return_fields: {"applied": false}
        self.degraded = False                         # meta.degraded_to_bm25 true
        self.auto_scope: Optional[Dict[str, Any]] = None  # semantix["auto_scope"], as the client keeps it
        self._failures: Dict[str, List[BaseException]] = {}
        self._jobs = 0
        FakeDocFieldsApi.current = self

    @classmethod
    def bind(cls, mode: str, **kw: Any) -> type:
        """A subclass whose constructor takes only ``config``, the way
        create_app builds its client."""
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        calibrated = kw.pop("calibrated", None)
        if kw:
            raise TypeError(f"unknown option(s) {sorted(kw)}")

        def __init__(self, config=None, principal_broker=None):
            cls.__init__(self, config, mode, calibrated=calibrated,
                         principal_broker=principal_broker)

        return type(f"FakeDocFieldsApi_{mode}", (cls,), {"__init__": __init__})

    # -- seeding and failure injection -------------------------------------

    def add_document(self, pointer: str, *, title: Optional[str] = None,
                     fields: Optional[Dict[str, Any]] = None, layer: str = "upload",
                     description: Optional[str] = None, held: bool = False,
                     in_search: bool = True, score: float = 0.9) -> Dict[str, Any]:
        """Seed one document. ``fields`` are stored as given (canonical
        shapes), all in ``layer``. ``in_search`` also makes the search return
        it, as a row like the real client maps."""
        doc = {
            "pointer": pointer,
            "document_uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"doc:{pointer}")),
            "version": 1 if (fields or title) else 0,
            "title": title or _basename(pointer),
            "title_source": "upload" if title else "path",
            "description": description,
            "held": held,
            "layers": {},
        }
        source = "transmission:tx-1" if layer == "upload" else "rule:r-1"
        for key, value in (fields or {}).items():
            doc["layers"][key] = [{"layer": layer, "value": copy.deepcopy(value),
                                   "verified": False, "effective": True,
                                   "source_ref": source, "created_at": _NOW}]
        self.docs[pointer] = doc
        if in_search:
            self.search_results.append({
                "doc_id": pointer, "path": pointer, "source": "semantix", "score": score,
                "title": display_title(pointer, title, None)[0],
            })
        return doc

    def fail_call(self, method: str, status_or_exc: Any, code: Optional[str] = None,
                  **details: Any) -> None:
        """The next call of ``method`` raises. ``status_or_exc`` is an
        exception, or a status that becomes DocFieldsError (QueryRejected for
        ``search_documents``) with ``code`` and ``details``."""
        if isinstance(status_or_exc, BaseException):
            exc = status_or_exc
        elif method == "search_documents":
            exc = QueryRejected(int(status_or_exc), code, details)
        else:
            exc = DocFieldsError(int(status_or_exc), code, "fake failure", details=details)
        self._failures.setdefault(method, []).append(exc)

    def _enter(self, method: str, **args: Any) -> None:
        self.doc_calls.append((method, args))
        queued = self._failures.get(method)
        if queued:
            raise queued.pop(0)
        if self.mode == "off" and method != "search_documents":
            raise DocFieldsUnavailable()

    # -- registry helpers ----------------------------------------------------

    def _field(self, key: Any, *, include_deprecated: bool = False) -> Optional[Dict[str, Any]]:
        for spec in self.registry:
            if spec["key"] == key and (include_deprecated or spec["status"] != "deprecated"):
                return spec
        return None

    def _suggest(self, key: str) -> List[str]:
        head = str(key)[:3].lower()
        return [s["key"] for s in self.registry if s["key"].startswith(head)][:3]

    def _effective(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, layers in doc["layers"].items():
            live = [e for e in layers if e["effective"] and not e.get("unset")]
            if live:
                out[key] = copy.deepcopy(live[0]["value"])
        return dict(sorted(out.items()))

    def _visible(self, pointer: str) -> Optional[Dict[str, Any]]:
        if pointer in self.denied_pointers:
            return None
        return self.docs.get(pointer)

    def _registry_write(self) -> None:
        if self.registry_write_denied:
            raise DocFieldsError(403, "registry_write_requires_full_clearance", "denied")

    # -- the client surface: probe and registry ------------------------------

    def doc_fields_probe(self) -> str:
        self.probe_calls += 1
        if self.probe_answer is not None:
            return self.probe_answer
        return self.mode

    def graph_nodes(self, node_type_id=None, q=None):
        self.graph_nodes_calls.append({"node_type_id": node_type_id, "q": q})
        return super().graph_nodes(node_type_id=node_type_id, q=q)

    def doc_fields(self) -> List[Dict[str, Any]]:
        self._enter("doc_fields")
        return copy.deepcopy(sorted(self.registry, key=lambda s: s["key"]))

    def create_doc_field(self, defn: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(defn, dict) or not defn.get("key"):
            raise ValueError("a field definition needs at least a key")
        self._enter("create_doc_field", defn=copy.deepcopy(defn))
        self._registry_write()
        key = str(defn["key"])
        if not _KEY_RE.match(key):
            raise DocFieldsError(400, "invalid_field_definition", "bad key", details={"path": "key"})
        if key in _PERSONAL_KEYS:
            raise DocFieldsError(422, "key_looks_personal", "personal")
        if key in _SYSTEM or self._field(key, include_deprecated=True):
            raise DocFieldsError(409, "field_key_exists", "exists")
        target = defn.get("target_node_type_id")
        if target and target not in {t["id"] for t in self.node_types}:
            raise DocFieldsError(400, "invalid_field_definition", "target",
                                 details={"path": "target_node_type_id"})
        enum = [(c, c) for c in defn.get("enum_values") or [] if isinstance(c, str)] or None
        spec = field_def(key, str(defn.get("datatype") or "text"),
                         str((defn.get("labels") or {}).get("de") or key),
                         cardinality=defn.get("cardinality") or "one", enum=enum,
                         target=target, sensitivity=defn.get("sensitivity") or "normal",
                         display=bool(defn.get("display")), facet=bool(defn.get("facet")),
                         date_role=defn.get("date_role"),
                         status=defn.get("status") or "active", origin="tenant", pack=None)
        self.registry.append(spec)
        return copy.deepcopy(spec)

    def update_doc_field(self, field_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(changes, dict) or not changes:
            raise ValueError("update_doc_field needs at least one change")
        self._enter("update_doc_field", field_id=field_id, changes=copy.deepcopy(changes))
        self._registry_write()
        spec = next((s for s in self.registry if s["id"] == field_id), None)
        if spec is None:
            return None
        if "key" in changes and changes["key"] != spec["key"]:
            raise DocFieldsError(400, "invalid_field_definition", "key", details={"path": "key"})
        if ("datatype" in changes and changes["datatype"] != spec["datatype"]
                and spec["status"] != "provisional"):
            raise DocFieldsError(409, "field_type_locked", "locked")
        for name, value in changes.items():
            if name == "labels" and isinstance(value, dict):
                spec["labels"].update(value)
            elif name in spec and name not in ("id", "key", "origin"):
                spec[name] = value
        return copy.deepcopy(spec)

    def deprecate_doc_field(self, field_id: str) -> Optional[Dict[str, Any]]:
        self._enter("deprecate_doc_field", field_id=field_id)
        self._registry_write()
        spec = next((s for s in self.registry if s["id"] == field_id), None)
        if spec is None:
            return None
        spec["status"] = "deprecated"
        return copy.deepcopy(spec)

    def doc_field_packs(self) -> List[Dict[str, Any]]:
        self._enter("doc_field_packs")
        return [{"key": key, "version": 1, "installed": key in self.installed_packs,
                 "installed_version": self.installed_packs.get(key)}
                for key in ("core", "legal_ch")]

    def install_doc_field_pack(self, pack: str) -> Dict[str, Any]:
        self._enter("install_doc_field_pack", pack=pack)
        self._registry_write()
        if pack == "core":
            return {"installed": 0, "skipped": len(core_fields()), "warnings": []}
        if pack != "legal_ch":
            raise DocFieldsError(404, "pack_not_found", "Field pack not found")
        types = {t["name"]: t["id"] for t in self.node_types}
        installed = skipped = 0
        warnings: List[str] = []
        for spec, type_name in legal_ch_fields():
            if self._field(spec["key"], include_deprecated=True):
                skipped += 1
                continue
            if spec["datatype"] == "entity_ref" and type_name:
                spec["target_node_type_id"] = types.get(type_name)
                if spec["target_node_type_id"] is None:
                    warnings.append(f"target_type_missing:{spec['key']}")
            self.registry.append(spec)
            installed += 1
        self.installed_packs["legal_ch"] = 1
        return {"installed": installed, "skipped": skipped, "warnings": warnings}

    def doc_field_settings(self) -> Dict[str, Any]:
        self._enter("doc_field_settings")
        return dict(self.settings)

    def set_doc_field_settings(self, unknown_keys=None, date_order=None) -> Dict[str, Any]:
        if unknown_keys is None and date_order is None:
            raise ValueError("set_doc_field_settings needs unknown_keys or date_order")
        self._enter("set_doc_field_settings", unknown_keys=unknown_keys, date_order=date_order)
        self._registry_write()
        for name, value, allowed in (("unknown_keys", unknown_keys, ("ignore", "reject", "register")),
                                     ("date_order", date_order, ("dmy", "mdy", "ymd"))):
            if value is None:
                continue
            if value not in allowed:
                raise DocFieldsError(400, "invalid_value", "bad", details={"path": name})
            self.settings[name] = value
        return dict(self.settings)

    # -- folder rules ----------------------------------------------------------

    def _rules_gate(self) -> None:
        if self.rules_denied:
            raise DocFieldsError(403, "registry_write_requires_full_clearance", "denied")

    def doc_field_rules(self) -> List[Dict[str, Any]]:
        self._enter("doc_field_rules")
        self._rules_gate()
        return [copy.deepcopy(r) for r in self.rules.values()]

    def put_doc_field_rule(self, prefix: str, values: Dict[str, Any]) -> Dict[str, Any]:
        if not str(prefix or "").strip() or not str(prefix).endswith("/"):
            raise ValueError("a rule prefix must be non-empty and end with '/'")
        if not isinstance(values, dict) or not values:
            raise ValueError("a rule needs at least one field value")
        self._enter("put_doc_field_rule", prefix=prefix, values=copy.deepcopy(values))
        self._rules_gate()
        for key in values:
            if self._field(key) is None:
                raise DocFieldsError(400, "unknown_field", "unknown",
                                     details={"path": f"set.{key}", "suggest": self._suggest(key)})
        old = self.rules.get(prefix)
        rule = {"id": old["id"] if old else str(uuid.uuid5(uuid.NAMESPACE_URL, f"rule:{prefix}")),
                "pointer_prefix": prefix, "set": copy.deepcopy(values),
                "version": (old["version"] + 1) if old else 1, "status": "live"}
        self.rules[prefix] = rule
        self._jobs += 1
        return {"rule": copy.deepcopy(rule), "reapply_job_id": f"job-{self._jobs}"}

    def retire_doc_field_rule(self, prefix: str) -> Optional[Dict[str, Any]]:
        if not str(prefix or "").strip():
            raise ValueError("a rule prefix is required")
        self._enter("retire_doc_field_rule", prefix=prefix)
        self._rules_gate()
        if self.rules.pop(prefix, None) is None:
            return None
        self._jobs += 1
        return {"retired": True, "reapply_job_id": f"job-{self._jobs}"}

    # -- values ----------------------------------------------------------------

    def doc_values(self, pointer: str) -> Optional[Dict[str, Any]]:
        if not str(pointer or "").strip():
            raise ValueError("pointer is required")
        self._enter("doc_values", pointer=pointer)
        doc = self._visible(pointer)
        if doc is None:
            return None
        if doc["held"]:
            return {"pointer": pointer, "acl_mode": "quarantined", "version": doc["version"]}
        return {
            "pointer": pointer, "document_uuid": doc["document_uuid"],
            "acl_mode": "follows_document", "version": doc["version"],
            "title": doc["title"], "title_source": doc["title_source"],
            "description": doc["description"], "path": pointer, "ingested_at": _NOW,
            "warnings": [], "fields": self._effective(doc),
            "layers": copy.deepcopy(doc["layers"]),
        }

    def _normalize(self, spec: Dict[str, Any], value: Any, path: str, strict: bool,
                   warnings: List[Dict[str, str]]) -> Any:
        """Just enough of the server's normaliser for route tests."""
        key = spec["key"]

        def warn(code: str) -> None:
            if strict:
                raise DocFieldsError(422, code, "strict", details={"path": path})
            warnings.append({"key": key, "code": code})

        if spec["datatype"] == "enum":
            for item in spec["enum_values"] or ():
                if _fold(value) in (_fold(item["code"]), _fold(item["labels"].get("de"))):
                    return item["code"]
            raise DocFieldsError(400, "invalid_value", "bad value", details={"path": path})
        if spec["datatype"] == "entity_ref":
            name = value.get("name") if isinstance(value, dict) else value
            if not isinstance(name, str) or not name.strip():
                raise DocFieldsError(400, "invalid_value", "bad value", details={"path": path})
            target = spec.get("target_node_type_id")
            match = [n for n in self.nodes.values()
                     if target and n.get("node_type_id") == target and _fold(n["name"]) == _fold(name)]
            if len(match) == 1:
                return {"node_id": match[0]["id"], "name": name.strip()}
            warn("ambiguous_entity" if len(match) > 1 else "unresolved_entity")
            return {"name": name.strip()}
        if spec["datatype"] == "date" and isinstance(value, str) and "/" in value:
            warn("ambiguous_date")
        if spec["datatype"] == "bool" and not isinstance(value, bool):
            raise DocFieldsError(400, "invalid_value", "bad value", details={"path": path})
        return value

    def patch_doc_values(self, pointer: str, if_version: int, *, set=None,  # noqa: A002
                         unset=None, add=None, remove=None, fields_strict: bool = False,
                         actor_ref: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not str(pointer or "").strip():
            raise ValueError("pointer is required")
        if isinstance(if_version, bool) or not isinstance(if_version, int) or if_version < 0:
            raise ValueError("if_version must be the non-negative version that was read")
        if actor_ref is not None and (not str(actor_ref).strip() or "@" in str(actor_ref)):
            raise ValueError("actor_ref must be an opaque id, not an e-mail address")
        if not (set or unset or add or remove):
            raise ValueError("patch_doc_values needs set, unset, add or remove")
        self._enter("patch_doc_values", pointer=pointer, if_version=if_version,
                    set=copy.deepcopy(set), unset=copy.deepcopy(unset), add=copy.deepcopy(add),
                    remove=copy.deepcopy(remove), fields_strict=fields_strict,
                    actor_ref=actor_ref)
        doc = self._visible(pointer)
        if doc is None:
            return None
        if pointer in self.change_denied:
            raise DocFieldsError(403, "change_not_authorized", "denied")
        if doc["held"]:
            raise DocFieldsError(409, "anchor_quarantined", "held")
        if if_version != doc["version"]:
            raise DocFieldsError(409, "version_conflict", "conflict",
                                 details={"current_version": doc["version"]})
        warnings: List[Dict[str, str]] = []
        typed = False
        manual: Dict[str, Any] = {}
        for key, value in (set or {}).items():
            if key in ("path", "ingested_at"):
                raise DocFieldsError(400, "invalid_value", "system", details={"path": f"set.{key}"})
            if key == "title":
                doc["title"] = value if value else _basename(pointer)
                doc["title_source"] = "manual" if value else "path"
                continue
            if key == "description":
                doc["description"] = value
                continue
            spec = self._field(key)
            if spec is None:
                if fields_strict:
                    raise DocFieldsError(422, "unknown_field", "unknown", details={"path": f"set.{key}"})
                warnings.append({"key": key, "code": "unknown_field"})
                continue
            typed = True
            if value is None:
                manual[key] = None
                continue
            items = value if isinstance(value, list) else [value]
            normalized = [self._normalize(spec, v, f"set.{key}[{i}]", fields_strict, warnings)
                          for i, v in enumerate(items)]
            manual[key] = normalized if spec["cardinality"] == "many" else normalized[0]
        for key in unset or ():
            if self._field(key) is None:
                raise DocFieldsError(400, "unknown_field", "unknown", details={"path": f"unset.{key}"})
            typed = True
            manual[key] = "__unset__"
        for section, adding in (("add", True), ("remove", False)):
            for key, values in ((add if adding else remove) or {}).items():
                spec = self._field(key)
                if spec is None or spec["cardinality"] != "many":
                    raise DocFieldsError(400, "invalid_value", "many only",
                                         details={"path": f"{section}.{key}"})
                typed = True
                current = list(self._effective(doc).get(key) or [])
                if adding:
                    current += [self._normalize(spec, v, f"add.{key}[{i}]", fields_strict, warnings)
                                for i, v in enumerate(values)]
                else:
                    current = [v for v in current if v not in values
                               and not (isinstance(v, dict) and v.get("name") in values)]
                manual[key] = current if current else "__unset__"
        for key, value in manual.items():
            layers = [e for e in doc["layers"].get(key, []) if e["layer"] != "manual"]
            if value is not None:
                entry = {"layer": "manual", "value": None if value == "__unset__" else value,
                         "verified": True, "effective": True, "source_ref": None,
                         "created_at": _NOW}
                if value == "__unset__":
                    entry["unset"] = True
                for lower in layers:
                    lower["effective"] = False
                layers.insert(0, entry)
            elif layers:
                layers[0]["effective"] = True
            doc["layers"][key] = layers
        doc["version"] += 1
        return {"pointer": pointer, "version": doc["version"], "title": doc["title"],
                "title_source": doc["title_source"], "description": doc["description"],
                "fields": self._effective(doc) if typed else {}, "warnings": warnings}

    # -- where ---------------------------------------------------------------

    def _check_where(self, where: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Resolve the keys; the echo's ``resolved`` list. Unknown keys are
        400 unknown_field with suggestions, as on the server."""
        resolved = []
        for key, operand in where.items():
            spec = self._field(key, include_deprecated=True)
            if spec is None:
                raise DocFieldsError(400, "unknown_field", "unknown",
                                     details={"path": f"where.{key}", "suggest": self._suggest(key)})
            op = "in" if isinstance(operand, list) else "eq"
            entry: Dict[str, Any] = {"field": key, "op": op,
                                     ("values" if op == "in" else "value"): copy.deepcopy(operand)}
            if spec["datatype"] == "entity_ref":
                names = operand if isinstance(operand, list) else [operand]
                names = [n.get("name") if isinstance(n, dict) else n for n in names]
                entry["resolved_nodes"] = sum(
                    1 for n in self.nodes.values()
                    if n.get("node_type_id") == spec.get("target_node_type_id")
                    and any(_fold(n["name"]) == _fold(x) for x in names))
            resolved.append(entry)
        return resolved

    def _matches(self, doc: Dict[str, Any], where: Dict[str, Any]) -> bool:
        if doc["held"]:
            return False
        values = self._effective(doc)
        for key, operand in where.items():
            if isinstance(operand, dict) and set(operand) == {"in"}:
                operand = operand["in"]
            elif isinstance(operand, dict) and set(operand) == {"eq"}:
                operand = operand["eq"]
            wanted = operand if isinstance(operand, list) else [operand]
            wanted = {_fold(w.get("name") if isinstance(w, dict) else w) for w in wanted}
            stored = values.get(key)
            stored = stored if isinstance(stored, list) else [stored]
            have = {_fold(s.get("name") if isinstance(s, dict) else s) for s in stored}
            if not wanted & have:
                return False
        return True

    def _return(self, doc: Dict[str, Any], return_fields: Any) -> Dict[str, Any]:
        values = self._effective(doc)
        if return_fields is True:
            keys = [s["key"] for s in self.registry
                    if s["status"] != "deprecated" and s["sensitivity"] == "normal"] + ["title"]
        else:
            keys = [k for k in return_fields or () if isinstance(k, str)]
        out = {k: values[k] for k in keys if k in values}
        if "title" in keys:
            out["title"] = doc["title"]
        return out

    def find_doc_values(self, where: Dict[str, Any], *, sort=None, limit: int = 50,
                        after: Optional[str] = None, return_fields=None) -> Dict[str, Any]:
        if not isinstance(where, dict) or not where:
            raise ValueError("find_doc_values needs a non-empty where")
        self._enter("find_doc_values", where=copy.deepcopy(where), sort=sort, limit=limit,
                    after=after, return_fields=return_fields)
        if self.mode == "values":
            raise DocFieldsError(400, "where_unsupported", "Field filters are not enabled")
        if self.scripted_pages:
            return copy.deepcopy(self.scripted_pages.pop(0))
        resolved = self._check_where(where)
        docs = [d for p, d in sorted(self.docs.items())
                if p not in self.denied_pointers and self._matches(d, where)]
        if sort and sort.get("field") not in (None, "pointer"):
            key = sort["field"]
            spec = self._field(key, include_deprecated=True)
            if spec is None or spec["datatype"] not in ("date", "period"):
                raise DocFieldsError(400, "invalid_value", "sort", details={"path": "sort.field"})
            present = [d for d in docs if self._effective(d).get(key) is not None]
            missing = [d for d in docs if self._effective(d).get(key) is None]
            present.sort(key=lambda d: str(self._effective(d)[key]),
                         reverse=sort.get("order") == "desc")
            docs = present + missing
        elif sort and sort.get("order") == "desc":
            docs.reverse()  # the pointer, descending
        offset = int(str(after)[1:]) if after else 0
        size = max(1, min(200, int(limit)))
        page = docs[offset:offset + size]
        more = offset + size < len(docs)
        out: Dict[str, Any] = {
            "documents": [],
            "next_after": f"o{offset + size}" if more else None,
            "complete": (not more) and not self.find_budget_exhausted,
        }
        for doc in page:
            row = {"pointer": doc["pointer"], "document_uuid": doc["document_uuid"],
                   "title": doc["title"]}
            if return_fields is not None:
                row["fields"] = self._return(doc, return_fields)
            out["documents"].append(row)
        if after is None:
            out["total_count"] = None if self.total_count_overflow else len(docs)
        if not self.drop_where_echo:
            out["where"] = {"applied": True, "clauses": len(where), "resolved": resolved}
        if return_fields is not None:
            out["return_fields"] = {"applied": True}
        return out

    # -- search ----------------------------------------------------------------

    def search_documents(self, query, limit=20, filters=None, *, where=None,
                         return_fields=None):
        self.search_calls.append(str(query))
        self.search_requests.append({"query": query, "limit": limit, "filters": filters,
                                     "where": where, "return_fields": return_fields})
        self._enter("search_documents", where=copy.deepcopy(where), return_fields=return_fields)
        rows = [dict(r) for r in self.search_results
                if str(r.get("doc_id") or "") not in self.denied_pointers]
        meta: Dict[str, Any] = {"status": "success", "message": "Query executed successfully",
                                "query_session_id": "qs-fake"}
        sent = self.mode != "off" and (where is not None or return_fields is not None)
        if sent and self.mode == "values":
            raise QueryRejected(400, "where_unsupported", {})
        if self.mode != "off" and where is not None:
            if not self.calibrated:
                raise QueryRejected(503, "where_requires_calibration", {})
            try:
                resolved = self._check_where(where)
            except DocFieldsError as exc:
                raise QueryRejected(exc.status, exc.error_code, exc.details) from None
            rows = [r for r in rows
                    if r.get("doc_id") in self.docs and self._matches(self.docs[r["doc_id"]], where)]
            for row in rows:
                row["relevance_tier"] = "strong"
            if not self.drop_where_echo:
                meta["where"] = {"applied": True, "clauses": len(where), "resolved": resolved,
                                 "may_be_partial": self.may_be_partial}
        if self.mode != "off" and return_fields is not None:
            if self.return_fields_unreadable:
                # Knovas could not read the values: results without fields.
                meta["return_fields"] = {"applied": False}
            else:
                for row in rows:
                    doc = self.docs.get(row.get("doc_id"))
                    fields = self._return(doc, return_fields) if doc else {}
                    row["fields"] = fields
                    row["title"], row["title_from_values"] = display_title(
                        row["doc_id"], row.get("title"), fields.get("title"))
                meta["return_fields"] = {"applied": True}
        rows = rows[:limit]
        meta.update({
            "result_count": len(rows), "pointers": [r.get("doc_id") for r in rows],
            "no_strong_matches": not rows,
            "no_results_reason": None if rows else "no_candidates",
            "relevance_gate_applied": bool(where is not None and self.mode != "off")
            or self.relevance_gate,
            "degraded_to_bm25": bool(self.degraded),
        })
        if self.auto_scope is not None:
            meta["auto_scope"] = copy.deepcopy(self.auto_scope)
        return {"results": rows, "total": len(rows), "semantix": meta}
