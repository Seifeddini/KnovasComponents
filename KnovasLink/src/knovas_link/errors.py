"""Refusals of the local API: an HTTP status and a code, answered as ``{"error": code, "message": …}``.

The message is English and never holds a name or a value (local-api.md, "Endpoints")."""
from __future__ import annotations


class LocalError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message or code


class StartupError(RuntimeError):
    """Link cannot start; the text is a code, never a value."""
