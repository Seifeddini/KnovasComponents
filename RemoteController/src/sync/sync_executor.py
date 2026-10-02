"""Orchestrate discover → read → upload with incremental state."""
from __future__ import annotations

import fnmatch
import logging
import os
import re
from collections import Counter
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional

import requests

from config import doc_fields_enabled, fields_reupload_max_attempts, fields_reupload_per_cycle
from discover.filesystem import resolve_root
from m365.inventory import RemoteFile
from m365.source import M365Source, active_m365_source, m365_configured, watch_root_subpath
from sync import doc_fields_metrics, ocr_metrics
from sync.doc_fields_payload import (
    EMPTY_SOURCE_SPEC,
    OUTCOME_CLEARED,
    OUTCOME_NONE,
    OUTCOME_NOT_ACCEPTED,
    OUTCOME_STAGED,
    REFUSED_PREFIX,
    REUPLOAD_FAILED_PREFIX,
    FieldsOutcome,
    FieldsRecord,
    SourceSpec,
    TemplateError,
    config_digest,
    record_for,
    reupload_failed_record,
    spec_from_source,
)
from sync.document_text import (
    DEFAULT_INCLUDE_GLOBS,
    is_syncable_extension,
    is_unconvertible_error,
)
from sync.knovas_uploader import SemantixUploader, UploadResult
from sync.rate_metrics import IngestRateMetrics
from sync.semantix_cert import ensure_mtls_certificate_freshness
from sync.subfolder_queue import SubfolderProgress, SubfolderQueue
from sync.sync_config import effective_filters
from sync.sync_state import (
    DocumentSyncRecord,
    DocumentSyncStatus,
    DocumentSyncSummary,
    SyncStateStore,
    status_from_fingerprint,
)
from sync.sync_state_db import REQUEUE_DIGEST

logger = logging.getLogger(__name__)

#: One upload-queue entry: (abs_path or RemoteFile, relative_path, mtime_iso,
#: size_bytes, the SourceSpec that governs the document's fields and groups).
UploadItem = tuple[Any, str, str, int, SourceSpec]


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; using default %d", name, raw, default)
        return default


#: Retryable extraction failures (wall-clock kill, killed child, transient
#: error) a file may collect before it is recorded partial for the
#: OCR-disabled backfill pass instead of burning the full timeout every
#: cycle (GI-EXTRACT-02). `RC_EXTRACT_MAX_RETRIES`, read once at import.
MAX_EXTRACT_RETRIES = _env_int("RC_EXTRACT_MAX_RETRIES", 3, minimum=1)

#: The partial note recorded when the retry cap is reached.
RETRIES_EXHAUSTED_NOTE = {"reason": "extract_retries_exhausted"}

UPLOAD_ORDERS = ("small_first", "scan")
DEFAULT_UPLOAD_ORDER = "small_first"


def upload_order() -> str:
    """`RC_UPLOAD_ORDER`: small_first (default) uploads the cycle's queue
    smallest file first, so one 300-page scan does not hold back the
    letters behind it; `scan` keeps the directory order."""
    raw = (os.environ.get("RC_UPLOAD_ORDER") or "").strip().lower()
    if not raw:
        return DEFAULT_UPLOAD_ORDER
    if raw in UPLOAD_ORDERS:
        return raw
    logger.warning("Invalid RC_UPLOAD_ORDER=%r; using %s", raw, DEFAULT_UPLOAD_ORDER)
    return DEFAULT_UPLOAD_ORDER


def _ordered_upload_queue(queue: list[UploadItem], order: str):
    if order == "small_first":
        # Stable: files of equal size keep their scan order.
        return sorted(queue, key=lambda item: int(item[3]))
    return list(queue)


def _matches_globs(rel_posix: str, patterns: list[str]) -> bool:
    path = Path(rel_posix)
    return any(path.match(g) or fnmatch.fnmatch(rel_posix, g) for g in patterns)


@dataclass
class SyncRunResult:
    files_scanned: int = 0
    files_uploaded: int = 0
    files_skipped: int = 0
    files_partial: int = 0
    files_retry: int = 0
    ingestion_requests_sent: int = 0
    transmissions: list[dict[str, Any]] = field(default_factory=list)
    transmissions_truncated: bool = False
    errors: list[dict[str, str]] = field(default_factory=list)
    paused_reason: Optional[str] = None
    document_sync: Optional[DocumentSyncSummary] = None
    scan_truncated: bool = False
    subfolder_progress: Optional[dict[str, Any]] = None
    rate_limit: Optional[dict[str, Any]] = None
    #: Knovas document fields of this run (codes and counts); None while
    #: RC_DOC_FIELDS is off.
    doc_fields: Optional["DocFieldsCycle"] = None


#: At most this many unknown keys / suggestions are kept per cycle.
MAX_REPORTED_KEYS = 20


@dataclass
class DocFieldsCycle:
    """What one cycle did with Knovas document fields (spec 3.8).

    Outcome names, refusal and warning codes, failure classes and counts --
    plus the registry or configuration KEYS the server did not know. Never a
    value, a capture, a path or a pointer.
    """

    outcomes: Counter = field(default_factory=Counter)
    refused: Counter = field(default_factory=Counter)
    reupload_failed: Counter = field(default_factory=Counter)
    warnings: Counter = field(default_factory=Counter)
    dropped: Counter = field(default_factory=Counter)
    unknown_keys: list[str] = field(default_factory=list)
    suggest: dict[str, list[str]] = field(default_factory=dict)
    rel_collisions: int = 0
    template_errors: int = 0
    #: ``fields_changed`` re-uploads of this cycle that left the queue.
    reuploads_done: int = 0
    #: Documents re-queued because the server started accepting fields.
    requeued: int = 0

    def note_outcome(self, outcome: str, fields: Optional[FieldsOutcome] = None) -> None:
        if outcome.startswith(REFUSED_PREFIX):
            self.refused[outcome[len(REFUSED_PREFIX):]] += 1
        elif outcome.startswith(REUPLOAD_FAILED_PREFIX):
            self.reupload_failed[outcome[len(REUPLOAD_FAILED_PREFIX):]] += 1
        else:
            self.outcomes[outcome] += 1
        if fields is None:
            return
        self.warnings.update(fields.warning_codes)
        for key in fields.unknown_keys:
            if key not in self.unknown_keys and len(self.unknown_keys) < MAX_REPORTED_KEYS:
                self.unknown_keys.append(key)
        for key, candidates in fields.suggest.items():
            if key not in self.suggest and len(self.suggest) < MAX_REPORTED_KEYS:
                self.suggest[key] = list(candidates)

    @property
    def active(self) -> bool:
        """True when the cycle did anything with fields worth reporting."""
        return bool(
            sum(self.outcomes.values())
            or self.refused
            or self.reupload_failed
            or self.dropped
            or self.rel_collisions
            or self.template_errors
            or self.requeued
        )

    def last_cycle(self) -> dict[str, Any]:
        return {
            "staged": self.outcomes.get(OUTCOME_STAGED, 0),
            "not_accepted": self.outcomes.get(OUTCOME_NOT_ACCEPTED, 0),
            "cleared": self.outcomes.get(OUTCOME_CLEARED, 0),
            "none": self.outcomes.get(OUTCOME_NONE, 0),
            "refused": dict(sorted(self.refused.items())),
            "reupload_failed": dict(sorted(self.reupload_failed.items())),
            "rel_collisions": self.rel_collisions,
            "requeued": self.requeued,
        }

    def as_dict(self) -> dict[str, Any]:
        """The ``doc_fields`` block of the /sync response."""
        return {
            "last_cycle": self.last_cycle(),
            "warnings": dict(sorted(self.warnings.items())),
            "dropped": dict(sorted(self.dropped.items())),
            "template_errors": {"field_template_invalid": self.template_errors},
        }


@dataclass(frozen=True)
class _WalkTarget:
    walk_root: Path
    rel_root: Path
    recursive: bool
    # Knovas RBAC groups for every document from this source. Empty means
    # "unset", which lets the Secure API apply its folder rule instead -- an
    # explicit empty list would mean "deliberately unrestricted" and would
    # override that rule.
    access_groups: tuple[str, ...] = ()
    # The source's Knovas document-fields configuration (and the same
    # access groups), handed to the uploader with each of its files.
    spec: SourceSpec = EMPTY_SOURCE_SPEC


def is_within_max_document_age(
    mtime_iso: str,
    max_age_seconds: int,
    *,
    now: datetime | None = None,
) -> bool:
    """True if file mtime is at most max_age_seconds old (relative to now)."""
    if max_age_seconds < 1:
        return True
    reference = now or datetime.now(timezone.utc)
    mtime = datetime.fromisoformat(mtime_iso.replace("Z", "+00:00"))
    if mtime.tzinfo is None:
        mtime = mtime.replace(tzinfo=timezone.utc)
    age_seconds = (reference - mtime).total_seconds()
    return age_seconds <= max_age_seconds


def _classify_status(
    stored: Optional[tuple[str, int]],
    mtime_iso: str,
    size_bytes: int,
    *,
    max_age_seconds: int | None,
    now: datetime | None,
    stored_digest: Optional[str] = None,
    digest: Optional[str] = None,
) -> DocumentSyncStatus:
    """``digest`` is the governing fields-config digest (None while
    RC_DOC_FIELDS is off: nothing is ever ``fields_changed``). A stored NULL
    digest equals "", so upgrading re-sends only sources with fields."""
    status = status_from_fingerprint(stored, mtime_iso, size_bytes)
    if status == "synced":
        if digest is not None and (stored_digest or "") != digest:
            return "fields_changed"
        return status
    if max_age_seconds is not None and not is_within_max_document_age(
        mtime_iso, max_age_seconds, now=now
    ):
        return "excluded_max_age"
    return status


def _relative_posix(path: Path, rel_root: Path) -> Optional[str]:
    try:
        return path.relative_to(rel_root).as_posix()
    except ValueError:
        return None


@dataclass
class _WalkBudget:
    """Caps directory visits so archives with few/no syncable files cannot walk forever."""

    max_dir_visits: int = 0
    dir_visits: int = 0
    max_files: int = 0
    files_toward_cap: int = 0
    truncated: bool = False
    stopped: bool = False
    resume_stack: list[Path] = field(default_factory=list)

    def consume_dir(self) -> bool:
        """Record one directory visit. Returns False when the budget is exhausted."""
        self.dir_visits += 1
        if self.max_dir_visits > 0 and self.dir_visits > self.max_dir_visits:
            self.truncated = True
            return False
        return True

    def note_file(self, counts_toward_cap: bool) -> None:
        """Count a yielded file toward the per-cycle work cap.

        Already up-to-date / skipped files must not count, otherwise a folder
        whose first N files are all synced would never advance past them.
        """
        if counts_toward_cap:
            self.files_toward_cap += 1

    def file_cap_reached(self) -> bool:
        return self.max_files > 0 and self.files_toward_cap >= self.max_files


def _walk_text_files(
    walk_root: Path,
    rel_root: Path,
    *,
    recursive: bool,
    include: list[str],
    exclude: list[str],
    max_bytes: int,
    should_stop: Callable[[], bool],
    budget: Optional[_WalkBudget] = None,
    initial_stack: list[Path] | None = None,
) -> Iterator[tuple[Path, str, str, int]]:
    """Yield (absolute_path, relative_path, mtime_iso, size_bytes) using os.scandir."""
    stack: list[Path] = list(initial_stack) if initial_stack else [walk_root]
    while stack:
        if should_stop():
            if budget is not None:
                budget.resume_stack = list(stack)
                budget.stopped = True
            return
        # File-yield cap is enforced at directory boundaries so the remaining
        # directories can be checkpointed for the next cycle (mirroring the
        # directory-visit budget). Enforcing mid-directory would abandon the
        # generator without a resume point and re-scan the same prefix forever.
        if budget is not None and budget.file_cap_reached():
            budget.resume_stack = list(stack)
            budget.truncated = True
            return
        current = stack.pop()
        if budget is not None and not budget.consume_dir():
            stack.append(current)
            budget.resume_stack = list(stack)
            return
        if budget is not None and budget.dir_visits % 5000 == 0:
            logger.info("Scan progress: %d directories visited under %s", budget.dir_visits, walk_root.name)
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            continue
        dir_paths: list[Path] = []
        for entry in entries:
            if should_stop():
                if budget is not None:
                    budget.resume_stack = list(stack)
                    budget.stopped = True
                return
            if budget is not None and budget.truncated:
                budget.resume_stack = list(stack)
                return
            try:
                if entry.is_dir(follow_symlinks=False):
                    if recursive:
                        dir_paths.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            path = Path(entry.path)
            if not is_syncable_extension(path.suffix):
                continue
            rel = _relative_posix(path, rel_root)
            if rel is None:
                continue
            if not _matches_globs(rel, include):
                continue
            if exclude and _matches_globs(rel, exclude):
                continue
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.st_size > max_bytes:
                continue
            mtime_iso = (
                datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            yield path, rel, mtime_iso, stat.st_size
        for path in sorted(dir_paths, key=lambda p: p.as_posix()):
            stack.append(path)
    if budget is not None:
        budget.resume_stack = []


def _iter_candidate_files(
    walk_targets: list[_WalkTarget],
    *,
    should_stop: Callable[[], bool],
    filters: dict[str, Any],
    max_scan_entries: int = 0,
    budget: Optional[_WalkBudget] = None,
    initial_stacks: dict[Path, list[Path]] | None = None,
) -> Iterator[UploadItem]:
    """Yield in-scope files with their source's ``SourceSpec``.

    Honours the directory visit budget and the optional file cap. The fifth
    element is the walk target's ``spec`` (access groups and document-fields
    configuration) so the upload queue can hand it to the uploader without a
    second lookup.
    """
    include = filters.get("include_globs") or list(DEFAULT_INCLUDE_GLOBS)
    exclude = filters.get("exclude_globs") or ["**/.git/**"]
    max_bytes = int(filters.get("max_file_bytes", 10_485_760))
    stacks = initial_stacks or {}
    if budget is not None and max_scan_entries > 0 and budget.max_files <= 0:
        budget.max_files = max_scan_entries

    for target in walk_targets:
        if should_stop():
            if budget is not None:
                budget.stopped = True
            break
        if budget is not None and budget.truncated:
            break
        # The file-yield cap now lives inside _walk_text_files (via the budget)
        # so it can checkpoint the remaining directory stack when it trips.
        for item in _walk_text_files(
            target.walk_root,
            target.rel_root,
            recursive=target.recursive,
            include=include,
            exclude=exclude,
            max_bytes=max_bytes,
            should_stop=should_stop,
            budget=budget,
            initial_stack=stacks.get(target.walk_root),
        ):
            yield (*item, target.spec)
        if budget is not None and budget.truncated:
            break


def _iter_m365_candidates(
    source: M365Source,
    sync_body: dict[str, Any],
    *,
    filters: dict[str, Any],
    should_stop: Callable[[], bool],
    budget: _WalkBudget,
    template_errors: Optional[Counter] = None,
) -> Iterator[UploadItem]:
    """The Microsoft 365 twin of ``_iter_candidate_files``.

    Same filters, same relative paths (relative to each source folder, as a
    filesystem walk computes them), same access groups and fields
    configuration -- only the listing comes from the change-tracked
    inventory instead of ``os.scandir``, and the first element is a
    ``RemoteFile`` that is downloaded at upload time.
    """
    include = filters.get("include_globs") or list(DEFAULT_INCLUDE_GLOBS)
    exclude = filters.get("exclude_globs") or ["**/.git/**"]
    max_bytes = int(filters.get("max_file_bytes", 10_485_760))
    files = source.files()
    ordered = sorted(files)
    fields_on = doc_fields_enabled()
    for index, src in enumerate(sync_body.get("sources") or []):
        sub = watch_root_subpath(str(src.get("path") or ""))
        if sub is None:
            logger.warning("Source %r is outside the Microsoft 365 folder; skipped", src.get("path"))
            continue
        spec = source_spec_or_none(src, index, fields_on=fields_on, template_errors=template_errors)
        if spec is None:
            continue
        recursive = bool(src.get("recursive", True))
        prefix = f"{sub}/" if sub else ""
        for full_rel in ordered:
            if should_stop():
                budget.stopped = True
                return
            if prefix and not full_rel.startswith(prefix):
                continue
            rel = full_rel[len(prefix):]
            if not rel or (not recursive and "/" in rel):
                continue
            remote = files[full_rel]
            if not is_syncable_extension(Path(remote.name).suffix):
                continue
            if not _matches_globs(rel, include):
                continue
            if exclude and _matches_globs(rel, exclude):
                continue
            if remote.size > max_bytes:
                continue
            yield remote, rel, remote.modified_iso, remote.size, spec


def _local_file(item: Any):
    """A readable local path for an upload-queue entry.

    Files on a share are read where they are. Microsoft 365 files are fetched
    into a private temp directory for the duration of the upload only.
    """
    if isinstance(item, RemoteFile):
        source = active_m365_source()
        if source is None:
            raise RuntimeError("Microsoft 365 source is no longer configured")
        return source.local_copy(item)
    return nullcontext(item)


#: setup.sh puts this in the empty folder that stands in for the share when the
#: documents are in Microsoft 365 (KNOVAS_DOCUMENTS_URL).
PLACEHOLDER_MARKER = ".knovas-no-local-documents"


def _refuse_placeholder_root() -> None:
    """Never read the Microsoft 365 stand-in folder as if it were a share.

    It is empty by design, so a cycle over it would find nothing and prune
    every document from Knovas. That happens exactly when the Microsoft 365
    settings did not reach this container (a blanked or misspelt key in
    knovas.env), so stop and say that instead.
    """
    from config import get_config

    for root in get_config().rc_watch_roots:
        if (Path(root) / PLACEHOLDER_MARKER).exists():
            raise RuntimeError(
                f"{root} is the stand-in for a OneDrive/SharePoint folder, not a share, "
                "but M365_FOLDER_URL is empty here. Check KNOVAS_DOCUMENTS_URL in "
                "knovas.env, then ./scripts/setup.sh && ./scripts/start.sh. Nothing "
                "was synced or removed."
            )


def _needs_upload(status: DocumentSyncStatus, mode: str) -> bool:
    if mode != "incremental":
        return status != "excluded_max_age"
    return status in ("pending", "modified", "fields_changed")


def _should_skip_failed_upload(upload: UploadResult, mode: str) -> bool:
    """Skip only definitively unconvertible files in incremental mode.

    Everything else - transient OS/SMB errors (locked file, "Permission
    denied", "being used by another process") and Semantix API failures - must
    stay retryable, so we never fingerprint a valid document as synced and drop
    it forever. Only errors the converter itself flags as unrecoverable skip.
    """
    if mode != "incremental" or upload.status != "error":
        return False
    return is_unconvertible_error(upload.error or "")


def _is_server_side_error(error: str) -> bool:
    """Errors from the Secure API, not from extraction: they never count
    toward the extraction retry cap."""
    lowered = error.lower()
    return (
        lowered.startswith("init failed:")
        or (lowered.startswith("part ") and " failed:" in lowered)
        or "rate limit" in lowered
    )


# --- Knovas document fields (spec 3.2, 3.6-3.7) --------------------------------


def source_spec_or_none(
    source: Mapping[str, Any],
    index: int,
    *,
    fields_on: bool,
    template_errors: Optional[Counter],
) -> Optional[SourceSpec]:
    """The ``SourceSpec`` of one sync source, or None to skip the source
    this cycle because a template does not compile (``field_template_invalid``).

    While RC_DOC_FIELDS is off nothing is compiled: the spec carries the
    access groups only and the source syncs exactly as before fields.
    """
    if not fields_on:
        return SourceSpec(access_groups=tuple(source.get("access_groups") or ()))
    try:
        return spec_from_source(source)
    except TemplateError as exc:
        if template_errors is not None:
            # Counted (and logged) once per cycle: the second call that only
            # reads the subfolder progress passes no counter.
            template_errors["field_template_invalid"] += 1
            # The source's index and the error code only: neither the
            # template nor the folder path (both may name a client).
            logger.warning(
                "Source #%d skipped this cycle: field_template_invalid (%s)", index + 1, exc.code
            )
        return None


def fields_upload_kwargs(
    spec: SourceSpec, *, fields_on: bool, previous_fields_sent: bool
) -> dict[str, Any]:
    """Keyword arguments of ``SemantixUploader.upload_file`` for one file.

    ``source`` goes along only when it can change the init body: the source
    has a fields configuration, or values staged earlier must be cleared.
    Every other call is the one made before fields existed.
    """
    kwargs: dict[str, Any] = {"access_groups": spec.access_groups}
    if fields_on and (spec.has_fields or previous_fields_sent):
        kwargs["source"] = spec
        kwargs["previous_fields_sent"] = bool(previous_fields_sent)
    return kwargs


_INIT_STATUS_RE = re.compile(r"^init failed: (\d{3})")


def _reupload_failure_class(error: str) -> str:
    """The closed class of a failed fields re-upload (``reupload_failed:<class>``)."""
    match = _INIT_STATUS_RE.match(error.strip().lower())
    if match:
        status = int(match.group(1))
        if status == 401:
            return "init_401"
        if status == 403:
            return "init_403"
        if 400 <= status < 500:
            return "init_4xx"
        if 500 <= status < 600:
            return "init_5xx"
        return "other"
    if _is_server_side_error(error):
        return "other"
    return "extract"


def _note_fields(
    stats: Optional[DocFieldsCycle], outcome: str, fields: Optional[FieldsOutcome] = None
) -> None:
    doc_fields_metrics.record_outcome(outcome)
    if fields is not None:
        doc_fields_metrics.record_warnings(fields.warning_codes)
    if stats is not None:
        stats.note_outcome(outcome, fields)


def _fields_record_for(upload: UploadResult, digest: str) -> FieldsRecord:
    """What the state DB stores for a successful upload (spec 3.7 table).

    The governing digest is the executor's (the uploader computed the same
    one from the same spec). A transient refusal stores ``REQUEUE_DIGEST``
    rather than keeping the old digest: the old one may equal the governing
    digest (a content change under an unchanged configuration) and the
    document would never come back for its fields.
    """
    outcome = upload.fields or FieldsOutcome(OUTCOME_NONE, digest=digest)
    record = record_for(outcome)
    if record.count_attempt:
        return replace(record, digest=REQUEUE_DIGEST)
    if record.digest is not None:
        return replace(record, digest=digest)
    return record


def _count_reupload_failure(
    state: SyncStateStore,
    relative_path: str,
    digest: str,
    failure_class: str,
    stats: Optional[DocFieldsCycle],
    *,
    attempts: Optional[int] = None,
) -> bool:
    """One more failed attempt of a fields re-upload; at
    ``RC_FIELDS_REUPLOAD_MAX_ATTEMPTS`` the digest is stored with
    ``reupload_failed:<class>`` and the document leaves the queue.
    Returns True when it left."""
    if attempts is None:
        attempts = state.increment_fields_attempts(relative_path)
    if attempts < fields_reupload_max_attempts():
        return False
    record = reupload_failed_record(digest, failure_class)
    state.update_fields(relative_path, record)
    _note_fields(stats, record.outcome)
    logger.warning("doc_fields outcome=%s attempts=%d", record.outcome, attempts)
    return True


def record_upload_outcome(
    state: SyncStateStore,
    relative_path: str,
    mtime_iso: str,
    size_bytes: int,
    upload: UploadResult,
    mode: str,
    *,
    digest: Optional[str] = None,
    fields_reupload: bool = False,
    stats: Optional[DocFieldsCycle] = None,
) -> str:
    """Record one upload's outcome in the sync state; returns the outcome.

    Alloy: ``mechanisms/client_pipeline.als::RcRecordingMechanism`` (model
    ``data_plane/ocr_budget_failsoft.als``), pinned by
    ``tests/unit/test_sync_executor_partial.py``:

    * ``"partial"`` — the library returned, OCR pages were skipped (or no OCR
      backend was available): fingerprint stored so the next cycle does not
      re-upload the file, note kept for ``scripts/backfill_partial_ocr.py``;
    * ``"synced"`` — clean upload;
    * ``"skipped"`` — the library flagged the input unconvertible: parked as
      ``skip:unconvertible`` (incremental mode only);
    * ``"retry"`` — anything else: a transient error, the RC's wall-clock
      kill, a killed child, a Secure API failure. Extraction-side failures
      are counted; past ``MAX_EXTRACT_RETRIES`` the file is recorded partial
      with ``{"reason": "extract_retries_exhausted"}`` so the backfill runs
      an OCR-disabled pass and the text pages land instead of looping.

    Full mode records nothing (as before): it re-uploads everything anyway.

    Knovas document fields (spec 3.7): ``digest`` is the governing config
    digest, None while RC_DOC_FIELDS is off (the fields columns are then
    never touched). Every outcome recorded here also stores a
    ``FieldsRecord`` with that digest -- including ``none`` when nothing was
    sent -- so a document never looks changed again; in full mode only the
    fields columns of existing rows are updated. A ``retry`` records
    nothing, but for a ``fields_changed`` re-upload (``fields_reupload``) it
    counts an attempt, and at the cap the document leaves the queue as
    ``reupload_failed:<class>``.
    """
    incremental = mode == "incremental"
    fields_on = digest is not None
    if upload.status == "ok":
        key = upload.transmission_key_id
        record = _fields_record_for(upload, digest) if fields_on else None
        if upload.partial:
            if incremental:
                state.record_partial(
                    relative_path, mtime_iso, size_bytes, key or "partial", dict(upload.partial),
                    fields=record,
                )
            elif record is not None:
                state.update_fields(relative_path, record)
            ocr_metrics.OCR_PARTIAL.inc()
            outcome = "partial"
        else:
            if incremental and key:
                state.record_upload(relative_path, mtime_iso, size_bytes, key, fields=record)
            elif record is not None and not incremental:
                state.update_fields(relative_path, record)
            outcome = "synced"
        if record is not None:
            if upload.fields is not None:
                _note_fields(stats, upload.fields.outcome, upload.fields)
            left = True
            if record.count_attempt:
                stored = state.fields_state(relative_path)
                left = _count_reupload_failure(
                    state, relative_path, digest, "fields_unavailable", stats,
                    attempts=stored.attempts if stored is not None else 0,
                )
            if fields_reupload and left and stats is not None:
                stats.reuploads_done += 1
        return outcome

    error = upload.error or "upload failed"
    if not incremental:
        return "retry"
    if _should_skip_failed_upload(upload, mode):
        state.record_skip(
            relative_path, mtime_iso, size_bytes, reason="unconvertible",
            fields=FieldsRecord(digest, OUTCOME_NONE) if fields_on else None,
        )
        ocr_metrics.SKIP_UNCONVERTIBLE.inc()
        if fields_reupload and stats is not None:
            stats.reuploads_done += 1
        return "skipped"
    if _is_server_side_error(error):
        if fields_reupload and fields_on:
            if _count_reupload_failure(
                state, relative_path, digest, _reupload_failure_class(error), stats
            ) and stats is not None:
                stats.reuploads_done += 1
        return "retry"
    attempts = state.increment_retry_count(relative_path, error=error)
    ocr_metrics.EXTRACT_RETRIES.inc()
    if attempts > MAX_EXTRACT_RETRIES:
        state.record_partial(
            relative_path,
            mtime_iso,
            size_bytes,
            "partial:extract_retries_exhausted",
            dict(RETRIES_EXHAUSTED_NOTE),
            fields=FieldsRecord(digest, OUTCOME_NONE) if fields_on else None,
        )
        ocr_metrics.OCR_PARTIAL.inc()
        if fields_reupload and stats is not None:
            stats.reuploads_done += 1
        return "partial"
    if fields_reupload and fields_on:
        if _count_reupload_failure(
            state, relative_path, digest, _reupload_failure_class(error), stats
        ) and stats is not None:
            stats.reuploads_done += 1
    return "retry"


@dataclass
class _ScanPlan:
    summary: DocumentSyncSummary
    # (abs_path or RemoteFile, relative_path, mtime_iso, size_bytes, SourceSpec):
    # pending and modified files (everything in full mode).
    upload_queue: list[UploadItem]
    scanned_paths: set[str] = field(default_factory=set)
    scan_truncated: bool = False
    scan_stopped: bool = False
    # ``fields_changed`` re-uploads, at most RC_FIELDS_REUPLOAD_PER_CYCLE and
    # only where ``max_files_per_cycle`` leaves room: uploaded after the
    # primary queue, so they never displace new or modified work.
    fields_queue: list[UploadItem] = field(default_factory=list)
    # Governing fields digest and ``fields_sent`` per queued path (fields on).
    fields_digests: dict[str, str] = field(default_factory=dict)
    fields_sent: dict[str, bool] = field(default_factory=dict)
    rel_collisions: int = 0
    template_errors: Counter = field(default_factory=Counter)

    @property
    def sources_skipped(self) -> int:
        """Sources left out this cycle (a template that does not compile).
        Their files were not scanned: nothing may be pruned, and a
        sequential subfolder may not advance."""
        return int(sum(self.template_errors.values()))


def _pointer_for_relative(identifier_prefix: str, relative_path: str) -> str:
    rel = relative_path.replace("\\", "/")
    return f"{identifier_prefix}/{rel}"


def _delete_on_remove_enabled(sync_body: dict[str, Any]) -> bool:
    ingestion = sync_body.get("ingestion") or {}
    return ingestion.get("delete_on_remove", True) is not False


def _prune_removed_documents(
    sync_body: dict[str, Any],
    uploader: SemantixUploader,
    state: SyncStateStore,
    scanned_paths: set[str],
    result: SyncRunResult,
) -> None:
    prefix = (sync_body.get("ingestion") or {}).get("identifier_prefix", "rc-sync")
    for tracked in state.list_tracked_paths():
        if tracked in scanned_paths:
            continue
        pointer = _pointer_for_relative(prefix, tracked)
        ok, err = uploader.delete_by_pointer(pointer)
        if ok:
            state.remove_tracked(tracked)
            logger.info("Pruned removed document from Knovas: %s", pointer)
        elif err:
            result.errors.append({"path": tracked, "error": err})


def _sequential_subfolders_enabled(sync_config: dict[str, Any] | None) -> bool:
    return bool(sync_config and sync_config.get("sequential_subfolders"))


def build_walk_targets(
    sync_body: dict[str, Any],
    sync_config: dict[str, Any] | None,
    queue: SubfolderQueue | None,
    *,
    template_errors: Optional[Counter] = None,
) -> tuple[list[_WalkTarget], Optional[SubfolderProgress]]:
    """Resolve filesystem walk targets for one scheduler cycle.

    Each target carries its source's ``SourceSpec``. A source whose field
    template does not compile is left out and counted in
    ``template_errors`` (``field_template_invalid``); the cycle goes on.
    """
    sources = sync_body.get("sources") or []
    if not sources:
        return [], None
    fields_on = doc_fields_enabled()

    if not _sequential_subfolders_enabled(sync_config):
        targets: list[_WalkTarget] = []
        for index, source in enumerate(sources):
            root, err = resolve_root(source.get("path"))
            if err or root is None:
                continue
            spec = source_spec_or_none(
                source, index, fields_on=fields_on, template_errors=template_errors
            )
            if spec is None:
                continue
            targets.append(
                _WalkTarget(
                    walk_root=root,
                    rel_root=root,
                    recursive=bool(source.get("recursive", True)),
                    access_groups=spec.access_groups,
                    spec=spec,
                )
            )
        return targets, None

    if len(sources) != 1:
        logger.warning("sequential_subfolders requires exactly one source; using first only")
    source = sources[0]
    root, err = resolve_root(source.get("path"))
    if err or root is None or queue is None:
        return [], None

    progress = queue.progress(root)
    if progress.completed:
        return [], progress

    sub_path = queue.current_path(root)
    if sub_path is None:
        return [], progress

    spec = source_spec_or_none(source, 0, fields_on=fields_on, template_errors=template_errors)
    if spec is None:
        return [], progress
    return [
        _WalkTarget(
            walk_root=sub_path,
            rel_root=root,
            recursive=True,
            access_groups=spec.access_groups,
            spec=spec,
        ),
    ], progress


def plan_sync_cycle(
    sync_body: dict[str, Any],
    state: SyncStateStore,
    *,
    should_stop: Callable[[], bool] = lambda: False,
    include_documents: bool = False,
    sync_config: dict[str, Any] | None = None,
    now: datetime | None = None,
    max_upload_files: int = 0,
    max_scan_entries: int = 0,
    queue: SubfolderQueue | None = None,
    m365_max_age_seconds: float = 0,
) -> _ScanPlan:
    """Single pass over the source: inventory counts + upload queue.

    The source is the watch root's file tree, or -- with ``M365_FOLDER_URL``
    set -- the OneDrive/SharePoint folder's change-tracked inventory, which
    is refreshed first and raises rather than plan from a stale view.

    Knovas document fields (spec 3.2, 3.7): while RC_DOC_FIELDS is on, each
    file's governing config digest is compared with the stored one; a synced
    file whose digest changed is ``fields_changed`` and goes to the bounded
    side queue. When several sources yield the same relative path, the first
    one's fields configuration governs every later duplicate of the cycle
    (each keeps its own access groups: content handling is unchanged), so
    the digests of the copies cannot alternate.
    """
    m365 = active_m365_source()
    if m365 is not None:
        m365.refresh(max_age_seconds=m365_max_age_seconds)
        queue = None
    else:
        _refuse_placeholder_root()
    filters = effective_filters(sync_body, sync_config)
    mode = sync_body.get("mode", "incremental")
    max_age = filters.get("max_document_age_seconds")
    max_age_seconds = int(max_age) if max_age is not None else None
    fingerprints = state.load_fingerprints()
    summary = DocumentSyncSummary()
    upload_queue: list[UploadItem] = []
    fields_queue: list[UploadItem] = []
    scanned_paths: set[str] = set()
    incremental = mode == "incremental"
    fields_on = doc_fields_enabled()
    per_cycle = fields_reupload_per_cycle() if fields_on else 0
    fields_states = state.load_fields_states() if fields_on else {}
    fields_digests: dict[str, str] = {}
    fields_sent: dict[str, bool] = {}
    governing: dict[str, SourceSpec] = {}
    rel_collisions = 0
    template_errors: Counter = Counter()
    walk_targets, _ = (
        build_walk_targets(sync_body, sync_config, queue, template_errors=template_errors)
        if m365 is None
        else ([], None)
    )
    visit_cap = max_scan_entries if max_scan_entries > 0 else 0
    budget = (
        _WalkBudget(max_dir_visits=visit_cap, max_files=max_scan_entries)
        if visit_cap
        else _WalkBudget()
    )

    initial_stacks: dict[Path, list[Path]] = {}
    if queue is not None and walk_targets:
        sources = sync_body.get("sources") or []
        if sources:
            source_root, _ = resolve_root(sources[0].get("path"))
            if source_root is not None:
                saved = queue.load_scan_stack(source_root)
                if saved:
                    initial_stacks[walk_targets[0].walk_root] = saved
                    logger.info(
                        "Resuming subfolder scan for %s (%d dirs queued)",
                        walk_targets[0].walk_root.name,
                        len(saved),
                    )

    if m365 is not None:
        candidates = _iter_m365_candidates(
            m365, sync_body, filters=filters, should_stop=should_stop, budget=budget,
            template_errors=template_errors,
        )
    else:
        candidates = _iter_candidate_files(
            walk_targets,
            should_stop=should_stop,
            filters=filters,
            max_scan_entries=max_scan_entries,
            budget=budget,
            initial_stacks=initial_stacks,
        )
    identifier_prefix = (sync_body.get("ingestion") or {}).get("identifier_prefix", "rc-sync")
    links: list[dict[str, str]] = []

    scanned = 0
    for abs_path, rel, mtime_iso, size_bytes, spec in candidates:
        scanned += 1
        scanned_paths.add(rel)
        digest: Optional[str] = None
        if fields_on:
            first = governing.get(rel)
            if first is None:
                governing[rel] = spec
            else:
                rel_collisions += 1
                spec = replace(first, access_groups=spec.access_groups)
            digest = config_digest(rel, spec)
        if isinstance(abs_path, RemoteFile):
            links.append(
                {
                    "doc_id": _pointer_for_relative(identifier_prefix, rel),
                    "web_url": abs_path.web_url,
                    "title": abs_path.name,
                    "modified_at": abs_path.modified_iso,
                    "item_id": abs_path.item_id,
                    "drive_id": abs_path.drive_id,
                }
            )
        stored = state.lookup_stored(rel, fingerprints)
        fields_state = fields_states.get(rel)
        status = _classify_status(
            stored,
            mtime_iso,
            size_bytes,
            max_age_seconds=max_age_seconds,
            now=now,
            stored_digest=fields_state.digest if fields_state is not None else None,
            digest=digest,
        )
        summary.total += 1
        if status == "synced":
            summary.synced += 1
        elif status == "pending":
            summary.pending += 1
        elif status == "modified":
            summary.modified += 1
        elif status == "fields_changed":
            summary.fields_changed += 1
        else:
            summary.excluded_max_age += 1
        # Only files that still need work count toward the per-cycle file cap,
        # so a folder whose leading files are already synced keeps advancing.
        # A fields re-upload is no such work: it never truncates a scan.
        budget.note_file(
            _needs_upload(status, mode) and not (incremental and status == "fields_changed")
        )
        if include_documents:
            summary.documents.append(
                DocumentSyncRecord(
                    relative_path=rel,
                    mtime_iso=mtime_iso,
                    size_bytes=size_bytes,
                    status=status,
                )
            )
        if _needs_upload(status, mode):
            item: UploadItem = (abs_path, rel, mtime_iso, size_bytes, spec)
            queued = False
            if incremental and status == "fields_changed":
                if len(fields_queue) < per_cycle:
                    fields_queue.append(item)
                    queued = True
            elif max_upload_files <= 0 or len(upload_queue) < max_upload_files:
                upload_queue.append(item)
                queued = True
            if queued and digest is not None:
                fields_digests[rel] = digest
                fields_sent[rel] = bool(fields_state is not None and fields_state.sent)

    if max_upload_files > 0:
        # Re-uploads only take what the cycle's file cap leaves after new
        # and modified work.
        del fields_queue[max(0, max_upload_files - len(upload_queue)):]

    if m365 is not None and not budget.stopped and not template_errors:
        # Only a complete pass describes the folder; a partial one would drop
        # the open/preview links of every document it did not reach (also
        # those of a source skipped for a field template that does not compile).
        m365.write_links(links)

    scan_truncated = budget.truncated
    # A stop/deadline interrupt is NOT the same as a completed scan: the tail is
    # unscanned, so the checkpoint must be preserved (not cleared) exactly like a
    # truncation, otherwise the cursor would advance past unscanned documents.
    scan_stopped = budget.stopped

    if queue is not None and walk_targets:
        sources = sync_body.get("sources") or []
        if sources:
            source_root, _ = resolve_root(sources[0].get("path"))
            if source_root is not None:
                if scan_truncated or scan_stopped:
                    queue.save_scan_stack(source_root, budget.resume_stack or [walk_targets[0].walk_root])
                else:
                    queue.clear_scan_stack(source_root)

    return _ScanPlan(
        summary=summary,
        upload_queue=upload_queue,
        scanned_paths=scanned_paths,
        scan_truncated=scan_truncated,
        scan_stopped=scan_stopped,
        fields_queue=fields_queue,
        fields_digests=fields_digests,
        fields_sent=fields_sent,
        rel_collisions=rel_collisions,
        template_errors=template_errors,
    )


def scan_document_inventory(
    sync_body: dict[str, Any],
    *,
    should_stop: Callable[[], bool] = lambda: False,
    include_documents: bool = False,
    sync_config: dict[str, Any] | None = None,
    now: datetime | None = None,
    max_scan_entries: int = 0,
) -> DocumentSyncSummary:
    """Scan in-scope documents and classify sync status against local state."""
    state = SyncStateStore()
    queue: SubfolderQueue | None = None
    try:
        if _sequential_subfolders_enabled(sync_config):
            queue = SubfolderQueue.from_config()
        return plan_sync_cycle(
            sync_body,
            state,
            should_stop=should_stop,
            include_documents=include_documents,
            sync_config=sync_config,
            now=now,
            max_scan_entries=max_scan_entries,
            queue=queue,
            m365_max_age_seconds=60,
        ).summary
    finally:
        if queue is not None:
            queue.close()
        state.close()


def _collect_files(
    sync_body: dict[str, Any],
    *,
    should_stop: Callable[[], bool],
    sync_config: dict[str, Any] | None = None,
    now: datetime | None = None,
    max_upload_files: int = 0,
) -> list[UploadItem]:
    """Return files that need upload (pending or modified, then the bounded
    fields re-uploads; all in-scope in full mode)."""
    state = SyncStateStore()
    try:
        plan = plan_sync_cycle(
            sync_body,
            state,
            should_stop=should_stop,
            sync_config=sync_config,
            now=now,
            max_upload_files=max_upload_files,
        )
        return plan.upload_queue + plan.fields_queue
    finally:
        state.close()


def _max_files_per_cycle(sync_config: dict[str, Any] | None) -> int:
    if not sync_config:
        return 0
    raw = sync_config.get("max_files_per_cycle")
    if raw is None:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _max_scan_entries_per_cycle(sync_config: dict[str, Any] | None) -> int:
    if not sync_config:
        return 10000
    raw = sync_config.get("max_scan_entries_per_cycle")
    if raw is None:
        return 10000 if sync_config.get("sequential_subfolders") else 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _default_max_sync_duration_minutes(sync_config: dict[str, Any] | None) -> Optional[int]:
    if not sync_config:
        return None
    raw = sync_config.get("max_sync_duration_minutes")
    if raw is not None:
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            pass
    if sync_config.get("sequential_subfolders"):
        return 120
    return None


def run_sync_work(
    sync_body: dict[str, Any],
    uploader: SemantixUploader,
    *,
    should_stop: Callable[[], bool] = lambda: False,
    is_in_sync_window: Callable[[], bool] = lambda: True,
    sync_config: dict[str, Any] | None = None,
    now: datetime | None = None,
    max_transmissions_in_response: int = 100,
) -> SyncRunResult:
    result = SyncRunResult()
    state = SyncStateStore()
    queue: SubfolderQueue | None = None
    # The subfolder queue bounds a filesystem walk of a huge share. The
    # Microsoft 365 inventory is already an in-memory listing, so it has
    # nothing to bound and ignores the setting.
    sequential = _sequential_subfolders_enabled(sync_config) and not m365_configured()
    source_root: Path | None = None

    try:
        ensure_mtls_certificate_freshness()
        if sequential:
            queue = SubfolderQueue.from_config()
            sources = sync_body.get("sources") or []
            if sources:
                source_root, _ = resolve_root(sources[0].get("path"))

        max_scan = _max_scan_entries_per_cycle(sync_config)
        plan = plan_sync_cycle(
            sync_body,
            state,
            should_stop=should_stop,
            include_documents=False,
            sync_config=sync_config,
            now=now,
            max_upload_files=_max_files_per_cycle(sync_config),
            max_scan_entries=max_scan,
            queue=queue,
        )
        _, progress = build_walk_targets(sync_body, sync_config, queue)
        if progress is not None:
            result.subfolder_progress = progress.as_dict()

        result.document_sync = plan.summary
        result.files_scanned = plan.summary.total
        result.files_skipped = plan.summary.synced
        result.scan_truncated = plan.scan_truncated
        if plan.scan_truncated:
            result.paused_reason = "scan_limit_reached"
        elif plan.scan_stopped:
            # Scan interrupted by should_stop (e.g. max_sync_duration). Block the
            # cursor from advancing over the unscanned tail of this subfolder.
            result.paused_reason = "cycle_time_limit"

        mode = sync_body.get("mode", "incremental")
        fields_on = doc_fields_enabled()
        stats = DocFieldsCycle() if fields_on else None
        if stats is not None:
            stats.rel_collisions = plan.rel_collisions
            stats.template_errors = plan.sources_skipped
            result.doc_fields = stats
        if plan.sources_skipped:
            result.errors.append({
                "path": "",
                "error": f"field_template_invalid: {plan.sources_skipped} source(s) skipped this cycle",
            })
        order = upload_order()
        work = [(item, False) for item in _ordered_upload_queue(plan.upload_queue, order)]
        work += [(item, True) for item in _ordered_upload_queue(plan.fields_queue, order)]
        requeue_checked = False
        for (abs_path, rel, mtime_iso, size_bytes, spec), fields_reupload in work:
            if should_stop():
                result.paused_reason = "stop_requested"
                break
            if not is_in_sync_window():
                result.paused_reason = "outside_window"
                break

            digest: Optional[str] = None
            if fields_on:
                digest = plan.fields_digests.get(rel)
                if digest is None:
                    digest = config_digest(rel, spec)
            upload_kwargs = fields_upload_kwargs(
                spec, fields_on=fields_on, previous_fields_sent=plan.fields_sent.get(rel, False)
            )
            try:
                with _local_file(abs_path) as local_path:
                    upload = uploader.upload_file(local_path, rel, sync_body, **upload_kwargs)
            except requests.RequestException as exc:
                if "rate limit" in str(exc).lower():
                    result.paused_reason = "rate_limited"
                    break
                raise
            except Exception as exc:
                logger.warning(
                    "Upload raised path=%s error=%s",
                    rel,
                    exc,
                    exc_info=True,
                )
                upload = UploadResult(
                    relative_path=rel,
                    transmission_key_id=None,
                    parts=0,
                    status="error",
                    ingestion_requests=0,
                    error=str(exc),
                )
            result.ingestion_requests_sent += upload.ingestion_requests
            if stats is not None and upload.fields_dropped:
                stats.dropped.update(upload.fields_dropped)

            outcome = record_upload_outcome(
                state, rel, mtime_iso, size_bytes, upload, mode,
                digest=digest, fields_reupload=fields_reupload, stats=stats,
            )
            if (
                stats is not None
                and not requeue_checked
                and upload.fields is not None
                and upload.fields.outcome in (OUTCOME_STAGED, OUTCOME_CLEARED)
            ):
                # The server takes fields now: documents it ignored them for
                # come back within the per-cycle bound (spec 2.3). Once per
                # cycle, so an inconsistent server cannot loop a document.
                requeue_checked = True
                if state.count_fields_requeue_candidates(OUTCOME_NOT_ACCEPTED):
                    stats.requeued += state.requeue_fields(OUTCOME_NOT_ACCEPTED)
                    logger.info("doc_fields requeued=%d outcome=not_accepted", stats.requeued)
            tx_entry: dict[str, Any]
            if upload.status == "ok":
                result.files_uploaded += 1
                tx_entry = {
                    "path": rel,
                    "transmission_key_id": upload.transmission_key_id,
                    "parts": upload.parts,
                    "status": "ok",
                }
                if upload.fields is not None and upload.fields.fields_sent:
                    tx_entry["fields"] = upload.fields.as_tx_entry()
                if outcome == "partial":
                    result.files_partial += 1
                    tx_entry["partial"] = upload.partial
                    logger.info("Recorded partial document for the backfill: %s %s", rel, upload.partial)
            else:
                err = upload.error or "upload failed"
                logger.warning("Upload failed path=%s error=%s", rel, err)
                if outcome == "skipped":
                    logger.info("Marked unconvertible path as skipped: %s", rel)
                elif outcome == "partial":
                    result.files_partial += 1
                    logger.warning(
                        "Extraction retries exhausted (%d); recorded partial for the "
                        "OCR-disabled backfill pass: %s",
                        MAX_EXTRACT_RETRIES,
                        rel,
                    )
                else:
                    result.files_retry += 1
                result.errors.append({"path": rel, "error": err})
                tx_entry = {"path": rel, "status": "error", "parts": upload.parts}

            if max_transmissions_in_response > 0 and len(result.transmissions) >= max_transmissions_in_response:
                result.transmissions_truncated = True
            else:
                result.transmissions.append(tx_entry)

        can_prune = (
            _delete_on_remove_enabled(sync_body)
            and not plan.scan_truncated
            and not plan.scan_stopped
            and not result.paused_reason
            and not sequential
            # A source skipped for a bad field template was not scanned: its
            # documents are still there and must not be removed from Knovas.
            and not plan.sources_skipped
        )
        if can_prune and not plan.scanned_paths and state.count_tracked_paths() > 0:
            # Every source came back empty while documents are tracked: a share
            # that is not mounted, a folder emptied by mistake, a source that
            # points nowhere. Pruning would remove the whole index in one cycle.
            # Leave it, and say so; a genuine emptying is one deliberate
            # "delete_all_documents" away.
            logger.warning(
                "Sources are empty but %d document(s) are tracked; NOT removing them "
                "from Knovas. Check the document source.",
                state.count_tracked_paths(),
            )
            result.errors.append({
                "path": "",
                "error": "sources empty while documents are tracked; nothing removed",
            })
        elif can_prune:
            _prune_removed_documents(sync_body, uploader, state, plan.scanned_paths, result)

        if (
            sequential
            and queue is not None
            and source_root is not None
            and result.document_sync is not None
            and not plan.sources_skipped
        ):
            ds = result.document_sync
            # A single maybe_advance handles empty/fully-synced folders too:
            # it already advances one step when pending==modified==0 and the
            # scan was neither truncated nor paused. A second call here would
            # advance again and skip the next subfolder entirely (data loss).
            # Fields re-uploads count as modified: a subfolder completes only
            # once they are done. A source skipped for a bad template was
            # not scanned at all and never advances.
            queue.maybe_advance(
                source_root,
                pending=ds.pending,
                modified=ds.modified + ds.fields_changed,
                scan_truncated=result.scan_truncated,
                paused_reason=result.paused_reason,
            )
            updated = queue.progress(source_root)
            result.subfolder_progress = updated.as_dict()
    finally:
        if queue is not None:
            queue.close()
        state.close()

    metrics = getattr(uploader, "_rate_metrics", None)
    if isinstance(metrics, IngestRateMetrics):
        result.rate_limit = metrics.as_dict()

    return result
