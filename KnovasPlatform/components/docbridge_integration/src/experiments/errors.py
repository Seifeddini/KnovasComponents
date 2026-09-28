"""Errors the experiments service raises.

Every message is German and written for the person using the UI; the web
layer returns it verbatim, so no message may contain internal detail (paths,
SQL, exception text).
"""

from __future__ import annotations

from typing import Dict, Optional


class ExperimentsError(Exception):
    """Base class. ``status`` is the HTTP status the web layer answers with."""

    status = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFound(ExperimentsError):
    status = 404


class Forbidden(ExperimentsError):
    status = 403


class Conflict(ExperimentsError):
    """The row changed since it was read, or a unique value is taken."""

    status = 409


class ValidationError(ExperimentsError):
    """Input was refused. ``fields`` maps a field name to its own message."""

    status = 400

    def __init__(self, message: str, fields: Optional[Dict[str, str]] = None) -> None:
        super().__init__(message)
        self.fields = dict(fields or {})


class Unavailable(ExperimentsError):
    """A dependency (Knovas, the evaluation runner) is not reachable."""

    status = 503
