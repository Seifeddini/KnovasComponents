"""Permits (connector protocol 2.0, "Permits"; permit format 1, the same the Brain builds).

A permit is a JWS compact token, ``base64url(header) "." base64url(payload) "." base64url(signature)``. Header and
payload are each the canonical JSON of their object, and each part is base64url without padding in its one canonical
spelling (no unused bits set), so one permit has exactly one encoding; the signature is Ed25519 over the ASCII of
``header "." payload``. ``kid`` is ``wbp-`` and the first 16 hex digits of SHA-256 over the raw public key. A token is
untrusted input: whatever it holds (a time past the year 9999, a deeply nested part), decoding and verifying refuse it
with ``PermitError`` and raise nothing else. This module decodes and verifies tokens, holds the pinned key ring, and
signs (only the suite's stand-in for the Brain and tests sign; at a site nothing does).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from knovas_connector_kit import contracts
from knovas_connector_kit.canonical import CanonicalError, canonical

HEADER_TYP = "knovas-permit+jws"
#: The protocol's maximum ``exp - iat``: 3 hours (v2.0 as amended 2026-11-10, D-15; it was 3600 s).
MAX_LIFETIME_SECONDS = 10800
#: The longest token read (the Brain's bound and the job schema's ``permits`` items).
MAX_TOKEN_CHARS = 8192
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class PermitError(Exception):
    """A permit is refused; ``code`` is the protocol's code, never a sentence."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64d(text: str) -> bytes:
    """base64url without padding in its one canonical spelling: a length of 1 modulo 4, or unused bits that are not
    zero (another spelling of the same bytes), is refused."""
    if not text or len(text) % 4 == 1 or any(
            c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in text):
        raise PermitError("permit_bad_signature")
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise PermitError("permit_bad_signature") from None
    if b64e(raw) != text:
        raise PermitError("permit_bad_signature")
    return raw


def kid_for(public_raw: bytes) -> str:
    return "wbp-" + hashlib.sha256(public_raw).hexdigest()[:16]


def public_raw(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


@dataclass(frozen=True)
class PinnedKey:
    kid: str
    key: Ed25519PublicKey
    not_before: str
    not_after: str


class KeyRing:
    """The permit keys Link pinned from ``GET /secured/connector/v2/permit-keys``."""

    def __init__(self, keys: Iterable[PinnedKey] = ()):
        self._keys = {k.kid: k for k in keys}

    @classmethod
    def from_document(cls, document: dict) -> "KeyRing":
        if not contracts.valid("permit-keys.schema.json", document):
            raise PermitError("permit_bad_signature")
        out = []
        for k in document["keys"]:
            raw = b64d(k["public_key"])
            if kid_for(raw) != k["kid"]:
                raise PermitError("permit_bad_signature")
            out.append(PinnedKey(k["kid"], Ed25519PublicKey.from_public_bytes(raw), k["not_before"], k["not_after"]))
        return cls(out)

    def get(self, kid: str, at_iso: str) -> Optional[PinnedKey]:
        """The key ``kid`` if its window holds ``at_iso`` (a permit's ``iat``)."""
        key = self._keys.get(kid)
        if key is None or not (key.not_before <= at_iso < key.not_after):
            return None
        return key


@dataclass(frozen=True)
class Decoded:
    header: dict
    payload: dict
    signing_input: bytes
    signature: bytes


def decode(token: object) -> Decoded:
    """Split and parse a token, refusing anything that is not exactly one canonical encoding. Does not verify. Raises
    nothing but ``PermitError``, whatever the token holds."""
    try:
        return _decode(token)
    except PermitError:
        raise
    except Exception:  # noqa: BLE001 - an untrusted token never raises past a refusal (deep nesting, huge numbers)
        raise PermitError("permit_bad_signature") from None


def _decode(token: object) -> Decoded:
    if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS or token.count(".") != 2:
        raise PermitError("permit_bad_signature")
    h, p, s = token.split(".")
    try:
        header, payload = json.loads(b64d(h)), json.loads(b64d(p))
        if canonical(header) != b64d(h) or canonical(payload) != b64d(p):
            raise PermitError("permit_bad_signature")
    except (ValueError, CanonicalError, RecursionError):
        raise PermitError("permit_bad_signature") from None
    if not contracts.valid("permit.schema.json", {"header": header, "payload": payload}):
        raise PermitError("permit_bad_signature")
    return Decoded(header, payload, f"{h}.{p}".encode("ascii"), b64d(s))


def permit_id_unverified(token: object) -> Optional[str]:
    """The ``permit_id`` a token names, not verified: for a write log only, so a refused permit is logged under the id
    it names. None when it names none; never raises."""
    try:
        if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS or token.count(".") != 2:
            return None
        payload = json.loads(b64d(token.split(".")[1]))
        value = payload.get("permit_id") if isinstance(payload, dict) else None
        return value if isinstance(value, str) and _UUID.match(value) else None
    except Exception:  # noqa: BLE001 - an untrusted token has no id rather than an error
        return None


def _iso(ts: int) -> Optional[str]:
    """``ts`` as the UTC time a key's window is written in; None for a time no calendar date holds (past 9999)."""
    import datetime as dt
    try:
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return None


def verify(token: object, ring: KeyRing) -> Decoded:
    """Decode and verify: ``alg`` EdDSA, a pinned key whose window holds the permit's ``iat`` (checked at the signing
    time, not at the verifier's clock, so a repeat after a key's retirement still gets its first result), ``exp``
    after ``iat`` and at most ``MAX_LIFETIME_SECONDS`` (10800 s) later, and a valid signature; else
    ``permit_bad_signature``. Raises nothing but ``PermitError``."""
    d = decode(token)
    iat, exp = d.payload["iat"], d.payload["exp"]
    if not (iat < exp <= iat + MAX_LIFETIME_SECONDS):
        raise PermitError("permit_bad_signature")
    signed_at = _iso(iat)
    key = ring.get(d.header["kid"], signed_at) if signed_at is not None else None
    if key is None or d.header["alg"] != "EdDSA":
        raise PermitError("permit_bad_signature")
    try:
        key.key.verify(d.signature, d.signing_input)
    except InvalidSignature:
        raise PermitError("permit_bad_signature") from None
    return d


class Signer:
    """Signs permits: the conformance suite's stand-in for the Brain, and tests. Never used at a site."""

    def __init__(self, private_key: Optional[Ed25519PrivateKey] = None):
        self.private_key = private_key or Ed25519PrivateKey.generate()
        self.public_raw = public_raw(self.private_key.public_key())
        self.kid = kid_for(self.public_raw)

    def keys_document(self, not_before: str = "2000-01-01T00:00:00Z", not_after: str = "2100-01-01T00:00:00Z") -> dict:
        return {"keys": [{"kid": self.kid, "alg": "EdDSA", "public_key": b64e(self.public_raw),
                          "not_before": not_before, "not_after": not_after}]}

    def sign(self, payload: dict) -> str:
        header = {"alg": "EdDSA", "kid": self.kid, "typ": HEADER_TYP}
        signing_input = b64e(canonical(header)) + "." + b64e(canonical(payload))
        return signing_input + "." + b64e(self.private_key.sign(signing_input.encode("ascii")))
