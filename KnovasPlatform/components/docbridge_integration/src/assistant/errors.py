"""What the Core's answer means for the assistant: busy or failed.

Both carry safe facts only -- an HTTP status and a short reason code -- never
a response body, a question or an answer, so ``str(exc)`` may appear in a log
line without leaking customer text (POST-04). Callers still log
``type(exc).__name__`` and never ``str(exc)``.
"""

from __future__ import annotations

from typing import Optional


class CoreError(Exception):
    """A call to the Knovas Core (search or generation) did not give an answer."""

    default_reason = "core_error"

    def __init__(self, reason: Optional[str] = None, *, status: Optional[int] = None) -> None:
        self.reason = reason or self.default_reason
        self.status = status
        text = self.reason if status is None else f"{self.reason} (HTTP {status})"
        super().__init__(text)


class CoreBusy(CoreError):
    """The Core is busy (HTTP 429 or 503): asking again a little later may work."""

    default_reason = "core_busy"


class CoreFailed(CoreError):
    """Any other failure: a timeout, another HTTP error, a malformed answer."""

    default_reason = "core_failed"
