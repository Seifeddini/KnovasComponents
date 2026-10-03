"""Document fields: what this Knovas tenant supports, learned, never configured.

Why this exists
---------------
Document fields are gated at Knovas per tenant (flags, allowlist, relevance
calibration), and every overlay ships them off. The Platform must not show a
filter it cannot honour or a panel the server will 404 (H6), so it asks: a
side-effect-free probe, ``POST /secured/graph/doc-values/find`` with ``{}``
(spec 2.2), answered by the server's gate order:

    404 HTTP_404 (or any non-JSON 404)    -> off
    400 where_unsupported                 -> values
    400 invalid_value, path "where"       -> filters
    401, 403, 429, 5xx, network error     -> unknown (shown as off, 30 s)

``listing_only`` cannot be probed -- ``find`` never checks calibration -- so
it is learned from the first query that answers 503
``where_requires_calibration`` and held for ``calibration_recheck_seconds``
(``LISTING_ONLY_HOLD_SECONDS``, five minutes: Knovas 1.5.0 calls that answer
a temporary problem on its side); the probe does not lift it in the meantime.

``web.doc_fields.ui: off`` and a client outside secured mode turn the
feature off without asking anyone (D1, D13). Nothing here can turn it on.

Process-wide state
------------------
One tenant per deployment, so one capability per process: ``shared_cache()``
is a singleton (gunicorn workers each learn it on their own). The registry,
entity-name and node-name caches are per *user*, because what Knovas returns
depends on who asks (``target_type_hidden``, node visibility); they are never
shared across people. ``reset_for_tests()`` clears all of it.

Nothing here logs a value: capability names, signal names and exception
class names only.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from doc_fields_view import NOTICE_NAMES_MAX, registry_targets, sanitize_registry

logger = logging.getLogger(__name__)


class Capability(str, enum.Enum):
    """What the tenant supports, from least to most."""

    off = "off"
    values = "values"
    listing_only = "listing_only"
    filters = "filters"
    unknown = "unknown"

    @property
    def shows_values(self) -> bool:
        """Detail panel and admin tab (D11)."""
        return self in (Capability.values, Capability.listing_only, Capability.filters)

    @property
    def shows_listing(self) -> bool:
        """Typed values on cards, the listing, the admin Feldfilter."""
        return self in (Capability.listing_only, Capability.filters)

    @property
    def shows_filters(self) -> bool:
        """The search filter rail. ``where`` goes out only here (H1)."""
        return self is Capability.filters

    @property
    def sends_return_fields(self) -> bool:
        """``return_fields`` goes out for filters and listing_only (H1)."""
        return self.shows_listing


SIGNALS = frozenset({"where_unsupported", "feature_off", "echo_missing", "needs_calibration"})

# The probe's string answers (KnovasAPIClient.doc_fields_probe).
_PROBE_ANSWERS = {
    "off": Capability.off,
    "values": Capability.values,
    "filters": Capability.filters,
}

DEFAULT_CAPABILITY_TTL = 300
DEFAULT_UNKNOWN_TTL = 30
#: How long a 503 ``where_requires_calibration`` holds the capability at
#: ``listing_only`` (search filters hidden, listing and card values kept)
#: before the probe may lift it. Knovas 1.5.0 calls that answer "a problem
#: on the Knovas side. Try again later." -- temporary, so the Platform asks
#: again after five minutes, not after an hour (spec F6).
LISTING_ONLY_HOLD_SECONDS = 300
DEFAULT_REGISTRY_CACHE = 300
DEFAULT_FIND_PAGE_SIZE = 50
FIND_PAGE_SIZE_MAX = 200  # DOC_FIELDS_FIND_MAX_LIMIT on the server
ENTITY_NAMES_MAX = 5000
#: Auto-scope notice names (spec F3): at most this many node reads per
#: notice -- one more than the names it shows, so one node the person may
#: not see does not cost a visible one its name; every other node is counted.
NODE_NAME_READS_MAX = NOTICE_NAMES_MAX + 1
#: Each node read's timeout, and the time after which no further read
#: starts: the names are optional and must not hold a search up.
NODE_NAME_TIMEOUT = 2.0
#: Remembered (person, node) answers; past this, expired ones are dropped,
#: then all of them.
NODE_NAMES_KEPT_MAX = 10000
KNOWN_ROLES = frozenset({"admin", "approver", "ingestion_manager", "member"})
_OFF_WORDS = frozenset({"off", "false", "0", "no", "disabled"})
_WARNED_ROLES: set = set()


# ---------------------------------------------------------------------------
# Settings (config.yaml web.doc_fields)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DocFieldsSettings:
    ui_enabled: bool
    capability_ttl: int
    unknown_ttl: int
    calibration_recheck: int
    registry_cache_seconds: int
    edit_roles: frozenset
    find_page_size: int


def _cfg(config: Any, key: str, default: Any) -> Any:
    getter = getattr(config, "get", None)
    if not callable(getter):
        return default
    try:
        value = getter(key, default)
    except Exception:  # noqa: BLE001 - a broken config keeps the default
        return default
    return default if value is None else value


def _cfg_int(config: Any, key: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(_cfg(config, key, default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def parse_edit_roles(raw: Any) -> frozenset:
    """``"admin, member"`` -> {"admin", "member"}. Unknown role names are
    dropped with a warning; an empty result falls back to ``admin``."""
    if isinstance(raw, (list, tuple, set, frozenset)):
        items = [str(r) for r in raw]
    else:
        items = str(raw or "").split(",")
    roles = {r.strip().lower() for r in items if r.strip()}
    unknown = roles - KNOWN_ROLES - _WARNED_ROLES
    if unknown:
        _WARNED_ROLES.update(unknown)
        logger.warning("web.doc_fields.edit_roles: ignoring unknown role(s) %s", sorted(unknown))
    roles &= KNOWN_ROLES
    return frozenset(roles or {"admin"})


def ui_enabled(config: Any) -> bool:
    """``web.doc_fields.ui``: ``auto`` (default) or ``off``. It can only
    switch the feature off; what Knovas supports decides the rest (D1)."""
    return str(_cfg(config, "web.doc_fields.ui", "auto")).strip().lower() not in _OFF_WORDS


def settings(config: Any) -> DocFieldsSettings:
    """``web.doc_fields`` from a ConfigLoader (or any object with ``get``)."""
    return DocFieldsSettings(
        ui_enabled=ui_enabled(config),
        capability_ttl=_cfg_int(config, "web.doc_fields.capability_ttl_seconds",
                                DEFAULT_CAPABILITY_TTL, 1, 86400),
        unknown_ttl=DEFAULT_UNKNOWN_TTL,
        calibration_recheck=_cfg_int(config, "web.doc_fields.calibration_recheck_seconds",
                                     LISTING_ONLY_HOLD_SECONDS, 1, 7 * 86400),
        registry_cache_seconds=_cfg_int(config, "web.doc_fields.registry_cache_seconds",
                                        DEFAULT_REGISTRY_CACHE, 0, 86400),
        edit_roles=parse_edit_roles(_cfg(config, "web.doc_fields.edit_roles", "admin")),
        find_page_size=_cfg_int(config, "web.doc_fields.find_page_size",
                                DEFAULT_FIND_PAGE_SIZE, 1, FIND_PAGE_SIZE_MAX),
    )


def is_secured(client: Any) -> bool:
    """Secured mode (``use_secured_api`` and mTLS). Outside it the legacy GET
    search has nowhere to carry ``where`` and no doc-fields route exists
    (D13). A client may answer through ``secured_mode()``."""
    probe = getattr(client, "secured_mode", None)
    if callable(probe):
        try:
            return bool(probe())
        except Exception:  # noqa: BLE001 - unknown means not secured
            return False
    return bool(getattr(client, "use_secured_api", False) and getattr(client, "mtls_enabled", False))


# ---------------------------------------------------------------------------
# The capability cache
# ---------------------------------------------------------------------------

def classify_probe(answer: Any) -> Capability:
    """The probe's string answer as a Capability; anything else is unknown."""
    return _PROBE_ANSWERS.get(str(answer or ""), Capability.unknown)


class CapabilityCache:
    """The tenant's capability, probed lazily and corrected by signals.

    ``get(client)`` answers from the cache while it is fresh, else probes
    once (other threads wait for that probe rather than sending their own).
    A probe that cannot run because nobody is signed in answers ``unknown``
    and caches nothing, so the next signed-in request probes. ``observe``
    applies the signals of spec 2.2.
    """

    def __init__(self, ttl: float = DEFAULT_CAPABILITY_TTL,
                 unknown_ttl: float = DEFAULT_UNKNOWN_TTL,
                 calibration_recheck: float = LISTING_ONLY_HOLD_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = float(ttl)
        self._unknown_ttl = float(unknown_ttl)
        self._recheck = float(calibration_recheck)
        self._clock = clock
        self._lock = threading.Lock()
        self._probe_lock = threading.Lock()
        self._value: Optional[Capability] = None
        self._expires_at = 0.0
        self._held_until: Optional[float] = None

    @classmethod
    def from_settings(cls, cfg: DocFieldsSettings,
                      clock: Callable[[], float] = time.monotonic) -> "CapabilityCache":
        return cls(cfg.capability_ttl, cfg.unknown_ttl, cfg.calibration_recheck, clock)

    def configure(self, cfg: DocFieldsSettings) -> None:
        """Adopt the TTLs of ``cfg`` for answers stored from now on."""
        with self._lock:
            self._ttl = float(cfg.capability_ttl)
            self._unknown_ttl = float(cfg.unknown_ttl)
            self._recheck = float(cfg.calibration_recheck)

    # -- reading ---------------------------------------------------------

    def _fresh(self, now: float) -> Optional[Capability]:
        """The cached answer if it still holds (call with the lock held)."""
        if self._held_until is not None:
            if now < self._held_until:
                return Capability.listing_only
            self._held_until = None
            self._value = None
        if self._value is not None and now < self._expires_at:
            return self._value
        return None

    def peek(self) -> Optional[Capability]:
        """The cached capability without probing, or None when stale."""
        with self._lock:
            return self._fresh(self._clock())

    def get(self, client: Any) -> Capability:
        with self._lock:
            cached = self._fresh(self._clock())
        if cached is not None:
            return cached
        with self._probe_lock:
            with self._lock:
                cached = self._fresh(self._clock())
            if cached is not None:
                return cached
            try:
                answer = client.doc_fields_probe()
            except PermissionError:
                # A broker is attached and nobody is signed in: nothing was
                # sent. Not the tenant's answer, so nothing is cached.
                return Capability.unknown
            except Exception as exc:  # noqa: BLE001 - any failure is "unknown"
                logger.warning("Document fields probe failed: %s", type(exc).__name__)
                answer = "unknown"
            capability = classify_probe(answer)
            with self._lock:
                held = self._fresh(self._clock())
                if held is Capability.listing_only:
                    # needs_calibration arrived while the probe was out.
                    return held
                self._set(capability, "probe")
            return capability

    # -- writing ---------------------------------------------------------

    def _set(self, capability: Capability, source: str) -> None:
        """Store an answer (call with the lock held)."""
        now = self._clock()
        ttl = self._unknown_ttl if capability is Capability.unknown else self._ttl
        if capability is not self._value:
            logger.info("Document fields capability: %s -> %s (%s)",
                        self._value.value if self._value else "-", capability.value, source)
        self._value = capability
        self._expires_at = now + ttl

    def observe(self, signal: str) -> None:
        """Apply what another call revealed (spec 2.2).

        ``where_unsupported`` -> values; ``feature_off`` -> off (both lift a
        calibration hold, because they say more than it does);
        ``echo_missing`` -> unknown, and the next call probes again;
        ``needs_calibration`` -> listing_only, held for the recheck period
        (``LISTING_ONLY_HOLD_SECONDS`` unless configured).
        """
        if signal not in SIGNALS:
            raise ValueError(f"unknown document fields signal: {signal!r}")
        with self._lock:
            now = self._clock()
            if signal == "where_unsupported":
                self._held_until = None
                self._set(Capability.values, signal)
            elif signal == "feature_off":
                self._held_until = None
                self._set(Capability.off, signal)
            elif signal == "echo_missing":
                if self._value is not Capability.unknown:
                    logger.info("Document fields capability: %s -> unknown (echo_missing)",
                                self._value.value if self._value else "-")
                self._value = Capability.unknown
                self._expires_at = now  # stale: the next get() probes
            else:  # needs_calibration
                self._held_until = now + self._recheck
                self._set(Capability.listing_only, signal)
                self._expires_at = self._held_until


def signal_for(exc: BaseException) -> Optional[str]:
    """The signal an exception from a doc-fields call or a query carries.

    ``DocFieldsUnavailable`` -> feature_off; an error with code
    ``where_unsupported`` -> where_unsupported; a 503
    ``where_requires_calibration`` -> needs_calibration. None otherwise.
    """
    from knovas_client import DocFieldsUnavailable

    if isinstance(exc, DocFieldsUnavailable):
        return "feature_off"
    code = getattr(exc, "error_code", None)
    if code == "where_unsupported":
        return "where_unsupported"
    if code == "where_requires_calibration":
        return "needs_calibration"
    return None


# ---------------------------------------------------------------------------
# Process-wide hooks
# ---------------------------------------------------------------------------

_SHARED: Optional[CapabilityCache] = None
_SHARED_CONFIGURED = False
_SHARED_LOCK = threading.Lock()


def shared_cache(config: Any = None) -> CapabilityCache:
    """The process singleton. Its TTLs come from the first config it is
    given (until then, the defaults: ``observe`` may run before any
    ``capability_for``)."""
    global _SHARED, _SHARED_CONFIGURED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = CapabilityCache.from_settings(settings(config))
            _SHARED_CONFIGURED = config is not None
        elif config is not None and not _SHARED_CONFIGURED:
            _SHARED.configure(settings(config))
            _SHARED_CONFIGURED = True
        return _SHARED


def capability_for(client: Any) -> Capability:
    """``off`` when ``web.doc_fields.ui`` is off or the client is not in
    secured mode -- without a request; otherwise the shared cache's answer.
    """
    config = getattr(client, "config", None)
    if not ui_enabled(config) or not is_secured(client):
        return Capability.off
    return shared_cache(config).get(client)


def observe(signal: str) -> None:
    """``shared_cache().observe(signal)``."""
    shared_cache().observe(signal)


def observe_exception(exc: BaseException) -> Optional[str]:
    """Observe the signal ``exc`` carries, if any; return it."""
    signal = signal_for(exc)
    if signal is not None:
        observe(signal)
    return signal


# ---------------------------------------------------------------------------
# Per-user caches: registry, entity names and node names
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _RegistryEntry:
    expires_at: float
    fields: Tuple[Dict[str, Any], ...]
    targets: Dict[str, str]


_now: Callable[[], float] = time.monotonic
_CACHE_LOCK = threading.Lock()
_REGISTRY: Dict[str, _RegistryEntry] = {}
_NAMES: Dict[Tuple[str, str], Tuple[float, Optional[Tuple[str, ...]]]] = {}
#: (person, node id) -> (expires_at, name; None for a node Knovas does not
#: show them).
_NODE_NAMES: Dict[Tuple[str, str], Tuple[float, Optional[str]]] = {}
#: person -> until when their node reads are skipped after one failed.
_NODE_NAMES_DOWN: Dict[str, float] = {}


def _user(user_key: Any) -> str:
    return "" if user_key is None else str(user_key)


def _registry_entry(client: Any, user_key: Any) -> _RegistryEntry:
    who = _user(user_key)
    now = _now()
    with _CACHE_LOCK:
        entry = _REGISTRY.get(who)
    if entry is not None and now < entry.expires_at:
        return entry
    raw = client.doc_fields()  # failures propagate and are not cached
    ttl = settings(getattr(client, "config", None)).registry_cache_seconds
    entry = _RegistryEntry(
        expires_at=now + ttl,
        fields=tuple(sanitize_registry(raw)),
        targets=registry_targets(raw),
    )
    with _CACHE_LOCK:
        _REGISTRY[who] = entry
    return entry


def registry_for(client: Any, user_key: Any) -> List[Dict[str, Any]]:
    """``sanitize_registry(client.doc_fields())`` for this user, cached for
    ``registry_cache_seconds``. A failure is raised and not cached. Callers
    get copies; the cache cannot be changed through them."""
    return [dict(spec) for spec in _registry_entry(client, user_key).fields]


def last_known_registry(user_key: Any) -> Optional[List[Dict[str, Any]]]:
    """This user's last registry read, expired or not, without asking
    Knovas; None when there is none (or it was invalidated). Only for
    decisions that must not fail open when a fresh read fails -- the
    deadline banner (H9)."""
    with _CACHE_LOCK:
        entry = _REGISTRY.get(_user(user_key))
    return None if entry is None else [dict(spec) for spec in entry.fields]


def registry_targets_for(client: Any, user_key: Any) -> Dict[str, str]:
    """``{key: target_node_type_id}`` of this user's registry: the entity
    fields whose target node type the person can see, from the same cached
    entry as ``registry_for``. Server-side only -- the ids never go to the
    browser. A failure is raised and not cached; callers get a copy."""
    return dict(_registry_entry(client, user_key).targets)


def entity_names_for(client: Any, user_key: Any, field: Any) -> Optional[List[str]]:
    """Suggestion names for an entity field, or None for free text only.

    The names of the field's target node type, fetched with
    ``graph_nodes(node_type_id=...)`` and **never with ``q``**: Knovas reads
    ``q`` from the query string only, and the gateway logs request lines, so
    a typed prefix must not leave the Platform (D10). The caller filters
    these names itself. None when the field is unknown, not an entity field,
    ``special``, has no target the person can see, when the node list
    cannot be read, or when it holds more than 5000 nodes.
    """
    key = field.get("key") if isinstance(field, dict) else field
    if not isinstance(key, str) or not key:
        return None
    entry = _registry_entry(client, user_key)
    spec = next((s for s in entry.fields if s.get("key") == key), None)
    if spec is None or spec.get("datatype") != "entity_ref" or spec.get("sensitivity") != "normal":
        return None
    target = entry.targets.get(key)
    if not target:
        return None
    who, now = _user(user_key), _now()
    with _CACHE_LOCK:
        cached = _NAMES.get((who, target))
    if cached is not None and now < cached[0]:
        return list(cached[1]) if cached[1] is not None else None
    try:
        nodes = client.graph_nodes(node_type_id=target)
    except Exception as exc:  # noqa: BLE001 - suggestions are optional
        logger.warning("Entity suggestions unavailable: %s", type(exc).__name__)
        return None
    names: Optional[Tuple[str, ...]]
    if len(nodes or ()) > ENTITY_NAMES_MAX:
        names = None
    else:
        unique = {str(n.get("name")).strip() for n in nodes or ()
                  if isinstance(n, dict) and isinstance(n.get("name"), str) and n["name"].strip()}
        names = tuple(sorted(unique, key=lambda s: (s.casefold(), s)))
    ttl = settings(getattr(client, "config", None)).registry_cache_seconds
    with _CACHE_LOCK:
        _NAMES[(who, target)] = (now + ttl, names)
    return list(names) if names is not None else None


def _keep_node_name(key: Tuple[str, str], expires_at: float, name: Optional[str]) -> None:
    """Remember one node answer (call with ``_CACHE_LOCK`` held), within
    ``NODE_NAMES_KEPT_MAX`` entries."""
    if len(_NODE_NAMES) >= NODE_NAMES_KEPT_MAX:
        now = _now()
        for stale in [k for k, (until, _) in _NODE_NAMES.items() if until <= now]:
            del _NODE_NAMES[stale]
        if len(_NODE_NAMES) >= NODE_NAMES_KEPT_MAX:
            _NODE_NAMES.clear()
    _NODE_NAMES[key] = (expires_at, name)


def node_names_for(client: Any, user_key: Any,
                   node_ids: Iterable[Any]) -> Tuple[List[str], int]:
    """``(names, hidden_count)`` for knowledge-graph node ids, as this
    person may see them (spec F3, auto scope).

    Each node is read on its own as this person (``graph_node_name``: one
    ``GET /secured/graph/nodes/<id>``; never the node list, never ``q``), in
    the order of ``node_ids``, until five are named. A node Knovas does not
    show them (404) is counted, never named -- and so is every node left
    unread: after ``NODE_NAME_READS_MAX`` reads, once ``NODE_NAME_TIMEOUT``
    seconds have passed, or after a failed read. Answers are cached per
    (person, node) for ``registry_cache_seconds``. A failed read (timeout,
    refusal) is not retried, and for ``unknown_ttl`` this person's notices
    count without reading: the names never hold a search up for long.
    Names keep the order of ``node_ids``, each once. Never raises; logs
    exception class names only.
    """
    wanted = list(dict.fromkeys(str(i) for i in node_ids or () if i))
    if not wanted:
        return [], 0
    who, start = _user(user_key), _now()
    with _CACHE_LOCK:
        down = start < _NODE_NAMES_DOWN.get(who, 0.0)
    cfg: Optional[DocFieldsSettings] = None
    names: List[str] = []
    named = reads = 0
    for node_id in wanted:
        if len(names) >= NOTICE_NAMES_MAX:
            break
        now = _now()
        with _CACHE_LOCK:
            cached = _NODE_NAMES.get((who, node_id))
        if cached is not None and now < cached[0]:
            name = cached[1]
        elif down or reads >= NODE_NAME_READS_MAX or now - start >= NODE_NAME_TIMEOUT:
            continue
        else:
            reads += 1
            cfg = cfg or settings(getattr(client, "config", None))
            try:
                name = client.graph_node_name(node_id, timeout=NODE_NAME_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - names are optional
                logger.warning("Node names unavailable: %s", type(exc).__name__)
                down = True
                with _CACHE_LOCK:
                    _NODE_NAMES_DOWN[who] = _now() + cfg.unknown_ttl
                continue
            name = name.strip() if isinstance(name, str) and name.strip() else None
            with _CACHE_LOCK:
                _keep_node_name((who, node_id), _now() + cfg.registry_cache_seconds, name)
        if name:
            named += 1
            if name not in names:
                names.append(name)
    return names, len(wanted) - named


def invalidate(user_key: Any = None) -> None:
    """Drop the registry, entity-name and node-name caches of one user, or
    of everyone when ``user_key`` is None (after a registry write, or an
    ``unknown_field`` answer)."""
    with _CACHE_LOCK:
        if user_key is None:
            _REGISTRY.clear()
            _NAMES.clear()
            _NODE_NAMES.clear()
            _NODE_NAMES_DOWN.clear()
            return
        who = _user(user_key)
        _REGISTRY.pop(who, None)
        _NODE_NAMES_DOWN.pop(who, None)
        for cache in (_NAMES, _NODE_NAMES):
            for key in [k for k in cache if k[0] == who]:
                cache.pop(key, None)


def reset_for_tests() -> None:
    """Forget the shared capability and every per-user cache."""
    global _SHARED, _SHARED_CONFIGURED
    with _SHARED_LOCK:
        _SHARED = None
        _SHARED_CONFIGURED = False
    invalidate()
