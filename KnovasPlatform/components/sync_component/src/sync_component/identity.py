"""What a component holds after it enrolled: its private key, certificate and control token, in its data folder.

The key is made here and never leaves; the Platform has the certificate signed from a request. Every file is readable
by the component's own user only. The enrolment token is used once and not kept.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@dataclass(frozen=True)
class Credentials:
    component: str
    control_token: str
    certificate: Optional[Path]              # None: the Platform issues none; the tenant's is given at installation
    key: Optional[Path]
    chain: Optional[Path]


def _private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


class Store:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.key = self.folder / "component.key"
        self.certificate = self.folder / "component.crt"
        self.chain = self.folder / "chain.pem"
        self.identity = self.folder / "identity.json"

    def load(self) -> Optional[Credentials]:
        """The credentials of an earlier enrolment, or None."""
        if not self.identity.exists():
            return None
        data = json.loads(self.identity.read_text(encoding="utf-8"))
        own = self.certificate.exists() and self.key.exists()
        return Credentials(data["component"], data["control_token"], self.certificate if own else None, self.key if own else None,
                           self.chain if self.chain.exists() else None)

    def request(self, name: str) -> str:
        """A new key, kept here, and the certificate request (PEM) that goes to the Platform."""
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        _private(self.key, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
        csr = x509.CertificateSigningRequestBuilder().subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name[:64])])).sign(key, hashes.SHA256())
        return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")

    def used(self, token: str) -> bool:
        """Whether this one-time token is the one these credentials came from. A different one is the Platform's
        «Token erneuern»: the old control token has stopped working, so the component must enrol again."""
        if not self.identity.exists():
            return False
        data = json.loads(self.identity.read_text(encoding="utf-8"))
        return data.get("enrolled_with") == hashlib.sha256(token.encode("utf-8")).hexdigest()

    def save(self, answer: dict, token: str = "") -> Credentials:
        """Keep what the Platform answered to the enrolment; of the one-time token only its hash."""
        if answer.get("certificate"):
            _private(self.certificate, str(answer["certificate"]).encode("utf-8"))
        else:
            self.certificate.unlink(missing_ok=True)
        # What the Brain's TLS certificate is checked against: the CA the Platform checks it against, else the chain.
        chain = answer.get("brain_ca") or answer.get("certificate_chain")
        if chain:
            _private(self.chain, ("\n".join(chain) if isinstance(chain, list) else str(chain)).encode("utf-8"))
        _private(self.identity, json.dumps({"component": answer["component"], "control_token": answer["control_token"],
                                            "enrolled_with": hashlib.sha256(token.encode("utf-8")).hexdigest() if token else None
                                            }).encode("utf-8"))
        loaded = self.load()
        assert loaded is not None
        return loaded
