"""One spelling for a document pointer, so a truth file and an instance agree.

The truth files name a document by its path on the share with forward slashes. An instance may hand the same
document back with backslashes, with a leading slash, or under a prefix of its own (the mount or the source name).
"""
from __future__ import annotations

import unicodedata


def _slashes(text: object) -> str:
    return unicodedata.normalize("NFC", str(text or "")).replace("\\", "/").lstrip("/")


def normalise(pointer: object, prefix: str = "") -> str:
    """``pointer`` as the truth files spell it: forward slashes, no leading slash, without ``prefix``.

    Case is kept (the Platform's own document grants do not fold it either).
    """
    text = _slashes(pointer)
    lead = _slashes(prefix)
    if lead and text.startswith(lead):
        text = text[len(lead):]
    return text.lstrip("/")
