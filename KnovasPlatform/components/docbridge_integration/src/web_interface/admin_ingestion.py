"""The Ingestion tab: what to index, when, how fast, behind which wall.

One profile, one form, one write (section B plan, "Ingestion administration").
The form edits an IngestionProfile; compile_profile is the only thing that
produces RemoteController documents; RemoteControllerClient.push is the only
thing that sends them. Saving is a guarded action, because a profile change
can widen or halt coverage (KC-B5-2).

Document fields (spec 4.8)
--------------------------
Each folder can give the documents it uploads Knovas field values: static
values, path-template captures and opted-in extractor metadata. They go to
RemoteController's upload layer, so the tab guards three things:

    - an older RemoteController refuses the new keys, so a profile using them
      is neither saved nor pushed unless it advertises ``source_fields_v1``
      (plus ``field_templates_v1`` / ``metadata_fields_v1`` when used, and
      ``metadata_fields_v2`` for the keywords and status file properties);
    - keys are checked against the tenant's registry when a profile is saved
      (enum labels become codes), and a key also set by a folder rule
      reaching the profile's documents is warned about;
    - changing a source's fields re-sends every document of that source,
      each a billed upload with fresh text recognition, so the save needs a
      confirmation that states the cost.

The inputs exist only while Knovas offers document fields (or the profile
already carries some, so a save cannot drop them silently). Values,
templates and captures stay on this page: the support JSON, the approvals
summary, the audit row and every log line carry counts only.

Re-extraction (spec L6)
-----------------------
After an extractor upgrade the tab shows how many documents an older
extraction produced (the Knovas Connector's ``extraction.outdated``) and
offers *Neu extrahieren* to an admin only, after a confirmation that states
count, cost and duration and carries the count it showed. Audited with
counts only.

Plan: docs/superpowers/plans/2026-09-02-admin-ingestion-tab.md
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping, Sequence
from uuid import UUID

from flask import jsonify, render_template, request

import doc_fields_capability
from doc_fields_capability import Capability
from doc_fields_view import field_label, requeue_audit, warning_text
from identity import audit
from identity.approvals import ApprovalService
from identity.field_templates import (
    MAX_TEMPLATE_CHARS,
    TemplateError,
    compile_template,
    first_match,
)
from identity.ingestion_compiler import (
    MAX_FIELD_TEMPLATES,
    METADATA_ITEMS,
    RC_TOO_OLD,
    RC_UNREACHABLE,
    TEMPLATE_ERROR_TEXT,
    IngestionProfile,
    ProfileError,
    SourceFolder,
    compile_profile,
    field_config_changes,
    field_config_counts,
    folder_rule_conflicts,
    is_field_key,
    profile_uses_fields,
    redact_for_support,
    upload_field_keys,
    validate_profile_fields,
)
from identity import ingestion_presets as presets
from identity.ingestion_profiles import (
    IngestionProfileRepository,
    profile_from_json,
    profile_to_json,
)
from remote_controller_client import (
    CAP_FIELDS_REQUEUE,
    CAP_SOURCE_FIELDS,
    REQUEUE_OUTCOMES,
    RemoteControllerError,
    advertised_capabilities,
    capabilities_from_status,
    required_capabilities,
)
from web_interface.guarded import run_guarded

logger = logging.getLogger(__name__)

MAX_FOLDER_ROWS = 12
KIND = "ingestion_profile_change"
FOLDER_DISCOVER_DEPTH = 1

#: Who can actually carry an approved profile change out. RemoteController's
#: gate admits `admin` and `ingestion_manager` only (RC/src/auth/
#: platform_principal.py::ADMIN_ROLES), while APPROVER_ROLES is
#: {approver, admin} -- so a pure approver can confirm the request and then
#: cannot execute it. Say that before a version row is written, not after
#: RemoteController answers 403.
EXECUTOR_ROLES = frozenset({"admin", "ingestion_manager"})

# German labels for the presets; the preset ids stay the compiler's.
LABELS = {
    "continuous": ("Laufend", "Neue und geaenderte Dokumente innerhalb weniger Minuten, den ganzen Tag."),
    "nightly": ("Nachts, ausserhalb der Buerozeiten", "Nur zwischen 19:00 und 06:00. Nichts laeuft, waehrend gearbeitet wird."),
    "manual": ("Nur wenn ich starte", "Laeuft einmal, wenn Sie auf Start druecken, und stoppt dann."),
    "gentle": ("Schonend", "Etwa 300 Dokumente pro Stunde. Keine spuerbare Last auf dem Dateiserver."),
    "normal": ("Normal", "Etwa 1'800 Dokumente pro Stunde. Die richtige Wahl fuer die meisten Kanzleien."),
    "fast": ("Schnell", "Etwa 7'200 Dokumente pro Stunde. Fuer den ersten Import, danach zuruecksetzen."),
    "documents": ("Dokumente", "Word, PDF, Text und Markdown."),
    "email": ("E-Mail", "Aus Outlook gespeicherte Nachrichten."),
}


# What "uebertragen" actually got the firm, in the words the tab uses. A push
# reaches RemoteController in three different states and only one of them is
# "the new folder list is being indexed right now"; saying so is the whole
# point of RemoteControllerClient.push returning an outcome (C2).
APPLIED_CLAUSES = {
    "started": "Abgleich gestartet.",
    "next_cycle": "wird beim naechsten Durchlauf wirksam.",
    "stored": "der Abgleich wird von Hand gestartet.",
}


def _requester(requested_by: str | None, actor) -> SimpleNamespace | None:
    """The person who asked, as something ``save_new_version`` can take.

    ``None`` when nobody else asked -- the actor requested and executed in
    one click -- or when the id is not a UUID, in which case attributing the
    row to the executor is the honest fallback.
    """
    if not requested_by or str(requested_by) == str(getattr(actor, "id", "")):
        return None
    try:
        return SimpleNamespace(id=UUID(str(requested_by)))
    except (TypeError, ValueError):
        logger.warning("requested_by ist keine UUID (%r); Version wird dem Ausfuehrenden "
                       "zugeschrieben.", requested_by)
        return None


def _applied_clause(result: Mapping[str, Any] | None) -> str:
    """The half-sentence that follows the version number in a save notice."""
    result = result or {}
    applied = str(result.get("applied") or "stored")
    text = "; " + APPLIED_CLAUSES.get(applied, APPLIED_CLAUSES["stored"])
    start_error = result.get("start_error")
    if start_error:
        text += f" Start fehlgeschlagen: {start_error}"
    return text


def _labelled(table: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    return {key: {"label": LABELS.get(key, (key, ""))[0],
                  "description": LABELS.get(key, (key, ""))[1]} for key in table}


# ---------------------------------------------------------------------------
# Document fields per folder (spec 4.8)
# ---------------------------------------------------------------------------
#
# German texts are \u escapes (.py files stay ASCII-only). No text here, and
# nothing logged, repeats a field value, a template or a capture.

#: What each extractor metadata item does, as the form says it (spec 3.5).
METADATA_LABELS = {
    "language": ("Sprache (aus Dokumenteigenschaften von pdf/docx, oft Programmsprache; "
                 ".eml: Content-Language)"),
    "email_date": "E-Mail-Datum als Dokumentdatum (nur .eml/.msg)",
    "email_doc_type": "E-Mails als Dokumentart \u201eE-Mail\u201c (nur .eml/.msg)",
    "email_author": "E-Mail-Absender als Autor (nur .eml/.msg)",
    # .md and .txt carry no document properties (knovas-extract reads them as
    # plain text), so the item yields nothing there.
    "document_author": "Autor aus Dokumenteigenschaften (pdf, docx)",
    # Spec L1. Knovas keeps a status only when it matches a choice of the
    # ``status`` field; one it does not know shows as invalid_value (status).
    "keywords": ("Stichw\u00f6rter aus Datei-Eigenschaften "
                 "(PDF/Word-Stichw\u00f6rter, Outlook-Kategorien)"),
    "document_status": "Status aus Word-Dokumentstatus",
}

#: RemoteController's default re-upload bound (RC_FIELDS_REUPLOAD_PER_CYCLE),
#: used when an older status does not report ``per_cycle``.
DEFAULT_REUPLOAD_PER_CYCLE = 100
#: Files per folder the template preview lists.
PREVIEW_FILES_PER_SOURCE = 20
#: How deep the preview scans, the same as the folder summary.
PREVIEW_SCAN_DEPTH = 3

REUPLOAD_UNCONFIRMED = ("Bitte best\u00e4tigen, dass die Dokumente der ge\u00e4nderten Ordner "
                        "erneut gesendet werden.")
FOLDER_RULE_ADVICE = ("Werte, die nicht vom Ordner abh\u00e4ngen, geh\u00f6ren in eine "
                      "Ordnervorgabe (Verwaltung \u2192 Dokumentfelder): sie gelten ohne "
                      "erneutes Senden.")
UNVERIFIABLE = ("Dokumentfelder k\u00f6nnen nicht gepr\u00fcft werden: Knovas stellt sie "
                "derzeit nicht bereit. Ge\u00e4nderte Felder werden erst nach einer "
                "Pr\u00fcfung \u00fcbertragen.")
NOT_RECHECKED = ("Feldschl\u00fcssel nicht erneut gepr\u00fcft: Dokumentfelder sind bei "
                 "Knovas derzeit nicht verf\u00fcgbar.")
RULES_FORBIDDEN = "Ordnervorgaben nicht pr\u00fcfbar (nur Knovas-Administratorgruppe)."
RULES_UNAVAILABLE = "Ordnervorgaben nicht pr\u00fcfbar."

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def parse_static_fields(text: Any, folder: int) -> tuple[tuple[str, Any], ...]:
    """``schluessel = Wert`` per line; several values separated by ``;``.

    Values stay strings: the server's normaliser decides what "GJ 2024"
    means, as it does for RemoteController's captures. Errors name the line,
    never its content.
    """
    pairs: dict[str, Any] = {}
    where = f"Ordner {folder}, feste Werte, Zeile"
    for line_no, raw in enumerate(str(text or "").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        key, sep, rest = line.partition("=")
        key = key.strip()
        if not sep:
            raise ProfileError(f"{where} {line_no}: erwartet \u201eschl\u00fcssel = Wert\u201c.")
        if not is_field_key(key):
            raise ProfileError(f"{where} {line_no}: ung\u00fcltiger Feldschl\u00fcssel "
                               "(Kleinbuchstaben, Ziffern und _, beginnt mit einem Buchstaben).")
        if key in pairs:
            raise ProfileError(f"{where} {line_no}: Feld \u201e{key}\u201c steht doppelt.")
        values = [v.strip() for v in rest.split(";") if v.strip()]
        if not values:
            raise ProfileError(f"{where} {line_no}: kein Wert.")
        pairs[key] = values[0] if len(values) == 1 else tuple(values)
    return tuple(sorted(pairs.items()))


def parse_templates(text: Any) -> tuple[str, ...]:
    """One template per line; blank lines dropped, each line stripped."""
    return tuple(line.strip() for line in str(text or "").splitlines() if line.strip())


def parse_metadata_items(items: Sequence[str] | None, folder: int) -> tuple[str, ...]:
    chosen = tuple(dict.fromkeys(str(i) for i in (items or ()) if i))
    if any(item not in METADATA_ITEMS for item in chosen):
        raise ProfileError(f"Ordner {folder}: unbekannte Dateieigenschaft.")
    return tuple(item for item in METADATA_ITEMS if item in chosen)


def _value_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def format_static_fields(pairs: Sequence[tuple[str, Any]]) -> str:
    """The inverse of ``parse_static_fields`` for the form."""
    lines = []
    for key, value in pairs or ():
        values = value if isinstance(value, (list, tuple)) else (value,)
        lines.append(f"{key} = {'; '.join(_value_text(v) for v in values)}")
    return "\n".join(lines)


def _count(value: Any) -> int:
    """A non-negative count from a status answer; anything else is 0."""
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _window_seconds(window: Mapping[str, Any]) -> int:
    """Length of a ``start_local``-``end_local`` window, across midnight too."""
    def minutes(text: Any) -> int:
        hours, _, mins = str(text or "00:00").partition(":")
        return int(hours or 0) * 60 + int(mins or 0)

    start, end = minutes(window.get("start_local")), minutes(window.get("end_local"))
    span = end - start if end > start else 24 * 60 - (start - end)
    return max(60, span * 60)


def _duration_text(seconds: int) -> str:
    if seconds < 3600:
        n = max(1, math.ceil(seconds / 60))
        return f"ca. {n} Minute" if n == 1 else f"ca. {n} Minuten"
    if seconds < 48 * 3600:
        n = math.ceil(seconds / 3600)
        return f"ca. {n} Stunde" if n == 1 else f"ca. {n} Stunden"
    return f"ca. {math.ceil(seconds / 86400)} Tage"


def reupload_bound(per_cycle: Any, throughput: str) -> int:
    """Documents re-sent per cycle: RemoteController's bound, capped by the
    throughput preset's files per cycle (the re-upload list only fills room
    that new and changed files leave)."""
    speed = presets.THROUGHPUT_PRESETS.get(throughput) or presets.THROUGHPUT_PRESETS["normal"]
    return max(1, min(_count(per_cycle) or DEFAULT_REUPLOAD_PER_CYCLE,
                      int(speed["max_files_per_cycle"])))


def reupload_eta(count: int, per_cycle: int, schedule: str, throughput: str) -> str:
    """How long re-sending ``count`` documents takes: ``ceil(count /
    per_cycle)`` cycles times the schedule's cadence (spec 4.8).

    The cadence is the preset's scan interval plus the time the throughput
    preset needs to upload one bound of documents; RemoteController runs
    cycle after cycle while its window is open, so a nightly schedule is
    expressed in nights of that window. ``manual`` runs one cycle per press
    of Start. Text recognition time is not included, which makes this the
    shorter side of the truth -- hence "ca.".
    """
    count = _count(count)
    if count == 0:
        return "keine"
    sched = presets.SCHEDULE_PRESETS.get(schedule) or presets.SCHEDULE_PRESETS["nightly"]
    speed = presets.THROUGHPUT_PRESETS.get(throughput) or presets.THROUGHPUT_PRESETS["normal"]
    bound = reupload_bound(per_cycle, throughput)
    cycles = math.ceil(count / bound)
    if sched["mode"] != "continuous":
        return "einmal Start" if cycles == 1 else f"{cycles}-mal Start"
    per_minute = max(1, int(speed["max_ingestion_requests_per_minute"]))
    period = int(sched["scan_interval_seconds"] or 0) + math.ceil(bound * 60 / per_minute)
    seconds = cycles * period
    window = _window_seconds(sched["window"])
    if window < 20 * 3600:
        nights = math.ceil(seconds / window)
        return "ca. 1 Nacht" if nights == 1 else f"ca. {nights} N\u00e4chte"
    return _duration_text(seconds)


def _schedule_label(schedule: str) -> str:
    return LABELS.get(schedule, (schedule, ""))[0]


def reupload_text(paths: Sequence[str], total: Any, per_cycle: Any,
                  schedule: str, throughput: str) -> str:
    """The cost sentence a field change must be confirmed against (spec 4.8).

    ``total`` is ``document_sync.total`` (all sources: an upper bound), or
    None before RemoteController has reported one.
    """
    bound = reupload_bound(per_cycle, throughput)
    head = (f"Alle Dokumente der Quelle(n) {', '.join(paths)} werden erneut gesendet "
            "\u2013 je ein verrechneter Upload mit erneuter Texterkennung.")
    label = _schedule_label(schedule)
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        eta = reupload_eta(total, bound, schedule, throughput)
        return (f"{head} H\u00f6chstens {total} Dokumente; bei {bound} pro Durchlauf und "
                f"Zeitplan \u201e{label}\u201c {eta}.")
    return (f"{head} Wie viele Dokumente es sind, meldet der Knovas Connector erst nach einem "
            f"Abgleich; gesendet werden {bound} pro Durchlauf, Zeitplan \u201e{label}\u201c.")


def reupload_info(paths: Sequence[str], rc_status: Mapping[str, Any] | None,
                  schedule: str, throughput: str) -> dict[str, Any] | None:
    if not paths:
        return None
    rc_status = rc_status if isinstance(rc_status, Mapping) else {}
    document_sync = rc_status.get("document_sync")
    total = document_sync.get("total") if isinstance(document_sync, Mapping) else None
    block = rc_status.get("doc_fields")
    per_cycle = block.get("per_cycle") if isinstance(block, Mapping) else None
    return {
        "paths": list(paths),
        "text": reupload_text(paths, total, per_cycle, schedule, throughput),
        "advice": FOLDER_RULE_ADVICE,
    }


def _codes(mapping: Any) -> list[tuple[str, int]]:
    """``{code: count}`` from a status block, keeping only code-shaped keys
    and positive counts (the block is meant to hold nothing else)."""
    if not isinstance(mapping, Mapping):
        return []
    return sorted((code, _count(n)) for code, n in mapping.items()
                  if isinstance(code, str) and _CODE_RE.match(code) and _count(n))


#: Warning entries the status bar shows at most (the Knovas Connector sends
#: at most 50, the most frequent first).
MAX_WARNING_ENTRIES = 50


def _warning_entries(value: Any) -> list[tuple[str, str, int]]:
    """``(code, key, count)`` from a status block's ``warnings``.

    A current Knovas Connector sends ``[{"code", "key", "count"}]`` (spec
    F4), an older one ``{code: count}`` -- read with an empty key. Only
    code-shaped codes, key-shaped keys and positive counts are kept, so
    nothing but a code, a field key or a number reaches the page.
    """
    if isinstance(value, Mapping):
        return [(code, "", n) for code, n in _codes(value)]
    if not isinstance(value, (list, tuple)):
        return []
    entries: list[tuple[str, str, int]] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        code, key, n = entry.get("code"), entry.get("key"), _count(entry.get("count"))
        if not (isinstance(code, str) and _CODE_RE.match(code) and n):
            continue
        entries.append((code, key if isinstance(key, str) and _CODE_RE.match(key) else "", n))
    return entries[:MAX_WARNING_ENTRIES]


def doc_fields_status(rc_status: Any, *, capability: Capability,
                      schedule: str = "nightly", throughput: str = "normal") -> dict | None:
    """The RemoteController ``doc_fields`` block as status-bar lines.

    None for a RemoteController without the block (older, or unreachable).
    Keys, codes and counts only (H7: "nicht \u00fcbernommen" when no echo came
    back, never "gespeichert"). ``requeue`` lists the "Erneut senden"
    offers: ``not_accepted`` once Knovas offers fields again, ``refused``
    and ``reupload_failed`` whenever there are any -- and only when this
    RemoteController can requeue at all.
    """
    if not isinstance(rc_status, Mapping):
        return None
    block = rc_status.get("doc_fields")
    if not isinstance(block, Mapping):
        return None
    documents = block.get("documents") if isinstance(block.get("documents"), Mapping) else {}
    last = block.get("last_cycle") if isinstance(block.get("last_cycle"), Mapping) else {}
    document_sync = rc_status.get("document_sync")
    document_sync = document_sync if isinstance(document_sync, Mapping) else {}
    per_cycle = reupload_bound(block.get("per_cycle"), throughput)
    lines: list[dict[str, str]] = []

    def say(text: str, level: str = "info") -> None:
        lines.append({"text": text, "level": level})

    if block.get("enabled") is False:
        say("Dokumentfelder sind im Knovas Connector ausgeschaltet (RC_DOC_FIELDS=off); "
            "es werden keine Felder gesendet.")
    server = block.get("server")
    if server == "accepted":
        say("Knovas \u00fcbernimmt Dokumentfelder.")
    elif server == "not_accepted":
        say("Knovas hat gesendete Felder nicht \u00fcbernommen.", "warn")
    elif server == "unknown":
        say("Noch keine R\u00fcckmeldung von Knovas zu Dokumentfeldern.")
    with_fields = _count(documents.get("with_fields"))
    if with_fields:
        say(f"{with_fields} Dokumente mit Feldern gesendet.")
    not_accepted = _count(documents.get("not_accepted"))
    if not_accepted:
        say(f"Felder bei {not_accepted} Uploads nicht \u00fcbernommen: Funktion bei Knovas aus.",
            "warn")
    refused = _count(documents.get("refused"))
    if refused:
        say(f"Felder bei {refused} Uploads abgelehnt; die Dokumente sind ohne diese Felder "
            "indexiert.", "warn")
    refusals = _codes(last.get("refused"))
    if refusals:
        say("Abgelehnt im letzten Durchlauf: "
            + ", ".join(f"{code} {n}\u00d7" for code, n in refusals) + ".", "warn")
    warnings = _warning_entries(block.get("warnings"))
    if warnings:
        # "invalid_value 3x (amount)" per code and field key, then each
        # code's meaning once (spec F4).
        say("Hinweise von Knovas: " + ", ".join(
            f"{code} {n}\u00d7" + (f" ({key})" if key else "") for code, key, n in warnings) + ".")
        say("Bedeutung der Codes \u2013 " + "; ".join(
            f"{code}: {warning_text(code)}"
            for code in dict.fromkeys(code for code, _, _ in warnings)) + ".")
    unknown = [k for k in block.get("unknown_keys") or () if isinstance(k, str) and _CODE_RE.match(k)]
    if unknown:
        suggest = block.get("suggest") if isinstance(block.get("suggest"), Mapping) else {}
        parts = []
        for key in unknown[:20]:
            options = [s for s in suggest.get(key) or () if isinstance(s, str) and _CODE_RE.match(s)]
            parts.append(f"{key} (Vorschlag: {', '.join(options[:3])})" if options else key)
        say("Bei Knovas unbekannte Feldschl\u00fcssel: " + ", ".join(parts) + ".", "warn")
    fields_changed = _count(document_sync.get("fields_changed"))
    if fields_changed:
        say(f"{fields_changed} Dokumente mit ge\u00e4nderten Feldeinstellungen erkannt.")
    pending = _count(documents.get("pending_reupload"))
    if pending:
        say(f"{pending} Dokumente warten auf erneutes Senden ({per_cycle} pro Durchlauf, "
            f"{reupload_eta(pending, per_cycle, schedule, throughput)}).")
    collisions = _count(last.get("rel_collisions"))
    if collisions:
        say(f"{collisions} Dateien liegen unter gleichem relativem Pfad in mehreren Ordnern; "
            "ihre Felder bestimmt der erste Ordner.", "warn")
    failed = _count(documents.get("reupload_failed"))
    if failed:
        say(f"{failed} Dokumente nach wiederholten Fehlern nicht erneut gesendet.", "warn")
    skipped = _count((block.get("template_errors") or {}).get("field_template_invalid")
                     if isinstance(block.get("template_errors"), Mapping) else 0)
    if skipped:
        say(f"{skipped}\u00d7 Ordner wegen ung\u00fcltiger Pfadvorlage \u00fcbersprungen.", "warn")

    requeue: list[dict[str, str]] = []
    if CAP_FIELDS_REQUEUE in capabilities_from_status(rc_status):
        if not_accepted and capability.shows_values:
            requeue.append({"outcome": "not_accepted", "label": "Nicht \u00fcbernommene erneut senden"})
        if refused:
            requeue.append({"outcome": "refused", "label": "Abgelehnte erneut senden"})
        if failed:
            requeue.append({"outcome": "reupload_failed",
                            "label": "Fehlgeschlagene erneut senden"})
    return {"lines": lines, "requeue": requeue}


# ---------------------------------------------------------------------------
# Re-extraction after an extractor upgrade (spec L6)
# ---------------------------------------------------------------------------
#
# Counts only: the Knovas Connector reports how many documents an older
# extraction produced; no path, title or text reaches this page or a log.

#: The Knovas Connector's default re-extraction bound (RC_REEXTRACT_PER_CYCLE),
#: used when its status does not report ``per_cycle``.
DEFAULT_REEXTRACT_PER_CYCLE = 100
REEXTRACT_AUDIT_ACTION = "ingestion.reextract_requeued"
REEXTRACT_UNCONFIRMED = "Bitte best\u00e4tigen, dass die Dokumente neu extrahiert werden."
REEXTRACT_CHANGED = ("Seit der Anzeige sind weitere Dokumente mit \u00e4lterer Extraktion "
                     "dazugekommen; bitte die neue Zahl best\u00e4tigen.")
REEXTRACT_TOO_OLD = ("Knovas Connector zu alt \u2013 bitte aktualisieren: er kann Dokumente "
                     "noch nicht neu extrahieren.")
REEXTRACT_UNREACHABLE = ("Knovas Connector nicht erreichbar \u2013 bitte sp\u00e4ter erneut "
                         "versuchen.")


def extraction_block(rc_status: Any) -> Mapping[str, Any] | None:
    """The Knovas Connector's ``extraction`` block when it counts outdated
    documents (one that can re-extract); None for an older or unreachable
    one."""
    if not isinstance(rc_status, Mapping):
        return None
    block = rc_status.get("extraction")
    if not isinstance(block, Mapping) or "outdated" not in block:
        return None
    return block


def reextract_status(rc_status: Any, *, throughput: str = "normal",
                     is_admin: bool = False) -> dict[str, Any] | None:
    """The tab's re-extraction section: ``N Dokumente mit \u00e4lterer
    Extraktion``, how many already wait, the per-cycle bound as the
    throughput preset lets it through, and whether to offer *Neu
    extrahieren* (admins only). None without the Connector's counts."""
    block = extraction_block(rc_status)
    if block is None:
        return None
    outdated = _count(block.get("outdated"))
    per_cycle = _count(block.get("per_cycle")) or DEFAULT_REEXTRACT_PER_CYCLE
    return {
        "outdated": outdated,
        "queued": _count(block.get("queued")),
        "per_cycle": reupload_bound(per_cycle, throughput),
        "can_request": bool(is_admin) and outdated > 0,
        "admin_only": not is_admin and outdated > 0,
        "confirm": None,
    }


def reextract_text(count: Any, per_cycle: Any, schedule: str, throughput: str) -> str:
    """The cost sentence *Neu extrahieren* is confirmed against, computed
    like a field change's (``reupload_text``). Only documents whose upload
    changes are sent, so ``count`` is the upper bound of billed uploads."""
    count = _count(count)
    bound = reupload_bound(_count(per_cycle) or DEFAULT_REEXTRACT_PER_CYCLE, throughput)
    eta = reupload_eta(count, bound, schedule, throughput)
    return (f"{count} Dokumente wurden mit einer \u00e4lteren Extraktion indexiert. Der Knovas "
            "Connector liest sie neu und sendet jedes, dessen Text, Seitenzahlen oder Felder "
            "sich ge\u00e4ndert haben, erneut an Knovas \u2013 je ein verrechneter Upload, "
            f"h\u00f6chstens {count}. Bei {bound} pro Durchlauf und Zeitplan "
            f"\u201e{_schedule_label(schedule)}\u201c {eta}.")


def _confirmed_count(raw: Any) -> int | None:
    """The count a confirmation carries (``confirm_reextract``); None
    without one. ASCII digits only, at most 18 of them (``isdigit`` alone
    admits a superscript two, which ``int`` refuses, and ``int`` refuses a
    number thousands of digits long): the field holds the number the dialog
    showed."""
    text = str(raw or "").strip()
    if not (text.isascii() and text.isdigit()) or len(text) > 18:
        return None
    return int(text)


def _reextract_error(exc: Exception) -> str:
    """What the page says when the Knovas Connector refused or could not
    be asked: 404 is an older Connector, no status an unreachable one."""
    status = getattr(exc, "status", None)
    if status == 404:
        return REEXTRACT_TOO_OLD
    if status is None:
        return REEXTRACT_UNREACHABLE
    return f"Neu extrahieren fehlgeschlagen: {exc}"


def template_preview(templates: Sequence[str], entries: Sequence[Any], *,
                     recursive: bool = True, limit: int = PREVIEW_FILES_PER_SOURCE) -> dict:
    """Template captures over the files RemoteController reported.

    ``entries`` is a ``/discover`` answer's list, paths relative to the
    folder -- the same ``rel`` RemoteController matches (spec 3.4). A
    template that does not compile makes RemoteController skip the whole
    folder, so the preview then captures nothing. Shown to the
    administrator only; nothing here is logged.
    """
    compiled = []
    errors: list[dict[str, Any]] = []
    for index, template in enumerate(templates or (), 1):
        try:
            compiled.append(compile_template(template))
        except TemplateError as exc:
            errors.append({"index": index, "code": exc.code,
                           "text": TEMPLATE_ERROR_TEXT.get(exc.code, exc.code)})
    if errors:
        compiled = []
    rows: list[dict[str, Any]] = []
    files = matched = 0
    for entry in entries or ():
        if not isinstance(entry, Mapping) or entry.get("type") != "file":
            continue
        rel = str(entry.get("path") or "")
        if not rel or (not recursive and "/" in rel.replace("\\", "/")):
            continue
        files += 1
        index, found = first_match(rel, compiled)
        if index is not None:
            matched += 1
        if len(rows) < limit:
            rows.append({
                "path": rel,
                "template": None if index is None else index + 1,
                "captures": [{"key": k, "value": v} for k, v in found.items()],
            })
    return {"rows": rows, "files": files, "matched": matched, "errors": errors,
            "truncated": files > len(rows)}


@dataclass(frozen=True)
class FieldCheck:
    """What saving ``profile`` means for document fields.

    ``profile`` has enum labels replaced by codes; ``changed`` names the
    sources RemoteController will re-send; ``warnings`` and ``notes`` are
    shown with the save; ``error`` is set instead of raising when the
    caller asked for a non-blocking check (the preview).
    """

    profile: IngestionProfile
    changed: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    error: str | None = None


def _field_configs(profile: IngestionProfile | None) -> dict[str, tuple]:
    if profile is None:
        return {}
    return {s.path: s.field_config() for s in profile.sources if s.has_fields}


def _readable_registry(client: Any, user_key: Any) -> list | None:
    """The tenant registry as this person sees it, or None when Knovas does
    not offer document fields (or the registry cannot be read)."""
    try:
        capability = doc_fields_capability.capability_for(client)
    except Exception as exc:  # noqa: BLE001 - unknown reads as unavailable
        logger.warning("Dokumentfelder-Faehigkeit nicht ermittelbar: %s", type(exc).__name__)
        return None
    if not capability.shows_values:
        return None
    try:
        return doc_fields_capability.registry_for(client, user_key)
    except Exception as exc:  # noqa: BLE001 - surfaced as "not checkable"
        doc_fields_capability.observe_exception(exc)
        logger.warning("Feldregister nicht lesbar: %s", type(exc).__name__)
        return None


def _rule_conflicts(profile: IngestionProfile, client: Any,
                    registry: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[str]]:
    """Warnings for keys set per folder and by a folder rule, plus a note
    when the rules cannot be read (they need the Knovas admin group)."""
    if not upload_field_keys(profile):
        return [], []
    try:
        rules = client.doc_field_rules()
    except Exception as exc:  # noqa: BLE001 - the check is advisory
        doc_fields_capability.observe_exception(exc)
        if getattr(exc, "status", None) == 403:
            return [], [RULES_FORBIDDEN]
        return [], [RULES_UNAVAILABLE]
    warnings = []
    for key, count in folder_rule_conflicts(profile, rules).items():
        label = field_label(registry, key)
        name = f"\u201e{label}\u201c ({key})" if label != key else f"\u201e{key}\u201c"
        rules_text = "1 Regel" if count == 1 else f"{count} Regeln"
        warnings.append(
            f"Feld {name} ist pro Ordner gesetzt und zugleich als Ordnervorgabe "
            f"({rules_text}): wo der Ordner einen Wert liefert, hat er Vorrang vor "
            "der Ordnervorgabe.")
    return warnings, []


def check_profile_fields(profile: IngestionProfile, current: IngestionProfile | None, *,
                         rc_client: Any, knovas_client: Any, user_key: Any,
                         strict: bool = True) -> FieldCheck:
    """Everything a save checks about document fields (spec 4.8).

    For a profile that uses fields: RemoteController must advertise them;
    keys are validated against the registry when Knovas offers it -- and a
    *changed* field configuration is refused when it cannot be checked; a
    key also set by a folder rule is warned about. For every profile: which
    existing sources change their fields (``changed``), so the caller can
    ask for the re-upload confirmation. ``strict=False`` returns the
    problem in ``error`` instead of raising ProfileError.
    """
    warnings: list[str] = []
    notes: list[str] = []
    try:
        if profile_uses_fields(profile):
            needed = required_capabilities(compile_profile(profile).sync_request)
            available = _advertised(rc_client)
            if available is None:
                raise ProfileError(RC_UNREACHABLE)
            if needed - available:
                raise ProfileError(RC_TOO_OLD)
            registry = _readable_registry(knovas_client, user_key)
            if registry is None:
                if _field_configs(profile) != _field_configs(current):
                    raise ProfileError(UNVERIFIABLE)
                notes.append(NOT_RECHECKED)
            else:
                profile = validate_profile_fields(profile, registry)
                warnings, notes = _rule_conflicts(profile, knovas_client, registry)
    except ProfileError as exc:
        if strict:
            raise
        return FieldCheck(profile, tuple(field_config_changes(current, profile)),
                          error=str(exc))
    return FieldCheck(profile, tuple(field_config_changes(current, profile)),
                      tuple(warnings), tuple(notes))


def _reupload_note(check: FieldCheck, current: Any = None) -> dict[str, int]:
    """For the approval payload: how many folders the change re-sends, so a
    second person confirms the cost too (a count, never a path), and the
    version that count was taken against (``base_version``). Absent when
    nothing is re-sent, which keeps every other payload as it was."""
    if not check.changed:
        return {}
    return {"reupload_folders": len(check.changed),
            "base_version": int(getattr(current, "version", 0) or 0)}


STALE_REUPLOAD = (
    "Das Profil wurde seit dieser Anfrage ge\u00e4ndert, und die \u00dcbernahme w\u00fcrde "
    "Ordner erneut senden, die bei der Anfrage niemand best\u00e4tigt hat. Bitte die "
    "\u00c4nderung neu beantragen."
)


def _refuse_unconfirmed_reupload(payload: Mapping[str, Any], current: Any,
                                 profile: IngestionProfile) -> None:
    """An approved change runs against the profile current *now*. When that
    profile changed since the request (another save, an earlier approval),
    pushing the requested one can revert someone's field configuration and
    re-send whole folders -- billed uploads with OCR -- that neither the
    requester confirmed nor the approver was shown. Refused then; a change
    that re-sends nothing goes through as before."""
    if current is None:
        return
    resend = field_config_changes(current.profile, profile)
    if not resend:
        return
    base = payload.get("base_version")
    if isinstance(base, int) and not isinstance(base, bool):
        if base == current.version:
            return
    elif len(resend) <= int(payload.get("reupload_folders") or 0):
        # A request from before base_version: its count still covers it.
        return
    raise ProfileError(STALE_REUPLOAD)


def _advertised(rc_client: Any) -> frozenset[str] | None:
    """What RemoteController advertises; None when it cannot be asked, so
    an unreachable one is not reported as too old."""
    return advertised_capabilities(rc_client)


def _require_rc_support(rc_client: Any, sync_request: Mapping[str, Any]) -> None:
    """Refuse before anything is saved or sent when RemoteController would
    refuse the body's document-field keys (spec 2.5). A body without them
    needs nothing and asks nothing."""
    needed = required_capabilities(sync_request)
    if not needed:
        return
    available = _advertised(rc_client)
    if available is None:
        raise RemoteControllerError(RC_UNREACHABLE, status=None)
    if needed - available:
        raise RemoteControllerError(RC_TOO_OLD, status=None)


def profile_from_form(form: Mapping[str, str], lists: Mapping[str, list[str]]) -> IngestionProfile:
    """Build the profile the form describes. Raises ProfileError with a
    sentence a person can act on; compile_profile validates the rest."""
    schedule = str(form.get("schedule", "") or "").strip()
    throughput = str(form.get("throughput", "") or "").strip()
    if schedule not in presets.SCHEDULE_PRESETS:
        raise ProfileError("Bitte einen der angebotenen Zeitplaene waehlen.")
    if throughput not in presets.THROUGHPUT_PRESETS:
        raise ProfileError("Bitte eine der angebotenen Geschwindigkeiten waehlen.")
    sources: list[SourceFolder] = []
    for n in range(MAX_FOLDER_ROWS):
        path = str(form.get(f"folder-{n}-path", "") or "").strip()
        if not path:
            continue
        # Document fields: absent from the form while Knovas does not offer
        # them, and then simply empty (spec 4.8).
        number = len(sources) + 1
        sources.append(SourceFolder(
            path=path,
            recursive=str(form.get(f"folder-{n}-recursive", "") or "") == "1",
            access_groups=tuple(dict.fromkeys(
                g for g in (lists.get(f"folder-{n}-groups") or []) if g
            )),
            fields=parse_static_fields(form.get(f"folder-{n}-fields", ""), number),
            field_templates=parse_templates(form.get(f"folder-{n}-templates", "")),
            metadata_fields=parse_metadata_items(lists.get(f"folder-{n}-metadata"), number),
        ))
    if not sources:
        raise ProfileError("Mindestens ein Ordner muss angegeben sein.")
    age = str(form.get("max_document_age_days", "") or "").strip()
    if age and not age.isdigit():
        raise ProfileError("Bitte die Altersgrenze als ganze Zahl in Tagen angeben.")
    return IngestionProfile(
        identifier_prefix=str(form.get("identifier_prefix", "") or "").strip(),
        sources=sources,
        file_types=[t for t in (lists.get("file_types") or []) if t] or ["documents"],
        schedule=schedule,
        throughput=throughput,
        max_document_age_days=int(age) if age else None,
        description=str(form.get("description", "") or "").strip(),
        # Ohne diesen Schalter gab es keinen Weg, eine Übernahme zu wiederholen.
        # RemoteController überspringt im Normalbetrieb alles, was sein
        # Zustandsspeicher als übertragen führt -- richtig, solange beide Seiten
        # dasselbe glauben. Wurde der Bestand bei Knovas neu aufgesetzt, stehen
        # die Dateien dort weiter als "synced", der Zyklus meldet
        # "uploaded=0 errors=0" und lädt nie wieder etwas hoch. Von aussen sieht
        # das aus wie eine Übernahme, die läuft, und wie eine Suche, die nichts
        # findet.
        full_rescan=str(form.get("full_rescan", "") or "") == "1",
    )


def form_from_request(form: Mapping[str, str], lists: Mapping[str, list[str]]) -> dict[str, Any]:
    """Rebuild the template's form structure from the raw, unvalidated request.

    Used to re-render a person's own input after ``profile_from_form`` or
    ``compile_profile`` rejects it, so a validation error does not erase the
    folder rows and checkbox choices they were in the middle of fixing.
    ``dict(request.form)`` cannot do this: it keeps only the first value of a
    repeated field (``file_types``, ``folder-N-groups``), and the template
    expects ``form.folders`` as a list of rows, which the raw form never has.
    """
    folders: list[dict[str, Any]] = []
    for n in range(MAX_FOLDER_ROWS):
        path = str(form.get(f"folder-{n}-path", "") or "").strip()
        if not path:
            continue
        folders.append({
            "path": path,
            "recursive": str(form.get(f"folder-{n}-recursive", "") or "") == "1",
            "groups": [g for g in (lists.get(f"folder-{n}-groups") or []) if g],
            "fields_text": str(form.get(f"folder-{n}-fields", "") or ""),
            "templates_text": str(form.get(f"folder-{n}-templates", "") or ""),
            "metadata": [m for m in (lists.get(f"folder-{n}-metadata") or []) if m],
        })
    return {
        "identifier_prefix": str(form.get("identifier_prefix", "") or "").strip(),
        "description": str(form.get("description", "") or "").strip(),
        "schedule": str(form.get("schedule", "") or "").strip(),
        "throughput": str(form.get("throughput", "") or "").strip(),
        "file_types": [t for t in (lists.get("file_types") or []) if t],
        "max_document_age_days": str(form.get("max_document_age_days", "") or "").strip(),
        "full_rescan": str(form.get("full_rescan", "") or "") == "1",
        "folders": folders,
    }


def folders_from_discover(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Immediate child directories of one discover scan, as the tree picker
    needs them: a display name and the absolute path the profile stores.

    Files are dropped. Nested relative paths are dropped so one expand is
    always one level, even if the scan was deeper than we asked for.
    """
    root = str(payload.get("root") or "").replace("\\", "/").rstrip("/")
    folders: list[dict[str, str]] = []
    for entry in payload.get("entries") or []:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("type") != "directory":
            continue
        rel = str(entry.get("path") or "").replace("\\", "/").strip("/")
        if not rel or "/" in rel:
            continue
        name = str(entry.get("name") or "") or rel
        path = f"{root}/{rel}" if root else rel
        folders.append({"name": name, "path": path})
    return {
        "root": root,
        "folders": folders,
        "truncated": bool(payload.get("truncated")),
    }


def child_folders(rc_client, root: str | None = None) -> dict[str, Any]:
    """One level of folders under ``root`` (or the default watch root)."""
    found = rc_client.discover(root=root, max_depth=FOLDER_DISCOVER_DEPTH)
    return folders_from_discover(found)


def form_from_profile(profile: IngestionProfile | None) -> dict[str, Any]:
    if profile is None:
        return {"identifier_prefix": "", "description": "", "schedule": "nightly",
                "throughput": "normal", "file_types": ["documents"],
                "max_document_age_days": "", "full_rescan": False, "folders": []}
    return {
        "identifier_prefix": profile.identifier_prefix,
        "description": profile.description,
        "schedule": profile.schedule,
        "throughput": profile.throughput,
        "file_types": list(profile.file_types),
        "max_document_age_days": "" if profile.max_document_age_days is None else str(profile.max_document_age_days),
        "full_rescan": bool(profile.full_rescan),
        "folders": [{"path": s.path, "recursive": bool(s.recursive),
                     "groups": list(s.access_groups),
                     "fields_text": format_static_fields(s.fields),
                     "templates_text": "\n".join(s.field_templates),
                     "metadata": list(s.metadata_fields)} for s in profile.sources],
    }


def apply_profile(payload: Mapping[str, Any], actor, *, conn, rc_client,
                  requested_by: str | None = None) -> dict:
    """Save (or reuse) a version and push it. Reached through
    ``execute_ingestion_change`` both when the actor may act alone and after
    a second person confirms.

    ``requested_by`` is the id of the person who asked, carried in the
    approval payload. When it differs from ``actor`` the version records
    both: ``created_by`` the requester, ``approved_by`` the executor. The
    audit row keeps ``actor``, because the executor is who acted.

    Never returns a ``failed`` list: every failure here raises, and
    ``_execute`` in admin_approvals then leaves the request approved and
    retryable.

    The version is saved *before* the push is attempted, on purpose: a push
    that fails still leaves a current-but-unpushed row behind, and that row
    is the record of what was attempted, not a bug to route around. What
    would be a bug is inserting a second, identical row on every retry -- so
    a save whose payload matches the current version, while that version is
    still unpushed, reuses it (no insert) and goes straight to push and
    ``mark_pushed`` again, rather than saving a new one.

    A body with document-field keys is refused before the version row when
    RemoteController does not advertise them (spec 2.5): an approved change
    may run long after it was asked for, against an RemoteController that
    was downgraded in between.
    """
    profile = profile_from_json(payload["profile"])
    compiled = compile_profile(profile)
    _require_rc_support(rc_client, compiled.sync_request)
    repo = IngestionProfileRepository(conn)
    current = repo.current()
    _refuse_unconfirmed_reupload(payload, current, profile)
    if (current is not None and current.pushed_at is None
            and profile_to_json(current.profile) == payload["profile"]):
        version = current
    else:
        requester = _requester(requested_by, actor)
        version = repo.save_new_version(
            profile,
            by=actor if requester is None else requester,
            approved_by=None if requester is None else actor,
        )
    pushed = dict(rc_client.push(compiled) or {})
    applied = str(pushed.get("applied") or "stored")
    repo.mark_pushed(version.id)
    audit.record(conn, action="ingestion.profile_pushed", actor=actor,
                 target_type="ingestion_profile", target_id=f"default v{version.version}",
                 detail={"folders": len(profile.sources), "schedule": profile.schedule,
                         "applied": applied, "requested_by": requested_by,
                         # Counts only: values and templates name clients.
                         **field_config_counts(profile.sources)})
    return {"version": version.version, "applied": applied,
            "start_error": pushed.get("start_error")}


def execute_stop(actor, *, conn, rc_client) -> dict:
    """Halt the sync and say so in the audit log. One implementation, two
    call sites: the ``stop`` route's guarded execute and the executor
    registry. They drifted once already -- the registry's inline lambda
    called ``stop()`` and wrote nothing, so an approved halt appeared only
    as ``approval.executed`` and never as ``ingestion.stopped``.
    """
    rc_client.stop()
    audit.record(conn, action="ingestion.stopped", actor=actor,
                 target_type="remote_controller", target_id="sync", detail={})
    return {"stopped": True}


def execute_ingestion_change(payload: Mapping[str, Any], actor, *, conn, rc_client) -> dict:
    """The executor registered for ``ingestion_profile_change``.

    One named function rather than a conditional lambda in the registry,
    because both branches have to audit and only one of them used to.
    """
    if payload.get("action") == "stop":
        return execute_stop(actor, conn=conn, rc_client=rc_client)
    if not (set(getattr(actor, "roles", ()) or ()) & EXECUTOR_ROLES):
        # Refuse here, before save_new_version: a pure approver would
        # otherwise leave a current-but-unpushed row behind and learn about
        # the role gap from RemoteController's 403.
        raise RemoteControllerError(
            "Die Ausfuehrung braucht die Rolle admin oder ingestion_manager; ein "
            "reiner Pruefer kann das Profil nicht an den Knovas Connector uebertragen.",
            status=None,
        )
    return apply_profile(payload, actor, conn=conn, rc_client=rc_client,
                         requested_by=payload.get("requested_by"))


def attach_ingestion_routes(bp, gate, *, csrf_valid, csrf_token, page_context,
                            client_factory, rc_client_factory, require_ingestion,
                            require_admin):
    def _csrf_ok() -> bool:
        return csrf_valid(str(request.form.get("csrf_token", "") or ""))

    def _approvals() -> ApprovalService:
        return ApprovalService(gate.connection(), gate.users())

    def _lists() -> dict[str, list[str]]:
        return {key: request.form.getlist(key) for key in request.form.keys()}

    def _reupload_confirmed() -> bool:
        return str(request.form.get("confirm_reupload", "") or "") == "1"

    def _check_fields(profile: IngestionProfile, *, strict: bool = True) -> FieldCheck:
        current = IngestionProfileRepository(gate.connection()).current()
        me = gate.current_user()
        return check_profile_fields(
            profile, current.profile if current else None,
            rc_client=rc_client_factory(), knovas_client=client_factory(),
            user_key=getattr(me, "id", None), strict=strict)

    def _doc_fields_context(form, rc_status, current, *, reupload_paths, restore_version,
                            warnings, notes, rc_reachable=True) -> dict[str, Any]:
        """What the template needs for document fields. The inputs show while
        Knovas offers fields, and also whenever the form already carries
        some -- hiding them then would drop them on the next save."""
        client = client_factory()
        try:
            capability = doc_fields_capability.capability_for(client)
        except Exception as exc:  # noqa: BLE001 - unknown reads as off
            logger.warning("Dokumentfelder-Faehigkeit nicht ermittelbar: %s", type(exc).__name__)
            capability = Capability.unknown
        registry = None
        if capability.shows_values:
            registry = _readable_registry(client, getattr(gate.current_user(), "id", None))
        folders = [f for f in (form or {}).get("folders") or () if isinstance(f, Mapping)]
        configured = any(f.get("fields_text") or f.get("templates_text") or f.get("metadata")
                         for f in folders)
        running = current.profile if current else None
        schedule = str((form or {}).get("schedule") or "nightly")
        throughput = str((form or {}).get("throughput") or "normal")
        return {
            "inputs": capability.shows_values or configured,
            "available": capability.shows_values,
            "registry": [f for f in registry or () if f.get("status") != "deprecated"],
            "metadata_items": [{"key": k, "label": METADATA_LABELS[k]} for k in METADATA_ITEMS],
            # None: RemoteController could not be asked -- never reported as
            # too old (it may well support fields).
            "rc_supports": (CAP_SOURCE_FIELDS in capabilities_from_status(rc_status)
                            if rc_reachable else None),
            "status": doc_fields_status(
                rc_status, capability=capability,
                schedule=running.schedule if running else "nightly",
                throughput=running.throughput if running else "normal"),
            "reupload": reupload_info(reupload_paths or (), rc_status, schedule, throughput),
            "restore_version": restore_version,
            "warnings": list(warnings or ()),
            "notes": list(notes or ()),
            "multi_source": len(folders) > 1,
            "advice": FOLDER_RULE_ADVICE,
        }

    def _reextract_context(rc_status, current, *, confirm: bool) -> dict[str, Any] | None:
        """The re-extraction section (spec L6); with ``confirm`` also the
        dialog that states count, cost and duration, bound to that count."""
        running = current.profile if current else None
        schedule = running.schedule if running else "nightly"
        throughput = running.throughput if running else "normal"
        roles = set(getattr(gate.current_user(), "roles", ()) or ())
        section = reextract_status(rc_status, throughput=throughput, is_admin="admin" in roles)
        if section is not None and confirm and section["can_request"]:
            section["confirm"] = {
                "count": section["outdated"],
                "text": reextract_text(section["outdated"], section["per_cycle"],
                                       schedule, throughput),
            }
        return section

    def _page(form=None, *, error=None, notice=None, status=200, preview=None, support_json=None,
              reupload_paths=None, restore_version=None, field_warnings=(), field_notes=(),
              reextract_confirm=False):
        repo = IngestionProfileRepository(gate.connection())
        current = repo.current()
        rc_status: dict[str, Any] = {}
        rc_reachable = True
        try:
            rc_status = rc_client_factory().status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Knovas-Connector-Status nicht abrufbar: %s", exc)
            rc_status = {"scheduler_state": "unbekannt"}
            rc_reachable = False
        groups = []
        try:
            groups = client_factory().access_groups()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Zugriffsgruppen nicht abrufbar: %s", exc)
        form = form if form is not None else form_from_profile(current.profile if current else None)
        return render_template(
            "admin_ingestion.html",
            active_nav="admin",
            **page_context(),
            form=form,
            schedules=_labelled(presets.SCHEDULE_PRESETS),
            throughputs=_labelled(presets.THROUGHPUT_PRESETS),
            file_types=_labelled(presets.FILE_TYPE_PRESETS),
            groups=groups,
            status=rc_status,
            current=current,
            versions=repo.versions(),
            preview=preview,
            support_json=support_json,
            doc_fields=_doc_fields_context(
                form, rc_status, current, reupload_paths=reupload_paths,
                restore_version=restore_version, warnings=field_warnings, notes=field_notes,
                rc_reachable=rc_reachable),
            reextract=_reextract_context(rc_status, current, confirm=reextract_confirm),
            me=gate.current_user(),
            error=error,
            notice=notice,
            csrf_token=csrf_token(),
        ), status

    def _queued_notice(req) -> str:
        return (f"Zur Freigabe eingereicht (Nr. {str(req.id)[:8]}). Das Profil wird erst "
                "nach Bestaetigung durch eine zweite Person uebernommen.")

    @bp.route("/ingestion")
    @require_ingestion
    def ingestion():
        return _page()

    @bp.route("/ingestion/folders")
    @require_ingestion
    def folders():
        root = str(request.args.get("root") or "").strip() or None
        try:
            payload = child_folders(rc_client_factory(), root)
        except (RemoteControllerError, PermissionError) as exc:
            return jsonify({"root": root or "", "folders": [], "error": str(exc)}), 502
        return jsonify(payload)

    @bp.route("/ingestion/preview", methods=["POST"])
    @require_ingestion
    def preview():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        try:
            profile = profile_from_form(request.form, _lists())
            compile_profile(profile)
        except ProfileError as exc:
            return _page(form_from_request(request.form, _lists()), error=str(exc), status=400)
        # Document fields are checked without blocking: the preview is where
        # a person finds out what a save would refuse or cost.
        check = _check_fields(profile, strict=False)
        summary = []
        rc = rc_client_factory()
        for source in profile.sources:
            try:
                found = rc.discover(root=source.path, max_depth=PREVIEW_SCAN_DEPTH)
                entries = found.get("entries") or []
                summary.append({
                    "path": source.path,
                    "files": sum(1 for e in entries if e.get("type") == "file"),
                    "folders": sum(1 for e in entries if e.get("type") == "directory"),
                    "truncated": bool(found.get("truncated")),
                    "error": None,
                    # Captures over the files just listed: shown on this
                    # page only, never in the support JSON or a log.
                    "templates": (template_preview(source.field_templates, entries,
                                                   recursive=source.recursive)
                                  if source.field_templates else None),
                })
            except (RemoteControllerError, PermissionError) as exc:
                summary.append({"path": source.path, "files": None, "folders": None,
                                "truncated": False, "error": str(exc), "templates": None})
        return _page(form_from_profile(check.profile), preview=summary,
                     support_json=redact_for_support(check.profile),
                     notice="Vorschau erstellt. Noch nichts gespeichert.",
                     error=check.error, reupload_paths=check.changed,
                     field_warnings=check.warnings, field_notes=check.notes)

    @bp.route("/ingestion/save", methods=["POST"])
    @require_ingestion
    def save():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        try:
            profile = profile_from_form(request.form, _lists())
            compile_profile(profile)
            check = _check_fields(profile)
        except ProfileError as exc:
            return _page(form_from_request(request.form, _lists()), error=str(exc), status=400)
        if check.changed and not _reupload_confirmed():
            return _page(form_from_request(request.form, _lists()), error=REUPLOAD_UNCONFIRMED,
                         status=400, reupload_paths=check.changed,
                         field_warnings=check.warnings, field_notes=check.notes)
        profile, extra = check.profile, {"field_warnings": check.warnings,
                                         "field_notes": check.notes}
        me = gate.current_user()
        # requested_by rides in the payload so an approved change records
        # the person who asked as the version author, not the approver who
        # clicks. The direct-execute path ignores it (it is the same person).
        base = IngestionProfileRepository(gate.connection()).current()
        payload = {"profile": profile_to_json(profile), "requested_by": str(me.id),
                   **_reupload_note(check, base)}
        try:
            outcome = run_guarded(
                _approvals(), me, kind=KIND, target_ref="ingestion_profile:default",
                payload=payload,
                execute=lambda: execute_ingestion_change(payload, me, conn=gate.connection(),
                                                         rc_client=rc_client_factory()),
            )
        except (RemoteControllerError, PermissionError) as exc:
            return _page(form_from_profile(profile),
                         error=f"Der Knovas Connector hat das Profil nicht uebernommen: {exc}", status=502)
        if outcome.queued:
            return _page(form_from_profile(profile), notice=_queued_notice(outcome.request), **extra)
        return _page(notice=(f"Profil gespeichert und uebertragen "
                             f"(Version {outcome.result['version']})"
                             f"{_applied_clause(outcome.result)}"), **extra)

    @bp.route("/ingestion/restore/<int:version>", methods=["POST"])
    @require_ingestion
    def restore(version):
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        repo = IngestionProfileRepository(gate.connection())
        old = next((v for v in repo.versions() if v.version == version), None)
        if old is None:
            return _page(error=f"Version {version} gibt es nicht.", status=404)
        try:
            check = _check_fields(old.profile)
        except ProfileError as exc:
            return _page(error=f"Wiederherstellen nicht m\u00f6glich: {exc}", status=400)
        if check.changed and not _reupload_confirmed():
            return _page(error=REUPLOAD_UNCONFIRMED, status=400, reupload_paths=check.changed,
                         restore_version=version)
        me = gate.current_user()
        payload = {"profile": profile_to_json(check.profile), "requested_by": str(me.id),
                   **_reupload_note(check, repo.current())}
        try:
            outcome = run_guarded(
                _approvals(), me, kind=KIND, target_ref=f"ingestion_profile:default@v{version}",
                payload=payload,
                execute=lambda: execute_ingestion_change(payload, me, conn=gate.connection(),
                                                         rc_client=rc_client_factory()),
            )
        except (RemoteControllerError, PermissionError) as exc:
            return _page(error=f"Wiederherstellen fehlgeschlagen: {exc}", status=502)
        if outcome.queued:
            return _page(notice=_queued_notice(outcome.request))
        return _page(notice=(f"Version {version} wiederhergestellt als Version "
                             f"{outcome.result['version']}"
                             f"{_applied_clause(outcome.result)}"))

    @bp.route("/ingestion/start", methods=["POST"])
    @require_ingestion
    def start():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        me = gate.current_user()
        try:
            rc_client_factory().start()
        except (RemoteControllerError, PermissionError) as exc:
            return _page(error=f"Start fehlgeschlagen: {exc}", status=502)
        audit.record(gate.connection(), action="ingestion.started", actor=me,
                     target_type="remote_controller", target_id="sync", detail={})
        return _page(notice="Abgleich gestartet.")

    @bp.route("/ingestion/stop", methods=["POST"])
    @require_ingestion
    def stop():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        me = gate.current_user()
        try:
            outcome = run_guarded(
                _approvals(), me, kind=KIND, target_ref="remote_controller:stop",
                payload={"action": "stop"},
                execute=lambda: execute_stop(me, conn=gate.connection(),
                                             rc_client=rc_client_factory()),
            )
        except (RemoteControllerError, PermissionError) as exc:
            return _page(error=f"Stopp fehlgeschlagen: {exc}", status=502)
        if outcome.queued:
            return _page(notice=_queued_notice(outcome.request))
        return _page(notice="Abgleich angehalten.")

    @bp.route("/ingestion/doc-fields/requeue", methods=["POST"])
    @require_ingestion
    def requeue_doc_fields():
        """The "Erneut senden" buttons: RemoteController queues the documents
        with that field outcome for re-upload, within its per-cycle bound.
        Each is a billed upload, so the request is audited (outcome and count
        only)."""
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        outcome = str(request.form.get("outcome", "") or "")
        if outcome not in REQUEUE_OUTCOMES:
            return _page(error="Unbekannte Auswahl.", status=400)
        me = gate.current_user()
        try:
            count = rc_client_factory().requeue_doc_fields(outcome)
        except (RemoteControllerError, PermissionError) as exc:
            return _page(error=f"Erneut senden fehlgeschlagen: {exc}", status=502)
        audit.record(gate.connection(), actor=me, **requeue_audit(outcome, count))
        if not count:
            # Nothing the RemoteController's scan still reaches matched: say
            # so instead of promising a re-send (spec 3.7).
            return _page(notice="Keine Dokumente zum erneuten Senden vorgemerkt.")
        return _page(notice=(f"{count} Dokumente zum erneuten Senden vorgemerkt; "
                             "der Knovas Connector sendet sie in den n\u00e4chsten Durchl\u00e4ufen, "
                             "je ein verrechneter Upload."))

    @bp.route("/ingestion/reextract", methods=["POST"])
    @require_admin
    def reextract():
        """*Neu extrahieren* (spec L6): the Knovas Connector queues every
        document an older extraction produced, re-extracts them within its
        per-cycle bound and uploads those whose text changed -- each a billed
        upload. Admin only; the first click shows count, cost and duration,
        the second carries the count it showed. Audited with counts only."""
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        rc = rc_client_factory()
        try:
            block = extraction_block(rc.status())
        except (RemoteControllerError, PermissionError) as exc:
            return _page(error=_reextract_error(exc), status=502)
        if block is None:
            return _page(error=REEXTRACT_TOO_OLD, status=400)
        outdated = _count(block.get("outdated"))
        if not outdated:
            return _page(notice="Keine Dokumente mit \u00e4lterer Extraktion.")
        confirmed = _confirmed_count(request.form.get("confirm_reextract"))
        if confirmed is None:
            return _page(error=REEXTRACT_UNCONFIRMED, status=400, reextract_confirm=True)
        if outdated > confirmed:
            return _page(error=REEXTRACT_CHANGED, status=400, reextract_confirm=True)
        try:
            answer = rc.requeue_reextract()
        except (RemoteControllerError, PermissionError) as exc:
            return _page(error=_reextract_error(exc), status=502)
        count = _count((answer or {}).get("requeued"))
        audit.record(gate.connection(), action=REEXTRACT_AUDIT_ACTION, actor=gate.current_user(),
                     target_type="remote_controller", target_id="sync",
                     detail={"outdated": outdated, "requeued": count})
        if not count:
            return _page(notice="Keine Dokumente zum Neu-Extrahieren vorgemerkt.")
        return _page(notice=(f"{count} Dokumente zum Neu-Extrahieren vorgemerkt; der Knovas "
                             "Connector liest sie in den n\u00e4chsten Durchl\u00e4ufen neu und "
                             "sendet die ge\u00e4nderten erneut \u2013 je ein verrechneter Upload."))

    @bp.route("/ingestion/template-preview", methods=["POST"])
    @require_ingestion
    def template_preview_json():
        """The live template preview of one folder row, as JSON.

        A POST with the folder and its templates in the body -- never in the
        URL, which access logs keep -- answered with the captures over the
        files RemoteController lists there, computed by the same code that
        checks the golden vectors. Nothing of it is logged.
        """
        csrf_ok = csrf_valid(str(request.headers.get("X-CSRF-Token", "") or ""))
        if not csrf_ok:
            return jsonify({"error": "Formular ist abgelaufen. Bitte die Seite neu laden."}), 400
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        path = body.get("path")
        templates = body.get("templates")
        if (not isinstance(path, str) or not path.strip() or not isinstance(templates, list)
                or len(templates) > MAX_FIELD_TEMPLATES
                or any(not isinstance(t, str) or len(t) > MAX_TEMPLATE_CHARS
                       for t in templates)):
            return jsonify({"error": "Ordner und h\u00f6chstens 8 Pfadvorlagen angeben."}), 400
        try:
            found = rc_client_factory().discover(root=path.strip(), max_depth=PREVIEW_SCAN_DEPTH)
        except (RemoteControllerError, PermissionError) as exc:
            return jsonify({"error": str(exc)}), 502
        result = template_preview([t.strip() for t in templates if t.strip()],
                                  found.get("entries") or [],
                                  recursive=body.get("recursive", True) is not False)
        result["scan_truncated"] = bool(found.get("truncated"))
        return jsonify(result)

    return bp
