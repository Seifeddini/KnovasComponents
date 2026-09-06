"""A Platform-signed principal at RemoteController's door (KC-IN-1).

The Platform already signs each user into its Knovas calls. RemoteController
verifies the same token so the firm's own administrator can configure their
own ingestion - beside, not instead of, the Knovas-employee path.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: E402

from auth.platform_principal import (  # noqa: E402
    InvalidPrincipalError,
    ReplayGuard,
    derive_key_id,
    verify_platform_principal,
)

TENANT = "22222222-2222-2222-2222-222222222222"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@pytest.fixture
def keypair():
    private = ed25519.Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public_pem


def mint(private, public_pem, *, alg="EdDSA", typ="knovas-principal+jws", kid=None,
         sign=True, **payload_overrides):
    now = int(time.time())
    payload = {"sub": "user-1", "tid": TENANT, "grp": ["litigation"],
               "rol": ["ingestion_manager"], "iat": now, "exp": now + 120,
               "jti": f"j-{time.time_ns()}"}
    payload.update(payload_overrides)
    header = {"alg": alg, "typ": typ, "kid": kid or derive_key_id(public_pem)}
    signing_input = (f"{_b64(json.dumps(header, separators=(',', ':')).encode())}."
                     f"{_b64(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode())}")
    sig = private.sign(signing_input.encode("ascii")) if sign else b"\x00" * 64
    return f"{signing_input}.{_b64(sig)}"


def _raw_token(private, header_obj, payload_obj):
    """Build a token whose header/payload need not be JSON objects at all -
    `[]`, `"x"`, `null`, `7` all decode cleanly as JSON but are not dicts.
    Signed for real, so a test using this exercises the shape check itself
    rather than an incidental signature failure."""
    signing_input = (f"{_b64(json.dumps(header_obj).encode())}."
                     f"{_b64(json.dumps(payload_obj).encode())}")
    sig = private.sign(signing_input.encode("ascii"))
    return f"{signing_input}.{_b64(sig)}"


class TestVerify:
    def test_a_genuine_token_yields_the_principal(self, keypair):
        private, pub = keypair
        p = verify_platform_principal(mint(private, pub), public_pem=pub,
                                      expected_tenant=TENANT, replay=ReplayGuard())
        assert p.subject == "user-1" and p.roles == ("ingestion_manager",)
        assert p.groups == ("litigation",)

    @pytest.mark.parametrize("kw", [
        dict(sign=False),
        dict(alg="none"),
        dict(alg="HS256"),
        dict(typ="knovas-dual-control+jws"),
        dict(kid="bk-0000000000000000"),
        dict(tid="33333333-3333-3333-3333-333333333333"),
        dict(exp=int(time.time()) - 60),
        dict(iat=int(time.time()) - 400, exp=int(time.time()) + 100),
    ])
    def test_each_forgery_is_refused(self, keypair, kw):
        private, pub = keypair
        with pytest.raises(InvalidPrincipalError):
            verify_platform_principal(mint(private, pub, **kw), public_pem=pub,
                                      expected_tenant=TENANT, replay=ReplayGuard())

    @pytest.mark.parametrize("sub", [None, "", 7, [], {}])
    def test_a_token_that_names_no_subject_is_refused(self, keypair, sub):
        """P-I1: `sub` is the only thing RemoteController records about who
        acted. str(None) is "None" and str(7) is "7" -- both would have been
        accepted as a subject and logged as one."""
        private, pub = keypair
        with pytest.raises(InvalidPrincipalError):
            verify_platform_principal(mint(private, pub, sub=sub), public_pem=pub,
                                      expected_tenant=TENANT, replay=ReplayGuard())

    def test_a_token_cannot_be_presented_twice(self, keypair):
        private, pub = keypair
        token, replay = mint(private, pub), ReplayGuard()
        verify_platform_principal(token, public_pem=pub, expected_tenant=TENANT, replay=replay)
        with pytest.raises(InvalidPrincipalError):
            verify_platform_principal(token, public_pem=pub, expected_tenant=TENANT, replay=replay)

    def test_the_kid_matches_the_platforms_derivation(self, keypair):
        import hashlib
        _, pub = keypair
        assert derive_key_id(pub) == "bk-" + hashlib.sha256(pub).hexdigest()[:16]

    def test_a_non_dict_header_is_refused_not_crashed(self, keypair):
        private, pub = keypair
        token = _raw_token(private, header_obj=[], payload_obj={"sub": "user-1"})
        with pytest.raises(InvalidPrincipalError):
            verify_platform_principal(token, public_pem=pub, expected_tenant=TENANT,
                                      replay=ReplayGuard())

    def test_a_non_dict_payload_is_refused_not_crashed(self, keypair):
        private, pub = keypair
        header = {"alg": "EdDSA", "typ": "knovas-principal+jws", "kid": derive_key_id(pub)}
        token = _raw_token(private, header_obj=header, payload_obj="x")
        with pytest.raises(InvalidPrincipalError):
            verify_platform_principal(token, public_pem=pub, expected_tenant=TENANT,
                                      replay=ReplayGuard())


class TestTheGate:
    @pytest.fixture
    def configured(self, keypair, tmp_path, monkeypatch, tmp_watch_root):
        private, pub = keypair
        pem = tmp_path / "broker_ed25519.pub"
        pem.write_bytes(pub)
        monkeypatch.setenv("RC_PLATFORM_BROKER_PUBKEY_PATH", str(pem))
        monkeypatch.setenv("RC_CLIENT_ID", TENANT)
        monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "false")
        monkeypatch.setenv("RC_DISCOVER_LOCAL_BYPASS", "false")
        from config import reset_config, load_config
        reset_config()
        load_config(validate=False, force_reload=True)
        return private, pub

    def test_status_answers_a_signed_ingestion_manager(self, rc_client, configured):
        private, pub = configured
        r = rc_client.get("/sync/status", headers={"X-Platform-Principal": mint(private, pub)})
        assert r.status_code == 200

    def test_a_member_without_the_role_is_refused(self, rc_client, configured):
        private, pub = configured
        r = rc_client.get("/sync/status",
                          headers={"X-Platform-Principal": mint(private, pub, rol=["member"])})
        assert r.status_code == 403

    def test_a_bad_signature_is_refused(self, rc_client, configured):
        private, pub = configured
        r = rc_client.get("/sync/status",
                          headers={"X-Platform-Principal": mint(private, pub, sign=False)})
        assert r.status_code == 401

    def test_without_the_header_the_employee_path_still_applies(self, rc_client, configured):
        assert rc_client.get("/sync/status").status_code == 401

    def test_a_signed_admin_is_also_admitted(self, rc_client, configured):
        private, pub = configured
        r = rc_client.get("/sync/status",
                          headers={"X-Platform-Principal": mint(private, pub, rol=["admin"])})
        assert r.status_code == 200

    def test_a_non_dict_header_through_the_app_is_401_not_500(self, rc_client, configured):
        private, pub = configured
        token = _raw_token(private, header_obj=[], payload_obj={"sub": "user-1"})
        r = rc_client.get("/sync/status", headers={"X-Platform-Principal": token})
        assert r.status_code == 401

    def test_a_non_dict_payload_through_the_app_is_401_not_500(self, rc_client, configured):
        private, pub = configured
        header = {"alg": "EdDSA", "typ": "knovas-principal+jws", "kid": derive_key_id(pub)}
        token = _raw_token(private, header_obj=header, payload_obj="x")
        r = rc_client.get("/sync/status", headers={"X-Platform-Principal": token})
        assert r.status_code == 401

    def test_an_unreadable_pubkey_is_a_503_not_a_silent_401(self, rc_client, configured,
                                                            tmp_path, monkeypatch, caplog):
        """P-I4: an operator who mounted the wrong path got "Not authorized"
        in the console and an empty RemoteController log -- indistinguishable
        from a forgery. That is a misconfiguration, and it is RC's."""
        import logging

        monkeypatch.setenv("RC_PLATFORM_BROKER_PUBKEY_PATH", str(tmp_path / "gone.pub"))
        from config import load_config, reset_config
        reset_config()
        load_config(validate=False, force_reload=True)
        private, pub = configured
        with caplog.at_level(logging.ERROR):
            r = rc_client.get("/sync/status", headers={"X-Platform-Principal": mint(private, pub)})
        assert r.status_code == 503
        assert r.get_json()["error"] == "platform principal verification unavailable"
        assert "gone.pub" in caplog.text

    def test_a_pem_that_is_not_a_key_is_also_a_503(self, rc_client, configured,
                                                   tmp_path, monkeypatch, caplog):
        import logging

        broken = tmp_path / "broken.pub"
        broken.write_bytes(b"-----BEGIN PUBLIC KEY-----\nnot a key\n-----END PUBLIC KEY-----\n")
        monkeypatch.setenv("RC_PLATFORM_BROKER_PUBKEY_PATH", str(broken))
        from config import load_config, reset_config
        reset_config()
        load_config(validate=False, force_reload=True)
        private, pub = configured
        with caplog.at_level(logging.ERROR):
            r = rc_client.get("/sync/status", headers={"X-Platform-Principal": mint(private, pub)})
        assert r.status_code == 503

    def test_a_forged_token_stays_a_uniform_401(self, rc_client, configured):
        """The 503 must not become a way to tell refusals apart."""
        private, pub = configured
        r = rc_client.get("/sync/status",
                          headers={"X-Platform-Principal": mint(private, pub, sign=False)})
        assert r.status_code == 401
        assert r.get_json()["error"] == "Not authorized"

    def test_every_admitted_principal_leaves_a_log_line(self, rc_client, configured, caplog):
        """P-I5: RemoteController recorded nothing when a tenant principal
        rewrote its configuration. This is RC's end of the four-eyes chain."""
        import logging

        private, pub = configured
        with caplog.at_level(logging.INFO):
            r = rc_client.get("/sync/status",
                              headers={"X-Platform-Principal": mint(private, pub, sub="u-42",
                                                                    rol=["ingestion_manager", "admin"])})
        assert r.status_code == 200
        assert "platform principal u-42" in caplog.text
        assert "roles=['admin', 'ingestion_manager']" in caplog.text
        assert "GET /sync/status" in caplog.text

    @pytest.fixture
    def unconfigured(self, keypair, monkeypatch, tmp_watch_root):
        monkeypatch.setenv("RC_PLATFORM_BROKER_PUBKEY_PATH", "")
        monkeypatch.setenv("RC_CLIENT_ID", TENANT)
        monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "false")
        monkeypatch.setenv("RC_DISCOVER_LOCAL_BYPASS", "false")
        from config import reset_config, load_config
        reset_config()
        load_config(validate=False, force_reload=True)
        return keypair

    def test_with_the_feature_off_the_header_is_refused_with_403(self, rc_client, unconfigured):
        private, pub = unconfigured
        r = rc_client.get("/sync/status", headers={"X-Platform-Principal": mint(private, pub)})
        assert r.status_code == 403


class TestTheMountedPrivateKey:
    """I4: the Platform's whole broker key directory is mounted into
    RemoteController, private half included. What keeps that key safe is not
    the :ro flag -- it is that docbridge-web runs as root and writes the file
    0600 while RemoteController runs as uid 10001. Nothing enforced that, so
    a USER line added to the Platform image one day would silently make the
    key readable to a service that parses untrusted documents."""

    def test_a_readable_private_key_refuses_the_start(self, tmp_path, monkeypatch, caplog):
        import logging

        from auth.platform_principal import refuse_if_broker_private_key_is_readable

        (tmp_path / "broker_ed25519.pub").write_bytes(b"pub")
        (tmp_path / "broker_ed25519.pem").write_bytes(b"private")
        with caplog.at_level(logging.CRITICAL):
            with pytest.raises(SystemExit) as excinfo:
                refuse_if_broker_private_key_is_readable(str(tmp_path / "broker_ed25519.pub"))
        assert excinfo.value.code == 1
        assert "readable by this process" in caplog.text

    def test_a_missing_private_key_is_fine(self, tmp_path):
        from auth.platform_principal import refuse_if_broker_private_key_is_readable

        (tmp_path / "broker_ed25519.pub").write_bytes(b"pub")
        refuse_if_broker_private_key_is_readable(str(tmp_path / "broker_ed25519.pub"))

    def test_an_unset_pubkey_path_checks_nothing(self):
        from auth.platform_principal import refuse_if_broker_private_key_is_readable

        refuse_if_broker_private_key_is_readable("")

    def test_the_app_refuses_to_start_with_the_private_key_in_its_mount(
        self, tmp_path, monkeypatch, tmp_watch_root
    ):
        from app import create_app
        from config import load_config, reset_config

        (tmp_path / "broker_ed25519.pub").write_bytes(b"pub")
        monkeypatch.setenv("RC_PLATFORM_BROKER_PUBKEY_PATH", str(tmp_path / "broker_ed25519.pub"))
        reset_config()
        load_config(validate=False, force_reload=True)
        assert create_app(skip_validation=True) is not None

        (tmp_path / "broker_ed25519.pem").write_bytes(b"private")
        with pytest.raises(SystemExit):
            create_app(skip_validation=True)
