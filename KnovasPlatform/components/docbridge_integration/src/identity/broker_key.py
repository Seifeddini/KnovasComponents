"""The Platform's Ed25519 signing key -- the thing that makes a person portable.

This key is the Platform's half of the trust boundary. Knovas holds the public
half against the tenant record; whoever holds this private half can assert any
of the firm's people. It therefore never leaves the firm's host, never enters
an image, and never appears in a log.

The failure mode this module exists to prevent is a *silently regenerated* key.
A fresh key still signs perfectly well, so the Platform would look healthy while
every assertion it minted was refused by a backend still holding the old public
key -- and the symptom would surface as "search returns nothing", days later,
far from the cause. So: unreadable is an error, never a reason to make a new one.

Plan: docs/superpowers/plans/2026-09-02-auth-assertion-end-to-end.md, Task 3.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from .assertion import AssertionSigner, Keypair, generate_keypair

_KEY_NAME = "broker_ed25519.pem"
_PUB_NAME = "broker_ed25519.pub"
_KID_NAME = "broker_ed25519.kid"


class BrokerKeyUnavailableError(RuntimeError):
    """The signing key could not be loaded, and must not be replaced."""


def _paths(key_dir: Path) -> tuple[Path, Path, Path]:
    return key_dir / _KEY_NAME, key_dir / _PUB_NAME, key_dir / _KID_NAME


def derive_key_id(public_pem: bytes) -> str:
    """A kid computed from the public half, so Knovas can recompute it.

    The backend selects which registered key to verify against by this id.
    Deriving it from the public PEM means the operator who registers the
    key and the Platform that signs with it can never disagree about it.
    """
    return "bk-" + hashlib.sha256(public_pem).hexdigest()[:16]


def load_or_create_signer(key_dir: Path, *, key_id: str | None = None) -> AssertionSigner:
    """Load the firm's signing key, or create it exactly once.

    Raises:
        BrokerKeyUnavailableError: the key exists but cannot be loaded, or the
            directory does not exist. Neither is ever repaired by generating a
            new key.
    """
    key_dir = Path(key_dir)
    priv, pub, kid = _paths(key_dir)

    # A half-restored backup is the dangerous case: with the private half
    # missing but its siblings present, "create if absent" would mint a new key
    # and every assertion would then be refused by a backend still holding the
    # old public half. Any incomplete bundle is an error, never a starting point.
    present = [p for p in (priv, pub, kid) if p.exists()]
    if present and len(present) != 3:
        raise BrokerKeyUnavailableError(
            f"Incomplete broker key bundle in {key_dir}: found "
            f"{', '.join(p.name for p in present)}. Refusing to generate or "
            "overwrite key material. Restore all three artifacts from backup, "
            "or rotate deliberately via the Employee Kit."
        )

    if priv.exists():
        try:
            return AssertionSigner(priv.read_bytes(), key_id=kid.read_text().strip())
        except Exception as exc:  # noqa: BLE001 - any failure here is fatal by design
            raise BrokerKeyUnavailableError(
                f"{priv} exists but could not be loaded as an Ed25519 signing key "
                f"({exc}). Refusing to generate a replacement: a new key would sign "
                "happily and every assertion would then be rejected by Knovas, which "
                "still holds the old public key. Restore the key from backup, or "
                "rotate deliberately via the Employee Kit."
            ) from exc

    if not key_dir.is_dir():
        raise BrokerKeyUnavailableError(
            f"{key_dir} is not a directory. The broker key directory must exist and "
            "be writable before the Platform starts."
        )

    pair: Keypair = generate_keypair()
    chosen_kid = key_id or derive_key_id(pair.public_pem)
    # O_EXCL with mode 0600 rather than write-then-chmod: the file is never
    # briefly world-readable, and a second gunicorn worker racing to create the
    # same key loses here instead of overwriting the winner's private half.
    try:
        fd = os.open(priv, os.O_CREAT | os.O_EXCL | os.O_WRONLY, stat.S_IRUSR | stat.S_IWUSR)
    except FileExistsError as exc:
        raise BrokerKeyUnavailableError(
            f"{priv} appeared while creating the broker key. Refusing to "
            "overwrite key material created by another process."
        ) from exc
    with os.fdopen(fd, "wb") as handle:
        handle.write(pair.private_pem)
    pub.write_bytes(pair.public_pem)
    kid.write_text(chosen_kid)
    return AssertionSigner(pair.private_pem, key_id=chosen_kid)


def public_pem(key_dir: Path) -> bytes:
    """The half that is registered with Knovas. Safe to print, copy and mail."""
    _, pub, _ = _paths(Path(key_dir))
    if not pub.exists():
        raise BrokerKeyUnavailableError(f"{pub} does not exist; no key has been generated.")
    return pub.read_bytes()
