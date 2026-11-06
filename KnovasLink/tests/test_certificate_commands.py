"""The site's own certificate on DEC-0056's interim path (option A, A1; MVP-15, MVP-23): ``certificate-request`` prints
the request made from the key enrolment kept, a Knovas admin confirms and signs it (DEC-0062), and ``install-certificate`` writes the
signed certificate as Link's own, only when it is for that key, valid now and issued for client authentication. A Link
waiting for its certificate picks it up at its next heartbeat and leaves ``waiting_for_certificate``.

The CA here is the tests' own; it signs as Knovas's client CA does, with the extended key usage clientAuth.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import stat

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from conftest import CA, eventually
from knovas_link import certificate, contracts
from knovas_link.__main__ import main
from knovas_link.identity import Store

CLIENT_AUTH = (ExtendedKeyUsageOID.CLIENT_AUTH,)
SPKI = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo


def sign(csr_pem: str, *, usages=CLIENT_AUTH, starts: float = -1, ends: float = 30, serial: int = 0x5A17E) -> str:
    """The test CA signs as the client CA does: the request's key and common name, the tenant added as an OU."""
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    assert csr.is_signature_valid
    now = dt.datetime.now(dt.timezone.utc)
    subject = x509.Name(list(csr.subject) + [x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME,
                                                                "Customer-00000000-0000-4000-8000-000000000000")])
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(CA.cert.subject).public_key(csr.public_key())
               .serial_number(serial).not_valid_before(now + dt.timedelta(days=starts))
               .not_valid_after(now + dt.timedelta(days=ends))
               .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True))
    if usages is not None:
        builder = builder.add_extension(x509.ExtendedKeyUsage(list(usages)), critical=True)
    return builder.sign(CA.key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()


def run(world, *argv, stdin: bytes = b""):
    out, err = io.StringIO(), io.StringIO()
    status = certificate.run(list(argv), environ={"LINK_DATA_DIR": str(world.data_dir)}, stdin=io.BytesIO(stdin),
                             out=out, err=err)
    text = out.getvalue() + err.getvalue()
    assert "PRIVATE KEY" not in text, "the key is never printed"
    return status, out.getvalue(), err.getvalue()


def stored_key(world):
    return Store(world.data_dir / "identity").private_key()


@pytest.fixture
def waiting(world):
    """A Link that enrolled without a certificate (production: secure-api cannot sign) and stopped."""
    world.platform.issue_certificate = False
    with world.running(loops=()) as (_, link):
        assert link.certificate == "waiting"
    return world


def crt(world):
    return world.data_dir / "identity" / "component.crt"


# ------------------------------------------------------------------ certificate-request

def test_the_request_is_made_from_the_stored_key_and_round_trips_through_a_test_ca(waiting):
    status, out, err = run(waiting, "certificate-request")
    assert status == 0 and err == ""
    csr = x509.load_pem_x509_csr(out.encode())
    assert out.startswith("-----BEGIN CERTIFICATE REQUEST-----") and csr.is_signature_valid
    assert csr.public_key().public_bytes(*SPKI) == stored_key(waiting).public_key().public_bytes(*SPKI)
    assert [(a.oid, a.value) for a in csr.subject] == [(NameOID.COMMON_NAME, "knovas-link")], \
        "the subject names no tenant: the CA adds it from customer_id"
    status, out, _ = run(waiting, "install-certificate", stdin=sign(out).encode())
    assert status == 0 and "certificate.installed" in out
    leaf = x509.load_pem_x509_certificate(crt(waiting).read_bytes())
    assert leaf.public_key().public_bytes(*SPKI) == stored_key(waiting).public_key().public_bytes(*SPKI)


def test_asking_again_gives_a_request_for_the_same_key(waiting):
    first, second = run(waiting, "certificate-request")[1], run(waiting, "certificate-request")[1]
    keys = {x509.load_pem_x509_csr(p.encode()).public_key().public_bytes(*SPKI) for p in (first, second)}
    assert len(keys) == 1, "the key does not change"


def test_without_an_enrolment_there_is_no_request(world):
    status, out, err = run(world, "certificate-request")
    assert status == 1 and out == ""
    lines = err.strip().splitlines()
    assert len(lines) == 2 and all(line.startswith("certificate.not_enrolled: ") for line in lines)
    assert "has not enrolled" in lines[0] and "nicht angemeldet" in lines[1], "English, then German"


# ------------------------------------------------------------------ install-certificate refuses and writes nothing

def _other_key_certificate() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "knovas-link")])).sign(key, hashes.SHA256())
    return sign(csr.public_bytes(serialization.Encoding.PEM).decode())


@pytest.mark.parametrize("case,code", [
    ("wrong_key", "certificate.wrong_key"),
    ("expired", "certificate.not_valid_now"),
    ("not_yet_valid", "certificate.not_valid_now"),
    ("no_usage", "certificate.not_client_auth"),
    ("server_only", "certificate.not_client_auth"),
    ("not_a_certificate", "certificate.unreadable"),
    ("a_request", "certificate.unreadable"),
    ("empty", "certificate.unreadable"),
])
def test_a_certificate_link_cannot_use_is_refused_and_nothing_is_written(waiting, case, code):
    request = run(waiting, "certificate-request")[1]
    given = {
        "wrong_key": lambda: _other_key_certificate(),
        "expired": lambda: sign(request, starts=-30, ends=-1),
        "not_yet_valid": lambda: sign(request, starts=1, ends=30),
        "no_usage": lambda: sign(request, usages=None),
        "server_only": lambda: sign(request, usages=(ExtendedKeyUsageOID.SERVER_AUTH,)),
        "not_a_certificate": lambda: "-----BEGIN CERTIFICATE-----\nbm90IGEgY2VydGlmaWNhdGU=\n-----END CERTIFICATE-----\n",
        "a_request": lambda: request,
        "empty": lambda: "",
    }[case]()
    status, out, err = run(waiting, "install-certificate", "-", stdin=given.encode())
    assert status == 1 and out == ""
    assert [line.split(":", 1)[0] for line in err.strip().splitlines()] == [code, code]
    assert not crt(waiting).exists(), "nothing written"
    assert not list((waiting.data_dir / "identity").glob("*.tmp"))
    assert code in contracts.codes()


def test_a_refused_certificate_leaves_the_one_installed_before(waiting):
    good = sign(run(waiting, "certificate-request")[1])
    assert run(waiting, "install-certificate", stdin=good.encode())[0] == 0
    before = crt(waiting).read_bytes()
    assert run(waiting, "install-certificate", stdin=_other_key_certificate().encode())[0] == 1
    assert crt(waiting).read_bytes() == before


def test_installing_before_enrolment_is_refused(world):
    status, _, err = run(world, "install-certificate", stdin=_other_key_certificate().encode())
    assert status == 1 and err.startswith("certificate.not_enrolled: ")


# ------------------------------------------------------------------ install-certificate writes the certificate

def test_the_certificate_is_written_private_with_its_chain_from_a_file_or_standard_input(waiting, tmp_path):
    leaf = sign(run(waiting, "certificate-request")[1], serial=0xC0FFEE)
    given = tmp_path / "site.crt"
    given.write_bytes((leaf + CA.pem).replace("\n", "\r\n").encode())       # as saved on a Windows machine
    status, out, err = run(waiting, "install-certificate", str(given))
    assert (status, err) == (0, "")
    assert stat.S_IMODE(crt(waiting).stat().st_mode) == 0o600, "readable by Link's user only, as identity.py writes"
    written = crt(waiting).read_bytes()
    assert b"\r" not in written and written.count(b"-----BEGIN CERTIFICATE-----") == 2
    assert x509.load_pem_x509_certificate(written).serial_number == 0xC0FFEE
    assert f"serial {0xC0FFEE}" in out and f"Seriennummer {0xC0FFEE}" in out, "the serial it is revoked by"
    assert "no restart is needed" in out and "Neustart ist nicht nötig" in out
    assert Store(waiting.data_dir / "identity").load().certificate == crt(waiting)


def test_replacing_a_certificate_says_that_link_takes_it_at_its_next_start(waiting):
    request = run(waiting, "certificate-request")[1]
    assert run(waiting, "install-certificate", stdin=sign(request).encode())[0] == 0
    status, out, _ = run(waiting, "install-certificate", stdin=sign(request, serial=0xBEEF).encode())
    assert status == 0 and "docker compose restart knovas-link" in out
    assert x509.load_pem_x509_certificate(crt(waiting).read_bytes()).serial_number == 0xBEEF


def test_a_running_link_leaves_waiting_for_certificate_at_its_next_heartbeat(world, caplog):
    caplog.set_level(logging.INFO)
    world.platform.issue_certificate = False
    with world.running(loops=("channel",)) as (client, link):
        assert link.certificate == "waiting" and link.certificate_mode is None
        relayed = client.get("/local/v1/brain/secured/graph/nodes", headers=world.headers())
        assert (relayed.status_code, relayed.json()["error"]) == (503, "waiting_for_certificate")
        request = run(world, "certificate-request")[1]
        assert run(world, "install-certificate", stdin=sign(request).encode())[0] == 0
        assert client.portal.call(link.beat) is True
        assert link.certificate_mode == "issued" and link.certificate == "present"
        assert world.platform.heartbeats[-1]["site"]["certificate"] == "present"
        assert client.get("/local/v1/ping", headers=world.headers()).json()["certificate"] == "present"
        assert link.credentials.certificate == crt(world) and link.platform.credentials.certificate == crt(world)
        eventually(lambda: world.secure.claims)
        assert client.get("/local/v1/brain/secured/graph/nodes", headers=world.headers()).status_code == 200
        assert client.portal.call(link.pick_up_certificate) is False, "picked up once"
    assert "certificate.picked_up" in caplog.text
    for secret in ("BEGIN", "PRIVATE"):
        assert secret not in caplog.text


def test_after_a_restart_link_uses_the_installed_certificate_without_enrolling_again(waiting):
    assert run(waiting, "install-certificate", stdin=sign(run(waiting, "certificate-request")[1]).encode())[0] == 0
    with waiting.running(loops=()) as (_, link):
        assert link.certificate_mode == "issued"
    assert len(waiting.platform.enrolments) == 1


def test_a_link_that_uses_its_certificate_does_not_swap_it_while_running(world):
    with world.running(loops=()) as (client, link):
        assert link.certificate_mode == "issued"
        assert run(world, "install-certificate", stdin=sign(run(world, "certificate-request")[1]).encode())[0] == 0
        assert client.portal.call(link.pick_up_certificate) is False


# ------------------------------------------------------------------ the command line

def test_python_m_knovas_link_dispatches_the_commands(waiting, monkeypatch, capsys):
    monkeypatch.setenv("LINK_DATA_DIR", str(waiting.data_dir))
    assert main(["certificate-request"]) == 0
    assert capsys.readouterr().out.startswith("-----BEGIN CERTIFICATE REQUEST-----")
    assert main(["install-certificate", "a", "b"]) == 2
    assert main(["sign-it-yourself"]) == 2
    assert "usage:" in capsys.readouterr().err
