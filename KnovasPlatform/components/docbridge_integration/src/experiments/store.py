"""All SQL of the experiments module except the job queue (jobs.py).

Functions take a psycopg connection first. Platform connections run in
autocommit mode; functions that write several statements open their own
transaction (a savepoint when the caller already has one open), so a caller
can compose them into a larger unit.

What every function returns is JSON-ready: ids are strings, timestamps ISO
8601 with an offset, numbers int or float -- aggregates are cast in SQL
(``sum(count)::float8``, ``sum(value)::float8``) so no ``Decimal`` ever
reaches Python and no sum can overflow (a BIGINT sum of counts could: 1'025
rows of 2**53 each already exceed it; float8 stays exact up to 2**53 and
the counts are turned back into int in Python) -- and NaN never appears (the
columns refuse it).

Rules that keep concurrent writers correct (plan, section 8):

* Version bumps are ``UPDATE ... SET current_version = current_version + 1
  ... RETURNING`` followed by the version row, in one transaction: the row
  lock orders concurrent editors, each gets its own number.
* Experiment keys come from ``UPDATE exp_domains SET next_seq = next_seq + 1
  ... RETURNING``, inside the transaction that inserts the experiment, so two
  people creating experiments in one domain at the same moment get MKT-7 and
  MKT-8, and a rolled-back creation leaves no gap.
* Changes to an experiment's own state take ``FOR UPDATE`` on its row first
  (``lock_experiment``); child writes (measurements, runs, notes) take ``FOR
  NO KEY UPDATE``, which is also what their ``updated_at`` touch needs, so the
  lock is never upgraded mid-transaction.
* Measurements are written with COPY: a CSV import of 200 000 rows is one
  round trip, not 200 000.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md (sections 3, 8, 9)
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import logging
import math
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from experiments import kinds, labels, permissions, schema
from experiments.errors import Conflict, ValidationError
from experiments.settings import RUNTIME_DEFAULTS, USER_PREF_SHOW_IN_SEARCH

logger = logging.getLogger(__name__)

_UTC = _dt.timezone.utc
#: Stands in for "no variant" where NULLs must compare equal in a hash join.
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"
KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}-[0-9]{1,9}$")

MSG_KEY_TAKEN = "Der Schl\u00fcssel \u00ab{key}\u00bb ist schon vergeben."
MSG_PREFIX_TAKEN = "Das K\u00fcrzel \u00ab{prefix}\u00bb ist schon vergeben."
MSG_BAD_CURSOR = "Die Fortsetzung der Liste ist ung\u00fcltig; bitte neu laden."
MSG_UNKNOWN_SETTING = "Diese Einstellung gibt es nicht."
MSG_SETTING_TYPE = "Der Wert passt nicht zu dieser Einstellung."
MSG_DELETED_ACCOUNT = "Gel\u00f6schtes Konto"

#: Snapshot sizes (plan, section 9).
SNAPSHOT_EVALUATIONS = 60
SNAPSHOT_EVALUATION_OUTPUTS = 20
SNAPSHOT_NOTES = 200
SNAPSHOT_RUNS = 100
#: Evaluations of the primary metric the list's "latest result" looks at per
#: experiment (the newest ones, in any status: a newer queued or failed
#: evaluation supersedes an older done one of the same group).
LATEST_CANDIDATES = 100
#: The next page of the experiment list also returns the experiments changed
#: since this long before the previous page was served: a change moves an
#: experiment above the cursor, where a keyset page would never reach it. The
#: margin covers writers whose updated_at (their transaction start) lies
#: before the page's query although they committed after it.
LIST_CHANGE_MARGIN_SECONDS = 120
#: index_error of experiments that purge-index took out of Knovas (index_state
#: 'off'). Rows turned 'off' because indexing was switched off keep
#: index_error NULL; maintenance uploads those again once it is back on.
INDEX_OFF_PURGED = (
    "Aus Knovas entfernt (purge-index); \u00abAlles neu indexieren\u00bb l\u00e4dt es wieder hoch."
)
#: Buckets one time series returns at most (the newest ones).
MAX_TIMESERIES_ROWS = 10_000
MAX_INDEX_ERROR_CHARS = 500
MAX_LOG_BYTES = 64 * 1024
TOKEN_PREFIX = "kxp_"
MAX_TOKEN_CHARS = 200
TIMESERIES_BUCKETS = ("day", "week", "month")

# -- helpers ------------------------------------------------------------------


def _jsonb(value: Any) -> Any:
    # Imported here: an environment without the identity extras (no psycopg)
    # must still be able to import this module.
    from psycopg.types.json import Jsonb

    return Jsonb(value)


def _unique_violation() -> type:
    import psycopg

    return psycopg.errors.UniqueViolation


def iso(value: Any) -> Optional[str]:
    """ISO 8601 with an offset (a naive timestamp is UTC); None stays None."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_UTC)
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    return str(value)


def canonical_uuid(value: Any) -> Optional[str]:
    """The canonical text of a UUID, or None for anything else.

    Every id that arrives from a URL or a body goes through here before it
    reaches SQL: a malformed id must read as "not found", not as a database
    error.
    """
    if isinstance(value, uuid.UUID):
        return str(value)
    if not isinstance(value, str) or not 32 <= len(value.strip()) <= 45:
        return None
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError:
        return None


def is_uuid(value: Any) -> bool:
    return canonical_uuid(value) is not None


def safe_text(value: Any, limit: int = 200) -> Optional[str]:
    """``value`` when it is text a query can carry (no NUL, valid UTF-8, at
    most ``limit`` characters), else None -- a lookup by such a key simply
    finds nothing instead of failing in the driver."""
    if not isinstance(value, str) or not value or len(value) > limit or "\x00" in value:
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return value


def _finite(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def _person(user_id: Any, display_name: Any) -> Optional[Dict[str, Any]]:
    if user_id is None:
        return None
    return {"id": str(user_id), "display_name": display_name or ""}


def encode_cursor(moment: Any, ident: Any, as_of: Any = None) -> str:
    """An opaque keyset cursor: (timestamp, id) of the last item shown and,
    for the experiment list, since when changed items are sent again."""
    text = f"{iso(moment)}|{ident}"
    if as_of is not None:
        text += f"|{iso(as_of)}"
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _stamp(text: str) -> _dt.datetime:
    moment = _dt.datetime.fromisoformat(text)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=_UTC)


def decode_cursor_parts(text: Any) -> Tuple[_dt.datetime, str, Optional[_dt.datetime]]:
    """(timestamp, id, as_of or None) of a cursor from encode_cursor."""
    if not isinstance(text, str) or not 1 <= len(text) <= 200:
        raise ValidationError(MSG_BAD_CURSOR)
    try:
        padded = text + "=" * (-len(text) % 4)
        parts = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8").split("|")
        if len(parts) not in (2, 3):
            raise ValueError("cursor parts")
        moment = _stamp(parts[0])
        as_of = _stamp(parts[2]) if len(parts) == 3 else None
    except (ValueError, UnicodeError):
        raise ValidationError(MSG_BAD_CURSOR) from None
    canonical = canonical_uuid(parts[1])
    if canonical is None:
        raise ValidationError(MSG_BAD_CURSOR)
    return moment, canonical, as_of


def decode_cursor(text: Any) -> Tuple[_dt.datetime, str]:
    moment, canonical, _ = decode_cursor_parts(text)
    return moment, canonical


def like_pattern(word: str) -> str:
    """``%word%`` for ILIKE with the wildcards in ``word`` taken literally."""
    escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _sql_set(changes: Dict[str, Any], allowed: Dict[str, str]) -> Tuple[List[str], List[Any]]:
    """SET fragments for whitelisted columns (``allowed`` maps a key to its
    SQL expression with one placeholder)."""
    parts: List[str] = []
    values: List[Any] = []
    for key, value in changes.items():
        expression = allowed.get(key)
        if expression is None:
            raise ValueError(f"column {key!r} may not be set here")
        parts.append(expression)
        values.append(value)
    return parts, values


# -- users --------------------------------------------------------------------


def user_exists(conn: Any, user_id: Any) -> bool:
    ident = canonical_uuid(user_id)
    if ident is None:
        return False
    return conn.execute("SELECT 1 FROM users WHERE id = %s", (ident,)).fetchone() is not None


def user_can_view_experiments(conn: Any, user_id: Any) -> bool:
    """Whether the account is active and holds a role that sees the module:
    only such a person can be named responsible for an experiment. (A
    temporary lockout after failed sign-ins does not count against it.)"""
    ident = canonical_uuid(user_id)
    if ident is None:
        return False
    return conn.execute(
        "SELECT 1 FROM users u JOIN user_roles ur ON ur.user_id = u.id "
        "JOIN roles r ON r.id = ur.role_id "
        "WHERE u.id = %s AND u.status = 'active' AND r.key = ANY(%s::text[]) LIMIT 1",
        (ident, sorted(permissions.VIEW_ROLES)),
    ).fetchone() is not None


def user_access_groups(conn: Any, user_id: Any) -> Tuple[str, ...]:
    ident = canonical_uuid(user_id)
    if ident is None:
        return ()
    rows = conn.execute(
        "SELECT group_id FROM user_access_groups WHERE user_id = %s ORDER BY group_id", (ident,)
    ).fetchall()
    return tuple(str(r[0]) for r in rows)


def viewers_without_groups(conn: Any, groups: Sequence[str], limit: int = 200) -> List[Dict[str, Any]]:
    """Active people with a viewing role who hold none of ``groups``: Knovas
    will not show them the experiment documents."""
    wanted = [str(g) for g in groups if g]
    if not wanted:
        return []
    rows = conn.execute(
        "SELECT u.display_name, u.email::text FROM users u "
        "WHERE u.status = 'active' "
        "  AND EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
        "              WHERE ur.user_id = u.id AND r.key = ANY(%s::text[])) "
        "  AND NOT EXISTS (SELECT 1 FROM user_access_groups a "
        "                  WHERE a.user_id = u.id AND a.group_id = ANY(%s::text[])) "
        "ORDER BY u.display_name, u.email LIMIT %s",
        (sorted(permissions.VIEW_ROLES), wanted, int(limit)),
    ).fetchall()
    return [{"user": f"{r[0]} ({r[1]})", "missing_groups": list(wanted)} for r in rows]


# -- domains ------------------------------------------------------------------

_DOMAIN_SELECT = (
    "SELECT d.id::text, d.key, d.name, d.description, d.color, d.id_prefix, d.pack, "
    "  d.archived_at IS NOT NULL, COALESCE(c.total, 0), COALESCE(c.running, 0) "
    "FROM exp_domains d "
    "LEFT JOIN LATERAL ("
    "  SELECT count(*)::int AS total, "
    "    count(*) FILTER (WHERE tv.definition->'states' @> "
    "      jsonb_build_array(jsonb_build_object('key', e.status, 'phase', 'running')))::int AS running "
    "  FROM exp_experiments e "
    "  JOIN exp_type_versions tv ON tv.type_id = e.type_id AND tv.version = e.type_version "
    "  WHERE e.domain_id = d.id AND NOT e.archived) c ON TRUE "
)

_DOMAIN_KEYS = ("id", "key", "name", "description", "color", "id_prefix", "pack", "archived",
                "experiment_count", "running_count")


def _domain(row: Sequence[Any]) -> Dict[str, Any]:
    return dict(zip(_DOMAIN_KEYS, row))


def list_domains(conn: Any, include_archived: bool = False) -> List[Dict[str, Any]]:
    where = "" if include_archived else "WHERE d.archived_at IS NULL "
    rows = conn.execute(_DOMAIN_SELECT + where + "ORDER BY d.name, d.key").fetchall()
    return [_domain(r) for r in rows]


def get_domain(conn: Any, key: Any) -> Optional[Dict[str, Any]]:
    key = safe_text(key)
    if key is None:
        return None
    row = conn.execute(_DOMAIN_SELECT + "WHERE d.key = %s", (key,)).fetchone()
    return _domain(row) if row else None


def _key_taken(key: str) -> Conflict:
    message = MSG_KEY_TAKEN.format(key=key)
    return Conflict(message, fields={"key": message})


def _domain_conflict(exc: Exception, key: str, prefix: str) -> Conflict:
    constraint = getattr(getattr(exc, "diag", None), "constraint_name", "") or ""
    if "prefix" in constraint:
        message = MSG_PREFIX_TAKEN.format(prefix=prefix)
        return Conflict(message, fields={"id_prefix": message})
    message = MSG_KEY_TAKEN.format(key=key)
    return Conflict(message, fields={"key": message})


def insert_domain(conn: Any, *, key: str, name: str, id_prefix: str, color: str,
                  description: str, pack: Optional[str], actor_id: Optional[str]) -> str:
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO exp_domains (key, name, id_prefix, color, description, pack, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id::text",
                (key, name, id_prefix, color, description, pack, actor_id),
            ).fetchone()
    except _unique_violation() as exc:
        raise _domain_conflict(exc, key, id_prefix) from None
    return row[0]


_DOMAIN_SET = {
    "name": "name = %s",
    "color": "color = %s",
    "description": "description = %s",
    "archived": "archived_at = CASE WHEN %s THEN COALESCE(archived_at, now()) ELSE NULL END",
}


def update_domain(conn: Any, domain_id: str, changes: Dict[str, Any]) -> None:
    if not changes:
        return
    parts, values = _sql_set(changes, _DOMAIN_SET)
    conn.execute(
        f"UPDATE exp_domains SET {', '.join(parts)}, updated_at = now() WHERE id = %s",
        values + [domain_id],
    )


def allocate_experiment_key(conn: Any, domain_id: str) -> str:
    """The next key of a domain, e.g. ``MKT-8``. Call inside the transaction
    that inserts the experiment: the row lock serialises concurrent creators
    and a rollback returns the number."""
    row = conn.execute(
        "UPDATE exp_domains SET next_seq = next_seq + 1, updated_at = now() "
        "WHERE id = %s RETURNING id_prefix, next_seq - 1",
        (domain_id,),
    ).fetchone()
    if row is None:
        raise ValidationError("Den Bereich gibt es nicht.")
    return f"{row[0]}-{int(row[1])}"


# -- values added to selection fields -----------------------------------------

#: Values one field of one domain may gain (the type's own options are up to 50).
MAX_FIELD_OPTIONS = 200


def lock_field_options(conn: Any, domain_id: str, *, shared: bool) -> None:
    """Serialise the domain's added values inside the caller's transaction:
    adding or removing one takes the lock exclusively, writing an
    experiment's field values shares it, so a value cannot disappear between
    an experiment's check and its insert."""
    fn = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    conn.execute(f"SELECT {fn}(hashtext('exp_field_options:' || %s::text))", (domain_id,))


def field_option_values(conn: Any, domain_id: Optional[str]) -> Dict[str, List[str]]:
    """field key -> the values added in the domain, in the order they came."""
    if domain_id is None:
        return {}
    out: Dict[str, List[str]] = {}
    for field_key, value in conn.execute(
            "SELECT field_key, value FROM exp_field_options WHERE domain_id = %s "
            "ORDER BY created_at, id", (domain_id,)).fetchall():
        out.setdefault(field_key, []).append(value)
    return out


def selection_fields(conn: Any, domain_id: str,
                     extra_definitions: Sequence[Dict[str, Any]] = ()) -> Dict[str, Dict[str, Any]]:
    """field key -> {label, options, extensible, everywhere} over the selection
    fields of the domain: its usable types (current versions), every type
    version its experiments still use, and ``extra_definitions``.

    ``extensible``: some field with that key takes added values.
    ``everywhere``: the casefolded options every extensible one has built in;
    such a value needs no row of its own."""
    definitions = [t["definition"] for t in list_types(conn, domain_id=domain_id, restrict=True)]
    definitions.extend(r[0] for r in conn.execute(
        "SELECT DISTINCT tv.definition FROM exp_experiments e JOIN exp_type_versions tv "
        "ON tv.type_id = e.type_id AND tv.version = e.type_version WHERE e.domain_id = %s",
        (domain_id,)).fetchall())
    definitions.extend(d for d in extra_definitions if d)
    out: Dict[str, Dict[str, Any]] = {}
    for definition in definitions:
        for field in (definition or {}).get("fields") or []:
            if field.get("type") not in ("enum", "multi_enum"):
                continue
            entry = out.setdefault(field["key"], {"label": field.get("label") or field["key"],
                                                  "options": [], "extensible": False,
                                                  "everywhere": None})
            options = [str(o) for o in field.get("options") or []]
            known = {o.casefold() for o in entry["options"]}
            entry["options"].extend(o for o in options if o.casefold() not in known)
            if schema.is_extensible(field):
                entry["extensible"] = True
                folded = {o.casefold() for o in options}
                entry["everywhere"] = folded if entry["everywhere"] is None else entry["everywhere"] & folded
    for entry in out.values():
        entry["everywhere"] = entry["everywhere"] or set()
    return out


def field_option_usage(conn: Any, domain_id: str, field_keys: Sequence[str]) -> Dict[Tuple[str, str], int]:
    """(field key, value) -> how many experiments of the domain hold it, in
    one pass over the domain's experiments."""
    if not field_keys:
        return {}
    rows = conn.execute(
        "SELECT f.key, x.v, count(DISTINCT e.id)::int FROM exp_experiments e "
        "CROSS JOIN LATERAL jsonb_each(e.fields) f "
        "CROSS JOIN LATERAL ("
        "  SELECT f.value #>> '{}' AS v WHERE jsonb_typeof(f.value) = 'string' "
        "  UNION ALL SELECT jsonb_array_elements_text(f.value) WHERE jsonb_typeof(f.value) = 'array'"
        ") x WHERE e.domain_id = %s AND f.key = ANY(%s::text[]) GROUP BY 1, 2",
        (domain_id, list(field_keys))).fetchall()
    return {(r[0], r[1]): int(r[2]) for r in rows}


def list_field_options(conn: Any, domain_id: str, *, with_usage: bool = False) -> List[Dict[str, Any]]:
    """The values added in a domain; with ``with_usage`` each with how many
    experiments hold it (the managers' list only: it reads every experiment)."""
    rows = conn.execute(
        "SELECT o.id::text, o.field_key, o.value, o.created_at, u.id::text, u.display_name "
        "FROM exp_field_options o LEFT JOIN users u ON u.id = o.created_by "
        "WHERE o.domain_id = %s ORDER BY o.field_key, lower(o.value)", (domain_id,)).fetchall()
    out = [{"id": r[0], "field": r[1], "value": r[2], "created_at": iso(r[3]),
            "created_by": _person(r[4], r[5])} for r in rows]
    if with_usage:
        usage = field_option_usage(conn, domain_id, sorted({o["field"] for o in out}))
        for option in out:
            option["used"] = usage.get((option["field"], option["value"]), 0)
    return out


def add_field_option(conn: Any, domain_id: str, field_key: str, value: str,
                     actor_id: Optional[str]) -> Tuple[str, bool]:
    """(the stored spelling, whether it is new). An existing value in another
    spelling ("kanzlei Mittel", "STRASSE" for "Strasse") is returned as it is
    stored; the check casefolds like schema does."""
    with conn.transaction():
        lock_field_options(conn, domain_id, shared=False)
        existing = [r[0] for r in conn.execute(
            "SELECT value FROM exp_field_options WHERE domain_id = %s AND field_key = %s",
            (domain_id, field_key)).fetchall()]
        for stored in existing:
            if stored.casefold() == value.casefold():
                return stored, False
        if len(existing) >= MAX_FIELD_OPTIONS:
            raise Conflict(f"Dieses Feld hat schon {MAX_FIELD_OPTIONS} hinzugef\u00fcgte Werte.",
                           fields={"value": "Keine weiteren Werte m\u00f6glich."})
        # clock_timestamp(): values added in one transaction (a pack import)
        # keep their order; now() would give them all the same time.
        conn.execute(
            "INSERT INTO exp_field_options (domain_id, field_key, value, created_by, created_at) "
            "VALUES (%s, %s, %s, %s, clock_timestamp())", (domain_id, field_key, value, actor_id))
    return value, True


def get_field_option(conn: Any, option_id: Any) -> Optional[Dict[str, Any]]:
    ident = canonical_uuid(option_id)
    if ident is None:
        return None
    row = conn.execute(
        "SELECT o.id::text, o.domain_id::text, o.field_key, o.value FROM exp_field_options o "
        "WHERE o.id = %s", (ident,)).fetchone()
    if row is None:
        return None
    usage = field_option_usage(conn, row[1], [row[2]])
    return {"id": row[0], "domain_id": row[1], "field": row[2], "value": row[3],
            "used": usage.get((row[2], row[3]), 0)}


def delete_field_option(conn: Any, option_id: str) -> None:
    conn.execute("DELETE FROM exp_field_options WHERE id = %s", (option_id,))


# -- types --------------------------------------------------------------------

_TYPE_SELECT = (
    "SELECT t.id::text, t.key, t.name, t.description, d.key, t.current_version, "
    "  t.archived_at IS NOT NULL, tv.definition, "
    "  (SELECT count(*)::int FROM exp_experiments e WHERE e.type_id = t.id), t.domain_id::text "
    "FROM exp_types t "
    "LEFT JOIN exp_domains d ON d.id = t.domain_id "
    "JOIN exp_type_versions tv ON tv.type_id = t.id AND tv.version = t.current_version "
)
_TYPE_KEYS = ("id", "key", "name", "description", "domain_key", "current_version", "archived",
              "definition", "experiment_count", "domain_id")


def _type(row: Sequence[Any]) -> Dict[str, Any]:
    return dict(zip(_TYPE_KEYS, row))


def list_types(conn: Any, *, domain_id: Optional[str] = None, restrict: bool = False,
               include_archived: bool = False) -> List[Dict[str, Any]]:
    """All types, or with ``restrict`` the ones usable in ``domain_id`` (its
    own and the global ones; ``domain_id`` None: only the global ones)."""
    where: List[str] = []
    params: List[Any] = []
    if restrict:
        if domain_id is None:
            where.append("t.domain_id IS NULL")
        else:
            where.append("(t.domain_id = %s OR t.domain_id IS NULL)")
            params.append(domain_id)
    if not include_archived:
        where.append("t.archived_at IS NULL")
    sql = _TYPE_SELECT + (("WHERE " + " AND ".join(where) + " ") if where else "")
    sql += "ORDER BY d.name NULLS FIRST, t.name, t.key"
    return [_type(r) for r in conn.execute(sql, params).fetchall()]


def get_type(conn: Any, type_id: Any) -> Optional[Dict[str, Any]]:
    ident = canonical_uuid(type_id)
    if ident is None:
        return None
    row = conn.execute(_TYPE_SELECT + "WHERE t.id = %s", (ident,)).fetchone()
    return _type(row) if row else None


def find_type(conn: Any, domain_id: Optional[str], ref: Any) -> Optional[Dict[str, Any]]:
    """A type usable in ``domain_id`` by id or by key (the domain's own type
    wins over a global one with the same key)."""
    ident = canonical_uuid(ref)
    if ident is not None:
        found = get_type(conn, ident)
        if found is None or (found["domain_id"] is not None and found["domain_id"] != domain_id):
            return None
        return found
    ref = safe_text(ref)
    if ref is None or not ref.strip():
        return None
    row = conn.execute(
        _TYPE_SELECT + "WHERE t.key = %s AND (t.domain_id = %s OR t.domain_id IS NULL) "
        "ORDER BY t.domain_id IS NULL LIMIT 1",
        (ref.strip(), domain_id),
    ).fetchone()
    return _type(row) if row else None


def type_versions(conn: Any, type_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT v.version, v.created_at, u.display_name FROM exp_type_versions v "
        "LEFT JOIN users u ON u.id = v.created_by WHERE v.type_id = %s ORDER BY v.version DESC",
        (type_id,),
    ).fetchall()
    return [{"version": int(r[0]), "created_at": iso(r[1]), "created_by": r[2]} for r in rows]


def insert_type(conn: Any, *, domain_id: Optional[str], key: str, name: str, description: str,
                definition: Dict[str, Any], actor_id: Optional[str]) -> str:
    try:
        with conn.transaction():
            type_id = conn.execute(
                "INSERT INTO exp_types (domain_id, key, name, description, created_by) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id::text",
                (domain_id, key, name, description, actor_id),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO exp_type_versions (type_id, version, definition, created_by) "
                "VALUES (%s, 1, %s, %s)",
                (type_id, _jsonb(definition), actor_id),
            )
    except _unique_violation():
        raise _key_taken(key) from None
    return type_id


def add_type_version(conn: Any, type_id: str, definition: Dict[str, Any],
                     actor_id: Optional[str]) -> Tuple[int, bool]:
    """(current version, whether a version was added). An identical
    definition adds nothing; the comparison runs under the row lock."""
    with conn.transaction():
        # Lock the type row on its own: joined to its version row, a waiter
        # would re-check the join against the version it read before the
        # lock was released and find nothing. NO KEY UPDATE orders concurrent
        # editors (it conflicts with itself) without blocking the foreign-key
        # KEY SHARE of an experiment being created with this type -- FOR
        # UPDATE would, and deadlock with a creation holding that lock and
        # waiting for a metric a pack import has locked.
        if conn.execute("SELECT 1 FROM exp_types WHERE id = %s FOR NO KEY UPDATE",
                        (type_id,)).fetchone() is None:
            raise ValidationError("Den Typ gibt es nicht.")
        row = conn.execute(
            "SELECT t.current_version, tv.definition FROM exp_types t "
            "JOIN exp_type_versions tv ON tv.type_id = t.id AND tv.version = t.current_version "
            "WHERE t.id = %s",
            (type_id,),
        ).fetchone()
        if row[1] == definition:
            return int(row[0]), False
        version = conn.execute(
            "UPDATE exp_types SET current_version = current_version + 1, updated_at = now() "
            "WHERE id = %s RETURNING current_version",
            (type_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO exp_type_versions (type_id, version, definition, created_by) "
            "VALUES (%s, %s, %s, %s)",
            (type_id, version, _jsonb(definition), actor_id),
        )
    return int(version), True


_TYPE_SET = {
    "name": "name = %s",
    "description": "description = %s",
    "archived": "archived_at = CASE WHEN %s THEN COALESCE(archived_at, now()) ELSE NULL END",
}


def update_type(conn: Any, type_id: str, changes: Dict[str, Any]) -> None:
    if not changes:
        return
    parts, values = _sql_set(changes, _TYPE_SET)
    conn.execute(
        f"UPDATE exp_types SET {', '.join(parts)}, updated_at = now() WHERE id = %s",
        values + [type_id],
    )


# -- metrics ------------------------------------------------------------------

_METRIC_COLUMNS = (
    "mt.id::text, mt.key, mt.name, mt.kind, mt.unit, mt.direction, mt.description, "
    "  mt.definition, d.key, mt.archived_at IS NOT NULL, mt.version, {in_use}, mt.domain_id::text "
)
_METRIC_SELECT = (
    "SELECT " + _METRIC_COLUMNS.format(
        in_use="EXISTS (SELECT 1 FROM exp_experiment_metrics em WHERE em.metric_id = mt.id)")
    + "FROM exp_metrics mt LEFT JOIN exp_domains d ON d.id = mt.domain_id "
)


def _metric(row: Sequence[Any]) -> Dict[str, Any]:
    kind, direction = row[3], row[5]
    spec = kinds.KINDS.get(kind)
    return {
        "id": row[0], "key": row[1], "name": row[2], "kind": kind,
        "kind_label": spec.label if spec else kind,
        "unit": row[4], "direction": direction,
        "direction_label": labels.DIRECTION_LABELS.get(direction, direction),
        "description": row[6], "definition": row[7] or {}, "domain_key": row[8],
        "archived": bool(row[9]), "version": int(row[10]), "in_use": bool(row[11]),
        "domain_id": row[12],
    }


def list_metrics(conn: Any, *, domain_id: Optional[str] = None, restrict: bool = False,
                 include_archived: bool = False) -> List[Dict[str, Any]]:
    where: List[str] = []
    params: List[Any] = []
    if restrict:
        if domain_id is None:
            where.append("mt.domain_id IS NULL")
        else:
            where.append("(mt.domain_id = %s OR mt.domain_id IS NULL)")
            params.append(domain_id)
    if not include_archived:
        where.append("mt.archived_at IS NULL")
    sql = _METRIC_SELECT + (("WHERE " + " AND ".join(where) + " ") if where else "")
    sql += "ORDER BY d.name NULLS FIRST, mt.name, mt.key"
    return [_metric(r) for r in conn.execute(sql, params).fetchall()]


def get_metric(conn: Any, metric_id: Any, *, lock: bool = False) -> Optional[Dict[str, Any]]:
    """The metric; ``lock`` takes FOR UPDATE on it (the metric editor). That
    lock deliberately conflicts with the KEY SHARE a measurement insert takes
    on the metrics it validated rows against (assigned_metrics(lock=True)):
    a kind change waits for the insert and then sees its rows."""
    ident = canonical_uuid(metric_id)
    if ident is None:
        return None
    sql = _METRIC_SELECT + "WHERE mt.id = %s"
    if lock:
        sql += " FOR UPDATE OF mt"
    row = conn.execute(sql, (ident,)).fetchone()
    return _metric(row) if row else None


def resolve_metrics(conn: Any, domain_id: Optional[str], keys: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Metric keys resolved in ``domain_id`` first, then globally."""
    wanted = sorted({k for k in keys if safe_text(k)})
    if not wanted:
        return {}
    if domain_id is None:
        scope_sql, params = "mt.domain_id IS NULL", [wanted]
    else:
        scope_sql, params = "(mt.domain_id = %s OR mt.domain_id IS NULL)", [wanted, domain_id]
    rows = conn.execute(
        _METRIC_SELECT + "WHERE mt.key = ANY(%s::text[]) AND " + scope_sql + " "
        "ORDER BY mt.key, mt.domain_id IS NULL",
        params,
    ).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        metric = _metric(row)
        out.setdefault(metric["key"], metric)
    return out


def global_metric_keys(conn: Any) -> List[str]:
    return [r[0] for r in conn.execute(
        "SELECT key FROM exp_metrics WHERE domain_id IS NULL ORDER BY key").fetchall()]


def insert_metric(conn: Any, *, domain_id: Optional[str], key: str, name: str, kind: str,
                  unit: str, direction: str, description: str, definition: Dict[str, Any],
                  actor_id: Optional[str]) -> str:
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO exp_metrics (domain_id, key, name, kind, unit, direction, description, "
                "definition, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id::text",
                (domain_id, key, name, kind, unit, direction, description, _jsonb(definition),
                 actor_id),
            ).fetchone()
    except _unique_violation():
        raise _key_taken(key) from None
    return row[0]


_METRIC_SET = {
    "name": "name = %s",
    "kind": "kind = %s",
    "unit": "unit = %s",
    "direction": "direction = %s",
    "description": "description = %s",
    "definition": "definition = %s",
    "archived": "archived_at = CASE WHEN %s THEN COALESCE(archived_at, now()) ELSE NULL END",
}


def update_metric(conn: Any, metric_id: str, changes: Dict[str, Any]) -> int:
    """Apply ``changes`` and bump the metric's version; returns it."""
    changes = dict(changes)
    if "definition" in changes:
        changes["definition"] = _jsonb(changes["definition"])
    parts, values = _sql_set(changes, _METRIC_SET)
    row = conn.execute(
        f"UPDATE exp_metrics SET {', '.join(parts + ['version = version + 1', 'updated_at = now()'])} "
        "WHERE id = %s RETURNING version",
        values + [metric_id],
    ).fetchone()
    return int(row[0]) if row else 0


def metric_has_measurements(conn: Any, metric_id: str) -> bool:
    return conn.execute(
        "SELECT EXISTS (SELECT 1 FROM exp_measurements WHERE metric_id = %s)", (metric_id,)
    ).fetchone()[0]


def experiments_using_metric(conn: Any, metric_id: str) -> List[str]:
    """Experiments that use the metric, except those purge-index took out of
    Knovas: a metric rename must not upload them again."""
    return [r[0] for r in conn.execute(
        "SELECT em.experiment_id::text FROM exp_experiment_metrics em "
        "JOIN exp_experiments e ON e.id = em.experiment_id "
        "WHERE em.metric_id = %s AND (e.index_state <> 'off' OR e.index_error IS DISTINCT FROM %s)",
        (metric_id, INDEX_OFF_PURGED),
    ).fetchall()]


# -- evaluators ---------------------------------------------------------------

_EVALUATOR_SELECT = (
    "SELECT ev.id::text, ev.key, ev.name, ev.language, v.description, v.input_kinds, "
    "  ev.current_version, ev.archived_at IS NOT NULL, v.params_schema, v.code "
    "FROM exp_evaluators ev "
    "JOIN exp_evaluator_versions v ON v.evaluator_id = ev.id AND v.version = ev.current_version "
)


def _builtin_specs() -> Dict[str, Any]:
    from experiments import evaluators

    return evaluators.BUILTINS


def _evaluator(row: Sequence[Any], *, with_code: bool = False) -> Dict[str, Any]:
    language = row[3]
    builtin = language == "builtin"
    needs_rows = True
    if builtin:
        spec = _builtin_specs().get(row[1])
        needs_rows = bool(spec.needs_rows) if spec is not None else False
    out = {
        "id": row[0], "key": row[1], "name": row[2], "language": language,
        "description": row[4], "input_kinds": list(row[5] or []),
        "current_version": int(row[6]), "archived": bool(row[7]), "builtin": builtin,
        "params_schema": row[8] or {}, "needs_rows": needs_rows,
    }
    if with_code:
        out["code"] = row[9]
    return out


def list_evaluators(conn: Any, include_archived: bool = False) -> List[Dict[str, Any]]:
    where = "" if include_archived else "WHERE ev.archived_at IS NULL "
    rows = conn.execute(
        _EVALUATOR_SELECT + where + "ORDER BY ev.language <> 'builtin', ev.name, ev.key"
    ).fetchall()
    return [_evaluator(r) for r in rows]


def get_evaluator(conn: Any, evaluator_id: Any = None, *, key: Any = None,
                  with_code: bool = False) -> Optional[Dict[str, Any]]:
    if key is not None:
        key = safe_text(key)
        if key is None:
            return None
        row = conn.execute(_EVALUATOR_SELECT + "WHERE ev.key = %s", (key,)).fetchone()
    else:
        ident = canonical_uuid(evaluator_id)
        if ident is None:
            return None
        row = conn.execute(_EVALUATOR_SELECT + "WHERE ev.id = %s", (ident,)).fetchone()
    return _evaluator(row, with_code=with_code) if row else None


def evaluator_versions(conn: Any, evaluator_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT v.version, v.created_at, u.display_name FROM exp_evaluator_versions v "
        "LEFT JOIN users u ON u.id = v.created_by WHERE v.evaluator_id = %s ORDER BY v.version DESC",
        (evaluator_id,),
    ).fetchall()
    return [{"version": int(r[0]), "created_at": iso(r[1]), "created_by": r[2]} for r in rows]


def evaluator_keys(conn: Any) -> List[str]:
    return [r[0] for r in conn.execute("SELECT key FROM exp_evaluators ORDER BY key").fetchall()]


def insert_evaluator(conn: Any, *, key: str, name: str, language: str, description: str,
                     code: str, input_kinds: List[str], params_schema: Dict[str, Any],
                     actor_id: Optional[str]) -> str:
    try:
        with conn.transaction():
            evaluator_id = conn.execute(
                "INSERT INTO exp_evaluators (key, name, language, created_by) "
                "VALUES (%s, %s, %s, %s) RETURNING id::text",
                (key, name, language, actor_id),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO exp_evaluator_versions (evaluator_id, version, code, description, "
                "input_kinds, params_schema, created_by) VALUES (%s, 1, %s, %s, %s, %s, %s)",
                (evaluator_id, code, description, list(input_kinds), _jsonb(params_schema), actor_id),
            )
    except _unique_violation():
        raise _key_taken(key) from None
    return evaluator_id


def add_evaluator_version(conn: Any, evaluator_id: str, *, name: str, code: str,
                          description: str, input_kinds: List[str], params_schema: Dict[str, Any],
                          actor_id: Optional[str]) -> Tuple[int, bool]:
    """(current version, whether one was added); nothing is added when code,
    description, kinds, schema and name are all unchanged."""
    with conn.transaction():
        if conn.execute("SELECT 1 FROM exp_evaluators WHERE id = %s FOR UPDATE",
                        (evaluator_id,)).fetchone() is None:
            raise ValidationError("Den Auswerter gibt es nicht.")
        row = conn.execute(
            "SELECT ev.current_version, ev.name, v.code, v.description, v.input_kinds, v.params_schema "
            "FROM exp_evaluators ev JOIN exp_evaluator_versions v "
            "  ON v.evaluator_id = ev.id AND v.version = ev.current_version "
            "WHERE ev.id = %s",
            (evaluator_id,),
        ).fetchone()
        current = (row[1], row[2], row[3], list(row[4] or []), row[5] or {})
        if current == (name, code, description, list(input_kinds), params_schema):
            return int(row[0]), False
        version = conn.execute(
            "UPDATE exp_evaluators SET current_version = current_version + 1, name = %s, "
            "updated_at = now() WHERE id = %s RETURNING current_version",
            (name, evaluator_id),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO exp_evaluator_versions (evaluator_id, version, code, description, "
            "input_kinds, params_schema, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (evaluator_id, version, code, description, list(input_kinds), _jsonb(params_schema),
             actor_id),
        )
    return int(version), True


def ensure_builtin_evaluators(conn: Any) -> None:
    """Register every built-in evaluator (idempotent, safe to run from every
    process at start). A built-in whose description, kinds or params schema
    changed in a release gets a new version, so evaluations keep pointing at
    the version they ran with."""
    specs = _builtin_specs()
    with conn.transaction():
        for key, spec in specs.items():
            wanted = (spec.name, key, spec.description, list(spec.input_kinds),
                      json.loads(json.dumps(spec.params_schema)))
            inserted = conn.execute(
                "INSERT INTO exp_evaluators (key, name, language) VALUES (%s, %s, 'builtin') "
                "ON CONFLICT (key) DO NOTHING RETURNING id::text",
                (key, spec.name),
            ).fetchone()
            if inserted is not None:
                conn.execute(
                    "INSERT INTO exp_evaluator_versions (evaluator_id, version, code, description, "
                    "input_kinds, params_schema) VALUES (%s, 1, %s, %s, %s, %s)",
                    (inserted[0], key, spec.description, list(spec.input_kinds),
                     _jsonb(wanted[4])),
                )
                continue
            current_sql = (
                "SELECT ev.id::text, ev.language, ev.name, v.code, v.description, v.input_kinds, "
                "  v.params_schema "
                "FROM exp_evaluators ev JOIN exp_evaluator_versions v "
                "  ON v.evaluator_id = ev.id AND v.version = ev.current_version "
                "WHERE ev.key = %s")
            row = conn.execute(current_sql, (key,)).fetchone()
            if row is None or row[1] != "builtin":
                logger.error("Evaluator key %s is taken by a non-builtin evaluator.", key)
                continue
            if (row[2], row[3], row[4], list(row[5] or []), row[6] or {}) == wanted:
                continue  # the usual case at start: nothing to lock
            # Changed in this release: lock, then compare again (another
            # process may have added the version meanwhile).
            conn.execute("SELECT 1 FROM exp_evaluators WHERE key = %s FOR UPDATE", (key,))
            row = conn.execute(current_sql, (key,)).fetchone()
            if (row[2], row[3], row[4], list(row[5] or []), row[6] or {}) == wanted:
                continue
            version = conn.execute(
                "UPDATE exp_evaluators SET current_version = current_version + 1, name = %s, "
                "updated_at = now() WHERE id = %s RETURNING current_version",
                (spec.name, row[0]),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO exp_evaluator_versions (evaluator_id, version, code, description, "
                "input_kinds, params_schema) VALUES (%s, %s, %s, %s, %s, %s)",
                (row[0], version, key, spec.description, list(spec.input_kinds), _jsonb(wanted[4])),
            )


# -- packs --------------------------------------------------------------------


def install_pack(conn: Any, pack: Dict[str, Any], *, actor_id: Optional[str] = None,
                 update_existing: bool = False,
                 changed_out: Optional[Dict[str, List[str]]] = None) -> Dict[str, int]:
    """Write a validated pack (packs.validate_pack output) in one transaction.

    Inserts are ``ON CONFLICT ... DO NOTHING`` followed by a re-select, so
    installing twice (or from two processes at once) changes nothing. With
    ``update_existing`` (a manager importing an edited pack) existing items
    are brought to the pack's state: a new type version when the definition
    differs, a new evaluator version when its code or contract differs, an
    updated metric. Returns how many items were created or changed.

    ``changed_out`` (a dict) receives what the Knovas copies of existing
    experiments show and the import changed: ``metric_ids`` (name, kind,
    unit, direction or definition changed) and ``type_ids`` (name changed).
    """
    counts = {"domain": 0, "types": 0, "metrics": 0, "evaluators": 0}
    reindex: Dict[str, List[str]] = {"metric_ids": [], "type_ids": []}
    with conn.transaction():
        required = [k for k in pack.get("requires_metrics") or []]
        if required:
            present = {r[0] for r in conn.execute(
                "SELECT key FROM exp_metrics WHERE domain_id IS NULL AND key = ANY(%s::text[])",
                (required,),
            ).fetchall()}
            missing = [k for k in required if k not in present]
            if missing:
                raise ValidationError(
                    f"Das Paket braucht die globale Metrik \u00ab{missing[0]}\u00bb; es gibt sie nicht."
                )

        domain_id: Optional[str] = None
        domain = pack.get("domain")
        if domain:
            domain_id, changed = _install_domain(conn, pack, domain, actor_id, update_existing)
            counts["domain"] = 1 if changed else 0

        for evaluator in pack.get("evaluators") or []:
            if _install_evaluator(conn, evaluator, actor_id, update_existing):
                counts["evaluators"] += 1
        for metric in pack.get("metrics") or []:
            changed, metric_id, shown_change = _install_metric(conn, domain_id, metric, actor_id,
                                                               update_existing)
            if changed:
                counts["metrics"] += 1
            if shown_change:
                reindex["metric_ids"].append(metric_id)
        for type_ in pack.get("types") or []:
            changed, type_id, renamed = _install_type(conn, domain_id, type_, actor_id,
                                                      update_existing)
            if changed:
                counts["types"] += 1
            if renamed:
                reindex["type_ids"].append(type_id)
        if domain and domain.get("field_options"):
            counts["field_options"] = _install_field_options(conn, domain_id, domain["field_options"],
                                                             actor_id)
    if changed_out is not None:
        changed_out.update(reindex)
    return counts


def _install_field_options(conn: Any, domain_id: str, field_options: Dict[str, List[str]],
                           actor_id: Optional[str]) -> int:
    """Add a pack's domain.field_options after its types: only to fields that
    take added values, without the ones the types have built in everywhere.
    Values only accumulate; an import never removes one. Returns how many
    are new."""
    fields = selection_fields(conn, domain_id)
    wanted: Dict[str, List[str]] = {}
    for field_key, values in field_options.items():
        entry = fields.get(field_key)
        if entry is None or not entry["extensible"]:
            continue    # e.g. a field an archived type had; nothing offers it
        wanted[field_key] = [v for v in values if v.casefold() not in entry["everywhere"]]
    present = field_option_values(conn, domain_id)
    for field_key, values in wanted.items():
        known = {v.casefold() for v in present.get(field_key, [])}
        fresh = {v.casefold() for v in values} - known
        if len(known) + len(fresh) > MAX_FIELD_OPTIONS:
            raise ValidationError(
                "Das Paket bringt zu viele Werte.",
                fields={f"domain.field_options.{field_key}":
                        f"Zusammen mit den vorhandenen mehr als {MAX_FIELD_OPTIONS} Werte."})
    created = 0
    for field_key, values in wanted.items():
        for value in values:
            if add_field_option(conn, domain_id, field_key, value, actor_id)[1]:
                created += 1
    return created


def _install_domain(conn: Any, pack: Dict[str, Any], domain: Dict[str, Any],
                    actor_id: Optional[str], update_existing: bool) -> Tuple[str, bool]:
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO exp_domains (key, name, id_prefix, color, description, pack, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (key) DO NOTHING RETURNING id::text",
                (domain["key"], domain["name"], domain["id_prefix"], domain["color"],
                 domain["description"], pack.get("pack"), actor_id),
            ).fetchone()
    except _unique_violation():
        raise Conflict(MSG_PREFIX_TAKEN.format(prefix=domain["id_prefix"])) from None
    if row is not None:
        return row[0], True
    existing = conn.execute(
        "SELECT id::text, id_prefix, name, color, description FROM exp_domains WHERE key = %s "
        "FOR UPDATE",
        (domain["key"],),
    ).fetchone()
    if not update_existing:
        return existing[0], False
    if existing[1] != domain["id_prefix"]:
        raise Conflict(
            f"Der Bereich \u00ab{domain['key']}\u00bb hat das K\u00fcrzel \u00ab{existing[1]}\u00bb; "
            "das K\u00fcrzel l\u00e4sst sich nicht \u00e4ndern."
        )
    wanted = (domain["name"], domain["color"], domain["description"])
    if tuple(existing[2:5]) == wanted:
        return existing[0], False
    conn.execute(
        "UPDATE exp_domains SET name = %s, color = %s, description = %s, updated_at = now() "
        "WHERE id = %s",
        wanted + (existing[0],),
    )
    return existing[0], True


def _install_evaluator(conn: Any, evaluator: Dict[str, Any], actor_id: Optional[str],
                       update_existing: bool) -> bool:
    row = conn.execute(
        "INSERT INTO exp_evaluators (key, name, language, created_by) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (key) DO NOTHING RETURNING id::text",
        (evaluator["key"], evaluator["name"], evaluator["language"], actor_id),
    ).fetchone()
    if row is not None:
        conn.execute(
            "INSERT INTO exp_evaluator_versions (evaluator_id, version, code, description, "
            "input_kinds, params_schema, created_by) VALUES (%s, 1, %s, %s, %s, %s, %s)",
            (row[0], evaluator["code"], evaluator["description"], list(evaluator["input_kinds"]),
             _jsonb(evaluator["params_schema"] or {}), actor_id),
        )
        return True
    if not update_existing:
        return False
    existing = conn.execute(
        "SELECT id::text, language FROM exp_evaluators WHERE key = %s", (evaluator["key"],)
    ).fetchone()
    if existing[1] != evaluator["language"]:
        raise Conflict(
            f"Den Auswerter \u00ab{evaluator['key']}\u00bb gibt es schon in einer anderen Sprache."
        )
    _, added = add_evaluator_version(
        conn, existing[0], name=evaluator["name"], code=evaluator["code"],
        description=evaluator["description"], input_kinds=list(evaluator["input_kinds"]),
        params_schema=evaluator["params_schema"] or {}, actor_id=actor_id,
    )
    return added


#: Metric columns an experiment's Knovas copy does not show.
_METRIC_UNINDEXED = frozenset({"description", "archived"})


def _install_metric(conn: Any, domain_id: Optional[str], metric: Dict[str, Any],
                    actor_id: Optional[str], update_existing: bool
                    ) -> Tuple[bool, Optional[str], bool]:
    """(created or changed, id, a change the Knovas copies show)."""
    row = conn.execute(
        "INSERT INTO exp_metrics (domain_id, key, name, kind, unit, direction, description, "
        "definition, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT ((COALESCE(domain_id::text, '')), key) DO NOTHING RETURNING id::text",
        (domain_id, metric["key"], metric["name"], metric["kind"], metric["unit"],
         metric["direction"], metric["description"], _jsonb(metric["definition"] or {}), actor_id),
    ).fetchone()
    if row is not None:
        return True, row[0], False
    if not update_existing:
        return False, None, False
    # NO KEY UPDATE, not FOR UPDATE: it must not block the foreign-key KEY
    # SHARE of an experiment being created with this metric (that creation
    # may already hold a type this import locks next -- a deadlock).
    existing = conn.execute(
        "SELECT id::text, name, kind, unit, direction, description, definition FROM exp_metrics "
        "WHERE COALESCE(domain_id::text, '') = COALESCE(%s::text, '') AND key = %s "
        "FOR NO KEY UPDATE",
        (domain_id, metric["key"]),
    ).fetchone()
    wanted = {"name": metric["name"], "kind": metric["kind"], "unit": metric["unit"],
              "direction": metric["direction"], "description": metric["description"],
              "definition": metric["definition"] or {}}
    current = dict(zip(("name", "kind", "unit", "direction", "description", "definition"),
                       existing[1:7]))
    changes = {k: v for k, v in wanted.items() if current.get(k) != v}
    if not changes:
        return False, existing[0], False
    if "kind" in changes:
        # A kind change must wait for measurement inserts in flight (they hold
        # KEY SHARE on the metric from validating their rows) and then see
        # their rows; only FOR UPDATE conflicts with KEY SHARE.
        conn.execute("SELECT 1 FROM exp_metrics WHERE id = %s FOR UPDATE", (existing[0],))
        if metric_has_measurements(conn, existing[0]):
            raise ValidationError(
                f"Die Art der Metrik \u00ab{metric['key']}\u00bb l\u00e4sst sich nicht mehr \u00e4ndern, "
                "es gibt schon Messwerte."
            )
    update_metric(conn, existing[0], changes)
    return True, existing[0], bool(set(changes) - _METRIC_UNINDEXED)


def _install_type(conn: Any, domain_id: Optional[str], type_: Dict[str, Any],
                  actor_id: Optional[str], update_existing: bool
                  ) -> Tuple[bool, Optional[str], bool]:
    """(created or changed, id, renamed)."""
    row = conn.execute(
        "INSERT INTO exp_types (domain_id, key, name, description, created_by) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT ((COALESCE(domain_id::text, '')), key) DO NOTHING RETURNING id::text",
        (domain_id, type_["key"], type_["name"], type_["description"], actor_id),
    ).fetchone()
    if row is not None:
        conn.execute(
            "INSERT INTO exp_type_versions (type_id, version, definition, created_by) "
            "VALUES (%s, 1, %s, %s)",
            (row[0], _jsonb(type_["definition"]), actor_id),
        )
        return True, row[0], False
    if not update_existing:
        return False, None, False
    existing = conn.execute(
        "SELECT id::text, name, description FROM exp_types "
        "WHERE COALESCE(domain_id::text, '') = COALESCE(%s::text, '') AND key = %s",
        (domain_id, type_["key"]),
    ).fetchone()
    changed = False
    if (existing[1], existing[2]) != (type_["name"], type_["description"]):
        update_type(conn, existing[0], {"name": type_["name"], "description": type_["description"]})
        changed = True
    _, added = add_type_version(conn, existing[0], type_["definition"], actor_id)
    return changed or added, existing[0], existing[1] != type_["name"]


def ensure_core_pack(conn: Any) -> None:
    """Install the shipped ``core`` pack when it is missing (idempotent)."""
    from experiments import packs

    install_pack(conn, packs.load_pack("core"), actor_id=None, update_existing=False)


def pack_installed(conn: Any, pack: Dict[str, Any]) -> bool:
    """Whether a pack's domain exists (domain packs) or all its types exist
    globally (packs without a domain, such as core)."""
    domain = pack.get("domain")
    if domain:
        return conn.execute("SELECT 1 FROM exp_domains WHERE key = %s",
                            (domain["key"],)).fetchone() is not None
    keys = [t["key"] for t in pack.get("types") or []]
    if not keys:
        return False
    n = conn.execute(
        "SELECT count(*)::int FROM exp_types WHERE domain_id IS NULL AND key = ANY(%s::text[])",
        (keys,),
    ).fetchone()[0]
    return int(n) == len(set(keys))


def domain_pack_parts(conn: Any, domain_id: str) -> Dict[str, Any]:
    """What export_domain writes: the domain's metrics and types (current
    definitions) and the evaluators and global metrics those types use."""
    metrics = list_metrics(conn, include_archived=True)
    own_metrics = [m for m in metrics if m["domain_id"] == domain_id]
    types = [t for t in list_types(conn, include_archived=False) if t["domain_id"] == domain_id]
    return {"metrics": own_metrics, "types": types,
            "global_metrics": {m["key"]: m for m in metrics if m["domain_id"] is None}}


# -- experiments --------------------------------------------------------------

_EXPERIMENT_ROW = (
    "SELECT e.id::text, e.key, e.domain_id::text, e.type_id::text, e.type_version, e.title, "
    "  e.hypothesis, e.description, e.status, e.fields, e.tags, e.owner_id::text, e.archived, "
    "  e.row_version, e.index_state, e.indexed_at, e.started_at, e.ended_at, e.decided_at, "
    "  e.updated_at, d.key, tv.definition "
    "FROM exp_experiments e "
    "JOIN exp_domains d ON d.id = e.domain_id "
    "JOIN exp_type_versions tv ON tv.type_id = e.type_id AND tv.version = e.type_version "
)
_EXPERIMENT_KEYS = ("id", "key", "domain_id", "type_id", "type_version", "title", "hypothesis",
                    "description", "status", "fields", "tags", "owner_id", "archived",
                    "row_version", "index_state", "indexed_at", "started_at", "ended_at",
                    "decided_at", "updated_at", "domain_key", "definition")


def _experiment_where(key_or_id: Any) -> Optional[Tuple[str, str]]:
    ident = canonical_uuid(key_or_id)
    if ident is not None:
        return "e.id = %s", ident
    if isinstance(key_or_id, str) and KEY_RE.fullmatch(key_or_id.strip()):
        return "e.key = %s", key_or_id.strip()
    return None


def get_experiment_row(conn: Any, key_or_id: Any, *, lock: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The experiment's own columns plus its domain key and type definition.

    ``lock``: None, ``"update"`` (the experiment's own state changes) or
    ``"no key update"`` (a child row is written and updated_at touched).
    """
    where = _experiment_where(key_or_id)
    if where is None:
        return None
    sql = _EXPERIMENT_ROW + f"WHERE {where[0]}"
    if lock == "update":
        sql += " FOR UPDATE OF e"
    elif lock == "no key update":
        sql += " FOR NO KEY UPDATE OF e"
    elif lock is not None:
        raise ValueError(f"unknown lock {lock!r}")
    row = conn.execute(sql, (where[1],)).fetchone()
    if row is None:
        return None
    out = dict(zip(_EXPERIMENT_KEYS, row))
    out["fields"] = out["fields"] or {}
    out["tags"] = list(out["tags"] or [])
    return out


def insert_experiment(conn: Any, *, key: str, domain_id: str, type_id: str, type_version: int,
                      title: str, hypothesis: str, description: str, status: str,
                      fields: Dict[str, Any], tags: List[str], owner_id: Optional[str],
                      actor_id: Optional[str], started: bool) -> str:
    row = conn.execute(
        "INSERT INTO exp_experiments (key, domain_id, type_id, type_version, title, hypothesis, "
        "  description, status, fields, tags, owner_id, created_by, started_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "  CASE WHEN %s THEN now() END) RETURNING id::text",
        (key, domain_id, type_id, type_version, title, hypothesis, description, status,
         _jsonb(fields), list(tags), owner_id, actor_id, bool(started)),
    ).fetchone()
    return row[0]


_EXPERIMENT_SET = {
    "title": "title = %s",
    "hypothesis": "hypothesis = %s",
    "description": "description = %s",
    "fields": "fields = %s",
    "tags": "tags = %s::text[]",
    "owner_id": "owner_id = %s::uuid",
    "archived": "archived = %s",
    "status": "status = %s",
    "started_at": "started_at = %s::timestamptz",
    "ended_at": "ended_at = %s::timestamptz",
    "decided_at": "decided_at = %s::timestamptz",
}
#: Timestamp columns a change may set to "now" with this marker.
NOW = object()


def update_experiment_state(conn: Any, experiment_id: str, changes: Dict[str, Any]) -> int:
    """Change the experiment's own editable state: bumps row_version and
    updated_at. Returns the new row_version. ``NOW`` as a timestamp value
    means the transaction time."""
    parts: List[str] = []
    values: List[Any] = []
    for column, value in changes.items():
        if column not in _EXPERIMENT_SET:
            raise ValueError(f"column {column!r} may not be set here")
        if value is NOW:
            parts.append(f"{column} = now()")
            continue
        if column == "fields":
            value = _jsonb(value)
        elif column == "tags":
            value = list(value)
        parts.append(_EXPERIMENT_SET[column])
        values.append(value)
    parts += ["row_version = row_version + 1", "updated_at = now()"]
    row = conn.execute(
        f"UPDATE exp_experiments SET {', '.join(parts)} WHERE id = %s RETURNING row_version",
        values + [experiment_id],
    ).fetchone()
    return int(row[0]) if row else 0


def lock_experiment_for_child(conn: Any, experiment_id: str) -> bool:
    """FOR NO KEY UPDATE on the experiment row before a child row is written
    (the lock order every writer follows: experiment first); False when it is
    gone."""
    ident = canonical_uuid(experiment_id)
    if ident is None:
        return False
    return conn.execute("SELECT 1 FROM exp_experiments WHERE id = %s FOR NO KEY UPDATE",
                        (ident,)).fetchone() is not None


def touch_experiment(conn: Any, experiment_id: str) -> None:
    """A child row changed: updated_at only, never row_version."""
    conn.execute("UPDATE exp_experiments SET updated_at = now() WHERE id = %s", (experiment_id,))


def delete_experiment(conn: Any, experiment_id: str) -> None:
    conn.execute("DELETE FROM exp_experiments WHERE id = %s", (experiment_id,))


def try_advisory_lock(conn: Any, name: str) -> bool:
    """A session-level advisory lock named ``name`` (released on unlock or
    when the connection closes); False when another session holds it."""
    return bool(conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                             (str(name),)).fetchone()[0])


def advisory_unlock(conn: Any, name: str) -> None:
    conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (str(name),))


def delete_pending_jobs(conn: Any, dedupe_keys: Sequence[str]) -> int:
    """Drop pending jobs that would work on something about to disappear."""
    keys = [str(k) for k in dedupe_keys if k]
    if not keys:
        return 0
    cur = conn.execute(
        "DELETE FROM exp_jobs WHERE status = 'pending' AND dedupe_key = ANY(%s::text[])", (keys,)
    )
    return max(0, int(cur.rowcount or 0))


# -- variants and assigned metrics ----------------------------------------------


def list_variants(conn: Any, experiment_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT v.id::text, v.key, v.name, v.description, v.is_control, v.allocation, v.position, "
        "  EXISTS (SELECT 1 FROM exp_measurements m WHERE m.variant_id = v.id) "
        "FROM exp_variants v WHERE v.experiment_id = %s ORDER BY v.position, v.key",
        (experiment_id,),
    ).fetchall()
    return [{"id": r[0], "key": r[1], "name": r[2], "description": r[3], "is_control": bool(r[4]),
             "allocation": r[5], "position": int(r[6]), "has_data": bool(r[7])} for r in rows]


def replace_variants(conn: Any, experiment_id: str, desired: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Make the variant list equal ``desired`` (kept by key). Call under the
    experiment's row lock after checking that removed variants have no data.
    Returns the added and removed keys."""
    existing = {v["key"]: v for v in list_variants(conn, experiment_id)}
    wanted = {v["key"] for v in desired}
    removed = [k for k in existing if k not in wanted]
    if removed:
        conn.execute("DELETE FROM exp_variants WHERE experiment_id = %s AND key = ANY(%s::text[])",
                     (experiment_id, removed))
    # Clear the control first: the one-control index is checked per statement.
    conn.execute("UPDATE exp_variants SET is_control = FALSE WHERE experiment_id = %s AND is_control",
                 (experiment_id,))
    added: List[str] = []
    for position, v in enumerate(desired):
        values = (v.get("name", ""), v.get("description", ""), bool(v.get("is_control")),
                  v.get("allocation"), position)
        if v["key"] in existing:
            conn.execute(
                "UPDATE exp_variants SET name = %s, description = %s, is_control = %s, "
                "allocation = %s, position = %s WHERE experiment_id = %s AND key = %s",
                values + (experiment_id, v["key"]),
            )
        else:
            conn.execute(
                "INSERT INTO exp_variants (experiment_id, key, name, description, is_control, "
                "allocation, position) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (experiment_id, v["key"]) + values,
            )
            added.append(v["key"])
    return {"added": added, "removed": removed}


def assigned_metrics(conn: Any, experiment_id: str, *, lock: bool = False) -> List[Dict[str, Any]]:
    """The experiment's metrics in order, each with role and guardrail.

    ``lock`` (inside the transaction of a measurement insert) takes KEY SHARE
    on the metric rows: the kind the rows are validated against cannot change
    until the insert commits (a kind change takes FOR UPDATE and then sees
    the new rows). The insert's foreign-key check takes the same lock in the
    same order a moment later anyway, so this adds no new wait.
    """
    rows = conn.execute(
        "SELECT em.role, em.guardrail_op, em.guardrail_value, em.position, "
        + _METRIC_COLUMNS.format(in_use="TRUE")
        + "FROM exp_experiment_metrics em JOIN exp_metrics mt ON mt.id = em.metric_id "
        "LEFT JOIN exp_domains d ON d.id = mt.domain_id "
        "WHERE em.experiment_id = %s ORDER BY em.position, mt.key"
        + (" FOR KEY SHARE OF mt" if lock else ""),
        (experiment_id,),
    ).fetchall()
    out = []
    for r in rows:
        metric = _metric(r[4:])
        metric.update({
            "role": r[0], "role_label": labels.METRIC_ROLE_LABELS.get(r[0], r[0]),
            "guardrail_op": r[1], "guardrail_value": r[2],
        })
        out.append(metric)
    return out


def replace_experiment_metrics(conn: Any, experiment_id: str, entries: List[Dict[str, Any]]) -> None:
    """entries: [{"metric_id", "role", "guardrail_op", "guardrail_value"}] in order."""
    conn.execute("DELETE FROM exp_experiment_metrics WHERE experiment_id = %s", (experiment_id,))
    if not entries:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO exp_experiment_metrics (experiment_id, metric_id, role, guardrail_op, "
            "guardrail_value, position) VALUES (%s, %s, %s, %s, %s, %s)",
            [(experiment_id, e["metric_id"], e["role"], e.get("guardrail_op"),
              e.get("guardrail_value"), i) for i, e in enumerate(entries)],
        )


def metrics_with_data(conn: Any, experiment_id: str, metric_ids: Sequence[str]) -> List[str]:
    ids = [i for i in metric_ids if i]
    if not ids:
        return []
    rows = conn.execute(
        "SELECT x.id::text FROM unnest(%s::uuid[]) AS x(id) WHERE EXISTS ("
        "  SELECT 1 FROM exp_measurements m WHERE m.experiment_id = %s AND m.metric_id = x.id)",
        (ids, experiment_id),
    ).fetchall()
    return [r[0] for r in rows]


# -- measurements -------------------------------------------------------------

MEASUREMENT_COLUMNS = ("experiment_id", "metric_id", "variant_id", "run_id", "batch_id",
                       "observed_at", "value", "count", "denominator", "sum_sq", "dims",
                       "source", "created_by")


def transaction_now(conn: Any) -> _dt.datetime:
    return conn.execute("SELECT now()").fetchone()[0]


def insert_batch(conn: Any, *, experiment_id: str, run_id: Optional[str], source: str, rows: int,
                 metric_keys: List[str], filename: Optional[str], actor_id: Optional[str]) -> str:
    return conn.execute(
        "INSERT INTO exp_batches (experiment_id, run_id, source, rows, metric_keys, filename, "
        "created_by) VALUES (%s, %s, %s, %s, %s::text[], %s, %s) RETURNING id::text",
        (experiment_id, run_id, source, int(rows), list(metric_keys), filename, actor_id),
    ).fetchone()[0]


def copy_measurements(conn: Any, rows: Iterable[Sequence[Any]]) -> int:
    """COPY rows (tuples in MEASUREMENT_COLUMNS order; dims as JSON text) into
    exp_measurements. Returns how many were written."""
    n = 0
    with conn.cursor() as cur:
        with cur.copy(
            f"COPY exp_measurements ({', '.join(MEASUREMENT_COLUMNS)}) FROM STDIN"
        ) as copy:
            for row in rows:
                copy.write_row(row)
                n += 1
    return n


def delete_batch(conn: Any, experiment_id: str, batch_id: Any) -> Optional[int]:
    """Delete a batch and (by cascade) its measurements; the rows it had, or
    None when the batch is not this experiment's."""
    ident = canonical_uuid(batch_id)
    if ident is None:
        return None
    row = conn.execute(
        "DELETE FROM exp_batches WHERE id = %s AND experiment_id = %s RETURNING rows",
        (ident, experiment_id),
    ).fetchone()
    return int(row[0]) if row else None


def list_batches(conn: Any, experiment_id: str, *, after: Optional[str] = None,
                 limit: int = 50) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    params: List[Any] = [experiment_id]
    where = "b.experiment_id = %s"
    if after:
        moment, ident = decode_cursor(after)
        where += " AND (b.created_at, b.id) < (%s, %s::uuid)"
        params += [moment, ident]
    rows = conn.execute(
        "SELECT b.id::text, b.source, b.rows, b.metric_keys, b.filename, b.created_at, "
        "  u.id::text, u.display_name "
        "FROM exp_batches b LEFT JOIN users u ON u.id = b.created_by "
        f"WHERE {where} ORDER BY b.created_at DESC, b.id DESC LIMIT %s",
        params + [int(limit) + 1],
    ).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    items = [{
        "batch_id": r[0], "source": r[1], "source_label": labels.SOURCE_LABELS.get(r[1], r[1]),
        "rows": int(r[2]), "metric_keys": list(r[3] or []), "filename": r[4],
        "created_at": iso(r[5]), "created_by": _person(r[6], r[7]),
    } for r in rows]
    next_after = encode_cursor(rows[-1][5], rows[-1][0]) if more and rows else None
    return items, next_after


def batch_counts(conn: Any, experiment_id: str) -> Tuple[int, int]:
    """(batches, measurement rows) -- from exp_batches, never by counting
    exp_measurements."""
    row = conn.execute(
        "SELECT count(*)::int, COALESCE(sum(rows), 0)::bigint FROM exp_batches "
        "WHERE experiment_id = %s",
        (experiment_id,),
    ).fetchone()
    return int(row[0]), int(row[1])


def run_ids_of(conn: Any, experiment_id: str, run_ids: Iterable[str]) -> set:
    ids = sorted({i for i in run_ids if i})
    if not ids:
        return set()
    rows = conn.execute(
        "SELECT id::text FROM exp_runs WHERE experiment_id = %s AND id = ANY(%s::uuid[])",
        (experiment_id, ids),
    ).fetchall()
    return {r[0] for r in rows}


# -- scope --------------------------------------------------------------------

#: Per (metric, variant) the newest finished run that has rows of that metric
#: and variant. The (metric, variant, run) pairs come from one index-only
#: pass over the covering index (variant_id and run_id are INCLUDE columns);
#: MATERIALIZED keeps PostgreSQL from inlining the CTE into the row scan.
_LATEST_CTE = (
    "latest AS MATERIALIZED ("
    "  SELECT DISTINCT ON (p.metric_id, p.variant_id) p.metric_id AS mid, p.variant_id AS vid, "
    "    p.run_id "
    "  FROM (SELECT DISTINCT x.metric_id, x.variant_id, x.run_id FROM exp_measurements x "
    "        WHERE x.experiment_id = %(eid)s AND x.metric_id = ANY(%(mids)s::uuid[]) "
    "          AND x.run_id IS NOT NULL) p "
    "  JOIN exp_runs r ON r.id = p.run_id AND r.experiment_id = %(eid)s AND r.status = 'finished' "
    "  ORDER BY p.metric_id, p.variant_id, r.created_at DESC, r.id DESC)"
)
#: No latest run for the row's variant: only its rows without a run count.
_LATEST_JOIN = (
    "LEFT JOIN latest l ON l.mid = m.metric_id "
    "AND COALESCE(l.vid, %(zero)s::uuid) = COALESCE(m.variant_id, %(zero)s::uuid)"
)
_LATEST_WHERE = "((l.run_id IS NULL AND m.run_id IS NULL) OR m.run_id = l.run_id)"


class _ScopeSQL:
    """FROM/WHERE pieces selecting the rows of a scope from exp_measurements m."""

    def __init__(self, experiment_id: str, metric_ids: Sequence[str], scope: Dict[str, Any]) -> None:
        self.ctes: List[str] = []
        self.joins: List[str] = []
        self.where = ["m.experiment_id = %(eid)s", "m.metric_id = ANY(%(mids)s::uuid[])"]
        self.params: Dict[str, Any] = {"eid": experiment_id, "mids": list(metric_ids)}
        runs = scope.get("runs")
        if runs == "latest":
            self.ctes.append(_LATEST_CTE)
            self.joins.append(_LATEST_JOIN)
            self.where.append(_LATEST_WHERE)
            self.params["zero"] = _ZERO_UUID
        elif isinstance(runs, list) and runs:
            self.where.append("m.run_id = ANY(%(runs)s::uuid[])")
            self.params["runs"] = list(runs)
        if scope.get("since"):
            self.where.append("m.observed_at >= %(since)s::timestamptz")
            self.params["since"] = scope["since"]
        if scope.get("until"):
            self.where.append("m.observed_at <= %(until)s::timestamptz")
            self.params["until"] = scope["until"]
        if scope.get("dims"):
            self.where.append("m.dims @> %(dims)s::jsonb")
            self.params["dims"] = _jsonb(scope["dims"])

    def with_sql(self) -> str:
        return ("WITH " + ", ".join(self.ctes) + " ") if self.ctes else ""

    def from_sql(self) -> str:
        return "FROM exp_measurements m " + " ".join(self.joins) + " WHERE " + " AND ".join(self.where)


def _variant_map(conn: Any, experiment_id: str) -> Dict[str, Tuple[str, int]]:
    rows = conn.execute(
        "SELECT id::text, key, position FROM exp_variants WHERE experiment_id = %s", (experiment_id,)
    ).fetchall()
    return {r[0]: (r[1], int(r[2])) for r in rows}


def _sums(conn: Any, experiment_id: str, metric_ids: Sequence[str], scope: Dict[str, Any],
          level_metric_ids: Sequence[str] = ()) -> Dict[str, Dict[Optional[str], Dict[str, Any]]]:
    """{metric_id: {variant_id|None: sums}} over the scope's rows, from the
    covering index. ``level_metric_ids``: metrics whose units per level are
    wanted too (ordinal, categorical); a group with more than
    kinds.MAX_AGGREGATE_LEVELS distinct values gets ``levels`` None.

    Counts are summed as float8: exact up to 2**53, and no number of rows
    can make the query fail (a BIGINT sum overflows)."""
    if not metric_ids:
        return {}
    q = _ScopeSQL(experiment_id, metric_ids, scope)
    rows = conn.execute(
        q.with_sql()
        + "SELECT m.metric_id::text, m.variant_id::text, count(*), sum(m.count)::float8, "
        "  sum(m.value)::float8, sum(m.value * m.count)::float8, "
        "  sum(m.value * m.value * m.count)::float8, sum(m.denominator)::float8, "
        "  sum(CASE WHEN m.count = 1 THEN COALESCE(m.sum_sq, m.value * m.value) "
        "           ELSE m.sum_sq END)::float8, "
        "  COALESCE(bool_or(m.count > 1 AND m.sum_sq IS NULL), FALSE) "
        + q.from_sql() + " GROUP BY m.metric_id, m.variant_id",
        q.params,
    ).fetchall()
    out: Dict[str, Dict[Optional[str], Dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r[0], {})[r[1]] = {
            "rows": int(r[2]), "n": _count(r[3]), "sum_value": r[4], "sum_value_count": r[5],
            "sum_sq_levels": r[6], "sum_denominator": r[7], "sum_sq": r[8],
            "sum_sq_missing": bool(r[9]), "levels": {},
        }
    wanted = [m for m in level_metric_ids if m in out]
    if wanted:
        q = _ScopeSQL(experiment_id, wanted, scope)
        # At most one level more than the limit per group: enough to know the
        # group has too many, without sending thousands of them.
        params = dict(q.params, max_levels=kinds.MAX_AGGREGATE_LEVELS + 1)
        rows = conn.execute(
            q.with_sql()
            + "SELECT metric_id, variant_id, value, units FROM ("
            "  SELECT m.metric_id::text AS metric_id, m.variant_id::text AS variant_id, "
            "    m.value AS value, sum(m.count)::float8 AS units, "
            "    row_number() OVER (PARTITION BY m.metric_id, m.variant_id ORDER BY m.value) AS rn "
            + q.from_sql() + " GROUP BY m.metric_id, m.variant_id, m.value) lv "
            "WHERE rn <= %(max_levels)s ORDER BY value",
            params,
        ).fetchall()
        for r in rows:
            group = out.get(r[0], {}).get(r[1])
            if group is None or group["levels"] is None:
                continue
            if len(group["levels"]) >= kinds.MAX_AGGREGATE_LEVELS:
                group["levels"] = None  # too many distinct values for a distribution
                continue
            group["levels"][kinds.level_key(r[2])] = _count(r[3])
    return out


def _count(value: Any) -> int:
    """A count summed as float8 back to int (0 for NULL)."""
    x = _finite(value)
    return int(x) if x is not None else 0


def _aggregate(kind: str, variant: Optional[str], sums: Dict[str, Any]) -> Dict[str, Any]:
    mean_like = kind in kinds.MEAN_LIKE_KINDS
    if kind == "ordinal":
        value_sum = sums["sum_value_count"]
        sum_sq = sums["sum_sq_levels"]
    else:
        value_sum = sums["sum_value"]
        sum_sq = None
        if mean_like and not sums["sum_sq_missing"]:
            sum_sq = sums["sum_sq"]
    agg = {
        "variant": variant,
        "rows": sums["rows"],
        "n": sums["n"],
        "value_sum": _finite(value_sum),
        "denominator_sum": _finite(sums["sum_denominator"]) if kind == "ratio" else None,
        "sum_sq": _finite(sum_sq),
        "estimate": None,
        # None also for a level kind with more than MAX_AGGREGATE_LEVELS
        # distinct values (an ordinal metric without defined levels).
        "levels": (dict(sums["levels"]) if kind in kinds.LEVEL_KINDS and sums["levels"] is not None
                   else None),
    }
    agg["estimate"] = kinds.estimate(kind, agg)
    return agg


def _aggregate_list(kind: str, by_variant: Dict[Optional[str], Dict[str, Any]],
                    variants: Dict[str, Tuple[str, int]]) -> List[Dict[str, Any]]:
    items = []
    for variant_id, sums in by_variant.items():
        if variant_id is None:
            items.append(((1, 0, ""), _aggregate(kind, None, sums)))
            continue
        key, position = variants.get(variant_id, (variant_id, 1_000_000))
        items.append(((0, position, key), _aggregate(kind, key, sums)))
    items.sort(key=lambda item: item[0])
    return [agg for _, agg in items]


def aggregates(conn: Any, experiment_id: str, metric_id: str, *, kind: str,
               scope: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """One aggregate per variant with rows in the scope, in variant order,
    plus one with ``"variant": None`` for rows without a variant (plan,
    section 3)."""
    if kind not in kinds.KINDS:
        raise ValidationError(f"Unbekannte Messart \u00ab{str(kind)[:40]}\u00bb.")
    eid, mid = canonical_uuid(experiment_id), canonical_uuid(metric_id)
    if eid is None or mid is None:
        return []
    scope = schema.validate_scope(scope)
    sums = _sums(conn, eid, [mid], scope, [mid] if kind in kinds.LEVEL_KINDS else [])
    return _aggregate_list(kind, sums.get(mid, {}), _variant_map(conn, eid))


def metric_aggregates(conn: Any, experiment_id: str, metrics: Sequence[Dict[str, Any]],
                      scope: Optional[Dict[str, Any]] = None) -> Dict[str, List[Dict[str, Any]]]:
    """aggregates() for several metrics ({"id", "kind"}) in two queries."""
    scope = schema.validate_scope(scope)
    ids = [m["id"] for m in metrics]
    level_ids = [m["id"] for m in metrics if m["kind"] in kinds.LEVEL_KINDS]
    sums = _sums(conn, experiment_id, ids, scope, level_ids)
    variants = _variant_map(conn, experiment_id)
    return {m["id"]: _aggregate_list(m["kind"], sums.get(m["id"], {}), variants) for m in metrics}


def evaluator_rows(conn: Any, experiment_id: str, metric_id: str, limit: int, *,
                   scope: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], bool]:
    """The newest ``limit`` rows of the scope, in ascending id order, shaped
    like the evaluator input contract; the flag says older rows were left
    out."""
    eid, mid = canonical_uuid(experiment_id), canonical_uuid(metric_id)
    limit = max(0, int(limit))
    if eid is None or mid is None or limit == 0:
        return [], False
    scope = schema.validate_scope(scope)
    q = _ScopeSQL(eid, [mid], scope)
    params = dict(q.params, lim=limit + 1)
    rows = conn.execute(
        q.with_sql()
        + "SELECT m.variant_id::text, m.run_id::text, m.value, m.count, m.denominator, m.sum_sq, "
        "  m.observed_at, m.dims "
        + q.from_sql() + " ORDER BY m.id DESC LIMIT %(lim)s",
        params,
    ).fetchall()
    truncated = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()
    variants = _variant_map(conn, eid)
    out = []
    for r in rows:
        out.append({
            "variant": variants[r[0]][0] if r[0] in variants else None,
            "run": r[1], "value": r[2], "count": int(r[3]), "denominator": r[4],
            "sum_sq": r[5], "observed_at": iso(r[6]), "dims": r[7] or {},
        })
    return out, truncated


def rows_fingerprint(conn: Any, experiment_id: str, metric_id: str,
                     scope: Optional[Dict[str, Any]] = None) -> Tuple[int, Optional[int]]:
    """(rows, highest id) of a scope. Measurements are insert-only with ids
    that are never reused, so this pair changes whenever the set of rows
    does -- an evaluation input can be recognised as unchanged without
    reading the rows."""
    scope = schema.validate_scope(scope)
    q = _ScopeSQL(experiment_id, [metric_id], scope)
    row = conn.execute(
        q.with_sql() + "SELECT count(*)::bigint, max(m.id) " + q.from_sql(), q.params
    ).fetchone()
    return int(row[0]), (int(row[1]) if row[1] is not None else None)


def timeseries(conn: Any, experiment_id: str, metric_id: str, *, kind: str, bucket: str,
               scope: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Per bucket (day/week/month of observed_at in UTC) and variant:
    ``[{"bucket_start","variant","n","value_sum","denominator_sum","estimate"}]``,
    oldest first (at most the newest MAX_TIMESERIES_ROWS)."""
    if bucket not in TIMESERIES_BUCKETS:
        raise ValidationError("Erlaubt sind die Intervalle day, week und month.")
    if kind not in kinds.KINDS:
        raise ValidationError(f"Unbekannte Messart \u00ab{str(kind)[:40]}\u00bb.")
    eid, mid = canonical_uuid(experiment_id), canonical_uuid(metric_id)
    if eid is None or mid is None:
        return []
    scope = schema.validate_scope(scope)
    q = _ScopeSQL(eid, [mid], scope)
    params = dict(q.params, bucket=bucket, lim=MAX_TIMESERIES_ROWS)
    rows = conn.execute(
        q.with_sql()
        + "SELECT date_trunc(%(bucket)s::text, m.observed_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC', "
        "  m.variant_id::text, count(*), sum(m.count)::float8, sum(m.value)::float8, "
        "  sum(m.value * m.count)::float8, sum(m.denominator)::float8 "
        + q.from_sql() + " GROUP BY 1, 2 ORDER BY 1 DESC LIMIT %(lim)s",
        params,
    ).fetchall()
    variants = _variant_map(conn, eid)
    out = []
    for r in reversed(rows):
        variant = variants[r[1]][0] if r[1] in variants else None
        value_sum = r[5] if kind == "ordinal" else r[4]
        agg = {"n": _count(r[3]), "value_sum": _finite(value_sum),
               "denominator_sum": _finite(r[6]) if kind == "ratio" else None}
        out.append({
            "bucket_start": iso(r[0]), "variant": variant, "n": agg["n"],
            "value_sum": agg["value_sum"], "denominator_sum": agg["denominator_sum"],
            "estimate": kinds.estimate(kind, agg),
        })
    position = {key: pos for key, pos in variants.values()}
    out.sort(key=lambda p: (p["bucket_start"], p["variant"] is None,
                            position.get(p["variant"], 1_000_000), p["variant"] or ""))
    return out


# -- runs ---------------------------------------------------------------------

_RUN_SELECT = (
    "SELECT r.id::text, r.name, v.key, r.status, r.params, r.environment, r.commit_ref, r.source, "
    "  r.started_at, r.ended_at, r.created_at "
    "FROM exp_runs r LEFT JOIN exp_variants v ON v.id = r.variant_id "
)


def insert_run(conn: Any, *, experiment_id: str, variant_id: Optional[str], name: str,
               status: str, params: Dict[str, Any], environment: Dict[str, Any], commit_ref: str,
               source: str, started_at: Optional[_dt.datetime], ended_at: Optional[_dt.datetime],
               actor_id: Optional[str]) -> str:
    return conn.execute(
        "INSERT INTO exp_runs (experiment_id, variant_id, name, status, params, environment, "
        "commit_ref, source, started_at, ended_at, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id::text",
        (experiment_id, variant_id, name, status, _jsonb(params), _jsonb(environment), commit_ref,
         source, started_at, ended_at, actor_id),
    ).fetchone()[0]


def run_metric_estimates(conn: Any, experiment_id: str, run_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """{run_id: {metric_key: estimate over the run's rows}}."""
    ids = [i for i in run_ids if i]
    if not ids:
        return {}
    rows = conn.execute(
        "SELECT m.run_id::text, mt.key, mt.kind, sum(m.count)::float8, sum(m.value)::float8, "
        "  sum(m.value * m.count)::float8, sum(m.denominator)::float8 "
        "FROM exp_measurements m JOIN exp_metrics mt ON mt.id = m.metric_id "
        "WHERE m.run_id = ANY(%s::uuid[]) AND m.experiment_id = %s "
        "GROUP BY m.run_id, mt.key, mt.kind ORDER BY mt.key",
        (ids, experiment_id),
    ).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        kind = r[2]
        agg = {"n": _count(r[3]), "value_sum": _finite(r[5] if kind == "ordinal" else r[4]),
               "denominator_sum": _finite(r[6])}
        out.setdefault(r[0], {})[r[1]] = kinds.estimate(kind, agg)
    return out


def _run(row: Sequence[Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row[0], "name": row[1], "variant": row[2], "status": row[3],
        "status_label": labels.RUN_STATUS_LABELS.get(row[3], row[3]),
        "params": row[4] or {}, "environment": row[5] or {}, "commit": row[6], "source": row[7],
        "started_at": iso(row[8]), "ended_at": iso(row[9]), "created_at": iso(row[10]),
        "metrics": metrics,
    }


def list_runs(conn: Any, experiment_id: str, *, after: Optional[str] = None,
              limit: int = 50) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    params: List[Any] = [experiment_id]
    where = "r.experiment_id = %s"
    if after:
        moment, ident = decode_cursor(after)
        where += " AND (r.created_at, r.id) < (%s, %s::uuid)"
        params += [moment, ident]
    rows = conn.execute(
        _RUN_SELECT + f"WHERE {where} ORDER BY r.created_at DESC, r.id DESC LIMIT %s",
        params + [int(limit) + 1],
    ).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    estimates = run_metric_estimates(conn, experiment_id, [r[0] for r in rows])
    items = [_run(r, estimates.get(r[0], {})) for r in rows]
    next_after = encode_cursor(rows[-1][10], rows[-1][0]) if more and rows else None
    return items, next_after


def get_run(conn: Any, experiment_id: str, run_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(_RUN_SELECT + "WHERE r.experiment_id = %s AND r.id = %s",
                       (experiment_id, run_id)).fetchone()
    if row is None:
        return None
    return _run(row, run_metric_estimates(conn, experiment_id, [row[0]]).get(row[0], {}))


def run_ids_by_name(conn: Any, experiment_id: str) -> Dict[str, List[str]]:
    """{name: [run ids]} of the experiment's named runs, oldest first (a CSV
    import may name a run instead of giving its id)."""
    out: Dict[str, List[str]] = {}
    for name, ident in conn.execute(
            "SELECT name, id::text FROM exp_runs WHERE experiment_id = %s AND name <> '' "
            "ORDER BY created_at, id", (experiment_id,)).fetchall():
        out.setdefault(name, []).append(ident)
    return out


def run_count(conn: Any, experiment_id: str) -> int:
    return int(conn.execute("SELECT count(*)::int FROM exp_runs WHERE experiment_id = %s",
                            (experiment_id,)).fetchone()[0])


# -- notes --------------------------------------------------------------------

_NOTE_SELECT = (
    "SELECT n.id::text, n.kind, n.body, v.key, n.run_id::text, u.id::text, u.display_name, "
    "  n.created_at, n.created_by::text "
    "FROM exp_notes n LEFT JOIN exp_variants v ON v.id = n.variant_id "
    "LEFT JOIN users u ON u.id = n.created_by "
)


def _note(row: Sequence[Any], actor: Any) -> Dict[str, Any]:
    author = row[8]
    can_delete = bool(actor is not None and permissions.can_view(actor) and (
        permissions.can_manage(actor) or (author is not None and author == str(actor.id))))
    return {
        "id": row[0], "kind": row[1], "kind_label": labels.NOTE_KIND_LABELS.get(row[1], row[1]),
        "body": row[2], "variant": row[3], "run_id": row[4],
        "created_by": _person(row[5], row[6]), "created_at": iso(row[7]), "can_delete": can_delete,
    }


def insert_note(conn: Any, *, experiment_id: str, kind: str, body: str, variant_id: Optional[str],
                run_id: Optional[str], actor_id: Optional[str]) -> str:
    return conn.execute(
        "INSERT INTO exp_notes (experiment_id, kind, body, variant_id, run_id, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id::text",
        (experiment_id, kind, body, variant_id, run_id, actor_id),
    ).fetchone()[0]


def get_note(conn: Any, experiment_id: str, note_id: Any, *, actor: Any = None) -> Optional[Dict[str, Any]]:
    ident = canonical_uuid(note_id)
    if ident is None:
        return None
    row = conn.execute(_NOTE_SELECT + "WHERE n.experiment_id = %s AND n.id = %s",
                       (experiment_id, ident)).fetchone()
    if row is None:
        return None
    note = _note(row, actor)
    note["author_id"] = row[8]
    return note


def delete_note(conn: Any, experiment_id: str, note_id: str) -> bool:
    cur = conn.execute("DELETE FROM exp_notes WHERE experiment_id = %s AND id = %s",
                       (experiment_id, note_id))
    return bool(cur.rowcount)


# -- decisions ----------------------------------------------------------------


def insert_decision(conn: Any, *, experiment_id: str, verdict: str, rationale: str, learning: str,
                    actor_id: Optional[str]) -> str:
    return conn.execute(
        "INSERT INTO exp_decisions (experiment_id, verdict, rationale, learning, decided_by) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id::text",
        (experiment_id, verdict, rationale, learning, actor_id),
    ).fetchone()[0]


def decision_facts(conn: Any, experiment_id: str) -> Tuple[bool, bool]:
    """(any decision recorded, the newest one has a learning)."""
    row = conn.execute(
        "SELECT learning FROM exp_decisions WHERE experiment_id = %s "
        "ORDER BY decided_at DESC, id DESC LIMIT 1",
        (experiment_id,),
    ).fetchone()
    if row is None:
        return False, False
    return True, bool((row[0] or "").strip())


# -- evaluations --------------------------------------------------------------

_EVALUATION_SELECT = (
    "SELECT ev.id::text, x.key, x.name, x.language, ev.evaluator_version, mt.key, ev.params, "
    "  ev.scope, ev.trigger, ev.status, ev.output->>'verdict', ev.output->>'headline', "
    "  {output}, ev.error, ev.created_at, ev.finished_at, ev.duration_ms, u.display_name, "
    "  ev.requested_by IS NOT NULL AND u.id IS NOT NULL, {superseded} {extra} "
    "FROM exp_evaluations ev "
    "JOIN exp_evaluators x ON x.id = ev.evaluator_id "
    "LEFT JOIN exp_metrics mt ON mt.id = ev.metric_id "
    "LEFT JOIN users u ON u.id = ev.requested_by "
)

# An evaluation is superseded when a newer one (created_at, then id) exists in
# its group: same evaluator, metric, params and scope, in any status. The
# newest of a group is its current result (a newer queued or failed one makes
# an older done one history too: it describes data that has changed since or
# a run that was repeated); find_reusable_evaluation reuses only that one, so
# what the pipeline returns is never superseded. Both forms below must agree.
#: For a whole page of evaluations of one experiment.
_SUPERSEDED_WINDOW = (
    "(row_number() OVER (PARTITION BY ev.evaluator_id, ev.metric_id, ev.params, ev.scope "
    "                    ORDER BY ev.created_at DESC, ev.id DESC) > 1)"
)
#: For a single evaluation.
_SUPERSEDED_EXISTS = (
    "EXISTS (SELECT 1 FROM exp_evaluations nx WHERE nx.experiment_id = ev.experiment_id "
    "  AND nx.evaluator_id = ev.evaluator_id AND nx.metric_id IS NOT DISTINCT FROM ev.metric_id "
    "  AND nx.params = ev.params AND nx.scope = ev.scope "
    "  AND (nx.created_at, nx.id) > (ev.created_at, ev.id))"
)


def _evaluation(row: Sequence[Any]) -> Dict[str, Any]:
    status = row[9]
    return {
        "id": row[0], "evaluator_key": row[1], "evaluator_name": row[2], "language": row[3],
        "evaluator_version": int(row[4]), "metric_key": row[5], "params": row[6] or {},
        "scope": row[7] or {}, "trigger": row[8], "status": status,
        "status_label": labels.EVALUATION_STATUS_LABELS.get(status, status),
        "verdict": row[10], "headline": row[11], "output": row[12], "error": row[13],
        "created_at": iso(row[14]), "finished_at": iso(row[15]),
        "duration_ms": int(row[16]) if row[16] is not None else None,
        "requested_by": {"display_name": row[17] or ""} if row[18] else None,
        "superseded": bool(row[19]),
    }


def get_evaluation(conn: Any, experiment_id: str, evaluation_id: Any, *,
                   with_logs: bool = False) -> Optional[Dict[str, Any]]:
    ident = canonical_uuid(evaluation_id)
    if ident is None:
        return None
    sql = _EVALUATION_SELECT.format(output="ev.output", superseded=_SUPERSEDED_EXISTS,
                                    extra=", ev.logs" if with_logs else "")
    row = conn.execute(sql + "WHERE ev.experiment_id = %s AND ev.id = %s",
                       (experiment_id, ident)).fetchone()
    if row is None:
        return None
    out = _evaluation(row)
    if with_logs:
        out["logs"] = row[20]
    return out


def insert_evaluation(conn: Any, *, experiment_id: str, evaluator_id: str, evaluator_version: int,
                      metric_id: Optional[str], params: Dict[str, Any], scope: Dict[str, Any],
                      trigger: str, status: str, output: Optional[Dict[str, Any]] = None,
                      error: Optional[str] = None, input_digest: Optional[str] = None,
                      requested_by: Optional[str] = None, duration_ms: Optional[int] = None) -> str:
    finished = status in ("done", "failed")
    return conn.execute(
        "INSERT INTO exp_evaluations (experiment_id, evaluator_id, evaluator_version, metric_id, "
        "  params, scope, trigger, status, output, error, input_digest, requested_by, duration_ms, "
        "  started_at, finished_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "  CASE WHEN %s THEN now() END, CASE WHEN %s THEN clock_timestamp() END) "
        "RETURNING id::text",
        (experiment_id, evaluator_id, int(evaluator_version), metric_id, _jsonb(params or {}),
         _jsonb(scope or {}), trigger, status, _jsonb(output) if output is not None else None,
         _clip_text(error, MAX_INDEX_ERROR_CHARS), input_digest, requested_by,
         int(duration_ms) if duration_ms is not None else None, finished, finished),
    ).fetchone()[0]


def find_reusable_evaluation(conn: Any, *, experiment_id: str, evaluator_id: str, version: int,
                             metric_id: str, params: Dict[str, Any], scope: Dict[str, Any],
                             input_digest: str) -> Optional[str]:
    """The current evaluation of the group (evaluator, metric, params, scope)
    when it ran -- or is about to run -- on exactly this input: the newest of
    the group, not failed, with the same digest and evaluator version.

    Only the newest counts. An older evaluation with the same digest (the
    data went back to an earlier state, e.g. an import was undone) is
    history: reusing it would leave the evaluation of the removed data the
    newest one, and that is what the page, the list and Knovas show. A
    failed newest one is run again rather than hidden behind an older result.
    """
    row = conn.execute(
        "SELECT id::text, status, evaluator_version, input_digest FROM exp_evaluations "
        "WHERE experiment_id = %s AND evaluator_id = %s AND metric_id = %s "
        "  AND params = %s AND scope = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (experiment_id, evaluator_id, metric_id, _jsonb(params or {}), _jsonb(scope or {})),
    ).fetchone()
    if row is None or row[1] not in ("queued", "running", "done"):
        return None
    if int(row[2]) != int(version) or row[3] != input_digest:
        return None
    return row[0]


def count_done_evaluations(conn: Any, experiment_id: str) -> int:
    return int(conn.execute(
        "SELECT count(*)::int FROM exp_evaluations WHERE experiment_id = %s AND status = 'done'",
        (experiment_id,),
    ).fetchone()[0])


def get_evaluation_record(conn: Any, evaluation_id: str) -> Optional[Dict[str, Any]]:
    """What the worker needs to run an evaluation (plan, section 8)."""
    ident = canonical_uuid(evaluation_id)
    if ident is None:
        return None
    row = conn.execute(
        "SELECT ev.id::text, ev.experiment_id::text, e.key, ev.evaluator_id::text, x.key, x.language, "
        "  v.code, ev.evaluator_version, v.params_schema, ev.metric_id::text, ev.params, ev.scope, "
        "  ev.status, ev.trigger, ev.created_at, x.name, ev.started_at "
        "FROM exp_evaluations ev "
        "JOIN exp_experiments e ON e.id = ev.experiment_id "
        "JOIN exp_evaluators x ON x.id = ev.evaluator_id "
        "JOIN exp_evaluator_versions v ON v.evaluator_id = ev.evaluator_id "
        "  AND v.version = ev.evaluator_version "
        "WHERE ev.id = %s",
        (ident,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "experiment_id": row[1], "experiment_key": row[2], "evaluator_id": row[3],
        "evaluator_key": row[4], "language": row[5], "code": row[6], "version": int(row[7]),
        "params_schema": row[8] or {}, "metric_id": row[9], "params": row[10] or {},
        "scope": row[11] or {}, "status": row[12], "trigger": row[13], "created_at": iso(row[14]),
        "evaluator_name": row[15], "started_at": iso(row[16]),
    }


def seconds_since_first_attempt(conn: Any, evaluation_id: str) -> Optional[float]:
    """How long ago the evaluation's first attempt started (started_at is kept
    across retries); None before the first attempt. The runner give-up clock
    counts from here, not from creation: an evaluation that waited in a long
    queue has not been failing all that time."""
    ident = canonical_uuid(evaluation_id)
    if ident is None:
        return None
    row = conn.execute(
        "SELECT EXTRACT(EPOCH FROM clock_timestamp() - started_at)::float8 "
        "FROM exp_evaluations WHERE id = %s",
        (ident,),
    ).fetchone()
    return None if row is None or row[0] is None else float(row[0])


def _clip_text(text: Any, limit: int) -> Optional[str]:
    if text is None:
        return None
    value = str(text).replace("\x00", "")
    return value[:limit]


def _clip_bytes(text: Any, limit: int) -> Optional[str]:
    """``text`` cut to at most ``limit`` UTF-8 bytes, NULs removed (a TEXT
    column cannot hold them) and unpaired surrogates replaced."""
    if text is None:
        return None
    value = str(text).replace("\x00", "")
    data = value.encode("utf-8", "replace")
    if len(data) <= limit:
        return data.decode("utf-8", "replace")
    return data[:limit].decode("utf-8", "ignore")


def mark_evaluation(conn: Any, evaluation_id: str, *, status: str, output: Any = None,
                    error: Any = None, logs: Any = None, duration_ms: Any = None) -> None:
    """Move an evaluation on. 'running' sets started_at on the first attempt
    (kept across retries: seconds_since_first_attempt); 'done' and 'failed'
    set finished_at (clock time) and touch the experiment's updated_at (its
    page and Knovas copy change); 'queued' puts it back for another
    attempt."""
    if status not in ("queued", "running", "done", "failed"):
        raise ValueError(f"unknown evaluation status {status!r}")
    ident = canonical_uuid(evaluation_id)
    if ident is None:
        return
    sets = ["status = %s"]
    values: List[Any] = [status]
    if status == "running":
        sets.append("started_at = COALESCE(started_at, clock_timestamp())")
    elif status == "queued":
        sets.append("finished_at = NULL")
    else:
        sets.append("finished_at = clock_timestamp()")
        sets += ["output = %s", "error = %s", "logs = %s", "duration_ms = %s"]
        duration = None
        if duration_ms is not None and _finite(duration_ms) is not None:
            duration = max(0, min(2 ** 31 - 1, int(float(duration_ms))))
        values += [_jsonb(output) if output is not None else None,
                   _clip_text(error, MAX_INDEX_ERROR_CHARS), _clip_bytes(logs, MAX_LOG_BYTES),
                   duration]
    sql = f"UPDATE exp_evaluations SET {', '.join(sets)} WHERE id = %s"
    if status not in ("done", "failed"):
        conn.execute(sql, values + [ident])
        return
    with conn.transaction():
        # The experiment row first, like every writer (a concurrent deletion
        # holds it and then removes the evaluations).
        conn.execute(
            "UPDATE exp_experiments SET updated_at = now() "
            "WHERE id = (SELECT experiment_id FROM exp_evaluations WHERE id = %s)",
            (ident,),
        )
        conn.execute(sql, values + [ident])


def prune_pipeline_evaluations(conn: Any, experiment_id: str, keep: int = 10) -> int:
    """Keep the newest ``keep`` done automatic evaluations per (evaluator,
    metric, params, scope); delete the older ones. Automatic means the
    pipeline job's ('pipeline') and CI's through the API ('api': a CI job
    evaluates after every run it logs, and the pipeline job then reuses those
    evaluations instead of adding its own). Evaluations a person started in
    the UI ('manual') are all kept. The newest of a group -- the one reuse
    returns -- is never among the pruned."""
    cur = conn.execute(
        "DELETE FROM exp_evaluations WHERE id IN ("
        "  SELECT id FROM ("
        "    SELECT id, row_number() OVER (PARTITION BY evaluator_id, metric_id, params, scope "
        "                                  ORDER BY created_at DESC, id DESC) AS rn "
        "    FROM exp_evaluations "
        "    WHERE experiment_id = %s AND trigger IN ('pipeline', 'api') AND status = 'done') ranked "
        "  WHERE rn > %s)",
        (experiment_id, int(keep)),
    )
    return max(0, int(cur.rowcount or 0))


# -- snapshot -----------------------------------------------------------------

_SNAPSHOT_SQL = (
    "SELECT e.id::text, e.key, e.title, e.hypothesis, e.description, e.status, e.fields, e.tags, "
    "  e.archived, e.started_at, e.ended_at, e.decided_at, e.created_at, e.updated_at, "
    "  e.row_version, e.index_state, e.indexed_at, e.index_error, e.type_version, "
    "  d.id::text, d.key, d.name, d.color, d.id_prefix, t.id::text, t.key, t.name, tv.definition, "
    "  o.id::text, o.display_name "
    "FROM exp_experiments e "
    "JOIN exp_domains d ON d.id = e.domain_id "
    "JOIN exp_types t ON t.id = e.type_id "
    "JOIN exp_type_versions tv ON tv.type_id = e.type_id AND tv.version = e.type_version "
    "LEFT JOIN users o ON o.id = e.owner_id "
)


def guardrail_status(metric: Dict[str, Any], aggregates_: Sequence[Dict[str, Any]]) -> Optional[str]:
    """"violated" when any aggregate's estimate is on the wrong side of the
    guardrail, "ok" when there are estimates and none is, else None."""
    if metric.get("role") != "guardrail":
        return None
    op, bound = metric.get("guardrail_op"), _finite(metric.get("guardrail_value"))
    if op not in ("max", "min") or bound is None:
        return None
    estimates = [a["estimate"] for a in aggregates_ if a.get("estimate") is not None]
    if not estimates:
        return None
    if any((e > bound) if op == "max" else (e < bound) for e in estimates):
        return "violated"
    return "ok"


def load_snapshot(conn: Any, key_or_id: str, *, actor: Any = None) -> Optional[Dict[str, Any]]:
    """The experiment as the page, the API and the indexer see it (plan,
    section 9); None when it does not exist. ``actor`` decides the notes'
    can_delete flags."""
    loaded = load_snapshot_with_definition(conn, key_or_id, actor=actor)
    return None if loaded is None else loaded[0]


def load_snapshot_with_definition(conn: Any, key_or_id: str, *, actor: Any = None
                                  ) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    where = _experiment_where(key_or_id)
    if where is None:
        return None
    row = conn.execute(_SNAPSHOT_SQL + f"WHERE {where[0]}", (where[1],)).fetchone()
    if row is None:
        return None
    eid = row[0]
    definition = row[27] or {}
    status = row[5]
    field_values = row[6] or {}
    fields = []
    for field in definition.get("fields") or []:
        value = field_values.get(field["key"])
        fields.append({
            "key": field["key"], "label": field.get("label") or field["key"],
            "type": field.get("type"), "value": value,
            "display": schema.display_field_value(field, value),
        })

    variants = list_variants(conn, eid)
    metrics = assigned_metrics(conn, eid)
    aggs = metric_aggregates(conn, eid, metrics) if metrics else {}
    metric_entries = []
    for m in metrics:
        entry = {k: m[k] for k in ("id", "key", "name", "kind", "kind_label", "unit", "direction",
                                   "direction_label", "role", "role_label", "guardrail_op",
                                   "guardrail_value", "definition")}
        entry["aggregates"] = aggs.get(m["id"], [])
        entry["guardrail_status"] = guardrail_status(entry, entry["aggregates"])
        metric_entries.append(entry)

    # The window numbers the rows in the same order the query returns them, so
    # only the newest SNAPSHOT_EVALUATION_OUTPUTS outputs are read and sent.
    # The superseded window runs over all the experiment's evaluations; a row
    # on the page has every newer row of its group on the page too.
    evaluation_rows = conn.execute(
        _EVALUATION_SELECT.format(
            output=f"CASE WHEN row_number() OVER (ORDER BY ev.created_at DESC, ev.id DESC) "
                   f"<= {SNAPSHOT_EVALUATION_OUTPUTS} THEN ev.output END",
            superseded=_SUPERSEDED_WINDOW,
            extra="",
        ) + "WHERE ev.experiment_id = %s ORDER BY ev.created_at DESC, ev.id DESC LIMIT %s",
        (eid, SNAPSHOT_EVALUATIONS),
    ).fetchall()

    decision_rows = conn.execute(
        "SELECT dc.id::text, dc.verdict, dc.rationale, dc.learning, u.id::text, u.display_name, "
        "  dc.decided_at FROM exp_decisions dc LEFT JOIN users u ON u.id = dc.decided_by "
        "WHERE dc.experiment_id = %s ORDER BY dc.decided_at DESC, dc.id DESC",
        (eid,),
    ).fetchall()
    note_rows = conn.execute(
        _NOTE_SELECT + "WHERE n.experiment_id = %s ORDER BY n.created_at DESC, n.id DESC LIMIT %s",
        (eid, SNAPSHOT_NOTES),
    ).fetchall()
    # One more than shown tells whether the list goes on; then runs_next_after
    # is the cursor list_runs (same order) continues with.
    run_rows = conn.execute(
        _RUN_SELECT + "WHERE r.experiment_id = %s ORDER BY r.created_at DESC, r.id DESC LIMIT %s",
        (eid, SNAPSHOT_RUNS + 1),
    ).fetchall()
    runs_next_after = None
    if len(run_rows) > SNAPSHOT_RUNS:
        run_rows = run_rows[:SNAPSHOT_RUNS]
        runs_next_after = encode_cursor(run_rows[-1][10], run_rows[-1][0])
    estimates = run_metric_estimates(conn, eid, [r[0] for r in run_rows])
    batches, measurements = batch_counts(conn, eid)

    snapshot = {
        "id": eid, "key": row[1], "title": row[2], "hypothesis": row[3], "description": row[4],
        "status": status, "status_label": schema.state_label(definition, status),
        "status_phase": schema.state_phase(definition, status), "archived": bool(row[8]),
        "domain": {"id": row[19], "key": row[20], "name": row[21], "color": row[22],
                   "id_prefix": row[23]},
        "type": {"id": row[24], "key": row[25], "name": row[26], "version": int(row[18])},
        "fields": fields,
        "field_values": field_values,
        # Values added to the domain's selection fields (exp_field_options).
        "field_options": field_option_values(conn, row[19]),
        "tags": list(row[7] or []),
        "owner": _person(row[28], row[29]),
        "variants": variants,
        "metrics": metric_entries,
        "evaluations": [_evaluation(r) for r in evaluation_rows],
        "decisions": [{
            "id": r[0], "verdict": r[1],
            "verdict_label": labels.DECISION_VERDICT_LABELS.get(r[1], r[1]),
            "rationale": r[2], "learning": r[3], "decided_by": _person(r[4], r[5]),
            "decided_at": iso(r[6]),
        } for r in decision_rows],
        "notes": [_note(r, actor) for r in note_rows],
        "runs": [_run(r, estimates.get(r[0], {})) for r in run_rows],
        "runs_next_after": runs_next_after,
        "run_count": run_count(conn, eid),
        "measurement_count": measurements,
        "batch_count": batches,
        "created_at": iso(row[12]), "updated_at": iso(row[13]), "started_at": iso(row[9]),
        "ended_at": iso(row[10]), "decided_at": iso(row[11]),
        "row_version": int(row[14]),
        "index": {"state": row[15], "state_label": labels.INDEX_STATE_LABELS.get(row[15], row[15]),
                  "indexed_at": iso(row[16]), "error": row[17]},
    }
    return snapshot, definition


def lookup_by_keys(conn: Any, keys: List[str]) -> Dict[str, Dict[str, Any]]:
    """What a search card needs, per existing KEY."""
    wanted = sorted({k.strip() for k in keys or [] if isinstance(k, str) and KEY_RE.fullmatch(k.strip())})
    if not wanted:
        return {}
    rows = conn.execute(
        "SELECT e.key, e.title, e.hypothesis, e.status, e.archived, d.key, d.name, d.color, t.name, "
        "  e.updated_at, tv.definition->'states' "
        "FROM exp_experiments e JOIN exp_domains d ON d.id = e.domain_id "
        "JOIN exp_types t ON t.id = e.type_id "
        "JOIN exp_type_versions tv ON tv.type_id = e.type_id AND tv.version = e.type_version "
        "WHERE e.key = ANY(%s::text[])",
        (wanted,),
    ).fetchall()
    out = {}
    for r in rows:
        out[r[0]] = {
            "key": r[0], "title": r[1], "hypothesis": r[2], "status": r[3],
            "status_label": schema.state_label({"states": r[10] or []}, r[3]),
            "status_phase": schema.state_phase({"states": r[10] or []}, r[3]),
            "archived": bool(r[4]), "domain_key": r[5], "domain_name": r[6], "domain_color": r[7],
            "type_name": r[8], "updated_at": iso(r[9]),
        }
    return out


# -- lists and search ----------------------------------------------------------

# "latest": among the current (not superseded) evaluations of the primary
# metric, the newest done one with a verdict other than n/a, else the newest
# done one. Current is decided within the newest LATEST_CANDIDATES of the
# metric, any status: a row there has every newer row of its group there too.
_SUMMARY_SQL = (
    "SELECT e.id::text, e.key, e.title, e.status, e.archived, e.tags, e.updated_at, e.index_state, "
    "  d.key, d.name, d.color, t.key, t.name, o.id::text, o.display_name, "
    "  (SELECT s->>'label' FROM jsonb_array_elements(tv.definition->'states') AS s "
    "    WHERE s->>'key' = e.status LIMIT 1), "
    "  pm.key, pm.name, pm.unit, pm.kind, lat.headline, lat.verdict, lat.finished_at, "
    "  (SELECT s->>'phase' FROM jsonb_array_elements(tv.definition->'states') AS s "
    "    WHERE s->>'key' = e.status LIMIT 1) "
    "FROM exp_experiments e "
    "JOIN exp_domains d ON d.id = e.domain_id "
    "JOIN exp_types t ON t.id = e.type_id "
    "JOIN exp_type_versions tv ON tv.type_id = e.type_id AND tv.version = e.type_version "
    "LEFT JOIN users o ON o.id = e.owner_id "
    "LEFT JOIN exp_experiment_metrics pem ON pem.experiment_id = e.id AND pem.role = 'primary' "
    "LEFT JOIN exp_metrics pm ON pm.id = pem.metric_id "
    "LEFT JOIN LATERAL ("
    "  SELECT c.output->>'headline' AS headline, c.output->>'verdict' AS verdict, c.finished_at "
    "  FROM (SELECT w.*, row_number() OVER (PARTITION BY w.evaluator_id, w.params, w.scope "
    "                                       ORDER BY w.created_at DESC, w.id DESC) AS rn "
    "        FROM (SELECT ev.id, ev.evaluator_id, ev.params, ev.scope, ev.status, ev.output, "
    "                ev.finished_at, ev.created_at FROM exp_evaluations ev "
    "              WHERE ev.experiment_id = e.id AND ev.metric_id = pem.metric_id "
    f"              ORDER BY ev.created_at DESC, ev.id DESC LIMIT {LATEST_CANDIDATES}) w) c "
    "  WHERE c.rn = 1 AND c.status = 'done' "
    "  ORDER BY (COALESCE(c.output->>'verdict', 'n/a') <> 'n/a') DESC, c.created_at DESC, c.id DESC "
    "  LIMIT 1) lat ON TRUE "
)


def _summary(row: Sequence[Any]) -> Dict[str, Any]:
    latest = None
    if row[20] is not None or row[21] is not None or row[22] is not None:
        latest = {"headline": row[20], "verdict": row[21], "finished_at": iso(row[22])}
    return {
        "key": row[1], "title": row[2], "status": row[3], "status_label": row[15] or row[3],
        "status_phase": row[23],
        "archived": bool(row[4]), "tags": list(row[5] or []),
        "domain": {"key": row[8], "name": row[9], "color": row[10]},
        "type": {"key": row[11], "name": row[12]},
        "owner": _person(row[13], row[14]),
        "primary_metric": ({"key": row[16], "name": row[17], "unit": row[18], "kind": row[19]}
                           if row[16] is not None else None),
        "latest": latest,
        "guardrail_violations": 0,
        "updated_at": iso(row[6]), "index_state": row[7],
    }


def guardrail_violations(conn: Any, experiment_ids: Sequence[str]) -> Dict[str, int]:
    """Per experiment, how many guardrail metrics have a variant whose
    estimate (plan, section 3) is on the wrong side -- computed in SQL from
    the covering index for a whole page of experiments at once."""
    ids = [i for i in experiment_ids if i]
    if not ids:
        return {}
    rows = conn.execute(
        "SELECT em.experiment_id::text, count(DISTINCT em.metric_id)::int "
        "FROM exp_experiment_metrics em "
        "JOIN exp_metrics mt ON mt.id = em.metric_id "
        "JOIN LATERAL ("
        "  SELECT CASE mt.kind "
        "    WHEN 'ratio' THEN sum(m.value) / NULLIF(sum(m.denominator), 0) "
        "    WHEN 'ordinal' THEN sum(m.value * m.count) / NULLIF(sum(m.count)::float8, 0) "
        "    WHEN 'categorical' THEN NULL "
        "    ELSE sum(m.value) / NULLIF(sum(m.count)::float8, 0) END AS estimate "
        "  FROM exp_measurements m "
        "  WHERE m.experiment_id = em.experiment_id AND m.metric_id = em.metric_id "
        "  GROUP BY m.variant_id) a ON TRUE "
        "WHERE em.experiment_id = ANY(%s::uuid[]) AND em.role = 'guardrail' "
        "  AND ((em.guardrail_op = 'max' AND a.estimate > em.guardrail_value) "
        "    OR (em.guardrail_op = 'min' AND a.estimate < em.guardrail_value)) "
        "GROUP BY em.experiment_id",
        (ids,),
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def _word_filters(words: Sequence[str], columns: Sequence[str], params: Dict[str, Any],
                  extra: Sequence[str] = ()) -> List[str]:
    clauses = []
    for i, word in enumerate(words):
        name = f"w{i}"
        params[name] = like_pattern(word)
        tests = [f"{c} ILIKE %({name})s" for c in columns]
        tests += [x.format(p=f"%({name})s") for x in extra]
        clauses.append("(" + " OR ".join(tests) + ")")
    return clauses


def list_summaries(conn: Any, *, domain: Optional[str] = None, status: Optional[str] = None,
                   words: Sequence[str] = (), tag: Optional[str] = None,
                   include_archived: bool = False, after: Optional[str] = None,
                   limit: int = 50) -> Tuple[List[Dict[str, Any]], Optional[str], int]:
    """(summaries, next cursor, total) ordered by updated_at DESC, id DESC.

    Every change bumps an experiment's updated_at, so an experiment changed
    while someone pages through the list jumps above the cursor, where no
    later page would reach it. The cursor therefore also carries when its
    page was served (less LIST_CHANGE_MARGIN_SECONDS), and the next page adds
    -- after its own items, flagged ``"moved": True`` -- the experiments above
    the cursor changed since then (at most ``limit``). One of them may
    already be on an earlier page: clients merge items by key.
    """
    params: Dict[str, Any] = {}
    where: List[str] = []
    if domain:
        where.append("d.key = %(domain)s")
        params["domain"] = domain
    if status:
        where.append("e.status = %(status)s")
        params["status"] = status
    if tag:
        where.append("%(tag)s = ANY(e.tags)")
        params["tag"] = tag
    if not include_archived:
        where.append("NOT e.archived")
    where += _word_filters(words, ("e.key", "e.title", "e.hypothesis"), params)
    base_where = ("WHERE " + " AND ".join(where) + " ") if where else ""
    total = conn.execute(
        "SELECT count(*)::int FROM exp_experiments e JOIN exp_domains d ON d.id = e.domain_id "
        + base_where, params,
    ).fetchone()[0]
    # Taken before the page is read: whatever commits after this moment is
    # newer than the next cursor's as_of and comes along with the next page.
    as_of = conn.execute("SELECT clock_timestamp() - make_interval(secs => %s)",
                         (float(LIST_CHANGE_MARGIN_SECONDS),)).fetchone()[0]
    page_where = list(where)
    moved_rows: List[Any] = []
    if after:
        moment, ident, since = decode_cursor_parts(after)
        params.update(c_at=moment, c_id=ident)
        if since is not None:
            params.update(c_since=since, moved_lim=int(limit))
            moved_rows = conn.execute(
                _SUMMARY_SQL + "WHERE " + " AND ".join(
                    where + ["(e.updated_at, e.id) > (%(c_at)s, %(c_id)s::uuid)",
                             "e.updated_at > %(c_since)s"])
                + " ORDER BY e.updated_at DESC, e.id DESC LIMIT %(moved_lim)s",
                params,
            ).fetchall()
        page_where.append("(e.updated_at, e.id) < (%(c_at)s, %(c_id)s::uuid)")
    params["lim"] = int(limit) + 1
    rows = conn.execute(
        _SUMMARY_SQL + (("WHERE " + " AND ".join(page_where) + " ") if page_where else "")
        + "ORDER BY e.updated_at DESC, e.id DESC LIMIT %(lim)s",
        params,
    ).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    shown = {r[0] for r in rows}
    moved_rows = [r for r in moved_rows if r[0] not in shown]
    items = [_summary(r) for r in rows] + [dict(_summary(r), moved=True) for r in moved_rows]
    all_rows = rows + moved_rows
    violations = guardrail_violations(conn, [r[0] for r in all_rows])
    for row, item in zip(all_rows, items):
        item["guardrail_violations"] = violations.get(row[0], 0)
    next_after = encode_cursor(rows[-1][6], rows[-1][0], as_of) if more and rows else None
    return items, next_after, int(total)


def summaries_by_keys(conn: Any, keys: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    wanted = sorted({k for k in keys if isinstance(k, str) and KEY_RE.fullmatch(k)})
    if not wanted:
        return {}
    rows = conn.execute(_SUMMARY_SQL + "WHERE e.key = ANY(%s::text[])", (wanted,)).fetchall()
    violations = guardrail_violations(conn, [r[0] for r in rows])
    out = {}
    for r in rows:
        item = _summary(r)
        item["guardrail_violations"] = violations.get(r[0], 0)
        out[item["key"]] = item
    return out


def search_database(conn: Any, words: Sequence[str], limit: int) -> List[Tuple[str, str]]:
    """(KEY, text to cut the snippet from) for experiments matching every
    word in key, title, hypothesis, description, a decision's learning or
    rationale, or a note; newest first."""
    if not words:
        return []
    params: Dict[str, Any] = {}
    clauses = _word_filters(
        words, ("e.key", "e.title", "e.hypothesis", "e.description"), params,
        extra=("EXISTS (SELECT 1 FROM exp_decisions dc WHERE dc.experiment_id = e.id "
               "AND (dc.learning ILIKE {p} OR dc.rationale ILIKE {p}))",
               "EXISTS (SELECT 1 FROM exp_notes n WHERE n.experiment_id = e.id AND n.body ILIKE {p})"),
    )
    params["lim"] = int(limit)
    rows = conn.execute(
        "SELECT e.key, COALESCE("
        "  CASE WHEN e.hypothesis ILIKE %(w0)s THEN e.hypothesis END, "
        "  CASE WHEN e.description ILIKE %(w0)s THEN e.description END, "
        "  (SELECT dc.learning FROM exp_decisions dc WHERE dc.experiment_id = e.id "
        "     AND dc.learning ILIKE %(w0)s ORDER BY dc.decided_at DESC LIMIT 1), "
        "  (SELECT dc.rationale FROM exp_decisions dc WHERE dc.experiment_id = e.id "
        "     AND dc.rationale ILIKE %(w0)s ORDER BY dc.decided_at DESC LIMIT 1), "
        "  (SELECT n.body FROM exp_notes n WHERE n.experiment_id = e.id "
        "     AND n.body ILIKE %(w0)s ORDER BY n.created_at DESC LIMIT 1), "
        "  e.hypothesis) "
        "FROM exp_experiments e WHERE " + " AND ".join(clauses)
        + " ORDER BY e.updated_at DESC, e.id DESC LIMIT %(lim)s",
        params,
    ).fetchall()
    return [(r[0], r[1] or "") for r in rows]


# -- Knovas index bookkeeping ----------------------------------------------------


def set_index_state(conn: Any, experiment_id: str, state: str, error: Optional[str] = None, *,
                    if_updated_at: Optional[str] = None) -> None:
    """Record where the Knovas copy stands; touches neither updated_at nor
    row_version. 'indexed' sets indexed_at and is skipped when
    ``if_updated_at`` no longer matches (a newer edit keeps 'pending').
    ``error`` is kept for 'error' and for 'off' (purge-index passes
    INDEX_OFF_PURGED); 'off' without it means "indexing was switched off",
    which maintenance uploads again once it is back on. It never replaces
    the purge marker: a change made while indexing is off keeps a purged
    experiment out of Knovas until someone reindexes it."""
    if state not in labels.INDEX_STATE_LABELS:
        raise ValueError(f"unknown index state {state!r}")
    ident = canonical_uuid(experiment_id)
    if ident is None:
        return
    if state == "indexed":
        sql = ("UPDATE exp_experiments SET index_state = 'indexed', indexed_at = now(), "
               "index_error = NULL WHERE id = %s")
        params: List[Any] = [ident]
        if if_updated_at is not None:
            sql += " AND updated_at = %s::timestamptz"
            params.append(iso(if_updated_at) if isinstance(if_updated_at, _dt.datetime)
                          else str(if_updated_at))
        conn.execute(sql, params)
        return
    message = _clip_text(error, MAX_INDEX_ERROR_CHARS) if state in ("error", "off") else None
    if state == "off" and message is None:
        conn.execute(
            "UPDATE exp_experiments SET index_state = 'off', index_error = CASE "
            "WHEN index_state = 'off' AND index_error = %s THEN index_error END WHERE id = %s",
            (INDEX_OFF_PURGED, ident),
        )
        return
    conn.execute(
        "UPDATE exp_experiments SET index_state = %s, index_error = %s WHERE id = %s",
        (state, message, ident),
    )


def set_all_index_states(conn: Any, state: str, error: Optional[str] = None) -> int:
    """Every experiment to ``state`` (not 'indexed'); ``error`` as in
    set_index_state. Rows already in the state get the new message too."""
    if state not in labels.INDEX_STATE_LABELS or state == "indexed":
        raise ValueError(f"state {state!r} cannot be set for every experiment")
    message = _clip_text(error, MAX_INDEX_ERROR_CHARS) if state in ("error", "off") else None
    cur = conn.execute("UPDATE exp_experiments SET index_state = %s, index_error = %s "
                       "WHERE index_state <> %s OR index_error IS DISTINCT FROM %s",
                       (state, message, state, message))
    return max(0, int(cur.rowcount or 0))


def record_index_document(conn: Any, pointer: str, experiment_id: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO exp_index_documents (pointer, experiment_id) VALUES (%s, %s) "
        "ON CONFLICT (pointer) DO UPDATE SET experiment_id = EXCLUDED.experiment_id, "
        "indexed_at = now()",
        (str(pointer), canonical_uuid(experiment_id)),
    )


def forget_index_document(conn: Any, pointer: str) -> None:
    conn.execute("DELETE FROM exp_index_documents WHERE pointer = %s", (str(pointer),))


def index_documents(conn: Any, after: Optional[str] = None, limit: int = 500) -> List[str]:
    """Recorded pointers in order, a page after ``after``."""
    n = max(1, min(10_000, int(limit)))
    if after is None:
        rows = conn.execute("SELECT pointer FROM exp_index_documents ORDER BY pointer LIMIT %s",
                            (n,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT pointer FROM exp_index_documents WHERE pointer > %s ORDER BY pointer LIMIT %s",
            (str(after), n),
        ).fetchall()
    return [r[0] for r in rows]


def experiments_for_reindex(conn: Any, *, domain_id: Optional[str] = None,
                            type_id: Optional[str] = None,
                            states: Optional[Sequence[str]] = None,
                            switched_off: bool = False,
                            include_purged: bool = False) -> List[str]:
    """Experiment ids, newest change first. ``states`` limits them to those
    index states; ``switched_off`` adds the ones turned 'off' while indexing
    was switched off (index_error NULL, unlike after purge-index): their
    Knovas copy is missing or stale once indexing is back on. Experiments
    purge-index took out of Knovas are left out unless ``include_purged``:
    only an explicit reindex brings them back, never a rename or pack
    import that re-queues many experiments."""
    where: List[str] = []
    params: List[Any] = []
    if not include_purged:
        where.append("(index_state <> 'off' OR index_error IS DISTINCT FROM %s)")
        params.append(INDEX_OFF_PURGED)
    if domain_id is not None:
        where.append("domain_id = %s")
        params.append(domain_id)
    if type_id is not None:
        where.append("type_id = %s")
        params.append(type_id)
    if states is not None or switched_off:
        tests = []
        if states is not None:
            tests.append("index_state = ANY(%s::text[])")
            params.append([str(s) for s in states])
        if switched_off:
            tests.append("(index_state = 'off' AND index_error IS NULL)")
        where.append("(" + " OR ".join(tests) + ")")
    sql = "SELECT id::text FROM exp_experiments"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return [r[0] for r in conn.execute(sql + " ORDER BY updated_at DESC, id DESC", params).fetchall()]


def index_state_counts(conn: Any) -> Dict[str, int]:
    out = {state: 0 for state in labels.INDEX_STATE_LABELS}
    for state, n in conn.execute(
            "SELECT index_state, count(*)::int FROM exp_experiments GROUP BY index_state").fetchall():
        out[str(state)] = int(n)
    return out


# -- API tokens ---------------------------------------------------------------


def hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _token(row: Sequence[Any]) -> Dict[str, Any]:
    return {"id": row[0], "name": row[1], "hint": row[2], "created_at": iso(row[3]),
            "last_used_at": iso(row[4]), "expires_at": iso(row[5]), "revoked": row[6] is not None}


_TOKEN_COLUMNS = "id::text, name, token_hint, created_at, last_used_at, expires_at, revoked_at"


def insert_token(conn: Any, *, user_id: str, name: str, token_hash: str, hint: str,
                 expires_days: int) -> Dict[str, Any]:
    row = conn.execute(
        "INSERT INTO exp_api_tokens (user_id, name, token_hash, token_hint, expires_at) "
        "VALUES (%s, %s, %s, %s, now() + make_interval(days => %s)) "
        f"RETURNING {_TOKEN_COLUMNS}",
        (user_id, name, token_hash, hint, int(expires_days)),
    ).fetchone()
    return _token(row)


def list_tokens(conn: Any, user_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_TOKEN_COLUMNS} FROM exp_api_tokens WHERE user_id = %s "
        "ORDER BY created_at DESC, id DESC",
        (user_id,),
    ).fetchall()
    return [_token(r) for r in rows]


def revoke_token(conn: Any, user_id: str, token_id: Any) -> Optional[Dict[str, Any]]:
    ident = canonical_uuid(token_id)
    if ident is None:
        return None
    row = conn.execute(
        "UPDATE exp_api_tokens SET revoked_at = COALESCE(revoked_at, now()) "
        f"WHERE id = %s AND user_id = %s RETURNING {_TOKEN_COLUMNS}",
        (ident, user_id),
    ).fetchone()
    return _token(row) if row else None


def resolve_api_token(conn: Any, plaintext: str) -> Optional[Dict[str, Any]]:
    """``{"token_id", "user_id"}`` for an unrevoked, unexpired token, else None.

    Only the SHA-256 of a token is stored; the lookup compares hashes in SQL
    through the unique index. last_used_at is written at most once a minute,
    so a busy CI job does not turn every request into a write.
    """
    if not isinstance(plaintext, str) or not plaintext.startswith(TOKEN_PREFIX) \
            or len(plaintext) > MAX_TOKEN_CHARS:
        return None
    try:
        digest = hash_token(plaintext)
    except UnicodeEncodeError:
        return None
    row = conn.execute(
        "WITH t AS ("
        "  SELECT id, user_id, last_used_at FROM exp_api_tokens "
        "  WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > now()), "
        "u AS ("
        "  UPDATE exp_api_tokens x SET last_used_at = now() FROM t "
        "  WHERE x.id = t.id AND (t.last_used_at IS NULL "
        "        OR t.last_used_at < now() - interval '1 minute') RETURNING x.id) "
        "SELECT t.id::text, t.user_id::text FROM t",
        (digest,),
    ).fetchone()
    if row is None:
        return None
    return {"token_id": row[0], "user_id": row[1]}


# -- runtime settings ---------------------------------------------------------


def _setting_matches(value: Any, default: Any) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, type(default))


def get_runtime_setting(conn: Any, key: str) -> Any:
    """A module setting from the shared ``settings`` table; only keys in
    settings.RUNTIME_DEFAULTS (a stored value of the wrong type reads as the
    default)."""
    if not isinstance(key, str) or key not in RUNTIME_DEFAULTS:
        raise ValidationError(MSG_UNKNOWN_SETTING)
    default = RUNTIME_DEFAULTS[key]
    row = conn.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
    if row is None or not _setting_matches(row[0], default):
        return default
    return row[0]


def set_runtime_setting(conn: Any, key: str, value: Any, actor: Any) -> None:
    if not isinstance(key, str) or key not in RUNTIME_DEFAULTS:
        raise ValidationError(MSG_UNKNOWN_SETTING)
    if not _setting_matches(value, RUNTIME_DEFAULTS[key]):
        raise ValidationError(MSG_SETTING_TYPE)
    conn.execute(
        "INSERT INTO settings (key, value, updated_by) VALUES (%s, %s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
        "updated_by = EXCLUDED.updated_by, updated_at = now()",
        (key, _jsonb(value), str(actor.id) if actor is not None else None),
    )


def _pref_key(user_id: Any) -> Optional[str]:
    ident = canonical_uuid(user_id)
    return None if ident is None else USER_PREF_SHOW_IN_SEARCH.format(user_id=ident)


def get_user_show_in_search(conn: Any, user_id: Any) -> bool:
    key = _pref_key(user_id)
    if key is None:
        return True
    row = conn.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
    if row is None or not isinstance(row[0], bool):
        return True
    return row[0]


def set_user_show_in_search(conn: Any, user_id: Any, value: bool) -> None:
    key = _pref_key(user_id)
    if key is None:
        raise ValidationError("Unbekanntes Konto.")
    if not isinstance(value, bool):
        raise ValidationError(MSG_SETTING_TYPE)
    conn.execute(
        "INSERT INTO settings (key, value, updated_by) VALUES (%s, %s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
        "updated_by = EXCLUDED.updated_by, updated_at = now()",
        (key, _jsonb(value), canonical_uuid(user_id)),
    )


# -- activity -----------------------------------------------------------------


def activity_rows(conn: Any, key: str, limit: int = 50) -> List[Dict[str, Any]]:
    """The experiment's audit entries, newest first: {"at", "action",
    "actor", "detail"} with the actor's current display name, "Gel\u00f6schtes
    Konto" for a removed account, or None for the system."""
    rows = conn.execute(
        "SELECT a.occurred_at, a.action, u.display_name, a.actor_user_id IS NOT NULL, "
        "  a.actor_email_snapshot IS NOT NULL, a.detail "
        "FROM audit_log a LEFT JOIN users u ON u.id = a.actor_user_id "
        "WHERE a.target_type = 'experiment' AND a.target_id = %s "
        "ORDER BY a.occurred_at DESC, a.id DESC LIMIT %s",
        (key, max(1, min(500, int(limit)))),
    ).fetchall()
    out = []
    for r in rows:
        if r[2] is not None:
            actor = r[2]
        elif r[3] or r[4]:
            actor = MSG_DELETED_ACCOUNT
        else:
            actor = None
        out.append({"at": iso(r[0]), "action": r[1], "actor": actor, "detail": r[5] or {}})
    return out
