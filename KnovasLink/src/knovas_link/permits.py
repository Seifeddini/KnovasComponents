"""Permits of connector protocol 2.0 (draft), as Knovas Link checks them (``connector-protocol-v2.md``, "Permits" and
"What Knovas Link checks"; the Brain's permit format 1). The same rules as the connector kit's reference
(``KnovasConnectorKit``), written again here because Link imports nothing from the rest of the repository; a test
holds both to the same verdicts.

A permit is a JWS compact token: ``base64url(header) "." base64url(payload) "." base64url(signature)``, header and
payload each the canonical JSON (RFC 8785) of their object, the signature Ed25519 over the ASCII of
``header "." payload``; each part is base64url without padding in its one canonical spelling (no unused bits set), so
a permit has exactly one encoding. ``kid`` is ``wbp-`` and the first 16 hex digits of SHA-256 over the raw public key.
A key's window is checked at the permit's ``iat``, never at Link's clock, so a repeat after a key's retirement still
reaches the used-permit store and gets its first result. A token is untrusted input: whatever it holds (a time past
the year 9999, a deeply nested part, anything else), it is refused with the protocol's code and never raises
anything else. Nothing here logs a token, a payload or a key.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from knovas_link import contracts

#: The protocol's maximum ``exp - iat``: 3 hours (v2.0 as amended 2026-11-10, D-15; it was 3600 s).
MAX_LIFETIME_SECONDS = 10800
MAX_SAFE_INTEGER = 2 ** 53 - 1
#: The longest token Link reads (the Brain's own bound; the schema's token pattern allows at most 5208 characters).
MAX_TOKEN_CHARS = 8192
_B64 = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class PermitRefused(Exception):
    """A permit Link refuses; ``code`` is the protocol's code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class CanonicalError(ValueError):
    pass


def _encode(value: Any, out: list) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalError("integer")
        out.append(str(value))
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, list):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _encode(item, out)
        out.append("]")
    elif isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise CanonicalError("key")
        out.append("{")
        for i, key in enumerate(sorted(value, key=lambda k: k.encode("utf-16-be"))):
            if i:
                out.append(",")
            out.append(json.dumps(key, ensure_ascii=False))
            out.append(":")
            _encode(value[key], out)
        out.append("}")
    else:                                   # a float, NaN or anything else has no canonical spelling here
        raise CanonicalError("type")


def canonical(value: Any) -> bytes:
    out: list = []
    _encode(value, out)
    return "".join(out).encode("utf-8")


def params_hash(target: str, params: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical({"target": target, "params": params})).hexdigest()


def _b64d(text: str) -> bytes:
    """base64url without padding, in its one canonical spelling: a length of 1 modulo 4, or unused bits that are not
    zero (another spelling of the same bytes), is refused (L5)."""
    if not text or not set(text) <= _B64 or len(text) % 4 == 1:
        raise PermitRefused("permit_bad_signature")
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise PermitRefused("permit_bad_signature") from None
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != text:
        raise PermitRefused("permit_bad_signature")
    return raw


def _iso(ts: int) -> Optional[str]:
    """``ts`` as the UTC time a key's window is written in; None for a time no calendar date holds (past 9999)."""
    try:
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return None


@dataclass(frozen=True)
class PinnedKey:
    kid: str
    public_key: str            # base64url, as the Brain sent it
    not_before: str
    not_after: str


class KeyRing:
    """The permit keys pinned from ``GET /secured/connector/v2/permit-keys``, and only from there. A key the Brain no
    longer lists is kept until 30 days after its ``not_after``, as long as Link keeps the used permits it verified."""

    KEEP_DAYS = 30

    def __init__(self, keys: Optional[dict] = None):
        self.keys: dict[str, PinnedKey] = dict(keys or {})

    @staticmethod
    def parse(document: Any) -> dict:
        """The keys of a permit-keys answer, each checked: its kid is the hash of its key. Else ValueError."""
        if not contracts.v2_valid("permit-keys.schema.json", document):
            raise ValueError("permit_keys")
        out = {}
        for k in document["keys"]:
            try:
                raw = _b64d(k["public_key"])
            except PermitRefused:
                raise ValueError("permit_keys") from None
            if len(raw) != 32 or "wbp-" + hashlib.sha256(raw).hexdigest()[:16] != k["kid"]:
                raise ValueError("permit_keys")
            out[k["kid"]] = PinnedKey(k["kid"], k["public_key"], k["not_before"], k["not_after"])
        return out

    def pin(self, document: Any, now: dt.datetime) -> int:
        """Take the Brain's answer; keep earlier keys within their retention. The number of keys held."""
        fresh = self.parse(document)
        cutoff = (now - dt.timedelta(days=self.KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        kept = {kid: k for kid, k in self.keys.items() if kid not in fresh and k.not_after > cutoff}
        self.keys = {**kept, **fresh}
        return len(self.keys)

    def to_json(self) -> dict:
        return {"keys": [{"kid": k.kid, "alg": "EdDSA", "public_key": k.public_key, "not_before": k.not_before,
                          "not_after": k.not_after} for k in self.keys.values()]}

    def at(self, kid: str, iat: int) -> Optional[Ed25519PublicKey]:
        key, signed_at = self.keys.get(kid), _iso(iat)
        if key is None or signed_at is None or not (key.not_before <= signed_at < key.not_after):
            return None
        return Ed25519PublicKey.from_public_bytes(_b64d(key.public_key))


@dataclass(frozen=True)
class Permit:
    kid: str
    payload: dict


def verify(token: Any, ring: KeyRing) -> Permit:
    """Step 2 of Link's checks: one canonical encoding, EdDSA, a pinned key whose window holds ``iat``, a lifetime
    of at most ``MAX_LIFETIME_SECONDS`` (10800 s), and a valid signature; else ``permit_bad_signature``. Raises
    nothing but ``PermitRefused``, whatever the token holds (L1)."""
    try:
        return _verify(token, ring)
    except PermitRefused:
        raise
    except Exception:  # noqa: BLE001 - an untrusted token never raises past a refusal (deep nesting, huge numbers)
        raise PermitRefused("permit_bad_signature") from None


def permit_id_unverified(token: Any) -> Optional[str]:
    """The ``permit_id`` a token names, not verified: only for the site's write log, so that a refused or forged permit
    is still logged under the id it names (the Brain's ``permit_id_unverified``). None when it names none."""
    try:
        if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS or token.count(".") != 2:
            return None
        payload = json.loads(_b64d(token.split(".")[1]))
        value = payload.get("permit_id") if isinstance(payload, dict) else None
        return value if isinstance(value, str) and _UUID.match(value) else None
    except Exception:  # noqa: BLE001 - an untrusted token has no id rather than an error
        return None


def _verify(token: Any, ring: KeyRing) -> Permit:
    if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS or token.count(".") != 2:
        raise PermitRefused("permit_bad_signature")
    h, p, s = token.split(".")
    try:
        header_raw, payload_raw = _b64d(h), _b64d(p)
        header, payload = json.loads(header_raw), json.loads(payload_raw)
        if canonical(header) != header_raw or canonical(payload) != payload_raw:
            raise PermitRefused("permit_bad_signature")
    except (ValueError, CanonicalError, RecursionError):
        raise PermitRefused("permit_bad_signature") from None
    if not contracts.v2_valid("permit.schema.json", {"header": header, "payload": payload}):
        raise PermitRefused("permit_bad_signature")
    if not payload["iat"] < payload["exp"] <= payload["iat"] + MAX_LIFETIME_SECONDS:
        raise PermitRefused("permit_bad_signature")
    key = ring.at(header["kid"], payload["iat"])
    if key is None:
        raise PermitRefused("permit_bad_signature")
    try:
        key.verify(_b64d(s), f"{h}.{p}".encode("ascii"))
    except InvalidSignature:
        raise PermitRefused("permit_bad_signature") from None
    return Permit(header["kid"], payload)
