"""What Link holds after it enrolled: its private key, certificate and control token, in its data folder.

Copied from the sync component (``sync_component/identity.py``, MVP-15), which keeps its own copy for direct mode.
One change: the new key is written only together with the Platform's answer, so a failed re-enrolment («Token
erneuern» with a token that is refused) does not leave a certificate next to a key it does not belong to.

The key is made here and never leaves; the Platform has the certificate signed from a request. Every file is readable
by Link's own user only, and the folder is closed to everyone else. The enrolment token is used once and not kept; of
it only its hash is stored, to see whether a token in the environment is a new one.

Where the Platform issues no certificate (DEC-0056, A1: production's secure-api cannot sign), the request is made again
from the same stored key (``certificate-request``) and the certificate Knovas signed from it is written next to the key
(``install-certificate``, ``knovas_link.certificate``).
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
    certificate: Optional[Path]              # None: the Platform issued none (DEC-0056); Link waits for its certificate
    key: Optional[Path]
    chain: Optional[Path]


def _private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.chmod(path, 0o600)


def _private_replace(path: Path, data: bytes) -> None:
    """``_private``, but a reader sees the old file or the new one, never half of it: the running Link may read it.
    Written by root (``docker compose exec -u root``), the file is given to the folder's owner, Link's user."""
    tmp = path.with_name(path.name + ".tmp")
    _private(tmp, data)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        owner = path.parent.stat()
        os.chown(tmp, owner.st_uid, owner.st_gid)
    os.replace(tmp, path)


def _csr(key: rsa.RSAPrivateKey, name: str) -> str:
    """The certificate request (PEM) for this key. Its subject is the common name only: the CA adds the tenant from
    the ``customer_id`` it signs for, so a request that named one itself would only be suspect."""
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name[:64])])
    csr = x509.CertificateSigningRequestBuilder().subject_name(subject).sign(key, hashes.SHA256())
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


class Store:
    def __init__(self, folder: Path, *, key_size: int = 3072):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.folder, 0o700)
        except OSError:                       # a folder Link does not own: the files themselves stay private
            pass
        self.key_size = key_size
        self.key = self.folder / "component.key"
        self.certificate = self.folder / "component.crt"
        self.chain = self.folder / "chain.pem"
        self.identity = self.folder / "identity.json"
        #: Knovas's client CA as the Platform named it at enrolment: install-certificate takes only what it signed.
        self.client_ca_file = self.folder / "client-ca.pem"

    def load(self) -> Optional[Credentials]:
        """The credentials of an earlier enrolment, or None."""
        if not self.identity.exists():
            return None
        data = json.loads(self.identity.read_text(encoding="utf-8"))
        own = self.certificate.exists() and self.key.exists()
        return Credentials(data["component"], data["control_token"], self.certificate if own else None,
                           self.key if own else None, self.chain if self.chain.exists() else None)

    def request(self, name: str) -> tuple[str, bytes]:
        """A new key (kept by the caller until the enrolment succeeded) and the certificate request (PEM) that goes
        to the Platform."""
        key = rsa.generate_private_key(public_exponent=65537, key_size=self.key_size)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        return _csr(key, name), pem

    def private_key(self) -> Optional[rsa.RSAPrivateKey]:
        """The key enrolment made and kept, or None before the first enrolment. It stays in this process."""
        if not (self.identity.exists() and self.key.exists()):
            return None
        key = serialization.load_pem_private_key(self.key.read_bytes(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError("component.key")
        return key

    def certificate_request(self, name: str) -> Optional[str]:
        """The certificate request (PEM) from the stored key, for Knovas to sign (DEC-0056, A1); None before the first
        enrolment. Asking again makes the same key's request again; the key does not change."""
        key = self.private_key()
        return None if key is None else _csr(key, name)

    def tenant(self) -> Optional[str]:
        """The site's tenant as the Platform named it at enrolment, or None."""
        if not self.identity.exists():
            return None
        found = json.loads(self.identity.read_text(encoding="utf-8")).get("tenant")
        return str(found) if found else None

    def client_ca(self) -> Optional[bytes]:
        """Knovas's client CA (PEM) as the Platform named it at enrolment, or None."""
        return self.client_ca_file.read_bytes() if self.client_ca_file.exists() else None

    def install_certificate(self, pem: bytes) -> None:
        """Write a certificate Knovas signed from ``certificate_request``, checked by the caller, as Link's own
        (``knovas_link.certificate.install``)."""
        _private_replace(self.certificate, pem)

    def used(self, token: str) -> bool:
        """Whether this one-time token is the one these credentials came from. A different one is the Platform's
        «Token erneuern»: the old control token has stopped working, so Link must enrol again."""
        if not self.identity.exists():
            return False
        data = json.loads(self.identity.read_text(encoding="utf-8"))
        return data.get("enrolled_with") == hashlib.sha256(token.encode("utf-8")).hexdigest()

    def save(self, answer: dict, token: str, key_pem: bytes) -> Credentials:
        """Keep what the Platform answered to the enrolment, with the key it was made for; of the one-time token only
        its hash."""
        component, control = answer["component"], answer["control_token"]
        _private(self.key, key_pem)
        if answer.get("certificate"):
            _private(self.certificate, str(answer["certificate"]).encode("utf-8"))
        else:
            self.certificate.unlink(missing_ok=True)
        # What the Brain's TLS certificate is checked against: the CA the Platform checks it against, else the chain.
        chain = answer.get("brain_ca") or answer.get("certificate_chain")
        if chain:
            _private(self.chain, ("\n".join(chain) if isinstance(chain, list) else str(chain)).encode("utf-8"))
        else:
            self.chain.unlink(missing_ok=True)
        # The site's tenant and Knovas's client CA, as the Platform named them: install-certificate takes only a
        # certificate that CA signed for that tenant (S-4 M2). An answer without them leaves Link unable to install one.
        client_ca = answer.get("client_ca")
        if isinstance(client_ca, str) and "BEGIN CERTIFICATE" in client_ca:
            _private(self.client_ca_file, client_ca.encode("utf-8"))
        else:
            self.client_ca_file.unlink(missing_ok=True)
        tenant = answer.get("tenant") if isinstance(answer.get("tenant"), str) and answer.get("tenant") else None
        _private(self.identity, json.dumps({"component": component, "control_token": control,
                                            "enrolled_with": hashlib.sha256(token.encode("utf-8")).hexdigest()
                                            if token else None, "tenant": tenant}).encode("utf-8"))
        loaded = self.load()
        assert loaded is not None
        return loaded
