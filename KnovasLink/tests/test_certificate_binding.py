"""The site's certificate is bound to the site's request, Knovas's client CA and the site's tenant (S-4 M2, BB-07 B3/B4;
FEAT-2026-11-05-knovas-brain-v1).

The S-4 pre-review: ``install-certificate`` checked the key, the validity and clientAuth, but not that the issuer is
Knovas's client CA or that the certificate's OU ``Semantix-Customer-<id>`` is the site's tenant, and Link did not know
its tenant. An admin who typed another client's id would have made the site write one company's knowledge into another
company's Brain, and Link would have installed it without complaint.

Now the Platform's enrolment answer names the tenant and the client CA (``LINK_TENANT`` and ``LINK_CLIENT_CA`` where it
does not), and ``install-certificate`` refuses another issuer or tenant, and installs nothing while it knows neither.
``certificate-fingerprint`` prints what the Builder recorded at enrolment, for the admin to compare before signing.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

import conftest
from conftest import CA, TENANT
from knovas_link import certificate, contracts
from knovas_link.identity import Store

OTHER_TENANT = "11111111-1111-4111-8111-111111111111"


def _sign(csr_pem: str, *, authority=CA, ous=(f"Semantix-Customer-{TENANT}",)) -> str:
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    now = dt.datetime.now(dt.timezone.utc)
    subject = x509.Name(list(csr.subject) + [x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, ou) for ou in ous])
    return (x509.CertificateBuilder().subject_name(subject).issuer_name(authority.cert.subject)
            .public_key(csr.public_key()).serial_number(0xB1D).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=True)
            .sign(authority.key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode())


def _run(world, *argv, stdin: bytes = b"", **environ):
    out, err = io.StringIO(), io.StringIO()
    status = certificate.run(list(argv), environ={"LINK_DATA_DIR": str(world.data_dir), **environ},
                             stdin=io.BytesIO(stdin), out=out, err=err)
    return status, out.getvalue(), err.getvalue()


def _enrolled(world, **answer):
    """A Link that enrolled without a certificate (DEC-0056 A1); ``answer`` changes the Platform's enrolment answer."""
    world.platform.issue_certificate = False
    world.platform.enrol_answer = dict(answer)
    with world.running(loops=()) as (_, link):
        assert link.certificate == "waiting"
    return world


def _crt(world):
    return world.data_dir / "identity" / "component.crt"


@pytest.fixture
def waiting(world):
    return _enrolled(world)


def _refused(world, given: str, code: str, **environ):
    status, out, err = _run(world, "install-certificate", stdin=given.encode(), **environ)
    assert status == 1 and out == ""
    assert [line.split(":", 1)[0] for line in err.strip().splitlines()] == [code, code]
    assert not _crt(world).exists(), "nothing written"
    assert code in contracts.codes()


class TestTheEnrolmentAnswerIsKept:
    def test_link_keeps_the_tenant_and_the_client_ca_the_platform_named(self, waiting):
        store = Store(waiting.data_dir / "identity")
        assert store.tenant() == TENANT and store.client_ca() == CA.pem.encode()
        assert oct(store.client_ca_file.stat().st_mode & 0o777) == "0o600"

    def test_a_certificate_of_the_client_ca_for_the_site_s_tenant_installs(self, waiting):
        request = _run(waiting, "certificate-request")[1]
        status, out, _ = _run(waiting, "install-certificate", stdin=_sign(request).encode())
        assert status == 0 and "certificate.installed" in out


class TestTheReproduction:
    def test_a_certificate_for_another_tenant_is_refused(self, waiting):
        """The S-4 pre-review's second scenario: the admin typed another client's id."""
        request = _run(waiting, "certificate-request")[1]
        _refused(waiting, _sign(request, ous=(f"Semantix-Customer-{OTHER_TENANT}",)), "certificate.wrong_tenant")

    def test_a_certificate_of_another_ca_is_refused(self, waiting):
        request = _run(waiting, "certificate-request")[1]
        _refused(waiting, _sign(request, authority=conftest.TestCA()), "certificate.wrong_issuer")

    def test_a_ca_with_the_same_name_but_another_key_is_refused(self, waiting):
        impostor = conftest.TestCA()                    # same subject "Test Brain CA", its own key
        assert impostor.cert.subject == CA.cert.subject
        request = _run(waiting, "certificate-request")[1]
        _refused(waiting, _sign(request, authority=impostor), "certificate.wrong_issuer")

    @pytest.mark.parametrize("ous", [(), (f"Semantix-Customer-{TENANT}", f"Semantix-Customer-{OTHER_TENANT}"),
                                     (f"Customer-{TENANT}",), (f"Semantix-Customer-{TENANT}x",)])
    def test_a_certificate_that_names_no_single_tenant_is_refused(self, waiting, ous):
        request = _run(waiting, "certificate-request")[1]
        _refused(waiting, _sign(request, ous=ous), "certificate.wrong_tenant")


class TestWithoutTheSiteSConfiguration:
    def test_without_a_client_ca_nothing_is_installed(self, world):
        _enrolled(world, client_ca=None)
        request = _run(world, "certificate-request")[1]
        _refused(world, _sign(request), "certificate.no_client_ca")

    def test_without_a_tenant_nothing_is_installed(self, world):
        _enrolled(world, tenant=None)
        request = _run(world, "certificate-request")[1]
        _refused(world, _sign(request), "certificate.no_tenant")

    def test_the_site_s_settings_name_them_when_the_platform_did_not(self, world, tmp_path):
        _enrolled(world, tenant=None, client_ca=None)
        ca_file = tmp_path / "client-ca.pem"
        ca_file.write_text(CA.pem)
        request = _run(world, "certificate-request")[1]
        status, out, _ = _run(world, "install-certificate", stdin=_sign(request).encode(), LINK_TENANT=TENANT,
                              LINK_CLIENT_CA=str(ca_file))
        assert status == 0 and "certificate.installed" in out

    def test_the_site_s_tenant_setting_wins(self, waiting):
        request = _run(waiting, "certificate-request")[1]
        _refused(waiting, _sign(request), "certificate.wrong_tenant", LINK_TENANT=OTHER_TENANT)


class TestTheFingerprint:
    def test_it_is_the_sha256_of_the_request_s_public_key(self, waiting):
        status, out, err = _run(waiting, "certificate-fingerprint")
        assert status == 0 and err == ""
        request = x509.load_pem_x509_csr(_run(waiting, "certificate-request")[1].encode())
        spki = request.public_key().public_bytes(serialization.Encoding.DER,
                                                 serialization.PublicFormat.SubjectPublicKeyInfo)
        assert out.strip() == hashlib.sha256(spki).hexdigest()
        assert "PRIVATE" not in out

    def test_without_an_enrolment_there_is_none(self, world):
        status, out, err = _run(world, "certificate-fingerprint")
        assert status == 1 and out == "" and err.startswith("certificate.not_enrolled: ")

    def test_another_key_has_another(self, waiting):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        assert _run(waiting, "certificate-fingerprint")[1].strip() != hashlib.sha256(other).hexdigest()
