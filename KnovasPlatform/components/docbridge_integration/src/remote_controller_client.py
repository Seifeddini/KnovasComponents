"""The console's client for the firm's own RemoteController.

Every call goes out as the signed-in person: the same Ed25519 assertion the
Platform sends Knovas, here in the X-Platform-Principal header, verified by
RemoteController's require_operator_or_tenant_admin. No session, no call.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Mapping, Optional
from urllib.parse import urlencode

import requests

from identity.ingestion_compiler import CompiledIngestion

logger = logging.getLogger(__name__)

PRINCIPAL_HEADER = "X-Platform-Principal"

#: What a RemoteController that knows document fields advertises in
#: ``/sync/status["capabilities"]`` (spec 3.8). An older one has no such
#: list, and answers 400 to a sync body carrying the new source keys -- so
#: the console asks before it sends them (spec 2.5).
CAP_SOURCE_FIELDS = "source_fields_v1"
CAP_FIELD_TEMPLATES = "field_templates_v1"
CAP_METADATA_FIELDS = "metadata_fields_v1"
#: A Knovas Connector that maps the file-property items ``keywords`` and
#: ``document_status`` (spec L1); an older one refuses them in the sync body.
CAP_METADATA_FIELDS_V2 = "metadata_fields_v2"
CAP_FIELDS_REQUEUE = "fields_requeue_v1"

#: The metadata items only a Connector with ``metadata_fields_v2`` accepts.
METADATA_ITEMS_V2 = frozenset({"keywords", "document_status"})

#: ``POST /sync/doc-fields/requeue`` outcomes (spec 3.7).
REQUEUE_OUTCOMES = frozenset({"not_accepted", "refused", "reupload_failed", "all"})


def capabilities_from_status(status: Any) -> frozenset[str]:
    """The capability names a ``/sync/status`` answer lists; empty when it
    lists none (an older RemoteController) or is not a status at all."""
    if not isinstance(status, Mapping):
        return frozenset()
    raw = status.get("capabilities")
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    return frozenset(c for c in raw if isinstance(c, str) and c)


#: A version as knovas-extract spells it (PEP 440); anything else in an
#: answer is not shown.
_EXTRACTOR_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+!_-]{0,39}")


def extractor_version_from_status(status: Any) -> Optional[str]:
    """``extraction.knovas_extract_version`` of a ``/sync/status`` answer;
    None from a Knovas Connector too old to report it, or when the value is
    not a plain version string."""
    if not isinstance(status, Mapping):
        return None
    block = status.get("extraction")
    if not isinstance(block, Mapping):
        return None
    version = block.get("knovas_extract_version")
    if isinstance(version, str) and _EXTRACTOR_VERSION_RE.fullmatch(version):
        return version
    return None


def advertised_capabilities(rc_client: Any) -> Optional[frozenset[str]]:
    """What a RemoteController client advertises; None when it cannot be
    asked, so an unreachable one is never reported as too old. A client
    without ``reachable_capabilities`` (an older test double) answers
    through ``capabilities``."""
    reachable = getattr(rc_client, "reachable_capabilities", None)
    if callable(reachable):
        return reachable()
    capabilities = getattr(rc_client, "capabilities", None)
    return frozenset(capabilities() or ()) if callable(capabilities) else frozenset()


def requeue_supported(rc_client: Any) -> Optional[bool]:
    """Whether ``rc_client`` can re-send uploads by field outcome: True when
    the RemoteController advertises ``fields_requeue_v1``, False for an
    older one (or none configured), None when it cannot be asked now."""
    if rc_client is None or not callable(getattr(rc_client, "requeue_doc_fields", None)):
        return False
    available = advertised_capabilities(rc_client)
    if available is None:
        return None
    return CAP_FIELDS_REQUEUE in available


def required_capabilities(sync_request: Mapping[str, Any]) -> frozenset[str]:
    """What a RemoteController must advertise to accept ``sync_request``.

    Empty for a body without document fields, which every RemoteController
    accepts. ``fields`` needs ``source_fields_v1``; templates and metadata
    items need theirs on top of it, and the items ``keywords`` and
    ``document_status`` also ``metadata_fields_v2``.
    """
    needed: set[str] = set()
    for source in (sync_request or {}).get("sources") or ():
        if not isinstance(source, Mapping):
            continue
        if source.get("fields"):
            needed.add(CAP_SOURCE_FIELDS)
        if source.get("field_templates"):
            needed.update((CAP_SOURCE_FIELDS, CAP_FIELD_TEMPLATES))
        items = source.get("metadata_fields")
        if items:
            needed.update((CAP_SOURCE_FIELDS, CAP_METADATA_FIELDS))
            if any(item in METADATA_ITEMS_V2 for item in items):
                needed.add(CAP_METADATA_FIELDS_V2)
    return frozenset(needed)

#: The scheduler states RemoteController reports while a continuous worker
#: exists. Read off ``RC/src/sync/sync_scheduler.py::_set_status``: every
#: status ``_run_once`` sets is set *by the worker*, so seeing one means a
#: worker is looping and will re-read the body at its next cycle. Everything
#: else -- ``not_running``, ``completed``, ``awaiting_initial_sync_body``,
#: ``worker_crashed``, ``worker_stopped``, and a missing key -- is idle, and
#: an idle scheduler has to be started for the profile to take effect.
SCHEDULER_RUNNING_STATES = frozenset({
    "running",
    "paused_outside_window",
    "idle_between_cycles",
    "backlog_pending",
    "subfolders_complete",
    "disabled",
    "error",
    # paused_reason values the executor reports mid-cycle; the worker is alive
    "scan_limit_reached",
    "cycle_time_limit",
    "stop_requested",
    "rate_limited",
})

#: A status render happens on every page load and a preview makes one
#: discover call per folder, so neither may sit on the long push timeout: a
#: RemoteController that blackholes instead of refusing would turn the tab
#: into a gunicorn timeout (M4).
STATUS_TIMEOUT_SECONDS = 5.0
DISCOVER_TIMEOUT_SECONDS = 10.0
#: A preview is someone waiting in front of an empty dialog; Graph answers in
#: well under a second, and past this the dialog falls back to the indexed text.
PREVIEW_TIMEOUT_SECONDS = 8.0


class RemoteControllerError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RemoteControllerClient:
    def __init__(self, base_url: str, *, principal_broker, session=None,
                 timeout: float = 20.0) -> None:
        self._base = base_url.rstrip("/")
        self._broker = principal_broker
        self._session = session or requests.Session()
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        user = self._broker.current_user()
        if user is None:
            raise PermissionError("Kein angemeldeter Benutzer; der Knovas Connector wird nicht aufgerufen.")
        return {PRINCIPAL_HEADER: self._broker.assertion_for(user),
                "Content-Type": "application/json"}

    def _call(self, method: str, path: str, *, body: Any = None, query: dict | None = None,
              timeout: float | None = None) -> Any:
        url = f"{self._base}{path}"
        if query:
            url += "?" + urlencode({k: v for k, v in query.items() if v is not None})
        try:
            resp = self._session.request(method, url, json=body, headers=self._headers(),
                                         timeout=self._timeout if timeout is None else timeout)
        except requests.RequestException as exc:
            raise RemoteControllerError(f"Der Knovas Connector ist nicht erreichbar: {exc}", status=None) from exc
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {}
        if resp.status_code >= 400:
            message = str((payload or {}).get("error") or f"HTTP {resp.status_code}")
            raise RemoteControllerError(message, status=resp.status_code)
        return payload

    def discover(self, root: str | None = None, max_depth: int = 3) -> dict:
        return self._call("GET", "/discover", query={"root": root, "max_depth": max_depth},
                          timeout=DISCOVER_TIMEOUT_SECONDS)

    def m365_preview(self, doc_id: str, page: int | None = None) -> dict:
        """Microsoft's embeddable viewer URL for an indexed OneDrive/SharePoint file."""
        body: dict[str, Any] = {"doc_id": doc_id}
        if page:
            body["page"] = int(page)
        return self._call("POST", "/m365/preview", body=body, timeout=PREVIEW_TIMEOUT_SECONDS)

    def status(self) -> dict:
        return self._call("GET", "/sync/status", timeout=STATUS_TIMEOUT_SECONDS)

    def health(self) -> dict:
        """``status()`` under the name the System tab's check looks up."""
        return self.status()

    def capabilities(self) -> frozenset[str]:
        """What this RemoteController advertises; empty for an older one, and
        empty when it cannot be asked (nobody signed in, unreachable), since
        then nothing it does not list can be relied on either."""
        return self.reachable_capabilities() or frozenset()

    def reachable_capabilities(self) -> Optional[frozenset[str]]:
        """``capabilities()``, but None when the RemoteController cannot be
        asked (nobody signed in, unreachable, an error answer) -- so a caller
        can tell "not reachable" from "too old" (empty)."""
        try:
            return capabilities_from_status(self.status())
        except (RemoteControllerError, PermissionError) as exc:
            logger.info("Knovas-Connector-Faehigkeiten nicht abrufbar: %s", type(exc).__name__)
            return None

    def requeue_doc_fields(self, outcome: str) -> int:
        """Queue documents with this field outcome for re-upload; the count.

        ``outcome`` is one of REQUEUE_OUTCOMES. RemoteController re-sends them
        within its per-cycle bound, each one a billed upload.
        """
        if outcome not in REQUEUE_OUTCOMES:
            raise ValueError(f"unknown requeue outcome: {outcome!r}")
        payload = self._call("POST", "/sync/doc-fields/requeue", body={"outcome": outcome})
        try:
            return max(0, int((payload or {}).get("requeued") or 0))
        except (TypeError, ValueError, AttributeError):
            return 0

    def requeue_reextract(self) -> dict:
        """Queue every document an older extraction produced for
        re-extraction (``POST /sync/reextract/requeue``); ``{"requeued": n}``.

        The Knovas Connector re-extracts them within its per-cycle bound and
        uploads only those whose text changed -- each such upload is billed.
        An older Connector answers 404: RemoteControllerError with
        ``status == 404``.
        """
        payload = self._call("POST", "/sync/reextract/requeue", body={})
        try:
            requeued = max(0, int((payload or {}).get("requeued") or 0))
        except (TypeError, ValueError, AttributeError):
            requeued = 0
        return {"requeued": requeued}

    def start(self) -> dict:
        return self._call("POST", "/sync/start", body={})

    def stop(self) -> dict:
        return self._call("POST", "/sync/stop", body={})

    def get_sync_config(self) -> dict:
        return self._call("GET", "/sync/config")

    def _previous_sync_config(self) -> dict:
        """The config a rollback would restore, or a sentence naming the switch.

        RemoteController's sync-config API is off unless
        RC_SYNC_CONFIG_API_ENABLED is true, and a disabled API answers 404
        with "Sync config API is disabled" -- true, and useless to the
        administrator who reads it in the console. Name the variable instead.
        """
        try:
            return self.get_sync_config()
        except RemoteControllerError as exc:
            if exc.status == 404:
                raise RemoteControllerError(
                    "Der Knovas Connector hat die Sync-Konfigurations-API abgeschaltet "
                    "(RC_SYNC_CONFIG_API_ENABLED=false); ohne sie kann das Profil "
                    "nicht uebertragen werden.",
                    status=404,
                ) from exc
            raise

    def push(self, compiled: CompiledIngestion) -> dict:
        """Config first, then the folder list, then whatever it takes to make
        the profile actually run. Returns ``{"applied": ...}``, one of:

        - ``"started"``  -- the scheduler was idle and has been started;
        - ``"next_cycle"`` -- a worker is already running and re-reads the
          body at the top of its next cycle;
        - ``"stored"`` -- a one_time (``manual``) profile, which only runs
          when a person presses Start; or a continuous one whose start
          failed, in which case ``start_error`` carries the reason.

        Never ``POST /sync``: that route answers ``already_running`` and
        changes nothing when a worker holds the lock, and in one_time mode it
        performs a whole scan-and-upload inside the request. ``/sync/body``
        stores, and starting is a separate decision.

        If RemoteController refuses the folder list, the previous config is
        put back so the two never diverge. A failed *start* is not rolled
        back: the profile is on RemoteController, and undoing the config
        would create exactly the divergence the rollback exists to prevent.
        """
        previous = self._previous_sync_config()
        self._call("POST", "/sync/config", body=compiled.sync_config)
        try:
            self._call("POST", "/sync/body", body=compiled.sync_request)
        except RemoteControllerError:
            try:
                self._call("POST", "/sync/config", body=previous)
            except Exception as rollback_exc:  # noqa: BLE001
                logger.error("Rollback der Sync-Konfiguration fehlgeschlagen: %s", rollback_exc)
            raise

        if compiled.sync_config.get("mode") != "continuous":
            return {"applied": "stored"}
        try:
            state = str((self.status() or {}).get("scheduler_state") or "")
            if state in SCHEDULER_RUNNING_STATES:
                return {"applied": "next_cycle"}
            self.start()
        except RemoteControllerError as exc:
            # Reading the state or starting the worker failed. Say so; do not
            # pretend the profile did not arrive, and do not roll it back.
            return {"applied": "stored", "start_error": str(exc)}
        return {"applied": "started"}
