"""What a log line may say about a document: a hash of its pointer, never the pointer.

No customer text in the logs (founder's rule, 2026-11-19): no question, no
document text, no response body, no exception message, no document name, path
or pointer, no person's name or e-mail address. A log line names a document by
``pointer_hash(pointer)`` when a line has to be correlated with others.
"""
from __future__ import annotations

import hashlib
from typing import Any


def pointer_hash(pointer: Any) -> str:
    """Stable 16-hex id for a document pointer (or a path), for log lines.

    The first 16 characters of the sha256 that also names the pointer's
    context sidecar (``context_store.sidecar_path_for_pointer``), so a logged
    hash finds its sidecar, and hashing a known pointer finds its log lines.
    """
    return hashlib.sha256(str(pointer or "").encode("utf-8")).hexdigest()[:16]
