"""Parse employee RC mTLS headers forwarded by edge reverse proxy."""
from __future__ import annotations

import re
import uuid
from functools import wraps
from typing import Any, Optional
from urllib.parse import unquote

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from flask import g, jsonify, request

from auth.cert_identity import normalize_certificate_serial_to_str
from config import get_config

_EMPLOYEE_CN_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def employee_id_from_cn(cn: Optional[str]) -> Optional[str]:
    if not cn or cn == "Unknown":
        return None
    cn = str(cn).strip()
    if _EMPLOYEE_CN_UUID.match(cn):
        try:
            return str(uuid.UUID(cn))
        except ValueError:
            return None
    return None


def _parse_dn(dn: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in dn.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip().upper()
        value = value.strip()
        if key in ("CN", "COMMONNAME"):
            result["common_name"] = value
        elif key in ("OU", "ORGANIZATIONALUNIT"):
            result["organisational_unit"] = value
    return result


def _decode_cert_header(raw: str) -> str:
    decoded = unquote(raw)
    return decoded.replace("\t", "\n")


def extract_mtls_from_request() -> tuple[bool, Optional[dict[str, Any]], Optional[tuple]]:
    cfg = get_config()
    if cfg.rc_mtls_dev_bypass and cfg.rc_mtls_dev_employee_id:
        try:
            emp = str(uuid.UUID(cfg.rc_mtls_dev_employee_id))
        except ValueError:
            return False, None, ({"error": "Invalid RC_MTLS_DEV_EMPLOYEE_ID", "status": "error"}, 500)
        return (
            True,
            {
                "employee_id": emp,
                "common_name": emp,
                "serial_number": "rc-dev-bypass",
            },
            None,
        )

    cert_header = request.headers.get("X-SSL-Client-Cert") or request.headers.get("SSL-Client-Cert")
    if not cert_header:
        return (
            False,
            None,
            ({"error": "Employee RC client certificate required", "status": "error"}, 401),
        )

    cert_pem = _decode_cert_header(cert_header)
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"), default_backend())
    except Exception:
        return (
            False,
            None,
            ({"error": "Invalid employee RC client certificate", "status": "error"}, 401),
        )

    serial = normalize_certificate_serial_to_str(cert.serial_number)
    dn_header = request.headers.get("X-SSL-Client-DN") or request.headers.get("X-SSL-Client-Subject")
    if dn_header:
        parsed = _parse_dn(dn_header)
        cn = parsed.get("common_name")
    else:
        attrs = cert.subject.rfc4514_string()
        parsed = _parse_dn(attrs.replace("+", ","))
        cn = parsed.get("common_name")

    employee_id = employee_id_from_cn(cn)
    if not employee_id:
        return (
            False,
            None,
            ({"error": "Employee RC certificate CN must be operator UUID", "status": "error"}, 401),
        )

    return (
        True,
        {"employee_id": employee_id, "common_name": cn, "serial_number": serial},
        None,
    )


def require_rc_mtls(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        ok, cert_info, err = extract_mtls_from_request()
        if not ok:
            body, status = err or ({"error": "mTLS validation failed", "status": "error"}, 401)
            return jsonify(body), status
        g.employee_rc_cert_info = cert_info
        g.rc_employee_id = cert_info["employee_id"]
        g.rc_certificate_serial = cert_info["serial_number"]
        return func(*args, **kwargs)

    return wrapper
