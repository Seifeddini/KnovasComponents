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
import logging
import os
import threading
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

logger = logging.getLogger(__name__)

HEADER = "X-Platform-Principal"
ALGORITHM = "EdDSA"
TOKEN_TYPE = "knovas-principal+jws"
MAX_LIFETIME_SECONDS = 300
CLOCK_SKEW_SECONDS = 30
ADMIN_ROLES = frozenset({"admin", "ingestion_manager"})

#: The Platform writes all three broker files into one directory and the root
#: compose mounts that whole directory here, so the private half is inside
#: RemoteController's filesystem. The name is the Platform's
#: (KnovasPlatform/.../identity/broker_key.py::_KEY_NAME).
BROKER_PRIVATE_KEY_NAME = "broker_ed25519.pem"


def refuse_if_broker_private_key_is_readable(pubkey_path: str) -> None:
    """Refuse to start if the Platform's signing key is readable here.

    :ro is not the protection. The protection is that docbridge-web runs as
    root and writes the key 0600 while RemoteController runs as uid 10001,
    which is a fact about two Dockerfiles and nothing enforces it. Whoever
    reads this key can assert any of the firm's people to Knovas, and
    RemoteController is the service that parses untrusted documents -- so if
    the file is ever readable here, stopping is better than serving.

    A missing sibling is the normal case for a pub-only mount and is fine.
    """
    if not pubkey_path:
        return
    private = os.path.join(os.path.dirname(pubkey_path), BROKER_PRIVATE_KEY_NAME)
    if os.access(private, os.R_OK):
        logger.critical(
            "platform broker private key %s is readable by this process; refusing to start",
            private,
        )
        raise SystemExit(1)


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

    # A decoded JSON value need not be an object: `[]`, `null`, `"x"` and `7`
    # all parse cleanly. Anything but a dict fails every .get() below with an
    # AttributeError, which would surface as an unauthenticated 500 instead
    # of a uniform refusal. Reject the shape before reading either field.
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise InvalidPrincipalError("refused")

    # alg is pinned here, never read to choose a verifier; the header's value
    # is only compared.
    if header.get("alg") != ALGORITHM or header.get("typ") != TOKEN_TYPE:
        raise InvalidPrincipalError("refused")
    if header.get("kid") != derive_key_id(public_pem):
        raise InvalidPrincipalError("refused")
    try:
        key = serialization.load_pem_public_key(public_pem)
        if not isinstance(key, ed25519.Ed25519PublicKey):
            raise InvalidPrincipalError("refused")
        key.verify(signature, f"{h64}.{p64}".encode("ascii"))
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
    # `sub` is the only thing RemoteController records about who acted, so an
    # absent, empty or non-string one is worthless in the log line the gate
    # writes. Mirrors the Platform's own check (identity/assertion.py).
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise InvalidPrincipalError("refused")
    jti = str(payload.get("jti") or "")
    if not jti or not replay.burn(jti, exp + CLOCK_SKEW_SECONDS):
        raise InvalidPrincipalError("refused")

    return PlatformPrincipal(
        subject=subject,
        tenant=expected_tenant,
        groups=tuple(str(g) for g in (payload.get("grp") or ())),
        roles=tuple(str(r) for r in (payload.get("rol") or ())),
        jti=jti,
        expires_at=exp,
    )
