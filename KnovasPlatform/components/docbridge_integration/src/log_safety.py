"""No customer text in the logs (founder's rule, 2026-11-19).

No question, no document text, no response body, no exception message, no
document name, path or pointer, no person's name or e-mail address. What this
module gives the rest of the Platform for that:

``pointer_hash(pointer)``
    How a log line names a document when it has to be correlated with others:
    an HMAC of the pointer under a key derived from the installation's
    ``WEB_SECRET_KEY``. Without the key a hash cannot be matched against a
    list of guessed names (that a mandate exists is itself confidential).
    Someone on the installation finds a document's lines with
    ``python -m log_safety <pointer>`` (or pointers on stdin with ``-``).

``guard_logging()``
    The log set-up's filter: below WARNING only records of the Platform's own
    modules reach a handler, whatever LOG_LEVEL says, because a library's
    DEBUG line can carry a URL with its query string (urllib3 logs every
    request line). urllib3 is held at ERROR: its retry and pool warnings name
    the URL too.

``install_excepthooks()``
    An exception nobody caught, in a thread or in the main program, is logged
    by its type and the thread's name, not printed as a traceback whose last
    line quotes the message.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sys
import threading
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

#: The installation's secret the hash key is derived from. The installer
#: generates it once (scripts/lib/expand_knovas_env.sh) and the app refuses to
#: start with a weak one; docbridge-web, its workers, its extraction children
#: and the commands run in its container all see it.
SECRET_ENV = "WEB_SECRET_KEY"
_KEY_LABEL = b"knovas platform: log pointer hash v1"
#: What pointer_hash says when no key is set (a test, a bare checkout): the
#: line still says "a document" and never falls back to an unkeyed hash.
NO_KEY = "nokey"

_key: Optional[bytes] = None

#: The Platform's own top-level modules: everything directly under src/.
#: ``__main__`` is web_interface.app run as ``python -m``.
PLATFORM_MODULES = frozenset({
    "__main__", "brain_sync", "builder", "config_loader", "context_store", "custom_views",
    "doc_fields_capability", "doc_fields_view", "document_grants", "experiments", "file_utils",
    "german_text", "graph_model", "graph_views", "identity", "knovas_client",
    "knovas_connector_client", "knovas_extract_upload", "knovas_transmit", "log_safety",
    "ontology_filters", "ontology_graph", "ontology_graph_filters", "ontology_store",
    "ontology_text", "open_tokens", "part_metadata", "platform_settings", "unc_path",
    "web_interface",
})


def _derive(secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), _KEY_LABEL, hashlib.sha256).digest()


def use_secret(secret: Optional[str]) -> None:
    """Key pointer_hash with this secret (create_app passes web.secret_key)."""
    global _key
    secret = str(secret or "").strip()
    _key = _derive(secret) if secret else None


def _current_key() -> Optional[bytes]:
    if _key is not None:
        return _key
    secret = os.environ.get(SECRET_ENV, "").strip()
    return _derive(secret) if secret else None


def pointer_hash(pointer: Any) -> str:
    """16 hex characters naming a document pointer (or a path) in a log line."""
    key = _current_key()
    if key is None:
        return NO_KEY
    return hmac.new(key, str(pointer or "").encode("utf-8"), hashlib.sha256).hexdigest()[:16]


class PlatformRecordsOnly(logging.Filter):
    """Below WARNING, only the Platform's own records pass."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING or record.name.split(".", 1)[0] in PLATFORM_MODULES


def guard_logging(handlers: Optional[Iterable[logging.Handler]] = None) -> None:
    """Filter the root handlers (or these) and hold urllib3 at ERROR."""
    for handler in (logging.getLogger().handlers if handlers is None else handlers):
        if not any(isinstance(f, PlatformRecordsOnly) for f in handler.filters):
            handler.addFilter(PlatformRecordsOnly())
    logging.getLogger("urllib3").setLevel(logging.ERROR)


def _log_uncaught(exc_type, exc, tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    logger.error("Uncaught %s in the main thread", exc_type.__name__)


def _log_uncaught_in_thread(args) -> None:
    if issubclass(args.exc_type, SystemExit):
        return  # what threading's own hook does
    name = args.thread.name if args.thread is not None else "-"
    logger.error("Uncaught %s in thread %s", args.exc_type.__name__, name)


def install_excepthooks() -> None:
    sys.excepthook = _log_uncaught
    threading.excepthook = _log_uncaught_in_thread


def main(argv: Optional[list] = None) -> int:
    """``python -m log_safety <pointer>...`` (or ``-`` and pointers on stdin, one per line):
    the hash each pointer has in this installation's log lines."""
    args = list(sys.argv[1:] if argv is None else argv)
    if _current_key() is None:
        print(f"{SECRET_ENV} is not set: run this where the Platform runs.", file=sys.stderr)
        return 2
    if not args:
        print("usage: python -m log_safety <pointer>... | -", file=sys.stderr)
        return 2
    pointers = [line.rstrip("\r\n") for line in sys.stdin] if args == ["-"] else args
    for pointer in pointers:
        print(pointer_hash(pointer))
    return 0


if __name__ == "__main__":
    sys.exit(main())
