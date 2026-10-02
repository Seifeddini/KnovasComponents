"""Prometheus counters for Knovas document fields on uploads (spec 3.8).

``/metrics`` is unauthenticated (docs/operations.md), so every label value
comes from a closed set and anything else is counted as ``other``: no field
key, value, capture, path or pointer can become a label. Counts only.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Iterable

from sync.doc_fields_payload import (
    DROP_REASONS,
    FIELD_REFUSAL_CODES,
    OUTCOME_CLEARED,
    OUTCOME_NONE,
    OUTCOME_NOT_ACCEPTED,
    OUTCOME_STAGED,
    REFUSED_PREFIX,
    REUPLOAD_FAILED_PREFIX,
)

logger = logging.getLogger(__name__)

OTHER = "other"

#: ``rc_doc_fields_uploads_total{outcome}``: the outcome family; the code of
#: a refusal goes to ``rc_doc_fields_refusals_total{code}``.
UPLOAD_OUTCOMES = frozenset(
    {OUTCOME_STAGED, OUTCOME_CLEARED, OUTCOME_NOT_ACCEPTED, OUTCOME_NONE, "refused", "reupload_failed", OTHER}
)
#: ``rc_doc_fields_refusals_total{code}``.
REFUSAL_CODES = frozenset(FIELD_REFUSAL_CODES | {OTHER})
#: ``rc_doc_fields_warnings_total{code}``: the init warning codes the server
#: documents (contract digest, sections 2 and 7).
WARNING_CODES = frozenset(
    {
        "invalid_value",
        "checksum_failed",
        "type_mismatch",
        "restricted_identifier",
        "cap_exceeded",
        "ambiguous_date",
        "unresolved_entity",
        "ambiguous_entity",
        "key_looks_personal",
        OTHER,
    }
)
#: ``rc_doc_fields_client_dropped_total{reason}``.
DROP_REASON_LABELS = frozenset(DROP_REASONS | {OTHER})


class _NoopCounter:
    def labels(self, *args: Any, **kwargs: Any) -> "_NoopCounter":  # noqa: ARG002
        return self

    def inc(self, amount: float = 1) -> None:  # noqa: ARG002 - mirrors prometheus_client
        return None


def _counter(name: str, documentation: str, label: str):
    try:
        from prometheus_client import Counter

        return Counter(name, documentation, [label])
    except ImportError:
        return _NoopCounter()
    except ValueError:
        # Already registered (module re-imported under a test runner).
        from prometheus_client import REGISTRY

        existing = getattr(REGISTRY, "_names_to_collectors", {}).get(name)
        return existing if existing is not None else _NoopCounter()


UPLOADS = _counter(
    "rc_doc_fields_uploads_total",
    "Uploads of documents whose source carries Knovas document fields, by outcome "
    "(reupload_failed: a document that left the fields re-upload queue)",
    "outcome",
)
REFUSALS = _counter(
    "rc_doc_fields_refusals_total",
    "Inits whose fields the server refused (the document was indexed without them), by code",
    "code",
)
WARNINGS = _counter(
    "rc_doc_fields_warnings_total",
    "Warnings in the server's init fields echo, by code",
    "code",
)
CLIENT_DROPPED = _counter(
    "rc_doc_fields_client_dropped_total",
    "Field values the RemoteController left out of an init before sending, by reason",
    "reason",
)


def closed(value: Any, allowed: frozenset) -> str:
    """``value`` when it is a member of ``allowed``, else ``other``."""
    return value if isinstance(value, str) and value in allowed else OTHER


def outcome_family(outcome: str) -> str:
    """``refused:<code>`` -> ``refused``; ``reupload_failed:<class>`` ->
    ``reupload_failed``; anything unknown -> ``other``."""
    if isinstance(outcome, str):
        if outcome.startswith(REFUSED_PREFIX):
            return "refused"
        if outcome.startswith(REUPLOAD_FAILED_PREFIX):
            return "reupload_failed"
    return closed(outcome, UPLOAD_OUTCOMES)


def _inc(counter: Any, label: str, amount: float = 1) -> None:
    try:
        counter.labels(label).inc(amount)
    except Exception as exc:  # noqa: BLE001 - a metric never fails an upload
        logger.debug("doc_fields metric not counted: %s", type(exc).__name__)


def record_outcome(outcome: str) -> None:
    family = outcome_family(outcome)
    _inc(UPLOADS, family)
    if family == "refused":
        _inc(REFUSALS, closed(outcome[len(REFUSED_PREFIX):], REFUSAL_CODES))


def record_warnings(codes: Mapping[str, int]) -> None:
    for code, count in (codes or {}).items():
        if count:
            _inc(WARNINGS, closed(code, WARNING_CODES), count)


def record_dropped(reasons: Mapping[str, int]) -> None:
    for reason, count in (reasons or {}).items():
        if count:
            _inc(CLIENT_DROPPED, closed(reason, DROP_REASON_LABELS), count)


def label_sets() -> dict[str, Iterable[str]]:
    """Every label value each counter may carry (tests pin the closure)."""
    return {
        "rc_doc_fields_uploads_total": UPLOAD_OUTCOMES,
        "rc_doc_fields_refusals_total": REFUSAL_CODES,
        "rc_doc_fields_warnings_total": WARNING_CODES,
        "rc_doc_fields_client_dropped_total": DROP_REASON_LABELS,
    }
