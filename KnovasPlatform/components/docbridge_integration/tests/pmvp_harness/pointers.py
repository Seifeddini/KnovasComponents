"""One spelling for a document pointer, so a truth file and an instance agree.

The truth files name a document by its path on the share. An instance may hand the same document back with
backslashes, a doubled or trailing slash, a leading ``./`` or ``/``, white space around it, another Unicode form,
another case (file shares do not tell case apart), or under a prefix of its own (the mount or the source name).
A leak must be counted however the pointer is spelled, so every comparison in the harness goes through
``normalise``: the questions' supporting pointers, the rights table and the passages of a reply.
"""
from __future__ import annotations

import unicodedata
from typing import List


def _folders(text: object) -> List[str]:
    """The path's parts, in the form they are compared in."""
    cleaned = unicodedata.normalize("NFC", str(text or "")).strip().replace("\\", "/")
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    return [unicodedata.normalize("NFC", part.casefold()) for part in parts]


def normalise(pointer: object, prefix: str = "") -> str:
    """The key ``pointer`` is compared by; ``""`` for no pointer.

    ``prefix`` is what the instance puts before the truth files' pointers. It is cut off only where it ends at a
    folder: the prefix ``alt`` does not match ``altMandate/...``.

    The key is not for showing: it is in lower case. What a person reads is the spelling of the truth files.
    """
    parts = _folders(pointer)
    lead = _folders(prefix)
    if lead and parts[:len(lead)] == lead:
        parts = parts[len(lead):]
    return "/".join(parts)
