"""The Dokumentfelder tab: field registry, packs, settings and folder rules.

Document fields are typed values per document (Knovas P1a/P1b). This tab is
where an administrator shapes them for the whole tenant:

- the **registry** (which keys exist, their type, labels, aliases, and
  whether they may show on a card or serve as a filter);
- **packs** (``core``, ``legal_ch``) that install a set of fields at once;
- **settings** (what an upload with an unknown key does; how ``03/04/2024``
  is read);
- **folder rules**: default values for every document under a pointer
  prefix. They live in the ``rule`` layer and re-apply in PostgreSQL without
  a re-upload (spec D3).

Who may write
-------------
The console gate is ``require_admin`` on every route, and every POST checks
the form's ``csrf_token`` before it does anything (admin routes are exempt
from the header gate, app.py). Knovas decides the rest: registry, pack,
settings and rule writes -- and the rule *listing* -- need the tenant-admin
group or full clearance (S8, ``registry_write_requires_full_clearance``).
The listing is the same check as the writes, so the page uses its answer to
predict the writes: a 403 there turns the forms read-only with an
explanation instead of letting the person fill in a form Knovas will refuse.

What never leaves the data path
-------------------------------
No field value, rule value, pointer prefix or entity name is logged or
written into an audit row. Audit details carry keys, ids, counts, versions
and setting codes (spec 4.6); a rule's audit target is the rule id, never the
prefix. Log lines carry codes only.

The tab exists only while the capability is at least ``values``; otherwise
the page says "bei Knovas nicht freigeschaltet" and offers nothing (H6).

This file is ASCII-only (scripts/check_ascii_py.py): umlauts are ``\\u``
escapes.

Spec: rc_platform_spec.md 4.6, 4.7 (drawer helpers), 6.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from flask import render_template, request

import doc_fields_capability as dfc
from doc_fields_capability import Capability
from doc_fields_view import (
    PRIVILEGED_HINT,
    SYSTEM_KEYS,
    TITLE_NOT_SEARCHABLE,
    can_edit,
    error_message,
    field_label,
    format_value,
    layer_label,
    profile_field_keys,
    requeue_audit,
    sanitize_registry,
    warning_text,
)
from identity import audit
from identity.rc_pointers import (
    FolderOutsideSources,
    normalize_pointer_prefix,
    prefix_depth,
    prefix_for_folder,
)
from knovas_client import DocFieldsError, DocFieldsUnavailable
from remote_controller_client import requeue_supported

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vocabulary (mirrors KnowledgeBase doc_fields/registry.py)
# ---------------------------------------------------------------------------

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ENUM_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
DATATYPES = ("text", "enum", "date", "period", "money", "number", "code", "bool", "entity_ref")
CARDINALITIES = ("one", "many")
DATE_ROLES = ("document", "period", "event", "due", "received")
SENSITIVITIES = ("normal", "special")
UNKNOWN_KEY_MODES = ("ignore", "reject", "register")
DATE_ORDERS = ("dmy", "mdy", "ymd")
LABEL_LANGS = ("de", "fr", "it", "en")

#: Rows of the folder-rule value form. A rule may name up to 64 keys at
#: Knovas; a handful per save is what a person fills in, and saving the same
#: prefix again adds to nothing -- it replaces the rule, so the form starts
#: from the rule's current values when one is edited.
RULE_ROWS = 6
RULE_MAX_KEYS = 64
VALUES_PER_KEY_MAX = 32

#: Attributes whose change cannot alter how an upload is taken. Any other
#: change to a field the ingestion profile writes needs a confirmation.
_TYPE_NEUTRAL = frozenset({"labels", "aliases", "display", "facet"})

DATATYPE_LABELS = {
    "text": "Text",
    "enum": "Auswahl",
    "date": "Datum",
    "period": "Zeitraum",
    "money": "Betrag",
    "number": "Zahl",
    "code": "Kennung",
    "bool": "Ja/Nein",
    "entity_ref": "Eintrag (Wissensgraph)",
}
CARDINALITY_LABELS = {"one": "ein Wert", "many": "mehrere Werte"}
STATUS_LABELS = {"active": "aktiv", "provisional": "vorl\u00e4ufig", "deprecated": "stillgelegt"}
ORIGIN_LABELS = {"system": "System", "pack": "Paket", "tenant": "eigenes", "auto": "automatisch"}
DATE_ROLE_LABELS = {
    "document": "Dokumentdatum",
    "period": "Zeitraum",
    "event": "Ereignis",
    "due": "Frist",
    "received": "Eingang",
}
UNKNOWN_KEY_LABELS = {
    "ignore": "ignorieren (Wert verwerfen, Dokument einlesen)",
    "reject": "ablehnen (Upload mit unbekanntem Schl\u00fcssel abweisen)",
    "register": "als vorl\u00e4ufiges Feld anlegen",
}
DATE_ORDER_LABELS = {
    "dmy": "Tag/Monat/Jahr (03/04/2024 = 3. April)",
    "mdy": "Monat/Tag/Jahr (03/04/2024 = 4. M\u00e4rz)",
    "ymd": "Jahr/Monat/Tag",
}
#: Knovas returns pack keys and versions only (contract 4.8, gap 7); the
#: console names them itself.
PACK_LABELS = {
    "core": ("Grundfelder", "Dokumentart, Datum, Zeitraum, Sprache, Autor, Partei, Referenz, Betrag, Status, Stichw\u00f6rter."),
    "legal_ch": ("Kanzlei (Schweiz)", "Klient, Mandat, Gericht, Gegenpartei, Gesch\u00e4ftsnummer, Dokumentklasse, Fristen, Rechtsgebiet, Anwaltsgeheimnis."),
}
_FIELD_WARNINGS = {
    "target_type_hidden": "Ziel-Typ f\u00fcr Sie nicht sichtbar",
}

# ---------------------------------------------------------------------------
# Texts (pinned by tests)
# ---------------------------------------------------------------------------

OFF_TEXT = "Dokumentfelder sind bei Knovas nicht freigeschaltet."
OFF_HINT = (
    "Knovas hat Dokumentfelder f\u00fcr diesen Mandanten ausgeschaltet, oder der "
    "Knovas-Server kennt sie noch nicht. Suche und Ingestion laufen wie bisher."
)
# The capability probe did not answer clearly (401/403/429/5xx, a network
# error, an answer without its echo): nothing is shown and nothing written,
# but nothing is claimed about Knovas's configuration either.
UNKNOWN_TEXT = ("Ob Knovas Dokumentfelder f\u00fcr diesen Mandanten anbietet, ist derzeit "
                "nicht feststellbar.")
UNKNOWN_HINT = (
    "Knovas hat auf die Abfrage nicht eindeutig geantwortet. Bitte sp\u00e4ter erneut "
    "versuchen; bis dahin bleiben Feldverzeichnis und \u00c4nderungen ausgeblendet."
)
READ_ONLY_TEXT = (
    "Nur Mitglieder der Knovas-Administratorgruppe (oder Personen, die jedes "
    "Dokument des Mandanten sehen) d\u00fcrfen Felder, Pakete, Einstellungen und "
    "Ordnervorgaben \u00e4ndern. Die Formulare sind deshalb gesperrt."
)
RULES_FORBIDDEN_TEXT = (
    "Ordnervorgaben sind nur f\u00fcr Mitglieder der Knovas-Administratorgruppe "
    "sichtbar: eine Vorgabe beschreibt Dokumente, die nicht jede Person sehen darf."
)
RULES_UNAVAILABLE_TEXT = "Die Ordnervorgaben sind derzeit nicht abrufbar."
IN_USE_DEPRECATE = (
    "wird von der Ingestion-Konfiguration verwendet; Uploads mit diesem "
    "Schl\u00fcssel werden danach nicht mehr \u00fcbernommen"
)
IN_USE_UPDATE = (
    "wird von der Ingestion-Konfiguration verwendet; diese \u00c4nderung kann "
    "Uploads mit diesem Schl\u00fcssel betreffen"
)
CONFIRM_REQUIRED = "Bitte die Best\u00e4tigung ankreuzen. Es wurde nichts ge\u00e4ndert."
REAPPLY_TEXT = "wird angewendet (meist Minuten)"
NO_LIVE_RULE = "keine aktive Vorgabe"
MULTI_SOURCE_CONFIRM = "gilt in allen Quellen mit diesem Unterordner"
REQUEUE_OFFER = "Abgelehnte Uploads erneut senden"
REQUEUE_UNSUPPORTED = (
    "Der Knovas Connector kennt das erneute Senden noch nicht \u2013 bitte "
    "den Knovas Connector aktualisieren."
)
REQUEUE_UNREACHABLE = (
    "Der Knovas Connector ist nicht erreichbar; es wurde nichts erneut gesendet. "
    "Bitte sp\u00e4ter erneut versuchen."
)
UNSET_TEXT = "aufgehoben"
HELD_TEXT = (
    "Werte zur\u00fcckgehalten: die Zugriffseinstellungen dieses Dokuments stimmen "
    "noch nicht \u00fcberein. Sie werden angezeigt und bearbeitbar, sobald Knovas "
    "sie wieder freigibt."
)
LOCKED_ENTITY_HINT = "enth\u00e4lt Eintr\u00e4ge, die Sie nicht sehen \u2013 hier nicht \u00e4nderbar"
EXPIRED_FORM = "Formular ist abgelaufen. Bitte erneut versuchen."


class FormError(ValueError):
    """Input the console refuses before calling Knovas. The message is shown
    to the person; it names fields and rows, never a value."""


# ---------------------------------------------------------------------------
# The capability, lazily, for templates
# ---------------------------------------------------------------------------

class LazyCapability:
    """The tenant's document-fields capability, resolved on first use.

    Injected into every console template by the blueprint's context
    processor (admin.py). Nothing is asked of Knovas until a template reads
    it -- the tab strip reads it only for an administrator -- and it is
    asked at most once per render. Any failure reads as ``off``: a console
    page must render even when Knovas does not answer.
    """

    def __init__(self, client_factory: Callable[[], Any]) -> None:
        self._factory = client_factory
        self._value: Optional[Capability] = None

    def get(self) -> Capability:
        if self._value is None:
            try:
                self._value = dfc.capability_for(self._factory())
            except Exception as exc:  # noqa: BLE001 - the page must render
                logger.warning("Document fields capability unavailable: %s", type(exc).__name__)
                self._value = Capability.off
        return self._value

    @property
    def value(self) -> str:
        return self.get().value

    @property
    def shows_values(self) -> bool:
        return self.get().shows_values

    @property
    def shows_listing(self) -> bool:
        return self.get().shows_listing

    @property
    def shows_filters(self) -> bool:
        return self.get().shows_filters

    def __bool__(self) -> bool:
        return self.shows_values

    def __str__(self) -> str:
        return self.value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LazyCapability):
            other = other.value
        return self.value == getattr(other, "value", other)

    def __hash__(self) -> int:
        return id(self)


# ---------------------------------------------------------------------------
# Pure helpers: forms -> request bodies
# ---------------------------------------------------------------------------

def _text(form: Mapping[str, Any], name: str) -> str:
    return str(form.get(name, "") or "").strip()


def _folder_text(form: Mapping[str, Any]) -> str:
    """The picked folder exactly as the tree sent it. Never stripped: a
    folder name may end in a space (Linux, Samba, NAS shares), and
    RemoteController keeps it in every pointer; a stripped path would make
    the rule miss that folder and hit a sibling without the space. Only an
    all-blank value counts as no folder."""
    raw = str(form.get("folder_path", "") or "")
    return raw if raw.strip() else ""


def _checked(form: Mapping[str, Any], name: str) -> bool:
    return str(form.get(name, "") or "").strip().lower() in ("1", "on", "true", "yes", "ja")


def split_list(text: Any, sep: str = ",") -> List[str]:
    """``"a, b,, a"`` -> ``["a", "b"]``: trimmed, blanks and repeats dropped."""
    out: List[str] = []
    for part in str(text or "").split(sep):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def parse_enum_lines(text: Any) -> List[Tuple[str, str]]:
    """``code = Bezeichnung`` per line -> ``[(code, label)]``.

    A line without ``=`` is a bare code. Codes follow the server's pattern;
    a bad or repeated code is a FormError naming the line, not the input.
    """
    out: List[Tuple[str, str]] = []
    seen: set = set()
    for number, line in enumerate(str(text or "").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        code, _, label = line.partition("=")
        code, label = code.strip(), label.strip()
        if not ENUM_CODE_RE.match(code):
            raise FormError(
                f"Auswahlwerte, Zeile {number}: ein Code besteht aus Buchstaben, "
                "Ziffern, Punkt, Bindestrich und _ (h\u00f6chstens 64 Zeichen).")
        if code in seen:
            raise FormError(f"Auswahlwerte, Zeile {number}: dieser Code kommt zweimal vor.")
        seen.add(code)
        out.append((code, label))
    return out


def _shown_enum_label(labels: Any) -> str:
    """The label the textarea shows for one code: ``de``, else the first
    other language (so the code is recognisable), else nothing."""
    labels = labels if isinstance(labels, Mapping) else {}
    if labels.get("de"):
        return str(labels["de"])
    return str(next((labels.get(x) for x in LABEL_LANGS if labels.get(x)), ""))


def enum_lines(enum_values: Any) -> str:
    """The textarea form of ``enum_values``: ``code = Bezeichnung (de)``."""
    lines: List[str] = []
    for item in enum_values or ():
        if isinstance(item, str):
            lines.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("code"), str):
            label = _shown_enum_label(item.get("labels"))
            lines.append(f"{item['code']} = {label}" if label else item["code"])
    return "\n".join(lines)


def _enum_entries(enum_values: Any) -> List[Any]:
    """``enum_values`` reduced to what a write may carry (code, labels,
    aliases), so it compares equal to what the form produces."""
    out: List[Any] = []
    for item in enum_values or ():
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("code"), str):
            entry = {"code": item["code"]}
            if isinstance(item.get("labels"), Mapping) and item["labels"]:
                entry["labels"] = dict(item["labels"])
            if isinstance(item.get("aliases"), list) and item["aliases"]:
                entry["aliases"] = list(item["aliases"])
            out.append(entry)
    return out


def merge_enum(current: Any, items: Sequence[Tuple[str, str]]) -> List[Any]:
    """The new ``enum_values`` from the textarea, keeping what it cannot show.

    The textarea carries one German label per code. A code that already had
    labels in other languages or aliases keeps them; only its ``de`` label
    changes (an empty label keeps the old ones). Dropping them would be a
    silent loss in a form that never displayed them.
    """
    by_code: Dict[str, Any] = {}
    for item in _enum_entries(current):
        by_code[item if isinstance(item, str) else item["code"]] = item
    out: List[Any] = []
    for code, label in items:
        old = by_code.get(code)
        if isinstance(old, dict):
            entry = dict(old)
            labels = dict(entry.get("labels") or {})
            # A code without a German label shows another language's; that
            # line sent back unchanged is no German label (and no change).
            fallback = not labels.get("de") and label == _shown_enum_label(labels)
            if label and not fallback:
                entry["labels"] = {**labels, "de": label}
            out.append(entry)
        elif label:
            out.append({"code": code, "labels": {"de": label}})
        else:
            out.append(code)
    return out


def labels_from_form(form: Mapping[str, Any], current: Optional[Mapping[str, Any]] = None) -> Dict[str, str]:
    """Labels DE/FR/IT/EN from ``label_<lang>``; other languages of
    ``current`` are kept. A blank input removes that language."""
    labels = dict(current or {})
    for lang in LABEL_LANGS:
        name = f"label_{lang}"
        if name not in form:
            continue
        text = _text(form, name)
        if text:
            labels[lang] = text
        else:
            labels.pop(lang, None)
    return labels


def definition_from_form(form: Mapping[str, Any]) -> Dict[str, Any]:
    """The ``POST /doc-fields`` body for the "Neues Feld" form.

    Checks only what the console can say better than a server path: the key
    pattern, the system keys and the choices of the selects. Knovas checks
    the rest (personal-looking keys and labels, the field cap, cross-column
    rules) and its answer is shown in German.
    """
    key = _text(form, "key")
    if not KEY_RE.match(key):
        raise FormError(
            "Der Schl\u00fcssel besteht aus Kleinbuchstaben, Ziffern und _ und beginnt "
            "mit einem Buchstaben (h\u00f6chstens 64 Zeichen).")
    if key in SYSTEM_KEYS or key == "pointer":
        raise FormError("Dieser Schl\u00fcssel ist ein Systemfeld von Knovas.")
    datatype = _text(form, "datatype")
    if datatype not in DATATYPES:
        raise FormError("Bitte einen Feldtyp w\u00e4hlen.")
    cardinality = _text(form, "cardinality") or "one"
    if cardinality not in CARDINALITIES:
        raise FormError("Bitte w\u00e4hlen, ob das Feld einen oder mehrere Werte hat.")
    sensitivity = _text(form, "sensitivity") or "normal"
    if sensitivity not in SENSITIVITIES:
        raise FormError("Unbekannte Schutzstufe.")
    defn: Dict[str, Any] = {"key": key, "datatype": datatype, "cardinality": cardinality}
    labels = labels_from_form(form)
    if labels:
        defn["labels"] = labels
    aliases = split_list(form.get("aliases"))
    if aliases:
        defn["aliases"] = aliases
    if datatype == "enum":
        items = parse_enum_lines(form.get("enum_values"))
        if not items:
            raise FormError("Ein Auswahlfeld braucht mindestens einen Code.")
        defn["enum_values"] = merge_enum(None, items)
    if datatype == "entity_ref":
        target = _text(form, "target_node_type_id")
        if target:
            defn["target_node_type_id"] = target
    if datatype in ("date", "period"):
        role = _text(form, "date_role")
        if role:
            if role not in DATE_ROLES:
                raise FormError("Unbekannte Datumsrolle.")
            defn["date_role"] = role
    defn["display"] = _checked(form, "display")
    defn["facet"] = _checked(form, "facet")
    defn["sensitivity"] = sensitivity
    return defn


def changes_from_form(form: Mapping[str, Any], current: Mapping[str, Any]) -> Dict[str, Any]:
    """The ``PATCH /doc-fields/<id>`` body: only what the form changed.

    Labels and enum values are merged with what the form cannot show (see
    ``merge_enum``). The display/facet checkboxes are read only when the
    form carries its ``flags`` marker, so a form without them is not read
    as "switch both off". A hidden entity target is never sent back.
    """
    changes: Dict[str, Any] = {}
    cur_labels = dict(current.get("labels") or {}) if isinstance(current.get("labels"), Mapping) else {}
    labels = labels_from_form(form, cur_labels)
    if labels != cur_labels:
        changes["labels"] = labels
    if "aliases" in form:
        aliases = split_list(form.get("aliases"))
        if aliases != list(current.get("aliases") or []):
            changes["aliases"] = aliases
    if "flags" in form:
        for flag in ("display", "facet"):
            value = _checked(form, flag)
            if value != (current.get(flag) is True):
                changes[flag] = value
    sensitivity = _text(form, "sensitivity")
    if sensitivity:
        if sensitivity not in SENSITIVITIES:
            raise FormError("Unbekannte Schutzstufe.")
        if sensitivity != (current.get("sensitivity") or "normal"):
            changes["sensitivity"] = sensitivity
    datatype = current.get("datatype")
    if datatype == "enum" and "enum_values" in form:
        items = parse_enum_lines(form.get("enum_values"))
        if not items:
            raise FormError("Ein Auswahlfeld braucht mindestens einen Code.")
        merged = merge_enum(current.get("enum_values"), items)
        if merged != _enum_entries(current.get("enum_values")):
            changes["enum_values"] = merged
    hidden = "target_type_hidden" in (current.get("warnings") or ())
    if datatype == "entity_ref" and "target_node_type_id" in form and not hidden:
        target = _text(form, "target_node_type_id") or None
        if target != (current.get("target_node_type_id") or None):
            changes["target_node_type_id"] = target
    if datatype in ("date", "period") and "date_role" in form:
        role = _text(form, "date_role") or None
        if role is not None and role not in DATE_ROLES:
            raise FormError("Unbekannte Datumsrolle.")
        if role != (current.get("date_role") or None):
            changes["date_role"] = role
    return changes


def needs_use_confirmation(changes: Mapping[str, Any]) -> bool:
    """Whether a change to a field the profile writes needs the checkbox:
    anything beyond labels, aliases and the display/facet hints."""
    return bool(set(changes) - _TYPE_NEUTRAL)


def typed_value(spec: Mapping[str, Any], text: str) -> Any:
    """A folder-rule value from a text input, typed by the registry.

    Entity values are names (D4: no node ids from the console); a many-field
    takes ``;``-separated values; bool takes ja/nein. Everything else goes
    as the text the person typed -- Knovas normalises dates, amounts and
    enum labels and answers with the field's path when it cannot.
    """
    many = spec.get("cardinality") == "many"
    parts = [p.strip() for p in text.split(";") if p.strip()] if many else [text.strip()]
    if len(parts) > VALUES_PER_KEY_MAX:
        raise FormError(f"{spec.get('label') or spec.get('key')}: h\u00f6chstens "
                        f"{VALUES_PER_KEY_MAX} Werte.")
    if spec.get("datatype") == "bool":
        out = []
        for part in parts:
            word = part.lower()
            if word in ("true", "ja", "1", "yes"):
                out.append(True)
            elif word in ("false", "nein", "0", "no"):
                out.append(False)
            else:
                raise FormError(f"{spec.get('label') or spec.get('key')}: bitte Ja oder Nein.")
        parts = out
    return parts if many else parts[0]


def rule_values_from_form(form: Mapping[str, Any], registry: Sequence[Mapping[str, Any]],
                          rows: int = RULE_ROWS) -> Dict[str, Any]:
    """``{key: value | [values] | None}`` from the rule form's rows.

    ``rule_key_<i>`` names a registry field, ``rule_value_<i>`` its value,
    ``rule_clear_<i>`` sends ``null`` -- which switches a shorter prefix's
    default off for this folder (contract 4.11).
    """
    specs = {str(s.get("key")): s for s in registry or () if isinstance(s, Mapping)}
    values: Dict[str, Any] = {}
    for i in range(rows):
        key = _text(form, f"rule_key_{i}")
        if not key:
            continue
        spec = specs.get(key)
        if spec is None or spec.get("status") == "deprecated":
            raise FormError(f"Zeile {i + 1}: dieses Feld gibt es nicht (mehr).")
        if key in values:
            raise FormError(f"\u201e{spec.get('label') or key}\u201c ist zweimal angegeben.")
        if _checked(form, f"rule_clear_{i}"):
            values[key] = None
            continue
        text = _text(form, f"rule_value_{i}")
        if not text:
            raise FormError(f"Zeile {i + 1}: bitte einen Wert eingeben oder \u201eaufheben\u201c w\u00e4hlen.")
        values[key] = typed_value(spec, text)
    if not values:
        raise FormError("Bitte mindestens ein Feld mit einem Wert angeben.")
    if len(values) > RULE_MAX_KEYS:
        raise FormError(f"H\u00f6chstens {RULE_MAX_KEYS} Felder pro Ordnervorgabe.")
    return values


# ---------------------------------------------------------------------------
# Pure helpers: Knovas answers -> page rows
# ---------------------------------------------------------------------------

def _str_list(value: Any) -> List[str]:
    return [str(v) for v in value or () if isinstance(v, str)] if isinstance(value, (list, tuple)) else []


def registry_rows(raw_fields: Any, node_types: Iterable[Mapping[str, Any]] = (),
                  in_use: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """The registry table: one row per field as Knovas listed it.

    Carries labels in DE/FR/IT/EN, aliases, status, origin and the field's
    warnings in words, plus ``in_use`` when the ingestion profile writes the
    key. Entity targets are shown by node-type name; a target the person
    cannot see stays "verborgen" and is never sent back (``changes_from_form``).
    """
    names = {str(t.get("id")): str(t.get("name") or "") for t in node_types or ()
             if isinstance(t, Mapping) and t.get("id")}
    used = set(in_use or ())
    rows: List[Dict[str, Any]] = []
    for raw in raw_fields or ():
        if not isinstance(raw, Mapping) or not isinstance(raw.get("key"), str):
            continue
        key = raw["key"]
        labels = raw.get("labels") if isinstance(raw.get("labels"), Mapping) else {}
        warnings = _str_list(raw.get("warnings"))
        target = raw.get("target_node_type_id")
        hidden = "target_type_hidden" in warnings
        status = str(raw.get("status") or "active")
        datatype = str(raw.get("datatype") or "")
        origin = str(raw.get("origin") or "")
        date_role = raw.get("date_role") if isinstance(raw.get("date_role"), str) else None
        aliases = _str_list(raw.get("aliases"))
        rows.append({
            "id": str(raw.get("id") or ""),
            "key": key,
            "label": next((str(labels[x]) for x in LABEL_LANGS if labels.get(x)), key),
            "labels": {lang: str(labels.get(lang) or "") for lang in LABEL_LANGS},
            "aliases": aliases,
            "aliases_text": ", ".join(aliases),
            "datatype": datatype,
            "datatype_label": DATATYPE_LABELS.get(datatype, datatype),
            "cardinality": "many" if raw.get("cardinality") == "many" else "one",
            "cardinality_label": CARDINALITY_LABELS["many" if raw.get("cardinality") == "many" else "one"],
            "status": status,
            "status_label": STATUS_LABELS.get(status, status),
            "origin": origin,
            "origin_label": ORIGIN_LABELS.get(origin, origin),
            "pack": str(raw.get("pack_key") or ""),
            "display": raw.get("display") is True,
            "facet": raw.get("facet") is True,
            "sensitivity": "normal" if raw.get("sensitivity") in (None, "normal") else "special",
            "enum_text": enum_lines(raw.get("enum_values")),
            "target_id": str(target or ""),
            "target_name": "verborgen" if hidden else names.get(str(target or ""), ""),
            "target_hidden": hidden,
            "date_role": date_role or "",
            "date_role_label": DATE_ROLE_LABELS.get(date_role or "", ""),
            "warnings": [_FIELD_WARNINGS.get(w, w) for w in warnings],
            "in_use": key in used,
            "deprecated": status == "deprecated",
        })
    return rows


def refill_field_row(rows: List[Dict[str, Any]], field_id: Any, form: Mapping[str, Any]) -> None:
    """Show what the person posted for ``field_id`` in its edit form again,
    opened, after a 409 asking to confirm: the confirmation must not throw
    the edit away. Only inputs the form carries; invalid choices keep the
    stored value."""
    row = next((r for r in rows if r.get("id") and r.get("id") == str(field_id or "")), None)
    if row is None:
        return
    row["open"] = True
    labels = dict(row.get("labels") or {})
    for lang in LABEL_LANGS:
        if f"label_{lang}" in form:
            labels[lang] = _text(form, f"label_{lang}")
    row["labels"] = labels
    if "aliases" in form:
        row["aliases_text"] = _text(form, "aliases")
    if "enum_values" in form:
        row["enum_text"] = str(form.get("enum_values") or "")
    if "target_node_type_id" in form and not row.get("target_hidden"):
        row["target_id"] = _text(form, "target_node_type_id")
    if "date_role" in form and _text(form, "date_role") in ("", *DATE_ROLES):
        row["date_role"] = _text(form, "date_role")
    if "flags" in form:
        row["display"] = _checked(form, "display")
        row["facet"] = _checked(form, "facet")
    if _text(form, "sensitivity") in SENSITIVITIES:
        row["sensitivity"] = _text(form, "sensitivity")


def pack_rows(packs: Any) -> List[Dict[str, Any]]:
    """The pack table, labelled by the console (Knovas sends keys only)."""
    rows: List[Dict[str, Any]] = []
    for pack in packs or ():
        if not isinstance(pack, Mapping) or not isinstance(pack.get("key"), str):
            continue
        key = pack["key"]
        label, description = PACK_LABELS.get(key, (key, ""))
        version = pack.get("version")
        installed_version = pack.get("installed_version")
        installed = pack.get("installed") is True
        rows.append({
            "key": key,
            "label": label,
            "description": description,
            "version": version,
            "installed": installed,
            "installed_version": installed_version,
            "upgradable": bool(installed and isinstance(version, int)
                               and isinstance(installed_version, int)
                               and version > installed_version),
        })
    return rows


def install_summary(pack: str, result: Mapping[str, Any],
                    registry: Any = None) -> Tuple[str, List[str]]:
    """``(notice, warnings)`` for a pack install: what was added, what was
    skipped because the key existed, and fields left without a target."""
    label = PACK_LABELS.get(pack, (pack, ""))[0]
    installed = int(result.get("installed") or 0)
    skipped = int(result.get("skipped") or 0)
    notice = (f"Paket \u201e{label}\u201c: {installed} Feld(er) angelegt, "
              f"{skipped} \u00fcbersprungen (Schl\u00fcssel bereits vorhanden).")
    warnings: List[str] = []
    for code in _str_list(result.get("warnings")):
        if code.startswith("target_type_missing:"):
            key = code.split(":", 1)[1]
            warnings.append(
                f"\u201e{field_label(registry, key)}\u201c: kein passender Typ im "
                "Wissensgraphen gefunden \u2013 ohne Verkn\u00fcpfungsziel angelegt; "
                "Namen bleiben unverkn\u00fcpft.")
        else:
            warnings.append(warning_text(code))
    return notice, warnings


def rule_rows(rules: Any, registry: Any) -> List[Dict[str, Any]]:
    """Live folder rules for the table, values formatted by the registry."""
    specs = {str(s.get("key")): s for s in registry or () if isinstance(s, Mapping)}
    rows: List[Dict[str, Any]] = []
    for rule in rules or ():
        if not isinstance(rule, Mapping):
            continue
        values = []
        for key, value in (rule.get("set") or {}).items() if isinstance(rule.get("set"), Mapping) else ():
            empty = value is None or value == []
            values.append({
                "key": str(key),
                "label": field_label(registry, str(key)),
                "text": "aufgehoben (keine Vorgabe)" if empty
                        else format_value(specs.get(str(key)), value),
            })
        rows.append({
            "id": str(rule.get("id") or ""),
            "prefix": str(rule.get("pointer_prefix") or ""),
            "version": rule.get("version"),
            "values": values,
        })
    rows.sort(key=lambda r: r["prefix"])
    return rows


def rule_warning_texts(warnings: Any, registry: Any) -> List[str]:
    """``[{key, path, code}]`` from a rule save, as per-field notes."""
    out: List[str] = []
    for item in warnings or ():
        if isinstance(item, Mapping) and isinstance(item.get("code"), str):
            key = item.get("key")
            note = warning_text(item["code"])
            out.append(f"\u201e{field_label(registry, key)}\u201c: {note}" if isinstance(key, str) else note)
    return out


def unknown_profile_keys(profile_keys: Iterable[str], raw_fields: Any) -> List[str]:
    """Keys the ingestion profile writes that the registry would not take:
    neither an active key nor an alias of one. ``reject`` refuses uploads
    carrying them."""
    known: set = set()
    for raw in raw_fields or ():
        if not isinstance(raw, Mapping) or raw.get("status") == "deprecated":
            continue
        if isinstance(raw.get("key"), str):
            known.add(raw["key"])
        known.update(a.casefold() for a in _str_list(raw.get("aliases")))
    return sorted(k for k in set(profile_keys or ()) if k not in known and k.casefold() not in known)


def admin_group_ids(groups: Any) -> frozenset:
    """The ids of the tenant's administrator group(s) at Knovas
    (``is_admin`` in ``GET /secured/access_groups``)."""
    return frozenset(
        str(g.get("group_id")) for g in groups or ()
        if isinstance(g, Mapping) and g.get("is_admin") is True and g.get("group_id")
    )


def may_write_registry(rules_state: str, admin_member: Optional[bool]) -> bool:
    """Predict Knovas's S8 check for the forms (spec 4.6).

    The rule listing needs exactly the clearance the writes need, so its
    answer decides: ``ok`` -> writable, ``forbidden`` (403) -> read-only.
    When the listing failed for another reason, membership in Knovas's
    administrator group is the fallback. Knovas still decides every write.
    """
    if rules_state == "ok":
        return True
    if rules_state == "forbidden":
        return False
    return bool(admin_member)


def knovas_message(exc: BaseException, registry: Any = None) -> Tuple[str, int]:
    """``(German text, HTTP status for the page)`` for a failed Knovas call."""
    if isinstance(exc, DocFieldsUnavailable):
        return OFF_TEXT, 409
    if isinstance(exc, DocFieldsError):
        code = int(exc.status or 0)
        status = code if 400 <= code < 500 else 502
        return error_message(exc.error_code, exc.details, registry), status
    return "Die Anfrage an Knovas ist fehlgeschlagen.", 502


def path_key(path: Any) -> Optional[str]:
    """``set.doc_type[0]`` / ``where.mandant`` -> the field key, else None."""
    if not isinstance(path, str) or "." not in path:
        return None
    key = re.sub(r"\[\d+\]$", "", path.split(".", 1)[1])
    return key or None


def _log_failure(action: str, exc: BaseException) -> None:
    """Codes only: the exception text may carry a server message."""
    logger.warning("doc-fields admin %s failed: %s %s", action,
                   getattr(exc, "status", "-"), getattr(exc, "error_code", type(exc).__name__))


# ---------------------------------------------------------------------------
# Pure helpers: the documents drawer and the Feldfilter (spec 4.7)
# ---------------------------------------------------------------------------

def _specs(registry: Any) -> Dict[str, Mapping[str, Any]]:
    return {str(s.get("key")): s for s in registry or ()
            if isinstance(s, Mapping) and isinstance(s.get("key"), str)}


def editable_keys(registry: Any, *, roles: Iterable[str], edit_roles: Iterable[str],
                  identity_on: bool = True, held: bool = False) -> List[str]:
    """``title``, ``description`` and the active fields this person may edit
    (D12: a role in ``edit_roles``; ``admin`` for special fields)."""
    if not can_edit(roles, edit_roles, "normal", held, identity_on):
        return []
    keys = ["title", "description"]
    for key, spec in _specs(registry).items():
        if key in SYSTEM_KEYS or spec.get("status") == "deprecated":
            continue
        if can_edit(roles, edit_roles, spec.get("sensitivity"), held, identity_on):
            keys.append(key)
    return keys


def _hidden_entities(value: Any) -> bool:
    items = value if isinstance(value, list) else [value]
    return any(isinstance(v, Mapping) and (v.get("hidden") is True
                                           or (v.get("node_id") and not v.get("name")))
               for v in items)


def _input_for(spec: Mapping[str, Any], value: Any) -> Dict[str, Any]:
    """One edit input: a select for a single enum or bool, else text that
    Knovas parses back (the formatted value is valid input: ``15.03.2024``,
    ``CHF 1'234.50``, labels for enum codes)."""
    key = str(spec.get("key"))
    out: Dict[str, Any] = {
        "key": key,
        "label": spec.get("label") or key,
        "datatype": spec.get("datatype"),
        "cardinality": spec.get("cardinality") or "one",
        "sensitivity": spec.get("sensitivity") or "normal",
        "kind": "text",
        "value": format_value(spec, value),
        "locked": False,
        "hint": "",
    }
    one = out["cardinality"] == "one"
    if spec.get("datatype") == "enum" and one:
        out["kind"] = "select"
        out["options"] = [{"code": e.get("code"), "label": e.get("label")}
                          for e in spec.get("enum") or () if isinstance(e, Mapping)]
        out["value"] = value if isinstance(value, str) else ""
    elif spec.get("datatype") == "bool" and one:
        out["kind"] = "bool"
        out["value"] = "true" if value is True else "false" if value is False else ""
    if spec.get("datatype") == "entity_ref" and _hidden_entities(value):
        out["locked"] = True
        out["hint"] = LOCKED_ENTITY_HINT
    if key == "privileged":
        out["hint"] = PRIVILEGED_HINT
    return out


def values_view(raw: Any, registry: Any, *, roles: Iterable[str], edit_roles: Iterable[str],
                identity_on: bool = True) -> Dict[str, Any]:
    """The drawer's JSON for one pointer's values view (contract 4.1).

    Every layer with its badge (``layer_label``) and ``changed_at``; which
    keys this person may edit; inputs for them. A held (quarantined)
    document is shown read-only with an explanation and no values.
    """
    raw = raw if isinstance(raw, Mapping) else {}
    version = raw.get("version")
    view: Dict[str, Any] = {
        "pointer": str(raw.get("pointer") or ""),
        "document_uuid": raw.get("document_uuid") if isinstance(raw.get("document_uuid"), str) else None,
        "version": version if isinstance(version, int) and not isinstance(version, bool) else 0,
        "held": raw.get("acl_mode") == "quarantined",
    }
    if view["held"]:
        view.update({"title": None, "title_source": None, "description": None, "fields": [],
                     "inputs": [], "editable_keys": [], "can_edit": False,
                     "warnings": [], "message": HELD_TEXT})
        return view
    specs = _specs(registry)
    effective = raw.get("fields") if isinstance(raw.get("fields"), Mapping) else {}
    layers = raw.get("layers") if isinstance(raw.get("layers"), Mapping) else {}
    present = set(effective) | set(layers)
    keys = [k for k in specs if k in present]
    keys += sorted(str(k) for k in present if str(k) not in specs)
    fields: List[Dict[str, Any]] = []
    for key in keys:
        if key in SYSTEM_KEYS:
            continue
        spec = specs.get(key)
        entries: List[Dict[str, Any]] = []
        for entry in layers.get(key) or ():
            if not isinstance(entry, Mapping):
                continue
            created = entry.get("created_at")
            entries.append({
                "layer": str(entry.get("layer") or ""),
                "layer_label": layer_label(entry.get("layer")),
                "text": UNSET_TEXT if entry.get("unset") else format_value(spec, entry.get("value")),
                "verified": entry.get("verified") is True,
                "effective": entry.get("effective") is True,
                "changed_at": created if isinstance(created, str) else None,
            })
        current = next((e for e in entries if e["effective"]), None)
        manual = next((e for e in entries if e["layer"] == "manual"), None)
        fields.append({
            "key": key,
            "label": field_label(registry, key),
            "text": format_value(spec, effective.get(key)) if key in effective else UNSET_TEXT,
            "layer": current["layer"] if current else None,
            "layer_label": current["layer_label"] if current else None,
            "verified": bool(current and current["verified"]),
            "changed_at": manual["changed_at"] if manual else None,
            "has_manual": manual is not None,
            "layers": entries,
            "known": spec is not None,
        })
    allowed = editable_keys(registry, roles=roles, edit_roles=edit_roles, identity_on=identity_on)
    inputs = [_input_for(specs[k], effective.get(k)) for k in allowed if k in specs]
    warnings = []
    for item in raw.get("warnings") or ():
        if isinstance(item, str):
            warnings.append({"code": item, "text": warning_text(item)})
        elif isinstance(item, Mapping) and isinstance(item.get("code"), str):
            key = item.get("key")
            warnings.append({"key": key if isinstance(key, str) else None,
                             "code": item["code"], "text": warning_text(item["code"])})
    view.update({
        "title": raw.get("title") if isinstance(raw.get("title"), str) else None,
        "title_source": raw.get("title_source") if isinstance(raw.get("title_source"), str) else None,
        "title_hint": TITLE_NOT_SEARCHABLE,
        "description": raw.get("description") if isinstance(raw.get("description"), str) else None,
        "fields": fields,
        "inputs": inputs,
        "editable_keys": allowed,
        "can_edit": bool(allowed),
        "warnings": warnings,
    })
    return view


_EDIT_OPS = ("set", "unset", "add", "remove")


def _clean_value(key: str, value: Any, op: str) -> Any:
    """One value from the browser, in the shapes a values PATCH takes."""
    if key == "title":
        if value is None or (isinstance(value, str) and 0 < len(value.strip()) <= 500):
            return value
        raise FormError("Der Titel hat 1 bis 500 Zeichen.")
    if key == "description":
        if value is None or (isinstance(value, str) and len(value) <= 2000):
            return value
        raise FormError("Die Beschreibung hat h\u00f6chstens 2000 Zeichen.")
    if value is None:
        if op != "set":
            raise FormError("Leere Werte sind hier nicht m\u00f6glich.")
        return None

    def scalar(item: Any) -> Any:
        if isinstance(item, bool) or (isinstance(item, str) and len(item) <= 4096):
            return item
        if isinstance(item, (int, float)):
            return item
        raise FormError("Ein Wert hat eine Form, die hier nicht vorgesehen ist.")

    if isinstance(value, list):
        if not value and op != "set":
            raise FormError("Leere Listen sind hier nicht m\u00f6glich.")
        if len(value) > VALUES_PER_KEY_MAX:
            raise FormError(f"H\u00f6chstens {VALUES_PER_KEY_MAX} Werte pro Feld.")
        return [scalar(item) for item in value]
    if op in ("add", "remove"):
        return [scalar(value)]
    return scalar(value)


def edit_ops_from_body(body: Any, registry: Any, *, roles: Iterable[str],
                       edit_roles: Iterable[str]) -> Dict[str, Any]:
    """``{set?, unset?, add?, remove?}`` from a drawer edit, limited to the
    keys this person may edit. Raises FormError."""
    body = body if isinstance(body, Mapping) else {}
    allowed = set(editable_keys(registry, roles=roles, edit_roles=edit_roles))
    specs = _specs(registry)
    ops: Dict[str, Any] = {}
    total = 0
    for op in _EDIT_OPS:
        part = body.get(op)
        if part is None:
            continue
        if op == "unset":
            if not isinstance(part, list):
                raise FormError("Die \u00c4nderung hat eine unerwartete Form.")
            keys = []
            for key in part:
                if not isinstance(key, str) or key not in allowed or key in SYSTEM_KEYS:
                    raise FormError("Ein Feld darf hier nicht geleert werden.")
                if key not in keys:
                    keys.append(key)
            if keys:
                ops[op] = keys
                total += len(keys)
            continue
        if not isinstance(part, Mapping):
            raise FormError("Die \u00c4nderung hat eine unerwartete Form.")
        items: Dict[str, Any] = {}
        for key, value in part.items():
            if not isinstance(key, str) or key not in allowed:
                raise FormError("Ein Feld darf hier nicht ge\u00e4ndert werden.")
            if op in ("add", "remove") and (key in SYSTEM_KEYS
                                            or specs.get(key, {}).get("cardinality") != "many"):
                raise FormError("Hinzuf\u00fcgen und Entfernen gibt es nur bei Feldern mit mehreren Werten.")
            items[key] = _clean_value(key, value, op)
        if items:
            ops[op] = items
            total += len(items)
    if total > 400:
        raise FormError("Die \u00c4nderung ist zu umfangreich.")
    return ops


def edit_warnings(warnings: Any, registry: Any) -> List[Dict[str, Any]]:
    """PATCH ``warnings[{key, code}]`` as per-field notes for the drawer."""
    out: List[Dict[str, Any]] = []
    for item in warnings or ():
        if isinstance(item, Mapping) and isinstance(item.get("code"), str):
            key = item.get("key") if isinstance(item.get("key"), str) else None
            out.append({"key": key, "label": field_label(registry, key) if key else None,
                        "code": item["code"], "text": warning_text(item["code"])})
    return out


# The ``document.values_edited`` audit (outcome and detail) is shared with
# the search panel's edit route, so both live in doc_fields_view:
# AUDIT_OUTCOME_REFUSED, VALUES_EDIT_REFUSALS, values_edit_audit_detail.


def where_from_pairs(pairs: Any, registry: Any) -> Dict[str, Any]:
    """The Feldfilter's ``where`` from ``[{field, value}]``.

    Typed by the registry: entity fields send ``{"name": ...}`` (D10: a name
    matches linked and unlinked values; never a node id), bool sends a
    bool, everything else the text as typed (Knovas parses "GJ 2024",
    "Q1 2024", enum labels). A field named twice becomes a list ("any of").
    Raises FormError; the caller still bounds the result with
    ``validate_where``.
    """
    specs = _specs(registry)
    where: Dict[str, Any] = {}
    for pair in pairs or () if isinstance(pairs, (list, tuple)) else ():
        if not isinstance(pair, Mapping):
            continue
        key = pair.get("field")
        value = pair.get("value")
        if not isinstance(key, str) or not key:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        spec = specs.get(key)
        if spec is None:
            raise FormError("Ein Filterfeld gibt es nicht (mehr). Bitte die Seite neu laden.")
        text = value.strip()
        if spec.get("datatype") == "entity_ref":
            operand: Any = {"name": text}
        elif spec.get("datatype") == "bool":
            operand = typed_value({**spec, "cardinality": "one"}, text)
        else:
            operand = text
        if key in where:
            prior = where[key]
            where[key] = (prior if isinstance(prior, list) else [prior]) + [operand]
        else:
            where[key] = operand
    if not where:
        raise FormError("Bitte mindestens ein Feld mit einem Wert angeben.")
    return where


def sort_from_body(sort: Any, registry: Any) -> Optional[Dict[str, str]]:
    """``{field, order}`` for find: only a date or period field, or None."""
    if not isinstance(sort, Mapping) or not sort.get("field"):
        return None
    key = str(sort.get("field"))
    spec = _specs(registry).get(key)
    if spec is None or spec.get("datatype") not in ("date", "period"):
        raise FormError("Sortieren geht nur nach einem Datums- oder Zeitraumfeld.")
    order = "desc" if sort.get("order") == "desc" else "asc"
    return {"field": key, "order": order}


def filter_fields(registry: Any) -> List[Dict[str, Any]]:
    """The fields the Feldfilter offers: active registry fields, with enum
    options for a select. Deprecated keys still match by exact key, but a
    form should not suggest them."""
    out: List[Dict[str, Any]] = []
    for key, spec in _specs(registry).items():
        if spec.get("status") == "deprecated" or key in SYSTEM_KEYS:
            continue
        out.append({
            "key": key,
            "label": spec.get("label") or key,
            "datatype": spec.get("datatype"),
            "enum": [{"code": e.get("code"), "label": e.get("label")}
                     for e in spec.get("enum") or () if isinstance(e, Mapping)],
            "sortable": spec.get("datatype") in ("date", "period"),
        })
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def attach_doc_field_routes(bp, gate, *, csrf_valid, csrf_token, page_context,
                            client_factory, rc_client_factory=None, require_admin):
    """Mount the Dokumentfelder routes onto the console blueprint.

    Takes ``require_admin`` from the blueprint factory, so there is one
    definition of who may reach the console.
    """

    def _csrf_ok() -> bool:
        return csrf_valid(str(request.form.get("csrf_token", "") or ""))

    def _capability(client) -> Capability:
        try:
            return dfc.capability_for(client)
        except Exception as exc:  # noqa: BLE001 - the page must render
            logger.warning("Document fields capability unavailable: %s", type(exc).__name__)
            return Capability.unknown

    def _profile():
        """The current ingestion profile (a ProfileVersion), or None."""
        try:
            from identity.ingestion_profiles import IngestionProfileRepository

            return IngestionProfileRepository(gate.connection()).current()
        except Exception as exc:  # noqa: BLE001 - optional context
            logger.warning("Ingestion profile not readable: %s", type(exc).__name__)
            return None

    def _rc():
        if rc_client_factory is None:
            return None
        try:
            return rc_client_factory()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Knovas Connector client unavailable: %s", type(exc).__name__)
            return None

    def _requeue_support() -> Optional[bool]:
        """Whether the RemoteController advertises ``fields_requeue_v1``:
        False for an older one or none, None when it cannot be asked."""
        try:
            return requeue_supported(_rc())
        except Exception as exc:  # noqa: BLE001 - asked, not answered
            logger.warning("Knovas Connector capabilities unavailable: %s", type(exc).__name__)
            return None

    def _admin_member(client) -> Optional[bool]:
        me = gate.current_user()
        if me is None:
            return None
        try:
            ids = admin_group_ids(client.access_groups())
            mine = set(gate.users().access_groups_of(me.id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Admin group membership not readable: %s", type(exc).__name__)
            return None
        return bool(ids & mine)

    def _render(context: Dict[str, Any], status: int):
        return render_template(
            "admin_doc_fields.html",
            active_nav="admin",
            **page_context(),
            me=gate.current_user(),
            csrf_token=csrf_token(),
            **context,
        ), status

    def _off_page(*, error=None, status=200, capability=None):
        """No registry, no forms. "Not enabled" only when Knovas said so; an
        unclear probe says it could not be determined (the System tab's
        "nicht feststellbar")."""
        unknown = capability is Capability.unknown
        return _render({"enabled": False, "off_text": UNKNOWN_TEXT if unknown else OFF_TEXT,
                        "off_hint": UNKNOWN_HINT if unknown else OFF_HINT,
                        "error": error, "notice": None, "warnings": []}, status)

    def _page(*, error=None, notice=None, warnings=None, status=200, offer_requeue=False,
              confirm=None, rule_form=None, settings_form=None, field_form=None):
        """``settings_form`` / ``field_form``: what the person posted, to show
        again with a confirmation (409) instead of Knovas's stored values --
        their own page only, never logged."""
        client = client_factory()
        capability = _capability(client)
        if not capability.shows_values:
            return _off_page(error=error, status=status, capability=capability)
        context: Dict[str, Any] = {
            "enabled": True, "error": error, "notice": notice, "warnings": list(warnings or []),
            "offer_requeue": bool(offer_requeue and _requeue_support() is True),
            "requeue_label": REQUEUE_OFFER,
            "confirm": {"update": None, "deprecate": None, "reject": False, **(confirm or {})},
            "rule_form": {"folder_path": "", "pointer_prefix": "", "rows": [],
                          **(rule_form or {})},
            "rule_rows_count": RULE_ROWS,
            "capability": capability.value,
            "datatypes": [(d, DATATYPE_LABELS[d]) for d in DATATYPES],
            "date_roles": [(r, DATE_ROLE_LABELS[r]) for r in DATE_ROLES],
            "unknown_key_modes": [(m, UNKNOWN_KEY_LABELS[m]) for m in UNKNOWN_KEY_MODES],
            "date_orders": [(o, DATE_ORDER_LABELS[o]) for o in DATE_ORDERS],
            "texts": {"read_only": READ_ONLY_TEXT, "in_use_deprecate": IN_USE_DEPRECATE,
                      "in_use_update": IN_USE_UPDATE, "reapply": REAPPLY_TEXT,
                      "multi_source": MULTI_SOURCE_CONFIRM},
        }
        problems: List[str] = []
        raw_fields: List[Dict[str, Any]] = []
        try:
            raw_fields = client.doc_fields()
        except DocFieldsUnavailable as exc:
            dfc.observe_exception(exc)
            return _off_page(error=error, status=status)
        except Exception as exc:  # noqa: BLE001
            _log_failure("registry read", exc)
            problems.append("Das Feldverzeichnis ist derzeit nicht abrufbar.")
        registry = sanitize_registry(raw_fields)
        packs: List[Dict[str, Any]] = []
        try:
            packs = client.doc_field_packs()
        except Exception as exc:  # noqa: BLE001
            _log_failure("packs read", exc)
            problems.append("Die Feldpakete sind derzeit nicht abrufbar.")
        current_settings: Dict[str, Any] = {}
        settings_ok = True
        try:
            current_settings = dict(client.doc_field_settings())
        except Exception as exc:  # noqa: BLE001
            _log_failure("settings read", exc)
            problems.append("Die Einstellungen sind derzeit nicht abrufbar.")
            # Unread settings cannot be shown, so they cannot be saved: the
            # selects would fall back to their first options and overwrite
            # values nobody saw.
            settings_ok = False
        rules: List[Dict[str, Any]] = []
        rules_state, rules_note = "ok", None
        try:
            rules = client.doc_field_rules()
        except DocFieldsError as exc:
            if exc.status == 403:
                rules_state, rules_note = "forbidden", RULES_FORBIDDEN_TEXT
            else:
                _log_failure("rules read", exc)
                rules_state, rules_note = "error", RULES_UNAVAILABLE_TEXT
        except Exception as exc:  # noqa: BLE001
            _log_failure("rules read", exc)
            rules_state, rules_note = "error", RULES_UNAVAILABLE_TEXT
        node_types: List[Dict[str, Any]] = []
        try:
            node_types = list(client.graph_node_types() or [])
        except Exception as exc:  # noqa: BLE001 - the target select stays empty
            _log_failure("node types read", exc)
        admin_member = _admin_member(client) if rules_state == "error" else None
        version = _profile()
        profile = getattr(version, "profile", None)
        in_use = profile_field_keys(version) if version is not None else set()
        sources = [str(getattr(s, "path", "") or "") for s in getattr(profile, "sources", None) or ()]
        rows = registry_rows(raw_fields, node_types, in_use)
        if field_form:
            refill_field_row(rows, field_form.get("id"), field_form.get("form") or {})
        context.update({
            "fields": rows,
            "rule_fields": [s for s in registry if s.get("status") != "deprecated"],
            "node_types": [{"id": str(t.get("id")), "name": str(t.get("name") or "")}
                           for t in node_types if isinstance(t, Mapping) and t.get("id")],
            "packs": pack_rows(packs),
            "settings": {**current_settings, **(settings_form or {})},
            "settings_ok": settings_ok,
            "unknown_profile_keys": unknown_profile_keys(in_use, raw_fields),
            "rules": rule_rows(rules, registry),
            "rules_state": rules_state,
            "rules_note": rules_note,
            "writable": may_write_registry(rules_state, admin_member),
            "problems": problems,
            "rc_enabled": rc_client_factory is not None,
            "sources": sources,
            "multi_source": len(sources) > 1,
            "has_profile": profile is not None,
        })
        return _render(context, status)

    def _guard_write(action: str):
        """CSRF first, then the capability. A response to return, or None."""
        if not _csrf_ok():
            return _page(error=EXPIRED_FORM, status=400)
        capability = _capability(client_factory())
        if not capability.shows_values:
            return _off_page(status=409, capability=capability)
        return None

    def _write_failed(action: str, exc: BaseException, registry: Any = None, **kw):
        if isinstance(exc, DocFieldsUnavailable):
            dfc.observe_exception(exc)
            return _off_page(status=409)
        _log_failure(action, exc)
        message, status = knovas_message(exc, registry)
        return _page(error=message, status=status, **kw)

    def _raw_field(client, field_id: str) -> Optional[Dict[str, Any]]:
        for raw in client.doc_fields() or ():
            if isinstance(raw, Mapping) and str(raw.get("id")) == str(field_id):
                return dict(raw)
        return None

    def _in_use() -> set:
        version = _profile()
        return profile_field_keys(version) if version is not None else set()

    @bp.route("/doc-fields")
    @require_admin
    def doc_fields():
        return _page()

    @bp.route("/doc-fields/create", methods=["POST"])
    @require_admin
    def doc_fields_create():
        refused = _guard_write("create")
        if refused is not None:
            return refused
        try:
            defn = definition_from_form(request.form)
        except FormError as exc:
            return _page(error=str(exc), status=400)
        client = client_factory()
        try:
            created = client.create_doc_field(defn)
        except Exception as exc:  # noqa: BLE001 - mapped to a German message
            return _write_failed("create", exc)
        dfc.invalidate()
        audit.record(
            gate.connection(), action="doc_field.created", actor=gate.current_user(),
            target_type="doc_field", target_id=str(created.get("id") or defn["key"]),
            detail={"key": defn["key"], "datatype": defn["datatype"]},
        )
        return _page(notice=f"Feld \u201e{defn['key']}\u201c angelegt.", offer_requeue=True)

    @bp.route("/doc-fields/<field_id>/update", methods=["POST"])
    @require_admin
    def doc_fields_update(field_id):
        refused = _guard_write("update")
        if refused is not None:
            return refused
        client = client_factory()
        try:
            current = _raw_field(client, field_id)
        except Exception as exc:  # noqa: BLE001
            return _write_failed("update", exc)
        if current is None:
            return _page(error="Dieses Feld gibt es nicht (mehr).", status=404)
        try:
            changes = changes_from_form(request.form, current)
        except FormError as exc:
            return _page(error=str(exc), status=400)
        if not changes:
            return _page(notice="Keine \u00c4nderung.")
        key = str(current.get("key"))
        if (key in _in_use() and needs_use_confirmation(changes)
                and not _checked(request.form, "confirm_in_use")):
            return _page(error=f"\u201e{key}\u201c {IN_USE_UPDATE}. {CONFIRM_REQUIRED}",
                         status=409, confirm={"update": field_id},
                         field_form={"id": field_id, "form": request.form})
        try:
            updated = client.update_doc_field(field_id, changes)
        except Exception as exc:  # noqa: BLE001
            return _write_failed("update", exc)
        if updated is None:
            return _page(error="Dieses Feld gibt es nicht (mehr).", status=404)
        dfc.invalidate()
        audit.record(
            gate.connection(), action="doc_field.updated", actor=gate.current_user(),
            target_type="doc_field", target_id=str(field_id),
            detail={"key": key, "datatype": str(current.get("datatype") or ""),
                    "changed": sorted(changes)},
        )
        return _page(notice=f"Feld \u201e{key}\u201c ge\u00e4ndert.", offer_requeue=True)

    @bp.route("/doc-fields/<field_id>/deprecate", methods=["POST"])
    @require_admin
    def doc_fields_deprecate(field_id):
        refused = _guard_write("deprecate")
        if refused is not None:
            return refused
        client = client_factory()
        try:
            current = _raw_field(client, field_id)
        except Exception as exc:  # noqa: BLE001
            return _write_failed("deprecate", exc)
        if current is None:
            return _page(error="Dieses Feld gibt es nicht (mehr).", status=404)
        key = str(current.get("key"))
        if key in _in_use() and not _checked(request.form, "confirm_in_use"):
            return _page(error=f"\u201e{key}\u201c {IN_USE_DEPRECATE}. {CONFIRM_REQUIRED}",
                         status=409, confirm={"deprecate": field_id})
        try:
            done = client.deprecate_doc_field(field_id)
        except Exception as exc:  # noqa: BLE001
            return _write_failed("deprecate", exc)
        if done is None:
            return _page(error="Dieses Feld gibt es nicht (mehr).", status=404)
        dfc.invalidate()
        audit.record(
            gate.connection(), action="doc_field.deprecated", actor=gate.current_user(),
            target_type="doc_field", target_id=str(field_id),
            detail={"key": key, "datatype": str(current.get("datatype") or "")},
        )
        return _page(
            notice=(f"Feld \u201e{key}\u201c stillgelegt. Vorhandene Werte bleiben und "
                    "sind weiter \u00fcber den genauen Schl\u00fcssel filterbar; neue "
                    "Uploads mit diesem Schl\u00fcssel werden nicht mehr \u00fcbernommen."),
            offer_requeue=True)

    @bp.route("/doc-fields/packs/<pack>/install", methods=["POST"])
    @require_admin
    def doc_fields_install_pack(pack):
        refused = _guard_write("install")
        if refused is not None:
            return refused
        client = client_factory()
        try:
            result = client.install_doc_field_pack(pack)
        except Exception as exc:  # noqa: BLE001
            return _write_failed("pack install", exc)
        dfc.invalidate()
        registry: List[Dict[str, Any]] = []
        try:
            registry = sanitize_registry(client.doc_fields())
        except Exception as exc:  # noqa: BLE001 - only for nicer labels
            _log_failure("registry read", exc)
        notice, warnings = install_summary(pack, result, registry)
        audit.record(
            gate.connection(), action="doc_field_pack.installed", actor=gate.current_user(),
            target_type="doc_field_pack", target_id=str(pack),
            detail={"pack": str(pack), "installed_count": int(result.get("installed") or 0),
                    "skipped_count": int(result.get("skipped") or 0)},
        )
        return _page(notice=notice, warnings=warnings, offer_requeue=True)

    @bp.route("/doc-fields/settings", methods=["POST"])
    @require_admin
    def doc_fields_settings():
        refused = _guard_write("settings")
        if refused is not None:
            return refused
        unknown_keys = _text(request.form, "unknown_keys")
        date_order = _text(request.form, "date_order")
        if unknown_keys not in UNKNOWN_KEY_MODES or date_order not in DATE_ORDERS:
            return _page(error="Bitte eine g\u00fcltige Einstellung w\u00e4hlen.", status=400)
        client = client_factory()
        if unknown_keys == "reject" and not _checked(request.form, "confirm_reject"):
            try:
                refused_keys = unknown_profile_keys(_in_use(), client.doc_fields())
            except Exception as exc:  # noqa: BLE001
                return _write_failed("settings", exc)
            if refused_keys:
                return _page(
                    error=("Mit \u201eablehnen\u201c w\u00fcrden Uploads mit diesen "
                           "Schl\u00fcsseln der Ingestion-Konfiguration abgewiesen: "
                           + ", ".join(refused_keys) + ". " + CONFIRM_REQUIRED),
                    status=409, confirm={"reject": True},
                    settings_form={"unknown_keys": unknown_keys, "date_order": date_order})
        try:
            client.set_doc_field_settings(unknown_keys=unknown_keys, date_order=date_order)
        except Exception as exc:  # noqa: BLE001
            return _write_failed("settings", exc)
        dfc.invalidate()
        audit.record(
            gate.connection(), action="doc_field_settings.changed", actor=gate.current_user(),
            target_type="doc_field_settings", target_id="-",
            detail={"unknown_keys": unknown_keys, "date_order": date_order},
        )
        return _page(notice="Einstellungen gespeichert.", offer_requeue=True)

    def _rule_form() -> Dict[str, Any]:
        """What the person posted, to fill the form again after an error.
        Rendered into their own page only, never logged."""
        form = request.form
        rows = [{"key": _text(form, f"rule_key_{i}"), "value": _text(form, f"rule_value_{i}"),
                 "clear": _checked(form, f"rule_clear_{i}")} for i in range(RULE_ROWS)]
        return {"folder_path": _folder_text(form),
                "pointer_prefix": _text(form, "pointer_prefix"), "rows": rows}

    @bp.route("/doc-fields/rules/save", methods=["POST"])
    @require_admin
    def doc_fields_rule_save():
        refused = _guard_write("rule save")
        if refused is not None:
            return refused
        form = request.form
        client = client_factory()
        me = gate.current_user()
        try:
            registry = dfc.registry_for(client, getattr(me, "id", None))
        except Exception as exc:  # noqa: BLE001
            return _write_failed("rule save", exc, rule_form=_rule_form())
        version = _profile()
        profile = getattr(version, "profile", None)
        sources = [str(getattr(s, "path", "") or "") for s in getattr(profile, "sources", None) or ()]
        try:
            folder = _folder_text(form)
            if folder:
                if profile is None:
                    raise FormError("Ohne Ingestion-Konfiguration bitte den Ordnerpfad bei Knovas eingeben.")
                try:
                    prefix, _matches = prefix_for_folder(profile.identifier_prefix, sources, folder)
                except FolderOutsideSources:
                    raise FormError("Der Ordner liegt in keiner Quelle der Ingestion-Konfiguration.") from None
                except ValueError:
                    raise FormError("F\u00fcr diesen Ordner l\u00e4sst sich kein Pfad bei Knovas bilden.") from None
            else:
                manual = _text(form, "pointer_prefix")
                if not manual:
                    raise FormError("Bitte einen Ordner w\u00e4hlen oder den Pfad bei Knovas eingeben.")
                try:
                    prefix = normalize_pointer_prefix(manual)
                except ValueError:
                    raise FormError("Der Pfad bei Knovas ist leer, zu lang oder enth\u00e4lt . oder ..") from None
            if len(sources) > 1 and not _checked(form, "confirm_all_sources"):
                raise FormError(
                    f"Die Ingestion-Konfiguration hat {len(sources)} Quellen. Der Quellordner "
                    "ist nicht Teil des Pfads bei Knovas; die Vorgabe "
                    f"{MULTI_SOURCE_CONFIRM}. Bitte das best\u00e4tigen.")
            values = rule_values_from_form(form, registry)
        except FormError as exc:
            return _page(error=str(exc), status=400, rule_form=_rule_form())
        try:
            result = client.put_doc_field_rule(prefix, values)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocFieldsError) and exc.error_code == "unknown_field":
                dfc.invalidate()
            return _write_failed("rule save", exc, registry, rule_form=_rule_form())
        rule = result.get("rule") if isinstance(result.get("rule"), Mapping) else {}
        audit.record(
            gate.connection(), action="doc_field_rule.saved", actor=me,
            target_type="doc_field_rule", target_id=str(rule.get("id") or "-"),
            detail={"prefix_depth": prefix_depth(prefix), "keys": sorted(values)},
        )
        warnings = rule_warning_texts(result.get("warnings"), registry)
        overlap = sorted(set(values) & _in_use())
        if overlap:
            warnings.append(
                "Diese Schl\u00fcssel setzt auch die Ingestion-Konfiguration; dort, wo "
                "ein Upload sie mitliefert, hat der Upload-Wert Vorrang: "
                + ", ".join(field_label(registry, k) for k in overlap) + ".")
        version_no = rule.get("version")
        return _page(
            notice=("Ordnervorgabe gespeichert"
                    + (f" (Version {version_no})" if isinstance(version_no, int) else "")
                    + f"; {REAPPLY_TEXT}."),
            warnings=warnings)

    @bp.route("/doc-fields/rules/delete", methods=["POST"])
    @require_admin
    def doc_fields_rule_delete():
        """Retire a rule. The browser names the rule by its id; the prefix
        is looked up here, so it never travels back from the page."""
        refused = _guard_write("rule delete")
        if refused is not None:
            return refused
        rule_id = _text(request.form, "rule_id")
        if not rule_id:
            return _page(error="Keine Vorgabe ausgew\u00e4hlt.", status=400)
        client = client_factory()
        try:
            live = client.doc_field_rules()
        except Exception as exc:  # noqa: BLE001
            return _write_failed("rule delete", exc)
        rule = next((r for r in live or () if isinstance(r, Mapping)
                     and str(r.get("id")) == rule_id), None)
        if rule is None:
            return _page(notice=f"F\u00fcr diesen Ordner gibt es {NO_LIVE_RULE} \u2013 nichts zu entfernen.")
        prefix = str(rule.get("pointer_prefix") or "")
        try:
            result = client.retire_doc_field_rule(prefix)
        except Exception as exc:  # noqa: BLE001
            return _write_failed("rule delete", exc)
        if result is None:
            return _page(notice=f"F\u00fcr diesen Ordner gibt es {NO_LIVE_RULE} \u2013 nichts zu entfernen.")
        audit.record(
            gate.connection(), action="doc_field_rule.retired", actor=gate.current_user(),
            target_type="doc_field_rule", target_id=rule_id,
            detail={"prefix_depth": prefix_depth(prefix),
                    "keys": sorted(str(k) for k in (rule.get("set") or {}))},
        )
        return _page(notice=f"Ordnervorgabe entfernt; {REAPPLY_TEXT}.")

    @bp.route("/doc-fields/requeue", methods=["POST"])
    @require_admin
    def doc_fields_requeue():
        """Ask the RemoteController to send uploads Knovas refused again,
        after the registry changed. Only a RemoteController that advertises
        ``fields_requeue_v1`` is asked; one that cannot be asked now is
        "nicht erreichbar", never "too old". Audited exactly like the
        Ingestion tab's buttons (one billed operation, one shape)."""
        refused = _guard_write("requeue")
        if refused is not None:
            return refused
        support = _requeue_support()
        if support is None:
            return _page(error=REQUEUE_UNREACHABLE, status=502)
        if not support:
            return _page(error=REQUEUE_UNSUPPORTED, status=409)
        try:
            count = int(_rc().requeue_doc_fields("refused") or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("doc-fields requeue failed: %s", type(exc).__name__)
            return _page(error="Der Knovas Connector hat das erneute Senden nicht angenommen.",
                         status=502)
        audit.record(gate.connection(), actor=gate.current_user(),
                     **requeue_audit("refused", count))
        if not count:
            return _page(notice="Keine abgelehnten Uploads zum erneuten Senden vorgemerkt.")
        return _page(notice=(f"{count} abgelehnte(r) Upload(s) werden beim n\u00e4chsten "
                             "Durchlauf erneut gesendet."))

    return bp
