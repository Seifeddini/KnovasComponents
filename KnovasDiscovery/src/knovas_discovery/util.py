"""Small helpers shared by the probes and the report: JSON output, probe results, error codes."""

from __future__ import annotations

import json
import re
import socket
import ssl
from pathlib import Path
from typing import Any

import yaml

STATUSES = ("ok", "partial", "failed", "skipped")


class _YamlLoader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 booleans: only true/false. YAML 1.1 reads the catalogue's key `on:` (and on, off,
    yes, no as values) as a boolean."""


_YamlLoader.yaml_implicit_resolvers = {
    first: [r for r in resolvers if r[0] != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_YamlLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
                                  list("tTfF"))


def load_yaml(text: str) -> Any:
    return yaml.load(text, Loader=_YamlLoader)  # noqa: S506 (a SafeLoader subclass)


def write_json(path: Path, data: Any) -> None:
    """Write JSON the same way every time: sorted keys, two-space indent, LF line ends, UTF-8."""
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    Path(path).write_text(text, encoding="utf-8", newline="\n")


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_text(path: Path, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8", newline="\n")


def result(key: str, status: str, error: str | None = None, facts: dict | None = None) -> dict:
    """A probe result as findings.json holds it."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    return {"key": key, "status": status, "error": error, "facts": facts if facts is not None else {}}


class ProbeError(Exception):
    """A probe failure with a code that is safe to share (no secret, no name)."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def error_code(exc: BaseException) -> str:
    """A code for an exception that names neither a secret nor a person, file or folder.

    The exception's text is never used here: drivers put server messages, account names and paths into it.
    """
    if isinstance(exc, ProbeError):
        return exc.code
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, FileNotFoundError):
        return "not_found"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(exc, socket.gaierror):
        return "name_not_resolved"
    if isinstance(exc, ssl.SSLError):
        return "tls_error"
    if isinstance(exc, OSError):
        return f"os_error:{exc.errno}" if exc.errno is not None else "os_error"
    return "error:" + type(exc).__name__
