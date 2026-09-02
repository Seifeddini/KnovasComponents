"""The signed principal assertion (KC-B2-2).

The linchpin of B2. `principal_resolver.py` says it plainly: the tenant comes
from the mTLS certificate and is unforgeable, `access_groups` comes from the
request body and is not bound to anyone. This is what binds it.

Every test here is about a way the binding could be broken.
"""

import base64
import json
import time

import pytest

from identity import assertion


@pytest.fixture
def keypair():
    return assertion.generate_keypair()


@pytest.fixture
def signer(keypair):
    return assertion.AssertionSigner(keypair.private_pem, key_id="k1")


@pytest.fixture
def verifier(keypair):
    return assertion.AssertionVerifier({"k1": keypair.public_pem})


def _mint(signer, **overrides):
    fields = dict(subject="user-1", tenant="tenant-a",
                  groups=["litigation"], roles=["member"])
    fields.update(overrides)
    return signer.mint(**fields)


def _parts(token: str):
    header_b64, payload_b64, _sig = token.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    return (
        json.loads(base64.urlsafe_b64decode(pad(header_b64))),
        json.loads(base64.urlsafe_b64decode(pad(payload_b64))),
    )


class TestWhatIsSigned:
    def test_a_minted_token_verifies(self, signer, verifier):
        assert verifier.verify(_mint(signer), tenant="tenant-a") is not None

    def test_the_subject_survives(self, signer, verifier):
        claims = verifier.verify(_mint(signer, subject="anna"), tenant="tenant-a")
        assert claims.subject == "anna"

    def test_the_groups_survive(self, signer, verifier):
        claims = verifier.verify(
            _mint(signer, groups=["ip", "litigation"]), tenant="tenant-a"
        )
        assert claims.groups == ("ip", "litigation")

    def test_groups_are_sorted_so_two_mints_compare_equal(self, signer):
        a = _parts(_mint(signer, groups=["litigation", "ip"]))[1]["grp"]
        b = _parts(_mint(signer, groups=["ip", "litigation"]))[1]["grp"]
        assert a == b

    def test_each_token_has_its_own_identifier(self, signer):
        first = _parts(_mint(signer))[1]["jti"]
        second = _parts(_mint(signer))[1]["jti"]
        assert first != second

    def test_the_token_carries_no_personal_data(self, signer):
        """Knovas must not learn a lawyer's name or address from this."""
        _header, payload = _parts(_mint(signer, subject="user-1"))
        blob = json.dumps(payload).lower()
        assert "@" not in blob
        assert "email" not in blob
        assert "name" not in blob


class TestRefusals:
    def test_a_tampered_payload_is_refused(self, signer, verifier):
        header_b64, payload_b64, sig = _mint(signer).split(".")
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
        payload["grp"] = ["everything"]
        forged = base64.urlsafe_b64encode(
            json.dumps(payload).encode()
        ).decode().rstrip("=")
        with pytest.raises(assertion.InvalidAssertionError):
            verifier.verify(f"{header_b64}.{forged}.{sig}", tenant="tenant-a")

    def test_a_token_from_another_key_is_refused(self, verifier):
        other = assertion.AssertionSigner(
            assertion.generate_keypair().private_pem, key_id="k1"
        )
        with pytest.raises(assertion.InvalidAssertionError):
            verifier.verify(_mint(other), tenant="tenant-a")

    def test_an_unknown_key_id_is_refused(self, keypair):
        signer = assertion.AssertionSigner(keypair.private_pem, key_id="rogue")
        verifier = assertion.AssertionVerifier({"k1": keypair.public_pem})
        with pytest.raises(assertion.InvalidAssertionError):
            verifier.verify(_mint(signer), tenant="tenant-a")

    def test_a_token_for_another_tenant_is_refused(self, signer, verifier):
        """The certificate decides the tenant. The token may only agree."""
        with pytest.raises(assertion.InvalidAssertionError):
            verifier.verify(_mint(signer, tenant="tenant-b"), tenant="tenant-a")

    def test_an_expired_token_is_refused(self, signer, verifier):
        token = _mint(signer, lifetime_seconds=-(assertion.CLOCK_SKEW_SECONDS + 5))
        with pytest.raises(assertion.ExpiredAssertionError):
            verifier.verify(token, tenant="tenant-a")

    def test_a_token_just_past_expiry_is_still_accepted_within_clock_skew(
        self, signer, verifier
    ):
        """Deliberate: the Platform and Knovas are different hosts, and a
        second of drift must not log the whole firm out."""
        token = _mint(signer, lifetime_seconds=-1)
        assert verifier.verify(token, tenant="tenant-a") is not None

    def test_a_token_from_the_future_is_refused(self, signer, verifier):
        """A clock skewed forward would otherwise mint long-lived tokens."""
        token = _mint(signer, issued_at=time.time() + 3600)
        with pytest.raises(assertion.InvalidAssertionError):
            verifier.verify(token, tenant="tenant-a")

    def test_the_algorithm_is_not_taken_from_the_header(self, keypair, verifier):
        """Algorithm confusion, the classic JWT break: a token claiming alg
        'none' must be refused, not trusted because it said so."""
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": assertion.TOKEN_TYPE, "kid": "k1"}).encode()
        ).decode().rstrip("=")
        payload = base64.urlsafe_b64encode(
            json.dumps({
                "sub": "x", "tid": "tenant-a", "grp": [], "rol": [],
                "iat": int(time.time()), "exp": int(time.time()) + 60, "jti": "j",
            }).encode()
        ).decode().rstrip("=")
        with pytest.raises(assertion.InvalidAssertionError):
            verifier.verify(f"{header}.{payload}.", tenant="tenant-a")

    def test_a_token_of_the_wrong_type_is_refused(self, signer, verifier):
        """A dual-control token must not pass as a principal assertion."""
        token = signer.mint_dual_control(
            tenant="tenant-a", action="matter_delete", target="node:1",
            requester="a", approver="b",
        )
        with pytest.raises(assertion.InvalidAssertionError):
            verifier.verify(token, tenant="tenant-a")

    def test_rubbish_is_refused_without_raising_something_else(self, verifier):
        for junk in ("", "abc", "a.b", "a.b.c.d", "...."):
            with pytest.raises(assertion.InvalidAssertionError):
                verifier.verify(junk, tenant="tenant-a")


class TestLifetime:
    def test_the_default_lifetime_is_short(self, signer):
        """The TTL is the bound on how long a revoked user keeps access."""
        _header, payload = _parts(_mint(signer))
        assert payload["exp"] - payload["iat"] <= assertion.MAX_LIFETIME_SECONDS

    def test_an_over_long_lifetime_is_refused_at_mint_time(self, signer):
        with pytest.raises(ValueError):
            _mint(signer, lifetime_seconds=assertion.MAX_LIFETIME_SECONDS + 1)


class TestDualControlTokens:
    def test_a_dual_control_token_verifies(self, signer, verifier):
        token = signer.mint_dual_control(
            tenant="tenant-a", action="matter_delete", target="node:1",
            requester="a", approver="b",
        )
        claims = verifier.verify_dual_control(
            token, tenant="tenant-a", action="matter_delete", target="node:1"
        )
        assert claims.requester == "a" and claims.approver == "b"

    def test_a_token_for_another_target_is_refused(self, signer, verifier):
        """An approval to delete one matter must not delete a different one."""
        token = signer.mint_dual_control(
            tenant="tenant-a", action="matter_delete", target="node:1",
            requester="a", approver="b",
        )
        with pytest.raises(assertion.InvalidAssertionError):
            verifier.verify_dual_control(
                token, tenant="tenant-a", action="matter_delete", target="node:999"
            )

    def test_a_token_for_another_action_is_refused(self, signer, verifier):
        token = signer.mint_dual_control(
            tenant="tenant-a", action="bulk_export", target="node:1",
            requester="a", approver="b",
        )
        with pytest.raises(assertion.InvalidAssertionError):
            verifier.verify_dual_control(
                token, tenant="tenant-a", action="matter_delete", target="node:1"
            )

    def test_self_approval_cannot_even_be_minted(self, signer):
        with pytest.raises(ValueError):
            signer.mint_dual_control(
                tenant="tenant-a", action="matter_delete", target="node:1",
                requester="a", approver="a",
            )

    def test_a_principal_assertion_is_refused_as_a_dual_control_token(self, signer, verifier):
        with pytest.raises(assertion.InvalidAssertionError):
            verifier.verify_dual_control(
                _mint(signer), tenant="tenant-a", action="matter_delete", target="node:1"
            )
