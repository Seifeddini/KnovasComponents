"""Document fields in the search UI: registry view, entity suggestions, the
listing, and one document's values (read and edit). Spec 4.3-4.4.

What this module owns
---------------------
The JSON routes the search page calls for document fields, plus the pieces
``/api/search`` shares with them (``app.py`` keeps the search route itself):
what a search may send (``search_plan``, H1), the one retry without
``return_fields`` (``run_search``, H3), and how a refused filter reaches the
browser (``filter_refusal``).

Three rules hold for every route here:

- **A filter is honest or absent.** ``where`` goes out only when the
  capability is ``filters`` (the listing: ``filters`` or ``listing_only``),
  results are shown only when Knovas echoed ``where.applied is True``, and a
  request that carried ``where`` is never retried without it.
- **Values never leave the data path.** Every route that carries a pointer
  or a name is a POST with a JSON body, and nothing here logs a value: log
  lines carry codes, counts and exception class names. Typed name prefixes
  never reach Knovas; suggestions come from a node list fetched without
  ``q`` and are filtered here (D10).
- **The Platform narrows, Knovas decides.** A value edit needs identity, a
  role in ``web.doc_fields.edit_roles`` (``admin`` for special fields) and a
  live grant -- the person's own search or listing returned the document.
  Knovas still answers 403 ``change_not_authorized`` where it disagrees.

Pointers travel verbatim (``knovas_pointer_for``): the browser sends a row's
``doc_id``, which for search and listing rows is the Knovas pointer. A
mount-relative spelling is not guessed back into a pointer; Knovas does not
know it and the route answers 404.

This file is ASCII-only (scripts/check_ascii_py.py): umlauts are ``\\u``
escapes.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from flask import jsonify, request

import doc_fields_capability as dfc
import doc_fields_view as dfv
from doc_fields_capability import Capability
from knovas_client import DocFieldsError, DocFieldsUnavailable, QueryRejected

logger = logging.getLogger(__name__)

# Registry keys (registry.py) and the anchor keys a person may edit.
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_EDITABLE_SYSTEM_KEYS = ("title", "description")

# Server codes that name what is wrong with one field of a filter (spec 4.3).
FILTER_FIELD_CODES = frozenset({
    "unknown_field", "ambiguous_field", "invalid_value", "type_mismatch",
    "where_too_complex", "restricted_identifier", "checksum_failed",
})
# Under a forced relevance gate the server may answer this instead of the
# where preflight; either way the filter needs a calibration at Knovas.
_CALIBRATION_CODES = frozenset({"where_requires_calibration",
                                "relevance_mode_calibration_missing"})

# Knovas refusals of an edit that are audited (with the code as reason).
_REFUSAL_REASONS = frozenset({"version_conflict", "change_not_authorized",
                              "anchor_quarantined"})

# Bounds on what the browser may send. Knovas validates the meaning; these
# only keep an oversized body from leaving the Platform.
POINTER_MAX = 1000
CURSOR_MAX = 4096
ENTITY_QUERY_MAX = 200
EDIT_MAX_KEYS = 64
EDIT_MAX_VALUES = 32
EDIT_MAX_BYTES = 32 * 1024
SUGGESTIONS_MAX = 10

# Texts the routes add (the shared ones live in doc_fields_view).
PARTIAL_HINT = (
    "Filter angewendet \u2013 Knovas konnte nicht alle Dokumente pr\u00fcfen; "
    "die Trefferliste ist m\u00f6glicherweise unvollst\u00e4ndig."
)
EDIT_NEEDS_ROLE = (
    "Dokumentwerte d\u00fcrfen nur Personen mit Bearbeitungsrecht \u00e4ndern."
)
EDIT_NEEDS_ADMIN = (
    "Besonders sch\u00fctzenswerte Felder darf nur die Administration \u00e4ndern."
)
EDIT_NEEDS_IDENTITY = (
    "Dokumentwerte lassen sich nur mit pers\u00f6nlicher Anmeldung \u00e4ndern."
)
NOT_FOUND_TEXT = "Nicht gefunden oder f\u00fcr Sie nicht sichtbar."
LISTING_UNAVAILABLE = (
    "Die Dokumentliste nach Feldern ist bei Knovas nicht freigeschaltet."
)
FILTER_SHAPE_INVALID = "Der Filter ist ung\u00fcltig oder zu umfangreich."
REQUEST_INVALID = "Die Anfrage ist ung\u00fcltig."
NO_SUGGESTIONS = "F\u00fcr dieses Feld gibt es keine Vorschl\u00e4ge."
KNOVAS_UPDATE_NEEDED = dfv.error_message("invalid_value", {"path": "pointer"})


def _err(error_code: str, message: str, status: int, **extra: Any):
    body, status = _refusal(error_code, message, status, **extra)
    return jsonify(body), status


def _json_body() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Who is asking, and what the tenant supports
# ---------------------------------------------------------------------------

def current_user(identity_gate: Any) -> Any:
    """The signed-in person, or None (identity off, nobody signed in, or the
    identity store unreachable -- the routes then act as for nobody)."""
    if identity_gate is None:
        return None
    try:
        return identity_gate.current_user()
    except Exception as exc:  # noqa: BLE001 - never a 500 for a lookup
        logger.warning("Document fields: current user unavailable (%s)", type(exc).__name__)
        return None


def user_key_for(identity_gate: Any) -> Optional[str]:
    """The per-user cache key: the person's opaque id; None without identity
    (one shared login, so one shared view)."""
    user = current_user(identity_gate)
    return str(user.id) if user is not None else None


def capability_now(client: Any) -> Capability:
    """``capability_for`` that can never fail a request: anything unexpected
    is ``unknown``, which every caller treats as off (H6)."""
    try:
        return dfc.capability_for(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Document fields capability unavailable (%s)", type(exc).__name__)
        return Capability.unknown


def registry_or_none(client: Any, user_key: Any) -> Optional[List[Dict[str, Any]]]:
    """The person's sanitized registry, or None when Knovas could not give
    it. A feature-off answer is observed, so the capability follows."""
    try:
        return dfc.registry_for(client, user_key)
    except Exception as exc:  # noqa: BLE001 - the caller degrades
        dfc.observe_exception(exc)
        logger.info("Document field registry unavailable (%s %s)",
                    type(exc).__name__, getattr(exc, "error_code", "") or "")
        return None


def _by_key(registry: Optional[Iterable[Mapping[str, Any]]]) -> Dict[str, Mapping[str, Any]]:
    return {str(spec["key"]): spec for spec in registry or ()
            if isinstance(spec, Mapping) and isinstance(spec.get("key"), str)}


def editable_keys(registry: Optional[Iterable[Mapping[str, Any]]], user: Any,
                  edit_roles: Iterable[str], *, held: bool, identity_on: bool) -> List[str]:
    """The keys this person may change from the Platform (D12): ``title`` and
    ``description`` first, then every active registry key ``can_edit``
    allows. Deprecated fields no longer resolve for writes at Knovas, so they
    are never offered."""
    roles = tuple(getattr(user, "roles", None) or ()) if user is not None else ()
    out: List[str] = []
    if dfv.can_edit(roles, edit_roles, "normal", held, identity_on):
        out.extend(_EDITABLE_SYSTEM_KEYS)
    for spec in registry or ():
        if not isinstance(spec, Mapping) or spec.get("status") == "deprecated":
            continue
        key = spec.get("key")
        if isinstance(key, str) and dfv.can_edit(roles, edit_roles, spec.get("sensitivity"),
                                                 held, identity_on):
            out.append(key)
    return out


# ---------------------------------------------------------------------------
# Search: what goes out, the one retry, how a refusal reads
# ---------------------------------------------------------------------------

@dataclass
class SearchPlan:
    """What ``/api/search`` sends for document fields, decided before Knovas
    is called. ``where`` and ``return_fields`` are None when not sent."""

    capability: Capability
    where: Optional[Dict[str, Any]] = None
    return_fields: Optional[List[str]] = None
    registry: List[Dict[str, Any]] = field(default_factory=list)
    fields_unavailable: bool = False
    refusal: Optional[Tuple[Dict[str, Any], int]] = None


def _refusal(error_code: str, message: str, status: int,
             **extra: Any) -> Tuple[Dict[str, Any], int]:
    body: Dict[str, Any] = {"success": False, "error_code": error_code, "error": message}
    body.update(extra)
    return body, status


def _capability_refusal(capability: Capability) -> Tuple[Dict[str, Any], int]:
    """The 409 for a filter the capability cannot carry (spec 4.3)."""
    if capability is Capability.listing_only:
        return _refusal("filters_need_calibration",
                        dfv.error_message("filters_need_calibration"), 409,
                        capability=capability.value)
    return _refusal("filters_unavailable", dfv.error_message("filters_unavailable"), 409,
                    capability=capability.value)


def search_plan(client: Any, where: Optional[Dict[str, Any]], user_key: Any, *,
                enabled: bool = True) -> SearchPlan:
    """H1: ``where`` only under ``filters``; ``return_fields`` under
    ``filters`` and ``listing_only``, from ``card_return_fields``.

    ``enabled=False`` (the local test fixtures) treats the feature as off.
    A filter the capability cannot carry is refused here, before anything is
    sent. A registry that cannot be read leaves ``return_fields`` out: the
    search still runs (``fields_unavailable``), and a filter still goes out
    -- its meaning does not depend on the labels the registry provides.
    """
    capability = capability_now(client) if enabled else Capability.off
    plan = SearchPlan(capability=capability)
    if where is not None and not capability.shows_filters:
        plan.refusal = _capability_refusal(capability)
        return plan
    plan.where = where
    if capability.sends_return_fields:
        registry = registry_or_none(client, user_key)
        if registry is None:
            plan.fields_unavailable = True
        else:
            plan.registry = registry
            plan.return_fields = dfv.card_return_fields(registry)
    return plan


def run_search(client: Any, plan: SearchPlan, *, query: Any, limit: int,
               filters: Dict[str, Any]) -> Dict[str, Any]:
    """``client.search_documents`` with the plan's keys -- passed only when
    they are not None, so a search without them is the call it always was.

    H3: a request **without** ``where`` that Knovas refuses with a
    doc-fields code (``QueryRejected``: by construction caused by
    ``return_fields``, the only doc-fields key it carried) is retried exactly
    once without ``return_fields``; nothing was filtered, so the results are
    honest and the plan says ``fields_unavailable``. A request **with**
    ``where`` is never retried: the QueryRejected propagates.
    """
    extra: Dict[str, Any] = {}
    if plan.where is not None:
        extra["where"] = plan.where
    if plan.return_fields is not None:
        extra["return_fields"] = plan.return_fields
    try:
        return client.search_documents(query=query, limit=limit, filters=filters, **extra)
    except QueryRejected as exc:
        if plan.where is not None or plan.return_fields is None:
            raise
        dfc.observe_exception(exc)
        if exc.error_code == "unknown_field":
            # A field left the registry (or was renamed) since it was cached.
            dfc.invalidate()
        logger.info("Search: return_fields refused (%s %s); retried once without",
                    exc.status, exc.error_code)
        plan.return_fields = None
        plan.fields_unavailable = True
        return client.search_documents(query=query, limit=limit, filters=filters)


def filter_refusal(exc: BaseException,
                   registry: Optional[Iterable[Mapping[str, Any]]]) -> Tuple[Dict[str, Any], int]:
    """How a refused ``where`` (search or listing) reaches the browser.

    | Knovas                                   | Browser                          |
    | 400 where_unsupported                    | 409 filters_unavailable          |
    | 400 unknown_field, ambiguous_field, ...  | 400 filter_invalid               |
    | 503 where_requires_calibration           | 409 filters_need_calibration     |
    | 503 where_unavailable (and other 5xx)    | 503 filter_temporarily_unavailable |

    The signal each answer carries is observed (``where_unsupported`` ->
    values, calibration -> listing_only). Nothing here retries.
    """
    if isinstance(exc, DocFieldsUnavailable):
        dfc.observe("feature_off")
        return _refusal("filters_unavailable", dfv.error_message("filters_unavailable"), 409)
    status = int(getattr(exc, "status", 0) or 0)
    code = getattr(exc, "error_code", None) or ""
    details = getattr(exc, "details", None) or {}
    if code == "where_unsupported":
        dfc.observe("where_unsupported")
        return _refusal("filters_unavailable", dfv.error_message("where_unsupported"), 409)
    if code in _CALIBRATION_CODES:
        dfc.observe("needs_calibration")
        return _refusal("filters_need_calibration",
                        dfv.error_message("where_requires_calibration"), 409)
    if code == "invalid_cursor":
        return _refusal("invalid_cursor", dfv.error_message("invalid_cursor"), 400)
    if code in FILTER_FIELD_CODES or (400 <= status < 500 and status not in (401, 403, 429)):
        if code == "unknown_field":
            dfc.invalidate()
        path = details.get("path") if isinstance(details, Mapping) else None
        key = _path_key(path)
        specs = _by_key(registry)

        def labels(keys: Any) -> List[str]:
            return [dfv.field_label(specs, k) for k in keys or () if isinstance(k, str)]

        suggest = details.get("suggest") if isinstance(details, Mapping) else None
        candidates = details.get("candidates") if isinstance(details, Mapping) else None
        return _refusal(
            "filter_invalid", dfv.error_message(code, details, specs), 400,
            code=code or None,
            field=key,
            field_label=dfv.field_label(specs, key) if key else None,
            suggest_labels=labels(suggest if isinstance(suggest, list) else candidates),
        )
    if status in (401, 403):
        return _refusal("filter_not_authorized",
                        dfv.error_message(code or "assertion_rejected"), 403)
    if status == 429:
        return _refusal("too_many_requests", dfv.error_message("too_many_requests"), 429)
    return _refusal("filter_temporarily_unavailable", dfv.error_message("where_unavailable"), 503)


def _path_key(path: Any) -> Optional[str]:
    """``where.mandant`` / ``set.doc_type[0]`` -> ``mandant`` / ``doc_type``."""
    if not isinstance(path, str) or "." not in path:
        return None
    key = re.sub(r"\[\d+\]$", "", path.split(".", 1)[1])
    return key or None


def filter_not_applied() -> Tuple[Dict[str, Any], int]:
    """H2: a 2xx without ``where.applied is True``. The results are withheld
    and the capability is asked again on the next call."""
    dfc.observe("echo_missing")
    return _refusal("filter_not_applied", dfv.error_message("filter_not_applied"), 409,
                    results=[], document_fields={"filter_state": "not_applied"})


def honesty_block(meta: Any) -> Dict[str, Any]:
    """What /secured/query said about how far to trust its answer; every
    value None on a server (or a mode) that does not say it."""
    meta = meta if isinstance(meta, Mapping) else {}
    return {key: meta.get(key) for key in
            ("no_strong_matches", "no_results_reason", "relevance_gate_applied",
             "degraded_to_bm25")}


def decorate_rows(rows: Iterable[Dict[str, Any]],
                  registry: Optional[Iterable[Mapping[str, Any]]]) -> None:
    """Each result row gains ``fields_display``, ``title_from_values`` and
    ``relevance_tier`` (H4: fields only where Knovas returned them)."""
    for row in rows:
        if not isinstance(row, dict):
            continue
        fields = row.get("fields")
        row["fields_display"] = (dfv.fields_display(registry or [], fields)
                                 if isinstance(fields, Mapping) else [])
        row["title_from_values"] = row.get("title_from_values") is True
        tier = row.get("relevance_tier")
        row["relevance_tier"] = tier if isinstance(tier, str) else None


def document_fields_block(plan: SearchPlan, state: str, echo: Any,
                          capability: Optional[Capability] = None) -> Dict[str, Any]:
    """The ``document_fields`` block of a search answer (spec 4.3)."""
    out: Dict[str, Any] = {
        "capability": (capability or plan.capability).value,
        "filter_state": state,
        "fields_unavailable": bool(plan.fields_unavailable),
        "resolved": dfv.resolved_chips(plan.where, echo, plan.registry) if plan.where else [],
    }
    if state == "partial":
        out["partial_hint"] = PARTIAL_HINT
    return out


def current_capability(fallback: Capability) -> Capability:
    """What the shared cache believes now (a signal may have moved it while
    the request ran), else ``fallback``. Never probes."""
    try:
        seen = dfc.shared_cache().peek()
    except Exception:  # noqa: BLE001
        seen = None
    if fallback is Capability.off or seen is None:
        return fallback
    return seen


def doc_field_links(client: Any, user_key: Any,
                    node_type_id: Any) -> Optional[List[Dict[str, str]]]:
    """Cortex: the entity fields whose target node type is ``node_type_id``,
    as ``[{key, label}]`` -- each becomes "Dokumente mit <Feld> = <Name>",
    a listing on the search page (spec 4.5, stretch).

    None when the capability has no listing, so the Cortex answer gains no
    key at all; ``[]`` when nothing points at this type or the registry
    cannot be read. The target ids stay here: ``registry_for`` keeps them
    from the browser, and so does this.
    """
    capability = capability_now(client)
    if not capability.shows_listing:
        return None
    if not node_type_id:
        return []
    try:
        # doc_fields_capability caches the target ids next to the sanitized
        # registry (per person) but has no public reader for them yet.
        entry = dfc._registry_entry(client, user_key)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - the links are optional
        dfc.observe_exception(exc)
        logger.info("Cortex document-field links unavailable (%s)", type(exc).__name__)
        return []
    want = str(node_type_id)
    return [{"key": str(spec["key"]), "label": str(spec.get("label") or spec["key"])}
            for spec in entry.fields
            if spec.get("datatype") == "entity_ref" and spec.get("status") != "deprecated"
            and entry.targets.get(str(spec.get("key"))) == want]


# ---------------------------------------------------------------------------
# Pointers and the values view
# ---------------------------------------------------------------------------

def knovas_pointer_for(doc_id: Any) -> Optional[str]:
    """The Knovas pointer a row's ``doc_id`` stands for: the ``doc_id``
    itself, verbatim. None when it cannot be one (empty, too long, NUL).

    Search grants the mount-relative spelling as well, for the file routes;
    such a spelling is not turned back into a pointer here -- Knovas does not
    know it, and the route answers 404 rather than guess.
    """
    if not isinstance(doc_id, str):
        return None
    if not doc_id.strip() or len(doc_id) > POINTER_MAX or "\x00" in doc_id:
        return None
    return doc_id


def _edit_value(spec: Optional[Mapping[str, Any]], value: Any) -> Any:
    """A value in the form the edit form puts back: enum codes, booleans and
    entity names as such, everything else as its displayed text (Knovas
    parses ``15.03.2024``, ``GJ 2024``, ``CHF 1'234.50`` back)."""
    datatype = (spec or {}).get("datatype")
    if isinstance(value, list):
        return [_edit_value(spec, item) for item in value]
    if datatype == "enum" and isinstance(value, str):
        return value
    if datatype == "bool" and isinstance(value, bool):
        return value
    if datatype == "entity_ref" and isinstance(value, Mapping):
        if value.get("hidden") is True:
            return None
        name = value.get("name")
        return name if isinstance(name, str) else None
    return dfv.format_value(spec, value) or None


def _has_hidden(value: Any) -> bool:
    items = value if isinstance(value, list) else [value]
    return any(isinstance(item, Mapping) and item.get("hidden") is True for item in items)


def values_view(values: Mapping[str, Any], registry: Optional[List[Dict[str, Any]]],
                user: Any, edit_roles: Iterable[str], *, identity_on: bool) -> Dict[str, Any]:
    """``GET /secured/graph/doc-values`` as the panel shows it (spec 4.4).

    One row per field that has a value or a layer, in registry order: the
    text, the layer it comes from (``Manuell``, ``Upload``, ``Ordnervorgabe``,
    ``Extrahiert``), whether it was verified, and ``changed_at`` -- the
    manual layer's ``created_at`` -- when a person set it. A held document
    (``acl_mode: quarantined``) shows nothing and is read-only.
    """
    held = values.get("acl_mode") == "quarantined"
    specs = _by_key(registry)
    fields = values.get("fields") if isinstance(values.get("fields"), Mapping) else {}
    layers = values.get("layers") if isinstance(values.get("layers"), Mapping) else {}
    keys = [k for k in specs if k in fields or k in layers]
    keys += sorted(str(k) for k in set(fields) | set(layers)
                   if str(k) not in specs and str(k) not in dfv.SYSTEM_KEYS)
    rows: List[Dict[str, Any]] = []
    for key in [] if held else keys:
        spec = specs.get(key)
        entries = [e for e in layers.get(key) or () if isinstance(e, Mapping)]
        effective = next((e for e in entries if e.get("effective") is True), None)
        manual = next((e for e in entries if e.get("layer") == "manual"), None)
        value = fields.get(key)
        layer = str(effective.get("layer")) if effective and effective.get("layer") else None
        row: Dict[str, Any] = {
            "key": key,
            "label": dfv.field_label(specs, key),
            "text": dfv.format_value(spec, value),
            "edit_value": _edit_value(spec, value),
            "layer": layer,
            "layer_label": dfv.layer_label(layer) if layer else None,
            "verified": bool(effective and effective.get("verified") is True),
            "datatype": (spec or {}).get("datatype"),
            "cardinality": (spec or {}).get("cardinality", "one"),
            "sensitivity": (spec or {}).get("sensitivity", "special"),
        }
        if effective is not None and effective.get("unset") is True:
            row["unset"] = True
        if _has_hidden(value):
            # A value the person cannot see would be dropped by saving the
            # visible rest; the field stays read-only for them.
            row["has_hidden"] = True
        if manual is not None and isinstance(manual.get("created_at"), str):
            row["changed_at"] = manual["created_at"]
        if key == "privileged":
            # H9: a flag, never an access restriction.
            row["note"] = dfv.PRIVILEGED_HINT
        rows.append(row)
    warnings: List[str] = []
    for item in values.get("warnings") or ():
        code = item.get("code") if isinstance(item, Mapping) else item
        if isinstance(code, str) and code:
            warnings.append(code)
    version = values.get("version")
    allowed = editable_keys(registry, user, edit_roles, held=held, identity_on=identity_on)
    hidden = {row["key"] for row in rows if row.get("has_hidden")}
    return {
        "pointer": values.get("pointer"),
        "document_uuid": values.get("document_uuid"),
        "version": version if isinstance(version, int) and not isinstance(version, bool) else 0,
        "title": None if held else values.get("title"),
        "title_source": None if held else values.get("title_source"),
        "description": None if held else values.get("description"),
        "held": held,
        "held_text": dfv.error_message("anchor_quarantined") if held else None,
        "title_note": dfv.TITLE_NOT_SEARCHABLE,
        "fields": rows,
        "warnings": warnings,
        "editable_keys": [key for key in allowed if key not in hidden],
    }


def _edit_refusal(exc: BaseException,
                  registry: Optional[Iterable[Mapping[str, Any]]]) -> Tuple[Dict[str, Any], int]:
    """A refused read or edit, for the panel (spec 4.4 error mapping)."""
    if isinstance(exc, DocFieldsUnavailable):
        dfc.observe("feature_off")
        return _refusal("doc_fields_off", dfv.error_message("HTTP_404"), 409)
    status = int(getattr(exc, "status", 0) or 0)
    code = getattr(exc, "error_code", None) or ""
    details = getattr(exc, "details", None) or {}
    path = details.get("path") if isinstance(details, Mapping) else None
    specs = _by_key(registry)
    if code == "invalid_value" and path == "pointer":
        # A Knovas without S2 reads the pointer from the query string only.
        # There is deliberately no fallback to it (the gateway logs URLs).
        return _refusal("knovas_update_needed", KNOVAS_UPDATE_NEEDED, 502)
    if code == "version_conflict":
        return _refusal("version_conflict", dfv.error_message(code), 409,
                        current_version=details.get("current_version"))
    if code == "change_not_authorized":
        return _refusal("change_not_authorized", dfv.error_message(code), 403, read_only=True)
    if code == "anchor_quarantined":
        return _refusal("anchor_quarantined", dfv.error_message(code), 409, read_only=True)
    if status in (400, 422):
        key = _path_key(path)
        return _refusal("field_invalid", dfv.error_message(code, details, specs), 400,
                        code=code or None, field=key,
                        field_label=dfv.field_label(specs, key) if key else None)
    if status in (401, 403):
        return _refusal("not_authorized", dfv.error_message(code or "assertion_rejected"), 403)
    if status == 429:
        return _refusal("too_many_requests", dfv.error_message("too_many_requests"), 429)
    return _refusal("doc_fields_unavailable", dfv.error_message("doc_fields_unavailable"), 503)


# ---------------------------------------------------------------------------
# Edit bodies
# ---------------------------------------------------------------------------

def _scalar_ok(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        return len(value) <= 4096
    if isinstance(value, Mapping):
        # {"name": ...} for an entity; {"amount", "currency"} for money.
        return len(value) <= 4 and all(isinstance(k, str) and _scalar_ok(v) and not
                                       isinstance(v, (Mapping, list)) for k, v in value.items())
    return False


def _values_ok(value: Any) -> bool:
    if isinstance(value, list):
        return len(value) <= EDIT_MAX_VALUES and all(_scalar_ok(v) for v in value)
    return _scalar_ok(value)


def parse_edit(body: Mapping[str, Any]) -> Dict[str, Any]:
    """The ``set`` / ``unset`` / ``add`` / ``remove`` of an edit, bounded.

    Raises ValueError (naming no value) for anything that is not the
    documented shape. Keys must be registry keys, or ``title`` /
    ``description`` inside ``set``.
    """
    try:
        size = len(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise ValueError("not plain JSON") from None
    if size > EDIT_MAX_BYTES:
        raise ValueError("too large")
    out: Dict[str, Any] = {}
    for name in ("set", "add", "remove"):
        section = body.get(name)
        if section is None:
            continue
        if not isinstance(section, Mapping) or len(section) > EDIT_MAX_KEYS:
            raise ValueError(f"{name} must be an object")
        for key, value in section.items():
            system = name == "set" and key in _EDITABLE_SYSTEM_KEYS
            if not isinstance(key, str) or not (system or _KEY_RE.match(key)):
                raise ValueError(f"{name} has a key that is not a field key")
            if not system and key in dfv.SYSTEM_KEYS:
                # path / ingested_at are read-only; title and description
                # are changed through set only.
                raise ValueError(f"{name} names a system key")
            if name in ("add", "remove") and not isinstance(value, list):
                raise ValueError(f"{name} values must be lists")
            if not _values_ok(value):
                raise ValueError(f"{name} has a value of the wrong shape")
        if section:
            out[name] = dict(section)
    unset = body.get("unset")
    if unset is not None:
        if (not isinstance(unset, list) or len(unset) > EDIT_MAX_KEYS
                or not all(isinstance(k, str) and _KEY_RE.match(k)
                           and k not in dfv.SYSTEM_KEYS for k in unset)):
            raise ValueError("unset must be a list of field keys")
        if unset:
            out["unset"] = list(unset)
    if not out:
        raise ValueError("nothing to change")
    return out


def edit_keys(ops: Mapping[str, Any]) -> List[str]:
    """Every key an edit names, in a stable order."""
    keys: List[str] = []
    for name in ("set", "unset", "add", "remove"):
        section = ops.get(name) or ()
        for key in (section if isinstance(section, list) else list(section)):
            if key not in keys:
                keys.append(key)
    return keys


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------

def attach(app: Any, *, config: Any, client_factory: Callable[[], Any], identity_gate: Any,
           grant: Callable[[List[Dict[str, Any]]], Any], grant_check: Callable[[str], bool],
           enhance: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
    """Register the document-fields routes on ``app`` (spec 4.4).

    ``grant(rows)`` records what a listing handed the person, as search does;
    ``grant_check(doc_id)`` asks whether the person's own search or listing
    returned that document (``_readable_for_current_user``); ``enhance`` is
    the search path's row enrichment. Every POST here goes through the
    app-wide ``X-CSRF-Token`` gate.
    """

    def _settings() -> dfc.DocFieldsSettings:
        return dfc.settings(config)

    def _off_body(capability: Capability) -> Dict[str, Any]:
        # ``unknown`` is reported as such; the page treats it as off (H6).
        return {"capability": capability.value, "fields": []}

    def _listing_refusal(capability: Capability):
        if capability.shows_listing:
            return None
        if capability is Capability.values:
            return _err("listing_unavailable", LISTING_UNAVAILABLE, 409,
                        capability=capability.value)
        return _err("doc_fields_off", dfv.error_message("HTTP_404"), 409,
                    capability=capability.value)

    @app.route("/api/doc-fields", methods=["GET"])
    def doc_fields_registry():
        """The registry as the search page uses it, for this person."""
        client = client_factory()
        capability = capability_now(client)
        if not capability.shows_values:
            return jsonify(_off_body(capability))
        user = current_user(identity_gate)
        registry = registry_or_none(client, str(user.id) if user is not None else None)
        # A feature-off answer to the registry read moved the capability.
        capability = current_capability(capability)
        if registry is None or not capability.shows_values:
            body = _off_body(capability)
            body.update({"editable_keys": [], "registry_unavailable": registry is None})
            return jsonify(body)
        return jsonify({
            "capability": capability.value,
            "fields": registry,
            "editable_keys": editable_keys(registry, user, _settings().edit_roles, held=False,
                                           identity_on=identity_gate is not None),
            "partial_hint": PARTIAL_HINT,
            "deadline_banner": dfv.DEADLINE_BANNER,
        })

    @app.route("/api/doc-fields/entities", methods=["POST"])
    def doc_fields_entities():
        """Name suggestions for an entity field. The typed text stays here:
        names come from the node list fetched without ``q`` (D10)."""
        client = client_factory()
        capability = capability_now(client)
        if not capability.shows_values:
            return _err("doc_fields_off", dfv.error_message("HTTP_404"), 409,
                        capability=capability.value)
        body = _json_body()
        key, typed = body.get("field"), body.get("q")
        if not isinstance(key, str) or not _KEY_RE.match(key):
            return _err("request_invalid", REQUEST_INVALID, 400)
        if not isinstance(typed, str) or len(typed) > ENTITY_QUERY_MAX:
            return _err("request_invalid", REQUEST_INVALID, 400)
        user_key = user_key_for(identity_gate)
        registry = registry_or_none(client, user_key)
        if registry is None:
            return _err("doc_fields_unavailable", dfv.error_message("doc_fields_unavailable"), 503)
        spec = _by_key(registry).get(key)
        if (spec is None or spec.get("datatype") != "entity_ref" or not spec.get("has_target")
                or spec.get("sensitivity") != "normal"):
            # Special fields get no suggestions at all (free text only), and
            # a field without a target type has nothing to suggest from.
            return _err("no_suggestions", NO_SUGGESTIONS, 400)
        if len(typed.strip()) < 2:
            return jsonify({"items": []})
        names = dfc.entity_names_for(client, user_key, key)
        if names is None:
            return jsonify({"items": [], "free_text": True})
        return jsonify({"items": [{"name": name} for name in
                                  dfv.match_names(names, typed, SUGGESTIONS_MAX)]})

    @app.route("/api/documents/find", methods=["POST"])
    def documents_find():
        """One page of the listing: documents whose values match ``where``.

        Shown only when Knovas echoed ``where.applied`` (H2); the incomplete
        notice only on the last page (H5); rows are granted like search rows,
        so a listed document can be previewed.
        """
        client = client_factory()
        capability = capability_now(client)
        refused = _listing_refusal(capability)
        if refused is not None:
            return refused
        body = _json_body()
        try:
            where = dfv.validate_where(body.get("where"))
        except ValueError:
            return _err("filter_invalid", FILTER_SHAPE_INVALID, 400, code="where_invalid")
        sort = body.get("sort")
        if sort is not None:
            if (not isinstance(sort, Mapping) or not set(sort) <= {"field", "order"}
                    or not isinstance(sort.get("field", "pointer"), str)
                    or not (sort.get("field", "pointer") == "pointer"
                            or _KEY_RE.match(sort.get("field", "pointer")))
                    or sort.get("order", "asc") not in ("asc", "desc")):
                return _err("request_invalid", REQUEST_INVALID, 400, code="sort_invalid")
            sort = {"field": sort.get("field", "pointer"), "order": sort.get("order", "asc")}
        after = body.get("after")
        if after is not None and (not isinstance(after, str) or not after
                                  or len(after) > CURSOR_MAX):
            return _err("request_invalid", REQUEST_INVALID, 400, code="cursor_invalid")
        user_key = user_key_for(identity_gate)
        registry = registry_or_none(client, user_key)
        return_fields = dfv.card_return_fields(registry) if registry is not None else None
        try:
            page = client.find_doc_values(where, sort=sort, limit=_settings().find_page_size,
                                          after=after, return_fields=return_fields)
        except (DocFieldsError, DocFieldsUnavailable) as exc:
            logger.info("Listing refused by Knovas (%s %s)", getattr(exc, "status", "-"),
                        getattr(exc, "error_code", "-"))
            payload, status = filter_refusal(exc, registry)
            return jsonify(payload), status
        state = dfv.filter_state(where, page)
        if state not in ("applied", "partial"):
            payload, status = filter_not_applied()
            return jsonify(payload), status
        rows = [dfv.find_row(doc, registry or []) for doc in page.get("documents") or ()
                if isinstance(doc, Mapping) and doc.get("pointer")]
        rows = list((enhance({"results": rows}) or {}).get("results") or rows)
        decorate_rows(rows, registry)
        grant(rows)
        notice = dfv.listing_notice(page)
        next_after = page.get("next_after")
        out: Dict[str, Any] = {
            "success": True,
            "documents": rows,
            "next_after": next_after if isinstance(next_after, str) else None,
            "complete": page.get("complete") is True,
            "notice": notice,
            "document_fields": {
                "capability": capability.value,
                "filter_state": state,
                "fields_unavailable": registry is None,
                "resolved": dfv.resolved_chips(where, page.get("where"), registry or []),
            },
        }
        if notice["total_count"] is not None:
            out["total_count"] = notice["total_count"]
        sort_key = (sort or {}).get("field")
        if any(dfv.is_deadline_field(registry or [], k) for k in [sort_key, *where]):
            # H9: never a deadline control, never "complete" on its own say-so.
            out["deadline_banner"] = dfv.DEADLINE_BANNER
        logger.info("Listing page: rows=%d next=%s complete=%s", len(rows),
                    out["next_after"] is not None, out["complete"])
        return jsonify(out)

    def _target_pointer(body: Mapping[str, Any]):
        """The checked pointer, or the refusal. 404 whether the document does
        not exist or this person was never handed it (a 403 would confirm
        it exists)."""
        pointer = knovas_pointer_for(body.get("doc_id"))
        if pointer is None:
            return None, _err("request_invalid", REQUEST_INVALID, 400)
        if not grant_check(pointer):
            return None, _err("not_found", NOT_FOUND_TEXT, 404)
        return pointer, None

    def _read(client: Any, pointer: str, registry: Optional[List[Dict[str, Any]]],
              user: Any) -> Optional[Dict[str, Any]]:
        values = client.doc_values(pointer)
        if values is None:
            return None
        return values_view(values, registry, user, _settings().edit_roles,
                           identity_on=identity_gate is not None)

    @app.route("/api/document-fields/read", methods=["POST"])
    def document_fields_read():
        """A document's values, layers and what this person may change."""
        client = client_factory()
        capability = capability_now(client)
        if not capability.shows_values:
            return _err("doc_fields_off", dfv.error_message("HTTP_404"), 409,
                        capability=capability.value)
        pointer, refused = _target_pointer(_json_body())
        if refused is not None:
            return refused
        user = current_user(identity_gate)
        registry = registry_or_none(client, str(user.id) if user is not None else None)
        try:
            view = _read(client, pointer, registry, user)
        except (DocFieldsError, DocFieldsUnavailable) as exc:
            logger.info("Document values read refused by Knovas (%s %s)",
                        getattr(exc, "status", "-"), getattr(exc, "error_code", "-"))
            payload, status = _edit_refusal(exc, registry)
            return jsonify(payload), status
        if view is None:
            return _err("not_found", NOT_FOUND_TEXT, 404)
        if registry is None:
            view["registry_unavailable"] = True
        return jsonify({"success": True, **view})

    @app.route("/api/document-fields/edit", methods=["POST"])
    def document_fields_edit():
        """Change a document's values: manual layer, non-strict, sent once.

        Identity, a role in ``edit_roles`` (``admin`` for special fields) and
        a live grant are checked before anything goes to Knovas; Knovas
        still decides. The answer is a fresh read (a PATCH that names no
        typed key returns ``fields: {}``) plus the warnings per key.
        """
        client = client_factory()
        capability = capability_now(client)
        if not capability.shows_values:
            return _err("doc_fields_off", dfv.error_message("HTTP_404"), 409,
                        capability=capability.value)
        user = current_user(identity_gate)
        if identity_gate is None or user is None:
            return _err("edit_not_allowed", EDIT_NEEDS_IDENTITY, 403)
        settings = _settings()
        roles = tuple(getattr(user, "roles", None) or ())
        if not dfv.can_edit(roles, settings.edit_roles, "normal", False, True):
            return _err("edit_not_allowed", EDIT_NEEDS_ROLE, 403)
        body = _json_body()
        if_version = body.get("if_version")
        if isinstance(if_version, bool) or not isinstance(if_version, int) or if_version < 0:
            return _err("request_invalid", REQUEST_INVALID, 400, code="if_version")
        try:
            ops = parse_edit(body)
        except ValueError:
            return _err("request_invalid", REQUEST_INVALID, 400, code="edit_invalid")
        pointer, refused = _target_pointer(body)
        if refused is not None:
            return refused
        registry = registry_or_none(client, str(user.id))
        if registry is None:
            return _err("doc_fields_unavailable", dfv.error_message("doc_fields_unavailable"), 503)
        specs = _by_key(registry)
        keys = edit_keys(ops)
        for key in keys:
            if key in _EDITABLE_SYSTEM_KEYS:
                continue
            spec = specs.get(key)
            if spec is None or spec.get("status") == "deprecated":
                message = dfv.error_message("unknown_field", {"path": f"set.{key}"}, specs)
                return _err("field_invalid", message, 400,
                            code="unknown_field", field=key, field_label=key)
            if not dfv.can_edit(roles, settings.edit_roles, spec.get("sensitivity"), False, True):
                return _err("edit_not_allowed", EDIT_NEEDS_ADMIN, 403, field=key)
        typed = [k for k in keys if k not in _EDITABLE_SYSTEM_KEYS]
        detail: Dict[str, Any] = {
            "keys": sorted(typed),
            "ops": {name: len(ops.get(name) or ()) for name in ("set", "unset", "add", "remove")},
            "title_changed": "title" in (ops.get("set") or {}),
            "description_changed": "description" in (ops.get("set") or {}),
            "version_from": if_version,
        }
        try:
            result = client.patch_doc_values(
                pointer, if_version, set=ops.get("set"), unset=ops.get("unset"),
                add=ops.get("add"), remove=ops.get("remove"), fields_strict=False,
                actor_ref=f"platform-user:{user.id}")
        except (DocFieldsError, DocFieldsUnavailable) as exc:
            logger.info("Document values edit refused by Knovas (%s %s)",
                        getattr(exc, "status", "-"), getattr(exc, "error_code", "-"))
            payload, status = _edit_refusal(exc, registry)
            code = getattr(exc, "error_code", None)
            if code in _REFUSAL_REASONS:
                # Refused by Knovas: the panel gets the current values, and the
                # audit says why. audit_log.outcome allows ok/denied/error
                # only, so a conflict is "denied" with its reason.
                fresh = _fresh_read(client, pointer, registry, user)
                if fresh is not None:
                    payload["document"] = fresh
                _audit(user, fresh, "denied", dict(detail, reason=code))
            return jsonify(payload), status
        if result is None:
            return _err("not_found", NOT_FOUND_TEXT, 404)
        warnings = []
        for item in result.get("warnings") or ():
            if not isinstance(item, Mapping) or not isinstance(item.get("code"), str):
                continue
            key = item.get("key") if isinstance(item.get("key"), str) else None
            warnings.append({"key": key, "label": dfv.field_label(specs, key) if key else None,
                             "code": item["code"], "text": dfv.warning_text(item["code"])})
        version_to = result.get("version")
        detail["version_to"] = (version_to if isinstance(version_to, int)
                                and not isinstance(version_to, bool) else None)
        detail["warning_codes"] = sorted({w["code"] for w in warnings})
        fresh = _fresh_read(client, pointer, registry, user)
        _audit(user, fresh, "ok", detail)
        logger.info("Document values edited: keys=%d warnings=%d", len(keys), len(warnings))
        return jsonify({"success": True, "document": fresh, "warnings": warnings})

    def _fresh_read(client: Any, pointer: str, registry: Optional[List[Dict[str, Any]]],
                    user: Any) -> Optional[Dict[str, Any]]:
        """Read again after an edit; a failure leaves the panel to reload."""
        try:
            return _read(client, pointer, registry, user)
        except Exception as exc:  # noqa: BLE001 - the edit's answer stands
            logger.info("Document values re-read failed (%s)", type(exc).__name__)
            return None

    def _audit(user: Any, view: Optional[Mapping[str, Any]], outcome: str,
               detail: Dict[str, Any]) -> None:
        """``document.values_edited``: target is the ``document_uuid``, never
        the pointer; the detail holds keys, counts and versions, never a
        value."""
        if identity_gate is None:
            return
        from identity import audit

        target = (view or {}).get("document_uuid")
        try:
            conn = identity_gate.connection()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Audit unavailable for document.values_edited (%s)",
                           type(exc).__name__)
            return
        audit.record(conn, action="document.values_edited", actor=user,
                     target_type="document", target_id=str(target) if target else None,
                     outcome=outcome, detail=detail)
