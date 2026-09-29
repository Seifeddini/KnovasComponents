"""The experiments service: every use case of the module, Flask-free.

Each public method of ``ExperimentService`` does the same four things in the
same order, so none of them can be forgotten:

1. Permission: ``can_view`` first (anyone else gets NotFound -- the module
   does not exist for them), ``can_manage`` for configuration and deletion.
2. Validation, with German messages per field; nothing half-valid reaches
   the store.
3. The change, in one transaction, taking the experiment's row lock first
   (``FOR UPDATE`` for its own state, ``FOR NO KEY UPDATE`` for child rows),
   plus its follow-up work in the same transaction: the debounced Knovas
   re-index and, after new measurements, the evaluation pipeline.
4. An audit entry after the commit (``identity.audit.record``), holding ids,
   keys and counts only -- never a body, a value, code or a token.

The worker entry points at the bottom (``execute_evaluation``,
``run_pipeline_job``, ``on_evaluation_dead``) run the same logic without a
person.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md (section 8)
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import ipaddress
import json
import logging
import math
import re
import secrets
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

import yaml

from experiments import (
    csv_import, evaluators, indexer, kinds, labels, packs, permissions, schema, stats, store, tasks,
)
from experiments import search as search_mod
from experiments.errors import Conflict, Forbidden, NotFound, Unavailable, ValidationError
from experiments.jobs import JobQueue, RetryLater
from experiments.settings import USER_PREF_SHOW_IN_SEARCH

logger = logging.getLogger(__name__)

# -- messages -----------------------------------------------------------------

MSG_MANAGE_ONLY = "Nur f\u00fcr Verantwortliche der Experimente."
MSG_CHANGED = "Das Experiment wurde inzwischen ge\u00e4ndert. Bitte neu laden."
MSG_NO_RUNNER = "Die Rechenumgebung ist nicht eingerichtet."
MSG_RUNNER_DOWN = "Die Rechenumgebung ist nicht erreichbar."
MSG_RUNNER_GONE_30 = "Die Rechenumgebung war 30 Minuten nicht erreichbar."
MSG_EVALUATION_DEAD = "Die Auswertung konnte nicht ausgef\u00fchrt werden."
MSG_EVALUATION_FAILED = "Die Auswertung ist fehlgeschlagen."
MSG_METRIC_GONE = "Die Metrik dieser Auswertung gibt es nicht mehr."
MSG_PIPELINE_BUSY = "Die Auswertungen dieses Experiments laufen gerade; neuer Versuch folgt."
MSG_DECIDE_BY_FORM = "Bitte die Entscheidung \u00fcber das Formular festhalten."
MSG_NO_DECISION_HERE = "Eine Entscheidung ist in diesem Status nicht vorgesehen."
MSG_STOP_REASON = "Bitte einen Grund f\u00fcr den Abbruch angeben."
MSG_LEARNING_REQUIRED = "Bitte die Erkenntnis festhalten."
MSG_KIND_LOCKED = "Die Art l\u00e4sst sich nicht mehr \u00e4ndern, es gibt schon Messwerte."
MSG_MISSING_GROUP = (
    "Ihnen fehlt die Knovas-Zugriffsgruppe f\u00fcr Experimente; gezeigt werden Datenbanktreffer."
)
MSG_KNOVAS_DOWN = "Die Knovas-Suche ist gerade nicht verf\u00fcgbar; gezeigt werden Datenbanktreffer."
MSG_ROW_VERSION = "Bitte die gelesene Version (row_version) mitsenden."
MSG_BODY = "Die Anfrage muss ein JSON-Objekt sein."
MSG_BUILTIN_LOCKED = "Eingebaute Auswerter lassen sich nicht \u00e4ndern."
MSG_NOTE_DELETE = (
    "Diese Notiz d\u00fcrfen nur ihre Verfasserin oder ihr Verfasser und Verantwortliche l\u00f6schen."
)

# -- limits -------------------------------------------------------------------

MAX_TITLE = 300
MAX_HYPOTHESIS = 20_000
MAX_DESCRIPTION = 50_000
MAX_TAGS = 20
MAX_TAG_CHARS = 50
MAX_NOTE = 50_000
MAX_COMMENT = 5_000
MAX_DECISION_TEXT = 20_000
MAX_RUN_JSON_BYTES = 16 * 1024
MAX_EVALUATOR_CODE = 200_000
MAX_CONFIG_DESCRIPTION = 2_000
MAX_SEARCH_WORDS = 8
MAX_QUERY_CHARS = 200
#: Values beyond this magnitude are refused (csv_import uses the same bound):
#: sums over many rows must stay far from the float8 limit.
MAX_ABS_VALUE = csv_import.MAX_ABS_VALUE
MAX_ABS_SUM_SQ = 1e30
PIPELINE_DELAY_SECONDS = 30
UNINDEX_DELAY_SECONDS = 330
PIPELINE_KEEP = 10
RUNNER_TEST_MAX_SECONDS = 60
#: Sample-size planning: largest spread / effect ratio (sd / mde, or the
#: proportions' standard deviation / mde) and largest answer per variant.
MAX_SAMPLE_EFFECT_RATIO = math.sqrt(5e7)
MAX_SAMPLE_PER_VARIANT = 10 ** 9
MAX_SAMPLE_COMPARISONS = 50
LABEL_MDE = "Kleinster relevanter Unterschied"
MSG_SAMPLE_TOO_LARGE = (
    "Die n\u00f6tige Stichprobe w\u00e4re unrealistisch gross; bitte einen gr\u00f6sseren "
    "Unterschied w\u00e4hlen."
)
RUNNER_GIVE_UP = _dt.timedelta(minutes=30)
TOKEN_DEFAULT_DAYS = 90
REINDEX_CHUNK = 100
#: Measurement rows go into COPY in chunks of this many, validated as they
#: go: a CSV import of max_csv_rows never holds all of them as tuples.
COPY_CHUNK_ROWS = 10_000

DOMAIN_KEY_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
ID_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
TYPE_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")
METRIC_KEY_RE = re.compile(schema.METRIC_KEY_PATTERN)
EVALUATOR_KEY_RE = re.compile(schema.EVALUATOR_KEY_PATTERN)
VARIANT_KEY_RE = re.compile(schema.VARIANT_KEY_PATTERN)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

SOURCES = ("manual", "api", "csv")
TRIGGERS = ("manual", "pipeline", "api")
RUN_STATUSES = ("finished", "failed", "cancelled")
USER_NOTE_KINDS = tuple(k for k in labels.NOTE_KIND_LABELS if k != "status")
_ROW_KEYS = frozenset({"metric", "variant", "value", "count", "denominator", "sum_sq",
                       "observed_at", "dims", "run_id"})
_RUN_METRIC_KEYS = frozenset({"value", "count", "denominator", "sum_sq"})

#: German names of the audit actions the activity list shows.
ACTIVITY_LABELS = {
    "experiments.experiment.create": "Experiment angelegt",
    "experiments.experiment.update": "Angaben ge\u00e4ndert",
    "experiments.experiment.transition": "Status gewechselt",
    "experiments.experiment.variants": "Varianten ge\u00e4ndert",
    "experiments.experiment.metrics": "Metriken ge\u00e4ndert",
    "experiments.experiment.decide": "Entscheidung festgehalten",
    "experiments.experiment.reindex": "Neu indexieren angestossen",
    "experiments.experiment.delete": "Experiment gel\u00f6scht",
    "experiments.measurements.add": "Messwerte erfasst",
    "experiments.measurements.import": "CSV-Datei importiert",
    "experiments.batch.delete": "Messwerte entfernt",
    "experiments.run.add": "Lauf erfasst",
    "experiments.note.add": "Notiz erfasst",
    "experiments.note.delete": "Notiz gel\u00f6scht",
    "experiments.evaluation.run": "Auswertung gestartet",
    "experiments.pipeline.run": "Auswertungen des Typs ausgef\u00fchrt",
}
SYSTEM_ACTOR = "System"


# -- input helpers ------------------------------------------------------------


def _obj(data: Any) -> Dict[str, Any]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValidationError(MSG_BODY)
    return data


def _refuse(field: str, message: str, label: Optional[str] = None) -> ValidationError:
    shown = f"\u00ab{label}\u00bb: {message}" if label else message
    return ValidationError(shown, fields={field: message})


def _choice(raw: Any, field: str, allowed: Any, message: str, label: Optional[str] = None) -> str:
    """``raw`` when it is one of ``allowed`` (a dict or a tuple of codes).
    Only text qualifies: a list or an object from a JSON body cannot be
    looked up in a dict (it is unhashable) and must be refused, not crash."""
    if not isinstance(raw, str) or raw not in allowed:
        raise _refuse(field, message, label)
    return raw


def _clean_text(raw: Any, field: str, *, limit: int, multiline: bool, label: str) -> str:
    if not isinstance(raw, str):
        raise _refuse(field, "Muss Text sein.", label)
    value = raw.strip()
    if "\x00" in value:
        raise _refuse(field, "Enth\u00e4lt ein Nullzeichen.", label)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _refuse(field, "Enth\u00e4lt ung\u00fcltige Zeichen.", label) from None
    if not multiline and _CONTROL_RE.search(value):
        raise _refuse(field, "Keine Zeilenumbr\u00fcche oder Steuerzeichen.", label)
    if len(value) > limit:
        raise _refuse(field, f"H\u00f6chstens {kinds.format_plain(limit)} Zeichen.", label)
    return value


def _text(data: Dict[str, Any], field: str, *, limit: int, label: str, required: bool = False,
          multiline: bool = False, default: str = "") -> str:
    raw = data.get(field)
    if raw is None:
        if required:
            raise _refuse(field, "Pflichtangabe fehlt.", label)
        return default
    value = _clean_text(raw, field, limit=limit, multiline=multiline, label=label)
    if required and not value:
        raise _refuse(field, "Darf nicht leer sein.", label)
    return value


def _flag(data: Dict[str, Any], field: str, label: str) -> bool:
    value = data.get(field)
    if not isinstance(value, bool):
        raise _refuse(field, "Muss true oder false sein.", label)
    return value


def _number(raw: Any) -> Optional[float]:
    """A finite number from JSON or a query string (decimal comma allowed)."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            value = float(raw)
        except OverflowError:
            return None
        return value if math.isfinite(value) else None
    if isinstance(raw, str):
        return csv_import.parse_number(raw) if raw.strip() else None
    return None


def _int_value(raw: Any, field: str, *, lo: int, hi: int, label: str,
               default: Optional[int] = None) -> int:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if default is not None:
            return default
        raise _refuse(field, "Pflichtangabe fehlt.", label)
    value = _number(raw)
    if value is None or not value.is_integer():
        raise _refuse(field, "Muss eine ganze Zahl sein.", label)
    if not lo <= value <= hi:
        raise _refuse(field, f"Erlaubt ist {lo} bis {hi}.", label)
    return int(value)


def _truthy(raw: Any) -> bool:
    """A flag from JSON or a query string: "0", "false", "nein" and "" are
    false (``bool("0")`` would be true)."""
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "ja", "on")
    return bool(raw)


def _limit(raw: Any, default: int, hi: int) -> int:
    value = _number(raw)
    if value is None:
        return default
    return max(1, min(hi, int(value)))


def _id(value: Any, message: str = "Nicht gefunden.") -> str:
    ident = store.canonical_uuid(value)
    if ident is None:
        raise NotFound(message)
    return ident


def _key(value: Any) -> str:
    if not isinstance(value, str) or not store.KEY_RE.fullmatch(value.strip()):
        raise NotFound()
    return value.strip()


def _tags(raw: Any) -> List[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _refuse("tags", "Muss eine Liste sein.", "Schlagw\u00f6rter")
    out: List[str] = []
    for item in raw:
        tag = " ".join(_clean_text(item, "tags", limit=MAX_TAG_CHARS, multiline=False,
                                   label="Schlagw\u00f6rter").split())
        if tag and tag not in out:
            out.append(tag)
    if len(out) > MAX_TAGS:
        raise _refuse("tags", f"H\u00f6chstens {MAX_TAGS} Schlagw\u00f6rter.", "Schlagw\u00f6rter")
    return out


def _instant(raw: Any, field: str, label: Optional[str]) -> Optional[_dt.datetime]:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    moment = csv_import.parse_instant(raw) if isinstance(raw, str) and len(raw) <= 64 else None
    if moment is None:
        raise _refuse(field, "Kein g\u00fcltiger Zeitpunkt (ISO 8601 oder TT.MM.JJJJ).", label)
    return moment


def _params(raw: Any) -> Any:
    """Evaluator params as plain JSON (no NaN, NUL or broken characters)
    before the evaluator's own schema looks at them."""
    if raw is None:
        return None
    return schema.checked_json(raw, "Die Parameter sind ung\u00fcltig.", max_nodes=2_000,
                               max_chars=64_000)


def _json_object(raw: Any, field: str, label: str, max_bytes: int) -> Dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _refuse(field, "Muss ein JSON-Objekt sein.", label)
    value = schema.checked_json(raw, f"\u00ab{label}\u00bb ist ung\u00fcltig.",
                                max_nodes=5_000, max_chars=max_bytes * 2)
    if schema.json_size(value) > max_bytes:
        raise _refuse(field, f"H\u00f6chstens {max_bytes // 1024} KB.", label)
    return value


def _words(q: Any) -> List[str]:
    if q is None:
        return []
    if not isinstance(q, str):
        raise _refuse("q", "Muss Text sein.", "Suche")
    text = _clean_text(q[:MAX_QUERY_CHARS] if len(q) > MAX_QUERY_CHARS else q, "q",
                       limit=MAX_QUERY_CHARS, multiline=True, label="Suche")
    words: List[str] = []
    for word in text.split():
        if word not in words:
            words.append(word[:100])
    return words[:MAX_SEARCH_WORDS]


def _filename(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL_RE.sub("", name).strip()
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        name = name.encode("utf-8", "replace").decode("utf-8")
    return name[:255] or None


# -- audit and follow-up work -------------------------------------------------


def _audit(conn: Any, action: str, *, actor: Any, target_type: Optional[str],
           target_id: Optional[str], detail: Optional[Dict[str, Any]] = None,
           request_meta: Optional[Dict[str, Any]] = None) -> None:
    """One audit entry, after the change committed. The values that come from
    the request are cleaned first so the insert cannot fail on them."""
    from identity import audit

    meta = request_meta or {}
    detail = dict(detail or {})
    if meta.get("token_id"):
        detail["token_id"] = str(meta["token_id"])
    ip = meta.get("ip")
    try:
        ip = str(ipaddress.ip_address(str(ip).strip())) if ip else None
    except ValueError:
        ip = None
    agent = meta.get("user_agent")
    agent = _CONTROL_RE.sub("", str(agent))[:500] if agent else None
    audit.record(conn, action=action, actor=actor, target_type=target_type, target_id=target_id,
                 detail=json.loads(json.dumps(detail, default=str)), ip=ip, user_agent=agent)


def _queue_index(conn: Any, settings: Any, experiment_id: str, *, priority: int = 10,
                 delay_seconds: Optional[float] = None) -> bool:
    """Re-upload the experiment to Knovas after its content changed
    (debounced); with indexing switched off record 'off' instead.

    The experiment row is written before the job: every transaction takes
    the experiment's row lock before the job's dedupe slot, so two writers
    queueing the same experiment can never wait for each other in a circle.
    """
    if settings.index_enabled:
        store.set_index_state(conn, experiment_id, "pending")
        delay = settings.index_debounce_seconds if delay_seconds is None else delay_seconds
        JobQueue(conn).enqueue("index", {"experiment_id": experiment_id},
                               dedupe_key=f"index:{experiment_id}", delay_seconds=delay,
                               priority=priority)
        return True
    store.set_index_state(conn, experiment_id, "off")
    return False


def _queue_index_many(conn: Any, settings: Any, experiment_ids: Sequence[str], *,
                      priority: int = 200, delay_seconds: Optional[float] = None) -> int:
    n = 0
    # One global order (by id), so two bulk re-queues cannot deadlock.
    for experiment_id in sorted(set(experiment_ids)):
        if _queue_index(conn, settings, experiment_id, priority=priority,
                        delay_seconds=delay_seconds):
            n += 1
    return n


def _queue_pipeline(conn: Any, experiment_id: str) -> None:
    JobQueue(conn).enqueue("pipeline", {"experiment_id": experiment_id},
                           dedupe_key=f"pipeline:{experiment_id}",
                           delay_seconds=PIPELINE_DELAY_SECONDS)


@contextlib.contextmanager
def _read_view(conn: Any) -> Iterator[None]:
    """Reads that must agree with each other (a snapshot is a dozen queries)
    run in one read-only REPEATABLE READ transaction when the connection is
    free; inside a caller's transaction they simply join it."""
    try:
        from psycopg import pq

        idle = conn.info.transaction_status == pq.TransactionStatus.IDLE
    except Exception:  # noqa: BLE001 - not a psycopg connection
        idle = False
    if not idle:
        yield
        return
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        yield


def _evaluator_by_key(conn: Any, key: Any, *, with_code: bool = False) -> Optional[Dict[str, Any]]:
    found = store.get_evaluator(conn, key=key, with_code=with_code)
    if found is None and isinstance(key, str) and key in evaluators.BUILTINS:
        # Normally registered at start; a fresh database may not have them yet.
        store.ensure_builtin_evaluators(conn)
        found = store.get_evaluator(conn, key=key, with_code=with_code)
    return found


def _public(item: Dict[str, Any], *drop: str) -> Dict[str, Any]:
    return {k: v for k, v in item.items() if k not in drop}


#: Top-level order of a type definition shown as YAML (schema's keys; the
#: first three only in case a definition ever carries them). Other keys follow
#: in their stored order.
DEFINITION_YAML_ORDER = ("key", "name", "description", "fields", "states", "initial",
                         "transitions", "variants", "metrics", "evaluation", "decision")


class _DefinitionDumper(yaml.SafeDumper):
    """Block style without anchors (parse_definition_text refuses aliases),
    list items indented under their key as in the pack files."""

    def ignore_aliases(self, data: Any) -> bool:
        return True

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> Any:
        return super().increase_indent(flow, False)


def _represent_text(dumper: yaml.SafeDumper, value: str) -> Any:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_DefinitionDumper.add_representer(str, _represent_text)


def definition_yaml(definition: Dict[str, Any]) -> str:
    """A type definition as YAML for the type editor, in a natural order
    (DEFINITION_YAML_ORDER on top). The stored JSONB has lost the order the
    entries were written in; when normalising reproduces the stored content
    exactly, its order (key, label, ... within an entry) is used, else the
    stored one. schema.parse_definition_text reads the text back as the
    stored definition."""
    ordered = definition
    try:
        normalised = schema.validate_type_definition(definition)
    except ValidationError:
        normalised = None
    if normalised == definition:
        ordered = normalised
    top = {k: ordered[k] for k in DEFINITION_YAML_ORDER if k in ordered}
    top.update((k, v) for k, v in ordered.items() if k not in top)
    return yaml.dump(top, Dumper=_DefinitionDumper, sort_keys=False, allow_unicode=True,
                     default_flow_style=False, width=4096)


def _variant_fields(variant: Dict[str, Any]) -> tuple:
    """What saving a variant list can change about one variant (its place
    in the list aside), to recognise a save that changes nothing."""
    return (variant["key"], variant.get("name") or "", variant.get("description") or "",
            bool(variant.get("is_control")), variant.get("allocation"))


# -- evaluations (shared by the service and the worker) --------------------------


def _metric_for_input(conn: Any, snapshot: Dict[str, Any], metric_id: str) -> Optional[Dict[str, Any]]:
    """The snapshot's entry for the metric, or -- when it is no longer assigned
    -- the metric itself without a role."""
    for metric in snapshot.get("metrics") or []:
        if metric["id"] == metric_id:
            return metric
    found = store.get_metric(conn, metric_id)
    if found is None:
        return None
    entry = {k: found[k] for k in ("id", "key", "name", "kind", "kind_label", "unit", "direction",
                                   "direction_label", "definition")}
    entry.update({"role": None, "role_label": None, "guardrail_op": None, "guardrail_value": None})
    return entry


def _needs_rows(evaluator: Dict[str, Any]) -> bool:
    return bool(evaluator.get("needs_rows")) or not evaluator.get("builtin")


MSG_TARGET_PROPORTION = "F\u00fcr Anteile das Ziel als Bruch angeben (0.8 f\u00fcr 80 %)."


def _check_target(evaluator: Dict[str, Any], metric: Dict[str, Any], params: Dict[str, Any]) -> None:
    """builtin.describe's optional ``target`` must be a value the metric can
    take: a proportion as a fraction 0..1 (80 % is 0.8, not 80), a bounded
    metric within its minimum and maximum. Anything else gives a confident
    verdict against an impossible goal, and that verdict becomes the
    experiment's latest result in the list, the search and Knovas."""
    if evaluator.get("key") != "builtin.describe" or not isinstance(params, dict):
        return
    target = _number(params.get("target"))
    if target is None:
        return
    if metric.get("kind") == "proportion":
        if not 0.0 <= target <= 1.0:
            raise _refuse("params.target", MSG_TARGET_PROPORTION, "Ziel")
        return
    if metric.get("kind") not in kinds.BOUNDED_KINDS:
        return
    bounds = metric.get("definition") or {}
    lo, hi = _number(bounds.get("min")), _number(bounds.get("max"))
    if (lo is not None and target < lo) or (hi is not None and target > hi):
        shown = (f"{kinds.format_plain(lo) if lo is not None else kinds.DASH}\u2013"
                 f"{kinds.format_plain(hi) if hi is not None else kinds.DASH}")
        raise _refuse("params.target", f"Das Ziel liegt ausserhalb des Wertebereichs der Metrik "
                                       f"({shown}).", "Ziel")


def _prepare_input(conn: Any, snapshot: Dict[str, Any], metric: Dict[str, Any],
                   evaluator: Dict[str, Any], params: Dict[str, Any],
                   scope: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """(evaluator input without rows, input digest).

    The digest covers everything the evaluator sees: the input contract
    (experiment, metric, variants, the complete aggregates, params, scope),
    the evaluator version, and -- for evaluators that read rows -- the rows'
    fingerprint (count and highest id; rows are insert-only). Equal digests
    mean the evaluation would give the same result.
    """
    aggregates = store.aggregates(conn, snapshot["id"], metric["id"], kind=metric["kind"],
                                  scope=scope)
    data = evaluators.build_input(snapshot=snapshot, metric=metric, aggregates=aggregates,
                                  rows=None, rows_truncated=False, params=params, scope=scope)
    fingerprint = None
    if _needs_rows(evaluator):
        fingerprint = list(store.rows_fingerprint(conn, snapshot["id"], metric["id"], scope))
    digest_source = {"input": data, "rows": fingerprint, "evaluator": evaluator["key"],
                     "version": evaluator["current_version"]}
    digest = hashlib.sha256(json.dumps(digest_source, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    return data, digest


def _with_rows(conn: Any, settings: Any, data: Dict[str, Any], snapshot: Dict[str, Any],
               metric: Dict[str, Any], scope: Dict[str, Any]) -> Dict[str, Any]:
    rows, truncated = store.evaluator_rows(conn, snapshot["id"], metric["id"],
                                           settings.evaluator_max_rows, scope=scope)
    data = dict(data)
    data["rows"] = rows
    data["rows_truncated"] = truncated
    return data


def _run_builtin(conn: Any, settings: Any, snapshot: Dict[str, Any], metric: Dict[str, Any],
                 evaluator: Dict[str, Any], params: Dict[str, Any], scope: Dict[str, Any],
                 data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    if evaluator.get("needs_rows"):
        data = _with_rows(conn, settings, data, snapshot, metric, scope)
    started = time.monotonic()
    output = evaluators.run_builtin(evaluator["key"], data)
    return output, int((time.monotonic() - started) * 1000)


def _start_evaluation(conn: Any, settings: Any, runner: Any, snapshot: Dict[str, Any],
                      metric: Dict[str, Any], evaluator: Dict[str, Any], params: Dict[str, Any],
                      scope: Dict[str, Any], *, trigger: str, requested_by: Optional[str],
                      reuse: bool, check_health: bool) -> Tuple[Optional[str], bool, Optional[str]]:
    """Run a built-in now or queue a custom evaluator.

    Returns (evaluation id, whether a new evaluation was created, warning).
    With ``reuse`` an evaluation on identical input is returned instead of a
    new one.
    """
    data, digest = _prepare_input(conn, snapshot, metric, evaluator, params, scope)
    if reuse:
        existing = store.find_reusable_evaluation(
            conn, experiment_id=snapshot["id"], evaluator_id=evaluator["id"],
            version=evaluator["current_version"], metric_id=metric["id"], params=params,
            scope=scope, input_digest=digest)
        if existing is not None:
            return existing, False, None
    if evaluator["builtin"]:
        output, duration = _run_builtin(conn, settings, snapshot, metric, evaluator, params, scope, data)
        with conn.transaction():
            if not store.lock_experiment_for_child(conn, snapshot["id"]):
                raise NotFound()  # deleted while the evaluation ran
            evaluation_id = store.insert_evaluation(
                conn, experiment_id=snapshot["id"], evaluator_id=evaluator["id"],
                evaluator_version=evaluator["current_version"], metric_id=metric["id"],
                params=params, scope=scope, trigger=trigger, status="done", output=output,
                input_digest=digest, requested_by=requested_by, duration_ms=duration)
            store.touch_experiment(conn, snapshot["id"])
            _queue_index(conn, settings, snapshot["id"])
        return evaluation_id, True, None
    if runner is None:
        if not check_health:
            return None, False, MSG_NO_RUNNER
        raise Unavailable(MSG_NO_RUNNER)
    if check_health:
        health = runner.health() or {}
        if not health.get("ok"):
            raise Unavailable(MSG_RUNNER_DOWN)
    with conn.transaction():
        if not store.lock_experiment_for_child(conn, snapshot["id"]):
            raise NotFound()
        evaluation_id = store.insert_evaluation(
            conn, experiment_id=snapshot["id"], evaluator_id=evaluator["id"],
            evaluator_version=evaluator["current_version"], metric_id=metric["id"], params=params,
            scope=scope, trigger=trigger, status="queued", input_digest=digest,
            requested_by=requested_by)
        JobQueue(conn).enqueue("evaluate", {"evaluation_id": evaluation_id},
                               dedupe_key=f"evaluate:{evaluation_id}",
                               priority=100 if trigger == "pipeline" else 10)
    return evaluation_id, True, None


def _skipped(evaluator_key: str, metric_key: Optional[str], message: str) -> Dict[str, Any]:
    return {"evaluator_key": evaluator_key, "metric_key": metric_key, "status": "skipped",
            "warning": message}


def _pipeline(conn: Any, settings: Any, runner: Any, snapshot: Dict[str, Any],
              definition: Dict[str, Any], scope_override: Optional[Dict[str, Any]], *,
              trigger: str, requested_by: Optional[str]) -> Tuple[List[Dict[str, Any]], int]:
    """Run the type's evaluation list: describe first, built-ins now, custom
    evaluators queued, unchanged input reused. Returns (entries, created)."""
    entries = list(definition.get("evaluation") or [])
    entries.sort(key=lambda e: e.get("evaluator") != "builtin.describe")
    metrics = snapshot.get("metrics") or []
    by_key = {m["key"]: m for m in metrics}
    primary = next((m for m in metrics if m.get("role") == "primary"), None)
    results: List[Dict[str, Any]] = []
    seen: set = set()
    created = 0
    for entry in entries:
        key = entry.get("evaluator")
        evaluator = _evaluator_by_key(conn, key)
        if evaluator is None or evaluator["archived"]:
            results.append(_skipped(key, None, f"Den Auswerter \u00ab{key}\u00bb gibt es nicht."))
            continue
        target = entry.get("metric")
        if target == "primary":
            if primary is None:
                results.append(_skipped(key, None, "Es ist keine prim\u00e4re Metrik festgelegt."))
                continue
            targets = [primary]
        elif target == "all":
            targets = [m for m in metrics if m["kind"] in evaluator["input_kinds"]]
        elif target in by_key:
            targets = [by_key[target]]
        else:
            results.append(_skipped(key, target,
                                    f"Die Metrik \u00ab{target}\u00bb ist diesem Experiment nicht "
                                    "zugeordnet."))
            continue
        for metric in targets:
            if metric["kind"] not in evaluator["input_kinds"]:
                results.append(_skipped(key, metric["key"], _kind_message(evaluator, metric)))
                continue
            try:
                params = evaluators.validate_params(evaluator["params_schema"],
                                                    _params(entry.get("params")))
                _check_target(evaluator, metric, params)
                scope = scope_override if scope_override is not None else \
                    schema.validate_scope(entry.get("scope") or {})
            except ValidationError as exc:
                results.append(_skipped(key, metric["key"], exc.message))
                continue
            evaluation_id, new, warning = _start_evaluation(
                conn, settings, runner, snapshot, metric, evaluator, params, scope,
                trigger=trigger, requested_by=requested_by, reuse=True, check_health=False)
            if warning:
                results.append(_skipped(key, metric["key"], warning))
                continue
            created += 1 if new else 0
            if evaluation_id in seen:
                continue
            seen.add(evaluation_id)
            found = store.get_evaluation(conn, snapshot["id"], evaluation_id)
            if found is not None:
                results.append(found)
    return results, created


def _kind_message(evaluator: Dict[str, Any], metric: Dict[str, Any]) -> str:
    label = kinds.KINDS[metric["kind"]].label if metric["kind"] in kinds.KINDS else metric["kind"]
    return (f"Der Auswerter \u00ab{evaluator['name']}\u00bb nimmt keine Metriken der Art "
            f"\u00ab{label}\u00bb.")


def execute_evaluation(conn: Any, evaluation_id: str, *, settings: Any, runner: Any) -> None:
    """Run one queued custom evaluation in the runner (the 'evaluate' job).

    The input is read in one short transaction, the runner is called outside
    any transaction (it may take minutes), the result is written in a new one.
    A runner that cannot be reached defers the job; 30 minutes after the
    first attempt the evaluation fails for good.
    """
    record = store.get_evaluation_record(conn, evaluation_id)
    if record is None or record["status"] in ("done", "failed"):
        return
    builtin = record["language"] == "builtin"
    if runner is None and not builtin:
        with conn.transaction():
            store.mark_evaluation(conn, record["id"], status="failed", error=MSG_NO_RUNNER)
            _queue_index(conn, settings, record["experiment_id"])
        return
    if record["metric_id"] is None:
        store.mark_evaluation(conn, record["id"], status="failed", error=MSG_METRIC_GONE)
        return
    with _read_view(conn):
        snapshot = store.load_snapshot(conn, record["experiment_id"])
        metric = _metric_for_input(conn, snapshot, record["metric_id"]) if snapshot else None
        data = None
        if snapshot is not None and metric is not None:
            aggregates = store.aggregates(conn, snapshot["id"], metric["id"], kind=metric["kind"],
                                          scope=record["scope"])
            rows, truncated = store.evaluator_rows(conn, snapshot["id"], metric["id"],
                                                   settings.evaluator_max_rows, scope=record["scope"])
            data = evaluators.build_input(snapshot=snapshot, metric=metric, aggregates=aggregates,
                                          rows=rows, rows_truncated=truncated,
                                          params=record["params"], scope=record["scope"])
    if snapshot is None:
        return
    if data is None:
        store.mark_evaluation(conn, record["id"], status="failed", error=MSG_METRIC_GONE)
        return
    store.mark_evaluation(conn, record["id"], status="running")
    try:
        if builtin:
            # Built-ins are trusted code of this process; the sandbox does not
            # know them.
            started = time.monotonic()
            result = {"ok": True, "output": evaluators.run_builtin(record["evaluator_key"], data),
                      "logs": "", "duration_ms": int((time.monotonic() - started) * 1000)}
        else:
            result = runner.run(language=record["language"], code=record["code"], data=data,
                                timeout_seconds=settings.runner_timeout_seconds)
    except ValidationError as exc:
        # A built-in refusing the stored params (its schema changed since).
        with conn.transaction():
            store.mark_evaluation(conn, record["id"], status="failed", error=exc.message)
            _queue_index(conn, settings, record["experiment_id"])
        return
    except Unavailable:
        # Counted from the first attempt, not from creation: an evaluation
        # that waited behind a long queue has not been failing all that time,
        # and one refusal (the runner answers 503 while its slots are busy)
        # must not end it for good.
        waited = store.seconds_since_first_attempt(conn, record["id"])
        if waited is not None and waited > RUNNER_GIVE_UP.total_seconds():
            with conn.transaction():
                store.mark_evaluation(conn, record["id"], status="failed", error=MSG_RUNNER_GONE_30)
                _queue_index(conn, settings, record["experiment_id"])
            return
        store.mark_evaluation(conn, record["id"], status="queued")
        raise RetryLater(60, MSG_RUNNER_DOWN) from None
    logs = result.get("logs") if isinstance(result, dict) else None
    duration = result.get("duration_ms") if isinstance(result, dict) else None
    with conn.transaction():
        if isinstance(result, dict) and result.get("ok"):
            try:
                output = evaluators.sanitize_output(result.get("output"),
                                                    evaluator_name=record["evaluator_name"],
                                                    metric_kind=metric["kind"])
            except ValidationError as exc:
                store.mark_evaluation(conn, record["id"], status="failed", error=exc.message,
                                      logs=logs, duration_ms=duration)
            else:
                store.mark_evaluation(conn, record["id"], status="done", output=output,
                                      logs=logs, duration_ms=duration)
        else:
            error = (result or {}).get("error") if isinstance(result, dict) else None
            store.mark_evaluation(conn, record["id"], status="failed",
                                  error=error or MSG_EVALUATION_FAILED, logs=logs,
                                  duration_ms=duration)
        _queue_index(conn, settings, record["experiment_id"])


def run_pipeline_job(conn: Any, experiment_id: str, *, settings: Any, runner: Any) -> None:
    """The 'pipeline' job: run_pipeline without a person, then keep only the
    newest 10 done automatic (pipeline and CI/API) evaluations per (evaluator,
    metric, params, scope).

    One pipeline per experiment at a time: a second worker that picks up the
    next pipeline job while the first still runs waits for it instead of
    computing the same evaluations twice.
    """
    lock = f"experiments.pipeline:{experiment_id}"
    if not store.try_advisory_lock(conn, lock):
        raise RetryLater(PIPELINE_DELAY_SECONDS, MSG_PIPELINE_BUSY)
    try:
        with _read_view(conn):
            loaded = store.load_snapshot_with_definition(conn, str(experiment_id))
        if loaded is None:
            return
        snapshot, definition = loaded
        results, created = _pipeline(conn, settings, runner, snapshot, definition, None,
                                     trigger="pipeline", requested_by=None)
        removed = store.prune_pipeline_evaluations(conn, snapshot["id"], keep=PIPELINE_KEEP)
    finally:
        store.advisory_unlock(conn, lock)
    if created:
        _audit(conn, "experiments.pipeline.run", actor=None, target_type="experiment",
               target_id=snapshot["key"],
               detail={"trigger": "pipeline", "created": created,
                       "evaluations": len([r for r in results if r.get("id")]), "pruned": removed})


def on_evaluation_dead(conn: Any, evaluation_id: str) -> None:
    """The evaluate job gave up: the evaluation must not stay 'queued'."""
    record = store.get_evaluation_record(conn, evaluation_id)
    if record is None or record["status"] in ("done", "failed"):
        return
    store.mark_evaluation(conn, record["id"], status="failed", error=MSG_EVALUATION_DEAD)


# -- the service --------------------------------------------------------------


class ExperimentService:
    """The experiments use cases for one person (``actor``) on one connection."""

    def __init__(self, conn: Any, actor: Any, settings: Any, *, runner: Any = None,
                 knovas_search: Optional[Callable[[str, int], Dict[str, Any]]] = None,
                 request_meta: Optional[Dict[str, Any]] = None) -> None:
        self.conn = conn
        self.actor = actor
        self.settings = settings
        self.runner = runner
        self.knovas_search = knovas_search
        self.request_meta = dict(request_meta or {})

    # -- plumbing ----------------------------------------------------------

    @property
    def _actor_id(self) -> Optional[str]:
        return str(self.actor.id) if self.actor is not None else None

    @property
    def _roles(self) -> frozenset:
        return frozenset(getattr(self.actor, "roles", None) or ())

    def _view(self) -> None:
        if not permissions.can_view(self.actor):
            raise NotFound()

    def _manage(self) -> None:
        self._view()
        if not permissions.can_manage(self.actor):
            raise Forbidden(MSG_MANAGE_ONLY)

    def _audit(self, action: str, target_type: Optional[str], target_id: Optional[str],
               detail: Optional[Dict[str, Any]] = None) -> None:
        _audit(self.conn, action, actor=self.actor, target_type=target_type, target_id=target_id,
               detail=detail, request_meta=self.request_meta)

    def _experiment(self, key: Any, *, lock: Optional[str] = None) -> Dict[str, Any]:
        row = store.get_experiment_row(self.conn, _key(key), lock=lock)
        if row is None:
            raise NotFound()
        return row

    def _snapshot(self, key: str) -> Dict[str, Any]:
        with _read_view(self.conn):
            snapshot = store.load_snapshot(self.conn, key, actor=self.actor)
        if snapshot is None:
            raise NotFound()
        return snapshot

    @staticmethod
    def _check_row_version(row: Dict[str, Any], data: Dict[str, Any], *, required: bool) -> None:
        if "row_version" not in data or data["row_version"] is None:
            if required:
                raise _refuse("row_version", MSG_ROW_VERSION)
            return
        sent = _number(data["row_version"])
        if sent is None or not sent.is_integer():
            raise _refuse("row_version", "Muss eine ganze Zahl sein.")
        if int(sent) != int(row["row_version"]):
            raise Conflict(MSG_CHANGED)

    def _domain_arg(self, raw: Any, *, allow_none: bool) -> Optional[Dict[str, Any]]:
        if raw is None or raw == "":
            if allow_none:
                return None
            raise _refuse("domain", "Pflichtangabe fehlt.", "Bereich")
        domain = store.get_domain(self.conn, raw) if isinstance(raw, str) else None
        if domain is None:
            raise _refuse("domain", "Den Bereich gibt es nicht.", "Bereich")
        return domain

    # -- domains -----------------------------------------------------------

    def list_domains(self, include_archived: bool = False) -> List[Dict[str, Any]]:
        self._view()
        return store.list_domains(self.conn, include_archived=_truthy(include_archived))

    def create_domain(self, data: Any) -> Dict[str, Any]:
        self._manage()
        data = _obj(data)
        key = _text(data, "key", limit=32, label="Schl\u00fcssel", required=True)
        if not DOMAIN_KEY_RE.match(key):
            raise _refuse("key", "Kleinbuchstaben, Ziffern und -, beginnend mit einem Buchstaben; "
                                 "2 bis 32 Zeichen.", "Schl\u00fcssel")
        name = _text(data, "name", limit=80, label="Name", required=True)
        prefix = _text(data, "id_prefix", limit=8, label="K\u00fcrzel", required=True).upper()
        if not ID_PREFIX_RE.match(prefix):
            raise _refuse("id_prefix", "Grossbuchstaben und Ziffern, beginnend mit einem Buchstaben; "
                                       "2 bis 8 Zeichen (z. B. MKT).", "K\u00fcrzel")
        color = _text(data, "color", limit=7, label="Farbe", default=packs.DEFAULT_COLOR) \
            or packs.DEFAULT_COLOR
        if not COLOR_RE.match(color):
            raise _refuse("color", "Eine Farbe im Format #RRGGBB.", "Farbe")
        description = _text(data, "description", limit=MAX_CONFIG_DESCRIPTION,
                            label="Beschreibung", multiline=True)
        domain_id = store.insert_domain(self.conn, key=key, name=name, id_prefix=prefix,
                                        color=color, description=description, pack=None,
                                        actor_id=self._actor_id)
        self._audit("experiments.domain.create", "exp_domain", key,
                    {"domain_id": domain_id, "id_prefix": prefix})
        return store.get_domain(self.conn, key)

    def update_domain(self, key: Any, data: Any) -> Dict[str, Any]:
        self._manage()
        data = _obj(data)
        changes: Dict[str, Any] = {}
        with self.conn.transaction():
            domain = store.get_domain(self.conn, key) if isinstance(key, str) else None
            if domain is None:
                raise NotFound("Den Bereich gibt es nicht.")
            if "name" in data:
                name = _text(data, "name", limit=80, label="Name", required=True)
                if name != domain["name"]:
                    changes["name"] = name
            if "color" in data:
                color = _text(data, "color", limit=7, label="Farbe", required=True)
                if not COLOR_RE.match(color):
                    raise _refuse("color", "Eine Farbe im Format #RRGGBB.", "Farbe")
                if color != domain["color"]:
                    changes["color"] = color
            if "description" in data:
                description = _text(data, "description", limit=MAX_CONFIG_DESCRIPTION,
                                    label="Beschreibung", multiline=True)
                if description != domain["description"]:
                    changes["description"] = description
            if "archived" in data:
                archived = _flag(data, "archived", "Archiviert")
                if archived != domain["archived"]:
                    changes["archived"] = archived
            store.update_domain(self.conn, domain["id"], changes)
            requeued = 0
            if "name" in changes:
                ids = store.experiments_for_reindex(self.conn, domain_id=domain["id"])
                requeued = _queue_index_many(self.conn, self.settings, ids, priority=200)
        if changes:
            self._audit("experiments.domain.update", "exp_domain", domain["key"],
                        {"changed": sorted(changes), "requeued": requeued})
        return store.get_domain(self.conn, domain["key"])

    # -- values added to selection fields -------------------------------------

    def _selection_fields(self, domain: Dict[str, Any],
                          extra_definitions: Sequence[Dict[str, Any]] = ()) -> Dict[str, Dict[str, Any]]:
        """field key -> {label, options, extensible} over the selection fields of
        the types usable in the domain (current versions) and ``extra_definitions``
        (the version an experiment was created with). A field is extensible when
        any type with that key lets people add values."""
        definitions = [t["definition"] for t in store.list_types(self.conn, domain_id=domain["id"],
                                                                  restrict=True)]
        definitions.extend(d for d in extra_definitions if d)
        out: Dict[str, Dict[str, Any]] = {}
        for definition in definitions:
            for field in (definition or {}).get("fields") or []:
                if field.get("type") not in ("enum", "multi_enum"):
                    continue
                entry = out.setdefault(field["key"], {"label": field.get("label") or field["key"],
                                                      "options": [], "extensible": False})
                entry["extensible"] = entry["extensible"] or schema.is_extensible(field)
                for option in field.get("options") or []:
                    if option.casefold() not in {o.casefold() for o in entry["options"]}:
                        entry["options"].append(option)
        return out

    def list_field_options(self, key: Any) -> Dict[str, Any]:
        """The values added to the domain's selection fields; ``by_field`` is
        what the forms add to a field's own options."""
        self._view()
        domain = store.get_domain(self.conn, key) if isinstance(key, str) else None
        if domain is None:
            raise NotFound("Den Bereich gibt es nicht.")
        options = store.list_field_options(self.conn, domain["id"])
        fields = self._selection_fields(domain)
        for option in options:
            option["field_label"] = (fields.get(option["field"]) or {}).get("label") or option["field"]
        return {"options": options, "by_field": store.field_option_values(self.conn, domain["id"])}

    def add_field_option(self, key: Any, data: Any) -> Dict[str, Any]:
        """Add a value to a selection field of the domain, e.g. a new segment.
        Anyone who works with experiments may; the type decides whether the
        field takes new values (``extensible``)."""
        self._view()
        data = _obj(data)
        domain = store.get_domain(self.conn, key) if isinstance(key, str) else None
        if domain is None:
            raise NotFound("Den Bereich gibt es nicht.")
        if domain["archived"]:
            raise _refuse("domain", "Der Bereich ist archiviert.", "Bereich")
        extra: List[Dict[str, Any]] = []
        if data.get("experiment") not in (None, ""):
            row = self._experiment(data["experiment"])
            if row["domain_id"] != domain["id"]:
                raise _refuse("experiment", "Das Experiment geh\u00f6rt zu einem anderen Bereich.")
            extra.append(row["definition"])
        field_key = data.get("field")
        fields = self._selection_fields(domain, extra)
        field = fields.get(field_key) if isinstance(field_key, str) else None
        if field is None:
            raise _refuse("field", "Ein Auswahlfeld mit diesem Schl\u00fcssel gibt es in diesem Bereich nicht.",
                          "Feld")
        if not field["extensible"]:
            raise _refuse("value", "Die Auswahl dieses Feldes ist fest vorgegeben; neue Werte legt eine "
                                   "verantwortliche Person im Typ an.", field["label"])
        value = _text(data, "value", limit=80, label=field["label"], required=True)
        own = {o.casefold(): o for o in field["options"]}
        if value.casefold() in own:
            return {"field": field_key, "value": own[value.casefold()], "created": False}
        stored, created = store.add_field_option(self.conn, domain["id"], field_key, value,
                                                 self._actor_id)
        if created:
            self._audit("experiments.field_option.create", "exp_domain", domain["key"],
                        {"field": field_key, "value": stored})
        return {"field": field_key, "value": stored, "created": created}

    def delete_field_option(self, key: Any, option_id: Any) -> Dict[str, Any]:
        """Remove an added value nobody uses (a typo, say)."""
        self._manage()
        domain = store.get_domain(self.conn, key) if isinstance(key, str) else None
        if domain is None:
            raise NotFound("Den Bereich gibt es nicht.")
        with self.conn.transaction():
            option = store.get_field_option(self.conn, option_id)
            if option is None or option["domain_id"] != domain["id"]:
                raise NotFound("Diesen Wert gibt es nicht.")
            if option["used"]:
                n = option["used"]
                raise Conflict(f"\u00ab{option['value']}\u00bb wird von {n} "
                               f"{'Experiment' if n == 1 else 'Experimenten'} verwendet und bleibt.")
            store.delete_field_option(self.conn, option["id"])
        self._audit("experiments.field_option.delete", "exp_domain", domain["key"],
                    {"field": option["field"], "value": option["value"]})
        return {"deleted": True}

    def export_domain(self, key: Any) -> str:
        self._manage()
        domain = store.get_domain(self.conn, key) if isinstance(key, str) else None
        if domain is None:
            raise NotFound("Den Bereich gibt es nicht.")
        parts = store.domain_pack_parts(self.conn, domain["id"])
        own = {m["key"]: m for m in parts["metrics"]}
        referenced: List[str] = []
        evaluator_keys: List[str] = []
        for type_ in parts["types"]:
            for ref in schema.metric_refs(type_["definition"]):
                if ref not in referenced:
                    referenced.append(ref)
            for ref in schema.evaluator_refs(type_["definition"]):
                if ref not in evaluator_keys:
                    evaluator_keys.append(ref)
        metrics = [m for m in parts["metrics"] if not m["archived"] or m["key"] in referenced]
        requires = [k for k in referenced if k not in own and k in parts["global_metrics"]]
        custom = []
        for ref in evaluator_keys:
            evaluator = store.get_evaluator(self.conn, key=ref, with_code=True)
            if evaluator is not None and not evaluator["builtin"]:
                custom.append({
                    "key": evaluator["key"], "name": evaluator["name"],
                    "language": evaluator["language"],
                    "description": evaluator["description"][:MAX_CONFIG_DESCRIPTION],
                    "input_kinds": evaluator["input_kinds"],
                    "params_schema": evaluator["params_schema"], "code": evaluator["code"],
                })
        pack = {
            "pack": domain["key"],
            "title": domain["name"],
            "description": domain["description"][:MAX_CONFIG_DESCRIPTION],
            "version": 1,
            "domain": {"key": domain["key"], "name": domain["name"], "id_prefix": domain["id_prefix"],
                       "color": domain["color"],
                       "description": domain["description"][:MAX_CONFIG_DESCRIPTION],
                       "field_options": store.field_option_values(self.conn, domain["id"])},
            "metrics": [{"key": m["key"], "name": m["name"], "kind": m["kind"], "unit": m["unit"],
                         "direction": m["direction"],
                         "description": m["description"][:MAX_CONFIG_DESCRIPTION],
                         "definition": m["definition"]} for m in metrics],
            "types": [{"key": t["key"], "name": t["name"],
                       "description": t["description"][:MAX_CONFIG_DESCRIPTION],
                       "definition": t["definition"]} for t in parts["types"]],
            "evaluators": custom,
            "requires_metrics": requires,
        }
        normalised = packs.validate_pack(pack, known_metrics=list(parts["global_metrics"]),
                                         known_evaluators=store.evaluator_keys(self.conn))
        return packs.dump_pack(normalised)

    # -- types -------------------------------------------------------------

    def list_types(self, domain: Any = None, include_archived: bool = False) -> List[Dict[str, Any]]:
        self._view()
        if domain:
            found = store.get_domain(self.conn, domain) if isinstance(domain, str) else None
            if found is None:
                raise NotFound("Den Bereich gibt es nicht.")
            items = store.list_types(self.conn, domain_id=found["id"], restrict=True,
                                     include_archived=_truthy(include_archived))
        else:
            items = store.list_types(self.conn, include_archived=_truthy(include_archived))
        return [_public(t, "domain_id") for t in items]

    def _type_out(self, type_id: str) -> Dict[str, Any]:
        found = store.get_type(self.conn, type_id)
        if found is None:
            raise NotFound("Den Typ gibt es nicht.")
        out = _public(found, "domain_id")
        out["definition_yaml"] = definition_yaml(found["definition"] or {})
        out["versions"] = store.type_versions(self.conn, found["id"])
        return out

    def get_type(self, type_id: Any) -> Dict[str, Any]:
        self._view()
        return self._type_out(_id(type_id, "Den Typ gibt es nicht."))

    def _definition_from(self, data: Dict[str, Any]) -> Dict[str, Any]:
        text = data.get("definition_text")
        if text not in (None, ""):
            return schema.parse_definition_text(text)
        if data.get("definition") is not None:
            return schema.validate_type_definition(data["definition"])
        raise _refuse("definition", "Bitte eine Typdefinition angeben.")

    def _check_definition_refs(self, definition: Dict[str, Any], domain_id: Optional[str]) -> None:
        """What schema cannot know: every metric resolves (in the domain, then
        globally), every evaluator exists and takes the metric's kind, and
        every evaluation's params fit the evaluator's schema."""
        errors: Dict[str, str] = {}
        resolved = store.resolve_metrics(self.conn, domain_id, schema.metric_refs(definition))
        primary = None
        for i, entry in enumerate(definition.get("metrics") or []):
            metric = resolved.get(entry["metric"])
            path = f"metrics[{i}].metric"
            if metric is None:
                errors.setdefault(path, f"Die Metrik \u00ab{entry['metric']}\u00bb gibt es nicht.")
                continue
            if metric["archived"]:
                # A new experiment would not get it (archived metrics cannot
                # be assigned anew); the type would promise what it cannot do.
                errors.setdefault(path, f"Die Metrik \u00ab{entry['metric']}\u00bb ist archiviert.")
            if entry["role"] == "primary":
                primary = metric
            if entry["role"] == "guardrail":
                value = float(entry["value"])
                bounds = metric["definition"] or {}
                if metric["kind"] == "proportion" and not 0.0 <= value <= 1.0:
                    errors.setdefault(f"metrics[{i}].value",
                                      "Anteile werden als Bruch angegeben (0.7 f\u00fcr 70 %).")
                elif ("min" in bounds and value < bounds["min"]) or \
                        ("max" in bounds and value > bounds["max"]):
                    errors.setdefault(f"metrics[{i}].value",
                                      "Der Wert liegt ausserhalb von Minimum und Maximum der Metrik.")
        for i, entry in enumerate(definition.get("evaluation") or []):
            evaluator = _evaluator_by_key(self.conn, entry["evaluator"])
            if evaluator is None:
                errors.setdefault(f"evaluation[{i}].evaluator",
                                  f"Den Auswerter \u00ab{entry['evaluator']}\u00bb gibt es nicht.")
                continue
            target = entry["metric"]
            metric = primary if target == "primary" else (None if target == "all" else resolved.get(target))
            if target not in ("primary", "all") and metric is None:
                errors.setdefault(f"evaluation[{i}].metric",
                                  f"Die Metrik \u00ab{target}\u00bb gibt es nicht.")
            elif metric is not None and metric["kind"] not in evaluator["input_kinds"]:
                errors.setdefault(f"evaluation[{i}].metric", _kind_message(evaluator, metric))
            try:
                evaluators.validate_params(evaluator["params_schema"], _params(entry.get("params")))
            except ValidationError as exc:
                for path, message in (exc.fields or {"params": exc.message}).items():
                    errors.setdefault(f"evaluation[{i}].{path}", message)
        if errors:
            path, message = next(iter(errors.items()))
            raise ValidationError(f"Die Typdefinition ist ung\u00fcltig: {path}: {message}",
                                  fields=errors)

    def validate_type(self, data: Any) -> Dict[str, Any]:
        self._manage()
        data = _obj(data)
        domain = self._domain_arg(data.get("domain"), allow_none=True)
        definition = self._definition_from(data)
        self._check_definition_refs(definition, domain["id"] if domain else None)
        return definition

    def create_type(self, data: Any) -> Dict[str, Any]:
        self._manage()
        data = _obj(data)
        domain = self._domain_arg(data.get("domain"), allow_none=True)
        domain_id = domain["id"] if domain else None
        key = _text(data, "key", limit=48, label="Schl\u00fcssel", required=True)
        if not TYPE_KEY_RE.match(key):
            raise _refuse("key", "Kleinbuchstaben, Ziffern, _ und -, beginnend mit einem Buchstaben; "
                                 "2 bis 48 Zeichen.", "Schl\u00fcssel")
        name = _text(data, "name", limit=80, label="Name", required=True)
        description = _text(data, "description", limit=MAX_CONFIG_DESCRIPTION,
                            label="Beschreibung", multiline=True)
        if data.get("copy_from") not in (None, "") and data.get("definition") is None \
                and data.get("definition_text") in (None, ""):
            source = store.get_type(self.conn, data["copy_from"])
            if source is None:
                raise _refuse("copy_from", "Den Typ gibt es nicht.", "Vorlage")
            definition = schema.validate_type_definition(source["definition"])
        else:
            definition = self._definition_from(data)
        self._check_definition_refs(definition, domain_id)
        type_id = store.insert_type(self.conn, domain_id=domain_id, key=key, name=name,
                                    description=description, definition=definition,
                                    actor_id=self._actor_id)
        self._audit("experiments.type.create", "exp_type", type_id,
                    {"key": key, "domain": domain["key"] if domain else None, "version": 1})
        return self._type_out(type_id)

    def add_type_version(self, type_id: Any, data: Any) -> Dict[str, Any]:
        self._manage()
        data = _obj(data)
        type_ = store.get_type(self.conn, _id(type_id, "Den Typ gibt es nicht."))
        if type_ is None:
            raise NotFound("Den Typ gibt es nicht.")
        changes: Dict[str, Any] = {}
        if "name" in data:
            name = _text(data, "name", limit=80, label="Name", required=True)
            if name != type_["name"]:
                changes["name"] = name
        if "description" in data:
            description = _text(data, "description", limit=MAX_CONFIG_DESCRIPTION,
                                label="Beschreibung", multiline=True)
            if description != type_["description"]:
                changes["description"] = description
        definition = None
        if data.get("definition") is not None or data.get("definition_text") not in (None, ""):
            definition = self._definition_from(data)
            self._check_definition_refs(definition, type_["domain_id"])
        with self.conn.transaction():
            version, added = type_["current_version"], False
            if definition is not None:
                version, added = store.add_type_version(self.conn, type_["id"], definition,
                                                        self._actor_id)
            store.update_type(self.conn, type_["id"], changes)
            requeued = 0
            if "name" in changes:
                ids = store.experiments_for_reindex(self.conn, type_id=type_["id"])
                requeued = _queue_index_many(self.conn, self.settings, ids, priority=200)
        if added or changes:
            self._audit("experiments.type.version", "exp_type", type_["id"],
                        {"version": version, "added": added, "changed": sorted(changes),
                         "requeued": requeued})
        return self._type_out(type_["id"])

    def set_type_archived(self, type_id: Any, archived: Any) -> Dict[str, Any]:
        self._manage()
        type_ = store.get_type(self.conn, _id(type_id, "Den Typ gibt es nicht."))
        if type_ is None:
            raise NotFound("Den Typ gibt es nicht.")
        if isinstance(archived, dict):
            archived = archived.get("archived")
        if not isinstance(archived, bool):
            raise _refuse("archived", "Muss true oder false sein.", "Archiviert")
        if archived != type_["archived"]:
            store.update_type(self.conn, type_["id"], {"archived": archived})
            self._audit("experiments.type.archive", "exp_type", type_["id"], {"archived": archived})
        return self._type_out(type_["id"])

    # -- metrics -----------------------------------------------------------

    def list_metrics(self, domain: Any = None, include_archived: bool = False) -> List[Dict[str, Any]]:
        self._view()
        if domain:
            found = store.get_domain(self.conn, domain) if isinstance(domain, str) else None
            if found is None:
                raise NotFound("Den Bereich gibt es nicht.")
            items = store.list_metrics(self.conn, domain_id=found["id"], restrict=True,
                                       include_archived=_truthy(include_archived))
        else:
            items = store.list_metrics(self.conn, include_archived=_truthy(include_archived))
        return [_public(m, "domain_id") for m in items]

    def create_metric(self, data: Any) -> Dict[str, Any]:
        self._manage()
        data = _obj(data)
        domain = self._domain_arg(data.get("domain"), allow_none=True)
        key = _text(data, "key", limit=48, label="Schl\u00fcssel", required=True)
        if not METRIC_KEY_RE.match(key) or key in schema.RESERVED_METRIC_KEYS:
            raise _refuse("key", schema.METRIC_KEY_MESSAGE
                          + " Nicht \u00abprimary\u00bb oder \u00aball\u00bb.", "Schl\u00fcssel")
        name = _text(data, "name", limit=80, label="Name", required=True)
        kind = _choice(data.get("kind"), "kind", kinds.KINDS, "Unbekannte Art.", "Art")
        unit = _text(data, "unit", limit=20, label="Einheit")
        direction = _choice(data.get("direction") or "higher", "direction", labels.DIRECTION_LABELS,
                            "Erlaubt sind higher, lower und none.", "Richtung")
        description = _text(data, "description", limit=MAX_CONFIG_DESCRIPTION,
                            label="Beschreibung", multiline=True)
        definition = schema.validate_metric_definition(kind, data.get("definition"))
        metric_id = store.insert_metric(
            self.conn, domain_id=domain["id"] if domain else None, key=key, name=name, kind=kind,
            unit=unit, direction=direction, description=description, definition=definition,
            actor_id=self._actor_id)
        self._audit("experiments.metric.create", "exp_metric", metric_id,
                    {"key": key, "kind": kind, "domain": domain["key"] if domain else None})
        return _public(store.get_metric(self.conn, metric_id), "domain_id")

    def update_metric(self, metric_id: Any, data: Any) -> Dict[str, Any]:
        self._manage()
        data = _obj(data)
        ident = _id(metric_id, "Die Metrik gibt es nicht.")
        with self.conn.transaction():
            metric = store.get_metric(self.conn, ident, lock=True)
            if metric is None:
                raise NotFound("Die Metrik gibt es nicht.")
            changes: Dict[str, Any] = {}
            if "name" in data:
                changes["name"] = _text(data, "name", limit=80, label="Name", required=True)
            kind = metric["kind"]
            if "kind" in data:
                kind = _choice(data["kind"], "kind", kinds.KINDS, "Unbekannte Art.", "Art")
                changes["kind"] = kind
            if "unit" in data:
                changes["unit"] = _text(data, "unit", limit=20, label="Einheit")
            if "direction" in data:
                changes["direction"] = _choice(data["direction"], "direction",
                                               labels.DIRECTION_LABELS,
                                               "Erlaubt sind higher, lower und none.", "Richtung")
            if "description" in data:
                changes["description"] = _text(data, "description", limit=MAX_CONFIG_DESCRIPTION,
                                               label="Beschreibung", multiline=True)
            if "definition" in data or kind != metric["kind"]:
                raw = data["definition"] if "definition" in data else metric["definition"]
                changes["definition"] = schema.validate_metric_definition(kind, raw)
            if "archived" in data:
                changes["archived"] = _flag(data, "archived", "Archiviert")
            current = dict(metric)
            changes = {k: v for k, v in changes.items() if current.get(k) != v}
            if "kind" in changes and store.metric_has_measurements(self.conn, metric["id"]):
                raise _refuse("kind", MSG_KIND_LOCKED, "Art")
            if changes:
                store.update_metric(self.conn, metric["id"], changes)
        requeued = 0
        if set(changes) - {"archived", "description"}:
            # A transaction of its own: holding the metric's row lock while
            # waiting for experiment rows would deadlock with a measurement
            # insert, which holds its experiment and waits for the metric
            # (foreign key check).
            with self.conn.transaction():
                ids = store.experiments_using_metric(self.conn, metric["id"])
                requeued = _queue_index_many(self.conn, self.settings, ids, priority=200)
        if changes:
            self._audit("experiments.metric.update", "exp_metric", metric["id"],
                        {"changed": sorted(changes), "requeued": requeued})
        return _public(store.get_metric(self.conn, metric["id"]), "domain_id")

    # -- evaluators --------------------------------------------------------

    def list_evaluators(self, include_archived: bool = False) -> List[Dict[str, Any]]:
        self._view()
        return store.list_evaluators(self.conn, include_archived=_truthy(include_archived))

    def _evaluator_out(self, evaluator_id: str) -> Dict[str, Any]:
        found = store.get_evaluator(self.conn, evaluator_id, with_code=True)
        if found is None:
            raise NotFound("Den Auswerter gibt es nicht.")
        found["versions"] = store.evaluator_versions(self.conn, found["id"])
        return found

    def get_evaluator(self, evaluator_id: Any) -> Dict[str, Any]:
        self._view()
        return self._evaluator_out(_id(evaluator_id, "Den Auswerter gibt es nicht."))

    def _evaluator_fields(self, data: Dict[str, Any], current: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        def pick(field: str) -> Any:
            if field in data:
                return data[field]
            return current.get(field) if current is not None else None

        name = _clean_text(pick("name") if pick("name") is not None else "", "name", limit=80,
                           multiline=False, label="Name")
        if not name:
            raise _refuse("name", "Darf nicht leer sein.", "Name")
        description = _clean_text(pick("description") or "", "description",
                                  limit=MAX_CONFIG_DESCRIPTION, multiline=True, label="Beschreibung")
        code = pick("code")
        if not isinstance(code, str) or not code.strip():
            raise _refuse("code", "Pflichtangabe fehlt.", "Code")
        if len(code) > MAX_EVALUATOR_CODE or "\x00" in code:
            raise _refuse("code", f"H\u00f6chstens {kinds.format_plain(MAX_EVALUATOR_CODE)} Zeichen, "
                                  "keine Nullzeichen.", "Code")
        try:
            code.encode("utf-8")
        except UnicodeEncodeError:
            raise _refuse("code", "Enth\u00e4lt ung\u00fcltige Zeichen.", "Code") from None
        input_kinds = pick("input_kinds")
        if not isinstance(input_kinds, list) or not input_kinds \
                or not all(isinstance(k, str) for k in input_kinds) \
                or any(k not in kinds.KINDS for k in input_kinds) \
                or len(set(input_kinds)) != len(input_kinds):
            raise _refuse("input_kinds", "Eine Liste von Messarten ohne Wiederholung.", "Messarten")
        raw_schema = pick("params_schema") or {}
        if not isinstance(raw_schema, dict):
            raise _refuse("params_schema", "Muss ein Objekt sein.", "Parameterschema")
        params_schema = packs.validate_params_schema(schema.checked_json(
            raw_schema, "Das Parameterschema ist ung\u00fcltig.", max_nodes=5_000, max_chars=64_000))
        return {"name": name, "description": description, "code": code,
                "input_kinds": list(input_kinds), "params_schema": params_schema}

    def create_evaluator(self, data: Any) -> Dict[str, Any]:
        self._manage()
        data = _obj(data)
        key = _text(data, "key", limit=64, label="Schl\u00fcssel", required=True)
        if not EVALUATOR_KEY_RE.match(key):
            raise _refuse("key", schema.EVALUATOR_KEY_MESSAGE, "Schl\u00fcssel")
        if key.startswith("builtin."):
            raise _refuse("key", "Schl\u00fcssel mit \u00abbuiltin.\u00bb sind den eingebauten Auswertern "
                                 "vorbehalten.", "Schl\u00fcssel")
        language = data.get("language")
        if language not in ("python", "julia"):
            raise _refuse("language", "Erlaubt sind python und julia.", "Sprache")
        fields = self._evaluator_fields(data, None)
        evaluator_id = store.insert_evaluator(self.conn, key=key, language=language,
                                              actor_id=self._actor_id, **fields)
        self._audit("experiments.evaluator.create", "exp_evaluator", evaluator_id,
                    {"key": key, "language": language, "version": 1})
        return self._evaluator_out(evaluator_id)

    def add_evaluator_version(self, evaluator_id: Any, data: Any) -> Dict[str, Any]:
        self._manage()
        data = _obj(data)
        current = store.get_evaluator(self.conn, _id(evaluator_id, "Den Auswerter gibt es nicht."),
                                      with_code=True)
        if current is None:
            raise NotFound("Den Auswerter gibt es nicht.")
        if current["builtin"]:
            raise ValidationError(MSG_BUILTIN_LOCKED)
        fields = self._evaluator_fields(data, current)
        version, added = store.add_evaluator_version(self.conn, current["id"],
                                                     actor_id=self._actor_id, **fields)
        if added:
            self._audit("experiments.evaluator.version", "exp_evaluator", current["id"],
                        {"key": current["key"], "version": version})
        return self._evaluator_out(current["id"])

    def test_evaluator(self, evaluator_id: Any, data: Any) -> Dict[str, Any]:
        self._manage()
        data = _obj(data)
        evaluator = store.get_evaluator(self.conn, _id(evaluator_id, "Den Auswerter gibt es nicht."),
                                        with_code=True)
        if evaluator is None:
            raise NotFound("Den Auswerter gibt es nicht.")
        key = data.get("experiment")
        if not isinstance(key, str) or not store.KEY_RE.fullmatch(key.strip()):
            raise _refuse("experiment", "Bitte ein Experiment angeben (z. B. MKT-1).", "Experiment")
        snapshot = self._snapshot(key.strip())
        metric = next((m for m in snapshot["metrics"] if m["key"] == data.get("metric")), None)
        if metric is None:
            raise _refuse("metric", "Die Metrik ist diesem Experiment nicht zugeordnet.", "Metrik")
        if metric["kind"] not in evaluator["input_kinds"]:
            raise _refuse("metric", _kind_message(evaluator, metric), "Metrik")
        params = evaluators.validate_params(evaluator["params_schema"], _params(data.get("params")))
        scope = schema.validate_scope(data.get("scope"))
        unsaved = data.get("code") is not None
        code = evaluator["code"]
        if unsaved:
            code = self._evaluator_fields({"code": data["code"]}, evaluator)["code"]
        code_sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest()
        with _read_view(self.conn):
            input_data, _ = _prepare_input(self.conn, snapshot, metric, evaluator, params, scope)
            if _needs_rows(evaluator):
                input_data = _with_rows(self.conn, self.settings, input_data, snapshot, metric, scope)
        if evaluator["builtin"]:
            started = time.monotonic()
            output = evaluators.run_builtin(evaluator["key"], input_data)
            result = {"ok": True, "output": output, "error": None, "logs": "",
                      "duration_ms": int((time.monotonic() - started) * 1000)}
        else:
            if self.runner is None:
                raise Unavailable(MSG_NO_RUNNER)
            timeout = min(int(self.settings.runner_timeout_seconds), RUNNER_TEST_MAX_SECONDS)
            raw = self.runner.run(language=evaluator["language"], code=code, data=input_data,
                                  timeout_seconds=timeout)
            result = {"ok": bool(raw.get("ok")), "output": None, "error": raw.get("error"),
                      "logs": raw.get("logs") or "", "duration_ms": raw.get("duration_ms")}
            if result["ok"]:
                try:
                    result["output"] = evaluators.sanitize_output(
                        raw.get("output"), evaluator_name=evaluator["name"],
                        metric_kind=metric["kind"])
                except ValidationError as exc:
                    result.update(ok=False, error=exc.message)
            elif not result["error"]:
                result["error"] = MSG_EVALUATION_FAILED
        self._audit("experiments.evaluator.test", "exp_evaluator", evaluator["id"],
                    {"experiment": snapshot["key"], "metric": metric["key"],
                     "code_sha256": code_sha256, "unsaved": unsaved})
        return result

    def sample_size(self, data: Any) -> Dict[str, Any]:
        """Units per variant for a two-sided test of the smallest relevant
        difference. ``comparisons`` (default 1): how many variants are each
        compared with the control. The built-in tests Holm-correct several
        comparisons, whose strictest step tests at alpha / comparisons; the
        plan uses that level (Bonferroni, on the safe side of Holm), so the
        sample keeps its power in the analysis that will actually run."""
        self._view()
        data = _obj(data)
        kind = _choice(data.get("kind"), "kind", ("proportion", "mean"),
                       "Erlaubt sind proportion und mean.", "Art")
        mde = _number(data.get("mde"))
        if mde is None or mde == 0:
            raise _refuse("mde", "Bitte den kleinsten relevanten Unterschied angeben (nicht 0).",
                          LABEL_MDE)
        alpha = _number(data.get("alpha")) if data.get("alpha") not in (None, "") else 0.05
        power = _number(data.get("power")) if data.get("power") not in (None, "") else 0.8
        if alpha is None or not 0.0 < alpha < 0.5:
            raise _refuse("alpha", "Erlaubt ist ein Wert zwischen 0 und 0,5.", "Signifikanzniveau")
        if power is None or not 0.5 <= power < 1.0:
            raise _refuse("power", "Erlaubt ist ein Wert zwischen 0,5 und 1.", "Testst\u00e4rke")
        comparisons = _int_value(data.get("comparisons"), "comparisons", lo=1,
                                 hi=MAX_SAMPLE_COMPARISONS, label="Vergleiche mit der Kontrolle",
                                 default=1)
        alpha_used = alpha / comparisons
        too_large = _refuse("mde", MSG_SAMPLE_TOO_LARGE, LABEL_MDE)
        # The spread-to-effect ratio is checked before anything is squared:
        # (sd / mde) ** 2 overflows and mde * mde underflows to 0 for tiny
        # effects, and neither may end as an internal error.
        if kind == "proportion":
            base = _number(data.get("base"))
            if base is None:
                raise _refuse("base", "Bitte die Basisrate angeben.", "Basisrate")
            spread = None
            if 0.0 < base < 1.0 and 0.0 < base + mde < 1.0:
                spread = math.sqrt((base * (1.0 - base) + (base + mde) * (1.0 - base - mde)) / 2.0)
            compute = lambda: stats.sample_size_proportion(base, mde, alpha_used, power)  # noqa: E731
        else:
            sd = _number(data.get("sd"))
            if sd is None or sd <= 0:
                raise _refuse("sd", "Bitte eine Standardabweichung gr\u00f6sser als 0 angeben.",
                              "Standardabweichung")
            spread = sd
            compute = lambda: stats.sample_size_mean(sd, mde, alpha_used, power)  # noqa: E731
        if spread is not None and spread / abs(mde) > MAX_SAMPLE_EFFECT_RATIO:
            raise too_large
        try:
            n = compute()
        except ValidationError:
            raise  # stats' own German refusal (e.g. base plus effect outside 0..1)
        except (ZeroDivisionError, OverflowError, ValueError):
            raise too_large from None
        if not isinstance(n, int) or n > MAX_SAMPLE_PER_VARIANT:
            raise too_large
        return {"per_variant": int(n), "alpha_used": alpha_used, "comparisons": comparisons}

    # -- experiments: reading ----------------------------------------------

    def list_experiments(self, *, domain: Any = None, status: Any = None, q: Any = None,
                         tag: Any = None, include_archived: bool = False, after: Any = None,
                         limit: Any = 50) -> Dict[str, Any]:
        self._view()
        filters = {}
        for name, value in (("domain", domain), ("status", status), ("tag", tag)):
            if value in (None, ""):
                continue
            filters[name] = _clean_text(value, name, limit=100, multiline=False, label="Filter")
        items, next_after, total = store.list_summaries(
            self.conn, domain=filters.get("domain"), status=filters.get("status"),
            words=_words(q), tag=filters.get("tag"), include_archived=_truthy(include_archived),
            after=after or None, limit=_limit(limit, 50, 200))
        return {"items": items, "next_after": next_after, "total": total}

    def get_experiment(self, key: Any) -> Dict[str, Any]:
        self._view()
        with _read_view(self.conn):
            loaded = store.load_snapshot_with_definition(self.conn, _key(key), actor=self.actor)
            if loaded is None:
                raise NotFound()
            snapshot, definition = loaded
            usable = self._usable_evaluators(snapshot)
        snapshot["definition"] = definition
        snapshot["transitions"] = self._transitions(snapshot, definition)
        snapshot["evaluators"] = usable
        return snapshot

    def _usable_evaluators(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        metric_kinds = {m["kind"] for m in snapshot["metrics"]}
        return [e for e in store.list_evaluators(self.conn)
                if metric_kinds & set(e["input_kinds"])]

    @staticmethod
    def _facts_from_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
        primary = next((m for m in snapshot["metrics"] if m["role"] == "primary"), None)
        decisions = snapshot["decisions"]
        return {
            "hypothesis": bool((snapshot["hypothesis"] or "").strip()),
            "primary_metric": primary is not None,
            "variants": len(snapshot["variants"]),
            "measurements": snapshot["measurement_count"],
            "evaluations": sum(1 for e in snapshot["evaluations"] if e["status"] == "done"),
            "decision": bool(decisions),
            "learning": bool(decisions and (decisions[0]["learning"] or "").strip()),
            "fields": dict(snapshot["field_values"]),
            "variant_n": ({a["variant"]: a["n"] for a in primary["aggregates"]
                           if a["variant"] is not None} if primary else {}),
        }

    def _facts(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Transition facts read inside the caller's transaction (after the lock)."""
        eid = row["id"]
        metrics = store.assigned_metrics(self.conn, eid)
        primary = next((m for m in metrics if m["role"] == "primary"), None)
        variant_n: Dict[str, int] = {}
        if primary is not None:
            for agg in store.aggregates(self.conn, eid, primary["id"], kind=primary["kind"]):
                if agg["variant"] is not None:
                    variant_n[agg["variant"]] = agg["n"]
        decided, learning = store.decision_facts(self.conn, eid)
        return {
            "hypothesis": bool((row["hypothesis"] or "").strip()),
            "primary_metric": primary is not None,
            "variants": len(store.list_variants(self.conn, eid)),
            "measurements": store.batch_counts(self.conn, eid)[1],
            "evaluations": store.count_done_evaluations(self.conn, eid),
            "decision": decided,
            "learning": learning,
            "fields": dict(row["fields"]),
            "variant_n": variant_n,
        }

    def _transitions(self, snapshot: Dict[str, Any], definition: Dict[str, Any]) -> List[Dict[str, Any]]:
        facts = self._facts_from_snapshot(snapshot)
        status = snapshot["status"]
        out = []
        for t in schema.transitions_from(definition, status):
            phase = schema.state_phase(definition, t["to"])
            decides = phase == "decided"
            here = dict(facts)
            if decides:
                # The decision form supplies the decision (and the learning).
                here["decision"] = True
                here["learning"] = True
            try:
                missing = schema.check_transition(definition, status, t["to"], facts=here,
                                                  roles=self._roles)
            except (Forbidden, ValidationError) as exc:
                missing = [exc.message]
            out.append({"to": t["to"], "label": t["label"], "allowed": not missing,
                        "missing": missing, "needs_comment": phase == "stopped",
                        "decides": decides})
        return out

    # -- experiments: writing ----------------------------------------------

    @staticmethod
    def _variants(raw: Any, rules: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(raw, list):
            raise _refuse("variants", "Muss eine Liste sein.", "Varianten")
        vmin, vmax = int(rules.get("min", 0)), int(rules.get("max", 10))
        if len(raw) > vmax:
            raise _refuse("variants", f"Der Typ erlaubt h\u00f6chstens {vmax} Varianten.", "Varianten")
        if len(raw) < vmin:
            raise _refuse("variants", f"Der Typ verlangt mindestens {vmin} Varianten.", "Varianten")
        out: List[Dict[str, Any]] = []
        seen: set = set()
        controls = 0
        allocation = 0.0
        for i, item in enumerate(raw):
            where = f"variants.{i}"
            if not isinstance(item, dict):
                raise _refuse(where, "Eine Variante ist ein Objekt.", "Varianten")
            key = item.get("key")
            if not isinstance(key, str) or not VARIANT_KEY_RE.match(key.strip()):
                raise _refuse(f"{where}.key", "Buchstaben, Ziffern, _, . und -, beginnend mit einem "
                                              "Buchstaben oder einer Ziffer; h\u00f6chstens 40 Zeichen.",
                              "Variante")
            key = key.strip()
            if key in seen:
                raise _refuse(f"{where}.key", f"Die Variante \u00ab{key}\u00bb gibt es schon.", "Variante")
            seen.add(key)
            name = _text(item, "name", limit=120, label="Name der Variante")
            description = _text(item, "description", limit=5000, label="Beschreibung der Variante",
                                multiline=True)
            is_control = item.get("is_control", False)
            if not isinstance(is_control, bool):
                raise _refuse(f"{where}.is_control", "Muss true oder false sein.", "Kontrolle")
            controls += 1 if is_control else 0
            if controls > 1:
                raise _refuse(f"{where}.is_control", "Nur eine Variante kann die Kontrolle sein.",
                              "Kontrolle")
            share = item.get("allocation")
            if share is not None:
                share = _number(share)
                if share is None or not 0.0 <= share <= 1.0:
                    raise _refuse(f"{where}.allocation",
                                  "Eine Zuteilung zwischen 0 und 1 (0\u2013100 %).", "Zuteilung")
                allocation += share
            out.append({"key": key, "name": name, "description": description,
                        "is_control": is_control, "allocation": share})
        if allocation > 1.0 + 1e-9:
            # The form takes percent, the API fractions: name both.
            raise _refuse("variants", "Die Zuteilungen ergeben zusammen mehr als 100 % "
                                      "(Summe der Anteile > 1).", "Varianten")
        return out

    def _metric_entries(self, raw: Any, domain_id: str, assigned: set) -> List[Dict[str, Any]]:
        if not isinstance(raw, list):
            raise _refuse("metrics", "Muss eine Liste sein.", "Metriken")
        if len(raw) > 30:
            raise _refuse("metrics", "H\u00f6chstens 30 Metriken.", "Metriken")
        keys = [item.get("metric") for item in raw if isinstance(item, dict)]
        resolved = store.resolve_metrics(self.conn, domain_id, [k for k in keys if isinstance(k, str)])
        out: List[Dict[str, Any]] = []
        seen: set = set()
        primaries = 0
        for i, item in enumerate(raw):
            where = f"metrics.{i}"
            if not isinstance(item, dict):
                raise _refuse(where, "Ein Eintrag ist ein Objekt.", "Metriken")
            key = item.get("metric")
            metric = resolved.get(key) if isinstance(key, str) else None
            if metric is None:
                raise _refuse(f"{where}.metric", f"Die Metrik \u00ab{str(key)[:48]}\u00bb gibt es nicht.",
                              "Metrik")
            if metric["archived"] and metric["id"] not in assigned:
                raise _refuse(f"{where}.metric", f"Die Metrik \u00ab{key}\u00bb ist archiviert.", "Metrik")
            if metric["id"] in seen:
                raise _refuse(f"{where}.metric", f"Die Metrik \u00ab{key}\u00bb steht schon in der Liste.",
                              "Metrik")
            seen.add(metric["id"])
            role = _choice(item.get("role"), f"{where}.role", labels.METRIC_ROLE_LABELS,
                           "Erlaubt sind primary, secondary und guardrail.", "Rolle")
            if role == "primary":
                primaries += 1
                if primaries > 1:
                    raise _refuse(f"{where}.role", "Es kann nur eine prim\u00e4re Metrik geben.", "Rolle")
            op, value = None, None
            if role == "guardrail":
                op = item.get("guardrail_op")
                value = _number(item.get("guardrail_value"))
                if op not in ("max", "min"):
                    raise _refuse(f"{where}.guardrail_op", "Erlaubt sind max und min.", "Leitplanke")
                if value is None:
                    raise _refuse(f"{where}.guardrail_value", "Bitte eine endliche Zahl angeben.",
                                  "Leitplanke")
                if metric["kind"] == "proportion" and not 0.0 <= value <= 1.0:
                    raise _refuse(f"{where}.guardrail_value",
                                  "Anteile werden als Bruch angegeben (0.7 f\u00fcr 70 %).", "Leitplanke")
            out.append({"metric_id": metric["id"], "key": metric["key"], "role": role,
                        "guardrail_op": op, "guardrail_value": value})
        return out

    def create_experiment(self, data: Any) -> Dict[str, Any]:
        self._view()
        data = _obj(data)
        domain = self._domain_arg(data.get("domain"), allow_none=False)
        if domain["archived"]:
            raise _refuse("domain", "Der Bereich ist archiviert.", "Bereich")
        type_ = store.find_type(self.conn, domain["id"], data.get("type"))
        if type_ is None:
            raise _refuse("type", "Den Typ gibt es in diesem Bereich nicht.", "Typ")
        if type_["archived"]:
            raise _refuse("type", "Der Typ ist archiviert.", "Typ")
        definition = type_["definition"]
        title = _text(data, "title", limit=MAX_TITLE, label="Titel", required=True)
        hypothesis = _text(data, "hypothesis", limit=MAX_HYPOTHESIS, label="Hypothese", multiline=True)
        description = _text(data, "description", limit=MAX_DESCRIPTION, label="Beschreibung",
                            multiline=True)
        fields = schema.validate_field_values(
            definition, data.get("fields") or {}, partial=False,
            extra_options=store.field_option_values(self.conn, domain["id"]))
        tags = _tags(data.get("tags"))
        variants = self._variants(
            data["variants"] if data.get("variants") is not None
            else definition["variants"]["defaults"], definition["variants"])
        skipped_metrics: List[str] = []
        if data.get("metrics") is not None:
            raw_metrics = data["metrics"]
        else:
            # The type's defaults, without the ones archived since: those
            # cannot be assigned anew, and the create form cannot leave them
            # out (metrics a caller names explicitly are still refused).
            defaults = definition.get("metrics") or []
            resolved = store.resolve_metrics(self.conn, domain["id"], [m["metric"] for m in defaults])
            raw_metrics = []
            for m in defaults:
                if (resolved.get(m["metric"]) or {}).get("archived"):
                    skipped_metrics.append(m["metric"])
                    continue
                raw_metrics.append({"metric": m["metric"], "role": m["role"],
                                    "guardrail_op": m.get("op"), "guardrail_value": m.get("value")})
        metrics = self._metric_entries(raw_metrics, domain["id"], set())
        status = schema.initial_state(definition)
        with self.conn.transaction():
            key = store.allocate_experiment_key(self.conn, domain["id"])
            experiment_id = store.insert_experiment(
                self.conn, key=key, domain_id=domain["id"], type_id=type_["id"],
                type_version=type_["current_version"], title=title, hypothesis=hypothesis,
                description=description, status=status, fields=fields, tags=tags,
                owner_id=self._actor_id, actor_id=self._actor_id,
                started=schema.state_phase(definition, status) == "running")
            store.replace_variants(self.conn, experiment_id, variants)
            store.replace_experiment_metrics(self.conn, experiment_id, metrics)
            _queue_index(self.conn, self.settings, experiment_id)
        detail = {"experiment_id": experiment_id, "domain": domain["key"], "type": type_["key"],
                  "type_version": type_["current_version"]}
        if skipped_metrics:
            detail["skipped_archived_metrics"] = skipped_metrics
        self._audit("experiments.experiment.create", "experiment", key, detail)
        return self._snapshot(key)

    def update_experiment(self, key: Any, data: Any) -> Dict[str, Any]:
        self._view()
        data = _obj(data)
        changes: Dict[str, Any] = {}
        with self.conn.transaction():
            row = self._experiment(key, lock="update")
            self._check_row_version(row, data, required=True)
            if "title" in data:
                changes["title"] = _text(data, "title", limit=MAX_TITLE, label="Titel", required=True)
            if "hypothesis" in data:
                changes["hypothesis"] = _text(data, "hypothesis", limit=MAX_HYPOTHESIS,
                                              label="Hypothese", multiline=True)
            if "description" in data:
                changes["description"] = _text(data, "description", limit=MAX_DESCRIPTION,
                                               label="Beschreibung", multiline=True)
            if "fields" in data:
                updates = schema.validate_field_values(
                    row["definition"], data["fields"] or {}, partial=True,
                    extra_options=store.field_option_values(self.conn, row["domain_id"]))
                merged = dict(row["fields"])
                for field, value in updates.items():
                    if value is None:
                        merged.pop(field, None)
                    else:
                        merged[field] = value
                changes["fields"] = merged
            if "tags" in data:
                changes["tags"] = _tags(data["tags"])
            if "owner_id" in data:
                owner = data["owner_id"]
                if owner in (None, ""):
                    changes["owner_id"] = None
                else:
                    ident = store.canonical_uuid(owner)
                    if ident is None or not store.user_exists(self.conn, ident):
                        raise _refuse("owner_id", "Dieses Konto gibt es nicht.", "Verantwortlich")
                    # Only on a change: repeating the current owner must keep
                    # working after that person has lost the role.
                    if ident != row.get("owner_id") and \
                            not store.user_can_view_experiments(self.conn, ident):
                        raise _refuse("owner_id", "Diese Person hat kein aktives Konto mit Zugang "
                                                  "zu den Experimenten.", "Verantwortlich")
                    changes["owner_id"] = ident
            if "archived" in data:
                changes["archived"] = _flag(data, "archived", "Archiviert")
            changes = {k: v for k, v in changes.items() if row.get(k) != v}
            if changes:
                store.update_experiment_state(self.conn, row["id"], changes)
                _queue_index(self.conn, self.settings, row["id"])
        if changes:
            detail: Dict[str, Any] = {"changed": sorted(changes)}
            if "archived" in changes:
                # The activity list names the direction (archived or restored).
                detail["archived"] = changes["archived"]
            self._audit("experiments.experiment.update", "experiment", row["key"], detail)
        return self._snapshot(row["key"])

    def transition(self, key: Any, data: Any) -> Dict[str, Any]:
        self._view()
        data = _obj(data)
        target = data.get("to")
        if not isinstance(target, str) or not target:
            raise _refuse("to", "Bitte den neuen Status angeben.", "Status")
        comment = _text(data, "comment", limit=MAX_COMMENT, label="Grund", multiline=True)
        with self.conn.transaction():
            row = self._experiment(key, lock="update")
            self._check_row_version(row, data, required=False)
            definition = row["definition"]
            source = row["status"]
            phase = schema.state_phase(definition, target)
            if phase == "decided":
                raise ValidationError(MSG_DECIDE_BY_FORM)
            missing = schema.check_transition(definition, source, target, facts=self._facts(row),
                                              roles=self._roles)
            if missing:
                message = " ".join(missing)
                raise ValidationError(message, fields={"to": message})
            if phase == "stopped" and not comment:
                raise ValidationError(MSG_STOP_REASON, fields={"comment": MSG_STOP_REASON})
            changes: Dict[str, Any] = {"status": target}
            if phase == "running" and row["started_at"] is None:
                changes["started_at"] = store.NOW
            if phase == "stopped":
                changes["ended_at"] = store.NOW
            if phase not in ("stopped", "decided"):
                # Reopened: an end or decision date would now be wrong.
                if row["ended_at"] is not None:
                    changes["ended_at"] = None
                if row["decided_at"] is not None:
                    changes["decided_at"] = None
            store.update_experiment_state(self.conn, row["id"], changes)
            note_id = None
            if comment:
                body = (f"\u00ab{schema.state_label(definition, source)}\u00bb \u2192 "
                        f"\u00ab{schema.state_label(definition, target)}\u00bb: {comment}")
                note_id = store.insert_note(self.conn, experiment_id=row["id"], kind="status",
                                            body=body, variant_id=None, run_id=None,
                                            actor_id=self._actor_id)
            _queue_index(self.conn, self.settings, row["id"])
        self._audit("experiments.experiment.transition", "experiment", row["key"],
                    {"from": source, "to": target, "note_id": note_id})
        return self._snapshot(row["key"])

    def set_variants(self, key: Any, data: Any) -> Dict[str, Any]:
        self._view()
        data = _obj(data)
        if "variants" not in data:
            raise _refuse("variants", "Pflichtangabe fehlt.", "Varianten")
        with self.conn.transaction():
            row = self._experiment(key, lock="update")
            # Before the no-op check: a stale row_version is refused even when
            # nothing would change, so the client learns its view is outdated.
            self._check_row_version(row, data, required=False)
            desired = self._variants(data["variants"], row["definition"].get("variants") or {})
            wanted = {v["key"] for v in desired}
            current = store.list_variants(self.conn, row["id"])
            if [_variant_fields(v) for v in current] == [_variant_fields(v) for v in desired]:
                # Saved unchanged: no new row_version, no re-index, no audit entry.
                changed = False
            else:
                changed = True
                for variant in current:
                    if variant["key"] not in wanted and variant["has_data"]:
                        raise _refuse("variants",
                                      f"Die Variante \u00ab{variant['key']}\u00bb hat Messwerte und "
                                      "kann nicht entfernt werden.")
                outcome = store.replace_variants(self.conn, row["id"], desired)
                store.update_experiment_state(self.conn, row["id"], {})
                _queue_index(self.conn, self.settings, row["id"])
        if changed:
            self._audit("experiments.experiment.variants", "experiment", row["key"],
                        {"variants": len(desired), "added": outcome["added"],
                         "removed": outcome["removed"]})
        return self._snapshot(row["key"])

    def set_metrics(self, key: Any, data: Any) -> Dict[str, Any]:
        self._view()
        data = _obj(data)
        if "metrics" not in data:
            raise _refuse("metrics", "Pflichtangabe fehlt.", "Metriken")
        with self.conn.transaction():
            row = self._experiment(key, lock="update")
            # As in set_variants: checked before an unchanged save is skipped.
            self._check_row_version(row, data, required=False)
            current = store.assigned_metrics(self.conn, row["id"])
            entries = self._metric_entries(data["metrics"], row["domain_id"],
                                           {m["id"] for m in current})
            kept = {e["metric_id"] for e in entries}
            removed = [m for m in current if m["id"] not in kept]
            changed = [(m["id"], m["role"], m["guardrail_op"], m["guardrail_value"])
                       for m in current] != [(e["metric_id"], e["role"], e["guardrail_op"],
                                              e["guardrail_value"]) for e in entries]
            if changed:
                with_data = set(store.metrics_with_data(self.conn, row["id"],
                                                        [m["id"] for m in removed]))
                for metric in removed:
                    if metric["id"] in with_data:
                        raise _refuse("metrics",
                                      f"Die Metrik \u00ab{metric['name']}\u00bb hat Messwerte und "
                                      "kann nicht entfernt werden.")
                store.replace_experiment_metrics(self.conn, row["id"], entries)
                store.update_experiment_state(self.conn, row["id"], {})
                _queue_index(self.conn, self.settings, row["id"])
        if changed:
            self._audit("experiments.experiment.metrics", "experiment", row["key"],
                        {"metrics": [e["key"] for e in entries],
                         "removed": [m["key"] for m in removed]})
        return self._snapshot(row["key"])

    # -- measurements ------------------------------------------------------

    def _row_context(self, row: Dict[str, Any], rows: List[Any]
                     ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str], set]:
        """What measurement rows are validated against: the experiment's
        metrics by key, its variants (key -> id) and the ids among the rows'
        run_id that are runs of it.

        Call inside the insert's transaction: the metrics are read with KEY
        SHARE, so their kinds cannot change before the rows are written.
        """
        metrics = {m["key"]: m for m in store.assigned_metrics(self.conn, row["id"], lock=True)}
        variants = {v["key"]: v["id"] for v in store.list_variants(self.conn, row["id"])}
        wanted_runs = {r.get("run_id") for r in rows
                       if isinstance(r, dict) and isinstance(r.get("run_id"), str)} - {""}
        valid_runs = store.run_ids_of(self.conn, row["id"],
                                      {store.canonical_uuid(r) for r in wanted_runs} - {None})
        return metrics, variants, valid_runs

    @staticmethod
    def _prepare_row(raw: Any, metrics: Dict[str, Dict[str, Any]], variants: Dict[str, str],
                     valid_runs: set, run_id: Optional[str], default_variant: Optional[str],
                     metric_keys: List[str]) -> tuple:
        if not isinstance(raw, dict):
            raise ValidationError("Ein Messwert muss ein Objekt sein.")
        unknown = [k for k in raw if k not in _ROW_KEYS]
        if unknown:
            raise _refuse(str(unknown[0])[:40], f"Unbekannte Angabe \u00ab{str(unknown[0])[:40]}\u00bb.")
        key = raw.get("metric")
        metric = metrics.get(key) if isinstance(key, str) else None
        if metric is None:
            raise _refuse("metric", f"Die Metrik \u00ab{str(key)[:48]}\u00bb ist diesem Experiment "
                                    "nicht zugeordnet.")
        variant = raw.get("variant", default_variant)
        if variant in (None, ""):
            variant = default_variant
        variant_id = None
        if variant not in (None, ""):
            if not isinstance(variant, str) or variant not in variants:
                raise _refuse("variant", f"Unbekannte Variante \u00ab{str(variant)[:40]}\u00bb.")
            variant_id = variants[variant]
        row_run = None
        if run_id is not None:
            if raw.get("run_id") not in (None, ""):
                raise _refuse("run_id", "Zeilen eines Laufs haben keine eigene run_id.")
            row_run = run_id
        elif raw.get("run_id") not in (None, ""):
            row_run = store.canonical_uuid(raw.get("run_id"))
            if row_run is None or row_run not in valid_runs:
                raise _refuse("run_id", "Diesen Lauf gibt es in diesem Experiment nicht.")
        definition = dict(metric["definition"] or {})
        definition["_name"] = metric["name"]
        normal = kinds.validate_row(metric["kind"], raw, definition)
        if abs(normal["value"]) > MAX_ABS_VALUE:
            raise _refuse("value", "Der Wert ist zu gross.")
        if normal["denominator"] is not None and normal["denominator"] > MAX_ABS_VALUE:
            raise _refuse("denominator", "Der Nenner ist zu gross.")
        if normal["sum_sq"] is not None and normal["sum_sq"] > MAX_ABS_SUM_SQ:
            raise _refuse("sum_sq", "Die Quadratsumme ist zu gross.")
        observed = _instant(raw.get("observed_at"), "observed_at", None) \
            if not isinstance(raw.get("observed_at"), _dt.datetime) else raw["observed_at"]
        dims = schema.normalize_dims(raw.get("dims"))
        if any(store.safe_text(v) is None for v in dims.values() if v):
            raise _refuse("dims", "Die Dimensionen enthalten ung\u00fcltige Zeichen.")
        if metric["key"] not in metric_keys:
            metric_keys.append(metric["key"])
        # In store.MEASUREMENT_COLUMNS order without the per-batch columns.
        return (metric["id"], variant_id, row_run, observed, normal["value"], normal["count"],
                normal["denominator"], normal["sum_sq"], json.dumps(dims, ensure_ascii=False))

    def _write_rows(self, row: Dict[str, Any], rows: List[Any],
                    context: Tuple[Dict[str, Dict[str, Any]], Dict[str, str], set], *,
                    source: str, filename: Optional[str], run_id: Optional[str],
                    lines: Optional[List[int]] = None, default_variant: Optional[str] = None,
                    row_labels: Optional[List[Optional[str]]] = None,
                    row_paths: Optional[List[Optional[str]]] = None
                    ) -> Tuple[str, int, List[str]]:
        """Validate measurement rows against the experiment (``context``
        from _row_context) and each metric's kind, and write them as one
        batch; returns (batch id, rows written, metric keys).

        All or nothing. The batch row comes first, then the rows go into COPY
        in chunks of COPY_CHUNK_ROWS as they are validated, so a 200'000-row
        import never holds all of them as tuples. After the first refused row
        nothing more is written, the rest is only checked (up to 20 errors),
        and the ValidationError rolls the caller's transaction back with the
        batch and every chunk already written. Errors name the row
        ("Messwert 3: ...", the file line for CSV, or ``row_labels``) and are
        keyed ``rows.<i>.<field>``, or under ``row_paths`` (a run's
        ``metrics.<key>``, ``metrics.<key>.count``: the names its form uses).
        """
        metrics, variants, valid_runs = context
        # The batch records the metrics in the order the rows name them. It
        # is written before the rows, so the list is taken from the rows up
        # front: when every row is valid it is exactly the metrics they use.
        named = (r.get("metric") for r in rows if isinstance(r, dict))
        metric_keys = [k for k in dict.fromkeys(n for n in named if isinstance(n, str))
                       if k in metrics]
        batch_id = store.insert_batch(self.conn, experiment_id=row["id"], run_id=run_id,
                                      source=source, rows=len(rows), metric_keys=metric_keys,
                                      filename=filename, actor_id=self._actor_id)
        now = store.transaction_now(self.conn)
        actor_id = self._actor_id
        errors: List[str] = []
        fields: Dict[str, str] = {}
        chunk: List[tuple] = []
        valid = written = 0
        seen_keys: List[str] = []
        for i, raw in enumerate(rows):
            try:
                metric_id, variant_id, run, observed, value, count, denominator, sum_sq, dims = \
                    self._prepare_row(raw, metrics, variants, valid_runs, run_id, default_variant,
                                      seen_keys)
            except ValidationError as exc:
                label = row_labels[i] if row_labels and i < len(row_labels) else None
                path = row_paths[i] if row_paths and i < len(row_paths) else None
                where = label or (f"Zeile {lines[i]}" if lines else f"Messwert {i + 1}")
                errors.append(f"{where}: {exc.message}")
                for name, message in (exc.fields or {"row": exc.message}).items():
                    if path:
                        key = path if name in ("value", "row", "metric") else f"{path}.{name}"
                    else:
                        key = f"rows.{i}.{name}"
                    fields.setdefault(key, message)
                if len(errors) >= 20:
                    break
                continue
            valid += 1
            if errors:
                continue  # refused anyway: only look for further errors
            chunk.append((row["id"], metric_id, variant_id, run, batch_id, observed or now, value,
                          count, denominator, sum_sq, dims, source, actor_id))
            if len(chunk) >= COPY_CHUNK_ROWS:
                written += store.copy_measurements(self.conn, chunk)
                chunk = []
        if errors:
            more = len(errors) >= 20 and valid + len(errors) < len(rows)
            text = csv_import.join_messages(errors[:20], more)
            if lines:
                # A file: the list is the message, the file field points to it.
                raise ValidationError(text, fields={"file": csv_import.MSG_SEE_ERRORS})
            raise ValidationError(text, fields=dict(list(fields.items())[:20]))
        if chunk:
            written += store.copy_measurements(self.conn, chunk)
        return batch_id, written, metric_keys

    def _add_rows(self, key: Any, rows: List[Any], *, source: str, filename: Optional[str],
                  lines: Optional[List[int]], action: str) -> Dict[str, Any]:
        with self.conn.transaction():
            row = self._experiment(key, lock="no key update")
            context = self._row_context(row, rows)
            batch_id, inserted, metric_keys = self._write_rows(
                row, rows, context, source=source, filename=filename, run_id=None, lines=lines)
            store.touch_experiment(self.conn, row["id"])
            _queue_pipeline(self.conn, row["id"])
            _queue_index(self.conn, self.settings, row["id"])
        self._audit(action, "experiment", row["key"],
                    {"batch_id": batch_id, "rows": inserted, "metrics": metric_keys,
                     "source": source})
        return {"batch_id": batch_id, "inserted": inserted}

    def add_measurements(self, key: Any, data: Any, source: str = "manual") -> Dict[str, Any]:
        self._view()
        if source not in SOURCES:
            raise ValueError(f"unknown source {source!r}")
        data = _obj(data)
        rows = data.get("rows")
        if not isinstance(rows, list) or not rows:
            raise _refuse("rows", "Bitte mindestens einen Messwert senden.", "Messwerte")
        limit = int(self.settings.max_rows_per_request)
        if len(rows) > limit:
            raise _refuse("rows", f"H\u00f6chstens {kinds.format_plain(limit)} Messwerte je Anfrage; "
                                  "bitte aufteilen.", "Messwerte")
        return self._add_rows(key, rows, source=source, filename=None, lines=None,
                              action="experiments.measurements.add")

    def import_csv(self, key: Any, content: bytes, filename: Any) -> Dict[str, Any]:
        self._view()
        row = self._experiment(key)
        metrics = {m["key"]: {"kind": m["kind"], "definition": m["definition"], "name": m["name"]}
                   for m in store.assigned_metrics(self.conn, row["id"])}
        if not metrics:
            raise ValidationError("Das Experiment hat noch keine Metriken; bitte zuerst Metriken zuordnen.")
        variants = {v["key"] for v in store.list_variants(self.conn, row["id"])}
        # The run column may name a run instead of giving its id; the names
        # are read only when a cell is not an id.
        parsed = csv_import.parse_csv(content, metrics=metrics, variants=variants,
                                      max_rows=int(self.settings.max_csv_rows),
                                      runs=lambda: store.run_ids_by_name(self.conn, row["id"]))
        result = self._add_rows(row["key"], parsed["rows"], source="csv", filename=_filename(filename),
                                lines=parsed.get("lines"), action="experiments.measurements.import")
        result["ignored_columns"] = parsed["ignored_columns"]
        return result

    def list_batches(self, key: Any, after: Any = None, limit: Any = 50) -> Dict[str, Any]:
        self._view()
        row = self._experiment(key)
        items, next_after = store.list_batches(self.conn, row["id"], after=after or None,
                                               limit=_limit(limit, 50, 200))
        return {"items": items, "next_after": next_after}

    def delete_batch(self, key: Any, batch_id: Any) -> Dict[str, Any]:
        self._view()
        ident = _id(batch_id)
        with self.conn.transaction():
            row = self._experiment(key, lock="no key update")
            deleted = store.delete_batch(self.conn, row["id"], ident)
            if deleted is None:
                raise NotFound()
            store.touch_experiment(self.conn, row["id"])
            _queue_pipeline(self.conn, row["id"])
            _queue_index(self.conn, self.settings, row["id"])
        self._audit("experiments.batch.delete", "experiment", row["key"],
                    {"batch_id": ident, "rows": deleted})
        return {"deleted": deleted}

    # -- runs --------------------------------------------------------------

    def add_run(self, key: Any, data: Any, source: str = "manual") -> Dict[str, Any]:
        self._view()
        if source not in SOURCES:
            raise ValueError(f"unknown source {source!r}")
        data = _obj(data)
        name = _text(data, "name", limit=200, label="Name")
        status = data.get("status") or "finished"
        if status not in RUN_STATUSES:
            raise _refuse("status", "Erlaubt sind finished, failed und cancelled.", "Status")
        params = _json_object(data.get("params"), "params", "Parameter", MAX_RUN_JSON_BYTES)
        environment = _json_object(data.get("environment"), "environment", "Umgebung",
                                   MAX_RUN_JSON_BYTES)
        commit = _text(data, "commit", limit=200, label="Commit")
        started_at = _instant(data.get("started_at"), "started_at", "Beginn")
        ended_at = _instant(data.get("ended_at"), "ended_at", "Ende")
        if started_at and ended_at and ended_at < started_at:
            raise _refuse("ended_at", "Das Ende liegt vor dem Beginn.", "Ende")
        note = _text(data, "note", limit=MAX_NOTE, label="Notiz", multiline=True)
        rows: List[Any] = []
        # For rows from the metrics object: the metric key, so an error names
        # the metric and lands on its input (metrics.<key>), not "Messwert 2".
        row_metric_keys: List[Optional[str]] = []
        plain_numbers: List[str] = []
        raw_metrics = data.get("metrics")
        if raw_metrics is not None:
            if not isinstance(raw_metrics, dict):
                raise _refuse("metrics", "Muss ein Objekt {Metrik: Wert} sein.", "Metriken")
            for metric_key, value in raw_metrics.items():
                if isinstance(value, dict):
                    extra = [k for k in value if k not in _RUN_METRIC_KEYS]
                    if extra:
                        raise _refuse(f"metrics.{metric_key}",
                                      f"Unbekannte Angabe \u00ab{str(extra[0])[:40]}\u00bb.", "Metriken")
                    rows.append(dict(value, metric=metric_key))
                else:
                    rows.append({"metric": metric_key, "value": value})
                    plain_numbers.append(metric_key)
                row_metric_keys.append(str(metric_key))
        raw_rows = data.get("rows")
        if raw_rows is not None:
            if not isinstance(raw_rows, list):
                raise _refuse("rows", "Muss eine Liste sein.", "Messwerte")
            rows.extend(raw_rows)
            row_metric_keys.extend([None] * len(raw_rows))
        limit = int(self.settings.max_rows_per_request)
        if len(rows) > limit:
            raise _refuse("rows", f"H\u00f6chstens {kinds.format_plain(limit)} Messwerte je Lauf; "
                                  "bitte aufteilen.", "Messwerte")
        with self.conn.transaction():
            row = self._experiment(key, lock="no key update")
            variants = {v["key"]: v["id"] for v in store.list_variants(self.conn, row["id"])}
            variant = data.get("variant")
            if variant in ("",):
                variant = None
            if variant is not None and (not isinstance(variant, str) or variant not in variants):
                raise _refuse("variant", f"Unbekannte Variante \u00ab{str(variant)[:40]}\u00bb.", "Variante")
            # Locked like _prepare_rows does: the kind checked here is the one
            # the rows are validated and written with.
            metrics = {m["key"]: m for m in store.assigned_metrics(self.conn, row["id"], lock=True)}
            for metric_key in plain_numbers:
                metric = metrics.get(metric_key)
                if metric is not None and metric["kind"] not in kinds.MEAN_LIKE_KINDS:
                    # A bare number is one observation; other kinds need their
                    # count (trials, units) or denominator to mean anything.
                    raise _refuse(f"metrics.{metric_key}",
                                  f"\u00ab{metric['name']}\u00bb braucht value und count "
                                  f"(Art \u00ab{metric['kind_label']}\u00bb).", "Metriken")
            run_id = store.insert_run(
                self.conn, experiment_id=row["id"], variant_id=variants.get(variant) if variant else None,
                name=name, status=status, params=params, environment=environment, commit_ref=commit,
                source=source, started_at=started_at, ended_at=ended_at, actor_id=self._actor_id)
            batch_id = None
            inserted = 0
            if rows:
                row_labels = [None if k is None else
                              f"\u00ab{(metrics.get(k) or {}).get('name') or k[:48]}\u00bb"
                              for k in row_metric_keys]
                row_paths = [None if k is None else f"metrics.{k}" for k in row_metric_keys]
                batch_id, inserted, _ = self._write_rows(
                    row, rows, self._row_context(row, rows), source=source, filename=None,
                    run_id=run_id, default_variant=variant, row_labels=row_labels,
                    row_paths=row_paths)
                _queue_pipeline(self.conn, row["id"])
            if note:
                store.insert_note(self.conn, experiment_id=row["id"], kind="note", body=note,
                                  variant_id=variants.get(variant) if variant else None,
                                  run_id=run_id, actor_id=self._actor_id)
            store.touch_experiment(self.conn, row["id"])
            _queue_index(self.conn, self.settings, row["id"])
        self._audit("experiments.run.add", "experiment", row["key"],
                    {"run_id": run_id, "batch_id": batch_id, "rows": inserted,
                     "status": status, "source": source})
        return store.get_run(self.conn, row["id"], run_id)

    def list_runs(self, key: Any, after: Any = None, limit: Any = 50) -> Dict[str, Any]:
        self._view()
        row = self._experiment(key)
        items, next_after = store.list_runs(self.conn, row["id"], after=after or None,
                                            limit=_limit(limit, 50, 200))
        return {"items": items, "next_after": next_after}

    # -- notes -------------------------------------------------------------

    def add_note(self, key: Any, data: Any) -> Dict[str, Any]:
        self._view()
        data = _obj(data)
        body = _text(data, "body", limit=MAX_NOTE, label="Notiz", required=True, multiline=True)
        kind = data.get("kind") or "note"
        if kind not in USER_NOTE_KINDS:
            raise _refuse("kind", "Erlaubt sind " + ", ".join(USER_NOTE_KINDS) + ".", "Art")
        with self.conn.transaction():
            row = self._experiment(key, lock="no key update")
            variant_id = None
            if data.get("variant") not in (None, ""):
                variants = {v["key"]: v["id"] for v in store.list_variants(self.conn, row["id"])}
                variant_id = variants.get(data["variant"]) if isinstance(data["variant"], str) else None
                if variant_id is None:
                    raise _refuse("variant", "Diese Variante gibt es nicht.", "Variante")
            run_id = None
            if data.get("run_id") not in (None, ""):
                run_id = store.canonical_uuid(data["run_id"])
                if run_id is None or run_id not in store.run_ids_of(self.conn, row["id"], [run_id]):
                    raise _refuse("run_id", "Diesen Lauf gibt es in diesem Experiment nicht.", "Lauf")
            note_id = store.insert_note(self.conn, experiment_id=row["id"], kind=kind, body=body,
                                        variant_id=variant_id, run_id=run_id,
                                        actor_id=self._actor_id)
            store.touch_experiment(self.conn, row["id"])
            _queue_index(self.conn, self.settings, row["id"])
        self._audit("experiments.note.add", "experiment", row["key"], {"note_id": note_id, "kind": kind})
        return _public(store.get_note(self.conn, row["id"], note_id, actor=self.actor), "author_id")

    def delete_note(self, key: Any, note_id: Any) -> Dict[str, Any]:
        self._view()
        ident = _id(note_id)
        with self.conn.transaction():
            row = self._experiment(key, lock="no key update")
            note = store.get_note(self.conn, row["id"], ident, actor=self.actor)
            if note is None:
                raise NotFound()
            if not note["can_delete"]:
                raise Forbidden(MSG_NOTE_DELETE)
            store.delete_note(self.conn, row["id"], ident)
            store.touch_experiment(self.conn, row["id"])
            _queue_index(self.conn, self.settings, row["id"])
        self._audit("experiments.note.delete", "experiment", row["key"],
                    {"note_id": ident, "kind": note["kind"]})
        return {"deleted": ident}

    # -- evaluations -------------------------------------------------------

    def run_evaluation(self, key: Any, data: Any, trigger: str = "manual") -> Dict[str, Any]:
        self._view()
        if trigger not in TRIGGERS:
            raise ValueError(f"unknown trigger {trigger!r}")
        data = _obj(data)
        evaluator = _evaluator_by_key(self.conn, data.get("evaluator"))
        if evaluator is None or evaluator["archived"]:
            raise _refuse("evaluator", "Diesen Auswerter gibt es nicht.", "Auswerter")
        with _read_view(self.conn):
            snapshot = store.load_snapshot(self.conn, _key(key))
        if snapshot is None:
            raise NotFound()
        metric = next((m for m in snapshot["metrics"] if m["key"] == data.get("metric")), None)
        if metric is None:
            raise _refuse("metric", "Die Metrik ist diesem Experiment nicht zugeordnet.", "Metrik")
        if metric["kind"] not in evaluator["input_kinds"]:
            raise _refuse("metric", _kind_message(evaluator, metric), "Metrik")
        params = evaluators.validate_params(evaluator["params_schema"], _params(data.get("params")))
        _check_target(evaluator, metric, params)
        scope = schema.validate_scope(data.get("scope"))
        evaluation_id, _, _ = _start_evaluation(
            self.conn, self.settings, self.runner, snapshot, metric, evaluator, params, scope,
            trigger=trigger, requested_by=self._actor_id, reuse=False, check_health=True)
        self._audit("experiments.evaluation.run", "experiment", snapshot["key"],
                    {"evaluation_id": evaluation_id, "evaluator": evaluator["key"],
                     "metric": metric["key"], "trigger": trigger})
        return store.get_evaluation(self.conn, snapshot["id"], evaluation_id)

    def run_pipeline(self, key: Any, data: Any = None, trigger: str = "manual") -> List[Dict[str, Any]]:
        self._view()
        if trigger not in TRIGGERS:
            raise ValueError(f"unknown trigger {trigger!r}")
        data = _obj(data)
        override = None
        if data.get("scope") is not None:
            override = schema.validate_scope(data["scope"])
        with _read_view(self.conn):
            loaded = store.load_snapshot_with_definition(self.conn, _key(key))
        if loaded is None:
            raise NotFound()
        snapshot, definition = loaded
        results, created = _pipeline(self.conn, self.settings, self.runner, snapshot, definition,
                                     override, trigger=trigger, requested_by=self._actor_id)
        pruned = 0
        if trigger == "api" and created:
            # CI evaluates after every run it logs; the pipeline job then
            # reuses these evaluations and prunes nothing, so retention runs
            # here too -- unless the job is running now (it prunes itself).
            lock = f"experiments.pipeline:{snapshot['id']}"
            if store.try_advisory_lock(self.conn, lock):
                try:
                    pruned = store.prune_pipeline_evaluations(self.conn, snapshot["id"],
                                                              keep=PIPELINE_KEEP)
                finally:
                    store.advisory_unlock(self.conn, lock)
        detail = {"trigger": trigger, "created": created,
                  "evaluations": len([r for r in results if r.get("id")])}
        if pruned:
            detail["pruned"] = pruned
        self._audit("experiments.pipeline.run", "experiment", snapshot["key"], detail)
        return results

    def get_evaluation(self, key: Any, evaluation_id: Any) -> Dict[str, Any]:
        self._view()
        ident = _id(evaluation_id)
        row = self._experiment(key)
        found = store.get_evaluation(self.conn, row["id"], ident, with_logs=True)
        if found is None:
            raise NotFound()
        return found

    # -- decisions ---------------------------------------------------------

    def decide(self, key: Any, data: Any) -> Dict[str, Any]:
        self._view()
        data = _obj(data)
        verdict = _choice(data.get("verdict"), "verdict", labels.DECISION_VERDICT_LABELS,
                          "Bitte ein Ergebnis w\u00e4hlen (ship, iterate, stop, inconclusive).",
                          "Entscheidung")
        rationale = _text(data, "rationale", limit=MAX_DECISION_TEXT, label="Begr\u00fcndung",
                          multiline=True)
        learning = _text(data, "learning", limit=MAX_DECISION_TEXT, label="Erkenntnis", multiline=True)
        with self.conn.transaction():
            row = self._experiment(key, lock="update")
            self._check_row_version(row, data, required=False)
            definition = row["definition"]
            if (definition.get("decision") or {}).get("require_learning") and not learning:
                raise ValidationError(MSG_LEARNING_REQUIRED, fields={"learning": MSG_LEARNING_REQUIRED})
            decided_state = schema.phase_state(definition, "decided")
            changes: Dict[str, Any] = {}
            if decided_state is not None:
                targets = {t["to"] for t in schema.transitions_from(definition, row["status"])}
                if decided_state not in targets:
                    raise ValidationError(MSG_NO_DECISION_HERE)
                facts = self._facts(row)
                facts["decision"] = True
                facts["learning"] = bool(learning)
                missing = schema.check_transition(definition, row["status"], decided_state,
                                                  facts=facts, roles=self._roles)
                if missing:
                    message = " ".join(missing)
                    raise ValidationError(message, fields={"verdict": message})
                changes = {"status": decided_state, "decided_at": store.NOW, "ended_at": store.NOW}
            decision_id = store.insert_decision(self.conn, experiment_id=row["id"], verdict=verdict,
                                                rationale=rationale, learning=learning,
                                                actor_id=self._actor_id)
            store.update_experiment_state(self.conn, row["id"], changes)
            _queue_index(self.conn, self.settings, row["id"])
        self._audit("experiments.experiment.decide", "experiment", row["key"],
                    {"decision_id": decision_id, "verdict": verdict, "from": row["status"],
                     "to": decided_state})
        return self._snapshot(row["key"])

    # -- lifecycle and operations ------------------------------------------

    def delete_experiment(self, key: Any) -> Dict[str, Any]:
        self._manage()
        with self.conn.transaction():
            row = self._experiment(key, lock="update")
            pointer = indexer.pointer_for(self.settings, row["domain_key"], row["key"])
            unindex = row["index_state"] != "off" or row["indexed_at"] is not None
            if unindex:
                # Delayed past any index job that may be uploading right now.
                JobQueue(self.conn).enqueue(
                    "unindex", {"pointer": pointer, "experiment_id": row["id"]},
                    dedupe_key=f"unindex:{pointer}", delay_seconds=UNINDEX_DELAY_SECONDS,
                    priority=10)
            store.delete_pending_jobs(self.conn, [f"index:{row['id']}", f"pipeline:{row['id']}"])
            store.delete_experiment(self.conn, row["id"])
        self._audit("experiments.experiment.delete", "experiment", row["key"],
                    {"experiment_id": row["id"], "domain": row["domain_key"], "unindex": unindex})
        return {"deleted": row["key"]}

    def reindex(self, key: Any) -> Dict[str, Any]:
        self._view()
        with self.conn.transaction():
            row = self._experiment(key)
            queued = _queue_index(self.conn, self.settings, row["id"], priority=10, delay_seconds=0)
        self._audit("experiments.experiment.reindex", "experiment", row["key"], {"queued": queued})
        return {"queued": queued}

    def activity(self, key: Any, limit: Any = 50) -> List[Dict[str, Any]]:
        self._view()
        row = self._experiment(key)
        out = []
        for item in store.activity_rows(self.conn, row["key"], _limit(limit, 50, 500)):
            out.append({"at": item["at"], "action": item["action"],
                        "label": ACTIVITY_LABELS.get(item["action"], item["action"]),
                        "actor": item["actor"] if item["actor"] is not None else SYSTEM_ACTOR,
                        "detail": item["detail"]})
        return out

    def get_timeseries(self, key: Any, metric_key: Any, bucket: Any = "week",
                       scope: Any = None) -> List[Dict[str, Any]]:
        self._view()
        row = self._experiment(key)
        metric = next((m for m in store.assigned_metrics(self.conn, row["id"])
                       if m["key"] == metric_key), None)
        if metric is None:
            raise NotFound("Die Metrik gibt es in diesem Experiment nicht.")
        if bucket not in store.TIMESERIES_BUCKETS:
            raise _refuse("bucket", "Erlaubt sind day, week und month.", "Intervall")
        return store.timeseries(self.conn, row["id"], metric["id"], kind=metric["kind"],
                                bucket=bucket, scope=scope)

    # -- search ------------------------------------------------------------

    def search(self, q: Any, limit: Any = 30) -> Dict[str, Any]:
        self._view()
        words = _words(q)
        limit = _limit(limit, 30, 100)
        if not words:
            return {"source": "database", "warning": None, "items": []}
        warning = None
        knovas: List[Tuple[str, str]] = []
        use_knovas = self.knovas_search is not None and self.settings.index_enabled
        groups = tuple(getattr(self.settings, "index_access_groups", ()) or ())
        if use_knovas and groups:
            mine = set(store.user_access_groups(self.conn, self._actor_id))
            if not mine & set(groups):
                use_knovas = False
                warning = MSG_MISSING_GROUP
        if use_knovas:
            try:
                answer = self.knovas_search(" ".join(words), min(200, limit * 5)) or {}
                knovas = self._knovas_hits(answer)
            except Exception:  # noqa: BLE001 - the database search still answers
                logger.warning("Knovas search for experiments failed.", exc_info=True)
                use_knovas = False
                warning = MSG_KNOVAS_DOWN
        database = store.search_database(self.conn, words, limit)
        order: List[str] = []
        snippets: Dict[str, str] = {}
        for key, text in knovas:
            if key not in snippets:
                order.append(key)
                snippets[key] = text
        from_database = 0
        for key, text in database:
            if key not in snippets:
                order.append(key)
                snippets[key] = _snippet(text, words)
                from_database += 1
        order = order[:limit]
        summaries = store.summaries_by_keys(self.conn, order)
        items = []
        for key in order:
            summary = summaries.get(key)
            if summary is None:
                continue
            item = dict(summary)
            item["snippet"] = snippets.get(key) or ""
            items.append(item)
        if use_knovas:
            source = "knovas+database" if from_database else "knovas"
        else:
            source = "database"
        return {"source": source, "warning": warning, "items": items}

    def _knovas_hits(self, answer: Any) -> List[Tuple[str, str]]:
        prefix = self.settings.pointer_prefix
        out: List[Tuple[str, str]] = []
        for hit in (answer.get("results") if isinstance(answer, dict) else None) or []:
            if not isinstance(hit, dict):
                continue
            key = None
            for field in search_mod.ROW_POINTER_FIELDS:
                key = search_mod.parse_pointer(prefix, hit.get(field))
                if key is not None:
                    break
            if key is None:
                continue
            out.append((key, _chunk_text(hit.get("top_chunks"))))
        return out

    # -- tokens ------------------------------------------------------------

    def list_tokens(self) -> List[Dict[str, Any]]:
        self._view()
        return store.list_tokens(self.conn, self._actor_id)

    def create_token(self, data: Any) -> Dict[str, Any]:
        self._view()
        data = _obj(data)
        name = _text(data, "name", limit=80, label="Name", required=True)
        days = _int_value(data.get("expires_days"), "expires_days", lo=1, hi=365,
                          label="G\u00fcltigkeit in Tagen", default=TOKEN_DEFAULT_DAYS)
        plaintext = store.TOKEN_PREFIX + secrets.token_urlsafe(32)
        hint = store.TOKEN_PREFIX + "\u2026" + plaintext[-4:]
        token = store.insert_token(self.conn, user_id=self._actor_id, name=name,
                                   token_hash=store.hash_token(plaintext), hint=hint,
                                   expires_days=days)
        self._audit("experiments.token.create", "exp_token", token["id"], {"expires_days": days})
        token["token"] = plaintext
        return token

    def revoke_token(self, token_id: Any) -> Dict[str, Any]:
        self._view()
        token = store.revoke_token(self.conn, self._actor_id, _id(token_id))
        if token is None:
            raise NotFound()
        self._audit("experiments.token.revoke", "exp_token", token["id"], {})
        return token

    # -- preferences and settings ------------------------------------------

    def get_preferences(self) -> Dict[str, Any]:
        self._view()
        return {"show_in_search": store.get_user_show_in_search(self.conn, self._actor_id)}

    def update_preferences(self, data: Any) -> Dict[str, Any]:
        self._view()
        data = _obj(data)
        if set(data) != {"show_in_search"}:
            raise _refuse("show_in_search", "Erlaubt ist nur show_in_search (true oder false).")
        value = _flag(data, "show_in_search", "In der Suche zeigen")
        store.set_user_show_in_search(self.conn, self._actor_id, value)
        self._audit("experiments.preferences.update", "exp_settings",
                    USER_PREF_SHOW_IN_SEARCH.format(user_id=self._actor_id), {"show_in_search": value})
        return self.get_preferences()

    def get_settings(self) -> Dict[str, Any]:
        self._view()
        return {"show_in_search": store.get_runtime_setting(self.conn, "experiments.show_in_search")}

    def update_settings(self, data: Any) -> Dict[str, Any]:
        self._manage()
        data = _obj(data)
        if set(data) != {"show_in_search"} or not isinstance(data["show_in_search"], bool):
            raise _refuse("show_in_search", "Erlaubt ist genau show_in_search (true oder false).")
        key = "experiments.show_in_search"
        store.set_runtime_setting(self.conn, key, data["show_in_search"], self.actor)
        self._audit("experiments.settings.update", "exp_settings", key,
                    {"show_in_search": data["show_in_search"]})
        return self.get_settings()

    def index_status(self) -> Dict[str, Any]:
        self._manage()
        queue = JobQueue(self.conn)
        groups = list(getattr(self.settings, "index_access_groups", ()) or ())
        return {
            "enabled": bool(self.settings.index_enabled),
            "unrestricted": bool(self.settings.index_unrestricted),
            "access_groups": groups,
            "counts": store.index_state_counts(self.conn),
            # Documents of deleted experiments still in Knovas (the worker's
            # maintenance repeats their deletion).
            "orphans": tasks.orphan_count(self.conn),
            "jobs": queue.counts(),
            "failures": queue.recent_failures(),
            "access_warnings": store.viewers_without_groups(self.conn, groups) if groups else [],
            "runner": self.runner.health() if self.runner is not None else {"configured": False},
        }

    def reindex_all(self) -> Dict[str, Any]:
        self._manage()
        if not self.settings.index_enabled:
            return {"queued": 0}
        ids = sorted(store.experiments_for_reindex(self.conn, include_purged=True))
        queued = 0
        # Chunks of their own: a person's edit never waits long behind a
        # transaction that holds the row locks of every experiment.
        for start in range(0, len(ids), REINDEX_CHUNK):
            with self.conn.transaction():
                queued += _queue_index_many(self.conn, self.settings, ids[start:start + REINDEX_CHUNK],
                                            priority=200, delay_seconds=0)
        self._audit("experiments.index.reindex_all", "exp_settings", "index", {"queued": queued})
        return {"queued": queued}

    # -- packs -------------------------------------------------------------

    def list_packs(self) -> List[Dict[str, Any]]:
        self._view()
        out = []
        for item in packs.available_packs():
            entry = dict(item)
            if item.get("domain_key"):
                entry["installed"] = store.get_domain(self.conn, item["domain_key"]) is not None
            else:
                try:
                    entry["installed"] = store.pack_installed(self.conn, packs.load_pack(item["name"]))
                except (NotFound, ValidationError):
                    entry["installed"] = False
            out.append(entry)
        return out

    def install_pack(self, name: Any) -> Dict[str, Any]:
        self._manage()
        pack = packs.load_pack(name)
        counts = store.install_pack(self.conn, pack, actor_id=self._actor_id, update_existing=False)
        domain_key = (pack.get("domain") or {}).get("key")
        self._audit("experiments.pack.install", "exp_domain" if domain_key else None, domain_key,
                    dict(counts, pack=pack["pack"]))
        return counts

    def import_pack(self, data: Any) -> Dict[str, Any]:
        self._manage()
        data = _obj(data)
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _refuse("text", "Bitte den Text des Pakets angeben.", "Paket")
        pack = packs.parse_pack_text(text, known_metrics=store.global_metric_keys(self.conn),
                                     known_evaluators=store.evaluator_keys(self.conn))
        if any(e["key"].startswith("builtin.") for e in pack.get("evaluators") or []):
            raise _refuse("text", "Schl\u00fcssel mit \u00abbuiltin.\u00bb sind den eingebauten "
                                  "Auswertern vorbehalten.", "Paket")
        domain = pack.get("domain")
        before = store.get_domain(self.conn, domain["key"]) if domain else None
        changed: Dict[str, List[str]] = {}
        counts = store.install_pack(self.conn, pack, actor_id=self._actor_id, update_existing=True,
                                    changed_out=changed)
        # The Knovas copies show the domain's name, the type's name and the
        # metrics' names, kinds, units, directions and level labels: re-upload
        # every experiment an import changed any of them for, as the editors
        # do. After the install committed, for the reason given in
        # update_metric.
        ids: set = set()
        requeued = 0
        with self.conn.transaction():
            if before is not None and before["name"] != domain["name"]:
                ids.update(store.experiments_for_reindex(self.conn, domain_id=before["id"]))
            for metric_id in changed.get("metric_ids") or []:
                ids.update(store.experiments_using_metric(self.conn, metric_id))
            for type_id in changed.get("type_ids") or []:
                ids.update(store.experiments_for_reindex(self.conn, type_id=type_id))
            if ids:
                requeued = _queue_index_many(self.conn, self.settings, sorted(ids), priority=200)
        self._audit("experiments.pack.import", "exp_domain" if domain else None,
                    domain["key"] if domain else None,
                    dict(counts, pack=pack["pack"], requeued=requeued))
        return counts


def _chunk_text(chunks: Any, width: int = 300) -> str:
    """The first text of a Knovas hit's top_chunks (str, or dict with text,
    snippet or content), flattened and cut to ``width`` characters."""
    for chunk in chunks if isinstance(chunks, list) else []:
        text = chunk if isinstance(chunk, str) else None
        if isinstance(chunk, dict):
            text = next((chunk[f] for f in ("text", "snippet", "content")
                         if isinstance(chunk.get(f), str) and chunk[f].strip()), None)
        if text and text.strip():
            flat = " ".join(text.split())
            return flat if len(flat) <= width else flat[:width - 1].rstrip() + "\u2026"
    return ""


def _snippet(text: str, words: Sequence[str], width: int = 300) -> str:
    """About ``width`` characters of ``text`` around the first search word."""
    flat = " ".join(str(text or "").split())
    if len(flat) <= width:
        return flat
    lower = flat.lower()
    hit = min((p for p in (lower.find(w.lower()) for w in words) if p >= 0), default=0)
    start = max(0, hit - width // 3)
    piece = flat[start:start + width - 2].strip()
    return ("\u2026" if start > 0 else "") + piece + ("\u2026" if start + width - 2 < len(flat) else "")
