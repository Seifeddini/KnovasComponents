"""A Platform-signed principal at RemoteController's door (KC-IN-1).

The firm's Platform signs each signed-in user into its Knovas calls with an
Ed25519 key. RemoteController holds the public half and verifies the same
token, so the firm's own administrator can configure their own ingestion.

This mirrors the Platform's assertion rules exactly; the bounds are theirs.
The kid derivation is a three-line contract shared with
KnovasPlatform/.../identity/broker_key.py::derive_key_id.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization

HEADER = "X-Platform-Principal"
ALGORITHM = "EdDSA"
TOKEN_TYPE = "knovas-principal+jws"
MAX_LIFETIME_SECONDS = 300
CLOCK_SKEW_SECONDS = 30
ADMIN_ROLES = frozenset({"admin", "ingestion_manager"})


class InvalidPrincipalError(Exception):
    """Refused. The message is deliberately uniform."""


@dataclass(frozen=True)
class PlatformPrincipal:
    subject: str
    tenant: str
    groups: tuple[str, ...]
    roles: tuple[str, ...]
    jti: str
    expires_at: int


def derive_key_id(public_pem: bytes) -> str:
    return "bk-" + hashlib.sha256(public_pem).hexdigest()[:16]


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class ReplayGuard:
    """In-process single-use jti store. One RemoteController per firm, so a
    process-local set is the right size; entries expire with the token."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def burn(self, jti: str, until: float) -> bool:
        now = time.time()
        with self._lock:
            for key, expires in list(self._seen.items()):
                if expires <= now:
                    del self._seen[key]
            if jti in self._seen:
                return False
            self._seen[jti] = until
            return True


def verify_platform_principal(
    token: str,
    *,
    public_pem: bytes,
    expected_tenant: str,
    replay: ReplayGuard,
    now: int | None = None,
) -> PlatformPrincipal:
    try:
        h64, p64, s64 = token.split(".")
        header = json.loads(_unb64(h64))
        payload = json.loads(_unb64(p64))
        signature = _unb64(s64)
    except Exception as exc:  # noqa: BLE001
        raise InvalidPrincipalError("refused") from exc

    # alg is pinned here, never read to choose a verifier; the header's value
    # is only compared.
    if header.get("alg") != ALGORITHM or header.get("typ") != TOKEN_TYPE:
        raise InvalidPrincipalError("refused")
    if header.get("kid") != derive_key_id(public_pem):
        raise InvalidPrincipalError("refused")
    try:
        serialization.load_pem_public_key(public_pem).verify(
            signature, f"{h64}.{p64}".encode("ascii")
        )
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise InvalidPrincipalError("refused") from exc

    now = int(time.time()) if now is None else now
    try:
        iat, exp = int(payload.get("iat")), int(payload.get("exp"))
    except (TypeError, ValueError) as exc:
        raise InvalidPrincipalError("refused") from exc
    if exp - iat > MAX_LIFETIME_SECONDS or exp - iat <= 0:
        raise InvalidPrincipalError("refused")
    if now > exp + CLOCK_SKEW_SECONDS or iat > now + CLOCK_SKEW_SECONDS:
        raise InvalidPrincipalError("refused")
    if not expected_tenant or payload.get("tid") != expected_tenant:
        raise InvalidPrincipalError("refused")
    jti = str(payload.get("jti") or "")
    if not jti or not replay.burn(jti, exp + CLOCK_SKEW_SECONDS):
        raise InvalidPrincipalError("refused")

    return PlatformPrincipal(
        subject=str(payload.get("sub") or ""),
        tenant=expected_tenant,
        groups=tuple(str(g) for g in (payload.get("grp") or ())),
        roles=tuple(str(r) for r in (payload.get("rol") or ())),
        jti=jti,
        expires_at=exp,
    )
