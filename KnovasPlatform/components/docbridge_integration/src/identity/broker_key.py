"""The Platform's Ed25519 signing key — the thing that makes a person portable.

This key is the Platform's half of the trust boundary. Knovas holds the public
half against the tenant record; whoever holds this private half can assert any
of the firm's people. It therefore never leaves the firm's host, never enters
an image, and never appears in a log.

The failure mode this module exists to prevent is a *silently regenerated* key.
A fresh key still signs perfectly well, so the Platform would look healthy while
every assertion it minted was refused by a backend still holding the old public
key — and the symptom would surface as "search returns nothing", days later, far
from the cause. So: unreadable is an error, never a reason to make a new one.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from .assertion import AssertionSigner, Keypair, generate_keypair

_KEY_NAME = "broker_ed25519.pem"
_PUB_NAME = "broker_ed25519.pub"
_KID_NAME = "broker_ed25519.kid"


class BrokerKeyUnavailableError(RuntimeError):
    """The signing key could not be loaded, and must not be replaced."""


def _paths(key_dir: Path) -> tuple[Path, Path, Path]:
    return key_dir / _KEY_NAME, key_dir / _PUB_NAME, key_dir / _KID_NAME


def load_or_create_signer(key_dir: Path, *, key_id: str | None = None) -> AssertionSigner:
    key_dir = Path(key_dir)
    priv, pub, kid = _paths(key_dir)
    existing_artifacts = [path for path in (priv, pub, kid) if path.exists()]

    if len(existing_artifacts) == 3:
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

    if existing_artifacts:
        existing_names = ", ".join(path.name for path in existing_artifacts)
        raise BrokerKeyUnavailableError(
            f"Incomplete broker key bundle in {key_dir}: found {existing_names}. "
            "Refusing to generate or overwrite key material. Restore all three "
            "artifacts from backup, or rotate deliberately via the Employee Kit."
        )

    if not key_dir.is_dir():
        raise BrokerKeyUnavailableError(
            f"{key_dir} is not a directory. The broker key directory must exist and "
            "be writable before the Platform starts."
        )

    pair: Keypair = generate_keypair()
    resolved_key_id = key_id or secrets.token_urlsafe(16)
    try:
        descriptor = os.open(priv, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise BrokerKeyUnavailableError(
            f"{priv} appeared while creating the broker key. Refusing to overwrite "
            "key material created by another process."
        ) from exc
    with os.fdopen(descriptor, "wb") as private_file:
        private_file.write(pair.private_pem)
    pub.write_bytes(pair.public_pem)
    kid.write_text(resolved_key_id)
    return AssertionSigner(pair.private_pem, key_id=resolved_key_id)


def public_pem(key_dir: Path) -> bytes:
    """The half that is registered with Knovas. Safe to print, copy and mail."""
    _, pub, _ = _paths(Path(key_dir))
    if not pub.exists():
        raise BrokerKeyUnavailableError(f"{pub} does not exist; no key has been generated.")
    return pub.read_bytes()
