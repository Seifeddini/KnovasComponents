"""Writes each experiment into the Knovas index as one Markdown document.

The document carries what people search for -- hypothesis, description,
fields, variants, results, runs, notes, decisions and learnings -- and nothing
that identifies a person: no owner, no author, no e-mail address. Knovas is a
separate system with its own retention; names do not need to travel there.

Visibility in Knovas is fail-closed: every upload carries
EXPERIMENTS_ACCESS_GROUPS, and without a group the indexer refuses to upload
(unless EXPERIMENTS_INDEX_UNRESTRICTED says a folder rule restricts the prefix
instead). The Platform additionally strips experiment hits from the search of
everyone without a viewing role (search.SearchIntegration).

Uploads use a second, unsigned KnovasAPIClient (no principal broker), like
RemoteController: the worker thread has no signed-in user to assert.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md (section 11)
"""

from __future__ import annotations

import email.utils
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from experiments import jobs, kinds, labels
from experiments import search as search_mod
from experiments.errors import Unavailable
from experiments.jobs import PermanentError, RetryLater

logger = logging.getLogger(__name__)

MAX_PART_CHARS = 40_000
MAX_DOCUMENT_CHARS = 400_000
MAX_RUNS = 50
MAX_TITLE_CHARS = 500
MAX_DESCRIPTION_CHARS = 2000
MAX_PATH_CHARS = 2000
PATH_ROOT = "/Experimente/"
TRUNCATED_LINE = "Gek\u00fcrzt."
DOT = " \u00b7 "
DASH = " \u2013 "

MSG_NO_GROUP = (
    "F\u00fcr Experimente ist keine Knovas-Zugriffsgruppe festgelegt "
    "(EXPERIMENTS_ACCESS_GROUPS)."
)
MSG_UNREACHABLE = "Knovas nicht erreichbar."
MSG_REJECTED = "Knovas hat das Dokument abgelehnt (HTTP {code})."
MSG_DELETE_REJECTED = "Knovas hat das L\u00f6schen abgelehnt (HTTP {code})."
MSG_TEMPORARY = "Knovas hat die Anfrage vor\u00fcbergehend nicht angenommen (HTTP {code})."
MSG_BUSY = "Knovas ist ausgelastet; neuer Versuch folgt."
MSG_RATE_SLOT = "Warten auf den n\u00e4chsten freien Upload-Platz."
MSG_NO_CLIENT = "Kein Knovas-Zugang eingerichtet."
MSG_BAD_KEY = "Das Experiment hat keinen g\u00fcltigen Schl\u00fcssel f\u00fcr Knovas."
MSG_UPLOAD_RUNNING = "Das Experiment wird gerade hochgeladen; neuer Versuch folgt."
MSG_LISTING_UNREACHABLE = (
    "Knovas konnte die Liste der Experiment-Dokumente nicht liefern; "
    "der Befehl kann wiederholt werden."
)
MSG_LISTING_REFUSED = (
    "Knovas hat die Liste der Dokumente abgelehnt (HTTP {code}); nicht erfasste "
    "Experiment-Dokumente wurden nicht gesucht."
)

DEFAULT_RETRY_AFTER = 60.0
MAX_RETRY_AFTER = 3600.0
#: Statuses that say the request may succeed later. 401/403 come from the
#: caller's credentials (a certificate being renewed, a group not set up yet),
#: not from the document, so they are retried like an outage.
TEMPORARY_STATUSES = (401, 403, 408, 409, 425)
#: One upload per experiment at a time, across every worker of every process
#: (a session advisory lock on the worker's connection, released on unlock or
#: when the connection ends). A second job for the same experiment waits this
#: long and then reads the snapshot afresh, so the newest content is always
#: the one uploaded last.
UPLOAD_LOCK_PREFIX = "experiments.index:"
UPLOAD_LOCK_RETRY_SECONDS = 10.0

try:  # Dates the way people in the firm read them; UTC when tzdata is missing.
    from zoneinfo import ZoneInfo

    _LOCAL_TZ: Any = ZoneInfo("Europe/Zurich")
except Exception:  # noqa: BLE001
    _LOCAL_TZ = timezone.utc


def _store():
    # Imported late: the store is another part's module, and tests replace it.
    from experiments import store

    return store


def make_index_client(config: Any):
    """The client uploads go through: its own instance, no principal broker."""
    from knovas_client import KnovasAPIClient

    return KnovasAPIClient(config)


def pointer_for(settings: Any, domain_key: str, key: str) -> str:
    return f"{settings.pointer_prefix}/{domain_key}/{key}"


# -- text helpers ---------------------------------------------------------------


def _line(value: Any) -> str:
    """One line: every run of whitespace (newlines included) becomes a space."""
    return " ".join(str(value or "").split())


def _block(value: Any) -> str:
    """Free text kept as paragraphs: normalised line ends, trimmed."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(part.rstrip() for part in text.split("\n")).strip()


def _date(value: Any) -> str:
    """TT.MM.JJJJ in Swiss local time; '' when the value is not a timestamp."""
    if value is None or value == "":
        return ""
    moment: Optional[datetime] = None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).strip())
        except ValueError:
            return ""
    if moment.tzinfo is not None:
        moment = moment.astimezone(_LOCAL_TZ)
    return moment.strftime("%d.%m.%Y")


def _decimals(metric: Dict[str, Any]) -> Optional[int]:
    definition = metric.get("definition") or {}
    value = definition.get("decimals") if isinstance(definition, dict) else None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _value(metric: Dict[str, Any], x: Any) -> str:
    return kinds.format_value(str(metric.get("kind") or ""), x, str(metric.get("unit") or ""),
                              _decimals(metric))


def _count(n: Any) -> str:
    return kinds.format_number(n, 0)


def _level_label(metric: Dict[str, Any], level: str) -> str:
    definition = metric.get("definition") or {}
    levels = definition.get("levels") if isinstance(definition, dict) else None
    if isinstance(levels, dict) and level in levels:
        return _line(levels[level])
    return str(level)


def _scope_key(scope: Any) -> str:
    try:
        return json.dumps(scope or {}, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return repr(scope)


# -- the Markdown document -----------------------------------------------------


class _Item:
    """One droppable or fixed piece of a section."""

    __slots__ = ("text", "category", "age")

    def __init__(self, text: str, category: Optional[str] = None, age: int = 0) -> None:
        self.text = text
        self.category = category
        #: Larger = older; the cap drops the oldest first.
        self.age = age


class _Section:
    __slots__ = ("heading", "items", "joiner")

    def __init__(self, heading: str, items: List[_Item], joiner: str) -> None:
        self.heading = heading
        self.items = items
        self.joiner = joiner

    def text(self) -> str:
        return f"## {self.heading}\n\n" + self.joiner.join(i.text for i in self.items)

    def length(self) -> int:
        if not self.items:
            return 0
        body = sum(len(i.text) for i in self.items) + len(self.joiner) * (len(self.items) - 1)
        return len(self.heading) + 5 + body


def _header(snapshot: Dict[str, Any]) -> str:
    key = _line(snapshot.get("key"))
    title = _line(snapshot.get("title"))
    domain = snapshot.get("domain") or {}
    type_ = snapshot.get("type") or {}
    facts = [
        f"Experiment im Bereich {_line(domain.get('name'))}",
        f"Typ {_line(type_.get('name'))}",
        f"Status {_line(snapshot.get('status_label') or snapshot.get('status'))}",
    ]
    updated = _date(snapshot.get("updated_at"))
    if updated:
        facts.append(f"aktualisiert {updated}")
    tags = [_line(t) for t in (snapshot.get("tags") or []) if _line(t)]
    if tags:
        facts.append("Schlagw\u00f6rter: " + ", ".join(tags))
    return f"# {key}{DOT}{title}\n\n" + DOT.join(facts)


def _field_items(snapshot: Dict[str, Any]) -> List[_Item]:
    items = []
    for field in snapshot.get("fields") or []:
        if not isinstance(field, dict):
            continue
        value = field.get("value")
        if value is None or value == "" or value == []:
            continue
        display = _line(field.get("display"))
        if not display or display == kinds.DASH:
            continue
        items.append(_Item(f"- {_line(field.get('label') or field.get('key'))}: {display}"))
    return items


def _variant_items(snapshot: Dict[str, Any]) -> List[_Item]:
    items = []
    for variant in snapshot.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        head = _line(variant.get("key"))
        if variant.get("is_control"):
            head += " (Kontrolle)"
        name = _line(variant.get("name"))
        description = _line(variant.get("description"))
        text = f"- {head}"
        if name or description:
            text += ": " + name
            if description:
                text += (DASH if name else "") + description
        items.append(_Item(text))
    return items


def _metric_values(metric: Dict[str, Any]) -> str:
    aggregates = [a for a in (metric.get("aggregates") or []) if isinstance(a, dict)]
    if not aggregates:
        return "noch keine Messwerte"
    parts = []
    for agg in aggregates:
        variant = _line(agg.get("variant")) or "ohne Variante"
        n = _count(agg.get("n"))
        levels = agg.get("levels")
        if metric.get("kind") == "categorical" and isinstance(levels, dict) and levels:
            shares = ", ".join(f"{_level_label(metric, str(level))} {_count(units)}"
                               for level, units in levels.items())
            parts.append(f"{variant}: {shares} (n = {n})")
        else:
            parts.append(f"{variant}: {_value(metric, agg.get('estimate'))} (n = {n})")
    return "; ".join(parts)


def _metric_items(snapshot: Dict[str, Any]) -> List[_Item]:
    items = []
    for metric in snapshot.get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        role = str(metric.get("role") or "")
        role_label = _line(metric.get("role_label") or labels.METRIC_ROLE_LABELS.get(role, role))
        op, bound = metric.get("guardrail_op"), metric.get("guardrail_value")
        if role == "guardrail" and op in ("max", "min") and bound is not None:
            role_label += f" {'<=' if op == 'max' else '>='} {_value(metric, bound)}"
        facets = [
            _line(metric.get("kind_label") or metric.get("kind")),
            _line(metric.get("direction_label")
                  or labels.DIRECTION_LABELS.get(str(metric.get("direction") or ""), "")),
            role_label,
        ]
        facets = [f for f in facets if f]
        text = f"- {_line(metric.get('name') or metric.get('key'))} ({', '.join(facets)}): "
        text += _metric_values(metric)
        if metric.get("guardrail_status") == "violated":
            text += DASH + "Leitplanke verletzt"
        items.append(_Item(text))
    return items


def _metric_index(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(m.get("key")): m for m in (snapshot.get("metrics") or [])
            if isinstance(m, dict) and m.get("key")}


def _evaluation_items(snapshot: Dict[str, Any]) -> List[_Item]:
    """The newest finished evaluation per (evaluator, metric, scope).

    An evaluation the snapshot marks ``superseded`` (a newer one of its
    group exists) is never the current result, so it is left out even when
    it is the newest finished one.
    """
    metrics = _metric_index(snapshot)
    seen = set()
    items = []
    for ev in snapshot.get("evaluations") or []:  # newest first
        if not isinstance(ev, dict) or ev.get("status") != "done":
            continue
        if ev.get("superseded") is True:
            continue
        ident = (ev.get("evaluator_key"), ev.get("metric_key"), _scope_key(ev.get("scope")))
        if ident in seen:
            continue
        seen.add(ident)
        title = [_line(ev.get("evaluator_name") or ev.get("evaluator_key"))]
        metric_key = ev.get("metric_key")
        if metric_key:
            title.append(_line((metrics.get(str(metric_key)) or {}).get("name") or metric_key))
        when = _date(ev.get("finished_at") or ev.get("created_at"))
        if when:
            title.append(when)
        paragraphs = ["### " + DASH.join(t for t in title if t)]
        headline = _line(ev.get("headline"))
        verdict = str(ev.get("verdict") or "")
        verdict_label = labels.EVALUATION_VERDICT_LABELS.get(verdict, "")
        if headline:
            if verdict and verdict != "n/a" and verdict_label:
                headline += f" ({verdict_label})"
            paragraphs.append(headline)
        output = ev.get("output")
        summary = _block(output.get("summary")) if isinstance(output, dict) else ""
        if summary:
            paragraphs.append(summary)
        items.append(_Item("\n\n".join(paragraphs), "evaluation", len(items)))
    return items


def _run_items(snapshot: Dict[str, Any]) -> List[_Item]:
    metrics = _metric_index(snapshot)
    items = []
    for run in (snapshot.get("runs") or [])[:MAX_RUNS]:  # newest first
        if not isinstance(run, dict):
            continue
        facts = [_line(run.get("variant"))]
        facts.append(_date(run.get("started_at") or run.get("created_at")))
        if run.get("status") and run.get("status") != "finished":
            facts.append(_line(run.get("status_label") or run.get("status")))
        facts = [f for f in facts if f]
        text = f"- {_line(run.get('name')) or 'Lauf'}"
        if facts:
            text += f" ({', '.join(facts)})"
        values = []
        run_metrics = run.get("metrics")
        if isinstance(run_metrics, dict):
            for metric_key, estimate in run_metrics.items():
                metric = metrics.get(str(metric_key)) or {"key": metric_key}
                values.append(f"{_line(metric.get('name') or metric_key)}: {_value(metric, estimate)}")
        if values:
            text += ": " + "; ".join(values)
        items.append(_Item(text, "run", len(items)))
    return items


def _note_items(snapshot: Dict[str, Any]) -> List[_Item]:
    items = []
    for note in snapshot.get("notes") or []:  # newest first
        if not isinstance(note, dict):
            continue
        body = _block(note.get("body"))
        if not body:
            continue
        kind = str(note.get("kind") or "note")
        title = [_line(note.get("kind_label") or labels.NOTE_KIND_LABELS.get(kind, kind))]
        when = _date(note.get("created_at"))
        if when:
            title.append(when)
        items.append(_Item("### " + DASH.join(title) + "\n\n" + body, "note", len(items)))
    return items


def _decision_items(snapshot: Dict[str, Any]) -> List[_Item]:
    items = []
    for decision in snapshot.get("decisions") or []:
        if not isinstance(decision, dict):
            continue
        verdict = str(decision.get("verdict") or "")
        title = [_line(decision.get("verdict_label")
                       or labels.DECISION_VERDICT_LABELS.get(verdict, verdict))]
        when = _date(decision.get("decided_at"))
        if when:
            title.append(when)
        paragraphs = ["### " + DASH.join(t for t in title if t)]
        rationale = _block(decision.get("rationale"))
        learning = _block(decision.get("learning"))
        if rationale:
            paragraphs.append("Begr\u00fcndung: " + rationale)
        if learning:
            paragraphs.append("Erkenntnis: " + learning)
        items.append(_Item("\n\n".join(paragraphs)))
    return items


def _sections(snapshot: Dict[str, Any]) -> List[_Section]:
    hypothesis = _block(snapshot.get("hypothesis"))
    description = _block(snapshot.get("description"))
    return [
        _Section("Hypothese", [_Item(hypothesis)] if hypothesis else [], "\n\n"),
        _Section("Beschreibung", [_Item(description)] if description else [], "\n\n"),
        _Section("Angaben", _field_items(snapshot), "\n"),
        _Section("Varianten", _variant_items(snapshot), "\n"),
        _Section("Metriken", _metric_items(snapshot), "\n"),
        _Section("Auswertungen", _evaluation_items(snapshot), "\n\n"),
        _Section("L\u00e4ufe", _run_items(snapshot), "\n"),
        _Section("Notizen", _note_items(snapshot), "\n\n"),
        _Section("Entscheidungen", _decision_items(snapshot), "\n\n"),
    ]


def _total_length(header: str, sections: List[_Section]) -> int:
    # header, then "\n\n" + section for each non-empty one, then a final "\n".
    return len(header) + sum(s.length() + 2 for s in sections if s.items) + 1


def _assemble(header: str, sections: List[_Section]) -> str:
    return "\n\n".join([header] + [s.text() for s in sections if s.items]) + "\n"


def render_markdown(snapshot: Dict[str, Any], *, max_chars: int = MAX_DOCUMENT_CHARS) -> str:
    """The experiment as Markdown, at most ``max_chars`` long.

    Over the cap, older evaluations go first, then runs, then notes (oldest
    first each); what is still too long is cut with the line "Gekuerzt.".
    """
    header = _header(snapshot)
    sections = _sections(snapshot)
    total = _total_length(header, sections)
    if total > max_chars:
        droppable = {s.items[0].category: s for s in sections if s.items and s.items[0].category}
        for category in ("evaluation", "run", "note"):
            section = droppable.get(category)
            if section is None:
                continue
            while section.items and total > max_chars:
                oldest = max(range(len(section.items)), key=lambda i: section.items[i].age)
                del section.items[oldest]
                total = _total_length(header, sections)
            if total <= max_chars:
                break
    text = _assemble(header, sections)
    if len(text) <= max_chars:
        return text
    tail = "\n\n" + TRUNCATED_LINE + "\n"
    room = max(0, max_chars - len(tail))
    cut = text[:room]
    # Prefer ending at a line or word boundary close to the limit.
    boundary = max(cut.rfind("\n", max(0, room - 2000)), cut.rfind(" ", max(0, room - 200)))
    if boundary > 0:
        cut = cut[:boundary]
    return cut.rstrip() + tail


def _split_long(section: str, max_chars: int) -> List[str]:
    """A section over the limit: at paragraph boundaries, then hard cuts."""
    pieces: List[str] = []
    paragraphs = re.split(r"(?<=\n\n)", section)
    current = ""
    for paragraph in paragraphs:
        while len(paragraph) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        if current and len(current) + len(paragraph) > max_chars:
            pieces.append(current)
            current = ""
        current += paragraph
    if current:
        pieces.append(current)
    return pieces


def split_parts(markdown: str, max_chars: int = MAX_PART_CHARS) -> List[str]:
    """Chunks of at most ``max_chars``, cut at ``## `` section boundaries.

    Sections are packed together while they fit; a section longer than a
    chunk is cut at paragraphs, and a paragraph longer than a chunk hard.
    """
    limit = max(1, int(max_chars))
    text = str(markdown or "")
    pieces: List[str] = []
    for section in re.split(r"(?m)^(?=## )", text):
        if not section:
            continue
        if len(section) <= limit:
            pieces.append(section)
        else:
            pieces.extend(_split_long(section, limit))
    chunks: List[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > limit:
            chunks.append(current)
            current = ""
        current += piece
    if current:
        chunks.append(current)
    out = [c.strip("\n") for c in chunks]
    out = [c for c in out if c.strip()]
    return out or [text.strip()[:limit] or "-"]


def _segment(value: Any) -> str:
    text = str(value or "").replace("/", "-").replace("\\", "-")
    return " ".join(text.split()) or "-"


def document_path(domain_name: Any, type_name: Any, key: str, title: Any) -> str:
    segments = [_segment(domain_name), _segment(type_name), _segment(f"{key} {title or ''}")]
    path = PATH_ROOT + "/".join(segments)
    if len(path) > MAX_PATH_CHARS:
        path = path[:MAX_PATH_CHARS].rstrip()
    return path


def document_for(snapshot: Dict[str, Any], settings: Any) -> Dict[str, Any]:
    """Everything one upload needs: identifier, title, description, path,
    parts and the access groups (a tuple, or None for none)."""
    key = str(snapshot.get("key") or "")
    domain = snapshot.get("domain") or {}
    type_ = snapshot.get("type") or {}
    identifier = pointer_for(settings, str(domain.get("key") or ""), key)
    # The search recognises (and hides) experiment documents by this shape; a
    # document it could not recognise would show up as an ordinary file hit.
    if search_mod.parse_pointer(settings.pointer_prefix, identifier) != key:
        raise PermanentError(MSG_BAD_KEY)
    title = _line(snapshot.get("title"))
    groups = tuple(g for g in (getattr(settings, "index_access_groups", ()) or ()) if g)
    return {
        "identifier": identifier,
        "title": f"{key}{DOT}{title}"[:MAX_TITLE_CHARS],
        "description": _block(snapshot.get("hypothesis"))[:MAX_DESCRIPTION_CHARS],
        "path": document_path(domain.get("name"), type_.get("name"), key, title),
        "parts": [{"snippet": part} for part in split_parts(render_markdown(snapshot))],
        "access_groups": groups or None,
    }


# -- talking to Knovas ------------------------------------------------------------


def _root_error(exc: BaseException) -> BaseException:
    """The error behind a tenacity RetryError: KnovasAPIClient retries
    transport errors with tenacity (without reraise), so an outage reaches
    callers wrapped in one."""
    try:
        from tenacity import RetryError
    except ImportError:  # pragma: no cover - tenacity comes with knovas_client
        return exc
    if isinstance(exc, RetryError):
        attempt = getattr(exc, "last_attempt", None)
        try:
            inner = attempt.exception() if attempt is not None and attempt.failed else None
        except Exception:  # noqa: BLE001 - a future in an unexpected state
            inner = None
        if isinstance(inner, BaseException):
            return inner
    return exc


def _http_status(exc: BaseException) -> Optional[int]:
    response = getattr(_root_error(exc), "response", None)
    status = getattr(response, "status_code", None)
    return int(status) if isinstance(status, int) else None


def _retry_after(exc: BaseException) -> float:
    headers = getattr(getattr(_root_error(exc), "response", None), "headers", None) or {}
    try:
        raw = headers.get("Retry-After")
    except Exception:  # noqa: BLE001
        raw = None
    if raw is None:
        return DEFAULT_RETRY_AFTER
    text = str(raw).strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            moment = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return DEFAULT_RETRY_AFTER
        if moment is None:
            return DEFAULT_RETRY_AFTER
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        seconds = (moment - datetime.now(timezone.utc)).total_seconds()
    if not math.isfinite(seconds):
        return DEFAULT_RETRY_AFTER
    return max(1.0, min(MAX_RETRY_AFTER, seconds))


def _is_transport_error(exc: BaseException) -> bool:
    exc = _root_error(exc)
    try:
        import requests
    except ImportError:  # pragma: no cover - requests is a hard dependency
        requests = None
    if requests is not None and isinstance(
        exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
    ):
        return True
    return isinstance(exc, (ConnectionError, TimeoutError))


def _raise_for_knovas_error(exc: BaseException, what: str, pointer: str) -> None:
    """Map a failed Knovas call to the queue's vocabulary (never returns
    for mapped errors). Other 4xx is left to the caller (returns)."""
    status = _http_status(exc)
    if status is not None:
        logger.warning("Knovas %s of %s failed: HTTP %s (%s)", what, pointer, status, exc)
        if status in (429, 503):
            raise RetryLater(_retry_after(exc), MSG_BUSY) from exc
        if status in TEMPORARY_STATUSES:
            raise Unavailable(MSG_TEMPORARY.format(code=status)) from exc
        if status >= 500:
            raise Unavailable(MSG_UNREACHABLE) from exc
        return
    if _is_transport_error(exc):
        logger.warning("Knovas %s of %s failed: %s", what, pointer, exc)
        raise Unavailable(MSG_UNREACHABLE) from exc
    # Anything else is a bug or an unexpected answer: the worker logs it with
    # its traceback and retries with a generic message.
    raise exc


def _read_snapshot(conn: Any, experiment_id: str) -> Optional[Dict[str, Any]]:
    """The snapshot as of one moment, so the remembered updated_at matches
    the content that is uploaded."""
    store = _store()
    try:
        from psycopg import pq

        idle = conn.info.transaction_status == pq.TransactionStatus.IDLE
    except Exception:  # noqa: BLE001 - not a psycopg connection (tests)
        idle = False
    if not idle:
        return store.load_snapshot(conn, experiment_id)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        return store.load_snapshot(conn, experiment_id)


def _try_lock(conn: Any, name: str) -> bool:
    """A session advisory lock, the same kind store.try_advisory_lock takes
    (kept here so the lock does not depend on the store module)."""
    row = conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                       (str(name),)).fetchone()
    return bool(row and row[0])


def _unlock(conn: Any, name: str) -> None:
    """Release a lock _try_lock took. Never raises: a connection that broke
    has released it already. A failed transaction left open is rolled back
    first -- nothing but a rollback can end it, and the lock must not outlive
    the job on a connection the worker keeps."""
    try:
        try:
            from psycopg import pq

            if conn.info.transaction_status == pq.TransactionStatus.INERROR:
                conn.rollback()
        except ImportError:  # pragma: no cover - psycopg is a hard dependency here
            pass
        conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (str(name),))
    except Exception:  # noqa: BLE001
        logger.warning("Could not release the upload lock %s; it ends with the connection.",
                       name, exc_info=True)


def index_experiment(conn: Any, experiment_id: str, client: Any, settings: Any) -> None:
    """Upload (or re-upload) one experiment. Handler of 'index' jobs."""
    store = _store()
    experiment_id = str(experiment_id)
    if not settings.index_enabled:
        store.set_index_state(conn, experiment_id, "off")
        return
    groups = tuple(getattr(settings, "index_access_groups", ()) or ())
    if not groups and not settings.index_unrestricted:
        # Fail closed: without a group the document would be visible to every
        # user of the tenant in Knovas.
        store.set_index_state(conn, experiment_id, "error", MSG_NO_GROUP)
        return
    if client is None:
        raise RetryLater(3600, MSG_NO_CLIENT)
    # A second job for the same experiment (an edit or "Neu indexieren" while
    # this upload runs) must not upload beside it: Knovas keeps whichever
    # transmission finishes last, which could be the older content. Taken
    # before the rate slot, so a job that has to wait does not use one up.
    lock = UPLOAD_LOCK_PREFIX + experiment_id
    if not _try_lock(conn, lock):
        raise RetryLater(UPLOAD_LOCK_RETRY_SECONDS, MSG_UPLOAD_RUNNING)
    try:
        _upload_locked(conn, store, experiment_id, client, settings)
    finally:
        _unlock(conn, lock)


def _upload_locked(conn: Any, store: Any, experiment_id: str, client: Any, settings: Any) -> None:
    """index_experiment while it holds the experiment's upload lock."""
    snapshot = _read_snapshot(conn, experiment_id)
    if snapshot is None:
        return
    remembered = snapshot.get("updated_at")
    if isinstance(remembered, datetime):
        remembered = remembered.isoformat()
    try:
        document = document_for(snapshot, settings)
    except PermanentError as exc:
        store.set_index_state(conn, experiment_id, "error", str(exc))
        raise PermanentError(str(exc), handled=True) from exc
    pointer = document["identifier"]
    # After the document is built: an experiment that is gone or that Knovas
    # could never take does not use up one of the tenant's upload slots.
    wait = jobs.take_rate_slot(conn, jobs.RATE_SLOT_KNOVAS_INIT, settings.index_per_minute)
    if wait > 0:
        raise RetryLater(wait, MSG_RATE_SLOT)

    try:
        client.upload_text_document(
            pointer,
            title=document["title"],
            description=document["description"],
            path=document["path"],
            parts=document["parts"],
            access_groups=document["access_groups"],
        )
    except Exception as exc:  # noqa: BLE001 - mapped below
        _raise_for_knovas_error(exc, "upload", pointer)
        status = _http_status(exc)
        message = MSG_REJECTED.format(code=status)
        store.set_index_state(conn, experiment_id, "error", message)
        raise PermanentError(message, handled=True) from exc

    store.record_index_document(conn, pointer, experiment_id)
    still_there = snapshot.get("key") in (store.lookup_by_keys(conn, [snapshot.get("key")]) or {})
    if not still_there:
        # Deleted while the upload ran: take the copy back out right away (the
        # delayed unindex job would do it too, this closes the window).
        try:
            client.delete_information_object(pointer)
        except Exception as exc:  # noqa: BLE001
            if _http_status(exc) != 404:
                logger.warning("Knovas delete of %s after a concurrent removal failed: %s",
                               pointer, exc)
                return
        store.forget_index_document(conn, pointer)
        return
    store.set_index_state(conn, experiment_id, "indexed", if_updated_at=remembered)


def unindex_pointer(conn: Any, client: Any, pointer: str) -> None:
    """Remove one document from Knovas. Handler of 'unindex' jobs."""
    if client is None:
        raise RetryLater(3600, MSG_NO_CLIENT)
    pointer = str(pointer or "").strip()
    if not pointer:
        return
    try:
        client.delete_information_object(pointer)
    except Exception as exc:  # noqa: BLE001 - mapped below
        if _http_status(exc) != 404:
            _raise_for_knovas_error(exc, "delete", pointer)
            raise PermanentError(MSG_DELETE_REJECTED.format(code=_http_status(exc))) from exc
    _store().forget_index_document(conn, pointer)


def _purge_one(client: Any, pointer: str) -> bool:
    """Delete one pointer for purge_all: True when it is gone (404 counts).
    Knovas being unreachable aborts the purge; a refusal skips the pointer."""
    for attempt in range(2):
        try:
            client.delete_information_object(pointer)
            return True
        except Exception as exc:  # noqa: BLE001
            status = _http_status(exc)
            if status == 404:
                return True
            if status in (429, 503) and attempt == 0:
                time.sleep(min(_retry_after(exc), 60.0))
                continue
            if (status is not None and status >= 500) or _is_transport_error(exc):
                logger.warning("Knovas delete of %s failed: %s", pointer, exc)
                raise Unavailable(MSG_UNREACHABLE) from exc
            logger.warning("Knovas refused to delete %s: %s", pointer, exc)
            return False
    return False


def _listing_failed(exc: BaseException, prefix: str, notes: Optional[List[str]]) -> None:
    """Map a failed Knovas listing for purge_all. Knovas away or busy raises
    Unavailable (the purge can be repeated); a refused listing only ends the
    listing pass, with a note for the operator; anything else is a bug and
    goes up unchanged."""
    root = _root_error(exc)
    status = _http_status(root)
    if _is_transport_error(root) or (status is not None and (status >= 500 or status in (
            408, 425, 429))):
        logger.warning("Knovas listing under %s/ failed: %s", prefix, root)
        raise Unavailable(MSG_LISTING_UNREACHABLE) from exc
    if status is not None:
        logger.warning("Knovas refused the listing under %s/: HTTP %s (%s)", prefix, status, root)
        if notes is not None:
            notes.append(MSG_LISTING_REFUSED.format(code=status))
        return
    raise exc


def _listed_documents(lister: Any, prefix: str, notes: Optional[List[str]]):
    """The documents Knovas lists under ``prefix/``, with its failures mapped
    by _listing_failed. Only the listing itself is guarded: an error while
    deleting a listed document keeps its own meaning."""
    try:
        iterator = iter(lister(prefix=f"{prefix}/"))
    except Exception as exc:  # noqa: BLE001 - mapped
        _listing_failed(exc, prefix, notes)
        return
    while True:
        try:
            document = next(iterator)
        except StopIteration:
            return
        except Exception as exc:  # noqa: BLE001 - mapped
            _listing_failed(exc, prefix, notes)
            return
        yield document


def purge_all(conn: Any, client: Any, settings: Any, *, knovas_listing: bool = True,
              notes: Optional[List[str]] = None) -> int:
    """Delete every experiment document from Knovas; returns how many.

    First every pointer the module recorded (exp_index_documents), then --
    when the client can list documents -- whatever Knovas still lists under
    the prefix. Only pointers of the exact experiment shape are deleted from
    the listing, so a misconfigured prefix can never reach ordinary files.

    The listing goes through the unsigned client, and Knovas lists documents
    by the caller's own access: documents uploaded with an access group are
    only found through the record. A listing Knovas refuses is skipped with
    a German note appended to ``notes``; Knovas unreachable raises
    Unavailable, after the recorded documents are already gone.
    """
    if client is None:
        raise Unavailable(MSG_NO_CLIENT)
    store = _store()
    deleted = 0
    gone = set()
    after: Optional[str] = None
    while True:
        batch = list(store.index_documents(conn, after=after, limit=500) or [])
        if not batch:
            break
        for pointer in batch:
            if _purge_one(client, pointer):
                store.forget_index_document(conn, pointer)
                gone.add(pointer)
                deleted += 1
        after = batch[-1]
        if len(batch) < 500:
            break
    lister = getattr(client, "iter_documents", None)
    if knovas_listing and callable(lister):
        prefix = settings.pointer_prefix
        for document in _listed_documents(lister, prefix, notes):
            if not isinstance(document, dict):
                continue
            pointer = None
            for field in ("pointer", "identifier", "doc_id"):
                value = document.get(field)
                if isinstance(value, str) and search_mod.parse_pointer(prefix, value):
                    pointer = value
                    break
            if pointer is None or pointer in gone:
                continue
            if _purge_one(client, pointer):
                store.forget_index_document(conn, pointer)
                gone.add(pointer)
                deleted += 1
    return deleted
