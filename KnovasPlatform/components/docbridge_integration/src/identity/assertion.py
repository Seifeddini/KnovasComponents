"""The signed principal assertion — what carries a person across mTLS.

Why this exists
---------------
``KnowledgeBase/app/src/services/rbac/principal_resolver.py`` states the v1
trust boundary in its own docstring, and states it correctly:

    tenant_id     — from the mTLS certificate CN. Authoritative, unforgeable.
    access_groups — from the request body. […] we do not cryptographically
                    bind them to an end user.

This module is the binding. The Platform has just authenticated a person, so it
signs *who they are and which groups they hold* with a key Knovas registered for
this tenant. The Secure API verifies against that key and stops accepting a bare
list from the request body.

Format
------
JWS compact serialisation, Ed25519. Small, no parameter negotiation, and no
RSA-vs-HMAC confusion surface to get wrong.

    header   {"alg":"EdDSA","typ":"knovas-principal+jws","kid":"<key id>"}
    payload  {"sub","tid","grp","rol","iat","exp","jti"}

Rules that are load-bearing rather than stylistic
-------------------------------------------------
    The algorithm is **never** read from the header. ``alg`` is pinned here and
    a token claiming anything else — ``none`` above all — is refused. Reading it
    is the classic JWT break, and a header is attacker-controlled.

    ``tid`` must equal the tenant the *certificate* proved. The token may only
    agree with the certificate, never override it, so a stolen assertion cannot
    be replayed into another tenant.

    ``typ`` separates a principal assertion from a dual-control token. They are
    signed with the same key and must not substitute for each other.

    The lifetime is capped at :data:`MAX_LIFETIME_SECONDS`. That cap is the
    formal bound on how long a person disabled at the Platform keeps access —
    the only part of B1's leaver rule KnowledgeBase can enforce on its own.

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-B2-2, KC-B5-3)
"""
from __future__ import annotations

import base64
import json
import secrets
import time
from dataclasses import dataclass
from typing import Iterable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

#: The only algorithm accepted, pinned in code and never read from a header.
ALGORITHM = "EdDSA"

TOKEN_TYPE = "knovas-principal+jws"
DUAL_CONTROL_TYPE = "knovas-dual-control+jws"

#: Two minutes. Long enough to survive a slow request, short enough that a
#: captured token is worth little and a revoked user is out quickly.
DEFAULT_LIFETIME_SECONDS = 120
MAX_LIFETIME_SECONDS = 300

#: A dual-control token is used once, immediately, by a person who is waiting.
DUAL_CONTROL_LIFETIME_SECONDS = 900

#: Tolerance for clock drift between the Platform and Knovas.
CLOCK_SKEW_SECONDS = 30


class InvalidAssertionError(Exception):
    """The token is not acceptable. Never says why to a caller."""


class ExpiredAssertionError(InvalidAssertionError):
    """The token was valid and is not any more."""


@dataclass(frozen=True)
class Keypair:
    private_pem: bytes
    public_pem: bytes


@dataclass(frozen=True)
class PrincipalClaims:
    subject: str
    tenant: str
    groups: tuple[str, ...]
    roles: tuple[str, ...]
    jti: str
    issued_at: int
    expires_at: int


@dataclass(frozen=True)
class DualControlClaims:
    tenant: str
    action: str
    target: str
    requester: str
    approver: str
    jti: str
    expires_at: int


# ── encoding helpers ───────────────────────────────────────────────────────


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def generate_keypair() -> Keypair:
    """A fresh Ed25519 keypair. The private half never leaves the firm."""
    private = ed25519.Ed25519PrivateKey.generate()
    return Keypair(
        private_pem=private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        public_pem=private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )


class AssertionSigner:
    """Mints tokens with the Platform's private key."""

    def __init__(self, private_pem: bytes | str, *, key_id: str) -> None:
        if isinstance(private_pem, str):
            private_pem = private_pem.encode("utf-8")
        key = serialization.load_pem_private_key(private_pem, password=None)
        if not isinstance(key, ed25519.Ed25519PrivateKey):
            raise ValueError("The broker key must be Ed25519.")
        self._key = key
        self._key_id = key_id

    def _sign(self, typ: str, payload: Mapping[str, object]) -> str:
        header = {"alg": ALGORITHM, "typ": typ, "kid": self._key_id}
        signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}." \
                        f"{_b64(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode())}"
        signature = self._key.sign(signing_input.encode("ascii"))
        return f"{signing_input}.{_b64(signature)}"

    def mint(
        self,
        *,
        subject: str,
        tenant: str,
        groups: Iterable[str],
        roles: Iterable[str] = (),
        lifetime_seconds: int = DEFAULT_LIFETIME_SECONDS,
        issued_at: float | None = None,
    ) -> str:
        """Sign one request's principal.

        ``subject`` is the opaque local user id, never an address or a name:
        Knovas has no business learning who works at the firm.

        Raises:
            ValueError: the requested lifetime exceeds MAX_LIFETIME_SECONDS.
                Refused at mint time so nothing can quietly widen the window
                that bounds revocation.
        """
        if lifetime_seconds > MAX_LIFETIME_SECONDS:
            raise ValueError(
                f"An assertion may live at most {MAX_LIFETIME_SECONDS}s; "
                f"{lifetime_seconds}s was requested. This cap is what bounds how "
                "long a disabled user keeps access."
            )
        now = int(issued_at if issued_at is not None else time.time())
        return self._sign(
            TOKEN_TYPE,
            {
                "sub": subject,
                "tid": tenant,
                # Sorted and deduplicated so two mints for the same person are
                # byte-identical — useful in a log, a test and a support call.
                "grp": sorted({g for g in groups if g}),
                "rol": sorted({r for r in roles if r}),
                "iat": now,
                "exp": now + lifetime_seconds,
                "jti": secrets.token_urlsafe(16),
            },
        )

    def mint_dual_control(
        self,
        *,
        tenant: str,
        action: str,
        target: str,
        requester: str,
        approver: str,
        lifetime_seconds: int = DUAL_CONTROL_LIFETIME_SECONDS,
    ) -> str:
        """Sign a two-person decision so the backend can enforce it.

        Raises:
            ValueError: requester and approver are the same. Refused here as
                well as in the service and in the database — three places,
                because a four-eyes control that one bug can disable is not one.
        """
        if requester == approver:
            raise ValueError("A dual-control token needs two different people.")
        now = int(time.time())
        return self._sign(
            DUAL_CONTROL_TYPE,
            {
                "tid": tenant,
                "act": action,
                "obj": target,
                "req": requester,
                "apr": approver,
                "iat": now,
                "exp": now + lifetime_seconds,
                "jti": secrets.token_urlsafe(16),
            },
        )


class AssertionVerifier:
    """Checks tokens against the registered public keys.

    Used by the RemoteController for the tenant-admin path, and mirrored in
    KnowledgeBase for the Secure API. Kept here too so the Platform can verify
    its own output in tests — a signer nobody can check is a signer nobody
    should trust.
    """

    def __init__(self, public_keys: Mapping[str, bytes | str]) -> None:
        self._keys: dict[str, ed25519.Ed25519PublicKey] = {}
        for key_id, pem in public_keys.items():
            if isinstance(pem, str):
                pem = pem.encode("utf-8")
            key = serialization.load_pem_public_key(pem)
            if not isinstance(key, ed25519.Ed25519PublicKey):
                raise ValueError(f"Key {key_id!r} is not Ed25519.")
            self._keys[key_id] = key

    def _decode(self, token: str, expected_type: str) -> Mapping[str, object]:
        if not isinstance(token, str) or token.count(".") != 2:
            raise InvalidAssertionError("Malformed token.")
        header_b64, payload_b64, signature_b64 = token.split(".")
        try:
            header = json.loads(_unb64(header_b64))
            payload = json.loads(_unb64(payload_b64))
            signature = _unb64(signature_b64)
        except Exception as exc:  # noqa: BLE001
            raise InvalidAssertionError("Malformed token.") from exc
        if not isinstance(header, dict) or not isinstance(payload, dict):
            raise InvalidAssertionError("Malformed token.")

        # The algorithm is ours, not the token's. A header that disagrees is
        # rejected rather than obeyed — this is the algorithm-confusion break.
        if header.get("alg") != ALGORITHM:
            raise InvalidAssertionError("Unacceptable algorithm.")
        if header.get("typ") != expected_type:
            raise InvalidAssertionError("Wrong token type.")

        key = self._keys.get(str(header.get("kid", "")))
        if key is None:
            raise InvalidAssertionError("Unknown key.")
        try:
            key.verify(signature, f"{header_b64}.{payload_b64}".encode("ascii"))
        except InvalidSignature as exc:
            raise InvalidAssertionError("Bad signature.") from exc

        now = time.time()
        expires_at = float(payload.get("exp", 0))
        issued_at = float(payload.get("iat", 0))
        if expires_at <= now - CLOCK_SKEW_SECONDS:
            raise ExpiredAssertionError("Token expired.")
        if issued_at > now + CLOCK_SKEW_SECONDS:
            raise InvalidAssertionError("Token issued in the future.")
        return payload

    def verify(self, token: str, *, tenant: str) -> PrincipalClaims:
        """Verify a principal assertion for ``tenant``.

        ``tenant`` is what the mTLS certificate proved. The token may agree with
        it and nothing else.
        """
        payload = self._decode(token, TOKEN_TYPE)
        if str(payload.get("tid", "")) != str(tenant):
            raise InvalidAssertionError("Token is for a different tenant.")
        subject = str(payload.get("sub", ""))
        if not subject:
            raise InvalidAssertionError("Token names no subject.")
        return PrincipalClaims(
            subject=subject,
            tenant=str(payload["tid"]),
            groups=tuple(str(g) for g in payload.get("grp", ())),
            roles=tuple(str(r) for r in payload.get("rol", ())),
            jti=str(payload.get("jti", "")),
            issued_at=int(payload.get("iat", 0)),
            expires_at=int(payload.get("exp", 0)),
        )

    def verify_dual_control(
        self, token: str, *, tenant: str, action: str, target: str
    ) -> DualControlClaims:
        """Verify an approval, bound to exactly this action and target.

        Binding both is what stops an approval to delete one matter from
        authorising the deletion of another.
        """
        payload = self._decode(token, DUAL_CONTROL_TYPE)
        if str(payload.get("tid", "")) != str(tenant):
            raise InvalidAssertionError("Token is for a different tenant.")
        if str(payload.get("act", "")) != str(action):
            raise InvalidAssertionError("Token authorises a different action.")
        if str(payload.get("obj", "")) != str(target):
            raise InvalidAssertionError("Token authorises a different target.")
        requester = str(payload.get("req", ""))
        approver = str(payload.get("apr", ""))
        if not requester or not approver or requester == approver:
            raise InvalidAssertionError("Token does not name two distinct people.")
        return DualControlClaims(
            tenant=str(payload["tid"]),
            action=str(payload["act"]),
            target=str(payload["obj"]),
            requester=requester,
            approver=approver,
            jti=str(payload.get("jti", "")),
            expires_at=int(payload.get("exp", 0)),
        )
