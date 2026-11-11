"""A site's certificate, bound to its request and enforced (BB-07 B3/B4, S-4 M2; FEAT-2026-11-05-knovas-brain-v1).

The pre-review: under DEC-0056 A1 the Platform issues no certificate at enrolment, so it recorded no fingerprint, and
``authenticate`` checked the terminator's ``X-Client-Cert-Fingerprint`` only when one was recorded. So
``BUILDER_CONNECTOR_REQUIRE_CLIENT_CERT=true`` protected no Knovas Link: its control token alone gave it configuration,
package and commands. And nothing tied the request a site prints to the site or the tenant.

Now: enrolment records the SHA-256 of the request's public key; until a Knovas admin records the certificate signed
from that request on the site page, a Link's control token opens its heartbeat only (no command delivered); the Builder
records only a certificate for that key and this tenant (and, where configured, of the client CA); then every call must
carry that certificate's fingerprint.
"""
import datetime as dt
import hashlib
import io
import zipfile

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from builder import protocol
from builder_harness import FakeEngine, MemorySecrets, MemoryWorkspaceStore, ProtocolClient, StandInUser, make_app

TENANT = "00000000-0000-4000-8000-0000000000b3"
OTHER = "00000000-0000-4000-8000-0000000000aa"
SPKI = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo


class Authority:
    """A made-up client CA that signs as Knovas's does: the request's key, the tenant as its one OU, clientAuth."""

    def __init__(self, name="Test Client CA"):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        now = dt.datetime.now(dt.timezone.utc)
        self.cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                     .public_key(self.key.public_key()).serial_number(1)
                     .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
                     .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                     .sign(self.key, hashes.SHA256()))
        self.pem = self.cert.public_bytes(serialization.Encoding.PEM).decode()

    def sign(self, csr_pem, *, tenant=TENANT, usages=(ExtendedKeyUsageOID.CLIENT_AUTH,), days=30):
        csr = x509.load_pem_x509_csr(csr_pem.encode())
        now = dt.datetime.now(dt.timezone.utc)
        subject = x509.Name(list(csr.subject) + [x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME,
                                                                    f"Semantix-Customer-{tenant}")])
        b = (x509.CertificateBuilder().subject_name(subject).issuer_name(self.cert.subject).public_key(csr.public_key())
             .serial_number(0x5173).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=days)))
        if usages:
            b = b.add_extension(x509.ExtendedKeyUsage(list(usages)), critical=True)
        return b.sign(self.key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()


CA = Authority()


def _request():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (x509.CertificateSigningRequestBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
                                                                                               "knovas-link")]))
           .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode())
    return csr, hashlib.sha256(key.public_key().public_bytes(*SPKI)).hexdigest()


def _world(*, require=True, client_ca=CA.pem, issue=False):
    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=MemorySecrets(),
                   require_certificate=require, issue_certificates=issue, client_ca=client_ca)
    http = app.test_client()
    site = http.post("/builder/api/sites", json={"name": "Büro Bern", "options": ["github"]}).get_json()
    env = zipfile.ZipFile(io.BytesIO(http.post(f"/builder/api/sites/{site['id']}/package", json={}).data)) \
        .read("knovas-link.env").decode()
    token = next(line.split("=", 1)[1] for line in env.splitlines() if line.startswith("LINK_ENROL_TOKEN="))
    return {"http": http, "store": store, "site": site, "token": token, "gate": app.extensions["builder_gate"],
            "admin": admin}


@pytest.fixture
def world():
    return _world()


def _enrolled(world):
    csr, fingerprint = _request()
    link = ProtocolClient(world["http"])
    answer = link.enrol(world["token"], version="1.1.0", capabilities=["work_channel"], csr=csr)
    assert answer.status_code == 201, answer.get_json()
    return link, csr, fingerprint, answer.get_json()


def _record(world, pem):
    return world["http"].post(f"/builder/api/sites/{world['site']['id']}/certificate", json={"certificate": pem})


def _link_row(world, link):
    return world["store"].component(TENANT, link.component, any_state=True)


class TestTheRequestIsRecorded:
    def test_enrolment_records_the_request_s_fingerprint_and_the_page_shows_it(self, world):
        link, _, fingerprint, answer = _enrolled(world)
        assert _link_row(world, link)["request_fingerprint"] == fingerprint
        assert protocol.request_fingerprint(_request()[0]) != fingerprint
        page = world["http"].get(world["site"]["url"]).get_data(as_text=True)
        assert f'data-site-request-fingerprint>{fingerprint}<' in page and "sha256sum" in page
        # The answer names the tenant and the client CA: Link installs only what that CA signed for that tenant.
        assert answer["tenant"] == TENANT and answer["client_ca"] == CA.pem and answer["certificate"] is None

    def test_a_link_without_a_valid_request_does_not_enrol_and_keeps_its_token(self, world):
        link = ProtocolClient(world["http"])
        refused = link.enrol(world["token"])                               # the harness's stand-in, not a request
        assert refused.status_code == 400 and refused.get_json()["error"] == "csr"
        assert link.enrol(world["token"], csr=_request()[0]).status_code == 201

    def test_renewing_the_token_forgets_both_fingerprints(self, world):
        link, csr, _, _ = _enrolled(world)
        assert _record(world, CA.sign(csr)).status_code == 200
        assert world["http"].post(f"/builder/api/sites/{world['site']['id']}/rotate").status_code == 200
        row = _link_row(world, link)
        assert row["request_fingerprint"] is None and row["certificate_fingerprint"] is None


class TestTheReproduction:
    def test_without_a_recorded_certificate_the_control_token_opens_the_heartbeat_only(self, world):
        """Before: with BUILDER_CONNECTOR_REQUIRE_CLIENT_CERT=true and no fingerprint, every call was taken."""
        link, _, _, _ = _enrolled(world)
        world["http"].post(f"/builder/api/sites/{world['site']['id']}/probe")          # a command waits for Link
        beat = link.heartbeat(site={"certificate": "waiting", "work_channel": "down"})
        assert beat.status_code == 200 and beat.get_json()["commands"] == []
        assert beat.get_json()["certificate"] == "not_recorded"
        for answer in (link.config(), link._send("GET", "/package"), link.report_run(kind="dry_run", status="ok"),
                       link.events({"code": "x.y", "counts": {}})):
            assert answer.status_code == 403 and answer.get_json()["error"] == "certificate_not_recorded"
        assert _link_row(world, link)["telemetry"]["certificate"] == "waiting", "its telemetry is kept"

    def test_once_recorded_every_call_needs_that_certificate(self, world):
        link, csr, _, _ = _enrolled(world)
        world["http"].post(f"/builder/api/sites/{world['site']['id']}/probe")
        signed = CA.sign(csr)
        recorded = _record(world, signed)
        assert recorded.status_code == 200, recorded.get_json()
        assert recorded.get_json()["fingerprint"] == protocol.certificate_fingerprint(signed)
        assert _link_row(world, link)["certificate_fingerprint"] == protocol.certificate_fingerprint(signed)
        assert "certificate.recorded" in [e["code"] for e in world["store"].events(link.component)]
        # Without the terminator's header, or with another certificate's, nothing is taken.
        assert link.config().status_code == 401 and link.heartbeat().status_code == 401
        link.certificate = CA.sign(_request()[0])
        assert link.config().get_json()["error"] == "bad_certificate"
        # With the recorded one: everything, and the command that waited.
        link.certificate = signed
        assert link.config().status_code == 200
        assert [c["command"] for c in link.heartbeat().get_json()["commands"]] == ["probe"]

    def test_a_deployment_without_a_terminator_says_so_and_keeps_the_token_alone(self):
        world = _world(require=False)
        link, _, _, _ = _enrolled(world)
        assert link.config().status_code == 200


class TestWhatIsRecorded:
    @pytest.mark.parametrize("case,code", [
        ("another_request", "wrong_key"),
        ("another_tenant", "wrong_tenant"),
        ("another_ca", "wrong_issuer"),
        ("not_client_auth", "not_client_auth"),
        ("expired", "not_valid_now"),
        ("not_pem", "unreadable"),
    ])
    def test_a_certificate_not_for_this_site_is_refused_and_nothing_recorded(self, world, case, code):
        link, csr, _, _ = _enrolled(world)
        pem = {"another_request": lambda: CA.sign(_request()[0]),
               "another_tenant": lambda: CA.sign(csr, tenant=OTHER),
               "another_ca": lambda: Authority().sign(csr),
               "not_client_auth": lambda: CA.sign(csr, usages=(ExtendedKeyUsageOID.SERVER_AUTH,)),
               "expired": lambda: CA.sign(csr, days=-1),
               "not_pem": lambda: "kein Zertifikat"}[case]()
        refused = _record(world, pem)
        assert refused.status_code in (400, 409) and refused.get_json()["success"] is False
        assert _link_row(world, link)["certificate_fingerprint"] is None
        assert world["store"].events(link.component)[0]["code"] != "certificate.recorded"
        assert refused.get_json()["code"] == code and refused.get_json()["error"]

    def test_without_a_configured_client_ca_the_key_and_the_tenant_still_bind_it(self):
        world = _world(client_ca=None)
        link, csr, _, _ = _enrolled(world)
        assert _record(world, CA.sign(csr, tenant=OTHER)).status_code == 409
        assert _record(world, Authority().sign(csr)).status_code == 200      # Link's own check refuses the issuer

    def test_only_who_manages_the_sites_records(self, world):
        _, csr, _, _ = _enrolled(world)
        world["gate"].user = StandInUser({"builder_author", "builder_publisher"}, "Theo Autor")
        assert _record(world, CA.sign(csr)).status_code == 403


class TestThePages:
    def test_link_s_page_no_longer_calls_it_a_test_setup(self, world):
        link, _, _, _ = _enrolled(world)
        page = world["http"].get(f"/builder/komponenten/{link.component}").get_data(as_text=True)
        assert "data-cp-shared-certificate" not in page and "nur zum Testen" not in page.lower()
        assert "data-cp-link-certificate" in page and "Zertifikat noch nicht erfasst" in page

    def test_the_checklist_ticks_the_certificate_only_once_recorded(self, world):
        link, csr, _, _ = _enrolled(world)
        link.heartbeat(site={"certificate": "present", "work_channel": "connected"})
        page = world["http"].get(world["site"]["url"]).get_data(as_text=True)
        assert 'data-check="certificate" data-ok="false"' in page and "Zertifikat erfassen" in page
        signed = CA.sign(csr)
        assert _record(world, signed).status_code == 200
        link.certificate = signed
        assert link.heartbeat(site={"certificate": "present", "work_channel": "connected"}).status_code == 200
        page = world["http"].get(world["site"]["url"]).get_data(as_text=True)
        assert 'data-check="certificate" data-ok="true"' in page
        assert f"data-site-certificate-fingerprint>{protocol.certificate_fingerprint(signed)}<" in page
