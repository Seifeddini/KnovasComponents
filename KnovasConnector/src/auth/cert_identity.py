"""Certificate serial normalization (matches KnowledgeBase certificate_identity)."""
from __future__ import annotations

from typing import Any


def normalize_certificate_serial_to_str(serial: Any) -> str:
    if serial is None:
        return ""
    if isinstance(serial, int):
        return str(serial)
    s = str(serial).strip()
    return s if s else ""
