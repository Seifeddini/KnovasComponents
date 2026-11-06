"""The site's own certificate on the interim path DEC-0056 decided (option A, A1): two commands, run in Link's container.

    docker compose exec -T knovas-link python -m knovas_link certificate-request > site.csr
    docker compose exec -T knovas-link python -m knovas_link install-certificate < site.crt

``certificate-request`` prints the certificate request (PEM) made from the key enrolment made and kept; it never
prints the key, which never leaves the data volume. Knovas's client CA signs it for the site's tenant: one Knovas
admin confirms and signs today through internal-api's ``POST /sign_certificate``; later the issuance job signs with the
HSM-held key (DEC-0062). ``install-certificate`` reads the signed certificate (PEM, optionally followed by its chain) from
a file or standard input and writes it as Link's ``component.crt`` only when it is for the stored key, valid now and
issued for client authentication; otherwise it writes nothing and exits 1 with a code from ``contracts/codes.json``.

A Link that waits for its certificate picks it up at its next heartbeat (``Link.pick_up_certificate``); a certificate
that replaces one Link already uses is taken at the next start (``docker compose restart knovas-link``).

Nothing here prints or logs a key, and a refusal names a code, never a value of the certificate.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, TextIO

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID

from knovas_link.identity import Store

REQUEST = "certificate-request"
INSTALL = "install-certificate"
COMMANDS = (REQUEST, INSTALL)
#: The commands as the site runs them; ``-T`` keeps docker compose from adding a terminal, which would turn the
#: request's line ends into CRLF and refuse a redirected standard input.
EXEC = "docker compose exec -T knovas-link python -m knovas_link"
#: A certificate and its chain are a few kilobytes; more is not a certificate.
MAX_INPUT_BYTES = 256 * 1024
_BEGIN, _END = b"-----BEGIN CERTIFICATE-----", b"-----END CERTIFICATE-----"

#: The refusals, each a code of contracts/codes.json with an English and a German line for the person at the site.
NOT_ENROLLED = "certificate.not_enrolled"
UNREADABLE = "certificate.unreadable"
WRONG_KEY = "certificate.wrong_key"
NOT_VALID_NOW = "certificate.not_valid_now"
NOT_CLIENT_AUTH = "certificate.not_client_auth"
HINTS: Mapping[str, tuple[str, str]] = {
    NOT_ENROLLED: (
        "Knovas Link has not enrolled yet, so it has no key. Start it with the install package's one-time token "
        "(docker compose up -d), then ask again.",
        "Knovas Link ist noch nicht angemeldet und hat darum keinen Schlüssel. Mit dem Einmal-Token aus dem "
        "Installationspaket starten (docker compose up -d), dann erneut beantragen."),
    UNREADABLE: (
        "No PEM certificate was given. Pass the certificate Knovas signed (-----BEGIN CERTIFICATE-----).",
        "Kein Zertifikat im PEM-Format erhalten. Das von Knovas signierte Zertifikat übergeben "
        "(-----BEGIN CERTIFICATE-----)."),
    WRONG_KEY: (
        "This certificate is not for this Knovas Link's key: sign the request from certificate-request of this "
        "Link (after «Token erneuern» Link has a new key, and the request must be made again). Nothing was written.",
        "Dieses Zertifikat gehört nicht zum Schlüssel dieses Knovas Link: den Antrag aus «certificate-request» "
        "dieses Link signieren lassen (nach «Token erneuern» hat Link einen neuen Schlüssel, der Antrag ist neu zu "
        "stellen). Es wurde nichts geschrieben."),
    NOT_VALID_NOW: (
        "This certificate has expired or is not valid yet: check the clock of this machine, else have the request "
        "signed again. Nothing was written.",
        "Dieses Zertifikat ist abgelaufen oder noch nicht gültig: die Uhr dieses Rechners prüfen, sonst den Antrag "
        "neu signieren lassen. Es wurde nichts geschrieben."),
    NOT_CLIENT_AUTH: (
        "This certificate is not issued for client authentication (extended key usage clientAuth): have the request "
        "signed by Knovas's client CA. Nothing was written.",
        "Dieses Zertifikat ist nicht für die Anmeldung als Client ausgestellt (erweiterte Schlüsselverwendung "
        "clientAuth): den Antrag von der Client-CA von Knovas signieren lassen. Es wurde nichts geschrieben."),
}


class Refused(Exception):
    """A certificate Link does not take; ``code`` is one of ``HINTS``."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Installed:
    serial: int
    not_after: dt.datetime
    replaced: bool                 # a certificate was there before: the running Link keeps it until it restarts


def store_of(environ: Mapping[str, str]) -> Store:
    """Link's identity folder, as the running Link finds it (``LINK_DATA_DIR``, else ``/data``)."""
    return Store(Path(environ.get("LINK_DATA_DIR") or "/data") / "identity")


def request(store: Store, name: str) -> str:
    """The certificate request from the stored key (PEM)."""
    csr = store.certificate_request(name)
    if csr is None:
        raise Refused(NOT_ENROLLED)
    return csr


def _certificates(data: bytes) -> list[x509.Certificate]:
    """Every certificate in the PEM text, the leaf first; anything else between them is ignored."""
    data = data.replace(b"\r\n", b"\n")
    out, at = [], 0
    while True:
        start = data.find(_BEGIN, at)
        if start < 0:
            break
        end = data.find(_END, start)
        if end < 0:
            raise Refused(UNREADABLE)
        at = end + len(_END)
        try:
            out.append(x509.load_pem_x509_certificate(data[start:at] + b"\n"))
        except ValueError:
            raise Refused(UNREADABLE) from None
    if not out:
        raise Refused(UNREADABLE)
    return out


def check(store: Store, data: bytes, now: Optional[dt.datetime] = None) -> list[x509.Certificate]:
    """The certificates in ``data``, the leaf first, when the leaf is for the stored key, valid at ``now`` and issued
    for client authentication; else ``Refused``."""
    key = store.private_key()
    if key is None:
        raise Refused(NOT_ENROLLED)
    if len(data) > MAX_INPUT_BYTES:
        raise Refused(UNREADABLE)
    chain = _certificates(data)
    leaf = chain[0]
    spki = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    if leaf.public_key().public_bytes(*spki) != key.public_key().public_bytes(*spki):
        raise Refused(WRONG_KEY)
    now = now or dt.datetime.now(dt.timezone.utc)
    if not leaf.not_valid_before_utc <= now <= leaf.not_valid_after_utc:
        raise Refused(NOT_VALID_NOW)
    try:
        usages = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound:
        raise Refused(NOT_CLIENT_AUTH) from None
    if ExtendedKeyUsageOID.CLIENT_AUTH not in usages:
        raise Refused(NOT_CLIENT_AUTH)
    return chain


def install(store: Store, data: bytes, now: Optional[dt.datetime] = None) -> Installed:
    """Check the certificate and write it, with its chain, as Link's ``component.crt``; nothing is written on a
    refusal."""
    chain = check(store, data, now)
    replaced = store.certificate.exists()
    store.install_certificate(b"".join(c.public_bytes(serialization.Encoding.PEM) for c in chain))
    return Installed(chain[0].serial_number, chain[0].not_valid_after_utc, replaced)


# ------------------------------------------------------------------ the commands

def _refuse(code: str, err: TextIO) -> int:
    english, german = HINTS[code]
    print(f"{code}: {english}", file=err)
    print(f"{code}: {german}", file=err)
    return 1


def _failed(english: str, german: str, exc: Exception, err: TextIO) -> int:
    """Not the certificate's fault (a file Link's user cannot read or write): the error's type, never its text."""
    print(f"{english} ({type(exc).__name__}). Run the command as Link's own user, without -u.", file=err)
    print(f"{german} ({type(exc).__name__}). Den Befehl als Benutzer von Knovas Link ausführen, ohne -u.", file=err)
    return 1


def _usage(err: TextIO) -> int:
    print(f"usage: {EXEC} {REQUEST} > site.csr", file=err)
    print(f"       {EXEC} {INSTALL} [FILE|-] < site.crt", file=err)
    return 2


def run(argv: list[str], *, environ: Mapping[str, str] = os.environ, stdin=None, out: Optional[TextIO] = None,
        err: Optional[TextIO] = None) -> int:
    """``certificate-request`` or ``install-certificate [FILE|-]``: 0 done, 1 refused (a code), 2 not understood."""
    out, err = out or sys.stdout, err or sys.stderr
    store = store_of(environ)
    if argv[:1] == [REQUEST] and len(argv) == 1:
        try:
            out.write(request(store, (environ.get("LINK_NAME") or "knovas-link")[:64]))
        except Refused as exc:
            return _refuse(exc.code, err)
        except (OSError, ValueError) as exc:
            return _failed("Link's key could not be read", "Der Schlüssel von Knovas Link ist nicht lesbar", exc, err)
        out.flush()
        return 0
    if argv[:1] == [INSTALL] and len(argv) <= 2:
        source = argv[1] if len(argv) == 2 else "-"
        try:
            if source == "-":
                data = (stdin or sys.stdin.buffer).read(MAX_INPUT_BYTES + 1)
            else:
                with open(source, "rb") as handle:
                    data = handle.read(MAX_INPUT_BYTES + 1)
        except OSError:
            return _refuse(UNREADABLE, err)
        try:
            done = install(store, data)
        except Refused as exc:
            return _refuse(exc.code, err)
        except (OSError, ValueError) as exc:
            return _failed("Nothing was installed", "Es wurde nichts installiert", exc, err)
        print(f"certificate.installed: serial {done.serial}, valid until {done.not_after:%Y-%m-%d %H:%M} UTC. "
              "Revoked by this serial, the site stops.", file=out)
        print(f"certificate.installed: Seriennummer {done.serial}, gültig bis {done.not_after:%d.%m.%Y %H:%M} UTC. "
              "Mit dieser Seriennummer gesperrt, hält der Standort an.", file=out)
        if done.replaced:
            print("Knovas Link keeps using its earlier certificate until it restarts: "
                  "docker compose restart knovas-link", file=out)
            print("Knovas Link verwendet bis zum Neustart sein bisheriges Zertifikat: "
                  "docker compose restart knovas-link", file=out)
        else:
            print("Knovas Link picks it up at its next heartbeat (LINK_HEARTBEAT_SECONDS, 30 s by default); "
                  "no restart is needed.", file=out)
            print("Knovas Link übernimmt es beim nächsten Heartbeat (LINK_HEARTBEAT_SECONDS, standardmässig 30 s); "
                  "ein Neustart ist nicht nötig.", file=out)
        return 0
    return _usage(err)
