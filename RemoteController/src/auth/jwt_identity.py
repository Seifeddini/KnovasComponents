"""Extract operator identity from employee JWT (signature verified by Knovas)."""
from __future__ import annotations

import base64
import json
import re
import uuid
from typing import Any, Optional

_EMPLOYEE_ID_CLAIMS = ("employee_id", "operator_id", "sub", "oid", "user_id")
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _normalize_uuid(value: str) -> Optional[str]:
    value = str(value).strip()
    if not _UUID.match(value):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def decode_jwt_payload_unverified(jwt_token: str) -> Optional[dict[str, Any]]:
    parts = jwt_token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + padding))
    except (ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def employee_id_from_jwt_payload(payload: dict[str, Any]) -> Optional[str]:
    for key in _EMPLOYEE_ID_CLAIMS:
        raw = payload.get(key)
        if raw is None:
            continue
        normalized = _normalize_uuid(str(raw))
        if normalized:
            return normalized
    return None


def employee_id_from_jwt_token(jwt_token: str) -> Optional[str]:
    payload = decode_jwt_payload_unverified(jwt_token)
    if not payload:
        return None
    return employee_id_from_jwt_payload(payload)
