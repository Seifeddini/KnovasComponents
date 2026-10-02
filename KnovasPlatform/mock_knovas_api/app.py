"""Offline mock of the Knovas Secure API (demo and contract tests).

`create_app()` builds one independent mock; all state lives on that app
instance, so two apps share nothing. The module keeps `app = create_app()`
for the compose `mock` profile (`python app.py`).

Document fields: `doc_fields` (env `MOCK_DOC_FIELDS`) selects which server
state the mock plays. The shapes follow the server contract
(KnowledgeBase `docs/Knovas_Developer_Kit/api/Knowledge_Graph_API.md`,
`Secure_API.md`); the provisional goldens in `goldens/` pin them.

- `off`: an old server, or the feature off for the tenant. Every
  `/secured/graph/doc-*` path answers the unknown-route 404 (`HTTP_404`) for
  every method; `fields`, `where` and `return_fields` are ignored without an
  echo. `/secured/query` and init answer exactly as before the feature.
- `values`: init stages `fields` and echoes them; doc-values GET/PATCH,
  registry (core seeded), packs, settings and folder rules work. A query
  carrying `where` or `return_fields`, and `find`, answer 400
  `where_unsupported`.
- `filters`: values, plus `where` and `return_fields` on query and `find`.
  `where` matches stored values by exact equality (codes and strings as
  stored, entity names casefolded); the mock does not emulate the server's
  normaliser (dates, periods, fiscal years, money), so range operators
  compare the stored text. `calibrated=False` makes a query with `where`
  answer 503 `where_requires_calibration`, while `find` and `return_fields`
  alone keep working.

`refuse_init_fields="<status>:<code>"` forces that refusal on every init that
carries `fields` (values and filters). `brokered=True` plays a BROKERED
tenant: without a `principal_assertion`, an init carrying `access_groups` or
an entity value answers 401 `assertion_rejected`, and so do query and the
graph routes (the assertion is required, not verified).

Tests reach the state through `app.extensions["knovas_mock"]` (a
`MockState`): the request log, the documents, seeded values and switches
for the 403/409 paths. Placeholder names only ("Muster AG", "Beispiel GmbH").
"""

from __future__ import annotations

import base64
import copy
import difflib
import json
import os
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from flask import Flask, abort, jsonify, request
from werkzeug.exceptions import HTTPException

DOCUMENTS: List[Dict[str, Any]] = [
    {
        "doc_id": "demo-001",
        "title": "Lease Agreement - ACME GmbH",
        "path": "contracts/lease_acme.pdf",
        "type": "contract",
        "snippet": "This lease agreement starts on 2026-01-01 and includes renewal options.",
        "timestamp": "2026-01-01T09:00:00Z",
    },
    {
        "doc_id": "demo-002",
        "title": "Employment Contract - Jane Doe",
        "path": "hr/employment_jane_doe.docx",
        "type": "employment",
        "snippet": "The probation period is 6 months with full benefits.",
        "timestamp": "2026-02-10T11:30:00Z",
    },
    {
        "doc_id": "demo-003",
        "title": "Case Notes - Matter 42",
        "path": "cases/matter_42_notes.txt",
        "type": "case_note",
        "snippet": "Initial hearing is scheduled for April with supporting evidence attached.",
        "timestamp": "2026-03-01T14:15:00Z",
    },
]

MODES = ("off", "values", "filters")

# Every path the server's doc-fields blueprint owns: answered the unknown-route
# 404 for every method while the feature is off.
OWNED_PREFIXES = (
    "/secured/graph/doc-values",
    "/secured/graph/doc-fields",
    "/secured/graph/doc-field-rules",
)

# The server's fixed message per code (doc_fields/errors.py). Never formatted.
FIXED_MESSAGES = {
    "doc_fields_unavailable": "Document fields are temporarily unavailable",
    "doc_fields_ingest_unavailable": "No ingest worker can store document fields right now",
    "version_conflict": "The document values changed since they were read",
    "anchor_quarantined": "The document's values are held until its access settings agree",
    "where_unavailable": "Field filters are temporarily unavailable",
    "where_requires_calibration": "Field filters need a relevance calibration for this tenant",
    "if_version_required": "if_version is required",
    "invalid_value": "A field value is invalid",
    "invalid_fields": "The fields object is invalid",
    "ambiguous_field": "A field key matches more than one registered field",
    "fields_too_large": "The fields object is too large",
    "unknown_field": "A field key is not registered",
    "change_not_authorized": "The caller may not change this document",
    "where_too_complex": "The where clause is too complex",
    "where_unsupported": "Field filters are not enabled for this tenant",
    "invalid_cursor": "The cursor is invalid",
    "invalid_field_definition": "The field definition is invalid",
    "field_key_exists": "A field with this key already exists",
    "field_cap_reached": "The tenant has reached its field limit",
    "field_type_locked": "The field's type can no longer change",
    "key_looks_personal": "The field key looks like personal data",
    "registry_write_requires_full_clearance": "Registry changes need full clearance or tenant-admin membership",
    "pack_not_found": "Field pack not found",
    # Not a doc-fields code: the RbacError message of a refused assertion.
    "assertion_rejected": "principal assertion rejected",
}
GENERIC_MESSAGE = "Document fields request failed"

SYSTEM_KEYS = ("title", "description", "path", "ingested_at", "pointer")
ANCHOR_KEYS = ("title", "description", "path", "ingested_at")
DATATYPES = ("date", "period", "money", "number", "code", "enum", "bool", "text", "entity_ref")
INTERVAL_TYPES = ("date", "period")
OPS = ("eq", "in", "gt", "gte", "lt", "lte", "between", "overlaps", "within", "prefix", "exists")
RANGE_OPS = ("gt", "gte", "lt", "lte", "between")
CALLER_KEYS = ("access_groups", "principal_assertion", "actor_ref")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

MAX_UPLOAD_KEYS = 64
MAX_UPLOAD_BYTES = 16384
MAX_VALUES_PER_KEY = 32
MAX_WHERE_KEYS = 8
MAX_WHERE_LIST = 50
MAX_RETURN_FIELDS = 64
MAX_FIELDS_PER_TENANT = 256
FIND_DEFAULT_LIMIT = 100
FIND_MAX_LIMIT = 200

_YES = frozenset({"ja", "j", "yes", "y", "oui", "si", "true", "wahr", "vrai", "vero", "1"})
_NO = frozenset({"nein", "n", "no", "non", "false", "falsch", "faux", "falso", "0"})
_HONORIFIC = re.compile(
    r"(?:^|(?<=[\s_\-/(]))(?:(?:hr|fr|dr|mr|mrs|ms|prof|mme|mlle)\."
    r"|(?:herr|frau|madame|monsieur|mister|miss)(?=$|[\s_\-./])|(?:dr|prof)_(?=[a-z]))",
    re.IGNORECASE)
_MOCK_NS = uuid.UUID("6b1d3e5c-2f0a-4c51-9d55-3f7e0c1a9b42")


def stable_id(kind: str, name: str) -> str:
    """A deterministic id, so a test can name a seeded node, field or
    document without reading it back first."""
    return str(uuid.uuid5(_MOCK_NS, f"{kind}:{name}"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _basename(value: Optional[str]) -> str:
    text = str(value or "").replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1]


def fold(text: Any) -> str:
    """The comparison form: NFKC, casefold, umlauts spelled out, marks and
    surplus whitespace removed (a simplified doc_fields/fold.py)."""
    out = unicodedata.normalize("NFKC", str(text)).casefold()
    out = out.replace("\u00e4", "ae").replace("\u00f6", "oe").replace("\u00fc", "ue")
    out = "".join(ch for ch in unicodedata.normalize("NFD", out)
                  if unicodedata.category(ch) != "Mn")
    return " ".join(out.split())


def normalize_key(raw_key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", fold(raw_key)).strip("_")[:64]


def looks_personal(raw_key: Any) -> bool:
    """A coarse version of the server's key heuristic: e-mail addresses,
    honorifics, dates and runs of four or more digits."""
    text = str(raw_key)
    if "@" in text or re.search(r"\d{4,}", text):
        return True
    if re.search(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", text):
        return True
    return bool(_HONORIFIC.search(text))


# ---------------------------------------------------------------------------
# field packs: a subset of config/doc_field_packs/*.yaml (keys, types, German
# and English labels), enough for the UI to label what it shows
# ---------------------------------------------------------------------------

def _enum(*codes: Tuple[str, str, str]) -> List[Dict[str, Any]]:
    return [{"code": code, "labels": {"de": de, "en": en}, "aliases": []} for code, de, en in codes]


def _field(key: str, datatype: str, de: str, en: str, **extra: Any) -> Dict[str, Any]:
    out = {"key": key, "datatype": datatype, "cardinality": "one",
           "labels": {"de": de, "en": en}, "aliases": []}
    out.update(extra)
    return out


CORE_FIELDS = [
    _field("doc_type", "enum", "Dokumentart", "Document type", facet=True, display=True,
           aliases=["Dokumenttyp", "Belegart", "document_type"],
           enum_values=_enum(
               ("contract", "Vertrag", "Contract"),
               ("contract.amendment", "Vertrags\u00e4nderung", "Amendment"),
               ("invoice", "Rechnung", "Invoice"),
               ("receipt", "Quittung", "Receipt"),
               ("offer", "Offerte", "Offer"),
               ("order", "Bestellung", "Order"),
               ("correspondence.letter", "Brief", "Letter"),
               ("correspondence.email", "E-Mail", "Email"),
               ("minutes", "Protokoll", "Minutes"),
               ("report", "Bericht", "Report"),
               ("memo", "Aktennotiz", "Memo"),
               ("form", "Formular", "Form"),
               ("certificate", "Bescheinigung", "Certificate"),
               ("statement", "Auszug", "Statement"),
               ("policy", "Richtlinie", "Policy"),
               ("presentation", "Pr\u00e4sentation", "Presentation"),
               ("legal_submission", "Rechtsschrift", "Legal submission"),
               ("decision", "Entscheid", "Decision"),
               ("power_of_attorney", "Vollmacht", "Power of attorney"),
               ("other", "Andere", "Other"))),
    _field("document_date", "date", "Dokumentdatum", "Document date", date_role="document",
           display=True, aliases=["Datum"]),
    _field("period", "period", "Zeitraum", "Period", date_role="period"),
    _field("language", "code", "Sprache", "Language", code_scheme="bcp47"),
    _field("author", "text", "Verfasser", "Author", cardinality="many", aliases=["Autor"]),
    _field("party", "entity_ref", "Partei", "Party", cardinality="many"),
    _field("reference", "code", "Referenz", "Reference", cardinality="many", code_scheme="generic"),
    _field("amount", "money", "Betrag", "Amount"),
    _field("status", "enum", "Status", "Status", facet=True,
           enum_values=_enum(("draft", "Entwurf", "Draft"), ("final", "Final", "Final"),
                             ("signed", "Unterzeichnet", "Signed"),
                             ("superseded", "Ersetzt", "Superseded"),
                             ("archived", "Archiviert", "Archived"))),
    _field("keywords", "text", "Stichw\u00f6rter", "Keywords", cardinality="many"),
]

LEGAL_CH_FIELDS = [
    _field("client", "entity_ref", "Mandant", "Client", display=True, facet=True,
           target_names=["Mandant", "Klient", "Client", "Cliente"]),
    _field("matter", "entity_ref", "Mandat", "Matter", display=True,
           target_names=["Mandat", "Dossier", "Matter", "Pratica"]),
    _field("court", "entity_ref", "Gericht", "Court", facet=True,
           target_names=["Gericht", "Tribunal", "Tribunale", "Court"]),
    _field("counterparty", "entity_ref", "Gegenpartei", "Counterparty"),
    _field("case_number", "code", "Gesch\u00e4ftsnummer", "Case number", cardinality="many",
           code_scheme="legal_case_ch"),
    _field("legal_class", "enum", "Dokumentklasse", "Document class", facet=True, display=True,
           enum_values=_enum(
               ("claim", "Klage", "Statement of claim"),
               ("answer", "Klageantwort", "Statement of defence"),
               ("reply", "Replik", "Reply"), ("rejoinder", "Duplik", "Rejoinder"),
               ("appeal", "Berufung", "Appeal"), ("judgment", "Urteil", "Judgment"),
               ("ruling", "Verf\u00fcgung", "Ruling"),
               ("power_of_attorney", "Vollmacht", "Power of attorney"),
               ("expert_opinion", "Gutachten", "Expert opinion"),
               ("fee_note", "Honorarnote", "Fee note"),
               ("correspondence", "Korrespondenz", "Correspondence"),
               ("submission", "Rechtsschrift", "Submission"),
               ("settlement", "Vergleich", "Settlement"),
               ("minutes", "Protokoll", "Minutes"))),
    _field("filed_on", "date", "Eingereicht am", "Filed on", date_role="event", display=True),
    _field("decision_date", "date", "Entscheiddatum", "Decision date", date_role="event"),
    _field("deadline", "date", "Frist", "Deadline", date_role="due", display=True),
    _field("legal_area", "enum", "Rechtsgebiet", "Legal area", cardinality="many", facet=True,
           enum_values=_enum(
               ("corporate", "Gesellschaftsrecht", "Corporate law"),
               ("contract", "Vertragsrecht", "Contract law"),
               ("employment", "Arbeitsrecht", "Employment law"),
               ("tenancy", "Mietrecht", "Tenancy law"),
               ("family", "Familienrecht", "Family law"),
               ("inheritance", "Erbrecht", "Inheritance law"),
               ("real_estate", "Immobilienrecht", "Real estate law"),
               ("debt_enforcement", "Schuldbetreibungs- und Konkursrecht",
                "Debt enforcement and bankruptcy"),
               ("criminal", "Strafrecht", "Criminal law"),
               ("administrative", "Verwaltungsrecht", "Administrative law"),
               ("tax", "Steuerrecht", "Tax law"),
               ("ip", "Immaterialg\u00fcterrecht", "Intellectual property"),
               ("competition", "Wettbewerbsrecht", "Competition law"),
               ("data_protection", "Datenschutzrecht", "Data protection"),
               ("banking_finance", "Bank- und Finanzmarktrecht", "Banking and finance"),
               ("litigation", "Prozessf\u00fchrung", "Litigation"),
               ("arbitration", "Schiedsgerichtsbarkeit", "Arbitration"))),
    _field("privileged", "bool", "Anwaltsgeheimnis", "Privileged"),
]

PACKS = {"core": (1, CORE_FIELDS), "legal_ch": (1, LEGAL_CH_FIELDS)}

# The registry's public field shape (doc_fields_api._PUBLIC_FIELD_KEYS).
PUBLIC_FIELD_KEYS = (
    "id", "key", "datatype", "cardinality", "labels", "aliases", "enum_values",
    "date_role", "date_order", "fy_start_month", "fy_label", "code_scheme",
    "target_node_type_id", "link_policy", "sensitivity", "status", "origin",
    "pack_key", "pack_version", "display", "facet",
)
_DEFINITION_KEYS = frozenset({
    "key", "datatype", "sample", "cardinality", "labels", "aliases", "enum_values",
    "date_role", "date_order", "fy_start_month", "fy_label", "code_scheme",
    "target_node_type_id", "link_policy", "sensitivity", "status", "display", "facet",
    "detect", "is_recency_field", "backing",
    # read-only keys a client may send back from a GET: accepted and ignored
    "id", "origin", "pack_key", "pack_version", "warnings",
})
_READ_ONLY_KEYS = ("id", "key", "origin", "pack_key", "pack_version", "warnings")

# Upload values of the demo documents (placeholder names), so the values and
# filters modes have something to show and to find.
SEED_VALUES = {
    "demo-001": {"doc_type": "contract", "party": ["Muster AG"], "document_date": "2026-01-01"},
    "demo-002": {"doc_type": "contract", "status": "signed", "party": ["Beispiel GmbH"],
                 "document_date": "2026-02-10"},
    "demo-003": {"doc_type": "memo", "document_date": "2026-03-01"},
}


class DocFieldError(Exception):
    """The server's doc-fields error body: a fixed message per code and a
    JSON path, never a submitted value."""

    def __init__(self, code: str, status: int = 400, path: Optional[str] = None, **extra: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.path = path
        self.extra = {k: v for k, v in extra.items() if v is not None and v != [] and v != {}}

    def body(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"status": "error", "error_code": self.code,
                               "error": FIXED_MESSAGES.get(self.code, GENERIC_MESSAGE)}
        if self.path:
            out["path"] = self.path
        out.update(self.extra)
        return out


class _Answer(Exception):
    """An error answer in another envelope (404 NOT_FOUND, 401 assertion)."""

    def __init__(self, body: Dict[str, Any], status: int) -> None:
        super().__init__(status)
        self.body = body
        self.status = status


def _not_found(resource: str) -> _Answer:
    return _Answer({"status": "error", "error": f"{resource} not found", "error_code": "NOT_FOUND"}, 404)


def _assertion_rejected() -> _Answer:
    return _Answer({"status": "error", "error": FIXED_MESSAGES["assertion_rejected"],
                    "error_code": "assertion_rejected"}, 401)


def _parse_refusal(spec: Optional[str]) -> Optional[Tuple[int, str]]:
    if not spec:
        return None
    status, _, code = str(spec).partition(":")
    if not status.strip().isdigit() or not code.strip():
        raise ValueError(f"refuse_init_fields must be '<status>:<code>', got {spec!r}")
    return int(status), code.strip()


class MockState:
    """Everything one mock app knows. Nothing is shared between apps."""

    def __init__(self, doc_fields: str, calibrated: bool, refuse_init_fields: Optional[str],
                 brokered: bool) -> None:
        mode = str(doc_fields or "off").strip().lower()
        if mode not in MODES:
            raise ValueError(f"doc_fields must be one of {MODES}, got {doc_fields!r}")
        self.mode = mode
        self.calibrated = bool(calibrated)
        self.refuse = _parse_refusal(refuse_init_fields)
        self.brokered = bool(brokered)
        # Switches for answers a test wants to reach on purpose.
        self.registry_write_allowed = True
        self.relevance_gate_enabled = False
        self.find_scan_budget: Optional[int] = None
        self.quarantined: set = set()
        self.change_forbidden: set = set()

        self.requests: List[Dict[str, Any]] = []
        self.documents: List[Dict[str, Any]] = copy.deepcopy(DOCUMENTS)
        self.stored_pointers: set = set()
        self.engagement_count = 0

        self.settings = {"unknown_keys": "ignore", "date_order": "dmy"}
        self.node_types: Dict[str, Dict[str, Any]] = {}
        self.nodes: Dict[str, Dict[str, Any]] = {}
        for type_name in ("Mandant", "Gericht"):
            tid = stable_id("node_type", type_name)
            self.node_types[tid] = {"id": tid, "name": type_name, "description": None}
        for type_name, name in (("Mandant", "Muster AG"), ("Mandant", "Beispiel GmbH"),
                                ("Gericht", "Bezirksgericht Musterhausen")):
            nid = stable_id("node", name)
            self.nodes[nid] = {"id": nid, "name": name, "description": None,
                               "node_type_id": stable_id("node_type", type_name)}
        self.access_groups = [{
            "group_id": stable_id("group", "Kanzlei"), "name": "Kanzlei", "depth": 0,
            "is_admin": False, "children": [
                {"group_id": stable_id("group", "Administration"), "name": "Administration",
                 "depth": 1, "is_admin": True, "children": []},
                {"group_id": stable_id("group", "Mandate"), "name": "Mandate",
                 "depth": 1, "is_admin": False, "children": []},
            ]}]

        self.fields: Dict[str, Dict[str, Any]] = {}
        self.install(CORE_FIELDS, "core", 1, origin="system")
        self.anchors: Dict[str, Dict[str, Any]] = {}
        self.rules: Dict[str, Dict[str, Any]] = {}
        for doc in self.documents:
            self.add_document(doc["doc_id"], title=doc["title"], path=doc["path"],
                              fields=SEED_VALUES.get(doc["doc_id"]), corpus=False)

    # -- registry -----------------------------------------------------------

    def install(self, definitions, pack_key: str, version: int, *, origin: str) -> Dict[str, Any]:
        """Copy a pack: additive, a key the tenant has is skipped. Entity
        targets resolve by node-type name; none or several -> no target and
        the warning target_type_missing:<key>."""
        installed, skipped, warnings = 0, 0, []
        for definition in definitions:
            if self.field_by_key(definition["key"]) is not None:
                skipped += 1
                continue
            row = {
                "id": stable_id("field", definition["key"]), "key": definition["key"],
                "datatype": definition["datatype"], "cardinality": definition["cardinality"],
                "labels": dict(definition["labels"]), "aliases": list(definition["aliases"]),
                "enum_values": copy.deepcopy(definition.get("enum_values")),
                "date_role": definition.get("date_role"), "date_order": None,
                "fy_start_month": None, "fy_label": None,
                "code_scheme": definition.get("code_scheme"), "target_node_type_id": None,
                "link_policy": "resolve", "sensitivity": "normal", "status": "active",
                "origin": origin, "pack_key": pack_key, "pack_version": version,
                "display": bool(definition.get("display")), "facet": bool(definition.get("facet")),
            }
            names = definition.get("target_names")
            if names:
                wanted = {fold(n) for n in names}
                hits = [t["id"] for t in self.node_types.values() if fold(t["name"]) in wanted]
                if len(hits) == 1:
                    row["target_node_type_id"] = hits[0]
                else:
                    warnings.append(f"target_type_missing:{definition['key']}")
            self.fields[row["id"]] = row
            installed += 1
        return {"installed": installed, "skipped": skipped, "warnings": warnings}

    def field_by_key(self, key: str) -> Optional[Dict[str, Any]]:
        for row in self.fields.values():
            if row["key"] == key:
                return row
        return None

    @staticmethod
    def public_field(row: Dict[str, Any]) -> Dict[str, Any]:
        out = {name: copy.deepcopy(row.get(name)) for name in PUBLIC_FIELD_KEYS}
        out["warnings"] = []
        return out

    def _active(self) -> List[Dict[str, Any]]:
        return [f for f in self.fields.values() if f["status"] != "deprecated"]

    def resolve_key(self, raw_key: Any, path: str) -> Optional[Dict[str, Any]]:
        """Exact key, then the normalised key, an alias, a label: the first
        step with a hit wins, and two fields at one step are ambiguous."""
        if not isinstance(raw_key, str):
            return None
        text = raw_key.replace("\x00", "").strip()
        steps = (
            lambda f: f["key"] == text,
            lambda f: f["key"] == normalize_key(text),
            lambda f: fold(text) in {fold(a) for a in f.get("aliases") or ()},
            lambda f: fold(text) in {fold(v) for v in (f.get("labels") or {}).values()},
        )
        for step in steps:
            hits = [f for f in self._active() if step(f)]
            if len(hits) > 1:
                raise DocFieldError("ambiguous_field", 400, path,
                                    candidates=sorted(f["key"] for f in hits))
            if hits:
                return hits[0]
        return None

    def resolve_where_key(self, raw_key: Any, path: str) -> Dict[str, Any]:
        """`resolve_key`, plus a deprecated field by its exact key."""
        row = self.resolve_key(raw_key, path)
        if row is None and isinstance(raw_key, str):
            row = self.field_by_key(raw_key.strip())
        if row is None:
            raise DocFieldError("unknown_field", 400, path, suggest=self.suggest(raw_key))
        return row

    def suggest(self, raw_key: Any, limit: int = 3) -> List[str]:
        if not isinstance(raw_key, str):
            return []
        choices: Dict[str, str] = {}
        for f in self._active():
            choices.setdefault(f["key"], f["key"])
            for text in list((f.get("labels") or {}).values()) + list(f.get("aliases") or ()):
                choices.setdefault(fold(text), f["key"])
        out: List[str] = []
        for probe in (normalize_key(raw_key), fold(raw_key)):
            for match in difflib.get_close_matches(probe, list(choices), n=limit * 3, cutoff=0.75):
                if choices[match] not in out:
                    out.append(choices[match])
        return out[:limit]

    # -- values -------------------------------------------------------------

    def nodes_named(self, name: str, target: Optional[str]) -> List[str]:
        folded = fold(name)
        return [n["id"] for n in self.nodes.values()
                if fold(n["name"]) == folded and (target is None or n["node_type_id"] == target)]

    def normalize(self, field: Dict[str, Any], raw: Any, *,
                  asserted: bool = True) -> Tuple[Any, Optional[str]]:
        """(stored value, or None when dropped; warning code). Not the
        server's normaliser: enum labels map to codes, an entity name links
        to the one node of the target type with the same folded name, and
        everything else is kept as sent."""
        dt = field["datatype"]
        if isinstance(raw, str):
            raw = raw.strip()
            if not raw or (len(raw) > 256 and dt != "money"):
                return None, "invalid_value"
        if dt == "enum":
            if not isinstance(raw, str):
                return None, "type_mismatch"
            for item in field.get("enum_values") or ():
                code = item["code"] if isinstance(item, dict) else str(item)
                names = {fold(code)}
                if isinstance(item, dict):
                    names |= {fold(v) for v in (item.get("labels") or {}).values()}
                    names |= {fold(a) for a in item.get("aliases") or ()}
                if raw == code or fold(raw) in names:
                    return code, None
            return None, "invalid_value"
        if dt == "entity_ref":
            if self.brokered and not asserted:
                # The server resolves the uploader to link a name: a
                # BROKERED tenant without an assertion refuses the init.
                raise _assertion_rejected()
            target = field.get("target_node_type_id")
            if isinstance(raw, dict) and "node_id" in raw and set(raw) <= {"node_id", "name"}:
                node = self.nodes.get(str(raw["node_id"]))
                if node is None or (target and node["node_type_id"] != target):
                    return None, "invalid_value"
                return {"node_id": node["id"], "name": node["name"]}, None
            if isinstance(raw, dict) and set(raw) == {"name"} and isinstance(raw["name"], str):
                raw = raw["name"].strip()
            if not isinstance(raw, str):
                return None, "type_mismatch"
            if not raw or len(raw) > 256:
                return None, "invalid_value"
            if target is None:
                return {"name": raw}, "unresolved_entity"
            hits = self.nodes_named(raw, target)
            if len(hits) == 1:
                return {"node_id": hits[0], "name": self.nodes[hits[0]]["name"]}, None
            return {"name": raw}, "ambiguous_entity" if hits else "unresolved_entity"
        if dt == "bool":
            if isinstance(raw, bool):
                return raw, None
            if isinstance(raw, int) and raw in (0, 1):
                return bool(raw), None
            if isinstance(raw, str) and fold(raw) in _YES | _NO:
                return fold(raw) in _YES, None
            return None, "invalid_value"
        if isinstance(raw, (bool, list)):
            return None, "type_mismatch"
        if dt == "money" and isinstance(raw, (int, float)):
            return None, "type_mismatch"
        if dt == "text":
            if isinstance(raw, dict):
                return None, "type_mismatch"
            return str(raw), None
        return raw, None

    def shown(self, field: Dict[str, Any], value: Any, *, node_ids: bool) -> Any:
        """A stored value as a reader sees it: an entity carries its node id
        only on the routes that show it (doc-values, find, rules)."""
        if field["datatype"] != "entity_ref" or not isinstance(value, dict):
            return copy.deepcopy(value)
        if "node_id" in value and node_ids:
            return {"node_id": value["node_id"], "name": value.get("name")}
        if value.get("name"):
            return {"name": value["name"]}
        return {"hidden": True}

    def add_document(self, pointer: str, *, title: Optional[str] = None, path: Optional[str] = None,
                     snippet: str = "", fields: Optional[Dict[str, Any]] = None,
                     corpus: bool = True) -> Dict[str, Any]:
        """Seed a document: searchable by its title and snippet (`corpus`),
        with `fields` as its upload layer (normalised as on init, warnings
        dropped). Raises KeyError for a key the registry does not have."""
        if corpus and not any(d["doc_id"] == pointer for d in self.documents):
            self.documents.append({"doc_id": pointer, "title": title or _basename(pointer),
                                   "path": path or pointer, "type": "document",
                                   "snippet": snippet, "timestamp": _now_iso()})
        anchor = self.anchor(pointer, create=True)
        anchor.update({"title": title, "path": path, "ingested_at": _now_iso()})
        for key, raw in (fields or {}).items():
            field = self.field_by_key(key)
            if field is None:
                raise KeyError(key)
            items = raw if isinstance(raw, list) else [raw]
            kept = [v for v, _code in (self.normalize(field, item) for item in items) if v is not None]
            if kept:
                anchor["upload"][field["key"]] = {"values": kept, "created_at": _now_iso(),
                                                  "source_ref": f"transmission:{uuid4()}"}
        anchor["version"] += 1
        return anchor

    def anchor(self, pointer: str, *, create: bool = False) -> Optional[Dict[str, Any]]:
        if pointer not in self.anchors and create:
            self.anchors[pointer] = {
                "version": 0, "title": None, "manual_title": None, "description": None,
                "manual_description": None, "path": None, "ingested_at": None,
                "upload": {}, "manual": {}, "document_uuid": stable_id("document", pointer),
            }
        return self.anchors.get(pointer)

    def known(self, pointer: str) -> bool:
        return (pointer in self.anchors or pointer in self.stored_pointers
                or any(d["doc_id"] == pointer for d in self.documents))

    def title_of(self, pointer: str) -> Tuple[str, str]:
        anchor = self.anchors.get(pointer) or {}
        if anchor.get("manual_title"):
            return anchor["manual_title"], "manual"
        if anchor.get("title"):
            return anchor["title"], "upload"
        return _basename(anchor.get("path")) or _basename(pointer), "path"

    def rule_values(self, pointer: str) -> Dict[str, Tuple[List[Any], Dict[str, Any]]]:
        """{key: (values, rule)}: the longest live prefix wins per field, and
        an empty set (a null in the rule) switches a shorter one off."""
        out: Dict[str, Tuple[List[Any], Dict[str, Any]]] = {}
        for prefix in sorted(self.rules, key=len):
            if pointer.startswith(prefix):
                rule = self.rules[prefix]
                for key, values in rule["set"].items():
                    out[key] = (values, rule)
        return {k: v for k, v in out.items() if v[0]}

    def effective(self, pointer: str) -> Dict[str, Tuple[str, List[Any]]]:
        """{key: (layer, values)}: manual > upload > rule; an unset marker
        in the manual layer hides the lower ones."""
        anchor = self.anchors.get(pointer)
        if anchor is None:
            return {}
        out: Dict[str, Tuple[str, List[Any]]] = {}
        for key, (values, _rule) in self.rule_values(pointer).items():
            out[key] = ("rule", values)
        for key, entry in anchor["upload"].items():
            out[key] = ("upload", entry["values"])
        for key, entry in anchor["manual"].items():
            if entry.get("unset"):
                out.pop(key, None)
            else:
                out[key] = ("manual", entry["values"])
        return out

    def fields_of(self, pointer: str, keys: Optional[List[str]] = None, *,
                  node_ids: bool) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, (_layer, values) in sorted(self.effective(pointer).items()):
            field = self.field_by_key(key)
            if field is None or (keys is not None and key not in keys):
                continue
            shown = [self.shown(field, v, node_ids=node_ids) for v in values]
            out[key] = shown if field["cardinality"] == "many" else shown[0]
        return out

    def values_view(self, pointer: str) -> Dict[str, Any]:
        anchor = self.anchors.get(pointer)
        if anchor is None:
            return {"pointer": pointer, "document_uuid": stable_id("document", pointer),
                    "acl_mode": "follows_document", "version": 0,
                    "title": _basename(pointer), "title_source": "path", "description": None,
                    "path": None, "ingested_at": None, "warnings": [], "fields": {}, "layers": {}}
        if pointer in self.quarantined:
            return {"pointer": pointer, "acl_mode": "quarantined", "version": anchor["version"]}
        title, source = self.title_of(pointer)
        effective = self.effective(pointer)
        entries = []
        for key, entry in anchor["manual"].items():
            entries.append(("manual", key, entry.get("values") or [None], entry, None))
        for key, entry in anchor["upload"].items():
            entries.append(("upload", key, entry["values"], entry, entry.get("source_ref")))
        for key, (values, rule) in self.rule_values(pointer).items():
            entries.append(("rule", key, values, rule, f"rule:{rule['id']}"))
        layers: Dict[str, List[Dict[str, Any]]] = {}
        for layer, key, values, entry, source_ref in entries:
            field = self.field_by_key(key)
            if field is None:
                continue
            for value in values:
                row = {"layer": layer,
                       "value": None if value is None else self.shown(field, value, node_ids=True),
                       "verified": False,
                       "effective": effective.get(key, ("",))[0] == layer and not entry.get("unset"),
                       "source_ref": source_ref,
                       "created_at": entry.get("created_at")}
                if entry.get("unset"):
                    row["unset"] = True
                layers.setdefault(key, []).append(row)
        description = anchor.get("manual_description")
        if description is None:
            description = anchor.get("description")
        return {"pointer": pointer, "document_uuid": anchor["document_uuid"],
                "acl_mode": "follows_document", "version": anchor["version"],
                "title": title, "title_source": source, "description": description,
                "path": anchor.get("path"), "ingested_at": anchor.get("ingested_at"),
                "warnings": [], "fields": self.fields_of(pointer, node_ids=True),
                "layers": dict(sorted(layers.items()))}

    # -- where --------------------------------------------------------------

    def resolve_where(self, where: Any, return_fields: Any) -> Dict[str, Any]:
        if where is not None and (not isinstance(where, dict) or not where):
            raise DocFieldError("invalid_value", 400, "where")
        if where is not None and len(where) > MAX_WHERE_KEYS:
            raise DocFieldError("where_too_complex", 400, "where")
        clauses: List[Dict[str, Any]] = []
        taken: set = set()
        for raw_key, raw_value in (where or {}).items():
            path = "where." + str(raw_key)[:64]
            field = self.resolve_where_key(raw_key, path)
            if field["id"] in taken:
                raise DocFieldError("invalid_value", 400, path)
            taken.add(field["id"])
            clauses.extend(self._clauses(field, raw_value, path))
        return {"clauses": clauses,
                "return_keys": self._return_keys(return_fields),
                "echo": [self._clause_echo(c) for c in clauses],
                "empty_visible": any(c["field"]["datatype"] == "entity_ref" and c["op"] != "exists"
                                     and not c["names"] and not c["node_ids"] for c in clauses)}

    def _return_keys(self, return_fields: Any):
        if return_fields is None or return_fields is False:
            return None
        if return_fields is True:
            return True
        if not isinstance(return_fields, list):
            raise DocFieldError("invalid_value", 400, "return_fields")
        if len(return_fields) > MAX_RETURN_FIELDS:
            raise DocFieldError("where_too_complex", 400, "return_fields")
        keys: List[str] = []
        for i, raw in enumerate(return_fields):
            path = f"return_fields[{i}]"
            if not isinstance(raw, str):
                raise DocFieldError("invalid_value", 400, path)
            key = raw.strip() if raw.strip() in ANCHOR_KEYS else self.resolve_where_key(raw, path)["key"]
            if key not in keys:
                keys.append(key)
        return keys

    def wanted_keys(self, return_keys) -> Optional[List[str]]:
        """`true`: every active non-special field plus the title."""
        if return_keys is True:
            return ["title"] + [f["key"] for f in self._active() if f["sensitivity"] != "special"]
        if isinstance(return_keys, list) and return_keys:
            return list(return_keys)
        return None

    def _clauses(self, field: Dict[str, Any], raw: Any, path: str) -> List[Dict[str, Any]]:
        is_ops = (isinstance(raw, dict) and bool(raw) and set(raw) <= set(OPS) | {"match"}
                  and bool(set(raw) & set(OPS)))
        ops = dict(raw) if is_ops else ({"in": raw} if isinstance(raw, list) else {"eq": raw})
        match = ops.pop("match", "certain")
        if match not in ("certain", "possible"):
            raise DocFieldError("invalid_value", 400, path + ".match")
        out = []
        for op in OPS:
            if op in ops:
                value = ops[op]
                op_path = f"{path}.{op}" if is_ops else path
                if op == "eq" and isinstance(value, list):
                    op = "in"
                out.append(self._clause(field, op, value, match, op_path))
        return out

    def _clause(self, field, op, value, match, path) -> Dict[str, Any]:
        dt = field["datatype"]
        clause = {"field": field, "op": op, "operands": [], "names": set(), "node_ids": set(),
                  "match": match}
        if op == "exists":
            if value is not True:
                raise DocFieldError("invalid_value", 400, path)
            return clause
        if ((dt == "entity_ref" and op not in ("eq", "in"))
                or (op in RANGE_OPS and dt not in ("date", "period", "number", "money"))
                or (op in ("overlaps", "within") and dt not in INTERVAL_TYPES)
                or (op == "prefix" and dt not in ("code", "text", "enum"))):
            raise DocFieldError("invalid_value", 400, path)
        if op == "in":
            if not isinstance(value, list) or not value or len(value) > MAX_WHERE_LIST:
                raise DocFieldError("where_too_complex", 400, path)
            items = value
        elif op == "between" or (op == "within" and isinstance(value, list)):
            if not isinstance(value, list) or len(value) != 2:
                raise DocFieldError("invalid_value", 400, path)
            items = value
        else:
            items = [value]
        for item in items:
            if dt == "entity_ref":
                if isinstance(item, dict):
                    if len(item) != 1 or not set(item) <= {"name", "node_id"}:
                        raise DocFieldError("type_mismatch", 400, path)
                    if "node_id" in item:
                        if str(item["node_id"]) in self.nodes:
                            clause["node_ids"].add(str(item["node_id"]))
                        continue
                    item = item["name"]
                if not isinstance(item, str) or not item.strip() or len(item) > 256:
                    raise DocFieldError("invalid_value", 400, path)
                clause["names"].add(fold(item))
                clause["node_ids"].update(self.nodes_named(item, field.get("target_node_type_id")))
                continue
            if op == "prefix":
                if not isinstance(item, str) or not item:
                    raise DocFieldError("invalid_value", 400, path)
                clause["operands"].append(item)
                continue
            stored, code = self.normalize(field, item)
            if stored is None:
                raise DocFieldError("type_mismatch" if code == "type_mismatch" else "invalid_value",
                                    400, path)
            clause["operands"].append(stored)
        return clause

    @staticmethod
    def _clause_echo(clause: Dict[str, Any]) -> Dict[str, Any]:
        """The query's own values (planner._clause_echo), never stored data."""
        field, op = clause["field"], clause["op"]
        out: Dict[str, Any] = {"field": field["key"], "op": op}
        if op == "exists":
            return out
        if field["datatype"] == "entity_ref":
            out["resolved_nodes"] = len(clause["node_ids"])
            return out
        values = clause["operands"]
        if op == "prefix":
            out["prefix"] = values[0]
        elif field["datatype"] in INTERVAL_TYPES:
            # The mock does not parse dates: an operand is its own interval.
            if op == "in":
                out["intervals"] = [[v, v] for v in values]
            elif len(values) == 2:
                out["interval"] = [values[0], values[1]]
            else:
                out["interval"] = [values[0], values[0]]
        elif op in ("in", "between"):
            out["values"] = values
        else:
            out["value"] = values[0]
        if clause["match"] == "possible" and field["datatype"] in INTERVAL_TYPES and op in RANGE_OPS:
            out["match"] = "possible"
        return out

    @staticmethod
    def _order_key(value: Any) -> Tuple[int, Any]:
        if isinstance(value, dict):
            value = value.get("lo", value.get("amount", json.dumps(value, sort_keys=True)))
        try:
            return (0, float(str(value).replace("'", "")))
        except ValueError:
            return (1, str(value))

    def _value_matches(self, clause: Dict[str, Any], value: Any) -> bool:
        op, operands = clause["op"], clause["operands"]
        if clause["field"]["datatype"] == "entity_ref":
            if not isinstance(value, dict):
                return False
            if value.get("node_id"):
                return value["node_id"] in clause["node_ids"]
            return fold(value.get("name") or "") in clause["names"]
        if op in ("eq", "in", "overlaps") or (op == "within" and len(operands) != 2):
            return any(value == o for o in operands)
        if op == "prefix":
            return fold(value).startswith(fold(operands[0]))
        key = self._order_key(value)
        if op in ("between", "within"):
            return self._order_key(operands[0]) <= key <= self._order_key(operands[1])
        bound = self._order_key(operands[0])
        return {"gt": key > bound, "gte": key >= bound, "lt": key < bound, "lte": key <= bound}[op]

    def matches(self, pointer: str, clauses: List[Dict[str, Any]]) -> bool:
        """Every clause met by the same pointer; quarantined never match."""
        if pointer in self.quarantined:
            return False
        effective = self.effective(pointer)
        for clause in clauses:
            values = effective.get(clause["field"]["key"], ("", []))[1]
            if clause["op"] == "exists":
                if not values:
                    return False
            elif not any(self._value_matches(clause, v) for v in values):
                return False
        return True


def _echo(resolved: Dict[str, Any], *, query: bool, gate: bool = True,
          no_strong_matches: bool = False, planned: bool = True) -> Dict[str, Any]:
    echo: Dict[str, Any] = {"applied": True, "clauses": len(resolved["clauses"]),
                            "resolved": resolved["echo"]}
    if query:
        echo["relevance_gate_applied"] = gate
        echo["no_strong_matches"] = no_strong_matches
        echo["may_be_partial"] = False
        if planned:
            # The mock caller sits inside the aggregate closure.
            echo["strategy"] = "allowlist"
            echo["exhaustive"] = True
    return echo


def _encode_cursor(sort_token: str, order: str, key: Any, pointer: str) -> str:
    raw = json.dumps({"v": 1, "s": sort_token, "o": order, "k": key, "p": pointer},
                     separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(after: Any, sort_token: str, order: str) -> Optional[Tuple[Any, str]]:
    if after is None:
        return None
    if not isinstance(after, str) or not after:
        raise DocFieldError("invalid_cursor", 400, "after")
    try:
        payload = json.loads(base64.urlsafe_b64decode(after + "=" * (-len(after) % 4)))
    except Exception:  # noqa: BLE001 - every malformed cursor is the same 400
        raise DocFieldError("invalid_cursor", 400, "after") from None
    if (not isinstance(payload, dict) or set(payload) != {"v", "s", "o", "k", "p"}
            or payload["v"] != 1 or payload["s"] != sort_token or payload["o"] != order
            or not isinstance(payload["p"], str)):
        raise DocFieldError("invalid_cursor", 400, "after")
    return payload["k"], payload["p"]


def create_app(doc_fields: Optional[str] = None, calibrated: bool = True,
               refuse_init_fields: Optional[str] = None, brokered: bool = False) -> Flask:
    """One mock Knovas API with its own state. `doc_fields` defaults to the
    env MOCK_DOC_FIELDS, read at call time (default `off`)."""
    if doc_fields is None:
        doc_fields = os.environ.get("MOCK_DOC_FIELDS", "off")
    app = Flask(__name__)
    state = MockState(doc_fields, calibrated, refuse_init_fields, brokered)
    app.extensions["knovas_mock"] = state

    # -- plumbing -----------------------------------------------------------

    def _body() -> Dict[str, Any]:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def _asserted(body: Dict[str, Any]) -> bool:
        token = body.get("principal_assertion") or request.args.get("principal_assertion")
        return isinstance(token, str) and bool(token.strip())

    def _caller(body: Dict[str, Any]) -> None:
        if state.brokered and not _asserted(body):
            raise _assertion_rejected()

    def _registry_writer() -> None:
        if not state.registry_write_allowed:
            raise DocFieldError("registry_write_requires_full_clearance", 403)

    def _definition(body: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in body.items() if k not in CALLER_KEYS}

    def _success(message: str, data: Optional[Dict[str, Any]] = None, status: int = 200):
        out: Dict[str, Any] = {"status": "success", "message": message}
        out.update(data or {})
        return jsonify(out), status

    @app.before_request
    def _record_and_gate():
        state.requests.append({
            "method": request.method, "path": request.path,
            "query": request.args.to_dict(flat=True), "body": request.get_data(cache=True),
            "json": request.get_json(silent=True),
        })
        if state.mode == "off" and any(request.path == p or request.path.startswith(p + "/")
                                       for p in OWNED_PREFIXES):
            abort(404)
        return None

    @app.errorhandler(HTTPException)
    def _http_exception_as_json(e: HTTPException):
        # The server's app-wide handler (error_handling_service.py): an unknown
        # route, and every doc-fields path while the feature is off.
        return jsonify({"status": "error", "error": e.description or e.name,
                        "error_code": f"HTTP_{e.code}"}), e.code

    @app.errorhandler(DocFieldError)
    def _doc_field_error(exc: DocFieldError):
        return jsonify(exc.body()), exc.status

    @app.errorhandler(_Answer)
    def _answer(exc: _Answer):
        return jsonify(exc.body), exc.status

    # -- routes that predate document fields --------------------------------

    @app.get("/health")
    def health() -> Any:
        return jsonify({"status": "healthy", "mock": True, "timestamp": datetime.now(timezone.utc).isoformat()})

    @app.get("/api/search")
    def search() -> Any:
        query = (request.args.get("query") or "").strip().lower()
        limit_raw = request.args.get("limit", "20")
        try:
            limit = max(1, int(limit_raw))
        except ValueError:
            limit = 20

        if not query:
            results = state.documents[:limit]
        else:
            results = [
                doc
                for doc in state.documents
                if query in (doc.get("title", "").lower() + " " + doc.get("snippet", "").lower())
            ][:limit]

        return jsonify({"success": True, "results": results, "total": len(results), "mock": True})

    @app.post("/api/docs/full-sync")
    def full_sync() -> Any:
        payload = request.get_json(silent=True) or {}
        documents = payload.get("documents", [])
        accepted = len(documents) if isinstance(documents, list) else 0
        return jsonify(
            {
                "success": True,
                "mock": True,
                "accepted": accepted,
                "sync_id": f"sync-{uuid4()}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    @app.post("/api/docs/new")
    def new_doc() -> Any:
        payload = request.get_json(silent=True) or {}
        doc_id = payload.get("doc_id", f"new-{uuid4()}")
        return jsonify(
            {
                "success": True,
                "mock": True,
                "doc_id": doc_id,
                "indexed": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    @app.get("/secured/health")
    def secured_health() -> Any:
        return jsonify({"status": "success", "message": "healthy", "mock": True})

    @app.post("/secured/query")
    def secured_query() -> Any:
        payload = request.get_json(silent=True) or {}
        raw_limit = payload.get("limit")
        if raw_limit is not None and (not isinstance(raw_limit, int) or isinstance(raw_limit, bool)
                                      or raw_limit < 1 or raw_limit > 50):
            # The server's bound (query_pipeline.py), in every mode.
            return jsonify({"status": "error", "error": "limit must be an integer between 1 and 50",
                            "type": "validation_error", "field": "limit"}), 422
        _caller(payload)
        where = payload.get("where")
        return_fields = payload.get("return_fields")
        resolved = None
        if state.mode != "off" and (where is not None or return_fields is not None):
            if state.mode == "values":
                raise DocFieldError("where_unsupported", 400)
            if where is not None and not state.calibrated:
                raise DocFieldError("where_requires_calibration", 503)
            resolved = state.resolve_where(where, return_fields)
        where_sent = resolved is not None and where is not None
        # Every entity clause resolved to nothing visible: answered without
        # searching (no_results_reason empty_where).
        short_circuit = where_sent and resolved["empty_visible"]

        query_input = payload.get("Input")
        queries: List[str] = []
        if isinstance(query_input, list):
            queries = [str(q) for q in query_input if str(q).strip()]
        elif query_input:
            queries = [str(query_input)]

        results = []
        for doc in ([] if short_circuit else state.documents):
            hay = (doc.get("title", "") + " " + doc.get("snippet", "")).lower()
            if not queries or any(q.lower() in hay for q in queries):
                if where_sent and not state.matches(doc["doc_id"], resolved["clauses"]):
                    continue
                results.append(
                    {
                        "pointer": doc["doc_id"],
                        "document_uuid": (str(uuid4()) if state.mode == "off"
                                          else stable_id("document", doc["doc_id"])),
                        "final_score": 0.9,
                        "cosine_similarity": 0.88,
                        "cosine_distance": 0.12,
                        "ingested_summary": {"present": True, "text": doc.get("snippet", "")},
                        "page_number": 1,
                        "sentence_number": 1,
                        "top_chunks": [],
                    }
                )

        body = {
            "status": "success",
            "message": "Query executed successfully",
            "query_session_id": str(uuid4()),
            "pointers": [r["pointer"] for r in results],
            "result_count": len(results),
            "results": results,
            "meta": {"embed_latency_ms": 1, "stage1_latency_ms": 1, "stage2_latency_ms": 1},
            "mock": True,
        }
        if state.mode == "off":
            return jsonify(body)

        # A query with `where` is always relevance-gated on the server.
        gate = (where_sent or state.relevance_gate_enabled) and not short_circuit
        if gate:
            for result in results:
                result["relevance_tier"] = "strong"
        body["no_strong_matches"] = not results
        if not results:
            body["no_results_reason"] = "empty_where" if short_circuit else "no_candidates"
        body["relevance_gate_applied"] = gate
        if where_sent:
            body["where"] = _echo(resolved, query=True, gate=gate,
                                  no_strong_matches=not results, planned=not short_circuit)
        wanted = state.wanted_keys(resolved["return_keys"]) if resolved else None
        if wanted is not None:
            for result in results:
                fields = state.fields_of(result["pointer"], wanted, node_ids=False)
                if "title" in wanted and result["pointer"] in state.anchors:
                    fields["title"] = state.title_of(result["pointer"])[0]
                result["fields"] = dict(sorted(fields.items()))
            body["return_fields"] = {"applied": True}
        return jsonify(body)

    @app.post("/secured/init_document_transmission")
    def secured_init() -> Any:
        payload = request.get_json(silent=True) or {}
        if state.brokered and "access_groups" in payload and not _asserted(payload):
            raise _assertion_rejected()
        echo = staged = None
        fields_mode = "replace"
        if state.mode != "off" and payload.get("fields") is not None:
            if state.refuse is not None:
                status, code = state.refuse
                raise DocFieldError(code, status, "fields" if status in (400, 422) else None)
            echo, staged, fields_mode = _parse_init_fields(payload)
        key = str(uuid4())
        pointer = payload.get("identifier")
        if pointer:
            state.stored_pointers.add(str(pointer))
        if state.mode != "off" and pointer:
            _commit_upload(str(pointer), payload, staged, fields_mode)
        body = {
            "status": "success",
            "message": "Transmission initialized",
            "transmission_key_id": key,
            "mock": True,
        }
        if echo is not None:
            # At the top level, next to transmission_key_id; only when staged.
            body["fields"] = echo
        return jsonify(body), 201

    def _parse_init_fields(payload: Dict[str, Any]):
        """The server's init order: shape, bounds, keys, values, strict."""
        fields = payload.get("fields")
        strict = payload.get("fields_strict", False)
        fields_mode = payload.get("fields_mode", "replace")
        if not isinstance(fields, dict):
            raise DocFieldError("invalid_fields", 400, "fields")
        if not isinstance(strict, bool):
            raise DocFieldError("invalid_fields", 400, "fields_strict")
        if fields_mode not in ("replace", "merge"):
            raise DocFieldError("invalid_fields", 400, "fields_mode")
        size = len(json.dumps(fields, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        if len(fields) > MAX_UPLOAD_KEYS or size > MAX_UPLOAD_BYTES:
            raise DocFieldError("fields_too_large", 400, "fields")
        for raw_key, raw in fields.items():
            if isinstance(raw, list) and len(raw) > MAX_VALUES_PER_KEY:
                raise DocFieldError("fields_too_large", 400, f"fields.{raw_key}")
            if not str(raw_key).strip():
                raise DocFieldError("invalid_fields", 400, f"fields.{raw_key}")
        asserted = _asserted(payload)
        mapped: Dict[str, str] = {}
        unknown: List[str] = []
        suggest: Dict[str, List[str]] = {}
        warnings: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []
        staged: Dict[str, List[Any]] = {}
        taken: set = set()
        for raw_key, raw in fields.items():
            path = f"fields.{raw_key}"
            if normalize_key(raw_key) in SYSTEM_KEYS:
                warnings.append({"key": str(raw_key), "path": path, "code": "invalid_value"})
                continue
            field = state.resolve_key(raw_key, path)
            if field is None:
                policy = state.settings["unknown_keys"]
                if strict:
                    errors.append({"path": path, "code": "unknown_field"})
                    continue
                if policy == "reject":
                    raise DocFieldError("unknown_field", 422, path)
                if policy == "register":
                    if state.brokered and not asserted:
                        raise _assertion_rejected()
                    key = normalize_key(raw_key)
                    if looks_personal(raw_key) or not KEY_RE.match(key or "-"):
                        warnings.append({"key": str(raw_key), "path": path,
                                         "code": "key_looks_personal"})
                        unknown.append(raw_key)
                        continue
                    field = _create_field({"key": key, "datatype": "text", "status": "provisional"},
                                          origin="auto")
                else:
                    unknown.append(raw_key)
                    close = state.suggest(raw_key)
                    if close:
                        suggest[raw_key] = close
                    continue
            if field["id"] in taken:
                raise DocFieldError("invalid_fields", 400, path)
            taken.add(field["id"])
            mapped[raw_key] = field["key"]
            if raw is None or raw == []:
                staged[field["key"]] = []          # clears the key (merge)
                continue
            items = raw if isinstance(raw, list) else [raw]
            kept = []
            for i, item in enumerate(items):
                item_path = f"{path}[{i}]" if isinstance(raw, list) else path
                value, code = state.normalize(field, item, asserted=asserted)
                if value is not None and field["cardinality"] == "one" and kept:
                    value, code = None, "cap_exceeded"
                if code:
                    warnings.append({"key": field["key"], "path": item_path, "code": code})
                    if strict:
                        errors.append({"path": item_path, "code": code})
                if value is not None:
                    kept.append(value)
            if kept:
                # A key whose values were all invalid is not staged, so a
                # merge does not clear its previous value.
                staged[field["key"]] = kept
        if errors:
            raise DocFieldError("invalid_fields", 422, errors=errors)
        echo: Dict[str, Any] = {"staged": sum(1 for v in staged.values() if v),
                                "mapped_keys": mapped, "unknown_keys": unknown,
                                "warnings": warnings}
        if suggest:
            echo["suggest"] = suggest
        return echo, staged, fields_mode

    def _commit_upload(pointer: str, payload: Dict[str, Any], staged: Optional[Dict[str, List[Any]]],
                       fields_mode: str) -> None:
        """Stores at once (the server commits after the document is stored).
        An init without `fields` keeps the previous upload layer."""
        anchor = state.anchor(pointer, create=True)
        for key in ("title", "description", "path"):
            if isinstance(payload.get(key), str):
                anchor[key] = payload[key]
        anchor["ingested_at"] = _now_iso()
        if staged is not None:
            source_ref = f"transmission:{uuid4()}"
            if fields_mode == "replace":
                anchor["upload"] = {}
            for key, values in staged.items():
                if values:
                    anchor["upload"][key] = {"values": values, "created_at": _now_iso(),
                                             "source_ref": source_ref}
                else:
                    anchor["upload"].pop(key, None)
        anchor["version"] += 1
        if not any(d["doc_id"] == pointer for d in state.documents):
            state.documents.append({
                "doc_id": pointer, "title": anchor.get("title") or _basename(pointer),
                "path": anchor.get("path") or pointer, "type": "document",
                "snippet": anchor.get("description") or "", "timestamp": _now_iso(),
            })

    @app.post("/secured/transmit_document_part")
    def secured_transmit() -> Any:
        payload = request.get_json(silent=True) or {}
        part_count = int(payload.get("part_number", 0))
        complete = part_count >= 0
        return jsonify(
            {
                "status": "success",
                "message": "Success",
                "transmission_complete": complete,
                "mock": True,
            }
        )

    @app.delete("/secured/delete_information_object")
    def secured_delete() -> Any:
        payload = request.get_json(silent=True) or {}
        pointer = str(payload.get("pointer") or "")
        if pointer not in state.stored_pointers:
            return jsonify({"status": "error", "message": "not found"}), 404
        state.stored_pointers.discard(pointer)
        return jsonify(
            {
                "status": "success",
                "message": "deleted",
                "document_uuid": str(uuid4()),
                "deleted_sentences": 1,
                "deleted_versions": 1,
                "mock": True,
            }
        )

    @app.post("/secured/analytics/engagement")
    def secured_engagement() -> Any:
        payload = request.get_json(silent=True) or {}
        events = payload.get("events") or []
        if not payload.get("query_session_id") or not events:
            return jsonify({"status": "error", "message": "bad request"}), 400
        accepted = len(events)
        state.engagement_count += accepted
        return jsonify(
            {
                "status": "success",
                "message": "Engagement events accepted",
                "accepted": accepted,
                "mock": True,
            }
        ), 202

    @app.post("/secured/sign_certificate")
    def secured_sign_certificate() -> Any:
        payload = request.get_json(silent=True) or {}
        csr = payload.get("csr") or ""
        if "BEGIN CERTIFICATE REQUEST" not in str(csr):
            return jsonify({"status": "error", "message": "invalid csr"}), 400
        return jsonify(
            {
                "status": "success",
                "message": "Certificate created successfully",
                "certificate": "-----BEGIN CERTIFICATE-----\nMOCK\n-----END CERTIFICATE-----\n",
                "certificate_chain": "-----BEGIN CERTIFICATE-----\nMOCK-CA\n-----END CERTIFICATE-----\n",
                "serial_number": "123",
                "expires_at": datetime.now(timezone.utc).isoformat(),
                "validity_days": payload.get("validity_days", 365),
                "mock": True,
            }
        )

    # -- graph vocabulary and access groups (read by the doc-fields UI) -----

    @app.get("/secured/graph/node-types")
    def node_types() -> Any:
        _caller(_body())
        return _success("Node types", {"node_types": list(state.node_types.values())})

    @app.get("/secured/graph/nodes")
    def nodes() -> Any:
        # Filtered by node_type_id only: the Platform never sends `q`, a typed
        # name prefix that would land in the gateway's access log.
        _caller(_body())
        wanted = request.args.get("node_type_id")
        rows = [n for n in state.nodes.values() if not wanted or n["node_type_id"] == wanted]
        return _success("Nodes", {"nodes": sorted(rows, key=lambda n: n["name"])})

    @app.get("/secured/access_groups")
    def access_groups() -> Any:
        return _success("Access groups retrieved",
                        {"groups": copy.deepcopy(state.access_groups), "epoch": 1})

    # -- doc-values ---------------------------------------------------------

    def _pointer(value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 1000 or "\x00" in value:
            raise DocFieldError("invalid_value", 400, "pointer")
        return value

    @app.get("/secured/graph/doc-values")
    def get_doc_values() -> Any:
        body = _body()
        _caller(body)
        # The pointer is read from the JSON body only (spec S2): a pointer in
        # the query string lands in the gateway's access log, so a client
        # that still sends it there gets 400 invalid_value, path "pointer".
        pointer = _pointer(body.get("pointer"))
        if not state.known(pointer):
            raise _not_found("Document")
        return _success("Document values", state.values_view(pointer))

    @app.patch("/secured/graph/doc-values")
    def patch_doc_values() -> Any:
        body = _body()
        _caller(body)
        pointer = _pointer(body.get("pointer"))
        if_version = body.get("if_version")
        if if_version is None:
            raise DocFieldError("if_version_required", 400, "if_version")
        if isinstance(if_version, bool) or not isinstance(if_version, int) or if_version < 0:
            raise DocFieldError("invalid_value", 400, "if_version")
        strict = body.get("fields_strict", False)
        if not isinstance(strict, bool):
            raise DocFieldError("invalid_value", 400, "fields_strict")
        edit = _prepare_edit(body, strict)          # 400/422 before the pointer
        if not state.known(pointer):
            raise _not_found("Document")
        if pointer in state.change_forbidden:
            raise DocFieldError("change_not_authorized", 403)
        if pointer in state.quarantined:
            raise DocFieldError("anchor_quarantined", 409)
        anchor = state.anchor(pointer, create=True)
        if anchor["version"] != if_version:
            raise DocFieldError("version_conflict", 409, current_version=anchor["version"])
        _apply_edit(pointer, anchor, edit)
        anchor["version"] += 1
        title, source = state.title_of(pointer)
        description = anchor.get("manual_description")
        if description is None:
            description = anchor.get("description")
        return _success("Document values updated", {
            "pointer": pointer, "version": anchor["version"], "title": title,
            "title_source": source, "description": description,
            # As on the server: {} when the edit named no typed key.
            "fields": state.fields_of(pointer, node_ids=True) if edit["typed_keys"] else {},
            "warnings": edit["warnings"],
        })

    def _prepare_edit(body: Dict[str, Any], strict: bool) -> Dict[str, Any]:
        edit: Dict[str, Any] = {"system": {}, "set": {}, "unset": [], "add": {}, "remove": {},
                                "warnings": [], "typed_keys": False}
        sections = {name: body.get(name) for name in ("set", "unset", "add", "remove")}
        for name in ("set", "add", "remove"):
            if sections[name] is not None and not isinstance(sections[name], dict):
                raise DocFieldError("invalid_value", 400, name)
        if sections["unset"] is not None and not isinstance(sections["unset"], list):
            raise DocFieldError("invalid_value", 400, "unset")
        if sum(len(s or ()) for s in sections.values()) > 400:
            raise DocFieldError("fields_too_large", 422)
        seen: Dict[str, str] = {}

        def typed(raw_key: Any, path: str, section: str) -> Optional[Dict[str, Any]]:
            field = state.resolve_key(raw_key, path)
            if field is None:
                if strict:
                    raise DocFieldError("unknown_field", 422, path)
                edit["warnings"].append({"key": str(raw_key), "code": "unknown_field"})
                return None
            prior = seen.get(field["key"])
            if prior is not None and {prior, section} != {"add", "remove"}:
                raise DocFieldError("invalid_value", 400, path)
            seen[field["key"]] = section
            edit["typed_keys"] = True
            return field

        def values(field: Dict[str, Any], raw: Any, path: str) -> List[Any]:
            items = raw if isinstance(raw, list) else [raw]
            if len(items) > MAX_VALUES_PER_KEY:
                raise DocFieldError("invalid_value", 400, path)
            out = []
            for i, item in enumerate(items):
                item_path = f"{path}[{i}]" if isinstance(raw, list) else path
                value, code = state.normalize(field, item)
                if value is None:
                    raise DocFieldError("type_mismatch" if code == "type_mismatch" else "invalid_value",
                                        400, item_path)
                if code:
                    if strict:
                        raise DocFieldError(code, 422, item_path)
                    edit["warnings"].append({"key": field["key"], "code": code})
                out.append(value)
            return out

        for raw_key, raw in (sections["set"] or {}).items():
            path = f"set.{raw_key}"
            if raw_key in ("path", "ingested_at"):
                raise DocFieldError("invalid_value", 400, path)
            if raw_key == "title":
                if raw is not None and (not isinstance(raw, str) or not raw.strip() or len(raw) > 500):
                    raise DocFieldError("invalid_value", 400, path)
                edit["system"]["title"] = raw
                continue
            if raw_key == "description":
                if raw is not None and (not isinstance(raw, str) or len(raw) > 2000):
                    raise DocFieldError("invalid_value", 400, path)
                edit["system"]["description"] = raw
                continue
            field = typed(raw_key, path, "set")
            if field is None:
                continue
            if raw is None or raw == []:
                edit["set"][field["key"]] = raw
            else:
                edit["set"][field["key"]] = values(field, raw, path)
        for i, raw_key in enumerate(sections["unset"] or ()):
            path = f"unset[{i}]"
            if not isinstance(raw_key, str) or normalize_key(raw_key) in SYSTEM_KEYS:
                raise DocFieldError("invalid_value", 400, path)
            field = typed(raw_key, path, "unset")
            if field is not None:
                edit["unset"].append(field["key"])
        for name in ("add", "remove"):
            for raw_key, raw in (sections[name] or {}).items():
                path = f"{name}.{raw_key}"
                field = typed(raw_key, path, name)
                if field is None:
                    continue
                if field["cardinality"] != "many":
                    raise DocFieldError("invalid_value", 400, path)
                edit[name][field["key"]] = values(field, raw, path)
        return edit

    def _apply_edit(pointer: str, anchor: Dict[str, Any], edit: Dict[str, Any]) -> None:
        now = _now_iso()
        if "title" in edit["system"]:
            anchor["manual_title"] = edit["system"]["title"]
        if "description" in edit["system"]:
            anchor["manual_description"] = edit["system"]["description"]
        for key, vals in edit["set"].items():
            if vals is None:
                anchor["manual"].pop(key, None)          # back to the lower layers
            elif vals == []:
                anchor["manual"][key] = {"values": [], "unset": True, "created_at": now}
            else:
                anchor["manual"][key] = {"values": vals, "created_at": now}
        for key in edit["unset"]:
            anchor["manual"][key] = {"values": [], "unset": True, "created_at": now}
        for key in set(edit["add"]) | set(edit["remove"]):
            # The effective set is copied up into the manual layer.
            current = list(state.effective(pointer).get(key, ("", []))[1])
            for value in edit["add"].get(key, []):
                if value not in current:
                    current.append(value)
            removed = edit["remove"].get(key, [])
            current = [v for v in current if v not in removed]
            anchor["manual"][key] = ({"values": current, "created_at": now} if current
                                     else {"values": [], "unset": True, "created_at": now})

    @app.post("/secured/graph/doc-values/find")
    def find_doc_values() -> Any:
        if state.mode != "filters":
            raise DocFieldError("where_unsupported", 400)    # before the principal
        body = _body()
        _caller(body)
        where = body.get("where")
        if not isinstance(where, dict) or not where:
            raise DocFieldError("invalid_value", 400, "where")
        resolved = state.resolve_where(where, body.get("return_fields"))
        sort = body.get("sort")
        sort_field, order = None, "asc"
        if sort is not None:
            if not isinstance(sort, dict) or not set(sort) <= {"field", "order"}:
                raise DocFieldError("invalid_value", 400, "sort")
            order = sort.get("order", "asc")
            if order not in ("asc", "desc"):
                raise DocFieldError("invalid_value", 400, "sort.order")
            name = sort.get("field", "pointer")
            if not isinstance(name, str) or not name.strip():
                raise DocFieldError("invalid_value", 400, "sort.field")
            if name != "pointer":
                sort_field = state.resolve_where_key(name, "sort.field")
                if sort_field["datatype"] not in INTERVAL_TYPES:
                    raise DocFieldError("invalid_value", 400, "sort.field")
        limit = body.get("limit")
        if limit is None:
            limit = FIND_DEFAULT_LIMIT
        elif isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise DocFieldError("invalid_value", 400, "limit")
        limit = min(limit, FIND_MAX_LIMIT)
        token = sort_field["key"] if sort_field else "pointer"
        # The cursor binds the sort, not `where`: the client resends it.
        position = _decode_cursor(body.get("after"), token, order)

        def sort_value(pointer: str) -> Optional[str]:
            if sort_field is None:
                return None
            values = state.effective(pointer).get(sort_field["key"], ("", []))[1]
            texts = [str(v.get("lo") if isinstance(v, dict) else v) for v in values]
            if not texts:
                return None
            return min(texts) if order == "asc" else max(texts)

        rows = []
        if not resolved["empty_visible"]:
            rows = [(sort_value(p), p) for p in state.anchors if state.matches(p, resolved["clauses"])]
        present = sorted((r for r in rows if r[0] is not None), reverse=order == "desc")
        rows = present + sorted(r for r in rows if r[0] is None)          # NULLs last
        if sort_field is None:
            rows = sorted(rows, key=lambda r: r[1], reverse=order == "desc")
        overflow = state.find_scan_budget is not None and len(rows) > state.find_scan_budget
        if overflow:
            rows = rows[:state.find_scan_budget]
        start = 0
        if position is not None:
            start = next((i + 1 for i, r in enumerate(rows) if r[1] == position[1]), len(rows))
        page = rows[start:start + limit]
        more = start + limit < len(rows)
        next_after = _encode_cursor(token, order, page[-1][0], page[-1][1]) if more and page else None
        wanted = state.wanted_keys(resolved["return_keys"])
        documents = []
        for _key, pointer in page:
            entry: Dict[str, Any] = {"pointer": pointer, "document_uuid": stable_id("document", pointer),
                                     "title": state.title_of(pointer)[0]}
            if wanted is not None:
                fields = state.fields_of(pointer, wanted, node_ids=True)
                if "title" in wanted:
                    fields["title"] = entry["title"]
                entry["fields"] = dict(sorted(fields.items()))
            documents.append(entry)
        # complete is false on every page with a successor, and on the last
        # page when the scan budget ran out (next_after null, complete false).
        out: Dict[str, Any] = {"documents": documents, "next_after": next_after,
                               "complete": next_after is None and not overflow}
        if position is None:
            out["total_count"] = None if overflow else len(rows)   # first page only
        out["where"] = _echo(resolved, query=False)
        if wanted is not None:
            out["return_fields"] = {"applied": True}
        return _success("Documents found", out)

    # -- registry -----------------------------------------------------------

    def _create_field(definition: Dict[str, Any], *, origin: str) -> Dict[str, Any]:
        unknown = sorted(set(definition) - _DEFINITION_KEYS)
        if unknown:
            raise DocFieldError("invalid_field_definition", 400, unknown[0])
        key = definition.get("key")
        if not isinstance(key, str) or not KEY_RE.match(key):
            raise DocFieldError("invalid_field_definition", 400, "key")
        if looks_personal(key):
            raise DocFieldError("key_looks_personal", 422, "key")
        if key in SYSTEM_KEYS or state.field_by_key(key) is not None:
            raise DocFieldError("field_key_exists", 409, "key")
        if len(state.fields) >= MAX_FIELDS_PER_TENANT:
            raise DocFieldError("field_cap_reached", 409)
        datatype = definition.get("datatype")
        if datatype is None:
            datatype = ("date" if key.endswith(("_date", "datum"))
                        else "money" if "amount" in key or "betrag" in key else "text")
        if datatype not in DATATYPES:
            raise DocFieldError("invalid_field_definition", 400, "datatype")
        row = {
            "id": stable_id("field", key), "key": key, "datatype": datatype,
            "cardinality": definition.get("cardinality", "one"),
            "labels": definition.get("labels") or {}, "aliases": definition.get("aliases") or [],
            "enum_values": definition.get("enum_values"),
            "date_role": definition.get("date_role"), "date_order": definition.get("date_order"),
            "fy_start_month": definition.get("fy_start_month"), "fy_label": definition.get("fy_label"),
            "code_scheme": definition.get("code_scheme"),
            "target_node_type_id": definition.get("target_node_type_id"),
            "link_policy": definition.get("link_policy", "resolve"),
            "sensitivity": definition.get("sensitivity", "normal"),
            "status": definition.get("status", "active"), "origin": origin,
            "pack_key": None, "pack_version": None,
            "display": definition.get("display", False), "facet": definition.get("facet", False),
        }
        _check_definition(row)
        state.fields[row["id"]] = row
        return row

    def _check_definition(row: Dict[str, Any]) -> None:
        if row["cardinality"] not in ("one", "many"):
            raise DocFieldError("invalid_field_definition", 400, "cardinality")
        if not isinstance(row["labels"], dict) or not all(
                isinstance(k, str) and isinstance(v, str) and len(v) <= 200
                for k, v in row["labels"].items()):
            raise DocFieldError("invalid_field_definition", 400, "labels")
        if not isinstance(row["aliases"], list) or len(row["aliases"]) > 32:
            raise DocFieldError("invalid_field_definition", 400, "aliases")
        for i, alias in enumerate(row["aliases"]):
            if not isinstance(alias, str) or not alias.strip() or len(alias) > 64:
                raise DocFieldError("invalid_field_definition", 400, f"aliases[{i}]")
            if looks_personal(alias):
                raise DocFieldError("key_looks_personal", 422, f"aliases[{i}]")
        if (row["datatype"] == "enum") != bool(row["enum_values"]):
            raise DocFieldError("invalid_field_definition", 400, "enum_values")
        if row["date_role"] is not None and row["datatype"] not in INTERVAL_TYPES:
            raise DocFieldError("invalid_field_definition", 400, "date_role")
        target = row["target_node_type_id"]
        if target is not None and (row["datatype"] != "entity_ref" or target not in state.node_types):
            # A hidden type answers exactly like an unknown one.
            raise DocFieldError("invalid_field_definition", 400, "target_node_type_id")
        if row["link_policy"] not in ("resolve", "never"):
            raise DocFieldError("invalid_field_definition", 400, "link_policy")
        if row["sensitivity"] not in ("normal", "special"):
            raise DocFieldError("invalid_field_definition", 400, "sensitivity")
        if row["status"] not in ("active", "provisional"):
            raise DocFieldError("invalid_field_definition", 400, "status")
        for flag in ("display", "facet"):
            if not isinstance(row[flag], bool):
                raise DocFieldError("invalid_field_definition", 400, flag)

    def _field_or_404(field_id: str) -> Dict[str, Any]:
        try:
            fid = str(uuid.UUID(str(field_id)))
        except ValueError:
            raise _not_found("Field") from None
        row = state.fields.get(fid)
        if row is None:
            raise _not_found("Field")
        return row

    @app.get("/secured/graph/doc-fields")
    def list_doc_fields() -> Any:
        _caller(_body())
        rows = sorted(state.fields.values(), key=lambda r: r["key"])
        return _success("Document fields", {"fields": [state.public_field(r) for r in rows]})

    @app.post("/secured/graph/doc-fields")
    def create_doc_field() -> Any:
        body = _body()
        _caller(body)
        _registry_writer()
        row = _create_field(_definition(body), origin="tenant")
        return _success("Document field created", {"field": state.public_field(row)}, 201)

    @app.patch("/secured/graph/doc-fields/<field_id>")
    def update_doc_field(field_id: str) -> Any:
        body = _body()
        _caller(body)
        _registry_writer()
        row = _field_or_404(field_id)
        definition = _definition(body)
        unknown = sorted(set(definition) - _DEFINITION_KEYS)
        if unknown:
            raise DocFieldError("invalid_field_definition", 400, unknown[0])
        if "key" in definition and definition["key"] != row["key"]:
            raise DocFieldError("invalid_field_definition", 400, "key")
        retype = any(name in definition and definition[name] != row[name]
                     for name in ("datatype", "date_role", "date_order", "fy_start_month",
                                  "fy_label", "code_scheme"))
        retype = retype or (row["cardinality"] == "many" and definition.get("cardinality") == "one")
        if row["datatype"] == "enum" and "enum_values" in definition:
            old = {e["code"] if isinstance(e, dict) else e for e in row["enum_values"] or ()}
            new = {e["code"] if isinstance(e, dict) else e for e in definition["enum_values"] or ()}
            retype = retype or bool(old - new)
        if retype and row["status"] != "provisional":
            raise DocFieldError("field_type_locked", 409)
        if definition.get("status") == "provisional" and row["status"] != "provisional":
            raise DocFieldError("field_type_locked", 409)
        updated = dict(row)
        for name in PUBLIC_FIELD_KEYS:
            if name in definition and name not in _READ_ONLY_KEYS:
                updated[name] = definition[name]
        _check_definition(updated)
        state.fields[row["id"]] = updated
        return _success("Document field updated", {"field": state.public_field(updated)})

    @app.post("/secured/graph/doc-fields/<field_id>/deprecate")
    def deprecate_doc_field(field_id: str) -> Any:
        _caller(_body())
        _registry_writer()
        row = _field_or_404(field_id)
        row["status"] = "deprecated"
        return _success("Document field deprecated", {"field": state.public_field(row)})

    @app.get("/secured/graph/doc-fields/packs")
    def list_packs() -> Any:
        _caller(_body())
        installed: Dict[str, int] = {}
        for row in state.fields.values():
            if row.get("pack_key"):
                installed[row["pack_key"]] = max(installed.get(row["pack_key"], 0),
                                                 int(row.get("pack_version") or 0))
        packs = [{"key": key, "version": version, "installed": key in installed,
                  "installed_version": installed.get(key)}
                 for key, (version, _definitions) in PACKS.items()]
        return _success("Document field packs", {"packs": packs})

    @app.post("/secured/graph/doc-fields/packs/<pack>/install")
    def install_pack(pack: str) -> Any:
        _caller(_body())
        _registry_writer()
        if pack not in PACKS:
            raise DocFieldError("pack_not_found", 404)
        version, definitions = PACKS[pack]
        return _success("Document field pack installed",
                        state.install(definitions, pack, version, origin="pack"))

    @app.route("/secured/graph/doc-fields/settings", methods=["GET", "PUT"])
    def doc_field_settings() -> Any:
        body = _body()
        _caller(body)
        if request.method == "GET":
            return _success("Document field settings", dict(state.settings))
        _registry_writer()
        changes = _definition(body)
        if not changes:
            raise DocFieldError("invalid_value", 400, "settings")
        allowed = {"unknown_keys": ("ignore", "reject", "register"), "date_order": ("dmy", "mdy", "ymd")}
        for name, value in changes.items():
            if name not in allowed or value not in allowed[name]:
                raise DocFieldError("invalid_value", 400, str(name))
        state.settings.update(changes)
        return _success("Document field settings updated", dict(state.settings))

    # -- folder rules -------------------------------------------------------

    def _rule_view(rule: Dict[str, Any]) -> Dict[str, Any]:
        shown: Dict[str, Any] = {}
        for key, values in rule["set"].items():
            field = state.field_by_key(key)
            if field is None:
                continue
            vals = [state.shown(field, v, node_ids=True) for v in values]
            shown[key] = vals if field["cardinality"] == "many" else (vals[0] if vals else None)
        return {"id": rule["id"], "pointer_prefix": rule["pointer_prefix"], "set": shown,
                "version": rule["version"], "status": "live"}

    @app.route("/secured/graph/doc-field-rules", methods=["GET", "PUT", "DELETE"])
    def doc_field_rules() -> Any:
        body = _body()
        _caller(body)
        _registry_writer()                       # all three methods, GET too
        if request.method == "GET":
            return _success("Document field rules",
                            {"rules": [_rule_view(state.rules[p]) for p in sorted(state.rules)]})
        prefix = body.get("pointer_prefix")      # from the JSON body, DELETE too
        if request.method == "DELETE":
            rule = state.rules.pop(prefix, None) if isinstance(prefix, str) else None
            if rule is None:
                raise _not_found("Rule")
            return _success("Document field rule retired",
                            {"retired": True, "reapply_job_id": str(uuid4())})
        if not isinstance(prefix, str) or not prefix or len(prefix) > 2000 or "\x00" in prefix:
            raise DocFieldError("invalid_value", 400, "pointer_prefix")
        raw_set = body.get("set")
        if not isinstance(raw_set, dict) or not raw_set or len(raw_set) > MAX_UPLOAD_KEYS:
            raise DocFieldError("invalid_value", 400, "set")
        values: Dict[str, List[Any]] = {}
        warnings: List[Dict[str, Any]] = []
        for raw_key, raw in raw_set.items():
            path = f"set.{raw_key}"
            field = state.resolve_key(raw_key, path)
            if field is None:
                raise DocFieldError("unknown_field", 400, path, suggest=state.suggest(raw_key))
            items = [] if raw is None else (raw if isinstance(raw, list) else [raw])
            if len(items) > MAX_VALUES_PER_KEY:
                raise DocFieldError("invalid_value", 400, path)
            kept = []
            for i, item in enumerate(items):
                item_path = f"{path}[{i}]" if isinstance(raw, list) else path
                value, code = state.normalize(field, item)
                if value is None:
                    raise DocFieldError("invalid_value", 400, item_path)
                if code:
                    warnings.append({"key": field["key"], "path": item_path, "code": code})
                kept.append(value)
            values[field["key"]] = kept          # [] switches a shorter prefix off
        current = state.rules.get(prefix)
        rule = {"id": current["id"] if current else str(uuid4()), "pointer_prefix": prefix,
                "set": values, "version": (current["version"] + 1) if current else 1,
                "created_at": _now_iso()}
        state.rules[prefix] = rule
        out: Dict[str, Any] = {"rule": _rule_view(rule), "reapply_job_id": str(uuid4())}
        if warnings:
            out["warnings"] = warnings
        return _success("Document field rule saved", out)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
