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
